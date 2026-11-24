"""Run a queue for each thread member on its configured provider. Persist input
before scheduling it. Bind dispatch to a session and journal position so a
returning worker can file the reply without sending the input twice.

Members can answer concurrently. Within a member, compatible input can join
the active turn or take over a session waiting on background work. Other
input stays ordered. See the thread protocol in broker/README.md."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from itertools import groupby

import loam_agents
import loam_config as lc
import loam_lane_notice as lane_notice
import loam_provider_memory as provider_memory
import loam_broker as cfg
from loam_provider_session import configure_thread
import loam_thread_steps as steps
from loam_thread_summaries import Summaries, prior as prior_summary
import loam_session_fence as fence
import loam_thread_inputs as inputs
import loam_threads as threads
import loam_waiters

# How long a turn waits for room (memory, lib/loam_broker.py) before it gives up
# with a system line, and how often it asks. Five minutes since 2026-11-09:
# the wait is said in the thread now, and a seat is only ever this short when
# every session on the box is mid-turn or asking.
CAP_WAIT_S = 300
# System turns (2026-11-17): a notice's key is remembered this long, and the
# same line for the same agent in one thread is not given again within
# SYSTEM_REPEAT_S, under any key. The record is in the threads' state.
SYSTEM_TURNS = "system-turns.json"
# A turn's mark (2026-11-19, batch B step 5): what the app shows on a turn
# that could not run or ended without a reply, kept here and never in the
# thread's record. One per (thread, member); "" is the thread's own (nobody
# to reach). Cleared when that member's next turn starts.
MARKS = "marks.json"
SYSTEM_KEEP_S = 30 * 86400
SYSTEM_REPEAT_S = 3600
CAP_RETRY_S = 5
# A turn has no clock (2026-11-10, the owner: "I don't want any limitations at
# all"). `TURN_TIMEOUT_S` (1800) ended the watch on a turn at 30 minutes and
# posted a system line saying so while the session worked on; a turn is now
# watched until its `result`, and after it for as long as the session lives.

# What a fresh session is handed from the record (2026-11-11): the recent
# exchange (`loam_threads.recent_window`), not the last 200 messages.
#
# Sessions rotate instead of growing (2026-11-11). A member's session for a
# thread is resumed while its context is small; once the last turn's
# `context.used` -- the broker's own sum over the CLI's numbers, the same on
# Claude Code, Codex and Antigravity (`loam_broker.context_used`) -- reaches
# ROTATE_SHARE of the model's window, or ROTATE_TOKENS whichever is smaller
# (and ROTATE_TOKENS alone when the window is not stated, as on Antigravity),
# the next turn starts a fresh session handed the persona's summary, the
# recent exchange and the file. 150k is the cap because the windows here are
# 1M: 40% of one is 400k tokens re-read on every request of every turn, which
# is the cost this replaces. A session with work still out (background
# tasks, a notification still to land) is never rotated: its replies would
# land in a session nobody reads.
ROTATE_SHARE = 0.4
ROTATE_TOKENS = 150_000


def reply_images_checked(text: str, extra: dict) -> tuple[str, dict, list[dict]]:
    """Collect up to three images in reading order while retaining their
    positions in the reply text, plus any local image references that could
    not be kept."""
    said = [seg["text"] for seg in extra.get("segments") or []
            if isinstance(seg, dict) and isinstance(seg.get("text"), str)]
    _, found, unkept = threads.reply_images_checked("\n\n".join([*said, text or ""]))
    if found:
        extra = {**extra, "attachments": found}
    return text, extra, unkept


def reply_images(text: str, extra: dict) -> tuple[str, dict]:
    """Collect up to three images in reading order while retaining their positions in the reply text."""
    text, extra, _ = reply_images_checked(text, extra)
    return text, extra


def session_context(session) -> dict | None:
    """The session's last stated ``{used, window}``: its last turn record
    whose ``context.used`` is a number. None when it has stated none."""
    for record in reversed(getattr(session, "turns", None) or []):
        context = record.get("context") if isinstance(record, dict) else None
        if isinstance(context, dict) and isinstance(context.get("used"), int):
            return {"used": context["used"], "window": context.get("window")}
    return None


def rotate_at(context: dict | None) -> int:
    """The context size at which a session is rotated."""
    window = (context or {}).get("window")
    if isinstance(window, int) and window > 0:
        return min(ROTATE_TOKENS, int(window * ROTATE_SHARE))
    return ROTATE_TOKENS


def over_threshold(context: dict | None) -> bool:
    return bool(context) and isinstance(context.get("used"), int) \
        and context["used"] >= rotate_at(context)


NUMBERS = ("no", "one", "two", "three", "four", "five", "six", "seven",
           "eight", "nine", "ten")


def _count(n: int) -> str:
    return NUMBERS[n] if 0 <= n < len(NUMBERS) else str(n)


def _others(persona: str, detail: dict) -> str:
    """Describe the other busy sessions reported when admission is refused."""
    busy = [b for b in (detail.get("busy") or []) if isinstance(b, dict)]
    asking = sorted({threads.display(b["agent"]) for b in busy
                     if b.get("status") == "needs_input" and b.get("agent")})
    mid = sum(1 for b in busy if b.get("status") != "needs_input")
    parts = []
    if mid:
        parts.append(f"{_count(mid)} {'other is' if mid == 1 else 'others are'} mid-turn")
    if asking:
        who = (", ".join(asking[:-1]) + " and " + asking[-1]) if len(asking) > 1 else asking[0]
        parts.append(f"{who} {'is' if len(asking) == 1 else 'are'} waiting on you")
    return " and ".join(parts) or "every session on the box is busy"


def waiting_sentence(persona: str, detail: dict) -> str:
    return (f"{threads.display(persona)} is waiting for a free seat: "
            f"{_others(persona, detail)}. I'll post as soon as one frees up.")


def no_seat_sentence(persona: str, detail: dict) -> str:
    minutes = max(1, round(CAP_WAIT_S / 60))
    return (f"{threads.display(persona)} couldn't get a seat: the box stayed "
            f"full for {_count(minutes)} "
            f"minute{'s' if minutes != 1 else ''} ({_others(persona, detail)}). "
            f"Tag @{threads.display(persona)} to try again.")


class NoSeat(Exception):
    """The cap stayed full for the whole wait. Its text is the person's
    sentence the thread posts."""


def step_label(block: dict) -> str:
    name = block.get("name") or "tool"
    args = block.get("input") if isinstance(block.get("input"), dict) else {}
    for key in ("description", "command", "file_path", "path", "pattern",
                "url", "query", "prompt"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            value = " ".join(value.split())
            return f"{name}: {value[:80]}{'…' if len(value) > 80 else ''}"
    return name


class Turn:
    def __init__(self, thread_id: str, persona: str, chain: int,
                 reason: str, model: str | None = None,
                 attachments: list[dict] | None = None,
                 note: str | None = None,
                 briefed_at: float | None = None) -> None:
        self.thread_id = thread_id
        # Its entry in the member's input list on disk (2026-11-19, batch B
        # step 2, lib/loam_thread_inputs.py), and when it was queued.
        self.input_id: str | None = None
        self.dispatch: dict | None = None
        self.cursor: int | None = None
        self.settle = True
        self.queued_at: str | None = None
        # Given again after a broker restart: its prompt says when it was
        # queued, so the persona can judge whether it still matters.
        self.held = False
        # A job's result: when its brief was accepted (epoch seconds). It
        # may be steered only into a turn that was already under way then,
        # the turn that briefed it (``ThreadEngine._claim``).
        self.briefed_at = briefed_at
        # Inputs steered into this turn while it ran (their input ids),
        # settled with its own when it ends; and how it ended, for that.
        self.carried: list[str] = []
        self.sent = False
        self.posted = False
        self.completed = False
        # Words for this persona alone, after the thread in its prompt
        # (2026-11-12): a job's receipt the persona is to read
        # (`ThreadEngine.deliver_job`). Never posted in the thread.
        self.note = note
        self.persona = persona
        self.chain = chain          # agent-triggered turns since the owner spoke
        self.reason = reason
        self.model = model          # this turn's model, over the member's
        self.attachments = attachments or []   # carried to the harness
        # The last message id this turn's prompt was shown (2026-11-10): what
        # the session has read, kept as its `through`. A reply's own id is not
        # it -- with members in parallel, another member's message can land
        # between what a turn read and what it posted, and it is news to this
        # member next time.
        self.read_through: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.thread_id, self.persona)

    def entry(self) -> dict:
        """What the input list on disk keeps to make this turn again."""
        out = {"reason": self.reason, "chain": self.chain}
        for name in ("model", "note", "briefed_at"):
            if getattr(self, name) is not None:
                out[name] = getattr(self, name)
        if self.attachments:
            out["attachments"] = self.attachments
        return out

    @classmethod
    def of_entry(cls, thread_id: str, persona: str, item: dict) -> "Turn":
        turn = cls(thread_id, persona, int(item.get("chain") or 0),
                   str(item.get("reason") or "mention"), model=item.get("model"),
                   attachments=item.get("attachments") or None,
                   note=item.get("note"), briefed_at=item.get("briefed_at"))
        turn.input_id, turn.queued_at, turn.held = item["id"], item.get("at"), True
        turn.dispatch = item.get("dispatch")
        return turn


def briefed_epoch(record: dict) -> float | None:
    """When a job's brief was accepted, epoch seconds: the waiter record's
    ``since_ts``, else its ``since`` (ISO, to the second) as the end of that
    second, so a turn started within it counts as under way. None unknown."""
    if isinstance(record.get("since_ts"), (int, float)):
        return float(record["since_ts"])
    from datetime import datetime  # noqa: PLC0415
    try:
        return datetime.fromisoformat(
            str(record.get("since")).replace("Z", "+00:00")).timestamp() + 1
    except (TypeError, ValueError):
        return None


def is_result(turn: "Turn") -> bool:
    return str(getattr(turn, "reason", "")).startswith("job:")


def held_line(turn: "Turn") -> str | None:
    """For input given again after a broker restart: when it was queued."""
    if not turn.held or not turn.queued_at:
        return None
    return (f"[Loam: this input was queued for you at "
            f"{threads.local_stamp(turn.queued_at)} and was still waiting when "
            f"Loam's session host restarted; it is given to you now. Judge "
            f"whether it still matters.]")


def result_steer_text(turn: "Turn") -> str:
    """A job's result steered into the turn that briefed it."""
    head = (f"[Loam: a result for work you briefed in this turn came in while "
            f"you were working, delivered at this step. It is not from "
            f"{lc.OWNER_NAME}, and nothing is posted for it. Take it into what "
            f"you are doing.]")
    return "\n".join(x for x in (head, held_line(turn), turn.note or "") if x)


def background_list(session, origins: dict | None = None) -> list[dict]:
    """Describe pending tasks with their first journal position, arrival time and originating thread message."""
    if session is None:
        return []
    out = []
    started = getattr(session, "task_started_at", None)
    origins = origins or {}
    for task in getattr(session, "background_tasks", None) or []:
        if not isinstance(task, dict):
            continue
        tid = task.get("task_id") or task.get("id")
        origin = origins.get(tid) or {}
        out.append({"id": tid, "description": task.get("description"),
                    "started_at": started(tid) if callable(started) and tid else None,
                    "message": origin.get("message"),
                    "launched_at_seq": origin.get("seq")})
    return out


# The thread events that change a row of the thread list, and so are told to
# the list's stream (`listen_list`) as that row (2026-11-09).
LIST_EVENTS = ("message.done", "turn.started", "turn.finished", "thread.changed",
               "member.joined", "turn.waiting", "members")

# The events after which a thread's members may read differently -- who is
# mid-turn, how many background tasks each has out -- so the engine works the
# members array out again and publishes `members` when it changed.
MEMBER_EVENTS = ("turn.started", "turn.finished", "member.joined")

WAITING_LABEL = "Waiting on a background task"

# The engines whose live session takes a new message while its background
# work runs (2026-11-10). Claude Code: the CLI reads stdin between turns and
# a task's notification later hands the persona a turn of its own. Codex:
# the root thread's turn is over (its subagents are other threads), so
# `turn/start` is accepted; a subagent that ends while that turn is in
# flight has its report handed to that turn by Codex, one that ends after
# it wakes the root as its own reply (`CodexSession._wake`). Antigravity
# runs one process per turn and states no background tasks, so a member on
# it is never parked; were one ever to be, the message would wait.
TAKES_INPUT_WHILE_BACKGROUND = {"claude-code": True, "codex": True,
                                "antigravity": False}

# The engine's own event, put on a parked turn's watch queue to say a
# message for its member has arrived (2026-11-10). Never on a session's
# stream and never in a record.
HAND_OVER = "loam.hand_over"

WILL_ANSWER_AFTER_BACKGROUND = "will answer when its background task returns"
WILL_ANSWER_AFTER_TURN = "will answer when this turn ends"

# Steering (2026-11-14). The owner, 11-14: a message sent while a persona was
# mid-task "gets queued up, but it doesn't always go to the agent once
# they're done"; what he asked for is that "when the current action ... is
# done before it moves on to the next action, it actually sees my new
# message". A message for a member whose turn is generating is now sent
# into that turn (`ThreadEngine._steer`) on an engine whose session takes
# input mid-turn (`Session.STEERS`: Claude Code and Codex, probed
# 2026-11-14; not Antigravity), at the harness's next safe boundary: a
# running tool finishes and keeps its result, nothing is cancelled. Where
# the engine cannot take it, or refuses it (Codex's `expectedTurnId`), the
# message keeps its place in the member's queue and has a turn of its own
# when this one ends, as before.
#
# How long a turn keeps watching after its `result` when it was steered
# (per engine): Claude Code answers a message written after the model's
# last words in a second harness turn whose `init` follows the `result` at
# once (0.1 s on 2.1.325), and that answer is still this turn's. Codex
# answers an accepted steer inside the same turn, so it waits for nothing.
STEER_FOLLOW_S = {"claude-code": 5.0}
WILL_SEE_AT_NEXT_STEP = "will see it at its next step"

