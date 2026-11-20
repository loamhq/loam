#!/usr/bin/env python3
"""Exercise both lane-size thresholds through the reporting path. Warnings must leave
the lane runnable.

Run: python3 tests/test_lane_check.py
"""

from __future__ import annotations

import json
import os
import tempfile as _tempfile
from pathlib import Path as _Path

ATTENTION = _Path(_tempfile.mkdtemp(prefix="loam-lane-check-attention-"))
os.environ["LOAM_ATTENTION_DIR"] = str(ATTENTION)
os.environ.setdefault("LOAM_WAITERS_DIR", str(ATTENTION / "waiters"))

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


def build(root: Path, size: int | None) -> tuple[Path, Path]:
    ws = root / "ai-labs" / AGENT
    vault = root / "vault"
    ws.mkdir(parents=True)
    (vault / "00_INBOX" / "from-agents").mkdir(parents=True)
    (ws / "agent.json").write_text(json.dumps({
        "name": AGENT, "display": "Lanechecktest",
        "workspace": str(ws), "vault": str(vault),
    }))
    if size is not None:
        (ws / "map.md").write_text(padded("Map.\n", size))
    return ws, vault


def run(ws: Path, *flags: str) -> tuple[int, str, str]:
    mod = load()
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = mod.main([*flags, str(ws)])
    return code, out.getvalue(), err.getvalue()


def wrote_nothing(vault: Path) -> bool:
    inbox = vault / "00_INBOX" / "from-agents"
    return (not any(inbox.iterdir())) and not list(ATTENTION.glob("*.json"))


def scenario(label: str, size: int | None, *, problem: str | None,
             warning: str | None) -> Path:
    root = Path(tempfile.mkdtemp(prefix="loam-lane-check-"))
    ws, vault = build(root, size)
    code, out, err = run(ws, "--json")
    found = json.loads(out)
    ok = code == 0 and found["agent"] == AGENT
    ok &= (any(problem in p for p in found["problems"]) if problem
           else not found["problems"])
    ok &= (any(warning in w for w in found["warnings"]) if warning
           else not found["warnings"])
    ok &= wrote_nothing(vault)
    check(label, ok, f"exit={code} {json.dumps(found)[:160]}")
    return root


roots = []
try:
    roots.append(scenario("missing map", None, problem=None, warning=None))
    roots.append(scenario("map just over warning", NEAR_LIMIT_CHARS + 1,
                          problem=None, warning="map.md"))
    roots.append(scenario("map just under limit", LIMIT_CHARS - 1,
                          problem=None, warning="map.md"))
    roots.append(scenario("map just over limit", LIMIT_CHARS + 1,
                          problem="map.md", warning=None))

    root = Path(tempfile.mkdtemp(prefix="loam-lane-check-"))
    roots.append(root)
    ws, vault = build(root, LIMIT_CHARS + 1)
    code, out, err = run(ws)
    check("by hand: the problem is printed, exit 0",
          code == 0 and "over the" in out and AGENT in out, out.strip()[:200])
    check("...and nothing is written or told", wrote_nothing(vault))
    code, out, err = run(root / "nowhere", "--json")
    check("an agent that cannot be read exits 2, said on stderr",
          code == 2 and "could not read" in err, f"{code} {err.strip()[:120]}")
finally:
    for root in roots:
        shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
