#!/usr/bin/env python3
"""The Claude Code sign-in arms the Remote Control servers (2026-11-14,
first release L6; bin/rc-arm).

  the unit     every loam-rc-<agent>.service carries ConditionPathExists= on
               the login file, so with no sign-in it is skipped, never
               restarted (the loop it replaces: 56 restarts in one install
               rehearsal, every 10 s).
  the watch    loam-claude-signin.path is a shipped template, PathChanged on the
               same file, wanted by default.target, rendered by gen-units.
  signed in    every enabled server waiting for a sign-in (inactive, its
               condition not passed) is started; a running one, and one that
               was stopped by hand while signed in, are left alone; a server
               that is not enabled (an archived agent) is never started.
  signed out   the file is looked at again before anything is stopped (a
               rewrite is not a sign-out); then every running server stops,
               and is written down, because a server stopped while it ran
               keeps ConditionResult=yes; the next sign-in starts it again
               and clears the list.
  dry run      says, does nothing.

systemctl is a recorder here; nothing is started or stopped.

Run: python3 tests/test_rc_arm.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_config as lc  # noqa: E402
import loam_units  # noqa: E402

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:62} {got!r}"[:220]
          + ("" if ok else f"   want {want!r}"))


def load(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


arm = load("rc_arm_test", AGENTS / "bin" / "rc-arm")
genrc = load("gen_rc_units_test", AGENTS / "bin" / "gen-rc-units")

# -- the unit and the watch ------------------------------------------------------
cred = str(lc.CLAUDE_HOME / ".credentials.json")
unit = genrc.TEMPLATE.format(display="Iris", name="iris", workspace="/w")
head = unit.split("[Service]")[0]
check("the RC unit is skipped without the login file",
      f"ConditionPathExists={cred}" in head, True)
check("...and still restarts a server that dies while signed in",
      "Restart=always" in unit, True)
check("rc-arm watches the same file", str(arm.CREDENTIALS), cred)
units = loam_units.rendered(AGENTS / "systemd")
path = units.get("loam-claude-signin.path", "")
check("loam-claude-signin.path ships, a path unit gen-units renders",
      (f"PathChanged={cred}" in path, "Unit=loam-claude-signin.service" in path,
       "WantedBy=default.target" in path), (True, True, True))
check("...PathChanged, never PathExists (which re-fires on every exit)",
      any(l.startswith("PathExists=") for l in path.splitlines()), False)
check("...and neither is named loam-rc-*, which is every agent's RC server to "
      "recycle-rc, provider-follow and check-drift",
      [n for n in units if n.startswith("loam-rc-")], [])
check("...its service runs rc-arm, once a change",
      ("Type=oneshot" in units.get("loam-claude-signin.service", ""),
       f"{lc.ROOT}/bin/rc-arm" in units.get("loam-claude-signin.service", "")), (True, True))


# -- the decision ------------------------------------------------------------------
class Box:
    """systemctl --user, for a box's RC servers: {unit: (enabled, active, condition)}."""

    def __init__(self, servers: dict[str, tuple[bool, str, str]]):
        self.servers = servers
        self.did: list[tuple[str, str]] = []

    def __call__(self, *args: str):
        if args[0] == "list-unit-files":
            out = "".join(f"{u} enabled enabled\n" for u, (en, _a, _c) in
                          sorted(self.servers.items()) if en)
            return SimpleNamespace(stdout=out, stderr="", returncode=0)
        if args[0] == "show":
            _en, act, cond = self.servers[args[1]]
            return SimpleNamespace(stdout=f"ActiveState={act}\nConditionResult={cond}\n",
                                   stderr="", returncode=0)
        if args[0] in ("start", "stop"):
            self.did.append((args[0], args[-1]))
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        raise AssertionError(args)


def fleet():
    return Box({
        "loam-rc-iris.service": (True, "inactive", "no"),      # waiting: never ran, or skipped
        "loam-rc-ada.service": (True, "active", "yes"),        # running
        "loam-rc-juno.service": (True, "inactive", "yes"),     # stopped by hand, signed in
        "loam-rc-pax.service": (False, "inactive", "no"),      # archived: disabled
        "loam-rc-wren.service": (True, "failed", "yes"),       # failed: left for its owner
    })


with tempfile.TemporaryDirectory() as tmp:
    login = Path(tmp) / ".credentials.json"
    rec = Path(tmp) / "rc-arm.json"
    slept: list[float] = []
    real_main = arm.main
    arm.main = lambda argv, **k: real_main(argv, record=rec, **k)

    box = fleet()
    login.write_text("{}")
    arm.main([], run=box, credentials=login, sleep=slept.append)
    check("signed in: the waiting server starts, and only it",
          box.did, [("start", "loam-rc-iris.service")])
    check("...without waiting", slept, [])

    box = fleet()
    arm.main(["--dry-run"], run=box, credentials=login, sleep=slept.append)
    check("dry run: nothing started", box.did, [])

    box = fleet()
    login.unlink()
    arm.main([], run=box, credentials=login, sleep=slept.append)
    check("signed out: looked again first", slept, [arm.RECHECK_S])
    check("...then every running server stops, nothing else",
          box.did, [("stop", "loam-rc-ada.service" )])
    check("...and is written down", arm.disarmed(rec), ["loam-rc-ada.service" ])
    after = fleet()
    after.servers["loam-rc-ada.service" ] = (True, "inactive", "yes")   # as systemd leaves it
    login.write_text("{}")
    arm.main([], run=after, credentials=login, sleep=slept.append)
    check("the next sign-in starts it again, and the waiting one",
          sorted(after.did), [("start", "loam-rc-ada.service" ), ("start", "loam-rc-iris.service")])
    check("...and clears the list", rec.exists(), False)
    login.unlink()

    box = fleet()
    slept.clear()
    arm.main([], run=box, credentials=login,
             sleep=lambda s: (slept.append(s), login.write_text("{}")))
    check("a file back by the second look is a rewrite: nothing stops",
          (box.did, slept), ([("start", "loam-rc-iris.service")], [arm.RECHECK_S]))

    box = Box({"loam-rc-ada.service" : (True, "active", "yes")})
    arm.main([], run=box, credentials=login, sleep=slept.append)
    check("a signed-in box (every server running): a token refresh does nothing",
          box.did, [])

print()
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
