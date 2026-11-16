"""GitHub as a GitHub App: the integration the owner connects from the app.

Added 2026-09-30 to replace the one account-level personal token
(``GITHUB_TOKEN`` in ``agents/secrets.env``, a classic token that expires
2026-10-07) with something that does not expire on him while he uses Loam and
that he controls from the app instead of by pasting.

The shape is GitHub's own, "registering a GitHub App from a manifest":

  1. manifest   the page posts ``manifest()``'s JSON to GitHub; the owner
                clicks Create; GitHub redirects the browser to
                ``<app>/integrations/github/registered?code=..&state=..``.
  2. register   ``register(code, state)`` exchanges the one-hour code at
                ``POST /app-manifests/{code}/conversions`` for the app's id,
                slug, private key, client id and secret. Stored here.
  3. install    the page sends him to ``install_url()`` (a GitHub screen,
                "All repositories"). Because the manifest asks for
                ``request_oauth_on_install``, GitHub authorizes him as a user
                in the same click and redirects to
                ``<app>/integrations/github/installed?code=..&installation_id=
                ..&setup_action=install&state=..``.
  4. callback   ``callback(...)`` exchanges that code for a user token and a
                refresh token, verifies the installation with an app JWT and
                the user with the user token, and stores both.

After that two kinds of token exist, and each is used for what only it can
do. **The user token** (eight hours, refreshed here for up to six months of
use, each refresh issuing a new pair) acts as the owner: creating a repository
on their personal account is a ``/user`` call, and an installation is not a
user. **Installation tokens** (one hour, minted from the private key, never
written to disk, and never cached: see ``installation_token``) act as the app
on one repository: a project engineer's push.

**The store is one file**, ``state/github/integration.json``, mode 600, owned
by the user the broker runs as, outside git (``state/`` is ignored) and never
mounted into the app's container. Every read-modify-write holds an ``flock``
on ``integration.lock`` beside it, because three processes may touch it --
the broker, ``bin/provision`` and ``bin/rc-preflight`` -- and a user refresh
token is single-use: two processes refreshing the same pair at once would
leave one of them holding a dead token, and the store holding whichever wrote
last. Under the lock the store is re-read before a refresh, so the second
process finds the first one's fresh pair and does not refresh at all.

**More than one installation** (since 2026-10-06). The first is
``installation``, on the account Loam was connected from; any installed since
on another account (an organization the owner owns, selected repositories) is in
``added_installations``. Each covers the repositories of its own account, and
a token for ``owner/name`` is minted from the one whose account is ``owner``
(``installation_for``) or not at all. An installation is recorded only through
a state this box issued, and, once a user is authorized, only if GitHub lists
it among that user's own installations (``GET /user/installations``): an app
that is public can be installed by anyone, and GitHub then redirects the
stranger to our setup URL with an installation id of their own. GitHub's own
stateless update redirect is accepted for an installation already recorded,
and for nothing else.

**More than one app** (since 2026-10-06, the same day). The app Loam was
connected with stays where it always was, at the top of the store. An app
owned by an organization -- registered from a manifest posted to that
organization's create page, private, installed there on selected
repositories -- is one entry of ``org_apps``, keyed by the organization's
login, with its own ``app``, ``installation``, ``user`` and ``health``. It
answers for that organization's repositories and for nothing else: a token
for ``owner/name`` comes from ``org_apps[owner]`` when there is one, and then
never from the first app, whatever the first app has recorded; and an org
app installed on any other account records nothing. The reason is GitHub's:
permissions are set per app, so an organization can widen, narrow or delete
its own app without touching the personal one. Its states have purposes of
their own (``register-org``, ``install-org``), so a broker still running the
code from before this refuses them instead of writing an org app over the
first one.

**A project that lands by pull request** (``"landing": "pull_request"`` in its
registry record, a repository Loam does not own) gets job tokens with
``contents: read``: its engineer fetches, and cannot push at all. The job's
branch reaches the remote through ``open_pull_request``, which the landing
asks the broker for, and which pushes ``job/<id>`` and nothing else.

Nothing here logs a token, a key or a secret. ``public_view()`` is the only
thing a route returns about the store, and it carries none of them.
"""

from __future__ import annotations

import base64
import fcntl
import json
import os
import re
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import loam_config as lc

import loam_offline

AGENTS_ROOT = lc.ROOT
STORE_DIR = lc.STATE / "github"
STORE_PATH = STORE_DIR / "integration.json"
LOCK_PATH = STORE_DIR / "integration.lock"
# The nightly check writes the date it last ran here, so the first
# rc-preflight of the night calls GitHub and the other six do not.
CHECKED_PATH = STORE_DIR / "checked-on"

# Module constants rather than parameters so a test can point both at a fake
# GitHub on loopback; nothing on the box sets them.
API = "https://api.github.com"
WEB = "https://github.com"
API_VERSION = "2022-11-28"

# The app that draws the integration and receives GitHub's redirects. Its
# host is `<APP>.<domain>`, the domain read from deploy/loam.toml.
APP = "loam-ui"
REGISTERED_PATH = "/integrations/github/registered"
INSTALLED_PATH = "/integrations/github/installed"

APP_NAME = "Loam"

# The minimum for what the runtime does with it; see manifest().
PERMISSIONS = {
    "administration": "write",   # create a repo's settings, deploy keys, delete on retire
    "contents": "write",         # clone, fetch and push
    "metadata": "read",          # mandatory for any repository permission
    "workflows": "write",        # a push that carries .github/workflows/* is refused without it
}
# What a project engineer's token may never carry, whatever the installation
# grants. Administration write can PATCH a repository's visibility, and making
# a repo public is the owner's hands only; the runtime's own provisioning uses
# the user token, not this one.
PROJECT_WITHHELD = frozenset({"administration"})
# What a job's token carries instead, on a project that lands by pull request:
# it may read the repository and may not write it. `workflows` goes with
# `contents: write`, since it only widens what a push may carry.
PULL_REQUEST_READ = {"contents": "read"}
PULL_REQUEST_WITHHELD = frozenset({"workflows"})
# An app owned by an organization, for repositories Loam does not own and
# never creates or deletes there: no administration. Each line is the reason.
ORG_PERMISSIONS = {
    "contents": "write",        # the broker pushes a job's branch; jobs get read
    "metadata": "read",         # mandatory for any repository permission
    "pull_requests": "write",   # a job lands as a pull request the broker opens
    "issues": "write",          # the engineer reads, opens and answers issues
    "actions": "read",          # the engineer reads CI runs and their logs
    "checks": "read",           # ...and a pull request's check results
}
ORG_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
# The only branch open_pull_request pushes: a job's own.
JOB_BRANCH_RE = re.compile(r"^job/\d{8}-\d{6}-[0-9a-f]{8}$")
# A person-authored project's pull request head is a plain name made from its
# title (``pr_head``), remembered on the job's branch in the canonical
# checkout's git config under this key so a continuation finds the same one.
PR_HEAD_KEY = "prhead"
PR_HEAD_MAX = 48

# What the page lists under "used for". Stated here so it is one list.
USES = ["provisioning", "engineer pushes"]

STATE_TTL_S = 3600            # GitHub's manifest code lives an hour; so does our state
# A state minted on the box for a link relayed to a person who clicks later
# (bin/github-app org-manifest --for): ours may wait longer than an hour,
# since GitHub's own code starts its hour only at Create. Never past a week.
STATE_TTL_MAX_S = 7 * 86400
USER_REFRESH_MARGIN_S = 3600  # refresh the user token within an hour of expiry
REFRESH_WARN_DAYS = 14        # the nightly check's warning window
PROVE_TRIES, PROVE_WAIT_S = 3, 2.0  # a new repo's token, before it counts as covered
JWT_LIFETIME_S = 540          # GitHub's ceiling is ten minutes


