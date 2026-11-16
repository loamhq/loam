#!/usr/bin/env python3
"""Server plugins and the one package shape (2026-11-13):
lib/loam_plugin_servers.py, the kinds and fields lib/loam_plugins.py gained,
and bin/plugin-gateway.

  - kinds: theme, extension, app, agent, skill; an agent or skill is a
    package alone; an app with no server/ is broken; an agent carrying a
    memory folder or a MEMORY.md, at any depth, is broken; a skill without
    SKILL.md is a warning; agents and skills are listed with switch
    "listed", never on, their PATCH a 409, and an install's preview says
    `installable: false` and its confirm installs nothing
  - `server: {run}`: read; a string is the same; a second line or no run is
    refused; a missing server/ or nothing to run is a warning; the default
    commands are server/main.py and server/main.js; `icon` kept when it is
    a picture in the folder, else a warning, and served by the files route
  - the supervisor, against a fake systemd: on starts one transient unit
    with its port, memory cap, NoNewPrivileges, restart burst and the
    plugin's environment (no token, only its file); off stops it; remove
    stops it before the folder goes; an update restarts it fresh; a running
    unit is not restarted by a second start; states from systemctl's
    properties (running, starting, failing on failed or auto-restart,
    stopped); a failing row carries its log; the port is kept across
    restarts; reconcile starts the wanted unit that is not loaded, leaves a
    failed one alone and stops an unwanted one; with Yours elsewhere and no
    LOAM_PLUGIN_UNITS nothing is ever started
  - the gateway, on loopback against a real upstream: /x/<id>/path reaches
    the plugin as /path with X-Forwarded-Prefix and Connection: close, the
    query kept; /x/<id> is a 308 to /x/<id>/; an unknown id is a 404; a
    plugin whose port answers nothing is a 502 that says so; a keep-alive
    client gets two answers from two plugins on separate connections

Run: python3 tests/test_plugin_servers.py
"""

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="loam-plugin-servers-test-"))
YOURS = TMP / "plugins"
os.environ["LOAM_VAULT"] = str(TMP / "vault")
os.environ["LOAM_AI_LABS"] = str(TMP / "ai-labs")
os.environ["LOAM_HOST_SETTINGS"] = str(TMP / "broker" / "settings.json")
os.environ["LOAM_PLUGIN_SHIPPED"] = ""
os.environ["LOAM_PLUGINS_DIR"] = str(YOURS)
os.environ["LOAM_PLUGIN_STATE"] = str(TMP / "state")
os.environ["LOAM_UI_CORE_OPTIONS"] = "none"
os.environ.pop("LOAM_PLUGIN_UNITS", None)

sys.path.insert(0, str(AGENTS / "lib"))
import loam_plugins as P  # noqa: E402
import loam_plugin_servers as S  # noqa: E402

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:72}"
          f"{'  | ' + str(detail)[:300] if detail != '' else ''}")


def refuses(fn, says):
    try:
        fn()
    except (P.ManifestError, P.PluginError) as exc:
        return says in str(exc), str(exc)
    return False, "no refusal"


def plugin(pid, kind, extra="", files=None, root=YOURS):
    d = root / pid
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    (d / "PLUGIN.md").write_text(f"---\nid: {pid}\nname: {pid.title()}\nversion: 1.0.0\n"
                                 f"api: 0\nkind: [{kind}]\n{extra}---\n\n# {pid}\n")
    for rel, text in (files or {}).items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(text)
    return d


def row(pid):
    return next(r for r in P.listing()["plugins"] if r["id"] == pid)


def manifest(text):
    data, _ = P.parse_frontmatter("---\n" + text + "---\n")
    return P.manifest_of(data, None)


# ---------------------------------------------------------------------------
# Kinds and fields
# ---------------------------------------------------------------------------

base = "id: x\nname: X\nversion: 1.0.0\napi: 0\n"
for kind in ("theme", "extension", "app", "agent", "skill"):
    m, _ = manifest(base + f"kind: [{kind}]\n")
    check(f"kind {kind} is read", m["kind"] == [kind], m["kind"])
check("an agent is a package alone",
      *refuses(lambda: manifest(base + "kind: [agent, extension]\n"), "an agent package is that alone"))
check("a skill is a package alone",
      *refuses(lambda: manifest(base + "kind: [theme, skill]\n"), "a skill package is that alone"))
