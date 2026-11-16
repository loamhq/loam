"""Each provider's usage reading, and the one loop that keeps them fresh.

Added 2026-10-14. Until then both readings were observe-only: a reading
existed when a session happened to emit one (Claude's ``rate_limit_event``,
Codex's ``account/rateLimits/updated``), so nothing refreshed it between
turns, a project engineer on ``codex exec`` spent the shared meter and the
row never moved, and Codex's sparse merge kept a five-hour window at 80% for
two days after a plan change had removed that window altogether (the
diagnostic in receipt 20261014-122554-e7908820).

The contract, one per provider (``Reader``):

- ``observe(event)``: what a session volunteers, merged over the kept reading
  -- a sparse update moves what it states and keeps the rest;
- ``read()``: optional, an on-demand snapshot that REPLACES the kept reading
  whole, plan and credits included, or None when the read failed (the kept
  reading then stays as it was);
- ``kept()``: what the row serves.

Every reading carries ``seen_at`` and ``source``, ``"read"`` or ``"event"``.

Around them ``Refresh``, which knows nothing about any provider: a read on a
``/v1/providers`` fetch, at most once a minute per provider; every five
minutes while that provider has a live session or a running engineer job;
and once when a job on it ends. Never otherwise -- a box nobody is using
reads nothing. A provider whose reader has no ``read`` is observe-only, and
``Refresh.reason`` says so for its row.

Stdlib only. Nothing here may fail a fetch: a read that raises is a read that
returned None.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

FETCH_EVERY_S = 60
LIVE_EVERY_S = 300
# How often the broker looks at who is active. Cheap -- a stat per job
# directory -- and it is what bounds how late the read after a job's end is.
POLL_S = 30
# A job is running while its heartbeat ticks and it has no verdict; the
# launcher's own rule (bin/engineer-job ``STILL_RUNNING_SECONDS``).
JOB_STALE_S = 120
# What runs a job, as the job directory's ``runtime`` file names it, to the
# provider whose meter it spends.
RUNTIME_PROVIDERS = {"claude": "claude-code", "codex": "codex"}

SOURCES = ("read", "event")

# Set to "off", no read is ever due. For tests: a read is a real CLI on the
# box's real login, and a test's broker object must not make one.
OFF_ENV = "LOAM_USAGE_READS"


def merge(prior: dict | None, reading: dict) -> dict:
    """``reading`` over ``prior``: what it states moves, the rest stays. A
    window that is a dict is replaced whole, never merged key by key."""
    out = dict(prior) if isinstance(prior, dict) else {}
    out.update(reading)
    return out


@dataclass
class Reader:
    """One provider's three verbs. ``read`` is None for an observe-only one."""
    provider: str
    observe: Callable[[dict], dict | None]
    kept: Callable[[], dict | None]
    read: Callable[[], dict | None] | None = None


def running_jobs(roots: list[Path], now: float | None = None) -> dict[str, str]:
    """``{job_id: provider}`` for every engineer job running under ``roots``:
    a heartbeat within ``JOB_STALE_S`` and no ``result.json``. A job whose
    runtime is not one we know spends no meter we read, and is left out."""
    now = time.time() if now is None else now
    out: dict[str, str] = {}
    for root in roots:
        try:
            dirs = list(root.iterdir())
        except OSError:
            continue
        for d in dirs:
            try:
                if now - (d / "heartbeat").stat().st_mtime >= JOB_STALE_S:
                    continue
                if (d / "result.json").exists():
                    continue
                runtime = (d / "runtime").read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if runtime in RUNTIME_PROVIDERS:
                out[d.name] = RUNTIME_PROVIDERS[runtime]
    return out


class Refresh:
    """When to call each reader's ``read``. The two inputs are functions so
    the loop can be driven by a test's clock and a test's activity:
    ``live()`` is the providers with a live session, ``jobs()`` the running
    engineer jobs as ``{job_id: provider}``."""

    def __init__(self, readers: list[Reader],
                 live: Callable[[], set[str]] = set,
                 jobs: Callable[[], dict[str, str]] = dict,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.readers = {r.provider: r for r in readers}
        self.live = live
        self.jobs = jobs
        self.clock = clock
        self.last_read: dict[str, float] = {}
        self.known_jobs: dict[str, str] | None = None
        self.lock = threading.Lock()
        # Providers a fetch has read in line once; after that a fetch's read
        # runs behind it (on_fetch). last_thread is for tests to join.
        self.ever_read: set[str] = set()
        self.last_thread: threading.Thread | None = None

    def reason(self, provider: str) -> str | None:
        """Why a row cannot be refreshed on demand, or None when it can."""
        reader = self.readers.get(provider)
        if reader is not None and reader.read is None:
            return (f"observe-only: {provider} states its usage on a turn and "
                    f"has no on-demand read")
        return None

    def _claim(self, provider: str, every: float | None) -> bool:
        """Whether a read is due, and if so mark it taken now, under the lock
        so two fetches at once make one read. ``every`` None is due always."""
        reader = self.readers.get(provider)
        if reader is None or reader.read is None \
                or os.environ.get(OFF_ENV) == "off":
            return False
        with self.lock:
            now = self.clock()
            last = self.last_read.get(provider)
            if every is not None and last is not None and now - last < every:
                return False
            self.last_read[provider] = now
            return True

    def read(self, provider: str) -> dict | None:
        """One read, unconditionally: the new reading, or None when the
        provider has none to give or the read failed."""
        reader = self.readers.get(provider)
        if reader is None or reader.read is None:
            return None
        try:
            return reader.read()
        except Exception:  # noqa: BLE001 - a failed read keeps what was kept
            return None

    def on_fetch(self) -> list[str]:
        """A ``/v1/providers`` fetch: each provider read at most once a
        minute. Answers who was read, for the tests and the log.

        The read happens BEHIND the fetch (2026-11-09), not in it: a read is
        a CLI process (~1 s Codex, ~2-3.5 s Claude Code), and in it, the
        first app open of every minute waited 2.3 s for its rows, measured on
        the box; the next fetch, 6 ms. So the fetch serves what is kept and
        the fresh reading lands for the next one, which is at most the
        minute this already allowed. Only a provider never read by this
        process is read in line, so a fresh broker's first row is not empty."""
        done = [p for p in self.readers if self._claim(p, FETCH_EVERY_S)]
        inline = [p for p in done if p not in self.ever_read]
        behind = [p for p in done if p in self.ever_read]
        for provider in inline:
            self.ever_read.add(provider)
            self.read(provider)
        if behind:
            t = threading.Thread(target=lambda: [self.read(p) for p in behind],
                                 name="provider-usage-read", daemon=True)
            t.start()
            self.last_thread = t
        return done

    def tick(self, live: set[str] | None = None) -> list[str]:
        """One look at activity, every ``POLL_S``: a read for a provider
        with something live that has not been read in five minutes, and a
        read for each provider one of whose jobs has ended since the last
        look. The first look only learns which jobs exist. ``live`` may be
        passed in, by a caller that must take it on its own thread."""
        jobs = self.jobs()
        ended: set[str] = set()
        if self.known_jobs is not None:
            ended = {p for j, p in self.known_jobs.items() if j not in jobs}
        self.known_jobs = dict(jobs)
        active = set(self.live() if live is None else live) | set(jobs.values())
        done = [p for p in self.readers
                if (p in ended and self._claim(p, None))
                or (p in active and self._claim(p, LIVE_EVERY_S))]
        for provider in done:
            self.read(provider)
        return done


def write_json(path: Path, data: dict) -> None:
    """Atomically, so a fetch never reads half a reading."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)
