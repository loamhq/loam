#!/usr/bin/env python3
"""A lane's effective model, read back from a transcript shaped like the CLI's.

The alarm added 2026-09-25 after Ada's lane ran most of a conversation on
Opus 4.8 while `ai-labs/ada/.claude/settings.json` said `fable`. The CLI's
`switchModelsOnFlag` default retries a refused turn on another model and
keeps the session there; nothing on this box compared what a lane was
configured for against what it actually ran.

Two readers ask this question -- `bin/check-drift` by hand and
`bin/lane-check` every day for the check on a clock -- and both get it from
lib/loam_session.py, so what is proved here is proved for both.

The two strictnesses are the point and each has a case below: a family
(`fable`) tolerates a point release, an explicit id (`claude-opus-5`) does
not tolerate an older Opus. Without the second, "every Opus use is the
latest Opus" is unenforceable -- a drop from Opus 5 to Opus 4.8 is a match
on family.

Run: python3 tests/test_session_model.py
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# A fault's fact for the system persona lands in scratch, never the box's.
os.environ.setdefault("LOAM_ATTENTION_DIR", tempfile.mkdtemp(prefix="loam-attention-"))
os.environ.setdefault("LOAM_WAITERS_DIR", os.environ["LOAM_ATTENTION_DIR"] + "/waiters")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_session  # noqa: E402

results = []


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:56} got={got!r}")
    if not ok:
        print(f"      wanted {want!r}")


def assistant(model: str, ts: str) -> str:
    return json.dumps({
        "type": "assistant", "timestamp": ts,
        "message": {"role": "assistant", "model": model, "content": []},
    })


# The real record the CLI wrote into Ada's session, fields it is read for.
FALLBACK = json.dumps({
    "type": "system", "subtype": "model_refusal_fallback", "level": "warning",
    "timestamp": "2026-09-25T18:16:34.204Z", "trigger": "refusal",
    "direction": "retry", "scope": "session",
    "originalModel": "claude-fable-5-1", "fallbackModel": "claude-opus-4-8",
    "apiRefusalCategory": "cyber",
})

root = Path(tempfile.mkdtemp(prefix="loam-model-test-"))
saved = loam_session.SESSIONS_DIR
try:
    loam_session.SESSIONS_DIR = root

    # A lane that was switched mid-session, exactly as Ada's was.
    ws = Path("/home/loam/ai-labs/ada")
    d = root / str(ws).replace("/", "-")
    d.mkdir(parents=True)
    (d / "switched.jsonl").write_text("\n".join([
        assistant("claude-fable-5-1", "2026-09-25T17:00:00Z"),
        FALLBACK,
        assistant("claude-opus-4-8", "2026-09-25T18:16:24Z"),
    ]) + "\n", encoding="utf-8")

    seen = loam_session.effective_model(ws)
    check("the LAST turn's model is the effective one", seen[0],
          "claude-opus-4-8")
    check("the session file is named", seen[1], "switched.jsonl")
    check("the refusal record is carried into the report",
          "claude-fable-5-1 -> claude-opus-4-8" in seen[2], True)
    check("the refusal category is carried too", "'cyber'" in seen[2], True)
    check("a switched lane does not match its config",
          loam_session.model_matches("fable", seen[0]), False)

    # A lane with no transcript at all is not a finding: a newly provisioned
    # agent has never run, and reporting it as drift would make provisioning
    # fail its own check.
    check("a lane with no transcript reports nothing",
          loam_session.effective_model(Path("/home/loam/ai-labs/nobody")),
          None)

    # Only the most recent session is read, which is the only way a finding
    # can clear while the offending session is still live and still open.
    later = d / "recovered.jsonl"
    later.write_text(assistant("claude-fable-5-1", "2026-09-26T09:00:00Z") + "\n",
                     encoding="utf-8")
    import os
    os.utime(later, (2 ** 31, 2 ** 31))
    check("a newer clean session clears the finding",
          loam_session.model_matches("fable",
                                     loam_session.effective_model(ws)[0]), True)
    check("and carries no refusal note", loam_session.effective_model(ws)[2], "")

    # A family tolerates a point release; an explicit id tolerates nothing.
    check("family: fable accepts claude-fable-5-1",
          loam_session.model_matches("fable", "claude-fable-5-1"), True)
    check("family: fable accepts a later Fable",
          loam_session.model_matches("fable", "claude-fable-6"), True)
    check("family: fable rejects an Opus",
          loam_session.model_matches("fable", "claude-opus-4-8"), False)
    check("family: a [1m] suffix names a window, not a model",
          loam_session.model_matches("fable[1m]", "claude-fable-5-1"), True)
    check("id: claude-opus-5 accepts itself",
          loam_session.model_matches("claude-opus-5", "claude-opus-5"), True)
    check("id: claude-opus-5 REJECTS an older Opus (the 09-25 rule)",
          loam_session.model_matches("claude-opus-5", "claude-opus-4-8"), False)
    check("id: and would reject a newer one until the pin is moved",
          loam_session.model_matches("claude-opus-5", "claude-opus-5-5"), False)
    check("family alone would have missed the stale Opus",
          loam_session.model_family("claude-opus-4-8")
          == loam_session.model_family("claude-opus-5"), True)

    # The comms line lane-check writes for this finding, through the real
    # `snippet()`. Regression: the first version put the cause in a SECOND
    # sentence, snippet() keeps only the first, and the surviving line --
    # behind record_failure()'s prefix (then "Remote Control start:") -- read
    # as though a Remote Control start had passed the model. Its first reader
    # concluded exactly that and briefed a fix for the wrong thing. What is
    # asserted here is the property that failed: the sentence that SURVIVES
    # truncation names the cause and clears the two innocent suspects.
    import importlib.machinery
    import importlib.util
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
    from loam_inbox import SNIPPET_CHARS, snippet  # noqa: PLC0415

    ldr = importlib.machinery.SourceFileLoader(
        "rcp", str(Path(__file__).resolve().parent.parent / "bin" / "lane-check"))
    spc = importlib.util.spec_from_loader("rcp", ldr)
    rcp = importlib.util.module_from_spec(spc)
    ldr.exec_module(rcp)

    lane = root / "lane"
    (lane / ".claude").mkdir(parents=True)
    (lane / ".claude" / "settings.json").write_text(
        json.dumps({"model": "fable"}), encoding="utf-8")
    ld = root / str(lane).replace("/", "-")
    ld.mkdir(parents=True)
    (ld / "s.jsonl").write_text("\n".join([
        assistant("claude-fable-5-1", "2026-09-25T17:00:00Z"),
        FALLBACK,
        assistant("claude-opus-4-8", "2026-09-25T18:16:24Z"),
    ]) + "\n", encoding="utf-8")

    # A chat may pick a model from the broker's + menu, and that is the owner
    # choosing rather than a lane drifting. ada already has a session
    # recorded with `model: "opus"`, so a check that could not tell the two
    # apart would have fired on a deliberate pick -- and an alarm that cries
    # wolf is the one that gets ignored, which is the failure mode that let
    # the 09-25 switch and the three before it go unnoticed for three weeks.
    import loam_broker  # noqa: PLC0415
    saved_broker = loam_broker.SESSION_DIR
    loam_broker.SESSION_DIR = root / "broker"
    (root / "broker").mkdir()

    def asked_for(model):
        (root / "broker" / "s.json").write_text(
            json.dumps({"agent": "lane", "model": model}), encoding="utf-8")

    def ran(model):
        (ld / "s.jsonl").write_text(assistant(model, "2026-09-25T20:00:00Z")
                                    + "\n", encoding="utf-8")

    ran("claude-opus-5")
    asked_for("opus")
    rcp.MODEL_MEMORY = root / "model-alarm"

    def flagged():
        return rcp.model_problem(lane) is not None

    check("a model the chat ASKED for is not a finding", flagged(), False)
    asked_for(None)
    check("the same model with nobody asking IS a finding", flagged(), True)
    asked_for("opus")
    ran("claude-haiku-4-5-20251001")
    check("asked for opus but ran something else IS a finding", flagged(), True)

    # An ADOPTED session's record is named for the conversation it adopted;
    # the transcript being written is the fork, whose id the record keeps as
    # `cli_session_id`. The lookup has to find the record from that side.
    (root / "broker" / "s.json").unlink()
    (root / "broker" / "adopted.json").write_text(json.dumps(
        {"agent": "lane", "model": "opus", "cli_session_id": "s"}),
        encoding="utf-8")
    ran("claude-opus-5")
    check("a choice on an adopted record is found by its fork's id",
          flagged(), False)
    (root / "broker" / "adopted.json").unlink()
    loam_broker.SESSION_DIR = saved_broker

    (ld / "s.jsonl").write_text("\n".join([
        assistant("claude-fable-5-1", "2026-09-25T17:00:00Z"),
        FALLBACK,
        assistant("claude-opus-4-8", "2026-09-25T18:16:24Z"),
    ]) + "\n", encoding="utf-8")
    model_problem = (rcp.model_problem(lane) or ("",))[0]
    line = snippet("Nightly lane check: " + model_problem)
    check("a model finding is raised at all", bool(model_problem), True)
    check("the comms line is not truncated", len(line) <= SNIPPET_CHARS
          and not line.endswith("…"), True)
    check("the surviving sentence names the refusal",
          "refusal" in line, True)
    check("it clears the run that found it",
          "not this run" in line, True)
    check("it clears the launch path",
          "no launch path chose it" in line, True)
    check("it names both models",
          "`fable`" in line and "`claude-opus-4-8`" in line, True)

    # --- 2026-09-28: the right session, a choice is a choice, and once. ---

    def set_model(text: str, ts: str, kind: str = "user") -> str:
        body = f"<local-command-stdout>Set model to {text}</local-command-stdout>"
        if kind == "system":
            return json.dumps({"type": "system", "subtype": "local_command",
                               "timestamp": ts, "content": body})
        return json.dumps({"type": "user", "timestamp": ts,
                           "message": {"role": "user", "content": body}})

    # 1. THE RIGHT SESSION. The Remote Control server's own UUID-v5 file is
    # touched at every start by an undated `atis-latch` record, so by mtime
    # a session whose last turn was 09-17 became the newest on disk every
    # night and outranked the real sessions of the night before. The shape
    # here is juno's on 09-28: that file, touched after a newer real one.
    rc = root / "rc-lane"
    rd = root / str(rc).replace("/", "-")
    rd.mkdir(parents=True)
    stale = rd / "65f9d85d-6c5f-5204-b3c3-5b2ab13f9f94.jsonl"
    stale.write_text("\n".join([
        assistant("claude-opus-5", "2026-09-11T17:40:26.398Z"),
        assistant("claude-opus-5", "2026-09-17T20:02:46.382Z"),
        json.dumps({"type": "atis-latch", "atis": "",
                    "sessionId": stale.stem}),
    ]) + "\n", encoding="utf-8")
    fresh = rd / "bf3c7767-2d79-4ead-9dc9-a3b7d609af4f.jsonl"
    fresh.write_text(assistant("claude-fable-5-1", "2026-09-27T10:02:00.000Z")
                     + "\n", encoding="utf-8")
    os.utime(fresh, (1_790_500_000, 1_790_500_000))   # 2026-09-27
    os.utime(stale, (1_790_587_000, 1_790_587_000))   # the next night, later
    check("fixture: the stale file IS the newer by mtime",
          stale.stat().st_mtime > fresh.stat().st_mtime, True)
    seen = loam_session.effective_model(rc)
    check("the newest DATED turn wins, not the newest touch",
          seen.session, fresh.name)
    check("and its model is what is read", seen.model, "claude-fable-5-1")
    check("and its last record is what ranked it",
          seen.last, "2026-09-27T10:02:00.000Z")
    fresh.unlink()
    check("with nothing newer, the old session is still read",
          loam_session.effective_model(rc).session, stale.name)
    stale.unlink()

    # 2. A CHOSEN MODEL IS NOT DRIFT. Ada's 09-03 session, in the order its
    # records came: a refusal moved it to Opus 4.8, then on 09-11 a /model
    # chose Opus 5 and it ran there. The alarm named the refusal and the
    # wrong model for three nights; the session's own record says chosen.
    (rd / "ada.jsonl").write_text("\n".join([
        assistant("claude-fable-5", "2026-09-03T01:39:00.000Z"),
        FALLBACK.replace("2026-09-25T18:16:34.204Z", "2026-09-03T01:40:06.606Z"),
        assistant("claude-opus-4-8", "2026-09-03T01:40:10.000Z"),
        set_model("claude-fable-5", "2026-09-03T01:42:00.000Z"),
        assistant("claude-fable-5", "2026-09-03T01:43:00.000Z"),
        set_model("`claude-opus-5`", "2026-09-11T17:27:00.000Z"),
        assistant("claude-opus-5", "2026-09-12T08:31:00.000Z"),
    ]) + "\n", encoding="utf-8")
    seen = loam_session.effective_model(rc)
    check("a later /model clears the earlier refusal", seen.refusal, "")
    check("chosen: /model then its model is 'chosen'",
          loam_session.model_verdict("fable", seen, None)[0], "chosen")
    check("chosen: the reason names the command",
          "/model claude-opus-5" in loam_session.model_verdict("fable", seen, None)[1],
          True)

    # The broker's own shape of the same command: a system record, a
    # marketing name, a trailing clause, and a <synthetic> acknowledgement
    # turn that is not a model anything ran on.
    (rd / "ada.jsonl").write_text("\n".join([
        assistant("claude-fable-5-1", "2026-09-22T02:16:00.000Z"),
        set_model("`Opus 5` for this session only\n     .claude/settings.json "
                  "pins `Fable 5.1` — that applies on restart",
                  "2026-09-22T02:16:41.166Z", kind="system"),
        assistant("<synthetic>", "2026-09-22T02:16:41.200Z"),
        assistant("claude-opus-5", "2026-09-22T02:17:00.000Z"),
    ]) + "\n", encoding="utf-8")
    seen = loam_session.effective_model(rc)
    check("the app's `Opus 5` names the opus family",
          seen.chosen.split(" at ")[0], "opus-5")
    check("chosen: Opus 5 picked in-session is chosen",
          loam_session.model_verdict("fable", seen, None)[0], "chosen")
    (rd / "ada.jsonl").write_text("\n".join([
        assistant("claude-opus-5", "2026-09-22T02:17:00.000Z"),
        set_model("`Opus 5` for this session only", "2026-09-22T02:18:00.000Z",
                  kind="system"),
        assistant("<synthetic>", "2026-09-22T02:18:00.100Z"),
    ]) + "\n", encoding="utf-8")
    check("a <synthetic> turn is never the effective model",
          loam_session.effective_model(rc).model, "claude-opus-5")

    # Drift, unchanged: a refusal after the choice is drift again, and so is
    # a model no record explains at all.
    (rd / "ada.jsonl").write_text("\n".join([
        set_model("fable", "2026-09-25T17:00:00.000Z"),
        assistant("claude-fable-5-1", "2026-09-25T17:00:01.000Z"),
        FALLBACK,
        assistant("claude-opus-4-8", "2026-09-25T18:16:40.000Z"),
    ]) + "\n", encoding="utf-8")
    seen = loam_session.effective_model(rc)
    check("drift: a refusal after a /model is drift",
          loam_session.model_verdict("fable", seen, None)[0], "drift")
    check("drift: and the refusal is what explains the last turn",
          seen.refusal_to == seen.model, True)
    (rd / "ada.jsonl").write_text(
        assistant("claude-opus-4-8", "2026-09-25T18:16:40.000Z") + "\n",
        encoding="utf-8")
    check("drift: a model no record explains",
          loam_session.model_verdict("fable",
                                     loam_session.effective_model(rc), None)[0],
          "drift")
    (rd / "ada.jsonl").unlink()

    # 3. ONCE, AND LABELLED RIGHT, through lane-check's real main().
    vault = root / "vault"
    (vault / "00_INBOX" / "from-agents").mkdir(parents=True)
    lane_cfg = root / "lane-project"
    lane_cfg.mkdir()
    (lane_cfg / "agent.json").write_text(json.dumps({
        "name": "lane", "workspace": str(lane), "vault": str(vault)}))
    for name in ("_always.md", "_active.md"):
        (lane / name).write_text("---\nname: conversation\n"
                                 "keep-coding-instructions: true\n---\nx\n")
    (lane / ".claude" / "output-styles").mkdir()
    (lane / ".claude" / "output-styles" / "conversation.md").symlink_to(
        "../../_active.md")
    reported: list[str] = []

    def start() -> None:
        """One daily read by the check on a clock: ``lane-check --json``."""
        import io
        from contextlib import redirect_stderr, redirect_stdout
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            rcp.main(["--json", str(lane_cfg)])
        reported.extend(p for p in json.loads(out.getvalue())["problems"] if "EARLIER" in p)

    shutil.rmtree(rcp.MODEL_MEMORY, ignore_errors=True)
    # A refusal to Opus 4.8, and a last turn on Opus 5 nothing chose: the
    # line must name Opus 5, and must not blame the refusal for it.
    (ld / "s.jsonl").write_text("\n".join([
        assistant("claude-fable-5-1", "2026-09-25T17:00:00Z"),
        FALLBACK,
        assistant("claude-opus-4-8", "2026-09-25T18:16:24Z"),
        assistant("claude-opus-5", "2026-09-25T19:00:00Z"),
    ]) + "\n", encoding="utf-8")
    text = rcp.model_problem(lane)[0]
    check("label: the line names the model the last turn ran",
          "`claude-opus-5`" in text and "opus-4-8" not in text, True)
    check("label: and does not blame a refusal that moved it elsewhere",
          "refusal" in text, False)

    start()
    check("once: the first read reports it", len(reported), 1)
    memory = json.loads((rcp.MODEL_MEMORY / "lane.json").read_text())
    check("once: the memory holds the session and its last turn",
          (memory["session"], memory["last"]), ("s.jsonl", "2026-09-25T19:00:00Z"))
    start()
    check("once: the next read does not report it again", len(reported), 1)
    # The same session going on drifting is still the same session.
    with (ld / "s.jsonl").open("a") as fh:
        fh.write(assistant("claude-opus-5", "2026-09-26T09:00:00Z") + "\n")
    start()
    check("once: new turns in a reported session are not a new report",
          len(reported), 1)
    # A different, newer drifted session IS new.
    (ld / "t.jsonl").write_text(
        assistant("claude-haiku-4-5-20251001", "2026-09-27T09:00:00Z") + "\n",
        encoding="utf-8")
    start()
    check("once: a newer drifted session is reported", len(reported), 2)
    check("once: naming its own model",
          "claude-haiku-4-5-20251001" in reported[-1], True)
    (ld / "t.jsonl").unlink()

    # A scheduled lane runs on agent.json's model (2026-11-19): a session on
    # it is configured, not drift; on neither model it still is.
    shutil.rmtree(rcp.MODEL_MEMORY, ignore_errors=True)
    (ld / "u.jsonl").write_text(assistant("claude-opus-5-5", "2026-09-28T09:00:00Z") + "\n",
                                encoding="utf-8")
    (lane / "agent.json").write_text(json.dumps({"name": "lane", "model": "opus"}))
    check("lane model: a session on agent.json's model is not drift",
          rcp.model_problem(lane), None)
    (lane / "agent.json").write_text(json.dumps({"name": "lane", "model": "haiku"}))
    check("lane model: on neither model it still is",
          bool(rcp.model_problem(lane)), True)
    (lane / "agent.json").unlink()
    (ld / "u.jsonl").unlink()
finally:
    loam_session.SESSIONS_DIR = saved
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
