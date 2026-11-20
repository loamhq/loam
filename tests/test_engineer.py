"""Verify the engineer's project boundary and brief admission through the tool and
launcher.

Run: python3 tests/test_engineer.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_engineer as eng  # noqa: E402

GUARD = str(Path(__file__).resolve().parent.parent / "hooks" / "boundary_guard.py")
PT = ZoneInfo("America/Los_Angeles")

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:44} {got!r}"
          + ("" if ok else f"   want {want!r}"))


# ---------------------------------------------------------------------------
# The boundary, against a throwaway tree shaped like the real one.
# ---------------------------------------------------------------------------

root = Path(tempfile.mkdtemp(prefix="loam-engineer-test-"))
try:
    agents = root / "agents"
    labs = root / "ai-labs"
    vault = root / "vault"
    engineer = agents / "engineer"
    (engineer / ".claude").mkdir(parents=True)
    (labs / "ada" / ".claude").mkdir(parents=True)
    (labs / "ada" / "crons").mkdir(parents=True)
    (vault / "15_CONVERSATIONS").mkdir(parents=True)
    (vault / "40_PROJECTS").mkdir(parents=True)
    (engineer / "agent.json").write_text(json.dumps({
        "name": "engineer", "kind": "engineer", "display": "Engineer",
        "project": "Loam", "workspace": str(engineer), "vault": str(vault),
        "ai_labs": str(labs),
    }))

    def run(tool: str, tool_input: dict) -> tuple[bool, str]:
        out = subprocess.run(
            [sys.executable, GUARD], input=json.dumps({
                "tool_name": tool, "tool_input": tool_input,
                "cwd": str(engineer),
            }), capture_output=True, text=True,
            env={"PATH": "/usr/bin:/bin", "CLAUDE_PROJECT_DIR": str(engineer),
                 **({"LOAM_CONFIG": os.environ["LOAM_CONFIG"]}
                    if os.environ.get("LOAM_CONFIG") else {})},
        )
        return out.returncode == 0, (out.stderr or "").strip()

    CASES = [
        # (label, tool, input, expect_allowed)
        # --- the runtime is its project. All of it. ---
        ("write a generator",        "Write", {"file_path": f"{agents}/bin/provision"}, True),
        ("write this very hook",     "Write", {"file_path": f"{agents}/hooks/boundary_guard.py"}, True),
        ("edit a lib",               "Edit",  {"file_path": f"{agents}/lib/loam_identity.py"}, True),
        ("write its own memory",     "Write", {"file_path": f"{engineer}/memory/units.md"}, True),
        ("write a job receipt",      "Write", {"file_path": f"{engineer}/jobs/x/receipt.md"}, True),

        # --- the vault, minus the transcript writer's folder ---
        ("write a vault report",     "Write", {"file_path": f"{vault}/40_PROJECTS/Loam/reports/x.md"}, True),
        ("write 15_CONVERSATIONS",   "Write", {"file_path": f"{vault}/15_CONVERSATIONS/x.md"}, False),

        # --- nothing else on the box ---
        ("write authorized_keys",    "Write", {"file_path": f"{eng.lc.HOME}/.ssh/authorized_keys"}, False),
        ("write a systemd unit",     "Write", {"file_path": f"{eng.lc.HOME}/.config/systemd/user/x.service"}, False),
        ("traversal out of agents",  "Write", {"file_path": f"{agents}/../.ssh/id_rsa"}, False),

        # --- ai-labs: provision, never compose ---
        ("write agent CLAUDE.md",    "Write", {"file_path": f"{labs}/ada/CLAUDE.md"}, True),
        ("write agent agent.json",   "Write", {"file_path": f"{labs}/ada/agent.json"}, True),
        ("write agent .mcp.json",    "Write", {"file_path": f"{labs}/ada/.mcp.json"}, True),
        ("write config.md (clause)", "Write", {"file_path": f"{labs}/ada/config.md"}, True),
        ("EDIT config.md",           "Edit",  {"file_path": f"{labs}/ada/config.md"}, False),
        ("write soul.md",            "Write", {"file_path": f"{labs}/ada/soul.md"}, False),
        ("write _reflection.md",     "Write", {"file_path": f"{labs}/ada/_reflection.md"}, False),
        ("write _always.md",         "Write", {"file_path": f"{labs}/ada/_always.md"}, False),
        ("write _active.md",         "Write", {"file_path": f"{labs}/ada/_active.md"}, False),
        ("edit _reflection.md",      "Edit",  {"file_path": f"{labs}/ada/_reflection.md"}, False),
        ("write a cron file",        "Write", {"file_path": f"{labs}/ada/crons/market-watch.md"}, False),
        ("write agent memory",       "Write", {"file_path": f"{labs}/ada/memory/MEMORY.md"}, False),
        ("write agent skill",        "Write", {"file_path": f"{labs}/ada/skills/x/SKILL.md"}, False),
        ("write the commons",        "Write", {"file_path": f"{labs}/commons/note.md"}, False),

        # --- .claude/ is the generator's, everywhere ---
        ("write agent .claude",      "Write", {"file_path": f"{labs}/ada/.claude/settings.json"}, False),
        ("write own .claude",        "Write", {"file_path": f"{engineer}/.claude/settings.json"}, False),

        # --- reads are never the guard's business ---
        ("read anything",            "Read",  {"file_path": "/etc/passwd"}, True),
    ]

    for label, tool, inp, expect in CASES:
        allowed, err = run(tool, inp)
        ok = allowed == expect
        fails += not ok
        detail = "" if allowed else "  | " + err.replace("\n", " ")[:70]
        print(f"{'PASS' if ok else 'FAIL'}  {label:28} allowed={allowed}{detail}")
finally:
    shutil.rmtree(root, ignore_errors=True)

print()

# ---------------------------------------------------------------------------
# The clocks.
# ---------------------------------------------------------------------------


# No recycle to refuse a job at, and a job has no clock and no cap unless set.
check("no crosses_recycle refusal left", hasattr(eng, "crosses_recycle"), False)
check("no recycle clock left", hasattr(eng, "next_recycle") or hasattr(eng, "next_start"), False)
check("no default clock", eng.DEFAULT_MAX_MINUTES, None)
check("no round-trip cap by default",
      eng.MAX_TURNS if not os.environ.get("LOAM_ENGINEER_MAX_TURNS") else None, None)

# The slug is the receipt's filename, and the owner reads it in a folder listing.
check("slug from a title line",
      eng.slug_for("Make the decoy sampled, not deterministic"),
      "make-the-decoy-sampled-not-deterministic")
check("slug skips frontmatter",
      eng.slug_for("---\nproject: Loam\nbriefed_by: ada\n---\n\n# Fix the decoy\n"),
      "fix-the-decoy")
check("slug of an empty brief", eng.slug_for(""), "job")
check("slug of a title with a colon is the title",
      eng.slug_for("Probe: an unattended lane reaches the engineer"),
      "probe-an-unattended-lane-reaches-the-engineer")

# Two briefs in the same second must not collide: the brief is written before
# the lock is taken, so a shared directory would lose one of them.
#
# Asserted as the GUARANTEE, not as the odds. The first version of this checked
# that 200 raw ids were all distinct and duly failed at 199 -- the birthday
# bound over four hex digits, which is a real defect rather than a flaky test.
# claim_job_dir creates the directory exclusively, so the property holds
# however the random suffix falls.
claim_root = Path(tempfile.mkdtemp(prefix="loam-jobid-test-"))
try:
    ids = set()
    for _ in range(200):
        job_id, directory = eng.claim_job_dir(claim_root)
        ids.add(job_id)
        check_dir = directory.is_dir()
    check("200 claims give 200 distinct job dirs", len(ids), 200)
    check("each claim created its own directory",
          len(list((claim_root / "jobs").iterdir())), 200)
    # A second claim on an id that already exists must not reuse the directory.
    taken = eng.job_dir(claim_root, sorted(ids)[0])
    check("an existing dir is never re-claimed",
          eng.claim_job_dir(claim_root)[1] != taken, True)
finally:
    shutil.rmtree(claim_root, ignore_errors=True)

print()

# ---------------------------------------------------------------------------
# The launcher's two entry forms, driven as the two vintages of server call
# them. Only paths that are refused BEFORE a directory is claimed, so this
# leaves nothing on disk and launches nothing -- the jobs root is the real
# one, and a test that minted a job there would be a test that ran a job.
#
# Why two forms exist: the persona-side server lives as long as its
# conversation, so a server started last week calls the positional form with
# an id it minted itself, while one started today hands the tool's arguments
# to `--brief` and mints nothing. The launcher must accept both, forever.
# ---------------------------------------------------------------------------

LAUNCHER = str(Path(__file__).resolve().parent.parent / "bin" / "engineer-job")
JOBS = eng.lc.JOBS  # job state lives outside the checkout

# Configure the allowed platform briefers through the scratch install's config.
BRIEFER = eng.lc.LEAD_AGENT
_cfg_dir = tempfile.mkdtemp(prefix="test-engineer-config-")
CONFIG = Path(_cfg_dir) / "loam.toml"
_live = eng.lc.CONFIG_FILE.read_text() if eng.lc.CONFIG_FILE.is_file() else ""
CONFIG.write_text(_live if eng.lc.get("engineer.briefers") == [BRIEFER]
                  else "\n".join(l for l in _live.splitlines()
                                 if not l.strip().startswith("briefers"))
                  + f'\n[engineer]\nbriefers = ["{BRIEFER}"]\n')


def launcher(argv: list[str], stdin: str = "",
             agent: str = BRIEFER,
             lane: str = "") -> subprocess.CompletedProcess:
    """Invoke the launcher with the caller identity the persona's MCP server would supply."""
    return subprocess.run(
        [LAUNCHER, *argv], input=stdin, capture_output=True, text=True,
        check=False, timeout=60,
        env={"PATH": f"/usr/bin:/bin:{eng.lc.LOCAL_BIN}",
             "HOME": str(eng.lc.HOME), "LOAM_AGENT": agent,
             "LOAM_CONFIG": str(CONFIG),
             **({"LOAM_WAKE": lane} if lane else {})},
    )


