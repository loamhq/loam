"""Read and write Markdown thread and board records. Opening a thread reads its
record. The member's provider session supplies a turn and can be resumed or replaced.

A thread's frontmatter identifies its default, members, engines and board::

    default: agent-a
    members: [agent-a, agent-b]
    engines: {agent-a: claude-code, agent-b: codex}

Each message starts with a heading and a metadata comment on the next line::

    ### Agent A · 2030-01-09 17:40
    <!-- loam {"id":"m1","author":"agent-a","at":"2030-01-09T17:40:00Z","kind":"message"} -->

    The message text.

Its text runs to the next heading and metadata pair. A board item's metadata
appears at the end of the item line. Runtime state holds file locations, member
sessions and read positions. Rebuild the index from the records. Keep this
module usable by bin/thread without the broker's environment.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import tempfile
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

import loam_config as lc

import loam_clock
import loam_file_revision

VAULT = Path(os.environ.get("LOAM_VAULT") or lc.VAULT)
AI_LABS = Path(os.environ.get("LOAM_AI_LABS") or lc.WORKSPACES)
STATE = Path(os.environ.get("LOAM_THREADS_STATE") or lc.STATE / "threads")

CONVERSATIONS = lc.vault_folder("conversations")
BOARDS = lc.vault_folder("boards")
GENERAL_ID = "general"
GENERAL_TITLE = "General"
GENERAL_BOARD = lc.vault_folder("general_board")

# Limit chains of agent mentions between owner messages. Report the limit in the
# thread.
AGENT_CHAIN_CAP = 3

# The page size of GET /v1/threads/<id>, newest first.
PAGE = 50

# Use is_owner() when comparing authors so older records with legacy owner IDs still
# match.
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
        # Prefer the display name from the agent profile, consistent with the broker's agent
        # cards.
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


# The owner can address every member with @all or @everyone. A persona's mention of
# either is ordinary text.
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
    """Write an empty default when untagged messages should receive no answer. Emit the
    icon, archive flag and model overrides only when set.
    """
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
    # Preserve the imported session ID and original transcript location on imported
    # threads.
    if meta.get("imported_from"):
        lines.append(f"imported_from: {meta['imported_from']}")
    if meta.get("source"):
        lines.append(f"source: {json.dumps(meta['source'], ensure_ascii=False)}")
    # Preserve the source thread, message and starting window on a branch.
    if meta.get("branched_from"):
        lines.append(f"branched_from: {meta['branched_from']}")
        lines.append(f"branched_message: {meta.get('branched_message') or ''}".rstrip())
        lines.append(f"branched_window: {_yaml_list(meta.get('branched_window') or [])}")
    # Mark the starter thread used for the first visit to a blank install.
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


# Store reply_to in metadata and a readable quote in the Markdown message.
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


# Hold a partial reply marker until the stream distinguishes it from ordinary text.
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
    """Remove a leading reply marker such as ↩ m12 from the displayed steps only when
    reply_to names the same message. Keep the stored record intact.
    """
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


# Keep inline image references in the message. The record writer saves copies of
# matching files and keeps the original destinations for placement in the app.
REPLY_IMAGES_MAX = 3
REPLY_IMAGE_BYTES = 40 * 1024 * 1024
REPLY_ATTACHMENTS = Path(lc.vault_folder("attachments")) / "loam-ui"
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".webp": "image/webp", ".gif": "image/gif", ".avif": "image/avif"}
IMAGE_REF_RE = re.compile(r"!\[([^\]\n]{0,300})\]\(\s*(<[^>\n]+>|[^)\n]+?)\s*\)")


def image_size(path: Path) -> tuple[int, int] | None:
    """``(width, height)`` from a PNG, GIF, JPEG or WebP header; None when the
    file does not say (AVIF, a damaged file). Reads the header, never the
    picture: a JPEG is walked marker by marker with seeks."""
    import struct  # noqa: PLC0415
    try:
        with path.open("rb") as f:
            head = f.read(32)
            if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
                w, h = struct.unpack(">II", head[16:24])
            elif head[:6] in (b"GIF87a", b"GIF89a"):
                w, h = struct.unpack("<HH", head[6:10])
            elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                kind = head[12:16]
                if kind == b"VP8X":
                    w = 1 + int.from_bytes(head[24:27], "little")
                    h = 1 + int.from_bytes(head[27:30], "little")
                elif kind == b"VP8 ":
                    f.seek(26)
                    w, h = (v & 0x3FFF for v in struct.unpack("<HH", f.read(4)))
                elif kind == b"VP8L":
                    f.seek(21)
                    b = f.read(4)
                    w = 1 + (((b[1] & 0x3F) << 8) | b[0])
                    h = 1 + (((b[3] & 0xF) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
                else:
                    return None
            elif head[:2] == b"\xff\xd8":
                f.seek(2)
                for _ in range(10_000):
                    marker = f.read(2)
                    while len(marker) == 2 and marker[0] == 0xFF and marker[1] == 0xFF:
                        marker = marker[1:] + f.read(1)       # fill bytes
                    if len(marker) < 2 or marker[0] != 0xFF:
                        return None
                    if marker[1] in (0xD8, 0x01) or 0xD0 <= marker[1] <= 0xD7:
                        continue                                # no length
                    size = f.read(2)
                    if len(size) < 2:
                        return None
                    length = struct.unpack(">H", size)[0]
                    if marker[1] in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                                     0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                        sof = f.read(5)
                        h, w = struct.unpack(">HH", sof[1:5])
                        break
                    f.seek(length - 2, 1)
                else:
                    return None
            else:
                return None
    except (OSError, struct.error, ValueError):
        return None
    return (w, h) if 0 < w <= 100_000 and 0 < h <= 100_000 else None


def _image_reference(raw: str) -> Path | None:
    from urllib.parse import unquote  # noqa: PLC0415
    raw = raw.strip()
    if raw.startswith("<") and raw.endswith(">"):
        raw = raw[1:-1].strip()
    elif re.search(r'\s+"[^"]*"$', raw):          # a Markdown title: ![a](p "t")
        raw = re.sub(r'\s+"[^"]*"$', "", raw)
    if re.match(r"^[a-z][a-z0-9+.-]*:", raw, re.I) and not raw.lower().startswith("file:"):
        return None                                 # a web address: not a file here
    raw = unquote(raw[7:] if raw.lower().startswith("file://") else raw)
    return VAULT / raw


def _image_location(path: Path) -> tuple[str, Path, list[str]] | None:
    """The allowed sources for an explicitly posted image."""
    import loam_files as files  # noqa: PLC0415
    if path.suffix.lower() not in IMAGE_TYPES:
        return None
    if path.is_relative_to(VAULT / CONVERSATIONS):
        return None
    if path.is_relative_to(VAULT):
        root, base = "vault", VAULT
    elif path.is_relative_to(AI_LABS):
        root, base = "spaces", AI_LABS
    elif path.is_relative_to(lc.JOBS):
        job = path.relative_to(lc.JOBS).parts
        if len(job) < 3 or job[1] != "captures":
            return None
        root, base = "local", lc.JOBS
    elif path.is_relative_to("/tmp"):
        root, base = "local", Path("/tmp")
    else:
        return None
    try:
        parts = files.parse_path(str(path.relative_to(base)))
    except files.FilesError:
        return None
    if not parts:
        return None
    agents = None
    if root == "spaces":
        import loam_agents  # noqa: PLC0415
        agents = set(loam_agents.every(AI_LABS))
    for i, part in enumerate(parts):
        if root == "spaces" and i > 0 and part == "scratch":
            continue
        if not files.visible(root, parts[:i], part, agents):
            return None
    return root, base, parts


def _open_image(path: Path) -> tuple[int, os.stat_result, str, str]:
    import loam_files as files  # noqa: PLC0415
    location = _image_location(path)
    if location is None:
        raise OSError("image outside the allowed sources")
    root, base, parts = location
    fd, info = files.open_regular_at(base, parts)
    if info.st_size > REPLY_IMAGE_BYTES or (root == "local" and info.st_uid != os.getuid()):
        os.close(fd)
        raise OSError("image is oversized or belongs to another user")
    return fd, info, root, str(path if root == "local" else path.relative_to(base))


def _image_file(raw: str) -> dict | None:
    path = _image_reference(raw)
    if path is None:
        return None
    try:
        fd, info, root, rel = _open_image(path)
    except OSError:
        return None
    os.close(fd)
    out = {"path": rel, "root": root, "name": path.name,
           "type": IMAGE_TYPES[path.suffix.lower()], "size": info.st_size}
    dims = image_size(path)
    if dims:
        out["width"], out["height"] = dims
    return out


def reply_images(text: str) -> tuple[str, list[dict]]:
    """The images a reply shows: ``(text, attachments)``. At most
    REPLY_IMAGES_MAX files, each once, every destination that named it in
    its ``refs``. The text comes back as it was: each reference is where
    its image is drawn."""
    if not text or "![" not in text:
        return text, []
    found: list[dict] = []
    by_file: dict[tuple[str, str], dict] = {}
    for m in IMAGE_REF_RE.finditer(text):
        ref = m.group(2).strip()
        known = next((f for f in found if ref in f["refs"]), None)
        if known is not None:
            continue
        if len(found) >= REPLY_IMAGES_MAX:
            # A fourth file stays text; one more name for a file already
            # shown is still that file.
            f = _image_file(ref)
            if f is not None and (f["root"], f["path"]) in by_file:
                by_file[(f["root"], f["path"])]["refs"].append(ref)
            continue
        f = _image_file(ref)
        if f is None:
            continue
        key = (f["root"], f["path"])
        if key in by_file:
            by_file[key]["refs"].append(ref)
            continue
        alt = m.group(1).strip()
        if alt:
            f["alt"] = alt[:200]
        f["refs"] = [ref]
        by_file[key] = f
        found.append(f)
    return text, found


def attachment_path(a: dict) -> str:
    """Where an attachment is on disk, for an agent reading the thread."""
    path = a.get("path") or ""
    if path.startswith("/"):
        return path
    return str((AI_LABS if a.get("root") == "spaces" else VAULT) / path)


def _save_reply_image(attachment: dict, day: str, *, created: list[Path] | None = None) -> dict:
    """Publish a complete, uniquely named copy before the record refers to it."""
    import loam_broker as cfg  # noqa: PLC0415
    source = Path(attachment_path(attachment))
    fd, _, root, rel = _open_image(source)
    if root == "vault" and Path(rel).is_relative_to(REPLY_ATTACHMENTS):
        os.close(fd)
        return {**attachment, "root": root, "path": rel}
    with os.fdopen(fd, "rb") as image:
        data = image.read(REPLY_IMAGE_BYTES + 1)
    if len(data) > REPLY_IMAGE_BYTES:
        raise OSError("image grew past the attachment limit")
    directory = cfg.attachment_dir("loam-ui", day, root=VAULT / REPLY_ATTACHMENTS.parent)
    if directory.resolve() != VAULT.resolve() / directory.relative_to(VAULT):
        raise OSError("the attachment directory contains a link")
    directory.mkdir(parents=True, exist_ok=True)
    name = Path(cfg.sanitise_name(source.name))
    target = directory / f"{name.stem}-{secrets.token_hex(8)}{source.suffix.lower()}"
    with tempfile.NamedTemporaryFile(dir=directory) as tmp:
        tmp.write(data)
        tmp.flush()
        os.link(tmp.name, target)
    if created is not None:
        created.append(target)
    saved = {**attachment, "root": "vault", "path": str(target.relative_to(VAULT)),
             "size": len(data)}
    saved.pop("width", None)
    saved.pop("height", None)
    if dims := image_size(target):
        saved["width"], saved["height"] = dims
    return saved


def render_message(msg: dict, quoted: dict | None = None) -> str:
    at = heading_at(msg["at"])
    who = display(msg["author"]) if msg["author"] != "system" else "Loam"
    head = f"### {who} · {at}"
    if msg.get("kind") == "bulletin":
        head += " · woke"
    meta = {k: msg[k] for k in ("id", "author", "at", "kind") if k in msg}
    if msg.get("reply_to"):
        meta["reply_to"] = msg["reply_to"]
    source = msg.get("reply_source")
    if source:
        meta["reply_source"] = source
        quoted = source
    if msg.get("did"):
        meta["did"] = msg["did"]
    # Preserve ordered text and tool steps in segments. Carry attachments and chosen
    # models, recovered late replies, branch links, insertion anchors, steered message IDs
    # and runtime-authored presets through serialization.
    for key in ("persona", "engine", "model", "segments", "attachments", "late",
                "link", "after", "steered", "preset", "journal_reply", "submission_id", "submission_hash",
                "occasion", "first_tag", "unaddressed", "answer_for"):
        if msg.get(key):
            meta[key] = msg[key]
    text = (msg.get("text") or "").strip()
    if msg.get("kind") == "system":
        text = f"*{text}*"
    # `-->` inside a string would close the comment for a reader of the
    # markdown; as `--\u003e` it is the same JSON.
    comment = json.dumps(meta, ensure_ascii=False).replace("-->", "--\\u003e")
    if msg.get("reply_to") and quoted is not None:
        line = quote_line(quoted)
        if source:
            line += f" · in {json.dumps(source['title'], ensure_ascii=False)}"
        text = f"{line}\n\n{text}"
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


def rebuild_index(*, save: bool = True) -> dict:
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
    if save:
        save_state("index.json", index)
    return index


def index() -> dict:
    data = load_state("index.json")
    if not data:
        data = rebuild_index()
    return data


def record_path(thread_id: str, *, save_index: bool = True) -> Path:
    """Find the record. Read-only callers can leave index repair to the broker."""
    name = load_state("index.json").get(thread_id)
    path = VAULT / CONVERSATIONS / name if name else None
    if path is None or not path.is_file():
        name = rebuild_index(save=save_index).get(thread_id)
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
    """Copy mutable member and engine fields as well as the outer mapping. A pending update
    must never mutate the cached record or the caller's before snapshot.
    """
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
    """Move a renamed thread's board with it. If another board owns the name, choose a free
    suffix such as (2).
    """
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


# Thread members use the broker's available provider engines.
ENGINES = ("claude-code", "codex", "antigravity")


def provider_card(persona: str) -> dict:
    try:
        card = json.loads((AI_LABS / persona / "agent.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return card if isinstance(card, dict) else {}


def default_engine(persona: str) -> str | None:
    return persona_defaults(persona)["provider"]


def persona_defaults(persona: str, override: str | None = None) -> dict:
    import loam_provider_defaults as providers  # noqa: PLC0415
    card = provider_card(persona)
    engine = override or providers.configured(card) or providers.automatic()
    return providers.agent_choice(card, engine)


def member_engine(meta: dict, persona: str) -> str | None:
    return meta["engines"].get(persona) or default_engine(persona)


def create(persona: str | None = None, title: str | None = None, *,
           thread_id: str | None = None, members: list[str] | None = None,
           pinned: bool = False, extra: dict | None = None,
           draft: str | None = None,
           draft_reply: dict | None = None) -> dict:
    """Create a thread with optional extra frontmatter, including branch metadata. Without
    a persona it has no members or default. An untagged message is recorded without an
    answer.
    """
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
            "engines": {},
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
        row = threads_state.setdefault(thread_id, {})
        row["placeholder_title"] = placeholder
        if draft and draft.strip():
            row["draft"] = draft
        if draft_reply:
            row["draft_reply"] = draft_reply
        save_state("threads.json", threads_state)
    return meta


def write_imported(meta: dict, msgs: list[dict]) -> Path:
    """Write all messages with their supplied IDs and timestamps. Record the source and
    mark the last imported message as read.
    """
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
    return append_once(thread_id, author, text, kind, did, **extra)[0]


def append_once(thread_id: str, author: str, text: str, kind: str = "message",
                did: list[dict] | None = None, **extra) -> tuple[dict, bool]:
    """Atomically find or file a keyed message under the thread's existing lock."""
    if kind not in KINDS:
        raise ThreadError(400, f"`kind` is one of {', '.join(KINDS)}")
    if not extra.get("reply_to"):
        extra.pop("reply_to", None)
    with locked(VAULT):
        meta, msgs, path = read(thread_id)
        if extra.get("occasion") and (meta.get("archived") or author not in meta["members"]):
            raise ThreadError(409, "This chat is closed to this occasion.")
        for field in ("journal_reply", "submission_id", "occasion"):
            if extra.get(field):
                existing = next((m for m in msgs if m["author"] == author
                                 and m.get(field) == extra[field]), None)
                if existing is not None:
                    return existing, False
        # Cross-thread quotes are resolved before journaling an opening. Local replies
        # still validate against this record under the append lock.
        if extra.get("reply_source"):
            if any(is_owner(m.get("author")) for m in msgs):
                raise ThreadError(409, "A branch quote belongs on the opening message.")
            quoted = None
        else:
            quoted = (check_reply_to(msgs, extra["reply_to"], thread_id)
                      if "reply_to" in extra else None)
        last = max((int(m["id"][1:]) for m in msgs
                    if str(m.get("id", "")).startswith("m")
                    and m["id"][1:].isdigit()), default=0)
        msg = {"id": f"m{last + 1}", "author": author, "at": now_iso(),
               "kind": kind, "text": text.strip(), "did": did or [], **extra}
        if author != "system" and not is_owner(author) and msg.get("attachments"):
            saved = []
            for attachment in msg["attachments"]:
                if not attachment.get("refs"):
                    saved.append(attachment)
                    continue
                try:
                    saved.append(_save_reply_image(attachment, msg["at"][:10]))
                except OSError:
                    saved.append({**attachment, "missing": True})
            msg["attachments"] = saved
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(render_message(msg, quoted))
    return msg, True


