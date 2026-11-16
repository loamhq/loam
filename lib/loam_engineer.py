"""What an engineer job is: where it lives, and who may brief it.

Two readers, one authority. ``bin/provision`` renders the engineer's
settings from ``ENGINEER_MODEL``; ``bin/engineer-job`` passes the same value
on the command line and (since 2026-09-07) writes the brief into the
directory shape defined here and reads the receipt back out of it.
``mcp/engineer_server.py`` imports nothing from here on purpose: it lives as
long as a conversation does, and anything it read from this file would be
frozen at whatever the file said when the conversation opened. A constant
with two homes is the defect this whole repo is built against, so none of
them define anything twice.

**A job has no clock and no round-trip cap since 2026-11-10** (the owner: "I
don't want any limitations at all"); ``max_minutes`` is a ceiling a briefer
may set and nothing sets by default. **The recycle boundary yields to the
job**, the other way round from 2026-09-05 to 2026-11-10, when a brief that
would cross 01:30 Pacific was refused. ``loam-recycle-rc.timer`` restarts the
Remote Control servers at 01:30, and a job is its own transient unit and
survives it; what the restart would lose is the call a persona has open on
the job from inside one of those servers. So ``bin/recycle-rc`` restarts every
server but one that holds a waiting call, and says which it skipped.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import loam_config as lc
from zoneinfo import ZoneInfo

AGENTS = lc.ROOT
VAULT = lc.VAULT
AI_LABS = lc.WORKSPACES
ENGINEER = AGENTS / "engineer"
LOG_DIR = lc.LOGS

# The model an engineer runs on. Pinned for the reason WAKE_MODEL is pinned:
# a project that runs headless must not run on whatever the CLI defaults to
# that week. The owner's call, 2026-09-16 in chat: every engineer -- the
# platform engineer and each project engineer -- runs Opus 5; the personas
# and the reviewer stay on `fable`. This is the ONE home of the value:
# bin/provision imports it to render the engineer's settings and
# bin/engineer-job passes it as `--model`, so the two cannot disagree.
# Project engineers carry no model in their rendered settings (they are
# portable on purpose) and get it from the launcher's flag alone.
#
# A VERSION, NOT AN ALIAS, since 2026-09-25 (the owner, in chat: "make sure
# whenever we are using Opus in our system, we are using the latest version
# at the time"). This read `opus` until then, and `opus` resolves to
# whatever Opus the running CLI ships -- so the moment the CLI moves, every
# engineer's model moves with it and no file on this box has changed to say
# so. That is the same defect as an unpinned CLI, one layer up. An explicit
# id means a version change is a commit.
#
# `claude-opus-5-5` since 2026-09-25, in the same commit as the CLI pin move
# to 2.1.281 -- and as an explicit id, not `opus` again. Opus 5.5 shipped in
# 2.1.280 ("Added Claude Opus 5.5 (`claude-opus-5-5`), now the default Opus
# model"), and the pin had to move first: earlier the same day this read
# `claude-opus-5` because 2.1.270 could not reach 5.5 at all -- the string
# `claude-opus-5-5` occurred 0 times in that binary and 51 times in 2.1.281,
# and the server labelled it "Opus 5.5 (disabled) / Update to 2.1.280+".
# The two levers are therefore one commit by necessity: this id without the
# CLI move is a model the binary does not know, and the CLI move without
# this id leaves every engineer on 5.0 while `opus` would have moved them
# silently. Verified by causing on 2.1.281 before the commit landed, with
# the flag derived from this constant rather than typed.
ENGINEER_MODEL = os.environ.get("LOAM_ENGINEER_MODEL", "claude-opus-5-5")

# No round-trip cap (2026-11-10, the owner: "Please remove any limitations on
# how long a turn or session can run. I don't want any limitations at all.").
# It was 300 from 2026-09-22 (100 before, a wake's number borrowed), passed
# as `--max-turns` and counted for a Codex job by lib/loam_codex_job.py. The
# environment can still set one for a probe; nothing in a unit does, and
# None means the flag is not passed at all.
MAX_TURNS = (int(os.environ["LOAM_ENGINEER_MAX_TURNS"])
             if os.environ.get("LOAM_ENGINEER_MAX_TURNS") else None)

# Which CLI runs a PROJECT engineer: the record's `runtime`, one of these.
# The owner, 2026-10-07 in chat: "make sure all of engineering agents for the
# project, not the Loam main agent, are with Codex instead of Claude Code.
# Their default model should be latest GPT with Sol on high effort." The
# platform engineer is always Claude Code. A record without the key ran on
# Claude Code before the key existed and still does; `bin/provision` writes
# DEFAULT_RUNTIME into every new record, and `bin/check-drift` names a
# record without one. Flipping one project back is `"runtime": "claude"` in
# its record and nothing else.
# `antigravity` since 2026-11-15 (lib/loam_agy_job.py), as a choice: the
# owner wants to try it before any engineer moves, so nothing defaults to it.
RUNTIMES = ("claude", "codex", "antigravity")
DEFAULT_RUNTIME = "codex"

# The model and effort a Codex engineer runs on: the ONE home, as
# ENGINEER_MODEL is Claude's, and an id rather than Codex's default for the
# same reason. `gpt-6.1-sol` since 2026-10-07: the newest Sol in `model/list`
# for this login (0.159.3), "Latest workhorse model for coding and everyday
# work", listed beside `gpt-6-sol` "Previous generation" and `gpt-5.6-sol`
# "Older generation". `bin/check-drift` says when the list stops offering it.
CODEX_ENGINEER_MODEL = os.environ.get("LOAM_CODEX_ENGINEER_MODEL", "gpt-6.1-sol")
CODEX_ENGINEER_EFFORT = "high"

# An Antigravity engineer's built-in model (2026-11-15): the one the owner's
# trial named, from `agy models` (1.5.9). Its id carries its thinking level
# (lib/loam_antigravity.py, LEVEL_RE), so there is no separate effort.
AGY_ENGINEER_MODEL = os.environ.get("LOAM_AGY_ENGINEER_MODEL",
                                    "gemini-4-hydrogen-high")

# No wall clock (2026-11-10, the owner, as above). Until then a job had 60
# minutes unless its brief asked for more, up to 180, and a brief from an
# unattended lane was held to 90. `max_minutes` stays as a ceiling a briefer
# may set for one job; absent, a job runs until it is done. A waiting call
# still ends under its transport's timeout (bin/engineer-job,
# WAITER_CEILING_SECONDS) -- that ends the call, never the job.
DEFAULT_MAX_MINUTES = None

# The server-side tick. It is the discriminator the Move 9 probes earned: a
# client that gave up and a job that died are otherwise indistinguishable,
# and the only reason that report could say the orphaned server *kept
# running* rather than *crashed* is that it was ticking into its own log.
HEARTBEAT_SECONDS = 30

PACIFIC = ZoneInfo(lc.TIMEZONE)

# 01:30 Pacific, which is when loam-recycle-rc.timer fires. Nothing refuses
# a job for it since 2026-11-10 (bin/recycle-rc yields to the job instead);
# `next_recycle` is still the one reading of when it is. The environment
# override exists for probes and for nothing else -- a branch at the boundary
# can otherwise only be caused once a day, at half past one in the morning, and a
# branch that is never caused is a branch nobody has seen work. Same seam, same
# reason, as LOAM_LOCK_WAIT_S in lib/loam_lock.py: the operative number is the
# default here, nothing in any unit or generator sets the variable, and moving
# it moves only the number -- the mechanism under test is the same one.
#
# It is read at call time rather than at import so a probe can move it without
# reloading the module.
RECYCLE_HOUR, RECYCLE_MINUTE = 1, 30


def _recycle_at() -> tuple[int, int]:
    raw = os.environ.get("LOAM_RECYCLE_AT")
    if not raw:
        return RECYCLE_HOUR, RECYCLE_MINUTE
    try:
        hour, minute = (int(x) for x in raw.split(":", 1))
    except ValueError:
        return RECYCLE_HOUR, RECYCLE_MINUTE
    return hour, minute
# When a job may next begin. Five minutes past the recycle, not on it: the
# restart is not instantaneous and a job started into it would race the
# servers coming back.
NEXT_START_HOUR, NEXT_START_MINUTE = 1, 35

# Deliberately loose in the suffix, and that looseness was bought with a
# failed job. The launcher validates a job id it did NOT mint: the id comes
# from the MCP server, which is a long-lived process started with the persona's
# Remote Control session, while the launcher is a fresh process on every call.
# So the two can be different vintages of this file. On 2026-09-05 they were --
# the suffix was widened from four hex to eight, the persona's server was still
# minting four, and the launcher refused the id as malformed before the job
# could start. Nothing was lost but the run, and the failure was at least loud;
# a launcher that is strict about a shape somebody else chose is a launcher
# that breaks on its own upgrade.
#
# What is actually load-bearing here is that a job id cannot contain a path
# separator or traverse, which is what this asserts. The suffix is opaque.
#
# The general rule this is an instance of: **changing `lib/` or `mcp/` takes
# effect on the launcher immediately and on a server only when its
# conversation ends** (the server is spawned per conversation session, not
# per RC unit; the 01:30 recycle restarts the RC processes, which hold none
# of this code). The wake path has no such seam -- every wake is a fresh
# process -- which is exactly why nothing before Move 9 had to think about it.
#
# Since 2026-09-07 (afternoon) the server mints nothing: it hands the tool's
# arguments to `bin/engineer-job --brief` and the launcher mints the id in
# the same process that validates it, so the two vintages can no longer
# disagree on this shape. The looseness stays for the positional form, which
# servers started before that change still call with an id of their own.
JOB_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4,16}$")


class BriefError(Exception):
    """The brief cannot be run as written. Always returned, never raised at
    a persona as a stack trace."""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_job_id(moment: datetime | None = None) -> str:
    """Sortable, probably unique, and readable in a directory listing.

    The random suffix is not decoration: two briefs sent in the same second
    from two personas would otherwise land in one directory and overwrite each
    other's brief, and the lock that stops two jobs RUNNING at once is taken
    after the brief is written.

    **Probably is not a guarantee, and the guarantee is `claim_job_dir`.**
    This was four hex digits until tests/test_engineer.py failed on it --
    199 unique ids in 200 draws, which is exactly what the birthday bound
    predicts at 65,536 values and is a coin-flip away at a few hundred. Eight
    hex makes a collision negligible; creating the directory exclusively makes
    it impossible. Both, because "negligible" is how the first version was
    justified too.
    """
    moment = (moment or now_utc()).astimezone(PACIFIC)
    return f"{moment:%Y%m%d-%H%M%S}-{secrets.token_hex(4)}"


def _jobs_root(engineer) -> Path:
    """A Project's jobs directory, or `<dir>/jobs` for a plain path -- the
    platform engineer's shape, kept so callers older than the registry
    still resolve."""
    if hasattr(engineer, "jobs"):
        return engineer.jobs
    if Path(engineer) == ENGINEER:
        return lc.JOBS
    return Path(engineer) / "jobs"


def claim_job_dir(engineer, attempts: int = 8) -> tuple[str, Path]:
    """Mint a job id and create its directory EXCLUSIVELY. (job_id, dir).

    `exist_ok=False` is the whole point: the first caller to create the
    directory owns that id, and a loser retries with a new one rather than
    writing its brief over somebody else's.
    """
    for _ in range(attempts):
        job_id = new_job_id()
        directory = job_dir(engineer, job_id)
        try:
            directory.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            continue
        return job_id, directory
    raise BriefError(
        f"could not claim a job directory under {_jobs_root(engineer)} after "
        f"{attempts} attempts")


def job_dir(engineer, job_id: str) -> Path:
    if not JOB_ID_RE.match(job_id):
        raise BriefError(f"not a job id: {job_id!r}")
    return _jobs_root(engineer) / job_id


# ---------------------------------------------------------------------------
# The job's scratch (since 2026-10-21). Every job's session runs with TMPDIR
# set to a directory of its own under /tmp, and the launcher removes it when
# the session ends, success or failure, as it removes gcloud's cache. Caused:
# jobs left ~30 GB of pnpm stores and review checkouts in /tmp and nothing
# removed them, until `/` had 2.5 MB free and running jobs failed. Outside the
# job directory on purpose: that directory is kept, and copied from, after the
# job; scratch is not. bin/sweep-tmp is the backstop for a unit killed before
# its `finally`, and knows the name by its job id.
# ---------------------------------------------------------------------------

SCRATCH_ROOT = Path("/tmp")


def scratch_dir(job_id: str, root: Path | None = None) -> Path:
    if not JOB_ID_RE.match(job_id):
        raise BriefError(f"not a job id: {job_id!r}")
    return (root or SCRATCH_ROOT) / f"loam-job-{job_id}"


def make_scratch(job_id: str, root: Path | None = None) -> Path:
    """The job's scratch, empty and private. A continuation of the same id
    (recovery's rerun) starts clean rather than with a dead run's leavings."""
    path = scratch_dir(job_id, root)
    remove_scratch(job_id, root)
    path.mkdir(mode=0o700, parents=True)
    return path


def remove_scratch(job_id: str, root: Path | None = None) -> None:
    """Gone, whatever is in it. `--one-file-system`, so a mount a tool left
    inside it stops the removal instead of being descended into; rm is used
    rather than shutil because a build leaves read-only directories behind
    (Go's module cache does) and rm -rf as the owner still removes those."""
    import subprocess  # noqa: PLC0415
    path = scratch_dir(job_id, root)
    if os.path.lexists(path):
        subprocess.run(["chmod", "-R", "u+w", "--", str(path)],
                       capture_output=True, check=False)
        subprocess.run(["rm", "-rf", "--one-file-system", "--", str(path)],
                       capture_output=True, check=False)


# ---------------------------------------------------------------------------
# The session id. Minted here, per job, and written into the job directory
# before the CLI is started.
#
# Since 2026-09-22 (continuation). The CLI will resume a session by id, which
# is what lets a capped job be continued instead of re-read from scratch --
# but the id has to be KNOWN, and the launcher used to learn it only from the
# result envelope, which a job that was killed or timed out never prints. So
# the launcher chooses the id (`--session-id <uuid>`, CLI 2.1.260) rather than
# discovering it. Two things follow, and both are the point: a job cut off at
# its wall clock still has a resumable session, and the round-trip count and
# API error in its record can be read from a transcript whose name was known
# before the run began.
# ---------------------------------------------------------------------------

def new_session_id() -> str:
    """A session id for one job. The CLI requires a valid UUID."""
    import uuid  # noqa: PLC0415
    return str(uuid.uuid4())


def session_id_file(engineer, job_id: str) -> Path:
    return job_dir(engineer, job_id) / "session-id"


def recorded_session(engineer, job_id: str) -> str | None:
    """The session id of a job that has already run, or None.

    None for every job run before 2026-09-22, which is why the refusal that
    reads this says what it says: the id was not recorded then and cannot be
    recovered now.
    """
    try:
        value = session_id_file(engineer, job_id).read_text(encoding="utf-8").strip()
    except (OSError, BriefError):
        return None
    return value or None


def locate_job(job_id: str) -> "Project | None":
    """Which project's jobs root holds `job_id`, or None.

    Only so a brief that asks to continue another project's job is told which
    project it belongs to. A session can be resumed only in the tree it ran
    in -- the CLI stores transcripts per working directory -- so this is a
    refusal either way, and naming the project turns it into something the
    persona can act on.
    """
    for project in projects().values():
        try:
            if job_dir(project, job_id).is_dir():
                return project
        except BriefError:
            return None
    return None


def slug_for(text: str, limit: int = 48) -> str:
    """A filename fragment from the brief's own first line.

    The receipt is a file the owner finds in a folder, so its name has to say
    what the job was without being opened. Taken from the brief rather than
    asked for separately, because a second field is a second thing to get
    wrong and the first line of a brief is already its title.

    A title is one line and is taken whole. The colon test below is for a
    brief's body, where a `key: value` line is header rather than prose; on a
    title it turned `Probe: an unattended lane ...` into `job` (2026-10-06).
    """
    if "\n" not in text.strip():
        line = text.strip().lstrip("#").strip() or "job"
    else:
        for line in text.splitlines():
            line = line.strip().lstrip("#").strip()
            if line and not line.startswith("---") and ":" not in line[:20]:
                break
        else:
            line = "job"
    slug = re.sub(r"[^a-z0-9]+", "-", line.lower()).strip("-")
    return (slug[:limit].rstrip("-") or "job")


def next_recycle(moment: datetime | None = None) -> datetime:
    """The next 01:30 Pacific, as an aware datetime."""
    local = (moment or now_utc()).astimezone(PACIFIC)
    hour, minute = _recycle_at()
    boundary = local.replace(
        hour=hour, minute=minute, second=0, microsecond=0)
    return boundary if local < boundary else boundary + timedelta(days=1)


def next_start(moment: datetime | None = None) -> datetime:
    """When a job refused at the boundary may next be sent."""
    boundary = next_recycle(moment)
    if os.environ.get("LOAM_RECYCLE_AT"):
        return boundary + timedelta(minutes=5)
    return boundary.replace(hour=NEXT_START_HOUR, minute=NEXT_START_MINUTE)


def log_line(payload: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with (LOG_DIR / "engineer.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")


# ---------------------------------------------------------------------------
# Projects. Which engineer serves which name, and where each one runs.
#
# The platform engineer is built in: its root is `agents/engineer/`, its jobs
# live beside it, and it has the vault and ai-labs because the runtime is its
# project. Every other engineer is a PROJECT engineer, registered by
# `bin/provision --project` as one JSON file under `agents/projects/`, and
# read from disk on every call so a project provisioned an hour ago is
# accepted by a server started yesterday. The registry file is the record;
# it carries no secret -- the deploy key it names lives in ~/.ssh, and the
# account token is never a project's.
#
# A project engineer's root is its repo's checkout on the box (decision 5's
# container comes with Move 10; a bind-mount of the same path keeps every
# registered path true). Its jobs live OUTSIDE the checkout, under
# `agents/state/projects/<name>/jobs/`, because the brief and the receipt are
# this runtime's protocol and nothing of that protocol goes into a repo a
# stranger may clone (decision 21). The launcher grants the job directory to
# the session with `--add-dir` at launch, so the repo's own settings never
# have to name a path on this box.
# ---------------------------------------------------------------------------

# `LOAM_PROJECTS_DIR` (2026-11-15) is a seam for a throwaway project, as
# LOAM_HOST_SETTINGS is for the settings: a registry of one scratch record,
# so a real job can be run on a runtime no registered project is on without
# a record in this repository. Nothing in a unit or generator sets it; the
# launcher hands it into the job's unit when its caller had it.
PROJECTS_DIR = Path(os.environ.get("LOAM_PROJECTS_DIR") or AGENTS / "projects")
PROJECT_STATE = lc.STATE / "projects"
PROJECT_CHECKOUTS = lc.PROJECTS
PROJECT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class Project:
    """One engineer: where it runs, where its jobs go, what it may push with."""

    def __init__(self, name: str, root: Path, jobs: Path, *, platform: bool,
                 repo: str | None = None, remote: str | None = None,
                 deploy_key: Path | None = None, record: dict | None = None):
        self.name = name
        self.root = root
        self.jobs = jobs
        self.platform = platform
        self.repo = repo
        self.remote = remote
        self.deploy_key = deploy_key
        self.record = record or {}

    @property
    def lock_name(self) -> str:
        # The name a job's run slot and markers are held under. Until
        # 2026-10-02 it was also a lock that ran one job at a time per
        # engineer; each job now has a worktree of its own
        # (lib/loam_worktree.py) and only the global slot cap counts it.
        return "engineer" if self.platform else f"engineer-{self.name}"

    @property
    def pull_request(self) -> bool:
        """Lands by pull request on a repository Loam does not own
        (`"landing": "pull_request"` in the record, since 2026-10-06): no
        merge into `main`, no push of it, no deploy lane, no deploy key."""
        return not self.platform and self.record.get("landing") == "pull_request"

    @property
    def record_runtime(self) -> str:
        """The record's own `runtime`: `claude` for the platform engineer
        and for a record that predates the key."""
        if self.platform:
            return "claude"
        value = self.record.get("runtime")
        return value if value in RUNTIMES else "claude"

    @property
    def runtime(self) -> str:
        """`claude`, `codex` or `antigravity`: which CLI a job of this engineer runs. Since
        2026-11-13 the engineer's setting (Settings > Agents > Engineers,
        `engineer_choice`) when one is made, else the record's `runtime`.
        The platform engineer is always Claude Code."""
        return engineer_choice(self)["runtime"]

    def receipts_dir(self, vault: Path) -> Path:
        # `vault_folder` (since 2026-10-07) when the vault already has a
        # folder for the project under another spelling: `widget`'s receipts go
        # to `Widget/`, because the Mac and the phone cannot hold two folders
        # that differ only in case.
        folder = self.record.get("vault_folder") or self.name
        return vault / "40_PROJECTS" / folder / "receipts"

    def __repr__(self) -> str:
        return f"Project({self.name!r}, root={str(self.root)!r})"


# ---------------------------------------------------------------------------
# What an engineer runs on, as the owner set it (2026-11-13).
#
# The owner, 2026-11-13 11:59 in chat: "customizing and editing the engineers
# and other systems ... being able for me to be able to set the provider for
# them." Settings > Agents > Engineers sets a provider, a model and a
# thinking level for each engineer, and one default for every other project,
# and a project's "deploy when a job lands". They are kept in the broker's
# host settings file (`state/broker/settings.json`, key `engineers`; written
# by lib/loam_profile.py under the broker's one lock), not in
# `projects/<name>.json`, because that record is tracked in this repo and a
# page's write must not dirty the canonical checkout. Read here, on every
# launch, so a change applies from the next job; a job already running
# finishes on what it started with.
#
# Precedence: the engineer's own setting, else (project engineers only) the
# default for every other project, else the record's `runtime` and the
# built-in model and effort above. A setting is whole: a provider and,
# optionally, a model and an effort for that provider; null model or effort
# is that provider's built-in.
# ---------------------------------------------------------------------------

HOST_SETTINGS = lc.STATE / "broker" / "settings.json"
# The page's provider ids for the CLIs a job runs on.
PROVIDER_OF = {"claude": "claude-code", "codex": "codex",
               "antigravity": "antigravity"}
RUNTIME_OF = {v: k for k, v in PROVIDER_OF.items()}


def builtin_choice(runtime: str) -> tuple[str, str | None]:
    """(model, effort) a runtime runs on when nobody chose."""
    if runtime == "codex":
        return CODEX_ENGINEER_MODEL, CODEX_ENGINEER_EFFORT
    if runtime == "antigravity":
        return AGY_ENGINEER_MODEL, None
    return ENGINEER_MODEL, None


def host_settings_path() -> Path:
    return Path(os.environ.get("LOAM_HOST_SETTINGS") or HOST_SETTINGS)


def engineer_settings() -> dict:
    """``{"default": {...}, "projects": {name: {...}}}`` as stored; empty
    when nothing was ever set or the file cannot be read (a launch never
    fails on a settings file: it runs on the record)."""
    try:
        data = json.loads(host_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    value = data.get("engineers") if isinstance(data, dict) else None
    return value if isinstance(value, dict) else {}


def engineer_choice(project: "Project", settings: dict | None = None) -> dict:
    """``{"runtime", "model", "effort", "source"}`` a new job of this
    engineer runs on. ``source`` is ``own``, ``default`` or ``record``."""
    stored = engineer_settings() if settings is None else settings
    own = (stored.get("projects") or {}).get(project.name)
    default = stored.get("default")
    row, source = None, "record"
    if isinstance(own, dict) and own.get("provider") in RUNTIME_OF:
        row, source = own, "own"
    elif (not project.platform and isinstance(default, dict)
          and default.get("provider") in RUNTIME_OF):
        row, source = default, "default"
    runtime = RUNTIME_OF[row["provider"]] if row else project.record_runtime
    if project.platform and runtime != "claude":
        row, source, runtime = None, "record", "claude"
    model, effort = builtin_choice(runtime)
    if row:
        model = row.get("model") or model
        effort = row.get("effort") or effort
    return {"runtime": runtime, "model": model, "effort": effort,
            "source": source}


def choice_for(project: "Project", runtime: str) -> tuple[str, str | None]:
    """(model, effort) for a job of this engineer on ``runtime``: the
    setting's when it is for that runtime, else the built-in (a continuation
    runs on its parent's CLI, which may not be the one chosen since)."""
    choice = engineer_choice(project)
    if choice["runtime"] == runtime:
        return choice["model"], choice["effort"]
    return builtin_choice(runtime)


# ---------------------------------------------------------------------------
# Three tiers per engineer (2026-11-15).
#
# The owner, 2026-11-15 20:53 PT: "Can we have personas have the ability to
# pick between 3 models from the list models the provider provides based on
# task? We set those three they can choose from?" So each engineer's setting
# (a project's own, or `default`) may carry `tiers`: `{"quick": <model>,
# "standard": <model>, "hard": <model>}`, each a model its provider lists
# (on Antigravity the id carries its level). A brief names a tier, never a
# model, so a brief survives model churn; left out it is `standard`. A tier
# nobody set is the engineer's `model` (or the provider's built-in), exactly
# as before tiers existed, so an engineer nobody set runs as it did. Tiers
# belong to the setting's provider: a provider change drops them, as it
# drops `model` and `effort` (lib/loam_profile.py, `update_engineer`).
# ---------------------------------------------------------------------------

TIERS = ("quick", "standard", "hard")
DEFAULT_TIER = "standard"


def tier_below(tier: str | None) -> str | None:
    """The tier a quota failure falls back to: one down, and none below
    `quick`."""
    if tier not in TIERS:
        return None
    i = TIERS.index(tier)
    return TIERS[i - 1] if i > 0 else None


def _setting_row(project: "Project", stored: dict) -> dict | None:
    """The setting row ``engineer_choice`` chose for this engineer, or
    None when it runs on its record."""
    own = (stored.get("projects") or {}).get(project.name)
    default = stored.get("default")
    if isinstance(own, dict) and own.get("provider") in RUNTIME_OF:
        return own
    if (not project.platform and isinstance(default, dict)
            and default.get("provider") in RUNTIME_OF):
        return default
    return None


def tier_models(project: "Project", settings: dict | None = None) -> dict:
    """``{tier: model or None}`` as set for this engineer's next job: the
    chosen setting row's ``tiers``, None for each one nobody set."""
    stored = engineer_settings() if settings is None else settings
    choice = engineer_choice(project, stored)
    row = _setting_row(project, stored) if choice["source"] != "record" else None
    tiers = (row or {}).get("tiers") if row else None
    tiers = tiers if isinstance(tiers, dict) else {}
    return {t: (tiers.get(t) if isinstance(tiers.get(t), str) and tiers.get(t)
                else None) for t in TIERS}


def tier_choice(project: "Project", runtime: str, tier: str | None,
                settings: dict | None = None) -> dict:
    """``{"tier", "model", "effort", "from"}`` for a job of this engineer
    on ``runtime`` at ``tier`` (None: `standard`). ``from`` is ``tier``
    when the tier is set, ``model`` when it falls back to the engineer's
    model or the built-in. A tier set for another runtime than the one the
    job runs on (a continuation of a job on an older provider) does not
    apply, as ``choice_for``."""
    tier = tier if tier in TIERS else DEFAULT_TIER
    stored = engineer_settings() if settings is None else settings
    choice = engineer_choice(project, stored)
    if choice["runtime"] == runtime:
        model, effort = choice["model"], choice["effort"]
        set_model = tier_models(project, stored)[tier]
    else:
        (model, effort), set_model = builtin_choice(runtime), None
    if set_model:
        return {"tier": tier, "model": set_model, "effort": effort, "from": "tier"}
    return {"tier": tier, "model": model, "effort": effort, "from": "model"}


def deploy_default(project: "Project") -> bool:
    """Whether a brief that does not say `deploy` deploys at landing: the
    engineer's "Deploy when a job lands" (2026-11-13), off unless set."""
    own = (engineer_settings().get("projects") or {}).get(project.name)
    return bool(isinstance(own, dict) and own.get("deploy_on_land") is True)


def platform_project() -> Project:
    return Project("Loam", ENGINEER, lc.JOBS, platform=True)


def project_record_path(name: str) -> Path:
    return PROJECTS_DIR / f"{name}.json"


def load_project_record(path: Path) -> Project:
    record = json.loads(path.read_text(encoding="utf-8"))
    name = record["name"]
    return Project(
        name, Path(record["checkout"]), PROJECT_STATE / name / "jobs",
        platform=False, repo=record.get("repo"), remote=record.get("remote"),
        deploy_key=Path(record["deploy_key"]) if record.get("deploy_key") else None,
        record=record,
    )


def projects() -> dict[str, Project]:
    """Every engineer this box can brief, by project name. Read from disk
    each time, never cached, for the reason above."""
    found = {"Loam": platform_project()}
    if PROJECTS_DIR.is_dir():
        for path in sorted(PROJECTS_DIR.glob("*.json")):
            try:
                project = load_project_record(path)
            except (OSError, ValueError, KeyError):
                continue   # a half-written record is check-drift's finding
            found[project.name] = project
    return found


def resolve_project(key: str) -> Project | None:
    """By name, or by root directory -- the latter so an MCP server started
    before this function existed, which still calls the launcher with a
    path, keeps working until the next RC restart."""
    known = projects()
    if key in known:
        return known[key]
    try:
        root = Path(key).resolve()
    except OSError:
        return None
    for project in known.values():
        if project.root.resolve() == root:
            return project
    return None


# ---------------------------------------------------------------------------
# Who may brief which project.
#
# **The personas `engineer.briefers` names (/etc/loam/loam.toml) brief the
# platform engineer; project engineers are open to every persona.** The
# platform engineer holds full sudo and the runtime itself, so an install may
# have one persona carry that line to its owner; a project engineer writes
# one repo behind NoNewPrivileges and its own deploy key, which is a brief
# anyone may send. Absent, every persona may: a fresh install has one person
# and often one persona, and a list naming nobody there would leave the
# runtime with no one to brief it. The first install names one persona
# alone, the owner's rule of 2026-09-11 in chat; until 2026-11-14 that name was this module's constant.
#
# Enforced HERE rather than in the tool's description, because a description
# is a request. Until 2026-09-25 nothing compared the caller to the project:
# all seven personas carry `mcp__engineer__brief` unscoped, so the rule lived
# only in whether each persona remembered it. What the launcher already knew
# is everything the rule needs -- `LOAM_AGENT` says who is calling and
# `projects()` says which projects exist -- so the comparison is one function
# and no persona's files change.
#
# **The deploy lane is the exception, and it is the Move 10 default, not a
# loophole.** A project engineer's build reaches its container by
# `bin/deploy <app> up <commit>`, which is sudo, which is Loam's. Recorded
# default (Move 10, 2026-09-08): the platform engineer runs it on a persona's
# brief. With the rule above and no exception, a persona could not ask for a
# deploy of its own site without another persona in the middle -- which is
# what happened on 2026-09-16, when it needed the owner's sanction to ask. So a Loam brief
# whose title begins `Deploy <project>`, for a project a project engineer is
# provisioned for, is accepted from any persona. The shape is not invented:
# all five Loam briefs from other personas this box has ever run were titled
# `Deploy <its site> at <commit>`, from one persona, in September.
# ---------------------------------------------------------------------------

PLATFORM_BRIEFERS = lc.ENGINEER_BRIEFERS

# `Deploy <project>` and whatever follows (the five real ones read
# `Deploy <its site> at 90496b3`). The verb is matched
# case-insensitively because a persona writes a title as prose; the project
# name is matched against the registry EXACTLY, because that is a name the
# runtime chose and a near-miss is a refusal worth reading rather than a
# guess worth acting on.
DEPLOY_TITLE_RE = re.compile(r"^deploy\s+(\S+)", re.IGNORECASE)


def deploy_target(title: str) -> str | None:
    """The project a `Deploy <project>` title names, when a project engineer
    is provisioned for it. None for every other title, including a deploy of
    a name no engineer serves and a deploy of `Loam` itself."""
    match = DEPLOY_TITLE_RE.match((title or "").strip())
    if not match:
        return None
    project = projects().get(match.group(1))
    if project is None or project.platform:
        return None
    return project.name


def brief_refusal(project: "Project", briefed_by: str, title: str,
                  briefers: tuple[str, ...] | None = None) -> str | None:
    """Why this persona may not send this brief to this project, or None.

    One function, both doors: `accept` asks it with the caller's identity from
    `LOAM_AGENT`, and `preflight` asks it again with `briefed_by` read back out
    of the brief's own header, so a brief written by hand cannot take a path
    the tool refuses. A brief that names no briefer is refused too -- the rule
    is a list of who may, and an unnamed caller is not on it.
    """
    if not project.platform:
        return None                      # every project engineer is open
    briefers = PLATFORM_BRIEFERS if briefers is None else briefers
    who = (briefed_by or "").strip()
    if who and (not briefers or who.lower() in briefers):
        return None
    if deploy_target(title):
        return None

    deployable = sorted(p.name for p in projects().values() if not p.platform)
    if deployable:
        lane = (
            f"The one {project.name} brief any persona may send is a deploy of "
            f"a project engineer's build: a title beginning `Deploy <project>`, "
            f"where <project> is one of {', '.join(deployable)} — the platform "
            f"engineer runs `bin/deploy <project> up <commit>`, which is sudo, "
            f"which is why it is a {project.name} brief at all."
        )
    else:
        lane = (
            f"The one {project.name} brief any persona may send is a deploy of "
            f"a project engineer's build, and no project engineer is "
            f"provisioned, so there is none to ask for today."
        )
    named = f"{who!r}" if who else "nobody"
    if not briefers:
        return (
            f"Refused before starting: a brief to project {project.name} names "
            f"the persona sending it, and this one names nobody. This is not "
            f"queued — there is nothing to wait for. This brief's title is "
            f"{title.strip()!r}.")
    listed = (briefers[0] if len(briefers) == 1
              else ", ".join(briefers[:-1]) + " and " + briefers[-1])
    return (
        f"Refused before starting: project {project.name} is briefed by "
        f"{listed} and by nobody else, and this brief is from {named}. "
        f"{lc.OWNER_NAME}'s rule (`engineer.briefers` in {lc.CONFIG_FILE}): the "
        f"platform engineer holds sudo and the runtime itself, so "
        f"{'one persona briefs' if len(briefers) == 1 else 'only those personas brief'} "
        f"it; every project engineer is open to every persona. This is not "
        f"queued — there is nothing to wait for.\n"
        f"\n"
        f"{lane} This brief's title is {title.strip()!r}.\n"
        f"\n"
        f"Anything else for {project.name} goes to "
        f"{briefers[0].capitalize()}, who asks {lc.OWNER_NAME} when it needs to."
    )


# ---------------------------------------------------------------------------
# Unattended lanes (2026-11-09).
#
# Every lane may brief: a wake, a cron and a one-off have what a conversation
# has, the engineer included (the owner, 2026-11-09: "lift the restrictions for
# the agents when the personas wake up"). Until then a table here,
# UNATTENDED_BRIEFERS, named the one cron allowed to brief (a persona's
# `infra-watch`, 2026-10-06) and refused the rest; its one-off
# `first-resize` woke on 11-08 without `headroom` and could do nothing. Until
# 2026-11-10 a brief from such a lane was held to a 90-minute clock
# (UNATTENDED_MAX_MINUTES) so the lane's own clock could outlast it; both
# clocks went that day, and the lane simply waits for its job.
# ---------------------------------------------------------------------------


def unattended(lane: str | None) -> bool:
    """Whether LOAM_WAKE names a lane nobody is present in.

    Unset is a conversation (the broker pops the variable on purpose), as is
    `conversation`; `engineer:<job>` is a job, which a conversation started.
    Every other value -- `reflection`, `cron:<name>`, the reviewer's
    `review:<run>` -- is a scheduled run with nobody in the room.
    """
    value = (lane or "").strip().lower()
    return bool(value) and value != "conversation" \
        and not value.startswith("engineer:")


def git_head(repo: Path) -> str | None:
    """The repo's HEAD, or None. Advisory: never fails a job."""
    import subprocess  # noqa: PLC0415
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def git_heads(project: "Project | None" = None) -> dict[str, str | None]:
    """The trees a job can move, before and after. The launcher's fact, not
    the engineer's claim -- a receipt that says "committed" and a repo whose
    HEAD did not move are two readings the persona can now compare without
    believing either one.

    The platform engineer's trees are the three it can reach. A project
    engineer's is its own checkout, and only that: the other three are not
    in its hands, and a row that cannot move is a row that teaches nothing.
    """
    if project is not None and not project.platform:
        return {project.name: git_head(project.root)}
    return {
        "agents": git_head(AGENTS),
        "vault": git_head(VAULT),
        "ai-labs": git_head(AI_LABS),
    }
