#!/usr/bin/env python3
"""Stop / SessionEnd hook: append this session to vault/15_CONVERSATIONS/.

**A transcript holds two things and nothing else: what the person typed, and
what the agent said back to them.** It is the record of a conversation as the
person saw it, so anything the app does not show by default is not in it. The
session file makes that harder than it sounds, because it uses the ``user`` role
for two different things -- what somebody typed, and whatever the harness needed
to hand the model next -- and the second class is out: a task notification
delivering a background subagent's result, a peer message from another Claude
session, a reminder the CLI wrapped around a prompt, a slash command's
expansion, the summary written when a conversation is compacted, a tool result
being fed back. Tool calls, their output and the agent's thinking are out for the
same reason. Where a user-role message MIXES the two -- the owner's sentence with
reminders wrapped around it -- only the typed text is kept; where it is nothing
but harness content it is not a turn at all, and the agent's reply to it still
is, because that reply is something the person read. The rule lives in
``loam_transcript.is_harness_prompt`` and ``loam_transcript.person_text``, is
covered by ``tests/test_transcript_harness.py``. *(the owner, 2026-09-22: "The conversation files should
be recording only the messages exchanged between the user and the agent ...
Basically I should be able to just go through that file as a record of my
conversation history.")*

Mechanical by construction. The model is never asked to log anything; the
hook reads the session JSONL at the ``transcript_path`` the hook payload
supplies -- never an assumed location -- and makes the filed transcript say
what that session says: new turns appended, and a turn that has grown since
it was written rewritten where it stands. The second half is not fussiness.
The CLI fires this hook before it has finished writing the turn's last
assistant entry, so the first read of a turn can hold the sentence said
before the tool calls and not the answer said after them. See
``loam_transcript.await_settled`` and ``loam_transcript.plan_update``.

**Titling happens once, at creation, so no file is ever renamed.** The
earlier design opened a file under a truncated first prompt and renamed it
when a title arrived. That was wrong twice over: wiki-links point at these
filenames, and a rename breaks them; and a session that never ends never
got titled, which is exactly the long-lived pinned session the operator
cares about most. Now the first firing that has a real exchange in it names
the file correctly and that name is final. Sessions with nothing but an
unanswered prompt are simply deferred to the next firing.

**The frontmatter says which surface the conversation was held on, and the
environment is the only place that can say it.** A session the broker
launched for the Loam interface is the same CLI, in the same workspace, under
the same settings and hooks as one the owner has in the Claude Code app, so
nothing in the session file tells them apart and every transcript written
before 2026-09-22 says `surface: claude-code`. The broker sets
``LOAM_SURFACE=loam-ui`` on every process it launches, and
``LOAM_ORIGIN=claude-code-app`` beside it on a session it adopted from the
app and forked; ``loam_transcript.surface_of_env`` reads them and
``render_file`` writes ``surface:`` and, when there is one, ``origin:``.
Absent means ``claude-code``, which is what an unbrokered session is.
Recorded at write time and never inferred later, which is the rule the
``surface`` field exists under.

**A fork appends turns; it never writes a second header.** Sending into a
session adopted from the app forks it, and a fork is a new CLI session id
with the parent's entries copied in -- with their own timestamps, verified on
this box across six fork pairs. So there is no sidecar for the new id,
``_find_by_created_at`` below matches the file the parent opened, and
``plan_update`` appends the turns beyond the end of it and bumps
``last_activity``. One conversation, one file, one header, whatever the CLI
calls the session today. ``tests/test_transcript_surface.py`` fires both
halves of that.

Both Stop and SessionEnd point here on purpose. Stop fires when Claude
finishes responding, SessionEnd when the session terminates, and either may
be the last thing that happens. Double delivery is harmless: a firing with
nothing to do compares the file to the session, finds them equal and writes
nothing.

``/clear`` is safe: measured 2026-08-26, the outgoing session receives its
Stop and SessionEnd with every turn intact, and the clear starts a fresh
session id which becomes its own transcript.

**Cron wakes deliberately produce no transcript.** A reflection wake is not
a conversation: nobody is on the other side of it, and its durable record is
what the agent chooses to write -- its workspace notes, the project docs, a
note left in ``commons/``. Filing wakes here would put five machine-generated
files a night into the folder every persona reads, duplicating a record that
already exists in a better place. The guard is the ``LOAM_WAKE`` check below,
which runs before anything is parsed or written, so a wake cannot reach this
path even by accident. Verified 2026-08-26 by running sessions under
``LOAM_WAKE=reflection`` and confirming zero files and zero log lines.

Probe sessions are quarantined outside the vault for a related reason -- see
``loam_transcript.destination_dir``.

**A thread's turns are not filed here once the thread keeps them itself
(2026-11-10).** A thread turn runs on a broker session like any chat, so this
hook filed it a second time as a classic transcript beside the thread record.
The broker's thread writer now keeps every reply in the thread and every step
of it in the full record (``lib/loam_thread_steps.py``), and as a turn starts
it writes ``state/threads/steps/sessions/<session id>``. A session with that
marker is skipped here, before anything is parsed. The gate is the marker and
nothing else: only the writer that keeps the full record writes it, so the
hook stops for exactly the sessions that writer is behind, and a session the
old broker ran, or one it never touched, is filed as before.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_config as lc  # noqa: E402

import loam_transcript as lt  # noqa: E402

STATE_DIR = lc.STATE / "transcripts"
THREAD_MARKERS = Path(os.environ.get("LOAM_THREADS_STATE") or lc.STATE / "threads") / "steps" / "sessions"
LOG_PATH = lc.LOGS / "transcript-capture.log"


def log(message: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")


def _resolve_path(directory: Path, title: str) -> Path:
    """First free ``<title>.md``, then ``<title> 2.md`` -- the chassis rule.

    Free means free on every device the vault syncs to, not only on this
    box. The Mac and the phone fold case, so a name that differs from an
    existing one only in case is taken: the 2026-09-16 smoke-test pair was
    two such files here and could not both exist there.
    """
    try:
        taken = {p.name.casefold() for p in directory.iterdir()}
    except OSError:
        taken = set()

    def free(candidate: Path) -> bool:
        return not candidate.exists() and candidate.name.casefold() not in taken

    candidate = directory / f"{title}.md"
    if free(candidate):
        return candidate
    n = 2
    while True:
        candidate = directory / f"{title} {n}.md"
        if free(candidate):
            return candidate
        n += 1


def _find_by_created_at(directory: Path, created_at: str) -> Path | None:
    """An existing transcript whose frontmatter created_at matches, if any.

    Recovery for a lost sidecar. ``created_at`` is the first turn's timestamp
    and is stable across re-parses, so it identifies a conversation without
    needing the sidecar -- which is what stops a second file being opened for
    a conversation that already has one.
    """
    needle = f"created_at: {created_at}"
    try:
        candidates = sorted(directory.glob("*.md"))
    except OSError:
        return None
    for path in candidates:
        try:
            if needle in path.read_text(encoding="utf-8")[:400]:
                return path
        except OSError:
            continue
    return None


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    if os.environ.get("LOAM_WAKE", "conversation").strip().lower() != "conversation":
        sys.exit(0)

    transcript_path = payload.get("transcript_path")
    session_id = payload.get("session_id")
    if not transcript_path or not session_id:
        log(f"skip: payload missing transcript_path/session_id: {payload!r}")
        sys.exit(0)

    if (THREAD_MARKERS / str(session_id)).exists():
        # A thread's session: the thread record and its full record hold it.
        log(f"{session_id}: a thread's session, kept by the thread; not filed")
        sys.exit(0)

    jsonl = Path(transcript_path)
    if not jsonl.exists():
        log(f"skip: no session file at {jsonl}")
        sys.exit(0)

    project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd", "."))
    try:
        config = json.loads((project / "agent.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log(f"skip: unreadable agent.json under {project}: {exc}")
        sys.exit(0)

    agent = config["name"]
    # No emoji in a speaker header (2026-11-17): an older agent.json's
    # retired `emoji` is not read here any more.
    conversations, quarantined = lt.destination_dir(Path(config["vault"]))

    # The CLI is still writing this turn's last assistant entry when it fires
    # this hook -- 53 ms behind, measured 2026-09-17. Parsing before the file
    # settles files an answer the person never sees. See
    # ``loam_transcript.await_settled``.
    if not lt.await_settled(jsonl):
        log(f"{session_id}: session file still growing at "
            f"{lt.SETTLE_LIMIT_S}s; parsing it as it stands")

    # A Codex thread's Stop hook names its rollout (2026-10-01). Same writer,
    # same file shape, one more frontmatter line; told apart by the file.
    codex = lt.is_codex_rollout(jsonl)
    turns = (lt.parse_codex_rollout(jsonl, agent) if codex
             else lt.parse_session(jsonl, agent))
    if not turns:
        sys.exit(0)

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_path = STATE_DIR / f"{session_id}.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}

    conversations.mkdir(parents=True, exist_ok=True)
    target = Path(state["path"]) if state.get("path") else None
    if target is not None and not target.exists():
        log(f"{session_id}: recorded file {target} is gone; looking again")
        target = None

    if target is None:
        # Sidecar lost but the file exists: adopt it. Crucially this does NOT
        # reset any title state -- an adopted transcript already has whatever
        # name it was created with, and re-titling it would rename a file that
        # wiki-links may already point at.
        adopted = _find_by_created_at(conversations, turns[0].timestamp)
        if adopted is not None:
            target = adopted
            log(f"{session_id}: adopted {target.name} (sidecar lost)")

    if target is None:
        # Creating. The name is decided now and is final, so it must be worth
        # keeping: one model call, only once there is a real exchange to name.
        if len(turns) < lt.TITLE_MIN_TURNS:
            log(f"{session_id}: deferring creation, only {len(turns)} turn(s)")
            sys.exit(0)
        # Belt and braces around the only network call in this hook. Every
        # known failure already returns None, but an unforeseen one must not
        # be allowed to escape and cost the transcript: the name is a
        # convenience, the turns are the record.
        try:
            title = ((None if codex else lt.ai_title(jsonl))
                     or lt.generate_title(turns, agent))
        except Exception as exc:  # noqa: BLE001 - deliberately total
            log(f"{session_id}: titler raised {type(exc).__name__}: {exc}")
            title = None
        titled = title is not None
        if not titled:
            title = (lt.sanitize_title(turns[0].content[:60]) if codex
                     else lt.session_title(jsonl, fallback=f"conversation with {agent}"))
            log(f"{session_id}: titling produced nothing, filed under first prompt")
        target = _resolve_path(conversations, title)
        # Where the person was sitting, said by the only party that knows:
        # the launcher. Nothing in a session file distinguishes a conversation
        # held in the Loam interface from one held in the Claude Code app --
        # same CLI, same workspace, same hooks -- so the broker sets
        # LOAM_SURFACE on every process it starts and this reads it. Absent is
        # `claude-code`, which is what an unbrokered session is.
        surface, origin = lt.surface_of_env()
        target.write_text(
            lt.render_file(
                title, turns, agent,
                session_id=session_id,
                surface=surface,
                origin=origin,
                provider="codex" if codex else None,
            ),
            encoding="utf-8",
        )
        state["titled"] = titled
        log(
            f"{session_id}: created {target.name} "
            f"({len(turns)} turn(s), titled={titled}, surface={surface}"
            + (f", origin={origin}" if origin else "") + ")"
            + (" [QUARANTINED probe]" if quarantined else "")
        )
    else:
        # Not "has the count gone up" but "does the file still say what the
        # session says". A turn the CLI had not finished writing when an
        # earlier firing read it keeps its index, so a count sees nothing to
        # do; comparing the text finds it and repairs it.
        updated, reason = lt.plan_update(
            target.read_text(encoding="utf-8"), turns, agent
        )
        if updated is not None:
            target.write_text(updated, encoding="utf-8")
        if reason != "unchanged":
            log(
                f"{session_id}: {reason} -> {target.name}"
                + (" [QUARANTINED probe]" if quarantined else "")
            )

    state_path.write_text(
        json.dumps({
            "path": str(target),
            "turns_written": len(turns),
            "titled": bool(state.get("titled", True)),
        }),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
