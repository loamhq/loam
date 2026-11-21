#!/usr/bin/env python3
"""Exercise adopted repository installations and pull requests using a loopback GitHub fixture and real temporary Git repositories."""

from __future__ import annotations

import base64
import contextlib
import importlib.machinery
import importlib.util
import io
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

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_deploy  # noqa: E402
import loam_engineer as eng  # noqa: E402
import loam_github  # noqa: E402
import loam_github_app as gh  # noqa: E402
import loam_lock  # noqa: E402
import loam_worktree as wt  # noqa: E402


def load_script(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


prov = load_script("provision_adopt", AGENTS / "bin" / "provision")

# Use the development role and its substitution tables together when rendering the
# fixture.
_shipped = prov.ENGINEER_SHIPPED / "role.md"
if not prov.ENGINEER_ROLE_SRC.is_file() or (_shipped.is_file() and prov.ENGINEER_ROLE_SRC.read_text(
        encoding="utf-8") == _shipped.read_text(encoding="utf-8")):
    print(f"SKIP  no shared engineer role a project engineer renders from "
          f"({prov.ENGINEER_ROLE_SRC})")
    sys.exit(0)
engineer_job = load_script("engineer_job_adopt", AGENTS / "bin" / "engineer-job")

results: list[bool] = []


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:70} got={got!r}"[:240])
    if not ok:
        print(f"      wanted {want!r}")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        capture_output=True, text=True, check=True).stdout.strip()


# ---------------------------------------------------------------------------
# A fake GitHub: two installations the owner made, one a stranger made.
# ---------------------------------------------------------------------------

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(serialization.Encoding.PEM,
                        serialization.PrivateFormat.TraditionalOpenSSL,
                        serialization.NoEncryption()).decode()
CLIENT_ID = "Iv23liADOPT"


class Fake:
    calls: list[tuple[str, str, dict]] = []
    accounts = {99: "octo-owner", 555: "acme-health", 777: "stranger"}
    # What GET /user/installations lists for the connected user: his own two.
    mine = {99, 555}
    login = "octo-owner"
    user_token = "ghu_user0"
    pair = 0
    minted = 0
    permissions = {"administration": "write", "contents": "write", "metadata": "read",
                   "workflows": "write", "pull_requests": "write", "issues": "write"}
    repos = {"acme-health/backend": {"default_branch": "develop"},
             "acme-health/dashboard": {"default_branch": "main"},
             "acme-health/frontend": {"default_branch": "main"}}
    pulls: dict[str, dict] = {}


