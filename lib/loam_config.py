"""The install's configuration: where everything is, who it is for, and when.

One file, ``/etc/loam/loam.toml`` (or ``$LOAM_CONFIG``), read once at import.
Every path, the user, the owner, the vault's folder names, the time zone and
the commit identity the runtime uses come from here, so a second install is
a second file and not a fork. ``lib/loam_broker`` and ``lib/loam_engineer``
re-export the paths under their historical names, so the ~40 modules that
import those two need no change at their call sites.

Added 2026-11-14 (first release, job L1). The rule for a missing key is the
fresh install's layout, below in ``_DEFAULTS``; a box that predates the file
carries its own values in it, so that on that box no value moved when this
module landed (the file came first, the code second). On the first install the
state paths in the file are the checkout's ``state/``, ``logs/`` and the rest
until ``bin/move-state`` has run; the move rewrites the file, and from then
on nothing the runtime writes is inside the checkout.

The checkout itself (``paths.root``) is named in the file rather than taken
from ``__file__`` on an install that has one, because code running in an
engineer job's tree must still mean the live runtime when it says "the
runtime" (provision run from a tree writes the live agents' configs, and
those must point at the live checkout, never at a tree that is deleted when
the job ends). With no file, it is this file's own checkout.

Nothing here is a secret. ``paths.secrets_env`` and ``paths.wake_env`` name
the files that hold them; their values are never read by this module.
"""

from __future__ import annotations

import os
import pwd
import tomllib
from pathlib import Path

CONFIG_FILE = Path(os.environ.get("LOAM_CONFIG") or "/etc/loam/loam.toml")

_CHECKOUT = Path(__file__).resolve().parent.parent


def _load() -> dict:
    try:
        return tomllib.loads(CONFIG_FILE.read_text())
    except FileNotFoundError:
        return {}


RAW = _load()


def get(dotted: str, default=None):
    """A value by its dotted key (``"paths.vault"``), or ``default``."""
    node = RAW
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


# -- who ---------------------------------------------------------------------

USER = get("user") or pwd.getpwuid(os.getuid()).pw_name
_pw = pwd.getpwnam(USER)
UID = _pw.pw_uid
GID = _pw.pw_gid
HOME = Path(get("paths.home") or _pw.pw_dir)


def _path(key: str, default: Path) -> Path:
    value = get(key)
    return Path(value).expanduser() if value else default


# -- where: the code, the record, the person's layers --------------------------

ROOT = _path("paths.root", _CHECKOUT)                  # the runtime checkout
VAULT = _path("paths.vault", HOME / "vault")
WORKSPACES = _path("paths.workspaces", HOME / "ai-labs")
PROJECTS = _path("paths.projects", HOME / "projects")
PLUGINS = _path("paths.plugins", HOME / "plugins")

# -- where: what the runtime writes (never inside ROOT on a fresh install) ----

STATE = _path("paths.state", HOME / ".local" / "state" / "loam")
LOGS = _path("paths.logs", STATE / "logs")
JOBS = _path("paths.jobs", STATE / "jobs")              # engineer job folders
SECRETS_ENV = _path("paths.secrets_env", HOME / ".config" / "loam" / "secrets.env")
WAKE_ENV = _path("paths.wake_env", HOME / ".config" / "loam" / "wake.env")
MCP_VENV = _path("paths.mcp_venv", HOME / ".local" / "share" / "loam" / "mcp-venv")
PYCACHE = _path("paths.pycache", HOME / ".cache" / "loam" / "pycache")
# The reviewer's own state: its key, because the reviewer's provisioned files
# are committed (reviewer/) and name it, so it is the one state path that must
# not change after it is set.
REVIEWER_STATE = _path("paths.reviewer_state", STATE / "reviewer")

# -- where: the user's own tools -----------------------------------------------

LOCAL_BIN = HOME / ".local" / "bin"
CLAUDE_BIN = _path("bin.claude", LOCAL_BIN / "claude")
CODEX_BIN = _path("bin.codex", LOCAL_BIN / "codex")
AGY_BIN = _path("bin.agy", LOCAL_BIN / "agy")
UV_BIN = _path("bin.uv", LOCAL_BIN / "uv")
CLAUDE_HOME = HOME / ".claude"
USER_UNIT_DIR = HOME / ".config" / "systemd" / "user"
# The user manager's cgroup, where every lane's memory is counted.
USER_SLICE = Path(f"/sys/fs/cgroup/user.slice/user-{UID}.slice/"
                  f"user@{UID}.service")
