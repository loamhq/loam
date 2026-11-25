"""Prepare isolated job clones and land their branch tips. See CONTRIBUTING.md.

A job runs outside the canonical checkout so its harness loads only the job's
instructions. The canonical checkout owns the project's refs and remotes.
A failed landing keeps the clone for recovery."""

from __future__ import annotations

import ipaddress
import json
import os
import re
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
import loam_git_identity
import loam_lock
import loam_marketplace_pin
import loam_offline
import loam_release_notes
import loam_signing

WORKTREES = lc.STATE / "worktrees"
MAIN = "main"
RECORD = "worktree.json"

# What bin/provision puts in a checkout that may not be in the repository.
HARNESS = ("CLAUDE.md", "role.md", "project.md", ".claude", "memory")

TEST_TIMEOUT = 900

# How much of a failing test run a receipt quotes.
TAIL_LINES = 40


class WorktreeError(Exception):
    """A git step this module needed did not happen."""


def canonical(project: eng.Project) -> Path:
    """The platform engineer's root is the checkout's engineer/ subdirectory. Other project roots are checkouts."""
    return project.root.parent if project.platform else project.root


def branch_for(job_id: str) -> str:
    return f"job/{job_id}"


def path_for(project: eng.Project, job_id: str) -> Path:
    return WORKTREES / project.name / job_id


def session_dir(project: eng.Project, tree: Path) -> Path:
    """Return the job's working directory within the clone."""
    if project.platform:
        return tree / "engineer"
    return tree / project.subdir if project.subdir else tree


def memory_rel(project: eng.Project) -> str:
    if project.platform:
        return "engineer/memory/MEMORY.md"
    return f"{project.subdir}/memory/MEMORY.md" if project.subdir else "memory/MEMORY.md"


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
    # Forced text diffs can contain binary bytes. Keep scanning beyond them.
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        errors="replace", check=False, timeout=timeout)
    if check and result.returncode != 0:
        raise WorktreeError(
            f"`git {' '.join(args)}` in {repo} exited {result.returncode}: "
            f"{(result.stderr or result.stdout).strip()[:500]}")
    return result


def rev(repo: Path, ref: str) -> str | None:
    out = git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}",
              check=False)
    return out.stdout.strip() or None


# The backup remote receives commits through its push refspec. Only origin supplies releases.
BACKUP = "backup"
# The last `main` the backup push took, so the recovery sweep can tell
# without the network whether one is pending. Local only; nothing fetches it.
BACKUP_MARK = "refs/loam/backup-pushed"


def has_remote(repo: Path, name: str = "origin") -> bool:
    return git(repo, "remote", "get-url", name, check=False).returncode == 0


def push_backup(repo: Path) -> bool | str | None:
    """Push `main` to the `backup` remote: True, git's words on a failure, or
    None when there is no such remote. Tags and other branches are not pushed."""
    if not has_remote(repo, BACKUP):
        return None
    sha = rev(repo, MAIN)
    out = git(repo, "push", "--quiet", BACKUP, MAIN, check=False, timeout=180)
    if out.returncode != 0:
        return (out.stderr or out.stdout).strip()[:400] or f"exit {out.returncode}"
    if sha:
        git(repo, "update-ref", BACKUP_MARK, sha, check=False)
    return True


def backup_pending(repo: Path) -> bool:
    """Whether `main` has moved past the last backup push (or none was made)."""
    return has_remote(repo, BACKUP) and rev(repo, MAIN) != rev(repo, BACKUP_MARK)


# Preflight outgoing changes before merging. Recheck every unpublished commit before push.
GATE_COMMITS = 200


def identity(repo: Path) -> tuple[str, str]:
    return (git(repo, "config", "user.name", check=False).stdout.strip(),
            git(repo, "config", "user.email", check=False).stdout.strip())


def read_deny_list(path: Path) -> tuple[list[str], list[tuple[str, re.Pattern]]]:
    """bin/scrub-check's format: one ERE per line, `allow PATH REGEX`,
    `@box-ip` (this machine's public addresses), `#` comments."""
    pats, allows = [], []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line == "@box-ip":
            out = subprocess.run(["ip", "-o", "addr", "show", "scope", "global"],
                                 capture_output=True, text=True, check=False).stdout
            for row in out.splitlines():
                parts = row.split()
                if len(parts) > 3 and parts[2] in ("inet", "inet6"):
                    ip = parts[3].split("/")[0]
                    try:
                        if ipaddress.ip_address(ip).is_global:
                            pats.append(re.escape(ip))
                    except ValueError:
                        pass
        elif line.startswith("allow "):
            _, where, rx = line.split(None, 2)
            allows.append((where, re.compile(rx)))
        else:
            pats.append(line)
    return pats, allows


def gate_on(repo: Path) -> bool:
    return git(repo, "config", "--type=bool", "loam.pushGate",
               check=False).stdout.strip() == "true"


def push_deny_file(repo: Path) -> Path | None:
    value = git(repo, "config", "loam.pushDeny", check=False).stdout.strip()
    return Path(value).expanduser() if value else None


def deny_rules(repo: Path, deny: Path | None = None
               ) -> tuple[re.Pattern | None, list[tuple[str, re.Pattern]], list[str]]:
    """The checkout's deny list as one pattern, its `allow` lines, and what
    is wrong with it (a list `loam.pushDeny` names that is missing)."""
    deny = deny if deny is not None else push_deny_file(repo)
    problems = []
    if deny and not deny.is_file():
        problems.append(f"the deny list `loam.pushDeny` names ({deny}) is missing")
    pats, allows = read_deny_list(deny) if deny and deny.is_file() else ([], [])
    big = re.compile("|".join(f"(?:{p})" for p in pats)) if pats else None
    return big, allows, problems


def push_gate(repo: Path, since: str | None, deny: Path | None = None,
              head: str = MAIN, objects: Path | None = None, *,
              expected: tuple[str, str] | None = None) -> list[str]:
    """Why `head` may not be pushed past `since` (what the remote has; None
    for a remote that has nothing). Empty when it may. ``deny`` defaults to
    the checkout's `loam.pushDeny`. ``objects`` is the repository the
    commits are read from when it is not ``repo`` (a landing asks of the
    job's clone before the canonical checkout has its commits); the
    deny list always comes from ``repo``. The launcher passes the resolved job
    identity as ``expected``; a standalone check uses the checkout identity."""
    objects = objects or repo
    spec = [f"{since}..{head}"] if since else [head]
    commits = git(objects, "rev-list", "--reverse", *spec, check=False).stdout.split()
    if not commits:
        return []
    if len(commits) > GATE_COMMITS:
        return [f"{len(commits)} commits to push, more than the gate reads "
                f"({GATE_COMMITS}); push them by hand after checking them"]
    name, email = expected if expected is not None else identity(repo)
    want = f"{name} <{email}>"
    big, allows, problems = deny_rules(repo, deny)
    for sha in commits:
        meta = git(objects, "show", "-s", "--format=%an <%ae>%x00%cn <%ce>%x00%B",
                   sha).stdout
        author, committer, body = (meta.split("\0", 2) + ["", ""])[:3]
        for role, who in (("author", author), ("committer", committer)):
            if who != want:
                problems.append(f"{sha[:7]}: {role} is {who!r}, expected {want!r}")
        if not big:
            continue
        for m in big.finditer(body):
            problems.append(f"{sha[:7]}: its message names {m.group(0)!r}")
        diff = git(objects, "show", "--format=", "-U0", "--no-color", "--no-renames",
                   "--no-ext-diff", "--text", sha, check=False).stdout
        path = None
        for line in diff.splitlines():
            if line.startswith("+++ "):
                path = line[6:] if line.startswith("+++ b/") else None
                for m in big.finditer(path or ""):
                    problems.append(f"{sha[:7]}: path {path} names {m.group(0)!r}")
            elif line.startswith("+") and path:
                for m in big.finditer(line[1:]):
                    if not any(path == w and rx.search(m.group(0)) for w, rx in allows):
                        problems.append(f"{sha[:7]}: {path} adds {m.group(0)!r}")
    return problems


# Re-author eligible unpublished commits. Reflog checks distinguish landings from manual changes.

REAUTHOR_NOTE = "loam: re-author landed commits"


def foreign(repo: Path, since: str, head: str, want: str) -> list[str]:
    """The commits in `since..head` whose author or committer is not
    ``want``."""
    out = git(repo, "log", "--format=%H%x00%an <%ae>%x00%cn <%ce>",
              f"{since}..{head}", check=False).stdout
    rows = [line.split("\0") for line in out.splitlines() if line]
    return [r[0] for r in rows if len(r) == 3 and (r[1] != want or r[2] != want)]


def _who(value: str) -> tuple[str, str, str]:
    """(name, email, date) from a commit header's `author`/`committer`."""
    head, _, date = value.rpartition(">")
    name, _, email = head.partition("<")
    return name.strip(), email.strip(), date.strip()


