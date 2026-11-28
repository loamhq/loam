"""Run one headless agy process per turn and resume with the conversation ID reported
by the CLI. Translate init, step_update and result events into the broker stream.
Render workspace instructions in the format this provider loads.
"""

from __future__ import annotations

from collections.abc import Iterable
import json
import math
import os
import re
import shlex
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import loam_config as lc

import loam_broker as cfg
import loam_provider_usage as usage_store

# The official binary: since 2026-11-19 a link to the build this release pins
# (lib/loam_pins.py), fetched by its recorded address and SHA-512 rather than
# by Google's installer (which takes the latest). Its own background updater
# is off in every child (``env``, the units).
AGY = str(lc.AGY_BIN)

# Antigravity's own home. There is no documented override (no CODEX_HOME
# equivalent), so it is ``~/.gemini`` and shared by every persona. The login
# lives here: the CLI tries the Secret Service keyring and, with none on the
# box, falls back to a file ("Failed to load stored token from keyring,
# falling back to file", cli.log, 2026-11-09).
HOME = Path.home() / ".gemini"

# Keys that would put a session on an API account instead of the owner's own
# login. ``GEMINI_API_KEY`` alone does nothing without a settings change, and
# ``AGY_ADC_AUTH`` switches to gcloud's credentials; both are removed so a
# variable in the broker's environment cannot move the billing.
STRIPPED_ENV = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "AGY_ADC_AUTH",
                "GOOGLE_APPLICATION_CREDENTIALS")

# The probe's answer is kept this long: the providers route is read often
# and a probe is a process.
LOGIN_CACHE_S = 60
LOGIN_TIMEOUT_S = 20
SIGN_IN_RE = re.compile(r"sign in|log in|authenticat", re.I)

# Where the persona's instructions are rendered, inside its workspace,
# because Antigravity loads rule files from the workspace and ``~/.gemini``
# only and the latter is shared. Antigravity 1.5.12 ignores ``.agents/rules/``
# files without ``trigger: always_on`` YAML frontmatter and truncates each rule
# file at 24,000 bytes (on line boundaries), so instructions are split across
# numbered ``always_on`` parts under ``RULE_MAX_BYTES`` and refreshed before
# every turn (resumed ``--conversation`` turns reload updated rules too).
RULES_DIR = Path(".agents") / "rules"
RULES_PREFIX = "loam-persona"
LEGACY_RULES_FILE = RULES_DIR / f"{RULES_PREFIX}.md"
RULES_FILE = RULES_DIR / f"{RULES_PREFIX}-01.md"
RULE_PART_RE = re.compile(rf"^{re.escape(RULES_PREFIX)}(?:-\d+)?\.md$")
RULE_TRIGGER_RE = re.compile(
    r"\A---\r?\n(?:[^\n]*\r?\n)*?trigger:\s*always_on\s*\r?\n(?:[^\n]*\r?\n)*?---(?:\r?\n|\Z)"
)
AGY_RULE_LIMIT_BYTES = 24_000
RULE_MAX_BYTES = 20_000
MCP_FILE = Path(".agents") / "mcp_config.json"
HOOKS_FILE = Path(".agents") / "hooks.json"
IMAGES_HOOK = "loam-images"
LOADING_SIZE_HOOK = "loam-loading-size"
LOADING_SIZE_MATCHER = (
    "^(write_file|edit_file|write_to_file|replace_file_content"
    "|multi_replace_file_content|.*move_file)$"
)

PROVIDER_LINE = ("You are running on Antigravity through Loam's broker: the "
                 "box's shell and Antigravity's own tools, and the MCP tools "
                 "below; Claude Code's Read, Edit, Grep, Agent, ToolSearch "
                 "and Skill are not here under those names.")

# A prompt longer than this goes in a file the turn is told to read: one
# argv string is capped at 128 KiB by the kernel (MAX_ARG_STRLEN).
ARG_MAX_CHARS = 100_000
PROMPTS_DIR = cfg.STATE_DIR / "antigravity" / "prompts"

# Keep video tool input under the inline media budget, including room for
# encoding overhead. The account-backed CLI has no Files API input block.
VIDEO_INPUT_MAX_BYTES = 16 * 1024 * 1024


