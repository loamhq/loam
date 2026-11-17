#!/usr/bin/env python3
"""Comments & suggestions (plugins/doc-comments, 2026-11-16): the plugin
through Loam's real interfaces, and the core hooks it added.

  - the plugin's own suites: its model (plugins/doc-comments/test/
    test_model.py: anchors, merge, sidecar, the operations) and its editor's
    text handling (test/editor.test.mjs: byte-for-byte round trips through the
    editor's state, CRLF, positions between server and page)
  - against the real session broker over HTTP, on a scratch vault: a
    persona's first note writes one id line and the broker's provenance stamp
    names that persona (`by`, a core hook), the sidecar is made under
    `<system>/Loam/data/doc-comments/`, a save made for an unknown persona is
    refused, and the notes follow a rename done through Files
  - the plugin's HTTP server and its agent tool (stdio MCP) end to end: a
    persona comments by quote and lists the doc's notes, under its own name
  - the kept folder: `<system>/Loam/data/` survives the library being
    replaced, is never counted as an edit to it, is not copied into an
    update's backup, and is kept as it is now when an update is undone
  - an agent tool is told its agent, lane, the vault, the workspaces and its
    plugin server's address; a plugin server is told its vault folder
  - plugin off: no contribution, no server wanted, no tool; and the page
    module registers nothing on a page without `files.own` and takes its one
    registration away when disposed

Run: python3 tests/test_doc_comments.py
"""

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "doc-comments"
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(PLUGIN / "server"))

import loam_broker as cfg  # noqa: E402
cfg.memory = lambda: {"available": None, "cgroup_room": None, "reserve": cfg.MEMORY_RESERVE_MB}
import loam_config as lc  # noqa: E402
import loam_files as files  # noqa: E402
import loam_skills as sk  # noqa: E402

results = []


def check(label, got, want):
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:76} got={str(got)[:110]!r}")
    if not ok:
        print(f"      wanted {want!r}")


# -- the plugin's own suites ------------------------------------------------------

def test_suites():
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    r = subprocess.run([sys.executable, str(PLUGIN / "test" / "test_model.py")],
                       capture_output=True, text=True, env=env, timeout=300)
    tail = (r.stdout + r.stderr).strip().splitlines()[-1:] or [""]
    check("the model's suite passes (anchors, merge, sidecar, operations)", (r.returncode, tail[0]), (0, "OK"))
    if r.returncode:
        print(r.stdout[-3000:], r.stderr[-3000:])
    node = shutil.which("node")
    if not node:
        check("node is here for the editor's suite", False, True)
        return
    r = subprocess.run([node, "--test", str(PLUGIN / "test" / "editor.test.mjs")],
                       capture_output=True, text=True, timeout=300)
    check("the editor's suite passes (round trips byte for byte, positions)", r.returncode, 0)
    if r.returncode:
        print(r.stdout[-3000:], r.stderr[-3000:])
    r = subprocess.run([sys.executable, str(PLUGIN / "test" / "test_share.py")],
                       capture_output=True, text=True, env=env, timeout=600)
    tail = (r.stdout + r.stderr).strip().splitlines()[-1:] or [""]
    check("the share door's suite passes (modes, throttling, off, one doc, text, agents)", (r.returncode, tail[0]), (0, "OK"))
    if r.returncode:
        print(r.stdout[-3000:], r.stderr[-3000:])
    r = subprocess.run([node, "--test", str(PLUGIN / "test" / "page.test.mjs")],
                       capture_output=True, text=True, timeout=300)
    check("the page module registers nothing without files.own, and cleans up", r.returncode, 0)
    if r.returncode:
        print(r.stdout[-3000:], r.stderr[-3000:])
    # The editor in a real browser (positions, typing, Suggesting): skipped
    # unless LOAM_PLAYWRIGHT names a Playwright install (the plugin's README).
    r = subprocess.run([node, "--test", "--test-timeout=120000", str(PLUGIN / "test" / "browser.test.mjs")],
                       capture_output=True, text=True, timeout=900)
    check("the editor in a browser (or skipped without Playwright)", r.returncode, 0)
    if r.returncode:
        print(r.stdout[-3000:], r.stderr[-3000:])
    r = subprocess.run([sys.executable, str(PLUGIN / "test" / "test_places.py")],
                       capture_output=True, text=True, env=env, timeout=600)
    tail = (r.stdout + r.stderr).strip().splitlines()[-1:] or [""]
    check("where a note sits after the text changes (carried, seeded)", (r.returncode, tail[0]), (0, "OK"))
    if r.returncode:
        print(r.stdout[-3000:], r.stderr[-3000:])
    r = subprocess.run([node, "--test", str(PLUGIN / "test" / "page.test.mjs")],
                       capture_output=True, text=True, timeout=300)
    check("the page module registers nothing without files.own, and cleans up", r.returncode, 0)
    if r.returncode:
        print(r.stdout[-3000:], r.stderr[-3000:])
    # The editor in a real browser (positions, typing, Suggesting): skipped
    # unless LOAM_PLAYWRIGHT names a Playwright install (the plugin's README).
    r = subprocess.run([node, "--test", "--test-timeout=120000", str(PLUGIN / "test" / "browser.test.mjs")],
                       capture_output=True, text=True, timeout=900)
    check("the editor in a browser (or skipped without Playwright)", r.returncode, 0)
    if r.returncode:
        print(r.stdout[-3000:], r.stderr[-3000:])


