"""The persona-side engineer server serves two briefs at once.

Caused on 2026-09-07: two briefs from one conversation, to two projects with
separate locks and free slots, ran one after the other because the tool's
handler was ``async def`` around ``subprocess.run`` -- a call that never
yields, so the server's one event loop was held for the length of the first
job and the second request sat unread in the pipe. This test fails if that
shape comes back.

**Driven over real stdio, with the real server file.** The venv python is
started exactly as a persona's ``.mcp.json`` starts it -- same interpreter,
same module, same transport -- except that the module's ``LAUNCHER`` is
pointed at a stand-in before ``mcp.run()``. The stand-in reads the brief,
sleeps, and prints the launcher's reply shape with its own start and finish
stamped in, so the two replies carry the same evidence a launcher record
does. Two ``tools/call`` requests go down the pipe back to back; the
assertion is that their windows overlap and that both come back in about
one sleep rather than two. The in-memory transport would have proved the
same thing about the library and nothing about this file's real entry
point, which is what a persona's session actually spawns.

The client is thirty lines of raw JSON-RPC rather than the library's
``ClientSession``: it keeps this file on the system interpreter (the suite
runs every test with ``python3``; only the server child needs the venv) and
makes ``bin/`` reusable for a hand-driven probe of the same path.

Run: python3 tests/test_engineer_server.py
"""

from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
SERVER = AGENTS / "mcp" / "engineer_server.py"
VENV_PYTHON = AGENTS / "mcp" / ".venv" / "bin" / "python"

# Older than the library's newest revision on purpose: it is the handshake
# era Claude Code speaks to this server (initialize, then the tool call).
PROTOCOL_VERSION = "2025-06-18"

STAND_IN = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, sys, time
    if "--status" in sys.argv:
        print("stand-in status: slot 1 free")
        sys.exit(0)
    args = json.loads(sys.stdin.read() or "{}")
    started = time.time()
    def note(what):
        with open(os.path.join(os.path.dirname(sys.argv[0]), "stand-in.log"), "a") as log:
            log.write(f"{time.time():.3f} {what} {args.get('project')} pid {os.getpid()}\\n")
    note("started")
    time.sleep(SLEEP)
    finished = time.time()
    note("finished")
    print(json.dumps({
        "ok": True, "job_id": "stand-in-" + args.get("project", "?"),
        "receipt_path": None,
        "receipt": "stand-in receipt for " + args.get("project", "?"),
        "started": started, "finished": finished,
        "seen_continue_job": args.get("continue_job"),
    }))
