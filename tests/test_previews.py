#!/usr/bin/env python3
"""The Apps section's previews (2026-11-15): lib/loam_deploy.caddy_preview_block.

An internal route past the login, so loam-ui's preview renderer (headless
Chromium in its own container, no credential) sees each app as it renders.
What earns a case here is what "internal only, and nothing else changes"
rests on:

  - the route is one plain-HTTP site bound to the broker bridge's gateway
    and nothing else, refusing any client outside the broker's subnet
    before any app is served, and answering 404 for a name it does not know;
  - it lists exactly what the Apps list shows with a host of its own: every
    agent's site and every container record that is not Loam's page; never
    the vault, the commons' files, the login or the page itself;
  - each app is served exactly as its public block serves it past the gate
    (the same root and hidden names, or the same loopback upstream), and an
    expired site is a 410 here too;
  - the public blocks are byte-identical with and without it, and check-drift's
    live-file reader sees no new host block;
  - the kernel wall holds the port in the same chain as the broker's, and
    bin/deploy hands the page the two values of the contract.

Runs against throwaway directories with the module's paths pointed at them.
Nothing on the box is read or written.

Run: python3 tests/test_previews.py
"""

from __future__ import annotations

import datetime as dt
import importlib.machinery
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_broker as brk  # noqa: E402
import loam_deploy as dep  # noqa: E402

results: list[bool] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:62}{'  | ' + detail if detail else ''}")


COMPOSE = """\
name: {name}
services:
  web:
    image: {name}:${{LOAM_COMMIT:?set by bin/deploy}}
    ports: ["127.0.0.1:{port}:8000"]
    logging: {{driver: journald, options: {{tag: "{{{{.Name}}}}"}}}}
"""

