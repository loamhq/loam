"""Making an agent by conversation, archiving it and restoring it, and its face.

Decided 2026-11-14 (first release, decisions 6 and 7; job L10):

- **Created by conversation only.** An agent (Iris, or any other) drafts the
  new agent's name, soul and config (the library skill
  ``loam-making-an-agent`` says how: a character, never a job), shows them to the person, revises
  on their word, and the agent is made only on their yes. The draft is a file
  here (``<state>/agents/drafts/<id>.json``) with a revision number, so the
  yes is tied to the version that was shown.
- **Archive, never delete.** Archiving puts an agent to sleep: ``archived`` in
  its ``agent.json`` takes it out of Chats, every picker and every mention;
  the schedule reconciler takes its wake and cron timers down (its records
  are untouched); a turn that would be its is refused with a line in the thread. Its threads stay readable and its
  files stay where they are. Restore removes the key, and the reconciler puts
  the same timers back from the same records. Nothing here deletes an agent.
- **A face** is an uploaded image (PNG, JPEG, WebP or GIF) kept in the
  agent's own workspace (``face.<ext>``) and named in ``agent.json``
  (``face``). It is the agent's, so it holds through any theme; a theme's
  faces apply only to agents without one.
- **No emoji face** (the owner, 2026-11-16: "We should remove the concept
  of persona face emoji. That was a legacy feature from the Telegram
  days"). A draft takes none and a made agent's ``agent.json`` has none; an
  older ``agent.json`` that still holds ``emoji`` is left as it is, and
  nothing serves the key.
- **Born in the map's shape** (2026-11-18, lib/loam_loading.py). A new
  agent is made with a ``soul.md``, a ``config.md`` that is the draft's own
  config as written (the owner's words for this agent, which may be one
  line), and a ``map.md`` (the draft's, or ``loam_loading.MAP_SEED``), and
  never ``_always.md``, ``_active.md`` or ``_reflection.md``. Loam's text and
  the owner's shared page load beside its config without being copied into
  it, so nothing of the fleet's configs is composed in any more. No line
  about ``About me.md`` is written either (Loam's text named it from
  2026-11-18 to 2026-11-19; the library's ``loam-how-it-works`` does).

**The yes.** A person's own call (the page, which sends no ``by``) is the
person's yes. An agent's call (``by``) must name the thread it is talking to
the person in and the id of the person's message that said yes; the route
checks, in the thread's record, that the agent is a member there, that the
message is the owner's, and that it was written after the version it
approves (for a draft: after it in the thread too, 2026-11-17, since a
stamp is to the second) or within ``YES_WINDOW`` (for an archive or a
restore), and it takes each yes once. The record is written by the runtime
alone, and every agent's post is recorded under its own name, so an agent
cannot write a message as the person through any route. That is a guard
against a mistaken or unattended act, not a wall: every process on this box
holds the broker's token, and a persona has a shell. An unattended lane is
refused before the route (lib/loam_settings.py, ``unattended``).

Stdlib only, and light at import: every roster on the box imports
``is_archived``.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import loam_config as lc

# `About me.md`'s name (lib/loam_skills.py, which seeds it; not imported, to
# keep this module light).
ABOUT_ME = "About me.md"

ARCHIVE_KEY = "archived"
FACE_KEY = "face"
# Folders under the workspaces root that are not agents, and words an id may
# not be: the routes' own path words, the owner's, and the threads'.
RESERVED_IDS = frozenset({
    "commons", "_shared", "shared", "drafts", "archived", "new", "all",
    "everyone", "system", "loam", "engineer", "engineers", "reviewer",
    "owner", "me", "you", lc.OWNER_ID, *lc.LEGACY_OWNER_IDS})
ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,23}$")
TEXT_MAX = 64 * 1024            # a soul or a config, in bytes
LAYER_MAX = 64 * 1024
FACE_MAX = 2 * 1024 * 1024      # an uploaded face, in bytes
FACE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp",
              "image/gif": "gif"}
YES_WINDOW = timedelta(minutes=30)
# The starter agent, which "New agent" opens a thread with.
STARTER = lc.get("agents.starter", "iris")
# Where the agent's soul and config come from, as fields of a draft.
DRAFT_TEXT = ("soul", "config", "map")
DRAFT_FIELDS = ("id", "name", "about", "provider", "wake", *DRAFT_TEXT)
# The draft card's one plain line about who the agent is (2026-11-18): the
# card showed the soul's opening, which is written to the agent ("You are
# Juniper, …"). Written by the drafting agent for the person, in the third
# person; never part of the soul, and not kept once the agent is made.
ABOUT_MAX = 200
# Retired keys a draft may still carry from before 2026-11-16: never shown.
# `active` and `reflection` since 2026-11-18: a new agent loads its map,
# and has neither file.
RETIRED_DRAFT_KEYS = ("emoji", "active", "reflection")
PROVIDERS = ("claude-code", "codex", "antigravity")

# A new agent's loading files were seeded here until 2026-11-18
# (`_active.md`, `_reflection.md`, and `_always.md` with a line about
# `About me.md`). A new agent now has a map (lib/loam_loading.MAP_SEED when
# the draft brings none); `About me.md` is named by the library's
# loam-how-it-works (by Loam's text from 2026-11-18 to 2026-11-19).


class AgentsError(Exception):
    def __init__(self, message: str, status: int = 400, detail: dict | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.detail = detail or {}


# ---------------------------------------------------------------------------
# Where things are. Each overridable, so the tests run on a scratch tree.
# ---------------------------------------------------------------------------

def ai_labs() -> Path:
    return Path(os.environ.get("LOAM_AI_LABS") or lc.WORKSPACES)


def state() -> Path:
    return Path(os.environ.get("LOAM_AGENTS_STATE") or lc.STATE / "agents")


def drafts_dir() -> Path:
    return state() / "drafts"


def events_path() -> Path:
    return state() / "events.jsonl"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc(stamp: str) -> datetime | None:
    try:
        return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Archived or not: what every roster asks.
# ---------------------------------------------------------------------------

_CARDS: dict[str, tuple[tuple[int, int], dict]] = {}


def card(agent: str, root: Path | None = None) -> dict:
    """An agent's agent.json, read again only when the file changed: every
    roster asks, and a roster is asked on every mention (one stat each, as
    the rosters' own `is_file` already cost). A copy, so no caller edits
    the cache."""
    path = (root or ai_labs()) / agent / "agent.json"
    try:
        st = path.stat()
        key = (st.st_mtime_ns, st.st_size)
        hit = _CARDS.get(str(path))
        if hit and hit[0] == key:
            return dict(hit[1])
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    _CARDS[str(path)] = (key, value)
    return dict(value)


def is_archived(agent: str, root: Path | None = None) -> bool:
    return bool(card(agent, root).get(ARCHIVE_KEY))


def every(root: Path | None = None) -> list[str]:
    """Every agent on the box, archived ones included."""
    root = root or ai_labs()
    try:
        return sorted(d.name for d in root.iterdir()
                      if d.is_dir() and (d / "agent.json").is_file())
    except OSError:
        return []


def live(names: list[str], root: Path | None = None) -> list[str]:
    return [n for n in names if not is_archived(n, root)]


def archived(root: Path | None = None) -> list[str]:
    return [n for n in every(root) if is_archived(n, root)]


# ---------------------------------------------------------------------------
# The record of what was made, archived and restored.
# ---------------------------------------------------------------------------

@contextmanager
def locked():
    state().mkdir(parents=True, exist_ok=True)
    with open(state() / ".lock", "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def log(event: dict) -> dict:
    event = {"at": now_iso(), **event}
    state().mkdir(parents=True, exist_ok=True)
    with open(events_path(), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def events() -> list[dict]:
    try:
        lines = events_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def _write_agent(agent: str, config: dict) -> None:
    import loam_profile  # noqa: PLC0415 -- agent.json's one renderer
    target = ai_labs() / agent / "agent.json"
    tmp = target.with_name(f".agent.json.{os.getpid()}.tmp")
    tmp.write_text(loam_profile.render_agent(config), encoding="utf-8")
    os.replace(tmp, target)


# ---------------------------------------------------------------------------
# The person's yes.
# ---------------------------------------------------------------------------

def check_yes(by: str | None, thread: str | None, yes: str | None, *,
              after: str | None = None, window: timedelta | None = None,
              used: set[tuple[str, str]] | None = None,
              after_message: tuple[str, str | None] | None = None) -> dict | None:
    """None when the call is the person's own (no ``by``). For an agent's
    call: the person's message ``yes`` in ``thread``, which the agent is a
    member of, written at or after ``after`` or within ``window`` of now,
    and not taken before. ``after_message`` (2026-11-17) is ``(thread, the
    id of its newest message when the draft's revision was written)``: a yes
    in that thread must come after it, which decides what a one-second
    stamp cannot. Answers ``{"thread", "message", "at"}``."""
    if not by:
        return None
    if not thread or not yes:
        raise AgentsError(
            f"an agent acts here only on {lc.OWNER_NAME}'s yes: pass `thread` "
            f"(the thread you are talking in) and `yes` (the id of "
            f"{lc.OWNER_NAME}'s message that said yes, like m12)", 403)
    import loam_threads as threads  # noqa: PLC0415
    try:
        meta, msgs, _ = threads.read(thread)
    except threads.ThreadError as exc:
        raise AgentsError(f"`thread`: {exc.message}", 404) from None
    except OSError:
        raise AgentsError(f"`thread`: no thread {thread!r}", 404) from None
    if by not in (meta.get("members") or []) and by != meta.get("default"):
        raise AgentsError(f"{by} is not in thread {thread}; the yes has to be "
                          f"said to you", 403)
    msg = threads.find(msgs, yes)
    if msg is None:
        raise AgentsError(f"`yes`: thread {thread} has no message {yes!r}", 404)
    if not lc.is_owner(msg.get("author")) or msg.get("kind") != "message":
        raise AgentsError(f"`yes`: {yes} in {thread} is not {lc.OWNER_NAME}'s "
                          f"message", 403)
    at = _utc(msg.get("at"))
    if after_message and after_message[0] == thread:
        ids = [m.get("id") for m in msgs]
        mark = after_message[1]
        if mark is not None and mark in ids and ids.index(yes) <= ids.index(mark):
            raise AgentsError(
                f"`yes`: {yes} was written before this version of the draft "
                f"(revision written after {mark}); show {lc.OWNER_NAME} the "
                f"version you are confirming and take the yes they give to it", 409)
    if after and (at is None or at < _utc(after)):
        raise AgentsError(
            f"`yes`: {yes} was written before this version of the draft "
            f"({after}); show {lc.OWNER_NAME} the version you are confirming "
            f"and take the yes they give to it", 409)
    if window and (at is None or datetime.now(timezone.utc) - at > window):
        raise AgentsError(f"`yes`: {yes} is older than "
                          f"{int(window.total_seconds() // 60)} minutes; ask again", 409)
    if used is not None and (thread, yes) in used:
        raise AgentsError(f"`yes`: {yes} in {thread} was already taken for "
                          f"another change; each yes is taken once", 409)
    return {"thread": thread, "message": yes, "at": msg.get("at")}


def _used_yeses() -> set[tuple[str, str]]:
    out = set()
    for e in events():
        y = e.get("yes")
        if isinstance(y, dict) and y.get("thread") and y.get("message"):
            out.add((y["thread"], y["message"]))
    for d in _all_drafts():
        y = d.get("yes")
        if isinstance(y, dict) and y.get("thread") and y.get("message"):
            out.add((y["thread"], y["message"]))
    return out


# ---------------------------------------------------------------------------
# Drafts.
# ---------------------------------------------------------------------------

def slug(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if text and not text[0].isalpha():
        text = "a-" + text
    return text[:24].rstrip("-")


def _names_taken(except_agent: str | None = None) -> dict[str, str]:
    """{lower-cased id or display name: whose}, archived agents included:
    a restore must never meet its own name taken."""
    out = {}
    for a in every():
        if a == except_agent:
            continue
        out[a.lower()] = a
        shown = card(a).get("display")
        if isinstance(shown, str) and shown.strip():
            out[shown.strip().lower()] = a
    return out


def _clean_text(field: str, value, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgentsError(f"`{field}` is the file's text and cannot be empty")
    if len(value.encode("utf-8")) > limit:
        raise AgentsError(f"`{field}` is more than {limit // 1024} KB")
    return value if value.endswith("\n") else value + "\n"


def _from_file(field: str, path, by: str | None) -> str:
    """A draft field given as a file the drafting agent wrote: read at the
    call, so the draft holds what was shown. Only from a workspace or the
    vault."""
    if not isinstance(path, str) or not path.strip():
        raise AgentsError(f"`{field}_file` is a path to the file")
    p = Path(path).expanduser()
    if not p.is_absolute() and by:
        p = ai_labs() / by / p
    p = p.resolve()
    roots = [ai_labs().resolve(), Path(lc.VAULT).resolve()]
    if not any(p == r or r in p.parents for r in roots):
        raise AgentsError(f"`{field}_file` must be in a workspace or the vault")
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise AgentsError(f"`{field}_file` cannot be read ({exc})") from None


def _clean_fields(patch: dict, draft: dict, by: str | None) -> dict:
    out = {}
    for field in DRAFT_TEXT:
        if f"{field}_file" in patch:
            patch = {**patch, field: _from_file(field, patch[f"{field}_file"], by)}
    if "name" in patch:
        name = " ".join(str(patch["name"] or "").split())
        if not name or len(name) > 40:
            raise AgentsError("`name` is 1 to 40 characters")
        if any(unicodedata.category(c).startswith("C") for c in name):
            raise AgentsError("`name` has a control character in it")
        out["name"] = name
    if "id" in patch and patch["id"] is not None:
        out["id"] = str(patch["id"]).strip().lower()
    if "about" in patch:
        about = " ".join(str(patch["about"] or "").split())
        if len(about) > ABOUT_MAX:
            raise AgentsError(f"`about` is one plain line, at most {ABOUT_MAX} "
                              f"characters")
        if any(unicodedata.category(c).startswith("C") for c in about):
            raise AgentsError("`about` has a control character in it")
        out["about"] = about or None
    if "provider" in patch:
        v = patch["provider"]
        if v is not None and v not in PROVIDERS:
            raise AgentsError(f"`provider` is one of {', '.join(PROVIDERS)}, or null "
                              f"to use a connected provider")
        out["provider"] = v
    if "wake" in patch:
        v = patch["wake"]
        if v is not None and not (isinstance(v, str) and re.fullmatch(r"\d{2}:\d{2}", v)
                                  and int(v[:2]) < 24 and int(v[3:]) < 60):
            raise AgentsError("`wake` is the nightly wake's time, HH:MM, or null for "
                              "a free one")
        out["wake"] = v
    for field in ("soul", "config"):
        if field in patch:
            out[field] = _clean_text(field, patch[field], TEXT_MAX)
    for field in ("map",):
        if field in patch:
            out[field] = (None if patch[field] is None
                          else _clean_text(field, patch[field], LAYER_MAX))
    merged = {**draft, **out}
    if "name" in out and "id" not in out and not draft.get("id_given"):
        merged["id"] = slug(out["name"])
    if "id" in out:
        merged["id_given"] = True
    return merged


def check_new(draft: dict) -> list[str]:
    """What stands between this draft and an agent; [] when nothing does."""
    problems = []
    agent = draft.get("id") or ""
    if not draft.get("name"):
        problems.append("it has no name")
    if not ID_RE.match(agent):
        problems.append(f"its id {agent!r} is not 2 to 24 lower-case letters, "
                        f"digits or dashes starting with a letter (give `id`)")
    elif agent in RESERVED_IDS:
        problems.append(f"{agent!r} is a word Loam keeps for itself (give `id`)")
    elif (ai_labs() / agent).exists():
        problems.append(f"there is already a folder named {agent!r} among the "
                        f"agents (give another `id`)")
    taken = _names_taken()
    for word in {agent.lower(), (draft.get("name") or "").lower()} - {""}:
        if word in taken:
            problems.append(f"{word!r} is {taken[word]}'s name or id; an @ would "
                            f"not know which of them it meant")
    if (draft.get("name") or "").lower() in RESERVED_IDS:
        problems.append(f"{draft['name']!r} is a word the threads keep for themselves")
    for field in ("soul", "config"):
        if not draft.get(field):
            problems.append(f"it has no {field}")
    return problems


def draft_path(draft_id: str) -> Path:
    if not re.fullmatch(r"d-[0-9a-f]{8}", draft_id or ""):
        raise AgentsError(f"no draft {draft_id!r}", 404)
    return drafts_dir() / f"{draft_id}.json"


def read_draft(draft_id: str) -> dict:
    try:
        return json.loads(draft_path(draft_id).read_text(encoding="utf-8"))
    except OSError:
        raise AgentsError(f"no draft {draft_id!r}", 404) from None


def _all_drafts() -> list[dict]:
    out = []
    folder = drafts_dir()
    for p in sorted(folder.glob("d-*.json")) if folder.is_dir() else []:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def shown(draft: dict) -> dict:
    """A draft as a route answers it: everything, and what stands in its way."""
    out = {k: v for k, v in draft.items()
           if k != "id_given" and k not in RETIRED_DRAFT_KEYS}
    out["problems"] = [] if draft.get("made") else check_new(draft)
    # What the made config.md adds to the draft's own: nothing since
    # 2026-11-18. Loam's text and the owner's shared page load beside it and
    # are not copied in (compose_config is no longer called by `make`).
    if not draft.get("made"):
        out["fleet_clauses"] = []
    out["ready"] = not out["problems"] and not draft.get("made")
    return out


def drafts(thread: str | None = None) -> list[dict]:
    rows = [d for d in _all_drafts() if not d.get("made") and not d.get("discarded")]
    if thread:
        rows = [d for d in rows if d.get("thread") == thread]
    return [shown(d) for d in rows]


def _newest_message(thread: str | None) -> str | None:
    """The id of ``thread``'s newest message, or None (no thread, or none
    yet): where a revision stands in the conversation its yes comes in."""
    if not thread:
        return None
    import loam_threads as threads  # noqa: PLC0415
    try:
        msgs = threads.read(thread)[1]
    except (threads.ThreadError, OSError):
        return None
    return msgs[-1].get("id") if msgs else None


def _refuse_retired(fields) -> None:
    old = sorted({"active", "reflection"} & set(fields))
    if old:
        raise AgentsError(
            f"`{'`, `'.join(old)}` retired on 2026-11-18: a new agent loads its "
            f"soul, its config and its map (`map`, optional; it points to "
            f"things and does not hold them), and has no _active.md or "
            f"_reflection.md")
    if "emoji" in fields:
        raise AgentsError("`emoji` is retired: an agent's face is the one the theme "
                          "draws, or an image uploaded once it is made (agent.face)")


def new_draft(payload: dict, by: str | None) -> dict:
    payload = dict(payload or {})
    thread = payload.pop("thread", None)
    payload.pop("by", None)
    unknown = sorted(set(payload) - set(DRAFT_FIELDS)
                     - {f"{f}_file" for f in DRAFT_TEXT})
    _refuse_retired(unknown)
    if unknown:
        raise AgentsError(f"a draft has no field {', '.join(unknown)}; it takes "
                          f"{', '.join(DRAFT_FIELDS)} (soul, config and map "
                          f"also as <field>_file)")
    if by and not thread:
        raise AgentsError("pass `thread`: the thread you are drafting in, where "
                          f"{lc.OWNER_NAME} will see it and say yes")
    if thread:
        import loam_threads as threads  # noqa: PLC0415
        try:
            threads.read(thread)
        except (threads.ThreadError, OSError):
            raise AgentsError(f"`thread`: no thread {thread!r}", 404) from None
    at = now_iso()
    draft = _clean_fields(payload, {"draft": "d-" + secrets.token_hex(4),
                                    "rev": 1, "by": by or lc.OWNER_ID,
                                    "thread": thread, "created": at,
                                    "updated": at, "revisions": []}, by)
    draft["revisions"] = [{"rev": 1, "at": at, "by": by or lc.OWNER_ID,
                           "fields": sorted(k for k in payload)}]
    with locked():
        draft["after_message"] = _newest_message(thread)
        _write_json(draft_path(draft["draft"]), draft)
    return shown(draft)


def revise(draft_id: str, patch: dict, by: str | None) -> dict:
    patch = {k: v for k, v in (patch or {}).items() if k not in ("by", "thread")}
    unknown = sorted(set(patch) - set(DRAFT_FIELDS) - {f"{f}_file" for f in DRAFT_TEXT})
    _refuse_retired(unknown)
    if unknown:
        raise AgentsError(f"a draft has no field {', '.join(unknown)}")
    if not patch:
        raise AgentsError(f"name a field to change: {', '.join(DRAFT_FIELDS)}")
    with locked():
        draft = read_draft(draft_id)
        if draft.get("made") or draft.get("discarded"):
            raise AgentsError(f"{draft_id} is {'made' if draft.get('made') else 'discarded'}; "
                              f"start a new draft", 409)
        draft = _clean_fields(patch, draft, by)
        draft["rev"] += 1
        draft["updated"] = now_iso()
        draft["after_message"] = _newest_message(draft.get("thread"))
        draft["revisions"].append({"rev": draft["rev"], "at": draft["updated"],
                                   "by": by or lc.OWNER_ID, "fields": sorted(patch)})
        _write_json(draft_path(draft_id), draft)
    return shown(draft)


def discard(draft_id: str, by: str | None) -> dict:
    with locked():
        draft = read_draft(draft_id)
        if draft.get("made"):
            raise AgentsError(f"{draft_id} made {draft['made']}; there is nothing to "
                              f"discard", 409)
        draft["discarded"] = {"at": now_iso(), "by": by or lc.OWNER_ID}
        _write_json(draft_path(draft_id), draft)
    return shown(draft)


# ---------------------------------------------------------------------------
# The fleet's clauses. Every config.md carries the same protocol clauses
# (Permission first, where things live, ...), operator-authored and held
# byte-identical by bin/check-drift (`check_config_clauses`: a clause most
# configs carry must be in every one). A new agent's config is those clauses,
# copied verbatim from the configs on the box with its own name put in, then
# what the draft says that is its own. Nothing is composed but the joins.
# ---------------------------------------------------------------------------

OWN_RULES = "Its own rules"


def _drift_module():
    import importlib.machinery  # noqa: PLC0415
    import importlib.util  # noqa: PLC0415
    path = _runtime_bin("check-drift")
    loader = importlib.machinery.SourceFileLoader("loam_check_drift", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def fleet_clauses(root: Path | None = None) -> list[tuple[str, str]]:
    """[(title, masked body)] of every clause a majority of the configs on the
    box carry, each in its most common form, in the order the configs give
    them; ``(preamble)`` first. Masked as check-drift masks: ``<Agent>`` and
    ``<agent>`` for each agent's own name."""
    from collections import Counter  # noqa: PLC0415
    root = root or ai_labs()
    raw = {}
    for name in every(root):
        try:
            raw[name] = (root / name / "config.md").read_text(encoding="utf-8")
        except OSError:
            continue
    if not raw:
        return []
    drift = _drift_module()
    lines = Counter(l for text in raw.values() for l in set(text.splitlines()))
    # Lines most configs carry word for word are kept unmasked. One config
    # proves nothing is shared: on a new install, whose only agent is Iris,
    # every line counted, nothing was masked, and the next agent's config
    # said "# Iris — Config" and named her workspace (found 2026-11-16).
    shared = ({l for l, n in lines.items() if n >= len(raw) // 2 + 1}
              if len(raw) > 1 else set())
    import loam_profile  # noqa: PLC0415
    per = {n: drift.config_clauses(text, n, loam_profile.self_name(n, card(n, root)), shared)
           for n, text in raw.items()}
    majority = len(per) // 2 + 1
    order: list[str] = []
    for clauses in per.values():
        for title in clauses:
            if title not in order:
                order.append(title)
    out = []
    for title in order:
        present = [c[title] for c in per.values() if title in c]
        if len(present) < majority:
            continue
        body, _ = Counter(present).most_common(1)[0]
        out.append((title, body))
    return out


def compose_config(agent: str, own: str, root: Path | None = None,
                   name: str | None = None) -> tuple[str, list[str]]:
    """The config.md a new agent is made with, and the fleet clause titles in
    it. The draft's own clauses follow the fleet's; a clause of the draft
    with a fleet clause's title gives way to the fleet's."""
    fleet = fleet_clauses(root)
    # The name the agent's own files use (2026-11-14, L9): the one it is made with.
    display = name or agent.capitalize()

    def unmask(text: str) -> str:
        return text.replace("<Agent>", display).replace("<agent>", agent)

    titles = [t for t, _ in fleet]
    parts = []
    for title, body in fleet:
        text = unmask(body)
        parts.append(text if title == "(preamble)" else f"## {title}\n\n{text}")
    own_parts: list[str] = []
    title, buf = None, []
    lead = []
    for line in own.splitlines():
        if line.startswith("## "):
            if title is not None:
                own_parts.append((title, "\n".join(buf).strip()))
            elif buf:
                lead = buf
            title, buf = line[3:].strip(), []
        elif line.startswith("# ") and title is None and not buf:
            continue            # the draft's own heading; the fleet's preamble has one
        else:
            buf.append(line)
    if title is None:
        lead = buf
    else:
        own_parts.append((title, "\n".join(buf).strip()))
    lead_text = "\n".join(lead).strip()
    if lead_text:
        parts.append(f"## {OWN_RULES}\n\n{lead_text}")
    for t, body in own_parts:
        if t in titles or t == "(preamble)":
            continue
        parts.append(f"## {t}\n\n{body}" if body else f"## {t}")
    if not fleet:
        # A box with no agent to copy from: the draft's text as it is.
        return (own if own.endswith("\n") else own + "\n"), []
    return "\n\n".join(p.strip() for p in parts if p.strip()) + "\n", \
        [t for t in titles if t != "(preamble)"]


# ---------------------------------------------------------------------------
# Making the agent.
# ---------------------------------------------------------------------------

def free_wake() -> str:
    """The first twenty-minute slot from 01:00 to 05:40 at least ten minutes
    from every other agent's nightly wake."""
    import loam_schedule  # noqa: PLC0415
    taken = []
    for a in every():
        hhmm = loam_schedule.wake_time(a)
        if isinstance(hhmm, str) and re.fullmatch(r"\d{2}:\d{2}", hhmm):
            taken.append(int(hhmm[:2]) * 60 + int(hhmm[3:]))
    for minute in range(60, 6 * 60, 20):
        if all(abs(minute - t) >= 10 for t in taken):
            return f"{minute // 60:02d}:{minute % 60:02d}"
    return "05:50"


def _runtime_bin(name: str) -> Path:
    return Path(__file__).resolve().parent.parent / "bin" / name


def confirm(draft_id: str, payload: dict, by: str | None, *,
            provision=None) -> dict:
    """Make the agent a draft describes, on the person's yes to its current
    revision. ``provision(agent, wake)`` is a seam for the tests; the box's
    is bin/provision."""
    payload = payload or {}
    rev = payload.get("rev")
    with locked():
        draft = read_draft(draft_id)
        if draft.get("made"):
            raise AgentsError(f"{draft_id} already made {draft['made']}", 409)
        if draft.get("discarded"):
            raise AgentsError(f"{draft_id} was discarded", 409)
        if rev is None or rev != draft["rev"]:
            raise AgentsError(
                f"`rev`: the draft is at revision {draft['rev']}; confirm the "
                f"revision {lc.OWNER_NAME} said yes to", 409, {"draft": shown(draft)})
        problems = check_new(draft)
        if problems:
            raise AgentsError(f"{draft_id} cannot be made yet: {'; '.join(problems)}",
                              409, {"draft": shown(draft)})
        yes = check_yes(by, payload.get("thread") or draft.get("thread"),
                        payload.get("yes"), after=draft["updated"],
                        used=_used_yeses(),
                        after_message=((draft.get("thread"), draft.get("after_message"))
                                       if "after_message" in draft else None))
        agent = draft["id"]
        wake = draft.get("wake") or free_wake()
        made = make(agent, draft, wake, provision=provision)
        draft["made"] = agent
        draft["made_at"] = now_iso()
        draft["yes"] = yes
        _write_json(draft_path(draft_id), draft)
        log({"kind": "created", "agent": agent, "by": by or lc.OWNER_ID,
             "draft": draft_id, "rev": draft["rev"], "yes": yes,
             "thread": draft.get("thread"), "wake": wake})
    return {"agent": agent, "draft": shown(draft), **made}


def make(agent: str, draft: dict, wake: str, *, provision=None) -> dict:
    ws = ai_labs() / agent
    ws.mkdir(parents=True)
    for sub in ("memory", "skills", "crons", "threads"):
        (ws / sub).mkdir()
    (ws / "soul.md").write_text(draft["soul"], encoding="utf-8")
    # The map's shape (2026-11-18): soul, the draft's config as written, and
    # a map. The map goes first of the three loading ones, so provisioning
    # renders the new shape and seeds none of the old files.
    import loam_loading  # noqa: PLC0415
    (ws / loam_loading.MAP).write_text(
        draft.get("map") or loam_loading.MAP_SEED, encoding="utf-8")
    config_text = draft["config"] if draft["config"].endswith("\n") else draft["config"] + "\n"
    (ws / "config.md").write_text(config_text, encoding="utf-8")
    fleet: list[str] = []
    loam_loading.ensure_owner_page()
    config = {"name": agent, "display": draft["name"],
              "workspace": str(ws), "vault": str(lc.VAULT)}
    if draft.get("provider"):
        config["provider"] = draft["provider"]
    if draft["name"] != agent.capitalize():
        # Its own files say the name it is made with ("Old Sam", not the
        # id's "Old-sam"); a later rename is the app's display only.
        config["self_name"] = draft["name"]
    _write_agent(agent, config)
    try:
        output = (provision or _provision)(agent, wake)
    except Exception as exc:  # noqa: BLE001 -- the half-made agent leaves the roster
        aside = ws / "agent.json.failed"
        os.replace(ws / "agent.json", aside)
        log({"kind": "create_failed", "agent": agent, "error": str(exc)})
        raise AgentsError(
            f"{agent} could not be provisioned, so it is not on the roster "
            f"(its agent.json is now {aside.name}; its files are in {ws}): {exc}",
            500) from None
    return {"workspace": str(ws), "wake": wake, "provision": output,
            "fleet_clauses": fleet}


# bin/provision holds Loam's live lock, and waits up to 20 minutes for it
# behind a landing (lib/loam_lock.py, WAIT_SECONDS); found live 2026-11-14,
# when a 10-minute ceiling here cut a provision that was only waiting.
PROVISION_TIMEOUT = 1800


def _provision(agent: str, wake: str) -> str:
    done = subprocess.run([sys.executable, str(_runtime_bin("provision")), agent, wake],
                          capture_output=True, text=True, check=False,
                          timeout=PROVISION_TIMEOUT)
    if done.returncode != 0:
        raise RuntimeError((done.stderr or done.stdout).strip()[-2000:])
    return done.stdout.strip()[-4000:]


# ---------------------------------------------------------------------------
# Archive and restore.
# ---------------------------------------------------------------------------

def _reconcile(agent: str) -> list[dict]:
    import loam_schedule  # noqa: PLC0415
    return loam_schedule.reconcile([agent])


def archive(agent: str, payload: dict, by: str | None, *, busy=None,
            reconcile=None) -> dict:
    """Put an agent to sleep. ``busy(agent)`` is the broker's: a sentence when
    the agent is mid-turn or mid-run, else None."""
    payload = payload or {}
    if agent not in every():
        raise AgentsError(f"no agent named {agent!r} on this box", 404)
    if live(every()) == [agent]:
        raise AgentsError(f"{agent} is the last agent awake; archiving it would "
                          f"leave nobody to talk to", 409)
    with locked():
        config = card(agent)
        if config.get(ARCHIVE_KEY):
            raise AgentsError(f"{agent} is already archived "
                              f"({config[ARCHIVE_KEY].get('at')})", 409)
        yes = check_yes(by, payload.get("thread"), payload.get("yes"),
                        window=YES_WINDOW, used=_used_yeses())
        sentence = busy(agent) if busy else None
        if sentence and by != agent:
            raise AgentsError(f"{agent} is {sentence}; archive it once that ends", 409)
        import loam_schedule  # noqa: PLC0415 -- the agent.json writers' lock
        with loam_schedule.locked():
            config = card(agent)
            config[ARCHIVE_KEY] = {"at": now_iso(), "by": by or lc.OWNER_ID,
                                   **({"yes": yes} if yes else {})}
            _write_agent(agent, config)
        timers = (reconcile or _reconcile)(agent)
        event = log({"kind": "archived", "agent": agent, "by": by or lc.OWNER_ID,
                     "yes": yes, "timers": [t.get("unit") for t in timers]})
    return {"agent": agent, "archived": config[ARCHIVE_KEY], "event": event}


def restore(agent: str, payload: dict, by: str | None, *,
            reconcile=None) -> dict:
    payload = payload or {}
    if agent not in every():
        raise AgentsError(f"no agent named {agent!r} on this box", 404)
    with locked():
        config = card(agent)
        was = config.get(ARCHIVE_KEY)
        if not was:
            raise AgentsError(f"{agent} is not archived", 409)
        taken = _names_taken(except_agent=agent)
        shown_name = str(config.get("display") or "").lower()
        if shown_name and shown_name in taken:
            raise AgentsError(f"{config['display']!r} is {taken[shown_name]}'s name "
                              f"now; rename one of them first", 409)
        yes = check_yes(by, payload.get("thread"), payload.get("yes"),
                        window=YES_WINDOW, used=_used_yeses())
        import loam_schedule  # noqa: PLC0415
        with loam_schedule.locked():
            config = card(agent)
            config.pop(ARCHIVE_KEY, None)
            _write_agent(agent, config)
        timers = (reconcile or _reconcile)(agent)
        event = log({"kind": "restored", "agent": agent, "by": by or lc.OWNER_ID,
                     "yes": yes, "archived_at": was.get("at"),
                     "timers": [t.get("unit") for t in timers]})
    return {"agent": agent, "event": event}


def archived_cards() -> list[dict]:
    import loam_broker as cfg  # noqa: PLC0415
    out = []
    for a in archived():
        row = cfg.agent_card(a)
        row["archived"] = card(a).get(ARCHIVE_KEY)
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# The face.
# ---------------------------------------------------------------------------

def sniff(data: bytes) -> str | None:
    """The image type its first bytes say, of the four a face may be."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


def face_of(agent: str, config: dict | None = None) -> dict | None:
    """``{"type", "sha", "bytes", "at", "url"}`` for an agent with an uploaded
    face, else None. ``url`` is the broker's route; the page's relay serves it
    under its own prefix."""
    config = card(agent) if config is None else config
    face = config.get(FACE_KEY)
    if not isinstance(face, dict) or not face.get("file"):
        return None
    if not (ai_labs() / agent / face["file"]).is_file():
        return None
    return {"type": face.get("type"), "sha": face.get("sha"),
            "bytes": face.get("bytes"), "at": face.get("at"),
            "url": f"/v1/agents/{agent}/face?v={(face.get('sha') or '')[:12]}"}


def face_bytes(agent: str) -> tuple[bytes, str]:
    if agent not in every():
        raise AgentsError(f"no agent named {agent!r} on this box", 404)
    face = card(agent).get(FACE_KEY)
    if not isinstance(face, dict) or not face.get("file"):
        raise AgentsError(f"{agent} has no uploaded face", 404)
    try:
        return (ai_labs() / agent / face["file"]).read_bytes(), face.get("type") or ""
    except OSError:
        raise AgentsError(f"{agent}'s face file is gone", 404) from None


def _face_path_arg(path, by: str | None) -> bytes:
    p = Path(str(path)).expanduser()
    if not p.is_absolute() and by:
        p = ai_labs() / by / p
    p = p.resolve()
    roots = [ai_labs().resolve(), Path(lc.VAULT).resolve()]
    if not any(r in p.parents for r in roots):
        raise AgentsError("`path` must be a file in a workspace or the vault")
    if not p.is_file():
        raise AgentsError(f"`path`: no file at {p}")
    if p.stat().st_size > FACE_MAX:
        raise AgentsError(f"a face is {FACE_MAX // (1024 * 1024)} MB at most")
    return p.read_bytes()


def set_face(agent: str, data: bytes | None, by: str | None) -> dict:
    """An uploaded image as the agent's face, or ``None`` to go back to the
    theme's. The old file is removed when a new one lands."""
    if agent not in every():
        raise AgentsError(f"no agent named {agent!r} on this box", 404)
    import loam_schedule  # noqa: PLC0415
    with loam_schedule.locked():
        config = card(agent)
        old = config.get(FACE_KEY) if isinstance(config.get(FACE_KEY), dict) else None
        if data is None:
            config.pop(FACE_KEY, None)
        else:
            if len(data) > FACE_MAX:
                raise AgentsError(f"a face is {FACE_MAX // (1024 * 1024)} MB at most", 413)
            kind = sniff(data)
            if kind is None:
                raise AgentsError("a face is a PNG, JPEG, WebP or GIF image", 415)
            digest = hashlib.sha256(data).hexdigest()
            name = f"face.{FACE_TYPES[kind]}"
            target = ai_labs() / agent / name
            tmp = target.with_name(f".{name}.{os.getpid()}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, target)
            config[FACE_KEY] = {"file": name, "type": kind, "sha": digest,
                                "bytes": len(data), "at": now_iso(),
                                "by": by or lc.OWNER_ID}
        _write_agent(agent, config)
        if old and old.get("file") and (data is None or old["file"] != config[FACE_KEY]["file"]):
            (ai_labs() / agent / old["file"]).unlink(missing_ok=True)
    log({"kind": "face", "agent": agent, "by": by or lc.OWNER_ID,
         "face": face_of(agent)})
    return {"agent": agent, "face": face_of(agent)}


def face_from_payload(agent: str, payload: dict, by: str | None) -> dict:
    """The JSON form of the face route, for an agent's call through
    bin/settings: ``{"path": <an image file>}``, or ``{"path": null}``."""
    if "path" not in (payload or {}):
        raise AgentsError("send the image as multipart/form-data in a `file` part, "
                          "or JSON {\"path\": <an image file on this box>} "
                          "({\"path\": null} goes back to the theme's face)")
    if payload["path"] is None:
        return set_face(agent, None, by)
    return set_face(agent, _face_path_arg(payload["path"], by), by)
