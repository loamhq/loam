#!/usr/bin/env python3
"""Tests for plugin Python environment inspection, automatic rebuild, and
problem visibility in `loam status` and `loam-check`.

Runs 100% offline using a locally built pure-Python wheel so landing's network
isolation never affects it.
"""

from __future__ import annotations

import hashlib
import http.server
import importlib.machinery
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="loam-plugin-env-test-"))
YOURS = TMP / "plugins"
STATE = TMP / "state"
WHEELHOUSE = TMP / "wheels"
UV_CACHE = TMP / "uv-cache"

os.environ["LOAM_HOST_SETTINGS"] = str(TMP / "broker" / "settings.json")
os.environ["LOAM_PLUGIN_SHIPPED"] = ""
os.environ["LOAM_PLUGINS_DIR"] = str(YOURS)
os.environ["LOAM_PLUGIN_STATE"] = str(STATE)
os.environ["LOAM_UI_CORE_OPTIONS"] = "none"
os.environ["LOAM_PLUGIN_UNITS"] = "1"
os.environ["LOAM_ATTENTION_DIR"] = str(TMP / "attention")
os.environ["LOAM_WAITERS_DIR"] = str(TMP / "waiters")
os.environ["LOAM_CHECK_STATE"] = str(TMP / "check.json")
os.environ["LOAM_RESULTS_KICK"] = "0"
os.environ["UV_CACHE_DIR"] = str(UV_CACHE)
os.environ["UV_NO_INDEX"] = "1"
os.environ["UV_OFFLINE"] = "1"
os.environ["UV_FIND_LINKS"] = str(WHEELHOUSE)

sys.path.insert(0, str(ROOT / "lib"))
import loam_antigravity  # noqa: E402
import loam_attention as la  # noqa: E402
import loam_broker  # noqa: E402
import loam_check as ck  # noqa: E402
import loam_codex  # noqa: E402
import loam_config as lc  # noqa: E402
import loam_plugin_env as E  # noqa: E402
import loam_plugin_servers as S  # noqa: E402
import loam_plugins as P  # noqa: E402
import loam_pressure  # noqa: E402

results: list[bool] = []


def check(label: str, ok, detail="") -> None:
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:72}"
          f"{'  | ' + str(detail)[:260] if detail != '' and not ok else ''}")


def build_local_wheel(dest_dir: Path, name: str = "sampledep", version: str = "1.0.0") -> Path:
    """Create a valid pure-Python wheel offline with stdlib zipfile only."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    whl = dest_dir / f"{name}-{version}-py3-none-any.whl"
    dist_info = f"{name}-{version}.dist-info"
    files = {
        f"{name}/__init__.py": f'ANSWER = "sample-lives"\n',
        f"{dist_info}/METADATA": (
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
        ),
        f"{dist_info}/WHEEL": (
            "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\n"
            "Tag: py3-none-any\n"
        ),
        f"{dist_info}/RECORD": "",
    }
    with zipfile.ZipFile(whl, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel, content in files.items():
            zf.writestr(rel, content)
    return whl


SERVER_PY = """\
import http.server
import os
import sys
import sampledep


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = sampledep.ANSWER.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    if "--check-import" in sys.argv:
        print(sampledep.ANSWER)
        sys.exit(0)
    port = int(os.environ["PORT"])
    srv = http.server.HTTPServer(("127.0.0.1", port), Handler)
    srv.serve_forever()
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_plugin(pid: str, name: str, run_cmd: str, *, always_on: bool = False,
                extra_files: dict[str, str] | None = None) -> Path:
    d = YOURS / pid
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    ao = "  always_on: true\n" if always_on else ""
    (d / "PLUGIN.md").write_text(
        f"---\nid: {pid}\nname: {name}\nversion: 1.0.0\napi: 0\n"
        f"kind: [app]\nserver:\n  run: {run_cmd}\n{ao}---\n\n# {name}\n",
        encoding="utf-8",
    )
    (d / "server").mkdir(exist_ok=True)
    (d / "server" / "main.py").write_text(SERVER_PY, encoding="utf-8")
    for rel, text in (extra_files or {}).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return d


