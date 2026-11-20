#!/usr/bin/env python3
"""Read and cache provider quota and credits on demand, using recorded CLI output.

Run: python3 tests/test_antigravity_usage.py"""

import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
os.environ["LOAM_USAGE_READS"] = "off"  # no real CLI reads; see loam_provider_usage
import loam_broker as cfg  # noqa: E402
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}
import loam_antigravity as agy  # noqa: E402
import loam_provider_usage as usage_store  # noqa: E402

loader = importlib.machinery.SourceFileLoader(
    "session_broker", str(AGENTS / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker_mod = importlib.util.module_from_spec(spec)
loader.exec_module(broker_mod)

results = []


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:64} got={got!r}"[:300])
    if not ok:
        print(f"      wanted {want!r}")


DATA = {"description": "Within each group, models share a weekly limit and a 5-hour limit.",
        "groups": [
            {"name": "Gemini Models", "description": "Models within this group: ...",
             "buckets": [
                 {"id": "gemini-weekly", "name": "Weekly Limit Remaining",
                  "window": "weekly", "remaining_fraction": 0.994755744934082,
                  "reset_time": "2026-11-23T00:36:17Z"},
                 {"id": "gemini-5h", "name": "Five Hour Limit Remaining",
                  "window": "5h", "remaining_fraction": 1,
                  "reset_time": "2026-11-16T10:36:17Z"}]},
            {"name": "Claude and GPT models", "description": "Models within this group: ...",
             "buckets": [
                 {"id": "3p-weekly", "name": "Weekly Limit Remaining",
                  "window": "weekly", "remaining_fraction": 0.9870348572731018,
                  "reset_time": "2026-11-23T00:36:17Z"},
                 {"id": "3p-5h", "name": "Five Hour Limit Remaining",
                  "window": "5h", "remaining_fraction": 1,
                  "reset_time": "2026-11-16T10:36:17Z"}]}]}
RESPONSE = ("Gemini Models\tWeekly Limit Remaining\t99%\t2026-11-23T00:36:17Z\n"
            "Gemini Models\tFive Hour Limit Remaining\t100%\t2026-11-16T10:36:17Z\n"
            "Claude and GPT models\tWeekly Limit Remaining\t99%\t2026-11-23T00:36:17Z\n"
            "Claude and GPT models\tFive Hour Limit Remaining\t100%\t2026-11-16T10:36:17Z\n")
ZERO = {"input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0,
        "cache_read_tokens": 0, "total_tokens": 0}


def answer(name: str, data, response: str, turns: int = 0) -> str:
    cmd = {"name": name, "data": data}
    lines = []
    if data is not None:
        lines.append({"event": "command_result", "command": cmd})
    lines.append({"event": "result", "result": {
        "conversation_id": "", "status": "SUCCESS", "response": response,
        "duration_seconds": 0, "num_turns": turns, "usage": ZERO,
        **({"command": cmd} if data is not None else {})}})
    return "\n".join(json.dumps(x) for x in lines) + "\n"


QUOTA = answer("usage", DATA, RESPONSE)
CREDITS_0 = answer("credits", {"remaining_credits": 0,
                               "upgrade_uri": "https://antigravity.google/g1-upgrade"},
                   "Remaining credits\t0\nUpgrade\thttps://antigravity.google/g1-upgrade\n")

# 1. the structured answer
u = agy.usage_from_read(QUOTA, CREDITS_0, "me@example.com")
b = u["buckets"]
check("every bucket kept, pool by pool, shorter window first",
      list(b), ["gemini-5h", "gemini-weekly", "3p-5h", "3p-weekly"])
check("utilization is USED, a fraction: 1 - remaining",
      b["gemini-weekly"]["utilization"], round(1 - 0.994755744934082, 6))
check("a full window is 0 used", b["3p-5h"]["utilization"], 0.0)
check("remaining kept beside it as Antigravity said it",
      b["3p-weekly"]["remaining"], 0.9870348572731018)
check("labelled <pool> · <window>",
      [b[k]["limit_name"] for k in b],
      ["Gemini Models · 5-hour", "Gemini Models · Weekly",
       "Claude and GPT models · 5-hour", "Claude and GPT models · Weekly"])
check("window length in minutes", (b["gemini-5h"]["window_mins"],
                                   b["gemini-weekly"]["window_mins"]), (300, 10080))
check("reset as ISO and epoch",
      (b["gemini-5h"]["resets_at"], b["gemini-5h"]["resets_at_epoch"]),
      ("2026-11-16T10:36:17Z", 1794825377))
check("stamped like the other rows",
      {k: u[k] for k in ("provider", "source", "account", "session_id")},
      {"provider": "antigravity", "source": "read",
       "account": "me@example.com", "session_id": None})
check("no top-level window the page would draw unlabelled",
      [k for k, v in u.items() if isinstance(v, dict) and "utilization" in v], [])

# 2. the printed fallback, unknowns, garbage
fallback = answer("usage", None, RESPONSE + "Something new\tthat we do not know\n")
f = agy.usage_from_read(fallback, None, None)
check("printed form read when the data is absent",
      list(f["buckets"]), ["gemini-models-5h", "gemini-models-weekly",
                           "claude-and-gpt-models-5h", "claude-and-gpt-models-weekly"])
check("printed percent is remaining, served as used",
      f["buckets"]["gemini-models-weekly"]["utilization"], 0.01)
check("an unknown line is kept, not dropped",
      f["unparsed"], ["Something new\tthat we do not know"])
check("an unknown window in the data is kept, length unknown",
      agy.buckets_from_data({"groups": [{"name": "New pool", "buckets": [
          {"id": "np-month", "name": "Monthly", "window": "monthly",
           "remaining_fraction": 0.5, "reset_time": None}]}]})["np-month"]
      ["utilization"], 0.5)
for label, junk in (("empty", ""), ("not json", "hello\nworld"),
                    ("json not an object", "[1,2]\n3\n"),
                    ("groups not a list", answer("usage", {"groups": 7}, "")),
                    ("bucket junk", answer("usage", {"groups": [{"name": 1, "buckets": [5, None, {}]}]}, "\t\t\t\n"))):
    try:
        got = agy.usage_from_read(junk)
        got = got if got is None else sorted(got["buckets"])
    except Exception as exc:  # noqa: BLE001
        got = f"raised {exc!r}"
    check(f"garbage ({label}) does not raise", isinstance(got, str), False)
check("an answer naming no bucket is None",
      agy.usage_from_read(answer("usage", {"groups": []}, "")), None)
check("a turn that ran is no quota answer",
      agy.usage_from_read(answer("usage", DATA, RESPONSE, turns=1)), None)

# 3. credits
check("0 credits: hasCredits false, so the page draws no line",
      u["credits"], {"hasCredits": False, "unlimited": False, "balance": "0",
                     "upgrade_uri": "https://antigravity.google/g1-upgrade"})
check("credits left: hasCredits true with the balance",
      agy.credits_from({"remaining_credits": 250}),
      {"hasCredits": True, "unlimited": False, "balance": "250"})
check("credits from the printed form",
      agy.credits_from(None, "Remaining credits\t12\n")["balance"], "12")
check("unread credits are null", agy.usage_from_read(QUOTA, "")["credits"], None)

# 4. read_quota and the file
tmp = Path(tempfile.mkdtemp(prefix="agy-usage-"))
agy.USAGE_FILE = tmp / "usage.json"
calls = []
answers = {"/quota": QUOTA, "/credits": CREDITS_0}
agy._run = lambda argv: calls.append(argv[1]) or answers.get(argv[1])
login = {"connected": True, "account": "me@example.com"}
agy.login_state = lambda fresh=False: dict(login)
got = agy.read_quota()
check("a read asks /quota then /credits", calls, ["/quota", "/credits"])
check("...and keeps the reading on disk",
      json.loads(agy.USAGE_FILE.read_text())["buckets"]["3p-weekly"]["utilization"],
      got["buckets"]["3p-weekly"]["utilization"])
kept = agy.USAGE_FILE.read_text()
answers["/quota"] = None
check("a failed /quota is a failed read", agy.read_quota(), None)
check("...and leaves the kept reading alone", agy.USAGE_FILE.read_text(), kept)
answers.update({"/quota": QUOTA, "/credits": None})
check("a failed /credits alone: a reading, credits null",
      agy.read_quota()["credits"], None)
login["connected"] = False
calls.clear()
check("no login: no read", (agy.read_quota(), calls), (None, []))

# 5. the row
login.update(connected=True, account="me@example.com")
agy.login_state = lambda fresh=False: {**login, "reason": None, "source": "google-account",
                                       "plan": None, "expires_at": None,
                                       "days_left": None, "models": []}
row = broker_mod.Server.antigravity_row()
check("the row serves the kept reading", sorted(row["usage"]["buckets"]),
      ["3p-5h", "3p-weekly", "gemini-5h", "gemini-weekly"])
check("...with no usage_reason", row["usage_reason"], None)
login["account"] = "someone-else@example.com"
row = broker_mod.Server.antigravity_row()
check("a reading taken on another login is not served", row["usage"], None)
check("...and the row says why", row["usage_reason"],
      "no reading yet: /quota has not answered on this login")
agy.USAGE_FILE.unlink()
login["account"] = "me@example.com"
check("no reading at all: null", broker_mod.Server.antigravity_row()["usage"], None)

# 6. the loop
refresh = usage_store.Refresh([agy.reader()])
check("the reader has a read, so no observe-only reason",
      refresh.reason("antigravity"), None)
check("a headless turn's event is not a reading", agy.reader().observe({}), None)
jobs = tmp / "jobs"
(jobs / "j1").mkdir(parents=True)
(jobs / "j1" / "heartbeat").write_text("")
(jobs / "j1" / "runtime").write_text("antigravity\n")
check("an antigravity job spends this meter",
      usage_store.running_jobs([jobs]), {"j1": "antigravity"})


def slow(name):
    def read():
        time.sleep(0.4)
        return {"provider": name}
    return usage_store.Reader(name, observe=lambda e: None, kept=lambda: None, read=read)


os.environ.pop("LOAM_USAGE_READS")
try:
    r = usage_store.Refresh([slow("a"), slow("b"), slow("c")])
    t0 = time.monotonic()
    done = r.on_fetch()
    took = time.monotonic() - t0
finally:
    os.environ["LOAM_USAGE_READS"] = "off"
check("a fresh broker's first reads all happen in the fetch", sorted(done), ["a", "b", "c"])
check("...side by side: three 0.4 s reads take well under 1.2 s", took < 0.9, True)
check("...and no reader thread is left running",
      [t.name for t in threading.enumerate() if t.name.startswith("provider-usage-first")], [])

print()
print(f"FAILURES: {results.count(False)}")
sys.exit(1 if False in results else 0)
