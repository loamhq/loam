#!/usr/bin/env python3
"""Check job, session and caller identity in Activity and result delivery.

Run: python3 tests/test_activity_who.py"""

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp())
os.environ["LOAM_VAULT"] = str(TMP / "vault")
os.environ["LOAM_AI_LABS"] = str(TMP / "ai-labs")
os.environ["LOAM_THREADS_STATE"] = str(TMP / "state")
os.environ["LOAM_WAITERS_DIR"] = str(TMP / "waiters")
for who in ("ada", "iris"):
    (TMP / "ai-labs" / who).mkdir(parents=True)
    (TMP / "ai-labs" / who / "agent.json").write_text(json.dumps({"name": who.capitalize()}))
(TMP / "vault" / "15_CONVERSATIONS").mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents").mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents" / "comms.md").write_text("# From agents\n")

sys.path.insert(0, str(AGENTS / "lib"))
import loam_broker as cfg  # noqa: E402
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}
import loam_engineer as eng  # noqa: E402
import loam_live  # noqa: E402
import loam_threads as threads  # noqa: E402
import loam_waiters as w  # noqa: E402

w.LOG = TMP / "deliveries.jsonl"
cfg.roster = threads.roster
cfg.roster_cards = lambda: [{"id": p} for p in threads.roster()]


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


job = load("engineer_job_who", AGENTS / "bin" / "engineer-job")
broker_mod = load("session_broker_who", AGENTS / "bin" / "session-broker")
engine_mod = broker_mod.loam_thread_engine

results = []


def check(label, got, want):
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:70} got={got!r}")
    if not ok:
        print(f"      wanted {want!r}")


# -- 1. lib/loam_waiters.py ---------------------------------------------------

check("lanes as run kinds",
      [w.lane_kind(x) for x in ("", "conversation", "reflection", "cron:infra-watch",
                                "review", "review:20261112", "engineer:2026", "job:2026")],
      ["conversation", "conversation", "wake", "cron", "reviewer", "reviewer", "job", "job"])


def fake_proc(chain):
    """chain: [(pid, ppid, environ dict, argv list)], the caller first."""
    root = TMP / f"proc-{len(list(TMP.glob('proc-*')))}"
    for pid, ppid, env, argv in chain:
        d = root / str(pid)
        d.mkdir(parents=True)
        (d / "stat").write_text(f"{pid} (x y) S {ppid} 1 1 0 -1 0 0 0 0 0 0 0 0 0 0 20 0 1 0 "
                                f"{1000 + pid} 0 0\n")
        (d / "environ").write_bytes(b"\0".join(f"{k}={v}".encode() for k, v in env.items()))
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv))
    return root


proc = fake_proc([(30, 20, {}, ["python3", "engineer-job", "--brief"]),
                  (20, 10, {"LOAM_AGENT": "ada"}, ["python", "engineer_server.py"]),
                  (10, 1, {w.SESSION_ENV: "sess-codex"}, ["codex", "app-server"])])
check("the calling session from an ancestor's environment (Codex filters the MCP env)",
      w.calling_session({}, 30, proc), "sess-codex")
check("...and from our own environment first",
      w.calling_session({w.SESSION_ENV: "mine"}, 30, proc), "mine")
proc2 = fake_proc([(31, 21, {}, ["python3", "engineer-job"]),
                   (21, 11, {}, ["python", "engineer_server.py"]),
                   (11, 1, {}, ["/home/x/.local/bin/claude", "-p", "--resume", "sess-old"])])
check("...and from an older Claude CLI's --resume when no environment says",
      w.calling_session({}, 31, proc2), "sess-old")
proc3 = fake_proc([(32, 1, {}, ["sh"])])
check("...and None when nothing says", w.calling_session({}, 32, proc3), None)

state = TMP / "state"
(state / "steps" / "sessions").mkdir(parents=True)
(state / "steps" / "sessions" / "sess-codex").write_text("t-marked\n")
(state / "sessions.json").write_text(json.dumps({"t-kept": {"ada": {"session_id": "sess-old"}}}))
check("the thread from the session's marker", w.thread_of("sess-codex", "ada", state), "t-marked")
check("...or from sessions.json", w.thread_of("sess-old", "ada", state), "t-kept")
check("...for that persona only", w.thread_of("sess-old", "iris", state), None)
check("caller() in a conversation: kind, session, thread",
      w.caller({"LOAM_AGENT": "ada", w.SESSION_ENV: "sess-codex"}, 30, proc, state),
      {"caller": "conversation", "caller_session": "sess-codex", "thread": "t-marked"})
