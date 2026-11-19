"""Transform a Claude Code session JSONL into a vault transcript.

The output has to be indistinguishable from what the chassis writes into
``vault/15_CONVERSATIONS/`` -- same frontmatter fields in the same order,
same ``<!-- turn ... -->`` markers, same speaker headers, same wiki-links.
A header is the agent's plain name (2026-11-17): the emoji face is retired,
and an older ``agent.json`` that still holds ``emoji`` no longer prefixes
the headers written from now on. Files already written keep theirs. A human reading the folder should not be able to tell
which runtime produced a given file.

Upstream calls the session JSONL schema internal and version-unstable, so
this module is a pin-walk-class dependency: it is built against a saved
golden fixture (``fixtures/golden-session.jsonl``) and the test beside it
is what will tell us when the schema moves.

Idempotency is keyed on session id plus turn index, held in a sidecar
under ``agents/state/transcripts/``. Deliberately NOT recorded in the
transcript itself: extra attributes on the turn markers would be exactly
the tell this transform exists to avoid.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import loam_config as lc

# Mirrors chassis.transcripts._UNSAFE_CHARS: control characters and the
# path separator are the only things a vault filename cannot carry.
UNSAFE_CHARS = re.compile(r"[\x00-\x1f/]+")

# Filenames carrying these cannot sync to the Pixel (Android rejects them).
ANDROID_ILLEGAL = re.compile(r'[?:*"<>|]')

TITLE_MAX_CHARS = 60

# A transcript shorter than this is not worth a model call to title.
TITLE_MIN_TURNS = 2

# Probe and test sessions never reach the production record folder. This is
# structural rather than a matter of care: on 2026-08-25 a synthetic
# first-person prompt written by the operator landed in 15_CONVERSATIONS,
# was read by a live agent as a report from the owner, and propagated into its
# canonical Training Log and two other agents' commons inboxes within hours.
# Once written, a test artifact and a real conversation were indistinguishable.
#
# Quarantine sits outside the vault entirely, so the retrieval index never
# sees it and no agent can read it -- agents reach the vault and ai-labs,
# and this is in neither.
PROBE_ENV_VAR = "LOAM_PROBE"
QUARANTINE_DIR = lc.STATE / "probe-transcripts"


def is_probe() -> bool:
    value = os.environ.get(PROBE_ENV_VAR, "").strip().lower()
    return value not in ("", "0", "false", "no")


def destination_dir(vault: Path) -> tuple[Path, bool]:
    """Where this session's transcript may be written, and whether quarantined.

    The transform decides, not the caller. A hook that passed the wrong
    directory would reintroduce exactly the failure this exists to prevent,
    so the refusal lives here rather than in whoever calls it.
    """
    if is_probe():
        return QUARANTINE_DIR, True
    return vault / lc.vault_folder("conversations"), False


@dataclass(frozen=True)
class Turn:
    speaker: str          # "user" or the agent's lowercase name
    content: str
    timestamp: str        # already formatted "%Y-%m-%dT%H:%M:%SZ"


def format_timestamp(raw: str) -> str:
    """ISO-8601 from the JSONL, normalised to the vault's UTC form."""
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return raw
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text_of(content) -> str:
    """Prose from a message body, dropping every non-prose block.

    ``thinking`` is private, ``tool_use`` / ``tool_result`` are machinery.
    A transcript records what was said.
    """
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "\n".join(p for p in parts if p).strip()


def iter_entries(jsonl_path: Path):
    """Every entry of a session file, in order, skipping what will not parse.

    The one reader of the CLI's session JSONL on this box. Everything that
    reads one -- the transcript transform below, the broker's adoption of a
    session started in the Claude Code app -- comes through here, so the
    schema upstream calls internal and version-unstable is understood in one
    place and moves in one place when it moves.
    """
    with jsonl_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def is_subagent(entry: dict) -> bool:
    """A subagent's traffic, which is never the persona's own words.

    ``isSidechain`` is the session file's mark and ``parent_tool_use_id`` is
    the SDK event stream's mark for the same thing; both are checked so a
    reader pointed at either shape cannot file a subagent's sentence as the
    persona's.
    """
    return bool(entry.get("isSidechain") or entry.get("parent_tool_use_id"))


