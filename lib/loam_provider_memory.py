"""Every provider's own memory, pinned off, and checked (first release L8).

Memory is Loam's on every provider (the owner, 2026-11-14): the soul, the
config, the loading files, the thread summaries. A provider's native memory
would be a second memory nobody reads and nothing governs, so each one is
turned off by its own setting and its store is checked for growth.

What each provider has, as read on this box (2026-11-14):

- **Claude Code 2.1.325.** Auto memory (`autoMemoryEnabled`, read and write
  of `~/.claude/projects/<cwd>/memory/`), and background consolidation
  ("auto-dream", `autoDreamEnabled`, "When set, overrides the server-side
  default", so unset is not off). Both pinned false in the user settings,
  and `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` as a second lock, in the settings'
  `env` and in the user manager's environment (every lane's process).
- **Codex 0.169.0.** The `memories` feature (stable, default false),
  `chronicle` and `external_agent_memory_import` (under development, false),
  the `[memories]` table's `generate_memories` / `use_memories`, and the
  prompt history (`history.persistence`). All pinned in `CODEX_HOME/config.toml`,
  which every Codex lane shares (lib/loam_codex.trust_config renders it).
  Codex ignores a key it does not know, so a pin that a later Codex renames
  does nothing silently; the store check below is what notices.
- **Antigravity 1.5.9.** Its binary carries knowledge items and agent
  memory, and no setting its help, its bundled guide, its changelog or its
  live CLI reference names turns them off. **Here "pinned off" means
  "checked empty"**: nothing is written, and the check reads its stores.
  The check runs at every session start, and after `provider-pin apply`
  moves a CLI to a new release's pin.

The check counts each store and compares it with the count first seen,
kept in `<state>/provider-memory.json`, and says so once per growth
(the new count becomes the one compared). A store that grew is a provider
writing memory of its own, and the caller says so: as a fact for the
system persona (lib/loam_attention.py), by the broker at a session start
(and `provider-pin apply` says it after moving a CLI).

Antigravity's stores leave out what Loam's engineer jobs' own
conversations made (2026-11-15, lib/loam_agy_files.py): the lead persona
tested 11-15 that the CLI has no memory tools and nothing carries across
conversations, so a job's `brain/<id>/`, its summary row and the implicit
files that appeared while it ran are its transcript and a `/resume` index,
cleared a week after the job. Everything else in those stores is counted,
and its growth is said, as before. (Since 2026-11-16 every other
conversation is cleared too, once idle a week.)

Then not even that (2026-11-16): the owner read three such lines, posted in
a fresh chat after his first message to a persona on Antigravity, as an
error. Every Antigravity conversation, a persona's as much as a job's, makes
the same per-conversation files, and the owner's 11-15 decision made them
accepted working state. So the three per-conversation stores (``WORKING``)
are still counted, for the log, and their baseline kept current, but their
growth is never said. What the check says for Antigravity now is anything
under its tree that is none of the kinds known on 2026-11-16
(``AGY_KNOWN``): a new store a later build adds is the one thing a memory of
its own would look like. And nothing it says goes into a conversation: the
broker hands a growth to the system persona as a fact, never a line in the
session's thread or on the board (2026-11-19).
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path

import loam_config as lc

# -- the pins ---------------------------------------------------------------------

CLAUDE_SETTINGS = {"autoMemoryEnabled": False, "autoDreamEnabled": False,
                   # No "Co-Authored-By: Claude" or "Generated with Claude
                   # Code" on anything a persona commits or opens (2026-11-18,
                   # lib/loam_git_identity.py): the same value a job is handed.
                   "attribution": {"commit": "", "pr": "", "sessionUrl": False},
                   "includeCoAuthoredBy": False}
CLAUDE_ENV = {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}

# Lines of CODEX_HOME/config.toml, after its top-level keys.
CODEX_TOML = """\
# Codex's own memory, off (lib/loam_provider_memory.py): memory is Loam's.
[features]
memories = false
chronicle = false
external_agent_memory_import = false

