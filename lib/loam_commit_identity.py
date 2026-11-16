"""Whose name a project's commits and pull requests carry (since 2026-10-06).

By default a project's commits are the engineer's (`<name> engineer
<engineer+<name>@noreply.local>`, set by ``bin/provision`` in the checkout) and
a pull request is the installation's. A project whose record carries

    "commit_identity": {"name": "...", "email": "...", "github_login": "..."}

is one where every commit and pull request belongs to a person, who answers
for it in a repository shared with other people:

- a job's clone commits as ``name <email>``, author and committer
  (lib/loam_worktree.configure_clone); the email is the one GitHub attributes
  to ``github_login`` -- by default that account's noreply address,
  ``<id>+<login>@users.noreply.github.com``, swappable for any address
  verified on the account;
- the engineer's CLI adds no attribution of its own (``CLI_SETTINGS``);
- ``problems`` refuses a branch whose new commits -- those the remote does
  not already have (``REMOTE_REFS``) -- carry any other author or
  committer, or whose messages name the tools: a co-author or session
  trailer, "Generated with", Claude, Codex, Loam, a job id. It runs
  at landing and again in the broker immediately before the push, which is
  the one the job cannot reach;
- the broker pushes the branch and opens the pull request with that person's
  user-to-server token, never the installation's, after checking the token
  is ``github_login``'s (lib/loam_github_app.open_pull_request);
- the pull request's title and body are checked by ``text_problems`` too, and
  its body is one the job wrote for the repository's readers
  (``PR_BODY_FILE``), never the receipt, which is ours.

Nothing here knows about any particular project: the record opts in.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

# The file in a job's directory that a person-authored project's pull request
# body is read from. Its first line is the title when it is a `# ` heading.
PR_BODY_FILE = "pull-request.md"

# What the engineer's CLI is handed in `--settings` for such a job. Claude
# Code 2.1.281: `attribution` replaced `includeCoAuthoredBy` (deprecated, read
# still); `commit`/`pr` are the trailer and the PR line, "" for none, and
# `sessionUrl` the Claude-Session trailer. The object form, because the
# binary's own help says older builds reject a bare false here.
CLI_SETTINGS = {"attribution": {"commit": "", "pr": "", "sessionUrl": False},
                "includeCoAuthoredBy": False}

# Names that never appear in a person's commit, title or body. Words, not
# substrings: "Claudette" passes; "loam-ui" does not. The tools' names, not
# our own vocabulary (2026-10-06): "engineer" and the personas' names were
# dropped, because words like "engineer" or "iris" are plausible in the
# product's own code, and refusing an honest commit costs more than they
# protect. A provider word a product might use ("OpenAI", "GPT") stays out
# for the same reason.
MACHINE_WORDS = ("claude", "codex", "loam")
TRAILER_RE = re.compile(r"^\s*(co-authored-by|claude-session|generated-by)\s*:",
                        re.IGNORECASE | re.MULTILINE)
PHRASES_RE = re.compile(r"generated with|noreply@anthropic\.com|claude\.ai|"
                        r"claude\.com", re.IGNORECASE)
JOB_ID_RE = re.compile(r"\b\d{8}-\d{6}-[0-9a-f]{4,16}\b")


def of(record: dict | None) -> dict | None:
    """The record's identity, or None. Malformed is None too, and
    ``refusal`` says so: a half-written identity must not quietly mean the
    engineer's."""
    ident = (record or {}).get("commit_identity")
    if not isinstance(ident, dict):
        return None
    if not all(isinstance(ident.get(k), str) and ident[k].strip()
               for k in ("name", "email", "github_login")):
        return None
    return {k: ident[k].strip() for k in ("name", "email", "github_login")}


def malformed(record: dict | None) -> str | None:
    """Why a record's `commit_identity` cannot be used, or None (absent or
    whole)."""
    if (record or {}).get("commit_identity") is None or of(record):
        return None
    return ("the record's commit_identity needs name, email and github_login, "
            "all non-empty")


