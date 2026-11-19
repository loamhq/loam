"""The marketplace: one list of plugins, read from a URL, installed by link.

Added 2026-11-17. Decided 2026-11-13: one public GitHub repository holds a
list file; builders host their own work and ask by pull request to be listed;
Settings › Market (Settings › Plugins until 2026-11-18) reads the list and
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
its declaration in full, its version, author and guide, its README, and for
a theme its token CSS and options, so the Market can draw its swatch and let
the owner try it before getting it. Its icon, its cover (the listing's
``cover``, else a theme's own ``image``), the README's pictures and its
theme.css are kept in this process for ``file``. Kept ``DETAILS_TTL`` seconds per link: a listing names a tag, so
what it holds does not change under a reader.

**Optional fields** (2026-11-18): ``featured: true`` makes a listing the
Market's lead story; ``cover`` and ``cover_dark`` are its pictures (in the
plugin's folder, or an https address; ``image`` is ``cover``'s older name);
``readme`` names the README in the folder (README.md when absent); and
``author`` may carry the creator's links. See "The creator, the cover and the
README" below.

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
# A listing's screenshots (2026-11-19, `screenshots` in PLUGIN.md): each
# picture at most IMAGE_MAX, all of them together at most SHOTS_TOTAL.
SHOTS_TOTAL = 16 * 1024 * 1024
# A listed theme's display faces, so its preview wears them before install:
# its fonts.css and the .woff2/.woff files beside it, each at most FONT_MAX.
FONT_MAX = 400 * 1024
FONTS_MAX = 8
FONT_NAME = re.compile(r"^[A-Za-z0-9_-]+\.(?:woff2|woff)$")
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
        value = author_name(entry) if field == "author" else entry.get(field)
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
    if not src["ref"]:
        # A listing is pinned: its readers see what its link names, always.
        return None, f"{pid}: `link` is the plugin's GitHub link at a tag or a commit"
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
    # The creator (2026-11-18): a name, and the links they choose to show.
    out["creator"] = creator_of(entry, out["author"])
    # The cover (2026-11-18): `cover` and `cover_dark`, a picture in the
    # plugin's folder at its tag or an https address; `image` is the older
    # name for `cover`, still read.
    for field, source in (("cover", entry.get("cover", entry.get("image"))),
                          ("cover_dark", entry.get("cover_dark"))):
        value = cover_value(source)
        if value:
            out[field] = value
            out[f"{field}_url"] = public_url(out, value)
    readme = entry.get("readme")
    if isinstance(readme, str) and is_readme_path(readme):
        out["readme"] = readme
    return out, None


# ---------------------------------------------------------------------------
# The creator, the cover and the README (2026-11-18)
# ---------------------------------------------------------------------------
#
# The owner, 2026-11-18: "Plugins should be able to have their repo readme
# displayed on their page and be able to have a beautiful cover photo with
# it. They should also have the creator ... The creator should also be able
# to attach their personal links, like social media, patreon, personal
# website, etc. optionally."
#
# **A listing's `author`** is a name, or ``{"name", "links": [{"label",
# "url"}]}``. The marketplace's build.py writes list.json with ``author`` as
# the name alone and the whole as ``creator``, so a Loam from before this
# reads the name and every listing stays on its list; this one reads
# ``creator`` first. Each link's kind (GitHub, Patreon, email, ...) is read
# from its address here, once, so the page and an agent's tool agree.

LINKS_MAX = 8
LABEL_MAX = 40
URL_MAX = 300

# A link's kind, by its host (or a host under it). Order matters only where
# hosts could overlap; none do.
LINK_HOSTS = (
    ("github", ("github.com",)),
    ("gitlab", ("gitlab.com",)),
    ("codeberg", ("codeberg.org",)),
    ("x", ("x.com", "twitter.com")),
    ("bluesky", ("bsky.app", "bsky.social")),
    ("instagram", ("instagram.com",)),
    ("threads", ("threads.net", "threads.com")),
    ("youtube", ("youtube.com", "youtu.be")),
    ("tiktok", ("tiktok.com",)),
    ("twitch", ("twitch.tv",)),
    ("linkedin", ("linkedin.com",)),
    ("patreon", ("patreon.com",)),
    ("kofi", ("ko-fi.com",)),
    ("buymeacoffee", ("buymeacoffee.com",)),
    ("substack", ("substack.com",)),
    ("discord", ("discord.gg", "discord.com")),
    ("mastodon", ("mastodon.social", "mastodon.online", "mastodon.world", "mstdn.social",
                  "mas.to", "fosstodon.org", "hachyderm.io", "infosec.exchange",
                  "techhub.social", "universeodon.com", "social.coop", "mastodon.art",
                  "kolektiva.social", "toot.community", "indieweb.social")),
)
LINK_LABEL = {
    "github": "GitHub", "gitlab": "GitLab", "codeberg": "Codeberg", "x": "X",
    "bluesky": "Bluesky", "instagram": "Instagram", "threads": "Threads", "youtube": "YouTube",
    "tiktok": "TikTok", "twitch": "Twitch", "linkedin": "LinkedIn", "patreon": "Patreon",
    "kofi": "Ko-fi", "buymeacoffee": "Buy Me a Coffee", "substack": "Substack",
    "discord": "Discord", "mastodon": "Mastodon", "email": "Email", "website": "Website",
}
LINK_KINDS = tuple(LINK_LABEL)
# Any other host whose path is just /@name is a Mastodon (or another
# fediverse) profile: the form every instance uses.
FEDIVERSE_PATH = re.compile(r"^/@[A-Za-z0-9_.]+(?:@[A-Za-z0-9.-]+)?/?$")


def link_kind(url: str) -> str:
    """The kind of a creator's link, read from its address."""
    if url.lower().startswith("mailto:"):
        return "email"
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    for prefix in ("www.", "m.", "mobile."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    for kind, hosts in LINK_HOSTS:
        if any(host == h or host.endswith("." + h) for h in hosts):
            return kind
    if FEDIVERSE_PATH.match(parts.path or ""):
        return "mastodon"
    return "website"


def check_link(raw) -> dict | None:
    """One ``{label, url}`` made safe to draw, with its kind, or None."""
    if isinstance(raw, str):
        raw = {"url": raw}
    if not isinstance(raw, dict):
        return None
    url = raw.get("url")
    if not isinstance(url, str):
        return None
    url = url.strip()
    if len(url) > URL_MAX or any(c.isspace() or ord(c) < 32 for c in url):
        return None
    if url.lower().startswith("mailto:"):
        if not re.match(r"^mailto:[^@/?#]+@[^@/?#]+\.[^@/?#]+$", url, re.I):
            return None
    elif not url.startswith("https://") or not urllib.parse.urlsplit(url).hostname:
        return None
    kind = link_kind(url)
    label = raw.get("label")
    label = " ".join(label.split()) if isinstance(label, str) else ""
    if not label:
        label = LINK_LABEL[kind] if kind != "website" else (
            (urllib.parse.urlsplit(url).hostname or "").removeprefix("www.") or "Website")
    return {"label": label[:LABEL_MAX], "url": url, "kind": kind}


def creator_of(entry: dict, name: str) -> dict:
    """``{"name", "links"}``: the listing's ``creator`` (as build.py writes
    it), else an ``author`` object, else the name alone. A link that is not
    one is left out, never the listing."""
    source = entry.get("creator") if isinstance(entry.get("creator"), dict) else entry.get("author")
    raw = source.get("links") if isinstance(source, dict) else None
    links, seen = [], set()
    for item in raw if isinstance(raw, list) else []:
        link = check_link(item)
        if link and link["url"] not in seen and len(links) < LINKS_MAX:
            seen.add(link["url"])
            links.append(link)
    return {"name": name, "links": links}


def author_name(entry: dict):
    """The name a listing gives its maker, whichever form it is in."""
    for field in ("creator", "author"):
        value = entry.get(field)
        if isinstance(value, dict):
            value = value.get("name")
        if isinstance(value, str) and value.strip():
            return value
    return entry.get("author")


def cover_value(value) -> str | None:
    """A cover as a listing may name it: a picture in the plugin's folder,
    or an https address of one."""
    import loam_plugins  # noqa: PLC0415
    if not isinstance(value, str) or not value.strip() or len(value) > URL_MAX:
        return None
    value = value.strip()
    if value.startswith("https://"):
        parts = urllib.parse.urlsplit(value)
        return value if parts.hostname and not any(c.isspace() for c in value) else None
    return value if loam_plugins.IMAGE.match(value) else None


def is_url(value: str) -> bool:
    return value.startswith("https://")


def public_url(entry: dict, value: str) -> str:
    """Where anyone can see a cover: its address, or the file in the
    plugin's folder at the listing's tag, on GitHub."""
    if is_url(value):
        return value
    folder = "/".join(p for p in (entry["path"], value) if p)
    return f"https://raw.githubusercontent.com/{entry['repo']}/{entry['ref']}/{folder}"


README_PATH = re.compile(r"^(?:[A-Za-z0-9_-][A-Za-z0-9_.-]*/)*[A-Za-z0-9_-][A-Za-z0-9_.-]*\.(?:md|markdown)$",
                         re.I)


def is_readme_path(value: str) -> bool:
    return (len(value) < 200 and bool(README_PATH.match(value))
            and ".." not in value.split("/"))


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


def sniff_image(data: bytes) -> str | None:
    """A picture's type from its first bytes, never from what a host says."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:12] in (b"ftypavif", b"ftypavis"):
        return "image/avif"
    return None


def remote_allowed(url: str) -> bool:
    """Whether a cover's address may be fetched: https, to a host whose
    every address is on the public internet (never this box or its LAN)."""
    import ipaddress  # noqa: PLC0415
    import socket  # noqa: PLC0415
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return False
    return bool(infos)


class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: PLR0913
        if not remote_allowed(newurl):
            raise urllib.error.URLError("a redirect to an address not allowed")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _remote_image(url: str):
    """A cover at an https address, as (bytes, type), or None. Fetched by
    the broker so the page never asks another host (and never tells it who
    is looking)."""
    if not remote_allowed(url):
        return None
    opener = urllib.request.build_opener(_CheckedRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": "loam-marketplace"})
    try:
        with opener.open(req, timeout=TIMEOUT) as resp:
            data = resp.read(IMAGE_MAX + 1)
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return None
    ctype = sniff_image(data)
    return (data, ctype) if ctype and len(data) <= IMAGE_MAX else None


def _cover(folder: Path, value: str):
    import loam_plugins  # noqa: PLC0415
    if is_url(value):
        return _remote_image(value)
    return _picture(folder, value, loam_plugins.IMAGE, IMAGE_MAX)


# The README (2026-11-18). The plugin folder's README.md at the listed tag,
# or the file the listing's `readme` names in that folder. What the page is
# handed is Markdown with no HTML in it: comments and tags are taken out
# (an <img> or <a> becomes its Markdown form first, so a README written for
# GitHub keeps its pictures and links), relative links point at the file on
# GitHub at the tag, and a picture in the folder is kept for the page to
# load from here. The page's renderer (app/public/markdown.js) builds
# nodes, never markup, and follows only http, https and mailto.
README_MAX = 100_000
README_IMAGES = 16
README_IMAGES_TOTAL = 12 * 1024 * 1024
FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
MD_LINK = re.compile(r"(!?)\[((?:[^\[\]]|\[[^\[\]]*\])*)\]\(\s*<?([^()\s<>]*(?:\([^()\s]*\)[^()\s<>]*)*)>?"
                     r"((?:\s+(?:\"[^\"]*\"|'[^']*'))?)\s*\)")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
HTML_IMG = re.compile(r"<img\b([^>]*)/?>", re.I)
HTML_A = re.compile(r"<a\b([^>]*)>(.*?)</a\s*>", re.I | re.S)
HTML_BR = re.compile(r"<br\s*/?>", re.I)
HTML_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>")
ATTR = re.compile(r"""([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")


def _attrs(text: str) -> dict:
    return {m.group(1).lower(): next(g for g in m.groups()[1:] if g is not None)
            for m in ATTR.finditer(text)}


def _resolve(folder_path: str, dest: str) -> str | None:
    """A relative destination in the README as a path from the repository's
    root, or None when it climbs out of the repository."""
    import posixpath  # noqa: PLC0415
    dest = urllib.parse.unquote(dest.split("#", 1)[0].split("?", 1)[0])
    base = "" if dest.startswith("/") else folder_path
    joined = posixpath.normpath(posixpath.join("/", base, dest.lstrip("/")))
    return joined.lstrip("/") if joined != "/" else ""


# What a README says that the listing's page already shows above it
# (2026-11-19): its pictures of the plugin at work, between
# `<!-- loam:gallery -->` and `<!-- /loam:gallery -->` (the gallery, or a
# theme's live preview), and its one-line promise, between `<!-- loam:hero -->`
# and `<!-- /loam:hero -->` (the listing's description). The page is handed
# the README without them; GitHub, and a Loam before this one, show them.
GALLERY_BLOCK = re.compile(r"<!--\s*loam:(gallery|hero)\s*-->.*?<!--\s*/loam:\1\s*-->", re.S)


def readme_markdown(text: str, entry: dict) -> tuple[str, list[str]]:
    """The README as the page is handed it, and the folder's pictures it
    shows (paths inside the plugin's folder), in order."""
    text = GALLERY_BLOCK.sub("", text)
    import html  # noqa: PLC0415
    import loam_plugins  # noqa: PLC0415
    repo_, ref, folder_path = entry["repo"], entry["ref"], entry.get("path") or ""
    blob = f"https://github.com/{repo_}/blob/{ref}/"
    raw = f"https://raw.githubusercontent.com/{repo_}/{ref}/"
    pictures: list[str] = []

    def html_to_md(chunk: str) -> str:
        chunk = HTML_COMMENT.sub("", chunk)

        def img(m):
            a = _attrs(m.group(1))
            return f"![{a.get('alt', '').replace(']', '')}]({a['src']})" if a.get("src") else ""

        def anchor(m):
            a = _attrs(m.group(1))
            inner = HTML_TAG.sub("", HTML_IMG.sub(img, m.group(2))).strip()
            return f"[{inner}]({a['href']})" if a.get("href") and inner else inner

        chunk = HTML_IMG.sub(img, HTML_A.sub(anchor, chunk))
        chunk = HTML_BR.sub("\n", chunk)
        return html.unescape(HTML_TAG.sub("", chunk))

    def link(m):
        bang, label, dest, title = m.groups()
        if not dest or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", dest) or dest.startswith("#"):
            return m.group(0)
        path = _resolve(folder_path, dest)
        if path is None:
            return label if not bang else ""
        inside = folder_path and (path == folder_path or path.startswith(folder_path + "/"))
        rel = path[len(folder_path) + 1:] if folder_path and inside else (path if not folder_path else None)
        if bang and rel is not None and loam_plugins.IMAGE.match(rel):
            if rel not in pictures and len(pictures) < README_IMAGES:
                pictures.append(rel)
            return f"![{label}]({rel})"
        return f"{bang}[{label}]({(raw if bang else blob) + urllib.parse.quote(path)})"

    out, fence, block = [], None, []

    def flush():
        if block:
            chunk = html_to_md("\n".join(block))
            # Inline code is kept as written; links outside it are resolved.
            pieces = re.split(r"(`+[^`]*`+)", chunk)
            out.append("".join(p if p.startswith("`") else MD_LINK.sub(link, p) for p in pieces))
            block.clear()

    for line in text.replace("\r\n", "\n").split("\n"):
        mark = FENCE.match(line)
        if fence:
            out.append(line)
            if mark and mark.group(1)[0] == fence[0] and len(mark.group(1)) >= len(fence):
                fence = None
            continue
        if mark:
            flush()
            fence = mark.group(1)
            out.append(line)
            continue
        block.append(line)
    flush()
    md = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return md, pictures


def _readme(folder: Path, entry: dict) -> tuple[str | None, dict]:
    """The README's Markdown and the pictures it shows, as files to keep."""
    rel = entry.get("readme") or "README.md"
    path = folder / rel
    try:
        path.resolve().relative_to(folder.resolve())
    except ValueError:
        return None, {}
    if not path.is_file() or path.is_symlink():
        return None, {}
    text = path.read_bytes()[:README_MAX].decode("utf-8", errors="replace")
    md, pictures = readme_markdown(text, entry)
    files, total = {}, 0
    import loam_plugins  # noqa: PLC0415
    for pic in pictures:
        got = _picture(folder, pic, loam_plugins.IMAGE, IMAGE_MAX)
        if got and total + len(got[0]) <= README_IMAGES_TOTAL:
            files[f"readme/{pic}"] = got
            total += len(got[0])
    return md or None, files


def _screenshots(folder: Path, manifest: dict, files: dict) -> list[dict]:
    """The screenshots its manifest names, kept as ``shot/<n>`` (and
    ``shot/<n>-dark``); what the page is told of each: its caption and
    whether it has a dark one."""
    import loam_plugins  # noqa: PLC0415
    out, total = [], 0
    for shot in (manifest.get("screenshots") or [])[:loam_plugins.SCREENSHOTS_MAX]:
        light = _picture(folder, shot.get("src"), loam_plugins.IMAGE, IMAGE_MAX)
        if not light or total + len(light[0]) > SHOTS_TOTAL:
            continue
        dark = _picture(folder, shot.get("src_dark"), loam_plugins.IMAGE, IMAGE_MAX) \
            if shot.get("src_dark") else None
        if dark and total + len(light[0]) + len(dark[0]) > SHOTS_TOTAL:
            dark = None
        n = len(out)
        files[f"shot/{n}"] = light
        total += len(light[0])
        if dark:
            files[f"shot/{n}-dark"] = dark
            total += len(dark[0])
        out.append({"caption": shot.get("caption") or "", "dark": bool(dark)})
    return out


def _theme_fonts(folder: Path, files: dict) -> bool:
    """A listed theme's fonts.css and its font files, kept as ``fonts.css``
    and ``font/<file>``. The app holds the sheet to @font-face rules naming
    files beside it (themeFontsCSS) before the page sees it."""
    sheet = folder / "fonts.css"
    if not sheet.is_file() or sheet.is_symlink() or sheet.stat().st_size > 64 * 1024:
        return False
    kept = 0
    for font in sorted(folder.iterdir()):
        if kept >= FONTS_MAX:
            break
        if FONT_NAME.match(font.name) and font.is_file() and not font.is_symlink() \
                and font.stat().st_size <= FONT_MAX:
            files[f"font/{font.name}"] = (font.read_bytes(), "font/woff2" if
                                          font.suffix == ".woff2" else "font/woff")
            kept += 1
    files["fonts.css"] = (sheet.read_bytes(), "text/css; charset=utf-8")
    return True


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
        # The cover: the listing's own, else the plugin's (a theme's
        # `image`). The dark one goes with whichever was used.
        own = bool(entry.get("cover"))
        for key, listed, fallback in (("cover", entry.get("cover"), manifest.get("image")),
                                      ("cover-dark", entry.get("cover_dark"),
                                       None if own else manifest.get("image_dark"))):
            got_cover = _cover(folder, listed) if listed else None
            if not got_cover and fallback and not (key == "cover" and own):
                got_cover = _picture(folder, fallback, loam_plugins.IMAGE, IMAGE_MAX)
            if got_cover:
                files[key] = got_cover
        if "cover-dark" in files and "cover" not in files:
            files["cover"] = files.pop("cover-dark")
        positions = {k: manifest.get(f"image_position{s}")
                     for k, s in (("cover_position", ""), ("cover_position_dark", "_dark"))
                     if manifest.get(f"image_position{s}")}
        readme, readme_files = _readme(folder, entry)
        files.update(readme_files)
        theme = None
        if "theme" in manifest["kind"]:
            css = folder / "theme.css"
            if css.is_file() and css.stat().st_size <= THEME_MAX:
                files["theme.css"] = (css.read_bytes(), "text/css; charset=utf-8")
                theme = {"options": manifest.get("options") or [],
                         "palette": manifest.get("palette") or [],
                         "fonts": _theme_fonts(folder, files)}
        shots = _screenshots(folder, manifest, files)
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
            "icon": "icon" in files, "cover": "cover" in files,
            "cover_dark": "cover-dark" in files, **positions,
            "readme": readme,
            "readme_images": sorted(k[len("readme/"):] for k in readme_files),
            "theme": theme,
            # 2026-11-19: its promise and its pictures (`words_of`).
            "description": manifest.get("description"),
            "screenshots": shots,
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


FILE_NAMES = ("icon", "cover", "cover-dark", "theme.css", "fonts.css")
SHOT_FILE = re.compile(r"^shot/[0-5](?:-dark)?$")


def file(pid: str, name: str) -> tuple[bytes, str]:
    """``GET /v1/marketplace/<id>/<icon|cover|cover-dark|theme.css|fonts.css>``,
    ``readme/<path>`` (a picture the README shows), ``shot/<n>`` and
    ``shot/<n>-dark`` (its screenshots) or ``font/<file>`` (a listed theme's
    face): one of the files ``details`` kept, as (bytes, type). ``image`` is
    the older name of ``cover``."""
    name = "cover" if name == "image" else name
    if name not in FILE_NAMES and not name.startswith(("readme/", "font/")) \
            and not SHOT_FILE.match(name):
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


def link_address(link: str, origin: str | None = None) -> str:
    """The app's address that opens the install-from-link sheet with a link
    (public/market-card.js, ``linkAddress``)."""
    return (f"{(origin or app_origin() or '').rstrip('/')}/?modal=market&install="
            f"{urllib.parse.quote(link, safe='')}")


def link_card(name: str, link: str, origin: str | None = None) -> str:
    """The line that is a card for a plugin by its link, in a reply."""
    name = " ".join(str(name).replace("[", "(").replace("]", ")").split())[:120] or "A plugin"
    return f"[{name}]({link_address(link, origin)})"


def read_link(link: str, by: str | None = None) -> dict:
    """``loam market link``: what a link someone pasted holds, read as the
    install sheet reads it (lib/loam_plugins.install's first call: nothing
    is installed), with the card line that offers it in a reply. A
    repository of several plugins answers each one, with its own card."""
    import loam_plugins  # noqa: PLC0415
    out = loam_plugins.install({"url": link}, by)
    if out.get("choose"):
        return {"repo": out["repo"], "choose": [
            {**{k: v for k, v in c.items() if k != "icon"},
             **({"card": link_card(c["name"], c["link"])} if c.get("link") and not c.get("broken") else {})}
            for c in out["choose"]]}
    keep = ("id", "name", "version", "author", "kind", "description", "declaration",
            "note", "link", "installable", "why", "replaces", "warnings")
    return {**{k: out.get(k) for k in keep}, "repo": out["source"]["repo"],
            "source": out["source"], "card": link_card(out["name"], out["link"])}


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
            "creator": e.get("creator") or {"name": e["author"], "links": []},
            "description": e.get("description") or "",
            **({"cover": e["cover_url"]} if e.get("cover_url") else {}),
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
    if preview.get("choose"):
        raise ValueError("that repository holds several plugins: "
                         + ", ".join(c["path"] for c in preview["choose"])
                         + "; give the link to one of them (its folder)")
    # Any link a person would paste is read; the listing carries the one it
    # is pinned at (its tag, or its whole commit), which never moves.
    entry = entry_for(preview, preview.get("link") or link.strip(), description)
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