def replace_message(thread_id: str, msg_id: str, new: list[dict]) -> list[dict]:
    """Replace only the specified message's bytes. The first replacement keeps its ID and
    subsequent entries take free IDs in order. An empty replacement removes it.
    """
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


def backfill_reply_images(thread_id: str, *, dry_run: bool = False) -> list[tuple[str, int, int]]:
    """Repair agent attachment metadata under the record writer's lock.

    Only metadata comments change. Return (message id, recoverable, unavailable)
    per affected message, with recoverable meaning written when applying.
    """
    import loam_files as files  # noqa: PLC0415
    changes = []
    created: list[Path] = []
    with locked(VAULT):
        path = record_path(thread_id, save_index=False)
        raw = path.read_bytes().decode("utf-8")
        _, body = split_front(raw)
        messages = {m.get("id"): m for m in parse_messages(body)}
        lines = raw.split("\n")
        for i in range(1, len(lines)):
            match = META_RE.match(lines[i]) if lines[i - 1].startswith("### ") else None
            if not match:
                continue
            try:
                meta = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            author, mid = meta.get("author"), meta.get("id")
            if not author or author == "system" or is_owner(author) or mid not in messages:
                continue
            attachments = meta.get("attachments") or []
            refs = {ref for a in attachments if isinstance(a, dict) for ref in a.get("refs") or []}
            segments = [s["text"] for s in meta.get("segments") or []
                        if isinstance(s, dict) and isinstance(s.get("text"), str)]
            _, inline = reply_images("\n\n".join([*segments, messages[mid]["text"]]))
            inline_refs = {(a["root"], a["path"]): a["refs"] for a in inline}
            # Old writers only recognized vault and workspace files. Recover
            # still-present temporary references that never had an attachment.
            local = [a for a in inline if a["root"] == "local" and refs.isdisjoint(a["refs"])]
            candidates = [*attachments, *local[:max(0, REPLY_IMAGES_MAX - len(attachments))]]
            day = str(meta.get("at", ""))[:10]
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                day = now_iso()[:10]
            saved, fixed, unavailable = [], 0, 0
            for attachment in candidates:
                if (not isinstance(attachment, dict) or attachment.get("type") not in IMAGE_TYPES.values()
                        or not isinstance(attachment.get("path"), str)):
                    saved.append(attachment)
                    continue
                source = Path(attachment_path(attachment))
                root = attachment.get("root") or "vault"
                base = AI_LABS if root == "spaces" else VAULT
                if root in ("vault", "spaces") and source.is_relative_to(base) and not attachment.get("missing"):
                    try:
                        fd, _, _ = files.download(root, str(source.relative_to(base)))
                        os.close(fd)
                        saved.append(attachment)
                        continue
                    except files.FilesError:
                        pass
                try:
                    if dry_run:
                        fd, _, _, _ = _open_image(source)
                        os.close(fd)
                    else:
                        repaired = _save_reply_image(attachment, day, created=created)
                        if not repaired.get("refs") and (original := inline_refs.get((root, attachment["path"]))):
                            repaired["refs"] = original
                        repaired.pop("missing", None)
                        saved.append(repaired)
                    fixed += 1
                except OSError:
                    if attachment not in local:
                        saved.append(attachment)
                    unavailable += 1
            if fixed:
                meta["attachments"] = saved
                if not dry_run:
                    lines[i] = (lines[i][:match.start(1)] + json.dumps(meta, ensure_ascii=False)
                                + lines[i][match.end(1):])
            if fixed or unavailable:
                changes.append((mid, fixed, unavailable))
        if not dry_run and any(fixed for _, fixed, _ in changes):
            try:
                _write_atomic(path, "\n".join(lines))
            except OSError:
                for target in created:
                    target.unlink(missing_ok=True)
                raise
            _CACHE.pop(str(path), None)
    return changes