def noreply_email(user_id: int, login: str) -> str:
    return f"{int(user_id)}+{login}@users.noreply.github.com"


WORDS_RE = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(MACHINE_WORDS) +
                      r")(?![A-Za-z0-9])", re.IGNORECASE)


def text_problems(text: str) -> list[str]:
    """What in ``text`` names the tools, as short phrases; [] if nothing."""
    found: list[str] = []
    for m in TRAILER_RE.finditer(text or ""):
        found.append(f"a {m.group(1)} trailer")
    for m in PHRASES_RE.finditer(text or ""):
        found.append(f"\"{m.group(0)}\"")
    for m in WORDS_RE.finditer(text or ""):
        found.append(f"\"{m.group(0)}\"")
    for m in JOB_ID_RE.finditer(text or ""):
        found.append(f"a job id ({m.group(0)})")
    return list(dict.fromkeys(found))


# What the remote already has, as ``repo`` last fetched it. The rule is that
# our push never *introduces* a commit that is not the person's; a commit
# already on one of the remote's branches is not introduced by pushing it
# again. Until 2026-10-20 only ``base`` was excluded, so a branch that merged
# the remote's default branch after it moved past the canonical `main` -- a
# pull request's "merge current main" -- was refused for the remote's own
# merge commits (an adopted project's job 20261020-092524-d5efbb06: GitHub's merge
# of #28, committer GitHub, five seconds after the job's fetch). Only
# meaningful in the canonical checkout, which a job cannot write: in a job's
# clone `refs/remotes/origin/*` is whatever the job made it.
REMOTE_REFS = "--remotes=origin"


def commits(repo: Path, base: str, tip: str) -> list[dict]:
    """The commits a push of ``tip`` would add to the remote: on ``tip``, not
    on ``base`` and not on any branch of the remote as ``repo`` holds it
    (``REMOTE_REFS``), oldest first."""
    sep, end = "\x1f", "\x1e"
    out = subprocess.run(
        ["git", "-C", str(repo), "log", "--reverse",
         f"--format=%H{sep}%an{sep}%ae{sep}%cn{sep}%ce{sep}%B{end}",
         tip, "--not", base, REMOTE_REFS, "--"],
        capture_output=True, text=True, check=True, timeout=60).stdout
    found = []
    for chunk in out.split(end):
        chunk = chunk.lstrip("\n")
        if not chunk:
            continue
        sha, an, ae, cn, ce, body = chunk.split(sep, 5)
        found.append({"sha": sha, "author": (an, ae), "committer": (cn, ce),
                      "message": body})
    return found


def problems(repo: Path, base: str, tip: str, ident: dict) -> list[str]:
    """Every reason the commits a push of ``tip`` would add (``commits``) are
    not ``ident``'s alone, one line each naming the commit; [] when they are.
    ``repo`` is the canonical checkout, never a job's clone."""
    want = (ident["name"], ident["email"].lower())
    out: list[str] = []
    for c in commits(repo, base, tip):
        short = c["sha"][:10]
        for role in ("author", "committer"):
            name, email = c[role]
            if (name, email.lower()) != want:
                out.append(f"{short}: {role} is {name} <{email}>, not "
                           f"{ident['name']} <{ident['email']}>")
        said = text_problems(c["message"])
        if said:
            out.append(f"{short}: the message carries {', '.join(said)}")
    return out


def pr_text(job_dir: Path, fallback_title: str) -> tuple[str, str] | None:
    """(title, body) from the job's ``PR_BODY_FILE``, or None when the job
    wrote none. A leading `# ` line is the title and is not repeated in the
    body; otherwise the title is ``fallback_title``."""
    try:
        text = (job_dir / PR_BODY_FILE).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    if not text:
        return None
    title = fallback_title
    first, _, rest = text.partition("\n")
    if first.startswith("# "):
        title, text = first[2:].strip(), rest.strip()
    return title, text
