"""Antigravity as a third provider for persona sessions (2026-11-09).

Google's Antigravity CLI, ``agy``, driven in its documented headless print
mode (https://antigravity.google/docs/cli/headless): one ``agy -p=<text>
--output-format stream-json`` process per turn, resumed by the
``conversation_id`` the first turn states (``--conversation <id>``). Its
NDJSON events -- ``init``, ``step_update``, ``result`` -- are translated onto
the Claude CLI's ``stream-json`` shapes the page already draws, by
``Translator`` below, a pure object with no process in it.

Why a process per turn and not ``--input-format stream-json``: the shape of
an input line is not documented, and before a login nothing can be learnt
from the binary about it (a piped stdin fails at authentication before any
line is parsed, 1.5.3, 2026-11-09). A turn per process needs no input shape
at all. The cost is a cold start per turn (~0.2 s to the auth check).

What is here, one authority each:

- the binary (``AGY``) and the environment a child runs in (``env``);
- the login, read by asking the CLI (``login_state``): ``agy models`` answers
  "Please sign in" in well under a second with no login, so it is the probe;
- the persona's instructions, rendered to a workspace rule file
  (``write_instructions``), because Antigravity reads ``AGENTS.md``,
  ``GEMINI.md`` and ``.agents/rules/*.md`` and not ``CLAUDE.md``;
- the launch line (``argv``);
- the translation (``Translator``).

The broker's half -- the session subclass and the provider row -- is in
``bin/session-broker``; the persona's ``.agents/`` (MCP servers, skills) is
rendered by ``bin/provision --antigravity``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import loam_config as lc

import loam_broker as cfg

# The official binary, as Google's installer puts it
# (``curl -fsSL https://antigravity.google/cli/install.sh | bash``, 1.5.3 on
# 2026-11-09). It updates itself in the background during runs; there is no
# pin, unlike Claude Code's and Codex's.
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
# only and the latter is shared. Generated on every session start; a
# hand-edit is overwritten.
RULES_FILE = Path(".agents") / "rules" / "loam-persona.md"
MCP_FILE = Path(".agents") / "mcp_config.json"

PROVIDER_LINE = ("You are running on Antigravity through Loam's broker: the "
                 "box's shell and Antigravity's own tools, and the MCP tools "
                 "below; Claude Code's Read, Edit, Grep, Agent, ToolSearch "
                 "and Skill are not here under those names.")

# A prompt longer than this goes in a file the turn is told to read: one
# argv string is capped at 128 KiB by the kernel (MAX_ARG_STRLEN).
ARG_MAX_CHARS = 100_000
PROMPTS_DIR = cfg.STATE_DIR / "antigravity" / "prompts"


def env(base: dict | None = None) -> dict:
    out = dict(os.environ if base is None else base)
    for name in STRIPPED_ENV:
        out.pop(name, None)
    return out


# -- the login -------------------------------------------------------------

_login_cache: tuple[float, dict] | None = None


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
    return {**base, "source": "google-account", "connected": True,
            "account": _account(), "models": parse_models(proc.stdout)}


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
    """``agy models`` as a list. The text form is not documented; a line
    whose first word looks like a slug is a model. Read back after the first
    signed-in run and tighten then."""
    out = []
    for line in text.splitlines():
        word = line.strip().split(" ")[0].strip("-*•")
        if re.fullmatch(r"[a-z0-9][a-z0-9.\-_/]+", word or "") and any(
                c.isdigit() for c in word):
            out.append({"id": word, "name": line.strip()})
    return out


_refreshing = threading.Lock()


def _refresh() -> dict:
    global _login_cache
    state = _probe()
    _login_cache = (time.monotonic(), state)
    return state


def login_state(fresh: bool = False) -> dict:
    """The login, by asking the CLI (~0.5 s), at most once a LOGIN_CACHE_S.

    Past the minute the kept answer is served and the CLI is asked behind
    the call (2026-11-09), so ``/v1/providers`` never waits on it; a sign-in
    shows on the first fetch after the re-ask, about a minute at most when
    the app is open. ``fresh`` (and the very first call) asks in line."""
    now = time.monotonic()
    if fresh or not _login_cache:
        return _refresh()
    if now - _login_cache[0] >= LOGIN_CACHE_S and _refreshing.acquire(blocking=False):
        def run():
            try:
                _refresh()
            finally:
                _refreshing.release()
        threading.Thread(target=run, name="antigravity-login", daemon=True).start()
    return _login_cache[1]


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


# -- the persona -----------------------------------------------------------

def instructions(workspace: Path) -> str:
    """The same files Codex is given (``loam_codex.instructions``), under
    this provider's line."""
    import loam_codex as codex  # noqa: PLC0415
    text = codex.instructions(workspace)
    return text.replace(codex.PROVIDER_LINE, PROVIDER_LINE, 1)


def write_instructions(workspace: Path) -> Path:
    path = workspace / RULES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    text = ("<!-- Rendered by Loam's broker at every Antigravity session "
            "start from CLAUDE.md and its imports. Not the persona's to "
            "edit: the next start writes it again. -->\n\n"
            + instructions(workspace))
    try:
        if path.read_text(encoding="utf-8") == text:
            return path
    except OSError:
        pass
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return path


def argv(prompt: str, conversation: str | None, model: str | None,
         effort: str | None) -> list[str]:
    """One turn's launch line. ``--dangerously-skip-permissions`` is the
    authority move's (2026-11-08) on this provider: ``permission_mode``
    ``always-proceed``, so nothing is soft-denied for want of a person to
    ask -- Antigravity's headless mode has no channel to ask one on."""
    out = [AGY, f"--print={prompt}", "--output-format", "stream-json",
           "--dangerously-skip-permissions"]
    if conversation:
        out += ["--conversation", conversation]
    if model:
        out += ["--model", model]
    if effort:
        out += ["--effort", effort]
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
        args = info.get("args") or info.get("input") or info.get("arguments")
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
            return self._step(obj)
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
