#!/usr/bin/env python3
"""Provider usage on demand (2026-10-14): one reader contract, one refresh
loop, Codex and Claude Code each with a ``read()``.

What is proved, offline -- no CLI is started; each provider's one process
call (``codex.app_server_call``, ``cfg.claude_control_call``) is replaced by
the answer the live login gave on 2026-10-14:

  1. a Codex read replaces a reading that held a stale plan -- the window
     the plan no longer has goes, ``planType`` and ``credits`` move -- and
     is stamped ``source: "read"``
  2. an event merges over a read and keeps what it does not carry, stamped
     ``source: "event"``, for Codex and for Claude Code
  3. extra Codex limits are kept, named by their id, in the window shape
  4. Claude Code's ``get_usage`` maps percent to the row's fraction, names a
     model's weekly limit, and an answer it does not know is a failed read
     that leaves the kept reading alone
  5. a provider without ``read()`` answers None and its row says why
  6. the loop reads on a fetch (once a minute), on the five-minute tick
     while a session is live or a job runs, and once as a job ends -- and
     not otherwise
  7. a broker's ``providers()`` is a fetch: it reads, and serves the read

Run: python3 tests/test_provider_usage_refresh.py
"""

import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
os.environ["LOAM_USAGE_READS"] = "off"  # no real CLI reads; see loam_provider_usage
import loam_broker as cfg  # noqa: E402
# Admission reads no real box here: a fixture session must not be refused
# because the machine running the suite is short of memory (a landing
# got 429 for one, 2026-11-09). test_broker_seats is where admission is tested.
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}
import loam_codex as codex  # noqa: E402
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


# ``account/rateLimits/read`` on the live login, 2026-10-14T19:26:32Z, verbatim
# but for ``accountId``: a ``prolite`` plan whose only window is the week.
CODEX_READ = {
    "ordinaryUsageAllowed": True,
    "rateLimits": {"limitId": "codex", "limitName": None,
                   "normalModelSlug": None,
                   "primary": {"usedPercent": 4, "windowDurationMins": 10080,
                               "resetsAt": 1792592698},
                   "secondary": None,
                   "credits": {"hasCredits": False, "unlimited": False,
                               "balance": "0"},
                   "individualLimit": None, "spendControlReached": False,
                   "planType": "prolite", "rateLimitReachedType": None},
    "rateLimitsByLimitId": {"codex": {
        "limitId": "codex", "limitName": None, "normalModelSlug": None,
        "primary": {"usedPercent": 4, "windowDurationMins": 10080,
                    "resetsAt": 1792592698},
        "secondary": None,
        "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
        "individualLimit": None, "spendControlReached": False,
        "planType": "prolite", "rateLimitReachedType": None}},
    "rateLimitResetCredits": {"availableCount": 0, "credits": []},
    "rateLimitUpsell": None}

# The kept file the same minute: the merge had kept the old plan's five-hour
# window, reset two days before, and the ring drew it because it is higher.
STALE = {"provider": "codex", "seen_at": "2026-10-14T19:24:32Z",
         "session_id": "d235c6a8", "account": "m@x", "status": None,
         "is_using_overage": None,
         "five_hour": {"utilization": 0.8, "resets_at": "2026-10-12T23:14:14Z",
                       "resets_at_epoch": 1791846854, "window_mins": 300},
         "seven_day": {"utilization": 0.04, "resets_at": "2026-10-21T14:24:58Z",
                       "resets_at_epoch": 1792592698, "window_mins": 10080},
         "credits": {"hasCredits": True, "unlimited": False, "balance": "12"},
         "planType": "plus"}

# Claude Code's ``get_usage`` on the live login the same minute, abridged to
# the keys the mapping reads and two of the null ones it must skip.
CLAUDE_READ = {
    "subscription_type": "max", "rate_limits_available": True,
    "rate_limits": {
        "five_hour": {"utilization": 2,
                      "resets_at": "2026-10-15T00:20:00.460493+00:00"},
        "seven_day": {"utilization": 9,
                      "resets_at": "2026-10-21T04:00:00.460525+00:00"},
        "seven_day_opus": None, "seven_day_sonnet": None,
        "extra_usage": {"is_enabled": True, "utilization": 35.958},
        "limits": [
            {"kind": "session", "percent": 2,
             "resets_at": "2026-10-15T00:20:00.460493+00:00", "scope": None},
            {"kind": "weekly_scoped", "group": "weekly", "percent": 16,
             "resets_at": "2026-10-21T04:00:00.460803+00:00",
             "scope": {"model": {"id": None, "display_name": "Fable"},
                       "surface": None}}]}}