check("an unknown kind names the five",
      *refuses(lambda: manifest(base + "kind: [widget]\n"), "theme, extension, app, agent or skill"))
m, w = manifest(base + "kind: [extension]\nserver: {run: node srv/index.js}\n")
check("server.run is read", m["server"] == {"run": "node srv/index.js"}, m.get("server"))
m, w = manifest(base + "kind: [extension]\nserver: python3 -m app\n")
check("server as a string is server.run", m["server"] == {"run": "python3 -m app"})
check("server with no run is refused",
      *refuses(lambda: manifest(base + "kind: [extension]\nserver: {port: 3}\n"), "server.run"))
check("server.run of two lines is refused",
      *refuses(lambda: manifest(base + 'kind: [extension]\nserver: {run: "a\\nb"}\n'), "server.run"))
m, w = manifest(base + "kind: [extension]\nserver: {run: x, memory: 1G}\n")
check("another server field is a warning, kept", m["server"].get("memory") == "1G"
      and any("server.memory" in x for x in w), w)

plugin("notes", "app", "icon: assets/icon.svg\n",
       {"server/main.py": "print('x')\n", "assets/icon.svg": "<svg/>"})
r = row("notes")
check("an app with server/ is listed, built here, off", (r["broken"], r["switch"], r["on"])
      == (None, "plugin", False), r.get("broken"))
check("its icon is kept", r.get("icon") == "assets/icon.svg", r.get("icon"))
path, ctype = P.file_for("notes", ["assets", "icon.svg"])
check("the files route serves the icon", ctype == "image/svg+xml", ctype)
check("server/ is still never served",
      *refuses(lambda: P.file_for("notes", ["server", "main.py"]), "nothing else"))
check("server_spec defaults to python3 server/main.py",
      P.server_spec(r) == {"run": "python3 server/main.py", "default": True}, P.server_spec(r))
plugin("bad-icon", "extension", "icon: ../x.png\n", {"page/main.js": ""})
check("an icon out of the folder is a warning and left out",
      "icon" not in row("bad-icon") and any("icon" in x for x in row("bad-icon")["warnings"]))
plugin("pageapp", "app", "", {"page/main.js": "export function activate(){}\n"})
check("an app with no server/ is broken", "no server/ to run" in (row("pageapp")["broken"] or ""),
      row("pageapp")["broken"])
plugin("nodeish", "extension", "", {"server/main.js": "//\n"})
check("server/main.js runs with node", P.server_spec(row("nodeish"))["run"] == "node server/main.js")
plugin("nothing", "extension", "", {"server/lib.py": "#\n"})
check("server/ with nothing to run: a warning and no spec",
      P.server_spec(row("nothing")) is None
      and any("nothing is run" in x for x in row("nothing")["warnings"]))
plugin("runless", "extension", "server: {run: python3 x.py}\n", {"page/main.js": ""})
check("server.run with no server/ folder: a warning",
      any("no server/ folder" in x for x in row("runless")["warnings"]))

plugin("ada", "agent", "", {"soul.md": "# Ada\n", "skills/write/SKILL.md": "x"})
r = row("ada")
check("an agent package with no memory is listed", r["broken"] is None, r["broken"])
check("...with switch listed, never on", (r["switch"], r["on"]) == ("listed", False))
check("...and no server, even with server/", P.server_spec({**r, "has": {"server": True}}) is None)
check("...its PATCH is a 409 that says why",
      *refuses(lambda: P.set_on("ada", True), "installs or runs none yet"))
plugin("iris", "agent", "", {"soul.md": "x", "memory/2026-11-01.md": "remembered"})
check("an agent carrying memory/ is broken",
      "never carries its memory" in (row("iris")["broken"] or ""), row("iris")["broken"])
plugin("loom", "agent", "", {"soul.md": "x", "notes/deep/MEMORY.md": "remembered"})
check("...and one carrying a MEMORY.md at any depth",
      "notes/deep/MEMORY.md" in (row("loom")["broken"] or ""), row("loom")["broken"])
plugin("writer", "skill", "", {})
check("a skill without SKILL.md: a warning", any("SKILL.md" in x for x in row("writer")["warnings"]))
plugin("writer", "skill", "", {"SKILL.md": "---\nname: writer\n---\n"})
check("a skill with SKILL.md is listed, switch listed",
      (row("writer")["broken"], row("writer")["switch"], row("writer")["warnings"])
      == (None, "listed", []))

