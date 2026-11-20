#!/usr/bin/env python3
"""Start a wake with small or large vault changes and record a failure when the launch
cannot complete.

Run: python3 tests/test_wake_launch.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="loam-wake-launch-"))
os.environ["LOAM_LOCK_DIR"] = str(TMP / "locks")
sys.path.insert(0, str(AGENTS / "lib"))
import loam_identity  # noqa: E402
import loam_skills  # noqa: E402

AGENT = "wakelaunchtest"
ARG_MAX_ONE = 131072          # MAX_ARG_STRLEN: 32 pages of 4 KiB
results: list[bool] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok and detail:
        print(f"      {detail}")


def load_wake():
    loader = importlib.machinery.SourceFileLoader(
        "wake_under_test", str(AGENTS / "bin" / "wake"))
    spec = importlib.util.spec_from_loader("wake_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def build_vault(root: Path, files: int) -> tuple[Path, str, str]:
    """A vault with one commit, then `files` files added in a second."""
    vault = root / "vault"
    (vault / "00_INBOX" / "from-agents").mkdir(parents=True)
    git(vault.parent, "init", "-q", str(vault))
    git(vault, "config", "user.email", "test@example.invalid")
    git(vault, "config", "user.name", "test")
    (vault / "README.md").write_text("vault\n")
    git(vault, "add", "-A")
    git(vault, "commit", "-qm", "one")
    since = git(vault, "rev-parse", "HEAD")
    deep = vault / "20_NOTES" / ("a-folder-name-long-enough-to-count-" * 2)
    deep.mkdir(parents=True)
    for i in range(files):
        (deep / f"note-{i:05d}-with-a-title-of-ordinary-length.md").write_text(f"{i}\n")
    git(vault, "add", "-A")
    git(vault, "commit", "-qm", "the autocommit")
    return vault, since, git(vault, "rev-parse", "HEAD")


def build_agent(root: Path, vault: Path) -> Path:
    ws = root / "ai-labs" / AGENT
    ws.mkdir(parents=True)
    (ws / "agent.json").write_text(json.dumps({
        "name": AGENT, "display": "Wakelaunchtest",
        "workspace": str(ws), "vault": str(vault),
    }))
    (ws / "map.md").write_text("Reflect.\n")
    return ws


FAKE_CLI = """#!/bin/sh
# Stands in for the CLI: proves the kernel ran it, and says how big its
# arguments were.
out="$1"; shift
max=0; for a in "$@"; do n=${#a}; [ "$n" -gt "$max" ] && max=$n; done
echo "$max" > "$out"
echo '{"subtype": "success", "result": "ok", "num_turns": 1}'
"""


def point_wake(wake, root: Path) -> None:
    wake.STATE_DIR = root / "state" / "wake"
    wake.LOG_DIR = root / "logs"
    loam_identity.preflight_identity = lambda project, model=None: ("c", "o")
    loam_skills.quiet_refresh = lambda *a, **k: None


try:
    # -- 1 and 2: a vault whose changes are over the per-argument limit ---------
    root = TMP / "big"
    vault, since, head = build_vault(root, 2400)
    raw = git(vault, "diff", "--name-status", since, head)
    check(f"the vault's changes are over the kernel's per-argument limit "
          f"({len(raw.encode()):,} bytes > {ARG_MAX_ONE:,})",
          len(raw.encode()) > ARG_MAX_ONE)
    try:
        subprocess.run(["/bin/true", "x" * (len(raw.encode()) + 1)], check=False)
        old_shape = "started"
    except OSError as exc:
        old_shape = f"errno {exc.errno}"
    check("the old shape (those changes inline in one argument) cannot start "
          "here", old_shape == "errno 7", old_shape)

    ws = build_agent(root, vault)
    wake = load_wake()
    point_wake(wake, root)
    wake.STATE_DIR.mkdir(parents=True)
    (wake.STATE_DIR / f"{AGENT}.json").write_text(json.dumps(
        {"last_vault_ref": since, "last_wake": "2026-11-16T11:00:00Z"}))
    cli = root / "claude"
    cli.write_text(FAKE_CLI)
    cli.chmod(0o755)
    sizes = root / "sizes"
    wake.CLAUDE = str(cli)
    real_argv = wake.claude_argv
    wake.claude_argv = lambda *a, **k: [real_argv(*a, **k)[0], str(sizes),
                                        *real_argv(*a, **k)[1:]]
    code = wake.run(ws, AGENT, vault, "2026-11-18T10:00:00Z")
    rows = [json.loads(line) for line in
            (wake.LOG_DIR / f"wake-{AGENT}.jsonl").read_text().splitlines()]
    check("the wake started and finished (exit 0, an ok row)",
          code == 0 and rows[-1].get("ok") is True, f"exit={code} rows={rows}")
    biggest = int(sizes.read_text()) if sizes.exists() else -1
    check(f"the real CLI process ran, its biggest argument {biggest:,} chars",
          0 < biggest < 8_000, str(biggest))
    state = json.loads((wake.STATE_DIR / f"{AGENT}.json").read_text())
    check("the marker advanced to the vault's head", state["last_vault_ref"] == head)

    prompt, _, _ = None, None, None
    (wake.STATE_DIR / f"{AGENT}.json").write_text(json.dumps(
        {"last_vault_ref": since, "last_wake": "2026-11-16T11:00:00Z"}))
    prompt, got_head, _ = wake.lane_prompt(AGENT, vault, "2026-11-18T10:00:00Z", None)
    listed = wake.changes_path(AGENT)
    check("the prompt is small", len(prompt) < 2_000, f"{len(prompt):,}")
    check("it says how many changed, and how",
          "2,400 files: 2,400 added" in prompt, prompt[:400])
    check("it names the file with the list, and the git command",
          str(listed) in prompt
          and f"git -C {vault} diff --name-status {since} {head}" in prompt)
    check("the file holds every line of the list",
          listed.read_text().strip() == raw.strip())
    check("it says the interval starts at the last good wake",
          "since your last wake (2026-11-16T11:00:00Z)" in prompt)

    # A small night is still inline, exactly as before.
    small = TMP / "small"
    sv, ssince, shead = build_vault(small, 3)
    (wake.STATE_DIR / f"{AGENT}.json").write_text(json.dumps(
        {"last_vault_ref": ssince, "last_wake": "2026-11-17T11:00:00Z"}))
    prompt, _, _ = wake.lane_prompt(AGENT, sv, "2026-11-18T10:00:00Z", None)
    check("a small diff is still inline", "A\t20_NOTES/" in prompt
          and "too many to list" not in prompt)
    check("it says where the vault stood at the last wake",
          f"Your last wake saw the vault at commit {ssince[:12]} "
          f"(2026-11-17T11:00:00Z). What has changed since:" in prompt, prompt)
    # No marker at all, and the marker equal to head: no changes.
    (wake.STATE_DIR / f"{AGENT}.json").unlink()
    prompt, _, _ = wake.lane_prompt(AGENT, sv, "2026-11-18T10:00:00Z", None)
    check("no marker yet: said as a first wake, with the vault's commit",
          "This is your first wake here" in prompt and shead[:12] in prompt,
          prompt)
    check("the instruction is Loam's default when the record has none",
          wake.loam_cron.reflection_default().strip() in prompt, prompt)
    # A marker that is not in the history any more.
    (wake.STATE_DIR / f"{AGENT}.json").write_text(json.dumps(
        {"last_vault_ref": "0" * 40, "last_wake": "2026-11-17T11:00:00Z"}))
    prompt, _, _ = wake.lane_prompt(AGENT, sv, "2026-11-18T10:00:00Z", None)
    check("a marker gone from history is said, not read as no changes",
          "no longer in the vault's history" in prompt
          and wake.NO_CHANGES not in prompt, prompt[:300])

    # -- 4: a crash before the model runs ----------------------------------------
    croot = TMP / "crash"
    cvault, _, _ = build_vault(croot, 1)
    cws = build_agent(croot, cvault)
    wake = load_wake()
    point_wake(wake, croot)
    real_run = wake.subprocess.run

    def e2big(argv, **kwargs):
        if argv and str(argv[0]) == wake.CLAUDE:
            raise OSError(7, "Argument list too long", wake.CLAUDE)
        return real_run(argv, **kwargs)
    wake.subprocess.run = e2big
    wake.loam_due.refuse_if_not_due = lambda lane: False
    wake.loam_schedule.record_fire = lambda *a, **k: None
    sys.argv = ["wake", str(cws)]
    code = wake.main()
    rows = [json.loads(line) for line in
            (wake.LOG_DIR / f"wake-{AGENT}.jsonl").read_text().splitlines()]
    log = (cvault / "00_INBOX" / "from-agents" / "loam-wake-failures.md").read_text()
    line = next((x for x in log.splitlines() if "crashed in bin/wake" in x), "")
    print(f"      the failure-log line: {line}")
    check("a crash exits 8", code == 8, str(code))
    check("the wake's own log has a `crash` row with the cause, model not run",
          rows[-1].get("stage") == "crash" and rows[-1].get("at") == "launch"
          and rows[-1].get("model_ran") is False
          and "Argument list too long" in rows[-1].get("error", ""), str(rows[-1]))
    check("the failure log says where it died and that the model never ran",
          "at `launch`, before the model ran: OSError: [Errno 7] Argument list "
          "too long" in line, line)
    check("and the run marker is gone (bin/wake-failed has nothing to repeat)",
          not (wake.STATE_DIR / f"{AGENT}.running").exists())
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