# The session file says who a prompt came from: ``origin: {"kind": "human"}``
# is a person typing, and a wake, a cron, a probe or a broker-driven turn
# carries no origin at all. Read off this box's own session files 2026-09-20,
# across 855 sessions: every wake and cron prompt has `promptSource: "sdk"`
# and `origin: null`, and the two other kinds that appear --
# `task-notification` (a subagent reporting back) and `peer` (another session
# writing in) -- are machinery a person did not type.
HUMAN_ORIGIN = "human"


def is_person(entry: dict) -> bool:
    """A turn a PERSON typed, which is stricter than ``promptSource``.

    ``parse_session`` below treats any ``user`` entry carrying a
    ``promptSource`` as a person's turn, and for a transcript that is right:
    the transcript hook never runs for a wake in the first place, so anything
    it sees with a prompt in it was said to the agent by somebody.

    Adoption has no such guard. It looks at every session file a persona has,
    wakes and crons included, and "a person was here" is the whole question
    it has to answer -- so it asks the field that states it.
    """
    if entry.get("type") != "user" or not entry.get("promptSource"):
        return False
    origin = entry.get("origin")
    return isinstance(origin, dict) and origin.get("kind") == HUMAN_ORIGIN


# ---------- what a person typed, and nothing else ----------

# The session file uses the `user` role for two different things: what somebody
# typed, and whatever the harness needed to hand the model next. Measured across
# all 42 project directories on this box 2026-09-23: of 1,722 user entries
# carrying a promptSource, 139 are task notifications (a background subagent
# reporting back, whole engineer receipts inside) and 20 are peer messages from
# another Claude session. Both state what they are in `origin.kind`, so the
# writer asks the field rather than guessing at the text.
#
# The Loam interface is why this cannot be done the other way round -- by
# keeping `origin.kind == "human"` and dropping the rest. A turn the broker
# sends into a live CLI has no origin at all (1,038 of those entries), and that
# is the owner typing in the browser. Absence of the field means nothing; the
# machinery kinds are named.
HARNESS_ORIGIN_KINDS = frozenset({"task-notification", "peer"})

# Harness-injected content wrapped around or beside a person's own words. Each
# pattern matches a COMPLETE envelope, opening tag to closing tag, and an
# unterminated tag is left exactly where it is: a person writing about a
# `<task-notification>` in prose is a shape that exists on this box (one
# message, 2.5 KB, 2026-09-05), and a rule that ran from an opening tag to the
# end of the text would have eaten all of it.
#
# The peer envelope carries harness framing outside its tags -- a preamble line
# before it and a paragraph of instruction after it, neither of which anybody
# typed -- so both are part of the envelope here.
_HARNESS_ENVELOPES = tuple(
    re.compile(pattern, re.S)
    for pattern in (
        r"<task-notification>.*?</task-notification>",
        r"<system-reminder>.*?</system-reminder>",
        r"(?:Another Claude session sent a message:[ \t]*\n)?"
        r"<cross-session-message\b.*?</cross-session-message>"
        r"(?:[ \t]*\n\s*This came from another Claude session[^\n]*)?",
        r"<local-command-caveat>.*?</local-command-caveat>",
        r"<local-command-stdout>.*?</local-command-stdout>",
        r"<local-command-stderr>.*?</local-command-stderr>",
        r"<command-name>.*?</command-name>",
        r"<command-message>.*?</command-message>",
        r"<command-args>.*?</command-args>",
        # The broker's clock line at the head of a person's message
        # (lib/loam_clock.py, 2026-11-09).
        r"<loam-clock>.*?</loam-clock>",
    )
)

_BLANK_RUN = re.compile(r"\n{3,}")


def person_text(text: str) -> str:
    """What a person typed, out of the user-role text of a session entry.

    Every complete harness envelope comes out and what is left is the typed
    text. A message that is nothing but envelope returns the empty string,
    which is the caller's signal that it was never a turn.
    """
    cleaned = text
    for pattern in _HARNESS_ENVELOPES:
        cleaned = pattern.sub("", cleaned)
    if cleaned != text:
        # An envelope cut out of the middle leaves a hole; close it up. Only
        # when something was actually cut: a message no envelope touched is
        # passed through with its own blank lines exactly as they were typed.
        # Collapsing unconditionally reformatted 84 turns across 34 files in a
        # dry run over the vault -- people's own paragraphs, not machinery.
        cleaned = _BLANK_RUN.sub("\n\n", cleaned)
    return cleaned.strip()


