#!/usr/bin/env python3
"""Wren's craft path as a shared MCP server: PDF, fonts, page render.

Ported from ``chassis.image_gen`` and ``chassis.pdf_gen``. Two deliberate
changes from the chassis version, both ratified 2026-08-25:

* **No per-agent allowlist.** On the chassis these were the only scoped tool
  grants in the system, gated to one persona for craft ownership rather than for
  safety. Here every agent gets them all.
* **Write targets are not fenced** (since 2026-11-08, "the authority
  move"; until then they were held to the agent's own workspace, the
  commons and the vault). A target's filename is still checked for the
  characters Android cannot store.

``render_typst_pages`` is the one that closes the loop -- it is how an agent
*sees* what it composed. Composing without preview is working blind.

The verbatim stdout/stderr passthrough on the Typst tools is the contract,
not a nicety: it is what lets an agent read its own compile diagnostics and
fix its own source. The parsed ``diagnostics`` overlay is a convenience; a
parse miss never affects the raw text.

Configured by environment: LOAM_AGENT, LOAM_AGENT_WORKSPACE, LOAM_VAULT and
LOAM_AI_LABS. Image making left on 2026-11-18 for the Images plugin, and
with it the only reason this server read a secret.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

import sys as _sys
_sys.path.append(str(Path(__file__).resolve().parent.parent / "lib"))
import loam_config as lc  # noqa: E402  constants only; nothing here holds state

from mcp.server import MCPServer

AGENT = os.environ.get("LOAM_AGENT", "unknown")
WORKSPACE = Path(os.environ.get("LOAM_AGENT_WORKSPACE", "/nonexistent")).resolve()
VAULT = Path(os.environ.get("LOAM_VAULT") or lc.VAULT).resolve()
AI_LABS = Path(os.environ.get("LOAM_AI_LABS") or lc.WORKSPACES).resolve()
COMMONS = AI_LABS / "commons"
CONVERSATIONS = VAULT / "15_CONVERSATIONS"

# Images left this server on 2026-11-18: core ships no image tool (the
# owner's decision, m444). The Images plugin (plugins/images/, built in since 0.2.10) has
# generate_image, from this server's own, for every engine.

TYPST_BIN_FALLBACK = "/usr/local/bin/typst"
COMPILE_TIMEOUT_S = 120.0
FONTS_TIMEOUT_S = 5.0
RENDER_TIMEOUT_S = 60.0
MIN_DPI, MAX_DPI, DEFAULT_DPI = 50, 600, 150

# Illegal on Android at the filesystem layer. A vault file carrying one of
# these is permanently invisible on the owner's phone, so it is refused at
# write time rather than discovered later.
ANDROID_ILLEGAL = set('?:*"<>|')

_SEVERITY_RE = re.compile(r"^(error|warning): (.*)$")
_LOCATION_RE = re.compile(r"┌─\s+(.+):(\d+):(\d+)")
_RENDER_LEAF_RE = re.compile(r"^page-(\d+)\.png$")
_PAGES_RE = re.compile(r"^\s*\d+(\s*-\s*\d+)?(\s*,\s*\d+(\s*-\s*\d+)?)*\s*$")

mcp = MCPServer("loam-composition")


# ---------- boundary ----------


def _error(kind: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": kind, "detail": message}


def _readable(path: str) -> Path | None:
    try:
        resolved = Path(path).resolve()
    except OSError:
        return None
    if resolved.is_relative_to(VAULT) or resolved.is_relative_to(AI_LABS):
        return resolved
    return None


def _writable(path: str) -> tuple[Path | None, str | None]:
    """Resolve a write target. No path boundary since 2026-11-08 ("the
    authority move"); the Android filename check stays, because a file with
    one of those characters never reaches the phone."""
    try:
        resolved = Path(os.path.abspath(path)).resolve()
    except OSError:
        return None, f"could not resolve {path!r}"

    bad = sorted(set(resolved.name) & ANDROID_ILLEGAL)
    if bad:
        return None, (
            f"filename {resolved.name!r} contains {''.join(bad)!r}, which "
            f"Android cannot store — the file would be permanently invisible "
            f"on the phone. Rename it."
        )

    return resolved, None


# ---------- typst ----------


def _typst_bin() -> str:
    return shutil.which("typst") or TYPST_BIN_FALLBACK


def _font_path_args() -> list[str]:
    """``--font-path <workspace>/fonts`` iff it exists.

    Per-agent by construction: any agent that earns a ``fonts/`` directory
    gets it included with no code change.
    """
    font_dir = WORKSPACE / "fonts"
    return ["--font-path", str(font_dir)] if font_dir.is_dir() else []


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


async def _run_typst(args: list[str], timeout: float):
    proc = await asyncio.create_subprocess_exec(
        _typst_bin(), *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (asyncio.TimeoutError, TimeoutError):
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        return None, "", "", True
    return proc.returncode, _decode(out), _decode(err), False


def _parse_diagnostics(stderr: str) -> list[dict[str, Any]]:
    """Convenience overlay on the verbatim text; a parse miss changes nothing."""
    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in stderr.splitlines():
        sev = _SEVERITY_RE.match(line.strip())
        if sev:
            current = {
                "severity": sev.group(1), "message": sev.group(2),
                "file": None, "line": None, "column": None,
            }
            records.append(current)
            continue
        loc = _LOCATION_RE.search(line)
        if loc and current is not None:
            current["file"] = loc.group(1)
            current["line"] = int(loc.group(2))
            current["column"] = int(loc.group(3))
            current = None
    return records


@mcp.tool(
    name="generate_pdf",
    description=(
        "Compile a Typst source file to PDF. Provide an absolute path to a "
        "`.typ` source file, an absolute target path ending in `.pdf`, and "
        "optionally a root directory (defaults to the source's parent — set "
        "this wider if your document imports templates or reads files from "
        "outside the source's folder). Fonts from your workspace's `fonts/` "
        "directory are included automatically. Returns the path to the "
        "written PDF on success, or a structured error carrying the full "
        "Typst stdout/stderr so you can read the diagnostics, fix your "
        "source, and recompile."
    ),
)
async def generate_pdf(
    source_path: str, target_path: str, root: str | None = None
) -> dict[str, Any]:
    started = time.monotonic()
    if not source_path or not Path(source_path).is_absolute():
        return _error("validation", f"source_path must be absolute; got {source_path!r}")
    source = _readable(source_path)
    if source is None:
        return _error("boundary", f"source_path {source_path!r} is outside the vault and ai-labs.")
    if not source.is_file() or not os.access(source, os.R_OK):
        return _error("validation", f"source_path {source_path!r} does not exist or is not readable.")
    if source.suffix.lower() != ".typ":
        return _error("validation", f"source_path must end in '.typ'; got {source.suffix or '(none)'!r}")
    if not target_path or not Path(target_path).is_absolute():
        return _error("validation", f"target_path must be absolute; got {target_path!r}")
    if Path(target_path).suffix.lower() != ".pdf":
        return _error("validation", f"target_path must end in '.pdf'; got {Path(target_path).suffix or '(none)'!r}")
    target, why = _writable(target_path)
    if target is None:
        return _error("boundary", why or "target is out of bounds")

    if root:
        if not Path(root).is_absolute():
            return _error("validation", f"root must be an absolute path; got {root!r}")
        root_resolved = _readable(root)
        if root_resolved is None or not root_resolved.is_dir():
            return _error("validation", f"root {root!r} is not a readable directory in the vault or ai-labs.")
    else:
        root_resolved = source.parent

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _error("write_error", f"could not create target directory: {exc}")

    args = ["compile", str(source), str(target), "--root", str(root_resolved), *_font_path_args()]
    try:
        code, out, err, timed_out = await _run_typst(args, timeout=COMPILE_TIMEOUT_S)
    except FileNotFoundError:
        return _error("environment", f"typst executable not found (looked for {_typst_bin()!r})")
    if timed_out:
        with contextlib.suppress(OSError):
            target.unlink(missing_ok=True)
        return _error("timeout", f"typst compile timed out after {COMPILE_TIMEOUT_S:.0f}s.")

    ok = code == 0 and target.is_file()
    return {
        "ok": ok,
        "path": str(target) if ok else None,
        "exit_code": code,
        "stdout": out,
        "stderr": err,
        "diagnostics": _parse_diagnostics(err),
        "elapsed_s": round(time.monotonic() - started, 2),
    }


@mcp.tool(
    name="typst_fonts",
    description=(
        "List the fonts Typst can see right now — bundled fonts, system "
        "fonts, and any fonts in your workspace's `fonts/` directory. "
        "Returns one family name per line. Useful before writing a `.typ` "
        "that picks a specific typeface."
    ),
)
async def typst_fonts() -> dict[str, Any]:
    try:
        code, out, err, timed_out = await _run_typst(["fonts", *_font_path_args()], timeout=FONTS_TIMEOUT_S)
    except FileNotFoundError:
        return _error("environment", f"typst executable not found (looked for {_typst_bin()!r})")
    if timed_out:
        return _error("timeout", f"typst fonts timed out after {FONTS_TIMEOUT_S:.0f}s.")
    families = [line.strip() for line in out.splitlines() if line.strip()]
    return {"ok": code == 0, "exit_code": code, "families": families,
            "count": len(families), "stdout": out, "stderr": err}


def _validate_pages(pages: str) -> str | None:
    if not _PAGES_RE.match(pages):
        return (
            f"pages {pages!r} is not valid Typst page-range syntax; "
            f'use e.g. "1", "1-3", "2,5,7-9"'
        )
    return None


@mcp.tool(
    name="render_typst_pages",
    description=(
        "Render a Typst source file to per-page PNG images. Provide an "
        "absolute path to a `.typ` source file and an absolute output "
        "directory; per-page images are written as `page-001.png`, "
        "`page-002.png`, ... (zero-padded to 3 digits, using physical page "
        "numbers from the document). Optional `dpi` sets pixels-per-inch "
        "(default 150, range 50-600). Optional `pages` selects a subset "
        'using Typst page-range syntax (e.g. "1", "1-3", "2,5,7-9"); omit '
        "to render every page. Returns the list of written page paths on "
        "success, or a structured error carrying the full Typst "
        "stdout/stderr. This is how you see what you composed."
    ),
)
async def render_typst_pages(
    source_path: str, output_dir: str, dpi: int = DEFAULT_DPI, pages: str | None = None
) -> dict[str, Any]:
    started = time.monotonic()
    if not source_path or not Path(source_path).is_absolute():
        return _error("validation", f"source_path must be absolute; got {source_path!r}")
    source = _readable(source_path)
    if source is None:
        return _error("boundary", f"source_path {source_path!r} is outside the vault and ai-labs.")
    if not source.is_file() or not os.access(source, os.R_OK):
        return _error("validation", f"source_path {source_path!r} does not exist or is not readable.")
    if source.suffix.lower() != ".typ":
        return _error("validation", f"source_path must end in '.typ'; got {source.suffix or '(none)'!r}")
    if not output_dir or not Path(output_dir).is_absolute():
        return _error("validation", f"output_dir must be absolute; got {output_dir!r}")
    out_dir, why = _writable(output_dir)
    if out_dir is None:
        return _error("boundary", why or "output_dir is out of bounds")
    if not isinstance(dpi, int) or isinstance(dpi, bool) or not (MIN_DPI <= dpi <= MAX_DPI):
        return _error("validation", f"dpi must be an integer in {MIN_DPI}-{MAX_DPI}; got {dpi!r}")
    if pages is not None:
        problem = _validate_pages(pages)
        if problem:
            return _error("validation", problem)

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _error("write_error", f"could not create output directory: {exc}")

    # Render into a hidden temp subdir so a failed render leaves output_dir
    # untouched, then rename in with canonical zero-padding.
    tmp_dir = out_dir / ".render-tmp"
    with contextlib.suppress(OSError):
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    args = ["compile", str(source), str(tmp_dir / "page-{p}.png"),
            "--root", str(source.parent), "--ppi", str(dpi), *_font_path_args()]
    if pages is not None:
        args.extend(["--pages", pages])
    try:
        code, out, err, timed_out = await _run_typst(args, timeout=RENDER_TIMEOUT_S)
    except FileNotFoundError:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return _error("environment", f"typst executable not found (looked for {_typst_bin()!r})")
    if timed_out:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return _error("timeout", f"typst render timed out after {RENDER_TIMEOUT_S:.0f}s.")

    written: list[str] = []
    if code == 0:
        for child in sorted(tmp_dir.iterdir()):
            m = _RENDER_LEAF_RE.match(child.name)
            if m is None:
                continue
            dest = out_dir / f"page-{int(m.group(1)):03d}.png"
            child.replace(dest)
            written.append(str(dest))
        written.sort()
    shutil.rmtree(tmp_dir, ignore_errors=True)

    return {
        "ok": code == 0 and bool(written),
        "pages": written,
        "page_count": len(written),
        "dpi": dpi,
        "exit_code": code,
        "stdout": out,
        "stderr": err,
        "diagnostics": _parse_diagnostics(err),
        "elapsed_s": round(time.monotonic() - started, 2),
    }


if __name__ == "__main__":
    mcp.run()
