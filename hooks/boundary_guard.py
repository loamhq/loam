#!/usr/bin/env python3
"""PreToolUse hook: the write boundary that path rules cannot express.

Most of this agent's boundary IS expressible natively and is expressed
there -- ``permissions.additionalDirectories`` grants the vault and this
agent's own workspace, and a deny rule fences ``15_CONVERSATIONS``. This
hook exists for the one rule that has no declarative form:

    writable: ai-labs/<this agent>/
    refused:  ai-labs/<any other agent>/

Deny rules beat allow rules in Claude Code, so a deny on ``ai-labs/**``
would also fence the agent out of its own workspace, and there is no
negation to carve it back out. Enumerating today's siblings would work and
is exactly what must not be written down -- rosters drift, patterns don't.
So the rule is computed: anything under ai-labs that is not under my own
workspace is refused, whoever else turns out to live there.

Leaving it to grant-absence is not enough. Measured 2026-08-25: an
unreached path fails closed in a non-interactive run, but surfaces as an
ordinary permission request in an interactive one -- which makes the
sibling boundary something a tired operator could approve away at 11pm.
A deny is not promptable.

Refusal text mirrors chassis/permissions.py so the agents read the same
sentences on both runtimes.

Since 2026-09-04 it also carries the size limit on the auto-loaded files.
That check is here rather than anywhere else for the reason the 08-30 hook
defect taught: **enforcement belongs at the layer that acts.** A wake
preflight and the nightly lane check both look at a file after it is written,
by which time the agent that wrote it has finished its turn and cannot be
told. Refusing the Edit puts the number in front of the only party who can
act on it, in the turn where acting is one more edit away.

The rule is asymmetric on purpose: over the limit is refused, but an edit
that leaves the file SMALLER than it already is always passes. A flat
refusal on an over-limit file would lock an agent out of the only file it
could shrink -- the same trap in miniature that a day-one wake refusal would
have been.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# This file's own tree first (2026-11-09): run from a job's worktree, as
# its tests do, it must use the branch's lib, not main's. Live, the two
# are one path; the canonical line behind it serves a copy elsewhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_config as lc  # noqa: E402

from loam_memory import (  # noqa: E402
    LIMIT_CHARS,
    LayerError,
    is_auto_loaded,
    load_config,
    project_dir_from,
    style_frontmatter_intact,
)

# Every tool that creates or mutates a file: the verb the agent reads back
# in a refusal, and every input field naming a path that has to be inside
# the boundary. The composition tools are here because they write files
# too, and the native permission layer does not inspect MCP tool arguments
# -- so ungating them for every agent would otherwise be a hole straight
# through the boundary. Deletion is a mutation and gets the same check. A
# move is checked at BOTH ends, because it is a delete at one and a write
# at the other, and a boundary applied to only one end is not a boundary.
WRITE_TOOLS = {
    "Write": ("Write", ("file_path",)),
    "Edit": ("Write", ("file_path",)),
    "MultiEdit": ("Write", ("file_path",)),
    "NotebookEdit": ("Write", ("notebook_path",)),
    # The Images plugin's tool (2026-11-18; it left the composition server).
    # The old name stays while a session started before then still has it.
    "mcp__images__generate_image": ("Write", ("target_path",)),
    "mcp__composition__generate_image": ("Write", ("target_path",)),
    "mcp__composition__generate_pdf": ("Write", ("target_path",)),
    "mcp__composition__render_typst_pages": ("Write", ("output_dir",)),
    # The servers check their own targets too; neither layer is trusted alone.
    "mcp__loam__delete_file": ("Delete", ("path",)),
    "mcp__loam__move_file": ("Move", ("source", "destination")),
}

# The subset of those fields that introduce a NEW name on disk, and so the
# only ones worth holding to the Android character rule below. A name being
# removed, or moved *away from*, is not a name this system is creating --
# and checking those was itself a bug: it refused deleting a file already
# stranded off the phone, and would refuse renaming one back into reach,
# which is the most useful thing move_file does.
FILENAME_FIELDS = {
    "Write": ("file_path",),
    "mcp__images__generate_image": ("target_path",),
    "mcp__composition__generate_image": ("target_path",),
    "mcp__composition__generate_pdf": ("target_path",),
    "mcp__composition__render_typst_pages": ("output_dir",),
    "mcp__loam__move_file": ("destination",),
}

# Illegal on Android at the filesystem layer. A vault file carrying one of
# these never syncs to the phone -- it is not a broken filename, it is a
# permanently invisible one. Cheaper to refuse the write than to find the
# gap later.
ANDROID_ILLEGAL = set('?:*"<>|')

# The one field per tool that names the file whose CONTENT this call
# produces -- the only field the size limit can be reasoned about at.
#
# move_file's source is deliberately absent, and that absence is the point:
# moving an over-limit file AWAY is the repair. It is what the agents
# themselves invented on 2026-08-31 when they split each loading file into an
# index and a body, `move_file`d byte-identically so nothing was retyped. A
# limit that refused the move would refuse the fix.
# What a persona's workspace holds that is the runtime's, and so read-only
# to it: bin/provision's RUNTIME_FILES, the same five names.
RUNTIME_FILES = frozenset({"CLAUDE.md", "agent.json", ".mcp.json", ".claude", ".codex"})

RESULT_FIELD = {
    "Write": "file_path",
    "Edit": "file_path",
    "MultiEdit": "file_path",
    "mcp__loam__move_file": "destination",
}


# Codex's file tool (2026-10-01). One call carries a whole patch -- adds,
# updates, deletes and moves across any number of files -- in
# ``tool_input.command``, with paths relative to the session's cwd. It is
# unpacked into the calls this file already knows, so one rule has two
# callers: an add is a Write with its content, an update is a Write of the
# file as the patch would leave it (or, when that cannot be modelled, an Edit
# the size limit leaves alone), a delete is a Delete, and a move is a Delete
# at one end and a Write at the other.
PATCH_HEADER = re.compile(r"^\*\*\* (Add File|Update File|Delete File|Move to): (.+)$")


def _apply_hunks(current: str, lines: list[str]) -> str | None:
    """An update's hunks applied to the file's text, or None if a hunk's
    context is not found -- in which case Codex would refuse it too."""
    hunks: list[list[str]] = []
    for line in lines:
        if line.startswith("@@") or not hunks:
            hunks.append([])
            if line.startswith("@@"):
                continue
        if line == "*** End of File":
            continue
        hunks[-1].append(line)
    text = current
    for hunk in hunks:
        old = [l[1:] for l in hunk if l[:1] in (" ", "-") or l == ""]
        new = [l[1:] for l in hunk if l[:1] in (" ", "+") or l == ""]
        if not old and not new:
            continue
        old_s, new_s = "\n".join(old), "\n".join(new)
        if old_s not in text:
            return None
        text = text.replace(old_s, new_s, 1)
    return text


def patch_calls(patch: str, cwd: str) -> list[tuple[str, dict]]:
    """An apply_patch body as the (tool, input) calls it amounts to."""
    files: list[list] = []          # [kind, path, body lines, move-to]
    for line in patch.splitlines():
        match = PATCH_HEADER.match(line)
        if match and match.group(1) == "Move to" and files:
            files[-1][3] = match.group(2).strip()
        elif match:
            files.append([match.group(1), match.group(2).strip(), [], None])
        elif files and not line.startswith("*** "):
            files[-1][2].append(line)
    calls: list[tuple[str, dict]] = []
    for kind, raw, body, move in files:
        path = os.path.join(cwd, raw)
        if kind == "Add File":
            calls.append(("Write", {"file_path": path, "content": "\n".join(
                l[1:] for l in body if l.startswith("+")) + "\n"}))
        elif kind == "Delete File":
            calls.append(("mcp__loam__delete_file", {"path": path}))
        else:
            current = read_text(resolve(path))
            result = _apply_hunks(current, body) if current is not None else None
            dest = os.path.join(cwd, move) if move else path
            if move:
                calls.append(("mcp__loam__delete_file", {"path": path}))
            calls.append(("Write", {"file_path": dest, "content": result})
                         if result is not None else ("Edit", {"file_path": dest}))
    return calls


def refuse(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(2)


def resolve(raw: str) -> Path:
    """Absolute, symlink-resolved, without requiring existence.

    The target of a Write usually does not exist yet, and symlink games are
    the reason this is not a string comparison.
    """
    target = Path(os.path.abspath(str(raw)))
    try:
        return target.resolve()
    except OSError:
        return target


def literal(raw: str) -> Path:
    """Absolute, symlinks NOT followed.

    The pair matters. ``resolve()`` is what makes the workspace boundary
    proof against symlink games; it is also what would hide a write aimed at
    ``.claude/output-styles/conversation.md``, since that link resolves back
    out to ``_active.md`` in the workspace root. So the ``.claude`` rule below
    is applied to both readings and refuses if either lands there.
    """
    return Path(os.path.abspath(str(raw)))


def through_skills_link(raw: str, target: Path, workspace: Path) -> bool:
    """A path addressed through the workspace's `.claude/skills` link that
    lands inside its own `skills/`.

    Since 2026-10-07 `.claude/skills` is a symlink to `../skills`, and the CLI
    presents every skill at that path, so a persona editing its own skill
    reaches for `.claude/skills/<name>/SKILL.md` first. Skills are its own;
    the link is the runtime's. Only the LITERAL reading is excused, and only
    when the link really is the one provision makes and the resolved path is
    strictly inside `skills/` -- so a link inside `skills/` that points out of
    it, or back into `.claude/`, gets no way through: its resolved reading is
    checked by every rule as before.
    """
    link = workspace / ".claude" / "skills"
    skills = workspace / "skills"
    lit = literal(raw)
    return (
        lit.is_relative_to(link) and lit != link
        and link.is_symlink() and resolve(str(link)) == skills
        and target.is_relative_to(skills) and target != skills
    )


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None
    except UnicodeDecodeError:
        return None


def resulting_text(tool_name: str, tool_input: dict, target: Path) -> str | None:
    """What the file will hold if this call goes through, or None if unknown.

    Unknown means no check: a call this cannot model is left to the rest of
    the guard rather than refused on a guess. Every tool that can grow an
    auto-loaded file today is modelled here, and a new one showing up
    unmodelled is a gap worth noticing rather than a silent pass -- which is
    what tests/test_boundary_guard.py's over-limit cases are for.
    """
    if tool_name == "Write":
        content = tool_input.get("content")
        return content if isinstance(content, str) else None

    if tool_name == "mcp__loam__move_file":
        # The destination ends up holding whatever the source holds.
        source = tool_input.get("source")
        return read_text(resolve(source)) if source else None

    if tool_name in ("Edit", "MultiEdit"):
        current = read_text(target)
        if current is None:
            return None
        edits = tool_input.get("edits")
        if not isinstance(edits, list):
            edits = [tool_input]
        for edit in edits:
            if not isinstance(edit, dict):
                return None
            old = edit.get("old_string")
            new = edit.get("new_string")
            if not isinstance(old, str) or not isinstance(new, str):
                return None
            count = -1 if edit.get("replace_all") else 1
            current = current.replace(old, new, count)
        return current

    return None


def check_auto_loaded(
    tool_name: str, tool_input: dict, verb: str, raw: str,
    target: Path, workspace: Path,
) -> None:
    """The size limit, and the style frontmatter `_active.md` cannot lose."""
    if not is_auto_loaded(target, workspace):
        return

    result = resulting_text(tool_name, tool_input, target)
    if result is None:
        return

    current = read_text(target)
    current_len = len(current) if current is not None else 0

    # Over the limit is refused -- unless the call makes the file smaller
    # than it already is. An agent handed an over-limit file must always be
    # able to trim it, and trimming is an edit that still leaves it over.
    if len(result) > LIMIT_CHARS and len(result) >= current_len:
        refuse(
            f"{verb} of {str(raw)!r} would leave it at {len(result):,} "
            f"characters, over the {LIMIT_CHARS:,}-character limit on files "
            f"that load automatically (currently {current_len:,}). This file "
            f"is in every run's context, so its size is paid on every run. "
            f"Move the bulk to a file you read on demand and leave an index "
            f"line pointing at it, or cut. An edit that makes this file "
            f"smaller than it is now is always allowed, even over the limit."
        )

    # The conversation lane reaches a phone-attached session by being the
    # target of .claude/output-styles/conversation.md, and the loader selects
    # by name. Measured 2026-09-04: a style whose `name:` disagrees with the
    # selected style loads NOTHING, silently -- so losing these keys does not
    # break loudly, it removes the whole conversation layer with no sign.
    if target.name == "_active.md" and current is not None:
        if style_frontmatter_intact(current) and not style_frontmatter_intact(result):
            refuse(
                f"{verb} of {str(raw)!r} removes or changes the frontmatter "
                f"block at the top of the file. That block is not decoration: "
                f"it is what makes this file load into a conversation at all, "
                f"and the loader fails silently without it -- you would lose "
                f"the whole layer with nothing to see. Keep the `---` block "
                f"(name, description, keep-coding-instructions) exactly as it "
                f"is and edit below it."
            )


# ---------------------------------------------------------------------------
# The engineer's rule. Same hook, different party.
#
# A persona's boundary is "your own workspace, the commons and the vault". The
# engineer's is wider on one axis and narrower on another, and neither half is
# expressible in path rules:
#
#   wider    it owns the runtime, so all of `agents/` is writable -- the
#            generators, the units, the hooks, this file.
#   narrower under `ai-labs/` it may provision and it may seed by verbatim
#            copy, and it may never COMPOSE. An `Edit` is composition by
#            definition: it reads what an agent wrote and puts different words
#            in their place. So Edit is refused across the whole of ai-labs,
#            and Write is refused everywhere under it except the handful of
#            files the generators own outright.
#
# **What this hook is, and what it is not.** The engineer has `Bash` and full
# sudo, so this is not containment: `sed -i` on an agent's `_reflection.md`
# never reaches this code, and nothing on this box would stop it. What it is
# is the rule stated at the layer that acts, refusing the honest mistake in
# the turn where it is one edit away from being fixed -- the same argument the
# size limit is here for. The lines sudo can cross are held by
# `vault/40_PROJECTS/Loam/engineer/role.md`, and by that file alone. That is
# the trade the owner took on 2026-09-05 when they made the grant full rather than
# scoped: a scoped grant would be a boundary the charter had to list and
# maintain, wrong within a week.
# ---------------------------------------------------------------------------

# The only files under an agent's workspace the engineer may WRITE. Every one
# of them is rendered by a generator and is read-only to the agent itself --
# they are how an agent is assembled, not anything it authored.
#
# `config.md` is the one that is neither. It is operator-authored, and it is
# here for the single exception the role file names: a fleet-wide clause
# the owner has approved, applied identically to all. Write, not Edit, is
# exactly the right verb for that -- an identical clause across seven agents
# is a render, and a per-agent Edit is how six of them end up agreeing and the
# seventh quietly working to a different protocol.
#
# `soul.md` is deliberately absent and always will be. It is the one file that
# must never be shared and never be composed.
ENGINEER_WRITABLE_IN_WORKSPACE = frozenset({
    "CLAUDE.md", "agent.json", ".mcp.json", "config.md",
})

# The self-authored files, named only so the refusal can say WHICH rule it is.
# The rule itself is the inverse of the set above, so a file an agent invents
# tomorrow is covered today.
ENGINEER_SELF_AUTHORED = (
    "_always.md", "_active.md", "_reflection.md", "soul.md", "map.md",
)


def engineer_boundary(
    tool_name: str, verb: str, raw: str, target: Path, config: dict,
) -> None:
    """The engineer's write rule. Refuses by calling refuse(); else returns."""
    agents_root = Path(config["workspace"]).parent
    ai_labs = Path(config.get("ai_labs") or lc.WORKSPACES)
    vault = Path(config["vault"])
    conversations = vault / lc.vault_folder("conversations")

    # The runtime's state and the engineer's job folders are its reach too,
    # wherever the install keeps them (lib/loam_config.py): inside the
    # checkout until 2026-11-14's move, beside it since.
    # The owner's plugins folder too (2026-11-18): the role says to prefer a
    # plugin to a change in Loam's own parts.
    state, jobs, plugins = lc.STATE, lc.JOBS, lc.PLUGINS
    if not any(
        target.is_relative_to(root)
        for root in (agents_root, vault, ai_labs, state, jobs, plugins)
    ):
        refuse(
            f"{verb} of {str(raw)!r} is outside {agents_root}, the vault, "
            f"{ai_labs}, the plugins folder ({plugins}) and the runtime's "
            f"state ({state}). Those are your whole reach."
        )

    if target == conversations or target.is_relative_to(conversations):
        refuse(
            f"{verb} of {str(raw)!r} is inside the vault's conversations folder, owned "
            f"by the transcript writer. Your record of a job is its receipt."
        )

    # `.claude/` anywhere -- your own and every agent's. Checked on both
    # readings of the path because an output-style entry is a symlink and
    # resolve() would walk it back out to a workspace root and see nothing
    # wrong. Provisioning writes these directories, and it does it through
    # bin/provision, which is the generator that owns them; a hand-edit here
    # is drift by construction, whoever makes it.
    for reading in (target, literal(raw)):
        if ".claude" in reading.parts and (
            reading.is_relative_to(ai_labs) or reading.is_relative_to(agents_root)
        ):
            refuse(
                f"{verb} of {str(raw)!r} is inside a `.claude/` directory. "
                f"Those are rendered by bin/provision, which is the one owner "
                f"of them. Change the generator and re-provision; a hand-edit "
                f"here is drift the moment it lands, and bin/check-drift will "
                f"say so."
            )

    if not target.is_relative_to(ai_labs):
        return  # agents/ and the vault: yours.

    # From here down: inside an agent's workspace.
    if tool_name in ("Edit", "MultiEdit", "NotebookEdit"):
        refuse(
            f"{verb} of {str(raw)!r} is an edit inside {ai_labs}. You move, "
            f"link and seed by verbatim copy; you never compose into an "
            f"agent's namespace. If this is a fleet-wide clause {lc.OWNER_NAME} has "
            f"approved, render it and Write the whole file, identically, to "
            f"every agent -- a per-agent edit is how six configs end up "
            f"agreeing and the seventh quietly works to a different protocol."
        )

    workspace = target.parent
    if target.name not in ENGINEER_WRITABLE_IN_WORKSPACE or (
        workspace.parent != ai_labs
    ):
        which = (
            "one of the agent's own files"
            if target.name in ENGINEER_SELF_AUTHORED
            else "not one of the files the generators own"
        )
        refuse(
            f"{verb} of {str(raw)!r} is {which}. Under {ai_labs} you may "
            f"write only {', '.join(sorted(ENGINEER_WRITABLE_IN_WORKSPACE))} "
            f"at a workspace root -- what bin/provision renders, plus "
            f"config.md for an approved fleet-wide clause. Souls, "
            f"`map.md`, `_always.md`, `_active.md`, `_reflection.md`, `crons/`, skills "
            f"and memory belong to the agent. Ask the persona who briefed you "
            f"to ask its owner."
        )


