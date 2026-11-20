#!/usr/bin/env python3
"""Route system facts to the responsible persona and drain outstanding facts through a
simulated run.

Run: python3 tests/test_attention.py"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="loam-attention-"))
os.environ["LOAM_ATTENTION_DIR"] = str(TMP / "attention")
os.environ["LOAM_WAITERS_DIR"] = str(TMP / "waiters")
os.environ["LOAM_LOCK_DIR"] = str(TMP / "locks")
os.environ["LOAM_SCHEDULE_STATE"] = str(TMP / "schedules")
os.environ["LOAM_HOST_SETTINGS"] = str(TMP / "settings.json")
os.environ["LOAM_AI_LABS"] = str(TMP / "ai-labs")
os.environ.pop("LOAM_RESULTS_KICK", None)
sys.path.insert(0, str(AGENTS / "lib"))
import loam_attention as la  # noqa: E402
import loam_config as lc  # noqa: E402

results: list[bool] = []


def check(label: str, ok: bool, detail="") -> None:
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok and detail != "":
        print(f"      {detail!r}"[:1500])


def agent(name: str, archived: bool = False) -> Path:
    ws = TMP / "ai-labs" / name
    ws.mkdir(parents=True, exist_ok=True)
    card = {"name": name, "workspace": str(ws), "vault": str(TMP / "vault")}
    (ws / "agent.json").write_text(json.dumps(card))
    if archived:
        import loam_agents  # noqa: PLC0415
        mark = getattr(loam_agents, "ARCHIVED_FILE", None)
        if mark:
            (ws / mark).write_text("{}")
        else:
            card["archived"] = {"at": "2026-11-19T00:00:00Z"}
            (ws / "agent.json").write_text(json.dumps(card))
    return ws


def host(value: dict | None) -> None:
    (TMP / "settings.json").write_text(json.dumps({"system": value} if value else {}))


def records() -> list[dict]:
    return la.pending()


CALLS = TMP / "calls.jsonl"
FAKE_CLI = f"""#!/usr/bin/env python3
import json, sys
argv = sys.argv[1:]
with open({str(CALLS)!r}, "a") as fh:
    fh.write(json.dumps(argv) + "\\n")
sid = argv[argv.index("--session-id") + 1] if "--session-id" in argv else None
print(json.dumps({{"subtype": "success", "result": "", "num_turns": 1,
                  "session_id": sid}}))
