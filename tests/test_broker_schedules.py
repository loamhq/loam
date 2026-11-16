#!/usr/bin/env python3
"""Schedules and Activity through the broker's own routes.

A broker's ``Server.route`` in process, over a made-up agent in a temp
``ai-labs``, a temp schedule log and a scratch unit directory, with a
stand-in for ``systemctl`` that records what it was asked and answers
``show`` for the timers present (the timers' real behaviour under the user
manager is tests/test_schedule.py's). What is asserted is what the page
would see and what the box would hold after each request:

  - the owner's create -> edit -> pause -> undo, each a change by the owner's id on
    lane `app`, the record and the timer following each, and the undo a new
    change naming the one it undid; an older change refused for undo;
  - a refused change comes back as the path's own sentence and changes
    nothing; Run now starts the service, and is refused on a paused record;
  - seen: an agent's change is unread in Activity and marked on its row;
    marking it seen on the row clears Activity, and marking seen in
    Activity clears the row;
  - a run in flight (the fake answering as systemd 252 does mid-run): the
    row carries `running_since` and still a `next_fire`; with none,
    `running_since` is null;
  - a direct edit made while one of the agent's chats is live is lane
    `conversation`; with no chat and no run, `unknown`;
  - a killed run: bin/wake-failed, given the marker a SIGTERM leaves,
    writes a fire line with outcome `killed`, and Activity shows it;
  - a job cancel: a queued job of a scratch project is cancelled through
    the route by the launcher's own `cancel`, gets its verdict, and shows
    in Activity as a finished job; a second cancel is refused.

Run: python3 tests/test_broker_schedules.py
"""

from __future__ import annotations

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
# This tree's modules, every one, before anything puts the box's lib/ on
# the path (bin/engineer-job does, when the activity module loads it).
import loam_memory  # noqa: E402,F401
import loam_engineer as eng  # noqa: E402
import loam_lock  # noqa: E402,F401
import loam_schedule as ls  # noqa: E402
import loam_cron  # noqa: E402
import loam_config as lc  # noqa: E402
OWNER = lc.OWNER_ID
import loam_activity as activity  # noqa: E402


def load(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


broker_mod = load("session_broker", AGENTS / "bin" / "session-broker")

results: list[bool] = []


def check(label: str, ok, detail="") -> None:
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:62}{'  | ' + str(detail)[:220] if detail != '' else ''}")


TMP = Path(tempfile.mkdtemp(prefix="loam-activity-test-"))
AGENT = f"zzact{os.getpid()}"
LABS = TMP / "ai-labs"
UNITS = TMP / "units"
UNITS.mkdir()
(LABS / AGENT / "crons").mkdir(parents=True)
(LABS / AGENT / "agent.json").write_text(json.dumps(
    {"name": AGENT, "wake_schedule": "02:00",
     "wake_timezone": "America/Los_Angeles"}) + "\n")

loam_cron.AI_LABS = LABS
loam_cron.UNIT_DIR = UNITS
ls.STATE = TMP / "state"
ls.WAKE_STATE = TMP / "wake"
ls.BROKER_SESSIONS = TMP / "sessions"
ls.WRITE_PAGE = False
activity.WAKE_LOGS = TMP / "logs"


