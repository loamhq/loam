"""Read and render each schedule from crons/<name>.md.

A record comes in three shapes, all under ``crons/``:

    ---                                 ---
    schedule: daily at 05:00 PT         at: 2026-10-20 14:30 PT
    enabled: true                       note: did the PR merge?
    reason: moved earlier for ...       enabled: true
    ---                                 reason: ...
                                        ---
    ...the instruction that loads...    ...optional instruction...

    recurring   ``schedule:`` in the grammar below. Body required.
    one-off     ``at:`` a PT date and minute, and ``note:`` saying why. Fires
                once; ``bin/wake`` then writes ``done:`` into the header and
                the reconciler removes its timer.
    reflection  ``crons/reflection.md`` with ``lane: reflection``: the nightly
                wake's time, switch and instruction. Its timer is
                ``loam-wake-<agent>.timer``.

A legacy approved date implies enabled when the enabled field is absent.
The reconciler validates changes and renders timers from accepted records.
The lane file is subject to the auto-loaded context size limit.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

# Append this path so a job's checkout keeps precedence over installed modules.
sys.path.append(str(Path(__file__).resolve().parent))

import loam_config as lc  # noqa: E402
import loam_engineer  # noqa: E402
from loam_lock import WAIT_SECONDS  # noqa: E402
from loam_memory import (  # noqa: E402
    CRON_LANE_DIR,
    front_matter,
    strip_frontmatter,
)

AI_LABS = lc.WORKSPACES
AGENTS = lc.ROOT
WAKE_BIN = AGENTS / "bin" / "wake"
UNIT_DIR = lc.USER_UNIT_DIR
VAULT = lc.VAULT

UNIT_PREFIX = "loam-cron-"

# Write the time zone into OnCalendar so systemd honors the schedule when the host
# uses another zone.
TZ = lc.TIMEZONE

# Render the schedule page inside the install's configured vault location
# (System/schedule.md on standard installs, or <projects>/Loam/infrastructure/schedule.md
# on legacy custom vault.projects layouts).
SCHEDULE_PAGE = lc.schedule_page()

DAYS = {
    "monday": "Mon", "tuesday": "Tue", "wednesday": "Wed",
    "thursday": "Thu", "friday": "Fri", "saturday": "Sat", "sunday": "Sun",
}
DAY_ORDER = list(DAYS.values())

# The whole grammar, in one string, which is also what the agents are told in
# their config clause and what an installer refusal quotes back. One place.
SCHEDULE_GRAMMAR = f"""\
  <when> at <HH:MM> {lc.ZONE_TOKEN}

  <when> is one of:
    daily
    weekdays                     Monday through Friday
    weekends                     Saturday and Sunday
    <Day>s                       e.g. Sundays
    <Day>s and <Day>s            e.g. Mondays and Thursdays

  The time is 24-hour, zero-padded. The zone is this install's,
  {lc.ZONE_TOKEN} ({lc.TIMEZONE}), or another spelled out (UTC,
  Europe/Berlin)."""

# `<when> at HH:MM PT`. The time is matched from the END so a `<when>` may
# contain the word "at" without ambiguity, and so the colon in the time is
# never mistaken for the frontmatter separator.
SCHEDULE_RE = re.compile(
    r"^(?P<when>.+?)\s+at\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<zone>[A-Za-z][A-Za-z0-9_/+-]*)$",
    re.IGNORECASE,
)

# A cron name has to be a safe systemd unit fragment and a safe filename, and
# it is read by a human in `list-timers`. Kebab case, nothing clever.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")

# A one-off's moment: `2026-10-20 14:30 PT`. Minute precision, PT only, the
# same zone rule as the recurring grammar.
AT_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<zone>[A-Za-z][A-Za-z0-9_/+-]*)$",
    re.IGNORECASE,
)

# The reflection's record. One name, reserved: `wake --cron reflection` would
# otherwise be a cron lane that shadows the reflection lane's own file.
REFLECTION = "reflection"

# Use the record body for the daily wake. New records and old placeholder bodies take
# the default instruction.
def reflection_default() -> str:
    return (f"This is your daily wake. Use it to keep learning from "
            f"{lc.OWNER_NAME}'s life and what is happening, to keep your "
            f"workspace up, and to do what might need doing; or do nothing, "
            f"which is a real use of it too. You have what a conversation "
            f"has: the whole box, subagents and the engineer included.\n")


def reflection_instruction(text: str | None) -> str:
    """Read the wake instructions from its schedule record."""
    return strip_frontmatter(text or "").strip() or reflection_default().strip()

# Reject unknown header keys so an ignored setting cannot appear to have taken effect.
HEADER_KEYS = ("lane", "schedule", "at", "note", "enabled", "reason", "done",
               "approved")


def pt_zone(zone: str | None = None):
    """The zone a schedule is read in: its own token's, or the install's."""
    from zoneinfo import ZoneInfo  # noqa: PLC0415
    return ZoneInfo(zone or TZ)


def _zone(match: "re.Match", raw: str) -> str:
    zone = lc.zone_of(match["zone"])
    if zone is None:
        raise CronError(f"{raw!r} ends in {match['zone']!r}, which is not a "
                        f"time zone. The grammar is:\n\n{SCHEDULE_GRAMMAR}")
    return zone


# Run each lane until it finishes without a unit time limit.
def lane_timeout_s(agent: str, lane: str) -> None:
    """Every wake lane runs without a time limit."""
    return None


def unit_timeout_s(agent: str, lane: str) -> str:
    """The service's TimeoutStartSec: `infinity`, the same for every lane."""
    return "infinity"


