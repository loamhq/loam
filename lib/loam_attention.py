"""Queue system faults for the configured system persona. Deduplicate facts by key and
acknowledge them only after successful delivery. A persona's own failed run goes to
its next turn. When no persona can run, retain the fact for retry. A reporting
failure must never fail the caller.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import loam_config as lc

STATE = Path(os.environ.get("LOAM_ATTENTION_DIR") or lc.STATE / "attention")
SEEN = "seen.json"
QUIET_DAYS = 3
FACT_CHARS = 1200
DETAIL_CHARS = 6000
KEY_CHARS = 200
# The host settings file's key (state/broker/settings.json), beside the
# engineers' and the reviewer's.
SETTING = "system"
LANE = "attention"
RETRY_MIN = 15
RETRY_MAX_H = 6


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Which persona hears.
# ---------------------------------------------------------------------------

def _host() -> dict:
    import loam_engineer  # noqa: PLC0415 -- stdlib-only; the path's home
    try:
        data = json.loads(loam_engineer.host_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def chosen() -> str | None:
    """The owner's choice as stored, or None for the default."""
    value = _host().get(SETTING)
    persona = value.get("persona") if isinstance(value, dict) else None
    if not isinstance(persona, str) or not persona:
        return None
    import loam_agents  # noqa: PLC0415
    return loam_agents.resolve_id(persona)


def awake() -> list[str]:
    import loam_threads  # noqa: PLC0415
    return loam_threads.roster()


def default_persona(people: list[str] | None = None) -> str | None:
    """The first agent: the install's lead agent (``lc.LEAD_AGENT``, following
    any rename) when it is awake, else the first awake agent by name. None when
    none is."""
    import loam_agents  # noqa: PLC0415
    people = awake() if people is None else people
    lead = loam_agents.resolve_id(lc.LEAD_AGENT)
    if lead in people:
        return lead
    return people[0] if people else None


def persona() -> str | None:
    """The persona that hears of the system's problems now: the owner's
    choice while that agent is awake, else the default."""
    people = awake()
    mine = chosen()
    if mine and mine in people:
        return mine
    return default_persona(people)


def state() -> dict:
    """What Settings shows: ``{"persona", "chosen", "default"}``."""
    people = awake()
    return {"persona": persona(), "chosen": chosen(),
            "default": default_persona(people)}


def validate(value) -> str | None:
    """A persona id to store, or None for the default. Raises ValueError."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("`persona` is an agent's id, or null for the first agent")
    people = awake()
    value = value.strip().lower()
    if value not in people:
        raise ValueError(f"{value} is not an agent awake on this box"
                         + (f"; awake: {', '.join(people)}" if people else ""))
    return None if value == default_persona(people) else value


# ---------------------------------------------------------------------------
# Telling.
# ---------------------------------------------------------------------------

def key_of(fact: str) -> str:
    """The fact's identity when the caller gave none: its first sentence,
    numbers out, so a count or a percentage moving is the same fact."""
    flat = " ".join(str(fact).split())
    head = re.split(r"(?<=[.!?])\s", flat, maxsplit=1)[0]
    return re.sub(r"\d+", "#", head)[:KEY_CHARS]


def _live_state_dir() -> Path:
    """The host install's live attention directory, independent of LOAM_CONFIG."""
    return lc.install_state_dir() / "attention"


def _is_live_state(path: Path | None = None) -> bool:
    target = (STATE if path is None else path).resolve()
    live = _live_state_dir().resolve()
    return target == live or live in target.parents


def _guard_state(what: str = "attention store", path: Path | None = None) -> None:
    """Refuse to read or write the host install's live attention store from a test."""
    import loam_offline  # noqa: PLC0415
    if loam_offline.offline() and _is_live_state(path):
        target = STATE if path is None else path
        raise loam_offline.Offline(
            f"refused in a test: {what} points at the live store ({target}); "
            "run through tests/run-all or set LOAM_ATTENTION_DIR to a temp folder")


