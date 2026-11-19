"""Preflight assertions that an agent's identity actually arrived.

Files being readable was never the risk. The risk, measured 2026-08-25, is
that ``CLAUDE.md``'s ``@path`` imports resolve **only** for files physically
inside the project directory -- an import pointing anywhere else is dropped
with no warning at all, and the agent runs, sounds entirely like itself,
and has no soul in context. A readability check cannot see that.

Two assertions, cheapest first:

1. **Structural.** Parse the ``@`` imports out of CLAUDE.md and confirm each
   resolves to a real file *inside the project root*. This is deterministic,
   free, and catches the exact silent-drop condition.
2. **Content.** Ask the model whether a phrase unique to ``soul.md`` is in
   its context, paired with a decoy phrase that is not. Requiring YES to the
   real one and NO to the decoy is what makes the answer worth anything --
   a bare YES/NO about context is a leading question, and models agree with
   leading questions.
"""

from __future__ import annotations

import json
import random
import re
import subprocess
from pathlib import Path

import loam_config as lc

from loam_account import AccountRefused, refusal

CLAUDE = str(lc.CLAUDE_BIN)

IMPORT_RE = re.compile(r"^@(\S+)\s*$", re.MULTILINE)

# The decoy is drawn from a DIFFERENT agent's soul, not invented. An invented
# decoy only proves the model will say NO to something absent; a sibling's
# soul proves this session is not carrying the wrong agent's identity — which
# is the failure this whole move is written against. Five near-identical
# projects, and the one thing that must never be shared is the soul.
AI_LABS = lc.WORKSPACES

# Phrases in soul.md that also appear in the agent's other auto-loaded files
# would not prove the soul import resolved, so a candidate must be unique to
# soul.md. `_always.md` joined this list on 2026-09-04 when it became a second
# CLAUDE.md import: a phrase present in both would have let a dropped soul
# import pass the gate, which is the exact failure this file exists to catch.
# `map.md` joined it on 2026-11-18, with the map's shape (lib/loam_loading.py),
# and Loam's text and the shared owner's page load beside it there, so a
# phrase in any of them is not unique to the soul either (SHARED_TEXTS).
SIBLING_FILES = ("config.md", "_always.md", "_active.md", "_reflection.md",
                 "map.md")


# Imports allowed from outside the project root: the shared reach.md
# (2026-11-10), which Claude Code loads because provision and the launcher
# approve external includes on the project's key. Anything else outside is
# still dropped silently, so it is still refused here.
# Since 2026-11-18 also Loam's text and the shared owner's page, the two
# absolute imports of an agent with a map (lib/loam_loading.py).
SHARED_IMPORTS = (
    AI_LABS / "_shared" / "reach.md",
    Path(__file__).resolve().parent.parent / "docs" / "persona" / "loam.md",
    AI_LABS / "_shared" / "owner.md",
)


def _shared_imports() -> tuple[Path, ...]:
    """SHARED_IMPORTS, with the owner's page where a test has put the
    workspaces (lib/loam_loading.owner_page)."""
    import loam_loading  # noqa: PLC0415
    return SHARED_IMPORTS + (loam_loading.owner_page().resolve(),)


def _shared_texts() -> str:
    """Loam's text and the shared owner's page: loaded beside the soul in the
    map's shape, so a phrase in either does not prove the soul arrived."""
    out = ""
    for path in dict.fromkeys(_shared_imports()[1:]):
        try:
            out += path.read_text(encoding="utf-8")
        except OSError:
            pass
    return out


class IdentityError(Exception):
    """Identity did not arrive. Always fatal for an unattended wake."""


def imported_paths(project: Path) -> list[Path]:
    text = (project / "CLAUDE.md").read_text(encoding="utf-8")
    return [(project / m).resolve() for m in IMPORT_RE.findall(text)]