# ---------------------------------------------------------------------------
# The job's rule (since 2026-10-02). Every engineer job edits its own git
# worktree, and the launcher lands it by a tested merge (lib/loam_worktree.py).
# The tree is the job's; the project's canonical checkout is nobody's working
# tree -- for Loam it is the live runtime -- and another job's tree is that
# job's. bin/engineer-job says which is which in the session's environment,
# and the CLI hands its environment to this hook. Outside a job (a persona, an
# interactive session) the variables are absent and this rule is silent.
#
# The same caveat as the engineer's rule above: Bash reaches past this hook.
# It refuses the honest mistake at the layer that acts; it is not a wall.
# ---------------------------------------------------------------------------

# What the platform engineer's settings deny by name in the canonical
# checkout, restated for the tree, where those absolute rules do not reach.
JOB_TREE_READONLY = ("engineer/CLAUDE.md", "engineer/role.md",
                     "engineer/project.md", "engineer/agent.json")


def job_scope() -> dict | None:
    env = {k: os.environ.get(k) for k in (
        "LOAM_JOB_TREE", "LOAM_JOB_DIR", "LOAM_JOB_CANONICAL",
        "LOAM_JOB_WORKTREES")}
    if not env["LOAM_JOB_TREE"] or not env["LOAM_JOB_CANONICAL"]:
        return None
    return {k: resolve(v) for k, v in env.items() if v}