def unb64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def jwt_valid(header: str) -> bool:
    try:
        head, claims, sig = header[7:].split(".")
        KEY.public_key().verify(unb64(sig), f"{head}.{claims}".encode(),
                                padding.PKCS1v15(), hashes.SHA256())
        return json.loads(unb64(claims))["iss"] == CLIENT_ID
    except Exception:  # noqa: BLE001
        return False


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

    def body(self) -> dict:
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.headers.get("Content-Type", "").startswith("application/x-www-form"):
            return dict(urllib.parse.parse_qsl(raw.decode()))
        return json.loads(raw) if raw else {}

    def do_POST(self):  # noqa: N802
        body = self.body()
        Fake.calls.append(("POST", self.path, body))
        auth = self.headers.get("Authorization", "")
        parts = self.path.strip("/").split("/")
        if self.path == "/login/oauth/access_token":
            Fake.pair += 1
            Fake.user_token = f"ghu_user{Fake.pair}"
            return self.answer(200, {"access_token": Fake.user_token, "expires_in": 28800,
                                     "refresh_token": f"ghr_{Fake.pair}",
                                     "refresh_token_expires_in": 15897600})
        if parts[:2] == ["app", "installations"] and parts[3:] == ["access_tokens"]:
            if not jwt_valid(auth):
                return self.answer(401, {"message": "bad JWT"})
            inst = int(parts[2])
            owner = Fake.accounts[inst]
            for name in body.get("repositories") or []:
                if f"{owner}/{name}" not in Fake.repos and owner != "octo-owner":
                    return self.answer(422, {"message": "not accessible"})
            Fake.minted += 1
            return self.answer(201, {"token": f"ghs_{inst}_{Fake.minted}",
                                     "expires_at": time.strftime(
                                         "%Y-%m-%dT%H:%M:%SZ",
                                         time.gmtime(time.time() + 3600))})
        if parts[:1] == ["repos"] and parts[3:] == ["pulls"]:
            repo = f"{parts[1]}/{parts[2]}"
            key = f"{repo}:{body.get('head')}"
            if key in Fake.pulls:
                return self.answer(422, {"message": "Validation Failed",
                                         "errors": [{"message": "A pull request already exists"}]})
            Fake.pulls[key] = {"number": len(Fake.pulls) + 1, **body, "state": "open",
                               "html_url": f"https://github.com/{repo}/pull/{len(Fake.pulls) + 1}"}
            return self.answer(201, Fake.pulls[key])
        return self.answer(404, {"message": "Not Found"})

    def do_GET(self):  # noqa: N802
        Fake.calls.append(("GET", self.path, {}))
        auth = self.headers.get("Authorization", "")
        path, _, query = self.path.partition("?")
        parts = path.strip("/").split("/")
        if parts[:2] == ["app", "installations"] and len(parts) == 3:
            if not jwt_valid(auth):
                return self.answer(401, {"message": "bad JWT"})
            inst = int(parts[2])
            return self.answer(200, {"id": inst, "account": {"login": Fake.accounts[inst]},
                                     "repository_selection": "selected" if inst != 99 else "all",
                                     "permissions": dict(Fake.permissions)})
        if path == "/user":
            return self.answer(200, {"login": Fake.login})
        if path == "/user/installations":
            if auth != f"Bearer {Fake.user_token}":
                return self.answer(401, {"message": "Bad credentials"})
            return self.answer(200, {"total_count": len(Fake.mine),
                                     "installations": [{"id": i} for i in sorted(Fake.mine)]})
        if parts[:1] == ["repos"] and len(parts) == 3:
            repo = f"{parts[1]}/{parts[2]}"
            if repo not in Fake.repos:
                return self.answer(404, {"message": "Not Found"})
            return self.answer(200, {"full_name": repo, "name": parts[2], "private": True,
                                     "visibility": "private",
                                     "html_url": f"https://github.com/{repo}",
                                     "default_branch": Fake.repos[repo]["default_branch"]})
        if parts[:1] == ["repos"] and parts[3:] == ["pulls"]:
            head = dict(urllib.parse.parse_qsl(query)).get("head", "")
            repo = f"{parts[1]}/{parts[2]}"
            branch = head.partition(":")[2]
            pr = Fake.pulls.get(f"{repo}:{branch}")
            return self.answer(200, [pr] if pr else [])
        return self.answer(404, {"message": "Not Found"})


def raised(fn, *args, **kwargs) -> str | None:
    try:
        fn(*args, **kwargs)
        return None
    except (gh.IntegrationError, SystemExit) as exc:
        return str(exc) or "exit"


def mints_to(inst: int) -> int:
    return sum(1 for c in Fake.calls
               if c[1] == f"/app/installations/{inst}/access_tokens")


# ---------------------------------------------------------------------------

