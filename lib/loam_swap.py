"""Manage automatic swap for Loam servers without disturbing operator swap.

Every Loam install needs swap so a sudden turn, browser or engineer-job spike
slows the server instead of triggering the OOM killer. On fresh installs and
updates, ``loam-swap`` ensures tiered swap sized for the server's RAM:

- **Primary tier: zram (priority 100)** — compressed RAM block device using
  ``zstd`` (falling back to ``lz4`` / ``lzo-rle``). Idle Node.js and Python
  heaps compress roughly 2.5-3.5x in microseconds with zero disk I/O and zero
  wear on metered or slow cloud volumes.
- **Overflow tier: local swapfile (priority 10)** at ``/var/lib/loam/swapfile``
  on the server's own local root disk (never on ``--data-dir``). Catches large
  or incompressible spikes that exceed zram, and serves as full fallback on
  minimal cloud kernels that omit ``zram.ko``.

If the operator already configured any swap (active in ``/proc/swaps`` or
listed in ``/etc/fstab`` outside Loam's own devices), ``loam-swap`` leaves it
and the host's ``vm.swappiness`` completely untouched.

Stdlib only. Tests: ``tests/test_swap.py``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import loam_limits as limits

SWAPFILE = Path("/var/lib/loam/swapfile")
STATE_FILE = Path("/var/lib/loam/swap.json")
SYSCTL_FILE = Path("/etc/sysctl.d/90-loam-swap.conf")
UNIT_NAME = "loam-swap.service"
UNIT_DIR = Path("/etc/systemd/system")
ROOT_PYCACHE = "/var/cache/loam/pycache"

ZRAM_LABEL = "loam-zram"
SWAPFILE_LABEL = "loam-swap"
ZRAM_PRIORITY = 100
SWAPFILE_PRIORITY = 10
SWAPPINESS = 20
PAGE_CLUSTER = 0
MIN_FREE_AFTER_SWAP_MB = 5120  # always leave at least 5 GiB free on /

ALGO_PREFERENCE = ("zstd", "lz4", "lzo-rle", "lzo")
UNSUPPORTED_SWAPFILE_FS = frozenset({
    "tmpfs", "ramfs", "overlay", "overlayfs", "nfs", "nfs4", "cifs",
    "smb3", "fuse", "fuseblk", "9p", "virtiofs",
})
CMD_ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LC_ALL": "C.UTF-8",
}


def plan(
    box_mb: int,
    *,
    root_free_mb: int | None = None,
    zram_available: bool = True,
) -> dict:
    """Compute target swap sizes (MiB) and sysctl parameters for ``box_mb`` RAM.

    Target total swap is ~1x RAM on 2-4 GB servers and capped at 4 GiB on 8 GB+:
    - 2 GB (<= 3,072 MB): 1,024 MB zram + 1,024 MB swapfile (or 2,048 MB swapfile)
    - 4 GB (<= 6,144 MB): 2,048 MB zram + 2,048 MB swapfile (or 4,096 MB swapfile)
    - 8 GB+ (> 6,144 MB): 2,048 MB zram + 2,048 MB swapfile (or 4,096 MB swapfile)
    """
    mb = max(512, int(box_mb))
    if mb <= 3072:
        zram_mb = 1024 if zram_available else 0
        file_mb = 1024 if zram_available else 2048
    else:
        zram_mb = 2048 if zram_available else 0
        file_mb = 2048 if zram_available else 4096

    if root_free_mb is not None and file_mb > 0:
        spare = int(root_free_mb) - MIN_FREE_AFTER_SWAP_MB
        if spare < file_mb:
            file_mb = max(0, (spare // 256) * 256)

    return {
        "box_mb": mb,
        "zram_mb": zram_mb,
        "swapfile_mb": file_mb,
        "total_mb": zram_mb + file_mb,
        "zram_priority": ZRAM_PRIORITY,
        "swapfile_priority": SWAPFILE_PRIORITY,
        "swappiness": SWAPPINESS,
        "page_cluster": PAGE_CLUSTER,
    }


def render_sysctl(swappiness: int = SWAPPINESS, page_cluster: int = PAGE_CLUSTER) -> str:
    return (
        "# Managed by Loam (bin/loam-swap). Only written when Loam manages swap.\n"
        f"vm.swappiness = {swappiness}\n"
        f"vm.page-cluster = {page_cluster}\n"
    )


def render_unit(root: Path, pycache: str = ROOT_PYCACHE) -> str:
    template = Path(root) / "systemd" / "system" / UNIT_NAME
    return (
        template.read_text(encoding="utf-8")
        .replace("@ROOT@", str(root))
        .replace("@PYCACHE@", pycache)
    )


def read_state(path: Path = STATE_FILE) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(data: dict, path: Path = STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def fstab_swaps(fstab_path: Path | str = "/etc/fstab") -> list[str]:
    """Return configured swap device/file paths from ``/etc/fstab``."""
    out: list[str] = []
    try:
        lines = Path(fstab_path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) >= 3 and fields[2] == "swap":
            out.append(fields[0])
    return out


def _device_label(dev: str, run_cmd=None) -> str | None:
    runner = run_cmd or _run
    res = runner(["blkid", "-s", "LABEL", "-o", "value", dev])
    if res.returncode == 0:
        return res.stdout.strip() or None
    return None


def is_loam_swap(
    name: str,
    *,
    state: dict | None = None,
    swapfile: Path = SWAPFILE,
    run_cmd=None,
) -> bool:
    """True when ``name`` is a swap device/file created and owned by Loam."""
    if name == str(swapfile):
        return True
    st = read_state() if state is None else state
    if st.get("swapfile") and name == str(st["swapfile"]):
        return True
    if st.get("zram_dev") and name == str(st["zram_dev"]):
        return True
    if name.startswith("/dev/zram") and _device_label(name, run_cmd=run_cmd) == ZRAM_LABEL:
        return True
    return False


def operator_swaps(
    *,
    swaps_path: Path | str = "/proc/swaps",
    fstab_path: Path | str = "/etc/fstab",
    state: dict | None = None,
    swapfile: Path = SWAPFILE,
    run_cmd=None,
) -> list[dict]:
    """Any active or fstab-configured swap that Loam did not create."""
    st = read_state() if state is None else state
    found: list[dict] = []
    seen: set[str] = set()
    for row in limits.read_swaps(swaps_path):
        name = row["name"]
        if not is_loam_swap(name, state=st, swapfile=swapfile, run_cmd=run_cmd):
            found.append({**row, "source": "active"})
            seen.add(name)
    for entry in fstab_swaps(fstab_path):
        if entry in seen:
            continue
        if not is_loam_swap(entry, state=st, swapfile=swapfile, run_cmd=run_cmd):
            found.append({"name": entry, "type": "fstab", "size_mb": None,
                          "used_mb": None, "priority": None, "is_zram": False,
                          "source": "fstab"})
            seen.add(entry)
    return found


def root_fstype(target: Path = SWAPFILE, mounts_path: Path | str = "/proc/mounts") -> str | None:
    """Filesystem type holding ``target`` (longest mountpoint match)."""
    try:
        resolved = str(target.parent.resolve())
    except OSError:
        resolved = str(target.parent)
    best_len = -1
    best_fs: str | None = None
    try:
        lines = Path(mounts_path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        mp = parts[1].replace("\\040", " ")
        fstype = parts[2]
        if resolved == mp or (mp == "/") or resolved.startswith(mp.rstrip("/") + "/"):
            if len(mp) > best_len:
                best_len = len(mp)
                best_fs = fstype
    return best_fs


def in_container() -> bool:
    """Best-effort check for container environments where swap cannot be managed."""
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return True
    try:
        res = _run(["systemd-detect-virt", "--container"])
        return res.returncode == 0
    except OSError:
        return False


def _run(argv: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, check=False,
            timeout=timeout, env=CMD_ENV,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(argv, 127, "", f"{argv[0]}: not found")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 124, "", f"{argv[0]}: timed out")


def pick_zram_algo(available_text: str) -> str | None:
    """Pick the best compression algorithm offered by ``comp_algorithm``."""
    tokens = [tok.strip("[]") for tok in available_text.split() if tok.strip()]
    for algo in ALGO_PREFERENCE:
        if algo in tokens:
            return algo
    return tokens[0] if tokens else None


def _ensure_zram(
    zram_mb: int,
    *,
    active: list[dict],
    state: dict,
    sys_block: Path = Path("/sys/block"),
    zram_control: Path = Path("/sys/class/zram-control/hot_add"),
    run_cmd=None,
) -> tuple[dict | None, str | None, bool]:
    """Ensure Loam's zram swap device is active. Returns ``(info, error, changed)``."""
    runner = run_cmd or _run
    # Already active?
    for row in active:
        if row["is_zram"] and is_loam_swap(row["name"], state=state, run_cmd=runner):
            dev_name = Path(row["name"]).name
            algo = None
            try:
                raw = (sys_block / dev_name / "comp_algorithm").read_text(encoding="utf-8")
                for tok in raw.split():
                    if tok.startswith("[") and tok.endswith("]"):
                        algo = tok.strip("[]")
                        break
            except OSError:
                pass
            return {
                "dev": row["name"], "size_mb": row["size_mb"],
                "priority": row["priority"], "algo": algo or state.get("zram_algo"),
            }, None, False

    mod = runner(["modprobe", "zram"])
    if mod.returncode != 0 and not (sys_block / "zram0").exists() and not zram_control.exists():
        return None, (mod.stderr or mod.stdout or "modprobe zram failed").strip(), False

    # Find an unused zram device (disksize == 0) or allocate one via hot_add.
    chosen: str | None = None
    active_names = {r["name"] for r in active}
    if sys_block.is_dir():
        for entry in sorted(sys_block.glob("zram*")):
            dev = f"/dev/{entry.name}"
            if dev in active_names:
                continue
            try:
                if int((entry / "disksize").read_text(encoding="utf-8").strip()) == 0:
                    chosen = entry.name
                    break
            except (OSError, ValueError):
                continue
    if chosen is None and zram_control.exists():
        try:
            idx = zram_control.read_text(encoding="utf-8").strip()
            if idx.isdigit():
                chosen = f"zram{idx}"
        except OSError as exc:
            return None, f"zram hot_add failed: {exc}", False
    if chosen is None:
        return None, "no free zram device available", False

    sys_dev = sys_block / chosen
    dev_path = f"/dev/{chosen}"
    algo: str | None = None
    try:
        alg_path = sys_dev / "comp_algorithm"
        if alg_path.exists():
            algo = pick_zram_algo(alg_path.read_text(encoding="utf-8"))
            if algo:
                alg_path.write_text(algo, encoding="utf-8")
        (sys_dev / "disksize").write_text(str(zram_mb * 1024 * 1024), encoding="utf-8")
    except OSError as exc:
        return None, f"configuring {dev_path} failed: {exc}", False

    mk = runner(["mkswap", "-L", ZRAM_LABEL, dev_path])
    if mk.returncode != 0:
        try:
            (sys_dev / "reset").write_text("1", encoding="utf-8")
        except OSError:
            pass
        return None, f"mkswap {dev_path}: {(mk.stderr or mk.stdout).strip()}", False

    on = runner(["swapon", "-p", str(ZRAM_PRIORITY), dev_path])
    if on.returncode != 0:
        try:
            (sys_dev / "reset").write_text("1", encoding="utf-8")
        except OSError:
            pass
        return None, f"swapon {dev_path}: {(on.stderr or on.stdout).strip()}", False

    return {
        "dev": dev_path, "size_mb": zram_mb,
        "priority": ZRAM_PRIORITY, "algo": algo,
    }, None, True