def tool(args, agent: str = BRIEFER,
         lane: str = "") -> tuple[int, dict]:
    out = launcher(["--brief"], agent=agent, lane=lane,
                   stdin=args if isinstance(args, str) else json.dumps(args))
    try:
        return out.returncode, json.loads(out.stdout)
    except ValueError:
        return out.returncode, {"_not_json": out.stdout, "_stderr": out.stderr}


jobs_before = sorted(JOBS.iterdir()) if JOBS.is_dir() else []

# --- the positional form, as a server from before today calls it ---
old = launcher(["no-such-project", "20260907-000000-abcd1234"])
check("positional, unknown name: exit 64", old.returncode, 64)
check("positional, unknown name: names the known", "Loam" in old.stderr, True)
old = launcher([f"{eng.lc.ROOT}/engineer", "../escape"])
check("positional, by path, bad id: exit 64", old.returncode, 64)
check("positional, by path, bad id: says so", "not a job id" in old.stderr, True)

# --- the tool form, as today's server calls it ---
code, reply = tool({"project": "no-such-project", "title": "t", "brief": "b",
                    "involvement": "go"})
check("tool, unknown name: ok is False", reply.get("ok"), False)
check("tool, unknown name: names the known", "Loam" in reply.get("error", ""), True)
check("tool, unknown name: exit 3", code, 3)
code, reply = tool({"project": "Loam", "title": "t", "brief": "   ",
                    "involvement": "go"})
