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
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

MARKER = "loam-data.json"
FORMAT_VERSION = 1
LOCAL_BASE = Path("/var/lib/loam/local")
FSTAB = Path("/etc/fstab")
FSTAB_BEGIN = "# >>> loam:data-disk (managed by Loam; do not edit)"
FSTAB_END = "# <<< loam:data-disk"
SYSTEMD_DIR = Path("/etc/systemd/system")
GATE_UNIT_NAME = "loam-data-disk.service"
DROPIN_NAME = "10-loam-data-disk.conf"
MIN_FREE_GB_FRESH = 5.0
MIN_FREE_GB_ADOPT = 1.0
IGNORED_ENTRIES = frozenset({"lost+found"})

# System units that read `/etc/loam`, `/home/<user>`, `<data_dir>` or
# `/home/<user>/loam` and therefore must wait for `loam-data-disk.service`
# (and never start against empty rootfs directories when the volume is absent).
# `user@<uid>.service` is added dynamically per install's UID so lingering never
# starts an empty user manager before the bind mounts and overlays are active.
# `loam-inbound-firewall.service` carries its dependency directly in its
# rendered unit (`lib/loam_deploy.inbound_firewall_unit`) so it does not block
# `network-pre.target` / SSH when the volume is detached at boot.
ORDERED_UNITS: tuple[str, ...] = (
    "docker.service",
    "caddy.service",
    "loam-app@.service",
    "loam-claim.service",
    "loam-user-manager.service",
    "loam-swap.service",
    "loam-browser-proxy.service",
    "loam-docker-idle.service",
    "loam-await-dns.service",
    "loam-host-history.service",
)

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
# Persistent mount check and systemd boot ordering (cloud-agnostic volume seam)
# ---------------------------------------------------------------------------

def fstab_has_mount(target: Path, *, fstab: Path = FSTAB) -> bool:
    """True when `fstab` has a non-comment mount entry whose target equals
    `target` (ignoring Loam's own managed bind-mount block)."""
    want = os.path.normpath(str(target))
    try:
        lines = fstab.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    in_managed = False
    for line in lines:
        stripped = line.strip()
        if stripped == FSTAB_BEGIN:
            in_managed = True
            continue
        if stripped == FSTAB_END:
            in_managed = False
            continue
        if in_managed or not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) >= 2 and os.path.normpath(_unescape_mountinfo(parts[1])) == want:
            return True
    return False


def systemd_escape_mount(target: Path) -> str:
    """Convert an absolute mount path to its systemd `.mount` unit filename
    (matching `systemd-escape --path --suffix=mount`)."""
    norm = os.path.normpath(str(target)).strip("/")
    if not norm:
        return "-.mount"
    out: list[str] = []
    for idx, ch in enumerate(norm):
        if ch == "/":
            out.append("-")
        elif ch.isascii() and (ch.isalnum() or ch in ":_"):
            out.append(ch)
        elif ch == "." and idx > 0:
            out.append(ch)
        else:
            for b in ch.encode("utf-8"):
                out.append(f"\\x{b:02x}")
    return "".join(out) + ".mount"


def has_persistent_mount(data_dir: Path, *, fstab: Path = FSTAB,
                         systemd_dir: Path = SYSTEMD_DIR) -> bool:
    """True when `data_dir` itself will be mounted at boot via `/etc/fstab` or
    a systemd `.mount` unit — independent of which cloud provider backs the
    block volume."""
    if fstab_has_mount(data_dir, fstab=fstab):
        return True
    unit = systemd_dir / systemd_escape_mount(data_dir)
    return unit.is_file()


def ordered_unit_names(uid: int | None = None) -> list[str]:
    names = list(ORDERED_UNITS)
    if uid is not None:
        names.append(f"user@{int(uid)}.service")
    return names