def _ensure_swapfile(
    swapfile_mb: int,
    *,
    active: list[dict],
    swapfile: Path = SWAPFILE,
    mounts_path: Path | str = "/proc/mounts",
    run_cmd=None,
) -> tuple[dict | None, str | None, bool]:
    """Ensure Loam's local disk swapfile is active. Returns ``(info, error, changed)``."""
    runner = run_cmd or _run
    for row in active:
        if row["name"] == str(swapfile):
            return {
                "path": str(swapfile), "size_mb": row["size_mb"],
                "priority": row["priority"],
            }, None, False

    if swapfile_mb <= 0:
        return None, "insufficient free disk on / for swapfile", False

    fs = root_fstype(swapfile, mounts_path=mounts_path)
    if fs in UNSUPPORTED_SWAPFILE_FS:
        return None, f"filesystem {fs!r} does not support a swapfile", False

    swapfile.parent.mkdir(parents=True, exist_ok=True)
    target_bytes = swapfile_mb * 1024 * 1024
    need_alloc = True
    try:
        st = swapfile.stat()
        if abs(st.st_size - target_bytes) <= 16 * 1024 * 1024:
            need_alloc = False
    except FileNotFoundError:
        pass

    changed = False
    used_fallocate = False
    if need_alloc:
        swapfile.unlink(missing_ok=True)
        if fs == "btrfs":
            # Btrfs swapfiles require NOCOW set on a zero-length file before writing.
            swapfile.touch(mode=0o600)
            runner(["chattr", "+C", str(swapfile)])
            alloc = runner(["dd", "if=/dev/zero", f"of={swapfile}", "bs=1M",
                            f"count={swapfile_mb}", "status=none"], timeout=300)
        else:
            alloc = runner(["fallocate", "-l", f"{swapfile_mb}M", str(swapfile)])
            if alloc.returncode == 0:
                used_fallocate = True
            else:
                alloc = runner(["dd", "if=/dev/zero", f"of={swapfile}", "bs=1M",
                                f"count={swapfile_mb}", "status=none"], timeout=300)
        if alloc.returncode != 0:
            swapfile.unlink(missing_ok=True)
            return None, f"allocating {swapfile}: {(alloc.stderr or alloc.stdout).strip()}", False
        os.chmod(swapfile, 0o600)
        mk = runner(["mkswap", "-L", SWAPFILE_LABEL, str(swapfile)])
        if mk.returncode != 0:
            swapfile.unlink(missing_ok=True)
            return None, f"mkswap {swapfile}: {(mk.stderr or mk.stdout).strip()}", False
        changed = True
    else:
        os.chmod(swapfile, 0o600)

    on = runner(["swapon", "-p", str(SWAPFILE_PRIORITY), str(swapfile)])
    if on.returncode != 0 and used_fallocate:
        # Some filesystems reject fallocated extents (holes) for swap; retry zero-filled.
        swapfile.unlink(missing_ok=True)
        alloc = runner(["dd", "if=/dev/zero", f"of={swapfile}", "bs=1M",
                        f"count={swapfile_mb}", "status=none"], timeout=300)
        if alloc.returncode == 0:
            os.chmod(swapfile, 0o600)
            if runner(["mkswap", "-L", SWAPFILE_LABEL, str(swapfile)]).returncode == 0:
                on = runner(["swapon", "-p", str(SWAPFILE_PRIORITY), str(swapfile)])
    elif on.returncode != 0 and not need_alloc:
        # Existing file may have lost its swap header; format once and retry.
        mk = runner(["mkswap", "-L", SWAPFILE_LABEL, str(swapfile)])
        if mk.returncode == 0:
            on = runner(["swapon", "-p", str(SWAPFILE_PRIORITY), str(swapfile)])
    if on.returncode != 0:
        err = (on.stderr or on.stdout).strip()
        if need_alloc:
            swapfile.unlink(missing_ok=True)
        return None, f"swapon {swapfile}: {err}", False

    return {
        "path": str(swapfile), "size_mb": swapfile_mb,
        "priority": SWAPFILE_PRIORITY,
    }, None, True


