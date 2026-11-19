#!/usr/bin/env python3
"""bin/drift-watch: what the nightly check-drift says, and where.

  clean        nothing told, no failure, the run in the log.
  new          one fact for the system persona per finding the last run
               lacked (lib/loam_attention.py); nothing on the board.
  repeat       the same finding the next night tells nothing.
  clear+back   a finding that cleared and returns, once its fact has been
               quiet, is new again.
  broken       exit 2, a traceback (exit 1 with no finding) and a timeout
               each land in the failure log and are told, and none is a
               drift fact.
  format       the parser reads check-drift's own report(), not a copy of it.

A fake check-drift is a shell one-liner; the vault, log and state are
throwaway.

Run: python3 tests/test_drift_watch.py
"""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import tempfile
from pathlib import Path

ATTENTION = Path(tempfile.mkdtemp(prefix="loam-drift-watch-attention-"))
os.environ["LOAM_ATTENTION_DIR"] = str(ATTENTION)
os.environ.setdefault("LOAM_WAITERS_DIR", str(ATTENTION / "waiters"))

BIN = Path(__file__).resolve().parent.parent / "bin"
results: list[bool] = []


def load(name: str):
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(BIN / name))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    return mod


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail and not ok else ""))


dw = load("drift-watch")
tmp = Path(tempfile.mkdtemp(prefix="loam-drift-watch-"))
vault, logf, state = tmp / "vault", tmp / "drift-check.log", tmp / "state.json"
comms = vault / "00_INBOX" / "from-agents" / "comms.md"
failures = vault / "00_INBOX" / "from-agents" / "loam-wake-failures.md"


def fake(code: int, *findings: str, stderr: str = "") -> list[str]:
    body = "".join(f"echo '### {f}'; " for f in findings)
    err = f"echo '{stderr}' >&2; " if stderr else ""
    return ["sh", "-c", f"echo 'check-drift: 7 agents'; {body}{err}exit {code}"]


def run(cmd: list[str], timeout: float = 10) -> int:
    return dw.watch(cmd, vault, logf, state, timeout)


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""


def told() -> str:
    """The facts, with each finding's double space back as drift-watch
    reads it (a fact's whitespace is folded)."""
    return "\n".join(json.loads(f.read_text())["fact"].replace("] ", "]  ")
                     .replace(".json [", ".json  [").replace(".timer [", ".timer  [")
                     for f in sorted(ATTENTION.glob("2*.json")))


check("clean exits 0", run(fake(0)) == 0)
check("clean tells nothing and fails nothing", told() == "" and not failures.exists())
check("clean is in the log", "[exit 0]" in read(logf))

A = "ada settings.json  [/home/loam/ai-labs/ada/.claude/settings.json]"
B = "static loam-x.timer  [/home/loam/.config/systemd/user/loam-x.timer]"
check("drift exits 0", run(fake(1, A)) == 0)
check("a new finding is one fact", told().count("new drift") == 1 and A in told(), told())
check("and nothing on the board", not comms.exists())
run(fake(1, A))
check("the same finding the next night tells nothing", told().count("new drift") == 1)
run(fake(1, A, B))
check("a second finding beside it tells only itself",
      told().count("new drift") == 2 and B in told())

# A returning finding is new to drift-watch; the fact is told again once its
# key has been quiet (QUIET_DAYS), here by forgetting what was seen.
run(fake(0))
(ATTENTION / "seen.json").unlink()
run(fake(1, A))
check("a finding that cleared and came back is new again", told().count(A) == 2, told())

before = told()
check("exit 2 is a failure", run(fake(2, stderr="boom")) == 2)
check("the failure log names the exit and stderr",
      "exit 2" in read(failures) and "boom" in read(failures), read(failures))
check("a broken check is told, and is no drift fact",
      told().count("new drift") == before.count("new drift") and "check-drift failed" in told())
check("exit 1 with no finding (a traceback) is a failure",
      run(fake(1, stderr="Traceback ...")) == 2 and "exit 1" in read(failures))
check("a timeout is a failure",
      run(["sh", "-c", "sleep 5"], timeout=0.5) == 2 and "did not run" in read(failures))
check("still nothing on the board", not comms.exists())

cd = load("check-drift")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    cd.report([("one label", "/a/path", "diff body"), ("two", "/b", "")], True)
check("the parser reads check-drift's report()",
      dw.findings(out.getvalue()) == ["one label  [/a/path]", "two  [/b]"],
      str(dw.findings(out.getvalue())))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
