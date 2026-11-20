#!/usr/bin/env python3
"""Check the authenticated passkey relay and how upstream success and failure appear to
the signed-in user.

Run: python3 tests/test_passkeys_relay.py
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import importlib.machinery
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import loam_deploy as dep  # noqa: E402

fails = 0


def check(label: str, got, want=True) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:64} {got!r}"[:240]
          + ("" if ok else f"   want {want!r}"))


def load_claim():
    loader = importlib.machinery.SourceFileLoader("loam_claim", str(ROOT / "bin" / "loam-claim"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


SEEN: list[dict] = []
ROWS = [{"id": 3, "description": "Phone", "created_at": "2026-11-15T23:40:00Z",
         "last_used_at": "2026-11-17T08:00:00Z", "rpid": "auth.example.test",
         "public_key": "c2VjcmV0", "kid": "a2lk", "sign_count": 9}]


class FakeLogin(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def answer(self, status, body):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        SEEN.append({"method": "GET", "path": self.path, **dict(self.headers)})
        if "loam_auth=good" not in (self.headers.get("Cookie") or ""):
            return self.answer(403, {"status": "KO", "message": "Forbidden"})
        return self.answer(200, {"status": "OK", "data": ROWS})

    def do_DELETE(self):  # noqa: N802
        SEEN.append({"method": "DELETE", "path": self.path, **dict(self.headers)})
        if "loam_auth=good" not in (self.headers.get("Cookie") or ""):
            return self.answer(403, {"status": "KO", "message": "Forbidden"})
        return self.answer(403, {"status": "KO", "message": "elevation required"})


def main() -> int:
    claim = load_claim()
    tmp = Path(tempfile.mkdtemp(prefix="test-passkeys-"))
    saved = {k: getattr(dep, k) for k in ("EDGE", "CLAIMED_FILE", "PUBLIC_FILE",
                                          "SHARE_DIR", "SITES_DIR", "DEPLOY", "CONFIG_PATH")}
    try:
        dep.EDGE = {}
        dep.CLAIMED_FILE = tmp / "claimed"
        dep.PUBLIC_FILE = tmp / "public"
        dep.SHARE_DIR = tmp / "share"
        dep.SITES_DIR = tmp / "sites"
        dep.DEPLOY = ROOT / "deploy"
        dep.CONFIG_PATH = dep.DEPLOY / "loam.toml"

        # -- the route ---------------------------------------------------------
        today = dt.date(2026, 11, 17)
        shell = next(a for a in dep.records() if a.app_shell and a.apex)
        staging = dataclasses.replace(shell, name="loam-ui-next", host="next", apex=False,
                                      port=8082, content_host=None)
        for app in (shell, staging):
            block = dep.caddy_host_block(app, False, today)
            at, sight = block.find(dep.PASSKEYS_PATH), block.find("@sight")
            check(f"{app.fqdn}: the passkeys route ahead of the browser's subnet",
                  0 < at < sight)
            check(f"{app.fqdn}: ...through the gate, to the claim service",
                  block[at:sight].count("import gate") == 1
                  and f"reverse_proxy 127.0.0.1:{dep.CLAIM_PORT}" in block[at:sight])
        check("the login-code relay stays on the domain itself only",
              (dep.RELAY_PATH in dep.caddy_host_block(shell, False, today),
               dep.RELAY_PATH in dep.caddy_host_block(staging, False, today)), (True, False))
        check("the login's session cookie keeps its name",
              "name: loam_auth" in dep.authelia_config())

        caddy = shutil.which("caddy")
        if caddy:
            text = dep.GATE_SNIPPET + "\n" + dep.caddy_host_block(staging, False, today)
            text = text.replace("import access_log", "# import access_log").replace(
                "import compress", "# import compress")
            path = tmp / "Caddyfile"
            path.write_text(text)
            res = subprocess.run([caddy, "adapt", "--config", str(path), "--adapter", "caddyfile"],
                                 capture_output=True, text=True, check=False)
            doc = json.loads(res.stdout) if res.returncode == 0 else {}
            routes = []
            for srv in doc.get("apps", {}).get("http", {}).get("servers", {}).values():
                for r in srv.get("routes", []):
                    routes = r["handle"][0]["routes"]
            order = ["passkeys" if dep.PASSKEYS_PATH in json.dumps(r) else
                     "sight" if dep.SIGHT_SUBNET in json.dumps(r) else "other" for r in routes]
            check("Caddy itself orders the passkeys route before the browser's subnet",
                  "passkeys" in order and "sight" in order
                  and order.index("passkeys") < order.index("sight"))
            route = next((r for r in routes if dep.PASSKEYS_PATH in json.dumps(r)), {})
            check("...and that route asks the login first",
                  "/api/authz/forward-auth" in json.dumps(route))
        else:
            print("SKIP  caddy is not on this box: the order is checked on the text only")

        # -- the relay ---------------------------------------------------------
        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeLogin)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_port}"

        check("the session cookie is taken out alone",
              claim.session_cookie("theme=dark; loam_auth=good; other=x"), "good")
        check("...and no session cookie is None", claim.session_cookie("theme=dark"), None)
        check("the portal is the request's cookie domain's",
              claim.portal_for(f"next.{dep.domain()}:443"), f"auth.{dep.domain()}")

        SEEN.clear()
        status, body = claim.passkeys("good", f"next.{dep.domain()}", "198.51.100.4", base)
        check("a signed-in session gets its passkeys, four fields each", (status, body),
              (200, {"passkeys": [{"id": 3, "description": "Phone",
                                   "created_at": "2026-11-15T23:40:00Z",
                                   "last_used_at": "2026-11-17T08:00:00Z"}]}))
        sent = SEEN[-1] if SEEN else {}
        check("...asked on the login's own list", sent.get("path"), claim.WEBAUTHN_API)
        check("...with the session cookie and no other",
              sent.get("Cookie"), "loam_auth=good")
        check("...on the portal, as https, for the person's address",
              (sent.get("X-Forwarded-Host"), sent.get("X-Forwarded-Proto"),
               sent.get("X-Forwarded-For")), (f"auth.{dep.domain()}", "https", "198.51.100.4"))
        status, body = claim.passkeys("stale", None, None, base)
        check("a session the login refuses is a 403", status, 403)

        status, body = claim.remove_passkey("good", "3", None, None, base)
        check("removal is the login's own DELETE on the one credential",
              (SEEN[-1]["method"], SEEN[-1]["path"]), ("DELETE", f"{claim.WEBAUTHN_ONE}/3"))
        check("...and its refusal (an elevation it wants) is passed on",
              (status, body.get("error")), (403, "elevation required"))

        # The handler: what reaches the login at all.
        class Fake:
            def __init__(self, path, headers):
                self.path, self.headers, self.sent = path, headers, []

            def json(self, status, obj):
                self.sent.append((status, obj))

        for name in ("signed_in", "client", "passkeys", "do_DELETE"):
            setattr(Fake, name, getattr(claim.Handler, name))
        claim.AUTHELIA = base
        SEEN.clear()
        f = Fake(dep.PASSKEYS_PATH, {"Cookie": "loam_auth=good"})
        f.passkeys()
        check("no gate user: a 401, and the login is not asked",
              (f.sent[0][0], len(SEEN)), (401, 0))
        f = Fake(dep.PASSKEYS_PATH, {"Remote-User": "sam", "Cookie": "theme=x"})
        f.passkeys()
        check("no session cookie: a 401, and the login is not asked",
              (f.sent[0][0], len(SEEN)), (401, 0))
        f = Fake(dep.PASSKEYS_PATH, {"Remote-User": "sam", "Cookie": "loam_auth=good",
                                     "Host": dep.domain(), "X-Forwarded-For": "198.51.100.4, 10.0.0.1"})
        f.passkeys()
        check("signed in: the list", (f.sent[0][0], len(f.sent[0][1]["passkeys"])), (200, 1))
        check("...for the first address Caddy names", SEEN[-1].get("X-Forwarded-For"), "198.51.100.4")
        SEEN.clear()
        f = Fake(dep.PASSKEYS_PATH + "/../x", {"Remote-User": "sam", "Cookie": "loam_auth=good"})
        f.do_DELETE()
        check("a malformed id is a 404 and asks nothing", (f.sent[0][0], len(SEEN)), (404, 0))
        server.shutdown()
    finally:
        for k, v in saved.items():
            setattr(dep, k, v)
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
