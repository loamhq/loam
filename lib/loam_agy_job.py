"""A project engineer's job on Antigravity (since 2026-11-15).

The owner, 2026-11-15 16:35 PT, through the lead persona's brief: the
engineers that default to Codex may move to Antigravity (his Google AI Ultra
sign-in), "but I first want to do a test". So this is the choice and nothing
else: ``"antigravity"`` is a third ``runtime`` (lib/loam_engineer.py), offered
by Settings > Agents > Engineers, chosen by nobody until the owner says so.

``bin/engineer-job`` keeps every guarantee it gives a Codex job, and this
module is the part of each that is Antigravity's shape. Everything here was
caused on ``agy`` 1.5.9 on 2026-11-15, in a scratch git repository, before it
was written down:

- **the process** is ``agy --print=<prompt> --output-format stream-json``,
  one per job, in the work directory. ``--dangerously-skip-permissions``
  (``permission_mode`` ``always-proceed`` in its ``init`` event) because a
  print run has no channel to ask anyone on, as Codex's ``approval_policy
  never``. Exit 0 on a finished turn, 1 on a refused launch (an unknown
  model, a model and ``--effort`` that disagree), with a ``result`` event
  whose ``status`` is ``ERROR`` and whose ``error`` says why.
- **the wall** is the launcher's mount namespace, as for every job. Antigravity
  has no write sandbox of its own to name the tree in (``--sandbox`` is
  "terminal restrictions", undocumented beyond that), so the harness files a
  Codex job holds read-only by its profile are held read-only here by the wall
  itself (``lib/loam_worktree.py``, ``walled(protect=...)``).
- **the instructions** are the Claude engineer's ``CLAUDE.md`` and its ``@``
  imports, read from the tree at start, as Codex is given them. Antigravity
  reads ``AGENTS.md`` (and ``GEMINI.md``, ``.agents/rules/``) from the
  workspace and not ``CLAUDE.md`` -- caused: a rule in AGENTS.md was obeyed,
  one in CLAUDE.md was not loaded -- and nothing is generated into a repo, so
  they open the prompt, where no file in the tree can drift from them. A
  project's own ``AGENTS.md``, if it has one, is loaded by the CLI as well.
- **the model** is the id ``agy models`` lists, its thinking level included
  (``gemini-4-hydrogen-high``); see ``loam_antigravity.LEVEL_RE`` for why
  that is the one mapping.
- **the transcript** the heartbeat watches is the event stream, written line
  by line into the job directory as it arrives, as Codex's is.
- **a continuation** is ``--conversation <id>``, the ``conversation_id`` the
  parent's first event named. Unlike ``codex exec fork`` it does not fork:
  the parent's conversation is appended to. The parent's own record (its
  stream in its job directory) is untouched, and a parent is never running
  when it is continued (the launcher refuses that), so nothing else is.
  An id Antigravity does not know is NOT refused by the CLI -- it warns on
  stderr ("conversation ... not found") and starts a new conversation -- so
  the launcher checks the conversation's file is there first, and the run
  says so when the conversation it got is not the one it asked for.
- **the cost** is tokens (a Google AI Ultra login is not billed per call);
  a turn states no quota; the broker reads it with ``/quota``
  (``loam_antigravity.read_quota``) and once more when a job ends.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
from pathlib import Path

import loam_antigravity as agy
import loam_codex_job as codex_job
import loam_engineer as eng
import loam_stuck

PROVIDER_LINE = (
    "You are a project engineer running on Antigravity (`agy --print`), "
    "headless: Antigravity's own tools (run_command, view_file, "
    "write_to_file, replace_file_content, grep_search, ...). Claude Code's "
    "Read, Edit, Grep, Agent, ToolSearch and Skill tools are not here; the "
    "files below are the instructions the Claude Code engineer reads, "
    "unchanged, and they bind you the same way. Nobody can answer an "
    "approval or a question: decide, and say so in the receipt. Do not use "
    "the browser, image generation or scheduling tools; a job is the shell "
    "and the files.")

# The platform engineer's line (since 2026-11-19), as Codex's
# PLATFORM_LINE: the wall holds what its Claude settings would have denied.
PLATFORM_LINE = (
    "You are the platform engineer running on Antigravity (`agy --print`), "
    "headless: Antigravity's own tools (run_command, view_file, "
    "write_to_file, replace_file_content, grep_search, ...), with this box's "
    "sudo, behind the job's wall. Claude Code's Read, Edit, Grep, Agent, "
    "ToolSearch and Skill tools are not here; the files below are the "
    "instructions the Claude Code engineer reads, unchanged, and they bind "
    "you the same way, including every line the Claude engineer's settings "
    "would have enforced for it. Nobody can answer an approval or a "
    "question: decide, and say so in the receipt. Do not use the browser, "
    "image generation or scheduling tools; a job is the shell and the files.")

STREAM = "agy.jsonl"
INSTRUCTIONS = "instructions.md"
PROMPT_FILE = "prompt.md"

# Where a conversation lives once Antigravity has made it (1.5.9): one
# SQLite file per id. Read only to tell whether a continuation can resume.
CONVERSATIONS = agy.HOME / "antigravity-cli" / "conversations"

# The harness a Claude engineer's settings deny it to Edit, as Codex's.
HARNESS = codex_job.HARNESS

# How a quota failure reads: the CLI's own matcher, as its 1.5.9 binary
# carries it (`const QUOTA_RE = ...`, read 2026-11-15), and its "Out of
# credits". Not caused on this login yet; the outcome names it when an
# error matches, so the receipt says "out of quota" rather than an exit code.
QUOTA_RE = re.compile(r"RESOURCE_EXHAUSTED|quota|\b429\b|exhausted your capacity|"
                      r"rate.?limit|out of credits", re.I)

# A side call's failure (caused 2026-11-19, job 20261118-203030-08dd33be).
# Beside the job's own conversation Antigravity makes "implicit" calls of
# its own (the `.pb` files the files record names), on its default model,
# whatever `--model` says. One of them answered 503 ("No capacity available
# for model gemini-4-hydrogen-high") after a run on a Claude model had done
# its work, and the CLI reported the whole run as ERROR (exit 0). An error
# that is transient (503 / UNAVAILABLE / no capacity) and names a model that
# is not the job's is that side call's, not the job's.
TRANSIENT_RE = re.compile(r"UNAVAILABLE|\b503\b|No capacity available", re.I)
ERROR_MODEL_RE = re.compile(r"\bfor model ([A-Za-z0-9][A-Za-z0-9._-]*)")


def side_call_failure(failed: str | None, job_model: str | None) -> str | None:
    """The other model's name when ``failed`` is a transient error of a
    model other than ``job_model`` (a side call's); else None, and the run's
    error stands as the job's."""
    if not failed or not job_model or not TRANSIENT_RE.search(failed):
        return None
    named = ERROR_MODEL_RE.search(failed)
    if not named:
        return None
    other = named.group(1)
    return None if other == job_model or job_model.startswith(other) \
        or other.startswith(job_model) else other


STEP_SILENT = ("user_input", "checkpoint")
STEP_TEXT = ("agent_response",)


def instructions(workdir: Path, platform: bool = False) -> str:
    """The Claude engineer's memory files, as Codex is given them, under
    this provider's line (the platform engineer's when ``platform``)."""
    if platform:
        text = codex_job.instructions(workdir, platform=True)
        return text.replace(codex_job.PLATFORM_LINE, PLATFORM_LINE, 1)
    text = codex_job.instructions(workdir)
    return text.replace(codex_job.PROVIDER_LINE, PROVIDER_LINE, 1)


def compose(instructions_text: str, prompt: str) -> str:
    """The one message a job is: its standing instructions, then its prompt."""
    return (f"<instructions>\n{instructions_text.strip()}\n</instructions>\n\n"
            f"{prompt.strip()}\n")


def prompt_arg(job_dir: Path, text: str) -> str:
    """``text``, or a pointer to the job directory's copy of it when it is
    too long for one argv string (MAX_ARG_STRLEN; loam_antigravity's
    ARG_MAX_CHARS). Written either way, so the job directory holds what the
    session was told."""
    path = job_dir / PROMPT_FILE
    path.write_text(text, encoding="utf-8")
    if len(text) <= agy.ARG_MAX_CHARS:
        return text
    return (f"Your instructions and this job's prompt are too long to pass "
            f"inline. Read {path} in full before anything else; it is both, "
            f"and it binds you.")


def argv(*, prompt: str, job_dir: Path, conversation: str | None,
         model: str | None = eng.AGY_ENGINEER_MODEL, effort: str | None = None,
         extra_dirs: list[Path] = ()) -> list[str]:
    """The ``agy`` command line, started in the work directory."""
    out = [agy.AGY, f"--print={prompt}", "--output-format", "stream-json",
           "--dangerously-skip-permissions", "--disable-slash-commands"]
    if model:
        out += ["--model", model]
    if effort and not agy.carries_level(model):
        out += ["--effort", effort]
    if conversation:
        out += ["--conversation", conversation]
    for d in (job_dir, *extra_dirs):
        out += ["--add-dir", str(d)]
    return out


def env(base: dict | None = None) -> dict:
    """``loam_antigravity.env`` (API keys out, so the owner's login bills),
    less the Claude login wake.env mounts."""
    out = agy.env(base)
    out.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    return out


def conversation_file(conversation: str | None) -> Path | None:
    if not conversation:
        return None
    path = CONVERSATIONS / f"{conversation}.db"
    return path if path.is_file() else None


class Run:
    """What the event stream said: the conversation, the steps, the usage."""

    def __init__(self, resumed: str | None = None):
        self.asked = resumed
        self.conversation_id: str | None = None
        self.model: str | None = None
        self.tool_calls = 0
        self.responses = 0
        self.num_turns: int | None = None
        self.status: str | None = None
        self.failed: str | None = None
        # This run's tokens: the sum of its steps' usage. The result's usage
        # is the whole conversation's (caused 2026-11-15: a continuation's
        # result carried its parent's tokens and its own), kept beside it.
        self.usage = {"input_tokens": 0, "cache_read_tokens": 0,
                      "output_tokens": 0, "thinking_tokens": 0}
        self.conversation_usage: dict | None = None
        self._stepped = False
        self.capped = False
        self.seen_result = False

    def read(self, line: str) -> None:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(event, dict):
            return
        kind = event.get("event")
        if kind == "init":
            self.conversation_id = event.get("conversation_id") or self.conversation_id
            init = event.get("init") if isinstance(event.get("init"), dict) else {}
            self.model = init.get("model") or self.model
        elif kind == "step_update":
            step = event.get("step_update")
            step = step if isinstance(step, dict) else event
            self.conversation_id = self.conversation_id or step.get("conversation_id")
            if step.get("state") != "DONE":
                return
            stype = step.get("step_type")
            if isinstance(step.get("usage"), dict):
                self._stepped = True
                for key, value in step["usage"].items():
                    if isinstance(value, int) and key in self.usage:
                        self.usage[key] += value
            if stype in STEP_SILENT:
                return
            if stype in STEP_TEXT:
                self.responses += 1
            else:
                self.tool_calls += 1
        elif kind == "result":
            self.seen_result = True
            res = event.get("result") if isinstance(event.get("result"), dict) else {}
            self.conversation_id = res.get("conversation_id") or self.conversation_id
            self.status = res.get("status")
            self.num_turns = res.get("num_turns")
            whole = {k: v for k, v in (res.get("usage") or {}).items()
                     if isinstance(v, int) and k in self.usage}
            self.conversation_usage = whole or None
            if whole and not self._stepped and not self.asked:
                self.usage.update(whole)   # a fresh run's whole is its own
            if self.status != "SUCCESS":
                self.failed = res.get("error") or self.status or "error"

    @property
    def round_trips(self) -> int:
        """Model responses: one per tool call and one per reply."""
        return self.tool_calls + self.responses

    @property
    def quota(self) -> bool:
        return bool(self.failed and QUOTA_RE.search(self.failed))

    @property
    def resumed_other(self) -> bool:
        """A continuation that came back on a conversation it did not ask
        for: the CLI started a new one in place of an id it did not know."""
        return bool(self.asked and self.conversation_id
                    and self.conversation_id != self.asked)


def run(command: list[str], cwd: str, timeout: int | None, job_dir: Path,
        stderr: Path, on_thread=None, on_stop=None, resumed: str | None = None
        ) -> tuple[int, Run, bool]:
    """Run ``agy``: (returncode, Run, timed_out). The stream goes to
    ``agy.jsonl`` line by line, which is what the heartbeat reads; the
    process is ended by group, as Codex's is (``agy`` starts a language
    server and tool children of its own)."""
    stream = job_dir / STREAM
    state = Run(resumed)
    with open(stderr, "w", encoding="utf-8") as err, \
            open(stream, "a", encoding="utf-8") as out:
        proc = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=err, text=True,
                                env=env(), start_new_session=True)
        timed_out = threading.Event()

        def clock():
            if codex_job._wait(proc, timeout) is None:  # noqa: SLF001
                timed_out.set()
                codex_job.end(proc)

        watcher = threading.Thread(target=clock, daemon=True)
        watcher.start()
        # A command that never returns is stopped, not the job
        # (lib/loam_stuck.py, since 2026-11-17).
        stuck = loam_stuck.Watch(proc.pid, loam_stuck.agy_busy, job_dir,
                                 on_stop=on_stop).start()
        assert proc.stdout is not None
        for line in proc.stdout:
            out.write(line)
            out.flush()
            stuck.line(line)
            had = state.conversation_id
            state.read(line)
            if state.conversation_id and not had and on_thread:
                on_thread(state.conversation_id)
            if (eng.MAX_TURNS and state.round_trips >= eng.MAX_TURNS
                    and not state.capped):
                state.capped = True
                codex_job.end(proc)
        returncode = proc.wait()
        stuck.stop()
    if returncode == 0 and not state.seen_result and not state.failed:
        state.failed = "no result event (the stream ended without one)"
    if state.resumed_other and not state.failed:
        state.failed = (f"asked to resume conversation {state.asked} and ran "
                        f"on {state.conversation_id}, a new one")
    return returncode, state, timed_out.is_set()


def usage_text(state: Run) -> str:
    """The receipt's cost cell for an Antigravity job: this run's tokens,
    and a continuation's conversation total beside them."""
    u = state.usage
    text = (f"{u['input_tokens']:,} input tokens ({u['cache_read_tokens']:,} "
            f"cached), {u['output_tokens']:,} output "
            f"({u['thinking_tokens']:,} of it thinking)")
    whole = state.conversation_usage
    if state.asked and whole:
        text += (f"; the conversation so far, the parent's with it: "
                 f"{whole.get('input_tokens', 0):,} input, "
                 f"{whole.get('output_tokens', 0):,} output")
    return text + ("; no dollar cost and no quota reading (a Google login; "
                   "headless Antigravity states neither)")