check("tool, known name, empty brief: refused",
      reply.get("error", "").startswith("The brief is empty"), True)
code, reply = tool({"project": "Loam", "title": "", "brief": "do a thing",
                    "involvement": "go"})
check("tool, empty title: refused",
      reply.get("error", "").startswith("title is empty"), True)
code, reply = tool({"project": "Loam", "title": "t", "brief": "do a thing",
                    "involvement": ""})
check("tool, empty involvement: refused",
      reply.get("error", "").startswith("involvement is empty"), True)
code, reply = tool({})
check("tool, no arguments at all: unknown project",
      reply.get("error", "").startswith("No engineer for project ''"), True)
code, reply = tool("this is not json")
check("tool, stdin not JSON: says so, as JSON",
      "not JSON" in reply.get("error", ""), True)
check("tool, stdin not JSON: exit 2", code, 2)
code, reply = tool("[1, 2]")
check("tool, stdin a list: refused, as JSON", reply.get("ok"), False)

# Reject invalid continuations before claiming a new job. A live job and a finished job
# without a session need different remedies.
def cont(extra: dict) -> str:
    _, reply = tool({"project": "Loam", "title": "t", "brief": "do a thing",
                     "involvement": "go", **extra})
    return reply.get("error", "")


# An empty `continue_job` is not a continuation. Asserted against a brief
# that is refused for a DIFFERENT reason, because a brief this one would
# accept is a brief that starts a real job, and a test does not run one.
check("continue_job empty: not a continuation refusal",
      cont({"continue_job": "", "involvement": ""}).startswith("involvement is empty"),
      True)