check("caller() in a cron: the kind, its lane and its own session, no thread",
      w.caller({"LOAM_WAKE": "cron:infra-watch", w.SESSION_ENV: "x"}, 30, proc, state),
      {"caller": "cron", "caller_lane": "cron:infra-watch", "caller_session": "x"})

jobdir = TMP / "jobs" / "20261112-120000-aaaaaaaa"
jobdir.mkdir(parents=True)
w.register("20261112-120000-aaaaaaaa", "Loam", jobdir, "ada",
           {"caller": "conversation", "thread": "t-x"})
check("a brief's record is written at accept, with no waiter in it",
      ([r["job"] for r in w.records()], "pid" in w.records()[0]),
      (["20261112-120000-aaaaaaaa"], False))
check("...not owed while its job has no verdict", w.owed(), [])
(jobdir / "result.json").write_text(json.dumps({
    "ok": True, "stage": "done", "outcome": "completed", "landing_state": "landed",
    "vault_receipt": "/v/r.md", "receipt_text": "Did the thing."}))
owed = w.owed()
check("...owed once the job has a verdict",
      [(r["job"], r["thread"], r["persona"]) for r in owed],
      [("20261112-120000-aaaaaaaa", "t-x", "ada")])
note = w.handoff_text(owed[0], "Fix it")
check("the persona's input names the job, the receipt's path, the verdict, the landing",
      all(s in note for s in ("20261112-120000-aaaaaaaa", "/v/r.md",
                              "verdict: completed", "Landing: landed",
                              "Nothing was posted", "end your turn without writing")),
      True)
check("...and the receipt's own path when the vault copy is not named",
      w.receipt_path({"job_dir": "/j/x", "result": {}}), "/j/x/receipt.md")
check("the verdict line: the outcome and the landing's sentence, plain",
      (w.verdict_line({"outcome": "completed", "stage": "done",
                       "landing": "**Landing: landed at `abc1234`** — merged"}),
       w.verdict_line({"stage": "died", "receipt_text": "The job stopped.\nMore."}),
       w.status_words({"landing_state": "nothing", "outcome": "completed"})),
      ("completed: Landing: landed at abc1234 — merged", "died: The job stopped.",
       "nothing to land"))
w.done(owed[0], "delivered")
w.register("20261112-120000-aaaaaaaa", "Loam", jobdir, "ada",
           {"caller": "conversation", "thread": "t-x"})
w.done(owed[0], "delivered")
check("...and once dealt with it is gone and logged",
      (w.records(), json.loads(w.LOG.read_text().splitlines()[-1])["outcome"]),
      ([], "delivered"))
w.register("20261112-120000-bbbbbbbb", "Loam", jobdir, "ada", {})
w.settle("20261112-120000-bbbbbbbb")
check("a settled call leaves nothing", w.records(), [])
w.register("20261112-120000-aaaaaaaa", "Loam", jobdir, "ada",
           {"caller": "cron", "caller_lane": "cron:infra-watch",
            "caller_session": "sess-cron"})
check("a cron's result goes by a run, not by the broker",
      ([r["job"] for r in w.owed()], [r["job"] for r in w.owed(by="run")],
       [r["job"] for r in w.owed(by="run", persona="iris")]),
      ([], ["20261112-120000-aaaaaaaa"], []))
w.settle("20261112-120000-aaaaaaaa")


# -- 2. bin/engineer-job ------------------------------------------------------

claude_argv = ["sudo", "sh", "-c", "… --model x …", "loam-wall", "/c/claude", "-p", "the prompt",
               "--model", "claude-opus-5-5", "--permission-mode", "bypassPermissions"]
codex_argv = ["codex", "exec", "--json", "-m", "gpt-6.1-sol", "-c",
              'model_reasoning_effort="high"', "-c", 'approval_policy="never"']
check("the model off a Claude command line", job.launched_model(claude_argv),
      ("claude-opus-5-5", None))
check("...and a Codex one, with its effort", job.launched_model(codex_argv),
      ("gpt-6.1-sol", "high"))

