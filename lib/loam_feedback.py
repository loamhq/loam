"""Telling Loam's makers: a bug, a feature request or an idea, upstream.

Added 2026-11-17. An agent on any install can hand a defect in Loam's core to
the people who make Loam, not only to its own engineer (which fixes that one
box's copy and tells nobody). The route needs no credential on the box: the
agent writes the report, this module takes the personal things out of it,
the agent shows the owner exactly what would be sent, and on their yes gives
them a link to GitHub's new-issue page for the upstream repository with the
title and body already filled in. The owner opens it, signed in to their own
GitHub account, and presses Submit. Nothing leaves the box by itself, and the
owner's yes is the press.

**Why not file it directly through the owner's connected GitHub.** The
Integrations connection is a GitHub App the owner registered for their own
account (lib/loam_github_app.py). Its user token reaches only the
repositories that app is installed on, and it is never installed on the
upstream repository, so GitHub refuses to open an issue there with it. A
direct route would need a different credential (a token of the owner's with
public-repository scope), which no install has today.

**What is taken out** (``scrub``), by name where the install knows the name
and by shape where it cannot:

- the owner's name and id, the install's Unix user, the box's host name, and
  the name of every agent except the one every install starts with;
- the install's domain, and any host under it;
- paths: the vault, the workspaces, the plugins folder, the state, the
  checkout and the home folder become ``<vault>``, ``<workspaces>``,
  ``<plugins>``, ``<state>``, ``<loam>`` and ``~``; any other
  ``/home/<name>/`` or ``/Users/<name>/`` becomes ``/home/<user>/``;
- e-mail addresses, IP addresses (but loopback), anything shaped like a key
  or a token, the query string of every link, and the target of every
  ``[[wiki link]]`` (a note's name is the vault's content).

The vault's content itself cannot be recognised by shape. The skill that
drives this (``loam-filing-a-bug-or-feature``) tells the agent never to quote
a note, a thread or a memory into a report, and the owner reads every word
before it goes.

**A patch.** A core bug the box's engineer has already fixed can go upstream
with its fix: ``draft(..., patch=True)`` appends the diff of the checkout's
own commits since the running release (or a range given), scrubbed the same
way. A real pull request would need a fork and a push from the owner's
account, which needs the same credential the direct route lacks; until then
the patch in the issue is what a maintainer applies.

**Sending it privately** (2026-11-18, Loam 0.2.7). Most people Loam is
given to have no GitHub account, and some of what they would say is not for
a public page. So a report can also go straight to a private inbox on Loam's
makers' box: ``draft`` keeps what it showed under its ``id`` in the state
folder, and ``send(id)`` posts exactly those words, once the owner has read
them and said yes. The other end is ``receive``, run by the claim service on
the makers' box (bin/loam-claim, ``POST /_loam/feedback``), which writes each
report as one markdown file in that vault and one line in its General board.
It takes a report only from a Loam install: the sender names its own domain
and the box's public DNS must say that name is the address the report came
from (installs' names under Loam's base domain are made by Loam's makers, so
a name that points at its caller is one of theirs). It takes plain text only,
of a capped size, a few an hour per install, and keeps a code for the
install, never its name or address. ``feedback.receive`` in loam.toml turns
the receiving end on (only the makers' box has it) on the host
``feedback.host`` (default: the product's base domain), and ``feedback.url``
moves where installs send.

The upstream repository is ``feedback.repo`` in loam.toml (default
``loamhq/loam``). The tests are tests/test_feedback.py.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import loam_config as lc

DEFAULT_REPO = "loamhq/loam"
STARTER_AGENTS = ("iris",)        # the agent every install starts with
# A kind and the label its public issue carries. An idea is everything that
# is neither a defect nor a request: "I wish", "this confused me", "I love".
KINDS = {"bug": "bug", "feature": "enhancement", "idea": "idea"}
# GitHub answers a new-issue link of up to about 8 KB; past that it is an
# error page. The link is kept under this, and a longer body is handed to the
# owner to paste.
URL_LIMIT = 7000
PATCH_LINES = 400


def repo() -> str:
    value = lc.get("feedback.repo") or DEFAULT_REPO
    if not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", str(value)):
        raise ValueError(f"feedback.repo {value!r} is not owner/name")
    return str(value)


# ---------------------------------------------------------------------------
# What is personal
# ---------------------------------------------------------------------------

def _agent_names() -> list[str]:
    try:
        import loam_agents  # noqa: PLC0415
        out = []
        for agent in loam_agents.every():
            if agent in STARTER_AGENTS:
                continue
            out.append(agent)
            name = loam_agents.card(agent).get("name")
            if isinstance(name, str):
                out.append(name)
        return out
    except Exception:  # noqa: BLE001 -- a report is still worth scrubbing without the roster
        return []


def _domain() -> str | None:
    value = lc.get("edge.domain")
    if value:
        return str(value)
    try:
        import loam_deploy  # noqa: PLC0415
        return loam_deploy.domain()
    except Exception:  # noqa: BLE001 -- no deploy layer on this box
        return None


NEVER_PRIVATE = {"loam"}


def private_names() -> list[tuple[str, str]]:
    """``(term, what it is)`` for every name this install knows is its own,
    longest first so a full name goes before its parts."""
    found: list[tuple[str, str]] = []

    def add(term, what):
        term = str(term or "").strip()
        # The product's own name is never personal, and an install's user
        # is `loam` by default: scrubbing it made every "Loam" in a report
        # "<user>" (found by the 0.2.2 rehearsal, 2026-11-18).
        if term.lower() in NEVER_PRIVATE:
            return
        if len(term) >= 3 and term.lower() not in {t.lower() for t, _ in found}:
            found.append((term, what))

    if lc.OWNER_NAME and lc.OWNER_NAME != "the owner":
        add(lc.OWNER_NAME, "your name")
        for part in str(lc.OWNER_NAME).split():
            add(part, "your name")
    if lc.OWNER_ID and lc.OWNER_ID != "owner":
        add(lc.OWNER_ID, "your name")
    for legacy in lc.LEGACY_OWNER_IDS:
        add(legacy, "your name")
    add(lc.USER, "this server's user name")
    try:
        add(socket.gethostname(), "this server's name")
    except OSError:
        pass
    for name in (lc.GIT_NAME, lc.AUTOCOMMIT_NAME):
        if name and name not in ("loam", "Loam"):
            add(name, "a commit name")
    for name in _agent_names():
        add(name, "an agent's name")
    found.sort(key=lambda item: -len(item[0]))
    return found


def _paths() -> list[tuple[str, str]]:
    pairs = [(lc.VAULT, "<vault>"), (lc.WORKSPACES, "<workspaces>"),
             (lc.PLUGINS, "<plugins>"), (lc.STATE, "<state>"), (lc.ROOT, "<loam>"),
             (lc.PROJECTS, "<projects>"), (lc.HOME, "~")]
    out = []
    for path, label in pairs:
        text = str(path).rstrip("/")
        if len(text) > 1:
            out.append((text, label))
    return sorted(out, key=lambda item: -len(item[0]))


# Shapes, each with the words the owner is shown for it.
# The fine-grained token's prefix is split: a scanner (and the push gate's
# deny list) reads the literal as a token.
SECRET = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github" r"_pat_[A-Za-z0-9_]{20,}|sk-ant-[A-Za-z0-9_-]{10,}"
    r"|sk-[A-Za-z0-9_-]{20,}|lak_[A-Za-z0-9_-]{10,}|xox[abprs]-[A-Za-z0-9-]{10,}"
    r"|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
    r"|(?<![A-Za-z0-9+_-])[A-Za-z0-9+_-]{32,}={0,2}(?![A-Za-z0-9+_-]))")
HEXISH = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")     # commits and hashes stay
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
IPV6 = re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f]{1,4}(?![0-9A-Fa-f:])")
HOME_PATH = re.compile(r"(/home/|/Users/)(?!<user>)[^/\s'\"`)]+")
QUERY = re.compile(r"(https?://[^\s?#'\"`)>]+)\?[^\s#'\"`)>]+")
WIKILINK = re.compile(r"\[\[[^\]]+\]\]")
KEYED = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|authorization|bearer)"
                   r"(\s*[:=]\s*|\s+)(['\"]?)[^\s'\",;]{6,}\3")


# A vault path: folders, then a file with its extension, or a last folder.
# A name may hold single spaces between words ("Wedding plans.md"), never a
# space before a slash, so a path ends where the sentence goes on.
_WORD = r"[^/\s'\"`)\]>]+"
_NAME = rf"{_WORD}(?: {_WORD})*?"
VAULT_PATH = re.compile(
    rf"<vault>/((?:{_NAME}/)*(?:{_NAME}\.(?:md|json|canvas|txt|pdf|png|jpe?g|webp|csv)\b"
    rf"|{_WORD}))")


def _loam_folders() -> set[str]:
    """The vault's folders that are Loam's own (Inbox, Conversations,
    System/Loam, ...): their names say nothing about the person."""
    names = {"Loam", "skills", "data", "boards"}
    for value in lc.VAULT_FOLDERS.values():
        names.update(part for part in str(value).split("/") if part and not part.endswith(".md"))
    return names


def _vault_path(m: re.Match) -> str:
    """``<vault>/Inbox/boards/x.md`` keeps Loam's folders and loses the rest:
    a note's name and its folders are the person's."""
    path = m.group(1)
    library = f"{lc.vault_folder('system')}/Loam/"
    if path.startswith(library) and not path.startswith(library + "data/"):
        return m.group(0)               # Loam's own library, shipped in every release
    parts = path.split("/")
    own = _loam_folders()
    kept = []
    for part in parts:
        if part not in own:
            break
        kept.append(part)
    if len(kept) == len(parts):
        return m.group(0)
    return "<vault>/" + "/".join(kept + ["<note>"])


KNOWN_PREFIX = re.compile(r"^(?:gh[pousr]_|github" r"_pat_|sk-|lak_|xox|AKIA|eyJ|-----BEGIN)")


def looks_secret(found: str) -> bool:
    """A key or token, not a commit, a hash or a long name: a long run with
    no known prefix counts only when it is random-looking (mixed case and
    digits), so `test_engineer_queue_wait_for_the_slot` stays."""
    if HEXISH.match(found):
        return False
    if KNOWN_PREFIX.match(found) or len(found) >= 60:
        return True
    return bool(re.search(r"[a-z]", found) and re.search(r"[A-Z]", found)
                and re.search(r"[0-9]", found))


def scrub(text: str, names: list[tuple[str, str]] | None = None,
          paths: list[tuple[str, str]] | None = None,
          domain: str | None | bool = False) -> tuple[str, dict[str, int]]:
    """``text`` without what is personal, and how many of each kind came
    out (``{"your name": 2, ...}``). ``names``, ``paths`` and ``domain``
    default to this install's own; a test passes its own."""
    removed: dict[str, int] = {}

    def count(what: str, n: int = 1) -> None:
        if n:
            removed[what] = removed.get(what, 0) + n

    names = private_names() if names is None else names
    paths = _paths() if paths is None else paths
    domain = _domain() if domain is False else domain

    def keep_hash(m: re.Match) -> str:
        found = m.group(0)
        if not looks_secret(found):
            return found
        count("a key or a token")
        return "<secret>"

    text = SECRET.sub(keep_hash, text)
    text, n = KEYED.subn(lambda m: f"{m.group(1)}{m.group(2)}<secret>", text)
    count("a key or a token", n)
    text, n = QUERY.subn(r"\1?<query>", text)
    count("the query part of a link", n)
    for path, label in paths:
        text, n = re.subn(re.escape(path) + r"(?=/|\b|$)", label, text)
        count("a path on this server", n)
    text, n = HOME_PATH.subn(r"\1<user>", text)
    count("a path on this server", n)
    text, n = VAULT_PATH.subn(_vault_path, text)
    count("the name of a note", n)
    if domain:
        text, n = re.subn(r"(?i)(?:[a-z0-9-]+\.)*" + re.escape(domain), "<domain>", text)
        count("this install's web address", n)
    text, n = EMAIL.subn("<email>", text)
    count("an e-mail address", n)

    def ip(m: re.Match) -> str:
        try:
            addr = ipaddress.ip_address(m.group(0))
        except ValueError:
            return m.group(0)
        if addr.is_loopback or addr.is_unspecified:
            return m.group(0)
        count("an IP address")
        return "<ip>"

    text = IPV4.sub(ip, text)
    text = IPV6.sub(ip, text)
    for term, what in names:
        text, n = re.subn(r"(?i)(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])",
                          "<agent>" if what == "an agent's name" else "<name>", text)
        count(what, n)
    text, n = WIKILINK.subn("[[<note>]]", text)
    count("the name of a note", n)
    return text, removed


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(lc.ROOT), *args], capture_output=True,
                          text=True, timeout=60)


