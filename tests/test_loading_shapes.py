#!/usr/bin/env python3
"""Verify the instruction set each run kind receives with and without a map on every provider."""

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
TMP = Path(tempfile.mkdtemp(prefix="loam-loading-shapes-"))
LABS = TMP / "ai-labs"
# The workspaces the shared owner's page is made under: this test's, never
# the install's.
os.environ["LOAM_AI_LABS"] = str(LABS)
sys.path.insert(0, str(AGENTS / "lib"))

import loam_antigravity as agy  # noqa: E402
import loam_codex as codex  # noqa: E402
import loam_codex_wake as cw  # noqa: E402
import loam_loading  # noqa: E402
import loam_memory  # noqa: E402

FAILURES = 0


def check(label: str, ok, detail="") -> None:
    global FAILURES  # noqa: PLW0603
    FAILURES += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok and detail != "":
        print(f"      {str(detail)[:900]}")


def load(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


prov = load("provision_shapes", AGENTS / "bin" / "provision")
wake = load("wake_shapes", AGENTS / "bin" / "wake")
import loam_identity  # noqa: E402  (after provision: same lib)

TOKENS = {
    "soul.md": "SOUL-QUARTZ",
    "config.md": "CONFIG-BERYL",
    "_always.md": "ALWAYS-OPAL",
    "_active.md": "ACTIVE-JADE",
    "_reflection.md": "REFL-RUBY",
    "crons/probe.md": "CRON-TOPAZ",
    "map.md": "MAP-AGATE",
}
OWNER_TOKEN = "OWNER-ONYX"
LOAM_HEADING = "# Loam's part of your config"


def build(name: str, mapped: bool) -> Path:
    ws = LABS / name
    (ws / "crons").mkdir(parents=True)
    (ws / "agent.json").write_text(json.dumps(
        {"name": name, "display": name.capitalize(), "workspace": str(ws),
         "vault": str(TMP / "vault"), "wake_schedule": "03:00"}))
    (ws / "soul.md").write_text(
        f"# Soul\n\nI am {name}, made for one test and nothing else at all. "
        f"My token is {TOKENS['soul.md']}.\n")
    (ws / "config.md").write_text(f"# Config\n\nToken {TOKENS['config.md']}.\n")
    (ws / "_always.md").write_text(f"# Always\n\nToken {TOKENS['_always.md']}.\n")
    (ws / "_active.md").write_text(
        "---\nname: conversation\ndescription: a lane.\n"
        f"keep-coding-instructions: true\n---\nToken {TOKENS['_active.md']}.\n")
    (ws / "_reflection.md").write_text(f"Token {TOKENS['_reflection.md']}.\n")
    (ws / "crons" / "probe.md").write_text(
        "---\nschedule: daily at 05:00 PT\nenabled: true\n---\n"
        f"Token {TOKENS['crons/probe.md']}.\n")
    if mapped:
        (ws / "map.md").write_text(f"# Map\n\nToken {TOKENS['map.md']}.\n")
    render(ws)
    return ws


def render(ws: Path) -> None:
    (ws / ".claude").mkdir(exist_ok=True)
    (ws / "CLAUDE.md").write_text(prov.claude_md(ws.name.capitalize(), ws))
    (ws / ".claude" / "settings.json").write_text(
        json.dumps(prov.settings(ws), indent=2) + "\n")
    (ws / ".claude" / "output-styles").mkdir()
    (ws / ".claude" / "output-styles" / "none.md").write_text(prov.NONE_STYLE)


def imports_text(ws: Path) -> str:
    """CLAUDE.md and its imports, resolved as Claude Code resolves them (the
    function Codex's instructions are made with)."""
    return "\n".join(p.read_text(encoding="utf-8")
                     for p in codex._imports(ws / "CLAUDE.md", 0, set()))  # noqa: SLF001


def style_text(ws: Path, style: str) -> str:
    path = ws / ".claude" / "output-styles" / f"{style}.md"
    return loam_memory.strip_frontmatter(path.read_text(encoding="utf-8")) if path.exists() else ""


def claude_conversation(ws: Path) -> str:
    settings = json.loads((ws / ".claude" / "settings.json").read_text())
    return imports_text(ws) + "\n" + style_text(ws, settings["outputStyle"])


def claude_lane(ws: Path, cron: str | None) -> tuple[str, list[str]]:
    text, _path = wake.lane_input(ws, cron)
    lane_file = None
    if text is not None:
        lane_file = TMP / f"{ws.name}-lane.md"
        lane_file.write_text(text)
    argv = wake.claude_argv(ws.name, "x", "prompt", lane_file, model="haiku")
    style = json.loads(argv[argv.index("--settings") + 1])["outputStyle"]
    out = imports_text(ws) + "\n" + style_text(ws, style)
    if "--append-system-prompt-file" in argv:
        out += "\n" + Path(argv[argv.index("--append-system-prompt-file") + 1]).read_text()
    return out, argv


def codex_lane(ws: Path, cron: str | None) -> str:
    text, path = wake.lane_input(ws, cron)
    return cw.instructions(ws, "lane", path if text is not None else None, text)


def tokens_in(text: str) -> set[str]:
    return {name for name, tok in TOKENS.items() if tok in text}


def main() -> int:
    loam_loading.ensure_owner_page()
    page = loam_loading.owner_page()
    check("the shared owner's page is made under this test's workspaces",
          page == LABS / "_shared" / "owner.md" and page.is_file(), page)
    page.write_text(page.read_text() + f"\n- 2026-11-18: token {OWNER_TOKEN}.\n")

    without = build("withoutmap", mapped=False)
    with_map = build("withmap", mapped=True)
    runs = {
        "Claude Code conversation": (claude_conversation, None),
        "Claude Code wake": (lambda ws: claude_lane(ws, None)[0], None),
        "Claude Code cron": (lambda ws: claude_lane(ws, "probe")[0], "probe"),
        "Codex conversation": (codex.instructions, None),
        "Codex wake": (lambda ws: codex_lane(ws, None), None),
        "Codex cron": (lambda ws: codex_lane(ws, "probe"), "probe"),
        "Antigravity conversation": (agy.instructions, None),
    }
    for label, (assemble, cron) in runs.items():
        for ws in (with_map, without):
            want = {"soul.md", "config.md"}
            if ws == with_map:
                want.add("map.md")
            if cron:
                want.add("crons/probe.md")
            text = assemble(ws)
            check(f"{label}, {ws.name}: exactly {sorted(want)}", tokens_in(text) == want)
            check(f"{label}, {ws.name}: shared instructions load",
                  LOAM_HEADING in text and OWNER_TOKEN in text)

    for ws in (with_map, without):
        _, argv = claude_lane(ws, None)
        check(f"{ws.name}: reflection appends no file",
              "--append-system-prompt-file" not in argv)
        check(f"{ws.name}: identity preflight resolves the shared imports",
              ("map.md" in {p.name for p in loam_identity.assert_imports_resolve(ws)})
              == (ws == with_map))

    before = {name: (without / name).read_bytes()
              for name in ("_always.md", "_active.md", "_reflection.md")}
    map_file = without / "map.md"
    existing = (with_map / "map.md").read_bytes()
    runner = [sys.executable, str(AGENTS / "lib" / "loam_map_migration.py")]
    done = subprocess.run(runner, check=True, text=True, capture_output=True)
    seeded = map_file.read_text()
    check("migration creates only the missing map",
          "Created 1 (withoutmap)" in done.stdout
          and existing == (with_map / "map.md").read_bytes(), done.stdout)
    check("migration points to the old files by run kind",
          all(line in seeded for line in (
              "Read `_always.md` at the start of every run.",
              "Read `_active.md` at the start of a conversation.",
              "Read `_reflection.md` at the start of a reflection wake.")))
    check("migration leaves the old files byte-identical",
          all((without / name).read_bytes() == body for name, body in before.items()))
    done = subprocess.run(runner, check=True, text=True, capture_output=True)
    check("migration is idempotent", "Created 0." in done.stdout and map_file.read_text() == seeded)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
