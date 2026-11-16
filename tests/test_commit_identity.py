#!/usr/bin/env python3
"""A project whose commits and pull requests are a person's (since 2026-10-06).

lib/loam_commit_identity.py. Against a fake GitHub on loopback and real git
repositories in a temp directory standing in for the remote:

  commits     a job's clone of a project with `commit_identity` commits as
              that person, author and committer; a project without one keeps
              the engineer's identity; the CLI is handed attribution off.
  refusal     a branch with a Co-Authored-By trailer, another author, or a
              message naming the tools is refused at landing and again by
              the broker, before any push; ordinary words pass, "engineer"
              and the personas' names among them.
  pull        the broker pushes and opens the pull request with the person's
  request     user token after checking its login, never the installation's;
              its body is the job's own `pull-request.md` plus how it was
              tested, never the receipt; none written, or one naming the
              machinery, is refused. The job's own token only reads.
  provision   --commit-as reads the account's id and name and writes the
              noreply address.

What GitHub then displays (author, committer, `author.login`, the PR's
`user.login`, any app badge) a fake cannot say; that was caused against
GitHub on 2026-10-06 and is recorded in broker/README.md.

Run: python3 tests/test_commit_identity.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_engineer as eng  # noqa: E402
import loam_github  # noqa: E402
import loam_github_app as gh  # noqa: E402
import loam_commit_identity as ident  # noqa: E402
import loam_lock  # noqa: E402
import loam_worktree as wt  # noqa: E402


def load_script(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


prov = load_script("provision_identity", AGENTS / "bin" / "provision")
engineer_job = load_script("engineer_job_identity", AGENTS / "bin" / "engineer-job")

results: list[bool] = []


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:72} got={got!r}"[:260])
    if not ok:
        print(f"      wanted {want!r}")


def config(repo: Path, key: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "config", key], capture_output=True,
                          text=True, check=True).stdout.strip()


def git(repo: Path, *args: str, who: tuple[str, str] = ("t", "t@t")) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", f"user.name={who[0]}", "-c", f"user.email={who[1]}",
         *args], capture_output=True, text=True, check=True).stdout.strip()


KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(serialization.Encoding.PEM,
                        serialization.PrivateFormat.TraditionalOpenSSL,
                        serialization.NoEncryption()).decode()
OWNER_IDENTITY = {"name": "Sam",
           "email": "1000001+octo-owner@users.noreply.github.com",
           "github_login": "octo-owner"}


class Fake:
    calls: list[tuple[str, str, str, dict]] = []
    login = "octo-owner"
    permissions = {"contents": "write", "metadata": "read", "workflows": "write",
                   "pull_requests": "write", "issues": "write"}
    pulls: dict[str, dict] = {}
    numbered = 0
    minted = 0


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def answer(self, status: int, body) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):  # noqa: N802
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = json.loads(raw) if raw else {}
        auth = self.headers.get("Authorization", "")
        Fake.calls.append(("POST", self.path, auth, body))
        parts = self.path.strip("/").split("/")
        if parts[:2] == ["app", "installations"] and parts[3:] == ["access_tokens"]:
            Fake.minted += 1
            return self.answer(201, {"token": f"ghs_{Fake.minted}", "expires_at":
                                     time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                   time.gmtime(time.time() + 3600))})
        if parts[:1] == ["repos"] and parts[3:] == ["pulls"]:
            key = f"{parts[1]}/{parts[2]}:{body.get('head')}"
            if (Fake.pulls.get(key) or {}).get("state") == "open":
                return self.answer(422, {"message": "Validation Failed",
                                         "errors": ["A pull request already exists"]})
            Fake.numbered += 1
            Fake.pulls[key] = {"number": Fake.numbered, "auth": auth, **body,
                               "state": "open",
                               "html_url": f"https://github.com/x/pull/{Fake.numbered}"}
            return self.answer(201, Fake.pulls[key])
        return self.answer(404, {"message": "Not Found"})

    def do_GET(self):  # noqa: N802
        auth = self.headers.get("Authorization", "")
        Fake.calls.append(("GET", self.path, auth, {}))
        parts = self.path.partition("?")[0].strip("/").split("/")
        if parts[:2] == ["app", "installations"] and len(parts) == 3:
            return self.answer(200, {"id": int(parts[2]),
                                     "account": {"login": "acme-health"},
                                     "permissions": dict(Fake.permissions)})
        if parts == ["user"]:
            return self.answer(200, {"login": Fake.login})
        if parts[:1] == ["repos"] and parts[3:] == ["pulls"]:
            query = urllib.parse.parse_qs(self.path.partition("?")[2])
            owner_head = query.get("head", [""])[0].partition(":")[2]
            key = f"{parts[1]}/{parts[2]}:{owner_head}"
            state = query.get("state", ["open"])[0]
            found = Fake.pulls.get(key)
            return self.answer(200, [found] if found and state in ("all", found["state"])
                               else [])
        if parts[:1] == ["users"] and len(parts) == 2:
            return self.answer(200, {"login": parts[1], "id": 1000001,
                                     "name": "Sam"})
        return self.answer(404, {"message": "Not Found"})


def raised(fn, *args, **kwargs) -> str | None:
    try:
        fn(*args, **kwargs)
        return None
    except (gh.IntegrationError, SystemExit) as exc:
        return str(exc) or "exit"


root = Path(tempfile.mkdtemp(prefix="loam-identity-test-"))
httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
fake_url = f"http://127.0.0.1:{httpd.server_address[1]}"
threading.Thread(target=httpd.serve_forever, daemon=True).start()
saved_gh = {n: getattr(gh, n) for n in ("STORE_DIR", "STORE_PATH", "LOCK_PATH", "API", "WEB")}
saved_eng = {n: getattr(eng, n) for n in ("PROJECTS_DIR", "PROJECT_STATE", "PROJECT_CHECKOUTS")}
saved_other = (wt.WORKTREES, loam_lock.LOCK_DIR, wt.open_pr)
try:
    gh.STORE_DIR = root / "github"
    gh.STORE_PATH = gh.STORE_DIR / "integration.json"
    gh.LOCK_PATH = gh.STORE_DIR / "integration.lock"
    gh.API = gh.WEB = fake_url
    eng.PROJECTS_DIR = root / "projects"
    eng.PROJECT_STATE = root / "project-state"
    eng.PROJECT_CHECKOUTS = root / "checkouts"
    wt.WORKTREES = root / "worktrees"
    loam_lock.LOCK_DIR = root / "locks"
    os.environ.pop(loam_lock.LIVE_HELD_VAR, None)

    # The first app on octo-owner; Acme's org app with the owner's own pair.
    gh.save({"app": {"id": 1, "slug": "loam", "client_id": "Iv1", "client_secret": "s",
                     "pem": PEM},
             "installation": {"installation_id": 99, "account": "octo-owner"},
             "user": {"login": "octo-owner", "token": "ghu_first",
                      "token_expires_at": "2099-01-01T00:00:00Z"},
             "org_apps": {"acme-health": {
                 "app": {"id": 2, "slug": "loam-for-acme", "client_id": "Iv2",
                         "client_secret": "s", "pem": PEM},
                 "installation": {"installation_id": 555, "account": "acme-health"},
                 "user": {"login": "octo-owner", "token": "ghu_acme",
                          "token_expires_at": "2099-01-01T00:00:00Z"}}}})

    # Their repository, adopted by hand the way provision --adopt leaves it.
    bare = root / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "develop", str(bare)], check=True)
    seed = root / "seed"
    subprocess.run(["git", "init", "-q", "-b", "develop", str(seed)], check=True)
    (seed / "app.py").write_text("print('acme')\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-qm", "theirs", who=("Their CEO", "ceo@acme.example"))
    git(seed, "push", "-q", str(bare), "develop")
    checkout = eng.PROJECT_CHECKOUTS / "acme"
    checkout.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", str(bare), str(checkout)], check=True)
    git(checkout, "branch", "-m", "develop", "main")
    git(checkout, "config", "user.name", "acme engineer")
    git(checkout, "config", "user.email", "engineer+acme@noreply.local")
    eng.PROJECTS_DIR.mkdir(parents=True)
    record = {"name": "acme", "repo": "acme-health/backend", "remote": str(bare),
              "checkout": str(checkout), "landing": "pull_request",
              "default_branch": "develop", "commit_identity": dict(OWNER_IDENTITY)}
    eng.project_record_path("acme").write_text(json.dumps(record))
    their_develop = git(bare, "rev-parse", "develop")

    wt.open_pr = lambda p, branch, title, body: gh.open_pull_request(
        p.name, branch, title, body)

    def project() -> eng.Project:
        return eng.projects()["acme"]

    def start(job_id: str) -> tuple[dict, Path]:
        job_dir = project().jobs / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        return wt.setup(project(), job_id, job_dir), job_dir

    def commit(rec: dict, rel: str, text: str, message: str = "Fix the reminder",
               who: tuple[str, str] | None = None) -> str:
        tree = Path(rec["path"])
        (tree / rel).write_text(text)
        args = ["-C", str(tree)]
        if who:
            args += ["-c", f"user.name={who[0]}", "-c", f"user.email={who[1]}"]
        subprocess.run(["git", *args, "add", "-f", rel], check=True)
        subprocess.run(["git", *args, "commit", "-qm", message], check=True)
        return git(tree, "rev-parse", "HEAD")

    def on_remote(branch: str) -> bool:
        return subprocess.run(["git", "-C", str(bare), "rev-parse", "--verify", "--quiet",
                               branch], capture_output=True).returncode == 0

    # == point 1: commits ==================================================
    job = "20261006-180000-aaaaaaaa"
    rec, job_dir = start(job)
    tree = Path(rec["path"])
    commit(rec, "app.py", "print('fixed')\n")
    check("test_job_commits_as_the_person: author and committer",
          git(tree, "log", "-1", "--format=%an <%ae> | %cn <%ce>"),
          f"{OWNER_IDENTITY['name']} <{OWNER_IDENTITY['email']}> | {OWNER_IDENTITY['name']} <{OWNER_IDENTITY['email']}>")
    check("...the tree's record says so", rec.get("commit_identity"),
          f"{OWNER_IDENTITY['name']} <{OWNER_IDENTITY['email']}>")
    check("test_cli_attribution_off: the settings key the pinned build reads",
          ident.CLI_SETTINGS["attribution"], {"commit": "", "pr": "", "sessionUrl": False})
    note = engineer_job.tree_note(project(), rec)
    check("test_job_prompt_names_the_identity_and_the_pr_file",
          (f"`{OWNER_IDENTITY['name']} <{OWNER_IDENTITY['email']}>`" in note,
           ident.PR_BODY_FILE in note, "receipt's opening" in note), (True, True, False))
    check("...the launcher's settings for such a job carry attribution off",
          bool(engineer_job.identity_of(project())), True)

    # == point 3, local: refusals ==========================================
    check("test_words_are_words: ordinary prose passes",
          ident.text_problems("Engineering note: reengineer the Claudette loamy "
                              "parser; iris-scan stays"), [])
    check("test_our_vocabulary_passes: the engineer and a persona's name are "
          "ordinary words",
          ident.text_problems("Let the engineer on call see Iris's chart; Ada "
                              "colours the crown"), [])
    check("...and the tools are named",
          ident.text_problems("Fix\n\nCo-Authored-By: Claude Opus <noreply@anthropic.com>\n"
                              "Claude-Session: abc\nGenerated-By: x\n"
                              "Generated with Claude Code and Codex, job "
                              "20261006-180000-aaaaaaaa on loam-ui"),
          ["a Co-Authored-By trailer", "a Claude-Session trailer", "a Generated-By trailer",
           "\"noreply@anthropic.com\"", "\"Generated with\"",
           "\"Claude\"", "\"Codex\"", "\"loam\"",
           "a job id (20261006-180000-aaaaaaaa)"])

    commit(rec, "app.py", "print('again')\n",
           message="Tidy\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>")
    (job_dir / ident.PR_BODY_FILE).write_text("# Fix the reminder\n\nA plain body.\n")
    result = wt.land(project(), rec, "Land", title="t", summary="A plain body.")
    check("test_coauthor_trailer_refused_before_push",
          (result["state"], result["reason"], any("Co-Authored-By" in f
                                                  for f in result["files"]),
           on_remote(f"job/{job}")), ("not_landed", "identity", True, False))
    brief = wt.continuation_brief(project(), job, result)
    check("...the continuation says how to rewrite it", "--reset-author" in brief, True)

    git(project().root, "fetch", "-q", rec["path"],
        f"+refs/heads/job/{job}:refs/heads/job/{job}")
    said = raised(gh.open_pull_request, "acme", f"job/{job}", "t", "b")
    check("test_broker_refuses_it_too: nothing pushed even if the landing were skipped",
          ("not Sam's alone" in (said or ""), on_remote(f"job/{job}")),
          (True, False))

    # Rewritten: the trailer gone, and a commit by someone else is refused.
    git(tree, "reset", "-q", "--soft", "HEAD~2")
    git(tree, "commit", "-qm", "Fix the reminder", who=(OWNER_IDENTITY["name"], OWNER_IDENTITY["email"]))
    commit(rec, "app.py", "print('x')\n", who=("acme engineer", "engineer+acme@noreply.local"))
    result = wt.land(project(), rec, "Land", title="t", summary="A plain body.")
    check("test_other_author_refused",
          (result["reason"], sum("author is acme engineer" in f for f in result["files"])),
          ("identity", 1))
    git(tree, "reset", "-q", "--hard", "HEAD~1")
    own_tip = git(tree, "rev-parse", "HEAD")

    # The remote's default branch moves during the job (2026-10-20, acme
    # #29): someone else's commit and GitHub's merge commit land on it after
    # the job's setup fetched, and the job merges it from GitHub directly.
    other = root / "other"
    subprocess.run(["git", "clone", "-q", "-b", "develop", str(bare), str(other)],
                   check=True)
    (other / "theirs.py").write_text("print('theirs')\n")
    git(other, "add", "-A")
    git(other, "commit", "-qm", "Their change", who=("A Teammate", "teammate@acme.example"))
    git(other, "checkout", "-qb", "side", "HEAD~1")
    (other / "side.py").write_text("print('side')\n")
    git(other, "add", "-A")
    git(other, "commit", "-qm", "Side", who=("A Teammate", "teammate@acme.example"))
    git(other, "checkout", "-q", "develop")
    git(other, "merge", "-q", "--no-ff", "-m", "Merge pull request #28", "side",
        who=("GitHub", "noreply@github.com"))
    git(other, "push", "-q", str(bare), "develop")
    their_develop = git(bare, "rev-parse", "develop")
    check("...the canonical has not seen the remote move",
          git(project().root, "rev-parse", "main") == their_develop, False)
    git(tree, "fetch", "-q", str(bare), "develop")
    git(tree, "merge", "-q", "--no-ff", "-m", "Merge current main", "FETCH_HEAD",
        who=(OWNER_IDENTITY["name"], OWNER_IDENTITY["email"]))
    result = wt.land(project(), rec, "Land", title="t", summary="")
    check("test_main_merged_in_passes: the remote's own commits are not ours to "
          "answer for",
          (result["reason"], result["files"]), ("pr_body", [ident.PR_BODY_FILE]))
    check("...because the landing read the remote's branch afresh",
          git(project().root, "rev-parse", "refs/remotes/origin/develop"), their_develop)
    merged = git(tree, "rev-parse", "HEAD")
    check("...and the broker, reading the same canonical, agrees",
          ident.problems(project().root, "refs/heads/main", merged, OWNER_IDENTITY), [])
    check("...and a broker still on the old main..tip walk reads only the job's own",
          git(project().root, "rev-list", f"main..{merged}").split(), [merged, own_tip])

    foreign = commit(rec, "app.py", "print('foreign')\n",
                     who=("Their CEO", "ceo@acme.example"))
    result = wt.land(project(), rec, "Land", title="t", summary="")
    check("test_foreign_commit_after_main_merged_still_refused: only it is named",
          (result["reason"], sorted({f.split(":")[0] for f in result["files"]})),
          ("identity", [foreign[:10]]))
    git(tree, "update-ref", "refs/remotes/origin/sneak", foreign)
    result = wt.land(project(), rec, "Land", title="t", summary="")
    check("...a remote ref the job writes in its own clone counts for nothing",
          result["reason"], "identity")
    git(tree, "update-ref", "-d", "refs/remotes/origin/sneak")
    git(project().root, "fetch", "-q", rec["path"],
        f"+refs/heads/job/{job}:refs/heads/job/{job}")
    said = raised(gh.open_pull_request, "acme", f"job/{job}", "t", "b")
    check("...and the broker refuses it, naming that commit alone",
          (foreign[:10] in (said or ""), "A Teammate" in (said or ""), "GitHub <" in (said or ""),
           on_remote(f"job/{job}")), (True, False, False, False))
    git(tree, "reset", "-q", "--hard", own_tip)

    # == point 2: the pull request =========================================
    result = wt.land(project(), rec, "Land", title="Fix the reminder", summary="")
    check("test_no_pr_description_refused: the receipt never stands in",
          (result["reason"], on_remote(f"job/{job}")), ("pr_body", False))
    result = wt.land(project(), rec, "Land", title="Fix the reminder",
                     summary="Written with Codex.")
    check("test_pr_text_naming_the_tools_refused",
          (result["reason"], result["files"]), ("pr_text", ["\"Codex\""]))

    own = ident.pr_text(job_dir, "brief title")
    check("test_pr_text_from_the_job_file: heading is the title",
          own, ("Fix the reminder", "A plain body."))
    # Someone else's branch already has the title's name.
    git(bare, "branch", "fix-the-reminder", "develop")
    before = len(Fake.calls)
    result = wt.land(project(), rec, "Land", title=own[0], summary=own[1])
    pr = Fake.pulls.get("acme-health/backend:fix-the-reminder-2") or {}
    check("test_pr_opened_with_the_persons_user_token",
          (result["state"], pr.get("auth"), on_remote("fix-the-reminder-2")),
          ("pull_request", "Bearer ghu_acme", True))
    check("test_pr_head_is_plain: from the title, deduplicated, no job id anywhere "
          "on the remote",
          (result["pull_request"]["head"], on_remote(f"job/{job}"),
           [b for b in git(bare, "branch", "--format=%(refname:short)").split()
            if job in b], git(bare, "rev-parse", "fix-the-reminder"), pr.get("head")),
          ("fix-the-reminder-2", False, [], their_develop, "fix-the-reminder-2"))
    check("...the landing's report names the head",
          "as `fix-the-reminder-2`" in wt.receipt_section(project(), job, result), True)
    again = gh.open_pull_request("acme", f"job/{job}", "A retitled continuation",
                                 "A plain body.")
    check("test_pr_head_continuation_finds_the_same_pr",
          (again["head"], again["created"], again["number"]),
          ("fix-the-reminder-2", False, pr["number"]))
    check("...the token's login checked before anything was pushed",
          [c[1] for c in Fake.calls[before:] if c[2] == "Bearer ghu_acme"][:1], ["/user"])
    check("...no installation token minted for the push",
          [c[1] for c in Fake.calls[before:] if c[1].endswith("/access_tokens")], [])
    check("test_pr_body_is_plain_prose: the job's text and **Untested.**",
          (pr.get("title"), pr.get("body")),
          ("Fix the reminder", "A plain body.\n\n**Untested.**"))
    check("...their develop never moved", git(bare, "rev-parse", "develop"), their_develop)

    job2 = "20261006-180100-bbbbbbbb"
    rec2, _ = start(job2)
    commit(rec2, "app.py", "print('two')\n")
    git(project().root, "fetch", "-q", rec2["path"],
        f"+refs/heads/job/{job2}:refs/heads/job/{job2}")
    Fake.login = "someone-else"
    said = raised(gh.open_pull_request, "acme", f"job/{job2}", "Fix", "Body")
    Fake.login = "octo-owner"
    check("test_user_token_of_another_login_refused: nothing pushed",
          ("someone-else" in (said or ""), on_remote(f"job/{job2}")), (True, False))
    env = dict(os.environ)
    check("test_pr_head_another_job_same_title_gets_its_own",
          gh.pr_head(project(), f"job/{job2}", "Fix the reminder!", "develop", env),
          "fix-the-reminder-3")
    check("test_pr_head_never_the_default_branch",
          gh.pr_head(project(), "job/20261006-180200-cccccccc", "Develop", "develop", env),
          "develop-2")
    check("...a title with no words still names a branch",
          gh._slug("!!!"), "change")

    # == a continuation and the pull request it had (2026-10-21) ===========
    # acme-foundation: a continuation rebuilt its work on a newer main after
    # #46 was closed, and three landings pushed it at #46's branch and were
    # rejected as non-fast-forward.
    first = Fake.pulls["acme-health/backend:fix-the-reminder-2"]
    n_cont = iter(range(10, 99))

    def cont(prev_dir: Path) -> tuple[dict, Path]:
        """A continuation of the job in ``prev_dir``: its tree reopened."""
        cdir = project().jobs / f"20261021-1700{next(n_cont)}-eeeeeeee"
        cdir.mkdir(parents=True)
        return wt.setup(project(), cdir.name, cdir, continues=prev_dir), cdir

    rec, job_dir = cont(job_dir)
    tree = Path(rec["path"])
    reviewed = commit(rec, "app.py", "print('reviewed')\n")
    result = wt.land(project(), rec, "Land", title="Fix the reminder", summary="A plain body.")
    check("test_fast_forward_continuation_updates_the_same_pr",
          (result["state"], result["pull_request"]["head"], result["pull_request"]["number"],
           result["pull_request"]["created"], "replaces" in result["pull_request"],
           git(bare, "rev-parse", "fix-the-reminder-2") == reviewed),
          ("pull_request", "fix-the-reminder-2", first["number"], False, False, True))
    check("...the receipt says updated, and nothing about a fresh branch",
          ("(updated)" in wt.receipt_section(project(), job, result),
           "fresh branch" in wt.receipt_section(project(), job, result)), (True, False))
    old_head = git(bare, "rev-parse", "fix-the-reminder-2")
    rec, job_dir = cont(job_dir)
    tree = Path(rec["path"])
    # Rebuilt as one commit on the remote's newer develop.
    git(tree, "fetch", "-q", str(bare), "develop")
    git(tree, "reset", "-q", "--hard", "FETCH_HEAD")
    rebuilt = commit(rec, "app.py", "print('rebuilt')\n")
    result = wt.land(project(), rec, "Land", title="Fix the reminder, rebuilt",
                     summary="A plain body.")
    check("test_rebuilt_continuation_with_open_pr_refused: nothing pushed, no second PR",
          (result["state"], result["reason"], git(bare, "rev-parse", "fix-the-reminder-2"),
           on_remote("fix-the-reminder-rebuilt"),
           sum(1 for k in Fake.pulls if k.startswith("acme-health/backend:fix-the-reminder"))),
          ("not_landed", "open_pr_diverged", old_head, False, 1))
    said = wt.receipt_section(project(), job, result)
    check("...in a sentence naming the open pull request and what to do",
          (f"#{first['number']} from fix-the-reminder-2 is still open" in said,
           "have #" in said, "closed" in said), (True, True, True))
    first["state"] = "closed"
    result = wt.land(project(), rec, "Land", title="Fix the reminder, rebuilt",
                     summary="A plain body.")
    pr = result.get("pull_request") or {}
    fresh = Fake.pulls.get("acme-health/backend:fix-the-reminder-rebuilt") or {}
    check("test_rebuilt_continuation_with_closed_pr_gets_a_new_branch_and_pr",
          (result["state"], pr.get("head"), pr.get("created"), pr.get("number"),
           fresh.get("title"), git(bare, "rev-parse", "fix-the-reminder-rebuilt")),
          ("pull_request", "fix-the-reminder-rebuilt", True, fresh.get("number"),
           "Fix the reminder, rebuilt", rebuilt))
    check("...the old branch left exactly where it was",
          (git(bare, "rev-parse", "fix-the-reminder-2"), pr.get("number") != first["number"]),
          (old_head, True))
    check("...the replaced head named, with why",
          {k: pr.get("replaces", {}).get(k) for k in ("head", "number", "state", "why")},
          {"head": "fix-the-reminder-2", "number": first["number"], "state": "closed",
           "why": "rebuilt"})
    said = wt.receipt_section(project(), job, result)
    check("...and the receipt says which happened",
          ("(opened)" in said, "fresh branch and a new pull request" in said,
           "`fix-the-reminder-2`" in said, "rebuilt" in said), (True, True, True, True))
    check("...recorded, so the next continuation finds the new head",
          config(project().root, f"branch.job/{job}.{gh.PR_HEAD_KEY}"),
          "fix-the-reminder-rebuilt")
    rec, job_dir = cont(job_dir)
    commit(rec, "app.py", "print('rebuilt, reviewed')\n")
    result = wt.land(project(), rec, "Land", title="Anything", summary="A plain body.")
    check("...and fast-forwards it, the same new pull request",
          (result["state"], result["pull_request"]["head"],
           result["pull_request"]["number"], result["pull_request"]["created"]),
          ("pull_request", "fix-the-reminder-rebuilt", fresh["number"], False))
    fresh["state"], fresh["merged_at"] = "closed", "2026-10-21T00:00:00Z"
    rec, job_dir = cont(job_dir)
    commit(rec, "app.py", "print('after the merge')\n")
    result = wt.land(project(), rec, "Land", title="Follow up", summary="A plain body.")
    check("test_fast_forward_onto_a_merged_pr_gets_a_new_branch: never a merged PR's",
          (result["pull_request"]["head"], result["pull_request"]["created"],
           (result["pull_request"].get("replaces") or {}).get("why")),
          ("follow-up", True, "merged"))
    # #46 on 2026-10-22: closed, then its branch deleted before the next
    # landing; the name is not reused for the new pull request.
    Fake.pulls["acme-health/backend:follow-up"]["state"] = "closed"
    git(bare, "branch", "-D", "follow-up")
    rec, job_dir = cont(job_dir)
    commit(rec, "app.py", "print('after the close')\n")
    result = wt.land(project(), rec, "Land", title="Follow up", summary="A plain body.")
    pr = result["pull_request"]
    check("test_closed_pr_whose_branch_was_deleted_gets_a_fresh_name",
          (pr["head"], pr["created"], pr["replaces"]["why"], pr["replaces"]["head_sha"],
           on_remote("follow-up")), ("follow-up-2", True, "closed", None, False))
    check("...and the receipt says the old branch was already gone",
          "Its branch had already been deleted" in wt.receipt_section(project(), job, result),
          True)

    before = len(Fake.calls)
    gh.installation_token("acme-health/backend")
    asked = [c[3] for c in Fake.calls[before:] if c[1].endswith("/access_tokens")][-1]
    check("test_job_token_only_reads_on_a_persons_repository",
          asked["permissions"], {"contents": "read", "issues": "read", "metadata": "read",
                                 "pull_requests": "read"})

    # == existing projects keep the engineer ===============================
    plain = dict(record, name="plain", commit_identity=None)
    plain.pop("commit_identity")
    plain_checkout = eng.PROJECT_CHECKOUTS / "plain"
    subprocess.run(["git", "clone", "-q", str(checkout), str(plain_checkout)], check=True)
    git(plain_checkout, "config", "user.name", "plain engineer")
    git(plain_checkout, "config", "user.email", "engineer+plain@noreply.local")
    plain["checkout"] = str(plain_checkout)
    eng.project_record_path("plain").write_text(json.dumps(plain))
    p_plain = eng.projects()["plain"]
    (p_plain.jobs / "j").mkdir(parents=True)
    prec = wt.setup(p_plain, "20261006-180200-cccccccc", p_plain.jobs / "j")
    check("test_project_without_identity_keeps_the_engineer",
          (config(Path(prec["path"]), "user.name"), "commit_identity" in prec,
           engineer_job.identity_of(p_plain)), ("plain engineer", False, None))

    broken = dict(record, commit_identity={"name": "Sam"})
    eng.project_record_path("acme").write_text(json.dumps(broken))
    try:
        start("20261006-180300-dddddddd")
        said = None
    except wt.WorktreeError as exc:
        said = str(exc)
    check("test_half_written_identity_refused_not_ignored",
          "commit_identity needs name, email and github_login" in (said or ""), True)
    eng.project_record_path("acme").write_text(json.dumps(record))

    # == provision =========================================================
    made = prov.commit_identity("octo-owner")
    check("test_provision_commit_as_reads_id_and_name", made, OWNER_IDENTITY)
    made = prov.commit_identity("octo-owner", email="owner@acme.example")
    check("...an address verified on the account replaces the noreply",
          made["email"], "owner@acme.example")
    prov.set_commit_identity("plain", prov.commit_identity("octo-owner"))
    check("test_provision_sets_identity_on_an_existing_project",
          (json.loads(eng.project_record_path("plain").read_text())["commit_identity"],
           config(plain_checkout, "user.email")), (OWNER_IDENTITY, OWNER_IDENTITY["email"]))
finally:
    for n, v in saved_gh.items():
        setattr(gh, n, v)
    for n, v in saved_eng.items():
        setattr(eng, n, v)
    wt.WORKTREES, loam_lock.LOCK_DIR, wt.open_pr = saved_other
    httpd.shutdown()
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
