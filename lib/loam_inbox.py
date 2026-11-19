"""The failure log: where the machinery records what went wrong, quietly.

``vault/<General's board folder>/loam-wake-failures.md`` (``00_INBOX/from-
agents/`` on the first install) is one appended line per failed wake or
broken layer: a log, every occurrence, a repeat included. Activity and the
failure log are the one quiet record (batch B, 2026-11-18).

**Nothing here writes on the General board any more** (2026-11-19, batch B
step 5): "I don't want the engineers posting on Threads. If something is
wrong, they should pass it to a persona that then decides what to do and how
to communicate with me if necessary." Until then this module paired each
failure with a line under Urgent in ``comms.md``. Now:

- a persona's own failed run is logged here and told to that persona at the
  top of its next turn (lib/loam_lane_notice.py), and nowhere else;
- a fault that is no one persona's (the account refusing runs, a vault write
  refused three nights running, a lane check's finding) is logged here and
  handed to the system persona as a fact (lib/loam_attention.py), which
  decides whether the owner hears of it, in its own words;
- ``append_comms`` is kept only so an older plugin that still calls it does
  not break: it writes nothing on the board and hands its line to the system
  persona as a fact.

``record_notice`` (a warning, once a night) and ``record_ledger`` (every
time, no dedupe) are log-only, as they always were.

**Three callers of the log**: ``bin/wake`` records its own failures,
``bin/wake-failed`` the ones that killed the wrapper before it could, and
``bin/lane-check`` what is wrong with a lane's files, nightly. Everything
here is stdlib (and loam_config, for the folder's name) and takes the vault
as an argument, so a test can point it at a temp tree and nothing in it can
fail a wake: every write is best-effort and swallows OSError.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath

import loam_config as lc

# Relative to the vault root. The machinery never hardcodes an absolute
# vault: bin/wake and bin/lane-check both read it out of agent.json, so a
# test tree and the live tree take the same path through here.
#
# The folder is the install's, not the first install's (2026-11-16, the
# first-day walk): comms.md is General's board (`vault.general_board`), and
# the failure log sits beside it -- on the first install in
# 00_INBOX/from-agents/, as it always was. Unless General's board sits in the
# boards folder itself, as on an install made by install.sh
# (Inbox/boards/general.md): every note there is read as a board
# (loam_threads.board_files), so the log goes in the inbox folder instead.
COMMS = lc.vault_folder("general_board")
FROM_AGENTS = str(PurePosixPath(COMMS).parent)
if PurePosixPath(FROM_AGENTS) == PurePosixPath(lc.vault_folder("boards")):
    FROM_AGENTS = lc.vault_folder("inbox")
FAILURE_LOG = f"{FROM_AGENTS}/loam-wake-failures.md"

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


def append_comms(
    vault: Path, agent: str, text: str, link: str,
    section: str = "Urgent", when: date | None = None,
) -> bool:
    """Kept for an older plugin that still calls it (2026-11-19): nothing is
    written on the board. The line is handed to the system persona as a
    fact (lib/loam_attention.py), told once. True if it was told."""
    import loam_attention  # noqa: PLC0415
    return loam_attention.tell(text, by=agent, evidence=link or None) == "told"


def _append_log(vault: Path, agent: str, reason: str, stamp: str) -> None:
    """One line into the failure log, in the one shape every line has."""
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
    """A failed run, in the log. Since 2026-11-19 that is all: the persona
    whose run it was hears of it at the top of its next turn
    (lib/loam_lane_notice.py), and the board hears nothing."""
    _append_log(vault, agent, reason, now_iso())


def record_fault(by: str, vault: Path, reason: str, key: str | None = None) -> str:
    """A fault that is no one persona's: its log line, and the fact handed
    to the system persona once (lib/loam_attention.py). Returns what
    ``tell`` said."""
    import loam_attention  # noqa: PLC0415
    _append_log(vault, by, reason, now_iso())
    return loam_attention.tell(reason, by=by, key=key, evidence=str(vault / FAILURE_LOG))


# The account refusing is one fact a day, not one per lane (the owner read
# seven wrong diagnoses on 2026-09-15 before the right one). It is the
# runtime's, not any agent's: the lane that happened to be refused first is
# not the subject of it.
ACCOUNT_POSTER = "loam"
ACCOUNT_PREFIX = "account refused runs from"


def account_text(since: str, reason: str) -> str:
    """``since`` is the first refused run's ISO stamp; HH:MMZ is shown."""
    return f"{ACCOUNT_PREFIX} {since[11:16]}Z ({snippet(reason)})"


def record_account_refusal(
    agent: str, vault: Path, reason: str, provider: str,
    stamp: str | None = None,
) -> None:
    """A lane the provider refused: its log line, and the day's one fact.

    ``reason`` is the lane's whole entry for the log; ``provider`` is what
    the provider said. The first refusal of a UTC date hands the system
    persona the fact; each later one that date is the log's line only (the
    log carries the count). When the account refuses every run, the system
    persona's run is refused too: that case is the app's mark, not this."""
    import loam_attention  # noqa: PLC0415
    stamp = stamp or now_iso()
    _append_log(vault, agent, reason, stamp)
    loam_attention.tell(
        f"The provider {account_text(stamp, provider)}; every lane it "
        f"refuses that day is a line in the failure log.",
        by=ACCOUNT_POSTER, key=f"account-refused:{stamp[:10]}",
        evidence=str(vault / FAILURE_LOG))


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
