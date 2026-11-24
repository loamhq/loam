#!/usr/bin/env python3
"""Verify tiered swap sizing, operator-swap preservation, idempotence, zram
fallback, btrfs/fallocate fallbacks, container degradation, and drift checks.

Run: python3 tests/test_swap.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_swap as swap  # noqa: E402

fails = 0


def check(label: str, got, want=True) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:64} {got!r}"[:220]
          + ("" if ok else f"\n      want {want!r}"))


def ok_res(argv, stdout="") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(argv, 0, stdout, "")


def err_res(argv, stderr="failed", rc=1) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(argv, rc, "", stderr)


root = Path(tempfile.mkdtemp(prefix="loam-swap-test-"))
try:
    # -- 1. Sizing plan across box sizes -------------------------------------
    p2 = swap.plan(1930, root_free_mb=20_000, zram_available=True)
    check("2 GB with zram: 1024 MB zram + 1024 MB swapfile",
          (p2["zram_mb"], p2["swapfile_mb"], p2["total_mb"], p2["swappiness"]),
          (1024, 1024, 2048, 20))
    p2_noz = swap.plan(1930, root_free_mb=20_000, zram_available=False)
    check("2 GB without zram: 0 zram + 2048 MB swapfile",
          (p2_noz["zram_mb"], p2_noz["swapfile_mb"], p2_noz["total_mb"]),
          (0, 2048, 2048))
    p4 = swap.plan(3850, root_free_mb=20_000, zram_available=True)
    check("4 GB with zram: 2048 MB zram + 2048 MB swapfile",
          (p4["zram_mb"], p4["swapfile_mb"], p4["total_mb"]),
          (2048, 2048, 4096))
    p8 = swap.plan(7800, root_free_mb=20_000, zram_available=True)
    check("8 GB with zram: 2048 MB zram + 2048 MB swapfile (capped at 4 GiB)",
          (p8["zram_mb"], p8["swapfile_mb"], p8["total_mb"]),
          (2048, 2048, 4096))
    p_tight = swap.plan(3850, root_free_mb=5_700, zram_available=True)
    check("tight disk leaves >= 5 GiB free by shrinking swapfile",
          (p_tight["zram_mb"], p_tight["swapfile_mb"]), (2048, 512))
    p_very_tight = swap.plan(3850, root_free_mb=4_000, zram_available=True)
    check("very tight disk skips swapfile and keeps zram",
          (p_very_tight["zram_mb"], p_very_tight["swapfile_mb"]), (2048, 0))

    check("algorithm preference selects zstd > lz4 > lzo-rle",
          (swap.pick_zram_algo("lzo [lzo-rle] lz4 zstd"),
           swap.pick_zram_algo("lzo [lzo-rle] lz4"),
           swap.pick_zram_algo("[lzo-rle] lzo")),
          ("zstd", "lz4", "lzo-rle"))

    # -- 2. Operator swap is NEVER disturbed ---------------------------------
    d_op = root / "op"
    d_op.mkdir()
    mi_op = d_op / "meminfo"
    mi_op.write_text("MemTotal: 32087040 kB\nMemAvailable: 28000000 kB\n"
                     "SwapTotal: 2097148 kB\nSwapFree: 1900000 kB\n")
    sw_op = d_op / "swaps"
    sw_op.write_text("Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"
                     "/swapfile                               file\t\t2097148\t\t197148\t\t-2\n")
    fstab_op = d_op / "fstab"
    fstab_op.write_text("/swapfile none swap sw 0 0\n")
    cmds: list[list[str]] = []

    res_op = swap.install(
        AGENTS,
        meminfo_path=mi_op, swaps_path=sw_op, fstab_path=fstab_op,
        swapfile=d_op / "loam-swapfile", state_file=d_op / "swap.json",
        sysctl_file=d_op / "90-loam-swap.conf", unit_dir=d_op / "units",
        run_cmd=lambda argv, **k: (cmds.append(argv), ok_res(argv))[1],
    )
    check("existing operator /swapfile: mode=operator, changed=False",
          (res_op["mode"], res_op["changed"]), ("operator", False))
    check("...zero commands run, zero files written",
          (cmds, (d_op / "loam-swapfile").exists(), (d_op / "swap.json").exists(),
           (d_op / "90-loam-swap.conf").exists(), (d_op / "units" / swap.UNIT_NAME).exists()),
          ([], False, False, False, False))
    check("...drift_findings returns empty for operator swap",
          swap.drift_findings(AGENTS, meminfo_path=mi_op, swaps_path=sw_op,
                              fstab_path=fstab_op, state_file=d_op / "swap.json",
                              swapfile=d_op / "loam-swapfile",
                              run_cmd=lambda argv, **k: ok_res(argv)),
          [])

    # Operator swap in /etc/fstab (even if currently inactive) is also left alone.
    sw_empty = d_op / "swaps-empty"
    sw_empty.write_text("Filename\tType\tSize\tUsed\tPriority\n")
    res_fstab = swap.ensure(
        meminfo_path=mi_op, swaps_path=sw_empty, fstab_path=fstab_op,
        swapfile=d_op / "loam-swapfile", state_file=d_op / "swap.json",
        sysctl_file=d_op / "90-loam-swap.conf",
        run_cmd=lambda argv, **k: ok_res(argv),
    )
    check("operator swap listed in /etc/fstab is also left alone",
          (res_fstab["mode"], res_fstab["changed"]), ("operator", False))

    # -- 3. Fresh 2 GB install: zram + swapfile + sysctl + unit + idempotence -
    d_fresh = root / "fresh"
    (d_fresh / "sys_block" / "zram0").mkdir(parents=True)
    (d_fresh / "sys_block" / "zram0" / "disksize").write_text("0\n")
    (d_fresh / "sys_block" / "zram0" / "comp_algorithm").write_text("lzo [lzo-rle] lz4 zstd\n")
    mi_2g = d_fresh / "meminfo"
    mi_2g.write_text("MemTotal: 1976320 kB\nMemAvailable: 1400000 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n")
    sw_2g = d_fresh / "swaps"
    sw_2g.write_text("Filename\tType\tSize\tUsed\tPriority\n")
    fstab_2g = d_fresh / "fstab"
    fstab_2g.write_text("UUID=abc / ext4 defaults 0 1\n")
    mounts_2g = d_fresh / "mounts"
    mounts_2g.write_text("/dev/sda1 / ext4 rw,relatime 0 0\n")
    sf_2g = d_fresh / "var" / "swapfile"
    st_2g = d_fresh / "var" / "swap.json"
    sys_2g = d_fresh / "sysctl" / "90-loam-swap.conf"
    units_2g = d_fresh / "units"
    seen_cmds: list[list[str]] = []

    def fake_runner(argv, **kw):
        seen_cmds.append(argv)
        if argv[0] == "fallocate":
            # Create a tiny stand-in file (no need to allocate 1 GB in tmpfs).
            Path(argv[-1]).write_bytes(b"\0" * 4096)
        elif argv[0] == "systemctl" and argv[1] == "is-enabled":
            return ok_res(argv, "enabled\n")
        return ok_res(argv)

    res1 = swap.install(
        AGENTS,
        meminfo_path=mi_2g, swaps_path=sw_2g, fstab_path=fstab_2g,
        mounts_path=mounts_2g, swapfile=sf_2g, state_file=st_2g,
        sysctl_file=sys_2g, sys_block=d_fresh / "sys_block",
        zram_control=d_fresh / "no-hot-add", unit_dir=units_2g,
        root_free_mb=20_000, run_cmd=fake_runner,
    )
    check("fresh 2 GB install sets up zram0 (zstd, pri=100) + swapfile (pri=10)",
          (res1["mode"], res1["changed"],
           res1["zram"]["dev"], res1["zram"]["size_mb"], res1["zram"]["algo"],
           res1["swapfile"]["size_mb"], res1["swappiness"]),
          ("loam", True, "/dev/zram0", 1024, "zstd", 1024, 20))
    check("...zram disksize & comp_algorithm written to sysfs",
          ((d_fresh / "sys_block" / "zram0" / "disksize").read_text().strip(),
           (d_fresh / "sys_block" / "zram0" / "comp_algorithm").read_text().strip()),
          (str(1024 * 1024 * 1024), "zstd"))
    check("...swapfile mode is 0600, sysctl & unit installed",
          (oct(sf_2g.stat().st_mode & 0o777),
           "vm.swappiness = 20" in sys_2g.read_text(),
           (units_2g / swap.UNIT_NAME).is_file(),
           str(AGENTS) in (units_2g / swap.UNIT_NAME).read_text()),
          ("0o600", True, True, True))

    # Simulate active swaps now present in /proc/swaps -> second run is idempotent!
    sw_2g.write_text(
        "Filename\tType\tSize\tUsed\tPriority\n"
        f"/dev/zram0\tpartition\t{1024 * 1024}\t0\t100\n"
        f"{sf_2g}\tfile\t{1024 * 1024}\t0\t10\n"
    )
    mi_2g.write_text(
        f"MemTotal: 1976320 kB\nMemAvailable: 1400000 kB\n"
        f"SwapTotal: {2048 * 1024} kB\nSwapFree: {2048 * 1024} kB\n"
    )
    seen_cmds.clear()
    res2 = swap.install(
        AGENTS,
        meminfo_path=mi_2g, swaps_path=sw_2g, fstab_path=fstab_2g,
        mounts_path=mounts_2g, swapfile=sf_2g, state_file=st_2g,
        sysctl_file=sys_2g, sys_block=d_fresh / "sys_block",
        zram_control=d_fresh / "no-hot-add", unit_dir=units_2g,
        root_free_mb=20_000, run_cmd=fake_runner,
    )
    check("second run is idempotent: changed=False, no mkswap/swapon/fallocate",
          (res2["mode"], res2["changed"], res2["unit_changed"],
           [c[0] for c in seen_cmds if c[0] in ("mkswap", "swapon", "fallocate", "dd")]),
          ("loam", False, False, []))

    # Drift check is green when active, unit matches & enabled, sysctl matches.
    check("drift_findings is green when Loam swap is healthy",
          swap.drift_findings(
              AGENTS, meminfo_path=mi_2g, swaps_path=sw_2g, fstab_path=fstab_2g,
              unit_dir=units_2g, sysctl_file=sys_2g, state_file=st_2g,
              swapfile=sf_2g, run_cmd=fake_runner,
          ), [])

    # Drift check flags disabled service.
    check("drift_findings flags disabled loam-swap.service",
          len(swap.drift_findings(
              AGENTS, meminfo_path=mi_2g, swaps_path=sw_2g, fstab_path=fstab_2g,
              unit_dir=units_2g, sysctl_file=sys_2g, state_file=st_2g,
              swapfile=sf_2g,
              run_cmd=lambda argv, **k: ok_res(argv, "disabled\n") if argv[:2] == ["systemctl", "is-enabled"] else ok_res(argv),
          )) == 1, True)

    # -- 4. Fallback when zram module is missing (e.g. minimal Ubuntu kernel) -
    d_noz = root / "nozram"
    d_noz.mkdir()
    mi_4g = d_noz / "meminfo"
    mi_4g.write_text("MemTotal: 3942400 kB\nMemAvailable: 3000000 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n")
    sf_4g = d_noz / "swapfile"

    def noz_runner(argv, **kw):
        if argv[0] == "modprobe":
            return err_res(argv, "modprobe: FATAL: Module zram not found")
        if argv[0] == "fallocate":
            Path(argv[-1]).write_bytes(b"\0" * 4096)
        return ok_res(argv)

    res_noz = swap.ensure(
        meminfo_path=mi_4g, swaps_path=sw_empty, fstab_path=fstab_2g,
        mounts_path=mounts_2g, swapfile=sf_4g, state_file=d_noz / "swap.json",
        sysctl_file=d_noz / "90-loam-swap.conf", sys_block=d_noz / "empty_sys",
        zram_control=d_noz / "no-hot-add", root_free_mb=20_000, run_cmd=noz_runner,
    )
    check("missing zram falls back cleanly to full 4096 MB swapfile",
          (res_noz["mode"], res_noz["zram"], res_noz["swapfile"]["size_mb"],
           any("zram skipped" in n for n in res_noz["notes"])),
          ("loam", None, 4096, True))

    # -- 5. Btrfs uses chattr +C + dd, not fallocate -------------------------
    d_bt = root / "btrfs"
    d_bt.mkdir()
    mounts_bt = d_bt / "mounts"
    mounts_bt.write_text("/dev/sda1 / btrfs rw,relatime 0 0\n")
    bt_cmds: list[list[str]] = []

    def bt_runner(argv, **kw):
        bt_cmds.append(argv)
        if argv[0] == "modprobe":
            return err_res(argv, "no zram")
        if argv[0] == "dd":
            for tok in argv:
                if tok.startswith("of="):
                    Path(tok[3:]).write_bytes(b"\0" * 4096)
        return ok_res(argv)

    res_bt = swap.ensure(
        meminfo_path=mi_2g, swaps_path=sw_empty, fstab_path=fstab_2g,
        mounts_path=mounts_bt, swapfile=d_bt / "swapfile",
        state_file=d_bt / "swap.json", sysctl_file=d_bt / "90-loam-swap.conf",
        sys_block=d_bt / "no-sys", zram_control=d_bt / "no-hot",
        root_free_mb=20_000, run_cmd=bt_runner,
    )
    check("btrfs uses chattr +C and dd instead of fallocate",
          (res_bt["mode"],
           any(c[:2] == ["chattr", "+C"] for c in bt_cmds),
           any(c[0] == "dd" for c in bt_cmds),
           any(c[0] == "fallocate" for c in bt_cmds)),
          ("loam", True, True, False))

    # -- 6. Container / overlayfs degrades gracefully without crashing -------
    d_ov = root / "overlay"
    d_ov.mkdir()
    mounts_ov = d_ov / "mounts"
    mounts_ov.write_text("overlay / overlay rw 0 0\n")
    res_ov = swap.ensure(
        meminfo_path=mi_2g, swaps_path=sw_empty, fstab_path=fstab_2g,
        mounts_path=mounts_ov, swapfile=d_ov / "swapfile",
        state_file=d_ov / "swap.json", sysctl_file=d_ov / "90-loam-swap.conf",
        sys_block=d_ov / "no-sys", zram_control=d_ov / "no-hot",
        root_free_mb=20_000, run_cmd=lambda argv, **k: err_res(argv, "Operation not permitted"),
    )
    check("container/overlay degrades gracefully to mode=unavailable",
          (res_ov["mode"], res_ov["changed"]), ("unavailable", False))

finally:
    shutil.rmtree(root, ignore_errors=True)

print(f"\nFAILURES: {fails}")
sys.exit(1 if fails else 0)