def is_harness_prompt(entry: dict) -> bool:
    """A user-role entry the harness delivered, that no person typed.

    ``isMeta`` is how the CLI marks its own interjections (the local-command
    caveat, an image's dimensions) and ``isCompactSummary`` how it marks the
    summary it writes when a conversation is compacted. Neither carries a
    promptSource in any session file on this box, so both are already dropped;
    they are named here so that the day one of them does carry one, it is still
    not filed as something the owner said.
    """
    origin = entry.get("origin")
    kind = origin.get("kind") if isinstance(origin, dict) else None
    if kind in HARNESS_ORIGIN_KINDS:
        return True
    return bool(entry.get("isMeta") or entry.get("isCompactSummary"))


def parse_session(jsonl_path: Path, agent: str) -> list[Turn]:
    """Extract the conversation from a session JSONL, in order.

    A real user turn carries ``promptSource``; a ``user`` entry without it
    is a tool result being fed back to the model, not something a person
    said. Carrying one is necessary and no longer sufficient: a task
    notification and a peer message carry one too, and ``is_harness_prompt``
    above refuses them. What survives goes through ``person_text``, so a
    typed message that arrived with reminders wrapped around it is filed as
    the sentence the owner typed. A harness entry still ENDS the agent's turn:
    the reply that follows it is a separate thing the person read, with its
    own timestamp, and merging it into the reply before would file two
    messages minutes apart as one.

    Consecutive assistant entries are coalesced into a single turn,
    because one reply is often split across several entries by interleaved
    tool calls -- the chassis writes one turn per reply and so must this.
    Every top-level text block of a turn belongs to the record, in order:
    the sentence said before the tools and the answer said after them are
    both things the agent said to the person, and what happened between
    them is not.

    Subagent traffic never appears. ``isSidechain`` is how the CLI's own
    session file marks it; ``parent_tool_use_id`` is how the same traffic
    is marked on the SDK's event stream, and is checked here so that a
    writer pointed at a stream-shaped file cannot file a subagent's words
    as the persona's. (Zero of the 54 session files on this box in
    2026-09 carry it; it costs nothing to refuse it before one does.)
    """
    turns: list[Turn] = []
    pending: list[str] = []
    pending_ts: str | None = None

    def flush() -> None:
        nonlocal pending, pending_ts
        if pending and pending_ts:
            body = "\n\n".join(pending).strip()
            if body:
                turns.append(Turn(agent, body, pending_ts))
        pending = []
        pending_ts = None

    for entry in iter_entries(jsonl_path):
        if is_subagent(entry):
            continue

        kind = entry.get("type")
        message = entry.get("message") or {}
        timestamp = format_timestamp(entry.get("timestamp", ""))

        if kind == "user" and entry.get("promptSource"):
            flush()
            if is_harness_prompt(entry):
                continue
            text = person_text(_text_of(message.get("content")))
            if text:
                turns.append(Turn("user", text, timestamp))
        elif kind == "assistant":
            text = _text_of(message.get("content"))
            if text:
                if pending_ts is None:
                    pending_ts = timestamp
                pending.append(text)

    flush()
    # A transcript starts when a person says something. `/clear` begins a new
    # session whose first entry is the assistant's own "No response requested."
    # acknowledgement of the clear, which would otherwise open the file with a
    # turn nobody asked for. Measured 2026-08-26.
    while turns and turns[0].speaker != "user":
        turns.pop(0)
    return turns


# -- Codex (2026-10-01) ------------------------------------------------------
#
# A Codex thread's session file is its rollout, under CODEX_HOME/sessions/,
# and the Stop hook's ``transcript_path`` names it exactly as Claude Code's
# names its JSONL. One line per record, ``{"timestamp", "type", "payload"}``;
# the first is ``session_meta``. What a person typed and what the persona said
# back are the ``item_completed`` events for ``UserMessage`` and
# ``AgentMessage`` items -- the same two items the app-server streams and the
# broker draws. Everything else (developer instructions, environment context,
# tool calls, reasoning) is harness and stays out, under the rule at the top
# of hooks/transcript_capture.py. The UserMessage shape is measured on this
# box (0.159.3, a turn that reached the API and was refused for want of a
# login); the AgentMessage shape is read tolerantly -- ``text`` or text
# content blocks -- until a signed-in turn has written one here.

