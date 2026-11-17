"""Where the machinery writes to the owner's inbox, and the only place it does.

``vault/<General's board folder>/`` (``00_INBOX/from-agents/`` on the first
install) is the pull-only outlet decided 2026-09-04
(phase-5 closing plan, decision 13). Two files live there and this module
owns both of the machinery's writes into them:

  loam-wake-failures.md   the detector. One appended line per failed wake or
                          failed Remote Control start. It is a log: every
                          occurrence lands, including a repeat.
  comms.md                the loop. One line under ``## Urgent`` so the log
                          has a reader. It is NOT a log -- a line per item,
                          not per night -- so a repeat of the same thing from
                          the same agent is written once and not again.

The two are written together because a failure nobody reads is the same
failure as one nobody detected. That pairing is the whole reason the comms
line exists (a persona's lean, Move 7).

One write is paired differently: ``record_account_refusal``, since
2026-09-30. A run the provider refused (spend limit, billing, credential)
still gets its log line, but comms.md gets one line a night for all of them,
with a count, because every lane that night fails for the same reason.

One write is deliberately NOT paired: ``record_notice``, since 2026-09-07.
A warning -- today, an auto-loaded file within a thousand characters of its
limit -- goes to the log alone, at most once a night for the same thing, and
never under Urgent. A nudge that lands in the loop every night is what
teaches its reader to stop opening the loop.

Nor is ``record_denials``, since 2026-09-30. A run the CLI refused a tool
call finished; the refusal is the boundary working. Its entry goes to the
log every time and to comms.md never -- with one exception, a persona's
write into the vault refused three nights running, which is one line under
Important (see lib/loam_usage.py).

**One owner.** ``bin/wake`` records its own failures, ``bin/wake-failed``
records the ones that killed the wrapper before it could, and
``bin/rc-preflight`` records what is wrong at a Remote Control start. Three
callers, three very different lifetimes -- and until this module they were
two different implementations of "how a failure is written", which is the
same defect as the second copy of the RC units. Everything here is stdlib (and
loam_config, for the folder's name) and takes the vault as an argument, so a test can point it at a temp tree
and nothing in it can fail a wake: every write is best-effort and swallows
OSError, exactly as the code it replaces did.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath

import loam_config as lc

# Relative to the vault root. The machinery never hardcodes an absolute
# vault: bin/wake and bin/rc-preflight both read it out of agent.json, so a
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

# The wiki-link the comms line points at. Obsidian resolves by basename, so
# this keeps working if the file moves again.
FAILURE_LOG_LINK = "[[loam-wake-failures]]"

SECTIONS = ("Urgent", "Important", "Other")

# A comms line is a snippet, never the explanation. The failure reasons run
# to 400 characters of relayed stderr; the link is what carries the rest.
SNIPPET_CHARS = 200

FAILURE_LOG_HEADER = """\
# Loam wake failures

Appended by `agents/bin/wake`, `agents/bin/wake-failed` and
`agents/bin/rc-preflight` when a scheduled wake does not complete or a Remote
Control server starts with a broken layer. A run of these starting on the same
night across agents means the OAuth token expired. Each one also leaves a line
under **Urgent** in `comms.md`, which is where they get read -- except a run
the account refused (`account_refused`), which shares one line a night with
every other refused run, counted. A line that says
"a warning, not a refusal" is the exception: the run went ahead, nothing
reached `comms.md`, and the same warning is written at most once a night.

"""

COMMS_HEADER = """\
# From the agents

> This is where the agents bring something to my attention outside the conversation
> we were in. **Urgent** is what I need before my next look here, or before a deadline.
> **Important** is what I should know this week. **Other** is when I get to it. One line
> per item — enough to make me want it, never the whole thing; if I want more I ask in
> chat. Nothing pings me. I come and look. Lines older than 14 days are swept.

## Urgent

## Important

## Other
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


def comms_line(agent: str, text: str, link: str, when: date | None = None) -> str:
    """The one line format. Date first, because the sweep reads it."""
    stamp = (when or datetime.now(timezone.utc).date()).isoformat()
    return f"- {stamp} · **{agent}** · {text} · {link}\n"


