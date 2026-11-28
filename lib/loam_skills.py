"""The shared skills library and the starter vault.

Three shelves of skills, all in the Agent Skills format (a folder holding a
``SKILL.md`` whose frontmatter has ``name`` and ``description``):

- **personal**: an agent's own ``skills/`` in its workspace. Its own to make,
  change and retire; the reviewer reads it.
- **the person's shelf**: ``<vault>/<system>/skills/``. The person's own
  skills, ones an agent writes for every agent, and marketplace installs.
  Loam's updates never touch it.
- **Loam's shelf**: ``<vault>/<system>/Loam/skills/``, inside
  ``<vault>/<system>/Loam/``, which is a copy of this repository's
  ``docs/library/`` and the one corner of the vault an update owns: it is replaced
  whole by ``install_library``.

**A name is one skill.** For one agent, its personal skill wins over a shared
one of the same name, and the person's shelf wins over Loam's. A copy is not
updated by its original: it is a different folder. The shadowing is computed
here rather than left to each provider, because the providers disagree:
Claude Code keeps the closer of two same-named skills, but Codex 0.169 lists
both, side by side, with nothing to tell them apart (verified with
``skills/list``; engineer/memory).

**What each provider is shown**:

- Claude Code reads ``.claude/skills`` in the working directory and in every
  parent above it, and the closest of two same-named skills wins. The
  persona's ``.claude/skills`` stays a link to ``../skills``; the shared
  shelves are one folder of links at ``<workspaces>/.claude/skills/``, the
  parent of every persona's workspace (``render_claude_shelf``). Claude Code
  does not read skills from ``permissions.additionalDirectories``.
- Codex reads both ``.codex/skills`` and ``.agents/skills`` in the working
  directory, and nothing above it (the broker's ``project_root_markers = []``).
  ``.codex/skills`` stays a link to ``../skills``, so a personal skill made
  during a thread shows at once; ``.agents/skills`` is this module's view.
  Codex lists a skill reached by two links once.
- Antigravity reads ``.agents/skills`` in the workspace, which becomes a
  folder this module renders (``render_view``): a link per personal skill and
  a link per shared skill no personal one shadows. Because ``agy`` 1.5.12
  rejects skill symlinks (and ``.agents/skills.json`` paths) that resolve
  outside ``workspaceDirs`` (``escapes workspace root``),
  ``loam_antigravity.argv`` passes ``--add-dir`` for the shared shelves
  (``loam_antigravity.skill_dirs``) on every persona turn so those links load
  natively with exact shadowing and zero duplicates.

The view is rendered at provisioning, at every session start in the broker
and in a wake, and by ``bin/library refresh``. Between two renders a skill
added to a shelf is not yet listed, and a personal copy made mid-thread is
listed beside the shared original on Codex until the next start.
``check-drift`` names any view that is behind.

Nothing here deletes anything but a symlink it would itself have made: a real
file or folder found inside a view is left where it is, and its name counts
as taken.
"""

from __future__ import annotations

import filecmp
import os
import re
import shutil
from pathlib import Path

import loam_config as lc

SKILL_FILE = "SKILL.md"
# The folder under the vault's system folder that Loam's updates own.
LOAM_DIR = "Loam"
# The person's own shared shelf, beside it.
OWN_DIR = "skills"
# The starter vault seeds an optional personal note for the owner.
ABOUT_ME = "About me.md"
ABOUT_ME_TEXT = """# About me

A note about me, mine to read and edit.
"""
# The temporary visual explainer seeded at the top of <projects>/Loam/ on new installs.
START_HERE = "Start here.html"
START_HERE_SRC = Path(__file__).resolve().parent.parent / "docs" / "starter" / START_HERE
# The one folder inside Loam's that an update keeps (2026-11-16): plugins'
# data that belongs in the vault, `<system>/Loam/data/<plugin id>/` (the
# doc-comments sidecars first). It is never in the library, never counted as
# an edit to it, carried across when the library is replaced, and kept as it
# is now when an update is undone.
KEPT_DIR = "data"
# This repository's library: what becomes <system>/Loam/. Under docs/ and not
# at the top level because a landing's tests see only top-level entries the
# canonical checkout already has (lib/loam_worktree.platform_argv); the
# release cut (L15) lays it out as `docs/library/`.
LIBRARY = Path(__file__).resolve().parent.parent / "docs" / "library"

# A view's link to the agent's own skill, relative, so the view survives the
# workspace being moved or restored elsewhere.
PERSONAL_PREFIX = "../../skills/"

NAME_RE = re.compile(r"^name:\s*['\"]?([^'\"\n]+?)['\"]?\s*$", re.M)


# -- where --------------------------------------------------------------------

