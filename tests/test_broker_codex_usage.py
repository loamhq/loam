#!/usr/bin/env python3
"""Persist provider rate-limit notifications and serve both quota windows on the provider row."""

import asyncio
import base64
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
os.environ["LOAM_USAGE_READS"] = "off"  # no real CLI reads; see loam_provider_usage
import loam_broker as cfg  # noqa: E402
# Use fixture memory to isolate the broker from machine load.
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}
import loam_codex as codex  # noqa: E402

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


# Use the provider rate-limit schema for both windows.
NOTE = {"rateLimits": {
    "limitId": "codex", "limitName": None,
    "primary": {"usedPercent": 2, "windowDurationMins": 300,
                "resetsAt": 1790894969},
    "secondary": {"usedPercent": 0, "windowDurationMins": 10080,
                  "resetsAt": 1791481769},
    "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
    "planType": "plus", "rateLimitReachedType": None}}


def sign_in(email: str) -> None:
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    claims = {"email": email,
              "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"}}
    codex.AUTH_PATH.write_text(json.dumps({"tokens": {
        "access_token": "a", "refresh_token": "r",
        "id_token": f"{part({})}.{part(claims)}.sig"}}), encoding="utf-8")


def mapping_cases() -> None:
    usage = codex.usage_from_rate_limits(NOTE, None, "s1", "m@x")
    check("the 5-hour window is a fraction with both reset forms",
          usage["five_hour"], {"utilization": 0.02,
                               "resets_at": "2026-10-01T22:49:29Z",
                               "resets_at_epoch": 1790894969,
                               "window_mins": 300})
    check("the weekly window is seven_day",
          (usage["seven_day"]["utilization"], usage["seven_day"]["window_mins"]),
          (0.0, 10080))
    check("credits and planType are beside the windows, verbatim",
          (usage["credits"], usage["planType"]),
          ({"hasCredits": False, "unlimited": False, "balance": "0"}, "plus"))
    check("...and the Claude row's scalars are there",
          {k: usage[k] for k in ("provider", "session_id", "status",
                                 "is_using_overage")},
          {"provider": "codex", "session_id": "s1", "status": None,
           "is_using_overage": None})
    sparse = {"rateLimits": {"limitId": "codex",
                             "primary": {"usedPercent": 40,
                                         "windowDurationMins": 300,
                                         "resetsAt": 1790894969},
                             "secondary": None, "credits": None,
                             "planType": None}}
    merged = codex.usage_from_rate_limits(sparse, usage, "s2", "m@x")
    check("a sparse update moves what it states and keeps the rest",
          (merged["five_hour"]["utilization"], merged["seven_day"]["utilization"],
           merged["planType"], merged["credits"]["hasCredits"]),
          (0.4, 0.0, "plus", False))
    check("...but not across logins",
          "seven_day" in codex.usage_from_rate_limits(sparse, usage, "s2", "o@x"),
          False)
    other = {"rateLimits": {**NOTE["rateLimits"], "limitId": "gpt-6-luna"}}
    kept = codex.usage_from_rate_limits(other, usage, "s3", "m@x")
    check("a reading for another limitId moves its bucket, not the row",
          (kept["five_hour"]["utilization"],
           kept["buckets"]["gpt-6-luna"]["five_hour"]["utilization"],
           "planType" in kept["buckets"]["gpt-6-luna"]),
          (0.02, 0.02, False))
    odd = {"rateLimits": {"primary": {"usedPercent": 5,
                                      "windowDurationMins": 1440}}}
    check("a window of another length is named by it, not drawn as 5-hour",
          [k for k in codex.usage_from_rate_limits(odd) if k.startswith("window")],
          ["window_1440_min"])


