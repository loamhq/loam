#!/usr/bin/env python3
"""An install's edge and its renders (first release L12): lib/loam_install.py,
the `[edge]` table in lib/loam_deploy.py, and the claim service's code reader.

What earns a case here:

  - a box without `[edge]` (the owner's) renders exactly what it did: the
    module's edge is empty and nothing of the claim, the relay or the gate
    appears;
  - an install renders NO host until its name has been seen resolving here,
    because Caddy would ask Let's Encrypt for every host it loads;
  - while unclaimed, the app's host answers /claim outside the login and
    sends everything else there, and nothing else about the hosts changes;
  - after the claim, the relay path goes through the gate and is matched
    ahead of the browser's subnet (asked of Caddy itself, `caddy adapt`, when
    Caddy is on the box), so a persona's browser never sees a login code;
  - the app is served at the install's domain itself, Authelia covers that
    name, and only the records the edge names are served;
  - the install's configuration is one loam_config reads, with the edge in it;
  - the name check tells "here", "taken", and "only the base's wildcard
    answers" apart (Pushback 2: `*.<base domain>` answers every name today);
  - the reach.md an install writes names this install's places only;
  - the claim service finds the newest one-time code in Authelia's file
    notifier, and only a recent one.

Runs on throwaway paths; nothing on the box is read or written.

Run: python3 tests/test_install.py
"""

from __future__ import annotations

import datetime as dt
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import tempfile
import time
from contextlib import redirect_stdout
from http.client import HTTPConnection
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="test-install-"))
sys.path.insert(0, str(ROOT / "lib"))
import loam_config as lc  # noqa: E402
import loam_deploy as dep  # noqa: E402
import loam_install as li  # noqa: E402

FAILURES = 0


def check(label: str, got, want=True) -> None:
    global FAILURES  # noqa: PLW0603
    ok = got == want
    FAILURES += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f"\n      got  {got!r}\n      want {want!r}"))


EDGE = {"name": "sam", "domain": "sam.loam-proof.test", "address": "203.0.113.7",
        "app": "loam-ui", "app_checkout": str(ROOT), "app_context": "app",
        "apps": ["loam-ui", "files", "vault"], "resolver": "system", "tls": "internal",
        "dns_gate": True}


def load_cmd(name: str):
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(ROOT / "bin" / name))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def main() -> int:
    saved = {k: getattr(dep, k) for k in ("EDGE", "EDGE_STATE", "CLAIM_FILE", "CLAIMED_FILE",
                                          "PUBLIC_FILE", "SHARE_DIR", "SITES_DIR",
                                          "DEPLOY", "CONFIG_PATH")}
    try:
        return cases()
    finally:
        for k, v in saved.items():
            setattr(dep, k, v)
        shutil.rmtree(TMP, ignore_errors=True)


