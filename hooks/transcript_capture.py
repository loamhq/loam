#!/usr/bin/env python3
"""File the visible exchange on Stop or SessionEnd. See Transcript capture in docs/logs.md."""

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
    """Choose a numbered suffix on collisions, including on case-insensitive sync targets."""
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
    """Recover the existing path by the first turn's timestamp when the sidecar is missing."""
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
    conversations, quarantined = lt.destination_dir(Path(config["vault"]))

    # Stop can arrive before the provider flushes the final reply.
    if not lt.await_settled(jsonl):
        log(f"{session_id}: session file still growing at "
            f"{lt.SETTLE_LIMIT_S}s; parsing it as it stands")

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
        # A fork preserves its first timestamp. Reuse the filename to preserve links.
        adopted = _find_by_created_at(conversations, turns[0].timestamp)
        if adopted is not None:
            target = adopted
            log(f"{session_id}: adopted {target.name} (sidecar lost)")

    if target is None:
        # Wait for an exchange worth naming. The filename is permanent.
        if len(turns) < lt.TITLE_MIN_TURNS:
            log(f"{session_id}: deferring creation, only {len(turns)} turn(s)")
            sys.exit(0)
        # File the exchange even if the title provider fails unexpectedly.
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
        # Only the launcher knows which interface started the session.
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
        # A turn may grow after capture without increasing the turn count.
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