class IntegrationError(Exception):
    """Something a person can act on, in a sentence. Never carries a secret."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Time and the store
# ---------------------------------------------------------------------------

def now() -> float:
    return time.time()


def iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


_thread_lock = threading.RLock()


@contextmanager
def locked():
    """The store's lock: a thread lock for the broker's worker threads and an
    flock for every other process. Re-entrant within a thread."""
    with _thread_lock:
        STORE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def load() -> dict:
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise IntegrationError(f"{STORE_PATH} cannot be read: {exc}", 500) from None
    return data if isinstance(data, dict) else {}


def save(data: dict) -> None:
    """Write whole, mode 600 from the first byte, then rename over."""
    STORE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = STORE_PATH.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, STORE_PATH)


# ---------------------------------------------------------------------------
# HTTP to GitHub
# ---------------------------------------------------------------------------

def _request(method: str, url: str, body: dict | None = None,
             auth: str | None = None, form: bool = False,
             ok: tuple[int, ...] = (200, 201, 204)) -> tuple[int, dict]:
    headers = {"Accept": "application/vnd.github+json" if not form else "application/json",
               "X-GitHub-Api-Version": API_VERSION,
               "User-Agent": "loam-runtime"}
    data = None
    if body is not None:
        if form:
            data = urllib.parse.urlencode(body).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
    if auth:
        headers["Authorization"] = f"Bearer {auth}"
    loam_offline.guard_url(url, f"GitHub {method} {url.split('?')[0]}")
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            payload = {"message": raw[:300]}
        if exc.code in ok:
            return exc.code, payload
        path = urllib.parse.urlsplit(url).path
        raise IntegrationError(
            f"GitHub returned {exc.code} for {method} {path}: "
            f"{payload.get('message', raw[:200])}", 502) from None
    except urllib.error.URLError as exc:
        raise IntegrationError(f"could not reach GitHub: {exc.reason}", 502) from None


def api(method: str, path: str, auth: str, body: dict | None = None,
        ok: tuple[int, ...] = (200, 201, 204)) -> tuple[int, dict]:
    return _request(method, API + path, body, auth=auth, ok=ok)


def _oauth(fields: dict) -> dict:
    """GitHub's token endpoint answers 200 with an `error` field on refusal."""
    _, payload = _request("POST", WEB + "/login/oauth/access_token", fields, form=True)
    if payload.get("error"):
        raise IntegrationError(
            f"GitHub refused the authorization: {payload.get('error')}"
            + (f" ({payload['error_description']})"
               if payload.get("error_description") else ""), 502)
    if not payload.get("access_token"):
        raise IntegrationError("GitHub's token endpoint answered with no token", 502)
    return payload


# ---------------------------------------------------------------------------
# The app JWT
# ---------------------------------------------------------------------------

def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def app_jwt(pem: str, issuer: str, at: float | None = None) -> str:
    """An RS256 JWT for GitHub's /app endpoints. `iat` is backdated a minute
    for clock drift, as GitHub's docs advise; `iss` is the client id."""
    from cryptography.hazmat.primitives import hashes, serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric import padding  # noqa: PLC0415

    at = now() if at is None else at
    header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64(json.dumps({"iat": int(at) - 60, "exp": int(at) + JWT_LIFETIME_S,
                              "iss": issuer}).encode())
    signing_input = f"{header}.{claims}".encode()
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{claims}.{_b64(signature)}"


def _jwt(data: dict) -> str:
    app = data.get("app") or {}
    if not app.get("pem"):
        raise IntegrationError("GitHub is not connected: no app is registered", 409)
    return app_jwt(app["pem"], str(app.get("client_id") or app["id"]))


# ---------------------------------------------------------------------------
# The flow
# ---------------------------------------------------------------------------

def app_url() -> str:
    import loam_deploy  # noqa: PLC0415
    app = next((a for a in loam_deploy.records() if a.name == APP), None)
    return f"https://{app.fqdn}" if app else f"https://{APP}.{loam_deploy.domain()}"


def _state_ttl(entry: dict) -> float:
    """How long a state lives: its own ``ttl`` when it was minted with one
    (capped at a week), otherwise an hour."""
    ttl = entry.get("ttl")
    if isinstance(ttl, (int, float)) and not isinstance(ttl, bool) and ttl > 0:
        return min(float(ttl), STATE_TTL_MAX_S)
    return STATE_TTL_S


def _new_state(data: dict, purpose: str, org: str | None = None,
               ttl_s: float | None = None) -> str:
    """A single-use state. An org app's carries the org, and a purpose the
    code before org apps does not know (``<purpose>-org``). ``ttl_s``, for a
    link relayed to someone who clicks later, is kept on the entry."""
    states = {k: v for k, v in (data.get("states") or {}).items()
              if now() - v.get("created", 0) < _state_ttl(v)}
    state = secrets.token_urlsafe(24)
    states[state] = ({"purpose": f"{purpose}-org", "org": org, "created": now()}
                     if org else {"purpose": purpose, "created": now()})
    if ttl_s is not None:
        if not 0 < ttl_s <= STATE_TTL_MAX_S:
            raise IntegrationError("a state lives between a moment and a week")
        states[state]["ttl"] = int(ttl_s)
    data["states"] = states
    return state


def _take_state(data: dict, state: str | None, purpose: str) -> str | None:
    """Spend a state issued for ``purpose``; the org it was issued for, or
    None for the first app."""
    states = data.get("states") or {}
    entry = states.pop(state or "", None)
    data["states"] = states
    if not entry or entry.get("purpose") not in (purpose, f"{purpose}-org"):
        raise IntegrationError(
            "that `state` is not one this box issued for this step, or it has "
            "been used; start the connection again from the app", 400)
    if now() - entry.get("created", 0) >= _state_ttl(entry):
        raise IntegrationError("that `state` has expired; start again", 400)
    return entry.get("org") if entry["purpose"].endswith("-org") else None


def _org_key(org: str | None) -> str | None:
    if org is None:
        return None
    if not ORG_RE.match(org):
        raise IntegrationError(f"{org!r} is not a GitHub organization login")
    return org.lower()


def org_apps(data: dict) -> dict[str, dict]:
    return {k: v for k, v in (data.get("org_apps") or {}).items() if isinstance(v, dict)}


def _slot(data: dict, org: str | None) -> dict:
    """The part of the store one app lives in: the store itself for the first
    app, ``org_apps[org]`` for an organization's."""
    if org is None:
        return data
    slot = org_apps(data).get(org.lower())
    if not slot or not slot.get("app"):
        raise IntegrationError(f"no app is registered for {org}", 409)
    return slot


def manifest(name: str = APP_NAME, org: str | None = None) -> dict:
    """What the page posts to GitHub. `name` takes a suffix if GitHub refuses
    the plain one as taken (app names are global on GitHub). An org's app
    asks for ``ORG_PERMISSIONS`` instead of ``PERMISSIONS``."""
    base = app_url()
    return {
        "name": name,
        "url": base,
        "redirect_url": base + REGISTERED_PATH,
        "callback_urls": [base + INSTALLED_PATH],
        "setup_url": base + INSTALLED_PATH,
        "setup_on_update": True,
        "request_oauth_on_install": True,
        "public": False,
        # No webhook: nothing on this box listens for one. `active: false`
        # is stated rather than omitting the block, because GitHub defaults
        # an omitted hook to active and then requires its url.
        "hook_attributes": {"url": base + "/integrations/github/webhook",
                            "active": False},
        "default_permissions": dict(ORG_PERMISSIONS if org else PERMISSIONS),
        "default_events": [],
    }


def start_manifest(name: str | None = None, org: str | None = None,
                   ttl_s: float | None = None) -> dict:
    """The manifest and where to post it: the personal create page, or with
    ``org`` that organization's (``/organizations/<org>/settings/apps/new``,
    GitHub's manifest docs), where only an owner of the org can click Create.
    The app it makes is owned by the org."""
    org = _org_key(org)
    with locked():
        data = load()
        state = _new_state(data, "register", org, ttl_s)
        save(data)
    page = f"/organizations/{org}/settings/apps/new" if org else "/settings/apps/new"
    return {
        "manifest": manifest(name or (f"{APP_NAME} for {org}" if org else APP_NAME), org),
        "post_url": f"{WEB}{page}?state={urllib.parse.quote(state)}",
        "state": state,
    }


