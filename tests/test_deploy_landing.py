#!/usr/bin/env python3
"""A public static folder on a name outside the domain (`[[landing]]`,
deploy/loam.toml, 2026-11-16).

What earns a case here:

  - an entry renders one ungated block that serves its folder in place, with
    no directory listing and `.git`/`.env` hidden, and the owner's quoted yes
    in its comment; Caddy itself adapts it (`caddy adapt`, when Caddy is here);
  - no entry, or an install's edge, renders nothing;
  - an entry without its yes, a host under the domain, a host `[moved]` or
    `[[forward]]` already answers, a relative root, or a stray key is refused.

Runs against a throwaway deploy config with the module pointed at it and
restored after. Nothing on the box is read or written.

Run: python3 tests/test_deploy_landing.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_deploy as dep  # noqa: E402

results: list[bool] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:62}{'  | ' + detail if detail else ''}")


def refuses(label: str, fn, needle: str) -> None:
    try:
        fn()
    except dep.DeployError as exc:
        check(label, needle in str(exc), str(exc)[:90])
        return
    check(label, False, "accepted instead of refusing")


BASE = 'domain = "sam.example.test"\nauth_host = "auth"\n'
MOVED = ('[moved]\nfrom = "example.test"\nuntil = 2026-12-15\n'
         'hosts = ["auth", "app"]\n')
FORWARD = ('[[forward]]\nhost = "app.example.test"\n'
           'paths = ["/integrations/github/*"]\n'
           'why = "an app registered on GitHub calls back here."\n')
LANDING = ('[[landing]]\nhost = "example.test"\n'
           'root = "/srv/landing/"\n'
           'public = "Sam, 2026-11-16, in chat:\\n \\"put it up\\""\n')

TMP = Path(tempfile.mkdtemp(prefix="loam-landing-test-"))
saved = {k: getattr(dep, k) for k in ("CONFIG_PATH", "EDGE")}
try:
    dep.EDGE = {}
    dep.CONFIG_PATH = TMP / "loam.toml"

    dep.CONFIG_PATH.write_text(BASE + MOVED)
    check("no [[landing]], nothing", dep.landings() == [])

    dep.CONFIG_PATH.write_text(BASE + MOVED + FORWARD + LANDING)
    got = dep.landings()
    check("an entry is read beside [moved] and [[forward]], root unslashed",
          [(e["host"], e["root"]) for e in got] == [("example.test", "/srv/landing")], str(got))
    check("the quoted yes is one line", "\n" not in got[0]["public"] and "put it up" in got[0]["public"])
    block = dep.caddy_landing_block(got[0])
    check("the block serves the folder in place",
          "\troot * /srv/landing\n" in block and "file_server {" in block, block)
    check("no listing, and .git and .env hidden",
          "browse" not in block and "hide .git .env" in block)
    check("ungated, the yes in its PUBLIC comment",
          "import gate" not in block and block.startswith("# PUBLIC: ") and "put it up" in block)
    check("the live-file parser reads it as one ungated host",
          dep.live_host_blocks(block) == [("example.test", False)])
    check("it writes its own access log", dep.live_hosts_unlogged(
        "(access_log) {\n}\n" + block) == [])

    caddy = shutil.which("caddy")
    if caddy:
        path = TMP / "Caddyfile"
        path.write_text("(access_log) {\n}\n(compress) {\n}\n" + block)
        res = subprocess.run([caddy, "adapt", "--config", str(path), "--adapter", "caddyfile"],
                             capture_output=True, text=True, check=False)
        check("Caddy adapts the block", res.returncode == 0, res.stderr[-200:])
        routes = json.dumps(json.loads(res.stdout)) if res.returncode == 0 else ""
        check("Caddy's routes: a file server on the folder, hiding .git",
              '"file_server"' in routes and '"/srv/landing"' in routes and '".git"' in routes
              and '"browse"' not in routes)

    dep.CONFIG_PATH.write_text(BASE + LANDING.replace(
        'public = "Sam, 2026-11-16, in chat:\\n \\"put it up\\""\n', ""))
    refuses("an entry without the owner's yes is refused", dep.landings, "yes quoted")
    dep.CONFIG_PATH.write_text(BASE + LANDING.replace('public = "Sam', 'public = "  "\n#'))
    refuses("an empty yes is refused", dep.landings, "yes quoted")
    dep.CONFIG_PATH.write_text(BASE + LANDING.replace('host = "example.test"',
                                                      'host = "www.sam.example.test"'))
    refuses("a host under the domain is refused", dep.landings, "under sam.example.test")
    dep.CONFIG_PATH.write_text(BASE + MOVED + LANDING.replace('host = "example.test"',
                                                              'host = "auth.example.test"'))
    refuses("a host [moved] answers is refused", dep.landings, "[moved]")
    dep.CONFIG_PATH.write_text(BASE + FORWARD + LANDING.replace('host = "example.test"',
                                                                'host = "app.example.test"'))
    refuses("a host [[forward]] answers is refused", dep.landings, "[[forward]]")
    dep.CONFIG_PATH.write_text(BASE + LANDING + LANDING)
    refuses("the same host twice is refused", dep.landings, "[[landing]]'s")
    dep.CONFIG_PATH.write_text(BASE + LANDING.replace('"/srv/landing/"', '"srv/landing"'))
    refuses("a relative root is refused", dep.landings, "absolute")
    dep.CONFIG_PATH.write_text(BASE + LANDING.replace('"/srv/landing/"', '"/srv/../etc"'))
    refuses("a root with .. is refused", dep.landings, "absolute")
    dep.CONFIG_PATH.write_text(BASE + LANDING + 'browse = true\n')
    refuses("a stray key is refused", dep.landings, "only host, root and public")

    dep.EDGE = {"domain": "sam.example.test"}
    check("an install's edge has no landings", dep.landings() == [])
finally:
    for k, v in saved.items():
        setattr(dep, k, v)
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
