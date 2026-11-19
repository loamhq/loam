"""Schedules: the one path a record changes by, the log, and the reconciler.

A schedule is a record, ``ai-labs/<agent>/crons/<name>.md`` (grammar and
shapes in lib/loam_cron.py), and **a record is live exactly when it says it is
enabled**. Nobody installs anything: whenever a record changes, the reconciler
here installs, moves, pauses or removes its timer, within a minute. Decided by
the owner 2026-10-14 (design: loam-ui's ``design/schedules-and-activity.md``),
replacing ``approved:`` plus a hand ``bin/install-cron``, after Iris's
``vocabulary-audit`` sat approved and uninstalled for six days.

Three things live here and nowhere else:

**The path.** ``change()`` is how a record is written by anyone who can say
who they are: the broker's routes, the agent's tool, ``bin/schedule`` for an
operator, ``bin/wake`` marking a one-off done. It validates (``validate()``),
writes, logs, and reconciles at once. An agent may change only its own
records and must give a reason; the one-off caps are enforced here, not in a
page. A record has no key for tools (loam_cron.HEADER_KEYS): every lane has
the persona's whole tool list, engineer included (2026-11-09).

**The log.** ``state/schedules/events.jsonl``, append-only, one JSON object a
line. ``kind`` is ``change`` (actor, lane, record, before -> after, reason),
``refused`` (the same, with why), ``claim`` (a direct write's author, see
below), ``timer`` (what the reconciler did to the box), ``fire`` (a run's
outcome: ran, failed, skipped while another run of the persona was live,
missed, or killed -- by its wall clock in bin/wake, by a signal in
bin/wake-failed) or ``run`` (the owner's Run now, from the broker). The
broker's routes read and write it through lib/loam_activity.py.

**The reconciler.** ``reconcile()``, run by ``loam-schedules.timer`` every
minute and by ``change()`` straight after a write. It keeps a copy of each
record as last accepted (``state/schedules/applied/``) and renders the timers
from those copies, never from a file it has not accepted. A record that
changed without coming through ``change()`` -- an agent editing its own file,
as every agent did before this path existed -- is validated as that agent's
change, and its ``reason:`` header is the reason; without one it is refused,
logged once, and the timer stays as it was. One exception, applied rather
than refused: a cron's file deleted outright, because keeping its timer would
fire a lane whose file is gone.

**A claim** is how a writer that is not the agent says a direct write is
its own: loam-ui's Files editor (lib/loam_files.py) logs ``kind: claim``
with the bytes' digest, actor ``owner.id``, lane ``app``, just before its
rename, and the reconciler takes a file whose digest a claim names as that
actor's change -- no ``reason:`` asked of the owner, the rules otherwise the
same. A file that does not parse is refused as theirs, logged once, and the
version accepted before it keeps running (decided 2026-10-15: a schedule's
detail and editing are its file, opened in Files).

One run of a persona at a time is unchanged: that is bin/wake's run lock, and
a fire that finds it held waits, skips, and logs ``fire`` / ``skipped``.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import loam_config as lc

import loam_cron
from loam_cron import REFLECTION, Cron, CronError
from loam_memory import CRON_LANE_DIR, front_matter

STATE = Path(os.environ.get("LOAM_SCHEDULE_STATE") or lc.STATE / "schedules")
WAKE_STATE = lc.STATE / "wake"
# The broker's own session records (lib/loam_broker.SESSION_DIR): how the
# reconciler tells that a direct edit came from a conversation.
BROKER_SESSIONS = lc.STATE / "broker" / "sessions"
# A conversation whose last event is this recent still owns an edit: the
# reconciler runs once a minute, so an edit made as a turn ended is read up
# to a minute after the turn's last event.
CONVERSATION_GRACE = timedelta(minutes=3)

# Who may change a record. A persona is named by its agent name and may touch
# its own records only. "loam" is the runtime itself (a one-off marked done).
SYSTEM_ACTORS = tuple(dict.fromkeys((lc.OWNER_ID, *lc.LEGACY_OWNER_IDS, "engineer", "loam")))
# `app` is the owner's page, through the broker's routes (2026-10-14).
LANES = ("conversation", "wake", "cron", "reconciler", "engineer", "app",
         "unknown")

# The owner's defaults (design, 2026-10-14): a wake that schedules its own next
# wake is the cost loop these exist to prevent.
ONE_OFF_MAX_PENDING = 5
ONE_OFF_MIN_AHEAD = timedelta(minutes=10)
ONE_OFF_MAX_AHEAD = timedelta(days=30)
# A one-off still not done this long after its moment never ran: the box was
# down, or the run was killed before bin/wake could mark it. The bound is
# above the longest a run can take (lock wait plus the briefing lane's clock).
ONE_OFF_STALE_AFTER = timedelta(hours=3)

# Shape-neutral since 2026-11-18: an agent with a map has no
# `_reflection.md` to load, and its wake's instruction is Loam's default.
REFLECTION_BODY = (
    "The nightly reflection wake. Its instruction is `_reflection.md`, or "
    "Loam's default once there is a `map.md`; this record holds only when it "
    "runs and whether it is on.\n")


class Refused(Exception):
    """A change the path will not make. The message says why."""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha(text: str | None) -> str | None:
    return None if text is None else hashlib.sha256(
        text.encode("utf-8")).hexdigest()[:16]


def events_path() -> Path:
    return STATE / "events.jsonl"


def applied_path(agent: str, name: str) -> Path:
    return STATE / "applied" / agent / f"{name}.md"


def record_path(agent: str, name: str) -> Path:
    return loam_cron.cron_path(loam_cron.AI_LABS / agent, name)


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


@contextmanager
def locked():
    """One writer of records, snapshots and units at a time."""
    STATE.mkdir(parents=True, exist_ok=True)
    with open(STATE / "reconcile.lock", "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# The log.
# ---------------------------------------------------------------------------

def log(event: dict) -> dict:
    event = {"ts": iso(now_utc()), "id": uuid.uuid4().hex[:12], **event}
    STATE.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False) + "\n"
    with open(events_path(), "a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.write(line)
        fcntl.flock(handle, fcntl.LOCK_UN)
    return event


def events() -> list[dict]:
    out = []
    try:
        lines = events_path().read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return out
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def header_of(text: str | None) -> dict[str, str]:
    return front_matter(text) if text else {}


# ---------------------------------------------------------------------------
# Header edits. Mechanical only: one key's line set or removed, everything
# else -- every other key, the body -- byte for byte as it was.
# ---------------------------------------------------------------------------

HEADER_RE = re.compile(r"\A---\r?\n(?P<lines>.*?)^---[ \t]*\r?\n", re.DOTALL | re.M)


def set_header(text: str, key: str, value: str | None) -> str:
    match = HEADER_RE.match(text)
    lines = match["lines"].splitlines() if match else []
    rest = text[match.end():] if match else "\n" + text
    out, found = [], False
    for line in lines:
        if line.partition(":")[0].strip() == key:
            found = True
            if value is not None:
                out.append(f"{key}: {value}")
        else:
            out.append(line)
    if not found and value is not None:
        out.append(f"{key}: {value}")
    return "---\n" + "".join(f"{line}\n" for line in out) + "---\n" + rest


# ---------------------------------------------------------------------------
# Validation: the one place a change is judged.
# ---------------------------------------------------------------------------

def problem(name: str, text: str | None, exc: Exception) -> dict:
    """Why a record is not running, in words a person reads on one line:
    ``{"why", "line", "text"}``. ``line`` (1-based) and ``text`` are the line
    of the header key the fault is in, None when it is in no one line (a
    missing key, the body, the size)."""
    why = re.sub(rf"\S*/crons/{re.escape(name)}\.md", f"crons/{name}.md",
                 str(exc))
    if "The grammar is:" in why:
        why = (why.split("The grammar is:")[0].rstrip()
               + f" A schedule reads `<when> at HH:MM {lc.ZONE_TOKEN}`, where <when> is "
                 "daily, weekdays, weekends, or days such as `Sundays` or "
                 "`Mondays and Thursdays`.")
    line = found = None
    key = getattr(exc, "key", None)
    match = HEADER_RE.match(text or "")
    if key and match:
        for number, raw in enumerate(match["lines"].splitlines(), start=2):
            if raw.partition(":")[0].strip() == key:
                line, found = number, raw
                break
    return {"why": why, "line": line, "text": found}


def parse(agent: str, name: str, text: str) -> Cron:
    try:
        return loam_cron.parse_record(agent, name, text)
    except CronError as exc:
        raise Refused(str(exc)) from exc


def pending_one_offs(agent: str, excluding: str,
                     now: datetime | None = None) -> list[str]:
    """The one-offs that can still fire: not done, enabled, moment ahead.

    A paused one-off, or one whose moment has passed, cannot fire without
    being changed again, and that change is validated against this cap then.
    Counting them refused a persona's five overnight one-offs on 2026-11-09: three
    paused one-offs from that morning made every new one see seven pending.
    A header that does not parse is counted, as the cautious reading."""
    now = now or now_utc()
    folder = loam_cron.AI_LABS / agent / CRON_LANE_DIR
    out = []
    for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        if path.stem == excluding:
            continue
        head = header_of(read_text(path))
        if not head.get("at") or head.get("done"):
            continue
        try:
            if not loam_cron.parse_enabled(head):
                continue
            if loam_cron.parse_at(head["at"])[2] <= now:
                continue
        except CronError:
            pass
        out.append(path.stem)
    return out


def validate(agent: str, name: str, text: str | None, *, actor: str,
             lane: str, reason: str | None, previous: str | None,
             now: datetime | None = None) -> Cron | None:
    """Raise Refused, or return the record as it will stand (None: deleted).

    The rules, in the order they are asked:
      - the actor is the owner, the engineer, the runtime, or the persona whose
        record it is; a persona's or the engineer's change carries a reason
      - the lane is one of LANES
      - the reflection's record is never deleted (pause it instead)
      - the record parses (loam_cron.parse_record: shape, keys, size cap)
      - a one-off newly set or moved is 10 min to 30 days ahead, and the
        persona has at most five pending
    """
    now = now or now_utc()
    roster = loam_cron.roster()
    if agent not in roster:
        raise Refused(f"{agent!r} is not an agent on this box")
    if actor not in SYSTEM_ACTORS and actor not in roster:
        raise Refused(f"{actor!r} is not someone who can change a schedule")
    if actor in roster and actor != agent:
        raise Refused(f"{actor} may change its own schedules only, not "
                      f"{agent}'s")
    if not (lc.is_owner(actor) or actor == "loam") and not (reason or "").strip():
        raise Refused(f"a change by {actor} carries a reason, and this one "
                      f"has none. Say why the schedule is changing.")
    if lane not in LANES:
        raise Refused(f"{lane!r} is not a lane ({', '.join(LANES)})")
    if text is None:
        if previous is None:
            raise Refused(f"there is no record {agent}/{name} to delete")
        if name == REFLECTION:
            raise Refused("the nightly reflection's record is not deleted; "
                          "pause it with `enabled: false`")
        return None

    cron = parse(agent, name, text)
    if cron.kind == "one-off" and not cron.done:
        before = header_of(previous)
        moved = (previous is None or before.get("at") != header_of(text)["at"]
                 or bool(before.get("done")))
        if moved:
            ahead = cron.at - now
            if ahead < ONE_OFF_MIN_AHEAD:
                raise Refused(
                    f"a one-off is set at least 10 minutes ahead; "
                    f"{cron.schedule_text} is {_minutes(ahead)} from now")
            if ahead > ONE_OFF_MAX_AHEAD:
                raise Refused(
                    f"a one-off is set at most 30 days ahead; "
                    f"{cron.schedule_text} is {ahead.days} days from now")
        others = pending_one_offs(agent, excluding=name, now=now)
        if len(others) >= ONE_OFF_MAX_PENDING:
            raise Refused(
                f"{agent} has {len(others)} one-offs pending "
                f"({', '.join(others)}); the cap is {ONE_OFF_MAX_PENDING}")
    return cron


def _minutes(delta: timedelta) -> str:
    return f"{round(delta.total_seconds() / 60)} min"


def op_of(before: str | None, after: str | None) -> str:
    if before is None:
        return "create"
    if after is None:
        return "delete"
    b, a = header_of(before), header_of(after)
    if a.get("done") and not b.get("done"):
        return "done"
    was = loam_cron.parse_enabled(b) if b else False
    try:
        now_on = loam_cron.parse_enabled(a)
    except CronError:
        now_on = was
    if was and not now_on:
        return "pause"
    if now_on and not was:
        return "resume"
    if (b.get("schedule"), b.get("at")) != (a.get("schedule"), a.get("at")):
        return "move"
    return "edit"


# ---------------------------------------------------------------------------
# The path.
# ---------------------------------------------------------------------------

def change(agent: str, name: str, text: str | None, *, actor: str, lane: str,
           reason: str | None = None, now: datetime | None = None,
           reconcile_now: bool = True, systemd: "Systemd | None" = None,
           extra: dict | None = None) -> dict:
    """Write (or, with ``text=None``, delete) one record. Returns the logged
    event. Raises Refused, after logging the refusal, if it may not.
    ``extra`` rides on the event as it is (an undo names what it undoes)."""
    with locked():
        return _change(agent, name, text, actor=actor, lane=lane,
                       reason=reason, now=now, reconcile_now=reconcile_now,
                       systemd=systemd, extra=extra)


def _change(agent, name, text, *, actor, lane, reason, now, reconcile_now,
            systemd, extra=None) -> dict:
    path = record_path(agent, name)
    previous = read_text(path)
    base = {"agent": agent, "record": name, "actor": actor, "lane": lane,
            "reason": (reason or "").strip() or None, **(extra or {})}
    try:
        validate(agent, name, text, actor=actor, lane=lane, reason=reason,
                 previous=previous, now=now)
    except Refused as exc:
        log({"kind": "refused", **base, "why": str(exc),
             "before": previous, "after": text})
        raise
    if text is None:
        path.unlink()
    else:
        write_atomic(path, text)
    event = log({"kind": "change", "op": op_of(previous, text), **base,
                 "before": previous, "after": text,
                 "before_sha": sha(previous), "after_sha": sha(text)})
    if reconcile_now:
        _reconcile([agent], now=now, systemd=systemd)
    return event


def claim(agent: str, name: str, text: str, *, actor: str, lane: str) -> dict:
    """Say, before a direct write lands, whose these bytes are: the
    reconciler takes a file with this digest as ``actor``'s change on
    ``lane``. Logged as it is; it judges nothing, the reconciler does."""
    # As the reconciler will read the file back: newlines translated.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return log({"kind": "claim", "agent": agent, "record": name,
                "actor": actor, "lane": lane, "sha": sha(text)})


def set_key(agent: str, name: str, key: str, value: str | None, **kw) -> dict:
    """``change()`` of one header key, the rest of the record untouched."""
    text = read_text(record_path(agent, name))
    if text is None:
        raise Refused(f"there is no record {agent}/{name}")
    return change(agent, name, set_header(text, key, value), **kw)


# ---------------------------------------------------------------------------
# Fires. bin/wake reports every run of a scheduled lane here.
# ---------------------------------------------------------------------------

def record_fire(agent: str, name: str, outcome: str, **detail) -> None:
    """Log a fire, and mark a one-off done. Never raises into the caller: a
    wake's bookkeeping must not turn a run that happened into a crash."""
    try:
        log({"kind": "fire", "agent": agent, "record": name,
             "outcome": outcome, **detail})
        text = read_text(record_path(agent, name))
        if text and header_of(text).get("at") and not header_of(text).get("done"):
            change(agent, name,
                   set_header(text, "done", f"{iso(now_utc())} {outcome}"),
                   actor="loam", lane="cron", reason=f"fired once: {outcome}")
    except Exception as exc:  # noqa: BLE001
        print(f"loam_schedule: could not record the fire of {agent}/{name}: "
              f"{exc}", flush=True)