def register(code: str, state: str) -> dict:
    """Exchange the manifest code. The code is single-use and good for an
    hour; the state is ours and single-use too, and says which app this is:
    the first, or an organization's."""
    if not code:
        raise IntegrationError("a `code` is required: the one GitHub redirected with")
    with locked():
        data = load()
        org = _take_state(data, state, "register")
        save(data)
    _, app = _request("POST", f"{API}/app-manifests/{urllib.parse.quote(code)}/conversions",
                      body={})
    for field in ("id", "slug", "pem", "client_id", "client_secret"):
        if not app.get(field):
            raise IntegrationError(f"GitHub's conversion answered without `{field}`", 502)
    owner = (app.get("owner") or {}).get("login")
    if org and (owner or "").lower() != org:
        raise IntegrationError(
            f"the app GitHub created ({app['slug']}) is owned by {owner}, not "
            f"{org}; nothing was recorded. Delete it on GitHub and start again "
            f"from {org}'s create page.", 409)
    record = {
        "id": app["id"], "slug": app["slug"],
        "html_url": app.get("html_url") or f"{WEB}/apps/{app['slug']}",
        "owner": owner,
        "client_id": app["client_id"], "client_secret": app["client_secret"],
        "pem": app["pem"], "webhook_secret": app.get("webhook_secret"),
        "registered_at": iso(now()),
    }
    with locked():
        data = load()
        if org:
            # A new app for the org replaces whatever the org had, whole.
            data.setdefault("org_apps", {})[org] = {"org": org, "app": record,
                                                    "health": {}}
        else:
            data["app"] = record
            # A new app is a new identity: whatever was installed or
            # authorized belonged to the old one. An org's app is its own.
            data.pop("installation", None)
            data.pop("added_installations", None)
            data.pop("user", None)
            data["health"] = {}
        save(data)
    return {**public_view(), "install": start_install(org)}


def start_install(org: str | None = None) -> dict:
    """The GitHub screen where he installs the app, with a state for the way
    back. Also the "refresh connection" path: installing again re-runs the
    OAuth half and records the installation afresh. With ``org``, that
    organization's app; GitHub offers a private org app only on the org."""
    org = _org_key(org)
    with locked():
        data = load()
        app = data.get("app") if org is None else \
            (org_apps(data).get(org) or {}).get("app")
        if not app:
            raise IntegrationError("register the app first", 409)
        state = _new_state(data, "install", org)
        save(data)
    return {"url": f"{WEB}/apps/{app['slug']}/installations/new?"
                   f"state={urllib.parse.quote(state)}",
            "state": state}


def _user_fields(tok: dict, at: float) -> dict:
    out = {"token": tok["access_token"],
           "token_expires_at": iso(at + int(tok["expires_in"]))
           if tok.get("expires_in") else None,
           "refresh_token": tok.get("refresh_token"),
           "refresh_expires_at": iso(at + int(tok["refresh_token_expires_in"]))
           if tok.get("refresh_token_expires_in") else None}
    return out


def installations(data: dict) -> list[dict]:
    """Every installation recorded: the first, then any added since."""
    out = [data["installation"]] if isinstance(data.get("installation"), dict) else []
    return out + [i for i in data.get("added_installations") or []
                  if isinstance(i, dict)]


def route(data: dict, repository: str) -> tuple[dict, dict]:
    """The app and the recorded installation a token for ``owner/name`` comes
    from: ``(slot, installation)``, the slot being what ``_jwt`` signs with.

    An organization with an app of its own is answered by that app alone,
    and only from its installation on that organization; the first app's
    installations are not candidates for it. Every other owner is the first
    app's, from its installation on ``owner``. An installation nobody
    recorded is never a candidate, whatever GitHub says it reaches."""
    owner = repository.partition("/")[0].lower()
    own = org_apps(data).get(owner)
    if own is not None:
        inst = own.get("installation")
        if inst and (inst.get("account") or "").lower() == owner:
            return own, inst
        slug = (own.get("app") or {}).get("slug")
        raise IntegrationError(
            f"{owner}'s own app ({slug}) is not installed on {owner}; a token "
            f"for {owner} comes from that app or not at all", 409)
    for inst in installations(data):
        if (inst.get("account") or "").lower() == owner:
            return data, inst
    if not installations(data):
        raise IntegrationError("GitHub is not connected: the app is not installed", 409)
    raise IntegrationError(
        f"no installation Loam recorded covers {owner}: install the app on "
        f"that account from the app's Integrations screen, which is the only "
        f"way one is recorded", 409)


def installation_for(data: dict, repository: str) -> dict:
    """The recorded installation ``route`` mints ``owner/name``'s token from."""
    return route(data, repository)[1]


def _record_installation(data: dict, installation: dict) -> None:
    """The first slot when it is empty or is this account's; otherwise the
    added list, replacing this installation or this account's previous one.
    An org app's slot holds its one installation, on the org."""
    inst_id = installation["installation_id"]
    account = (installation.get("account") or "").lower()
    if data.get("org"):
        data["installation"] = installation
        return
    first = data.get("installation")
    if (not first or first.get("installation_id") == inst_id
            or (first.get("account") or "").lower() == account):
        data["installation"] = installation
        return
    added = [i for i in data.get("added_installations") or []
             if i.get("installation_id") != inst_id
             and (i.get("account") or "").lower() != account]
    data["added_installations"] = added + [installation]


def callback(code: str | None, installation_id, setup_action: str | None,
             state: str | None) -> dict:
    """After install. A redirect GitHub starts on its own (he changed the
    repository selection on GitHub, `setup_action=update`) carries no state
    of ours, and is accepted only for an installation already recorded.

    A new installation also needs a state we issued, and -- when a user is
    authorized, in this redirect or before it -- GitHub must list it among
    that user's installations. The user is the one Loam is connected as: an
    authorization by anyone else records nothing, so a second account's
    install never replaces the token provisioning acts with.

    Which app: the one the state was issued for, or, for a stateless update,
    the one that recorded the installation. An organization's app records an
    installation on that organization and nowhere else, and its user must be
    the one the first app is connected as."""
    try:
        inst_id = int(installation_id)
    except (TypeError, ValueError):
        raise IntegrationError("an `installation_id` is required") from None
    with locked():
        data = load()
        if not data.get("app") and not org_apps(data):
            raise IntegrationError("register the app first", 409)
        holder = {i.get("installation_id"): None for i in installations(data)}
        for key, own in org_apps(data).items():
            if own.get("installation"):
                holder[own["installation"].get("installation_id")] = key
        if state or setup_action != "update" or inst_id not in holder:
            org = _take_state(data, state, "install")
        else:
            org = holder[inst_id]
        save(data)
        slot = _slot(data, org)
        if not slot.get("app"):
            raise IntegrationError("register the app first", 409)
        recorded = {i.get("installation_id") for i in installations(slot)}
        app = slot["app"]
        known = (slot.get("user") or {}).get("login") or \
            ((data.get("user") or {}).get("login") if org else None)
    jwt = _jwt(slot)
    _, inst = api("GET", f"/app/installations/{inst_id}", jwt)
    installation = {
        "installation_id": inst_id,
        "account": (inst.get("account") or {}).get("login"),
        "repository_selection": inst.get("repository_selection"),
        "installed_at": iso(now()),
    }
    if org and (installation["account"] or "").lower() != org:
        raise IntegrationError(
            f"{app['slug']} is {org}'s app, and this installation is on "
            f"{installation['account']}; nothing was recorded", 403)
    user, user_tok = None, None
    if code:
        at = now()
        tok = _oauth({"client_id": app["client_id"], "client_secret": app["client_secret"],
                      "code": code})
        _, me = api("GET", "/user", tok["access_token"])
        if known and (me.get("login") or "").lower() != known.lower():
            raise IntegrationError(
                f"this install was authorized by {me.get('login')}, and Loam "
                f"is connected as {known}; nothing was recorded. Install it "
                f"signed in as {known}.", 403)
        user = {"login": me.get("login"), **_user_fields(tok, at),
                "authorized_at": iso(at)}
        user_tok = tok["access_token"]
    elif slot.get("user"):
        user_tok = user_token(org=org)
    if user_tok and inst_id not in recorded:
        # A user token is the app's own: this lists what this app has
        # installed that this user can reach.
        _, mine = api("GET", "/user/installations?per_page=100", user_tok)
        if inst_id not in {i.get("id") for i in mine.get("installations") or []}:
            raise IntegrationError(
                f"installation {inst_id} (on {installation['account']}) is not "
                f"one {(user or {}).get('login') or known} can reach on GitHub, "
                f"so it was not started from this connection; nothing was "
                f"recorded", 403)
    with locked():
        data = load()
        slot = _slot(data, org)
        _record_installation(slot, installation)
        if user:
            slot["user"] = user
        slot["health"] = {"last_ok_at": iso(now()), "last_error": None}
        save(data)
    return public_view()