def video_preview(source: str, output: Path) -> bool:
    """Make a smaller MP4 of the whole video for agy's read_file tool."""
    formats = ["-protocol_whitelist", "file,pipe", "-format_whitelist", "mov,matroska,webm"]
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", *formats, "-show_entries", "format=duration",
             "-of", "csv=p=0", source], capture_output=True, check=True, timeout=15)
        # A WebM recorded to a stream can lack a duration in its header.
        # Use a conservative bitrate in that case and check the result below.
        duration = 300 if probe.stdout.strip() == b"N/A" else float(probe.stdout)
        if not math.isfinite(duration) or duration <= 0:
            return False
        bits_per_second = min(2_400_000, int(VIDEO_INPUT_MAX_BYTES * 8 * .88 / duration))
        audio_rate = min(64_000, max(8_000, bits_per_second // 4))
        video_rate = max(1_000, bits_per_second - audio_rate)
        subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-threads", "2", *formats, "-i", source,
             "-map", "0:V:0", "-map", "0:a:0?", "-sn", "-dn", "-map_metadata", "-1",
             "-vf", "scale=w=min(1280\\,iw):h=min(720\\,ih):force_original_aspect_ratio=decrease:force_divisible_by=2,fps=12",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "25",
             "-maxrate", str(video_rate), "-bufsize", str(max(1, video_rate // 2)),
             "-c:a", "aac", "-ac", "2", "-b:a", str(audio_rate), "-threads", "2",
             "-movflags", "+faststart", str(output)], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=300)
        return 0 < output.stat().st_size <= VIDEO_INPUT_MAX_BYTES
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def env(base: dict | None = None) -> dict:
    out = dict(os.environ if base is None else base)
    for name in STRIPPED_ENV:
        out.pop(name, None)
    # The CLI stays at the release's pin (lib/loam_pins.py): its own
    # background updater is off in every child, read only as `true`.
    out["AGY_CLI_DISABLE_AUTO_UPDATE"] = "true"
    return out


# -- the login -------------------------------------------------------------

_login_cache: tuple[float, tuple, dict] | None = None


def _probe() -> dict:
    base = {"source": "none", "connected": False, "reason": None,
            "account": None, "plan": None, "expires_at": None,
            "days_left": None, "models": []}
    if not os.access(AGY, os.X_OK):
        return {**base, "reason": "agy is not installed on the box"}
    try:
        proc = subprocess.run([AGY, "models"], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, env=env(),
                              timeout=LOGIN_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {**base, "reason": f"agy models did not answer ({type(exc).__name__})"}
    said = (proc.stdout + "\n" + proc.stderr).strip()
    if proc.returncode != 0:
        if SIGN_IN_RE.search(said):
            return {**base, "reason": "no login on the box"}
        return {**base, "reason": said.splitlines()[-1][:240] if said else
                f"agy models exited {proc.returncode}"}
    models = parse_models(proc.stdout)
    keep_models(models)
    return {**base, "source": "google-account", "connected": True,
            "account": _account(), "models": models}


# The last list `agy models` gave (2026-11-15), kept on disk for a reader
# outside the broker (bin/check-drift, as Codex's `codex-models.json` is).
# The broker's own copy is the login probe's, asked again at most once a
# LOGIN_CACHE_S behind the call, so a model Google adds shows within a
# minute of the page asking and a self-update of the CLI needs nothing.
MODELS_FILE = cfg.STATE_DIR / "antigravity" / "models.json"


def keep_models(models: list[dict]) -> None:
    if not models:
        return
    try:
        if MODELS_FILE.is_file() and json.loads(
                MODELS_FILE.read_text(encoding="utf-8")).get("models") == models:
            return
        MODELS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = MODELS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"at": cfg.now_iso(), "models": models}) + "\n",
                       encoding="utf-8")
        tmp.replace(MODELS_FILE)
    except (OSError, ValueError):
        pass


