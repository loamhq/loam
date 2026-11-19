"""Exercise job isolation, landing outcomes, recovery and live locks against temporary repositories."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_engineer as eng  # noqa: E402
import loam_lock  # noqa: E402
import loam_worktree as wt  # noqa: E402

AGENTS = Path(__file__).resolve().parent.parent
GUARD = str(AGENTS / "hooks" / "boundary_guard.py")
DEPLOY = str(AGENTS / "bin" / "deploy")

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:64} {got!r}"
          + ("" if ok else f"\n      wanted {want!r}"))


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        capture_output=True, text=True, check=True).stdout.strip()


root = Path(tempfile.mkdtemp(prefix="loam-worktree-test-"))
saved = (wt.WORKTREES, loam_lock.LOCK_DIR)
wt.WORKTREES = root / "worktrees"
loam_lock.LOCK_DIR = root / "locks"
os.environ.pop(loam_lock.LIVE_HELD_VAR, None)

CHECK = """#!/bin/sh
# Fails on a BAD line, and on more than one `extra` line -- which two branches
# that each pass alone can produce together.
! grep -q BAD lines.txt && [ "$(grep -c '^extra' lines.txt)" -le 1 ]
"""


def make_project(name: str, test_command: str | None = "sh check.sh") -> eng.Project:
    origin = root / f"{name}.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    seed = root / f"{name}-seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    (seed / "lines.txt").write_text("one\ntwo\nthree\n")
    (seed / "check.sh").write_text(CHECK)
    (seed / "memory").mkdir()
    (seed / "memory" / "MEMORY.md").write_text("# Memory\n\nFirst entry.\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-qm", "start")
    git(seed, "remote", "add", "origin", str(origin))
    git(seed, "push", "-q", "origin", "main")
    checkout = root / name
    subprocess.run(["git", "clone", "-q", str(origin), str(checkout)], check=True)
    # Every provisioned checkout has its engineer's identity in its config.
    git(checkout, "config", "user.name", f"{name} engineer")
    git(checkout, "config", "user.email", f"engineer+{name}@noreply.local")
    record = {"name": name, "checkout": str(checkout)}
    if test_command:
        record["test_command"] = test_command
    return eng.Project(name, checkout, root / f"{name}-jobs", platform=False,
                       record=record)


def start(project: eng.Project, job_id: str, continues: str | None = None) -> dict:
    job_dir = project.jobs / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    return wt.setup(project, job_id, job_dir,
                    project.jobs / continues if continues else None)


def commit(record: dict, path: str, text: str, message: str = "edit") -> str:
    tree = Path(record["path"])
    (tree / path).write_text(text)
    git(tree, "add", "-A")
    git(tree, "commit", "-qm", message)
    return git(tree, "rev-parse", "HEAD")


def edit_line(record: dict, old: str, new: str) -> str:
    tree = Path(record["path"])
    text = (tree / "lines.txt").read_text().replace(old, new)
    return commit(record, "lines.txt", text)


def main_of(project: eng.Project) -> str:
    return git(project.root, "rev-parse", "main")


def origin_of(project: eng.Project) -> str:
    return git(project.root, "ls-remote", "origin", "refs/heads/main").split()[0]


def is_ff_over(project: eng.Project, older: str) -> bool:
    """main contains `older` -- nothing landed before was rewritten."""
    return subprocess.run(["git", "-C", str(project.root), "merge-base",
                           "--is-ancestor", older, "main"]).returncode == 0


def land(project, record, job_id):
    result = wt.land(project, record, f"Land {record['branch']}")
    return result, wt.receipt_section(project, job_id, result)


try:
    # -----------------------------------------------------------------------
    # Landing.
    # -----------------------------------------------------------------------
    proj = make_project("demo")
    a = start(proj, "20261002-100000-aaaaaaaa")
    b = start(proj, "20261002-100001-bbbbbbbb")
    check("two jobs of one project have two trees",
          (Path(a["path"]).is_dir(), Path(b["path"]).is_dir(), a["path"] != b["path"]),
          (True, True, True))
    check("...each on its own branch from main",
          (a["branch"], b["branch"], a["base"] == b["base"] == main_of(proj)),
          ("job/20261002-100000-aaaaaaaa", "job/20261002-100001-bbbbbbbb", True))
    check("...and the canonical checkout is still on main, untouched",
          (git(proj.root, "symbolic-ref", "--short", "HEAD"),
           git(proj.root, "status", "--porcelain")), ("main", ""))

    tip_a = edit_line(a, "one", "ONE")
    tip_b = edit_line(b, "three", "THREE")
    first, text = land(proj, a, a["job"])
    check("fast-forward: landed at the branch tip",
          (first["state"], first["sha"]), ("landed", tip_a))
    check("...main here and on origin are that commit",
          (main_of(proj), origin_of(proj)), (tip_a, tip_a))
    check("...its tree and branch are gone",
          (Path(a["path"]).exists(),
           git(proj.root, "branch", "--list", a["branch"])), (False, ""))
    check("...the receipt says landed at the sha",
          f"**Landing: landed at `{tip_a[:7]}`**" in text and "Pushed to origin" in text,
          True)

    second, text = land(proj, b, b["job"])
    merged = second.get("sha") or ""
    check("merge on a main that moved: landed", second["state"], "landed")
    check("...as a merge commit whose first parent is the first landing",
          git(proj.root, "rev-parse", f"{merged}^1", f"{merged}^2").split(),
          [tip_a, tip_b])
    check("...tested on the merged tree", second["test"]["tree"], "merge")
    check("...both edits are in main",
          (proj.root / "lines.txt").read_text(), "ONE\ntwo\nTHREE\n")
    check("...origin has it", origin_of(proj), merged)

    # A conflict, kept and reported, then resolved by its continuation.
    c = start(proj, "20261002-100002-cccccccc")
    d = start(proj, "20261002-100003-dddddddd")
    edit_line(c, "two", "two from c")
    tip_d = edit_line(d, "two", "two from d")
    landed_c, _ = land(proj, c, c["job"])
    before = main_of(proj)
    conflict, text = land(proj, d, d["job"])
    check("conflict: not landed, the file named",
          (conflict["state"], conflict["reason"], conflict["files"]),
          ("not_landed", "conflict", ["lines.txt"]))
    check("...main and origin did not move",
          (main_of(proj), origin_of(proj)), (before, before))
    check("...the tree is kept, on its branch, at its own tip, clean",
          (Path(d["path"]).is_dir(),
           git(Path(d["path"]), "symbolic-ref", "--short", "HEAD"),
           git(Path(d["path"]), "rev-parse", "HEAD"),
           git(Path(d["path"]), "status", "--porcelain")),
          (True, d["branch"], tip_d, ""))
    check("...the receipt says not landed, with the continuation brief",
          ("**Landing: not landed** — merging it into main conflicts" in text,
           f"> continue_job: {d['job']}" in text, "`lines.txt`" in text),
          (True, True, True))
    print("\n--- the conflict receipt section ---\n" + text + "\n---\n")

    e = start(proj, "20261002-100004-eeeeeeee", continues=d["job"])
    check("continuation: the same tree and branch, reused",
          (e["path"], e["branch"], e["root_job"], e["opened"]),
          (d["path"], d["branch"], d["job"], "reused"))
    tree = Path(e["path"])
    subprocess.run(["git", "-C", str(tree), "merge", "-q", "main"],
                   capture_output=True, check=False)
    (tree / "lines.txt").write_text("ONE\ntwo from c and d\nTHREE\n")
    git(tree, "add", "-A")
    git(tree, "commit", "-qm", "Resolve")
    resolved, text = land(proj, e, e["job"])
    check("...the continuation's landing: landed, fast-forward over main",
          (resolved["state"], is_ff_over(proj, landed_c["sha"])),
          ("landed", True))
    check("...main has the resolution",
          (proj.root / "lines.txt").read_text(), "ONE\ntwo from c and d\nTHREE\n")

    # A continuation of a job that landed: a fresh branch at the same path,
    # because the CLI resumes a session only in the directory it ran in.
    f = start(proj, "20261002-100005-ffffffff", continues=e["job"])
    check("continuation of a landed job: same path, a fresh branch from main",
          (f["path"], f["branch"], f["base"] == main_of(proj)),
          (e["path"], "job/20261002-100005-ffffffff", True))
    nothing, text = land(proj, f, f["job"])
    check("nothing to land: no commits, tree removed",
          (nothing["state"], Path(f["path"]).exists()), ("nothing", False))
    check("...and the receipt says so", "**Landing: nothing to land**" in text, True)

    # Landed mid-job (`engineer-job --land` keeps the tree), then the end of
    # the job finds nothing new: that is "landed earlier", never "nothing
    # was pushed", and it offers no continuation -- whether the job completed
    # or ended without a receipt.
    q = start(proj, "20261002-100015-qqqqqqqq")
    tip_q = edit_line(q, "two from c and d", "two, landed mid-job")
    mid = wt.land(proj, q, "Land mid-job", keep_tree=True)
    check("mid-job --land: landed, tree kept",
          (mid["state"], mid["sha"], Path(q["path"]).is_dir()), ("landed", tip_q, True))
    check("...an unfinished job whose work is on main: landed earlier, no continuation",
          (wt.incomplete_result(proj, q, "ended completed and wrote no receipt")["state"],
           "continue_job" in wt.receipt_section(
               proj, q["job"], wt.incomplete_result(proj, q, "ended completed and wrote no receipt"))),
          ("landed_earlier", False))
    end, text = land(proj, q, q["job"])
    check("...the end-of-job landing: landed earlier at the tip",
          (end["state"], end["sha"]), ("landed_earlier", tip_q))
    check("...and the receipt says so, not 'nothing was pushed'",
          (f"**Landing: landed at `{tip_q[:7]}` earlier, by `--land`**" in text,
           "Nothing was pushed" in text, "continue_job" in text), (True, False, False))
    r2 = start(proj, "20261002-100016-rrrrrrrr")
    edit_line(r2, "THREE", "THREE unlanded")
    check("an unfinished job with unlanded commits: still not landed, incomplete",
          (wt.incomplete_result(proj, r2, "ended cut off")["state"],
           wt.incomplete_result(proj, r2, "ended cut off")["reason"]),
          ("not_landed", "incomplete"))
    wt.remove_tree(proj, r2, delete_branch=True)

    # Tests failing on the branch.
    g = start(proj, "20261002-100006-gggggggg")
    commit(g, "lines.txt", "BAD\n")
    before = main_of(proj)
    bad, text = land(proj, g, g["job"])
    check("branch tests fail: not landed, nothing merged",
          (bad["state"], bad["reason"], main_of(proj), Path(g["path"]).is_dir()),
          ("not_landed", "branch_tests", before, True))
    check("...the receipt quotes the failing command",
          "Failing run of `sh check.sh` on the branch" in text, True)

    # Tests that pass on each branch and fail on the merge.
    h = start(proj, "20261002-100007-hhhhhhhh")
    i = start(proj, "20261002-100008-iiiiiiii")
    base = (proj.root / "lines.txt").read_text()
    commit(h, "lines.txt", "extra h\n" + base)
    tip_i = commit(i, "lines.txt", base + "extra i\n")
    land(proj, h, h["job"])
    before = main_of(proj)
    merged_bad, text = land(proj, i, i["job"])
    check("merged tests fail: not landed, main unmoved",
          (merged_bad["state"], merged_bad["reason"], main_of(proj), origin_of(proj)),
          ("not_landed", "merged_tests", before, before))
    check("...the tree is back on its own branch tip",
          git(Path(i["path"]), "rev-parse", "HEAD"), tip_i)
    check("...the receipt names the merged tree",
          "on the merged tree" in text and "`git merge main`" in text, True)

    # A landing's tests run without the launcher's credentials: the unit
    # mounts wake.env, and a test must see what a developer's shell sees.
    keyed = make_project("keyed", test_command=(
        'test -z "$CLAUDE_CODE_OAUTH_TOKEN" && test -z "$GEMINI_API_KEY" '
        '&& test -z "$GITHUB_TOKEN" && test -n "$PATH"'))
    kj = start(keyed, "20261002-100017-kkkkkkk1")
    commit(kj, "lines.txt", "keyed\n")
    held = {k: os.environ.get(k) for k in
            ("CLAUDE_CODE_OAUTH_TOKEN", "GEMINI_API_KEY", "GITHUB_TOKEN")}
    os.environ.update({k: "sk-not-for-tests" for k in held})
    try:
        keyed_result, _ = land(keyed, kj, kj["job"])
    finally:
        for k, v in held.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    check("tests see no credential from the launcher's environment",
          (keyed_result["state"], (keyed_result.get("test") or {}).get("passed")),
          ("landed", True))

    # Uncommitted work.
    j = start(proj, "20261002-100009-jjjjjjjj")
    (Path(j["path"]) / "lines.txt").write_text("forgot to commit\n")
    dirty, text = land(proj, j, j["job"])
    check("uncommitted: not landed, the file named",
          (dirty["state"], dirty["reason"], dirty["files"]),
          ("not_landed", "uncommitted", ["lines.txt"]))

    # Memory: two jobs append to it, both land, nothing conflicts.
    k = start(proj, "20261002-100010-kkkkkkkk")
    m = start(proj, "20261002-100011-mmmmmmmm")
    mem = (proj.root / "memory" / "MEMORY.md").read_text()
    commit(k, "memory/MEMORY.md", mem + "\nFrom k.\n")
    commit(m, "memory/MEMORY.md", mem + "\nFrom m.\n")
    land(proj, k, k["job"])
    union, _ = land(proj, m, m["job"])
    final = (proj.root / "memory" / "MEMORY.md").read_text()
    check("memory: two appends both land by union",
          (union["state"], "From k." in final, "From m." in final),
          ("landed", True, True))

    # No test command: nothing lands untested.
    bare = make_project("untested", test_command=None)
    n = start(bare, "20261002-100012-nnnnnnnn")
    commit(n, "lines.txt", "x\n")
    untested, text = land(bare, n, n["job"])
    check("no test command: not landed",
          (untested["state"], untested["reason"], main_of(bare) != git(Path(n["path"]), "rev-parse", "HEAD")),
          ("not_landed", "no_tests", True))

    # Merge local harness memory back even when Git does not track it.
    lone = make_project("harness")
    git(lone.root, "rm", "-q", "-r", "--cached", "memory")
    (lone.root / ".git" / "info" / "exclude").write_text("/memory/\n")
    git(lone.root, "commit", "-qm", "memory leaves the repository")
    git(lone.root, "push", "-q", "origin", "main")
    p1 = start(lone, "20261002-100013-pppppppp")
    p2 = start(lone, "20261002-100014-qqqqqqqq")
    check("untracked memory is copied into each tree",
          (p1["seeded"], (Path(p1["path"]) / "memory" / "MEMORY.md").is_file()),
          (["memory"], True))
    for rec, word in ((p1, "one"), (p2, "two")):
        tree = Path(rec["path"])
        with (tree / "memory" / "MEMORY.md").open("a") as handle:
            handle.write(f"\nFrom tree {word}.\n")
        commit(rec, "lines.txt", (tree / "lines.txt").read_text() + word + "\n"
               if word == "one" else "zero\n" + (tree / "lines.txt").read_text())
    r1, _ = land(lone, p1, p1["job"])
    r2, _ = land(lone, p2, p2["job"])
    canonical_memory = (lone.root / "memory" / "MEMORY.md").read_text()
    check("...both trees' memory reaches the canonical copy",
          (r1["state"], r2["state"], "From tree one." in canonical_memory,
           "From tree two." in canonical_memory, r2["memory"]),
          ("landed", "landed", True, True, ["memory/MEMORY.md"]))

    # A detached checkout must not change the job branch that lands.
    hp = make_project("detached")
    base = main_of(hp)
    dj = start(hp, "20261013-100000-dddddddd")
    dtree = Path(dj["path"])
    elsewhere = commit(dj, "lines.txt", "somebody else's work\n")
    git(dtree, "checkout", "-q", "--detach", elsewhere)
    git(dtree, "branch", "-f", dj["branch"], base)
    check("detached: the job's own view -- branch at base, HEAD elsewhere",
          (git(dtree, "rev-parse", f"refs/heads/{dj['branch']}"),
           git(dtree, "rev-parse", "HEAD") == base), (base, False))
    nothing_d, text = land(hp, dj, dj["job"])
    check("...lands as nothing, main and origin unmoved, the tree removed",
          (nothing_d["state"], main_of(hp), origin_of(hp), dtree.exists()),
          ("nothing", base, base, False))
    check("...in the words nothing-to-land already uses, naming the HEAD",
          ("**Landing: nothing to land**" in text,
           f"Its HEAD was detached at `{elsewhere[:7]}`" in text), (True, True))

    rj = start(hp, "20261013-100001-rrrrrrrr")
    rtree = Path(rj["path"])
    real = commit(rj, "lines.txt", "one\ntwo\nthree\nfour\n")
    stray = commit(rj, "lines.txt", "BAD\n")
    git(rtree, "checkout", "-q", "--detach", stray)
    git(rtree, "branch", "-f", rj["branch"], real)
    landed_r, text = land(hp, rj, rj["job"])
    check("detached, with a real commit on the branch: lands that commit",
          (landed_r["state"], landed_r["sha"], main_of(hp), origin_of(hp)),
          ("landed", real, real, real))
    check("...and not the detached commit, which is not on the branch",
          (hp.root / "lines.txt").read_text(), "one\ntwo\nthree\nfour\n")
    check("...the receipt says where HEAD was",
          f"Its HEAD was detached at `{stray[:7]}`" in text, True)

    # A read-only brief lands nothing, whatever the tree holds.
    oj = start(hp, "20261013-100002-oooooooo")
    commit(oj, "lines.txt", "one\n")
    (Path(oj["path"]) / "scratch.txt").write_text("notes\n")
    before_o = main_of(hp)
    ro = wt.read_only_result(hp, oj)
    text = wt.receipt_section(hp, oj["job"], ro)
    check("read-only: nothing landed, nothing pushed, the tree removed",
          (ro["state"], main_of(hp), origin_of(hp), Path(oj["path"]).exists()),
          ("read_only", before_o, before_o, False))
    check("...the receipt says so and what it discarded",
          ("**Landing: read-only brief: nothing landed**" in text,
           "1 commit(s) `main` lacks, 1 uncommitted path(s)" in text,
           "The worktree and its branch are removed." in text), (True, True, True))
    kj2 = start(hp, "20261013-100003-kkkkkkkk")
    kept = wt.read_only_result(hp, kj2, keep_tree=True)
    check("read-only mid-job (`--land`): nothing landed, the tree stays",
          (kept["state"], Path(kj2["path"]).is_dir()), ("read_only", True))

    # -----------------------------------------------------------------------
    # The guard: a job's boundary, through the real hook.
    # -----------------------------------------------------------------------
    gp = make_project("guarded")
    mine = start(gp, "20261002-110000-11111111")
    theirs = start(gp, "20261002-110001-22222222")
    env = {**os.environ,
           "LOAM_JOB_TREE": mine["path"],
           "LOAM_JOB_DIR": str(gp.jobs / mine["job"]),
           "LOAM_JOB_CANONICAL": str(gp.root),
           "LOAM_JOB_WORKTREES": str(wt.WORKTREES),
           "CLAUDE_PROJECT_DIR": mine["path"]}

    def guard(tool: str, path: Path, **extra) -> tuple[int, str]:
        payload = {"tool_name": tool, "cwd": mine["path"],
                   "tool_input": {"file_path": str(path), "content": "x\n", **extra}}
        out = subprocess.run(["python3", GUARD], input=json.dumps(payload),
                             capture_output=True, text=True, env=env)
        return out.returncode, out.stderr

    check("guard: a write in the job's own tree passes",
          guard("Write", Path(mine["path"]) / "lines.txt")[0], 0)
    check("guard: a write in the job's own directory passes",
          guard("Write", gp.jobs / mine["job"] / "receipt.md")[0], 0)
    code, why = guard("Write", gp.root / "lines.txt")
    check("guard: a write to the canonical checkout is refused",
          (code, "canonical checkout, which is nobody's working tree" in why), (2, True))
    code, why = guard("Edit", gp.root / "lines.txt", old_string="one", new_string="1")
    check("guard: ...an Edit there too", code, 2)
    code, why = guard("Write", Path(theirs["path"]) / "lines.txt")
    check("guard: a write to another job's tree is refused",
          (code, "another job's worktree" in why), (2, True))
    code, why = guard("Write", gp.jobs / theirs["job"] / "receipt.md")
    check("guard: a write to another job's directory is refused", code, 2)
    link = Path(mine["path"]) / "escape"
    link.symlink_to(gp.root)
    check("guard: a symlink out of the tree into the canonical checkout is refused",
          guard("Write", link / "lines.txt")[0], 2)
    link.unlink()
    # -----------------------------------------------------------------------
    # The wall: the same boundary for Bash, by mount namespace. The project
    # route (an unprivileged user namespace) is the one a test can raise from
    # anywhere; Loam's goes through sudo and is proved by causing.
    # -----------------------------------------------------------------------
    wall_job = gp.jobs / mine["job"]
    check("wall: raised around a probe", wt.wall_check(gp, mine, wall_job), None)
    shell = (f"echo y > {gp.root}/canon.txt; echo y > {theirs['path']}/x.txt; "
             f"echo y > {gp.jobs / theirs['job']}/x.txt; "
             f"cd {mine['path']} && echo walled > wall.txt && git add wall.txt "
             f"&& git commit -qm wall && echo committed; "
             f"echo r > {wall_job}/receipt.md && echo job-dir-ok")
    out = subprocess.run(wt.walled(gp, mine, wall_job, ["sh", "-c", shell]),
                         capture_output=True, text=True,
                         env={**os.environ, **wt.wall_env()})
    check("wall: Bash cannot write the canonical checkout",
          (f"{gp.root}/canon.txt: Read-only file system" in out.stderr,
           (gp.root / "canon.txt").exists()), (True, False))
    check("wall: ...nor another job's tree, nor its directory",
          out.stderr.count("Read-only file system"), 3)
    check("wall: ...and commits in its own tree, and writes its job directory",
          ("committed" in out.stdout, "job-dir-ok" in out.stdout,
           git(Path(mine["path"]), "log", "-1", "--format=%s")), (True, True, "wall"))

    # -----------------------------------------------------------------------
    # Git isolation: a job's tree is a clone of its own, so nothing a job's
    # git does reaches the canonical repository or another job's clone. Each
    # line below is a mutation that the shared-worktree layout allowed or
    # half-allowed; all are attempted, each from inside the wall, and every
    # ref, object directory listing, config and hook is compared after.
    # -----------------------------------------------------------------------
    check("isolation: a new tree is a clone of its own, not a worktree",
          (wt.is_clone(Path(mine["path"])), Path(mine["path"]) not in
           wt.worktrees(gp.root), mine.get("kind")), (True, True, "clone"))
    canon_git, their_git = gp.root / ".git", Path(theirs["path"]) / ".git"

    def snapshot(gitdir: Path) -> dict:
        files = {}
        for path in sorted(gitdir.rglob("*")):
            if path.is_file() and "index" != path.name and "FETCH_HEAD" != path.name:
                files[str(path.relative_to(gitdir))] = path.read_bytes()
        return files

    # An unreachable loose object, which a prune in there would delete.
    loose = subprocess.run(["git", "-C", str(gp.root), "hash-object", "-w",
                            "--stdin"], input="unreachable\n", text=True,
                           capture_output=True, check=True).stdout.strip()
    before_canon, before_theirs = snapshot(canon_git), snapshot(their_git)
    attempts = {
        "update-ref main": f"git --git-dir={canon_git} update-ref refs/heads/main main~1",
        "delete their branch": f"git --git-dir={their_git} branch -D {theirs['branch']}",
        "reset their branch": f"git --git-dir={their_git} update-ref refs/heads/{theirs['branch']} {mine['base']}",
        "packed-refs": f"git --git-dir={canon_git} pack-refs --all",
        "replace refs dir": f"mv {canon_git}/refs {canon_git}/refs.x",
        "config": f"git --git-dir={canon_git} config core.bare true",
        "hook": f"echo 'exit 1' > {canon_git}/hooks/pre-commit",
        "prune": f"git --git-dir={canon_git} prune --expire=now",
        "gc": f"git --git-dir={canon_git} gc --prune=now --quiet",
        "their reflog": f"git --git-dir={their_git} reflog expire --expire=now --all",
        "their config": f"git --git-dir={their_git} config user.name intruder",
        "push to canonical": f"git -C {mine['path']} push -q origin HEAD:refs/heads/main",
        "push their branch": f"git -C {mine['path']} push -q {their_git} HEAD:refs/heads/{theirs['branch']}",
    }
    denied = {}
    for name, command in attempts.items():
        run = subprocess.run(wt.walled(gp, mine, wall_job, ["sh", "-c", command]),
                             capture_output=True, text=True,
                             env={**os.environ, **wt.wall_env()})
        denied[name] = run.returncode != 0
    # A prune with nothing it may delete exits 0; what matters is the object.
    denied["prune"] = denied["gc"] = (
        canon_git / "objects" / loose[:2] / loose[2:]).is_file()
    check("isolation: every canonical and cross-job git mutation is refused",
          [n for n, ok in denied.items() if not ok], [])
    check("isolation: ...the canonical .git is byte-for-byte unchanged",
          snapshot(canon_git) == before_canon, True)
    check("isolation: ...and so is the other job's",
          snapshot(their_git) == before_theirs, True)
    own = subprocess.run(wt.walled(gp, mine, wall_job, ["sh", "-c", (
        f"cd {mine['path']} && git merge -q main && echo o >> own.txt && "
        f"git add own.txt && git commit -qm own && git gc --quiet --prune=now "
        f"&& git branch scratch && git branch -D scratch && echo own-ok")]),
        capture_output=True, text=True, env={**os.environ, **wt.wall_env()})
    check("isolation: ...while its own commit, merge from main, gc and branch "
          "housekeeping all work", ("own-ok" in own.stdout, own.stderr[-300:]),
          (True, ""))

    # A tree made before isolation (a shared worktree) is converted in place
    # when a continuation reopens it: same path, its commits and its
    # uncommitted file kept, its branch still in the canonical repository.
    legacy_id = "20261002-100009-1e9ac100"
    legacy_path = wt.path_for(gp, legacy_id)
    git(gp.root, "worktree", "add", "-q", "-b", wt.branch_for(legacy_id),
        str(legacy_path), "main")
    legacy_tip = commit({"path": str(legacy_path)}, "legacy.txt", "old\n")
    (legacy_path / "wip.txt").write_text("uncommitted\n")
    (gp.jobs / legacy_id).mkdir(parents=True)
    wt.write_record(gp.jobs / legacy_id, {
        "project": gp.name, "job": legacy_id, "root_job": legacy_id,
        "path": str(legacy_path), "branch": wt.branch_for(legacy_id),
        "base": main_of(gp), "canonical": str(gp.root), "opened": "new"})
    converted = start(gp, "20261002-100010-1e9ac101", continues=legacy_id)
    check("legacy worktree: converted in place on continuation",
          (converted["path"], wt.is_clone(legacy_path),
           git(legacy_path, "rev-parse", "HEAD"),
           (legacy_path / "wip.txt").read_text(),
           legacy_path in wt.worktrees(gp.root),
           git(gp.root, "rev-parse", f"refs/heads/{wt.branch_for(legacy_id)}")),
          (str(legacy_path), True, legacy_tip, "uncommitted\n", False, legacy_tip))
    (legacy_path / "wip.txt").unlink()
    check("legacy worktree: ...and its branch lands from the clone",
          wt.land(gp, converted, "land legacy")["state"], "landed")

    # -----------------------------------------------------------------------
    # The live lock: bin/deploy waits for it, says so, and goes on.
    # -----------------------------------------------------------------------
    loam_lock.LOCK_DIR = saved[1]
    app = f"locktest{os.getpid()}"
    lock_file = loam_lock.live_lock_path(app)
    holder_ready = root / "holder-ready"
    holder = subprocess.Popen([sys.executable, "-c", (
        f"import sys, time; sys.path.insert(0, {str(Path(__file__).resolve().parent.parent / 'lib')!r})\n"
        "import loam_lock\n"
        f"with loam_lock.live({app!r}, 'land job/x'):\n"
        f"    open({str(holder_ready)!r}, 'w').close(); time.sleep(4)\n")])
    while not holder_ready.exists():
        time.sleep(0.05)
    t0 = time.monotonic()
    deploy = subprocess.Popen([DEPLOY, app, "up", "abc1234"], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True,
                              env={**os.environ, "LOAM_LOCK_WAIT_S": "60"})
    time.sleep(2)
    check("lock: bin/deploy is still waiting while the landing holds the lock",
          deploy.poll(), None)
    _, err = deploy.communicate(timeout=60)
    waited = time.monotonic() - t0
    holder.wait()
    check("lock: ...it said what it was waiting for",
          f"waiting for the {app} live lock, held by" in err and "land job/x" in err, True)
    check("lock: ...and went on only once the holder let go (waited >= 3 s)",
          waited >= 3, True)
    check("lock: ...to its own next step (not root: refused there, after the lock)",
          "must run as root" in err, True)
    lock_file.unlink(missing_ok=True)

    os.environ[loam_lock.LIVE_HELD_VAR] = app
    t0 = time.monotonic()
    with loam_lock.live(app, "nested", wait_seconds=0):
        pass
    check("lock: a holder's child does not wait on its own parent",
          time.monotonic() - t0 < 1, True)
    os.environ.pop(loam_lock.LIVE_HELD_VAR, None)
    lock_file.unlink(missing_ok=True)

finally:
    wt.WORKTREES, loam_lock.LOCK_DIR = saved
    shutil.rmtree(root, ignore_errors=True)

print(f"\nFAILURES: {fails}")
sys.exit(1 if fails else 0)
