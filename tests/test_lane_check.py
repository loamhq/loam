#!/usr/bin/env python3
"""bin/lane-check against the two thresholds, through the real code path.

The nightly lane check is a report, never a gate (see the script's own
docstring), so everything it finds is a write into the vault inbox and an
exit of 0. Two kinds of write since 2026-09-07, and this proves the line
between them:

  over the limit    the log AND a line under Urgent in comms.md.
  near the limit    the log alone, once a night, comms untouched.

Driven on both sides of ``NEAR_LIMIT_CHARS`` and both sides of ``LIMIT_CHARS``
by running ``main()`` of the shipped script against a throwaway workspace
that carries everything the check reads: agent.json, the two auto-loaded
files, and the conversation-style symlink with its frontmatter.

Run: python3 tests/test_lane_check.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import shutil
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from loam_memory import LIMIT_CHARS, NEAR_LIMIT_CHARS  # noqa: E402

AGENT = "lanechecktest"
STYLE_HEADER = "---\nname: conversation\nkeep-coding-instructions: true\n---\n"
WARNING = f"within {LIMIT_CHARS - NEAR_LIMIT_CHARS:,} characters of the"

results: list[bool] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:52}{'  | ' + detail if detail else ''}")


def load():
    loader = importlib.machinery.SourceFileLoader(
        "lane_check_under_test", str(Path(__file__).resolve().parent.parent / "bin" / "lane-check"))
    spec = importlib.util.spec_from_loader("lane_check_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def padded(header: str, size: int) -> str:
    """A file of exactly ``size`` characters, header included."""
    return header + "x" * (size - len(header))


def build(root: Path, always: int, active: int) -> tuple[Path, Path]:
    ws = root / "ai-labs" / AGENT
    vault = root / "vault"
    styles = ws / ".claude" / "output-styles"
    styles.mkdir(parents=True)
    (vault / "00_INBOX" / "from-agents").mkdir(parents=True)
    (ws / "agent.json").write_text(json.dumps({
        "name": AGENT, "display": "Lanechecktest",
        "workspace": str(ws), "vault": str(vault),
    }))
    (ws / "_always.md").write_text(padded("Always.\n", always))
    (ws / "_active.md").write_text(padded(STYLE_HEADER, active))
    (styles / "conversation.md").symlink_to("../../_active.md")
    return ws, vault


def run(ws: Path) -> tuple[int, str, str]:
    mod = load()
    # The GitHub integration's nightly check has its own test
    # (tests/test_github_app.py) and must not stamp the box's real date file.
    mod.github_check = lambda vault: []
    out, err = io.StringIO(), io.StringIO()
    sys.argv = ["lane-check", str(ws)]
    with redirect_stdout(out), redirect_stderr(err):
        code = mod.main()
    return code, out.getvalue(), err.getvalue()


def inbox(vault: Path) -> tuple[str, str]:
    texts = []
    for name in ("loam-wake-failures.md", "comms.md"):
        path = vault / "00_INBOX" / "from-agents" / name
        texts.append(path.read_text(encoding="utf-8") if path.exists() else "")
    return texts[0], texts[1]


def scenario(label: str, always: int, active: int, *, expect_log: str | None,
             expect_comms: bool, expect_file: str | None = None) -> Path:
    root = Path(tempfile.mkdtemp(prefix="loam-lane-check-"))
    ws, vault = build(root, always, active)
    code, out, err = run(ws)
    log, comms = inbox(vault)
    ok = code == 0
    ok &= (expect_log in log) if expect_log else (not log)
    ok &= (f"**{AGENT}**" in comms) == expect_comms
    if expect_file:
        ok &= f"`{expect_file}`" in log
    check(label, ok, f"exit={code} log={bool(log)} comms={f'**{AGENT}**' in comms}")
    if not ok:
        print(f"      stdout: {out.strip()[:200]}\n      stderr: {err.strip()[:300]}"
              f"\n      log: {log.strip()[:300]}")
    return root


roots = []
try:
    roots.append(scenario(
        "both files clear of the warning: nothing written",
        NEAR_LIMIT_CHARS - 1, NEAR_LIMIT_CHARS - 1,
        expect_log=None, expect_comms=False))
    roots.append(scenario(
        "_always.md just over the warning: log only",
        NEAR_LIMIT_CHARS + 1, NEAR_LIMIT_CHARS - 1,
        expect_log=WARNING, expect_comms=False, expect_file="_always.md"))
    roots.append(scenario(
        "_active.md just under the limit: log only",
        NEAR_LIMIT_CHARS - 1, LIMIT_CHARS - 1,
        expect_log=WARNING, expect_comms=False, expect_file="_active.md"))
    roots.append(scenario(
        "_active.md just over the limit: log AND comms",
        NEAR_LIMIT_CHARS - 1, LIMIT_CHARS + 1,
        expect_log="over the", expect_comms=True, expect_file="_active.md"))

    # Once a night. drift-watch is one run; a run by hand the same day is
    # the repeat this must not write twice.
    root = Path(tempfile.mkdtemp(prefix="loam-lane-check-"))
    roots.append(root)
    ws, vault = build(root, NEAR_LIMIT_CHARS + 1, NEAR_LIMIT_CHARS - 1)
    run(ws)
    (ws / "_always.md").write_text(padded("Always.\n", NEAR_LIMIT_CHARS + 2))
    code, out, _ = run(ws)
    log, comms = inbox(vault)
    warning_lines = [l for l in log.splitlines() if WARNING in l]
    check("the second run the same day adds no line",
          len(warning_lines) == 1, f"{len(warning_lines)} line(s)")
    check("the warning names the count",
          f"{NEAR_LIMIT_CHARS + 1:,} characters" in log)
    # This test is not the nightly run, and the line says so rather than
    # claiming to be it.
    check("the warning names what ran it: not the nightly run",
          "Nightly lane check:" not in log
          and ("lane-check run by hand:" in log or "lane-check run from " in log),
          log.strip()[-160:])
    rcp = load()
    check("origin: drift-watch's unit is the nightly lane check",
          rcp.origin("0::/user.slice/user-1000.slice/user@1000.service/"
                     "app.slice/loam-check-drift.service\n")
          == "Nightly lane check")
    check("origin: another unit is named",
          rcp.origin("0::/user.slice/user-1000.slice/user@1000.service/"
                     "app.slice/loam-check-memory.service\n")
          == "lane-check run from loam-check-memory")
    check("origin: a login shell is by hand",
          rcp.origin("0::/user.slice/user-1000.slice/session-12.scope\n")
          == "lane-check run by hand")
    check("a warned run still reports OK on stdout", "OK" in out, out.strip())
    check("a warned run still exits 0", code == 0, str(code))
    check("still nothing in comms", f"**{AGENT}**" not in comms)
finally:
    for root in roots:
        shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