project = eng.Project("Loam", TMP, TMP / "pjobs", platform=True)
parent, child = "20261112-100000-11111111", "20261112-110000-22222222"
for jid, extra in ((parent, "caller: conversation\ncaller_session: s1\nthread: t-real\n"),
                   (child, f"continue_job: {parent}\nauto_continuation: 1\n"
                           f"caller: conversation\nthread: t-real\n")):
    d = project.jobs / jid
    d.mkdir(parents=True)
    (d / "brief.md").write_text(f"---\nproject: Loam\ntitle: Job {jid[-2:]}\nbriefed_by: ada\n"
                                f"involvement: go\nmax_minutes: none\n{extra}---\n\nDo it.\n")
job.write_engine(job.paths(project, parent), "codex", codex_argv)
label = job.job_label(project, parent)
check("a job's label: run kind, engine, model, effort as launched",
      (label["run_kind"], label["engine"], label["model"], label["effort"]),
      ("job", "codex", "gpt-6.1-sol", "high"))
check("...who called, from which session and thread",
      (label["briefed_by"], label["caller"], label["caller_session"], label["thread"]),
      ("ada", "conversation", "s1", "t-real"))
check("...a first job continues nothing and is its own chain",
      (label["continues"], label["chain"]), (None, parent))
label = job.job_label(project, child)
check("a continuation names the job it continues and the chain's first",
      (label["continues"], label["chain"], label["auto_continuation"]), (parent, parent, 1))
check("...and with no engine.json and no runtime file, no engine is guessed",
      (label["engine"], label["model"]), (None, None))
(project.jobs / child / "runtime").write_text("claude\n")
check("...an older job's runtime file gives the engine, and no model",
      (job.job_label(project, child)["engine"], job.job_label(project, child)["model"]),
      ("claude-code", None))
old = "20261001-100000-33333333"
(project.jobs / old).mkdir()
(project.jobs / old / "brief.md").write_text("---\ntitle: Old\nbriefed_by: iris\nlane: cron:x\n---\n\nx\n")
label = job.job_label(project, old)
check("a brief from before 2026-11-12: caller from its lane, no thread",
      (label["caller"], label["thread"], label["thread_title"]), ("cron", None, None))


# -- 3. the broker ------------------------------------------------------------

class FakeSession:
    def __init__(self, agent, sid, provider="claude-code"):
        self.agent, self.id, self.provider = agent, sid, provider
        self.model, self.title, self._busy = None, None, None
        self.subs, self.prompts = set(), []
        self.idle_timeout_s, self.live, self.proc = None, True, None
        self.last_activity = time.time() - 600

    def busy(self):
        return self._busy

    def save(self):
        pass

    def watch(self, q):
        self.subs.add(q)

    def unwatch(self, q):
        self.subs.discard(q)

    def meta(self):
        return {"session_id": self.id, "agent": self.agent}

    async def send_message(self, text):
        self.prompts.append(text)

        async def go():
            for q in list(self.subs):
                q.put_nowait({"type": "assistant", "parent_tool_use_id": None,
                              "message": {"content": [{"type": "text", "text": "read it"}]}})
                q.put_nowait({"type": "result", "parent_tool_use_id": None})
        asyncio.get_running_loop().create_task(go())
        return 1

    async def stop(self):
        self.live = False


class FakeBroker:
    def __init__(self):
        self.sessions, self.record_subscribers = {}, set()
        self.stopping = False

    def live(self):
        return [s for s in self.sessions.values() if s.live]

    def seats_taken(self):
        return len(self.live())

    def room(self):
        return {"ok": True, "available_mb": 20_000, "reserve_mb": 3_000, "need_mb": 450,
                "starting": 0, "cgroup_room_mb": None}

    async def start_session(self, agent, session_id=None, resume=False, provider=None, **kw):
        if session_id and session_id in self.sessions:
            return self.sessions[session_id]
        s = FakeSession(agent, f"s{len(self.sessions) + 1}", provider or "claude-code")
        self.sessions[s.id] = s
        return s