# -- a real broker on a scratch vault ------------------------------------------------

def load_broker():
    loader = importlib.machinery.SourceFileLoader("session_broker", str(ROOT / "bin" / "session-broker"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


async def serve(server, ready):
    srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
    ready["port"] = srv.sockets[0].getsockname()[1]
    ready["loop"] = asyncio.get_running_loop()
    ready["event"].set()
    async with srv:
        await ready["stop"]


ESSAY = "---\ntags: [essay]\n---\n\n# A walk\n\nThe river was high that spring. We walked anyway.\n"


def test_with_broker(tmp: Path):
    from doccomments import frontmatter as fm
    from doccomments.service import Service
    from doccomments.sidecar import NoteError
    from doccomments.store import BrokerStore
    import main as plugin_server

    broker_mod = load_broker()
    vault, labs = tmp / "vault", tmp / "labs"
    (vault / "Essays").mkdir(parents=True)
    (vault / "Essays" / "walk.md").write_text(ESSAY)
    (labs / "ada").mkdir(parents=True)
    (labs / "ada" / "agent.json").write_text(json.dumps({"display": "Ada"}))
    keep = (cfg.STATE_DIR, cfg.SESSION_DIR, cfg.USAGE_PATH, cfg.TOKEN_PATH, cfg.VAULT, cfg.AI_LABS)
    cfg.STATE_DIR = tmp / "state"
    cfg.SESSION_DIR = tmp / "state" / "sessions"
    cfg.USAGE_PATH = tmp / "state" / "usage.json"
    cfg.TOKEN_PATH = tmp / "state" / "token"
    cfg.VAULT, cfg.AI_LABS = vault, labs
    server = broker_mod.Server(broker_mod.Broker())
    ready = {"event": threading.Event()}

    def run():
        loop = asyncio.new_event_loop()
        ready["stop"] = loop.create_future()
        loop.run_until_complete(serve(server, ready))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    ready["event"].wait(10)
    token_file = tmp / "token"
    token_file.write_text(server.broker.token)
    data_dir = f"{lc.vault_folder('system')}/Loam/data/doc-comments"
    try:
        store = BrokerStore(f"http://127.0.0.1:{ready['port']}", str(token_file), data_dir)
        svc = Service(store)
        ada = store.agent("ada")
        check("the broker's people: the owner and the persona, by name",
              (store.owner()["kind"], ada and ada["name"]), ("owner", "Ada"))
        P = ("vault", "Essays/walk.md")
        got = svc.get(store.owner(), *P)
        check("a doc nobody commented on: no id, no notes, nothing written",
              (got["id"], got["notes"], (vault / "Essays" / "walk.md").read_text()), (None, [], ESSAY))
        out = svc.add(ada, *P, {"kind": "comment", "quote": "The river was high", "text": "Which river?"})
        text = (vault / "Essays" / "walk.md").read_text()
        did = fm.doc_id(text)
        check("the first note writes the id line, stamped as the persona (core `by`)",
              text, ESSAY.replace("tags: [essay]\n", f"tags: [essay]\nloam_doc_id: {did}\nlast_modified_by: \"[[Ada]]\"\n"))
        side = vault / data_dir / f"{did}.json"
        check("the sidecar is under <system>/Loam/data/doc-comments/", side.is_file(), True)
        sc = json.loads(side.read_text())
        check("the sidecar is readable JSON naming the doc and the note",
              (sc["format"], sc["doc"], sc["threads"][0]["author"]["id"], sc["threads"][0]["text"]),
              ("loam-doc-notes", {"root": "vault", "path": "Essays/walk.md"}, "ada", "Which river?"))
        check("the note points at its passage", out["notes"][0]["where"]["state"], "attached")
        try:
            files.save_text({"root": "vault", "path": "Essays/walk.md", "text": text + "x",
                             "base_revision": files.revision(text.encode()), "by": "nobody"})
            refused = None
        except files.FilesError as exc:
            refused = exc.status
        check("a save made for someone who is not a persona is refused", refused, 400)
        # Rename through Files: the notes follow, by the id in the doc.
        (vault / "Published").mkdir()
        files.rename({"root": "vault", "path": "Essays/walk.md", "to": "Published/A walk.md"})
        got = svc.get(store.owner(), "vault", "Published/A walk.md")
        check("after a rename through Files the notes follow the doc",
              (len(got["notes"]), got["notes"][0]["where"]["state"]), (1, "attached"))
        check("...and the sidecar records the new path",
              json.loads(side.read_text())["doc"]["path"], "Published/A walk.md")
        # The plugin's HTTP server and the agent tool.
        httpd = plugin_server.serve(svc, 0)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            env = {**os.environ, "LOAM_PLUGIN_URL": f"http://127.0.0.1:{port}/", "LOAM_AGENT": "ada",
                   "LOAM_VAULT": str(vault), "LOAM_WORKSPACES": str(labs)}
            msgs = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "doc_suggest", "arguments": {
                    "path": str(vault / "Published" / "A walk.md"), "quote": "We walked anyway.",
                    "replacement": "We walked all the same.", "text": "Softer."}}},
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "doc_notes", "arguments": {
                    "path": "Published/A walk.md"}}},
            ]
            r = subprocess.run([sys.executable, str(PLUGIN / "tools" / "notes_mcp.py")],
                               input="\n".join(json.dumps(m) for m in msgs) + "\n",
                               capture_output=True, text=True, env=env, timeout=60)
            answers = {a["id"]: a for a in map(json.loads, r.stdout.splitlines())}
            names = sorted(t["name"] for t in answers[2]["result"]["tools"])
            check("the tool lists its eight operations", names,
                  sorted(["doc_notes", "doc_comment", "doc_suggest", "doc_reply", "doc_resolve",
                          "doc_reopen", "doc_accept", "doc_reject"]))
            check("a persona suggests by quote through the tool (absolute path)",
                  answers[3]["result"].get("isError"), None)
            listing = answers[4]["result"]["content"][0]["text"]
            check("doc_notes shows both notes, each under its author's name",
                  ("comment by Ada" in listing, "suggestion by Ada" in listing, "We walked all the same." in listing),
                  (True, True, True))
            doc = (vault / "Published" / "A walk.md").read_text()
            check("a suggestion leaves the doc's text as it was", "We walked anyway." in doc, True)
        finally:
            httpd.shutdown()
    finally:
        ready["loop"].call_soon_threadsafe(ready["stop"].set_result, None)
        thread.join(10)
        (cfg.STATE_DIR, cfg.SESSION_DIR, cfg.USAGE_PATH, cfg.TOKEN_PATH, cfg.VAULT, cfg.AI_LABS) = keep


