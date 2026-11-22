"""Keep the Market reviewer's Loam pin moving with Loam releases.

When a Loam release tag (``x.y.z``) is pushed to ``loamhq/loam``, this module
opens a branch pull request in ``loamhq/marketplace`` updating
``review/config.json`` to that named release commit, waits for both
``review-tests`` (``test``) and ``review-proof`` (``proof``) to pass, and
merges the pull request. If either check fails, the pull request stays open,
``main`` is unchanged, and the failure is returned loudly to the caller.

CLI:
    python3 lib/loam_marketplace_pin.py [--dry-run] VERSION COMMIT
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import loam_offline

API = "https://api.github.com"
API_VERSION = "2022-11-28"
DEFAULT_MARKETPLACE = "loamhq/marketplace"
DEFAULT_LOAM_REPO = "loamhq/loam"
REQUIRED_CHECKS = ("test", "proof")
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
SHA = re.compile(r"^[0-9a-f]{40}$")
CONFIG_PATH = "review/config.json"


class PinSyncError(Exception):
    """Raised internally when a GitHub API step fails; caught by sync_pin."""


def parse_remote_slug(url: str) -> str | None:
    """Return ``owner/repo`` (lowercase) from a GitHub SSH or HTTPS remote URL."""
    url = (url or "").strip()
    if not url:
        return None
    m = re.match(r"^(?:https?://github\.com/|ssh://git@[^/]+/|git@[^:]+:)([^/\s]+/[^/\s]+?)(?:\.git)?/?$", url)
    return m.group(1).lower() if m else None


def repo_slug(repo: Path, remote: str = "origin") -> str | None:
    done = subprocess.run(
        ["git", "-C", str(repo), "remote", "get-url", remote],
        capture_output=True, text=True, check=False,
    )
    if done.returncode != 0:
        return None
    return parse_remote_slug(done.stdout.strip())


def resolve_token(marketplace: str = DEFAULT_MARKETPLACE) -> str | None:
    """Resolve a GitHub token with write access to ``marketplace`` without printing it."""
    org = marketplace.split("/", 1)[0] if "/" in marketplace else None
    try:
        import loam_github_app as app  # noqa: PLC0415
        for candidate_org in ([org, None] if org else [None]):
            try:
                tok = app.user_token(org=candidate_org) if candidate_org else app.user_token()
                if tok:
                    return tok
            except Exception:
                continue
    except Exception:
        pass
    env = {k: v for k, v in os.environ.items() if k != "GITHUB_TOKEN"}
    try:
        done = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, env=env, timeout=15)
        if done.returncode == 0 and done.stdout.strip():
            return done.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def make_api(token: str):
    """Build a minimal GitHub REST helper returning ``(status, json_body)``."""
    def call(method: str, path: str, body: dict | None = None,
             ok: tuple[int, ...] = (200, 201, 204)) -> tuple[int, dict | list]:
        url = path if path.startswith("https://") else f"{API}{path}"
        loam_offline.guard_url(url, f"GitHub {method} {path.split('?')[0]}")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "loam-release-pin-sync",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                raw = resp.read()
                parsed = json.loads(raw) if raw else {}
                if resp.status not in ok:
                    raise PinSyncError(f"GitHub {method} {path} answered {resp.status}")
                return resp.status, parsed
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                msg = json.loads(raw).get("message") or raw[:200]
            except ValueError:
                msg = raw[:200]
            if exc.code in ok:
                try:
                    return exc.code, json.loads(raw) if raw else {}
                except ValueError:
                    return exc.code, {}
            raise PinSyncError(f"GitHub {method} {path} failed ({exc.code}): {msg}") from None
        except (OSError, TimeoutError) as exc:
            raise PinSyncError(f"GitHub {method} {path} error: {exc}") from None
    return call


def _latest_checks(check_runs: list[dict]) -> dict[str, dict]:
    """Map check name -> most recent run dict (highest id)."""
    by_name: dict[str, dict] = {}
    for run in check_runs or []:
        name = run.get("name")
        if not name:
            continue
        prev = by_name.get(name)
        if prev is None or int(run.get("id") or 0) >= int(prev.get("id") or 0):
            by_name[name] = {
                "id": run.get("id"),
                "name": name,
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "html_url": run.get("html_url") or "",
            }
    return by_name


def _wait_checks(api, marketplace: str, head_sha: str, required: tuple[str, ...],
                 *, wait_seconds: int, poll_interval: float, grace_seconds: int, say) -> tuple[bool, dict[str, dict], str]:
    deadline = time.monotonic() + wait_seconds
    grace_deadline = time.monotonic() + min(grace_seconds, wait_seconds)
    latest: dict[str, dict] = {}
    while True:
        _, body = api("GET", f"/repos/{marketplace}/commits/{head_sha}/check-runs")
        latest = _latest_checks((body or {}).get("check_runs") or [])
        present = {name: latest[name] for name in required if name in latest}
        missing = [name for name in required if name not in latest]
        if not missing and all(c["status"] == "completed" for c in present.values()):
            failed = [c for c in present.values() if c["conclusion"] != "success"]
            if not failed:
                return True, present, "all required checks passed"
            desc = ", ".join(
                f"{c['name']}={c['conclusion']}" + (f" ({c['html_url']})" if c["html_url"] else "")
                for c in failed
            )
            return False, present, f"reviewer checks failed: {desc}"
        now = time.monotonic()
        if missing and now >= grace_deadline and not present:
            return False, present, f"required checks never started ({', '.join(missing)})"
        if now >= deadline:
            pending = [name for name in required if name not in latest or latest[name]["status"] != "completed"]
            return False, present, f"timed out waiting for checks ({', '.join(pending)})"
        if say:
            states = ", ".join(f"{n}:{latest[n]['status']}/{latest[n]['conclusion'] or '-'}" if n in latest else f"{n}:waiting"
                               for n in required)
            say(f"marketplace pin checks on {head_sha[:12]}: {states}")
        time.sleep(poll_interval)


def sync_pin(tag: str, sha: str, *,
             marketplace: str = DEFAULT_MARKETPLACE,
             loam_repo: str = DEFAULT_LOAM_REPO,
             token: str | None = None,
             dry_run: bool = False,
             wait_seconds: int = 900,
             poll_interval: float = 5.0,
             grace_seconds: int = 120,
             required_checks: tuple[str, ...] = REQUIRED_CHECKS,
             say=None,
             api=None) -> dict:
    """Move ``marketplace``'s ``review/config.json`` Loam pin to ``(tag, sha)`` via a checked PR.

    Never raises; returns ``{"state": "updated"|"current"|"dry_run"|"failed", ...}``.
    """
    tag = (tag or "").strip()
    sha = (sha or "").strip().lower()
    if not SEMVER.match(tag):
        return {"state": "failed", "version": tag, "commit": sha,
                "detail": f"{tag!r} is not a release version (x.y.z)"}
    if not SHA.match(sha):
        return {"state": "failed", "version": tag, "commit": sha,
                "detail": f"{sha!r} is not a 40-character commit SHA"}

    if api is None:
        tok = token or resolve_token(marketplace)
        if not tok:
            return {"state": "failed", "version": tag, "commit": sha,
                    "detail": f"no GitHub token available to update {marketplace}"}
        api = make_api(tok)

    branch = f"loam-pin/{tag}"
    pr_number: int | None = None
    pr_url = ""
    try:
        _, cfg_file = api("GET", f"/repos/{marketplace}/contents/{CONFIG_PATH}?ref=main")
        cfg = json.loads(base64.b64decode(cfg_file["content"]).decode("utf-8"))
        loam = cfg.get("loam") or {}
        prev_ver = loam.get("version") or ""
        prev_sha = (loam.get("commit") or "").lower()
        if prev_ver == tag and prev_sha == sha:
            return {"state": "current", "version": tag, "commit": sha,
                    "detail": f"already pinned to {tag} ({sha[:12]})"}

        updated_cfg = dict(cfg)
        updated_cfg["loam"] = {
            "repo": loam.get("repo") or loam_repo,
            "version": tag,
            "commit": sha,
        }
        new_text = json.dumps(updated_cfg, indent=2, ensure_ascii=False) + "\n"
        if dry_run:
            return {
                "state": "dry_run",
                "version": tag,
                "commit": sha,
                "previous_version": prev_ver,
                "previous_commit": prev_sha,
                "branch": branch,
                "required_checks": list(required_checks),
                "config": updated_cfg,
                "detail": (f"would open PR `{branch}` on {marketplace} moving loam pin "
                           f"{prev_ver or prev_sha[:12]} -> {tag} ({sha[:12]}), wait for "
                           f"{', '.join(required_checks)}, and merge on pass"),
            }

        _, main_ref = api("GET", f"/repos/{marketplace}/git/ref/heads/main")
        main_sha = main_ref["object"]["sha"]

        st, _ = api("GET", f"/repos/{marketplace}/git/ref/heads/{branch}", ok=(200, 404))
        if st == 200:
            api("PATCH", f"/repos/{marketplace}/git/refs/heads/{branch}",
                {"sha": main_sha, "force": True})
        else:
            api("POST", f"/repos/{marketplace}/git/refs",
                {"ref": f"refs/heads/{branch}", "sha": main_sha})

        _, put_res = api("PUT", f"/repos/{marketplace}/contents/{CONFIG_PATH}", {
            "message": f"Pin reviewer Loam to {tag} ({sha[:12]})",
            "content": base64.b64encode(new_text.encode("utf-8")).decode("ascii"),
            "sha": cfg_file["sha"],
            "branch": branch,
            "committer": {"name": "loam-marketplace", "email": "marketplace@users.noreply.github.com"},
            "author": {"name": "loam-marketplace", "email": "marketplace@users.noreply.github.com"},
        })
        pr_head_sha = put_res["commit"]["sha"]

        owner = marketplace.split("/", 1)[0]
        _, existing_prs = api("GET", f"/repos/{marketplace}/pulls?state=open&head={owner}:{branch}&base=main")
        if existing_prs:
            pr = existing_prs[0]
        else:
            _, pr = api("POST", f"/repos/{marketplace}/pulls", {
                "title": f"Pin reviewer Loam to {tag}",
                "head": branch,
                "base": "main",
                "body": (
                    f"Moves `review/config.json` to Loam release `{tag}` (`{sha}`).\n\n"
                    f"Both `review-tests` (`test`) and `review-proof` (`proof`) must pass before "
                    f"this pin change merges to `main`."
                ),
            })
        pr_number = pr["number"]
        pr_url = pr["html_url"]
        if say:
            say(f"marketplace pin PR #{pr_number} opened for Loam {tag}: {pr_url}")

        ok, checks, why = _wait_checks(
            api, marketplace, pr_head_sha, required_checks,
            wait_seconds=wait_seconds, poll_interval=poll_interval,
            grace_seconds=grace_seconds, say=say,
        )
        if not ok:
            comment = (
                f"Reviewer pin move to Loam `{tag}` (`{sha[:12]}`) did **not** merge because "
                f"{why}.\n\n`main` remains on `{prev_ver or prev_sha[:12]}`."
            )
            try:
                api("POST", f"/repos/{marketplace}/issues/{pr_number}/comments", {"body": comment})
            except PinSyncError:
                pass
            return {
                "state": "failed",
                "version": tag,
                "commit": sha,
                "pr_number": pr_number,
                "pr_url": pr_url,
                "checks": checks,
                "detail": f"{why}; PR #{pr_number} left open and {marketplace} main unchanged ({pr_url})",
            }

        api("PUT", f"/repos/{marketplace}/pulls/{pr_number}/merge", {
            "sha": pr_head_sha,
            "merge_method": "squash",
            "commit_title": f"Pin reviewer Loam to {tag} (#{pr_number})",
        })
        try:
            api("DELETE", f"/repos/{marketplace}/git/refs/heads/{branch}", ok=(204, 404, 422))
        except PinSyncError:
            pass
        return {
            "state": "updated",
            "version": tag,
            "commit": sha,
            "pr_number": pr_number,
            "pr_url": pr_url,
            "checks": checks,
            "detail": f"moved to {tag} ({sha[:12]}) via PR #{pr_number} ({pr_url}); "
                      f"{', '.join(required_checks)} passed",
        }
    except PinSyncError as exc:
        return {
            "state": "failed",
            "version": tag,
            "commit": sha,
            "pr_number": pr_number,
            "pr_url": pr_url,
            "detail": str(exc),
        }


def notify_failure(pin: dict, *, kick: bool = True) -> None:
    """Alert the system persona via loam_attention when a release pin move fails."""
    if loam_offline.offline() or pin.get("state") != "failed":
        return
    try:
        import loam_attention  # noqa: PLC0415
        ver = pin.get("version") or "?"
        loam_attention.tell(
            f"Moving the Market reviewer's Loam pin to {ver} failed; marketplace main was left unchanged.",
            by="loam-release",
            key=f"marketplace-pin-{ver}",
            evidence=pin.get("pr_url") or "",
            detail=pin.get("detail") or "",
            kick=kick,
        )
    except Exception:
        pass


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="loam_marketplace_pin.py", description=__doc__.splitlines()[0])
    ap.add_argument("version")
    ap.add_argument("commit")
    ap.add_argument("--marketplace", default=DEFAULT_MARKETPLACE)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--wait-seconds", type=int, default=900)
    args = ap.parse_args(argv)
    res = sync_pin(
        args.version, args.commit,
        marketplace=args.marketplace, dry_run=args.dry_run,
        wait_seconds=args.wait_seconds, say=lambda msg: print(msg, file=sys.stderr),
    )
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 1 if res.get("state") == "failed" else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
