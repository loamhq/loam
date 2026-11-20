#!/usr/bin/env python3
"""The wake line carries the run's usage and denials; usage-by-lane reads them.

Five groups, against the shipped code:

  envelope       lib/loam_usage.from_envelope on a real 2.1.281 envelope
                 (below, trimmed), and on an older envelope with none of the
                 fields.
  wake line      bin/wake end to end with only the CLI and the identity
                 preflight stubbed: the fields land on the jsonl line under
                 the envelope's own names; an older envelope leaves a valid
                 line without them.
  denials        one denial: a failure-log entry naming the lane and the
                 tool and not its arguments, and nobody told. None:
                 no entry at all.
  repeat         a denied vault Write on three consecutive nights is one
                 fact for the system persona (lib/loam_attention.py), and
                 nothing on the board; on two it is nothing.
  table          bin/usage-by-lane on a fixture of three lanes: one with
                 usage, one with none (older runs), one mixed.

Run: python3 tests/test_wake_usage.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("LOAM_ATTENTION_DIR", tempfile.mkdtemp(prefix="loam-wake-usage-attention-"))
os.environ.setdefault("LOAM_WAITERS_DIR", os.environ["LOAM_ATTENTION_DIR"] + "/waiters")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_identity  # noqa: E402
import loam_usage  # noqa: E402

AGENT = "wakeusagetest"
REAL_RUN = subprocess.run
results: list[bool] = []

# Use the provider's result envelope, including cache writes and reads, to calculate
# totals.
REAL_ENVELOPE = {
    "type": "result", "subtype": "success", "is_error": False,
    "num_turns": 4, "terminal_reason": "completed",
    "session_id": "94653ae4-dc53-48cb-b00b-25c542cbefac",
    "total_cost_usd": 0.40364475,
    "usage": {
        "input_tokens": 66, "cache_creation_input_tokens": 18376,
        "cache_read_input_tokens": 35659, "output_tokens": 531,
        "output_tokens_details": {"thinking_tokens": 168},
        "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
        "service_tier": "standard",
        "cache_creation": {"ephemeral_1h_input_tokens": 18376,
                           "ephemeral_5m_input_tokens": 0},
        "inference_geo": "not_available", "speed": "standard",
    },
    "modelUsage": {
        "claude-fable-5-1": {
            "inputTokens": 66, "outputTokens": 531,
            "cacheReadInputTokens": 35659, "cacheCreationInputTokens": 18376,
            "webSearchRequests": 0, "costUSD": 0.40364475,
            "contextWindow": 1000000, "maxOutputTokens": 64000,
            "thinkingTokens": 168, "canonicalModel": "claude-fable-5-1",
            "provider": "firstParty", "costBasis": "list",
        },
    },
    "permission_denials": [
        {"tool_name": "Read", "tool_use_id": "toolu_011eFTC4EGKNuNcgPDpLMwx3",
         "tool_input": {"file_path": "/etc/hostname"}},
        {"tool_name": "Bash", "tool_use_id": "toolu_015YNqDGCnpFPCrLgr5hm9Fc",
         "tool_input": {"command": "curl -s https://example.com | head -1",
                        "description": "Fetch first line of example.com"}},
        {"tool_name": "Read", "tool_use_id": "toolu_01Sx7xyrt7KpGAmAGtDi6hVJ",
         "tool_input": {"file_path": "/etc/os-release"}},
    ],
    "result": "done",
}

# What a wake got before the CLI grew these fields: the shape the existing
# tests have always stubbed.
OLD_ENVELOPE = {"type": "result", "subtype": "success", "is_error": False,
                "num_turns": 3, "total_cost_usd": 1.25, "result": "ok"}


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok and detail:
        print(f"      {detail[:600]}")


# --- envelope ---------------------------------------------------------------

def test_envelope_parse_real() -> None:
    got = loam_usage.from_envelope(REAL_ENVELOPE)
    check("envelope: usage counts under the envelope's names",
          got.get("usage") == {
              "input_tokens": 66, "output_tokens": 531,
              "cache_read_input_tokens": 35659,
              "cache_creation_input_tokens": 18376,
              "cache_creation": {"ephemeral_1h_input_tokens": 18376,
                                 "ephemeral_5m_input_tokens": 0}}, str(got))
    check("envelope: modelUsage per model, counts and costUSD only",
          got.get("modelUsage") == {"claude-fable-5-1": {
              "inputTokens": 66, "outputTokens": 531,
              "cacheReadInputTokens": 35659,
              "cacheCreationInputTokens": 18376, "costUSD": 0.40364475}},
          str(got.get("modelUsage")))
    check("envelope: permission_denials as the CLI gave them",
          got.get("permission_denials") == REAL_ENVELOPE["permission_denials"])
    check("envelope: denied names, commonest first, no arguments",
          loam_usage.denied_names(got["permission_denials"]) == "Read ×2, Bash")


def test_envelope_parse_old() -> None:
    check("envelope: older CLI yields no fields at all",
          loam_usage.from_envelope(OLD_ENVELOPE) == {})
    check("envelope: malformed fields are absent, not an exception",
          loam_usage.from_envelope({"usage": "x", "modelUsage": [1],
                                    "permission_denials": None}) == {})


def test_envelope_clips_long_input() -> None:
    body = "x" * 5000
    got = loam_usage.from_envelope({"permission_denials": [
        {"tool_name": "Write", "tool_use_id": "t",
         "tool_input": {"file_path": "/v/a.md", "content": body}}]})
    d = got["permission_denials"][0]
    check("envelope: a denied Write's content is clipped, its path kept",
          d["tool_input"]["file_path"] == "/v/a.md"
          and len(d["tool_input"]["content"]) < 600
          and "5,000 chars" in d["tool_input"]["content"], str(d))


# --- the wake, end to end ---------------------------------------------------

def load_wake():
    loader = importlib.machinery.SourceFileLoader(
        "wake_under_test", str(Path(__file__).resolve().parent.parent / "bin" / "wake"))
    spec = importlib.util.spec_from_loader("wake_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Fake:
    returncode = 0
    stderr = ""

    def __init__(self, envelope: dict):
        self.stdout = json.dumps(envelope)


def run_wake(root: Path, envelope: dict, started: str,
             prior: list[dict] | None = None) -> tuple[int, dict, str, str]:
    """One reflection wake in a temp tree. (exit, jsonl line, log, comms)."""
    ws = root / "ai-labs" / AGENT
    vault = root / "vault"
    ws.mkdir(parents=True)
    (vault / "00_INBOX" / "from-agents").mkdir(parents=True)
    (ws / "agent.json").write_text(json.dumps({
        "name": AGENT, "workspace": str(ws), "vault": str(vault)}))
    (ws / "map.md").write_text("reflect\n")
    wake = load_wake()
    wake.LOG_DIR = root / "logs"
    wake.STATE_DIR = root / "state"
    wake.LOG_DIR.mkdir()
    log = wake.LOG_DIR / f"wake-{AGENT}.jsonl"
    if prior:
        log.write_text("".join(json.dumps(r) + "\n" for r in prior))
    loam_identity.preflight_identity = lambda project, model=None: ("c", "o")

    def fake(argv, **kwargs):
        if argv and str(argv[0]).endswith("/claude"):
            return Fake(envelope)
        return REAL_RUN(argv, **kwargs)
    wake.subprocess.run = fake
    wake.loam_session.round_trips = lambda sid: None
    code = wake.run(ws, AGENT, vault, started)
    row = json.loads(log.read_text().splitlines()[-1])
    box = vault / "00_INBOX" / "from-agents"
    text = [(box / n).read_text() if (box / n).exists() else ""
            for n in ("loam-wake-failures.md", "comms.md")]
    # `comms`: the board, which must stay empty, and then each fact the
    # system persona was told, as `FACT **agent** text` lines.
    attention = Path(os.environ["LOAM_ATTENTION_DIR"])
    told = ""
    for p in sorted(attention.glob("*.json")):
        if p.name.startswith("2"):
            told += f"FACT **{AGENT}** {json.loads(p.read_text())['fact']}\n"
        p.unlink()
    return code, row, text[0], text[1] + told


def in_temp(fn):
    root = Path(tempfile.mkdtemp(prefix="loam-wake-usage-"))
    try:
        fn(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_wake_line_real(root: Path) -> None:
    code, row, log, comms = run_wake(root, REAL_ENVELOPE, "2026-09-30T09:00:00Z")
    check("wake line: exit 0 and ok", code == 0 and row["ok"] is True)
    check("wake line: usage, modelUsage, permission_denials on the line",
          row.get("usage", {}).get("cache_read_input_tokens") == 35659
          and "claude-fable-5-1" in row.get("modelUsage", {})
          and len(row.get("permission_denials", [])) == 3, json.dumps(row))
    check("wake line: total_cost_usd is the envelope's",
          row.get("total_cost_usd") == 0.40364475)


def test_wake_line_old(root: Path) -> None:
    code, row, log, comms = run_wake(root, OLD_ENVELOPE, "2026-09-30T09:00:00Z")
    check("wake line (older CLI): exit 0, the line valid and complete",
          code == 0 and row["ok"] is True and row["num_turns"] == 3
          and row["total_cost_usd"] == 1.25, json.dumps(row))
    check("wake line (older CLI): no usage fields invented",
          not {"usage", "modelUsage", "permission_denials"} & row.keys())
    check("wake line (older CLI): nothing in the failure log", log == "")


# --- denials ----------------------------------------------------------------

def test_denial_one(root: Path) -> None:
    env = {**OLD_ENVELOPE, "permission_denials": [
        {"tool_name": "Bash", "tool_use_id": "t1",
         "tool_input": {"command": "rm -rf /secret-argument"}}]}
    code, row, log, comms = run_wake(root, env, "2026-09-30T09:00:00Z")
    check("denial (one): the run is still a success", code == 0 and row["ok"])
    check("denial (one): failure log names the lane and the tool",
          f"**{AGENT}**" in log and "reflection was refused 1 tool call: Bash"
          in log, log)
    check("denial (one): the arguments stay out of the log",
          "secret-argument" not in log)
    check("denial (one): nobody is told, nothing on the board", f"**{AGENT}**" not in comms)


def test_denial_none(root: Path) -> None:
    env = {**OLD_ENVELOPE, "permission_denials": []}
    code, row, log, comms = run_wake(root, env, "2026-09-30T09:00:00Z")
    check("denial (none): recorded as an empty list",
          row.get("permission_denials") == [])
    check("denial (none): no failure-log entry, nobody told",
          log == "" and f"**{AGENT}**" not in comms)


def vault_write(root: Path) -> dict:
    return {"tool_name": "Write", "tool_use_id": "w",
            "tool_input": {"file_path": str(root / "vault" / "20_X" / "n.md"),
                           "content": "draft"}}


def prior_night(root: Path, day: str) -> dict:
    return {"ts": f"{day}T09:00:00Z", "ok": True, "lane": "reflection",
            "permission_denials": [vault_write(root)]}


def test_repeat_three_nights(root: Path) -> None:
    env = {**OLD_ENVELOPE, "permission_denials": [vault_write(root)]}
    prior = [prior_night(root, "2026-09-28"), prior_night(root, "2026-09-29")]
    code, row, log, comms = run_wake(root, env, "2026-09-30T09:00:00Z", prior)
    facts = [l for l in comms.splitlines() if l.startswith("FACT ")]
    check("repeat (3 nights): one fact for the system persona",
          len(facts) == 1 and "three nights running" in facts[0], comms)
    check("repeat (3 nights): nothing on the board",
          all(l.startswith("FACT ") for l in comms.splitlines() if l.strip()), comms)


def test_repeat_two_nights(root: Path) -> None:
    env = {**OLD_ENVELOPE, "permission_denials": [vault_write(root)]}
    prior = [prior_night(root, "2026-09-27"), prior_night(root, "2026-09-29")]
    code, row, log, comms = run_wake(root, env, "2026-09-30T09:00:00Z", prior)
    check("repeat (a gap in the nights): log entry, nobody told",
          "Write" in log and f"**{AGENT}**" not in comms, comms)


# --- the table --------------------------------------------------------------

def load_usage_by_lane():
    loader = importlib.machinery.SourceFileLoader(
        "usage_by_lane", str(Path(__file__).resolve().parent.parent / "bin" / "usage-by-lane"))
    spec = importlib.util.spec_from_loader("usage_by_lane", loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_table_three_lanes(root: Path) -> None:
    mod = load_usage_by_lane()
    usage = {"input_tokens": 100, "output_tokens": 2000,
             "cache_read_input_tokens": 900_000,
             "cache_creation_input_tokens": 99_900}
    rows = {
        "alpha": [
            {"ts": "2026-09-29T09:00:00Z", "ok": True, "lane": "reflection",
             "total_cost_usd": 2.0, "usage": usage},
            {"ts": "2026-09-30T09:00:00Z", "ok": True, "lane": "reflection",
             "total_cost_usd": 3.0, "usage": usage},
            # Outside the window, and a skip that launched nothing.
            {"ts": "2026-09-01T09:00:00Z", "ok": True, "lane": "reflection",
             "total_cost_usd": 99.0, "usage": usage},
            {"ts": "2026-09-30T10:00:00Z", "ok": False,
             "stage": "lock_timeout", "lane": "reflection"},
            {"ts": "2026-09-30T12:00:00Z", "ok": True,
             "lane": "cron:watch", "total_cost_usd": 1.5},
        ],
        "beta": [
            {"ts": "2026-09-28T09:00:00Z", "ok": True, "lane": "reflection",
             "total_cost_usd": 4.0},
            {"ts": "2026-09-30T09:00:00Z", "ok": False, "stage": "turn_cap",
             "lane": "reflection", "total_cost_usd": 1.0, "usage": usage},
        ],
    }
    (root / "logs").mkdir()
    for agent, lines in rows.items():
        (root / "logs" / f"wake-{agent}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in lines) + "not json\n")
    now = datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc)
    lanes = mod.tally(root / "logs", 7, now)
    out = mod.render(lanes, 7)
    print("      " + out.replace("\n", "\n      "))
    alpha = lanes[("alpha", "reflection")]
    check("table: three lanes, one row each",
          set(lanes) == {("alpha", "reflection"), ("alpha", "cron:watch"),
                         ("beta", "reflection")})
    check("table: window and launched runs only (2 runs, $5.00)",
          alpha["runs"] == 2 and alpha["cost"] == 5.0
          and alpha["cache_read"] == 1_800_000, str(alpha))
    alpha_row = next(l for l in out.splitlines()
                     if l.startswith("alpha reflection"))
    check("table: cached share over all input is 90.0%",
          alpha_row.endswith("90.0%"), alpha_row)
    cron_row = next(l for l in out.splitlines()
                    if l.startswith("alpha cron:watch"))
    check("table: a lane with no usage says so, not zero",
          "no usage recorded" in cron_row and "$1.50" in cron_row, cron_row)
    beta_row = next(l for l in out.splitlines()
                    if l.startswith("beta reflection"))
    check("table: a mixed lane shows runs as a/b and sums cost over all",
          " 1/2 " in beta_row and "$5.00" in beta_row
          and "runs a/b" in out, beta_row)


if __name__ == "__main__":
    test_envelope_parse_real()
    test_envelope_parse_old()
    test_envelope_clips_long_input()
    for fn in (test_wake_line_real, test_wake_line_old, test_denial_one,
               test_denial_none, test_repeat_three_nights,
               test_repeat_two_nights, test_table_three_lanes):
        in_temp(fn)
    print()
    print("FAILURES:", results.count(False))
    sys.exit(1 if False in results else 0)