def remake(repo: Path, since: str, head: str, pick) -> str:
    """`head` with the commits in `since..head` made again where ``pick``
    says: ``pick(sha, author, committer, message, signed)`` (author and
    committer as ``(name, email)``, message as text) answers None to keep the
    commit, or ``(name, email, message)`` to make it as that identity, author
    and committer, with that message. Trees, dates and parents (mapped) are
    kept; a commit kept whose parent was re-made is re-made with its own
    people and message. A commit that comes out the same keeps its hash.
    Returns the new head; refs are not touched."""
    shas = git(repo, "rev-list", "--topo-order", "--reverse",
               f"{since}..{head}").stdout.split()
    mapped: dict[str, str] = {}
    for sha in shas:
        raw = subprocess.run(["git", "-C", str(repo), "cat-file", "commit", sha],
                             capture_output=True, check=True).stdout
        header, _, body = raw.partition(b"\n\n")
        tree, parents, people, signed = None, [], {}, False
        for line in header.decode("utf-8", "replace").splitlines():
            key, _, value = line.partition(" ")
            if key == "tree":
                tree = value
            elif key == "parent":
                parents.append(value)
            elif key in ("author", "committer"):
                people[key] = _who(value)
            elif key in ("gpgsig", "gpgsig-sha256"):
                signed = True
        new_parents = [mapped.get(p, p) for p in parents]
        a, c = people.get("author", ("", "", "")), people.get("committer", ("", "", ""))
        text = body.decode("utf-8", "surrogateescape")
        change = pick(sha, a[:2], c[:2], text, signed)
        if change is None and new_parents == parents:
            mapped[sha] = sha
            continue
        if change is not None:
            an = cn = change[0]
            ae = ce = change[1]
            body = change[2].encode("utf-8", "surrogateescape")
        else:
            (an, ae), (cn, ce) = a[:2], c[:2]
        env = dict(os.environ, GIT_AUTHOR_NAME=an, GIT_AUTHOR_EMAIL=ae,
                   GIT_COMMITTER_NAME=cn, GIT_COMMITTER_EMAIL=ce,
                   GIT_AUTHOR_DATE=a[2], GIT_COMMITTER_DATE=c[2])
        argv = ["git", "-C", str(repo), "commit-tree", tree]
        for p in new_parents:
            argv += ["-p", p]
        made = subprocess.run(argv, input=body, env=env, capture_output=True,
                              check=False)
        if made.returncode != 0:
            raise WorktreeError(f"re-authoring {sha[:7]} failed: "
                                f"{made.stderr.decode(errors='replace').strip()[:300]}")
        mapped[sha] = made.stdout.decode().strip()
    tip = rev(repo, head) or head
    return mapped.get(tip, tip)


def reauthor(repo: Path, since: str, head: str, name: str, email: str) -> str:
    """`head` with every commit in `since..head` made again as ``name
    <email>``: same tree, message, parents (mapped) and dates. A commit that
    already has the identity comes out as itself. If the range contains a
    signed commit, return the original head so its signature stays valid."""
    want = (name, email)
    has_signature = False

    def pick(_sha, author, committer, message, signed):
        nonlocal has_signature
        has_signature |= signed
        if author == want and committer == want:
            return None
        return name, email, message
    new = remake(repo, since, head, pick)
    return head if has_signature else new


def job_identity(project: eng.Project, repo: Path) -> dict:
    """Resolve the project's person, the owner, or the checkout fallback."""
    return loam_git_identity.resolve(loam_commit_identity.of(project.record),
                                     identity(repo))


def remote_tips(repo: Path) -> list[str]:
    """Every commit the canonical checkout holds as a remote's branch: what
    a landing never re-makes, because a remote already has it."""
    out = git(repo, "for-each-ref", "--format=%(objectname)", "refs/remotes",
              check=False).stdout.split()
    return sorted(set(out))


def settle_commits(want: dict, repo: Path, tree: Path, branch: str,
                   main_sha: str, say, result: dict | None = None) -> str | None:
    """Set the clone's identity and normalize unpublished commits before merging.
    Preserve signatures. An ungated checkout also preserves human attribution.
    Return the previous tip if the branch moves."""
    if not is_clone(tree):
        return None
    if not want["name"] or not want["email"]:
        return None
    if result is not None:
        result["attribution"] = loam_git_identity.summary(want)
    name, email = want["name"], want["email"]
    for key, value in (("user.name", name), ("user.email", email)):
        if git(tree, "config", "--local", "--get", key, check=False).stdout.strip() != value:
            git(tree, "config", key, value)
    tip = branch_tip(tree, branch)
    if not tip:
        return None
    own = set(git(tree, "rev-list", tip, "--not", main_sha, *remote_tips(repo),
                  check=False).stdout.split())
    if not own:
        return None
    gated = gate_on(repo)
    agents = {(name, email.lower()), (identity(repo)[0], identity(repo)[1].lower())}
    kept_signed: list[str] = []

    def pick(sha, author, committer, message, signed):
        if sha not in own:
            return None
        if not (gated or loam_git_identity.agent_made(author, committer, agents)):
            return None
        clean = loam_git_identity.strip_provider(message)
        if author == (name, email) and committer == (name, email) and clean == message:
            return None
        if signed:
            kept_signed.append(sha[:10])
            return None
        return name, email, clean
    new = remake(tree, main_sha, tip, pick)
    if kept_signed:
        say(f"landing: kept {len(kept_signed)} signed commit(s) as made "
            f"({', '.join(kept_signed[:5])})")
        if result is not None:
            result["signed_kept"] = kept_signed
    if new == tip:
        return None
    git(tree, "update-ref", f"refs/heads/{branch}", new, tip)
    say(f"landing: the job's own commits now carry {name} "
        f"<{loam_git_identity.masked(email)}> and no provider attribution "
        f"({tip[:7]} -> {new[:7]})")
    return tip


def landings_only(repo: Path, since: str) -> bool:
    """Whether every move of `main` since it last stood at a commit `origin`
    has was a landing's fast-forward (`merge <sha>: Fast-forward`) or an
    earlier re-authoring of this kind."""
    out = git(repo, "reflog", "show", "--format=%H%x00%gs", f"refs/heads/{MAIN}",
              check=False).stdout
    for line in out.splitlines():
        sha, _, subject = line.partition("\0")
        if sha == since or is_ancestor(repo, sha, since):
            return True
        if not ((subject.startswith("merge ") and subject.endswith(": Fast-forward"))
                or subject.startswith(REAUTHOR_NOTE)):
            return False
    return False


def reauthor_landed(repo: Path, since: str | None, *,
                    expected: tuple[str, str]) -> tuple[str, str] | None:
    """Re-author the landed commits `origin` lacks when another identity
    holds them and only landings put them there: (old main, new main), or
    None when nothing was done."""
    name, email = expected
    head = rev(repo, MAIN)
    if not since or not head or not name or not email \
            or not is_ancestor(repo, since, head) \
            or not foreign(repo, since, head, f"{name} <{email}>") \
            or not landings_only(repo, since):
        return None
    mark = rev(repo, BACKUP_MARK) if has_remote(repo, BACKUP) else None
    if mark and not is_ancestor(repo, mark, since):
        return None         # the off-box copy already took them
    # A fetched branch other than origin/main can already have part of this range.
    pending = git(repo, "rev-list", "--count", f"{since}..{head}").stdout
    unpublished = git(repo, "rev-list", "--count", head, "--not", since,
                      *remote_tips(repo)).stdout
    if pending != unpublished:
        return None
    new = reauthor(repo, since, head, name, email)
    if new == head:
        return None
    # Same tree as `head`, so the checkout's index and files already match.
    git(repo, "update-ref", "-m", f"{REAUTHOR_NOTE} as {name} <{email}>",
        f"refs/heads/{MAIN}", new, head)
    return head, new


def push_origin(project: eng.Project, *, expected: tuple[str, str] | None = None) -> bool | str:
    """Push `main` to `origin`, through the gate when the checkout has
    one: True, or why not. Gated, it first re-authors landed commits
    (``reauthor_landed``), which may move `main`."""
    repo = canonical(project)
    problems = []
    if gate_on(repo):
        git(repo, "fetch", "--quiet", "--no-tags", "origin", MAIN, check=False,
            timeout=120)
        since = rev(repo, f"origin/{MAIN}")
        if expected is None:
            want = job_identity(project, repo)
            expected = want["name"], want["email"]
        reauthor_landed(repo, since, expected=expected)
        problems = push_gate(repo, since, expected=expected)
    if problems:
        shown = "; ".join(problems[:5]) + (
            f"; and {len(problems) - 5} more" if len(problems) > 5 else "")
        return f"held by the push gate: {shown}"
    push = git(repo, "push", "--quiet", "origin", MAIN, check=False, timeout=180)
    return True if push.returncode == 0 else (
        (push.stderr or push.stdout).strip()[:400] or f"exit {push.returncode}")


# The canonical reflog identifies superseded main commits even after a previous mid-job replay.
# Include the recorded base in case its reflog entry has expired.

def former_mains(repo: Path) -> list[str]:
    """Every commit `main` has pointed at in the canonical checkout, newest
    first, each once."""
    out = git(repo, "reflog", "show", "--format=%H", f"refs/heads/{MAIN}",
              check=False).stdout.split()
    return list(dict.fromkeys(out))


