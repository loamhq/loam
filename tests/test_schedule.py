#!/usr/bin/env python3
"""Schedules: a record is live exactly when it says it is enabled.

Against a real systemd user manager -- a private one, started for this test
alone (tests/private_systemd.py, since 2026-11-16), never the box's own: a
made-up agent in a temp `ai-labs`, its units in that manager's runtime unit
directory, enabled with `--runtime`. Until 11-16 this ran in the live user
manager's /run/user/<uid>/systemd/user, beside every persona's timers. Every
unit is removed at the end, pass or fail, and the manager is stopped.

What it proves, each against the timer the manager actually holds:
  - a recurring record created, moved, paused, resumed, and deleted through
    the path; the timer follows each, and the log has a line for each
  - an agent's change with no reason is refused, logged, and changes nothing;
    so is a change to another agent's record and a `tools:` key
  - a direct edit of the file is picked up by the reconciler: refused with no
    `reason:` header (the timer keeps its old time), applied with one
  - the one-off caps: 10 minutes to 30 days ahead, five pending
  - a one-off fires once from its real timer, is marked done, and its timer
    is removed; a done record does not launch
  - the migration: `approved:` becomes `enabled: true`, the reflection gains
    a record whose timer is byte-identical to the one provision wrote

Run: python3 tests/test_schedule.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent / "lib"
sys.path.insert(0, str(LIB))

import loam_schedule as ls  # noqa: E402
import loam_cron  # noqa: E402
import loam_config as lc  # noqa: E402
from private_systemd import PrivateManager  # noqa: E402
OWNER = lc.OWNER_ID

results: list[bool] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:58}{'  | ' + str(detail) if detail else ''}")


AGENT = f"zzsched{os.getpid()}"
OTHER = f"zzother{os.getpid()}"
TMP = Path(tempfile.mkdtemp(prefix="loam-schedule-test-"))
LABS = TMP / "ai-labs"
# The test's own user manager; every `systemctl --user` below, this
# process's and its children's, reaches it and not the box's.
MANAGER = PrivateManager(TMP / "systemd").start()
UNITS = MANAGER.unit_dir

for name in (AGENT, OTHER):
    (LABS / name / "crons").mkdir(parents=True)
    (LABS / name / "agent.json").write_text(json.dumps(
        {"name": name, "display": name.capitalize(), "wake_schedule": "02:00",
         "wake_timezone": "America/Los_Angeles"}, indent=2) + "\n")

# The wake a fire launches: records the fire through the same call bin/wake
# makes, pointed at this scratch box.
# The stub runs under the (private) user manager, so it gets the code under
# test as a copy of its own rather than whatever the manager's view of the
# runtime's path holds.
STUB_LIB = TMP / "lib"
shutil.copytree(LIB, STUB_LIB, ignore=shutil.ignore_patterns("__pycache__"))
STUB = TMP / "wake-stub"
STUB.write_text(f"""#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, {str(STUB_LIB)!r})
import loam_schedule as ls, loam_cron
loam_cron.AI_LABS = Path({str(LABS)!r})
loam_cron.UNIT_DIR = Path({str(UNITS)!r})
ls.STATE = Path({str(TMP / 'state')!r})
ls.RUNTIME, ls.WRITE_PAGE = True, False
ls.record_fire(Path(sys.argv[1]).name, sys.argv[3], "ran", lane="cron:" + sys.argv[3])
""")
STUB.chmod(0o755)

loam_cron.AI_LABS = LABS
loam_cron.WAKE_BIN = STUB
ls.STATE = TMP / "state"
ls.WAKE_STATE = TMP / "wake"
ls.RUNTIME, ls.WRITE_PAGE = True, False
SD = ls.Systemd(unit_dir=UNITS)
UNIT = f"loam-cron-{AGENT}-digest"


def record(schedule: str, enabled: str = "true", extra: str = "") -> str:
    return (f"---\nschedule: {schedule}\nenabled: {enabled}\n{extra}---\n\n"
            f"# Cron: digest\n\nRead the week and write it down.\n")


def next_elapse(unit: str) -> str:
    return SD.next_elapse(f"{unit}.timer")


def list_timer(unit: str) -> str:
    out = SD.run("list-timers", "--all", "--no-pager", f"{unit}.timer").stdout
    rows = [r for r in out.splitlines() if unit in r]
    return rows[0] if rows else "(no row)"


def last_event(**match) -> dict:
    for event in reversed(ls.events()):
        if all(event.get(k) == v for k, v in match.items()):
            return event
    return {}


def show(event: dict) -> str:
    return json.dumps({k: v for k, v in event.items()
                       if k not in ("before", "after", "id")})


try:
    # -- recurring: create --------------------------------------------------
    ls.change(AGENT, "digest", record("Sundays at 05:00 PT"), actor=AGENT,
              lane="conversation", reason="a weekly digest the owner asked for",
              systemd=SD)
    nxt = next_elapse(UNIT)
    check("create: timer installed and armed", (UNITS / f"{UNIT}.timer").exists()
          and nxt.startswith("Sun") and "05:00:00" in nxt, list_timer(UNIT))
    print("      log:", show(last_event(kind="change", record="digest")))
    print("      log:", show(last_event(kind="timer", unit=f"{UNIT}.timer")))

    # -- move ---------------------------------------------------------------
    ls.change(AGENT, "digest", record("Wednesdays at 06:30 PT"), actor=AGENT,
              lane="wake", reason="Sunday collides with the audit", systemd=SD)
    nxt = next_elapse(UNIT)
    check("move: the timer follows to Wed 06:30", nxt.startswith("Wed")
          and "06:30:00" in nxt, list_timer(UNIT))
    print("      log:", show(last_event(kind="change", op="move")))
    print("      log:", show(last_event(kind="timer", action="recut")))

    # -- pause / resume -------------------------------------------------------
    ls.set_key(AGENT, "digest", "enabled", "false", actor=OWNER,
               lane="conversation", systemd=SD)
    check("pause: timer and service removed",
          not (UNITS / f"{UNIT}.timer").exists()
          and not (UNITS / f"{UNIT}.service").exists(), list_timer(UNIT))
    check("  a paused record does not launch",
          ls.enabled_now(LABS / AGENT, "digest")[0] is False,
          ls.enabled_now(LABS / AGENT, "digest")[1])
    print("      log:", show(last_event(kind="change", op="pause")))
    ls.set_key(AGENT, "digest", "enabled", "true", actor=AGENT, lane="cron",
               reason="back on", systemd=SD)
    check("resume: armed again at Wed 06:30",
          next_elapse(UNIT).startswith("Wed"), list_timer(UNIT))

    # -- refusals through the path ------------------------------------------
    before = (LABS / AGENT / "crons" / "digest.md").read_text()
    for label, kw, text in (
        ("an agent's change with no reason", dict(actor=AGENT, reason=""),
         record("daily at 04:00 PT")),
        ("a change to another agent's record", dict(actor=OTHER, reason="x"),
         record("daily at 04:00 PT")),
        ("a `tools:` key (grants are the owner's)", dict(actor=AGENT, reason="x"),
         record("daily at 04:00 PT", extra="tools: engineer\n")),
    ):
        try:
            ls.change(AGENT, "digest", text, lane="wake", systemd=SD, **kw)
            check(f"refused: {label}", False, "accepted")
        except ls.Refused as exc:
            check(f"refused: {label}", True, str(exc)[:70])
    check("  the record and the timer are as they were",
          (LABS / AGENT / "crons" / "digest.md").read_text() == before
          and next_elapse(UNIT).startswith("Wed"))
    print("      log:", show(last_event(kind="refused", reason=None)))

    # -- direct edits, picked up by the reconciler -----------------------------
    (LABS / AGENT / "crons" / "digest.md").write_text(record("daily at 04:00 PT"))
    ls.reconcile([AGENT, OTHER], systemd=SD)
    check("direct edit, no reason: refused, timer kept",
          next_elapse(UNIT).startswith("Wed")
          and last_event(kind="refused", direct=True).get("record") == "digest",
          last_event(kind="refused", direct=True).get("why", "")[:60])
    ls.reconcile([AGENT, OTHER], systemd=SD)
    check("  refused once, not every minute",
          sum(1 for e in ls.events() if e.get("kind") == "refused"
              and e.get("direct")) == 1)
    (LABS / AGENT / "crons" / "digest.md").write_text(
        record("daily at 04:00 PT", extra="reason: earlier, before the grid\n"))
    ls.reconcile([AGENT, OTHER], systemd=SD)
    nxt = next_elapse(UNIT)
    check("direct edit with reason: applied", "04:00:00" in nxt, list_timer(UNIT))

    # -- delete ---------------------------------------------------------------
    ls.change(AGENT, "digest", None, actor=AGENT, lane="conversation",
              reason="folded into the reflection", systemd=SD)
    check("delete: units gone", not (UNITS / f"{UNIT}.timer").exists()
          and not (UNITS / f"{UNIT}.service").exists(), list_timer(UNIT))
    print("      log:", show(last_event(kind="change", op="delete")))
    print("      log:", show(last_event(kind="timer", action="removed")))

    # -- one-off caps ----------------------------------------------------------
    pt = loam_cron.pt_zone()
    now = datetime.now(timezone.utc)

    def one_off(moment: datetime, note: str = "check the PR") -> str:
        return (f"---\nat: {moment.astimezone(pt):%Y-%m-%d %H:%M} PT\n"
                f"note: {note}\nenabled: true\n---\n")

    for label, delta in (("5 minutes ahead", timedelta(minutes=5)),
                         ("31 days ahead", timedelta(days=31))):
        try:
            ls.change(AGENT, "soon", one_off(now + delta), actor=AGENT,
                      lane="wake", reason="r", systemd=SD)
            check(f"one-off refused: {label}", False, "accepted")
        except ls.Refused as exc:
            check(f"one-off refused: {label}", True, str(exc)[:60])
    for i in range(5):
        ls.change(AGENT, f"later-{i}", one_off(now + timedelta(days=2, hours=i)),
                  actor=AGENT, lane="wake", reason="r", systemd=SD)
    try:
        ls.change(AGENT, "later-5", one_off(now + timedelta(days=3)),
                  actor=AGENT, lane="wake", reason="r", systemd=SD)
        check("one-off refused: a sixth pending", False, "accepted")
    except ls.Refused as exc:
        check("one-off refused: a sixth pending", True, str(exc)[:60])
    # A paused one-off cannot fire, so it is not pending (2026-11-09: three
    # paused one-offs refused Ada's five overnight ones).
    ls.set_key(AGENT, "later-4", "enabled", "false", actor=AGENT, lane="wake",
               reason="r", systemd=SD)
    ls.change(AGENT, "later-5", one_off(now + timedelta(days=3)),
              actor=AGENT, lane="wake", reason="r", systemd=SD)
    check("one-off accepted: a paused one does not count as pending", True)
    ls.change(AGENT, "later-5", None, actor=OWNER, lane="conversation",
              systemd=SD)
    for i in range(5):
        ls.change(AGENT, f"later-{i}", None, actor=OWNER, lane="conversation",
                  systemd=SD)

    # -- a one-off fires once from its real timer -----------------------------
    fire_at = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
    if (fire_at - now).total_seconds() < 20:
        fire_at += timedelta(minutes=1)
    ls.change(AGENT, "nudge", one_off(fire_at, "did the PR merge?"),
              actor=AGENT, lane="conversation", reason="follow up on the PR",
              now=fire_at - timedelta(minutes=11), systemd=SD)
    # And a recurring record due the same minute, so that after it has fired
    # it can be moved: a re-cut of a timer that has triggered before must not
    # fire it (the migration of 2026-10-14 did, through a `restart`).
    ls.change(AGENT, "tick", record(f"daily at {fire_at.astimezone(loam_cron.pt_zone()):%H:%M} PT"),
              actor=AGENT, lane="conversation", reason="a daily tick",
              systemd=SD)
    nunit = f"loam-cron-{AGENT}-nudge"
    print("      armed:", list_timer(nunit))
    deadline = time.monotonic() + 150
    head = {}
    while time.monotonic() < deadline:
        head = ls.header_of((LABS / AGENT / "crons" / "nudge.md").read_text())
        if head.get("done"):
            break
        time.sleep(2)
    check("one-off fired and was marked done", head.get("done", "").endswith(" ran"),
          head.get("done"))
    # `done` is written before the reconcile that removes the timer, inside
    # the same change(), so the poll above can see the record between the two
    # (2 runs in 2 on 2026-11-08). Wait for the removal, as for the fire.
    deadline = time.monotonic() + 30
    while (UNITS / f"{nunit}.timer").exists() and time.monotonic() < deadline:
        time.sleep(0.5)
    check("  its timer was removed", not (UNITS / f"{nunit}.timer").exists())
    fires = [e for e in ls.events() if e.get("kind") == "fire"
             and e.get("record") == "nudge"]
    check("  exactly one fire logged", len(fires) == 1, show(fires[0]) if fires else "")
    print("      log:", show(last_event(kind="change", op="done")))
    check("  a done one-off does not launch again",
          ls.enabled_now(LABS / AGENT, "nudge")[0] is False, ls.enabled_now(LABS / AGENT, "nudge")[1])

    def tick_fires() -> int:
        return sum(1 for e in ls.events() if e.get("kind") == "fire"
                   and e.get("record") == "tick")

    deadline = time.monotonic() + 30
    while tick_fires() < 1 and time.monotonic() < deadline:
        time.sleep(1)
    tunit = f"loam-cron-{AGENT}-tick"
    check("recurring record fired at its minute", tick_fires() == 1,
          SD.last_trigger(f"{tunit}.timer"))
    ls.change(AGENT, "tick", record("Mondays at 09:15 PT"), actor=AGENT,
              lane="wake", reason="weekly is enough", systemd=SD)
    time.sleep(6)
    check("  moving a timer that has fired does not fire it again",
          tick_fires() == 1 and next_elapse(tunit).startswith("Mon"),
          f"fires={tick_fires()} {list_timer(tunit)}")
    ls.set_key(AGENT, "tick", "enabled", "false", actor=OWNER,
               lane="conversation", systemd=SD)
    ls.set_key(AGENT, "tick", "enabled", "true", actor=OWNER,
               lane="conversation", systemd=SD)
    time.sleep(6)
    check("  nor does pausing and resuming it", tick_fires() == 1
          and next_elapse(tunit).startswith("Mon"),
          f"fires={tick_fires()} {list_timer(tunit)}")

    # -- migration -------------------------------------------------------------
    legacy = ("---\nschedule: Sundays at 07:00 PT\napproved: 2026-10-07\n---\n\n"
              "# Cron: audit\n\nAudit the vocabulary.\n")
    (LABS / OTHER / "crons" / "audit.md").write_text(legacy)
    old_timer = ("[Unit]\nDescription=Daily reflection wake for "
                 f"{OTHER.capitalize()} at 02:00 Pacific\n\n[Timer]\n"
                 "OnCalendar=*-*-* 02:00:00 America/Los_Angeles\nPersistent=false\n"
                 f"Unit=loam-wake-{OTHER}.service\n\n[Install]\n"
                 "WantedBy=timers.target\n")
    # The reflection's timer names loam-wake-OTHER.service, which provision
    # writes for a real agent. Without one, systemd logs "Failed to start
    # loam-wake-zzother<pid>.timer" on every landing's test run (seen on
    # every landing through 2026-11-09). A stub that is never due in the
    # test's few minutes; the finally below removes it with the rest.
    (UNITS / f"loam-wake-{OTHER}.service").write_text(
        "[Unit]\nDescription=test stub for the reflection timer\n\n"
        "[Service]\nType=oneshot\nExecStart=/bin/true\n")
    SD.reload()
    ls.migrate([OTHER], systemd=SD)
    migrated = (LABS / OTHER / "crons" / "audit.md").read_text()
    check("migrate: approved: became enabled: true",
          "enabled: true" in migrated and "approved" not in migrated
          and migrated.endswith("Audit the vocabulary.\n"), migrated.split("---")[1])
    check("  the audit's timer is installed",
          next_elapse(f"loam-cron-{OTHER}-audit").startswith("Sun"),
          list_timer(f"loam-cron-{OTHER}-audit"))
    check("  reflection record's timer is byte-identical to provision's",
          (UNITS / f"loam-wake-{OTHER}.timer").read_text() == old_timer)
    ls.set_key(OTHER, "reflection", "schedule", "daily at 01:40 PT",
               actor=OTHER, lane="conversation", reason="earlier", systemd=SD)
    check("  moving the reflection moves its timer and agent.json",
          "OnCalendar=*-*-* 01:40:00" in (UNITS / f"loam-wake-{OTHER}.timer").read_text()
          and json.loads((LABS / OTHER / "agent.json").read_text())["wake_schedule"] == "01:40")
    try:
        ls.change(OTHER, "reflection", None, actor=OWNER, lane="conversation",
                  systemd=SD)
        check("  the reflection's record cannot be deleted", False)
    except ls.Refused as exc:
        check("  the reflection's record cannot be deleted", True, str(exc)[:50])
    ls.set_key(OTHER, "reflection", "enabled", "false", actor=OWNER,
               lane="conversation", systemd=SD)
    check("  pausing the reflection removes its timer",
          not (UNITS / f"loam-wake-{OTHER}.timer").exists())

    # -- the box's own manager ---------------------------------------------------
    live = subprocess.run(
        ["systemctl", "--user", "list-units", "--all", "--no-legend", "--plain",
         f"*{AGENT}*", f"*{OTHER}*"], capture_output=True, text=True, check=False,
        env={**{k: v for k, v in os.environ.items() if k not in MANAGER.env()},
             "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}"}, timeout=30)
    live_dir = Path(f"/run/user/{os.getuid()}/systemd/user")
    check("the box's own user manager never held one of this test's units",
          live.stdout.strip() == "" and not any(
              AGENT in f.name or OTHER in f.name
              for f in (live_dir.iterdir() if live_dir.is_dir() else [])),
          live.stdout.strip()[:200] or live.stderr.strip()[:200])
finally:
    for unit in sorted(UNITS.glob("loam-*")):
        if AGENT in unit.name or OTHER in unit.name:
            if unit.suffix == ".timer":
                SD.disarm(unit.name)
            unit.unlink(missing_ok=True)
    for wants in UNITS.glob("*.wants"):
        for link in wants.iterdir():
            if AGENT in link.name or OTHER in link.name:
                link.unlink()
    SD.reload()
    MANAGER.stop()
    shutil.rmtree(TMP, ignore_errors=True)

failed = results.count(False)
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