def forget(org: str | None = None) -> dict:
    """Forget everything here, or with ``org`` that organization's app alone.
    The app and the authorization on GitHub are his to remove; these are the
    screens where he does."""
    org = _org_key(org)
    with locked():
        data = load()
        if org:
            # An org with no app here is a 404, never a quiet success: a
            # mistyped org must not read as "forgotten".
            if org not in org_apps(data):
                raise IntegrationError(f"no app here for the organization {org}", 404)
            app = (org_apps(data).get(org) or {}).get("app") or {}
            data.get("org_apps", {}).pop(org, None)
            save(data)
        else:
            app = data.get("app") or {}
            STORE_PATH.unlink(missing_ok=True)
    slug = app.get("slug")
    if org:
        return {"forgotten": True, "org": org,
                "uninstall_url": f"{WEB}/organizations/{org}/settings/installations"
                if slug else None,
                "revoke_url": f"{WEB}/settings/apps/authorizations" if slug else None,
                "delete_app_url": f"{WEB}/organizations/{org}/settings/apps/{slug}/advanced"
                if slug else None}
    return {
        "forgotten": True,
        "uninstall_url": f"{WEB}/settings/installations" if slug else None,
        "revoke_url": f"{WEB}/settings/apps/authorizations" if slug else None,
        "delete_app_url": f"{WEB}/settings/apps/{slug}/advanced" if slug else None,
    }


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

def user_token(force_refresh: bool = False, org: str | None = None) -> str:
    """The owner's user token, refreshed here when within an hour of expiry.
    The whole check-refresh-save runs under the store lock. With ``org``,
    his authorization of that organization's app, which is a pair of its own:
    a user token belongs to the app that issued it."""
    with locked():
        data = load()
        slot = _slot(data, org)
        user = slot.get("user")
        if not user or not user.get("token"):
            raise IntegrationError("GitHub is not connected: no user authorization", 409)
        expires = parse_iso(user.get("token_expires_at"))
        due = expires is not None and expires - now() < USER_REFRESH_MARGIN_S
        if not (force_refresh or due):
            return user["token"]
        if not user.get("refresh_token"):
            raise IntegrationError(
                "the user token cannot be refreshed: GitHub issued no refresh "
                "token (the app's \"expire user authorization tokens\" is off)", 409)
        refresh_expires = parse_iso(user.get("refresh_expires_at"))
        if refresh_expires is not None and refresh_expires <= now():
            raise IntegrationError(
                "the GitHub authorization has expired; reconnect from the app", 409)
        app = slot["app"]
        at = now()
        try:
            tok = _oauth({"client_id": app["client_id"],
                          "client_secret": app["client_secret"],
                          "grant_type": "refresh_token",
                          "refresh_token": user["refresh_token"]})
        except IntegrationError as exc:
            slot.setdefault("health", {})["last_error"] = str(exc)
            save(data)
            raise
        user.update(_user_fields(tok, at))
        user["refreshed_at"] = iso(at)
        slot["user"] = user
        save(data)
        return user["token"]


def project_permissions(installation: dict) -> dict:
    """What a project token asks for: every permission the installation has
    accepted, less ``PROJECT_WITHHELD``. Read live, because a widened app
    reaches tokens only once the installation accepts it, and asking for a
    permission the installation lacks is GitHub's 422 for the whole mint.

    Until 2026-10-01 this was a fixed contents/metadata/workflows set, so
    Issues, Pull requests and Actions, once the owner added them to the app,
    could never have reached a project engineer however they were accepted."""
    granted = installation.get("permissions") or {}
    return {k: v for k, v in sorted(granted.items()) if k not in PROJECT_WITHHELD}


def _pr_record(repository: str) -> dict | None:
    """The registry record of the project that lands ``owner/name`` by pull
    request, read fresh, or None."""
    import loam_engineer as eng  # noqa: PLC0415
    for project in eng.projects().values():
        if (not project.platform and (project.repo or "").lower() == repository.lower()
                and project.record.get("landing") == "pull_request"):
            return project.record
    return None


def pull_request_only(repository: str) -> bool:
    """Whether ``owner/name`` is the repository of a project that lands by
    pull request (its registry record, read fresh)."""
    return _pr_record(repository) is not None


def job_permissions(installation: dict, repository: str) -> dict:
    """``project_permissions``, read-only on contents for a repository that
    lands by pull request. Everything else the installation grants still
    reaches the token, so a wider grant later widens it with no code here."""
    perms = project_permissions(installation)
    record = _pr_record(repository)
    if record is not None:
        perms = {k: v for k, v in perms.items() if k not in PULL_REQUEST_WITHHELD}
        perms.update(PULL_REQUEST_READ)
        if record.get("commit_identity"):
            # A person's repository (lib/loam_commit_identity.py): whatever the job
            # wrote on GitHub itself -- a comment, an issue -- would be the
            # app's bot speaking there. It reads; the person's token writes.
            perms = {k: "read" if v == "write" else v for k, v in perms.items()}
    return perms


def _mint(data: dict, inst_id: int, body: dict) -> dict:
    _, tok = api("POST", f"/app/installations/{inst_id}/access_tokens", _jwt(data),
                 body=body)
    expires = parse_iso(tok.get("expires_at")) or now() + 3600
    return {"token": tok["token"], "expires_at": iso(expires)}


def installation_token(repository: str | None = None) -> dict:
    """An installation token, scoped to one repository when one is named
    (``owner/name``), minted from the recorded installation on ``owner``,
    with ``job_permissions`` of that installation as it is live.

    Minted fresh on every call. Until 2026-10-01 these were cached for most
    of their hour, and the cache was wrong in a way no expiry check sees:
    GitHub resolves ``repositories: [name]`` to a repository id when it
    mints, so a token cached before a repo was deleted and recreated under
    the same name kept answering for the dead id -- ``GET
    /installation/repositories`` 0, the new repo 404 -- for up to 55 minutes.
    The deleting and the recreating happen in ``bin/provision``, a different
    process from the broker that held the cache. One JWT-signed POST per git
    operation is the price."""
    data = load()
    if not repository:
        inst = (data.get("installation") or {}).get("installation_id")
        if not inst:
            raise IntegrationError("GitHub is not connected: the app is not installed", 409)
        return {**_mint(data, inst, {}), "repository": None}
    owner, _, name = repository.partition("/")
    if not owner or not name:
        raise IntegrationError("`repository` is owner/name")
    slot, found = route(data, repository)
    inst = found["installation_id"]
    _, live = api("GET", f"/app/installations/{inst}", _jwt(slot))
    body = {"repositories": [name], "permissions": job_permissions(live, repository)}
    return {**_mint(slot, inst, body), "repository": repository}


def git_token_env(token: str) -> dict[str, str]:
    """This process's environment, with git authenticating to github.com by
    ``token`` through an extra header in GIT_CONFIG_* -- never in a URL, an
    argument or a config file -- and every inherited helper or rewrite gone."""
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("GIT_CONFIG", "GIT_SSH")) and k != "LOAM_GIT_REPO"}
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_COUNT": "2",
                "GIT_CONFIG_KEY_0": "credential.helper", "GIT_CONFIG_VALUE_0": "",
                "GIT_CONFIG_KEY_1": "http.https://github.com/.extraheader",
                "GIT_CONFIG_VALUE_1": f"AUTHORIZATION: basic {basic}"})
    return env


def read_token(repository: str) -> dict:
    """A token that can read ``owner/name`` and nothing more: what
    bin/provision adopts a repository with, minted in-process from the
    recorded installation on ``owner``."""
    slot, found = route(load(), repository)
    name = repository.partition("/")[2]
    return _mint(slot, found["installation_id"], {"repositories": [name],
                              "permissions": {"contents": "read", "metadata": "read"}})


