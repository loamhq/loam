#!/usr/bin/env python3
"""lib/loam_skills.py and bin/library: the shared skills library, the shelves
every agent sees, and the starter vault (first release L7).

Everything runs in a throwaway install: LOAM_CONFIG names a scratch config
before anything imports loam_config, so the vault, the workspaces and the
library copy are all under a temporary folder and nothing on the box moves.

Checks: the starter vault's shape from a blank config, and from numbered
folder names; that seeding twice, or over a vault that exists, changes
nothing of the person's; the library is installed whole and an edit made in
it is named by the diff; which skill wins a name (personal over the shared
shelf over Loam's; by folder name, by frontmatter name, by a single-file
skill); the view Codex and Antigravity read (a link per personal and
unshadowed shared skill, a stale link removed, a real folder left alone,
the old `.agents/skills -> ../skills` link turned into a folder); the Claude
shelf; `behind` naming a view that is out of date; `quiet_refresh` never
raising; and the pointer line in every persona's rendered CLAUDE.md.

Run: python3 tests/test_skills.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="test-skills-"))
CONFIG = TMP / "loam.toml"
CONFIG.write_text(f'''
[paths]
home = "{TMP}"
vault = "{TMP}/vault"
workspaces = "{TMP}/agents"
state = "{TMP}/state"
''')
os.environ["LOAM_CONFIG"] = str(CONFIG)
sys.path.insert(0, str(ROOT / "lib"))
import loam_config as lc  # noqa: E402
import loam_skills as sk  # noqa: E402

FAILURES = 0


def check(label: str, got, want) -> None:
    global FAILURES  # noqa: PLW0603
    ok = got == want
    FAILURES += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f"\n      got  {got!r}\n      want {want!r}"))


def skill(folder: Path, name: str | None = None, text: str = "body") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name or folder.name}\ndescription: {folder}\n---\n{text}\n")
    return folder


def agent(name: str) -> Path:
    ws = lc.WORKSPACES / name
    (ws / "skills").mkdir(parents=True, exist_ok=True)
    (ws / "agent.json").write_text(f'{{"name": "{name}"}}')
    return ws


def tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) + ("/" if p.is_dir() else "")
                  for p in root.rglob("*"))


def main() -> int:
    check("the scratch config is the one read", lc.VAULT, TMP / "vault")

    # -- the starter vault, from a blank config --------------------------------
    out = subprocess.run([sys.executable, str(ROOT / "bin" / "library"), "seed-vault"],
                         capture_output=True, text=True, env=dict(os.environ))
    check("bin/library seed-vault exits 0", (out.returncode, out.stderr), (0, ""))
    shape = [p for p in tree(lc.VAULT) if "/skills/loam-" not in p and "skills/loam-" not in p]
    check("a blank install's vault: the plain folders, the README, both shelves",
          shape, ["About me.md", "Attachments/", "Conversations/", "Inbox/", "Inbox/boards/", "Projects/",
                  "README.md", "System/", "System/Loam/", "System/Loam/README.md",
                  "System/Loam/skills/", "System/skills/", "System/skills/README.md"])
    check("...and Loam's shelf is the library, file for file",
          sk.library_diff(), {"added": [], "removed": [], "changed": []})
    readme = (lc.VAULT / "README.md").read_text()
    check("the README names each folder by its configured name",
          all(f"`{n}/`" in readme for n in ("Inbox", "Conversations", "Projects",
                                             "Attachments", "System", "System/Loam",
                                             "System/skills")), True)

    (lc.VAULT / "README.md").write_text("mine\n")
    (lc.VAULT / "System" / "skills" / "README.md").write_text("mine too\n")
    made = sk.seed_vault(lc.VAULT)
    check("seeding again makes nothing", made, [])
    check("...and leaves the person's README and shelf as they wrote them",
          ((lc.VAULT / "README.md").read_text(),
           (lc.VAULT / "System" / "skills" / "README.md").read_text()),
          ("mine\n", "mine too\n"))

    numbered = TMP / "numbered"
    folders = {"inbox": "00_INBOX", "boards": "00_INBOX/boards",
               "general_board": "00_INBOX/from-agents/comms.md",
               "conversations": "15_CONVERSATIONS", "projects": "40_PROJECTS",
               "attachments": "98_ATTACHMENTS", "system": "99_SYSTEM"}
    (numbered / "99_SYSTEM" / "conventions").mkdir(parents=True)
    (numbered / "README.md").write_text("an old vault\n")
    made = sk.seed_vault(numbered, folders)
    check("numbered folder names are used as configured, nothing else made",
          made, ["00_INBOX/", "00_INBOX/boards/", "15_CONVERSATIONS/", "40_PROJECTS/",
                 "98_ATTACHMENTS/", "99_SYSTEM/skills/", "99_SYSTEM/Loam/ (installed)"])
    check("...and an existing vault gets no About me.md",
          (numbered / "About me.md").exists(), False)
    check("...an existing vault's own folders and README are untouched",
          ((numbered / "99_SYSTEM" / "conventions").is_dir(),
           (numbered / "README.md").read_text()), (True, "an old vault\n"))

    # -- the library, installed whole ---------------------------------------------
    loam = sk.loam_dir()
    (loam / "skills" / "loam-skills" / "SKILL.md").write_text("edited in place\n")
    (loam / "stray.md").write_text("someone's note\n")
    diff = sk.install_library()
    check("an install names an edit made in System/Loam and a file added there",
          (diff["changed"], diff["removed"]), (["skills/loam-skills/SKILL.md"], ["stray.md"]))
    check("...and replaces the folder whole", sk.library_diff(),
          {"added": [], "removed": [], "changed": []})
    check("...leaving no temporary folder beside it",
          sorted(p.name for p in loam.parent.iterdir()), ["Loam", "skills"])

    # -- who wins a name ----------------------------------------------------------------
    own = lc.VAULT / "System" / "skills"
    skill(own / "essay")
    skill(own / "loam-skills", text="the person's version")
    ada = agent("ada")
    skill(ada / "skills" / "essay")                       # shadows by folder name
    skill(ada / "skills" / "my-plans", name="loam-how-it-works")    # by frontmatter name
    (ada / "skills" / "loam-building-a-plugin.md").write_text("a single-file skill\n")
    seen = sk.seen_by(ada)
    check("the person's shelf wins a name over Loam's",
          sk.shared_skills()["loam-skills"][0], "shared")
    check("a personal skill wins by folder name", seen["essay"][0], "personal")
    check("...by its SKILL.md's name, and by a single-file skill's stem",
          ("loam-how-it-works" in seen, "loam-building-a-plugin" in seen), (False, False))
    check("...and everything else shared is seen",
          sorted(n for n, (shelf, _f) in seen.items() if shelf != "personal"),
          sorted({d.name for d in (sk.LIBRARY / "skills").iterdir() if d.is_dir()}
                 - {"loam-how-it-works", "loam-building-a-plugin"}))

    # -- the view (.agents/skills) -----------------------------------------------------
    view = sk.view_dir(ada)
    view.parent.mkdir(parents=True, exist_ok=True)
    view.symlink_to("../skills")                          # the layout before L7
    sk.render_view(ada)
    check("the old link to ../skills becomes a folder, and skills/ is untouched",
          (view.is_symlink(), view.is_dir(), (ada / "skills" / "essay" / "SKILL.md").is_file()),
          (False, True, True))
    check("a personal skill is a relative link into skills/",
          os.readlink(view / "essay"), "../../skills/essay")
    check("a shared one is a link to its shelf's folder",
          os.readlink(view / "loam-skills"), str(own / "loam-skills"))
    check("a shadowed one is not in the view", (view / "loam-how-it-works").exists(), False)
    (view / "gone").symlink_to(str(own / "gone"))
    skill(view / "real-one")                              # somebody made a real folder here
    check("behind() names a stale link", sk.behind(ada)[-1:],
          ["ada .agents/skills: gone should not be there"])
    sk.render_view(ada)
    check("a stale link is removed; a real folder is left where it is",
          ((view / "gone").is_symlink(), (view / "real-one" / "SKILL.md").is_file()),
          (False, True))
    sk.render_claude_shelf()
    check("with both rendered, nothing is behind", sk.behind(), [])
    skill(own / "new-for-all")
    check("a skill added to a shelf makes every view and the shelf behind",
          sorted(sk.behind()), ["ada .agents/skills: new-for-all missing or stale",
                                "claude shelf: new-for-all missing or stale"])
    sk.refresh()
    check("refresh brings them current", sk.behind(), [])

    # -- the Claude shelf -------------------------------------------------------------------
    shelf = sk.claude_shelf_dir()
    check("the Claude shelf is above every workspace", shelf, lc.WORKSPACES / ".claude" / "skills")
    check("...and holds every shared skill, shadowed or not (Claude Code keeps the closer)",
          sorted(p.name for p in shelf.iterdir()),
          sorted(sk.shared_skills()))

    # -- a start never fails over skills -------------------------------------------------------
    broken = agent("broken")
    (broken / ".agents").write_text("a file where a folder should be")
    try:
        sk.quiet_refresh(broken)
        raised = False
    except Exception:  # noqa: BLE001
        raised = True
    check("quiet_refresh never raises", raised, False)

    # -- the pointer line, in the rendered CLAUDE.md ------------------------------------------
    out = subprocess.run(
        [sys.executable, "-c",
         "import importlib.machinery, importlib.util, sys;"
         f"l = importlib.machinery.SourceFileLoader('p', {str(ROOT / 'bin' / 'provision')!r});"
         "s = importlib.util.spec_from_loader('p', l); m = importlib.util.module_from_spec(s);"
         "l.exec_module(m); print(m.skills_line())"],
        capture_output=True, text=True, env=dict(os.environ))
    line = out.stdout.strip()
    check("provision's pointer line names both shelves and the rule",
          (str(lc.VAULT / "System" / "skills") in line,
           str(lc.VAULT / "System" / "Loam" / "skills") in line,
           "wins over a shared one of the same name" in line, line.count("\n")),
          (True, True, True, 0))

    print(f"\n{'OK' if not FAILURES else f'{FAILURES} FAILED'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        import shutil
        shutil.rmtree(TMP, ignore_errors=True)
