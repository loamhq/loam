"""A landing that reports GitHub's checks instead of running its own (since 2026-10-13).

A project that lands by pull request may set ``"github_checks": true`` in its
registry record. Off unless set: a project's own
call, never a Loam-wide policy (the owner's word when it was built for the
first project that wanted it).

Why: the launcher's own test run on that project reported "tested" while 72
of its 103 tests were skipped for want of a database, and failed outright the
same morning on a pnpm store mismatch in a reused tree. Its repository already
runs the whole suite on every pull request, against a real database, on a
clean machine. That is the gate; the landing's job is to say what it said.

What a landing does with it (bin/engineer-job ``report_checks``): once the
broker has pushed the branch and opened or updated the pull request, it lets
go of its run slot -- the box's other jobs need it more than a poll does --
and reads the check runs on the pull request's head commit until every one
has finished or ``WAIT_SECONDS`` pass, whichever is first. The receipt's
landing line carries the outcome (passed / failed / still running), each
check's name and conclusion, and the link to the run. A failed check is
reported and undoes nothing: the push stands, and the project's owners merge.

Read with the job's own read token from the broker's ``token`` route, which
carries every repository permission the installation has accepted
(lib/loam_github_app.job_permissions). A 403 is reported as "CI result
unavailable: missing permission", with which permission to grant, and nothing
is read another way. Measured on an organisation's own app installation on
2026-10-13: Checks read and Actions read granted, commit statuses not.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

import loam_config as lc
import loam_offline

API = "https://api.github.com"
SETTING = "github_checks"

# The bound on the wait. The first project's checks took one to five minutes; fifteen is
# the brief's "plenty", and a run still going then is reported as such.
WAIT_SECONDS = 15 * 60
POLL_SECONDS = 20
# Actions registers a check run a few seconds after the push; until one
# exists there is nothing to wait for but the run to appear.

# A conclusion that is not a failure. Anything else that is completed --
# failure, cancelled, timed_out, action_required, startup_failure, stale --
# is one.
GOOD = frozenset({"success", "neutral", "skipped"})


class Unavailable(Exception):
    """GitHub refused the read: a permission the installation lacks."""


def enabled(project) -> bool:
    return bool(project.pull_request and project.record.get(SETTING) is True)


def _token(repo: str) -> str:
    import loam_github  # noqa: PLC0415
    return loam_github.ask_broker("token", {"repository": repo}, timeout=60)["token"]


def _get(path: str, token: str) -> dict:
    loam_offline.guard_url(f"{API}{path}", "a GitHub CI read")
    req = urllib.request.Request(
        f"{API}{path}",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "User-Agent": "loam-runtime"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 404):
            # 404 is what GitHub answers a token that may not see a resource.
            raise Unavailable(f"GitHub answered {exc.code} for {path.split('?')[0]}") from None
        raise


def read(repo: str, sha: str, token: str | None = None, get=None) -> dict:
    """One look at ``sha``'s check runs and workflow runs. ``get(path)`` is for
    tests; it raises Unavailable as ``_get`` does."""
    if get is None:
        token = token or _token(repo)
        get = lambda path: _get(path, token)  # noqa: E731
    runs = get(f"/repos/{repo}/commits/{sha}/check-runs?per_page=100")
    checks = [{"name": c.get("name") or "?", "status": c.get("status"),
               "conclusion": c.get("conclusion"),
               "url": c.get("html_url") or c.get("details_url")}
              for c in runs.get("check_runs") or []]
    try:
        flows = get(f"/repos/{repo}/actions/runs?head_sha={sha}&per_page=50")
        workflows = [{"name": w.get("name") or "?", "status": w.get("status"),
                      "conclusion": w.get("conclusion"), "url": w.get("html_url")}
                     for w in flows.get("workflow_runs") or []]
    except Unavailable:
        workflows = None       # the check runs alone decide; the links are theirs
    return {"checks": checks, "workflows": workflows}


def outcome(seen: dict) -> str:
    """passed | failed | running | none, of one look."""
    checks, workflows = seen["checks"], seen.get("workflows") or []
    if not checks:
        return "running" if workflows else "none"
    if any(c["status"] == "completed" and c["conclusion"] not in GOOD
           for c in checks):
        # A failure is final whatever else is still going.
        return "failed"
    if any(c["status"] != "completed" for c in checks) or any(
            w["status"] != "completed" for w in workflows):
        return "running"
    return "passed"


def wait(repo: str, sha: str, pr_url: str | None, *, bound: float = WAIT_SECONDS,
         poll: float = POLL_SECONDS, say=None, get=None, sleep=time.sleep,
         clock=time.monotonic) -> dict:
    """Wait for ``sha``'s checks, at most ``bound`` seconds, and return

        state      passed | failed | running | none | unavailable | error
        checks     [{name, status, conclusion, url}]
        links      the workflow runs' URLs, else the pull request's checks tab
        waited_s   how long it waited
        detail     why, on unavailable or error

    Never raises: a landing that has pushed is not undone by what this finds.
    """
    say = say or (lambda _text: None)
    started = clock()
    seen = {"checks": [], "workflows": []}
    state, detail = "none", None
    token = None
    while True:
        try:
            if get is None and token is None:
                token = _token(repo)
            seen = read(repo, sha, token=token, get=get)
            state = outcome(seen)
        except Unavailable as exc:
            state, detail = "unavailable", str(exc)
            break
        except Exception as exc:  # noqa: BLE001 -- reported, never raised
            state, detail = "error", str(exc)[:300]
            break
        if state in ("passed", "failed"):
            break
        if clock() - started >= bound:
            break
        say(f"landing: waiting for GitHub's checks on {sha[:7]} ({state})")
        sleep(poll)
    links = [w["url"] for w in seen.get("workflows") or [] if w.get("url")]
    if not links and pr_url:
        links = [f"{pr_url}/checks"]
    return {"state": state, "repo": repo, "sha": sha, "checks": seen["checks"], "links": links,
            "waited_s": round(clock() - started), "detail": detail,
            "bound_s": round(bound)}


def text(ci: dict | None) -> str:
    """The landing line's clause on CI, in place of "tested"/"untested"."""
    if not ci:
        return ("GitHub's checks **not read** by this landing (a mid-job "
                "`--land` does not wait for them; the job's final landing does)")
    state, links = ci.get("state"), ci.get("links") or []
    link = f" ({', '.join(links)})" if links else ""
    checks = "; ".join(
        f"`{c['name']}` {c['conclusion'] or c['status'] or '?'}"
        for c in ci.get("checks") or [])
    minutes = f"{(ci.get('waited_s') or 0) / 60:.1f} min"
    if state == "unavailable":
        return (f"**CI result unavailable: missing permission** — {ci.get('detail')}. "
                f"{lc.OWNER_NAME} grants the GitHub App installed on "
                f"{(ci.get('repo') or 'the repository').split('/')[0]} read "
                f"access to Checks (the app's settings, Permissions → "
                f"Repository permissions → Checks: Read-only, and Actions: "
                f"Read-only for the run's link; then they accept the change on "
                f"the installation); "
                f"nothing was read another way{link}")
    if state == "error":
        return f"**CI result unavailable** — {ci.get('detail')}{link}"
    if state == "none":
        return (f"**CI: no checks reported** on `{ci['sha'][:7]}` within "
                f"{minutes}{link}")
    if state == "running":
        return (f"**CI still running** after {minutes} (the wait is bounded at "
                f"{round((ci.get('bound_s') or WAIT_SECONDS) / 60)} min): "
                f"{checks or 'no check run yet'}{link}")
    word = "passed" if state == "passed" else "FAILED"
    tail = (" — the push stands; a failed check is reported, not undone"
            if state == "failed" else "")
    return (f"**CI {word}** on `{ci['sha'][:7]}` after {minutes}: {checks}"
            f"{link}{tail}")