CLAUDE_EVENT = {"type": "rate_limit_event", "rate_limit_info": {
    "status": "allowed", "isUsingOverage": False,
    "unifiedWindows": {"five_hour": {"utilization": 0.05,
                                     "resetsAt": 1792023600}}}}


def codex_cases() -> None:
    codex.login_state = lambda: {"connected": True, "account": "m@x"}
    codex.USAGE_PATH.write_text(json.dumps(STALE), encoding="utf-8")
    codex.app_server_call = lambda method, params=None, timeout=0: (
        CODEX_READ if method == "account/rateLimits/read" else None)
    usage = codex.read_rate_limits()
    kept = json.loads(codex.USAGE_PATH.read_text())
    check("a read replaces the stale reading on disk", kept == usage, True)
    check("...the old plan's five-hour window is gone",
          "five_hour" in kept, False)
    check("...the week is the read's, named by its length",
          (kept["seven_day"]["utilization"], kept["seven_day"]["window_mins"]),
          (0.04, 10080))
    check("...planType and credits are the read's, not the kept ones",
          (kept["planType"], kept["credits"]["balance"]), ("prolite", "0"))
    check("...stamped as a read, with a fresh seen_at, on this login",
          (kept["source"], kept["seen_at"] != STALE["seen_at"],
           kept["account"]), ("read", True, "m@x"))

    note = {"rateLimits": {"limitId": "codex",
                           "primary": {"usedPercent": 6,
                                       "windowDurationMins": 10080,
                                       "resetsAt": 1792592698},
                           "secondary": None, "credits": None,
                           "planType": None}}
    merged = codex.keep_rate_limits(note, "s9")
    check("an event after the read moves what it states",
          merged["seven_day"]["utilization"], 0.06)
    check("...keeps what it does not carry, and is stamped as an event",
          (merged["planType"], merged["credits"]["balance"], merged["source"]),
          ("prolite", "0", "event"))

    many = json.loads(json.dumps(CODEX_READ))
    many["rateLimitsByLimitId"]["gpt-6-luna"] = {
        "limitId": "gpt-6-luna", "limitName": "GPT-6 Luna",
        "primary": {"usedPercent": 30, "windowDurationMins": 300,
                    "resetsAt": 1792023600},
        "secondary": {"usedPercent": 12, "windowDurationMins": 10080,
                      "resetsAt": 1792592698},
        "credits": None, "planType": "prolite", "rateLimitReachedType": None}
    usage = codex.usage_from_read(many, "m@x")
    check("an extra limit is kept under buckets by its id",
          sorted(usage["buckets"]), ["gpt-6-luna"])
    check("...in the window shape, with its name and no account fields",
          usage["buckets"]["gpt-6-luna"],
          {"status": None, "limit_name": "GPT-6 Luna",
           "five_hour": {"utilization": 0.3, "resets_at": "2026-10-15T00:20:00Z",
                         "resets_at_epoch": 1792023600, "window_mins": 300},
           "seven_day": {"utilization": 0.12,
                         "resets_at": "2026-10-21T14:24:58Z",
                         "resets_at_epoch": 1792592698, "window_mins": 10080}})
    check("...and the codex limit keeps its place as the row's windows",
          ("seven_day" in usage, "five_hour" in usage), (True, False))

    codex.app_server_call = lambda *a, **k: None
    before = codex.USAGE_PATH.read_text()
    check("a failed read answers None", codex.read_rate_limits(), None)
    check("...and leaves the kept reading alone",
          codex.USAGE_PATH.read_text() == before, True)


