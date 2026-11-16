#!/usr/bin/env python3
"""First-party plugins (2026-11-14, first release L9): the reviewer and daily
notes, this runtime's own `plugins/`; and a plugin's pasted key.

  - default_on is a shipped plugin's default and nothing more: the reviewer
    is on and daily notes off with no saved choice, and a saved choice wins
    both ways
  - the reviewer: its timer and its words come and go with its switch; the
    timer follows its `schedule` setting (an invalid one falls back to the
    manifest's); bin/review --dry-run runs on and refuses off
  - daily notes: on makes its folder, a new folder setting makes the new
    one, off makes nothing and removes nothing
  - settings.secret: a pasted key is checked (a plugin's own check and the
    host's builtin:gemini against a fake Google), kept only when good, 0600,
    in the plugin's private folder; the list never shows a value; the routes
    answer the same and refuse an agent's call; a key with no check is kept
  - the declaration names the secret and the folder

Run: python3 tests/test_first_party.py
"""

import asyncio
import http.server
import importlib.machinery
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="loam-first-party-"))
YOURS = TMP / "yours"
YOURS.mkdir()
os.environ["LOAM_PLUGIN_SHIPPED"] = f"{AGENTS / 'plugins'}:{TMP / 'shipped'}"
os.environ["LOAM_PLUGINS_DIR"] = str(YOURS)
os.environ["LOAM_PLUGIN_STATE"] = str(TMP / "plugin-state")
os.environ["LOAM_VAULT"] = str(TMP / "vault")
os.environ["LOAM_CONTRIBUTIONS_FOLLOW"] = "0"
os.environ["LOAM_THREADS_STATE"] = str(TMP / "threads")
os.environ["LOAM_UI_CORE_OPTIONS"] = "none"
os.environ["LOAM_UI_APPEARANCE_REGISTRY"] = "none"
(TMP / "vault").mkdir()

# A scratch plugin with a key of its own: its check accepts "good-key".
KEYED = TMP / "shipped" / "keyed"
KEYED.mkdir(parents=True)
(KEYED / "PLUGIN.md").write_text(
    "---\nid: keyed\nname: Keyed\nversion: 1.0.0\napi: 0\nkind: [extension]\n"
    "contributes:\n  settings.secret:\n"
    "    - name: KEYED_API_KEY\n      label: Keyed's API key\n      check: python3 check.py\n"
    "    - name: GEMINI_API_KEY\n      label: Gemini API key\n      check: builtin:gemini\n"
    "    - name: LOOSE_KEY\n      label: A key nobody checks\n---\n\nKeyed.\n")
(KEYED / "check.py").write_text(
    "import sys\nv = sys.stdin.read()\n"
    "print('The key works.' if v == 'good-key' else 'That key was refused by Keyed.')\n"
    "sys.exit(0 if v == 'good-key' else 1)\n")

sys.path.insert(0, str(AGENTS / "lib"))
import loam_plugins as lp  # noqa: E402
import loam_contributions as c  # noqa: E402
import loam_profile as profile  # noqa: E402

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:72}"
          f"{'  | ' + str(detail)[:300] if detail != '' else ''}")


def row(pid):
    return next(r for r in lp._rows() if r["id"] == pid)


def active(pid):
    return [p for p in c.active() if p["id"] == pid]


# -- default_on ----------------------------------------------------------------
check("the reviewer is first-party, shipped and on by default",
      row("reviewer")["layer"] == "shipped" and row("reviewer")["on"] is True
      and not row("reviewer")["broken"] and row("reviewer")["warnings"] == [], row("reviewer"))
check("daily notes ships off", row("daily-notes")["on"] is False
      and not row("daily-notes")["broken"], row("daily-notes"))
lp.set_on("reviewer", False)
lp.set_on("daily-notes", True)
check("a saved choice wins over default_on, both ways",
      row("reviewer")["on"] is False and row("daily-notes")["on"] is True)
