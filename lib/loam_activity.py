"""Schedules and Activity, as the page reads and changes them.

Step 2 of loam-ui's ``design/schedules-and-activity.md`` (agreed with the owner
2026-10-14). The broker's ``/v1/schedules``, ``/v1/activity`` and
``/v1/jobs`` routes only dispatch to the functions here; everything they say
is derived, at the moment of asking, from sources that already exist:

  - the records, ``ai-labs/<agent>/crons/*.md`` (lib/loam_cron.py);
  - the schedule log, ``state/schedules/events.jsonl`` (lib/loam_schedule.py):
    changes, refusals, fires, and the owner's Run now;
  - the timers the user manager holds (next and last fire);
  - each wake's own log line, ``logs/wake-<agent>.jsonl``, for what a fire
    produced;
  - the engineer launcher's ``box_status()`` and ``cancel()``, called in
    process from ``bin/engineer-job`` itself, so this and
    ``mcp__engineer__status`` (``engineer-job --status``) read one function
    and cannot disagree; and each job's ``result.json`` for the finished.

**Every write is ``loam_schedule.change()``** with actor ``owner.id`` and lane
``app``. A refusal is ``loam_schedule.Refused`` and its message is the
sentence the page shows. A record has no key for tools: every lane has the
persona's whole tool list, the engineer included (2026-11-09).

**Seen** is the one piece of state of this module's own:
``state/schedules/seen.json``, the ids the owner has seen and the moment the
store began (everything before it counts as seen, so the first read is not
three hundred old jobs unread). An item done by the owner themselves is born
seen. A schedule row's marker and an Activity item are one id, so marking
either clears both.
"""

from __future__ import annotations

import contextlib
import fcntl
import importlib.machinery
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import loam_config as lc
import loam_cron
import loam_engineer as eng
import loam_inbox
import loam_schedule as ls
from loam_memory import CRON_LANE_DIR, strip_frontmatter

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "bin" / "engineer-job"
WAKE_LOGS = eng.LOG_DIR
# The install's folder, from the one place that writes it (lib/loam_inbox.py).
FAILURE_LOG = eng.VAULT / loam_inbox.FAILURE_LOG

ACTOR, LANE = lc.OWNER_ID, "app"
KINDS = ("change", "refused", "fire", "run", "job")
# The tests' seam: a Systemd pointed at a scratch unit directory.
SYSTEMD: "ls.Systemd | None" = None


class NotFound(ls.Refused):
    """No such record, event or job. The message says which."""


def _systemd() -> ls.Systemd:
    return SYSTEMD or ls.Systemd()


# ---------------------------------------------------------------------------
# Seen.
# ---------------------------------------------------------------------------

def seen_path() -> Path:
    return ls.STATE / "seen.json"


