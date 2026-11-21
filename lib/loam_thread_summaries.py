"""Debounce private handoffs per thread member. Only real turns make a handoff due."""
import asyncio
import hashlib
import time

import loam_agents
import loam_session_fence as fence
import loam_thread_steps as steps
import loam_threads as threads


def prior(key, saved=None):
    tid, persona = key
    note = threads.read_summary(persona, tid)
    digest = hashlib.sha256((note or "").encode()).hexdigest()
    if saved is None:
        saved = threads.load_state("handoffs.json").get(tid, {}).get(persona, {})
    # A member can still replace its own workspace note between sessions.
    if saved.get("workspace_digest") == digest:
        return saved["text"], saved["through"], digest
    return note, None, digest


def prompt_for(key, meta, messages, previous, through):
    # A fork already knows the session. Include new thread text for providers
    # without a private fork and for messages that arrived after the last reply.
    newest = []
    remaining = 20000
    for message in reversed(messages):
        if message.get("id") == through or remaining <= 0:
            break
        line = f"{message['author']} ({message['id']}): {threads._said(message)}"
        newest.append(line[:remaining])
        remaining -= len(line)
    return (f"This is private thread housekeeping for {key[1]} in {meta['title']}. "
            "Return only a short handoff for your own next session, in your own voice. "
            "Preserve the purpose, decisions, reasons, unfinished work and details "
            "worth remembering. Use the supplied history as material. Respond with "
            "the handoff text only. Do not call tools, post, or edit any files.\n\n"
            f"Previous handoff:\n{(previous or '(none)')[:threads.SUMMARY_CHARS]}\n\n"
            "Recent messages:\n" + "\n\n".join(reversed(newest)))


class Summaries:
    def __init__(self, engine):
        self.engine = engine
        self.closed = False
        self.timers = {}
        self.running = {}

    def arm(self, key, due):
        if self.closed:
            return
        old = self.timers.pop(key, None)
        if old:
            old.cancel()
        loop = asyncio.get_running_loop()
        self.timers[key] = loop.call_later(max(0, due - time.time()), self.start, key)

    def preempt(self, key):
        task = self.running.get(key)
        if task and not task.done():
            task.cancel()
            self.arm(key, time.time() + threads.PAUSE_S)

    def restore(self):
        saved = threads.load_state("handoffs.json")
        for tid, members in threads.load_state("sessions.json").items():
            for persona, kept in members.items():
                # The API may have stopped just after the checkpoint rename,
                # before clearing the due marker in the session binding.
                last = saved.get(tid, {}).get(persona, {})
                if "summary_due" in kept and not self.covered(kept, last):
                    self.arm((tid, persona), kept["summary_due"])

    @staticmethod
    def covered(row, saved):
        return (row["session_id"] == saved.get("session_id") and
                row["summary_due"] == saved.get("source_due"))

    def expedite(self, key):
        if key and "summary_due" in (threads.session_for(*key) or {}):
            self.arm(key, time.time())

    def start(self, key):
        self.timers.pop(key, None)
        if key not in self.running and not getattr(self.engine.broker, "stopping", False):
            task = asyncio.create_task(self.run(key))
            self.running[key] = task
            # Cancellation can arrive before run() enters its try block.
            task.add_done_callback(lambda done: self.finished(key, done))

    def finished(self, key, task):
        if self.running.get(key) is task:
            del self.running[key]

    async def run(self, key):
        tid, persona = key
        try:
            row = threads.session_for(*key) or {}
            if "summary_due" not in row or loam_agents.is_archived(persona, threads.AI_LABS):
                return
            worker = self.engine.workers.get(key)
            live = self.engine.live_session(tid, persona)
            if (worker and not worker.done()) or self.engine.background_of(live):
                self.arm(key, time.time() + threads.PAUSE_S)
                return
            meta, messages, _ = threads.read(tid)
            if persona not in meta["members"] or not messages:
                return
            saved = threads.load_state("handoffs.json").get(tid, {}).get(persona, {})
            if self.covered(row, saved):
                return
            prior_text, through, digest = prior(key, saved)
            prompt = prompt_for(key, meta, messages, prior_text, through)
            sid = row["session_id"]
            with fence.admission() as admitted:
                if not admitted:
                    self.arm(key, time.time() + 60)
                    return
                steps.log(f"{tid} {persona}: private summary requested ({row['engine']}, session {sid[:8]})")
                text = await self.engine.broker.private_summary(sid, prompt)
            if not text or len(text) > threads.SUMMARY_CHARS:
                raise ValueError("empty or oversized handoff")
            with threads.locked(threads.VAULT):
                sessions = threads.load_state("sessions.json")
                current = sessions.get(tid, {}).get(persona, {})
                if (current.get("session_id"), current.get("summary_due")) != (sid, row["summary_due"]):
                    return
                saved = threads.load_state("handoffs.json")
                saved.setdefault(tid, {})[persona] = {
                    "text": text, "through": messages[-1]["id"], "at": threads.now_iso(),
                    "session_id": sid, "source_due": row["summary_due"], "workspace_digest": digest}
                threads.save_state("handoffs.json", saved)
                del current["summary_due"]
                threads.save_state("sessions.json", sessions)
            steps.log(f"{tid} {persona}: private summary saved through {messages[-1]['id']} ({row['engine']})")
        except asyncio.CancelledError:
            if key not in self.timers:
                self.arm(key, time.time() + 60)
            steps.log(f"{tid} {persona}: private summary deferred")
            raise
        except Exception as exc:
            # Admission failures are transient. A failed provider turn keeps the
            # last good handoff and waits for a new turn instead of billing in a loop.
            if getattr(exc, "status", None) in (429, 503):
                self.arm(key, time.time() + 60)
            steps.log(f"{tid} {persona}: private summary deferred ({exc})")

    async def close(self):
        self.closed = True
        for timer in self.timers.values():
            timer.cancel()
        self.timers.clear()
        tasks = list(self.running.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