check("continue_job not a job id: says so",
      "is not a job id" in cont({"continue_job": "../escape"}), True)
check("continue_job unknown id: names the project",
      "there is no job 20260101-000000-deadbeef for Loam"
      in cont({"continue_job": "20260101-000000-deadbeef"}), True)

fixture_id = "20260101-000000-aaaabbbb"
fixture = JOBS / fixture_id
fixture.mkdir(parents=True, exist_ok=True)
try:
    # Exists, but nothing recorded a session for it: the shape of every job
    # run before this change, and of every probe and refusal.
    check("continue_job with no session recorded: refused",
          "has no session to resume" in cont({"continue_job": fixture_id}), True)

    # A session id on disk whose transcript is not there. The CLI would fail
    # on --resume AFTER the job was minted and launched, which is the whole
    # reason this is checked here instead.
    (fixture / "session-id").write_text(
        "00000000-0000-4000-8000-000000000000\n", encoding="utf-8")
    check("continue_job whose transcript is gone: refused",
          "transcript is gone" in cont({"continue_job": fixture_id}), True)

    # Still running: no verdict on disk and a heartbeat still ticking. The
    # two files the runtime already keeps, so this needs no systemd.
    (fixture / "heartbeat").write_text("2026-01-01T00:00:00Z", encoding="utf-8")
    check("a fresh heartbeat with no verdict reads as running",
          "is still running" in cont({"continue_job": fixture_id}), True)
    # ...and the same directory once a verdict exists is finished, whatever
    # the heartbeat says. It falls through to the transcript refusal above.
    (fixture / "result.json").write_text("{}", encoding="utf-8")
    check("a verdict on disk reads as finished",
          "is still running" in cont({"continue_job": fixture_id}), False)
finally:
    shutil.rmtree(fixture, ignore_errors=True)

# Another project's job. Refused whichever way it is sent, and the sentence
# names the project that owns it -- a session can only be resumed in the tree
# it ran in. (The Loam engineer's app-only scope is not another project: a
# Loam brief continuing one of its jobs is routed to it, tests/test_app_scope.py.)
others = [p for p in eng.engineers().values() if not p.platform]
if others:
    other = others[0]
    other_id = "20260101-000000-ccccdddd"
    other_dir = eng.job_dir(other, other_id)
    other_dir.mkdir(parents=True, exist_ok=True)
    try:
        said = cont({"continue_job": other_id})
        check("another project's job: refused",
              f"belongs to project {other.name}" in said, True)
        check("another project's job: says why", "the tree it ran in" in said, True)
    finally:
        shutil.rmtree(other_dir, ignore_errors=True)
else:
    print("SKIP  another project's job: no project engineer registered")

print()

# Check caller authorization at both the tool and launcher entry points.

platform = eng.platform_project()
other_name = others[0].name if others else None


