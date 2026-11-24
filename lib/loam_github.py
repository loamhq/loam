"""GitHub, as the platform engineer's hands. Repos and deploy keys, nothing else.

Since 2026-09-30 the credential is the GitHub integration's user token
(``token()``, lib/loam_github_app.py), and a project engineer's push goes
through ``helper_env()``. Proven on 2026-10-01: the user token creates a
personal repo (``POST /user/repos``) and deletes it, and the personal-token
fallback was removed the same day. What follows was true of that personal
token, and is kept for the history; ``git_env`` still strips it from git's
environment while ``secrets.env`` carries it.

Decision 20 of the phase-5 closing plan: GitHub is the engineers' work, never
the owner's and never a persona's. One account-level credential exists on this
box -- ``GITHUB_TOKEN`` in ``agents/secrets.env``, issued by the owner's hands
on 2026-09-07 -- and this module is the only code that uses it. It creates a
repository (private, always), issues that repository its own deploy key, and
deletes both when a project is retired. A project engineer never sees the
token: its job is launched with the deploy key alone (see bin/engineer-job).

**The token is read from the environment and goes nowhere else.** It is never
logged, never written, never put in a URL. Every function here raises
``GitHubError`` with GitHub's own words on failure, because the persona reading
the receipt has to know whether the fix is a different token (the owner's hands)
or a bug (the engineer's).

Measured 2026-09-07 with the token as issued: a classic token with `repo`,
`delete_repo`, `admin:public_key`, `workflow` and the packages scopes. The
scopes arrive in the `x-oauth-scopes` response header on a classic token and
not at all on a fine-grained one, which is how ``whoami`` tells them apart.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import loam_config as lc

import loam_offline

API = "https://api.github.com"
API_VERSION = "2022-11-28"


class GitHubError(Exception):
    """GitHub refused, with its own words. Never carries the token."""

    def __init__(self, message: str, status: int | None = None,
                 payload: dict | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload


def token(force_refresh: bool = False) -> str:
    """The owner's user token from the GitHub integration (lib/loam_github_app.py),
    refreshed there if it is near its expiry. The only credential: the
    personal-token fallback went on 2026-10-01, once a throwaway provision had
    created, pushed to and deleted a repo through the integration."""
    import loam_github_app as app  # noqa: PLC0415
    try:
        if app.status() == "authorized":
            return app.user_token(force_refresh=force_refresh)
        raise GitHubError(
            f"GitHub is not connected (the integration is {app.status()}); "
            f"connect it, or refresh the connection, from Settings › "
            f"Connections › GitHub.")
    except app.IntegrationError as exc:
        raise GitHubError(f"the GitHub integration could not give a token: {exc}") from None


def _can_refresh_user_token() -> bool:
    import loam_github_app as app  # noqa: PLC0415
    try:
        return bool((app.load().get("user") or {}).get("refresh_token"))
    except app.IntegrationError:
        return False


def call(method: str, path: str, body: dict | None = None,
         ok: tuple[int, ...] = (200, 201, 204)) -> tuple[int, dict, dict]:
    """(status, json body or {}, response headers). Raises on anything not in ok."""
    loam_offline.guard_url(API + path, f"GitHub {method} {path.split('?')[0]}")
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(2):
        tok = token(force_refresh=attempt > 0)
        req = urllib.request.Request(
            API + path, data=data, method=method,
            headers={
                "Authorization": f"Bearer {tok}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "Content-Type": "application/json",
            })
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                payload = json.loads(raw) if raw else {}
                return resp.status, payload, dict(resp.headers)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = {"message": raw[:500]}
            if exc.code in ok:
                return exc.code, payload, dict(exc.headers)
            if exc.code == 401 and attempt == 0 and _can_refresh_user_token():
                continue
            raise GitHubError(
                f"GitHub returned {exc.code} for {method} {path}: "
                f"{payload.get('message', raw[:300])}"
                + (f" — {payload['errors']}" if payload.get("errors") else ""),
                status=exc.code, payload=payload,
            ) from None
        except urllib.error.URLError as exc:
            raise GitHubError(f"could not reach api.github.com: {exc.reason}") from None
    raise AssertionError("unreachable")


def whoami() -> dict:
    """Login, token kind and scopes. Nothing here is secret; the token itself
    is never returned."""
    status, user, headers = call("GET", "/user")
    scopes = headers.get("x-oauth-scopes") or headers.get("X-OAuth-Scopes")
    kind = ("github-app-user" if token().startswith("ghu_")
            else "classic" if scopes is not None else "fine-grained")
    return {
        "login": user.get("login"),
        "kind": kind,
        "scopes": [s.strip() for s in scopes.split(",")] if scopes else [],
        "expires": (headers.get("github-authentication-token-expiration")
                    or headers.get("GitHub-Authentication-Token-Expiration")),
    }


def get_repo(owner: str, name: str) -> dict | None:
    status, repo, _ = call("GET", f"/repos/{owner}/{name}", ok=(200, 404))
    return repo if status == 200 else None


def add_to_installation(repo: dict) -> str | None:
    """Put a repo just created in the app's installation and prove the
    installation reaches it: ``"added"`` or ``"covered"`` (on All
    repositories), see ``loam_github_app.add_repo_to_installation``. None
    when the integration is not connected, and GitHubError when it is and
    the repo cannot be proved reachable."""
    import loam_github_app as app  # noqa: PLC0415
    if app.status() != "authorized":
        return None
    try:
        return app.add_repo_to_installation(int(repo["id"]), repo["full_name"])
    except app.IntegrationError as exc:
        raise GitHubError(str(exc)) from None


def create_private_repo(name: str, description: str = "") -> dict:
    """Private, always. There is no parameter here to make it otherwise:
    public is the owner's hands (decision 8, 20), and a function that could be
    called with the wrong flag is a function that will be."""
    _, repo, _ = call("POST", "/user/repos", {
        "name": name,
        "description": description,
        "private": True,
        "auto_init": False,
        "has_wiki": False,
        "has_projects": False,
    })
    if not repo.get("private"):
        raise GitHubError(
            f"created {repo.get('full_name')} but GitHub reports private="
            f"{repo.get('private')!r}. Refusing to continue with a repository "
            f"that is not private; delete it by hand.")
    return repo


def create_repo(owner: str, name: str, description: str = "", *,
                private: bool = True) -> dict:
    """Create a repository on the connected user's account (POST /user/repos)
    or an organization (POST /orgs/<owner>/repos). Private by default; pass
    ``private=False`` only on the owner's direct yes."""
    me = whoami().get("login") or ""
    path = "/user/repos" if owner.lower() == me.lower() else f"/orgs/{owner}/repos"
    _, repo, _ = call("POST", path, {
        "name": name,
        "description": description,
        "private": bool(private),
        "auto_init": False,
        "has_wiki": False,
        "has_projects": False,
    })
    if private and not repo.get("private"):
        raise GitHubError(
            f"created {repo.get('full_name')} but GitHub reports private="
            f"{repo.get('private')!r}. Refusing to continue with a repository "
            f"that is not private; delete it by hand.")
    return repo


