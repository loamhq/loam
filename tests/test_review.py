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
# Use the first-party reviewer plugin with scratch user plugin state.
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
                                         "VAULT", "LOG", "REVIEWER", "WAKE_STATE", "PROVIDER",
                                         "MODEL", "EFFORT", "MCP_FILE")}
    saved_agy = rv.agy.AGY
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

        # Run on Antigravity
        on_antigravity(rv, tmp, check)

        print(f"\n{'ALL PASSED' if not failures else f'{failures} FAILED'}")
        return 1 if failures else 0
    finally:
        for k, v in saved.items():
            setattr(rv, k, v)
        rv.agy.AGY = saved_agy
        rv.loam_lock.LOCK_DIR, rv.loam_lock.WAIT_SECONDS = saved_lock
        sys.argv = saved_argv
        shutil.rmtree(tmp, ignore_errors=True)


def refusals_reach_the_failure_log(rv, tmp: Path, check) -> None:
    """Drive each refusal through main twice on the same day and inspect the failure record."""
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

    # A reviewer run can cross the reflection window without a time-based refusal.
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


# A stand-in for `agy` 1.5.9 in print mode: it records how it was started,
# writes a note into the outbox the prompt names (as the reviewer does) and a
# memory page, and answers in the CLI's stream-json events.
FAKE_AGY = r'''#!/usr/bin/env python3
import json, os, re, sys
from pathlib import Path
args = sys.argv[1:]
prompt = next(a[len("--print="):] for a in args if a.startswith("--print="))
Path(os.environ["FAKE_AGY_LOG"]).write_text(json.dumps(
    {"argv": args, "cwd": os.getcwd(),
     "claude_token": "CLAUDE_CODE_OAUTH_TOKEN" in os.environ}))
outbox = re.search(r"write your one note as (\S+)/<persona>/", prompt).group(1)
memory = re.search(r"Your memory is at (\S+)/\.", prompt).group(1)
Path(outbox, "alpha").mkdir(parents=True, exist_ok=True)
Path(outbox, "alpha", "stale-pointer.md").write_text("# A stale pointer\n\nfinding\n")
Path(memory, "alpha.md").write_text("flagged a stale pointer\n")
cid = "conv-review-1"
def say(e): print(json.dumps(e), flush=True)
say({"event": "init", "conversation_id": cid, "init": {"model": args[args.index("--model") + 1]}})
say({"event": "step_update", "step_update": {"conversation_id": cid, "step_index": 1,
     "state": "DONE", "step_type": "tool", "tool_name": "view_file",
     "usage": {"input_tokens": 10, "output_tokens": 2}}})
say({"event": "step_update", "step_update": {"conversation_id": cid, "step_index": 2,
     "state": "DONE", "step_type": "agent_response", "usage": {"input_tokens": 5, "output_tokens": 1}}})
say({"event": "result", "result": {"conversation_id": cid, "status": os.environ.get("FAKE_AGY_STATUS", "SUCCESS"),
     "error": os.environ.get("FAKE_AGY_ERROR"), "num_turns": 1}})
'''