def open_pull_request(project_name: str, branch: str, title: str,
                      body: str = "") -> dict:
    """Push a job's branch of a pull-request project to its remote and open
    (or find) the pull request against the default branch. The broker's half
    of such a landing: the job's own token cannot write the repository.

    What is pushed is decided here, not by the caller: the commit
    ``refs/heads/<branch>`` names in the project's canonical checkout, and
    ``branch`` must be a job's (``job/<id>``). It goes to
    ``refs/heads/<branch>`` on the remote, or for a person-authored project to
    a plain name from the title that no other branch there has (``pr_head``),
    so the job id never reaches their repository. Nothing here can name the
    default branch as a target. A continuation whose commit no longer
    fast-forwards that branch, or whose pull request was closed or merged,
    goes to a fresh branch and a new pull request, and one whose pull request
    is still open is refused (``settle_head``)."""
    project, base, sha = _pr_job(project_name, branch)
    owner = project.repo.partition("/")[0]
    import loam_commit_identity  # noqa: PLC0415
    identity = loam_commit_identity.of(project.record)
    token = _pr_token(project, branch, sha, title, body)
    env = git_token_env(token)
    head = (pr_head(project, branch, title, base, env) if identity
            else recorded_head(project, branch) or branch)
    if head.lower() == base.lower():
        raise IntegrationError(f"refusing to push {branch} to {project.repo}'s "
                               f"default branch {base}", 409)
    head, replaces = settle_head(project, branch, title, base, sha, head, token,
                                 env, bool(identity))
    if replaces.get("refused"):
        return {"sha": sha, "branch": branch, "head": head, "base": base,
                "created": False, "pushed": False, **replaces}
    push = subprocess.run(
        ["git", "-C", str(project.root), "push", "--quiet", project.remote,
         f"{sha}:refs/heads/{head}"],
        capture_output=True, text=True, check=False, env=env, timeout=180)
    if push.returncode != 0:
        said = (push.stderr or push.stdout).replace(token, "***").strip()[:400]
        raise IntegrationError(f"the push of {branch} to {project.repo} failed: {said}", 502)
    status, pr = api("POST", f"/repos/{project.repo}/pulls", token,
                     body={"title": title or head, "head": head, "base": base,
                           "body": body, "maintainer_can_modify": True},
                     ok=(201, 422))
    created = status == 201
    if not created:
        query = urllib.parse.urlencode({"head": f"{owner}:{head}", "state": "open"})
        _, open_prs = api("GET", f"/repos/{project.repo}/pulls?{query}", token)
        if not isinstance(open_prs, list) or not open_prs:
            raise IntegrationError(f"GitHub refused the pull request: "
                                   f"{pr.get('message')} {pr.get('errors') or ''}", 502)
        pr = open_prs[0]
    return {"url": pr.get("html_url"), "number": pr.get("number"), "sha": sha,
            "branch": branch, "head": head, "base": base, "created": created,
            **({"replaces": replaces} if replaces else {})}


def settle_head(project, branch: str, title: str, base: str, sha: str,
                head: str, token: str, env: dict, plain_names: bool) -> tuple[str, dict]:
    """``(head, replaces)``: the remote branch a job's commit goes to, when
    the name it would use is already on the remote (since 2026-10-21).

    A continuation is pushed to the head its first landing used, so its
    pull request is updated. That holds only while the commit fast-forwards
    that head and the pull request is still open. Caused 2026-10-21 on an
    adopted project: a continuation rebuilt its work on a newer main after
    its owner closed #46, and three landings in a row pushed it at #46's
    branch and were rejected as non-fast-forward. So:

    - no pull request from it was ever closed or merged, and it is not on
      the remote or the commit fast-forwards it: the same head, ``replaces``
      empty (an open one is then updated, today's path);
    - a pull request from it is open and the commit does not fast-forward
      it: refused, ``replaces["refused"] == "open_pr_diverged"``, with
      nothing pushed. An open pull request is never forked silently;
    - otherwise (its pull request closed or merged, whether or not its
      branch was deleted since, or none and the commit rebuilt): a fresh
      head (``fresh_head``), recorded for ``branch`` so the next
      continuation finds it, and ``replaces`` names the old one. The old
      branch is left exactly as it was, or absent if it was. The name of a
      closed pull request's deleted branch is not reused: on 2026-10-21 it
      was, and #49 was opened from #46's old name.

    Never force-pushes and never deletes a branch."""
    root = str(project.root)
    listed = subprocess.run(
        ["git", "-C", root, "ls-remote", "--heads", project.remote, f"refs/heads/{head}"],
        capture_output=True, text=True, check=False, env=env, timeout=60)
    if listed.returncode != 0:
        said = (listed.stderr or listed.stdout).replace(token, "***").strip()[:300]
        raise IntegrationError(f"{project.repo}'s branch {head} could not be "
                               f"read, so nothing was pushed: {said}", 502)
    remote_sha = (listed.stdout.split() or [""])[0]
    owner = project.repo.partition("/")[0]
    query = urllib.parse.urlencode({"head": f"{owner}:{head}", "state": "all"})
    _, pulls = api("GET", f"/repos/{project.repo}/pulls?{query}", token)
    pulls = pulls if isinstance(pulls, list) else []
    still_open = [p for p in pulls if p.get("state") == "open"]
    ended = [p for p in pulls if p.get("state") != "open"]
    forward = True
    if remote_sha:
        fetched = subprocess.run(
            ["git", "-C", root, "fetch", "--quiet", "--no-tags", project.remote,
             f"refs/heads/{head}"],
            capture_output=True, text=True, check=False, env=env, timeout=180)
        if fetched.returncode != 0:
            said = (fetched.stderr or fetched.stdout).replace(token, "***").strip()[:300]
            raise IntegrationError(f"{project.repo}'s branch {head} could not be "
                                   f"fetched, so nothing was pushed: {said}", 502)
        forward = subprocess.run(
            ["git", "-C", root, "merge-base", "--is-ancestor", remote_sha, sha],
            capture_output=True, check=False).returncode == 0
    if still_open:
        if forward:
            return head, {}
        number = still_open[0].get("number")
        return head, {"refused": "open_pr_diverged", "number": number,
                      "url": still_open[0].get("html_url"), "detail": (
            f"pull request #{number} from {head} is still open, and {branch} at "
            f"{sha[:10]} does not fast-forward its head at {remote_sha[:10]} "
            f"(the work was rebuilt on a newer {base}); nothing was pushed, "
            f"nothing here force-pushes, and an open pull request is not "
            f"replaced by a second one. Either rebuild the branch on top of "
            f"{remote_sha[:10]}, or have #{number} closed and land again, which "
            f"opens a fresh branch and pull request")}
    if forward and not ended:
        return head, {}
    old = ended[0] if ended else {}
    state = ("merged" if old.get("merged_at") else old.get("state")) if old else None
    fresh = fresh_head(project, branch, title, base, env, plain_names,
                       also={head, branch})
    subprocess.run(["git", "-C", root, "config", f"branch.{branch}.{PR_HEAD_KEY}", fresh],
                   capture_output=True, check=True)
    why = ("rebuilt" if not forward else state)
    return fresh, {"head": head, "head_sha": remote_sha or None, "number": old.get("number"),
                   "state": state, "why": why}


def _pr_job(project_name: str, branch: str):
    """``(project, base, sha)`` for a job's branch of a pull-request project,
    the commit read from the canonical checkout, or IntegrationError."""
    import loam_engineer as eng  # noqa: PLC0415
    project = eng.projects().get(project_name)
    if project is None or project.platform or \
            project.record.get("landing") != "pull_request":
        raise IntegrationError(f"{project_name} is not a project that lands by "
                               f"pull request", 400)
    if not JOB_BRANCH_RE.match(branch or ""):
        raise IntegrationError(f"only a job's branch (job/<id>) is pushed here, "
                               f"not {branch!r}", 400)
    base = project.record.get("default_branch")
    if not base or not project.repo or not project.remote:
        raise IntegrationError(f"{project_name}'s record has no default_branch, "
                               f"repo or remote", 409)
    found = subprocess.run(
        ["git", "-C", str(project.root), "rev-parse", "--verify", "--quiet",
         f"refs/heads/{branch}^{{commit}}"], capture_output=True, text=True, check=False)
    sha = found.stdout.strip()
    if found.returncode != 0 or not sha:
        raise IntegrationError(f"{project.root} has no branch {branch}", 409)
    import loam_commit_identity  # noqa: PLC0415
    bad = loam_commit_identity.malformed(project.record)
    if bad:
        raise IntegrationError(f"{project_name}: {bad}", 409)
    return project, base, sha


def _pr_token(project, branch: str, sha: str, title: str, body: str) -> str:
    """The token a pull-request project's branch is pushed with: the person's
    (``person_token``, which checks the commits and the text first) on a
    person-authored project, else an installation token for the one repo."""
    import loam_commit_identity  # noqa: PLC0415
    identity = loam_commit_identity.of(project.record)
    if identity:
        return person_token(project, branch, sha, identity, title, body)
    slot, found = route(load(), project.repo)
    inst = found["installation_id"]
    _, live = api("GET", f"/app/installations/{inst}", _jwt(slot))
    return _mint(slot, inst, {"repositories": [project.repo.partition("/")[2]],
                              "permissions": project_permissions(live)})["token"]


