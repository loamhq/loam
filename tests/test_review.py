#!/usr/bin/env python3
"""bin/review: the parts that are code because prose cannot be trusted with
them -- delivery (one persona, one note, the peer-note name), the facts
sheet's ordering, and what counts as a persona having been reviewed.

Everything runs against a throwaway tree; nothing touches the live state,
the live commons or the live records. The module's path constants are
pointed at the throwaway and restored.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
# The reviewer is a first-party plugin (2026-11-14, L9), on by default: this
# tree's own, with a scratch Yours holding no choice.
os.environ["LOAM_PLUGIN_SHIPPED"] = str(AGENTS / "plugins")
os.environ["LOAM_PLUGINS_DIR"] = tempfile.mkdtemp(prefix="review-test-yours-")


def load_review():
    loader = importlib.machinery.SourceFileLoader("review", str(AGENTS / "bin" / "review"))
    spec = importlib.util.spec_from_loader("review", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def main() -> int:
    rv = load_review()
    tmp = Path(tempfile.mkdtemp(prefix="review-test-"))
    saved = {k: getattr(rv, k) for k in ("AI_LABS", "COMMONS", "STATE", "RUNS", "MEMORY", "NOTES", "INBOX",
                                         "VAULT", "LOG", "REVIEWER", "WAKE_STATE")}
    saved_lock = (rv.loam_lock.LOCK_DIR, rv.loam_lock.WAIT_SECONDS)
    saved_argv = sys.argv
    try:
        rv.AI_LABS = tmp / "ai-labs"
        rv.COMMONS = rv.AI_LABS / "commons"
        rv.STATE = tmp / "state"
        rv.RUNS = rv.STATE / "runs"
        rv.MEMORY = rv.STATE / "memory"
        rv.NOTES = rv.STATE / "notes"
        rv.INBOX = rv.COMMONS / "reviewer"
        for name in ("alpha", "beta"):
            (rv.AI_LABS / name).mkdir(parents=True)
            (rv.AI_LABS / name / "agent.json").write_text("{}")
        (rv.AI_LABS / "commons").mkdir()
        rv.RUNS.mkdir(parents=True)
        failures = 0

        def check(cond: bool, what: str) -> None:
            nonlocal failures
            print(("ok   " if cond else "FAIL ") + what)
            if not cond:
                failures += 1

        # --- slugify: the convention's name from whatever the reviewer wrote
        check(rv.slugify("Stale Pointers in _always", "2026-09-09") == "stale-pointers-in-always",
              "slugify lowercases and hyphenates")
        check(rv.slugify("2026-09-09-reviewer-stale-pointers", "2026-09-09") == "stale-pointers",
              "slugify strips a date and reviewer- prefix the reviewer already wrote")
        check(rv.slugify("", "2026-09-09") == "note", "slugify of nothing is 'note'")

        # --- deliver: one persona, one note
        def fresh_run(n: str) -> tuple[Path, Path]:
            run_dir = rv.RUNS / n
            outbox = run_dir / "outbox"
            outbox.mkdir(parents=True)
            return run_dir, outbox

        run_dir, outbox = fresh_run("r1")
        persona, delivered, defect = rv.deliver(outbox, run_dir, ["alpha", "beta"], "2026-09-09")
        check((persona, delivered, defect) == (None, None, None), "empty outbox delivers nothing, no defect")

        run_dir, outbox = fresh_run("r2")
        (outbox / "alpha").mkdir()
        (outbox / "alpha" / "two things.md").write_text("# note\n\nfinding\n")
        persona, delivered, defect = rv.deliver(outbox, run_dir, ["alpha", "beta"], "2026-09-09")
        target = rv.COMMONS / "alpha" / "inbox" / "2026-09-09-reviewer-two-things.md"
        check(persona == "alpha" and defect is None, "one note to one persona is delivered")
        check(delivered == str(target) and target.read_text() == "# note\n\nfinding\n",
              "delivered into commons/<persona>/inbox/ under the peer-note name, byte for byte")
        check((rv.NOTES / "alpha" / target.name).read_text() == target.read_text(),
              "the launcher keeps its own copy under state/notes/<persona>/")

        run_dir, outbox = fresh_run("r3")
        (outbox / "alpha").mkdir()
        (outbox / "alpha" / "again.md").write_text("x")
        (outbox / "beta").mkdir()
        (outbox / "beta" / "also.md").write_text("y")
        persona, delivered, defect = rv.deliver(outbox, run_dir, ["alpha", "beta"], "2026-09-09")
        check(delivered is None and "2 personas" in (defect or ""), "notes to two personas: nothing delivered, defect named")
        check(not (rv.COMMONS / "beta").exists(), "the second persona's inbox was never created")

        run_dir, outbox = fresh_run("r4")
        (outbox / "alpha").mkdir()
        (outbox / "alpha" / "one.md").write_text("x")
        (outbox / "alpha" / "two.md").write_text("y")
        persona, delivered, defect = rv.deliver(outbox, run_dir, ["alpha", "beta"], "2026-09-09")
        check(delivered is None and "2 files" in (defect or ""), "two notes to one persona: nothing delivered")

        run_dir, outbox = fresh_run("r5")
        (outbox / "nobody").mkdir()
        (outbox / "nobody" / "n.md").write_text("x")
        persona, delivered, defect = rv.deliver(outbox, run_dir, ["alpha", "beta"], "2026-09-09")
        check(delivered is None and "not on the roster" in (defect or ""), "a persona not on the roster is refused")

        run_dir, outbox = fresh_run("r6")
        (outbox / "alpha").mkdir()
        (outbox / "alpha" / "empty.md").write_text("  \n")
        persona, delivered, defect = rv.deliver(outbox, run_dir, ["alpha", "beta"], "2026-09-09")
        check(delivered is None and "empty" in (defect or ""), "an empty note is not delivered")

        run_dir, outbox = fresh_run("r7")
        (outbox / "alpha").mkdir()
        (outbox / "alpha" / "two things.md").write_text("second night, same slug")
        persona, delivered, defect = rv.deliver(outbox, run_dir, ["alpha", "beta"], "2026-09-09")
        check(delivered and delivered != str(target) and Path(delivered).exists(),
              "a same-day name collision gets a suffix rather than overwriting")

        # --- reviewed(): what counts
        check(rv.reviewed({"persona": "alpha", "outcome": "completed"}), "a delivered note counts as reviewed")
        check(rv.reviewed({"persona": "alpha", "outcome": "completed, no note"}), "a memory page with no note counts")
        check(not rv.reviewed({"persona": None, "outcome": "completed, reviewed nobody"}), "reviewing nobody does not")
        check(not rv.reviewed({"persona": "alpha", "outcome": "cut off at the wall clock"}), "a cut-off run does not")

        # --- last_reviews and the sheet's order
        records = [
            {"persona": "beta", "outcome": "completed", "started": "2026-09-01T10:00:00Z"},
            {"persona": "alpha", "outcome": "completed", "started": "2026-09-05T10:00:00Z"},
            {"persona": "beta", "outcome": "completed, no note", "started": "2026-09-07T10:00:00Z"},
        ]
        last = rv.last_reviews(records)
        check(last == {"beta": "2026-09-07T10:00:00Z", "alpha": "2026-09-05T10:00:00Z"},
              "last_reviews keeps the latest reviewing run per persona")
        for i, rec in enumerate(records):
            (rv.RUNS / f"x{i}").mkdir()
            (rv.RUNS / f"x{i}" / "run.json").write_text(json.dumps(rec))
        (rv.AI_LABS / "gamma").mkdir()
        (rv.AI_LABS / "gamma" / "agent.json").write_text("{}")
        rv.git = lambda *a: ""   # no repo under the throwaway; git facts read as unknown
        sheet, rows = rv.facts(datetime(2026, 9, 9, 12, tzinfo=timezone.utc), None)
        check([r["name"] for r in rows] == ["gamma", "alpha", "beta"],
              "never-reviewed first, then least recently reviewed")
        check(rows[1]["days_since_review"] == 4 and rows[2]["days_since_review"] == 2,
              "days since review computed from the record")
        check("| gamma | ? |" in sheet and "never" in sheet, "the sheet renders unknowns as unknowns")
        sheet, _ = rv.facts(datetime(2026, 9, 9, tzinfo=timezone.utc), "alpha")
        check("Pinned by hand" in sheet and "`alpha`" in sheet, "a pinned persona is stated on the sheet")

        # --- refusals: every one is a line in the failure log
        refusals_reach_the_failure_log(rv, tmp, check)

        print(f"\n{'ALL PASSED' if not failures else f'{failures} FAILED'}")
        return 1 if failures else 0
    finally:
        for k, v in saved.items():
            setattr(rv, k, v)
        rv.loam_lock.LOCK_DIR, rv.loam_lock.WAIT_SECONDS = saved_lock
        sys.argv = saved_argv
        shutil.rmtree(tmp, ignore_errors=True)


def refusals_reach_the_failure_log(rv, tmp: Path, check) -> None:
    """Cause each of bin/review's refusals through main(), twice on one date,
    and read the failure log: one line per refusal, agent `reviewer`, in the
    shape bin/wake's lines have. Until 2026-10-13 the second refusal of a
    UTC date was dropped as a repeat (the 09-09 20:36Z pair)."""
    rv.VAULT = tmp / "vault"
    rv.LOG = tmp / "logs" / "reviewer.jsonl"
    rv.REVIEWER = tmp / "reviewer"
    rv.WAKE_STATE = tmp / "wake-state"
    rv.loam_lock.LOCK_DIR = tmp / "locks"
    rv.loam_lock.WAIT_SECONDS = 0
    rv.REVIEWER.mkdir()
    sys.argv = ["review"]
    log = rv.VAULT / "00_INBOX" / "from-agents" / "loam-wake-failures.md"

    def lines() -> list[str]:
        if not log.exists():
            return []
        return [l for l in log.read_text().splitlines() if l.startswith("- `")]

    shape = re.compile(r"^- `\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ` \*\*reviewer\*\* — review ")

    # preflight: no instruction file
    codes = [rv.main(), rv.main()]
    got = lines()
    check(codes == [3, 3] and len(got) == 2 and all("preflight refused" in l for l in got),
          "two preflight refusals on one date are two failure-log lines")

    # recycle: nothing refuses a run at 01:30 since 2026-11-10, so there is
    # no refusal branch left to cause; the reviewer has no clock either.
    (rv.REVIEWER / "CLAUDE.md").write_text("instructions\n")
    (rv.REVIEWER / ".claude").mkdir()
    (rv.REVIEWER / ".claude" / "settings.json").write_text("{}\n")
    check(not hasattr(rv.eng, "crosses_recycle") and rv.WALL_SECONDS is None
          and rv.MAX_TURNS is None,
          "no recycle refusal, wall clock or round-trip cap on the reviewer")

    # lock: another run holds the reviewer's lock
    with rv.loam_lock.hold(rv.AGENT, rv.LANE, wait_seconds=0):
        codes = [rv.main(), rv.main()]
    got = lines()
    check(codes == [0, 0] and sum("run lock and gave up" in l for l in got) == 2,
          "two lock refusals on one date are two failure-log lines")

    check(len(got) == 4 and all(shape.match(l) for l in got),
          "every line is `- `<stamp>` **reviewer** — review ...`, bin/wake's shape")
    stages = [json.loads(l)["stage"] for l in rv.LOG.read_text().splitlines()]
    check(stages == ["preflight"] * 2 + ["lock_timeout"] * 2,
          "each refusal is also in the reviewer's own log")
    comms = rv.VAULT / "00_INBOX" / "from-agents" / "comms.md"
    check(not comms.exists(), "and none of them posts a comms line")


if __name__ == "__main__":
    raise SystemExit(main())
