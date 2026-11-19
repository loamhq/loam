#!/usr/bin/env python3
"""The Loam engineer's provider is its owner's to set (since 2026-11-19).

The owner, in chat: "Please make the Loam engineer's provider editable by me
and other users." "It should be like the others." Until then the platform
engineer ran on Claude Code whatever was set. What is held here:

  the setting   Loam's own setting picks its CLI, as a project engineer's
                does; with none it is Claude Code (an upgrade moves nobody),
                and the default for every other project is never Loam's.
  the line      a platform job on Codex has no Codex sandbox (it would take
                the platform engineer's sudo away), keeps --strict-config
                and the rest of a project job's line; a project job's line
                is unchanged.
  the words     the platform engineer is told it is the platform engineer,
                on each CLI; a project engineer's words are unchanged.
  the wall      on Codex or Antigravity the wall holds read-only what the
                Claude settings deny (the untracked harness, the transcripts,
                the list of public apps) and adds the directories those
                settings add; nothing changes for a Claude job.
  the real wall raised around a shell, when this box's sudo answers without
                a password: sudo works inside it, the tree is writable, the
                harness, the transcripts and the canonical checkout are not.

Run: python3 tests/test_engineer_platform_provider.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_agy_job as aj  # noqa: E402
import loam_codex_job as cj  # noqa: E402
import loam_engineer as eng  # noqa: E402
import loam_worktree as wt  # noqa: E402

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:66} {got!r}"[:240]
          + ("" if ok else f"   want {want!r}"))


def load(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


job = load("engineer_job_platform_provider", AGENTS / "bin" / "engineer-job")
root = Path(tempfile.mkdtemp(prefix="loam-platform-provider-"))


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


try:
    # -- the setting -------------------------------------------------------
    loam = eng.Project("Loam", root / "canon" / "engineer", root / "jobs",
                       platform=True, record={})
    check("nothing set: Claude Code, its built-in model",
          eng.engineer_choice(loam, {}),
          {"runtime": "claude", "model": eng.ENGINEER_MODEL, "effort": None,
           "source": "record"})
    for provider, runtime in (("codex", "codex"), ("antigravity", "antigravity"),
                              ("claude-code", "claude")):
        choice = eng.engineer_choice(loam, {"projects": {"Loam": {"provider": provider}}})
        check(f"Loam's own {provider}: its next job runs {runtime}",
              (choice["runtime"], choice["model"], choice["source"]),
              (runtime, eng.builtin_choice(runtime)[0], "own"))
    choice = eng.engineer_choice(loam, {"projects": {"Loam": {
        "provider": "codex", "model": "gpt-x", "effort": "low"}}})
    check("...with the model and level set for it",
          (choice["model"], choice["effort"]), ("gpt-x", "low"))
    check("...its tiers, as a project engineer's",
          eng.tier_choice(loam, "codex", "hard", {"projects": {"Loam": {
              "provider": "codex", "tiers": {"hard": "gpt-hard"}}}})["model"],
          "gpt-hard")
    check("the default for every other project is never Loam's",
          eng.engineer_choice(loam, {"default": {"provider": "codex"}})["runtime"],
          "claude")
    check("an unknown provider in the file: Claude Code, not a failed launch",
          eng.engineer_choice(loam, {"projects": {"Loam": {"provider": "gpt"}}})["runtime"],
          "claude")
    check("a continuation on the CLI its parent ran: that CLI's built-in",
          eng.choice_for(loam, "claude"), (eng.ENGINEER_MODEL, None))

    # -- the line ----------------------------------------------------------
    tree, jd = root / "tree", root / "jobs" / "j1"
    jd.mkdir(parents=True)
    args = dict(workdir=tree / "engineer", tree=tree, job_dir=jd, prompt="go",
                extra_dirs=[], fork_from=None, instructions_text="i")
    plat = cj.argv(**args, platform=True)
    proj = cj.argv(**args)
    check("platform on Codex: no Codex sandbox (sudo stays)",
          ('sandbox_mode="danger-full-access"' in plat,
           any("workspace-write" in a for a in plat),
           any(a.startswith("permissions.engineer") for a in plat)),
          (True, False, False))
    check("...the rest of the line a project job has",
          all(f in plat for f in ("--strict-config", "--ignore-user-config",
                                  'approval_policy="never"', "-o")), True)
    check("a project job's line unchanged: Codex's write sandbox",
          ('sandbox_mode="workspace-write"' in proj,
           'sandbox_mode="danger-full-access"' in proj), (True, False))
    agy = aj.argv(prompt="p", job_dir=jd, conversation=None,
                  extra_dirs=[eng.VAULT, eng.AI_LABS])
    check("platform on Antigravity: the vault and workspaces added",
          [agy[i + 1] for i, a in enumerate(agy) if a == "--add-dir"],
          [str(jd), str(eng.VAULT), str(eng.AI_LABS)])

    # -- the words ---------------------------------------------------------
    work = root / "words"
    work.mkdir()
    (work / "CLAUDE.md").write_text("@role.md\n")
    (work / "role.md").write_text("The role.\n")
    check("Codex: the platform engineer is told it is",
          cj.instructions(work, platform=True).startswith(cj.PLATFORM_LINE), True)
    check("Antigravity: the same, in its own words",
          aj.instructions(work, platform=True).startswith(aj.PLATFORM_LINE), True)
    check("...and the role file under it",
          "The role." in aj.instructions(work, platform=True), True)
    check("a project engineer's words unchanged",
          (cj.instructions(work).startswith(cj.PROVIDER_LINE),
           aj.instructions(work).startswith(aj.PROVIDER_LINE)), (True, True))

    # -- the wall's lists --------------------------------------------------
    canon = root / "canon"
    (canon / "engineer" / "memory").mkdir(parents=True)
    (canon / "engineer" / "memory" / "MEMORY.md").write_text("m\n")
    git(canon, "init", "-q", "-b", "main")
    git(canon, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    git(canon, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    git(root, "clone", "-q", str(canon), str(tree))
    workdir = tree / "engineer"
    for name in ("CLAUDE.md", "role.md", "project.md", "agent.json"):
        (workdir / name).write_text(name)
    (workdir / ".claude").mkdir()
    check("a Claude platform job: nothing more held, nothing added",
          job.platform_walls(loam, "claude", tree, workdir), ([], []))
    project_engineer = eng.Project("p", tree, root / "pj", platform=False, record={})
    check("a project engineer's walls are its own (unchanged)",
          job.platform_walls(project_engineer, "codex", tree, workdir), ([], []))
    protect, dirs = job.platform_walls(loam, "codex", tree, workdir)
    check("on Codex: the untracked harness held read-only",
          sorted(p.name for p in protect if p.parent == workdir),
          sorted([".claude", "CLAUDE.md", "agent.json", "project.md", "role.md"]))
    check("...the memory, which the tree tracks, stays writable",
          workdir / "memory" in protect or workdir / "memory" / "MEMORY.md" in protect,
          False)
    check("...the transcripts and the list of public apps",
          [eng.VAULT / "15_CONVERSATIONS", Path("/etc/loam/public")] == protect[-2:], True)
    check("...the directories its Claude settings add",
          all(d in (eng.AGENTS, eng.VAULT, eng.AI_LABS, job.lc.PLUGINS) for d in dirs), True)
    check("on Antigravity: the same",
          job.platform_walls(loam, "antigravity", tree, workdir), (protect, dirs))

    # -- the real wall -----------------------------------------------------
    sudo = subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0
    if not sudo:
        # A box without passwordless sudo cannot raise the platform's wall
        # at all. Inside a job's own wall it can (caused 2026-11-19): the
        # paths here are all scratch, so no remount meets the job's.
        print("SKIP  the real platform wall: no passwordless sudo")
    else:
        old_vault, old_worktrees = eng.VAULT, wt.WORKTREES
        eng.VAULT, wt.WORKTREES = root / "vault", root / "worktrees"
        try:
            (eng.VAULT / "15_CONVERSATIONS").mkdir(parents=True)
            wt.WORKTREES.mkdir()
            protect, _ = job.platform_walls(loam, "codex", tree, workdir)
            probe = (
                "r=''; sudo -n true && r=\"$r sudo\"; "
                f"touch {tree}/ok && r=\"$r tree\"; "
                f"touch {workdir}/memory/MEMORY.md && r=\"$r memory\"; "
                f"echo x >> {workdir}/role.md 2>/dev/null && r=\"$r HARNESS\"; "
                f"touch {eng.VAULT}/15_CONVERSATIONS/x 2>/dev/null && r=\"$r TRANSCRIPTS\"; "
                f"touch {canon}/x 2>/dev/null && r=\"$r CANONICAL\"; echo $r")
            out = subprocess.run(
                wt.walled(loam, {"path": str(tree)}, jd, ["sh", "-c", probe], protect=protect),
                capture_output=True, text=True, env={**os.environ, **wt.wall_env()},
                timeout=60)
            check("the real platform wall: sudo, the tree and memory; not the rest",
                  out.stdout.split(), ["sudo", "tree", "memory"])
        finally:
            eng.VAULT, wt.WORKTREES = old_vault, old_worktrees
finally:
    subprocess.run(["sudo", "-n", "rm", "-rf", str(root)], capture_output=True)
    shutil.rmtree(root, ignore_errors=True)

print("FAILURES:", fails) if fails else print("all passed")
sys.exit(1 if fails else 0)