def on_antigravity(rv, tmp: Path, check) -> None:
    """The reviewer on Antigravity: the same lane, prompt, delivery and
    record as on Claude Code; the session is `agy` as the custom agent
    `loam-reviewer`, with its tools, servers and deny rules."""
    import loam_profile  # noqa: PLC0415
    # The choice: Claude Code unless set; Antigravity's model defaults to
    # gemini-4-hydrogen-high, with no effort; a Claude model is not carried over.
    saved_env = os.environ.pop("LOAM_REVIEWER_PROVIDER", None)
    saved_stored = loam_profile.reviewer_stored
    try:
        loam_profile.reviewer_stored = lambda: {}
        check(loam_profile.reviewer_provider() == "claude-code",
              "unset, the reviewer's provider is Claude Code")
        loam_profile.reviewer_stored = lambda: {"provider": "antigravity"}
        check(loam_profile.reviewer_provider() == "antigravity"
              and loam_profile.reviewer_choice("fable") == ("gemini-4-hydrogen-high", None),
              "on Antigravity its model defaults to gemini-4-hydrogen-high, no effort")
        loam_profile.reviewer_stored = lambda: {"provider": "antigravity",
                                                "model": "claude-opus-5-5-high"}
        check(loam_profile.reviewer_choice("fable") == ("claude-opus-5-5-high", None),
              "Claude Opus via Antigravity, when picked")
        loam_profile.reviewer_stored = lambda: {
            "provider": "claude-code", "model": "opus", "effort": "high"}
        os.environ["LOAM_REVIEWER_PROVIDER"] = "antigravity"
        check(loam_profile.reviewer_choice("fable", provider="antigravity")
              == ("gemini-4-hydrogen-high", None),
              "a Claude Code model and effort are never run on Antigravity")
        check(loam_profile.reviewer_provider() == "antigravity",
              "LOAM_REVIEWER_PROVIDER names one for a one-off run")
    finally:
        loam_profile.reviewer_stored = saved_stored
        os.environ.pop("LOAM_REVIEWER_PROVIDER", None)
        if saved_env is not None:
            os.environ["LOAM_REVIEWER_PROVIDER"] = saved_env

    # write_denials: everything beside the way down to the writable folder.
    tree = tmp / "denials"
    for d in ("a/keep/inner", "a/other", "b", "a/keep-not"):
        (tree / d).mkdir(parents=True)
    (tree / "f.txt").write_text("x")
    got = sorted(str(p.relative_to(tree)) for p in rv.write_denials(tree / "a" / "keep", tree))
    check(got == ["a/keep-not", "a/other", "b", "f.txt"],
          "write_denials: every entry beside the way down, and nothing on it")

    # The lane's plugin servers file is live state; the test names none.
    rv.contributions.lane_mcp_file = lambda lane: None
    fake = tmp / "agy-fake"
    fake.write_text(FAKE_AGY)
    fake.chmod(0o755)
    log = tmp / "agy-argv.json"
    os.environ["FAKE_AGY_LOG"] = str(log)
    os.environ["LOAM_AGY_CLI_HOME"] = str(tmp / "agy-home")
    rv.agy.AGY = str(fake)
    rv.PROVIDER, rv.MODEL, rv.EFFORT = "antigravity", "gemini-4-hydrogen-high", None
    rv.MCP_FILE = tmp / "reviewer-mcp.json"
    rv.MCP_FILE.write_text(json.dumps({"mcpServers": {"history": {"command": "h", "args": []}}}))
    saved_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = "not-a-token"
    try:
        code = rv.run("alpha")
    finally:
        if saved_token is None:
            os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        else:
            os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = saved_token
    run_dir = sorted((p for p in rv.RUNS.iterdir() if (p / "runtime").is_file()),
                     key=lambda p: p.name)[-1]
    record = json.loads((run_dir / "run.json").read_text())
    started = json.loads(log.read_text())
    argv = started["argv"]
    check(code == 0 and record["outcome"] == "completed" and record["persona"] == "alpha",
          "a run on Antigravity completes and reviews the persona it was given")
    delivered = Path(record["delivered"])
    check(delivered.parent == rv.COMMONS / "alpha" / "inbox"
          and re.fullmatch(r"\d{4}-\d\d-\d\d-reviewer-stale-pointer\.md", delivered.name)
          and delivered.read_text() == "# A stale pointer\n\nfinding\n",
          "its note is delivered the same way: the persona's inbox, the peer-note name, byte for byte")
    check(record["memory_pages_written"] == ["alpha"] and record["provider"] == "antigravity"
          and record["model"] == "gemini-4-hydrogen-high" and record["api_turns"] == 2
          and record["session_id"] == "conv-review-1" and record["agy_usage"]["input_tokens"] == 15,
          "the record: its memory page, the provider, the model, its round trips and tokens")
    check(argv[argv.index("--agent") + 1] == "loam-reviewer"
          and argv[argv.index("--model") + 1] == "gemini-4-hydrogen-high"
          and "--effort" not in argv and "--dangerously-skip-permissions" in argv
          and started["cwd"] == str(run_dir / "agy") and not started["claude_token"],
          "agy runs as loam-reviewer on the model, in the run's own workspace, without the Claude login")
    prompt = next(a for a in argv if a.startswith("--print="))
    check("<instructions>" in prompt and "instructions" in prompt
          and f"Your outbox for this run is {run_dir / 'outbox'}/" in prompt
          and "pinned by hand to `alpha`" in prompt,
          "the prompt is the Claude reviewer's, its instruction file ahead of it")
    settings = json.loads(Path(argv[argv.index("--settings") + 1]).read_text())
    deny = settings["permissions"]["deny"]
    writes = [d[len("write_file("):-1] for d in deny if d.startswith("write_file(")]
    check({"command(*)", "read_url(*)", "execute_url(*)",
           f"read_file({rv.VAULT / '15_CONVERSATIONS'})"} <= set(deny),
          "deny: the shell, the web, and the vault's 15_CONVERSATIONS/")
    check(not any(rv.STATE.resolve() == Path(w) or rv.STATE.resolve().is_relative_to(Path(w))
                  for w in writes)
          and str(rv.AI_LABS) in [str(Path(w)) for w in writes]
          and str(run_dir / "agy") in writes,
          "deny: every write but the state directory's, and its own agent files")
    agent = (run_dir / "agy" / ".agents" / "agents" / "loam-reviewer.md").read_text()
    tools = re.findall(r"^  - (\S+)$", agent, re.M)
    check(tools == ["view_file", "write_to_file", "replace_file_content",
                    "multi_replace_file_content"]
          and "inheritMcp: true" in agent and "subagent: false" in agent,
          "the agent's tools are the file tools and nothing else (no shell, web, browser, subagent)")
    servers = json.loads((run_dir / "agy" / ".agents" / "mcp_config.json").read_text())["mcpServers"]
    check(set(servers) >= {"history", "files"}
          and servers["files"]["env"]["LOAM_FILES_EXCLUDE"] == str(rv.VAULT / "15_CONVERSATIONS"),
          "its servers: history, and files for listing and search, 15_CONVERSATIONS/ held out")
    files = json.loads((run_dir / "antigravity.json").read_text())
    check((run_dir / "runtime").read_text().strip() == "antigravity"
          and files["conversations"] == ["conv-review-1"],
          "the run directory names its conversation, for the 7-day clear")

    import time  # noqa: PLC0415
    time.sleep(1.1)       # a run id is its second
    os.environ["FAKE_AGY_STATUS"], os.environ["FAKE_AGY_ERROR"] = "ERROR", "RESOURCE_EXHAUSTED"
    try:
        code = rv.run("alpha")
    finally:
        os.environ.pop("FAKE_AGY_STATUS")
        os.environ.pop("FAKE_AGY_ERROR")
    record = json.loads(sorted(p for p in rv.RUNS.glob("*/run.json")
                               if (p.parent / "runtime").is_file())[-1].read_text())
    check(code == 4 and "out of quota: RESOURCE_EXHAUSTED" in record["outcome"]
          and not record["ok"],
          "a failed Antigravity session is a failed run, and says why")


if __name__ == "__main__":
    raise SystemExit(main())