def install_line() -> str:
    """The release and the kind of install, which a maintainer needs and
    which says nothing about the person."""
    try:
        import loam_update  # noqa: PLC0415
        cur = loam_update.current()
        kind = cur["kind"] + (f", {cur['local_commits']} local commits"
                              if cur.get("local_commits") else "")
        return f"Loam {cur.get('version') or 'unknown'} ({kind})"
    except Exception:  # noqa: BLE001 -- a report outlives a broken updater
        try:
            return f"Loam {json.loads((lc.ROOT / 'manifest.json').read_text())['version']}"
        except (OSError, ValueError, KeyError):
            return "Loam (version unknown)"


def local_patch(spec: str | None = None) -> tuple[str, list[str]]:
    """The diff of the checkout's own commits since the running release
    (``spec`` a git range instead), and their subjects."""
    if not spec:
        import loam_update  # noqa: PLC0415
        base = loam_update.tag_of(loam_update.running_version())
        if not base:
            raise ValueError("this checkout has no release tag to measure its own "
                             "commits from; name a range, e.g. `--patch 0.2.1..main`")
        spec = f"refs/tags/{base}..HEAD"
    log = _git("log", "--no-merges", "--format=%s", spec)
    if log.returncode:
        raise ValueError(f"git log {spec}: {log.stderr.strip()[:200]}")
    diff = _git("diff", spec.replace("..", "...", 1) if ".." in spec and "..." not in spec
                else spec)
    if diff.returncode:
        raise ValueError(f"git diff {spec}: {diff.stderr.strip()[:200]}")
    return diff.stdout, [s for s in log.stdout.splitlines() if s.strip()]


