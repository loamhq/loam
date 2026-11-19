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
from pathlib import Path

import loam_adopt as adoption
import loam_broker as cfg
from loam_provider_session import BrokerError, Session, first_words, parse_iso, session_class
from loam_session_records import backfill_records, normalise, prune_fork_duplicates, sync_titles


@contextmanager
def own_store(path, owner):
    """Only one process can admit sessions or write their journals in a store."""
    path.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"another {owner} owns {path}") from exc
        yield
    finally:
        os.close(fd)


def own_session_store():
    return own_store(cfg.SESSION_DIR, "session host")


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
        # When a scan for app sessions last ran, so that a read of the list can
        # trigger one without paying for it on every read. Set by the scan
        # itself, which is also what makes the start-up scan count.
        self._scanned = 0.0
        # Starts that have been admitted and whose process is not up yet:
        # each counts SESSION_START_MB against the memory it was admitted to,
        # and one against the backstop, so two starts that arrive together
        # cannot both take the last room (2026-11-09).
        self._starting: set[str] = set()
        self._opening: dict[str, tuple[str, str, asyncio.Task]] = {}
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
        if session_id and session_id in self._opening:
            owner, engine, task = self._opening[session_id]
            if owner != agent or (provider and provider != engine):
                raise BrokerError(409, "session is starting for another agent or provider")
            return await asyncio.shield(task)
        if session_id in self._starting:
            # A fresh start has published its generated id but has not
            # returned it to its caller yet. Only one launch can own it.
            raise BrokerError(409, "the session is still starting")
        if session_id:
            try:
                record = json.loads((cfg.SESSION_DIR / f"{session_id}.json").read_text(encoding="utf-8"))
                if record.get("agent") and record["agent"] != agent:
                    raise BrokerError(409, "session belongs to another agent")
                stored = record.get("provider")
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
        sid = session_id or str(uuid.uuid4())

        async def open_session():
            try:
                if self.stopping:
                    raise BrokerError(503, "the broker is stopping")
                await self.make_room(agent)
                if self.stopping:
                    raise BrokerError(503, "the broker is stopping")
                self._starting.add(sid)
                return await self._start(agent, sid, resume, provider, model, effort)
            finally:
                self._starting.discard(sid)
                self._opening.pop(sid, None)

        if session_id is None:
            # A first start has no caller-supplied key. Hand its result to
            # the caller inline, before queued provider events are pumped.
            return await open_session()

        # Register before yielding, including while making room. A lost
        # caller can rejoin the same launch without taking another seat.
        task = asyncio.create_task(open_session())
        self._opening[sid] = (agent, provider, task)
        # A caller can leave before a failed launch returns its error.
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        return await asyncio.shield(task)

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

    def prepare_records(self) -> None:
        """Repair and adopt the catalog once at host startup, before serving."""
        # Before a byte is served: every record the box has, filled from its own
        # log. A record written before 2026-09-18 has no title and no last
        # message, and a home screen drawn from those records is a list of rows
        # all reading "New chat" with no status box -- which is what it was.
        seen, changed = backfill_records()
        print(f"backfill: {changed} of {seen} records filled from their logs",
              file=sys.stderr, flush=True)
        # And every record's title made the vault's title, for the records the
        # broker's own titler named before it was taken out (2026-09-22). Once,
        # here, because a name is not something that keeps changing.
        try:
            seen, renamed = sync_titles()
            print(f"titles: {len(renamed)} of {seen} records took the vault's title",
                  file=sys.stderr, flush=True)
            for session_id, was, now in renamed:
                print(f"titles: {session_id[:8]} {was!r} -> {now!r}",
                      file=sys.stderr, flush=True)
        except Exception as exc:  # noqa: BLE001 - never the reason a broker is down
            print(f"titles: the sync failed ({exc!r})", file=sys.stderr, flush=True)
        # And the records a scan made of this broker's own forks, before the scan
        # below runs: between 09-20 and 09-22 a reply into an adopted session left
        # a second record of the same conversation, and the home screen drew both.
        try:
            gone = prune_fork_duplicates()
            print(f"prune: {len(gone)} duplicate records of a fork removed",
                  file=sys.stderr, flush=True)
            for session_id, title in gone:
                print(f"prune: {session_id} {title!r}",
                      file=sys.stderr, flush=True)
        except Exception as exc:  # noqa: BLE001 - never the reason a broker is down
            print(f"prune: the pass failed ({exc!r})", file=sys.stderr, flush=True)
        # And every session the owner had in the Claude Code app, which are Claude
        # Code sessions on this box that the broker simply did not know about.
        # At start as well as on the route, because the app is still where some
        # of his conversations happen and a restart is the cheapest moment to
        # catch up. Fail-soft: a scan that goes wrong is reported and the broker
        # still serves, because everything it already holds is unaffected.
        try:
            report = self.adopt_app_sessions()
            per_agent = ", ".join(f"{agent} {n}"
                                  for agent, n in report["agents"].items())
            print(f"adopt: {len(report['adopted'])} app sessions adopted "
                  f"({per_agent or 'none new'}), "
                  f"{len(report['refreshed'])} refreshed",
                  file=sys.stderr, flush=True)
        except Exception as exc:  # noqa: BLE001 - never the reason a broker is down
            print(f"adopt: the scan failed ({exc!r})", file=sys.stderr, flush=True)

    def _settled(self) -> set[str]:
        """Ids the adoption scan must leave exactly as they are.

        Everything the broker started itself, and every adopted session that
        has somehow been written into since -- a record with a log of this
        broker's own has numbering that is load-bearing, and a rescan that
        re-counted the file under it would renumber events a client has
        already seen. Since the fork landed (2026-09-20) that is no longer
        hypothetical: the first message into an adopted session gives it a
        log of this broker's own, and from that moment the app's file is its
        history and nothing else.

        **The log, not the position field.** `record_seq` was what this asked
        until the replay landed on 2026-09-20; since then an adopted record
        carries the translated count there from the moment it is made, so
        asking `record_seq` would have settled every adopted session at its
        first scan and frozen it. The existence of a log of our own is the
        question that was always meant.

        An adopted record nobody has written into is deliberately NOT here:
        rescanning it is how a conversation the owner is still having in the
        app keeps its last message, its place on the home screen and the
        turns he has taken since.
        """
        settled = set()
        for path in sorted(cfg.SESSION_DIR.glob("*.json")):
            try:
                meta = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                settled.add(path.stem)      # unreadable: touch nothing
                continue
            session_id = meta.get("session_id") or path.stem
            log = cfg.SESSION_DIR / f"{session_id}.jsonl"
            if meta.get("origin") != adoption.ORIGIN_APP:
                settled.add(session_id)
            elif log.exists() and log.stat().st_size:
                settled.add(session_id)
        return settled

    def _forks_and_heads(self) -> tuple[set[str], dict[str, str]]:
        """The two things a scan needs so it does not adopt a fork of its own.

        Added 2026-09-22, after the home screen drew one conversation twice.
        A reply into an adopted session forks it, the CLI writes the fork into
        a session file of its own in the same folder, and that file has a
        person in it and today's date on it -- so the scan adopted it as a
        second conversation wearing the first one's title and history.

        - **the fork ids**: every record's ``cli_session_id``. Free, read out
          of records the scan is opening anyway.
        - **the heads**: the first uuid of the file behind each record with a
          source (``loam_adopt.first_uuid``), against the id of the record it
          belongs to, because a fork copies its parent's entries verbatim,
          uuids and all. This is the net under a fork whose id no record
          carries -- one the CLI minted for a turn that died before the broker
          read the id back. The id is kept beside the uuid so that a record's
          own file, which a full rescan re-reads on purpose, is not read as a
          copy of itself.

        Measured on this box 2026-09-22: 49 records, 30 of them with a source,
        **24 ms** warm and 41 ms cold, against the 240 ms of the cheapest scan
        it runs inside. The heads it built collided on exactly four pairs, and
        those four were the four duplicate records this landed to remove.
        """
        forks: set[str] = set()
        heads: dict[str, str] = {}
        for path in sorted(cfg.SESSION_DIR.glob("*.json")):
            try:
                meta = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            fork = meta.get("cli_session_id")
            if fork:
                forks.add(fork)
            session_id = meta.get("session_id") or path.stem
            source = meta.get("source")
            if source:
                head = adoption.first_uuid(Path(source))
                if head:
                    heads.setdefault(head, session_id)
        return forks, heads

    def _recorded(self) -> set[str]:
        """Every id the broker has a record of at all.

        What a scan that is only looking for conversations it has never seen
        passes as `held`: the records it already has are kept current by the
        refresh on read, one stat each, so re-reading all 27 of their files
        would be 0.69 s spent to learn what 0.36 ms of stats already said.
        """
        return {path.stem for path in cfg.SESSION_DIR.glob("*.json")}

    def _source_moved(self, meta: dict) -> bool:
        """Has the file behind this record been touched since it was read.

        The cheap question the listing asks of a record it has not loaded: one
        stat, and only for a record whose history is in somebody else's file.
        A record with a log of this broker's own, a forked one and everything
        the broker started itself answer False without a syscall.

        True for a touch as well as for growth, because the two are told apart
        by `Session.refresh_from_source` -- which is the one place that rule
        lives -- and a touch is answered there without a parse.
        """
        if meta.get("origin") != adoption.ORIGIN_APP or meta.get("cli_session_id"):
            return False
        source = meta.get("source")
        session_id = meta.get("session_id")
        if not source or not session_id:
            return False
        log = cfg.SESSION_DIR / f"{session_id}.jsonl"
        try:
            if log.stat().st_size:
                return False
        except OSError:
            pass
        size, mtime = adoption.source_stat(Path(source))
        if size is None:
            return False
        return (size, mtime) != (meta.get("source_size"),
                                 meta.get("source_mtime"))

    def scan_for_new(self) -> dict | None:
        """Look for app sessions the broker has never seen, on a read of the
        list, at most once a minute. None if it was not due.

        The owner started a conversation on their phone (Remote Control, until
        2026-11-19) and expected to find it in Loam UI; before this it appeared at the next broker restart, which
        could be days (2026-09-21). A read of the list is the moment to look,
        because it is the moment somebody is looking.

        **Only what is unknown**, which is what makes it affordable on a read:
        `_recorded` holds every id back, so nothing already adopted is
        re-parsed -- the refresh on read does that job now, and better, per
        record and only when its file moved. Measured on this box 2026-09-21:
        0.24 s over 576 session files, against 0.93 s for the full rescan the
        route and the restart still run.

        Fail-soft and on the loop. A scan that goes wrong is reported and the
        list is answered anyway; 0.24 s once a minute is a pause every open
        stream pays and no reader can see, and a thread would buy that back at
        the price of writing records from one.
        """
        if time.time() - self._scanned < cfg.SCAN_INTERVAL_S:
            return None
        try:
            return self.adopt_app_sessions(new_only=True)
        except Exception as exc:  # noqa: BLE001 - never the reason a list fails
            self._scanned = time.time()
            print(f"scan: the read-triggered scan failed ({exc!r})",
                  file=sys.stderr, flush=True)
            return None

    def adopt_app_sessions(self, new_only: bool = False) -> dict:
        """Every persona's Claude Code app sessions, as records of this box.

        Idempotent, which is what makes it safe both at start and on the
        route: a session already held is either settled (left alone) or
        refreshed from its file, and nothing is ever adopted twice.

        **A fork of an adopted session is never adopted**, which is the other
        half of "nothing is adopted twice" and was missing until 2026-09-22:
        the fork is a session file like any other, and the conversation in it
        is one this broker already holds. `_forks_and_heads` is the two nets.

        `new_only` narrows it to files no record exists for, which is what a
        read of the list triggers (`scan_for_new`): the records that do exist
        are kept in step by the refresh on read. Without it this is the full
        rescan -- every adopted record nobody has written into re-read from its
        file -- which is what the route and every broker start still do.

        The records are written through `Session.save()` rather than straight
        to disk, so the live list hears about them the moment they exist --
        a page open while this runs gains the rows without reloading.
        """
        self._scanned = time.time()
        settled = self._recorded() if new_only else self._settled()
        forks, heads = self._forks_and_heads()
        report: dict = {"adopted": [], "refreshed": [], "agents": {},
                        "skipped": [], "window_days": adoption.WINDOW_DAYS,
                        "new_only": new_only}
        for agent in cfg.roster():
            workspace = cfg.workspace(agent)
            found, passed = adoption.candidates(agent, workspace, settled,
                                                forks=forks, heads=heads)
            for note in passed:
                report["skipped"].append({"agent": agent, **note})
            adopted = 0
            for path in found:
                try:
                    row = self._adopt_one(agent, path)
                except OSError as exc:
                    report["skipped"].append(
                        {"agent": agent, "session_id": path.stem,
                         "why": f"unreadable ({exc})"})
                    continue
                if row is None:
                    report["skipped"].append(
                        {"agent": agent, "session_id": path.stem,
                         "why": "no thread in the file"})
                    continue
                new, record = row
                report["adopted" if new else "refreshed"].append(record)
                adopted += new
            if adopted:
                report["agents"][agent] = adopted
        return report

    def _adopt_one(self, agent: str, path: Path) -> tuple[bool, dict] | None:
        """One app session as a record. None if the file holds no thread."""
        session_id = path.stem
        # Stat'ed BEFORE the file is read, and the direction matters: if the
        # app appends during the read, the record understates the size and the
        # next read of it sees growth and reads again. Stat'ing afterwards
        # would have the record claim a size it never counted, and the turn
        # that landed in between would be invisible until the next one.
        stat = adoption.source_stat(path)
        summary = adoption.summarise(path, session_id, agent)
        if not summary["messages"]:
            return None
        new = not (cfg.SESSION_DIR / f"{session_id}.json").exists()
        session = self.adopt(Session(agent, session_id))
        session.origin = adoption.ORIGIN_APP
        session.source = str(path)
        # What the file will replay, and the number the next process numbers
        # from. Set together and from one count, because a record that
        # promised a different number than the stream delivers is a record
        # whose next turn overwrites the end of its own history.
        session.replayed = summary["events"]
        session.seq = summary["events"]
        # The reading this record is, with its two numbers on it, so that the
        # next read of the record can tell whether the conversation has moved
        # on since. See `Session.refresh_from_source`.
        session.source_size, session.source_mtime = stat
        session.created = summary["created"] or session.created
        session.last_event_at = summary["last_event_at"]
        session.last_activity = parse_iso(summary["last_event_at"],
                                          session.last_activity)
        if summary["last_message"]:
            session.last_message = summary["last_message"][
                :cfg.LAST_MESSAGE_CHARS]
        # The transcript writer's title if it filed one, else the opening
        # words -- the same stand-in every other session wears. A title
        # already on the record (a person's rename) outranks both.
        if not session.title:
            filed = adoption.transcript_title(session_id)
            session.title = filed or first_words(summary["opening"] or "") or None
            session.title_source = "vault" if filed else None
        # Done looking only if the name came from somewhere that outranks the
        # stand-in. One still wearing the opening words is looked at again the
        # next time its file is read, because the writer may file it later.
        session._titled = session.title_source in ("vault", "person")
        # No process of this broker's is behind it. It is a conversation that
        # happened, and `done` is what that is.
        session.state = "exited"
        session._restored_status = "done"
        session.save()
        return new, {"session_id": session_id, "agent": agent,
                     "title": session.title,
                     "messages": summary["messages"],
                     "created": session.created,
                     "last_event_at": session.last_event_at}

    def known(self, agent: str | None = None) -> list[dict]:
        """Every session this broker has a record of, live or not, archived
        or not (the classic list's archive filter retired with it on
        2026-11-12; Running now always asked for the archive too), newest
        activity first. Read off disk so a restarted broker still lists what
        it started before it restarted -- those processes are gone, but their
        ids resume, and a client that cannot see them cannot resume them.

        **An adopted record is stat'ed here and re-read if it has grown**
        (2026-09-21). The rows the owner is still talking to in the Claude Code
        app are exactly the rows whose last message goes stale, and the home
        screen is where he reads them. Measured on this box: 27 adopted
        records, 0.36 ms of stats for the lot of them, against 0.69 s to
        re-read all 27 files -- which is why the stat decides and the parse
        happens only for the file that moved. See
        `Session.refresh_from_source`.
        """
        out: dict[str, dict] = {}
        for path in sorted(cfg.SESSION_DIR.glob("*.json")):
            try:
                meta = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if meta.get("session_id") in self.sessions:
                meta = self.sessions[meta["session_id"]].meta()
            elif self._source_moved(meta):
                try:
                    meta = self.load(meta["session_id"]).meta()
                except BrokerError:
                    # The record went away between the glob and the read; it is
                    # not on the list, which is the truth about it.
                    continue
            else:
                if meta.get("state") == "running":
                    # This broker did not start it, so it is not running now.
                    meta["state"] = "exited"
                if "seq" in meta:
                    # A record written before 2026-09-16 calls the position
                    # `seq`; a file is only rewritten when its session is
                    # resumed, so the listing answers under one name rather
                    # than handing a client two spellings of one field and
                    # a None for the sessions it has not touched since.
                    meta["record_seq"] = meta.pop("seq")
                meta = normalise(meta)
            out[meta["session_id"]] = meta
        records = list(out.values())
        if agent:
            records = [r for r in records if r.get("agent") == agent]
        records.sort(key=lambda r: r.get("last_activity", ""), reverse=True)
        return records