""")


def bootstrap(launcher: Path) -> str:
    """The server file, loaded by path, with its launcher swapped, then run.
    The real entry point is `mcp.run()` at the bottom of the file; this is
    that, minus the `__main__` guard."""
    return textwrap.dedent(f"""\
        import importlib.util
        spec = importlib.util.spec_from_file_location("engineer_server", {str(SERVER)!r})
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        m.LAUNCHER = {str(launcher)!r}
        m.SETTINGS = {str(launcher.parent / "settings-echo")!r}
        m.THREAD = {str(launcher.parent / "settings-echo")!r}
        import logging
        logging.getLogger().setLevel(logging.DEBUG)
        m.mcp.run()
    """)


class Server:
    """One stdio MCP server, spoken to in raw JSON-RPC, one line per frame."""

    def __init__(self, argv: list[str], env: dict, cwd: str):
        self.proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1, env=env, cwd=cwd)
        self.next_id = 1
        # Every frame read that answered nothing asked for, in case the
        # missing reply came back in a shape `replies` does not match.
        self.stray: list[dict] = []
        # The server logs at DEBUG (see `bootstrap`), so its stderr is
        # drained as it comes: a full pipe would stall the very server whose
        # stall it is there to explain.
        self._stderr: list[str] = []
        self._drain = threading.Thread(
            target=lambda: self._stderr.extend(self.proc.stderr), daemon=True)
        self._drain.start()

    def send(self, method: str, params: dict | None = None, *, notify: bool = False) -> int | None:
        frame: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            frame["params"] = params
        rid = None
        if not notify:
            rid = self.next_id
            self.next_id += 1
            frame["id"] = rid
        self.proc.stdin.write(json.dumps(frame) + "\n")
        self.proc.stdin.flush()
        return rid

    def read(self, timeout: float) -> dict | None:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            ready, _, _ = select.select([self.proc.stdout], [], [], remaining)
            if not ready:
                return None
            line = self.proc.stdout.readline()
            if not line:
                return None
            line = line.strip()
            if line:
                return json.loads(line)

    def replies(self, ids: set[int], timeout: float) -> dict[int, dict]:
        """Every response for `ids`, in arrival order, or what arrived."""
        got: dict[int, dict] = {}
        deadline = time.monotonic() + timeout
        while set(got) != ids:
            frame = self.read(max(0.0, deadline - time.monotonic()))
            if frame is None:
                break
            if frame.get("id") in ids:
                got[frame["id"]] = frame
            else:
                self.stray.append(frame)
        return got

    def initialize(self) -> dict:
        rid = self.send("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "loam-test", "version": "0"},
        })
        reply = self.replies({rid}, 30).get(rid) or {}
        self.send("notifications/initialized", notify=True)
        return reply

    def close(self) -> str:
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
            self.proc.wait()
        self._drain.join(timeout=5)
        return "".join(self._stderr)


def tool_result(frame: dict) -> dict:
    """The dict the tool returned, from either place the library puts it."""
    result = frame.get("result") or {}
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    for block in result.get("content") or []:
        if block.get("type") == "text":
            try:
                return json.loads(block["text"])
            except ValueError:
                return {"_text": block["text"]}
    return {"_raw": frame}


def brief_args(project: str) -> dict:
    return {"name": "brief", "arguments": {
        "project": project, "title": f"concurrency probe {project}",
        "brief": "Do nothing. This is a probe of the server's wait.",
        "involvement": "go and do it, the owner reviews after it is running",
    }}


def main() -> int:
    fails = 0

    def check(label: str, got, want) -> None:
        nonlocal fails
        ok = got == want
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {label:52} {got!r}"
              + ("" if ok else f"   want {want!r}"))

    if not VENV_PYTHON.is_file():
        print(f"SKIP  no venv python at {VENV_PYTHON}; nothing to drive")
        return 0

    sleep_s = 3
    root = Path(tempfile.mkdtemp(prefix="loam-engineer-server-test-"))
    try:
        stand_in = root / "stand-in-launcher"
        stand_in.write_text(STAND_IN.replace("SLEEP", str(sleep_s)), encoding="utf-8")
        stand_in.chmod(0o755)
        echo = root / "settings-echo"
        echo.write_text("#!/usr/bin/env python3\nimport json, sys\n"
                        "print(json.dumps({'argv': sys.argv[1:]}))\n",
                        encoding="utf-8")
        echo.chmod(0o755)
        env = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home()),
               "LOAM_AGENT": "tester"}
        # cwd off the repo: `import mcp` from inside `agents/` would find the
        # repo's own `mcp/` directory as a namespace package, which a persona's
        # session, started from its workspace, never does.
        server = Server([str(VENV_PYTHON), "-c", bootstrap(stand_in)], env, str(root))
        try:
            init = server.initialize()
            check("initialize answered with a server name",
                  (init.get("result") or {}).get("serverInfo", {}).get("name"),
                  "loam-engineer")

            rid = server.send("tools/list")
            tools = (server.replies({rid}, 30).get(rid) or {}).get("result", {}).get("tools", [])
            brief = next((t for t in tools if t["name"] == "brief"), None)
            check("the tools are `brief`, `cancel`, `status`, `settings`, `site`, `thread` (capacity and sandboxes are plugins' since 2026-11-14)",
                  sorted(t["name"] for t in tools), ["brief", "cancel", "settings", "site", "status", "thread"])
            # `settings` (2026-11-10) hands off to bin/settings: flags first,
            # then `--`, so a value that starts with a dash is a value.
            rid = server.send("tools/call", {"name": "settings", "arguments": {
                "action": "set", "setting": "thread.title", "value": "-a dash",
                "target": "t-1", "by": "ada"}})
            got = tool_result(server.replies({rid}, 30).get(rid) or {})
            if isinstance(got.get("result"), str):
                got = json.loads(got["result"])      # a str tool's structured form
            check("`settings` hands bin/settings its arguments",
                  got.get("argv"), ["--target=t-1", "--by=ada", "--",
                                    "set", "thread.title", "-a dash"])
            # `thread` action `branch` (2026-11-11) hands off to bin/thread:
            # the source and the message, then only the flags it was given.
            rid = server.send("tools/call", {"name": "thread", "arguments": {
                "action": "branch", "thread": "t-1", "message": "m4",
                "persona": "ada", "text": "Opening:", "by": "iris"}})
            got = tool_result(server.replies({rid}, 30).get(rid) or {})
            if isinstance(got.get("result"), str):
                got = json.loads(got["result"])
            check("`thread` branch hands bin/thread its arguments",
                  got.get("argv"), ["branch", "t-1", "m4", "--persona", "ada",
                                    "--text", "Opening:", "--by", "iris"])
            status = next((t for t in tools if t["name"] == "status"), None)
            check("`status` takes no arguments",
                  sorted((status or {}).get("inputSchema", {}).get("properties", {})), [])
            rid = server.send("tools/call", {"name": "status", "arguments": {}})
            got = tool_result(server.replies({rid}, 30).get(rid) or {})
            check("`status` returns `engineer-job --status`'s text",
                  (got.get("_text") or got.get("result") or "").strip(),
                  "stand-in status: slot 1 free")
            schema = (brief or {}).get("inputSchema", {})
            check("its parameters are unchanged",
                  sorted(schema.get("properties", {})),
                  # `tier` since 2026-11-15: which of the engineer's three
                  # models, optional.
                  ["brief", "continue_job", "deploy", "involvement",
                   "max_minutes", "project", "tier", "title"])
            check("`tier` is optional",
                  "tier" in (schema.get("required") or []), False)
            check("max_minutes, continue_job and deploy are the optional ones",
                  sorted(schema.get("required", [])),
                  ["brief", "involvement", "project", "title"])

            # Two briefs, two projects, back to back on one pipe.
            t0 = time.monotonic()
            a = server.send("tools/call", brief_args("alpha"))
            b = server.send("tools/call", brief_args("beta"))
            got = server.replies({a, b}, sleep_s * 4 + 10)
            elapsed = time.monotonic() - t0

            check("both calls answered", sorted(got), sorted([a, b]))
            ra = tool_result(got.get(a, {}))
            rb = tool_result(got.get(b, {}))
            check("first reply is the launcher's, verbatim",
                  ra.get("job_id"), "stand-in-alpha")
            check("second reply is the launcher's, verbatim",
                  rb.get("job_id"), "stand-in-beta")
            windows_overlap = (
                "started" in ra and "started" in rb
                and max(ra["started"], rb["started"]) < min(ra["finished"], rb["finished"])
            )
            check("the two launcher windows overlap", windows_overlap, True)
            # The whole point. Serialised, this is two sleeps and change.
            check(f"both back in under two sleeps ({elapsed:.1f}s of {2 * sleep_s}s)",
                  elapsed < 2 * sleep_s, True)
            gap = abs(ra.get("started", 0) - rb.get("started", 0))
            check(f"second launcher started within a second of the first ({gap:.2f}s)",
                  gap < 1.0, True)

            # `continue_job` (2026-09-22) crosses to the launcher verbatim,
            # and is the empty string when the caller omits it. The server
            # decides nothing about it; that it ARRIVES is the whole of its
            # part, and the rules are tested against the launcher in
            # tests/test_engineer.py.
            args = brief_args("gamma")
            args["arguments"]["continue_job"] = "20260922-010101-abcd1234"
            rid = server.send("tools/call", args)
            rg = tool_result(server.replies({rid}, sleep_s * 4 + 10).get(rid, {}))
            check("continue_job reaches the launcher verbatim",
                  rg.get("seen_continue_job"), "20260922-010101-abcd1234")
            check("omitted, it reaches the launcher as empty",
                  ra.get("seen_continue_job"), "")
        finally:
            stderr = server.close()
            if fails:
                # What the next flake needs to carry its cause (10-09 15:54Z:
                # one of two back-to-back calls never answered, and the
                # server, logging at INFO, had said nothing).
                print("--- server stderr (DEBUG) ---")
                print(stderr[-12000:] if stderr.strip() else "(empty)")
                print("--- frames read that answered no request ---")
                for frame in server.stray:
                    print(json.dumps(frame)[:500])
                print("--- stand-in launcher log ---")
                log = root / "stand-in.log"
                print(log.read_text(encoding="utf-8") if log.is_file() else "(none)")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print()
    print("FAILURES:", fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
