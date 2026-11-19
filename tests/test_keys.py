#!/usr/bin/env python3
"""Shared keys (2026-11-18): Settings › Connections › API keys, lib/loam_keys.py.

  - the manifest's `keys:`: a provider Loam knows takes Loam's fields; another
    needs its name and fields; a wrong one breaks the row with a sentence;
    the declaration shows it beside `touches`, and a later version that adds
    a provider asks again
  - a key is checked before it is kept (a fake OpenAI), 0600, never in a
    listing whole; a refusal keeps nothing
  - resolution: a plugin's own key, then the shared one, then the box's
    secrets file; a plugin that declared no provider is handed nothing
  - keys.env: each declaring plugin's resolved keys and nothing else, 0600,
    rewritten on a change, gone when the plugin stops declaring
  - adopt: a key a plugin kept before shared keys becomes the shared one
  - the broker's routes, an agent's `by` accepted and logged, and the app
    server's /value route resolving a declared field
  - read aloud and dictation declare Google

Run: python3 tests/test_keys.py
"""

import asyncio
import http.server
import importlib.machinery
import importlib.util
import json
import os
import stat
import sys
import tempfile
import threading
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="loam-keys-"))
YOURS = TMP / "yours"
SHIPPED = TMP / "shipped"
YOURS.mkdir()
SHIPPED.mkdir()
os.environ["LOAM_PLUGIN_SHIPPED"] = str(SHIPPED)
os.environ["LOAM_PLUGINS_DIR"] = str(YOURS)
os.environ["LOAM_PLUGIN_STATE"] = str(TMP / "plugin-state")
os.environ["LOAM_VAULT"] = str(TMP / "vault")
os.environ["LOAM_CONTRIBUTIONS_FOLLOW"] = "0"
os.environ["LOAM_THREADS_STATE"] = str(TMP / "threads")
os.environ["LOAM_UI_CORE_OPTIONS"] = "none"
os.environ["LOAM_UI_APPEARANCE_REGISTRY"] = "none"
os.environ["LOAM_SECRETS_FILE"] = str(TMP / "box-secrets.env")
(TMP / "vault").mkdir()
(TMP / "box-secrets.env").write_text("# the box's own\nGEMINI_API_KEY=AIzaBOX\n")


def plugin(pid, head, layer=SHIPPED):
    folder = layer / pid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "PLUGIN.md").write_text(
        f"---\nid: {pid}\nname: {pid.title()}\nversion: 1.0.0\napi: 0\n"
        f"kind: [extension]\n{head}---\n\n{pid}.\n")


plugin("pics", "keys:\n  - provider: openai\n    why: makes the pictures\n    optional: true\n"
               "  - provider: google\n    why: makes them too\n    optional: true\n")
plugin("talk", "keys:\n  - provider: google\n    why: speaks\n")
plugin("plain", "")
plugin("odd", "keys:\n  - provider: replicate\n    name: Replicate\n    why: runs models\n"
              "    fields: [{name: REPLICATE_API_TOKEN, label: API token}]\n")
plugin("wrong", "keys:\n  - provider: replicate\n    why: no fields\n")
plugin("twice", "keys: [openai, openai]\n")

sys.path.insert(0, str(AGENTS / "lib"))
import loam_plugins as lp  # noqa: E402
import loam_contributions as c  # noqa: E402
import loam_keys as k  # noqa: E402

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:72}"
          f"{'  | ' + str(detail)[:300] if detail != '' else ''}")


def row(pid):
    return next(r for r in lp._rows() if r["id"] == pid)


# -- the manifest -----------------------------------------------------------------
pics = row("pics")
check("keys: a known provider takes Loam's fields",
      [x["provider"] for x in pics["keys"]] == ["openai", "google"]
      and pics["keys"][0]["fields"][0]["name"] == "OPENAI_API_KEY", pics.get("keys"))
check("keys: the declaration shows them beside touches",
      pics["declaration"]["touches"]["keys"] == [
          {"provider": "openai", "name": "OpenAI", "why": "makes the pictures", "optional": True},
          {"provider": "google", "name": "Google Gemini", "why": "makes them too", "optional": True}],
      pics["declaration"]["touches"].get("keys"))
odd = row("odd")
check("keys: an unknown provider brings its own name and fields",
      odd["keys"][0]["name"] == "Replicate"
      and odd["keys"][0]["fields"][0]["name"] == "REPLICATE_API_TOKEN", odd.get("keys"))