# -- the kept folder across an update ---------------------------------------------------

def test_kept(tmp: Path):
    import loam_update as up
    lib = tmp / "library"
    (lib / "skills" / "one").mkdir(parents=True)
    (lib / "README.md").write_text("library\n")
    (lib / "skills" / "one" / "SKILL.md").write_text("---\nname: one\n---\n")
    vault = tmp / "kv"
    dest = sk.loam_dir(vault)
    sk.install_library(lib, dest=dest)
    (dest / sk.KEPT_DIR / "doc-comments").mkdir(parents=True)
    note = dest / sk.KEPT_DIR / "doc-comments" / "d-aaaaaaaaaa.json"
    note.write_text('{"notes": 1}\n')
    check("the kept folder is not an edit to the library", any(sk.library_diff(lib, dest=dest).values()), False)
    (lib / "README.md").write_text("library, newer\n")
    sk.install_library(lib, dest=dest)
    check("replacing the library keeps the kept folder", note.read_text(), '{"notes": 1}\n')
    check("...and the library did change", (dest / "README.md").read_text(), "library, newer\n")
    # An update's backup and its undo, through loam_update with Loam's folder
    # pointed at the scratch one.
    keep = (sk.loam_dir, up._path)
    backups = tmp / "upd"
    sk.loam_dir = lambda vault_=None: dest
    up._path = lambda rel: backups / rel
    try:
        (backups / "backups").mkdir(parents=True)
        b = up.backup_library("r1")
        check("an update's backup leaves the kept folder out", (Path(b) / sk.KEPT_DIR).exists(), False)
        note.write_text('{"notes": 2}\n')
        up.restore_library(b)
        check("undoing an update keeps the kept folder as it is now", note.read_text(), '{"notes": 2}\n')
        check("...and puts the library back", (dest / "README.md").read_text(), "library, newer\n")
    finally:
        sk.loam_dir, up._path = keep


