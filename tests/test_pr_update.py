#!/usr/bin/env python3
"""Exercise fast-forward updates and replies to an existing pull request against a local remote."""

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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_engineer as eng  # noqa: E402
import loam_github  # noqa: E402,F401
import loam_github_app as gh  # noqa: E402
import loam_commit_identity  # noqa: E402,F401
import loam_lock  # noqa: E402
import loam_pr_target as target  # noqa: E402
import loam_worktree as wt  # noqa: E402


def load_script(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


engineer_job = load_script("engineer_job_pr_update", AGENTS / "bin" / "engineer-job")

results: list[bool] = []


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:72} got={got!r}"[:300])
    if not ok:
        print(f"      wanted {want!r}")


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
WHO = (OWNER_IDENTITY["name"], OWNER_IDENTITY["email"])
REPO = "acme-health/backend"
HEAD = "fix-the-reminder"


class Fake:
    bare: Path
    calls: list[tuple[str, str, str]] = []
    comments: list[dict] = []
    comment_status = 201
    opened: list[dict] = []
    pulls = {7: "open", 8: "closed"}


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

    def pr(self, number: int) -> dict:
        sha = git(Fake.bare, "rev-parse", HEAD)
        return {"number": number, "state": Fake.pulls[number],
                "html_url": f"https://github.com/{REPO}/pull/{number}",
                "head": {"ref": HEAD, "sha": sha, "repo": {"full_name": REPO}},
                "base": {"ref": "develop", "repo": {"full_name": REPO}}}

    def do_POST(self):  # noqa: N802
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = json.loads(raw) if raw else {}
        auth = self.headers.get("Authorization", "")
        Fake.calls.append(("POST", self.path, auth))
        parts = self.path.strip("/").split("/")
        if self.path == "/v1/integrations/github/token":
            return self.answer(200, {"token": "ghs_read", "repository": body["repository"]})
        if parts[:2] == ["app", "installations"] and parts[3:] == ["access_tokens"]:
            return self.answer(201, {"token": "ghs_x", "expires_at": "2099-01-01T00:00:00Z"})
        if parts[:1] == ["repos"] and parts[3:] == ["pulls"]:
            Fake.opened.append(body)
            return self.answer(201, {"number": 99, "html_url": "x"})
        if parts[:1] == ["repos"] and parts[3] == "issues" and parts[5:] == ["comments"]:
            if Fake.comment_status != 201:
                return self.answer(Fake.comment_status, {
                    "message": "Resource not accessible by integration"})
            n = len(Fake.comments) + 1
            Fake.comments.append({"number": int(parts[4]), "auth": auth, **body})
            return self.answer(201, {"id": n, "html_url":
                                     f"https://github.com/{REPO}/pull/{parts[4]}"
                                     f"#issuecomment-{n}"})
        return self.answer(404, {"message": "Not Found"})

    def do_GET(self):  # noqa: N802
        auth = self.headers.get("Authorization", "")
        Fake.calls.append(("GET", self.path, auth))
        parts = self.path.partition("?")[0].strip("/").split("/")
        if parts[:2] == ["app", "installations"] and len(parts) == 3:
            return self.answer(200, {"id": int(parts[2]), "permissions": {
                "contents": "write", "metadata": "read", "pull_requests": "write",
                "issues": "write"}})
        if parts == ["user"]:
            return self.answer(200, {"login": "octo-owner"})
        if parts[:1] == ["repos"] and parts[3:4] == ["pulls"] and len(parts) == 5:
            number = int(parts[4])
            if number not in Fake.pulls:
                return self.answer(404, {"message": "Not Found"})
            return self.answer(200, self.pr(number))
        return self.answer(404, {"message": "Not Found"})