RUNTIME_DIR = Path(f"/run/user/{UID}")
# The PATH a unit or a lane starts with: the user's tools first.
UNIT_PATH = f"{LOCAL_BIN}:/usr/local/bin:/usr/bin:/bin"

# -- memory: no ceilings, an order --------------------------------------------------
# No agent run has a memory or swap ceiling (2026-11-16, the owner: "remove
# all of these limits on the different agents, whether personas or
# engineers"). Admission by memory decides when a run starts; what decides
# who goes if the box truly runs out is the kernel's OOM order, and these two
# numbers set it. The kernel kills the process with the highest share of
# RAM + swap in per mille plus its oom_score_adj, so a 700-point gap means a
# core process is taken before an agent only if it alone holds 70% of the
# box more than the agent does -- that is, only if it is itself the runaway.
#
#   AGENT  every agent run: an engineer job, a wake, a cron, the reviewer, a
#          Remote Control server, a chat the broker starts (`choom`), and the
#          provider smoke runs. One number, so between agents the largest goes.
#   CORE   the session broker's own process and the plugins gateway. 100 is
#          the lowest a user unit can take: the user manager runs at 100
#          (systemd 252) and an unprivileged process cannot go below the
#          floor it inherited. Caddy, the claim service, Authelia and the app
#          are the system's and Docker's, at 0, lower still.
AGENT_OOM_SCORE_ADJ = 800
CORE_OOM_SCORE_ADJ = 100

# -- whose -----------------------------------------------------------------------

OWNER_ID = get("owner.id", "owner")
OWNER_NAME = get("owner.name", "the owner")
OWNER_LINK = get("owner.link", f'"[[{OWNER_NAME}]]"')
# Ids an install used for its owner before `owner.id` existed
# (`owner.legacy_ids`, 2026-11-14, first release L2). Accepted as the owner's,
# coming in, so an app or a record that still says one keeps working;
# everything the runtime writes says OWNER_ID. Records already on disk keep
# the old id, which is why a comparison is is_owner() and never `== OWNER_ID`.
# A new install has none.
LEGACY_OWNER_IDS = tuple(str(x).strip().lower() for x in (get("owner.legacy_ids") or ())
                         if str(x).strip())


def is_owner(value) -> bool:
    """Whether an id on the wire or in a record is the owner's."""
    if not isinstance(value, str):
        return False
    who = value.strip().lower()
    return bool(who) and (who == OWNER_ID.lower() or who in LEGACY_OWNER_IDS)


def owner() -> dict:
    """What `GET /v1/owner` answers: the app's whole knowledge of the owner."""
    return {"id": OWNER_ID, "name": OWNER_NAME,
            "accepts": sorted({OWNER_ID, *LEGACY_OWNER_IDS})}

TIMEZONE = get("timezone", "UTC")

# The schedules' zone token (2026-11-14, first release L3): the word a
# schedule ends with, `daily at 05:00 PT`. The install writes its own, and
# accepts its own, these short names, `UTC`, or any IANA zone spelled out.
# "PT" was the only one accepted until this day, and every record on
# the first install says it, so it always means America/Los_Angeles.
ZONE_NAMES = {"PT": "America/Los_Angeles", "MT": "America/Denver",
              "CT": "America/Chicago", "ET": "America/New_York",
              "UTC": "UTC"}
ZONE_TOKEN = get("timezone_token") or {
    zone: token for token, zone in ZONE_NAMES.items()}.get(TIMEZONE, TIMEZONE)


# The zone in a unit's description, "at 04:20 Pacific".
ZONE_WORD = {"America/Los_Angeles": "Pacific", "America/Denver": "Mountain",
             "America/Chicago": "Central", "America/New_York": "Eastern",
             }.get(TIMEZONE, TIMEZONE)


def zone_of(token: str) -> str | None:
    """The IANA zone a schedule's zone token names, or None if it names none."""
    if not token:
        return None
    if token.upper() in ZONE_NAMES:
        return ZONE_NAMES[token.upper()]
    if token == ZONE_TOKEN:
        return TIMEZONE
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError  # noqa: PLC0415
    try:
        ZoneInfo(token)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return token

