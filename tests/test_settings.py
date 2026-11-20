#!/usr/bin/env python3
"""Exercise the settings registry and CLI through a loopback broker.
LOAM_UI_SOURCE selects the app checkout for the relay comparison. Set it to
none to skip only that comparison."""

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp())
os.environ["LOAM_VAULT"] = str(TMP / "vault")
os.environ["LOAM_AI_LABS"] = str(TMP / "ai-labs")
os.environ["LOAM_THREADS_STATE"] = str(TMP / "state")
os.environ["LOAM_HOST_SETTINGS"] = str(TMP / "broker" / "settings.json")
# The plugin host's two layers (lib/loam_plugins.py), its themes in loam-ui's
# theme-folder shape: the shipped pair and one more, a folder whose manifest
# names another id (broken, so not a theme), a folder with no manifest, a
# stray file; and the Yours layer with a theme of its own.
THEMES, USER_THEMES = TMP / "themes", TMP / "user-themes"
# Use the shipped theme's option schema and an isolated plugin directory.
LATTE_OPTIONS = [
    {"id": "agent-colours", "label": "Agent colours",
     "choices": [{"id": "palette", "label": "Palette"},
                 {"id": "latte-tones", "label": "Latte tones"}],
     "default": "palette"},
    {"id": "agent-shapes", "label": "Agent shapes",
     "choices": [{"id": "distinct", "label": "Each its own"},
                 {"id": "cleo", "label": "Cleo"},
                 {"id": "rounded-square", "label": "Rounded square"},
                 {"id": "latte-art", "label": "Latte art"}],
     "default": "distinct"},
    {"id": "latte-mug", "label": "In a mug",
     "choices": [{"id": "on", "label": "On"}, {"id": "off", "label": "Off"}],
     "default": "on"},
    {"id": "latte-inside", "label": "Inside",
     "choices": [{"id": "pour", "label": "Pour"},
                 {"id": "initials", "label": "Initials"}],
     "default": "pour"}]
LATTE_DEFAULTS = {"latte": {o["id"]: o["default"] for o in LATTE_OPTIONS}}
# loam-ui's table of the options its core page draws (public/theme-css.js,
# `coreOptions`), as that file writes it; the host reads it from there.
CORE_TABLE = TMP / "theme-css.js"
CORE_TABLE.write_text("const slug = /^[a-z][a-z0-9-]*$/\nconst coreOptions = {\n"
                      + "".join(f"  '{o['id']}': [{', '.join(repr(c['id']) for c in o['choices'])}],\n"
                                for o in LATTE_OPTIONS) + "}\n")
os.environ["LOAM_UI_CORE_OPTIONS"] = str(CORE_TABLE)
# Read modes and locations from the app's appearance registry.
HANDOFF = TMP / "appearance-settings.registry.json"
HANDOFF_ROWS = [
    {"key": "appearance.location", "type": ["object", "null"], "default": None,
     "required": ["latitude", "longitude", "source", "timeZone"],
     "properties": {"latitude": {"type": "number"}, "longitude": {"type": "number"},
                    "source": {"type": "string", "enum": ["geolocation", "timezone"]},
                    "timeZone": {"type": "string"}}},
    *[{"key": f"appearance.{k}.mode", "type": "string",
       "enum": ["light", "dark", "sun", "system"], "default": "system"}
      for k in ("phone", "desktop")],
    # Sun transition
    *[{"key": f"appearance.{k}.transition", "type": "string",
       "enum": ["fast", "normal", "long"], "default": "normal"}
      for k in ("phone", "desktop")]]
HANDOFF.write_text(json.dumps({"route": "/v1/settings/appearance",
                               "settings": HANDOFF_ROWS}))
os.environ["LOAM_UI_APPEARANCE_REGISTRY"] = str(HANDOFF)
(THEMES / "latte").mkdir(parents=True)
(THEMES / "latte" / "PLUGIN.md").write_text(
    "---\nid: latte\nname: Latte\nversion: 1.0.0\nauthor: Loam contributors\n"
    "api: 0\nkind: [theme]\noptions: " + json.dumps(LATTE_OPTIONS) + "\n"
    "places: []\noverrides: []\ntouches:\n  network: []\n  record: none\n"
    "  memory: none\n  server: false\n---\n\nLatte is a token-only theme.\n")
(THEMES / "latte" / "theme.css").write_text(":root {}\n")
for root, tid, mid in ((THEMES, "plain", "plain"),
                       (THEMES, "sepia-night", "sepia-night"),
                       (THEMES, "misnamed", "other"), (THEMES, "Upper", "Upper"),
                       (USER_THEMES, "midnight", "midnight")):
    (root / tid).mkdir(parents=True)
    (root / tid / "manifest.json").write_text(json.dumps(
        {"schemaVersion": 1, "id": mid, "name": tid, "version": "1.0.0",
         "author": "test", "modes": ["light", "dark"], "stylesheet": "theme.css"}))
    (root / tid / "theme.css").write_text(":root {}\n")
(THEMES / "no-manifest").mkdir()
(THEMES / "README.md").write_text("not a theme")
os.environ["LOAM_PLUGIN_SHIPPED"] = f"{THEMES}:{TMP / 'absent'}"
os.environ["LOAM_PLUGINS_DIR"] = str(USER_THEMES)
# Render the display field even when the workspace ID is lower-case.
for who in ("ada", "iris", "cleo"):
    (TMP / "ai-labs" / who).mkdir(parents=True)
    (TMP / "ai-labs" / who / "agent.json").write_text(
        json.dumps({"name": who, "display": who.capitalize()}))
(TMP / "vault" / "15_CONVERSATIONS").mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents").mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents" / "comms.md").write_text("# From agents\n")

sys.path.insert(0, str(AGENTS / "lib"))
import loam_broker as cfg  # noqa: E402
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}
import loam_threads as threads  # noqa: E402
OWNER_NAME = cfg.lc.OWNER_NAME
import loam_settings as settings  # noqa: E402

cfg.roster = threads.roster
cfg.roster_cards = lambda: [{"id": p} for p in threads.roster()]
cfg.VAULT = TMP / "vault"

loader = importlib.machinery.SourceFileLoader(
    "session_broker", str(AGENTS / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker_mod = importlib.util.module_from_spec(spec)
loader.exec_module(broker_mod)
engine_mod = broker_mod.loam_thread_engine

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:68}"
          f"{'  | ' + str(detail)[:240] if detail != '' else ''}")


# -- the registry against the page's relay -----------------------------------

source = os.environ.get("LOAM_UI_SOURCE", str((Path(__file__).resolve().parent.parent / "app")))
if source == "none":
    print("SKIP  the page's relay: LOAM_UI_SOURCE=none")