def update_pull_request(project_name: str, branch: str, number,
                        reply: str = "") -> dict:
    """The landing of a job whose brief names the pull request it updates
    (since 2026-10-09; the title form is lib/loam_pr_target.py): push the
    job's branch to that pull request's head branch, fast-forward only, then
    post ``reply`` on it as a comment, with the same token.

    Fast-forward only, checked twice: here, against the head GitHub reports
    and fetched from the remote, and by the remote itself, since the push is
    never forced. A head that is not an ancestor of the job's commit is
    answered ``{"pushed": False, "refused": "not_fast_forward"}`` with
    nothing pushed and nothing posted. A pull request that is not open, or
    whose head is on another repository, is refused before anything is
    fetched. The reply is posted only after the push; GitHub refusing it
    (403/404, the app or the user lacking a permission) is answered in
    ``reply`` with what to grant, never retried another way."""
    project, base, sha = _pr_job(project_name, branch)
    try:
        number = int(number)
    except (TypeError, ValueError):
        raise IntegrationError(f"{number!r} is not a pull request number", 400) from None
    reply = (reply or "").strip()
    token = _pr_token(project, branch, sha, "", reply)
    status, pr = api("GET", f"/repos/{project.repo}/pulls/{number}", token,
                     ok=(200, 404))
    if status == 404:
        raise IntegrationError(f"{project.repo} has no pull request #{number}", 409)
    if pr.get("state") != "open":
        raise IntegrationError(f"{project.repo}#{number} is {pr.get('state')}, "
                               f"not open; nothing was pushed", 409)
    head_info = pr.get("head") or {}
    head, before = head_info.get("ref") or "", head_info.get("sha") or ""
    if ((head_info.get("repo") or {}).get("full_name") or "").lower() != project.repo.lower():
        raise IntegrationError(f"{project.repo}#{number}'s head is on another "
                               f"repository; nothing was pushed", 409)
    if not head or head.lower() == base.lower():
        raise IntegrationError(f"refusing to push {branch} to {project.repo}'s "
                               f"default branch {base}", 409)
    env = git_token_env(token)
    root = str(project.root)
    fetched = subprocess.run(
        ["git", "-C", root, "fetch", "--quiet", "--no-tags", project.remote,
         f"refs/heads/{head}"],
        capture_output=True, text=True, check=False, env=env, timeout=180)
    if fetched.returncode != 0:
        said = (fetched.stderr or fetched.stdout).replace(token, "***").strip()[:400]
        raise IntegrationError(f"{project.repo}'s branch {head} could not be "
                               f"fetched, so nothing was pushed: {said}", 502)
    remote_head = subprocess.run(
        ["git", "-C", root, "rev-parse", "FETCH_HEAD^{commit}"],
        capture_output=True, text=True, check=False).stdout.strip()
    out = {"url": pr.get("html_url"), "number": number, "sha": sha,
           "branch": branch, "head": head, "base": base, "created": False,
           "updated": True, "before": remote_head or before, "after": None,
           "pushed": False, "reply": {"state": "none"}}
    for older in dict.fromkeys(c for c in (before, remote_head) if c):
        if subprocess.run(["git", "-C", root, "merge-base", "--is-ancestor",
                           older, sha], capture_output=True,
                          check=False).returncode != 0:
            out.update(refused="not_fast_forward", detail=(
                f"#{number}'s head {head} is at {older[:10]}, which is not an "
                f"ancestor of {branch} at {sha[:10]}; nothing was pushed, and "
                f"nothing here force-pushes"))
            out["reply"] = {"state": "not_posted",
                            "detail": "the push did not happen"} if reply else out["reply"]
            return out
    push = subprocess.run(
        ["git", "-C", root, "push", "--quiet", project.remote,
         f"{sha}:refs/heads/{head}"],
        capture_output=True, text=True, check=False, env=env, timeout=180)
    if push.returncode != 0:
        said = (push.stderr or push.stdout).replace(token, "***").strip()[:400]
        raise IntegrationError(f"the push of {branch} to {project.repo}'s "
                               f"{head} failed: {said}", 502)
    out.update(pushed=True, after=sha)
    if not reply:
        return out
    status, comment = api("POST", f"/repos/{project.repo}/issues/{number}/comments",
                          token, body={"body": reply}, ok=(201, 403, 404))
    if status == 201:
        out["reply"] = {"state": "posted", "url": comment.get("html_url"),
                        "id": comment.get("id")}
        return out
    slot, inst = route(load(), project.repo)
    app_slug = (slot.get("app") or {}).get("slug") or "the app"
    out["reply"] = {
        "state": "refused", "status": status,
        "detail": f"GitHub returned {status}: {comment.get('message')}",
        "grant": (f"Pull requests: Read and write (or Issues: Read and write) "
                  f"on the GitHub App `{app_slug}`, accepted by its installation "
                  f"on `{inst.get('account')}`, and the signed-in user able "
                  f"to comment on {project.repo}")}
    return out


def _slug(title: str) -> str:
    """A branch name from a title: lowercase words joined by hyphens, cut at
    a word boundary near ``PR_HEAD_MAX``."""
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    out = ""
    for w in words:
        if out and len(out) + 1 + len(w) > PR_HEAD_MAX:
            break
        out = f"{out}-{w}" if out else w[:PR_HEAD_MAX]
    return out or "change"


def pr_head(project, branch: str, title: str, base: str, env: dict) -> str:
    """The remote branch a person-authored project's job is pushed to: the
    one recorded for ``branch`` when there is one (a continuation, so the same
    pull request), else ``_slug(title)``, with ``-2``, ``-3``... until no
    branch on the remote, no other job's record and not ``base`` has it.
    Never an existing branch someone else made, so never the default."""
    had = recorded_head(project, branch)
    if had:
        return had
    head = fresh_head(project, branch, title, base, env, plain_names=True)
    subprocess.run(["git", "-C", str(project.root), "config",
                    f"branch.{branch}.{PR_HEAD_KEY}", head],
                   capture_output=True, check=True)
    return head


def recorded_head(project, branch: str) -> str:
    """The remote branch recorded for ``branch`` by an earlier landing, or ""."""
    return subprocess.run(
        ["git", "-C", str(project.root), "config", "--get",
         f"branch.{branch}.{PR_HEAD_KEY}"],
        capture_output=True, text=True, check=False).stdout.strip()


def fresh_head(project, branch: str, title: str, base: str, env: dict,
               plain_names: bool, also: set[str] = frozenset()) -> str:
    """A remote branch name nothing has: ``_slug(title)`` on a person-authored
    project (``plain_names``), else ``branch`` itself, then ``-2``, ``-3``...
    until no branch on the remote, no job's record, not ``base`` and nothing
    in ``also`` has it. Recorded by the caller."""
    root = str(project.root)
    listed = subprocess.run(
        ["git", "-C", root, "ls-remote", "--heads", project.remote],
        capture_output=True, text=True, check=False, env=env, timeout=60)
    if listed.returncode != 0:
        raise IntegrationError(f"the branches of {project.repo} could not be "
                               f"listed, so nothing was pushed", 502)
    taken = {line.split("refs/heads/", 1)[1].lower()
             for line in listed.stdout.splitlines() if "refs/heads/" in line}
    recorded = subprocess.run(
        ["git", "-C", root, "config", "--get-regexp",
         rf"^branch\..*\.{PR_HEAD_KEY}$"],
        capture_output=True, text=True, check=False).stdout.split("\n")
    taken |= {line.split(" ", 1)[1].lower() for line in recorded if " " in line}
    taken |= {base.lower()} | {a.lower() for a in also}
    stem = _slug(title) if plain_names else branch
    head, n = stem, 1
    while head.lower() in taken:
        n += 1
        head = f"{stem}-{n}"
    if subprocess.run(["git", "check-ref-format", "--branch", head],
                      capture_output=True, check=False).returncode != 0:
        raise IntegrationError(f"{head!r} is not a branch name", 409)
    return head


