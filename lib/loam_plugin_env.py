"""Inspect and repair a plugin server's local runtime environment.

When a Loam data disk moves to another server (`loam-hosting migrate` or
`revive`), the host upgrades its OS in place (for example Debian 12 Python 3.11
to Debian 13 Python 3.13), or a plugin folder is restored from backup without
its gitignored `.venv`, a plugin whose `server.run` invokes a local Python
virtual environment (such as `.venv/bin/python server/main.py`) can break while
the server is asleep.

This module provides:
- ``inspect(folder, run_cmd)``: read-only preflight that checks whether the
  command, interpreter, virtual environment, and script entrypoint are usable
  on this host without waking or mutating anything.
- ``repair(pid, folder, run_cmd, ...)`` / ``ensure_ready(...)``: rebuilds a
  broken or missing Python virtual environment in place under a per-plugin
  lock from the plugin's own declarative dependency files (``uv.lock`` +
  ``pyproject.toml`` via ``uv sync --frozen``, ``pyproject.toml``, or
  ``requirements.txt``) without modifying any tracked plugin files.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
from contextlib import contextmanager
from pathlib import Path

import loam_config as lc

DEFAULT_REPAIR_COOLDOWN_S = 1800.0   # 30 min backoff for background reconcile
START_REPAIR_COOLDOWN_S = 30.0       # brief dedupe for interactive wakes
DEFAULT_REBUILD_TIMEOUT_S = 300.0

_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SHELL_OPS = frozenset({";", "&&", "||", "|", "&", ">", ">>", "<", "2>&1", "1>&2"})
_VENV_INLINE_RE = re.compile(
    r"(?:^|[\s'\"(=])((?:\./)?(?:\.venv|venv)/bin/([A-Za-z0-9._-]+))"
)
_MM_RE = re.compile(r"^(\d+)\.(\d+)")
_PY_SITE_DIR_RE = re.compile(r"^python(\d+\.\d+)$")
_SH_EXEC_RE = re.compile(r"'''exec'\s+'([^']+)'")

# In-process cache of probed interpreter major.minor keyed by
# (realpath, st_dev, st_ino, st_mtime_ns, st_size).
_PROBE_CACHE: dict[tuple[str, int, int, int, int], str] = {}


def state_dir() -> Path:
    env = os.environ.get("LOAM_PLUGIN_STATE")
    return Path(env) if env else lc.STATE / "plugins"


def repair_state_path() -> Path:
    return state_dir() / "env-repair.json"


@contextmanager
def _plugin_lock(pid: str):
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", pid)
    with open(d / f"env-{safe}.lock", "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _read_repair_state() -> dict:
    try:
        data = json.loads(repair_state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_repair_record(pid: str, record: dict | None) -> None:
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "env-repair.lock", "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            data = _read_repair_state()
            if record is None:
                data.pop(pid, None)
            else:
                data[pid] = record
            path = repair_state_path()
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(tmp, path)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def last_repair_record(pid: str) -> dict | None:
    rec = _read_repair_state().get(pid)
    return rec if isinstance(rec, dict) else None


def last_repair_error(
    pid: str,
    folder: str | Path | None = None,
    run_cmd: str | None = None,
) -> str | None:
    rec = last_repair_record(pid)
    if not (rec and rec.get("ok") is False and isinstance(rec.get("error"), str)):
        return None
    if folder is not None and run_cmd is not None and rec.get("signature"):
        root = Path(folder)
        parsed = _parse_run(root, run_cmd)
        if parsed["kind"] == "python-venv":
            recipe = _find_python_recipe(root, parsed["venv_dir"])
            if recipe and _repair_signature(root, parsed["venv_dir"], recipe) != rec["signature"]:
                return None
    return rec["error"]


def _expand_plugin_dir(token: str, folder: Path) -> str:
    return (token
            .replace("${LOAM_PLUGIN_DIR}", str(folder))
            .replace("$LOAM_PLUGIN_DIR", str(folder)))


def _is_inside(path: Path, parent: Path) -> bool:
    """True when `path` (normalized lexically, without resolving a final symlink)
    is strictly inside `parent`."""
    try:
        norm_parent = Path(os.path.normpath(str(parent.resolve())))
        norm_path = Path(os.path.normpath(str(path)))
        if not norm_path.is_absolute():
            norm_path = Path(os.path.normpath(str(norm_parent / norm_path)))
        return norm_path != norm_parent and norm_path.is_relative_to(norm_parent)
    except (OSError, ValueError):
        return False


def _is_inside_loam_checkout(folder: Path) -> bool:
    try:
        return folder.resolve().is_relative_to(lc.ROOT.resolve())
    except OSError:
        return False


def _symlink_escapes(path: Path, folder: Path) -> bool:
    if not path.is_symlink():
        return False
    try:
        target = path.resolve(strict=False)
        return not target.is_relative_to(folder.resolve())
    except OSError:
        return True


def _find_uv() -> str | None:
    override = os.environ.get("LOAM_UV_BIN")
    if override is not None:
        return override if (override and os.access(override, os.X_OK)) else None
    if lc.UV_BIN.is_file() and os.access(lc.UV_BIN, os.X_OK):
        return str(lc.UV_BIN)
    return shutil.which("uv", path=lc.UNIT_PATH)


def _find_python_recipe(folder: Path, venv_dir: Path) -> dict | None:
    candidates: list[Path] = []
    if venv_dir.parent != folder and _is_inside(venv_dir.parent, folder):
        candidates.append(venv_dir.parent)
    candidates.append(folder)
    srv = folder / "server"
    if srv.is_dir() and srv not in candidates:
        candidates.append(srv)

    for base in candidates:
        lock = base / "uv.lock"
        pyproj = base / "pyproject.toml"
        reqs = base / "requirements.txt"
        if lock.is_file() and pyproj.is_file():
            return {"kind": "uv-lock", "base": base, "files": [lock, pyproj]}
        if pyproj.is_file():
            return {"kind": "pyproject", "base": base, "files": [pyproj]}
        if reqs.is_file():
            return {"kind": "requirements", "base": base, "files": [reqs]}
    return None


def _can_run_recipe(recipe: dict | None) -> tuple[bool, str | None]:
    if not recipe:
        return False, "no uv.lock, pyproject.toml, or requirements.txt found to rebuild it"
    uv = _find_uv()
    if recipe["kind"] == "uv-lock":
        if uv:
            return True, None
        return False, "uv is not installed on this server"
    if uv:
        return True, None
    if shutil.which("python3", path=lc.UNIT_PATH):
        return True, None
    return False, "neither uv nor python3 is available on this server"


def _extract_script_arg(exe_name: str, args: list[str], folder: Path) -> str | None:
    low = exe_name.lower()
    if low.startswith("python"):
        skip_next = False
        for tok in args:
            if skip_next:
                skip_next = False
                continue
            if tok in ("-m", "-c", "-"):
                return None
            if tok in ("-W", "-X", "--check-hash-based-pycs"):
                skip_next = True
                continue
            if tok.startswith("-"):
                continue
            expanded = _expand_plugin_dir(tok, folder)
            if "$" in expanded:
                return None
            return expanded
        return None
    if low == "node":
        skip_next = False
        for tok in args:
            if skip_next:
                skip_next = False
                continue
            if tok in ("-e", "--eval", "-p", "--print", "-"):
                return None
            if tok in ("-r", "--require", "--import", "--loader", "--env-file",
                       "--conditions", "-C"):
                skip_next = True
                continue
            if tok.startswith("-"):
                continue
            expanded = _expand_plugin_dir(tok, folder)
            if "$" in expanded:
                return None
            return expanded
        return None
    if low in ("sh", "bash", "dash"):
        for tok in args:
            if tok == "-c":
                return None
            if tok.startswith("-"):
                continue
            expanded = _expand_plugin_dir(tok, folder)
            if "$" in expanded:
                return None
            return expanded
        return None
    return None


def _parse_run(folder: Path, run_cmd: str) -> dict:
    try:
        tokens = shlex.split(run_cmd)
    except ValueError:
        tokens = run_cmd.split()

    i = 0
    while i < len(tokens) and _ENV_ASSIGN_RE.match(tokens[i]):
        i += 1
    if i < len(tokens) and tokens[i] == "exec":
        i += 1
    if i >= len(tokens):
        return {"kind": "empty", "cmd_raw": "", "args": [], "script": None}

    cmd_raw = _expand_plugin_dir(tokens[i], folder)
    rest: list[str] = []
    for tok in tokens[i + 1:]:
        if tok in _SHELL_OPS:
            break
        rest.append(tok)

    if "/" in cmd_raw:
        cmd_path = (Path(os.path.normpath(cmd_raw)) if os.path.isabs(cmd_raw)
                    else Path(os.path.normpath(str(folder / cmd_raw))))
        if cmd_path.parent.name == "bin":
            venv_dir = cmd_path.parent.parent
            in_folder = _is_inside(venv_dir, folder)
            looks_venv = (
                (venv_dir / "pyvenv.cfg").is_file()
                or venv_dir.name in (".venv", "venv")
                or (cmd_path.name.lower().startswith("python")
                    and in_folder
                    and _find_python_recipe(folder, venv_dir) is not None)
            )
            if looks_venv:
                return {
                    "kind": "python-venv",
                    "cmd_raw": cmd_raw,
                    "cmd_path": cmd_path,
                    "venv_dir": venv_dir,
                    "exe_name": cmd_path.name,
                    "in_folder": in_folder,
                    "args": rest,
                    "script": _extract_script_arg(cmd_path.name, rest, folder),
                }

    # Shell wrappers (e.g. `sh -c '.venv/bin/python server/main.py'`)
    m = _VENV_INLINE_RE.search(run_cmd)
    if m:
        rel_cmd, exe_name = m.group(1), m.group(2)
        cmd_path = Path(os.path.normpath(str(folder / rel_cmd)))
        venv_dir = cmd_path.parent.parent
        in_folder = _is_inside(venv_dir, folder)
        if in_folder and (
            (venv_dir / "pyvenv.cfg").is_file()
            or venv_dir.exists()
            or _find_python_recipe(folder, venv_dir) is not None
        ):
            return {
                "kind": "python-venv",
                "cmd_raw": rel_cmd,
                "cmd_path": cmd_path,
                "venv_dir": venv_dir,
                "exe_name": exe_name,
                "in_folder": in_folder,
                "args": [],
                "script": None,
            }

    return {
        "kind": "command",
        "cmd_raw": cmd_raw,
        "args": rest,
        "script": _extract_script_arg(Path(cmd_raw).name, rest, folder),
    }


def _parse_pyvenv_cfg(cfg_path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in cfg_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip().lower()] = v.strip()
    except OSError:
        pass
    return out


def _major_minor(version_text: str | None) -> str | None:
    if not version_text:
        return None
    m = _MM_RE.match(version_text.strip())
    return f"{m.group(1)}.{m.group(2)}" if m else None


def _site_packages_versions(venv_dir: Path) -> list[str]:
    found: set[str] = set()
    for lib_name in ("lib", "lib64"):
        lib_dir = venv_dir / lib_name
        if not lib_dir.is_dir():
            continue
        try:
            for child in lib_dir.iterdir():
                m = _PY_SITE_DIR_RE.match(child.name)
                if m and (child / "site-packages").is_dir():
                    found.add(m.group(1))
        except OSError:
            pass
    return sorted(found)


def _clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k not in ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH")}
    env["PATH"] = lc.UNIT_PATH
    return env


def _probe_python_version(py_path: Path) -> tuple[str | None, str | None]:
    try:
        realpath = os.path.realpath(py_path)
        st = os.stat(realpath)
    except OSError as exc:
        return None, str(exc)
    key = (realpath, st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)
    cached = _PROBE_CACHE.get(key)
    if cached is not None:
        return cached, None
    try:
        done = subprocess.run(
            [str(py_path), "-c",
             "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
            capture_output=True, text=True, timeout=10, env=_clean_env(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)
    if done.returncode != 0:
        err = (done.stderr or done.stdout or f"exit {done.returncode}").strip()
        return None, err.splitlines()[-1][:200] if err else f"exit {done.returncode}"
    mm = _major_minor(done.stdout.strip())
    if not mm:
        return None, f"unexpected version output: {done.stdout.strip()[:80]!r}"
    _PROBE_CACHE[key] = mm
    return mm, None


def _check_shebang(script: Path) -> str | None:
    """Return the missing/unusable interpreter named in a `#!` header, or None."""
    try:
        with script.open("rb") as fh:
            head = fh.read(512)
    except OSError:
        return None
    if not head.startswith(b"#!"):
        return None
    lines = head.decode("utf-8", errors="replace").splitlines()
    if not lines:
        return None
    first = lines[0][2:].strip()
    if not first:
        return None
    parts = first.split()
    interp = parts[0]
    if interp in ("/bin/sh", "/usr/bin/sh") and len(lines) > 1:
        m = _SH_EXEC_RE.search(lines[1])
        if m:
            target = Path(m.group(1))
            if not (target.exists() and os.access(target, os.X_OK)):
                return str(target)
            return None
    if Path(interp).name == "env":
        idx = 1
        while idx < len(parts) and parts[idx].startswith("-"):
            idx += 1
        if idx < len(parts):
            wanted = parts[idx]
            if not shutil.which(wanted, path=lc.UNIT_PATH):
                return wanted
        return None
    target = Path(interp)
    if not (target.exists() and os.access(target, os.X_OK)):
        return interp
    return None


def _rel_display(path: Path, folder: Path) -> str:
    try:
        return path.relative_to(folder).as_posix()
    except ValueError:
        return str(path)


def _check_script_exists(folder: Path, script_arg: str | None) -> dict | None:
    if not script_arg:
        return None
    p = Path(script_arg) if os.path.isabs(script_arg) else (folder / script_arg)
    if not p.is_file():
        return {
            "ok": False,
            "code": "script_missing",
            "reason": f"server entrypoint '{script_arg}' is missing",
            "repairable": False,
        }
    return None


def _inspect_python_venv(folder: Path, parsed: dict) -> dict:
    venv_dir: Path = parsed["venv_dir"]
    exe_path: Path = parsed["cmd_path"]
    exe_name: str = parsed["exe_name"]
    in_folder: bool = parsed["in_folder"]
    rel_venv = _rel_display(venv_dir, folder)
    rel_exe = _rel_display(exe_path, folder)

    can_touch = (
        in_folder
        and not _is_inside_loam_checkout(folder)
        and not _symlink_escapes(venv_dir, folder)
    )
    recipe = _find_python_recipe(folder, venv_dir) if can_touch else None
    can_rebuild, repair_why = _can_run_recipe(recipe) if can_touch else (
        False,
        "virtual environment is outside the plugin folder" if not in_folder
        else "cannot rebuild inside Loam's own checkout" if _is_inside_loam_checkout(folder)
        else "virtual environment symlinks outside the plugin folder",
    )

    def bad(code: str, reason: str, *, repairable: bool = True) -> dict:
        out = {
            "ok": False,
            "kind": "python-venv",
            "code": code,
            "reason": reason,
            "venv": str(venv_dir),
            "repairable": bool(repairable and can_rebuild),
        }
        if recipe:
            out["recipe"] = recipe["kind"]
        if repairable and not can_rebuild and repair_why:
            out["repair_why"] = repair_why
        return out

    if venv_dir.is_symlink() and not venv_dir.exists():
        return bad("venv_broken_symlink",
                   f"Python environment {rel_venv} is a broken symlink ({os.readlink(venv_dir)})")
    if not venv_dir.exists():
        return bad("venv_missing", f"Python environment {rel_venv} is missing")
    if not venv_dir.is_dir():
        return bad("venv_invalid", f"{rel_venv} is not a directory", repairable=False)

    cfg_path = venv_dir / "pyvenv.cfg"
    if not cfg_path.is_file():
        return bad("pyvenv_cfg_missing",
                   f"Python environment {rel_venv} is incomplete (missing pyvenv.cfg)")

    if exe_path.is_symlink() and not exe_path.exists():
        return bad("dangling_interpreter",
                   f"Python environment {rel_venv} points to a missing interpreter "
                   f"({os.readlink(exe_path)})")
    if not exe_path.exists():
        return bad("exe_missing", f"{rel_exe} is missing")
    if not os.access(exe_path, os.X_OK):
        return bad("exe_not_executable", f"{rel_exe} is not executable")

    venv_python = None
    for cand in (exe_path if exe_name.lower().startswith("python") else None,
                 venv_dir / "bin" / "python",
                 venv_dir / "bin" / "python3"):
        if cand is None:
            continue
        if cand.is_symlink() and not cand.exists():
            return bad("dangling_interpreter",
                       f"Python environment {rel_venv} points to a missing interpreter "
                       f"({os.readlink(cand)})")
        if cand.exists() and os.access(cand, os.X_OK):
            venv_python = cand
            break
    if venv_python is None:
        return bad("python_missing", f"Python interpreter in {rel_venv} is missing")

    if exe_path != venv_python:
        bad_shebang = _check_shebang(exe_path)
        if bad_shebang:
            return bad("broken_shebang",
                       f"{rel_exe} points to a missing interpreter ({bad_shebang})")

    actual_mm, probe_err = _probe_python_version(venv_python)
    if actual_mm is None:
        return bad("interpreter_failed",
                   f"Python interpreter in {rel_venv} cannot run ({probe_err})")

    cfg = _parse_pyvenv_cfg(cfg_path)
    cfg_mm = _major_minor(cfg.get("version_info") or cfg.get("version"))
    if cfg_mm and cfg_mm != actual_mm:
        return bad("python_version_mismatch",
                   f"Python environment {rel_venv} was built for Python {cfg_mm}, "
                   f"but its interpreter is now Python {actual_mm}")

    site_vers = _site_packages_versions(venv_dir)
    if actual_mm not in site_vers:
        found_note = f" (found {', '.join('Python ' + v for v in site_vers)})" if site_vers else ""
        return bad("site_packages_missing",
                   f"Python environment {rel_venv} has no site-packages for "
                   f"Python {actual_mm}{found_note}")

    script_err = _check_script_exists(folder, parsed.get("script"))
    if script_err:
        return {**script_err, "kind": "python-venv", "venv": str(venv_dir)}

    return {
        "ok": True,
        "kind": "python-venv",
        "venv": str(venv_dir),
        "python": actual_mm,
        **({"recipe": recipe["kind"]} if recipe else {}),
    }


def _inspect_command(folder: Path, parsed: dict) -> dict:
    cmd_raw: str = parsed["cmd_raw"]
    if not cmd_raw:
        return {"ok": False, "kind": "command", "code": "empty_command",
                "reason": "server.run is empty", "repairable": False}

    if "/" in cmd_raw:
        cmd_path = (Path(os.path.normpath(cmd_raw)) if os.path.isabs(cmd_raw)
                    else Path(os.path.normpath(str(folder / cmd_raw))))
        if cmd_path.is_symlink() and not cmd_path.exists():
            return {"ok": False, "kind": "command", "code": "dangling_command",
                    "reason": f"server command '{cmd_raw}' points to a missing target "
                              f"({os.readlink(cmd_path)})",
                    "repairable": False}
        if not cmd_path.exists():
            return {"ok": False, "kind": "command", "code": "command_missing",
                    "reason": f"server command '{cmd_raw}' does not exist",
                    "repairable": False}
        if not os.access(cmd_path, os.X_OK):
            return {"ok": False, "kind": "command", "code": "command_not_executable",
                    "reason": f"server command '{cmd_raw}' is not executable",
                    "repairable": False}
        bad_shebang = _check_shebang(cmd_path)
        if bad_shebang:
            return {"ok": False, "kind": "command", "code": "broken_shebang",
                    "reason": f"server command '{cmd_raw}' points to a missing interpreter "
                              f"({bad_shebang})",
                    "repairable": False}
    else:
        if "$" not in cmd_raw and not shutil.which(cmd_raw, path=lc.UNIT_PATH):
            return {"ok": False, "kind": "command", "code": "command_not_found",
                    "reason": f"server command '{cmd_raw}' is not installed on this server",
                    "repairable": False}

    script_err = _check_script_exists(folder, parsed.get("script"))
    if script_err:
        return {**script_err, "kind": "command"}

    if Path(cmd_raw).name.lower() == "node":
        pkg_json = folder / "package.json"
        if pkg_json.is_file():
            try:
                pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pkg = {}
            deps = pkg.get("dependencies") if isinstance(pkg, dict) else None
            if (isinstance(deps, dict) and deps
                    and not (folder / "node_modules").is_dir()
                    and not (folder / "server" / "node_modules").is_dir()):
                return {"ok": False, "kind": "command", "code": "node_modules_missing",
                        "reason": "Node dependencies (node_modules) are missing",
                        "repairable": False}

    return {"ok": True, "kind": "command"}


def inspect(folder: str | Path, run_cmd: str) -> dict:
    """Read-only check of whether a plugin's server environment can start."""
    root = Path(folder)
    if not root.is_dir():
        return {"ok": False, "kind": "folder", "code": "folder_missing",
                "reason": f"plugin folder '{root}' does not exist",
                "repairable": False}
    parsed = _parse_run(root, run_cmd)
    if parsed["kind"] == "python-venv":
        return _inspect_python_venv(root, parsed)
    return _inspect_command(root, parsed)