def render_gate_unit(data_dir: Path, home: Path, *, etc: Path = Path("/etc/loam"),
                     local_base: Path = LOCAL_BASE,
                     pycache: str = "/var/cache/loam/pycache") -> str:
    """Render `/etc/systemd/system/loam-data-disk.service`.

    Cloud volumes and Loam's bind mounts carry `nofail` in `/etc/fstab` so a
    missing volume never drops the server into emergency mode and locks out
    SSH. Because `nofail` removes `Before=local-fs.target`, Loam's services
    would otherwise race ahead of the volume and bind mounts (or start against
    empty rootfs directories when the volume is detached). This gate unit
    depends on every mount via `RequiresMountsFor=` and runs from the server's
    local disk (`<local_base>/loam/bin/loam-data-disk`), holding Loam services
    until the data disk and all nine bind mounts are verified active.
    """
    specs = mount_specs(data_dir, home, etc=etc, local_base=local_base)
    req_mounts = " ".join([str(data_dir), *(str(s.target) for s in specs)])
    helper = local_base / "loam" / "bin" / "loam-data-disk"
    return (
        "# GENERATED by Loam (lib/loam_data_disk.py). Do not edit.\n"
        "[Unit]\n"
        "Description=Loam: wait for the data disk and its bind mounts\n"
        "DefaultDependencies=no\n"
        "After=local-fs.target\n"
        f"RequiresMountsFor={req_mounts}\n"
        "Before=shutdown.target\n"
        "Conflicts=shutdown.target\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        "RemainAfterExit=yes\n"
        f"Environment=PYTHONPYCACHEPREFIX={pycache}\n"
        f"ExecStart=/usr/bin/python3 {helper} ready --data-dir {data_dir} "
        f"--home {home} --etc {etc} --local-base {local_base}\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def render_ordering_dropin() -> str:
    return (
        "# GENERATED by Loam (lib/loam_data_disk.py). Do not edit.\n"
        "[Unit]\n"
        f"Requires={GATE_UNIT_NAME}\n"
        f"After={GATE_UNIT_NAME}\n"
    )


def boot_ordering_files(data_dir: Path, home: Path, *, uid: int | None = None,
                        etc: Path = Path("/etc/loam"),
                        local_base: Path = LOCAL_BASE,
                        systemd_dir: Path = SYSTEMD_DIR) -> dict[Path, str]:
    """Every systemd unit and drop-in Loam installs for `--data-dir` boot
    ordering."""
    files: dict[Path, str] = {
        systemd_dir / GATE_UNIT_NAME: render_gate_unit(
            data_dir, home, etc=etc, local_base=local_base),
    }
    dropin = render_ordering_dropin()
    for unit in ordered_unit_names(uid):
        files[systemd_dir / f"{unit}.d" / DROPIN_NAME] = dropin
    return files


def _write_atomic(path: Path, content: str, mode: int = 0o644) -> bool:
    if path.is_file():
        try:
            if path.read_text(encoding="utf-8") == content:
                return False
        except OSError:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.loam-", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return True


def install_boot_ordering(data_dir: Path, home: Path, *, uid: int | None = None,
                          etc: Path = Path("/etc/loam"),
                          local_base: Path = LOCAL_BASE,
                          systemd_dir: Path = SYSTEMD_DIR,
                          run=None, enable: bool = True) -> list[Path]:
    """Install or update `loam-data-disk.service` and all ordering drop-ins.
    Returns the list of files written."""
    files = boot_ordering_files(
        data_dir, home, uid=uid, etc=etc, local_base=local_base,
        systemd_dir=systemd_dir)
    changed = [p for p, text in files.items() if _write_atomic(p, text, 0o644)]
    runner = run or (lambda argv: subprocess.run(argv, capture_output=True, text=True, check=False))
    if enable and (run is not None or (os.geteuid() == 0 and shutil.which("systemctl"))):
        if changed:
            res = runner(["systemctl", "daemon-reload"])
            if res.returncode != 0:
                raise DataDiskError(f"systemctl daemon-reload failed: {(res.stderr or res.stdout).strip()}")
        for action in ("enable", "start"):
            res = runner(["systemctl", action, GATE_UNIT_NAME])
            if res.returncode != 0:
                raise DataDiskError(
                    f"systemctl {action} {GATE_UNIT_NAME} failed: {(res.stderr or res.stdout).strip()}")
    return changed


def ensure_ready(data_dir: Path, home: Path, *, etc: Path = Path("/etc/loam"),
                 local_base: Path = LOCAL_BASE, wait_seconds: float = 0.0,
                 root: Path = Path("/"),
                 mountinfo_path: Path = Path("/proc/self/mountinfo"),
                 run=None) -> tuple[bool, str]:
    """Verify the data disk is mounted separately, valid, and all nine bind
    mounts are active (mounting any missing bind mounts once the data disk is
    mounted). Returns `(ok, message)`."""
    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    while True:
        if data_dir.is_dir() and is_separate_mount(data_dir, root=root):
            break
        if time.monotonic() >= deadline:
            return (
                False,
                f"{data_dir} is not mounted on a separate filesystem "
                "(volume not attached or not mounted yet); holding Loam services",
            )
        time.sleep(min(1.0, max(0.05, deadline - time.monotonic())))

    info = classify(data_dir)
    if info["kind"] != "existing":
        return False, f"{data_dir} is not a valid Loam data disk ({info['reason']})"

    specs = mount_specs(data_dir, home, etc=etc, local_base=local_base)
    mounted_now = 0
    try:
        for spec in specs:
            if ensure_bind_mount(spec.source, spec.target,
                                 mountinfo_path=mountinfo_path, run=run):
                mounted_now += 1
    except DataDiskError as exc:
        return False, str(exc)

    extra = f" (mounted {mounted_now})" if mounted_now else ""
    return True, f"data disk {data_dir} and all {len(specs)} bind mounts ready{extra}"


# ---------------------------------------------------------------------------
# Drift and health checks
# ---------------------------------------------------------------------------

def drift_findings(data_dir: Path, home: Path, *, etc: Path = Path("/etc/loam"),
                   local_base: Path = LOCAL_BASE, fstab: Path = FSTAB,
                   mountinfo_path: Path = Path("/proc/self/mountinfo"),
                   check_docker: bool = True, docker_run=None,
                   uid: int | None = None,
                   systemd_dir: Path = SYSTEMD_DIR,
                   check_systemd: bool = True,
                   systemd_run=None,
                   root: Path = Path("/")) -> list[tuple[str, str, str]]:
    """Verify data-disk mount, marker, all bind mounts, `/etc/fstab` block,
    persistent mount & systemd boot ordering, and the bind-backed
    `authelia-data` Docker volume. Returns `(label, where, detail)` tuples in
    `bin/check-drift` format."""
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

    if check_systemd:
        if not has_persistent_mount(data_dir, fstab=fstab, systemd_dir=systemd_dir):
            findings.append((
                "data disk persistence",
                str(fstab),
                f"no persistent mount entry for {data_dir} in {fstab} or {systemd_dir}; "
                "the volume will not mount automatically after a reboot",
            ))
        for path, expected in boot_ordering_files(
            data_dir, home, uid=uid, etc=etc, local_base=local_base,
            systemd_dir=systemd_dir,
        ).items():
            if not path.is_file():
                findings.append((
                    f"data disk boot unit {path.name}",
                    str(path),
                    "missing; run `sudo bin/loam-data-disk install` (or `sudo bin/gen-deploy`)",
                ))
            else:
                try:
                    actual = path.read_text(encoding="utf-8")
                except OSError as exc:
                    findings.append((f"data disk boot unit {path.name}", str(path), str(exc)))
                else:
                    if actual != expected:
                        findings.append((
                            f"data disk boot unit {path.name}",
                            str(path),
                            "differs from generator; run `sudo bin/loam-data-disk install`",
                        ))
        if systemd_run is not None or (systemd_dir == SYSTEMD_DIR and shutil.which("systemctl")):
            srunner = systemd_run or (lambda argv: subprocess.run(
                argv, capture_output=True, text=True, check=False, timeout=10))
            try:
                res = srunner(["systemctl", "is-enabled", GATE_UNIT_NAME])
            except (OSError, subprocess.SubprocessError):
                res = None
            if res is not None and (res.stdout or "").strip() != "enabled":
                state = (res.stdout or res.stderr or "disabled").strip()
                findings.append((
                    f"data disk boot unit {GATE_UNIT_NAME}",
                    f"systemctl is-enabled {GATE_UNIT_NAME}",
                    f"{state}; run `sudo systemctl enable {GATE_UNIT_NAME}`",
                ))

    if check_docker:
        docker_asleep = False
        if docker_run is None or getattr(docker_run, "__module__", "") == "loam_deploy":
            try:
                res = subprocess.run(
                    ["systemctl", "is-active", "docker.service"],
                    capture_output=True, text=True, check=False, timeout=10,
                )
                docker_asleep = res.returncode != 0 and res.stdout.strip() == "inactive"
            except (OSError, subprocess.SubprocessError):
                docker_asleep = False
        info = None if docker_asleep else docker_volume_info("authelia-data", use_sudo=True, run=docker_run)
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