def assert_imports_resolve(project: Path) -> list[Path]:
    project = project.resolve()
    paths = imported_paths(project)
    if not paths:
        raise IdentityError(f"{project}/CLAUDE.md declares no @ imports")
    for path in paths:
        if not path.is_file():
            raise IdentityError(f"CLAUDE.md imports {path}, which does not exist")
        if not path.is_relative_to(project) and path not in _shared_imports():
            raise IdentityError(
                f"CLAUDE.md imports {path}, which is outside the project root "
                f"{project} — Claude Code drops such imports silently"
            )
    return paths


def _sentences(text: str) -> list[str]:
    """Candidate phrases: one line, 45 to 110 characters, in document order.

    A line break is a sentence boundary. Until 2026-09-05 the split was only
    on whitespace after ``.``, ``!`` or ``?``, so a heading -- which has no
    terminal punctuation -- was glued to the sentence beneath it, the piece
    carried a newline, and every soul lost its first sentence after each
    heading: usually the opening line of its identity section, the sentence
    most likely to be unique to it. Splitting on newlines and nothing
    cleverer lets the length band do the filtering; a list marker or a
    short table cell is simply too short to be a candidate.
    """
    out: list[str] = []
    for line in text.splitlines():
        for piece in re.split(r"(?<=[.!?])\s+", line):
            piece = piece.strip()
            # The no-newline rule is the invariant the probe relies on: a
            # phrase spanning two lines is never returned. Splitting on
            # lines makes it unreachable, and it stays as the guard.
            if 45 <= len(piece) <= 110 and "\n" not in piece:
                out.append(piece)
    return out


def sibling_souls(project: Path) -> dict[str, str]:
    """Every other provisioned agent's soul text, keyed by name."""
    out: dict[str, str] = {}
    for d in sorted(AI_LABS.iterdir()):
        if not d.is_dir() or d.resolve() == project.resolve():
            continue
        soul = d / "soul.md"
        if soul.is_file():
            try:
                out[d.name] = soul.read_text(encoding="utf-8")
            except OSError:
                pass
    return out


def pick_decoy(
    project: Path, rng: random.Random | None = None
) -> tuple[str, str]:
    """A sentence from another agent's soul, absent from this agent's files.

    Sampled, not deterministic, since 2026-09-05. Before that the decoy was
    the first usable sentence of the first sibling in sorted order, which
    made it one persona's for six of seven agents every night and meant five
    souls never served as anyone's decoy. Now the owner is drawn uniformly
    among siblings with at least one eligible sentence, and then a sentence
    is drawn within that owner. Two stages rather than one pool over every
    sentence, so a long soul cannot crowd the short ones out of the draw.

    ``rng`` exists so a test can seed the draw; the wake passes nothing and
    gets the system RNG. The owner drawn is what ``bin/wake`` records as
    ``identity_decoy_from``, so a sampled decoy stays diagnosable.
    """
    rng = rng if rng is not None else random.SystemRandom()
    mine = _shared_texts()
    for name in ("soul.md",) + SIBLING_FILES:
        try:
            mine += (project / name).read_text(encoding="utf-8")
        except OSError:
            pass
    eligible: dict[str, list[str]] = {}
    for owner, text in sibling_souls(project).items():
        # dict.fromkeys dedupes while keeping order, so a sentence a soul
        # repeats is not drawn twice as often as its neighbours.
        sentences = [
            s for s in dict.fromkeys(_sentences(text)) if s not in mine
        ]
        if sentences:
            eligible[owner] = sentences
    if not eligible:
        raise IdentityError("no decoy could be drawn from a sibling soul")
    owner = rng.choice(sorted(eligible))
    return rng.choice(eligible[owner]), owner


def pick_canary(project: Path) -> str:
    """A sentence unique to soul.md, chosen deterministically.

    Unique means: absent from this agent's other auto-loaded files AND from
    every sibling's soul. A phrase two souls share proves nothing about which
    one arrived.
    """
    soul = (project / "soul.md").read_text(encoding="utf-8")
    others = "".join(sibling_souls(project).values()) + _shared_texts()
    for name in SIBLING_FILES:
        try:
            others += (project / name).read_text(encoding="utf-8")
        except OSError:
            pass
    for sentence in _sentences(soul):
        if sentence not in others:
            return sentence
    raise IdentityError("no phrase unique to soul.md could be selected")


