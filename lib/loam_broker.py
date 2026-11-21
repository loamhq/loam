"""Shared broker configuration, state paths and public row formats. The host launches
sessions with the persona's workspace and login. Apps use the authenticated broker
interface described in broker/README.md.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import signal
import stat
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import loam_config as lc

import loam_provider_usage as usage_store
from loam_permissions import PERMISSION_MODE

AGENTS_ROOT = lc.ROOT
AI_LABS = lc.WORKSPACES
CLAUDE = str(lc.CLAUDE_BIN)
# The flags every broker session's launch line opens with, after the binary
# (``Session.argv`` in bin/session-broker says why each is load-bearing).
# Here so one authority holds them (the retired provider follower's smoke
# test shared it, 2026-11-11 to 11-19).
CLAUDE_STREAM_FLAGS = (
    "-p",
    "--input-format", "stream-json",
    "--output-format", "stream-json",
    "--include-partial-messages",
    "--verbose",
    "--permission-prompt-tool", "stdio",
    "--permission-prompts", "host",
    # Every tool runs unasked, deny rules still applied (2026-11-12,
    # lib/loam_permissions.py). On the line, not in the persona's settings:
    # 2.1.322 ignores `bypassPermissions` as a project `defaultMode` and
    # starts in `default`. AskUserQuestion still reaches the channel.
    "--permission-mode", PERMISSION_MODE,
)
# The CLI's own configuration directory, and the login every broker session
# uses in place. It is read here for one bit only -- whether a login exists --
# and no value from it ever leaves this box.
CLAUDE_HOME = lc.CLAUDE_HOME
CREDENTIALS_PATH = CLAUDE_HOME / ".credentials.json"
# The CLI's settings file, beside its directory (not in it). The login's
# account -- `oauthAccount.emailAddress` -- is kept here by `claude auth
# login`, and is what `claude auth status` prints as `email` (2.1.322,
# measured 2026-11-11). Read for that one field and nothing else.
CLAUDE_CONFIG_JSON = CLAUDE_HOME.parent / ".claude.json"

STATE_DIR = lc.STATE / "broker"
SESSION_DIR = STATE_DIR / "sessions"
TOKEN_PATH = STATE_DIR / "token"


def chosen_model(session_id: str) -> str | None:
    """Return the model explicitly requested for this conversation, or None. For adopted
    sessions, look up the transcript's CLI fork ID as well as the record ID. An
    unreadable record cannot establish that a model discrepancy was intentional.
    """
    try:
        rec = json.loads(
            (SESSION_DIR / f"{session_id}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        rec = None
        try:
            paths = list(SESSION_DIR.glob("*.json"))
        except OSError:
            paths = []
        for path in paths:
            try:
                cand = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(cand, dict) and cand.get("cli_session_id") == session_id:
                rec = cand
                break
    if not isinstance(rec, dict):
        return None
    model = rec.get("model")
    return model if isinstance(model, str) and model else None
# The last rate_limit_event seen on any session's stream, kept across a
# restart so the page is not blank until somebody happens to take a turn.
USAGE_PATH = STATE_DIR / "provider-usage.json"

# The interface, and the whole of how a container reaches it. See
# broker/README.md; the pattern is the personas' sight wall one network over
# (loam-sight, 172.30.0.0/24), which is the precedent this was built on
# rather than a second invention.
#
# A Unix socket is the obvious answer and is unavailable: handing one to a
# container means bind-mounting it, and the deployment layer refuses a bind
# mount absolutely (lib/loam_deploy.py, _loopback_port). So it is TCP on a
# Docker bridge the host owns: the broker binds ONLY to this network's
# gateway address, which no interface outside the box carries and which the
# proxy never publishes.
NETWORK = "loam-broker"
SUBNET = "172.31.0.0/24"
BIND_ADDR = "172.31.0.1"
PORT = 8932  # 8931 is the personas' browser; 8080/8081 are the apps.

# Binding to a bridge gateway is not by itself a wall: every other container
# on the box routes to the host, so 172.20.x could reach 172.31.0.1 too.
# These are the chain names the INPUT wall uses to say otherwise.
WALL_CHAIN = "LOAM-BROKER-HOST"
WALL_SERVICE = "loam-broker-firewall.service"
FIREWALL_SCRIPT = Path("/etc/loam/broker/firewall.sh")

SERVICE = "loam-session-broker.service"

# How many persona sessions may be alive at once, and the arithmetic.
#
# **Sessions are admitted by memory, not by count (2026-11-09).** The owner,
# 11-09: "I want to make sure that I can have as many sessions as I need when
# I'm trying to run different things in parallel, and I'm not bottlenecked by
# the number of sessions or anything else. And also, my sessions can grow to
# have as much RAM and size as they need."
#
# So a start asks the box (`memory()`): the kernel's MemAvailable, and the
# broker cgroup's own room under a MemoryMax if it has one (since 2026-11-16
# it has none, and that term is None), each less what the starts in flight
# will take. A start is admitted while that leaves `MEMORY_RESERVE_MB`
# for everything that is not a chat -- the system, sshd, Caddy, Authelia,
# docker, the persona browser, the engineer jobs and wakes
# yet to start -- plus `SESSION_START_MB` for the new process. Short, the
# broker ends the least recently used idle session and asks again; only when
# every session is busy is a start refused (`Broker.make_room`). An idle
# session leaves memory on its own after `CLASSIC_IDLE_S` or `THREAD_IDLE_S`
# and resumes by id. A session is not sized: it grows as large as it needs,
# and what keeps a runaway one from taking the box with it is the OOM order
# (`CHILD_OOM_SCORE_ADJ`), not a cap (the unit's MemoryMax went 2026-11-16).
def _box_mb() -> int:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return 7750


BOX_MB = _box_mb()

# What a session needs to start: the CLI plus the persona's stdio MCP
# servers, ~394-450 MB measured 2026-09-16 and 2026-10-14. A start is the
# only moment a size is assumed; after it, a session takes what it takes.
SESSION_START_MB = 450

# Kept free for everything that is not a chat: 2 GB, or 12% of RAM on a
# bigger box. 2,048 MB on the 7,750 MB CX33 (where ~2.6 GB of Remote Control servers,
# retired 2026-11-19, and ~1.2 GB of docker are already counted out of MemAvailable, and this is the
# room for a wake, an engineer job at its 1 GiB High and the browser to
# start), 3,870 MB on the 32 GB CX53.
MEMORY_RESERVE_MB = max(2048, BOX_MB * 12 // 100)

# Not a limit anybody should meet: a sanity backstop against a loop that
# starts sessions, at one per 256 MB of RAM. 30 on the CX33 (7,750 MB),
# 123 on the CX53 (~31,600 MB). Memory refuses long before either.
MAX_SESSIONS = max(16, BOX_MB // 256)

# Every chat's process tree is at the agents' place in the kernel's OOM order
# (2026-11-09; lib/loam_config.py since 11-16): each CLI the broker spawns is
# raised to this, which its MCP servers inherit, while the unit -- and so the
# broker's own process -- runs at the core's 100. So the killer takes a chat,
# a job or a wake (all 800) before the broker, and anything of the system's
# (0) after that. Raising one's own child is unprivileged; lowering is not,
# which is why the broker raises the chats rather than lowering itself.
CHILD_OOM_SCORE_ADJ = lc.AGENT_OOM_SCORE_ADJ


def _meminfo_mb(key: str) -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith(key + ":"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def _cgroup_room_mb() -> int | None:
    """MemoryMax less memory.current for this process's own cgroup, or None
    when there is no limit or no cgroup v2 to read."""
    try:
        rel = next(line.split("::", 1)[1].strip()
                   for line in Path("/proc/self/cgroup").read_text().splitlines()
                   if line.startswith("0::"))
        base = Path("/sys/fs/cgroup") / rel.lstrip("/")
        limit = (base / "memory.max").read_text().strip()
        if limit == "max":
            return None
        current = int((base / "memory.current").read_text().strip())
        return (int(limit) - current) // (1024 * 1024)
    except (OSError, ValueError, StopIteration):
        return None


def memory() -> dict:
    """The box as admission reads it, in MB: ``available`` (MemAvailable),
    ``cgroup_room`` (the broker's own room under its MemoryMax, or None), and
    ``reserve``. A test replaces this function to simulate a box."""
    return {"available": _meminfo_mb("MemAvailable"),
            "cgroup_room": _cgroup_room_mb(),
            "reserve": MEMORY_RESERVE_MB}


# A classic chat nobody has spoken to in this long, and that is not busy,
# leaves memory (2026-11-09; it was the hour below until then). Its id
# resumes, so ending it costs its next message a respawn of a few seconds;
# under pressure an idle chat is ended sooner, by `make_room`.
CLASSIC_IDLE_S = 1800

# No session is ended for its age (2026-11-10, the owner: "I don't want any
# limitations at all"). Until then a session with no sign of life for an
# hour was ended whatever it was doing -- mid-turn, waiting on its children
# -- as the backstop for a wedged turn; a two-hour command or a long
# subagent looks the same as a wedge from here, so the backstop went. The
# idle stops above and below end only a session with nothing outstanding,
# and a turn that has truly wedged is ended by a person from the app.

# A thread member's session is ended this long after its turn (2026-11-09).
# One person in two threads with two members each was the whole cap for an
# hour; a thread turn resumes its member's session by id on the next turn,
# and a resume is a spawn and a re-read of the CLI's own file, a few seconds,
# against the prompt cache's hour, which a fresh process still hits. A thread
# session mid-turn, waiting on its children or asking is never ended by this,
# however long it has been.
THREAD_IDLE_S = 600

# Short of memory, a start ends the least recently used idle session to make
# room instead of refusing (2026-11-09). One active within this many seconds
# is not idle for that purpose: a chat started a moment ago whose first
# message is on its way is not a seat to take.
EVICT_GRACE_S = 15

# A session paused on a permission card or a question is asking, not idle,
# and the idle reaper leaves it alone. When a request has waited this long
# with nobody answering, the request is withdrawn and not the process: the
# broker answers the CLI with a deny carrying PERMISSION_EXPIRED_MESSAGE, the
# turn finishes on its own, and the idle clock starts again from there.
# Until 2026-10-05 the reaper counted a raised card as the last sign of life
# and ended the chat an hour later, leaving its cards open in the log forever.
PERMISSION_WAIT_S = 3600
PERMISSION_EXPIRED_MESSAGE = ("Nobody answered this request within an hour. "
                              f"Ask {lc.OWNER_NAME} in words, or go on without it.")

# A turn has no clock (2026-11-10). `TURN_TIMEOUT_S` stood here, read by
# nothing since the classic path stopped using it; the thread engine's own
# 30-minute clock went the same day.

# A stop that has not finished this long after SIGTERM writes every thread's
# stack to stderr, and so to the journal, and then goes on waiting as it was
# (2026-10-13). systemd SIGKILLs at 90 s; four stops between 10-07 and 10-09
# ran the full 90 s with nothing captured. 20 s is well past a clean stop
# (1-2 s on this box, with `Session.stop` capped at 10 s a session) and leaves
# 70 s for a second look by hand. SHUTDOWN_FALLBACK_S is the dump that needs
# no Python, for a stop where no thread can take the GIL; the first dump
# cancels it, so a hang writes one dump and not two.
SHUTDOWN_DUMP_S = 20
SHUTDOWN_FALLBACK_S = 40

# The stop's own bounds (2026-11-10), so it ends inside systemd's 90 s with
# everything written. The 11-10 11:11 stop was SIGKILLed at 90 s: the thread
# engine had begun starting a session after SIGTERM, the task was cancelled
# inside `create_subprocess_exec`, and asyncio's cancel of it never returned.
# Now: no session starts once the stop begins; the live sessions are stopped
# together, SHUTDOWN_SESSIONS_S at most (each is capped at 10 s); each
# thread's turn gets SHUTDOWN_TASKS_S to write its last line; every other
# task is cancelled and given SHUTDOWN_TASKS_S; one that will not end is
# named in the journal and the broker exits without it. SHUTDOWN_EXIT_S is
# the backstop for anything after that (asyncio joining a worker thread with
# no timeout): the broker names what is still running and exits.
SHUTDOWN_SESSIONS_S = 15
SHUTDOWN_TASKS_S = 10
SHUTDOWN_EXIT_S = 60

# -- providers -------------------------------------------------------------
#
# The vendor accounts a session can run on. The owner's naming, 2026-09-18:
# these are **Providers**, not "coding accounts" -- the word appears on the
# page, in this interface and in the designs, and one word is the point.
#
# Three are wired: Claude Code, Codex since 2026-10-01 (its binary, home,
# login and models are lib/loam_codex.py), and Antigravity since 2026-11-09
# (lib/loam_antigravity.py). The other two are here because the
# page has a row for each and an empty row with no explanation is worse than a
# row that says why it is empty; they answer `connected: false` with a reason,
# not an error.
PROVIDERS = ("claude-code", "codex", "antigravity", "grok", "opencode")
PROVIDER_NAMES = {
    "claude-code": "Claude Code",
    "codex": "Codex",
    "antigravity": "Antigravity",
    "grok": "Grok",
    "opencode": "OpenCode",
}
WIRED_PROVIDERS = ("claude-code", "codex", "antigravity")
NOT_WIRED = "not wired"
DEFAULT_PROVIDER = "claude-code"

# How each wired provider signs in from the app (2026-11-11), so one card
# draws every provider: `paste-code` shows a link and takes a code back;
# `device-code` shows a link and a code to enter there, and finishes by
# itself. `label` is the button's words, in each provider's own terms (the
# account a person signs in with: Claude, ChatGPT, Google). `sign_out` is
# the route that removes the login from the box: every wired provider since
# the afternoon of 2026-11-11, because each CLI can remove its own login
# (`claude auth logout`, `codex logout`; Antigravity's file, which its CLI
# offers no headless way to remove).
SIGN_IN_KINDS = {"claude-code": "paste-code", "codex": "device-code",
                 "antigravity": "paste-code"}
SIGN_IN_LABELS = {"claude-code": "Sign in with Claude",
                  "codex": "Sign in with ChatGPT",
                  "antigravity": "Sign in with Google"}
SIGN_OUT_PROVIDERS = ("claude-code", "codex", "antigravity")


def sign_in(provider: str, account: str | None = None) -> dict | None:
    """The row's `sign_in` descriptor: the kind, the button's words, whose
    login it is (`account`, null when signed out or when nothing on the box
    names one), and every route by path; or None for a provider with no
    login to make."""
    kind = SIGN_IN_KINDS.get(provider)
    if kind is None:
        return None
    base = f"/v1/providers/{provider}/login"
    return {"kind": kind, "label": SIGN_IN_LABELS[provider],
            "account": account if isinstance(account, str) and account else None,
            "start": base, "status": base, "cancel": base,
            "code": f"{base}/code" if kind == "paste-code" else None,
            "sign_out": (f"/v1/providers/{provider}/logout"
                         if provider in SIGN_OUT_PROVIDERS else None)}

# -- the login, and making it again ----------------------------------------
#
# Which credential a session actually authenticates with. Answered on the
# provider row as `login.source` so a page can say "the file" or "the token"
# without knowing that either is a file or a token.
LOGIN_SOURCE_FILE = "credentials-file"
LOGIN_SOURCE_TOKEN = "oauth-token"
LOGIN_SOURCE_API_KEY = "api-key"
LOGIN_SOURCE_NONE = "none"

# What `claude auth login` is, as this box runs it. `--claudeai` is the
# default and is written out because the other branch (`--console`) bills an
# API account and is not what these sessions run on.
LOGIN_ARGV = ("auth", "login", "--claudeai")

# The CLI's own prompt, verbatim (2.1.270, read off a pty on 2026-09-22). The
# flow is "pending" from the moment this appears: the URL is out and the CLI
# is blocked on a person.
LOGIN_PROMPT = "Paste code here if prompted"

# How long the start route waits for the CLI to print its URL before it gives
# up and reports a flow that never began. Measured at well under a second on
# this box; the margin is for a cold node start.
LOGIN_URL_TIMEOUT_S = 25

# How long a flow nobody finishes is left holding a pty and a process. A
# person has to open a URL on a phone, sign in, and paste a code back, so it
# is generous; it exists so that an abandoned flow is not a process that lives
# until the next broker restart.
LOGIN_FLOW_TIMEOUT_S = 900

# How long the submit route waits for the CLI to answer a pasted code. The
# exchange is one HTTPS round trip; a code that has expired answers in about
# a second with a 400.
LOGIN_SUBMIT_TIMEOUT_S = 60

# The most of the CLI's own words this interface ever repeats, on a failure.
# Bounded because it is a terminal's buffer, not a message anybody designed.
LOGIN_MESSAGE_CHARS = 240

# A terminal's escapes, so that what the CLI drew can be read as text. The OSC
# branch is first and matters: the CLI writes its URL twice, once as an OSC-8
# hyperlink target (ESC ] 8 ; ; <url> BEL) and once as visible coloured text,
# and stripping the OSC sequence whole leaves exactly one URL to find.
ANSI_RE = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"      # OSC ... BEL or ST
    r"|\x1b\[[0-9;?]*[ -/]*[@-~]"             # CSI
    r"|\x1b[()][A-Za-z0-9]"                   # charset designation
    r"|\x1b[=>NOP\\]"                         # the short ones
    r"|[\x00-\x08\x0b\x0c\x0e-\x1a\x1c-\x1f]")  # control bytes, not \t \n \r

# A URL in that text. Deliberately not anchored on claude.com: the host is the
# CLI's to change and an anchored pattern would answer "no URL" on the day it
# did. The first match carrying `oauth` is the one.
URL_RE = re.compile(r"https://[^\s\"'<>\\^`{|}]+")


def plain(raw: bytes | str) -> str:
    """What a terminal would have shown, as text."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    return ANSI_RE.sub("", raw)


def login_url(raw: bytes | str) -> str | None:
    """The sign-in URL out of what the CLI drew, or None if it has not drawn
    one yet. Trailing punctuation is not stripped: the CLI prints the URL on
    a line of its own and inventing a rule for a full stop would one day cut
    a query string."""
    for match in URL_RE.finditer(plain(raw)):
        if "oauth" in match.group(0):
            return match.group(0)
    return None


def login_message(raw: bytes | str, secret: str | None = None) -> str | None:
    """The CLI's last word, for a failure a person has to act on -- e.g.
    ``Login failed: Request failed with status code 400``.

    The buffer it comes out of is a terminal the person's own pasted code was
    echoed into, so the code is removed before anything is answered. Nothing
    else from that buffer ever leaves this box.
    """
    text = plain(raw)
    if secret:
        for piece in (secret, secret.strip()):
            if piece:
                text = text.replace(piece, "<code>")
    lines = [line.strip() for line in text.replace("\r", "\n").splitlines()]
    for line in reversed(lines):
        # Not the prompt, and not the line the URL was drawn on: both are
        # things the CLI says to everybody, and neither tells a person why
        # this attempt did not work. The pasted code is echoed onto the
        # prompt line, which is a second reason that line is never the answer.
        if line and LOGIN_PROMPT not in line and "https://" not in line:
            return line[:LOGIN_MESSAGE_CHARS]
    return None

# -- models and efforts ----------------------------------------------------
#
# What the + menu offers for one chat: a model and an effort level, both of
# them the provider's own vocabulary and neither of them this runtime's
# invention.
#
# The aliases are the CLI's own list, read off the binary on this box
# (2.1.270, 2026-09-21): ``["sonnet","opus","haiku","fable","best",
# "sonnet[1m]","opus[1m]","fable[1m]","opusplan"]``. ``--model`` also takes a
# full model id (``claude-fable-5``), which is why ``model_ok`` below accepts
# one and this list does not enumerate them: ids age out with every release
# and a menu of stale ids is worse than four families that never move. The
# four families come first because they are what a person picks; the rest are
# after them and a client is free to draw only the head of the list.
#
# The display names are this interface's, not the CLI's -- the CLI has none.
CLAUDE_CODE_MODELS = (
    ("fable", "Fable"),
    ("opus", "Opus"),
    ("sonnet", "Sonnet"),
    ("haiku", "Haiku"),
    ("best", "Best available"),
    ("opusplan", "Opus while planning, Sonnet after"),
    ("fable[1m]", "Fable (1M context)"),
    ("opus[1m]", "Opus (1M context)"),
    ("sonnet[1m]", "Sonnet (1M context)"),
)
PROVIDER_MODELS = {"claude-code": CLAUDE_CODE_MODELS}

# What each alias runs on today (since 2026-11-10): `resolves_to` on the
# providers row. The installed CLI carries its alias table in its own build,
# a baked catalog of the form
#   aliases:{opus:{default:"claude-opus-5-5",per_provider:{…}},…,
#            fable:{default:"claude-fable-5-2",…}},defaults:{},best:"opus",
# so it is read straight out of the binary the symlink at CLAUDE points to:
# no session, no model call, ~0.25 s over a 340 MB file, done once per build
# and kept until the symlink's target (or that file's size or mtime) changes.
# This is the table the 2.1.319 → 2.1.322 bump diffed: 2.1.319's `fable` was
# claude-fable-5-1, 2.1.322's is claude-fable-5-2 (and its `best` moved from
# fable to opus). `per_provider` is not read: these sessions are first-party.
# A build whose table cannot be found answers `resolves_to: null`, never a
# guess; the menu works the same without it.
ALIAS_TABLE_RE = re.compile(rb'aliases:\{\w+:\{default:"')
ALIAS_ENTRY_RE = re.compile(rb'[{,](\w+):\{default:"([^"]+)"')
ALIAS_BEST_RE = re.compile(rb'best:"(\w+)"')
_ALIASES: dict = {}


def alias_table(binary: str | Path | None = None) -> dict:
    """`{"version", "binary", "aliases": {family: id}, "best"}` of the
    installed CLI, read once per build (see ALIAS_TABLE_RE). `aliases` is {}
    when the build has no table this can read."""
    import mmap  # noqa: PLC0415

    link = Path(binary or CLAUDE)
    try:
        target = link.resolve(strict=True)
        info = target.stat()
    except OSError:
        return {"version": None, "binary": None, "aliases": {}, "best": None}
    key = (str(target), info.st_size, info.st_mtime_ns)
    if _ALIASES.get("key") == key:
        return _ALIASES["table"]
    aliases, best = {}, None
    try:
        with target.open("rb") as handle, mmap.mmap(
                handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
            found = ALIAS_TABLE_RE.search(data)
            if found:
                segment = data[found.start():found.start() + 4000]
                segment = segment.split(b"alias_migration")[0]
                head = segment.split(b"defaults:")[0]
                aliases = {k.decode(): v.decode()
                           for k, v in ALIAS_ENTRY_RE.findall(head)}
                match = ALIAS_BEST_RE.search(segment)
                best = match.group(1).decode() if match else None
    except (OSError, ValueError):
        aliases, best = {}, None
    table = {"version": target.name, "binary": str(target),
             "aliases": aliases, "best": best}
    _ALIASES.update(key=key, table=table)
    return table


def resolves_to(alias: str, table: dict | None = None) -> str | None:
    """The concrete model id `alias` runs on with the installed CLI, or None
    when the table does not say. A `[1m]` alias is the same model with the
    long context; `best` is whichever family the build names; `opusplan` is
    opus while planning (its `after_plan` is `resolves_after_plan`)."""
    table = table if table is not None else alias_table()
    aliases = table.get("aliases") or {}
    base = alias.removesuffix("[1m]")
    if base == "best":
        base = table.get("best") or ""
    if base == "opusplan":
        base = "opus"
    if base in aliases:
        return aliases[base]
    return alias if alias.startswith("claude-") else None


def resolves_after_plan(alias: str, table: dict | None = None) -> str | None:
    """`opusplan`'s model once planning is over (sonnet's); None otherwise."""
    if alias != "opusplan":
        return None
    table = table if table is not None else alias_table()
    return (table.get("aliases") or {}).get("sonnet")

# ``--effort <level>``, and the CLI's help states exactly these five. There is
# a sixth, ``ultracode``, and it is deliberately not here: the CLI pins it
# behind xhigh and refuses it outside an interactive terminal ("Run /effort
# ultracode in an interactive terminal to release the pin"), so offering it on
# a surface that is neither would be offering something that cannot be had.
EFFORTS = ("low", "medium", "high", "xhigh", "max")

# The persona's own settings name a model and nothing else; this is the
# fallback when the file cannot be read, and it is what bin/provision writes.
FALLBACK_MODEL = "fable"

# The last top-level assistant text, cut to this, for the home screen's status
# box. The design clamps it to two lines at 15px, so a few hundred characters
# is already more than the box can draw; the cut is here rather than in the
# app because every client would otherwise make its own guess, and because
# this string rides on every record on the live list.
LAST_MESSAGE_CHARS = 400

# A session with no title yet wears the opening words of its first message,
# cut to this. The same number the transcript titler uses for a filename, so
# the stand-in and the real title are the same size on the page.
TITLE_FALLBACK_CHARS = 60

# How long the broker waits, after a turn ends, for the transcript writer to
# file this conversation and name it. The writer runs on the same Stop hook
# this turn's end fires, and its titler is a model call with a 45s budget, so
# one read straight after the result would miss on the first turn every time.
# Seconds to wait before each attempt; a session still unnamed when they are
# exhausted is tried again at the end of the next turn, which is the cheapest
# retry there is. Reads of one small file, so the cost of being generous here
# is nothing.
TITLE_POLL_WAITS = (2, 4, 8, 15, 20, 30)

# -- keeping an adopted session in step with the app's own file ------------
#
# A session adopted from the Claude Code app is a conversation the owner may
# still be having somewhere else, and the record is a reading of a file
# another process is appending to. Added 2026-09-21, because the app showed a
# persona's conversation as it had been hours earlier. (`FOLLOW_POLL_S`, how often
# the file behind an OPEN adopted classic chat was stat'ed, retired with the
# classic view on 2026-11-12; `SCAN_INTERVAL_S` below still holds.)

def context_used(usage: dict | None) -> int | None:
    """How many tokens the model's context held at one request, from the CLI's
    own usage numbers: what was sent fresh, what was read from cache and what
    was written to it.

    Here rather than in the broker because two readers need the same
    arithmetic over the same three keys -- a live turn's last assistant
    message, and an adopted session's, which `lib/loam_adopt.py` translates
    out of the CLI's own session file. Two copies of a sum is two answers to
    one question the day somebody changes one.

    Output tokens are deliberately not in it. The context a request occupied
    is what went IN; the answer that came back is in the next request's
    numbers, which is where a page would double-count it.

    ``None`` when the numbers are not there at all -- a usage object the CLI
    did not state is not a context to invent -- and the sum of whichever keys
    ARE there otherwise, because a missing key is a zero the envelope simply
    did not bother to write.
    """
    if not isinstance(usage, dict):
        return None
    total = 0
    found = False
    for key in ("input_tokens", "cache_read_input_tokens",
                "cache_creation_input_tokens"):
        value = usage.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total += int(value)
            found = True
    return total if found else None


# -- background tasks behind a turn ----------------------------------------
#
# A persona can launch a subagent that goes on running after the turn that
# launched it has ended. The CLI states the live ones on its own stream
# (`system`/`background_tasks_changed`) and notifies when one stops
# (`system`/`task_notification`), and between those two events there is a
# moment where a task has left the list and its notification has not yet
# arrived. The session is still working in that moment, so the record says so.
#
# This is the outer bound on that moment. A task that leaves the CLI's list
# and never notifies -- a shape nothing on this box has produced, and one no
# reading of the stream can rule out -- must not hold a row on `working`
# forever, which is the failure the whole point is about, inverted. Generous
# because the notification has always arrived in the same breath: it is a
# backstop, not a wait anybody is meant to feel.
BACKGROUND_SETTLE_S = 20

# The floor between two scans for app sessions the broker has never seen, when
# the scan is triggered by a read of the list rather than by a restart or the
# route. Measured on this box 2026-09-21: a scan that looks only for unknown
# files is 0.24 s over 576 files, and it runs on the event loop -- so it is
# paid at most once a minute and never on the read that follows it.
SCAN_INTERVAL_S = 60


# -- attachments -----------------------------------------------------------
#
# A file a person attached to a chat, on its way to the persona reading that
# chat. Added 2026-09-21 for the app's composer; the numbers and the list are
# the interface, so they are stated here once and documented in
# broker/README.md rather than written into the route.

# The vault, which is where an attachment lands. Not the persona's workspace:
# a file a person hands to a conversation is a thing they own, it outlives the
# session, and every persona already reads the vault. The app that uploaded it
# names the directory under this, so two front ends never share a folder.
VAULT = lc.VAULT
ATTACHMENT_ROOT = VAULT / lc.vault_folder("attachments")

# The biggest file the interface accepts, and the biggest image it will put in
# front of the model inline. They are two numbers because they are two limits:
# the first is this box's (a browser upload held in memory, then written down),
# the second is the API's own per-image ceiling. A bigger image is still stored
# and still named by its path in the message -- the persona can Read it -- it
# is simply not base64'd into the turn.
ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024
INLINE_IMAGE_MAX_BYTES = 5 * 1024 * 1024

# What the wire may carry, keyed by the extension, because the extension is the
# one thing both the persona's Read and the model's image block agree on. The
# browser's own content-type is not trusted for this: it is whatever the
# operating system guessed, and on a phone it is often nothing at all.
#
# Images first -- these four are what the model can be shown inline -- then
# documents, then the text and code a persona is asked to read.
ATTACHMENT_TYPES = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "webp": "image/webp",

    "mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm",

    "pdf": "application/pdf",
    "docx": ("application/vnd.openxmlformats-officedocument"
             ".wordprocessingml.document"),
    "xlsx": ("application/vnd.openxmlformats-officedocument"
             ".spreadsheetml.sheet"),
    "pptx": ("application/vnd.openxmlformats-officedocument"
             ".presentationml.presentation"),
    "odt": "application/vnd.oasis.opendocument.text",
    "epub": "application/epub+zip",
    "rtf": "application/rtf",
    # An export from another AI service arrives as one ZIP (the library skill
    # loam-bringing-context-from-another-ai). Stored as sent, never unpacked
    # here; the agent inspects it without extracting.
    "zip": "application/zip",

    "txt": "text/plain", "md": "text/markdown", "markdown": "text/markdown",
    "csv": "text/csv", "tsv": "text/tab-separated-values",
    "log": "text/plain", "json": "application/json",
    "yaml": "application/yaml", "yml": "application/yaml",
    "toml": "application/toml", "ini": "text/plain", "cfg": "text/plain",
    "html": "text/html", "xml": "application/xml", "css": "text/css",
    "diff": "text/x-diff", "patch": "text/x-diff", "sql": "application/sql",
    "py": "text/x-python", "js": "text/javascript", "mjs": "text/javascript",
    "ts": "text/x-typescript", "tsx": "text/x-typescript",
    "jsx": "text/javascript", "sh": "text/x-shellscript",
    "bash": "text/x-shellscript", "c": "text/x-c", "h": "text/x-c",
    "cpp": "text/x-c++", "hpp": "text/x-c++", "rs": "text/x-rust",
    "go": "text/x-go", "rb": "text/x-ruby", "java": "text/x-java",
    "kt": "text/x-kotlin", "swift": "text/x-swift", "php": "text/x-php",
}

# The extensions whose bytes are sent to the model as an image block. A subset
# of the above and not a guess: these are the four media types the API takes.
INLINE_IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")


def attachment_type(name: str) -> str | None:
    """The media type this interface will store ``name`` as, or None if it
    will not. The extension decides; see ATTACHMENT_TYPES."""
    _, dot, ext = name.rpartition(".")
    if not dot:
        return None
    return ATTACHMENT_TYPES.get(ext.lower())


def sanitise_name(raw: str) -> str:
    """A file name that is a name and not a path.

    The browser hands over whatever the person's disk called the file, and a
    name is the one part of an upload this box puts into a path of its own. So
    the directory parts go (both separators -- a Windows browser sends
    backslashes), then anything that is not a letter, a digit, a dot, a dash or
    an underscore becomes a dash, then the leading dots go, so an upload cannot
    write a dotfile or climb with `..`. Never empty, and never longer than a
    file name may be.
    """
    name = raw.replace("\\", "/").rsplit("/", 1)[-1].strip()
    name = "".join(c if (c.isalnum() and c.isascii()) or c in "._-" else "-"
                   for c in name)
    name = name.lstrip(".") or "file"
    if len(name) > 120:
        stem, dot, ext = name.rpartition(".")
        name = (stem[:120 - len(ext) - 1] + dot + ext) if dot else name[:120]
    return name


def attachment_dir(app: str, day: str | None = None, *, root: Path | None = None) -> Path:
    """Where today's uploads from one front end go. The day is in the path
    because a folder of every file anyone ever attached is a folder nobody can
    read; the app is in it because two front ends must not collide."""
    day = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return (ATTACHMENT_ROOT if root is None else root) / sanitise_name(app) / day


def free_path(directory: Path, name: str) -> Path:
    """``directory/name``, or the same name with ``-1``, ``-2`` before the
    extension if it is taken. Two people attaching `screenshot.png` on the
    same day must not overwrite each other, and the second one must not be
    told a lie about which file the persona is about to read."""
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    for n in range(1, 1000):
        candidate = directory / (f"{stem}-{n}.{ext}" if ext else f"{stem}-{n}")
        if not candidate.exists():
            return candidate
    raise OSError(f"too many files named like {name}")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch_iso(seconds) -> str | None:
    """A rate_limit_event's reset time, which arrives as a unix epoch, as the
    UTC string every other time on this interface is written in. The epoch is
    answered beside it: a page that wants to count down wants the number."""
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
        return None
    return datetime.fromtimestamp(float(seconds), timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def login_source(env: dict | None = None) -> str:
    """Identify the credential a child inherits from the broker. The CLI checks the API key
    first, then the OAuth token, then the login file.
    """
    env = os.environ if env is None else env
    if env.get("ANTHROPIC_API_KEY"):
        return LOGIN_SOURCE_API_KEY
    if env.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return LOGIN_SOURCE_TOKEN
    if CREDENTIALS_PATH.is_file():
        return LOGIN_SOURCE_FILE
    return LOGIN_SOURCE_NONE


def login_state(env: dict | None = None) -> dict:
    """Read the current login file on every call because the CLI refreshes it in place.
    Report refresh-token expiry in whole days rounded down, negative after expiry and
    unknown for sources without an expiry. Keep token values out of the result and logs.
    """
    source = login_source(env)
    if source in (LOGIN_SOURCE_API_KEY, LOGIN_SOURCE_TOKEN):
        # Nothing here can read an expiry out of an opaque token, and saying
        # "expires never" would be a lie with a date on it.
        return {"source": source, "expires_at": None, "days_left": None,
                "connected": True, "reason": None}
    try:
        data = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"source": LOGIN_SOURCE_NONE, "expires_at": None,
                "days_left": None, "connected": False,
                "reason": "no login on the box"}
    except (OSError, json.JSONDecodeError) as exc:
        return {"source": source, "expires_at": None, "days_left": None,
                "connected": False,
                "reason": f"the login could not be read ({type(exc).__name__})"}
    oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
    oauth = oauth if isinstance(oauth, dict) else {}
    if not oauth.get("accessToken"):
        return {"source": source, "expires_at": None, "days_left": None,
                "connected": False, "reason": "the login holds no token"}
    expiry = oauth.get("refreshTokenExpiresAt")
    if expiry is None and not oauth.get("refreshToken"):
        expiry = oauth.get("expiresAt")
    if not isinstance(expiry, (int, float)) or isinstance(expiry, bool):
        # A login with no date on it. Connected, and honest that it cannot
        # say when that stops being true.
        return {"source": source, "expires_at": None, "days_left": None,
                "connected": True, "reason": None}
    # The file's timestamps are milliseconds; every other epoch on this
    # interface is seconds.
    seconds = expiry / 1000.0
    left = int((seconds - time.time()) // 86400)
    state = {"source": source, "expires_at": epoch_iso(seconds),
             "days_left": left, "connected": True, "reason": None}
    if seconds <= time.time():
        state["connected"] = False
        state["reason"] = "the login has expired and must be made again"
    return state


_account_cache: tuple[tuple, str | None] | None = None


def claude_code_account() -> str | None:
    """The email of the Claude login the box holds, out of the CLI's own
    settings file (``oauthAccount.emailAddress``, which ``claude auth
    status`` prints as ``email``): a local read, no process, no network.
    Keyed on the file's mtime and size, so the providers route does not parse
    150 KB on every call. Nothing else in the file is read or kept."""
    global _account_cache
    try:
        st = CLAUDE_CONFIG_JSON.stat()
    except OSError:
        return None
    key = (st.st_mtime_ns, st.st_size)
    if _account_cache is not None and _account_cache[0] == key:
        return _account_cache[1]
    try:
        data = json.loads(CLAUDE_CONFIG_JSON.read_text(encoding="utf-8"))
        oauth = data.get("oauthAccount") if isinstance(data, dict) else None
        email = oauth.get("emailAddress") if isinstance(oauth, dict) else None
        account = email if isinstance(email, str) and email else None
    except (OSError, ValueError):
        account = None
    _account_cache = (key, account)
    return account


def claude_code_row_login(state: dict | None = None) -> dict:
    """The Claude row's ``login`` object. ``account`` is named only when the
    sessions run on the login file and it is connected: under a token or a
    key in the environment, the settings file's account may be somebody
    else's, and a signed-out file names nobody."""
    state = login_state() if state is None else state
    account = (claude_code_account() if state["connected"]
               and state["source"] == LOGIN_SOURCE_FILE else None)
    return {"expires_at": state["expires_at"],
            "days_left": state["days_left"],
            "source": state["source"], "account": account}


LOGOUT_TIMEOUT_S = 60


def claude_code_sign_out() -> dict:
    """Run claude auth logout for app sessions. Wakes, crons and jobs using a separately
    supplied token retain it. Report whether the credential file disappeared and include
    the CLI's message on failure.
    """
    existed = CREDENTIALS_PATH.is_file()
    message = None
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")}
    try:
        done = subprocess.run([CLAUDE, "auth", "logout"], env=env,
                              stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=LOGOUT_TIMEOUT_S)
        if done.returncode != 0:
            message = login_message(done.stdout + "\n" + done.stderr) or (
                f"claude auth logout exited {done.returncode}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        message = f"claude auth logout did not finish ({type(exc).__name__})"
    out = {"removed": existed and not CREDENTIALS_PATH.is_file(),
           "login": claude_code_row_login()}
    if message:
        out["message"] = message
    return out


def claude_code_login() -> tuple[bool, str | None]:
    """``(connected, reason)`` for the one wired provider, which is what the
    provider row has carried since before there was a date on it. The whole
    reading is ``login_state`` above; this is the two fields of it that the
    row states at the top level."""
    state = login_state()
    return state["connected"], state["reason"]


def read_usage() -> dict | None:
    """The last rate limit reading seen, or None if none has been."""
    try:
        return json.loads(USAGE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_usage(usage: dict) -> None:
    usage_store.write_json(USAGE_PATH, usage)


def keep_usage_event(event: dict, session_id: str | None = None) -> dict | None:
    """``observe``: a ``rate_limit_event`` merged over the kept reading and
    kept, or None when it carries no windows. The CLI states every window it
    has on each event, so the merge only matters over a read: a window the
    read named and the event does not is kept until the next read."""
    usage = usage_from_event(event, session_id)
    if usage is None:
        return None
    usage = usage_store.merge(read_usage(), usage)
    write_usage(usage)
    return usage


def usage_from_event(event: dict, session_id: str | None = None) -> dict | None:
    """Return the rate-limit windows by name, or None if there are none. Copy unfamiliar
    names too so new provider windows reach clients.
    """
    info = event.get("rate_limit_info")
    if not isinstance(info, dict):
        return None
    windows = info.get("unifiedWindows")
    if not isinstance(windows, dict):
        return None
    out: dict = {
        "provider": DEFAULT_PROVIDER,
        "seen_at": now_iso(),
        "source": "event",
        "session_id": session_id,
        "status": info.get("status"),
        "is_using_overage": info.get("isUsingOverage"),
    }
    for name, window in windows.items():
        if not isinstance(window, dict):
            continue
        out[name] = {
            "utilization": window.get("utilization"),
            "resets_at": epoch_iso(window.get("resetsAt")),
            "resets_at_epoch": window.get("resetsAt"),
        }
    return out



# -- usage on demand: the CLI's own /usage ---------------------------------
#
# Found 2026-10-14 in 2.1.294: the stream-json control channel answers
# ``{"subtype": "get_usage"}`` with what ``/usage`` draws, before and without
# any turn -- ``total_cost_usd`` 0 and ``total_api_duration_ms`` 0 on the
# probe, so no model call and nothing against the meter. The SDK names its
# wrapper ``usage_EXPERIMENTAL_MAY_CHANGE_DO_NOT_RELY_ON_THIS_API_YET``, so
# the mapping below takes only what it recognises and a changed answer is a
# failed read, which keeps the reading as it was. ``skip_behaviors`` drops
# the per-day request breakdown, which the row has no use for.
#
# What it answers, abridged: ``rate_limits.{five_hour, seven_day, ...}`` with
# ``utilization`` a PERCENT (2, where the event says 0.02) and ``resets_at``
# an ISO time; ``rate_limits.limits``, a list in which a model's own weekly limit is
# ``{kind: "weekly_scoped", scope: {model: {display_name}}, percent}``;
# ``subscription_type``. The windows are named as the event names them and
# the model's weekly as ``seven_day_<model>``.

USAGE_READ_ARGV = ("-p", "--input-format", "stream-json",
                   "--output-format", "stream-json", "--verbose",
                   "--no-session-persistence", "--strict-mcp-config")
USAGE_READ_TIMEOUT_S = 30


def _iso_epoch(value) -> tuple[str | None, int | None]:
    if not isinstance(value, str):
        return None, None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None, None
    seconds = int(moment.timestamp())
    return epoch_iso(seconds), seconds


def _read_window(percent, resets) -> dict:
    iso, epoch = _iso_epoch(resets)
    return {"utilization": percent / 100 if isinstance(percent, (int, float))
            and not isinstance(percent, bool) else None,
            "resets_at": iso, "resets_at_epoch": epoch}


def usage_from_read(response: dict) -> dict | None:
    """A ``get_usage`` answer as a whole new reading, or None when it holds
    no rate limits this mapping recognises."""
    if not isinstance(response, dict) or not response.get("rate_limits_available"):
        return None
    limits = response.get("rate_limits")
    if not isinstance(limits, dict):
        return None
    out: dict = {"provider": DEFAULT_PROVIDER, "seen_at": now_iso(),
                 "source": "read", "session_id": None, "status": None,
                 "is_using_overage": None,
                 "planType": response.get("subscription_type")}
    for name, window in limits.items():
        if isinstance(window, dict) and "utilization" in window \
                and "resets_at" in window:
            out[name] = _read_window(window["utilization"], window["resets_at"])
    for limit in limits.get("limits") or ():
        if not isinstance(limit, dict) or limit.get("kind") != "weekly_scoped":
            continue
        model = ((limit.get("scope") or {}).get("model") or {}).get("display_name")
        if isinstance(model, str) and model:
            slug = re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_")
            out[f"seven_day_{slug}"] = _read_window(limit.get("percent"),
                                                    limit.get("resets_at"))
    if "five_hour" not in out and "seven_day" not in out:
        return None
    return out


def claude_control_call(request: dict,
                        timeout: float = USAGE_READ_TIMEOUT_S) -> dict | None:
    """One control request to a short-lived ``claude -p`` in stream-json, on
    the box's login, with no prompt ever sent: its ``response``, or None.
    Blocking, for a thread; the process group is ended whatever happens."""
    env = dict(os.environ)
    env["DISABLE_AUTOUPDATER"] = "1"
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("LOAM_WAKE", None)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([CLAUDE, *USAGE_READ_ARGV], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            cwd=str(STATE_DIR), env=env, text=True,
                            start_new_session=True)
    try:
        assert proc.stdin is not None and proc.stdout is not None
        for request_id, body in (("init", {"subtype": "initialize"}),
                                 ("ask", request)):
            proc.stdin.write(json.dumps({"type": "control_request",
                                         "request_id": request_id,
                                         "request": body}) + "\n")
        proc.stdin.flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            answer = obj.get("response") if obj.get("type") == "control_response" else None
            if isinstance(answer, dict) and answer.get("request_id") == "ask":
                body = answer.get("response")
                return body if answer.get("subtype") == "success" \
                    and isinstance(body, dict) else None
        return None
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.wait()


def read_claude_usage() -> dict | None:
    """``read``: the CLI's ``/usage``, kept in place of the last reading and
    answered. None, and the file untouched, when there is no login or the
    answer is not one this mapping knows."""
    if not login_state()["connected"]:
        return None
    usage = usage_from_read(claude_control_call(
        {"subtype": "get_usage", "skip_behaviors": True}))
    if usage is None:
        return None
    write_usage(usage)
    return usage


def usage_reader() -> usage_store.Reader:
    return usage_store.Reader(DEFAULT_PROVIDER, observe=keep_usage_event,
                              kept=read_usage, read=read_claude_usage)


def read_token() -> str:
    """Read the shared broker secret from runtime state, creating it on first use. The
    deployment supplies it to the app separately.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not TOKEN_PATH.exists():
        TOKEN_PATH.write_text(secrets.token_urlsafe(32), encoding="utf-8")
    os.chmod(TOKEN_PATH, stat.S_IRUSR | stat.S_IWUSR)
    return TOKEN_PATH.read_text(encoding="utf-8").strip()


def roster(archived: bool = False) -> list[str]:
    """Return workspace IDs with an agent.json. Exclude archived agents from pickers and
    new sessions. Use archived=True for file and profile readers.
    """
    names = sorted(
        d.name for d in AI_LABS.iterdir()
        if d.is_dir() and (d / "agent.json").is_file()
    )
    if archived:
        return names
    import loam_agents  # noqa: PLC0415
    return loam_agents.live(names, AI_LABS)


def agent_card(name: str) -> dict:
    """Return id, name and face only. Prefer the configured display name and use the
    capitalized ID as fallback. A null face tells the client to use its theme.
    """
    try:
        config = json.loads(
            (AI_LABS / name / "agent.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        config = {}
    display = config.get("display")
    import loam_agents  # noqa: PLC0415
    return {
        "id": name,
        "name": display if isinstance(display, str) and display.strip()
        else name.capitalize(),
        # An uploaded face (2026-11-14): over any theme's.
        "face": loam_agents.face_of(name, config) if isinstance(config, dict) else None,
    }


def roster_cards() -> list[dict]:
    """Return the roster as the objects served by GET /v1/agents."""
    return [agent_card(name) for name in roster()]


def workspace(agent: str) -> Path:
    """The persona's own directory, which is the session's working directory.
    A session started anywhere else is a different agent wearing the name."""
    return AI_LABS / agent


def persona_model(agent: str | None = None) -> str | None:
    """Return the selected persona's Claude default for the provider menu. With no persona,
    return the common default or None when they differ. The CLI reads the provisioned
    setting when a launch omits --model.
    """
    if agent is None:
        found = {persona_model(name) for name in roster()}
        found.discard(None)
        return found.pop() if len(found) == 1 else None
    import loam_profile  # noqa: PLC0415
    try:
        card = json.loads((workspace(agent) / "agent.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loam_profile.claude_model(card)


def model_ok(value: str) -> bool:
    """Whether ``--model`` would take this. An alias from the list above, or
    a full model id, which the CLI's help says it accepts alongside them --
    refusing one would make this interface narrower than the tool behind it
    for no reason anybody could see from the page."""
    return (any(value == alias for alias, _ in CLAUDE_CODE_MODELS)
            or bool(re.fullmatch(r"claude-[a-z0-9][a-z0-9.\-]*", value)))


# ---------------------------------------------------------------------------
# The chats a restart ended.
#
# Every session ends with the broker (`amain`'s last loop), so a restart ends
# every chat that was resident, and each one's event log gets a `broker.exit`
# stamped inside the unit's stop. Nothing else says which chats those were:
# the record is resumed and rewritten afterwards, and `broker.exit` is the
# same event the idle reaper and DELETE write. What tells them apart is when:
# systemd's "Stopping" and the "Started" after it bracket the stop, and an
# exit inside that bracket is one the stop caused. The 10-07 22:13Z restart:
# Stopping 22:13:50.956Z, Started 22:13:52.172Z, exits at 22:13:51Z and
# 22:13:52Z (the event's stamp is to the second, so the bracket is widened to
# whole seconds).
_ONE_SECOND = timedelta(seconds=1)


def _utc(stamp: str) -> datetime:
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def broker_stops(since: datetime, until: datetime,
                 entries: list[dict] | None = None) -> list[tuple[datetime, datetime]]:
    """Each stop of the broker's unit between ``since`` and ``until``, as
    (stopping, started-again) -- ``until`` when it had not started again.

    ``entries`` are journal entries as `journalctl -o json` gives them, for a
    test; by default the user journal is read. Best-effort: an unreadable
    journal is no stops, and the receipt then lists nothing rather than
    failing."""
    if entries is None:
        import subprocess  # noqa: PLC0415
        try:
            out = subprocess.run(
                ["journalctl", "--user", "-u", SERVICE, "-o", "json", "--no-pager",
                 "--since", f"@{int(since.timestamp())}",
                 "--until", f"@{int(until.timestamp()) + 1}"],
                capture_output=True, text=True, check=False, timeout=30,
                stdin=subprocess.DEVNULL).stdout
        except (OSError, subprocess.TimeoutExpired):
            return []
        entries = []
        for line in out.splitlines():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    stops: list[tuple[datetime, datetime]] = []
    opened: datetime | None = None
    for entry in entries:
        message = entry.get("MESSAGE")
        if not isinstance(message, str):
            continue
        try:
            at = datetime.fromtimestamp(int(entry["__REALTIME_TIMESTAMP"]) / 1e6,
                                        timezone.utc)
        except (KeyError, TypeError, ValueError):
            continue
        if message.startswith("Stopping ") and opened is None:
            opened = at
        elif message.startswith("Started ") and opened is not None:
            stops.append((opened, at))
            opened = None
    if opened is not None:
        stops.append((opened, until))
    return stops


def ended_by_restart(since: datetime, until: datetime,
                     stops: list[tuple[datetime, datetime]] | None = None,
                     session_dir: Path | None = None) -> list[dict]:
    """The chats a broker restart between ``since`` and ``until`` ended, in
    the order they ended: ``session_id``, ``title``, ``agent``, ``provider``,
    ``ended`` (the `broker.exit` stamp) and ``last_turn`` (the last finished
    turn before it, from the record; None when it had none)."""
    stops = broker_stops(since, until) if stops is None else stops
    if not stops:
        return []
    windows = [(a.replace(microsecond=0), b.replace(microsecond=0) + _ONE_SECOND)
               for a, b in stops]
    first = windows[0][0].timestamp()
    ended = []
    for log in sorted((session_dir or SESSION_DIR).glob("*.jsonl")):
        try:
            if log.stat().st_mtime < first:
                continue   # nothing written since the first stop began
            exits = []
            with log.open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if '"broker.exit"' not in line:
                        continue
                    try:
                        event = json.loads(line)
                        at = _utc(event["ts"])
                    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                        continue
                    if any(a <= at <= b for a, b in windows):
                        exits.append(event["ts"])
            if not exits:
                continue
            record = json.loads(log.with_suffix(".json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for stamp in exits:
            turns = [t.get("ts") for t in record.get("turns") or []
                     if isinstance(t, dict) and isinstance(t.get("ts"), str)
                     and t["ts"] <= stamp]
            ended.append({
                "session_id": record.get("session_id") or log.stem,
                "title": record.get("title"),
                "agent": record.get("agent"),
                "provider": record.get("provider"),
                "ended": stamp,
                "last_turn": max(turns) if turns else None,
            })
    return sorted(ended, key=lambda e: (e["ended"], e["session_id"]))

