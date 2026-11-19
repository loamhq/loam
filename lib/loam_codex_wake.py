"""A persona's wake or cron on Codex (2026-11-13).

The owner, 11-13 (m247, recorded in the vault's *open-source — core and
plugins*, "Agreed 11-13: providers"): what Loam builds is the same on every
provider and must be used. A persona's wakes and crons are Loam's layer, so
they run on the persona's own provider (``agent.json`` ``provider``), not on
Claude Code whatever it is set to. bin/wake chooses; this is the Codex half.

**The same inputs as a Claude wake.** Claude Code's wake loads ``CLAUDE.md``
with its ``@`` imports (soul, config, ``_always.md``, reach.md), no
conversation style (``outputStyle: none``), and the lane's own file
(``_reflection.md`` or ``crons/<name>.md``) appended to the system prompt.
``instructions`` builds exactly that list, in that order, in the layout the
Codex conversation lane uses (``loam_codex.instructions``), with the lane's
file last where the conversation's ``_active.md`` would be. The tools are the
persona's own ``.codex/config.toml`` (the five MCP servers, the engineer
among them, every one approved; full access; Codex's live web search), the
same file a Codex conversation runs on, and the runtime's hooks are trusted
by hash as the broker trusts them.

**Why the app-server and not ``codex exec``.** The engineer jobs run on
``codex exec`` (lib/loam_codex_job.py), and that was the first choice here.
Two things decided against it, both measured on codex-cli 0.169.0:
``exec`` cannot set the multi-agent mode (``multi_agent_mode`` is not a
config key, ``--strict-config`` refuses it; the mode is a ``thread/start``
parameter only, and on 0.169.0 not honoured even there: see
``loam_codex.SUBAGENT_ASK``, the explicit ask both lanes send instead); and ``exec`` ends with its turn, so a subagent the wake did
not wait for is lost (engineer/memory, 2026-10-02). On the app-server the
wake starts its thread the way the broker starts a conversation's, and a
subagent that finishes after the root's turn hands its report back in a new
root turn, as the broker's ``CodexSession._wake`` does and as Claude Code's
task notification does. The app-server is also the conversation lane's own
path, so a wake and a conversation load the persona the same way.

**Unattended.** Nobody can answer a question. Every approval is granted, as
Claude Code's ``bypassPermissions`` grants it (the persona's config asks for
none anyway); a question for a person is answered with nothing, and Codex
then goes on with its own judgment ("If ``request_user_input`` returns no
answers, continue with best judgment", its own Default-mode text).

Pure stdlib, synchronous: bin/wake is a oneshot, one process per run.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import loam_codex as codex

# The line the runtime adds at the top of a wake's instructions: the
# conversation's, saying which launcher this is and that nobody is there.
PROVIDER_LINE = codex.PROVIDER_LINE.replace(
    "through Loam's broker",
    "through Loam's wake launcher (unattended: nobody is in a conversation "
    "with you, and a question for a person gets no answer)", 1)

REQUEST_TIMEOUT_S = 90
# How long to wait for anything at all from the app-server before checking
# that it is still alive; not a clock on the run (a wake has none).
POLL_S = 5.0

# Server requests that are an approval: granted, as bypassPermissions would.
APPROVALS = ("item/commandExecution/requestApproval",
             "item/fileChange/requestApproval",
             "item/permissions/requestApproval",
             "mcpServer/elicitation/request",
             "item/tool/requestUserInput",
             "execCommandApproval", "applyPatchApproval")

TOOL_ITEMS = ("commandExecution", "fileChange", "mcpToolCall", "webSearch",
              "imageGeneration", "dynamicToolCall", "collabAgentToolCall")


class CodexWakeError(Exception):
    """The app-server could not run the lane: not started, not answering,
    or a request refused. ``refused`` is set when it is the account."""

    def __init__(self, message: str, refused: bool = False):
        super().__init__(message)
        self.refused = refused


def lane_block(lane_label: str, lane_path: Path, lane_text: str) -> str:
    return f"# Lane: {lane_label} ({lane_path})\n\n{lane_text.strip()}"


def instructions(workspace: Path, lane_label: str, lane_path: Path | None,
                 lane_text: str | None) -> str:
    """The wake's instructions: the provider line, ``CLAUDE.md`` and its
    imports in Claude Code's order, each under ``Contents of <path>`` as the
    Codex conversation lane presents them, then the lane's own file
    (frontmatter already stripped by ``loam_memory.resolve_lane``). No
    ``_active.md``: a wake has no conversation layer on either provider.

    ``lane_text`` is None for the reflection wake of an agent with a map
    (2026-11-18): no lane file, as on Claude Code; its instruction is the
    prompt, and what loads is what its conversations load."""
    parts = [PROVIDER_LINE]
    for source in codex._imports(workspace / "CLAUDE.md", 0, set()):  # noqa: SLF001
        parts.append(f"Contents of {source}:\n\n"
                     f"{source.read_text(encoding='utf-8').strip()}")
    if lane_text is not None and lane_path is not None:
        parts.append(lane_block(lane_label, lane_path, lane_text))
    return "\n\n".join(parts) + "\n"


def instruction_sources(workspace: Path) -> list[Path]:
    """The memory files a wake loads before its lane file, in order."""
    return codex._imports(workspace / "CLAUDE.md", 0, set())  # noqa: SLF001


def unattended_answer(method: str, params: dict) -> dict | None:
    """What an unattended run answers a server request with, or None for
    one it does not handle (refused, as the broker refuses it)."""
    if method not in APPROVALS:
        return None
    if method == "item/tool/requestUserInput":
        return {"answers": {}}
    if method == "mcpServer/elicitation/request":
        import loam_questions as questions_fmt  # noqa: PLC0415
        try:
            asked = questions_fmt.from_elicitation(params)
        except Exception:  # noqa: BLE001 -- a shape it cannot read is a form
            asked = True
        # A form asks a person to fill something in: nobody can. A bare
        # approval (an MCP call asking leave) is granted.
        return {"action": "decline"} if asked else {"action": "accept", "content": {}}
    if method == "item/permissions/requestApproval":
        return {"permissions": params.get("permissions") or {}, "scope": "turn"}
    if method in ("execCommandApproval", "applyPatchApproval"):
        return {"decision": "approved"}
    return {"decision": "accept"}


def envelope(task_id: str, name: str, said: str | None) -> str:
    """The ``<task-notification>`` a root turn is handed when a subagent or
    a call it left running finishes after its turn: the broker's
    ``wake_envelope`` shape, which is Claude Code's."""
    said = (said or "").strip() or "(it said nothing)"
    return ("<task-notification>\n"
            f"<task-id>{task_id}</task-id>\n"
            "<status>completed</status>\n"
            f"<summary>{name} finished after your turn had ended. "
            "What it finished with is below.</summary>\n"
            f"<result>\n{said}\n</result>\n"
            "</task-notification>")


