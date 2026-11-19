#!/usr/bin/env python3
"""Check launch surface, thread ownership and fork adoption through the Stop hook."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))

import loam_transcript as lt  # noqa: E402

loader = importlib.machinery.SourceFileLoader(
    "transcript_capture", str(AGENTS / "hooks" / "transcript_capture.py"))
spec = importlib.util.spec_from_loader(loader.name, loader)
hook = importlib.util.module_from_spec(spec)
loader.exec_module(hook)

results: list[bool] = []


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:62} got={got!r}")
    if not ok:
        print(f"      wanted {want!r}")


def session_file(path: Path, turns: list[tuple[str, str, str]],
                 session_id: str, title: str) -> Path:
    """Create a titled session fixture so capture needs no model call."""
    lines = [json.dumps({"type": "ai-title", "aiTitle": title})]
    for speaker, text, stamp in turns:
        if speaker == "user":
            lines.append(json.dumps({
                "type": "user", "promptSource": "cli",
                "sessionId": session_id, "timestamp": stamp,
                "origin": {"kind": "human"},
                "message": {"role": "user", "content": text}}))
        else:
            lines.append(json.dumps({
                "type": "assistant", "sessionId": session_id,
                "timestamp": stamp,
                "message": {"role": "assistant",
                            "content": [{"type": "text", "text": text}]}}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def fire(jsonl: Path, session_id: str, workspace: Path, env: dict) -> None:
    """The hook, for real: a payload on stdin and an environment around it."""
    saved_stdin, saved_env = sys.stdin, dict(os.environ)
    sys.stdin = io.StringIO(json.dumps({
        "transcript_path": str(jsonl), "session_id": session_id,
        "cwd": str(workspace)}))
    os.environ.pop(lt.SURFACE_ENV_VAR, None)
    os.environ.pop(lt.ORIGIN_ENV_VAR, None)
    # Simulate a conversation regardless of the test runner’s lane.
    os.environ.pop(lt.PROBE_ENV_VAR, None)
    os.environ["LOAM_WAKE"] = "conversation"
    os.environ["CLAUDE_PROJECT_DIR"] = str(workspace)
    os.environ.update(env)
    try:
        hook.main()
    except SystemExit:
        pass
    finally:
        sys.stdin = saved_stdin
        os.environ.clear()
        os.environ.update(saved_env)


def frontmatter(path: Path) -> dict:
    fields = {}
    for line in path.read_text(encoding="utf-8").split("\n")[1:]:
        if line.strip() == "---":
            break
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip().strip('"')
    return fields


def headers(path: Path) -> int:
    """Count frontmatter outside code fences, where a conversation can quote another header."""
    fenced = False
    blocks = 0
    lines = path.read_text(encoding="utf-8").split("\n")
    for i, line in enumerate(lines):
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and line.strip() == "---" and \
                i + 1 < len(lines) and lines[i + 1].startswith("title: "):
            blocks += 1
    return blocks


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="loam-surface-test-"))
    saved_state = hook.STATE_DIR
    try:
        workspace = root / "probe"
        vault = root / "vault"
        conversations = vault / "15_CONVERSATIONS"
        workspace.mkdir()
        conversations.mkdir(parents=True)
        (workspace / "agent.json").write_text(
            json.dumps({"name": "probe", "emoji": "🧪", "vault": str(vault)}),
            encoding="utf-8")
        hook.STATE_DIR = root / "transcripts"
        hook.LOG_PATH = root / "capture.log"

        # Each independent session needs a distinct first timestamp.
        def conversation(minute: int) -> list[tuple[str, str, str]]:
            return [("user", "surface check", f"2026-09-22T19:0{minute}:00Z"),
                    ("probe", "checked.", f"2026-09-22T19:0{minute}:04Z")]

        turns = conversation(0)

        fire(session_file(root / "a.jsonl", conversation(1), "sess-a",
                          "Unbrokered"),
             "sess-a", workspace, {})
        fm = frontmatter(conversations / "Unbrokered.md")
        check("no LOAM_SURFACE: the transcript says claude-code",
              fm.get("surface"), "claude-code")
        check("...and states no origin, because there is none to state",
              "origin" in fm, False)

        fire(session_file(root / "b.jsonl", conversation(2), "sess-b", "From the UI"),
             "sess-b", workspace, {lt.SURFACE_ENV_VAR: "loam-ui"})
        fm = frontmatter(conversations / "From the UI.md")
        check("LOAM_SURFACE=loam-ui: the transcript says loam-ui",
              fm.get("surface"), "loam-ui")

        # The thread writer owns sessions that have its marker.
        hook.THREAD_MARKERS = root / "markers"
        hook.THREAD_MARKERS.mkdir()
        (hook.THREAD_MARKERS / "sess-t").write_text("t-1\n")
        try:
            fire(session_file(root / "t.jsonl", conversation(4), "sess-t",
                              "Thread · Beds"),
                 "sess-t", workspace, {lt.SURFACE_ENV_VAR: "loam-ui"})
        except SystemExit:
            pass
        check("a thread session with the writer's marker is not filed again",
              sorted(p.name for p in conversations.glob("Thread*")), [])
        check("...and says so in the hook's log",
              "a thread's session, kept by the thread" in hook.LOG_PATH.read_text(),
              True)
        check("...and still says which session it was",
              fm.get("session_id"), "sess-b")

        fire(session_file(root / "c.jsonl", conversation(3), "sess-c",
                          "Forked from app"),
             "sess-c", workspace,
             {lt.SURFACE_ENV_VAR: "loam-ui",
              lt.ORIGIN_ENV_VAR: "claude-code-app"})
        fm = frontmatter(conversations / "Forked from app.md")
        check("an adopted-and-forked session: held here, came from the app",
              (fm.get("surface"), fm.get("origin")),
              ("loam-ui", "claude-code-app"))
        check("...with origin after surface, where a reader of the folder "
              "finds it",
              list(fm).index("origin") - list(fm).index("surface"), 1)

        fire(session_file(root / "d.jsonl", conversation(4), "sess-d",
                          "Junk surface"),
             "sess-d", workspace, {lt.SURFACE_ENV_VAR: "/etc/passwd\nrm -rf"})
        check("a surface that is not one token falls back rather than "
              "mangling the field",
              frontmatter(conversations / "Junk surface.md").get("surface"),
              "claude-code")

        # Forks copy the parent’s timestamps and append new turns under a new session ID.
        parent = session_file(root / "p.jsonl", turns, "sess-p", "A long chat")
        fire(parent, "sess-p", workspace, {lt.SURFACE_ENV_VAR: "loam-ui"})
        filed = conversations / "A long chat.md"
        before = len(filed.read_text(encoding="utf-8").split("\n"))

        forked = session_file(
            root / "f.jsonl",
            turns + [("user", "and after the fork?", "2026-09-22T19:10:00Z"),
                     ("probe", "still here.", "2026-09-22T19:10:06Z")],
            "sess-f", "A long chat")
        fire(forked, "sess-f", workspace, {lt.SURFACE_ENV_VAR: "loam-ui"})

        check("the fork writes no second file",
              sorted(p.name for p in conversations.glob("A long chat*.md")),
              ["A long chat.md"])
        check("...and no second header", headers(filed), 1)
        check("...it appends its turns under the one that is there",
              len(lt.parse_file_turns(filed)), 4)
        check("...and the file grew rather than being rewritten",
              len(filed.read_text(encoding="utf-8").split("\n")) > before, True)
        check("...with last_activity bumped to the fork's last turn",
              frontmatter(filed).get("last_activity"), "2026-09-22T19:10:06Z")
        check("...and created_at left at the conversation's own beginning",
              frontmatter(filed).get("created_at"), "2026-09-22T19:00:00Z")
        check("...and the sidecar of the fork pointing at that same file",
              json.loads((hook.STATE_DIR / "sess-f.json").read_text())["path"],
              str(filed))
    finally:
        hook.STATE_DIR = saved_state
        shutil.rmtree(root, ignore_errors=True)

    print()
    print("FAILURES:", results.count(False))
    sys.exit(1 if False in results else 0)


if __name__ == "__main__":
    main()