def stale_points(repo: Path, tree: Path, record: dict, main_sha: str,
                 tip: str) -> list[str]:
    """The commits the branch would bring into `main` that were once `main`
    and are not in it now -- left behind by a rewrite of `main` (or a reset
    of it) -- newest first. Empty for a branch that is safe to merge."""
    brings = set(git(tree, "rev-list", tip, f"^{main_sha}",
                     check=False).stdout.split())
    if not brings:
        return []
    candidates = former_mains(repo)
    base = record.get("base")
    if base:
        candidates.append(rev(tree, base) or base)
    return [c for c in dict.fromkeys(candidates) if c in brings]


def replay_plan(repo: Path, tree: Path, record: dict, main_sha: str,
                tip: str) -> dict | None:
    """What a landing replays, and the only place that says it: None when
    the branch carries nothing a rewrite left behind; else ``point`` (the
    newest old `main` commit in the branch), ``commits`` (the job's own
    after it, oldest first, merges left out), ``skipped`` (those of them
    whose change `main` already has, by patch id -- a commit that landed
    mid-job and was then rewritten), and ``apart`` (old `main` commits on
    another line than ``point``, which make the job's own not one range)."""
    stale = stale_points(repo, tree, record, main_sha, tip)
    if not stale:
        return None
    point = stale[0]
    apart = [c for c in stale[1:] if not is_ancestor(tree, c, point)]
    commits = git(tree, "rev-list", "--reverse", "--topo-order", "--no-merges",
                  tip, f"^{point}", f"^{main_sha}", check=False).stdout.split()
    # `--cherry-pick` over main...tip marks, on the branch's side, each
    # commit a commit of main's side matches by patch id.
    marked = git(tree, "rev-list", "--left-right", "--cherry-mark", "--no-merges",
                 f"{main_sha}...{tip}", f"^{point}", check=False).stdout.split()
    same = {m[1:] for m in marked if m.startswith("=")}
    return {"point": point, "apart": apart,
            "commits": [c for c in commits if c not in same],
            "skipped": [c for c in commits if c in same]}


def replay_if_rewritten(repo: Path, tree: Path, branch: str, record: dict,
                        main_sha: str, say) -> str | None:
    """Replay only the job's changes onto rewritten main. Skip patches already present.
    Leave the branch ref in place until all picks succeed. Return a reason on failure."""
    tip = branch_tip(tree, branch)
    if not tip:
        return None
    plan = replay_plan(repo, tree, record, main_sha, tip)
    if not plan:
        return None
    point = plan["point"]
    if plan["apart"]:
        return (f"the branch carries old `{MAIN}` commits on more than one line "
                f"({point[:7]}, {', '.join(c[:7] for c in plan['apart'][:3])}), "
                f"so which commits are the job's own is not one range; rebase "
                f"the job's own commits onto `{MAIN}` by hand")
    say(f"landing: main was rewritten since the branch took {point[:7]}; "
        f"replaying the job's own commits onto it ({len(plan['commits'])}, "
        f"{len(plan['skipped'])} already on main)")
    name, email = identity(repo)
    ident = ["-c", f"user.name={name}", "-c", f"user.email={email}"]
    git(tree, "checkout", "--quiet", "--detach", main_sha)
    for sha in plan["commits"]:
        out = git(tree, *ident, "cherry-pick", "--allow-empty",
                  "--keep-redundant-commits", sha, check=False, timeout=600)
        if out.returncode != 0:
            conflicts = git(tree, "diff", "--name-only", "--diff-filter=U",
                            check=False).stdout.split()
            git(tree, "cherry-pick", "--abort", check=False)
            git(tree, "checkout", "--quiet", "-f", branch, check=False)
            return (f"replaying {sha[:7]} conflicts in "
                    f"{', '.join(conflicts) or '?'}: "
                    + ((out.stderr or out.stdout).strip()[-400:]
                       or f"git cherry-pick exited {out.returncode}"))
        head = rev(tree, "HEAD")
        if rev(tree, f"{head}^{{tree}}") == rev(tree, f"{head}~1^{{tree}}") \
                and rev(tree, f"{sha}^{{tree}}") != rev(tree, f"{sha}~1^{{tree}}"):
            git(tree, "reset", "--quiet", "--hard", "HEAD~1")   # came out empty
    new = rev(tree, "HEAD")
    # Whose name the replayed commits carry is settled next, by the landing's
    # ``settle_commits``, as for any branch.
    git(tree, "update-ref", f"refs/heads/{branch}", new, tip)
    git(tree, "checkout", "--quiet", "-f", branch)
    record["base"] = main_sha
    return None


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
    """Keep the union merge attribute for project memory in the local Git attributes file."""
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


# The platform harness is generated locally under engineer/ and must be available in the clone.
PLATFORM_HARNESS = lc.ENGINEER_HARNESS


def harness_names(project: eng.Project) -> tuple[str, ...]:
    if project.platform:
        return PLATFORM_HARNESS
    if project.subdir:
        return tuple(f"{project.subdir}/{name}" for name in HARNESS)
    return HARNESS


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


# A shared clone reads canonical objects through an alternate but owns its refs, index and writes.

def is_clone(tree: Path) -> bool:
    return (tree / ".git").is_dir()


def common_dir(repo: Path) -> Path:
    common = Path(git(repo, "rev-parse", "--git-common-dir").stdout.strip())
    return common if common.is_absolute() else repo / common


def configure_clone(repo: Path, tree: Path, identity: dict | None = None) -> None:
    """Configure commit identity, harness exclusions and memory merge attributes.
    Use the canonical repository as the clone's remote."""
    configure_identity(repo, tree)
    apply_identity(tree, identity)
    install_hook(tree)
    git(tree, "config", "gc.auto", "0")
    info_src, info_dst = common_dir(repo) / "info", tree / ".git" / "info"
    info_dst.mkdir(parents=True, exist_ok=True)
    for name in ("exclude", "attributes"):
        if (info_src / name).is_file():
            shutil.copy2(info_src / name, info_dst / name)


def install_hook(tree: Path) -> bool:
    """Loam's `commit-msg` hook in a clone (lib/loam_git_identity.py), unless
    the clone has a hook of that name that is not ours. True when written."""
    hooks = tree / ".git" / "hooks"
    target = hooks / loam_git_identity.HOOK_NAME
    text = loam_git_identity.hook_script(Path(loam_git_identity.__file__).resolve().parent)
    try:
        if target.exists():
            old = target.read_text(encoding="utf-8", errors="replace")
            if loam_git_identity.HOOK_MARK not in old or old == text:
                return False
        hooks.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        target.chmod(0o755)
    except OSError:
        return False
    return True


def configure_identity(repo: Path, tree: Path) -> None:
    for key in ("user.name", "user.email"):
        value = git(repo, "config", "--local", "--get", key, check=False).stdout.strip()
        if value:
            git(tree, "config", key, value)


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


# With mirror refresh enabled, expose upstream branches through the local mirror.
# Prune deleted branches but retain unpublished release tags.

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
    """Expose upstream branches as origin/* through the canonical mirror. Track them explicitly."""
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
    """Each clone has its own Git root and CLI trust entry. For a live clone,
    update missing trust flags atomically and retry concurrent writes."""
    if not tree.is_relative_to(LIVE_WORKTREES) or not CLAUDE_JSON.is_file():
        return False
    for _attempt in range(5):
        before = CLAUDE_JSON.stat()
        cfg = json.loads(CLAUDE_JSON.read_text(encoding="utf-8"))
        entry = cfg.setdefault("projects", {}).setdefault(str(tree), {})
        # Trust includes the shared instructions imported from outside the tree.
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
    """Resolve a recorded worktree path under the configured state directory."""
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
    made_main = None
    if project.platform and not rev(repo, f"refs/heads/{MAIN}"):
        with loam_lock.live(project.name, "local main"):
            made_main = ensure_local_main(repo)
    resolved = job_identity(project, repo)
    # The clone uses the same identity rules as the landing.
    identity = ({"name": resolved["name"], "email": resolved["email"]}
                if resolved["name"] and resolved["email"] else None)
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
            # A tree made before the checkout's identity, or the owner's, was
            # set: it gets today's below, and the hook it may predate.
            install_hook(path)
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
    if made_main:
        record["local_main"] = made_main
    if identity:
        # A reused or converted tree too: its config predates the identity
        # resolved now.
        apply_identity(path, identity)
    if resolved["source"] == "project":
        record["commit_identity"] = f"{identity['name']} <{identity['email']}>"
    # Public-safe: the address masked unless it is a GitHub no-reply.
    record["attribution"] = {"source": resolved["source"],
                             "name": resolved["name"],
                             "email": loam_git_identity.masked(resolved["email"]),
                             "summary": loam_git_identity.summary(resolved)}
    record["seeded"] = seed_harness(project, record)
    record["trusted"] = ensure_trust(path)
    write_record(job_dir, record)
    return record


def tree_lock(record: dict):
    """Held by the job running in this tree, so two continuations of one job
    cannot both resume into it. Non-blocking: the second is refused."""
    path = Path(record["path"])
    return loam_lock.LOCK_DIR / f"tree-{path.parent.name}-{path.name}.lock"


