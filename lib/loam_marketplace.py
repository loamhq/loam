"""The marketplace: one list of plugins, read from a URL, installed by link.

Added 2026-11-17. Decided 2026-11-13: one public GitHub repository holds a
list file; builders host their own work and ask by pull request to be listed;
a maintainer reviews each by hand; Settings › Market (Settings › Plugins
until 2026-11-18) reads the list and
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

**A listing's own page** (``details``, 2026-11-18, Settings › Market): what
the plugin at the listing's link says of itself, read the way an install's
first call reads it (fetched at its tag, PLUGIN.md read, nothing written):
its declaration in full, its version, author and guide, and for a theme its
token CSS and options, so the Market can draw its swatch and let the owner
try it before getting it. Its icon, its cover (the listing's ``image``, else
a theme's own cover) and its theme.css are kept in this process for
``file``. Kept ``DETAILS_TTL`` seconds per link: a listing names a tag, so
what it holds does not change under a reader.

**Two optional fields** (2026-11-18): ``featured: true`` makes a listing
the Market's lead story; ``image`` is a picture inside the plugin's folder
(the same rule as a theme's cover) drawn as that story's cover.

**Finding things, for an agent** (``search``, 2026-11-18): the listings
that answer plain words, ranked by the Market page's own search (run in
Node from app/public/market.js, so there is one matching rule), each with
the line that shows its card in a reply (``card_line``).

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
DETAILS_TTL = 3600
TIMEOUT = 8
MAX_BYTES = 1 << 20
FORMAT = 1

ID = re.compile(r"^[a-z][a-z0-9-]*$")
KINDS = ("theme", "extension", "app", "agent", "skill")
LIMITS = {"name": 60, "author": 60, "description": 200}

_cache: dict = {}
_lock = threading.Lock()
# A listing's details, by link: {"at", "body", "files": {name: (bytes, type)}}.
_details: dict = {}
_details_locks: dict[str, threading.Lock] = {}
ICON_MAX = 512 * 1024
IMAGE_MAX = 3 * 1024 * 1024
THEME_MAX = 128 * 1024
GUIDE_MAX = 8000


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
    # The Market's lead story and its cover (2026-11-18): optional; a value
    # that is not one is left out, never the listing.
    if entry.get("featured") is True:
        out["featured"] = True
    image = entry.get("image")
    if isinstance(image, str) and loam_plugins.IMAGE.match(image) and len(image) < 200:
        out["image"] = image
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
# A listing's own page
# ---------------------------------------------------------------------------

class NotListed(LookupError):
    pass


def _entry(pid: str) -> dict:
    if not isinstance(pid, str) or not ID.match(pid):
        raise NotListed(f"{pid!r} is not a plugin id")
    for entry in listing().get("plugins", []):
        if entry["id"] == pid:
            return entry
    raise NotListed(f"the marketplace lists no {pid!r}")


def _picture(folder: Path, rel, pattern, limit: int):
    """A picture inside the plugin's folder, as (bytes, type), or None."""
    import loam_plugins  # noqa: PLC0415
    if not isinstance(rel, str) or not pattern.match(rel):
        return None
    path = (folder / rel)
    try:
        path.resolve().relative_to(folder.resolve())
    except ValueError:
        return None
    if not path.is_file() or path.is_symlink() or path.stat().st_size > limit:
        return None
    ctype = loam_plugins.TYPES.get(path.suffix.lower())
    return (path.read_bytes(), ctype) if ctype and ctype.startswith("image/") else None