def migrate_reply_images() -> dict | None:
    """The thread writer's one-time upgrade of older image references."""
    name = "reply-images-v1.json"
    if _state_path(name).is_file():
        return None
    changed, errors = {}, {}
    for thread_id in sorted(rebuild_index(save=False)):
        try:
            rows = backfill_reply_images(thread_id)
        except (OSError, UnicodeError, ThreadError) as exc:
            errors[thread_id] = str(exc)
            continue
        if rows:
            changed[thread_id] = {mid: {"copied": fixed, "unavailable": missing}
                                  for mid, fixed, missing in rows}
    result = {"threads": changed, "errors": errors}
    if not errors:
        save_state(name, result)
    return result


_UNSET = object()


def update(thread_id: str, *, title: str | None = None, default=_UNSET,
           add: list[str] | None = None, remove: list[str] | None = None,
           engines: dict | None = None, models: dict | None = None,
           icon=_UNSET, pinned: bool | None = None,
           archived: bool | None = None) -> dict:
    """Change the frontmatter. Clearing the default leaves untagged messages unanswered. A
    null member model restores its own default. General keeps its name and stays pinned
    and unarchived.
    """
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
        for p, engine in (engines or {}).items():
            if engine is not None and engine not in ENGINES:
                raise ThreadError(400, f"engine {engine!r}: one of {', '.join(ENGINES)}, or null to inherit")
            if p not in meta["members"]:
                raise ThreadError(400, f"{p} is not a member of this thread")
            if meta["engines"].get(p) != engine:
                # A model is an engine's; a switch goes back to the persona's.
                meta["models"].pop(p, None)
            if engine is None:
                meta["engines"].pop(p, None)
            else:
                meta["engines"][p] = engine
        for p, model in (models or {}).items():
            if p not in meta["members"]:
                raise ThreadError(400, f"{p} is not a member of this thread")
            if model is None:
                meta["models"].pop(p, None)
            else:
                # A saved model belongs to the provider it was checked on.
                if p not in meta["engines"]:
                    engine = default_engine(p)
                    if engine is None:
                        from loam_provider_defaults import SETUP  # noqa: PLC0415
                        raise ThreadError(409, SETUP)
                    meta["engines"][p] = engine
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
    """Remove the thread, board and runtime state on the owner's confirmed action. Preserve
    General and report what was removed.
    """
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
        for name in ("threads.json", "sessions.json", "handoffs.json"):
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


