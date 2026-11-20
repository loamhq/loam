#!/usr/bin/env python3
"""A persona is told, once, that its last scheduled run failed (2026-11-18).

When the last run of a persona's reflection or of one of its crons did not
run to the end, the next thread turn the broker starts for it carries one
line at the top: which run, when, the cause as recorded, the file to read.
Driven through the real ThreadEngine with a stand-in broker (the harness of
tests/test_thread_context.py), against a scratch wake log:

  - a failed reflection: the first turn says so, quoted below;
  - the next turn does not say it again;
  - a good run clears it: a failure followed by a good run is never told;
  - a new failure after a told one is told, on a resumed session too;
  - a cron's failure is named as that cron; a not-enabled fire is no run;
  - a broker on scratch workspaces with the live logs tells nothing.

Run: python3 tests/test_lane_notice.py
"""

from __future__ import annotations

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp())
os.environ["LOAM_VAULT"] = str(TMP / "vault")
os.environ["LOAM_AI_LABS"] = str(TMP / "ai-labs")
os.environ["LOAM_THREADS_STATE"] = str(TMP / "state")
os.environ["LOAM_LANE_NOTICE_LOGS"] = str(TMP / "logs")
os.environ["LOAM_LANE_NOTICE_STATE"] = str(TMP / "told")
(TMP / "logs").mkdir()
for who in ("ada", "juno"):
    (TMP / "ai-labs" / who).mkdir(parents=True)
    (TMP / "ai-labs" / who / "agent.json").write_text(
        json.dumps({"name": who, "display": who.capitalize()}))
(TMP / "vault" / "15_CONVERSATIONS").mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents").mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents" / "comms.md").write_text("# From agents\n")

sys.path.insert(0, str(AGENTS / "lib"))
import loam_broker as cfg  # noqa: E402
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}
import loam_threads as threads  # noqa: E402
OWNER, OWNER_NAME = cfg.lc.OWNER_ID, cfg.lc.OWNER_NAME

cfg.roster = threads.roster
cfg.roster_cards = lambda: [{"id": p} for p in threads.roster()]
cfg.VAULT = TMP / "vault"
cfg.ATTACHMENT_ROOT = cfg.VAULT / "98_ATTACHMENTS"