def read_models() -> list[dict]:
    try:
        data = json.loads(MODELS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    models = data.get("models") if isinstance(data, dict) else None
    return models if isinstance(models, list) else []


def _account() -> str | None:
    """Whose login it is, as ``agy acp --login`` said when it was made
    ("Logged in as <email>."), kept by the broker in ``ACCOUNT_FILE``. Not
    the credential and nothing from it: the CLI has no headless command
    that names the account, so the line it printed is the only source."""
    try:
        data = json.loads(ACCOUNT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    account = data.get("account") if isinstance(data, dict) else None
    return account if isinstance(account, str) and account else None


def parse_models(text: str) -> list[dict]:
    """Parse model rows as tab-separated ID and display name. Skip the CLI's progress line."""
    out = []
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        word = parts[0].strip("-*•") if parts else ""
        if re.fullmatch(r"[a-z0-9][a-z0-9.\-_/]+", word) and "-" in word:
            name = parts[1].strip() if len(parts) > 1 else word
            out.append({"id": word, "name": name})
    return out


# The thinking level, in Antigravity's model ids (2026-11-15, 1.5.9): most
# ids end in it (`gemini-4-hydrogen-high`, `claude-opus-5-5-low`), and
# `agy` refuses `--effort` beside such an id unless they agree ("--model
# gemini-4-hydrogen-low conflicts with --effort=xhigh", exit 1). `--effort`
# is also refused for an id that takes none ("--effort is not supported for
# model \"gemini-pro-orchestrator\""), and a bare family id takes it
# (`--model gemini-4-hydrogen --effort high` runs). So the one mapping Loam
# uses: **the id from `agy models` is the whole choice, level included**,
# and `--effort` is passed only beside an id that does not end in a level.
LEVEL_RE = re.compile(r"-(low|medium|high|xhigh|max)$")


def carries_level(model: str | None) -> bool:
    return bool(model and LEVEL_RE.search(model))


_refreshing = threading.Lock()


def _login_key() -> tuple:
    # A sign-in in the HTTP broker must also invalidate the session host's
    # login reading. The account marker covers keyring-backed app sign-ins.
    def signature(path):
        try:
            stat = path.stat()
            return stat.st_ino, stat.st_mtime_ns, stat.st_size
        except OSError:
            return None
    return signature(TOKEN_FILE), signature(ACCOUNT_FILE)


def _refresh() -> dict:
    global _login_cache
    key = _login_key()
    state = _probe()
    _login_cache = (time.monotonic(), key, state)
    return state


def login_state(fresh: bool = False) -> dict:
    """Probe the CLI on the first read or when fresh is requested. Serve a cached reading
    during background refresh so the providers route can return immediately.
    """
    now = time.monotonic()
    if fresh or not _login_cache or _login_cache[1] != _login_key():
        return _refresh()
    if now - _login_cache[0] >= LOGIN_CACHE_S and _refreshing.acquire(blocking=False):
        def run():
            try:
                _refresh()
            finally:
                _refreshing.release()
        threading.Thread(target=run, name="antigravity-login", daemon=True).start()
    return _login_cache[2]


# -- signing in, and out (2026-11-11) --------------------------------------
#
# What the CLI does, measured on this box (1.5.6, 2026-11-11) under a decoy
# HOME so the box's own ~/.gemini was never touched:
#
#   * ``agy acp --login`` ("Log in with Google interactively, then exit (ACP
#     terminal auth)") needs NO pty. With stdout a pipe it prints, in 0.2 s,
#     "Open this link in a browser to log in with Google:", the Google OAuth
#     URL (PKCE, redirect https://antigravity.google/oauth-callback, which
#     shows the person a code), then "Then paste the authorization code, or
#     the full URL of the page you land on, and press Enter:", and reads one
#     line from stdin.
#   * With stdin at EOF it exits 1: "login cancelled: no authorization code
#     was entered".
#   * A fake code, bare or inside a landing URL, exits 1 in 0.1 s: "couldn't
#     log in: token exchange failed: oauth2: "invalid_grant" "Bad Request"".
#     The code is not written to the CLI's log.
#   * Success prints "Logged in as %s." (or "Logged in.") and exits 0 -- read
#     out of the binary; no login has been made on this box yet.
#   * The token, with no keyring here, is ``TOKEN_FILE`` (the probe opens
#     exactly that path, strace'd). ``/logout`` exists only inside the TUI
#     and is refused in print mode ("it clears stored credentials"); with
#     the keyring unreachable it "short-circuits token removal directly to
#     file storage" (changelog), so sign-out here removes that file.
#
# There is no device-code branch: one link, one code pasted back, which is
# Claude Code's flow without the pty.
LOGIN_ARGV = ("acp", "--login")
TOKEN_FILE = HOME / "antigravity-cli" / "antigravity-oauth-token"
ACCOUNT_FILE = cfg.STATE_DIR / "antigravity" / "account.json"
LOGIN_PROMPT = "paste the authorization code"
LOGIN_PRINT_TIMEOUT_S = 25
LOGIN_FLOW_TIMEOUT_S = 900
LOGIN_SUBMIT_TIMEOUT_S = 60
SIGNED_IN_RE = re.compile(r"Logged in as (\S+?)\.?\s*$", re.M)


def signin_url(text: str) -> str | None:
    """The Google sign-in URL out of what ``agy acp --login`` printed."""
    for match in cfg.URL_RE.finditer(cfg.plain(text)):
        if "oauth" in match.group(0):
            return match.group(0)
    return None


def signed_in_as(text: str) -> str | None:
    match = SIGNED_IN_RE.search(cfg.plain(text))
    return match.group(1) if match else None


def remember_account(account: str | None) -> None:
    ACCOUNT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = ACCOUNT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"account": account,
                               "signed_in_at": cfg.now_iso()}) + "\n",
                   encoding="utf-8")
    tmp.replace(ACCOUNT_FILE)


def row_login(state: dict) -> dict:
    """The provider row's ``login`` object, in the Codex row's six keys."""
    return {"expires_at": None, "days_left": None,
            "source": state.get("source"), "account": state.get("account"),
            "plan": None, "status": None}


def sign_out() -> dict:
    """Remove the login from the box: the token file the CLI keeps (there is
    no keyring here to hold another copy) and the account the broker kept.
    Every Antigravity session stops authenticating from its next turn."""
    removed = False
    for path in (TOKEN_FILE, ACCOUNT_FILE):
        try:
            path.unlink()
            removed = removed or path == TOKEN_FILE
        except FileNotFoundError:
            pass
    return {"removed": removed, "login": row_login(login_state(fresh=True))}


# -- usage on demand: the CLI's own /quota (2026-11-15) ---------------------
#
# Found in 1.5.9: ``agy -p /quota`` (``/usage`` is the same command) answers
# without a turn. In ``--output-format stream-json`` it is one
# ``command_result`` event and a ``result`` with ``num_turns`` 0, every token
# count 0 and an empty ``conversation_id``: no model call, no conversation
# made. Five reads on 2026-11-15 (three plain, two stream-json, ~1.9 s each)
# moved no ``remaining_fraction`` in its last digit, so a read spends nothing
# and it is read on the cadence the other two are (loam_provider_usage).
#
# What it answers, abridged: ``command.data.groups``, each a pool of models
# (``Gemini Models``; ``Claude and GPT models``) with ``buckets``, each
# ``{id, name, window: "weekly"|"5h", remaining_fraction, reset_time}``.
# ``remaining_fraction`` is what is LEFT; the row's ``utilization`` is what
# is USED, as Claude's and Codex's are, so it is ``1 - remaining``. The
# printed form -- ``<pool>\t<window name>\t<percent left>%\t<reset ISO>`` per
# line, in ``result.response`` -- is the fallback when the structured data is
# absent, and a line it cannot read is kept under ``unparsed``, never fatal.
#
# Every bucket is served under the row's ``buckets``, keyed by Antigravity's
# own id and labelled ``<pool> · <window>`` (``limit_name``), because there is
# no one pool to call the row's own: a Gemini session and a Claude-on-agy
# session spend different ones. ``/credits`` (paid overflow, ``useG1Credits``)
# is read beside it and served as ``credits`` in Codex's shape, whose
# ``hasCredits`` false the page draws as no line, so a balance of 0 shows
# nothing and any other balance shows.

