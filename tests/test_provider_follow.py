#!/usr/bin/env python3
"""lib/loam_follow.py: the daily provider follower's decisions, offline.

  versions     newer-than, the prune (live and the previous two kept, a
               newer candidate left alone), model names, alias moves.
  decide       not signed in is a run-log skip and never a comms line; a
               self-updating provider is skipped; current is nothing (or a
               rehearsal under --force); newer is installed beside the live
               build with the link untouched; a refused build is never
               taken again.
  switch       a pass re-points, recycles RC (Claude Code), restarts the
               broker, writes one Other line naming the alias move; a fail
               re-points nothing and writes an Important line; a rehearsal
               acts on nothing, even beside a real switch's hands.
  broker       busy is `working` or `asking`, a failed read is busy, the
               restart waits for quiet and defers past its bound.
  watch        a lane failure after the switch rolls the link back, writes
               Important, refuses the build; a clean window closes and prunes.
  signals      bin/wake's failed stages since the switch, broker sessions of
               the provider with an error exit.

Fake providers and fake hands; no network, no real link, no real broker.

Run: python3 tests/test_provider_follow.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="test-provider-follow-"))
os.environ.update({
    "LOAM_FOLLOW_STATE": str(TMP / "state"),
    "LOAM_FOLLOW_LOG": str(TMP / "follow.jsonl"),
    "LOAM_FOLLOW_VAULT": str(TMP / "vault"),
    "LOAM_FOLLOW_WAKE_LOGS": str(TMP / "wake-logs"),
    "LOAM_FOLLOW_WATCH_S": "0",
    # The memory check's baseline, the test's own (the box's stores still
    # count, but against a first-seen of this run's).
    "LOAM_PROVIDER_MEMORY_BASELINE": str(TMP / "provider-memory.json"),
})
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_follow as f  # noqa: E402

results: list[bool] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  | {detail}" if detail and not ok else ""))


# --- versions ----------------------------------------------------------------

check("2.1.323 is newer than 2.1.322", f.newer("2.1.323", "2.1.322"))
check("2.1.322 is not newer than itself", not f.newer("2.1.322", "2.1.322"))
check("0.167.10 is newer than 0.167.9 (numeric, not text)", f.newer("0.167.10", "0.167.9"))
check("a pre-release tag is never taken", not f.newer("0.169.0-alpha.11", "0.167.1"))
check("anything is newer than no live build", f.newer("1.0.0", None))
check("prune keeps live and the previous two",
      f.prunable(["2.1.303", "2.1.312", "2.1.319", "2.1.322"], "2.1.322") == ["2.1.303"])
check("prune leaves a newer candidate alone",
      f.prunable(["2.1.312", "2.1.319", "2.1.322", "2.1.330"], "2.1.322") == [])
check("prune honours a protected build",
      f.prunable(["2.1.1", "2.1.2", "2.1.3", "2.1.4"], "2.1.4", protect=("2.1.1",)) == [])
check("claude-fable-5-3 reads as Fable 5.3", f.model_name("claude-fable-5-3") == "Fable 5.3")
check("a dated id reads by family", f.model_name("claude-haiku-4-5-20251001") == "Haiku 4.5")
check("a non-Claude id stays as it is", f.model_name("gpt-6.1-sol") == "gpt-6.1-sol")
moves = f.alias_moves({"fable": "claude-fable-5-2", "opus": "claude-opus-5-5"},
                      {"fable": "claude-fable-5-3", "opus": "claude-opus-5-5"})
check("an alias that moved is named, as the owner's example line",
      moves == ["fable now means Fable 5.3 (was claude-fable-5-2)"], str(moves))
check("an alias the live build did not report is not a move",
      f.alias_moves({}, {"fable": "claude-fable-5-3"}) == [])


# --- fakes -------------------------------------------------------------------

class Fake(f.Provider):
    name, label, broker_provider = "fake", "Fake CLI", "fake"
    recycles_rc = True

    def __init__(self, root: Path, latest: str = "1.0.1", signed: bool = True,
                 smoke_ok: bool = True, fails: list[str] | None = None) -> None:
        super().__init__(root / "bin" / "fake", root / "versions")
        self.link.parent.mkdir(parents=True, exist_ok=True)
        self.versions.mkdir(parents=True, exist_ok=True)
        self._latest, self._signed, self.smoke_ok = latest, signed, smoke_ok
        self.fails = fails or []
        self.installs: list[str] = []

    def put(self, version: str) -> None:
        self.build(version).write_text("#!/bin/sh\necho " + version + "\n")
        self.build(version).chmod(0o755)

    def signed_in(self):
        return self._signed, "signed in" if self._signed else "no login on the box"

    def latest(self):
        return self._latest

    def build(self, version):
        return self.versions / version

    def install(self, version):
        self.installs.append(version)
        self.put(version)

    def smoke(self, candidate, live, ws):
        return [f.Check("version", True, candidate),
                f.Check("alias table", True, gating=False, data={
                    "moves": f.alias_moves({"fable": "claude-fable-5-2"},
                                           {"fable": "claude-fable-5-3"})}),
                f.Check("wake run", self.smoke_ok, "rc 0" if self.smoke_ok else "rc 1",
                        data={"models": ["claude-fable-5-3"]})]

    def lane_failures(self, since):
        return list(self.fails)

    def describe_move(self, checks):
        return [m for c in checks for m in c.data.get("moves", [])]


class FakeHands(f.Hands):
    def __init__(self, dry: bool = False) -> None:
        super().__init__(dry=dry, vault=TMP / "vault", quiet_max_s=0)
        self.did: list[tuple] = []

    def dry_copy(self):
        twin = FakeHands(dry=True)
        twin.did = self.did
        twin.do_repoint = False
        return twin

    def recycle_rc(self):
        self.did.append(("recycle", self.dry))
        return True

    def restart_broker(self):
        self.did.append(("restart", self.dry))
        return True

    def comms(self, section, text):
        self.did.append(("comms", section, text, self.dry))
        return True


def fresh(name: str, **kw) -> Fake:
    root = TMP / name
    p = Fake(root, **kw)
    p.name = name
    return p


# --- decide ------------------------------------------------------------------

p = fresh("unsigned", signed=False)
hands = FakeHands()
check("not signed in: nothing to do", f.decide(p, False, hands) is None)
check("not signed in: no comms line", not [d for d in hands.did if d[0] == "comms"])
rows = [json.loads(ln) for ln in (TMP / "follow.jsonl").read_text().splitlines()]
check("not signed in: the run log says so",
      any(r["provider"] == "unsigned" and r["step"] == "skip"
          and "not signed in" in r["reason"] for r in rows))

p = fresh("selfupdating")
p.follows = False
p.put("1.0.0")
p.repoint("1.0.0")
hands = FakeHands()
check("a self-updating provider is skipped", f.decide(p, False, hands) is None)
check("... and asks once, under Other, for its pin",
      [d[1] for d in hands.did if d[0] == "comms"] == ["Other"])

p = fresh("current", latest="1.0.0")
p.put("1.0.0")
p.repoint("1.0.0")
check("current: nothing to do", f.decide(p, False) is None)
plan = f.decide(p, True)
check("current under --force: the live build is its own candidate, a rehearsal",
      plan is not None and plan.rehearsal and plan.candidate == "1.0.0")

p = fresh("newer", latest="1.0.1")
p.put("1.0.0")
p.repoint("1.0.0")
plan = f.decide(p, False)
check("newer: installed beside the live build",
      plan is not None and p.installs == ["1.0.1"] and p.build("1.0.1").exists())
check("newer: the link is untouched by the install", p.live() == "1.0.0")


# --- switch ------------------------------------------------------------------

f.smoke(plan, TMP)
check("the smoke passed on the fake", plan.passed)
hands = FakeHands()
went = f.switch([plan], hands)
check("a pass re-points the link", p.live() == "1.0.1" and went == [plan])
check("a pass recycles the RC servers and restarts the broker, for real",
      ("recycle", False) in hands.did and ("restart", False) in hands.did)
lines = [d for d in hands.did if d[0] == "comms"]
check("one comms line, under Other", len(lines) == 1 and lines[0][1] == "Other", str(lines))
check("the line has the versions and the alias move",
      "1.0.0 → 1.0.1" in lines[0][2] and "fable now means Fable 5.3" in lines[0][2],
      lines[0][2])
state = f.read_state(p)
check("the state names previous and opens the window",
      state["live"] == "1.0.1" and state["previous"] == "1.0.0"
      and state["watched"] is False)

q = fresh("failing", latest="2.0.1", smoke_ok=False)
q.put("2.0.0")
q.repoint("2.0.0")
qplan = f.decide(q, False)
f.smoke(qplan, TMP)
hands = FakeHands()
went = f.switch([qplan], hands)
check("a failed smoke re-points nothing", q.live() == "2.0.0" and went == [])
check("a failed smoke restarts nothing",
      not [d for d in hands.did if d[0] in ("recycle", "restart")])
lines = [d for d in hands.did if d[0] == "comms"]
check("a failed smoke is one Important line naming the check",
      len(lines) == 1 and lines[0][1] == "Important" and "wake run" in lines[0][2], str(lines))

r = fresh("rehearsed", latest="3.0.0")
r.put("3.0.0")
r.repoint("3.0.0")
rplan = f.decide(r, True)
f.smoke(rplan, TMP)
hands = FakeHands()
f.switch([rplan], hands)
check("a rehearsal recycles and restarts only dry",
      ("recycle", True) in hands.did and ("restart", True) in hands.did
      and ("recycle", False) not in hands.did and ("restart", False) not in hands.did,
      str(hands.did))
check("a rehearsal writes no comms line", not [d for d in hands.did if d[0] == "comms"])
check("a rehearsal leaves no switch in the state", not f.read_state(r).get("switched_at"))


# --- the broker --------------------------------------------------------------

check("working and asking are busy",
      len(f.busy([{"state": "working"}, {"state": "asking"}, {"state": "idle"},
                  {"state": "background"}])) == 2)
check("a failed read counts as busy", bool(f.busy(None)))
check("idle and background are quiet", f.busy([{"state": "idle"}, {"state": "background"}]) == [])

reads = iter([None, [{"state": "working", "agent": "ada", "id": "abc"}], []])
f.broker_rows = lambda timeout=10: next(reads)
f.QUIET_POLL_S = 0
real_run = f.run
calls = []
f.run = lambda argv, **kw: calls.append(argv) or type("D", (), {"returncode": 0, "stdout": ""})()
h = f.Hands(dry=False, vault=TMP / "vault", quiet_max_s=60)
check("the restart waits through a failed read and a working session, then restarts",
      h.restart_broker() and len(calls) == 1 and calls[0][0].endswith("restart-broker"))
f.broker_rows = lambda timeout=10: None
calls.clear()
h = f.Hands(dry=False, vault=TMP / "vault", quiet_max_s=0)
check("past its bound the restart is deferred, never forced",
      h.restart_broker() is False and calls == [])
f.run = real_run


# --- watch -------------------------------------------------------------------

s = fresh("rollback", latest="4.0.1", fails=["ada wake invoke failed at now"])
s.put("4.0.0")
s.repoint("4.0.0")
splan = f.decide(s, False)
f.smoke(splan, TMP)
hands = FakeHands()
f.switch([splan], hands)
check("switched before the watch", s.live() == "4.0.1")
f.watch([s], hands, poll_s=0)
check("a lane failure rolls the link back", s.live() == "4.0.0")
lines = [d for d in hands.did if d[0] == "comms" and d[1] == "Important"]
check("one Important line saying what failed",
      len(lines) == 1 and "4.0.1 rolled back to 4.0.0" in lines[0][2]
      and "ada wake invoke failed" in lines[0][2], str(lines))
check("the rollback restarts the same careful way",
      hands.did.count(("restart", False)) == 2 and hands.did.count(("recycle", False)) == 2)
state = f.read_state(s)
check("the build is refused", "4.0.1" in state.get("refused", {}))
check("a refused build is not taken again", f.decide(s, False) is None)
s._latest = "4.0.2"
check("the next release is", (pl := f.decide(s, False)) is not None and pl.candidate == "4.0.2")

c = fresh("clean", latest="5.0.4")
for v in ("5.0.0", "5.0.1", "5.0.2", "5.0.3"):
    c.put(v)
c.repoint("5.0.3")
cplan = f.decide(c, False)
f.smoke(cplan, TMP)
f.switch([cplan], FakeHands())
f.watch([c], FakeHands(), poll_s=0)
state = f.read_state(c)
check("a clean window closes", state.get("watched") is True and c.live() == "5.0.4")
check("and prunes to live plus the previous two",
      c.installed() == ["5.0.2", "5.0.3", "5.0.4"], str(c.installed()))


# --- signals ------------------------------------------------------------------

logs = TMP / "wake-logs"
logs.mkdir(exist_ok=True)
now = time.time()
(logs / "wake-iris.jsonl").write_text("\n".join(json.dumps(r) for r in [
    {"ts": f.iso(now - 7200), "ok": False, "stage": "invoke", "lane": "wake"},
    {"ts": f.iso(now - 10), "ok": False, "stage": "preflight", "lane": "wake"},
    {"ts": f.iso(now - 10), "ok": False, "stage": "account_refused", "lane": "wake"},
    {"ts": f.iso(now - 5), "ok": True, "stage": None, "lane": "wake"},
    {"ts": f.iso(now - 5), "ok": False, "stage": "identity", "lane": "cron:x",
     "error": "canary missing"},
]) + "\n")
found = f.wake_failures(now - 60, logs)
check("wake failures: the identity failure after the switch, nothing else",
      len(found) == 1 and "iris cron:x identity" in found[0], str(found))

sessions = TMP / "sessions"
sessions.mkdir()
for sid, provider, code in (("aaaaaaaa1", "codex", 1), ("bbbbbbbb2", "codex", 143),
                            ("cccccccc3", "claude-code", 1), ("dddddddd4", "codex", None)):
    (sessions / f"{sid}.json").write_text(json.dumps(
        {"provider": provider, "state": "exited", "exit_code": code, "agent": "ada"}))
found = f.broker_session_failures(now - 60, "codex", sessions)
check("broker sessions: an error exit of this provider, not a stop or another provider",
      len(found) == 1 and "aaaaaaaa" in found[0], str(found))


print(f"\n{sum(results)}/{len(results)} passed")
import shutil  # noqa: E402
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(0 if all(results) else 1)