def format_reason(info: dict, repair_err: str | None = None) -> str:
    base = info.get("reason") or "server environment is not ready"
    if repair_err:
        return f"{base}; automatic rebuild failed: {repair_err}"
    why = info.get("repair_why")
    if why and not info.get("repairable"):
        return f"{base} ({why})"
    return base


_full_reason = format_reason


def _repair_signature(folder: Path, venv_dir: Path, recipe: dict) -> str:
    h = hashlib.sha256()
    h.update(recipe["kind"].encode() + b"\0")
    for p in [*recipe["files"], venv_dir / "pyvenv.cfg"]:
        h.update(str(p).encode() + b"\0")
        try:
            st = p.lstat()
            h.update(f"{st.st_mtime_ns}:{st.st_size}".encode() + b"\0")
        except OSError:
            h.update(b"missing\0")
    sys_py = shutil.which("python3", path=lc.UNIT_PATH) or ""
    h.update(os.path.realpath(sys_py).encode() if sys_py else b"")
    return h.hexdigest()[:24]


def _pyproject_has_build_system(path: Path) -> bool:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return isinstance(data.get("build-system"), dict)
    except (OSError, ValueError):
        return False


def _run_cmd(argv: list[str], cwd: Path, env: dict[str, str],
             timeout: float) -> tuple[bool, str]:
    try:
        done = subprocess.run(
            argv, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)[:300]
    if done.returncode == 0:
        return True, ""
    out = (done.stderr or done.stdout or f"exit {done.returncode}").strip()
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    tail = " | ".join(lines[-3:]) if lines else f"exit {done.returncode}"
    return False, tail[-300:]


