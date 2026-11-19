"""When something in Loam needs attention: a fact, handed to one persona.

Decided 2026-11-18 (batch B, "knowing it works"): a problem goes to a
persona, never to the owner and never from an engineer or a program. No
program writes a line into a thread's record or onto the General board.
The persona that hears of the system's problems is one the owner chooses
(``system.persona``, Settings › Agents; the first agent on a new install).
It decides what to do: brief the engineer, say it in General in its own
words, or nothing.

**Which faults come here.** A fault that belongs to no one persona: drift,
memory pressure, the account refusing runs, a check that failed, a plugin's
event, an update's notes, a job that hung. A persona's own failed run is
not one of them: it is told to that persona at the top of its next turn
(lib/loam_lane_notice.py), as before.

**How it is heard.** ``tell`` writes one record under ``STATE`` and starts
the system persona's drain (``loam-results-<agent>``, the same unit and
runner as a job's result for a run with no thread, lib/loam_results.py).
The drain hands every pending fact to the persona as the input of one new,
fresh run (``input_text``), under the agent's run lock and the box's memory
admission. Nothing is posted anywhere. The run's record is its row in
``logs/wake-<agent>.jsonl`` (lane ``attention``) and a fire in the schedule
log, which Activity shows.

**Once, not once per occurrence.** A fact has a key: the caller's, or the
fact's first sentence with its numbers taken out, so "RAM at 91%" and "RAM
at 93%" are the same fact. A key is told once. Each later occurrence only
moves its ``last`` time, and tells nobody, until the key has been quiet for
``QUIET_DAYS`` (no occurrence at all): then it is a new fact, told again.
A wake failing the same way every night is told once; the same failure a
week after it stopped is told again.

**Addressed when heard, not when told.** A record does not name its
persona. The drain of whichever persona is the system persona when it runs
takes it, so changing the setting moves what is still pending.

**A run that failed did not tell** (2026-11-19, batch B step 6). When the
run handed the facts fails (the account refusing it, the CLI erroring), the
records stay pending and are handed over again by a later drain, after
``RETRY_MIN`` minutes doubling each try up to ``RETRY_MAX_H`` hours
(``failed``, ``due``). Only a run that succeeded removes them. A run that
died after its input went in is the other case (``given``): it is not handed
over twice.

**When nobody can run** (the account refused, every persona archived), a
fact waits here and its runs keep failing into the failure log at that
pace. The app shows an install-wide status line when the session host
reports that agents cannot run (lib/loam_check.py).

For a plugin: ``loam_attention.tell("what happened", by="<plugin id>")`` in
Python with the runtime's ``lib/`` on the path, or ``loam tell "what
happened" --by <plugin id>`` from anywhere (docs/plugins.md).

Stdlib and loam_config only, and every write is best-effort: telling must
never be what fails a wake, a check or a plugin.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
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
    return persona if isinstance(persona, str) and persona else None


def awake() -> list[str]:
    import loam_threads  # noqa: PLC0415
    return loam_threads.roster()


def default_persona(people: list[str] | None = None) -> str | None:
    """The first agent: the install's lead agent (``lc.LEAD_AGENT``) when it
    is awake, else the first awake agent by name. None when none is."""
    people = awake() if people is None else people
    if lc.LEAD_AGENT in people:
        return lc.LEAD_AGENT
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


def _load_seen() -> dict:
    try:
        data = json.loads((STATE / SEEN).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


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
        fact = " ".join(str(fact).split())[:FACT_CHARS]
        if not fact:
            return "failed: no fact"
        ident = (key or key_of(fact))[:KEY_CHARS]
        ident = f"{by}:{ident}"
        now = time.time()
        quiet = QUIET_DAYS * 86400
        STATE.mkdir(parents=True, exist_ok=True)
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
            out.append(record)
    return sorted(out, key=lambda r: (str(r.get("at")), str(r.get("id"))))


def mark_given(records: list[dict]) -> None:
    stamp = now_iso()
    for record in records:
        try:
            _write(STATE / f"{record['id']}.json", {**record, "given": stamp})
        except (OSError, KeyError):
            pass


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
    for record in records:
        tries = int(record.get("tries") or 0) + 1
        wait = min(RETRY_MAX_H * 3600, RETRY_MIN * 60 * 2 ** (tries - 1))
        clean = {k: v for k, v in record.items() if k != "given"}
        try:
            _write(STATE / f"{record['id']}.json",
                   {**clean, "tries": tries, "retry_at": at + wait})
        except (OSError, KeyError):
            pass


def done(records: list[dict]) -> None:
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