root = Path(tempfile.mkdtemp(prefix="loam-pr-update-test-"))
httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
fake_url = f"http://127.0.0.1:{httpd.server_address[1]}"
threading.Thread(target=httpd.serve_forever, daemon=True).start()
saved_gh = {n: getattr(gh, n) for n in ("STORE_DIR", "STORE_PATH", "LOCK_PATH", "API", "WEB")}
saved_eng = {n: getattr(eng, n) for n in ("PROJECTS_DIR", "PROJECT_STATE", "PROJECT_CHECKOUTS")}
saved_other = (wt.WORKTREES, loam_lock.LOCK_DIR, wt.update_pr, wt.open_pr, target.API)
saved_env = {k: os.environ.get(k) for k in ("LOAM_BROKER_URL", "LOAM_BROKER_TOKEN_FILE")}
try:
    gh.STORE_DIR = root / "github"
    gh.STORE_PATH = gh.STORE_DIR / "integration.json"
    gh.LOCK_PATH = gh.STORE_DIR / "integration.lock"
    gh.API = gh.WEB = target.API = fake_url
    eng.PROJECTS_DIR = root / "projects"
    eng.PROJECT_STATE = root / "project-state"
    eng.PROJECT_CHECKOUTS = root / "checkouts"
    wt.WORKTREES = root / "worktrees"
    loam_lock.LOCK_DIR = root / "locks"
    os.environ.pop(loam_lock.LIVE_HELD_VAR, None)
    (root / "broker-token").write_text("b\n")
    os.environ["LOAM_BROKER_URL"] = fake_url
    os.environ["LOAM_BROKER_TOKEN_FILE"] = str(root / "broker-token")
    # The broker's half runs in-process here, as test_commit_identity does.
    wt.update_pr = lambda p, branch, n, reply: gh.update_pull_request(p.name, branch, n, reply)
    wt.open_pr = lambda p, branch, title, body: gh.open_pull_request(p.name, branch, title, body)

    gh.save({"app": {"id": 1, "slug": "loam", "client_id": "Iv1", "client_secret": "s",
                     "pem": PEM},
             "installation": {"installation_id": 99, "account": "octo-owner"},
             "user": {"login": "octo-owner", "token": "ghu_first",
                      "token_expires_at": "2099-01-01T00:00:00Z"},
             "org_apps": {"acme-health": {
                 "app": {"id": 2, "slug": "loam-for-acme-health", "client_id": "Iv2",
                         "client_secret": "s", "pem": PEM},
                 "installation": {"installation_id": 555, "account": "acme-health"},
                 "user": {"login": "octo-owner", "token": "ghu_acme",
                          "token_expires_at": "2099-01-01T00:00:00Z"}}}})

    # Their repository: develop, and the PR's branch one commit ahead of it.
    bare = Fake.bare = root / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "develop", str(bare)], check=True)
    seed = root / "seed"
    subprocess.run(["git", "init", "-q", "-b", "develop", str(seed)], check=True)
    (seed / "app.py").write_text("print('acme')\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-qm", "theirs", who=WHO)
    git(seed, "push", "-q", str(bare), "develop")
    git(seed, "checkout", "-qb", HEAD)
    (seed / "app.py").write_text("print('first try')\n")
    git(seed, "commit", "-qam", "First try", who=WHO)
    git(seed, "push", "-q", str(bare), HEAD)
    develop = git(bare, "rev-parse", "develop")
    checkout = eng.PROJECT_CHECKOUTS / "acme"
    checkout.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", str(bare), str(checkout)], check=True)
    git(checkout, "branch", "-m", "develop", "main")
    eng.PROJECTS_DIR.mkdir(parents=True)
    record = {"name": "acme", "repo": REPO, "remote": str(bare),
              "checkout": str(checkout), "landing": "pull_request",
              "default_branch": "develop", "commit_identity": dict(OWNER_IDENTITY)}
    eng.project_record_path("acme").write_text(json.dumps(record))

    def project() -> eng.Project:
        return eng.projects()["acme"]

    def start(job_id: str) -> tuple[dict, Path, Path]:
        job_dir = project().jobs / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        rec = wt.setup(project(), job_id, job_dir)
        return rec, job_dir, Path(rec["path"])

    def work_on_pr(tree: Path, text: str, message: str) -> str:
        """What the tree note tells the job: start from the PR's head."""
        git(tree, "fetch", "-q", str(bare), HEAD)
        git(tree, "reset", "-q", "--hard", "FETCH_HEAD")
        (tree / "app.py").write_text(text)
        git(tree, "commit", "-qam", message, who=WHO)
        return git(tree, "rev-parse", "HEAD")

    def land(job_id: str, rec: dict, job_dir: Path) -> dict:
        p = {"dir": job_dir, "receipt": job_dir / "receipt.md"}
        return engineer_job.land_job(project(), job_id, rec,
                                     "Updates #7: revise after review", p)

    # == fast-forward ======================================================
    job = "20261009-090000-aaaaaaaa"
    rec, job_dir, tree = start(job)
    first = git(bare, "rev-parse", HEAD)
    note = engineer_job.tree_note(project(), rec, "Updates #7: revise after review")
    check("test_tree_note_names_the_pr_and_the_reply_file",
          ("pull/7/head" in note, target.REPLY_FILE in note,
           "Before your receipt, write `pull-request.md`" in note), (True, True, False))
    tip = work_on_pr(tree, "print('revised')\n", "Revise after review")
    (job_dir / target.REPLY_FILE).write_text("Thanks. Revised and pushed to this PR.\n")
    result = land(job, rec, job_dir)
    pr = result.get("pull_request") or {}
    check("test_fast_forward_moves_the_prs_head_to_the_job",
          (result["state"], git(bare, "rev-parse", HEAD), pr.get("before"), pr.get("after")),
          ("pull_request", tip, first, tip))
    check("...no pull request opened, develop untouched, no job branch on the remote",
          (Fake.opened, git(bare, "rev-parse", "develop"),
           git(bare, "branch", "--list", f"job/{job}")), ([], develop, ""))
    check("test_reply_posted_on_the_pr_as_the_person_after_the_push",
          Fake.comments, [{"number": 7, "auth": "Bearer ghu_acme",
                           "body": "Thanks. Revised and pushed to this PR."}])
    section = wt.receipt_section(project(), job, result)
    check("...the receipt names the PR, the branch, before and after, the reply",
          (f"https://github.com/{REPO}/pull/7 updated" in section,
           f"`{HEAD}`" in section, f"`{first[:10]}` to `{tip[:10]}`" in section,
           f"https://github.com/{REPO}/pull/7#issuecomment-1" in section),
          (True, True, True, True))

    # == not an ancestor ===================================================
    # Someone else pushes to the PR while the next job works.
    job2 = "20261009-090100-bbbbbbbb"
    rec2, job_dir2, tree2 = start(job2)
    work_on_pr(tree2, "print('second')\n", "Second revision")
    git(seed, "fetch", "-q", str(bare), HEAD)
    git(seed, "reset", "-q", "--hard", "FETCH_HEAD")
    (seed / "other.py").write_text("x = 1\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-qm", "A reviewer's fixup", who=WHO)
    git(seed, "push", "-q", str(bare), HEAD)
    theirs = git(bare, "rev-parse", HEAD)
    (job_dir2 / target.REPLY_FILE).write_text("Second reply.\n")
    result = land(job2, rec2, job_dir2)
    check("test_not_an_ancestor_refused_nothing_pushed_nothing_posted",
          (result["state"], result["reason"], git(bare, "rev-parse", HEAD),
           len(Fake.comments), Fake.opened),
          ("not_landed", "not_fast_forward", theirs, 1, []))
    section = wt.receipt_section(project(), job2, result)
    check("...the receipt says why, and the continuation says how",
          ("not an ancestor" in section, "nothing here force-pushes" in section,
           "Nothing was pushed" in section, "git fetch origin pull/7/head" in section,
           f"still at `{theirs[:10]}`" in section), (True, True, True, True, True))

    # == the reply refused ==================================================
    job3 = "20261009-090200-cccccccc"
    rec3, job_dir3, tree3 = start(job3)
    tip3 = work_on_pr(tree3, "print('third')\n", "Third revision")
    (job_dir3 / target.REPLY_FILE).write_text("Third reply.\n")
    Fake.comment_status = 403
    result = land(job3, rec3, job_dir3)
    Fake.comment_status = 201
    check("test_403_push_done_nothing_posted",
          (result["state"], git(bare, "rev-parse", HEAD), len(Fake.comments),
           (result["pull_request"]["reply"] or {}).get("state")),
          ("pull_request", tip3, 1, "refused"))
    section = wt.receipt_section(project(), job3, result)
    check("...the receipt names the permission and the app to grant it on",
          ("The reply was not posted" in section, "GitHub returned 403" in section,
           "Pull requests: Read and write" in section,
           "`loam-for-acme-health`" in section, "on `acme-health`" in section),
          (True, True, True, True, True))

    # == brief time ========================================================
    def briefs() -> list[str]:
        return sorted(d.name for d in project().jobs.iterdir()) if project().jobs.is_dir() else []

    def accept(title: str):
        return engineer_job.accept({"project": "acme", "title": title, "brief": "Do it.",
                                    "involvement": "go"}, "ada")

    had = briefs()
    said = accept("Updates #8: on a closed one")
    check("test_brief_naming_a_closed_pr_refused_before_a_job_exists",
          (isinstance(said, str) and "#8, which is closed on" in said, briefs()),
          (True, had))
    said = accept("Updates #12: on none")
    check("...one that does not exist, likewise",
          (isinstance(said, str) and "does not exist" in said, briefs()), (True, had))
    said = accept("Updates #7: on the open one")
    check("...an open one is accepted", isinstance(said, tuple), True)
    asked = len(Fake.calls)
    said = accept("Fix the reminder (#7 aside)")
    check("test_title_naming_none_asks_github_nothing",
          (isinstance(said, tuple), len(Fake.calls) - asked), (True, 0))
    plain = dict(record, name="plain", landing="main")
    eng.project_record_path("plain").write_text(json.dumps(plain))
    check("...a project that does not land by pull request refuses the form",
          "does not land by pull request" in (target.refusal(eng.projects()["plain"],
                                                             "Updates #7") or ""), True)
finally:
    for n, v in saved_gh.items():
        setattr(gh, n, v)
    for n, v in saved_eng.items():
        setattr(eng, n, v)
    wt.WORKTREES, loam_lock.LOCK_DIR, wt.update_pr, wt.open_pr, target.API = saved_other
    for k, v in saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    httpd.shutdown()
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
