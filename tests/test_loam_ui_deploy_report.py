#!/usr/bin/env python3
"""bin/loam-ui-deploy-report deploys only after a restart that went through.

Since 2026-10-14, when loam-ui 0288530 was armed to deploy after the 22:00
PDT broker restart. The restart's report gates it: absent, or present without
the "went through" line, is a failed precondition, and a failed precondition
renders as "not deployed" with the rollback still named.

Run: python3 tests/test_loam_ui_deploy_report.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
loader = importlib.machinery.SourceFileLoader(
    "loam_ui_deploy_report", str(ROOT / "bin" / "loam-ui-deploy-report"))
spec = importlib.util.spec_from_loader(loader.name, loader)
ldr = importlib.util.module_from_spec(spec)
loader.exec_module(ldr)


def check(name: str, ok: bool) -> bool:
    print(f"{'ok  ' if ok else 'FAIL'} {name}")
    return ok


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "restart.md"
        absent = ldr.restart_result(report, 0)
        report.write_text("**The restart did not complete cleanly; how the "
                          "unit was left is below.**\n", encoding="utf-8")
        failed = ldr.restart_result(report, 0)
        report.write_text("# Scheduled broker restart\n\n"
                          "**The restart went through.**\n", encoding="utf-8")
        good = ldr.restart_result(report, 0)

    common = {"fired": "2026-10-15T05:10:00Z", "commit": "0288530",
              "rollback": "d8f219d", "served_commit": "0288530"}
    skipped = ldr.render("[[Ada]]", dry=False, checks=[absent, (True, "HTTP 200")],
                         deployed=None, served=None, relays=[], **common)
    landed = ldr.render("[[Ada]]", dry=False, checks=[good, (True, "HTTP 200")],
                        deployed=(0, "  loam-ui: healthy on loam-ui:0288530"),
                        served=(55, 55, []),
                        relays=["`GET /api/schedules`: HTTP 200, 9 schedules"],
                        **common)
    dry = ldr.render("[[Ada]]", dry=True, checks=[failed], deployed=None,
                     served=(51, 51, []), relays=[], **common)
    results = [
        check("an absent restart report is a failed precondition",
              absent[0] is False and "does not exist" in absent[1]),
        check("a failed restart is a failed precondition", failed[0] is False),
        check("a restart that went through passes", good[0] is True),
        check("a failed precondition is not deployed, rollback named",
              "**Not deployed" in skipped and "## The deploy\n\nNot run." in skipped
              and "deploy loam-ui up d8f219d" in skipped),
        check("a deploy reports its line and the served-file count",
              "**Deployed `loam-ui` at `0288530`.**" in landed
              and "healthy on loam-ui:0288530" in landed
              and "55/55 served files" in landed),
        check("a dry run says what a real run would do",
              "would not deploy" in dry and "(dry run)" in dry),
        check("provenance is the brief's", 'created_by: "[[Ada]]"' in landed),
    ]
    results += schedule_gate(common)
    return 0 if all(results) else 1


def schedule_gate(common: dict) -> list[bool]:
    """Since 2026-10-23: a broker answering 200 is not enough; its rows
    must carry the fields the page reads, which only the restarted broker
    serves. And a deploy whose checks after it failed says so on top."""
    import json
    full = {k: None for k in ldr.SCHEDULE_FIELDS}
    real_get = ldr.get
    try:
        ldr.get = lambda *a, **k: (200, json.dumps({"schedules": [{"agent": "a"}]}).encode(), "")
        old = ldr.broker_schedules()
        ldr.get = lambda *a, **k: (200, json.dumps({"schedules": [full, full]}).encode(), "")
        new = ldr.broker_schedules()
        ldr.get = lambda *a, **k: (200, b'{"schedules": []}', "")
        empty = ldr.broker_schedules()
    finally:
        ldr.get = real_get
    landed = ldr.render("[[Ada]]", dry=False, checks=[(True, "ok")],
                        deployed=(0, "healthy"), served=(55, 55, []), relays=[],
                        after=[(True, "`GET /up`: HTTP 200"),
                               (False, "same-site relay probe: HTTP 200 (want 403)")],
                        **common)
    return [
        check("rows without the 10-15 fields fail the gate, named",
              old[0] is False and "`state_words`" in old[1]),
        check("rows with every field pass", new[0] is True),
        check("no rows cannot prove the fields", empty[0] is False),
        check("a failed check after the deploy is in the headline and listed",
              "1 of 2 checks after it failed" in landed
              and "- NO: same-site relay probe" in landed),
    ]



if __name__ == "__main__":
    sys.exit(main())
