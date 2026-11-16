"""Every engineer job edits its own git worktree, and lands by a tested merge.

Since 2026-10-02. Until then a project had one checkout and every job of that
project edited it, so the launcher ran one job per project and refused the
second ("two sessions writing the same trees is two writers with no merge").
That rule met the owner three times in one evening. The checkout was what made
the rule necessary, so the checkout stopped being anybody's working tree:

    canonical   the project's checkout on the box (`agents/` itself for the
                Loam lane). `main` is checked out there and it moves only by a
                fast-forward to a merge this module tested. For Loam it is also
                the live runtime, which is why nothing else may write it.
    worktree    `state/worktrees/<project>/<job id>/`, on branch `job/<id>`
                from `main` as it stood when the job began. The session runs
                here, commits here, and never sees another job's tree. Since
                the git isolation of 2026-10-02 it is a clone with a git
                directory of its own, not a `git worktree` (see below).

**Where the worktrees live, and why not in the job directory.** Claude Code
loads every CLAUDE.md from the working directory UP to `/`. The Loam lane's
job directories are under `agents/engineer/jobs/`, so a worktree there would
load the canonical `agents/engineer/CLAUDE.md` -- the live role, not the
tree's -- on top of its own. Nothing above `state/worktrees/` carries one.

**Landing** (`land`) runs under the project's live lock (lib/loam_lock.live):
the branch's own tests, then a merge of the branch into `main` at its current
head -- a fast-forward when `main` has not moved, a merge commit when it has,
never a rebase of `main` and never a force-push -- then the tests again on the
merged tree, then a fast-forward of the canonical `main` to that tested
commit, the push, and the worktree's removal. Any failure leaves `main`,
`origin` and the worktree exactly as they were and says why; the next stage
hands that to a continuation automatically, this one writes the brief for it.
What lands is the job branch's ref, never HEAD: a tree whose HEAD was
detached elsewhere lands its branch, or nothing (``branch_tip``, 2026-10-13).
A read-only brief -- its `involvement` opens with "Read-only" (bin/engineer-job
``is_read_only``) -- lands nothing at all and its tree is removed
(``read_only_result``).

**The harness that is not in the repository.** Since 2026-10-01 a project's
CLAUDE.md, role.md, project.md, memory/ and .claude/ are excluded from its
repository (bin/provision HARNESS_PATHS). `git worktree add` brings only
tracked files, so those are copied in from the canonical checkout; the memory
is merged back into it at landing, three-way against the copy the job began
from, with git's union driver. A tracked memory file gets the same driver
through `info/attributes`, because two jobs that each append a paragraph to
MEMORY.md is the normal case and must not be a conflict.

**A project that lands by pull request** (``Project.pull_request``, since
2026-10-06: a repository Loam does not own) lands differently. Its canonical
`main` is a local mirror of the remote's default branch, fast-forwarded from
it when a job starts (``sync_adopted``), because the remote moves without us.
The landing tests the branch when there is a test command and says it is
untested when there is none; it never merges into `main` and never pushes it.
It refuses a branch whose commits touch the harness, puts the branch in the
canonical repository, and asks the broker to push that branch and open a pull
request against the default branch (lib/loam_github_app.open_pull_request).
The job's own token cannot push at all; see ``job_permissions`` there. On a
person-authored project the remote branch is a plain name from the pull
request's title, not `job/<id>` (``pr_head`` there). A brief titled `Updates #<n>`
updates that open pull request instead, fast-forward only, and posts the
job's reply on it (lib/loam_pr_target.py).
"""

from __future__ import annotations

import json
import os
import resource
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import loam_config as lc

import loam_engineer as eng
import loam_ci
import loam_commit_identity
import loam_lock

WORKTREES = lc.STATE / "worktrees"
MAIN = "main"
RECORD = "worktree.json"

# What bin/provision puts in a checkout that may not be in the repository.
HARNESS = ("CLAUDE.md", "role.md", "project.md", ".claude", "memory")

# How long a landing's test command may run. The agents suite takes about a
# hundred seconds; this is a ceiling, not an estimate.
TEST_TIMEOUT = 900

# How much of a failing test run a receipt quotes.
TAIL_LINES = 40


class WorktreeError(Exception):
    """A git step this module needed did not happen."""


def canonical(project: eng.Project) -> Path:
    """The checkout `main` lives in. Loam's engineer is `agents/engineer/`,
    so its repository is the directory above -- derived from the project, not
    from a constant, so a test's Loam is never the live one."""
    return project.root.parent if project.platform else project.root


def branch_for(job_id: str) -> str:
    return f"job/{job_id}"


def path_for(project: eng.Project, job_id: str) -> Path:
    return WORKTREES / project.name / job_id


def session_dir(project: eng.Project, tree: Path) -> Path:
    """Where the session runs: the tree's `engineer/` for Loam, whose engineer
    project is a subdirectory of the repository, and the tree itself for a
    project engineer."""
    return tree / "engineer" if project.platform else tree


def memory_rel(project: eng.Project) -> str:
    return "engineer/memory/MEMORY.md" if project.platform else "memory/MEMORY.md"


def test_command(project: eng.Project) -> str | None:
    """The command a landing runs, in the tree, to call it tested. A project's
    is `test_command` in its registry record; Loam's is its own runner."""
    if project.platform:
        return "python3 tests/run-all"
    value = project.record.get("test_command")
    return value if isinstance(value, str) and value.strip() else None


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------

def git(repo: Path, *args: str, check: bool = True,
        timeout: int = 300) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        check=False, timeout=timeout)
    if check and result.returncode != 0:
        raise WorktreeError(
            f"`git {' '.join(args)}` in {repo} exited {result.returncode}: "
            f"{(result.stderr or result.stdout).strip()[:500]}")
    return result


def rev(repo: Path, ref: str) -> str | None:
    out = git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}",
              check=False)
    return out.stdout.strip() or None


def is_ancestor(repo: Path, older: str, newer: str) -> bool:
    return git(repo, "merge-base", "--is-ancestor", older, newer,
               check=False).returncode == 0


def tracked(repo: Path, rel: str) -> bool:
    return bool(git(repo, "ls-files", "--", rel, check=False).stdout.strip())


def worktrees(repo: Path) -> set[Path]:
    out = git(repo, "worktree", "list", "--porcelain", check=False).stdout
    return {Path(line[len("worktree "):]) for line in out.splitlines()
            if line.startswith("worktree ")}