def ensure(
    *,
    meminfo_path: Path | str = "/proc/meminfo",
    swaps_path: Path | str = "/proc/swaps",
    fstab_path: Path | str = "/etc/fstab",
    mounts_path: Path | str = "/proc/mounts",
    swapfile: Path = SWAPFILE,
    state_file: Path = STATE_FILE,
    sysctl_file: Path = SYSCTL_FILE,
    sys_block: Path = Path("/sys/block"),
    zram_control: Path = Path("/sys/class/zram-control/hot_add"),
    root_free_mb: int | None = None,
    run_cmd=None,
) -> dict:
    """Idempotently ensure swap is active, leaving any operator swap untouched."""
    runner = run_cmd or _run
    state = read_state(state_file)
    op = operator_swaps(
        swaps_path=swaps_path, fstab_path=fstab_path,
        state=state, swapfile=swapfile, run_cmd=runner,
    )
    if op:
        return {
            "mode": "operator",
            "changed": False,
            "swaps": op,
            "message": (
                "existing operator swap left alone ("
                + ", ".join(
                    f"{s['name']} {s['size_mb']} MB" if s.get("size_mb") else s["name"]
                    for s in op
                )
                + ")"
            ),
        }

    box_mb = limits.box_memory_mb(meminfo_path)
    if root_free_mb is None:
        try:
            root_free_mb = shutil.disk_usage(swapfile.parent if swapfile.parent.exists() else "/").free // (1024 * 1024)
        except OSError:
            root_free_mb = None

    active = limits.read_swaps(swaps_path)
    target = plan(box_mb, root_free_mb=root_free_mb, zram_available=True)
    notes: list[str] = []
    changed = False

    zram_info = None
    if target["zram_mb"] > 0:
        zram_info, zram_err, z_changed = _ensure_zram(
            target["zram_mb"], active=active, state=state,
            sys_block=sys_block, zram_control=zram_control, run_cmd=runner,
        )
        changed = changed or z_changed
        if zram_err:
            notes.append(f"zram skipped: {zram_err}")
            target = plan(box_mb, root_free_mb=root_free_mb, zram_available=False)

    file_info = None
    if target["swapfile_mb"] > 0:
        file_info, file_err, f_changed = _ensure_swapfile(
            target["swapfile_mb"], active=active, swapfile=swapfile,
            mounts_path=mounts_path, run_cmd=runner,
        )
        changed = changed or f_changed
        if file_err:
            notes.append(f"swapfile skipped: {file_err}")

    if zram_info is None and file_info is None:
        return {
            "mode": "unavailable",
            "changed": False,
            "notes": notes,
            "message": "swap could not be enabled: " + ("; ".join(notes) or "unknown error"),
        }

    # Write and apply sysctl tuning only when Loam manages swap.
    wanted_sysctl = render_sysctl(target["swappiness"], target["page_cluster"])
    try:
        sysctl_file.parent.mkdir(parents=True, exist_ok=True)
        if not sysctl_file.exists() or sysctl_file.read_text(encoding="utf-8") != wanted_sysctl:
            sysctl_file.write_text(wanted_sysctl, encoding="utf-8")
            os.chmod(sysctl_file, 0o644)
            changed = True
        runner(["sysctl", "-p", str(sysctl_file)])
    except OSError as exc:
        notes.append(f"sysctl skipped: {exc}")

    new_state = {
        "managed_by": "loam",
        "box_mb": box_mb,
        "zram_dev": zram_info["dev"] if zram_info else None,
        "zram_mb": zram_info["size_mb"] if zram_info else 0,
        "zram_algo": zram_info.get("algo") if zram_info else None,
        "swapfile": file_info["path"] if file_info else None,
        "swapfile_mb": file_info["size_mb"] if file_info else 0,
        "swappiness": target["swappiness"],
        "page_cluster": target["page_cluster"],
    }
    if new_state != state:
        write_state(new_state, state_file)

    parts = []
    if zram_info:
        algo = f" ({zram_info['algo']})" if zram_info.get("algo") else ""
        parts.append(f"zram {zram_info['dev']} {zram_info['size_mb']} MB{algo} pri={zram_info['priority']}")
    if file_info:
        parts.append(f"swapfile {file_info['path']} {file_info['size_mb']} MB pri={file_info['priority']}")
    return {
        "mode": "loam",
        "changed": changed,
        "zram": zram_info,
        "swapfile": file_info,
        "total_mb": (zram_info["size_mb"] if zram_info else 0) + (file_info["size_mb"] if file_info else 0),
        "swappiness": target["swappiness"],
        "notes": notes,
        "message": "Loam swap active: " + ", ".join(parts),
    }