QUOTA_ARGV = ("-p", "/quota", "--output-format", "stream-json")
CREDITS_ARGV = ("-p", "/credits", "--output-format", "stream-json")
USAGE_READ_TIMEOUT_S = 30
USAGE_FILE = cfg.STATE_DIR / "antigravity" / "usage.json"
WINDOW_MINS = {"5h": 300, "weekly": 10080}
WINDOW_WORDS = {300: "5-hour", 10080: "Weekly"}
WINDOW_MINS_KEYS = {v: k for k, v in WINDOW_MINS.items()}


def window_mins(window) -> int | None:
    """``"5h"``, ``"weekly"`` or a printed name ("Five Hour Limit
    Remaining") as minutes, or None for one not recognised."""
    if not isinstance(window, str):
        return None
    w = window.strip().lower()
    if w in WINDOW_MINS:
        return WINDOW_MINS[w]
    if "week" in w:
        return 10080
    if re.search(r"\b(five|5)[\s-]*h(ou)?r?", w):
        return 300
    m = re.fullmatch(r"(\d+)\s*(h|d|m)", w)
    if m:
        return int(m.group(1)) * {"m": 1, "h": 60, "d": 1440}[m.group(2)]
    return None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "bucket"


def _bucket(group: str, name, window, remaining, reset) -> dict:
    mins = window_mins(window) or window_mins(name)
    used = None
    if isinstance(remaining, (int, float)) and not isinstance(remaining, bool):
        used = round(min(1.0, max(0.0, 1.0 - float(remaining))), 6)
    iso, epoch = cfg._iso_epoch(reset)
    word = WINDOW_WORDS.get(mins) if mins else None
    word = word or (name if isinstance(name, str) and name else window) or "window"
    return {"utilization": used, "remaining": remaining,
            "resets_at": iso, "resets_at_epoch": epoch, "window_mins": mins,
            "limit_name": f"{group} · {word}" if group else str(word)}


def _ordered(buckets: dict) -> dict:
    """Pool by pool as Antigravity listed them, the shorter window first
    within a pool, as the other rows lead with five_hour. Each bucket comes
    in carrying ``_pool``, which goes out with it."""
    pools = list(dict.fromkeys(b["_pool"] for b in buckets.values()))
    items = sorted(buckets.items(), key=lambda kv: (
        pools.index(kv[1]["_pool"]), kv[1]["window_mins"] or 10**9))
    return {k: {x: y for x, y in v.items() if x != "_pool"} for k, v in items}


def buckets_from_data(data) -> dict:
    """``command.data`` of a ``/quota`` answer as ``{id: bucket}``."""
    out: dict = {}
    groups = data.get("groups") if isinstance(data, dict) else None
    for group in groups if isinstance(groups, list) else ():
        if not isinstance(group, dict):
            continue
        pool = group.get("name") if isinstance(group.get("name"), str) else ""
        for raw in group.get("buckets") or ():
            if not isinstance(raw, dict):
                continue
            bid = raw.get("id") if isinstance(raw.get("id"), str) and raw.get("id") \
                else f"{_slug(pool)}-{_slug(str(raw.get('window') or raw.get('name')))}"
            out[bid] = {**_bucket(pool, raw.get("name"), raw.get("window"),
                                  raw.get("remaining_fraction"),
                                  raw.get("reset_time")), "_pool": pool}
    return _ordered(out)


PERCENT_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*%\s*$")


def buckets_from_text(text: str) -> tuple[dict, list[str]]:
    """The printed form, ``<pool>\t<window>\t<N>%\t<reset>`` a line: the
    buckets it names, and every non-blank line it could not read."""
    out: dict = {}
    unparsed: list[str] = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split("\t")]
        m = PERCENT_RE.match(parts[2]) if len(parts) == 4 else None
        if not m:
            unparsed.append(line.strip())
            continue
        pool, name, _, reset = parts
        mins = window_mins(name)
        bid = f"{_slug(pool)}-{WINDOW_MINS_KEYS.get(mins) or _slug(name)}"
        out[bid] = {**_bucket(pool, name, None, float(m.group(1)) / 100, reset),
                    "_pool": pool}
    return _ordered(out), unparsed