def _run_python_rebuild(venv_dir: Path, recipe: dict, *, timeout: float) -> tuple[bool, str]:
    base: Path = recipe["base"]
    kind: str = recipe["kind"]
    uv = _find_uv()
    env = _clean_env()
    env["UV_PYTHON_PREFERENCE"] = "system"
    env["UV_PROJECT_ENVIRONMENT"] = str(venv_dir)
    if os.environ.get("UV_CACHE_DIR"):
        env["UV_CACHE_DIR"] = os.environ["UV_CACHE_DIR"]
    if os.environ.get("UV_OFFLINE"):
        env["UV_OFFLINE"] = os.environ["UV_OFFLINE"]
    if os.environ.get("UV_FIND_LINKS"):
        env["UV_FIND_LINKS"] = os.environ["UV_FIND_LINKS"]
    if os.environ.get("UV_NO_INDEX"):
        env["UV_NO_INDEX"] = os.environ["UV_NO_INDEX"]

    if kind == "uv-lock":
        if not uv:
            return False, "uv is not installed on this server"
        # --frozen guarantees uv.lock and pyproject.toml are never modified.
        return _run_cmd([uv, "sync", "--frozen", "--no-progress"], base, env, timeout)

    if kind == "pyproject":
        pyproj = base / "pyproject.toml"
        has_build = _pyproject_has_build_system(pyproj)
        if uv:
            ok, err = _run_cmd([uv, "venv", "--quiet", str(venv_dir)], base, env, timeout)
            if not ok:
                return False, err
            py = str(venv_dir / "bin" / "python")
            if has_build:
                return _run_cmd([uv, "pip", "install", "--quiet", "--python", py, str(base)],
                                base, env, timeout)
            return _run_cmd([uv, "pip", "install", "--quiet", "--python", py, "-r", str(pyproj)],
                            base, env, timeout)
        sys_py = shutil.which("python3", path=lc.UNIT_PATH)
        if not sys_py:
            return False, "neither uv nor python3 is available"
        ok, err = _run_cmd([sys_py, "-m", "venv", str(venv_dir)], base, env, timeout)
        if not ok:
            return False, err
        return _run_cmd([str(venv_dir / "bin" / "pip"), "install", "--quiet", str(base)],
                        base, env, timeout)

    if kind == "requirements":
        reqs = base / "requirements.txt"
        if uv:
            ok, err = _run_cmd([uv, "venv", "--quiet", str(venv_dir)], base, env, timeout)
            if not ok:
                return False, err
            py = str(venv_dir / "bin" / "python")
            return _run_cmd([uv, "pip", "install", "--quiet", "--python", py, "-r", str(reqs)],
                            base, env, timeout)
        sys_py = shutil.which("python3", path=lc.UNIT_PATH)
        if not sys_py:
            return False, "neither uv nor python3 is available"
        ok, err = _run_cmd([sys_py, "-m", "venv", str(venv_dir)], base, env, timeout)
        if not ok:
            return False, err
        return _run_cmd([str(venv_dir / "bin" / "pip"), "install", "--quiet", "-r", str(reqs)],
                        base, env, timeout)

    return False, f"unknown rebuild recipe {kind!r}"