# -- what a tool and a server are told ------------------------------------------------------

def test_environment(tmp: Path):
    os.environ["LOAM_PLUGIN_STATE"] = str(tmp / "pstate")
    import loam_contributions as lcon
    import loam_plugin_servers as lps
    plugin = {"id": "doc-comments", "folder": PLUGIN, "settings": tmp / "s", "data": tmp / "d",
              "has_server": True,
              "contributes": {"agent.tool": [{"name": "doc-comments", "run": "python3 tools/notes_mcp.py",
                                              "lanes": ["persona"], "agents": None}]}}
    servers = lcon.lane_servers("persona", "ada", plugins=[plugin])
    env = servers["doc-comments"]["env"]
    port = lps.read_ports().get("doc-comments")
    check("an agent tool is told its agent and lane", (env.get("LOAM_AGENT"), env.get("LOAM_LANE")), ("ada", "persona"))
    check("...the vault and the workspaces", (env.get("LOAM_VAULT"), env.get("LOAM_WORKSPACES")),
          (str(lc.VAULT), str(lc.WORKSPACES)))
    check("...and its plugin server's loopback address", env.get("LOAM_PLUGIN_URL"), f"http://127.0.0.1:{port}/")
    check("the engineer lane's copy names no agent",
          "LOAM_AGENT" in (lcon.lane_servers("engineer", None, plugins=[{**plugin, "contributes": {"agent.tool": [
              {"name": "doc-comments", "run": "x", "lanes": ["engineer"], "agents": None}]}}])["doc-comments"]["env"]), False)
    want = {"folder": PLUGIN, "run": "python3 server/main.py"}
    os.environ["LOAM_PLUGINS_DIR"] = str(tmp / "yours")
    senv = lps.environment("doc-comments", want, 9199)
    check("a plugin server is told its kept vault folder",
          (senv["LOAM_VAULT_DATA"], senv["LOAM_VAULT_DATA_DIR"]),
          (f"{lc.vault_folder('system')}/Loam/data/doc-comments",
           str(lc.VAULT / lc.vault_folder("system") / "Loam" / "data" / "doc-comments")))


# -- plugin off ---------------------------------------------------------------------------