[memories]
generate_memories = false
use_memories = false

[history]
persistence = "none"
"""

# The same pins as `-c` overrides, for a Codex run that ignores the home
# config (an engineer job: lib/loam_codex_job.py). Valid under
# --strict-config on 0.169.0; an unknown key there is a refusal.
CODEX_OVERRIDES = ("features.memories=false", "features.chronicle=false",
                   "features.external_agent_memory_import=false",
                   "memories.generate_memories=false",
                   "memories.use_memories=false",
                   'history.persistence="none"')

ENV_D = lc.HOME / ".config" / "environment.d" / "60-loam-memory.conf"

# `LOAM_PROVIDER_MEMORY_BASELINE` (2026-11-15) for a test: without it
# tests/test_provider_follow.py compared the box's real stores with the
# box's real baseline, and wrote it, so any real Antigravity run between two
# test runs failed the test (and the test moved the box's baseline).
BASELINE = Path(os.environ.get("LOAM_PROVIDER_MEMORY_BASELINE")
                or lc.STATE / "provider-memory.json")


def claude_settings_gaps(live: dict) -> list[str]:
    gaps = [k for k, v in CLAUDE_SETTINGS.items() if live.get(k) != v]
    env = live.get("env") or {}
    gaps += [f"env.{k}" for k, v in CLAUDE_ENV.items() if env.get(k) != v]
    return gaps


def pin_claude(path: Path | None = None) -> list[str]:
    """The Claude Code pins in the user settings; what was written."""
    path = path or lc.CLAUDE_HOME / "settings.json"
    try:
        live = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        live = {}
    gaps = claude_settings_gaps(live)
    if not gaps:
        return []
    live.update(CLAUDE_SETTINGS)
    live["env"] = {**(live.get("env") or {}), **CLAUDE_ENV}
    tmp = path.with_name(path.name + ".memory-tmp")
    # A fresh install pins before anyone has signed in, when ~/.claude/ does
    # not exist yet (found in the first-release L12 rehearsal).
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(live, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return [f"{path}: {g}" for g in gaps]


def pin_env() -> list[str]:
    """The env lock in the user manager, for every unit it starts."""
    text = ("# lib/loam_provider_memory.py: Claude Code's auto memory, off.\n"
            + "".join(f"{k}={v}\n" for k, v in CLAUDE_ENV.items()))
    try:
        if ENV_D.read_text() == text:
            return []
    except OSError:
        pass
    ENV_D.parent.mkdir(parents=True, exist_ok=True)
    ENV_D.write_text(text)
    subprocess.run(["systemctl", "--user", "set-environment",
                    *(f"{k}={v}" for k, v in CLAUDE_ENV.items())],
                   capture_output=True, check=False)
    return [str(ENV_D)]


def pin_codex() -> list[str]:
    """Codex's pins live in the file the broker renders whole; rewrite it."""
    import loam_codex  # noqa: PLC0415
    hooks = loam_codex.read_trusted_hooks()
    if loam_codex.write_trust(loam_codex.persona_workspaces(), hooks):
        return [str(loam_codex.HOME_CONFIG)]
    return []


def pin_memory() -> list[str]:
    """Every provider's pins, written where they are not already held; what
    was written. Antigravity has none to write (see the module's note)."""
    return pin_claude() + pin_env() + pin_codex()


# -- the check --------------------------------------------------------------------

def _rows(db: Path, tables: tuple[str, ...], skip: set[str] | None = None,
          column: str = "conversation_id") -> int:
    """Rows in ``tables``, less those whose ``column`` is in ``skip``."""
    if not db.exists():
        return 0
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
    try:
        total = 0
        for table in tables:
            try:
                if skip:
                    total += sum(1 for (cid,) in con.execute(
                        f'SELECT "{column}" FROM "{table}"') if cid not in skip)
                else:
                    total += con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            except sqlite3.Error:
                pass
        return total
    finally:
        con.close()