def person_token(project, branch: str, sha: str, identity: dict, title: str,
                 body: str) -> str:
    """The token a person-authored project's branch is pushed and its pull
    request opened with: the user-to-server token of the app that reaches
    the repository, which GitHub attributes to the user, not the app's bot.
    Only after the branch is proved theirs (lib/loam_commit_identity.problems),
    here, where nothing the job runs can reach, and only if the token is
    ``github_login``'s."""
    import loam_commit_identity  # noqa: PLC0415
    base = "refs/heads/main"
    try:
        wrong = loam_commit_identity.problems(project.root, base, sha, identity)
    except subprocess.SubprocessError as exc:
        raise IntegrationError(f"the commits of {branch} could not be read: {exc}", 409)
    wrong += [f"the pull request carries {w}"
              for w in loam_commit_identity.text_problems(f"{title}\n{body}")]
    if wrong:
        raise IntegrationError(f"{branch} is not {identity['name']}'s alone, so "
                               f"nothing was pushed: " + "; ".join(wrong[:6]), 409)
    data = load()
    owner = project.repo.partition("/")[0].lower()
    route(data, project.repo)  # the same refusals as an installation token
    token = user_token(org=owner if owner in org_apps(data) else None)
    _, user = api("GET", "/user", token)
    if (user.get("login") or "").lower() != identity["github_login"].lower():
        raise IntegrationError(
            f"the user token for {project.repo} is {user.get('login')!r}'s, not "
            f"{identity['github_login']}'s; nothing was pushed", 409)
    return token


def add_repo_to_installation(repo_id: int, full_name: str) -> str:
    """After provisioning a repo, make sure the installation reaches it, and
    prove it: ``"added"`` (selected repositories; this call added it) or
    ``"covered"`` (all repositories). Either way a token scoped to the repo
    has just been minted, which GitHub refuses (422) for a repository the
    installation does not reach, so the answer is never a no-op's word.
    Raises ``IntegrationError`` if it cannot be proved.

    The selection is read live with the app JWT, not from the store: the
    store's copy is from the install, and he can change it on GitHub
    without a redirect reaching us. A changed value is written back."""
    slot, found = route(load(), full_name)
    inst_id, org = found["installation_id"], slot.get("org")
    _, live = api("GET", f"/app/installations/{inst_id}", _jwt(slot))
    selection = live.get("repository_selection")
    with locked():
        data = load()
        for inst in installations(_slot(data, org)):
            if inst.get("installation_id") == inst_id \
                    and inst.get("repository_selection") != selection:
                inst["repository_selection"] = selection
                save(data)
    outcome = "covered" if selection == "all" else "added"
    if outcome == "added":
        api("PUT", f"/user/installations/{inst_id}/repositories/{repo_id}",
            user_token(org=org), ok=(204,))
    last: IntegrationError | None = None
    for attempt in range(PROVE_TRIES):
        try:
            installation_token(full_name)
            return outcome
        except IntegrationError as exc:   # a repo seconds old may not be visible yet
            last = exc
            if attempt + 1 < PROVE_TRIES:
                time.sleep(PROVE_WAIT_S)
    raise IntegrationError(
        f"the installation ({selection} repositories) does not reach "
        f"{full_name}: minting a token for it failed: {last}", 502)


# ---------------------------------------------------------------------------
# State and health
# ---------------------------------------------------------------------------

def status(data: dict | None = None) -> str:
    data = load() if data is None else data
    if not data.get("app"):
        return "unregistered"
    if (data.get("health") or {}).get("last_error"):
        return "unhealthy"
    if not data.get("installation"):
        return "registered"
    if not data.get("user"):
        return "installed"
    return "authorized"


def public_view(data: dict | None = None) -> dict:
    """What GET /v1/integrations/github answers. No token, key or secret."""
    data = load() if data is None else data
    app, inst, user = data.get("app"), data.get("installation"), data.get("user")
    health = data.get("health") or {}
    return {
        "status": status(data),
        "registered": bool(app),
        "app": {"id": app["id"], "slug": app["slug"], "url": app.get("html_url"),
                "registered_at": app.get("registered_at")} if app else None,
        "installed": {k: inst.get(k) for k in
                      ("installation_id", "account", "repository_selection",
                       "installed_at")} if inst else None,
        # Every installation recorded, the first included; `installed` stays
        # the first alone, which is what the page drew before there were two.
        "installations": [{k: i.get(k) for k in
                           ("installation_id", "account", "repository_selection",
                            "installed_at")} for i in installations(data)],
        "user": {k: user.get(k) for k in
                 ("login", "token_expires_at", "refresh_expires_at",
                  "authorized_at")} if user else None,
        "health": {"last_ok_at": health.get("last_ok_at"),
                   "last_error": health.get("last_error")},
        "uses": list(USES),
        # Apps owned by an organization, each answering for that org alone.
        # Its health is its own: an org removing its app never makes the
        # first app's card unhealthy.
        "org_apps": [_org_view(own) for _, own in sorted(org_apps(data).items())],
    }


def _org_view(own: dict) -> dict:
    app, inst, user = own.get("app") or {}, own.get("installation"), own.get("user")
    health = own.get("health") or {}
    return {
        "org": own.get("org"),
        "status": ("unhealthy" if health.get("last_error") else
                   "registered" if not inst else
                   "installed" if not user else "authorized"),
        "app": {"id": app.get("id"), "slug": app.get("slug"), "url": app.get("html_url"),
                "registered_at": app.get("registered_at")},
        "installed": {k: inst.get(k) for k in
                      ("installation_id", "account", "repository_selection",
                       "installed_at")} if inst else None,
        "user": {k: user.get(k) for k in
                 ("login", "token_expires_at", "refresh_expires_at",
                  "authorized_at")} if user else None,
        "health": {"last_ok_at": health.get("last_ok_at"),
                   "last_error": health.get("last_error")},
    }


def _check_slot(slot: dict, org: str | None, force_refresh: bool,
                only: int | None = None) -> None:
    """One app: its installations with its JWT, its user with its token.
    Each installation checked gets its own health (2026-11-13, one account
    refreshed at a time); the app's health is the first error among all its
    installations' own, or the user's. ``only`` checks that installation
    alone, and the user, whose token all of one app's installations share."""
    errors: dict[int, str] = {}
    user_error = None
    name = f"{org}'s app" if org else "the Loam app"
    checked = [i for i in installations(slot)
               if only is None or i.get("installation_id") == only]
    try:
        jwt = _jwt(slot)
    except IntegrationError as exc:
        jwt, user_error = None, str(exc)
    for inst in checked if jwt else []:
        try:
            status_code, _ = api("GET", f"/app/installations/{inst['installation_id']}",
                                 jwt, ok=(200, 404))
            if status_code == 404:
                errors[inst["installation_id"]] = (
                    f"GitHub says {name}'s installation is gone "
                    f"(on {inst.get('account')}); install it again from the app")
        except IntegrationError as exc:
            errors[inst["installation_id"]] = str(exc)
    if user_error is None and slot.get("user"):
        try:
            api("GET", "/user", user_token(force_refresh=force_refresh, org=org))
        except IntegrationError as exc:
            user_error = str(exc)
    at = iso(now())
    with locked():
        data = load()
        try:
            fresh = _slot(data, org)
        except IntegrationError:      # forgotten while we were asking
            return
        ids = {i.get("installation_id") for i in checked}
        for inst in installations(fresh):
            if inst.get("installation_id") not in ids:
                continue
            own = inst.setdefault("health", {})
            own["checked_at"] = at
            error = errors.get(inst["installation_id"]) or user_error
            if error:
                own["last_error"] = error
            else:
                own["last_ok_at"] = at
                own["last_error"] = None
        # The app's health: the first error any of its installations holds
        # now (each its own last check), or the user's, or none.
        first = user_error or next(
            ((i.get("health") or {}).get("last_error") for i in installations(fresh)
             if (i.get("health") or {}).get("last_error")), None)
        health = fresh.setdefault("health", {})
        health["checked_at"] = at
        if first:
            health["last_error"] = first
        else:
            health["last_ok_at"] = at
            health["last_error"] = None
        save(data)


def check(force_refresh: bool = False) -> dict:
    """One call each, per app: the installation with an app JWT, the user
    with the user token. Records the outcome as health; returns the public
    view."""
    data = load()
    if data.get("app"):
        _check_slot(data, None, force_refresh)
    for org, own in org_apps(data).items():
        if own.get("app"):
            _check_slot(own, org, force_refresh)
    return public_view()


