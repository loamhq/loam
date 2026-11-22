#!/usr/bin/env python3
"""Deliver completed jobs to the calling run when it has no thread. Recover pending
deliveries without starting duplicate runs.

Run: python3 tests/test_results.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="loam-results-"))
os.environ["LOAM_ATTENTION_DIR"] = str(TMP / "attention")
os.environ["LOAM_WAITERS_DIR"] = str(TMP / "waiters")
os.environ["LOAM_LOCK_DIR"] = str(TMP / "locks")
os.environ["LOAM_SCHEDULE_STATE"] = str(TMP / "schedules")
os.environ.pop("LOAM_RESULTS_KICK", None)
sys.path.insert(0, str(AGENTS / "lib"))
import loam_lock  # noqa: E402
import loam_results as lr  # noqa: E402
import loam_session  # noqa: E402
import loam_waiters as w  # noqa: E402

w.LOG = TMP / "deliveries.jsonl"
loam_session.SESSIONS_DIR = TMP / "claude" / "projects"
AGENT = "resultstest"
results: list[bool] = []


def check(label: str, ok: bool, detail="") -> None:
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok and detail != "":
        print(f"      {detail!r}"[:1500])


def load_wake():
    loader = importlib.machinery.SourceFileLoader("wake_results", str(AGENTS / "bin" / "wake"))
    spec = importlib.util.spec_from_loader("wake_results", loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CALLS = TMP / "calls.jsonl"
FAKE_CLI = f"""#!/usr/bin/env python3
import json, sys
argv = sys.argv[1:]
with open({str(CALLS)!r}, "a") as fh:
    fh.write(json.dumps(argv) + "\\n")
sid = None
for flag in ("--resume", "--session-id"):
    if flag in argv:
        sid = argv[argv.index(flag) + 1]
if "--resume" in argv and sid.startswith("refused"):
    print("No conversation found with session ID: " + sid)
    sys.exit(1)
print(json.dumps({{"subtype": "success", "result": "", "num_turns": 1,
                  "session_id": sid}}))