# Legacy branches carried a window ending at the chosen message: back to the last real pause (a
# gap over BRANCH_PAUSE_S between two messages), or BRANCH_MAX messages,
# whichever comes first, and never fewer than BRANCH_MIN when the thread has
# them. System lines are not part of it.
BRANCH_PAUSE_S = 30 * 60
BRANCH_MAX = 12
BRANCH_MIN = 3


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


def branch_quote(source_id: str, msg_id: str) -> dict:
    """Resolve a cross-thread reply against the source and snapshot its words."""
    meta, msgs, path = read(source_id)
    target = check_reply_to(msgs, msg_id, source_id)
    if target.get("kind") == "system":
        raise ThreadError(400, "a branch starts from a message")
    return {"thread": meta["id"], "title": meta["title"], "path": str(path),
            "id": target["id"], "author": target["author"], "at": target["at"],
            "text": _said(target, verbatim=True),
            **({"attachments": target["attachments"]} if target.get("attachments") else {})}


def clear_draft_reply(thread_id: str) -> None:
    with locked(VAULT):
        data = load_state("threads.json")
        if data.get(thread_id, {}).pop("draft_reply", None) is not None:
            save_state("threads.json", data)


def draft_reply_for(thread_id: str) -> dict | None:
    return load_state("threads.json").get(thread_id, {}).get("draft_reply")