async def broker_cases() -> None:
    sign_in("owner@example.com")
    row = broker_mod.Server.codex_row()
    check("before any reading, usage is null and says why",
          (row["usage"], row["usage_reason"]),
          (None, "no reading yet: the read failed and no Codex turn has run"))

    session = broker_mod.CodexSession("probe")
    emitted: list[dict] = []

    async def emit(event):
        emitted.append(event)
    session.emit = emit
    cfg.write_usage({"provider": "claude-code", "five_hour": {"utilization": 0.5}})
    await session._handle({"jsonrpc": "2.0",
                           "method": "account/rateLimits/updated",
                           "params": NOTE})
    check("a session's notification is kept on disk",
          json.loads(codex.USAGE_PATH.read_text())["five_hour"]["utilization"],
          0.02)
    check("...and put on the chat's stream as a rate_limit_event",
          [(e["type"], e["rate_limit_info"]["unifiedWindows"]["five_hour"]["resetsAt"],
            e["rate_limit_info"]["unifiedWindows"]["five_hour"]["utilization"])
           for e in emitted],
          [("rate_limit_event", 1790894969, 0.02)])
    check("...which leaves Claude's reading alone",
          cfg.read_usage(), {"provider": "claude-code",
                             "five_hour": {"utilization": 0.5}})

    # A new broker object, as after a restart: nothing held in memory.
    server = broker_mod.Server(broker_mod.Broker())
    rows = {r["id"]: r for r in server.providers()}
    check("a re-created broker serves the kept reading on the codex row",
          (rows["codex"]["usage"]["five_hour"]["utilization"],
           rows["codex"]["usage"]["seven_day"]["resets_at"],
           rows["codex"]["usage"]["planType"], rows["codex"]["usage_reason"]),
          (0.02, "2026-10-08T17:49:29Z", "plus", None))
    check("...and the Claude row still serves its own",
          rows["claude-code"]["usage"]["provider"], "claude-code")

    sign_in("someone-else@example.com")
    check("a reading taken on another login is not served",
          broker_mod.Server.codex_row()["usage"], None)


def window_cases() -> None:
    """Read context windows from the catalog entry for the model actually used."""
    codex.write_models([{"id": i, "displayName": i} for i in
                         ("gpt-6-astra", "gpt-6-luna", "gpt-new")])
    row = broker_mod.Server.codex_row()
    check("with no catalogue, no model claims a window",
          [m["context_window"] for m in row["models"]], [None, None, None])
    (codex.CODEX_HOME / codex.CATALOGUE_NAME).write_text(json.dumps({
        "fetched_at": "2026-10-08T00:25:32Z", "models": [
            {"slug": "gpt-6-astra", "context_window": 272000,
             "max_context_window": 872000,
             "effective_context_window_percent": 95},
            {"slug": "gpt-6-luna", "context_window": 128000},
            {"slug": "gpt-odd", "context_window": "big"}]}), encoding="utf-8")
    row = broker_mod.Server.codex_row()
    check("each listed model carries its catalogue window, scaled as a turn states it",
          {m["id"]: m["context_window"] for m in row["models"]},
          {"gpt-6-astra": 258400, "gpt-6-luna": 128000, "gpt-new": None})
    (codex.CODEX_HOME / codex.CATALOGUE_NAME).write_text("{not json")
    check("an unreadable catalogue is no window, not an error",
          codex.context_windows(), {})


root = Path(tempfile.mkdtemp(prefix="loam-broker-codex-usage-test-"))
saved = {name: getattr(cfg, name) for name in
         ("STATE_DIR", "SESSION_DIR", "TOKEN_PATH", "USAGE_PATH", "AI_LABS")}
saved_codex = {name: getattr(codex, name) for name in
               ("CODEX_HOME", "AUTH_PATH", "MODELS_PATH", "USAGE_PATH")}
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
    mapping_cases()
    asyncio.run(broker_cases())
    window_cases()
finally:
    for name, value in saved.items():
        setattr(cfg, name, value)
    for name, value in saved_codex.items():
        setattr(codex, name, value)
    shutil.rmtree(root, ignore_errors=True)

print(f"\nFAILURES: {results.count(False)}")
sys.exit(0 if all(results) else 1)