def load_launcher():
    """bin/engineer-job as a module. It has no `.py` name and nothing at
    import but its own constants, so this is how `deploy_row` -- the receipt's
    half of the rule -- is asserted without running a job."""
    import importlib.machinery  # noqa: PLC0415
    import importlib.util  # noqa: PLC0415
    loader = importlib.machinery.SourceFileLoader("engineer_job", LAUNCHER)
    spec = importlib.util.spec_from_loader("engineer_job", loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

check("deploy_target reads the shape the five real ones used",
      eng.deploy_target(f"Deploy {other_name} at 90496b3") if other_name else "SKIP",
      other_name if other_name else "SKIP")
check("deploy_target: the verb is case-insensitive",
      eng.deploy_target(f"deploy {other_name} at 90496b3") if other_name else "SKIP",
      other_name if other_name else "SKIP")
check("deploy_target: a name no engineer serves is not a target",
      eng.deploy_target("Deploy nosuchproject at 90496b3"), None)
check("deploy_target: Loam itself is not a deploy target",
      eng.deploy_target("Deploy Loam at 90496b3"), None)
check("deploy_target: a title that only mentions deploying is not one",
      eng.deploy_target("Fix the deploy generator"), None)
check("deploy_target: empty title", eng.deploy_target(""), None)

ONLY = (BRIEFER,)
check(f"{BRIEFER} may brief Loam", eng.brief_refusal(platform, BRIEFER, "Anything", ONLY), None)
check("the briefer capitalised is still the briefer",
      eng.brief_refusal(platform, BRIEFER.capitalize(), "Anything", ONLY), None)
refusal = eng.brief_refusal(platform, "otto", "Scope the engineer surface", ONLY)
check("another persona may not brief Loam", refusal is None, False)
check("the refusal names who may send it", f"briefed by {BRIEFER}" in refusal, True)
check("the refusal names where the rule is set", "`engineer.briefers`" in refusal, True)
# The deploy lane covers every deployable project, the app-only scope's
# app (`Deploy loam-ui`) included.
deployable = [p for p in eng.projects().values() if not p.platform]
check("the refusal names the deploy lane it could have used",
      ("`Deploy <project>`" if deployable else "no project engineer is provisioned")
      in refusal, True)
check("the refusal names the provisioned projects",
      all(p.name in refusal for p in deployable) if deployable else True, True)
check("...and, with an app, the app-only scope any persona may brief",
      ("`scope: app`" in refusal) if eng.app_lane() is not None else True, True)
check("the refusal quotes the title it refused",
      "'Scope the engineer surface'" in refusal, True)
check("the refusal says it is not queued", "not queued" in refusal, True)
check("a brief naming no briefer is refused too",
      eng.brief_refusal(platform, "", "Anything", ONLY) is None, False)
check("...and says so rather than naming a persona",
      "from nobody" in eng.brief_refusal(platform, "", "Anything", ONLY), True)
two = eng.brief_refusal(platform, "otto", "Anything", ("ada", "iris"))
check("two briefers: both may, the refusal names both",
      eng.brief_refusal(platform, "iris", "Anything", ("ada", "iris")) is None
      and "briefed by ada and iris and by nobody else" in two, True)
check("no list (a fresh install): every persona may brief Loam",
      eng.brief_refusal(platform, "otto", "Anything", ()), None)
check("...but a brief naming nobody is still refused",
      "names nobody" in (eng.brief_refusal(platform, "", "Anything", ()) or ""), True)
if other_name:
    check("the deploy lane is open to another persona",
          eng.brief_refusal(platform, "otto", f"Deploy {other_name} at 90496b3", ONLY), None)
    check("a deploy of a name no engineer serves is not the lane",
          eng.brief_refusal(platform, "otto", "Deploy nosuchproject at 1", ONLY) is None, False)
    for who in (BRIEFER, "otto", "unknown", ""):
        check(f"a project engineer is open to {who or 'an unnamed briefer'}",
              eng.brief_refusal(others[0], who, "Anything", ONLY), None)

# --- caused through the tool door ---
code, reply = tool({"project": "Loam", "title": "Scope the engineer surface",
                    "brief": "do a thing", "involvement": "go"}, agent="otto")
check("tool, Loam from another persona: refused", reply.get("ok"), False)
check("tool, Loam from another persona: the rule's sentence",
      f"briefed by {BRIEFER} and by nobody else" in reply.get("error", ""), True)
check("tool, Loam from another persona: exit 3", code, 3)
# The authority answer comes before the hygiene ones: a persona that may not
# send this brief at all should not be sent away to fill in a title first.
code, reply = tool({"project": "Loam", "title": "", "brief": "",
                    "involvement": ""}, agent="otto")
check("tool, Loam from another persona: authority before hygiene",
      f"briefed by {BRIEFER}" in reply.get("error", ""), True)

# Exercise briefs from each supported calling lane.
for lane in ("reflection", "cron:strategic-watch", "cron:first-resize", "review:1"):
    code, reply = tool({"project": "Loam", "title": "", "brief": "do a thing",
                        "involvement": "go"}, lane=lane)
    check(f"tool, {BRIEFER}'s {lane}: past the lane, to the title",
          ("nobody in the conversation" in reply.get("error", ""),
           reply.get("error", "").startswith("title is empty")), (False, True))
check("lib: no grant table is left", hasattr(eng, "UNATTENDED_BRIEFERS"), False)

# --- caused through the header door, which is the hand-written form ---
hand_id = "20260101-000000-bbbbcccc"
hand = JOBS / hand_id
hand.mkdir(parents=True, exist_ok=True)
try:
    (hand / "brief.md").write_text(
        "---\nproject: Loam\ntitle: Scope the engineer surface\n"
        "briefed_by: otto\ninvolvement: go\nmax_minutes: 5\n---\n\nDo a thing.\n",
        encoding="utf-8")
    out = launcher(["Loam", hand_id], agent=BRIEFER)
    check("positional, header says another persona: refused",
          f"briefed by {BRIEFER} and by nobody else" in out.stdout, True)
    check("positional, header says another persona: exit 3", out.returncode, 3)
    check("positional: the environment cannot override the header",
          "otto" in out.stdout, True)
finally:
    shutil.rmtree(hand, ignore_errors=True)

# The receipt's deploy-brief row, which is how a Loam job briefed by another
# persona reads as the lane rather than as the rule not being enforced.
launch = load_launcher()
check("receipt row: a deploy brief names the project it deploys",
      launch.deploy_row(platform, {"title": f"Deploy {other_name} at 90496b3"})
      if other_name else "SKIP",
      f"`{other_name}` — the deploy lane, which any persona may send to Loam "
      f"(lib/loam_engineer.brief_refusal)" if other_name else "SKIP")
check("receipt row: every other Loam job",
      launch.deploy_row(platform, {"title": "Scope the engineer surface"}), "—")
check("receipt row: a project engineer's job is never a deploy brief",
      launch.deploy_row(others[0], {"title": f"Deploy {other_name} at 90496b3"})
      if others else "—", "—")

# Only the involvement header selects read-only landing.
check("read-only: the involvement the 10-12 job carried",
      launch.is_read_only("Read-only. Change nothing, commit nothing, open no PR."), True)
check("read-only: any case, any punctuation after it",
      [launch.is_read_only(v) for v in ("read-only: quote it back", "READ-ONLY", " Read-only")],
      [True, True, True])
check("read-only: a dial that only mentions it later is not the flag",
      [launch.is_read_only(v) for v in (
          "The owner, 2026-10-12 in chat: Go and do it; read-only.",
          "Go and do it; the owner reviews after it is running.",
          "Read-onlyish", "", None)],
      [False, False, False, False, False])

print()

jobs_after = sorted(JOBS.iterdir()) if JOBS.is_dir() else []
# This test's own fixtures (20260101-…) come and go, and one a crashed run
# left behind is removed by the next: neither is a refusal claiming a folder.
check("no refusal claimed a job directory",
      [p for p in jobs_after if p not in jobs_before
       and not p.name.startswith("20260101-")], [])

print()
print("FAILURES:", fails)
shutil.rmtree(_cfg_dir, ignore_errors=True)
sys.exit(1 if fails else 0)