def is_codex_rollout(path: Path) -> bool:
    try:
        for entry in iter_entries(path):
            return entry.get("type") == "session_meta" and isinstance(
                entry.get("payload"), dict)
    except OSError:
        return False
    return False


def _codex_text(item: dict) -> str:
    if isinstance(item.get("text"), str):
        return item["text"].strip()
    parts = [block.get("text") for block in item.get("content") or []
             if isinstance(block, dict) and isinstance(block.get("text"), str)]
    return "\n".join(p for p in parts if p).strip()


def parse_codex_rollout(path: Path, agent: str) -> list[Turn]:
    """The conversation in a Codex rollout, as ``parse_session`` answers it
    for a Claude session: user turns, and each reply's messages coalesced
    into one agent turn."""
    turns: list[Turn] = []
    pending: list[str] = []
    pending_ts: str | None = None

    def flush() -> None:
        nonlocal pending, pending_ts
        if pending and pending_ts:
            turns.append(Turn(agent, "\n\n".join(pending), pending_ts))
        pending, pending_ts = [], None

    for entry in iter_entries(path):
        payload = entry.get("payload") or {}
        if entry.get("type") != "event_msg" or payload.get("type") != "item_completed":
            continue
        item = payload.get("item") or {}
        kind = str(item.get("type") or "").lower()
        timestamp = format_timestamp(entry.get("timestamp", ""))
        if kind == "usermessage":
            flush()
            text = person_text(_codex_text(item))
            if text:
                turns.append(Turn("user", text, timestamp))
        elif kind == "agentmessage":
            text = _codex_text(item)
            if text:
                pending_ts = pending_ts or timestamp
                pending.append(text)
    flush()
    while turns and turns[0].speaker != "user":
        turns.pop(0)
    return turns


def session_title(jsonl_path: Path, fallback: str) -> str:
    """Prefer the session's own AI-generated title; else the first prompt.

    The fallback is the first prompt a PERSON typed, under the same rule the
    turns are filed under -- a file named after a task notification's task id
    would be the same leak in the filename.
    """
    ai_title = None
    first_prompt = None
    for entry in iter_entries(jsonl_path):
        if entry.get("type") == "ai-title" and entry.get("aiTitle"):
            ai_title = entry["aiTitle"]
            break
        if (
            first_prompt is None
            and entry.get("type") == "user"
            and entry.get("promptSource")
            and not is_harness_prompt(entry)
        ):
            first_prompt = person_text(
                _text_of((entry.get("message") or {}).get("content"))
            ) or None
    return sanitize_title(ai_title or first_prompt or fallback)


def sanitize_title(title: str) -> str:
    cleaned = UNSAFE_CHARS.sub(" ", title)
    cleaned = ANDROID_ILLEGAL.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lstrip(".").strip()
    if len(cleaned) > TITLE_MAX_CHARS:
        cleaned = cleaned[:TITLE_MAX_CHARS].rsplit(" ", 1)[0].strip()
    return cleaned or "untitled conversation"


def yaml_double_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


# Surface values are a positive fact recorded at write time, never inferred.
# The old signal — "no telegram_chat_id, therefore not Telegram" — silently
# mislabels anything that was never a Telegram conversation, and a wrong value
# is worse than a missing one because it gets filtered on.
SURFACE_CLAUDE_CODE = "claude-code"
SURFACE_TELEGRAM = "telegram"
# A conversation held in the Loam interface. Its process is still a Claude
# Code CLI in the persona's workspace -- that is the whole trick of the broker
# -- so nothing about the session file says which surface a person was sitting
# at, and before 2026-09-22 every one of these filed as `claude-code`,
# indistinguishable from a Remote Control conversation. The launcher knows and
# nothing else does, so the launcher says so in the environment.
SURFACE_LOAM_UI = "loam-ui"

# The two variables the broker sets on every process it launches. Absent means
# `claude-code`, which is what a session nobody launched through the broker is:
# the owner at a terminal or in the Claude Code app.
SURFACE_ENV_VAR = "LOAM_SURFACE"
ORIGIN_ENV_VAR = "LOAM_ORIGIN"