# An agent package's install: previewed, never installed.
pkg = plugin("pax", "agent", "", {"soul.md": "x"}, root=TMP / "repo")
real_fetch = P.fetch
P.fetch = lambda src, into: {"folder": pkg, "source": {
    "kind": "link", "repo": "a/b", "path": "", "ref": "v1", "ref_kind": "tag",
    "commit": "0" * 40, "url": "https://github.com/a/b"}}
try:
    prev = P.install({"url": "a/b", "ref": "v1"})
    check("an agent package's preview: installable false, with why",
          prev.get("installable") is False and "installs none yet" in prev.get("why", ""), prev)
    ok, said = refuses(lambda: P.install({"url": "a/b", "ref": "v1",
                                          "confirm": prev["confirm"]}), "Nothing was installed")
    check("...its confirm installs nothing", ok and not (YOURS / "pax").exists(), said)
finally:
    P.fetch = real_fetch


# ---------------------------------------------------------------------------
# The supervisor, against a fake systemd
# ---------------------------------------------------------------------------

class FakeSystemd:
    def __init__(self):
        self.units = {}        # unit -> props
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        out, rc = "", 0
        if args[0] == "systemd-run":
            name = next(a.split("=", 1)[1] for a in args if a.startswith("--unit="))
            self.units[name] = {"LoadState": "loaded", "ActiveState": "active",
                                "SubState": "running", "MainPID": "4242", "NRestarts": "0",
                                "Result": "success", "ActiveEnterTimestamp": "now",
                                "ExecMainStatus": "0"}
        elif args[:3] == ["systemctl", "--user", "show"]:
            names = [a for a in args[3:] if a.endswith(".service")]
            blocks = []
            for n in names:
                props = self.units.get(n, {"LoadState": "not-found", "ActiveState": "inactive",
                                           "SubState": "dead", "MainPID": "0",
                                           "NRestarts": "0", "Result": "success",
                                           "ActiveEnterTimestamp": "", "ExecMainStatus": "0"})
                blocks.append("\n".join(f"{k}={v}" for k, v in props.items()))
            out = "\n\n".join(blocks) + "\n"
        elif args[:3] == ["systemctl", "--user", "stop"]:
            u = self.units.get(args[3])
            if u and u["ActiveState"] != "failed":
                self.units.pop(args[3])
        elif args[:3] == ["systemctl", "--user", "reset-failed"]:
            self.units.pop(args[3], None)
        elif args[:3] == ["systemctl", "--user", "list-units"]:
            out = "".join(f"{n} {p['LoadState']} {p['ActiveState']} {p['SubState']} x\n"
                          for n, p in self.units.items())
        elif args[0] == "journalctl":
            out = "Traceback (most recent call last):\nValueError: boom\n"
        return type("Done", (), {"returncode": rc, "stdout": out, "stderr": ""})()

    def started(self):
        return [a for a in self.calls if a[0] == "systemd-run"]


fake = FakeSystemd()
S.run = fake

# Nothing is managed when Yours is not the box's own folder.
P.set_on("notes", True)
check("with Yours elsewhere and no LOAM_PLUGIN_UNITS, nothing starts", fake.started() == [])
check("...and its row says unmanaged", row("notes")["service"]["state"] == "unmanaged",
      row("notes").get("service"))

os.environ["LOAM_PLUGIN_UNITS"] = "1"
P.set_on("notes", False)
fake.calls.clear()
P.set_on("notes", True)
runs = fake.started()
check("on: one transient unit is started", len(runs) == 1, fake.calls)
argv = runs[0] if runs else []
env = {a.split("=", 1)[1].split("=", 1)[0]: a.split("=", 2)[2] for a in argv
       if a.startswith("--setenv=")}
props = {a.split("=", 1)[1].split("=", 1)[0]: a.split("=", 2)[2] for a in argv
         if a.startswith("--property=")}
check("...named loam-plugin-notes.service", "--unit=loam-plugin-notes.service" in argv)
check("...capped at 512M, no swap, 256 tasks",
      (props.get("MemoryMax"), props.get("MemorySwapMax"), props.get("TasksMax"))
      == ("512M", "0", "256"), props)
check("...NoNewPrivileges, Restart=always, 5 starts in 60 s",
      (props.get("NoNewPrivileges"), props.get("Restart"), props.get("StartLimitBurst"),
       props.get("StartLimitIntervalSec")) == ("yes", "always", "5", "60"), props)
