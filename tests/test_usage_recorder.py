#!/usr/bin/env python3
"""Tests for the per-minute numbers-only box usage recorder and optional collector pusher:
sampling, privacy allowlists, rotation/retention bounds, off-by-default silence,
batch delivery, dead-collector resilience & catch-up, and live broker non-blocking.

Run: python3 tests/run-all test_usage_recorder.py
"""

from __future__ import annotations

import http.client
import http.server
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import loam_broker as cfg  # noqa: E402
import loam_config as lc  # noqa: E402
import loam_limits as limits  # noqa: E402
import loam_usage_recorder as ur  # noqa: E402

FAILS: list[str] = []


def check(label: str, got, want) -> None:
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label:68} {got!r}"
          + ("" if ok else f"\n      wanted {want!r}"))
    if not ok:
        FAILS.append(label)


def check_true(label: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {label}" + (f" ({detail})" if detail else ""))
    if not cond:
        FAILS.append(label)


def write_fake_proc(
    root: Path,
    *,
    mem_total_kb: int = 4096 * 1024,
    mem_avail_kb: int = 2500 * 1024,
    swap_total_kb: int = 2048 * 1024,
    swap_free_kb: int = 1900 * 1024,
    cpu: tuple[int, ...] = (1000, 0, 500, 8000, 500, 0, 0, 0),
    btime: int = 1700000000,
    vmstat_oom: int = 0,
    loadavg: str = "0.42 0.31 0.25 1/200 1234",
    pids: dict[int, int] | None = None,
) -> dict[str, Path]:
    proc = root / "proc"
    proc.mkdir(parents=True, exist_ok=True)
    meminfo = proc / "meminfo"
    meminfo.write_text(
        f"MemTotal:       {mem_total_kb} kB\n"
        f"MemFree:        {mem_avail_kb // 2} kB\n"
        f"MemAvailable:   {mem_avail_kb} kB\n"
        f"SwapTotal:      {swap_total_kb} kB\n"
        f"SwapFree:       {swap_free_kb} kB\n",
        encoding="utf-8",
    )
    stat = proc / "stat"
    stat.write_text(
        "cpu  " + " ".join(str(x) for x in cpu) + " 0 0\n"
        f"btime {btime}\n",
        encoding="utf-8",
    )
    vmstat = proc / "vmstat"
    vmstat.write_text(f"pgmajfault 123\noom_kill {vmstat_oom}\n", encoding="utf-8")
    loadavg_p = proc / "loadavg"
    loadavg_p.write_text(loadavg + "\n", encoding="utf-8")

    for pid, starttime in (pids or {}).items():
        pdir = proc / str(pid)
        pdir.mkdir(parents=True, exist_ok=True)
        # /proc/<pid>/stat: pid (comm) state ppid ... field[19] after ')' is starttime
        fields = ["S"] + ["0"] * 18 + [str(starttime)] + ["0"] * 10
        (pdir / "stat").write_text(f"{pid} (proc) " + " ".join(fields) + "\n", encoding="utf-8")

    return {
        "proc": proc,
        "meminfo": meminfo,
        "stat": stat,
        "vmstat": vmstat,
        "loadavg": loadavg_p,
    }


def make_cgroup_unit(parent: Path, name: str, pids: list[int] | None) -> Path:
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    if pids is not None:
        (d / "cgroup.procs").write_text("\n".join(str(p) for p in pids) + ("\n" if pids else ""), encoding="utf-8")
    return d


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeCollector:
    """Thread-safe local HTTP receiver simulating healthy, dead, hanging,
    and redirecting collector behaviour."""

    def __init__(self, expected_token: str) -> None:
        self.expected_token = expected_token
        self.mode = "ok"          # "ok" | "503" | "hang" | "redirect" | "401"
        self.hang_s = 1.5
        self.hang_started = threading.Event()
        self.hang_release = threading.Event()
        self.received: list[dict] = []
        self.requests: list[dict] = []
        self.lock = threading.Lock()
        self._server: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = 0

    def start(self, port: int = 0) -> str:
        parent = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or "0")
                raw = self.rfile.read(length) if length else b""
                auth = self.headers.get("Authorization", "")
                with parent.lock:
                    mode = parent.mode
                    hang_s = parent.hang_s
                    parent.requests.append({
                        "path": self.path,
                        "auth": auth,
                        "content_type": self.headers.get("Content-Type", ""),
                        "ua": self.headers.get("User-Agent", ""),
                        "mode": mode,
                        "bytes": len(raw),
                    })
                if mode == "hang":
                    parent.hang_started.set()
                    parent.hang_release.wait(timeout=hang_s)
                    try:
                        self.send_response(504)
                        self.end_headers()
                    except OSError:
                        pass
                    return
                if mode == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "http://127.0.0.1:9/evil")
                    self.end_headers()
                    return
                if mode == "503":
                    self.send_response(503)
                    self.end_headers()
                    self.wfile.write(b"unavailable")
                    return
                if mode == "401" or auth != f"Bearer {parent.expected_token}":
                    self.send_response(401)
                    self.end_headers()
                    self.wfile.write(b"unauthorized")
                    return
                payload = json.loads(raw.decode("utf-8"))
                seqs = [r["seq"] for r in payload.get("samples", [])] + [
                    r["seq"] for r in payload.get("events", [])
                ]
                max_seq = max(seqs) if seqs else 0
                with parent.lock:
                    parent.received.append(payload)
                resp = json.dumps({"ok": True, "acked_seq": max_seq}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return f"http://127.0.0.1:{self.port}/v1/ingest"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


def test_sampling_and_events(tmp: Path) -> None:
    print("\n-- 1. Sampling, CPU delta, events & privacy allowlist --")
    fake = tmp / "sample-case"
    app_slice = fake / "app.slice"
    app_slice.mkdir(parents=True)
    app_cg = fake / "loam-app@loam-ui.service"
    browser_state = fake / "browser-state.json"
    limits_file = fake / "limits" / "events.jsonl"
    data_disk = fake / "data-disk"
    data_disk.mkdir()

    # Create active units, an empty lingering unit, and a wake-failed unit.
    make_cgroup_unit(app_slice, "loam-engineer-job1.service", [2001])
    make_cgroup_unit(app_slice, "loam-engineer-empty.service", [])
    make_cgroup_unit(app_slice, "loam-wake-alpha.service", [2002])
    make_cgroup_unit(app_slice, "loam-wake-failed@alpha.service", [2003])
    make_cgroup_unit(app_slice, "loam-cron-alpha-daily.service", [2004])
    make_cgroup_unit(app_slice, "loam-plugin-reviewer--nightly.service", [2005])
    make_cgroup_unit(app_slice, "loam-session-broker.service", [1001])
    make_cgroup_unit(app_slice, "loam-session-host.service", [1002])
    make_cgroup_unit(app_cg, ".", [1003])
    browser_state.write_text(json.dumps({"state": "awake"}), encoding="utf-8")

    proc = write_fake_proc(
        fake,
        mem_total_kb=4096 * 1024,
        mem_avail_kb=2500 * 1024,
        swap_total_kb=2048 * 1024,
        swap_free_kb=1900 * 1024,
        cpu=(1000, 0, 500, 8000, 500, 0, 0, 0),  # total=10000, idle=8500
        btime=1700000000,
        vmstat_oom=2,
        loadavg="0.75 0.50 0.25 1/100 999",
        pids={1001: 500, 1002: 400, 1003: 300},
    )

    clock_t = [1795536000.0]  # 2026-11-24T16:00:00Z
    chats = [(3, 1)]
    store = ur.UsageStore(fake / "usage")
    rec = ur.UsageRecorder(
        store,
        meminfo_path=proc["meminfo"],
        stat_path=proc["stat"],
        vmstat_path=proc["vmstat"],
        loadavg_path=proc["loadavg"],
        proc_dir=proc["proc"],
        root_disk_path=fake,
        data_disk_path=data_disk,
        app_slice_dir=app_slice,
        system_app_cgroup_dir=app_cg,
        browser_state_path=browser_state,
        limits_events_path=limits_file,
        chats_provider=lambda: chats[0],
        clock=lambda: clock_t[0],
        vcpus_provider=lambda: 2,
    )

    rows1 = rec.tick()
    check("first tick emits exactly 1 sample (no false restarts)", len(rows1), 1)
    s1 = rows1[0]
    ur.validate_record(s1)
    check("sample 1 seq and type", (s1["type"], s1["seq"]), ("sample", 1))
    check("sample 1 UTC timestamp", s1["ts"], "2026-11-24T16:00:00Z")
    check("sample 1 interval_s and cpu_pct are None before baseline",
          (s1["interval_s"], s1["cpu_pct"]), (None, None))
    check("sample 1 memory & swap MiB",
          (s1["mem_total_mb"], s1["mem_used_mb"], s1["mem_available_mb"],
           s1["swap_total_mb"], s1["swap_used_mb"]),
          (4096, 1596, 2500, 2048, 148))
    check("sample 1 vcpus & load", (s1["vcpus"], s1["load_1m"], s1["load_5m"]), (2, 0.75, 0.5))
    check_true("sample 1 root & data disk reported as non-negative ints",
               isinstance(s1["disk_root_total_mb"], int) and s1["disk_root_total_mb"] > 0
               and isinstance(s1["disk_data_total_mb"], int) and s1["disk_data_total_mb"] > 0)
    check("sample 1 Loam activity counts (empty cgroup & wake-failed excluded)",
          (s1["chats_live"], s1["chats_busy"], s1["jobs"], s1["wakes"], s1["crons"], s1["browser_up"]),
          (3, 1, 1, 1, 2, True))

    # Write limit events loaded with private canary strings to prove scrubbing.
    canaries = [
        "secret-agent-canary", "secret-thread-canary", "secret-session-canary",
        "secret-job-canary", "secret-project-canary", "cron:secret-cron-name",
        "secret-for-agent", "loam-wake-secret-agent 999 MB", "secret-provider",
    ]
    limits.record(
        "chat_wait", outcome="started", duration_s=14.26,
        subject={"agent": canaries[0], "thread": canaries[1], "session": canaries[2]},
        memory={"available_mb": 420, "swap_used_mb": 300},
        detail={"reason": "memory", "retries": 3},
        ts=clock_t[0] + 15, path=limits_file,
    )
    limits.record(
        "chat_evicted", outcome="evicted",
        subject={"agent": canaries[0], "thread": canaries[1]},
        memory={"available_mb": 380, "swap_used_mb": 310},
        detail={"trigger": "waiting_run", "for_agent": canaries[6], "idle_s": 142.4},
        ts=clock_t[0] + 20, path=limits_file,
    )
    limits.record(
        "job_queued", outcome="started", duration_s=45.0,
        subject={"agent": canaries[0], "job": canaries[3], "project": canaries[4], "lane": canaries[5]},
        memory={"available_mb": 350, "swap_used_mb": 320},
        ts=clock_t[0] + 25, path=limits_file,
    )
    limits.record(
        "pressure_episode", outcome="ended", duration_s=240.0,
        memory={"available_mb": 210, "swap_used_mb": 512},
        detail={"ram_pct": 92, "swap_free_pct": 12, "heaviest": [canaries[7]]},
        ts=clock_t[0] + 30, path=limits_file,
    )
    limits.record(
        "oom_kill", outcome="killed",
        subject={"agent": canaries[0], "unit": canaries[7]},
        memory={"available_mb": 64, "swap_used_mb": 1024},
        detail={"source": "cgroup_memory_events", "oom_kills": 2, "provider": canaries[8]},
        ts=clock_t[0] + 35, path=limits_file,
    )

    # Tick 2: 60s later, 25% busy CPU, 1 kernel OOM increment, app + broker restarted, browser asleep.
    clock_t[0] += 60.0
    chats[0] = (1, 0)
    browser_state.write_text(json.dumps({"state": "asleep"}), encoding="utf-8")
    make_cgroup_unit(app_slice, "loam-session-broker.service", [1051])
    make_cgroup_unit(app_cg, ".", [1053])
    write_fake_proc(
        fake,
        mem_total_kb=4096 * 1024,
        mem_avail_kb=3000 * 1024,
        swap_total_kb=2048 * 1024,
        swap_free_kb=2048 * 1024,
        # +1000 total jiffies: +200 user, +50 sys, +750 idle => 25.0% CPU
        cpu=(1200, 0, 550, 8750, 500, 0, 0, 0),
        btime=1700000000,
        vmstat_oom=3,  # +1 kernel OOM
        loadavg="0.10 0.20 0.25 1/100 999",
        pids={1051: 650, 1002: 400, 1053: 700},
    )

    rows2 = rec.tick()
    ev_rows = [r for r in rows2 if r["type"] == "event"]
    sample_rows = [r for r in rows2 if r["type"] == "sample"]
    check("tick 2 emits 1 sample", len(sample_rows), 1)
    s2 = sample_rows[0]
    check("sample 2 interval_s and exact cpu_pct (25.0%)",
          (s2["interval_s"], s2["cpu_pct"]), (60, 25.0))
    check("sample 2 browser_up is False", s2["browser_up"], False)

    kinds = [e["kind"] for e in ev_rows]
    check("tick 2 captured restarts, kernel_oom and all 5 limit events",
          sorted(kinds),
          sorted(["service_restart", "service_restart", "kernel_oom",
                  "chat_wait", "chat_evicted", "job_queued", "pressure_episode", "oom_kill"]))
    restart_svcs = sorted(e["service"] for e in ev_rows if e["kind"] == "service_restart")
    check("restarted services are broker and app (host unchanged)", restart_svcs, ["app", "broker"])
    evict = next(e for e in ev_rows if e["kind"] == "chat_evicted")
    check("chat_evicted preserves trigger & idle_s only",
          (evict.get("trigger"), evict.get("idle_s"), evict.get("outcome")),
          ("waiting_run", 142, "evicted"))
    koom = next(e for e in ev_rows if e["kind"] == "kernel_oom")
    check("kernel_oom count delta is 1", (koom["count"], koom["outcome"]), (1, "killed"))

    # Strict privacy check across the entire stored file + wire batch.
    raw_file = store.active_path.read_text(encoding="utf-8")
    wire = json.dumps(ur.build_wire_batch(
        store.read_after(0), box_id="box-1", plan="4gb", version="0.2.35", sent_at=ur.now_iso()
    ))
    leaked = [c for c in canaries if c in raw_file or c in wire]
    check("zero canary strings leak into local file or wire batch", leaked, [])

    # Tick 3: no new limit events, verify deduplication + reboot detection.
    clock_t[0] += 60.0
    write_fake_proc(
        fake,
        cpu=(100, 0, 50, 850, 0, 0, 0, 0),
        btime=1700099999,  # reboot!
        vmstat_oom=0,
        pids={1051: 12, 1002: 14, 1053: 16},
    )
    rows3 = rec.tick()
    s3 = [r for r in rows3 if r["type"] == "sample"][0]
    ev3 = [r for r in rows3 if r["type"] == "event"]
    check("reboot resets cpu_pct cleanly to None", s3["cpu_pct"], None)
    check("reboot emits service_restart with outcome='reboot' and zero duplicate limit events",
          sorted((e["kind"], e["service"], e["outcome"]) for e in ev3),
          [("service_restart", "app", "reboot"),
           ("service_restart", "broker", "reboot"),
           ("service_restart", "session_host", "reboot")])

    # Validation guard refuses stray keys or free-form strings.
    bad_raised = False
    try:
        ur.validate_record({**s1, "agent": "alice"})
    except ValueError:
        bad_raised = True
    check("validate_record rejects unexpected key on sample", bad_raised, True)
    bad_ev = False
    try:
        ur.validate_record({"type": "event", "seq": 99, "ts": "2026-11-24T16:00:00Z",
                            "kind": "chat_wait", "outcome": "free-form-text",
                            "mem_available_mb": 100, "swap_used_mb": 0})
    except ValueError:
        bad_ev = True
    check("validate_record rejects non-enum outcome", bad_ev, True)


def test_rotation_and_retention(tmp: Path) -> None:
    print("\n-- 2. Rotation bounds file size and 90-day retention prunes old days --")
    fake = tmp / "rot-case"
    proc = write_fake_proc(fake)
    app_slice = fake / "empty-slice"
    app_slice.mkdir()
    browser_state = fake / "missing-browser.json"
    limits_file = fake / "missing-limits.jsonl"

    # Tight byte caps so rotation + pruning exercise cleanly in milliseconds.
    store = ur.UsageStore(fake / "usage", max_file_bytes=1200, max_total_bytes=4096, retention_days=90)
    t = [1795536000.0]
    rec = ur.UsageRecorder(
        store,
        meminfo_path=proc["meminfo"], stat_path=proc["stat"],
        vmstat_path=proc["vmstat"], loadavg_path=proc["loadavg"],
        proc_dir=proc["proc"], root_disk_path=fake,
        app_slice_dir=app_slice, system_app_cgroup_dir=fake / "no-app",
        browser_state_path=browser_state, limits_events_path=limits_file,
        clock=lambda: t[0], vcpus_provider=lambda: 1,
    )
    for _ in range(80):
        rec.tick()
        t[0] += 60.0

    total = store.total_bytes()
    archives = store._archive_entries()
    check_true("size rotation produced compressed archive segments", len(archives) >= 1, f"{len(archives)} archive(s)")
    check_true("total store size stays bounded under max_total_bytes",
               total <= store.max_total_bytes, f"{total} <= {store.max_total_bytes}")
    rows = store.read_after(0)
    seqs = [r["seq"] for r in rows]
    check("kept records are strictly increasing in seq with newest present",
          (seqs == sorted(seqs), seqs[-1] == 80), (True, True))

    # Recovery if state.json is deleted: seq continues monotonically.
    store.state_path.unlink()
    next_rows = rec.tick()
    check("missing state.json recovers last_seq monotonically", next_rows[-1]["seq"], 81)

    # 90-day retention pruning across daily rollovers.
    day_store = ur.UsageStore(fake / "days", max_file_bytes=2 * 1024 * 1024,
                              max_total_bytes=16 * 1024 * 1024, retention_days=90)
    day_t = [1767225600.0]  # 2026-01-01T00:00:00Z
    day_rec = ur.UsageRecorder(
        day_store,
        meminfo_path=proc["meminfo"], stat_path=proc["stat"],
        vmstat_path=proc["vmstat"], loadavg_path=proc["loadavg"],
        proc_dir=proc["proc"], root_disk_path=fake,
        app_slice_dir=app_slice, system_app_cgroup_dir=fake / "no-app",
        browser_state_path=browser_state, limits_events_path=limits_file,
        clock=lambda: day_t[0], vcpus_provider=lambda: 1,
    )
    for _ in range(95):
        day_rec.tick()
        day_t[0] += 86400.0
    kept_days = [e[0] for e in day_store._archive_entries()]
    check_true("retention drops archives older than 90 days",
               len(kept_days) <= 91 and kept_days[0] >= "2026-01-05",
               f"{len(kept_days)} archives, oldest={kept_days[0]}")


def test_no_collector_never_connects(tmp: Path) -> None:
    print("\n-- 3. No collector configured -> zero outbound attempts --")
    fake = tmp / "nocollector"
    fake.mkdir()
    store = ur.UsageStore(fake / "usage")
    store.append_batch([("sample", {
        "ts": "2026-11-24T17:00:00Z", "interval_s": 60,
        "mem_total_mb": 2048, "mem_used_mb": 512, "mem_available_mb": 1536,
        "swap_total_mb": 1024, "swap_used_mb": 0,
        "cpu_pct": 1.2, "vcpus": 1, "load_1m": 0.05, "load_5m": 0.04,
        "disk_root_total_mb": 40000, "disk_root_used_mb": 8000,
        "disk_data_total_mb": None, "disk_data_used_mb": None,
        "chats_live": 0, "chats_busy": 0, "jobs": 0, "wakes": 0, "crons": 0, "browser_up": False,
        "owner_services_up": 0, "owner_sites": 0,
    })], now_ts=1795539600.0)

    attempts = [0]
    def forbidden_transport(*args, **kwargs):
        attempts[0] += 1
        raise AssertionError("outbound connection attempted when collector was off!")

    cases = [
        ("empty config", ""),
        ("url without token_file", '[usage]\ncollector_url = "https://usage.example.com/v1/ingest"\n'),
        ("missing token file", f'[usage]\ncollector_url = "https://usage.example.com/v1/ingest"\ntoken_file = "{fake / "nope.tok"}"\n'),
        ("non-HTTPS remote URL", f'[usage]\ncollector_url = "http://usage.example.com/v1/ingest"\ntoken_file = "{fake / "tok"}"\n'),
        ("URL with embedded creds", f'[usage]\ncollector_url = "https://a:b@usage.example.com/v1/ingest"\ntoken_file = "{fake / "tok"}"\n'),
    ]
    (fake / "tok").write_text("secret-token\n", encoding="utf-8")

    for name, toml_text in cases:
        cfg_file = fake / f"{name.replace(' ', '_')}.toml"
        cfg_file.write_text(toml_text, encoding="utf-8")
        pub = ur.public_status(cfg_file)
        pusher = ur.UsagePusher(store, config_path=cfg_file, transport=forbidden_transport)
        res = pusher.push_once()
        check(f"{name}: sending=False and push=off",
              (pub["sending"], pub["collector_host"], res["status"]),
              (False, None, "off"))
    check("zero outbound transport calls across all off/invalid cases", attempts[0], 0)


def test_collector_delivery_outage_and_catchup(tmp: Path) -> None:
    print("\n-- 4. Collector delivery, dead-collector resilience & catch-up --")
    fake = tmp / "push-case"
    fake.mkdir()
    tok_file = fake / "collector.token"
    tok_file.write_text("tok_test_9f8e7d6c\n", encoding="utf-8")

    collector = FakeCollector("tok_test_9f8e7d6c")
    url = collector.start()
    cfg_file = fake / "loam.toml"
    cfg_file.write_text(
        "[usage]\n"
        f'collector_url = "{url}"\n'
        f'token_file = "{tok_file}"\n'
        'box_id = "box_demo_01"\n'
        'plan = "4gb"\n',
        encoding="utf-8",
    )

    pub = ur.public_status(cfg_file)
    check("public_status reports sending=True and host only (no token/box_id)",
          (pub["sending"], pub["collector_host"], "token" in json.dumps(pub), "box_demo_01" in json.dumps(pub)),
          (True, f"127.0.0.1:{collector.port}", False, False))

    proc = write_fake_proc(fake)
    app_slice = fake / "slice"
    app_slice.mkdir()
    store = ur.UsageStore(fake / "usage", max_file_bytes=1200, max_total_bytes=64 * 1024)
    clock_t = [1795536000.0]
    rec = ur.UsageRecorder(
        store, config_path=cfg_file,
        meminfo_path=proc["meminfo"], stat_path=proc["stat"],
        vmstat_path=proc["vmstat"], loadavg_path=proc["loadavg"],
        proc_dir=proc["proc"], root_disk_path=fake,
        app_slice_dir=app_slice, system_app_cgroup_dir=fake / "no-app",
        browser_state_path=fake / "no-browser.json",
        limits_events_path=fake / "no-limits.jsonl",
        clock=lambda: clock_t[0], vcpus_provider=lambda: 2,
    )
    pusher = ur.UsagePusher(
        store, config_path=cfg_file,
        timeout_s=0.4, base_backoff_s=10.0, max_backoff_s=60.0,
        max_batch_records=4, clock=lambda: clock_t[0], version="0.2.35",
    )

    try:
        # 1) Healthy delivery
        for _ in range(3):
            rec.tick()
            clock_t[0] += 60.0
        r1 = pusher.push_once()
        check("healthy push sends 3 samples in 1 batch",
              (r1["status"], r1["sent"], r1["batches"], r1["acked_seq"]),
              ("sent", 3, 1, 3))
        batch1 = collector.received[-1]
        check("wire envelope headers & metadata",
              (batch1["format"], batch1["box_id"], batch1["plan"], batch1["loam_version"],
               len(batch1["samples"]), len(batch1["events"])),
              ("loam-usage/1", "box_demo_01", "4gb", "0.2.35", 3, 0))
        check("bearer token & content-type sent",
              (collector.requests[-1]["auth"], collector.requests[-1]["content_type"]),
              ("Bearer tok_test_9f8e7d6c", "application/json; charset=utf-8"))

        # Second push when already caught up sends nothing.
        r_idle = pusher.push_once()
        check("caught-up push is idle", (r_idle["status"], r_idle["sent"]), ("idle", 0))

        # 2) Redirect is refused (token never forwarded).
        rec.tick()  # seq 4
        clock_t[0] += 60.0
        collector.mode = "redirect"
        r_redir = pusher.push_once()
        check("302 redirect is refused without advancing acked_seq",
              (r_redir["status"], r_redir["error"], r_redir["acked_seq"]),
              ("error", "HTTP 302", 3))

        # 3) Backoff honored before next_attempt_at.
        r_back = pusher.push_once()
        check("immediate retry respects backoff", r_back["status"], "backoff")

        # 4) Dead collector (503) loses nothing and keeps token out of push.json.
        collector.mode = "503"
        clock_t[0] += 15.0
        for _ in range(4):
            rec.tick()  # seq 5..8 (crosses max_file_bytes -> rotates to archive!)
            clock_t[0] += 60.0
        r_503 = pusher.push_once()
        check("HTTP 503 leaves acked_seq at 3",
              (r_503["status"], r_503["error"], r_503["acked_seq"]),
              ("error", "HTTP 503", 3))
        push_text = store.push_path.read_text(encoding="utf-8")
        check("token never appears in push.json", "tok_test_9f8e7d6c" in push_text, False)

        # 5) Hanging collector times out quickly without wedging.
        collector.mode = "hang"
        collector.hang_s = 2.0
        clock_t[0] += 60.0
        t0 = time.monotonic()
        r_hang = pusher.push_once(force=True)
        elapsed = time.monotonic() - t0
        check("hanging collector times out cleanly",
              (r_hang["status"], r_hang["error"], r_hang["acked_seq"]),
              ("error", "timeout", 3))
        check_true("hanging timeout bounded near timeout_s", elapsed < 1.2, f"{elapsed:.2f}s")

        # 6) Completely stopped collector (connection refused) loses nothing.
        saved_port = collector.port
        collector.stop()
        for _ in range(3):
            rec.tick()  # seq 9..11
            clock_t[0] += 60.0
        r_dead = pusher.push_once(force=True)
        check("stopped collector returns error and keeps acked_seq=3",
              (r_dead["status"], r_dead["acked_seq"]), ("error", 3))

        # 7) Collector comes back up -> catches up ALL 8 missed records (seq 4..11)
        # spanning both archived .jsonl.gz segments and active usage.jsonl!
        collector.mode = "ok"
        collector.start(port=saved_port)
        clock_t[0] += 120.0
        r_catch = pusher.push_once()
        check("catch-up delivers all 8 missed records in 2 batches of 4",
              (r_catch["status"], r_catch["sent"], r_catch["batches"], r_catch["acked_seq"]),
              ("sent", 8, 2, 11))
        all_seqs = [
            s["seq"]
            for batch in collector.received
            for s in batch["samples"]
        ]
        check("collector received seq 1..11 exactly once in order",
              all_seqs, list(range(1, 12)))
    finally:
        collector.stop()


def test_live_broker_nonblocking_and_cli(tmp: Path) -> None:
    print("\n-- 5. Live broker route, non-blocking under hanging collector & CLI --")
    fake = tmp / "broker-case"
    fake.mkdir()
    tok_file = fake / "usage.token"
    tok_file.write_text("broker_tok_1234\n", encoding="utf-8")

    # Configure a hanging collector in the test's scratch LOAM_CONFIG.
    collector = FakeCollector("broker_tok_1234")
    collector.mode = "hang"
    collector.hang_s = 3.0
    url = collector.start()

    scratch_cfg = Path(os.environ["LOAM_CONFIG"])
    orig_cfg = scratch_cfg.read_text(encoding="utf-8")
    scratch_cfg.write_text(
        orig_cfg + "\n[usage]\n"
        f'collector_url = "{url}"\n'
        f'token_file = "{tok_file}"\n'
        'box_id = "box_broker_e2e"\n'
        'plan = "2gb"\n',
        encoding="utf-8",
    )

    port = free_port()
    token = cfg.read_token()
    env = dict(os.environ)
    env["LOAM_USAGE_INTERVAL_S"] = "0.2"
    env["LOAM_USAGE_PUSH_INTERVAL_S"] = "0.2"
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "bin" / "session-broker"),
         "--addr", "127.0.0.1", "--port", str(port)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        # Wait for broker /up
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            try:
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                c.request("GET", "/up")
                if c.getresponse().status == 200:
                    c.close()
                    break
            except OSError:
                time.sleep(0.1)

        # Wait until the broker's background pusher is actively blocked on the hanging collector!
        check_true("broker background pusher started a request to hanging collector",
                   collector.hang_started.wait(timeout=5.0))
        t0 = time.monotonic()
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        c.request("GET", "/v1/usage/status", headers={"Authorization": f"Bearer {token}"})
        resp = c.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        c.close()
        dt_status = time.monotonic() - t0
        check("GET /v1/usage/status returns 200 with sending=True and host",
              (resp.status, body["recording"], body["sending"], body["collector_host"]),
              (200, True, True, f"127.0.0.1:{collector.port}"))
        check_true("GET /v1/usage/status is non-blocking while collector hangs",
                   dt_status < 0.5, f"{dt_status * 1000:.1f} ms")

        t1 = time.monotonic()
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        c.request("GET", "/v1/sessions/live", headers={"Authorization": f"Bearer {token}"})
        resp2 = c.getresponse()
        resp2.read()
        c.close()
        dt_live = time.monotonic() - t1
        check("GET /v1/sessions/live returns 200", resp2.status, 200)
        check_true("broker chat/live route is unblocked during collector hang",
                   dt_live < 0.5, f"{dt_live * 1000:.1f} ms")

        # Verify local samples were written by the running broker.
        usage_file = lc.STATE / "usage" / "usage.jsonl"
        wait_end = time.monotonic() + 5.0
        while time.monotonic() < wait_end and (not usage_file.exists() or usage_file.stat().st_size == 0):
            time.sleep(0.1)
        check_true("broker writes local usage.jsonl samples automatically",
                   usage_file.exists() and usage_file.stat().st_size > 0)

        # CLI `bin/loam usage status --json` works.
        cli = subprocess.run(
            [str(ROOT / "bin" / "loam"), "usage", "status", "--json"],
            capture_output=True, text=True, check=False, timeout=15,
        )
        check("loam usage status --json exits 0", cli.returncode, 0)
        cli_data = json.loads(cli.stdout)
        check("CLI reports sending=True and last_seq >= 1",
              (cli_data["sending"], cli_data["last_seq"] >= 1), (True, True))

        # Release the hanging request and restore collector -> catch-up delivers live broker batches!
        collector.mode = "ok"
        collector.hang_release.set()
        push_end = time.monotonic() + 5.0
        push_out = {}
        while time.monotonic() < push_end:
            push_cli = subprocess.run(
                [str(ROOT / "bin" / "loam"), "usage", "push", "--force", "--json"],
                capture_output=True, text=True, check=False, timeout=15,
            )
            if push_cli.returncode == 0:
                push_out = json.loads(push_cli.stdout)
                if push_out.get("status") != "busy":
                    break
            time.sleep(0.1)
        check("loam usage push --force delivers after collector recovers",
              push_out.get("status"), "sent")
        check_true("collector received live broker batch after recovery",
                   len(collector.received) >= 1 and collector.received[-1]["box_id"] == "box_broker_e2e")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        collector.stop()
        scratch_cfg.write_text(orig_cfg, encoding="utf-8")