def enabled_now(workspace: Path, name: str) -> tuple[bool, str]:
    """Should a launch of this lane run? bin/wake asks at launch, so a timer
    standing a minute after its record was paused does not fire the lane.
    A reflection with no record runs: its record is the switch, not the
    permission. Read from the workspace the wake was given."""
    text = read_text(loam_cron.cron_path(Path(workspace), name))
    if text is None:
        return (name == REFLECTION, f"no record crons/{name}.md")
    head = header_of(text)
    try:
        if not loam_cron.parse_enabled(head):
            return False, f"crons/{name}.md says it is not enabled"
    except CronError as exc:
        return False, str(exc)
    if head.get("done"):
        return False, f"crons/{name}.md is a one-off already done ({head['done']})"
    return True, ""


# ---------------------------------------------------------------------------
# The box. A thin seam so the tests can point it at a scratch unit directory
# (runtime-scoped enables) and the real manager.
# ---------------------------------------------------------------------------

RUNTIME = False     # the tests' switch: enable --runtime, nothing persisted
WRITE_PAGE = True   # and theirs: the vault page is the real box's alone


class Systemd:
    def __init__(self, unit_dir: Path | None = None,
                 runtime: bool | None = None):
        self.unit_dir = unit_dir or loam_cron.UNIT_DIR
        self.runtime = RUNTIME if runtime is None else runtime

    def run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["systemctl", "--user", *args],
                              capture_output=True, text=True, check=False,
                              timeout=60)

    def reload(self) -> None:
        self.run("daemon-reload")

    def arm(self, timer: str) -> None:
        """A NEW timer only. `enable` alone does not arm a timer in the
        running manager (measured 2026-09-05), so it is started too; a timer
        that has never triggered does not fire on start.

        A timer that already stands is never restarted, stopped or started
        here: the daemon-reload is what moves it. Measured 2026-10-14, the
        hard way: the migration's reconcile `restart`ed a persona's two re-cut
        cron timers, both of which had fired before, and both services
        started on the spot (00:16:31Z; stopped by hand 27 s later). Iris's,
        never triggered, did not. A probe the same evening showed
        daemon-reload alone re-arming a triggered timer's changed OnCalendar
        with no fire. The suffix is always written."""
        assert timer.endswith(".timer")
        # Never a plain start of a timer that has stood before: that fires it
        # (lib/loam_due.py). loam_units.arm_timer leaves a standing timer to
        # the reload and makes an inactive one fresh before it starts it.
        import loam_units  # noqa: PLC0415
        return loam_units.arm_timer(timer, unit_dir=self.unit_dir,
                                    runtime=self.runtime, run=self.run)

    def disarm(self, timer: str) -> None:
        assert timer.endswith(".timer")
        self.run("disable", "--now", *(["--runtime"] if self.runtime else []),
                 timer)
        self.run("stop", timer)

    def next_elapse(self, timer: str) -> str:
        out = self.run("show", "-p", "NextElapseUSecRealtime", "--value", timer)
        return out.stdout.strip()

    def last_trigger(self, timer: str) -> str:
        out = self.run("show", "-p", "LastTriggerUSec", "--value", timer)
        value = out.stdout.strip()
        return "" if value in ("", "n/a", "0") else value

    def timers(self, timers: list[str]) -> dict[str, dict]:
        """{timer: {"next": iso | None, "last": iso | None,
        "running_since": iso | None}}, in two asks of the manager: the
        timers, then the services they fire. A timer it does not hold reads
        as none of them.

        ``running_since`` is when the timer's service, in flight now, began
        (a wake is a oneshot, so in flight reads ``activating``, and its
        ActiveEnterTimestamp stays empty until it ends: the start is
        InactiveExitTimestamp). A timer whose service it fired is still
        running has no next elapse at all in systemd 252 -- `show` and
        `list-timers` both blank it, and TimersCalendar's ``next_elapse``
        is the trigger just past (measured 2026-10-23 on a transient
        timer). Its next is then the calendar's own, after now, which is
        what the manager arms when the run ends."""
        out: dict[str, dict] = {t: {"next": None, "last": None,
                                    "running_since": None} for t in timers}
        if not timers:
            return out
        shown = self.run("show", "-p", "Id,NextElapseUSecRealtime,"
                         "LastTriggerUSec,SubState,TimersCalendar,Unit", *timers)
        services: dict[str, str] = {}
        for props in _blocks(shown.stdout):
            timer = props.get("Id")
            if timer not in out:
                continue
            nxt = _shown_iso(props.get("NextElapseUSecRealtime"))
            if nxt is None and props.get("SubState") == "running":
                nxt = calendar_next(re.findall(r"OnCalendar=(.*?) ;",
                                               props.get("TimersCalendar", "")))
            out[timer].update(next=nxt,
                              last=_shown_iso(props.get("LastTriggerUSec")))
            if props.get("Unit"):
                services[props["Unit"]] = timer
        if services:
            shown = self.run("show", "-p",
                             "Id,ActiveState,InactiveExitTimestamp", *services)
            for props in _blocks(shown.stdout):
                timer = services.get(props.get("Id", ""))
                if timer and props.get("ActiveState") in IN_FLIGHT:
                    out[timer]["running_since"] = _shown_iso(
                        props.get("InactiveExitTimestamp"))
        return out

    def start(self, service: str) -> subprocess.CompletedProcess:
        """Run now: one fire of a lane's service, not waited on. The run is
        bin/wake's, with its lock and its enabled check, as from a timer."""
        assert service.endswith(".service")
        return self.run("start", "--no-block", service)


