#!/usr/bin/env python3
"""Tests for keeping the Market reviewer's Loam pin in step with Loam releases."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="test-marketplace-pin-"))
os.environ["LOAM_ATTENTION_DIR"] = str(TMP / "attention")

sys.path.insert(0, str(ROOT / "lib"))
import loam_attention  # noqa: E402
import loam_marketplace_pin as pin  # noqa: E402
import loam_offline  # noqa: E402
import loam_worktree as wt  # noqa: E402

FAILURES = 0


def check(label: str, got, want) -> None:
    global FAILURES  # noqa: PLW0603
    ok = got == want
    FAILURES += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}"
          + ("" if ok else f"\n      got  {got!r}\n      want {want!r}"))


class FakeGitHub:
    """In-memory mock of the GitHub REST endpoints used by sync_pin."""

    def __init__(self, initial_cfg: dict, check_sequences: list[list[dict]]):
        self.cfg = initial_cfg
        self.cfg_sha = "f" * 40
        self.main_sha = "1" * 40
        self.branches: dict[str, str] = {"main": self.main_sha}
        self.prs: list[dict] = []
        self.comments: list[tuple[int, str]] = []
        self.merged: list[tuple[int, dict]] = []
        self.deleted_branches: list[str] = []
        self.written_configs: list[dict] = []
        self.check_sequences = list(check_sequences)
        self.check_poll = 0
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method: str, path: str, body=None, ok=(200, 201, 204)):
        self.calls.append((method, path))
        if method == "GET" and path.endswith(f"/contents/{pin.CONFIG_PATH}?ref=main"):
            encoded = base64.b64encode(json.dumps(self.cfg).encode()).decode()
            return 200, {"content": encoded, "sha": self.cfg_sha}
        if method == "GET" and path.endswith("/git/ref/heads/main"):
            return 200, {"object": {"sha": self.main_sha}}
        if method == "GET" and "/git/ref/heads/" in path:
            name = path.split("/git/ref/heads/", 1)[1]
            if name in self.branches:
                return 200, {"object": {"sha": self.branches[name]}}
            if 404 in ok:
                return 404, {}
            raise pin.PinSyncError(f"404 {name}")
        if method == "POST" and path.endswith("/git/refs"):
            name = body["ref"].removeprefix("refs/heads/")
            self.branches[name] = body["sha"]
            return 201, {"ref": body["ref"], "object": {"sha": body["sha"]}}
        if method == "PATCH" and "/git/refs/heads/" in path:
            name = path.split("/git/refs/heads/", 1)[1]
            self.branches[name] = body["sha"]
            return 200, {"object": {"sha": body["sha"]}}
        if method == "PUT" and path.endswith(f"/contents/{pin.CONFIG_PATH}"):
            written = json.loads(base64.b64decode(body["content"]).decode())
            self.written_configs.append(written)
            head = "9" * 40
            self.branches[body["branch"]] = head
            return 200, {"commit": {"sha": head}}
        if method == "GET" and "/pulls?state=open" in path:
            return 200, list(self.prs)
        if method == "POST" and path.endswith("/pulls"):
            pr = {"number": 42, "html_url": "https://github.com/loamhq/marketplace/pull/42",
                  "head": {"sha": self.branches[body["head"]]}}
            self.prs.append(pr)
            return 201, pr
        if method == "GET" and "/check-runs" in path:
            idx = min(self.check_poll, len(self.check_sequences) - 1)
            self.check_poll += 1
            return 200, {"check_runs": self.check_sequences[idx]}
        if method == "POST" and "/comments" in path:
            num = int(path.split("/issues/", 1)[1].split("/", 1)[0])
            self.comments.append((num, body["body"]))
            return 201, {"id": 1}
        if method == "PUT" and path.endswith("/merge"):
            num = int(path.split("/pulls/", 1)[1].split("/", 1)[0])
            self.merged.append((num, body))
            return 200, {"merged": True}
        if method == "DELETE" and "/git/refs/heads/" in path:
            name = path.split("/git/refs/heads/", 1)[1]
            self.deleted_branches.append(name)
            self.branches.pop(name, None)
            return 204, {}
        raise AssertionError(f"unexpected call: {method} {path}")


try:
    OLD_SHA = "a" * 40
    NEW_SHA = "b" * 40
    BASE_CFG = {
        "model": "gemini-4-argon",
        "loam": {"repo": "loamhq/loam", "version": "0.2.28", "commit": OLD_SHA},
        "limits": {"context_bytes": 2500000},
    }

    # -- remote URL parsing ---------------------------------------------------------
    check("parse SSH alias remote URL",
          pin.parse_remote_slug("git@github-loamhq-loam:loamhq/loam.git"), "loamhq/loam")
    check("parse HTTPS remote URL",
          pin.parse_remote_slug("https://github.com/loamhq/loam.git"), "loamhq/loam")
    check("parse local bare path returns None",
          pin.parse_remote_slug("/tmp/bare/loam.git"), None)

    # -- validation -----------------------------------------------------------------
    check("rejects branch name instead of release version",
          pin.sync_pin("main", NEW_SHA, api=FakeGitHub(BASE_CFG, []))["state"], "failed")
    check("rejects short commit hash",
          pin.sync_pin("0.2.29", "abc123", api=FakeGitHub(BASE_CFG, []))["state"], "failed")

    # -- already current ------------------------------------------------------------
    fg_cur = FakeGitHub(BASE_CFG, [])
    cur = pin.sync_pin("0.2.28", OLD_SHA, api=fg_cur)
    check("already pinned returns current with no writes",
          (cur["state"], len(fg_cur.calls)), ("current", 1))

    # -- dry run --------------------------------------------------------------------
    fg_dry = FakeGitHub(BASE_CFG, [])
    dry = pin.sync_pin("0.2.29", NEW_SHA, dry_run=True, api=fg_dry)
    check("dry_run returns proposed config and makes no writes",
          (dry["state"], dry["branch"], dry["config"]["loam"], len(fg_dry.calls)),
          ("dry_run", "loam-pin/0.2.29",
           {"repo": "loamhq/loam", "version": "0.2.29", "commit": NEW_SHA}, 1))

    # -- happy path: PR opened, test + proof pass, merged at reviewed head ----------
    fg_ok = FakeGitHub(BASE_CFG, [
        [{"id": 1, "name": "test", "status": "in_progress", "conclusion": None, "html_url": "https://gh/1"}],
        [{"id": 1, "name": "test", "status": "completed", "conclusion": "success", "html_url": "https://gh/1"},
         {"id": 2, "name": "proof", "status": "completed", "conclusion": "success", "html_url": "https://gh/2"}],
    ])
    ok = pin.sync_pin("0.2.29", NEW_SHA, api=fg_ok, poll_interval=0.0)
    check("passing checks merge PR and delete branch",
          (ok["state"], ok["pr_number"], len(fg_ok.merged), fg_ok.merged[0][1]["sha"],
           fg_ok.deleted_branches, fg_ok.written_configs[0]["loam"]),
          ("updated", 42, 1, "9" * 40, ["loam-pin/0.2.29"],
           {"repo": "loamhq/loam", "version": "0.2.29", "commit": NEW_SHA}))
    check("release_section reports moved pin cleanly",
          "Market reviewer pin moved to `0.2.29`" in wt.release_section({
              "tag": "0.2.29", "state": "pushed", "sha": NEW_SHA,
              "detail": "matches manifest.json", "marketplace_pin": ok,
          }), True)

    # -- failing pin move: never merges, leaves PR open, comments, fails loud -------
    fg_fail = FakeGitHub(BASE_CFG, [
        [{"id": 10, "name": "test", "status": "completed", "conclusion": "failure",
          "html_url": "https://github.com/loamhq/marketplace/actions/runs/100/job/10"},
         {"id": 11, "name": "proof", "status": "completed", "conclusion": "failure",
          "html_url": "https://github.com/loamhq/marketplace/actions/runs/101/job/11"}],
    ])
    failed = pin.sync_pin("0.2.30", NEW_SHA, api=fg_fail, poll_interval=0.0)
    check("failing checks leave PR open, never merge, and keep branch",
          (failed["state"], failed["pr_number"], len(fg_fail.merged),
           fg_fail.deleted_branches, len(fg_fail.comments)),
          ("failed", 42, 0, [], 1))
    check("failure detail names failed checks and PR link",
          ("test=failure" in failed["detail"] and "proof=failure" in failed["detail"]
           and "pull/42" in failed["detail"] and "main unchanged" in failed["detail"]), True)
    check("failure PR comment explains main stayed on prior release",
          ("did **not** merge" in fg_fail.comments[0][1] and "`0.2.28`" in fg_fail.comments[0][1]), True)
    rec_line = wt.release_section({
        "tag": "0.2.30", "state": "pushed", "sha": NEW_SHA,
        "detail": "matches manifest.json", "marketplace_pin": failed,
    })
    check("release_section reports failing pin move loudly",
          "**Market reviewer pin move to `0.2.30` FAILED**" in rec_line and "pull/42" in rec_line, True)

    # -- notify_failure writes an attention record when online ----------------------
    import loam_claim_start  # noqa: PLC0415
    with patch.object(loam_offline, "offline", return_value=False), \
            patch.object(loam_claim_start, "unclaimed", return_value=False):
        pin.notify_failure(failed, kick=False)
    records = [p for p in (TMP / "attention").glob("*.json") if p.name != "seen.json"]
    check("notify_failure writes an attention fact for the system persona", len(records), 1)
    saved = json.loads(records[0].read_text())
    check("attention fact names the failed release and PR URL",
          ("0.2.30" in saved["fact"], saved["evidence"]), (True, failed["pr_url"]))

    # -- _sync_marketplace_pin skips non-Loam repos and offline runs ----------------
    bare = TMP / "other-repo"
    bare.mkdir()
    subprocess.run(["git", "-C", str(bare), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(bare), "remote", "add", "origin", "https://github.com/example/other.git"], check=True)
    with patch.object(loam_offline, "offline", return_value=False):
        check("_sync_marketplace_pin skips non-loamhq/loam repos",
              wt._sync_marketplace_pin(bare, "0.2.30", NEW_SHA), None)

    loam_repo = TMP / "loam-repo"
    loam_repo.mkdir()
    subprocess.run(["git", "-C", str(loam_repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(loam_repo), "remote", "add", "origin", "git@github-loamhq-loam:loamhq/loam.git"], check=True)
    with patch.object(loam_offline, "offline", return_value=True):
        check("_sync_marketplace_pin skips when offline",
              wt._sync_marketplace_pin(loam_repo, "0.2.30", NEW_SHA), None)
    with patch.object(loam_offline, "offline", return_value=False), \
            patch.object(pin, "sync_pin", return_value=failed) as sp, \
            patch.object(pin, "notify_failure") as nf:
        got_fail = wt._sync_marketplace_pin(loam_repo, "0.2.30", NEW_SHA)
        check("_sync_marketplace_pin calls sync_pin and notify_failure on failure",
              (got_fail["state"], sp.call_count, nf.call_count), ("failed", 1, 1))

    print(f"\n{'FAILED' if FAILURES else 'OK'}: {FAILURES} failure(s)")
    sys.exit(1 if FAILURES else 0)
finally:
    shutil.rmtree(TMP, ignore_errors=True)
