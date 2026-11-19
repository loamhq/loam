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

The upstream repository is ``feedback.repo`` in loam.toml (default
``loamhq/loam``). The tests are tests/test_feedback.py.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import subprocess
import urllib.parse
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
    if report["fits"]:
        lines.append("On the owner's yes, give them this link: it opens the report on "
                     "GitHub filled in, and it is sent when they press Submit, signed "
                     "in to their own account:")
    else:
        lines.append("The report is too long for a link to carry. On the owner's yes, "
                     "give them this link: it opens the report on GitHub with its "
                     "title; they paste the body above into it and press Submit, "
                     "signed in to their own account:")
    lines.append(report["url"])
    return "\n".join(lines)