# Stage writable mounts before read-only parents. User namespaces lock inherited read-only flags.

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
    vault_dir = project.vault_write_dir()
    ro = [repo]
    if project.record.get("vault_write") is True:
        ro.append(eng.VAULT)
    if vault_dir:
        rw.append(vault_dir)
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
    # Keep runtime state writable on installs that store it under the checkout.
    broad = ([p for p in (lc.STATE, lc.LOGS) if p.is_relative_to(repo)]
             if project.platform else [])
    ro_after = [WORKTREES, project.jobs]
    keep = lambda paths: [p for p in paths if p.is_dir()]  # noqa: E731
    return {"ro": keep(ro), "rw_broad": keep(broad),
            "ro_after": keep(ro_after), "rw": keep(rw)}


def wall_script(project: eng.Project, record: dict, job_dir: Path,
                protect: list[Path] = ()) -> str:
    """Build the mount sequence, including read-only overlays on protected paths."""
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
    # Isolate the optional cleanup failure so it cannot mask a failed mount.
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
    if project.record.get("vault_write") is True and project.vault_write_dir() is None:
        return "the project's writable vault folder is missing or has moved"
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
    """Build the sealed test command described in tests/README.md.

    Bind tracked top-level entries over their canonical paths. Tests need the
    configured runtime paths and working git metadata. Bind the MCP virtual
    environment before covering its parent. A new top-level entry without a
    canonical mount target is absent from this view."""
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
    for path in sealed_paths():
        mounts.append(f"mount -t tmpfs -o ro,size=64k,mode=0755 loam-sealed {_q(str(path))}")
    mounts.append("ip link set lo up")
    inner = (f"cd {eng.AGENTS} && exec unshare -U --map-user={uid} "
             f"--map-group={gid} sh -c {_q(command)}")
    script = " && ".join(mounts + [f"exec sh -c {_q(inner)}"])
    return ["unshare", "-Urmn", "sh", "-c", script]


def sealed_paths() -> list[Path]:
    """The live folders a landing's tests see empty: the install's data, its
    other trees and repositories, its secrets and its user manager. Never
    the checkout the tree is shown at, nor anything holding it, nor the
    tools the tests run (the MCP venv, ~/.local/bin)."""
    keep = [eng.AGENTS, lc.MCP_VENV, lc.LOCAL_BIN]
    out = []
    for path in (lc.VAULT, lc.WORKSPACES, lc.PROJECTS, lc.PLUGINS, lc.STATE,
                 lc.LOGS, lc.JOBS, lc.REVIEWER_STATE, lc.SECRETS_ENV.parent,
                 lc.WAKE_ENV.parent, lc.USER_UNIT_DIR, lc.RUNTIME_DIR):
        if not path.is_dir() or path in out:
            continue
        if any(k == path or path in k.parents for k in keep):
            continue
        out.append(path)
    # A parent before what it holds: once it is covered, a folder inside it
    # is not there to mount on (and is hidden already).
    out.sort(key=lambda p: len(p.parts))
    return [p for p in out if not any(q in p.parents for q in out)]


