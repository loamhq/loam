"""The one check on a clock: a fixed list of facts about Loam, read every
quarter hour, with no judgment in it.

Decided 2026-11-18 (batch B, "knowing it works"; step 6 of the engineer's
plan): one small program on one timer is the only thing in Loam that watches
Loam on a clock (``bin/loam-check``, ``loam-check.timer``). It replaced the
separate watchers on 2026-11-19: drift-watch and its nightly lane checks,
check-memory, the minute sampler's own telling, and the sign-in watch.

**What it does.** It reads every fact below and writes one state file,
``<state>/check.json`` (``STATE_FILE``). When every fact is true that is all
it does: no run, no cost, nothing told, nothing posted. When a fact is false,
or cannot be read, the fact is handed to the agent that hears when Loam needs
attention (``loam_attention.tell``, lib/loam_attention.py), keyed by the
fact's id. That seam's own rule against flooding (told once per key, again
only after three quiet days) is the only rate rule; this file has none.

**A fact that cannot be read is not true.** A fact whose reading raised is
recorded as ``ok: null`` with what went wrong, and told as a fact of its own
("could not read whether ..."). If the program itself crashes, the state
file says so (``crashed``) and that too is told. The unit exits non-zero.

**The facts.** Each is a plain sentence a persona can act on, with where to
look:

- ``reflection:<agent>``: every agent that is not archived had its daily
  reflection complete in the last ``REFLECTION_HOURS`` hours (its ledger,
  ``logs/wake-<agent>.jsonl``). An agent whose record was written in that
  window (a new agent, a time the owner moved) is not yet owed one, and a
  reflection the owner turned off is not owed.
- ``broker``: the session host answers ``/up``. Asked twice, ``BROKER_RETRY_S``
  apart, so a planned restart's few seconds are not a fact.
- ``login:<provider>``: no provider login an agent runs on is gone, expired,
  or within ``LOGIN_WARN_DAYS`` days of expiring (``login_state``, the same
  reading the app's Providers page draws).
- ``hears``: an agent is awake to hear. With every agent archived, a fact
  reaches nobody; the state file says so for the app (step 7).
- ``memory``: no memory pressure episode is under way (``<state>/pressure.json``,
  written each minute by ``bin/pressure``, lib/loam_pressure.py).
- ``memsample``: the box's memory history is still being written
  (``logs/memsample.jsonl``; it once stopped for six weeks unnoticed).
- ``disk:<mount>``: each mount Loam writes to has at least ``DISK_FREE_PCT``
  percent and ``DISK_FREE_GB`` GB free.
- ``unit:<unit>``: no unit of Loam's own machinery is failed: a unit this
  repository ships (``systemd/``, ``systemd/system/``) or a plugin's
  (``loam-plugin-*``: the off-box backup's push, the reviewer's night).
  A persona's own runs (wakes, crons, results), engineer jobs, and the
  one-off transient units a job arms with ``systemd-run`` are left out: a
  persona's failed run is told to that persona at its next turn, a job's to
  whoever briefed it, and a one-off's to the job that armed it.
- ``github:...``: the GitHub integration's refresh token is not within
  fourteen days of its expiry and its installation is not gone, read from
  its store as the nightly housekeeping's call left it
  (``loam_github_app.nightly_warnings``). Not connected is silence.

Once a day, at the first tick at or after ``DAILY_AT`` local (and on the
first run of a new install), and their result kept until the next day:

- ``drift:<finding>``: ``bin/check-drift`` finds the generators and the box
  in step. Each finding is its own fact. Its whole output is appended to
  ``logs/drift-check.log``.
- ``lane:<agent>:<problem>``: ``bin/lane-check`` finds nothing wrong with
  each agent's auto-loaded files and the model its last session ran on.

**Whether agents can run.** A live agent needs a usable provider. The
session host checks the freshness of this file, then reads the current
roster, logins and recent scheduled outcomes so recovery needs no new tick.
``conversations`` also depends on whether the broker answers.

Stdlib and the runtime's lib only. Every reading is wrapped, so one fact
that breaks costs that fact, never the others.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import loam_config as lc

STATE_FILE = Path(os.environ.get("LOAM_CHECK_STATE") or lc.STATE / "check.json")
BY = "loam-check"
PERIOD_S = 15 * 60
# Two periods and a margin: one late tick is not a stopped check.
STALE_S = 2 * PERIOD_S + 120
DAILY_AT = "01:40"
REFLECTION_HOURS = 26
BROKER_RETRY_S = 30
LOGIN_WARN_DAYS = 3
REFUSAL_HOURS = 26
DISK_FREE_PCT = 10
DISK_FREE_GB = 4
MEMSAMPLE_STALE_MIN = 20
PRESSURE_STALE_MIN = 10
DRIFT_TIMEOUT_S = 300
LANE_TIMEOUT_S = 120
DRIFT_LOG = lc.LOGS / "drift-check.log"
# This file's own tree's tools: run from a job's worktree, the branch's.
# Live, the two are one path.
TREE = Path(__file__).resolve().parent.parent
# A persona's own runs and engineer jobs: told elsewhere (see the docstring).
NOT_MACHINERY = ("loam-wake-", "loam-cron-", "loam-results-", "loam-engineer-")


def now() -> float:
    return time.time()


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text) -> float | None:
    try:
        return datetime.strptime(str(text)[:19], "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def fact(fid: str, ok: bool | None, says: str, evidence: str | None = None,
         blocks: str | None = None) -> dict:
    """One fact. ``says`` is the sentence a persona reads: what is wrong when
    ``ok`` is false, what could not be read when it is None, what is true
    when it is true. ``blocks`` is ``conversations`` when this fact being
    false stops conversations independently of agent availability."""
    return {"id": fid, "ok": ok, "says": says, "evidence": evidence, "blocks": blocks}


def _run(argv: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                          check=False)


# ---------------------------------------------------------------------------
# The facts. Each returns a list of facts and may raise; ``read`` wraps it.
# ---------------------------------------------------------------------------

def agents() -> list[str]:
    import loam_broker  # noqa: PLC0415
    return loam_broker.roster()


def _config(agent: str) -> dict:
    return json.loads((lc.WORKSPACES / agent / "agent.json").read_text(encoding="utf-8"))


def _tail_rows(path: Path, size: int = 512 * 1024) -> list[dict]:
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        end = handle.tell()
        handle.seek(max(0, end - size))
        data = handle.read().decode("utf-8", "replace")
    rows = []
    for line in data.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def reflections(at: float, names: list[str] | None = None) -> list[dict]:
    out = []
    window = REFLECTION_HOURS * 3600
    for agent in (agents() if names is None else names):
        record = lc.WORKSPACES / agent / "crons" / "reflection.md"
        ledger = lc.LOGS / f"wake-{agent}.jsonl"
        evidence = f"`{ledger}`"
        try:
            text = record.read_text(encoding="utf-8")
        except FileNotFoundError:
            text = None
        if text is not None and re.search(r"(?m)^enabled:\s*false\s*$", text):
            out.append(fact(f"reflection:{agent}", True,
                            f"{agent}'s daily reflection is turned off by the owner",
                            f"`{record}`"))
            continue
        try:
            rows = [r for r in _tail_rows(ledger) if r.get("lane") == "reflection"]
        except FileNotFoundError:
            rows = []
        ended = [parse_iso(r.get("finished") or r.get("ts")) or 0 for r in rows if r.get("ok")]
        last_ok = max(ended, default=0)
        if at - last_ok < window:
            out.append(fact(f"reflection:{agent}", True,
                            f"{agent}'s daily reflection completed at {iso(last_ok)}",
                            evidence))
            continue
        try:
            fresh = at - record.stat().st_mtime < window
        except OSError:
            fresh = False
        if fresh:
            out.append(fact(f"reflection:{agent}", True,
                            f"{agent}'s reflection record is under {REFLECTION_HOURS} "
                            f"hours old; no reflection is owed yet", f"`{record}`"))
            continue
        last = rows[-1] if rows else None
        tail = ""
        if last and not last.get("ok"):
            why = last.get("stage") or last.get("error") or last.get("detail") or "no reason recorded"
            tail = f" Its last try, at {last.get('ts')}, failed ({str(why)[:160]})."
        out.append(fact(
            f"reflection:{agent}", False,
            f"{agent}'s daily reflection has not completed in the last "
            f"{REFLECTION_HOURS} hours (last completed: "
            f"{iso(last_ok) if last_ok else 'never'}).{tail}", evidence))
    return out


def broker(sleep: Callable[[float], None] = time.sleep) -> list[dict]:
    import loam_broker as brk  # noqa: PLC0415
    url = os.environ.get("LOAM_CHECK_BROKER_URL") or f"http://{brk.BIND_ADDR}:{brk.PORT}/up"
    error = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(url, timeout=5) as answer:
                if answer.status == 200:
                    return [fact("broker", True, "the session host answers", url)]
                error = f"HTTP {answer.status}"
        except Exception as exc:  # noqa: BLE001 -- any failure to answer is the fact
            error = f"{type(exc).__name__}: {exc}"[:200]
        if attempt == 0:
            sleep(BROKER_RETRY_S)
    return [fact("broker", False,
                 f"the session host (the broker) does not answer at {url} ({error}); "
                 f"conversations in the app cannot start, while scheduled runs still can",
                 "`journalctl --user -u loam-session-broker.service`",
                 blocks="conversations")]


def provider_logins(names: list[str]) -> dict:
    import loam_antigravity
    import loam_broker
    import loam_codex
    from loam_threads import default_engine

    readers = {"claude-code": loam_broker.login_state, "codex": loam_codex.login_state,
               "antigravity": loam_antigravity.login_state}
    states = {}
    for provider in sorted({default_engine(agent) for agent in names}):
        try:
            states[provider] = readers[provider]()
        except Exception as exc:  # a broken reading is local to one provider
            states[provider] = {"reason": type(exc).__name__}
    return states


def logins(states: dict) -> list[dict]:
    from loam_broker import PROVIDER_NAMES

    out = []
    page = "the app's Providers page"
    for provider, state in states.items():
        name = PROVIDER_NAMES[provider]
        days = state.get("days_left")
        if "connected" not in state:
            out.append(fact(f"login:{provider}", None,
                            f"the {name} login could not be read ({state['reason']})", page))
        elif not state["connected"]:
            out.append(fact(f"login:{provider}:gone", False,
                            f"the {name} login cannot be used: {state.get('reason') or 'not signed in'}; "
                            f"agents on {name} cannot run until someone signs in again",
                            page))
        elif isinstance(days, int) and days <= LOGIN_WARN_DAYS:
            out.append(fact(f"login:{provider}:expiring", False,
                            f"the {name} login expires in {days} day{'s' if days != 1 else ''} "
                            f"({state.get('expires_at')}); signing in again from the app renews it",
                            page))
        else:
            out.append(fact(f"login:{provider}", True,
                            f"the {name} login is good"
                            + (f" for {days} days" if isinstance(days, int) else ""), page))
    return out


def hears() -> list[dict]:
    import loam_attention  # noqa: PLC0415
    who = loam_attention.persona()
    if who:
        return [fact("hears", True, f"{who} hears when Loam needs attention",
                     "Settings › Agents")]
    return [fact("hears", False,
                 "no agent is awake to hear when Loam needs attention: every agent is "
                 "archived, so nothing that goes wrong reaches anyone",
                 "Settings › Agents")]


def memory(at: float) -> list[dict]:
    import loam_pressure  # noqa: PLC0415
    path = loam_pressure.PRESSURE
    state = json.loads(path.read_text(encoding="utf-8"))
    read_at = parse_iso(state.get("at")) or 0
    evidence = f"`{path}` (bin/pressure status)"
    if at - read_at > PRESSURE_STALE_MIN * 60:
        return [fact("memory:reading", False,
                     f"the memory pressure reading has not moved since {state.get('at')}; "
                     f"loam-pressure.timer is not running", evidence)]
    if state.get("episode_since"):
        says = state.get("episode_says") or (
            f"memory pressure on the box since {state['episode_since']}: RAM "
            f"{state.get('ram_pct')}% in use, swap {state.get('swap_free_pct')}% free")
        return [fact("memory", False, says, evidence)]
    return [fact("memory", True, f"no memory pressure (RAM {state.get('ram_pct')}% in use)",
                 evidence)]


def memsample(at: float) -> list[dict]:
    path = lc.LOGS / "memsample.jsonl"
    age = at - path.stat().st_mtime
    if age > MEMSAMPLE_STALE_MIN * 60:
        return [fact("memsample", False,
                     f"the box's memory history has not been written for {int(age // 60)} "
                     f"minutes; loam-memsample.timer is not running", f"`{path}`")]
    return [fact("memsample", True, "the box's memory history is being written", f"`{path}`")]


def disks() -> list[dict]:
    out, seen = [], set()
    for where in (Path("/"), lc.STATE, lc.VAULT, lc.WORKSPACES, lc.ROOT):
        try:
            dev = where.stat().st_dev
        except OSError:
            continue
        if dev in seen:
            continue
        seen.add(dev)
        usage = shutil.disk_usage(where)
        free_gb = usage.free / 1e9
        pct = 100 * usage.free / usage.total if usage.total else 100
        mount = _mount_of(where)
        if pct < DISK_FREE_PCT or free_gb < DISK_FREE_GB:
            out.append(fact(f"disk:{mount}", False,
                            f"the disk at {mount} is nearly full: {free_gb:.1f} GB free "
                            f"({pct:.0f}%)", f"`df -h {mount}`"))
        else:
            out.append(fact(f"disk:{mount}", True,
                            f"the disk at {mount} has {free_gb:.1f} GB free ({pct:.0f}%)",
                            f"`df -h {mount}`"))
    return out


def _mount_of(path: Path) -> str:
    path = path.resolve()
    while not os.path.ismount(path) and path != path.parent:
        path = path.parent
    return str(path)


def systemctl(*args: str) -> subprocess.CompletedProcess:
    return _run(["systemctl", *args], 30)


def machinery() -> set[str]:
    """The units this repository ships, user and system."""
    names = {p.name for p in (TREE / "systemd").glob("loam-*")}
    names |= {p.name for p in (TREE / "systemd" / "system").glob("loam-*")}
    return names


def is_machinery(unit: str, shipped: set[str]) -> bool:
    if unit.startswith(NOT_MACHINERY):
        return False
    return unit in shipped or unit.startswith("loam-plugin-")


def units(run: Callable[..., subprocess.CompletedProcess] = systemctl) -> list[dict]:
    out = []
    shipped = machinery()
    for scope in (["--user"], []):
        done = run(*scope, "list-units", "--failed", "--all", "--plain",
                   "--no-legend", "--no-pager", "loam-*")
        if done.returncode != 0:
            raise RuntimeError(f"systemctl {' '.join(scope + ['list-units'])}: "
                               f"{(done.stderr or '').strip()[:200]}")
        for line in (done.stdout or "").splitlines():
            unit = line.split()[0] if line.split() else ""
            if not unit or not is_machinery(unit, shipped):
                continue
            journal = f"`journalctl {' '.join(scope)} -u {unit}`".replace("  ", " ")
            out.append(fact(f"unit:{unit}", False,
                            f"{unit} has failed: its last run did not succeed", journal))
    if not out:
        out.append(fact("units", True, "no unit of Loam's machinery is failed",
                        "`systemctl --user list-units --failed 'loam-*'`"))
    return out


def github() -> list[dict]:
    import loam_attention  # noqa: PLC0415
    import loam_github_app as gh  # noqa: PLC0415
    warnings = gh.nightly_warnings(gh.public_view())
    return [fact(f"github:{loam_attention.key_of(w)}", False, w,
                 "the app's Integrations screen, GitHub") for w in warnings]


def drift() -> list[dict]:
    check = [sys.executable, str(TREE / "bin" / "check-drift"), "--quiet"]
    stamp = iso(now())
    evidence = f"`{DRIFT_LOG}`"
    try:
        done = _run(check, DRIFT_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log(DRIFT_LOG, stamp, f"loam-check: check-drift did not run: {exc}")
        return [fact("drift", None, f"check-drift did not run: {exc}", evidence)]
    _log(DRIFT_LOG, stamp, done.stdout + (f"\n[stderr]\n{done.stderr}" if done.stderr.strip() else "")
         + f"\n[exit {done.returncode}]")
    # A finding's heading is its identity; check-drift may print one twice
    # (its summary and its diff), which is one fact.
    found = list(dict.fromkeys(m.group(1).strip() for m in
                               re.finditer(r"^### (.+)$", done.stdout, re.MULTILINE)))
    if done.returncode == 0:
        return [fact("drift", True, "check-drift finds the generators and the box in step",
                     evidence)]
    if done.returncode == 1 and found:
        return [fact(f"drift:{item}", False, f"check-drift found drift: {item}", evidence)
                for item in found]
    last = (done.stderr.strip().splitlines() or ["no stderr"])[-1]
    return [fact("drift", None, f"check-drift failed (exit {done.returncode}): {last}",
                 evidence)]


def lanes(names: list[str] | None = None) -> list[dict]:
    import loam_attention  # noqa: PLC0415
    out = []
    for agent in (agents() if names is None else names):
        project = lc.WORKSPACES / agent
        argv = [sys.executable, str(TREE / "bin" / "lane-check"), "--json", str(project)]
        try:
            done = _run(argv, LANE_TIMEOUT_S)
            found = json.loads(done.stdout)
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            out.append(fact(f"lane:{agent}", None, f"lane-check of {agent} did not run: {exc}",
                            f"`bin/lane-check {project}`"))
            continue
        problems = list(dict.fromkeys(found.get("problems") or []))
        for problem in problems:
            out.append(fact(f"lane:{agent}:{loam_attention.key_of(problem)}", False,
                            f"{agent}: {problem}", f"`bin/lane-check {project}`"))
        if not problems:
            out.append(fact(f"lane:{agent}", True, f"{agent}'s auto-loaded files are sound",
                            f"`bin/lane-check {project}`"))
    return out


def _log(path: Path, stamp: str, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n{stamp}\n{text.rstrip()}\n")


# ---------------------------------------------------------------------------
# The tick.
# ---------------------------------------------------------------------------

def read(name: str, reader: Callable[[], list[dict]]) -> list[dict]:
    """A fact that raised is a fact that could not be read: ``ok: null``."""
    try:
        return reader()
    except Exception as exc:  # noqa: BLE001 -- never silently true
        return [fact(name, None, f"{type(exc).__name__}: {exc}"[:400],
                     f"`{STATE_FILE}`")]


def every_tick(at: float, states: dict) -> list[tuple[str, Callable[[], list[dict]]]]:
    return [("reflection", lambda: reflections(at)), ("broker", broker),
            ("login", lambda: logins(states)), ("hears", hears), ("memory", lambda: memory(at)),
            ("memsample", lambda: memsample(at)), ("disk", disks),
            ("units", units), ("github", github)]


def once_a_day() -> list[tuple[str, Callable[[], list[dict]]]]:
    return [("drift", drift), ("lane", lanes)]


def local_day(at: float) -> tuple[str, str]:
    """(date, HH:MM) on the install's clock."""
    from zoneinfo import ZoneInfo  # noqa: PLC0415
    local = datetime.fromtimestamp(at, ZoneInfo(lc.TIMEZONE))
    return local.strftime("%Y-%m-%d"), local.strftime("%H:%M")