"""


def calls() -> list[list[str]]:
    return [json.loads(x) for x in CALLS.read_text().splitlines()] if CALLS.exists() else []


try:
    # -- 1: which persona -----------------------------------------------------
    for name in ("ada", "bo", "cy"):
        agent(name)
    lead = lc.LEAD_AGENT
    lc.LEAD_AGENT = "bo"
    host(None)
    check("the first agent: the lead agent while it is awake", la.persona() == "bo", la.state())
    lc.LEAD_AGENT = "nobody"
    check("...else the first awake agent by name", la.persona() == "ada", la.state())
    lc.LEAD_AGENT = "bo"
    host({"persona": "cy"})
    check("the owner's choice", la.state() == {"persona": "cy", "chosen": "cy", "default": "bo"},
          la.state())
    real_awake = la.awake
    la.awake = lambda: ["ada", "bo"]
    check("a chosen agent that is not awake falls back to the first agent",
          la.persona() == "bo", la.state())
    la.awake = real_awake
    try:
        la.validate("zed")
        refused = False
    except ValueError as exc:
        refused = "not an agent awake" in str(exc)
    check("validate refuses an agent not awake", refused)
    check("validate stores the default as null, and an id otherwise",
          (la.validate("bo"), la.validate("CY"), la.validate(None)) == (None, "cy", None))

    # -- 2: once ----------------------------------------------------------------
    host({"persona": "ada"})
    said = [la.tell("RAM at 91% for 3 min.", by="pressure", kick=False),
            la.tell("RAM at 93% for 4 min.", by="pressure", kick=False),
            la.tell("the backup failed: no key", by="git-backup", evidence="Settings › Plugins", kick=False)]
    check("the same fact with other numbers is a repeat; another fact is told",
          said == ["told", "repeat", "told"], said)
    seen = json.loads((TMP / "attention" / "seen.json").read_text())
    check("...and the repeat is counted, not told",
          seen["pressure:RAM at #% for # min."]["count"] == 2, seen)
    check("two records wait", len(records()) == 2, records())
    seen["pressure:RAM at #% for # min."]["last"] = time.time() - la.QUIET_DAYS * 86400 - 5
    (TMP / "attention" / "seen.json").write_text(json.dumps(seen))
    check("quiet for QUIET_DAYS, the same fact is new again",
          la.tell("RAM at 95% for 3 min.", by="pressure", kick=False) == "told")
    check("an explicit key is the fact's identity",
          [la.tell("job x hung twice", by="engineer-job", key="stuck:x", kick=False),
           la.tell("job x hung twice again", by="engineer-job", key="stuck:x", kick=False)]
          == ["told", "repeat"])

    # -- 3: heard -----------------------------------------------------------------
    cli = TMP / "claude-cli"
    cli.write_text(FAKE_CLI)
    cli.chmod(0o755)
    loader = importlib.machinery.SourceFileLoader("wake_attention", str(AGENTS / "bin" / "wake"))
    wake = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(wake)
    wake.CLAUDE = str(cli)
    wake.LOG_DIR = TMP / "logs"
    wake.STATE_DIR = TMP / "wake-state"
    before = len(records())
    rc = wake.results_main(TMP / "ai-labs" / "bo")
    check("another persona's drain does not take them",
          rc == 0 and calls() == [] and len(records()) == before, calls())
    rc = wake.results_main(TMP / "ai-labs" / "ada")
    got = calls()
    check("the system persona's drain: one fresh run for all of them",
          rc == 0 and len(got) == 1 and "--session-id" in got[0] and "--resume" not in got[0], got)
    text = got[0][got[0].index("-p") + 1] if got else ""
    check("...its input is the facts, who says so and the evidence",
          all(s in text for s in ("RAM at 91% for 3 min.", "From: pressure",
                                  "the backup failed: no key", "Evidence: Settings › Plugins",
                                  "job x hung twice", "4 facts")), text)
    check("...and says nothing was posted, without telling it what to do",
          "nothing has been posted anywhere" in text and "brief" not in text.lower(), text)
    row = [json.loads(x) for x in (TMP / "logs" / "wake-ada.jsonl").read_text().splitlines()][-1]
    check("...its own row in the wake log, lane attention",
          (row["lane"], row["ok"], len(row["facts"])) == ("attention", True, 4), row)
    fires = [json.loads(x) for x in (TMP / "schedules" / "events.jsonl").read_text().splitlines()]
    check("...a fire in the schedule log", fires[-1]["lane"] == "attention"
          and fires[-1]["outcome"] == "ran" and fires[-1]["facts"] == 4, fires[-1])
    check("...and the records gone", records() == [], records())

    # -- 4: never twice -------------------------------------------------------
    CALLS.unlink()
    la.tell("a fact whose run died", by="probe", kick=False)
    la.mark_given(records())
    rc = wake.results_main(TMP / "ai-labs" / "ada")
    log = (TMP / "vault" / "00_INBOX" / "from-agents" / "loam-wake-failures.md")
    check("a fact already given is not given again: logged and removed",
          calls() == [] and records() == []
          and "a fact whose run died" in log.read_text(), (calls(), records()))

    # A failed notification run must remain recoverable.
    CALLS.unlink(missing_ok=True)
    refusing = TMP / "claude-refusing"
    refusing.write_text(FAKE_CLI.replace('"subtype": "success"', '"subtype": "error_during_execution"')
                        + "sys.exit(1)\n")
    refusing.chmod(0o755)
    wake.CLAUDE = str(refusing)
    la.tell("a fact whose run is refused", by="probe", kick=False)
    rc = wake.results_main(TMP / "ai-labs" / "ada")
    back = records()
    check("a fact whose run failed stays pending, not given, a try counted",
          len(calls()) == 1 and len(back) == 1 and not back[0].get("given")
          and back[0].get("tries") == 1 and back[0].get("retry_at"), back)
    rc = wake.results_main(TMP / "ai-labs" / "ada")
    check("...and is not handed over again before its retry time",
          len(calls()) == 1 and len(records()) == 1, calls())
    wake.CLAUDE = str(cli)
    la._write(la.STATE / f"{back[0]['id']}.json", {**back[0], "retry_at": 0})
    rc = wake.results_main(TMP / "ai-labs" / "ada")
    check("...after it, a run that succeeds hears it and removes it",
          len(calls()) == 2 and records() == [], records())

    # -- 5: the seam for plugins ----------------------------------------------
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    outs = [subprocess.run([str(AGENTS / "bin" / "loam"), "tell", "the sandbox spent $40 this month",
                            "--by", "sandbox"], capture_output=True, text=True, env=env).stdout.strip()
            for _ in range(2)]
    check("loam tell: told, then repeat", outs == ["told", "repeat"], outs)
    lc.LEAD_AGENT = lead
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
