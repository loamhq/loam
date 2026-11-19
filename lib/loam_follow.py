"""Every provider follows its own latest release, tested, with rollback.

The owner, 2026-11-11 11:06 PT, of Claude Code, Codex "and other providers
that we're going to be implementing": "let's do the automatic the way that
you've described so it automatically happens ... for all the providers, or
at least any provider that is actively signed in and active." The way
described is the proposal in the 11-10 receipt
(``40_PROJECTS/Loam/receipts/2026-11-10-20261110-163556-44d16a28-…``), with
one change by his word: **no stop on an alias or model move**. A build that
moves what an alias means goes live like any other once it passes, and its
comms line says so ("fable now means Fable 5.3").

Once a day (``loam-provider-follow.timer``, 02:50 Pacific), per provider:

1. **Signed in?** A provider with no login is skipped, and says so in the run
   log (``logs/provider-follow.jsonl``), never in comms.
2. **Newer?** The published latest against the live build (what the pinned
   link points at). A build that was rolled back is never taken again; the
   next release is.
3. **Install beside**, never switching: a versioned build next to the live
   one, the link untouched.
4. **Smoke test the candidate by its full path**, never through the link:
   ``--version``; one wake-shaped run on the provider's default alias,
   reporting the model it ran; one broker-shaped session start with its MCP
   servers connected (every server that connects on the live build must
   connect on the candidate); the provider's half of
   ``tests/test_codex_parity.py`` (no MCP tool and no built-in the live build
   has may be missing); for Claude Code the alias table, diffed against the
   live build and recorded, never gating.
5. **Switch** when every gating check passes: the link re-pointed, the
   broker restarted only
   once no session is ``working`` or ``asking`` (polled; a failed read counts
   as busy; a turn is never cut), and one line in ``comms.md`` under Other
   with the versions and any alias move.
6. **Watch for an hour, and roll back by itself**: if a lane fails to start
   on the new build -- a wake or cron whose identity preflight or run exits
   non-zero, a wake/cron/reviewer unit failed, a broker session of that provider ending with an error exit, a
   start failure in the broker's journal, the link's ``--version`` failing --
   the link goes back to the previous build, the same careful restarts
   follow, one Important line says what failed, and the build is refused.
   After a clean hour, builds older than the previous two are removed.
   **Only a failure that is the CLI's counts** (2026-11-18): a wake or cron
   unit whose run's own line in ``wake-<agent>.jsonl`` says it was refused
   at preflight, crashed in bin/wake, was refused by the account or the
   sign-in, or was skipped by the lock is excused (``NOT_THE_CLIS``,
   ``explained``), as is another provider's run; a unit whose run left no
   line is still blamed. A build refused before this rule whose every reason
   it now excuses is taken again (``lift_refusals``).

One adapter per provider (``ClaudeCode``, ``Codex``, ``Antigravity``); a
provider added later is one more subclass of ``Provider`` in ``PROVIDERS``.
Antigravity updates itself in the background during its own runs (1.5.3 on
11-09, 1.5.6 on 11-11, nobody moved it), so it has no pin to follow: while it
is not signed in it is skipped like any other; once it is, the run log says it
is signed in and unpinned, and one comms line asks for the pin brief.

Everything that touches the box goes through ``Hands`` so a dry run and the
rollback drill (``bin/provider-follow drill``) take every decision for real
and act only where they are told to.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import loam_config as lc

import loam_broker as cfg
import loam_live

AGENTS = Path(__file__).resolve().parent.parent
HOME = lc.HOME
STATE = Path(os.environ.get("LOAM_FOLLOW_STATE") or lc.STATE / "provider-follow")
LOG = Path(os.environ.get("LOAM_FOLLOW_LOG") or lc.LOGS / "provider-follow.jsonl")
VAULT = Path(os.environ.get("LOAM_FOLLOW_VAULT") or cfg.VAULT)
WAKE_LOGS = Path(os.environ.get("LOAM_FOLLOW_WAKE_LOGS") or lc.LOGS)
POSTER = "loam"
LINK = "`agents/logs/provider-follow.jsonl` (bin/provider-follow)"

# The workspace the smoke runs in: a real persona's, because a lane's reach
# is its workspace's (.mcp.json, settings, CLAUDE.md), with LOAM_PROBE=1 so
# the transcript is quarantined outside the vault. One persona's, as every probe.
PROBE_AGENT = os.environ.get("LOAM_FOLLOW_AGENT", lc.LEAD_AGENT)
# The broker-shaped start and the parity half run on Haiku 4.5 by id, the
# parity test's own model: they prove a launch line and its MCP servers, not
# a model. The wake-shaped run is on the lane's default alias, and runs LAST,
# so the lane's latest session is a Fable one and `check-drift`'s effective
# model is not left on Haiku (MEMORY 2026-11-09).
PROBE_MODEL = "claude-haiku-4-5-20251001"
ALIASES = ("fable", "opus", "sonnet", "haiku")
WAKE_PROMPT = ("This is a smoke test of a new build. Reply with the exact id "
               "of the model you are running on and nothing else. Use no tools.")

WATCH_S = int(os.environ.get("LOAM_FOLLOW_WATCH_S", "3600"))
WATCH_POLL_S = float(os.environ.get("LOAM_FOLLOW_POLL_S", "300"))
QUIET_POLL_S = float(os.environ.get("LOAM_FOLLOW_QUIET_POLL_S", "60"))
# How long a switch waits for a quiet broker before leaving the restart to
# the next run. Not a clock on a turn: nothing is cut, the restart waits.
QUIET_MAX_S = int(os.environ.get("LOAM_FOLLOW_QUIET_MAX_S", str(6 * 3600)))
KEEP_PREVIOUS = 2
# The least time bin/restart-broker is given to see idle again under the
# live lock, after this module's own wait saw it.
RESTART_RECHECK_S = 300

BROKER_UNIT = "loam-session-broker.service"
BUSY_STATES = set(loam_live.RESTART_HOLDS)   # the one rule, lib/loam_live.py
# A session that ended because it was stopped (SIGTERM from the broker or a
# restart, SIGKILL on a Codex process group) is not a failed start.
QUIET_EXITS = {None, 0, 143, -15, 137, -9}
# What a binary that does not start looks like in the broker's journal.
JOURNAL_START_FAILURE = re.compile(
    r"Traceback \(most recent call last\)|No such file or directory|ENOENT"
    r"|exec format error|cannot execute binary|error while loading shared"
    r" libraries|panicked at")
# bin/wake's log stages that mean the CLI itself did not run to the end.
WAKE_FAILURE_STAGES = {"identity", "invoke"}
# And the ones that are never the CLI's (2026-11-18): the lane refused at
# its own preflight, bin/wake crashed in its own code, the account or the
# sign-in refused, the lock skipped it. A wake or cron unit that failed with
# one of these as its run's line is not a failure of the new build. Until
# then every failed wake unit was: Claude Code 2.1.326 was rolled back for a
# reflection refused over a 10,009-character file (11-17), and 2.1.327 for
# a wake that died in `execve` with E2BIG before the CLI existed (11-18).
NOT_THE_CLIS = {"preflight", "crash", "account_refused", "lock_timeout",
                "not_enabled"}


def iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if ts is None else ts,
                                  timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str | None) -> float:
    try:
        return datetime.fromisoformat((text or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def log(provider: str, step: str, ok: bool | None = None, **fields) -> dict:
    """One line in the run log and the same line in the unit's journal."""
    row = {"ts": iso(), "provider": provider, "step": step, "ok": ok, **fields}
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")
    print(json.dumps(row), flush=True)
    return row