# What a surface or an origin is allowed to look like in frontmatter. A value
# read from the environment reaches a YAML field, and one token of lowercase
# letters, digits and dashes is the whole vocabulary these fields have ever
# had. Anything else is somebody's shell exporting something unrelated, and
# the honest answer to it is the default rather than a mangled field.
_TOKEN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def surface_of_env(env=None) -> tuple[str, str | None]:
    """The surface this session is being held on, and where it came from.

    Read from the environment and not inferred, for the same reason the
    surface field exists at all: the writer cannot see a browser, and a guess
    that is wrong is worse than no field, because the field gets filtered on.

    ``origin`` is the second fact and is usually absent. It is set for a
    session the broker adopted from the Claude Code app and then forked --
    that conversation is now being held in the Loam interface, which is what
    ``surface`` says, and it began somewhere else, which nothing else would
    record.
    """
    values = os.environ if env is None else env
    surface = (values.get(SURFACE_ENV_VAR) or "").strip().lower()
    origin = (values.get(ORIGIN_ENV_VAR) or "").strip().lower()
    if not _TOKEN.match(surface):
        surface = SURFACE_CLAUDE_CODE
    if not _TOKEN.match(origin):
        origin = None
    return surface, origin


def serialize_frontmatter(fm: dict) -> str:
    """Field order matches chassis.transcripts._serialize_frontmatter.

    The two ``telegram_*`` fields are omitted. That is not a deviation:
    the chassis writer already omits them when absent rather than writing
    null, so a file without them is a shape the existing format allows.
    """
    lines = [
        f"title: {yaml_double_quote(fm['title'])}",
        f"created_at: {fm['created_at']}",
        f"last_activity: {fm['last_activity']}",
        f"default_agent: {yaml_double_quote(fm['default_agent'])}",
        "agents: [" + ", ".join(yaml_double_quote(a) for a in fm["agents"]) + "]",
        f"created_by: {yaml_double_quote(fm['created_by'])}",
        f"last_modified_by: {yaml_double_quote(fm['last_modified_by'])}",
    ]
    # Appended after the historical keys so existing files keep their order
    # and a diff of a backfilled file is one added line.
    if fm.get("surface"):
        lines.append(f"surface: {fm['surface']}")
    # Only when there is one. A conversation that began where it is being held
    # has no origin to state, and a field that said `origin: loam-ui` beside
    # `surface: loam-ui` on 150 files would be a column of nothing.
    if fm.get("origin"):
        lines.append(f"origin: {fm['origin']}")
    if fm.get("session_id"):
        lines.append(f"session_id: {fm['session_id']}")
    # Only when it is not Claude Code, which every transcript before
    # 2026-10-01 was: one added line on a Codex conversation's file.
    if fm.get("provider"):
        lines.append(f"provider: {fm['provider']}")
    return "\n".join(lines) + "\n"


def render_turn(turn: Turn, display: str) -> str:
    return (
        f"<!-- turn speaker={turn.speaker.lower()} timestamp={turn.timestamp} -->\n"
        f"### {display}\n\n"
        f"{turn.content}"
    )


def render_file(
    title: str,
    turns: list[Turn],
    agent: str,
    session_id: str | None = None,
    surface: str = SURFACE_CLAUDE_CODE,
    origin: str | None = None,
    provider: str | None = None,
) -> str:
    display_agent = agent.capitalize()
    link = f"[[{display_agent}]]"
    agent_display = display_agent
    fm = {
        "title": title,
        "created_at": turns[0].timestamp,
        "last_activity": turns[-1].timestamp,
        "default_agent": link,
        "agents": [link],
        "created_by": link,
        "last_modified_by": link,
        "surface": surface,
        "origin": origin,
        "session_id": session_id,
        "provider": provider,
    }
    body = "\n\n".join(
        render_turn(t, "User" if t.speaker == "user" else agent_display)
        for t in turns
    )
    return f"---\n{serialize_frontmatter(fm)}---\n\n{body}\n"


def ai_title(jsonl_path: Path) -> str | None:
    """The session's own generated title, once Claude Code has made one.

    Absent early in a session (and always in short ``-p`` runs), which is
    why the capture hook opens a file under a provisional prompt-derived
    name and renames it when this starts returning something. A vault full
    of truncated first prompts would be exactly the tell the transform
    exists to avoid.
    """
    try:
        for entry in iter_entries(jsonl_path):
            if entry.get("type") == "ai-title" and entry.get("aiTitle"):
                return sanitize_title(entry["aiTitle"])
    except OSError:
        return None
    return None