def delete_repo(owner: str, name: str) -> None:
    call("DELETE", f"/repos/{owner}/{name}", ok=(204,))


def add_deploy_key(owner: str, name: str, title: str, public_key: str,
                   read_only: bool = False) -> dict:
    _, key, _ = call("POST", f"/repos/{owner}/{name}/keys", {
        "title": title, "key": public_key.strip(), "read_only": read_only,
    })
    return key


def delete_deploy_key(owner: str, name: str, key_id: int) -> None:
    call("DELETE", f"/repos/{owner}/{name}/keys/{key_id}", ok=(204,))


# ---------------------------------------------------------------------------
# The key on disk, and git through it.
# ---------------------------------------------------------------------------

def generate_ssh_key(path: Path, comment: str) -> str:
    """An ed25519 keypair at `path` (mode 600) and `path.pub`. Returns the
    public key text. Refuses to overwrite: a key that exists is a key
    something may be using."""
    if path.exists():
        raise GitHubError(f"{path} already exists; refusing to overwrite a key")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment,
         "-f", str(path)],
        check=True, capture_output=True, text=True)
    path.chmod(0o600)
    return path.with_suffix(".pub").read_text(encoding="utf-8").strip()


def ssh_command(key: Path) -> str:
    """The GIT_SSH_COMMAND that makes git use this one key and nothing else.

    `IdentitiesOnly=yes` is load-bearing: without it ssh offers every key the
    agent holds, and GitHub answers with whichever repo the FIRST accepted
    key belongs to -- a push meant for one project landing in another is the
    failure that flag exists to prevent.
    """
    return f"ssh -i {key} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"


