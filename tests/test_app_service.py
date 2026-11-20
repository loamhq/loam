#!/usr/bin/env python3
"""Exercise service-app release selection, health, rollback and cleanup against a
temporary installation.

Run: python3 tests/test_app_service.py"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

TMP = Path(tempfile.mkdtemp(prefix="loam-app-service-"))
HOME = TMP / "home"
CHECKOUT = HOME / "loam"
(HOME / "loam" / "deploy" / "loam-ui").mkdir(parents=True)
CONFIG = TMP / "loam.toml"
CONFIG.write_text(f"""
user = "{os.environ.get('USER', 'loam')}"
[owner]
id = "owner"
name = "Sam"
[paths]
home = "{HOME}"
root = "{CHECKOUT}"
""")
os.environ["LOAM_CONFIG"] = str(CONFIG)

import loam_autodeploy as ad  # noqa: E402
import loam_config as lc  # noqa: E402
import loam_deploy as dep  # noqa: E402

results: list[bool] = []


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label}"
          + ("" if ok else f"\n      got  {got!r}\n      want {want!r}"))


def load(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(CHECKOUT), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


def commit(files: dict[str, str], message: str) -> str:
    for rel, text in files.items():
        path = CHECKOUT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message)
    return git("rev-parse", "HEAD")


# -- the record ---------------------------------------------------------------------------

RECORD = """exposure = "private"
apex = true
content_host = "loam-ui-content"
app_shell = true
checkout = "@ROOT@"
context = "app"

