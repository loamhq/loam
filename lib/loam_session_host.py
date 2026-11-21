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
import loam_session_fence as fence
import loam_broker as cfg
import loam_private_summary
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
    """Describe who holds the sessions in a person's words. For example: "A and B are
    mid-turn, C is waiting on you".
    """
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
    """Explain the admission limit and identify the sessions holding it."""
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
        # Throttle list-triggered scans, including the scan at startup.
        self._scanned = 0.0
        # Reserve memory and a seat until each admitted process finishes starting.
        self._starting: set[str] = set()
        self._summaries: dict[str, asyncio.Task] = {}
        self._opening: dict[str, tuple[str, str, asyncio.Task]] = {}
        # Subscribers watch the whole catalog across session arrivals and departures.
        self.record_subscribers: set[asyncio.Queue] = set()
        # Publish a residency event only when the resident count changes.
        self._announced_live = 0
        # Reject new starts once shutdown begins.
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
        """Report resident sessions across all agents, even when the listing is filtered. The
        cap is a backstop. Available memory governs admission.
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
        """Reuse the resident object so one session has only one writer. Load ended sessions
        from their records and refresh adopted history when its source file changes.
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
            raise BrokerError(503, "the broker is stopping; start the session "
                                   "again once it is back")
        if agent not in cfg.roster():
            raise BrokerError(404, f"no agent named {agent!r}")
        # A record keeps the provider it was started on, and a resume that
        # names none is a resume on that one. A conversation cannot move
        # between providers: neither CLI can read the other's thread.
        stored = None
        requested = provider
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
                stored = record.get("provider") or cfg.DEFAULT_PROVIDER
            except (OSError, json.JSONDecodeError):
                stored = None
        if provider and stored and provider != stored:
            raise BrokerError(
                409, f"session {session_id} runs on {stored}; a conversation "
                     f"cannot move to {provider}. Start a new session instead.")
        # Older session records already belong to Claude Code, even if
        # they predate the provider field. A new session can inherit.
        if not provider and not stored:
            from loam_provider_defaults import SETUP  # noqa: PLC0415
            from loam_threads import persona_defaults  # noqa: PLC0415
            choice = await asyncio.to_thread(persona_defaults, agent)
            provider = choice["provider"]
            if provider is None:
                raise BrokerError(409, SETUP)
            model, effort = model or choice["model"], effort or choice["effort"]
        provider = provider or stored
        if provider not in cfg.WIRED_PROVIDERS:
            known = ", ".join(cfg.PROVIDERS)
            wired = ", ".join(cfg.WIRED_PROVIDERS)
            raise BrokerError(
                400,
                f"provider {provider!r} is not wired: {wired} are the values "
                f"this broker accepts today. The providers it knows about are "
                f"{known}, and GET /v1/providers says which are connected.")
        # Resolving a default runs off the loop. Another start may have
        # registered this ID while that read was in progress.
        if session_id in self._opening:
            owner, engine, task = self._opening[session_id]
            if owner != agent or (requested and requested != engine):
                raise BrokerError(409, "session is starting for another agent or provider")
            return await asyncio.shield(task)
        # Reusing a resident process needs no new admission.
        if session_id and session_id in self.sessions:
            existing = self.sessions[session_id]
            if existing.agent != agent or (requested and requested != existing.provider):
                raise BrokerError(409, "session belongs to another agent or provider")
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

    async def private_summary(self, sid, prompt):
        """Reserve without reclaiming a seat. Disconnecting the API cancels its private child."""
        session = self.load(sid)
        if self.stopping or session.busy() or sid in self._summaries or not self.room()["ok"]:
            raise BrokerError(429, "private summary deferred")
        task = asyncio.current_task()
        self._summaries[sid] = task
        reservation = "summary/" + sid
        self._starting.add(reservation)
        try:
            return await loam_private_summary.ask(session, prompt)
        finally:
            self._starting.discard(reservation)
            self._summaries.pop(sid, None)

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
        """While admission is short of memory, reclaim the least recently active idle session
        outside the grace period. Its ID can resume. If all remaining sessions are busy,
        return the reason, advice, memory reading and who holds them.
        """
        while True:
            room = self.room()
            if room["ok"]:
                return
            if self._summaries:
                # Foreground work gets the seat before any resident is evicted.
                task = next(iter(self._summaries.values()))
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                continue
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
        # Register before start publishes the record so subscribers see the new count.
        # Restore the prior entry if the process fails to start.
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
            with fence.admission() as admitted:
                if admitted:
                    await self.reap_once(time.time())

    async def reap_once(self, now: float) -> None:
        """Preserve sessions waiting on a decision. Deny an expired request and restart its
        idle clock from the answer. Log each session's failure and continue the pass.
        """
        for session in self.live():
            try:
                if session.pending:
                    for request_id in await session.expire_permissions(now):
                        print(f"idle: expired {request_id} in "
                              f"{session.agent}/{session.id}",
                              file=sys.stderr, flush=True)
                    continue
                idle = now - session.last_activity
                # Reap only idle sessions. Quiet time cannot expire active work.
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
        """Under memory pressure, release at most one session per pass. Choose the least
        recently active idle one.
        """
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
        # Recover missing catalog fields before serving the first listing.
        seen, changed = backfill_records()
        print(f"backfill: {changed} of {seen} records filled from their logs",
              file=sys.stderr, flush=True)
        # Preserve person-set titles while syncing the others from transcript sidecars.
        try:
            seen, renamed = sync_titles()
            print(f"titles: {len(renamed)} of {seen} records took the vault's title",
                  file=sys.stderr, flush=True)
            for session_id, was, now in renamed:
                print(f"titles: {session_id[:8]} {was!r} -> {now!r}",
                      file=sys.stderr, flush=True)
        except Exception as exc:  # noqa: BLE001 - never the reason a broker is down
            print(f"titles: the sync failed ({exc!r})", file=sys.stderr, flush=True)
        # Remove duplicate fork records before scanning for new sessions.
        try:
            gone = prune_fork_duplicates()
            print(f"prune: {len(gone)} duplicate records of a fork removed",
                  file=sys.stderr, flush=True)
            for session_id, title in gone:
                print(f"prune: {session_id} {title!r}",
                      file=sys.stderr, flush=True)
        except Exception as exc:  # noqa: BLE001 - never the reason a broker is down
            print(f"prune: the pass failed ({exc!r})", file=sys.stderr, flush=True)
        # Catch up on provider sessions at startup. A failed scan leaves the catalog available.
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
        """Exclude broker-created sessions and adopted sessions with a broker journal.
        Recounting their source would change sequence numbers already delivered to clients.
        An adopted session without its own journal can still refresh.
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
        """Detect forks by recorded CLI session ID or by the first copied entry UUID. Keep the
        original record ID beside each UUID so a rescan recognizes its own source. This also
        catches a fork whose turn ended before its ID was saved.
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
        """On a list read, periodically scan for unknown app sessions. Existing records refresh
        on access. Report a scan failure and still serve the listing.
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
        """Import app conversations once, skipping forks and records with a broker journal. A
        full scan refreshes eligible sources. A list read requests new IDs only. Save
        through the session so subscribers see new records.
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
        """List all recorded sessions, including archived and ended sessions, newest activity
        first. Refresh changed adopted sources on read so an ongoing app conversation keeps
        its place.
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
                    # Normalize the legacy sequence key on read.
                    meta["record_seq"] = meta.pop("seq")
                meta = normalise(meta)
            out[meta["session_id"]] = meta
        records = list(out.values())
        if agent:
            records = [r for r in records if r.get("agent") == agent]
        records.sort(key=lambda r: r.get("last_activity", ""), reverse=True)
        return records
