#!/usr/bin/env python3
"""mcp/files_server.py: the reviewer's listing and search on Antigravity
(2026-11-15). Read-only, held to its roots, the excluded path unreachable
by name, by `..` and by symlink.

Run: mcp/.venv/bin/python tests/test_files_server.py
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="loam-files-server-")).resolve()
LABS, VAULT, OUT = TMP / "ai-labs", TMP / "vault", TMP / "outside"
CONV = VAULT / "15_CONVERSATIONS"
for d in (LABS / "ada" / "skills" / "s", CONV, VAULT / "10", OUT, LABS / ".git"):
    d.mkdir(parents=True)
(LABS / "ada" / "config.md").write_text("Conventions: dates are ISO.\nnothing\n")
(LABS / "ada" / "skills" / "s" / "SKILL.md").write_text("# s\nStale pointer here\n")
(LABS / ".git" / "HEAD").write_text("ref: stale\n")
(CONV / "t.md").write_text("a stale transcript\n")
(VAULT / "10" / "n.md").write_text("stale vault note\n")
(OUT / "x.md").write_text("stale outside\n")
(LABS / "ada" / "link-out").symlink_to(OUT)
(LABS / "ada" / "link-conv").symlink_to(CONV)
os.environ["LOAM_FILES_ROOTS"] = f"{LABS}:{VAULT}"
os.environ["LOAM_FILES_EXCLUDE"] = str(CONV)

spec = importlib.util.spec_from_file_location("files_server", AGENTS / "mcp" / "files_server.py")
fs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fs)

fails = 0


def check(label: str, cond: bool, detail=None) -> None:
    global fails
    fails += not cond
    print(("PASS  " if cond else "FAIL  ") + label + ("" if cond else f"   {detail!r}"))


try:
    out = fs.list_(str(LABS / "ada"))
    check("list: a folder's entries, folders marked, sizes given",
          out["ok"] and "skills/" in out["text"] and "config.md\t" in out["text"], out)
    check("list: a symlink out of the reach is not listed",
          "link-out" not in out["text"] and "link-conv" not in out["text"], out)
    out = fs.list_(str(LABS), pattern="**/*.md")
    check("list with a glob: every matching file, recursively",
          out["ok"] and "ada/config.md" in out["text"]
          and "ada/skills/s/SKILL.md" in out["text"], out)
    check("...not .git, and nothing through a symlink out",
          "HEAD" not in out["text"] and "x.md" not in out["text"]
          and "t.md" not in out["text"], out)
    out = fs.list_(str(VAULT), pattern="**/*.md")
    check("the vault's 15_CONVERSATIONS/ is not walked",
          out["ok"] and "10/n.md" in out["text"] and "t.md" not in out["text"], out)
    for path, label in ((str(CONV), "the excluded folder by name"),
                        (str(LABS / ".." / "outside"), "a path out by `..`"),
                        (str(LABS / "ada" / "link-out"), "a symlink out"),
                        (str(LABS / "ada" / "link-conv"), "a symlink into the excluded folder"),
                        ("/etc", "a path outside the roots")):
        check(f"refused: {label}", not fs.list_(path)["ok"]
              and not fs.grep("stale", path)["ok"])
    out = fs.grep("stale", str(TMP))
    check("grep at a parent of the roots is refused", not out["ok"], out)
    out = fs.grep("(?i)STALE", str(LABS))
    check("grep: file:line: text for every match, .git and symlinks left out",
          out["ok"] and f"{LABS}/ada/skills/s/SKILL.md:2: Stale pointer here" in out["text"]
          and "HEAD" not in out["text"] and "outside" not in out["text"]
          and "transcript" not in out["text"], out)
    out = fs.grep("stale", str(VAULT))
    check("grep in the vault: its notes, never 15_CONVERSATIONS/",
          out["ok"] and "vault note" in out["text"] and "transcript" not in out["text"], out)
    out = fs.grep("iso", str(LABS), glob="*.md", ignore_case=True)
    check("grep with a glob and ignore_case", out["ok"] and "config.md:1:" in out["text"], out)
    check("grep: a bad pattern is an error, not a crash", not fs.grep("(", str(LABS))["ok"])
    before = sorted(str(p) for p in TMP.rglob("*"))
    fs.list_(str(LABS), pattern="**/*")
    fs.grep(".", str(LABS))
    check("nothing is written", sorted(str(p) for p in TMP.rglob("*")) == before)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{'OK' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