def command_answer(stdout: str) -> tuple[dict | None, str]:
    """``(command.data, result.response)`` out of one stream-json answer;
    either may be missing. A turn that ran (``num_turns`` above 0) is not an
    answer to a slash command, and yields neither."""
    data, response, turns = None, "", 0
    for line in (stdout or "").splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("event") == "command_result":
            data = ((obj.get("command") or {}).get("data"))
        elif obj.get("event") == "result" and isinstance(obj.get("result"), dict):
            r = obj["result"]
            turns = r.get("num_turns") or 0
            response = r.get("response") if isinstance(r.get("response"), str) else ""
            if data is None:
                data = (r.get("command") or {}).get("data")
    if turns:
        return None, ""
    return (data if isinstance(data, dict) else None), response


def credits_from(data: dict | None, response: str = "") -> dict | None:
    """``/credits`` in Codex's ``credits`` shape, or None when unread."""
    n = data.get("remaining_credits") if isinstance(data, dict) else None
    if n is None:
        m = re.search(r"Remaining credits\t\s*(\d+(?:\.\d+)?)", response or "")
        n = float(m.group(1)) if m else None
        n = int(n) if isinstance(n, float) and n.is_integer() else n
    if not isinstance(n, (int, float)) or isinstance(n, bool):
        return None
    out = {"hasCredits": n > 0, "unlimited": False, "balance": str(n)}
    if isinstance(data, dict) and isinstance(data.get("upgrade_uri"), str):
        out["upgrade_uri"] = data["upgrade_uri"]
    return out


def usage_from_read(quota_stdout: str, credits_stdout: str | None = None,
                    account: str | None = None) -> dict | None:
    """One ``/quota`` (and ``/credits``) answer as a whole new reading, in
    the row's shape; None when it names no bucket."""
    data, response = command_answer(quota_stdout)
    buckets = buckets_from_data(data)
    unparsed: list[str] = []
    if not buckets:
        buckets, unparsed = buckets_from_text(response)
    if not buckets:
        return None
    out: dict = {"provider": "antigravity", "seen_at": cfg.now_iso(),
                 "source": "read", "session_id": None, "account": account,
                 "status": None, "is_using_overage": None, "planType": None,
                 "credits": None, "buckets": buckets}
    if unparsed:
        out["unparsed"] = unparsed
    if credits_stdout is not None:
        out["credits"] = credits_from(*command_answer(credits_stdout))
    return out


def _run(argv: tuple[str, ...]) -> str | None:
    try:
        proc = subprocess.run([AGY, *argv], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, env=env(),
                              cwd=str(cfg.STATE_DIR),
                              timeout=USAGE_READ_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def read_usage() -> dict | None:
    try:
        data = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_quota() -> dict | None:
    """``read``: ``/quota`` and ``/credits``, kept on disk in place of the
    last reading and answered. None, and the file untouched, when there is
    no login or ``/quota`` names nothing; a failed ``/credits`` alone is a
    reading with ``credits`` null."""
    state = login_state()
    if not state["connected"]:
        return None
    quota = _run(QUOTA_ARGV)
    if quota is None:
        return None
    usage = usage_from_read(quota, _run(CREDITS_ARGV) or "", state["account"])
    if usage is None:
        return None
    usage_store.write_json(USAGE_FILE, usage)
    return usage


def row_usage() -> dict | None:
    """The row's ``usage``: the kept reading, if it was taken on the login
    the box holds now, else None."""
    usage = read_usage()
    state = login_state()
    if usage is None or not state["connected"] \
            or usage.get("account") != state["account"]:
        return None
    return usage


def reader() -> usage_store.Reader:
    """Observe-nothing: a headless turn states tokens and no quota, so the
    reading moves only by ``read``."""
    return usage_store.Reader("antigravity", observe=lambda _event: None,
                              kept=row_usage, read=read_quota)


# -- the persona -----------------------------------------------------------

def images_hook() -> dict:
    return {"PreToolUse": [{
        "matcher": "^(generate_image|invoke_subagent)$",
        "hooks": [{"type": "command", "timeout": 10,
                   "command": "python3 " + shlex.quote(str(lc.ROOT / "hooks" / "antigravity_images.py"))}],
    }]}


def loading_size_hook() -> dict:
    cmd = "python3 " + shlex.quote(str(lc.ROOT / "hooks" / "antigravity_loading_size.py"))
    return {
        "PostToolUse": [{
            "matcher": LOADING_SIZE_MATCHER,
            "hooks": [{"type": "command", "timeout": 15, "command": cmd}],
        }],
        "PostInvocation": [{"type": "command", "timeout": 10, "command": cmd}],
    }


def workspace_hooks(workspace: Path) -> dict:
    path = workspace / HOOKS_FILE
    try:
        hooks = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(hooks, dict):
        raise ValueError(f"{path} must contain an object of hook groups")
    return hooks


def sync_images_hook(workspace: Path, enabled: bool) -> bool:
    """Render Loam's named hook group in the CLI's shared workspace file."""
    hooks = workspace_hooks(workspace)
    desired = images_hook() if enabled else None
    if hooks.get(IMAGES_HOOK) == desired:
        return False
    if enabled:
        hooks[IMAGES_HOOK] = desired
    else:
        del hooks[IMAGES_HOOK]
    _write_workspace_file(workspace / HOOKS_FILE, json.dumps(hooks, indent=2) + "\n")
    return True


def sync_loading_size_hook(workspace: Path) -> bool:
    """Keep Loam's post-write size-notice hook group in the workspace file."""
    hooks = workspace_hooks(workspace)
    desired = loading_size_hook()
    if hooks.get(LOADING_SIZE_HOOK) == desired:
        return False
    hooks[LOADING_SIZE_HOOK] = desired
    _write_workspace_file(workspace / HOOKS_FILE, json.dumps(hooks, indent=2) + "\n")
    return True


def _write_workspace_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=f".{path.name}-", delete=False) as tmp:
        temp_path = Path(tmp.name)
        try:
            tmp.write(text)
            tmp.close()
            temp_path.replace(path)
        finally:
            temp_path.unlink(missing_ok=True)


