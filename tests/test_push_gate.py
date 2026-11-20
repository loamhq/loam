"""Check outgoing identities and private names, plus job replay after a rewrite of main."""

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
           git(proj.root, "rev-parse", "main") != first, "made again" in text),
          (True, True, True, True))

    # Only landing fast-forwards make an unpublished main eligible for re-authoring.
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

    # A denied word in an added line: refused at the landing, before main
    # has it, with a continuation that says what to rewrite.
    pushed = origin_main(origin)
    here = git(proj.root, "rev-parse", "main")
    r = start(proj, "20261116-100000-00000004")
    commit(r, "d.txt", "hello Secretname\n")
    result, text = land(proj, r)
    check("landing gate: a denied word in an added line is not landed",
          (result["state"], result["reason"], "d.txt adds 'Secretname'" in text,
           git(proj.root, "rev-parse", "main"), origin_main(origin)),
          ("not_landed", "gate", True, here, pushed))
    brief = wt.continuation_brief(proj, r["job"], result)
    check("landing gate: the continuation says to rewrite the branch",
          ("push gate" in brief and "d.txt adds 'Secretname'" in brief
           and "placeholder" in brief), True)
    # ...and fixed in a later commit on the branch is still refused: the
    # history keeps it.
    commit(r, "d.txt", "hello\n")
    result, _ = land(proj, r)
    check("landing gate: removing it in a later commit is still refused",
          (result["state"], result["reason"]), ("not_landed", "gate"))
    # The job rewrites its own commit: lands and is pushed.
    git(Path(r["path"]), "reset", "-q", "--soft", "main")
    git(Path(r["path"]), "commit", "-qm", "add d.txt")
    result, _ = land(proj, r)
    check("landing gate: the branch rewritten without it lands and is pushed",
          (result["state"], result["pushed"]), ("landed", True))
    # A commit already on main (put there before the landing asked) is the
    # push's to hold, not a later job's: that job lands, its push is held.
    pushed = origin_main(origin)
    (proj.root / "f.txt").write_text("hello Secretname\n")
    git(proj.root, "add", "-A")
    git(proj.root, "commit", "-qm", "on main before the landing gate")
    r = start(proj, "20261116-100000-00000005")
    commit(r, "g.txt", "g\n")
    result, text = land(proj, r)
    check("gate: an older held commit holds the push, not the next landing",
          (result["state"], isinstance(result["pushed"], str),
           "f.txt adds 'Secretname'" in text, origin_main(origin)),
          ("landed", True, True, pushed))
    git(proj.root, "reset", "-q", "--hard", pushed)   # the person's fix: drop it

    # A brief's title that names a denied word: the merge says only the branch.
    r = start(proj, "20261116-100000-00000009")
    commit(r, "h.txt", "h\n")
    (proj.root / "i.txt").write_text("i\n")          # main moves: a real merge
    git(proj.root, "add", "-A")
    git(proj.root, "commit", "-qm", "main moves")
    git(proj.root, "push", "-q", "origin", "main")
    result = wt.land(proj, r, f"Land {r['branch']}: the Secretname page")
    check("landing gate: a denied title is left out of the merge message",
          (result["state"], result["pushed"],
           git(proj.root, "log", "-1", "--format=%s", "main")),
          ("landed", True, f"Land {r['branch']}"))

    # An allowed file, a path, a message.
    r = start(proj, "20261116-100000-00000006")
    commit(r, "docs/ok.md", "Secretname is allowed here\n")
    result, _ = land(proj, r)
    check("gate: an `allow` line lets that file's match through",
          result["pushed"], True)
    r = start(proj, "20261116-100000-00000007")
    commit(r, "box-only-host/x.txt", "x\n")
    result, text = land(proj, r)
    check("landing gate: a denied path is not landed",
          (result["reason"], "path box-only-host/x.txt" in text), ("gate", True))
    r = start(proj, "20261116-100000-00000008")
    commit(r, "e.txt", "e\n", msg="mention Secretname in the message")
    result, text = land(proj, r)
    # A denied commit message can be replaced while preserving the work.
    check("landing gate: a denied word in a message never reaches main or origin",
          (result["state"], "its message names 'Secretname'" in text,
           "Secretname" in git(proj.root, "log", "--format=%B", "main")),
          ("landed", True, False))
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

    # Reload the job record for each landing, as separate launcher processes do.
    def land_from_disk(project, record, keep_tree=False):
        fresh = wt.read_record(project.jobs / record["job"])
        return wt.land(project, fresh, f"Land {record['branch']}",
                       keep_tree=keep_tree)

    def rewrite_main(project, since: str, old: str, new: str) -> None:
        """A filter-branch over `since..main` rewording one message, as a
        scrub before a push does; origin is put at the result."""
        subprocess.run(["git", "-C", str(project.root), "filter-branch", "-f",
                        "--msg-filter", f"sed 's/{old}/{new}/'",
                        f"{since}..main"], check=True, capture_output=True,
                       env=dict(os.environ, FILTER_BRANCH_SQUELCH_WARNING="1"))
        git(project.root, "update-ref", "-d", "refs/original/refs/heads/main")
        git(project.root, "push", "-q", "-f", "origin", "main")

    def in_main(project, sha: str) -> bool:
        return subprocess.run(["git", "-C", str(project.root), "merge-base",
                               "--is-ancestor", sha, "main"]).returncode == 0

    # (a) The branch took a commit from main by a mid-job landing; main was
    # rewritten after the job's base; the final landing merges.
    proj, origin = make("predates")
    git(proj.root, "config", "loam.pushGate", "true")
    base = git(proj.root, "rev-parse", "main")
    job = start(proj, "20261116-100000-0000000c")
    other = start(proj, "20261116-100000-0000000d")
    commit(other, "x.txt", "x\n", msg="unscrubbed x")
    land(proj, other)
    old_x = git(proj.root, "rev-parse", "main")
    commit(job, "j1.txt", "one\n")
    first = land_from_disk(proj, job, keep_tree=True)
    rewrite_main(proj, base, "unscrubbed", "scrubbed")
    commit(job, "j2.txt", "two\n")
    result = land_from_disk(proj, job)
    files = git(proj.root, "ls-tree", "--name-only", "main").split()
    check("predates (a): the mid-job landing landed",
          first["state"], "landed")
    check("predates (a): the final landing lands and pushes",
          (result["state"], result["pushed"],
           origin_main(origin) == git(proj.root, "rev-parse", "main")),
          ("landed", True, True))
    check("predates (a): the rewritten commit does not come back",
          (in_main(proj, old_x),
           git(proj.root, "log", "--format=%s", "main").count("unscrubbed x"),
           git(proj.root, "log", "--format=%s", "main").count("scrubbed x")),
          (False, 0, 1))
    check("predates (a): the job's own work is all there",
          ("j1.txt" in files, "j2.txt" in files, "x.txt" in files), (True, True, True))

    # (b) The exact sequence: the base itself is rewritten, a mid-job landing
    # replays the branch (and the record keeps the old base), the job lands
    # mid-job again, main is rewritten a second time, the job lands.
    proj, origin = make("twice")
    git(proj.root, "config", "loam.pushGate", "true")
    (proj.root / "b.txt").write_text("b\n")
    git(proj.root, "add", "-A")
    git(proj.root, "commit", "-qm", "base one")
    git(proj.root, "push", "-q", "origin", "main")
    root_commit = git(proj.root, "rev-list", "--max-parents=0", "main")
    job = start(proj, "20261116-100000-0000000e")
    commit(job, "k1.txt", "one\n")
    rewrite_main(proj, root_commit, "base one", "base 1")        # rewrite 1
    first = land_from_disk(proj, job, keep_tree=True)
    commit(job, "k2.txt", "two\n", msg="unscrubbed k2")
    second = land_from_disk(proj, job, keep_tree=True)
    old_k2 = git(proj.root, "rev-parse", "main")
    rewrite_main(proj, root_commit, "unscrubbed", "scrubbed")     # rewrite 2
    commit(job, "k3.txt", "three\n")
    result = land_from_disk(proj, job)
    subjects = git(proj.root, "log", "--format=%s", "main").splitlines()
    check("predates (b): the record still names the first base",
          (wt.read_record(proj.jobs / job["job"])["base"] == job["base"],
           in_main(proj, job["base"])), (True, False))
    check("predates (b): both mid-job landings landed",
          (first["state"], second["state"]), ("landed", "landed"))
    check("predates (b): the final landing lands and pushes",
          (result["state"], result["pushed"]), ("landed", True))
    check("predates (b): nothing from before either rewrite comes back",
          (in_main(proj, old_k2), "unscrubbed k2" in subjects, "base one" in subjects),
          (False, False, False))
    check("predates (b): main is the rewritten main plus the job's last commit",
          subjects, ["add k3.txt", "scrubbed k2", "add k1.txt", "base 1", "start"])

    def subjects_of(project) -> list[str]:
        return git(project.root, "log", "--format=%s", "main").splitlines()

    def plan_now(project, record):
        """The dry check: what the landing's replay will do, read before it."""
        tree = Path(record["path"])
        git(tree, "fetch", "-q", "origin", "+main:main")
        if not hasattr(wt, "replay_plan"):
            return "no dry check"     # older checkout without replay_plan
        fresh = wt.read_record(project.jobs / record["job"])
        plan = wt.replay_plan(project.root, tree, fresh, git(tree, "rev-parse", "main"),
                              git(tree, "rev-parse", record["branch"]))
        return {k: [git(tree, "log", "-1", "--format=%s", c) for c in plan[k]]
                for k in ("commits", "skipped")} if plan else None

    # A missing reflog leaves the recorded base as the fallback for replay.
    proj, origin = make("stuck")
    git(proj.root, "config", "loam.pushGate", "true")
    (proj.root / "b.txt").write_text("b\n")
    git(proj.root, "add", "-A")
    git(proj.root, "commit", "-qm", "unscrubbed base")
    git(proj.root, "push", "-q", "origin", "main")
    root_commit = git(proj.root, "rev-list", "--max-parents=0", "main")
    job = start(proj, "20261116-100000-0000000f")
    commit(job, "m1.txt", "one\n", msg="unscrubbed m1")
    first = land_from_disk(proj, job, keep_tree=True)
    rewrite_main(proj, root_commit, "unscrubbed", "scrubbed")   # the base too
    git(proj.root, "reflog", "expire", "--expire=now", "--all")
    commit(job, "m2.txt", "two\n")
    plan = plan_now(proj, job)
    result = land_from_disk(proj, job)
    check("stuck (c): the mid-job landing landed", first["state"], "landed")
    check("stuck (c): the dry check names what the replay skips",
          plan, {"commits": ["add m2.txt"], "skipped": ["unscrubbed m1"]})
    check("stuck (c): the final landing lands and pushes",
          (result["state"], result.get("reason"), result["pushed"]),
          ("landed", None, True))
    check("stuck (c): main is the rewritten main plus the job's new commit",
          subjects_of(proj), ["add m2.txt", "scrubbed m1", "scrubbed base", "start"])

    # (d) The reflog kept, and main already has one of the job's later
    # changes by another route (another job landed the same patch) before
    # the rewrite: the replay skips it and lands the rest.
    proj, origin = make("same-patch")
    git(proj.root, "config", "loam.pushGate", "true")
    job = start(proj, "20261116-100000-00000010")
    commit(job, "n1.txt", "one\n", msg="unscrubbed n1")
    land_from_disk(proj, job, keep_tree=True)
    commit(job, "fix.txt", "the same fix\n", msg="the fix, by this job")
    commit(job, "n3.txt", "three\n")
    other = start(proj, "20261116-100000-00000011")
    commit(other, "fix.txt", "the same fix\n", msg="the fix, by another job")
    land(proj, other)
    rewrite_main(proj, job["base"], "unscrubbed", "scrubbed")
    rewritten = git(proj.root, "rev-parse", "main")
    plan = plan_now(proj, job)
    result = land_from_disk(proj, job)
    check("same patch (d): the dry check skips the change main has",
          plan, {"commits": ["add n3.txt"], "skipped": ["the fix, by this job"]})
    check("same patch (d): the final landing lands and pushes",
          (result["state"], result.get("reason"), result["pushed"]),
          ("landed", None, True))
    check("same patch (d): main is the rewritten main plus the job's own",
          subjects_of(proj),
          ["add n3.txt", "the fix, by another job", "scrubbed n1", "start"])
    check("same patch (d): main's own commits are untouched (a fast-forward)",
          (in_main(proj, rewritten), origin_main(origin) == git(proj.root, "rev-parse", "main")),
          (True, True))
    # Invalid UTF-8 in a forced text diff must not break scanning.
    bin_repo = root / "binary"
    git(root, "init", "-q", "-b", "main", str(bin_repo))
    (bin_repo / "font.woff2").write_bytes(b"wOF2\x00\x01\x98\xff\xfe" * 300)
    git(bin_repo, "add", "-A")
    git(bin_repo, "commit", "-qm", "a font", who="Demo")
    sha = git(bin_repo, "rev-parse", "HEAD")
    shown = wt.git(bin_repo, "show", "--format=", "-U0", "--text", sha, check=False)
    check("a binary file's diff is read, not a crash",
          (shown.returncode, "\ufffd" in shown.stdout), (0, True))
finally:
    wt.WORKTREES, loam_lock.LOCK_DIR = saved
    shutil.rmtree(root, ignore_errors=True)

print(f"\n{'all passed' if not fails else f'{fails} failed'}")
sys.exit(1 if fails else 0)