def exercise_claim(claim, unclaimed_render: str) -> None:
    dep.CLAIMED_FILE.unlink()
    with patch.object(li, "CLAIM_FILE", TMP / "claim-token"), \
         patch.object(dep, "CADDYFILE", TMP / "claim-Caddyfile"):
        li.CLAIM_FILE.write_text(json.dumps({"token": "fixture-token"}))
        dep.CADDYFILE.write_text(unclaimed_render)
        saving = threading.Event()
        writing = threading.Event()
        starting = threading.Event()

        def hash_password(_password):
            saving.set()
            if not writing.wait(5):
                raise RuntimeError("fixture never released password hashing")
            return "fixture-hash"

        def finish():
            starting.set()

        with patch.object(claim, "hash_password", side_effect=hash_password), \
             patch.object(claim, "write_users") as write_users, \
             patch.object(claim, "finish_claim", side_effect=finish):
            server = claim.ThreadingHTTPServer(("127.0.0.1", 0), claim.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            def request(method, path, body=None):
                conn = HTTPConnection(*server.server_address, timeout=5)
                try:
                    conn.request(method, path, json.dumps(body) if body is not None else None)
                    res = conn.getresponse()
                    content = res.read()
                    return res.status, json.loads(content) if res.headers["Content-Type"] == "application/json" else content
                finally:
                    conn.close()

            body = {"token": "fixture-token", "username": "PageTester", "password": "fixture password"}
            try:
                check("claim status begins open and contains no account information",
                      request("GET", "/claim?status"), (200, {"stage": "open"}))
                check("claim refuses a malformed body", request("POST", "/claim", [])[0], 400)
                check("password validation happens before starting the worker",
                      request("POST", "/claim", {**body, "password": "short"})[0], 400)
                check("valid claim is accepted while the password hash is still running",
                      request("POST", "/claim", body), (202, {"ok": True}))
                check("the worker entered the hashing step", saving.wait(5))
                check("status is responsive while hashing",
                      request("GET", "/claim?status"), (200, {"stage": "saving"}))
                check("a second submission cannot replace an in-flight password",
                      request("POST", "/claim", {**body, "password": "another password"})[0], 409)
                check("token is kept until the admin is saved", li.CLAIM_FILE.exists())
                writing.set()
                check("the worker advances even after the POST connection closed", starting.wait(5))
                check("the admin is saved once under the lowercase username",
                      write_users.call_args.args, ("pagetester", lc.OWNER_NAME, "fixture-hash"))
                check("the claim marker matches the user's stored name",
                      json.loads(dep.CLAIMED_FILE.read_text())["username"], "pagetester")
                check("the spent token is removed", li.CLAIM_FILE.exists(), False)
                check("the page can be reopened while the portal starts", request("GET", "/claim")[0], 200)
                check("status reports sign-in startup without revealing the username",
                      request("GET", "/claim?status"), (200, {"stage": "starting_login"}))
                check("the claim cannot be repeated", request("POST", "/claim", body)[0], 410)
                check("a restart would resume the unfinished claim", claim.unfinished_claim())
            finally:
                writing.set()
                server.shutdown()
                server.server_close()
                thread.join()

        dep.CLAIMED_FILE.unlink()
        li.CLAIM_FILE.write_text(json.dumps({"token": "fixture-token"}))
        with patch.object(claim, "hash_password", side_effect=RuntimeError("fixture failure")):
            claim.save_claim("pagetester", "fixture password")
        check("save failure leaves the welcome link usable",
              (claim.CLAIM_STAGE, dep.CLAIMED_FILE.exists(), li.CLAIM_FILE.exists()),
              ("save_failed", False, True))
        dep.CLAIMED_FILE.write_text("{}")
        with patch.object(claim, "run", return_value=Mock(returncode=1, stdout="", stderr="failed")), \
             patch.object(claim.time, "sleep"):
            claim.finish_claim()
        check("exhausted activation retries report a saved sign-in that still needs help",
              (claim.CLAIM_STAGE, claim.unfinished_claim()), ("finish_failed", True))


def exercise_claim_edge(unclaimed: str, claimed: str) -> None:
    gen = load_cmd("gen-deploy")
    caddyfile = TMP / "generator-Caddyfile"
    secrets = TMP / "authelia-secrets"
    secrets.touch()
    caddyfile.write_text(unclaimed)
    files = {caddyfile: claimed, dep.SUDOERS: "", dep.AUTHELIA_CONFIG: ""}
    with patch.multiple(dep, CADDYFILE=caddyfile, AUTHELIA_SECRETS=secrets,
                        generated=lambda: files, records=lambda: [], preview_sysctl_live=lambda: True,
                        sight_wall_missing=lambda: [], inbound_wall_status=lambda: ""), \
         patch.multiple(gen, ensure_access_log_dir=Mock(), validate_caddyfile=Mock(),
                        validate_sudoers=Mock(), validate_authelia=Mock(), ensure_static_roots=Mock(),
                        warn_unresolved=Mock(), write_root=lambda path, text, *_: path.write_text(text),
                        diff=Mock(side_effect=lambda path, _new: path == caddyfile),
                        compose=Mock(return_value=Mock(returncode=0)), run=Mock(return_value=Mock(returncode=0)),
                        wait_for_login=Mock(side_effect=SystemExit(1))):
        try:
            gen.generate(False)
        except SystemExit as exc:
            check("generator defers edge change when login health fails", exc.code, 1)
        check("health failure leaves the claim route on disk", caddyfile.read_text(), unclaimed)
        check("health failure never reloads Caddy", gen.run.call_count, 0)
        gen.wait_for_login.side_effect = None
        gen.run.return_value = Mock(returncode=1, stderr="reload refused")
        try:
            gen.generate(False)
        except SystemExit as exc:
            check("generator surfaces a claim reload failure", exc.code, 1)
        check("reload failure also keeps the claim route on disk", caddyfile.read_text(), unclaimed)
        gen.run.return_value = Mock(returncode=0)
        check("after Authelia is ready, the generator completes the claim", gen.generate(False), 0)
        check("the live render is persisted once Caddy accepts it", caddyfile.read_text(), claimed)


def cases() -> int:
    today = dt.date(2026, 11, 14)
    dep.PUBLIC_FILE = TMP / "public"
    dep.SHARE_DIR = TMP / "share"
    dep.SITES_DIR = TMP / "sites"
    # This tree's records, not the live checkout's: a job's tree differs.
    dep.DEPLOY = ROOT / "deploy"
    dep.CONFIG_PATH = dep.DEPLOY / "loam.toml"

    # -- a box without [edge] ------------------------------------------------
    dep.EDGE = {}
    dep.CLAIMED_FILE = TMP / "claimed"
    plain = dep.caddyfile(today)
    check("no [edge]: no claim, local certs or gate comment",
          [w for w in ("/claim", "local_certs", "No host yet") if w in plain], [])
    # An apex app can provide the relay without an edge record.
    apex = [a for a in dep.records() if a.apex]
    check("no [edge]: the record that says apex is the domain itself",
          [(a.name, a.fqdn) for a in apex], [("loam-ui", dep.domain())])
    check("...and its host carries the relay, behind the gate",
          f"handle {dep.RELAY_PATH} {{\n\t\timport gate" in plain, True)
    # The old names: one block each, the redirect alone, the app's to the apex.
    moved = dep.moved()
    if moved:
        old_app = f"loam-ui.{moved['from']}"
        targets = dict(dep.moved_targets())
        check("[moved]: the app's old host goes to the domain itself",
              targets.get(old_app), f"https://{dep.domain()}")
        check("...every other old host to its own label",
              [o for o, n in targets.items() if o != old_app
               and n != f"https://{o.removesuffix('.' + moved['from'])}.{dep.domain()}"], [])
        blocks = dict(dep._live_blocks(plain))
        body = blocks.get(old_app, "")
        check("...and its block is the log and the 308 alone, no gate",
              [ln for ln in body.splitlines() if ln and ln != "}" and not ln.startswith(("import access_log", "import compress"))],
              [f"redir https://{dep.domain()}{{uri}} 308"])
        check("...and is not overdue on its own date, but is the day after",
              (dep.moved_overdue(moved["until"]),
               dep.moved_overdue(moved["until"] + dt.timedelta(days=1))),
              (None, moved["until"]))
    check("no [edge]: never unclaimed, always ready", (dep.unclaimed(), dep.edge_ready()), (False, True))
    check("no [edge]: the domain is deploy/loam.toml's", dep.domain(),
          __import__("tomllib").loads((ROOT / "deploy" / "loam.toml").read_text())["domain"])

    # -- an install, before DNS ---------------------------------------------
    dep.EDGE = dict(EDGE)
    dep.EDGE_STATE = TMP / "edge.json"
    check("the install's domain is its edge's", dep.domain(), "sam.loam-proof.test")
    check("only the records the edge names are served",
          sorted(a.name for a in dep.records()), ["files", "loam-ui", "vault"])
    app = {a.name: a for a in dep.records()}["loam-ui"]
    check("the app is at the domain itself, built from the edge's checkout and folder",
          (app.fqdn, app.apex, app.checkout, app.context),
          ("sam.loam-proof.test", True, ROOT, "app"))
    before = dep.caddyfile(today)
    check("before the name resolves here: not one host block",
          ([h for h, _ in dep.live_host_blocks(before)], "No host yet" in before), ([], True))
    check("...and Caddy's own authority on a rehearsal", "\tlocal_certs" in before.split("(gate)")[0])

    # -- resolved, unclaimed --------------------------------------------------
    dep.EDGE_STATE.write_text(json.dumps({"dns": "resolved", "domain": "sam.loam-proof.test"}))
    check("resolved for another domain does not count",
          (json.loads(dep.EDGE_STATE.read_text())["domain"] == dep.EDGE["domain"]) and dep.edge_ready())
    unclaimed = dep.caddyfile(today)
    hosts = [h for h, _ in dep.live_host_blocks(unclaimed)]
    check("resolved: every host is rendered",
          sorted(hosts), sorted(["auth.sam.loam-proof.test", "sam.loam-proof.test",
                                 "files.sam.loam-proof.test", "vault.sam.loam-proof.test",
                                 "loam-ui-content.sam.loam-proof.test"]))
    block = dict(dep._live_blocks(unclaimed))["sam.loam-proof.test"]  # noqa: SLF001
    check("unclaimed: the app's host is the claim, outside the login",
          ("reverse_proxy 127.0.0.1:8934" in block, "import gate" in block,
           f"redir * {dep.CLAIM_PATH} 302" in block), (True, False, True))
    vault_block = dict(dep._live_blocks(unclaimed))["vault.sam.loam-proof.test"]  # noqa: SLF001
    check("unclaimed: every other host is gated as ever", "import gate" in vault_block)
    rules = __import__("yaml").safe_load(dep.authelia_config())["access_control"]["rules"]
    check("Authelia covers the domain itself and every name under it",
          {"domain": ["sam.loam-proof.test", "*.sam.loam-proof.test"], "policy": "one_factor"} in rules)
    # No one reads an install's file notifier, so its sign-in page offers no
    # password reset that would wait for an email (the first-day walk).
    check("Authelia itself matches usernames without regard to case",
          __import__("yaml").safe_load(dep.authelia_config())["authentication_backend"]["file"]["search"],
          {"case_insensitive": True})
    reset = lambda: __import__("yaml").safe_load(dep.authelia_config())[  # noqa: E731
        "authentication_backend"]["password_reset"]["disable"]
    check("an install's sign-in page offers no password reset", reset(), True)
    edge_was, dep.EDGE = dep.EDGE, {}
    try:
        check("...the first install's still does (its engineer reads the notifier)", reset(), False)
    finally:
        dep.EDGE = edge_was

    # -- claimed ---------------------------------------------------------------
    dep.CLAIMED_FILE.write_text("{}")
    claimed = dep.caddyfile(today)
    block = dict(dep._live_blocks(claimed))["sam.loam-proof.test"]  # noqa: SLF001
    check("claimed: no claim route, the app behind the gate",
          ("/claim" in block, "import gate" in block, "reverse_proxy 127.0.0.1:8081" in block),
          (False, True, True))
    relay_at, sight_at = block.find(dep.RELAY_PATH), block.find("@sight")
    check("claimed: the relay is written ahead of the browser's subnet, through the gate",
          (0 < relay_at < sight_at, "import gate" in block[relay_at:sight_at]), (True, True))
    caddy = shutil.which("caddy")
    if caddy:
        path = TMP / "Caddyfile"
        path.write_text(claimed)
        res = subprocess.run([caddy, "adapt", "--config", str(path), "--adapter", "caddyfile"],
                             capture_output=True, text=True, check=False)
        doc = json.loads(res.stdout) if res.returncode == 0 else {}
        routes = []
        for srv in doc.get("apps", {}).get("http", {}).get("servers", {}).values():
            for r in srv.get("routes", []):
                if "sam.loam-proof.test" in json.dumps(r.get("match", [])) and \
                        "loam-ui-content" not in json.dumps(r.get("match", [])) and \
                        any("sam.loam-proof.test" == h for m in r.get("match", []) for h in m.get("host", [])):
                    routes = r["handle"][0]["routes"]
        order = []
        for r in routes:
            text = json.dumps(r)
            order.append("relay" if dep.RELAY_PATH in text else "sight" if "172.30.0.0/24" in text
                         else "other")
        check("Caddy itself orders the relay before the browser's subnet",
              order.index("relay") < order.index("sight") if "relay" in order and "sight" in order else order,
              True)
        relay_route = next((r for r in routes if dep.RELAY_PATH in json.dumps(r)), {})
        check("...and the relay's route asks the login first",
              '"forward-auth"' in json.dumps(relay_route) or "/api/authz/forward-auth" in json.dumps(relay_route))
    else:
        print("SKIP  caddy is not on this box: the order is checked on the text only")

    # -- the configuration an install writes ---------------------------------
    import getpass  # noqa: PLC0415
    me = getpass.getuser()   # an existing user: loam_config looks the user up
    check("an install runs as the user `loam`", li.render_config(
        name="sam", owner="Sam", timezone="UTC", address="203.0.113.7",
        home="/home/loam").splitlines()[5], 'user = "loam"')
    text = li.render_config(name="sam", owner="Sam", timezone="Europe/Berlin",
                            address="203.0.113.7", home=str(TMP / "home"), user=me,
                            resolver="system", tls="internal")
    cfg_path = TMP / "loam.toml"
    cfg_path.write_text(text)
    out = subprocess.run([sys.executable, "-c",
                          "import sys, json; sys.path.insert(0, sys.argv[1]); import loam_config as lc; "
                          "print(json.dumps([lc.OWNER_NAME, lc.TIMEZONE, str(lc.ROOT), str(lc.WORKSPACES), "
                          "lc.get('edge.domain'), lc.get('edge.apps'), lc.USER]))",
                          str(ROOT / "lib")],
                         env={**os.environ, "LOAM_CONFIG": str(cfg_path)},
                         capture_output=True, text=True, check=False)
    got = json.loads(out.stdout) if out.returncode == 0 else out.stderr
    check("the install's loam.toml is read by loam_config, edge and all",
          got[:6] if isinstance(got, list) else got,
          ["Sam", "Europe/Berlin", f"{TMP}/home/loam", f"{TMP}/home/agents",
           f"sam.{li.BASE_DOMAIN}", ["loam-ui", "files", "vault"]])

    # -- names -----------------------------------------------------------------
    check("a good name", li.valid_name("juniper"), None)
    check("too short, a capital, a reserved one, a trailing hyphen are refused",
          [bool(li.valid_name(n)) for n in ("ab", "Sam", "auth", "sam-", "a--b")],
          [True, True, True, True, True])

    answers: dict[str, set[str] | None] = {}
    real = li.resolve
    li.resolve = lambda name, resolver="public": answers.get(name.split(".", 1)[0]
                                                             if not name.startswith("loam-nobody-")
                                                             else "nobody", set())
    try:
        here = "203.0.113.7"
        answers.update({"sam": {here}, "loam-check": {here}, "nobody": {"198.51.100.1"}})
        check("both names here: here", li.name_check("sam", here)["state"], "here")
        answers.update({"sam": {"192.0.2.9"}, "loam-check": {"192.0.2.9"}})
        check("pointing elsewhere: taken", li.name_check("sam", here)["state"], "taken")
        answers.update({"sam": {"198.51.100.1"}, "loam-check": {"198.51.100.1"}})
        check("answering only as the base's wildcard does: unset, not taken",
              li.name_check("sam", here)["state"], "unset")
        answers.update({"sam": set(), "loam-check": set(), "nobody": set()})
        check("nothing at all: unset", li.name_check("sam", here)["state"], "unset")
        answers.update({"sam": {here}, "loam-check": set()})
        check("the name here but not its wildcard: unset (both are needed)",
              li.name_check("sam", here)["state"], "unset")
        answers["sam"] = None
        check("no resolver answered: unknown", li.name_check("sam", here)["state"], "unknown")
    finally:
        li.resolve = real
    check("the two records to ask for",
          li.records_to_ask_for("sam", "203.0.113.7"),
          [f"sam.{li.BASE_DOMAIN}  A  203.0.113.7", f"*.sam.{li.BASE_DOMAIN}  A  203.0.113.7"])

    # -- an own domain (Loam 0.2.8) ---------------------------------------------
    check("own domains: a host, an apex, and refusals",
          [li.valid_domain(d) is None for d in
           ("loam.example.com", "example.com", "https://x.example.com", "*.example.com",
            "localhost", f"sam.{li.BASE_DOMAIN}", li.BASE_DOMAIN, "a.b.123", "bad_.example.com")],
          [True, True, False, False, False, False, False, False, False])
    text = li.render_config(name="", owner="Sam", timezone="UTC", address="203.0.113.7",
                            home="/home/loam", domain="Loam.Example.com.")
    import tomllib  # noqa: PLC0415
    edge = tomllib.loads(text)["edge"]
    check("an own domain's [edge]: the domain as given, its first label as name, own_domain",
          (edge["domain"], edge["name"], edge.get("own_domain")),
          ("loam.example.com", "loam", True))
    check("a name under the base has no own_domain key (that path is as it was)",
          "own_domain" in li.render_config(name="sam", owner="Sam", timezone="UTC",
                                           address="203.0.113.7", home="/home/loam"), False)
    check("an own domain's two records", li.edge_records(edge),
          ["loam.example.com  A  203.0.113.7", "*.loam.example.com  A  203.0.113.7"])
    check("a name's two records through the same helper",
          li.edge_records({"name": "sam", "domain": f"sam.{li.BASE_DOMAIN}", "address": "203.0.113.7"}),
          li.records_to_ask_for("sam", "203.0.113.7"))

    four: dict[str, set[str] | None] = {}
    six: dict[str, set[str] | None] = {}
    real4, real6 = li.resolve, li._resolve6
    li.resolve = lambda name, resolver="public": four.get(name, set())
    li._resolve6 = lambda name, resolver="public": six.get(name, set())
    dom, probe, here = "loam.example.com", "loam-check.loam.example.com", "203.0.113.7"
    try:
        def own(mine=frozenset()):
            return li.domain_check(dom, here, mine=set(mine))
        four.update({dom: {here}, probe: {here}})
        got = own()
        check("own domain, both records here: here, nothing to say",
              (got["state"], got["problems"]), ("here", []))
        four.update({probe: set()})
        got = own()
        check("own domain, the wildcard missing: waiting, and it says which",
              (got["state"], [p.split(" ", 1)[0] for p in got["problems"]]),
              ("waiting", ["*.loam.example.com"]))
        four.update({dom: {"192.0.2.9"}, probe: {"192.0.2.9"}})
        got = own()
        check("own domain pointing elsewhere: waiting (never 'taken': it is theirs), both named",
              (got["state"], len(got["problems"]), "change its A record to 203.0.113.7" in got["problems"][0]),
              ("waiting", 2, True))
        four.update({dom: {here}, probe: {here}})
        six.update({dom: {"2001:db8::1"}})
        got = own()
        check("an AAAA record that is not this box's holds it back, named",
              (got["state"], "IPv6" in " ".join(got["problems"])), ("waiting", True))
        check("an AAAA record that is this box's own is fine",
              own({"2001:db8::1"})["state"], "here")
        six.clear()
        four.update({dom: {"104.21.3.4"}, probe: {"172.67.1.2"}})
        check("Cloudflare's proxy is named as such",
              any("DNS only" in p for p in own()["problems"]), True)
        four[dom] = None
        check("no resolver answered: unknown", own()["state"], "unknown")
        four.update({dom: {here}, probe: {here}})
        check("edge_check takes the own-domain path from the table",
              li.edge_check({**edge, "address": here})["state"], "here")
    finally:
        li.resolve, li._resolve6 = real4, real6

    check("a private address is not public", (li.is_public("10.0.0.5"), li.is_public("203.0.113.7"),
                                               li.is_public("8.8.8.8")), (False, False, True))

    # -- reach.md --------------------------------------------------------------
    out = subprocess.run([sys.executable, "-c",
                          "import sys; sys.path.insert(0, sys.argv[1]); import loam_install; "
                          "print(loam_install.reach_text(), end='')", str(ROOT / "lib")],
                         env={**os.environ, "LOAM_CONFIG": str(cfg_path)},
                         capture_output=True, text=True, check=False)
    reach = out.stdout
    check("reach.md points at the library page and names this install's domain and places",
          ("loam-what-you-can-reach/SKILL.md" in reach, f"https://vault.sam.{li.BASE_DOMAIN}/" in reach,
           f"`{TMP}/home/agents/<name>/`" in reach, f"{TMP}/home/vault/Conversations/" in reach),
          (True, True, True, True))
    check("...and nothing of another box's", [w for w in ("ai-labs", "first-install-owner", "15_CONVERSATIONS",
                                                         "loam-ui." + li.BASE_DOMAIN.split(".")[0]) if w in reach], [])

    # -- the claim service's code reader ---------------------------------------
    claim = load_cmd("loam-claim")
    vol = TMP / "vol"
    vol.mkdir()
    claim.volume_dir = lambda: vol
    note = vol / "notification.txt"
    # Authelia 4.39.23's own text, as its file notifier wrote it in the
    # rehearsal (names and the revoke id changed).
    note.write_text(
        "Date: 2026-11-15 05:30:00.1 +0000 UTC m=+1.0\nRecipient: {Sam sam@x}\n"
        "Subject: Reset your password\nold message\n"
        "Date: 2026-11-15 05:39:20.709356224 +0000 UTC m=+30.866786938\n"
        "Recipient: {Sam sam@sam.loam-proof.test}\nSubject: Confirm your identity\n"
        "A ONE-TIME CODE HAS BEEN GENERATED TO COMPLETE A REQUESTED ACTION\nHi Sam,\n"
        "This notification has been sent to you in order to verify your identity to change "
        "security details for your account at sam.loam-proof.test.\n"
        "Do not share this notification or the content of this notification with anyone.\n"
        "The following one-time code should only be used in the prompt displayed in your browser.\n"
        "----------------------------------------\nK7QP2MXA\n----------------------------------------\n"
        "If you did not initiate the process your credentials may have been compromised and you should:\n"
        " 1. Revoke this code using the provided links below\n"
        "https://auth.sam.loam-proof.test/revoke/one-time-code?id=x\n"
        "Powered by Authelia https://www.authelia.com\n")
    got = claim.newest_code()
    check("the newest message's one-time code is read", (got.get("code"), got.get("subject")),
          ("K7QP2MXA", "Confirm your identity"))
    old = time.time() - 3600
    os.utime(note, (old, old))
    check("...and a stale one is not shown", claim.newest_code().get("code"), None)
    note.unlink()
    check("...and no file is no code", claim.newest_code(), {"code": None})

    # -- the claim page's CSP header --------------------------------------------
    # claim.html embeds its fonts as data: URIs; the header is stricter than the
    # page's own meta and wins, so it must allow them and nothing remote.
    class _Fake:
        def __init__(self):
            self.headers = {}
            self.wfile = type("W", (), {"write": lambda _s, _b: None})()
        def send_response(self, _status): pass
        def send_header(self, k, v): self.headers[k] = v
        def end_headers(self): pass
    fake = _Fake()
    claim.Handler.send(fake, 200, b"<html></html>", "text/html; charset=utf-8")
    csp = {d.split()[0]: d.split()[1:] for d in
           (x.strip() for x in fake.headers.get("Content-Security-Policy", "").split(";")) if d}
    check("the claim page's CSP allows data: fonts and no remote font source",
          csp.get("font-src"), ["data:"])
    check("...and keeps every other directive",
          {k: v for k, v in csp.items() if k != "font-src"},
          {"default-src": ["'none'"], "style-src": ["'unsafe-inline'"],
           "script-src": ["'unsafe-inline'"], "connect-src": ["'self'"],
           "form-action": ["'self'"], "frame-ancestors": ["'none'"], "base-uri": ["'none'"]})
    fake = _Fake()
    claim.Handler.send(fake, 200, b"{}")
    check("...and JSON answers carry no CSP", "Content-Security-Policy" in fake.headers, False)

    exercise_claim(claim, unclaimed)
    exercise_claim_edge(unclaimed, claimed)

    inst = load_cmd("loam-install")
    # Test the OS gate with a different, deterministic preflight failure next.
    with patch.object(inst.Path, "read_text", return_value="MemTotal: 1024 kB\n"):
        for distro, version in (("debian", "12"), ("debian", "13"),
                                ("ubuntu", "24.04"), ("ubuntu", "26.04")):
            with patch.object(inst, "os_release", return_value={"ID": distro, "VERSION_ID": version}):
                try:
                    inst.step_preflight(None, {})
                except inst.Stop as stop:
                    check(f"{distro} {version} passes the OS gate", stop.say.startswith("Loam needs a server"))
                else:
                    check(f"{distro} {version} still checks memory", False)
        with patch.object(inst, "os_release", return_value={
            "ID": "ubuntu", "VERSION_ID": "25.10", "PRETTY_NAME": "Ubuntu 25.10",
        }):
            try:
                inst.step_preflight(None, {})
            except inst.Stop as stop:
                check("unsupported OS refusal names the four releases and the detected OS",
                      stop.say, "Loam installs on Debian 12, Debian 13, Ubuntu 24.04, or Ubuntu 26.04. "
                      "This server is Ubuntu 25.10.")
            else:
                check("Ubuntu 25.10 is refused", False)

    # Select a preview browser available on each supported distribution.
    check("Debian takes the headless shell from its archive, Ubuntu does not ask for it",
          (inst.PREVIEW_BROWSER in inst.apt_packages("debian"),
           inst.PREVIEW_BROWSER in inst.apt_packages("ubuntu")), (True, False))
    with tempfile.TemporaryDirectory() as td:
        saved_pw = inst.PLAYWRIGHT_BROWSERS
        inst.PLAYWRIGHT_BROWSERS = Path(td)
        try:
            check("no Playwright shell yet: none found", inst.playwright_shell(), None)
            for build in ("1200", "1255", "999"):
                f = (Path(td) / f"chromium_headless_shell-{build}" / "chrome-headless-shell-linux64"
                     / "chrome-headless-shell")
                f.parent.mkdir(parents=True)
                f.write_text("")
            check("...the newest build is the one linked",
                  inst.playwright_shell().parts[-3], "chromium_headless_shell-1255")
            ran = []
            saved_run = inst.run
            inst.run = lambda *a, **k: ran.append(a)
            try:
                inst.ensure_preview_browser("debian")
            finally:
                inst.run = saved_run
            check("...and on Debian nothing is fetched or linked", ran, [])
        finally:
            inst.PLAYWRIGHT_BROWSERS = saved_pw
    check("the app is told to run the same command name on both",
          inst.PREVIEW_BROWSER, "chromium-headless-shell")

    # -- the boot unit for Loam's user manager ---------------------------------
    # After a reboot of the walk's VM, lingering did not start Loam's user
    # manager: the system came back, the agents' engine and timers did not.
    unit = inst.system_unit_text("loam-user-manager.service", {"root": str(ROOT), "user": "sam"})
    check("step 9 installs the boot unit for Loam's user manager",
          "loam-user-manager.service" in inst.SYSTEM_UNITS, True)
    check("...which starts the install user's manager, after logind, at boot",
          ('id -u sam' in unit, "@USER@" in unit, "After=systemd-logind.service" in unit,
           "WantedBy=multi-user.target" in unit), (True, False, True, True))

    # Status must recognize a healthy service app.
    import http.server  # noqa: PLC0415
    import threading  # noqa: PLC0415

    class Up(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200 if self.path == "/up" else 404)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Up)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    st = load_cmd("loam-status")
    bare = dict(exposure="private", host="", policy="one_factor", static_root=None,
                share_expires=None, compose=None, checkout=None)
    svc = dep.App(name="loam-ui", port=srv.server_port, service=True, **bare)
    ctr = dep.App(name="other", port=None, service=False, **bare)
    asked: list = []
    unit_state = {"v": "active"}

    def fake_run(argv, **kw):
        asked.append(argv[:2])
        out = unit_state["v"] if argv[0] == "systemctl" else "running healthy"
        return subprocess.CompletedProcess(argv, 0, out + "\n", "")

    saved_records, saved_run = st.dep.records, st.run
    st.dep.records, st.run = (lambda: [svc, ctr]), fake_run
    try:
        check("a service app, active and answering /up, is running healthy",
              st.app_health("loam-ui"), "running healthy")
        check("...asked of systemd, not Docker", ["docker", "inspect"] in asked, False)
        unit_state["v"] = "inactive"
        check("...a stopped unit is absent", st.app_health("loam-ui"), "absent")
        unit_state["v"] = "failed"
        check("...a failed one says so", st.app_health("loam-ui"), "failed")
        asked.clear()
        check("a container app is still asked of Docker",
              (st.app_health("other"), ["docker", "inspect"] in asked), ("running healthy", True))
    finally:
        st.dep.records, st.run = saved_records, saved_run
        srv.shutdown()

    # -- the command ----------------------------------------------------------
    res = subprocess.run([str(ROOT / "bin" / "loam"), "help"], capture_output=True, text=True, check=False)
    check("`loam help` lists status and hides the services' own commands",
          ("loam status" in res.stdout, "loam claim" in res.stdout, "loam await-dns" in res.stdout),
          (True, False, False))
    res = subprocess.run([str(ROOT / "bin" / "loam"), "claim"], capture_output=True, text=True, check=False)
    check("`loam claim` is not a verb", res.returncode, 2)

    # Installation time estimate
    for f in ("install.sh", "README.md", "docs/install.md"):
        check(f"{f} no longer promises fifteen minutes",
              "fifteen minutes" in (ROOT / f).read_text(encoding="utf-8"), False)
    check("install.sh says a few minutes",
          "This takes a few minutes" in (ROOT / "install.sh").read_text(encoding="utf-8"))
    check("the finale says what it took",
          [li.took(s) for s in (40, 70, 130, 900)],
          ["under a minute", "about a minute", "about 2 minutes", "about 15 minutes"])
    installer_source = (ROOT / "bin" / "loam-install").read_text(encoding="utf-8")
    check("...and only a run that did every step says it",
          "finale(time.time() - began if start == 1 else None)" in installer_source)

    # Exercise the installed command's closing instructions with scratch claim state.
    domain = EDGE["domain"]
    link = f"https://{domain}/claim?token=fixture"
    stages = (
        ("waiting for DNS", True, {}, "This is your link to make your sign-in"),
        ("ready to claim", True, {"ready_at": "now"}, "Open this link in your browser"),
        ("already claimed", False, {"ready_at": "now"}, f"Open https://{domain}/ and sign in."),
    )
    with (patch.object(inst, "LOG", TMP / "install-finale.log"),
          patch.object(inst, "config_values", return_value={"root": str(ROOT), "edge": EDGE}),
          patch.object(li, "claim_link", return_value=link)):
        for label, unclaimed, state, instruction in stages:
            output = StringIO()
            with (patch.object(dep, "edge_state", return_value=state),
                  patch.object(dep, "unclaimed", return_value=unclaimed),
                  redirect_stdout(output)):
                inst.finale()
            words = " ".join(output.getvalue().split())
            check(f"the installer finale ({label}) points to sign-in and status",
                  (instruction in words, link in words, "loam status" in words),
                  (True, unclaimed, True))
            check(f"the installer finale ({label}) has no backup recommendation",
                  not any(phrase in words.lower() for phrase in ("backup", "back up", "back them up")))

    print(f"\n{'OK' if not FAILURES else f'{FAILURES} FAILED'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