def system_dir(vault: Path | None = None) -> Path:
    return (vault or lc.VAULT) / lc.vault_folder("system")


def loam_dir(vault: Path | None = None) -> Path:
    return system_dir(vault) / LOAM_DIR


def shelves(vault: Path | None = None) -> list[tuple[str, Path]]:
    """The shared shelves, the winner of a name first."""
    return [("shared", system_dir(vault) / OWN_DIR),
            ("loam", loam_dir(vault) / "skills")]


# -- what is on a shelf ---------------------------------------------------------

def skill_name(folder: Path) -> str | None:
    """The frontmatter ``name`` of a skill folder's SKILL.md, or None."""
    try:
        head = (folder / SKILL_FILE).read_text(encoding="utf-8")[:4000]
    except OSError:
        return None
    if not head.startswith("---"):
        return None
    end = head.find("\n---", 3)
    match = NAME_RE.search(head[3:end if end > 0 else len(head)])
    return match.group(1).strip() if match else None


def shelf_skills(shelf: Path) -> dict[str, Path]:
    """Every skill on one shelf, by its folder's name."""
    out = {}
    try:
        entries = sorted(shelf.iterdir())
    except OSError:
        return out
    for entry in entries:
        if entry.name.startswith((".", "_")):
            continue
        if entry.is_dir() and (entry / SKILL_FILE).is_file():
            out[entry.name] = entry
    return out


def shared_skills(vault: Path | None = None) -> dict[str, tuple[str, Path]]:
    """Every shared skill by name: ``{name: (shelf, folder)}``, the person's
    shelf winning a name over Loam's (by folder name or frontmatter ``name``)."""
    out: dict[str, tuple[str, Path]] = {}
    taken: set[str] = set()
    for label, shelf in shelves(vault):
        for name, folder in shelf_skills(shelf).items():
            fm = skill_name(folder) or name
            if name in out or name in taken or fm in taken:
                continue
            out[name] = (label, folder)
            taken.add(name)
            taken.add(fm)
    return out


def personal_names(workspace: Path) -> set[str]:
    """The names an agent's own skills take: each folder's name and its
    SKILL.md's ``name``, and a single-file skill's stem (``skills/x.md``), so
    a skill in either shape shadows a shared one."""
    names: set[str] = set()
    try:
        entries = list((workspace / "skills").iterdir())
    except OSError:
        return names
    for entry in entries:
        if entry.name.startswith((".", "_")):
            continue
        if entry.is_dir():
            names.add(entry.name)
            own = skill_name(entry)
            if own:
                names.add(own)
        elif entry.suffix == ".md":
            names.add(entry.stem)
    return names


def seen_by(workspace: Path, vault: Path | None = None) -> dict[str, tuple[str, Path]]:
    """What one agent is shown, by name: ``{name: (shelf, folder)}``, its own
    skills under ``"personal"``."""
    out: dict[str, tuple[str, Path]] = {}
    for name, folder in shelf_skills(workspace / "skills").items():
        out[name] = ("personal", folder)
    taken = personal_names(workspace)
    for name, (label, folder) in shared_skills(vault).items():
        if name not in taken and name not in out and (skill_name(folder) or name) not in taken:
            out[name] = (label, folder)
    return out


# -- rendering ------------------------------------------------------------------

def _wanted_view(workspace: Path, vault: Path | None) -> dict[str, str]:
    """A view's links: name -> link target."""
    out = {}
    for name, (label, folder) in seen_by(workspace, vault).items():
        out[name] = (PERSONAL_PREFIX + folder.name if label == "personal"
                     else str(folder))
    return out


def _ours(link: Path) -> bool:
    """Whether an entry of a rendered folder is this module's: every link is
    (the folder is Loam's); a real file or folder is somebody's own."""
    return link.is_symlink()


def _sync_links(folder: Path, wanted: dict[str, str]) -> list[str]:
    """Make ``folder`` hold exactly ``wanted`` as links, touching no real
    file or folder. Returns what changed, in words."""
    changed = []
    # A link where the folder should be (the `.agents/skills -> ../skills` of
    # before 2026-11-14) is replaced by a folder; the link only is removed.
    if folder.is_symlink():
        folder.unlink()
        changed.append(f"{folder}: was a link, now a folder")
    folder.mkdir(parents=True, exist_ok=True)
    for entry in sorted(folder.iterdir()):
        if entry.name in wanted:
            continue
        if _ours(entry):
            entry.unlink()
            changed.append(f"-{entry.name}")
    for name, target in sorted(wanted.items()):
        link = folder / name
        if link.is_symlink():
            if os.readlink(link) == target:
                continue
        elif link.exists():
            continue   # a real folder of that name: it is the agent's, it stays
        tmp = folder / f".{name}.{os.getpid()}.tmp"
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        tmp.symlink_to(target)
        os.replace(tmp, link)
        changed.append(f"+{name}")
    return changed