@contextlib.contextmanager
def _seen_lock():
    ls.STATE.mkdir(parents=True, exist_ok=True)
    with open(ls.STATE / "seen.lock", "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _read_seen() -> dict:
    try:
        store = json.loads(seen_path().read_text(encoding="utf-8"))
        return {"since": store["since"], "ids": list(store.get("ids") or [])}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def seen_store() -> dict:
    store = _read_seen()
    if store:
        return store
    with _seen_lock():
        store = _read_seen() or {"since": ls.iso(ls.now_utc()), "ids": []}
        ls.write_atomic(seen_path(), json.dumps(store) + "\n")
    return store


def mark_seen(ids: list[str]) -> int:
    """Add ids to the seen set. Returns how many were new."""
    seen_store()
    with _seen_lock():
        store = _read_seen()
        known = set(store["ids"])
        new = [i for i in ids if isinstance(i, str) and i and i not in known]
        if new:
            store["ids"] += new
            ls.write_atomic(seen_path(), json.dumps(store) + "\n")
    return len(new)


def _is_seen(item: dict, store: dict) -> bool:
    return (lc.is_owner(item.get("actor")) or (item.get("ts") or "") < store["since"]
            or item["id"] in set(store["ids"]))


# ---------------------------------------------------------------------------
# What a change changed, in the page's terms.
# ---------------------------------------------------------------------------

def diff(before: str | None, after: str | None) -> dict:
    """{header key: [before, after]} for every key that moved, and whether
    the instruction body did. A whole record is in the log; the page is
    owed what changed."""
    b, a = ls.header_of(before), ls.header_of(after)
    keys = {k: [b.get(k), a.get(k)] for k in sorted(set(b) | set(a))
            if b.get(k) != a.get(k)}
    body = lambda t: strip_frontmatter(t).strip() if t else None  # noqa: E731
    return {"header": keys, "body_changed": body(before) != body(after),
            "created": before is None, "deleted": after is None}


def by(actor: str | None) -> str | None:
    """Who an actor is, in the page's three words and the runtime's own:
    ``owner`` (the owner), ``agent`` (a persona, its own record), ``operator`` (the
    engineer, bin/schedule) or ``runtime`` (loam: a one-off marked done)."""
    if not actor:
        return None
    if lc.is_owner(actor):
        return "owner"
    return {"engineer": "operator", "loam": "runtime"}.get(actor, "agent")


def _change_item(event: dict) -> dict:
    item = {k: event.get(k) for k in ("id", "ts", "agent", "record", "op",
                                      "actor", "lane", "reason")}
    item["by"] = by(event.get("actor"))
    item["kind"] = "change"
    item["diff"] = diff(event.get("before"), event.get("after"))
    if event.get("undoes"):
        item["undoes"] = event["undoes"]
    return item


def _refused_item(event: dict) -> dict:
    item = {k: event.get(k) for k in ("id", "ts", "agent", "record", "actor",
                                      "lane", "reason", "why")}
    item["by"] = by(event.get("actor"))
    item["kind"] = "refused"
    item["direct"] = bool(event.get("direct"))
    item["diff"] = diff(event.get("before"), event.get("after"))
    return item


class _WakeLogs:
    """Each wake's log, indexed by (started, lane), read once per mtime."""
    def __init__(self) -> None:
        self.cache: dict[Path, tuple[float, dict]] = {}

    def line(self, agent: str, started: str | None, lane: str | None) -> dict | None:
        path = WAKE_LOGS / f"wake-{agent}.jsonl"
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None
        cached = self.cache.get(path)
        if cached is None or cached[0] != mtime:
            index = {}
            for raw in path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                index[(row.get("ts"), row.get("lane"))] = row
            cached = self.cache[path] = (mtime, index)
        return cached[1].get((started, lane))


WAKE_LOG_INDEX = _WakeLogs()


def produced(event: dict) -> dict | None:
    """Where a fire's result is: the wake's own log line (its vault commit,
    cost, the stage it stopped at) and, for a run that did not finish well,
    the failure log every wake failure is written to. A wake has no
    transcript: transcripts are conversations', by design."""
    agent = event.get("agent") or ""
    line = WAKE_LOG_INDEX.line(agent, event.get("started"), event.get("lane"))
    out: dict = {}
    if line is not None:
        out["wake_log"] = str(WAKE_LOGS / f"wake-{agent}.jsonl")
        out["line"] = {k: line[k] for k in (
            "ts", "finished", "ok", "stage", "vault_ref", "total_cost_usd",
            "error", "reported_by") if k in line}
    if event.get("outcome") in ("failed", "killed", "missed"):
        out["failure_log"] = str(FAILURE_LOG)
    return out or None


def _fire_item(event: dict) -> dict:
    item = {k: event.get(k) for k in ("id", "ts", "agent", "record", "outcome",
                                      "lane", "started", "finished")}
    item["kind"] = "fire"
    item["actor"] = "loam"
    item["stage"] = event.get("stage") or event.get("detail")
    item["produced"] = produced(event)
    return item


def _run_item(event: dict) -> dict:
    item = {k: event.get(k) for k in ("id", "ts", "agent", "record", "actor",
                                      "lane", "unit")}
    item["kind"] = "run"
    return item


ITEM_OF = {"change": _change_item, "refused": _refused_item,
           "fire": _fire_item, "run": _run_item}


# ---------------------------------------------------------------------------
# Engineer jobs: the launcher's own functions, loaded from its file.
# ---------------------------------------------------------------------------

_LAUNCHER: dict = {}


def launcher():
    """bin/engineer-job as a module, reloaded when the file changes, so a
    launcher landed under a running broker is the one these routes call."""
    mtime = LAUNCHER.stat().st_mtime
    if _LAUNCHER.get("mtime") != mtime:
        loader = importlib.machinery.SourceFileLoader("engineer_job", str(LAUNCHER))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        _LAUNCHER.update(mtime=mtime, module=module)
    return _LAUNCHER["module"]


def live_jobs() -> dict:
    """Run slots, landings and the queue: ``box_status()``, the one source
    of ``engineer-job --status`` and so of ``mcp__engineer__status``."""
    return launcher().box_status()


_JOBS: dict[Path, tuple[float, dict]] = {}
# The fields a job row carries beyond its outcome, from the launcher's
# `job_label`: one list, so a finished row and a running one say the same.
JOB_FIELDS = ("run_kind", "briefed_by", "engine", "model", "effort", "caller",
              "caller_session", "thread", "continues", "chain",
              "auto_continuation")


def _titled(rows: list[dict]) -> list[dict]:
    """`thread_title` read now, for the rows of one page: a thread renamed
    since a job finished shows its name today. Null for a deleted thread."""
    job = launcher()
    titles: dict[str, str | None] = {}
    for row in rows:
        tid = row.get("thread")
        if row.get("kind") == "job" and tid:
            if tid not in titles:
                titles[tid] = job.thread_title(tid)
            row["thread_title"] = titles[tid]
        elif row.get("kind") == "job":
            row["thread_title"] = None
    return rows


def finished_jobs() -> list[dict]:
    """Every job with a verdict, as an Activity item. A result is read once
    per change of its file."""
    job = launcher()
    out = []
    for project in eng.projects().values():
        try:
            results = list(project.jobs.glob("*/result.json"))
        except OSError:
            continue
        for path in results:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            cached = _JOBS.get(path)
            if cached is None or cached[0] != mtime:
                try:
                    result = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                job_id = path.parent.name
                label = job.job_label(project, job_id)
                cached = _JOBS[path] = (mtime, {
                    "id": f"job:{job_id}", "kind": "job", "job": job_id,
                    "ts": result.get("finished") or ls.iso(
                        datetime.fromtimestamp(mtime, timezone.utc)),
                    "started": result.get("started"),
                    "agent": label["briefed_by"], "actor": label["briefed_by"],
                    "project": label["project"], "scope": label.get("scope"),
                    "title": label["title"], "ok": result.get("ok"), "stage": result.get("stage"),
                    "outcome": result.get("outcome") or result.get("stage"),
                    "landing": result.get("landing_state"),
                    "landed_sha": result.get("landed_sha"),
                    "receipt": result.get("vault_receipt"),
                    "deployed": _deployed(result),
                    "job_dir": str(path.parent),
                    # Since 2026-11-12: what it ran on, who called and from
                    # where, and the chain it is part of (job_label).
                    **{k: label.get(k) for k in JOB_FIELDS}})
            out.append(dict(cached[1]))
    return out


def _deployed(result: dict) -> bool | None:
    """Whether a deploy at landing put the commit live: True, False (asked
    for and skipped or failed), or None when the job had none."""
    deploy = result.get("deploy")
    if not isinstance(deploy, dict):
        return None
    done = deploy.get("result") if isinstance(deploy.get("result"), dict) else {}
    return deploy.get("exit") == 0 and done.get("action") == "deployed"


# ---------------------------------------------------------------------------
# One row per engineer job in flight or just finished (2026-11-13).
#
# The one reader of job state for anything that lists a thread's or an
# agent's jobs: a thread's record (`jobs` on GET /v1/threads/{id}) reads it,
# and so should any per-agent line. It is built from the two readers above,
# `live_jobs()` (box_status: running, landing, queued) and `finished_jobs()`
# (result.json), never from a persona's session, so a job outlives the
# session that briefed it.
# ---------------------------------------------------------------------------

# How long a finished job stays in the list, so a finish is seen.
RECENT_SECONDS = 600
# Rows are read at most this often; a page polls every few seconds and the
# broker's thread routes run on its loop.
ROWS_TTL = 3.0
_ROWS: dict = {}

# A finished job's outcome, as one word for the page (`outcome`), with the
# launcher's own words beside it (`outcome_detail`).
OUTCOMES = ("landed", "deployed", "deploy failed", "pull request",
            "not landed", "finished", "failed", "cut off", "cancelled",
            "interrupted")


def outcome_word(item: dict) -> str:
    """`finished_jobs()` item -> one of OUTCOMES."""
    stage, said = item.get("stage"), str(item.get("outcome") or "")
    if stage == "cancelled":
        return "cancelled"
    if stage == "interrupted":
        return "interrupted"
    if said.startswith("cut off"):
        return "cut off"
    if not item.get("ok"):
        return "failed"
    if item.get("deployed") is True:
        return "deployed"
    if item.get("deployed") is False:
        return "deploy failed"
    landing = item.get("landing")
    if landing in ("landed", "landed_earlier"):
        return "landed"
    if landing == "pull_request":
        return "pull request"
    if landing == "not_landed":
        return "not landed"
    return "finished"


def _parse(stamp: str | None) -> datetime | None:
    try:
        return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _session_since(job_id: str) -> str | None:
    """When a job's session started: its inner's lease, `owner.json`."""
    project = eng.locate_job(job_id)
    if project is None:
        return None
    try:
        return json.loads((eng.job_dir(project, job_id) / "owner.json")
                          .read_text(encoding="utf-8")).get("since")
    except (OSError, ValueError, AttributeError, eng.BriefError):
        return None


def _vault_path(path: str | None) -> str | None:
    """A receipt's path inside the vault, as Files opens it; a path outside
    the vault (a probe's, kept in its job directory) as it is."""
    if not path:
        return None
    try:
        return str(Path(path).relative_to(eng.VAULT))
    except ValueError:
        return path


def _job_row(label: dict, state: str, now: datetime, **extra) -> dict:
    row = {"job": label.get("job"), "project": label.get("project"),
           "scope": label.get("scope"), "title": label.get("title"), "briefed_by": label.get("briefed_by"),
           "thread": label.get("thread"), "state": state, "phase": None,
           "since": None, "started": None, "finished": None,
           "elapsed_seconds": None, "outcome": None, "outcome_detail": None,
           "ok": None, "receipt": None, "position": None, "held_by": None,
           "last_activity": None, "quiet_since": None, "child_stops": 0}
    row.update(extra)
    began = _parse(row["started"])
    if began is not None:
        end = _parse(row["finished"]) or now
        row["elapsed_seconds"] = max(0, int((end - began).total_seconds()))
    return row


def job_rows(recent: int = RECENT_SECONDS, now: datetime | None = None,
             live: dict | None = None, finished: list[dict] | None = None,
             fresh: bool = False) -> list[dict]:
    """Every engineer job queued, running, landing, or finished in the last
    ``recent`` seconds, one shape for all (the contract is in
    broker/README.md, "A thread's engineer jobs"). Active first, oldest first; then the
    finished, newest first. A job with a verdict is finished even if its
    admission has not been let go yet. Cached for ROWS_TTL seconds unless
    the sources are passed in or ``fresh``."""
    use_cache = live is None and finished is None and now is None and not fresh
    clock = datetime.now(timezone.utc)
    if use_cache and _ROWS.get("at") and \
            (clock - _ROWS["at"]).total_seconds() < ROWS_TTL \
            and _ROWS.get("recent") == recent:
        return [dict(r) for r in _ROWS["rows"]]
    now = now or clock
    live = live_jobs() if live is None else live
    finished = finished_jobs() if finished is None else finished
    done: dict[str, dict] = {}
    for item in finished:
        ended = _parse(item.get("ts"))
        if ended is None or (now - ended).total_seconds() > recent:
            continue
        job_id = item.get("job")
        done[job_id] = _job_row(
            item | {"job": job_id}, "finished", now,
            since=item.get("ts"), started=item.get("started"),
            finished=item.get("ts"), outcome=outcome_word(item),
            outcome_detail=item.get("outcome") or item.get("stage"),
            ok=item.get("ok"), receipt=_vault_path(item.get("receipt")))
    active: list[dict] = []
    seen: set[str] = set(done)
    for slot in live.get("slots") or []:
        if slot.get("kind") != "engineer job" or slot.get("job") in seen:
            continue
        seen.add(slot["job"])
        active.append(_job_row(slot, "running", now, since=slot.get("since"),
                           started=slot.get("since"),
                           last_activity=slot.get("last_activity"),
                           quiet_since=slot.get("quiet_since"),
                           child_stops=slot.get("child_stops") or 0))
    for land in live.get("landing") or []:
        if land.get("job") in seen:
            continue
        seen.add(land["job"])
        active.append(_job_row(land, "landing", now, phase=land.get("phase"),
                           since=land.get("since"),
                           started=_session_since(land["job"]) or land.get("since")))
    for queued in live.get("queue") or []:
        if queued.get("job") in seen:
            continue
        seen.add(queued["job"])
        active.append(_job_row(queued, "queued", now, since=queued.get("joined"),
                           position=queued.get("position"),
                           held_by=queued.get("held_by")))
    order = {"running": 0, "landing": 0, "queued": 1}
    active.sort(key=lambda r: (order[r["state"]], r["since"] or ""))
    rows = active + sorted(done.values(), key=lambda r: r["finished"] or "",
                           reverse=True)
    if use_cache:
        _ROWS.update(at=clock, recent=recent, rows=[dict(r) for r in rows])
    return rows


def thread_jobs(thread_id: str, rows: list[dict] | None = None) -> list[dict]:
    """The `job_rows()` briefed from one thread. A job with no thread (a
    wake's, a cron's, the shell's) is in no thread's list."""
    rows = job_rows() if rows is None else rows
    return [r for r in rows if thread_id and r.get("thread") == thread_id]


def cancel_job(job_id: str) -> str:
    """The launcher's durable cancel, by the owner. A queued job never starts;
    a running one is stopped (since 2026-11-10) and records itself as
    cancelled, and nothing lands, continues or recovers it. Blocking for up
    to the launcher's CANCEL_WAIT_SECONDS; the broker runs it in a thread."""
    if not eng.JOB_ID_RE.match(job_id or ""):
        raise NotFound(f"{job_id!r} is not a job id")
    code, sentence = launcher().cancel(job_id, ACTOR)
    if code == 64:
        raise NotFound(sentence)
    if code:
        raise ls.Refused(sentence)
    return sentence


# ---------------------------------------------------------------------------
# Activity.
# ---------------------------------------------------------------------------

def items(store: dict | None = None) -> list[dict]:
    """Every Activity item, newest first, each with ``seen``."""
    store = store or seen_store()
    # A line for an agent not on the roster is a test's or a retired
    # agent's, and is not something that happened to the owner's personas.
    roster = set(loam_cron.roster())
    out = [ITEM_OF[e["kind"]](e) for e in ls.events()
           if e.get("kind") in ITEM_OF and e.get("agent") in roster]
    out += finished_jobs()
    for item in out:
        item["seen"] = _is_seen(item, store)
    # Oldest first by the second, the log's own order within one (a sort is
    # stable), then turned round: newest first, and of two in one second the
    # later line first.
    out.sort(key=lambda i: i.get("ts") or "")
    return out[::-1]


def activity(agent: str | None = None, kind: str | None = None,
             limit: int = 50, offset: int = 0) -> dict:
    agents = {a for a in (agent or "").split(",") if a}
    kinds = {k for k in (kind or "").split(",") if k}
    unknown = kinds - set(KINDS)
    if unknown:
        raise ls.Refused(f"{', '.join(sorted(unknown))} is not a kind of "
                         f"activity; the kinds are {', '.join(KINDS)}")
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    everything = items()
    chosen = [i for i in everything
              if (not agents or i.get("agent") in agents)
              and (not kinds or i["kind"] in kinds)]
    page = _titled(chosen[offset:offset + limit])
    try:
        live, live_error = live_jobs(), None
    except Exception as exc:  # noqa: BLE001 -- the feed stands without it
        live, live_error = None, f"the launcher's status could not be read: {exc}"
    return {"items": page, "total": len(chosen),
            "next_offset": offset + limit if offset + limit < len(chosen) else None,
            "unread": sum(not i["seen"] for i in everything),
            "jobs": live, "jobs_error": live_error}


def unread() -> int:
    return sum(not i["seen"] for i in items())


# ---------------------------------------------------------------------------
# Schedules.
# ---------------------------------------------------------------------------

def _words(cron: loam_cron.Cron) -> str:
    if cron.kind == "one-off":
        return f"once, {cron.schedule_text}"
    return cron.schedule_text


def _timer(agent: str, name: str, kind: str) -> str:
    if kind == "reflection":
        return f"loam-wake-{agent}.timer"
    return f"{loam_cron.unit_base(agent, name)}.timer"


def schedules(agent: str | None = None) -> list[dict]:
    """Every persona's records (or one persona's), as rows. A one-off that
    has fired is gone from the list and kept in Activity."""
    roster = loam_cron.roster()
    if agent is not None and agent not in roster:
        raise NotFound(f"{agent!r} is not an agent on this box")
    # An archived agent's records stay on its profile and leave the list
    # (lib/loam_agents.py, 2026-11-14): nothing of them runs.
    import loam_agents  # noqa: PLC0415
    names = [agent] if agent else loam_agents.live(roster, loam_cron.AI_LABS)
    store = seen_store()
    log = ls.events()
    rows = []
    for who in names:
        folder = loam_cron.AI_LABS / who / CRON_LANE_DIR
        for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
            row = _row(who, path.stem, ls.read_text(path), log, store)
            if row is not None:
                rows.append(row)
    return _with_timers(rows)


def _with_timers(rows: list[dict]) -> list[dict]:
    """Each row's ``next_fire`` (null when no version of it runs) and
    ``running_since``: when a run of it began, if one is in flight now --
    whatever version, paused or not, since the run is the box's fact. A run
    in flight keeps its ``next_fire``; ``last_fire`` is written when a run
    ends, so ``running_since`` is what tells an in-flight run from a record
    that has not fired since the log began."""
    timers = _systemd().timers([r["timer"] for r in rows])
    for row in rows:
        timer = timers[row["timer"]]
        row["next_fire"] = timer["next"] if row["running"] != "none" else None
        row["running_since"] = timer["running_since"]
    return rows


def schedule(agent: str, name: str) -> dict:
    text = ls.read_text(ls.record_path(agent, name))
    row = _row(agent, name, text, ls.events(), seen_store(), keep_done=True)
    if row is None:
        raise NotFound(f"there is no schedule {agent}/{name}")
    return _with_timers([row])[0]


def after_file_save(agent: str, name: str) -> dict | None:
    """Files saved a schedule record (its claim already logged): reconcile
    the agent now rather than within the minute, and answer the row as it
    then stands, so the editor can say at once whether the save runs. A
    reconcile that fails here is the minute timer's to finish, and the row
    then says ``pending``; None for a folder that is no agent's."""
    if agent not in loam_cron.roster():
        return None
    try:
        ls.reconcile([agent], systemd=_systemd())
    except Exception:  # noqa: BLE001 -- the save stands; the timer retries
        pass
    try:
        return schedule(agent, name)
    except ls.Refused:
        return None


def _state(cron, problem: dict | None, current: str, accepted: str | None,
           previous, refusal: dict | None) -> tuple[str, str, str]:
    """(state, running, words) for a record.

    ``state`` is the file's: ``live``, ``off``, ``broken`` (it does not
    parse), ``refused`` (it parses and the path would not take it: an agent's
    edit with no reason, a one-off past its caps), or ``pending`` (written
    outside the path and not read yet). ``running`` is the box's: ``this``
    file, the ``previous`` version accepted before it, or ``none``. A broken
    or refused file never stops a schedule by itself: the reconciler renders
    only what it accepted, so the version before keeps its timer."""
    if current == accepted:
        running = "this" if cron is not None and cron.live else "none"
    else:
        running = "previous" if previous is not None and previous.live else "none"
    still = (f" The previous version still runs ({previous.schedule_text})."
             if running == "previous" else " Nothing runs.")
    if problem is not None:
        why = problem["why"] if problem["line"] else _sentence(problem["why"])
        where = f"Line {problem['line']}: " if problem["line"] else ""
        return ("broken", running,
                f"Not running this file. {where}{why}{still}")
    if current == accepted:
        if cron.live:
            return "live", running, "Live."
        if cron.done:
            return "off", running, f"Off: a one-off that has run ({cron.done})."
        return "off", running, "Off: the file says it is not enabled."
    if refusal is not None:
        return ("refused", running,
                f"Not running this file: the change was refused. "
                f"{_sentence(refusal['why'])}{still}")
    return ("pending", running,
            "Changed on disk; the box takes it up within a minute.")


def _sentence(why: str) -> str:
    """A reason as a sentence's start; a file's own name keeps its case."""
    return why if why.startswith("crons/") else why[:1].upper() + why[1:]


def _row(agent: str, name: str, text: str | None, log: list[dict],
         store: dict, keep_done: bool = False) -> dict | None:
    if text is None:
        return None
    try:
        cron = loam_cron.parse_record(agent, name, text, check_systemd=False)
    except loam_cron.CronError as exc:
        cron, error, problem = None, str(exc), ls.problem(name, text, exc)
    else:
        error = problem = None
    if cron is not None and cron.done and not keep_done:
        return None
    accepted = ls.read_text(ls.applied_path(agent, name))
    try:
        previous = (loam_cron.parse_record(agent, name, accepted,
                                           check_systemd=False)
                    if accepted is not None else None)
    except loam_cron.CronError:
        previous = None
    kind = cron.kind if cron else (previous.kind if previous else "unknown")
    mine = [e for e in log if (e.get("agent"), e.get("record")) == (agent, name)]
    changes = [e for e in mine if e.get("kind") == "change"]
    fires = [e for e in mine if e.get("kind") == "fire"]
    last_change = _change_item(changes[-1]) if changes else None
    unseen = [e["id"] for e in mine if e.get("kind") in ("change", "refused")
              and not _is_seen({"id": e["id"], "ts": e.get("ts"),
                                "actor": e.get("actor")}, store)]
    if last_change:
        last_change["seen"] = last_change["id"] not in unseen
    latest = mine[-1] if mine else None
    refused = (_refused_item(latest) if latest and latest.get("kind") == "refused"
               else None)
    # The file's own last edit, whichever path made it: a change, or a
    # direct write the path refused (a hand edit included). A route's
    # request that was refused changed no file and is not one.
    edit = next((e for e in reversed(mine) if e.get("kind") == "change"
                 or (e.get("kind") == "refused" and e.get("direct"))), None)
    refusal = (edit if edit and edit["kind"] == "refused"
               and ls.sha(text) == ls.sha(edit.get("after")) else None)
    try:
        enabled = loam_cron.parse_enabled(ls.header_of(text))
    except loam_cron.CronError:
        enabled = None
    state, running, words = _state(cron, problem, text, accepted, previous,
                                   refusal)
    if problem is None and refusal is not None:
        problem = {"why": refusal["why"], "line": None, "text": None}
    return {
        "agent": agent, "name": name, "kind": kind,
        "state": state, "running": running, "state_words": words,
        "problem": problem,
        "schedule": cron.schedule_text if cron else None,
        "words": _words(cron) if cron else None,
        "note": cron.note if cron else None,
        "enabled": enabled,
        "done": cron.done if cron else None,
        "body": strip_frontmatter(text).strip(),
        # The reflection's instruction is its body (2026-11-19); an agent
        # without a map also has its own `_reflection.md` loaded with it.
        "instruction": ("_reflection.md" if kind == "reflection"
                        and not (_ai_labs_ws(agent) / "map.md").is_file() else None),
        "timer": _timer(agent, name, kind),
        "file": {"root": "spaces", "path": f"{agent}/{CRON_LANE_DIR}/{name}.md"},
        "last_fire": ({k: fires[-1].get(k) for k in ("id", "ts", "outcome")}
                      | {"stage": fires[-1].get("stage") or fires[-1].get("detail")}
                      if fires else None),
        "last_change": last_change,
        "last_edit": ({"id": edit["id"], "ts": edit.get("ts"),
                       "actor": edit.get("actor"), "by": by(edit.get("actor")),
                       "lane": edit.get("lane"),
                       "taken": edit.get("kind") == "change"}
                      if edit else None),
        "changed_since_seen": bool(unseen),
        "unseen": unseen,
        "refused": refused,
        "error": error,
    }


# ---------------------------------------------------------------------------
# The owner's writes. Each is one change() call.
# ---------------------------------------------------------------------------

FIELDS = ("schedule", "at", "note", "body", "enabled")


def compose(fields: dict, previous: str | None) -> str:
    """A record from the page's fields over the record as it stands. Keys
    the page does not send are left byte for byte; `schedule` and `at` are
    exclusive, so setting one clears the other (and a one-off set again is
    no longer done)."""
    unknown = sorted(set(fields) - set(FIELDS)
                     - {"name", "reason", "by", "lane", "thread", "yes"})
    if unknown:
        raise ls.Refused(f"a schedule has no field {', '.join(unknown)}; the "
                         f"fields are {', '.join(FIELDS)}")
    text = previous if previous is not None else "---\n---\n"
    for key in ("schedule", "at", "note"):
        if key in fields and fields[key] is not None and not isinstance(fields[key], str):
            raise ls.Refused(f"`{key}` is written as words, like "
                             + (f"\"daily at 05:00 {lc.ZONE_TOKEN}\"" if key == "schedule"
                                else f"\"2026-10-20 14:30 {lc.ZONE_TOKEN}\"" if key == "at"
                                else "\"did the PR merge?\""))
    if fields.get("schedule"):
        for key in ("at", "note", "done"):
            text = ls.set_header(text, key, None)
        text = ls.set_header(text, "schedule", fields["schedule"].strip())
    if fields.get("at"):
        for key in ("schedule", "done"):
            text = ls.set_header(text, key, None)
        text = ls.set_header(text, "at", fields["at"].strip())
    if "note" in fields:
        text = ls.set_header(text, "note", (fields["note"] or "").strip() or None)
    if "enabled" in fields:
        if not isinstance(fields["enabled"], bool):
            raise ls.Refused("`enabled` is true or false")
        text = ls.set_header(text, "enabled", "true" if fields["enabled"] else "false")
    if "body" in fields:
        if not isinstance(fields["body"], str):
            raise ls.Refused("the instruction is text")
        match = ls.HEADER_RE.match(text)
        head = text[:match.end()] if match else "---\n---\n"
        body = fields["body"].strip()
        text = head + (body + "\n" if body else "")
    return text


def _reason(fields: dict) -> str | None:
    reason = fields.get("reason")
    return reason.strip() if isinstance(reason, str) and reason.strip() else None


def _actor(fields: dict | None) -> tuple[str, str]:
    """Who is writing, and from which lane (2026-11-10). The page sends
    neither and is the owner on `app`; a persona through `bin/settings` sends
    `by` and the lane it is in, and loam_schedule.validate() holds it to its
    own records and to a reason, exactly as it holds the persona's own tool."""
    by = (fields or {}).get("by")
    if by is None or (isinstance(by, str) and (not by.strip() or lc.is_owner(by))):
        return ACTOR, LANE
    if not isinstance(by, str):
        raise ls.Refused("`by` is a persona's name")
    lane = (fields or {}).get("lane") or "conversation"
    return by.strip().lower(), str(lane)


def _write(agent: str, name: str, text: str | None, reason: str | None,
           fields: dict | None = None, *, as_owner: bool = False,
           **extra) -> dict:
    actor, lane = (ACTOR, "conversation") if as_owner else _actor(fields)
    event = ls.change(agent, name, text, actor=actor, lane=lane, reason=reason,
                      systemd=_systemd(), extra=extra or None)
    return {"event": _change_item(event),
            "schedule": schedule(agent, name) if text is not None else None}


def create(agent: str, fields: dict) -> dict:
    name = fields.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ls.Refused("a new schedule needs a name: lower-case letters, "
                         "digits and dashes")
    name = name.strip()
    if ls.read_text(ls.record_path(agent, name)) is not None:
        raise ls.Refused(f"{agent} already has a schedule named {name}")
    fields = {"enabled": True, **fields}
    return _write(agent, name, compose(fields, None), _reason(fields), fields)


def edit(agent: str, name: str, fields: dict) -> dict:
    previous = ls.read_text(ls.record_path(agent, name))
    if previous is None:
        raise NotFound(f"there is no schedule {agent}/{name}")
    text = compose(fields, previous)
    if text == previous:
        raise ls.Refused("that changes nothing in the schedule")
    word = owners_word(agent, name, fields, previous, text)
    if word:
        return _write(agent, name, text, _reason(fields), fields,
                      as_owner=True, **word)
    try:
        return _write(agent, name, text, _reason(fields), fields)
    except ls.Refused as exc:
        if str(exc) == ls.REFLECTION_OWNERS:
            raise ls.Refused(
                f"{exc} If {lc.OWNER_NAME} asked you to turn it off, pass "
                f"`thread` (the thread he asked in) and `yes` (the id of his "
                f"message asking, like m12) with `enabled: false`.") from None
        raise


# How long the owner's message stays good for turning a reflection off.
OWNERS_WORD_WINDOW = timedelta(hours=1)


def owners_word(agent: str, name: str, fields: dict, previous: str,
                text: str) -> dict | None:
    """The settings route's one way for a persona to turn its daily
    reflection off (2026-11-19): on the owner's word, cited. The persona
    passes ``thread`` and ``yes``, the id of the owner's message asking; it
    must be the owner's own message (the broker writes those only from his
    signed-in app), in a thread the persona is in, under an hour old, from a
    conversation, and not already taken for another change. The change is
    then the owner's, logged with who relayed it and the message.

    What it cannot tell: whether that message asked for this. A persona can
    cite an unrelated message of the owner's from the last hour; the check
    is that he spoke to it, recently, in a conversation, and the event names
    the message so it can be read back. Without ``yes`` the call is the
    persona's own, and the pause is refused (loam_schedule.REFLECTION_OWNERS).
    None when the fields carry no owner's word."""
    if not (fields.get("yes") or fields.get("thread")):
        return None
    by, lane = _actor(fields)
    if lc.is_owner(by):
        return None
    if name != loam_cron.REFLECTION or fields.get("enabled") is not False \
            or set(fields) - {"enabled", "reason", "by", "lane", "thread", "yes"}:
        raise ls.Refused("`thread` and `yes` are for one change only: turning "
                         "your daily reflection off on the owner's word "
                         "(`enabled: false` and nothing else)")
    if lane != "conversation":
        raise ls.Refused(f"the owner's word is taken only in a conversation; "
                         f"this is an unattended run ({lane})")
    import loam_agents  # noqa: PLC0415
    used = loam_agents._used_yeses() | {
        (e["yes"]["thread"], e["yes"]["message"]) for e in ls.events()
        if isinstance(e.get("yes"), dict) and e["yes"].get("thread")}
    try:
        yes = loam_agents.check_yes(by, fields.get("thread"), fields.get("yes"),
                                    window=OWNERS_WORD_WINDOW, used=used)
    except loam_agents.AgentsError as exc:
        raise ls.Refused(exc.message) from None
    return {"relayed_by": by, "yes": yes}


def delete(agent: str, name: str, fields: dict | None = None) -> dict:
    if ls.read_text(ls.record_path(agent, name)) is None:
        raise NotFound(f"there is no schedule {agent}/{name}")
    return _write(agent, name, None, _reason(fields or {}), fields)


def undo(event_id: str, fields: dict | None = None) -> dict:
    """Put a record back as it was before one logged change, as a new change
    by the owner. Only the record's latest change can be undone: undoing an
    older one would silently throw away every change made since."""
    event = next((e for e in reversed(ls.events())
                  if e.get("id") == event_id and e.get("kind") == "change"), None)
    if event is None:
        raise NotFound(f"there is no schedule change {event_id!r} in the log")
    agent, name = event["agent"], event["record"]
    current = ls.read_text(ls.record_path(agent, name))
    if ls.sha(current) != event.get("after_sha"):
        later = next((e for e in reversed(ls.events())
                      if (e.get("agent"), e.get("record")) == (agent, name)
                      and e.get("kind") == "change"), None)
        who = (f" ({later.get('op')} by {later.get('actor')} at {later.get('ts')})"
               if later else "")
        raise ls.Refused(f"{agent}/{name} has changed since that change{who}; "
                         f"undo the later change first")
    actor, lane = _actor(fields)
    if not lc.is_owner(actor):
        # Validated before it is marked seen, so a refused undo leaves the
        # item unread on the page.
        ls.validate(agent, name, event.get("before"), actor=actor, lane=lane,
                    reason="undo", previous=current)
    mark_seen([event_id])
    return _write(agent, name, event.get("before"),
                  f"undo of {event.get('op')} by {event.get('actor')} at "
                  f"{event.get('ts')}", fields, undoes=event_id)


def run_now(agent: str, name: str, fields: dict | None = None) -> dict:
    """One fire now, as the timer would: bin/wake's lock and its enabled
    check apply. Logged as the owner's `run`; the fire is logged by the run."""
    text = ls.read_text(ls.record_path(agent, name))
    if text is None:
        raise NotFound(f"there is no schedule {agent}/{name}")
    actor, lane = _actor(fields)
    if not lc.is_owner(actor):
        # Boundary 3 of the charter, bent only where the owner is present: a
        # persona runs its own schedule now from a conversation, never from a
        # wake or a cron, and never another persona's.
        if actor != agent:
            raise ls.Refused(f"{actor} may run its own schedules only, not "
                             f"{agent}'s")
        if lane != "conversation":
            raise ls.Refused("a schedule is run now only from a conversation "
                             f"with {lc.OWNER_NAME}, never from a wake or a cron")
    cron = ls.parse(agent, name, text)
    if cron.done:
        raise ls.Refused(f"{agent}/{name} is a one-off that has already run "
                         f"({cron.done})")
    if not cron.enabled:
        raise ls.Refused(f"{agent}/{name} is paused, and a paused schedule "
                         f"does not run; resume it first")
    systemd = _systemd()
    service = f"{cron.unit}.service"
    if not (systemd.unit_dir / service).is_file():
        raise ls.Refused(f"{agent}/{name} has no service installed yet; the "
                         f"reconciler installs it within a minute of a change")
    done = systemd.start(service)
    if done.returncode != 0:
        raise ls.Refused(f"systemd would not start {service}: "
                         f"{(done.stderr or done.stdout or '').strip()}")
    event = ls.log({"kind": "run", "agent": agent, "record": name,
                    "actor": actor, "lane": lane, "unit": service})
    return {"event": _run_item(event)}


def seen_schedule(agent: str, name: str) -> dict:
    row = schedule(agent, name)
    return {"marked": mark_seen(row["unseen"]), "unread": unread()}


def _ai_labs_ws(agent: str):
    """The agent's workspace, for which shape its reflection is in."""
    import loam_config as lc  # noqa: PLC0415
    return lc.WORKSPACES / agent