# ---------- titling ----------

# Overridable so the fail-soft paths can be exercised with a real failing
# binary rather than by trusting that the except clauses are right.
CLAUDE_BIN = os.environ.get("LOAM_TITLER_BIN") or str(lc.CLAUDE_BIN)
WAKE_ENV = lc.WAKE_ENV
# A neutral working directory with no settings and no CLAUDE.md above it, so
# the titling call fires no hooks and captures no transcript of its own.
# ``--bare`` would also skip hooks but forces ANTHROPIC_API_KEY auth, and a
# metered key for filenames is the first crack in the subscription premise
# this runtime exists to test.
TITLER_CWD = lc.STATE / "titler"
# Well inside the 120s Stop/SessionEnd hook budget. The titler is a
# convenience sitting on the critical path of writing the record, so it must
# always leave enough of the budget to do the write. A hung titler that ate
# the whole hook timeout would cost the transcript, which is the one thing
# here that must never be lossy.
TITLE_TIMEOUT_S = 45
# By id, not the `haiku` alias (2026-10-14). The alias is the CLI's to move,
# and it did: `haiku` was claude-haiku-4-5-20251001 on 2.1.281 and is
# claude-haiku-5-5 on 2.1.294. An unattended call that changes model when the
# pin moves is a model decision nobody made. Adopting a newer Haiku is a
# change to this line, made on purpose.
TITLE_MODEL = "claude-haiku-4-5-20251001"


def _oauth_token() -> str | None:
    try:
        for line in WAKE_ENV.read_text(encoding="utf-8").splitlines():
            key, _, value = line.strip().partition("=")
            if key.strip() == "CLAUDE_CODE_OAUTH_TOKEN" and value.strip():
                return value.strip()
    except OSError:
        return None
    return None


def _title_debug(message: str) -> None:
    try:
        with open(lc.LOGS / "title-debug.log", "a") as fh:
            fh.write(message.rstrip() + "\n")
    except OSError:
        pass


def generate_title(turns: list[Turn], agent: str) -> str | None:
    """A short human title for a conversation, or None if anything goes wrong.

    Sessions outside the broker never produced an ``ai-title`` entry (measured
    2026-08-25, on Remote Control: 0 of the agent's sessions against 413 chassis-era ones), so
    without this every transcript keeps a truncated first prompt as its
    filename -- the one remaining tell that a file came from this runtime.

    Fail-soft by contract: every failure path returns None and the caller
    keeps the existing filename. A title is a convenience; the transcript is
    the record, and no titling problem may ever cost or corrupt one.
    """
    if len(turns) < TITLE_MIN_TURNS:
        return None
    token = _oauth_token()
    if not token:
        return None

    excerpt = "\n\n".join(
        f"{'{lc.OWNER_NAME}' if t.speaker == 'user' else agent.capitalize()}: {t.content[:1500]}"
        for t in turns[:4]
    )
    prompt = (
        "Below is the start of a conversation. Reply with a short title for "
        "it — at most 8 words, no quotes, no trailing period, no preamble. "
        "Name the subject the way a person would when filing it. Reply with "
        "the title and nothing else.\n\n"
        f"<conversation>\n{excerpt}\n</conversation>"
    )

    env = dict(os.environ)
    env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    # Belt and braces: if this call somehow did fire hooks, it is a probe and
    # its transcript would be quarantined rather than filed.
    env[PROBE_ENV_VAR] = "1"

    try:
        TITLER_CWD.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                CLAUDE_BIN, "-p", prompt, "--model", TITLE_MODEL,
                "--disallowed-tools", "Read", "Glob", "Grep", "Task",
                "WebFetch", "WebSearch", "Bash", "Write", "Edit",
            ],
            cwd=str(TITLER_CWD), env=env, capture_output=True, text=True,
            check=False, timeout=TITLE_TIMEOUT_S,
            # DEVNULL, not inherited: this runs inside a hook whose stdin is
            # the pipe the hook payload arrived on. The nested CLI blocks
            # reading that pipe until it times out, which is why every
            # in-hook titling call silently produced nothing while the same
            # call from a shell worked. Measured 2026-08-26.
            stdin=subprocess.DEVNULL,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        _title_debug(f"exception: {type(exc).__name__}: {exc}")
        return None
    if result.returncode != 0:
        _title_debug(
            f"rc={result.returncode} out={result.stdout[:300]!r} "
            f"err={result.stderr[:400]!r}"
        )
        return None

    candidate = (result.stdout or "").strip().splitlines()
    if not candidate:
        _title_debug(f"empty stdout; err={result.stderr[:400]!r}")
        return None
    raw = candidate[0].strip().strip('"').strip()
    # Validate the RAW reply, before sanitize_title truncates it. Checking
    # after truncation is no check at all: a rambling 26-word answer gets cut
    # to 10 words and passes, and the transcript is filed under the model's
    # preamble. Measured 2026-08-26 with a deliberately verbose titler.
    if not raw or len(raw.split()) > 12 or len(raw) > 2 * TITLE_MAX_CHARS:
        _title_debug(f"rejected candidate {raw[:200]!r}")
        return None
    title = sanitize_title(raw)
    if not title:
        _title_debug(f"candidate sanitised to nothing: {raw[:120]!r}")
        return None
    return title


