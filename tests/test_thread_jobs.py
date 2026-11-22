#!/usr/bin/env python3
"""Check server-derived job ownership, live status and ordered delivery of finished jobs to their thread."""

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp())
os.environ["LOAM_VAULT"] = str(TMP / "vault")
os.environ["LOAM_AI_LABS"] = str(TMP / "ai-labs")
os.environ["LOAM_THREADS_STATE"] = str(TMP / "state")
os.environ["LOAM_WAITERS_DIR"] = str(TMP / "waiters")
os.environ["LOAM_LOCK_DIR"] = str(TMP / "locks")
os.environ["LOAM_HOST_SETTINGS"] = str(TMP / "broker" / "settings.json")
os.environ["LOAM_UI_SOURCE"] = "none"
(TMP / "locks").mkdir()
sys.path.insert(0, str(AGENTS / "lib"))
import loam_config  # noqa: E402

# The briefer is the configured one: a brief from anyone else is refused.
LEAD = loam_config.LEAD_AGENT
for who in dict.fromkeys((LEAD, "iris")):
    (TMP / "ai-labs" / who).mkdir(parents=True)
    (TMP / "ai-labs" / who / "agent.json").write_text(
        json.dumps({"name": who, "display": who.capitalize()}))
(TMP / "vault" / "15_CONVERSATIONS").mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents").mkdir(parents=True)

sys.path.insert(0, str(AGENTS / "lib"))
import loam_broker as cfg  # noqa: E402
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}
import loam_activity as activity  # noqa: E402
import loam_engineer as eng  # noqa: E402
import loam_lock  # noqa: E402
import loam_threads as threads  # noqa: E402
import loam_waiters as w  # noqa: E402

cfg.roster = threads.roster
cfg.roster_cards = lambda: [{"id": p} for p in threads.roster()]
cfg.VAULT = TMP / "vault"
eng.VAULT = TMP / "vault"
# Memory always admits: this box's own pressure is not this test's subject.
(TMP / "meminfo").write_text("MemAvailable:   64000000 kB\n")
loam_lock.MEMINFO = str(TMP / "meminfo")

# One platform engineer of the test's own, with an agent.json the launcher's
# composition check reads; every reader goes through eng.projects().
ROOT = TMP / "engineer"
ROOT.mkdir()
(ROOT / "agent.json").write_text(json.dumps({"kind": "engineer"}))
PROJECT = eng.Project("Loam", ROOT, ROOT / "jobs", platform=True)
eng.projects = lambda: {"Loam": PROJECT}


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


job = load("engineer_job_thread_jobs", AGENTS / "bin" / "engineer-job")
broker_mod = load("session_broker_thread_jobs", AGENTS / "bin" / "session-broker")
engine_mod = broker_mod.loam_thread_engine

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:72}"
          f"{'  | ' + str(detail)[:300] if detail != '' else ''}")


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = datetime.now(timezone.utc)

# -- 1. accept() records the thread the session serves ------------------------

home = threads.create(LEAD, "Themes")["id"]
other = threads.create(LEAD, "Elsewhere")["id"]
state = TMP / "state"
(state / "steps" / "sessions").mkdir(parents=True, exist_ok=True)
(state / "sessions.json").write_text(json.dumps(
    {home: {LEAD: {"session_id": "sess-lead"}},
     other: {LEAD: {"session_id": "sess-lead-2"}}}))


def brief(title, env, args=None):
    """`accept` with the environment the brief tool's launcher would have."""
    keep = {k: os.environ.get(k) for k in ("LOAM_WAKE", w.SESSION_ENV, "LOAM_AGENT")}
    for k in keep:
        os.environ.pop(k, None)
    os.environ.update(env)
    try:
        got = job.accept({"project": "Loam", "title": title, "brief": "Do it.",
                          "involvement": "go and do it", **(args or {})},
                         env.get("LOAM_AGENT", LEAD))
    finally:
        for k, v in keep.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    assert not isinstance(got, str), got
    return got[1], got[2]


run_id, run_p = brief("Running from the thread",
                      {"LOAM_AGENT": LEAD, w.SESSION_ENV: "sess-lead"},
                      {"thread": other})
meta = job.read_brief_meta(run_p)
check("a brief from a thread member's session records that thread",
      (meta["caller"], meta["caller_session"], meta["thread"])
      == ("conversation", "sess-lead", home),
      (meta["caller"], meta["caller_session"], meta["thread"]))
check("...derived on the server: a `thread` the caller passed is not it",
      meta["thread"] != other, meta["thread"])