def probe_prompt(canary: str, decoy: str) -> str:
    """The question the preflight asks, on every provider."""
    return (
        "Answer with exactly two words separated by a space, nothing else. "
        "First word: YES if the text currently in your context contains the "
        f"exact phrase <<{canary}>>, otherwise NO. "
        "Second word: YES if it contains the exact phrase "
        f"<<{decoy}>>, otherwise NO."
    )


def judge(raw: str, stderr: str = "") -> None:
    """The answer, judged: the account first, then the soul. Raises
    AccountRefused or IdentityError; returns when the answer is YES NO."""
    # The account before the soul. A spend limit or an expired credential
    # comes back as the model's text, and testing that text for the soul
    # named the account's fault the soul's six times on 2026-09-15. Asked of
    # the raw answer, before it is upper-cased, so the reason reads as the
    # provider wrote it.
    refused = refusal((raw or "").strip() or stderr)
    if refused:
        raise AccountRefused(refused)
    answer = (raw or "").strip().upper()
    if "YES NO" not in answer:
        raise IdentityError(
            "soul.md content is not in the composed context "
            f"(expected 'YES NO' for real/decoy, got {answer[:120]!r})"
        )


def assert_soul_in_context(
    project: Path, canary: str, decoy: str, model: str = "fable",
    ask=None,
) -> None:
    """``ask(prompt) -> text`` asks another provider the same question
    (2026-11-13: a Codex wake asks it of a Codex thread composed as the wake
    will be, lib/loam_codex_wake.py); by default it is Claude Code."""
    prompt = probe_prompt(canary, decoy)
    if ask is not None:
        judge(ask(prompt))
        return
    # `--settings '{"outputStyle":"none"}'` composes this probe the way the
    # wake it gates will be composed. Without it the probe would load the
    # conversation lane (the project settings name it) and the wake would
    # not -- a preflight that proves the soul arrives for a session shaped
    # differently from the one that runs, which is the same class of mistake
    # as running the preflight on a different model.
    argv = [
        CLAUDE, "-p", prompt, "--model", model,
        "--settings", json.dumps({"outputStyle": "none"}),
        "--disallowed-tools", "Read", "Glob", "Grep", "Task",
        "WebFetch", "WebSearch", "Bash",
    ]

    # One retry, and only on a timeout. On 2026-09-01 this call hung and
    # a persona lost the whole night to it -- a gate on a live model call turns
    # a slow API minute into a missing wake. A timeout says nothing about
    # whether the soul loaded, so retrying it costs one more two-word call
    # and recovers the night.
    #
    # Deliberately NOT retried: a wrong answer. "NO YES" means this session
    # is carrying the wrong soul, "NO NO" that it carries none, and both are
    # findings, not noise. Retrying a real refusal until it passes is how a
    # gate becomes decorative.
    for attempt in (1, 2):
        try:
            result = subprocess.run(
                argv, cwd=str(project), capture_output=True, text=True,
                check=False, timeout=300,
            )
            break
        except subprocess.TimeoutExpired:
            if attempt == 2:
                raise
    judge(result.stdout or "", result.stderr or "")


def preflight_identity(project: Path, model: str = "fable",
                       ask=None) -> tuple[str, str]:
    """Returns (canary, decoy_owner). Raises IdentityError if identity is wrong.

    A YES to the decoy is not a soft failure: it means this session is
    carrying another agent's soul, which is worse than carrying none.
    """
    assert_imports_resolve(project)
    canary = pick_canary(project)
    decoy, decoy_owner = pick_decoy(project)
    assert_soul_in_context(project, canary, decoy, model=model, ask=ask)
    return canary, decoy_owner
