#!/usr/bin/env python3
"""Exercise preview, consent, HTTP relay and issuer follow-up in a private fixture.

Run: python3 tests/test_report_delivery.py
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


def run():
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        config = folder / "loam.toml"
        config.write_text(f'''[paths]
home = "{folder}"
root = "{folder}/checkout"
vault = "{folder}/vault"
workspaces = "{folder}/workspaces"
state = "{folder}/state"
logs = "{folder}/logs"
plugins = "{folder}/plugins"
[owner]
name = "River Sample"
[feedback]
repo = "test/loam"
''')
        os.environ["LOAM_CONFIG"] = str(config)
        os.environ.pop("LOAM_AI_LABS", None)
        os.environ.pop("LOAM_JOB_TREE", None)
        os.environ.update(LOAM_WAKE="cron:sync", LOAM_AGENT="wren")
        import loam_feedback as fb
        import loam_report_delivery as delivery
        from loam_report_relay import Relay
        checkout = folder / "checkout"
        checkout.mkdir()
        (checkout / "manifest.json").write_text('{"version":"1.0.0"}')
        (folder / "logs").mkdir()
        row = dict(ts=datetime.now(timezone.utc).isoformat(), lane="cron:sync",
                   ok=False, stage="invoke", returncode=72, error="a note's private contents")
        (folder / "logs/wake-wren.jsonl").write_text(json.dumps(row) + "\n")
        issues = []

        def fake_github(repo, payload):
            assert repo == "test/loam"
            issues.append(payload)
            return 42

        def cli(*args, ok=True):
            p = subprocess.run([str(ROOT / "bin/loam"), "report", *args], capture_output=True,
                               text=True, stdin=subprocess.DEVNULL, timeout=30)
            assert (p.returncode == 0) == ok, (p.returncode, p.stdout, p.stderr)
            return p.stdout if ok else p.stderr

        def post(url, raw):
            req = urllib.request.Request(url, data=raw, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as exc:
                return exc.code, json.load(exc)

        assert "Offer once" in cli("offer", "market:timer")
        assert "remembered" in cli("decline", "market:timer")
        assert "Stay quiet" in cli("offer", "market:timer")
        assert "available" in cli("idea", "--title", "Calendar", "--body", "Group by day.")
        with Relay(0, "test/loam", folder / "relay.db", fake_github) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}/v1/reports"
            with config.open("a") as out:
                out.write(f'relay = "{url}"\n')
            draft = json.loads(cli("bug", "--title", "A timer stopped for River Sample",
                                   "--body", "The timer failed at 192.0.2.10.", "--json"))
            assert draft["title"] == "A timer stopped for <name>"
            assert "invoke (exit 72)" in draft["body"] and "Diagnostics" in draft["body"]
            assert "private contents" not in draft["body"] and "192.0.2.10" not in draft["body"]
            assert not issues
            ident = draft["id"]
            (folder / "logs/wake-wren.jsonl").unlink()
            assert json.loads(cli("preview", ident, "--json")) == draft
            assert "owner" in cli("send", ident, ok=False) and not issues
            assert cli("send", ident, "--yes").strip() == "https://github.com/test/loam/issues/42"
            assert issues == [{"title": draft["title"], "body": draft["body"],
                               "labels": ["bug", "sent-from-Loam"]}]
            print("PASS  approved preview exactly matches the test issue", issues[0]["title"])
            assert cli("send", ident, "--yes").strip().endswith("/42") and len(issues) == 1
            assert cli("track", ident, "https://github.com/test/loam/issues/42").strip().endswith("/42")
            assert "different" in cli("track", ident, "https://github.com/test/loam/issues/43", ok=False)
            assert "repository" in cli("track", ident, "https://example.test/42", ok=False)
            assert post(url, b"x" * (delivery.MAX_BYTES + 1))[0] == 413
            assert post(url, b'{"text":"hello"}')[0] == 400
            with closing(delivery.database()) as db:
                install = db.execute("SELECT value FROM settings WHERE name='install'").fetchone()[0]
            packet = {"format": delivery.FORMAT, "install": install,
                      **{k: draft[k] for k in ("repo", "kind", "body", "title")}}
            assert post(url, json.dumps(packet).encode())[1]["url"].endswith("/42")
            assert len(issues) == 1
            assert post(url, json.dumps(dict(packet, title="Call a@example.test")).encode())[0] == 422
            for i in range(4):
                assert post(url, json.dumps(dict(packet, title=f"Timer {i}")).encode())[0] == 200
            assert post(url, json.dumps(dict(packet, title="Limit")).encode())[0] == 429
            print("PASS  retry creates one issue; oversized 413, non-Loam 400, privacy 422, rate 429")
            server.shutdown()
            thread.join()
        issue = {"state": "closed", "comments": 1, "labels": [], "user": {"id": 1}}
        comments = [{"id": 17, "author_association": "MEMBER", "user": {"id": 2, "type": "User"}}]
        def get(path):
            if path.endswith("/42"):
                return issue
            if "/comments?" in path:
                return comments
            assert path.endswith("/git/ref/tags/1.0.1")
            return {"ref": "refs/tags/1.0.1"}

        # Exercise the maintainer adapter as well as the HTTP/client flow.
        # The GitHub transport here is entirely in-process.
        import loam_github_app as github
        from loam_report_relay import file_issue
        created = []
        def api(method, path, token, body=None, ok=()):
            assert token == "fixture"
            if method == "GET":
                assert path.startswith("/repos/test/loam/labels/")
                return 404, {}
            if path == "/repos/test/loam/labels":
                created.append(body["name"])
                return 201, body
            assert path == "/repos/test/loam/issues" and created == issues[0]["labels"]
            assert body == issues[0]
            return 201, {"number": 42}

        with patch.object(github, "load", return_value={}), \
                patch.object(github, "route", return_value=({}, {"installation_id": 7})), \
                patch.object(github, "_mint", return_value={"token": "fixture"}) as mint, \
                patch.object(github, "api", side_effect=api):
            assert file_issue("test/loam", issues[0]) == 42
            mint.assert_called_once_with({}, 7, {"repositories": ["loam"],
                                                 "permissions": {"issues": "write"}})
        print("PASS  bot adapter creates missing labels and requests only issues-write for one repo")
        import loam_attention
        loam_attention.STATE = folder / "attention"
        said = []
        def notify(line, **kwargs):
            said.append(line)
            return loam_attention.tell(line, kick=False, **kwargs)

        delivery.check(get, notify)
        delivery.check(get, notify)
        assert len(said) == 1 and "maker replied" in said[0]
        print("PASS ", said[-1])
        issue["labels"] = [{"name": "fixed-in:1.0.1"}]
        delivery.check(get, notify)
        delivery.check(get, notify)
        assert len(said) == 2 and "fixed in 1.0.1" in said[-1]
        assert sorted(r["fact"] for r in loam_attention.pending()) == sorted(said)
        print("PASS ", said[-1])
        with patch("loam_report_diagnostics.collect", return_value="x" * 7000):
            capped = fb.draft("bug", "Timer", "Stopped.")
        assert "x" * 4097 not in capped["body"]
        print("PASS  bounded diagnostics; check stays quiet after each update")


if __name__ == "__main__":
    run()
