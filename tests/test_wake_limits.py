#!/usr/bin/env python3
"""Oversized lane files produce warnings and the wake can still run."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("LOAM_ATTENTION_DIR", tempfile.mkdtemp(prefix="loam-wake-limits-attention-"))
os.environ.setdefault("LOAM_WAITERS_DIR", os.environ["LOAM_ATTENTION_DIR"] + "/waiters")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_config as lc  # noqa: E402
import loam_identity  # noqa: E402
import loam_memory  # noqa: E402
from loam_memory import (  # noqa: E402
    LIMIT_CHARS,
    NEAR_LIMIT_CHARS,
)

OLD_REFUSAL_DATE = date(2026, 9, 11)

AGENT = "wakelimittest"
REAL_RUN = subprocess.run


def only_claude(recorder):
    """Intercept the wake's own launch; let git and everything else through.

    bin/wake shells out twice for reasons that have nothing to do with this
    test -- `git diff` for the vault summary -- and a blanket stub would
    capture those too and report a launch that never happened.
    """
    def fake(argv, **kwargs):
        if argv and str(argv[0]).endswith("/claude"):
            return recorder(argv, **kwargs)
        return REAL_RUN(argv, **kwargs)
    return fake


def load_wake():
    loader = importlib.machinery.SourceFileLoader(
        "wake_under_test", str(Path(__file__).resolve().parent.parent / "bin" / "wake"))
    spec = importlib.util.spec_from_loader("wake_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build(root: Path, reflection: str) -> tuple[Path, Path]:
    ws = root / "ai-labs" / AGENT
    vault = root / "vault"
    ws.mkdir(parents=True)
    (vault / "00_INBOX" / "from-agents").mkdir(parents=True)
    (ws / "agent.json").write_text(json.dumps({
        "name": AGENT, "display": "Wakelimittest",
        "workspace": str(ws), "vault": str(vault),
    }))
    (ws / "_reflection.md").write_text(reflection)
    return ws, vault


class FakeCompleted:
    returncode = 0
    stdout = json.dumps({"subtype": "success", "result": "ok", "num_turns": 1})
    stderr = ""


def stub(wake, launched: list, today: date) -> None:
    """Only the two boundaries with the outside world are replaced."""
    loam_identity.preflight_identity = (
        lambda project, model=None: ("canary", "someone")
    )
    wake.subprocess.run = only_claude(
        lambda argv, **k: (launched.append(argv) or FakeCompleted())
    )
    loam_memory.date = type("D", (), {"today": staticmethod(lambda: today)})


def inbox(vault: Path) -> tuple[str, str]:
    """(failure log, the board and every fact told, as one text), empty
    where nothing was written."""
    out = []
    for name in ("loam-wake-failures.md", "comms.md"):
        path = vault / "00_INBOX" / "from-agents" / name
        out.append(path.read_text(encoding="utf-8") if path.exists() else "")
    attention = Path(os.environ["LOAM_ATTENTION_DIR"])
    told = "".join(f"**{AGENT}** " + json.loads(p.read_text())["fact"] + "\n"
                   for p in sorted(attention.glob("2*.json"))
                   if AGENT in p.read_text())
    return out[0], out[1] + told


def scenario(label: str, reflection: str, today: date, expect_code,
             expect_note, expect_comms: bool | None = None):
    """Run a wake with one lane file and check the warning it produces."""
    if expect_comms is None:
        expect_comms = False
    root = Path(tempfile.mkdtemp(prefix="loam-wake-test-"))
    launched = []
    try:
        ws, vault = build(root, reflection)
        wake = load_wake()
        stub(wake, launched, today)

        code = wake.run(ws, AGENT, vault, "2026-09-04T00:00:00Z")
        text, comms = inbox(vault)

        ok = code == expect_code
        ok &= (expect_note in text) if expect_note else (not text)
        ok &= bool(launched) == (expect_code == 0)
        ok &= (f"**{AGENT}**" in comms) == expect_comms
        print(f"{'PASS' if ok else 'FAIL'}  {label:38} "
              f"exit={code} launched={bool(launched)} "
              f"comms={f'**{AGENT}**' in comms}")
        if not ok:
            print(f"      wanted exit={expect_code}, note containing "
                  f"{expect_note!r}, comms={expect_comms}; inbox held:\n"
                  f"      {text.strip()[:300]}")
        return ok
    finally:
        shutil.rmtree(root, ignore_errors=True)
        loam_memory.date = date


over = "o" * (LIMIT_CHARS + 1)          # 10,001: the limit
nearly = "n" * (LIMIT_CHARS - 1)        #  9,999: under the limit, warned
warned = "w" * (NEAR_LIMIT_CHARS + 1)   #  9,001: just over the warning
clear = "c" * (NEAR_LIMIT_CHARS - 1)    #  8,999: nothing to say
before = OLD_REFUSAL_DATE - timedelta(days=1)
after = date(2026, 11, 18)
WARNING = f"within {LIMIT_CHARS - NEAR_LIMIT_CHARS:,} characters of the"

results = [
    scenario("clear of the warning, before the date", clear, before, 0, None),
    scenario("clear of the warning, after the date", clear, after, 0, None),
    scenario("just over the WARNING, before the date", warned, before, 0,
             WARNING, expect_comms=False),
    scenario("just over the WARNING, after the date", warned, after, 0,
             WARNING, expect_comms=False),
    scenario("just under the limit, before the date", nearly, before, 0,
             WARNING, expect_comms=False),
    scenario("just under the limit, after the date", nearly, after, 0,
             WARNING, expect_comms=False),
    scenario("OVER limit, before the old date", over, before, 0,
             "Reported, not refused"),
    scenario("OVER limit, after it: still runs", over, after, 0,
             "the reflection lane went ahead"),
    scenario("9 over (11-17's case): runs", "o" * (LIMIT_CHARS + 9), after,
             0, "over the 10,000-character limit"),
    scenario("missing lane file", "", after, 3, "is empty"),
]

# The warning names the count, and it is written once a night: a second wake
# the same day against the same file adds nothing to the log.
root = Path(tempfile.mkdtemp(prefix="loam-wake-warn-"))
try:
    ws, vault = build(root, warned)
    wake = load_wake()
    launched: list = []
    stub(wake, launched, before)
    first = wake.run(ws, AGENT, vault, "2026-09-04T00:00:00Z")
    text1, _ = inbox(vault)
    (ws / "_reflection.md").write_text("w" * (NEAR_LIMIT_CHARS + 2))
    second = wake.run(ws, AGENT, vault, "2026-09-04T00:00:00Z")
    text2, comms2 = inbox(vault)
    warning_lines = [l for l in text2.splitlines() if WARNING in l]
    checks = [
        ("the warning names the count",
         f"{NEAR_LIMIT_CHARS + 1:,} characters" in text1),
        ("the warning names the file", "`_reflection.md`" in text1),
        ("the warning says it is not a refusal",
         "A warning, not a refusal" in text1),
        ("both wakes ran", first == 0 and second == 0 and len(launched) == 2),
        ("one line per night, not one per wake", len(warning_lines) == 1),
        ("nothing reached the board or anyone", f"**{AGENT}**" not in comms2),
    ]
    for label, ok in checks:
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {label:38}")
        if not ok:
            print(f"      log held:\n      {text2.strip()[:400]}")
finally:
    shutil.rmtree(root, ignore_errors=True)
    loam_memory.date = date

# The lane text reaches the CLI stripped, through the file flag, alongside the
# style override -- the three facts that make the wake a lane of its own.
root = Path(tempfile.mkdtemp(prefix="loam-wake-argv-"))
try:
    ws, vault = build(root, "---\nstray: yaml\n---\n\nLANE BODY\n")
    wake = load_wake()
    loam_identity.preflight_identity = lambda project, model=None: ("c", "o")
    seen = {}

    def capture(argv, **kwargs):
        seen["argv"] = argv
        i = argv.index("--append-system-prompt-file")
        seen["lane"] = Path(argv[i + 1]).read_text(encoding="utf-8")
        return FakeCompleted()

    wake.subprocess.run = only_claude(capture)
    wake.run(ws, AGENT, vault, "2026-09-04T00:00:00Z")
    argv = seen["argv"]
    checks = [
        ("passes --append-system-prompt-file", "--append-system-prompt-file" in argv),
        ("lane text is frontmatter-stripped", "stray" not in seen["lane"]),
        ("lane text carries the body", "LANE BODY" in seen["lane"]),
        ("suppresses the conversation style",
         json.loads(argv[argv.index("--settings") + 1]).get("outputStyle") == "none"),
        ("temp lane file is cleaned up",
         not Path(argv[argv.index("--append-system-prompt-file") + 1]).exists()),
    ]
    for label, ok in checks:
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {label:38}")
finally:
    shutil.rmtree(root, ignore_errors=True)
    for stray in (
        Path(f"{lc.STATE}/wake/{AGENT}.json"),
        Path(f"{lc.STATE}/wake/{AGENT}.running"),
        Path(f"{lc.LOGS}/wake-{AGENT}.jsonl"),
    ):
        stray.unlink(missing_ok=True)

# Distinguish a provider-reported turn cap from a successful envelope.
import loam_session  # noqa: E402

SID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def session_lines() -> str:
    def entry(kind, **fields):
        return json.dumps({"type": kind, **fields})
    out = [entry("user", message={"role": "user", "content": "wake"})]
    for i, mid in enumerate(("msg_1", "msg_2", "msg_3")):
        for n in range(4):
            out.append(entry("assistant", message={"id": mid, "content": [
                {"type": "tool_use", "id": f"t{i}{n}", "name": "Read"}]}))
        for n in range(4):
            out.append(entry("user", message={"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"t{i}{n}"}]}))
    out.append(entry("assistant", message={"id": "msg_4", "content": [
        {"type": "text", "text": "done"}]}))
    return "\n".join(out) + "\n"


class FakeFinished:
    returncode = 0
    stdout = json.dumps({"subtype": "success", "result": "ok",
                         "terminal_reason": "completed",
                         "num_turns": 13, "session_id": SID})
    stderr = ""


class FakeCapped:
    returncode = 1
    stdout = json.dumps({"subtype": "error_max_turns", "is_error": True,
                         "terminal_reason": "max_turns", "num_turns": 4,
                         "session_id": SID, "result": ""})
    stderr = ""


log_path = Path(f"{lc.LOGS}/wake-{AGENT}.jsonl")
saved_sessions = loam_session.SESSIONS_DIR
for label, fake, want_code, want_row in (
    ("finished wake", FakeFinished, 0,
     {"ok": True, "num_turns": 13, "api_turns": 4, "hit_turn_cap": False}),
    ("capped wake", FakeCapped, 6,
     {"ok": False, "stage": "turn_cap", "num_turns": 4, "api_turns": 4,
      "hit_turn_cap": True}),
):
    root = Path(tempfile.mkdtemp(prefix="loam-wake-cap-"))
    try:
        ws, vault = build(root, clear)
        (root / "sessions" / "-some-project").mkdir(parents=True)
        (root / "sessions" / "-some-project" / f"{SID}.jsonl").write_text(
            session_lines(), encoding="utf-8")
        wake = load_wake()
        launched = []
        stub(wake, launched, before)
        wake.subprocess.run = only_claude(
            lambda argv, **k: (launched.append(argv) or fake()))
        loam_session.SESSIONS_DIR = root / "sessions"
        log_path.unlink(missing_ok=True)
        code = wake.run(ws, AGENT, vault, "2026-09-04T00:00:00Z")
        row = json.loads(log_path.read_text().splitlines()[-1])
        text, _ = inbox(vault)
        checks = [
            (f"{label}: exit code", code == want_code),
            (f"{label}: log row",
             all(row.get(k) == v for k, v in want_row.items())),
            (f"{label}: max_turns logged", row.get("max_turns") == wake.MAX_TURNS),
            (f"{label}: failure line",
             ("round-trip cap" in text) == (want_code == 6)),
        ]
        for name, ok in checks:
            results.append(ok)
            print(f"{'PASS' if ok else 'FAIL'}  {name:38}")
            if not ok:
                print(f"      exit={code} row={row} inbox={text.strip()[:200]}")
    finally:
        loam_session.SESSIONS_DIR = saved_sessions
        loam_memory.date = date
        shutil.rmtree(root, ignore_errors=True)
        for stray in (
            Path(f"{lc.STATE}/wake/{AGENT}.json"),
            Path(f"{lc.STATE}/wake/{AGENT}.running"),
            log_path,
        ):
            stray.unlink(missing_ok=True)

print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