def job_boundary(verb: str, raw: str, scope: dict) -> None:
    """Refuses by calling refuse(); else returns."""
    tree, canonical = scope["LOAM_JOB_TREE"], scope["LOAM_JOB_CANONICAL"]
    job_dir = scope.get("LOAM_JOB_DIR")
    worktrees = scope.get("LOAM_JOB_WORKTREES")
    for reading in (resolve(raw), literal(raw)):
        if reading.is_relative_to(tree):
            rel = reading.relative_to(tree).as_posix()
            if rel in JOB_TREE_READONLY:
                refuse(
                    f"{verb} of {str(raw)!r} is your own composition "
                    f"(`{rel}`), which is operator-authored in every tree as "
                    f"it is in the canonical checkout.")
            continue
        if job_dir and reading.is_relative_to(job_dir):
            continue
        if worktrees and reading.is_relative_to(worktrees):
            refuse(
                f"{verb} of {str(raw)!r} is inside another job's worktree. "
                f"Each job edits its own tree, {tree}, and lands by a merge; "
                f"another job's tree is that job's until it lands or is "
                f"continued.")
        if reading.is_relative_to(canonical):
            refuse(
                f"{verb} of {str(raw)!r} is in {canonical}, the project's "
                f"canonical checkout, which is nobody's working tree. Make "
                f"the change in your own tree, {tree}, and commit it; the "
                f"launcher merges it into main, tests it and pushes when your "
                f"session ends.")
        if job_dir and reading.is_relative_to(job_dir.parent):
            refuse(
                f"{verb} of {str(raw)!r} is in another job's directory. Your "
                f"job's files are under {job_dir}.")


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if tool_name == "apply_patch":
        patch = tool_input.get("command")
        if isinstance(patch, list):  # an argv, the patch is its last element
            patch = patch[-1] if patch else ""
        cwd = payload.get("cwd") or os.getcwd()
        for name, call_input in patch_calls(str(patch or ""), cwd):
            check(payload, name, call_input)
        sys.exit(0)
    check(payload, tool_name, tool_input)
    sys.exit(0)