def set_draft(thread_id: str, text: str | None) -> None:
    with locked(VAULT):
        data = load_state("threads.json")
        row = data.setdefault(thread_id, {})
        if text and text.strip():
            row["draft"] = text
        elif row.pop("draft", None) is None:
            return
        save_state("threads.json", data)


def clear_opening_draft(thread_id: str) -> None:
    with locked(VAULT):
        data = load_state("threads.json")
        row = data.get(thread_id, {})
        draft = row.pop("draft", None)
        reply = row.pop("draft_reply", None)
        if draft is not None or reply is not None:
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
           title: str | None = None, draft: str | None = None) -> dict:
    """Open a composer with a source quote. The source gains a link after the
    owner sends the opening with the quote still attached."""
    quote = branch_quote(source_id, msg_id)
    if not persona:
        persona = read(source_id)[0].get("default")
    if not persona:
        raise ThreadError(400, "who is the new thread with? pass `persona`")
    return create(persona, title, draft=draft, draft_reply=quote)


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
                     context: dict | None = None, watch: bool | None = None,
                     summary_due: float | None = None) -> None:
    """Save the last recorded session sequence so the next turn can recover a late reply.
    Keep the last context usage and window for rotation after the session leaves memory.
    """
    with locked(VAULT):
        data = load_state("sessions.json")
        row = {"session_id": session_id, "engine": engine, "through": through}
        if isinstance(seq, int):
            row["seq"] = seq
        if isinstance(context, dict) and isinstance(context.get("used"), int):
            row["context"] = {"used": context["used"], "window": context.get("window")}
        previous = data.get(thread_id, {}).get(persona, {})
        if summary_due is not None:
            row["summary_due"] = summary_due
        elif previous.get("session_id") == session_id and "summary_due" in previous:
            row["summary_due"] = previous["summary_due"]
        if watch or (watch is None and previous.get("watch") and previous.get("session_id") == session_id):
            row["watch"] = True
        data.setdefault(thread_id, {})[persona] = row
        save_state("sessions.json", data)


