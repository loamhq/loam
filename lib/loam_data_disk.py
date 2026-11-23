"""Optional separate data disk for a Loam install (`install.sh --data-dir DIR`).

A fresh install without `--data-dir` behaves identically to before and never
uses this module's mounts. With `--data-dir DIR` pointing at an existing,
mounted filesystem, every piece of the person's data lives under `DIR` while
Loam's runtime, provider CLIs, caches, generated units, build scratch and
engineer worktrees stay on the server's local disk.

Ordinary paths (`/home/<user>/...`, `/etc/loam/...`) are preserved through
bind mounts (recorded in `/etc/fstab` under a managed block):

- `<DIR>/etc` -> `/etc/loam`
- `<DIR>/home` -> `/home/<user>`
- 7 server-local overlays under `/var/lib/loam/local/` mounted back on top
  inside `/home/<user>` (`loam`, `.local/bin`, `.local/share`, `.cache`,
  `.config/systemd/user`, `.config/environment.d`, `.local/state/loam/worktrees`)
- `<DIR>/login/authelia-data` -> Docker `local` bind volume `authelia-data`

Moving the disk to a second fresh server and running `install.sh --data-dir DIR`
adopts the existing data without overwriting it, preserving the login, agents,
threads, vault, plugins, settings and file-based provider sign-ins.

Stdlib only; never imports `loam_config` at module level (step 1 runs before
`/etc/loam/loam.toml` exists).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

MARKER = "loam-data.json"
FORMAT_VERSION = 1
LOCAL_BASE = Path("/var/lib/loam/local")
FSTAB = Path("/etc/fstab")
FSTAB_BEGIN = "# >>> loam:data-disk (managed by Loam; do not edit)"
FSTAB_END = "# <<< loam:data-disk"
MIN_FREE_GB_FRESH = 5.0
MIN_FREE_GB_ADOPT = 1.0
IGNORED_ENTRIES = frozenset({"lost+found"})

# Server-local directories bind-mounted on top inside `/home/<user>` so heavy
# I/O, Loam's own checkout, pinned provider CLIs, caches and regenerated units
# stay on the server's local disk rather than the person's data disk.
# Each entry is (subdir under LOCAL_BASE, relative path under HOME, mode).
LOCAL_OVERLAYS: tuple[tuple[str, str, int], ...] = (
    ("loam", "loam", 0o755),
    ("bin", ".local/bin", 0o755),
    ("share", ".local/share", 0o755),
    ("cache", ".cache", 0o755),
    ("systemd-user", ".config/systemd/user", 0o755),
    ("environment.d", ".config/environment.d", 0o755),
    ("worktrees", ".local/state/loam/worktrees", 0o755),
)

_FORBIDDEN_EXACT = frozenset({"/", "/etc", "/home", "/var", "/var/lib",
                              "/usr", "/bin", "/sbin", "/lib", "/lib64",
                              "/boot", "/root"})
_FORBIDDEN_TREES = ("/proc", "/sys", "/dev", "/run")
_OCTAL_ESCAPE = re.compile(r"\\([0-7]{3})")
_SECTION_RE = re.compile(r"^\s*\[([^\[\]]+)\]\s*(?:#.*)?$")


class DataDiskError(Exception):
    """A data-disk check or mount operation could not proceed."""


@dataclass(frozen=True)
class MountSpec:
    source: Path
    target: Path
    role: str            # "data" | "local"
    requires_mount: Path
    label: str


# ---------------------------------------------------------------------------
# Path and filesystem checks
# ---------------------------------------------------------------------------

def is_separate_mount(path: Path, root: Path = Path("/")) -> bool:
    """True when `path` lives on a different filesystem (device) than `root`."""
    try:
        return os.stat(path).st_dev != os.stat(root).st_dev
    except OSError:
        return False


def is_read_only(path: Path) -> bool:
    """True when `path`'s filesystem is mounted read-only."""
    try:
        flag = os.statvfs(path).f_flag
        return bool(flag & getattr(os, "ST_RDONLY", 1))
    except OSError:
        return False


