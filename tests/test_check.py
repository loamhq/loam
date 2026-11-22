#!/usr/bin/env python3
"""Exercise scheduled health checks on a scratch installation, including failure
reporting and recovery.

Run: python3 tests/test_check.py
"""

from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

AGENTS = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="loam-check-"))
os.environ.setdefault("LOAM_ATTENTION_DIR", str(TMP / "attention"))
os.environ.setdefault("LOAM_WAITERS_DIR", str(TMP / "waiters"))
os.environ["LOAM_CHECK_STATE"] = str(TMP / "check.json")
os.environ["LOAM_RESULTS_KICK"] = "0"
sys.path.insert(0, str(AGENTS / "lib"))

import loam_config as lc  # noqa: E402
import loam_attention as la  # noqa: E402
import loam_antigravity  # noqa: E402
import loam_broker  # noqa: E402
import loam_check as ck  # noqa: E402
import loam_codex  # noqa: E402
import loam_pressure  # noqa: E402

results: list[bool] = []


def check(label: str, ok, detail="") -> None:
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  | {detail}" if detail and not ok else ""))


# -- the scratch install ------------------------------------------------------

AT = time.time()
ATTN = la.STATE
LABS = lc.WORKSPACES
NAMES = ["ada", "iris"]


def agent(name: str) -> None:
    ws = LABS / name
    (ws / "crons").mkdir(parents=True, exist_ok=True)
    (ws / "agent.json").write_text(json.dumps({"name": name, "workspace": str(ws),
                                               "vault": str(lc.VAULT)}))
    rec = ws / "crons" / "reflection.md"
    rec.write_text("---\nschedule: daily at 04:00 PT\nenabled: true\n---\nReflect.\n")
    old = AT - 40 * 86400
    os.utime(rec, (old, old))


