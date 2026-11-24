"""Read the installation layout, owner, time zone and vault folders from
/etc/loam/loam.toml or LOAM_CONFIG. Missing settings take the fresh-install
defaults. The configured root also works when code is running from a job checkout.
Store paths to credentials here, never their values.
"""

from __future__ import annotations

import os
import pwd
import tomllib
from pathlib import Path

INSTALL_CONFIG_FILE = Path("/etc/loam/loam.toml")
CONFIG_FILE = Path(os.environ.get("LOAM_CONFIG") or str(INSTALL_CONFIG_FILE))

_CHECKOUT = Path(__file__).resolve().parent.parent

try:
    import loam_offline as _loam_offline
    _loam_offline.offline()
except ImportError:
    pass


def is_install_config() -> bool:
    """True when this process reads the host's own configuration, not a test's
    scratch LOAM_CONFIG."""
    return CONFIG_FILE.resolve() == INSTALL_CONFIG_FILE.resolve()


def _install_live_paths() -> dict[str, Path]:
    """The host install's live mutable paths (from /etc/loam/loam.toml or the
    install user's defaults), independent of LOAM_CONFIG."""
    try:
        raw = tomllib.loads(INSTALL_CONFIG_FILE.read_text())
    except OSError:
        raw = {}
    paths = raw.get("paths") if isinstance(raw.get("paths"), dict) else {}
    if paths.get("home"):
        home = Path(str(paths["home"])).expanduser()
    else:
        user = raw.get("user")
        try:
            home = Path(pwd.getpwnam(user).pw_dir) if user else Path(pwd.getpwuid(os.getuid()).pw_dir)
        except KeyError:
            home = Path.home()

    def _p(key: str, default: Path) -> Path:
        v = paths.get(key)
        return (Path(str(v)).expanduser() if v else default).resolve()

    state = _p("state", home / ".local" / "state" / "loam")
    return {
        "home": home.resolve(),
        "vault": _p("vault", home / "vault"),
        "workspaces": _p("workspaces", home / "ai-labs"),
        "projects": _p("projects", home / "projects"),
        "plugins": _p("plugins", home / "plugins"),
        "state": state,
        "logs": _p("logs", state / "logs"),
        "jobs": _p("jobs", state / "jobs"),
        "reviewer_state": _p("reviewer_state", state / "reviewer"),
        "secrets_env": _p("secrets_env", home / ".config" / "loam" / "secrets.env"),
        "wake_env": _p("wake_env", home / ".config" / "loam" / "wake.env"),
        "user_unit_dir": _p("user_unit_dir", home / ".config" / "systemd" / "user"),
    }


def install_state_dir() -> Path:
    """The host install's live state folder (from /etc/loam/loam.toml or the
    current user's default), independent of LOAM_CONFIG."""
    return _install_live_paths()["state"]


def install_plugins_dir() -> Path:
    """The host install's live Yours plugins folder (from /etc/loam/loam.toml or
    the current user's default), independent of LOAM_CONFIG."""
    return _install_live_paths()["plugins"]


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
_data_dir = get("storage.data_dir")
DATA_DIR: Path | None = Path(_data_dir).expanduser() if _data_dir else None

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

# The platform engineer's rendered files, relative to the checkout (bin/provision
# --engineer writes them). Never in a release and never committed: provision
# puts them in the checkout's `info/exclude`, a job's tree gets them copied in
# (loam_worktree.seed_harness), and an update's clean-tree rule passes over
# them (loam_update.dirt).
ENGINEER_HARNESS = tuple(f"engineer/{name}" for name in
                         ("CLAUDE.md", "role.md", "project.md", "agent.json", ".claude"))

REVIEWER_HARNESS = tuple(f"reviewer/{name}" for name in
                         ("CLAUDE.md", "agent.json", ".mcp.json", ".claude/settings.json"))

# -- where: the user's own tools -----------------------------------------------

LOCAL_BIN = HOME / ".local" / "bin"
CLAUDE_BIN = _path("bin.claude", LOCAL_BIN / "claude")
CODEX_BIN = _path("bin.codex", LOCAL_BIN / "codex")
AGY_BIN = _path("bin.agy", LOCAL_BIN / "agy")
UV_BIN = _path("bin.uv", LOCAL_BIN / "uv")
CLAUDE_HOME = HOME / ".claude"
USER_UNIT_DIR = _path("paths.user_unit_dir", HOME / ".config" / "systemd" / "user")