# How long a turn keeps watching after an empty `result` (2026-11-10): a
# resumed Claude Code session can open with a `result` of `num_turns: 0` that
# answers nothing -- it closes the background tasks the last process left --
# and the real turn follows at once. m13 in t-6daf5a6f was that `result`
# taken for the end of the turn: "(no reply)" was posted, the watcher let go,
# and the real answer went unwatched.
EMPTY_RESULT_GRACE_S = 30

# How often a turn parked on its background tasks asks the session whether
# anything is still out (2026-11-10): the broker's settle backstop clears a
# stale entry with no event, and a turn waiting for an event waited forever.
PARKED_CHECK_S = 5

# Why a turn ended, said in the thread's system line when it ended without
# its `result` (2026-11-10). Before, a turn that died wrote a persona message
# reading "(no reply: the turn ended by timeout)". Only real causes: there is
# no time-based one since 2026-11-10.
ENDED_WHY = {
    "exit": "its session exited",
    "closed": "its session closed",
    "stopping": "the broker restarted",
}


def ended_sentence(persona: str, why: str) -> str:
    """Format the reason a member's turn ended, including a result with no reply."""
    if why == "result":
        return f"{threads.display(persona)}'s turn ended without a reply"
    return (f"{threads.display(persona)}'s turn ended: "
            f"{ENDED_WHY.get(why, why)}")


def quiet_turn(turn: "Turn") -> bool:
    """A silent turn triggered by a job result has no thread message to post."""
    return str(getattr(turn, "reason", "")).startswith("job:")


def working_now(session) -> bool:
    """A resident session mid-turn on its own: its `status()` says
    ``working``. False for none."""
    status = getattr(session, "status", None) if session is not None else None
    try:
        return callable(status) and status() == "working"
    except Exception:  # noqa: BLE001 - a read for a row, never a failure
        return False


def used_model(reply: "Reply", session, chosen: str | None) -> str | None:
    """Prefer the model reported by the reply, then session init, then the requested model."""
    said = getattr(session, "_init_model", None)
    return reply.model or (said if isinstance(said, str) and said else None) or chosen


def mark(session, thread_id: str) -> None:
    """Mark every id the session goes by for the transcript hook: the
    broker's, a fork's ``cli_session_id``, and ``thread_id`` -- the Codex
    thread id, which is the ``session_id`` Codex's Stop hook payload carries
    (verified 2026-11-10: every Codex record's transcript sidecar,
    ``state/transcripts/<id>.json``, is under its ``thread_id``, none under
    the broker's id). Antigravity's ``thread_id`` is its conversation id."""
    ids = [session.id]
    for name in ("cli_session_id", "thread_id"):
        other = getattr(session, name, None)
        if isinstance(other, str) and other and other not in ids:
            ids.append(other)
    for sid in ids:
        steps.mark_session(sid, thread_id)


class Reply:
    """One harness turn's reply as it is heard: its steps (``did``), its
    speech in order (``ordered``: ``{"text"}`` and ``{"did": [...]}`` runs),
    what it said since its last top-level step (the message's text), and
    every event it emitted for the full record."""

    def __init__(self) -> None:
        self.did: list[dict] = []
        self.ordered: list[dict] = []
        self.segments: list[str] = []
        self.events: list[dict] = []
        self.draft = ""
        # The reply's ``↩ m12`` as its stream decided it (2026-11-17,
        # ``ThreadEngine.shown_draft``): None until the opening words are
        # known, "" for none, else the id; ``mark_run`` while the draft is
        # still the run of words that opened with it; ``shown``, the draft
        # last sent.
        self.mark: str | None = None
        self.mark_run = False
        self.shown = ""
        # Background tasks this harness turn launched (2026-11-10): ids, so
        # the reply's message id can be written onto them when it is posted.
        self.launched: list[str] = []
        # The model the engine says it ran this reply on (2026-11-10): the
        # `model` of its `system/init`, else of its top-level assistant
        # messages. All three engines state it there (Codex and Antigravity
        # through their translators). The concrete id, never the alias.
        self.model: str | None = None

    @classmethod
    def of(cls, events: list[dict]) -> "Reply":
        reply = cls()
        for event in events:
            reply.keep(event)
            if event.get("type") != "assistant":
                continue
            top = event.get("parent_tool_use_id") is None
            for block in (event.get("message") or {}).get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and top:
                    reply.said(block.get("text") or "")
                elif block.get("type") == "tool_use":
                    label = step_label(block)
                    reply.step(label if top else "↳ " + label, top)
        return reply

    def keep(self, event) -> None:
        if steps.keep(event):
            self.events.append(event)
        if not isinstance(event, dict):
            return
        said = None
        if event.get("type") == "system" and event.get("subtype") == "init":
            said = event.get("model")
        elif event.get("type") == "assistant" and event.get("parent_tool_use_id") is None:
            said = (event.get("message") or {}).get("model")
        if isinstance(said, str) and said.strip():
            self.model = said.strip()

    def said(self, text: str) -> None:
        self.segments.append(text)
        if text.strip():
            self.ordered.append({"text": text.strip()})

    def step(self, label: str, top: bool = True, ends: bool | None = None) -> None:
        """A step. Only a top-level one ends what was said before it: a
        subagent still working after the persona spoke must not wipe the
        reply's text (2026-11-10). ``ends=False`` is a step of the broker's
        own -- "Waiting on a background task" -- which ends nothing either:
        m11 in t-6daf5a6f lost "Status only: …" to one (2026-11-10)."""
        ends = top if ends is None else ends
        if ends and self.segments:
            said = " ".join(" ".join(self.segments).split())
            if said:
                self.did.append({"label": f"Said: {said[:160]}"})
            self.segments = []
        self.did.append({"label": label})
        if not ends and self.segments:
            return
        if self.ordered and "did" in self.ordered[-1]:
            self.ordered[-1]["did"].append({"label": label})
        else:
            self.ordered.append({"did": [{"label": label}]})
        self.draft = ""
        self.shown = ""
        self.mark_run = False

    def text(self) -> str:
        return "\n\n".join(s.strip() for s in self.segments if s.strip()).strip()

    def _last_said(self) -> int:
        return max((i for i, seg in enumerate(self.ordered) if "text" in seg),
                   default=-1)

    def spoken(self) -> str:
        """The message's text: what was said since the last step, else the
        last thing said before the steps that followed it, else nothing --
        and a reply that said nothing is not a message (2026-11-10)."""
        if self.text():
            return self.text()
        last = self._last_said()
        return self.ordered[last]["text"] if last >= 0 else ""

    def before(self) -> list[dict]:
        """Everything before the message's text, whole: its `segments`. The
        steps after the last words, when there are some, are in `did`."""
        if self.text() or self._last_said() < 0:
            last = max((i for i, seg in enumerate(self.ordered) if "did" in seg),
                       default=-1)
            return self.ordered[:last + 1]
        return self.ordered[:self._last_said()]


