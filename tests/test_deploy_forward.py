#!/usr/bin/env python3
"""An old name's paths kept past `[moved]` (`[[forward]]`, deploy/loam.toml).

What earns a case here:

  - while `[moved]` lists the host, its whole-host redirect is the one block
    and `[[forward]]` adds nothing (Caddy refuses two blocks for one host);
  - once `[moved]` is gone, the host renders one ungated block: the named
    paths 308 to the same path on the domain, everything else 404s, and
    Caddy itself reads it that way (`caddy adapt`, when Caddy is here);
  - a forward under the domain itself, a path that is not a path, or no
    `why` is refused.

Runs against a throwaway deploy config with the module pointed at it and
restored after. Nothing on the box is read or written.

Run: python3 tests/test_deploy_forward.py
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


BASE = 'domain = "new.example.test"\nauth_host = "auth"\n'
MOVED = ('[moved]\nfrom = "old.example.test"\nuntil = 2026-12-15\n'
         'hosts = ["auth", "app"]\n')
FORWARD = ('[[forward]]\nhost = "app.old.example.test"\n'
           'paths = ["/integrations/github/*"]\n'
           'why = "an app registered on GitHub calls back here."\n')

TMP = Path(tempfile.mkdtemp(prefix="loam-forward-test-"))
saved = {k: getattr(dep, k) for k in ("CONFIG_PATH", "EDGE")}
try:
    dep.EDGE = {}
    dep.CONFIG_PATH = TMP / "loam.toml"

    dep.CONFIG_PATH.write_text(BASE + MOVED + FORWARD)
    hosts = [f["host"] for f in dep.forwards()]
    check("a forward is read, its target the domain itself",
          (hosts, dep.forwards()[0]["to"]) == (["app.old.example.test"], "new.example.test"))
    old = [o for o, _ in dep.moved_targets([])]
    check("while [moved] lists it, the host is in the moved list",
          "app.old.example.test" in old)
    check("forward_hosts names it", dep.forward_hosts() == {"app.old.example.test"})

    dep.CONFIG_PATH.write_text(BASE + FORWARD)
    check("without [moved], no moved targets and the forward stands",
          (dep.moved_targets([]), len(dep.forwards())) == ([], 1))
    block = dep.caddy_forward_block(dep.forwards()[0])
    check("the block redirects the paths, 308, to the domain",
          "redir @forward https://new.example.test{uri} 308" in block
          and "@forward path /integrations/github/*" in block, block)
    check("the block answers 404 for anything else, and is ungated",
          "respond 404" in block and "import gate" not in block)
    check("the live-file parser reads it as one ungated host",
          dep.live_host_blocks(block) == [("app.old.example.test", False)])

    caddy = shutil.which("caddy")
    if caddy:
        path = TMP / "Caddyfile"
        path.write_text("(access_log) {\n}\n(compress) {\n}\n" + block)
        res = subprocess.run([caddy, "adapt", "--config", str(path), "--adapter", "caddyfile"],
                             capture_output=True, text=True, check=False)
        text = res.stdout if res.returncode == 0 else ""
        check("Caddy adapts the block", res.returncode == 0, res.stderr[-200:])
        doc = json.loads(text) if text else {}
        routes = json.dumps(doc)
        check("Caddy's routes: the path matcher, a 308 and a 404",
              all(s in routes for s in ('"/integrations/github/*"', '"status_code": 308',
                                         '"status_code": 404')), "")

    dep.CONFIG_PATH.write_text(BASE + FORWARD.replace("app.old.example.test", "app.new.example.test"))
    refuses("a host under the domain itself is refused", dep.forwards, "serves itself")
    dep.CONFIG_PATH.write_text(BASE + FORWARD.replace('"/integrations/github/*"', '"integrations"'))
    refuses("a path that is not a path is refused", dep.forwards, "paths is a list")
    dep.CONFIG_PATH.write_text(BASE + FORWARD.replace(
        'why = "an app registered on GitHub calls back here."\n', ""))
    refuses("a forward without its why is refused", dep.forwards, "why says")
    dep.CONFIG_PATH.write_text(BASE + FORWARD + FORWARD)
    refuses("the same host twice is refused", dep.forwards, "distinct")

    dep.EDGE = {"domain": "sam.example.test"}
    check("an install's edge has no forwards", dep.forwards() == [])
finally:
    for k, v in saved.items():
        setattr(dep, k, v)
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