def make_foreign_minor_venv(venv_dir: Path, old_minor: str = "3.11") -> None:
    """Build a venv mimicking Debian 12 (Python 3.11) moved onto Debian 13 (3.13):
    .venv/bin/python -> /usr/bin/python3, pyvenv.cfg says 3.11.2, and packages
    live only under lib/python3.11/site-packages."""
    if venv_dir.exists() or venv_dir.is_symlink():
        shutil.rmtree(venv_dir, ignore_errors=True)
    (venv_dir / "bin").mkdir(parents=True)
    (venv_dir / "lib" / f"python{old_minor}" / "site-packages" / "sampledep").mkdir(parents=True)
    (venv_dir / "lib" / f"python{old_minor}" / "site-packages" / "sampledep" / "__init__.py").write_text(
        'ANSWER = "old-venv"\n', encoding="utf-8"
    )
    (venv_dir / "pyvenv.cfg").write_text(
        f"home = /usr/bin\nimplementation = CPython\nversion_info = {old_minor}.2\n"
        "include-system-site-packages = false\n",
        encoding="utf-8",
    )
    sys_py = shutil.which("python3") or sys.executable
    os.symlink(sys_py, venv_dir / "bin" / "python")
    os.symlink("python", venv_dir / "bin" / "python3")


def file_hashes(folder: Path, names: tuple[str, ...]) -> dict[str, str]:
    return {n: hashlib.sha256((folder / n).read_bytes()).hexdigest() for n in names}


class FakeSystemd:
    def __init__(self) -> None:
        self.units: dict[str, dict[str, str]] = {}
        self.logs_by_unit: dict[str, str] = {}
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]):
        self.calls.append(list(args))
        out, rc = "", 0
        if args[0] == "systemd-run":
            name = next(a.split("=", 1)[1] for a in args if a.startswith("--unit="))
            folder = next((a.split("=", 2)[2] for a in args
                           if a.startswith("--property=WorkingDirectory=")), "")
            self.units[name] = {
                "WorkingDirectory": folder, "LoadState": "loaded",
                "ActiveState": "active", "SubState": "running", "MainPID": "5151",
                "NRestarts": "0", "Result": "success", "ActiveEnterTimestamp": "now",
                "ExecMainStatus": "0",
            }
        elif args[:3] == ["systemctl", "--user", "show"]:
            names = [a for a in args[3:] if a.endswith(".service")]
            blocks = []
            for n in names:
                props = self.units.get(n, {
                    "LoadState": "not-found", "ActiveState": "inactive",
                    "SubState": "dead", "MainPID": "0", "NRestarts": "0",
                    "Result": "success", "ActiveEnterTimestamp": "",
                    "ExecMainStatus": "0",
                })
                blocks.append("\n".join(f"{k}={v}" for k, v in props.items()))
            out = "\n\n".join(blocks) + "\n"
        elif args[:3] == ["systemctl", "--user", "stop"]:
            u = self.units.get(args[3])
            if u and u["ActiveState"] != "failed":
                self.units.pop(args[3])
        elif args[:3] == ["systemctl", "--user", "reset-failed"]:
            self.units.pop(args[3], None)
        elif args[:3] == ["systemctl", "--user", "list-units"]:
            out = "".join(
                f"{n} {p['LoadState']} {p['ActiveState']} {p['SubState']} x\n"
                for n, p in self.units.items()
                if n.startswith(S.UNIT_PREFIX)
            )
        elif args[0] == "journalctl":
            unit_name = args[args.index("-u") + 1] if "-u" in args else ""
            out = self.logs_by_unit.get(unit_name, "")
        return SimpleNamespace(returncode=rc, stdout=out, stderr="")