def dirty_files(tree: Path) -> list[str]:
    """Uncommitted changes, untracked files included and ignored ones not."""
    out = git(tree, "status", "--porcelain", "--untracked-files=all",
              check=False).stdout
    return [line[3:] for line in out.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# The record a job directory keeps of its tree.
# ---------------------------------------------------------------------------

def read_record(job_dir: Path) -> dict | None:
    try:
        record = json.loads((job_dir / RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def write_record(job_dir: Path, record: dict) -> None:
    tmp = job_dir / (RECORD + ".tmp")
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    os.replace(tmp, job_dir / RECORD)


# ---------------------------------------------------------------------------
# Making the tree.
# ---------------------------------------------------------------------------

def ensure_union(project: eng.Project) -> None:
    """The memory file merges by union in every worktree of this repository.

    `info/attributes` is in the common git directory, so it holds for every
    worktree and is local to this box -- the repository's own files are not
    touched, the way `info/exclude` already carries the harness."""
    repo = canonical(project)
    common = Path(git(repo, "rev-parse", "--git-common-dir").stdout.strip())
    if not common.is_absolute():
        common = repo / common
    attributes = common / "info" / "attributes"
    line = f"/{memory_rel(project)} merge=union"
    current = attributes.read_text(encoding="utf-8") if attributes.is_file() else ""
    if line in current.splitlines():
        return
    attributes.parent.mkdir(parents=True, exist_ok=True)
    attributes.write_text(
        current + ("" if not current or current.endswith("\n") else "\n")
        + "# bin/engineer-job: parallel jobs both append to the memory.\n"
        + line + "\n", encoding="utf-8")


def memory_base(record: dict) -> Path:
    """The untracked memory as the job's tree first had it."""
    return Path(record["path"]).parent / f"{Path(record['path']).name}.memory-base"


# Loam's own engineer is `engineer/` in the repository, and its harness is
# there: provision renders the role, the project file, CLAUDE.md, agent.json
# and .claude/ from the install's operator-authored sources, so a release
# ships none of them and an install's checkout carries them untracked (in
# `info/exclude`). A tree gets them the way a project's tree gets its own.
# Its memory is the repository's, tracked, so it is never seeded.
PLATFORM_HARNESS = tuple(f"engineer/{name}" for name in
                         ("CLAUDE.md", "role.md", "project.md", "agent.json", ".claude"))


def harness_names(project: eng.Project) -> tuple[str, ...]:
    return PLATFORM_HARNESS if project.platform else HARNESS


def seed_harness(project: eng.Project, record: dict) -> list[str]:
    """Copy the untracked harness into a fresh tree. Returns what was copied."""
    repo, tree = canonical(project), Path(record["path"])
    copied = []
    for name in harness_names(project):
        src, dst = repo / name, tree / name
        if not src.exists() or dst.exists() or tracked(repo, name):
            continue
        if src.is_dir():
            shutil.copytree(src, dst, symlinks=True)
        else:
            shutil.copy2(src, dst)
        copied.append(name)
    if "memory" in copied:
        base = memory_base(record)
        shutil.rmtree(base, ignore_errors=True)
        shutil.copytree(repo / "memory", base, symlinks=True)
    return copied


# ---------------------------------------------------------------------------
# The job's own repository (since 2026-10-02, git isolation). Until then a
# job's tree was a `git worktree` of the canonical checkout, so every job
# shared one git directory: the wall could make the canonical files read-only
# but had to leave `.git/objects` and `.git/refs/heads/job/` writable for a
# commit to work, and with them every OTHER job's branch and reflog, the
# object store a `git prune` empties, and -- one `git update-ref` away -- the
# packed refs. Masking one loose ref is not isolation.
#
# So a job's tree is now a clone of its own: `git clone --shared`, which reads
# the canonical object store through `objects/info/alternates` and writes
# every object, ref, index, config and hook into the tree's own `.git`. The
# wall makes the WHOLE canonical repository read-only, `.git` included, and
# binds back nothing of it. A clone's `git gc`/`prune` touches only its own
# store (git never prunes an alternate). The canonical checkout is changed by
# one writer, the landing, which fetches the job's branch out of the tree.
#
# `--shared` is safe here because the alternate is only ever read and its
# objects are reachable from canonical `main`, which only moves forward.
# ---------------------------------------------------------------------------

def is_clone(tree: Path) -> bool:
    return (tree / ".git").is_dir()


def common_dir(repo: Path) -> Path:
    common = Path(git(repo, "rev-parse", "--git-common-dir").stdout.strip())
    return common if common.is_absolute() else repo / common


def configure_clone(repo: Path, tree: Path, identity: dict | None = None) -> None:
    """What a commit and a merge in the clone need from the canonical repo's
    LOCAL config and info/ -- the committer identity (there is no global one
    on this box), the harness exclusion and the memory's union driver -- and
    nothing else: no hooks, no remotes but the canonical one. A project with
    a ``commit_identity`` (lib/loam_commit_identity.py) commits as that person
    instead of as whatever the canonical checkout says."""
    for key in ("user.name", "user.email"):
        value = git(repo, "config", "--local", "--get", key, check=False).stdout.strip()
        if value:
            git(tree, "config", key, value)
    apply_identity(tree, identity)
    git(tree, "config", "gc.auto", "0")
    info_src, info_dst = common_dir(repo) / "info", tree / ".git" / "info"
    info_dst.mkdir(parents=True, exist_ok=True)
    for name in ("exclude", "attributes"):
        if (info_src / name).is_file():
            shutil.copy2(info_src / name, info_dst / name)


def apply_identity(tree: Path, identity: dict | None) -> None:
    if identity:
        git(tree, "config", "user.name", identity["name"])
        git(tree, "config", "user.email", identity["email"])


def refresh_main(repo: Path, tree: Path) -> None:
    """The clone's `main` (and `origin/main`) at the canonical `main`, so `git
    merge main` in the tree means the main a landing merges into. Only for a
    clone: in a legacy worktree `main` is the canonical ref itself. A clone
    that sees the mirror (``mirror_view``) keeps `origin/main` as the
    remote's, like every other `origin/*` there."""
    if is_clone(tree):
        specs = [f"+refs/heads/{MAIN}:refs/heads/{MAIN}"]
        if not sees_mirror(tree):
            specs.append(f"+refs/heads/{MAIN}:refs/remotes/origin/{MAIN}")
        git(tree, "fetch", "--quiet", "origin", *specs)


# ---------------------------------------------------------------------------
# The mirror (since 2026-10-13). A project's canonical checkout is the local
# mirror of its GitHub repository, and a job's clone has the canonical as its
# `origin` -- so a job sees GitHub only as the mirror last fetched it. Until
# this, nothing fetched the mirror's other branches: a pull-request project's
# `sync_adopted` fetched its default branch alone, a Loam-owned project's
# landing fetched `main` alone, and a clone's default refspec reads the
# canonical's `refs/heads/*`, which are `main` and job branches -- never the
# `refs/remotes/origin/*` GitHub's branches live in there. Caused 2026-10-09:
# an adopted project's job asked to read a branch pushed to GitHub that
# afternoon reported it did not exist; `git fetch --all` in the job fetched
# the mirror, not GitHub (job 20261009-132136-6d5239b2, and
# 20261009-134034-a41d47a0, which found it by asking GitHub directly).
#
# So a job's setup (``setup``) on a project whose record sets
# ``"mirror_refresh": true`` first refreshes the mirror from its `origin` --
# every branch, pruned, and every tag -- and then points the clone's
# `origin/*` at the mirror's copy of the remote's branches, so the job's own
# `git fetch` keeps reading them. A refresh that fails does not refuse the
# job: the job runs on the mirror as it was, which is what `sync_adopted`
# already did with a failed fetch, and the receipt's `mirror` row says the
# refresh failed and when the last one succeeded. A project's own setting,
# never a default, on the owner's word the same day: "each project might have
# its own requirements and needs". A project without the key gets none of this: git's
# default view of the canonical, no fetch from GitHub, no `mirror` row.
#
# Tags are fetched with `--tags`, which `--prune` does not prune: a release
# tag `cut_release` has made and not yet pushed is never deleted by a job
# starting beside it. A branch deleted on GitHub leaves the mirror on the
# next job's start; its objects stay until a `git gc` there expires them
# (two weeks by default), longer than any clone that fetched the ref lives.
# ---------------------------------------------------------------------------

MIRROR_REFSPEC = "+refs/remotes/origin/*:refs/remotes/origin/*"
MIRROR_SKIP = "^refs/remotes/origin/HEAD"
MIRROR_STATE = "loam-mirror.json"
MIRROR_TIMEOUT = 180


def refreshes_mirror(project: eng.Project) -> bool:
    return project.record.get("mirror_refresh") is True


def mirror_branches(repo: Path) -> int:
    out = git(repo, "for-each-ref", "--format=%(refname)", "refs/remotes/origin/",
              check=False).stdout.split()
    return sum(1 for ref in out if ref != "refs/remotes/origin/HEAD")


def refresh_mirror(repo: Path, url: str | None) -> dict:
    """Fetch every branch and tag of the canonical's `origin` into it,
    pruning branches the remote no longer has. Never raises. Returns

        at        when the refresh ran (ISO, UTC)
        remote    the project's remote, from its record
        ok        True when the fetch succeeded
        branches  how many of the remote's branches the mirror holds after
        detail    git's words when it failed
        last_ok   when a refresh last succeeded, this one included; None if
                  none ever has

    The last success is kept in the canonical's git directory, beside the
    refs it describes. One refresh at a time per repository: two jobs
    starting together would otherwise race for the same ref locks."""
    import fcntl  # noqa: PLC0415

    try:
        state_file = common_dir(repo) / MIRROR_STATE
        with open(common_dir(repo) / (MIRROR_STATE + ".lock"), "a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            at = eng.iso(eng.now_utc())
            try:
                out = subprocess.run(
                    ["git", "-C", str(repo), "fetch", "--quiet", "--prune",
                     "--tags", "--force", "origin",
                     "+refs/heads/*:refs/remotes/origin/*"],
                    capture_output=True, text=True, check=False,
                    timeout=MIRROR_TIMEOUT,
                    env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
                ok, detail = out.returncode == 0, (out.stderr or out.stdout).strip()
            except subprocess.TimeoutExpired:
                ok, detail = False, f"timed out after {MIRROR_TIMEOUT} s"
            try:
                last_ok = json.loads(state_file.read_text(encoding="utf-8")).get("last_ok")
            except (OSError, ValueError, AttributeError):
                last_ok = None
            if ok:
                last_ok = at
                tmp = state_file.with_name(state_file.name + ".tmp")
                tmp.write_text(json.dumps({"last_ok": at, "remote": url}),
                               encoding="utf-8")
                os.replace(tmp, state_file)
    except (OSError, WorktreeError) as exc:
        return {"at": eng.iso(eng.now_utc()), "remote": url, "ok": False,
                "branches": mirror_branches(repo), "detail": str(exc)[:300],
                "last_ok": None}
    return {"at": at, "remote": url, "ok": ok,
            "branches": mirror_branches(repo),
            "detail": None if ok else (detail[-300:] or "no output"),
            "last_ok": last_ok}


def sees_mirror(tree: Path) -> bool:
    out = git(tree, "config", "--get-all", "remote.origin.fetch", check=False)
    return MIRROR_REFSPEC in out.stdout.split()


def mirror_view(tree: Path) -> None:
    """Point the clone's `origin/*` at the remote's branches as the mirror
    holds them, and fetch them now: `git branch -r` in the job lists GitHub's
    branches, `git checkout --track origin/<branch>` checks one out (the
    bare-name guess does not: git makes it only from `refs/heads/*` sources),
    and the job's own `git fetch` re-reads the mirror's copy. The canonical's
    job branches drop out of the view, which no job needed."""
    git(tree, "config", "--replace-all", "remote.origin.fetch", MIRROR_REFSPEC)
    git(tree, "config", "--add", "remote.origin.fetch", MIRROR_SKIP)
    git(tree, "fetch", "--quiet", "--prune", "origin")


def mirror_row(record: dict) -> str:
    """The receipt's `mirror` row, from the tree's record; empty for a
    project that does not refresh its mirror."""
    mirror = record.get("mirror")
    if not mirror:
        return ""
    count = mirror["branches"]
    branches = f"{count} branch{'' if count == 1 else 'es'}"
    if mirror["ok"]:
        return (f"\n| mirror | refreshed from `{mirror['remote']}` at {mirror['at']}: "
                f"every branch and tag, pruned; the mirror holds {branches} |")
    last = (f"its last successful refresh was {mirror['last_ok']}"
            if mirror.get("last_ok") else "it has never refreshed successfully")
    detail = " ".join((mirror.get("detail") or "").split())[:200]
    return (f"\n| mirror | **refresh failed** from `{mirror['remote']}` at {mirror['at']} "
            f"({detail}); the job ran on the mirror as it was, and {last}. "
            f"It holds {branches} |")


def make_clone(repo: Path, path: Path, branch: str, start: str = MAIN,
               identity: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    git(repo.parent, "clone", "--quiet", "--shared", "--no-checkout",
        "--branch", MAIN, str(repo), str(path))
    configure_clone(repo, path, identity)
    if start != MAIN:
        git(path, "fetch", "--quiet", "origin", f"+refs/heads/{start}:refs/heads/{branch}")
        git(path, "checkout", "--quiet", branch)
    else:
        git(path, "checkout", "--quiet", "-b", branch, MAIN)


def convert_worktree(repo: Path, tree: Path, branch: str) -> str:
    """Turn a legacy `git worktree` into a clone IN PLACE, keeping its path
    (a continuation resumes a session keyed on it) and every file in it,
    committed or not. Its branch stays in the canonical repository too; only
    the canonical's per-worktree bookkeeping for it is retired. The index is
    rebuilt from HEAD, so what was staged reads as unstaged, nothing lost."""
    legacy = gitdir_of(tree)
    staging = tree.parent / f".{tree.name}.clone-{os.getpid()}"
    shutil.rmtree(staging, ignore_errors=True)
    git(repo.parent, "init", "--quiet", str(staging))
    objects = common_dir(repo) / "objects"
    (staging / ".git" / "objects" / "info" / "alternates").write_text(
        f"{objects}\n", encoding="utf-8")
    git(staging, "remote", "add", "origin", str(repo))
    git(staging, "fetch", "--quiet", "origin",
        f"+refs/heads/{branch}:refs/heads/{branch}",
        f"+refs/heads/{MAIN}:refs/heads/{MAIN}",
        f"+refs/heads/{MAIN}:refs/remotes/origin/{MAIN}")
    git(staging, "symbolic-ref", "HEAD", f"refs/heads/{branch}")
    configure_clone(repo, staging)
    (tree / ".git").replace(tree.parent / f"{tree.name}.legacy-gitfile")
    (staging / ".git").rename(tree / ".git")
    shutil.rmtree(staging, ignore_errors=True)
    git(tree, "reset", "--quiet")
    if legacy and legacy.is_dir() and legacy.parent.name == "worktrees":
        shutil.rmtree(legacy, ignore_errors=True)
    return "converted from a shared worktree to a clone of its own"


CLAUDE_JSON = lc.HOME / ".claude.json"
LIVE_WORKTREES = lc.STATE / "worktrees"


TRUST_FLAGS = ("hasTrustDialogAccepted", "hasClaudeMdExternalIncludesApproved",
               "hasClaudeMdExternalIncludesWarningShown")


def ensure_trust(tree: Path) -> bool:
    """Trust the clone in ~/.claude.json. The CLI keys trust on the git root:
    a worktree's was the canonical checkout, which provision trusts, but a
    clone is a root of its own, and an untrusted one has its settings'
    permissions ignored (every Bash call would wait for an approval nobody
    gives). Same discipline as bin/provision.ensure_trust: write only when
    missing, re-read if a live session wrote meanwhile, replace atomically.
    Only for a tree under the live worktrees root, so a test never edits the
    real file. True when the entry is (now) there."""
    if not tree.is_relative_to(LIVE_WORKTREES) or not CLAUDE_JSON.is_file():
        return False
    for _attempt in range(5):
        before = CLAUDE_JSON.stat()
        cfg = json.loads(CLAUDE_JSON.read_text(encoding="utf-8"))
        entry = cfg.setdefault("projects", {}).setdefault(str(tree), {})
        # Trust, and the approval to load CLAUDE.md's one import from
        # outside the tree, the shared reach.md (2026-11-10; without it the
        # import is dropped silently: bin/provision TRUST_FLAGS).
        if all(entry.get(flag) for flag in TRUST_FLAGS):
            return True
        entry.update({flag: True for flag in TRUST_FLAGS})
        now = CLAUDE_JSON.stat()
        if (now.st_mtime_ns, now.st_size) != (before.st_mtime_ns, before.st_size):
            time.sleep(0.2)
            continue
        tmp = CLAUDE_JSON.with_name(f"{CLAUDE_JSON.name}.loam-trust-{os.getpid()}")
        tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        os.replace(tmp, CLAUDE_JSON)
        return True
    return False


def sync_adopted(project: eng.Project) -> str:
    """Fast-forward a pull-request project's canonical `main` to the remote's
    default branch, under the project's live lock. Returns what happened, in
    words for the job's record; never raises. A failed fetch leaves `main`
    where it was, which the job still works from."""
    repo, base = canonical(project), project.record.get("default_branch") or MAIN
    try:
        with loam_lock.live(project.name, "sync with the remote", wait_seconds=120):
            fetched = git(repo, "fetch", "--quiet", "--no-tags", "origin",
                          f"+refs/heads/{base}:refs/remotes/origin/{base}",
                          check=False, timeout=180)
            if fetched.returncode != 0:
                return f"fetch failed: {(fetched.stderr or '').strip()[:200]}"
            local, remote = rev(repo, MAIN), rev(repo, f"origin/{base}")
            if local == remote:
                return f"at origin/{base}"
            out = git(repo, "merge", "--quiet", "--ff-only", f"origin/{base}", check=False)
            return (f"fast-forwarded to origin/{base} {remote[:7]}" if out.returncode == 0
                    else f"could not fast-forward to origin/{base}: "
                         f"{(out.stderr or '').strip()[:200]}")
    except (loam_lock.Busy, OSError, subprocess.SubprocessError, WorktreeError) as exc:
        return f"not synced: {exc}"


def current_place(path: Path) -> Path:
    """A recorded tree path, where trees are kept now. A job recorded before
    the state moved out of the checkout (2026-11-14) names
    `<checkout>/state/worktrees/<project>/<job>`; continued, it reopens at
    `<paths.state>/worktrees/<project>/<job>` instead of recreating a
    `state/` inside the checkout. The session still resumes: the CLI finds it
    by id, and bin/move-state carried its folder to the new name."""
    if path.is_relative_to(WORKTREES) or path.parent.parent.name != "worktrees":
        return path
    return WORKTREES / path.parent.name / path.name


def setup(project: eng.Project, job_id: str, job_dir: Path,
          continues: Path | None = None) -> dict:
    """Make (or, for a continuation, reopen) this job's tree and record it.

    A continuation resumes the previous job's CLI session, and the CLI keeps a
    session's transcript per working directory -- so it must run at the SAME
    path. If the previous branch never landed, the tree is reopened on it. If
    it landed, its tree was removed with it; a fresh branch from `main` is
    made at the same path, which is what lets the session resume at all.

    Every tree made here is a clone of its own (see above). A continuation of
    a job whose tree is a legacy worktree converts it in place.
    """
    repo = canonical(project)
    bad = loam_commit_identity.malformed(project.record)
    if bad:
        raise WorktreeError(f"{project.name}: {bad}")
    identity = loam_commit_identity.of(project.record)
    ensure_union(project)
    git(repo, "worktree", "prune", check=False)
    mirror = refresh_mirror(repo, project.remote) if refreshes_mirror(project) else None
    synced = sync_adopted(project) if project.pull_request else None
    prior = read_record(continues) if continues else None
    if prior:
        path, branch = Path(prior["path"]), prior["branch"]
        path = current_place(path)
        root_job, base = prior.get("root_job") or prior.get("job"), prior.get("base")
        reopened = "reused"
        if path.is_dir() and is_clone(path):
            refresh_main(repo, path)
        elif path.is_dir() and path in worktrees(repo):
            reopened = convert_worktree(repo, path, branch)
        elif rev(repo, f"refs/heads/{branch}"):
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
            make_clone(repo, path, branch, start=branch, identity=identity)
            reopened = "reopened"
        else:
            branch, base = branch_for(job_id), rev(repo, MAIN)
            make_clone(repo, path, branch, identity=identity)
            reopened = "fresh at the same path (the previous branch landed)"
    else:
        path, branch = path_for(project, job_id), branch_for(job_id)
        root_job, base, reopened = job_id, rev(repo, MAIN), "new"
        make_clone(repo, path, branch, identity=identity)
    record = {"project": project.name, "job": job_id, "root_job": root_job,
              "path": str(path), "kind": "clone",
              "branch": branch, "base": base, "canonical": str(repo),
              "opened": reopened}
    if mirror:
        mirror_view(path)
        record["mirror"] = mirror
    if synced:
        record["synced"] = synced
    if identity:
        # A reused or converted tree too: its config predates the record's
        # identity, or was the engineer's.
        apply_identity(path, identity)
        record["commit_identity"] = f"{identity['name']} <{identity['email']}>"
    record["seeded"] = seed_harness(project, record)
    record["trusted"] = ensure_trust(path)
    write_record(job_dir, record)
    return record


def tree_lock(record: dict):
    """Held by the job running in this tree, so two continuations of one job
    cannot both resume into it. Non-blocking: the second is refused."""
    path = Path(record["path"])
    return loam_lock.LOCK_DIR / f"tree-{path.parent.name}-{path.name}.lock"


# ---------------------------------------------------------------------------
# The wall (since 2026-10-02, stage two). The guard refuses a job's Write and
# Edit outside its tree; this does the same for everything the session runs,
# Bash included, by running the CLI in a private mount namespace where the
# canonical checkout, every job's tree and the jobs root are READ-ONLY, and
# only this job's tree, its job directory and the parts of the shared git
# directory a commit writes are bound back read-write.
#
# Two ways in, because the two kinds of engineer differ in one property:
#
#   Loam      `sudo unshare -m`, then `setpriv` back to this user. A real
#             mount namespace in the host's user namespace, so the engineer's
#             sudo still works inside it -- full sudo is its project file's
#             grant, and a user namespace would have taken it away (sudo
#             refuses to run when /usr/bin/sudo is not owned by uid 0, which
#             is how it looks from inside one). Root inside sees the same
#             read-only mounts; only a deliberate remount gets past them.
#   project   `unshare -Urm`, then `unshare -U` mapping back to this uid. The
#             unit carries NoNewPrivileges, so sudo is out; an unprivileged
#             user namespace needs none. Mount flags made read-only in a user
#             namespace are LOCKED: a bind taken later from inside a
#             read-only mount stays read-only (caused, 2026-10-02). So every
#             read-write path is bound to a staging point first, while it is
#             still writable, and moved into place after the read-only binds.
#
# Both use the staging order, so one script serves both.
# ---------------------------------------------------------------------------

def gitdir_of(tree: Path) -> Path | None:
    try:
        line = (tree / ".git").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return Path(line.split(":", 1)[1].strip()) if line.startswith("gitdir:") else None


def wall_paths(project: eng.Project, record: dict, job_dir: Path) -> dict:
    """{"ro": [...], "rw_broad": [...], "ro_after": [...], "rw": [...]} in
    the order they are applied. Directories that do not exist are dropped."""
    repo, tree = canonical(project), Path(record["path"])
    rw = [tree, job_dir]
    if not is_clone(tree):
        # A legacy worktree (made before git isolation, still running): its
        # commits need the shared git directory's parts, as they always did.
        common = repo / ".git"
        for sub in ("refs/heads/job", "logs/refs/heads/job"):
            (common / sub).mkdir(parents=True, exist_ok=True)
        rw += [common / "objects", common / "refs" / "heads" / "job",
               common / "logs" / "refs" / "heads" / "job"]
        gitdir = gitdir_of(tree)
        if gitdir:
            rw.append(gitdir)
    # Loam's canonical checkout holds the runtime's own state and logs, which
    # its tools write while the engineer works (locks, markers, test scratch);
    # they are not the repository and stay writable -- except the trees.
    broad = ([p for p in (lc.STATE, lc.LOGS) if p.is_relative_to(repo)]
             if project.platform else [])
    ro_after = [WORKTREES, project.jobs]
    keep = lambda paths: [p for p in paths if p.is_dir()]  # noqa: E731
    return {"ro": keep([repo]), "rw_broad": keep(broad),
            "ro_after": keep(ro_after), "rw": keep(rw)}


def wall_script(project: eng.Project, record: dict, job_dir: Path,
                protect: list[Path] = ()) -> str:
    """The mounts, as one shell line. ``protect`` (since 2026-11-15): paths
    inside the tree held read-only over its writable bind, for a CLI with no
    write sandbox of its own to hold them (an Antigravity job's harness,
    lib/loam_agy_job.py); a path that is not there is skipped."""
    paths = wall_paths(project, record, job_dir)
    stage_root = f"/tmp/loam-wall-{Path(record['path']).name}-{os.getpid()}"
    staged = paths["rw_broad"] + paths["rw"]
    steps = [f"mkdir -p {_q(stage_root)}"]
    for i, path in enumerate(staged):
        steps += [f"mkdir -p {_q(f'{stage_root}/{i}')}",
                  f"mount --bind {_q(str(path))} {_q(f'{stage_root}/{i}')}"]

    def read_only(path: Path) -> list[str]:
        return [f"mount --bind {_q(str(path))} {_q(str(path))}",
                f"mount -o remount,bind,ro {_q(str(path))} {_q(str(path))}"]

    def place(i: int, path: Path) -> list[str]:
        return [f"mount --bind {_q(f'{stage_root}/{i}')} {_q(str(path))}"]

    for path in paths["ro"]:
        steps += read_only(path)
    for i, path in enumerate(paths["rw_broad"]):
        steps += place(i, path)
    for path in paths["ro_after"]:
        steps += read_only(path)
    offset = len(paths["rw_broad"])
    for i, path in enumerate(paths["rw"]):
        steps += place(offset + i, path)
    for path in protect:
        if path.exists():
            steps += read_only(path)
    for i in range(len(staged)):
        steps.append(f"umount {_q(f'{stage_root}/{i}')}")
    # Braced (2026-11-15): bare, its `|| true` closed the whole `&&` chain,
    # so a mount that failed skipped every mount after it and the CLI still
    # ran behind a partial wall (caused from inside a job, where the nested
    # remount of the worktrees fails). Now a failed mount ends the line and
    # the session is not started.
    steps.append(f"{{ rmdir {_q(stage_root)}/* {_q(stage_root)} 2>/dev/null || true; }}")
    return " && ".join(steps)


def walled(project: eng.Project, record: dict, job_dir: Path,
           argv: list[str], protect: list[Path] = ()) -> list[str]:
    """`argv`, run behind the wall as this user with this environment."""
    uid, gid = os.getuid(), os.getgid()
    script = wall_script(project, record, job_dir, list(protect))
    if project.platform:
        # sudo resets PATH to its secure_path and may reset HOME; both are
        # put back for the CLI by name rather than trusted to survive.
        back = (f"exec setpriv --reuid={uid} --regid={gid} --init-groups -- "
                f"env PATH=\"$LOAM_WALL_PATH\" HOME=\"$LOAM_WALL_HOME\" "
                f"LOAM_WALLED=platform \"$@\"")
        prefix = ["sudo", "-n", "--preserve-env", "unshare", "-m",
                  "--propagation", "private"]
    else:
        back = (f"exec unshare -U --map-user={uid} --map-group={gid} -- "
                f"env LOAM_WALLED=project \"$@\"")
        prefix = ["unshare", "-Urm", "--propagation", "private"]
    return prefix + ["sh", "-c", f"{script} && {back}", "loam-wall", *argv]


def wall_env() -> dict[str, str]:
    return {"LOAM_WALL_PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LOAM_WALL_HOME": os.environ.get("HOME") or str(lc.HOME)}


def wall_check(project: eng.Project, record: dict, job_dir: Path) -> str | None:
    """Raise the wall around a probe before the session: why it cannot be
    raised, or None. A job whose wall fails does not run unwalled."""
    tree = Path(record["path"])
    probe = (f"touch {_q(str(tree / '.loam-wall-probe'))} && "
             f"rm {_q(str(tree / '.loam-wall-probe'))} && "
             f"! touch {_q(str(canonical(project) / '.loam-wall-probe'))} 2>/dev/null")
    out = subprocess.run(walled(project, record, job_dir, ["sh", "-c", probe]),
                         capture_output=True, text=True, check=False,
                         env={**os.environ, **wall_env()}, timeout=60)
    if out.returncode == 0:
        return None
    return (f"the wall did not hold (exit {out.returncode}): "
            f"{(out.stderr or out.stdout).strip()[-400:] or 'no output'}")


# ---------------------------------------------------------------------------
# Testing a tree.
# ---------------------------------------------------------------------------

def tail(text: str, lines: int = TAIL_LINES) -> str:
    return "\n".join(text.rstrip().splitlines()[-lines:])


def platform_argv(tree: Path, command: str) -> list[str]:
    """Run `command` with the tree's files at the runtime's own path.

    The runtime names itself by absolute path -- lib/ is put on sys.path as
    `<root>/lib` by some scripts and test -- so a test run in
    a worktree would import the LIVE code and pass for the wrong reason. A
    private mount namespace shows the tree at the canonical path to this one
    command and nobody else: the outer user namespace is root so it may
    mount, the inner one maps back to this uid so every test sees the user it
    was written for.

    What is bound is each top-level entry the tree's HEAD tracks, over the
    canonical one -- not the tree as a whole. A worktree's `.git` is a file
    naming `<root>/.git/worktrees/<id>`, which a whole-tree
    mount would hide, and every git call a test makes from the repository
    would then fail (caused: test_github_app's `git credential fill`). So the
    canonical `.git` stays, and with it everything untracked -- state/,
    logs/, the venv -- exactly as a test run in the canonical checkout sees
    it. The venv is bound under the tree's `mcp/` first, because that mount
    hides the canonical one. A top-level entry the job ADDED has nothing to
    mount over and is missing from the view; the tests say so if it matters.
    """
    uid, gid = os.getuid(), os.getgid()
    entries = git(tree, "ls-tree", "--name-only", "HEAD").stdout.split("\n")
    mounts = []
    venv_live, venv_tree = lc.MCP_VENV, tree / "mcp" / ".venv"
    if venv_live.is_dir() and venv_tree.is_dir():
        mounts.append(f"mount --bind {_q(str(venv_live))} {_q(str(venv_tree))}")
    for name in filter(None, entries):
        src, dst = tree / name, eng.AGENTS / name
        if dst.exists() and src.is_dir() == dst.is_dir():
            mounts.append(f"mount --rbind {_q(str(src))} {_q(str(dst))}")
    inner = (f"cd {eng.AGENTS} && exec unshare -U --map-user={uid} "
             f"--map-group={gid} sh -c {_q(command)}")
    script = " && ".join(mounts + [f"exec sh -c {_q(inner)}"])
    return ["unshare", "-Urm", "sh", "-c", script]


def _q(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


CREDENTIAL_NAMES = frozenset({"CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY",
                              "ANTHROPIC_AUTH_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"})
CREDENTIAL_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD")


def is_credential(name: str) -> bool:
    return name in CREDENTIAL_NAMES or name.endswith(CREDENTIAL_SUFFIXES)


def run_tests(project: eng.Project, tree: Path) -> tuple[bool | None, str, str]:
    """(passed, the command, the output's tail). None when there is no test
    command to run, which a landing treats as a failure to be reported."""
    command = test_command(project)
    if not command:
        return None, "", ""
    if project.platform:
        (tree / "mcp" / ".venv").mkdir(parents=True, exist_ok=True)
        argv, cwd = platform_argv(tree, command), str(tree)
    else:
        argv, cwd = ["sh", "-c", command], str(tree)
    # The tests run as themselves, not as the job: a landing runs inside the
    # job's own environment, and a test that feeds the guard a payload would
    # otherwise be judged by the JOB's boundary (caused: test_boundary_guard
    # failing one case on a mid-job landing, 2026-10-02).
    #
    # And without credentials. The launcher's unit mounts wake.env (and, for
    # Loam, secrets.env), so a landing ran every test with the account's
    # OAuth token in its environment -- which no developer's shell has, and
    # which test_broker_login reads as "the session runs on a token" (4
    # failures on the first in-unit Loam landing, 2026-10-02, while the same
    # suite passed from a session). A project's test command is also code a
    # job just wrote; it has no business holding the account's keys.
    env = {k: v for k, v in os.environ.items()
           if k != "CLAUDE_PROJECT_DIR"
           and not k.startswith(("LOAM_JOB_", "LOAM_WALL"))
           and not is_credential(k)}
    # And offline: a key read from a file, not the environment, is refused
    # too (lib/loam_offline.py; the 2026-11-10 DNS outage).
    env["LOAM_OFFLINE"] = "1"
    # And with a lock table of their own: the suite takes run slots and live
    # locks and waits for them, and a landing runs while this job -- and
    # every job beside it -- holds a real slot. With all three held the
    # agents suite waited out its own timeout (caused, 2026-10-02).
    locks = Path(tempfile.mkdtemp(prefix="loam-test-locks-"))
    env["LOAM_LOCK_DIR"] = str(locks)
    env.pop("LOAM_LIVE_LOCKS", None)
    try:
        result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                                check=False, timeout=TEST_TIMEOUT, env=env,
                                stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        return False, command, tail(out) + f"\n(timed out after {TEST_TIMEOUT} s)"
    finally:
        shutil.rmtree(locks, ignore_errors=True)
    return result.returncode == 0, command, tail(
        (result.stdout or "") + (result.stderr or ""))


# ---------------------------------------------------------------------------
# Landing.
# ---------------------------------------------------------------------------

def merge_memory_back(project: eng.Project, record: dict) -> list[str]:
    """Fold an untracked memory/ back into the canonical checkout, three-way
    by union against the copy the tree began with. Returns what changed."""
    if "memory" not in (record.get("seeded") or []):
        return []
    repo, tree = canonical(project), Path(record["path"])
    base_dir = memory_base(record)
    changed = []
    for theirs in sorted((tree / "memory").rglob("*")):
        if not theirs.is_file():
            continue
        rel = theirs.relative_to(tree)
        ours, base = repo / rel, base_dir / theirs.relative_to(tree / "memory")
        new = theirs.read_bytes()
        if base.is_file() and base.read_bytes() == new:
            continue                        # the job did not change it
        if not ours.is_file():
            ours.parent.mkdir(parents=True, exist_ok=True)
            ours.write_bytes(new)
            changed.append(str(rel))
            continue
        base_arg = str(base) if base.is_file() else os.devnull
        merged = subprocess.run(
            ["git", "merge-file", "--union", "-p", str(ours), base_arg, str(theirs)],
            capture_output=True, check=False)
        if merged.stdout != ours.read_bytes():
            ours.write_bytes(merged.stdout)
            changed.append(str(rel))
    return changed


def branch_tip(tree: Path, branch: str) -> str | None:
    """What a landing lands: the job branch's ref, never HEAD. Caused
    2026-10-12: a read-only job of an adopted project checked out a pull request's
    commit detached (`git checkout --detach 27928e66`) and committed nothing;
    the landing read HEAD as the branch tip, saw work that `main` lacked, and
    asked the broker to push `job/<id>` -- still at the base -- as a pull
    request GitHub refused ("No commits between main and ...")."""
    return rev(tree, f"refs/heads/{branch}")


def head_note(tree: Path, branch: str) -> str | None:
    """Where HEAD stood when it was not the branch: "detached at <sha>", "on
    <other branch>", or None when the tree is on its branch."""
    on = git(tree, "symbolic-ref", "--quiet", "--short", "HEAD",
             check=False).stdout.strip()
    if on == branch:
        return None
    if on:
        return f"on `{on}`"
    return f"detached at `{(rev(tree, 'HEAD') or '?')[:7]}`"


def onto_branch(tree: Path, branch: str, result: dict) -> None:
    """Put a clean tree back on its branch before the landing tests and
    merges it, noting where HEAD was. Only called once the tree is known to
    be clean, so nothing uncommitted can be lost; a commit made on a
    detached HEAD is not on the branch and does not land."""
    note = head_note(tree, branch)
    if note:
        result["head"] = note
        git(tree, "checkout", "--quiet", branch)


def landed_earlier(project: eng.Project, record: dict) -> str | None:
    """The branch tip, when the branch has commits of its own (it moved from
    where the tree began) and every one of them is already on `main`. None
    otherwise. Caused 2026-10-02: job 20261001-175221-8c963ca0 landed its
    work mid-job with `--land`, and its end-of-job record said "not landed;
    nothing was pushed" while its own git table showed main at the tip."""
    repo, tree = canonical(project), Path(record["path"])
    if not tree.is_dir():
        return None
    tip, main = branch_tip(tree, record["branch"]), rev(repo, MAIN)
    base = rev(repo, record["base"]) if record.get("base") else None
    if not tip or not main or tip == base or not is_ancestor(repo, tip, main):
        return None
    return tip


def incomplete_result(project: eng.Project, record: dict, detail: str) -> dict:
    """What the launcher records for a job that did not complete (or wrote no
    receipt) instead of landing it. A branch whose work is already on `main`
    is reported as landed earlier, with no continuation: there is nothing
    left on it to finish."""
    base = {"branch": record["branch"], "tree": record["path"], "files": [],
            "detail": detail}
    earlier = landed_earlier(project, record)
    if earlier:
        return {**base, "state": "landed_earlier", "sha": earlier,
                "reason": None, "incomplete": True}
    return {**base, "state": "not_landed", "reason": "incomplete"}


def remove_tree(project: eng.Project, record: dict, *, delete_branch: bool) -> None:
    repo, tree = canonical(project), Path(record["path"])
    git(repo, "worktree", "remove", "--force", str(tree), check=False)
    shutil.rmtree(tree, ignore_errors=True)
    shutil.rmtree(memory_base(record), ignore_errors=True)
    git(repo, "worktree", "prune", check=False)
    if delete_branch:
        git(repo, "branch", "-D", record["branch"], check=False)


def sync_with_origin(repo: Path) -> str | None:
    """Bring the canonical `main` up to `origin/main` if it is behind. Returns
    why it could not, or None. A fetch that fails is not a refusal -- the
    push will say so if the remote moved -- but a `main` that has diverged
    from its remote is, because no merge here may rewrite either."""
    if not git(repo, "remote", check=False).stdout.split():
        return None
    if git(repo, "fetch", "--quiet", "origin", MAIN, check=False,
           timeout=120).returncode != 0:
        return None
    local, remote = rev(repo, MAIN), rev(repo, f"origin/{MAIN}")
    if not remote or local == remote or is_ancestor(repo, remote, local):
        return None
    if is_ancestor(repo, local, remote):
        out = git(repo, "merge", "--ff-only", remote, check=False)
        return None if out.returncode == 0 else (
            f"the canonical checkout could not fast-forward to origin/{MAIN} "
            f"({remote[:7]}): {out.stderr.strip()[:300]}")
    return (f"the canonical {MAIN} ({local[:7]}) and origin/{MAIN} "
            f"({remote[:7]}) have diverged; nothing here rewrites either")


def open_pr(project: eng.Project, branch: str, title: str, body: str) -> dict:
    """The broker's push and pull request. A function of its own so a test
    can stand in for the broker."""
    import loam_github  # noqa: PLC0415
    return loam_github.open_pull_request(project.name, branch, title, body)


def update_pr(project: eng.Project, branch: str, number: int, reply: str) -> dict:
    """The broker's fast-forward of an open pull request and its reply
    (lib/loam_pr_target.py). A function of its own for the same reason."""
    import loam_pr_target  # noqa: PLC0415
    return loam_pr_target.update(project.name, branch, number, reply)


# What a pull-request project's commits may never touch: the harness, which
# lives on this box and never in someone else's history.
HARNESS_IN_COMMITS = HARNESS + (".codex",)


def harness_in_commits(tree: Path, base: str, tip: str) -> list[str]:
    changed = git(tree, "diff", "--name-only", f"{base}...{tip}",
                  check=False).stdout.split()
    return [f for f in changed
            if any(f == h or f.startswith(h + "/") for h in HARNESS_IN_COMMITS)]


def refresh_remote_view(project: eng.Project, repo: Path) -> None:
    """Fetch the remote's branches into the canonical's `refs/remotes/origin/*`
    -- every branch for a project that refreshes its mirror, else the default
    branch, as ``sync_adopted`` does -- under a lock the caller already
    holds. Never raises: a failed fetch leaves the older view, which can only
    count fewer commits as the remote's, so refuse more, never less.

    `main` is then fast-forwarded to the remote's default branch, as
    ``sync_adopted`` does at setup, so a broker still running the
    `base..tip` check of before 2026-10-20 reads the same range."""
    base = project.record.get("default_branch") or MAIN
    try:
        if refreshes_mirror(project):
            refresh_mirror(repo, project.remote)
        else:
            git(repo, "fetch", "--quiet", "--no-tags", "origin",
                f"+refs/heads/{base}:refs/remotes/origin/{base}", check=False,
                timeout=180)
        local, remote = rev(repo, MAIN), rev(repo, f"origin/{base}")
        if local and remote and local != remote and is_ancestor(repo, local, remote):
            git(repo, "merge", "--quiet", "--ff-only", f"origin/{base}", check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def _land_pr_locked(project, repo, tree, branch, keep_tree, result, say,
                    record, title, summary, target_pr=None, reply="",
                    cancelled=None) -> None:
    """A pull-request project's landing. See the module docstring. With
    ``target_pr`` (the brief's title names one, lib/loam_pr_target.py) the
    branch updates that pull request instead of opening one, and ``reply``
    is posted on it; the job's own description is not asked for."""
    refresh_main(repo, tree)
    main_sha, tip = rev(repo, MAIN), branch_tip(tree, branch)
    result["head"] = head_note(tree, branch)
    if is_ancestor(tree, tip, main_sha):
        result["memory"] = merge_memory_back(project, record)
        if not keep_tree:
            remove_tree(project, record, delete_branch=True)
        result.update(state="nothing", sha=main_sha)
        return
    harness = harness_in_commits(tree, main_sha, tip)
    if harness:
        result.update(reason="harness", files=harness)
        return
    onto_branch(tree, branch, result)
    identity = loam_commit_identity.of(project.record)
    if identity:
        # A person's repository: every commit the push adds is theirs alone,
        # and the pull request is written for its readers
        # (lib/loam_commit_identity.py). Read in the canonical checkout, whose
        # view of the remote the job cannot write, freshly fetched so that
        # what the remote gained during the job counts as the remote's.
        # Checked again by the broker right before it pushes.
        say("landing: reading what the remote already has")
        refresh_remote_view(project, repo)
        git(repo, "fetch", "--quiet", "--no-tags", str(tree),
            f"+refs/heads/{branch}:refs/heads/{branch}")
        wrong = loam_commit_identity.problems(repo, main_sha, tip, identity)
        if wrong:
            result.update(reason="identity", files=wrong)
            return
        if target_pr is None and not (summary or "").strip():
            result.update(reason="pr_body", files=[loam_commit_identity.PR_BODY_FILE])
            return
        said = loam_commit_identity.text_problems(
            reply if target_pr is not None else f"{title}\n{summary}")
        if said:
            result.update(reason="pr_text", files=said)
            return
    say("landing: testing the branch")
    with timed(result, "branch_tests"):
        passed, command, out = run_tests(project, tree)
    result["test"] = {"command": command, "passed": passed, "tail": out,
                      "tree": "branch"}
    if passed is False:
        result.update(reason="branch_tests")
        return
    untested = passed is None
    body = (summary or "").strip() or f"`{branch}`, from an engineer job."
    if untested and loam_ci.enabled(project):
        # GitHub's checks are this project's test (lib/loam_ci.py): they show
        # on the pull request itself, and the receipt reads them after the
        # push. "Untested" would be false.
        pass
    elif identity:
        # Plain prose, the author's own: how it was tested goes last.
        body += ("\n\n**Untested.**" if untested else
                 f"\n\nTested with `{command}`.")
    elif untested:
        body = ("**Untested.** This project has no test command registered "
                "with the engineer yet, so nothing was run before this pull "
                "request was opened.\n\n" + body)
    else:
        body = f"Tested with `{command}` on the branch.\n\n" + body
    git(repo, "fetch", "--quiet", "--no-tags", str(tree),
        f"+refs/heads/{branch}:refs/heads/{branch}")
    if stop_for_cancel(cancelled, result, say):
        return
    if target_pr is not None:
        say(f"landing: asking the broker to fast-forward pull request #{target_pr}")
    else:
        say("landing: asking the broker to push the branch and open the pull request")
    try:
        with timed(result, "push"):
            pr = (update_pr(project, branch, target_pr, reply or "")
                  if target_pr is not None else open_pr(project, branch, title, body))
    except Exception as exc:  # noqa: BLE001 -- the broker's words, reported
        result.update(reason="pull_request", detail=str(exc)[:600],
                      target_pr=target_pr)
        return
    if pr.get("refused"):
        result.update(reason=pr["refused"], detail=pr.get("detail"),
                      pull_request=pr, target_pr=target_pr)
        return
    result.update(state="pull_request", sha=tip, pull_request=pr, untested=untested,
                  pushed=True)
    if loam_ci.enabled(project):
        # Read by the caller once it holds nothing (bin/engineer-job
        # ``report_checks``); None until then, and for a mid-job `--land`.
        result["ci"] = None
    result["memory"] = merge_memory_back(project, record)
    if keep_tree and result["memory"]:
        shutil.rmtree(memory_base(record), ignore_errors=True)
        shutil.copytree(tree / "memory", memory_base(record), symlinks=True)
    if not keep_tree:
        # The branch stays in the canonical repository: a continuation
        # reopens it, and its next landing updates the same pull request.
        remove_tree(project, record, delete_branch=False)


def land(project: eng.Project, record: dict, message: str, *,
         keep_tree: bool = False, say=None, release: str | None = None,
         title: str | None = None, summary: str = "",
         target_pr: int | None = None, reply: str = "",
         cancelled=None) -> dict:
    """Land the job's branch on `main`. Always returns a result, never raises:

        state    landed | nothing | not_landed
                 (read_only is `read_only_result`'s, never this one's)
        reason   uncommitted | branch_tests | conflict | merged_tests |
                 no_tests | canonical | diverged | error | cancelled
                 (not_landed only)
        sha      the commit `main` now points at (landed)
        files    the uncommitted or conflicting paths
        test     {"command", "passed", "tail"} of the run that decided it
        pushed   True, or the push's error
        memory   untracked memory files merged back
        head     where HEAD was when it was not the branch, or None;
                 the branch ref is what lands, never HEAD
        release  what became of `release`, the tag the job asked for
                 (`cut_release`); absent when it asked for none

    `keep_tree` is the mid-job form (`engineer-job --land`): the session is
    still running in the tree, so it stays, and its branch is fast-forwarded
    to what landed so the next landing starts from there.

    `cancelled`, a callable (since 2026-11-10: the job's `cancelled` marker
    exists), is asked once the live lock is held and again immediately
    before `main` moves or the branch is pushed; true either time, nothing
    lands, the tree and branch are kept, and the reason is `cancelled`.
    """
    say = say or (lambda _text: None)
    repo, tree, branch = canonical(project), Path(record["path"]), record["branch"]
    result = {"state": "not_landed", "reason": None, "sha": None, "files": [],
              "test": None, "pushed": None, "memory": [], "branch": branch,
              "tree": str(tree)}
    try:
        if not tree.is_dir():
            result.update(reason="error", detail=f"the tree {tree} is gone")
            return result
        dirty = dirty_files(tree)
        if dirty:
            result.update(reason="uncommitted", files=dirty)
            return result

        def on_wait(name, holder):
            say(f"waiting for the {name} live lock, held by "
                f"{loam_lock.describe(holder)}")

        wall0, cpu0 = time.monotonic(), cpu_seconds()
        with loam_lock.live(project.name, f"land {branch}", on_wait=on_wait):
            result.setdefault("timing", {})["lock_wait"] = {
                "wall_s": round(time.monotonic() - wall0, 2),
                "cpu_s": round(cpu_seconds() - cpu0, 2)}
            say("landing: holding the live lock")
            if stop_for_cancel(cancelled, result, say):
                return result
            if project.pull_request:
                _land_pr_locked(project, repo, tree, branch, keep_tree, result,
                                say, record, title or message, summary,
                                target_pr, reply, cancelled)
            else:
                _land_locked(project, repo, tree, branch, message, keep_tree,
                             result, say, record, cancelled)
            if release:
                say(f"landing: release tag {release}")
                try:
                    result["release"] = cut_release(repo, result, release)
                except (WorktreeError, OSError,
                        subprocess.SubprocessError) as exc:
                    # Not the landing's failure: `main` has already moved.
                    result["release"] = {"tag": release, "state": "failed",
                                         "sha": result.get("sha"),
                                         "detail": str(exc)[:400]}
            return result
    except (WorktreeError, OSError, subprocess.SubprocessError,
            loam_lock.Busy) as exc:
        result.update(state="not_landed", reason="error", detail=str(exc))
        return result
    finally:
        # Every way out that did not reach cut_release still answers the job
        # that asked for a tag.
        if release and "release" not in result:
            result["release"] = release_refused(
                release, f"the branch did not land ({result.get('reason')})")


# ---------------------------------------------------------------------------
# A release tag a job asks for (since 2026-10-02). A job's clone cannot push
# a tag -- nothing in it reaches origin but the branch the landing merges --
# so a release that a tag push cuts (Spoken's `release.yml`) had no route
# from a job. The job writes the tag alone into `release-tag` in its job
# directory; the landing tags the commit it landed and pushes the tag, and
# only once the merge, the tests and the push of `main` have all happened.
# ---------------------------------------------------------------------------

RELEASE_FILE = "release-tag"

# Where a landed tree states its version. The tag must equal each of these
# that exists and carries one; a tree with neither takes any valid tag.
VERSION_FILES = ("manifest.json", "package.json")


def read_release(job_dir: Path) -> str | None:
    """The tag a job asked for, or None. The file's first non-blank line."""
    try:
        text = (job_dir / RELEASE_FILE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[0] if lines else None


def release_refused(tag: str, why: str, sha: str | None = None) -> dict:
    return {"tag": tag, "state": "refused", "sha": sha, "detail": why}


def tree_versions(repo: Path, sha: str) -> dict[str, str]:
    """`{file: version}` for each of VERSION_FILES at `sha` that has one."""
    found = {}
    for name in VERSION_FILES:
        out = git(repo, "show", f"{sha}:{name}", check=False)
        if out.returncode != 0:
            continue
        try:
            version = json.loads(out.stdout).get("version")
        except (ValueError, AttributeError):
            continue
        if isinstance(version, str):
            found[name] = version
    return found


def cut_release(repo: Path, result: dict, tag: str) -> dict:
    """Tag what landed as `tag` and push the tag, or say why not. Never
    raises and never touches `main`: a refused tag leaves the merge landed.

        state   pushed | refused | failed
        sha     the commit tagged (or that would have been)
        detail  why, when not pushed
    """
    sha = result.get("sha")
    state = result.get("state")
    if state not in ("landed", "landed_earlier") or not sha:
        return release_refused(tag, f"the branch did not land ({state})")
    if git(repo, "check-ref-format", f"refs/tags/{tag}",
           check=False).returncode != 0:
        return release_refused(tag, f"{tag!r} is not a valid tag name", sha)
    if not git(repo, "remote", check=False).stdout.split():
        return release_refused(tag, "the repository has no remote to push "
                                    "a tag to", sha)
    if state == "landed" and result.get("pushed") is not True:
        return release_refused(tag, f"`{MAIN}` was not pushed", sha)
    if state == "landed_earlier":
        # Landed by a mid-job `--land`, which pushed then; origin says so.
        git(repo, "fetch", "--quiet", "--no-tags", "origin", MAIN, check=False,
            timeout=120)
        remote = rev(repo, f"origin/{MAIN}")
        if not remote or not is_ancestor(repo, sha, remote):
            return release_refused(tag, f"origin's `{MAIN}` does not have "
                                        f"{sha[:7]}", sha)
    versions = tree_versions(repo, sha)
    wrong = {name: v for name, v in versions.items() if v != tag}
    if wrong:
        said = ", ".join(f"{name} says {v}" for name, v in wrong.items())
        return release_refused(tag, f"the landed tree's version is not "
                                    f"{tag}: {said}", sha)
    local = rev(repo, f"refs/tags/{tag}")
    if local and local != sha:
        return release_refused(tag, f"the tag already exists here, at "
                                    f"{local[:7]}", sha)
    remote_tag = git(repo, "ls-remote", "origin", f"refs/tags/{tag}",
                     check=False, timeout=120).stdout.split()
    if remote_tag and remote_tag[0] != sha:
        # An annotated tag's ref names the tag object; peel it before calling
        # it a different commit.
        peeled = git(repo, "ls-remote", "origin", f"refs/tags/{tag}^{{}}",
                     check=False, timeout=120).stdout.split()
        if (peeled[0] if peeled else remote_tag[0]) != sha:
            return release_refused(tag, "the tag already exists on origin, "
                                        "at another commit", sha)
    made = False
    if not local:
        git(repo, "tag", tag, sha)
        made = True
    push = git(repo, "push", "--quiet", "origin", f"refs/tags/{tag}",
               check=False, timeout=180)
    if push.returncode != 0:
        if made:
            git(repo, "tag", "-d", tag, check=False)
        return {"tag": tag, "state": "failed", "sha": sha,
                "detail": (push.stderr or push.stdout).strip()[:400]}
    return {"tag": tag, "state": "pushed", "sha": sha,
            "detail": ("matches " + ", ".join(sorted(versions))
                       if versions else "no version file in the tree")}


def release_section(release: dict | None) -> str:
    """The receipt's sentence on a release tag, or "" when none was asked."""
    if not release:
        return ""
    tag, sha = release["tag"], (release.get("sha") or "")[:7]
    if release["state"] == "pushed":
        return (f" **Release tag `{tag}` pushed** at `{sha}` "
                f"({release.get('detail')}).")
    word = "refused" if release["state"] == "refused" else "failed to push"
    return (f" **Release tag `{tag}` {word}**: {release.get('detail')}. "
            f"No tag was pushed; the landing above is unaffected.")


def cpu_seconds() -> float:
    """CPU this process and its finished children have used, user + system.
    A landing's tests and git calls are children, so the difference across a
    step is that step's CPU."""
    own, kids = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(
        resource.RUSAGE_CHILDREN)
    return own.ru_utime + own.ru_stime + kids.ru_utime + kids.ru_stime


@contextmanager
def timed(result: dict, step: str):
    """Wall and CPU seconds for one landing step, into result["timing"]."""
    wall0, cpu0 = time.monotonic(), cpu_seconds()
    try:
        yield
    finally:
        timing = result.setdefault("timing", {})
        timing[step] = {"wall_s": round(time.monotonic() - wall0, 2),
                        "cpu_s": round(cpu_seconds() - cpu0, 2)}


TIMED_STEPS = ("lock_wait", "branch_tests", "merge", "merge_tests", "push")


def timing_cell(result: dict | None) -> str:
    """The receipt's landing-telemetry cell."""
    timing = (result or {}).get("timing") or {}
    parts = [f"{step.replace('_', ' ')} {timing[step]['wall_s']:.1f} s "
             f"({timing[step]['cpu_s']:.1f} s CPU)"
             for step in TIMED_STEPS if step in timing]
    return "; ".join(parts) if parts else "—"


def stop_for_cancel(cancelled, result: dict, say) -> bool:
    """True, with the result marked, when the job was cancelled: the
    landing's check, read at the last moment before anything moves."""
    if cancelled is None or not cancelled():
        return False
    say("landing: the job is cancelled; nothing lands")
    result.update(state="not_landed", reason="cancelled",
                  detail="the job was cancelled; its landing stopped before "
                         "anything moved")
    return True


def _land_locked(project, repo, tree, branch, message, keep_tree, result,
                 say, record, cancelled=None) -> dict:
    if (git(repo, "symbolic-ref", "--short", "HEAD", check=False).stdout.strip()
            != MAIN):
        result.update(reason="canonical", detail=(
            f"the canonical checkout {repo} is not on {MAIN}"))
        return result
    diverged = sync_with_origin(repo)
    if diverged:
        result.update(reason="diverged", detail=diverged)
        return result
    refresh_main(repo, tree)
    main_sha, tip = rev(repo, MAIN), branch_tip(tree, branch)
    result["head"] = head_note(tree, branch)

    # Ancestry is asked of the tree: a clone has both commits, the canonical
    # repository does not have the branch's until the landing fetches it.
    if is_ancestor(tree, tip, main_sha):
        # Nothing on the branch that main does not have -- either the job
        # committed nothing, or what it committed is already on main because
        # it landed earlier (a mid-job `--land`, or an earlier job of this
        # tree). The two read differently, and the second is a landing.
        earlier = landed_earlier(project, record)
        result["memory"] = merge_memory_back(project, record)
        if not keep_tree:
            remove_tree(project, record, delete_branch=True)
        if earlier:
            result.update(state="landed_earlier", sha=earlier)
        else:
            result.update(state="nothing", sha=main_sha)
        return result

    onto_branch(tree, branch, result)
    say("landing: testing the branch")
    with timed(result, "branch_tests"):
        passed, command, out = run_tests(project, tree)
    result["test"] = {"command": command, "passed": passed, "tail": out,
                      "tree": "branch"}
    if passed is None:
        result.update(reason="no_tests")
        return result
    if not passed:
        result.update(reason="branch_tests")
        return result

    if is_ancestor(tree, main_sha, tip):
        merged = tip                     # a fast-forward: the tree is the merge
    else:
        say("landing: merging main")
        with timed(result, "merge"):
            git(tree, "checkout", "--quiet", "--detach", main_sha)
            out = git(tree, "merge", "--no-ff", "--no-edit", "-m", message,
                      branch, check=False)
        if out.returncode != 0:
            conflicts = git(tree, "diff", "--name-only", "--diff-filter=U",
                            check=False).stdout.split()
            git(tree, "merge", "--abort", check=False)
            git(tree, "checkout", "--quiet", "-f", branch, check=False)
            # A merge that failed with nothing unmerged is not a conflict
            # (caused: a repository with no committer identity), and calling
            # it one would send a continuation to resolve nothing.
            result.update(reason="conflict" if conflicts else "error",
                          files=conflicts,
                          detail=(out.stdout + out.stderr).strip()[-800:],
                          main=main_sha)
            return result
        merged = rev(tree, "HEAD")
        say("landing: testing the merge")
        with timed(result, "merge_tests"):
            passed, command, out_text = run_tests(project, tree)
        result["test"] = {"command": command, "passed": passed,
                          "tail": out_text, "tree": "merge"}
        if not passed:
            git(tree, "checkout", "--quiet", "-f", branch, check=False)
            result.update(reason="merged_tests", main=main_sha)
            return result
        git(tree, "checkout", "--quiet", branch)
        git(tree, "merge", "--quiet", "--ff-only", merged)

    if stop_for_cancel(cancelled, result, say):
        # The tree may be on the merge; the branch is what is kept.
        git(tree, "checkout", "--quiet", "-f", branch, check=False)
        return result
    if is_clone(tree):
        # The one write into the canonical repository a job's work causes:
        # its objects, fetched by the landing, which holds the live lock. No
        # ref is made for it but FETCH_HEAD; `main` moves only below.
        git(repo, "fetch", "--quiet", "--no-tags", str(tree),
            f"refs/heads/{branch}")
    out = git(repo, "merge", "--quiet", "--ff-only", merged, check=False)
    if out.returncode != 0:
        result.update(reason="canonical", detail=(
            f"the canonical checkout refused to fast-forward {MAIN} to "
            f"{merged[:7]}: {(out.stderr or out.stdout).strip()[:400]}"))
        return result
    result.update(state="landed", sha=merged)
    result["memory"] = merge_memory_back(project, record)
    if keep_tree and result["memory"]:
        # The tree goes on; what it has now is already in the canonical copy.
        shutil.rmtree(memory_base(record), ignore_errors=True)
        shutil.copytree(tree / "memory", memory_base(record), symlinks=True)
    if git(repo, "remote", check=False).stdout.split():
        with timed(result, "push"):
            push = git(repo, "push", "--quiet", "origin", MAIN, check=False,
                       timeout=180)
        result["pushed"] = True if push.returncode == 0 else (
            (push.stderr or push.stdout).strip()[:400])
    if not keep_tree:
        remove_tree(project, record, delete_branch=True)
    return result


def read_only_result(project: eng.Project, record: dict, *,
                     keep_tree: bool = False) -> dict:
    """A read-only brief's landing (since 2026-10-13): nothing is merged,
    pushed or proposed, whatever the tree holds; the tree and its branch are
    removed (kept for a mid-job `--land`, whose session is still in it). The
    untracked memory is still merged back -- it is the engineer's, not the
    repository's. What the branch held that `main` lacks is counted, so the
    receipt says what was discarded."""
    repo, tree, branch = canonical(project), Path(record["path"]), record["branch"]
    result = {"state": "read_only", "reason": None, "sha": None, "files": [],
              "test": None, "pushed": None, "memory": [], "branch": branch,
              "tree": str(tree)}
    try:
        if tree.is_dir():
            main_sha, tip = rev(repo, MAIN), branch_tip(tree, branch)
            if tip and main_sha:
                result["unlanded"] = int(git(
                    tree, "rev-list", "--count", f"{main_sha}..{tip}",
                    check=False).stdout.strip() or 0)
            result["head"] = head_note(tree, branch)
            result["dirty"] = len(dirty_files(tree))
            result["memory"] = merge_memory_back(project, record)
        if not keep_tree:
            remove_tree(project, record, delete_branch=True)
    except (WorktreeError, OSError, subprocess.SubprocessError) as exc:
        result["detail"] = str(exc)[:400]
    return result


# ---------------------------------------------------------------------------
# What a receipt says about it.
# ---------------------------------------------------------------------------

REASONS = {
    "uncommitted": "the tree has uncommitted changes, and only commits land",
    "branch_tests": "the tests fail on the branch itself, so it was not merged",
    "conflict": "merging it into main conflicts",
    "merged_tests": "it merges cleanly, but the tests fail on the merged tree",
    "no_tests": "the project has no test command registered "
                "(`test_command` in agents/projects/<name>.json), and nothing "
                "lands untested",
    "canonical": "the canonical checkout would not take it",
    "diverged": "the canonical main and its remote have diverged",
    "error": "a step of the landing failed",
    "cancelled": "the job was cancelled",
    "harness": "its commits touch the engineer's harness, which never enters "
               "this repository's history",
    "pull_request": "the broker could not push the branch or open the pull request",
    "not_fast_forward": "the pull request it updates has a head that is not an "
                        "ancestor of this branch, and nothing here force-pushes",
    "open_pr_diverged": "its earlier pull request is still open and this branch "
                        "no longer fast-forwards its head, and an open pull "
                        "request is neither force-pushed nor replaced",
    "identity": "its commits are not the project owner's alone (author, committer "
                "or message), and only theirs are pushed to this repository",
    "pr_body": "the job wrote no pull request description (`pull-request.md` in "
               "its job directory), and this project's pull requests never carry "
               "the receipt",
    "pr_text": "the pull request's title or description names the machinery behind it",
    "incomplete": "the job did not complete, and half of a change does not land",
    "interrupted": "the job's process died mid-run",
}


def continuation_brief(project: eng.Project, job_id: str, result: dict) -> str:
    """The brief that finishes what this landing could not. Sent with
    `continue_job: <job_id>` it reopens the same tree and session."""
    branch, tree = result["branch"], result["tree"]
    files = ", ".join(f"`{f}`" for f in result.get("files") or []) or "—"
    steps = {
        "conflict": (f"Your branch `{branch}` did not land: merging it into "
                     f"`main` conflicts in {files}. In your tree, run `git merge "
                     f"main`, resolve those files so both changes survive, run "
                     f"the tests, and commit the merge. Do not push and do not "
                     f"touch `main`; the launcher lands the branch when you "
                     f"finish."),
        "uncommitted": (f"Your branch `{branch}` did not land: the tree has "
                        f"uncommitted changes in {files}. Commit what belongs "
                        f"in the change and remove what does not; the launcher "
                        f"lands the branch when you finish."),
        "branch_tests": (f"Your branch `{branch}` did not land: the tests fail "
                         f"on it. Make them pass on the branch and commit; the "
                         f"launcher lands it when you finish."),
        "incomplete": (f"Your branch `{branch}` was not landed because the job "
                       f"did not complete ({result.get('detail') or '?'}). "
                       f"Finish the work, commit it, and write your receipt; "
                       f"the launcher lands the branch when you finish."),
        "interrupted": (f"Your job's process died mid-run, before it finished "
                        f"or landed `{branch}` (a crash or a kill, recovered "
                        f"by the recovery sweep). Before doing anything else, "
                        f"check what you had already done -- commits on the "
                        f"branch, anything outside the tree -- and do NOT "
                        f"repeat a deploy, a provision or any other external "
                        f"step that may already have happened; verify it "
                        f"instead. Then finish the work, commit it, and write "
                        f"your receipt; the launcher lands the branch when "
                        f"you finish."),
        "identity": (f"Your branch `{branch}` was not pushed: its commits must "
                     f"be the project owner's alone, as your tree's git config "
                     f"already sets, with nothing in a message naming the "
                     f"model, the runtime, a job id or a co-author. What was "
                     f"wrong: {files}. Rewrite those commits on the branch (`git rebase -i` is unavailable; "
                     f"`git reset --soft` and recommit, or `git commit --amend "
                     f"--reset-author`) and do not push; the launcher lands "
                     f"it when you finish."),
        "pr_body": (f"Your branch `{branch}` was not pushed: this project's "
                    f"pull request is described by `pull-request.md` in your "
                    f"job directory, which you did not write. Write it -- a "
                    f"`# ` title line, then what changed, why, and how it was "
                    f"tested, for the repository's own readers -- and the "
                    f"launcher lands the branch when you finish."),
        "pr_text": (f"Your branch `{branch}` was not pushed: the pull "
                    f"request's title or `pull-request.md` names the machinery "
                    f"behind it ({files}). Reword it for the repository's own "
                    f"readers; the launcher lands the branch when you finish."),
        "not_fast_forward": (
            f"Your branch `{branch}` was not pushed: the pull request it "
            f"updates has moved to a head your branch does not contain "
            f"({result.get('detail') or '?'}). In your tree, `git fetch origin "
            f"pull/{result.get('target_pr')}/head`, merge `FETCH_HEAD` into "
            f"your branch so both survive, run the tests, and commit; the "
            f"launcher pushes it to the same pull request when you finish. "
            f"(Title this brief `Updates #{result.get('target_pr')}: ...`; "
            f"without it the landing opens a new pull request.)"),
        "open_pr_diverged": (
            f"Your branch `{branch}` was not pushed: {result.get('detail') or '?'}. "
            f"If the old pull request should carry this work, merge or rebase "
            f"onto its head in your tree so the branch contains it, and "
            f"commit; the launcher updates the same pull request when you "
            f"finish. If it should be replaced, that is a call for its owners: "
            f"once it is closed, the next landing opens a fresh branch."),
        "merged_tests": (f"Your branch `{branch}` did not land: it merges into "
                         f"`main` cleanly but the tests fail on the result. "
                         f"Run `git merge main` in your tree, make the tests "
                         f"pass, and commit; the launcher lands it when you "
                         f"finish."),
    }
    body = steps.get(result.get("reason"), (
        f"Your branch `{branch}` did not land: "
        f"{REASONS.get(result.get('reason'), 'unknown')}. "
        f"{result.get('detail') or ''} Find out why, fix what is yours to fix, "
        f"and say what is not."))
    return (f"project: {project.name}\ncontinue_job: {job_id}\n\n"
            f"{body} Your tree is `{tree}`.")


def tested_clause(result: dict, test: dict) -> str:
    """How a pull request's branch was tested: GitHub's checks on a project
    whose landing reads them (lib/loam_ci.py), else the launcher's own run,
    else "untested"."""
    if "ci" in result:
        return loam_ci.text(result["ci"])
    if result.get("untested"):
        return "**untested** (no `test_command` registered)"
    return f"tested with `{test.get('command')}` on the branch"


def update_section(result: dict) -> str:
    """The landing's paragraph for a job that updated an open pull request
    (lib/loam_pr_target.py): the pull request, its head branch before and
    after, and what became of the reply."""
    pr, test = result.get("pull_request") or {}, result.get("test") or {}
    tested = tested_clause(result, test)
    before, after = (pr.get("before") or "")[:10], (pr.get("after") or "")[:10]
    moved = ("which was already there, so nothing moved" if before == after
             else f"fast-forwarded from `{before}` to `{after}`")
    text = (f"**Landing: pull request {pr.get('url')} updated** — `{result['branch']}` "
            f"pushed to its head branch `{pr.get('head')}`, {moved}; {tested}. "
            f"No pull request was opened, nothing was force-pushed, and "
            f"`{pr.get('base')}` was not pushed.")
    reply = pr.get("reply") or {}
    if reply.get("state") == "posted":
        text += f" Reply posted on it: {reply.get('url')}."
    elif reply.get("state") == "refused":
        text += (f" **The reply was not posted**: {reply.get('detail')}. Nothing "
                 f"was posted another way. To post one, grant {reply.get('grant')}; "
                 f"widening an app is {lc.OWNER_NAME}'s click on GitHub.")
    else:
        text += (f" No reply: the job wrote no `{_reply_file()}`.")
    return text


def replaces_clause(pr: dict) -> str:
    """Which way a continuation's pull request went
    (lib/loam_github_app.settle_head): a fresh branch beside an old one it
    could not update, said with why; "" when it used the head it had."""
    old = pr.get("replaces") or {}
    if not old.get("head"):
        return ""
    of = (f"pull request #{old['number']}'s branch, {old['state']}"
          if old.get("number") else "the branch an earlier landing pushed")
    why = ("this branch was rebuilt and no longer fast-forwards it"
           if old.get("why") == "rebuilt" else
           f"its pull request is {old.get('state')}")
    left = (f"It was left as it was, at `{old['head_sha'][:10]}`"
            if old.get("head_sha") else "Its branch had already been deleted")
    return (f" **A fresh branch and a new pull request:** the earlier head "
            f"`{old['head']}` ({of}) was not reused because {why}. {left}; "
            f"nothing was force-pushed or deleted.")


def _reply_file() -> str:
    import loam_pr_target  # noqa: PLC0415
    return loam_pr_target.REPLY_FILE


def receipt_section(project: eng.Project, job_id: str, result: dict | None) -> str:
    """The launcher's paragraph on the landing, for the receipt."""
    if result is None:
        return "**Landing.** None attempted."
    state, branch = result["state"], result["branch"]
    test = result.get("test") or {}
    tested = (f" Tested with `{test['command']}` on the "
              f"{'merged tree' if test.get('tree') == 'merge' else 'branch'}, "
              f"which is the merge when `main` had not moved."
              if test.get("command") and test.get("passed") else "")
    if state == "landed":
        pushed = result.get("pushed")
        push = ("Pushed to origin." if pushed is True else
                "No remote to push to." if pushed is None else
                f"**The push failed**: `{pushed}` — `main` moved on this box "
                f"and origin is behind it.")
        memory = (f" Memory merged back: {', '.join(result['memory'])}."
                  if result.get("memory") else "")
        kept = ("The worktree stays: the session is still in it, and its "
                "branch now starts from what landed." if Path(result["tree"]).is_dir()
                else "The worktree is removed.")
        head = (f" Its HEAD was {result['head']} and was put back on the "
                f"branch; the branch ref is what landed."
                if result.get("head") else "")
        return (f"**Landing: landed at `{result['sha'][:7]}`** — `{branch}` "
                f"merged into `main`.{head}{tested} {push} {kept}{memory}"
                + release_section(result.get("release")))
    if state == "landed_earlier":
        where = ("The worktree stays." if Path(result["tree"]).is_dir()
                 else "The worktree is removed.")
        text = (f"**Landing: landed at `{result['sha'][:7]}` earlier, by "
                f"`--land`** (or by an earlier job in this tree) — everything "
                f"on `{branch}` is already on `main`, so this landing had "
                f"nothing left to merge or push. {where}")
        if result.get("incomplete"):
            text += (f" The job itself {result.get('detail') or 'did not complete'}; "
                     f"its work had already landed, so no continuation is "
                     f"offered.")
        return text + release_section(result.get("release"))
    if state == "pull_request" and (result.get("pull_request") or {}).get("updated"):
        return update_section(result) + release_section(result.get("release"))
    if state == "pull_request":
        pr = result.get("pull_request") or {}
        tested = tested_clause(result, test)
        return (f"**Landing: pull request {pr.get('url')}** "
                f"({'opened' if pr.get('created') else 'updated'}) — `{branch}` "
                f"at `{(result.get('sha') or '')[:7]}` pushed to the remote"
                + (f" as `{pr['head']}`" if pr.get("head") not in (None, branch) else "")
                + f" and "
                f"proposed against `{pr.get('base')}`, {tested}. Nothing was "
                f"merged and `{pr.get('base')}` was not pushed: this project "
                f"lands by pull request, and its owners merge."
                + replaces_clause(pr)
                + release_section(result.get("release")))
    head = (f" Its HEAD was {result['head']}; the branch ref is what lands."
            if result.get("head") else "")
    if state == "nothing":
        return (f"**Landing: nothing to land** — `{branch}` had no commits "
                f"that `main` lacks.{head} The worktree is removed."
                + release_section(result.get("release")))
    if state == "read_only":
        held = []
        if result.get("unlanded"):
            held.append(f"{result['unlanded']} commit(s) `main` lacks")
        if result.get("dirty"):
            held.append(f"{result['dirty']} uncommitted path(s)")
        where = ("The worktree stays: the session is still in it."
                 if Path(result["tree"]).is_dir() else
                 "The worktree and its branch are removed.")
        return ("**Landing: read-only brief: nothing landed** — nothing was "
                "merged or pushed and no pull request was opened."
                + (f" The tree held {', '.join(held)}, discarded."
                   if held else "")
                + (f" Its HEAD was {result['head']}." if result.get("head") else "")
                + f" {where}"
                + (f" Memory merged back: {', '.join(result['memory'])}."
                   if result.get("memory") else "")
                + (f" Detail: `{result['detail']}`" if result.get("detail") else "")
                + release_section(result.get("release")))
    if result.get("reason") == "cancelled":
        # No continuation is offered: the cancel is someone's decision.
        return (f"**Landing: not landed** — the job was cancelled"
                + (" before its landing began" if result.get("before_landing")
                   else ", and its landing stopped before anything moved")
                + f". Nothing was pushed; `main` is where it was. The branch "
                f"`{branch}` and its worktree `{result['tree']}` are kept."
                + release_section(result.get("release")))
    reason = REASONS.get(result.get("reason"), result.get("reason") or "?")
    parts = [f"**Landing: not landed** — {reason}. Nothing was pushed; "
             f"`main` is where it was. The branch `{branch}` and its worktree "
             f"`{result['tree']}` are kept."
             + release_section(result.get("release"))]
    if result.get("files"):
        parts.append("Files: " + ", ".join(f"`{f}`" for f in result["files"]) + ".")
    if result.get("detail"):
        parts.append(f"Detail: `{result['detail'][:600]}`")
    if result.get("target_pr") is not None:
        pr = result.get("pull_request") or {}
        parts.append(f"The pull request this job updates: #{result['target_pr']}"
                     + (f" ({pr['url']})" if pr.get("url") else "")
                     + (f", its head branch `{pr['head']}` still at "
                        f"`{(pr.get('before') or '')[:10]}`" if pr.get("head") else "")
                     + ". Nothing was pushed to it and no reply was posted.")
    if test.get("command") and test.get("passed") is False:
        parts.append(f"Failing run of `{test['command']}` on the "
                     f"{'merged tree' if test.get('tree') == 'merge' else 'branch'}:"
                     f"\n\n```\n{test.get('tail', '')}\n```")
    parts.append("The continuation that resolves it — send it as a brief to "
                 f"{project.name} with `continue_job: {job_id}`:\n\n"
                 + "\n".join("> " + line if line else ">"
                             for line in continuation_brief(
                                 project, job_id, result).splitlines()))
    return "\n\n".join(parts)
