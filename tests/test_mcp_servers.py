#!/usr/bin/env python3
"""Verify the lightweight stdlib MCP runtime (lib/loam_mcp.py) and every core
MCP server's contract, idle memory footprint, concurrency, cancellation, large
payloads, image content blocks, and real tool execution.

Run: python3 tests/test_mcp_servers.py
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import zipfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_config as lc  # noqa: E402

CONTRACTS_PATH = AGENTS / "fixtures" / "mcp_tool_contracts.json"
MAX_IDLE_PSS_MB = 15.0


class RpcClient:
    def __init__(self, argv: list[str], env: dict[str, str], cwd: str) -> None:
        self.proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
            cwd=cwd,
        )
        self._next_id = 1
        self._frames: queue.Queue[str | None] = queue.Queue()
        self._stderr: list[str] = []

        def _drain_out() -> None:
            try:
                for line in self.proc.stdout:
                    self._frames.put(line)
            finally:
                self._frames.put(None)

        self._t_out = threading.Thread(target=_drain_out, daemon=True)
        self._t_err = threading.Thread(target=lambda: self._stderr.extend(self.proc.stderr), daemon=True)
        self._t_out.start()
        self._t_err.start()

    def send(self, method: str, params: dict | None = None, *, notify: bool = False) -> int | None:
        frame: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            frame["params"] = params
        rid = None
        if not notify:
            rid = self._next_id
            self._next_id += 1
            frame["id"] = rid
        self.proc.stdin.write(json.dumps(frame) + "\n")
        self.proc.stdin.flush()
        return rid

    def read(self, timeout: float = 15.0) -> dict | None:
        deadline = time.monotonic() + timeout
        while True:
            rem = deadline - time.monotonic()
            if rem <= 0:
                return None
            try:
                line = self._frames.get(timeout=rem)
            except queue.Empty:
                return None
            if line is None:
                return None
            line = line.strip()
            if line:
                return json.loads(line)

    def call(self, method: str, params: dict | None = None, timeout: float = 15.0) -> dict:
        rid = self.send(method, params)
        deadline = time.monotonic() + timeout
        while True:
            frame = self.read(max(0.0, deadline - time.monotonic()))
            if frame is None:
                return {}
            if frame.get("id") == rid:
                return frame

    def initialize(self, version: str = "2025-06-18") -> dict:
        resp = self.call(
            "initialize",
            {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "loam-test", "version": "0"}},
        )
        self.send("notifications/initialized", notify=True)
        return resp

    def pss_mb(self) -> float | None:
        path = Path(f"/proc/{self.proc.pid}/smaps_rollup")
        if not path.is_file():
            return None
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("Pss:"):
                return int(line.split()[1]) / 1024.0
        return None

    def close(self) -> str:
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
            self.proc.wait()
        self._t_out.join(timeout=5)
        self._t_err.join(timeout=5)
        return "".join(self._stderr)


def _normalize_desc(script: str, name: str, desc: str) -> str:
    desc = desc.replace("OwnerSentinel", "{OWNER}").replace("40_PROJECTS", "{PROJECTS}")
    if script == "engineer_server.py" and name == "site":
        desc = re.sub(r"`<name>\.[^`]+`", "`<name>.{SITE_DOMAIN}`", desc, count=1)
    return desc


def _make_sample_epub(dest: Path) -> None:
    """Create a minimal valid EPUB 3 archive with two chapters."""
    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?>'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        zf.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0" encoding="utf-8"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="uid">test-book-1</dc:identifier>'
            '<dc:title>Fixture Book</dc:title>'
            '<dc:creator>Ada Tester</dc:creator>'
            '<dc:language>en</dc:language>'
            '</metadata>'
            '<manifest>'
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            '<item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="ch2" href="ch2.xhtml" media-type="application/xhtml+xml"/>'
            '</manifest>'
            '<spine toc="ncx"><itemref idref="ch1"/><itemref idref="ch2"/></spine>'
            '</package>',
        )
        zf.writestr(
            "OEBPS/toc.ncx",
            '<?xml version="1.0" encoding="utf-8"?>'
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
            '<head/><docTitle><text>Fixture Book</text></docTitle><navMap>'
            '<navPoint id="n1" playOrder="1"><navLabel><text>Opening</text></navLabel>'
            '<content src="ch1.xhtml"/></navPoint>'
            '<navPoint id="n2" playOrder="2"><navLabel><text>Closing</text></navLabel>'
            '<content src="ch2.xhtml"/></navPoint>'
            '</navMap></ncx>',
        )
        zf.writestr(
            "OEBPS/ch1.xhtml",
            '<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Opening</title></head>'
            '<body><h1>Opening</h1><p>First chapter body text.</p></body></html>',
        )
        zf.writestr(
            "OEBPS/ch2.xhtml",
            '<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Closing</title></head>'
            '<body><h1>Closing</h1><p>Second chapter body text.</p></body></html>',
        )


def main() -> int:
    fails = 0

    def check(label: str, cond: bool, detail: object = "") -> None:
        nonlocal fails
        fails += not cond
        suffix = f"  | {str(detail)[:300]}" if not cond and detail != "" else ""
        print(f"{'PASS' if cond else 'FAIL'}  {label}{suffix}")

    tmp = Path(tempfile.mkdtemp(prefix="loam-mcp-test-")).resolve()
    try:
        # ---- 1. Protocol, concurrency, cancellation, images, large frames ----
        probe_script = tmp / "probe_server.py"
        cancel_marker = tmp / "cancelled_pid"
        probe_script.write_text(textwrap.dedent(f"""\
            import sys, time
            from pathlib import Path
            from typing import Any
            sys.path.insert(0, {str(AGENTS / 'lib')!r})
            from loam_mcp import MCPServer

            mcp = MCPServer("probe-srv", version="0.1")

            @mcp.tool(name="echo_dict", description="echo args")
            def echo_dict(text: str, count: int = 1, ratio: float = 1.0, flag: bool = False) -> dict[str, Any]:
                return {{"text": text, "count": count, "ratio": ratio, "flag": flag}}

            @mcp.tool(name="echo_str", description="echo str")
            def echo_str(value: str = "ok") -> str:
                return f"str:{{value}}"

            @mcp.tool(name="get_image", description="return an image content block")
            def get_image() -> list[dict[str, Any]]:
                return [{{"type": "image", "data": "iVBORw0KGgo=", "mimeType": "image/png"}}]

            @mcp.tool(name="fail_tool", description="raises")
            def fail_tool() -> str:
                raise RuntimeError("intentional boom")

            @mcp.tool(name="slow_sync", description="sleep sync")
            def slow_sync(tag: str, secs: float = 0.6) -> dict[str, Any]:
                t0 = time.monotonic()
                time.sleep(secs)
                return {{"tag": tag, "t0": t0, "t1": time.monotonic()}}

            @mcp.tool(name="slow_async", description="sleep async")
            async def slow_async(tag: str, secs: float = 0.6) -> dict[str, Any]:
                import asyncio
                t0 = time.monotonic()
                await asyncio.sleep(secs)
                return {{"tag": tag, "t0": t0, "t1": time.monotonic()}}

            @mcp.tool(name="cancel_subproc", description="runs a child until cancelled")
            async def cancel_subproc() -> dict[str, Any]:
                import asyncio, contextlib
                proc = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(30)")
                Path({str(cancel_marker)!r}).write_text(str(proc.pid))
                try:
                    await proc.wait()
                except asyncio.CancelledError:
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    await proc.wait()
                    raise
                return {{"ok": True}}

            mcp.run()
        """), encoding="utf-8")

        env = os.environ.copy()
        cli = RpcClient([sys.executable, str(probe_script)], env, str(tmp))
        try:
            init = cli.initialize("2025-11-25")
            check("loam_mcp: initialize echoes protocolVersion",
                  init.get("result", {}).get("protocolVersion") == "2025-11-25", init)
            check("loam_mcp: ping returns empty object",
                  cli.call("ping").get("result") == {})
            check("loam_mcp: prompts/list returns empty list",
                  cli.call("prompts/list").get("result") == {"prompts": []})
            check("loam_mcp: resources/list returns empty list",
                  cli.call("resources/list").get("result") == {"resources": []})
            check("loam_mcp: unknown method returns -32601",
                  cli.call("no/such").get("error", {}).get("code") == -32601)

            # Type coercion & ignoring extra args
            r = cli.call("tools/call", {"name": "echo_dict", "arguments": {
                "text": "hi", "count": "7", "ratio": "2.5", "flag": "true", "extra_ignored": 99}})
            sc = r.get("result", {}).get("structuredContent")
            check("loam_mcp: dict result coerces types and ignores unknown extra args",
                  sc == {"text": "hi", "count": 7, "ratio": 2.5, "flag": True}
                  and r["result"]["isError"] is False, r)

            # String result
            r = cli.call("tools/call", {"name": "echo_str", "arguments": {"value": "abc"}})
            check("loam_mcp: str result carries content and structuredContent.result",
                  r.get("result", {}).get("structuredContent") == {"result": "str:abc"}
                  and r["result"]["content"][0]["text"] == "str:abc", r)

            # Image content block pass-through
            r = cli.call("tools/call", {"name": "get_image", "arguments": {}})
            check("loam_mcp: image content blocks pass through intact",
                  r.get("result", {}).get("content") == [{"type": "image", "data": "iVBORw0KGgo=", "mimeType": "image/png"}]
                  and r["result"]["isError"] is False, r)

            # Missing required arg, unknown tool, raised exception
            r = cli.call("tools/call", {"name": "echo_dict", "arguments": {}})
            check("loam_mcp: missing required arg returns isError=True",
                  r.get("result", {}).get("isError") is True, r)
            r = cli.call("tools/call", {"name": "no_such_tool", "arguments": {}})
            check("loam_mcp: unknown tool returns isError=True",
                  r.get("result", {}).get("isError") is True and "Unknown tool" in r["result"]["content"][0]["text"], r)
            r = cli.call("tools/call", {"name": "fail_tool", "arguments": {}})
            check("loam_mcp: raised exception returns isError=True",
                  r.get("result", {}).get("isError") is True and "intentional boom" in r["result"]["content"][0]["text"], r)

            # Large payload (> 300 KB, well past asyncio's default 64 KB line limit)
            big = "x" * 350_000
            r = cli.call("tools/call", {"name": "echo_dict", "arguments": {"text": big}})
            check("loam_mcp: handles >300 KB JSON frames cleanly",
                  len(r.get("result", {}).get("structuredContent", {}).get("text", "")) == 350_000)

            # Concurrent sync calls overlap
            id_a = cli.send("tools/call", {"name": "slow_sync", "arguments": {"tag": "a", "secs": 0.6}})
            id_b = cli.send("tools/call", {"name": "slow_sync", "arguments": {"tag": "b", "secs": 0.6}})
            got = {}
            while len(got) < 2:
                f = cli.read(5.0)
                if f is None:
                    break
                got[f["id"]] = f["result"]["structuredContent"]
            check("loam_mcp: concurrent sync tools overlap",
                  len(got) == 2 and max(got[id_a]["t0"], got[id_b]["t0"]) < min(got[id_a]["t1"], got[id_b]["t1"]),
                  got)

            # Concurrent async calls overlap
            id_a = cli.send("tools/call", {"name": "slow_async", "arguments": {"tag": "a", "secs": 0.6}})
            id_b = cli.send("tools/call", {"name": "slow_async", "arguments": {"tag": "b", "secs": 0.6}})
            got = {}
            while len(got) < 2:
                f = cli.read(5.0)
                if f is None:
                    break
                got[f["id"]] = f["result"]["structuredContent"]
            check("loam_mcp: concurrent async tools overlap",
                  len(got) == 2 and max(got[id_a]["t0"], got[id_b]["t0"]) < min(got[id_a]["t1"], got[id_b]["t1"]),
                  got)

            # Cancellation kills an in-flight async subprocess and emits no response
            cid = cli.send("tools/call", {"name": "cancel_subproc", "arguments": {}})
            deadline = time.monotonic() + 5.0
            while not cancel_marker.is_file() and time.monotonic() < deadline:
                time.sleep(0.05)
            child_pid = int(cancel_marker.read_text().strip()) if cancel_marker.is_file() else -1
            cli.send("notifications/cancelled", {"requestId": cid, "reason": "test"}, notify=True)
            # Server stays responsive and child process terminates promptly.
            pong = cli.call("ping")
            check("loam_mcp: server answers ping immediately after cancelling a call",
                  pong.get("result") == {}, pong)
            deadline = time.monotonic() + 5.0
            alive = True
            while time.monotonic() < deadline:
                try:
                    os.kill(child_pid, 0)
                    time.sleep(0.05)
                except ProcessLookupError:
                    alive = False
                    break
            check("loam_mcp: notifications/cancelled terminates in-flight async subprocess",
                  child_pid > 0 and not alive, f"pid={child_pid}")
        finally:
            cli.close()

        # Piped batch mode: stdin closes immediately while tool is running
        batch_in = "\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "slow_async", "arguments": {"tag": "batch", "secs": 0.2}}}),
        ]) + "\n"
        batch = subprocess.run([sys.executable, str(probe_script)], input=batch_in,
                               capture_output=True, text=True, timeout=10)
        lines = [json.loads(l) for l in batch.stdout.splitlines() if l.strip()]
        check("loam_mcp: piped batch waits for in-flight call before exit",
              batch.returncode == 0 and len(lines) == 2
              and lines[1]["result"]["structuredContent"]["tag"] == "batch", lines)

        # ---- 2. Core servers: contract lock-down + idle PSS budget ----
        contracts = json.loads(CONTRACTS_PATH.read_text(encoding="utf-8"))
        venv_py = lc.MCP_VENV / "bin" / "python"
        py_for_core = str(venv_py) if venv_py.is_file() else sys.executable

        contract_cfg = tmp / "contract-loam.toml"
        existing_cfg = os.environ.get("LOAM_CONFIG")
        base_toml = Path(existing_cfg).read_text(encoding="utf-8") if existing_cfg and Path(existing_cfg).is_file() else ""
        base_toml = re.sub(r'(?ms)^\[owner\]\n(?:(?!\[).)*', "", base_toml)
        contract_cfg.write_text(base_toml.rstrip() + '\n\n[owner]\nname = "OwnerSentinel"\n', encoding="utf-8")
        base_env = {
            **os.environ,
            "LOAM_CONFIG": str(contract_cfg),
            "LOAM_AGENT": "tester",
            "LOAM_AGENT_WORKSPACE": str(tmp),
            "LOAM_VAULT": str(tmp / "vault"),
            "LOAM_AI_LABS": str(tmp / "ai-labs"),
            "LOAM_EPUB_ROOTS": str(tmp),
            "LOAM_FILES_ROOTS": str(tmp),
            "LOAM_FILES_EXCLUDE": str(tmp / "excluded"),
            "LOAM_HISTORY_REPO": str(tmp / "repo"),
        }
        (tmp / "vault").mkdir(exist_ok=True)
        (tmp / "ai-labs").mkdir(exist_ok=True)

        for script, expected in contracts.items():
            cli = RpcClient([py_for_core, str(AGENTS / "mcp" / script)], base_env, str(tmp))
            try:
                init = cli.initialize()
                tresp = cli.call("tools/list")
                time.sleep(0.1)
                pss = cli.pss_mb()
            finally:
                cli.close()

            init_res = init.get("result") or {}
            check(f"{script}: serverInfo matches contract",
                  init_res.get("serverInfo") == expected["serverInfo"], init_res.get("serverInfo"))
            check(f"{script}: capabilities match contract",
                  init_res.get("capabilities") == expected["capabilities"], init_res.get("capabilities"))

            got_tools = tresp.get("result", {}).get("tools", [])
            actual = []
            for t in got_tools:
                nd = _normalize_desc(script, t["name"], t["description"])
                entry = {
                    "name": t["name"],
                    "description_sha256": hashlib.sha256(nd.encode("utf-8")).hexdigest(),
                    "inputSchema": t["inputSchema"],
                    "outputSchema": t.get("outputSchema"),
                }
                if "annotations" in t:
                    entry["annotations"] = t["annotations"]
                actual.append(entry)
            check(f"{script}: tools/list (names, descriptions, schemas) matches contract",
                  actual == expected["tools"],
                  f"got {[t['name'] for t in actual]} vs {[t['name'] for t in expected['tools']]}")

            if script == "engineer_server.py":
                brief_desc = next(t["description"] for t in got_tools if t["name"] == "brief")
                check("engineer_server.py: stale 20% RAM cap text removed",
                      "20% of the box's RAM" not in brief_desc
                      and "no per-job memory ceiling" in brief_desc)

            if pss is not None:
                check(f"{script}: idle PSS <= {MAX_IDLE_PSS_MB:.0f} MB ({pss:.1f} MB)",
                      pss <= MAX_IDLE_PSS_MB, f"{pss:.1f} MB")

        # ---- 3. Real tool calls over stdio JSON-RPC ----
        # 3a. composition_server.py (if typst is available)
        if shutil.which("typst") or Path("/usr/local/bin/typst").is_file():
            typ = tmp / "ai-labs" / "sample.typ"
            pdf = tmp / "ai-labs" / "sample.pdf"
            pages_dir = tmp / "ai-labs" / "pages"
            typ.write_text("= Sample\n\nRendered via loam_mcp.\n", encoding="utf-8")
            cli = RpcClient([py_for_core, str(AGENTS / "mcp" / "composition_server.py")], base_env, str(tmp))
            try:
                cli.initialize()
                r_fonts = cli.call("tools/call", {"name": "typst_fonts", "arguments": {}})
                check("composition: typst_fonts returns ok",
                      r_fonts.get("result", {}).get("structuredContent", {}).get("ok") is True, r_fonts)
                r_pdf = cli.call("tools/call", {"name": "generate_pdf", "arguments": {
                    "source_path": str(typ), "target_path": str(pdf)}}, timeout=30.0)
                sc_pdf = r_pdf.get("result", {}).get("structuredContent") or {}
                check("composition: generate_pdf compiles real PDF",
                      sc_pdf.get("ok") is True and pdf.is_file() and pdf.stat().st_size > 100, sc_pdf)
                r_png = cli.call("tools/call", {"name": "render_typst_pages", "arguments": {
                    "source_path": str(typ), "output_dir": str(pages_dir), "dpi": 100}}, timeout=30.0)
                sc_png = r_png.get("result", {}).get("structuredContent") or {}
                check("composition: render_typst_pages writes PNG",
                      sc_png.get("ok") is True and sc_png.get("page_count") == 1, sc_png)
            finally:
                cli.close()

        # 3b. read_epub_server.py (when venv with ebooklib/bs4 is available)
        if venv_py.is_file():
            epub_path = tmp / "book.epub"
            _make_sample_epub(epub_path)
            cli = RpcClient([str(venv_py), str(AGENTS / "mcp" / "read_epub_server.py")], base_env, str(tmp))
            try:
                cli.initialize()
                r_toc = cli.call("tools/call", {"name": "read_epub", "arguments": {"path": str(epub_path)}})
                sc_toc = r_toc.get("result", {}).get("structuredContent") or {}
                check("read_epub: TOC mode reads synthetic EPUB via lazy ebooklib/bs4",
                      sc_toc.get("ok") is True and sc_toc.get("chapter_count") == 2
                      and sc_toc.get("metadata", {}).get("title") == "Fixture Book", sc_toc)
                r_ch = cli.call("tools/call", {"name": "read_epub", "arguments": {
                    "path": str(epub_path), "chapter": 1}})
                sc_ch = r_ch.get("result", {}).get("structuredContent") or {}
                check("read_epub: chapter mode extracts text",
                      sc_ch.get("ok") is True and "First chapter body text." in sc_ch.get("text", ""), sc_ch)
            finally:
                cli.close()

        # 3c. files_server.py
        sample_file = tmp / "ai-labs" / "hello.md"
        sample_file.write_text("alpha\nneedle here\nomega\n", encoding="utf-8")
        cli = RpcClient([py_for_core, str(AGENTS / "mcp" / "files_server.py")], base_env, str(tmp))
        try:
            cli.initialize()
            r_list = cli.call("tools/call", {"name": "list", "arguments": {"path": str(tmp / "ai-labs")}})
            check("files_server: list returns folder entries over stdio",
                  r_list.get("result", {}).get("structuredContent", {}).get("ok") is True
                  and "hello.md" in r_list["result"]["structuredContent"]["text"], r_list)
            r_grep = cli.call("tools/call", {"name": "grep", "arguments": {
                "pattern": "needle", "path": str(tmp / "ai-labs")}})
            check("files_server: grep finds match over stdio",
                  r_grep.get("result", {}).get("structuredContent", {}).get("ok") is True
                  and "needle here" in r_grep["result"]["structuredContent"]["text"], r_grep)
        finally:
            cli.close()

        # 3d. history_server.py
        repo_dir = tmp / "repo"
        repo_dir.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo_dir, check=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo_dir, check=True)
        (repo_dir / "note.md").write_text("v1\n", encoding="utf-8")
        subprocess.run(["git", "add", "note.md"], cwd=repo_dir, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo_dir, check=True)
        cli = RpcClient([py_for_core, str(AGENTS / "mcp" / "history_server.py")], base_env, str(tmp))
        try:
            cli.initialize()
            r_log = cli.call("tools/call", {"name": "log", "arguments": {"path": "note.md", "limit": 5}})
            sc_log = r_log.get("result", {}).get("structuredContent") or {}
            check("history_server: log returns commit over stdio",
                  sc_log.get("ok") is True and sc_log.get("commits_returned") == 1, sc_log)
        finally:
            cli.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'OK' if not fails else f'{fails} FAILED'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