def _overlaps(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def validate_path(raw: str | Path, *, home: Path = Path("/home/loam"),
                  etc: Path = Path("/etc/loam"), local_base: Path = LOCAL_BASE,
                  require_separate_mount: bool = True,
                  root: Path = Path("/")) -> tuple[Path | None, str | None]:
    """Validate a `--data-dir` path. Returns `(Path, None)` or `(None, reason)`."""
    text = str(raw or "").strip()
    if not text:
        return None, "a path is required for --data-dir"
    if any(ch.isspace() or ord(ch) < 32 for ch in text):
        return None, "the data directory path may not contain spaces or control characters"
    if not text.startswith("/"):
        return None, f"{text!r} must be an absolute path (starting with /)"
    raw_path = Path(text)
    if ".." in raw_path.parts:
        return None, f"{text!r} must not contain '..'"
    norm = Path(os.path.normpath(text))
    s = str(norm)
    if s in _FORBIDDEN_EXACT or any(s == t or s.startswith(t + "/") for t in _FORBIDDEN_TREES):
        return None, f"{norm} cannot be used as a data directory"
    for target in (etc, home, local_base):
        if _overlaps(norm, target):
            return None, f"{norm} overlaps Loam's own path {target}"
    if not norm.exists():
        return None, f"{norm} does not exist (mount the data disk there first)"
    if norm.is_symlink():
        return None, f"{norm} is a symbolic link; pass the real mounted directory"
    if not norm.is_dir():
        return None, f"{norm} is not a directory"
    if require_separate_mount and not is_separate_mount(norm, root=root):
        return None, f"{norm} is on the server's root disk, not on a mounted separate disk"
    if is_read_only(norm):
        return None, f"{norm} is mounted read-only"
    return norm, None


def login_data_dir(data_dir: Path) -> Path:
    return data_dir / "login" / "authelia-data"


def marker_path(data_dir: Path) -> Path:
    return data_dir / MARKER


def read_marker(data_dir: Path) -> dict | None:
    path = marker_path(data_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_marker(data_dir: Path, **fields) -> dict:
    """Create or update `<data_dir>/loam-data.json` atomically."""
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    data = read_marker(data_dir) or {"version": FORMAT_VERSION, "created_at": now}
    data.setdefault("version", FORMAT_VERSION)
    data.setdefault("created_at", now)
    for key, value in fields.items():
        if value is not None:
            data[key] = value
    path = marker_path(data_dir)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    return data


def classify(data_dir: Path) -> dict:
    """Classify `data_dir` as `fresh`, `existing` (Loam disk to adopt), or
    `unrecognized` (non-empty unknown or damaged directory)."""
    try:
        entries = sorted(p.name for p in data_dir.iterdir() if p.name not in IGNORED_ENTRIES)
    except OSError as exc:
        return {"kind": "unrecognized", "marker": None, "entries": [],
                "reason": f"could not list {data_dir}: {exc}"}
    if not entries:
        return {"kind": "fresh", "marker": None, "entries": [], "reason": "empty data directory"}
    mpath = marker_path(data_dir)
    if not mpath.exists():
        sample = ", ".join(entries[:5]) + ("..." if len(entries) > 5 else "")
        return {"kind": "unrecognized", "marker": None, "entries": entries,
                "reason": f"contains {sample} and no {MARKER}"}
    marker = read_marker(data_dir)
    if marker is None:
        return {"kind": "unrecognized", "marker": None, "entries": entries,
                "reason": f"{MARKER} could not be read"}
    ver = marker.get("version")
    if not isinstance(ver, int) or ver < 1 or ver > FORMAT_VERSION:
        return {"kind": "unrecognized", "marker": marker, "entries": entries,
                "reason": f"unsupported data disk format version {ver!r}"}
    cfg_file = data_dir / "etc" / "loam.toml"
    home_dir = data_dir / "home"
    if not cfg_file.is_file():
        return {"kind": "unrecognized", "marker": marker, "entries": entries,
                "reason": f"{MARKER} is present, but etc/loam.toml is missing"}
    if not home_dir.is_dir():
        return {"kind": "unrecognized", "marker": marker, "entries": entries,
                "reason": f"{MARKER} is present, but home/ is missing"}
    try:
        tomllib.loads(cfg_file.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return {"kind": "unrecognized", "marker": marker, "entries": entries,
                "reason": f"etc/loam.toml is unreadable: {exc}"}
    return {"kind": "existing", "marker": marker, "entries": entries,
            "reason": "existing Loam data disk"}


# ---------------------------------------------------------------------------
# Mount specifications, mount table inspection, bind mounting, and /etc/fstab
# ---------------------------------------------------------------------------

def etc_spec(data_dir: Path, etc: Path = Path("/etc/loam")) -> MountSpec:
    return MountSpec(source=data_dir / "etc", target=etc, role="data",
                     requires_mount=data_dir, label="Loam configuration and edge metadata")


def mount_specs(data_dir: Path, home: Path, *, etc: Path = Path("/etc/loam"),
                local_base: Path = LOCAL_BASE) -> list[MountSpec]:
    specs = [
        etc_spec(data_dir, etc),
        MountSpec(source=data_dir / "home", target=home, role="data",
                  requires_mount=data_dir, label="Loam user home (person's data)"),
    ]
    for sub, rel, _mode in LOCAL_OVERLAYS:
        specs.append(MountSpec(
            source=local_base / sub,
            target=home / rel,
            role="local",
            requires_mount=home,
            label=f"server-local {rel}",
        ))
    return specs


def _unescape_mountinfo(raw: str) -> str:
    return _OCTAL_ESCAPE.sub(lambda m: chr(int(m.group(1), 8)), raw)


def mount_targets(mountinfo_path: Path = Path("/proc/self/mountinfo")) -> set[str]:
    """Every active mount target path from `/proc/self/mountinfo`."""
    out: set[str] = set()
    try:
        lines = mountinfo_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        parts = line.split(" ")
        if len(parts) >= 5:
            out.add(_unescape_mountinfo(parts[4]))
    return out


def same_inode(a: Path, b: Path) -> bool:
    try:
        sa, sb = os.stat(a), os.stat(b)
    except OSError:
        return False
    return sa.st_dev == sb.st_dev and sa.st_ino == sb.st_ino


def is_bind_mounted(source: Path, target: Path, *,
                    mountinfo_path: Path = Path("/proc/self/mountinfo")) -> bool:
    """True when `target` is an active mount point whose inode matches `source`."""
    t_str = os.path.normpath(str(target))
    return t_str in mount_targets(mountinfo_path) and same_inode(source, target)


def ensure_bind_mount(source: Path, target: Path, *,
                      mountinfo_path: Path = Path("/proc/self/mountinfo"),
                      run=None) -> bool:
    """Ensure `source` is bind-mounted at `target`. Returns True if newly mounted,
    False if already active. Raises DataDiskError on conflicting mount or failure."""
    if not source.is_dir():
        raise DataDiskError(f"bind-mount source {source} does not exist")
    t_str = os.path.normpath(str(target))
    targets = mount_targets(mountinfo_path)
    if t_str in targets:
        if same_inode(source, target):
            return False
        raise DataDiskError(
            f"{target} is already a mount point, but does not point at {source}")
    target.mkdir(parents=True, exist_ok=True)
    runner = run or (lambda argv: subprocess.run(argv, capture_output=True, text=True, check=False))
    res = runner(["mount", "--bind", str(source), str(target)])
    if res.returncode != 0:
        err = (res.stderr or res.stdout or "").strip()
        raise DataDiskError(f"could not bind-mount {source} onto {target}: {err}")
    if run is None and not is_bind_mounted(source, target, mountinfo_path=mountinfo_path):
        raise DataDiskError(f"bind-mount {source} -> {target} did not appear in mount table")
    return True


def ensure_owned_path(base: Path, rel: str, uid: int, gid: int, mode: int = 0o755) -> Path:
    """Create `base / rel` walking component by component so any newly created
    directory under `base` is owned by `uid:gid` instead of root."""
    cur = base
    parts = Path(rel).parts
    for i, part in enumerate(parts):
        cur = cur / part
        if not cur.exists():
            cur.mkdir(mode=mode if i == len(parts) - 1 else 0o755)
            os.chown(cur, uid, gid)
            os.chmod(cur, mode if i == len(parts) - 1 else 0o755)
        elif cur.is_dir():
            st = cur.stat()
            if st.st_uid == 0 and uid != 0:
                os.chown(cur, uid, gid)
    return cur


def render_fstab_block(specs: list[MountSpec]) -> str:
    lines = [FSTAB_BEGIN]
    for spec in specs:
        opts = f"bind,nofail,x-systemd.requires-mounts-for={spec.requires_mount}"
        lines.append(f"{spec.source} {spec.target} none {opts} 0 0")
    lines.append(FSTAB_END)
    return "\n".join(lines) + "\n"


def replace_fstab_block(existing: str, block: str) -> str:
    """Replace the managed `loam:data-disk` block in `existing`, or append it."""
    lines = existing.splitlines(keepends=True)
    start = next((i for i, ln in enumerate(lines) if ln.strip() == FSTAB_BEGIN), None)
    if start is not None:
        end = next((i for i in range(start + 1, len(lines)) if ln_strip(lines[i]) == FSTAB_END), None)
        if end is None:
            raise DataDiskError(f"unterminated {FSTAB_BEGIN} block in fstab")
        return "".join(lines[:start]) + block + "".join(lines[end + 1:])
    if not existing:
        return block
    sep = "" if existing.endswith("\n") else "\n"
    if not existing.endswith("\n\n"):
        sep += "\n"
    return existing + sep + block


def ln_strip(line: str) -> str:
    return line.strip()


def update_fstab(specs: list[MountSpec], *, fstab: Path = FSTAB) -> bool:
    """Write or update the managed block in `/etc/fstab` atomically. Returns
    True when the file changed."""
    existing = fstab.read_text(encoding="utf-8") if fstab.exists() else ""
    block = render_fstab_block(specs)
    updated = replace_fstab_block(existing, block)
    if updated == existing:
        return False
    fstab.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".fstab.loam-", dir=str(fstab.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(updated)
        os.chmod(tmp, 0o644)
        os.replace(tmp, fstab)
    finally:
        tmp.unlink(missing_ok=True)
    return True


# ---------------------------------------------------------------------------
# Updating loam.toml on adoption (preserving comments and owner settings)
# ---------------------------------------------------------------------------

def set_toml_string(text: str, section: str, key: str, value: str) -> str:
    """Set `key = "<value>"` inside `[section]` of TOML `text`, preserving
    comments, ordering and all other sections and keys."""
    rendered = f"{key} = {json.dumps(value, ensure_ascii=False)}"
    key_re = re.compile(rf"^(\s*){re.escape(key)}\s*=.*$")
    lines = text.splitlines()
    sec_start: int | None = None
    sec_end: int | None = None
    for idx, line in enumerate(lines):
        m = _SECTION_RE.match(line)
        if m:
            name = m.group(1).strip()
            if sec_start is not None:
                sec_end = idx
                break
            if name == section:
                sec_start = idx
    if sec_start is None:
        out = text
        if out and not out.endswith("\n"):
            out += "\n"
        if out and not out.endswith("\n\n"):
            out += "\n"
        return f"{out}[{section}]\n{rendered}\n"
    if sec_end is None:
        sec_end = len(lines)
    for idx in range(sec_start + 1, sec_end):
        stripped = lines[idx].lstrip()
        if stripped.startswith("#"):
            continue
        m = key_re.match(lines[idx])
        if m:
            lines[idx] = f"{m.group(1)}{rendered}"
            return "\n".join(lines) + "\n"
    insert_at = sec_end
    while insert_at > sec_start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines.insert(insert_at, rendered)
    return "\n".join(lines) + "\n"


def adopt_config_text(text: str, *, address: str, data_dir: str,
                      resolver: str | None = None, tls: str | None = None) -> str:
    """Update only the machine-bound fields in an adopted `loam.toml`."""
    updated = set_toml_string(text, "edge", "address", address)
    if resolver is not None:
        updated = set_toml_string(updated, "edge", "resolver", resolver)
    if tls is not None:
        updated = set_toml_string(updated, "edge", "tls", tls)
    updated = set_toml_string(updated, "storage", "data_dir", data_dir)
    parsed = tomllib.loads(updated)
    if parsed.get("edge", {}).get("address") != address:
        raise DataDiskError("adopted loam.toml failed edge.address check")
    if parsed.get("storage", {}).get("data_dir") != data_dir:
        raise DataDiskError("adopted loam.toml failed storage.data_dir check")
    return updated


# ---------------------------------------------------------------------------
# Docker bind-backed login volume inspection
# ---------------------------------------------------------------------------

def docker_volume_info(volume: str = "authelia-data", *, docker: str | None = None,
                       use_sudo: bool = False, run=None) -> dict | None:
    """Return `docker volume inspect` JSON dict for `volume`, or None if absent
    or Docker is unreachable. Also handles plain-path test mocks."""
    exe = docker or shutil.which("docker")
    if not exe:
        return None
    argv = [exe, "volume", "inspect", "--format", "{{json .}}", volume]
    if use_sudo and os.geteuid() != 0 and run is None:
        argv = ["sudo", "-n", *argv]
    runner = run or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True,
                                                check=False, timeout=30))
    try:
        res = runner(argv)
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    out = (res.stdout or "").strip()
    if not out:
        return None
    if out.startswith(("{", "[")):
        try:
            parsed = json.loads(out)
        except ValueError:
            return None
        if isinstance(parsed, list):
            return parsed[0] if parsed and isinstance(parsed[0], dict) else None
        return parsed if isinstance(parsed, dict) else None
    return {"Mountpoint": out, "Options": {}}


def docker_volume_path(volume: str = "authelia-data", *, docker: str | None = None,
                       use_sudo: bool = False, run=None) -> Path | None:
    """Host path holding `volume`'s files: prefers `Options.device` (for a
    `local` bind volume backed by `--data-dir`), falling back to `Mountpoint`."""
    info = docker_volume_info(volume, docker=docker, use_sudo=use_sudo, run=run)
    if not info:
        return None
    opts = info.get("Options")
    if isinstance(opts, dict):
        device = (opts.get("device") or "").strip()
        if device:
            return Path(device)
    mountpoint = (info.get("Mountpoint") or "").strip()
    return Path(mountpoint) if mountpoint else None


# ---------------------------------------------------------------------------
# Drift and health checks
# ---------------------------------------------------------------------------

def drift_findings(data_dir: Path, home: Path, *, etc: Path = Path("/etc/loam"),
                   local_base: Path = LOCAL_BASE, fstab: Path = FSTAB,
                   mountinfo_path: Path = Path("/proc/self/mountinfo"),
                   check_docker: bool = True, docker_run=None,
                   root: Path = Path("/")) -> list[tuple[str, str, str]]:
    """Verify data-disk mount, marker, all bind mounts, `/etc/fstab` block, and
    the bind-backed `authelia-data` Docker volume. Returns `(label, where, detail)`
    tuples in `bin/check-drift` format."""
    findings: list[tuple[str, str, str]] = []
    if not data_dir.is_dir():
        findings.append(("data disk", str(data_dir),
                         "configured storage.data_dir does not exist or is not a directory"))
        return findings
    if not is_separate_mount(data_dir, root=root):
        findings.append(("data disk", str(data_dir),
                         "is on the same filesystem as /; expected a separate mounted data disk"))
    if read_marker(data_dir) is None:
        findings.append(("data disk marker", str(marker_path(data_dir)),
                         f"missing or unreadable {MARKER}"))

    specs = mount_specs(data_dir, home, etc=etc, local_base=local_base)
    for spec in specs:
        if not is_bind_mounted(spec.source, spec.target, mountinfo_path=mountinfo_path):
            findings.append((
                f"data disk mount {spec.target}",
                str(spec.target),
                f"expected active bind mount from {spec.source} ({spec.label})",
            ))

    want_block = render_fstab_block(specs).strip()
    try:
        fstab_text = fstab.read_text(encoding="utf-8")
    except OSError as exc:
        findings.append(("data disk fstab", str(fstab), f"could not read {fstab}: {exc}"))
    else:
        if want_block not in fstab_text:
            findings.append((
                "data disk fstab",
                str(fstab),
                "missing or out-of-date loam:data-disk managed mount block",
            ))

    if check_docker:
        info = docker_volume_info("authelia-data", use_sudo=True, run=docker_run)
        if info is not None:
            want_login = login_data_dir(data_dir).resolve()
            dev = ((info.get("Options") or {}).get("device") or "").strip()
            if not dev:
                findings.append((
                    "data disk login volume",
                    "docker volume authelia-data",
                    f"is not bind-backed to {want_login} (Options.device is empty; "
                    "login database lives on server disk)",
                ))
            elif Path(dev).resolve() != want_login:
                findings.append((
                    "data disk login volume",
                    "docker volume authelia-data",
                    f"points at {dev}, expected {want_login}",
                ))
    return findings