def load_state(path: Path | None = None) -> dict:
    try:
        data = json.loads((path or STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(data: dict, path: Path | None = None) -> None:
    path = path or STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def refused_providers(at: float, names: list[str], providers: set[str]) -> set[str]:
    """Require two recent refusals with no later success between or after them.

    The bounded wake ledger tails already have the provider and outcome.
    A skipped lane or a local failure says nothing about the account.
    """
    if not providers:
        return set()
    outcomes: dict[str, list[tuple[float, bool]]] = {p: [] for p in providers}
    for agent in names:
        try:
            rows = _tail_rows(lc.LOGS / f"wake-{agent}.jsonl", size=32 * 1024)
        except OSError:
            continue
        for row in rows:
            provider = row.get("provider", "claude-code")
            refused = row.get("stage") == "account_refused"
            if provider not in outcomes or (not refused and row.get("ok") is not True):
                continue
            when = parse_iso(row.get("finished") or row.get("ts"))
            if when is not None and at - REFUSAL_HOURS * 3600 <= when <= at:
                outcomes[provider].append((when, refused))
    # A success wins a same-second tie: the order is not known that precisely.
    return {p for p, rows in outcomes.items()
            if [refused for _, refused in sorted(rows, key=lambda row: (-row[0], row[1]))[:2]]
            == [True, True]}


def run_status(at: float, names: list[str], states: dict) -> dict:
    from loam_agents import archived
    from loam_broker import PROVIDER_NAMES

    if not names:
        if archived():
            return {"agents_can_run": False, "why": ["Every agent is archived."],
                    "action": {"kind": "archived", "label": "Restore an agent"}}
        return {"agents_can_run": False, "why": ["There are no agents yet."],
                "action": {"kind": "agents", "label": "Open Agents"}}

    connected = {p for p, state in states.items() if state.get("connected")}
    refused = refused_providers(at, names, connected)
    if connected - refused:
        return {"agents_can_run": True, "why": [], "action": None}

    why, action = [], None
    for provider, state in states.items():
        name = "Claude" if provider == "claude-code" else PROVIDER_NAMES[provider]
        if state.get("connected") is False:
            reason = f"Sign in to {name} again"
            if action is None:
                action = {"kind": "provider", "provider": provider, "label": reason}
            why.append(reason)
        elif provider in refused:
            why.append(f"{name} refused its recent runs.")
        else:
            why.append(f"Loam couldn't read the {name} sign-in.")
    if action:
        why.remove(action["label"])
        why.insert(0, action["label"])
    return {"agents_can_run": False, "why": why, "action": action}


def current_run_status() -> dict:
    """The session host's answer. An old check can't vouch for this install."""
    at = now()
    state = load_state()
    if not state and not STATE_FILE.exists():
        # Step 8 seeds the hello thread before step 9 arms the check timer.
        # Until its first tick is due there is no observation of availability.
        import loam_starter
        starter = loam_starter.status()
        born = parse_iso(starter.get("at")) if isinstance(starter, dict) and starter.get("thread") else None
        if born is not None and 0 <= at - born < PERIOD_S:
            return {"agents_can_run": None, "why": [], "action": None}
    stale_after = parse_iso(state.get("stale_after"))
    if stale_after is None or at >= stale_after or state.get("crashed"):
        return {"agents_can_run": False, "why": ["Loam's own check has stopped."],
                "action": None}
    names = agents()
    return run_status(at, names, provider_logins(names))


def tell_all(facts: list[dict], tell: Callable[..., str] | None = None) -> dict:
    """Every false or unreadable fact, to the seam. ``{id: what tell said}``."""
    if tell is None:
        import loam_attention  # noqa: PLC0415
        tell = loam_attention.tell
    said = {}
    for f in facts:
        if f["ok"] is True:
            continue
        sentence = f["says"] if f["ok"] is False else \
            f"Loam's check could not read whether {f['id']} is true: {f['says']}"
        key = f["id"] if f["ok"] is False else f"unreadable:{f['id']}"
        said[f["id"]] = tell(sentence, by=BY, key=key, evidence=f.get("evidence"))
    return said


def tick(at: float | None = None, daily: bool | None = None,
         tell: Callable[..., str] | None = None, path: Path | None = None) -> dict:
    """One reading of every fact: the state file written, what is false told.
    ``daily`` forces (True) or skips (False) the once-a-day facts; None runs
    them at the first tick at or after ``DAILY_AT`` local each date."""
    at = now() if at is None else at
    prev = load_state(path)
    today, hhmm = local_day(at)
    last_daily = (prev.get("daily") or {})
    if daily is None:
        daily = last_daily.get("date") != today and (hhmm >= DAILY_AT or not last_daily)
    names = agents()
    states = provider_logins(names)
    facts: list[dict] = []
    for name, reader in every_tick(at, states):
        facts.extend(read(name, reader))
    if daily:
        day_facts: list[dict] = []
        for name, reader in once_a_day():
            day_facts.extend(read(name, reader))
        last_daily = {"date": today, "at": iso(at), "facts": day_facts}
    facts.extend(last_daily.get("facts") or [])
    told = tell_all(facts, tell)
    status = run_status(at, names, states)
    talks = [f["says"] for f in facts if f["ok"] is False and f.get("blocks") == "conversations"]
    state = {
        "at": iso(at),
        "stale_after": iso(at + STALE_S),
        "ok": all(f["ok"] is True for f in facts),
        "crashed": None,
        "agents_can_run": status["agents_can_run"], "why": status["why"],
        "conversations": status["agents_can_run"] and not talks,
        "conversations_why": status["why"] + talks,
        "facts": [f for f in facts if f["ok"] is not True] +
                 [f for f in facts if f["ok"] is True],
        "told": told,
        "daily": last_daily,
    }
    write_state(state, path)
    return state


def crashed(exc: BaseException, at: float | None = None,
            tell: Callable[..., str] | None = None, path: Path | None = None) -> dict:
    """The program itself failed: said in the state file, and told."""
    at = now() if at is None else at
    prev = load_state(path)
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-4000:]
    last = f"{type(exc).__name__}: {exc}"[:300]
    state = {**{k: prev.get(k) for k in ("agents_can_run", "why", "conversations",
                                         "conversations_why", "daily")},
             "at": iso(at), "stale_after": iso(at + STALE_S), "ok": False,
             "crashed": {"at": iso(at), "error": last, "traceback": text},
             "facts": prev.get("facts") or [], "told": {}}
    try:
        if tell is None:
            import loam_attention  # noqa: PLC0415
            tell = loam_attention.tell
        state["told"] = {"crashed": tell(
            f"Loam's check of its own state crashed, so nothing about Loam is being "
            f"checked: {last}", by=BY, key="check crashed",
            evidence=f"`{path or STATE_FILE}` and `journalctl --user -u loam-check.service`")}
    except Exception:  # noqa: BLE001 -- the state file still says it
        pass
    write_state(state, path)
    return state
