#!/usr/bin/env python3
"""check-drift's config clause comparison, against a temp ai-labs.

The seven config.md files are compared with each agent's own name masked.
A fleet-wide sentence that names one agent (the per-cron grant, 2026-10-06:
"Today's only grant: Ada's `infra-watch`") is identical in every raw copy
and must not read as drift in the named agent's copy; a real divergence
still must.

Run: python3 tests/test_check_drift.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent

fails = 0


def check(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}{'' if cond else '  | ' + detail}")


def load():
    path = AGENTS / "bin" / "check-drift"
    loader = importlib.machinery.SourceFileLoader("check_drift", str(path))
    spec = importlib.util.spec_from_loader("check_drift", loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def config(name: str, grant: str) -> str:
    return (f"# {name.capitalize()}\n\nYou are {name.capitalize()}, in "
            f"`ai-labs/{name}/`.\n\n## Crons\n\nA cron is a job.\n\n"
            f"{grant}\n")


GRANT = "Today's only grant: Ada's `infra-watch`, 2026-10-06."


def run(cd, configs: dict[str, str]) -> list:
    with tempfile.TemporaryDirectory() as tmp:
        for name, text in configs.items():
            (Path(tmp) / name).mkdir()
            (Path(tmp) / name / "config.md").write_text(text, encoding="utf-8")
        cd.AI_LABS = Path(tmp)
        findings: list = []
        cd.check_config_clauses(findings, sorted(configs))
        return findings


def main() -> int:
    cd = load()
    names = ("ada", "cleo", "juno")

    found = run(cd, {n: config(n, GRANT) for n in names})
    check("a sentence naming one agent, identical everywhere, is not drift",
          not found, repr(found))

    configs = {n: config(n, GRANT) for n in names}
    configs["juno"] = config("juno", GRANT.replace("2026-10-06", "2026-10-07"))
    found = run(cd, configs)
    check("a real divergence in that sentence is still drift",
          [f[0] for f in found] == ["config.md clause 'Crons' in juno"],
          repr(found))

    found = run(cd, {n: config(n, "Your workspace is "
                                  f"`ai-labs/{n}/`.") for n in names})
    check("an agent's own name still masks", not found, repr(found))

    print(f"\n{'OK' if not fails else f'{fails} FAILED'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