check("keys: an unknown provider without fields breaks the row, in a sentence",
      "needs a `name`" in (row("wrong")["broken"] or ""), row("wrong")["broken"])
check("keys: a provider named twice breaks the row",
      "named twice" in (row("twice")["broken"] or ""), row("twice")["broken"])
before = row("talk")["declaration"]
after = {**before, "touches": {**before["touches"],
                               "keys": before["touches"]["keys"] + [
                                   {"provider": "openai", "name": "OpenAI", "why": "x"}]}}
check("a later version that adds a provider asks again",
      "keys" in lp.added(before, after), lp.added(before, after))


# -- a fake OpenAI ------------------------------------------------------------------
class Fake(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        auth = self.headers.get("Authorization", "")
        code = 200 if auth == "Bearer sk-good-0000000000001234" else 401
        data = json.dumps({"data": []} if code == 200 else {"error": {"message": "bad"}}).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


srv = http.server.HTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["LOAM_OPENAI_URL"] = f"http://127.0.0.1:{srv.server_address[1]}"

GOOD = "sk-good-0000000000001234"
try:
    k.set_shared("openai", {"value": "sk-wrong"})
    refused = False
except k.KeyError_ as exc:
    refused = "not a valid API key" in str(exc)
check("a key the provider refuses is not kept, and says why", refused
      and not (k.keys_dir() / "openai.env").exists())
answer = k.set_shared("openai", {"value": f"  {GOOD} "}, by="iris")
path = k.keys_dir() / "openai.env"
check("a good key is kept trimmed, 0600 in a 0700 folder",
      path.read_text() == f"OPENAI_API_KEY={GOOD}\n"
      and stat.S_IMODE(path.stat().st_mode) == 0o600
      and stat.S_IMODE(path.parent.stat().st_mode) == 0o700, path.read_text())
listing = json.dumps(k.listing())
check("the listing never carries the value; it shows the last four",
      GOOD not in listing and answer["shown"] == {"OPENAI_API_KEY": "…1234"}
      and answer["source"] == "saved" and answer["checked"]["ok"], answer)
ids = [p["id"] for p in k.listing()["providers"]]
check("API keys lists Loam's three first, then the ones plugins declare",
      ids[:3] == ["openai", "google", "hetzner"] and "replicate" in ids
      and "modal" not in ids, ids)

# -- resolution ---------------------------------------------------------------------
check("a declaring plugin gets the shared key", c.secret_for("pics", "OPENAI_API_KEY") == GOOD)
check("a plugin that did not declare the provider gets nothing",
      c.secret_for("talk", "OPENAI_API_KEY") is None
      and c.secret_for("plain", "OPENAI_API_KEY") is None)
check("no shared Google key: the box's own secrets file still serves",
      c.secret_for("talk", "GEMINI_API_KEY") == "AIzaBOX"
      and k.one("google")["source"] == "box")
os.environ["LOAM_GEMINI_URL"] = f"http://127.0.0.1:{srv.server_address[1]}"   # any answer
k.CHECKS["gemini"] = lambda v: (v.startswith("AIza"), "Google accepted the key." if
                               v.startswith("AIza") else "Google says no.")
k.set_own("google", "talk", {"value": "AIzaOWN"})
check("a plugin's own key wins for it alone",
      c.secret_for("talk", "GEMINI_API_KEY") == "AIzaOWN"
      and c.secret_for("pics", "GEMINI_API_KEY") == "AIzaBOX")
g = k.one("google")
check("...and API keys says which plugin uses its own",
      {u["id"]: u["uses"] for u in g["plugins"]} == {"pics": "shared", "talk": "own"}, g["plugins"])
try:
    k.set_own("openai", "talk", {"value": GOOD})
    held = False
except k.KeyError_ as exc:
    held = exc.status == 409 and "never given one" in str(exc)
check("a plugin cannot be given a key of a provider it did not declare", held)
k.use_shared("google", "talk")
check("use the shared key again: its own is forgotten",
      c.secret_for("talk", "GEMINI_API_KEY") == "AIzaBOX")

# -- keys.env -----------------------------------------------------------------------
changed = k.sync()
kf = k.keys_file("pics")
text = kf.read_text()
check("keys.env: the declared providers' keys, resolved, 0600",
      f"OPENAI_API_KEY={GOOD}" in text and "GEMINI_API_KEY=AIzaBOX" in text
      and stat.S_IMODE(kf.stat().st_mode) == 0o600, text)
check("keys.env: a plugin that declares nothing has none",
      not k.keys_file("plain").exists())
check("keys.env: unchanged is not rewritten", "pics" not in k.sync())
k.forget_shared("openai")
k.sync()
check("keys.env: a forgotten key leaves it", "OPENAI_API_KEY" not in kf.read_text())
plugin("pics", "")
k.sync()
check("keys.env: gone when the plugin stops declaring", not kf.exists())
plugin("pics", "keys:\n  - provider: openai\n    why: makes the pictures\n")
env = c._env({"id": "pics", "folder": SHIPPED / "pics", "settings": TMP, "data": TMP, "keys": True})
check("a plugin's process is told where its keys.env is, a path only",
      env.get("LOAM_PLUGIN_KEYS") == str(kf))

# -- adopt --------------------------------------------------------------------------
priv = TMP / "plugin-state" / "private" / "odd"
priv.mkdir(parents=True, exist_ok=True)
(priv / "secrets.env").write_text("REPLICATE_API_TOKEN=r8_old\nOTHER=kept\n")
did = k.adopt()
check("adopt: a plugin's own key from before becomes the shared one",
      (k.keys_dir() / "replicate.env").read_text() == "REPLICATE_API_TOKEN=r8_old\n"
      and (priv / "secrets.env").read_text() == "OTHER=kept\n" and did, did)
check("adopt: idempotent", k.adopt() == [])


# -- the routes ---------------------------------------------------------------------
import loam_broker as cfg  # noqa: E402
cfg.memory = lambda: {"available": None, "cgroup_room": None, "reserve": cfg.MEMORY_RESERVE_MB}
cfg.roster = lambda: ["iris"]
loader = importlib.machinery.SourceFileLoader("session_broker", str(AGENTS / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker_mod = importlib.util.module_from_spec(spec)
loader.exec_module(broker_mod)


class FakeBroker:
    sessions = {}
    token = "t"
    stopping = False


async def call(port, method, path, body=None):
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
        st, body = await call(port, "GET", "/v1/keys")
        check("GET /v1/keys", st == 200 and body["providers"][0]["id"] == "openai", st)
        st, body = await call(port, "PUT", "/v1/keys/openai", {"value": "sk-nope"})
        check("PUT a refused key: 422 with the provider's sentence",
              st == 422 and "not a valid API key" in body["error"], body)
        st, body = await call(port, "PUT", "/v1/keys/openai", {"value": GOOD, "by": "iris"})
        check("PUT by an agent: saved, attributed, no value in the answer",
              st == 200 and body["set"] and body["checked"]["by"] == "iris"
              and GOOD not in json.dumps(body), body)
        st, body = await call(port, "PUT", "/v1/keys/openai", {"value": GOOD, "by": "nobody"})
        check("PUT by a name not on the box: 400", st == 400, body)
        st, body = await call(port, "GET", "/v1/plugins/pics/secrets/OPENAI_API_KEY/value")
        check("the app server's /value route resolves a declared field",
              st == 200 and body["value"] == GOOD, st)
        st, body = await call(port, "GET", "/v1/plugins/talk/secrets/OPENAI_API_KEY/value")
        check("...and refuses a field the plugin did not declare", st == 404, body)
        st, body = await call(port, "DELETE", "/v1/keys/openai")
        check("DELETE forgets it", st == 200 and not body["set"], body)
        st, body = await call(port, "DELETE", "/v1/keys/google")
        check("DELETE of a key set on the box itself: 404, saying so",
              st == 404 and "own secrets file" in body["error"], body)
        st, body = await call(port, "GET", "/v1/keys/nothing")
        check("an unknown provider: 404", st == 404, body)
    finally:
        tcp.close()
        await tcp.wait_closed()


asyncio.run(routes())

# -- the shipped plugins --------------------------------------------------------------
for name in ("read-aloud", "dictation"):
    data, _ = lp.parse_frontmatter((AGENTS / "plugins" / "_listed" / name / "PLUGIN.md").read_text())
    man, _ = lp.manifest_of(data, name)
    check(f"{name} declares Google's key", [x["provider"] for x in man.get("keys") or []]
          == ["google"], man.get("keys"))
    # Its settings.secret of the same name stays for one release, so a broker
    # from before 0.2.3 still hands it the key; a field of a declared
    # provider resolves through keys first all the same.
    check(f"{name}: its old settings.secret is the same name",
          [x["name"] for x in man["contributes"]["settings.secret"]] == ["GEMINI_API_KEY"])

srv.shutdown()
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