def _load_seen() -> dict:
    try:
        data = json.loads((STATE / SEEN).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(path: Path, data: dict) -> None:
    _guard_state("attention write", path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


@contextmanager
def _queue_lock():
    _guard_state("attention store")
    STATE.mkdir(parents=True, exist_ok=True)
    fd = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def withdraw(keys: set[str]) -> None:
    """Cancel these queued notices, including any retry of a claimed notice."""
    if not keys:
        return
    _guard_state("attention withdraw")
    if not STATE.is_dir():
        return
    with _queue_lock():
        cancelled = [r for r in pending() if r.get("key") in keys]
        if cancelled:
            done(cancelled)
            seen = _load_seen()
            for record in cancelled:
                seen.pop(record["key"], None)
            _write(STATE / SEEN, seen)


def tell(fact: str, *, by: str = "loam", key: str | None = None,
         evidence: str | None = None, detail: str | None = None,
         kick: bool = True) -> str:
    """Hand the system persona a fact. Returns ``told`` (a record was
    written and the drain started), ``repeat`` (the same key was told and
    has not been quiet for ``QUIET_DAYS``), or ``failed: ...``. Never
    raises.

    ``fact`` is what happened, in plain words. ``by`` is who says so (a
    program's or a plugin's name). ``evidence`` is where to look: a path, a
    log, a command. ``detail`` is longer text the fact comes with, kept
    as written (an update's notice, which says what its release asks)."""
    try:
        _guard_state("attention write")
        fact = " ".join(str(fact).split())[:FACT_CHARS]
        if not fact:
            return "failed: no fact"
        ident = (key or key_of(fact))[:KEY_CHARS]
        ident = f"{by}:{ident}"
        now = time.time()
        quiet = QUIET_DAYS * 86400
        with _queue_lock():
            import loam_claim_start
            if loam_claim_start.unclaimed():
                return "unclaimed"
            seen = _load_seen()
            seen = {k: v for k, v in seen.items()
                    if isinstance(v, dict) and now - float(v.get("last", 0)) < quiet}
            prior = seen.get(ident)
            if prior is not None:
                prior["last"] = now
                prior["count"] = int(prior.get("count", 1)) + 1
                _write(STATE / SEEN, seen)
                return "repeat"
            seen[ident] = {"told": now, "last": now, "count": 1}
            _write(STATE / SEEN, seen)
            at = now_iso()
            rid = f"{at.replace(':', '').replace('-', '')}-" + \
                hashlib.sha1(f"{ident}{now}".encode()).hexdigest()[:8]
            _write(STATE / f"{rid}.json", {"id": rid, "at": at, "by": by,
                                           "key": ident, "fact": fact,
                                           "evidence": evidence or None,
                                           "detail": (str(detail).strip()[:DETAIL_CHARS]
                                                      if detail else None)})
    except Exception as exc:  # noqa: BLE001 -- telling never fails its caller
        return f"failed: {exc}"
    if kick:
        try:
            import loam_results  # noqa: PLC0415
            who = persona()
            if who:
                loam_results.kick(who)
        except Exception:  # noqa: BLE001 -- the sweep starts it within 5 min
            pass
    return "told"


def pending() -> list[dict]:
    """Facts not yet heard, oldest first. A record being heard carries
    ``given``; one found ``given`` later belonged to a run that died after
    its input went in, and is not given again (``stale_given``)."""
    _guard_state("attention read")
    import loam_claim_start
    if loam_claim_start.unclaimed():
        return []
    since = loam_claim_start.claimed_at()
    out = []
    try:
        paths = sorted(STATE.glob("*.json"))
    except OSError:
        return out
    for path in paths:
        if path.name == SEEN:
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and record.get("id") and record.get("fact"):
            if str(record.get("at") or "")[:19] >= since:
                out.append(record)
    return sorted(out, key=lambda r: (str(r.get("at")), str(r.get("id"))))


def mark_given(records: list[dict]) -> list[dict]:
    stamp = now_iso()
    claimed = []
    with _queue_lock():
        for record in records:
            path = STATE / f"{record['id']}.json"
            if path.exists():
                _write(path, {**record, "given": stamp})
                claimed.append(record)
    return claimed


def due(records: list[dict], at: float | None = None) -> list[dict]:
    """The records a drain hands over now: not given, and past any retry
    time a failed run set."""
    at = time.time() if at is None else at
    return [r for r in records if not r.get("given")
            and float(r.get("retry_at") or 0) <= at]


def waiting(records: list[dict], at: float | None = None) -> list[dict]:
    """The records a drain has work for now: due ones, and given ones (a
    dead run's, which the drain logs and removes)."""
    return [r for r in records if r.get("given")] + due(records, at)


def failed(records: list[dict], at: float | None = None) -> None:
    """The run they were handed failed: they were not heard. Each goes back
    to pending, to be handed over again after its next retry time."""
    at = time.time() if at is None else at
    with _queue_lock():
        for record in records:
            if not (STATE / f"{record['id']}.json").exists():
                continue
            tries = int(record.get("tries") or 0) + 1
            wait = min(RETRY_MAX_H * 3600, RETRY_MIN * 60 * 2 ** (tries - 1))
            clean = {k: v for k, v in record.items() if k != "given"}
            try:
                _write(STATE / f"{record['id']}.json",
                       {**clean, "tries": tries, "retry_at": at + wait})
            except (OSError, KeyError):
                pass


def done(records: list[dict]) -> None:
    if not records:
        return
    _guard_state("attention done")
    for record in records:
        try:
            (STATE / f"{record['id']}.json").unlink(missing_ok=True)
        except (OSError, KeyError):
            pass


def input_text(records: list[dict]) -> str:
    """The run's input: each fact, when, who says so, where the evidence
    is. Facts only; what to do is the persona's."""
    import loam_clock  # noqa: PLC0415
    lines = [
        f"[Loam: {'a fact' if len(records) == 1 else f'{len(records)} facts'} "
        f"about this install, for you as the agent that hears when something "
        f"in Loam needs attention (Settings › Agents). "
        f"Nobody is in a conversation, and nothing has been posted anywhere. "
        f"You decide what to do with each fact. You can act on it, tell the owner, "
        f"or record it in a thread, your memory or another place you choose. "
        f"Each is told once; a repeat is not told again until it has been "
        f"quiet for {QUIET_DAYS} days.]", ""]
    for record in records:
        when = loam_clock.local_stamp(str(record.get("at") or ""))
        lines.append(f"- {record['fact']}")
        lines.append(f"  When: {when}. From: {record.get('by') or 'loam'}."
                     + (f" Evidence: {record['evidence']}." if record.get("evidence") else ""))
        if record.get("detail"):
            lines.append("  It came with:")
            lines.extend(f"    {line}" if line else "" for line in
                         str(record["detail"]).splitlines())
    return "\n".join(lines) + "\n"
