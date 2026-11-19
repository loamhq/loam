#!/usr/bin/env python3
"""check-drift's config clause comparison, against a temp ai-labs.

The seven config.md files are compared with each agent's own name masked.
A fleet-wide sentence that names one agent (the per-cron grant, 2026-10-06:
"Today's only grant: Ada's `infra-watch`") is identical in every raw copy
and must not read as drift in the named agent's copy; a real divergence
still must.

The reviewer on another provider (Antigravity) is not compared with the
newest Claude Code session on disk, which is from before the switch
(2026-11-16: a "family mismatch" no run had); on Claude Code it still is.

`--quiet` prints one line per finding saying what differs.

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


def lane_checks(cd) -> None:
    import loam_engineer as eng
    asked = []
    eng.engineer_choice = lambda project, settings=None: {"runtime": "claude", "model": "fable"}
    eng.platform_project = lambda: None
    cd.sess.effective_model = lambda ws: (asked.append(ws.name) or
                                          ("claude-fable-5-2", "s.jsonl", None))
    cd.sess.model_verdict = lambda configured, seen, chosen: (
        ("ok", "") if configured in seen[0] else ("drift", ""))
    cd.brk.chosen_model = lambda stem: None
    prov = type("P", (), {"REVIEWER_MODEL": "fable"})

    cd.loam_profile.reviewer_provider = lambda: "antigravity"
    cd.loam_profile.reviewer_choice = lambda model, *a, **k: ("gemini-4-hydrogen-high", None)
    findings: list = []
    notes = cd.check_lane_models(findings, [], prov)
    check("the reviewer on Antigravity is not compared with a Claude session",
          "reviewer" not in asked and not findings, repr((asked, findings)))
    check("...and the note says where it runs",
          any("reviewer: on Antigravity" in n for n in notes),
          repr(notes))

    cd.loam_profile.reviewer_provider = lambda: "claude-code"
    cd.loam_profile.reviewer_choice = lambda model, *a, **k: ("opus", None)
    findings = []
    cd.check_lane_models(findings, [], prov)
    check("the reviewer on Claude Code still is",
          [f[0] for f in findings] == ["reviewer effective model"], repr(findings))


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

    lane_checks(cd)

    # --quiet says what differs in one line, never a bare heading (2026-11-19).
    diff = ("rendered from role.md @ aaa; the source is now @ bbb. Re-render.\n"
            "--- live:x\n+++ generator\n@@ -24,2 +24,3 @@\n same\n+- **New line.**")
    line = cd.one_line(diff)
    check("--quiet: a moved source and its diff are one line",
          line == "rendered from role.md @ aaa; the source is now @ bbb: +1/-0 "
                  "line(s) against the generator from line 24, first +- **New line.**",
          repr(line))
    check("--quiet: a message alone is its first line",
          cd.one_line("\nfirst\nsecond") == "first", cd.one_line("\nfirst\nsecond"))
    check("--quiet: a long line is cut", len(cd.one_line("x" * 500)) == 240)

    print(f"\n{'OK' if not fails else f'{fails} FAILED'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
