"""Read, repair and migrate the session host's on-disk records."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import loam_adopt as adoption
import loam_broker as cfg
import loam_transcript
from loam_provider_session import first_words, history_bytes, recovered_status, with_context


def normalise(meta: dict) -> dict:
    """Fill missing fields on read so clients can use records written by older versions."""
    meta["status"] = recovered_status(meta.get("status"), meta.get("turns") or [])
    meta.setdefault("provider", cfg.DEFAULT_PROVIDER)
    meta.setdefault("title", None)
    # An unknown title source remains eligible for transcript title sync.
    meta.setdefault("title_source", None)
    meta.setdefault("last_message", None)
    meta.setdefault("last_event_at", None)
    # Records without an origin predate adoption and were started by the broker.
    meta.setdefault("origin", adoption.ORIGIN_UI)
    meta.setdefault("source", None)
    # Adopted records count translated source events.
    meta.setdefault("replayed", None)
    # Missing measurements leave the source eligible for a fresh scan.
    meta.setdefault("source_size", None)
    meta.setdefault("source_mtime", None)
    # Forks can run under a different provider session ID.
    meta.setdefault("cli_session_id", None)
    meta.setdefault("forked_from", None)
    # Codex resumes by provider thread ID.
    meta.setdefault("thread_id", None)
    # A missing override inherits the persona’s model or effort.
    meta.setdefault("model", None)
    meta.setdefault("effort", None)
    meta["pinned"] = bool(meta.get("pinned"))
    meta["archived"] = bool(meta.get("archived"))
    meta["seen_seq"] = int(meta.get("seen_seq") or 0)
    # A disk-only record has no resident process or surviving background tasks.
    meta["background_tasks"] = 0
    meta["live"] = False
    # Derive replay size from the same source measurement as the record.
    meta["history_bytes"] = history_bytes(meta.get("origin"),
                                          meta.get("cli_session_id"),
                                          meta.get("source_size"))
    meta["turns"] = [with_context(turn) for turn in (meta.get("turns") or [])
                     if isinstance(turn, dict)]
    return meta


def from_log(path: Path) -> dict:
    """Recover the first human message, last top-level reply, result and highest
    event sequence from the journal. Ignore tool results when choosing a title."""
    found = {"title": None, "last_message": None, "result_ok": None,
             "last_seq": 0}
    if not path.exists():
        return found
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            seq = event.get("seq")
            if isinstance(seq, int) and seq > found["last_seq"]:
                found["last_seq"] = seq
            if found["title"] is None and kind == "broker.user_message":
                found["title"] = first_words(event.get("text") or "") or None
            elif found["title"] is None and kind == "user":
                content = (event.get("message") or {}).get("content")
                # Provider user-role events can contain tool results.
                if not (isinstance(content, list) and any(
                        isinstance(block, dict)
                        and block.get("type") == "tool_result"
                        for block in content)):
                    found["title"] = first_words(
                        loam_transcript._text_of(content)) or None
            elif kind == "assistant" and event.get("parent_tool_use_id") is None:
                said = loam_transcript._text_of(
                    (event.get("message") or {}).get("content"))
                if said:
                    found["last_message"] = said[:cfg.LAST_MESSAGE_CHARS]
            elif kind == "result":
                found["result_ok"] = not (
                    bool(event.get("is_error"))
                    or (event.get("subtype") or "success") != "success")
    return found


def _tail_last_seq(path: Path, chunk: int = 65536) -> int:
    """Return the last integer ``seq`` in ``path`` by reading backward from EOF.
    Avoids re-parsing multi-gigabyte journals on startup when catalog fields are
    already present."""
    try:
        size = path.stat().st_size
    except OSError:
        return 0
    if size <= 0:
        return 0
    try:
        with path.open("rb") as fh:
            pos = size
            buf = b""
            while pos > 0:
                step = min(chunk, pos)
                pos -= step
                fh.seek(pos)
                buf = fh.read(step) + buf
                parts = buf.split(b"\n")
                if pos > 0:
                    complete, buf = parts[1:], parts[0]
                else:
                    complete, buf = parts, b""
                for raw in reversed(complete):
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        event = json.loads(raw)
                    except (UnicodeDecodeError, ValueError):
                        continue
                    if not isinstance(event, dict):
                        continue
                    seq = event.get("seq")
                    if isinstance(seq, int) and not isinstance(seq, bool):
                        return seq
    except OSError:
        return 0
    return 0


def backfill(meta: dict, log_path: Path) -> bool:
    """Fill missing fields without overwriting stored titles, replies or status.
    Advance the sequence to the journal maximum so a resumed session cannot reuse it."""
    before = dict(meta)
    if meta.get("title") and meta.get("last_message") and meta.get("status"):
        last_seq = _tail_last_seq(log_path)
    else:
        found = from_log(log_path)
        if not meta.get("title") and found["title"]:
            meta["title"] = found["title"]
        if not meta.get("last_message") and found["last_message"]:
            meta["last_message"] = found["last_message"]
        # The journal result distinguishes success from failure when status is missing.
        if not meta.get("status") and found["result_ok"] is not None:
            meta["status"] = "done" if found["result_ok"] else "failed"
        last_seq = found["last_seq"]
    position = int(meta.get("record_seq") or meta.get("seq") or 0)
    meta.pop("seq", None)
    meta["record_seq"] = max(position, last_seq)
    return meta != before


def backfill_records() -> tuple[int, int]:
    """Repair records at startup so listing them does not rescan the journals.
    Report per-file failures and return the numbers seen and changed."""
    seen = changed = 0
    for path in sorted(cfg.SESSION_DIR.glob("*.json")):
        seen += 1
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"backfill: {path.name}: unreadable ({exc})",
                  file=sys.stderr, flush=True)
            continue
        session_id = meta.get("session_id") or path.stem
        if not backfill(meta, cfg.SESSION_DIR / f"{session_id}.jsonl"):
            continue
        try:
            path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        except OSError as exc:
            print(f"backfill: {path.name}: not written ({exc})",
                  file=sys.stderr, flush=True)
            continue
        changed += 1
    return seen, changed


def sync_titles() -> tuple[int, list[tuple[str, str, str]]]:
    """Sync titles from transcript sidecars unless the person set the title.
    Return the old and new titles for the startup log."""
    seen = 0
    changed: list[tuple[str, str, str]] = []
    for path in sorted(cfg.SESSION_DIR.glob("*.json")):
        seen += 1
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"titles: {path.name}: unreadable ({exc})",
                  file=sys.stderr, flush=True)
            continue
        if meta.get("title_source") == "person":
            continue
        session_id = meta.get("session_id") or path.stem
        # The sidecar is keyed by the ID under which the provider filed the turns.
        filed = adoption.transcript_title(
            meta.get("cli_session_id") or meta.get("thread_id") or session_id)
        if not filed or filed == meta.get("title"):
            if filed and meta.get("title_source") != "vault":
                meta["title_source"] = "vault"
            else:
                continue
        else:
            changed.append((session_id, meta.get("title") or "", filed))
            meta["title"] = filed
            meta["title_source"] = "vault"
        try:
            path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        except OSError as exc:
            print(f"titles: {path.name}: not written ({exc})",
                  file=sys.stderr, flush=True)
    return seen, changed


def prune_fork_duplicates() -> list[tuple[str, str]]:
    """Remove duplicate app records for forks driven by another record. Preserve
    records with their own fork or a nonempty journal, and keep the provider’s
    session history. Return each removed record’s ID and title."""
    records: dict[str, dict] = {}
    for path in sorted(cfg.SESSION_DIR.glob("*.json")):
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"prune: {path.name}: unreadable ({exc})",
                  file=sys.stderr, flush=True)
            continue
        records[meta.get("session_id") or path.stem] = meta
    driven = {meta.get("cli_session_id") for meta in records.values()
              if meta.get("cli_session_id")}
    removed: list[tuple[str, str]] = []
    for session_id in sorted(driven & set(records)):
        meta = records[session_id]
        if meta.get("origin") != adoption.ORIGIN_APP:
            continue
        if meta.get("cli_session_id"):
            continue
        log = cfg.SESSION_DIR / f"{session_id}.jsonl"
        try:
            if log.exists() and log.stat().st_size:
                continue                # spoken into here; not a bare copy
        except OSError:
            continue
        try:
            (cfg.SESSION_DIR / f"{session_id}.json").unlink()
            # An empty log of our own beside it is the broker's, not the
            # CLI's, and a log with no record is reachable by nothing.
            log.unlink(missing_ok=True)
        except OSError as exc:
            print(f"prune: {session_id}: not removed ({exc})",
                  file=sys.stderr, flush=True)
            continue
        removed.append((session_id, meta.get("title") or ""))
    return removed
