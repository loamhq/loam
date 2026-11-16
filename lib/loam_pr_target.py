"""A job that updates an open pull request instead of opening one (since 2026-10-09).

A project that lands by pull request opens a new one for every job
(lib/loam_worktree._land_pr_locked). That is wrong for the commonest kind of
follow-up: a reviewer asks for changes "pushed to the same PR", and a second
pull request is the one thing they did not ask for. Caused on 2026-10-08 on
an adopted project: the job did the revision, wrote that no second PR must be
opened, and its landing opened #165 beside #152 anyway, because the job's
prose is not an instruction the launcher takes and nothing else said which
pull request it was.

**The brief names it, in its title**: a title that begins ``Updates #<n>``
(``Updates #152: rebase onto main and the atomic preflight``). The title,
because it is the one field every persona's tool already sends -- a new tool
parameter reaches a conversation only once its MCP server restarts -- and
because the receipt's filename then says which pull request the job was for.
Nothing in the brief's body is read for it.

What follows from a title that names one:

- **at brief time** (``refusal``, in ``accept`` and again in ``preflight``):
  refused unless the project lands by pull request and ``#<n>`` is open on
  its repository with its head on that same repository. Read with the job's
  own read token from the broker's ``token`` route, which is live today.
- **at landing**: the broker pushes the job's branch to that pull request's
  head branch, fast-forward only, and refuses with nothing pushed when the
  head is not an ancestor of the job's commit
  (lib/loam_github_app.update_pull_request). No new pull request, no force
  push, nothing to the default branch.
- **the reply**: what the job writes to ``REPLY_FILE`` in its job directory
  is posted on the pull request as a comment, with the same token as the push
  (the person's on a ``commit_identity`` project), and only after the push.
  A permission GitHub refuses is reported with what to grant, and nothing is
  posted another way.

A title that names none behaves exactly as before.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

import loam_offline

TITLE_RE = re.compile(r"^\s*Updates\s+#(\d+)\b", re.IGNORECASE)

# The reply a job posts on the pull request it updates, in its job directory.
REPLY_FILE = "pull-request-reply.md"

API = "https://api.github.com"


def of_title(title: str | None) -> int | None:
    """The pull request a brief's title names, or None."""
    m = TITLE_RE.match(title or "")
    return int(m.group(1)) if m else None


def reply_text(job_dir: Path) -> str:
    try:
        return (job_dir / REPLY_FILE).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def fetch(repo: str, number: int) -> dict | None:
    """``GET /repos/<repo>/pulls/<n>`` with a read token the broker mints for
    the repository, or None on 404. Raises loam_github.GitHubError."""
    loam_offline.guard_url(f"{API}/repos/{repo}/pulls/{number}", "a GitHub PR read")
    import loam_github  # noqa: PLC0415
    token = loam_github.ask_broker("token", {"repository": repo}, timeout=60)["token"]
    req = urllib.request.Request(
        f"{API}/repos/{repo}/pulls/{number}",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "User-Agent": "loam-runtime"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise loam_github.GitHubError(
            f"GitHub returned {exc.code} for pull request #{number}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise loam_github.GitHubError(f"could not reach GitHub: {exc}") from None


def refusal(project, title: str | None) -> str | None:
    """Why a brief with this title must not start, or None. None, without a
    call anywhere, for a title that names no pull request."""
    number = of_title(title)
    if number is None:
        return None
    if project.platform or project.record.get("landing") != "pull_request":
        return (f"Refused before starting: the title names pull request "
                f"#{number}, and {project.name} does not land by pull request. "
                f"Drop `Updates #{number}` from the title.")
    import loam_github  # noqa: PLC0415
    try:
        pr = fetch(project.repo, number)
    except (loam_github.GitHubError, KeyError, TypeError) as exc:
        return (f"Refused before starting: the title names pull request "
                f"#{number}, and whether it is open on {project.repo} could "
                f"not be read ({exc}). Send it again once GitHub answers.")
    head_repo = (((pr or {}).get("head") or {}).get("repo") or {}).get("full_name") or ""
    if pr is None or pr.get("state") != "open":
        state = "does not exist" if pr is None else f"is {pr.get('state')}"
        return (f"Refused before starting: the title names pull request "
                f"#{number}, which {state} on {project.repo}; a job updates an "
                f"open pull request or opens a new one. Name an open one, or "
                f"drop `Updates #{number}` from the title to open a new one.")
    if head_repo.lower() != project.repo.lower():
        return (f"Refused before starting: pull request #{number}'s branch is "
                f"on {head_repo or 'a deleted repository'}, not {project.repo}, "
                f"and only a branch on {project.repo} is pushed to.")
    return None


def update(project_name: str, branch: str, number: int, reply: str) -> dict:
    """The broker's push and reply (lib/loam_github_app.update_pull_request)."""
    import loam_github  # noqa: PLC0415
    return loam_github.ask_broker("pull-request-update", {
        "project": project_name, "branch": branch, "number": number,
        "reply": reply})