listed = json.loads((YOURS / "installed.json").read_text())
listed["on"].pop("reviewer")
(YOURS / "installed.json").write_text(json.dumps(listed))
check("with the choice gone, default_on stands again", row("reviewer")["on"] is True)

# -- the reviewer --------------------------------------------------------------
units = c.desired_units()
timer = units.get(profile.REVIEWER_TIMER, "")
check("on: its timer at 04:20 Pacific, its service is bin/plugin-run",
      "OnCalendar=*-*-* 04:20:00 America/Los_Angeles" in timer
      and "bin/plugin-run reviewer nightly" in units.get(
          profile.REVIEWER_TIMER.replace(".timer", ".service"), ""), timer)
words = c.lane_prompt("persona", "ada")
check("on: every persona is told about it, in the plugin's words",
      "a reviewer that is not a persona" in words and "**reviewer**" in words, words[:200])
c.set_setting("reviewer", "schedule", "daily at 03:10 PT")
check("the timer follows its schedule setting",
      "OnCalendar=*-*-* 03:10:00 America/Los_Angeles" in c.desired_units()[profile.REVIEWER_TIMER])
check("...and the profile reads it", profile.reviewer_schedule() == "daily at 03:10 PT")
c.set_setting("reviewer", "schedule", "now and then")
check("a schedule that does not parse leaves the manifest's",
      "04:20:00" in c.desired_units()[profile.REVIEWER_TIMER])
c.set_setting("reviewer", "schedule", "daily at 04:20 PT")

env = dict(os.environ)


def review(*argv):
    return subprocess.run([sys.executable, str(AGENTS / "bin" / "review"), *argv],
                          capture_output=True, text=True, env=env, timeout=120)


on = review("--dry-run")
check("on: bin/review --dry-run prints its facts sheet",
      on.returncode == 0 and "refuse" not in on.stderr and on.stdout.strip(),
      (on.returncode, on.stderr[-200:]))
lp.set_on("reviewer", False)
off = review("--dry-run")
check("off: bin/review refuses, and prints no sheet",
      off.returncode == 0 and not off.stdout.strip() and "plugin is off" in off.stderr,
      (off.stdout[:100], off.stderr[-200:]))
check("off: no timer and no words", profile.REVIEWER_TIMER not in c.desired_units()
      and "reviewer" not in c.lane_prompt("persona", "ada"))
check("off: the profile says so", profile.reviewer_on() is False)
lp.set_on("reviewer", True)

fakebin = TMP / "fakebin"
fakebin.mkdir()
(fakebin / "systemctl").write_text(f"#!/bin/sh\necho \"$@\" > {TMP}/systemctl.args\n")
(fakebin / "systemctl").chmod(0o755)
ran = subprocess.run([sys.executable, "run.py"], cwd=AGENTS / "plugins" / "reviewer",
                     env={**env, "PATH": f"{fakebin}:{env['PATH']}"},
                     capture_output=True, text=True, timeout=30)
check("its run starts the reviewer's lane unit and does not wait for it",
      ran.returncode == 0 and (TMP / "systemctl.args").read_text().split()
      == ["--user", "start", "--no-block", "loam-reviewer.service"], ran.stderr)

# -- daily notes ---------------------------------------------------------------
vault = TMP / "vault"
made = c.ensure_vault_folders()
check("on: its folder is made (Daily, unless set)", made == ["Daily"]
      and (vault / "Daily").is_dir(), made)
c.set_setting("daily-notes", "folder", "10_DAILY")
check("a new folder setting: the new folder, the old one kept",
      c.ensure_vault_folders() == ["10_DAILY"] and (vault / "Daily").is_dir()
      and (vault / "10_DAILY").is_dir())
check("a folder outside the vault is never made",
      c._vault_rel("../etc") is False and c._vault_rel("/etc") is False)
(vault / "10_DAILY" / "2026-11-14.md").write_text("a note\n")
lp.set_on("daily-notes", False)
check("off: no folder is wanted, and nothing is removed",
      c.vault_folders() == [] and (vault / "10_DAILY" / "2026-11-14.md").is_file())