HELPER = lc.ROOT / "bin" / "git-credential-loam"


def helper_env(repo: str) -> dict[str, str]:
    """The environment that makes git push to `repo` (owner/name) with an
    installation token from the integration: ``git@github.com:`` rewritten
    to https, the credential asked of bin/git-credential-loam with the path,
    and no prompt if it answers nothing. Environment only (GIT_CONFIG_*), so
    no checkout's config changes and nothing is left behind by a job.
    Empty when the app is not installed: the deploy key stays the path."""
    import loam_github_app as app  # noqa: PLC0415
    try:
        if app.status() not in ("installed", "authorized"):
            return {}
    except app.IntegrationError:
        return {}
    pairs = [("url.https://github.com/.insteadOf", "git@github.com:"),
             ("credential.https://github.com.helper", ""),
             ("credential.https://github.com.helper", str(HELPER)),
             ("credential.https://github.com.useHttpPath", "true")]
    env = {"GIT_CONFIG_COUNT": str(len(pairs)), "LOAM_GIT_REPO": repo,
           "GIT_TERMINAL_PROMPT": "0"}
    for i, (key, value) in enumerate(pairs):
        env[f"GIT_CONFIG_KEY_{i}"] = key
        env[f"GIT_CONFIG_VALUE_{i}"] = value
    return env


def https_remote(repo: str) -> str:
    """The remote a repository Loam does not own is cloned from: https, so
    every fetch goes through the installation's token and no deploy key is
    ever added to someone else's repository."""
    return f"https://github.com/{repo}.git"


def ask_broker(route: str, payload: dict, timeout: int = 240) -> dict:
    """POST to the broker's GitHub routes (``/v1/integrations/github/<route>``)
    as bin/git-credential-loam does, and return its JSON. Raises GitHubError
    with the broker's own words. ``LOAM_BROKER_URL`` and
    ``LOAM_BROKER_TOKEN_FILE`` are for tests."""
    import loam_broker as cfg  # noqa: PLC0415
    base = os.environ.get("LOAM_BROKER_URL") or f"http://{cfg.BIND_ADDR}:{cfg.PORT}"
    token_file = Path(os.environ.get("LOAM_BROKER_TOKEN_FILE") or cfg.TOKEN_PATH)
    try:
        bearer = token_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise GitHubError(f"cannot read the broker's token: {exc.strerror}") from None
    req = urllib.request.Request(
        f"{base}/v1/integrations/github/{route}", method="POST",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {bearer}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            reason = json.loads(exc.read()).get("error")
        except ValueError:
            reason = None
        raise GitHubError(f"the broker answered {exc.code} for {route}: "
                          f"{reason or exc.reason}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise GitHubError(f"could not ask the broker ({route}): {exc}") from None


def open_pull_request(project: str, branch: str, title: str, body: str) -> dict:
    """The landing of a pull-request project: the broker pushes the job's
    branch and opens the PR (lib/loam_github_app.open_pull_request)."""
    return ask_broker("pull-request", {"project": project, "branch": branch,
                                       "title": title, "body": body})


def git_env(key: Path | None) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("GITHUB_TOKEN", None)   # git never needs it, so it never gets it
    if key is not None:
        env["GIT_SSH_COMMAND"] = ssh_command(key)
    return env


def git(args: list[str], cwd: Path, key: Path | None = None,
        check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), env=git_env(key),
        capture_output=True, text=True, check=check, timeout=300)