check("...in its own folder", props.get("WorkingDirectory") == str(YOURS / "notes"))
check("...run by sh -c, exec'd", argv[-4:] == ["--", "/bin/sh", "-c",
                                               "exec python3 server/main.py"], argv[-4:])
port = int(env.get("PORT", 0))
check("...a port in 9100-9299, the same in LOAM_PLUGIN_PORT",
      9100 <= port < 9300 and env.get("LOAM_PLUGIN_PORT") == str(port), env.get("PORT"))
check("...its id, folder, base, data and settings",
      (env.get("LOAM_PLUGIN_ID"), env.get("LOAM_PLUGIN_DIR"), env.get("LOAM_PLUGIN_BASE"),
       env.get("LOAM_PLUGIN_DATA"), env.get("LOAM_PLUGIN_SETTINGS"))
      == ("notes", str(YOURS / "notes"), "/x/notes/", str(YOURS / "_data" / "notes"),
          str(YOURS / "_settings" / "notes" / "settings.md")), env)
check("...the data and settings folders exist",
      (YOURS / "_data" / "notes").is_dir() and (YOURS / "_settings" / "notes").is_dir())
check("...the broker's URL and its token's file, never the token",
      env.get("LOAM_BROKER_URL", "").startswith("http://")
      and env.get("LOAM_BROKER_TOKEN_FILE", "").endswith("/token")
      and not any("TOKEN=" in a and "FILE" not in a for a in argv), env)
svc = row("notes")["service"]
check("the row's service: running, wanted, its port, address and pid",
      (svc["state"], svc["wanted"], svc["port"], svc["url"], svc["pid"])
      == ("running", True, port, "/x/notes/", 4242), svc)
check("a running row carries no log unless asked", "log" not in svc)
check("one() carries the log", "log" in P.one("notes")["plugin"]["service"])

fake.calls.clear()
S.start("notes", P.wanted_servers()["notes"])
check("a second start of a running unit does nothing", fake.started() == [], fake.calls)
fake.calls.clear()
P.restart_server("notes")
check("restart: stopped, then started fresh",
      [a[2] for a in fake.calls if a[0] == "systemctl" and a[2] in ("stop", "reset-failed")]
      == ["stop", "reset-failed"] and len(fake.started()) == 1, fake.calls)
check("...on the same port", S.read_ports()["notes"] == port)

for props_, want in ((dict(ActiveState="failed", SubState="failed"), "failing"),
                     (dict(ActiveState="activating", SubState="auto-restart"), "failing"),
                     (dict(ActiveState="activating", SubState="start"), "starting"),
                     (dict(ActiveState="active", SubState="running"), "running"),
                     (dict(LoadState="not-found"), "stopped")):
    got = S._state({"LoadState": "loaded", **props_})
    check(f"state of {props_}: {want}", got == want, got)

fake.units["loam-plugin-notes.service"].update(ActiveState="failed", SubState="failed",
                                               NRestarts="5", MainPID="0",
                                               Result="exit-code")
svc = row("notes")["service"]
check("a failing row: state failing, restarts and its last log lines",
      (svc["state"], svc["restarts"], svc["log"][-1:]) == ("failing", 5, ["ValueError: boom"]),
      svc)

P.set_on("notes", False)
check("off: the unit is gone, the row says stopped and not wanted",
      "loam-plugin-notes.service" not in fake.units
      and (row("notes")["service"]["state"], row("notes")["service"]["wanted"])
      == ("stopped", False), row("notes").get("service"))
P.set_on("notes", True)
check("on again: running", row("notes")["service"]["state"] == "running")

# remove stops the unit before the folder goes
order = []
real_stop = S.stop
S.stop = lambda pid: (order.append(("stop", (YOURS / pid).exists())), real_stop(pid))[1]
try:
    P.remove("notes")
finally:
    S.stop = real_stop
check("remove: its server stops while its folder is still there",
      order[:1] == [("stop", True)] and "loam-plugin-notes.service" not in fake.units, order)

# reconcile
plugin("echo", "app", "", {"server/main.py": "#\n"})
plugin("other", "extension", "", {"server/main.py": "#\n"})
P.set_on("echo", True)
P.set_on("other", True)
fake.units.clear()                                  # as after a reboot
fake.units["loam-plugin-other.service"] = {"LoadState": "loaded", "ActiveState": "failed",
                                           "SubState": "failed", "MainPID": "0",
                                           "NRestarts": "5", "Result": "exit-code",
                                           "ActiveEnterTimestamp": "", "ExecMainStatus": "1"}