else:
    server_js = Path(source) / "src" / "server.js"
    check("loam-ui's relay is there to read", server_js.is_file(), str(server_js))
    if server_js.is_file():
        relayed = settings.relay_routes(server_js.read_text(encoding="utf-8"))
        unknown = [r for r in relayed if not settings.classified(r)]
        check("the relay has dozens of route families", len(relayed) > 30, len(relayed))
        check("every route the page's relay passes is in PAGE_RELAY "
              "(classify a new one in lib/loam_settings.py)", not unknown, unknown)
        gone = [r for r in settings.PAGE_RELAY if r not in relayed]
        check("PAGE_RELAY names no route the relay has lost", not gone, gone)
        check("PAGE_RELAY_AHEAD and PAGE_RELAY name no route twice",
              not set(settings.PAGE_RELAY) & set(settings.PAGE_RELAY_AHEAD))
        for r in settings.PAGE_RELAY_AHEAD:
            if r in relayed:
                print(f"NOTE  the relay now passes {r}: move its line from "
                      f"PAGE_RELAY_AHEAD into PAGE_RELAY")

    # Validate the appearance settings against the app's handoff.
    handoff = Path(source) / "docs" / "appearance-settings.registry.json"
    check("loam-ui's appearance handoff is there to read", handoff.is_file(),
          str(handoff))
    if handoff.is_file():
        import loam_appearance as appearance  # noqa: PLC0415
        declared = json.loads(handoff.read_text(encoding="utf-8"))
        rows = {r["key"]: r for r in declared["settings"]}
        # The theme ids and their options are not a list here: they are what
        # loam-ui's own folders hold (the source's, which on the box is the
        # checkout the broker reads), read the way the broker reads them.
        import loam_plugins  # noqa: PLC0415
        env_dirs = os.environ.pop("LOAM_PLUGIN_SHIPPED")
        env_yours = os.environ["LOAM_PLUGINS_DIR"]
        # The core table too is the source's (the host reads it from the
        # checkout on the box); the fixture's is set aside.
        env_core = os.environ["LOAM_UI_CORE_OPTIONS"]
        os.environ["LOAM_UI_CORE_OPTIONS"] = f"{source}/public/theme-css.js"
        # And the modes and location sources: the handoff's own, as on the box.
        env_handoff = os.environ["LOAM_UI_APPEARANCE_REGISTRY"]
        os.environ["LOAM_UI_APPEARANCE_REGISTRY"] = str(handoff)
        os.environ["LOAM_PLUGINS_DIR"] = str(TMP / "absent")
        try:
            dirs = loam_plugins.shipped_roots()
            os.environ["LOAM_PLUGIN_SHIPPED"] = (f"{source}/public/themes:"
                                                 f"{source}/public/plugins")
            real = appearance.themes()
            real_cat = appearance.catalog()
            real_rows = loam_plugins.listing()["plugins"]
            live = {e.key: e for e in settings.option_entries()}
            real_declared = appearance.declared()
            # Keep the theme directory override active during validation, which reads installed
            # themes.
            refused = []
            for theme, opts_ in real_cat.items():
                for opt, spec in opts_.items():
                    for c in spec["choices"]:
                        try:
                            appearance.validate(
                                {"desktop": {"options": {theme: {opt: c}}}})
                        except appearance.AppearanceError as exc:
                            refused.append((theme, opt, c, exc.message))
        finally:
            os.environ["LOAM_UI_APPEARANCE_REGISTRY"] = env_handoff
            os.environ["LOAM_PLUGIN_SHIPPED"] = env_dirs
            os.environ["LOAM_PLUGINS_DIR"] = env_yours
            os.environ["LOAM_UI_CORE_OPTIONS"] = env_core
        ours = sorted([k for k in settings.BY_KEY if k.startswith("appearance.")]
                      + list(live))
        check("the registry's appearance keys are the page's (theme options "
              "read from its manifests)", sorted(rows) == ours, (sorted(rows), ours))
        check("the route is the page's",
              all(e.route == "PATCH " + declared["route"]
                  and e.read == "GET " + declared["route"]
                  for k in ours for e in [settings.BY_KEY.get(k) or live[k]]))
        bad = []
        for key, r in rows.items():
            field = key.rsplit(".", 1)[1]
            if field == "mode" and (tuple(r["enum"]) != real_declared["modes"]
                                    or r["default"] != appearance.DEFAULT["mode"]):
                bad.append(key)
            if field == "transition" and (
                    tuple(r["enum"]) != appearance.TRANSITIONS
                    or r["default"] != appearance.DEFAULT["transition"]):
                bad.append(key)
            if key == "appearance.location" and (
                    tuple(r["properties"]["source"]["enum"])
                    != real_declared["sources"] or r["default"] is not None
                    or sorted(r["required"]) != sorted(appearance.LOCATION_FIELDS)):
                bad.append(key)
            if field == "size" and ((r["minimum"], r["maximum"], r["multipleOf"],
                                     r["default"]) != (appearance.SIZE_MIN,
                                                       appearance.SIZE_MAX,
                                                       appearance.SIZE_STEP,
                                                       appearance.DEFAULT["size"])):
                bad.append(key)
            if field == "theme" and r["default"] != appearance.DEFAULT["theme"]:
                bad.append(key)
            if ".options." in key:
                theme, opt = key.split(".")[3:5]
                spec = real_cat.get(theme, {}).get(opt) or {}
                if (r["enum"] != spec.get("choices")
                        or r["default"] != spec.get("default")):
                    bad.append(key)
        check("the store's values and defaults are the page's", not bad, bad)
        broken = {r["id"]: r["broken"] for r in real_rows
                  if r["broken"] and "theme" in str(r["folder"])}
        check("no theme folder loam-ui ships reads as broken (its manifest, "
              "options and all, is one the host accepts)", not broken, broken)
        declared_opts = {r["id"] for r in real_rows
                         if not r["broken"] and r.get("options")}
        check("every shipped theme whose manifest has options is in the catalog",
              declared_opts == set(real_cat) and real_cat, (declared_opts, real_cat))
        check("every choice of every option loam-ui's themes declare validates",
              not refused, refused[:3])
        # Use the app source as the shipped theme layer.
        import loam_plugins as _lp  # noqa: PLC0415
        ui = (_lp.ui_checkout() or Path("/nonexistent")) / "public"
        check("on the box the shipped layer is the loam-ui checkout's theme "
              "and plugin folders",
              # Include the runtime's first-party plugins.
              dirs[:2] == [ui / "themes", ui / "plugins"]
              and dirs[2:] == [Path(settings.lc.ROOT) / "plugins"]
              , dirs)
        enums = {k: r["enum"] for k, r in rows.items() if k.endswith(".theme")}
        check("the handoff states the theme of both kinds", len(enums) == 2, enums)
        check("the themes read from loam-ui's folders are the handoff's ids",
              all(sorted(v) == sorted(real) for v in enums.values()),
              (real, enums))

