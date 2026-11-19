"""API-side session views. Only the host admits, configures and stops providers."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
import uuid

from loam_provider_session import BrokerError
from loam_session_socket import HostClient


class RemoteSession:
    def __init__(self, client, view):
        self.client = client
        self.subscribers = set()
        self.pump = None
        self.events = []
        self.closed = False
        self.apply(view)
        self.seq = self.record["record_seq"]

    def apply(self, view):
        self.record = view["record"]
        self.id = self.record["session_id"]
        self.incarnation = view["incarnation"]
        self.permissions = view["permissions"]
        self.background_tasks = view["background"]["tasks"]
        self._behind = view["behind"]
        self._busy = view["busy"]
        self._init_model = view["running_model"]
        self._task_seen = view["task_times"]
        self.STEERS = view["steers"]
        self.last_activity = view["last_activity"]
        self.proc = SimpleNamespace(pid=view["pid"]) if view["pid"] else None
        self.update_record(self.record)

    def update_record(self, record):
        self.record = record
        for key in ("agent", "model", "provider", "effort", "state", "turns", "thread_id",
                    "cli_session_id", "last_event_at", "title"):
            setattr(self, key, record.get(key))

    def meta(self):
        return self.record

    def status(self):
        return self.record["status"]

    def busy(self):
        return self._busy

    def is_live(self):
        return self.record["live"]

    def working_behind(self):
        return self._behind

    def pending_events(self):
        return self.permissions

    def task_started_at(self, task_id):
        return self._task_seen.get(task_id)

    async def control(self, op, **fields):
        return await self.client.call(op, session_id=self.id, incarnation=self.incarnation, **fields)

    async def configure_thread(self, title, model, effort):
        self.apply(await self.control("configure", title=title, model=model, effort=effort))

    async def answer_permission(self, request_id, behavior, message, updated_input, answers, updates):
        decision = {"behavior": behavior, "message": message, "updatedInput": updated_input,
                    "answers": answers, "updatedPermissions": updates}
        self.apply(await self.control("answer", request_id=request_id, decision=decision))

    async def stop(self, *, idle=False):
        self.apply(await self.control("stop", idle=idle))

    async def dispatch(self, key, text, attachments=None, *, steer=False):
        # A lost reply is ambiguous until the host answers for the same key.
        while True:
            try:
                return await self.control("dispatch", id=key, text=text,
                                          attachments=attachments, steer=steer)
            except (OSError, ConnectionError):
                await asyncio.sleep(.25)

    async def history(self, since):
        result = []
        async with self.client.request("watch", session_id=self.id, since=since) as reader:
            while True:
                frame = await self.client.receive(reader)
                if "caught_up" in frame:
                    return result
                if "event" in frame and "seq" in frame["event"]:
                    result.append(frame["event"])

    async def attach(self, since):
        # Each turn has its own reader. Keep just its replay until the
        # worker has installed its queue, then forward new events directly.
        self.seq = since
        ready = asyncio.get_running_loop().create_future()
        self.pump = self.client.reader_task(self._read(ready))
        try:
            await ready
        except BaseException:
            self.pump.cancel()
            await asyncio.gather(self.pump, return_exceptions=True)
            raise

    async def _read(self, ready):
        retry = .1
        try:
            while True:
                try:
                    async with self.client.request("watch", session_id=self.id, since=self.seq) as reader:
                        while True:
                            frame = await self.client.receive(reader)
                            if "behind" in frame:
                                self._behind = frame["behind"]
                            if "attached" in frame:
                                view = frame["attached"]
                                if view["incarnation"] != self.incarnation and view["record"]["state"] == "running":
                                    raise BrokerError(409, "session binding changed while reading")
                                self.apply(view)
                            elif "event" in frame:
                                self.event(frame["event"])
                            elif "caught_up" in frame:
                                retry = .1
                                if not ready.done():
                                    ready.set_result(None)
                            elif "ended" in frame:
                                return
                except (OSError, BrokerError) as exc:
                    if isinstance(exc, BrokerError) and exc.status != 503:
                        raise
                    await asyncio.sleep(retry)
                    retry = min(2, retry * 2)
        except Exception as exc:
            if not ready.done():
                ready.set_exception(exc)
            else:
                for queue in self.subscribers:
                    queue.put_nowait(exc)
        finally:
            self.closed = True
            for queue in self.subscribers:
                queue.put_nowait(None)

    def event(self, event):
        seq = event.get("seq")
        if seq is not None:
            self.seq = seq
            if not self.subscribers:
                self.events.append(event)
        if event["type"] == "broker.session":
            self.update_record(event)
        elif event["type"] == "broker.background_tasks":
            self.background_tasks = event["tasks"]

        elif event["type"] == "broker.permission_request":
            if not any(p["request_id"] == event["request_id"] for p in self.permissions):
                self.permissions.append(event)
        elif event["type"] == "broker.permission_resolved":
            self.permissions = [p for p in self.permissions if p["request_id"] != event["request_id"]]
        for queue in self.subscribers:
            queue.put_nowait(event)

    def replay(self, since):
        return [e for e in self.events if e["seq"] > since]

    def watch(self, queue):
        self.subscribers.add(queue)
        self.events.clear()
        if self.closed:
            queue.put_nowait(None)

    def unwatch(self, queue):
        self.subscribers.discard(queue)
        asyncio.get_running_loop().call_soon(self.release)

    def release(self):
        if not self.subscribers and self.pump:
            self.pump.cancel()


class RemoteRegistry:
    remote = True

    def __init__(self, path):
        self.client = HostClient(path)
        self.sessions = {}
        self.record_subscribers = set()
        self.stopping = False
        self._room = {}
        self._seats = 0
        self.host_fenced = False
        super().__init__()

    async def synchronize(self):
        data = await self.client.call("registry")
        self.sessions = {v["record"]["session_id"]: RemoteSession(self.client, v)
                         for v in data["sessions"]}
        self.host_fenced = data["admission_closed"]
        self.host_pid = data["pid"]
        self.host_revision = data["revision"]
        self._room, self._seats = data["room"], data["seats"]

    async def listen_registry(self, ready):
        while True:
            try:
                async with self.client.request("records") as r:
                    opening = {}
                    while True:
                        frame = await self.client.receive(r)
                        if "ready" in frame:
                            self.sessions = opening
                            ready.set()
                        elif "record" in frame:
                            event, view = frame["record"], frame["resident"]
                            if view:
                                sid = view["record"]["session_id"]
                                if view["record"]["state"] == "running":
                                    self.sessions[sid] = RemoteSession(self.client, view)
                                else:
                                    self.sessions.pop(sid, None)
                            for q in self.record_subscribers:
                                q.put_nowait(event)
                        else:
                            view = frame["resident"]
                            opening[view["record"]["session_id"]] = RemoteSession(self.client, view)
            except (OSError, BrokerError):
                self.close_record_streams()
                await asyncio.sleep(.5)

    def close_record_streams(self):
        for q in self.record_subscribers:
            q.put_nowait(None)

    def live(self):
        return [s for s in self.sessions.values() if s.is_live()]

    def room(self):
        return self._room

    def seats_taken(self):
        return self._seats

    async def catalog(self, agent=None):
        rows = []
        async with self.client.request("catalog", agent=agent) as r:
            while True:
                frame = await self.client.receive(r)
                if "ok" in frame:
                    return rows, frame["ok"]
                rows.append(frame["record"])

    async def inspect(self, sid):
        return RemoteSession(self.client, await self.client.call("inspect", session_id=sid))

    async def start_session(self, agent, session_id=None, resume=False, provider=None,
                            model=None, effort=None, start_key=None):
        sid = session_id or str(uuid.uuid5(uuid.NAMESPACE_URL, start_key) if start_key else uuid.uuid4())
        data = await self.client.call("start", session_id=sid, agent=agent, resume=resume,
                                      provider=provider, model=model, effort=effort)
        return RemoteSession(self.client, data)
