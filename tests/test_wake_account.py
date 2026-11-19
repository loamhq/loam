#!/usr/bin/env python3
"""A provider refusal is named ``account_refused``, not a soul-composition fault.

On 2026-09-15 the account's monthly spend ceiling stopped the night, and the
failure log read as six identity-preflight failures, one run cut off with a
JSON envelope for a reason, and a reviewer line. The fixtures below are
those lines, quoted from ``vault/00_INBOX/from-agents/loam-wake-failures.md``
and, for Wren, from the session transcript that held the real cause.

Three layers, each against the shipped code:

  matcher   lib/loam_account.py over the 09-15 strings, the structured
            category (transcript entry and stream-json api_retry event),
            and answers that must NOT match.
  inbox     lib/loam_inbox.record_account_refusal: one fact a day for the
            system persona (lib/loam_attention.py; nothing on the board
            since 2026-11-19), every lane in the log, a new fact on a new
            date.
  wake      bin/wake end to end, with only the CLI stubbed: two preflight
            refusals and one mid-run refusal on one night are one fact; a
            genuine "NO NO" is still the identity's failure, logged and told
            to nobody; a genuine non-zero exit is still the CLI's.

Run: python3 tests/test_wake_account.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import os
import tempfile
from pathlib import Path

# The system persona's facts land in a scratch folder, never the box's.
ATTENTION = Path(tempfile.mkdtemp(prefix="loam-account-attention-"))
os.environ["LOAM_ATTENTION_DIR"] = str(ATTENTION)
os.environ.setdefault("LOAM_WAITERS_DIR", str(ATTENTION / "waiters"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_account  # noqa: E402
import loam_identity  # noqa: E402
import loam_inbox  # noqa: E402
import loam_session  # noqa: E402

REAL_RUN = subprocess.run
REAL_PREFLIGHT = loam_identity.preflight_identity
REAL_SESSIONS = loam_session.SESSIONS_DIR

# --- The 09-15 fixtures, verbatim ------------------------------------------

# What the preflight got back, as the failure log quotes it (upper-cased and
# cut at 120 characters by the old error string). Pax 09:40:50Z, Juno
# 10:00:50Z, Ada 10:20:50Z, Otto 10:40:50Z, Iris 11:00:50Z and Ada's
# market-watch cron 12:00:50Z all carried exactly this.
PREFLIGHT_ANSWER_0915 = (
    "YOU'VE HIT YOUR MONTHLY SPEND LIMIT. SWITCH TO ANOTHER MODEL, OR MANAGE "
    "USAGE CREDITS AT CLAUDE.AI/SETTINGS/USAGE?FROM=C"
)
PAX_LINE_0915 = (
    "reflection identity preflight refused: soul.md content is not in the "
    "composed context (expected 'YES NO' for real/decoy, got \"YOU'VE HIT "
    "YOUR MONTHLY SPEND LIMIT. SWITCH TO ANOTHER MODEL, OR MANAGE USAGE "
    "CREDITS AT CLAUDE.AI/SETTINGS/USAGE?FROM=C\")"
)
CRON_LINE_0915 = (
    "cron:market-watch identity preflight refused: soul.md content is not in "
    "the composed context (expected 'YES NO' for real/decoy, got \"YOU'VE HIT "
    "YOUR MONTHLY SPEND LIMIT. SWITCH TO ANOTHER MODEL, OR MANAGE USAGE "
    "CREDITS AT CLAUDE.AI/SETTINGS/USAGE?FROM=C\")"
)
REVIEWER_LINE_0915 = (
    "review run 20260915-042047: exited 1 (subtype=success) — rate_limit: "
    "You've hit your monthly spend limit. Switch to another model, or manage "
    "usage credits at claude.ai/settings/usage?from=cc_cli_limit_message, to "
    "continue.. Record: /home/loam/agents/state/reviewer/runs/"
    "20260915-042047/run.json."
)
# Wren's line in the log is the envelope cut at 400 characters and says
# nothing about the account. The cause was in the session transcript, entry
# at 2026-09-15T09:22:28.769Z of c8b933ba-6dfe-41a3-97a0-4b9112f09164:
WREN_LINE_0915 = (
    "reflection: claude exited 1 (subtype=success): {\"duration_api_ms\":"
    "94899,\"stop_reason\":\"stop_sequence\",\"session_id\":\"c8b933ba-6dfe-"
    "41a3-97a0-4b9112f09164\",\"total_cost_usd\":1.71136825"
)
WREN_TRANSCRIPT_0915 = {
    "type": "assistant", "isApiErrorMessage": True, "error": "rate_limit",
    "message": {"content": [{"type": "text", "text": (
        "You've hit your monthly spend limit. Switch to another model, or "
        "manage usage credits at claude.ai/settings/usage?from=cc_cli_limit_"
        "message, to continue."
    )}]},
}

results: list[bool] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok and detail:
        print(f"      {detail}")


# --- matcher ---------------------------------------------------------------

def test_matcher_names_the_0915_strings():
    for label, text in (
        ("the preflight answer", PREFLIGHT_ANSWER_0915),
        ("pax's log line", PAX_LINE_0915),
        ("the market-watch cron's log line", CRON_LINE_0915),
        ("the reviewer's log line", REVIEWER_LINE_0915),
    ):
        check(f"matcher: 09-15 {label} is account_refused",
              loam_account.refusal(text) is not None)
    check("matcher: wren's logged envelope alone is NOT enough (why the "
          "transcript is read)", loam_account.refusal(WREN_LINE_0915) is None)
    said = WREN_TRANSCRIPT_0915["message"]["content"][0]["text"]
    got = loam_account.refusal(said, kind=WREN_TRANSCRIPT_0915["error"])
    check("matcher: wren's transcript entry is account_refused, category first",
          got == f"rate_limit: {said}", repr(got))


def test_matcher_retry_event_path():
    # The stream-json event shape, category only in `error`; the words
    # need not match anything for the category to decide.
    for kind in sorted(loam_account.ACCOUNT_ERRORS):
        event = {"type": "system", "subtype": "api_retry", "attempt": 1,
                 "max_retries": 10, "retry_delay_ms": 500,
                 "error_status": 402, "error": kind}
        got = loam_account.refusal(
            "Request failed.", kind=loam_account.kind_from_event(event))
        check(f"matcher: api_retry error={kind} is account_refused",
              got == f"{kind}: Request failed.", repr(got))
    server = {"type": "system", "subtype": "api_retry", "error": "server_error"}
    check("matcher: api_retry error=server_error is NOT account_refused",
          loam_account.refusal("Overloaded.",
                               kind=loam_account.kind_from_event(server)) is None)
    check("matcher: a non-retry event carries no category",
          loam_account.kind_from_event(
              {"type": "system", "subtype": "init", "error": "rate_limit"}) is None)


def test_matcher_leaves_real_answers_alone():
    for answer in ("YES NO", "NO NO", "NO YES", "YES YES", "",
                   "I read the billing page and wrote up the limits."):
        check(f"matcher: {answer!r} is not a refusal",
              loam_account.refusal(answer) is None)


# --- inbox -----------------------------------------------------------------

def comms_of(vault: Path) -> str:
    """The board: it must stay as it was (2026-11-19)."""
    path = vault / loam_inbox.COMMS
    return path.read_text(encoding="utf-8") if path.exists() else ""


def facts() -> list[str]:
    """What the system persona was told, oldest first."""
    return [json.loads(p.read_text())["fact"]
            for p in sorted(ATTENTION.glob("2*.json"))]


def reset_facts() -> None:
    for p in ATTENTION.glob("*.json"):
        p.unlink()


def log_of(vault: Path) -> str:
    path = vault / loam_inbox.FAILURE_LOG
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_inbox_one_line_a_night():
    root = Path(tempfile.mkdtemp(prefix="loam-account-inbox-"))
    reset_facts()
    try:
        vault = root / "vault"
        said = loam_account.refusal(PREFLIGHT_ANSWER_0915)
        night = [("wren", "2026-09-15T09:22:29Z"), ("pax", "2026-09-15T09:40:50Z"),
                 ("juno", "2026-09-15T10:00:50Z"), ("ada", "2026-09-15T10:20:50Z"),
                 ("otto", "2026-09-15T10:40:50Z"), ("iris", "2026-09-15T11:00:50Z"),
                 ("ada", "2026-09-15T12:00:50Z")]
        for agent, stamp in night:
            loam_inbox.record_account_refusal(
                agent, vault, f"reflection account_refused (identity): {said}",
                said, stamp=stamp)
        told = [f for f in facts() if loam_inbox.ACCOUNT_PREFIX in f]
        want = ("The provider account refused runs from 09:22Z (YOU'VE HIT YOUR "
                "MONTHLY SPEND LIMIT.); every lane it refuses that day is a line "
                "in the failure log.")
        print(f"      the 09-15 night's fact: {told[0] if told else None}")
        check("inbox: seven refusals in one night are one fact", len(told) == 1, told)
        check("inbox: that fact says what happened", told == [want], repr(told))
        check("inbox: no agent's own name is in it",
              not any(a in told[0] for a, _ in night))
        check("inbox: nothing on the board", comms_of(vault) == "")
        check("inbox: each lane still has its own log entry",
              sum(1 for l in log_of(vault).splitlines()
                  if l.startswith("- `") and "account_refused" in l) == 7)
        loam_inbox.record_account_refusal(
            "pax", vault, "reflection account_refused (identity): x", said,
            stamp="2026-09-16T09:40:50Z")
        told = [f for f in facts() if loam_inbox.ACCOUNT_PREFIX in f]
        check("inbox: the next day is a fact of its own",
              len(told) == 2 and any("09:40Z" in f for f in told), repr(told))
    finally:
        shutil.rmtree(root, ignore_errors=True)


# --- bin/wake end to end ---------------------------------------------------

def load_wake():
    loader = importlib.machinery.SourceFileLoader(
        "wake_under_test", str(Path(__file__).resolve().parent.parent / "bin" / "wake"))
    spec = importlib.util.spec_from_loader("wake_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def build(root: Path, agent: str) -> Path:
    ws = root / "ai-labs" / agent
    ws.mkdir(parents=True)
    (ws / "agent.json").write_text(json.dumps({
        "name": agent, "workspace": str(ws), "vault": str(root / "vault")}))
    (ws / "_reflection.md").write_text("reflect\n")
    return ws


def wake_once(root: Path, agent: str, preflight_answer: str,
              run: Done | None = None) -> int:
    """One reflection wake. The preflight's own assertion runs for real; only
    the CLI calls are answered here, the probe first and the wake second."""
    ws = build(root, agent)
    wake = load_wake()
    wake.LOG_DIR = root / "logs"
    wake.STATE_DIR = root / "state"
    answers = [Done(0, preflight_answer + "\n"),
               run or Done(0, json.dumps({"subtype": "success", "result": "ok"}))]

    def fake(argv, **kwargs):
        if argv and str(argv[0]).endswith("/claude"):
            return answers.pop(0)
        return REAL_RUN(argv, **kwargs)

    wake.subprocess.run = fake
    loam_identity.preflight_identity = (
        lambda project, model=None: (
            loam_identity.assert_soul_in_context(project, "canary", "decoy", model),
            ("canary", "someone"))[1]
    )
    try:
        return wake.run(ws, agent, root / "vault", "2026-09-15T09:40:47Z")
    finally:
        subprocess.run = REAL_RUN
        loam_identity.preflight_identity = REAL_PREFLIGHT


def test_wake_names_the_night():
    root = Path(tempfile.mkdtemp(prefix="loam-account-wake-"))
    reset_facts()
    try:
        vault = root / "vault"
        (vault / "00_INBOX" / "from-agents").mkdir(parents=True)
        sessions = root / "sessions" / "-proj"
        sessions.mkdir(parents=True)
        loam_session.SESSIONS_DIR = root / "sessions"
        (sessions / "c8b933ba.jsonl").write_text(json.dumps(WREN_TRANSCRIPT_0915) + "\n")

        # Wren: preflight passes, the run is cut off. Exit 1, a `success`
        # envelope, nothing on stderr -- the 09-15 shape.
        wren = wake_once(root, "wren", "YES NO", Done(1, json.dumps({
            "subtype": "success", "session_id": "c8b933ba",
            "duration_api_ms": 94899, "total_cost_usd": 1.71136825}), ""))
        pax = wake_once(root, "pax", PREFLIGHT_ANSWER_0915)
        juno = wake_once(root, "juno", PREFLIGHT_ANSWER_0915)
        log, comms = log_of(vault), comms_of(vault)

        check("wake: wren's mid-run refusal exits 7 (account_refused)", wren == 7)
        check("wake: pax's and juno's preflight refusals exit 7",
              pax == 7 and juno == 7)
        check("wake: wren's log entry names the transcript's category",
              "**wren** — reflection account_refused (invoke): rate_limit: "
              "You've hit your monthly spend limit." in log, log)
        check("wake: pax's log entry is account_refused, not the soul",
              "**pax** — reflection account_refused (identity): YOU'VE HIT "
              "YOUR MONTHLY SPEND LIMIT." in log
              and "soul.md content is not in the composed context" not in log, log)
        told = [f for f in facts() if loam_inbox.ACCOUNT_PREFIX in f]
        check("wake: three refused lanes are one fact",
              len(told) == 1 and re.search(
                  r"^The provider account refused runs from \d\d:\d\dZ "
                  r"\(rate_limit: You've hit your monthly spend limit\.\)",
                  told[0]) is not None, repr(told))
        check("wake: and nothing on the board", comms == "", comms)
        records = [json.loads(l) for l in
                   (root / "logs" / "wake-pax.jsonl").read_text().splitlines()]
        check("wake: the jsonl stage is account_refused",
              records[-1].get("stage") == "account_refused"
              and records[-1].get("refused_at") == "identity", repr(records[-1]))

        # Genuine failures keep their names.
        otto = wake_once(root, "otto", "NO NO")
        log, comms = log_of(vault), comms_of(vault)
        check("wake: a genuine 'NO NO' is still an identity preflight failure",
              otto == 5 and "**otto** — reflection identity preflight "
              "refused: soul.md content is not in the composed context "
              "(expected 'YES NO' for real/decoy, got 'NO NO')" in log, log)
        check("wake: a persona's own failure is told to nobody but it (its next turn)",
              comms == "" and not any("otto" in f for f in facts()), facts())
        iris = wake_once(root, "iris", "YES NO", Done(
            1, json.dumps({"subtype": "error_during_execution"}), "boom"))
        check("wake: a genuine non-zero exit is still the CLI's failure",
              iris == 4 and "**iris** — reflection: claude exited 1" in log_of(vault))
        check("wake: the account is still one fact",
              len([f for f in facts() if loam_inbox.ACCOUNT_PREFIX in f]) == 1)
    finally:
        loam_session.SESSIONS_DIR = REAL_SESSIONS
        shutil.rmtree(root, ignore_errors=True)


test_matcher_names_the_0915_strings()
test_matcher_retry_event_path()
test_matcher_leaves_real_answers_alone()
test_inbox_one_line_a_night()
test_wake_names_the_night()

print(f"\n{sum(results)}/{len(results)} passed")
shutil.rmtree(ATTENTION, ignore_errors=True)
sys.exit(0 if all(results) else 1)
