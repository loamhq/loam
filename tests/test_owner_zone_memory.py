#!/usr/bin/env python3
"""Check configured owner identity, schedule time zone and provider memory across
install layouts.

Run: python3 tests/test_owner_zone_memory.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import loam_config as lc  # noqa: E402
import loam_cron  # noqa: E402
import loam_codex  # noqa: E402
import loam_due  # noqa: E402
import loam_provider_memory as pm  # noqa: E402
import loam_threads  # noqa: E402

results: list[bool] = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:66}  | {str(detail)[:200]}")


# -- the owner ---------------------------------------------------------------------

check("owner.id is the owner", lc.is_owner(lc.OWNER_ID), lc.OWNER_ID)
check("a new install has no legacy owner id", lc.get("owner.legacy_ids") is not None
      or (lc.LEGACY_OWNER_IDS == () and not lc.is_owner("old-owner")), lc.LEGACY_OWNER_IDS)
_saved_legacy, lc.LEGACY_OWNER_IDS = lc.LEGACY_OWNER_IDS, ("old-owner",)
check("an id in owner.legacy_ids is the owner", lc.is_owner("old-owner"))
check("...in any case, padded", lc.is_owner("  Old-Owner "))
lc.LEGACY_OWNER_IDS = _saved_legacy
check("a persona is not the owner", not lc.is_owner("ada"))
check("nothing is not the owner", not lc.is_owner("") and not lc.is_owner(None))
answer = lc.owner()
check("GET /v1/owner: id, name, and the ids accepted",
      answer["id"] == lc.OWNER_ID and answer["name"] == lc.OWNER_NAME
      and set(answer["accepts"]) == {lc.OWNER_ID, *lc.LEGACY_OWNER_IDS}, answer)
check("threads write the owner as owner.id", loam_threads.OWNER == lc.OWNER_ID)
check("threads show the owner by name", loam_threads.display(lc.OWNER_ID) == lc.OWNER_NAME)

# -- the zone token ------------------------------------------------------------------

_, spec = loam_cron.parse_schedule("daily at 05:00 PT")
check("PT is America/Los_Angeles, always", spec.endswith("05:00:00 America/Los_Angeles"), spec)
_, spec = loam_cron.parse_schedule(f"Sundays at 07:00 {lc.ZONE_TOKEN}")
check("the install's own token", spec == f"Sun *-*-* 07:00:00 {lc.TIMEZONE}", spec)
_, spec = loam_cron.parse_schedule("weekdays at 09:30 UTC")
check("UTC", spec == "Mon..Fri *-*-* 09:30:00 UTC", spec)
_, spec = loam_cron.parse_schedule("daily at 06:00 Europe/Berlin")
check("an IANA zone spelled out", spec == "*-*-* 06:00:00 Europe/Berlin", spec)
try:
    loam_cron.parse_schedule("daily at 05:00 XYZ")
    check("a word that is no zone is refused", False, "accepted")
except loam_cron.CronError as exc:
    check("a word that is no zone is refused", "not a time zone" in str(exc), str(exc)[:80])
raw, spec, moment = loam_cron.parse_at("2026-12-01 14:30 UTC")
check("a one-off in UTC", spec == "2026-12-01 14:30:00 UTC" and moment.utcoffset().total_seconds() == 0, spec)
timer = loam_cron.reflection_timer("probe", "daily at 03:20 PT")
check("a reflection timer carries its zone",
      "OnCalendar=*-*-* 03:20:00 America/Los_Angeles\n" in timer, timer.splitlines()[5])

# -- the due check ---------------------------------------------------------------------

def us(text: str) -> str:
    return str(int(datetime.fromisoformat(text).timestamp() * 1_000_000))


calendars = {"loam-wake-x.timer": ["*-*-* 03:20:00 America/Los_Angeles"]}
real = loam_due._calendars
loam_due._calendars = lambda unit: calendars.get(unit, [])
try:
    env = {"TRIGGER_UNIT": "loam-wake-x.timer"}
    check("fired on time: due",
          loam_due.not_due({**env, "TRIGGER_TIMER_REALTIME_USEC": us("2026-11-14T03:20:00.3-08:00")}) is None)
    check("fired a minute late: due",
          loam_due.not_due({**env, "TRIGGER_TIMER_REALTIME_USEC": us("2026-11-14T03:21:10-08:00")}) is None)
    why = loam_due.not_due({**env, "TRIGGER_TIMER_REALTIME_USEC": us("2026-11-14T10:51:47-08:00")})
    check("fired by a restart at 10:51: not due", why and why.startswith("not due"), why)
    check("started by hand (no trigger): never refused", loam_due.not_due({}) is None)
    check("a monotonic timer (no calendar): never refused",
          loam_due.not_due({"TRIGGER_UNIT": "loam-job-recovery.timer",
                            "TRIGGER_TIMER_REALTIME_USEC": us("2026-11-14T10:51:47-08:00")}) is None)
finally:
    loam_due._calendars = real

# -- provider memory --------------------------------------------------------------------

with tempfile.TemporaryDirectory() as tmp:
    settings = Path(tmp) / "settings.json"
    settings.write_text(json.dumps({"theme": "dark", "env": {"DISABLE_AUTOUPDATER": "1"}}))
    wrote = pm.pin_claude(settings)
    live = json.loads(settings.read_text())
    check("Claude Code: both memory settings pinned false",
          live["autoMemoryEnabled"] is False and live["autoDreamEnabled"] is False, live)
    check("...the env lock beside what the env held",
          live["env"] == {"DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}, live["env"])
    check("...and the rest untouched", live["theme"] == "dark")
    check("...said what it wrote", len(wrote) == 5, wrote)
    check("a second pin writes nothing", pm.pin_claude(settings) == [])
    # A fresh install pins before any sign-in: no ~/.claude/ yet (L12).
    fresh = Path(tmp) / "no-claude-yet" / "settings.json"
    pm.pin_claude(fresh)
    check("Claude Code: pinned on a box with no ~/.claude/ yet",
          json.loads(fresh.read_text()).get("autoMemoryEnabled") is False)

text = loam_codex.trust_config([Path("/w/a")], {})
check("Codex: the memory pins are in the rendered config",
      all(line in text for line in ("memories = false", "generate_memories = false",
                                    "use_memories = false", 'persistence = "none"')))
import tomllib  # noqa: E402
parsed = tomllib.loads(text)
check("...and it parses, the trust table after them",
      parsed["features"]["memories"] is False and parsed["history"]["persistence"] == "none"
      and "/w/a" in parsed["projects"], list(parsed))

with tempfile.TemporaryDirectory() as tmp:
    real_base, real_stores = pm.BASELINE, pm.stores
    pm.BASELINE = Path(tmp) / "provider-memory.json"
    counts = {"codex memories rows": 0, "antigravity other entries": 0,
              "antigravity brain entries": 0}
    pm.stores = lambda: dict(counts)
    try:
        _, grew = pm.check()
        check("first check: nothing grew (the baseline is set)", grew == [], grew)
        counts["codex memories rows"] = 3
        _, grew = pm.check("codex")
        check("a store that grew is said", len(grew) == 1 and "codex memories rows: 3" in grew[0], grew)
        _, grew = pm.check("codex")
        check("...once, not at every start", grew == [], grew)
        counts["antigravity other entries"] = 1
        _, grew = pm.check("codex")
        check("another provider's growth waits for its own start", grew == [], grew)
        _, grew = pm.check("antigravity")
        check("...and is said there", len(grew) == 1, grew)
        counts["antigravity brain entries"] = 5
        _, grew = pm.check("antigravity")
        check("Antigravity's per-conversation files are never said (2026-11-16)", grew == [], grew)
    finally:
        pm.BASELINE, pm.stores = real_base, real_stores

failed = results.count(False)
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
