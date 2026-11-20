#!/usr/bin/env python3
"""The schedule grammar and the installer's refusals, against the real code.

Two halves.

**The grammar** is the part an agent has to be able to write from a sentence
in its config, so every shape documented in ``SCHEDULE_GRAMMAR`` is asserted
here, and each rendered spec is handed to ``systemd-analyze`` — the same
authority the installer uses. A grammar that our parser accepts and systemd
does not is the failure that would otherwise show up as a timer that simply
never fires.

**The refusals** are what keep a record readable. An unknown key (a tool
grant above all), an unparseable schedule, an empty body, an over-limit body,
a one-off with no note: each is exercised against a real file on disk through
the real ``read_cron``. Who may change a record, and the lifecycle, are
tests/test_schedule.py's.

Run: python3 tests/test_cron.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import loam_cron  # noqa: E402
from loam_memory import LIMIT_CHARS  # noqa: E402

results: list[bool] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:52}{'  | ' + detail if detail else ''}")


# ---------------------------------------------------------------------------
# The grammar. Every documented shape, and systemd's own agreement on each.
# ---------------------------------------------------------------------------
GRAMMAR_CASES = [
    ("daily at 05:00 PT",                 "*-*-* 05:00:00 America/Los_Angeles"),
    ("weekdays at 06:30 PT",              "Mon..Fri *-*-* 06:30:00 America/Los_Angeles"),
    ("weekends at 09:00 PT",              "Sat,Sun *-*-* 09:00:00 America/Los_Angeles"),
    ("Sundays at 07:00 PT",               "Sun *-*-* 07:00:00 America/Los_Angeles"),
    ("Mondays and Thursdays at 06:00 PT", "Mon,Thu *-*-* 06:00:00 America/Los_Angeles"),
    # Case and spacing are the agent's to get wrong; the grammar is small
    # enough that being forgiving here costs nothing and refusing costs a run.
    ("DAILY  at  5:00  PT",               "*-*-* 05:00:00 America/Los_Angeles"),
    ("Thursdays and Mondays at 06:00 PT", "Mon,Thu *-*-* 06:00:00 America/Los_Angeles"),
]

for text, expected in GRAMMAR_CASES:
    try:
        raw, spec = loam_cron.parse_schedule(text)
        ok = spec == expected
        detail = "" if ok else f"got {spec!r}, wanted {expected!r}"
    except loam_cron.CronError as exc:
        ok, detail = False, str(exc).splitlines()[0]
    check(f"parses {text!r}", ok, detail)
    if ok:
        try:
            loam_cron.assert_systemd_parses(spec)
            check(f"  systemd agrees on {spec}", True)
        except loam_cron.CronError as exc:
            check(f"  systemd agrees on {spec}", False, str(exc))

BAD_SCHEDULES = [
    "",
    "daily",                       # no time
    "daily at 05:00",              # no zone
    "daily at 05:00 XYZ",          # unknown time zone
    "daily at 25:00 PT",           # impossible hour
    "daily at 05:61 PT",           # impossible minute
    "fortnightly at 05:00 PT",     # not in the grammar
    "Mondays and Blursdays at 06:00 PT",
    "every second Tuesday at 06:00 PT",
    "0 5 * * *",                   # crontab, which is deliberately not it
]
for text in BAD_SCHEDULES:
    try:
        loam_cron.parse_schedule(text)
        check(f"refuses {text!r}", False, "it was accepted")
    except loam_cron.CronError:
        check(f"refuses {text!r}", True)


# ---------------------------------------------------------------------------
# The refusals, against real files. loam_cron reads from the live AI_LABS, so
# the module's root is pointed at a throwaway tree for the duration.
# ---------------------------------------------------------------------------
print()
root = Path(tempfile.mkdtemp(prefix="loam-cron-test-"))
real_root = loam_cron.AI_LABS
try:
    loam_cron.AI_LABS = root
    ws = root / "testagent"
    (ws / "crons").mkdir(parents=True)
    (ws / "agent.json").write_text(
        '{"name": "testagent", "display": "Testagent", '
        f'"workspace": "{ws}", "vault": "{root}/vault", '
        '"wake_schedule": "03:00", "wake_timezone": "America/Los_Angeles"}')

    def write(name: str, text: str) -> None:
        (ws / "crons" / f"{name}.md").write_text(text, encoding="utf-8")

    good = "---\nschedule: daily at 05:00 PT\napproved: 2026-09-05\n---\n\nDo the thing.\n"
    write("good", good)
    write("no-approval", "---\nschedule: daily at 05:00 PT\n---\n\nDo the thing.\n")
    write("blank-approval", "---\nschedule: daily at 05:00 PT\napproved:\n---\n\nDo it.\n")
    write("bad-approval", "---\nschedule: daily at 05:00 PT\napproved: yes\n---\n\nDo it.\n")
    write("soon", "---\nschedule: soon-ish\napproved: 2026-09-05\n---\n\nDo it.\n")
    write("no-body", "---\nschedule: daily at 05:00 PT\napproved: 2026-09-05\n---\n")
    write("no-header", "Just a body, no header at all.\n")
    write("huge", "---\nschedule: daily at 05:00 PT\napproved: 2026-09-05\n---\n\n"
                  + "x" * (LIMIT_CHARS + 1))

    write("tool-grant", "---\nschedule: daily at 05:00 PT\nenabled: true\n"
                        "tools: engineer\n---\n\nDo it.\n")
    write("both", "---\nschedule: daily at 05:00 PT\nat: 2026-10-20 14:30 PT\n"
                  "note: x\nenabled: true\n---\n\nDo it.\n")
    write("one-off-no-note", "---\nat: 2026-10-20 14:30 PT\nenabled: true\n---\n")
    write("reflection", "---\nschedule: daily at 05:00 PT\nenabled: true\n---\n\nx\n")

    # A record without enabled is inactive. Preserve compatibility with legacy approval
    # metadata.
    for label, name, want in (
        ("legacy `approved:` date reads as enabled", "good", True),
        ("no `enabled:` and no `approved:`: not live", "no-approval", False),
        ("`approved:` empty: not live", "blank-approval", False),
    ):
        check(label, loam_cron.read_cron("testagent", name).enabled is want)

    REFUSALS = [
        ("good file parses",              "good",           True),
        ("a `tools:` key is refused",     "tool-grant",     False),
        ("`schedule:` and `at:` together", "both",          False),
        ("a one-off without a note",      "one-off-no-note", False),
        ("`reflection` without its lane", "reflection",     False),
        ("schedule not in the grammar",   "soon",           False),
        ("header but no body",            "no-body",        False),
        ("no header at all",              "no-header",      False),
        ("over the char limit: not refused", "huge",         True),
        ("no such file",                  "missing",        False),
        ("name is not kebab-safe",        "../escape",      False),
    ]
    for label, name, expect_ok in REFUSALS:
        try:
            loam_cron.read_cron("testagent", name)
            got, detail = True, ""
        except loam_cron.CronError as exc:
            got, detail = False, str(exc).splitlines()[0][:80]
        check(label, got == expect_ok, detail if got != expect_ok else "")

    # The units, rendered from the good file. Asserted on the properties that
    # would be silent failures rather than on the whole text: a started
    # service, a missing timezone, a catch-up fire, a [Install] on a oneshot.
    cron = loam_cron.read_cron("testagent", "good")
    service, timer = loam_cron.render_units(cron)
    check("service runs the cron lane, not reflection",
          f"--cron {cron.name}" in service)
    # Section headers, not the word: the service explains in a comment why
    # it has no [Install], and a substring test would read its own reasoning
    # as the thing it forbids.
    check("service has no [Install] (a timer starts it, nothing else)",
          "[Install]" not in
          [ln.strip() for ln in service.splitlines() if not ln.startswith("#")])
    check("service carries the lane's TimeoutStartSec",
          f"TimeoutStartSec={loam_cron.unit_timeout_s('testagent', 'cron:good')}" in service)

    # Neither the launcher nor the generated unit imposes an implicit wall clock.
    for agent, lane in (("ada", "cron:infra-watch"), ("ada", "reflection"),
                        ("iris", "cron:strategic-watch")):
        check(f"{agent} {lane}: no wall clock",
              loam_cron.lane_timeout_s(agent, lane) is None)
        check(f"{agent} {lane}: the unit never times out",
              loam_cron.unit_timeout_s(agent, lane) == "infinity")
    check("the cron unit says TimeoutStartSec=infinity",
          "TimeoutStartSec=infinity" in service)

    # And the wake withholds no tool from any lane: no --disallowed-tools.
    import importlib.machinery  # noqa: PLC0415
    import importlib.util  # noqa: PLC0415
    loader = importlib.machinery.SourceFileLoader(
        "wake_under_test", str(Path(__file__).resolve().parent.parent / "bin" / "wake"))
    spec = importlib.util.spec_from_loader("wake_under_test", loader)
    wake = importlib.util.module_from_spec(spec)
    loader.exec_module(wake)
    for agent, lane in (("ada", "reflection"), ("ada", "cron:strategic-watch"),
                        ("iris", "reflection"), ("iris", "cron:infra-watch")):
        argv = wake.claude_argv(agent, lane, "p", Path("/dev/null"))
        check(f"wake: {agent} {lane} withholds nothing",
              "--disallowed-tools" not in argv)
    check("service keeps the shared OnFailure handler",
          "OnFailure=loam-wake-failed@%N.service" in service)
    check("timer carries the zone explicitly",
          "America/Los_Angeles" in timer)
    check("timer does not catch up", "Persistent=false" in timer)
    check("timer is the thing that is enabled",
          "WantedBy=timers.target" in timer)

    # Unit names round-trip through the roster, including a cron whose name
    # contains the dash that makes naive splitting wrong.
    write("market-watch", good)
    unit = loam_cron.unit_base("testagent", "market-watch")
    check("unit name round-trips through the roster",
          loam_cron.split_unit(f"{unit}.timer", ["testagent"])
          == ("testagent", "market-watch"))
    try:
        loam_cron.split_unit("loam-cron-nobody-x.timer", ["testagent"])
        check("an orphan unit is a refusal, not a guess", False)
    except loam_cron.CronError:
        check("an orphan unit is a refusal, not a guess", True)

    check("a legacy approved record is live", cron.live)
finally:
    loam_cron.AI_LABS = real_root
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