class FakeSystemd(ls.Systemd):
    """systemctl, recorded. `show` answers for the timers in the unit dir."""
    def __init__(self) -> None:
        super().__init__(unit_dir=UNITS, runtime=True)
        self.calls: list[tuple[str, ...]] = []
        # timer -> when its service started, for a run in flight. Answered
        # as systemd 252 answers (measured 2026-10-23): the timer `running`,
        # no NextElapse, TimersCalendar's next_elapse the trigger just past.
        self.running: dict[str, str] = {}

    def _timer(self, unit: str) -> str:
        if not (UNITS / unit).is_file():
            return f"NextElapseUSecRealtime=\nLastTriggerUSec=\nId={unit}"
        spec = next(line.partition("=")[2] for line in
                    (UNITS / unit).read_text().splitlines()
                    if line.startswith("OnCalendar="))
        since = self.running.get(unit)
        return (f"NextElapseUSecRealtime="
                f"{'' if since else 'Wed 2026-10-21 05:00:00 PDT'}\n"
                f"LastTriggerUSec={since or ''}\nId={unit}\n"
                f"SubState={'running' if since else 'waiting'}\n"
                f"TimersCalendar={{ OnCalendar={spec} ; next_elapse="
                f"{since or 'Wed 2026-10-21 05:00:00 PDT'} }}\n"
                f"Unit={unit.removesuffix('.timer')}.service")

    def _service(self, unit: str) -> str:
        since = self.running.get(f"{unit.removesuffix('.service')}.timer")
        return (f"Id={unit}\nActiveState={'activating' if since else 'inactive'}\n"
                f"InactiveExitTimestamp={since or ''}")

    def run(self, *args: str) -> subprocess.CompletedProcess:
        self.calls.append(args)
        out = ""
        if args[0] == "show" and args[2].startswith("Id,NextElapseUSecRealtime,"):
            out = "\n\n".join(self._timer(unit) for unit in args[3:])
        elif args[0] == "show" and args[2] == "Id,ActiveState,InactiveExitTimestamp":
            out = "\n\n".join(self._service(unit) for unit in args[3:])
        return subprocess.CompletedProcess(args, 0, out, "")


SYSTEMD = FakeSystemd()
activity.SYSTEMD = SYSTEMD


class FakeWriter:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.chunks.append(data)

    async def drain(self) -> None:
        return None

    def body(self):
        head, _, payload = b"".join(self.chunks).decode().partition("\r\n\r\n")
        return int(head.split()[1]), json.loads(payload)


SERVER = broker_mod.Server(None)


def call(method: str, path: str, payload: dict | None = None):
    target, _, query = path.partition("?")
    params = dict(p.split("=", 1) for p in query.split("&") if p)
    writer = FakeWriter()

    async def go():
        try:
            await SERVER.route(method, target, params, payload or {}, writer)
        except broker_mod.BrokerError as exc:
            return exc.status, {"error": exc.message}
        return writer.body()
    return asyncio.run(go())


def record() -> str:
    return (LABS / AGENT / "crons" / "digest.md").read_text(encoding="utf-8")