def ledger(name: str, rows: list[dict]) -> None:
    lc.LOGS.mkdir(parents=True, exist_ok=True)
    (lc.LOGS / f"wake-{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def healthy() -> None:
    for name in NAMES:
        agent(name)
        ledger(name, [{"ts": ck.iso(AT - 5 * 3600), "finished": ck.iso(AT - 5 * 3600 + 300),
                       "ok": True, "lane": "reflection"}])
    loam_pressure.PRESSURE.parent.mkdir(parents=True, exist_ok=True)
    loam_pressure.PRESSURE.write_text(json.dumps({"at": ck.iso(AT - 30), "ram_pct": 40,
                                                  "swap_free_pct": 90, "episode_since": None}))
    sample = lc.LOGS / "memsample.jsonl"
    sample.write_text("{}\n")
    os.utime(sample, (AT - 60, AT - 60))
    CREDS.write_text(json.dumps({"claudeAiOauth": {
        "accessToken": "x", "refreshTokenExpiresAt": (AT + 30 * 86400) * 1000}}))
    FAILED["user"], FAILED["system"] = "", ""
    for p in ATTN.glob("*.json"):
        p.unlink()
    ck.STATE_FILE.unlink(missing_ok=True)


CREDS = TMP / "credentials.json"
loam_broker.CREDENTIALS_PATH = CREDS
loam_broker.login_source = lambda env=None: "file"
# Automatic routing must see only the logins this test creates, including
# when Claude expires and the selector tries the other providers.
loam_codex.AUTH_PATH = TMP / "codex-auth.json"
loam_antigravity.login_state = lambda fresh=False: {
    "source": "none", "connected": False, "reason": "no login on the box"}

FAILED = {"user": "", "system": ""}


def fake_systemctl(*args):
    scope = "user" if "--user" in args else "system"
    return SimpleNamespace(returncode=0, stdout=FAILED[scope], stderr="")


ck.systemctl = fake_systemctl
ck.units.__defaults__ = (fake_systemctl,)
ck.broker.__defaults__ = (lambda s: None,)


class Up(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Up)
threading.Thread(target=server.serve_forever, daemon=True).start()
UP = f"http://127.0.0.1:{server.server_address[1]}/up"
os.environ["LOAM_CHECK_BROKER_URL"] = UP

DRIFT = {"facts": [ck.fact("drift", True, "in step")]}
DAILY_RUNS = []


def fake_daily():
    DAILY_RUNS.append(1)
    return DRIFT["facts"]


ck.once_a_day = lambda: [("drift", fake_daily)]
ck.github = lambda: []

TOLD: list[tuple] = []


def tell(fact, **kw):
    """The real seam, without starting a drain; each call recorded."""
    said = la.tell(fact, kick=False, **kw)
    TOLD.append((kw.get("key"), said))
    return said


def records() -> list[dict]:
    return la.pending()


# -- 1. all true -------------------------------------------------------------

healthy()
state = ck.tick(AT, daily=True, tell=tell)
false = [f for f in state["facts"] if f["ok"] is not True]
check("1. all true: every fact true", state["ok"] and not false, false)
check("...the state file is written", json.loads(ck.STATE_FILE.read_text())["at"] == ck.iso(AT))
check("...nothing told, the attention folder empty",
      TOLD == [] and not list(ATTN.glob("*")), (TOLD, list(ATTN.glob("*"))))
check("...agents can run, and conversations", state["agents_can_run"] and state["conversations"])
check("...it knows when it goes stale", state["stale_after"] == ck.iso(AT + ck.STALE_S))
for n in range(3):
    ck.tick(AT + 60 * (n + 1), tell=tell)
check("...and over three more ticks still nothing told", TOLD == [] and not list(ATTN.glob("*")))


# -- 2. each fact false, once -----------------------------------------------

def forced(label: str, breaks, key: str, blocks=None) -> None:
    healthy()
    TOLD.clear()
    breaks()
    first = ck.tick(AT, daily=True, tell=tell)
    pend = records()
    mine = [r for r in pend if r["key"] == f"{ck.BY}:{key}"]
    check(f"2. {label}: told once, one record", len(mine) == 1 and len(pend) == 1,
          [(r["key"], r["fact"][:80]) for r in pend])
    second = ck.tick(AT + 60, daily=True, tell=tell)
    check("...the next tick tells nothing more",
          second["told"].get(key) == "repeat" and len(records()) == 1, second["told"])
    if blocks:
        check(f"...{blocks}", first[blocks] is False and first[blocks + "_why" if blocks ==
              "conversations" else "why"], first)


forced("a reflection not completed in 26 h",
       lambda: ledger("ada", [{"ts": ck.iso(AT - 30 * 3600), "ok": True, "lane": "reflection"},
                              {"ts": ck.iso(AT - 6 * 3600), "ok": False, "lane": "reflection",
                               "stage": "invoke"}]),
       "reflection:ada")
forced("the broker not answering",
       lambda: os.environ.__setitem__("LOAM_CHECK_BROKER_URL", "http://127.0.0.1:9/up"),
       "broker", blocks="conversations")
os.environ["LOAM_CHECK_BROKER_URL"] = UP
forced("a login within 3 days of expiring",
       lambda: CREDS.write_text(json.dumps({"claudeAiOauth": {
           "accessToken": "x", "refreshTokenExpiresAt": (AT + 2 * 86400) * 1000}})),
       "login:claude-code:expiring")
forced("a login expired",
       lambda: CREDS.write_text(json.dumps({"claudeAiOauth": {
           "accessToken": "x", "refreshTokenExpiresAt": (AT - 3600) * 1000}})),
       "login:claude-code:gone", blocks="agents_can_run")
forced("memory pressure under way",
       lambda: loam_pressure.PRESSURE.write_text(json.dumps({
           "at": ck.iso(AT - 30), "ram_pct": 91, "swap_free_pct": 50,
           "episode_since": ck.iso(AT - 300), "episode_says": "memory pressure on the box"})),
       "memory")
forced("the memory history stopped",
       lambda: os.utime(lc.LOGS / "memsample.jsonl", (AT - 3600, AT - 3600)), "memsample")
forced("a unit of the machinery failed (the host history)",
       lambda: FAILED.__setitem__("system", "loam-host-history.service loaded failed failed x\n"),
       "unit:loam-host-history.service")
healthy()
FAILED["user"] = "loam-wake-ada.service loaded failed failed x\n"
check("...a persona's own failed wake is not machinery",
      all(f["ok"] for f in ck.units(fake_systemctl)))

# A plugin timer that failed and then became active again (absent from --failed)
# is not reported on the next check, and any undelivered notice is withdrawn.
healthy()
FAILED["user"] = "loam-plugin-boards--expire.timer loaded failed failed x\n"
TOLD.clear()
s_fail = ck.tick(AT, daily=True, tell=tell)
check("...a failed plugin timer is reported",
      any(f["id"] == "unit:loam-plugin-boards--expire.timer" and f["ok"] is False
          for f in s_fail["facts"]))
FAILED["user"] = ""
s_ok = ck.tick(AT + 60, daily=True, tell=None)
check("...once active again it is not reported and its pending notice is withdrawn",
      s_ok["ok"] and not any(r["key"] == f"{ck.BY}:unit:loam-plugin-boards--expire.timer"
                             for r in records()),
      (s_ok["ok"], [r["key"] for r in records()]))


def drift_false():
    DRIFT["facts"] = [ck.fact("drift:installed unit loam-x.timer", False,
                              "check-drift found drift: installed unit loam-x.timer")]


forced("check-drift finding drift", drift_false, "drift:installed unit loam-x.timer")
DRIFT["facts"] = [ck.fact("drift", True, "in step")]

ck.DISK_FREE_GB = 10 ** 6
forced("a disk nearly full", lambda: None, f"disk:{ck._mount_of(Path('/'))}")
ck.DISK_FREE_GB = 4

real_persona = la.persona
la.persona = lambda: None
forced("nobody awake to hear", lambda: None, "hears")
la.persona = real_persona


# -- Install-wide availability, between ticks -------------------------------

healthy()
ck.tick(AT, daily=True, tell=tell)
CREDS.unlink()
status = ck.current_run_status()
check("availability: current login loss overrides the previous tick",
      status["agents_can_run"] is False and status["action"]["kind"] == "providers", status)
ada_card = LABS / "ada" / "agent.json"
original = json.loads(ada_card.read_text())
ada_card.write_text(json.dumps({**original, "provider": "codex"}))
loam_codex.AUTH_PATH.write_text(json.dumps({"tokens": {"refresh_token": "scratch"}}))
check("availability: another provider can run while Claude is signed out",
      ck.current_run_status()["agents_can_run"] is True)
states = ck.provider_logins(["ada"])
check("availability: Codex-only needs just the Codex login",
      set(states) == {"codex"} and ck.run_status(AT, ["ada"], states)["agents_can_run"] is True)

refused = {"provider": "codex", "ok": False, "stage": "account_refused"}
ledger("ada", [{**refused, "ts": ck.iso(AT - 100)}, {**refused, "ts": ck.iso(AT - 50)}])
status = ck.current_run_status()
check("availability: all used providers blocked, with evidence of Codex refusals",
      status["agents_can_run"] is False and "Codex refused its recent runs." in status["why"], status)
ledger("ada", [{**refused, "ts": ck.iso(AT - 100)}, {**refused, "ts": ck.iso(AT - 50)},
               {"provider": "codex", "ok": True, "ts": ck.iso(AT - 10)}])
check("availability: a later successful wake clears the account refusal",
      ck.current_run_status()["agents_can_run"] is True)
ledger("ada", [{**refused, "ts": ck.iso(AT - 27 * 3600)}, {**refused, "ts": ck.iso(AT - 50)}])
check("availability: one recent refusal and an old one do not establish an outage",
      ck.current_run_status()["agents_can_run"] is True)
ada_card.write_text(json.dumps(original))
healthy()
ck.tick(AT, daily=True, tell=tell)
for name in NAMES:
    card = LABS / name / "agent.json"
    card.write_text(json.dumps({**json.loads(card.read_text()), "archived": {"at": ck.iso(AT)}}))
status = ck.current_run_status()
check("availability: a fully archived roster has a restore action",
      status["agents_can_run"] is False and status["action"]["kind"] == "archived", status)
ck.write_state({"agents_can_run": True, "stale_after": ck.iso(AT - 1)})
status = ck.current_run_status()
check("availability: stale state never reports healthy and has no owner action",
      status == {"agents_can_run": False, "why": ["Loam's own check has stopped."], "action": None}, status)


# -- 3. unreadable is not true -----------------------------------------------

healthy()
TOLD.clear()
loam_pressure.PRESSURE.unlink()
state = ck.tick(AT, daily=True, tell=tell)
mem = [f for f in state["facts"] if f["id"] == "memory"]
check("3. a fact that cannot be read is ok: null, not true",
      mem and mem[0]["ok"] is None and not state["ok"], mem)
check("...and told as a fact of its own",
      any(r["key"] == f"{ck.BY}:unreadable:memory" and "could not read" in r["fact"]
          for r in records()), [r["key"] for r in records()])


# -- 4. the crash ------------------------------------------------------------

healthy()
env = dict(os.environ, LOAM_CHECK_CRASH="1")
run = subprocess.run([sys.executable, str(AGENTS / "bin" / "loam-check")], env=env,
                     capture_output=True, text=True, timeout=60)
saved = json.loads(ck.STATE_FILE.read_text())
check("4. a crash exits 2", run.returncode == 2, (run.returncode, run.stderr[-300:]))
check("...the state file says it crashed, with the error",
      saved["crashed"] and "crash on purpose" in saved["crashed"]["error"]
      and saved["ok"] is False, saved.get("crashed"))
check("...and it is told", any(r["key"] == f"{ck.BY}:check crashed" for r in records()),
      [r["key"] for r in records()])


# -- 5. once a day -------------------------------------------------------------

healthy()
DAILY_RUNS.clear()
from datetime import datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402
zone = ZoneInfo(lc.TIMEZONE)


def local(day: int, hh: int, mm: int) -> float:
    return datetime(2026, 11, day, hh, mm, tzinfo=zone).timestamp()


ck.tick(local(20, 1, 25), tell=tell)
check("5. the first tick of a new install reads the daily facts", len(DAILY_RUNS) == 1)
ck.tick(local(20, 1, 40), tell=tell)
check("...not again before the next date", len(DAILY_RUNS) == 1)
s = ck.tick(local(21, 1, 25), tell=tell)
check("...not before 01:40 on the next date, its result carried",
      len(DAILY_RUNS) == 1 and any(f["id"] == "drift" for f in s["facts"]))
ck.tick(local(21, 1, 40), tell=tell)
check("...at the first tick at or after 01:40", len(DAILY_RUNS) == 2)
ck.tick(local(21, 1, 55), tell=tell)
check("...once", len(DAILY_RUNS) == 2)


# -- 6. a reflection turned off, a new agent ---------------------------------

healthy()
ledger("ada", [])
rec = LABS / "ada" / "crons" / "reflection.md"
rec.write_text("---\nenabled: false\n---\n")
os.utime(rec, (AT - 40 * 86400, AT - 40 * 86400))
check("6. a reflection the owner turned off is not owed",
      all(f["ok"] for f in ck.reflections(AT, ["ada"])))
rec.write_text("---\nenabled: true\n---\n")
check("...a record made in the last 26 h is not owed one yet",
      all(f["ok"] for f in ck.reflections(AT, ["ada"])))
os.utime(rec, (AT - 40 * 86400, AT - 40 * 86400))
said = ck.reflections(AT, ["ada"])
check("...an old one with no completed reflection is false, saying never",
      said[0]["ok"] is False and "never" in said[0]["says"], said)


# -- 7. a failed run told nobody ---------------------------------------------

for p in ATTN.glob("*.json"):
    p.unlink()
la.tell("a fact for the retry", by="t", kick=False)
pend = la.pending()
la.mark_given(pend)
check("7. a given record is not due", la.due(la.pending()) == [])
la.failed(la.pending(), at=AT)
back = la.pending()
check("...a failed run puts it back, not given, with a try counted",
      len(back) == 1 and not back[0].get("given") and back[0]["tries"] == 1, back)
check("...not due before the retry wait", la.due(back, at=AT + 60) == [])
check("...due after it", len(la.due(back, at=AT + la.RETRY_MIN * 60 + 1)) == 1)
la.failed(back, at=AT)
again = la.pending()[0]
check("...the wait doubles", again["retry_at"] == AT + 2 * la.RETRY_MIN * 60, again)

server.shutdown()
print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
