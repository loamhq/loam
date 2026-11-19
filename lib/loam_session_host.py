"""The resident provider sessions, their admission, records and idle reaper."""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import sys
import time
import uuid
from contextlib import contextmanager

import loam_broker as cfg
from loam_provider_session import BrokerError, Session, session_class


@contextmanager
def own_session_store():
    """Only one process can admit sessions or write their journals in a store."""
    cfg.SESSION_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(cfg.SESSION_DIR, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"another session host owns {cfg.SESSION_DIR}") from exc
        yield
    finally:
        os.close(fd)


NUMBER_WORDS = ("no", "one", "two", "three", "four", "five", "six", "seven",
                "eight", "nine", "ten", "eleven", "twelve")

BUSY_WORDS = {"working": "mid-turn", "needs_input": "waiting on you",
              "waiting": "waiting on work it started",
              "starting": "starting up"}


def number_word(n: int) -> str:
    return NUMBER_WORDS[n] if 0 <= n < len(NUMBER_WORDS) else str(n)


def join_names(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def busy_clause(busy: list[dict]) -> str:
    """``Ada and Iris are mid-turn, Pax is waiting on you``: who holds the
    seats, by what they are doing, in a person's words."""
    groups: dict[str, list[str]] = {}
    for row in busy:
        word = BUSY_WORDS.get(row.get("status"), "busy")
        name = cfg.agent_card(row["agent"])["name"]
        groups.setdefault(word, [])
        if name not in groups[word]:
            groups[word].append(name)
    parts = [f"{join_names(names)} {'is' if len(names) == 1 else 'are'} {word}"
             for word, names in groups.items()]
    return ", ".join(parts)


def cap_sentence(agent: str, busy: list[dict], reason: str = "memory") -> str:
    """What a person reads when a start cannot be admitted (2026-11-09): the
    box is short of memory and every session on it is busy, so nothing idle
    could be ended for it. No route and no id: the `hint` beside it carries
    those for an API caller."""
    who = cfg.agent_card(agent)["name"]
    clause = busy_clause(busy)
    n = len(busy)
    held = (f"all {number_word(n)} session{'s' if n != 1 else ''} on it "
            f"{'are' if n != 1 else 'is'} busy" if n else "nothing on it can be ended")
    why = ("the box is short of memory" if reason == "memory"
           else f"the box is at its backstop of {cfg.MAX_SESSIONS} sessions")
    return (f"There's no room for {who} right now: {why} and {held}"
            f"{f' ({clause})' if clause else ''}. Try again when one "
            f"finishes.")


class SessionHost:
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}
        # Starts that have been admitted and whose process is not up yet:
        # each counts SESSION_START_MB against the memory it was admitted to,
        # and one against the backstop, so two starts that arrive together
        # cannot both take the last room (2026-11-09).
        self._starting: set[str] = set()
        # Everyone watching the live list. Sessions come and go; a subscriber
        # here is watching the whole box, so it is held by the broker and not
        # by any one session.
        self.record_subscribers: set[asyncio.Queue] = set()
        # The resident count this broker has last told the live list about,
        # so that `broker.residency` is sent when the number moves and not on
        # every record write -- a title, a pin and a seen mark are all saves
        # and none of them is a session arriving in or leaving RAM. Zero is
        # the truth at start: a broker with no processes.
        self._announced_live = 0
        # Set by the first SIGTERM or SIGINT (2026-11-10): from then on no
        # session starts, and a thread turn queued behind says so.
        self.stopping = False
        cfg.SESSION_DIR.mkdir(parents=True, exist_ok=True)

    def live(self) -> list[Session]:
        """The sessions with a process behind them right now -- the resident
        set, which is what `MAX_SESSIONS` is a cap on.

        Asked of each session's own process (`Session.is_live`) rather than of
        its `state` field, so the count here and the `live` on each record are
        one answer to one question and cannot disagree for a single write.
        """
        return [s for s in self.sessions.values() if s.is_live()]

    def residency(self) -> dict:
        """How many sessions are in RAM right now and how many may be.

        Two fields, so a page can draw "2 live" without counting rows --
        which it could not do correctly anyway: `?agent=` narrows the rows and
        the count is the whole box's. Since 2026-11-09 `cap` is only the
        sanity backstop (`cfg.MAX_SESSIONS`, 30 on 8 GB, 123 on 32 GB):
        admission is by memory (`Broker.room`), so a page should not draw
        "of 30" as a limit anybody meets. `cap` is answered rather than configured
        into the app for the same reason: one number on this box, read from
        `lib/loam_broker.py`, and no copy anywhere that could disagree.
        """
        return {"live_count": len(self.live()), "cap": cfg.MAX_SESSIONS}

    def residency_event(self) -> dict:
        """The same two fields as a stream item, for the live list."""
        return {"type": "broker.residency", **self.residency()}

    def announce_residency(self) -> None:
        """Tell the live list the count moved, if it did.

        Hung off `record_changed` -- which is hung off `Session.save()` --
        because every arrival and departure is a record being written: a
        process starting, `_reap` when one exits, and `stop()` when one is
        ended or reaped for idleness. A residency change that saved nothing
        would be a screen that never heard, so the coupling is the guarantee
        and not a convenience.
        """
        count = len(self.live())
        if count == self._announced_live:
            return
        self._announced_live = count
        event = self.residency_event()
        for queue in list(self.record_subscribers):
            queue.put_nowait(event)

    def adopt(self, session: Session) -> Session:
        """Wire a session's record writes to the live list."""
        session.on_record_change = self.record_changed
        return session

    def close_record_streams(self) -> None:
        """End every live-list stream: the broker is stopping.

        A ``None`` after whatever is already queued, so a client is sent the
        records the stop itself wrote -- each chat ending -- and then the
        stream returns. Called by ``amain`` after the sessions stop, so the
        stop does not rest on ``asyncio.run`` cancelling a handler that a
        connected client is keeping busy (the 10-14 hang)."""
        for queue in list(self.record_subscribers):
            queue.put_nowait(None)

    def record_changed(self, session: Session) -> None:
        """A record was written: hand it to everyone watching the live list.

        The same ``broker.session`` snapshot the per-session stream sends, so
        a client has one shape to read and not two. Archived records are sent
        too, deliberately: a page that filters them out still has to be told
        the moment one becomes archived, and an update it never receives is
        a row that stays on the screen.
        """
        snapshot = session.snapshot()
        for queue in list(self.record_subscribers):
            queue.put_nowait(snapshot)
        # The row's own `live` is on that snapshot; this is the box's count
        # against the cap, which no single row can be read for. Sent after
        # the record, so a client applies the row and then the total.
        self.announce_residency()

    def load(self, session_id: str) -> Session:
        """A session as an object, live if it is and off disk if it is not.

        Pin, archive and rename have to work on a session whose process ended
        weeks ago -- that is most of what is on the home screen -- so they
        cannot be routes that only a running session answers.

        **And it is where an adopted record is brought up to date** (added
        2026-09-21): every route that reaches a record comes through here, so
        one call refreshes it against the app's own file for all of them --
        `GET` on the record, the listing, and opening the thread's stream. It
        costs a stat on an adopted record and nothing at all on any other; the
        file is only re-read when it has grown. See
        `Session.refresh_from_source`.

        A session an object already exists for gets that object. Building a
        second one for a running id would be two writers of one record file.
        """
        session = self.sessions.get(session_id)
        if session is not None:
            session.refresh_from_source()
            return session
        path = cfg.SESSION_DIR / f"{session_id}.json"
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise BrokerError(404, f"no session {session_id}")
        # Not put into self.sessions: it has no process, it must not count
        # against the cap, and the file is the state. The Session constructor
        # reads the record back, which is what makes this an object and not a
        # dict with methods bolted on.
        session = self.adopt(session_class(meta.get("provider"))(
            meta.get("agent") or "", session_id))
        session.refresh_from_source()
        return session

    async def start_session(self, agent: str, session_id: str | None = None,
                            resume: bool = False,
                            provider: str | None = None,
                            model: str | None = None,
                            effort: str | None = None) -> Session:
        if self.stopping:
            # The 11-10 stop hung on a start begun after SIGTERM (2026-11-10).
            raise BrokerError(503, "the broker is stopping; start the session "
                                   "again once it is back")
        if agent not in cfg.roster():
            raise BrokerError(404, f"no agent named {agent!r}")
        # A record keeps the provider it was started on, and a resume that
        # names none is a resume on that one. A conversation cannot move
        # between providers: neither CLI can read the other's thread.
        stored = None
        if session_id:
            try:
                stored = json.loads((cfg.SESSION_DIR / f"{session_id}.json")
                                    .read_text(encoding="utf-8")).get("provider")
            except (OSError, json.JSONDecodeError):
                stored = None
        if provider and stored and provider != stored:
            raise BrokerError(
                409, f"session {session_id} runs on {stored}; a conversation "
                     f"cannot move to {provider}. Start a new session instead.")
        provider = provider or stored or cfg.DEFAULT_PROVIDER
        if provider not in cfg.WIRED_PROVIDERS:
            known = ", ".join(cfg.PROVIDERS)
            wired = ", ".join(cfg.WIRED_PROVIDERS)
            raise BrokerError(
                400,
                f"provider {provider!r} is not wired: {wired} are the values "
                f"this broker accepts today. The providers it knows about are "
                f"{known}, and GET /v1/providers says which are connected.")
        # Its own process, if it has one, before admission: a session that is
        # already in RAM needs no room to be used (2026-11-09; a thread member
        # whose session was live was refused at the cap the other way round).
        if session_id and session_id in self.sessions:
            existing = self.sessions[session_id]
            if existing.state == "running":
                return existing
        await self.make_room(agent)
        reserved = session_id or f"starting-{uuid.uuid4()}"
        self._starting.add(reserved)
        try:
            return await self._start(agent, session_id, resume, provider,
                                     model, effort)
        finally:
            self._starting.discard(reserved)

    def seats_taken(self) -> int:
        """Sessions in RAM and starts in flight: what the backstop counts."""
        return len(self.live()) + len(self._starting)

    def room(self) -> dict:
        """Whether one more session fits, as admission reads it: the box's
        MemAvailable and the broker cgroup's room under its MemoryMax
        (`cfg.memory`), each less `SESSION_START_MB` per start in flight,
        against the reserve; and the count against the backstop."""
        mem = cfg.memory()
        pending = len(self._starting) * cfg.SESSION_START_MB
        need = cfg.SESSION_START_MB
        available = mem.get("available")
        reserve = mem.get("reserve", cfg.MEMORY_RESERVE_MB)
        box_ok = available is None or available - pending - reserve >= need
        cg = mem.get("cgroup_room")
        cg_ok = cg is None or cg - pending >= need
        count_ok = self.seats_taken() < cfg.MAX_SESSIONS
        return {"ok": box_ok and cg_ok and count_ok,
                "reason": None if box_ok and cg_ok and count_ok
                else ("backstop" if box_ok and cg_ok else "memory"),
                "available_mb": available, "cgroup_room_mb": cg,
                "reserve_mb": reserve, "starting": len(self._starting),
                "need_mb": need}

    async def make_room(self, agent: str) -> None:
        """Admission (2026-11-09): nothing while there is room (`room`).
        Short of it, end the least recently used live session that is idle
        (`Session.busy` is None and nothing for `cfg.EVICT_GRACE_S`), thread
        or classic -- its id resumes, so ending it costs its next turn a
        respawn -- and ask again. Only when nothing idle is left is the start
        refused, in a person's sentence (`error`), with the route-level
        advice in `hint`, the box's numbers in `memory` and who holds it in
        `busy`."""
        while True:
            room = self.room()
            if room["ok"]:
                return
            now = time.time()
            idle = sorted((s for s in self.live() if s.busy() is None
                           and now - s.last_activity >= cfg.EVICT_GRACE_S),
                          key=lambda s: s.last_activity)
            if not idle:
                raise self.cap_error(agent, room)
            victim = idle[0]
            print(f"room: ending {victim.agent}/{victim.id} (idle "
                  f"{int(now - victim.last_activity)} s) for {agent}: "
                  f"{room['reason']}, {room['available_mb']} MB available, "
                  f"reserve {room['reserve_mb']}", file=sys.stderr, flush=True)
            await victim.stop()

    def cap_error(self, agent: str, room: dict | None = None) -> BrokerError:
        room = room or self.room()
        busy = [{"session_id": s.id, "agent": s.agent,
                 "status": s.busy() or "starting"} for s in self.live()]
        reason = room.get("reason") or "memory"
        return BrokerError(
            429, cap_sentence(agent, busy, reason),
            {"reason": reason, "cap": cfg.MAX_SESSIONS,
             "live_count": len(self.live()), "busy": busy,
             "memory": {k: room.get(k) for k in
                        ("available_mb", "cgroup_room_mb", "reserve_mb",
                         "need_mb", "starting")},
             "hint": (f"{len(self.live())} sessions are running and every one "
                      f"is busy, and "
                      + (f"the box has {room.get('available_mb')} MB available "
                         f"against a {room.get('reserve_mb')} MB reserve and "
                         f"{room.get('need_mb')} MB to start one"
                         if reason == "memory" else
                         f"the backstop is {cfg.MAX_SESSIONS} sessions")
                      + ". Try again when one finishes, or end one with "
                        "DELETE /v1/sessions/<id>; its id still resumes.")})

    async def _start(self, agent: str, session_id: str | None, resume: bool,
                     provider: str, model: str | None,
                     effort: str | None) -> Session:
        session = self.adopt(session_class(provider)(
            agent, session_id, provider=provider, model=model, effort=effort))
        # In the table BEFORE the process starts, and this is load-bearing
        # (caused 2026-09-25). The resident count is read off this table, and
        # `start()` writes the record -- which fans the new session's own
        # arrival out to the live list. Registered afterwards, as it was,
        # that fan-out ran while the table still said the session was not
        # there, and every count an open list was told was one behind: four
        # starts against a cap of four announced 1, 2, 3.
        #
        # A start that fails leaves the table exactly as it found it. A
        # session whose process never started is not one the broker has.
        prior = self.sessions.get(session.id)
        self.sessions[session.id] = session
        try:
            await session.start(resume=resume)
        except Exception:
            if prior is None:
                self.sessions.pop(session.id, None)
            else:
                self.sessions[session.id] = prior
            raise
        return session

    async def reap_idle(self) -> None:
        while True:
            await asyncio.sleep(60)
            await self.reap_once(time.time())

    async def reap_once(self, now: float) -> None:
        """One pass of the reaper. **A session that is asking is not idle**
        (2026-10-05): one with a request pending is never ended here, however
        old its `last_activity`; a request that has waited
        `PERMISSION_WAIT_S` is denied instead, and the idle clock runs again
        from that answer. Until then a raised card was the last sign of life
        the reaper saw, and it ended the chat an hour later.

        One session's failure is printed and the pass goes on: an exception
        out of here would end the reaper task for the life of the broker."""
        for session in self.live():
            try:
                if session.pending:
                    for request_id in await session.expire_permissions(now):
                        print(f"idle: expired {request_id} in "
                              f"{session.agent}/{session.id}",
                              file=sys.stderr, flush=True)
                    continue
                idle = now - session.last_activity
                # Idle and not busy past its time -- `THREAD_IDLE_S` for a
                # thread member's, `CLASSIC_IDLE_S` for a chat -- a session
                # leaves memory, and its id resumes at the next message.
                # A busy one -- mid-turn, waiting on work it launched -- is
                # never ended here, however long since its last event: the
                # hour backstop that ended every session went 2026-11-10
                # ("a turn has no clock").
                short = session.idle_timeout_s or cfg.CLASSIC_IDLE_S
                if idle > short and session.busy() is None:
                    print(f"idle: ending {session.agent}/{session.id}",
                          file=sys.stderr, flush=True)
                    await session.stop()
            except Exception as exc:  # noqa: BLE001 - the next pass retries
                print(f"idle: {session.agent}/{session.id}: {exc!r}",
                      file=sys.stderr, flush=True)
        try:
            await self.relieve(now)
        except Exception as exc:  # noqa: BLE001 - the next pass retries
            print(f"pressure: {exc!r}", file=sys.stderr, flush=True)

    async def relieve(self, now: float) -> Session | None:
        """Under pressure, one idle session leaves memory per pass
        (2026-11-09): when the box's MemAvailable is below the reserve,
        sessions growing has eaten what the system was kept, and the least
        recently used idle one is ended before the kernel has to choose."""
        mem = cfg.memory()
        available = mem.get("available")
        if available is None or available >= mem.get("reserve", cfg.MEMORY_RESERVE_MB):
            return None
        idle = sorted((s for s in self.live() if s.busy() is None
                       and now - s.last_activity >= cfg.EVICT_GRACE_S),
                      key=lambda s: s.last_activity)
        if not idle:
            return None
        print(f"pressure: ending {idle[0].agent}/{idle[0].id}, {available} MB "
              f"available against a {mem.get('reserve')} MB reserve",
              file=sys.stderr, flush=True)
        await idle[0].stop()
        return idle[0]
