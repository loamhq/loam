#!/usr/bin/env python3
"""Recover placeholder replies from session logs while preserving existing messages, IDs and timestamps."""

import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR")))
os.environ["LOAM_VAULT"] = str(TMP / "vault")
os.environ["LOAM_AI_LABS"] = str(TMP / "ai-labs")
os.environ["LOAM_THREADS_STATE"] = str(TMP / "state")
(TMP / "ai-labs" / "ada").mkdir(parents=True)
(TMP / "ai-labs" / "ada" / "agent.json").write_text(json.dumps({"name": "Ada"}))
(TMP / "vault" / "15_CONVERSATIONS").mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents").mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents" / "comms.md").write_text("# From agents\n")

sys.path.insert(0, str(AGENTS / "lib"))
import loam_broker as cfg  # noqa: E402
import loam_thread_steps as steps  # noqa: E402
import loam_threads as threads  # noqa: E402
OWNER, OWNER_NAME = cfg.lc.OWNER_ID, cfg.lc.OWNER_NAME

cfg.SESSION_DIR = TMP / "sessions"
cfg.SESSION_DIR.mkdir()
loader = importlib.machinery.SourceFileLoader(
    "backfill", str(AGENTS / "bin" / "backfill-thread-steps"))
spec = importlib.util.spec_from_loader(loader.name, loader)
backfill = importlib.util.module_from_spec(spec)
loader.exec_module(backfill)

results = []


def check(label, got, want):
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:66} got={got!r}")
    if not ok:
        print(f"      wanted {want!r}")


SID = "sess-recover"
seq = 0


def ev(e, ts=None):
    global seq
    seq += 1
    e = {**e, "seq": seq, "session_id": SID}
    if ts:
        e["timestamp"] = ts
    return e


def text(t, ts):
    return ev({"type": "assistant", "parent_tool_use_id": None,
               "message": {"content": [{"type": "text", "text": t}]}}, ts)


def tool(name, ts):
    return ev({"type": "assistant", "parent_tool_use_id": None,
               "message": {"content": [{"type": "tool_use", "id": f"t{seq}",
                                        "name": name, "input": {}}]}}, ts)


def result():
    return ev({"type": "result", "parent_tool_use_id": None, "num_turns": 2})


log = [
    ev({"type": "broker.user_message", "text": "go"}),
    tool("Bash", "2026-11-10T18:35:00.100Z"),
    text("First reply, said at 18:37.", "2026-11-10T18:37:42.900Z"), result(),
    tool("Agent", "2026-11-10T18:50:00.000Z"),
    text("Second reply, said at 18:53.", "2026-11-10T18:53:40.200Z"), result(),
    ev({"type": "system", "subtype": "init"}, "2026-11-10T19:00:00.000Z"),
    ev({"type": "broker.user_message", "text": "and?"}),
    text("Already posted late.", "2026-11-10T19:10:00.000Z"), result(),
]
(cfg.SESSION_DIR / f"{SID}.jsonl").write_text(
    "".join(json.dumps(e) + "\n" for e in log))

tid = threads.create("ada", "Recover")["id"]
real_now = threads.now_iso
threads.now_iso = lambda: "2026-11-10T18:34:59Z"
threads.append(tid, threads.OWNER, "go")
threads.now_iso = lambda: "2026-11-10T19:04:31Z"
place = threads.append(tid, "ada", "(no reply: the turn ended by timeout)",
                       did=[{"label": "Said: First reply, said at"},
                            {"label": "Said: Second reply, said at 18:53."}],
                       engine="claude-code")
threads.now_iso = lambda: "2026-11-10T19:09:00Z"
threads.append(tid, threads.OWNER, "and?")
slot = threads.append(tid, "ada", "(no reply)", engine="claude-code")
threads.now_iso = lambda: "2026-11-10T19:20:00Z"
threads.append(tid, "ada", "Already posted late.", engine="claude-code", late=True)
threads.now_iso = real_now
threads.remember_session(tid, "ada", SID, "claude-code", "m6", seq=seq)
steps.write(tid, steps.entry(slot, tid, SID, log[8:9]))
_, before, path = threads.read(tid)
raw_before = path.read_text()

check("a message that is not a placeholder is refused",
      backfill.recover(tid, before[0]["id"], dry=False), False)
check("the placeholder whose turn said two things is recovered",
      backfill.recover(tid, place["id"], dry=False), True)
check("the in-flight slot whose reply is already in the thread",
      backfill.recover(tid, slot["id"], dry=False), True)
_, after, _ = threads.read(tid)
check("two replies at their own times, the first keeping the id, then the rest",
      [(m["id"] if m["author"] == "ada" and m["text"] != "Already posted late."
        else None, m["author"], m["text"], m.get("late")) for m in after],
      [(None, OWNER, "go", None),
       (place["id"], "ada", "First reply, said at 18:37.", True),
       ("m6", "ada", "Second reply, said at 18:53.", True),
       (None, OWNER, "and?", None),
       (None, "ada", "Already posted late.", True)])
old = {m["id"]: m for m in before}
check("no other message changed",
      [m["id"] for m in after if m["id"] in old and m["id"] not in (place["id"],)
       and m != old[m["id"]]], [])
blocks_kept = all(threads.render_message(m) in path.read_text()
                  for m in before if m["id"] not in (place["id"], slot["id"]))
check("...byte for byte in the file", blocks_kept and raw_before != path.read_text(), True)
entries = steps.read(tid)
check("...each at the time it was said",
      [m["at"] for m in after if m.get("late") and m["text"] != "Already posted late."],
      ["2026-11-10T18:37:42Z", "2026-11-10T18:53:40Z"])
check("full-record entries for both recovered replies, by seq",
      [(entries[i]["seq"], entries[i].get("recovered")) for i in (place["id"], "m6")],
      [([1, 4], True), ([5, 8], True)])
check("a second run finds no placeholder left to force",
      backfill.recover(tid, place["id"], dry=False), False)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