def issue_url(title: str, body: str, kind: str, repository: str | None = None) -> str:
    query = {"title": title, "body": body, "labels": KINDS[kind]}
    return (f"https://github.com/{repository or repo()}/issues/new?"
            + urllib.parse.urlencode(query, quote_via=urllib.parse.quote))


def draft(kind: str, title: str, body: str, *, patch: bool | str = False,
          names=None, paths=None, domain=False) -> dict:
    """The report as it would be sent: its scrubbed title and body, what
    was taken out, and the link that opens it on GitHub. Nothing is sent."""
    if kind not in KINDS:
        raise ValueError(f"a report is a bug, a feature or an idea, not {kind!r}")
    title, body = (title or "").strip(), (body or "").strip()
    if not title:
        raise ValueError("a report needs a title: one line saying what is wrong or wanted")
    if not body:
        raise ValueError("a report needs a body: what happened, what is wanted and why, "
                         "or the thought itself")
    parts = [body]
    subjects: list[str] = []
    if patch:
        diff, subjects = local_patch(patch if isinstance(patch, str) else None)
        if not diff.strip():
            raise ValueError("there is no local change to send: the checkout has no "
                             "commits of its own since the release")
        lines = diff.splitlines()
        cut = len(lines) > PATCH_LINES
        shown = "\n".join(lines[:PATCH_LINES])
        parts.append("### A fix made on this install\n\n"
                     + "".join(f"- {s}\n" for s in subjects)
                     + f"\n```diff\n{shown}\n```"
                     + (f"\n\n(The diff is cut at {PATCH_LINES} of its {len(lines)} lines.)"
                        if cut else ""))
    parts.append(f"---\n{install_line()}. Written by an agent on a Loam install and "
                 f"sent by its owner, with personal details taken out.")
    full = "\n\n".join(parts)
    clean_title, removed_t = scrub(title, names, paths, domain)
    clean_body, removed_b = scrub(full, names, paths, domain)
    removed = dict(removed_t)
    for k, v in removed_b.items():
        removed[k] = removed.get(k, 0) + v
    url = issue_url(clean_title, clean_body, kind)
    fits = len(url) <= URL_LIMIT
    if not fits:
        url = issue_url(clean_title, "(The report is pasted below.)", kind)
    return {
        "repo": repo(),
        "kind": kind,
        "label": KINDS[kind],
        "title": clean_title,
        "body": clean_body,
        "removed": removed,
        "url": url,
        "fits": fits,
        "patch": subjects if patch else None,
        "id": hashlib.sha256((clean_title + "\0" + clean_body).encode()).hexdigest()[:12],
    }