def _entries(path: Path, skip: set[str] | None = None) -> int:
    """Entries in ``path``, less those named (a file's stem or a folder's
    name) in ``skip``."""
    try:
        return sum(1 for e in path.iterdir()
                   if not skip or (e.name not in skip and e.stem not in skip))
    except OSError:
        return 0


# The stores whose count may fall back (Antigravity's, as a week-old job's
# files are cleared): a fall moves the baseline down with it, so the next
# growth is measured from what is there. The other providers' are as before.
FALLS = ("antigravity ",)

# Antigravity's per-conversation files (2026-11-16, the module's note):
# accepted working state, counted and never said.
WORKING = frozenset({"antigravity brain entries", "antigravity implicit entries",
                     "antigravity conversation summaries"})

# Every entry of Antigravity's tree as read on 2026-11-16 (agy 1.5.9). One
# not named here is counted as "antigravity other entries", whose growth is
# said: a store a later build adds is what a memory of its own would be.
AGY_KNOWN = frozenset({
    "antigravity-oauth-token", "bin", "brain", "builtin", "cache", "cli.log",
    "conversations", "conversation_summaries.db", "conversation_summaries.db-shm",
    "conversation_summaries.db-wal", "conversation_summaries.db-journal",
    "crashes", "implicit", "installation_id", "jetbox_summaries_proto.pb",
    "jetski_state.pbtxt", "last_check.timestamp", "log", "mcp",
    "persistent_terminals", "presence", "updater"})


def stores() -> dict[str, int]:
    """Each provider memory store and how much is in it now."""
    import loam_codex  # noqa: PLC0415
    import loam_agy_files  # noqa: PLC0415
    gemini = loam_agy_files.root()
    claude = sum(_entries(d) for d in (lc.CLAUDE_HOME / "projects").glob("*/memory"))
    # What Loam's engineer jobs own on Antigravity: never counted.
    try:
        job_convs, job_implicit = loam_agy_files.owned()
    except Exception:  # noqa: BLE001 -- a check never fails for want of it
        job_convs, job_implicit = set(), set()
    return {
        "claude-code auto memory files": claude,
        "codex memories rows": _rows(loam_codex.CODEX_HOME / "memories_1.sqlite",
                                     ("stage1_outputs", "jobs")),
        "antigravity brain entries": _entries(gemini / "brain", job_convs),
        "antigravity implicit entries": _entries(gemini / "implicit", job_implicit),
        "antigravity conversation summaries": _rows(
            gemini / "conversation_summaries.db", ("conversation_summaries",),
            job_convs),
        "antigravity other entries": _entries(gemini, AGY_KNOWN),
    }


def check(provider: str | None = None) -> tuple[dict, list[str]]:
    """(the counts now, a sentence for each store that grew since first seen).
    ``provider`` narrows the sentences to one (`claude-code`, `codex`,
    `antigravity`); the counts are always all of them."""
    now = stores()
    try:
        seen = json.loads(BASELINE.read_text())
    except (OSError, json.JSONDecodeError):
        seen = {}
    grew = []
    for store, count in now.items():
        if store not in seen:
            seen[store] = count
            continue
        if store in WORKING:
            seen[store] = count    # working state: kept current, never said
            continue
        if count > seen[store] and (provider is None or store.startswith(provider)):
            grew.append(f"{store}: {count}, was {seen[store]}. A provider is "
                        f"keeping a memory of its own, and memory is Loam's "
                        f"(lib/loam_provider_memory.py).")
            seen[store] = count    # said once per growth, not at every start
        elif count < seen[store] and store.startswith(FALLS):
            seen[store] = count    # a job's files cleared: measured from here
    try:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        tmp = BASELINE.with_suffix(".tmp")
        tmp.write_text(json.dumps(seen, indent=2) + "\n")
        os.replace(tmp, BASELINE)
    except OSError:
        pass
    return now, grew
