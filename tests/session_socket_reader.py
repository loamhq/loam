"""Test adapter for running a bound ThreadEngine against the private socket.

Starts and configuration happen in the probe host. This reader only owns
its received events. It never opens a session file or starts a provider.
"""
import asyncio

from loam_session_socket import HostClient


class BoundReader:
    def __init__(self, path, sid):
        self.client, self.id = HostClient(path), sid
        self.events, self.subscribers = [], set()
        self.ready = asyncio.Event()
        self.permissions = []
        self.seq = 0

    async def connect(self, since=0):
        self.seq = since
        self.pump = asyncio.create_task(self._read(since))
        waiting = asyncio.create_task(self.ready.wait())
        try:
            await asyncio.wait((waiting, self.pump), return_when=asyncio.FIRST_COMPLETED)
            if self.pump.done():
                await self.pump
        finally:
            waiting.cancel()

    def record(self, meta):
        self.agent, self.provider = meta["agent"], meta["provider"]
        self.STEERS = self.provider != "antigravity"
        self.state = meta["state"]
        self.model, self.effort = meta["model"], meta["effort"]
        self._status = meta["status"]
        self.turns = meta["turns"]
        self.thread_id = meta["thread_id"]

    async def _read(self, since):
        try:
            async for frame in self.client.watch(self.id, since):
                if "attached" in frame:
                    info = frame["attached"]
                    self.record(info["record"])
                    self.incarnation = info["incarnation"]
                    self.permissions = info["permissions"]
                    self.background_tasks = info["background"]["tasks"]
                elif "caught_up" in frame:
                    self.ready.set()
                elif "event" in frame:
                    event = frame["event"]
                    if "seq" in event:
                        self.seq = event["seq"]
                        self.events.append(event)
                    if event["type"] == "broker.session":
                        self.record(event)
                    elif event["type"] == "broker.background_tasks":
                        self.background_tasks = event["tasks"]
                    for sub in self.subscribers:
                        sub.put_nowait(event)
        finally:
            for sub in self.subscribers:
                sub.put_nowait(None)

    def watch(self, q):
        self.subscribers.add(q)

    def unwatch(self, q):
        self.subscribers.discard(q)

    def replay(self, since):
        return [e for e in self.events if e["seq"] > since]

    async def dispatch(self, key, text, attachments=None, steer=False):
        return await self.client.call("dispatch", session_id=self.id, incarnation=self.incarnation,
                                      id=key, text=text, attachments=attachments, steer=steer)

    def save(self):
        pass

    def status(self):
        return self._status

    def is_live(self):
        return self.state == "running"

    def pending_events(self):
        return self.permissions

    def working_behind(self):
        return bool(self.background_tasks)