# ---------------------------------------------------------------------------
# Accounts (2026-11-13): refresh one, disconnect one, add another
# ---------------------------------------------------------------------------
#
# An account is a GitHub login Loam reaches. Three kinds, from the store's
# own shape: `personal`, the account the Loam app was connected from (its
# first installation, whose authorization every installation of that app
# shares); `installation`, another account the same app was installed on
# (`added_installations`); and `org`, an organization with an app of its own
# (`org_apps`). Settings' refresh and disconnect acted on all of them at once
# until this; each route below names one.

def _account_rows(data: dict) -> list[dict]:
    rows = []
    app = data.get("app")
    if app:
        user = data.get("user")
        insts = installations(data)
        slot_health = data.get("health") or {}
        if not insts:
            rows.append({"account": app.get("owner"), "kind": "personal",
                         "app": app, "installation": None, "user": user,
                         "health": slot_health, "org": None, "slot": None,
                         "also": []})
        for i, inst in enumerate(insts):
            acct = inst.get("account")
            personal = i == 0
            rows.append({"account": acct,
                         "kind": "personal" if personal else "installation",
                         "app": app, "installation": inst, "user": user,
                         "health": inst.get("health") or slot_health,
                         "org": None, "slot": None,
                         # The personal account is the app's own: forgetting it
                         # forgets the app, and every installation goes with it.
                         "also": [x.get("account") for x in insts[1:]] if personal
                         else []})
    for org, own in sorted(org_apps(data).items()):
        rows.append({"account": own.get("org") or org, "kind": "org",
                     "app": own.get("app") or {}, "installation": own.get("installation"),
                     "user": own.get("user"), "health": own.get("health") or {},
                     "org": org, "slot": own, "also": []})
    return rows


def _account_view(row: dict) -> dict:
    app, inst, user, health = row["app"] or {}, row["installation"], row["user"], \
        row["health"] or {}
    status_ = ("unhealthy" if health.get("last_error") else
               "registered" if not inst else
               "installed" if not user else "authorized")
    acct = row["account"]
    if row["kind"] == "org":
        manage = f"{WEB}/organizations/{acct}/settings/installations"
    elif row["kind"] == "personal":
        manage = f"{WEB}/settings/installations"
    else:
        # A user or an organization, which the store does not say: the app's
        # own page lists where it is installed and opens each one.
        manage = (f"{WEB}/apps/{app.get('slug')}/installations/new"
                  if app.get("slug") else f"{WEB}/settings/installations")
    return {
        "account": acct,
        "kind": row["kind"],
        "status": status_,
        "app": {"id": app.get("id"), "slug": app.get("slug"),
                "url": app.get("html_url"), "owner": app.get("owner"),
                "registered_at": app.get("registered_at")} if app else None,
        "installation": {k: inst.get(k) for k in
                         ("installation_id", "repository_selection",
                          "installed_at")} if inst else None,
        "user": {k: user.get(k) for k in
                 ("login", "token_expires_at", "refresh_expires_at",
                  "authorized_at")} if user else None,
        "health": {k: health.get(k) for k in ("last_ok_at", "last_error",
                                              "checked_at")},
        "disconnect_also": [a for a in row["also"] if a],
        "manage_url": manage,
    }


def accounts(data: dict | None = None) -> dict:
    """``GET /v1/integrations/github/accounts``: every account, in the
    order personal, its other installations, then each organization's app."""
    data = load() if data is None else data
    return {"accounts": [_account_view(r) for r in _account_rows(data)]}


def _account(data: dict, login: str) -> dict:
    if not isinstance(login, str) or not login.strip():
        raise IntegrationError("name the account: its GitHub login")
    for row in _account_rows(data):
        if (row["account"] or "").lower() == login.strip().lower():
            return row
    raise IntegrationError(f"no GitHub account {login!r} is connected here", 404)


def check_account(login: str) -> dict:
    """``POST /v1/integrations/github/accounts/<login>/refresh``: that
    account alone. Its installation is checked with its app's JWT and its
    authorization's token refreshed now (an installation of the Loam app
    shares the personal account's, so that one refreshes for both); its
    health is recorded on it. The answer is the account and the whole list."""
    row = _account(load(), login)
    inst_id = (row["installation"] or {}).get("installation_id")
    slot = row["slot"] if row["kind"] == "org" else load()
    _check_slot(slot, row["org"], True, only=inst_id)
    data = load()
    return {"account": _account_view(_account(data, login)), **accounts(data)}


def forget_account(login: str) -> dict:
    """``DELETE /v1/integrations/github/accounts/<login>``: that account
    alone. An organization's app goes whole (it answers for that org only);
    another installation of the Loam app goes, leaving the app and the
    others; the personal account is the Loam app's own, so the app goes with
    it, and with it every installation it holds (named in
    ``disconnect_also`` before, and in ``forgot`` after). Organizations'
    apps are never touched by another account's disconnect. On GitHub the
    installation and the authorization are his to remove: the answer names
    the pages."""
    with locked():
        data = load()
        row = _account(data, login)
        acct = row["account"]
        slug = (row["app"] or {}).get("slug")
        forgot = [acct]
        if row["kind"] == "org":
            data.get("org_apps", {}).pop(row["org"], None)
            pages = {"uninstall_url": f"{WEB}/organizations/{acct}/settings/installations",
                     "revoke_url": f"{WEB}/settings/apps/authorizations",
                     "delete_app_url": f"{WEB}/organizations/{acct}/settings/apps/{slug}/advanced"
                     if slug else None}
        elif row["kind"] == "installation":
            inst_id = row["installation"]["installation_id"]
            data["added_installations"] = [
                i for i in data.get("added_installations") or []
                if i.get("installation_id") != inst_id]
            pages = {"uninstall_url": _account_view(row)["manage_url"],
                     "revoke_url": None, "delete_app_url": None}
        else:
            forgot += row["also"]
            for key in ("app", "installation", "added_installations", "user"):
                data.pop(key, None)
            data["health"] = {}
            pages = {"uninstall_url": f"{WEB}/settings/installations",
                     "revoke_url": f"{WEB}/settings/apps/authorizations",
                     "delete_app_url": f"{WEB}/settings/apps/{slug}/advanced"
                     if slug else None}
        if not data.get("app") and not org_apps(data):
            STORE_PATH.unlink(missing_ok=True)
            data = {}
        else:
            save(data)
    return {"forgotten": True, "account": acct, "kind": row["kind"],
            "forgot": [a for a in forgot if a], **pages, **accounts(data)}


def add_account(org: str | None = None, own_app: bool = False) -> dict:
    """``POST /v1/integrations/github/accounts``: where to go to add one.
    With nothing, the Loam app's install screen, where GitHub lets him pick
    any account he can install on (it comes back as an installation, or as
    the personal account when there is none yet); with ``org`` and
    ``own_app``, a new app owned by that organization (the manifest to post,
    as ``POST /manifest`` answers); with ``org`` alone, that organization's
    own app's install screen, which must be registered already. No Loam app
    yet: the manifest to register one."""
    data = load()
    if org and own_app:
        return {"step": "register", **start_manifest(None, org)}
    if org:
        return {"step": "install", **start_install(org)}
    if not data.get("app"):
        return {"step": "register", **start_manifest(None, None)}
    return {"step": "install", **start_install(None)}


def nightly_warnings() -> list[str]:
    """What the nightly check says in comms.md, or nothing. Two cases only:
    the refresh token is within fourteen days of its expiry, or GitHub says
    the installation is gone. Unregistered is silence: not connecting is a
    choice, not a fault."""
    view = check()
    out = []
    apps = ([(None, view)] if view["registered"] else []) + \
        [(own["org"], own) for own in view["org_apps"]]
    for org, part in apps:
        error = part["health"]["last_error"] or ""
        if "installation is gone" in error:
            out.append(error)
        refresh = parse_iso((part["user"] or {}).get("refresh_expires_at"))
        if refresh is not None and refresh - now() < REFRESH_WARN_DAYS * 86400:
            days = max(0, int((refresh - now()) // 86400))
            out.append(f"the GitHub authorization{f' of the {org} app' if org else ''} "
                       f"expires in {days} days "
                       f"({(part['user'] or {}).get('refresh_expires_at')}); "
                       + (f"install the {org} app again" if org else
                          "reconnect GitHub from the app"))
    return out
