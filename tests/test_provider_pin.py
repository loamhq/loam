#!/usr/bin/env python3
"""lib/loam_pins.py: the provider CLIs at the release's pin, offline.

1. The pin is one table, well formed: a version per provider, and for
   Antigravity a build address and SHA-512 per platform.
2. ``apply`` on a box at release A's pin, then at release B's (a different
   pin): B's build is installed beside A's, checked by its own --version,
   the link moved; A's is kept; a second apply moves nothing; a dry run
   moves nothing; a build whose --version is wrong does not get the link.
3. ``drift`` names a link off its pin, and nothing when it is on it.
4. The follower's units are retired: gone from systemd/, listed in
   ``loam_units.RETIRED``, and ``retire`` disables and removes them.
5. Every lane's environment turns both CLIs' own updaters off.

Builds are shell scripts that print a version; nothing is downloaded.

Run: python3 tests/test_provider_pin.py
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_pins as pins  # noqa: E402
import loam_units  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="test-provider-pin-"))
results: list[bool] = []


def check(label: str, got, want=True) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f"  | got {got!r}, want {want!r}"))


# -- 1: the pin --------------------------------------------------------------------
check("one entry per provider the box runs",
      sorted(pins.PINS), ["antigravity", "claude-code", "codex"])
check("each pin is a version",
      all(pins.is_version(v["version"]) for v in pins.PINS.values()))
agy = pins.PINS["antigravity"]["builds"]
check("Antigravity's pin names its build for linux-x64 and linux-arm64",
      sorted(agy), ["linux-arm64", "linux-x64"])
check("...each by an https address that carries the version, and a SHA-512",
      all(b["url"].startswith("https://") and pins.PINS["antigravity"]["version"] in b["url"]
          and re.fullmatch(r"[0-9a-f]{128}", b["sha512"]) for b in agy.values()))
check("the classes cover the pin", sorted(c.name for c in pins.PROVIDERS), sorted(pins.PINS))


# -- 2: apply, release A then release B ----------------------------------------------
class Fake(pins.Provider):
    name, label = "claude-code", "Claude Code"
    lie: set[str] = set()
    installs: list[str] = []

    def __init__(self, link=None, versions=None):
        super().__init__(TMP / "bin" / "claude", TMP / "versions")

    def build(self, version):
        return self.versions / version

    def install(self, version):
        self.installs.append(version)
        self.versions.mkdir(parents=True, exist_ok=True)
        says = "9.9.9" if version in self.lie else version
        path = self.build(version)
        path.write_text(f"#!/bin/sh\necho '{says} (Claude Code)'\n")
        path.chmod(0o755)


real_providers, real_pins = pins.PROVIDERS, dict(pins.PINS)
pins.PROVIDERS = (Fake,)
try:
    pins.PINS["claude-code"] = {"version": "2.1.300"}      # release A
    rows = pins.apply()
    p = Fake()
    check("A: the pinned build installed and the link at it",
          (rows[0]["moved"], rows[0]["installed"], p.live(), p.version_of(p.link)),
          (True, True, "2.1.300", "2.1.300"))
    check("A: the link is a symlink into versions/", os.readlink(p.link), str(p.build("2.1.300")))

    rows = pins.apply()
    check("A again: nothing moves, nothing installs",
          (rows[0]["moved"], rows[0]["installed"], Fake.installs), (False, False, ["2.1.300"]))

    pins.PINS["claude-code"] = {"version": "2.1.310"}      # release B, another pin
    dry = pins.apply(dry=True)
    check("B, dry run: says what it would do and moves nothing",
          (dry[0]["would"], p.live(), Fake.installs),
          ("install 2.1.310 beside and move the link", "2.1.300", ["2.1.300"]))
    check("drift names the link off the new pin",
          len(pins.drift()) == 1 and "pins 2.1.310" in pins.drift()[0])

    rows = pins.apply()
    check("B: the new pin's build installed beside, the link moved to it",
          (rows[0]["before"], rows[0]["after"], rows[0]["installed"], p.version_of(p.link)),
          ("2.1.300", "2.1.310", True, "2.1.310"))
    check("B: A's build is kept beside it", p.installed(), ["2.1.300", "2.1.310"])
    check("drift: nothing, once the box is at the pin", pins.drift(), [])

    for v in ("2.1.290", "2.1.295"):
        Fake().install(v)
    pins.apply()
    check(f"prune: the pin and the {pins.KEEP_OTHERS} highest others kept",
          p.installed(), ["2.1.295", "2.1.300", "2.1.310"])

    Fake.lie = {"2.1.320"}
    pins.PINS["claude-code"] = {"version": "2.1.320"}
    rows = pins.apply()
    check("a build whose own --version is not the pin never gets the link",
          (bool(rows[0]["error"]), "9.9.9" in rows[0]["error"], p.live()),
          (True, True, "2.1.310"))

    shutil.rmtree(TMP / "bin")
    check("a provider not on the box is not drift", pins.drift(), [])
finally:
    pins.PROVIDERS = real_providers
    pins.PINS.clear()
    pins.PINS.update(real_pins)



# -- 4: the follower's units are retired -------------------------------------------------
check("no follower unit in systemd/",
      sorted(p.name for p in (AGENTS / "systemd").glob("loam-provider-follow.*")), [])
check("...and both listed as retired", sorted(loam_units.RETIRED),
      ["loam-provider-follow.service", "loam-provider-follow.timer"])
check("no follower code left", [p for p in ("bin/provider-follow", "lib/loam_follow.py")
                                if (AGENTS / p).exists()], [])
units = TMP / "units"
(units / "timers.target.wants").mkdir(parents=True)
for u in loam_units.RETIRED:
    (units / u).write_text("[Unit]\n")
os.symlink(units / "loam-provider-follow.timer",
           units / "timers.target.wants" / "loam-provider-follow.timer")
calls = []
gone = loam_units.retire(unit_dir=units, run=lambda *a: calls.append(a))
check("retire: both found, the timer first",
      gone, ["loam-provider-follow.timer", "loam-provider-follow.service"])
check("...disabled and stopped, reloaded",
      calls[:2] + [calls[2]], [("disable", "--now", "loam-provider-follow.timer"),
                               ("disable", "--now", "loam-provider-follow.service"),
                               ("daemon-reload",)])
check("...the files and the enable link removed",
      sorted(str(p.relative_to(units)) for p in units.rglob("*") if not p.is_dir()), [])
check("...and a second run finds nothing", loam_units.retire(unit_dir=units, run=calls.append), [])


# -- 5: the CLIs' own updaters off in every lane ----------------------------------------------
AGY_OFF = "AGY_CLI_DISABLE_AUTO_UPDATE=true"
static = loam_units.rendered(AGENTS / "systemd")
for unit in ("loam-session-broker.service", "loam-reviewer.service"):
    check(f"{unit}: both updaters off",
          "Environment=DISABLE_AUTOUPDATER=1" in static[unit]
          and f"Environment={AGY_OFF}" in static[unit])
for path in ("bin/provision", "lib/loam_cron.py"):
    text = (AGENTS / path).read_text()
    check(f"{path}: the units it renders turn both off",
          "Environment=DISABLE_AUTOUPDATER=1" in text and f"Environment={AGY_OFF}" in text)
check("bin/engineer-job: a job's unit turns Antigravity's off",
      f'"--setenv={AGY_OFF}"' in (AGENTS / "bin" / "engineer-job").read_text())
import loam_antigravity  # noqa: E402
check("loam_antigravity.env(): every agy child has it, as `true`",
      loam_antigravity.env({}).get("AGY_CLI_DISABLE_AUTO_UPDATE"), "true")
check("SELF_UPDATE_OFF says the same", pins.SELF_UPDATE_OFF,
      {"DISABLE_AUTOUPDATER": "1", "AGY_CLI_DISABLE_AUTO_UPDATE": "true"})

shutil.rmtree(TMP, ignore_errors=True)
print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
