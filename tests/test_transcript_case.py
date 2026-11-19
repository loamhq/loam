#!/usr/bin/env python3
"""Check exact and case-folded filename collisions through the capture hook."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "transcript_surface", HERE / "test_transcript_surface.py")
surface = importlib.util.module_from_spec(spec)
spec.loader.exec_module(surface)
hook, session_file, fire = surface.hook, surface.session_file, surface.fire

results: list[bool] = []


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:62} got={got!r}")
    if not ok:
        print(f"      wanted {want!r}")


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="loam-case-test-"))
    saved_state, saved_log = hook.STATE_DIR, hook.LOG_PATH
    try:
        workspace = root / "probe"
        conversations = root / "vault" / "15_CONVERSATIONS"
        workspace.mkdir()
        conversations.mkdir(parents=True)
        (workspace / "agent.json").write_text(
            json.dumps({"name": "probe", "emoji": "🧪",
                        "vault": str(root / "vault")}),
            encoding="utf-8")
        hook.STATE_DIR = root / "transcripts"
        hook.LOG_PATH = root / "capture.log"

        def conversation(minute: int, title: str, sid: str) -> None:
            turns = [("user", "smoke", f"2026-09-16T19:{minute:02}:00Z"),
                     ("probe", "ok.", f"2026-09-16T19:{minute:02}:04Z")]
            fire(session_file(root / f"{sid}.jsonl", turns, sid, title),
                 sid, workspace, {})

        def names() -> list[str]:
            return sorted(p.name for p in conversations.iterdir())

        conversation(1, "Smoke test", "s1")
        check("the first conversation takes its title", names(),
              ["Smoke test.md"])

        conversation(2, "smoke test", "s2")
        check("a case-only collision is stepped, not filed alongside", names(),
              ["Smoke test.md", "smoke test 2.md"])
        check("...so no two names fold to one",
              len({n.casefold() for n in names()}), len(names()))

        conversation(3, "Smoke test", "s3")
        check("an exact collision steps past the case-only one too", names(),
              ["Smoke test 3.md", "Smoke test.md", "smoke test 2.md"])

        # A stepped name collides case-only with an existing stepped name.
        conversation(4, "SMOKE TEST", "s4")
        check("a case-only collision with stepped names steps past them all",
              names(),
              ["SMOKE TEST 4.md", "Smoke test 3.md", "Smoke test.md",
               "smoke test 2.md"])

        conversation(5, "Something else", "s5")
        check("an unrelated title keeps its own name",
              "Something else.md" in names(), True)

        # Each conversation's sidecar points at its own file.
        check("each sidecar names its own file",
              [Path(json.loads((hook.STATE_DIR / f"s{i}.json").read_text())
                    ["path"]).name for i in (1, 2, 3, 4)],
              ["Smoke test.md", "smoke test 2.md", "Smoke test 3.md",
               "SMOKE TEST 4.md"])
    finally:
        hook.STATE_DIR, hook.LOG_PATH = saved_state, saved_log
        shutil.rmtree(root, ignore_errors=True)

    print()
    print("FAILURES:", results.count(False))
    sys.exit(1 if False in results else 0)


if __name__ == "__main__":
    main()