"""

ws = TMP / "ai-labs" / AGENT
(ws / "crons").mkdir(parents=True)
vault = TMP / "vault"
(vault / "00_INBOX" / "from-agents").mkdir(parents=True)
(ws / "agent.json").write_text(json.dumps({"name": AGENT, "workspace": str(ws),
                                           "vault": str(vault)}))
(ws / "map.md").write_text("Reflect.\n")
(ws / "crons" / "watch.md").write_text("---\nschedule: daily at 09:00\n---\nWATCH-THE-BOX\n")
cli = TMP / "claude-cli"
cli.write_text(FAKE_CLI)
cli.chmod(0o755)

wake = load_wake()
wake.CLAUDE = str(cli)
wake.LOG_DIR = TMP / "logs"
WAKE_LOG = wake.LOG_DIR / f"wake-{AGENT}.jsonl"


def job(job_id: str, finished: str, caller: dict, brief: str = "Do the thing.") -> Path:
    d = TMP / "jobs" / job_id
    d.mkdir(parents=True)
    (d / "brief.md").write_text(f"---\ntitle: Job {job_id[-4:]}\n---\n\n{brief}\n")
    w.register(job_id, "Loam", d, AGENT, caller)
    (d / "result.json").write_text(json.dumps({
        "ok": True, "stage": "done", "outcome": "completed", "finished": finished,
        "landing_state": "landed", "landing": "landed at abc1234",
        "vault_receipt": f"/v/{job_id}.md"}))
    return d


def session_file(sid: str) -> None:
    d = loam_session.SESSIONS_DIR / lr.project_slug(ws)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sid}.jsonl").write_text("{}\n")


def calls() -> list[list[str]]:
    if not CALLS.exists():
        return []
    return [json.loads(x) for x in CALLS.read_text().splitlines()]


def rows() -> list[dict]:
    if not WAKE_LOG.exists():
        return []
    return [json.loads(x) for x in WAKE_LOG.read_text().splitlines()]


def deliveries() -> list[dict]:
    if not w.LOG.exists():
        return []
    return [json.loads(x) for x in w.LOG.read_text().splitlines()]


def prompt(argv: list[str]) -> str:
    return argv[argv.index("-p") + 1]


def appended(argv: list[str]) -> str | None:
    if "--append-system-prompt-file" not in argv:
        return None
    return argv[argv.index("--append-system-prompt-file") + 1]


try:
    # -- 1 and 2: resumed, in order, once each --------------------------------
    cron = {"caller": "cron", "caller_lane": "cron:watch", "caller_session": "sess-cron"}
    session_file("sess-cron")
    job("20261119-100000-0000bbbb", "2026-11-19T18:10:00Z", cron)    # finished second
    job("20261119-100000-0000aaaa", "2026-11-19T18:05:00Z", cron)    # finished first
    seen_lanes: list[str] = []
    real_claude_argv = wake.claude_argv

    def spy_argv(*a, **k):
        # The lane file is a temp file removed after the run: read it now.
        argv = real_claude_argv(*a, **k)
        lf = appended(argv)
        seen_lanes.append(Path(lf).read_text() if lf else "")
        return argv
    wake.claude_argv = spy_argv
    rc = wake.results_main(ws)
    got = calls()
    check("two results, two runs, exit 0", rc == 0 and len(got) == 2, (rc, got))
    check("...oldest verdict first", [("0000aaaa" in prompt(a)) for a in got] == [True, False],
          [prompt(a)[:90] for a in got])
    check("...each resumes the asking session, and names no new one",
          all(a[a.index("--resume") + 1] == "sess-cron" and "--session-id" not in a
              for a in got), got)
    check("...with the cron's own file loaded again",
          all("WATCH-THE-BOX" in t for t in seen_lanes), seen_lanes)
    first = prompt(got[0])
    check("...its input is the result: job, receipt, verdict, landing, and silence",
          all(s in first for s in ("20261119-100000-0000aaaa", "/v/20261119-100000-0000aaaa.md",
                                   "completed: landed at abc1234", "Landing: landed",
                                   "resumed your session", "nothing has been posted")), first)
    r = rows()
    check("...each its own row in the wake log, lane result:<job>, resumed",
          [(x["lane"], x["ok"], x["resumed"], x["result_of"], x["asked_by_lane"]) for x in r]
          == [("result:20261119-100000-0000aaaa", True, True, "20261119-100000-0000aaaa", "cron:watch"),
              ("result:20261119-100000-0000bbbb", True, True, "20261119-100000-0000bbbb", "cron:watch")],
          r)
    fires = [json.loads(x) for x in (TMP / "schedules" / "events.jsonl").read_text().splitlines()]
    check("...and a fire each in the schedule log, under the cron that asked",
          [(f["record"], f["lane"], f["outcome"], f["resumed"]) for f in fires]
          == [("watch", "result:20261119-100000-0000aaaa", "ran", True),
              ("watch", "result:20261119-100000-0000bbbb", "ran", True)], fires)
    check("...the records gone, each delivered once",
          (w.records(), [(d["job"], d["outcome"]) for d in deliveries()])
          == ([], [("20261119-100000-0000aaaa", "resumed"), ("20261119-100000-0000bbbb", "resumed")]),
          (w.records(), deliveries()))
    check("...and the cron's own schedule is untouched (no done mark, no change)",
          "done:" not in (ws / "crons" / "watch.md").read_text(), "")
    wake.claude_argv = real_claude_argv
    seen_lanes.clear()

    # -- 3: the fallback ------------------------------------------------------
    CALLS.unlink()
    wake.claude_argv = spy_argv
    job("20261119-110000-0000cccc", "2026-11-19T19:00:00Z",
        {"caller": "wake", "caller_lane": "reflection", "caller_session": "sess-gone"},
        brief="Find out why the BOX-IS-SLOW and say.")
    job("20261119-110000-0000dddd", "2026-11-19T19:05:00Z",
        {"caller": "cron", "caller_lane": "cron:watch", "caller_session": "refused-1"})
    session_file("refused-1")
    rc = wake.results_main(ws)
    got = calls()
    check("a session whose file is gone: one fresh run with a new session id",
          len(got) == 3 and "--resume" not in got[0] and "--session-id" in got[0], got)
    fresh = prompt(got[0])
    check("...carrying the result, the asker's lane, its brief word for word, and why",
          all(s in fresh for s in ("20261119-110000-0000cccc", "your reflection wake",
                                   "BOX-IS-SLOW", "its session file is gone",
                                   "/v/20261119-110000-0000cccc.md", "fresh run")), fresh)
    check("...the fresh reflection result uses the shared imports", seen_lanes[0] == "", seen_lanes)
    check("a session the CLI refuses: tried, then a fresh run in the same pass",
          "--resume" in got[1] and "--session-id" in got[2]
          and "could not find the session" in prompt(got[2])
          and "crons/watch.md" in prompt(got[2]), [prompt(a)[:200] for a in got[1:]])
    r = rows()[-2:]
    check("...both rows say not resumed, and why",
          [(x["resumed"], bool(x.get("not_resumed"))) for x in r] == [(False, True), (False, True)], r)
    check("...delivered once each, as fresh",
          [(d["job"], d["outcome"]) for d in deliveries()[-2:]]
          == [("20261119-110000-0000cccc", "fresh"), ("20261119-110000-0000dddd", "fresh")],
          deliveries())
    wake.claude_argv = real_claude_argv

    # -- 4: given, then dead --------------------------------------------------
    CALLS.unlink()
    d = job("20261119-120000-0000eeee", "2026-11-19T20:00:00Z", cron)
    rec = json.loads(w._path("20261119-120000-0000eeee").read_text())
    w.write({**rec, "given": "2026-11-19T20:01:00Z"})
    rc = wake.results_main(ws)
    failures = (vault / "00_INBOX" / "from-agents").glob("*.md")
    said = "".join(p.read_text() for p in failures)
    check("a record already given: not handed over again",
          (calls(), w.records()) == ([], []), (calls(), w.records()))
    check("...logged, and said in the failure log",
          deliveries()[-1]["outcome"].startswith("given")
          and "20261119-120000-0000eeee" in said, (deliveries()[-1], said[-400:]))

    # -- 5: the lock ----------------------------------------------------------
    job("20261119-130000-0000ffff", "2026-11-19T21:00:00Z", cron)
    loam_lock.WAIT_SECONDS = 0.5
    with loam_lock.hold(AGENT, "reflection", wait_seconds=1):
        rc = wake.results_main(ws)
    rec = json.loads(w._path("20261119-130000-0000ffff").read_text())
    check("while another run holds the lock: nothing given, the record stays",
          (calls(), "given" in rec, rows()[-1]["stage"]) == ([], False, "lock_timeout"),
          (calls(), rec, rows()[-1]))
    w.settle("20261119-130000-0000ffff")

    # -- 6: kicks -------------------------------------------------------------
    check("kicks are off under a scratch records directory", lr.kicks_enabled() is False)
    started = []
    real_kick = lr.kick
    lr.kick = lambda agent: started.append(agent) or "started"
    job("20261119-140000-00001111", "2026-11-19T22:00:00Z", cron)
    job("20261119-140000-00002222", "2026-11-19T22:00:00Z",
        {"caller": "conversation", "caller_session": "s", "thread": "t-x"})
    check("a run's record is in a folder of its own, out of an older broker's sight",
          (w._path("20261119-140000-00001111").parent == w.RUNS,
           w._path("20261119-140000-00002222").parent == w.STATE,
           sorted(x.name for x in w.STATE.glob("*.json")))
          == (True, True, ["20261119-140000-00002222.json"]))
    check("kick_for: a run's record starts its persona's drain; a thread's does not",
          (lr.kick_for("20261119-140000-00001111"), lr.kick_for("20261119-140000-00002222"),
           started) == ("started", None, [AGENT]), started)
    import loam_attention
    from unittest.mock import patch
    with patch.object(loam_attention, "persona", return_value="fixture-attention"):
        check("kick_all: one drain for the result and one for the previously refused run",
              lr.kick_all() == {AGENT: "started", "fixture-attention": "started"})
    rec = json.loads(w._path("20261119-140000-00002222").read_text())
    lr.to_headless(rec, "thread gone")
    moved = json.loads(w._path("20261119-140000-00002222").read_text())
    check("a thread result whose thread is gone goes by a run instead",
          (w.route(moved), moved.get("headless_why"), started[-1],
           w._path("20261119-140000-00002222").parent) == ("run", "thread gone", AGENT, w.RUNS),
          moved)
    lr.kick = real_kick
    fresh = lr.fresh_text({**moved, "result": json.loads(
        (TMP / "jobs" / "20261119-140000-00002222" / "result.json").read_text())},
        "it was a conversation's")
    check("...and its fresh run says which thread could not take it",
          "t-x" in fresh and "thread gone" in fresh, fresh)
    session, why = lr.resumable(moved, ws, "claude-code")
    check("...never resuming a session the session host may hold", session is None, why)
    check("a Codex persona's result is never resumed (yet)",
          lr.resumable({**rec, "caller": "cron"}, ws, "codex")[0] is None)
    argv = wake.claude_argv(AGENT, "reflection", "p", None, session_id="abc")
    check("every Claude wake names its own session id",
          argv[argv.index("--session-id") + 1] == "abc" and "--resume" not in argv, argv)
    check("--results parses; a cron cannot be called that",
          (wake.parse_argv([str(ws), "--results"])[1] == wake.RESULTS), "")
    try:
        wake.parse_argv([str(ws), "--cron", wake.RESULTS])
        check("...refused", False)
    except ValueError:
        check("...refused", True)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