def _q(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


CREDENTIAL_NAMES = frozenset({"CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY",
                              "ANTHROPIC_AUTH_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"})
CREDENTIAL_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD")


def is_credential(name: str) -> bool:
    return name in CREDENTIAL_NAMES or name.endswith(CREDENTIAL_SUFFIXES)


def low_priority_argv(argv: list[str], *, which=shutil.which) -> list[str]:
    """Wrap a landing test command in lowest CPU/IO priority (`nice -n 19`,
    `ionice -t -c 3`) so tests yield to owner-facing chats and the app even when
    invoked outside an engineer systemd unit."""
    prefix: list[str] = []
    if which("ionice"):
        prefix += ["ionice", "-t", "-c", "3"]
    if which("nice"):
        prefix += ["nice", "-n", str(lc.ENGINEER_NICE)]
    return prefix + argv


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
    argv = low_priority_argv(argv)
    # The test command runs project code. Strip job restrictions and inherited credentials.
    env = {k: v for k, v in os.environ.items()
           if k != "CLAUDE_PROJECT_DIR"
           and not k.startswith(("LOAM_JOB_", "LOAM_WALL"))
           and not is_credential(k)}
    # Offline mode also covers credentials loaded from files.
    env["LOAM_OFFLINE"] = "1"
    # Isolate test locks from the job and other live holders.
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

def outside_scope(project: eng.Project, tree: Path, base: str, tip: str) -> list[str]:
    """List branch changes outside the project subdirectory."""
    if not project.subdir:
        return []
    changed = git(tree, "diff", "--name-only", "--no-renames", f"{base}...{tip}",
                  check=False).stdout.split()
    return [p for p in changed if not p.startswith(project.subdir + "/")]


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
    """Read the job branch by ref even when the working tree has a detached HEAD."""
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



# Commits made during testing stay on the job branch for its next landing.

def carry_onto(tree: Path, branch: str, commits: list[str], onto: str,
               result: dict) -> bool:
    """Put ``commits`` (oldest first) on top of ``onto`` as ``branch``, by
    cherry-pick in a detached HEAD. True when done; on a conflict nothing
    moves, the newest of them is kept under ``refs/loam/kept/``, and the
    result says so."""
    if not commits:
        return True
    start = branch_tip(tree, branch)
    git(tree, "checkout", "--quiet", "--detach", onto)
    for sha in commits:
        out = git(tree, "cherry-pick", "--allow-empty", "--keep-redundant-commits",
                  sha, check=False, timeout=600)
        if out.returncode != 0:
            git(tree, "cherry-pick", "--abort", check=False)
            git(tree, "update-ref", f"refs/loam/kept/{commits[-1][:12]}", commits[-1])
            git(tree, "checkout", "--quiet", "-f", branch, check=False)
            result.setdefault("kept", []).append(commits[-1])
            return False
    new = rev(tree, "HEAD")
    git(tree, "update-ref", f"refs/heads/{branch}", new, start or "")
    git(tree, "checkout", "--quiet", branch)
    result.setdefault("carried", []).extend(commits)
    return True


def back_to_branch(tree: Path, branch: str, left_at: str | None, result: dict,
                   *, force: bool = False) -> None:
    """Put the tree back on ``branch`` from where the landing left HEAD
    (``left_at``), keeping any commit made on top of it meanwhile."""
    on = git(tree, "symbolic-ref", "-q", "HEAD", check=False).stdout.strip()
    head = rev(tree, "HEAD")
    extra: list[str] = []
    if on != f"refs/heads/{branch}" and head and left_at and head != left_at \
            and is_ancestor(tree, left_at, head):
        extra = git(tree, "rev-list", "--reverse", "--no-merges", head,
                    f"^{left_at}", check=False).stdout.split()
    git(tree, "checkout", "--quiet", *(["-f"] if force else []), branch, check=False)
    if extra:
        carry_onto(tree, branch, extra, branch_tip(tree, branch) or head, result)


def landed_earlier(project: eng.Project, record: dict) -> str | None:
    """Return the tip if the branch advanced from its base and main already contains it."""
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


# A release install keeps local changes on its own main. See docs/update.md.
LOCAL_MAIN_KEY = "loam.localMain"


def local_main(repo: Path) -> bool:
    """Whether the checkout's `main` is a release install's own."""
    return git(repo, "config", "--bool", "--get", LOCAL_MAIN_KEY,
               check=False).stdout.strip() == "true"


def ensure_local_main(repo: Path) -> str | None:
    """On a checkout detached with no `main` (a release install), make
    `main` at HEAD, put HEAD on it and mark it local. What it did, or None
    when there was nothing to do. Nothing in the tree or the index moves."""
    if rev(repo, f"refs/heads/{MAIN}"):
        return None
    if git(repo, "symbolic-ref", "-q", "HEAD", check=False).returncode == 0:
        return None                     # on another branch: a person's choice
    head = rev(repo, "HEAD")
    if not head:
        return None
    git(repo, "update-ref", "-m", "loam: the install's own main", f"refs/heads/{MAIN}",
        head, "")
    git(repo, "symbolic-ref", "-m", "loam: the install's own main", "HEAD",
        f"refs/heads/{MAIN}")
    git(repo, "config", LOCAL_MAIN_KEY, "true")
    return f"made `{MAIN}` at {head[:7]}, the release this install runs"


def sync_with_origin(repo: Path) -> str | None:
    """Bring the canonical `main` up to `origin/main` if it is behind. Returns
    why it could not, or None. A fetch that fails is not a refusal -- the
    push will say so if the remote moved -- but a `main` that has diverged
    from its remote is, because no merge here may rewrite either. A release
    install's own `main` is never synced (``local_main``)."""
    if not has_remote(repo) or local_main(repo):
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


# Keep install-local harness files out of upstream pull requests.
HARNESS_IN_COMMITS = HARNESS + (".codex",)


def harness_in_commits(tree: Path, base: str, tip: str) -> list[str]:
    changed = git(tree, "diff", "--name-only", f"{base}...{tip}",
                  check=False).stdout.split()
    return [f for f in changed
            if any(f == h or f.startswith(h + "/") for h in HARNESS_IN_COMMITS)]


def refresh_remote_view(project: eng.Project, repo: Path) -> None:
    """Fetch remote branches under the caller’s lock and fast-forward the local mirror.
    A failed fetch keeps the previous view, so a commit may still need review."""
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


# If only the memory note fails to land, retain its patch with the job. See CONTRIBUTING.md.

MEMORY_PATCH = "memory-note.patch"
MEMORY_COMMIT = "Engineer memory (committed by the landing)"
MEMORY_RETRY = ("branch_tests", "merged_tests", "error")


def memory_prefix(project: eng.Project) -> str:
    return Path(memory_rel(project)).parent.as_posix() + "/"


def only_memory(project: eng.Project, paths) -> bool:
    paths = [p for p in paths if p]
    prefix = memory_prefix(project)
    return bool(paths) and all(p.startswith(prefix) for p in paths)


def branch_changes(tree: Path, main_sha: str, tip: str) -> list[str]:
    """The paths the branch changes since it last met `main`."""
    return [p for p in git(tree, "diff", "--name-only", "--no-renames",
                           f"{main_sha}...{tip}", check=False).stdout.split("\n") if p]


def diff_paths(tree: Path, base: str, target: str) -> list[str]:
    """The paths that differ between two commits (`base` -> `target`)."""
    return [p for p in git(tree, "diff", "--name-only", "--no-renames",
                           base, target, check=False).stdout.split("\n") if p]


def is_notes_only(project: eng.Project, tree: Path, main_sha: str,
                  tip: str, merged: str) -> bool:
    """True when both the branch and its merge into `main` touch nothing
    outside the engineer's own memory folder."""
    return (only_memory(project, branch_changes(tree, main_sha, tip))
            and only_memory(project, diff_paths(tree, main_sha, merged)))


def commit_memory(project: eng.Project, tree: Path, say) -> bool:
    """Commit the tree's uncommitted changes when they are the memory file
    alone. True when it did."""
    dirty = dirty_files(tree)
    if not dirty or not only_memory(project, dirty):
        return False
    git(tree, "add", "-A", "--", memory_prefix(project))
    out = git(tree, "commit", "--quiet", "--no-verify", "-m", MEMORY_COMMIT, check=False)
    if out.returncode != 0:
        return False
    say("landing: committed the engineer's uncommitted memory note")
    return True


def hold_note(project: eng.Project, record: dict, tree: Path, main_sha: str,
              tip: str, why: str, detail: str) -> dict:
    """Keep the memory change the branch makes as a patch in the job's
    folder; what the receipt says of it."""
    patch = git(tree, "diff", "--no-color", "--no-ext-diff", f"{main_sha}...{tip}",
                "--", memory_prefix(project), check=False).stdout
    where = project.jobs / record["job"] / MEMORY_PATCH
    try:
        where.write_text(patch, encoding="utf-8")
        kept = str(where)
    except OSError:
        kept = None
    return {"reason": why, "detail": (detail or "")[:600], "patch": kept}


def gate_held_note_only(project: eng.Project, held: list[str]) -> bool:
    """The gate's hold names nothing the work adds: every problem is the
    memory file or a commit's message (identities are made right before the
    gate reads them, ``settle_commits``)."""
    prefix = memory_prefix(project)
    for item in held:
        _, _, what = item.partition(": ")
        if what.startswith("its message names "):
            continue
        if what.startswith(prefix) and " adds " in what:
            continue
        if what.startswith(f"path {prefix}"):
            continue
        return False
    return bool(held)


def without_memory(project: eng.Project, tree: Path, branch: str, main_sha: str,
                   message: str) -> str | None:
    """Move `branch` to one commit on its merge base with `main` carrying
    the branch's whole change except the memory file, under ``message``.
    The old tip stays reachable as `refs/loam/held/<branch>`. Returns it."""
    tip = branch_tip(tree, branch)
    base = git(tree, "merge-base", main_sha, tip, check=False).stdout.strip()
    if not tip or not base:
        return None
    index = Path(tempfile.mkdtemp(prefix="loam-note-index-")) / "index"
    env = {**os.environ, "GIT_INDEX_FILE": str(index)}
    try:
        def g(*args):
            return subprocess.run(["git", "-C", str(tree), *args], capture_output=True,
                                  text=True, env=env, check=True).stdout
        g("read-tree", tip)
        g("rm", "-r", "--cached", "--quiet", "--ignore-unmatch", "--", memory_prefix(project))
        if git(tree, "ls-tree", "-r", "--name-only", base, "--",
               memory_prefix(project), check=False).stdout.strip():
            g("read-tree", "--prefix=" + memory_prefix(project), f"{base}:{memory_prefix(project)}")
        new_tree = g("write-tree").strip()
        if new_tree == git(tree, "rev-parse", f"{base}^{{tree}}").stdout.strip():
            return None     # nothing but the note
        name, email = identity(canonical(project))
        cenv = {**os.environ, "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
                "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email}
        commit = subprocess.run(["git", "-C", str(tree), "commit-tree", new_tree,
                                 "-p", base, "-m", message], capture_output=True,
                                text=True, env=cenv, check=True).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return None
    finally:
        shutil.rmtree(index.parent, ignore_errors=True)
    git(tree, "update-ref", f"refs/loam/held/{branch}", tip)
    git(tree, "checkout", "--quiet", "-f", "-B", branch, commit)
    return tip


def land(project: eng.Project, record: dict, message: str, *,
         keep_tree: bool = False, say=None, release: str | None = None,
         title: str | None = None, summary: str = "",
         target_pr: int | None = None, reply: str = "",
         cancelled=None) -> dict:
    """Land the job's branch on `main` (``_land_once``), with the memory note
    kept out of the way of the work (the section above). Same result, and
    ``memory_held`` ({"reason", "detail", "patch"}) when the note did not
    land."""
    say = say or (lambda _text: None)
    kw = dict(keep_tree=keep_tree, say=say, release=release, title=title,
              summary=summary, target_pr=target_pr, reply=reply,
              cancelled=cancelled)
    tree, branch = Path(record["path"]), record["branch"]
    if project.pull_request or not tree.is_dir():
        return _land_once(project, record, message, **kw)
    try:
        committed = commit_memory(project, tree, say)
    except (WorktreeError, OSError, subprocess.SubprocessError):
        committed = False
    result = _land_once(project, record, message, **kw)
    if committed:
        result["memory_committed"] = True
    if result.get("state") != "not_landed" or result.get("reason") in (
            "cancelled", "uncommitted", None) or not tree.is_dir():
        return result
    try:
        repo = canonical(project)
        refresh_main(repo, tree)
        main_sha, tip = rev(repo, MAIN), branch_tip(tree, branch)
        if not main_sha or not tip:
            return result
        changes = branch_changes(tree, main_sha, tip)
        if only_memory(project, changes):
            if result["reason"] in MEMORY_RETRY:
                say("landing: only the memory note is left and it did not "
                    "land; trying once more")
                again = _land_once(project, record, message, **kw)
                if again.get("state") != "not_landed":
                    return again
                result = again
            held = hold_note(project, record, tree, main_sha, tip,
                             result.get("reason"), result.get("detail") or (
                                 (result.get("test") or {}).get("tail", "")[-600:]))
            base = git(tree, "merge-base", main_sha, tip, check=False).stdout.strip()
            start = rev(repo, record["base"]) if record.get("base") else None
            earlier = base if base and start and base != start \
                and is_ancestor(tree, start, base) else None
            say("landing: the memory note did not land and is kept as "
                f"{held['patch'] or 'nothing (the patch could not be written)'}; "
                "the work is not held by it")
            out = {**result, "state": "landed_earlier" if earlier else "nothing",
                   "sha": earlier or main_sha, "reason": None,
                   "memory_held": held, "memory_test": result.get("test")}
            if not keep_tree:
                remove_tree(project, record, delete_branch=True)
            return out
        if result["reason"] == "gate" and gate_held_note_only(project, result.get("gate") or []):
            if keep_tree:
                return result   # the session is in the tree; it can fix its note
            say("landing: the push gate holds only the memory note or a commit "
                "message; landing the work as one commit without them")
            note = any(c.startswith(memory_prefix(project)) for c in changes)
            held = (hold_note(project, record, tree, main_sha, tip, "gate",
                              result.get("detail") or "") if note else None)
            old_tip = without_memory(project, tree, branch, main_sha, message)
            if not old_tip:
                return result
            again = _land_once(project, record, message, **kw)
            if again.get("state") == "not_landed":
                # Put the branch back as the engineer left it.
                git(tree, "checkout", "--quiet", "-f", "-B", branch, old_tip, check=False)
                return again
            if held:
                again["memory_held"] = held
            again["squashed"] = {"tip": old_tip, "gate": result.get("gate") or []}
            return again
    except (WorktreeError, OSError, subprocess.SubprocessError) as exc:
        say(f"landing: could not set the memory note aside: {exc}")
    return result


def _land_once(project: eng.Project, record: dict, message: str, *,
               keep_tree: bool = False, say=None, release: str | None = None,
               title: str | None = None, summary: str = "",
               target_pr: int | None = None, reply: str = "",
               cancelled=None) -> dict:
    """Land the job's branch on `main`. Always returns a result, never raises:

        state    landed | nothing | not_landed
                 (read_only is `read_only_result`'s, never this one's)
        reason   uncommitted | branch_tests | conflict | merged_tests |
                 no_tests | canonical | diverged | rewritten | gate | scope |
                 error | cancelled
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

    Check cancellation before testing and before moving refs. A cancelled
    landing keeps the branch and tree for inspection.

    The result records test retries and whether contention required testing
    the deciding merge under the lock.
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

        def finish() -> None:
            """The release tag, under the lock the landing still holds."""
            if release:
                say(f"landing: release tag {release}")
                try:
                    result["release"] = cut_release(repo, result, release,
                                                    require_notes=project.platform)
                except (WorktreeError, OSError,
                        subprocess.SubprocessError) as exc:
                    # Not the landing's failure: `main` has already moved.
                    result["release"] = {"tag": release, "state": "failed",
                                         "sha": result.get("sha"),
                                         "detail": str(exc)[:400]}

        lock = lambda: live_held(project, branch, result, on_wait, say)  # noqa: E731
        if project.pull_request:
            with lock():
                say("landing: holding the live lock")
                if stop_for_cancel(cancelled, result, say):
                    return result
                _land_pr_locked(project, repo, tree, branch, keep_tree, result,
                                say, record, title or message, summary,
                                target_pr, reply, cancelled)
                finish()
        else:
            _land_tested(project, repo, tree, branch, message, keep_tree,
                         result, say, record, cancelled, lock, finish,
                         require_tests=bool(release))
        rel = result.get("release") or {}
        if project.platform and rel.get("state") == "pushed" and rel.get("sha"):
            pin = _sync_marketplace_pin(repo, rel["tag"], rel["sha"], say=say)
            if pin is not None:
                rel["marketplace_pin"] = pin
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


# Tag the tested commit only after its main push succeeds.

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


def parse_release(spec: str | None) -> tuple[str | None, str | None]:
    """Split a release spec `TAG` or `TAG@COMMIT` into `(tag, target)`."""
    if not spec:
        return None, None
    text = spec.strip()
    if "@" in text:
        tag, target = text.split("@", 1)
        return tag.strip() or None, target.strip() or None
    return text or None, None


def cut_release(repo: Path, result: dict, tag: str, *, require_notes: bool = False) -> dict:
    """Tag what landed as `tag` (or an ancestor commit named by `tag@commit`,
    such as a hotfix branch merged back into `main`) and push the tag, or say
    why not. Never raises and never touches `main`: a refused tag leaves the
    merge landed.

        state   pushed | refused | failed
        sha     the commit tagged (or that would have been)
        detail  why, when not pushed
    """
    parsed_tag, target = parse_release(tag)
    tag = parsed_tag or tag
    landed_sha = result.get("sha")
    state = result.get("state")
    if state not in ("landed", "landed_earlier") or not landed_sha:
        return release_refused(tag, f"the branch did not land ({state})")
    sha = landed_sha
    if target:
        resolved = rev(repo, f"{target}^{{commit}}")
        if not resolved:
            return release_refused(tag, f"release target {target!r} is not a commit in {repo}", landed_sha)
        if not is_ancestor(repo, resolved, landed_sha):
            return release_refused(
                tag,
                f"release target {resolved[:7]} is not an ancestor of landed {MAIN} ({landed_sha[:7]})",
                resolved,
            )
        sha = resolved
    if git(repo, "check-ref-format", f"refs/tags/{tag}",
           check=False).returncode != 0:
        return release_refused(tag, f"{tag!r} is not a valid tag name", sha)
    if not has_remote(repo):
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
    if require_notes:
        missing = loam_release_notes.requirement(repo, sha, tag)
        if missing:
            return release_refused(tag, missing, sha)
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
    signed = sign_release(repo, tag, sha, local)
    if signed["state"] != "unsigned":
        if signed["state"] != "signed":
            return signed
        made = signed["made"]
    elif not local:
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
                       if versions else "no version file in the tree")
                      + (f"; {signed['detail']}" if signed.get("detail") else "")}


# Verify signed release tags before pushing. See docs/signing.md.

def sign_release(repo: Path, tag: str, sha: str, local: str | None) -> dict:
    """Sign ``tag`` at ``sha`` when the release must be signed.

        state  unsigned   no keys anywhere: tag as before
               signed     made (``made``) or already here, and verified
               to-sign    no signing key on this checkout; nothing tagged
               refused / failed, with ``detail``
    """
    need = loam_signing.required_signers(repo, tag, sha)
    if not need:
        return {"state": "unsigned"}
    made = False
    if not local:
        key = git(repo, "config", "user.signingKey", check=False).stdout.strip()
        if not key:
            return {"tag": tag, "state": "to-sign", "sha": sha, "detail": (
                f"this release must be signed and this checkout holds no "
                f"signing key; on the machine that holds it, run "
                f"`bin/sign-release {tag} {sha[:12]}` (docs/signing.md)")}
        done = git(repo, "-c", "gpg.format=ssh", "tag", "-s", "-m", f"Loam {tag}",
                   tag, sha, check=False, timeout=300)
        if done.returncode != 0:
            git(repo, "tag", "-d", tag, check=False)
            return {"tag": tag, "state": "failed", "sha": sha, "detail": (
                "signing the tag failed: "
                + (done.stderr or done.stdout).strip()[:300])}
        made = True
    for where, text in need:
        res = loam_signing.verify_tag(repo, tag, text)
        if not res["ok"]:
            if made:
                git(repo, "tag", "-d", tag, check=False)
            return release_refused(tag, f"the tag does not verify against the "
                                        f"keys in {where}: {res['message']}", sha)
    return {"state": "signed", "made": made,
            "detail": f"signed by {res.get('principal')} ({res.get('fingerprint')})"}


def _sync_marketplace_pin(repo: Path, tag: str, sha: str, *, say=None) -> dict | None:
    """Move the Market reviewer's Loam pin after a Loam release tag is pushed.

    Runs outside the project live lock. Skips offline tests and non-Loam
    repositories; on failure, alerts the system persona via loam_attention.
    """
    if os.environ.get("LOAM_MARKETPLACE_PIN_SYNC") == "0" or loam_offline.offline():
        return None
    if loam_marketplace_pin.repo_slug(repo) != loam_marketplace_pin.DEFAULT_LOAM_REPO:
        return None
    if say:
        say(f"landing: syncing marketplace reviewer pin to {tag}")
    pin = loam_marketplace_pin.sync_pin(tag, sha, say=say)
    if pin.get("state") == "failed":
        loam_marketplace_pin.notify_failure(pin)
    return pin


def release_section(release: dict | None) -> str:
    """The receipt's sentence on a release tag, or "" when none was asked."""
    if not release:
        return ""
    tag, sha = release["tag"], (release.get("sha") or "")[:7]
    if release["state"] == "pushed":
        out = (f" **Release tag `{tag}` pushed** at `{sha}` "
               f"({release.get('detail')}).")
        pin = release.get("marketplace_pin")
        if pin:
            pstate = pin.get("state")
            if pstate == "updated":
                out += f" Market reviewer pin moved to `{tag}` ({pin.get('detail')})."
            elif pstate == "current":
                out += f" Market reviewer pin was already at `{tag}`."
            elif pstate == "failed":
                out += f" **Market reviewer pin move to `{tag}` FAILED**: {pin.get('detail')}."
        return out
    if release["state"] == "to-sign":
        return (f" **Release `{tag}` is waiting to be signed** at `{sha}`: "
                f"{release.get('detail')}. No tag was pushed; the landing "
                f"above is unaffected.")
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


# Accumulate test and lock times across retries so the receipt accounts for contention.
TIMED_STEPS = ("merge", "branch_tests", "merge_tests", "lock_wait", "lock_held",
               "push")


def timing_cell(result: dict | None) -> str:
    """The receipt's landing-telemetry cell: each step's wall and CPU time,
    where the tests ran, and how many times `main` moving made them run
    again."""
    result = result or {}
    timing = result.get("timing") or {}
    parts = []
    for step in TIMED_STEPS:
        if step not in timing:
            continue
        label = step.replace("_", " ")
        if step.endswith("_tests") and "tested_under_lock" in result:
            label += (" (under the lock)" if result["tested_under_lock"]
                      else " (outside the lock)")
        parts.append(f"{label} {timing[step]['wall_s']:.1f} s "
                     f"({timing[step]['cpu_s']:.1f} s CPU)")
    if parts and "retests" in result:
        parts.append(f"re-tests {result['retests']}")
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


# Test the merge before taking the lock and recheck both tips inside it. See CONTRIBUTING.md.

# Attempts outside the live lock before the landing tests under it.
OUTSIDE_TRIES = 3

# Poll briefly because an uncontended final fast-forward holds the lock only briefly.
LAND_POLL_S = 0.5

# The tested commit is kept here in the job's tree between its test run and
# the fast-forward under the lock, so nothing can collect it meanwhile.
TESTED_REF = "refs/loam/tested"


def add_time(result: dict, step: str, wall: float, cpu: float) -> None:
    """Add one span to a step's total: a landing may wait for the lock, or
    test, more than once."""
    timing = result.setdefault("timing", {})
    was = timing.get(step) or {"wall_s": 0.0, "cpu_s": 0.0}
    timing[step] = {"wall_s": round(was["wall_s"] + wall, 2),
                    "cpu_s": round(was["cpu_s"] + cpu, 2)}


@contextmanager
def live_held(project: eng.Project, branch: str, result: dict, on_wait,
              say=None):
    """The project's live lock for one span, its wait and hold timed into
    ``lock_wait`` and ``lock_held`` (both summed over the landing), and the
    span's hold said as the lock is let go."""
    wall0, cpu0 = time.monotonic(), cpu_seconds()
    with loam_lock.live(project.live_lock, f"land {branch}", on_wait=on_wait,
                        poll=LAND_POLL_S):
        wall1, cpu1 = time.monotonic(), cpu_seconds()
        add_time(result, "lock_wait", wall1 - wall0, cpu1 - cpu0)
        try:
            yield
        finally:
            held = time.monotonic() - wall1
            add_time(result, "lock_held", held, cpu_seconds() - cpu1)
            if say:
                say(f"landing: let go of the live lock after {held:.1f} s "
                    f"(waited {wall1 - wall0:.1f} s)")


@contextmanager
def timed_add(result: dict, step: str):
    wall0, cpu0 = time.monotonic(), cpu_seconds()
    try:
        yield
    finally:
        add_time(result, step, time.monotonic() - wall0, cpu_seconds() - cpu0)


def canonical_ready(repo: Path, result: dict) -> bool:
    """Under the lock: the canonical checkout is on `main` and level with its
    remote. False, with the reason in ``result``, when it is not."""
    if (git(repo, "symbolic-ref", "--short", "HEAD", check=False).stdout.strip()
            != MAIN):
        result.update(reason="canonical", detail=(
            f"the canonical checkout {repo} is not on {MAIN}"))
        return False
    diverged = sync_with_origin(repo)
    if diverged:
        result.update(reason="diverged", detail=diverged)
        return False
    return True


def _prepare(project, repo, tree, branch, message, result, say, record, want,
             locked=False, *, require_tests: bool = False):
    """Merge the branch into `main` as it stands now and test the merged
    tree, touching nothing but the job's tree. Returns ``(main_sha, tip,
    merged)``: ``merged`` is None when the branch has nothing `main` lacks,
    and the whole return is None when the result is final (refused, a
    conflict, failing tests)."""
    result.pop("notes_only", None)
    refresh_main(repo, tree)
    main_sha = rev(repo, MAIN)
    replay = replay_if_rewritten(repo, tree, branch, record, main_sha, say)
    if replay:
        result.update(reason="rewritten", detail=(
            f"`{MAIN}` was rewritten since this job began, and replaying the "
            f"job's own commits onto it stopped: {replay}"))
        return None
    if settle_commits(want, repo, tree, branch, main_sha, say, result):
        result["reauthored"] = True
    tip = branch_tip(tree, branch)
    result["head"] = head_note(tree, branch)
    outside = outside_scope(project, tree, main_sha, tip)
    if outside:
        result.update(reason="scope", files=outside, detail=(
            f"{project.name} is the `{project.subdir}/` folder of this repository, "
            f"and its commits touch {len(outside)} path(s) outside it"))
        return None
    # Ancestry is asked of the tree: a clone has both commits, the canonical
    # repository does not have the branch's until the landing fetches it.
    if is_ancestor(tree, tip, main_sha):
        return main_sha, tip, None

    onto_branch(tree, branch, result)
    gated = gate_on(repo) and not project.pull_request
    if gated:
        big, _allows, _problems = deny_rules(repo)
        if big and big.search(message):
            # The landing's own merge message (the brief's title) is the
            # launcher's text, which no continuation can reword.
            say("landing: the brief's title names what the push gate denies; "
                "the merge says only the branch")
            message = f"Land {branch}"
    if is_ancestor(tree, main_sha, tip):
        merged, which = tip, "branch"   # a fast-forward: the tree is the merge
    else:
        say(f"landing: merging main ({main_sha[:7]})")
        with timed_add(result, "merge"):
            git(tree, "checkout", "--quiet", "--detach", main_sha)
            out = git(tree, "merge", "--no-ff", "--no-edit", "-m", message,
                      branch, check=False)
        if out.returncode != 0:
            conflicts = git(tree, "diff", "--name-only", "--diff-filter=U",
                            check=False).stdout.split()
            git(tree, "merge", "--abort", check=False)
            back_to_branch(tree, branch, main_sha, result, force=True)
            # Only unmerged paths give a continuation a conflict to resolve.
            result.update(reason="conflict" if conflicts else "error",
                          files=conflicts,
                          detail=(out.stdout + out.stderr).strip()[-800:],
                          main=main_sha)
            return None
        merged, which = rev(tree, "HEAD"), "merge"
    if gated:
        # What the push would refuse is refused here, before `main` has it
        # Only what this landing adds is read: a commit
        # already on `main` is the push's to report, not this job's.
        held = push_gate(repo, main_sha, head=merged, objects=tree,
                         expected=(want["name"], want["email"]))
        if held:
            back_to_branch(tree, branch, merged, result, force=True)
            result.update(reason="gate", gate=held, main=main_sha, detail=(
                "; ".join(held[:5])
                + (f"; and {len(held) - 5} more" if len(held) > 5 else "")))
            return None
    if not require_tests and is_notes_only(project, tree, main_sha, tip, merged):
        say(f"landing: skipping tests for {merged[:7]} "
            f"(changes only {memory_prefix(project)})")
        result["notes_only"] = True
        result["test"] = None
        git(tree, "update-ref", TESTED_REF, merged)
        back_to_branch(tree, branch, merged, result)
        return main_sha, tip, merged
    step = "branch_tests" if which == "branch" else "merge_tests"
    say(f"landing: testing the {which} ({merged[:7]})"
        + ("" if locked else ", outside the live lock"))
    with timed_add(result, step):
        passed, command, out_text = run_tests(project, tree)
    result["test"] = {"command": command, "passed": passed, "tail": out_text,
                      "tree": which}
    if passed is None:
        back_to_branch(tree, branch, merged, result, force=True)
        result.update(reason="no_tests")
        return None
    if not passed:
        back_to_branch(tree, branch, merged, result, force=True)
        if which == "branch":
            result.update(reason="branch_tests")
            return None
        # Which tree is broken is what the continuation needs: the branch is
        # tested now, and only now that the merge has failed.
        say("landing: the merge fails its tests; testing the branch alone")
        with timed_add(result, "branch_tests"):
            b_passed, b_command, b_out = run_tests(project, tree)
        if b_passed is False:
            result["test"] = {"command": b_command, "passed": False,
                              "tail": b_out, "tree": "branch"}
            result.update(reason="branch_tests")
        else:
            result.update(reason="merged_tests", main=main_sha)
        return None
    git(tree, "update-ref", TESTED_REF, merged)
    back_to_branch(tree, branch, merged, result)
    return main_sha, tip, merged


def _finish_nothing(project, record, keep_tree, result, main_sha) -> None:
    """Nothing on the branch that main does not have -- either the job
    committed nothing, or what it committed is already on main because it
    landed earlier (a mid-job `--land`, or an earlier job of this tree). The
    two read differently, and the second is a landing. Under the lock: the
    memory is merged into the canonical checkout."""
    earlier = landed_earlier(project, record)
    result["memory"] = merge_memory_back(project, record)
    if not keep_tree:
        remove_tree(project, record, delete_branch=True)
    if earlier:
        result.update(state="landed_earlier", sha=earlier)
    else:
        result.update(state="nothing", sha=main_sha)


def _finish_landed(project, repo, tree, branch, keep_tree, result, say,
                   record, cancelled, tip, merged, expected) -> None:
    """Under the lock, with `main` where the tested merge was made: move the
    branch and then `main` to the tested commit, merge the memory, push.
    A commit made on the branch since ``tip`` was tested is carried onto
    the merge (``carry_onto``) instead of stopping the fast-forward."""
    now_tip = branch_tip(tree, branch)
    if now_tip and now_tip != tip and is_ancestor(tree, tip, now_tip) \
            and not is_ancestor(tree, now_tip, merged):
        since = git(tree, "rev-list", "--reverse", "--no-merges", now_tip,
                    f"^{tip}", check=False).stdout.split()
        if not carry_onto(tree, branch, since, merged, result):
            # Kept under refs/loam/kept/; the branch takes what landed.
            git(tree, "checkout", "--quiet", "--detach", merged)
            git(tree, "update-ref", f"refs/heads/{branch}", merged)
            git(tree, "checkout", "--quiet", branch)
    elif merged != tip:
        git(tree, "merge", "--quiet", "--ff-only", merged)
    git(tree, "update-ref", "-d", TESTED_REF, check=False)
    if stop_for_cancel(cancelled, result, say):
        # The branch may be on the merge; the branch is what is kept.
        git(tree, "checkout", "--quiet", "-f", branch, check=False)
        return
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
        return
    result.update(state="landed", sha=merged)
    result["memory"] = merge_memory_back(project, record)
    if keep_tree and result["memory"]:
        # The tree goes on; what it has now is already in the canonical copy.
        shutil.rmtree(memory_base(record), ignore_errors=True)
        shutil.copytree(tree / "memory", memory_base(record), symlinks=True)
    if has_remote(repo) and local_main(repo):
        result["local_only"] = True     # a release install's own main
    elif has_remote(repo):
        with timed(result, "push"):
            result["pushed"] = push_origin(project, expected=expected)
        now = rev(repo, MAIN)
        if now and now != merged:
            # The push re-authored landed commits (``reauthor_landed``): the
            # same tree under new commits, and that is what landed.
            say(f"landing: re-authored the commits origin lacked "
                f"({merged[:7]} -> {now[:7]})")
            result.update(sha=now, reauthored=True)
            if keep_tree and branch_tip(tree, branch) == merged:
                git(tree, "fetch", "--quiet", "--no-tags", str(repo), MAIN)
                git(tree, "update-ref", f"refs/heads/{branch}", now, merged)
    backup = push_backup(repo)
    if backup is not None:
        result["backup"] = backup
    if not keep_tree:
        remove_tree(project, record, delete_branch=True)


def _land_tested(project, repo, tree, branch, message, keep_tree, result,
                 say, record, cancelled, lock, finish, *,
                 require_tests: bool = False) -> None:
    """A `main`-merging landing; see the section above."""
    result["retests"] = 0
    result["tested_under_lock"] = False
    want = job_identity(project, repo)
    expected = want["name"], want["email"]
    for attempt in range(OUTSIDE_TRIES + 1):
        if attempt == OUTSIDE_TRIES:
            # Lost every race: today's order, under the lock throughout.
            say(f"landing: main moved under {OUTSIDE_TRIES} test runs in a "
                f"row; testing under the live lock")
            result["tested_under_lock"] = True
            with lock():
                say("landing: holding the live lock")
                if stop_for_cancel(cancelled, result, say) \
                        or not canonical_ready(repo, result):
                    return
                ready = _prepare(project, repo, tree, branch, message, result,
                                 say, record, want, locked=True,
                                 require_tests=require_tests)
                if ready is None:
                    return
                main_sha, tip, merged = ready
                if merged is None:
                    _finish_nothing(project, record, keep_tree, result, main_sha)
                else:
                    _finish_landed(project, repo, tree, branch, keep_tree,
                                   result, say, record, cancelled, tip, merged, expected)
                finish()
            return

        if stop_for_cancel(cancelled, result, say):
            return
        ready = _prepare(project, repo, tree, branch, message, result, say,
                         record, want, require_tests=require_tests)
        if ready is None:
            return
        main_sha, tip, merged = ready
        with lock():
            say("landing: holding the live lock")
            if stop_for_cancel(cancelled, result, say):
                git(tree, "update-ref", "-d", TESTED_REF, check=False)
                return
            if not canonical_ready(repo, result):
                git(tree, "update-ref", "-d", TESTED_REF, check=False)
                return
            now, tip_now = rev(repo, MAIN), branch_tip(tree, branch)
            if now == main_sha and tip_now == tip:
                if merged is None:
                    _finish_nothing(project, record, keep_tree, result, main_sha)
                else:
                    _finish_landed(project, repo, tree, branch, keep_tree,
                                   result, say, record, cancelled, tip, merged, expected)
                finish()
                return
        # Lost the race: the lock is already let go.
        git(tree, "update-ref", "-d", TESTED_REF, check=False)
        moved = (f"main moved ({main_sha[:7]} -> {(now or '?')[:7]})"
                 if now != main_sha else
                 f"the branch moved ({tip[:7]} -> {(tip_now or '?')[:7]})")
        if merged is not None and not result.get("notes_only"):
            result["retests"] += 1
        say(f"landing: {moved} while it was tested; merging again")


def read_only_result(project: eng.Project, record: dict, *,
                     keep_tree: bool = False) -> dict:
    """Report discarded commits and merge back untracked memory for a read-only job.
    Keep the tree for a mid-job landing and remove it on completion."""
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
                "(`test_command` in the project's registry record), and nothing "
                "lands untested",
    "canonical": "the canonical checkout would not take it",
    "diverged": "the canonical main and its remote have diverged",
    "rewritten": "main was rewritten since the job began, and its own commits "
                 "could not be replayed onto the new main",
    "gate": "the push gate would refuse its commits (an identity that is not "
            "the checkout's, or a name the deny list guards), and nothing "
            "lands on main that cannot be pushed",
    "error": "a step of the landing failed",
    "cancelled": "the job was cancelled",
    "scope": "its commits change files outside the folder of the repository "
             "this project is, and a folder project changes that folder alone",
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
        "scope": (f"Your branch `{branch}` did not land: it changes {files}, "
                  f"outside the folder of the repository this project is. Take "
                  f"those changes out of your branch (revert them in a new "
                  f"commit) and say in your receipt what they were for: a change "
                  f"there is the platform's, briefed to Loam. The launcher lands "
                  f"the branch when you finish."),
        "gate": (f"Your branch `{branch}` did not land: the push gate would "
                 f"refuse it, and it reads every commit, not only the last: "
                 f"{result.get('detail') or '?'}. A later commit that removes "
                 f"the name does not help. Rewrite the branch's own commits "
                 f"(`git rebase -i` is unavailable: `git reset --soft main` "
                 f"and recommit, or `git commit --amend` for the last one) so "
                 f"none of them adds, touches or says it: a persona's or the "
                 f"owner's name in a test becomes a placeholder, as the "
                 f"release scrub does; a message is reworded. Do not push and "
                 f"do not touch `main`; the launcher lands the branch when "
                 f"you finish."),
        "rewritten": (f"Your branch `{branch}` did not land: `main` was "
                      f"rewritten after your branch took commits from it, and "
                      f"replaying your own commits onto the new `main` "
                      f"stopped: {result.get('detail') or '?'}. In your tree, "
                      f"make a branch from `main` (the new one), cherry-pick "
                      f"only the commits that are your job's own onto it, "
                      f"resolve, run the tests, and move `{branch}` to the "
                      f"result (on `{branch}`, `git reset --hard` to it). None of the old `main`'s "
                      f"commits may come back. Do not push and do not touch "
                      f"`main`; the launcher lands the branch when you "
                      f"finish."),
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


def note_section(result: dict) -> str:
    """What became of the engineer's memory note, when it was not simply
    part of what landed (``land``)."""
    out = ""
    if result.get("memory_committed"):
        out += (" The engineer's memory note was uncommitted; the landing "
                "committed it.")
    squashed = result.get("squashed")
    if squashed:
        out += (f" The push gate held the branch only for its memory note or "
                f"its commits' messages ({'; '.join(squashed.get('gate', [])[:3])}), "
                f"so its change landed as one commit without them; the "
                f"branch as made is `{squashed['tip'][:7]}`.")
    held = result.get("memory_held")
    if held:
        why = REASONS.get(held.get("reason"), held.get("reason") or "it did not land")
        where = (f"kept as `{held['patch']}`" if held.get("patch")
                 else "**not kept**: its patch could not be written")
        out += (f" **The engineer's memory note did not land** ({why}"
                + (f": {held['detail'][:300]}" if held.get("detail") else "")
                + f"); it is {where}, and no continuation is sent for it.")
    return out


def receipt_section(project: eng.Project, job_id: str, result: dict | None) -> str:
    """The launcher's paragraph on the landing, for the receipt."""
    if result is None:
        return "**Landing.** None attempted."
    state, branch = result["state"], result["branch"]
    test = result.get("test") or {}
    tested = (f" Tested with `{test['command']}` on the "
              f"{'merged tree' if test.get('tree') == 'merge' else 'branch'}, "
              f"which is the merge when `main` had not moved."
              if test.get("command") and test.get("passed") else
              f" Test suite skipped (changes only `{memory_prefix(project)}`)."
              if result.get("notes_only") else "")
    if state == "landed":
        pushed = result.get("pushed")
        push = ("Kept on this install: its `main` is its own, so nothing is "
                "pushed upstream (`loam report bug --patch` offers a fix to "
                "Loam's makers)." if result.get("local_only") else
                "Pushed to origin." if pushed is True else
                "No remote to push to." if pushed is None else
                f"**The push failed**: `{pushed}` — `main` moved on this box "
                f"and origin is behind it; the recovery sweep tries again.")
        backup = result.get("backup")
        if backup is True:
            push += f" Backed up off the box to `{BACKUP}`."
        elif backup:
            push += (f" **The backup push failed**: `{backup}`; the next "
                     f"landing or recovery sweep tries again.")
        if result.get("reauthored"):
            push += (" The job's own commits were made again under the name "
                     "below, without a provider's attribution lines (same "
                     "trees and dates), so their hashes changed.")
        if result.get("signed_kept"):
            push += (f" Signed commits were kept as made: "
                     f"{', '.join(result['signed_kept'][:5])}.")
        if result.get("attribution"):
            push += f" Attribution: {result['attribution']}"
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
                + (f" Session services: {result['session_services']}" if result.get("session_services") else "")
                + note_section(result) + release_section(result.get("release")))
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
        text += note_section(result)
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
    if state == "nothing" and result.get("memory_held"):
        return (f"**Landing: nothing landed** — `{branch}` added only the "
                f"engineer's memory note, and it did not land.{head} The "
                f"worktree is removed." + note_section(result)
                + release_section(result.get("release")))
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