TIMER = f"loam-cron-{AGENT}-digest.timer"
try:
    # -- seen store begins; nothing yet ------------------------------------
    status, body = call("GET", f"/v1/schedules/{AGENT}")
    check("an agent with no records lists none", status == 200 and
          body["schedules"] == [] and body["unread"] == 0, body)

    # -- the owner's create -> edit -> pause -> undo ---------------------------
    status, body = call("POST", f"/v1/schedules/{AGENT}", {
        "name": "digest", "schedule": "daily at 06:00 PT",
        "body": "Write the morning digest."})
    ev = body.get("event", {})
    check("create: 200, a change by the owner on lane app",
          status == 200 and (ev.get("op"), ev.get("actor"), ev.get("lane"))
          == ("create", OWNER, "app"), ev)
    check("create: the record says enabled, and its timer stands",
          "enabled: true" in record() and (UNITS / TIMER).is_file())
    row = body["schedule"]
    check("create: the row has words, body, next fire, a seen last change",
          row["words"] == "daily at 06:00 PT" and row["body"] ==
          "Write the morning digest." and row["next_fire"] == "2026-10-21T12:00:00Z"
          and row["last_change"]["seen"] is True, row)

    status, body = call("PATCH", f"/v1/schedules/{AGENT}/digest",
                        {"schedule": "weekdays at 07:30 PT"})
    move_id = body["event"]["id"]
    check("edit schedule: op move, the header follows",
          status == 200 and body["event"]["op"] == "move"
          and "schedule: weekdays at 07:30 PT" in record(), body["event"])
    status, body = call("PATCH", f"/v1/schedules/{AGENT}/digest",
                        {"body": "Write the digest; lead with the inbox."})
    check("edit body: op edit, the body changed and the header did not",
          status == 200 and body["event"]["op"] == "edit"
          and body["event"]["diff"] == {"header": {}, "body_changed": True,
                                        "created": False, "deleted": False}
          and record().endswith("lead with the inbox.\n"), body["event"])

    status, body = call("PATCH", f"/v1/schedules/{AGENT}/digest", {"enabled": False})
    pause_id = body["event"]["id"]
    check("pause: op pause, the timer is gone, the row says not enabled",
          status == 200 and body["event"]["op"] == "pause"
          and not (UNITS / TIMER).exists() and body["schedule"]["enabled"] is False
          and body["schedule"]["next_fire"] is None, body["event"])

    status, body = call("POST", f"/v1/schedules/{AGENT}/digest/run")
    check("Run now on a paused record: refused in a sentence",
          status == 400 and "paused" in body["error"], body)

    status, body = call("POST", f"/v1/activity/{pause_id}/undo")
    check("undo the pause: a new change by the owner, op resume, naming it",
          status == 200 and (body["event"]["op"], body["event"]["actor"],
                             body["event"].get("undoes")) == ("resume", OWNER, pause_id)
          and "enabled: true" in record() and (UNITS / TIMER).is_file(), body.get("event"))
    status, body = call("POST", f"/v1/activity/{move_id}/undo")
    check("undo an older change: refused, it has changed since",
          status == 400 and "has changed since" in body["error"], body)
    status, body = call("POST", "/v1/activity/nosuchevent/undo")
    check("undo of no such change: 404", status == 404, body)

    # -- a refused change surfaces its sentence ----------------------------
    before = record()
    status, body = call("PATCH", f"/v1/schedules/{AGENT}/digest",
                        {"schedule": "now and then"})
    check("a schedule the grammar refuses: 400 with the path's sentence",
          status == 400 and len(body["error"]) > 20 and record() == before, body)
    check("and the refusal is in the log",
          ls.events()[-1]["kind"] == "refused" and ls.events()[-1]["actor"] == OWNER)
    soon = (datetime.now(timezone.utc) + timedelta(minutes=5)).astimezone(
        loam_cron.PACIFIC if hasattr(loam_cron, "PACIFIC") else eng.PACIFIC)
    status, body = call("POST", f"/v1/schedules/{AGENT}", {
        "name": "nudge", "at": soon.strftime("%Y-%m-%d %H:%M PT"),
        "note": "check the PR"})
    check("a one-off five minutes ahead: refused, the cap said",
          status == 400 and "at least 10 minutes ahead" in body["error"], body)
    status, body = call("PATCH", f"/v1/schedules/{AGENT}/digest", {"tools": "engineer"})
    check("a tool grant through the route: refused, no such field",
          status == 400 and "no field tools" in body["error"], body)

    # -- Run now -----------------------------------------------------------
    status, body = call("POST", f"/v1/schedules/{AGENT}/digest/run")
    check("Run now: the service is started, not waited on",
          status == 200 and ("start", "--no-block", f"loam-cron-{AGENT}-digest.service")
          in SYSTEMD.calls and body["event"]["kind"] == "run", body)

    # -- seen and unread, from either side ---------------------------------
    ls.change(AGENT, "digest", ls.set_header(record(), "schedule", "daily at 08:00 PT"),
              actor=AGENT, lane="wake", reason="the inbox fills by eight",
              systemd=SYSTEMD)
    status, body = call("GET", "/v1/activity")
    agent_item = body["items"][0]
    check("an agent's change: unread 1, the newest item, not seen",
          body["unread"] == 1 and agent_item["actor"] == AGENT
          and agent_item["seen"] is False, (body["unread"], agent_item))
    row = call("GET", f"/v1/schedules/{AGENT}/digest")[1]
    check("its row is marked: last change by the agent, not seen",
          row["last_change"]["actor"] == AGENT and row["last_change"]["seen"] is False
          and row["unseen"] == [agent_item["id"]], row["last_change"])
    status, body = call("POST", f"/v1/schedules/{AGENT}/digest/seen")
    check("seen on the row: unread 0", status == 200 and body["unread"] == 0, body)
    status, body = call("GET", "/v1/activity")
    check("and the Activity item reads seen",
          body["items"][0]["seen"] is True and body["unread"] == 0)

    ls.change(AGENT, "digest", ls.set_header(record(), "schedule", "daily at 08:15 PT"),
              actor=AGENT, lane="wake", reason="a quarter later", systemd=SYSTEMD)
    item = call("GET", "/v1/activity")[1]["items"][0]
    status, body = call("POST", "/v1/activity/seen", {"ids": [item["id"]]})
    row = call("GET", f"/v1/schedules/{AGENT}/digest")[1]
    check("seen in Activity: unread 0 and the row's marker is clear",
          body["unread"] == 0 and row["last_change"]["seen"] is True
          and row["unseen"] == [], (body, row["last_change"]))

    # -- a direct edit's lane ----------------------------------------------
    ls.BROKER_SESSIONS.mkdir()
    (ls.BROKER_SESSIONS / "s1.json").write_text(json.dumps(
        {"agent": AGENT, "live": True, "status": "working",
         "last_event_at": ls.iso(ls.now_utc())}))
    path = LABS / AGENT / "crons" / "digest.md"
    path.write_text(ls.set_header(ls.set_header(record(), "schedule",
                                                "daily at 09:00 PT"),
                                  "reason", "The owner asked for nine"))
    ls.reconcile([AGENT], systemd=SYSTEMD)
    last = [e for e in ls.events() if e["kind"] == "change"][-1]
    check("a direct edit during a live chat: lane conversation",
          (last["actor"], last["lane"], last.get("direct")) == (AGENT, "conversation", True),
          (last["actor"], last["lane"]))
    (ls.BROKER_SESSIONS / "s1.json").write_text(json.dumps(
        {"agent": AGENT, "live": True, "status": "done",
         "last_event_at": "2026-01-01T00:00:00Z"}))
    path.write_text(ls.set_header(record(), "schedule", "daily at 09:30 PT"))
    ls.reconcile([AGENT], systemd=SYSTEMD)
    last = [e for e in ls.events() if e["agent"] == AGENT and
            e["kind"] in ("change", "refused")][-1]
    check("with the chat long quiet and no run: lane unknown",
          last["lane"] == "unknown", (last["kind"], last["lane"]))
    path.write_text(ls.read_text(ls.applied_path(AGENT, "digest")))

    # -- a killed run --------------------------------------------------------
    failed = load("wake_failed", AGENTS / "bin" / "wake-failed")
    wake = load("wake", AGENTS / "bin" / "wake")
    wake.STATE_DIR = ls.WAKE_STATE
    wake.log_line = lambda agent, payload: None
    wake.record_failure = lambda agent, vault, reason: None
    failed.load_wake = lambda: wake
    failed.unit_property = lambda unit, name: {
        "Result": "signal", "ExecMainCode": "2", "ExecMainStatus": "15"}.get(name, "")
    ls.WAKE_STATE.mkdir(parents=True, exist_ok=True)
    (ls.WAKE_STATE / f"{AGENT}.running").write_text(json.dumps(
        {"ts": "2026-10-14T12:00:00Z", "pid": 1, "vault": str(TMP / "vault"),
         "lane": "cron:digest"}))
    sys.argv = ["wake-failed", f"loam-cron-{AGENT}-digest"]
    failed.main()
    fire = ls.events()[-1]
    check("wake-failed on a SIGTERM'd cron: a fire line, outcome killed",
          (fire["kind"], fire["record"], fire["outcome"]) == ("fire", "digest", "killed")
          and "status=15" in fire["stage"], fire)
    item = next(i for i in call("GET", f"/v1/activity?agent={AGENT}&kind=fire")[1]["items"])
    check("Activity shows it, pointing at the failure log",
          item["outcome"] == "killed" and item["produced"]["failure_log"].endswith(
              "loam-wake-failures.md"), item)

    # The wall clock: bin/wake's own main, its run cut by the lane's timeout.
    import contextlib  # noqa: PLC0415
    import types  # noqa: PLC0415
    cut = load("wake_cut", AGENTS / "bin" / "wake")  # this tree's, not the box's
    cut.STATE_DIR = ls.WAKE_STATE
    cut.loam_lock = types.SimpleNamespace(
        hold=lambda agent, lane, **kw: contextlib.nullcontext(), Busy=RuntimeError)

    def too_long(*args, **kwargs):
        raise subprocess.TimeoutExpired(["claude"], 1800)
    cut.run = too_long
    (LABS / AGENT / "agent.json").write_text(json.dumps(
        {"name": AGENT, "vault": str(TMP / "vault"), "wake_schedule": "02:00",
         "wake_timezone": "America/Los_Angeles"}) + "\n")
    sys.argv = ["wake", str(LABS / AGENT), "--cron", "digest"]
    try:
        cut.main()
    except subprocess.TimeoutExpired:
        pass
    fire = ls.events()[-1]
    check("bin/wake cut by its wall clock: a fire line, outcome killed",
          (fire["kind"], fire["outcome"], fire["stage"])
          == ("fire", "killed", "wall clock: cut after 1800 s"), fire)

    # -- a job cancel --------------------------------------------------------
    jobs = TMP / "demo-jobs"
    job_id = "20991231-000000-abcd1234"
    (jobs / job_id).mkdir(parents=True)
    (jobs / job_id / "brief.md").write_text(
        "---\nproject: demo\ntitle: A job to cancel\nbriefed_by: iris\n---\n\nDo nothing.\n")
    (jobs / job_id / "queued").write_text(json.dumps(
        {"joined": "2099-12-31T00:00:00Z", "job": job_id, "project": "demo"}))
    demo = eng.Project("demo", TMP, jobs, platform=False)
    real_projects = eng.projects
    eng.projects = lambda: {"demo": demo}
    try:
        live = call("GET", "/v1/activity?kind=job")[1]["jobs"]
        check("the queue shows the waiting job, first in line",
              [(q["job"], q["position"], q["briefed_by"]) for q in live["queue"]]
              == [(job_id, 1, "iris")], live["queue"])
        status, body = call("POST", f"/v1/jobs/{job_id}/cancel")
        check("cancel: 200, the launcher's sentence",
              status == 200 and "had not started and never will" in body["said"], body)
        result = json.loads((jobs / job_id / "result.json").read_text())
        by = json.loads((jobs / job_id / "cancelled").read_text())["by"]
        check("the durable cancel: verdict cancelled, by the owner",
              result["stage"] == "cancelled" and by == OWNER, (result["stage"], by))
        body = call("GET", "/v1/activity?kind=job")[1]
        check("Activity: no longer queued, a finished job, cancelled",
              body["jobs"]["queue"] == [] and body["items"][0]["job"] == job_id
              and body["items"][0]["stage"] == "cancelled"
              and body["items"][0]["title"] == "A job to cancel", body["items"][:1])
        status, body = call("POST", f"/v1/jobs/{job_id}/cancel")
        check("a second cancel: refused, it has a verdict",
              status == 400 and "already has a verdict" in body["error"], body)
        status, body = call("POST", "/v1/jobs/20991231-000000-ffff0000/cancel")
        check("cancel of no such job: 404", status == 404, body)
    finally:
        eng.projects = real_projects

    # -- a schedule is its file: Files saves, the row's state ---------------
    import loam_broker  # noqa: PLC0415
    loam_broker.AI_LABS = LABS
    watch = LABS / AGENT / "crons" / "watch.md"
    WATCH_TIMER = UNITS / f"loam-cron-{AGENT}-watch.timer"

    def read_file(name: str):
        return call("GET", f"/v1/files/read?root=spaces&path={AGENT}/crons/{name}.md")

    def save_file(name: str, text: str):
        base = read_file(name)[1]["revision"]
        return call("PUT", "/v1/files/text", {
            "root": "spaces", "path": f"{AGENT}/crons/{name}.md",
            "text": text, "base_revision": base})

    def row_of(name: str) -> dict:
        return call("GET", f"/v1/schedules/{AGENT}/{name}")[1]

    call("POST", f"/v1/schedules/{AGENT}", {
        "name": "watch", "schedule": "daily at 06:00 PT", "body": "Watch."})
    status, body = read_file("watch")
    check("Files: a crons record is authority schedule, writable",
          status == 200 and body["authority"] == "schedule"
          and body["capabilities"]["write"] is True, body.get("capabilities"))
    status, body = call("GET", f"/v1/files/stat?root=spaces&path={AGENT}/crons")
    check("Files: the crons folder itself is not a place to create",
          status == 200 and body["capabilities"]["create"] is False, body)
    status, body = call("GET", f"/v1/files/read?root=spaces&path={AGENT}/agent.json")
    check("Files: agent.json beside it stays read-only",
          status == 200 and body["capabilities"]["write"] is False, body.get("authority"))
    row = row_of("watch")
    check("a record made by the route: live, running this, no problem",
          (row["state"], row["running"], row["problem"], row["enabled"])
          == ("live", "this", None, True)
          and row["file"] == {"root": "spaces", "path": f"{AGENT}/crons/watch.md"},
          (row["state"], row["running"]))

    good = ls.set_header(watch.read_text(), "schedule", "daily at 07:00 PT")
    status, body = save_file("watch", good)
    last = [e for e in ls.events() if e.get("record") == "watch"
            and e["kind"] in ("change", "refused")][-1]
    check("Files save, no reason: a change by the owner on lane app, direct",
          status == 200 and (last["kind"], last["actor"], last["lane"],
                             last["direct"], last["reason"])
          == ("change", OWNER, "app", True, None), last)
    check("the editor stamped nothing: no provenance key in the record",
          body["provenance"] is False and "last_modified_by" not in watch.read_text()
          and "created_by" not in watch.read_text(), body.get("provenance"))
    check("the timer followed at once, not a minute later",
          "07:00:00" in WATCH_TIMER.read_text(), WATCH_TIMER.read_text()[-120:])
    sched = body["schedule"]
    check("the save answers the row: live, last edit by the owner",
          (sched["state"], sched["last_edit"]["by"], sched["last_edit"]["taken"],
           sched["changed_since_seen"]) == ("live", "owner", True, False), sched)

    broken = ls.set_header(good, "schedule", "whenever it rains")
    status, body = save_file("watch", broken)
    sched = body["schedule"]
    check("a broken save is saved: the file is what the owner wrote",
          status == 200 and watch.read_text() == broken)
    line_no = broken.splitlines().index("schedule: whenever it rains") + 1
    check("its row: broken, the previous version running, the bad line",
          (sched["state"], sched["running"], sched["problem"]["line"],
           sched["problem"]["text"]) == ("broken", "previous", line_no,
                                         "schedule: whenever it rains"), sched["problem"])
    check("the why is plain words, no host path, no grammar block",
          "whenever it rains" in sched["problem"]["why"]
          and "/" not in sched["problem"]["why"].split("`")[0]
          and "\n" not in sched["problem"]["why"], sched["problem"]["why"])
    check("the words say the previous version still runs",
          "previous version still runs (daily at 07:00 PT)" in sched["state_words"],
          sched["state_words"])
    check("the timer still stands at the good version, with a next fire",
          "07:00:00" in WATCH_TIMER.read_text() and sched["next_fire"] is not None)
    refusal = ls.events()[-1]
    check("the log: refused once, as the owner's, direct",
          (refusal["kind"], refusal["actor"], refusal["direct"])
          == ("refused", OWNER, True), refusal.get("why"))
    ls.reconcile([AGENT], systemd=SYSTEMD)
    check("a second reconcile does not log it again",
          ls.events()[-1]["id"] == refusal["id"])
    status, body = call("PATCH", f"/v1/schedules/{AGENT}/watch", {"enabled": False})
    check("the switch on a broken file: refused with the parse sentence",
          status == 400 and "whenever it rains" in body["error"]
          and watch.read_text() == broken, body)

    status, body = save_file("watch", good.replace("\n", "\r\n"))
    check("a CRLF save of the good text: taken as the owner's, live again",
          status == 200 and body["schedule"]["state"] == "live"
          and ls.events()[-1]["actor"] == OWNER, body["schedule"]["state"])

    stamped = ls.set_header(good, "last_modified_by", '"[[Sam]]"')
    watch.write_text(stamped)
    ls.reconcile([AGENT], systemd=SYSTEMD)
    row = row_of("watch")
    check("a stamped key would not parse: broken, its line named",
          row["state"] == "broken" and row["problem"]["text"].startswith(
              "last_modified_by:"), row["problem"])

    watch.write_text(ls.set_header(good, "schedule", "daily at 08:00 PT"))
    row = row_of("watch")
    check("a hand edit not yet read: pending, previous running",
          (row["state"], row["running"]) == ("pending", "previous"), row["state"])
    ls.reconcile([AGENT], systemd=SYSTEMD)
    row = row_of("watch")
    check("read with no run and no reason: refused as the agent's, said so",
          (row["state"], row["running"], row["last_edit"]["by"],
           row["last_edit"]["taken"]) == ("refused", "previous", "agent", False)
          and "carries a reason" in row["problem"]["why"], row["state_words"])
    check("and it is unseen on the row",
          row["changed_since_seen"] is True, row["unseen"])
    call("POST", f"/v1/schedules/{AGENT}/watch/seen")
    check("marked seen: changed_since_seen goes false",
          row_of("watch")["changed_since_seen"] is False)

    fresh = LABS / AGENT / "crons" / "fresh.md"
    fresh.write_text("---\nschedule: sometimes\nenabled: true\n---\nGo.\n")
    ls.reconcile([AGENT], systemd=SYSTEMD)
    row = row_of("fresh")
    check("broken with nothing accepted before: running none, no next fire",
          (row["state"], row["running"], row["next_fire"], row["problem"]["line"])
          == ("broken", "none", None, 2), row["state_words"])
    check("its words say nothing runs", row["state_words"].endswith("Nothing runs."),
          row["state_words"])
    fresh.unlink()
    ls.reconcile([AGENT], systemd=SYSTEMD)

    ls.change(AGENT, "reflection", ls.reflection_record("02:00"), actor="engineer",
              lane="engineer", reason="the test's reflection", systemd=SYSTEMD)
    status, body = save_file("reflection", ls.reflection_record("03:30"))
    config = json.loads((LABS / AGENT / "agent.json").read_text())
    check("Files save of crons/reflection.md: the owner's, agent.json follows",
          status == 200 and body["schedule"]["state"] == "live"
          and body["schedule"]["last_edit"]["actor"] == OWNER
          and config["wake_schedule"] == "03:30", body["schedule"]["state_words"])
    status, body = call("GET", f"/v1/schedules/{AGENT}")
    check("GET /v1/schedules: every row has the line's fields",
          status == 200 and all({"state", "running", "state_words", "problem",
                                 "enabled", "next_fire", "last_edit",
                                 "changed_since_seen", "running_since"} <= set(r)
                                for r in body["schedules"]), body["schedules"][:1])

    # -- a run in flight: running_since, and next_fire kept ------------------
    # The 10-15 read: Ada's reflection mid-run came back next_fire null,
    # because systemd blanks a running timer's next elapse. The active state
    # is simulated in the fake (as systemd 252 reports it); the next elapse
    # is the real systemd-analyze's arithmetic on the rendered OnCalendar.
    wake_timer = f"loam-wake-{AGENT}.timer"
    print("reflection, idle:", json.dumps({k: row_of("reflection")[k] for k in
          ("name", "running", "next_fire", "running_since", "last_fire")}))
    SYSTEMD.running[wake_timer] = "Thu 2026-10-15 03:30:04 PDT"
    status, body = call("GET", f"/v1/schedules/{AGENT}")
    row = next(r for r in body["schedules"] if r["name"] == "reflection")
    print("reflection, mid-run:", json.dumps({k: row[k] for k in
          ("name", "running", "next_fire", "running_since", "last_fire")}))
    now = ls.iso(datetime.now(timezone.utc))
    check("mid-run: running_since is the run's start, next_fire still set",
          row["running_since"] == "2026-10-15T10:30:04Z"
          and row["next_fire"] is not None and row["next_fire"] > now
          and row["next_fire"].endswith(":30:00Z"),
          (row["running_since"], row["next_fire"]))
    check("mid-run: one record's run leaves the others' running_since null",
          all(r["running_since"] is None for r in body["schedules"]
              if r["name"] != "reflection"), [r["name"] for r in body["schedules"]])
    del SYSTEMD.running[wake_timer]
    row = row_of("reflection")
    check("no run in flight: running_since is null, next_fire the timer's",
          (row["running_since"], row["next_fire"]) == (None, "2026-10-21T12:00:00Z"),
          (row["running_since"], row["next_fire"]))
    watch.write_text(good)
    ls.claim(AGENT, "watch", good, actor=OWNER, lane="app")
    ls.reconcile([AGENT], systemd=SYSTEMD)

    # -- a persona's change through the same route (`by`, 2026-11-10) -----
    other = f"zzoth{os.getpid()}"
    (LABS / other / "crons").mkdir(parents=True)
    (LABS / other / "agent.json").write_text(json.dumps({"name": other}) + "\n")
    status, body = call("PATCH", f"/v1/schedules/{AGENT}/digest",
                        {"enabled": False, "by": AGENT, "lane": "conversation"})
    check("by, no reason: refused by the path, record unchanged",
          status == 400 and "carries a reason" in body["error"]
          and "enabled: true" in record(), body)
    status, body = call("PATCH", f"/v1/schedules/{AGENT}/digest",
                        {"enabled": False, "by": AGENT, "lane": "conversation",
                         "reason": "The owner asked to pause it"})
    ev = body.get("event", {})
    check("by with a reason: a pause by the persona on lane conversation",
          status == 200 and (ev.get("op"), ev.get("actor"), ev.get("lane"))
          == ("pause", AGENT, "conversation") and "enabled: false" in record(), ev)
    status, body = call("PATCH", f"/v1/schedules/{AGENT}/digest",
                        {"enabled": True, "by": other, "reason": "x"})
    check("another persona's schedule: refused, its own only",
          status == 400 and "its own schedules only" in body["error"]
          and "enabled: false" in record(), body)
    call("PATCH", f"/v1/schedules/{AGENT}/digest",
         {"enabled": True, "by": AGENT, "reason": "back on"})
    started = len(SYSTEMD.calls)
    status, body = call("POST", f"/v1/schedules/{AGENT}/digest/run",
                        {"by": AGENT, "lane": "cron"})
    check("Run now by a persona from a cron: refused (Boundary 3)",
          status == 400 and "only from a conversation" in body["error"]
          and not any(c[0] == "start" for c in SYSTEMD.calls[started:]), body)
    status, body = call("POST", f"/v1/schedules/{AGENT}/digest/run",
                        {"by": other, "lane": "conversation"})
    check("Run now of another persona's schedule: refused",
          status == 400 and "its own schedules only" in body["error"], body)
    status, body = call("POST", f"/v1/schedules/{AGENT}/digest/run",
                        {"by": AGENT, "lane": "conversation"})
    check("Run now by the persona in a conversation: started, logged as it",
          status == 200 and (body["event"].get("actor"), body["event"].get("lane"))
          == (AGENT, "conversation"), body)
    shutil.rmtree(LABS / other)

    # -- delete, filters, paging -------------------------------------------
    status, body = call("DELETE", f"/v1/schedules/{AGENT}/digest")
    check("delete: op delete, the record and timer are gone",
          status == 200 and body["event"]["op"] == "delete"
          and not path.exists() and not (UNITS / TIMER).exists(), body.get("event"))
    body = call("GET", f"/v1/activity?agent={AGENT}&kind=change&limit=3")[1]
    check("filters and paging: changes of one agent, three a page",
          len(body["items"]) == 3 and body["next_offset"] == 3
          and all(i["kind"] == "change" and i["agent"] == AGENT for i in body["items"]),
          (body["total"], body["next_offset"]))
    status, body = call("GET", "/v1/activity?kind=gossip")
    check("an unknown kind: 400 naming the kinds", status == 400
          and "change, refused, fire, run, job" in body["error"], body)
    status, body = call("PUT", "/v1/schedules")
    check("an unknown route: 405", status == 405, body)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if results and all(results) else 1)