def claude_cases() -> None:
    cfg.login_state = lambda: {"connected": True}
    asked = []

    def control(request, timeout=0):
        asked.append(request)
        return CLAUDE_READ
    cfg.claude_control_call = control
    cfg.write_usage({"provider": "claude-code", "source": "event",
                     "five_hour": {"utilization": 0.5},
                     "seven_day_overage_included": {"utilization": 0.16}})
    usage = cfg.read_claude_usage()
    check("get_usage is asked, without the behaviours block",
          asked, [{"subtype": "get_usage", "skip_behaviors": True}])
    check("a read's percents are the row's fractions",
          (usage["five_hour"]["utilization"], usage["seven_day"]["utilization"]),
          (0.02, 0.09))
    check("...resets in both forms, as the event gives them",
          (usage["five_hour"]["resets_at"], usage["five_hour"]["resets_at_epoch"]),
          ("2026-10-15T00:20:00Z", 1792023600))
    check("...a model's weekly limit is named seven_day_<model>",
          usage["seven_day_fable"]["utilization"], 0.16)
    check("...null and non-window entries are not windows",
          sorted(k for k, v in usage.items()
                 if isinstance(v, dict) and "utilization" in v),
          ["five_hour", "seven_day", "seven_day_fable"])
    check("...the read replaced the kept reading whole",
          ("seven_day_overage_included" in cfg.read_usage(),
           cfg.read_usage()["source"], cfg.read_usage()["planType"]),
          (False, "read", "max"))

    merged = cfg.keep_usage_event(CLAUDE_EVENT, "s1")
    check("an event merges over the read: it moves its own window",
          merged["five_hour"]["utilization"], 0.05)
    check("...and keeps the read's that it does not carry",
          (merged["seven_day"]["utilization"], merged["seven_day_fable"]["utilization"],
           merged["source"], merged["status"]), (0.09, 0.16, "event", "allowed"))

    cfg.claude_control_call = lambda request, timeout=0: {"changed": "shape"}
    before = cfg.read_usage()
    check("an answer the mapping does not know is a failed read",
          cfg.read_claude_usage(), None)
    check("...which leaves the kept reading alone", cfg.read_usage(), before)


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def loop_cases(jobs_root: Path) -> None:
    os.environ.pop(usage_store.OFF_ENV, None)
    reads: list[str] = []

    def reader(name, readable=True):
        return usage_store.Reader(
            name, observe=lambda e: None, kept=lambda: None,
            read=(lambda: reads.append(name) or {"source": "read"})
            if readable else None)

    quiet = usage_store.Reader("quiet", observe=lambda e: None, kept=lambda: None)
    check("a provider without read() answers None",
          usage_store.Refresh([quiet]).read("quiet"), None)
    check("...and its reason says it is observe-only",
          usage_store.Refresh([quiet]).reason("quiet"),
          "observe-only: quiet states its usage on a turn and has no on-demand read")
    check("...where a provider with one has no reason",
          usage_store.Refresh([reader("a")]).reason("a"), None)

    clock = Clock()
    live: set[str] = set()
    refresh = usage_store.Refresh(
        [reader("a"), reader("b"), quiet], live=lambda: live,
        jobs=lambda: usage_store.running_jobs([jobs_root]), clock=clock)
    check("a fetch reads every provider that can be read",
          refresh.on_fetch(), ["a", "b"])
    clock.t += 59
    check("...a second fetch inside the minute reads nothing",
          refresh.on_fetch(), [])
    clock.t += 1
    check("...and one a minute on reads again", refresh.on_fetch(), ["a", "b"])
    refresh.last_thread.join(5)
    check("...behind the fetch, on a thread, not in it",
          (refresh.last_thread.name, sorted(reads[-2:])), ("provider-usage-read", ["a", "b"]))

    # A read that takes seconds does not hold the fetch (2026-11-09: the
    # first app open of every minute waited 2.3 s for its rows).
    gate = threading.Event()
    slow = usage_store.Refresh([usage_store.Reader(
        "s", observe=lambda e: None, kept=lambda: None,
        read=lambda: gate.wait(5) and {"source": "read"})], clock=clock)
    slow.ever_read.add("s")            # read once already, as after the first fetch
    started = time.monotonic()
    slow.on_fetch()
    check("a slow read does not hold a fetch", time.monotonic() - started < 1, True)
    gate.set()
    slow.last_thread.join(5)

    reads.clear()
    for _ in range(12):           # an hour of ticks on an idle box
        clock.t += usage_store.POLL_S * 10
        refresh.tick()
    check("with nothing live and no job, the tick never reads", reads, [])

    live.add("a")
    clock.t += 1
    refresh.last_read["a"] = clock.t
    check("a live session, read under five minutes ago: no read",
          refresh.tick(), [])
    clock.t += usage_store.LIVE_EVERY_S - 1
    check("...still not at 4:59", refresh.tick(), [])
    clock.t += 1
    check("...and at five minutes the live provider alone is read",
          refresh.tick(), ["a"])
    live.clear()

    job = jobs_root / "20261014-000000-abcd"
    job.mkdir()
    (job / "runtime").write_text("codex\n", encoding="utf-8")
    (job / "heartbeat").write_text("tick\n", encoding="utf-8")
    refresh = usage_store.Refresh(
        [reader("claude-code"), reader("codex")],
        jobs=lambda: usage_store.running_jobs([jobs_root]), clock=clock)
    check("a running job is found, by the provider its runtime spends",
          usage_store.running_jobs([jobs_root]), {job.name: "codex"})
    check("...the tick reads that provider while it runs",
          refresh.tick(), ["codex"])
    clock.t += 60
    check("...and not again inside five minutes", refresh.tick(), [])
    (job / "result.json").write_text("{}", encoding="utf-8")
    clock.t += 1
    check("the tick after the job ends reads its provider at once",
          refresh.tick(), ["codex"])
    clock.t += 1
    check("...once", refresh.tick(), [])
    old = jobs_root / "20261013-000000-dead"
    old.mkdir()
    (old / "runtime").write_text("claude\n", encoding="utf-8")
    (old / "heartbeat").write_text("tick\n", encoding="utf-8")
    stale = time.time() - usage_store.JOB_STALE_S - 1
    os.utime(old / "heartbeat", (stale, stale))
    check("a job whose heartbeat stopped is not running",
          usage_store.running_jobs([jobs_root]), {})

    reads.clear()
    refresh = usage_store.Refresh([reader("a")], live=lambda: {"a"}, clock=clock)
    os.environ[usage_store.OFF_ENV] = "off"
    check("with the switch off, neither a fetch nor a tick reads",
          (refresh.on_fetch(), refresh.tick(), reads), ([], [], []))


