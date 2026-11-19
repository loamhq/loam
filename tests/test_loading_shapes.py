#!/usr/bin/env python3
"""What an agent loads, per run kind and provider, with a map and without
(2026-11-18, lib/loam_loading.py).

On two throwaway agents in a temp directory, never a real one's files: each
has a soul, a config, `_always.md`, `_active.md` (with the loader's
frontmatter), `_reflection.md` and one cron, every file carrying a token of
its own; one of them also has a `map.md`. Their CLAUDE.md, settings and
output styles are rendered by bin/provision's own functions, and each run is
assembled by the code that assembles it:

  Claude Code  conversation: CLAUDE.md's imports, then the output style the
               settings select (`_active.md` through the symlink, or the
               empty `none`). Wake and cron: bin/wake's own `lane_input` and
               `claude_argv` (`--settings outputStyle none`, and the file it
               appends, if any).
  Codex        conversation: `loam_codex.instructions`; wake and cron:
               `loam_codex_wake.instructions` with bin/wake's `lane_input`.
  Antigravity  conversation: `loam_antigravity.instructions` (its wakes run
               on Claude Code; lib/loam_profile.py says why).

For each, the tokens present are exactly those of
`loam_loading.own_files(kind)`: with a map, soul, config and map (and a
cron's own file in a cron), with Loam's text and the owner's shared page
beside them; without one, exactly what loaded before.

Then the switch: writing a map re-renders CLAUDE.md and the settings
(`loam_loading.refresh`, and the persona's PostToolUse hook after a write
made with the shell), removing it puts the old shape back, and the old
files are never touched. And the readers: the identity gate's import check,
lane-check, the size warning, the profile's file list.

Run: python3 tests/test_loading_shapes.py
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
    prov.output_styles(ws)


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
    _, text, _path = wake.lane_input(ws, cron)
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
    _, text, path = wake.lane_input(ws, cron)
    return cw.instructions(ws, "lane", path if text is not None else None, text)


def tokens_in(text: str) -> set[str]:
    return {name for name, tok in TOKENS.items() if tok in text}


def expected(ws: Path, kind: str, cron: str | None = None) -> set[str]:
    return {str(p.relative_to(ws)) for p in loam_loading.own_files(ws, kind, cron)}


def main() -> int:
    loam_loading.ensure_owner_page()
    page = loam_loading.owner_page()
    check("the shared owner's page is made under this test's workspaces",
          page == LABS / "_shared" / "owner.md" and page.is_file(), page)
    page.write_text(page.read_text() + f"\n- 2026-11-18: token {OWNER_TOKEN}.\n")

    old = build("oldshape", mapped=False)
    new = build("newshape", mapped=True)

    # -- what loads, per run kind and provider --------------------------------
    runs = {
        "Claude Code conversation": (lambda ws: claude_conversation(ws), "conversation", None),
        "Claude Code wake": (lambda ws: claude_lane(ws, None)[0], "wake", None),
        "Claude Code cron": (lambda ws: claude_lane(ws, "probe")[0], "cron", "probe"),
        "Codex conversation": (lambda ws: codex.instructions(ws), "conversation", None),
        "Codex wake": (lambda ws: codex_lane(ws, None), "wake", None),
        "Codex cron": (lambda ws: codex_lane(ws, "probe"), "cron", "probe"),
        "Antigravity conversation": (lambda ws: agy.instructions(ws), "conversation", None),
    }
    for label, (assemble, kind, cron) in runs.items():
        for ws, shape in ((new, "with a map"), (old, "without a map")):
            text = assemble(ws)
            got, want = tokens_in(text), expected(ws, kind, cron)
            check(f"{label}, {shape}: exactly {sorted(want)}", got == want,
                  f"got {sorted(got)}")
            mapped = ws is new
            check(f"{label}, {shape}: Loam's text and the owner's page "
                  f"{'load' if mapped else 'do not load'}",
                  ((LOAM_HEADING in text) == mapped) and ((OWNER_TOKEN in text) == mapped),
                  (LOAM_HEADING in text, OWNER_TOKEN in text))

    check("the shapes, stated: with a map, soul, config and map on every kind",
          all(expected(new, k) == {"soul.md", "config.md", "map.md"}
              for k in ("conversation", "wake")), "")
    check("...and a cron adds only its own file",
          expected(new, "cron", "probe") == {"soul.md", "config.md", "map.md", "crons/probe.md"})

    _, argv_new = claude_lane(new, None)
    _, argv_old = claude_lane(old, None)
    check("a mapped agent's reflection appends no file; an unmapped one's does",
          ("--append-system-prompt-file" not in argv_new,
           "--append-system-prompt-file" in argv_old), (True, True))
    check("a mapped agent's wake is sized by its map",
          wake.lane_input(new, None)[0] == "map.md" and wake.lane_input(new, None)[2] == new / "map.md")
    check("the mapped wake's prompt is Loam's default: the changes, and judgment",
          "{diff_block}" in wake.MAPPED_PROMPT_TEMPLATE
          and "judgment" in wake.MAPPED_PROMPT_TEMPLATE
          and "your map says where" in wake.MAPPED_PROMPT_TEMPLATE)
    check("the mapped cron's prompt does not claim `_always.md` loaded",
          "_always.md" not in wake.MAPPED_CRON_PROMPT_TEMPLATE
          and "_always.md" in wake.CRON_PROMPT_TEMPLATE)

    # -- rendered files ---------------------------------------------------------
    s_new = json.loads((new / ".claude" / "settings.json").read_text())
    s_old = json.loads((old / ".claude" / "settings.json").read_text())
    check("settings: a mapped agent's conversation selects the empty style",
          (s_new["outputStyle"], s_old["outputStyle"]) == ("none", "conversation"),
          (s_new["outputStyle"], s_old["outputStyle"]))
    claude_new = (new / "CLAUDE.md").read_text()
    claude_old = (old / "CLAUDE.md").read_text()
    check("CLAUDE.md with a map: soul, Loam's text, owner's page, config, map, in that order",
          [l for l in claude_new.splitlines() if l.startswith("@")][:5]
          == ["@soul.md", f"@{loam_loading.LOAM_TEXT}", f"@{page}", "@config.md", "@map.md"],
          [l for l in claude_new.splitlines() if l.startswith("@")])
    check("...and tells the agent its old files no longer load",
          "no longer loads" in claude_new and "output-styles" not in claude_new)
    check("CLAUDE.md without a map is the old shape, word for word",
          "@_always.md" in claude_old and "@map.md" not in claude_old
          and "output-styles/conversation.md" in claude_old)
    check("Loam's text is under 2,000 characters",
          len(loam_loading.LOAM_TEXT.read_text()) < 2000,
          len(loam_loading.LOAM_TEXT.read_text()))
    check("the identity gate accepts the mapped CLAUDE.md's imports",
          len(loam_identity.assert_imports_resolve(new)) >= 5)
    canary = loam_identity.pick_canary(new)
    check("...and its canary is a soul sentence found in none of the others",
          canary in (new / "soul.md").read_text()
          and canary not in loam_loading.LOAM_TEXT.read_text(), canary)

    # -- the switch ------------------------------------------------------------
    sw = build("switcher", mapped=False)
    before = {n: (sw / n).read_bytes() for n in ("_always.md", "_active.md", "_reflection.md")}
    check("no map: refresh changes nothing", loam_loading.refresh(sw) is False)
    (sw / "map.md").write_text(f"# Map\n\nToken {TOKENS['map.md']}.\n")
    check("a map written: refresh re-renders", loam_loading.refresh(sw) is True)
    check("...CLAUDE.md imports the map, settings select `none`",
          loam_loading.rendered_mapped(sw) is True
          and json.loads((sw / ".claude" / "settings.json").read_text())["outputStyle"] == "none")
    check("...and its conversation now loads soul, config and map only",
          tokens_in(claude_conversation(sw)) == {"soul.md", "config.md", "map.md"},
          sorted(tokens_in(claude_conversation(sw))))
    check("...the old files are untouched, byte for byte",
          all((sw / n).read_bytes() == b for n, b in before.items()))
    check("a second refresh changes nothing", loam_loading.refresh(sw) is False)
    (sw / "map.md").unlink()
    check("the map removed: the old shape comes back",
          loam_loading.refresh(sw) is True and loam_loading.rendered_mapped(sw) is False
          and tokens_in(claude_conversation(sw)) == {"soul.md", "config.md", "_always.md", "_active.md"})

    # The hook, after a write it cannot name (a map made with the shell).
    (sw / "map.md").write_text("# Map\n")
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(sw)}
    proc = subprocess.run(
        [sys.executable, str(AGENTS / "hooks" / "loading_size.py")],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "echo > map.md"},
                          "cwd": str(sw)}),
        text=True, capture_output=True, env=env)
    check("the PostToolUse hook re-renders after a Bash write, exit 0",
          proc.returncode == 0 and loam_loading.rendered_mapped(sw) is True,
          proc.stderr[-400:])

    # -- the readers -----------------------------------------------------------
    check("the size warning follows the shape: the map loads, `_always.md` does not",
          loam_memory.is_auto_loaded(new / "map.md", new)
          and not loam_memory.is_auto_loaded(new / "_always.md", new)
          and loam_memory.is_auto_loaded(old / "_always.md", old)
          and not loam_memory.is_auto_loaded(old / "map.md", old))
    rc = load("lane_check_shapes", AGENTS / "bin" / "lane-check")
    (new / "_active.md").write_text("no frontmatter at all\n")
    problems, _ = rc.findings(new)
    check("lane-check with a map: no style finding for an `_active.md` that no longer loads",
          problems == [], problems)
    (new / "map.md").write_text("x" * (loam_memory.LIMIT_CHARS + 5))
    problems, _ = rc.findings(new)
    check("...and the map over the guideline is reported, not refused",
          len(problems) == 1 and "map.md" in problems[0] and "Reported, not refused" in problems[0],
          problems)
    import loam_profile  # noqa: PLC0415
    loam_profile._ai_labs = lambda: LABS  # noqa: SLF001
    check("the profile lists the map for a mapped agent, the three for the other",
          (set(loam_profile.files("newshape")["memory"]),
           set(loam_profile.files("oldshape")["memory"]))
          == ({"map", "folder"}, {"always", "active", "reflection", "folder"}),
          (loam_profile.files("newshape")["memory"].keys(),))

    print(f"\n{'OK' if not FAILURES else f'{FAILURES} FAILED'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
