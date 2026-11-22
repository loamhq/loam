"""Record each failed wake or broken layer in the failure log. Direct actionable
notices through the appropriate persona while retaining repeated occurrences in the
log.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import loam_config as lc

# Relative to the vault root. The machinery never hardcodes an absolute
# vault: bin/wake and bin/lane-check both read it out of agent.json, so a
# test tree and the live tree take the same path through here.
#
# Keep the existing ledger beside incoming agent notes on older installs.
_inbox = Path(lc.vault_folder("inbox"))
# A customized install may already have a ledger beside its former inbox
# document. Consult the old configuration solely to retain that ledger.
_previous_note = lc.get("vault.general_board")
_previous_folder = Path(_previous_note).parent if _previous_note else _inbox / "from-agents"
if str(_previous_folder) == lc.get("vault.boards"):
    _previous_folder = _inbox
_old_log = _previous_folder / "loam-wake-failures.md"
FAILURE_LOG = str(lc.get("vault.failure_log") or
                  (_old_log if (lc.VAULT / _old_log).is_file()
                   else _inbox / "loam-wake-failures.md"))

# The failure log's wiki-link. Obsidian resolves by basename, so
# this keeps working if the file moves again.
FAILURE_LOG_LINK = "[[loam-wake-failures]]"

# A fact's first line is a snippet, never the explanation. The failure reasons run
# to 400 characters of relayed stderr; the link is what carries the rest.
SNIPPET_CHARS = 200

FAILURE_LOG_HEADER = """\
# Loam wake failures

Appended by `agents/bin/wake`, `agents/bin/wake-failed` and
`agents/bin/lane-check` when a scheduled wake does not complete or the
nightly lane check finds a broken layer. A run of these starting on the same
night across agents means the sign-in expired. Nothing here is posted
anywhere: a persona's own failed run is told to that persona at its next
turn, and a fault that is no one persona's is handed to the agent chosen to
hear of Loam's problems (Settings › Agents). A line that says
"a warning, not a refusal" is a run that went ahead, written at most once a
night.

"""



def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def snippet(reason: str) -> str:
    """One line that brings the thing to his attention. First sentence, capped.

    Failure reasons are written to be read whole in the log. Here they are an
    invitation, so this takes the first sentence and stops -- and never lets a
    newline through, because the sweep and the format are both per-line.
    """
    flat = " ".join(reason.split())
    head = re.split(r"(?<=[.!?])\s", flat, maxsplit=1)[0]
    if len(head) > SNIPPET_CHARS:
        head = head[: SNIPPET_CHARS - 1].rstrip() + "…"
    return head


def _append_log(vault: Path, agent: str, reason: str, stamp: str) -> None:
    """One line into the failure log, in the one shape every line has."""
    import loam_claim_start
    if loam_claim_start.unclaimed():
        return
    note = vault / FAILURE_LOG
    try:
        note.parent.mkdir(parents=True, exist_ok=True)
        new = not note.exists()
        with note.open("a", encoding="utf-8") as handle:
            if new:
                handle.write(FAILURE_LOG_HEADER)
            handle.write(f"- `{stamp}` **{agent}** — {reason}\n")
    except OSError:
        pass


def record_failure(agent: str, vault: Path, reason: str) -> None:
    """Append the failure to the log. The lane notice delivers it separately to the
    persona's next turn.
    """
    _append_log(vault, agent, reason, now_iso())


def record_fault(by: str, vault: Path, reason: str, key: str | None = None) -> str:
    """A fault that is no one persona's: its log line, and the fact handed
    to the system persona once (lib/loam_attention.py). Returns what
    ``tell`` said."""
    import loam_attention  # noqa: PLC0415
    _append_log(vault, by, reason, now_iso())
    return loam_attention.tell(reason, by=by, key=key, evidence=str(vault / FAILURE_LOG))


def record_notice(agent: str, vault: Path, reason: str, key: str) -> bool:
    """One warning line in the log, at most once a night. No comms line.

    ``key`` is what makes two notices the same notice -- the name of the
    file being warned about, in practice. The count in the text changes from
    night to night, so the text itself cannot be the identity. "Once a night"
    is once per UTC date of the stamp, which the wake grid (09:00-12:00 UTC)
    and the 01:40 PT lane check (09:40 UTC) both fall inside; a second
    check the same night is exactly the repeat this suppresses.

    Returns True if a line was written. Best-effort like everything here: an
    unreadable log is treated as empty and an unwritable one is ignored,
    because a warning must never be what fails a wake.
    """
    stamp = now_iso()
    note = vault / FAILURE_LOG
    try:
        existing = note.read_text(encoding="utf-8") if note.exists() else ""
    except OSError:
        existing = ""
    today = f"- `{stamp[:10]}"
    for line in existing.splitlines():
        if line.startswith(today) and f"**{agent}**" in line and key in line:
            return False
    _append_log(vault, agent, reason, stamp)
    return True


def record_ledger(agent: str, vault: Path, reason: str) -> None:
    """One line in the log, every time, and no comms line.

    For a lane that produces nothing for the owner by design (the reviewer):
    its failures are on the ledger like every other lane's, but they are
    events, not a nightly warning, so unlike ``record_notice`` nothing is
    deduplicated. The 09-09 20:36Z lock and recycle refusals went through
    ``record_notice`` with the lane as the key and were swallowed as repeats
    of the 20:16Z pair.
    """
    _append_log(vault, agent, reason, now_iso())


VAULT_WRITE_REFUSED = (
    "a write into the vault was refused three nights running; the persona "
    "is losing that work each night"
)


def record_denials(agent: str, vault: Path, reason: str,
                   repeated_vault_write: bool = False) -> None:
    """A run refused one or more tool calls: its log entry, every time.

    The system persona hears of it only when ``repeated_vault_write`` --
    the caller has seen a denied vault write tonight and on each of the two
    nights before -- told once (lib/loam_attention.py)."""
    _append_log(vault, agent, reason, now_iso())
    if repeated_vault_write:
        import loam_attention  # noqa: PLC0415
        loam_attention.tell(f"{agent}: {VAULT_WRITE_REFUSED}.", by="loam",
                            key=f"vault-write-refused:{agent}",
                            evidence=str(vault / FAILURE_LOG))
