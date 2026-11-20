#!/usr/bin/env python3
"""Verify private-by-default site rendering, explicit publication, expiry and
name-collision handling.

Run: python3 tests/test_sites.py
"""

from __future__ import annotations

import datetime as dt
import shutil
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
    check(label, False, "rendered instead of refusing")


root = Path(tempfile.mkdtemp(prefix="loam-sites-test-"))
names = ("DEPLOY", "CONFIG_PATH", "PUBLIC_FILE", "SHARE_DIR", "AGENTS", "SITES_DIR")
saved = {k: getattr(dep, k) for k in names}
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
    (deploy / "files").mkdir()
    (deploy / "files" / "deploy.toml").write_text(
        f'exposure = "private"\nstatic_root = "{www}"\n')

    def site(name: str, body: str) -> None:
        (dep.SITES_DIR / f"{name}.toml").write_text(body)

    def clear() -> None:
        for p in dep.SITES_DIR.iterdir():
            p.unlink()

    today = dt.date(2026, 11, 8)

    # -- private by default ------------------------------------------------
    site("demo", f'owner = "ada"\nroot = "{www}"\n')
    text = dep.caddyfile(today)
    blocks = dict(dep.live_host_blocks(text))
    check("a site with nothing said about access is gated", blocks.get("demo.example.test") is True,
          str(blocks.get("demo.example.test")))
    body = text.split("demo.example.test {", 1)[1].split("\n}\n", 1)[0]
    check("the browser's subnet passes ahead of the gate",
          body.index(dep.SIGHT_SUBNET) < body.index("import gate"))
    check("a static site hides .git and .env", "hide .git .env" in body)
    check("no PUBLIC comment on a private site", "# PUBLIC" not in text)

    # -- public only on a quoted yes ---------------------------------------
    site("demo", f'owner = "ada"\nroot = "{www}"\npublic = "Sam, 2026-11-08: yes, open it"\n')
    text = dep.caddyfile(today)
    blocks = dict(dep.live_host_blocks(text))
    check("a site whose record quotes the owner's yes is ungated",
          blocks.get("demo.example.test") is False)
    check("the yes is named in the block's comment",
          f"# PUBLIC: {dep.lc.OWNER_NAME}'s yes in the site record: Sam, 2026-11-08: yes, open it. No gate." in text)
    site("demo", f'owner = "ada"\nroot = "{www}"\npublic = "  "\n')
    refuses("an empty public is refused, not read as a yes", dep.sites, "not a yes")
    site("demo", f'owner = "ada"\nroot = "{www}"\npublic = "x\\n}}\\nevil.test {{"\n')
    text = dep.caddyfile(today)
    check("a quoted yes cannot break out of its comment line",
          "\nevil.test {" not in text and "evil.test" not in dict(dep.live_host_blocks(text)))
    clear()

    # -- names and ports that are not a site's ------------------------------
    site("files", f'owner = "ada"\nroot = "{www}"\n')
    refuses("a site cannot take a deployment record's name", dep.sites, "already")
    clear()
    site("auth", f'owner = "ada"\nroot = "{www}"\n')
    refuses("a site cannot take the login portal", dep.sites, "login portal")
    clear()
    site("second-factor", f'owner = "ada"\nroot = "{www}"\n')
    refuses("a site cannot take the two_factor name", dep.sites, "two_factor")
    clear()
    site("api", f'owner = "ada"\nport = {dep.AUTHELIA_PORT}\n')
    refuses("a site cannot proxy to the login's port", dep.sites, "authelia")
    clear()
    site("api", f'owner = "ada"\nport = {dep.BROWSER_PORT}\n')
    refuses("a site cannot proxy to the browser's port", dep.sites, "browser")
    clear()
    site("api", 'owner = "ada"\nport = 80\n')
    refuses("a privileged port is refused", dep.sites, "1024-65535")
    clear()
    site("both", f'owner = "ada"\nroot = "{www}"\nport = 9001\n')
    refuses("root and port together are refused", dep.sites, "exactly one")
    clear()
    site("odd", 'owner = "ada"\nroot = "/tmp/a b"\n')
    refuses("a root that would need quoting is refused", dep.sites, "must be an absolute path")
    clear()
    site("odd", 'owner = "ada"\nroot = "/tmp/../etc"\n')
    refuses("a root with .. is refused", dep.sites, "must be an absolute path")
    clear()
    site("Bad_Name", f'owner = "ada"\nroot = "{www}"\n')
    refuses("a name that is not a label is refused", dep.sites, "not a valid site name")
    clear()
    site("a", 'owner = "ada"\nport = 9001\n')
    site("b", 'owner = "ada"\nport = 9001\n')
    refuses("two sites on one port are refused", dep.sites, "is a's")
    clear()

    # -- a backend --------------------------------------------------------
    site("api", 'owner = "ada"\nport = 9001\n')
    text = dep.caddyfile(today)
    check("a port site proxies to loopback behind the gate",
          "reverse_proxy 127.0.0.1:9001" in text and dict(dep.live_host_blocks(text)).get("api.example.test") is True)
    clear()

    # -- expiry -----------------------------------------------------------
    site("tmp", f'owner = "ada"\nroot = "{www}"\nexpires = 2026-11-09\n')
    text = dep.caddyfile(today)
    body = text.split("tmp.example.test {", 1)[1].split("\n}\n", 1)[0]
    deadline = dep._deadline_unix(dt.date(2026, 11, 9))
    check("an expiry renders a 410 at the end of its date",
          f">= {deadline}`" in body and 'respond "expired" 410' in body)
    check("the 410 comes before the browser's pass and the gate",
          body.index("@expired") < body.index("@sight") < body.index("import gate"))
    check("the render does not depend on the day (check-drift stays green)",
          dep.caddyfile(dt.date(2026, 11, 20)) == text)
    clear()

    check("no sites directory means no sites",
          (shutil.rmtree(dep.SITES_DIR) or True) and dep.sites() == [])
finally:
    for k, v in saved.items():
        setattr(dep, k, v)
    shutil.rmtree(root, ignore_errors=True)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