# -- a pasted key --------------------------------------------------------------
priv = c.private_dir("keyed")
try:
    c.set_secret("keyed", "KEYED_API_KEY", "bad-key")
    refused = None
except c.ContributionError as exc:
    refused = str(exc)
check("a bad key is refused with the check's own sentence, and kept nowhere",
      refused == "That key was refused by Keyed." and not (priv / "secrets.env").exists(),
      refused)
got = c.set_secret("keyed", "KEYED_API_KEY", "  good-key\n")
mode = stat.S_IMODE((priv / "secrets.env").stat().st_mode)
check("a good key is kept, trimmed, 0600 in the plugin's private folder",
      got["source"] == "plugin" and got["checked"]["ok"]
      and (priv / "secrets.env").read_text() == "KEYED_API_KEY=good-key\n"
      and mode == 0o600 and c.secret_for("keyed", "KEYED_API_KEY") == "good-key", (got, oct(mode)))
listing = c.secrets_of("keyed")
check("the list says set and checked, and never carries a value",
      "good-key" not in json.dumps(listing)
      and listing[0]["set"] and listing[0]["checked"]["ok"], listing)
check("a key nobody checks is kept as given",
      c.set_secret("keyed", "LOOSE_KEY", "anything")["set"])
try:
    c.set_secret("keyed", "LOOSE_KEY", 'a"b')
    bad = False
except c.ContributionError:
    bad = True
check("...a quote is refused", bad)
try:
    c.set_secret("keyed", "OTHER", "x")
    undeclared = False
except c.ContributionError as exc:
    undeclared = "declares no secret" in str(exc)
check("...with a sentence naming it", undeclared)


