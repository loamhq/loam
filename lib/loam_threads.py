"""Threads and boards (2026-11-08): the record, its index, and the boards.

A thread is a markdown file in the vault, written by the runtime and by
nobody else -- the broker for a person's message and a persona's reply,
``bin/thread post`` for an agent posting from a wake or a turn. A harness
session is only the engine a persona used for one turn, so opening a thread
reads this file and never replays a session.

The record, under ``15_CONVERSATIONS/<title>.md``::

    ---
    kind: thread
    id: t-3f9c2a1b
    title: Planning the garden
    default: ada
    members: [ada, iris]
    engines: {ada: claude-code, iris: codex}
    board: 00_INBOX/boards/Planning the garden — board.md
    created: 2026-11-08T17:40:02Z
    ---

    ### Sam · 2026-11-08 17:40
    <!-- loam {"id": "m1", "author": "owner", "at": "...", "kind": "message"} -->

    The message text.

Every message is a ``###`` heading followed on the next line by its ``loam``
comment; the comment is what the parser reads (id, author, time, kind and
the "What I did" steps), and the heading is there for a person reading the
file in Obsidian. A message's text runs to the next such pair.

Boards are ordinary markdown under ``00_INBOX/boards/``, except General's,
which is the inbox's ``from-agents/comms.md``. An item's metadata is an
end-of-line comment: ``- [ ] text <!-- Ada · new · until 2026-11-22 -->``.

What the runtime keeps beside the vault (``state/threads/``) is a cache:
which file holds which id, each persona's harness session per thread, and
how far the owner has read. Losing it loses nothing the files do not say,
except those three conveniences; ``rebuild_index`` reads the ids back.

Stdlib only, so ``bin/thread`` runs without the broker or its venv.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

import loam_config as lc

import loam_clock

VAULT = Path(os.environ.get("LOAM_VAULT") or lc.VAULT)
AI_LABS = Path(os.environ.get("LOAM_AI_LABS") or lc.WORKSPACES)
STATE = Path(os.environ.get("LOAM_THREADS_STATE") or lc.STATE / "threads")

CONVERSATIONS = lc.vault_folder("conversations")
BOARDS = lc.vault_folder("boards")
GENERAL_ID = "general"
GENERAL_TITLE = "General"
GENERAL_BOARD = lc.vault_folder("general_board")

# Capacity hygiene, not permission (brief, 2026-11-08): how many turns agents
# may give each other by @mention before the owner says something again. A
# chain past this stops with a system line. Change it here; nothing else
# holds the number.
AGENT_CHAIN_CAP = 3

# The page size of GET /v1/threads/<id>, newest first.
PAGE = 50

# The owner, as an author (lib/loam_config.py). Compare with is_owner():
# records written before 2026-11-14 say the legacy id (LEGACY_OWNER_IDS).
OWNER = lc.OWNER_ID
is_owner = lc.is_owner
KINDS = ("message", "system", "bulletin")

META_RE = re.compile(r"^<!-- loam (\{.*\}) -->\s*$")
UNTIL_RE = re.compile(r"\buntil (\d{4}-\d{2}-\d{2})\b")
ITEM_META_RE = re.compile(r"<!--\s*(.*?)\s*-->\s*$")
MENTION_RE = re.compile(r"(?<![\w@])@([A-Za-z][\w-]*)")
UNSAFE_TITLE = re.compile(r'[\\/:*?"<>|#^\[\]\n\r\t]')


class ThreadError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def roster() -> list[str]:
    """The agents awake: an archived one (lib/loam_agents.py) is no member
    to bring in, no @ to match and no default to give a thread."""
    import loam_agents  # noqa: PLC0415
    try:
        names = sorted(d.name for d in AI_LABS.iterdir()
                       if d.is_dir() and (d / "agent.json").is_file())
    except OSError:
        return []
    return loam_agents.live(names, AI_LABS)


def display(persona: str) -> str:
    if lc.is_owner(persona):
        return lc.OWNER_NAME
    try:
        card = json.loads((AI_LABS / persona / "agent.json").read_text("utf-8"))
        # `display` first, as loam_broker.agent_card reads it: every live
        # agent.json has `name` as the lower-case id and `display` as the
        # name ("Sam brought in cleo" until 2026-11-10).
        shown = card.get("display")
        if isinstance(shown, str) and shown.strip():
            return shown.strip()
        name = card.get("name")
        if isinstance(name, str) and name.strip() and name.strip() != persona:
            return name.strip()
    except (OSError, json.JSONDecodeError):
        pass
    return persona.capitalize()


def mentions(text: str, people: list[str] | None = None) -> list[str]:
    """The personas an ``@Name`` names, in order, each once. A name is
    matched to a persona's id or its display name, case-insensitively; an
    ``@`` that names nobody is ordinary text."""
    people = roster() if people is None else people
    names = {}
    for p in people:
        names[p.lower()] = p
        names[display(p).lower()] = p
    out: list[str] = []
    for match in MENTION_RE.finditer(text or ""):
        who = names.get(match.group(1).lower())
        if who and who not in out:
            out.append(who)
    return out


# `@all` and `@everyone` (2026-11-10): from the owner, a turn for every member;
# from a persona, plain text. Not names a persona can have.
ALL_WORDS = ("all", "everyone")


def mentions_all(text: str) -> bool:
    """Whether the text says ``@all`` or ``@everyone``, case-insensitively."""
    return any(m.group(1).lower() in ALL_WORDS
               for m in MENTION_RE.finditer(text or ""))


def clean_title(title: str) -> str:
    return " ".join((title or "").split())[:120] or "Untitled thread"


def safe_title(title: str) -> str:
    """The title as a file name: what Obsidian and the filesystem refuse
    taken out."""
    line = " ".join(UNSAFE_TITLE.sub(" ", title or "").split()).strip(" .")
    return line[:80].rstrip() or "Untitled thread"


def board_rel(thread_id: str, title: str) -> str:
    if thread_id == GENERAL_ID:
        return GENERAL_BOARD
    return f"{BOARDS}/{safe_title(title)} — board.md"


# -- the record ---------------------------------------------------------------

def _yaml_list(items: list[str]) -> str:
    return "[" + ", ".join(items) + "]"


def _yaml_map(items: dict[str, str]) -> str:
    return "{" + ", ".join(f"{k}: {v}" for k, v in items.items()) + "}"


def _parse_flow(value: str):
    value = value.strip()
    if value.startswith('"'):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value.strip('"')
    if value.startswith("[") and value.endswith("]"):
        return [v.strip() for v in value[1:-1].split(",") if v.strip()]
    if value.startswith("{") and value.endswith("}"):
        out = {}
        for pair in value[1:-1].split(","):
            k, _, v = pair.partition(":")
            if k.strip():
                out[k.strip()] = v.strip()
        return out
    if value in ("true", "false"):
        return value == "true"
    return value


def render_front(meta: dict) -> str:
    """The frontmatter. ``default`` is written empty when the thread has
    none (2026-11-09): untagged messages are then recorded and nobody
    answers. ``icon``, ``archived`` and ``models`` (a member's model, when
    it is not the persona's own) are written only when set."""
    lines = ["---", "kind: thread", f"id: {meta['id']}",
             f"title: {json.dumps(meta['title'], ensure_ascii=False)}",
             f"default: {meta.get('default') or ''}".rstrip()]
    if meta.get("icon"):
        lines.append(f"icon: {json.dumps(meta['icon'], ensure_ascii=False)}")
    lines.append(f"members: {_yaml_list(meta['members'])}")
    lines.append(f"engines: {_yaml_map(meta['engines'])}")
    models = {p: m for p, m in (meta.get("models") or {}).items()
              if m and p in meta["members"]}
    if models:
        lines.append(f"models: {_yaml_map(models)}")
    lines.append(f"board: {meta['board']}")
    if meta.get("pinned"):
        lines.append("pinned: true")
    if meta.get("archived"):
        lines.append("archived: true")
    # A thread imported from a classic chat (2026-11-11): the broker session
    # it was, and the transcript the writer filed it under, which stays where
    # it is. Written only when set, like the keys above.
    if meta.get("imported_from"):
        lines.append(f"imported_from: {meta['imported_from']}")
    if meta.get("source"):
        lines.append(f"source: {json.dumps(meta['source'], ensure_ascii=False)}")
    # A thread branched from a message of another (2026-11-11): the source
    # thread's id, the message it was branched from, and the ids of the
    # window its first turns are handed (`branch_window`).
    if meta.get("branched_from"):
        lines.append(f"branched_from: {meta['branched_from']}")
        lines.append(f"branched_message: {meta.get('branched_message') or ''}".rstrip())
        lines.append(f"branched_window: {_yaml_list(meta.get('branched_window') or [])}")
    # The hello thread a blank install starts with (2026-11-14, first
    # release L11, lib/loam_starter.py): where the person lands first.
    if meta.get("hello"):
        lines.append("hello: true")
    lines.append(f"created: {meta['created']}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def split_front(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    meta = {}
    for line in text[4:end].splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = _parse_flow(value)
    return meta, text[end + 5:]


def parse_messages(body: str) -> list[dict]:
    lines = body.split("\n")
    out: list[dict] = []
    current: dict | None = None
    text: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if (line.startswith("### ") and i + 1 < len(lines)
                and META_RE.match(lines[i + 1])):
            if current is not None:
                current["text"] = "\n".join(text).strip()
                out.append(current)
            try:
                current = json.loads(META_RE.match(lines[i + 1]).group(1))
            except json.JSONDecodeError:
                current = {}
            current.setdefault("kind", "message")
            current.setdefault("did", [])
            text = []
            i += 2
            continue
        if current is not None:
            text.append(line)
        i += 1
    if current is not None:
        current["text"] = "\n".join(text).strip()
        out.append(current)
    return out


# A reply to one earlier message (2026-11-11). The record's `loam` comment
# carries `reply_to: <id>`; the readable markdown opens the message with one
# quote line naming who, the referenced message's own heading time and id,
# and its first QUOTE_CHARS characters. The parser drops that line again, so
# a message's `text` is only what its author wrote.
QUOTE_CHARS = 80
QUOTE_PREFIX = "> ↩ "
# How a persona replies from a turn: its reply opens with `↩ m12` (an ASCII
# `<- m12` too, for an engine that will not write the arrow). The writer
# strips the marker and records `reply_to`.
REPLY_MARK_RE = re.compile(r"^\s*(?:↩|<-)\s*(m\d+)\b[ \t]*[:.,—-]?[ \t]*\n?")
# How much of the referenced message a persona's prompt quotes in full.
PROMPT_QUOTE_CHARS = 4000


def heading_at(at: str) -> str:
    """A message's time as its ``###`` heading writes it."""
    return (at or "").replace("T", " ")[:16]


def quote_line(target: dict) -> str:
    who = display(target["author"]) if target.get("author") != "system" else "Loam"
    said = " ".join((target.get("text") or "").split())
    if len(said) > QUOTE_CHARS:
        said = said[:QUOTE_CHARS].rstrip() + "…"
    return (f"{QUOTE_PREFIX}{who}, {heading_at(target.get('at', ''))} "
            f"({target.get('id')}): {json.dumps(said, ensure_ascii=False)}")


def take_reply_mark(text: str) -> tuple[str, str | None]:
    """A persona's leading ``↩ m12`` marker: the text without it and the id,
    or the text as it came and None."""
    match = REPLY_MARK_RE.match(text or "")
    if not match:
        return text, None
    return text[match.end():].lstrip(), match.group(1)


# A reply's opening that may still become a marker while it streams: an
# arrow, an id still being written, nothing after it yet (2026-11-17). The
# stream holds such an opening back rather than flash `↩ m1` on the page.
PARTIAL_MARK_RE = re.compile(r"^\s*(?:(?:↩|<-?)\s*(?:m\d*)?[ \t]*[:.,—-]?[ \t]*)?$")


def streamed_opening(draft: str) -> tuple[str | None, str | None]:
    """A reply's opening words as they stream: ``(None, None)`` while they
    may still be a ``↩ m12`` marker, ``(rest, id)`` once a whole marker has
    words after it, else ``(draft, None)``."""
    if PARTIAL_MARK_RE.match(draft or ""):
        return None, None
    rest, target = take_reply_mark(draft)
    return (rest, target) if target else (draft, None)


def unmark_steps(entry_steps: list, reply_to: str | None) -> list:
    """A reply's full record as it is shown (2026-11-17): the ``↩ m12`` its
    first top-level words opened with comes off when ``reply_to`` names that
    same id -- the marker the writer already took off the message. Any other
    words, ids and markers stay as written; the file itself is never
    changed."""
    if not reply_to or not isinstance(entry_steps, list):
        return entry_steps
    for index, step in enumerate(entry_steps):
        if not isinstance(step, dict) or step.get("parent") is not None \
                or step.get("type") != "text" or not str(step.get("text") or "").strip():
            continue
        rest, target = take_reply_mark(step["text"])
        if target != reply_to:
            return entry_steps
        out = list(entry_steps)
        out[index] = {**step, "text": rest}
        return out
    return entry_steps


def find(msgs: list[dict], msg_id: str | None) -> dict | None:
    return next((m for m in msgs if msg_id and m.get("id") == msg_id), None)


def check_reply_to(msgs: list[dict], reply_to, thread_id: str) -> dict:
    """The message ``reply_to`` names in this thread, or a 400 / 404."""
    if not isinstance(reply_to, str) or not re.fullmatch(r"m\d+", reply_to):
        raise ThreadError(400, "`reply_to` is a message id like m12")
    target = find(msgs, reply_to)
    if target is None:
        raise ThreadError(404, f"no message {reply_to!r} in {thread_id}")
    return target


# -- images in an agent's reply (2026-11-18) -------------------------------------
#
# An agent shows an image in its reply by writing `![a few words](path)` on a
# line of its own (or anywhere in its text): a file in the vault or the
# workspaces, an absolute path or one relative to the vault. Up to three
# come off the text and ride on the message as `attachments`, the shape the
# owner's own files have, plus `root` (`vault` or `spaces`, the Files root
# the page reads them from). Every engine can do it: it is words, not a tool.
# A reference that is not such a file stays in the text as it was written.
REPLY_IMAGES_MAX = 3
REPLY_IMAGE_BYTES = 40 * 1024 * 1024
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".webp": "image/webp", ".gif": "image/gif", ".avif": "image/avif"}
IMAGE_REF_RE = re.compile(r"!\[([^\]\n]{0,300})\]\(\s*(<[^>\n]+>|[^)\n]+?)\s*\)")


def _image_file(raw: str) -> dict | None:
    from urllib.parse import unquote  # noqa: PLC0415
    raw = raw.strip()
    if raw.startswith("<") and raw.endswith(">"):
        raw = raw[1:-1].strip()
    elif re.search(r'\s+"[^"]*"$', raw):          # a Markdown title: ![a](p "t")
        raw = re.sub(r'\s+"[^"]*"$', "", raw)
    if re.match(r"^[a-z][a-z0-9+.-]*:", raw, re.I) and not raw.lower().startswith("file:"):
        return None                                 # a web address: not a file here
    raw = unquote(raw[7:] if raw.lower().startswith("file://") else raw)
    path = Path(raw) if raw.startswith("/") else VAULT / raw
    try:
        path = path.resolve()
        vault, spaces = VAULT.resolve(), AI_LABS.resolve()
    except OSError:
        return None
    if path.suffix.lower() not in IMAGE_TYPES or not path.is_file():
        return None
    conversations = vault / lc.vault_folder("conversations")
    if path.is_relative_to(vault) and not path.is_relative_to(conversations):
        root, rel = "vault", path.relative_to(vault)
    elif path.is_relative_to(spaces):
        root, rel = "spaces", path.relative_to(spaces)
    else:
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size > REPLY_IMAGE_BYTES:
        return None
    return {"path": str(rel), "root": root, "name": path.name,
            "type": IMAGE_TYPES[path.suffix.lower()], "size": size}


def reply_images(text: str) -> tuple[str, list[dict]]:
    """The images a reply shows, taken off its text: ``(text, attachments)``.
    At most REPLY_IMAGES_MAX, each once; a line that held only images goes
    with them."""
    if not text or "![" not in text:
        return text, []
    found: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def take(m: re.Match) -> str:
        if len(found) >= REPLY_IMAGES_MAX:
            return m.group(0)
        f = _image_file(m.group(2))
        if f is None:
            return m.group(0)
        if (f["root"], f["path"]) not in seen:
            seen.add((f["root"], f["path"]))
            alt = m.group(1).strip()
            if alt:
                f["alt"] = alt[:200]
            found.append(f)
        return "\0"

    out = IMAGE_REF_RE.sub(take, text)
    if not found:
        return text, []
    lines = [l for l in out.split("\n") if l.replace("\0", "").strip() or "\0" not in l]
    out = "\n".join(re.sub(r"[ \t]?\0", "", l) for l in lines)
    return re.sub(r"\n{3,}", "\n\n", out).strip(), found


def attachment_path(a: dict) -> str:
    """Where an attachment is on disk, for an agent reading the thread."""
    path = a.get("path") or ""
    if path.startswith("/"):
        return path
    return str((AI_LABS if a.get("root") == "spaces" else VAULT) / path)


def render_message(msg: dict, quoted: dict | None = None) -> str:
    at = heading_at(msg["at"])
    who = display(msg["author"]) if msg["author"] != "system" else "Loam"
    head = f"### {who} · {at}"
    if msg.get("kind") == "bulletin":
        head += " · woke"
    meta = {k: msg[k] for k in ("id", "author", "at", "kind") if k in msg}
    if msg.get("reply_to"):
        meta["reply_to"] = msg["reply_to"]
    if msg.get("did"):
        meta["did"] = msg["did"]
    # `segments` (2026-11-09): a reply that spoke, worked and spoke again,
    # in order -- `{"text"}` and `{"did": [{"label"}]}` -- up to the text
    # below, which is the last thing it said. `attachments` are the files a
    # message carried; `model` the model a turn ran on when it was chosen.
    # `late` (2026-11-10): a reply recovered from the session log because no
    # turn was watching when it was made.
    # `link` (2026-11-11): a system line that points at another thread --
    # `{"thread", "message"?, "title"}` -- as a branch's two lines do.
    # `after`: the message a line was placed under rather than appended
    # after the last (`insert_after`).
    # `steered` (2026-11-14): the ids of messages sent into the turn while
    # it worked, which this reply took in.
    # `preset` (2026-11-14, first release L11): the runtime's words posted in
    # an agent's name -- the hello thread's first message -- not a model's
    # turn (docs/starter.md).
    for key in ("persona", "engine", "model", "segments", "attachments", "late",
                "link", "after", "steered", "preset"):
        if msg.get(key):
            meta[key] = msg[key]
    text = (msg.get("text") or "").strip()
    if msg.get("kind") == "system":
        text = f"*{text}*"
    # `-->` inside a string would close the comment for a reader of the
    # markdown; as `--\u003e` it is the same JSON.
    comment = json.dumps(meta, ensure_ascii=False).replace("-->", "--\\u003e")
    if msg.get("reply_to") and quoted is not None:
        text = f"{quote_line(quoted)}\n\n{text}"
    return f"\n{head}\n<!-- loam {comment} -->\n\n{text}\n"


def _strip_system(msg: dict) -> dict:
    if msg.get("reply_to"):
        t = msg.get("text") or ""
        if t.startswith(QUOTE_PREFIX):
            msg["text"] = t.partition("\n")[2].strip()
    if msg.get("kind") == "system":
        t = msg.get("text") or ""
        if len(t) >= 2 and t.startswith("*") and t.endswith("*"):
            msg["text"] = t[1:-1]
    return msg


@contextmanager
def locked(path: Path):
    """One writer at a time per record: the broker and ``bin/thread`` are
    separate processes, and an append is read-modify-write of the counter."""
    STATE.mkdir(parents=True, exist_ok=True)
    lock = STATE / ".lock"
    with open(lock, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# -- the runtime's cache ------------------------------------------------------

def _state_path(name: str) -> Path:
    return STATE / name


def load_state(name: str) -> dict:
    try:
        return json.loads(_state_path(name).read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(name: str, data: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    _write_atomic(_state_path(name), json.dumps(data, indent=1, sort_keys=True))


def rebuild_index() -> dict:
    """Every thread record in the vault, by id, read off its frontmatter."""
    index = {}
    for path in (VAULT / CONVERSATIONS).glob("*.md"):
        try:
            with open(path, encoding="utf-8") as fh:
                head = fh.read(2048)
        except OSError:
            continue
        if "kind: thread" not in head:
            continue
        meta, _ = split_front(head if "\n---\n" in head[4:] else head + "\n---\n")
        if meta.get("kind") == "thread" and meta.get("id"):
            index[meta["id"]] = path.name
    save_state("index.json", index)
    return index


def index() -> dict:
    data = load_state("index.json")
    if not data:
        data = rebuild_index()
    return data


def record_path(thread_id: str) -> Path:
    name = index().get(thread_id)
    path = VAULT / CONVERSATIONS / name if name else None
    if path is None or not path.is_file():
        name = rebuild_index().get(thread_id)
        if not name:
            raise ThreadError(404, f"no thread {thread_id!r}")
        path = VAULT / CONVERSATIONS / name
    return path


# Parsed records, keyed by path and invalidated by (mtime, size): opening a
# thread is a stat and, when it changed, one read of one file.
_CACHE: dict[str, tuple[tuple[int, int], dict, list[dict]]] = {}


def read(thread_id: str) -> tuple[dict, list[dict], Path]:
    path = record_path(thread_id)
    st = path.stat()
    key = (st.st_mtime_ns, st.st_size)
    hit = _CACHE.get(str(path))
    if hit and hit[0] == key:
        return _copy_meta(hit[1]), hit[2], path
    meta, body = split_front(path.read_text("utf-8"))
    meta.setdefault("members", [])
    meta.setdefault("engines", {})
    meta["default"] = meta.get("default") or None
    meta["models"] = meta.get("models") if isinstance(meta.get("models"), dict) else {}
    meta["icon"] = meta.get("icon") or None
    meta["archived"] = meta.get("archived") is True
    meta["imported_from"] = meta.get("imported_from") or None
    meta["source"] = meta.get("source") or None
    meta["branched_from"] = meta.get("branched_from") or None
    meta["branched_message"] = meta.get("branched_message") or None
    meta["branched_window"] = (meta.get("branched_window")
                               if isinstance(meta.get("branched_window"), list) else [])
    msgs = [_strip_system(m) for m in parse_messages(body)]
    _CACHE[str(path)] = (key, meta, msgs)
    return _copy_meta(meta), msgs, path


def _copy_meta(meta: dict) -> dict:
    """A caller's own copy, members and engines included: ``update`` appends
    to ``members``, and a shallow copy shared that list with the cache and
    with every other caller's copy, so a PATCH's ``before`` already held the
    member it was adding (found 2026-11-09)."""
    out = dict(meta)
    out["members"] = list(meta.get("members") or [])
    out["engines"] = dict(meta.get("engines") or {})
    out["models"] = dict(meta.get("models") or {})
    out["branched_window"] = list(meta.get("branched_window") or [])
    return out


def _unique_path(title: str, own: Path | None = None) -> Path:
    """A free record name for ``title``; ``own`` (the record being renamed)
    counts as free, so a rename whose file name does not change keeps it."""
    base = safe_title(title)
    path = VAULT / CONVERSATIONS / f"{base}.md"
    n = 2
    while path.exists() and path != own:
        path = VAULT / CONVERSATIONS / f"{base} ({n}).md"
        n += 1
    return path


def _rename_board(thread_id: str, old_rel: str, title: str) -> str:
    """The board's new name for a renamed thread, its file moved there
    (2026-11-10). A name another thread's board already holds gets
    `` (2)``: before, the rename pointed this thread at that board and left
    its own behind, unlinked."""
    new_rel = board_rel(thread_id, title)
    if new_rel == old_rel:
        return old_rel
    stem = new_rel[:-len(" — board.md")]
    n = 2
    while (VAULT / new_rel).exists():
        new_rel = f"{stem} ({n}) — board.md"
        n += 1
    old = VAULT / old_rel
    if old.exists():
        old.rename(VAULT / new_rel)
    return new_rel


# The engines a thread member can run on: the broker's wired providers
# (Antigravity joined 2026-11-09).
ENGINES = ("claude-code", "codex", "antigravity")


def default_engine(persona: str) -> str:
    """The engine a member starts on: the persona's own ``provider`` in its
    agent.json if it names one, otherwise Claude Code."""
    try:
        card = json.loads((AI_LABS / persona / "agent.json").read_text("utf-8"))
        if card.get("provider") in ENGINES:
            return card["provider"]
    except (OSError, json.JSONDecodeError):
        pass
    return "claude-code"


def persona_defaults(persona: str) -> dict:
    """The persona's own defaults from its agent.json (2026-11-13, Settings >
    Agents > its profile): ``{"provider", "model", "effort"}``, the provider
    Claude Code when it names none and the others null. A turn takes the
    model and effort when its member is on that provider and the thread has
    chosen no model for it (lib/loam_thread_engine.py, ``run_turn``)."""
    try:
        card = json.loads((AI_LABS / persona / "agent.json").read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        card = {}
    if not isinstance(card, dict):
        card = {}
    provider = card.get("provider") if card.get("provider") in ENGINES else "claude-code"
    pick = (lambda k: card[k] if isinstance(card.get(k), str) and card[k].strip()
            else None)
    return {"provider": provider, "model": pick("model"), "effort": pick("effort")}


def create(persona: str | None = None, title: str | None = None, *,
           thread_id: str | None = None, members: list[str] | None = None,
           pinned: bool = False, extra: dict | None = None) -> dict:
    """A new thread. With no ``persona`` (2026-11-09) it has no default and
    no members: what is written untagged is recorded and nobody answers,
    until a default is set or someone is tagged. ``extra`` is frontmatter
    written with it (a branch's ``branched_*`` keys)."""
    if persona is not None and persona not in roster():
        raise ThreadError(404, f"no persona named {persona!r}")
    thread_id = thread_id or "t-" + secrets.token_hex(4)
    members = members or ([persona] if persona else [])
    placeholder = not title
    title = clean_title(title or (f"New thread with {display(persona)}"
                                  if persona else "New thread"))
    meta = {"id": thread_id, "title": title, "default": persona,
            "icon": None, "archived": False, "models": {},
            "members": members,
            "engines": {m: default_engine(m) for m in members},
            "board": board_rel(thread_id, title), "pinned": pinned,
            "created": now_iso(), **(extra or {})}
    with locked(VAULT):
        path = (VAULT / CONVERSATIONS / f"{GENERAL_TITLE}.md"
                if thread_id == GENERAL_ID else _unique_path(title))
        if thread_id == GENERAL_ID and path.exists():
            raise ThreadError(409, "General exists")
        _write_atomic(path, render_front(meta))
        idx = index()
        idx[thread_id] = path.name
        save_state("index.json", idx)
        threads_state = load_state("threads.json")
        threads_state.setdefault(thread_id, {})["placeholder_title"] = placeholder
        save_state("threads.json", threads_state)
    return meta


def write_imported(meta: dict, msgs: list[dict]) -> Path:
    """A whole thread record written at once, its messages with the ids and
    times they are given (2026-11-11, for ``bin/import-classic-chats``).

    ``create`` and ``append`` stamp now; an imported chat keeps the times
    it was had. ``meta`` is ``create``'s shape plus ``imported_from`` and
    ``source``. The read mark is set at the last message, so a chat already
    read in the classic view does not arrive with all of it unread."""
    with locked(VAULT):
        path = _unique_path(meta["title"])
        meta = {**meta, "board": board_rel(meta["id"], meta["title"])}
        text = render_front(meta) + "".join(
            render_message(m, find(msgs, m.get("reply_to"))) for m in msgs)
        _write_atomic(path, text)
        idx = index()
        idx[meta["id"]] = path.name
        save_state("index.json", idx)
        data = load_state("threads.json")
        row = data.setdefault(meta["id"], {})
        row["placeholder_title"] = False
        row["imported"] = len(msgs)
        if msgs:
            row["seen"] = msgs[-1]["id"]
        save_state("threads.json", data)
        done = load_state("imported.json")
        done[meta["imported_from"]] = meta["id"]
        save_state("imported.json", done)
    return path


def append_imported(thread_id: str, msgs: list[dict]) -> list[dict] | None:
    """What a classic chat said after it was imported, added to its thread
    with the ids that follow and the times it was said -- only while the
    thread holds exactly what the import wrote: once anyone has written in
    it (the owner continuing it, a system line), it is a thread, and the chat
    it came from is no longer its record. None when it is not appended."""
    with locked(VAULT):
        meta, have, path = read(thread_id)
        row = load_state("threads.json").get(thread_id, {})
        if row.get("imported") != len(have) or not meta.get("archived"):
            return None
        last = max((_num(m.get("id")) for m in have), default=0)
        out = [{**m, "id": f"m{last + i}"} for i, m in enumerate(msgs, 1)]
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("".join(render_message(m, find(have + out, m.get("reply_to")))
                             for m in out))
        _CACHE.pop(str(path), None)
        data = load_state("threads.json")
        data.setdefault(thread_id, {})["imported"] = len(have) + len(out)
        if out and data[thread_id].get("seen") == (have[-1]["id"] if have else None):
            data[thread_id]["seen"] = out[-1]["id"]
        save_state("threads.json", data)
    return out


def imported() -> dict[str, str]:
    """Classic session id -> the thread imported from it: the records' own
    frontmatter, over ``state/threads/imported.json``, which the import
    writes too. Either alone would do; both, because a broker older than
    this code rewrites a record's frontmatter without ``imported_from``
    when the thread is changed, and the state file can be lost. A thread
    since deleted is in neither."""
    idx = index()
    out: dict[str, str] = {sid: tid for sid, tid in load_state("imported.json").items()
                           if tid in idx}
    for thread_id, name in idx.items():
        try:
            with open(VAULT / CONVERSATIONS / name, encoding="utf-8") as fh:
                head = fh.read(4096)
        except OSError:
            continue
        if "imported_from:" not in head:
            continue
        meta, _ = split_front(head if "\n---\n" in head[4:] else head + "\n---\n")
        if meta.get("imported_from"):
            out[meta["imported_from"]] = thread_id
    return out


def ensure_general() -> None:
    try:
        record_path(GENERAL_ID)
        meta, _, _ = read(GENERAL_ID)
        missing = [p for p in roster() if p not in meta["members"]]
        if missing:
            update(GENERAL_ID, add=missing)
    except ThreadError:
        people = roster()
        if people:
            default = lc.LEAD_AGENT if lc.LEAD_AGENT in people else people[0]
            create(default, GENERAL_TITLE, thread_id=GENERAL_ID,
                   members=people, pinned=True)


def _rewrite(path: Path, meta: dict) -> None:
    _, body = split_front(path.read_text("utf-8"))
    _write_atomic(path, render_front(meta) + body)


def append(thread_id: str, author: str, text: str, kind: str = "message",
           did: list[dict] | None = None, **extra) -> dict:
    if kind not in KINDS:
        raise ThreadError(400, f"`kind` is one of {', '.join(KINDS)}")
    if not extra.get("reply_to"):
        extra.pop("reply_to", None)
    with locked(VAULT):
        meta, msgs, path = read(thread_id)
        # Checked under the lock, against the record as written: a reply to
        # a message of another thread, or to none, is refused (2026-11-11).
        quoted = (check_reply_to(msgs, extra["reply_to"], thread_id)
                  if "reply_to" in extra else None)
        last = max((int(m["id"][1:]) for m in msgs
                    if str(m.get("id", "")).startswith("m")
                    and m["id"][1:].isdigit()), default=0)
        msg = {"id": f"m{last + 1}", "author": author, "at": now_iso(),
               "kind": kind, "text": text.strip(), "did": did or [], **extra}
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(render_message(msg, quoted))
    return msg


def replace_message(thread_id: str, msg_id: str, new: list[dict]) -> list[dict]:
    """Put ``new`` where message ``msg_id`` is, touching no other message's
    bytes (2026-11-10, for ``bin/backfill-thread-steps --force-placeholder``).

    The first of ``new`` keeps ``msg_id``; the rest take the next free ids,
    in order, and sit after it. An empty ``new`` removes the message. Only
    that message's block of the file is rewritten: the rest is copied as it
    is, not parsed and rendered again."""
    with locked(VAULT):
        _, msgs, path = read(thread_id)
        if not any(m.get("id") == msg_id for m in msgs):
            raise ThreadError(404, f"no message {msg_id!r} in {thread_id}")
        last = max((_num(m.get("id")) for m in msgs), default=0)
        out = []
        for i, msg in enumerate(new):
            mid = msg_id if i == 0 else f"m{last + i}"
            out.append({"kind": "message", "did": [], **msg, "id": mid})
        raw = path.read_text("utf-8")
        lines = raw.split("\n")
        offsets, at = [], 0
        for line in lines:
            offsets.append(at)
            at += len(line) + 1
        heads = [i for i in range(len(lines) - 1) if lines[i].startswith("### ")
                 and META_RE.match(lines[i + 1])]
        mine = [i for i in heads if json.loads(
            META_RE.match(lines[i + 1]).group(1)).get("id") == msg_id]
        if len(mine) != 1:
            raise ThreadError(409, f"{msg_id} is in {thread_id} {len(mine)} times")
        later = [i for i in heads if i > mine[0]]
        # A block runs from the newline its render opens with to the one the
        # next block opens with (`render_message`).
        start = offsets[mine[0]] - 1
        end = offsets[later[0]] - 1 if later else len(raw)
        text = raw[:start] + "".join(render_message(m, find(msgs, m.get("reply_to")))
                                     for m in out) + raw[end:]
        _write_atomic(path, text)
        # The read cache keys on (mtime, size), and a same-size rewrite in
        # the same clock tick would read as unchanged.
        _CACHE.pop(str(path), None)
    return out


_UNSET = object()


def update(thread_id: str, *, title: str | None = None, default=_UNSET,
           add: list[str] | None = None, remove: list[str] | None = None,
           engines: dict | None = None, models: dict | None = None,
           icon=_UNSET, pinned: bool | None = None,
           archived: bool | None = None) -> dict:
    """Change a thread's frontmatter. ``default=None`` clears the default
    (nobody answers untagged messages); ``models`` maps a member to a model
    or None (the persona's own), checked by the caller against what its
    engine lists. General keeps its name, its pin and its place: renaming or
    archiving it is refused (2026-11-09)."""
    people = roster()
    general = thread_id == GENERAL_ID
    if general and title is not None:
        raise ThreadError(400, "General can't be renamed")
    if general and archived:
        raise ThreadError(400, "General can't be archived")
    if general and pinned is False:
        raise ThreadError(400, "General is always pinned")
    with locked(VAULT):
        meta, _, path = read(thread_id)
        for p in (add or []):
            if p not in people:
                raise ThreadError(404, f"no persona named {p!r}")
            if p not in meta["members"]:
                meta["members"].append(p)
                meta["engines"].setdefault(p, default_engine(p))
        keep = meta["default"] if default is _UNSET else default
        for p in (remove or []):
            if p in meta["members"] and p != keep:
                meta["members"].remove(p)
                meta["engines"].pop(p, None)
                meta["models"].pop(p, None)
        if default is not _UNSET:
            if default is not None and default not in people:
                raise ThreadError(404, f"no persona named {default!r}")
            meta["default"] = default
            if default is not None and default not in meta["members"]:
                meta["members"].append(default)
                meta["engines"].setdefault(default, default_engine(default))
        for p, engine in (engines or {}).items():
            if engine not in ENGINES:
                raise ThreadError(400, f"engine {engine!r}: one of {', '.join(ENGINES)}")
            if p not in meta["members"]:
                raise ThreadError(400, f"{p} is not a member of this thread")
            if meta["engines"].get(p) != engine:
                # A model is an engine's; a switch goes back to the persona's.
                meta["models"].pop(p, None)
            meta["engines"][p] = engine
        for p, model in (models or {}).items():
            if p not in meta["members"]:
                raise ThreadError(400, f"{p} is not a member of this thread")
            if model is None:
                meta["models"].pop(p, None)
            else:
                meta["models"][p] = model
        if icon is not _UNSET:
            if icon is not None and (not isinstance(icon, str) or len(icon) > 16):
                raise ThreadError(400, "`icon` is a short string (an emoji) or null")
            meta["icon"] = icon or None
        if pinned is not None:
            meta["pinned"] = bool(pinned)
        if archived is not None:
            meta["archived"] = bool(archived)
        if title is not None:
            new = clean_title(title)
            if new != meta["title"]:
                meta["title"] = new
                meta["board"] = _rename_board(thread_id, meta["board"], new)
                target = _unique_path(new, own=path)
                _rewrite(path, meta)
                if target != path:
                    path.rename(target)
                idx = index()
                idx[thread_id] = target.name
                save_state("index.json", idx)
                ts = load_state("threads.json")
                ts.setdefault(thread_id, {})["placeholder_title"] = False
                save_state("threads.json", ts)
                return meta
        _rewrite(path, meta)
    return meta


def delete(thread_id: str) -> dict:
    """Remove a thread's record and its board from the vault, and its
    runtime state (2026-11-09). The owner's explicit action only: the app
    confirms first. General is never deleted. Returns what was removed."""
    if thread_id == GENERAL_ID:
        raise ThreadError(400, "General can't be deleted")
    with locked(VAULT):
        meta, _, path = read(thread_id)
        board = VAULT / meta["board"]
        removed = [str(path.relative_to(VAULT))]
        path.unlink()
        if board.is_file() and board.parent == VAULT / BOARDS:
            board.unlink()
            removed.append(meta["board"])
        _CACHE.pop(str(path), None)
        idx = index()
        idx.pop(thread_id, None)
        save_state("index.json", idx)
        for name in ("threads.json", "sessions.json"):
            data = load_state(name)
            if data.pop(thread_id, None) is not None:
                save_state(name, data)
    return {"thread": thread_id, "removed": removed}


def title_from_first(thread_id: str, text: str) -> None:
    """A thread started from a persona is named by its first message."""
    ts = load_state("threads.json").get(thread_id, {})
    if ts.get("placeholder_title") and thread_id != GENERAL_ID:
        line = " ".join((text or "").split())
        if len(line) > 60:
            line = (line[:60].rsplit(" ", 1)[0] or line[:60]) + "…"
        if line:
            update(thread_id, title=line)


# -- branching (2026-11-11) ---------------------------------------------------
#
# A conversation branched off at one message into a thread of its own: the
# new thread's first line links back to that message, the source gets a line
# under it linking forward, and the persona's first turn in the branch is
# handed the window around that message, not the whole source thread.

# The window ending at the chosen message: back to the last real pause (a
# gap over BRANCH_PAUSE_S between two messages), or BRANCH_MAX messages,
# whichever comes first, and never fewer than BRANCH_MIN when the thread has
# them. System lines are not part of it.
BRANCH_PAUSE_S = 30 * 60
BRANCH_MAX = 12
BRANCH_MIN = 3
# How many words the summarizer's draft may run to.
BRANCH_DRAFT_WORDS = 90


def _when(msg: dict) -> datetime | None:
    try:
        return datetime.strptime(msg.get("at") or "", "%Y-%m-%dT%H:%M:%SZ") \
            .replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def branch_window(msgs: list[dict], msg_id: str, thread_id: str = "") -> list[dict]:
    """The messages a branch at ``msg_id`` carries, in record order, ending
    with it. Walking back from it, a message joins while the window has
    fewer than BRANCH_MAX and either the window has fewer than BRANCH_MIN or
    no gap over BRANCH_PAUSE_S lies between it and the chosen message; a
    pause crossed to reach BRANCH_MIN tops the window up to that minimum and
    no further. Deterministic:
    the record's own times and order, nothing else. A message the thread
    does not hold is a 404, a system line a 400."""
    if not isinstance(msg_id, str) or not re.fullmatch(r"m\d+", msg_id):
        raise ThreadError(400, "`message` is a message id like m12")
    at = next((i for i, m in enumerate(msgs) if m.get("id") == msg_id), None)
    if at is None:
        raise ThreadError(404, f"no message {msg_id!r} in {thread_id or 'this thread'}")
    if msgs[at].get("kind") == "system":
        raise ThreadError(400, "a branch starts from a message, not a system line")
    earlier = [m for m in msgs[:at] if m.get("kind") != "system"]
    out = [msgs[at]]
    paused = False
    for m in reversed(earlier):
        if len(out) >= BRANCH_MAX:
            break
        later, this = _when(out[0]), _when(m)
        paused = paused or (later is not None and this is not None
                            and (later - this).total_seconds() > BRANCH_PAUSE_S)
        # Past a pause, only what the minimum still needs.
        if paused and len(out) >= BRANCH_MIN:
            break
        out.insert(0, m)
    return out


def branch_people(meta: dict) -> list[str]:
    """Who a branch can be started with, in the sheet's order: the source
    thread's members (its default first), then everyone else."""
    people = roster()
    first = ([meta["default"]] if meta.get("default") in people else []) + [
        p for p in meta.get("members") or [] if p in people and p != meta.get("default")]
    return first + [p for p in people if p not in first]


def _excerpt(window: list[dict]) -> str:
    out = []
    for m in window:
        who = "Loam" if m.get("author") == "system" else display(m["author"])
        out.append(f"[{local_stamp(m.get('at', ''))}] {who} ({m.get('id')}):\n"
                   f"{_said(m)}")
    return "\n\n".join(out)


def branch_prompt(meta: dict, window: list[dict], persona: str | None) -> dict:
    """What the summarizer is asked, so every caller asks the same thing:
    ``system_instruction`` and ``input`` for one text call, whose answer is
    one JSON object ``{"title", "draft"}``. The app sends it to the model it
    already writes read-aloud briefs with (2026-11-11)."""
    to = display(persona) if persona else "the agent"
    instruction = "\n".join([
        f"{lc.OWNER_NAME} is branching part of a conversation off into a new thread of "
        f"its own, with {to}. You write the opening message of that new "
        f"thread, in {lc.OWNER_NAME}'s own voice: first person, plain, the way he "
        "types to a colleague.",
        "The excerpt is the stretch of the old thread that led up to the "
        "message he branched from, which is the last one. That last message "
        "is what the new thread is about.",
        "Write one line saying what this thread is about. Then the essential "
        "context from the excerpt in a few short sentences: what was found "
        "or decided, and the specific names, files, errors and numbers it "
        "needs. Leave out anything the new thread does not need.",
        "End with a final line that is left open for him to finish in his "
        "own words, for example one ending with a colon. Do not answer it.",
        f"At most {BRANCH_DRAFT_WORDS} words. No greeting, no sign-off, no "
        "@mentions, and nothing the excerpt does not say.",
        "Also give the thread a title of three to seven words, with no "
        "quotation marks and no full stop.",
        'Answer with only a JSON object: {"title": "...", "draft": "..."}.',
    ])
    text = (f"The old thread is called \"{meta.get('title')}\".\n"
            f"The new thread is with {to}.\n\nThe excerpt:\n\n{_excerpt(window)}")
    return {"system_instruction": instruction, "input": text,
            "answer": {"title": "string", "draft": "string"}}


def fallback_branch(meta: dict, window: list[dict]) -> dict:
    """A plain title and draft from the record alone, for a caller whose
    summarizer failed or that has none: the source's title and the chosen
    message's opening, and an open line."""
    said = " ".join((_said(window[-1]) or "").split())
    if len(said) > 160:
        said = (said[:160].rsplit(" ", 1)[0] or said[:160]) + "…"
    who = display(window[-1]["author"]) if not lc.is_owner(window[-1].get("author")) else "I"
    lead = (f'Branching off "{meta.get("title")}", where {who} said: "{said}"'
            if who != "I" else f'Branching off "{meta.get("title")}", where I said: "{said}"')
    title = f"From {meta.get('title')}"
    return {"title": clean_title(title)[:80], "draft": f"{lead}\n\n"}


def draft_for(thread_id: str) -> str | None:
    """The opening a thread's composer starts with, if it has one: a
    convenience kept beside the record (``state/threads/threads.json``),
    like the placeholder-title flag, not in it."""
    got = load_state("threads.json").get(thread_id, {}).get("draft")
    return got if isinstance(got, str) and got.strip() else None


def set_draft(thread_id: str, text: str | None) -> None:
    with locked(VAULT):
        data = load_state("threads.json")
        row = data.setdefault(thread_id, {})
        if text and text.strip():
            row["draft"] = text
        elif "draft" in row:
            row.pop("draft")
        else:
            return
        save_state("threads.json", data)


def insert_after(thread_id: str, msg_id: str, author: str, text: str,
                 kind: str = "system", **extra) -> dict:
    """A message placed under ``msg_id`` instead of after the last one, its
    id the next free one and ``after: msg_id`` in its comment, touching no
    other message's bytes. Lines already placed under the same message stay
    above it, so two branches from one message read in the order made."""
    if kind not in KINDS:
        raise ThreadError(400, f"`kind` is one of {', '.join(KINDS)}")
    with locked(VAULT):
        _, msgs, path = read(thread_id)
        at = next((i for i, m in enumerate(msgs) if m.get("id") == msg_id), None)
        if at is None:
            raise ThreadError(404, f"no message {msg_id!r} in {thread_id}")
        nxt = at + 1
        while nxt < len(msgs) and msgs[nxt].get("after") == msg_id:
            nxt += 1
        last = max((_num(m.get("id")) for m in msgs), default=0)
        msg = {"id": f"m{last + 1}", "author": author, "at": now_iso(),
               "kind": kind, "text": text.strip(), "did": [], **extra,
               "after": msg_id}
        raw = path.read_text("utf-8")
        if nxt >= len(msgs):
            out = raw.rstrip("\n") + "\n" + render_message(msg)
        else:
            want = msgs[nxt].get("id")
            lines = raw.split("\n")
            offsets, pos = [], 0
            for line in lines:
                offsets.append(pos)
                pos += len(line) + 1
            heads = [i for i in range(len(lines) - 1) if lines[i].startswith("### ")
                     and META_RE.match(lines[i + 1])
                     and json.loads(META_RE.match(lines[i + 1]).group(1)).get("id") == want]
            if len(heads) != 1:
                raise ThreadError(409, f"{want} is in {thread_id} {len(heads)} times")
            # A block opens with the newline before its heading
            # (`render_message`), so the new one goes in just before it.
            cut = offsets[heads[0]] - 1
            out = raw[:cut] + render_message(msg) + raw[cut:]
        _write_atomic(path, out)
        _CACHE.pop(str(path), None)
    return msg


def branch(source_id: str, msg_id: str, persona: str | None = None, *,
           title: str | None = None, draft: str | None = None,
           by: str | None = None) -> dict:
    """Branch ``source_id`` at ``msg_id`` into a new thread with ``persona``
    (the source's default when not given) as its default and only member.

    The new thread is titled ``title`` (or by the draft's first line, or
    after its source), holds ``draft`` as its composer's opening, and records
    where it came from (``branched_*``). Its first line is a system line
    linking back to the message; the source gets one under that message
    linking forward. ``by`` is the persona that branched, when it was not
    the owner. Returns ``{"meta", "from", "into", "window"}``."""
    smeta, smsgs, spath = read(source_id)
    window = branch_window(smsgs, msg_id, source_id)
    persona = persona or smeta.get("default")
    if not persona:
        raise ThreadError(400, "who is the new thread with? pass `persona`")
    if persona not in roster():
        raise ThreadError(404, f"no persona named {persona!r}")
    first_line = next((l.strip() for l in (draft or "").splitlines() if l.strip()), "")
    if len(first_line) > 60:
        first_line = (first_line[:60].rsplit(" ", 1)[0] or first_line[:60]) + "…"
    name = clean_title(title or first_line or fallback_branch(smeta, window)["title"])
    meta = create(persona, name, extra={
        "branched_from": source_id, "branched_message": msg_id,
        "branched_window": [m["id"] for m in window]})
    who = display(by) if by and not lc.is_owner(by) else None
    source_title = smeta["title"]
    back = append(meta["id"], "system",
                  f"{who} branched this from {source_title}" if who
                  else f"Branched from {source_title}",
                  kind="system",
                  link={"thread": source_id, "message": msg_id, "title": source_title})
    into = insert_after(source_id, msg_id, "system",
                        f"{who} branched into {meta['title']}" if who
                        else f"Branched into {meta['title']}",
                        link={"thread": meta["id"], "title": meta["title"]})
    if draft and draft.strip():
        set_draft(meta["id"], draft)
    # Branching is the owner (or a persona) reading the source; the line under
    # the message is not news to them (2026-11-11): when the source was read
    # through its last message, it stays read.
    seen = load_state("threads.json").get(source_id, {}).get("seen")
    last_before = max((_num(m.get("id")) for m in smsgs), default=0)
    if seen and _num(seen) >= last_before:
        mark_seen(source_id, into["id"])
    return {"meta": meta, "from": back, "into": into,
            "window": [m["id"] for m in window], "record": str(spath)}


def branch_context(meta: dict) -> str:
    """What a persona's fresh session in a branch is handed before the
    branch's own messages: where it came from, the window's messages in
    full, and the source file for anything earlier. Empty for a thread that
    is not a branch."""
    source = meta.get("branched_from")
    if not source:
        return ""
    try:
        smeta, smsgs, spath = read(source)
    except ThreadError:
        return (f"This thread was branched from thread {source} at its message "
                f"{meta.get('branched_message')}, which no longer exists.\n\n")
    ids = set(meta.get("branched_window") or [])
    window = [m for m in smsgs if m.get("id") in ids]
    if not window:
        try:
            window = branch_window(smsgs, meta.get("branched_message") or "", source)
        except ThreadError:
            window = []
    return (f"This thread was branched from the thread \"{smeta['title']}\" "
            f"(id {source}) at its message {meta.get('branched_message')}. The "
            f"exchange it was branched from follows; anything earlier is in "
            f"the thread file {spath}, to read when you need it.\n\n"
            f"{transcript(window, smsgs, str(spath))}\n\n"
            f"This thread so far:\n\n")


# -- sessions and the read mark ------------------------------------------------

def session_for(thread_id: str, persona: str) -> dict | None:
    return load_state("sessions.json").get(thread_id, {}).get(persona)


def remember_session(thread_id: str, persona: str, session_id: str,
                     engine: str, through: str, seq: int | None = None,
                     context: dict | None = None) -> None:
    """``seq`` (2026-11-10) is how far into the broker's session log the
    thread has recorded; the next turn posts any reply past it that nobody
    was watching (``ThreadEngine.recover_late``). ``context`` (2026-11-11)
    is the session's last ``{used, window}``, what the next turn's rotation
    reads when the session is no longer resident."""
    with locked(VAULT):
        data = load_state("sessions.json")
        row = {"session_id": session_id, "engine": engine, "through": through}
        if isinstance(seq, int):
            row["seq"] = seq
        if isinstance(context, dict) and isinstance(context.get("used"), int):
            row["context"] = {"used": context["used"], "window": context.get("window")}
        data.setdefault(thread_id, {})[persona] = row
        save_state("sessions.json", data)


def mark_seen(thread_id: str, msg_id: str) -> None:
    with locked(VAULT):
        data = load_state("threads.json")
        data.setdefault(thread_id, {})["seen"] = msg_id
        save_state("threads.json", data)


def is_message_id(reason) -> bool:
    """A thread message id (``m12``), as a turn's ``reason`` is when a
    message made it; not ``notification`` or ``job:<id>``."""
    return isinstance(reason, str) and len(reason) > 1 and reason[0] == "m" \
        and reason[1:].isdigit()


def _num(msg_id: str | None) -> int:
    try:
        return int(str(msg_id)[1:])
    except ValueError:
        return 0


def unread(thread_id: str, msgs: list[dict]) -> int:
    seen = _num(load_state("threads.json").get(thread_id, {}).get("seen", "m0"))
    return sum(1 for m in msgs if _num(m.get("id")) > seen
               and not lc.is_owner(m.get("author")))


# -- boards --------------------------------------------------------------------

def board_text(meta: dict, create_missing: bool = True) -> str:
    path = VAULT / meta["board"]
    if not path.exists():
        if not create_missing:
            return ""
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(path, f"# {meta['title']} — board\n\n")
    return path.read_text("utf-8")


def write_board(meta: dict, markdown: str) -> None:
    if not isinstance(markdown, str):
        raise ThreadError(400, "`markdown` is a string")
    _write_atomic(VAULT / meta["board"], markdown)


def item_meta(line: str) -> list[str]:
    match = ITEM_META_RE.search(line)
    if not match or not line.lstrip().startswith(("- ", "* ")):
        return []
    return [part.strip() for part in match.group(1).split("·")]


def board_new(markdown: str) -> int:
    return sum(1 for line in markdown.splitlines() if "new" in item_meta(line))


def sweep_board(markdown: str, today: date) -> tuple[str, list[str]]:
    """Remove every item whose ``until`` day has passed. The ``until`` day is
    the last day it stays, so an item until today is still here today."""
    kept, gone = [], []
    lines = markdown.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        expired = False
        for part in item_meta(line):
            match = UNTIL_RE.search(part)
            if match:
                try:
                    expired = date.fromisoformat(match.group(1)) < today
                except ValueError:
                    expired = False
        if expired:
            gone.append(line)
            indent = len(line) - len(line.lstrip())
            i += 1
            # An item's own continuation lines and children go with it.
            while i < len(lines) and lines[i].strip() and (
                    len(lines[i]) - len(lines[i].lstrip()) > indent):
                gone.append(lines[i])
                i += 1
            continue
        kept.append(line)
        i += 1
    return "\n".join(kept), gone


def board_files() -> list[Path]:
    files = sorted((VAULT / BOARDS).glob("*.md")) if (VAULT / BOARDS).is_dir() else []
    general = VAULT / GENERAL_BOARD
    if general.is_file():
        files.append(general)
    return files


# -- the list -------------------------------------------------------------------

def last_spoke(msgs: list[dict]) -> dict[str, str]:
    """When each author last posted a message or a wake bulletin; a system
    line about a persona (a failed turn, a join) is not that persona
    speaking."""
    out: dict[str, str] = {}
    for m in msgs:
        if m.get("kind") in ("message", "bulletin") and m.get("at"):
            out[m["author"]] = m["at"]
    return out


def summary(thread_id: str, running=None) -> dict:
    """One thread's row. ``running`` is the member whose turn is in flight,
    or a list of them (members answer in parallel since 2026-11-10), which
    only the broker's engine knows. Each member carries
    ``last_spoke_at`` (null if never) and ``running`` (2026-11-09), so a
    header can draw who is actually in the room."""
    meta, msgs, _ = read(thread_id)
    live = {running} if isinstance(running, str) else set(running or ())
    shown = [m for m in msgs if m.get("kind") != "system"] or msgs
    last = shown[-1] if shown else None
    board = board_text(meta, create_missing=False)
    spoke = last_spoke(msgs)
    return {
        "id": meta["id"], "title": meta["title"], "default": meta["default"],
        "icon": meta.get("icon"),
        # `model`: the member's own choice for this thread, null for the
        # persona's default (2026-11-09). `busy` is `running`'s other name,
        # the one the app reads.
        "members": [{"persona": p, "engine": meta["engines"].get(p, default_engine(p)),
                     "model": (meta.get("models") or {}).get(p),
                     "last_spoke_at": spoke.get(p), "running": p in live,
                     "busy": p in live}
                    for p in meta["members"]],
        "last": ({"author": last["author"], "text": (last["text"] or "")[:200],
                  "at": last["at"]} if last else None),
        "unread": unread(thread_id, msgs),
        "board_new": board_new(board),
        "board": meta["board"],
        "pinned": meta["id"] == GENERAL_ID or bool(meta.get("pinned")),
        "archived": bool(meta.get("archived")) and meta["id"] != GENERAL_ID,
        "general": meta["id"] == GENERAL_ID,
        "created": meta.get("created"),
        # The classic chat this thread was imported from, and its transcript
        # (2026-11-11); null for every thread made as one.
        "imported_from": meta.get("imported_from"),
        "source": meta.get("source"),
        # A branch (2026-11-11): where it came from, null for every other
        # thread; `draft` the opening its composer starts with until the owner
        # sends a message or clears it.
        "branched_from": ({"thread": meta["branched_from"],
                           "message": meta.get("branched_message")}
                          if meta.get("branched_from") else None),
        "draft": draft_for(thread_id),
        # The hello thread (2026-11-14, first release L11): the one a blank
        # install starts with, where the page lands the person first.
        "hello": bool(meta.get("hello")),
    }


def listing(running: dict | None = None,
            archived: bool = False) -> list[dict]:
    """Every thread's row; ``running`` maps a thread id to the member (or
    the list of members) whose turn is in flight there. Archived threads only with ``archived``
    (`?archived=1`), and then beside the rest, as the sessions list does."""
    rows = []
    for thread_id in list(index()):
        try:
            row = summary(thread_id, (running or {}).get(thread_id))
        except (ThreadError, OSError, KeyError):
            continue
        if row["archived"] and not archived:
            continue
        rows.append(row)
    rows.sort(key=lambda r: (r["last"] or {}).get("at") or r.get("created") or "",
              reverse=True)
    rows.sort(key=lambda r: (not r["pinned"], not (r["pinned"] and r["id"] == GENERAL_ID)))
    return rows


def page(thread_id: str, before: str | None = None, limit: int = PAGE) -> dict:
    """The last ``limit`` messages, in record order, of those earlier in the
    record than ``before`` (all of them without it).

    ``before`` is a position in the record, not a number (2026-11-10): a
    recovered late reply is appended where it was recovered, so m17 can sit
    between m10 and m11, and a cut on ``id < before`` paged it above m3. An
    id the record does not hold falls back to the number cut."""
    meta, msgs, _ = read(thread_id)
    if before:
        at = next((i for i, m in enumerate(msgs) if m.get("id") == before), None)
        if at is not None:
            msgs = msgs[:at]
        else:
            cut = _num(before)
            msgs = [m for m in msgs if _num(m.get("id")) < cut]
    window = msgs[-limit:] if limit else msgs
    return {"meta": meta, "messages": window,
            "has_more": len(msgs) > len(window)}


# The owner's clock, which is the box's (lib/loam_clock.py). A thread turn is
# told the time in it (2026-11-09).
PACIFIC = loam_clock.PACIFIC
local_stamp = loam_clock.local_stamp


def clock_line(now: datetime | None = None) -> str:
    """The sentence every thread turn opens with: the weekday, the date and
    the time in America/Los_Angeles."""
    return (f"{loam_clock.sentence(now)} Message times below are in the "
            f"same zone.")


def _said(m: dict) -> str:
    # What a reply said before its steps is part of what it said
    # (`segments`, 2026-11-09).
    said = [seg["text"].strip() for seg in (m.get("segments") or [])
            if isinstance(seg, dict) and isinstance(seg.get("text"), str)
            and seg["text"].strip()]
    return "\n\n".join(said + [m["text"]]) if said else m["text"]


def replied_block(m: dict, record: list[dict] | None,
                  record_file: str | None = None) -> str:
    """The message ``m`` replies to, as the prompt shows it right above
    ``m``'s own words (2026-11-11): who said it, when, its id, and its whole
    text -- or its first PROMPT_QUOTE_CHARS characters and where the rest
    is. Shown whether or not the referenced message is also in the window."""
    if not m.get("reply_to"):
        return ""
    who = display(m["author"])
    target = find(record or [], m["reply_to"])
    if target is None:
        return (f"{who} is replying to message {m['reply_to']}, which is no "
                f"longer in the thread.\n")
    by = "Loam" if target.get("author") == "system" else display(target["author"])
    full = _said(target) or ""
    shown = full
    if len(full) > PROMPT_QUOTE_CHARS:
        where = f"the thread file {record_file}" if record_file else "the thread file"
        shown = (full[:PROMPT_QUOTE_CHARS].rstrip() + "\n[… cut at "
                 f"{PROMPT_QUOTE_CHARS} of {len(full)} characters; the full "
                 f"text is in {where} at id {target.get('id')}]")
    quoted = "\n".join("> " + line if line else ">" for line in shown.split("\n"))
    return (f"{who} is replying to this earlier message, {by}'s "
            f"{target.get('id')} of [{local_stamp(target.get('at', ''))}]:\n"
            f"{quoted}\n"
            f"{who}'s words in reply:\n")


def transcript(msgs: list[dict], record: list[dict] | None = None,
               record_file: str | None = None) -> str:
    """Messages as one persona reads them in its next turn's prompt, each
    with its id (2026-11-11: what a reply names). ``record`` is the whole
    thread, where a message's ``reply_to`` is looked up; without it, the
    window itself."""
    record = msgs if record is None else record
    out = []
    for m in msgs:
        who = "Loam" if m["author"] == "system" else display(m["author"])
        tag = " (wake bulletin)" if m.get("kind") == "bulletin" else ""
        if m.get("preset"):
            # Posted by Loam in this agent's name before any turn (the hello
            # thread's opening, lib/loam_starter.py): said as its own, written
            # by nobody's model.
            tag += (" (preset: Loam posted this opening in your name before "
                    "your first turn; carry on from it as yours)")
        # The files a message carried are named by path, which is what a
        # persona can read.
        body = _said(m)
        files = [f"- {attachment_path(a)}" for a in (m.get("attachments") or [])
                 if isinstance(a, dict) and a.get("path")]
        if files:
            body += "\nAttached (files on disk):\n" + "\n".join(files)
        ident = f" ({m['id']})" if m.get("id") else ""
        out.append(f"[{local_stamp(m['at'])}] {who}{ident}{tag}:\n"
                   f"{replied_block(m, record, record_file)}{body}")
    return "\n\n".join(out)


# -- what a persona carries into a turn (2026-11-11) ---------------------------
#
# The owner, 2026-11-11: "it doesn't make sense for the entire history of the
# thread to be uploaded or added to the context ... if they ever need
# information from earlier on in the conversation, they can always go refer to
# the file." A turn is handed (a) the persona's own summary of the thread, (b)
# the recent exchange, (c) any message a message in it replies to, whole, and
# (d) the thread file's path. Nothing else from the thread.

# The recent exchange: from the newest message back to the last real pause --
# a gap of more than PAUSE_S between two consecutive messages in the record --
# or WINDOW_MAX messages, whichever comes first. Deterministic: a pure
# function of the record. (Not `branch_window`: that one ends at a chosen
# message, skips system lines and keeps a minimum; this one ends at the
# record's newest and is a turn's.) A gap that cannot be read (a time missing, or a
# recovered late reply out of time order) is not a pause.
PAUSE_S = 30 * 60
WINDOW_MAX = 20
# A summary longer than this is shown cut, with where the rest is: it is
# meant to be a few lines, and a runaway one must not become the long
# history this replaces.
SUMMARY_CHARS = 8000


def recent_window(msgs: list[dict], pause_s: int = PAUSE_S,
                  cap: int = WINDOW_MAX) -> list[dict]:
    """The newest messages of ``msgs`` back to the last pause of more than
    ``pause_s`` seconds, at most ``cap`` of them. The newest is always in."""
    if not msgs:
        return []
    start = len(msgs) - 1
    while start > 0 and len(msgs) - start < cap:
        later, earlier = _when(msgs[start]), _when(msgs[start - 1])
        if later and earlier and (later - earlier).total_seconds() > pause_s:
            break
        start -= 1
    return msgs[start:]


def summary_path(persona: str, thread_id: str) -> Path:
    """Where a persona keeps its own summary of a thread: in its own
    workspace, written by the persona and only ever read by the runtime."""
    return AI_LABS / persona / "threads" / f"{thread_id}.md"


def read_summary(persona: str, thread_id: str) -> str | None:
    """The persona's summary of the thread, or None when it has none yet (or
    it is empty). Read only: nothing here creates the file or its folder."""
    try:
        text = summary_path(persona, thread_id).read_text("utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return text or None


def turn_prompt(meta: dict, persona: str, msgs: list[dict], fresh: bool,
                now: datetime | None = None,
                record: list[dict] | None = None,
                summary: str | None = None,
                unshown: int = 0) -> str:
    """One turn's input. ``msgs`` is the window the engine chose (the recent
    exchange on a fresh session; on a resumed one, what in it is new to this
    persona); ``record`` the whole thread, where a ``reply_to`` is looked up;
    ``summary`` the persona's own summary, given on a fresh session only (a
    resumed session has it already, and keeps it itself); ``unshown`` how
    many messages new to a resumed session fell outside the window."""
    names = ", ".join(display(p) for p in meta["members"])
    answers = (f"{display(meta['default'])} answers when nobody is tagged"
               if meta.get("default") else
               "nobody answers when nobody is tagged; you were tagged")
    try:
        record_file = str(record_path(meta["id"]))
    except (ThreadError, KeyError, OSError):
        record_file = None
    mine = summary_path(persona, meta["id"])
    head = (f"[Loam thread \"{meta['title']}\" (id {meta['id']}). Members: {names}; "
            f"{answers}. "
            f"The board is {VAULT / meta['board']}.]\n"
            f"[{clock_line(now)}]\n")
    if fresh:
        head += ("This turn opens a new session of yours in this thread. What it "
                 "carries is small on purpose: your own summary of the thread, the "
                 "recent exchange, and where the rest is.\n\n")
        if summary is not None:
            shown = summary
            if len(shown) > SUMMARY_CHARS:
                shown = (shown[:SUMMARY_CHARS].rstrip() + f"\n[… cut at "
                         f"{SUMMARY_CHARS} of {len(summary)} characters; the rest "
                         f"is in {mine}]")
            head += (f"Your summary of this thread, as you last wrote it in {mine}:\n"
                     f"<your-summary>\n{shown}\n</your-summary>\n\n")
        else:
            head += (f"You have no summary of this thread yet ({mine} does not "
                     f"exist): write one before you end this turn.\n\n")
        # A branch's first turn (2026-11-11): the window it came from, and the
        # source file for the rest -- not the whole source thread. It ends
        # with its own heading for this thread's messages.
        branched = branch_context(meta)
        head += branched or (
            "The recent exchange (back to the last pause of more than "
            f"{PAUSE_S // 60} minutes, at most {WINDOW_MAX} messages):\n\n")
    else:
        head += "New in the thread since your last turn:\n\n"
        if unshown:
            head += (f"[{unshown} earlier message{'s' if unshown != 1 else ''} new "
                     f"to you {'are' if unshown != 1 else 'is'} not shown here: "
                     f"{'they are' if unshown != 1 else 'it is'} in the thread "
                     f"file.]\n\n")
    where = record_file or "the thread's file in 15_CONVERSATIONS"
    pointer = (f"\n\n[Earlier history is in the thread file, {where}: every "
               f"message under its id. Read it when you need something from "
               f"before what is shown here.]")
    tail = (f"\n\nYour summary of this thread is {mine}. It is yours: a few lines "
            "on what this thread is about from your perspective, the decisions "
            "made, what is open, and anything you want to remember. Loam only "
            "reads it, to hand it to your next fresh session here, and it is never "
            "shown in the app. Before you end this turn, update it if something "
            "material changed (make its folder if it is missing)."
            "\n\nWhatever you write now is posted to the thread as your message. "
            "Mention a peer with @Name only when you are sure it adds a lot. "
            "To show an image (at most three), put ![a few words](its path) "
            "on a line of its own: a file in the vault or the workspaces, "
            "and it shows in your message. "
            "To reply to one earlier message, begin with ↩ and its id "
            "(\"↩ m12\"); otherwise just write. Loam turns that opening into a "
            "quote of the message's own words and hides the id. Message ids "
            "are Loam's bookkeeping, never words for the reader: do not write "
            "one anywhere else in your reply. Name an earlier message by who "
            "said it and what it said (\"your point about subtlety\"), not by "
            "its id.")
    return head + transcript(msgs, record, record_file) + pointer + tail


def steer_prompt(meta: dict, persona: str, msgs: list[dict],
                 now: datetime | None = None,
                 record: list[dict] | None = None) -> str:
    """What a member's turn in flight is handed when messages for it arrive
    while it works (2026-11-14, ``ThreadEngine._steer``): only the new
    messages, said to be new mid-turn, with the thread named so a session
    with more than one thread in its history knows which. Its reply is
    still the turn's one message; the summary line of ``turn_prompt`` is
    not repeated."""
    try:
        record_file = str(record_path(meta["id"]))
    except (ThreadError, KeyError, OSError):
        record_file = None
    head = (f"[Loam thread \"{meta['title']}\" (id {meta['id']}): new while you "
            f"were working, delivered at this step. Take it into account "
            f"before your next step: change course if it asks you to, and "
            f"answer it in the reply this turn is writing.]\n"
            f"[{clock_line(now)}]\n\n")
    return head + transcript(msgs, record, record_file)