@dataclass
class Outcome:
    """What one run on the app-server came to."""
    ok: bool = False
    reply: str = ""
    thread_id: str | None = None
    model: str | None = None
    turns: int = 0                 # root turns, the first and every wake
    tool_calls: int = 0            # tool items completed, every thread
    subagents: int = 0             # subagent threads that ran
    error: str | None = None
    error_kind: str | None = None  # Codex's codexErrorInfo, when it gave one
    usage: dict = field(default_factory=dict)   # summed over every thread
    events: list = field(default_factory=list)  # (thread, method) seen, for tests

    @property
    def round_trips(self) -> int:
        """Model responses: one per tool call, plus each turn's last."""
        return self.tool_calls + self.turns


class AppServer:
    """One ``codex app-server`` in the persona's workspace, spoken to over
    stdio; a reader thread puts every line on a queue."""

    def __init__(self, workspace: Path, env: dict, argv: list[str] | None = None):
        self.proc = subprocess.Popen(
            argv or [codex.CODEX, *codex.APP_SERVER_ARGV], cwd=str(workspace),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, env=env,
            start_new_session=True)
        self.inbox: queue.Queue = queue.Queue()
        self._next = 0
        self.held: list[dict] = []   # notifications read while awaiting an answer
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                self.inbox.put(obj)
        self.inbox.put(None)

    def send(self, obj: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", **obj}) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict,
                timeout: float = REQUEST_TIMEOUT_S) -> dict:
        self._next += 1
        ident = self._next
        self.send({"id": ident, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise CodexWakeError(f"codex did not answer {method} in {timeout:.0f}s")
            try:
                obj = self.inbox.get(timeout=left)
            except queue.Empty:
                continue
            if obj is None:
                self.inbox.put(None)
                raise CodexWakeError(f"codex app-server exited during {method}")
            if obj.get("id") == ident and "method" not in obj:
                if "error" in obj:
                    err = obj.get("error") or {}
                    message = err.get("message") if isinstance(err, dict) else str(err)
                    raise CodexWakeError(f"codex {method}: {message}",
                                         refused=_account_words(message))
                result = obj.get("result")
                return result if isinstance(result, dict) else {}
            self.held.append(obj)

    def next(self, timeout: float = POLL_S):
        """The next message, ``None`` when the process is gone, or
        ``False`` when nothing came within ``timeout``."""
        if self.held:
            return self.held.pop(0)
        try:
            obj = self.inbox.get(timeout=timeout)
        except queue.Empty:
            return False
        if obj is None:
            self.inbox.put(None)
        return obj

    def close(self) -> None:
        codex.kill_group(self.proc.pid, 15)
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            codex.kill_group(self.proc.pid)
            self.proc.wait()


ACCOUNT_WORDS = ("401", "unauthorized", "not logged in", "log in", "login",
                 "usage limit", "usagelimitexceeded", "rate limit")


def _account_words(text: str | None) -> bool:
    flat = (text or "").lower()
    return any(word in flat for word in ACCOUNT_WORDS)


def _usage_add(into: dict, total: dict) -> None:
    for key, value in total.items():
        if isinstance(value, int) and not isinstance(value, bool):
            into[key] = into.get(key, 0) + value


def open_server(workspace: Path, env: dict | None = None,
                argv: list[str] | None = None) -> AppServer:
    """An app-server for this persona, initialised, with the workspace and
    the runtime's hooks trusted the way the broker trusts them before a
    conversation's thread starts.

    The persona's conversation instructions file is written too, as the
    broker writes it at every start: the persona's ``.codex/config.toml``
    names it (``model_instructions_file``) and Codex refuses to start a
    thread when it is missing (measured on 0.169.0, 2026-11-13), even
    though the thread's ``baseInstructions`` -- the wake's own -- are what
    the model is given (the same measurement: the base instructions' codeword
    came back, the file's did not)."""
    try:
        name = json.loads((workspace / "agent.json").read_text(encoding="utf-8"))["name"]
    except (OSError, ValueError, KeyError, TypeError):
        name = None   # not a persona: nothing of anybody's to write
    if name:
        codex.write_instructions(name, workspace)
        # The shared skills view, current for this run (first release L7).
        import loam_skills  # noqa: PLC0415
        loam_skills.quiet_refresh(workspace)
    codex.write_trust(sorted(set(codex.persona_workspaces()) | {workspace}),
                      codex.read_trusted_hooks())
    server = AppServer(workspace, env if env is not None else codex.env(), argv)
    try:
        server.request("initialize", {
            "clientInfo": {"name": "loam-wake", "title": "Loam", "version": "1"}})
        server.send({"method": "initialized"})
        hooks = server.request("hooks/list", {"cwds": [str(workspace)]})
        mine = codex.runtime_hooks(hooks.get("data"), workspace)
        prefix = str(workspace / ".codex" / "config.toml") + ":"
        trusted = {k: v for k, v in codex.read_trusted_hooks().items()
                   if not k.startswith(prefix)}
        codex.write_trust(sorted(set(codex.persona_workspaces()) | {workspace}),
                          {**trusted, **mine})
    except Exception:
        server.close()
        raise
    return server


def start_thread(server: AppServer, workspace: Path, instructions_text: str,
                 model: str | None, ephemeral: bool = False) -> dict:
    params = {"cwd": str(workspace), "baseInstructions": instructions_text,
              "developerInstructions": codex.SUBAGENT_ASK}
    if model:
        params["model"] = model
    if ephemeral:
        params["ephemeral"] = True
    return server.request("thread/start", params)


def drive(server: AppServer, thread_id: str, prompt: str, *,
          model: str | None = None, effort: str | None = None,
          outcome: Outcome | None = None, deadline: float | None = None) -> Outcome:
    """Run ``prompt`` as a root turn of ``thread_id`` and everything it
    leads to: subagents that finish after the root's turn, and calls the
    root left running, each handed back in a new root turn, until nothing
    is left running. Raises ``subprocess.TimeoutExpired`` past ``deadline``
    (a monotonic time; None, every lane since 2026-11-10, is none)."""
    out = outcome or Outcome(thread_id=thread_id)

    def turn(text: str) -> None:
        params = {"threadId": thread_id, "input": [{"type": "text", "text": text}]}
        if model:
            params["model"] = model
        if effort:
            params["effort"] = effort
        server.request("turn/start", params)
        out.turns += 1

    children: dict[str, str] = {}      # subagent thread -> its path (name)
    parent_of: dict[str, str] = {}     # subagent thread -> the thread that spawned it
    running: set[str] = set()          # subagent threads with a turn open
    said: dict[str, str] = {}          # subagent thread -> its last message
    totals: dict[str, dict] = {}       # thread -> its tokenUsage.total
    open_calls: dict[str, dict] = {}   # root call item id -> the item, while open
    owed: list[str] = []               # envelopes for the next root turn
    late: set[str] = set()             # root calls open when its turn ended
    root_open = True
    turn(prompt)
    while True:
        if deadline is not None and time.monotonic() > deadline:
            raise subprocess.TimeoutExpired("codex app-server", 0)
        if not root_open and not running and not late and not owed:
            break
        if not root_open and owed and not running and not late:
            text, owed[:] = "\n\n".join(owed), []
            root_open = True
            turn(text)
            continue
        obj = server.next()
        if obj is False:
            continue
        if obj is None:
            out.error = out.error or "codex app-server exited before the run ended"
            break
        method = obj.get("method")
        params = obj.get("params") if isinstance(obj.get("params"), dict) else {}
        if "id" in obj and method:
            answer = unattended_answer(method, params)
            if answer is None:
                server.send({"id": obj["id"], "error": {
                    "code": -32601, "message": f"{method} is not handled by this client"}})
            else:
                server.send({"id": obj["id"], "result": answer})
            continue
        if not method:
            continue
        thread = params.get("threadId")
        out.events.append((thread, method))
        item = params.get("item") if isinstance(params.get("item"), dict) else {}
        if method == "account/rateLimits/updated":
            try:
                codex.keep_rate_limits(params)
            except OSError:
                pass
            continue
        if (item.get("type") == "subAgentActivity" and item.get("agentThreadId")):
            child = item["agentThreadId"]
            if child not in children:
                children[child] = item.get("agentPath") or child
                out.subagents += 1
            if thread:
                parent_of.setdefault(child, thread)
        if method == "thread/tokenUsage/updated":
            total = (params.get("tokenUsage") or {}).get("total")
            if isinstance(total, dict) and thread:
                totals[thread] = total
            continue
        if method == "item/completed" and item.get("type") in TOOL_ITEMS:
            out.tool_calls += 1
        if thread and thread != thread_id:
            if (method == "item/completed" and item.get("type") == "agentMessage"
                    and isinstance(item.get("text"), str) and item["text"].strip()):
                said[thread] = item["text"]
            ended = (method in ("turn/completed", "thread/closed")
                     or (method == "thread/status/changed"
                         and (params.get("status") or {}).get("type")
                         in ("idle", "systemError", "notLoaded")))
            if method == "turn/started" or (
                    method == "thread/status/changed"
                    and (params.get("status") or {}).get("type") == "active"):
                running.add(thread)
            elif ended and thread in running:
                running.discard(thread)
                # Only the root's own children report to the root; a
                # grandchild reports to the subagent that spawned it.
                if not root_open and parent_of.get(thread, thread_id) == thread_id:
                    owed.append(envelope(thread, children.get(thread, "A subagent"),
                                         said.get(thread)))
            continue
        # The root thread.
        if item.get("type") in ("mcpToolCall", "commandExecution") and item.get("id"):
            if method == "item/started":
                open_calls[item["id"]] = item
            elif method == "item/completed":
                open_calls.pop(item["id"], None)
                if item["id"] in late:
                    late.discard(item["id"])
                    content = codex.tool_result(item).get("content")
                    if not isinstance(content, str):
                        content = "\n".join(b.get("text") or "" for b in content or []
                                            if isinstance(b, dict))
                    owed.append(envelope(item["id"], codex.tool_use(item).get("name")
                                         or item["type"], content))
        if method == "item/completed" and item.get("type") == "agentMessage":
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                out.reply = text
        if method == "error" and not params.get("willRetry"):
            err = params.get("error") or {}
            out.error = (err.get("message") if isinstance(err, dict) else str(err)) or out.error
            info = err.get("codexErrorInfo") if isinstance(err, dict) else None
            if info:
                out.error_kind = info if isinstance(info, str) else json.dumps(info)
        if method == "turn/completed":
            t = params.get("turn") or {}
            root_open = False
            status = t.get("status")
            if status == "completed":
                late |= set(open_calls)
                out.ok = True
            else:
                err = t.get("error") if isinstance(t.get("error"), dict) else {}
                out.ok = False
                out.error = err.get("message") or out.error or f"turn {status}"
                info = err.get("codexErrorInfo")
                if info:
                    out.error_kind = info if isinstance(info, str) else json.dumps(info)
                # A failed root turn is the end: nothing is handed back to it.
                running.clear()
                late.clear()
                owed.clear()
    usage: dict = {}
    for total in totals.values():
        _usage_add(usage, total)
    out.usage = usage
    return out


def claude_usage(usage: dict) -> dict:
    """Codex's summed token counts under the wake log's names (Anthropic's,
    as lib/loam_usage.py keeps them), plus Codex's reasoning count. OpenAI's
    ``inputTokens`` includes the cached part."""
    total_in = usage.get("inputTokens") or 0
    cached = usage.get("cachedInputTokens") or 0
    out = {"input_tokens": max(total_in - cached, 0),
           "cache_read_input_tokens": cached,
           "output_tokens": usage.get("outputTokens") or 0}
    if usage.get("reasoningOutputTokens"):
        out["reasoning_output_tokens"] = usage["reasoningOutputTokens"]
    return {k: v for k, v in out.items() if v}


def rollout(thread_id: str | None) -> Path | None:
    if not thread_id:
        return None
    try:
        return next((codex.CODEX_HOME / "sessions").glob(
            f"*/*/*/rollout-*-{thread_id}.jsonl"), None)
    except OSError:
        return None


def ask(workspace: Path, instructions_text: str, model: str | None,
        prompt: str, env: dict | None = None) -> str:
    """One question to a fresh, ephemeral thread composed exactly as the
    wake's will be (the identity preflight's half on Codex). Answers the
    reply's text; an account refusal comes back as Codex's own sentence,
    which ``loam_identity.judge`` reads before the soul."""
    server = open_server(workspace, env)
    try:
        started = start_thread(server, workspace, instructions_text, model,
                               ephemeral=True)
        tid = (started.get("thread") or {}).get("id")
        out = drive(server, tid, prompt, model=model)
    except CodexWakeError as exc:
        return str(exc)
    finally:
        server.close()
    if not out.ok:
        return out.error or "the Codex turn failed with no message"
    return out.reply


def run(workspace: Path, instructions_text: str, prompt: str, *,
        model: str | None, effort: str | None, timeout: float | None = None,
        env: dict | None = None) -> Outcome:
    """The lane: a thread started for it, the prompt, and everything after."""
    deadline = time.monotonic() + timeout if timeout else None
    server = open_server(workspace, env)
    try:
        started = start_thread(server, workspace, instructions_text, model)
        thread = started.get("thread") or {}
        out = Outcome(thread_id=thread.get("id"),
                      model=started.get("model") or model)
        return drive(server, out.thread_id, prompt, model=model, effort=effort,
                     outcome=out, deadline=deadline)
    finally:
        server.close()


def account_refusal(out: Outcome) -> str | None:
    """The account's sentence when a run failed because of the account."""
    if out.ok:
        return None
    if out.error_kind and _account_words(out.error_kind):
        return f"{out.error_kind}: {out.error or ''}".strip()
    if _account_words(out.error):
        return out.error
    return None


def login_refusal() -> str | None:
    """Before anything is started: is Codex signed in at all?"""
    state = codex.login_state()
    if state.get("connected"):
        return None
    return f"Codex is not signed in: {state.get('reason') or 'no login'}"


def env_for_lane(base: dict | None = None) -> dict:
    """The app-server's environment for a lane: ``loam_codex.env`` (no API
    key, the broker's ``CODEX_HOME``) with no broker session in it, so
    nothing it starts mistakes the lane for a conversation."""
    out = codex.env(base)
    out.pop("LOAM_SESSION_ID", None)
    return out