loader = importlib.machinery.SourceFileLoader(
    "session_broker", str(AGENTS / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker_mod = importlib.util.module_from_spec(spec)
loader.exec_module(broker_mod)
engine_mod = broker_mod.loam_thread_engine
steps_mod = broker_mod.thread_steps

results = []


def check(label, got, want):
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:66} got={got!r}")
    if not ok:
        print(f"      wanted {want!r}")


class FakeWriter:
    def __init__(self):
        self.chunks = []

    def write(self, data):
        self.chunks.append(data)

    async def drain(self):
        return None

    def json(self):
        text = b"".join(self.chunks).decode()
        return int(text.split()[1]), json.loads(text.split("\r\n\r\n", 1)[1])

    def items(self):
        text = b"".join(self.chunks).decode()
        return [json.loads(line[6:]) for line in text.splitlines()
                if line.startswith("data: ")]


def text(t):
    return {"type": "assistant", "parent_tool_use_id": None,
            "message": {"content": [{"type": "text", "text": t}]}}


def tool(tid, name, inp):
    return {"type": "assistant", "parent_tool_use_id": None,
            "message": {"content": [{"type": "tool_use", "id": tid, "name": name,
                                     "input": inp}]}}


def output(tid, out):
    return {"type": "user", "parent_tool_use_id": None,
            "message": {"content": [{"type": "tool_result", "tool_use_id": tid,
                                     "content": out}]}}


def result(n):
    return {"type": "result", "parent_tool_use_id": None, "num_turns": n}


class Session:
    n = 0

    def __init__(self, agent, provider):
        Session.n += 1
        self.agent, self.provider, self.id = agent, provider, f"sess-{Session.n}"
        self.model = None
        self.subs = set()
        self.script = []
        self.prompts = []
        self.background_tasks = []
        self.log = []
        self.seq = 0
        self.turns = []
        self.live = True

    def is_live(self):
        return self.live

    def busy(self):
        return None

    def save(self):
        pass

    def watch(self, q):
        self.subs.add(q)

    def unwatch(self, q):
        self.subs.discard(q)

    def working_behind(self):
        return False

    def replay(self, since):
        return [e for e in self.log if e["seq"] > since]

    def emit(self, ev):
        self.seq += 1
        ev = {**ev, "seq": self.seq}
        self.log.append(ev)
        for q in list(self.subs):
            q.put_nowait(ev)

    async def send_message(self, prompt, attachments=None):
        self.prompts.append(prompt)
        script, self.script = self.script or [text("ok"), result(0)], []

        async def play():
            for ev in script:
                await asyncio.sleep(0.002)
                self.emit(ev)
        asyncio.get_running_loop().create_task(play())
        return 1


class Broker:
    def __init__(self):
        self.sessions = {}

    async def start_session(self, agent, session_id=None, resume=False,
                            provider=None, model=None):
        if session_id in self.sessions:
            return self.sessions[session_id]
        s = Session(agent, provider)
        self.sessions[s.id] = s
        return s


async def call(server, method, path, payload=None, params=None):
    w = FakeWriter()
    try:
        await server.route(method, path, params or {}, payload or {}, w)
    except (broker_mod.BrokerError, threads.ThreadError) as exc:
        return exc.status, {"error": exc.message}
    return w.json()


async def settle(server, tid, rounds=400):
    for _ in range(rounds):
        await asyncio.sleep(0.01)
        if server.threads.settled(tid):
            return




def wake_row(who, **row):
    with (TMP / "logs" / f"wake-{who}.jsonl").open("a") as fh:
        fh.write(json.dumps(row) + "\n")


async def turn(server, fake, tid, words):
    """Return the prompt dispatched for this message."""
    seen = {s.id: len(s.prompts) for s in fake.sessions.values()}
    await call(server, "POST", f"/v1/threads/{tid}/messages", {"text": words})
    await settle(server, tid)
    new = [p for s in fake.sessions.values() if s.agent == "ada"
           for p in s.prompts[seen.get(s.id, 0):]]
    return new[0] if new else ""


async def main():
    import loam_lane_notice as notice
    server = broker_mod.Server.__new__(broker_mod.Server)
    fake = Broker()
    server.broker = fake
    server.threads = engine_mod.ThreadEngine(fake)

    wake_row("ada", ts="2026-11-18T10:00:00Z", ok=True, lane="reflection")
    wake_row("ada", ts="2026-11-18T10:00:04Z", ok=False, stage="crash",
             at="launch", lane="reflection",
             error="OSError: [Errno 7] Argument list too long: '/usr/bin/claude'")
    tid = (await call(server, "POST", "/v1/threads", {"persona": "ada"}))[1]["id"]
    first = await turn(server, fake, tid, "hello ada")
    line = first.split("\n", 1)[0]
    print(f"      the line: {line}")
    check("a failed wake: the first turn opens with one line saying so",
          line.startswith("[Loam: your last nightly reflection wake "
                          "(2026-11-18T10:00:04Z) did not run to the end -- crash: "
                          "OSError: [Errno 7] Argument list too long"), True)
    check("...naming the failure log and the run's own line",
          ("loam-wake-failures.md" in line, "wake-ada.jsonl" in line), (True, True))
    check("...and the turn is otherwise the usual one",
          "hello ada" in first and "[Loam thread" in first, True)
    second = await turn(server, fake, tid, "and again")
    check("told once: the next turn does not carry it", "[Loam: your last" in second, False)

    other = (await call(server, "POST", "/v1/threads", {"persona": "ada"}))[1]["id"]
    third = await turn(server, fake, other, "a new thread")
    check("...nor does a fresh session in another thread", "[Loam: your last" in third, False)

    wake_row("ada", ts="2026-11-19T10:00:00Z", ok=True, lane="reflection")
    check("a good run clears it", notice.pending("ada"), [])

    wake_row("ada", ts="2026-11-20T10:00:00Z", ok=False, stage="preflight",
             lane="reflection", error="_reflection.md is missing")
    wake_row("ada", ts="2026-11-20T10:20:00Z", ok=True, lane="reflection")
    fourth = await turn(server, fake, tid, "after a dark night that recovered")
    check("a failure the next run already cleared is never told",
          "[Loam: your last" in fourth, False)

    wake_row("ada", ts="2026-11-21T10:00:00Z", ok=False, stage="invoke",
             lane="reflection", returncode=1, stderr="boom")
    wake_row("ada", ts="2026-11-21T13:00:00Z", ok=True, stage="not_enabled",
             lane="cron:infra-watch")
    wake_row("ada", ts="2026-11-21T14:00:00Z", ok=False, stage="lock_timeout",
             lane="cron:market-watch")
    fifth = await turn(server, fake, tid, "a resumed session")
    line = fifth.split("\n", 1)[0]
    check("a new failure is told, on a resumed session too",
          "nightly reflection wake (2026-11-21T10:00:00Z)" in line
          and "invoke: boom" in line, True)
    check("...with a cron's failure named as that cron, in the same line",
          "cron `market-watch` (2026-11-21T14:00:00Z)" in line, True)
    check("...and a not-enabled fire is no run", "infra-watch" in line, False)

    # A scratch broker reading the live logs tells nothing and marks nothing.
    saved = os.environ.pop("LOAM_LANE_NOTICE_LOGS")
    check("scratch workspaces, live logs: nothing is told", notice.take("ada"), None)
    os.environ["LOAM_LANE_NOTICE_LOGS"] = saved

    print(f"\n{sum(results)}/{len(results)} passed")
    return all(results)


ok = asyncio.run(main())
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(0 if ok else 1)
