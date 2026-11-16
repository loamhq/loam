#!/usr/bin/env python3
"""Antigravity's per-conversation files for engineer jobs (2026-11-15).

  the clearing     a conversation Loam's job records name is cleared 7 days
                   after the last job naming it finished: its .db, its brain
                   folder, the implicit files attributed to its jobs, and its
                   rows in conversation_summaries.db (the summary, the search
                   index, the parent link). A chain waits for its last job; a
                   job with no verdict keeps it; a conversation no job names
                   (a persona's, the owner's) is never touched. Recorded in
                   each job's antigravity.json, once.
  the attribution  implicit files that appeared during a run are the job's
                   only when no conversation no job names was written then.
  the reviewer     its runs on Antigravity (bin/review, since 2026-11-15) are
                   cleared the same way: a run directory is a job directory,
                   its verdict's time read from its run.json.
  continue_job     of a cleared job: refused with a sentence that says so.
  idle (11-16)     a conversation no job names is cleared once nothing has
                   touched it for 7 days and no agy process is on it, its
                   summary rows with it; one resumed within the week stays.
                   Implicit files no job names go at 7 days old. The thread
                   engine starts a fresh session for a member whose kept
                   conversation was cleared.
  the memory check leaves a job's own conversation out of its counts, so a
                   job's growth posts nothing; since 2026-11-16 a persona's
                   posts nothing either, and a new kind of entry under the
                   tree is what is said.

Run: python3 tests/test_agy_files.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="loam-agy-files-"))
HOME = TMP / "antigravity-cli"
JOBS = TMP / "jobs"
REVIEWS = TMP / "reviewer-runs"     # bin/review's run directories
os.environ["LOAM_AGY_CLI_HOME"] = str(HOME)
os.environ["LOAM_AGY_JOB_ROOTS"] = os.pathsep.join((str(JOBS), str(REVIEWS)))
os.environ["LOAM_PROVIDER_MEMORY_BASELINE"] = str(TMP / "baseline.json")

sys.path.insert(0, str(AGENTS / "lib"))
import loam_agy_files as af  # noqa: E402
import loam_agy_job as aj  # noqa: E402
import loam_engineer as eng  # noqa: E402
import loam_provider_memory as pm  # noqa: E402

fails = 0
DAY = 86400
NOW = time.time()


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:64} {got!r}"[:240]
          + ("" if ok else f"   want {want!r}"))


def load(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def conversation(cid: str) -> None:
    """A conversation's files as agy 1.5.9 leaves them."""
    (HOME / "conversations" / f"{cid}.db").write_text("x")
    (HOME / "brain" / cid).mkdir(parents=True)
    (HOME / "brain" / cid / "notes.md").write_text("x")
    con = sqlite3.connect(HOME / "conversation_summaries.db")
    con.execute("INSERT INTO conversation_summaries (conversation_id, title) VALUES (?, ?)",
                (cid, cid))
    con.execute("INSERT INTO conversations_fts (conversation_id, title, user_body) "
                "VALUES (?, ?, ?)", (cid, cid, "body"))
    con.execute("INSERT INTO conversation_parents VALUES (?, ?)", (cid, "root-" + cid))
    con.commit()
    con.close()


def a_job(job_id: str, conv: str | None, finished: float | None,
          implicit: list[str] = (), runtime: str = "antigravity") -> Path:
    d = JOBS / job_id
    d.mkdir(parents=True)
    (d / "runtime").write_text(runtime + "\n")
    if conv:
        (d / "session-id").write_text(conv + "\n")
    if finished is not None:
        (d / "result.json").write_text(json.dumps({"finished": af.iso(finished)}))
    if implicit:
        af.write_record(d, {"conversations": [conv], "implicit": list(implicit)})
    return d


def rows(table: str, column: str = "conversation_id") -> set[str]:
    con = sqlite3.connect(HOME / "conversation_summaries.db")
    try:
        return {r[0] for r in con.execute(f"SELECT {column} FROM {table}")}
    finally:
        con.close()


