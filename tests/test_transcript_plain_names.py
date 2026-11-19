#!/usr/bin/env python3
"""Check plain speaker headers on new turns and byte preservation of existing emoji headers."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(AGENTS / "lib"))

import loam_transcript as lt  # noqa: E402

loader = importlib.machinery.SourceFileLoader(
    "transcript_capture", str(AGENTS / "hooks" / "transcript_capture.py"))
hook = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(hook)

results: list[bool] = []


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:66} got={got!r}")
    if not ok:
        print(f"      wanted {want!r}")


LEGACY = "\N{BRICK}"                       # a face an older agent.json holds
SAID_USER = "\N{WAVING HAND SIGN} hi, how did the beds do? \N{SEEDLING}"
SAID_AGENT = "Thriving \N{SEEDLING}\N{PARTY POPPER}, the tomatoes too \N{TOMATO}."


def session_file(path: Path, turns, session_id: str, title: str) -> Path:
    """A session JSONL of the shape the CLI writes, titled so the hook makes
    no model call."""
    lines = [json.dumps({"type": "ai-title", "aiTitle": title})]
    for speaker, text, stamp in turns:
        if speaker == "user":
            lines.append(json.dumps({
                "type": "user", "promptSource": "cli", "sessionId": session_id,
                "timestamp": stamp, "origin": {"kind": "human"},
                "message": {"role": "user", "content": text}}))
        else:
            lines.append(json.dumps({
                "type": "assistant", "sessionId": session_id, "timestamp": stamp,
                "message": {"role": "assistant",
                            "content": [{"type": "text", "text": text}]}}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def fire(jsonl: Path, session_id: str, workspace: Path) -> None:
    saved_stdin, saved_env = sys.stdin, dict(os.environ)
    sys.stdin = io.StringIO(json.dumps({"transcript_path": str(jsonl),
                                        "session_id": session_id,
                                        "cwd": str(workspace)}))
    for var in (lt.SURFACE_ENV_VAR, lt.ORIGIN_ENV_VAR, lt.PROBE_ENV_VAR):
        os.environ.pop(var, None)
    os.environ["LOAM_WAKE"] = "conversation"
    os.environ["CLAUDE_PROJECT_DIR"] = str(workspace)
    try:
        hook.main()
    except SystemExit:
        pass
    finally:
        sys.stdin = saved_stdin
        os.environ.clear()
        os.environ.update(saved_env)


def header_lines(text: str) -> list[str]:
    return re.findall(r"^### .*$", text, re.M)


def has_emoji(s: str) -> bool:
    return any(ord(c) >= 0x2600 for c in s)


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="loam-plain-names-"))
    try:
        workspace, vault = root / "ada", root / "vault"
        conversations = vault / "15_CONVERSATIONS"
        workspace.mkdir()
        conversations.mkdir(parents=True)
        (workspace / "agent.json").write_text(json.dumps(
            {"name": "ada", "display": "Ada", "emoji": LEGACY, "vault": str(vault)}),
            encoding="utf-8")
        hook.STATE_DIR = root / "transcripts"
        hook.LOG_PATH = root / "capture.log"
        hook.THREAD_MARKERS = root / "markers"

        turns = [("user", SAID_USER, "2026-11-17T19:00:00Z"),
                 ("ada", SAID_AGENT, "2026-11-17T19:00:05Z")]
        fire(session_file(root / "a.jsonl", turns, "sess-a", "Garden beds"),
             "sess-a", workspace)
        text = (conversations / "Garden beds.md").read_text(encoding="utf-8")
        print("\n--- after: the new transcript ---\n" + text + "--- end ---\n")
        check("1. a persona with a legacy emoji: headers are plain names",
              header_lines(text), ["### User", "### Ada"])
        check("...no emoji in any header line",
              [h for h in header_lines(text) if has_emoji(h)], [])
        check("...the legacy face appears nowhere in the file", LEGACY in text, False)
        check("...the person's emoji survive byte for byte", SAID_USER in text, True)
        check("...and so do the agent's", SAID_AGENT in text, True)

        old_turns = [lt.Turn("user", "first \N{SEEDLING}", "2026-11-10T08:00:00Z"),
                     lt.Turn("ada", "an old answer \N{SUN WITH FACE}", "2026-11-10T08:00:04Z")]
        old = (f"---\ntitle: \"Old beds\"\ncreated_at: {old_turns[0].timestamp}\n"
               f"last_activity: {old_turns[1].timestamp}\n---\n\n"
               + "\n\n".join(lt.render_turn(t, "User" if t.speaker == "user"
                                            else f"{LEGACY} Ada") for t in old_turns)
               + "\n")
        path = conversations / "Old beds.md"
        path.write_text(old, encoding="utf-8")
        hook.STATE_DIR.mkdir(parents=True, exist_ok=True)
        (hook.STATE_DIR / "sess-old.json").write_text(json.dumps({"path": str(path)}))
        grown = [(t.speaker, t.content, t.timestamp) for t in old_turns] + [
            ("user", "and now? \N{EYES}", "2026-11-17T19:10:00Z"),
            ("ada", "Still green \N{GREEN HEART}", "2026-11-17T19:10:04Z")]
        fire(session_file(root / "old.jsonl", grown, "sess-old", "Old beds"),
             "sess-old", workspace)
        after = path.read_text(encoding="utf-8")
        print("--- after: an older transcript, one exchange appended ---\n"
              + after + "--- end ---\n")
        body_before = old.split("---\n\n", 1)[1]
        check("2. an older transcript: everything already written is kept byte "
              "for byte", after.split("---\n\n", 1)[1].startswith(body_before.rstrip("\n")),
              True)
        check("...its old emoji headers included, and the new turns plain",
              header_lines(after), ["### User", f"### {LEGACY} Ada", "### User", "### Ada"])
        check("...the new turns' emoji survive",
              ("and now? \N{EYES}" in after, "Still green \N{GREEN HEART}" in after),
              (True, True))

        settled = path.read_bytes()
        fire(root / "old.jsonl", "sess-old", workspace)
        check("3. a firing with nothing new leaves the file as it was",
              path.read_bytes() == settled, True)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