def repair(
    pid: str,
    folder: str | Path,
    run_cmd: str,
    *,
    force: bool = False,
    cooldown_s: float = DEFAULT_REPAIR_COOLDOWN_S,
    timeout: float = DEFAULT_REBUILD_TIMEOUT_S,
) -> dict:
    """Repair a plugin's broken or missing Python venv if possible.

    Healthy environments return immediately without touching disk or taking a lock.
    """
    root = Path(folder)
    info = inspect(root, run_cmd)
    if info["ok"]:
        return {"ok": True, "repaired": False, "info": info}
    if not info.get("repairable"):
        return {"ok": False, "repaired": False, "reason": _full_reason(info), "info": info}

    with _plugin_lock(pid):
        info = inspect(root, run_cmd)
        if info["ok"]:
            return {"ok": True, "repaired": False, "info": info}
        if not info.get("repairable"):
            return {"ok": False, "repaired": False, "reason": _full_reason(info), "info": info}

        parsed = _parse_run(root, run_cmd)
        venv_dir: Path = parsed["venv_dir"]
        recipe = _find_python_recipe(root, venv_dir)
        if not recipe:
            return {"ok": False, "repaired": False, "reason": _full_reason(info), "info": info}

        sig = _repair_signature(root, venv_dir, recipe)
        prev = last_repair_record(pid)
        if (not force and prev and prev.get("ok") is False
                and prev.get("signature") == sig
                and time.time() - float(prev.get("at") or 0) < cooldown_s):
            err = str(prev.get("error") or "previous rebuild failed")
            return {
                "ok": False,
                "repaired": False,
                "skipped_cooldown": True,
                "error": err,
                "reason": _full_reason(info, err),
                "info": info,
            }

        backup: Path | None = None
        if venv_dir.exists() or venv_dir.is_symlink():
            backup = venv_dir.parent / f".{venv_dir.name}.loam-old-{os.getpid()}"
            if backup.exists() or backup.is_symlink():
                if backup.is_symlink() or backup.is_file():
                    backup.unlink()
                else:
                    shutil.rmtree(backup, ignore_errors=True)
            os.rename(venv_dir, backup)

        try:
            ok, err = _run_python_rebuild(venv_dir, recipe, timeout=timeout)
            _PROBE_CACHE.clear()
            after = inspect(root, run_cmd) if ok else None
            if ok and after and after["ok"]:
                if backup is not None:
                    if backup.is_symlink() or backup.is_file():
                        backup.unlink(missing_ok=True)
                    else:
                        shutil.rmtree(backup, ignore_errors=True)
                _write_repair_record(pid, {
                    "at": time.time(),
                    "ok": True,
                    "error": None,
                    "recipe": recipe["kind"],
                    "signature": sig,
                })
                return {
                    "ok": True,
                    "repaired": True,
                    "recipe": recipe["kind"],
                    "before": info["reason"],
                    "info": after,
                }
            fail_msg = err or (after.get("reason") if after else "rebuilt environment failed verification")
        except BaseException as exc:  # noqa: BLE001
            fail_msg = f"{type(exc).__name__}: {exc}"[:300]

        # Roll back partial rebuild and restore prior venv so nothing is lost.
        if venv_dir.exists() or venv_dir.is_symlink():
            if venv_dir.is_symlink() or venv_dir.is_file():
                venv_dir.unlink(missing_ok=True)
            else:
                shutil.rmtree(venv_dir, ignore_errors=True)
        if backup is not None and (backup.exists() or backup.is_symlink()):
            os.rename(backup, venv_dir)

        _write_repair_record(pid, {
            "at": time.time(),
            "ok": False,
            "error": fail_msg,
            "recipe": recipe["kind"],
            "signature": sig,
        })
        return {
            "ok": False,
            "repaired": False,
            "error": fail_msg,
            "reason": _full_reason(info, fail_msg),
            "info": info,
        }


def ensure_ready(
    pid: str,
    folder: str | Path,
    run_cmd: str,
    *,
    force_retry: bool = False,
    cooldown_s: float = START_REPAIR_COOLDOWN_S,
    timeout: float = DEFAULT_REBUILD_TIMEOUT_S,
) -> dict:
    """Verify a plugin's server environment and repair it when needed."""
    return repair(
        pid, folder, run_cmd,
        force=force_retry, cooldown_s=cooldown_s, timeout=timeout,
    )