# --- versions -------------------------------------------------------------

VERSION_RE = re.compile(r"^\d+(\.\d+)*$")


def vkey(version: str) -> tuple[int, ...]:
    return tuple(int(x) for x in version.split("."))


def is_version(text: str | None) -> bool:
    return bool(text and VERSION_RE.match(text))


def newer(candidate: str | None, live: str | None) -> bool:
    if not is_version(candidate):
        return False
    return not is_version(live) or vkey(candidate) > vkey(live)


def prunable(installed: list[str], live: str | None, keep: int = KEEP_PREVIOUS,
             protect: tuple[str, ...] = ()) -> list[str]:
    """Builds to remove: everything but the live build and the `keep`
    newest older than it. A build newer than live (an installed candidate
    that failed, or one waiting) is left alone, and so is anything protected."""
    builds = sorted((v for v in installed if is_version(v)), key=vkey)
    if not is_version(live):
        return []
    older = [v for v in builds if vkey(v) < vkey(live)]
    kept = set(older[-keep:]) if keep else set()
    return [v for v in older if v not in kept and v not in protect]


def model_name(model_id: str | None) -> str:
    """claude-fable-5-3 -> "Fable 5.3"; anything else as it is."""
    m = re.fullmatch(r"claude-([a-z]+)-(\d+)-(\d+)(?:-\d{8})?(\[1m\])?",
                     model_id or "")
    if not m:
        return model_id or "?"
    return f"{m.group(1).capitalize()} {m.group(2)}.{m.group(3)}"


def alias_moves(before: dict, after: dict) -> list[str]:
    """["fable now means Fable 5.3 (was claude-fable-5-2)"] for every alias
    whose resolution differs. An alias the live build did not report is not a
    move: there is nothing to compare."""
    out = []
    for alias in after:
        old, new = before.get(alias), after.get(alias)
        if old and new and old != new:
            out.append(f"{alias} now means {model_name(new)} (was {old})")
    return out


# --- the broker -----------------------------------------------------------

def broker_live(timeout: float = 10) -> dict | None:
    """`GET /v1/sessions/live`'s body, or None when the read failed."""
    import http.client  # noqa: PLC0415
    try:
        conn = http.client.HTTPConnection(cfg.BIND_ADDR, cfg.PORT, timeout=timeout)
        conn.request("GET", "/v1/sessions/live",
                     headers={"Authorization": f"Bearer {cfg.read_token()}"})
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            return None
        live = json.loads(body)
        return live if isinstance(live, dict) else None
    except (OSError, ValueError):
        return None


def broker_rows(timeout: float = 10) -> list[dict] | None:
    """`GET /v1/sessions/live`'s rows, or None when the read failed."""
    live = broker_live(timeout)
    return None if live is None else list(live.get("sessions") or [])


def restart_read(timeout: float = 10) -> list[str]:
    """One fresh read of what holds a planned restart (`loam_live.
    restart_blockers` over the rows and the thread turns in flight). Empty
    means idle; a failed read is busy."""
    live = broker_live(timeout)
    if live is None:
        return loam_live.restart_blockers(None)
    return loam_live.restart_blockers(live.get("sessions"), live.get("turns"))


def busy(rows: list[dict] | None) -> list[str]:
    """Why the broker may not restart now (`loam_live.restart_blockers`).
    Empty means quiet."""
    return loam_live.restart_blockers(rows)


# --- watch signals ----------------------------------------------------------

def systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True,
                          text=True, check=False)