def test_owner_services_and_sites_counts(tmp: Path) -> None:
    print("\n-- 6. Owner services and sites counts (numbers only, never names) --")
    fake = tmp / "owner-counts"
    fake.mkdir()
    proc = write_fake_proc(fake)

    # System slice with core apps (excluded), live non-core apps (counted, deduplicated),
    # and a stopped non-core app (empty cgroup.procs -> excluded).
    sys_slice = fake / "system.slice"
    app_sub = sys_slice / r"system-loam\x2dapp.slice"
    for d, pids in (
        (app_sub / "loam-app@loam-ui.service", "100\n"),
        (app_sub / "loam-app@files.service", "101\n"),
        (app_sub / "loam-app@vault.service", "102\n"),
        (app_sub / "loam-app@secret-owner-app.service", "200\n"),
        (sys_slice / "loam-app@secret-owner-app.service", "200\n"),  # duplicate name
        (sys_slice / "loam-app@secret-blog.service", "201\n"),
        (sys_slice / "loam-app@stopped-app.service", ""),
    ):
        d.mkdir(parents=True, exist_ok=True)
        (d / "cgroup.procs").write_text(pids, encoding="utf-8")

    # User app.slice + gateway.json: one live always_on plugin (+1), one stopped always_on (0),
    # one live on-demand plugin (0).
    app_slice = fake / "app.slice"
    for name, pids in (
        ("loam-plugin-secret-daemon.service", "300\n"),
        ("loam-plugin-stopped-daemon.service", ""),
        ("loam-plugin-ondemand-tool.service", "301\n"),
    ):
        d = app_slice / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "cgroup.procs").write_text(pids, encoding="utf-8")
    gw_json = fake / "gateway.json"
    gw_json.write_text(json.dumps({
        "wanted": {
            "secret-daemon": {"always_on": True},
            "stopped-daemon": {"always_on": True},
            "ondemand-tool": {"always_on": False},
        },
    }), encoding="utf-8")

    # Sites dir: 1 permanent site, 1 future-expiring site, 1 expired site (excluded).
    sites_dir = fake / "sites"
    sites_dir.mkdir()
    (sites_dir / "secret-static-site.toml").write_text(
        'owner = "ada"\nroot = "/srv/site"\n', encoding="utf-8",
    )
    (sites_dir / "future-preview.toml").write_text(
        'owner = "ada"\nport = 9580\nexpires = 2026-12-01\n', encoding="utf-8",
    )
    (sites_dir / "expired-preview.toml").write_text(
        'owner = "ada"\nroot = "/srv/old"\nexpires = 2026-11-20\n', encoding="utf-8",
    )

    # Deploy dir: core dirs excluded, 2 non-core owner apps counted.
    deploy_dir = fake / "deploy"
    for name in ("loam-ui", "files", "vault", "login", "secret-owner-app", "secret-blog"):
        (deploy_dir / name).mkdir(parents=True)
        (deploy_dir / name / "deploy.toml").write_text('exposure = "private"\n', encoding="utf-8")

    store = ur.UsageStore(fake / "usage")
    rec = ur.UsageRecorder(
        store,
        meminfo_path=proc["meminfo"], stat_path=proc["stat"],
        vmstat_path=proc["vmstat"], loadavg_path=proc["loadavg"],
        proc_dir=proc["proc"], root_disk_path=fake,
        app_slice_dir=app_slice, system_slice_dir=sys_slice,
        system_app_cgroup_dir=app_sub / "loam-app@loam-ui.service",
        gateway_json_path=gw_json, sites_dir=sites_dir, deploy_dir=deploy_dir,
        browser_state_path=fake / "no-browser.json",
        limits_events_path=fake / "no-limits.jsonl",
        clock=lambda: 1795536000.0,  # 2026-11-24T16:00:00Z
        vcpus_provider=lambda: 2,
    )
    rows = rec.tick()
    s = rows[-1]
    ur.validate_record(s)
    # 2 unique live non-core loam-app services + 1 live always_on plugin = 3
    check("owner_services_up counts unique live non-core apps + always_on plugins",
          s["owner_services_up"], 3)
    # 2 non-expired sites + 2 non-core deploy apps = 4
    check("owner_sites counts non-expired sites + non-core deploy apps",
          s["owner_sites"], 4)

    # Privacy: zero canary app/site/plugin names in persisted JSONL.
    raw_jsonl = store.active_path.read_text(encoding="utf-8")
    leaks = [c for c in ("secret-owner-app", "secret-blog", "secret-daemon", "secret-static-site")
             if c in raw_jsonl]
    check("zero owner app/site/plugin names leaked to usage.jsonl", leaks, [])

    # Privacy guard rejects negative or non-int owner counts.
    for bad_field, bad_val in (("owner_services_up", -1), ("owner_sites", "two"), ("owner_sites", True)):
        raised = False
        try:
            ur.validate_record({**s, bad_field: bad_val})
        except ValueError:
            raised = True
        check(f"validate_record rejects {bad_field}={bad_val!r}", raised, True)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="loam-usage-test-"))
    try:
        test_sampling_and_events(tmp)
        test_rotation_and_retention(tmp)
        test_no_collector_never_connects(tmp)
        test_collector_delivery_outage_and_catchup(tmp)
        test_live_broker_nonblocking_and_cli(tmp)
        test_owner_services_and_sites_counts(tmp)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'OK: 0 failure(s)' if not FAILS else f'FAIL: {len(FAILS)} failure(s): ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