def _read_details(entry: dict) -> dict:
    import loam_plugins  # noqa: PLC0415
    src = {"repo": entry["repo"], "ref": entry["ref"], "path": entry["path"]}
    work = Path(tempfile.mkdtemp(prefix="market-", dir=loam_plugins._staging()))
    try:
        got = loam_plugins.fetch(src, work)
        folder = got["folder"]
        try:
            found = loam_plugins.read_folder(folder, None)
        except loam_plugins.ManifestError as exc:
            raise loam_plugins.PluginError(f"{entry['name']} at its link is not a plugin "
                                           f"Loam can read: {exc}", 422) from None
        manifest = found["manifest"]
        if manifest["id"] != entry["id"]:
            raise loam_plugins.PluginError(f"the link holds the plugin {manifest['id']!r}, "
                                           f"not {entry['id']!r}", 422)
        files: dict = {}
        icon = _picture(folder, manifest.get("icon"), loam_plugins.ICON, ICON_MAX)
        if icon:
            files["icon"] = icon
        image = _picture(folder, entry.get("image") or manifest.get("image"),
                         loam_plugins.IMAGE, IMAGE_MAX)
        if image:
            files["image"] = image
        theme = None
        if "theme" in manifest["kind"]:
            css = folder / "theme.css"
            if css.is_file() and css.stat().st_size <= THEME_MAX:
                files["theme.css"] = (css.read_bytes(), "text/css; charset=utf-8")
                theme = {"options": manifest.get("options") or []}
        listed_only = [k for k in manifest["kind"] if k in loam_plugins.LISTED_ONLY]
        guide = (found.get("guide") or "").strip()
        body = {
            "id": entry["id"], "listing": entry,
            "name": manifest["name"], "version": manifest["version"],
            "author": manifest["author"], "kind": manifest["kind"],
            "declaration": loam_plugins.declaration(manifest, found["has"]),
            "guide": guide[:GUIDE_MAX] if guide else None,
            "warnings": found["warnings"],
            "installable": not listed_only,
            "why": (f"Loam reads and lists {listed_only[0]} packages and installs none yet"
                    if listed_only else None),
            "source": {**got["source"], "url": entry["link"]},
            "icon": "icon" in files, "image": "image" in files, "theme": theme,
        }
        return {"at": time.time(), "body": body, "files": files}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _loaded(pid: str, refresh: bool = False) -> dict:
    entry = _entry(pid)
    key = entry["link"]
    with _lock:
        gate = _details_locks.setdefault(key, threading.Lock())
    with gate:
        hit = _details.get(key)
        if hit and not refresh and time.time() - hit["at"] < DETAILS_TTL:
            got = hit
        else:
            got = _read_details(entry)
            _details[key] = got
    # The installed marks move with this install, not with the cache.
    return {**got, "body": {**got["body"], "listing": entry}}


def details(pid: str, refresh: bool = False) -> dict:
    """``GET /v1/marketplace/<id>``: the listing and what the plugin at its
    link declares, before anything is installed. Raises NotListed (404) or
    loam_plugins.PluginError (GitHub could not be read, not a plugin)."""
    return _loaded(pid, refresh)["body"]


def file(pid: str, name: str) -> tuple[bytes, str]:
    """``GET /v1/marketplace/<id>/<icon|image|theme.css>``: one of the files
    ``details`` kept, as (bytes, type)."""
    if name not in ("icon", "image", "theme.css"):
        raise NotListed(f"a listing has no file {name!r}")
    got = _loaded(pid)["files"].get(name)
    if not got:
        raise NotListed(f"{pid} has no {name}")
    return got


# ---------------------------------------------------------------------------
# Finding things, for an agent
# ---------------------------------------------------------------------------
#
# The owner, 2026-11-18: "Agents should be able to go navigate the
# Marketplace and create that card in chat to let the user install something
# if they wanted to ... It should just be a capability that the agents have
# access to, and the UI supports it too." `search` is the agent's half
# (bin/loam-market search, the `market` tool of mcp/engineer_server.py); the
# card is app/public/market-card.js and page/market.js.
#
# **One matching rule.** The ranking is the Market page's own: the list is
# handed to app/tools/market-search.mjs, which runs public/market.js's
# `search` in Node (the app's own runtime, on every Loam host). A second
# matcher in Python would drift from the page's the first time either
# changed; app/test/market-agent-search.test.js holds the two answers equal.

SEARCH_LIMIT = 8
SEARCH_SCRIPT = lc.ROOT / "app" / "tools" / "market-search.mjs"


class SearchError(RuntimeError):
    pass


def _node() -> str | None:
    found = shutil.which("node")
    if found:
        return found
    return "/usr/bin/node" if Path("/usr/bin/node").is_file() else None


def ranked(entries: list[dict], q: str, kind: str = "all") -> list[str]:
    """The ids of ``entries`` that answer ``q``, in the Market page's order."""
    node = _node()
    if not node or not SEARCH_SCRIPT.is_file():
        raise SearchError("the Market's search needs Node and app/tools/market-search.mjs")
    run = subprocess.run([node, str(SEARCH_SCRIPT)], cwd=str(SEARCH_SCRIPT.parent),
                         input=json.dumps({"entries": entries, "q": q, "kind": kind}),
                         capture_output=True, text=True, timeout=20, check=False)
    try:
        return json.loads(run.stdout)["ids"]
    except (ValueError, KeyError, TypeError):
        raise SearchError(f"the Market's search failed: {run.stderr.strip()[-300:]}") from None