named = [k for v in settings.PAGE_RELAY.values() if isinstance(v, list) for k in v]
check("every entry PAGE_RELAY names exists",
      all(k in settings.BY_KEY for k in named),
      [k for k in named if k not in settings.BY_KEY])
check("every persona entry with a route has a body",
      all(e.body for e in settings.ENTRIES if e.reach == "persona" and e.route),
      [e.key for e in settings.ENTRIES if e.reach == "persona" and e.route and not e.body])
check("every entry out of a persona's reach says why",
      all(e.why for e in settings.ENTRIES if e.reach != "persona" or not e.route),
      [e.key for e in settings.ENTRIES if (e.reach != "persona" or not e.route) and not e.why])
check("every gettable entry has a reader",
      all(e.pick for e in settings.ENTRIES if e.read))
check("keys are unique", len(settings.BY_KEY) == len(settings.ENTRIES))
doc = (AGENTS / "broker" / "settings.md").read_text(encoding="utf-8")
check("broker/settings.md's inventory has a row for every key",
      all(f"| `{k}` |" in doc for k in settings.BY_KEY),
      [k for k in settings.BY_KEY if f"| `{k}` |" not in doc])


# -- through a broker on loopback ---------------------------------------------

class FakeBroker:
    def __init__(self):
        self.sessions = {}
        self.token = "test-token"

    async def start_session(self, *a, **k):
        raise AssertionError("no turn is started in this test")


TOKEN = TMP / "token"
TOKEN.write_text("test-token")


async def cli(port, *argv, lane=None, agent="ada"):
    env = dict(os.environ, LOAM_BROKER_URL=f"http://127.0.0.1:{port}",
               LOAM_BROKER_TOKEN_FILE=str(TOKEN), LOAM_AGENT=agent)
    env.pop("LOAM_WAKE", None)
    if lane:
        env["LOAM_WAKE"] = lane
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(AGENTS / "bin" / "settings"), *argv, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    try:
        return json.loads(out)
    except ValueError:
        return {"ok": False, "error": f"no JSON: {out!r} {err[-400:]!r}"}


async def http(port, method, path, body=None):
    import urllib.request  # noqa: PLC0415

    def go():
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": "Bearer test-token",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())
    return await asyncio.to_thread(go)


def lines(tid):
    return [m["text"] for m in threads.read(tid)[1] if m.get("kind") == "system"]