# builtin:gemini against a fake Google
class Fake(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        key = self.headers.get("x-goog-api-key")
        if key == "AIzaGOOD":
            code, body = 200, {"models": []}
        elif key == "AIzaQUOTA":
            code, body = 429, {"error": {"message": "quota"}}
        elif key == "AIzaOFF":
            code, body = 403, {"error": {"message": "API has not been used in project 1"}}
        else:
            code, body = 400, {"error": {"message": "API key not valid.", "details": [
                {"reason": "API_KEY_INVALID"}]}}
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        assert "key=" not in self.path

    def log_message(self, *a):
        pass


srv = http.server.HTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["LOAM_GEMINI_URL"] = f"http://127.0.0.1:{srv.server_address[1]}"
import loam_keys  # noqa: E402
check("builtin:gemini: a good key", loam_keys.check("gemini", "AIzaGOOD")[0])
ok, why = loam_keys.check("gemini", "AIzaWRONG")
check("builtin:gemini: an invalid key, said plainly",
      not ok and why.startswith("Google says this is not a valid API key"), why)
ok, why = loam_keys.check("gemini", "AIzaOFF")
check("builtin:gemini: a key Google refuses, with Google's reason",
      not ok and "API has not been used" in why, why)
check("builtin:gemini: a key out of quota is a good key",
      loam_keys.check("gemini", "AIzaQUOTA")[0])
os.environ["LOAM_GEMINI_URL"] = "http://127.0.0.1:9"
ok, why = loam_keys.check("gemini", "AIzaGOOD")
check("builtin:gemini: Google unreachable is not a refusal of the key",
      not ok and why.startswith("Could not reach Google"), why)
os.environ["LOAM_GEMINI_URL"] = f"http://127.0.0.1:{srv.server_address[1]}"

decl = lp.declaration(row("keyed"), {})
touches = decl.get("touches", {})
check("the declaration names the plugin's secrets",
      touches.get("secrets") == ["GEMINI_API_KEY", "KEYED_API_KEY", "LOOSE_KEY"], touches)
check("daily notes' declaration names its folder",
      lp.declaration(row("daily-notes"), {})["touches"].get("vault") == ["(its folder setting)"])

# -- the routes ----------------------------------------------------------------
import loam_broker as cfg  # noqa: E402
import loam_config as lc  # noqa: E402
cfg.memory = lambda: {"available": None, "cgroup_room": None, "reserve": cfg.MEMORY_RESERVE_MB}
loader = importlib.machinery.SourceFileLoader("session_broker", str(AGENTS / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker_mod = importlib.util.module_from_spec(spec)
loader.exec_module(broker_mod)


class FakeBroker:
    sessions = {}
    token = "t"
    stopping = False


async def http(port, method, path, body=None):
    import urllib.error  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    def go():
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": "Bearer t", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())
    return await asyncio.to_thread(go)


async def routes():
    server = broker_mod.Server.__new__(broker_mod.Server)
    server.broker = FakeBroker()
    server.threads = broker_mod.loam_thread_engine.ThreadEngine(server.broker)
    tcp = await asyncio.start_server(server.handle, "127.0.0.1", 0)
    port = tcp.sockets[0].getsockname()[1]
    try:
        st, body = await http(port, "GET", "/v1/plugins/keyed/secrets")
        check("GET …/secrets: names, labels, set and checked, no value",
              st == 200 and [s["name"] for s in body["secrets"]]
              == ["KEYED_API_KEY", "GEMINI_API_KEY", "LOOSE_KEY"]
              and "good-key" not in json.dumps(body), body)
        st, body = await http(port, "PUT", "/v1/plugins/keyed/secrets/GEMINI_API_KEY",
                              {"value": "AIzaWRONG"})
        check("PUT a bad key: 422 with the plain sentence",
              st == 422 and body["error"].startswith("Google says this is not a valid"), body)
        st, body = await http(port, "GET", "/v1/plugins/keyed/secrets")
        gem = [s for s in body["secrets"] if s["name"] == "GEMINI_API_KEY"][0]
        check("...a refused paste is `refused`, and leaves `checked` (the key in force) alone",
              gem["refused"]["ok"] is False and gem["checked"] is None, gem)
        st, body = await http(port, "PUT", "/v1/plugins/keyed/secrets/GEMINI_API_KEY",
                              {"value": "AIzaGOOD"})
        check("PUT a good key: 200, kept, checked",
              st == 200 and body["source"] == "plugin" and body["checked"]["ok"], body)
        st, body = await http(port, "POST", "/v1/plugins/keyed/secrets/GEMINI_API_KEY/check", {})
        check("POST …/check checks the key in force again", st == 200
              and body["checked"]["ok"], body)
        st, body = await http(port, "GET", "/v1/plugins/keyed/secrets/GEMINI_API_KEY/value")
        check("GET …/value is the app server's, and answers the key",
              st == 200 and body["value"] == "AIzaGOOD", st)
        st, body = await http(port, "PUT", "/v1/plugins/keyed/secrets/GEMINI_API_KEY",
                              {"value": "AIzaGOOD", "by": lc.LEAD_AGENT})
        check("an agent's call is refused (403)", st == 403, body)
        st, body = await http(port, "PUT", "/v1/plugins/keyed/secrets/NOPE", {"value": "x"})
        check("an undeclared secret is a 404", st == 404, body)
        st, body = await http(port, "DELETE", "/v1/plugins/keyed/secrets/GEMINI_API_KEY", {})
        check("DELETE forgets the plugin's own key",
              st == 200 and c.secret_source("keyed", "GEMINI_API_KEY") in (None, "box"), body)
        st, body = await http(port, "PATCH", "/v1/plugins/daily-notes/settings",
                              {"values": {"folder": "Journal"}})
        check("daily notes: off, its settings are refused (on first)", st == 400, body)
        lp.set_on("daily-notes", True)
        st, body = await http(port, "PATCH", "/v1/plugins/daily-notes/settings",
                              {"values": {"folder": "Journal"}})
        check("daily notes: on, the folder setting reads back",
              st == 200 and body["values"]["folder"] == "Journal", body)
    finally:
        tcp.close()
        await tcp.wait_closed()


asyncio.run(routes())
srv.shutdown()
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