def instructions(workspace: Path) -> str:
    """The same files Codex is given (``loam_codex.instructions``), under
    this provider's line."""
    import loam_codex as codex  # noqa: PLC0415
    text = codex.instructions(workspace)
    return text.replace(codex.PROVIDER_LINE, PROVIDER_LINE, 1)


def _utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def _rule_header(part: int, total: int) -> str:
    return (
        "---\n"
        "trigger: always_on\n"
        "---\n\n"
        "<!-- Rendered by Loam's broker before each Antigravity turn "
        f"from CLAUDE.md and its imports (part {part} of {total}). "
        "Generated file. -->\n\n"
    )


def _split_by_bytes(text: str, limit: int) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    size = 0
    for ch in text:
        ch_bytes = len(ch.encode("utf-8"))
        if buf and size + ch_bytes > limit:
            out.append("".join(buf))
            buf = [ch]
            size = ch_bytes
        else:
            buf.append(ch)
            size += ch_bytes
    if buf:
        out.append("".join(buf))
    return out


def _pack_units(units: list[str], sep: str, limit: int) -> list[str]:
    sep_bytes = _utf8_len(sep)
    chunks: list[str] = []
    current = ""
    current_bytes = 0
    for unit in units:
        unit_bytes = _utf8_len(unit)
        if not current:
            current, current_bytes = unit, unit_bytes
        elif current_bytes + sep_bytes + unit_bytes <= limit:
            current = current + sep + unit
            current_bytes += sep_bytes + unit_bytes
        else:
            chunks.append(current)
            current, current_bytes = unit, unit_bytes
    if current:
        chunks.append(current)
    return chunks


def _split_piece(piece: str, limit: int) -> list[str]:
    if _utf8_len(piece) <= limit:
        return [piece]
    para_units: list[str] = []
    for para in piece.split("\n\n"):
        if _utf8_len(para) <= limit:
            para_units.append(para)
            continue
        line_units: list[str] = []
        for line in para.split("\n"):
            if _utf8_len(line) <= limit:
                line_units.append(line)
            else:
                line_units.extend(_split_by_bytes(line, limit))
        para_units.extend(_pack_units(line_units, "\n", limit))
    return _pack_units(para_units, "\n\n", limit)


def _instruction_sections(text: str) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return [""]
    marker = "\n\nContents of "
    raw = stripped.split(marker)
    sections = [raw[0]] + [f"Contents of {part}" for part in raw[1:]]
    return sections


def split_rules(text: str, max_bytes: int = RULE_MAX_BYTES) -> list[tuple[Path, str]]:
    """Split rendered instructions into one or more ``always_on`` rule files,
    each guaranteed to fit within ``max_bytes`` UTF-8 bytes."""
    overhead = _utf8_len(_rule_header(999, 999)) + 1
    body_limit = max_bytes - overhead
    if body_limit <= 0:
        raise ValueError(f"max_bytes ({max_bytes}) is too small for the rule header")
    sep_bytes = 2  # "\n\n"
    chunks: list[str] = []
    current = ""
    current_bytes = 0
    for section in _instruction_sections(text):
        sec_bytes = _utf8_len(section)
        if sec_bytes <= body_limit:
            if not current:
                current, current_bytes = section, sec_bytes
            elif current_bytes + sep_bytes + sec_bytes <= body_limit:
                current = current + "\n\n" + section
                current_bytes += sep_bytes + sec_bytes
            else:
                chunks.append(current)
                current, current_bytes = section, sec_bytes
        else:
            if current:
                chunks.append(current)
                current, current_bytes = "", 0
            parts = _split_piece(section, body_limit)
            chunks.extend(parts[:-1])
            current = parts[-1]
            current_bytes = _utf8_len(current)
    if current or not chunks:
        chunks.append(current)
    total = len(chunks)
    width = max(2, len(str(total)))
    out: list[tuple[Path, str]] = []
    for index, body in enumerate(chunks, 1):
        rel = RULES_DIR / f"{RULES_PREFIX}-{index:0{width}d}.md"
        content = _rule_header(index, total) + (body + "\n" if body else "")
        out.append((rel, content))
    return out


def render_rule_files(workspace: Path,
                      max_bytes: int = RULE_MAX_BYTES) -> list[tuple[Path, str]]:
    return split_rules(instructions(workspace), max_bytes=max_bytes)


