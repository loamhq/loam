"""The gate in front of `origin`, and a landing after `main` was rewritten
(since 2026-11-16; lib/loam_worktree.py `push_gate`, `push_origin`,
`replay_if_rewritten`).

Asserted against real git repositories in a temp directory: a bare `origin`,
a canonical checkout, job trees made by lib/loam_worktree.py as
bin/engineer-job makes them. Every check reads what is observable: where
`main` is on origin, what the landing's result and receipt say, which
identities the landed commits carry.

  - with no `loam.pushGate`, a landing pushes as it always has, whoever the
    commits are by (a project's own repository has no gate);
  - with the gate on, a commit made by hand under another identity is held,
    `main` still lands here, and the receipt says why; once origin has it no
    longer matters;
  - a job tree from before the checkout's identity was set lands its commits
    re-authored as the checkout's identity (same tree and dates), and a
    commit a landing already put on `main` that way is re-authored by the
    next push, but only when nothing but landings moved `main`;
  - a deny-list match in an added line, a path or a message is held; an
    `allow` line lets one file's match through; a missing list holds all;
  - a job begun before `main` was rewritten lands only its own commits,
    replayed onto the new `main` as the checkout's identity, and none of
    the old history comes back.

Run: python3 tests/test_push_gate.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_engineer as eng  # noqa: E402
import loam_lock  # noqa: E402
import loam_worktree as wt  # noqa: E402

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:66} {got!r}"
          + ("" if ok else f"\n      wanted {want!r}"))


def git(repo: Path, *args: str, who: str | None = None) -> str:
    ident = ["-c", f"user.name={who}", "-c", f"user.email={who}@x"] if who else []
    return subprocess.run(["git", "-C", str(repo), *ident, *args],
                          capture_output=True, text=True, check=True).stdout.strip()


root = Path(tempfile.mkdtemp(prefix="loam-gate-test-"))
saved = (wt.WORKTREES, loam_lock.LOCK_DIR)
wt.WORKTREES = root / "worktrees"
loam_lock.LOCK_DIR = root / "locks"
os.environ.pop(loam_lock.LIVE_HELD_VAR, None)
NAME, EMAIL = "Demo engineer", "engineer+demo@noreply.local"


def make(name: str) -> tuple[eng.Project, Path]:
    origin = root / f"{name}.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    checkout = root / name
    subprocess.run(["git", "init", "-q", "-b", "main", str(checkout)], check=True)
    git(checkout, "config", "user.name", NAME)
    git(checkout, "config", "user.email", EMAIL)
    (checkout / "a.txt").write_text("a\n")
    git(checkout, "add", "-A")
    git(checkout, "commit", "-qm", "start")
    git(checkout, "remote", "add", "origin", str(origin))
    git(checkout, "push", "-q", "origin", "main")
    git(checkout, "fetch", "-q", "origin")
    project = eng.Project(name, checkout, root / f"{name}-jobs", platform=False,
                          record={"name": name, "checkout": str(checkout),
                                  "test_command": "true"})
    project.jobs.mkdir()
    return project, origin


def start(project: eng.Project, job_id: str) -> dict:
    job_dir = project.jobs / job_id
    job_dir.mkdir()
    return wt.setup(project, job_id, job_dir, None)


def commit(record: dict, name: str, text: str, msg: str | None = None,
           who: str | None = None) -> None:
    tree = Path(record["path"])
    (tree / name).parent.mkdir(parents=True, exist_ok=True)
    (tree / name).write_text(text)
    git(tree, "add", "-A")
    git(tree, "commit", "-qm", msg or f"add {name}", who=who)


def land(project: eng.Project, record: dict) -> tuple[dict, str]:
    result = wt.land(project, record, f"Land {record['branch']}")
    return result, wt.receipt_section(project, record["job"], result)


def origin_main(origin: Path) -> str:
    return git(origin, "rev-parse", "main")


try:
    # -- No gate: a project's own repository, as before.
    proj, origin = make("plain")
    r = start(proj, "20261116-100000-00000001")
    commit(r, "b.txt", "Somebody\n", who="someone-else")
    result, _ = land(proj, r)
    check("no gate: another identity's commit lands and is pushed",
          (result["state"], result["pushed"], origin_main(origin) == git(proj.root, "rev-parse", "main")),
          ("landed", True, True))

    # -- The gate on: identity.
    proj, origin = make("gated")
    deny = root / "deny"
    deny.write_text("# names\n\\bSecretname\\b\nbox-only-host\nallow docs/ok.md Secretname\n")
    git(proj.root, "config", "loam.pushGate", "true")
    git(proj.root, "config", "loam.pushDeny", str(deny))
    before = origin_main(origin)
    # A person's commit, made by hand on the checkout's main: held, and a
    # landing after it is held too (main moved other than by a landing).
    (proj.root / "hand.txt").write_text("by hand\n")
    git(proj.root, "add", "-A")
    git(proj.root, "commit", "-qm", "by hand", who="someone-else")
    hand = git(proj.root, "rev-parse", "main")
    r = start(proj, "20261116-100000-00000002")
    commit(r, "b.txt", "b\n")
    result, text = land(proj, r)
    check("gate: a hand commit by another identity is held, main lands here",
          (result["state"], isinstance(result["pushed"], str), origin_main(origin)),
          ("landed", True, before))
    check("gate: the receipt says the push was held, and by what",
          "held by the push gate" in text and "someone-else" in text, True)
    check("gate: the sweep's push is held the same way",
          wt.push_origin(proj.root).startswith("held by the push gate"), True)
    check("gate: the hand commit is never re-authored",
          git(proj.root, "merge-base", "--is-ancestor", hand, "main") == "", True)
    # Taken by hand (the person decided): origin has it, so it is not asked again.
    git(proj.root, "push", "-q", "origin", "main")

    # A job tree made before the checkout's identity was set: its commits
    # are the engineer's, re-authored at the landing, and pushed.
    r = start(proj, "20261116-100000-00000012")
    git(Path(r["path"]), "config", "user.name", "old-box")
    git(Path(r["path"]), "config", "user.email", "old@box")
    commit(r, "old-tree.txt", "from an old tree\n")
    first = git(Path(r["path"]), "rev-parse", "HEAD")
    first_tree = git(Path(r["path"]), "rev-parse", "HEAD^{tree}")
    first_date = git(Path(r["path"]), "log", "-1", "--format=%ad")
    result, text = land(proj, r)
    check("old tree: its commit is re-authored and pushed",
          (result["state"], result["pushed"], result.get("reauthored"),
           git(proj.root, "log", "-1", "--format=%an <%ae>|%cn <%ce>|%s", "main"),
           origin_main(origin) == git(proj.root, "rev-parse", "main")),
          ("landed", True, True, f"{NAME} <{EMAIL}>|{NAME} <{EMAIL}>|add old-tree.txt", True))
    check("old tree: same tree and author date, a new hash; the receipt says so",
          (git(proj.root, "rev-parse", "main^{tree}") == first_tree,
           git(proj.root, "log", "-1", "--format=%ad", "main") == first_date,
           git(proj.root, "rev-parse", "main") != first, "re-authored" in text),
          (True, True, True, True))

    # A commit a landing already put on main under another identity (the
    # 2026-11-16 case: landed by a tree from before the gate, then held): the
    # next push re-authors it, because only landings moved main since origin.
    tree = root / "pre-gate-tree"
    subprocess.run(["git", "clone", "-q", str(proj.root), str(tree)], check=True)
    (tree / "held.txt").write_text("held\n")
    git(tree, "add", "-A")
    git(tree, "commit", "-qm", "landed while the gate came on", who="old-box")
    git(proj.root, "fetch", "-q", str(tree), "main")
    git(proj.root, "merge", "--quiet", "--ff-only", "FETCH_HEAD")
    held = git(proj.root, "rev-parse", "main")
    check("landed earlier: the gate would hold it",
          bool(wt.push_gate(proj.root, origin_main(origin))), True)
    pushed = wt.push_origin(proj.root)
    check("landed earlier: the sweep's push re-authors it and pushes",
          (pushed, git(proj.root, "log", "-1", "--format=%an|%s", "main"),
           origin_main(origin) == git(proj.root, "rev-parse", "main"),
           git(proj.root, "rev-parse", "main^{tree}") == git(proj.root, "rev-parse", f"{held}^{{tree}}"),
           git(proj.root, "status", "--porcelain")),
          (True, f"{NAME}|landed while the gate came on", True, True, ""))
    check("landed earlier: a second push changes nothing",
          (wt.push_origin(proj.root), wt.reauthor_landed(proj.root, origin_main(origin))),
          (True, None))

    # Identity right, deny list clean: pushed.
    r = start(proj, "20261116-100000-00000003")
    commit(r, "c.txt", "plain words\n")
    result, _ = land(proj, r)
    check("gate: its own identity, nothing denied: pushed",
          (result["pushed"], origin_main(origin)), (True, git(proj.root, "rev-parse", "main")))

    # A denied word in an added line.
    pushed = origin_main(origin)
    r = start(proj, "20261116-100000-00000004")
    commit(r, "d.txt", "hello Secretname\n")
    result, text = land(proj, r)
    check("gate: a denied word in an added line is held",
          (isinstance(result["pushed"], str), "d.txt adds 'Secretname'" in text,
           origin_main(origin)), (True, True, pushed))
    # ...and fixed in a later commit is still held: the history keeps it.
    r = start(proj, "20261116-100000-00000005")
    commit(r, "d.txt", "hello\n")
    result, _ = land(proj, r)
    check("gate: removing it later does not unpublish it; still held",
          isinstance(result["pushed"], str), True)
    git(proj.root, "reset", "-q", "--hard", pushed)   # the person's fix: drop it

    # An allowed file, a path, a message.
    r = start(proj, "20261116-100000-00000006")
    commit(r, "docs/ok.md", "Secretname is allowed here\n")
    result, _ = land(proj, r)
    check("gate: an `allow` line lets that file's match through",
          result["pushed"], True)
    pushed = origin_main(origin)
    r = start(proj, "20261116-100000-00000007")
    commit(r, "box-only-host/x.txt", "x\n")
    result, text = land(proj, r)
    check("gate: a denied path is held", "path box-only-host/x.txt" in text, True)
    git(proj.root, "reset", "-q", "--hard", pushed)
    r = start(proj, "20261116-100000-00000008")
    commit(r, "e.txt", "e\n", msg="mention Secretname in the message")
    result, text = land(proj, r)
    check("gate: a denied word in a message is held",
          "its message names 'Secretname'" in text, True)
    git(proj.root, "reset", "-q", "--hard", pushed)
    git(proj.root, "config", "loam.pushDeny", str(root / "gone"))
    check("gate: a deny list that is missing holds everything",
          any("is missing" in p for p in wt.push_gate(proj.root, None)), True)
    git(proj.root, "config", "loam.pushDeny", str(deny))

    # -- main rewritten under a running job.
    proj, origin = make("rewritten")
    pre = start(proj, "20261116-100000-0000000a")
    commit(pre, "old.txt", "private note\n")
    result, _ = land(proj, pre)
    git(origin, "update-ref", "refs/heads/main", git(proj.root, "rev-parse", "main~1"))
    git(proj.root, "fetch", "-q", "origin")   # origin never had the old commit
    late = start(proj, "20261116-100000-0000000b")       # begins on the old history
    commit(late, "mine.txt", "the job's own work\n")
    # The rewrite: main without the old commit, as a different commit.
    root_commit = git(proj.root, "rev-list", "--max-parents=0", "main")
    git(proj.root, "checkout", "-q", "-B", "main", root_commit)
    (proj.root / "clean.txt").write_text("rewritten\n")
    git(proj.root, "add", "-A")
    git(proj.root, "commit", "-qm", "the rewrite")
    rewritten = git(proj.root, "rev-parse", "main")
    result, text = land(proj, late)
    files = git(proj.root, "ls-tree", "--name-only", "main").split()
    check("rewritten: the job lands on the new main",
          (result["state"], git(proj.root, "merge-base", "--is-ancestor", rewritten,
                                "main") == ""), ("landed", True))
    check("rewritten: its own file came, the old history did not",
          ("mine.txt" in files, "old.txt" in files, "clean.txt" in files),
          (True, False, True))
    check("rewritten: the replayed commit is the checkout's identity",
          git(proj.root, "show", "-s", "--format=%s|%an <%ae>|%cn <%ce>", "main"),
          f"add mine.txt|{NAME} <{EMAIL}>|{NAME} <{EMAIL}>")
    check("rewritten: the landing said why, and origin took the new main",
          (result.get("reason"), result["pushed"], origin_main(origin)),
          (None, True, git(proj.root, "rev-parse", "main")))
    check("rewritten: main is one commit past the rewrite (a fast-forward)",
          git(proj.root, "rev-list", "--count", f"{rewritten}..main"), "1")
finally:
    wt.WORKTREES, loam_lock.LOCK_DIR = saved
    shutil.rmtree(root, ignore_errors=True)

print(f"\n{'all passed' if not fails else f'{fails} failed'}")
sys.exit(1 if fails else 0)