root = Path(tempfile.mkdtemp(prefix="loam-adopt-test-"))
httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
fake_url = f"http://127.0.0.1:{httpd.server_address[1]}"
threading.Thread(target=httpd.serve_forever, daemon=True).start()
saved_gh = {n: getattr(gh, n) for n in ("STORE_DIR", "STORE_PATH", "LOCK_PATH", "API", "WEB")}
saved_eng = {n: getattr(eng, n) for n in ("PROJECTS_DIR", "PROJECT_STATE", "PROJECT_CHECKOUTS")}
saved_other = (wt.WORKTREES, loam_lock.LOCK_DIR, prov.CLAUDE_JSON,
               loam_github.https_remote, wt.open_pr, loam_deploy.AGENTS)
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
    prov.CLAUDE_JSON = root / "claude.json"
    prov.CLAUDE_JSON.write_text("{}")

    # The box as it is today: one app, one installation, the owner authorized.
    gh.save({"app": {"id": 1, "slug": "loam", "client_id": CLIENT_ID,
                     "client_secret": "secret", "pem": PEM},
             "installation": {"installation_id": 99, "account": "octo-owner",
                              "repository_selection": "all"},
             "user": {"login": "octo-owner", "token": Fake.user_token,
                      "token_expires_at": "2099-01-01T00:00:00Z",
                      "refresh_token": "ghr_0"},
             "health": {}})

    # == installations ======================================================
    state = gh.start_install()["state"]
    view = gh.callback("oauth-code", 555, "install", state)
    check("test_second_installation_recorded: Acme's org beside octo-owner",
          [(i["installation_id"], i["account"]) for i in view["installations"]],
          [(99, "octo-owner"), (555, "acme-health")])
    check("...the first installation is untouched, and still the page's `installed`",
          (gh.load()["installation"]["installation_id"], view["installed"]["installation_id"]),
          (99, 99))
    check("...checked against GitHub's list of the user's own installations",
          any(c[1].startswith("/user/installations") for c in Fake.calls), True)

    said = raised(gh.callback, None, 777, "install", None)
    check("test_installation_without_our_state_refused: install redirect, no state",
          "not one this box issued" in (said or ""), True)
    said = raised(gh.callback, None, 777, "update", None)
    check("...nor GitHub's stateless update redirect, for an installation nobody added",
          "not one this box issued" in (said or ""), True)
    said = raised(gh.callback, "oauth-code", 777, "install", gh.start_install()["state"])
    check("test_installation_not_the_users_refused: our state, but not his installation",
          "not one octo-owner can reach" in (said or ""), True)
    check("...and none of the three was recorded",
          [i["installation_id"] for i in gh.installations(gh.load())], [99, 555])
    Fake.login = "someone-else"
    said = raised(gh.callback, "oauth-code", 555, "install", gh.start_install()["state"])
    Fake.login = "octo-owner"
    check("test_install_by_another_user_refused: the user token is never replaced",
          ("authorized by someone-else" in (said or ""), gh.load()["user"]["login"]),
          (True, "octo-owner"))
    check("...GitHub's own update redirect for a recorded installation is accepted",
          raised(gh.callback, None, 555, "update", None), None)

    before = {i: mints_to(i) for i in (99, 555, 777)}
    tok = gh.installation_token("acme-health/dashboard")
    check("test_token_from_the_owners_installation: acme-health/* from 555",
          (tok["token"].startswith("ghs_555_"), mints_to(555) - before[555],
           mints_to(99) - before[99]), (True, 1, 0))
    tok = gh.installation_token("octo-owner/loam-ui")
    check("...octo-owner/* still from 99", tok["token"].startswith("ghs_99_"), True)
    said = raised(gh.installation_token, "stranger/anything")
    check("test_unrecorded_installation_never_mints: stranger/* refused, 777 never asked",
          ("no installation Loam recorded covers stranger" in (said or ""),
           mints_to(777) - before[777]), (True, 0))

    # == adoption ===========================================================
    def remote_for(name: str, branch: str, extra: dict | None = None) -> Path:
        bare = root / "remotes" / f"{name}.git"
        bare.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "--bare", "-b", branch, str(bare)], check=True)
        seed = root / "seeds" / name
        subprocess.run(["git", "init", "-q", "-b", branch, str(seed)], check=True)
        (seed / "app.py").write_text("print('acme')\n")
        for rel, text in (extra or {}).items():
            (seed / rel).parent.mkdir(parents=True, exist_ok=True)
            (seed / rel).write_text(text)
        git(seed, "add", "-A")
        git(seed, "commit", "-qm", "theirs")
        git(seed, "push", "-q", str(bare), branch)
        return bare

    remotes = {"acme-health/backend": remote_for("backend", "develop"),
               "acme-health/dashboard": remote_for("dashboard", "main",
                                                   {"CLAUDE.md": "their own\n"})}
    loam_github.https_remote = lambda repo: str(remotes[repo])
    theirs_before = git(remotes["acme-health/backend"], "for-each-ref")

    prov.adopt_project("acme-backend", "acme-health/backend", None, None)
    checkout = eng.PROJECT_CHECKOUTS / "acme-backend"
    record = json.loads(eng.project_record_path("acme-backend").read_text())
    check("test_adopt_registers_a_pull_request_project",
          (record["landing"], record["default_branch"], record["repo"], "deploy_key" in record),
          ("pull_request", "develop", "acme-health/backend", False))
    check("...the remote's default branch is the checkout's `main`",
          (git(checkout, "branch", "--show-current"),
           git(checkout, "rev-parse", "--abbrev-ref", "main@{upstream}")),
          ("main", "origin/develop"))
    check("test_adopt_harness_never_in_history: rendered on disk, status clean",
          ((checkout / "CLAUDE.md").is_file(), (checkout / ".claude" / "settings.json").is_file(),
           git(checkout, "status", "--porcelain")), (True, True, ""))
    check("...excluded in .git/info/exclude",
          all(f"/{p}" in (checkout / ".git" / "info" / "exclude").read_text()
              for p in prov.HARNESS_PATHS), True)
    check("...and nothing was pushed to their repository",
          git(remotes["acme-health/backend"], "for-each-ref"), theirs_before)
    check("...cloned with a read-only token",
          [c[2].get("permissions") for c in Fake.calls
           if c[1] == "/app/installations/555/access_tokens"][-1],
          {"contents": "read", "metadata": "read"})

    # Registration in a job publishes to local state, outside the job's git tree.
    remotes["acme-health/frontend"] = remote_for("frontend", "main")
    job_tree = root / "job-tree"
    saved_tree = os.environ.get("LOAM_JOB_TREE")
    os.environ["LOAM_JOB_TREE"] = str(job_tree)
    try:
        prov.adopt_project("acme-frontend", "acme-health/frontend", None, None)
    finally:
        if saved_tree is None:
            os.environ.pop("LOAM_JOB_TREE")
        else:
            os.environ["LOAM_JOB_TREE"] = saved_tree
    record_path = eng.project_record_path("acme-frontend")
    check("test_adopt_in_a_job_keeps_the_record_out_of_git",
          ((job_tree / "projects").exists(), json.loads(record_path.read_text())["landing"],
           (eng.PROJECT_STATE / "acme-frontend" / "jobs").is_dir()),
          (False, "pull_request", True))
    shutil.rmtree(eng.PROJECT_CHECKOUTS / "acme-frontend")

    try:
        prov.adopt_project("acme-dashboard", "acme-health/dashboard", None, None)
        said = None
    except SystemExit as exc:
        said = str(exc)
    check("test_adopt_refuses_a_repo_that_tracks_the_harness: nothing kept",
          ((eng.PROJECT_CHECKOUTS / "acme-dashboard").exists(),
           eng.project_record_path("acme-dashboard").exists()), (False, False))
    try:
        prov.adopt_project("acme-x", "stranger/thing", None, None)
        said = None
    except SystemExit:
        said = "refused"
    check("...and a repository on an account with no recorded installation", said, "refused")

    project = eng.projects()["acme-backend"]
    check("test_pull_request_project_has_no_deploy_lane",
          (project.pull_request, eng.platform_project().pull_request), (True, False))
    loam_deploy.AGENTS = root
    check("...bin/deploy's record loader names it as having none",
          loam_deploy._lands_by_pull_request("acme-backend"), True)
    loam_deploy.AGENTS = saved_other[5]

    vault = root / "vault"
    (vault / "40_PROJECTS" / "Acme").mkdir(parents=True)
    check("test_receipts_folder_defaults_to_the_project_name",
          project.receipts_dir(vault), vault / "40_PROJECTS" / "acme-backend" / "receipts")
    check("test_vault_folder_routes_receipts: `Acme/`, not a `acme/` sibling",
          (prov.vault_folder_for("acme", "Acme", vault),
           eng.Project("acme", root, root, platform=False,
                       record={"vault_folder": "Acme"}).receipts_dir(vault)),
          ("Acme", vault / "40_PROJECTS" / "Acme" / "receipts"))
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        said = raised(prov.vault_folder_for, "acme", None, vault)
    said = said and err.getvalue()
    check("...a case-only twin of an existing folder is refused, naming the flag",
          "pass --vault-folder Acme" in (said or ""), True)
    check("...and a folder spelled as the name records nothing",
          prov.vault_folder_for("acme-backend", None, vault), None)

    # == landing ============================================================
    before = len(Fake.calls)
    gh.installation_token("acme-health/backend")
    asked = [c[2] for c in Fake.calls[before:] if c[1].endswith("/access_tokens")][-1]
    check("test_job_token_is_read_only: contents read, no workflows, no administration",
          asked["permissions"], {"contents": "read", "issues": "write",
                                 "metadata": "read", "pull_requests": "write"})
    tok = gh.installation_token("acme-health/dashboard")
    asked = [c[2] for c in Fake.calls if c[1].endswith("/access_tokens")][-1]
    check("...a repository with no pull-request project keeps contents write",
          asked["permissions"]["contents"], "write")

    # The broker is the landing's other half; here it is called directly.
    wt.open_pr = lambda p, branch, title, body: gh.open_pull_request(
        p.name, branch, title, body)

    def start(job_id: str) -> dict:
        job_dir = project.jobs / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        return wt.setup(project, job_id, job_dir)

    def commit(rec: dict, rel: str, text: str) -> str:
        tree = Path(rec["path"])
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        (tree / rel).write_text(text)
        git(tree, "add", "-f", rel)
        git(tree, "commit", "-qm", f"edit {rel}")
        return git(tree, "rev-parse", "HEAD")

    bare = remotes["acme-health/backend"]
    their_develop = git(bare, "rev-parse", "develop")
    job = "20261006-120000-aaaaaaaa"
    rec = start(job)
    check("test_job_starts_from_their_default_branch",
          (git(Path(rec["path"]), "rev-parse", "HEAD"), rec.get("synced")),
          (their_develop, "at origin/develop"))
    tip = commit(rec, "app.py", "print('fixed')\n")
    result = wt.land(project, rec, "Land", title="Fix the reminder bug",
                     summary="Found the bug; fixed it.")
    section = wt.receipt_section(project, job, result)
    check("test_landing_opens_a_pull_request_never_their_main",
          (result["state"], git(bare, "rev-parse", "develop"),
           git(bare, "rev-parse", f"job/{job}")), ("pull_request", their_develop, tip))
    pr = Fake.pulls[f"acme-health/backend:job/{job}"]
    check("...against their default branch, titled from the brief",
          (pr["base"], pr["head"], pr["title"]), ("develop", f"job/{job}", "Fix the reminder bug"))
    check("test_untested_pull_request_says_so: no test_command",
          (pr["body"].startswith("**Untested.**"), "Found the bug" in pr["body"],
           "**untested**" in section), (True, True, True))
    check("...pushed by the broker with the installation's full token, not the job's",
          [c[2]["permissions"]["contents"] for c in Fake.calls
           if c[1] == "/app/installations/555/access_tokens"][-1], "write")
    check("...the canonical main did not move, the branch stays for a continuation",
          (git(project.root, "rev-parse", "main"),
           bool(git(project.root, "rev-parse", "--verify", f"refs/heads/job/{job}"))),
          (their_develop, True))

    again = gh.open_pull_request("acme-backend", f"job/{job}", "Fix", "")
    check("...a second landing of the same branch finds the open pull request",
          (again["created"], again["number"]), (False, pr["number"]))
    # A rebuilt contribution gets a fresh branch after the previous pull request closed.
    pr["state"] = "closed"
    rebuilt = git(project.root, "commit-tree", "-p", their_develop, "-m", "Rebuilt",
                  f"{their_develop}^{{tree}}")
    git(project.root, "update-ref", f"refs/heads/job/{job}", rebuilt)
    fresh = gh.open_pull_request("acme-backend", f"job/{job}", "Fix", "")
    check("test_plain_rebuilt_continuation_with_closed_pr_gets_a_fresh_branch",
          (fresh["head"], fresh["created"], fresh["number"] != pr["number"],
           git(bare, "rev-parse", f"job/{job}"), git(bare, "rev-parse", f"job/{job}-2"),
           (fresh.get("replaces") or {}).get("why")),
          (f"job/{job}-2", True, True, tip, rebuilt, "rebuilt"))
    again = gh.open_pull_request("acme-backend", f"job/{job}", "Fix", "")
    check("...and the next landing finds that one",
          (again["head"], again["created"], again["number"]),
          (f"job/{job}-2", False, fresh["number"]))

    said = raised(gh.open_pull_request, "acme-backend", "main", "t", "")
    check("test_push_to_main_refused: the broker pushes a job's branch and nothing else",
          ("only a job's branch" in (said or ""), git(bare, "rev-parse", "develop")),
          (True, their_develop))
    said = raised(gh.open_pull_request, "acme-backend", "develop", "t", "")
    check("...their default branch by its own name too", "only a job's branch" in (said or ""),
          True)

    # Inspect the job branch even when the working tree has a detached HEAD.
    jobd = "20261006-120050-dddddddd"
    recd = start(jobd)
    elsewhere = commit(recd, "app.py", "print('their pull request')\n")
    git(Path(recd["path"]), "checkout", "-q", "--detach", elsewhere)
    git(Path(recd["path"]), "branch", "-f", f"job/{jobd}", their_develop)
    pulls_before = dict(Fake.pulls)
    result = wt.land(project, recd, "Land", title="Read back the pull request",
                     summary="Read it.")
    check("test_detached_head_opens_no_pull_request: nothing to land",
          (result["state"], Fake.pulls == pulls_before,
           subprocess.run(["git", "-C", str(bare), "rev-parse", "--verify", "--quiet",
                           f"job/{jobd}"], capture_output=True).returncode != 0,
           Path(recd["path"]).exists()),
          ("nothing", True, True, False))

    job2 = "20261006-120100-bbbbbbbb"
    rec2 = start(job2)
    commit(rec2, "CLAUDE.md", "smuggled\n")
    result = wt.land(project, rec2, "Land", title="t")
    check("test_harness_commit_refused: a branch that commits the harness is not pushed",
          (result["state"], result["reason"], result["files"],
           subprocess.run(["git", "-C", str(bare), "rev-parse", "--verify", "--quiet",
                           f"job/{job2}"], capture_output=True).returncode != 0),
          ("not_landed", "harness", ["CLAUDE.md"], True))
    drift = load_script("check_drift_adopt", AGENTS / "bin" / "check-drift")
    check("...and so the canonical repository never took it in",
          drift.adopted_harness(project.root), [])
    git(project.root, "fetch", "-q", rec2["path"], f"+refs/heads/job/{job2}:refs/heads/job/{job2}")
    found = drift.adopted_harness(project.root)
    check("test_check_drift_flags_harness_in_adopted_history",
          [f.split("`")[1] for f in found], [f"job/{job2}"])

    note = engineer_job.tree_note(project, rec)
    check("test_job_prompt_says_pull_request: no merge, no push, read-only credential",
          ("opens a pull request against `develop`" in note,
           "can fetch and cannot push" in note, "merges" in note), (True, True, False))

    project.record["test_command"] = "false"
    job3 = "20261006-120200-cccccccc"
    rec3 = start(job3)
    commit(rec3, "app.py", "broken\n")
    result = wt.land(project, rec3, "Land", title="t")
    check("test_failing_tests_open_no_pull_request",
          (result["state"], result["reason"], f"acme-health/backend:job/{job3}" in Fake.pulls),
          ("not_landed", "branch_tests", False))
finally:
    for n, v in saved_gh.items():
        setattr(gh, n, v)
    for n, v in saved_eng.items():
        setattr(eng, n, v)
    (wt.WORKTREES, loam_lock.LOCK_DIR, prov.CLAUDE_JSON, loam_github.https_remote,
     wt.open_pr, loam_deploy.AGENTS) = saved_other
    httpd.shutdown()
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