def wake_rows(path: Path) -> list[dict]:
    """The last 200 rows of one wake log, the unreadable ones skipped."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-200:]
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def wake_failures(since: float, log_dir: Path | None = None,
                  provider: str | None = None) -> list[str]:
    """Wakes and crons that STARTED after `since` and whose CLI did not run
    to the end (bin/wake's own log lines). With `provider`, only a row of
    that provider's run (a row without one predates 2026-11-13 and is
    Claude Code's): a Codex wake's failure is not Claude Code's."""
    out = []
    for path in sorted((log_dir or WAKE_LOGS).glob("wake-*.jsonl")):
        for row in wake_rows(path):
            if provider and (row.get("provider") or "claude-code") != provider:
                continue
            if (row.get("ok") is False and row.get("stage") in WAKE_FAILURE_STAGES
                    and parse_iso(row.get("ts")) >= since):
                detail = row.get("error") or row.get("returncode") or ""
                out.append(f"{path.stem[5:]} {row.get('lane') or 'wake'} "
                           f"{row['stage']} failed at {row.get('ts')} {detail}".strip())
    return out


def failed_units(since: float, patterns: tuple[str, ...]) -> list[str]:
    """Units of these patterns that entered `failed` after `since`."""
    out = systemctl("list-units", *patterns, "--all", "--state=failed",
                    "--plain", "--no-legend").stdout
    found = []
    for line in out.splitlines():
        unit = line.split()[0] if line.split() else ""
        if not unit:
            continue
        stamp = systemctl("show", "-P", "StateChangeTimestamp", "--timestamp=unix",
                          unit).stdout.strip().lstrip("@")
        try:
            at = float(stamp)
        except ValueError:
            at = 0.0
        if at >= since:
            found.append(f"{unit} failed at {iso(at)}")
    return found


_FAILED_UNIT = re.compile(r"^(?P<unit>\S+)\.service failed at (?P<at>\S+)$")


def unit_lane(unit: str) -> tuple[str, str] | None:
    """``(agent, lane)`` for a wake or cron unit's name, else None."""
    if unit.startswith("loam-wake-"):
        return unit[len("loam-wake-"):], "reflection"
    if unit.startswith("loam-cron-"):
        try:
            import loam_cron  # noqa: PLC0415
            agent, name = loam_cron.split_unit(unit)
        except Exception:  # noqa: BLE001 - an unknown unit is blamed
            return None
        return agent, f"cron:{name}"
    return None


def explained(failure: str, log_dir: Path | None = None,
              provider: str | None = None) -> str | None:
    """Why a failed wake or cron unit is not the build's, from its run's own
    line in the wake log; None when it is, or when nothing says.

    The run's line is the lane's last row that started at or before the
    unit failed (within a day). A stage in NOT_THE_CLIS excuses it; so
    does a stage in WAKE_FAILURE_STAGES of this provider's, which
    `wake_failures` already reports -- once is enough; and so does a run of
    another provider. A unit whose run left no line is blamed: nothing says
    it was not the build."""
    hit = _FAILED_UNIT.match(failure)
    lane = unit_lane(hit["unit"]) if hit else None
    if lane is None:
        return None
    agent, lane_name = lane
    at = parse_iso(hit["at"])
    rows = [r for r in wake_rows((log_dir or WAKE_LOGS) / f"wake-{agent}.jsonl")
            if (r.get("lane") or "reflection") == lane_name
            and at - 86400 <= parse_iso(r.get("ts")) <= at + 60]
    if not rows:
        return None
    row = rows[-1]
    stage = row.get("stage")
    if provider and (row.get("provider") or "claude-code") != provider:
        return f"{row.get('provider')}'s run, not this provider's"
    if row.get("ok") is False and stage in NOT_THE_CLIS:
        return f"bin/wake recorded `{stage}`: {(row.get('error') or '')[:160]}"
    if row.get("ok") is False and stage in WAKE_FAILURE_STAGES:
        return f"already counted as the run's `{stage}` failure"
    return None


def blamed(failures: list[str], log_dir: Path | None = None,
           provider: str | None = None) -> list[str]:
    """`failures` without the wake and cron units whose own run line says the
    failure was not the CLI's. Each excusal is a line in the run log."""
    kept = []
    for failure in failures:
        why = explained(failure, log_dir, provider)
        if why:
            log(provider or "box", "excused", None, failure=failure, why=why)
            continue
        kept.append(failure)
    return kept


def broker_session_failures(since: float, provider_id: str,
                            sessions: Path | None = None) -> list[str]:
    out = []
    for path in (sessions or cfg.SESSION_DIR).glob("*.json"):
        try:
            if path.stat().st_mtime < since:
                continue
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if row.get("provider") != provider_id:
            continue
        if row.get("state") == "exited" and row.get("exit_code") not in QUIET_EXITS:
            out.append(f"broker session {path.stem[:8]} ({row.get('agent')}) "
                       f"exited {row.get('exit_code')}")
    return out


def journal_failures(since: float, unit: str = BROKER_UNIT) -> list[str]:
    done = subprocess.run(["journalctl", "--user", "-u", unit, "--since",
                           f"@{int(since)}", "-o", "cat", "--no-pager"],
                          capture_output=True, text=True, check=False)
    return [f"broker journal: {line[:200]}" for line in done.stdout.splitlines()
            if JOURNAL_START_FAILURE.search(line)]


# --- providers ----------------------------------------------------------------

@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    gating: bool = True
    data: dict = field(default_factory=dict)


def run(argv: list[str], timeout: float = 600, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                          check=False, **kw)


def _killpg(pid: int) -> None:
    try:
        os.killpg(pid, 9)
    except (ProcessLookupError, PermissionError):
        pass


def load_source(name: str, path: Path):
    loader = SourceFileLoader(name, str(path))
    module = module_from_spec(spec_from_loader(name, loader))
    loader.exec_module(module)
    return module


def fetch(url: str, timeout: float = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "loam-provider-follow"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read()


class Provider:
    """One provider's build, its link, and how to prove a build of it."""
    name = ""                 # the run log's and the state file's key
    label = ""                # what comms calls it
    broker_provider = ""      # the `provider` a broker session record names
    follows = True            # False: the binary moves itself, nothing to follow

    def __init__(self, link: Path, versions: Path) -> None:
        self.link, self.versions = Path(link), Path(versions)

    # -- what the box has
    def signed_in(self) -> tuple[bool, str]:
        raise NotImplementedError

    def latest(self) -> str:
        raise NotImplementedError

    def build(self, version: str) -> Path:
        """The binary of a version, by its full path."""
        raise NotImplementedError

    def entry(self, version: str) -> Path:
        """What lives in `versions/` for a version (removed by a prune)."""
        return self.versions / version

    def live(self) -> str | None:
        try:
            target = Path(os.readlink(self.link))
        except OSError:
            return None
        if not target.is_absolute():
            target = (self.link.parent / target)
        try:
            rel = target.relative_to(self.versions)
        except ValueError:
            return None
        return rel.parts[0] if rel.parts else None

    def installed(self) -> list[str]:
        try:
            return sorted((p.name for p in self.versions.iterdir()
                           if is_version(p.name) and self.build(p.name).exists()),
                          key=vkey)
        except OSError:
            return []

    def install(self, version: str) -> None:
        raise NotImplementedError

    def repoint(self, version: str) -> None:
        """Move the link to a version's build, atomically."""
        target = self.build(version)
        if not target.exists():
            raise FileNotFoundError(target)
        tmp = self.link.with_name(f".{self.link.name}.follow-{os.getpid()}")
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        os.symlink(target, tmp)
        os.replace(tmp, self.link)

    def remove(self, version: str) -> None:
        entry = self.entry(version)
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink(missing_ok=True)

    # -- proof
    def version_of(self, binary: Path) -> str | None:
        try:
            done = run([str(binary), "--version"], timeout=60,
                       env={**os.environ, "DISABLE_AUTOUPDATER": "1"})
        except (OSError, subprocess.SubprocessError):
            return None
        m = re.search(r"\d+\.\d+\.\d+", done.stdout)
        return m.group(0) if done.returncode == 0 and m else None

    def smoke(self, candidate: str, live: str | None, ws: Path) -> list[Check]:
        raise NotImplementedError

    def lane_failures(self, since: float) -> list[str]:
        """What says a lane of this provider did not start, since `since`."""
        found = []
        if self.version_of(self.link) is None:
            found.append(f"{self.link} --version did not answer")
        found += broker_session_failures(since, self.broker_provider)
        found += [line for line in journal_failures(since)
                  if self.journal_mentions(line)]
        return found

    def journal_mentions(self, line: str) -> bool:
        return True

    def describe_move(self, checks: list[Check]) -> list[str]:
        return []


class ClaudeCode(Provider):
    name, label, broker_provider = "claude-code", "Claude Code", "claude-code"
    DIST_TAGS = "https://registry.npmjs.org/-/package/@anthropic-ai/claude-code/dist-tags"
    DOWNLOADS = "https://downloads.claude.ai/claude-code-releases"

    def __init__(self, link: Path = Path(cfg.CLAUDE),
                 versions: Path = HOME / ".local/share/claude/versions") -> None:
        super().__init__(link, versions)

    def signed_in(self) -> tuple[bool, str]:
        # The lanes run on the operator login (the broker) and on
        # wake.env's token (wakes, crons); either one is a provider in use.
        if cfg.CREDENTIALS_PATH.exists() or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
            return True, "the operator login"
        return False, "no Claude Code login on the box"

    def latest(self) -> str:
        return json.loads(fetch(self.DIST_TAGS))["latest"]

    def build(self, version: str) -> Path:
        return self.versions / version

    @staticmethod
    def platform() -> str:
        arch = {"x86_64": "x64", "aarch64": "arm64"}.get(platform.machine(),
                                                         platform.machine())
        return f"linux-{arch}"

    def install(self, version: str) -> None:
        """Download the build straight from the release bucket, checksum it
        against its manifest, and put it beside the live one. The vendor's
        installer is not used: it re-points the link as it goes."""
        manifest = json.loads(fetch(f"{self.DOWNLOADS}/{version}/manifest.json"))
        want = manifest["platforms"][self.platform()]["checksum"]
        self.versions.mkdir(parents=True, exist_ok=True)
        partial = self.versions / f".{version}.follow-partial"
        blob = fetch(f"{self.DOWNLOADS}/{version}/{self.platform()}/claude", timeout=600)
        if hashlib.sha256(blob).hexdigest() != want:
            raise ValueError(f"{version}: checksum does not match its manifest")
        partial.write_bytes(blob)
        partial.chmod(0o755)
        os.replace(partial, self.build(version))

    def alias_table(self, binary: Path) -> dict:
        """What each alias resolves to on a build, read off its `init` event
        and killed before the turn: no model is asked (about 1 s each)."""
        probe = STATE / "alias-probe"
        probe.mkdir(parents=True, exist_ok=True)
        table = {}
        for alias in ALIASES:
            proc = subprocess.Popen(
                [str(binary), "-p", "hi", "--model", alias, "--output-format",
                 "stream-json", "--verbose", "--strict-mcp-config", "--tools", "",
                 "--no-session-persistence", "--max-turns", "1"],
                cwd=probe, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, text=True, start_new_session=True,
                env={**os.environ, "DISABLE_AUTOUPDATER": "1", "LOAM_PROBE": "1"})
            timer = threading.Timer(60, _killpg, (proc.pid,))
            timer.start()
            try:
                for line in proc.stdout:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if event.get("type") == "system" and event.get("subtype") == "init":
                        table[alias] = event.get("model")
                        break
            finally:
                timer.cancel()
                _killpg(proc.pid)
                proc.wait()
        return table

    def broker_start(self, binary: Path, ws: Path) -> dict:
        """The broker's own launch line (cfg.CLAUDE_STREAM_FLAGS) on a build,
        one message in, its result out; the MCP servers that connected, read
        from --debug-file (init lists stdio servers as pending)."""
        with tempfile.TemporaryDirectory(prefix="follow-") as tmp:
            debug = Path(tmp) / "debug.txt"
            argv = [str(binary), *cfg.CLAUDE_STREAM_FLAGS, "--model", PROBE_MODEL,
                    "--session-id", str(uuid.uuid4()), "--debug-file", str(debug)]
            env = {k: v for k, v in os.environ.items() if k != "LOAM_WAKE"}
            env.update({"DISABLE_AUTOUPDATER": "1", "CLAUDE_PROJECT_DIR": str(ws),
                        "LOAM_PROBE": "1"})
            message = {"type": "user", "message": {"role": "user", "content":
                       "Reply with the single word pong. Use no tools."}}
            done = run(argv, timeout=300, cwd=str(ws), env=env,
                       input=json.dumps(message) + "\n")
            text = debug.read_text(encoding="utf-8", errors="replace") if debug.exists() else ""
        init, result = {}, {}
        for line in done.stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "system" and event.get("subtype") == "init":
                init = event
            if event.get("type") == "result":
                result = event
        return {"rc": done.returncode, "version": init.get("claude_code_version"),
                "servers": sorted(s.get("name") for s in init.get("mcp_servers") or []),
                "connected": sorted(set(re.findall(
                    r'MCP server "([^"]+)": Successfully connected', text))),
                "result": result.get("subtype"), "is_error": result.get("is_error")}

    def wake_run(self, binary: Path, ws: Path) -> dict:
        """bin/wake's own `claude_argv` on a build, the lane's default alias,
        one round trip, LOAM_PROBE=1."""
        wake = load_source("loam_wake_for_follow", AGENTS / "bin" / "wake")
        lane_file = ws / "_reflection.md"
        argv = wake.claude_argv(ws.name, "wake", WAKE_PROMPT, lane_file, max_turns=1)
        argv[0] = str(binary)
        env = {k: v for k, v in os.environ.items() if k != "LOAM_WAKE"}
        env.update({"DISABLE_AUTOUPDATER": "1", "CLAUDE_PROJECT_DIR": str(ws),
                    "LOAM_PROBE": "1"})
        done = run(argv, timeout=600, cwd=str(ws), env=env, stdin=subprocess.DEVNULL)
        try:
            out = json.loads(done.stdout)
        except ValueError:
            out = {}
        return {"rc": done.returncode, "alias": wake.WAKE_MODEL,
                "models": sorted((out.get("modelUsage") or {}).keys()),
                "result": (out.get("result") or "")[:200],
                "is_error": out.get("is_error"), "stderr": done.stderr[-300:]}

    def parity_half(self, binary: Path, ws: Path) -> dict:
        parity = load_source("loam_parity_for_follow",
                             AGENTS / "tests" / "test_codex_parity.py")
        tools, answer = parity.claude_half(ws, str(binary))
        return {"tools": sorted(tools), "answered": bool(answer)}

    def smoke(self, candidate: str, live: str | None, ws: Path) -> list[Check]:
        cand = self.build(candidate)
        base = self.build(live) if live else None
        checks = []
        got = self.version_of(cand)
        checks.append(Check("version", got == candidate, f"{cand} --version: {got}"))
        if got != candidate:
            return checks

        before = self.alias_table(base) if base else {}
        after = self.alias_table(cand)
        checks.append(Check("alias table", bool(after), json.dumps(after), gating=False,
                            data={"before": before, "after": after,
                                  "moves": alias_moves(before, after)}))

        p_live = self.parity_half(base, ws) if base else {"tools": []}
        p_cand = self.parity_half(cand, ws)
        lost = sorted(set(p_live["tools"]) - set(p_cand["tools"]))
        checks.append(Check(
            "parity half", bool(p_cand["tools"]) and p_cand["answered"] and not lost,
            f"{len(p_cand['tools'])} tools ({len(p_live['tools'])} on {live}); "
            f"missing on {candidate}: {lost or 'none'}"))

        b_live = self.broker_start(base, ws) if base else {"connected": []}
        b_cand = self.broker_start(cand, ws)
        lost = sorted(set(b_live["connected"]) - set(b_cand["connected"]))
        checks.append(Check(
            "broker start", b_cand["rc"] == 0 and b_cand["result"] == "success"
            and not b_cand["is_error"] and b_cand["version"] == candidate and not lost,
            f"rc {b_cand['rc']}, init {b_cand['version']}, result {b_cand['result']}; "
            f"MCP connected {b_cand['connected']} of {b_cand['servers']} "
            f"({b_live['connected']} on {live}); lost: {lost or 'none'}",
            data=b_cand))

        w = self.wake_run(cand, ws)
        checks.append(Check(
            "wake run", w["rc"] == 0 and not w["is_error"] and bool(w["models"]),
            f"rc {w['rc']}, --model {w['alias']} ran {w['models']}, said "
            f"{w['result']!r}" + (f"; stderr {w['stderr']!r}" if w["rc"] else ""),
            data=w))
        return checks

    def lane_failures(self, since: float) -> list[str]:
        found = super().lane_failures(since)
        found += wake_failures(since, provider=self.broker_provider)
        found += blamed(failed_units(since, ("loam-wake-*.service",
                                             "loam-cron-*.service",
                                             "loam-reviewer.service")),
                        provider=self.broker_provider)
        return found

    def journal_mentions(self, line: str) -> bool:
        return "codex" not in line.lower()

    def describe_move(self, checks: list[Check]) -> list[str]:
        moves = []
        for c in checks:
            if c.name == "alias table":
                moves += c.data.get("moves", [])
        return moves


class Codex(Provider):
    name, label, broker_provider = "codex", "Codex", "codex"
    DIST_TAGS = "https://registry.npmjs.org/-/package/@openai/codex/dist-tags"

    def __init__(self, link: Path | None = None,
                 versions: Path = HOME / ".local/share/codex/versions") -> None:
        import loam_codex  # noqa: PLC0415
        self.codex = loam_codex
        super().__init__(link or Path(loam_codex.CODEX), versions)

    def signed_in(self) -> tuple[bool, str]:
        state = self.codex.login_state()
        return bool(state.get("connected")), state.get("reason") or "signed in"

    def latest(self) -> str:
        return json.loads(fetch(self.DIST_TAGS))["latest"]

    def build(self, version: str) -> Path:
        return self.versions / version / "node_modules" / ".bin" / "codex"

    def install(self, version: str) -> None:
        """An npm prefix holding `@openai/codex@<v>` and nothing else, the
        shape every Codex pin has had (MEMORY 2026-10-01)."""
        self.versions.mkdir(parents=True, exist_ok=True)
        partial = self.versions / f".{version}.follow-partial"
        shutil.rmtree(partial, ignore_errors=True)
        done = run(["npm", "install", "--prefix", str(partial), "--no-audit",
                    "--no-fund", "--loglevel=error", f"@openai/codex@{version}"],
                   timeout=900)
        if done.returncode != 0:
            shutil.rmtree(partial, ignore_errors=True)
            raise RuntimeError(f"npm install exited {done.returncode}: {done.stderr[-300:]}")
        os.replace(partial, self.versions / version)

    def exec_run(self, binary: Path, ws: Path) -> dict:
        """`codex exec` in the workspace with the broker's CODEX_HOME, the
        unattended shape (MEMORY 2026-11-09), on the login's default model.
        The model and the build come from the rollout it leaves."""
        done = run([str(binary), "exec", "--json", "-C", str(ws), WAKE_PROMPT],
                   timeout=600, cwd=str(ws), env=self.codex.env(),
                   stdin=subprocess.DEVNULL)
        thread, said = None, ""
        for line in done.stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "thread.started":
                thread = event.get("thread_id")
            item = event.get("item") or {}
            if item.get("type") == "agent_message":
                said = item.get("text") or ""
        model = cli = None
        if thread:
            for path in (self.codex.CODEX_HOME / "sessions").rglob(f"*{thread}*.jsonl"):
                for line in path.read_text(encoding="utf-8").splitlines():
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    payload = row.get("payload") or {}
                    if row.get("type") == "session_meta":
                        cli = payload.get("cli_version")
                    if row.get("type") == "turn_context" and payload.get("model"):
                        model = payload["model"]
        return {"rc": done.returncode, "model": model, "cli_version": cli,
                "result": said[:200], "stderr": done.stderr[-300:]}

    def app_server(self, binary: Path, ws: Path) -> dict:
        parity = load_source("loam_parity_for_follow",
                             AGENTS / "tests" / "test_codex_parity.py")
        started, servers = parity.codex_mcp_status(ws, str(binary))
        thread = started.get("thread") or {}
        return {"model": started.get("model"), "cli_version": thread.get("cliVersion"),
                "servers": sorted(s["name"] for s in servers),
                "connected": sorted(s["name"] for s in servers
                                    if s.get("runtimeStatus") == "connected"),
                "tools": sorted(f"mcp__{s['name']}__{t}" for s in servers
                                for t in (s.get("tools") or {}))}

    def smoke(self, candidate: str, live: str | None, ws: Path) -> list[Check]:
        cand = self.build(candidate)
        base = self.build(live) if live else None
        checks = []
        got = self.version_of(cand)
        checks.append(Check("version", got == candidate, f"{cand} --version: {got}"))
        if got != candidate:
            return checks

        a_live = self.app_server(base, ws) if base else {"connected": [], "tools": []}
        a_cand = self.app_server(cand, ws)
        lost = sorted(set(a_live["connected"]) - set(a_cand["connected"]))
        checks.append(Check(
            "broker start", bool(a_cand["model"]) and not lost,
            f"app-server thread on {a_cand['model']}; MCP connected "
            f"{a_cand['connected']} of {a_cand['servers']} ({a_live['connected']} "
            f"on {live}); lost: {lost or 'none'}", data=a_cand))
        lost_tools = sorted(set(a_live["tools"]) - set(a_cand["tools"]))
        checks.append(Check(
            "parity half", bool(a_cand["tools"]) and not lost_tools,
            f"{len(a_cand['tools'])} MCP tools ({len(a_live['tools'])} on {live}); "
            f"missing on {candidate}: {lost_tools or 'none'}"))
        checks.append(Check(
            "default model", bool(a_cand["model"]), str(a_cand["model"]), gating=False,
            data={"before": {"default": a_live.get("model")} if base else {},
                  "after": {"default": a_cand["model"]},
                  "moves": alias_moves({"default": a_live.get("model")},
                                       {"default": a_cand["model"]}) if base else []}))

        w = self.exec_run(cand, ws)
        checks.append(Check(
            "wake run", w["rc"] == 0 and bool(w["result"]) and w["cli_version"] == candidate,
            f"rc {w['rc']}, build {w['cli_version']} ran {w['model']}, said "
            f"{w['result']!r}" + (f"; stderr {w['stderr']!r}" if w["rc"] else ""),
            data=w))
        return checks

    def journal_mentions(self, line: str) -> bool:
        return "codex" in line.lower()

    def describe_move(self, checks: list[Check]) -> list[str]:
        return [m.replace("default now means", "the default model is now")
                for c in checks if c.name == "default model"
                for m in c.data.get("moves", [])]


class Antigravity(Provider):
    """Signed in or not; never followed while it moves itself."""
    name, label, broker_provider = "antigravity", "Antigravity", "antigravity"
    follows = False

    def __init__(self) -> None:
        import loam_antigravity  # noqa: PLC0415
        self.agy = loam_antigravity
        super().__init__(Path(loam_antigravity.AGY), HOME / ".local/share/agy/versions")

    def signed_in(self) -> tuple[bool, str]:
        state = self.agy.login_state(fresh=True)
        return bool(state.get("connected")), state.get("reason") or "signed in"

    def live(self) -> str | None:
        return self.version_of(self.link)


PROVIDERS = (ClaudeCode, Codex, Antigravity)


# --- the hands -------------------------------------------------------------

class Hands:
    """Everything that changes the box. `dry` restarts nothing and writes no
    comms line (it logs what it would have done, after really polling the
    broker); `repoint` False leaves every link where it is."""

    def __init__(self, dry: bool = False, repoint: bool = True,
                 vault: Path | None = None, quiet_max_s: int | None = None) -> None:
        self.dry, self.do_repoint = dry, repoint
        self.vault = vault or VAULT
        self.quiet_max_s = QUIET_MAX_S if quiet_max_s is None else quiet_max_s

    def dry_copy(self) -> "Hands":
        return Hands(dry=True, repoint=False, vault=self.vault, quiet_max_s=0)

    def repoint(self, p: Provider, version: str) -> None:
        if self.do_repoint:
            p.repoint(version)
        log(p.name, "repoint", True, to=version, dry=not self.do_repoint,
            link=str(p.link))

    def restart_broker(self) -> bool:
        """Once every session is idle and no thread turn is in flight.
        Polled; a failed read is busy; never past `quiet_max_s` (then the next
        run restarts it). The decision itself is bin/restart-broker
        --when-idle's, under the live lock and on fresh reads (2026-11-17:
        until then one clear read here was followed by a plain restart,
        however long the lock took)."""
        started = time.monotonic()
        while True:
            why = restart_read()
            if not why:
                break
            if time.monotonic() - started >= self.quiet_max_s:
                log("box", "restart-broker", False, deferred=True, busy=why,
                    waited_s=round(time.monotonic() - started))
                return False
            log("box", "restart-broker-wait", None, busy=why)
            time.sleep(QUIET_POLL_S)
        if self.dry:
            log("box", "restart-broker", True, dry=True, note="quiet now; would restart")
            return True
        left = max(RESTART_RECHECK_S,
                   self.quiet_max_s - (time.monotonic() - started))
        done = run([str(AGENTS / "bin" / "restart-broker"), "--when-idle",
                    "--max-wait", str(round(left)), "--ceiling", "0"],
                   timeout=left + 900)
        ok = done.returncode == 0
        log("box", "restart-broker", ok, waited_s=round(time.monotonic() - started),
            rc=done.returncode, **({"deferred": True} if done.returncode == 75 else {}))
        return ok

    def comms(self, section: str, text: str) -> bool:
        if self.dry:
            log("box", "comms", None, dry=True, section=section, text=text)
            return False
        import loam_inbox  # noqa: PLC0415
        wrote = loam_inbox.append_comms(self.vault, POSTER, text, LINK, section=section)
        log("box", "comms", wrote, section=section, text=text)
        return wrote


# --- state -------------------------------------------------------------------

def state_path(p: Provider) -> Path:
    return STATE / f"{p.name}.json"


def read_state(p: Provider) -> dict:
    try:
        return json.loads(state_path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_state(p: Provider, state: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = state_path(p).with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, state_path(p))


class Busy(Exception):
    pass


def locked():
    """One follower at a time; a second run says so and leaves."""
    STATE.mkdir(parents=True, exist_ok=True)
    handle = (STATE / ".lock").open("w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise Busy("another provider-follow run holds the lock") from exc
    return handle


# --- one run -----------------------------------------------------------------

@dataclass
class Plan:
    provider: Provider
    candidate: str
    live: str | None
    checks: list[Check] = field(default_factory=list)
    rehearsal: bool = False      # the live build smoked as its own candidate

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(c.ok for c in self.checks if c.gating)


def decide(p: Provider, force: bool, hands: Hands | None = None) -> Plan | None:
    """Steps 1-3 for one provider: skip, nothing to do, or a candidate on
    disk beside the live build."""
    ok, why = p.signed_in()
    if not ok:
        log(p.name, "skip", None, reason=f"not signed in: {why}")
        return None
    if not p.follows:
        version = p.live()
        log(p.name, "skip", None, reason=f"signed in; it updates itself "
            f"({version}) and has no pin to follow yet")
        if hands is not None:
            hands.comms("Other", f"{p.label} is signed in and updates itself; "
                        f"the daily follower cannot smoke-test "
                        f"its builds until it is pinned like Claude Code and "
                        f"Codex, which is a brief to the platform engineer")
        return None
    live = p.live()
    try:
        latest = p.latest()
    except (OSError, ValueError, KeyError) as exc:
        log(p.name, "latest", False, error=str(exc)[:300], live=live)
        return None
    refused = lift_refusals(p)
    log(p.name, "latest", True, latest=latest, live=live)
    if latest in refused:
        log(p.name, "skip", None, reason=f"{latest} was rolled back on "
            f"{refused[latest].get('at')}: {refused[latest].get('reason')}")
        return None
    if not newer(latest, live):
        if force and live:
            log(p.name, "current", None, live=live, note="forced: the live build "
                "is smoked as its own candidate and the switch is a dry run")
            return Plan(p, live, live, rehearsal=True)
        log(p.name, "current", None, live=live)
        return None
    if latest not in p.installed():
        try:
            p.install(latest)
        except (OSError, ValueError, KeyError, RuntimeError,
                subprocess.SubprocessError) as exc:
            log(p.name, "install", False, version=latest, error=str(exc)[:300])
            return None
        log(p.name, "install", True, version=latest, path=str(p.build(latest)))
    else:
        log(p.name, "install", True, version=latest, note="already on disk")
    return Plan(p, latest, live)


def smoke(plan: Plan, ws: Path) -> None:
    p = plan.provider
    try:
        plan.checks = p.smoke(plan.candidate, plan.live, ws)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        plan.checks = [Check("smoke", False, f"{type(exc).__name__}: {exc}"[:400])]
    for c in plan.checks:
        log(p.name, f"smoke: {c.name}", c.ok, gating=c.gating, detail=c.detail,
            candidate=plan.candidate)
    log(p.name, "smoke", plan.passed, candidate=plan.candidate)


def switch_line(plan: Plan) -> str:
    p = plan.provider
    moves = p.describe_move(plan.checks)
    wake = next((c for c in plan.checks if c.name == "wake run"), None)
    ran = ""
    if wake:
        models = wake.data.get("models") or [wake.data.get("model")]
        ran = f", a wake-shaped run on {', '.join(m for m in models if m)}"
    text = (f"{p.label} {plan.live} → {plan.candidate} is live by itself, after "
            f"its smoke test (version{ran}, a broker start, the parity half)")
    if moves:
        text += "; " + "; ".join(moves)
    return text + f". Rollback is automatic for an hour; {plan.live} stays on disk"


def switch(plans: list[Plan], hands: Hands) -> list[Plan]:
    """Step 5 for every plan that passed, with one broker restart between
    them."""
    went = []
    for plan in plans:
        p = plan.provider
        if not plan.passed:
            failed = [f"{c.name}: {c.detail}" for c in plan.checks if c.gating and not c.ok]
            log(p.name, "switch", False, candidate=plan.candidate, reason=failed)
            if not plan.rehearsal:
                names = ", ".join(c.name for c in plan.checks if c.gating and not c.ok)
                hands.comms("Important", f"{p.label} {plan.candidate} failed its "
                            f"smoke test ({names}) and is not live; {plan.live} "
                            f"stays, and the build is tried again at the next run")
            continue
        if plan.rehearsal:
            log(p.name, "repoint", True, to=plan.candidate, dry=True,
                note="rehearsal: the link is not moved")
        else:
            hands.repoint(p, plan.candidate)
        went.append(plan)
    if not went:
        return went
    real = [pl for pl in went if not pl.rehearsal]
    repointed = time.time()
    # A rehearsal (the live build smoked as its own candidate) takes every
    # decision and acts on none: the broker is polled for real and not
    # restarted. A real switch in the same run acts.
    act = hands if real else hands.dry_copy()
    restarted = act.restart_broker()
    now = time.time()
    for plan in went:
        p = plan.provider
        if plan.rehearsal:
            log(p.name, "comms", None, dry=True, would_write=switch_line(plan))
            continue
        hands.comms("Other", switch_line(plan))
        state = read_state(p)
        state.update({"live": plan.candidate, "previous": plan.live,
                      "switched_at": iso(repointed), "restarted_at": iso(now),
                      "watch_until": iso(now + WATCH_S),
                      "watched": False, "broker_restarted": restarted,
                      "checks": [c.__dict__ for c in plan.checks]})
        write_state(p, state)
    if real:
        memory_pins(hands)
    return went


def memory_pins(hands: Hands) -> None:
    """After a switch: every provider's own memory re-pinned off and its
    store checked (first release L8, lib/loam_provider_memory.py). A new
    build may have turned a setting back on or renamed it; a store that grew
    goes to comms as Important. A failure to pin is Important too."""
    import loam_provider_memory as pm  # noqa: PLC0415
    try:
        wrote = pm.pin_memory()
        counts, grew = pm.check()
    except Exception as exc:  # noqa: BLE001 -- said, not swallowed
        log("box", "memory-pins", False, error=str(exc))
        hands.comms("Important", f"the provider memory pins could not be "
                    f"checked after the switch: {exc}")
        return
    log("box", "memory-pins", not grew, wrote=wrote, counts=counts, grew=grew)
    for text in grew:
        hands.comms("Important", text)


def clear_agy_files(dry: bool = False) -> list[dict]:
    """Antigravity's per-conversation files, cleared after a week
    (lib/loam_agy_files.py): an engineer job's a week after the job
    (2026-11-15), every other conversation once idle a week (2026-11-16),
    as this nightly pass's last step. Logged, never said in comms: it is housekeeping the
    owner agreed to, and a failure is retried the next night."""
    import loam_agy_files  # noqa: PLC0415
    try:
        rows = loam_agy_files.clear(dry=dry)
    except Exception as exc:  # noqa: BLE001 -- logged, not swallowed
        log("antigravity", "clear-job-files", False, error=str(exc), dry=dry)
        return []
    for row in rows:
        log("antigravity", "clear-job-files", row["error"] is None, dry=dry, **row)
    return rows


def lift_refusals(p: Provider, log_dir: Path | None = None) -> dict:
    """The provider's refused builds, less any whose every reason the wake
    log now says was not the CLI's (2026-11-18). A rollback before the rule
    in ``explained`` existed blamed a build for a wake its own launcher
    refused or crashed; such a build is taken again, by the ordinary route
    -- installed beside, smoked, switched, watched -- and the lifting is a
    line in the run log. Returns what stays refused."""
    state = read_state(p)
    refused = state.get("refused", {})
    lifted = {}
    for version, entry in list(refused.items()):
        reasons = entry.get("reasons") or [
            part.strip() for part in str(entry.get("reason") or "").split("; ")
            if part.strip()]
        whys = [explained(r, log_dir, p.broker_provider) for r in reasons]
        if reasons and all(whys):
            lifted[version] = whys
            refused.pop(version)
    if lifted:
        state["refused"] = refused
        state.setdefault("lifted", {}).update(
            {v: {"at": iso(), "why": w} for v, w in lifted.items()})
        write_state(p, state)
        for version, whys in lifted.items():
            log(p.name, "refusal lifted", True, version=version, why=whys)
    return refused


def rollback(p: Provider, hands: Hands, state: dict, reasons: list[str]) -> None:
    bad, back = state["live"], state["previous"]
    hands.repoint(p, back)
    hands.restart_broker()
    what = "; ".join(reasons)[:300]
    hands.comms("Important", f"{p.label} {bad} rolled back to {back} by itself: "
                f"{what}. {bad} will not be taken again; the next release will")
    state.setdefault("refused", {})[bad] = {"at": iso(), "reason": what,
                                            "reasons": reasons}
    state.update({"live": back, "previous": None, "watched": True,
                  "rolled_back_at": iso()})
    write_state(p, state)
    log(p.name, "rollback", True, from_=bad, to=back, reasons=reasons)


def watch(providers: list[Provider], hands: Hands,
          poll_s: float | None = None) -> None:
    """Step 6 for every provider whose switch is inside its first hour."""
    poll_s = WATCH_POLL_S if poll_s is None else poll_s
    while True:
        open_ = []
        for p in providers:
            state = read_state(p)
            if not state.get("switched_at") or state.get("watched"):
                continue
            since = parse_iso(state["switched_at"])
            found = p.lane_failures(since)
            if found:
                log(p.name, "watch", False, failures=found)
                rollback(p, hands, state, found)
                continue
            if time.time() >= parse_iso(state["watch_until"]):
                state["watched"] = True
                write_state(p, state)
                log(p.name, "watch", True, note="a clean hour on "
                    f"{state['live']}; rollback window closed")
                prune(p, protect=(state.get("previous") or "",))
                continue
            open_.append(p)
        if not open_:
            return
        time.sleep(poll_s)


def watch_rehearsal(p: Provider, window_s: int = WATCH_S) -> list[str]:
    """The watch's signals, read once over the last hour, acting on nothing."""
    found = p.lane_failures(time.time() - window_s)
    log(p.name, "watch", not found, dry=True, window_s=window_s,
        failures=found, note="rehearsal: would roll back" if found else
        "rehearsal: nothing would roll back")
    return found


def prune(p: Provider, protect: tuple[str, ...] = ()) -> list[str]:
    gone = prunable(p.installed(), p.live(), protect=protect)
    for version in gone:
        p.remove(version)
    log(p.name, "prune", True, removed=gone, kept=p.installed())
    return gone


def follow(providers: list[Provider], hands: Hands, force: bool = False,
           ws: Path | None = None) -> list[Plan]:
    """One whole run: resume any open watch, then steps 1-6."""
    ws = ws or cfg.AI_LABS / PROBE_AGENT
    watch(providers, hands)
    plans = []
    for p in providers:
        plan = decide(p, force, hands)
        if plan is None:
            continue
        if not hands.do_repoint:
            plan.rehearsal = True        # --dry-run: a newer build rehearses too
        smoke(plan, ws)
        plans.append(plan)
    went = switch(plans, hands)
    for pl in went:
        if pl.rehearsal:
            watch_rehearsal(pl.provider)
            log(pl.provider.name, "prune", None, dry=True,
                would_remove=prunable(pl.provider.installed(), pl.provider.live()))
    if any(not pl.rehearsal for pl in went):
        watch([pl.provider for pl in went if not pl.rehearsal], hands)
    return plans