IN_FLIGHT = ("activating", "active", "deactivating", "reloading")


def _blocks(stdout: str) -> list[dict]:
    """`systemctl show` of several units: one dict of properties each."""
    return [dict(line.partition("=")[::2] for line in block.splitlines())
            for block in stdout.strip().split("\n\n")]


def calendar_next(specs: list[str]) -> str | None:
    """The soonest elapse after now of OnCalendar specs, as ISO UTC, by
    `systemd-analyze calendar`: the manager's own arithmetic, zones
    included. None for no spec, or one it will not read."""
    if not specs:
        return None
    try:
        done = subprocess.run(["systemd-analyze", "calendar", *specs],
                              capture_output=True, text=True, check=False,
                              timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    found = []
    for line in done.stdout.splitlines():
        key, _, value = line.strip().partition(": ")
        if key == "(in UTC)":
            found.append(iso(datetime.strptime(
                " ".join(value.split()[1:3]), "%Y-%m-%d %H:%M:%S")
                .replace(tzinfo=timezone.utc)))
        elif key == "Next elapse" and value.endswith(" UTC"):
            # A box in UTC prints no "(in UTC)" line under it.
            found.append(_shown_iso(value))
    found = [f for f in found if f]
    return min(found) if found else None


def _shown_iso(value: str | None) -> str | None:
    """`Thu 2026-10-15 03:20:00 PDT` (systemctl show, in the box's own
    zone; systemd 252 ignores --timestamp=unix on show) as ISO UTC."""
    parts = (value or "").split()
    if len(parts) < 3:
        return None
    try:
        moment = datetime.strptime(f"{parts[1]} {parts[2]}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return iso(moment.astimezone())


# ---------------------------------------------------------------------------
# The reconciler.
# ---------------------------------------------------------------------------

def reconcile(names: list[str] | None = None, now: datetime | None = None,
              systemd: Systemd | None = None) -> list[dict]:
    with locked():
        return _reconcile(names, now=now, systemd=systemd)


def running_lane(agent: str, now: datetime | None = None) -> str:
    """Best attribution for a record edited outside the path: the lane of the
    agent's run in flight, if any; else `conversation` when the broker's own
    record of one of the agent's chats says it is mid-turn or spoke within
    CONVERSATION_GRACE; else unknown, said as such. A wake's marker wins over
    a chat: the wake holds the run lock and a chat does not, so both can be
    live, and the marker names a run where a chat's record names only a
    window. (Closed 2026-10-14; step 1 attributed every chat's edit as
    unknown.)"""
    try:
        lane = json.loads((WAKE_STATE / f"{agent}.running").read_text(
            encoding="utf-8")).get("lane", "")
    except (OSError, json.JSONDecodeError):
        return "conversation" if in_conversation(agent, now) else "unknown"
    return "cron" if lane.startswith("cron:") else "wake"


def in_conversation(agent: str, now: datetime | None = None) -> bool:
    now = now or now_utc()
    for path in BROKER_SESSIONS.glob("*.json") if BROKER_SESSIONS.is_dir() else []:
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("agent") != agent or not meta.get("live"):
            continue
        if meta.get("status") in ("working", "needs_input", "waiting"):
            return True
        try:
            last = datetime.strptime(meta.get("last_event_at") or "",
                                     "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if now - last <= CONVERSATION_GRACE:
            return True
    return False


def _accept(agent: str, name: str, current: str | None, previous: str | None,
            now: datetime) -> bool:
    """Has this record's current text come through the path, or can it be
    taken as its agent's own direct edit? Logs the change or the refusal."""
    digest = sha(current)
    claimed = None
    for event in reversed(events()):
        if (event.get("agent"), event.get("record")) != (agent, name):
            continue
        if event.get("kind") == "change" and event.get("after_sha") == digest:
            return True
        if event.get("kind") == "refused" and event.get("direct") and \
                sha(event.get("after")) == digest:
            return False        # already refused, already logged once
        if event.get("kind") == "claim" and event.get("sha") == digest \
                and current is not None and claimed is None:
            claimed = event
        if event.get("kind") == "change":
            break
    if claimed:
        actor, lane = claimed["actor"], claimed["lane"]
    else:
        actor, lane = agent, running_lane(agent)
    reason = header_of(current).get("reason")
    base = {"agent": agent, "record": name, "actor": actor, "lane": lane,
            "reason": reason or None, "direct": True}
    deleting_cron = current is None and name != REFLECTION
    try:
        if not deleting_cron:
            validate(agent, name, current, actor=actor, lane=lane,
                     reason=reason, previous=previous, now=now)
    except Refused as exc:
        log({"kind": "refused", **base, "why": str(exc),
             "before": previous, "after": current})
        return False
    log({"kind": "change", "op": op_of(previous, current), **base,
         "before": previous, "after": current,
         "before_sha": sha(previous), "after_sha": digest})
    return True


def desired_units(agent: str, name: str, text: str) -> dict[str, str | None]:
    """{unit filename: content, or None for absent}, for an accepted record.

    An archived agent (lib/loam_agents.py, 2026-11-14) wants none of its
    timers: they come down with the archive and back with the restore, from
    the same accepted record, which the archive never touches."""
    cron = loam_cron.parse_record(agent, name, text, check_systemd=False)
    import loam_agents  # noqa: PLC0415
    if loam_agents.is_archived(agent, loam_cron.AI_LABS):
        if cron.kind == "reflection":
            return {f"{cron.unit}.timer": None}
        return {f"{cron.unit}.service": None, f"{cron.unit}.timer": None}
    if cron.kind == "reflection":
        # The service is bin/provision's; only the clock is the record's.
        return {f"{cron.unit}.timer": loam_cron.reflection_timer(
            agent, cron.schedule_text) if cron.live else None}
    if not cron.live:
        return {f"{cron.unit}.service": None, f"{cron.unit}.timer": None}
    service, timer = loam_cron.render_units(cron)
    return {f"{cron.unit}.service": service, f"{cron.unit}.timer": timer}


def _reconcile(names: list[str] | None, now: datetime | None,
               systemd: Systemd | None) -> list[dict]:
    now = now or now_utc()
    systemd = systemd or Systemd()
    names = names if names is not None else loam_cron.roster()
    unit_dir = systemd.unit_dir
    actions: list[dict] = []
    stale: list[tuple[str, str, str]] = []

    for agent in names:
        folder = loam_cron.AI_LABS / agent / CRON_LANE_DIR
        files = {p.stem for p in folder.glob("*.md")} if folder.is_dir() else set()
        snaps_dir = STATE / "applied" / agent
        snaps = {p.stem for p in snaps_dir.glob("*.md")} if snaps_dir.is_dir() else set()

        wanted: dict[str, str | None] = {}
        for name in sorted(files | snaps):
            current = read_text(record_path(agent, name))
            previous = read_text(applied_path(agent, name))
            if current != previous and _accept(agent, name, current, previous, now):
                if current is None:
                    applied_path(agent, name).unlink(missing_ok=True)
                else:
                    write_atomic(applied_path(agent, name), current)
                previous = current
            if previous is None:
                continue
            try:
                wanted.update(desired_units(agent, name, previous))
                cron = loam_cron.parse_record(agent, name, previous,
                                              check_systemd=False)
            except CronError:
                continue
            if cron.kind == "one-off" and cron.live and \
                    now - cron.at > ONE_OFF_STALE_AFTER:
                stale.append((agent, name, cron.unit))
            if cron.kind == "reflection" and cron.live:
                _sync_agent_json(agent, loam_cron.reflection_hhmm(
                    cron.schedule_text))

        # Orphans: a cron unit of this agent's that no accepted record wants.
        for unit in unit_dir.glob(f"{loam_cron.UNIT_PREFIX}{agent}-*"):
            if unit.suffix in (".service", ".timer") and unit.name not in wanted:
                try:
                    owner = loam_cron.split_unit(unit.name, names)[0]
                except CronError:
                    continue
                if owner == agent:
                    wanted[unit.name] = None

        actions += _apply(agent, wanted, systemd)

    if actions:
        systemd.reload()
        for action in actions:
            # A recut timer is moved by the reload above and nothing else.
            if action["action"] == "installed":
                systemd.arm(action["unit"])
            action["next"] = systemd.next_elapse(action["unit"])
            log(action)

    for agent, name, unit in stale:
        fired = systemd.last_trigger(f"{unit}.timer")
        outcome = "failed" if fired else "missed"
        log({"kind": "fire", "agent": agent, "record": name,
             "outcome": outcome, "lane": f"cron:{name}",
             "detail": ("the timer fired and no run reported back"
                        if fired else "the timer never fired")})
        text = read_text(record_path(agent, name))
        if text:
            try:
                _change(agent, name, set_header(text, "done",
                                                f"{iso(now)} {outcome}"),
                        actor="loam", lane="reconciler",
                        reason=f"one-off past its moment: {outcome}", now=now,
                        reconcile_now=True, systemd=systemd)
            except Refused:
                pass

    if actions and WRITE_PAGE:
        loam_cron.write_schedule_page()
    return actions


def _apply(agent: str, wanted: dict[str, str | None],
           systemd: Systemd) -> list[dict]:
    """Make the unit directory say what `wanted` says. Timers are disarmed
    before their files go, because systemd reads [Install] to unlink."""
    out = []
    for filename, content in sorted(wanted.items()):
        path = systemd.unit_dir / filename
        live = read_text(path)
        if live == content:
            continue
        is_timer = filename.endswith(".timer")
        if content is None:
            if is_timer:
                systemd.disarm(filename)
            path.unlink(missing_ok=True)
            if is_timer:
                out.append({"kind": "timer", "agent": agent, "unit": filename,
                            "action": "removed"})
            continue
        write_atomic(path, content)
        if is_timer:
            out.append({"kind": "timer", "agent": agent, "unit": filename,
                        "action": "installed" if live is None else "recut"})
        else:
            # A changed service under an unchanged timer still needs the
            # reload; it rides on an entry that arms nothing new.
            out.append({"kind": "timer", "agent": agent,
                        "unit": filename.removesuffix(".service") + ".timer",
                        "action": "recut" if live is not None else "installed",
                        "service_only": True})
    # One entry per timer: the service and the timer of one cron are one act,
    # and it is the timer's own fate that says whether it is new.
    merged: dict[str, dict] = {}
    for action in out:
        unit = action["unit"]
        if unit not in merged or not action.get("service_only"):
            merged[unit] = {k: v for k, v in action.items()
                            if k != "service_only"}
    return list(merged.values())


def _sync_agent_json(agent: str, hhmm: str) -> None:
    """agent.json's `wake_schedule` follows the reflection record. It is read
    by the broker and the page; the record is the one place it is set."""
    path = loam_cron.AI_LABS / agent / "agent.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if config.get("wake_schedule") == hhmm:
        return
    config["wake_schedule"] = hhmm
    write_atomic(path, json.dumps(config, indent=2, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# The migration from `approved:` (2026-10-14), idempotent.
# ---------------------------------------------------------------------------

def reflection_record(hhmm: str) -> str:
    return (f"---\nlane: {REFLECTION}\nschedule: daily at {hhmm} {lc.ZONE_TOKEN}\n"
            f"enabled: true\n---\n{REFLECTION_BODY}")


def migrate(names: list[str] | None = None, systemd: Systemd | None = None,
            reason: str = "migration from approved: to enabled:") -> list[dict]:
    """Every record onto the path, each through change() as the engineer.

    An `approved:` record becomes `enabled: true` (the approval date stays in
    the log's `before`); a record already enabled is adopted as it stands;
    each agent gains `crons/reflection.md` at agent.json's current time, which
    renders the reflection timer byte-identical to the one standing.
    """
    out = []
    names = names if names is not None else loam_cron.roster()
    for agent in names:
        reflection = record_path(agent, REFLECTION)
        if not reflection.exists():
            config = json.loads((loam_cron.AI_LABS / agent / "agent.json")
                                .read_text(encoding="utf-8"))
            out.append(change(agent, REFLECTION,
                              reflection_record(config["wake_schedule"]),
                              actor="engineer", lane="engineer",
                              reason=reason, reconcile_now=False))
        folder = loam_cron.AI_LABS / agent / CRON_LANE_DIR
        for path in sorted(folder.glob("*.md")):
            if path.stem == REFLECTION or applied_path(agent, path.stem).exists():
                continue
            text = path.read_text(encoding="utf-8")
            head = header_of(text)
            if "approved" in head and "enabled" not in head:
                text = set_header(set_header(text, "enabled", "true"),
                                  "approved", None)
                # Keep the key order readable: enabled where approved was.
            out.append(change(agent, path.stem, text, actor="engineer",
                              lane="engineer", reason=reason,
                              reconcile_now=False))
    reconcile(names, systemd=systemd)
    return out