async def broker_checks():
    server = broker_mod.Server.__new__(broker_mod.Server)
    fake = FakeBroker()
    server.broker = fake
    server.threads = engine_mod.ThreadEngine(fake)
    # Exercise the same thread route as the app.
    class Writer:
        def __init__(self):
            self.chunks = []

        def write(self, d):
            self.chunks.append(d)

        async def drain(self):
            pass

        def json(self):
            t = b"".join(self.chunks).decode()
            return json.loads(t.split("\r\n\r\n", 1)[1])
    wr = Writer()
    await server.route("POST", "/v1/threads", {}, {"persona": "ada", "title": "Garden"}, wr)
    tid = wr.json()["id"]

    # Session rows.
    s = FakeSession("ada", "c-1")
    s._init_model = "claude-opus-5-5"
    s.model = "opus"
    fake.sessions["c-1"] = s
    x = FakeSession("iris", "c-2", provider="codex")
    x._codex = type("T", (), {"model": "gpt-6.1-sol"})()
    fake.sessions["c-2"] = x
    wr = Writer()
    await server.route("GET", "/v1/sessions/live", {}, {}, wr)
    rows = {r["id"]: r for r in wr.json()["sessions"]}
    check("a session row: run kind, engine, its label, the model it runs and the ask",
          {k: rows["c-1"][k] for k in ("run_kind", "engine", "engine_label", "model", "model_asked")},
          {"run_kind": "conversation", "engine": "claude-code", "engine_label": "Claude Code",
           "model": "claude-opus-5-5", "model_asked": "opus"})
    check("...a Codex one says Codex and its thread's model",
          (rows["c-2"]["engine_label"], rows["c-2"]["model"]), ("Codex", "gpt-6.1-sol"))

    # Delivery.
    record = {"job": "20261112-120000-cccccccc", "thread": tid, "persona": "ada",
              "result": {"outcome": "completed", "vault_receipt": "/v/r.md",
                         "receipt_text": "All done."}}
    before = len(threads.read(tid)[1])
    got = server.threads.deliver_job(record, w.handoff_text(record, "Fix it"))
    for _ in range(300):
        await asyncio.sleep(0.01)
        if server.threads.settled(tid):
            break
    msgs = threads.read(tid)[1]
    check("deliver_job: delivered, nothing posted by Loam, then Ada's turn answers",
          (got, [m["author"] for m in msgs[before:]]), ("delivered", ["ada"]))
    check("...the thread holds no receipt body and no path",
          any("All done." in m["text"] or "/v/r.md" in m["text"] for m in msgs), False)
    ada = next(v for v in fake.sessions.values() if v.agent == "ada" and v.prompts)
    check("...and Ada's turn input names the receipt's path to read",
          ("/v/r.md" in ada.prompts[-1], "own words" in ada.prompts[-1],
           ada.prompts[-1].rstrip().endswith("]")), (True, True, True))
    check("...delivered once: the same job again is not",
          server.threads.deliver_job(record, "x"), "already delivered")

    # A turn interrupted before its first word keeps its input for recovery.
    async def boom(t):
        fake.stopping = True      # the restart lands mid-turn
        raise RuntimeError("cut")
    real, server.threads.run_turn = server.threads.run_turn, boom
    before = len(threads.read(tid)[1])
    server.threads.schedule(engine_mod.Turn(tid, "ada", 0, "y"))
    for _ in range(100):
        await asyncio.sleep(0.01)
        if all(t.done() for t in server.threads.workers.values()):
            break
    fake.stopping = False
    lines = [m["text"] for m in threads.read(tid)[1][before:]]
    check("...a turn the restart cuts before a word posts no line", lines, [])
    check("...and its input is kept for the next start",
          [i["reason"] for i in engine_mod.inputs.items(tid, "ada")], ["y"])
    engine_mod.inputs.drop_member(tid, "ada")
    server.threads.run_turn = real
    check("...a persona not in the thread is not given a turn",
          server.threads.deliver_job({**record, "persona": "iris"}, "x"), "not a member")
    check("...nor a thread that is gone",
          server.threads.deliver_job({**record, "thread": "t-nope"}, "x"), "thread gone")


asyncio.run(broker_checks())


# -- 4. the restart rule ------------------------------------------------------

rows = [{"agent": "ada", "id": "a" * 10, "state": "background"},
        {"agent": "iris", "id": "b" * 10, "state": "idle"}]
# A planned restart requires idle sessions, including those waiting on background work.
check("idle does not hold a restart", loam_live.restart_blockers(rows[1:]), [])
check("background, working and asking do",
      loam_live.restart_blockers(rows + [{"agent": "ada", "id": "c" * 10, "state": "working"},
                                         {"agent": "pax", "id": "d" * 10, "state": "asking"}]),
      ["ada/aaaaaaaa background", "ada/cccccccc working", "pax/dddddddd asking"])
check("a failed read holds it", bool(loam_live.restart_blockers(None)), True)
check("a thread turn in flight holds it",
      loam_live.restart_blockers(rows[1:], [{"thread_id": "t-1", "persona": "iris"}]),
      ["iris's turn in t-1 in flight"])

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