class CronError(Exception):
    """The file does not define an installable cron. Always refuse.

    ``key`` is the header key the fault is in, when it is in one, so a page
    can point at the line (loam_schedule.problem); None for the file whole."""

    def __init__(self, message: str, key: str | None = None):
        super().__init__(message)
        self.key = key


@dataclass(frozen=True)
class Cron:
    agent: str
    display: str
    name: str
    workspace: Path
    path: Path
    schedule_text: str      # exactly as the file wrote it (`at:` for a one-off)
    oncalendar: str         # what the timer will carry
    enabled: bool
    body_chars: int
    kind: str = "recurring"            # recurring | one-off | reflection
    at: datetime | None = None         # a one-off's moment, PT-aware
    note: str = ""
    done: str = ""                     # a one-off's `done:` once it has fired
    reason: str = ""

    @property
    def unit(self) -> str:
        if self.kind == "reflection":
            return f"loam-wake-{self.agent}"
        return unit_base(self.agent, self.name)

    @property
    def live(self) -> bool:
        """Should a timer stand for this record right now?"""
        return self.enabled and not self.done


def unit_base(agent: str, name: str) -> str:
    return f"{UNIT_PREFIX}{agent}-{name}"


def roster() -> list[str]:
    """Derived, never listed — the same rule as every other generator here."""
    return sorted(
        d.name for d in AI_LABS.iterdir()
        if d.is_dir() and (d / "agent.json").is_file()
    )


def split_unit(unit: str, names: list[str] | None = None) -> tuple[str, str]:
    """Split the unit at the longest matching roster ID because both parts may contain
    dashes. For example, with agent-a on the roster,
    loam-cron-agent-a-weekly-review.timer gives ("agent-a", "weekly-review"). An orphan
    is a finding.
    """
    rest = unit.removesuffix(".timer").removesuffix(".service")
    if not rest.startswith(UNIT_PREFIX):
        raise CronError(f"{unit} is not a Loam cron unit")
    rest = rest[len(UNIT_PREFIX):]
    for agent in sorted(names if names is not None else roster(), key=len,
                        reverse=True):
        if rest.startswith(f"{agent}-"):
            return agent, rest[len(agent) + 1:]
    raise CronError(f"{unit} names no agent on the roster")