job = load("engineer_job_under_test_agy_files", AGENTS / "bin" / "engineer-job")

try:
    for sub in ("conversations", "brain", "implicit"):
        (HOME / sub).mkdir(parents=True)
    con = sqlite3.connect(HOME / "conversation_summaries.db")
    con.executescript(
        "CREATE TABLE conversation_summaries (conversation_id text PRIMARY KEY, "
        "title text NOT NULL DEFAULT '');"
        "CREATE VIRTUAL TABLE conversations_fts USING fts5(conversation_id UNINDEXED, "
        "title, user_body);"
        "CREATE TABLE conversation_parents (conversation_id TEXT PRIMARY KEY, "
        "parent_id TEXT NOT NULL) WITHOUT ROWID;")
    con.close()

    for cid in ("OLD", "NEW", "CHAIN", "RUNNING", "PERSONA"):
        conversation(cid)
    for name in ("old-1.pb", "old-2.pb", "persona.pb"):
        (HOME / "implicit" / name).write_text("x")

    a_job("20261101-000000-00000001", "OLD", NOW - 8 * DAY, ["old-1.pb", "old-2.pb"])
    a_job("20261112-000000-00000002", "NEW", NOW - 3 * DAY)
    a_job("20261101-000000-00000003", "CHAIN", NOW - 10 * DAY)
    a_job("20261113-000000-00000004", "CHAIN", NOW - 2 * DAY)   # its continuation
    a_job("20261101-000000-00000005", "RUNNING", None)          # no verdict
    a_job("20261101-000000-00000006", "CLAUDE-SESSION", NOW - 30 * DAY,
          runtime="claude")

    check("jobs: only Antigravity jobs", sorted(r["dir"].name for r in af.jobs()),
          ["20261101-000000-00000001", "20261101-000000-00000003",
           "20261101-000000-00000005", "20261112-000000-00000002",
           "20261113-000000-00000004"])
    check("owned: every conversation a job names, finished or not",
          af.owned()[0], {"OLD", "NEW", "CHAIN", "RUNNING"})
    check("due: only the one whose every job is over a week old",
          sorted(af.due(af.jobs(), NOW)), ["OLD"])
    check("due: the chain once its last job is a week old",
          sorted(af.due(af.jobs(), NOW + 6 * DAY)), ["CHAIN", "NEW", "OLD"])

    dry = af.clear(NOW, dry=True)
    check("dry run: says what would go, removes nothing",
          ([r["conversation"] for r in dry],
           (HOME / "conversations" / "OLD.db").exists()), (["OLD"], True))

    done = af.clear(NOW)
    check("clear: one conversation, no error",
          [(r["conversation"], r["error"]) for r in done], [("OLD", None)])
    check("clear: its .db, brain and attributed implicit files gone",
          sorted(done[0]["removed"]),
          ["brain/OLD", "conversations/OLD.db", "implicit/old-1.pb", "implicit/old-2.pb"])
    check("clear: its summary, search and parent rows gone",
          ("OLD" in rows("conversation_summaries"), "OLD" in rows("conversations_fts"),
           "OLD" in rows("conversation_parents"), done[0]["rows"]),
          (False, False, False, 3))
    check("clear: every other conversation untouched",
          sorted(p.stem for p in (HOME / "conversations").iterdir()),
          ["CHAIN", "NEW", "PERSONA", "RUNNING"])
    check("clear: other rows untouched",
          rows("conversation_summaries"), {"NEW", "CHAIN", "RUNNING", "PERSONA"})
    check("clear: a persona's implicit file untouched",
          sorted(p.name for p in (HOME / "implicit").iterdir()), ["persona.pb"])
    rec = af.read_record(JOBS / "20261101-000000-00000001")
    check("clear: recorded in the job's antigravity.json",
          rec["cleared"]["conversations"], ["OLD"])
    check("clear: never twice", af.clear(NOW), [])

    # A chain: both its jobs record it.
    done = af.clear(NOW + 6 * DAY)
    check("a chain cleared once its last job is a week old",
          sorted(r["conversation"] for r in done), ["CHAIN", "NEW"])
    check("...recorded by both its jobs",
          [af.read_record(JOBS / j)["cleared"]["conversations"]
           for j in ("20261101-000000-00000003", "20261113-000000-00000004")],
          [["CHAIN"], ["CHAIN"]])
    check("the running job's and the persona's stay",
          sorted(p.stem for p in (HOME / "conversations").iterdir()),
          ["PERSONA", "RUNNING"])

    # -- the reviewer's runs ---------------------------------------------------
    check("the reviewer's runs are a root the clearing reads, live",
          (lambda: (os.environ.pop("LOAM_AGY_JOB_ROOTS"),
                    af.lc.REVIEWER_STATE / "runs" in af.job_roots(),
                    os.environ.__setitem__("LOAM_AGY_JOB_ROOTS", os.pathsep.join(
                        (str(JOBS), str(REVIEWS)))))[1])(), True)
    conversation("REVIEW")
    run = REVIEWS / "20261105-042000"
    run.mkdir(parents=True)
    (run / "runtime").write_text("antigravity\n")
    (run / "run.json").write_text(json.dumps({"run": run.name,
                                              "finished": af.iso(NOW - 8 * DAY)}))
    af.write_record(run, {"conversations": ["REVIEW"], "implicit": []})
    fresh = REVIEWS / "20261114-042000"
    fresh.mkdir(parents=True)
    (fresh / "runtime").write_text("antigravity\n")
    (fresh / "run.json").write_text(json.dumps({"finished": af.iso(NOW - 1 * DAY)}))
    af.write_record(fresh, {"conversations": ["REVIEW2"]})
    conversation("REVIEW2")
    claude_run = REVIEWS / "20261101-042000"           # a Claude Code run: no runtime file
    claude_run.mkdir(parents=True)
    (claude_run / "run.json").write_text(json.dumps({"finished": af.iso(NOW - 30 * DAY)}))
    check("a reviewer run on Antigravity is read as a job, its run.json the verdict",
          sorted((r["dir"].name, r["conversations"], r["finished"] is not None)
                 for r in af.jobs() if r["dir"].parent == REVIEWS),
          [("20261105-042000", ["REVIEW"], True), ("20261114-042000", ["REVIEW2"], True)])
    done = af.clear(NOW)
    check("a reviewer run's conversation is cleared a week after the run, a fresh one kept",
          [(r["conversation"], r["error"]) for r in done], [("REVIEW", None)])
    check("...its .db and brain gone, and recorded in its antigravity.json",
          ((HOME / "conversations" / "REVIEW.db").exists(),
           (HOME / "brain" / "REVIEW").exists(),
           (HOME / "conversations" / "REVIEW2.db").exists(),
           af.read_record(run)["cleared"]["conversations"]),
          (False, False, True, ["REVIEW"]))
    shutil.rmtree(REVIEWS)
    for cid in ("REVIEW2",):
        (HOME / "conversations" / f"{cid}.db").unlink()
        shutil.rmtree(HOME / "brain" / cid)
        con = sqlite3.connect(HOME / "conversation_summaries.db")
        for table, column in af.SUMMARY_TABLES:
            con.execute(f'DELETE FROM "{table}" WHERE "{column}" = ?', (cid,))
        con.commit()
        con.close()

    # -- continue_job of a cleared job ---------------------------------------
    proj = eng.Project("p", TMP / "canon", JOBS, platform=False,
                       record={"runtime": "antigravity"})
    old = aj.CONVERSATIONS
    aj.CONVERSATIONS = HOME / "conversations"
    try:
        sid, refusal = job.resolve_continuation(proj, "20261101-000000-00000001")
        check("continue_job of a cleared job: refused, and said why",
              (sid, "was cleared on" in (refusal or ""),
               "kept a week" in (refusal or ""), "without `continue_job`" in (refusal or "")),
              (None, True, True, True))
    finally:
        aj.CONVERSATIONS = old

    # -- attribution ---------------------------------------------------------
    for db in (HOME / "conversations").iterdir():      # written a while ago
        os.utime(db, (NOW - 3600, NOW - 3600))
    before = af.snapshot()
    time.sleep(1.2)
    (HOME / "implicit" / "job.pb").write_text("x")
    (HOME / "conversations" / "J.db").write_text("x")
    rec = af.attribute(before, ["J"])
    check("attribute: a new implicit file, nothing else written: the job's",
          (rec["conversations"], rec["implicit"], "implicit_note" in rec),
          (["J"], ["job.pb"], False))
    before = af.snapshot()
    time.sleep(1.2)
    (HOME / "implicit" / "maybe.pb").write_text("x")
    (HOME / "conversations" / "J2.db").write_text("x")
    os.utime(HOME / "conversations" / "PERSONA.db")      # a persona wrote meanwhile
    rec = af.attribute(before, ["J2"])
    check("attribute: a persona's conversation written too: none attributed",
          (rec["implicit"], "PERSONA" in rec.get("implicit_note", "")), ([], True))

    # -- the memory check ----------------------------------------------------
    pm.BASELINE = TMP / "baseline.json"
    counts, grew = pm.check()
    base = dict(counts)
    check("memory check: the running job's conversation is not counted",
          (counts["antigravity brain entries"],
           counts["antigravity conversation summaries"]), (1, 1))   # PERSONA only
    a_job("20261115-000000-00000007", "JOB2", None)
    conversation("JOB2")
    counts, grew = pm.check()
    check("memory check: a new engineer job's conversation posts nothing",
          ([g for g in grew if g.startswith("antigravity")],
           counts["antigravity brain entries"]), ([], 1))
    conversation("PERSONA2")
    (HOME / "implicit" / "persona2.pb").write_text("x")
    counts, grew = pm.check()
    check("memory check: a persona's new conversation posts nothing either "
          "(2026-11-16, working state)", [g for g in grew if g.startswith("antigravity")], [])
    seen = json.loads(pm.BASELINE.read_text())
    check("...and its baseline is kept current",
          seen["antigravity brain entries"], counts["antigravity brain entries"])
    (HOME / "knowledge").mkdir()
    counts, grew = pm.check()
    check("memory check: a store Antigravity adds under its tree is said",
          [g.split(":")[0] for g in grew if g.startswith("antigravity")],
          ["antigravity other entries"])
    counts, grew = pm.check()
    check("...once", [g for g in grew if g.startswith("antigravity")], [])
    del base

    # -- every other conversation: cleared once idle a week (2026-11-16) -----
    def age(path: Path, days: float) -> None:
        when = NOW - days * DAY
        for p in [path, *path.rglob("*")] if path.is_dir() else [path]:
            os.utime(p, (when, when))

    for cid in ("IDLE", "RESUMED", "BUSY"):
        conversation(cid)
        age(HOME / "conversations" / f"{cid}.db", 20)
        age(HOME / "brain" / cid, 20)
    # Begun three weeks ago, resumed two days ago: the turn wrote its .db.
    age(HOME / "conversations" / "RESUMED.db", 2)
    # A summary row whose conversation's files are already gone.
    con = sqlite3.connect(HOME / "conversation_summaries.db")
    con.execute("INSERT INTO conversation_summaries (conversation_id, title) "
                "VALUES ('ORPHAN', 'x')")
    con.commit()
    con.close()
    for name, days in (("stale.pb", 9), ("young.pb", 3)):
        (HOME / "implicit" / name).write_text("x")
        age(HOME / "implicit" / name, days)
    a_job("20261101-000000-00000008", "JOBIDLE", NOW - 2 * DAY, ["jobs-own.pb"])
    (HOME / "implicit" / "jobs-own.pb").write_text("x")
    age(HOME / "implicit" / "jobs-own.pb", 20)
    conversation("JOBIDLE")
    age(HOME / "conversations" / "JOBIDLE.db", 20)
    age(HOME / "brain" / "JOBIDLE", 20)

    real_running = af.running
    af.running = lambda: {"BUSY"}           # an agy process is on it
    try:
        due_idle = af.idle(af.jobs(), NOW)
        check("idle: a week untouched, no job names it, nothing running on it",
              sorted(due_idle), ["IDLE", "ORPHAN"])
        dry = af.clear(NOW, dry=True)
        check("idle: the dry run says so and removes nothing",
              ([r["conversation"] for r in dry],
               (HOME / "conversations" / "IDLE.db").exists()),
              (["IDLE", "ORPHAN", None], True))
        done = af.clear(NOW)
        by = {r["conversation"]: r for r in done}
        check("idle: the idle conversation's .db and brain go, and its rows",
              (sorted(by["IDLE"]["removed"]), by["IDLE"]["rows"], by["IDLE"]["error"],
               "IDLE" in rows("conversation_summaries"), "IDLE" in rows("conversations_fts"),
               "IDLE" in rows("conversation_parents")),
              (["brain/IDLE", "conversations/IDLE.db"], 3, None, False, False, False))
        check("idle: a summary row with no files left goes too",
              ("ORPHAN" in rows("conversation_summaries"), by["ORPHAN"]["rows"]), (False, 1))
        check("idle: resumed within the week, running now, or a job's: all stay",
              sorted(p.stem for p in (HOME / "conversations").iterdir()
                     if p.stem in ("RESUMED", "BUSY", "JOBIDLE")),
              ["BUSY", "JOBIDLE", "RESUMED"])
        check("idle: a week-old implicit file no job names goes; a young one "
              "and a job's stay",
              (by[None]["removed"], sorted(p.name for p in (HOME / "implicit").iterdir()
                                           if p.name in ("young.pb", "jobs-own.pb"))),
              (["implicit/stale.pb"], ["jobs-own.pb", "young.pb"]))
        check("idle: a second pass clears nothing", af.clear(NOW), [])
        af.running = lambda: set()
        done = af.clear(NOW + 4 * DAY)
        check("idle: four days on, only the one whose process ended goes",
              [r["conversation"] for r in done if r["conversation"]], ["BUSY"])
        done = af.clear(NOW + 5.1 * DAY)
        check("idle: the resumed one goes a week after its last turn; the "
              "job's a week after its job",
              sorted(r["conversation"] for r in done if r["conversation"]),
              ["JOBIDLE", "RESUMED"])
    finally:
        af.running = real_running
    check("running: reads --conversation off the process table",
          isinstance(af.running(), set), True)

    # -- the thread engine starts a fresh session for a cleared one ----------
    import loam_thread_engine as te  # noqa: E402
    import loam_threads as threads_mod  # noqa: E402

    class Live:
        thread_id = "LIVE-CONV"

    class Broker:
        sessions = {"S-live": Live()}

    conversation("LIVE-CONV")
    engine = te.ThreadEngine.__new__(te.ThreadEngine)
    engine.broker = Broker()
    turn = type("T", (), {"thread_id": "t-1", "persona": "probe1"})()
    saved_for, saved_dir = threads_mod.session_for, te.cfg.SESSION_DIR
    te.cfg.SESSION_DIR = TMP / "broker-sessions"
    te.cfg.SESSION_DIR.mkdir()
    (te.cfg.SESSION_DIR / "S-disk.json").write_text(json.dumps(
        {"provider": "antigravity", "thread_id": "IDLE"}))
    try:
        threads_mod.session_for = lambda t, p: {"session_id": "S-live",
                                                "engine": "antigravity"}
        check("engine: a kept session whose conversation is there resumes",
              engine.agy_conversation_gone(turn, "antigravity"), None)
        threads_mod.session_for = lambda t, p: {"session_id": "S-disk",
                                                "engine": "antigravity"}
        check("engine: one whose conversation was cleared starts fresh",
              engine.agy_conversation_gone(turn, "antigravity"), "IDLE")
        check("engine: another provider is never asked",
              engine.agy_conversation_gone(turn, "codex"), None)
    finally:
        threads_mod.session_for, te.cfg.SESSION_DIR = saved_for, saved_dir
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{'OK' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