def view_dir(workspace: Path) -> Path:
    return workspace / ".agents" / "skills"


def claude_shelf_dir(workspaces: Path | None = None) -> Path:
    return (workspaces or lc.WORKSPACES) / ".claude" / "skills"


def render_view(workspace: Path, vault: Path | None = None) -> list[str]:
    """One agent's ``.agents/skills``: what Codex and Antigravity are shown."""
    if not (workspace / "agent.json").is_file():
        return []
    return _sync_links(view_dir(workspace), _wanted_view(workspace, vault))


def render_claude_shelf(workspaces: Path | None = None,
                        vault: Path | None = None) -> list[str]:
    """``<workspaces>/.claude/skills``: every shared skill, for Claude Code,
    which lets a persona's own skill of the same name win by itself."""
    wanted = {name: str(folder) for name, (_label, folder) in shared_skills(vault).items()}
    return _sync_links(claude_shelf_dir(workspaces), wanted)


def refresh(workspace: Path | None = None, vault: Path | None = None,
            workspaces: Path | None = None) -> list[str]:
    """The Claude shelf, and one agent's view (or every agent's)."""
    root = workspaces or lc.WORKSPACES
    changed = [f"claude shelf {c}" for c in render_claude_shelf(root, vault)]
    targets = [workspace] if workspace else sorted(
        p.parent for p in root.glob("*/agent.json"))
    for ws in targets:
        changed += [f"{ws.name} {c}" for c in render_view(ws, vault)]
    return changed


def quiet_refresh(workspace: Path | None = None) -> None:
    """``refresh`` for a session start: never raises, because no session may
    fail to start over its skills list."""
    try:
        refresh(workspace)
    except Exception:  # noqa: BLE001
        pass


def behind(workspace: Path | None = None, vault: Path | None = None,
           workspaces: Path | None = None) -> list[str]:
    """What ``refresh`` would change, without changing it, for check-drift."""
    root = workspaces or lc.WORKSPACES
    out = []

    def diff(folder: Path, wanted: dict[str, str], label: str):
        if folder.is_symlink() or not folder.is_dir():
            out.append(f"{label}: {folder} is not a rendered folder")
            return
        have = {e.name: os.readlink(e) for e in folder.iterdir()
                if _ours(e)}
        real = {e.name for e in folder.iterdir() if not e.is_symlink()
                and not e.name.startswith(".")}
        for name, target in wanted.items():
            if name not in real and have.get(name) != target:
                out.append(f"{label}: {name} missing or stale")
        for name in have:
            if name not in wanted:
                out.append(f"{label}: {name} should not be there")

    diff(claude_shelf_dir(root),
         {n: str(f) for n, (_l, f) in shared_skills(vault).items()}, "claude shelf")
    targets = [workspace] if workspace else sorted(
        p.parent for p in root.glob("*/agent.json"))
    for ws in targets:
        diff(view_dir(ws), _wanted_view(ws, vault), f"{ws.name} .agents/skills")
    return out


# -- Loam's shelf: the library, installed ---------------------------------------

def library_diff(source: Path | None = None, vault: Path | None = None,
                 dest: Path | None = None) -> dict[str, list[str]]:
    """How ``<system>/Loam/`` (or ``dest``) differs from the library: files
    ``added`` (in the library only), ``removed`` (in the vault only) and
    ``changed``."""
    src, dst = source or LIBRARY, dest or loam_dir(vault)

    def files(root: Path) -> set[str]:
        if not root.is_dir():
            return set()
        return {str(p.relative_to(root)) for p in root.rglob("*")
                if p.is_file() and not any(part.startswith(".") for part in
                                           p.relative_to(root).parts)}

    a, b = files(src), files(dst)
    kept = KEPT_DIR + os.sep
    a = {f for f in a if not f.startswith(kept)}
    b = {f for f in b if not f.startswith(kept)}
    return {"added": sorted(a - b), "removed": sorted(b - a),
            "changed": sorted(f for f in a & b
                              if not filecmp.cmp(src / f, dst / f, shallow=False))}