def parse_schedule(text: str) -> tuple[str, str]:
    """``"daily at 05:00 PT"`` -> ``("daily at 05:00 PT", OnCalendar spec)``.

    Small on purpose. Every shape the grid actually needs, and nothing that
    would let a file express a schedule nobody can read back out of it.
    """
    raw = " ".join((text or "").split())
    match = SCHEDULE_RE.match(raw)
    if not match:
        raise CronError(
            f"cannot parse schedule {raw!r}. The grammar is:\n\n"
            f"{SCHEDULE_GRAMMAR}"
        )
    hour, minute = int(match["hour"]), int(match["minute"])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise CronError(f"{raw!r} names an impossible time of day")
    zone = _zone(match, raw)

    when = match["when"].strip().lower()
    if when in ("daily", "every day"):
        prefix = ""
    elif when == "weekdays":
        prefix = "Mon..Fri "
    elif when == "weekends":
        prefix = "Sat,Sun "
    else:
        tokens = [t.strip() for t in re.split(r",|\band\b", when) if t.strip()]
        days = []
        for token in tokens:
            day = DAYS.get(token.rstrip("s"))
            if day is None:
                raise CronError(
                    f"cannot parse {token!r} in schedule {raw!r}. "
                    f"The grammar is:\n\n{SCHEDULE_GRAMMAR}"
                )
            if day not in days:
                days.append(day)
        if not days:
            raise CronError(f"schedule {raw!r} names no days")
        days.sort(key=DAY_ORDER.index)
        prefix = ",".join(days) + " "

    return raw, f"{prefix}*-*-* {hour:02d}:{minute:02d}:00 {zone}"