root = Path(tempfile.mkdtemp(prefix="loam-previews-test-"))
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

    def record(name: str, toml: str, compose: str | None = None) -> None:
        (deploy / name).mkdir()
        (deploy / name / "deploy.toml").write_text(toml)
        if compose:
            (deploy / name / "compose.yaml").write_text(compose)

    def site(name: str, body: str) -> None:
        (dep.SITES_DIR / f"{name}.toml").write_text(body)

    record("files", f'exposure = "private"\nstatic_root = "{www}"\n')
    record("vault", f'exposure = "private"\nstatic_root = "{www}"\nhide = [".git"]\n')
    record("page", 'exposure = "private"\napp_shell = true\napex = true\ncheckout = "/srv/page"\n',
           COMPOSE.format(name="page", port=8101))
    record("shop", 'exposure = "private"\ncheckout = "/srv/shop"\n',
           COMPOSE.format(name="shop", port=8102))
    site("demo", f'owner = "ada"\nroot = "{www}"\n')
    site("api", 'owner = "ada"\nport = 9001\n')
    site("tmp", f'owner = "ada"\nroot = "{www}"\nexpires = 2026-11-20\n')
    site("open", 'owner = "ada"\nport = 9002\npublic = "Sam, 2026-11-08: yes"\n')

    today = dt.date(2026, 11, 15)
    text = dep.caddyfile(today)
    block = text[text.index(f"http://:{dep.PREVIEW_PORT} {{"):]
    block = block[:block.index("\n}\n") + 3]

    # -- where it listens -------------------------------------------------
    check("plain HTTP on the previews' port", block.startswith(f"http://:{dep.PREVIEW_PORT} {{\n"))
    check("bound to the broker bridge's gateway and nothing else",
          f"\tbind {brk.BIND_ADDR}\n" in block and block.count("\tbind ") == 1
          and dep.PREVIEW_ADDR == brk.BIND_ADDR)
    check("the port is no runtime service's",
          dep.PREVIEW_PORT not in (brk.PORT, dep.BROWSER_PORT, dep.AUTHELIA_PORT,
                                   dep.CLAIM_PORT, dep.PLUGIN_GATEWAY_PORT))
    first_handle = block.index("\thandle ")
    check("a client outside the broker's subnet is refused first",
          block.index(f"@outside not remote_ip {brk.SUBNET}") < first_handle
          and block[first_handle:].startswith('\thandle @outside {\n\t\trespond "forbidden" 403'))
    check("an unknown name is a 404", block.rstrip().endswith(
        '\thandle {\n\t\trespond "no such app" 404\n\t}\n}'))
    check("it writes its own access log", f"import access_log {dep.PREVIEW_LOG}\n" in block)
    check("no gate, no share, no frame header",
          "import gate" not in block and "share" not in block
          and "Content-Security-Policy" not in block)

    # -- what it lists ----------------------------------------------------
    hosts = [a.host for a in dep.previewable()]
    check("the Apps list's sites and projects, sorted", hosts == ["api", "demo", "open", "shop", "tmp"],
          ", ".join(hosts))
    for absent in ("files", "vault", "page", "auth"):
        check(f"not {absent}", f"{absent}.{dep.PREVIEW_SUFFIX}" not in block)

    # -- what each serves -------------------------------------------------
    def handle(host: str) -> str:
        start = block.index(f"\thandle @app_{host} {{\n")
        return block[start:block.index("\n\t}\n", start)]

    check("a static site: its root, with .git and .env hidden",
          f"\troot * {www}\n" in handle("demo") and "hide .git .env" in handle("demo"))
    check("a port site: its loopback upstream", "reverse_proxy 127.0.0.1:9001" in handle("api"))
    check("a project: its container's loopback publish", "reverse_proxy 127.0.0.1:8102" in handle("shop"))
    check("a public site is listed like any other", "reverse_proxy 127.0.0.1:9002" in handle("open"))
    deadline = dep._deadline_unix(dt.date(2026, 11, 20))  # noqa: SLF001
    check("an expired site is a 410 before its files",
          f">= {deadline}`" in handle("tmp")
          and handle("tmp").index("410") < handle("tmp").index("file_server"))
    check("matched by host under the internal suffix",
          f"\t@app_demo host demo.{dep.PREVIEW_SUFFIX}\n" in block)
    check("preview_url is the contract's address",
          dep.preview_url(dep.previewable()[1]) == f"http://demo.{dep.PREVIEW_SUFFIX}:{dep.PREVIEW_PORT}/")

    # -- nothing else changes ----------------------------------------------
    without = text.replace("\n" + block, "")
    check("the block is only an addition", without != text and block not in without)
    hosts_seen = [h for h, _ in dep.live_host_blocks(text)]
    check("check-drift's reader sees no new host block",
          hosts_seen == [h for h, _ in dep.live_host_blocks(without)]
          and not any(dep.PREVIEW_SUFFIX in h for h in hosts_seen))
    check("every public site stays gated but the one with a yes",
          {h: g for h, g in dep.live_host_blocks(text) if h.endswith(".example.test")
           and h.split(".")[0] in ("demo", "api", "tmp", "shop", "open")}
          == {"shop.example.test": True, "demo.example.test": True, "api.example.test": True,
              "tmp.example.test": True, "open.example.test": False})
    check("the render does not depend on the day", dep.caddyfile(dt.date(2026, 12, 1)) == text)
    for p in dep.SITES_DIR.iterdir():
        p.unlink()
    for d in ("shop",):
        shutil.rmtree(deploy / d)
    text = dep.caddyfile(today)
    check("with nothing previewable the listener still renders",
          f"http://:{dep.PREVIEW_PORT} {{\n\tbind {brk.BIND_ADDR}" in text
          and "@app_" not in text)

    # -- the kernel's half, and the sysctl ---------------------------------
    fw = (AGENTS / "broker" / "firewall.sh").read_text(encoding="utf-8")
    check("the wall script holds the previews' port", f"PREVIEW_PORT={dep.PREVIEW_PORT}\n" in fw
          and 'for P in "$PORT" "$PREVIEW_PORT"; do' in fw
          and f"ADDR={dep.PREVIEW_ADDR}\n" in fw and f"SUBNET={brk.SUBNET}\n" in fw)
    check("the sysctl render sets the early bind",
          dep.preview_sysctl().rstrip().endswith(f"{dep.PREVIEW_SYSCTL_KEY} = 1")
          and dep.PREVIEW_SYSCTL in dep.generated(today))
finally:
    for k, v in saved.items():
        setattr(dep, k, v)
    shutil.rmtree(root, ignore_errors=True)

# -- the page's contract, from bin/deploy ----------------------------------
loader = importlib.machinery.SourceFileLoader("deploy_bin", str(AGENTS / "bin" / "deploy"))
spec = importlib.util.spec_from_loader(loader.name, loader)
cmd = importlib.util.module_from_spec(spec)
loader.exec_module(cmd)
env = cmd.origins_env()
check("bin/deploy hands the base and the address",
      env.get("LOAM_PREVIEW_BASE") == f"http://{dep.PREVIEW_SUFFIX}:{dep.PREVIEW_PORT}"
      and env.get("LOAM_PREVIEW_ADDR") == brk.BIND_ADDR, str({k: v for k, v in env.items()
                                                              if "PREVIEW" in k}))
# loam-ui is tracked; loam-ui-next is one install's own app, kept out of the
# repository by `.git/info/exclude` (first release L16), so a fresh clone --
# a job's tree, a release checkout -- has no copy, and its check runs only
# where the install has one.
for app in ("loam-ui", "loam-ui-next"):
    path = AGENTS / "deploy" / app / "compose.yaml"
    if app != "loam-ui" and not path.is_file():
        continue
    compose = path.read_text(encoding="utf-8")
    check(f"{app}'s compose passes both to the page",
          'APPS_PREVIEW_BASE: "${LOAM_PREVIEW_BASE:?set by bin/deploy}"' in compose
          and 'APPS_PREVIEW_ADDR: "${LOAM_PREVIEW_ADDR:?set by bin/deploy}"' in compose)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
