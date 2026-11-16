"""A project engineer's job on Codex (since 2026-10-07).

The owner, 2026-10-07 in chat: "make sure all of engineering agents for the
project, not the Loam main agent, are with Codex instead of Claude Code.
Their default model should be latest GPT with Sol on high effort." So a
project's registry record carries ``"runtime": "codex"`` (or ``"claude"``),
read by ``Project.runtime``; the platform engineer is always Claude Code.

``bin/engineer-job`` keeps every guarantee it gave a Claude job, and this
module is the part of each that is Codex's shape rather than the CLI's:

- **the wall** is the launcher's mount namespace, unchanged. Inside it Codex's
  own sandbox (``workspace-write``, bwrap, nested -- caused 2026-10-07 inside
  the wall's two user namespaces) holds writes to the tree, its ``.git`` and
  the job directory, as ``hooks/boundary_guard.py`` holds a Claude session's.
  Codex marks a workspace's ``.git`` read-only unless it is named as a
  writable root, so it is named. Network stays on: a test command fetches.
- **no cap** since 2026-11-10. ``codex exec`` has no turn limit, and the
  one this module counted (300, ``MAX_TURNS``) went with the Claude job's;
  it is still counted here, and ends the job only when a probe sets
  ``LOAM_ENGINEER_MAX_TURNS``. One round trip is one model response.
- **the transcript** the heartbeat watches is the event stream, written line
  by line into the job directory as it arrives.
- **the instructions** are the same files the Claude engineer loads --
  ``CLAUDE.md`` and its ``@`` imports, read from the job's tree at start --
  passed as ``developer_instructions``. Nothing is generated into the repo,
  so there is no second copy to drift.
- **the cost** is tokens, not dollars (a ChatGPT login is not billed per
  call), and the login's five-hour and weekly meters as the job left them,
  read from the rollout Codex writes.
- **a continuation** is ``codex exec fork``, which leaves the parent's
  thread as it was, as ``--fork-session`` does.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import loam_codex
import loam_engineer as eng
import loam_provider_memory as pm

# The prompt's one addition, at the top of the instructions: what is not
# here that the role file may name.
PROVIDER_LINE = (
    "You are a project engineer running on Codex (`codex exec`), headless: a "
    "sandboxed shell and apply_patch. Claude Code's Read, Edit, Grep, Agent, "
    "ToolSearch and Skill tools are not here; the files below are the "
    "instructions the Claude Code engineer reads, unchanged, and they bind "
    "you the same way. Nobody can answer an approval: a command the sandbox "
    "refuses is refused.")

# Event items that are a tool call: each one ends a model response.
TOOL_ITEMS = ("command_execution", "file_change", "mcp_tool_call",
              "web_search", "collab_tool_call")

# Codex features on by default for this login (`codex features list`,
# 0.159.3, 2026-10-07) that are tools a Claude engineer never had: a
# browser, the desktop, image generation, plugins (which bring MCP servers),
# goals and tool suggestions. `apps`, the login's connectors (an MCP server
# of 236 tools, engineer/memory 2026-10-01), is turned off with them.
#
# Subagents stay, as Claude's `Task` did: `spawn_agent` and `wait_agent`
# (multi-agent v2, which `--disable multi_agent` does not remove -- caused
# 2026-10-07, the tools were still in the rollout's instructions). A subagent
# the engineer does not wait for is lost when `codex exec` ends with the
# parent's turn (engineer/memory, 2026-10-02).
DISABLED_FEATURES = ("apps", "browser_use", "browser_use_external",
                     "computer_use", "image_generation", "plugins",
                     "remote_plugin", "goals", "tool_suggest")

STREAM = "codex.jsonl"
INSTRUCTIONS = "instructions.md"
LAST_MESSAGE = "last-message.md"


def instructions(workdir: Path) -> str:
    """The Claude engineer's memory files, in Claude Code's order and
    layout, under the provider line."""
    parts = [PROVIDER_LINE]
    for source in loam_codex._imports(workdir / "CLAUDE.md", 0, set()):  # noqa: SLF001
        parts.append(f"Contents of {source}:\n\n"
                     f"{source.read_text(encoding='utf-8').strip()}")
    return "\n\n".join(parts) + "\n"


def toml_string(text: str) -> str:
    """A TOML basic string. JSON's escapes are a subset of TOML's, and a
    value that is not valid TOML would be taken raw by ``-c``."""
    return json.dumps(text, ensure_ascii=False)


# The harness a Claude engineer's settings deny it to Edit
# (`Edit(/CLAUDE.md)`, `/role.md`, `/project.md`, `/.claude/**`).
HARNESS = ("CLAUDE.md", "role.md", "project.md", ".claude")


def profile(tree: Path, job_dir: Path, protected: list[Path]) -> str:
    """The job's permission profile, as one TOML inline table: everything
    readable, as it is to a Claude engineer; the tree, its ``.git`` (which
    ``workspace-write`` would otherwise hold read-only), the job directory
    and /tmp writable; ``protected`` read-only inside the tree. Narrowest
    path wins (engineer/memory, 2026-10-01)."""
    paths = {"/": "read", str(tree): "write", str(tree / ".git"): "write",
             str(job_dir): "write", "/tmp": "write"}
    paths.update({str(p): "read" for p in protected})
    return "{" + ",".join(f"{toml_string(k)}={toml_string(v)}"
                          for k, v in paths.items()) + "}"


def argv(*, workdir: Path, tree: Path, job_dir: Path, prompt: str,
         extra_dirs: list[Path], fork_from: str | None,
         instructions_text: str, protected: list[Path] = (),
         model: str | None = None, effort: str | None = None,
         mcp_servers: dict | None = None) -> list[str]:
    """The ``codex exec`` command line. ``--ignore-user-config`` because
    ``CODEX_HOME/config.toml`` is the broker's, rewritten at every persona
    thread start; a job's whole configuration is on this line."""
    writable = [tree / ".git", job_dir, *extra_dirs]
    flags = [
        "--json", "--strict-config", "--ignore-user-config",
        "--skip-git-repo-check",
        # The engineer's setting (2026-11-13, eng.choice_for), else built-in.
        "-m", model or eng.CODEX_ENGINEER_MODEL,
        "-c", f"model_reasoning_effort={toml_string(effort or eng.CODEX_ENGINEER_EFFORT)}",
        "-c", 'approval_policy="never"',
        "-c", 'sandbox_mode="workspace-write"',
        "-c", "sandbox_workspace_write.network_access=true",
        "-c", "sandbox_workspace_write.writable_roots="
              + json.dumps([str(p) for p in writable]),
        "-c", 'default_permissions="engineer"',
        "-c", f"permissions.engineer.filesystem={profile(tree, job_dir, list(protected))}",
        "-c", "permissions.engineer.network.enabled=true",
        *[f for name in DISABLED_FEATURES for f in ("--disable", name)],
        # Codex's own memory, off (first release L8): a job ignores the home
        # config the broker pins it in, so the pins ride this line. Each key
        # was checked under --strict-config on 0.169.0 (2026-11-14).
        *[f for kv in pm.CODEX_OVERRIDES for f in ("-c", kv)],
        # What plugins that are on give an engineer (first release L5, the
        # gap L4 named): their MCP servers, approved whole.
        *[f for name, spec in sorted((mcp_servers or {}).items())
          for f in mcp_flags(name, spec)],
        "-c", f"developer_instructions={toml_string(instructions_text)}",
        # No `-C`: `exec fork` does not take it (0.159.3), and the launcher
        # starts the process in the work directory either way.
        "-o", str(job_dir / LAST_MESSAGE),
    ]
    head = [loam_codex.CODEX, "exec"]
    if fork_from:
        return head + ["fork", *flags, fork_from, prompt]
    return head + flags + [prompt]