def test_off(tmp: Path):
    os.environ["LOAM_PLUGIN_SHIPPED"] = str(ROOT / "plugins")
    os.environ["LOAM_PLUGINS_DIR"] = str(tmp / "yours-off")
    import loam_contributions as lcon
    import loam_plugins as lp
    row = next(r for r in lp._rows() if r["id"] == "doc-comments")
    check("the manifest reads clean: no breakage, no warning", (row.get("broken"), row.get("warnings") or []), (None, []))
    check("it ships off", (row.get("default_on"), row.get("on")), (False, False))
    check("off: none of its contributions are in force",
          [p["id"] for p in lcon.active() if p["id"] == "doc-comments"], [])
    check("off: its server is not wanted", "doc-comments" in lp.wanted_servers(), False)
    check("off: no agent gets its tool", "doc-comments" in lcon.lane_servers("persona", "ada"), False)


# -- the share door in core ------------------------------------------------------------

def test_door():
    import importlib.machinery
    import importlib.util
    import loam_deploy
    import loam_plugin_servers
    loader = importlib.machinery.SourceFileLoader("plugin_gateway", str(ROOT / "bin" / "plugin-gateway"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    gw = importlib.util.module_from_spec(spec)
    loader.exec_module(gw)
    tok = "a" * 32
    check("the door takes /s/<token> and its paths only",
          [bool(gw.DOOR_PATH.match(p)) for p in (f"/s/{tok}", f"/s/{tok}/api/doc?x=1", "/s/short", "/x/doc-comments/",
                                                 f"/s/{tok}#f", "/api/threads", f"/s/{'a' * 65}")],
          [True, True, False, False, False, False, False])
    hdrs = gw.door_headers(["Host: next.example", "Cookie: authelia_session=SECRET; loam_share=abc; other=1",
                            "Remote-User: owner", "Authorization: Bearer lak_xyz", "X-Loam-Door: 1",
                            "X-Loam-Agent: ada", "X-Forwarded-Prefix: /x/doc-comments", "Content-Type: application/json"])
    check("the door passes only allowed headers: no login cookie, no user, no marker from outside",
          hdrs, ["Host: next.example", "Cookie: loam_share=abc", "Authorization: Bearer lak_xyz",
                 "Content-Type: application/json"])
    check("...a bearer that is not an agent key is dropped",
          gw.door_headers(["Authorization: Bearer some-broker-token"]), [])
    check("...an upgrade, a chunked or an oversized body is refused",
          [gw.door_headers(["Upgrade: websocket"]), gw.door_headers(["Transfer-Encoding: chunked"]),
           gw.door_headers([f"Content-Length: {3 * 1024 * 1024}"])], [None, None, None])
    check("the login's port takes the door's marker off", "x-loam-door" in gw.HOP, True)
    ports = {n: v for n, v in vars(loam_deploy).items() if n.endswith("_PORT") and isinstance(v, int)}
    ports.update({f"servers.{n}": v for n, v in vars(loam_plugin_servers).items()
                  if n.endswith("_PORT") and isinstance(v, int)})
    dupes = sorted(n for n, v in ports.items() if v == loam_deploy.PLUGIN_DOOR_PORT)
    check("the door's port is no other service's", dupes, ["PLUGIN_DOOR_PORT", "servers.DOOR_PORT"])
    import loam_contributions as lcon
    w: list = []
    got = lcon.contributes_of({"web.door": [{"why": "visitors reach one doc"}]}, None, w)
    check("web.door is a contribution, with its why", got, {"web.door": [{"why": "visitors reach one doc"}]})
    try:
        lcon.contributes_of({"web.door": [{}]}, None, [])
        refused = False
    except lcon.ContributionError:
        refused = True
    check("...and a door with no why is refused", refused, True)
    check("the door's holder is the first plugin by id that has one",
          lcon.door_owner([{"id": "b", "contributes": {"web.door": [{}]}}, {"id": "a", "contributes": {}},
                           {"id": "c", "contributes": {"web.door": [{}]}}]), "b")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="doc-comments-test-"))
    saved = {k: os.environ.get(k) for k in ("LOAM_PLUGIN_STATE", "LOAM_PLUGINS_DIR", "LOAM_PLUGIN_SHIPPED")}
    try:
        test_suites()
        test_with_broker(tmp)
        test_kept(tmp)
        test_environment(tmp)
        test_off(tmp)
        test_door()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
