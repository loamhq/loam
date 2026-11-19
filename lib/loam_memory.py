"""The auto-loaded agent files: which ones there are, and how big they may be.

This module used to resolve a memory layer for a SessionStart hook to inject.
That hook is retired. Identity now composes from three native routes and this
file holds what survived them:

  ``_always.md``      a second ``@import`` in CLAUDE.md, on every run.
  ``_active.md``      the conversation lane, reached through the output-style
                      symlink at ``.claude/output-styles/conversation.md``.
  ``_reflection.md``  the reflection lane, passed by bin/wake as
                      ``--append-system-prompt-file``.
  ``crons/<lane>.md`` a cron lane, passed the same way by the same wrapper.
                      Named here before the lane existed; built 2026-09-05,
                      and the guard needed no change to cover it because
                      ``is_auto_loaded`` was written structurally rather than
                      as a list of names. Re-proved against the live path
                      that day rather than assumed.

All four load without anybody choosing to read them, which is what makes a
size limit worth having: the cost lands on every run, and nothing in any of
the three transports announces itself when a file grows. So one constant,
here, is the authority -- ``bin/provision`` renders it into every CLAUDE.md so
the agent can read the number, ``hooks/boundary_guard.py`` enforces it at edit
time (on the engineer and reviewer lanes; a persona's hook only reports),
and ``bin/wake`` and ``bin/lane-check`` report one -- neither refuses a run
over it (since 2026-11-18 for the wake). Since 2026-09-07 a second, lower number sits beside it:
``NEAR_LIMIT_CHARS``, at which the same two reporters write a warning line
and change nothing else.

The 10,000 is no longer a transport number, and none of the three routes
above has a cap anywhere near it -- the append-file route loaded 600,111
characters whole. It stays for two reasons that have nothing to do with a
transport: it is the number every agent has already internalised, and at
seven agents paying it on every run it is the right hygiene number on its
own account.

*One number in the history of this file is wrong and is retracted here,
because it travelled into four documents before anyone tested it.* The
SessionStart cap was reported as having moved from 10,000 to ~33,800 with the
2.1.259 release of 2026-09-03. **It did not move.** Settled 2026-09-04 by
running the original harness against all three binaries in one sitting:

    payload   2.1.245   2.1.259   2.1.260
      9,900    inline    inline    inline
      9,990    inline    inline    inline
     10,010     spill     spill     spill
     33,792     spill     spill     spill

The mechanism of the error is the part worth keeping. Over the limit the
model is not given less -- it is given a 2 KB preview *and a path*, and it
may read the rest. The original table's outcome column recorded whether the
model could produce the tail token, so at 33 KB it recorded a success the
model had achieved by reading the spill file the notice pointed it at. Its
own transcripts show the `Read` call. Two intermediate diagnoses were also
wrong and are retracted with it: that the cap is content-dependent rather
than fixed, and that the two versions had been run against different
payloads. They were the same payloads, from the same generator, on the same
evening. **What differed was not the binary and not the input, but whether
the model went and fetched what it had been told was missing** -- so the
measurement never discriminated between the two readings, and the observed
effect that made the story feel confirmed could not have.

None of this changes anything built here: the hook is retired, and the limit
never rested on the cap. The rule every agent had already internalised was
right the whole time.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

# ONE authority for the limit. bin/provision renders it into CLAUDE.md,
# boundary_guard.py refuses past it, bin/wake and bin/lane-check report it
# and never refuse a run.
# Characters, not bytes: it is what an agent can reason about, and what the
# CLAUDE.md sentence promises.
LIMIT_CHARS = 10_000

# The earlier warning, kept beside the limit so the two cannot drift apart.
# A file between this and LIMIT_CHARS is reported -- one line in the failure
# log, no comms line, at most one per night -- and the run proceeds exactly
# as it would have. Nothing refuses at this number; the edit-time guard in
# hooks/boundary_guard.py knows only LIMIT_CHARS.
#
# Why it exists: the two files closest to the limit on 2026-09-07 (9,957 and
# 9,750 characters) are rewritten by their agents on every wake, and grew on
# two of the three nights before. A warning that first arrives at 10,001 is
# a warning with no room left to act on; this one arrives with a thousand
# characters still to spend.
NEAR_LIMIT_CHARS = 9_000

# Nothing refuses a run over the limit (2026-11-18). From 2026-09-11 to
# 2026-11-18 bin/wake refused an over-limit lane file (OVER_LIMIT_REFUSED_FROM,
# gone); the owner's word of 2026-11-08 had made the limit a warning, and the
# refusal outlived it by ten days, keeping one persona's wake dark two nights
# over 9 characters. Every reporter now warns and the run goes ahead.

ALWAYS_LAYER = "_always.md"
CONVERSATION_LAYER = "_active.md"
# The map (2026-11-18, lib/loam_loading.py): the one file of the agent's own
# that loads, on every run, once the agent has one. Its presence is the
# switch between the two shapes; without it the three files above load as
# they always have.
MAP_LAYER = "map.md"
REFLECTION_LAYER = "_reflection.md"
CRON_LANE_DIR = "crons"

# The files that load on every run, whatever the lane.
UNIVERSAL_LAYERS = (ALWAYS_LAYER, CONVERSATION_LAYER, REFLECTION_LAYER)

# The output style is selected by name, and the name must match. Measured
# 2026-09-04 on 2.1.260: a style file whose `name:` disagrees with the
# selected style loads NOTHING, silently -- the same shape as a missing file,
# which also loads nothing and exits 0. `name:` may be omitted, in which case
# the filename decides; it may not be present and wrong. So the key is
# written explicitly and the guard refuses an edit that changes it.
STYLE_NAME = "conversation"
STYLE_REQUIRED = {
    "name": STYLE_NAME,
    "keep-coding-instructions": "true",
}

# Defence-in-depth strip, mirroring the chassis. `_always.md` and the lane
# files are stored without frontmatter by convention, and the append-file
# route does no stripping of its own -- a stray block would reach the model as
# literal ---bracketed YAML it would read as content. `_active.md` is the
# exception: its frontmatter is REQUIRED and is consumed by the style loader.
FRONTMATTER_RE = re.compile(r"\A---\r?\n(?:.*?\r?\n)?---\r?\n*", re.DOTALL)


class LayerError(Exception):
    """An auto-loaded file could not be established. Always fatal on a wake."""


def load_config(project_dir: Path) -> dict:
    path = project_dir / "agent.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LayerError(f"unreadable agent config at {path}: {exc}") from exc


def project_dir_from(payload: dict) -> Path:
    raw = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd")
    if not raw:
        raise LayerError("cannot locate the agent project directory")
    return Path(raw)


def strip_frontmatter(text: str) -> str:
    return FRONTMATTER_RE.sub("", text, count=1)


def is_auto_loaded(target: Path, workspace: Path) -> bool:
    """Does this path load into context without anyone choosing to read it?

    Deliberately structural rather than a list of names to keep in sync: the
    three universal layers in the workspace root, plus every ``.md`` under the
    cron lane folder, whatever lanes turn out to exist. A cron lane added
    later is covered the day it is added, with nothing to remember.

    In the map's shape (2026-11-18) the root's loading file is the map alone:
    an agent with a ``map.md`` loads none of the three, so a write to one of
    them is a write to an ordinary file.
    """
    try:
        target = target.resolve()
        workspace = workspace.resolve()
    except OSError:
        return False
    if target.parent == workspace:
        if (workspace / MAP_LAYER).is_file():
            if target.name == MAP_LAYER:
                return True
        elif target.name in UNIVERSAL_LAYERS:
            return True
    lanes = workspace / CRON_LANE_DIR
    return target.is_relative_to(lanes) and target.suffix == ".md"


def over_limit(text: str) -> bool:
    return len(text) > LIMIT_CHARS


def near_limit(size: int) -> bool:
    """Over the warning threshold but not over the limit. Sizes in characters."""
    return NEAR_LIMIT_CHARS < size <= LIMIT_CHARS


def near_limit_reason(name: str, size: int, path: Path) -> str:
    """The one sentence both reporters write, so the log reads the same from
    a wake and from the nightly lane check."""
    return (
        f"`{name}` is {size:,} characters, within "
        f"{LIMIT_CHARS - NEAR_LIMIT_CHARS:,} characters of the "
        f"{LIMIT_CHARS:,}-character limit on files that load automatically, "
        f"with {LIMIT_CHARS - size:,} to spare ({path}). A warning, not a "
        f"refusal: the file still loads and the run went ahead."
    )


def over_limit_reason(name: str, size: int, path: Path,
                      run: str = "the run went ahead") -> str:
    """The sentence both reporters write for a file over the limit. It says
    the run went ahead, because it did: nothing refuses a run over it."""
    return (
        f"`{name}` is {size:,} characters, over the {LIMIT_CHARS:,}-character "
        f"limit on files that load automatically, by {size - LIMIT_CHARS:,} "
        f"({path}). Reported, not refused: the file still loads whole and "
        f"{run}. Trim it when you next can."
    )


def resolve_lane(project_dir: Path, lane: str = REFLECTION_LAYER) -> tuple[str, str, Path]:
    """Return ``(agent_name, lane_text, lane_path)`` or raise LayerError.

    The text comes back with leading frontmatter stripped, because the caller
    is going to hand it to ``--append-system-prompt-file`` and that route
    delivers exactly what the file holds.
    """
    config = load_config(project_dir)
    agent = config["name"]
    workspace = Path(config["workspace"])
    path = workspace / lane

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LayerError(
            f"{agent} cannot read its lane file at {path}: {exc}"
        ) from exc

    stripped = strip_frontmatter(text).strip()
    if not stripped:
        raise LayerError(f"{agent}'s lane file at {path} is empty")
    return agent, stripped, path


def front_matter(text: str) -> dict[str, str]:
    """The leading frontmatter block's flat ``key: value`` pairs, as written.

    A hand-rolled two-line parser rather than a YAML dependency: these blocks
    are written by bin/provision and by the agents themselves and only ever
    hold flat lines, and the guard that calls this runs on every single tool
    call.

    ``partition`` splits on the FIRST colon only, so a value may contain one --
    which ``schedule: daily at 05:00 PT`` relies on entirely.
    """
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}
    body = match.group(0).split("---", 2)[1] if "---" in match.group(0) else ""
    out: dict[str, str] = {}
    for line in body.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip():
            out[key.strip()] = value.strip().strip("\"'")
    return out


def style_keys(text: str) -> dict[str, str]:
    """The frontmatter keys the output-style loader reads, case-folded.

    The loader matches its selector case-insensitively, so the comparison the
    guard makes has to as well. A cron header cannot be read this way -- an
    approval date survives lower-casing but a schedule read back for a receipt
    should be the agent's own text -- which is why the case-preserving reading
    above is the one that is shared.
    """
    return {k.lower(): v.lower() for k, v in front_matter(text).items()}


def style_frontmatter_intact(text: str) -> bool:
    keys = style_keys(text)
    return all(keys.get(k) == v for k, v in STYLE_REQUIRED.items())
