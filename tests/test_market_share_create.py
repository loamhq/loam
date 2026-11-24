#!/usr/bin/env python3
"""Exercise `loam market share` repository creation, token refresh, and refusals
against a local fake GitHub API + bare git remotes.

Run: python3 tests/run-all test_market_share_create.py
"""

from __future__ import annotations

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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import loam_github  # noqa: E402
import loam_github_app as gh  # noqa: E402
import loam_market_asset  # noqa: E402
import loam_marketplace as mk  # noqa: E402
import loam_plugins  # noqa: E402

results: list[bool] = []


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:72} got={got!r}"[:220])
    if not ok:
        print(f"      wanted {want!r}")


KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(serialization.Encoding.PEM,
                        serialization.PrivateFormat.TraditionalOpenSSL,
                        serialization.NoEncryption()).decode()
CLIENT_ID = "Iv23liSHARETEST"


def iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


class FakeGitHub:
    hub: Path
    calls: list[tuple[str, str, dict]] = []
    user_login = "octo"
    user_token = "ghu_initial"
    refresh_token = "ghr_initial"
    pair = 0
    selection = "all"
    permissions: dict[str, str] = {
        "administration": "write",
        "contents": "write",
        "metadata": "read",
        "workflows": "write",
    }
    repos: dict[str, dict] = {}
    reaches: set[str] = set()
    hidden_existing: set[str] = set()
    force_create_403 = False
    next_id = 9000

    @classmethod
    def reset(cls, hub: Path) -> None:
        cls.hub = hub
        cls.calls = []
        cls.user_login = "octo"
        cls.user_token = "ghu_initial"
        cls.refresh_token = "ghr_initial"
        cls.pair = 0
        cls.selection = "all"
        cls.permissions = {
            "administration": "write",
            "contents": "write",
            "metadata": "read",
            "workflows": "write",
        }
        cls.repos = {}
        cls.reaches = set()
        cls.hidden_existing = set()
        cls.force_create_403 = False
        cls.next_id = 9000

    @classmethod
    def init_bare(cls, owner: str, name: str) -> Path:
        bare = cls.hub / owner / f"{name}.git"
        bare.parent.mkdir(parents=True, exist_ok=True)
        if not bare.exists():
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
        return bare

    @classmethod
    def add_existing(cls, owner: str, name: str, *, private: bool = True,
                     in_installation: bool = True) -> dict:
        cls.next_id += 1
        full = f"{owner}/{name}"
        rec = {
            "id": cls.next_id,
            "name": name,
            "full_name": full,
            "owner": {"login": owner},
            "private": private,
            "visibility": "private" if private else "public",
            "default_branch": "main",
        }
        cls.repos[full.lower()] = rec
        if in_installation:
            cls.reaches.add(name)
        cls.init_bare(owner, name)
        return rec

    @classmethod
    def issue(cls) -> dict:
        cls.pair += 1
        cls.user_token = f"ghu_refreshed_{cls.pair}"
        cls.refresh_token = f"ghr_refreshed_{cls.pair}"
        return {
            "access_token": cls.user_token,
            "expires_in": 28800,
            "refresh_token": cls.refresh_token,
            "refresh_token_expires_in": 15897600,
            "token_type": "bearer",
            "scope": "",
        }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def answer(self, status: int, body: dict | None = None) -> None:
        raw = b"" if body is None else json.dumps(body).encode()
        self.send_response(status)
        if body is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        if raw:
            self.wfile.write(raw)

    def read_body(self) -> dict:
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.headers.get("Content-Type", "").startswith("application/x-www-form"):
            return dict(urllib.parse.parse_qsl(raw.decode()))
        return json.loads(raw) if raw else {}

    def _check_user_auth(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {FakeGitHub.user_token}"

    def do_GET(self):  # noqa: N802
        FakeGitHub.calls.append(("GET", self.path, {}))
        if self.path == "/user":
            if not self._check_user_auth():
                return self.answer(401, {"message": "Bad credentials"})
            return self.answer(200, {"login": FakeGitHub.user_login})
        if self.path.startswith("/app/installations/"):
            inst_id = int(self.path.split("/")[3])
            acct = "acme-org" if inst_id == 200 else FakeGitHub.user_login
            perms = ({"contents": "write", "metadata": "read", "pull_requests": "write"}
                     if inst_id == 200 else dict(FakeGitHub.permissions))
            return self.answer(200, {
                "id": inst_id,
                "account": {"login": acct},
                "repository_selection": FakeGitHub.selection,
                "permissions": perms,
            })
        if self.path.startswith("/repos/"):
            parts = self.path.strip("/").split("/")
            if len(parts) == 3:
                full = f"{parts[1]}/{parts[2]}".lower()
                if self.headers.get("Authorization"):
                    if not self._check_user_auth():
                        return self.answer(401, {"message": "Bad credentials"})
                    repo = FakeGitHub.repos.get(full)
                    if not repo:
                        return self.answer(404, {"message": "Not Found"})
                    return self.answer(200, repo)
                # Unauthenticated public check (is_public)
                repo = FakeGitHub.repos.get(full)
                if repo and not repo["private"]:
                    return self.answer(200, repo)
                return self.answer(404, {"message": "Not Found"})
        return self.answer(404, {"message": "Not Found"})

    def do_POST(self):  # noqa: N802
        body = self.read_body()
        FakeGitHub.calls.append(("POST", self.path, body))
        if self.path == "/login/oauth/access_token":
            if body.get("grant_type") == "refresh_token":
                if body.get("refresh_token") != FakeGitHub.refresh_token:
                    return self.answer(200, {"error": "bad_refresh_token"})
                return self.answer(200, FakeGitHub.issue())
            return self.answer(200, {"error": "unsupported_grant_type"})
        if self.path.startswith("/app/installations/") and self.path.endswith("/access_tokens"):
            wanted = set(body.get("repositories") or [])
            if wanted and not (wanted <= FakeGitHub.reaches):
                return self.answer(422, {"message": "Repository is not accessible to the installation."})
            return self.answer(201, {
                "token": "ghs_install_tok",
                "expires_at": iso(time.time() + 3600),
            })
        if self.path == "/user/repos":
            if not self._check_user_auth():
                return self.answer(401, {"message": "Bad credentials"})
            if FakeGitHub.force_create_403:
                return self.answer(403, {"message": "Resource not accessible by integration"})
            name = body["name"]
            full = f"{FakeGitHub.user_login}/{name}".lower()
            if full in FakeGitHub.repos or full in FakeGitHub.hidden_existing:
                return self.answer(422, {
                    "message": "Repository creation failed.",
                    "errors": [{"resource": "Repository", "code": "custom",
                                "field": "name", "message": "name already exists on this account"}],
                })
            private = bool(body.get("private", True))
            rec = FakeGitHub.add_existing(
                FakeGitHub.user_login, name, private=private,
                in_installation=(FakeGitHub.selection == "all"))
            rec["description"] = body.get("description", "")
            return self.answer(201, rec)
        return self.answer(404, {"message": "Not Found"})

    def do_PUT(self):  # noqa: N802
        FakeGitHub.calls.append(("PUT", self.path, {}))
        parts = self.path.strip("/").split("/")
        if parts[:2] == ["user", "installations"] and parts[3:4] == ["repositories"]:
            if not self._check_user_auth():
                return self.answer(401, {"message": "Bad credentials"})
            repo_id = int(parts[4])
            found = next((r for r in FakeGitHub.repos.values() if r["id"] == repo_id), None)
            if not found:
                return self.answer(404, {"message": "Not Found"})
            FakeGitHub.reaches.add(found["name"])
            return self.answer(204, None)
        return self.answer(404, {"message": "Not Found"})


def write_store(*, authorized: bool = True, installed: bool = True,
                expired_hours: float | None = None, last_error: str | None = None,
                selection: str = "all", org_app: str | None = None) -> None:
    data: dict = {}
    if installed or authorized or last_error:
        data["app"] = {
            "id": 4242, "slug": "loam", "html_url": "https://github.com/apps/loam",
            "owner": FakeGitHub.user_login, "client_id": CLIENT_ID,
            "client_secret": "secret", "pem": PEM,
            "registered_at": iso(time.time() - 86400),
        }
        data["health"] = {"last_ok_at": iso(time.time() - 3600), "last_error": last_error}
    if installed:
        data["installation"] = {
            "installation_id": 99,
            "account": FakeGitHub.user_login,
            "repository_selection": selection,
            "installed_at": iso(time.time() - 86400),
        }
    if authorized:
        exp = time.time() - expired_hours * 3600 if expired_hours is not None else time.time() + 25000
        data["user"] = {
            "login": FakeGitHub.user_login,
            "token": FakeGitHub.user_token,
            "token_expires_at": iso(exp),
            "refresh_token": FakeGitHub.refresh_token,
            "refresh_expires_at": iso(time.time() + 90 * 86400),
            "authorized_at": iso(time.time() - 86400),
        }
    if org_app:
        data["org_apps"] = {
            org_app: {
                "org": org_app,
                "app": {
                    "id": 5151, "slug": f"loam-{org_app}",
                    "html_url": f"https://github.com/apps/loam-{org_app}",
                    "owner": org_app, "client_id": CLIENT_ID,
                    "client_secret": "secret", "pem": PEM,
                    "registered_at": iso(time.time() - 86400),
                },
                "installation": {
                    "installation_id": 200, "account": org_app,
                    "repository_selection": "all", "installed_at": iso(time.time() - 86400),
                },
                "health": {"last_ok_at": iso(time.time() - 3600), "last_error": None},
            }
        }
    if data:
        gh.save(data)
    else:
        gh.STORE_PATH.unlink(missing_ok=True)


def run_share_err(pid: str, repo: str, *, public: bool = False) -> str | None:
    try:
        mk.share(pid, repo, public=public)
        return None
    except mk.ShareError as exc:
        return str(exc)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="loam-share-create-"))
    hub = tmp / "hub"
    hub.mkdir()
    FakeGitHub.reset(hub)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    fake_url = f"http://127.0.0.1:{port}"
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    saved = (
        gh.STORE_DIR, gh.STORE_PATH, gh.LOCK_PATH, gh.API, gh.WEB,
        gh.PROVE_WAIT_S, loam_github.API, loam_github.helper_env,
        mk._share_base, loam_market_asset.publish, loam_plugins.yours_root,
    )
    published: list[tuple[str, str, str]] = []
    try:
        os.environ.update({
            "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@example.test",
            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@example.test",
            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
        })
        os.environ.pop("LOAM_SHARE_GIT_BASE", None)

        gh.STORE_DIR = tmp / "github"
        gh.STORE_PATH = gh.STORE_DIR / "integration.json"
        gh.LOCK_PATH = gh.STORE_DIR / "integration.lock"
        gh.API = gh.WEB = fake_url
        gh.PROVE_WAIT_S = 0.01
        loam_github.API = fake_url
        loam_github.helper_env = lambda repo: {"LOAM_TEST_HELPER": repo}
        mk._share_base = lambda: f"file://{hub}/"
        loam_market_asset.publish = lambda repo, tag, pid, *a: (
            published.append((repo, tag, pid)) or f"https://github.com/{repo}/releases/download/{tag}/loam-{pid}.tar"
        )
        plugins_dir = tmp / "plugins"
        loam_plugins.yours_root = lambda: plugins_dir

        pet = plugins_dir / "pocket-pets"
        pet.mkdir(parents=True)
        (pet / "PLUGIN.md").write_text(
            "---\nid: pocket-pets\nname: Pocket Pets\nversion: 1.0.0\napi: 0\n"
            "kind: [extension]\nauthor: Sam\n"
            "description: Tiny companions for your Loam workspace.\n---\nPocket Pets.\n",
            encoding="utf-8",
        )
        (pet / "entry.js").write_text("export default {};\n", encoding="utf-8")

        # 1. Create-when-missing: private by default (all repositories)
        write_store(authorized=True, selection="all")
        out = mk.share("pocket-pets", "octo/loam-pocket-pets")
        create_calls = [b for m, p, b in FakeGitHub.calls if m == "POST" and p == "/user/repos"]
        check("create-when-missing calls POST /user/repos with private=True",
              (len(create_calls), create_calls[0]["name"], create_calls[0]["private"]),
              (1, "loam-pocket-pets", True))
        check("share returns created=True, public=False and install link",
              (out["created"], out["public"], out["link"]),
              (True, False, "https://github.com/octo/loam-pocket-pets/tree/v1.0.0/pocket-pets"))
        listed = subprocess.run(
            ["git", "-C", str(hub / "octo" / "loam-pocket-pets.git"),
             "ls-tree", "-r", "--name-only", "v1.0.0"],
            capture_output=True, text=True, check=True).stdout.split()
        check("new repo holds the tagged plugin folder",
              listed, ["pocket-pets/PLUGIN.md", "pocket-pets/entry.js"])
        check("release asset published for newly created repo",
              published[-1], ("octo/loam-pocket-pets", "v1.0.0", "pocket-pets"))

        # 2. Create-when-missing on selected repositories: adds repo to installation via PUT
        FakeGitHub.calls.clear()
        FakeGitHub.selection = "selected"
        write_store(authorized=True, selection="selected")
        out_sel = mk.share("pocket-pets", "octo/loam-pocket-pets-sel")
        put_calls = [p for m, p, _ in FakeGitHub.calls if m == "PUT" and "/repositories/" in p]
        check("selected-repos install adds newly created repo to installation before cloning",
              (out_sel["created"], out_sel["public"], len(put_calls)),
              (True, False, 1))
        FakeGitHub.selection = "all"

        # 3. Create-when-missing with public=True (explicit owner yes)
        FakeGitHub.calls.clear()
        write_store(authorized=True, selection="all")
        out_pub = mk.share("pocket-pets", "octo/loam-pocket-pets-pub", public=True)
        create_calls = [b for m, p, b in FakeGitHub.calls if m == "POST" and p == "/user/repos"]
        check("create-when-missing with public=True sends private=False",
              (len(create_calls), create_calls[0]["private"], out_pub["created"], out_pub["public"]),
              (1, False, True, True))

        # 4. Existing private repo: does NOT recreate or flip visibility even with public=True
        FakeGitHub.calls.clear()
        out_existing = mk.share("pocket-pets", "octo/loam-pocket-pets", public=True)
        create_calls = [b for m, p, b in FakeGitHub.calls if m == "POST" and p == "/user/repos"]
        check("existing private repo is reused without POST /user/repos or flipping public",
              (len(create_calls), out_existing["created"], out_existing["public"]),
              (0, False, False))

        # 5. Expired 8-hour user token (e.g. expired 12 hours ago) is refreshed BEFORE API calls
        FakeGitHub.calls.clear()
        old_token = FakeGitHub.user_token
        write_store(authorized=True, expired_hours=12.0)
        out_exp = mk.share("pocket-pets", "octo/loam-pocket-pets-late")
        oauth_calls = [b for m, p, b in FakeGitHub.calls if m == "POST" and p == "/login/oauth/access_token"]
        stored_after = gh.load()["user"]["token"]
        check("expired 8-hour user token is refreshed before create and saved to store",
              (len(oauth_calls), oauth_calls[0].get("grant_type"),
               stored_after != old_token, stored_after == FakeGitHub.user_token,
               out_exp["created"]),
              (1, "refresh_token", True, True, True))

        # 6. Stale token with non-expired timestamp gets 401 -> force-refreshes once and succeeds
        FakeGitHub.calls.clear()
        write_store(authorized=True, expired_hours=None)
        # Rotate token on fake server so the stored token triggers HTTP 401
        FakeGitHub.user_token = "ghu_rotated_on_github"
        out_401 = mk.share("pocket-pets", "octo/loam-pocket-pets-401")
        oauth_calls = [b for m, p, b in FakeGitHub.calls if m == "POST" and p == "/login/oauth/access_token"]
        check("401 on stale token triggers force-refresh retry and succeeds",
              (len(oauth_calls), out_401["created"]),
              (1, True))

        # 7. Refusal: old install whose GitHub App lacks administration: write
        FakeGitHub.calls.clear()
        FakeGitHub.permissions = {"contents": "write", "metadata": "read", "workflows": "write"}
        write_store(authorized=True)
        err_perm = run_share_err("pocket-pets", "octo/loam-no-admin")
        check("old install lacking administration: write refuses in plain words pointing to Settings",
              (err_perm is not None,
               "permission to create repositories" in (err_perm or ""),
               "Settings › Connections › GitHub" in (err_perm or ""),
               "github.com" not in (err_perm or "").lower()),
              (True, True, True, True))
        FakeGitHub.permissions = {
            "administration": "write", "contents": "write",
            "metadata": "read", "workflows": "write",
        }

        # 8. Refusal: GitHub answers 403 on POST /user/repos
        FakeGitHub.force_create_403 = True
        write_store(authorized=True)
        err_403 = run_share_err("pocket-pets", "octo/loam-403")
        check("403 on POST /user/repos refuses pointing to Settings, not github.com",
              (err_403 is not None,
               "Settings › Connections › GitHub" in (err_403 or ""),
               "github.com" not in (err_403 or "").lower()),
              (True, True, True))
        FakeGitHub.force_create_403 = False

        # 9. Refusal: GitHub not connected (unregistered)
        write_store(authorized=False, installed=False)
        err_unreg = run_share_err("pocket-pets", "octo/loam-unreg")
        check("not connected refuses pointing to Settings › Connections › GitHub",
              (err_unreg is not None,
               "not connected" in (err_unreg or ""),
               "Settings › Connections › GitHub" in (err_unreg or ""),
               "github.com" not in (err_unreg or "").lower()),
              (True, True, True, True))

        # 10. Refusal: installed without user sign-in (missing repo refused; existing repo works)
        write_store(authorized=False, installed=True)
        err_nouser = run_share_err("pocket-pets", "octo/loam-nouser-missing")
        check("installed without user sign-in refuses missing repo pointing to Settings",
              (err_nouser is not None,
               "Finish connecting" in (err_nouser or ""),
               "Settings › Connections › GitHub" in (err_nouser or "")),
              (True, True, True))
        out_nouser_ok = mk.share("pocket-pets", "octo/loam-pocket-pets")
        check("installed without user sign-in still shares to existing reachable repo",
              out_nouser_ok["created"], False)

        # 11. Refusal: unhealthy connection
        write_store(authorized=True, last_error="token refresh failed")
        err_unhealthy = run_share_err("pocket-pets", "octo/loam-unhealthy")
        check("unhealthy connection refuses pointing to Settings › Connections › GitHub",
              (err_unhealthy is not None,
               "needs attention" in (err_unhealthy or ""),
               "Settings › Connections › GitHub" in (err_unhealthy or "")),
              (True, True, True))

        # 12. Refusal: wrong owner account (not connected)
        write_store(authorized=True)
        err_wrong = run_share_err("pocket-pets", "someone-else/loam-pet")
        check("wrong owner names connected account and points to Settings",
              (err_wrong is not None,
               "octo" in (err_wrong or ""),
               "Settings › Connections › GitHub" in (err_wrong or "")),
              (True, True, True))

        # 13. Refusal: private repo exists on GitHub but outside selected repositories (422)
        FakeGitHub.hidden_existing.add("octo/loam-hidden")
        write_store(authorized=True, selection="selected")
        err_hidden = run_share_err("pocket-pets", "octo/loam-hidden")
        check("existing hidden private repo (422) points to Settings Connection details",
              (err_hidden is not None,
               "already exists" in (err_hidden or ""),
               "Connection details" in (err_hidden or ""),
               "Settings › Connections › GitHub" in (err_hidden or "")),
              (True, True, True, True))

        # 14. Refusal: org app cannot create missing repo, but can share to existing repo
        write_store(authorized=True, org_app="acme-org")
        err_org = run_share_err("pocket-pets", "acme-org/loam-org-missing")
        check("org app refuses missing repo pointing to Settings",
              (err_org is not None,
               "cannot create repositories" in (err_org or ""),
               "Settings › Connections › GitHub" in (err_org or "")),
              (True, True, True))
        FakeGitHub.add_existing("acme-org", "loam-org-existing", private=True, in_installation=True)
        out_org_ok = mk.share("pocket-pets", "acme-org/loam-org-existing")
        check("org app still shares to an existing reachable repository",
              out_org_ok["created"], False)

        # 15. CLI end-to-end checks via bin/loam-market
        import contextlib
        import importlib.machinery
        import importlib.util
        import io
        loader = importlib.machinery.SourceFileLoader("loam_market_cli", str(ROOT / "bin" / "loam-market"))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        cli_mod = importlib.util.module_from_spec(spec)
        loader.exec_module(cli_mod)

        def run_cli(argv: list[str]) -> tuple[int, str, str]:
            out_buf, err_buf = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
                rc = cli_mod.main(argv)
            return rc, out_buf.getvalue(), err_buf.getvalue()

        write_store(authorized=True, selection="all")
        rc, out_s, err_s = run_cli(["share", "pocket-pets", "octo/loam-cli-priv"])
        check("CLI share creates private repo by default and prints visibility notice",
              (rc, "Created private repository octo/loam-cli-priv." in out_s,
               "repository is private" in out_s, err_s),
              (0, True, True, ""))

        rc, out_s, err_s = run_cli(["share", "pocket-pets", "octo/loam-cli-pub", "--public"])
        check("CLI share --public creates public repo with no private notice",
              (rc, "Created public repository octo/loam-cli-pub." in out_s,
               "repository is private" not in out_s, err_s),
              (0, True, True, ""))

        rc, out_s, err_s = run_cli(["share", "pocket-pets", "octo/loam-cli-priv", "--public"])
        check("CLI share --public on existing private repo explains visibility was not changed",
              (rc, "already existed as private" in out_s, err_s),
              (0, True, ""))

        rc, out_s, err_s = run_cli(["share", "pocket-pets", "octo/loam-cli-json", "--public", "--json"])
        parsed = json.loads(out_s)
        check("CLI share --public --json reports created=True and public=True",
              (rc, parsed["created"], parsed["public"], err_s),
              (0, True, True, ""))

        FakeGitHub.permissions = {"contents": "write", "metadata": "read", "workflows": "write"}
        rc, out_s, err_s = run_cli(["share", "pocket-pets", "octo/loam-cli-refused"])
        check("CLI share refusal exits 2 with Settings guidance on stderr",
              (rc, "Settings › Connections › GitHub" in err_s, "github.com" not in err_s.lower()),
              (2, True, True))
        FakeGitHub.permissions = {
            "administration": "write", "contents": "write",
            "metadata": "read", "workflows": "write",
        }

        cli_help = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "loam-market"), "share", "--help"],
            capture_output=True, text=True, check=True).stdout
        check("bin/loam-market share --help includes --public",
              "--public" in cli_help, True)

        # 16. Shared skill check: owner manual github.com repo creation step is gone
        skill = (ROOT / "docs" / "library" / "skills"
                 / "loam-publishing-to-the-marketplace" / "SKILL.md").read_text(encoding="utf-8")
        check("skill removes manual 'The owner makes the repository' step and documents --public",
              ("The owner makes the repository" not in skill,
               "--public" in skill,
               "Settings › Connections › GitHub" in skill,
               "private by default" in skill),
              (True, True, True, True))

    finally:
        srv.shutdown()
        srv.server_close()
        (
            gh.STORE_DIR, gh.STORE_PATH, gh.LOCK_PATH, gh.API, gh.WEB,
            gh.PROVE_WAIT_S, loam_github.API, loam_github.helper_env,
            mk._share_base, loam_market_asset.publish, loam_plugins.yours_root,
        ) = saved
        shutil.rmtree(tmp, ignore_errors=True)

    failed = results.count(False)
    print(f"\n{'OK' if not failed else 'FAILED'}: {failed} failure(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