def install_library(source: Path | None = None, vault: Path | None = None,
                    dest: Path | None = None) -> dict[str, list[str]]:
    """Replace ``<system>/Loam/`` whole with the library. Returns the diff
    that was applied (``library_diff`` before the copy), so an edit someone
    made there is named rather than lost silently."""
    src, dst = source or LIBRARY, dest or loam_dir(vault)
    if not (src / "README.md").is_file():
        raise FileNotFoundError(f"no library at {src}")
    diff = library_diff(src, dest=dst)
    if dst.is_dir() and not any(diff.values()):
        return diff
    dst.parent.mkdir(parents=True, exist_ok=True)
    new = dst.with_name(f".{LOAM_DIR}.new-{os.getpid()}")
    old = dst.with_name(f".{LOAM_DIR}.old-{os.getpid()}")
    shutil.rmtree(new, ignore_errors=True)
    shutil.copytree(src, new, symlinks=False,
                    ignore=shutil.ignore_patterns(".*", "__pycache__"))
    shutil.rmtree(new / KEPT_DIR, ignore_errors=True)
    if dst.exists():
        os.replace(dst, old)
    carry_kept(old, new)
    os.replace(new, dst)
    shutil.rmtree(old, ignore_errors=True)
    return diff


def carry_kept(src: Path, dst: Path) -> None:
    """Move ``src``'s kept folder into ``dst`` (replacing any there): the data
    plugins keep in the vault survives Loam's folder being swapped whole."""
    kept = src / KEPT_DIR
    if kept.is_dir() and not kept.is_symlink():
        shutil.rmtree(dst / KEPT_DIR, ignore_errors=True)
        dst.mkdir(parents=True, exist_ok=True)
        os.replace(kept, dst / KEPT_DIR)


# -- the starter vault ------------------------------------------------------------

OWN_SHELF_README = """# Shared skills

Every agent reads the skills on this shelf, whatever its provider. A skill is
a folder holding a `SKILL.md` (frontmatter with `name`, matching the folder,
and `description`; the instructions below it). Put skills here that you want
every agent to share — ones you or an agent write, or skill folders you copy
from the Market.

Loam's updates never touch this folder. A skill here wins over one of
Loam's with the same name, and an agent's own skill (in its workspace's
`skills/`) wins over both.
"""


def seed_dictation_terms(system: Path) -> bool:
    """Create the shared vocabulary note without changing an owner's terms."""
    note = system / "Dictation terms.md"
    try:
        with note.open("x", encoding="utf-8") as out:
            out.write("# Dictation terms\n\n%% Add one name or term per line. %%\n")
    except FileExistsError:
        return False
    return True


def seed_vault(vault: Path, folders: dict[str, str] | None = None,
               library: Path | None = None) -> list[str]:
    """The starter vault a blank install gets, at ``vault``: ``About me.md``,
    ``<projects>/Loam/Start here.html``, and the Loam files folders
    (conversations, attachments, boards, system + library + skills shelf).
    No vault-root ``README.md``, ``Inbox``, ``Daily`` or ``Images`` folders are
    created on a new install. Writes only what is missing (the library
    excepted, which is Loam's and installed whole), so on a vault that already
    exists it moves nothing; ``About me.md`` and ``Start here.html`` only on a
    fresh vault (one with no visible entries yet). Returns what it made."""
    f = {**lc.VAULT_FOLDERS, **(folders or {})}
    made = []
    # A brand-new vault has no visible files or folders yet; an existing vault
    # is never handed starter pages it did not have (or that the owner removed).
    fresh = not vault.exists() or not any(
        not p.name.startswith(".") for p in vault.iterdir()
    )
    for key in ("conversations", "projects", "attachments", "boards", "system"):
        path = vault / f[key]
        if not path.is_dir():
            path.mkdir(parents=True)
            made.append(f[key] + "/")
    if seed_dictation_terms(vault / f["system"]):
        made.append(f"{f['system']}/Dictation terms.md")
    shelf = vault / f["system"] / OWN_DIR
    if not shelf.is_dir():
        shelf.mkdir(parents=True)
        made.append(f"{f['system']}/{OWN_DIR}/")
        (shelf / "README.md").write_text(OWN_SHELF_README, encoding="utf-8")
    about = vault / ABOUT_ME
    if fresh and not about.exists():
        about.write_text(ABOUT_ME_TEXT, encoding="utf-8")
        made.append(ABOUT_ME)
    if fresh and START_HERE_SRC.is_file():
        loam_proj = vault / f["projects"] / LOAM_DIR
        loam_proj.mkdir(parents=True, exist_ok=True)
        explainer = loam_proj / START_HERE
        if not explainer.exists():
            explainer.write_text(START_HERE_SRC.read_text(encoding="utf-8"),
                                 encoding="utf-8")
            made.append(f"{f['projects']}/{LOAM_DIR}/{START_HERE}")
    dst = vault / f["system"] / LOAM_DIR
    before = dst.is_dir()
    diff = install_library(library or LIBRARY, dest=dst)
    if not before or any(diff.values()):
        made.append(f"{f['system']}/{LOAM_DIR}/ ({'replaced' if before else 'installed'})")
    return made