def start_and_verify_http(folder: Path, run_cmd: str) -> tuple[int, str]:
    port = free_port()
    env = {**os.environ, "PORT": str(port)}
    proc = subprocess.Popen(
        ["/bin/sh", "-c", "exec " + run_cmd],
        cwd=str(folder), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 10.0
        last_err = None
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return 0, f"exited {proc.returncode}: {proc.stderr.read()}"
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as resp:
                    return resp.status, resp.read().decode("utf-8")
            except OSError as exc:
                last_err = exc
                time.sleep(0.05)
        return 0, f"timeout: {last_err}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def load_cmd(name: str):
    path = ROOT / "bin" / name
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def main() -> int:
    whl = build_local_wheel(WHEELHOUSE)
    uv_bin = E._find_uv()
    check("uv is available for offline venv rebuild tests", uv_bin is not None, uv_bin)
    if not uv_bin:
        return 1

    fake = FakeSystemd()
    S.run = fake

    # Pick a foreign minor version guaranteed to differ from this host's Python.
    host_mm = f"{sys.version_info[0]}.{sys.version_info[1]}"
    foreign_mm = "3.11" if host_mm != "3.11" else "3.12"

    # -----------------------------------------------------------------------
    # 1. Exact SampleApp scenario: uv.lock + pyproject.toml + foreign minor .venv
    # -----------------------------------------------------------------------
    pyproject = (
        '[project]\nname = "sampleapp"\nversion = "1.0.0"\n'
        'requires-python = ">=3.11"\n'
        'dependencies = ["sampledep==1.0.0"]\n'
    )
    sampleapp = make_plugin(
        "sampleapp", "SampleApp", ".venv/bin/python server/main.py",
        extra_files={"pyproject.toml": pyproject, ".gitignore": ".venv/\n"},
    )
    lock_res = subprocess.run(
        [uv_bin, "lock", "--offline", "--no-index", "--find-links", str(WHEELHOUSE)],
        cwd=str(sampleapp), capture_output=True, text=True,
        env={**os.environ, "UV_PYTHON_PREFERENCE": "system"},
    )
    check("generated uv.lock offline for sampleapp fixture",
          lock_res.returncode == 0 and (sampleapp / "uv.lock").is_file(),
          lock_res.stderr)

    tracked = ("PLUGIN.md", "pyproject.toml", "uv.lock", "server/main.py", ".gitignore")
    before_hashes = file_hashes(sampleapp, tracked)

    make_foreign_minor_venv(sampleapp / ".venv", old_minor=foreign_mm)
    P.set_on("sampleapp", True)

    # Prove the broken venv genuinely fails before our mechanism runs.
    pre_run = subprocess.run(
        [str(sampleapp / ".venv" / "bin" / "python"), "server/main.py", "--check-import"],
        cwd=str(sampleapp), capture_output=True, text=True,
    )
    check("before repair: foreign-minor .venv fails with ModuleNotFoundError",
          pre_run.returncode != 0 and "ModuleNotFoundError" in pre_run.stderr,
          (pre_run.returncode, pre_run.stderr))

    insp = E.inspect(sampleapp, ".venv/bin/python server/main.py")
    check("inspect() catches Python minor mismatch on sleeping plugin",
          not insp["ok"] and insp["code"] == "python_version_mismatch"
          and insp["repairable"] is True
          and foreign_mm in insp["reason"] and host_mm in insp["reason"],
          insp)

    # Run reconcile() — rebuilds .venv without waking the on-demand server.
    rec = S.reconcile()
    check("reconcile() repairs sampleapp .venv and keeps it cleanly asleep",
          rec["repaired"] == ["sampleapp"] and "sampleapp" in rec["asleep"]
          and not rec.get("env_problems") and not rec["errors"],
          rec)
    check("...without modifying any tracked plugin file",
          file_hashes(sampleapp, tracked) == before_hashes)
    check("...and inspect() is now healthy", E.inspect(sampleapp, ".venv/bin/python server/main.py")["ok"])

    # Prove the server actually starts and serves HTTP 200 using sampledep!
    status_code, body = start_and_verify_http(sampleapp, ".venv/bin/python server/main.py")
    check("after repair: sampleapp server starts and answers HTTP 200 with installed package",
          status_code == 200 and body == "sample-lives", (status_code, body))

    # Idempotency: running reconcile() again must NOT rebuild a healthy venv.
    cfg_st = (sampleapp / ".venv" / "pyvenv.cfg").stat()
    rec2 = S.reconcile()
    cfg_st2 = (sampleapp / ".venv" / "pyvenv.cfg").stat()
    check("healthy plugin is not rebuilt on subsequent reconcile",
          rec2["repaired"] == [] and (cfg_st.st_ino, cfg_st.st_mtime_ns) == (cfg_st2.st_ino, cfg_st2.st_mtime_ns),
          (rec2, cfg_st, cfg_st2))

    # -----------------------------------------------------------------------
    # 2. On-demand wake repair via S.start() (before reconcile ever runs)
    # -----------------------------------------------------------------------
    make_foreign_minor_venv(sampleapp / ".venv", old_minor=foreign_mm)
    fake.calls.clear()
    S.start("sampleapp", P.wanted_servers()["sampleapp"])
    check("start() repairs a broken .venv on first wake and launches the unit",
          E.inspect(sampleapp, ".venv/bin/python server/main.py")["ok"]
          and any(c[0] == "systemd-run" for c in fake.calls),
          fake.calls)
    status_code2, body2 = start_and_verify_http(sampleapp, ".venv/bin/python server/main.py")
    check("...and server runs cleanly after wake-time repair",
          status_code2 == 200 and body2 == "sample-lives", (status_code2, body2))

    # -----------------------------------------------------------------------
    # 3. Dangling symlink (lost uv-managed Python) + previously failed unit
    # -----------------------------------------------------------------------
    shutil.rmtree(sampleapp / ".venv")
    (sampleapp / ".venv" / "bin").mkdir(parents=True)
    (sampleapp / ".venv" / "pyvenv.cfg").write_text(
        "home = /home/old/.local/share/uv/python/cpython-3.11.9/bin\nversion_info = 3.11.9\n",
        encoding="utf-8",
    )
    os.symlink("/home/old/.local/share/uv/python/cpython-3.11.9/bin/python3.11",
               sampleapp / ".venv" / "bin" / "python")
    fake.units["loam-plugin-sampleapp.service"] = {
        "WorkingDirectory": str(sampleapp), "LoadState": "loaded",
        "ActiveState": "failed", "SubState": "failed", "MainPID": "0",
        "NRestarts": "5", "Result": "exit-code", "ActiveEnterTimestamp": "",
        "ExecMainStatus": "1",
    }
    insp_dangling = E.inspect(sampleapp, ".venv/bin/python server/main.py")
    check("inspect() catches dangling interpreter symlink",
          not insp_dangling["ok"] and insp_dangling["code"] == "dangling_interpreter"
          and insp_dangling["repairable"] is True, insp_dangling)
    rec3 = S.reconcile()
    check("reconcile() repairs dangling-symlink venv and clears failed on-demand unit to asleep",
          rec3["repaired"] == ["sampleapp"] and "sampleapp" in rec3["asleep"]
          and "loam-plugin-sampleapp.service" not in fake.units,
          (rec3, fake.units))

    # -----------------------------------------------------------------------
    # 4. Restore from backup (missing .venv) with requirements.txt recipe
    # -----------------------------------------------------------------------
    reqplug = make_plugin(
        "reqplug", "Reqplug", ".venv/bin/python server/main.py",
        extra_files={"requirements.txt": f"{whl}\n"},
    )
    P.set_on("reqplug", True)
    insp_missing = E.inspect(reqplug, ".venv/bin/python server/main.py")
    check("inspect() catches missing .venv after restore",
          not insp_missing["ok"] and insp_missing["code"] == "venv_missing"
          and insp_missing["repairable"] is True, insp_missing)
    rec4 = S.reconcile()
    check("reconcile() builds missing .venv from requirements.txt",
          rec4["repaired"] == ["reqplug"]
          and E.inspect(reqplug, ".venv/bin/python server/main.py")["ok"],
          rec4)
    sc_req, body_req = start_and_verify_http(reqplug, ".venv/bin/python server/main.py")
    check("...and requirements.txt plugin server starts cleanly",
          sc_req == 200 and body_req == "sample-lives", (sc_req, body_req))

    # -----------------------------------------------------------------------
    # 4b. pyproject.toml without uv.lock (must NOT create uv.lock) +
    #     broken console script shebang detection
    # -----------------------------------------------------------------------
    pyp = make_plugin(
        "pyponly", "PypOnly", ".venv/bin/python server/main.py",
        extra_files={"pyproject.toml": pyproject},
    )
    make_foreign_minor_venv(pyp / ".venv", old_minor=foreign_mm)
    P.set_on("pyponly", True)
    rec4b = S.reconcile()
    check("reconcile() rebuilds pyproject.toml-only plugin without creating uv.lock",
          rec4b["repaired"] == ["pyponly"]
          and E.inspect(pyp, ".venv/bin/python server/main.py")["ok"]
          and not (pyp / "uv.lock").exists(),
          rec4b)

    # Broken console script shebang (e.g. moved home directory)
    cli_script = pyp / ".venv" / "bin" / "myserve"
    cli_script.write_text("#!/home/old-user/plugins/pyponly/.venv/bin/python\nprint('x')\n",
                          encoding="utf-8")
    cli_script.chmod(0o755)
    insp_shebang = E.inspect(pyp, ".venv/bin/myserve --port 8000")
    check("inspect() catches broken console script shebang inside .venv/bin",
          not insp_shebang["ok"] and insp_shebang["code"] == "broken_shebang"
          and insp_shebang["repairable"] is True,
          insp_shebang)
    P.set_on("pyponly", False)

    # -----------------------------------------------------------------------
    # 5. Failed rebuild rolls back original venv and applies cooldown
    # -----------------------------------------------------------------------
    badlock = make_plugin(
        "badlock", "Badlock", ".venv/bin/python server/main.py",
        extra_files={
            "pyproject.toml": (
                '[project]\nname = "badlock"\nversion = "1.0.0"\n'
                'dependencies = ["nonexistent-pkg-xyz==9.9.9"]\n'
            ),
            "uv.lock": "not valid toml = = =\n",
        },
    )
    make_foreign_minor_venv(badlock / ".venv", old_minor=foreign_mm)
    (badlock / ".venv" / "sentinel.txt").write_text("kept", encoding="utf-8")
    P.set_on("badlock", True)
    rec_bad = S.reconcile()
    check("failed rebuild restores original .venv and records env_problems",
          "badlock" in (rec_bad.get("env_problems") or {})
          and (badlock / ".venv" / "sentinel.txt").is_file()
          and E.last_repair_error("badlock") is not None,
          rec_bad)
    # Cooldown prevents hammering rebuild immediately.
    rep_again = E.repair("badlock", badlock, ".venv/bin/python server/main.py")
    check("subsequent repair respects failure cooldown",
          rep_again.get("skipped_cooldown") is True, rep_again)
    P.set_on("badlock", False)

    # -----------------------------------------------------------------------
    # 6. Visibility in `loam status` and `loam-check`:
    #    (a) sleeping server that would fail on wake (unrepairable)
    #    (b) crash-looped failed unit
    # -----------------------------------------------------------------------
    # (a) Sleeping plugin with foreign venv and NO dependency recipe (unrepairable).
    unrep = make_plugin("unrep", "Unrep", ".venv/bin/python server/main.py")
    make_foreign_minor_venv(unrep / ".venv", old_minor=foreign_mm)
    P.set_on("unrep", True)

    # (b) Enabled plugin whose unit crash-looped and stayed failed.
    crasher = make_plugin("crasher", "Crasher", "python3 server/main.py")
    P.set_on("crasher", True)
    fake.units["loam-plugin-crasher.service"] = {
        "WorkingDirectory": str(crasher), "LoadState": "loaded",
        "ActiveState": "failed", "SubState": "failed", "MainPID": "0",
        "NRestarts": "5", "Result": "exit-code", "ActiveEnterTimestamp": "",
        "ExecMainStatus": "1",
    }
    fake.logs_by_unit["loam-plugin-crasher.service"] = (
        "Traceback (most recent call last):\n"
        "  File \"server/main.py\", line 1, in <module>\n"
        "ModuleNotFoundError: No module named 'uvicorn'\n"
    )

    # (c) Disabled plugin with broken venv must NOT be reported.
    disabled = make_plugin("offplug", "Offplug", ".venv/bin/python server/main.py")
    make_foreign_minor_venv(disabled / ".venv", old_minor=foreign_mm)
    P.set_on("offplug", False)

    # Verify `loam status` (`bin/loam-status` gather()).
    st = load_cmd("loam-status")
    st.dep.EDGE = {}
    st.lc.DATA_DIR = None
    st.user_systemctl = lambda *a: "active"
    st.broker_up = lambda: True
    st.app_health = lambda name: "running healthy"
    st.dep.unclaimed = lambda: False
    st.login_health = lambda: "running healthy"
    st.container_health = lambda name: "running"
    st.loam_units.rendered = lambda: []

    data = st.gather()
    by_key = {p["key"]: p for p in data["parts"]}
    check("loam status is NOT ok when enabled plugins cannot start",
          data["ok"] is False, data)
    check("loam status flags sleeping unrepairable plugin in plain words",
          "plugin:unrep" in by_key and by_key["plugin:unrep"]["state"] == st.BAD
          and foreign_mm in by_key["plugin:unrep"]["say"]
          and "cannot start" in by_key["plugin:unrep"]["say"],
          by_key.get("plugin:unrep"))
    check("loam status flags crash-looped failed plugin with error detail",
          "plugin:crasher" in by_key and by_key["plugin:crasher"]["state"] == st.BAD
          and "ModuleNotFoundError" in by_key["plugin:crasher"]["say"],
          by_key.get("plugin:crasher"))
    check("loam status ignores disabled plugin with broken venv",
          "plugin:offplug" not in by_key, list(by_key))
    check("loam status does NOT flag healthy repaired plugins (sampleapp, reqplug)",
          "plugin:sampleapp" not in by_key and "plugin:reqplug" not in by_key, list(by_key))

    # Verify `loam-check` reports both once to attention agent without duplicating unit: facts.
    AT = time.time()
    ws = lc.WORKSPACES / "iris"
    (ws / "crons").mkdir(parents=True, exist_ok=True)
    (ws / "agent.json").write_text(json.dumps({"name": "iris", "workspace": str(ws), "vault": str(lc.VAULT)}))
    rec_md = ws / "crons" / "reflection.md"
    rec_md.write_text("---\nschedule: daily at 04:00 PT\nenabled: true\n---\nReflect.\n")
    lc.LOGS.mkdir(parents=True, exist_ok=True)
    (lc.LOGS / "wake-iris.jsonl").write_text(
        json.dumps({"ts": ck.iso(AT - 3600), "finished": ck.iso(AT - 3500), "ok": True, "lane": "reflection"}) + "\n"
    )
    loam_pressure.PRESSURE.parent.mkdir(parents=True, exist_ok=True)
    loam_pressure.PRESSURE.write_text(json.dumps({
        "at": ck.iso(AT - 30), "ram_pct": 40, "swap_free_pct": 90, "episode_since": None,
    }))
    sample = lc.LOGS / "memsample.jsonl"
    sample.write_text("{}\n")
    os.utime(sample, (AT - 60, AT - 60))
    creds = TMP / "credentials.json"
    creds.write_text(json.dumps({"claudeAiOauth": {
        "accessToken": "x", "refreshTokenExpiresAt": (AT + 30 * 86400) * 1000,
    }}))
    loam_broker.CREDENTIALS_PATH = creds
    loam_broker.login_source = lambda env=None: "file"
    loam_codex.AUTH_PATH = TMP / "codex-auth.json"
    loam_antigravity.login_state = lambda fresh=False: {"source": "none", "connected": False, "reason": "none"}

    class Up(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    broker_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Up)
    threading.Thread(target=broker_srv.serve_forever, daemon=True).start()
    os.environ["LOAM_CHECK_BROKER_URL"] = f"http://127.0.0.1:{broker_srv.server_address[1]}/up"

    # Simulate systemctl --user list-units --failed showing loam-plugin-crasher.service.
    def fake_ck_systemctl(*args):
        if "--user" in args and "--failed" in args:
            return SimpleNamespace(
                returncode=0,
                stdout="loam-plugin-crasher.service loaded failed failed x\n",
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    ck.systemctl = fake_ck_systemctl
    ck.units.__defaults__ = (fake_ck_systemctl,)
    ck.broker.__defaults__ = (lambda s: None,)
    ck.once_a_day = lambda: []
    ck.github = lambda: []
    ck.shutil.disk_usage = lambda where: ck.shutil._ntuple_diskusage(
        100 * 10**9, 10 * 10**9, 90 * 10**9
    )

    told = []
    def fake_tell(text, **kw):
        res = la.tell(text, kick=False, **kw)
        told.append((kw.get("key"), res))
        return res

    ck_state = ck.tick(AT, daily=False, tell=fake_tell)
    fact_by_id = {f["id"]: f for f in ck_state["facts"]}
    pend_keys = [r["key"] for r in la.pending()]
    check("loam-check flags sleeping unrepairable plugin as false fact",
          "plugin-server:unrep" in fact_by_id and fact_by_id["plugin-server:unrep"]["ok"] is False
          and "cannot start" in fact_by_id["plugin-server:unrep"]["says"],
          fact_by_id.get("plugin-server:unrep"))
    check("loam-check flags crash-looped failed plugin once (no duplicate unit: fact)",
          "plugin-server:crasher" in fact_by_id and fact_by_id["plugin-server:crasher"]["ok"] is False
          and "unit:loam-plugin-crasher.service" not in fact_by_id,
          sorted(fact_by_id))
    check("...and queues both for the attention agent",
          "loam-check:plugin-server:unrep" in pend_keys
          and "loam-check:plugin-server:crasher" in pend_keys,
          pend_keys)

    # Once both problems are resolved, loam status and loam-check both go green.
    P.set_on("unrep", False)
    fake.units.pop("loam-plugin-crasher.service", None)
    data_ok = st.gather()
    check("after fixing problems, loam status returns ok=True",
          data_ok["ok"] is True and not any(p["key"].startswith("plugin:") for p in data_ok["parts"]),
          data_ok)

    ck.units.__defaults__ = (lambda *a: SimpleNamespace(returncode=0, stdout="", stderr=""),)
    ck_ok = ck.tick(AT + 60, daily=False, tell=None)
    check("after fixing problems, loam-check is green and withdraws pending notices",
          ck_ok["ok"] is True and not la.pending(),
          (ck_ok["ok"], la.pending()))

    broker_srv.shutdown()
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