async def main():
    server = broker_mod.Server.__new__(broker_mod.Server)
    fake = FakeBroker()
    server.broker = fake
    server.threads = engine_mod.ThreadEngine(fake)
    tcp = await asyncio.start_server(server.handle, "127.0.0.1", 0)
    port = tcp.sockets[0].getsockname()[1]
    try:
        status, body = await http(port, "GET", "/v1/settings")
        check("GET /v1/settings answers the table",
              status == 200 and {r["key"] for r in body["settings"]}
              == {e.key for e in settings.entries()}, status)
        check("...the theme options among them, read from the manifests",
              {f"appearance.{k}.options.latte.{o}" for k in ("phone", "desktop")
               for o in LATTE_DEFAULTS["latte"]}
              <= {r["key"] for r in body["settings"]}, body["settings"][-4:])

        out = await cli(port, "do", "thread.create", "Throwaway")
        tid = (out.get("answer") or {}).get("id")
        check("do thread.create: a thread, started by Ada",
              out.get("ok") and tid and lines(tid) == ["Ada started this thread"],
              out)

        board = (await cli(port, "get", "thread.board", "--target", tid))["value"]
        board["markdown"] += "- settings edit\n"
        saved = await cli(port, "set", "thread.board", json.dumps(board), "--target", tid)
        check("settings can read and write a revisioned board",
              saved.get("value", {}).get("markdown") == board["markdown"], saved)
        stale = await cli(port, "set", "thread.board", json.dumps(board), "--target", tid)
        check("settings refuses an intervening board revision", not stale.get("ok"), stale)
        out = await cli(port, "set", "thread.title", "Settings probe", "--target", tid)
        check("set thread.title: the value read back is the new title",
              out.get("ok") and out.get("value") == "Settings probe", out)
        out = await cli(port, "set", "thread.icon", "\U0001f9ea", "--target", tid)
        check("set thread.icon: read back", out.get("value") == "\U0001f9ea", out)
        out = await cli(port, "set", "thread.default", "iris", "--target", tid)
        check("set thread.default iris: read back",
              out.get("ok") and out.get("value") == "iris", out)
        out = await cli(port, "do", "thread.members.add", "cleo", "--target", tid)
        check("do thread.members.add cleo", out.get("ok"), out)
        out = await cli(port, "set", "thread.engine", "codex", "--target", tid,
                        "--member", "cleo")
        check("set thread.engine for a member: read back",
              out.get("value") == "codex", out)
        out = await cli(port, "do", "thread.members.remove", "cleo", "--target", tid)
        check("do thread.members.remove cleo", out.get("ok"), out)
        out = await cli(port, "set", "thread.pinned", "true", "--target", tid)
        check("set thread.pinned true: a JSON value, read back",
              out.get("value") is True, out)
        out = await cli(port, "get", "thread.members", "--target", tid)
        check("get thread.members", out.get("value") == ["iris"], out)

        said = lines(tid)
        want = ["Ada started this thread",
                "Ada renamed this thread to “Settings probe”",
                "Ada changed the icon to \U0001f9ea",
                "Ada made Iris the default",
                "Ada brought in Cleo",
                "Ada put Cleo on codex",
                "Ada took Cleo out of this thread",
                "Ada pinned this thread"]
        check("one line per change, each naming the persona", said == want, said)

        status, _ = await http(port, "PATCH", f"/v1/threads/{tid}",
                               {"default": "ada", "icon": "\U0001f4dd"})
        check("the owner's own PATCH: '<owner> made ...', no icon line",
              status == 200 and lines(tid)[len(want):] == [f"{OWNER_NAME} made Ada the default"],
              lines(tid)[len(want):])

        status, body = await http(port, "PATCH", f"/v1/threads/{tid}",
                                  {"title": "x", "by": "nobody"})
        check("a `by` naming nobody: the broker's 400, nothing changed",
              status == 400 and "no persona" in body["error"]
              and threads.read(tid)[0]["title"] == "Settings probe", body)

        for argv, says in (
                (("set", "device.sidebar", "true"), "not on the host"),
                (("do", "provider.login"), f"{OWNER_NAME}'s hands only"),
                (("do", "files.write"), "your own file tools"),
                (("do", "job.cancel", "--target", "j1"), "mcp__engineer__cancel"),
                (("set", "thread.title", "x", "--target", tid, "--by", "nobody"),
                 "no persona named"),
                (("set", "thread.nonsense", "x"), "no setting"),
                (("set", "thread.engine", "codex", "--target", tid), "needs `member`"),
                (("do", "thread.title", "x", "--target", tid), "is a setting")):
            out = await cli(port, *argv)
            check(f"refused: {' '.join(argv[:2])}",
                  out.get("ok") is False and says in out.get("error", ""), out)
        out = await cli(port, "set", "thread.title", "x", "--target", tid, agent="")
        check("refused: no `by` at all", out.get("ok") is False
              and "pass `by`" in out.get("error", ""), out)

        # -- Appearance -------------------------------------------------------
        def full(phone, desktop, popts=None, dopts=None, location=None):
            return {"phone": {"transition": "normal",
                              **dict(zip(("mode", "size", "theme"), phone)),
                              "options": popts or LATTE_DEFAULTS},
                    "desktop": {"transition": "normal",
                                **dict(zip(("mode", "size", "theme"), desktop)),
                                "options": dopts or LATTE_DEFAULTS},
                    "location": location}

        def base(value):
            # Mode, size and theme: the transition has checks of its own.
            return {k: {f: v for f, v in value[k].items()
                        if f not in ("options", "transition")}
                    for k in ("phone", "desktop")}

        status, body = await http(port, "GET", "/v1/settings/appearance")
        check("GET appearance with nothing stored: the defaults",
              status == 200 and body == full(("system", 100, "latte"),
                                             ("system", 100, "latte")), body)
        status, body = await http(port, "PATCH", "/v1/settings/appearance",
                                  {"desktop": {"size": 90}})
        check("PATCH desktop size 90: 200, the whole value, the rest kept",
              status == 200 and body == full(("system", 100, "latte"),
                                             ("system", 90, "latte")), body)
        status, body = await http(port, "PATCH", "/v1/settings/appearance",
                                  {"phone": {"mode": "dark", "size": 125},
                                   "desktop": {"mode": "light"}})
        check("PATCH both kinds at once: merged, desktop size still 90",
              body == full(("dark", 125, "latte"), ("light", 90, "latte")), body)
        stored = json.loads(Path(os.environ["LOAM_HOST_SETTINGS"]).read_text())
        check("persisted under `appearance` in the host settings file",
              base(stored.get("appearance")) == base(body)
              and all(stored["appearance"][k]["options"] == {}
                      for k in ("phone", "desktop")),
              stored)
        for patch, says in (({}, "name what"),
                            ({"tablet": {"size": 90}}, "not a device kind"),
                            ({"desktop": {}}, "name a mode"),
                            ({"desktop": {"zoom": 90}}, "not an appearance field"),
                            ({"desktop": {"mode": "sepia"}}, "light, dark, sun or system"),
                            ({"desktop": {"size": True}}, "whole percent"),
                            ({"desktop": {"size": 92}}, "whole percent"),
                            ({"desktop": {"size": 90.0}}, "whole percent"),
                            ({"desktop": {"size": 70}}, "whole percent"),
                            ({"desktop": {"size": 155}}, "whole percent"),
                            ({"desktop": {"size": 95}, "phone": {"mode": "x"}},
                             "light, dark, sun or system"),
                            ({"desktop": {"theme": "mocha"}}, "installed theme"),
                            ({"desktop": {"theme": "misnamed"}}, "installed theme"),
                            ({"desktop": {"theme": "other"}}, "installed theme"),
                            ({"desktop": {"theme": "Upper"}}, "installed theme"),
                            ({"desktop": {"theme": "no-manifest"}}, "installed theme"),
                            ({"desktop": {"theme": None}}, "installed theme"),
                            ({"desktop": {"theme": ["latte"]}}, "installed theme"),
                            ({"phone": {"theme": ""}}, "installed theme"),
                            ({"phone": {"size": 95, "theme": "mocha"}},
                             "installed theme")):
            status, body = await http(port, "PATCH", "/v1/settings/appearance", patch)
            check(f"PATCH refused with a sentence: {json.dumps(patch)[:40]}",
                  status == 400 and says in body.get("error", ""), (status, body))
        status, body = await http(port, "PATCH", "/v1/settings/appearance",
                                  {"desktop": {"theme": "mocha"}})
        check("an uninstalled theme's refusal lists the installed ones, Latte first",
              "(latte, midnight, plain, sepia-night)" in body.get("error", ""), body)
        status, body = await http(port, "GET", "/v1/settings/appearance")
        check("nothing a refused patch named was written (not even its valid half)",
              body == full(("dark", 125, "latte"), ("light", 90, "latte")), body)
        status, _ = await http(port, "DELETE", "/v1/settings/appearance")
        check("DELETE appearance: 405", status == 405, status)

        # -- Themes ----------------------------------------------------------
        status, body = await http(port, "PATCH", "/v1/settings/appearance",
                                  {"desktop": {"theme": "plain"}})
        check("PATCH desktop theme plain: mode, size and the phone untouched",
              status == 200 and body == full(("dark", 125, "latte"),
                                             ("light", 90, "plain")), body)
        status, body = await http(port, "PATCH", "/v1/settings/appearance",
                                  {"phone": {"theme": "sepia-night", "mode": "light"}})
        check("a theme added to the folder is settable with no code change",
              status == 200 and body["phone"] == {"mode": "light",
                                                   "transition": "normal", "size": 125,
                                                   "theme": "sepia-night",
                                                   "options": LATTE_DEFAULTS}, body)
        status, body = await http(port, "PATCH", "/v1/settings/appearance",
                                  {"phone": {"theme": "midnight"}})
        check("a theme in the Yours layer is settable",
              status == 200 and body["phone"]["theme"] == "midnight", body)
        # The record as it was before themes: no `theme` in either kind.
        settings_file = Path(os.environ["LOAM_HOST_SETTINGS"])
        settings_file.write_text(json.dumps({"appearance": {
            "phone": {"mode": "system", "size": 100},
            "desktop": {"mode": "light", "size": 90}}, "other": {"kept": True}}))
        status, body = await http(port, "GET", "/v1/settings/appearance")
        check("a record from before themes reads as latte, mode and size as stored",
              body == full(("system", 100, "latte"), ("light", 90, "latte")), body)
        status, body = await http(port, "PATCH", "/v1/settings/appearance",
                                  {"phone": {"theme": "plain"}})
        stored = json.loads(settings_file.read_text())
        check("its first theme write merges in; the file's other sections kept",
              base(stored["appearance"]) == base(full(("system", 100, "plain"),
                                                      ("light", 90, "latte")))
              and stored.get("other") == {"kept": True}, stored)
        # A theme removed from the folders after it was chosen: kept on read
        # (the page draws Latte), so it comes back with the folder.
        stored["appearance"]["desktop"]["theme"] = "gone-away"
        stored["appearance"]["phone"]["theme"] = "../../etc"
        settings_file.write_text(json.dumps(stored))
        status, body = await http(port, "GET", "/v1/settings/appearance")
        check("a stored theme no longer installed is kept; one not shaped "
              "like an id reads as latte",
              body["desktop"]["theme"] == "gone-away"
              and body["phone"]["theme"] == "latte", body)
        status, body = await http(port, "PATCH", "/v1/settings/appearance",
                                  {"desktop": {"size": 100}})
        check("...and an unrelated write does not throw it away",
              base(body)["desktop"] == {"mode": "light", "size": 100,
                                        "theme": "gone-away"}, body)
        saved = (os.environ["LOAM_PLUGIN_SHIPPED"], os.environ["LOAM_PLUGINS_DIR"])
        os.environ["LOAM_PLUGIN_SHIPPED"] = str(TMP / "absent")
        os.environ["LOAM_PLUGINS_DIR"] = str(TMP / "absent")
        try:
            import loam_appearance as appearance  # noqa: PLC0415
            check("no folder readable: the two the page always has",
                  appearance.themes() == ["latte", "plain"], appearance.themes())
        finally:
            os.environ["LOAM_PLUGIN_SHIPPED"], os.environ["LOAM_PLUGINS_DIR"] = saved
        status, body = await http(port, "PATCH", "/v1/settings/appearance",
                                  {"desktop": {"size": 90, "theme": "latte"}})
        check("back to desktop light, 90, latte",
              base(body)["desktop"] == {"mode": "light", "size": 90,
                                        "theme": "latte"}, body)

        # Theme options
        def opts(colours="palette", shapes="distinct", mug="on", inside="pour"):
            return {"latte": {"agent-colours": colours, "agent-shapes": shapes,
                              "latte-mug": mug, "latte-inside": inside}}

        status, body = await http(port, "PATCH", "/v1/settings/appearance",
                                  {"desktop": {"options": {"latte": {
                                      "agent-shapes": "cleo"}}}})
        check("PATCH one desktop option: 200, the other option and all else kept",
              status == 200 and body == full(("system", 100, "latte"),
                                             ("light", 90, "latte"),
                                             dopts=opts(shapes="cleo")), body)
        status, body = await http(port, "PATCH", "/v1/settings/appearance",
                                  {"desktop": {"options": {"latte": {
                                      "agent-colours": "latte-tones"}}}})
        check("...then the other: both kept, the phone untouched",
              body == full(("system", 100, "latte"), ("light", 90, "latte"),
                           dopts=opts("latte-tones", "cleo")), body)
        stored = json.loads(settings_file.read_text())
        check("only what was chosen is stored, under the kind and the theme",
              stored["appearance"]["desktop"]["options"]
              == {"latte": {"agent-shapes": "cleo",
                            "agent-colours": "latte-tones"}}
              and stored["appearance"]["phone"]["options"] == {}, stored)
        await http(port, "PATCH", "/v1/settings/appearance",
                   {"desktop": {"theme": "plain"}})
        status, body = await http(port, "PATCH", "/v1/settings/appearance",
                                  {"desktop": {"theme": "latte"}})
        check("Plain and back to Latte: Latte's choices are still there",
              body["desktop"]["options"] == opts("latte-tones", "cleo"), body)
        status, body = await http(port, "PATCH", "/v1/settings/appearance",
                                  {"phone": {"theme": "plain", "options": {"latte": {
                                      "agent-shapes": "rounded-square"}}}})
        check("a theme and another theme's option in one write",
              body["phone"]["theme"] == "plain"
              and body["phone"]["options"] == opts(shapes="rounded-square"), body)
        # Concurrent writes of different leaves: none lost.
        leaves = [("phone", "agent-colours", "latte-tones"),
                  ("phone", "agent-shapes", "cleo"),
                  ("desktop", "agent-shapes", "distinct"),
                  ("desktop", "agent-colours", "palette")]
        await asyncio.gather(*(http(port, "PATCH", "/v1/settings/appearance",
                                    {k: {"options": {"latte": {o: c}}}})
                               for k, o, c in leaves))
        status, body = await http(port, "GET", "/v1/settings/appearance")
        check("four concurrent writes of different leaves: all four kept",
              body["phone"]["options"] == opts("latte-tones", "cleo")
              and body["desktop"]["options"] == opts("palette", "distinct"), body)
        before = body
        for patch, says in (
                ({"desktop": {"options": {}}}, "naming at least one"),
                ({"desktop": {"options": []}}, "a map of theme"),
                ({"desktop": {"options": "latte"}}, "a map of theme"),
                ({"desktop": {"options": {"latte": {}}}}, "naming at least one"),
                ({"desktop": {"options": {"latte": ["cleo"]}}}, "a map of option"),
                ({"desktop": {"options": {"plain": {"x": "y"}}}},
                 "not an installed theme with options"),
                ({"desktop": {"options": {"mocha": {"x": "y"}}}},
                 "not an installed theme with options"),
                ({"desktop": {"options": {"latte": {"agent-hats": "tall"}}}},
                 "no option"),
                ({"desktop": {"options": {"latte": {"agent-shapes": "hexagon"}}}},
                 "one of distinct, cleo, rounded-square, latte-art"),
                ({"desktop": {"options": {"latte": {"agent-shapes": 1}}}}, "one of"),
                ({"desktop": {"options": {"latte": {"agent-shapes": None}}}}, "one of"),
                ({"desktop": {"size": 95, "options": {"latte": {
                    "agent-shapes": "hexagon"}}}}, "one of")):
            status, body = await http(port, "PATCH", "/v1/settings/appearance", patch)
            check(f"PATCH refused with a sentence: {json.dumps(patch)[12:52]}",
                  status == 400 and says in body.get("error", ""), (status, body))
        status, body = await http(port, "GET", "/v1/settings/appearance")
        check("nothing a refused options patch named was written", body == before,
              body)
        # A choice the theme no longer offers, and a theme no longer installed.
        stored = json.loads(settings_file.read_text())
        stored["appearance"]["phone"]["options"]["latte"]["agent-shapes"] = "triangle"
        stored["appearance"]["phone"]["options"]["gone-away"] = {"tint": "blue"}
        settings_file.write_text(json.dumps(stored))
        status, body = await http(port, "GET", "/v1/settings/appearance")
        check("a choice no longer offered reads as the default; a gone theme's "
              "choices are not shown",
              body["phone"]["options"] == opts("latte-tones", "distinct"), body)
        await http(port, "PATCH", "/v1/settings/appearance",
                   {"phone": {"options": {"latte": {"agent-colours": "palette"}}}})
        stored = json.loads(settings_file.read_text())
        check("...and both are kept in the file, for when they come back",
              stored["appearance"]["phone"]["options"].get("gone-away")
              == {"tint": "blue"}
              and stored["appearance"]["phone"]["options"]["latte"]["agent-shapes"]
              == "triangle", stored["appearance"]["phone"])
        # Every choice of every option Latte's PLUGIN.md declares, both kinds:
        # through the page's route, then read back by a fresh GET (a reload),
        # then through a persona's `set` (the parity rule), then read back.
        missed = []
        for kind in ("phone", "desktop"):
            for o in LATTE_OPTIONS:
                for c in [ch["id"] for ch in o["choices"]] + [o["default"]]:
                    status, body = await http(port, "PATCH", "/v1/settings/appearance",
                                              {kind: {"options": {"latte": {o["id"]: c}}}})
                    _, again = await http(port, "GET", "/v1/settings/appearance")
                    if (status != 200 or body[kind]["options"]["latte"][o["id"]] != c
                            or again != body):
                        missed.append(("page", kind, o["id"], c, status, body))
        check("every choice of every Latte option, both kinds, saves through "
              "the page's route and reads back", not missed, missed[:2])
        missed = []
        for kind in ("phone", "desktop"):
            for o in LATTE_OPTIONS:
                for c in [ch["id"] for ch in o["choices"]] + [o["default"]]:
                    key = f"appearance.{kind}.options.latte.{o['id']}"
                    out = await cli(port, "set", key, c)
                    got = await cli(port, "get", key)
                    if not out.get("ok") or got.get("value") != c:
                        missed.append(("persona", key, c, out, got))
        check("...and through a persona's `set`, read back by `get`",
              not missed, missed[:2])
        status, body = await http(port, "GET", "/v1/settings/appearance")
        check("...leaving every option at its default again",
              body["phone"]["options"] == opts() == body["desktop"]["options"], body)
        import loam_appearance as appearance  # noqa: PLC0415
        try:
            appearance.validate({"desktop": {"options": {"plain": {"x": "y"}}}})
            said = None
        except appearance.AppearanceError as exc:
            said = exc.message
        check("the refusal names the one theme with options in a sentence",
              said and said.endswith("(the only installed theme with options "
                                     "is latte)"), said)
        # Read the app's current core table on every validation.
        table = CORE_TABLE.read_text()
        CORE_TABLE.write_text(table.replace(", 'latte-art'", ""))
        os.utime(CORE_TABLE, ns=(1, 1))
        try:
            stale = appearance.catalog()
            try:
                appearance.validate({"desktop": {"options": {"latte": {
                    "agent-shapes": "latte-art"}}}})
                said = None
            except appearance.AppearanceError as exc:
                said = exc.message
        finally:
            CORE_TABLE.write_text(table)
        check("a core table that lacks a choice Latte offers: Latte has no "
              "options, and the refusal says so in a sentence",
              "latte" not in stale and said
              and said.endswith("(no installed theme has options)"), (stale, said))
        check("...the table restored: Latte's four options are back",
              list(appearance.catalog().get("latte", {}))
              == [o["id"] for o in LATTE_OPTIONS], appearance.catalog())
        # A record from before options: the defaults.
        settings_file.write_text(json.dumps({"appearance": {
            "phone": {"mode": "dark", "size": 110, "theme": "latte"},
            "desktop": {"mode": "light", "size": 90, "theme": "latte"}}}))
        status, body = await http(port, "GET", "/v1/settings/appearance")
        check("a record from before options: every default, the rest as stored",
              body == full(("dark", 110, "latte"), ("light", 90, "latte")), body)

        out = await cli(port, "describe")
        keys = [r["key"] for r in out.get("settings") or []]
        want = [f"appearance.{k}.options.latte.{o}" for k in ("phone", "desktop")
                for o in LATTE_DEFAULTS["latte"]]
        check("describe lists every theme option of both kinds, from the manifest",
              all(k in keys for k in want), keys)
        out = await cli(port, "describe", "appearance.desktop.options.latte.agent-shapes")
        row = out.get("setting") or {}
        check("describe one option: its choices live, its default in words",
              row.get("allowed") == ["distinct", "cleo", "rounded-square",
                                     "latte-art"]
              and "default distinct" in row.get("values", ""), out)
        out = await cli(port, "set", "appearance.desktop.options.latte.agent-shapes",
                        "rounded-square")
        check("set a theme option as a persona: read back",
              out.get("ok") and out.get("value") == "rounded-square", out)
        status, body = await http(port, "GET", "/v1/settings/appearance")
        check("...the same value the page's GET reads, the other option kept",
              body["desktop"]["options"] == opts("palette", "rounded-square"), body)
        out = await cli(port, "get", "appearance.phone.options.latte.agent-colours")
        check("get the phone's own option", out.get("value") == "palette", out)
        out = await cli(port, "set", "appearance.phone.options.latte.agent-shapes",
                        "hexagon")
        check("set a choice the theme has not: the broker's refusal",
              out.get("ok") is False and "one of" in out.get("error", ""), out)
        out = await cli(port, "set", "appearance.phone.options.plain.agent-shapes",
                        "cleo")
        check("a theme without options has no option keys",
              out.get("ok") is False and "no setting" in out.get("error", ""), out)

        out = await cli(port, "set", "appearance.desktop.size", "110")
        check("set appearance.desktop.size 110 as a persona: read back as 110",
              out.get("ok") and out.get("value") == 110, out)
        out = await cli(port, "get", "appearance.desktop.mode")
        check("get appearance.desktop.mode: untouched", out.get("value") == "light", out)
        out = await cli(port, "set", "appearance.phone.mode", "system")
        check("set appearance.phone.mode system", out.get("value") == "system", out)
        out = await cli(port, "set", "appearance.desktop.size", "111")
        check("set appearance.desktop.size 111: the broker's refusal",
              out.get("ok") is False and "steps of 5" in out.get("error", ""), out)
        out = await cli(port, "set", "appearance.desktop.size", "90", lane="cron:watch")
        check("a cron may set appearance (it is a setting, not an end)",
              out.get("value") == 90, out)

        # Sun mode and saved location
        home = {"latitude": 37.8, "longitude": -122.4, "source": "geolocation",
                "timeZone": "America/Los_Angeles"}
        status, body = await http(port, "GET", "/v1/settings/appearance")
        check("a reading always carries location, null when none is saved",
              "location" in body and body["location"] is None, body)
        status, body = await http(port, "PATCH", "/v1/settings/appearance",
                                  {"desktop": {"mode": "sun"},
                                   "location": {**home, "latitude": 37.77,
                                                "longitude": -122.45}})
        check("the page's Sun body: desktop mode sun and the place in one PATCH, "
              "rounded to tenths half up as the page rounds",
              status == 200 and body["desktop"]["mode"] == "sun"
              and body["location"] == home and body["phone"]["mode"] == "system",
              (status, body))
        status, again = await http(port, "GET", "/v1/settings/appearance")
        check("...read back by a fresh GET, the phone's mode its own",
              again == body, again)
        status, body = await http(port, "PATCH", "/v1/settings/appearance",
                                  {"phone": {"size": 105}})
        check("a later write of another field keeps Sun and the place",
              body["desktop"]["mode"] == "sun" and body["location"] == home, body)
        moved = {"latitude": -33.9, "longitude": 151.2, "source": "timezone",
                 "timeZone": "Australia/Sydney"}
        status, body = await http(port, "PATCH", "/v1/settings/appearance",
                                  {"location": moved})
        check("the place alone, as the page sends it: shared, both kinds unchanged",
              status == 200 and body["location"] == moved
              and body["desktop"]["mode"] == "sun", body)
        for patch, says in (
                ({"location": "home"}, "a place or null"),
                ({"location": {**home, "lat": 1}}, "'lat' is not a location field"),
                ({"location": {"latitude": 37.8, "longitude": -122.4,
                               "source": "geolocation"}}, "it has no timeZone"),
                ({"location": {**home, "latitude": 91}}, "latitude is a number"),
                ({"location": {**home, "latitude": True}}, "latitude is a number"),
                ({"location": {**home, "latitude": "37.8"}}, "latitude is a number"),
                ({"location": {**home, "longitude": -180.5}}, "longitude is a number"),
                ({"location": {**home, "source": "gps"}},
                 "source is geolocation or timezone"),
                ({"location": {**home, "timeZone": "Pacific Time"}}, "IANA time zone"),
                ({"location": {**home, "timeZone": "posix/UTC"}}, "IANA time zone"),
                ({"location": {**home, "timeZone": "Mars/Olympus"}}, "IANA time zone"),
                ({"desktop": {"mode": "dark"}, "location": {**home, "source": "x"}},
                 "source is"),
                ({"desktop": {"mode": "dusk"}}, "light, dark, sun or system")):
            status, body = await http(port, "PATCH", "/v1/settings/appearance", patch)
            check(f"PATCH refused with a sentence: {json.dumps(patch)[:50]}",
                  status == 400 and says in body.get("error", ""), (status, body))
        status, body = await http(port, "GET", "/v1/settings/appearance")
        check("...and none of them wrote anything",
              body["location"] == moved and body["desktop"]["mode"] == "sun", body)
        status, body = await http(port, "PATCH", "/v1/settings/appearance",
                                  {"location": None})
        check("location null clears the place", status == 200
              and body["location"] is None and body["desktop"]["mode"] == "sun", body)

        # The modes are loam-ui's, read from its handoff: a handoff without
        # sun refuses it on write, but a stored sun is kept on read and through
        # another write; the handoff restored brings sun back with no change here.
        import loam_appearance as appearance  # noqa: PLC0415
        handoff_text = HANDOFF.read_text()
        HANDOFF.write_text(handoff_text.replace('"sun", ', ""))
        os.utime(HANDOFF, ns=(1, 1))
        try:
            status, body = await http(port, "PATCH", "/v1/settings/appearance",
                                      {"phone": {"mode": "sun"}})
            refused = (status, body)
            status, body = await http(port, "PATCH", "/v1/settings/appearance",
                                      {"desktop": {"size": 90}})
            kept = body
        finally:
            HANDOFF.write_text(handoff_text)
        check("a handoff without sun: a write of sun is refused, listing the modes",
              refused[0] == 400 and "light, dark or system, not 'sun'"
              in refused[1].get("error", ""), refused)
        check("...and a stored sun survives another field's write",
              kept["desktop"]["mode"] == "sun" and kept["desktop"]["size"] == 90, kept)
        os.environ["LOAM_UI_APPEARANCE_REGISTRY"] = "none"
        try:
            gone = appearance.declared()
            try:
                appearance.validate({"location": home})
                said = None
            except appearance.AppearanceError as exc:
                said = exc.message
        finally:
            os.environ["LOAM_UI_APPEARANCE_REGISTRY"] = str(HANDOFF)
        check("no handoff to read: the three modes the page always had, and "
              "a location refused in a sentence",
              gone == {"modes": ("light", "dark", "system"), "sources": None}
              and said and "declares no saved place" in said, (gone, said))
        check("...the handoff restored: sun and both sources are back",
              appearance.declared() == {"modes": ("light", "dark", "sun", "system"),
                                        "sources": ("geolocation", "timezone")},
              appearance.declared())

        # Persona parity: Sun and the place through a persona's set.
        out = await cli(port, "describe", "appearance.phone.mode")
        row = out.get("setting") or {}
        check("describe a mode: the modes loam-ui declares, live",
              row.get("allowed") == ["light", "dark", "sun", "system"], out)
        out = await cli(port, "set", "appearance.phone.mode", "sun")
        check("set appearance.phone.mode sun as a persona: read back",
              out.get("ok") and out.get("value") == "sun", out)
        out = await cli(port, "set", "appearance.location", json.dumps(
            {**home, "source": "timezone", "longitude": -122.44}))
        check("set appearance.location as a persona: rounded, read back",
              out.get("ok") and out.get("value") == {**home, "source": "timezone"},
              out)
        status, body = await http(port, "GET", "/v1/settings/appearance")
        check("...the same place and mode the page's GET reads",
              body["location"] == {**home, "source": "timezone"}
              and body["phone"]["mode"] == "sun", body)
        out = await cli(port, "get", "appearance.location")
        check("get appearance.location", out.get("value") == body["location"], out)
        out = await cli(port, "set", "appearance.location",
                        json.dumps({"lat": 37.8, "lon": -122.4}))
        check("set a location in other words: the broker's refusal",
              out.get("ok") is False and "not a location field" in out.get("error", ""),
              out)
        out = await cli(port, "set", "appearance.location", "null")
        check("set appearance.location null clears it", out.get("ok")
              and out.get("value") is None, out)
        out = await cli(port, "set", "appearance.phone.mode", "system")
        out2 = await cli(port, "set", "appearance.desktop.mode", "light")
        check("...and the modes go back", out.get("value") == "system"
              and out2.get("value") == "light", (out, out2))
        # Sun transition
        status, body = await http(port, "GET", "/v1/settings/appearance")
        check("a reading carries each kind's transition, normal by default",
              body["phone"]["transition"] == "normal"
              and body["desktop"]["transition"] == "normal", body)
        before = body
        for kind in ("phone", "desktop"):
            for value in ("fast", "long", "normal"):
                status, body = await http(port, "PATCH", "/v1/settings/appearance",
                                          {kind: {"transition": value}})
                status2, again = await http(port, "GET", "/v1/settings/appearance")
                other = "desktop" if kind == "phone" else "phone"
                check(f"PATCH {kind} transition {value}, then GET: round-trips, "
                      f"the other kind and every other field untouched",
                      status == 200 and status2 == 200
                      and again[kind]["transition"] == value and again == body
                      and again[other] == before[other]
                      and {f: v for f, v in again[kind].items() if f != "transition"}
                      == {f: v for f, v in before[kind].items() if f != "transition"}
                      and again["location"] == before["location"], (status, again))
        status, body = await http(port, "PATCH", "/v1/settings/appearance",
                                  {"desktop": {"transition": "long", "mode": "sun"}})
        check("the page's Sun body with a transition: both in one PATCH",
              status == 200 and body["desktop"]["transition"] == "long"
              and body["desktop"]["mode"] == "sun", body)
        stored = json.loads(Path(os.environ["LOAM_HOST_SETTINGS"]).read_text())
        check("...persisted in the host settings file",
              stored["appearance"]["desktop"]["transition"] == "long", stored)
        for patch in ({"desktop": {"transition": "slow"}},
                      {"phone": {"transition": 10}},
                      {"phone": {"transition": None}},
                      {"phone": {"transition": "Fast"}},
                      {"phone": {"size": 90, "transition": "medium"}}):
            status, body = await http(port, "PATCH", "/v1/settings/appearance", patch)
            check(f"PATCH refused with a sentence: {json.dumps(patch)}",
                  status == 400 and "transition is fast, normal or long"
                  in body.get("error", ""), (status, body))
        status, body = await http(port, "GET", "/v1/settings/appearance")
        check("...and none of them wrote anything",
              body["desktop"]["transition"] == "long"
              and body["phone"]["transition"] == "normal"
              and body["phone"]["size"] == before["phone"]["size"], body)
        out = await cli(port, "describe", "appearance.phone.transition")
        row = out.get("setting") or {}
        check("describe appearance.phone.transition: the three ids",
              row.get("allowed") == ["fast", "normal", "long"]
              and "default normal" in row.get("values", ""), out)
        out = await cli(port, "get", "appearance.desktop.transition")
        check("get appearance.desktop.transition as a persona: the page's long",
              out.get("ok") and out.get("value") == "long", out)
        print("QUOTE get appearance.desktop.transition ->", json.dumps(out))
        out = await cli(port, "set", "appearance.phone.transition", "fast")
        check("set appearance.phone.transition fast as a persona: read back",
              out.get("ok") and out.get("value") == "fast", out)
        print("QUOTE set appearance.phone.transition fast ->", json.dumps(out))
        status, body = await http(port, "GET", "/v1/settings/appearance")
        check("...the same value the page's GET reads",
              body["phone"]["transition"] == "fast", body)
        out = await cli(port, "set", "appearance.phone.transition", "glacial")
        check("set appearance.phone.transition glacial: the broker's refusal",
              out.get("ok") is False
              and "transition is fast, normal or long" in out.get("error", ""), out)
        print("QUOTE set appearance.phone.transition glacial ->", json.dumps(out))
        out = await cli(port, "set", "appearance.phone.transition", "normal")
        out2 = await cli(port, "set", "appearance.desktop.transition", "normal")
        out3 = await cli(port, "set", "appearance.desktop.mode", "light")
        check("...and the transitions and the desktop mode go back",
              out.get("value") == "normal" and out2.get("value") == "normal"
              and out3.get("value") == "light", (out, out2, out3))

        status, body = await http(port, "PATCH", "/v1/settings/appearance",
                                  {"phone": {"board_view": "doc"}})
        check("appearance refuses the retired board view", status == 400, body)
        out = await cli(port, "describe", "appearance.desktop.theme")
        row = out.get("setting") or {}
        check("describe appearance.desktop.theme: the installed ids, live",
              row.get("allowed") == ["latte", "midnight", "plain", "sepia-night"]
              and "latte" in row.get("values", ""), out)
        out = await cli(port, "describe")
        keys = [r["key"] for r in out.get("settings") or []]
        check("describe omits the retired board view",
              all(not key.endswith(".board_view") for key in keys), keys)
        check("describe lists both theme keys",
              "appearance.phone.theme" in keys and "appearance.desktop.theme" in keys,
              keys)
        out = await cli(port, "set", "appearance.desktop.theme", "plain")
        check("set appearance.desktop.theme plain as a persona: read back",
              out.get("ok") and out.get("value") == "plain", out)
        out = await cli(port, "get", "appearance.desktop.size")
        check("...and the desktop size it did not name is still 90",
              out.get("value") == 90, out)
        out = await cli(port, "get", "appearance.phone.theme")
        check("...and the phone's theme is its own (latte since the write that "
              "dropped its malformed id)", out.get("value") == "latte", out)
        out = await cli(port, "set", "appearance.phone.theme", "mocha")
        check("set appearance.phone.theme mocha: the broker's refusal",
              out.get("ok") is False and "installed theme" in out.get("error", ""), out)
        out = await cli(port, "set", "appearance.desktop.theme", "latte",
                        lane="cron:watch")
        check("a cron may set the theme", out.get("value") == "latte", out)

        out = await cli(port, "do", "thread.delete", "--target", tid, lane="cron:watch")
        check("thread.delete from a cron: refused, the thread is still there",
              out.get("ok") is False and "unattended" in out.get("error", "")
              and threads.read(tid)[0]["id"] == tid, out)
        out = await cli(port, "do", "thread.delete", "--target", tid,
                        lane="conversation")
        try:
            threads.read(tid)
            gone = False
        except threads.ThreadError:
            gone = True
        check("thread.delete in a conversation: deleted", out.get("ok") and gone, out)
    finally:
        tcp.close()
        await tcp.wait_closed()


asyncio.run(main())

# Disabling reflection requires a cited owner instruction.
_on = settings.entry("schedule.enabled")
check("schedule.enabled: a plain value is the switch",
      _on.body(False, "") == {"enabled": False})
check("schedule.enabled: the owner's word rides through as thread and yes",
      _on.body({"enabled": False, "thread": "t-1", "yes": "m4", "x": 1}, "")
      == {"enabled": False, "thread": "t-1", "yes": "m4"})
check("schedule.enabled says the reflection is the owner's to turn off",
      "owner's to turn off" in _on.values)
check("schedule.body no longer refuses the reflection",
      "not the reflection's" not in settings.entry("schedule.body").values)
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if results and all(results) else 1)