cron_id, cron_p = brief("From a cron",
                        {"LOAM_AGENT": LEAD, "LOAM_WAKE": "cron:infra-watch",
                         w.SESSION_ENV: "sess-lead"})
check("a brief from a cron records no thread",
      job.read_brief_meta(cron_p)["thread"] == "", job.read_brief_meta(cron_p)["thread"])
shell_id, shell_p = brief("From the shell", {"LOAM_AGENT": LEAD})
check("a brief from the shell (no session anywhere above it) records no thread",
      job.read_brief_meta(shell_p)["thread"] == "",
      job.read_brief_meta(shell_p)["thread"])

# -- 2. from a subagent: the session is the CLI's, levels up ------------------


def fake_proc(chain):
    root = TMP / f"proc-{len(list(TMP.glob('proc-*')))}"
    for pid, ppid, env, argv in chain:
        d = root / str(pid)
        d.mkdir(parents=True)
        (d / "stat").write_text(f"{pid} (x) S {ppid} 1 1 0 -1 0 0 0 0 0 0 0 0 0 0 "
                                f"20 0 1 0 {1000 + pid} 0 0\n")
        (d / "environ").write_bytes(b"\0".join(f"{k}={v}".encode()
                                               for k, v in env.items()))
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv))
    return root


# A background subagent runs inside the session's own CLI process; its
# engineer tool call is the same MCP server, child of that CLI.
proc = fake_proc([(50, 40, {}, ["python3", "engineer-job", "--brief"]),
                  (40, 30, {"LOAM_AGENT": LEAD}, ["python", "engineer_server.py"]),
                  (30, 20, {w.SESSION_ENV: "sess-lead"},
                   ["/home/x/.local/bin/claude", "-p", "--session-id", "sess-lead"]),
                  (20, 1, {}, ["python3", "session-broker"])])
check("from a subagent's tool call: the thread of the session above it",
      w.caller({"LOAM_AGENT": LEAD}, 50, proc, state).get("thread") == home,
      w.caller({"LOAM_AGENT": LEAD}, 50, proc, state))

# -- 3. every state, on the thread's record -----------------------------------

queued_id, queued_p = brief("Queued from the thread",
                            {"LOAM_AGENT": LEAD, w.SESSION_ENV: "sess-lead"})
(queued_p["dir"] / "queued").write_text(json.dumps(
    {"joined": iso(NOW - timedelta(minutes=1)), "held_by": "memory"}))

landing_id, landing_p = brief("Landing from the thread",
                              {"LOAM_AGENT": LEAD, w.SESSION_ENV: "sess-lead"})
(landing_p["dir"] / "owner.json").write_text(json.dumps(
    {"pid": os.getpid(), "since": iso(NOW - timedelta(minutes=30))}))
(landing_p["dir"] / "landing").write_text(json.dumps(
    {"pid": os.getpid(), "since": iso(NOW - timedelta(minutes=2)), "phase": "ci",
     "until": (NOW + timedelta(minutes=5)).timestamp()}))


def finished(title, ago, result, env=None):
    jid, p = brief(title, env or {"LOAM_AGENT": LEAD, w.SESSION_ENV: "sess-lead"})
    receipt = TMP / "vault" / "40_PROJECTS" / "Loam" / "receipts" / f"{jid}.md"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text("receipt\n")
    (p["dir"] / "result.json").write_text(json.dumps({
        "started": iso(NOW - ago - timedelta(minutes=12)),
        "finished": iso(NOW - ago), "vault_receipt": str(receipt), **result}))
    return jid


landed_id = finished("Landed five minutes ago", timedelta(minutes=5),
                     {"ok": True, "stage": "done", "outcome": "completed",
                      "landing_state": "landed"})
deployed_id = finished("Deployed a minute ago", timedelta(minutes=1),
                       {"ok": True, "stage": "done", "outcome": "completed",
                        "landing_state": "landed",
                        "deploy": {"exit": 0, "result": {"action": "deployed"}}})
cut_id = finished("Cut off", timedelta(minutes=3),
                  {"ok": False, "stage": "done",
                   "outcome": "cut off at its 60-minute wall clock",
                   "landing_state": "not_landed"})
old_id = finished("Finished twenty minutes ago", timedelta(minutes=20),
                  {"ok": True, "stage": "done", "outcome": "completed",
                   "landing_state": "landed"})
