"""The marketplace: one list of plugins, read from a URL, installed by link.

Added 2026-11-17. Decided 2026-11-13: one public GitHub repository holds a
list file; builders host their own work and ask by pull request to be listed;
a maintainer reviews each by hand; Settings › Plugins reads the list and
installs from it through the same two calls as any link
(lib/loam_plugins.install); installing from any link still works.

**The list** is ``list.json`` at ``marketplace.list`` in loam.toml (default
``DEFAULT_LIST``; an empty string turns the section off). Its format, and the
repository it comes from, are in docs/plugins.md, "The marketplace";
``docs/marketplace/`` in this repository is that repository's content.

**Reading it** never errors at the page. A list that is not there (404: not
published yet) or that lists nothing is ``state: "empty"``; one that cannot
be reached or read is ``state: "unreachable"`` with a sentence; an entry that
breaks a rule is left out and counted in ``skipped`` with its reason, and the
rest are drawn. The answer is kept for ``TTL`` seconds in the process that
asked (the broker), so opening Settings does not fetch every time.

**Sharing by link** (``share``): the plugin's folder pushed from the owner's
plugins folder to a repository the owner made, at the tag of its version,
through their connected GitHub; the answer is the link to install it by.

**Asking to be listed** (``listing_request``): the entry for a plugin at a
tagged link, read from the plugin itself (the first, writing-nothing call of
an install), and a link to GitHub's new-file page in the marketplace
repository with that entry filled in as ``listings/<id>.json``. The owner
opens it signed in to their own account; GitHub makes the fork and the pull
request when they press Propose. Nothing is sent from the box.

The tests are tests/test_marketplace.py.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import loam_config as lc

DEFAULT_REPO = "loamhq/marketplace"
DEFAULT_LIST = f"https://raw.githubusercontent.com/{DEFAULT_REPO}/main/list.json"
TTL = 600
TIMEOUT = 8
MAX_BYTES = 1 << 20
FORMAT = 1

ID = re.compile(r"^[a-z][a-z0-9-]*$")
KINDS = ("theme", "extension", "app", "agent", "skill")
LIMITS = {"name": 60, "author": 60, "description": 200}

_cache: dict = {}
_lock = threading.Lock()


def list_url() -> str:
    value = lc.get("marketplace.list")
    return DEFAULT_LIST if value is None else str(value).strip()


def repo() -> str:
    value = str(lc.get("marketplace.repo") or DEFAULT_REPO)
    if not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", value):
        raise ValueError(f"marketplace.repo {value!r} is not owner/name")
    return value


# ---------------------------------------------------------------------------
# The format
# ---------------------------------------------------------------------------

def check_entry(entry) -> tuple[dict | None, str | None]:
    """One listing, made safe to draw, or the reason it is left out."""
    import loam_plugins  # noqa: PLC0415
    if not isinstance(entry, dict):
        return None, "an entry is an object"
    pid = entry.get("id")
    if not isinstance(pid, str) or not ID.match(pid):
        return None, f"`id` {pid!r} is not a plugin id (lower case, digits, dashes)"
    out = {"id": pid}
    for field, required in (("name", True), ("author", True), ("description", False)):
        value = entry.get(field)
        if value is None and not required:
            continue
        if not isinstance(value, str) or not value.strip():
            return None, f"{pid}: `{field}` is a line of text"
        value = " ".join(value.split())
        if len(value) > LIMITS[field]:
            return None, f"{pid}: `{field}` is longer than {LIMITS[field]} characters"
        out[field] = value
    kind = entry.get("kind")
    kind = [kind] if isinstance(kind, str) else kind
    if not isinstance(kind, list) or not kind or any(k not in KINDS for k in kind):
        return None, f"{pid}: `kind` is one or more of {', '.join(KINDS)}"
    out["kind"] = list(dict.fromkeys(kind))
    link = entry.get("link")
    if not isinstance(link, str):
        return None, f"{pid}: `link` is the plugin's GitHub link at a tag or a commit"
    try:
        src = loam_plugins.parse_source({"url": link})
    except loam_plugins.PluginError as exc:
        return None, f"{pid}: {exc.message}"
    out.update(link=link.strip(), repo=src["repo"], ref=src["ref"], path=src["path"])
    home = entry.get("homepage")
    if isinstance(home, str) and home.startswith("https://") and len(home) < 300:
        out["homepage"] = home
    if isinstance(entry.get("version"), str) and loam_plugins.VERSION.match(entry["version"]):
        out["version"] = entry["version"]
    return out, None


def parse(text: str) -> tuple[list[dict], list[str]]:
    """``list.json``'s text into its entries, sorted by name, and the
    reasons for any left out. A list that is not one raises ValueError."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"the list is not JSON ({exc})") from None
    if not isinstance(data, dict) or not isinstance(data.get("plugins", []), list):
        raise ValueError('the list is an object with a "plugins" array')
    if data.get("version", FORMAT) != FORMAT:
        raise ValueError(f"the list is format {data.get('version')!r}; this Loam reads "
                         f"format {FORMAT}")
    entries, skipped, seen = [], [], set()
    for raw in data.get("plugins", []):
        entry, why = check_entry(raw)
        if why:
            skipped.append(why)
        elif entry["id"] in seen:
            skipped.append(f"{entry['id']}: listed twice")
        else:
            seen.add(entry["id"])
            entries.append(entry)
    entries.sort(key=lambda e: (e["name"].lower(), e["id"]))
    return entries, skipped