# Two identities. `git.identity` is the one Loam's own releases and the
# `loam` repository's root commit are made under (a Loam HQ bot, never a
# person's address: decided 2026-11-14); unset until that bot exists.
# `git.autocommit` is the box's own, on the vault, workspaces and plugins
# commits every 15 minutes (`bin/*-autocommit`).
GIT_NAME = get("git.identity.name")
GIT_EMAIL = get("git.identity.email")
AUTOCOMMIT_NAME = get("git.autocommit.name", "loam")
AUTOCOMMIT_EMAIL = get("git.autocommit.email", f"loam@{USER}")

# Who may brief the platform engineer (2026-11-14, first release L6): the
# personas named, lower-cased; empty means every persona
# (lib/loam_engineer.py, "Who may brief which project").
_briefers = get("engineer.briefers") or []
ENGINEER_BRIEFERS: tuple[str, ...] = tuple(
    str(n).strip().lower() for n in
    ([_briefers] if isinstance(_briefers, str) else _briefers) if str(n).strip())
# The agent a probe or a report defaults to (2026-11-15): the first briefer
# named, else the starter agent every install has.
LEAD_AGENT = ENGINEER_BRIEFERS[0] if ENGINEER_BRIEFERS else "iris"

# -- the vault's own folders (relative to VAULT) -------------------------------

_VAULT_DEFAULTS = {
    "inbox": "Inbox",
    "boards": "Inbox/boards",
    "general_board": "Inbox/boards/general.md",
    "conversations": "Conversations",
    "projects": "Projects",
    "attachments": "Attachments",
    "system": "System",
}
VAULT_FOLDERS = {k: get(f"vault.{k}", v) for k, v in _VAULT_DEFAULTS.items()}


def vault_folder(name: str) -> str:
    """A vault folder's name, relative to the vault (``"conversations"``)."""
    return VAULT_FOLDERS[name]


def expand(text: str) -> str:
    """A committed file's path tokens, filled for this install: ``@VAULT@``,
    ``@WORKSPACES@``, ``@PROJECTS@``, ``@ROOT@``, ``@STATE@``, ``@HOME@``.
    How a record in deploy/ names a place without naming this box."""
    for token, value in (("@VAULT@", VAULT), ("@WORKSPACES@", WORKSPACES),
                         ("@PROJECTS@", PROJECTS), ("@STATE@", STATE),
                         ("@ROOT@", ROOT), ("@HOME@", HOME)):
        text = text.replace(token, str(value))
    return text


# Every key this module answers for, for ``bin/loam-config`` and the docs.
def as_dict() -> dict:
    return {
        "user": USER, "uid": UID,
        "paths.home": str(HOME), "paths.root": str(ROOT),
        "paths.vault": str(VAULT), "paths.workspaces": str(WORKSPACES),
        "paths.projects": str(PROJECTS), "paths.plugins": str(PLUGINS),
        "paths.state": str(STATE), "paths.logs": str(LOGS),
        "paths.jobs": str(JOBS), "paths.secrets_env": str(SECRETS_ENV),
        "paths.wake_env": str(WAKE_ENV), "paths.mcp_venv": str(MCP_VENV),
        "paths.pycache": str(PYCACHE),
        "paths.reviewer_state": str(REVIEWER_STATE),
        "bin.claude": str(CLAUDE_BIN), "bin.codex": str(CODEX_BIN),
        "bin.agy": str(AGY_BIN), "bin.uv": str(UV_BIN),
        "owner.id": OWNER_ID, "owner.name": OWNER_NAME,
        "owner.link": OWNER_LINK, "timezone": TIMEZONE,
        "timezone_token": ZONE_TOKEN, "timezone_word": ZONE_WORD,
        "git.identity.name": GIT_NAME or "", "git.identity.email": GIT_EMAIL or "",
        "git.autocommit.name": AUTOCOMMIT_NAME,
        "git.autocommit.email": AUTOCOMMIT_EMAIL,
        "engineer.briefers": ",".join(ENGINEER_BRIEFERS),
        **{f"vault.{k}": v for k, v in VAULT_FOLDERS.items()},
    }