def mcp_flags(name: str, spec: dict) -> list[str]:
    """One stdio MCP server as ``-c mcp_servers.<name>.…`` overrides."""
    out = ["-c", f"mcp_servers.{name}.command={toml_string(spec['command'])}",
           "-c", f"mcp_servers.{name}.args={json.dumps(spec.get('args') or [])}",
           "-c", f'mcp_servers.{name}.default_tools_approval_mode="approve"']
    if spec.get("env"):
        inline = ", ".join(f"{k} = {toml_string(v)}" for k, v in sorted(spec["env"].items()))
        out += ["-c", f"mcp_servers.{name}.env={{{inline}}}"]
    return out


def env(base: dict | None = None) -> dict:
    """``loam_codex.env`` (API keys out, ``CODEX_HOME`` in), less the Claude
    login wake.env mounts, which a Codex job has no use for."""
    out = loam_codex.env(base)
    out.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    return out


class Run:
    """What the event stream said: the thread, the tool calls, the usage."""

    def __init__(self):
        self.thread_id: str | None = None
        self.tool_calls = 0
        self.turns = 0
        self.failed: str | None = None
        self.usage = {"input_tokens": 0, "cached_input_tokens": 0,
                      "output_tokens": 0, "reasoning_output_tokens": 0}
        self.capped = False

    def read(self, line: str) -> None:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        kind = event.get("type")
        if kind == "thread.started":
            self.thread_id = event.get("thread_id")
        elif kind == "item.completed":
            if (event.get("item") or {}).get("type") in TOOL_ITEMS:
                self.tool_calls += 1
        elif kind == "turn.completed":
            self.turns += 1
            for key, value in (event.get("usage") or {}).items():
                if isinstance(value, int) and key in self.usage:
                    self.usage[key] += value
        elif kind in ("turn.failed", "error"):
            err = event.get("error") or {}
            self.failed = (err.get("message") if isinstance(err, dict)
                           else None) or event.get("message") or kind

    @property
    def round_trips(self) -> int:
        """Model responses: one per tool call, plus each turn's last."""
        return self.tool_calls + self.turns


