"""Exercise the real Settings and tool code against a local Hetzner fixture."""

import json
import os
import select
import stat
import subprocess
import sys
import threading
import unittest
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import loam_contributions as contributions
import loam_hetzner as api
import loam_keys as keys
import loam_plugins as plugins

PERSONAL = "fixture-personal-token-1"
WORK = "fixture-work-token-2"
ROTATED = "fixture-rotated-token-3"
BAD = "fixture-refused-token-4"
HOST = "42"


class HetznerTest(unittest.TestCase):
    def setUp(self):
        stack = self.enterContext(ExitStack())
        tmp = Path(stack.enter_context(TemporaryDirectory()))
        self.calls = []
        calls = self.calls

        class FakeHetzner(BaseHTTPRequestHandler):
            def handle_request(self):
                token = self.headers.get("Authorization", "").removeprefix("Bearer ")
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"null")
                calls.append((self.command, self.path, token, body))
                status = 200
                headers = {}
                result = {"echo": [token, {token: f"Bearer {token}"}]}
                if self.path == "/instance-id":
                    response = HOST.encode()
                else:
                    if token not in {PERSONAL, WORK, ROTATED}:
                        status = 401
                    elif self.path == "/v1/pricing":
                        result["pricing"] = {"server_types": [{"name": "example-type", "prices": [
                            {"location": "hel1", "price_monthly": {"net": "10.00"}}]}]}
                    elif self.path == "/v1/server_types":
                        result["server_types"] = [{"name": "example-type", "memory": 8,
                                                   "locations": [{"name": "hel1"}]}]
                    elif self.path == "/v1/redirect":
                        status = 302
                        headers["Location"] = f"http://127.0.0.1:{self.server.server_port}/leak"
                    if self.command == "DELETE" and status == 200:
                        status = 204
                    response = json.dumps(result).encode() if status != 204 else b""
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(response)

            do_GET = do_POST = do_DELETE = handle_request

            def log_message(self, *args):
                pass

        server = stack.enter_context(ThreadingHTTPServer(("127.0.0.1", 0), FakeHetzner))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        stack.callback(server.shutdown)
        origin = f"http://127.0.0.1:{server.server_port}"
        self.API = origin + "/v1"
        stack.enter_context(patch.object(api, "API", self.API))
        stack.enter_context(patch.object(api, "METADATA", origin + "/instance-id"))
        stack.enter_context(patch.dict(os.environ, {
            "LOAM_HETZNER_URL": self.API,
            "LOAM_PLUGIN_STATE": str(tmp / "state"),
            "LOAM_PLUGIN_SHIPPED": str(ROOT / "plugins"),
            "LOAM_PLUGINS_DIR": str(tmp / "plugins"),
            "LOAM_CONTRIBUTIONS_FOLLOW": "0",
            "LOAM_SECRETS_FILE": str(tmp / "secrets.env"),
        }))

    def connect_projects(self):
        shared = keys.set_shared("hetzner", {"value": PERSONAL})
        named = keys.set_hetzner_project("Work", {"value": WORK})
        self.assertEqual(shared["shown"], {})
        self.assertEqual([p["id"] for p in named["projects"]], ["work"])
        self.assertEqual(self.calls, [
            ("GET", "/v1/locations?per_page=1", PERSONAL, None),
            ("GET", "/v1/locations?per_page=1", WORK, None),
        ])
        for token in (PERSONAL, WORK):
            self.assertNotIn(token, json.dumps(named))

    def test_projects_and_hel1(self):
        self.connect_projects()
        prices = api.call("personal", "GET", "/pricing")
        types = api.call("WORK", "GET", "/server_types")
        self.assertEqual(prices["body"]["pricing"]["server_types"][0]["prices"][0]["location"], "hel1")
        self.assertEqual(types["body"]["server_types"][0]["locations"][0]["name"], "hel1")
        self.assertEqual(self.calls[-2:], [
            ("GET", "/v1/pricing", PERSONAL, None),
            ("GET", "/v1/server_types", WORK, None),
        ])
        body = {"server_type": "example-type", "location": "hel1",
                "ssh_keys": [1], "user_data": "#cloud-config\n"}
        self.assertTrue(api.call("work", "POST", "/servers", body)["ok"])
        self.assertEqual(self.calls[-1], ("POST", "/v1/servers", WORK, body))
        with self.assertRaisesRegex(keys.KeyError_, "not a valid API token"):
            keys.set_hetzner_project("work", {"value": BAD})
        self.assertEqual(keys.hetzner_project_token("work"), WORK)
        keys.set_hetzner_project("work", {"value": ROTATED})
        self.assertTrue(api.call("work", "GET", "/servers?per_page=10")["ok"])
        self.assertEqual(self.calls[-1], ("GET", "/v1/servers?per_page=10", ROTATED, None))
        key_file = keys.keys_dir() / "hetzner-projects" / "work.json"
        self.assertEqual(stat.S_IMODE(key_file.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(key_file.parent.stat().st_mode), 0o700)
        keys.forget_hetzner_project("Work")
        before = len(self.calls)
        for project in ("work", "missing", "../personal"):
            self.assertFalse(api.call(project, "GET", "/servers")["ok"])
        self.assertEqual(len(self.calls), before)
        self.assertEqual(keys.hetzner_project_token("personal"), PERSONAL)
        with self.assertRaisesRegex(keys.KeyError_, "shared"):
            keys.set_hetzner_project("PERSONAL", {"value": WORK})

    def test_protect_host_on_both_projects(self):
        self.connect_projects()
        self.calls.clear()
        actions = [
            ("DELETE", "/servers/42"),
            ("POST", "/servers/42/actions/rebuild"),
            ("POST", "/v1/servers/%2B00042/actions/%70oweroff/?x=1"),
            ("POST", "/servers/42/actions/shutdown"),
            ("POST", "/servers/42/actions/change_type"),
        ]
        for project in ("personal", "work"):
            for method, path in actions:
                with self.subTest(project=project, path=path):
                    result = api.call(project, method, path)
                    self.assertFalse(result["ok"])
                    self.assertIn("server running Loam", result["error"])
        self.assertEqual(self.calls, [("GET", "/instance-id", "", None)] * (2 * len(actions)))
        self.assertTrue(api.call("work", "DELETE", "/servers/43")["ok"])
        self.assertTrue(api.call("personal", "POST", "/servers/42/actions/poweron")["ok"])
        with patch.object(api, "server_id", return_value=None):
            self.assertIn("could not identify", api.call("work", "DELETE", "/servers/43")["error"])
            self.assertTrue(api.call("work", "GET", "/servers")["ok"])

    def test_redacts_echoes_and_never_follows_redirects(self):
        self.connect_projects()
        for result in (api.call("personal", "GET", "/pricing"),
                       api.request(BAD, "GET", "/pricing"),
                       api.call("work", "GET", "/redirect")):
            output = json.dumps(result)
            self.assertIn("[redacted]", output)
            for token in (PERSONAL, WORK, BAD):
                self.assertNotIn(token, output)
        self.assertEqual(self.calls[-1][1], "/v1/redirect")
        before = len(self.calls)
        self.assertFalse(api.call("work", "GET", "http://example.invalid/pricing")["ok"])
        self.assertFalse(api.call("work", "DELETE", "/servers/../servers/42")["ok"])
        self.assertEqual(len(self.calls), before)

    def test_enabled_lanes_and_stdio_tool(self):
        self.assertNotIn(keys.HETZNER_PLUGIN, contributions.lane_servers("persona"))
        plugins.set_on(keys.HETZNER_PLUGIN, True)
        for lane in ("persona", "engineer"):
            self.assertIn("hetzner_api", contributions.lane_prompt(lane))
            spec = contributions.lane_servers(lane)[keys.HETZNER_PLUGIN]
            self.assertIn("LOAM_PLUGIN_KEYS", spec["env"])
        messages = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "test", "version": "1"}}},
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
        # Keep stdin open until each response arrives. EOF can shut down the
        # MCP server while its asynchronous tools/list handler is still running.
        proc = subprocess.Popen([spec["command"], *spec["args"]],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True,
                                env={**os.environ, **spec["env"]})
        try:
            def send(message):
                proc.stdin.write(json.dumps(message) + "\n")
                proc.stdin.flush()

            def receive(expected):
                ready, _, _ = select.select([proc.stdout], [], [], 15)
                self.assertTrue(ready, f"MCP response {expected} timed out")
                line = proc.stdout.readline()
                self.assertTrue(line, f"MCP server exited before response {expected}")
                reply = json.loads(line)
                self.assertEqual(reply.get("id"), expected, reply)
                return reply

            send(messages[0])
            receive(1)
            send(messages[1])
            send(messages[2])
            reply = receive(2)
            tool = reply["result"]["tools"][0]
        finally:
            proc.kill()
            proc.communicate(timeout=5)
        self.assertEqual(tool["name"], "hetzner_api")
        self.assertEqual(set(tool["inputSchema"]["required"]), {"project", "method", "path"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