def install(
    root: Path,
    *,
    unit_dir: Path = UNIT_DIR,
    run_cmd=None,
    **ensure_kwargs,
) -> dict:
    """Ensure swap is active and, when Loam manages swap, install & enable
    ``loam-swap.service`` so it comes back automatically on every boot."""
    runner = run_cmd or _run
    res = ensure(run_cmd=runner, **ensure_kwargs)
    if res["mode"] != "loam":
        return res
    unit_path = unit_dir / UNIT_NAME
    wanted = render_unit(root)
    unit_changed = not unit_path.exists() or unit_path.read_text(encoding="utf-8") != wanted
    if unit_changed:
        unit_dir.mkdir(parents=True, exist_ok=True)
        unit_path.write_text(wanted, encoding="utf-8")
        os.chmod(unit_path, 0o644)
        runner(["systemctl", "daemon-reload"])
    runner(["systemctl", "enable", UNIT_NAME])
    res["unit_installed"] = str(unit_path)
    res["unit_changed"] = unit_changed
    return res


def status(
    *,
    meminfo_path: Path | str = "/proc/meminfo",
    swaps_path: Path | str = "/proc/swaps",
    fstab_path: Path | str = "/etc/fstab",
    state_file: Path = STATE_FILE,
    swapfile: Path = SWAPFILE,
    run_cmd=None,
) -> dict:
    """Read-only summary of current swap state."""
    runner = run_cmd or _run
    state = read_state(state_file)
    active = limits.read_swaps(swaps_path)
    op = operator_swaps(
        swaps_path=swaps_path, fstab_path=fstab_path,
        state=state, swapfile=swapfile, run_cmd=runner,
    )
    mi = limits.read_meminfo(meminfo_path)
    if op:
        mode = "operator"
    elif state.get("managed_by") == "loam" or any(
        is_loam_swap(r["name"], state=state, swapfile=swapfile, run_cmd=runner) for r in active
    ):
        mode = "loam"
    else:
        mode = "none"
    swappiness = None
    try:
        swappiness = int(Path("/proc/sys/vm/swappiness").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        pass
    return {
        "mode": mode,
        "box_mb": limits.box_memory_mb(meminfo_path),
        "swap_total_mb": mi.get("SwapTotal", 0),
        "swap_free_mb": mi.get("SwapFree", 0),
        "swap_used_mb": max(0, mi.get("SwapTotal", 0) - mi.get("SwapFree", 0)),
        "swappiness": swappiness,
        "active": active,
        "operator_swaps": op,
        "state": state or None,
    }


def drift_findings(
    root: Path,
    *,
    meminfo_path: Path | str = "/proc/meminfo",
    swaps_path: Path | str = "/proc/swaps",
    fstab_path: Path | str = "/etc/fstab",
    unit_dir: Path = UNIT_DIR,
    sysctl_file: Path = SYSCTL_FILE,
    state_file: Path = STATE_FILE,
    swapfile: Path = SWAPFILE,
    run_cmd=None,
) -> list[tuple[str, str, str]]:
    """Return ``check-drift`` findings for swap. Zero findings when operator
    swap is active or when Loam-managed swap matches its unit and is active."""
    runner = run_cmd or _run
    st = status(
        meminfo_path=meminfo_path, swaps_path=swaps_path, fstab_path=fstab_path,
        state_file=state_file, swapfile=swapfile, run_cmd=runner,
    )
    findings: list[tuple[str, str, str]] = []
    if st["mode"] == "operator":
        return findings
    if st["mode"] == "none":
        if not in_container():
            findings.append((
                "swap", "/proc/swaps",
                f"no swap is active on this server; a memory spike can trigger OOM kills. "
                f"`sudo {root}/bin/loam-swap install`",
            ))
        return findings

    # Loam-managed swap: verify active, unit matches & enabled, sysctl present.
    if st["swap_total_mb"] <= 0:
        findings.append((
            "swap active", "/proc/swaps",
            f"Loam swap is configured but no swap is active right now. "
            f"`sudo systemctl start {UNIT_NAME}`",
        ))
    unit_path = unit_dir / UNIT_NAME
    wanted_unit = render_unit(root)
    if not unit_path.exists():
        findings.append((
            f"system {UNIT_NAME}", str(unit_path),
            f"MISSING on disk. `sudo {root}/bin/loam-swap install`",
        ))
    elif unit_path.read_text(encoding="utf-8") != wanted_unit:
        findings.append((
            f"system {UNIT_NAME}", str(unit_path),
            f"differs from template. `sudo {root}/bin/loam-swap install`",
        ))
    enabled = runner(["systemctl", "is-enabled", UNIT_NAME]).stdout.strip()
    if enabled != "enabled":
        findings.append((
            "swap service", f"systemctl is-enabled {UNIT_NAME}",
            f"{enabled or 'unknown'}: swap will not return on reboot. "
            f"`sudo systemctl enable {UNIT_NAME}`",
        ))
    wanted_sysctl = render_sysctl()
    if not sysctl_file.exists() or sysctl_file.read_text(encoding="utf-8") != wanted_sysctl:
        findings.append((
            "swap sysctl", str(sysctl_file),
            f"missing or differs from expected tuning. `sudo {root}/bin/loam-swap install`",
        ))
    return findings