def assert_systemd_parses(oncalendar: str) -> str:
    """Belt to our own parser: make systemd agree before anything is written.

    Our grammar is the narrow one and systemd's is the wide one, so this can
    only ever catch a rendering bug — which is precisely the bug that would
    otherwise be discovered by a timer silently never firing.
    """
    result = subprocess.run(
        ["systemd-analyze", "calendar", oncalendar],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise CronError(
            f"systemd cannot parse the rendered calendar spec "
            f"{oncalendar!r}: {(result.stderr or '').strip()}"
        )
    for line in result.stdout.splitlines():
        if line.strip().startswith("Next elapse:"):
            return line.split(":", 1)[1].strip()
    return ""


def parse_at(text: str) -> tuple[str, str, datetime]:
    """``"2026-10-20 14:30 PT"`` -> (raw, OnCalendar spec, aware datetime)."""
    raw = " ".join((text or "").split())
    match = AT_RE.match(raw)
    if not match:
        raise CronError(
            f"cannot parse one-off time {raw!r}. It is `YYYY-MM-DD HH:MM "
            f"{lc.ZONE_TOKEN}`, 24-hour, e.g. `at: 2026-10-20 14:30 {lc.ZONE_TOKEN}`.")
    zone = _zone(match, raw)
    try:
        day = date.fromisoformat(match["date"])
        moment = datetime(day.year, day.month, day.day, int(match["hour"]),
                          int(match["minute"]), tzinfo=pt_zone(zone))
    except ValueError as exc:
        raise CronError(f"{raw!r} names an impossible date or time") from exc
    return (raw, f"{moment:%Y-%m-%d %H:%M}:00 {zone}", moment)


def parse_enabled(header: dict[str, str]) -> bool:
    """`enabled:` decides. A legacy `approved: <date>` with no `enabled:` is
    enabled: it was live under the old rule and must not stop at the switch."""
    raw = header.get("enabled")
    if raw is None:
        return bool(header.get("approved"))
    value = raw.strip().lower()
    if value in ("true", "yes", "on"):
        return True
    if value in ("false", "no", "off"):
        return False
    raise CronError(f"`enabled: {raw}` is neither true nor false",
                    key="enabled")


def cron_path(workspace: Path, name: str) -> Path:
    return workspace / CRON_LANE_DIR / f"{name}.md"


def read_cron(agent: str, name: str) -> Cron:
    """The file, fully validated, or CronError saying exactly what is wrong."""
    if not NAME_RE.match(name or ""):
        raise CronError(
            f"{name!r} is not a usable cron name. Lower-case letters, digits "
            f"and dashes; it becomes a filename and a systemd unit name."
        )
    workspace = AI_LABS / agent
    if not (workspace / "agent.json").is_file():
        raise CronError(f"{agent!r} is not an agent on this box")

    path = cron_path(workspace, name)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CronError(f"no cron file at {path}: {exc}") from exc
    return parse_record(agent, name, text)


def parse_record(agent: str, name: str, text: str,
                 check_systemd: bool = True) -> Cron:
    """A record's text, fully validated as a shape, or CronError. Says
    nothing about who may write it or when -- that is loam_schedule's."""
    if not NAME_RE.match(name or ""):
        raise CronError(f"{name!r} is not a usable cron name")
    workspace = AI_LABS / agent
    path = cron_path(workspace, name)
    header = front_matter(text)
    unknown = sorted(set(header) - set(HEADER_KEYS))
    if unknown:
        raise CronError(
            f"{path} carries header key(s) a record does not have: "
            f"{', '.join(unknown)}. The keys are {', '.join(HEADER_KEYS[:-1])}"
            f". Tool grants are not a record's to carry: they are {lc.OWNER_NAME}'s, "
            f"in lib/loam_engineer.py.", key=unknown[0])
    # Size checks report overlong instructions separately from validating or running the
    # record.
    enabled = parse_enabled(header)
    body = strip_frontmatter(text).strip()
    lane = header.get("lane", "").strip().lower()
    if (lane == REFLECTION) != (name == REFLECTION):
        raise CronError(
            f"`crons/{REFLECTION}.md` and `lane: {REFLECTION}` go together: "
            f"the name is reserved for the nightly reflection's record",
            key="lane")
    if lane and lane != REFLECTION:
        raise CronError(f"`lane: {lane}` is not a lane a record can name",
                        key="lane")
    if header.get("schedule") and header.get("at"):
        raise CronError(f"{path} has both `schedule:` and `at:`; a record is "
                        f"recurring or one-off, not both", key="at")

    common = dict(agent=agent, display=agent.capitalize(), name=name,
                  workspace=workspace, path=path, enabled=enabled,
                  body_chars=len(body), reason=header.get("reason", ""))
    if header.get("at"):
        raw, oncalendar, moment = _keyed(parse_at, header["at"], "at")
        note = header.get("note", "").strip()
        if not note:
            raise CronError(f"{path} is a one-off with no `note:`. A one-off "
                            f"carries a short note of why it was set.",
                            key="note")
        if check_systemd:
            assert_systemd_parses(oncalendar)
        return Cron(schedule_text=raw, oncalendar=oncalendar, kind="one-off",
                    at=moment, note=note, done=header.get("done", ""),
                    **common)

    schedule_text, oncalendar = _keyed(parse_schedule,
                                       header.get("schedule", ""), "schedule")
    if check_systemd:
        assert_systemd_parses(oncalendar)
    if lane == REFLECTION:
        return Cron(schedule_text=schedule_text, oncalendar=oncalendar,
                    kind="reflection", **common)
    if not body:
        raise CronError(
            f"{path} has a header but no body. The body is the instruction "
            f"that loads when the cron runs; a cron with nothing to do is not "
            f"a cron."
        )
    return Cron(schedule_text=schedule_text, oncalendar=oncalendar, **common)


def _keyed(parse, raw: str, key: str):
    """``parse(raw)``, its CronError marked as being about ``key``."""
    try:
        return parse(raw)
    except CronError as exc:
        exc.key = exc.key or key
        raise


def proposals(names: list[str] | None = None) -> list[tuple[str, str, Path]]:
    """Every cron file on the box, installed or not. (agent, name, path)."""
    out = []
    for agent in (names if names is not None else roster()):
        folder = AI_LABS / agent / CRON_LANE_DIR
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.md")):
            out.append((agent, path.stem, path))
    return out


def installed(names: list[str] | None = None) -> list[tuple[str, str]]:
    """Every installed cron, read off the units. (agent, name), sorted."""
    out = []
    for timer in sorted(UNIT_DIR.glob(f"{UNIT_PREFIX}*.timer")):
        try:
            out.append(split_unit(timer.name, names))
        except CronError:
            continue
    return out


# ---------------------------------------------------------------------------
# The units. One pair per cron, rendered from the file and nothing else.
# ---------------------------------------------------------------------------

def render_units(cron: Cron) -> tuple[str, str]:
    """Render the service and timer together. A reflection runs bin/wake on the workspace.
    Other crons pass their name with --cron.
    """
    if cron.kind == "reflection":
        return reflection_service(cron), reflection_timer(cron.agent,
                                                          cron.schedule_text)
    header = (
        f"# Generated by agents/lib/loam_schedule.py (the reconciler) from\n"
        f"#   {cron.path}\n"
        f"# That file is the definition. Do not edit this unit: edit the file\n"
        f"# and the reconciler re-cuts it within a minute.\n"
    )
    service = f"""\
{header}
[Unit]
Description=Loam cron `{cron.name}` for {cron.display} ({cron.schedule_text})
# Same handler as the reflection wakes. A run killed by the kernel cannot
# record its own death, so the report comes from outside the dying process --
# and bin/wake-failed reads the unit name to learn which lane died.
OnFailure=loam-wake-failed@%N.service

[Service]
Type=oneshot
# Identical to the reflection wake's: no memory ceiling (2026-11-16; MemoryHigh
# 1G / MemoryMax 1536M until then), and the agents' place in the kernel's OOM
# order (lib/loam_config.py), so a runaway run goes before Loam's core.
OOMScoreAdjust={lc.AGENT_OOM_SCORE_ADJ}
CPUWeight={lc.BACKGROUND_CPU_WEIGHT}
Nice={lc.BACKGROUND_NICE}
IOSchedulingClass={lc.BACKGROUND_IO_CLASS}
# Pinned, not drifting. The CLI updated itself once with nobody choosing it;
# upgrades are an operator decision taken with the changelog open.
Environment=DISABLE_AUTOUPDATER=1
# And Antigravity's own updater off (lib/loam_pins.py: the CLIs stay at
# the release's pin; the variable is read only as `true`).
Environment=AGY_CLI_DISABLE_AUTO_UPDATE=true
Environment=PATH={lc.UNIT_PATH}
EnvironmentFile=-{lc.WAKE_ENV}
WorkingDirectory={cron.workspace}
ExecStart={WAKE_BIN} {cron.workspace} --cron {cron.name}
# No clock (since 2026-11-10): a run goes until it is done, an engineer job
# it briefs and waits on included, and systemd never times it out.
TimeoutStartSec={unit_timeout_s(cron.agent, f"cron:{cron.name}")}

# Deliberately no [Install] section. A cron is started by its timer and by
# nothing else -- a oneshot service wanted by default.target would run once at
# every boot, unattended, off the clock.
"""
    once = (
        "# A one-off: it fires once, to the second, and is then marked done\n"
        "# and this timer removed.\nAccuracySec=1s\n"
        if cron.kind == "one-off" else "")
    timer = f"""\
{header}
[Unit]
Description=Loam cron `{cron.name}` for {cron.display} — {cron.schedule_text}

[Timer]
OnCalendar={cron.oncalendar}
{once}# No catch-up. A cron that missed its window because the box was down should
# wait for its next one, not fire into the middle of the morning.
Persistent=false
Unit={cron.unit}.service

[Install]
WantedBy=timers.target
"""
    return service, timer


def reflection_service(cron: Cron) -> str:
    """Render the reflection's wake command in a timer-started service. Leave out [Install]
    so enabling the timer cannot also schedule a boot wake.
    """
    return f"""\
# Generated by agents/lib/loam_schedule.py (the reconciler) from
#   {cron.path}
# That file is the definition. Do not edit this unit: edit the file
# and the reconciler re-cuts it within a minute.

[Unit]
Description=Loam reflection wake for {cron.display} ({cron.schedule_text})
# A run killed by the kernel cannot record its own death, so the report comes
# from outside the dying process; bin/wake-failed reads the unit name.
OnFailure=loam-wake-failed@%N.service

[Service]
Type=oneshot
# No memory ceiling (2026-11-16), and the agents' place in the kernel's OOM
# order (lib/loam_config.py), so a runaway run goes before Loam's core.
OOMScoreAdjust={lc.AGENT_OOM_SCORE_ADJ}
CPUWeight={lc.BACKGROUND_CPU_WEIGHT}
Nice={lc.BACKGROUND_NICE}
IOSchedulingClass={lc.BACKGROUND_IO_CLASS}
# Pinned, not drifting: upgrades are an operator decision. Antigravity's own
# updater off too (lib/loam_pins.py; the variable is read only as `true`).
Environment=DISABLE_AUTOUPDATER=1
Environment=AGY_CLI_DISABLE_AUTO_UPDATE=true
Environment=PATH={lc.UNIT_PATH}
EnvironmentFile=-{lc.WAKE_ENV}
WorkingDirectory={cron.workspace}
ExecStart={WAKE_BIN} {cron.workspace}
# No clock (since 2026-11-10): a run goes until it is done.
TimeoutStartSec={unit_timeout_s(cron.agent, REFLECTION)}
"""


def reflection_timer(agent: str, schedule_text: str) -> str:
    """Render the reflection timer with its schedule's time zone on the calendar line."""
    raw, spec = parse_schedule(schedule_text)
    hhmm = spec.split(" ")[1][:5]
    if spec.startswith("*-*-*"):
        title = (f"Daily reflection wake for {agent.capitalize()} at {hhmm} "
                 f"{lc.ZONE_WORD}")
    else:
        title = f"Reflection wake for {agent.capitalize()}, {raw}"
    return f"""[Unit]
Description={title}

[Timer]
OnCalendar={spec}
Persistent=false
Unit=loam-wake-{agent}.service

[Install]
WantedBy=timers.target
"""


def reflection_hhmm(schedule_text: str) -> str:
    """The HH:MM a reflection schedule fires at, as agent.json carries it."""
    return parse_schedule(schedule_text)[1].rsplit(" ", 2)[1][:5]


# ---------------------------------------------------------------------------
# The schedule page. One table, in the vault, so "I am aware of them" stays
# true without opening a terminal.
# ---------------------------------------------------------------------------

PAGE_TEMPLATE = """\
---
type: infrastructure
infrastructure_for: schedule
generated_by: lib/loam_cron.py
generated_at: {when}
---
# Schedule

Everything that runs on this server unattended, per agent. **Generated — do not
edit by hand.** It is rewritten automatically by Loam's schedule reconciler
whenever a schedule changes, and by `bin/provision`; an edit here changes
nothing. View and edit each agent's schedules in **Settings › Agents**.{companion}

{table}

Times are {zone_word}. Every row is a record under `{workspaces}/<agent>/crons/`,
the daily reflection included (`crons/reflection.md`), and a record is live
while it says `enabled: true`: the reconciler installs, moves, pauses and
removes its timer within a minute of the record changing. Every change and
every fire is recorded in `{events_path}`. The review lane (when the reviewer
plugin is on) reads one agent's workspace each night, least recently reviewed
first, and leaves a note in that agent's commons inbox.

## Paused, refused or done

{pending}

## Invariants

- **One run per agent at a time.** Reflection and cron alike take that agent's
  run lock for the whole run. A launch that finds it held waits 20 minutes and
  then skips, recording a line in [[loam-wake-failures]]. It never kills the
  holder.
- **No count of runs across the box.** A run starts when the box has the
  memory for it (256 MB plus 12% of RAM kept free on servers under 16 GB,
  or 2 GB / 12% on 16 GB+, as the session broker admits a chat); one that
  finds no room waits 20 minutes and then skips, the same way. No run has
  a wall clock or a round-trip cap.
- Every run loads the agent's soul, config and map when present. A cron adds
  its own instruction file.
- **Every persona wakes once a day**, by its reflection record: its body is
  what the wake is told (Loam's default to start with), and only the owner
  turns it off. The persona moves its time and keeps any other schedules.
"""


def schedule_page(vault: Path = VAULT, names: list[str] | None = None,
                  stamp: str | None = None) -> str:
    names = names if names is not None else roster()
    when = stamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    ws_name = AI_LABS.name
    # The page shows what the box will do, so a row is a timer that stands
    # (read off the unit directory) described by its record; a record with no
    # timer is listed below the table with why.
    rows: list[tuple[str, ...]] = []
    elsewhere: list[str] = []
    for agent in names:
        config = json.loads(
            (AI_LABS / agent / "agent.json").read_text(encoding="utf-8"))
        display = (config.get("display") or "").strip() or agent.capitalize()
        for cron_agent, cron_name, path in proposals([agent]):
            rel = f"`{ws_name}/{cron_agent}/crons/{cron_name}.md`"
            try:
                cron = read_cron(cron_agent, cron_name)
            except CronError as exc:
                # A record that no longer parses is a real state of the box
                # and the page says so. check-drift is where it is chased.
                elsewhere.append(f"- {rel} \u2014 **unreadable**: "
                                 + str(exc).split("\n")[0])
                continue
            timer = UNIT_DIR / f"{cron.unit}.timer"
            lane = ("reflection" if cron.kind == "reflection"
                    else f"{'one-off' if cron.at else 'cron'} `{cron_name}`")
            if cron.live and timer.is_file():
                rows.append((display, lane, cron.schedule_text,
                             f"`{cron.unit}.timer`", rel))
            else:
                why = (f"done {cron.done}" if cron.done
                       else "paused" if not cron.enabled
                       else "enabled, timer not yet installed")
                elsewhere.append(f"- {rel} \u2014 {lane}, {cron.schedule_text}"
                                 f": {why}")

    # Include the installed reviewer's schedule alongside the persona schedules on the
    # page.
    reviewer_timer = UNIT_DIR / "loam-plugin-reviewer--nightly.timer"
    if reviewer_timer.is_file():
        when_text = "nightly"
        for line in reviewer_timer.read_text(encoding="utf-8").splitlines():
            if line.startswith("OnCalendar="):
                m = re.search(r"(\d{2}:\d{2}):\d{2}\b", line)
                if m:
                    when_text = f"daily at {m.group(1)} {lc.ZONE_TOKEN}"
        rows.append((
            "Reviewer", "review", when_text,
            "`loam-plugin-reviewer--nightly.timer`",
            "`plugins/reviewer/instructions.md`",
        ))

    table = [
        f"| Agent | Lane | When ({lc.ZONE_WORD}) | Timer | Definition |",
        "| :---- | :--- | :------------- | :---- | :--------- |",
    ]
    table += ["| " + " | ".join(row) + " |" for row in rows]

    pending = "\n".join(elsewhere) or "_None._"
    page_parent = (vault / SCHEDULE_PAGE).parent
    companion = " Companion to [[agent-runtime]]." if (page_parent / "agent-runtime.md").is_file() else ""
    events_path = str(lc.STATE / "schedules" / "events.jsonl")

    return PAGE_TEMPLATE.format(
        when=when, table="\n".join(table), pending=pending,
        zone_word=lc.ZONE_WORD, workspaces=ws_name,
        events_path=events_path, companion=companion,
    )


def _without_stamp(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines()
        if not line.startswith("generated_at:")
    )


def write_schedule_page(vault: Path = VAULT, names: list[str] | None = None) -> bool:
    """Rewrite the page. True if it changed. Never fails a caller.

    The timestamp alone is not a change. Without this the page would be
    rewritten on every installer run whatever the outcome, and since the vault
    is auto-committed that would put a commit in the history saying nothing
    happened -- which is the same defect as a generator that rewrites identical
    units and invites a needless daemon-reload.
    """
    path = vault / SCHEDULE_PAGE
    try:
        content = schedule_page(vault, names)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and _without_stamp(
                path.read_text(encoding="utf-8")) == _without_stamp(content):
            return False
        path.write_text(content, encoding="utf-8")
        return True
    except OSError as exc:
        print(f"could not write {path}: {exc}", file=sys.stderr)
        return False