def append_comms(
    vault: Path, agent: str, text: str, link: str,
    section: str = "Urgent", when: date | None = None,
) -> bool:
    """Put one line at the top of a section. True if it was written.

    Newest first inside a section, so the line goes immediately under the
    heading rather than at the end of the file.

    Deduplicated on (agent, text) across the whole file, regardless of date or
    section: this outlet is not a log, and a wake that fails the same way for
    four nights is one item. The log beside it carries the count.
    """
    path = vault / COMMS
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = path.read_text(encoding="utf-8") if path.exists() else COMMS_HEADER
    except OSError:
        return False

    marker = f"**{agent}** · {text} ·"
    if marker in body:
        return False

    line = comms_line(agent, text, link, when)
    lines = body.splitlines(keepends=True)
    heading = f"## {section}"
    for i, existing in enumerate(lines):
        if existing.strip() != heading:
            continue
        # Land directly on top of the section's existing items, keeping the
        # one blank line that separates them from the heading. Inserting the
        # blank instead of preserving it splits the section into a loose list
        # with a gap between every item.
        cursor = i + 1
        if cursor < len(lines) and not lines[cursor].strip():
            cursor += 1
        else:
            lines.insert(cursor, "\n")
            cursor += 1
        lines.insert(cursor, line)
        # An empty section is `## Urgent`, a blank, then the next heading, so
        # the first line into it lands flush against that heading. Give it the
        # blank line back; an item following an item does not need one.
        following = lines[cursor + 1] if cursor + 1 < len(lines) else ""
        if following.strip() and not following.lstrip().startswith(("-", "*", "+")):
            lines.insert(cursor + 1, "\n")
        break
    else:
        # No such section. Rather than drop the line, give it one.
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.extend(["\n", f"{heading}\n", "\n", line])

    try:
        path.write_text("".join(lines), encoding="utf-8")
    except OSError:
        return False
    return True


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
    """Make a failed wake visible where the owner actually looks.

    The OAuth token is one-year with no documented renewal flow, and its
    expiry stops every agent's wake on the same night. A failure that only
    exists in a log file on a VPS is a failure nobody finds for a week, so
    this lands one line in the vault inbox -- one file, appended, not one
    note per failure -- and one line under Urgent in comms.md so the log has
    a reader.
    """
    _append_log(vault, agent, reason, now_iso())
    append_comms(vault, agent, snippet(reason), FAILURE_LOG_LINK)


# The account refusing is one item a night, not one per lane (the owner read
# seven wrong diagnoses on 2026-09-15 before the right one). The line is the
# runtime's, not any agent's: the lane that happened to be refused first is
# not the subject of it.
ACCOUNT_POSTER = "loam"
ACCOUNT_PREFIX = "the account refused runs from"
_SKIPPED_RE = re.compile(r"; (\d+) lanes? skipped")


def account_text(since: str, reason: str, count: int) -> str:
    """``since`` is the first refused run's ISO stamp; HH:MMZ is shown."""
    lanes = "lane" if count == 1 else "lanes"
    return (f"{ACCOUNT_PREFIX} {since[11:16]}Z ({snippet(reason)}); "
            f"{count} {lanes} skipped")


def record_account_refusal(
    agent: str, vault: Path, reason: str, provider: str,
    stamp: str | None = None,
) -> None:
    """A lane the provider refused: its log line, and the night's one line.

    ``reason`` is the lane's whole entry for the log; ``provider`` is what
    the provider said, which is all the comms line quotes.

    The failure log gets the lane's entry as every failure does. In comms.md
    the first refusal of a UTC date posts the line under Urgent; each later
    one that date raises the count on that line instead of posting. Best-
    effort like everything here.
    """
    stamp = stamp or now_iso()
    _append_log(vault, agent, reason, stamp)
    path = vault / COMMS
    try:
        body = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError:
        return
    tonight = f"- {stamp[:10]} · **{ACCOUNT_POSTER}** · {ACCOUNT_PREFIX} "
    lines = body.splitlines(keepends=True)
    for i, line in enumerate(lines):
        hit = _SKIPPED_RE.search(line) if line.startswith(tonight) else None
        if hit:
            count = int(hit.group(1)) + 1
            lines[i] = _SKIPPED_RE.sub(f"; {count} lanes skipped", line, count=1)
            try:
                path.write_text("".join(lines), encoding="utf-8")
            except OSError:
                pass
            return
    append_comms(vault, ACCOUNT_POSTER, account_text(stamp, provider, 1),
                 FAILURE_LOG_LINK, when=date.fromisoformat(stamp[:10]))


def record_notice(agent: str, vault: Path, reason: str, key: str) -> bool:
    """One warning line in the log, at most once a night. No comms line.

    ``key`` is what makes two notices the same notice -- the name of the
    file being warned about, in practice. The count in the text changes from
    night to night, so the text itself cannot be the identity. "Once a night"
    is once per UTC date of the stamp, which the wake grid (09:00-12:00 UTC)
    and the 01:30 PT recycle (08:30 UTC) both fall inside; a second Remote
    Control start the same night is exactly the repeat this suppresses.

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

    comms.md only when ``repeated_vault_write`` -- the caller has seen a
    denied vault write tonight and on each of the two nights before. The
    line is deduplicated like any other, so the fourth night adds nothing.
    """
    _append_log(vault, agent, reason, now_iso())
    if repeated_vault_write:
        append_comms(vault, agent, VAULT_WRITE_REFUSED, FAILURE_LOG_LINK,
                     section="Important")