[service]
port = 8081
"""

git_init = subprocess.run(["git", "init", "-q", "-b", "main", str(CHECKOUT)], check=True)
A = commit({"deploy/loam.toml": 'domain = "example.com"\nauth_host = "auth"\n',
            "app/src/server.js": "// a\n", "app/package.json": "{}\n",
            "app/public/index.html": "<p>a</p>\n", "bin/deploy": "#!/bin/sh\n",
            "deploy/loam-ui/deploy.toml": RECORD}, "A")
B = commit({"app/src/server.js": "// b\n"}, "B")
C = commit({"app/src/server.js": "// c, which will not answer\n"}, "C")
subprocess.run(["git", "-C", str(CHECKOUT), "rm", "-q", "app/src/server.js"], check=True)
git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "no server")
BROKEN = git("rev-parse", "HEAD")
git("reset", "-q", "--hard", C)

apps = {a.name: a for a in dep.records()}
app = apps["loam-ui"]
check("a [service] record is an app of its own: a unit, a loopback port, Loam's checkout, app/",
      (app.service, app.unit, app.port, app.compose, app.checkout, app.context, app.backend),
      (True, "loam-app@loam-ui.service", 8081, None, CHECKOUT, "app",
       "reverse_proxy 127.0.0.1:8081"))
check("...and the sudoers line lets the box's user deploy it, as any app",
      f"{dep.DEPLOY_BIN} loam-ui *" in dep.sudoers(), True)


def refused(record: str, compose: bool = False) -> str:
    where = CHECKOUT / "deploy" / "bad"
    where.mkdir(exist_ok=True)
    (where / "deploy.toml").write_text(record)
    if compose:
        (where / "compose.yaml").write_text("services: {}\n")
    try:
        dep.records()
        return "accepted"
    except dep.DeployError as exc:
        return str(exc)
    finally:
        for f in where.iterdir():
            f.unlink()
        where.rmdir()


check("[service] holds a port and nothing else",
      "nothing else" in refused('exposure = "private"\n[service]\nport = 8090\nuser = "root"\n'), True)
check("...a port, not a word or a privileged number",
      ("is not a port" in refused('exposure = "private"\n[service]\nport = "8090"\n'),
       "is not a port" in refused('exposure = "private"\n[service]\nport = 80\n')), (True, True))
check("...never beside a compose.yaml",
      "never two" in refused('exposure = "private"\n[service]\nport = 8090\n', compose=True), True)
check("...a context that climbs out of the checkout is refused",
      "not a relative folder" in refused('exposure = "private"\ncontext = "../etc"\n[service]\nport = 8090\n'),
      True)

# -- bin/deploy, with systemd and Docker in memory ------------------------------------------

deploy = load("deploy_service_mod", ROOT / "bin" / "deploy")
dep.SERVICE_RELEASES = TMP / "var-lib-loam-app"
REAL_RUN = deploy.run


class Box:
    def __init__(self, healthy=True, container=False):
        self.active = False
        self.healthy = healthy        # True, or a set of commits that answer
        self.container = container
        self.restarts: list[str] = []
        self.events: list[str] = []

    def install(self):
        d = deploy
        d.resolve_commit = lambda a, ref: git("rev-parse", ref)
        d.unit_state = lambda a: "active" if self.active else "inactive"
        d.container_running = lambda name: self.container
        d.container_commit = lambda a: "c" * 40
        d.health_of = lambda name: "healthy"
        d.wait_healthy = lambda name, seconds=90: "healthy"

        def start_unit(a):
            self.active = True
            self.restarts.append(d.link_target(d.service_dir(a) / "current"))
            return subprocess.CompletedProcess([], 0, "", "")
        d.start_unit = start_unit

        def health(a):
            if not self.active:
                return "inactive"
            cur = d.link_target(d.service_dir(a) / "current")
            ok = self.healthy is True or cur in self.healthy
            return "healthy" if ok else "starting"
        d.service_health = health
        d.wait_service_healthy = lambda a, seconds=60: health(a)

        def stop_container(name):
            self.container = False
            self.events.append("container stopped")
        d.stop_container = stop_container

        def start_container(name):
            self.active = False
            self.container = True
            self.events.append("container started")
        d.start_container = start_container
        # Files are real (tar, chmod); systemctl, journalctl and docker are not.
        d.run = lambda argv, **kw: (REAL_RUN(argv, **kw) if argv[0] in ("tar", "chmod")
                                    else subprocess.CompletedProcess(argv, 0, "", ""))
        return self


def up(box: Box, ref: str, forward_only=False):
    box.install()
    out = io.StringIO()
    code = 0
    with redirect_stdout(out):
        try:
            deploy.up(app, ref, forward_only=forward_only)
        except SystemExit as exc:
            code = exc.code
    return code, ad.parse_result(out.getvalue()), out.getvalue()


def links():
    base = dep.SERVICE_RELEASES / "loam-ui"
    return (deploy.link_target(base / "current"), deploy.link_target(base / "previous"),
            sorted(p.name for p in (base / "releases").iterdir()))


box = Box(container=True)
code, r, out = up(box, A)
rel = dep.SERVICE_RELEASES / "loam-ui" / "releases" / A
check("the first up replaces the container: stopped and kept, the unit on A",
      (code, r["action"], r["before"], r["after"], box.events, links()),
      (0, "deployed", "c" * 40, A, ["container stopped"], (A, None, [A])))
check("...the release is the app's folder at A and nothing else of the repository",
      sorted(str(p.relative_to(rel)) for p in rel.rglob("*")),
      ["package.json", "public", "public/index.html", "src", "src/server.js"])
check("...readable by the unit's own uid, written by nobody but root",
      (oct(rel.stat().st_mode & 0o777), oct((rel / "src" / "server.js").stat().st_mode & 0o777)),
      ("0o755", "0o644"))

code, r, _ = up(box, B)
check("a later commit: current B, previous A, both kept",
      (code, r["action"], r["before"], r["after"], links()),
      (0, "deployed", A, B, (B, A, sorted([A, B]))))

code, r, _ = up(box, A, forward_only=True)
check("forward-only: a commit B already carries changes nothing",
      (code, r["action"], box.restarts[-1], links()[0]), (0, "carried", B, B))

code, r, _ = up(box, A)
check("without it, the earlier commit is a rollback, one command: current A, previous B",
      (code, r["action"], r["after"], links()[:2]), (0, "deployed", A, (A, B)))

box.healthy = {A, B}
code, r, out = up(box, C)
check("an unhealthy release goes back to the one before, and nothing is pruned",
      (code, r["action"], r["before"], r["after"], links()[:2], C in links()[2]),
      (2, "rolled_back", A, A, (A, B), True))
check("...and the unit's last lines are printed", "the unit's last lines" in out, True)

code, r, _ = up(box, BROKEN)
check("a commit whose app has no server is refused before anything moves",
      (code, r["action"], r["after"], links()[0]), (1, "build_failed", A, A))

box.healthy = True
code, r, _ = up(box, B)
check("prune keeps current and previous, and the rest goes",
      (code, links()), (0, (B, A, sorted([A, B]))))

fresh = Box(healthy=set(), container=True)
dep.SERVICE_RELEASES = TMP / "second"
code, r, _ = up(fresh, A)
check("a unit that never answers puts the container back, started again",
      (code, r["action"], fresh.events, fresh.active), (2, "rolled_back",
                                                         ["container stopped", "container started"], False))

# -- the environment ---------------------------------------------------------------------------

deploy.origins_env = lambda: {
    "LOAM_DOMAIN": "example.com", "LOAM_VAULT_ORIGIN": "https://vault.example.com",
    "LOAM_FILES_ORIGIN": "https://files.example.com",
    "LOAM_PREVIEW_BASE": "http://preview.internal:8935", "LOAM_PREVIEW_ADDR": "172.31.0.1"}
deploy.app_env = lambda: {"LOAM_AVATAR_COLOURS": '{"sam":"c54"}'}
deploy.broker_env = lambda: {"BROKER_URL": "http://172.31.0.1:8932", "BROKER_TOKEN": "t0k"}
deploy.app_secrets = lambda a: {"GEMINI_API_KEY": "k3y"}
env = deploy.service_env(app)
compose_names = {"APP_PORT", "BROKER_URL", "BROKER_TOKEN", "GEMINI_API_KEY",
                 "AVATAR_COLOURS", "FILES_VAULT_URLS", "FILES_WEB_URLS",
                 "FILES_CONTENT_ORIGIN", "APPS_DOMAIN", "APPS_PREVIEW_BASE",
                 "APPS_PREVIEW_ADDR", "APP_PREVIEW_CHROME", "NODE_ENV"}
check("every name the compose file handed the container is handed to the unit",
      sorted(compose_names - set(env)), [])
check("...with the same values it had",
      (env["APP_PORT"], env["FILES_VAULT_URLS"], env["FILES_CONTENT_ORIGIN"],
       env["FILES_WEB_URLS"], env["APP_PREVIEW_CHROME"]),
      ("8081", "https://vault.example.com/", "https://loam-ui-content.example.com",
       '[{"root":"spaces","path":"commons/www","url":"https://files.example.com"}]',
       "chromium-headless-shell"))
check("...and the install's own vault folders and zone (a fresh install said 00_INBOX)",
      (env["VAULT_INBOX_FOLDER"], env["VAULT_BOARDS_FOLDER"], env["VAULT_GENERAL_BOARD"],
       env["VAULT_ATTACHMENTS_FOLDER"], env["APP_TIME_ZONE"]),
      (lc.vault_folder("inbox"), lc.vault_folder("boards"), lc.vault_folder("general_board"),
       lc.vault_folder("attachments"), lc.TIMEZONE))
check("...and what the host adds: loopback, its state directory, the browser's OOM score",
      (env["APP_HOST"], env["APP_MEDIA_DIR"], env["APP_PREVIEW_OOM_SCORE_ADJ"]),
      ("127.0.0.1", "/var/lib/loam-app/loam-ui/app-media", str(lc.AGENT_OOM_SCORE_ADJ)))
check("...and nothing of Read aloud's: its server carries its own (0.2.12)",
      sorted(n for n in env if n.startswith("SPEECH_")), [])
text = deploy.env_file_text({"A": 'sam=Aoede:warm, "quoted"', "B": '{"x":"y"}', "C": "a\\b\nc"})
check("an EnvironmentFile line keeps quotes, spaces, JSON and backslashes whole",
      text.splitlines()[1:], ['A="sam=Aoede:warm, \\"quoted\\""', 'B="{\\"x\\":\\"y\\"}"',
                              'C="a\\\\b\\nc"'])

# -- the unit ----------------------------------------------------------------------------------

dep.SERVICE_RELEASES = Path("/var/lib/loam-releases")
unit = dep.service_unit()
need = ["DynamicUser=yes", "CapabilityBoundingSet=\n", "NoNewPrivileges=yes", "ProtectHome=yes",
        "ProtectSystem=strict", f"OOMScoreAdjust={lc.CORE_OOM_SCORE_ADJ}",
        "EnvironmentFile=-/run/loam/app/%i.env", "WorkingDirectory=/var/lib/loam-releases/%i/current",
        f"ExecStartPre=+/usr/bin/env PYTHONPYCACHEPREFIX=/var/cache/loam/pycache /usr/bin/python3 {CHECKOUT}/bin/deploy %i env",
        "SyslogIdentifier=%i"]
check("the unit template carries the wall, the core's OOM score and its env from /run",
      [n for n in need if n not in unit], [])
check("...and no memory ceiling", any(k in unit for k in ("MemoryMax=", "MemoryHigh=")), False)
check("...and gen-deploy renders it with the rest", dep.generated()[dep.SERVICE_UNIT] == unit, True)

print(f"\n{'OK' if all(results) else 'FAILED'}: {results.count(False)} failure(s)")
sys.exit(0 if all(results) else 1)