def run(command: list[str], cwd: str, timeout: int | None, job_dir: Path,
        stderr: Path, on_thread=None) -> tuple[int, Run, bool]:
    """Run ``codex exec``: (returncode, Run, timed_out). The stream goes to
    ``codex.jsonl`` line by line, which is what the heartbeat reads; the
    process is ended by group, because ``codex`` is a node wrapper whose
    native child SIGKILL cannot reach (engineer/memory, 2026-10-01)."""
    stream = job_dir / STREAM
    state = Run()
    with open(stderr, "w", encoding="utf-8") as err, \
            open(stream, "a", encoding="utf-8") as out:
        proc = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=err, text=True,
                                env=env(), start_new_session=True)
        timed_out = threading.Event()

        def clock():
            # No ceiling (None, every job since 2026-11-10 unless its brief
            # set one): wait for the process and never end it.
            if _wait(proc, timeout) is None:
                timed_out.set()
                end(proc)

        watcher = threading.Thread(target=clock, daemon=True)
        watcher.start()
        assert proc.stdout is not None
        for line in proc.stdout:
            out.write(line)
            out.flush()
            had = state.thread_id
            state.read(line)
            if state.thread_id and not had and on_thread:
                on_thread(state.thread_id)
            if (eng.MAX_TURNS and state.round_trips >= eng.MAX_TURNS
                    and not state.capped):
                state.capped = True
                end(proc)
        returncode = proc.wait()
    return returncode, state, timed_out.is_set()


def _wait(proc: subprocess.Popen, timeout: float):
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


def end(proc: subprocess.Popen) -> None:
    loam_codex.kill_group(proc.pid, 15)
    deadline = time.monotonic() + 10
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.2)
    loam_codex.kill_group(proc.pid)


def rollout(thread_id: str | None) -> Path | None:
    if not thread_id:
        return None
    try:
        return next((loam_codex.CODEX_HOME / "sessions").glob(
            f"*/*/*/rollout-*-{thread_id}.jsonl"), None)
    except OSError:
        return None


def meters(thread_id: str | None) -> dict | None:
    """The login's rate-limit windows as this thread last saw them:
    ``{"five_hour": %, "seven_day": %, "resets": {...}}``, or None."""
    path = rollout(thread_id)
    if path is None:
        return None
    last = None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if '"rate_limits"' in line:
                last = line
    except OSError:
        return None
    if last is None:
        return None
    try:
        limits = json.loads(last).get("payload", {}).get("rate_limits") or {}
    except json.JSONDecodeError:
        return None
    out = {}
    for slot, name in (("primary", "five_hour"), ("secondary", "seven_day")):
        window = limits.get(slot) or {}
        if isinstance(window.get("used_percent"), (int, float)):
            out[name] = window["used_percent"]
            out[f"{name}_resets_at"] = window.get("resets_at")
    return out or None


def usage_text(state: Run, thread_id: str | None) -> str:
    """The receipt's cost cell for a Codex job."""
    u = state.usage
    text = (f"{u['input_tokens']:,} input tokens ({u['cached_input_tokens']:,} "
            f"cached), {u['output_tokens']:,} output "
            f"({u['reasoning_output_tokens']:,} reasoning); no dollar cost "
            f"(ChatGPT login)")
    m = meters(thread_id)
    if m:
        text += (f"; the login's meters after: five-hour "
                 f"{m.get('five_hour', '—')}%, weekly {m.get('seven_day', '—')}%")
    return text