# ---------------------------------------------------------------------------
# Reading it
# ---------------------------------------------------------------------------

def fetch(url: str) -> tuple[str, str | None]:
    """``(state, text)``: ``listed`` with the text, ``empty`` when there is
    no list there yet, or ``unreachable`` with the reason as the text."""
    if not url.startswith(("https://", "http://127.0.0.1", "http://localhost", "file://")):
        return "unreachable", "the marketplace's address is not an https link"
    req = urllib.request.Request(url, headers={"User-Agent": "loam-marketplace",
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 -- checked above
            raw = resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return "empty", None
        return "unreachable", f"the list's host answered {exc.code}"
    except FileNotFoundError:
        return "empty", None
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        return "unreachable", f"the list could not be fetched ({reason})"
    if len(raw) > MAX_BYTES:
        return "unreachable", "the list is larger than 1 MiB"
    return "listed", raw.decode("utf-8", errors="replace")


def read(url: str | None = None, refresh: bool = False) -> dict:
    """The list as the page draws it, before it is matched to what is
    installed. Cached for ``TTL`` seconds per URL."""
    url = list_url() if url is None else url
    if not url:
        return {"url": None, "state": "off", "plugins": [], "skipped": [],
                "checked_at": None, "message": None}
    with _lock:
        hit = _cache.get(url)
        if hit and not refresh and time.time() - hit["_at"] < TTL:
            return {k: v for k, v in hit.items() if k != "_at"}
    state, text = fetch(url)
    out = {"url": url, "state": state, "plugins": [], "skipped": [], "message": None,
           "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if state == "listed":
        try:
            out["plugins"], out["skipped"] = parse(text or "")
            if not out["plugins"]:
                out["state"] = "empty"
        except ValueError as exc:
            out.update(state="unreachable", message=str(exc))
    elif state == "unreachable":
        out["message"] = text
    with _lock:
        _cache[url] = {**out, "_at": time.time()}
    return out


def listing(refresh: bool = False) -> dict:
    """``GET /v1/marketplace``: the list, each entry marked with whether a
    plugin of its id is installed here and at which version."""
    out = read(refresh=refresh)
    try:
        import loam_plugins  # noqa: PLC0415
        rows = [r for r in loam_plugins.listing().get("plugins", []) if r.get("effective")]
    except Exception:  # noqa: BLE001 -- the list is still worth drawing
        rows = []
    by_id = {r["id"]: r for r in rows if r.get("id")}
    plugins = []
    for entry in out["plugins"]:
        row = by_id.get(entry["id"])
        src = (row or {}).get("source") or {}
        plugins.append({**entry, "installed": bool(row),
                        "installed_version": (row or {}).get("version"),
                        "installed_from": src.get("repo") if src.get("kind") == "link"
                        else src.get("kind")})
    return {**out, "plugins": plugins, "repo": repo()}


# ---------------------------------------------------------------------------
# Asking to be listed
# ---------------------------------------------------------------------------

def entry_for(preview: dict, link: str, description: str | None) -> dict:
    """The listing for a plugin from its install preview (lib/loam_plugins
    ``install`` without ``confirm``). The marketplace asks for a
    description, so this does too."""
    if not isinstance(description, str) or not description.strip():
        raise ValueError("a listing says in one sentence what the plugin does "
                         "(`--description`)")
    entry = {"id": preview["id"], "name": preview.get("name") or preview["id"],
             "author": preview.get("author") or "", "kind": preview.get("kind") or [],
             "link": link}
    if preview.get("version"):
        entry["version"] = preview["version"]
    if description:
        entry["description"] = description
    checked, why = check_entry(entry)
    if why:
        raise ValueError(why)
    keep = ("id", "name", "description", "author", "kind", "version", "link", "homepage")
    return {k: entry[k] for k in keep if k in entry and k in checked}


def request_url(entry: dict, repository: str | None = None) -> str:
    """GitHub's new-file page in the marketplace repository with the entry
    filled in as ``listings/<id>.json``. For someone who cannot write to the
    repository GitHub forks it and opens a pull request."""
    text = json.dumps(entry, indent=2, ensure_ascii=False) + "\n"
    query = urllib.parse.urlencode({"filename": f"listings/{entry['id']}.json",
                                    "value": text}, quote_via=urllib.parse.quote)
    return f"https://github.com/{repository or repo()}/new/main?{query}"


def listing_request(link: str, description: str | None = None, by: str | None = None) -> dict:
    """The entry for the plugin at ``link`` and the link that proposes it."""
    import loam_plugins  # noqa: PLC0415
    # The first call of an install: it fetches and reads, writes nothing,
    # and refuses a branch (a listing must not move under its readers).
    preview = loam_plugins.install({"url": link}, by)
    if preview.get("installed"):
        raise ValueError("the link was installed, not read; this is a bug")
    entry = entry_for(preview, link.strip(), description)
    return {"entry": entry, "url": request_url(entry), "repo": repo(),
            "declaration": preview.get("declaration"),
            "commit": (preview.get("source") or {}).get("commit")}


# ---------------------------------------------------------------------------
# Sharing a plugin by link
# ---------------------------------------------------------------------------
#
# A plugin built here lives in the owner's plugins folder. To share it, it
# goes into a GitHub repository of the owner's, at a tag named by its version,
# as a folder named by its id: the link is then
# https://github.com/<owner>/<name>/tree/v<version>/<id>, which anyone can
# install (docs/plugins.md, "Install from a link"). The repository is the
# owner's to make, on github.com, because whether it is public is their
# direct yes; the push is the integration's (the owner's connected GitHub,
# lib/loam_github.helper_env: an installation token minted per push, never
# written down). Nothing else changes in the repository but that folder.

class ShareError(Exception):
    pass


def _share_git(args: list[str], cwd, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=180)


def _share_base() -> str:
    return (os.environ.get("LOAM_SHARE_GIT_BASE") or "https://github.com/").rstrip("/") + "/"


def is_public(repository: str) -> bool | None:
    """Whether GitHub shows the repository to a stranger: True, False, or
    None when GitHub could not be asked."""
    req = urllib.request.Request(f"https://api.github.com/repos/{repository}",
                                 headers={"User-Agent": "loam-marketplace"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT):  # noqa: S310 -- a fixed https host
            return True
    except urllib.error.HTTPError as exc:
        return False if exc.code == 404 else None
    except (urllib.error.URLError, OSError, TimeoutError):
        return None


def share(pid: str, repository: str) -> dict:
    """Push the plugin ``pid`` from the owner's plugins folder to
    ``repository`` (owner/name, made by the owner) as ``<id>/`` at the tag
    ``v<version>``, and answer its link."""
    import loam_github  # noqa: PLC0415
    import loam_plugins  # noqa: PLC0415
    if not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", repository or ""):
        raise ShareError(f"{repository!r} is not a repository: owner/name")
    folder = loam_plugins.yours_root() / pid
    if not ID.match(pid or "") or not folder.is_dir():
        raise ShareError(f"there is no plugin {pid!r} in the plugins folder "
                         f"({loam_plugins.yours_root()})")
    try:
        manifest = loam_plugins.read_folder(folder, pid)["manifest"]
    except loam_plugins.ManifestError as exc:
        raise ShareError(f"{pid} is not a plugin yet: {exc}") from None
    version = manifest["version"]
    tag = f"v{version}"
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    if not os.environ.get("LOAM_SHARE_GIT_BASE"):
        helper = loam_github.helper_env(repository)
        if not helper:
            raise ShareError("GitHub is not connected on this install: the owner "
                             "connects it in Settings › Integrations, then this "
                             "can push to a repository on their account")
        env.update(helper)
    remote = f"{_share_base()}{repository}.git"
    work = Path(tempfile.mkdtemp(prefix="loam-share-"))
    try:
        got = _share_git(["clone", "--quiet", remote, str(work / "repo")], None, env)
        if got.returncode:
            raise ShareError(f"{repository} could not be read: "
                             f"{(got.stderr or got.stdout).strip()[-300:]} (make it on "
                             f"github.com first, on the account GitHub is connected with)")
        repo = work / "repo"
        tags = _share_git(["ls-remote", "--tags", "origin", f"refs/tags/{tag}"], repo, env)
        if tags.stdout.strip():
            raise ShareError(f"{repository} already has the tag {tag}: raise the "
                             f"plugin's version in its PLUGIN.md to share a new one")
        if not _share_git(["rev-parse", "-q", "--verify", "HEAD"], repo, env).returncode == 0:
            _share_git(["checkout", "-q", "-b", "main"], repo, env)
        target = repo / pid
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(folder, target, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        ident = ["-c", "user.name=" + (manifest.get("author") or "Loam"),
                 "-c", "user.email=loam@users.noreply.github.com"]
        _share_git(["add", "-A", pid], repo, env)
        if _share_git(["diff", "--cached", "--quiet"], repo, env).returncode:
            done = _share_git([*ident, "commit", "-q", "-m",
                               f"{manifest.get('name') or pid} {version}"], repo, env)
            if done.returncode:
                raise ShareError(f"the commit failed: {done.stderr.strip()[-300:]}")
        _share_git([*ident, "tag", "-a", tag, "-m", f"{manifest.get('name') or pid} {version}"],
                   repo, env)
        branch = _share_git(["symbolic-ref", "--short", "HEAD"], repo, env).stdout.strip() or "main"
        pushed = _share_git(["push", "--quiet", "origin", f"HEAD:refs/heads/{branch}",
                             f"refs/tags/{tag}"], repo, env)
        if pushed.returncode:
            raise ShareError(f"the push to {repository} failed: "
                             f"{(pushed.stderr or pushed.stdout).strip()[-300:]}")
        commit = _share_git(["rev-parse", "HEAD"], repo, env).stdout.strip()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    public = None if os.environ.get("LOAM_SHARE_GIT_BASE") else is_public(repository)
    return {"id": pid, "version": version, "repo": repository, "tag": tag, "commit": commit,
            "link": f"https://github.com/{repository}/tree/{tag}/{pid}", "public": public}
