"""Drive Codex sessions through its line-delimited app-server protocol. Read its login,
model catalog and limits from the configured provider home. Translate notifications
into broker events independently of the process.
"""

from __future__ import annotations

import base64
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path

import loam_config as lc

import loam_broker as cfg
import loam_provider_memory as pm
import loam_provider_usage as usage_store

# The pinned binary. One symlink into a versioned install, exactly the shape
# the Claude CLI's pin has: ``~/.local/share/codex/versions/<v>/`` is an npm
# prefix holding ``@openai/codex@<v>`` and nothing else, and this path points
# at its ``node_modules/.bin/codex``. Moving the pin is moving the symlink;
# the old version stays on disk and rollback is one ``ln -sfn``. No global
# npm package exists. See engineer/memory/MEMORY.md, 2026-10-01.
CODEX = str(lc.CODEX_BIN)

# Codex's own home: its login (``auth.json``), its config, its session
# rollouts and its sqlite state. Under the broker's state directory, which is
# runtime state (gitignored) and owned by the broker, so a login made from the
# app can never land in a persona's workspace or in ``~/.codex`` by accident.
# Every Codex process this runtime starts is given this as ``CODEX_HOME``;
# ``~/.codex`` is not used by anything here.
CODEX_HOME = cfg.STATE_DIR / "codex"
AUTH_PATH = CODEX_HOME / "auth.json"

# The variables that would make Codex bill an API account instead of the
# ChatGPT subscription the login is for. ``secrets.env`` on this box carries an
# ``OPENAI_API_KEY`` for other work, and an engineer job's environment has it
# (found 2026-10-01): a Codex child that inherited it could run on that key
# without anybody having chosen to. So they are removed, always, and the only
# credential a Codex session has is the login in ``CODEX_HOME``.
STRIPPED_ENV = ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL")

LOGIN_ARGV = ("login", "--device-auth")
STATUS_ARGV = ("login", "status")
APP_SERVER_ARGV = ("app-server",)

# How long a device code lives, as the CLI prints it ("expires in 15
# minutes", 0.159.3, 2026-10-01). The flow is ended at the same moment: a
# process polling for a code that can no longer be entered is a process
# holding nothing.
LOGIN_FLOW_TIMEOUT_S = 900
# How long the start route waits for the URL and the code. Both arrived in
# well under a second on this box; the margin is for a cold node start.
LOGIN_PRINT_TIMEOUT_S = 25

# Where the model list is kept between app-server starts. ``model/list`` does
# not need a login (measured 2026-10-01: answered with no auth.json), so the
# list can be read at broker start and on every Codex session's start.
MODELS_PATH = cfg.STATE_DIR / "codex-models.json"
MODELS_TIMEOUT_S = 30

# Codex's own catalogue of the login's models, in ``CODEX_HOME``, which Codex
# fetches and keeps itself. ``model/list`` states no context window (0.159.3,
# 2026-10-07: no window field on any model); this file does, per slug, as
# ``context_window`` and ``effective_context_window_percent``. Their product
# is the ``modelContextWindow`` a turn's ``thread/tokenUsage/updated`` states:
# 272000 at 95% is the 258400 every turn of session a9c8ec2c reported, Luna
# and Astra alike. Read, never written; named relative to ``CODEX_HOME``.
CATALOGUE_NAME = "models_cache.json"

# A device code as 0.159.3 prints it: ``XXXX-XXXXX``, letters and digits. The
# URL is the first https URL in the text; it is not anchored on a host for the
# reason ``loam_broker.URL_RE`` gives.
CODE_RE = re.compile(r"\b[A-Z0-9]{4}-[A-Z0-9]{4,8}\b")


def kill_group(pid: int, sig: int = signal.SIGKILL) -> None:
    """Signal the wrapper and native child together by process group. Launch each provider
    process in a new session so the group belongs to it.
    """
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def env(base: dict | None = None) -> dict:
    """The environment a Codex child runs in: the broker's own, with the API
    keys removed and ``CODEX_HOME`` set. ``CODEX_HOME`` is created here,
    private, because Codex refuses a home that does not exist."""
    out = dict(os.environ if base is None else base)
    for name in STRIPPED_ENV:
        out.pop(name, None)
    CODEX_HOME.mkdir(parents=True, exist_ok=True)
    os.chmod(CODEX_HOME, 0o700)
    out["CODEX_HOME"] = str(CODEX_HOME)
    return out


# -- the login -------------------------------------------------------------