fake.units["loam-plugin-gone.service"] = dict(fake.units["loam-plugin-other.service"],
                                              ActiveState="active", SubState="running")
fake.calls.clear()
out = S.reconcile()
check("reconcile starts the wanted unit that is not loaded", out["started"] == ["echo"], out)
check("...leaves a failed one alone", "other" not in out["started"]
      and fake.units["loam-plugin-other.service"]["ActiveState"] == "failed")
check("...and stops one no longer wanted", out["stopped"] == ["gone"]
      and "loam-plugin-gone.service" not in fake.units, out)

# an update restarts only that unit
fake.calls.clear()
before = dict(fake.units)
P._servers_follow("echo", restart=True)
check("an update restarts its unit fresh, and no other",
      [a for a in fake.started()] and all("--unit=loam-plugin-echo.service" in a
                                          for a in fake.started())
      and "loam-plugin-other.service" in fake.units, fake.calls)
os.environ.pop("LOAM_PLUGIN_UNITS")


# ---------------------------------------------------------------------------
# The gateway, on loopback
# ---------------------------------------------------------------------------

seen = []


class Upstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802
        seen.append({"path": self.path, "prefix": self.headers.get("X-Forwarded-Prefix"),
                     "connection": self.headers.get("Connection")})
        body = json.dumps({"path": self.path, "port": self.server.server_port}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


ups = []
for _ in range(2):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    ups.append(srv)
dead = free_port()
(TMP / "state").mkdir(exist_ok=True)
(TMP / "state" / "ports.json").write_text(json.dumps({"ports": {
    "echo": ups[0].server_port, "other": ups[1].server_port, "down": dead}}))

loader = importlib.machinery.SourceFileLoader("plugin_gateway", str(AGENTS / "bin" / "plugin-gateway"))
spec = importlib.util.spec_from_loader("plugin_gateway", loader)
gw = importlib.util.module_from_spec(spec)
loader.exec_module(gw)
gport = free_port()


def serve():
    async def main():
        server = await asyncio.start_server(gw.handle, "127.0.0.1", gport, limit=gw.HEAD_MAX)
        async with server:
            await server.serve_forever()
    asyncio.run(main())


threading.Thread(target=serve, daemon=True).start()
for _ in range(50):
    try:
        socket.create_connection(("127.0.0.1", gport), 0.2).close()
        break
    except OSError:
        time.sleep(0.05)

import http.client  # noqa: E402

c = http.client.HTTPConnection("127.0.0.1", gport, timeout=5)
c.request("GET", "/x/echo/hello/world?a=1&b=2")
resp = c.getresponse()
body = json.loads(resp.read())
check("/x/echo/hello/world?a=1&b=2 reaches the plugin as /hello/world?a=1&b=2",
      resp.status == 200 and body["path"] == "/hello/world?a=1&b=2", body)
check("...with X-Forwarded-Prefix /x/echo and Connection: close",
      (seen[-1]["prefix"], seen[-1]["connection"]) == ("/x/echo", "close"), seen[-1])
c.close()

c = http.client.HTTPConnection("127.0.0.1", gport, timeout=5)
c.request("GET", "/x/echo/")
a = json.loads(c.getresponse().read())
try:
    c.request("GET", "/x/other/two")
    b = json.loads(c.getresponse().read())
except (http.client.HTTPException, OSError):
    c.close()
    c = http.client.HTTPConnection("127.0.0.1", gport, timeout=5)
    c.request("GET", "/x/other/two")
    b = json.loads(c.getresponse().read())
check("two requests to two plugins each reach their own",
      (a["port"], a["path"], b["port"], b["path"])
      == (ups[0].server_port, "/", ups[1].server_port, "/two"), (a, b))
c.close()

for path, status, says in (("/x/echo", 308, "/x/echo/"),
                           ("/x/ghost/", 404, "no plugin 'ghost'"),
                           ("/elsewhere", 404, "/x/<id>/"),
                           ("/x/down/", 502, "not answering")):
    c = http.client.HTTPConnection("127.0.0.1", gport, timeout=8)
    c.request("GET", path)
    r = c.getresponse()
    text = r.read().decode()
    loc = r.getheader("Location")
    check(f"{path}: {status}", r.status == status and says in (text + (loc or "")),
          (r.status, text.strip(), loc))
    c.close()

for s in ups:
    s.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