def managed_rule_files(workspace: Path) -> list[Path]:
    rules_dir = workspace / RULES_DIR
    try:
        entries = sorted(rules_dir.iterdir())
    except OSError:
        return []
    return [p for p in entries
            if (p.is_file() or p.is_symlink()) and RULE_PART_RE.match(p.name)]


def has_instructions(workspace: Path) -> bool:
    return bool(managed_rule_files(workspace))


def sync_instructions(workspace: Path,
                      max_bytes: int = RULE_MAX_BYTES) -> tuple[list[Path], bool]:
    """Write the split ``always_on`` rule files and remove obsolete
    ``loam-persona.md`` or stale extra parts. Returns ``(paths, changed)``."""
    rendered = render_rule_files(workspace, max_bytes=max_bytes)
    wanted_names = {rel.name for rel, _ in rendered}
    changed = False
    paths: list[Path] = []
    for rel, text in rendered:
        path = workspace / rel
        paths.append(path)
        try:
            if path.read_text(encoding="utf-8") == text:
                continue
        except OSError:
            pass
        _write_workspace_file(path, text)
        changed = True
    for entry in managed_rule_files(workspace):
        if entry.name not in wanted_names:
            entry.unlink(missing_ok=True)
            changed = True
    return paths, changed


def write_instructions(workspace: Path) -> Path:
    paths, _ = sync_instructions(workspace)
    return paths[0]


def rule_file_issues(workspace: Path) -> list[str]:
    """Check Antigravity's rendered persona rule files for obsolete files,
    missing frontmatter, or oversized parts without diffing dynamic persona
    content."""
    issues: list[str] = []
    legacy = workspace / LEGACY_RULES_FILE
    if legacy.exists() or legacy.is_symlink():
        issues.append(f"obsolete {LEGACY_RULES_FILE} (missing trigger frontmatter) is still present")
    parts = [p for p in managed_rule_files(workspace) if p.name != LEGACY_RULES_FILE.name]
    if (workspace / "CLAUDE.md").is_file() and not parts:
        issues.append(f"missing {RULES_FILE}")
    for part in parts:
        rel = part.relative_to(workspace)
        try:
            raw = part.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            issues.append(f"{rel} unreadable ({exc})")
            continue
        if not RULE_TRIGGER_RE.match(text):
            issues.append(f"{rel} is missing `trigger: always_on` frontmatter")
        if len(raw) > AGY_RULE_LIMIT_BYTES:
            issues.append(
                f"{rel} is {len(raw)} bytes (over Antigravity's "
                f"{AGY_RULE_LIMIT_BYTES}-byte per-file cap)")
    return issues


def _within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def skill_dirs(workspace: Path | None = None,
               vault: Path | None = None) -> list[Path]:
    """Existing resolved directories outside ``workspace`` that Antigravity
    needs via ``--add-dir`` so symlinks in ``.agents/skills/`` pass its
    workspace-root containment check (agy 1.5.12 rejects targets outside
    ``workspaceDirs`` with ``escapes workspace root``)."""
    import loam_skills  # noqa: PLC0415
    ws_root = workspace.resolve() if workspace is not None else None
    dirs: list[Path] = []

    def add(candidate: Path) -> None:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            return
        if not resolved.is_dir():
            return
        if ws_root is not None and _within(resolved, ws_root):
            return
        if any(_within(resolved, existing) for existing in dirs):
            return
        dirs[:] = [existing for existing in dirs if not _within(existing, resolved)]
        dirs.append(resolved)

    for _label, shelf in loam_skills.shelves(vault):
        add(shelf)
    if workspace is not None:
        view = loam_skills.view_dir(workspace)
        try:
            entries = sorted(view.iterdir())
        except OSError:
            entries = []
        for entry in entries:
            if entry.is_symlink():
                add(entry)
    return dirs


def argv(prompt: str, conversation: str | None, model: str | None,
         effort: str | None, extra_dirs: Iterable[Path] = ()) -> list[str]:
    """Build a headless turn with the configured permission mode. This invocation has no
    interactive approval channel.
    """
    out = [AGY, f"--print={prompt}", "--output-format", "stream-json",
           "--dangerously-skip-permissions"]
    if conversation:
        out += ["--conversation", conversation]
    if model:
        out += ["--model", model]
    if effort and not carries_level(model):
        # An id that ends in its level carries it (LEVEL_RE above).
        out += ["--effort", effort]
    for d in extra_dirs:
        out += ["--add-dir", str(d)]
    return out


def prompt_arg(session_id: str, text: str) -> str:
    """``text``, or a pointer to a file holding it when it is too long for
    one argv string."""
    if len(text) <= ARG_MAX_CHARS:
        return text
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    path = PROMPTS_DIR / f"{session_id}-{int(time.time() * 1000)}.md"
    path.write_text(text, encoding="utf-8")
    return (f"The message for this turn is too long to pass inline. Read "
            f"{path} in full; it is the message, and answer it.")


EFFORTS = ["low", "medium", "high", "xhigh", "max"]


# -- the translation -------------------------------------------------------

TEXT_STEPS = ("agent_response",)
SILENT_STEPS = ("user_input", "checkpoint")