def parse_file_turns(path: Path) -> list[str]:
    """Turn markers already present in a written transcript."""
    try:
        return re.findall(r"<!-- turn speaker=", path.read_text(encoding="utf-8"))
    except OSError:
        return []


# ---------- reconciling a written transcript with its session ----------

# The Stop hook races the CLI's own write of the turn's last assistant entry.
# Measured 2026-09-17 with a hook that polled the session file it had just
# been handed: at t+0.003s the file held only the sentence said before the
# tool calls, and the answer said after them appeared on disk at t+0.056s.
# The transform was reading a turn the CLI had not finished writing, which is
# why two Iris conversations were filed holding an opening line and no answer.
#
# Two defences, because either alone is lossy. This one waits for the file to
# stop growing before anything is parsed; ``plan_update`` below repairs a turn
# that grew after it was written, for the run where the wait was not enough.
SETTLE_QUIET_S = 0.3
SETTLE_LIMIT_S = 5.0
SETTLE_POLL_S = 0.05


def await_settled(
    path: Path,
    quiet: float = SETTLE_QUIET_S,
    limit: float = SETTLE_LIMIT_S,
) -> bool:
    """Block until the session file has been still for ``quiet`` seconds.

    True if it settled, False if it was still growing at ``limit``. Never
    raises: a session file that cannot be stat-ed is someone else's problem
    and the parse will fail informatively.
    """
    deadline = time.monotonic() + limit
    signature = None
    still_since = 0.0
    while True:
        try:
            stat = path.stat()
        except OSError:
            return True
        current = (stat.st_size, stat.st_mtime_ns)
        now = time.monotonic()
        if current != signature:
            signature, still_since = current, now
        elif now - still_since >= quiet:
            return True
        if now >= deadline:
            return False
        time.sleep(SETTLE_POLL_S)


TURN_MARKER = re.compile(r"^<!-- turn speaker=\S+ timestamp=\S+ -->$", re.M)


def split_transcript(text: str) -> tuple[str, list[str]]:
    """A written transcript as its frontmatter block and its rendered turns."""
    marks = [m.start() for m in TURN_MARKER.finditer(text)]
    if not marks:
        return text, []
    bounds = marks + [len(text)]
    blocks = [text[bounds[i]:bounds[i + 1]].strip() for i in range(len(marks))]
    return text[:marks[0]], blocks


def turn_body(block: str) -> str:
    """What was said in a rendered turn, without its marker and its header."""
    parts = block.split("\n", 2)
    return parts[2].strip() if len(parts) > 2 else ""


def bump_last_activity(text: str, timestamp: str) -> str:
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("last_activity: "):
            lines[i] = f"last_activity: {timestamp}"
            break
    return "\n".join(lines)