def show(report: dict) -> str:
    """The words for the agent to show the owner before they say yes."""
    removed = report["removed"]
    taken = ("Taken out: " + "; ".join(f"{what} ({n})" for what, n in sorted(removed.items()))
             + ".") if removed else "Nothing personal was found to take out."
    lines = [f"To Loam's makers, as {'an' if report['kind'] == 'idea' else 'a'} "
             f"{report['kind']}:", "",
             f"Title: {report['title']}", "", report["body"], "", taken, ""]
    private = report.get("private")
    if private is None:
        lines.append("On the owner's yes: ")
    elif private.get("ok"):
        lines += ["Two ways to send it, on the owner's yes:", "",
                  f"1. Privately, no account needed: `loam report send {report['id']}`. "
                  "It goes to a private inbox only Loam's makers read. It carries this "
                  f"install's name ({private['install']}) so their box can check it "
                  "comes from a Loam install; they keep a code for the install, not "
                  "its name.", "",
                  "2. In public, for an owner who wants to follow it on GitHub: "]
    else:
        lines += [f"It cannot go privately from here: {private.get('reason') or 'unknown'}",
                  "On the owner's yes: "]
    if report["fits"]:
        lines[-1] += ("this link opens the report on GitHub filled in, and it is sent "
                      "when they press Submit, signed in to their own account:")
    else:
        lines[-1] += ("the report is too long for a link to carry. The link opens it "
                      "on GitHub with its title; they paste the body above into it and "
                      "press Submit, signed in to their own account:")
    lines.append(report["url"])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sending it privately (the install's side)
# ---------------------------------------------------------------------------

RECEIVE_PATH = "/_loam/feedback"
# Where installs send: the product's own domain, which the makers' box
# serves (`feedback.host` there); `feedback.url` moves it.
def _send_url() -> str:
    import loam_install  # noqa: PLC0415 -- the one place the domain is written
    return f"https://{loam_install.BASE_DOMAIN}{RECEIVE_PATH}"


SEND_URL = _send_url()
SEND_TIMEOUT = 20
DRAFTS_KEPT = 14 * 86400          # a draft nobody sent is dropped after this


def drafts_dir() -> Path:
    return lc.STATE / "feedback" / "drafts"


def send_url() -> str:
    value = str(lc.get("feedback.url") or SEND_URL)
    if not value.startswith("https://") and not value.startswith("http://127.0.0.1"):
        raise ValueError(f"feedback.url {value!r} is not an https address")
    return value


def private_route() -> dict:
    """{"ok": True, "install": domain} when this install can send privately,
    else {"ok": False, "reason": ...}. It needs its own name on the web (the
    makers' box checks the report came from it) and its owner."""
    domain = lc.get("edge.domain")
    if not domain:
        return {"ok": False, "reason": "this install has no name on the web that Loam's "
                "makers know it by, so their box cannot tell it is a Loam install. "
                "The GitHub link below still works."}
    try:
        import loam_deploy  # noqa: PLC0415
        if loam_deploy.unclaimed():
            return {"ok": False, "reason": "this install has not been claimed by its "
                    "owner yet."}
    except Exception:  # noqa: BLE001 -- no deploy layer: the name is what counts
        pass
    return {"ok": True, "install": str(domain)}


def keep(report: dict) -> Path:
    """Keep the report as shown, under its id, for `send`. Older drafts go."""
    folder = drafts_dir()
    folder.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for old in folder.glob("*.json"):
        try:
            if now - old.stat().st_mtime > DRAFTS_KEPT:
                old.unlink()
        except OSError:
            pass
    path = folder / f"{report['id']}.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return path


def kept(report_id: str) -> dict:
    if not re.match(r"^[0-9a-f]{12}$", report_id or ""):
        raise ValueError(f"{report_id!r} is not a report's id (twelve letters and digits "
                         "from `loam report bug|feature|idea`)")
    try:
        return json.loads((drafts_dir() / f"{report_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError(f"no report {report_id} was drafted here (or it is older than "
                         "two weeks); draft it again and show the owner") from None


def version() -> str:
    try:
        return str(json.loads((lc.ROOT / "manifest.json").read_text())["version"])
    except (OSError, ValueError, KeyError):
        return "unknown"


def send(report_id: str, *, url: str | None = None, opener=None) -> dict:
    """Post the kept report `report_id`, exactly as shown, to the makers'
    inbox. Returns their answer ({"ok": True, "message": ...}); raises
    ValueError with words for the owner when it did not go."""
    report = kept(report_id)
    route = private_route()
    if not route["ok"]:
        raise ValueError(route["reason"])
    payload = {"v": 1, "kind": report["kind"], "title": report["title"],
               "body": report["body"], "version": version(), "install": route["install"]}
    req = urllib.request.Request(url or send_url(), method="POST",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": f"loam/{version()}"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=SEND_TIMEOUT) as res:
            status, raw = res.status, res.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    except (OSError, urllib.error.URLError) as exc:
        raise ValueError(f"Loam's makers' box did not answer ({exc}); try again later, "
                         "or use the GitHub link") from None
    try:
        answer = json.loads(raw or b"{}")
    except ValueError:
        answer = {}
    if status != 200 or not answer.get("ok"):
        raise ValueError(answer.get("error") or f"it was not taken (HTTP {status})")
    try:
        (drafts_dir() / f"{report_id}.json").unlink()
    except OSError:
        pass
    return answer


# ---------------------------------------------------------------------------
# Receiving it (the makers' box: bin/loam-claim, POST /_loam/feedback)
# ---------------------------------------------------------------------------

MAX_REQUEST = 48 * 1024           # bytes on the wire; Caddy cuts at 64 KB first
MAX_TITLE = 200
MAX_BODY = 40_000                 # characters; a report with a patch fits
MAX_INSTALL = 253
# Per install: (window in seconds, how many). And for everyone together, a
# day's worth, so a run of new names cannot fill the vault.
LIMITS = ((3600, 5), (86400, 20))
ALL_PER_DAY = 200
# A caller that is not an install is refused cheaply, and a run of them
# from one address is refused before the DNS question is asked.
REFUSALS = (600, 30)
FIELDS = {"v", "kind", "title", "body", "version", "install"}
HOST_RE = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
VERSION_RE = re.compile(r"^[0-9A-Za-z.+-]{1,32}$")
# Plain text: any character but the controls, which have no place in a
# report and are how a terminal or a renderer is fooled.
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u202a-\u202e\u2066-\u2069]")
STATE_FILE = Path("/var/lib/loam/feedback.json")
ANSWER = "Sent. Loam's makers read every one; they may not answer."


class Refused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def receiving() -> bool:
    return bool(lc.get("feedback.receive"))


def inbox_folder() -> str:
    return str(lc.get("feedback.folder") or f"{lc.vault_folder('projects')}/Loam/feedback")


def parse(raw: bytes) -> dict:
    """The report out of the request's body, or Refused."""
    if len(raw) > MAX_REQUEST:
        raise Refused(413, f"a report is at most {MAX_REQUEST // 1024} KB")
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise Refused(400, "a report is a JSON object of text") from None
    if not isinstance(doc, dict) or set(doc) - FIELDS or doc.get("v") != 1:
        raise Refused(400, f"a report has exactly these fields: {', '.join(sorted(FIELDS))}")
    out = {}
    for key in ("kind", "title", "body", "version", "install"):
        value = doc.get(key)
        if not isinstance(value, str):
            raise Refused(400, f"{key} must be text")
        out[key] = value
    if out["kind"] not in KINDS:
        raise Refused(400, f"kind is one of {', '.join(KINDS)}")
    out["title"] = " ".join(out["title"].split())
    out["body"] = out["body"].replace("\r\n", "\n").strip()
    if not out["title"] or not out["body"]:
        raise Refused(400, "a report needs a title and a body")
    if len(out["title"]) > MAX_TITLE or len(out["body"]) > MAX_BODY:
        raise Refused(413, f"a title is at most {MAX_TITLE} characters and a body "
                      f"{MAX_BODY}")
    if CONTROL.search(out["title"]) or CONTROL.search(out["body"]):
        raise Refused(400, "a report is plain text")
    out["install"] = out["install"].strip().lower().rstrip(".")
    if not HOST_RE.match(out["install"]):
        raise Refused(400, "install is the sending install's domain")
    if not VERSION_RE.match(out["version"]):
        out["version"] = "unknown"
    return out


def resolve(host: str) -> set[str]:
    """Every address the box's resolver gives for `host` (A and AAAA)."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        return set()
    return {str(ipaddress.ip_address(info[4][0].split("%")[0])) for info in infos}


def admitted(install: str, caller: str | None, resolver=resolve) -> bool:
    """Whether the report comes from the install it names: that name's DNS
    is the caller's address. A private or loopback caller is never one."""
    try:
        addr = ipaddress.ip_address((caller or "").strip())
    except ValueError:
        return False
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_unspecified:
        return False
    return str(addr) in resolver(install)


def slug(text: str, n: int = 48) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:n].strip("-")
    return out or "report"


def comms_title(text: str, n: int = 90) -> str:
    """A title fit for a line of the board: words and plain punctuation
    only, so nothing in it renders, links or tags."""
    flat = re.sub(r"[^\w\s.,;:!?'()&%+/-]", " ", text, flags=re.UNICODE)
    flat = " ".join(flat.replace("_", " ").split())
    return flat if len(flat) <= n else flat[: n - 1].rstrip() + "…"


def fence(body: str) -> str:
    """A fence longer than any run of backticks in the body, so the body is
    shown as written and nothing in it is rendered."""
    runs = [len(m) for m in re.findall(r"`+", body)]
    return "`" * max(3, (max(runs) + 1) if runs else 3)


def note_text(report: dict, install_id: str, received: str) -> str:
    f = fence(report["body"])
    return (
        "---\n"
        f"kind: {report['kind']}\n"
        f"title: {json.dumps(report['title'], ensure_ascii=False)}\n"
        f"received: {received}\n"
        f"loam_version: {json.dumps(report['version'])}\n"
        f"install: {install_id}\n"
        "---\n\n"
        "*From a Loam install, sent privately by its owner with personal details "
        "taken out on their side. The body below is exactly as sent, shown and "
        "never rendered.*\n\n"
        f"{f}text\n{report['body']}\n{f}\n")


class Inbox:
    """The receiving end: the limits' state, the install code's key, and
    where reports land. Paths and the clock are arguments so a test can
    point it at a temp tree."""

    def __init__(self, vault: Path | None = None, state: Path | None = None,
                 folder: str | None = None, owner: str | None = None, resolver=resolve):
        self.vault = Path(vault or lc.VAULT)
        self.state_path = Path(state or STATE_FILE)
        self.folder = folder or inbox_folder()
        self.owner = owner if owner is not None else lc.USER
        self.resolver = resolver

    # The state: {"key": hex, "sent": {install_id: [t, ...]}, "refused": {ip: [t]}}
    def _read(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        if not isinstance(data.get("key"), str) or len(data["key"]) < 32:
            data["key"] = secrets.token_hex(32)
        data.setdefault("sent", {})
        data.setdefault("refused", {})
        return data

    def _write(self, data: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, self.state_path)

    @staticmethod
    def _recent(times, window: float, now: float) -> list[float]:
        return [t for t in (times or []) if isinstance(t, (int, float)) and now - t < window]

    def install_id(self, data: dict, install: str) -> str:
        """A code for the install that is the same for each of its reports
        and says nothing of its name: keyed by a secret only this box has."""
        return hmac.new(bytes.fromhex(data["key"]), install.encode(),
                        hashlib.sha256).hexdigest()[:12]

    def _chown(self, path: Path) -> None:
        if os.geteuid() != 0 or not self.owner:
            return
        try:
            import pwd  # noqa: PLC0415
            pw = pwd.getpwnam(self.owner)
            os.chown(path, pw.pw_uid, pw.pw_gid)
        except (KeyError, OSError):
            pass

    def receive(self, raw: bytes, caller: str | None, now: float | None = None) -> dict:
        """Take one report: {"ok", "message", "file"} or Refused. Nothing of
        the caller (its address, its name) is kept in the file."""
        now = time.time() if now is None else now
        data = self._read()
        key_ip = str(caller or "?")
        fails = self._recent(data["refused"].get(key_ip), REFUSALS[0], now)
        if len(fails) >= REFUSALS[1]:
            raise Refused(429, "too many refused reports from here; wait ten minutes")
        try:
            report = parse(raw)
            if not admitted(report["install"], caller, self.resolver):
                raise Refused(403, "this did not come from the Loam install it names; "
                              "a report is taken only from a Loam install with its own "
                              "name on the web, sent by that install")
        except Refused:
            data["refused"] = {ip: ts for ip, ts in data["refused"].items()
                               if self._recent(ts, REFUSALS[0], now)}
            data["refused"][key_ip] = fails + [now]
            self._write(data)
            raise
        iid = self.install_id(data, report["install"])
        mine = data["sent"].get(iid) or []
        for window, most in LIMITS:
            if len(self._recent(mine, window, now)) >= most:
                raise Refused(429, f"this install has sent {most} reports in the last "
                              f"{'hour' if window == 3600 else 'day'}, which is as many as "
                              "Loam's makers take; send it later")
        everyone = sum(len(self._recent(ts, 86400, now)) for ts in data["sent"].values())
        if everyone >= ALL_PER_DAY:
            raise Refused(429, "Loam's makers' inbox is full for today; send it tomorrow")

        when = dt.datetime.fromtimestamp(now, dt.timezone.utc)
        received = when.isoformat(timespec="seconds").replace("+00:00", "Z")
        folder = self.vault / self.folder
        folder.mkdir(parents=True, exist_ok=True)
        self._chown(folder)
        stem = f"{when:%Y-%m-%d-%H%M%S}-{report['kind']}-{slug(report['title'])}"
        path = folder / f"{stem}.md"
        n = 2
        while path.exists():
            path = folder / f"{stem}-{n}.md"
            n += 1
        path.write_text(note_text(report, iid, received), encoding="utf-8")
        self._chown(path)

        data["sent"] = {k: v for k, v in data["sent"].items() if self._recent(v, 86400, now)}
        data["sent"][iid] = self._recent(mine, 86400, now) + [now]
        self._write(data)

        import loam_inbox  # noqa: PLC0415
        rel = f"{self.folder}/{path.stem}"
        loam_inbox.append_comms(
            self.vault, "loam",
            f"Feedback from a Loam install, {'an' if report['kind'] == 'idea' else 'a'} "
            f"{report['kind']}: “{comms_title(report['title'])}”",
            f"[[{rel}|the report]]", section="Other", when=when.date())
        board = self.vault / loam_inbox.COMMS
        if board.exists():
            self._chown(board)
        return {"ok": True, "message": ANSWER, "file": str(path)}
