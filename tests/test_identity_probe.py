#!/usr/bin/env python3
"""Identity preflight prompt and Codex ask guard.

A literal-minded answerer asked about "the text currently in your context"
truthfully sees the decoy inside the prompt itself and answers YES YES.
Scoping the prompt to instructions loaded before this message (not counting
this message itself) makes a literal-minded answerer return YES NO for the
right soul while still returning NO YES / NO NO / YES YES when the loaded
soul is wrong, missing, or contaminated. On Codex, ``loam_codex_wake.ask``
also refuses any probe reply that used tools or subagents.

Run: python3 tests/test_identity_probe.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_codex_wake as cw  # noqa: E402
import loam_identity as li  # noqa: E402

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"      got  {got!r}\n      want {want!r}")


def raises_identity(fn) -> str | None:
    try:
        fn()
    except li.IdentityError as exc:
        return str(exc)
    return None


def literal_answer(loaded_instructions: str, prompt: str) -> str:
    """Answer the exact letter of ``prompt``.

    When the prompt asks about "the text currently in your context" without
    excluding the prompt itself, the search space is the loaded instructions
    plus the prompt — where both ``<<...>>`` phrases appear literally.
    When the prompt explicitly restricts the check to instructions loaded
    before this message (excluding this message itself), the search space is
    ``loaded_instructions`` alone.
    """
    phrases = re.findall(r"<<(.+?)>>", prompt)
    assert len(phrases) == 2, f"expected two <<...>> phrases, got {phrases!r}"
    excludes_prompt = bool(
        re.search(
            r"(?:before this message|not counting this message|excluding this message)",
            prompt,
            re.IGNORECASE,
        )
    )
    haystack = (
        loaded_instructions
        if excludes_prompt
        else f"{loaded_instructions}\n{prompt}"
    )
    return " ".join("YES" if phrase in haystack else "NO" for phrase in phrases)


CANARY = "She was here before anyone else, and she stays."
DECOY = "She wants to know why the circus appeals before she says a word about the circus."
RIGHT_SOUL = f"# Soul\n\n{CANARY}\n"
WRONG_SOUL = f"# Soul\n\n{DECOY}\n"
NO_SOUL = "# Config\n\nKeep answers concise.\n"
BOTH_SOULS = f"{RIGHT_SOUL}\n{WRONG_SOUL}"

OLD_PROMPT = (
    "Answer with exactly two words separated by a space, nothing else. "
    "First word: YES if the text currently in your context contains the "
    f"exact phrase <<{CANARY}>>, otherwise NO. "
    "Second word: YES if it contains the exact phrase "
    f"<<{DECOY}>>, otherwise NO."
)

# 1. Today's (old) prompt fails for a literal-minded answerer even with the right soul.
old_reply = literal_answer(RIGHT_SOUL, OLD_PROMPT)
check("old prompt causes literal-minded answerer to reply YES YES", old_reply, "YES YES")
check("judge refuses old prompt's YES YES",
      raises_identity(lambda: li.judge(old_reply)) is not None, True)

# 2. The fixed prompt passes for the right soul and refuses wrong, missing, or merged souls.
new_prompt = li.probe_prompt(CANARY, DECOY)
check("new prompt forbids tools and file reads",
      "use no tools" in new_prompt.lower() and "read no files" in new_prompt.lower(),
      True)
check("new prompt gives YES NO on right soul for literal-minded answerer",
      literal_answer(RIGHT_SOUL, new_prompt), "YES NO")
check("assert_soul_in_context passes on right soul",
      raises_identity(
          lambda: li.assert_soul_in_context(
              Path("."), CANARY, DECOY,
              ask=lambda q: literal_answer(RIGHT_SOUL, q),
          )
      ),
      None)
check("new prompt refuses wrong soul (NO YES)",
      raises_identity(
          lambda: li.assert_soul_in_context(
              Path("."), CANARY, DECOY,
              ask=lambda q: literal_answer(WRONG_SOUL, q),
          )
      ) is not None and literal_answer(WRONG_SOUL, new_prompt) == "NO YES",
      True)
check("new prompt refuses missing soul (NO NO)",
      raises_identity(
          lambda: li.assert_soul_in_context(
              Path("."), CANARY, DECOY,
              ask=lambda q: literal_answer(NO_SOUL, q),
          )
      ) is not None and literal_answer(NO_SOUL, new_prompt) == "NO NO",
      True)
check("new prompt refuses contaminated instructions carrying both souls (YES YES)",
      raises_identity(
          lambda: li.assert_soul_in_context(
              Path("."), CANARY, DECOY,
              ask=lambda q: literal_answer(BOTH_SOULS, q),
          )
      ) is not None and literal_answer(BOTH_SOULS, new_prompt) == "YES YES",
      True)


# 3. Codex ask() refuses any probe turn that invoked tools or subagents.
class FakeServer:
    def __init__(self, script: list[dict]):
        self.queue = list(script)
        self.closed = False

    def request(self, method, params, timeout=0):
        if method == "thread/start":
            return {"thread": {"id": "th-probe"}}
        return {"turn": {"id": "t1"}}

    def send(self, obj):
        pass

    def next(self, timeout=0):
        return self.queue.pop(0) if self.queue else None

    def close(self):
        self.closed = True


def n(method, **params):
    return {"method": method, "params": params}


real_open = cw.open_server
try:
    clean_script = [
        n("turn/started", threadId="th-probe", turn={"id": "t1"}),
        n("item/completed", threadId="th-probe",
          item={"type": "agentMessage", "id": "m1", "text": "YES NO"}),
        n("turn/completed", threadId="th-probe", turn={"id": "t1", "status": "completed"}),
    ]
    srv = FakeServer(clean_script)
    cw.open_server = lambda ws, env=None, argv=None: srv
    clean_reply = cw.ask(Path("."), RIGHT_SOUL, None, new_prompt)
    check("codex ask returns YES NO when zero tools were used",
          clean_reply == "YES NO" and srv.closed, True)

    tool_script = [
        n("turn/started", threadId="th-probe", turn={"id": "t1"}),
        n("item/completed", threadId="th-probe",
          item={"type": "commandExecution", "id": "c1", "status": "completed",
                "aggregatedOutput": DECOY, "exitCode": 0}),
        n("item/completed", threadId="th-probe",
          item={"type": "agentMessage", "id": "m1", "text": "YES NO"}),
        n("turn/completed", threadId="th-probe", turn={"id": "t1", "status": "completed"}),
    ]
    srv = FakeServer(tool_script)
    cw.open_server = lambda ws, env=None, argv=None: srv
    tool_reply = cw.ask(Path("."), RIGHT_SOUL, None, new_prompt)
    check("codex ask refuses a probe reply that executed a tool",
          "used tools" in tool_reply and srv.closed
          and raises_identity(lambda: li.judge(tool_reply)) is not None,
          True)

    sub_script = [
        n("turn/started", threadId="th-probe", turn={"id": "t1"}),
        n("item/completed", threadId="th-probe",
          item={"type": "subAgentActivity", "id": "s1", "kind": "started",
                "agentThreadId": "th-sub", "agentPath": "/root/helper"}),
        n("item/completed", threadId="th-probe",
          item={"type": "agentMessage", "id": "m1", "text": "YES NO"}),
        n("turn/completed", threadId="th-probe", turn={"id": "t1", "status": "completed"}),
    ]
    srv = FakeServer(sub_script)
    cw.open_server = lambda ws, env=None, argv=None: srv
    sub_reply = cw.ask(Path("."), RIGHT_SOUL, None, new_prompt)
    check("codex ask refuses a probe reply that spawned a subagent",
          "used tools" in sub_reply and srv.closed
          and raises_identity(lambda: li.judge(sub_reply)) is not None,
          True)
finally:
    cw.open_server = real_open

print()
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