def broker_cases() -> None:
    os.environ.pop(usage_store.OFF_ENV, None)
    server = broker_mod.Server(broker_mod.Broker())
    reads = []
    stamp = {"provider": "codex", "account": "m@x", "source": "read",
             "seen_at": "2026-10-14T20:00:00Z"}

    def read_codex():
        reads.append("codex")
        usage_store.write_json(codex.USAGE_PATH, stamp)
        return stamp
    server.broker.usage.readers["codex"].read = read_codex
    server.broker.usage.readers["claude-code"].read = None
    cfg.login_state = lambda: {"connected": True, "reason": None,
                               "expires_at": None, "days_left": None,
                               "source": None}
    codex.login_state = lambda: {"connected": True, "reason": None,
                                 "account": "m@x", "plan": None,
                                 "expires_at": None, "days_left": None,
                                 "source": None}
    codex.status_line = lambda: None
    rows = {r["id"]: r for r in server.providers()}
    check("a providers() fetch reads, and serves the read",
          (reads, rows["codex"]["usage"]["source"]), (["codex"], "read"))
    server.providers()
    check("...a second fetch the same minute does not read again",
          reads, ["codex"])
    check("a wired provider without read() says so on its row",
          rows["claude-code"]["usage_reason"],
          "observe-only: claude-code states its usage on a turn and has no "
          "on-demand read")
    os.environ[usage_store.OFF_ENV] = "off"


root = Path(tempfile.mkdtemp(prefix="loam-provider-usage-test-"))
saved = {name: getattr(cfg, name) for name in
         ("STATE_DIR", "SESSION_DIR", "TOKEN_PATH", "USAGE_PATH", "AI_LABS",
          "login_state", "claude_control_call")}
saved_codex = {name: getattr(codex, name) for name in
               ("CODEX_HOME", "AUTH_PATH", "MODELS_PATH", "USAGE_PATH",
                "login_state", "app_server_call", "status_line")}
try:
    cfg.STATE_DIR = root / "state"
    cfg.SESSION_DIR = cfg.STATE_DIR / "sessions"
    cfg.TOKEN_PATH = cfg.STATE_DIR / "token"
    cfg.USAGE_PATH = cfg.STATE_DIR / "provider-usage.json"
    cfg.AI_LABS = root / "agents"
    codex.CODEX_HOME = cfg.STATE_DIR / "codex"
    codex.AUTH_PATH = codex.CODEX_HOME / "auth.json"
    codex.MODELS_PATH = cfg.STATE_DIR / "codex-models.json"
    codex.USAGE_PATH = cfg.STATE_DIR / "codex-usage.json"
    codex.CODEX_HOME.mkdir(parents=True)
    (cfg.AI_LABS / "probe").mkdir(parents=True)
    (cfg.AI_LABS / "probe" / "agent.json").write_text("{}", encoding="utf-8")
    (root / "jobs").mkdir()
    codex_cases()
    claude_cases()
    loop_cases(root / "jobs")
    broker_cases()
finally:
    for name, value in saved.items():
        setattr(cfg, name, value)
    for name, value in saved_codex.items():
        setattr(codex, name, value)
    shutil.rmtree(root, ignore_errors=True)

print(f"\nFAILURES: {results.count(False)}")
sys.exit(0 if all(results) else 1)