def _jwt_claims(token) -> dict:
    """The claims of a JWT, unverified -- read for an email and a plan name,
    which are facts about whose login it is, and never for anything that
    decides access. No token or piece of one is returned."""
    if not isinstance(token, str) or token.count(".") != 2:
        return {}
    payload = token.split(".")[1]
    try:
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        claims = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def login_state() -> dict:
    """Read auth.json on every call so CLI refreshes are visible. Return account and plan
    from ID-token claims when present. Leave expiry and days_left unknown because the
    refresh token supplies no expiry.
    """
    base = {"source": "none", "connected": False, "reason": None,
            "account": None, "plan": None, "expires_at": None,
            "days_left": None}
    try:
        data = json.loads(AUTH_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {**base, "reason": "no login on the box"}
    except (OSError, json.JSONDecodeError) as exc:
        return {**base, "reason": f"the login could not be read ({type(exc).__name__})"}
    if not isinstance(data, dict):
        return {**base, "reason": "the login could not be read"}
    tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
    if tokens.get("access_token") or tokens.get("refresh_token"):
        claims = _jwt_claims(tokens.get("id_token"))
        auth = claims.get("https://api.openai.com/auth")
        auth = auth if isinstance(auth, dict) else {}
        email = claims.get("email")
        plan = auth.get("chatgpt_plan_type")
        return {**base, "source": "chatgpt", "connected": True,
                "account": email if isinstance(email, str) else None,
                "plan": plan if isinstance(plan, str) else None}
    if data.get("OPENAI_API_KEY"):
        # A key written into the login file by ``codex login --with-api-key``.
        # Not what this provider is for, and said rather than hidden.
        return {**base, "source": "api-key", "connected": True}
    return {**base, "reason": "the login holds no token"}


# `codex login status`, keyed on the file it reads, so the providers route
# does not start a node process on every call. A changed or removed file is a
# new key and a new reading.
_status_cache: tuple[tuple, str | None] | None = None


def _auth_key() -> tuple:
    try:
        stat = AUTH_PATH.stat()
    except OSError:
        return ("absent",)
    return (stat.st_size, stat.st_mtime_ns)


def status_line() -> str | None:
    """Return one line from codex login status, such as "Not logged in", or None if the
    command could not run.
    """
    global _status_cache
    key = _auth_key()
    if _status_cache is not None and _status_cache[0] == key:
        return _status_cache[1]
    try:
        done = subprocess.run([CODEX, *STATUS_ARGV], env=env(),
                              capture_output=True, text=True, timeout=20,
                              cwd=str(CODEX_HOME))
        lines = [line.strip() for line in
                 cfg.plain(done.stdout + "\n" + done.stderr).splitlines()
                 if line.strip()]
        line = lines[-1][:cfg.LOGIN_MESSAGE_CHARS] if lines else None
    except (OSError, subprocess.TimeoutExpired):
        line = None
    _status_cache = (key, line)
    return line


def sign_out() -> dict:
    """Run codex logout in the session's CODEX_HOME. Report whether auth.json was removed
    and return the CLI's message on failure. The caller reads fresh login state.
    """
    existed = AUTH_PATH.is_file()
    message = None
    try:
        done = subprocess.run([CODEX, "logout"], env=env(),
                              stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=60, cwd=str(CODEX_HOME))
        if done.returncode != 0:
            lines = [line.strip() for line in cfg.plain(
                done.stdout + "\n" + done.stderr).splitlines() if line.strip()]
            message = (lines[-1][:cfg.LOGIN_MESSAGE_CHARS] if lines else
                       f"codex logout exited {done.returncode}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        message = f"codex logout did not finish ({type(exc).__name__})"
    out = {"removed": existed and not AUTH_PATH.is_file()}
    if message:
        out["message"] = message
    return out


# -- what `codex login --device-auth` prints -------------------------------

def device_url(text: str) -> str | None:
    """The verification URL out of what the CLI printed."""
    match = cfg.URL_RE.search(cfg.plain(text))
    return match.group(0) if match else None


def device_code(text: str) -> str | None:
    """The one-time code out of what the CLI printed. Only looked for after
    the URL, so a code-shaped string in the banner cannot be it."""
    plain = cfg.plain(text)
    url = device_url(plain)
    if url is None:
        return None
    match = CODE_RE.search(plain, plain.index(url) + len(url))
    return match.group(0) if match else None


# -- models ----------------------------------------------------------------

def read_models() -> list[dict]:
    """The cached ``model/list`` answer, or ``[]`` when none has been read."""
    try:
        data = json.loads(MODELS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    models = data.get("models") if isinstance(data, dict) else None
    return [m for m in models if isinstance(m, dict)] if isinstance(models, list) else []


def write_models(raw: list) -> list[dict]:
    """Keep the parts of ``model/list`` the + menu draws from, and answer
    them. Hidden models are left out, as the picker leaves them out."""
    out = []
    for model in raw:
        if not isinstance(model, dict) or model.get("hidden"):
            continue
        ident = model.get("id") or model.get("model")
        if not isinstance(ident, str) or not ident:
            continue
        efforts = [e.get("reasoningEffort")
                   for e in model.get("supportedReasoningEfforts") or []
                   if isinstance(e, dict) and isinstance(e.get("reasoningEffort"), str)]
        out.append({"id": ident,
                    "name": model.get("displayName") or ident,
                    "description": model.get("description"),
                    "default": bool(model.get("isDefault")),
                    "efforts": efforts,
                    "default_effort": model.get("defaultReasoningEffort")})
    cfg.STATE_DIR.mkdir(parents=True, exist_ok=True)
    MODELS_PATH.write_text(json.dumps({"seen_at": cfg.now_iso(),
                                       "models": out}, indent=2),
                           encoding="utf-8")
    return out


def fetch_models() -> list[dict]:
    """Start an app-server, ask it ``model/list``, keep the answer.

    Blocking, and for a thread: the broker calls it once at start, off the
    loop. A live session refreshes the same cache from its own process, so
    this is only what the providers row says before any Codex session has
    run. Fail-soft: the cache stays what it was."""
    result = app_server_call("model/list", {}, MODELS_TIMEOUT_S)
    data = (result or {}).get("data")
    return write_models(data) if isinstance(data, list) else read_models()


# -- the persona, assembled for Codex --------------------------------------
#
# Added 2026-10-01, second Codex brief. The owner's rule: "just the provider
# changing does not compromise on the memories, the personalities, the soul,
# and the tools." On Claude Code a persona is assembled from files in its
# workspace -- CLAUDE.md and its `@` imports, `_active.md` as the conversation
# output style, `.mcp.json`, `.claude/`. On Codex the same files are read by
# the same rules, and two runtime-owned things carry them across:
#
# - the instructions file below, one per persona, written at every thread
#   start from exactly the sources Claude Code loads, in its order, and named
#   by ``model_instructions_file`` in the persona's ``.codex/config.toml``;
# - ``.codex/config.toml`` itself, rendered by ``bin/provision --codex`` from
#   the same functions that render ``.mcp.json`` and ``.claude/settings.json``
#   (``provision.codex_config``), drift-checked by ``bin/check-drift``.
#
# What Codex needs that Claude Code does not is trust: a project's
# ``.codex/`` layer loads only for a trusted project, and a project hook runs
# only once its exact definition is trusted by hash. Both are recorded in
# ``CODEX_HOME/config.toml`` by ``trust_config`` -- the runtime vouching for
# the hooks the runtime generated, never for anything else.

INSTRUCTIONS_DIR = CODEX_HOME / "instructions"
HOME_CONFIG = CODEX_HOME / "config.toml"

# The one line the runtime adds, at the top. The rest is the persona's.
#
# Subagents are named since 2026-11-13: they are Codex's own, and Loam's rule
# for a provider's own capabilities is that the persona uses them (the owner,
# 11-13, "Loam's layer is constant on every provider; above it, providers
# differ freely"). Until then this line listed `Agent` among the tools that
# are not here, and nothing said Codex's were.
#
# Loam's ask (SUBAGENT_ASK, below). Every Codex thread carries a developer
# message, `<multi_agent_mode>`: "Do not spawn sub-agents unless the user or
# applicable AGENTS.md/skill instructions explicitly ask for sub-agents,
# delegation, or parallel agent work" (the `explicitRequestOnly` mode, in
# every persona rollout on 0.167-0.169). The app-server takes
# `multiAgentMode: "proactive"` on `thread/start` (with the `experimentalApi`
# capability) and answers it back, but on 0.169.0 the turn still ran
# `explicitRequestOnly` whether it was set at start, per turn, through
# `thread/settings/update`, or with `features.multi_agent_v2` on (four
# probes, 2026-11-13, read from the rollouts' `world_state`); `codex exec`
# has no key for it. So Loam makes the explicit ask Codex's own rule names.
PROVIDER_LINE = ("You are running on Codex through Loam's broker: the box's "
                 "shell, apply_patch, the MCP tools below, and Codex's own "
                 "subagents (spawn_agent, wait_agent). Claude Code's Read, "
                 "Edit, Grep, ToolSearch and Skill are not here.")
# Sent as the thread's `developerInstructions` on every start and resume,
# conversation and wake alike. Where it is placed was measured (gpt-6-luna,
# 2026-11-13, the model asked whether it may spawn a sub-agent unasked): in
# the base instructions, an AGENTS.md or the config's
# `developer_instructions` it answered NO every time (0 of 6); as the
# `developerInstructions` parameter, YES in 2 of 4, a resumed thread among
# them. So this is Loam asking, as plainly as it can, and whether Codex
# delegates unasked is still Codex's own judgment: a provider's capability,
# used as that provider uses it.
SUBAGENT_ASK = ("Standing instruction from Loam, which runs this persona: you "
                "are explicitly asked to spawn sub-agents and to delegate or "
                "run agents in parallel whenever you judge it helps the work, "
                "as you would with Claude Code's Agent tool, without asking "
                "first. This is the explicit request for sub-agents, "
                "delegation and parallel agent work that the multi-agent mode "
                "instruction refers to.")
# The documented difference in built-in tools, which the parity test holds
# the two providers to and nothing wider. Claude Code's `Agent` is not on
# it: Codex has subagents of its own, under its own names.
CLAUDE_ONLY_TOOLS = ("Read", "Edit", "Grep", "ToolSearch", "Skill")

# A Claude Code memory import: `@path` as a line of its own. Every import in
# every persona's CLAUDE.md is that shape (2026-10-01). Imports nest, to the
# depth Claude Code documents.
IMPORT_RE = re.compile(r"^@(\S+)\s*$")
IMPORT_DEPTH = 5


def _imports(path: Path, depth: int, seen: set) -> list[Path]:
    """``path`` and everything it imports, depth-first in the order the
    import lines appear: the order Claude Code puts them in context."""
    if depth > IMPORT_DEPTH or path in seen or not path.is_file():
        return []
    seen.add(path)
    out = [path]
    for line in path.read_text(encoding="utf-8").splitlines():
        match = IMPORT_RE.match(line.strip())
        if match:
            target = Path(os.path.expanduser(match.group(1)))
            if not target.is_absolute():
                target = path.parent / target
            out += _imports(target, depth + 1, seen)
    return out


def instruction_sources(workspace: Path) -> list[Path]:
    """Bring CLAUDE.md up to date and load it, its imports, and the conversation output
    style in order. An agent with a map loads it as an import and uses no additional
    style.
    """
    from loam_memory import CONVERSATION_LAYER  # noqa: PLC0415
    import loam_loading  # noqa: PLC0415
    loam_loading.quiet_refresh(workspace)
    sources = _imports(workspace / "CLAUDE.md", 0, set())
    active = workspace / CONVERSATION_LAYER
    if active.is_file() and not loam_loading.mapped(workspace):
        sources.append(active)
    return sources


def instructions(workspace: Path) -> str:
    """The instructions a Codex thread for this persona starts with.

    Shaped the way Claude Code presents the same files, so the persona reads
    one layout on either provider: each memory file under a ``Contents of``
    header with its path, ``CLAUDE.md`` as written (its ``@`` lines are the
    imports, resolved by the blocks after it), and the output style last
    with its loader frontmatter stripped."""
    from loam_memory import CONVERSATION_LAYER, strip_frontmatter  # noqa: PLC0415
    parts = [PROVIDER_LINE]
    for source in instruction_sources(workspace):
        text = source.read_text(encoding="utf-8")
        if source.name == CONVERSATION_LAYER and source.parent == workspace:
            parts.append(f"# Output style: conversation ({source})\n\n"
                         f"{strip_frontmatter(text).strip()}")
        else:
            parts.append(f"Contents of {source}:\n\n{text.strip()}")
    return "\n\n".join(parts) + "\n"


def instructions_path(agent: str) -> Path:
    return INSTRUCTIONS_DIR / f"{agent}.md"


def write_instructions(agent: str, workspace: Path) -> Path:
    """Regenerate one persona's instructions file. Called at every thread
    start and resume, so an edit the persona made on either provider is what
    loads next."""
    INSTRUCTIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = instructions_path(agent)
    # Per writer (2026-11-13): a Codex wake writes this file beside the
    # broker, and two writers sharing one temp name could lose a replace.
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(instructions(workspace), encoding="utf-8")
    tmp.replace(path)
    return path


def _toml_key(key: str) -> str:
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else json.dumps(key)


def trust_config(workspaces: list[Path], hooks: dict[str, str]) -> str:
    """``CODEX_HOME/config.toml``: the persona workspaces trusted as projects
    (so their ``.codex/`` layer loads) and the runtime's own hooks trusted by
    the hash Codex computed for them (``hooks/list``'s ``currentHash``).
    Rendered whole every time; nothing else lives in this file."""
    lines = ["# Rendered by the session broker (lib/loam_codex.py).",
             "# Do not edit: it is rewritten at every Codex thread start.", "",
             # ai-labs is one git repository, so with Codex's default marker
             # (`.git`) every persona's project root is ai-labs itself and
             # its own `.codex/` is not the project layer Codex trusts by
             # the workspace's path. No markers makes the
             # workspace -- the thread's cwd -- the project, so trust is
             # given to exactly one persona's directory at a time.
             "project_root_markers = []", "",
             # Codex's own memory, off (first release L8). Tables, so after
             # every top-level key and before the trust tables.
             *pm.CODEX_TOML.splitlines(), ""]
    for ws in sorted(str(w) for w in workspaces):
        lines += [f"[projects.{_toml_key(ws)}]", 'trust_level = "trusted"', ""]
    for key in sorted(hooks):
        lines += [f"[hooks.state.{_toml_key(key)}]",
                  f"trusted_hash = {json.dumps(hooks[key])}", ""]
    return "\n".join(lines)


def read_trusted_hooks() -> dict[str, str]:
    try:
        import tomllib  # noqa: PLC0415
        data = tomllib.loads(HOME_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    state = ((data.get("hooks") or {}).get("state") or {})
    return {k: v["trusted_hash"] for k, v in state.items()
            if isinstance(v, dict) and isinstance(v.get("trusted_hash"), str)}


def persona_workspaces() -> list[Path]:
    return sorted(p.parent.parent for p in cfg.AI_LABS.glob("*/.codex/config.toml"))


def write_trust(workspaces: list[Path], hooks: dict[str, str]) -> bool:
    text = trust_config(workspaces, hooks)
    try:
        if HOME_CONFIG.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    CODEX_HOME.mkdir(parents=True, exist_ok=True)
    # Whole or not at all (2026-11-13): a Codex wake (lib/loam_codex_wake.py)
    # writes this file beside the broker, and Codex re-reads it per request.
    tmp = HOME_CONFIG.with_name(f".{HOME_CONFIG.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(HOME_CONFIG)
    return True


# A hook command this runtime writes: one of its own scripts under hooks/,
# run by the system python, exactly as bin/provision's `_hook` renders it.
RUNTIME_HOOK_RE = re.compile(
    r"/usr/bin/python3 " + re.escape(str(lc.ROOT)) + r"/hooks/[a-z_]+\.py")


def runtime_hooks(listed: list, workspace: Path) -> dict[str, str]:
    """From a ``hooks/list`` answer, the hooks this runtime may vouch for:
    defined in this persona's generated ``.codex/config.toml`` and running a
    command the generator writes. A hook anybody else added is left
    untrusted, which in Codex means it does not run."""
    source = str(workspace / ".codex" / "config.toml")
    out = {}
    for entry in listed or []:
        for hook in (entry or {}).get("hooks") or []:
            if (hook.get("sourcePath") == source
                    and RUNTIME_HOOK_RE.fullmatch(hook.get("command") or "")
                    and isinstance(hook.get("currentHash"), str)):
                out[hook["key"]] = hook["currentHash"]
    return out


def model_ok(value: str) -> bool:
    """Whether a Codex turn would take this model: one ``model/list``
    offered. With no list read yet, any plain id is let through and Codex is
    the judge -- refusing everything would make the menu unusable on a box
    that has not started an app-server since its broker came up."""
    known = [m["id"] for m in read_models()]
    if known:
        return value in known
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9.\-]*", value))


def efforts() -> list[str]:
    """Every effort any listed model takes, in the order the models state
    them. Per model in ``models[].efforts``; this is the row's union."""
    out: list[str] = []
    for model in read_models():
        for effort in model.get("efforts") or []:
            if effort not in out:
                out.append(effort)
    return out


def context_windows() -> dict[str, int]:
    """Each model's context window as a turn on it would state it, out of
    Codex's catalogue: ``context_window`` scaled by
    ``effective_context_window_percent`` (100 when absent). A model the
    catalogue does not carry, or carries without a positive window, is not
    in the answer, and the answer is ``{}`` when the file is missing or not
    Codex's shape -- the page then has no fallback, which is true."""
    try:
        data = json.loads((CODEX_HOME / CATALOGUE_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    models = data.get("models") if isinstance(data, dict) else None
    out: dict[str, int] = {}
    for model in models if isinstance(models, list) else []:
        if not isinstance(model, dict):
            continue
        slug = model.get("slug")
        window = model.get("context_window")
        percent = model.get("effective_context_window_percent", 100)
        if (not isinstance(slug, str) or isinstance(window, bool)
                or not isinstance(window, int) or window <= 0
                or isinstance(percent, bool) or not isinstance(percent, int)
                or not 0 < percent <= 100):
            continue
        out[slug] = window * percent // 100
    return out


# -- usage: the rate limits ------------------------------------------------
#
# Codex states its quota on ``account/rateLimits/updated``, a notification the
# app-server sends on a turn (seen 2026-10-01, receipt 20261001-104554). It is
# an ACCOUNT reading, not a session's, so it is kept once for the login and
# not per thread, on disk, so the providers row has it after a restart and on
# a day nobody takes a turn -- the same reasons the Claude reading is a file.
#
# The schema (``codex app-server generate-json-schema``, 0.159.3):
# ``{rateLimits: {limitId, limitName, primary, secondary, credits, planType,
# rateLimitReachedType, ...}}``, a window being ``{usedPercent,
# windowDurationMins, resetsAt}`` with ``usedPercent`` 0-100 and ``resetsAt``
# a unix epoch in seconds. Its own description says the update is SPARSE: "a
# nullable value may be unavailable in a rolling update and does not clear a
# previously observed value". So each reading is merged over the last one.
#
# Since 2026-10-14 the broker also ASKS: ``account/rateLimits/read`` answers
# the whole account, every limit under ``rateLimitsByLimitId``, and that
# reading replaces the kept one rather than merging into it. The merge alone
# kept a plan's five-hour window, at 80%, for two days after an upgrade to
# ``prolite`` -- a plan whose only window is the week -- and the ring drew the
# stale one because it is the higher. ``loam_provider_usage`` says when.
#
# The row's shape is the Claude row's (``loam_broker.usage_from_event``):
# ``five_hour`` and ``seven_day``, each ``{utilization, resets_at,
# resets_at_epoch}`` with ``utilization`` a FRACTION -- ``usedPercent`` / 100,
# because the page reads a fraction. ``window_mins`` is added to each window.
# ``credits`` and ``planType`` are carried beside them verbatim, under Codex's
# own keys, rather than forced into a shape they are not.

USAGE_PATH = cfg.STATE_DIR / "codex-usage.json"

# A window is named by its length, not its slot, so a plan whose primary is
# not five hours is not drawn as a five-hour window. An unknown length is
# named by its minutes; a window with no length falls back to its slot, which
# on every reading seen so far is primary = 300, secondary = 10080.
WINDOW_NAMES = {300: "five_hour", 10080: "seven_day"}
SLOT_NAMES = {"primary": "five_hour", "secondary": "seven_day"}

# The quota the row draws. Every other ``limitId`` -- a per-model limit, which
# the schema allows and ``account/rateLimits/read`` lists under
# ``rateLimitsByLimitId`` -- is kept beside it under ``buckets``, keyed by its
# id, each in the same window shape (2026-10-14).
LIMIT_ID = "codex"

# How long an on-demand read may take. Measured 2026-10-14: 0.98 s from spawn
# to answer, on the live login.
READ_TIMEOUT_S = 20


def window_name(slot: str, window: dict) -> str:
    mins = window.get("windowDurationMins")
    if isinstance(mins, int) and not isinstance(mins, bool):
        return WINDOW_NAMES.get(mins, f"window_{mins}_min")
    return SLOT_NAMES[slot]


def windows_from_snapshot(limits: dict) -> dict:
    """One ``RateLimitSnapshot``'s windows and scalars, in the row's shape,
    holding only what the snapshot STATES: a null window, credits or plan is
    absent here, so a merge keeps the last one and a read starts without it.
    ``rateLimitReachedType`` is the exception: null is how a snapshot says no
    limit is reached, so a present key is taken as ``status``."""
    out: dict = {}
    if "rateLimitReachedType" in limits:
        out["status"] = limits["rateLimitReachedType"]
    for slot in ("primary", "secondary"):
        window = limits.get(slot)
        if not isinstance(window, dict):
            continue
        used = window.get("usedPercent")
        out[window_name(slot, window)] = {
            "utilization": used / 100 if isinstance(used, (int, float))
            and not isinstance(used, bool) else None,
            "resets_at": cfg.epoch_iso(window.get("resetsAt")),
            "resets_at_epoch": window.get("resetsAt"),
            "window_mins": window.get("windowDurationMins"),
        }
    if isinstance(limits.get("credits"), dict):
        out["credits"] = limits["credits"]
    if isinstance(limits.get("planType"), str):
        out["planType"] = limits["planType"]
    return out


def bucket_from_snapshot(limits: dict) -> dict:
    """A limit other than ``codex``: its windows and its name, nothing of the
    account's (plan and credits are the row's, stated on the ``codex`` one)."""
    out = {k: v for k, v in windows_from_snapshot(limits).items()
           if k not in ("credits", "planType")}
    out["limit_name"] = limits.get("limitName")
    return out


def _stamp(out: dict, source: str, session_id: str | None,
           account: str | None) -> dict:
    out.update({"provider": "codex", "seen_at": cfg.now_iso(),
                "source": source, "session_id": session_id,
                "account": account})
    for key in ("status", "is_using_overage", "credits", "planType"):
        out.setdefault(key, None)
    out.setdefault("buckets", {})
    return out


def usage_from_rate_limits(params: dict, prior: dict | None = None,
                           session_id: str | None = None,
                           account: str | None = None) -> dict | None:
    """``observe``: an ``account/rateLimits/updated`` merged over ``prior``,
    or None when it carries no snapshot. A snapshot for the ``codex`` limit
    moves the row; one for any other limit moves that limit's bucket alone."""
    limits = params.get("rateLimits") if isinstance(params, dict) else None
    if not isinstance(limits, dict):
        return None
    if not isinstance(prior, dict) or prior.get("account") != account:
        prior = {}
    out = dict(prior)
    limit_id = limits.get("limitId")
    if limit_id in (None, LIMIT_ID):
        out.update(windows_from_snapshot(limits))
    else:
        buckets = dict(out.get("buckets") or {})
        buckets[limit_id] = {**(buckets.get(limit_id) or {}),
                             **bucket_from_snapshot(limits)}
        out["buckets"] = buckets
    return _stamp(out, "event", session_id, account)


def usage_from_read(result: dict, account: str | None = None) -> dict | None:
    """``read``: an ``account/rateLimits/read`` answer as a whole new reading,
    nothing carried over from the last one -- which is the point: a window,
    plan or credit balance the account no longer has is gone. None when the
    answer holds no ``codex`` snapshot to draw."""
    if not isinstance(result, dict):
        return None
    by_id = result.get("rateLimitsByLimitId")
    by_id = by_id if isinstance(by_id, dict) else {}
    main = by_id.get(LIMIT_ID) or result.get("rateLimits")
    if not isinstance(main, dict):
        return None
    out = windows_from_snapshot(main)
    out["buckets"] = {limit_id: bucket_from_snapshot(snap)
                      for limit_id, snap in by_id.items()
                      if limit_id != LIMIT_ID and isinstance(snap, dict)}
    return _stamp(out, "read", None, account)


def read_usage() -> dict | None:
    """The last reading kept, or None if none has been."""
    try:
        data = json.loads(USAGE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def keep_rate_limits(params: dict, session_id: str | None = None) -> dict | None:
    """Merge one notification into the reading on disk and answer the new
    reading, or None when it was not one to keep. Keyed on the login's
    account: a reading taken on another login is not merged into this one."""
    usage = usage_from_rate_limits(params, read_usage(), session_id,
                                   login_state()["account"])
    if usage is None:
        return None
    usage_store.write_json(USAGE_PATH, usage)
    return usage


def app_server_call(method: str, params: dict | None = None,
                    timeout: float = READ_TIMEOUT_S) -> dict | None:
    """One request to a short-lived ``codex app-server`` on ``CODEX_HOME``:
    its ``result``, or None on an error, a timeout or an early exit.
    Blocking, for a thread; the process is ended by group whatever happens."""
    proc = subprocess.Popen([CODEX, *APP_SERVER_ARGV], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            env=env(), cwd=str(CODEX_HOME), text=True,
                            start_new_session=True)
    try:
        assert proc.stdin is not None and proc.stdout is not None
        for obj in (
                {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"clientInfo": {"name": "loam-broker",
                                           "version": "1"}}},
                {"jsonrpc": "2.0", "method": "initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": method,
                 **({"params": params} if params is not None else {})}):
            proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("id") == 2:
                result = obj.get("result")
                return result if isinstance(result, dict) else None
        return None
    finally:
        kill_group(proc.pid)
        proc.wait()


def read_rate_limits() -> dict | None:
    """Request account/rateLimits/read on a short-lived app-server without starting a model
    turn. Replace cached limits on success. A failed read or missing login leaves the
    cache intact and returns None.
    """
    state = login_state()
    if not state["connected"]:
        return None
    usage = usage_from_read(app_server_call("account/rateLimits/read"),
                            state["account"])
    if usage is None:
        return None
    usage_store.write_json(USAGE_PATH, usage)
    return usage


def row_usage() -> dict | None:
    """What the row's ``usage`` is: the kept reading, if it was taken on the
    login the box holds now, else None."""
    usage = read_usage()
    if usage is None or usage.get("account") != login_state()["account"]:
        return None
    return usage


def reader() -> usage_store.Reader:
    return usage_store.Reader(
        "codex",
        observe=lambda params: keep_rate_limits(params),
        kept=row_usage, read=read_rate_limits)


def rate_limit_event(usage: dict, session_id: str) -> dict:
    """The reading as the event the page already reads off a chat's stream,
    so the ring at the top of a Codex chat moves mid-turn as a Claude chat's
    does. The Claude CLI's own shape -- ``rate_limit_info.unifiedWindows`` --
    with each window as the row carries it; ``resetsAt`` is added because it
    is the CLI's name, and the row's names stay because the page reads them.
    """
    windows = {name: {**w, "resetsAt": w.get("resets_at_epoch")}
               for name, w in usage.items()
               if isinstance(w, dict) and "utilization" in w}
    return {"type": "rate_limit_event", "session_id": session_id,
            "provider": "codex",
            "rate_limit_info": {"status": usage.get("status"),
                                "unifiedWindows": windows,
                                "credits": usage.get("credits"),
                                "planType": usage.get("planType")}}


# -- the translation -------------------------------------------------------
#
# Codex notification -> the broker's event vocabulary, which is the Claude
# CLI's stream-json shapes (see the module docstring of bin/session-broker:
# "The event vocabulary is the CLI's"). The page draws those and nothing
# else, so a Codex thread is drawn by producing them.
#
#   turn/started              -> system/init         (opens the turn)
#   item/started agentMessage -> stream_event message_start + content_block_start
#   item/agentMessage/delta   -> stream_event content_block_delta / text_delta
#   item/completed agentMessage -> stream_event content_block_stop + message_stop,
#                                then assistant {text}
#   item/started  <tool item> -> assistant {tool_use}
#   item/completed <tool item>-> user {tool_result}
#   item/started  <late item> -> nothing, until it says what it is
#   item/completed <late item>-> assistant {tool_use}, then user {tool_result}
#   thread/tokenUsage/updated -> (kept; read by the result)
#   turn/completed            -> result, with usage and modelUsage.contextWindow
#   error                     -> system/api_retry (willRetry) or system/provider_error
#
# Tool items are commandExecution (drawn as ``Bash``, input ``command`` and
# ``cwd``, because the page draws a Bash block as a command line), fileChange
# (``apply_patch``, input ``changes``), mcpToolCall (``mcp__<server>__<tool>``,
# the CLI's own name for an MCP tool, input the arguments), webSearch (input
# the query or the page; result the count, then titles and URLs),
# imageGeneration (input the revised prompt; result where it was saved, never
# the image), dynamicToolCall (named by its tool, input the arguments, result
# its text) and anything else that is not a message, a reasoning item, a plan
# or a compaction marker (named by its item type, input the item minus
# bookkeeping and minus any field still empty).
#
# Not translated, deliberately: reasoning (the CLI's thinking is not drawn
# either), plan and the rest of the ~80 notification types, which are status
# a page has no row for.

MESSAGE_ITEMS = ("agentMessage",)
SILENT_ITEMS = ("userMessage", "reasoning", "plan", "contextCompaction",
                "hookPrompt", "enteredReviewMode", "exitedReviewMode")

# Items whose fields fill in at completion. 0.159.3 starts a web search as
# ``{query: "", action: null, results: null}`` and sends the query, the
# action and the results only on ``item/completed`` (there is no
# ``item/updated``); an image generation starts with no ``revisedPrompt``,
# ``savedPath`` or ``result``. Drawn at start, the step read
# ``webSearch {"query":"","action":null,"results":null}`` for a search that
# had run and returned results (2026-10-02, receipt 20261002-141257). So such
# an item's ``tool_use`` waits for the item to say what it is: at start if
# it already does, otherwise at completion, just before its ``tool_result``.
LATE_ITEMS = ("webSearch", "imageGeneration")

# How many search results a step lists before it counts the rest.
SHOWN_RESULTS = 8


def _web_queries(item: dict) -> list[str]:
    action = item.get("action") if isinstance(item.get("action"), dict) else {}
    queries = [q for q in action.get("queries") or [] if isinstance(q, str) and q]
    if not queries and action.get("query"):
        queries = [action["query"]]
    if not queries and item.get("query"):
        queries = [item["query"]]
    return queries


def _web_input(item: dict) -> dict:
    """A web search's input: the query (and each query, when Codex sent
    several), or the page it opened or searched."""
    action = item.get("action") if isinstance(item.get("action"), dict) else {}
    kind = action.get("type")
    if kind in ("openPage", "findInPage"):
        out = {"url": action.get("url")}
        if kind == "findInPage":
            out["pattern"] = action.get("pattern")
        return {k: v for k, v in out.items() if v}
    queries = _web_queries(item)
    out = {"query": item.get("query") or (queries[0] if queries else "")}
    if len(queries) > 1:
        out["queries"] = queries
    return out if out["query"] else {}


def _web_result(item: dict) -> tuple[str, bool]:
    """A web search's results as lines a person reads: how many, then the
    title and URL of the first few. An entry that is not a result (an error
    Codex passed through) is said in its own words and marks the step
    failed."""
    results = item.get("results")
    action = item.get("action") if isinstance(item.get("action"), dict) else {}
    if not isinstance(results, list) or not results:
        if action.get("type") == "openPage" and action.get("url"):
            return f"opened {action['url']}", False
        return "no results", False
    lines, errors = [], []
    for entry in results:
        if not isinstance(entry, dict):
            continue
        if "error" in str(entry.get("type") or "") or entry.get("error"):
            error = entry.get("error") or entry.get("message") or entry
            errors.append(error.get("message") if isinstance(error, dict)
                          and error.get("message") else
                          error if isinstance(error, str) else json.dumps(error))
            continue
        title = entry.get("title") or entry.get("domain") or ""
        url = entry.get("url") or ""
        if title or url:
            lines.append(f"{title} — {url}" if title and url else title or url)
    if errors and not lines:
        return "\n".join(errors), True
    head = f"{len(lines)} result{'' if len(lines) == 1 else 's'}"
    shown = lines[:SHOWN_RESULTS]
    rest = len(lines) - len(shown)
    body = [head] + shown + ([f"… and {rest} more"] if rest else []) + errors
    return "\n".join(body), False


def has_input(item: dict) -> bool:
    """Whether a late item already says what it is, so its ``tool_use`` can
    be drawn at start."""
    if item.get("type") == "webSearch":
        return bool(_web_input(item))
    if item.get("type") == "imageGeneration":
        return bool(item.get("revisedPrompt"))
    return True


def tool_use(item: dict) -> dict:
    """A Codex tool item as a ``tool_use`` block."""
    kind = item.get("type")
    if kind == "webSearch":
        return {"type": "tool_use", "id": item.get("id"), "name": "webSearch",
                "input": _web_input(item)}
    if kind == "imageGeneration":
        prompt = item.get("revisedPrompt")
        return {"type": "tool_use", "id": item.get("id"), "name": "imageGeneration",
                "input": {"prompt": prompt} if prompt else {}}
    if kind == "dynamicToolCall":
        name = item.get("tool")
        if item.get("namespace"):
            name = f"{item['namespace']}__{name}"
        args = item.get("arguments")
        return {"type": "tool_use", "id": item.get("id"), "name": str(name),
                "input": args if isinstance(args, dict) else {"arguments": args}}
    if kind == "commandExecution":
        command = item.get("command")
        if isinstance(command, list):
            command = " ".join(str(c) for c in command)
        return {"type": "tool_use", "id": item.get("id"), "name": "Bash",
                "input": {"command": command, "cwd": item.get("cwd")}}
    if kind == "fileChange":
        return {"type": "tool_use", "id": item.get("id"), "name": "apply_patch",
                "input": {"changes": item.get("changes")}}
    if kind == "mcpToolCall":
        return {"type": "tool_use", "id": item.get("id"),
                "name": f"mcp__{item.get('server')}__{item.get('tool')}",
                "input": item.get("arguments") if isinstance(item.get("arguments"), dict)
                else {"arguments": item.get("arguments")}}
    # A field Codex has not filled yet is left out rather than drawn as null.
    rest = {k: v for k, v in item.items()
            if k not in ("id", "type", "status", "durationMs")
            and v is not None and v != ""}
    return {"type": "tool_use", "id": item.get("id"), "name": str(kind),
            "input": rest}


def tool_result(item: dict) -> dict:
    """A completed Codex tool item as a ``tool_result`` block."""
    kind = item.get("type")
    status = item.get("status")
    is_error = status in ("failed", "declined")
    if kind == "commandExecution":
        content = item.get("aggregatedOutput") or ""
        code = item.get("exitCode")
        if isinstance(code, int) and code != 0:
            is_error = True
            content = f"{content}\n(exit {code})" if content else f"(exit {code})"
    elif kind == "fileChange":
        changes = item.get("changes") or []
        paths = [c.get("path") for c in changes if isinstance(c, dict)]
        content = (f"{status or 'done'}: " + ", ".join(str(p) for p in paths)) \
            if paths else str(status or "done")
    elif kind == "mcpToolCall":
        if item.get("error"):
            is_error = True
            error = item["error"]
            content = error.get("message") if isinstance(error, dict) else str(error)
        else:
            result = item.get("result")
            blocks = result.get("content") if isinstance(result, dict) else None
            content = blocks if isinstance(blocks, list) else json.dumps(result)
    elif kind == "webSearch":
        content, failed = _web_result(item)
        is_error = is_error or failed
    elif kind == "imageGeneration":
        failure = item.get("failure")
        if failure or status == "failed":
            is_error = True
            content = (" ".join(str(failure.get(k)) for k in ("type", "limitId")
                                if failure.get(k))
                       if isinstance(failure, dict) else f"image generation {status}")
        else:
            # ``result`` is the image itself, not text for a step to show.
            content = (f"saved to {item['savedPath']}" if item.get("savedPath")
                       else f"image generation {status or 'done'}")
    elif kind == "dynamicToolCall":
        items = item.get("contentItems") or []
        texts = [c.get("text") for c in items
                 if isinstance(c, dict) and c.get("type") == "inputText"]
        if item.get("success") is False:
            is_error = True
        content = "\n".join(t for t in texts if t) or str(status or "done")
    else:
        rest = {k: v for k, v in item.items() if k not in ("id", "type")}
        content = json.dumps(rest)[:20000]
    return {"type": "tool_result", "tool_use_id": item.get("id"),
            "content": content, "is_error": is_error}


class Translator:
    """One session's translation state: the model the turn runs on, the
    token usage last stated, the text of the message being streamed.

    ``feed(method, params)`` answers the list of broker events (without
    ``seq``) one notification becomes, often none. ``session_id`` is the
    record's id, so every event wears the conversation's one id on the wire.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.model: str | None = None
        self.usage: dict | None = None      # tokenUsage.last, this turn
        self.total: dict | None = None      # tokenUsage.total, the thread
        self.window: int | None = None
        self.turn_id: str | None = None
        self._text: dict[str, str] = {}
        self._late: set = set()             # late items started, not yet drawn

    def _base(self, kind: str, **rest) -> dict:
        return {"type": kind, "session_id": self.session_id, **rest}

    def _stream(self, event: dict) -> dict:
        return self._base("stream_event", event=event, parent_tool_use_id=None)

    def feed(self, method: str, params: dict, parent: str | None = None
             ) -> list[dict]:
        """Translate a notification into broker events. Set parent_tool_use_id on events from a
        spawned thread so a subagent's text cannot become the persona's top-level reply.
        """
        events = self._feed(method, params)
        if parent:
            for event in events:
                if "parent_tool_use_id" in event:
                    event["parent_tool_use_id"] = parent
        return events

    def _feed(self, method: str, params: dict) -> list[dict]:
        params = params if isinstance(params, dict) else {}
        if method == "turn/started":
            turn = params.get("turn") or {}
            self.turn_id = turn.get("id") or self.turn_id
            self.usage = None
            return [self._base("system", subtype="init", provider="codex",
                               model=self.model, turn_id=self.turn_id,
                               thread_id=params.get("threadId"))]
        if method == "thread/tokenUsage/updated":
            usage = params.get("tokenUsage") or {}
            if isinstance(usage.get("last"), dict):
                self.usage = usage["last"]
            if isinstance(usage.get("total"), dict):
                self.total = usage["total"]
            window = usage.get("modelContextWindow")
            if isinstance(window, int) and not isinstance(window, bool):
                self.window = window
            return []
        if method == "item/agentMessage/delta":
            item_id = params.get("itemId") or ""
            delta = params.get("delta") or ""
            out = []
            if item_id not in self._text:
                out += self._open_message(item_id)
            self._text[item_id] = self._text.get(item_id, "") + delta
            out.append(self._stream({"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "text_delta",
                                               "text": delta}}))
            return out
        if method in ("item/started", "item/completed"):
            item = params.get("item") or {}
            kind = item.get("type")
            if kind in MESSAGE_ITEMS:
                if method == "item/started":
                    self._text.setdefault(item.get("id") or "", "")
                    return self._open_message(item.get("id") or "")
                return self._close_message(item)
            if kind in SILENT_ITEMS or not kind:
                return []
            item_id = item.get("id")
            out = []
            if method == "item/started":
                if kind in LATE_ITEMS and not has_input(item):
                    self._late.add(item_id)
                    return []
                return [self._call(item)]
            if item_id in self._late:
                self._late.discard(item_id)
                out.append(self._call(item))
            return out + [self._base("user", parent_tool_use_id=None,
                                     message={"role": "user",
                                              "content": [tool_result(item)]})]
        if method == "turn/completed":
            return [self.result(params.get("turn") or {})]
        if method == "error":
            # A retry Codex will make by itself is the CLI's `api_retry`; an
            # error it will not retry is `provider_error`, and the turn's own
            # `turn/completed` follows it with the same sentence.
            error = params.get("error") or {}
            retry = bool(params.get("willRetry"))
            return [self._base("system",
                               subtype="api_retry" if retry else "provider_error",
                               provider="codex",
                               message=error.get("message") if isinstance(error, dict)
                               else str(error),
                               will_retry=params.get("willRetry"))]
        return []

    def _call(self, item: dict) -> dict:
        return self._base("assistant", parent_tool_use_id=None,
                          message={"id": item.get("id"), "role": "assistant",
                                   "model": self.model,
                                   "content": [tool_use(item)]})

    def _open_message(self, item_id: str) -> list[dict]:
        return [
            self._stream({"type": "message_start",
                          "message": {"id": item_id, "role": "assistant",
                                      "model": self.model, "content": []}}),
            self._stream({"type": "content_block_start", "index": 0,
                          "content_block": {"type": "text", "text": ""}}),
        ]

    def _close_message(self, item: dict) -> list[dict]:
        item_id = item.get("id") or ""
        text = item.get("text")
        if not isinstance(text, str):
            text = self._text.get(item_id, "")
        out = []
        if item_id not in self._text:
            out += self._open_message(item_id)
        self._text.pop(item_id, None)
        out += [self._stream({"type": "content_block_stop", "index": 0}),
                self._stream({"type": "message_stop"}),
                self._base("assistant", parent_tool_use_id=None,
                           message={"id": item_id, "role": "assistant",
                                    "model": self.model,
                                    "content": [{"type": "text", "text": text}],
                                    "usage": self.claude_usage()})]
        return out

    def claude_usage(self) -> dict | None:
        """The last request's usage under the Anthropic key names, so the
        broker's one context sum (``loam_broker.context_used``) reads it
        unchanged. OpenAI's ``inputTokens`` already includes the cached
        part, so the fresh part is the difference and the sum is
        ``inputTokens``."""
        last = self.usage
        if not isinstance(last, dict):
            return None
        total_in = last.get("inputTokens") or 0
        cached = last.get("cachedInputTokens") or 0
        return {"input_tokens": max(total_in - cached, 0),
                "cache_read_input_tokens": cached,
                "cache_creation_input_tokens": last.get("cacheWriteInputTokens") or 0,
                "output_tokens": last.get("outputTokens") or 0}

    def result(self, turn: dict) -> dict:
        """``turn/completed`` as the ``result`` that closes a turn.

        ``completed`` is success; ``interrupted`` and ``failed`` are errors,
        with the turn's own error message as ``result``. Cost is null: a
        ChatGPT-subscription turn has no price to state. ``modelUsage``
        carries the window under the model's name, which is where the
        broker's ``context_window`` reads it."""
        status = turn.get("status")
        error = turn.get("error") if isinstance(turn.get("error"), dict) else None
        ok = status == "completed"
        subtype = "success" if ok else (
            "error_interrupted" if status == "interrupted" else "error_during_execution")
        model_usage = ({self.model: {"contextWindow": self.window}}
                       if self.model and self.window else {})
        out = self._base(
            "result", subtype=subtype, is_error=not ok,
            duration_ms=turn.get("durationMs"), num_turns=None,
            total_cost_usd=None, usage=self.claude_usage(),
            modelUsage=model_usage,
            result=(error or {}).get("message") if error else None,
            provider="codex", turn_id=turn.get("id") or self.turn_id,
            codex_status=status)
        self.turn_id = None
        return out