def _guard_test_config() -> None:
    """Refuse any test process that loads the live install config or still points
    any mutable path (vault, workspaces, plugins, state, logs, jobs, reviewer
    state, secrets, or user systemd units) at the host install.

    Production processes and `tests/live_*.py` have `loam_offline.offline() == False`.
    Run tests via `python3 tests/run-all <test.py>` or supply a scratch `LOAM_CONFIG`
    whose paths (or `paths.home`) are outside the live install.
    """
    if os.environ.get("LOAM_TEST_INSTALL_CONFIG"):
        return
    try:
        import loam_offline  # noqa: PLC0415
    except ImportError:
        return
    if not loam_offline.offline():
        return
    if is_install_config():
        raise loam_offline.Offline(
            f"refused in a test: loam_config would load the live install config "
            f"({INSTALL_CONFIG_FILE}); run via `python3 tests/run-all <test.py>` "
            f"or point LOAM_CONFIG at a scratch loam.toml (lib/loam_config.py)"
        )
    live = _install_live_paths()
    configured = {
        "vault": VAULT,
        "workspaces": WORKSPACES,
        "projects": PROJECTS,
        "plugins": PLUGINS,
        "state": STATE,
        "logs": LOGS,
        "jobs": JOBS,
        "reviewer_state": REVIEWER_STATE,
        "secrets_env": SECRETS_ENV,
        "wake_env": WAKE_ENV,
        "user_unit_dir": USER_UNIT_DIR,
    }
    leaked = []
    for name, path in configured.items():
        cur = path.expanduser().resolve()
        host = live[name]
        if cur == host or host in cur.parents:
            leaked.append(f"paths.{name}={cur}")
    if leaked:
        raise loam_offline.Offline(
            f"refused in a test: scratch LOAM_CONFIG ({CONFIG_FILE}) still reaches "
            f"live install path(s): {', '.join(leaked)}; set paths.home or override "
            f"those paths in the scratch config (lib/loam_config.py)"
        )


_guard_test_config()
# The user manager's cgroup, where every lane's memory is counted.
USER_SLICE = Path(f"/sys/fs/cgroup/user.slice/user-{UID}.slice/"
                  f"user@{UID}.service")
RUNTIME_DIR = Path(f"/run/user/{UID}")
# Shared output directory bind-mounted into the persona browser container so
# screenshots and snapshots land on the host at the exact path the tool returns.
BROWSER_OUTPUT = Path("/var/lib/loam/browser")
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
#          chat the broker starts (`choom`), and the provider smoke runs. One number, so between agents the largest goes.
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
# A file over this many megabytes (10^6 bytes) is kept out of those commits
# and named instead (lib/loam_autocommit.py): GitHub refuses a file over
# 100 MiB, and one refused file would stop every later backup push.
AUTOCOMMIT_MAX_FILE_MB = int(get("git.autocommit.max_file_mb", 100))

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
    "boards": "Boards",
    "conversations": "Conversations",
    "projects": "Projects",
    "attachments": "Attachments",
    "system": "System",
}
VAULT_FOLDERS = {k: get(f"vault.{k}", v) for k, v in _VAULT_DEFAULTS.items()}


def vault_folder(name: str) -> str:
    """A vault folder's name, relative to the vault (``"conversations"``)."""
    return VAULT_FOLDERS[name]


def engineer_vault_dir() -> str:
    """Vault-relative folder for the platform engineer's role and project files.

    Defaults to ``<system>/engineer`` (inside Loam files) so standard installs
    keep engineer machinery out of the owner's Projects folder. Explicit
    ``vault.engineer_dir`` overrides; an install with a non-default
    ``vault.projects`` (e.g. a legacy ``40_PROJECTS`` vault) keeps
    ``<projects>/Loam/engineer``.
    """
    explicit = get("vault.engineer_dir")
    if explicit:
        return str(explicit).strip("/")
    projects = vault_folder("projects")
    if projects != _VAULT_DEFAULTS["projects"]:
        return f"{projects}/Loam/engineer"
    return f"{vault_folder('system')}/engineer"


def schedule_page() -> str:
    """Vault-relative path of the generated schedule table.

    Defaults to ``<system>/schedule.md`` (inside Loam files) so standard
    installs keep generated timer tables out of the owner's Projects folder.
    Explicit ``vault.schedule_page`` overrides; an install with a non-default
    ``vault.projects`` keeps ``<projects>/Loam/infrastructure/schedule.md``.
    """
    explicit = get("vault.schedule_page")
    if explicit:
        return str(explicit).strip("/")
    projects = vault_folder("projects")
    if projects != _VAULT_DEFAULTS["projects"]:
        return f"{projects}/Loam/infrastructure/schedule.md"
    return f"{vault_folder('system')}/schedule.md"


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
        "storage.data_dir": str(DATA_DIR) if DATA_DIR else "",
        "bin.claude": str(CLAUDE_BIN), "bin.codex": str(CODEX_BIN),
        "bin.agy": str(AGY_BIN), "bin.uv": str(UV_BIN),
        "owner.id": OWNER_ID, "owner.name": OWNER_NAME,
        "owner.link": OWNER_LINK, "timezone": TIMEZONE,
        "timezone_token": ZONE_TOKEN, "timezone_word": ZONE_WORD,
        "git.identity.name": GIT_NAME or "", "git.identity.email": GIT_EMAIL or "",
        "git.autocommit.name": AUTOCOMMIT_NAME,
        "git.autocommit.email": AUTOCOMMIT_EMAIL,
        "git.autocommit.max_file_mb": str(AUTOCOMMIT_MAX_FILE_MB),
        "engineer.briefers": ",".join(ENGINEER_BRIEFERS),
        **{f"vault.{k}": v for k, v in VAULT_FOLDERS.items()},
    }
