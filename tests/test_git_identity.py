"""Whose name an agent's commits carry, and no provider's (since 2026-11-18;
lib/loam_git_identity.py, lib/loam_worktree.py ``job_identity``,
``settle_commits``, ``install_hook``; lib/loam_github_app.owner_token).

Asserted against real git repositories in a temp directory, with job trees
made by lib/loam_worktree.py as bin/engineer-job makes them, and a scratch
host settings file (``LOAM_HOST_SETTINGS``) standing for an install's:

  - the message filter takes out exactly a provider's lines (Claude Code's
    co-author trailer and "Generated with" line, Claude-Session, Codex's
    trailer, a provider bot) and keeps a person's co-author, a person named
    Claude, Signed-off-by and every other line;
  - the setting: "github" makes the name and the no-reply address from
    GitHub's answer; a typed one is checked; a provider's address is
    refused; null clears;
  - the order: a project's person, then a repository's own identity (any
    that is not provision's default, and any on a gated checkout), then the
    owner's, then the engineer's with the prompt;
  - a fresh install (nothing set): a job commits as the engineer, the
    receipt says how to set the owner's, and provider trailers still go;
  - an existing install whose owner sets it later: what already landed keeps
    its hashes, a continuation's reused tree takes the owner's identity, and
    the landing re-makes the job's own engineer commits as the owner;
  - Claude Code's, Codex's and an Antigravity-shaped attribution, written by
    hand as each would, are taken out at commit time by the clone's hook,
    and again at the landing for a commit made with --no-verify;
  - a person's commit in a job's branch, a person's co-author trailer and a
    signed commit are kept exactly;
  - a second install's owner gets their own name; an explicit repository
    identity and a project's person win over the owner's;
  - the pull request's actor: the owner's token only when GitHub says it is
    the owner's chosen account, else the app.

Run: python3 tests/test_git_identity.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_engineer as eng  # noqa: E402
import loam_git_identity as gi  # noqa: E402
import loam_lock  # noqa: E402
import loam_worktree as wt  # noqa: E402

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:70} {got!r}"
          + ("" if ok else f"\n      wanted {want!r}"))


def git(repo: Path, *args: str, who: tuple[str, str] | None = None,
        env: dict | None = None) -> str:
    ident = ["-c", f"user.name={who[0]}", "-c", f"user.email={who[1]}"] if who else []
    return subprocess.run(["git", "-C", str(repo), *ident, *args],
                          capture_output=True, text=True, check=True,
                          env=env).stdout.strip()


root = Path(tempfile.mkdtemp(prefix="loam-git-identity-test-"))
saved_env = os.environ.get("LOAM_HOST_SETTINGS")
wt.WORKTREES = root / "worktrees"
loam_lock.LOCK_DIR = root / "locks"
os.environ.pop(loam_lock.LIVE_HELD_VAR, None)
SETTINGS = root / "settings.json"
os.environ["LOAM_HOST_SETTINGS"] = str(SETTINGS)
OWNER = {"source": "github", "login": "ada", "name": "Ada Lovelace",
         "email": "1815+ada@users.noreply.github.com"}
CLAUDE = ("\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n\n"
          "\U0001F916 Generated with [Claude Code](https://claude.com/claude-code)\n")
CODEX = "\n\nCo-authored-by: Codex <noreply@openai.com>\n"
AGY = "\n\nCo-authored-by: Antigravity <antigravity@google.com>\n"


def set_owner(value) -> None:
    SETTINGS.write_text(json.dumps({"git": value} if value else {}))


def make(name: str, ident=("demo engineer", "engineer+demo@noreply.local"),
         record_extra: dict | None = None) -> tuple[eng.Project, Path]:
    origin = root / f"{name}.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    checkout = root / name
    subprocess.run(["git", "init", "-q", "-b", "main", str(checkout)], check=True)
    git(checkout, "config", "user.name", ident[0])
    git(checkout, "config", "user.email", ident[1])
    (checkout / "a.txt").write_text("a\n")
    git(checkout, "add", "-A")
    git(checkout, "commit", "-qm", "start")
    git(checkout, "remote", "add", "origin", str(origin))
    git(checkout, "push", "-q", "origin", "main")
    git(checkout, "fetch", "-q", "origin")
    record = {"name": name, "checkout": str(checkout), "test_command": "true",
              **(record_extra or {})}
    project = eng.Project(name, checkout, root / f"{name}-jobs", platform=False,
                          record=record)
    project.jobs.mkdir()
    return project, origin


def start(project: eng.Project, job_id: str, continues: Path | None = None) -> dict:
    job_dir = project.jobs / job_id
    job_dir.mkdir()
    return wt.setup(project, job_id, job_dir, continues)


def commit(record: dict, name: str, msg: str, who=None, verify=True) -> None:
    tree = Path(record["path"])
    (tree / name).write_text(name + "\n")
    git(tree, "add", "-A")
    git(tree, "commit", "-q", *([] if verify else ["--no-verify"]), "-m", msg, who=who)


def land(project: eng.Project, record: dict) -> dict:
    return wt.land(project, record, f"Land {record['branch']}")


def people(repo: Path, ref: str = "main", n: int = 1) -> list[str]:
    return git(repo, "log", f"-{n}", "--format=%an <%ae> / %cn <%ce>", ref).splitlines()


def bodies(repo: Path, rng: str) -> list[str]:
    out = git(repo, "log", "--format=%B%x1e", rng)
    return [b.strip() for b in out.split("\x1e") if b.strip()]


try:
    # -- The filter --------------------------------------------------------
    msg = ("Fix the thing\n\nWhy it was broken.\n\nSigned-off-by: Ada <ada@example.org>\n"
           "Co-authored-by: Bea Person <bea@example.org>\n"
           "Co-authored-by: Claude Monet <claude@monet.example>\n"
           "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n"
           "Co-authored-by: Codex <noreply@openai.com>\n"
           "Co-authored-by: claude[bot] <209825114+claude[bot]@users.noreply.github.com>\n"
           "Claude-Session: https://claude.ai/code/session_x\n\n"
           "\U0001F916 Generated with [Claude Code](https://claude.com/claude-code)\n"
           "Generated with [Codex](https://openai.com/codex/).\n")
    check("filter: a provider's lines go, a person's and Signed-off-by stay",
          gi.strip_provider(msg),
          "Fix the thing\n\nWhy it was broken.\n\nSigned-off-by: Ada <ada@example.org>\n"
          "Co-authored-by: Bea Person <bea@example.org>\n"
          "Co-authored-by: Claude Monet <claude@monet.example>\n")
    plain = "Add Claude support to the parser\n\nMentions Codex, noreply.\n"
    check("filter: a message with none is returned as it was",
          gi.strip_provider(plain) is plain, True)

    # -- The setting -------------------------------------------------------
    got = gi.validate({"identity": "github"},
                      lambda: {"login": "ada", "id": 1815, "name": "Ada Lovelace"})
    check("setting: github is the profile name and GitHub's no-reply", got, OWNER)
    check("setting: github with no profile name uses the login",
          gi.validate({"identity": "github"}, lambda: {"login": "bea", "id": 7, "name": None})["name"],
          "bea")
    check("setting: typed", gi.validate({"identity": {"name": " Ada  L ", "email": "a@ex.org"}}),
          {"source": "typed", "name": "Ada L", "email": "a@ex.org"})
    for bad, why in (({"identity": {"name": "X", "email": "noreply@anthropic.com"}}, "provider"),
                     ({"identity": {"name": "X", "email": "nope"}}, "shape"),
                     ({"identity": {"name": "", "email": "a@ex.org"}}, "no name"),
                     ({"identity": "github"}, "github not connected"),
                     ({"other": 1}, "unknown field")):
        try:
            gi.validate(bad)
            check(f"setting: refused ({why})", "accepted", "refused")
        except gi.IdentityError:
            check(f"setting: refused ({why})", "refused", "refused")
    check("setting: null clears", gi.validate({"identity": None}), None)
    check("masked: a no-reply in full, any other hidden",
          (gi.masked(OWNER["email"]), gi.masked("ada@example.org")),
          (OWNER["email"], "a…@example.org"))

    # -- The order ---------------------------------------------------------
    eng_id = ("demo engineer", "engineer+demo@noreply.local")
    person = {"name": "Pat", "email": "9+pat@users.noreply.github.com", "github_login": "pat"}
    check("order: a project's person first",
          gi.resolve(person, ("Ada Repo", "ada@repo.example"), True, OWNER)["source"], "project")
    check("order: a gated checkout keeps its own identity",
          gi.resolve(None, ("Loam engineer", "engineer+loam@noreply.local"), True, OWNER)["source"], "repo")
    check("order: a repository identity set on purpose",
          gi.resolve(None, ("Ada Repo", "ada@repo.example"), False, OWNER)["name"], "Ada Repo")
    check("order: the owner's over provision's default",
          gi.resolve(None, eng_id, False, OWNER)["name"], "Ada Lovelace")
    check("order: nothing set is the engineer, never a provider",
          gi.resolve(None, eng_id, False, {})["source"], "engineer")
    bea = {"source": "typed", "name": "Bea Second", "email": "bea@second.example"}
    check("order: a second install's owner gets their own name",
          gi.resolve(None, eng_id, False, bea)["name"], "Bea Second")

    # -- A fresh install: nothing set --------------------------------------
    set_owner(None)
    proj, origin = make("fresh")
    r = start(proj, "20261118-100000-00000001")
    check("fresh: the job commits as the engineer", r["attribution"]["source"], "engineer")
    check("fresh: the record carries the prompt",
          "settings set git.identity github" in r["attribution"]["summary"], True)
    commit(r, "b.txt", "Add b" + CLAUDE, verify=False)
    result = land(proj, r)
    check("fresh: lands", result["state"], "landed")
    check("fresh: the commit is the engineer's",
          people(proj.root, "main~1^2" if git(proj.root, "rev-list", "--count", "main") == "3" else "main")[0],
          "demo engineer <engineer+demo@noreply.local> / demo engineer <engineer+demo@noreply.local>")
    check("fresh: no provider line reached main",
          [b for b in bodies(proj.root, "main") if "anthropic" in b or "Generated with" in b], [])
    check("fresh: the receipt names how to set it",
          "settings set git.identity" in wt.receipt_section(proj, r["job"], result), True)

    # -- An existing install: the owner sets it later ----------------------
    landed_before = git(proj.root, "rev-parse", "main")
    origin_before = git(origin, "rev-parse", "main")
    r1 = start(proj, "20261118-100000-00000002")
    commit(r1, "c.txt", "Add c")                      # as the engineer, before the setting
    set_owner(OWNER)
    r2 = start(proj, "20261118-100000-00000003", continues=proj.jobs / r1["job"])
    check("existing: a continuation reuses the tree", r2["opened"], "reused")
    check("existing: the reused tree now commits as the owner",
          git(Path(r2["path"]), "config", "user.name"), "Ada Lovelace")
    commit(r2, "d.txt", "Add d" + CODEX, verify=False)
    result = land(proj, r2)
    check("existing: lands", result["state"], "landed")
    check("existing: what landed before keeps its hash",
          git(proj.root, "merge-base", "--is-ancestor", landed_before, "main") == "", True)
    check("existing: origin's history is untouched (fast-forward only)",
          git(origin, "merge-base", "--is-ancestor", origin_before, "main") == "", True)
    new = git(proj.root, "log", "--format=%an <%ae>|%cn <%ce>", f"{landed_before}..main").splitlines()
    want = f"Ada Lovelace <{OWNER['email']}>"
    check("existing: every new commit, the merge too, is the owner's",
          sorted(set(new)), [f"{want}|{want}"])
    check("existing: no provider line reached main",
          [b for b in bodies(proj.root, f"{landed_before}..main") if "openai" in b], [])
    check("existing: the receipt says whose name, from where",
          "from the owner's GitHub account" in wt.receipt_section(proj, r2["job"], result), True)

    # -- The hook, per harness; a person's work kept ----------------------
    r = start(proj, "20261118-100000-00000004")
    tree = Path(r["path"])
    check("hook: the clone has Loam's commit-msg hook",
          gi.HOOK_MARK in (tree / ".git/hooks/commit-msg").read_text(), True)
    for i, (harness, tail) in enumerate((("Claude Code", CLAUDE), ("Codex", CODEX),
                                          ("Antigravity-shaped", AGY))):
        commit(r, f"h{i}.txt", f"Harness {harness}" + tail)
        check(f"hook: {harness}'s attribution is gone at commit time",
              git(tree, "log", "-1", "--format=%B").strip(), f"Harness {harness}")
    commit(r, "p.txt", "A person's commit\n\nCo-authored-by: Bea Person <bea@example.org>"
           + CLAUDE, who=("Bea Person", "bea@example.org"), verify=False)
    person_sha = git(tree, "rev-parse", "HEAD")
    commit(r, "q.txt", "Pair work\n\nCo-authored-by: Bea Person <bea@example.org>")
    # A signed commit, made by hand: a gpgsig header GitHub would show.
    head = git(tree, "rev-parse", "HEAD")
    treeid = git(tree, "rev-parse", "HEAD^{tree}")
    raw = (f"tree {treeid}\nparent {head}\n"
           f"author demo engineer <engineer+demo@noreply.local> 1763500000 +0000\n"
           f"committer demo engineer <engineer+demo@noreply.local> 1763500000 +0000\n"
           f"gpgsig -----BEGIN PGP SIGNATURE-----\n \n fake\n -----END PGP SIGNATURE-----\n"
           f"\nSigned work\n\nCo-authored-by: Codex <noreply@openai.com>\n")
    signed = subprocess.run(["git", "-C", str(tree), "hash-object", "-t", "commit", "-w", "--stdin"],
                            input=raw, capture_output=True, text=True, check=True).stdout.strip()
    git(tree, "update-ref", f"refs/heads/{r['branch']}", signed)
    commit(r, "z.txt", "After the signed one")
    base = git(proj.root, "rev-parse", "main")
    result = land(proj, r)
    check("person: lands", result["state"], "landed")
    rows = git(proj.root, "log", "--reverse", "--format=%an|%s|%B%x1e", f"{base}..main^2"
               if len(git(proj.root, "log", "-1", "--format=%P", "main").split()) == 2
               else f"{base}..main")
    rows = [x.strip() for x in rows.split("\x1e") if x.strip()]
    by_subject = {x.split("|")[1]: x for x in rows}
    check("person: a person's commit is kept as made, its hash too",
          git(proj.root, "merge-base", "--is-ancestor", person_sha, "main") == "", True)
    check("person: its own trailers, the provider's included, untouched",
          "noreply@anthropic.com" in by_subject["A person's commit"], True)
    check("coauthor: a person's co-author on an agent's commit stays",
          "Co-authored-by: Bea Person <bea@example.org>" in by_subject["Pair work"], True)
    check("coauthor: and that commit is the owner's",
          by_subject["Pair work"].split("|")[0], "Ada Lovelace")
    check("signed: a signed commit is kept as made",
          git(proj.root, "merge-base", "--is-ancestor", signed, "main") == "", True)
    check("signed: the landing says it kept it", result.get("signed_kept"), [signed[:10]])

    # -- A repository's own identity, and a project's person, win ----------
    proj_r, _ = make("explicit", ident=("Ada Repo", "ada@repo.example"))
    r = start(proj_r, "20261118-100000-00000005")
    check("explicit repo: the job commits as the repository says",
          (r["attribution"]["source"], git(Path(r["path"]), "config", "user.name")),
          ("repo", "Ada Repo"))
    proj_p, _ = make("person", record_extra={"commit_identity": person})
    r = start(proj_p, "20261118-100000-00000006")
    check("project person: the job commits as the project's person",
          (r["attribution"]["source"], git(Path(r["path"]), "config", "user.email")),
          ("project", person["email"]))
    set_owner(bea)
    proj_b, _ = make("second")
    r = start(proj_b, "20261118-100000-00000007")
    check("second owner: their own name, their address masked in the record",
          (git(Path(r["path"]), "config", "user.name"), r["attribution"]["email"]),
          ("Bea Second", "b…@second.example"))

    # -- The pull request's actor (fixture, no network) --------------------
    import loam_github_app as gh  # noqa: E402
    saved = (gh.load, gh.route, gh.user_token, gh.api, gh.org_apps)
    proj_pr = eng.Project("pr", root / "pr", root / "pr-jobs", platform=False,
                          repo="ada/thing", record={"name": "pr"})
    try:
        gh.load = lambda: {}
        gh.route = lambda data, repo: ({}, {"installation_id": 1})
        gh.org_apps = lambda data: {}
        gh.user_token = lambda **kw: "user-token"
        answers = {"login": "ada"}
        gh.api = lambda method, path, token, **kw: (200, dict(answers))
        set_owner(OWNER)
        check("actor: the owner's token when GitHub says it is ada's",
              gh.owner_token(proj_pr), "user-token")
        answers["login"] = "someone-else"
        check("actor: another account's token is never used", gh.owner_token(proj_pr), None)
        set_owner(bea)
        check("actor: a typed identity never uses a token", gh.owner_token(proj_pr), None)
    finally:
        gh.load, gh.route, gh.user_token, gh.api, gh.org_apps = saved
finally:
    if saved_env is None:
        os.environ.pop("LOAM_HOST_SETTINGS", None)
    else:
        os.environ["LOAM_HOST_SETTINGS"] = saved_env
    shutil.rmtree(root, ignore_errors=True)

print(f"\n{'all passed' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