class ThreadEngine:
    """Serialize turns per thread member while letting different members answer concurrently."""

    def __init__(self, broker) -> None:
        self.broker = broker
        self.handoffs = Summaries(self)
        self._system_turns: dict | None = None   # read on first use
        self._marks: dict | None = None          # read on first use
        self.queues: dict[tuple[str, str], asyncio.Queue] = {}
        self.workers: dict[tuple[str, str], asyncio.Task] = {}
        self.listeners: dict[str, set[asyncio.Queue]] = {}
        # (thread id, persona) -> that member's turn in flight, in the order
        # the turns started; and the session each runs on.
        self.turns: dict[tuple[str, str], dict] = {}
        self.turn_sessions: dict[tuple[str, str], object] = {}
        # (thread id, persona) -> the `Turn` in flight (2026-11-14): its
        # `read_through` is what a steer moves on.
        self.turn_live: dict[tuple[str, str], Turn] = {}
        self.list_listeners: set[asyncio.Queue] = set()
        # (thread id, persona) -> the watch kept on that member's session
        # after its turn's `result` (2026-11-10): {"task", "queue", "session"}.
        self.tails: dict[tuple[str, str], dict] = {}
        self.members_sent: dict[str, list] = {}   # thread id -> last `members`
        # (thread id, persona) -> the watch queue of that member's turn in
        # flight, where a hand-over is said (2026-11-10).
        self.turn_watch: dict[tuple[str, str], asyncio.Queue] = {}
        # (thread id, persona) -> {task id: {"message", "seq"}}: the reply
        # that launched each background task (2026-11-10), read by
        # `background_list`. Kept to the tasks still out.
        self.task_origins: dict[tuple[str, str], dict[str, dict]] = {}
        # (thread id, persona) -> the lock one steer at a time holds
        # (2026-11-14), so rapid messages go in in their order.
        self.steer_locks: dict[tuple[str, str], asyncio.Lock] = {}

    # -- the turns in flight ---------------------------------------------------

    def live_turns(self, thread_id: str) -> list[dict]:
        """Every turn in flight in a thread, earliest started first."""
        return [turn for (tid, _), turn in self.turns.items() if tid == thread_id]

    @property
    def active(self) -> dict[str, dict]:
        """Thread id -> its earliest turn in flight: the singular ``turn`` a
        read and the snapshot carry, kept for the clients that read one."""
        out: dict[str, dict] = {}
        for (tid, _), turn in self.turns.items():
            out.setdefault(tid, turn)
        return out

    @property
    def sessions(self) -> dict[str, object]:
        """Thread id -> the session of its earliest turn in flight."""
        out: dict[str, object] = {}
        for key in self.turns:
            if key in self.turn_sessions:
                out.setdefault(key[0], self.turn_sessions[key])
        return out

    def settled(self, thread_id: str) -> bool:
        """No member of the thread has a turn queued or running."""
        for key, worker in self.workers.items():
            if key[0] == thread_id and not worker.done():
                return False
        return all(q.empty() for key, q in self.queues.items() if key[0] == thread_id)

    def running(self) -> dict[str, list[str]]:
        """Thread id -> the members whose turns are in flight there."""
        out: dict[str, list[str]] = {}
        for tid, persona in self.turns:
            out.setdefault(tid, []).append(persona)
        return out

    def summary(self, thread_id: str) -> dict:
        """A thread's row with its running members marked (2026-11-09), and
        each member's ``background``, ``background_tasks`` and ``working``
        (2026-11-10)."""
        return self.decorate(threads.summary(thread_id,
                                             self.running().get(thread_id, [])))

    def listing(self, archived: bool = False) -> list[dict]:
        kept = threads.load_state("sessions.json")
        return [self.decorate(row, kept.get(row["id"]) or {})
                for row in threads.listing(self.running(), archived=archived)]

    # -- who is working, and on what (2026-11-10) -----------------------------

    def live_session(self, thread_id: str, persona: str, kept: dict | None = None):
        """The member's session for this thread when it is resident, else
        None: the one a turn or a watch holds, else the broker's own by the
        id ``sessions.json`` remembers."""
        tail = self.tails.get((thread_id, persona))
        if tail is not None:
            return tail["session"]
        if (thread_id, persona) in self.turns and (thread_id, persona) in self.turn_sessions:
            return self.turn_sessions[(thread_id, persona)]
        row = (kept if kept is not None else
               (threads.load_state("sessions.json").get(thread_id) or {})).get(persona)
        sid = (row or {}).get("session_id")
        session = (getattr(self.broker, "sessions", None) or {}).get(sid) if sid else None
        if session is None:
            return None
        live = getattr(session, "is_live", None)
        if callable(live) and not live():
            return None
        return session

    @staticmethod
    def background_of(session) -> int:
        """Background tasks or subagents out behind a session: the harness's
        own list, as `background_tasks` on its record counts it; and 1 while
        the list has emptied but its notification has not landed yet
        (`working_behind`), so this is never 0 while `GET /v1/sessions/live`
        says ``state: background`` for the same session."""
        if session is None:
            return 0
        count = len(getattr(session, "background_tasks", None) or [])
        if count:
            return count
        behind = getattr(session, "working_behind", None)
        return 1 if callable(behind) and behind() and not working_now(session) else 0

    def decorate(self, row: dict, kept: dict | None = None) -> dict:
        """``background``, ``background_tasks`` and ``working`` on each
        member, the counts summed on the row. ``working`` on a member is its
        turn being in flight or its session mid-turn on its own (a background
        task's notification); ``busy`` and ``running`` stay what they were,
        the turn in flight. ``busy`` and not ``working`` is a member whose
        words are done and who waits on ``background_tasks``."""
        if kept is None:
            kept = threads.load_state("sessions.json").get(row["id"]) or {}
        for member in row.get("members") or []:
            session = self.live_session(row["id"], member["persona"], kept)
            turn = self.turns.get((row["id"], member["persona"])) or {}
            member["background"] = self.background_of(session)
            member["background_tasks"] = background_list(
                session, self.task_origins.get((row["id"], member["persona"])))
            # The turn in flight is working unless it is parked on its
            # background tasks; then, as any other member, its session says.
            member["working"] = (bool(member.get("running"))
                                 and not turn.get("on_background")) \
                or working_now(session)
            # Messages waiting for this member's turn to end (2026-11-10),
            # and what the member will do about them, in words.
            queue = self.queues.get((row["id"], member["persona"]))
            member["queued"] = queue.qsize() if queue is not None else 0
            member["note"] = None
            if turn.get("steering") and not member["queued"]:
                # Being sent into the turn in flight (2026-11-14).
                member["note"] = WILL_SEE_AT_NEXT_STEP
            if member["queued"] and turn and not turn.get("handing"):
                # No turn in flight, or one handing over: the next turn is
                # starting, and there is nothing to wait for.
                if not (turn.get("on_background") and not member["working"]):
                    member["note"] = WILL_ANSWER_AFTER_TURN
                elif not TAKES_INPUT_WHILE_BACKGROUND.get(turn.get("engine"), False):
                    member["note"] = WILL_ANSWER_AFTER_BACKGROUND
        row["background"] = sum(m["background"] for m in row.get("members") or [])
        row["working"] = sum(1 for m in row.get("members") or [] if m["working"])
        return row

    def members_changed(self, thread_id: str) -> None:
        """Publish ``members{members, background, working}`` when the
        members array differs from the one last published."""
        try:
            row = self.summary(thread_id)
        except (threads.ThreadError, OSError, KeyError):
            return
        members = row.get("members") or []
        if self.members_sent.get(thread_id) == members:
            return
        self.members_sent[thread_id] = members
        self.publish(thread_id, {"type": "members", "members": members,
                                 "background": row["background"],
                                 "working": row["working"]})

    # -- streams ---------------------------------------------------------------

    def listen(self, thread_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self.listeners.setdefault(thread_id, set()).add(queue)
        return queue

    def unlisten(self, thread_id: str, queue: asyncio.Queue) -> None:
        self.listeners.get(thread_id, set()).discard(queue)

    def pending(self, thread_id: str) -> list[dict]:
        """The requests the turns in flight are waiting on, each in the shape
        the stream publishes it live: ``broker.permission_request`` with
        ``persona`` and ``session_id``. For the opening ``thread.snapshot``,
        so a page that connects after the question was asked can draw the
        card (2026-11-08). Empty when no turn is running."""
        out = []
        for key, turn in list(self.turns.items()):
            if key[0] != thread_id:
                continue
            session = self.turn_sessions.get(key)
            events = getattr(session, "pending_events", None)
            if session is None or not callable(events):
                continue
            out.extend({**event, "thread": thread_id, "persona": turn["persona"],
                        "session_id": session.id}
                       for event in events())
        return out

    def publish(self, thread_id: str, event: dict) -> None:
        event.setdefault("thread", thread_id)
        for queue in list(self.listeners.get(thread_id, ())):
            queue.put_nowait(event)
        if event.get("type") in LIST_EVENTS:
            self.publish_row(thread_id)
        if event.get("type") in MEMBER_EVENTS:
            self.members_changed(thread_id)

    # -- the thread list's stream (2026-11-09) --------------------------------

    def listen_list(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self.list_listeners.add(queue)
        return queue

    def unlisten_list(self, queue: asyncio.Queue) -> None:
        self.list_listeners.discard(queue)

    def publish_row(self, thread_id: str) -> None:
        """The thread's row, to every open list: ``thread.changed`` with
        ``summary``. Nothing is read when no list is open."""
        if not self.list_listeners:
            return
        try:
            event = {"type": "thread.changed", "thread": thread_id,
                     "summary": self.summary(thread_id)}
        except (threads.ThreadError, OSError, KeyError):
            return
        for queue in list(self.list_listeners):
            queue.put_nowait(event)

    def publish_removed(self, thread_id: str) -> None:
        for queue in list(self.list_listeners):
            queue.put_nowait({"type": "thread.removed", "thread": thread_id})

    # -- messages --------------------------------------------------------------

    def post_person(self, thread_id: str, text: str,
                    attachments: list[dict] | None = None,
                    model: str | None = None,
                    reply_to: str | None = None, submission_id: str | None = None,
                    signature: str | None = None, reply_thread: str | None = None) -> dict:
        """Store the message and attachments before scheduling its recipients. Mentions
        take precedence over the default member. A thread with neither retains the
        message for future turns. A per-message model overrides the member choice."""
        text = (text or "").strip()
        attachments = attachments or []
        if not text and (reply_thread is not None or not attachments):
            raise threads.ThreadError(400, "a `text` is required")
        self.finish_pending_submission(thread_id)
        meta, msgs, _ = threads.read(thread_id)
        named = threads.mentions(text)
        recipients = named or ([meta["default"]] if meta.get("default") else [])
        fanout = threads.mentions_all(text)
        if fanout:
            pool = dict.fromkeys([*named, *meta["members"], meta.get("default")])
            recipients = [p for p in pool if p and not threads.is_owner(p)]
        request = {"thread": thread_id, "text": text, "model": model, "reply_to": reply_to,
                   "attachments": attachments, "submission_id": submission_id, "named": named,
                   "recipients": recipients, "fanout": fanout,
                   "first": not any(threads.is_owner(m.get("author")) for m in msgs)}
        if not meta.get("default") and named and not any(m.get("first_tag") for m in msgs):
            request["first_tag"] = named[0]
        if not recipients and not fanout:
            request["unaddressed"] = True
        if submission_id:
            request["submission_hash"] = signature or self.submission_hash(
                text, [f["path"] for f in attachments], reply_to, model, reply_thread)
            prior = self.accepted_submission(msgs, submission_id, request["submission_hash"])
            if prior:
                return prior
            if request["first"] and meta["title"] == "New thread" and text.startswith("@"):
                clean = re.sub(r"^(?:@[\w-]+\s*)+", "", text).strip()
                request["opening_title"] = (clean[:59].rstrip() + "…" if len(clean) > 60
                                            else clean or "New thread")
            if reply_to and reply_thread is None:
                threads.check_reply_to(msgs, reply_to, thread_id)
        if reply_thread is not None:
            if not isinstance(reply_thread, str) or not reply_thread or reply_thread == thread_id:
                raise threads.ThreadError(400, "Choose a source message in another thread.")
            if not request["first"]:
                raise threads.ThreadError(409, "A branch quote belongs on the opening message.")
            request["reply_source"] = threads.branch_quote(reply_thread, reply_to)
        if submission_id:
            threads._write_atomic(self._submission_path(thread_id), json.dumps(request))
        return self._finish_submission(request)

    @staticmethod
    def _submission_path(thread_id):
        name = hashlib.sha256(thread_id.encode()).hexdigest()
        return threads.STATE / "submissions" / f"{name}.json"

    def finish_pending_submission(self, thread_id):
        pending = self._submission_path(thread_id)
        if pending.exists():
            self._finish_submission(json.loads(pending.read_text()))

    @staticmethod
    def submission_hash(text, paths, reply_to, model, reply_thread=None):
        fields = [text.strip(), paths, reply_to or None, model]
        if reply_thread is not None:
            fields.append(reply_thread)
        data = json.dumps(fields, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(data.encode()).hexdigest()

    @staticmethod
    def accepted_submission(messages, key, signature):
        for msg in messages:
            if msg.get("submission_id") == key and threads.is_owner(msg.get("author")):
                if msg.get("submission_hash") != signature:
                    raise threads.ThreadError(409, "This message ID was used for different input.")
                return msg
        return None

    def answer_note(self, thread_id, message_id, persona):
        self.finish_pending_submission(thread_id)
        meta, messages, _ = threads.read(thread_id)
        note = threads.check_reply_to(messages, message_id, thread_id)
        prior = next((m for m in messages if m.get("answer_for") == message_id), None)
        if prior and prior.get("persona") == persona:
            return self.summary(thread_id)
        if prior or meta.get("default") or not note.get("unaddressed"):
            raise threads.ThreadError(409, "This message or thread already has an agent.")
        if not isinstance(persona, str) or persona not in threads.roster():
            raise threads.ThreadError(400, "Choose an available agent.")
        request = {"thread": thread_id, "answer_for": message_id, "persona": persona,
                   "attachments": note.get("attachments", [])}
        threads._write_atomic(self._submission_path(thread_id), json.dumps(request))
        self._finish_submission(request)
        return self.summary(thread_id)

    def _finish_submission(self, request, *, enqueue=True):
        if request.get("answer_for"):
            tid, mid, persona = request["thread"], request["answer_for"], request["persona"]
            threads.update(tid, default=persona)
            line = threads.append(tid, "system", f"{threads.display(persona)} now answers this thread.",
                                  kind="system", answer_for=mid, persona=persona,
                                  submission_id=f"answer-{mid}")
            turn = self.message_input(Turn(tid, persona, 0, mid, attachments=request["attachments"]), mid)
            self.publish(tid, {"type": "message.done", "message": line})
            self.publish(tid, {"type": "thread.changed", "summary": self.summary(tid)})
            self._submission_path(tid).unlink()
            if turn and enqueue:
                self.schedule(turn, persist=False)
            return line
        thread_id, text = request["thread"], request["text"]
        attachments = request["attachments"]
        key = request["submission_id"]
        extra = {"attachments": attachments} if attachments else {}
        for field in ("unaddressed", "first_tag"):
            if request.get(field):
                extra[field] = request[field]
        if request["reply_to"]:
            extra["reply_to"] = request["reply_to"]
        if request.get("reply_source"):
            extra["reply_source"] = request["reply_source"]
        if key:
            extra.update(submission_id=key, submission_hash=request["submission_hash"])
        msg = threads.append(thread_id, threads.OWNER, text, **extra)
        meta = threads.read(thread_id)[0]
        self.publish(thread_id, {"type": "message.done", "message": msg})
        self.clear_mark(thread_id, "")
        threads.clear_opening_draft(thread_id)
        if meta.get("archived") and meta.get("imported_from"):
            # A classic chat imported as an archived thread (2026-11-11) is
            # history until the owner writes in it; then it is a thread like
            # any other, back on the list, answered by its persona.
            threads.update(thread_id, archived=False)
            meta = threads.read(thread_id)[0]
            self.publish(thread_id, {"type": "thread.changed",
                                     "summary": self.summary(thread_id)})
            steps.log(f"{thread_id}: imported chat {meta['imported_from'][:8]} "
                      f"continued by {lc.OWNER_NAME}; out of the archive")
        named, answer = request["named"], request["recipients"]
        self._join(thread_id, meta, named, by=threads.OWNER)
        if request.get("first_tag") and not meta.get("default"):
            threads.update(thread_id, default=request["first_tag"])
            self.publish(thread_id, {"type": "thread.changed", "summary": self.summary(thread_id)})
        if request["fanout"]:
            if not answer:
                self.mark(thread_id, "", "Nobody to reach: bring someone in first")
            steps.COUNTS["fanout"] += len([p for p in answer if p not in named])
            steps.log(f"{thread_id}: @all from {lc.OWNER_NAME}, {len(answer)} turn(s) "
                      f"({', '.join(answer) or 'nobody'})")
        deliveries = []
        for persona in answer:
            turn = Turn(thread_id, persona, 0, msg["id"], model=request["model"],
                        attachments=attachments)
            if key:
                delivery = self.message_input(turn, msg["id"])
                if delivery:
                    deliveries.append(delivery)
            else:
                self.schedule(turn)
        if request["first"]:
            before = meta["title"]
            if request.get("opening_title") and before == "New thread":
                threads.update(thread_id, title=request["opening_title"])
            else:
                threads.title_from_first(thread_id, text)
            after = threads.read(thread_id)[0]
            if after["title"] != before:
                self.publish(thread_id, {"type": "thread.changed",
                                         "thread": thread_id,
                                         "summary": self.summary(thread_id)})
        if key:
            self._submission_path(thread_id).unlink()
            if enqueue:
                for turn in deliveries:
                    self.schedule(turn, persist=False)
        return msg

    def _join(self, thread_id: str, meta: dict, named: list[str], by: str) -> None:
        new = [p for p in named if p not in meta["members"]]
        if not new:
            return
        threads.update(thread_id, add=new)
        for persona in new:
            line = threads.append(
                thread_id, "system",
                f"{threads.display(by)} brought in {threads.display(persona)}",
                kind="system")
            self.publish(thread_id, {"type": "member.joined", "persona": persona,
                                     "engine": threads.default_engine(persona),
                                     "by": by})
            self.publish(thread_id, {"type": "message.done", "message": line})

    def schedule(self, turn: Turn, persist: bool = True) -> None:
        """Queue a turn behind that member's own, never behind another
        member's (2026-11-10). With that member's turn in flight, the
        message is handed over at once when the turn only waits on its
        background work, or steered into the turn when it is generating
        (2026-11-14); otherwise it waits its turn.

        The input is written to the member's list on disk first
        (2026-11-19, ``loam_thread_inputs``), so a restart loses none of it;
        ``persist`` is False only for input read back from that list."""
        self.handoffs.preempt(turn.key)
        if persist and turn.input_id is None:
            turn.queued_at = threads.now_iso()
            try:
                turn.input_id = inputs.add(turn.thread_id, turn.persona,
                                           {**turn.entry(), "at": turn.queued_at})
            except OSError as exc:  # still queued in memory: lost only to a restart
                steps.log(f"{turn.thread_id} {turn.persona}: {turn.reason} could "
                          f"not be written to its input list ({exc})")
        queue = self.queues.setdefault(turn.key, asyncio.Queue())
        queue.put_nowait(turn)
        if not self._ensure_worker(turn.key):
            if not self.hand_over(turn.key):
                self.steer_soon(turn.key)
            self.members_changed(turn.thread_id)

    def message_input(self, turn, message_id):
        """Durable, stable delivery for this message and member."""
        turn.input_id = f"{turn.thread_id}-{message_id}-{turn.persona}"
        prior = next((item for item in inputs.items(*turn.key) if item["id"] == turn.input_id), None)
        if prior:
            return Turn.of_entry(*turn.key, prior)
        through = (threads.session_for(*turn.key) or {}).get("through")
        if threads._num(through) >= threads._num(message_id):
            return None
        turn.queued_at = threads.now_iso()
        inputs.add(*turn.key, {**turn.entry(), "id": turn.input_id, "at": turn.queued_at})
        return turn

    def _ensure_worker(self, key: tuple[str, str]) -> bool:
        """Start the member's worker when none is running; True when this
        started one."""
        worker = self.workers.get(key)
        if worker is None or worker.done():
            self.workers[key] = asyncio.create_task(self._work(key))
            return True
        return False

    # -- steering (2026-11-14) -------------------------------------------------

    def steer_soon(self, key: tuple[str, str]) -> None:
        """A steer, off the caller's stack (``schedule`` is not async)."""
        turn = self.turns.get(key)
        if turn is None or self.turn_watch.get(key) is None:
            return
        session = self.turn_sessions.get(key)
        if not getattr(session, "STEERS", False):
            return
        asyncio.get_running_loop().create_task(self._steer(key))

    def joins(self, key: tuple[str, str], queued: Turn,
              model: str | None) -> bool:
        """Steer a message only when its model is compatible with the running turn.
        A job result can join the turn that requested it, keeping unrelated results queued."""
        if threads.is_message_id(queued.reason):
            return queued.model in (None, model)
        if is_result(queued):
            started = (self.turns.get(key) or {}).get("started_ts")
            return (queued.briefed_at is not None and started is not None
                    and started <= queued.briefed_at)
        return False

    def _claim(self, key: tuple[str, str], model: str | None) -> list[Turn]:
        """Take the leading run of queued turns that can go into the turn in
        flight (``joins``). A turn that cannot -- a result about other work,
        a notice, a message asking for another model -- and everything
        behind it keep their place, so nothing overtakes anything."""
        queue = self.queues.get(key)
        if queue is None:
            return []
        rest = []
        while not queue.empty():
            rest.append(queue.get_nowait())
        taken = []
        while rest and self.joins(key, rest[0], model):
            taken.append(rest.pop(0))
        for t in rest:
            queue.put_nowait(t)
        return taken

    def _unclaim(self, key: tuple[str, str], taken: list[Turn]) -> None:
        """Claimed turns back at the front of the queue, in their order, and
        a worker to run them should the turn in flight have ended since."""
        if not taken:
            return
        queue = self.queues.setdefault(key, asyncio.Queue())
        rest = []
        while not queue.empty():
            rest.append(queue.get_nowait())
        for t in taken + rest:
            queue.put_nowait(t)
        self._ensure_worker(key)

    async def _steer(self, key: tuple[str, str]) -> bool:
        """Claim compatible messages before steering to exclude a second worker.
        Restore their queue position on refusal. Advance read_through only after acceptance."""
        lock = self.steer_locks.setdefault(key, asyncio.Lock())
        async with lock:
            turn = self.turns.get(key)
            session = self.turn_sessions.get(key)
            live = self.turn_live.get(key)
            if turn is None or session is None or live is None \
                    or self.turn_watch.get(key) is None:
                return False
            if turn.get("on_background") and not working_now(session):
                # Parked and not generating: the hand-over's to deliver.
                self.hand_over(key)
                return False
            if not working_now(session) or turn.get("ended_by"):
                return False
            taken = self._claim(key, turn.get("model"))
            if not taken:
                return False
            thread_id, persona = key
            try:
                meta, msgs, _ = threads.read(thread_id)
            except threads.ThreadError:
                self._unclaim(key, taken)
                return False
            cut = threads._num(live.read_through)
            news, parts, results = [], [], []
            for jobs, group in groupby(taken, key=is_result):
                if jobs:
                    for job in group:
                        parts.append(result_steer_text(job))
                        results.append(job)
                    continue
                # Include intervening context only through the last message
                # this group claimed. Later inputs may require another model.
                end = max(threads._num(t.reason) for t in group)
                batch = [m for m in msgs if cut < threads._num(m.get("id")) <= end
                         and m.get("author") != persona]
                if batch:
                    parts.append(threads.steer_prompt(meta, persona, batch, record=msgs))
                    news.extend(batch)
                cut = max(cut, end)
            ids = {t.reason for t in taken}
            if not parts:
                # Every claimed message was already in what the turn read.
                steps.COUNTS["already_heard"] += len(taken)
                steps.log(f"{thread_id} {persona}: {', '.join(sorted(ids))} already "
                          f"in what its turn in flight read; not sent again")
                self._settle_inputs(key, taken, done=True)
                self.members_changed(thread_id)
                return False
            attachments = [a for t in taken for a in t.attachments]
            text = "\n\n".join(parts)
            turn["steering"] = True
            self.members_changed(thread_id)
            descriptor = None
            try:
                if live.dispatch:
                    descriptor = {"id": inputs.new_id(), "inputs": [t.input_id for t in taken],
                                  "messages": [m["id"] for m in news],
                                  "text": text, "attachments": attachments}
                    live.dispatch["steers"].append(descriptor)
                    inputs.bind(*key, live.input_id, live.dispatch)
                    seq = await session.dispatch(descriptor["id"], text, attachments,
                                                 steer=True)
                else:
                    seq = await session.steer(text, attachments or None)
            except Exception as exc:  # noqa: BLE001 - a refused steer is a queued message
                steps.log(f"{thread_id} {persona}: steer failed ({exc!r})")
                seq = None
                if descriptor:
                    since = live.dispatch["since"]
                    events = await session.history(since) if hasattr(session, "history") else session.replay(since)
                    seq = next((e["seq"] for e in events if e.get("dispatch_id") == descriptor["id"]), None)
            finally:
                turn.pop("steering", None)
            if seq is None:
                if descriptor:
                    live.dispatch["steers"].remove(descriptor)
                    inputs.bind(*key, live.input_id, live.dispatch)
                steps.COUNTS["steer_refused"] += 1
                steps.log(f"{thread_id} {persona}: {', '.join(sorted(ids))} could "
                          f"not go into the turn in flight; kept in order for a "
                          f"turn of its own")
                self._unclaim(key, taken)
                self.members_changed(thread_id)
                return False
            heard = [m["id"] for m in news]
            if heard:
                live.read_through = heard[-1]
                turn.setdefault("steered", []).extend(heard)
            # On disk the steered inputs are the turn in flight's now, settled
            # with its own when it ends.
            carried = [t.input_id for t in taken if t.input_id]
            live.carried.extend(carried)
            try:
                inputs.mark_sent(thread_id, persona, carried)
            except OSError as exc:
                steps.log(f"{thread_id} {persona}: input list not marked ({exc})")
            steps.COUNTS["steered"] += len(heard) + len(results)
            named = heard + [t.reason for t in results]
            steps.log(f"{thread_id} {persona}: {', '.join(named)} steered into its "
                      f"turn in flight (session {session.id[:8]}, seq {seq}); "
                      f"answered in this turn")
            self.publish(thread_id, {"type": "turn.steered", "persona": persona,
                                     "messages": heard, "seq": seq,
                                     **({"results": [t.reason for t in results]}
                                        if results else {})})
            self.members_changed(thread_id)
            return True

    def already_heard(self, turn: Turn) -> bool:
        """Skip a queued message already covered by the previous turn’s prompt or accepted steering input."""
        if not threads.is_message_id(turn.reason):
            return False
        kept = threads.session_for(turn.thread_id, turn.persona) or {}
        if threads._num(kept.get("through")) < threads._num(turn.reason):
            return False
        steps.COUNTS["already_heard"] += 1
        steps.log(f"{turn.thread_id} {turn.persona}: {turn.reason} was already "
                  f"in what its last turn read (through {kept.get('through')}); "
                  f"no second turn for it")
        return True

    def hand_over(self, key: tuple[str, str]) -> bool:
        """Yield a background-only turn to new input if the provider can receive it while its tasks run."""
        turn = self.turns.get(key)
        watch = self.turn_watch.get(key)
        queue = self.queues.get(key)
        if turn is None or watch is None or queue is None or queue.empty():
            return False
        # A message or a job's result hands over (a result since 2026-11-19,
        # batch B step 2: a turn parked on background work is not mid-answer,
        # so the result is taken now, in the same session). A notice alone
        # waits for the turn to end, and the queue keeps its order.
        if not any(threads.is_message_id(t.reason) or is_result(t)
                   for t in list(queue._queue)):  # noqa: SLF001
            return False
        if not turn.get("on_background") or turn.get("handing"):
            return False
        if working_now(self.turn_sessions.get(key)):
            return False
        if not TAKES_INPUT_WHILE_BACKGROUND.get(turn.get("engine"), False):
            if not turn.get("said_later"):
                turn["said_later"] = True
                steps.log(f"{key[0]} {key[1]}: a message waits; {turn.get('engine')} "
                          f"cannot take input while its background work runs, so "
                          f"{key[1]} {WILL_ANSWER_AFTER_BACKGROUND}")
            return False
        turn["handing"] = True
        watch.put_nowait({"type": HAND_OVER})
        return True

    def _settle_inputs(self, key: tuple[str, str], turns: list[Turn],
                       done: bool | None = None) -> None:
        """Each turn's input on disk, and what was steered into it: removed
        once dealt with, or kept to be given again when the broker stopping
        cut the turn before it said or finished anything (``done`` None:
        decide from the turn). Fail-soft."""
        stopping = getattr(self.broker, "stopping", False)
        for turn in turns:
            ids = [i for i in [turn.input_id, *turn.carried] if i]
            if not ids:
                continue
            finished = done if done is not None else \
                (not stopping or turn.posted or turn.completed)
            try:
                if finished:
                    inputs.remove(key[0], key[1], ids)
                else:
                    inputs.keep(key[0], key[1], ids)
                    steps.log(f"{key[0]} {key[1]}: {turn.reason} "
                              f"{'cut before a word' if turn.sent else 'not started'} "
                              f"as the broker stops; kept for when it starts again"
                              + (f" (and {len(turn.carried)} steered into it)"
                                 if turn.carried else ""))
            except OSError as exc:
                steps.log(f"{key[0]} {key[1]}: input list not settled ({exc})")

    def resume_inputs(self) -> dict:
        """At the broker's start (2026-11-19): every member's input still on
        disk scheduled again, once each and in order, its prompt saying when
        it was queued. An input a crash left marked as given that is not a
        message (a result, a notice) is not given twice; a message's is,
        unless the member's ``through`` has passed it (``already_heard``).
        Input for a thread that is gone is dropped. Returns the counts."""
        for pending in (threads.STATE / "submissions").glob("*.json"):
            request = json.loads(pending.read_text())
            try:
                self._finish_submission(request, enqueue=False)
            except threads.ThreadError as exc:
                if exc.status != 404:
                    raise
                pending.unlink(missing_ok=True)
        counts = {"members": 0, "scheduled": 0, "dropped": 0}
        for tid, persona, items in inputs.pending():
            try:
                threads.read(tid)
            except threads.ThreadError:
                counts["dropped"] += len(items)
                inputs.drop_member(tid, persona)
                steps.log(f"{tid} {persona}: {len(items)} input(s) for a thread "
                          f"that is gone; dropped")
                continue
            counts["members"] += 1
            carried = {input_id for parent in items
                       for steer in (parent.get("dispatch") or {}).get("steers", [])
                       for input_id in steer["inputs"]}
            for item in items:
                if item["id"] in carried:
                    continue
                turn = Turn.of_entry(tid, persona, item)
                if item.get("sent") and not turn.dispatch and not threads.is_message_id(turn.reason):
                    inputs.remove(tid, persona, [turn.input_id])
                    counts["dropped"] += 1
                    steps.log(f"{tid} {persona}: {turn.reason} was given to the "
                              f"session at {item['sent']} before the broker "
                              f"stopped; not given twice")
                    continue
                counts["scheduled"] += 1
                steps.log(f"{tid} {persona}: {turn.reason}, queued at "
                          f"{item.get('at')}, scheduled again after the broker "
                          f"restarted")
                self.schedule(turn, persist=False)
        return counts

    async def _work(self, key: tuple[str, str]) -> None:
        queue = self.queues[key]
        while not queue.empty():
            with fence.admission() as admitted:
                if admitted:
                    await self._work_one(key, queue.get_nowait())
            if not admitted:
                await asyncio.sleep(.25)

    async def _work_one(self, key, turn):
        thread_id = key[0]
        try:
            if getattr(self.broker, "stopping", False):
                # No session starts once the broker is stopping
                # (2026-11-10): the 11-10 stop hung on a start it began
                # after SIGTERM. The input stays on disk and is given
                # when the broker starts again (2026-11-19); nothing is
                # posted.
                return
            if loam_agents.is_archived(turn.persona, threads.AI_LABS):
                # Asleep (2026-11-14): a thread's default or member that
                # was archived answers nothing; its threads stay readable
                # and go on with another agent.
                self.mark(thread_id, turn.persona, (
                    f"{threads.display(turn.persona)} is archived, so it "
                    f"won't answer. Tag another agent, or restore "
                    f"{threads.display(turn.persona)} in Settings › Agents › "
                    f"Archived."))
                return
            if not turn.dispatch and self.already_heard(turn):
                return
            await self.run_turn(turn)
        except OSError as exc:
            if not turn.dispatch and not getattr(self.broker, "remote", False):
                self.mark(thread_id, turn.persona,
                          f"{threads.display(turn.persona)}'s turn failed: {exc}")
                return
            # A failed cursor or markdown write is retryable. Keep the
            # same intent at the head, even if a shutdown interrupts us.
            turn.settle = False
            steps.log(f"{thread_id} {turn.persona}: retrying dispatch "
                      f"{turn.input_id} after IO failure ({exc})")
            await asyncio.sleep(1)
            entries = inputs.items(*key)
            item = next((entry for entry in entries if entry["id"] == turn.input_id), None)
            if item and not getattr(self.broker, "stopping", False):
                self._unclaim(key, [Turn.of_entry(*key, item)])
        except asyncio.CancelledError:
            turn.settle = False
            raise
        except Exception as exc:  # noqa: BLE001 - one turn must not end the queue
            if getattr(self.broker, "stopping", False):
                # Its input is kept for the next start, unless the turn
                # had already posted words.
                text = (ended_sentence(turn.persona, "stopping")
                        if turn.posted else None)
            else:
                text = (str(exc) if isinstance(exc, NoSeat) else
                        f"{threads.display(turn.persona)}'s turn failed: "
                        f"{getattr(exc, 'message', None) or exc}")
            if text:
                self.mark(thread_id, turn.persona, text)
        finally:
            if turn.settle:
                self._settle_inputs(key, [turn])
            self.turns.pop(key, None)
            self.turn_sessions.pop(key, None)
            self.turn_live.pop(key, None)
            # The row's `running` went false; the list hears it.
            self.publish(thread_id, {"type": "turn.finished",
                                     "persona": turn.persona})

    # -- ending a turn from the app (2026-11-10) -------------------------------

    def turn_of(self, session_id: str) -> str | None:
        """The thread whose turn in flight runs on this session, or None."""
        key = self.turn_key_of(session_id)
        return key[0] if key else None

    def turn_key_of(self, session_id: str) -> tuple[str, str] | None:
        for key, session in list(self.turn_sessions.items()):
            if getattr(session, "id", None) == session_id and key in self.turns:
                return key
        return None

    def mark_ended(self, session_id: str, by: str = threads.OWNER) -> str | None:
        """Record who stopped an active turn so it can keep the partial reply and post the stop as a system line."""
        key = self.turn_key_of(session_id)
        if key is None:
            return None
        self.turns[key]["ended_by"] = by
        return key[0]

    def ended_line(self, thread_id: str, persona: str, by: str) -> dict:
        return threads.append(
            thread_id, "system",
            f"{threads.display(by)} ended {threads.display(persona)}'s turn",
            kind="system")

    # -- one turn --------------------------------------------------------------

    def rotation(self, turn: Turn, engine: str) -> dict | None:
        """Whether this turn starts a fresh session because the member's
        session has grown past the threshold (2026-11-11): the context it
        last stated, or None to resume as before. Never while that session
        has anything out."""
        kept = threads.session_for(turn.thread_id, turn.persona)
        if not kept or kept.get("engine") != engine:
            return None          # fresh anyway
        live = self.live_session(turn.thread_id, turn.persona)
        record = live or (getattr(self.broker, "sessions", None) or {}).get(
            kept.get("session_id"))
        context = session_context(record) or kept.get("context")
        if not over_threshold(context):
            return None
        busy = getattr(live, "busy", None)
        if live is not None and (self.background_of(live) or working_now(live)
                                 or (callable(busy) and busy())):
            steps.log(f"{turn.thread_id} {turn.persona}: session "
                      f"{str(kept.get('session_id'))[:8]} is past the threshold "
                      f"({context['used']} tokens) but has work out; resumed")
            return None
        return context

    def agy_conversation_gone(self, turn: Turn, engine: str, inspected=None) -> str | None:
        """The Antigravity conversation this member's kept session would
        resume, when its files were cleared (lib/loam_agy_files.py
        ``clear_idle``, a week idle; 2026-11-16): the turn then starts a fresh
        session that is given the thread, since ``agy`` asked to resume a
        conversation it no longer has starts an empty one. None otherwise."""
        if engine != "antigravity":
            return None
        kept = threads.session_for(turn.thread_id, turn.persona)
        if not kept or kept.get("engine") != engine or not kept.get("session_id"):
            return None
        sid = kept["session_id"]
        live = (getattr(self.broker, "sessions", None) or {}).get(sid)
        conversation = getattr(inspected or live, "thread_id", None)
        if conversation is None and not getattr(self.broker, "remote", False):
            try:
                conversation = json.loads((cfg.SESSION_DIR / f"{sid}.json")
                                          .read_text(encoding="utf-8")).get("thread_id")
            except (OSError, ValueError, AttributeError):
                return None
        if not conversation:
            return None
        import loam_agy_files  # noqa: PLC0415
        db = loam_agy_files.root() / "conversations" / f"{conversation}.db"
        return None if db.exists() else conversation

    async def _session(self, turn: Turn, meta: dict, engine: str,
                       model: str | None = None, fresh: bool = False,
                       effort: str | None = None):
        kept = threads.session_for(turn.thread_id, turn.persona)
        resume = bool(kept and kept.get("engine") == engine) and not fresh
        deadline = time.monotonic() + CAP_WAIT_S
        said_wait = False
        while True:
            try:
                session = await self.broker.start_session(
                    turn.persona, kept["session_id"] if resume else None,
                    resume=resume, provider=engine,
                    **({"start_key": f"{turn.thread_id}/{turn.persona}/{turn.input_id}/{engine}"}
                       if getattr(self.broker, "remote", False) else {}),
                    **({"model": model} if model else {}),
                    **({"effort": effort} if effort else {}))
                # In the background: the check never holds a turn up.
                asyncio.get_running_loop().create_task(
                    self._memory_pins(turn.thread_id, engine))
                break
            except Exception as exc:  # noqa: BLE001
                status = getattr(exc, "status", None)
                if status == 429:
                    detail = getattr(exc, "detail", None) or {}
                    if time.monotonic() >= deadline:
                        raise NoSeat(no_seat_sentence(turn.persona, detail)) from exc
                    if not said_wait:
                        # Said once, in the thread and on the stream, so the
                        # wait is something a person can see happening.
                        said_wait = True
                        text = waiting_sentence(turn.persona, detail)
                        active = self.turns.get(turn.key)
                        if active is not None:
                            active["waiting"] = text
                        # State on the turn, never a line in the record
                        # (2026-11-19): the app draws `waiting`.
                        self.publish(turn.thread_id, {
                            "type": "turn.waiting", "persona": turn.persona,
                            "text": text})
                    await asyncio.sleep(CAP_RETRY_S)
                    continue
                if resume and status in (404, 409):
                    resume = False
                    continue
                raise
        active = self.turns.get(turn.key)
        if active is not None:
            active.pop("waiting", None)
        title = f"Thread · {meta['title']}"
        configure = getattr(session, "configure_thread", None)
        if configure is None:
            await configure_thread(session, title, model, effort)
        else:
            await configure(title, model, effort)
            await session.attach(session.seq)
        return session, (kept if resume else None)

    async def _prepare_turn(self, turn, meta, engine, model, effort):
        rotated = self.rotation(turn, engine)
        if rotated is not None:
            old = threads.session_for(turn.thread_id, turn.persona) or {}
            # Nothing of the old session's is lost: a reply it made that no
            # watch heard is posted now, as at any resumed turn.
            prior = self.live_session(turn.thread_id, turn.persona) or \
                (getattr(self.broker, "sessions", None) or {}).get(old.get("session_id"))
            if prior is not None:
                await self.recover_late(turn, prior, old, engine, model)
            steps.COUNTS["rotated"] += 1
            steps.log(f"{turn.thread_id} {turn.persona}: session "
                      f"{str(old.get('session_id'))[:8]} holds {rotated['used']} "
                      f"tokens (rotates at {rotate_at(rotated)}); this turn starts "
                      f"a fresh one")
        inspected = None
        if rotated is None and engine == "antigravity" and getattr(self.broker, "remote", False):
            kept = threads.session_for(*turn.key) or {}
            sid = kept.get("session_id")
            if sid and kept.get("engine") == engine and sid not in self.broker.sessions:
                try:
                    inspected = await self.broker.inspect(sid)
                except Exception as exc:
                    if getattr(exc, "status", None) != 404:
                        raise
        gone = None if rotated is not None else self.agy_conversation_gone(turn, engine, inspected)
        if gone:
            steps.log(f"{turn.thread_id} {turn.persona}: Antigravity conversation "
                      f"{gone[:8]} was cleared after a week idle; this turn "
                      f"starts a fresh session")
        session, kept = await self._session(turn, meta, engine, model,
                                            fresh=rotated is not None or bool(gone),
                                            effort=effort)
        # The thread as it stands now the session is open: another member may
        # have spoken while this one waited for its seat.
        meta, msgs, _ = threads.read(turn.thread_id)
        # What a persona carries into a turn (2026-11-11): the recent
        # exchange, and on a fresh session its own summary too; on a resumed
        # one, what in that exchange is new to it.
        window = threads.recent_window(msgs)
        summary, summary_through, unshown = None, None, 0
        if kept:
            cut = threads._num(kept.get("through"))
            unseen = [m for m in msgs if threads._num(m.get("id")) > cut
                      and m.get("author") != turn.persona]
            shown = {m.get("id") for m in window}
            news = [m for m in unseen if m.get("id") in shown]
            unshown = len(unseen) - len(news)
        else:
            news = window
            summary, summary_through, _ = prior_summary(turn.key)
        prompt = threads.turn_prompt(meta, turn.persona, news, fresh=kept is None,
                                     record=msgs, summary=summary, unshown=unshown,
                                     summary_through=summary_through)
        if turn.note:
            prompt += "\n\n" + turn.note
        held = held_line(turn)
        if held:
            prompt += "\n\n" + held
        # A scheduled run of this persona's that failed since it last heard
        # (2026-11-18): one line, once, at the top of its next turn on any
        # thread and any provider; cleared by that lane's next good run.
        dark = lane_notice.take(turn.persona)
        if dark:
            prompt = dark + "\n\n" + prompt
            steps.log(f"{turn.thread_id} {turn.persona}: told of a failed "
                      f"scheduled run: {dark[:200]}")
        through = msgs[-1]["id"] if msgs else "m0"
        return session, kept, prompt, through

    async def _restore_steers(self, turn, session, history):
        """Reconcile claimed FIFO batches before the replacement reader runs."""
        intent = turn.dispatch
        receipts = {e["dispatch_id"] for e in history if e.get("dispatch_id")}
        entries = {i["id"]: i for i in inputs.items(*turn.key)}
        refused = []
        for steer in list(intent["steers"]):
            accepted = steer["id"] in receipts
            if not accepted and session.incarnation == intent["incarnation"]:
                seq = await session.dispatch(steer["id"], steer["text"],
                                             steer["attachments"], steer=True)
                accepted = seq is not None
            if accepted:
                turn.carried.extend(steer["inputs"])
            else:
                refused.extend(Turn.of_entry(*turn.key, entries[i])
                               for i in steer["inputs"] if i in entries)
                intent["steers"].remove(steer)
        if refused:
            inputs.bind(*turn.key, turn.input_id, intent)
            self._unclaim(turn.key, refused)

    async def run_turn(self, turn: Turn) -> dict | None:
        meta, msgs, _ = threads.read(turn.thread_id)
        if turn.persona not in meta["members"]:
            return None
        # A watch kept on this member's session since its last turn lets
        # go: this turn reads the session from here.
        self.stop_tail(turn.thread_id, turn.persona)
        if turn.dispatch:
            engine, model, effort = (turn.dispatch[key] for key in ("engine", "model", "effort"))
        else:
            from loam_provider_defaults import SETUP  # noqa: PLC0415
            choice = await asyncio.to_thread(threads.persona_defaults, turn.persona,
                                             meta["engines"].get(turn.persona))
            engine = choice["provider"]
            if not engine:
                self.mark(turn.thread_id, turn.persona, SETUP)
                return None
            model = turn.model or (meta.get("models") or {}).get(turn.persona) or choice["model"]
            effort = choice["effort"]
        others = [t["persona"] for t in self.live_turns(turn.thread_id)
                  if t["persona"] != turn.persona]
        if others:
            # Started while another member's turn runs (2026-11-10): before,
            # it waited for that turn to finish.
            steps.COUNTS["parallel"] = steps.COUNTS.get("parallel", 0) + 1
            steps.log(f"{turn.thread_id} {turn.persona}: turn starts while "
                      f"{', '.join(others)} {'is' if len(others) == 1 else 'are'} "
                      f"mid-turn; they run in parallel")
        self.turns[turn.key] = {"persona": turn.persona, "engine": engine,
                                "model": model, "started_at": threads.now_iso(),
                                "started_ts": time.time()}
        self.clear_mark(turn.thread_id, turn.persona)
        self.publish(turn.thread_id, {"type": "turn.started", "persona": turn.persona,
                                      "engine": engine, "model": model})
        recovering = turn.dispatch is not None
        same_host = False
        if recovering:
            intent = turn.dispatch
            remote = getattr(self.broker, "remote", False)
            if remote:
                session = await self.broker.inspect(intent["session_id"])
                await session.attach(intent["since"])
            else:
                session = self.broker.load(intent["session_id"])
            if session.agent != turn.persona or session.provider != engine:
                raise ValueError("dispatch belongs to another provider session")
            kept, prompt, through = intent["previous"], intent["prompt"], intent["through"]
            history = session.replay(intent["since"])
            receipt = next((e["seq"] for e in history if e.get("type") == "broker.user_message"
                            and e.get("dispatch_id") == turn.input_id), None)
            same_host = session.incarnation == intent["incarnation"]
            if receipt is None and not same_host:
                # The old host exited before its journalled write. A new
                # process can take this intent with its original binding.
                session = await self.broker.start_session(
                    turn.persona, session.id, resume=bool(kept), provider=engine,
                    model=model, effort=intent["effort"])
                if remote:
                    await session.configure_thread(f"Thread · {meta['title']}", model, intent["effort"])
                    await session.attach(intent["since"])
                else:
                    session.tell_time = False
                    session.idle_timeout_s = cfg.THREAD_IDLE_S
                history = session.replay(intent["since"])
                intent["incarnation"] = session.incarnation
                inputs.bind(*turn.key, turn.input_id, intent)
            self.turns[turn.key].update(engine=engine, model=model,
                                        started_ts=intent["started_ts"])
        else:
            session, kept, prompt, through = await self._prepare_turn(
                turn, meta, engine, model, effort)
            receipt = None
            if hasattr(session, "dispatch"):
                if turn.input_id is None:
                    raise OSError("turn must have a durable input before dispatch")
                turn.dispatch = {
                    "session_id": session.id, "incarnation": session.incarnation,
                    "engine": engine, "model": model, "effort": effort,
                    "previous": kept, "through": through, "since": session.seq,
                    "prompt": prompt, "started_ts": self.turns[turn.key]["started_ts"],
                    "steers": [],
                }
                inputs.bind(*turn.key, turn.input_id, turn.dispatch)
        turn.read_through = through

        queue: asyncio.Queue = asyncio.Queue()
        if recovering:
            for event in history:
                queue.put_nowait(event)
            if receipt is not None and (not same_host or session.state != "running"):
                queue.put_nowait(None)
        session.watch(queue)
        self.turn_sessions[turn.key] = session
        self.turn_live[turn.key] = turn
        follow_s = STEER_FOLLOW_S.get(engine, 0.0)
        steered_since = False   # a steer went in since the last `result`
        # The new writer is behind this session from here: the transcript
        # hook stops filing a second copy of its turns (2026-11-10).
        mark(session, turn.thread_id)
        # Replies the last turn's watcher never saw, posted before this one.
        if kept and not turn.dispatch:
            await self.recover_late(turn, session, kept, engine, model)

        reply = Reply()
        posted: list[dict] = []
        ended = None
        waiting = False

        def step(label: str, top: bool = True, ends: bool | None = None,
                 **extra) -> None:
            reply.step(label, top, ends)
            self.publish(turn.thread_id, {
                "type": "turn.step", "persona": turn.persona,
                "label": label, "steps": len(reply.did), **extra})

        def parked_done() -> bool:
            """A parked turn whose session has nothing out and is not
            mid-turn: what the last `result` was waiting for has settled."""
            behind = getattr(session, "working_behind", None)
            return not (callable(behind) and behind()) and not working_now(session)

        def park(on: bool) -> None:
            """The turn is parked on its background tasks, or back from them."""
            nonlocal waiting
            waiting = on
            active = self.turns.get(turn.key)
            if active is not None:
                active["on_background"] = on
                if on:
                    # A message that arrived while this turn was generating
                    # is answered now its words are done (2026-11-10).
                    self.hand_over(turn.key)
            self.members_changed(turn.thread_id)

        def waiting_on(tasks: list) -> str:
            named = [t.get("description") for t in tasks
                     if isinstance(t, dict) and t.get("description")]
            return WAITING_LABEL + (f": {named[0][:80]}" if named else "")

        def flush(why: str, more: bool) -> None:
            """One harness turn, one message (2026-11-10). Until then a turn
            that went on after its own `result` -- a background task's
            notification handing the session another turn -- was one
            message whose text was the LAST reply's, and every reply before
            it survived only as a 160-character `Said:` step.

            A harness turn that said nothing is not a message: its steps go
            on to the reply that does say something, or, when none comes,
            into the full record alone (the end of ``run_turn``)."""
            nonlocal reply
            if not reply.spoken():
                if reply.did and more:
                    steps.COUNTS["carried"] += 1
                    steps.log(f"{turn.thread_id} {turn.persona}: a harness turn said "
                              f"nothing; its {len(reply.did)} step(s) carry on")
                return
            msg = self.post_reply(turn, session, reply, engine, model, why)
            posted.append(msg)
            reply = Reply()

        import loam_recent  # noqa: PLC0415
        try:
            vault_snap = await asyncio.to_thread(loam_recent.snapshot_vault)
        except Exception:  # noqa: BLE001
            vault_snap = {}
        snap_started = time.time()
        try:
            restored_steers = {}
            if recovering:
                await self._restore_steers(turn, session, history)
                restored_steers = {s["id"]: s for s in turn.dispatch["steers"]}
            if turn.dispatch:
                if receipt is None or (same_host and session.state == "running"):
                    receipt = await session.dispatch(turn.input_id, prompt, turn.attachments)
                if kept:
                    await self.recover_late(turn, session, kept, engine, model, before=receipt)
            elif turn.attachments:
                await session.send_message(prompt, turn.attachments)
            else:
                await session.send_message(prompt)
            # The once-only mark, with no await between the send and it.
            turn.sent = True
            if turn.input_id:
                try:
                    inputs.mark_sent(turn.thread_id, turn.persona, [turn.input_id])
                except OSError as exc:
                    steps.log(f"{turn.thread_id} {turn.persona}: input list not "
                              f"marked ({exc})")
            self.turn_watch[turn.key] = queue
            queued = self.queues.get(turn.key)
            if queued is not None and not queued.empty():
                self.steer_soon(turn.key)
            heard = False        # an assistant event since the last result
            empty_until = None   # watching on past an empty result, until
            # No deadline (2026-11-10): the watch ends on the turn's
            # `result`, its session's exit or close, or a person ending it.
            while ended is None:
                if empty_until is not None and time.monotonic() >= empty_until:
                    ended = "result"   # the empty result was the end after all
                    break
                # Parked, a look every PARKED_CHECK_S at whether anything is
                # still out; otherwise the next event, however long it takes.
                wait = (PARKED_CHECK_S if waiting else None) \
                    if empty_until is None else \
                    max(0.01, empty_until - time.monotonic())
                try:
                    async with asyncio.timeout(wait):
                        event = await queue.get()
                except TimeoutError:
                    if waiting and parked_done():
                        # Parked on background work that has since settled
                        # with no event to say so: the turn is over.
                        steps.log(f"{turn.thread_id} {turn.persona}: nothing is "
                                  f"out any more; the parked turn ends")
                        ended = "result"
                    elif waiting:
                        # A message refused the hand-over while the turn was
                        # still generating is asked again at every look
                        # (2026-11-14): before, it was asked once, and m6 in
                        # t-232b9184 waited 90 s for the owner's next message.
                        self.hand_over(turn.key)
                    continue
                if isinstance(event, Exception):
                    raise OSError(str(event)) from event
                if event is None:
                    ended = "closed"
                    break
                if event.get("type") == HAND_OVER:
                    active = self.turns.get(turn.key) or {}
                    if waiting and not working_now(session):
                        ended = "handed"
                        break
                    # Back from its background work since: it is generating
                    # again, and the message waits for this turn's next
                    # park or its end.
                    active["handing"] = False
                    continue
                if receipt is not None and event.get("seq", receipt) < receipt:
                    # A previous background reply can arrive between saving
                    # the intent and the host accepting this prompt.
                    continue
                turn.cursor = event.get("seq", turn.cursor)
                reply.keep(event)
                kind = event.get("type")
                top = event.get("parent_tool_use_id") is None
                if kind == "broker.user_message" and event.get("steered"):
                    steered_since = True
                    restore = restored_steers.pop(event.get("dispatch_id"), None)
                    if restore and restore["messages"]:
                        turn.read_through = restore["messages"][-1]
                        self.turns[turn.key].setdefault("steered", []).extend(restore["messages"])
                    continue
                if kind == "stream_event" and top:
                    inner = event.get("event") or {}
                    delta = inner.get("delta") or {}
                    if inner.get("type") == "content_block_delta" and \
                            delta.get("type") == "text_delta":
                        reply.draft += delta.get("text") or ""
                        shown = self.shown_draft(turn, reply)
                        if shown is not None:
                            self.publish(turn.thread_id, {
                                "type": "message.delta", "persona": turn.persona,
                                **shown})
                elif kind == "assistant":
                    heard = True
                    empty_until = None
                    for block in (event.get("message") or {}).get("content") or []:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "text" and top:
                            reply.said(block.get("text") or "")
                        elif block.get("type") == "tool_use":
                            label = step_label(block)
                            step(label if top else "↳ " + label, top)
                elif kind in ("broker.permission_request",
                              "broker.permission_resolved"):
                    self.publish(turn.thread_id, {
                        **event, "persona": turn.persona,
                        "session_id": session.id})
                elif kind == "broker.background_tasks" and event.get("count"):
                    self.note_launched(turn.key, event, reply, posted)
                    # The harness says work it launched is still running
                    # (2026-11-09): an explicit waiting state on the stream.
                    if not waiting:
                        park(True)
                        step(waiting_on(event.get("tasks") or []), ends=False,
                             waiting=True, tasks=event.get("tasks") or [])
                    else:
                        self.members_changed(turn.thread_id)
                elif kind == "result" and top and not heard \
                        and event.get("num_turns") == 0 \
                        and not event.get("is_error"):
                    # An empty result: nothing was asked of the model in it.
                    # The real turn is still to come; keep watching a while.
                    empty_until = time.monotonic() + EMPTY_RESULT_GRACE_S
                    steps.log(f"{turn.thread_id} {turn.persona}: an empty result "
                              f"(num_turns 0) is not the end of the turn")
                elif kind == "result" and top:
                    heard = False
                    empty_until = None
                    behind = getattr(session, "working_behind", None)
                    more = (event["working_behind"] if "working_behind" in event
                            else bool(callable(behind) and behind()))
                    follow = steered_since and follow_s > 0
                    steered_since = False
                    flush("result", more or follow)
                    self.members_changed(turn.thread_id)
                    if more:
                        # The turn's result came with its children still
                        # out; the CLI hands the persona another turn when
                        # they report. That turn is its own message.
                        if not waiting:
                            park(True)
                            step(waiting_on(getattr(session, "background_tasks", [])),
                                 ends=False, waiting=True)
                        else:
                            # Parked since before this result (a task launched
                            # mid-turn): a message the hand-over refused while
                            # the turn generated is delivered now (2026-11-14).
                            self.hand_over(turn.key)
                        continue
                    if follow:
                        # A steer may have landed after the model's last
                        # words: Claude Code answers it in a harness turn of
                        # its own that opens at once. Watch for it a moment;
                        # its answer is this turn's next message.
                        empty_until = time.monotonic() + follow_s
                        continue
                    ended = "result"
                elif kind == "broker.background_tasks":
                    self.members_changed(turn.thread_id)
                elif kind == "system" and event.get("subtype") == "init":
                    if empty_until is not None and not waiting:
                        empty_until = None   # the steered message's own turn
                    if waiting:
                        park(False)          # the follow-up turn has begun
                elif kind == "broker.exit":
                    ended = "exit"
            lock = self.steer_locks.get(turn.key)
            if lock is not None and lock.locked():
                # Close this turn to new steers, but keep reading while the
                # last write is acknowledged. Events arriving in that gap
                # belong in the queue inherited by the after-result watch.
                self.turn_watch.pop(turn.key, None)
                async with lock:
                    pass
        finally:
            session.unwatch(queue)
            if self.turn_watch.get(turn.key) is queue:
                self.turn_watch.pop(turn.key, None)
            # Schedule attribution diff in the thread pool without awaiting
            # here so remember_session(..., watch=...) runs synchronously on
            # the same tick after flush().
            if vault_snap is not None:
                try:
                    asyncio.get_running_loop().run_in_executor(
                        None,
                        lambda: loam_recent.record_diff(
                            vault_snap, kind="agent", actor=turn.persona,
                            source="turn", started_at=snap_started))
                except Exception:  # noqa: BLE001
                    pass
        # What this turn read is what it has seen -- its prompt and anything
        # steered into it -- unless it died before a word came back: then
        # what it was shown is still news to the next turn, and nothing reads
        # as answered that was not (2026-11-14).
        through = turn.read_through or through
        seen = through
        if ended in ("exit", "closed") and not posted and not reply.spoken() \
                and not (self.turns.get(turn.key) or {}).get("ended_by"):
            seen = (kept or {}).get("through") or "m0"
            steps.log(f"{turn.thread_id} {turn.persona}: turn ended ({ended}) "
                      f"before any reply; its messages stay new to the next turn")
        due = self.summary_due(turn, session) if ended in ("result", "handed") else None
        threads.remember_session(turn.thread_id, turn.persona, session.id,
                                 engine, seen, seq=turn.cursor, summary_due=due,
                                 context=session_context(session),
                                 watch=ended == "result" and getattr(self.broker, "remote", False))
        if ended == "handed":
            # Its words were posted at its `result`; what it waits on is the
            # next turn's to hear, on the same session (2026-11-10). No
            # system line, no after-result watch: the next turn is starting.
            if reply.spoken():
                posted.append(self.post_reply(turn, session, reply, engine, model,
                                              "handed"))
            elif reply.did:
                # Steps since its `result` and before the hand-over; nothing
                # at all is the usual case and writes nothing.
                self.keep_unspoken(turn, session, reply, posted, through, "handed")
            turn.completed, turn.posted = True, bool(posted)
            steps.COUNTS["delivered_while_waiting"] += 1
            tasks = [t.get("description") or t.get("id")
                     for t in background_list(session)]
            steps.log(f"{turn.thread_id} {turn.persona}: input arrived while "
                      f"its turn waited on background work "
                      f"({'; '.join(str(t) for t in tasks) or 'settling'}); "
                      f"delivered now into session {session.id[:8]}")
            mark(session, turn.thread_id)
            return posted[-1] if posted else None
        ended_by = (self.turns.get(turn.key) or {}).get("ended_by")
        if ended_by and ended != "result":
            # Ended from the app (2026-11-10): what it said stays, nothing
            # pretends it was a reply, no mention in it starts a turn, and
            # one quiet line says who ended it.
            if reply.spoken():
                self.post_reply(turn, session, reply, engine, model, "ended",
                                mentions=False)
            else:
                self.keep_unspoken(turn, session, reply, posted, through, "ended")
            turn.completed = True
            line = self.ended_line(turn.thread_id, turn.persona, ended_by)
            self.publish(turn.thread_id, {"type": "message.done", "message": line})
            return None
        if ended in ("exit", "closed") and getattr(self.broker, "stopping", False):
            ended = "stopping"
        if reply.spoken():
            posted.append(self.post_reply(turn, session, reply, engine, model,
                                          ended or "result"))
        else:
            # Steps and no words: the full record keeps them, the readable
            # record and the stream get no message (2026-11-10).
            self.keep_unspoken(turn, session, reply, posted, through,
                               ended or "result")
        turn.posted, turn.completed = bool(posted), ended == "result"
        if ended == "result" and not posted and quiet_turn(turn):
            # A job's result read and left unsaid (2026-11-18): the
            # persona's choice, and nothing goes into the thread.
            steps.log(f"{turn.thread_id} {turn.persona}: {turn.reason} taken in "
                      f"with no words; nothing posted")
        elif ended == "stopping" and not posted:
            # Cut by the broker's stop before a word (2026-11-19): its input
            # is kept on disk and given again when the broker starts, so
            # there is nothing to tell anyone and no line.
            steps.log(f"{turn.thread_id} {turn.persona}: turn cut by the "
                      f"broker's stop before any reply; its input is given "
                      f"again at the next start")
        elif ended != "result" or not posted:
            # A turn that died, or finished saying nothing: a mark on the
            # turn in the app, never a line in the record (2026-11-19; a
            # system line since 2026-11-10).
            self.mark(turn.thread_id, turn.persona,
                      ended_sentence(turn.persona, ended or "result"))
        # Codex states its thread id once the session is open; mark it too.
        mark(session, turn.thread_id)
        if ended == "result":
            self.keep_watching(turn, session, engine, model, backlog=queue)
        return posted[-1] if posted else None

    # -- after the result (2026-11-10) -----------------------------------------

    async def restore_tails(self):
        """Resume after-result watches, including results journaled during an API outage."""
        bindings = threads.load_state("sessions.json")
        if not bindings:
            return
        pending = {(tid, persona) for tid, persona, items in inputs.pending() if items}
        for tid, members in bindings.items():
            for persona, kept in members.items():
                since = kept.get("seq")
                if not kept.get("watch") or (tid, persona) in pending or not isinstance(since, int):
                    continue
                turn = Turn(tid, persona, 0, "notification")
                turn.read_through = kept.get("through")
                try:
                    session = await self.broker.inspect(kept["session_id"])
                except Exception as exc:
                    if getattr(exc, "status", None) != 404:
                        raise
                    threads.finish_watch(tid, persona, kept["session_id"])
                    continue
                if not session.is_live() and session.seq <= since:
                    threads.finish_watch(tid, persona, session.id)
                    continue
                await session.attach(since)
                queue = asyncio.Queue()
                for event in session.replay(since):
                    queue.put_nowait(event)
                self.keep_watching(turn, session, kept["engine"], session.model, backlog=queue)

    def keep_watching(self, turn: Turn, session, engine: str, model: str | None,
                      backlog: asyncio.Queue | None = None) -> None:
        """Keep a light watch on the member's session after its turn's
        `result`: a queue on the session, nothing polled, for as long as the
        session is alive (the reaper ends an idle thread session after
        ``THREAD_IDLE_S``; its exit ends the watch). A harness turn it hears
        to its top-level `result` -- a background task's notification handing
        the persona another turn after `working_behind()` said nothing was
        out -- is posted the moment it finishes, as its own message with its
        own ``message.done``, counted as ``late_prompt``. Before this it
        waited for the persona's next turn and came in marked ``late``;
        that path stays, for a broker restart."""
        if getattr(self.broker, "stopping", False):
            return
        live = getattr(session, "is_live", None)
        if callable(live) and not live() and (backlog is None or backlog.empty()):
            return
        key = (turn.thread_id, turn.persona)
        self.stop_tail(*key)
        queue = backlog if backlog is not None else asyncio.Queue()
        session.watch(queue)
        after = Turn(turn.thread_id, turn.persona, turn.chain, "notification",
                     model=turn.model)
        after.read_through = turn.read_through
        task = asyncio.create_task(self._tail(after, session, engine, model, queue))
        self.tails[key] = {"task": task, "queue": queue, "session": session}

    def stop_tail(self, thread_id: str, persona: str) -> None:
        tail = self.tails.pop((thread_id, persona), None)
        if tail is None:
            return
        tail["session"].unwatch(tail["queue"])
        if not tail["task"].done():
            tail["task"].cancel()

    def stop_all_tails(self) -> None:
        for key in list(self.tails):
            self.stop_tail(*key)

    async def _tail(self, turn: Turn, session, engine: str, model: str | None,
                    queue: asyncio.Queue) -> None:
        reply = Reply()
        foreign = False   # a harness turn somebody else asked for
        key = (turn.thread_id, turn.persona)
        tail_posted: list[dict] = []   # replies this watch posted
        try:
            while True:
                event = await queue.get()
                if event is None:
                    if getattr(self.broker, "remote", False) and not self.broker.stopping:
                        threads.finish_watch(*key, session.id)
                    break
                if isinstance(event, Exception):
                    raise event
                kind = event.get("type")
                if kind == "broker.exit":
                    if getattr(self.broker, "remote", False):
                        threads.finish_watch(*key, session.id)
                    break
                reply.keep(event)
                top = event.get("parent_tool_use_id") is None
                if kind == "broker.user_message" and not event.get("steered"):
                    # A message sent into the session from elsewhere (the
                    # classic view): its answer is not the thread's. A
                    # thread's own steer (2026-11-14) is the thread's.
                    foreign = True
                elif kind == "assistant":
                    for block in (event.get("message") or {}).get("content") or []:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "text" and top:
                            reply.said(block.get("text") or "")
                        elif block.get("type") == "tool_use":
                            label = step_label(block)
                            label = label if top else "↳ " + label
                            reply.step(label, top)
                            self.publish(turn.thread_id, {
                                "type": "turn.step", "persona": turn.persona,
                                "label": label, "steps": len(reply.did),
                                "after_result": True})
                elif kind == "broker.background_tasks" or (
                        kind == "system" and event.get("subtype") in (
                            "init", "task_notification")):
                    if kind == "broker.background_tasks" and event.get("count"):
                        self.note_launched(key, event, reply, tail_posted)
                    self.members_changed(turn.thread_id)
                elif kind == "result" and top:
                    behind = getattr(session, "working_behind", None)
                    more = bool(callable(behind) and behind())
                    if foreign:
                        steps.log(f"{turn.thread_id} {turn.persona}: a turn asked "
                                  f"outside the thread (session {session.id[:8]}); "
                                  f"not posted")
                        reply, foreign = Reply(), False
                    elif reply.spoken():
                        steps.COUNTS["late_prompt"] += 1
                        steps.log(f"{turn.thread_id} {turn.persona}: a reply after the "
                                  f"turn's result (session {session.id[:8]}) posted "
                                  f"as it finished")
                        tail_posted.append(self.post_reply(
                            turn, session, reply, engine, model, "notification"))
                        reply = Reply()
                    elif reply.did and more:
                        steps.COUNTS["carried"] += 1
                    else:
                        if reply.did:
                            self.keep_unspoken(
                                turn, session, reply, [],
                                (threads.session_for(*key) or {}).get("through") or "m0",
                                "notification")
                        reply = Reply()
                    self.members_changed(turn.thread_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a watch must never take the broker down
            steps.log(f"{turn.thread_id} {turn.persona}: the after-result watch "
                      f"failed ({exc!r})")
        finally:
            if self.tails.get(key, {}).get("queue") is queue:
                self.tails.pop(key, None)
                session.unwatch(queue)
                self.members_changed(turn.thread_id)

    # -- which reply launched a background task (2026-11-10) -----------------

    def note_launched(self, key: tuple[str, str], event: dict, reply: "Reply",
                      posted: list[dict] | None = None) -> None:
        """A `broker.background_tasks` event heard while a thread turn (or
        its after-result watch) reads the session: each task in it not seen
        before was launched by the harness turn being heard, whose reply is
        `reply` -- its message id is written on the task when that reply is
        posted (`post_reply`). A task listed before the turn has done or said
        anything (its tool call came in the reply already posted) belongs to
        the last reply posted, `posted[-1]`."""
        origins = self.task_origins.setdefault(key, {})
        live = {t.get("task_id") for t in event.get("tasks") or []
                if isinstance(t, dict)}
        for tid in [t for t in origins if t not in live
                    and origins[t].get("message") is not None]:
            origins.pop(tid, None)
        fresh = not (reply.did or reply.segments or reply.ordered or reply.draft)
        for tid in live:
            if not tid or tid in origins:
                continue
            if fresh and posted:
                origins[tid] = {"message": posted[-1]["id"], "seq": event.get("seq")}
            else:
                origins[tid] = {"message": None, "seq": event.get("seq")}
                reply.launched.append(tid)

    def stamp_launched(self, key: tuple[str, str], reply: "Reply", msg: dict) -> None:
        """The posted reply's id onto the tasks its harness turn launched."""
        origins = self.task_origins.get(key) or {}
        stamped = False
        for tid in reply.launched:
            if tid in origins and origins[tid].get("message") is None:
                origins[tid]["message"] = msg["id"]
                stamped = True
        reply.launched = []
        if stamped:
            self.members_changed(key[0])

    async def _memory_pins(self, thread_id: str, engine: str) -> None:
        """Every provider's own memory off, re-pinned and checked at each
        session start (first release L8; lib/loam_provider_memory.py): a CLI
        that updated itself can have turned one back on. A store that grew
        goes to the system persona through the attention queue. The turn
        proceeds while the persona handles the notice."""
        try:
            def run() -> list[str]:
                # Codex's pins ride CODEX_HOME/config.toml, which the broker
                # renders whole at every Codex thread start (trust_config).
                provider_memory.pin_claude()
                provider_memory.pin_env()
                grew = provider_memory.check(engine)[1]
                import loam_attention  # noqa: PLC0415
                for text in grew:
                    steps.log(f"{thread_id}: provider memory: {text}")
                    loam_attention.tell(
                        text, by="loam",
                        evidence="`lib/loam_provider_memory.py` (the broker's "
                                 "check at a session start)")
                return grew
            await asyncio.to_thread(run)
        except Exception as exc:  # noqa: BLE001 -- a check never costs a turn
            steps.log(f"{thread_id}: the provider memory check failed: {exc}")

    def system_line(self, thread_id: str, text: str) -> dict:
        """A line recording an act someone did in the thread (a draft made,
        an agent made). Never a report of Loam's own state: that is a mark
        (``mark``), since 2026-11-19."""
        line = threads.append(thread_id, "system", text, kind="system")
        self.publish(thread_id, {"type": "message.done", "message": line})
        return line

    # -- marks on a turn (2026-11-19, batch B step 5) ------------------------
    #
    # "Waiting for room", "X is archived", "X's turn failed: ...", "X's turn
    # ended without a reply": until 2026-11-19 each was a Loam line in the
    # thread's record. The record is conversation only now; these are state
    # the app draws on the member's turn, read with the thread
    # (``marks_for``) and streamed as ``turn.mark``.

    def _marks_all(self) -> dict:
        if self._marks is None:
            try:
                self._marks = json.loads(
                    (threads.STATE / MARKS).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._marks = {}
            if not isinstance(self._marks, dict):
                self._marks = {}
        return self._marks

    def _marks_save(self) -> None:
        try:
            path = threads.STATE / MARKS
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.{os.getpid()}")
            tmp.write_text(json.dumps(self._marks or {}), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:  # kept in memory still
            steps.log(f"marks: the record could not be written ({exc})")

    def mark(self, thread_id: str, persona: str, text: str) -> dict:
        """Put ``text`` on ``persona``'s turn in this thread ("" for the
        thread's own), replacing the one before. Nothing is written in the
        record."""
        row = {"persona": persona or None, "text": text, "at": threads.now_iso()}
        self._marks_all().setdefault(thread_id, {})[persona or ""] = row
        self._marks_save()
        steps.log(f"{thread_id} {persona or '-'}: mark: {text}")
        self.publish(thread_id, {"type": "turn.mark", **row})
        if persona:
            import loam_attention
            loam_attention.tell(text, by=f"thread {thread_id}/{persona}",
                                evidence=f"thread {thread_id}")
        return row

    def clear_mark(self, thread_id: str, persona: str) -> None:
        marks = self._marks_all().get(thread_id)
        if not marks or (persona or "") not in marks:
            return
        marks.pop(persona or "", None)
        if not marks:
            self._marks_all().pop(thread_id, None)
        self._marks_save()
        self.publish(thread_id, {"type": "turn.mark", "persona": persona or None,
                                 "text": None})

    def marks_for(self, thread_id: str) -> list[dict]:
        rows = (self._marks_all().get(thread_id) or {}).values()
        return sorted((dict(r) for r in rows if isinstance(r, dict)),
                      key=lambda r: str(r.get("at")))

    # -- system turns: once each, never in a loop (2026-11-17) ---------------
    #
    # A notice the runtime leaves (an update's, a job's its waiting call could
    # not carry) posts a Loam line and gives an agent a turn on its own: no
    # person is there to notice a repeat, and each turn is a paid one. The
    # 0.2.0 walk found an update notice its source could not remove posted
    # and given a turn every 30 s without end. Whatever the source does, the
    # engine now delivers one notice once (its ``key``, kept on disk across
    # restarts), and the same line for the same agent in the same thread not
    # again within SYSTEM_REPEAT_S, under any key.

    def _system_seen(self) -> dict:
        if self._system_turns is None:
            try:
                self._system_turns = json.loads(
                    (threads.STATE / SYSTEM_TURNS).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._system_turns = {}
            if not isinstance(self._system_turns, dict):
                self._system_turns = {}
            self._system_turns.setdefault("keys", {})
            self._system_turns.setdefault("lines", {})
        return self._system_turns

    def system_turn_allowed(self, key: str, tid: str, persona: str,
                            line: str | None) -> str | None:
        """None when this notice may post its line and give its turn, and
        then it is recorded as given; otherwise why not (``already
        delivered``, ``repeat``), and nothing is posted."""
        seen = self._system_seen()
        now = time.time()
        for bucket, keep in (("keys", SYSTEM_KEEP_S), ("lines", SYSTEM_REPEAT_S)):
            seen[bucket] = {k: t for k, t in seen[bucket].items()
                            if isinstance(t, (int, float)) and now - t < keep}
        same = f"{tid} {persona} {line}"
        if key in seen["keys"]:
            return "already delivered"
        if line is None:
            # Nothing is posted (a job's result, 2026-11-18): once per key,
            # and no repeat-line rule, which keys on a posted line.
            seen["keys"][key] = now
            self._system_save()
            return None
        if same in seen["lines"]:
            seen["keys"][key] = now
            self._system_save()
            steps.log(f"{tid} {persona}: notice {key} repeats a line posted "
                      f"{int(now - seen['lines'][same])} s ago; not posted, no turn")
            return "repeat"
        seen["keys"][key], seen["lines"][same] = now, now
        self._system_save()
        return None

    def _system_save(self) -> None:
        try:
            path = threads.STATE / SYSTEM_TURNS
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.{os.getpid()}")
            tmp.write_text(json.dumps(self._system_turns), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:  # kept in memory still: once per process
            steps.log(f"system turns: the record could not be written ({exc})")

    def deliver_job(self, record: dict, note: str) -> str:
        """Deliver the result once to the briefing member in its originating thread.
        Persist it as member input so a restart can recover it. It may join the requesting
        turn or a session parked on background work. Otherwise it waits in order."""
        tid, persona = record.get("thread"), record.get("persona")
        if not tid or not persona:
            return "no thread"
        try:
            meta = threads.read(tid)[0]
        except threads.ThreadError:
            return "thread gone"
        if persona not in meta["members"]:
            return "not a member"
        if (tid, persona) not in self.turns and threads.member_engine(meta, persona) is None:
            return "waiting for a provider"
        why = self.system_turn_allowed(f"job:{record.get('job')}", tid, persona, None)
        if why:
            return why
        steps.log(f"{tid} {persona}: job {record.get('job')} finished; its result "
                  f"queued as this persona's input, nothing posted")
        self.schedule(Turn(tid, persona, 0, f"job:{record.get('job')}", note=note,
                           briefed_at=briefed_epoch(record)))
        return "delivered"

    def deliver_note(self, note: dict) -> str:
        """Record an update notice once for the system persona, which decides whether to notify the owner."""
        import loam_attention  # noqa: PLC0415
        text = note.get("line") or "Loam's update."
        said = loam_attention.tell(text, by="loam update",
                                   key=f"update:{note.get('id')}",
                                   detail=note.get("note"))
        steps.log(f"update notice {note.get('id')} ({note.get('kind')}): "
                  f"{said} to the system persona; nothing posted")
        if said == "repeat":
            return "already delivered"
        return "delivered" if said == "told" else said

    def keep_unspoken(self, turn: Turn, session, reply: "Reply", posted: list[dict],
                      through: str, why: str) -> None:
        """A harness turn's steps with nothing said after them, into the full
        record alone, as an entry with no message: ``"message": null,
        "unspoken": true`` and ``after``, the last message id it follows."""
        if not reply.events and not reply.did:
            return
        after = posted[-1]["id"] if posted else through
        record = steps.entry({"id": None, "author": turn.persona,
                              "engine": getattr(session, "provider", None),
                              "model": used_model(reply, session, turn.model),
                              "at": threads.now_iso()},
                             turn.thread_id, session.id, reply.events,
                             ended=why, unspoken=True, after=after)
        steps.write(turn.thread_id, record)
        steps.log(f"{turn.thread_id} {turn.persona}: {len(reply.did)} step(s) and no "
                  f"words ({why}); kept in the full record only")

    def summary_due(self, turn, session):
        # Once the threshold is known the next message can replace this session.
        now = time.time()
        due = now + threads.PAUSE_S
        # Persistent providers have an earlier, known idle reap. Antigravity
        # releases its process on every turn and keeps a resumable conversation.
        # Floor the private handoff debounce at DEFAULT_THREAD_IDLE_S (600 s) so
        # shorter small-box idle unloads do not spawn a forked summary CLI on
        # every brief pause; ordinary unload/resume reads the CLI's durable
        # history directly without needing a handoff note.
        last = getattr(session, "last_activity", None)
        handoff_floor = max(cfg.THREAD_IDLE_S, cfg.DEFAULT_THREAD_IDLE_S)
        if session.provider != "antigravity" and isinstance(last, (int, float)):
            due = min(due, last + handoff_floor)
        if over_threshold(session_context(session)):
            due = now
        self.handoffs.arm(turn.key, due)
        return due

    def post_reply(self, turn: Turn, session, reply: "Reply", engine: str,
                   model: str | None, why: str, mentions: bool = True) -> dict:
        """One reply into the readable record, its full steps beside it, and
        on the stream as its own ``message.done``."""
        text = reply.spoken()
        extra = {"engine": engine}
        before = reply.before()
        if before and any("text" in seg for seg in before):
            extra["segments"] = before
        text, extra = self.reply_mark(turn, text, extra)
        text, extra, unkept = reply_images_checked(text, extra)
        # The messages steered into this turn since its last reply
        # (2026-11-14): the record says which ones this reply took in.
        active = self.turns.get(turn.key) or {}
        if active.get("steered"):
            extra["steered"] = active.pop("steered")
        used = used_model(reply, session, model)
        if used:
            extra["model"] = used
        seq = next((event["seq"] for event in reversed(reply.events)
                    if isinstance(event.get("seq"), int)), None)
        if seq is not None:
            extra["journal_reply"] = f"{session.id}/{seq}"
        msg, created = threads.append_once(turn.thread_id, turn.persona, text,
                                           did=reply.did, **extra)
        if created:
            steps.COUNTS["replies"] += 1
        if created or not steps.read(turn.thread_id, msg["id"]):
            steps.write(turn.thread_id, steps.entry(
                msg, turn.thread_id, session.id, reply.events, ended=why))
        # `through` stays what the turn read: its own reply is not news to it
        # (the next turn skips its own messages), and another member's message
        # that landed while it ran is (2026-11-10). `seq` moves on.
        read = turn.read_through or (threads.session_for(*turn.key) or {}).get("through") \
            or msg["id"]
        threads.remember_session(turn.thread_id, turn.persona, session.id,
                                 engine, read, seq=seq if seq is not None else getattr(session, "seq", None),
                                 context=session_context(session), summary_due=self.summary_due(turn, session))
        self.publish(turn.thread_id, {"type": "message.done", "message": msg})
        if created:
            seen_refs = {u["ref"] for u in unkept}
            for att in msg.get("attachments") or []:
                if isinstance(att, dict) and att.get("missing"):
                    ref = (att.get("refs") or [att.get("path") or ""])[0]
                    if ref and ref not in seen_refs:
                        seen_refs.add(ref)
                        unkept.append({"ref": ref, "path": att.get("path") or ref,
                                       "reason": "could not be copied when saving the message"})
            if unkept:
                detail = "; ".join(f"{u['ref']} ({u['reason']})" for u in unkept)
                steps.log(f"{turn.thread_id} {turn.persona}: {len(unkept)} image(s) in "
                          f"{msg['id']} could not be kept: {detail}")
                if not str(turn.reason).startswith("unkept-image:"):
                    self.schedule(Turn(turn.thread_id, turn.persona, turn.chain,
                                       f"unkept-image:{msg['id']}", model=turn.model,
                                       note=threads.unkept_image_note(msg["id"], unkept)))
        if reply.launched:
            self.stamp_launched(turn.key, reply, msg)
        if mentions:
            self.agent_mentions(turn, threads.read(turn.thread_id)[0], text, message_id=msg["id"])
        return msg

    def shown_draft(self, turn: Turn, reply: "Reply") -> dict | None:
        """Withhold an incomplete leading reply marker. Stream a valid target as reply_to and only the newly visible text as the delta."""
        if reply.mark is None:
            # The opening may be words already said: a reply whose first
            # words were the marker alone, then steps.
            said = next((seg["text"] for seg in reply.ordered if "text" in seg), None)
            rest, target = (threads.take_reply_mark(said) if said is not None
                            else threads.streamed_opening(reply.draft))
            if rest is None:
                return None
            if target and threads.find(threads.read(turn.thread_id)[1], target) is None:
                target = None
            reply.mark, reply.mark_run = target or "", bool(target)
        draft = reply.draft
        if reply.mark_run:
            rest, target = threads.take_reply_mark(draft)
            if target == reply.mark:
                draft = rest
        before, reply.shown = reply.shown, draft
        out = {"text": draft[len(before):] if draft.startswith(before) else draft,
               "draft": draft}
        if reply.mark:
            out["reply_to"] = reply.mark
        return out

    def reply_mark(self, turn: Turn, text: str, extra: dict) -> tuple[str, dict]:
        """Extract a leading reply target from the first text segment. Preserve the original words if the target is absent from the thread."""
        segments = [dict(seg) for seg in extra.get("segments") or []]
        first = next((seg for seg in segments
                      if isinstance(seg.get("text"), str) and seg["text"].strip()), None)
        opening = first["text"] if first is not None else text
        rest, target = threads.take_reply_mark(opening)
        if target is None:
            return text, extra
        msgs = threads.read(turn.thread_id)[1]
        if threads.find(msgs, target) is None:
            steps.log(f"{turn.thread_id} {turn.persona}: reply marker names "
                      f"{target}, which this thread does not hold; kept as text")
            return text, extra
        if first is not None:
            first["text"] = rest
            extra = {**extra, "segments": segments}
        else:
            text = rest
        steps.COUNTS["reply_to"] += 1
        return text, {**extra, "reply_to": target}

    async def recover_late(self, turn: Turn, session, kept: dict, engine: str,
                     model: str | None, before: int | None = None) -> None:
        """Post and count each reply journaled after the previous watcher finished as a separate late message."""
        since = kept.get("seq")
        replay = getattr(session, "replay", None)
        if not isinstance(since, int) or not callable(replay):
            return
        try:
            events = await session.history(since) if hasattr(session, "history") else replay(since)
            events = [e for e in events if isinstance(e, dict)
                      and (before is None or e.get("seq", before) < before)]
        except Exception as exc:  # noqa: BLE001 - recovery must not stop a turn
            steps.log(f"{turn.thread_id} {turn.persona}: late-reply check failed ({exc})")
            return
        for chunk in steps.split_replies(events):
            if not any(e.get("type") == "result" and e.get("parent_tool_use_id") is None
                       for e in chunk):
                continue
            if any(e.get("type") == "broker.user_message" for e in chunk):
                # Asked from outside the thread (the classic view): its
                # answer is not the thread's (2026-11-10).
                continue
            late = Reply.of(chunk)
            if not late.spoken():
                continue
            steps.COUNTS["late_recovered"] += 1
            steps.log(f"{turn.thread_id} {turn.persona}: a reply nobody was watching "
                      f"(session {session.id[:8]}, after seq {since}) recovered")
            said, marked = self.reply_mark(turn, late.spoken(), {})
            said, marked = reply_images(said, marked)
            last = chunk[-1].get("seq")
            if last is not None:
                marked["journal_reply"] = f"{session.id}/{last}"
            msg, created = threads.append_once(
                turn.thread_id, turn.persona, said, did=late.did, engine=engine,
                late=True, **marked, **({"model": used} if (used := used_model(
                    late, session, model)) else {}))
            if created or not steps.read(turn.thread_id, msg["id"]):
                steps.write(turn.thread_id, steps.entry(msg, turn.thread_id, session.id,
                                                        chunk, ended="late"))
            self.publish(turn.thread_id, {"type": "message.done", "message": msg})

    def posted(self, thread_id: str, message_id: str, by: str) -> dict:
        """Broadcast an external post after it is durable. Schedule its mentions once under
        the same chain cap as a reply, accounting for both the poster’s turn and intervening posts."""
        meta, msgs, _ = threads.read(thread_id)
        msg = next((m for m in msgs if m.get("id") == message_id), None)
        if msg is None:
            raise threads.ThreadError(404, f"{thread_id} has no message {message_id}")
        if msg.get("author") != by or by not in meta["members"]:
            raise threads.ThreadError(400, f"{message_id} is not {by}'s post")
        seen = self.__dict__.setdefault("_posted", set())
        if (thread_id, message_id) in seen:
            return {"woke": [], "already": True}
        seen.add((thread_id, message_id))
        self.publish(thread_id, {"type": "message.done", "message": msg})
        text = msg.get("text") or ""
        live = self.turn_live.get((thread_id, by))
        since = 0
        for m in reversed(msgs):
            if threads.is_owner(m.get("author")):
                break
            # Posts alone (a reply carries its `engine`): replies are
            # bounded by their turns' own chain, and two agents posting at
            # each other from other threads are bounded by this count.
            if m.get("kind") == "message" and not m.get("engine") \
                    and m.get("author") in meta["members"]:
                since += 1
        chain = max(live.chain if live else 0, since - 1)
        named = [p for p in threads.mentions(text) if p != by]
        self.agent_mentions(Turn(thread_id, by, chain, "post"), meta, text)
        woke = [p for p in named if chain + 1 <= threads.AGENT_CHAIN_CAP]
        steps.log(f"{thread_id} {by}: post {message_id} outside a turn; "
                  f"{'woke ' + ', '.join(woke) if woke else 'no turn given'}"
                  f"{' (chain cap)' if named and not woke else ''}")
        return {"woke": woke}

    def agent_mentions(self, turn: Turn, meta: dict, text: str, message_id=None) -> None:
        # `@all` from a persona is plain text: no fan-out (2026-11-10), so two
        # agents cannot set every member answering every other.
        named = [p for p in threads.mentions(text) if p != turn.persona]
        if not named:
            return
        chain = turn.chain + 1
        if chain > threads.AGENT_CHAIN_CAP:
            # The thread's own mark, cleared when the owner next writes.
            self.mark(turn.thread_id, "",
                      f"Paused after {threads.AGENT_CHAIN_CAP} turns between agents; "
                      f"{', '.join(threads.display(p) for p in named)} will answer "
                      f"when {lc.OWNER_NAME} next writes or tags them.")
            return
        self._join(turn.thread_id, meta, named, by=turn.persona)
        for persona in named:
            mention = Turn(turn.thread_id, persona, chain, "mention")
            if message_id:
                delivery = self.message_input(mention, message_id)
                if delivery and not delivery.held:
                    self.schedule(delivery, persist=False)
            else:
                self.schedule(mention)