def finish_watch(thread_id, persona, session_id):
    with locked(VAULT):
        data = load_state("sessions.json")
        kept = data.get(thread_id, {}).get(persona, {})
        if kept.get("session_id") == session_id and kept.pop("watch", False):
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

def empty_board(meta: dict) -> str:
    return f"# {meta['title']} — board\n\n"


def board_text(meta: dict, create_missing: bool = True) -> str:
    path = VAULT / meta["board"]
    try:
        return path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        if not create_missing:
            return ""
        with locked(path):
            if not path.exists():
                _write_atomic(path, empty_board(meta))
            return path.read_bytes().decode("utf-8")


def board_snapshot(meta: dict, markdown: str | None = None) -> dict:
    if markdown is None:
        markdown = board_text(meta)
    return {"path": meta["board"], "markdown": markdown,
            "revision": loam_file_revision.revision(markdown.encode("utf-8"))}


def write_board(meta: dict, markdown: str, revision: str) -> dict:
    if not isinstance(markdown, str) or not isinstance(revision, str) or not revision:
        raise ThreadError(400, "A board save needs markdown and its base revision")
    path = VAULT / meta["board"]
    with locked(path):
        try:
            loam_file_revision.replace_text(path, markdown, revision)
        except loam_file_revision.RevisionConflict as exc:
            raise ThreadError(409, f"The board {exc}") from None
        return board_snapshot(meta, markdown)


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
    """Build a thread row with each member's last_spoke_at and running state. Parallel
    turns may put more than one member in the running field.
    """
    meta, msgs, _ = read(thread_id)
    live = {running} if isinstance(running, str) else set(running or ())
    shown = [m for m in msgs if m.get("kind") != "system"] or msgs
    last = shown[-1] if shown else None
    board = board_text(meta, create_missing=False)
    spoke = last_spoke(msgs)
    opening = next((m for m in msgs if is_owner(m.get("author"))), {})
    source = opening.get("reply_source")
    draft = load_state("threads.json").get(thread_id, {})
    return {
        "id": meta["id"], "title": meta["title"], "default": meta["default"],
        "icon": meta.get("icon"),
        # A null model uses the persona's default. The app reads busy as an alias for running.
        "members": [{"persona": p, "engine": member_engine(meta, p),
                     "engine_override": meta["engines"].get(p),
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
        # Return the source session and transcript for an imported thread.
        "imported_from": meta.get("imported_from"),
        "source": meta.get("source"),
        "branched_from": ({"thread": source["thread"], "message": opening["reply_to"],
                           "reply": True} if source else
                          {"thread": meta["branched_from"], "message": meta.get("branched_message")}
                          if meta.get("branched_from") else None),
        "draft": draft.get("draft"),
        # Identify the starter thread used for the first visit.
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
    """Page back from the position of before in record order. A recovered m17 can precede
    m11. Use the numeric fallback only when the record lacks the requested ID.
    """
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


# Render the current time in the install's time zone.
PACIFIC = loam_clock.PACIFIC
local_stamp = loam_clock.local_stamp


def clock_line(now: datetime | None = None) -> str:
    """The sentence every thread turn opens with: the weekday, the date and
    the time in America/Los_Angeles."""
    return (f"{loam_clock.sentence(now)} Message times below are in the "
            f"same zone.")


def _said(m: dict, *, verbatim: bool = False) -> str:
    # Include earlier text segments as well as the reply's closing words.
    said = [seg["text"] if verbatim else seg["text"].strip() for seg in (m.get("segments") or [])
            if isinstance(seg, dict) and isinstance(seg.get("text"), str)
            and seg["text"].strip()]
    return "\n\n".join(said + [m["text"]]) if said else m["text"]


def replied_block(m: dict, record: list[dict] | None,
                  record_file: str | None = None) -> str:
    """Quote the referenced message above the reply, even outside the prompt window.
    Include its author, time, ID and text, with a pointer to the rest of a long quote.
    """
    if not m.get("reply_to"):
        return ""
    who = display(m["author"])
    source = m.get("reply_source")
    if source:
        try:
            meta, _, path = read(source["thread"])
            title, path = meta["title"], str(path)
        except (ThreadError, OSError):
            title, path = source["title"], source["path"]
        quoted = "\n".join("> " + line if line else ">" for line in source["text"].split("\n"))
        return (f'{who} is replying to {display(source["author"])}\'s message '
                f'{source["id"]} of [{local_stamp(source["at"])}] in the thread '
                f'"{title}" (id {source["thread"]}). You may read earlier messages '
                f'in the source thread file {path} if you need them.\n'
                f"{quoted}\n{who}'s words in reply:\n")
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
    """Format the member's next prompt window with message IDs. Resolve reply targets
    against the full record when available.
    """
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


# Give the member its own summary, recent exchange, quoted reply targets and the
# thread file path.

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
                unshown: int = 0, summary_through: str | None = None) -> str:
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
                shown = shown[:SUMMARY_CHARS].rstrip() + f"\n[… the rest is in {mine}]"
            source = (f"through {summary_through}" if summary_through else
                      f"as you last wrote it in {mine}")
            head += (f"Your summary of this thread, {source}:\n"
                     f"<your-summary>\n{shown}\n</your-summary>\n"
                     "Later messages may change this account. Use the recent exchange "
                     "below and read the thread file for any intervening messages.\n\n")
        else:
            head += "You have no summary of this thread yet.\n\n"
        # Older branches keep the source-window context they were created with.
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
    where = record_file or f"the thread's file in {lc.vault_folder('conversations')}"
    pointer = (f"\n\n[Earlier history is in the thread file, {where}: every "
               f"message under its id. Read it when you need something from "
               f"before what is shown here.]")
    tail = ("\n\nWrite your reply to the thread. All your words around tool calls "
            "are part of that reply. Finish when you have answered. "
            "Mention a peer with @Name only when you are sure it adds a lot. "
            "To show an image (at most three), put ![a few words](its path) "
            "on a line of its own where you want it: a file in the vault or "
            "the workspaces, and it shows there in your message, its words "
            "as a caption. "
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
    """Deliver only the new messages and identify their thread to a member already
    mid-turn. Its eventual response still belongs to the same turn.
    """
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
