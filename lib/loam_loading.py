"""What an agent loads on every run, and the one switch between two shapes.

Decided 2026-11-18 with the owner (the vault's *decisions, batch A: what
loads, memory, config*). An agent loads three things of its own on every
kind of run, the same in a conversation, at a wake and in a cron, on every
provider: its **soul**, its **config** and its **map**. A run adds only its
own input: a thread's summary and recent messages, a wake's instruction and
the list of what changed in the vault, a cron's body.

**Config has two parts that load together.**

- *Loam's text* (``LOAM_TEXT``, ``docs/persona/loam.md`` in the checkout): short,
  the same on every install, replaced by an update. No agent edits it.
- *The owner's words*: the owner's rules, verbatim and dated. One shared
  page every agent on the install loads (``OWNER_PAGE``,
  ``<workspaces>/_shared/owner.md``), and the agent's own ``config.md`` for
  the rules that are only its. An update never touches either: neither is in
  the checkout or the vault's ``System/Loam/``. An agent adds to them when
  the owner states a rule.

**The map** (``MAP``, ``map.md`` in the workspace) is the agent's own and
the only file of its own that loads. Loam says nothing about what is in it.
Over ``loam_memory.LIMIT_CHARS`` it is reported and still loads; nothing
refuses a run over it. It has no frontmatter that belongs to a loader and no
symlink to break: it reaches every provider as a ``CLAUDE.md`` import.

**The presence of the map is the switch.** An agent with no ``map.md``
loads exactly as before (``_always.md`` on every run, ``_active.md`` in a
conversation through the output style, ``_reflection.md`` at a wake, a
cron's file in a cron), so nothing breaks before an agent moves, and moving
is the agent's own act: it writes ``map.md``. Loam never deletes or moves
the old files; an agent with a map simply stops loading them.

The shape is rendered into the two files every conversation route reads at
start (the broker, the wake, the engineer and reviewer launchers, and Codex
and Antigravity, which compose from ``CLAUDE.md``'s imports): ``CLAUDE.md`` and
``.claude/settings.json``, by ``bin/provision``'s ``claude_md`` and
``settings``. Because the agent can make or remove its map at any moment,
``refresh`` re-renders those two whenever the shape on disk and the shape
rendered disagree. It is called by every launcher before a run and by the
persona's PostToolUse hook after every write, so the next run is in the new
shape whichever route starts it. A session already running keeps what it
started with.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path

import loam_config as lc
from loam_memory import (
    ALWAYS_LAYER,
    CONVERSATION_LAYER,
    CRON_LANE_DIR,
    MAP_LAYER,
    REFLECTION_LAYER,
)

MAP = MAP_LAYER
# From this file's own tree, as bin/provision finds its lib (2026-11-09):
# live, that is the checkout; in a job's tree or a landing's, that tree, so
# a test there renders and resolves the text the tree carries.
LOAM_TEXT = Path(__file__).resolve().parent.parent / "docs" / "persona" / "loam.md"
OWNER_PAGE = lc.WORKSPACES / "_shared" / "owner.md"


def owner_page() -> Path:
    """The shared owner's page, under the workspaces a test may point
    elsewhere (``LOAM_AI_LABS``, as lib/loam_agents.py reads it), so no test
    writes the install's own."""
    root = os.environ.get("LOAM_AI_LABS")
    return Path(root) / "_shared" / "owner.md" if root else OWNER_PAGE
CONFIG = "config.md"
SOUL = "soul.md"

# What the shared page holds when Loam makes it: a heading and one line, so
# the import resolves and the page says what it is. The words under it are
# the owner's, and nothing here writes them.
OWNER_PAGE_SEED = (
    "# The owner's words\n\n"
    "Rules the owner gave every agent, in their words, each with its date.\n"
)

# The one line a new agent's map starts with (2026-11-18): Loam's only words
# in it, and the agent's to keep or cut.
MAP_SEED = "# Map\n\nWhat loads with me on every run. It points to things; it does not hold them.\n"

# Run kinds, as the tests and the proof name them.
CONVERSATION = "conversation"
WAKE = "wake"
CRON = "cron"


def mapped(workspace: Path) -> bool:
    """Whether this agent has moved: its workspace has a ``map.md``."""
    return (Path(workspace) / MAP).is_file()


def rendered_mapped(workspace: Path) -> bool | None:
    """Which shape ``CLAUDE.md`` was rendered in: True when it imports the
    map, False when it does not, None when there is no ``CLAUDE.md``."""
    try:
        text = (Path(workspace) / "CLAUDE.md").read_text(encoding="utf-8")
    except OSError:
        return None
    return f"@{MAP}" in text.splitlines()


def own_files(workspace: Path, kind: str, cron: str | None = None) -> list[Path]:
    """The agent's own files that load on a run of this kind, in order.

    The one statement of the two shapes, which the tests hold every launcher
    and provider to. Loam's text, the shared owner's page, ``CLAUDE.md``'s
    own paragraphs and ``reach.md`` are not the agent's and are not listed;
    nor is a run's own input (a thread's summary, a wake's prompt), except a
    cron's body, which arrives as a file of the agent's."""
    ws = Path(workspace)
    if mapped(ws):
        out = [ws / SOUL, ws / CONFIG, ws / MAP]
    else:
        out = [ws / SOUL, ws / CONFIG, ws / ALWAYS_LAYER]
        if kind == CONVERSATION:
            out.append(ws / CONVERSATION_LAYER)
        elif kind == WAKE:
            out.append(ws / REFLECTION_LAYER)
    if kind == CRON and cron:
        out.append(ws / CRON_LANE_DIR / f"{cron}.md")
    return out


def ensure_owner_page() -> bool:
    """Make the shared owner's page if there is none. Never rewrites one.
    Returns whether it was made."""
    page = owner_page()
    if page.exists():
        return False
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(OWNER_PAGE_SEED, encoding="utf-8")
    return True


def _provision():
    path = Path(__file__).resolve().parent.parent / "bin" / "provision"
    loader = importlib.machinery.SourceFileLoader("loam_provision_render", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _write_if_changed(path: Path, text: str) -> bool:
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    tmp = path.with_name(f".{path.name}.loading-tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return True


def refresh(workspace: Path, force: bool = False) -> bool:
    """Re-render ``CLAUDE.md`` and ``.claude/settings.json`` when the shape
    on disk is not the shape they were rendered in (or always, with
    ``force``). Returns whether either file changed. A workspace that was
    never provisioned (no ``agent.json`` or no ``CLAUDE.md``) is left alone.
    """
    ws = Path(workspace)
    rendered = rendered_mapped(ws)
    if rendered is None or not (ws / "agent.json").is_file():
        return False
    if not force and rendered == mapped(ws):
        return False
    if mapped(ws):
        ensure_owner_page()
    prov = _provision()
    import loam_profile  # noqa: PLC0415
    card = json.loads((ws / "agent.json").read_text(encoding="utf-8"))
    display = loam_profile.self_name(ws.name, card if isinstance(card, dict) else {})
    changed = _write_if_changed(ws / "CLAUDE.md", prov.claude_md(display, ws))
    (ws / ".claude").mkdir(exist_ok=True)
    changed |= _write_if_changed(ws / ".claude" / "settings.json",
                                 json.dumps(prov.settings(ws), indent=2) + "\n")
    return changed


def quiet_refresh(workspace: Path) -> bool:
    """``refresh`` for a launcher: never fails a run. A render that cannot
    happen leaves the files as they were, and the run loads what they say."""
    try:
        return refresh(workspace)
    except Exception:  # noqa: BLE001 -- a launch is never refused over this
        return False