# Tools that take a path relative to the vault: checked where they write,
# not where the agent's working folder would put it (Images, 2026-11-18).
VAULT_RELATIVE = {"mcp__images__generate_image": ("target_path",)}


def check(payload: dict, tool_name: str, tool_input: dict) -> None:
    """One call against the boundary. Refuses by exiting 2; else returns."""
    entry = WRITE_TOOLS.get(tool_name)
    if not entry:
        return
    for f in VAULT_RELATIVE.get(tool_name, ()):
        raw = tool_input.get(f)
        if isinstance(raw, str) and raw and not raw.startswith("/"):
            tool_input = {**tool_input, f: str(lc.VAULT / raw)}
    verb, fields = entry
    targets = [(f, tool_input.get(f)) for f in fields]
    if not any(raw for _, raw in targets):
        return

    scope = job_scope()
    if scope:
        for _, raw in targets:
            if raw:
                job_boundary(verb, str(raw), scope)
        # A project engineer's job carries no agent.json: its repository is
        # portable and names nothing of this box, so the job's rule is its
        # whole rule here. The platform engineer goes on to its own.
        if not (project_dir_from(payload) / "agent.json").is_file():
            return

    try:
        config = load_config(project_dir_from(payload))
    except LayerError as exc:
        refuse(f"loam boundary: {exc}")

    # Who is calling. A persona's `agent.json` has no `kind`; the
    # engineer's says so, and the two rules below share nothing but this
    # function. The engineer's workspace is under `agents/`, so every
    # persona-shaped derivation here -- `ai_labs` from the workspace's
    # parent, most of all -- is wrong for it by construction.
    if config.get("kind") == "engineer":
        for field, raw in targets:
            if raw:
                engineer_boundary(
                    tool_name, verb, str(raw), resolve(raw), config)
        return

    # The reviewer (Move 11, 2026-09-09). Its whole write reach is its own
    # state directory; the deny rules in its settings already fence ai-labs,
    # the vault and its home, and this is the second wall for the same rule
    # -- plus the sentence that says what the rule is, which a native deny
    # cannot carry. Everything else under agents/ and the rest of the box is
    # outside its grants and fails closed on a headless run anyway.
    if config.get("kind") == "reviewer":
        state = resolve(config.get("state") or "/nonexistent")
        for field, raw in targets:
            if raw and not resolve(raw).is_relative_to(state):
                refuse(
                    f"{verb} of {str(raw)!r} is outside {state}, which is the "
                    f"only place you write. You never act on a persona's "
                    f"workspace, the commons, the vault or the runtime: your "
                    f"note goes into your outbox under {state} and the "
                    f"launcher delivers it; your memory lives beside it."
                )
        return

    ai_labs = Path(config["workspace"]).parent
    workspace = Path(config["workspace"])
    # The town square, co-owned by every agent. Chassis parity: reads are
    # democratised across all of ai-labs, writes are confined to an agent's
    # own workspace plus this.
    commons = ai_labs / "commons"
    vault = Path(config["vault"])
    conversations = vault / lc.vault_folder("conversations")
    filename_checked = FILENAME_FIELDS.get(tool_name, ())

    for field, raw in targets:
        if not raw:
            continue
        target = resolve(raw)

        if field in filename_checked:
            bad = sorted(set(target.name) & ANDROID_ILLEGAL)
            if bad:
                refuse(
                    f"{verb} of {str(raw)!r} has {''.join(bad)!r} in its "
                    f"filename, which Android cannot store — the file would be "
                    f"permanently invisible on the phone. Rename it."
                )

        # Outside both roots entirely. For Write/Edit the native permission
        # layer already refuses this and the check is redundant; for an MCP
        # tool it is not redundant at all, because that layer does not look
        # at MCP arguments. Without it the server's own check would be the
        # only thing standing between move_file's destination and any path
        # on the box -- one wall, where the whole point of this file is that
        # there are two.
        if not (
            target.is_relative_to(ai_labs) or target.is_relative_to(vault)
        ):
            refuse(
                f"{verb} of {str(raw)!r} is outside the vault and ai-labs."
            )

        if (
            target.is_relative_to(ai_labs)
            and not target.is_relative_to(workspace)
            and not target.is_relative_to(commons)
        ):
            refuse(
                f"{verb} of {str(raw)!r} is inside another agent's workspace "
                f"under {ai_labs}. Only your own workspace and the shared "
                f"ai-labs/commons/ are writable."
            )

        if target == conversations or target.is_relative_to(conversations):
            refuse(
                f"{verb} of {str(raw)!r} is inside the vault's conversations folder, "
                f"owned by the transcript writer."
            )

        # The runtime's files at the workspace root -- how the agent is
        # assembled. Claude Code's deny rules say so for Edit; Codex has no
        # deny rules, so the guard says it for every caller (2026-10-01).
        # A skill reached through `.claude/skills` is checked at where it
        # lands, which is `skills/`, and nowhere else (2026-10-13).
        readings = [target]
        if not through_skills_link(str(raw), target, workspace):
            readings.append(literal(raw))
        for reading in readings:
            if reading.is_relative_to(workspace) and reading != workspace:
                head = reading.relative_to(workspace).parts[0]
                if head in RUNTIME_FILES:
                    refuse(
                        f"{verb} of {str(raw)!r} is the runtime's `{head}`, "
                        f"not yours -- it is how you are assembled, and it is "
                        f"read-only to you on purpose. Your own files in the "
                        f"workspace root are where your writing goes."
                    )

        # `.claude/` is the runtime's, not the agent's, and the native rules
        # say so as `Edit(<ws>/.claude/**)`. They said it only for Edit: an
        # MCP move into that directory was gated by neither the deny rule nor
        # this hook (investigation 2026-09-04, section 1 aside). Checked on
        # BOTH readings of the path, because .claude/output-styles/
        # conversation.md is a symlink and resolve() would walk it back out
        # to the workspace root and see nothing wrong. The one literal
        # reading excused is the skills link's, above.
        for reading in readings:
            if reading.is_relative_to(ai_labs) and ".claude" in reading.parts:
                refuse(
                    f"{verb} of {str(raw)!r} is inside a `.claude/` "
                    f"directory. That is the runtime's -- it is how you are "
                    f"assembled, and it is read-only to you on purpose. Your "
                    f"own files in the workspace root are where your writing "
                    f"goes; where one of them is linked from `.claude/`, edit "
                    f"the file itself, not the link."
                )

        if field == RESULT_FIELD.get(tool_name):
            check_auto_loaded(
                tool_name, tool_input, verb, str(raw), target, workspace
            )


if __name__ == "__main__":
    main()
