#!/usr/bin/env python3
"""Check which deployments and agent sites appear in the broker's Apps list and how
their URLs are presented.

Run: python3 tests/test_apps.py"""

from __future__ import annotations

import asyncio
import datetime as dt
import importlib.machinery
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_broker as cfg  # noqa: E402
import loam_deploy as dep  # noqa: E402
import loam_apps  # noqa: E402
OWNER = cfg.lc.OWNER_ID

cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}

results: list[bool] = []


def check(label: str, ok: bool, detail="") -> None:
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:66}"
          f"{'  | ' + str(detail)[:200] if detail != '' else ''}")


COMPOSE = """\
name: {name}
services:
  web:
    image: {name}:${{LOAM_COMMIT:?x}}
    container_name: {name}
    ports:
      - "127.0.0.1:{port}:8080"
    logging:
      driver: journald
      options:
        tag: "{{{{.Name}}}}"
"""


class FakeWriter:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.chunks.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    def parsed(self):
        head, _, payload = b"".join(self.chunks).decode().partition("\r\n\r\n")
        lines = head.split("\r\n")
        headers = {k.strip().lower(): v.strip() for k, _, v in
                   (line.partition(":") for line in lines[1:])}
        return int(lines[0].split()[1]), headers, payload


async def request(server, target: str, token: str, method: str = "GET"):
    reader = asyncio.StreamReader()
    reader.feed_data(f"{method} {target} HTTP/1.1\r\n"
                     f"Authorization: Bearer {token}\r\n\r\n".encode())
    reader.feed_eof()
    writer = FakeWriter()
    await server.handle(reader, writer)
    return writer.parsed()