def app_origin() -> str | None:
    """Where the app is, `https://<host>`: the record that serves Loam's
    page at the install's own domain, else the first that serves it, else
    the domain. None without a deploy layer (a test, a bare checkout)."""
    try:
        import loam_deploy  # noqa: PLC0415
        dom = loam_deploy.domain()
        shells = [a for a in loam_deploy.records() if a.app_shell]
    except Exception:  # noqa: BLE001 -- no deploy layer here
        return None
    for app in shells:
        if dom in app.hostnames:
            return f"https://{dom}"
    return f"https://{shells[0].fqdn}" if shells else f"https://{dom}"


def listing_address(pid: str, origin: str | None = None) -> str:
    """A listing's page in the app; the same form app/public/market-card.js
    reads as a card (`listingAddress` there)."""
    origin = origin if origin is not None else (app_origin() or "https://loam.invalid")
    return f"{origin.rstrip('/')}/?modal=market&listing={pid}"


def card_line(entry: dict, origin: str | None = None) -> str:
    """The line an agent puts in its reply to show a listing's card: the
    name, linked to its page. Plain Markdown, so the thread record reads it
    as what it is; brackets in a name are dropped so the link stays one."""
    name = re.sub(r"[\[\]\n]", "", entry.get("name") or entry["id"]).strip() or entry["id"]
    return f"[{name}]({listing_address(entry['id'], origin)})"


def search(q: str = "", kind: str = "all", limit: int = SEARCH_LIMIT,
           refresh: bool = False) -> dict:
    """What the Market lists that answers ``q`` (plain words, as a person
    types them in the Market's field), best first, each with what an agent
    needs to choose and explain it and the line that shows its card."""
    kind = (kind or "all").strip().lower()
    if kind not in ("all", *KINDS):
        raise ValueError(f"kind is one of {', '.join(KINDS)} (or empty for everything)")
    q = " ".join(str(q or "").split())[:200]
    out = listing(refresh=refresh)
    entries = out.get("plugins", [])
    base = {"query": q, "kind": kind, "state": out["state"], "results": [], "count": 0,
            "listed": len(entries)}
    if out["state"] == "off":
        return {**base, "say": "This Loam has the Market turned off (marketplace.list "
                "in its config). Nothing can be found there; offer to build it instead."}
    if out["state"] == "unreachable":
        return {**base, "say": f"The Market can't be read just now ({out.get('message')}). "
                "Say so, and offer to build it instead or to look again later."}
    if not entries:
        return {**base, "say": "The Market lists nothing yet. Offer to build it: "
                "you can make it as the owner's own plugin."}
    ids = ranked(entries, q, kind)
    by_id = {e["id"]: e for e in entries}
    import loam_plugins  # noqa: PLC0415
    origin = app_origin()
    results = []
    for pid in ids[:max(1, min(int(limit or SEARCH_LIMIT), 20))]:
        e = by_id[pid]
        listed_only = [k for k in e["kind"] if k in loam_plugins.LISTED_ONLY]
        results.append({
            "id": pid, "name": e["name"], "author": e["author"], "kind": e["kind"],
            "description": e.get("description") or "",
            "installed": bool(e.get("installed")),
            "installable": not listed_only,
            **({"why_not": f"Loam lists {listed_only[0]} packages and installs none yet"}
               if listed_only else {}),
            "card": card_line(e, origin),
        })
    where = f" among {kind}s" if kind != "all" else ""
    if not ids:
        say = (f"Nothing in the Market matches “{q}”{where}. Offer to build it as the "
               "owner's own plugin.")
    else:
        say = (f"{len(ids)} {'listing matches' if len(ids) == 1 else 'listings match'}"
               f"{f' “{q}”' if q else ''}{where}, best first. To offer one, put its `card` "
               "on a line of its own in your reply (one to three); installing is the "
               "owner's tap on the card, never yours.")
    return {**base, "results": results, "count": len(ids), "say": say}


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
                             "connects it in Settings › Connections, then this "
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