def plan_update(
    existing: str,
    turns: list[Turn],
    agent: str,
) -> tuple[str | None, str]:
    """The text this transcript should now hold, and why; None if it matches.

    Appending was once the whole of this, keyed on a turn count kept in a
    sidecar. That silently made the race above permanent: a repaired turn has
    the same index as the truncated one, so a count says there is nothing to
    do and the answer is lost for good. The key is content now, and a turn
    whose text has grown is rewritten where it stands.

    Conservative by construction: a turn already in the file is only replaced
    when its text survives verbatim inside what would replace it. Anything
    else -- a compacted session, a file adopted from another conversation, a
    hand edit -- is left exactly as it stands, and only turns beyond the end
    of the file are appended. The record may gain; it may never lose.
    """
    header, blocks = split_transcript(existing)
    if not blocks:
        return None, "no turn markers in file"
    bodies = [turn_body(b) for b in blocks]
    # The turns kept are left as written, an old emoji header included; only
    # the turns rendered now get the plain name.
    display = agent.capitalize()

    i = 0
    while i < min(len(bodies), len(turns)) and bodies[i] == turns[i].content:
        i += 1

    if i < len(bodies):
        stale = bodies[i:]
        replacing = [t.content for t in turns[i:]]
        if all(any(s in r for r in replacing) for s in stale):
            keep, render = blocks[:i], turns[i:]
            reason = f"repaired {len(stale)} turn(s), {len(render)} written"
        else:
            keep, render = blocks, turns[len(bodies):]
            reason = f"left {len(stale)} diverged turn(s) alone"
    else:
        keep, render = blocks, turns[i:]
        reason = f"+{len(render)} turn(s)"

    if not render:
        return None, "unchanged" if i == len(bodies) else reason
    rendered = [
        render_turn(t, "User" if t.speaker == "user" else display) for t in render
    ]
    head = bump_last_activity(header.rstrip("\n"), turns[-1].timestamp)
    return f"{head}\n\n" + "\n\n".join(keep + rendered) + "\n", reason


# ---------- repairing a transcript written before the rule ----------

SPEAKER = re.compile(r"^<!-- turn speaker=(\S+)")


@dataclass(frozen=True)
class Repair:
    """What the rule takes out of one already-written transcript."""

    text: str | None        # the file's new text, or None if it was clean
    dropped: list[str]      # first line of each turn removed whole
    trimmed: list[str]      # first line of each turn that lost an envelope
    turns_before: int
    turns_after: int
    note: str


def repair_transcript(existing: str) -> Repair:
    """Take the harness's own content out of a filed transcript.

    The same rule ``parse_session`` files under, applied to the markdown for
    the files written before the rule existed. A user turn that is nothing but
    envelope stops being a turn; one that mixes typed text with an envelope
    keeps the typed text; every other turn, the agent's included, is passed
    through byte-for-byte with its marker and its timestamp.

    Frontmatter is not touched, ``last_activity`` included. The writer is the
    author of these files and this is the writer correcting itself, which is a
    different thing from the file having been written later.

    Idempotent because it is a function of the text: run it on its own output
    and nothing matches, so ``text`` is None and nothing is written.
    """
    header, blocks = split_transcript(existing)
    if not blocks:
        return Repair(None, [], [], 0, 0, "no turn markers in file")

    kept: list[str] = []
    dropped: list[str] = []
    trimmed: list[str] = []
    for block in blocks:
        speaker = SPEAKER.match(block)
        if not speaker or speaker.group(1).lower() != "user":
            kept.append(block)
            continue
        body = turn_body(block)
        cleaned = person_text(body)
        if cleaned == body:
            kept.append(block)
            continue
        first = body.strip().split("\n", 1)[0]
        if not cleaned:
            dropped.append(first)
            continue
        marker, head, _ = block.split("\n", 2)
        kept.append(f"{marker}\n{head}\n\n{cleaned}")
        trimmed.append(first)

    if not dropped and not trimmed:
        return Repair(None, [], [], len(blocks), len(blocks), "clean")
    if not kept:
        # Every turn in the file was harness content. Nothing legitimate is
        # left to keep, and a transcript with no turns is not a transcript --
        # so this is reported and the file is left for a person to look at.
        return Repair(None, dropped, trimmed, len(blocks), 0,
                      "every turn is harness content; left alone")

    text = header.rstrip("\n") + "\n\n" + "\n\n".join(kept) + "\n"
    return Repair(
        text, dropped, trimmed, len(blocks), len(kept),
        f"-{len(dropped)} turn(s)"
        + (f", {len(trimmed)} trimmed" if trimmed else ""),
    )