root = Path(tempfile.mkdtemp(prefix="loam-apps-test-"))
names = ("DEPLOY", "CONFIG_PATH", "PUBLIC_FILE", "SHARE_DIR", "AGENTS", "SITES_DIR")
saved = {k: getattr(dep, k) for k in names}
broker_saved = (cfg.STATE_DIR, cfg.SESSION_DIR, cfg.USAGE_PATH, cfg.TOKEN_PATH)
try:
    deploy = root / "deploy"
    deploy.mkdir()
    (deploy / "loam.toml").write_text('domain = "example.test"\nauth_host = "auth"\n')
    dep.DEPLOY, dep.CONFIG_PATH = deploy, deploy / "loam.toml"
    dep.PUBLIC_FILE = root / "public"
    dep.SHARE_DIR = root / "share"
    dep.SHARE_DIR.mkdir()
    dep.AGENTS = root / "agents"
    (dep.AGENTS / "projects").mkdir(parents=True)
    dep.SITES_DIR = root / "sites"
    dep.SITES_DIR.mkdir()
    www = root / "www"
    www.mkdir()
    checkout = root / "checkout"
    checkout.mkdir()

    def record(name: str, toml: str, port: int | None = None) -> None:
        d = deploy / name
        d.mkdir(exist_ok=True)
        (d / "deploy.toml").write_text(toml)
        if port is not None:
            (d / "compose.yaml").write_text(COMPOSE.format(name=name, port=port))

    def site(name: str, body: str) -> None:
        (dep.SITES_DIR / f"{name}.toml").write_text(body)

    record("files", f'exposure = "private"\nstatic_root = "{www}"\n')
    record("vault", f'exposure = "private"\nstatic_root = "{www}"\n')
    record("shell", f'exposure = "private"\napp_shell = true\ncheckout = "{checkout}"\n', 8081)
    record("shell-next", f'exposure = "private"\nhost = "next"\napp_shell = true\n'
                         f'checkout = "{checkout}"\n', 8082)
    record("homepage", f'exposure = "private"\ncheckout = "{checkout}"\n'
                       '[domains."home.test"]\nhosts = ["home.test", "www.home.test"]\n', 8080)
    record("demo", f'exposure = "shared"\ncheckout = "{checkout}"\n'
                   '[share]\nexpires = 2026-12-01\n', 8083)
    site("light", f'owner = "otto"\ncreated = "2026-11-10T11:46:24Z"\nroot = "{www}"\n')
    site("rows", f'owner = "ada"\nroot = "{www}"\nexpires = 2026-11-25\n')
    site("api", 'owner = "ada"\nport = 9001\npublic = "Sam, 2026-11-12: yes, open it"\n')
    site("ingest", 'owner = "ada"\nport = 9580\npublic = "Sam, 2026-11-24: yes, open ingest"\nhidden = true\n')
    site("quiet-static", f'owner = "otto"\nroot = "{www}"\nhidden = true\n')
    # The plugin host's two layers, in the throwaway root: one app plugin
    # and one extension built in Yours, nothing shipped.
    import os  # noqa: PLC0415
    os.environ["LOAM_PLUGINS_DIR"] = str(root / "plugins")
    os.environ["LOAM_PLUGIN_SHIPPED"] = ""
    for pid, kind in (("notes", "app"), ("pet", "extension")):
        d = root / "plugins" / pid
        (d / "server").mkdir(parents=True)
        (d / "server" / "main.py").write_text("print('hi')\n")
        (d / "icon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
        (d / "PLUGIN.md").write_text(f"---\nid: {pid}\nname: {pid.title()}\n"
                                     f"version: 1.0.0\napi: 0\nkind: [{kind}]\n"
                                     "icon: icon.svg\ntouches: {server: true}\n---\n")

    out = loam_apps.listing()
    rows = {r["name"]: r for r in out["apps"]}
    check("sources: yours, installed, loam", out["sources"] == ["yours", "installed", "loam"],
          out["sources"])
    check("the rows: three sites by name, two projects by name, then the app plugin",
          [r["name"] for r in out["apps"]] == ["api", "light", "rows", "demo", "homepage",
                                               "Notes"],
          [r["name"] for r in out["apps"]])
    notes = rows.pop("Notes")
    out["apps"] = [r for r in out["apps"] if r["kind"] != "plugin"]
    check("an app plugin: at /x/<id>/, built here, private, framed, with its icon",
          (notes["url"], notes["kind"], notes["source"], notes["access"], notes["frame"],
           notes["icon"], notes["plugin"], notes["on"])
          == ("/x/notes/", "plugin", "yours", "private", True,
              "/v1/plugins/notes/files/icon.svg", "notes", False), notes)
    check("an extension with a server is not an app row", "Pet" not in rows)
    check("files and vault (runtime static hosts) are not apps",
          "files" not in rows and "vault" not in rows)
    check("an app_shell record (Loam's page, live and staging) is not listed",
          "shell" not in rows and "shell-next" not in rows)
    check("hidden sites (port or static) are omitted from Apps",
          "ingest" not in rows and "quiet-static" not in rows)
    check("hidden sites are omitted from previewable() while visible sites remain",
          {a.name for a in dep.previewable()} == {"api", "demo", "homepage", "light", "rows"})
    check("every site's and project's source is yours",
          {r["source"] for r in out["apps"]} == {"yours"})
    keys = {"name", "url", "also_at", "source", "kind", "owner", "project", "access",
            "expires", "shared_until", "serves", "frame", "created", "icon"}
    check("an app plugin's row has those keys and plugin, on, state",
          set(notes) == keys | {"plugin", "on", "state"}, sorted(set(notes) ^ keys))
    check("every row has the same keys, no more and no fewer",
          all(set(r) == keys for r in out["apps"]),
          [sorted(set(r) ^ keys) for r in out["apps"]])

    light = rows["light"]
    check("a site: url, owner, kind, serves static",
          (light["url"], light["owner"], light["kind"], light["serves"], light["project"])
          == ("https://light.example.test/", "otto", "site", "static", None), light)
    check("a site: private, no expiry, created from its record",
          (light["access"], light["expires"], light["created"])
          == ("private", None, "2026-11-10T11:46:24Z"), light)
    check("a site's expiry is its last day, as a date",
          rows["rows"]["expires"] == "2026-11-25", rows["rows"]["expires"])
    check("a static site may be framed by the page", light["frame"] is True)
    check("a site on a port: container, public on its quoted yes, frame unknown -> false",
          (rows["api"]["serves"], rows["api"]["access"], rows["api"]["frame"])
          == ("container", "public", False), rows["api"])

    home = rows["homepage"]
    check("a project: owned by the owner, marked by its project, a container",
          (home["kind"], home["owner"], home["project"], home["serves"])
          == ("project", OWNER, "homepage", "container"), home)
    check("a project's url is its name under the runtime's domain",
          home["url"] == "https://homepage.example.test/", home["url"])
    check("its own-domain hostnames are also_at",
          home["also_at"] == ["https://home.test/", "https://www.home.test/"], home["also_at"])
    check("a project is not framed by the list's say-so", home["frame"] is False)
    check("a shared record is shared, with its date",
          (rows["demo"]["access"], rows["demo"]["shared_until"]) == ("shared", "2026-12-01"),
          rows["demo"])
    dep.PUBLIC_FILE.write_text("homepage\n")
    check("the operator file makes a project public",
          {r["name"]: r for r in loam_apps.listing()["apps"]}["homepage"]["access"] == "public")
    dep.PUBLIC_FILE.unlink()

    # -- the frame policy Caddy sends --------------------------------------
    text = dep.caddyfile(dt.date(2026, 11, 12))
    blocks = dict(dep._live_blocks(text))
    want = ('header Content-Security-Policy "frame-ancestors \'self\' '
            'https://next.example.test https://shell.example.test"')
    check("a static site is framed by itself and the app_shell origins only",
          want in blocks["light.example.test"].splitlines(), blocks["light.example.test"][:300])
    check("the header is in the block before any handle, so every answer carries it",
          blocks["rows.example.test"].index(want) < blocks["rows.example.test"].index("handle"))
    check("a port site gets no frame policy from Caddy",
          "frame-ancestors" not in blocks["api.example.test"])
    for host in ("homepage.example.test", "home.test", "shell.example.test",
                 "next.example.test", "files.example.test"):
        check(f"no frame policy on a record's host: {host}",
              "frame-ancestors" not in blocks[host])
    check("the sites stay gated with the header added",
          dict(dep.live_host_blocks(text))["light.example.test"] is True)
    (deploy / "shell" / "deploy.toml").write_text(
        f'exposure = "private"\ncheckout = "{checkout}"\n')
    (deploy / "shell-next" / "deploy.toml").write_text(
        f'exposure = "private"\nhost = "next"\ncheckout = "{checkout}"\n')
    text = dep.caddyfile(dt.date(2026, 11, 12))
    check("with no app_shell record a static site is framed by itself only",
          'header Content-Security-Policy "frame-ancestors \'self\'"'
          in dict(dep._live_blocks(text))["light.example.test"].splitlines())
    check("...and the list says it cannot be framed",
          {r["name"]: r for r in loam_apps.listing()["apps"]}["light"]["frame"] is False)
    record("shell", f'exposure = "private"\napp_shell = "yes"\ncheckout = "{checkout}"\n')
    try:
        dep.records()
        check("app_shell must be a boolean", False, "accepted")
    except dep.DeployError as exc:
        check("app_shell must be a boolean", "true or false" in str(exc), str(exc))
    record("shell", f'exposure = "private"\napp_shell = true\nstatic_root = "{www}"\n')
    try:
        dep.records()
        check("app_shell on a static host is refused", False, "accepted")
    except dep.DeployError as exc:
        check("app_shell on a static host is refused", "container" in str(exc), str(exc))
    record("shell", f'exposure = "private"\napp_shell = true\ncheckout = "{checkout}"\n')
    (deploy / "shell" / "compose.yaml").write_text(COMPOSE.format(name="shell", port=8081))

    # -- the route ---------------------------------------------------------
    loader = importlib.machinery.SourceFileLoader(
        "session_broker", str(AGENTS / "bin" / "session-broker"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    broker_mod = importlib.util.module_from_spec(spec)
    loader.exec_module(broker_mod)
    btmp = root / "broker"
    btmp.mkdir()
    cfg.STATE_DIR, cfg.SESSION_DIR = btmp, btmp / "sessions"
    cfg.USAGE_PATH, cfg.TOKEN_PATH = btmp / "provider-usage.json", btmp / "token"
    server = broker_mod.Server(broker_mod.Broker())
    token = server.broker.token

    async def route_cases() -> None:
        status, headers, payload = await request(server, "/v1/apps", token)
        body = json.loads(payload)
        check("GET /v1/apps: 200", status == 200, status)
        check("GET /v1/apps: no-store", headers.get("cache-control") == "no-store",
              headers.get("cache-control"))
        check("the route answers what listing() does", body == loam_apps.listing(),
              payload[:200])
        print("--- a 200 body's first row, verbatim ---\n"
              + json.dumps(body["apps"][0], indent=2) + "\n---")
        status, _, _ = await request(server, "/v1/apps", "wrong")
        check("no token: 401", status == 401, status)
        for method in ("POST", "PATCH", "DELETE", "PUT"):
            status, _, _ = await request(server, "/v1/apps", token, method)
            check(f"{method}: 405, the list is read-only", status == 405, status)
        status, _, _ = await request(server, "/v1/apps/light", token)
        check("a longer path is not this route", status != 200, status)
        site("bad", 'owner = "ada"\nroot = "relative"\n')
        status, _, payload = await request(server, "/v1/apps", token)
        check("an unreadable site record: 503 naming it",
              status == 503 and "bad.toml" in json.loads(payload)["error"], payload[:200])
        (dep.SITES_DIR / "bad.toml").unlink()

    asyncio.run(route_cases())
finally:
    for k, v in saved.items():
        setattr(dep, k, v)
    (cfg.STATE_DIR, cfg.SESSION_DIR, cfg.USAGE_PATH, cfg.TOKEN_PATH) = broker_saved
    shutil.rmtree(root, ignore_errors=True)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