iris_id = finished("Another thread's", timedelta(minutes=2),
                   {"ok": True, "stage": "done", "outcome": "completed",
                    "landing_state": "landed"},
                   {"LOAM_AGENT": LEAD, w.SESSION_ENV: "sess-lead-2"})

FIELDS = {"job", "project", "scope", "title", "briefed_by", "thread", "state", "phase",
          "since", "started", "finished", "elapsed_seconds", "outcome",
          "outcome_detail", "ok", "receipt", "position", "held_by",
          "last_activity", "quiet_since", "child_stops"}


class FakeBroker:
    def __init__(self):
        self.sessions = {}
        self.token = "test-token"

    async def start_session(self, *a, **k):
        raise AssertionError("no turn is started in this test")


TOKEN = TMP / "token"
TOKEN.write_text("test-token")


async def http(port, method, path):
    import urllib.request  # noqa: PLC0415

    def go():
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", method=method,
            headers={"Authorization": "Bearer test-token"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    return await asyncio.to_thread(go)


async def cli(port, *argv):
    env = dict(os.environ, LOAM_BROKER_URL=f"http://127.0.0.1:{port}",
               LOAM_BROKER_TOKEN_FILE=str(TOKEN), LOAM_AGENT=LEAD)
    env.pop("LOAM_WAKE", None)
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(AGENTS / "bin" / "settings"), *argv, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    try:
        return json.loads(out)
    except ValueError:
        return {"ok": False, "error": f"no JSON: {out!r} {err[-400:]!r}"}


async def main():
    server = broker_mod.Server.__new__(broker_mod.Server)
    fake = FakeBroker()
    server.broker = fake
    server.threads = engine_mod.ThreadEngine(fake)
    tcp = await asyncio.start_server(server.handle, "127.0.0.1", 0)
    port = tcp.sockets[0].getsockname()[1]
    try:
        # The running job holds a real run lock, as its inner does.
        with loam_lock.hold("engineer", f"job:{run_id}", wait_seconds=5):
            activity._ROWS.clear()
            status, body = await http(port, "GET", f"/v1/threads/{home}?limit=1&seen=0")
            rows = body["thread"].get("jobs")
            check("GET /v1/threads/{id} answers with thread.jobs",
                  status == 200 and isinstance(rows, list)
                  and body["thread"].get("jobs_error") is None, body["thread"].get("jobs_error"))
            by_id = {r["job"]: r for r in rows}
            check("...this thread's jobs, and only those",
                  set(by_id) == {run_id, queued_id, landing_id, landed_id,
                                 deployed_id, cut_id}, sorted(by_id))
            check("...not a cron's, not the shell's, not another thread's, "
                  "not one finished 20 minutes ago",
                  not {cron_id, shell_id, iris_id, old_id} & set(by_id))
            check("...every row has the contract's fields",
                  all(set(r) == FIELDS for r in rows),
                  [sorted(set(r) ^ FIELDS) for r in rows])
            check("...active first (running and landing by start, then "
                  "queued), then the finished, newest first",
                  [r["job"] for r in rows]
                  == [landing_id, run_id, queued_id, deployed_id, cut_id, landed_id],
                  [r["title"] for r in rows])
            r = by_id[run_id]
            check("running: state, start, elapsed, who briefed it",
                  r["state"] == "running" and r["started"] and r["elapsed_seconds"] is not None
                  and r["briefed_by"] == LEAD and r["project"] == "Loam"
                  and r["title"] == "Running from the thread" and r["outcome"] is None, r)
            r = by_id[queued_id]
            check("queued: position and what it waits on, not started",
                  r["state"] == "queued" and r["position"] == 1
                  and r["held_by"] == "memory" and r["started"] is None
                  and r["elapsed_seconds"] is None, r)
            r = by_id[landing_id]
            check("landing: its phase, started when its session did",
                  r["state"] == "landing" and r["phase"] == "ci"
                  and r["started"] == iso(NOW - timedelta(minutes=30))
                  and r["elapsed_seconds"] >= 30 * 60 - 5, r)
            r = by_id[landed_id]
            check("finished: landed, its receipt's vault path, elapsed to its finish",
                  r["state"] == "finished" and r["outcome"] == "landed"
                  and r["ok"] is True and r["elapsed_seconds"] == 12 * 60
                  and r["receipt"] == f"40_PROJECTS/Loam/receipts/{landed_id}.md", r)
            check("finished: deployed", by_id[deployed_id]["outcome"] == "deployed",
                  by_id[deployed_id])
            check("finished: cut off, with the launcher's own words beside it",
                  by_id[cut_id]["outcome"] == "cut off"
                  and by_id[cut_id]["outcome_detail"].startswith("cut off at"),
                  by_id[cut_id])

            status, body = await http(port, "GET", f"/v1/threads/{other}?limit=1&seen=0")
            check("the other thread sees its own job alone",
                  [r["job"] for r in body["thread"]["jobs"]] == [iris_id],
                  body["thread"]["jobs"])

            # 4. a persona reads the same list through the settings tool.
            out = await cli(port, "get", "thread.jobs", "--target", home)
            check("bin/settings get thread.jobs: the same list",
                  out.get("ok") and [r["job"] for r in out.get("value") or []]
                  == [r["job"] for r in rows], out)
        # The run lock let go and a verdict written: the job is finished.
        (run_p["dir"] / "result.json").write_text(json.dumps({
            "ok": False, "stage": "cancelled", "outcome": "cancelled",
            "started": iso(NOW), "finished": iso(datetime.now(timezone.utc))}))
        activity._ROWS.clear()
        status, body = await http(port, "GET", f"/v1/threads/{home}?limit=1&seen=0")
        r = {x["job"]: x for x in body["thread"]["jobs"]}.get(run_id) or {}
        check("a job that ends stays on the thread as finished, with its outcome",
              r.get("state") == "finished" and r.get("outcome") == "cancelled", r)
    finally:
        tcp.close()
        await tcp.wait_closed()


asyncio.run(main())

# -- 5. the outcome words ------------------------------------------------------

cases = [
    ({"stage": "cancelled", "ok": False}, "cancelled"),
    ({"stage": "interrupted", "ok": False, "outcome": "needs_persona"}, "interrupted"),
    ({"stage": "done", "ok": False, "outcome": "cut off at the 100-round-trip cap"}, "cut off"),
    ({"stage": "refused", "ok": False}, "failed"),
    ({"stage": "done", "ok": False, "outcome": "exited 1"}, "failed"),
    ({"stage": "done", "ok": True, "deployed": True, "landing": "landed"}, "deployed"),
    ({"stage": "done", "ok": True, "deployed": False, "landing": "landed"}, "deploy failed"),
    ({"stage": "done", "ok": True, "landing": "landed_earlier"}, "landed"),
    ({"stage": "done", "ok": True, "landing": "pull_request"}, "pull request"),
    ({"stage": "done", "ok": True, "landing": "not_landed"}, "not landed"),
    ({"stage": "done", "ok": True, "landing": "read_only"}, "finished"),
    ({"stage": "done", "ok": True, "landing": None}, "finished"),
]
got = [activity.outcome_word(item) for item, _ in cases]
check("outcome words from the launcher's verdicts", got == [w_ for _, w_ in cases], got)
check("...each one of OUTCOMES", set(got) <= set(activity.OUTCOMES))

# -- 6. a brief returns at once; its result comes back as input ---------------

import io  # noqa: E402
import time  # noqa: E402

launched = []
job.launch = lambda project, jid, p, minutes: launched.append(jid)
job.resume_queued = lambda: []
job.preflight = lambda project, p, meta: None   # the composition is not this test's


def tool(env, title):
    """`engineer-job --brief` in-process: stdin in, the one JSON line out."""
    keep = {k: os.environ.get(k) for k in ("LOAM_WAKE", w.SESSION_ENV, "LOAM_AGENT")}
    for k in keep:
        os.environ.pop(k, None)
    os.environ.update(env)
    stdin, stdout = sys.stdin, sys.stdout
    sys.stdin = io.StringIO(json.dumps({"project": "Loam", "title": title,
                                        "brief": "Do it.", "involvement": "go"}))
    sys.stdout = io.StringIO()
    started = time.monotonic()
    try:
        code = job.run_tool()
        out = json.loads(sys.stdout.getvalue())
    finally:
        sys.stdin, sys.stdout = stdin, stdout
        for k, v in keep.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    return code, out, time.monotonic() - started


THREAD_ENV = {"LOAM_AGENT": LEAD, w.SESSION_ENV: "sess-lead"}
code, out, took = tool(THREAD_ENV, "Returns at once")
fast_id = out.get("job_id")
check("--brief from a thread returns in under 5 s, with the job id",
      code == 0 and out.get("ok") and fast_id and took < 5, (round(took, 2), out))
check("...launched, nothing waited on",
      launched[-1:] == [fast_id], launched)
check("...the receipt's path and a sentence saying the result comes back as input",
      out.get("receipt_path") == str(job.paths(PROJECT, fast_id)["receipt"])
      and "next input" in out.get("receipt", "") and out.get("queued") is False, out)
rec = json.loads(w._path(fast_id).read_text())
check("...its record names the thread and has no waiter",
      rec.get("thread") == home and rec.get("returned") is True and "pid" not in rec, rec)
w.settle(fast_id)

code, out, took = tool({"LOAM_AGENT": LEAD, "LOAM_WAKE": "cron:infra-watch",
                        w.SESSION_ENV: "sess-cron"}, "From a cron, at once")
cron_id = out.get("job_id")
rec = json.loads(w._path(cron_id).read_text())
check("a brief with no thread (a cron) returns at once too",
      code == 0 and out.get("ok") and took < 5 and launched[-1:] == [cron_id],
      (round(took, 2), out))
check("...saying its result comes back as a run resuming this session",
      "resumes this session" in out.get("receipt", ""), out)
check("...its record names its session and lane, and goes by a run",
      (rec.get("caller"), rec.get("caller_session"), rec.get("caller_lane"),
       w.route(rec)) == ("cron", "sess-cron", "cron:infra-watch", "run"), rec)
w.settle(cron_id)
check("...and a settled record leaves nothing", w.records() == [], w.records())


class TurnSession:
    """A session whose turns say nothing unless told to, and whose first
    turn can be held open (`gate`) to stand for a turn under way."""
    STEERS = True

    def __init__(self, agent, sid):
        self.agent, self.id, self.provider = agent, sid, "claude-code"
        self.model, self.title, self._busy = None, None, None
        self.subs, self.prompts, self.steers = set(), [], []
        self.idle_timeout_s, self.live, self.proc = None, True, None
        self.last_activity = time.time() - 600
        self.gate, self.words, self.state = None, {}, "idle"

    def busy(self):
        return self._busy

    def status(self):
        return self.state

    def save(self):
        pass

    def watch(self, q):
        self.subs.add(q)

    def unwatch(self, q):
        self.subs.discard(q)

    def meta(self):
        return {"session_id": self.id, "agent": self.agent}

    async def steer(self, text, attachments=None):
        self.steers.append(text)
        return 1

    async def send_message(self, text):
        self.prompts.append(text)
        n = len(self.prompts)

        async def go():
            self.state = "working"
            if n == 1 and self.gate is not None:
                await self.gate.wait()
            for q in list(self.subs):
                said = self.words.get(n)
                if said:
                    q.put_nowait({"type": "assistant", "parent_tool_use_id": None,
                                  "message": {"content": [{"type": "text", "text": said}]}})
                q.put_nowait({"type": "result", "parent_tool_use_id": None})
            self.state = "idle"
        asyncio.get_running_loop().create_task(go())
        return 1

    async def stop(self):
        self.live = False


class TurnBroker:
    def __init__(self):
        self.sessions, self.record_subscribers = {}, set()
        self.stopping = False
        self.token = "test-token"

    def live(self):
        return [s for s in self.sessions.values() if s.live]

    def seats_taken(self):
        return len(self.live())

    def room(self):
        return {"ok": True, "available_mb": 20_000, "reserve_mb": 3_000, "need_mb": 450,
                "starting": 0, "cgroup_room_mb": None}

    async def start_session(self, agent, session_id=None, resume=False, provider=None, **kw):
        for s in self.sessions.values():
            if s.agent == agent:
                return s
        s = TurnSession(agent, session_id or f"s{len(self.sessions) + 1}")
        self.sessions[s.id] = s
        return s


def loam_lines(tid):
    return sum(1 for m in threads.read(tid)[1] if m.get("author") == "system")


async def deliveries():
    quiet = threads.create(LEAD, "Quiet results")["id"]
    server = broker_mod.Server.__new__(broker_mod.Server)
    fake = TurnBroker()
    server.broker = fake
    server.threads = engine_mod.ThreadEngine(fake)
    # Two jobs briefed from this thread, finishing two seconds apart; the
    # later one registered first, so order is the verdicts', not the files'.
    ids = []
    for title, finished_at in (("Second to finish", NOW + timedelta(seconds=2)),
                               ("First to finish", NOW)):
        jid, p = brief(title, THREAD_ENV)
        (p["dir"] / "result.json").write_text(json.dumps({
            "ok": True, "stage": "done", "outcome": "completed",
            "landing_state": "landed", "landing": "**Landing: landed at `abc1234`**",
            "finished": iso(finished_at)}))
        w.register(jid, "Loam", p["dir"], LEAD,
                   {"caller": "conversation", "thread": quiet})
        ids.append(jid)
    second, first = ids
    # The persona is mid-answer to a message when they arrive.
    session = await fake.start_session(LEAD)
    session.gate = asyncio.Event()
    session.words = {1: "Here is my answer about the other thing."}
    msg = threads.post_person(quiet, "Something else, please.") \
        if hasattr(threads, "post_person") else None
    server.threads.schedule(engine_mod.Turn(quiet, LEAD, 0,
                                            (msg or {}).get("id") or "m1"))
    for _ in range(200):
        await asyncio.sleep(0.01)
        if session.prompts:
            break
    lines_before = loam_lines(quiet)
    count_before = len(threads.read(quiet)[1])
    broker_mod.DELIVER_FIRST_S, broker_mod.DELIVER_EVERY_S = 0, 0.05
    pass_task = asyncio.create_task(server.deliver_owed())
    for _ in range(200):
        await asyncio.sleep(0.02)
        if not w.records():
            break
    check("the delivery pass takes both results while the persona's turn is under way",
          w.records() == [] and len(session.prompts) == 1, (w.records(), len(session.prompts)))
    await asyncio.sleep(0.3)
    check("...neither is steered into the turn that is generating",
          session.steers == [] and len(session.prompts) == 1,
          (len(session.steers), len(session.prompts)))
    session.gate.set()
    for _ in range(400):
        await asyncio.sleep(0.02)
        if server.threads.settled(quiet) and len(session.prompts) >= 3:
            break
    await asyncio.sleep(0.2)
    fake.stopping = True
    await asyncio.wait_for(pass_task, 5)
    prompts = session.prompts
    check("...then each is the input of its own turn, after the one under way, oldest first",
          len(prompts) == 3 and first in prompts[1] and second in prompts[2]
          and second not in prompts[1], [p[-400:] for p in prompts[1:]])
    check("...the input carries the receipt's path, the verdict and the landing",
          all(s in prompts[1] for s in ("receipt.md", "completed: Landing: landed at abc1234",
                                        "Landing: landed", "Nothing was posted")),
          prompts[1][-500:])
    msgs = threads.read(quiet)[1]
    check("the thread holds no new Loam line (the count before and after)",
          (lines_before, loam_lines(quiet)) == (lines_before, lines_before),
          (lines_before, loam_lines(quiet)))
    check("...and only the persona's answer to the message: the turns that "
          "said nothing left nothing", [m["author"] for m in msgs[count_before:]]
          in ([LEAD], []) and len(msgs) - count_before <= 1,
          [(m["author"], m["text"][:60]) for m in msgs[count_before:]])
    log = [json.loads(x) for x in w.LOG.read_text().splitlines()]
    mine = [x for x in log if x["job"] in ids]
    check("each delivered once, and logged so, oldest first",
          [(x["job"], x["outcome"]) for x in mine]
          == [(first, "delivered"), (second, "delivered")], mine)
    check("...a second delivery of the same job is refused",
          server.threads.deliver_job({"job": first, "thread": quiet, "persona": LEAD},
                                     "x") == "already delivered")


w.LOG = TMP / "deliveries.jsonl"
asyncio.run(deliveries())

# A job that returned at once and whose unit ended before its inner took it
# has no waiter to say so: recovery writes the waiter's "died", and the
# delivery pass then carries it like any verdict.
job.unit_state = lambda unit: "inactive"
dead_id, dead_p = brief("Died before it started", THREAD_ENV)
w.register(dead_id, "Loam", dead_p["dir"], LEAD, {"thread": home})
check("recovery leaves a just-launched job alone for its startup grace",
      not job.died_unheard(PROJECT, dead_id, dead_p) and not dead_p["result"].is_file())
rec = json.loads(w._path(dead_id).read_text())
rec["since"] = iso(NOW - timedelta(minutes=2))
w._path(dead_id).write_text(json.dumps(rec))
report = job.sweep()
verdict = json.loads(dead_p["result"].read_text()) if dead_p["result"].is_file() else {}
check("...then writes it a 'died' verdict, which makes its result owed",
      dead_id in report["died"] and verdict.get("stage") == "died"
      and dead_id in [r["job"] for r in w.owed()], (report.get("died"), verdict))
check("...and a legacy job with no such record is left as legacy",
      dead_id not in report["legacy"] and not set(report["died"]) - {dead_id},
      report["died"])
w.settle(dead_id)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if results and all(results) else 1)