def _usage(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    return {"input_tokens": raw.get("input_tokens") or 0,
            "cache_read_input_tokens": raw.get("cache_read_tokens") or 0,
            "cache_creation_input_tokens": 0,
            "output_tokens": (raw.get("output_tokens") or 0)
            + (raw.get("thinking_tokens") or 0)}


def _tool_input(step: dict) -> dict:
    info = step.get("tool_info")
    if isinstance(info, dict):
        args = (info.get("parameters") or info.get("args") or info.get("input")
                or info.get("arguments"))
        if isinstance(args, dict):
            return args
        return {k: v for k, v in info.items() if isinstance(v, (str, int, float))}
    return {}


def _tool_output(step: dict) -> str:
    info = step.get("tool_info")
    if isinstance(info, dict):
        for key in ("output", "result", "response", "error"):
            value = info.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


class Translator:
    """One turn's translation state. ``feed(event)`` answers the broker
    events (without ``seq``) one ``agy`` stream-json line becomes."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.model: str | None = None
        self.conversation_id: str | None = None
        self._text: dict = {}       # step_index -> text so far
        self._tools: set = set()    # step indexes whose call was drawn
        self.done = False

    def _base(self, kind: str, **rest) -> dict:
        return {"type": kind, "session_id": self.session_id, **rest}

    def _stream(self, event: dict) -> dict:
        return self._base("stream_event", event=event, parent_tool_use_id=None)

    def _parent(self, step: dict):
        sub = step.get("subagent_info")
        if isinstance(sub, dict) and sub:
            return f"agy-sub-{sub.get('id') or sub.get('name') or 'agent'}"
        return None

    def feed(self, obj: dict) -> list[dict]:
        if not isinstance(obj, dict):
            return []
        kind = obj.get("event")
        if kind == "init":
            init = obj.get("init") if isinstance(obj.get("init"), dict) else {}
            self.model = init.get("model") or self.model
            return [self._base("system", subtype="init", provider="antigravity",
                               model=self.model,
                               permission_mode=init.get("permission_mode"),
                               tools=init.get("tools"))]
        if kind == "step_update":
            # The step is under its own key on the wire (1.5.9, read off a
            # signed-in run 2026-11-15); flat in the shape first guessed.
            inner = obj.get("step_update")
            return self._step(inner if isinstance(inner, dict) else obj)
        if kind == "result":
            return [self.result(obj.get("result") or {})]
        return []

    def _step(self, step: dict) -> list[dict]:
        self.conversation_id = step.get("conversation_id") or self.conversation_id
        index = step.get("step_index")
        stype = step.get("step_type")
        state = step.get("state")
        parent = self._parent(step)
        if stype in SILENT_STEPS:
            return []
        if stype in TEXT_STEPS:
            out = []
            delta = step.get("text_delta") or ""
            if index not in self._text:
                self._text[index] = ""
                out += [self._stream({"type": "message_start",
                                      "message": {"id": f"agy-{index}",
                                                  "role": "assistant",
                                                  "model": self.model,
                                                  "content": []}}),
                        self._stream({"type": "content_block_start", "index": 0,
                                      "content_block": {"type": "text", "text": ""}})]
            if delta:
                self._text[index] += delta
                if parent is None:
                    out.append(self._stream({"type": "content_block_delta",
                                             "index": 0,
                                             "delta": {"type": "text_delta",
                                                       "text": delta}}))
            if state == "DONE":
                text = self._text.pop(index, "")
                out += [self._stream({"type": "content_block_stop", "index": 0}),
                        self._stream({"type": "message_stop"}),
                        self._base("assistant", parent_tool_use_id=parent,
                                   message={"id": f"agy-{index}",
                                            "role": "assistant",
                                            "model": self.model,
                                            "content": [{"type": "text",
                                                         "text": text}],
                                            "usage": _usage(step.get("usage"))})]
            return out
        # Everything else with a name is a tool step.
        name = step.get("tool_name") or stype or "tool"
        call_id = f"agy-{index}"
        out = []
        if index not in self._tools:
            self._tools.add(index)
            out.append(self._base(
                "assistant", parent_tool_use_id=parent,
                message={"id": call_id, "role": "assistant", "model": self.model,
                         "content": [{"type": "tool_use", "id": call_id,
                                      "name": name, "input": _tool_input(step)}]}))
        if state == "DONE":
            out.append(self._base(
                "user", parent_tool_use_id=parent,
                message={"role": "user", "content": [{
                    "type": "tool_result", "tool_use_id": call_id,
                    "content": _tool_output(step)}]}))
        return out

    def result(self, res: dict) -> dict:
        self.done = True
        self.conversation_id = res.get("conversation_id") or self.conversation_id
        status = res.get("status")
        ok = status == "SUCCESS"
        subtype = "success" if ok else (
            "error_interrupted" if status in ("CANCELED", "INTERRUPTED")
            else "error_during_execution")
        seconds = res.get("duration_seconds")
        return self._base(
            "result", subtype=subtype, is_error=not ok,
            duration_ms=int(seconds * 1000) if isinstance(seconds, (int, float)) else None,
            num_turns=res.get("num_turns"), total_cost_usd=None,
            usage=_usage(res.get("usage")), modelUsage={},
            result=res.get("response") if ok else (res.get("error") or status),
            provider="antigravity", antigravity_status=status,
            conversation_id=self.conversation_id)
