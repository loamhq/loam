"""Check commit authorship and attribution across the job, landing and GitHub identity paths."""

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
    repo_id = ("Ada Repo", "ada@repo.example")
    person = {"name": "Pat", "email": "9+pat@users.noreply.github.com", "github_login": "pat"}
    set_owner(OWNER)
    check("order: a project's person first",
          gi.resolve(person, repo_id)["source"], "project")
    check("order: the owner's setting wins over a repository-local identity",
          gi.resolve(None, repo_id)["name"], "Ada Lovelace")
    check("order: the owner's over provision's default",
          gi.resolve(None, eng_id)["name"], "Ada Lovelace")
    set_owner(None)
    check("order: nothing set is the engineer, never a provider",
          gi.resolve(None, eng_id)["source"], "engineer")
    check("order: without an owner identity the checkout can supply one",
          gi.resolve(None, repo_id)["source"], "repo")
    bea = {"source": "typed", "name": "Bea Second", "email": "bea@second.example"}

    # -- A fresh install: nothing set --------------------------------------
    set_owner(None)
    proj, origin = make("fresh")
    r = start(proj, "20261118-100000-00000001")
    check("fresh: the job commits as the engineer", r["attribution"]["source"], "engineer")
    check("fresh: the record carries the prompt",
          "Connect your GitHub account" in r["attribution"]["summary"], True)
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

    # -- The owner's setting and a project's person ------------------------
    proj_r, _ = make("explicit", ident=("Ada Repo", "ada@repo.example"))
    r = start(proj_r, "20261118-100000-00000005")
    check("explicit repo: the job still commits as the owner",
          (r["attribution"]["source"], git(Path(r["path"]), "config", "user.name")),
          ("owner", "Ada Lovelace"))
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

    # -- The default: unset means the one connected account ----------------
    ADA_PROFILE = {"status": "connected", "login": "ada", "id": 1815,
                   "name": "Ada Lovelace"}
    profile = {"value": dict(ADA_PROFILE)}
    gi.PROFILE_SOURCE = lambda: dict(profile["value"])
    gi.GLOBAL_GIT_SOURCE = lambda: ("", "")
    set_owner(None)
    proj_d, origin_d = make("default-fresh")
    r = start(proj_d, "20261118-110000-00000001")
    check("default fresh: the job's tree is the connected account",
          (r["attribution"]["source"], git(Path(r["path"]), "config", "user.name"),
           git(Path(r["path"]), "config", "user.email")),
          ("owner", "Ada Lovelace", "1815+ada@users.noreply.github.com"))
    check("default fresh: the receipt says it is the default, no prompt",
          (r["attribution"]["summary"].endswith("(the default)"),
           "Connect your GitHub" in r["attribution"]["summary"]), (True, False))
    commit(r, "d.txt", "Add d" + CLAUDE, verify=False)
    result = land(proj_d, r)
    check("default fresh: lands as the owner, no provider line",
          (result["state"], people(proj_d.root, "main")[0],
           any("anthropic" in b for b in bodies(proj_d.root, "main"))),
          ("landed", "Ada Lovelace <1815+ada@users.noreply.github.com> / "
                     "Ada Lovelace <1815+ada@users.noreply.github.com>", False))
    check("default fresh: origin has it", git(origin_d, "rev-parse", "main"),
          git(proj_d.root, "rev-parse", "main"))

    # An existing install: engineer commits already landed while nothing was
    # connected; the connection comes later; history keeps its hashes.
    profile["value"] = {"status": "missing"}
    proj_e, origin_e = make("default-existing")
    r = start(proj_e, "20261118-110000-00000002")
    check("existing: before a connection, the engineer and the prompt",
          (r["attribution"]["source"], "no GitHub account is connected"
           in r["attribution"]["summary"]), ("engineer", True))
    commit(r, "e1.txt", "Add e1")
    land(proj_e, r)
    before = git(origin_e, "rev-list", "main").split()
    profile["value"] = dict(ADA_PROFILE)          # connection added
    r = start(proj_e, "20261118-110000-00000003")
    check("existing: connection added, the next job is the owner",
          r["attribution"]["source"], "owner")
    commit(r, "e2.txt", "Add e2")
    land(proj_e, r)
    after = git(origin_e, "rev-list", "main").split()
    check("existing: what landed before keeps its hashes",
          set(before) <= set(after), True)
    check("existing: earlier commit still the engineer's",
          people(proj_e.root, before[0])[0].split(" / ")[0],
          "demo engineer <engineer+demo@noreply.local>")
    profile["value"] = {"status": "missing"}        # connection removed
    r = start(proj_e, "20261118-110000-00000004")
    check("existing: connection removed, back to the engineer with the prompt",
          (r["attribution"]["source"], git(Path(r["path"]), "config", "user.name")),
          ("engineer", "demo engineer"))

    # Missing or ambiguous: never a guess.
    profile["value"] = {"status": "ambiguous", "logins": ["ada", "ada-work"]}
    r = start(proj_e, "20261118-110000-00000005")
    check("ambiguous: the engineer, and the prompt names both and says choose",
          (r["attribution"]["source"], "ada, ada-work" in r["attribution"]["summary"],
           "Choose one" in r["attribution"]["summary"]), ("engineer", True, True))
    profile["value"] = {"status": "unreachable", "login": "ada", "error": "x"}
    check("unreachable: the engineer, never a guessed address",
          gi.resolve(None, eng_id)["source"], "engineer")

    # No connection: a global git identity only if it is already a no-reply.
    profile["value"] = {"status": "missing"}
    eng_id = ("demo engineer", "engineer+demo@noreply.local")
    gi.GLOBAL_GIT_SOURCE = lambda: ("Ada Box", "ada.private@example.org")
    check("privacy: a private global address is never used",
          gi.resolve(None, eng_id)["source"], "engineer")
    gi.GLOBAL_GIT_SOURCE = lambda: ("Ada Box", "77+ada@users.noreply.github.com")
    got = gi.resolve(None, eng_id)
    check("git-config: a global no-reply identity is the default",
          (got["source"], got["via"], got["name"]), ("owner", "git-config", "Ada Box"))
    gi.GLOBAL_GIT_SOURCE = lambda: ("Claude", "noreply@anthropic.com")
    check("git-config: a provider's global identity is not",
          gi.resolve(None, eng_id)["source"], "engineer")
    profile["value"] = {"status": "ambiguous", "logins": ["a", "b"]}
    gi.GLOBAL_GIT_SOURCE = lambda: ("Ada Box", "77+ada@users.noreply.github.com")
    check("git-config: not consulted when two accounts are connected",
          gi.resolve(None, eng_id)["source"], "engineer")
    gi.GLOBAL_GIT_SOURCE = lambda: ("", "")
    profile["value"] = dict(ADA_PROFILE)

    # Explicit choices win over the default; neutral opts out.
    set_owner({"source": "neutral"})
    r = start(proj_e, "20261118-110000-00000006")
    check("neutral: the engineer, no prompt, says it was chosen",
          (r["attribution"]["source"], "Connect" in r["attribution"]["summary"],
           "neutral" in r["attribution"]["summary"]), ("engineer", False, True))
    check("neutral: the view says so",
          (gi.public()["identity"], gi.public()["status"], gi.public()["prompt"]),
          ("neutral", "neutral", None))
    set_owner(bea)
    check("explicit typed wins over the default",
          gi.resolve(None, eng_id)["name"], "Bea Second")
    set_owner(None)
    view = gi.public()
    check("view: unset is the default, with what commits carry",
          (view["identity"], view["status"], view["effective"]["email"], view["prompt"]),
          (None, "default", "1815+ada@users.noreply.github.com", None))
    check("setting: neutral and default round-trip",
          (gi.validate({"identity": "neutral"}), gi.validate({"identity": "default"}),
           gi.validate({"identity": None})), ({"source": "neutral"}, None, None))
    neutral = ("Loam engineer", "engineer+loam@noreply.local")
    proj_g, origin_g = make("gated-default", ident=neutral)
    deny = root / "gated-deny"
    deny.write_text("Ada Lovelace\n")
    git(proj_g.root, "config", "loam.pushGate", "true")
    git(proj_g.root, "config", "loam.pushDeny", str(deny))
    r = start(proj_g, "20261118-110000-00000007")
    check("gated: a new clone uses the owner's GitHub default",
          (r["attribution"]["source"], git(Path(r["path"]), "config", "user.name")),
          ("owner", "Ada Lovelace"))
    commit(r, "legacy.txt", "Work from an older job" + CLAUDE, who=neutral, verify=False)
    (proj_g.root / "other.txt").write_text("main moved\n")
    git(proj_g.root, "add", "other.txt")
    git(proj_g.root, "commit", "-qm", "Main moved while the job ran")
    git(proj_g.root, "push", "-q", "origin", "main")
    before = git(origin_g, "rev-parse", "main")
    result = land(proj_g, r)
    landed = git(origin_g, "rev-parse", "main")
    check("gated: the older job commit and the merge push as the owner",
          (result["state"], result["pushed"], result["reauthored"],
           git(origin_g, "rev-parse", "main^1"),
           people(origin_g, "main") + people(origin_g, "main^2")),
          ("landed", True, True, before, [f"{want} / {want}"] * 2))
    check("gated: the job's provider trailers were removed",
          bodies(origin_g, f"{before}..{landed}"),
          [f"Land {r['branch']}", "Work from an older job"])
    r = start(proj_g, "20261118-110000-00000009")
    commit(r, "private.txt", "Mention Ada Lovelace in prose")
    result = wt.land(proj_g, r, "Land privacy check", keep_tree=True)
    check("gated: the deny list still holds the owner's name in a message",
          (result["state"], result["reason"], git(origin_g, "rev-parse", "main")),
          ("not_landed", "gate", landed))
    r = start(proj_g, "20261118-110000-0000000a")
    commit(r, "retry.txt", "Landed before the push recovered", who=neutral)
    git(proj_g.root, "fetch", "-q", r["path"], r["branch"])
    git(proj_g.root, "merge", "--quiet", "--ff-only", "FETCH_HEAD")
    check("gated: a retry resolves the owner for a pending push",
          (wt.push_origin(proj_g), people(origin_g), git(origin_g, "rev-parse", "main^")),
          (True, [f"{want} / {want}"], landed))
    proj_rp, _ = make("repo-default", ident=("Ada Repo", "ada@repo.example"))
    check("repo fallback: the GitHub default wins over a repository identity",
          start(proj_rp, "20261118-110000-00000008")["attribution"]["source"], "owner")

    # The default is metadata only: it never makes the owner the PR actor.
    import loam_github_app as gh  # noqa: E402
    proj_pr = eng.Project("pr", root / "pr", root / "pr-jobs", platform=False,
                          repo="ada/thing", record={"name": "pr"})
    saved_gh = (gh.load, gh.route, gh.user_token, gh.api, gh.org_apps)
    try:
        gh.load = lambda: {}
        gh.route = lambda data, repo: ({}, {"installation_id": 1})
        gh.org_apps = lambda data: {}
        gh.user_token = lambda **kw: "user-token"
        gh.api = lambda method, path, token, **kw: (200, {"login": "ada", "id": 1815})
        set_owner(None)
        check("actor: the default never uses the owner's token",
              gh.owner_token(proj_pr), None)
    finally:
        gh.load, gh.route, gh.user_token, gh.api, gh.org_apps = saved_gh
    gi.PROFILE_SOURCE = gi.GLOBAL_GIT_SOURCE = None

    # -- owner_profile, against a fixture store ----------------------------
    saved_store = (gh.STORE_DIR, gh.STORE_PATH, gh.LOCK_PATH, gh.api, gh.user_token)
    calls = []
    try:
        gh.STORE_DIR = root / "gh"
        gh.STORE_PATH = gh.STORE_DIR / "integration.json"
        gh.LOCK_PATH = gh.STORE_DIR / "integration.lock"
        gh.user_token = lambda force_refresh=False, org=None: f"tok-{org}"
        me = {"value": {"login": "ada", "id": 1815, "name": "Ada Lovelace"}}

        def fake_api(method, path, token, **kw):
            calls.append((path, token))
            if isinstance(me["value"], Exception):
                raise me["value"]
            return 200, dict(me["value"])
        gh.api = fake_api
        check("profile: no store is missing", gh.owner_profile()["status"], "missing")
        user = {"login": "ada", "token": "t"}
        gh.save({"app": {"id": 1}, "user": dict(user),
                 "org_apps": {"adaorg": {"app": {"id": 2}, "user": dict(user)}}})
        got = gh.owner_profile()
        check("profile: one login across both apps is connected",
              (got["status"], got["id"], got["name"], calls[-1]),
              ("connected", 1815, "Ada Lovelace", ("/user", "tok-None")))
        check("profile: the id is cached on the authorization",
              gh.load()["user"]["profile"]["id"], 1815)
        n = len(calls)
        gh.owner_profile()
        check("profile: not re-read within a day", len(calls), n)
        me["value"] = gh.IntegrationError("down", 502)
        got = gh.owner_profile(max_age=0)
        check("profile: a failed read keeps the cache",
              (got["status"], got["id"]), ("connected", 1815))
        data = gh.load()
        data["user"].pop("profile")
        gh.save(data)
        check("profile: never read and unreachable",
              gh.owner_profile()["status"], "unreachable")
        data["org_apps"]["adaorg"]["user"]["login"] = "ada-work"
        gh.save(data)
        got = gh.owner_profile()
        check("profile: two logins is ambiguous, never a guess",
              (got["status"], got["logins"]), ("ambiguous", ["ada", "ada-work"]))
        gh.save({"app": {"id": 1}})
        check("profile: an app with no authorization is missing",
              gh.owner_profile()["status"], "missing")
        me["value"] = {"login": "bea", "id": 9}
        gh.save({"app": {"id": 1, "slug": "loam"}, "user": dict(user)})
        check("profile: GitHub answering for another account is not used",
              gh.owner_profile()["status"], "unreachable")
        check("profile: the public view carries no profile or token",
              "profile" in json.dumps(gh.public_view()), False)
    finally:
        gh.STORE_DIR, gh.STORE_PATH, gh.LOCK_PATH, gh.api, gh.user_token = saved_store
finally:
    if saved_env is None:
        os.environ.pop("LOAM_HOST_SETTINGS", None)
    else:
        os.environ["LOAM_HOST_SETTINGS"] = saved_env
    shutil.rmtree(root, ignore_errors=True)

print(f"\n{'all passed' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
