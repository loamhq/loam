"""Read manifests from the Shipped and Yours layers, resolve enabled plugins, and serve
their page files. Inspect an install link before confirmation. Server contributions
run in separate units. See docs/plugins.md for the manifest, update and settings
contracts.
"""

from __future__ import annotations

from collections import OrderedDict
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import loam_config as lc

import loam_broker as cfg

API = 0                                  # the interface version this host is
ID = re.compile(r"^[a-z][a-z0-9-]*$")    # loam-ui src/themes.js `slug`
VERSION = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?$")
KINDS = ("theme", "extension", "app", "agent", "skill")
# What is read and listed and never installed or run, yet (2026-11-13): an
# agent (soul, config, skills) and a skill. Each is a package alone.
LISTED_ONLY = ("agent", "skill")
# What an agent package never carries (the owner, m263: "a shared agent never
# carries its memory"): a folder named memory, or a MEMORY.md, at any depth.
MEMORY_NAMES = ("memory",)
MEMORY_FILES = ("memory.md",)
# The smallest server field (2026-11-13): `server: {run: "<command>"}`, run
# by `sh -c` in the plugin's folder. Without it, server/main.py or
# server/main.js is run by python3 or node.
SERVER_DEFAULTS = (("server/main.py", "python3 server/main.py"),
                   ("server/main.js", "node server/main.js"))
# A plugin's icon (Apps, Settings > Plugins): a picture inside its folder.
ICON = re.compile(r"^(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.(?:svg|webp|avif|jpe?g|png)$",
                  re.I)
RECORD = ("none", "read", "write", "bypass")
MEMORY = ("none", "read", "write")
# The places spec v0 names. A manifest naming another is listed with a
# warning, not refused: a place may be newer than this host.
PLACES = (
    "rail.section", "app.overlay", "settings.section", "thread.list.decoration",
    "thread.header.action", "thread.kind", "message.renderer",
    "message.footer.action", "composer.tool", "files.viewer", "files.action", "files.root",
    "activity.kind", "apps.source", "apps.icon", "thread.list.line", "command",
    "provider", "compute", "publishing", "agent.tool", "route", "schedule.kind",
    # An agent's profile (2026-11-13): a row or a section a plugin adds to
    # Settings > Agents > <agent>. Every plugin may, first-party or not
    # (the owner, 11-13: "anything ours needs, everyone gets").
    "agent.profile",
    # The machine's seams a manifest fills under `contributes:` (2026-11-14,
    # first release L4; lib/loam_contributions.py).
    "agent.prompt", "settings.entry", "engineer.env", "drift.check",
    "box.listener",
    # First release L9 (2026-11-14): a key the person pastes; a vault folder.
    "settings.secret", "vault.folder",
    # First release L6 (2026-11-14): a command run at an engineer job's launch.
    "engineer.job")
FIELDS = ("id", "name", "version", "author", "api", "kind", "places",
          "overrides", "touches", "settings", "forked_from", "options",
          "image", "image_position", "image_dark", "image_position_dark",
          "agent_settings", "server", "icon", "contributes", "default_on",
          # 2026-11-18: the providers whose keys it uses (lib/loam_keys.py).
          "keys",
          # 2026-11-19: its one-line promise, its pictures, and a theme's
          # named colours (`words_of`), for its Market page and its page in
          # Settings > Plugins. A Loam before them keeps them as `extra`
          # with a warning, and the plugin still installs and shows.
          "description", "screenshots", "palette")
SHA256 = re.compile(r"[a-f0-9]{64}")
LIST_FILE = "installed.json"
STAGING = ".staging"
# The Yours layer's default: its own folder and git repository, beside the
# vault and ai-labs (bin/plugins-autocommit commits it every 15 minutes).
YOURS_DEFAULT = lc.PLUGINS
# Reserved in Yours, for when plugin settings and data are built: a plugin's
# own settings file and its data, outside its folder, so an update that
# replaces the folder whole never touches them. `_` names are never plugins.
SETTINGS_DIR, DATA_DIR = "_settings", "_data"
UI_PROJECT = "loam-ui"
SHIPPED_THEMES = ("latte", "plain")      # the page will not start without them
# A theme's options (2026-11-12), held to loam-ui's rule (public/theme-css.js,
# `validateThemeOptions`): at most 8 options of 2 to 8 choices, ids slugs of
# at most 48 characters, labels at most 80, a default among the choices, and
# an option the core page draws itself takes only the choices it knows.
OPTIONS_MAX, CHOICES_MIN, CHOICES_MAX, OPTION_ID_MAX, LABEL_MAX = 8, 2, 8, 48, 80
# A plugin's per-agent settings (2026-11-13; `agent_settings` in the
# manifest, stored in Yours' `_settings/<id>/agents.json`): at most 16 keys an
# agent, a declared field's choices at most 100 (Gemini's voices are 30), a
# text value at most 200 characters.
AGENT_SETTINGS_MAX, AGENT_CHOICES_MAX, AGENT_TEXT_MAX = 16, 100, 200
AGENTS_FILE = "agents.json"
# Which options the core page draws, and the choices it knows for each, are
# loam-ui's table (`coreOptions` in public/theme-css.js), read from that file
# each time (`core_options`), never copied here: a copy went stale on
# 2026-11-12 when loam-ui 6100882 gave Latte `latte-art`, `latte-mug` and
# `latte-inside`, the copy refused the new choice, Latte's manifest read as
# broken, and Appearance found no theme with options.
CORE_TABLE = "public/theme-css.js"
# A theme's cover (2026-11-13, loam-ui 329b219): the picture its Appearance
# card shows, and where the card crops it. Held to loam-ui's rule
# (`themeImage` in public/theme-image.js), which throws on anything else and
# so fails the whole theme in the page: a path of [A-Za-z0-9_-] segments
# ending in .webp, .avif, .jpg, .jpeg or .png, and a position of two
# percentages from 0% to 100%. Wrong is a warning and the field dropped,
# never a broken row.
IMAGE = re.compile(r"^(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.(?:webp|avif|jpe?g|png)$",
                   re.I)
IMAGE_POSITION = re.compile(r"^(\d{1,3}(?:\.\d+)?)% (\d{1,3}(?:\.\d+)?)%$")
IMAGE_POSITION_MAX = 32
# A theme's dark-mode cover (2026-11-13, loam-ui's five themes): the page
# shows `image_dark` and crops it at `image_position_dark` in dark mode, each
# falling back to its light counterpart when absent. Same rules as the light
# pair, and neither without a usable `image` (themeImage throws).
COVER_FIELDS = ("image", "image_position", "image_dark", "image_position_dark")
DESCRIPTION_MAX = 160
SCREENSHOTS_MAX = 6
CAPTION_MAX = 140
PALETTE_MAX = 8
PALETTE_NAME_MAX = 24
TOKEN = re.compile(r"^--[a-z][a-z0-9-]{0,47}$")
# What the files route serves of a plugin's folder: its page code, its
# assets, its theme and its guide. Never `server/`, never anything else.
SERVED_DIRS = ("page", "assets")
SERVED_FILES = ("theme.css", "PLUGIN.md")
# A theme's display faces (2026-11-18, 0.2.5): its `fonts.css` and the font
# files beside it, which the app serves checked (app/src/themes.js,
# themeFontsCSS) at /theme-fonts/<id>.css.
THEME_FILES = ("fonts.css",)
FONT_SUFFIXES = (".woff2", ".woff")
FILE_MAX = 20 * 1024 * 1024
HASH_MAX = 64 * 1024 * 1024              # past this a folder is not fingerprinted
GIT_TIMEOUT = 180
TYPES = {".js": "text/javascript; charset=utf-8",
         ".mjs": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8",
         ".json": "application/json; charset=utf-8",
         ".map": "application/json; charset=utf-8",
         ".md": "text/markdown; charset=utf-8",
         ".txt": "text/plain; charset=utf-8",
         ".html": "text/html; charset=utf-8",
         ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
         ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
         ".avif": "image/avif", ".ico": "image/x-icon",
         ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf",
         ".otf": "font/otf", ".mp3": "audio/mpeg", ".wav": "audio/wav",
         ".ogg": "audio/ogg", ".mp4": "video/mp4", ".webm": "video/webm",
         ".wasm": "application/wasm"}

_threads = threading.RLock()
_owner = threading.local()


@contextmanager
def locked():
    """Serialize installed.json writers across broker threads and installer processes."""
    with _threads:
        if getattr(_owner, "locked", False):
            yield
            return
        root = yours_root()
        root.mkdir(parents=True, exist_ok=True)
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            _owner.locked = True
            yield
        finally:
            _owner.locked = False
            os.close(fd)


class PluginError(Exception):
    def __init__(self, message: str, status: int = 400, detail: dict | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.detail = detail or {}


class ManifestError(Exception):
    """Why a folder is not a plugin; the list shows it as the row's reason."""


# ---------------------------------------------------------------------------
# The frontmatter reader: the YAML a manifest uses, and nothing else
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """``PLUGIN.md``'s text into its frontmatter as data and its body.

    The frontmatter sits between a first line ``---`` and the next line
    ``---`` (or ``...``). It must be a mapping."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if not lines or lines[0].rstrip() != "---":
        raise ManifestError("PLUGIN.md does not begin with a `---` line, so it "
                            "has no frontmatter")
    for end in range(1, len(lines)):
        if lines[end].rstrip() in ("---", "..."):
            break
    else:
        raise ManifestError("PLUGIN.md's frontmatter has no closing `---` line")
    data = parse_yaml("\n".join(lines[1:end]), first_line=2)
    if not isinstance(data, dict):
        raise ManifestError("PLUGIN.md's frontmatter is not a mapping of fields")
    return data, "\n".join(lines[end + 1:]).lstrip("\n")


def parse_yaml(text: str, first_line: int = 1):
    """A strict reader of block YAML's everyday subset.

    Mappings and lists by indentation (spaces); ``- key: value`` items;
    flow lists and mappings (``[a, b]``, ``{a: 1}``); plain, single- and
    double-quoted scalars; ``|`` and ``>`` block text; ``#`` comments. A
    plain scalar is ``true``/``false``, ``null``/``~``/nothing, a whole
    number, or text: there are no floats, so ``version: 1.2`` is the text
    "1.2". Anchors, aliases, tags, complex keys and a second document are
    refused by line, as is a duplicate key or a tab in indentation."""
    rows = []
    for n, raw in enumerate(text.split("\n"), start=first_line):
        stripped = _strip_comment(raw)
        if not stripped.strip():
            rows.append((n, None, raw, raw))
            continue
        lead = raw[:len(raw) - len(raw.lstrip(" \t"))]
        if "\t" in lead:
            raise ManifestError(f"line {n}: a tab in indentation (use spaces)")
        rows.append((n, len(lead), stripped.rstrip(), raw))
    reader = _Yaml(rows)
    start = reader.next_content(0)
    if start is None:
        return {}
    value, i = reader.block(start, reader.rows[start][1])
    i = reader.next_content(i)
    if i is not None:
        raise ManifestError(f"line {reader.rows[i][0]}: indented where nothing "
                            f"can begin")
    return value


def _strip_comment(line: str) -> str:
    """The line without a trailing ``# comment`` (not inside quotes)."""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == "\\" and quote == '"':
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'" and (i == 0 or line[i - 1] in " \t[{,:-"):
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


class _Yaml:
    def __init__(self, rows):
        self.rows = rows

    def next_content(self, i):
        while i < len(self.rows) and self.rows[i][1] is None:
            i += 1
        return i if i < len(self.rows) else None

    def block(self, i, indent):
        text = self.rows[i][2].strip()
        if text == "-" or text.startswith("- "):
            return self.sequence(i, indent)
        return self.mapping(i, indent)

    def mapping(self, i, indent, first=None):
        out: dict = {}
        while True:
            n, ind, line, _ = self.rows[i]
            text = first if first is not None else line.strip()
            first = None
            if text == "-" or text.startswith("- "):
                raise ManifestError(f"line {n}: a list item where a field was expected")
            key, rest = self.key(text, n)
            if key in out:
                raise ManifestError(f"line {n}: {key!r} is given twice")
            out[key], i = self.value_after(rest, i, indent, n)
            j = self.next_content(i)
            if j is None:
                return out, i
            if self.rows[j][1] < indent:
                return out, j
            if self.rows[j][1] > indent:
                raise ManifestError(f"line {self.rows[j][0]}: indented under "
                                    f"something that takes no more")
            i = j

    def sequence(self, i, indent, first=None):
        out = []
        while True:
            n, ind, line, _ = self.rows[i]
            text = first if first is not None else line.strip()
            first = None
            if not (text == "-" or text.startswith("- ")):
                raise ManifestError(f"line {n}: a field where a list item was expected")
            rest = text[1:].lstrip(" ")
            if not rest:
                j = self.next_content(i + 1)
                if j is not None and self.rows[j][1] > indent:
                    value, i = self.block(j, self.rows[j][1])
                else:
                    value, i = None, i + 1
            elif rest == "-" or rest.startswith("- "):
                # `- - a`: a list as the item, its further items where the inner `-` is.
                value, i = self.sequence(i, ind + (len(text) - len(rest)), first=rest)
            elif _looks_like_key(rest):
                # `- key: value`: a mapping whose further keys sit where `key` does.
                inner = ind + (len(text) - len(rest))
                value, i = self.mapping(i, inner, first=rest)
            else:
                value, i = self.value_after_scalar(rest, i, indent, n)
            out.append(value)
            j = self.next_content(i)
            if j is None or self.rows[j][1] < indent:
                return out, (j if j is not None else i)
            nxt = self.rows[j][2].strip()
            if self.rows[j][1] == indent and not (nxt == "-" or nxt.startswith("- ")):
                return out, j          # `key:` then `- a` at its level: the next key
            if self.rows[j][1] > indent:
                raise ManifestError(f"line {self.rows[j][0]}: indented under a "
                                    f"list item that takes no more")
            i = j

    def key(self, text, n):
        if text.startswith("? "):
            raise ManifestError(f"line {n}: complex keys are not in the manifest's YAML")
        if text[0] in "\"'":
            end = _quoted_end(text, n)
            key = _scalar(text[:end + 1], n)
            rest = text[end + 1:]
            if not rest.startswith(":"):
                raise ManifestError(f"line {n}: expected `:` after the key")
            return str(key), rest[1:].strip()
        m = re.match(r"^([^:#\[\]{},&*!|>'\"%@`][^:]*?)\s*:(?:\s+(.*)|$)", text)
        if not m:
            raise ManifestError(f"line {n}: expected `field: value`, got {text!r}")
        return m.group(1).strip(), (m.group(2) or "").strip()

    def value_after(self, rest, i, indent, n):
        if not rest:
            j = self.next_content(i + 1)
            if j is not None and self.rows[j][1] > indent:
                return self.block(j, self.rows[j][1])
            if j is not None and self.rows[j][1] == indent and (
                    self.rows[j][2].strip() == "-"
                    or self.rows[j][2].strip().startswith("- ")):
                return self.sequence(j, indent)       # `key:` then `- a` at its level
            return None, i + 1
        return self.value_after_scalar(rest, i, indent, n)

    def value_after_scalar(self, rest, i, indent, n):
        if rest[0] in "|>":
            return self.block_text(rest, i, indent, n)
        if rest[0] in "[{":
            # A flow value may run over several lines, until its brackets close.
            text, j = rest, i
            while not _balanced(text):
                j += 1
                if j >= len(self.rows):
                    raise ManifestError(f"line {n}: a `{rest[0]}` that never closes")
                if self.rows[j][1] is not None:
                    text += " " + self.rows[j][2].strip()
            return _flow(text, n), j + 1
        return _scalar(rest, n), i + 1

    def block_text(self, head, i, indent, n):
        m = re.match(r"^([|>])([+-]?)$", head)
        if not m:
            raise ManifestError(f"line {n}: block text is `|` or `>` alone on its line")
        style, chomp = m.groups()
        # Inside block text `#` is text, so the raw lines are read here.
        body, j, child = [], i + 1, None
        while j < len(self.rows):
            raw = self.rows[j][3].rstrip()
            if not raw.strip():
                body.append("")
                j += 1
                continue
            if "\t" in raw[:len(raw) - len(raw.lstrip(" \t"))]:
                raise ManifestError(f"line {self.rows[j][0]}: a tab in indentation")
            ind = len(raw) - len(raw.lstrip(" "))
            if ind <= indent:
                break
            child = ind if child is None else child
            if ind < child:
                break
            body.append(raw[child:])
            j += 1
        while body and body[-1] == "" and chomp != "+":
            body.pop()
        if style == "|":
            value = "\n".join(body)
        else:
            value, para = "", []
            for line in body + [""]:
                if line == "":
                    if para:
                        value += " ".join(para) + "\n"
                        para = []
                    else:
                        value += "\n"
                else:
                    para.append(line.strip())
            value = value.rstrip("\n")
        if chomp != "-" and value:
            value += "\n"
        return value, j


def _looks_like_key(text: str) -> bool:
    if text[0] in "[{|>":
        return False
    if text[0] in "\"'":
        try:
            end = _quoted_end(text, 0)
        except ManifestError:
            return False
        return text[end + 1:].startswith(":")
    return bool(re.match(r"^[^:#\[\]{},&*!|>'\"%@`][^:]*?\s*:(\s|$)", text))


def _quoted_end(text: str, n: int) -> int:
    q = text[0]
    i = 1
    while i < len(text):
        if q == '"' and text[i] == "\\":
            i += 2
            continue
        if text[i] == q:
            if q == "'" and i + 1 < len(text) and text[i + 1] == "'":
                i += 2
                continue
            return i
        i += 1
    raise ManifestError(f"line {n}: a quote that never closes")


def _scalar(text: str, n: int):
    text = text.strip()
    if not text:
        return None
    if text[0] in "&*!%@`":
        raise ManifestError(f"line {n}: {text[0]!r} (anchors, aliases, tags) is not "
                            f"in the manifest's YAML; quote the text")
    if text[0] == '"':
        end = _quoted_end(text, n)
        if text[end + 1:].strip():
            raise ManifestError(f"line {n}: text after a closing quote")
        try:
            return json.loads(text[:end + 1])
        except ValueError:
            raise ManifestError(f"line {n}: a double-quoted text with an escape "
                                f"JSON does not know") from None
    if text[0] == "'":
        end = _quoted_end(text, n)
        if text[end + 1:].strip():
            raise ManifestError(f"line {n}: text after a closing quote")
        return text[1:end].replace("''", "'")
    if text in ("true", "True", "TRUE"):
        return True
    if text in ("false", "False", "FALSE"):
        return False
    if text in ("null", "Null", "NULL", "~"):
        return None
    if re.fullmatch(r"[-+]?\d+", text):
        return int(text)
    if text == "---" or text == "...":
        raise ManifestError(f"line {n}: a second document is not in the manifest")
    if ": " in text or text.endswith(":"):
        raise ManifestError(f"line {n}: a `: ` inside plain text; quote the text")
    return text


def _balanced(text: str) -> bool:
    depth, quote, i = 0, None, 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        i += 1
    return depth <= 0


def _flow(text: str, n: int):
    value, i = _flow_value(text.strip(), 0, n)
    if text.strip()[i:].strip():
        raise ManifestError(f"line {n}: text after a closing bracket")
    return value


def _flow_value(text, i, n):
    while i < len(text) and text[i] == " ":
        i += 1
    if i >= len(text):
        raise ManifestError(f"line {n}: a value is missing")
    if text[i] == "[":
        out, i = [], i + 1
        while True:
            while i < len(text) and text[i] == " ":
                i += 1
            if i < len(text) and text[i] == "]":
                return out, i + 1
            item, i = _flow_value(text, i, n)
            out.append(item)
            while i < len(text) and text[i] == " ":
                i += 1
            if i < len(text) and text[i] == ",":
                i += 1
            elif i < len(text) and text[i] == "]":
                return out, i + 1
            else:
                raise ManifestError(f"line {n}: expected `,` or `]` in a list")
    if text[i] == "{":
        out, i = {}, i + 1
        while True:
            while i < len(text) and text[i] == " ":
                i += 1
            if i < len(text) and text[i] == "}":
                return out, i + 1
            key, i = _flow_value(text, i, n)
            if not isinstance(key, str) or i >= len(text) or text[i] != ":":
                raise ManifestError(f"line {n}: expected `key: value` in a mapping")
            if key in out:
                raise ManifestError(f"line {n}: {key!r} is given twice")
            out[key], i = _flow_value(text, i + 1, n)
            while i < len(text) and text[i] == " ":
                i += 1
            if i < len(text) and text[i] == ",":
                i += 1
            elif i < len(text) and text[i] == "}":
                return out, i + 1
            else:
                raise ManifestError(f"line {n}: expected `,` or `}}` in a mapping")
    if text[i] in "\"'":
        end = i + _quoted_end(text[i:], n)
        return _scalar(text[i:end + 1], n), end + 1
    j = i
    while j < len(text) and text[j] not in ",]}" and not (
            text[j] == ":" and (j + 1 == len(text) or text[j + 1] in " ,]}")):
        j += 1
    return _scalar(text[i:j], n), j


# ---------------------------------------------------------------------------
# The manifest
# ---------------------------------------------------------------------------

def _strings(value, field: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip()
                                              for v in value):
        raise ManifestError(f"`{field}` is a list of names")
    out = []
    for v in value:
        if v.strip() not in out:
            out.append(v.strip())
    return out


def _relative(value, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"`{field}` is a file's path inside the plugin")
    parts = Path(value.strip()).parts
    if value.strip().startswith("/") or ".." in parts:
        raise ManifestError(f"`{field}` is a path inside the plugin, not {value!r}")
    return value.strip()


def options_of(value) -> list[dict]:
    """A theme's ``options`` as its manifest gives them (``manifest.json``
    or ``PLUGIN.md``), checked by the rule of loam-ui's loader, so the host
    accepts exactly the options the page will draw; a ManifestError for the
    first thing wrong. Absent is none."""
    if value is None:
        return []
    core = core_options()

    def label(v):
        return isinstance(v, str) and bool(v.strip()) and len(v) <= LABEL_MAX

    def slug(v):
        return isinstance(v, str) and bool(ID.match(v)) and len(v) <= OPTION_ID_MAX

    if not isinstance(value, list) or len(value) > OPTIONS_MAX:
        raise ManifestError(f"`options` is a list of at most {OPTIONS_MAX} options")
    out, seen = [], set()
    for opt in value:
        if (not isinstance(opt, dict)
                or set(opt) - {"id", "label", "choices", "default"}
                or not slug(opt.get("id")) or opt["id"] in seen
                or not label(opt.get("label"))
                or not isinstance(opt.get("choices"), list)
                or not CHOICES_MIN <= len(opt["choices"]) <= CHOICES_MAX):
            name = opt.get("id") if isinstance(opt, dict) else opt
            raise ManifestError(
                f"options: {name!r} is not an option as loam-ui's loader takes one "
                f"(an id, a label, {CHOICES_MIN} to {CHOICES_MAX} choices, a default)")
        seen.add(opt["id"])
        ids: list[str] = []
        for ch in opt["choices"]:
            if (not isinstance(ch, dict) or set(ch) - {"id", "label"}
                    or not slug(ch.get("id")) or not label(ch.get("label"))
                    or ch["id"] in ids
                    or (opt["id"] in core
                        and ch["id"] not in core[opt["id"]])):
                raise ManifestError(f"options: {opt['id']} has a choice loam-ui's "
                                    f"loader refuses ({ch!r})")
            ids.append(ch["id"])
        if opt.get("default") not in ids:
            raise ManifestError(f"options: {opt['id']}'s default is not one of its "
                                f"choices")
        out.append({"id": opt["id"], "label": opt["label"].strip(),
                    "choices": [{"id": c["id"], "label": c["label"].strip()}
                                for c in opt["choices"]],
                    "default": opt["default"]})
    return out


def agent_settings_of(value) -> list[dict]:
    """A plugin's ``agent_settings``: the fields it keeps for each agent and
    shows on the agent's profile, e.g. Read aloud's voice::

        agent_settings:
          - id: voice
            label: Voice
            choices: [{id: Kore, label: Kore}, {id: Puck, label: Puck}]
            default: Kore

    Each is an ``id`` (a slug of at most 48 characters), a ``label`` (at most
    80), and optionally ``choices`` (1 to 100 ``{id, label}``; ids are any
    text of at most 48 characters, since a voice's name is its id) and a
    ``default`` (one of them, or text). A field without choices takes text
    of at most 200 characters. Absent is none: such a plugin may still keep
    undeclared values per agent (``set_agent_values``).
    """
    if value is None:
        return []

    def label(v):
        return isinstance(v, str) and bool(v.strip()) and len(v) <= LABEL_MAX

    if not isinstance(value, list) or len(value) > AGENT_SETTINGS_MAX:
        raise ManifestError(f"`agent_settings` is a list of at most "
                            f"{AGENT_SETTINGS_MAX} fields")
    out, seen = [], set()
    for f in value:
        if (not isinstance(f, dict)
                or set(f) - {"id", "label", "choices", "default"}
                or not isinstance(f.get("id"), str) or not ID.match(f["id"])
                or len(f["id"]) > OPTION_ID_MAX or f["id"] in seen
                or not label(f.get("label"))):
            name = f.get("id") if isinstance(f, dict) else f
            raise ManifestError(f"agent_settings: {name!r} is not a field (an id, "
                                f"a label, and optionally choices and a default)")
        seen.add(f["id"])
        row = {"id": f["id"], "label": f["label"].strip()}
        choices = f.get("choices")
        if choices is not None:
            if (not isinstance(choices, list)
                    or not 1 <= len(choices) <= AGENT_CHOICES_MAX):
                raise ManifestError(f"agent_settings: {f['id']}'s choices are 1 to "
                                    f"{AGENT_CHOICES_MAX} {{id, label}}")
            ids: list[str] = []
            for ch in choices:
                if (not isinstance(ch, dict) or set(ch) - {"id", "label"}
                        or not isinstance(ch.get("id"), str)
                        or not ch["id"].strip() or len(ch["id"]) > OPTION_ID_MAX
                        or ch["id"] in ids or not label(ch.get("label"))):
                    raise ManifestError(f"agent_settings: {f['id']} has a choice "
                                        f"that is not {{id, label}} ({ch!r})")
                ids.append(ch["id"])
            row["choices"] = [{"id": c["id"], "label": c["label"].strip()}
                              for c in choices]
        default = f.get("default")
        if default is not None:
            if choices is not None and default not in [c["id"] for c in row["choices"]]:
                raise ManifestError(f"agent_settings: {f['id']}'s default is not "
                                    f"one of its choices")
            if not isinstance(default, str) or len(default) > AGENT_TEXT_MAX:
                raise ManifestError(f"agent_settings: {f['id']}'s default is text")
        row["default"] = default
        out.append(row)
    return out


_core_cache: dict = {}


def core_table_path() -> Path | None:
    """loam-ui's file holding ``coreOptions``: ``LOAM_UI_CORE_OPTIONS`` (a
    path, or ``none`` for no table), or the loam-ui checkout's
    ``public/theme-css.js``, the file the page's loader imports."""
    env = os.environ.get("LOAM_UI_CORE_OPTIONS")
    if env is not None:
        return None if env in ("", "none") else Path(env)
    ui = ui_checkout()
    return ui / CORE_TABLE if ui else None


def parse_core_options(text: str) -> dict[str, tuple[str, ...]] | None:
    """``const coreOptions = { 'id': ['choice', ...], ... }`` out of loam-ui's
    source; ``None`` when the file has no such table."""
    m = re.search(r"\bconst\s+coreOptions\s*=\s*\{([^{}]*)\}", text)
    if not m:
        return None
    out: dict[str, tuple[str, ...]] = {}
    for key, body in re.findall(
            r"""['"]?([a-z][a-z0-9-]*)['"]?\s*:\s*\[([^\]]*)\]""", m.group(1)):
        out[key] = tuple(re.findall(r"""['"]([a-z][a-z0-9-]*)['"]""", body))
    return out


def core_options() -> dict[str, tuple[str, ...]]:
    """The options the core page draws and the choices it knows, as loam-ui's
    source states them now (``parse_core_options``), cached by the file's
    mtime. When the file cannot be read or holds no table there is no core
    restriction: each theme's own choices stand, which is what Appearance
    may store; the page's loader is the last word on what it draws."""
    where = core_table_path()
    if where is None:
        return {}
    try:
        stamp = where.stat().st_mtime_ns
    except OSError:
        return {}
    hit = _core_cache.get(str(where))
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        table = parse_core_options(where.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError):
        table = {}
    _core_cache[str(where)] = (stamp, table)
    return table


def touches_of(value, warnings: list[str]) -> dict:
    """``touches`` as the list shows it: every field present, defaults where
    the manifest is silent, what it adds beyond spec v0 kept as given."""
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ManifestError("`touches` is a mapping (network, record, memory, server)")
    out = {"network": [], "record": "none", "memory": "none", "server": False}
    for key, v in value.items():
        if key == "network":
            items = [] if v is None else v
            if not isinstance(items, list):
                raise ManifestError("`touches.network` is a list of `host` and `why`")
            for item in items:
                if isinstance(item, str) and item.strip():
                    warnings.append(f"touches.network: {item!r} says no `why`")
                    item = {"host": item.strip(), "why": None}
                if not isinstance(item, dict) or not isinstance(item.get("host"), str) \
                        or not item["host"].strip():
                    raise ManifestError("each `touches.network` entry names a `host`")
                why = item.get("why")
                if why is not None and not isinstance(why, str):
                    raise ManifestError("a `touches.network` entry's `why` is text")
                if why is None and isinstance(item, dict) and "why" not in item:
                    warnings.append(f"touches.network: {item['host']!r} says no `why`")
                row = {"host": item["host"].strip(), "why": why}
                row.update({k: x for k, x in item.items() if k not in ("host", "why")})
                out["network"].append(row)
        elif key == "facts":
            from loam_activity_facts import GROUPS
            if not isinstance(v, list) or any(item not in GROUPS for item in v):
                raise ManifestError("touches.facts names groups from " + ", ".join(GROUPS))
            out[key] = list(dict.fromkeys(v))
        elif key == "record":
            if v not in RECORD:
                raise ManifestError(f"`touches.record` is one of {', '.join(RECORD)}")
            out["record"] = v
        elif key == "memory":
            if v not in MEMORY:
                raise ManifestError(f"`touches.memory` is one of {', '.join(MEMORY)}")
            out["memory"] = v
        elif key == "server":
            if not isinstance(v, bool):
                raise ManifestError("`touches.server` is true or false")
            out["server"] = v
        else:
            warnings.append(f"touches.{key} is not in spec v0; listed as given")
            out[key] = v
    if "public-work" in out.get("facts", []):
        hosts = {entry["host"] for entry in out["network"]}
        if not {"api.github.com", "raw.githubusercontent.com"}.issubset(hosts):
            raise ManifestError("public-work needs api.github.com and raw.githubusercontent.com in touches.network")
    return out


def _cover_image(name: str, image, folder: Path | None,
                 warnings: list[str]) -> bool:
    """Whether a cover path (``image`` or ``image_dark``) is usable; a
    warning when it is not."""
    why = None
    if not isinstance(image, str) or not IMAGE.match(image):
        why = ("is a path inside the theme's folder to a .webp, .avif, "
               ".jpg or .png, of letters, digits, _ and - only")
    elif folder is not None:
        base = folder.resolve()
        try:
            real = (folder / image).resolve(strict=True)
        except (OSError, RuntimeError):
            real = None
        if real is None or not real.is_file():
            why = "names no file in the theme's folder"
        elif not real.is_relative_to(base):
            why = "leaves the theme's folder"
    if why:
        warnings.append(f"{name} {image!r} {why}; it is left out")
    return why is None


def _cover_position(name: str, position, warnings: list[str]) -> bool:
    m = (IMAGE_POSITION.match(position) if isinstance(position, str)
         and len(position) <= IMAGE_POSITION_MAX else None)
    if not m or any(float(v) > 100 for v in m.groups()):
        warnings.append(f"{name} {position!r} is two percentages "
                        "from 0% to 100%, like 62% 45%; it is left out")
        return False
    return True


def cover_of(data: dict, kind: list[str], folder: Path | None,
             warnings: list[str]) -> dict:
    """A theme's cover, ``image`` and ``image_position``, and its dark-mode
    cover, ``image_dark`` and ``image_position_dark`` (each falling back to
    its light counterpart in the page, and kept only with a usable
    ``image``), as the fields to add to its
    manifest: none when absent, and each one wrong left out with a
    warning. ``folder`` is where an image must exist; ``None`` skips that
    check."""
    given = [k for k in COVER_FIELDS if data.get(k) is not None]
    if not given:
        return {}
    if "theme" not in kind:
        warnings.append(f"{', '.join(given)} {'is' if len(given) == 1 else 'are'}"
                        " a theme's cover; this plugin's kind has no theme, so "
                        f"{'it is' if len(given) == 1 else 'they are'} left out")
        return {}
    out: dict = {}
    for key in ("image", "image_dark"):
        if data.get(key) is not None and _cover_image(key, data[key], folder,
                                                      warnings):
            out[key] = data[key]
    for key in ("image_position", "image_position_dark"):
        position = data.get(key)
        if position is not None and _cover_position(key, position, warnings):
            out[key] = position
    # Every other field hangs on a usable `image`: the page throws on a dark
    # cover or any position with no default image ("A dark theme image needs
    # a default image."), and so would fail the whole theme.
    if "image" not in out:
        for key in [k for k in out]:
            warnings.append(f"{key} is given without a usable image; "
                            "it is left out")
        return {}
    return out


def _in_folder(image, folder: Path | None) -> bool:
    if not isinstance(image, str) or not IMAGE.match(image):
        return False
    if folder is None:
        return True
    try:
        real = (folder / image).resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    return real.is_file() and real.is_relative_to(folder.resolve())


def words_of(data: dict, kind: list[str], folder: Path | None,
             warnings: list[str]) -> dict:
    """What a plugin says about itself to a person: its
    ``description``, one plain line of at most 160 characters, what someone
    gets from it; its ``screenshots``, at most six pictures in its folder,
    each ``{src, src_dark?, caption}``; and, for a theme, its ``palette``,
    at most eight of its colours by token, each with a name from the theme's
    story (``{token, name, name_dark?}``). Each wrong one is left out with a
    warning; none of them ever breaks the plugin.
    """
    out: dict = {}
    desc = data.get("description")
    if desc is not None:
        if isinstance(desc, str) and desc.strip() and len(desc.strip()) <= DESCRIPTION_MAX \
                and "\n" not in desc.strip():
            out["description"] = desc.strip()
        else:
            warnings.append(f"description is one line of at most {DESCRIPTION_MAX} "
                            "characters; it is left out")
    shots = data.get("screenshots")
    if shots is not None:
        kept = []
        if not isinstance(shots, list):
            warnings.append("screenshots is a list of {src, src_dark, caption}; "
                            "they are left out")
            shots = []
        for i, shot in enumerate(shots):
            if len(kept) >= SCREENSHOTS_MAX:
                warnings.append(f"screenshots: at most {SCREENSHOTS_MAX}; the rest "
                                "are left out")
                break
            if not isinstance(shot, dict) or not _in_folder(shot.get("src"), folder):
                warnings.append(f"screenshots[{i}] names no picture in its folder "
                                "(.webp, .avif, .jpg or .png); it is left out")
                continue
            one = {"src": shot["src"]}
            if shot.get("src_dark") is not None:
                if _in_folder(shot.get("src_dark"), folder):
                    one["src_dark"] = shot["src_dark"]
                else:
                    warnings.append(f"screenshots[{i}].src_dark names no picture in "
                                    "its folder; the light one is shown in dark")
            cap = shot.get("caption")
            if isinstance(cap, str) and cap.strip():
                one["caption"] = cap.strip()[:CAPTION_MAX]
            kept.append(one)
        if kept:
            out["screenshots"] = kept
    palette = data.get("palette")
    if palette is not None:
        if "theme" not in kind:
            warnings.append("palette is a theme's colours; this plugin's kind has no "
                            "theme, so it is left out")
        elif not isinstance(palette, list):
            warnings.append("palette is a list of {token, name}; it is left out")
        else:
            kept = []
            for i, sw in enumerate(palette[:PALETTE_MAX]):
                ok = (isinstance(sw, dict) and isinstance(sw.get("token"), str)
                      and TOKEN.match(sw["token"]) and isinstance(sw.get("name"), str)
                      and 0 < len(sw["name"].strip()) <= PALETTE_NAME_MAX)
                if not ok:
                    warnings.append(f"palette[{i}] is {{token: --a-token, name: a few "
                                    f"words}}; it is left out")
                    continue
                one = {"token": sw["token"], "name": sw["name"].strip()}
                dark = sw.get("name_dark")
                if isinstance(dark, str) and 0 < len(dark.strip()) <= PALETTE_NAME_MAX:
                    one["name_dark"] = dark.strip()
                kept.append(one)
            if kept:
                out["palette"] = kept
    return out


def server_of(value, warnings: list[str]) -> dict | None:
    """The manifest's ``server``: ``{run: "<command>"}``, the one way a
    plugin says how its server starts. ``None`` when absent."""
    if value is None:
        return None
    if isinstance(value, str):
        value = {"run": value}
    if not isinstance(value, dict):
        raise ManifestError('`server` is a mapping: {run: "python3 server/main.py"}')
    run = value.get("run")
    if not isinstance(run, str) or not run.strip() or "\n" in run.strip():
        raise ManifestError("`server.run` is the one line that starts its server, "
                            "run in the plugin's folder (python3 server/main.py)")
    for k in value:
        if k != "run":
            warnings.append(f"server.{k} is not a server field (run); kept as given")
    return {"run": run.strip(), **{k: v for k, v in value.items() if k != "run"}}


def _icon_ok(icon, folder: Path | None, warnings: list[str]) -> bool:
    why = None
    if not isinstance(icon, str) or not ICON.match(icon):
        why = ("is a path inside the plugin's folder to an .svg, .webp, .avif, "
               ".jpg or .png, of letters, digits, _ and - only")
    elif folder is not None:
        try:
            real = (folder / icon).resolve(strict=True)
        except (OSError, RuntimeError):
            real = None
        if real is None or not real.is_file():
            why = "names no file in the plugin's folder"
        elif not real.is_relative_to(folder.resolve()):
            why = "leaves the plugin's folder"
    if why:
        warnings.append(f"icon {icon!r} {why}; it is left out")
    return why is None


def carried_memory(folder: Path) -> list[str]:
    """What in a folder is an agent's memory: every folder named ``memory``
    and every ``MEMORY.md``, at any depth, as paths inside it."""
    found = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        base = Path(dirpath)
        for d in list(dirnames):
            if d.lower() in MEMORY_NAMES:
                found.append((base / d).relative_to(folder).as_posix() + "/")
                dirnames.remove(d)
        for f in filenames:
            if f.lower() in MEMORY_FILES:
                found.append((base / f).relative_to(folder).as_posix())
    return sorted(found)


def server_spec(row: dict) -> dict | None:
    """What a row would run: ``{"run", "default"}``, or ``None`` when it has
    no ``server/`` or nothing to start it with. An agent or skill package
    runs nothing."""
    if row.get("broken") or not (row.get("has") or {}).get("server"):
        return None
    if any(k in LISTED_ONLY for k in row.get("kind") or []):
        return None
    if row.get("server"):
        return {"run": row["server"]["run"], "default": False}
    folder = Path(row["folder"])
    for rel, run in SERVER_DEFAULTS:
        if (folder / rel).is_file():
            return {"run": run, "default": True}
    return None


def manifest_of(data: dict, folder_name: str | None,
                folder: Path | None = None) -> tuple[dict, list[str]]:
    """A frontmatter mapping into the manifest the list shows, and its
    warnings; a ManifestError for anything that makes it not a plugin.
    ``folder`` is where a cover image must exist (``None``: not checked)."""
    warnings: list[str] = []
    pid = data.get("id")
    if not isinstance(pid, str) or not ID.match(pid):
        raise ManifestError("`id` is lower-case letters, digits and dashes, "
                            "beginning with a letter" + (f", not {pid!r}" if pid
                                                         is not None else
                                                         "; there is none"))
    if folder_name is not None and pid != folder_name:
        raise ManifestError(f"`id` is {pid!r} but the folder is {folder_name!r}; "
                            f"a plugin's folder is named by its id")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ManifestError("`name` is missing")
    version = data.get("version")
    if isinstance(version, int) and not isinstance(version, bool):
        version = str(version)
    if not isinstance(version, str) or not VERSION.match(version):
        raise ManifestError(f"`version` is major.minor.patch, like 1.2.0, not {version!r}")
    author = data.get("author")
    if author is not None and (not isinstance(author, str) or not author.strip()):
        raise ManifestError("`author` is a name")
    api = data.get("api")
    if not isinstance(api, int) or isinstance(api, bool) or api < 0:
        raise ManifestError("`api` is the interface version it was written for "
                            f"(this Loam's is {API})" + ("; there is none" if api
                                                         is None else ""))
    if api > API:
        warnings.append(f"written for api {api}; this Loam is api {API}")
    kind = _strings(data.get("kind"), "kind")
    if not kind:
        raise ManifestError("`kind` is theme, extension, app, agent or skill")
    for k in kind:
        if k not in KINDS:
            raise ManifestError(f"`kind` is theme, extension, app, agent or skill, "
                                f"not {k!r}")
    alone = [k for k in kind if k in LISTED_ONLY]
    if alone and len(kind) > 1:
        raise ManifestError(f"{'an' if alone[0] == 'agent' else 'a'} {alone[0]} "
                            f"package is that alone: `kind: "
                            f"[{alone[0]}]`, not {', '.join(kind)}")
    places = _strings(data.get("places"), "places")
    for p in places:
        if p not in PLACES:
            warnings.append(f"places: {p!r} is not a place spec v0 names")
    overrides = _strings(data.get("overrides"), "overrides")
    for o in overrides:
        if not o.startswith("core."):
            warnings.append(f"overrides: {o!r} is not a core component id (core.…)")
    forked = data.get("forked_from")
    if forked is not None and (not isinstance(forked, str)
                               or not re.match(r"^[a-z][a-z0-9-]*@\S+$", forked)):
        raise ManifestError(f"`forked_from` is id@version, like read-aloud@1.2.0, "
                            f"not {forked!r}")
    out = {"id": pid, "name": name.strip(), "version": version,
           "author": author.strip() if author else None, "api": api,
           "kind": kind, "places": places, "overrides": overrides,
           "touches": touches_of(data.get("touches"), warnings),
           "settings": _relative(data.get("settings"), "settings"),
           "forked_from": forked}
    opts = options_of(data.get("options"))
    if opts:
        if "theme" not in kind:
            raise ManifestError("`options` are a theme's; this plugin's kind has "
                                "no theme")
        out["options"] = opts
    out.update(cover_of(data, kind, folder, warnings))
    out.update(words_of(data, kind, folder, warnings))
    fields = agent_settings_of(data.get("agent_settings"))
    if fields:
        out["agent_settings"] = fields
    server = server_of(data.get("server"), warnings)
    if server:
        out["server"] = server
    import loam_contributions  # noqa: PLC0415
    try:
        contrib = loam_contributions.contributes_of(data.get("contributes"), folder,
                                                    warnings)
    except loam_contributions.ContributionError as exc:
        raise ManifestError(str(exc))
    # The providers whose keys it uses (2026-11-18, shared keys): shown
    # before install beside `touches`; a key is handed to no plugin that did
    # not declare its provider. lib/loam_keys.py.
    import loam_keys  # noqa: PLC0415
    try:
        keys = loam_keys.keys_of(data.get("keys"), warnings, pid)
    except ValueError as exc:
        raise ManifestError(str(exc))
    if keys:
        out["keys"] = keys
    default_on = data.get("default_on")
    if default_on is not None:
        if not isinstance(default_on, bool):
            raise ManifestError("`default_on` is true or false: whether a shipped "
                                "plugin is on before anyone turns it on or off")
        out["default_on"] = default_on
    if contrib:
        if "extension" not in kind and "app" not in kind:
            raise ManifestError("`contributes` is an extension's or an app's; this "
                                "plugin's kind is neither")
        for place in contrib:
            if place not in places:
                places.append(place)      # contributing to a place is filling it
        out["contributes"] = contrib
    icon = data.get("icon")
    if icon is not None and _icon_ok(icon, folder, warnings):
        out["icon"] = icon
    extra = {k: v for k, v in data.items() if k not in FIELDS}
    for k in extra:
        warnings.append(f"{k!r} is not a manifest field in spec v0; kept as given")
    if extra:
        out["extra"] = extra
    return out, warnings


def legacy_theme(folder: Path) -> tuple[dict, list[str]]:
    """loam-ui's theme folder (``manifest.json``, schemaVersion 1) as a theme
    plugin, held to the rule loam-ui's ``loadThemes`` holds it to."""
    try:
        m = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except OSError as exc:
        raise ManifestError(f"manifest.json cannot be read ({exc.strerror})") from None
    except ValueError as exc:
        raise ManifestError(f"manifest.json is not JSON ({exc})") from None
    if not isinstance(m, dict) or m.get("schemaVersion") != 1:
        raise ManifestError("manifest.json is not loam-ui's theme manifest "
                            "(schemaVersion 1)")
    if m.get("id") != folder.name or not ID.match(folder.name):
        raise ManifestError(f"manifest.json's id is {m.get('id')!r} but the folder "
                            f"is {folder.name!r}")
    for field in ("name", "author"):
        if not isinstance(m.get(field), str) or not m[field].strip():
            raise ManifestError(f"manifest.json has no `{field}`")
    if not isinstance(m.get("version"), str) or not re.match(r"^\d+\.\d+\.\d+$",
                                                             m["version"]):
        raise ManifestError("manifest.json's version is not major.minor.patch")
    if m.get("modes") != ["light", "dark"] or m.get("stylesheet") != "theme.css":
        raise ManifestError("manifest.json's modes are not [light, dark] with "
                            "theme.css, as loam-ui's loader requires")
    return manifest_of({"id": m["id"], "name": m["name"], "version": m["version"],
                        "author": m["author"], "api": 0, "kind": ["theme"],
                        "options": m.get("options")},
                       folder.name)


def has_of(folder: Path) -> dict:
    """What the folder holds, as seen, whatever the manifest says."""
    return {"page": (folder / "page").is_dir(),
            "page_main": (folder / "page" / "main.js").is_file(),
            "server": (folder / "server").is_dir(),
            "assets": (folder / "assets").is_dir(),
            "theme": (folder / "theme.css").is_file()}


def read_folder(folder: Path, name: str | None = None) -> dict:
    """A folder as a plugin: its manifest, format, guide, what it holds and
    its warnings. ``name`` is the id it must have (its folder's name on
    disk); ``None`` when it is being installed under whatever id it states."""
    plugin_md = folder / "PLUGIN.md"
    if plugin_md.is_file():
        try:
            text = plugin_md.read_text(encoding="utf-8")
        except OSError as exc:
            raise ManifestError(f"PLUGIN.md cannot be read ({exc.strerror})") from None
        except UnicodeDecodeError:
            raise ManifestError("PLUGIN.md is not UTF-8 text") from None
        data, body = parse_frontmatter(text)
        manifest, warnings = manifest_of(data, name, folder)
        fmt = "PLUGIN.md"
        if (folder / "manifest.json").is_file() and "theme" in manifest["kind"]:
            warnings.append("manifest.json is here too; PLUGIN.md is the one read")
    elif (folder / "manifest.json").is_file():
        manifest, warnings = legacy_theme(folder)
        body, fmt = None, "manifest.json"
    else:
        raise ManifestError("no PLUGIN.md (a plugin is a folder with one)")
    has = has_of(folder)
    if "theme" in manifest["kind"] and not has["theme"]:
        raise ManifestError("its kind is theme but it has no theme.css")
    if has["server"] and not manifest["touches"]["server"]:
        warnings.append("it has server/ code but does not declare touches.server")
    if "extension" in manifest["kind"] and not (has["page"] or has["server"]
                                                or manifest.get("contributes")):
        warnings.append("its kind is extension but it has no page/ or server/ "
                        "code and contributes nothing")
    if "app" in manifest["kind"] and not has["server"]:
        raise ManifestError("its kind is app but it has no server/ to run; an app "
                            "is a server plugin that also shows in Apps")
    if manifest.get("server") and not has["server"]:
        warnings.append("it names server.run but has no server/ folder, so "
                        "nothing is run")
    if has["server"] and not manifest.get("server") and not any(
            (folder / rel).is_file() for rel, _ in SERVER_DEFAULTS) \
            and not any(k in LISTED_ONLY for k in manifest["kind"]):
        warnings.append("it has server/ but no server.run and no server/main.py "
                        "or server/main.js, so nothing is run")
    if "agent" in manifest["kind"]:
        memory = carried_memory(folder)
        if memory:
            raise ManifestError("a shared agent never carries its memory, and this "
                                f"one holds {', '.join(memory[:5])}"
                                + (" and more" if len(memory) > 5 else ""))
    if "skill" in manifest["kind"] and not (folder / "SKILL.md").is_file():
        warnings.append("its kind is skill but it has no SKILL.md")
    if has["page"] and not has["page_main"]:
        warnings.append("it has page/ but no page/main.js for the page to import")
    return {"manifest": manifest, "format": fmt, "guide": body,
            "has": has, "warnings": warnings}


def declaration(manifest: dict, has: dict) -> dict:
    """What a plugin is shown to touch before it is installed: the manifest's
    ``touches`` and ``overrides``, and server code present or declared."""
    touches = dict(manifest["touches"])
    touches["server"] = bool(touches.get("server") or has.get("server"))
    # What it adds to the machine (lib/loam_contributions.py): its tools and
    # words for agents, its timers, settings, secrets and listeners. Computed
    # from `contributes:`, so it cannot be declared short of what it does.
    import loam_contributions  # noqa: PLC0415
    touches.update(loam_contributions.declared(manifest.get("contributes") or {}))
    if manifest.get("keys"):
        import loam_keys  # noqa: PLC0415
        touches["keys"] = loam_keys.declaration_of(manifest["keys"])
        # A pasted secret named as a declared provider's field is that
        # provider's key, not one of its own: shown once, as "Uses your
        # Google Gemini key" (2026-11-19: Read aloud's listing said both).
        fields = {f["name"] for k in manifest["keys"] for f in k.get("fields") or []}
        own = [n for n in touches.get("secrets") or [] if n not in fields]
        if own:
            touches["secrets"] = own
        else:
            touches.pop("secrets", None)
    return {"touches": touches, "overrides": list(manifest["overrides"]),
            "page_code": bool(has.get("page"))}


def added(before: dict | None, after: dict) -> dict:
    """What ``after`` declares beyond ``before``: the "asks again" of a later
    version. Empty when it declares nothing more."""
    if not before:
        return {}
    out: dict = {}
    bt, at = before.get("touches") or {}, after["touches"]
    new_hosts = [h for h in at["network"] if h not in (bt.get("network") or [])]
    if new_hosts:
        out["network"] = new_hosts
    for field, order in (("record", RECORD), ("memory", MEMORY)):
        was, now = bt.get(field, "none"), at[field]
        if was != now and (now == "bypass" or was not in order
                           or order.index(now) > order.index(was)):
            out[field] = now
    if at["server"] and not bt.get("server"):
        out["server"] = True
    new_over = [o for o in after["overrides"] if o not in (before.get("overrides") or [])]
    if new_over:
        out["overrides"] = new_over
    if after.get("page_code") and not before.get("page_code"):
        out["page_code"] = True
    for key, value in at.items():
        if key in ("network", "record", "memory", "server"):
            continue
        previous = bt.get(key)
        if isinstance(value, list) and isinstance(previous, list):
            value = [item for item in value if item not in previous]
            if value:
                out[key] = value
        elif key == "agents" and isinstance(previous, dict) and isinstance(value, dict) and all(
                isinstance(items, list) and isinstance(previous.get(k, []), list) for k, items in value.items()):
            delta = {k: [item for item in items if item not in previous.get(k, [])]
                     for k, items in value.items()}
            if any(delta.values()):
                out[key] = delta
        elif value != previous:
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# The layers
# ---------------------------------------------------------------------------

def ui_checkout() -> Path | None:
    """Resolve app source from the app's project record when present and from the release's
    app/ tree on other installs.
    """
    record = cfg.AGENTS_ROOT / "projects" / f"{UI_PROJECT}.json"
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
        checkout, sub = data.get("checkout"), data.get("subdir")
    except (OSError, ValueError, AttributeError):
        checkout, sub = None, None
    # Since 2026-11-16 the app project is the `app/` folder of Loam's own
    # checkout (`subdir`); the app's source is that folder.
    if checkout:
        return Path(checkout) / sub if isinstance(sub, str) and sub.strip("/") else Path(checkout)
    bundled = cfg.AGENTS_ROOT / "app"
    return bundled if (bundled / "public").is_dir() else None


def shipped_roots() -> list[Path]:
    """Return Shipped roots in precedence order. Honor the colon-separated
    LOAM_PLUGIN_SHIPPED override, otherwise use the app's themes and plugins followed by
    the runtime's first-party plugins.
    """
    env = os.environ.get("LOAM_PLUGIN_SHIPPED")
    if env is not None:
        return [Path(p) for p in env.split(":") if p]
    ui = ui_checkout()
    roots = [ui / "public" / "themes", ui / "public" / "plugins"] if ui else []
    return roots + [lc.ROOT / "plugins"]


def yours_root() -> Path:
    """The Yours layer: ``LOAM_PLUGINS_DIR``, or ``paths.plugins`` (lib/loam_config.py)."""
    env = os.environ.get("LOAM_PLUGINS_DIR")
    return Path(env) if env else YOURS_DEFAULT


def _folders(root: Path) -> list[Path]:
    try:
        return sorted((p for p in root.iterdir()
                       if p.is_dir() and not p.name.startswith((".", "_"))),
                      key=lambda p: p.name)
    except OSError:
        return []


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_list(strict: bool) -> dict:
    """``installed.json``: the installed list and every switch anyone set.

    A file that cannot be read is a 500 for a write (nothing is written over
    it, which would lose every line in it unseen) and, for the list, an
    empty list with the reason under ``error``, so the list still draws."""
    path = yours_root() / LIST_FILE
    error = None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError) as exc:
        if strict:
            raise PluginError(f"the installed list ({path}) cannot be read ({exc}); "
                              f"nothing is written over it", 500) from None
        data, error = {}, f"the installed list ({path}) cannot be read ({exc})"
    if not isinstance(data, dict):
        if strict:
            raise PluginError(f"the installed list ({path}) is not a JSON object; "
                              f"nothing is written over it", 500)
        data, error = {}, f"the installed list ({path}) is not a JSON object"
    plugins = data.get("plugins") if isinstance(data.get("plugins"), dict) else {}
    on = data.get("on") if isinstance(data.get("on"), dict) else {}
    out = {"version": 1, "plugins": plugins,
           "on": {k: v for k, v in on.items() if isinstance(v, bool)}}
    if isinstance(data.get("copies"), dict):
        out["copies"] = data["copies"]
    if isinstance(data.get("update_history"), list):
        out["update_history"] = data["update_history"]
    if error:
        out["error"] = error
    return out


README = """# Your plugins

This folder is Loam's *Yours* layer: the plugins you installed or built, and
`installed.json`, the list of what was installed from where and what is on.
Updates to Loam never touch it. A plugin here with the id of one Loam ships
wins over the shipped one.

Each plugin is a folder named by its id with a `PLUGIN.md` in it. Write one
here by hand (or have an agent write one) and it is listed as *built here*,
off until you turn it on. Installing from a link and turning plugins on and
off are Settings > Plugins, or `bin/settings` for an agent.

`_settings/<id>/` and `_data/<id>/` are reserved for each plugin's own
settings and data. This folder is its own git repository, committed every
15 minutes, so every install, edit and removal has its history here.

The interface is `docs/plugins.md` in the runtime's repository.
"""


def _save_list(data: dict) -> None:
    root = yours_root()
    root.mkdir(parents=True, exist_ok=True)
    if not (root / "README.md").exists():
        (root / "README.md").write_text(README, encoding="utf-8")
    if not (root / ".gitignore").exists():
        (root / ".gitignore").write_text(f"{STAGING}/\n", encoding="utf-8")
    target = root / LIST_FILE
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, target)


def legacy_fingerprint(folder: Path) -> str | None:
    """Verify the pre-v2 hash of paths and bytes for an existing install."""
    if not folder.is_dir() or folder.is_symlink():
        return None
    h, total = hashlib.sha256(), 0

    def failed(exc):
        raise exc

    try:
        for parent, dirs, files in os.walk(folder, onerror=failed):
            dirs[:] = sorted(d for d in dirs if d != ".git")
            linked_dirs = [d for d in dirs if (Path(parent) / d).is_symlink()]
            for name in sorted(files + linked_dirs):
                path = Path(parent) / name
                if path.is_symlink():
                    content = ("link:" + os.readlink(path)).encode()
                elif path.is_file() and path.stat().st_size <= HASH_MAX - total:
                    content = path.read_bytes()
                else:
                    return None
                total += len(content)
                if total > HASH_MAX:
                    return None
                h.update(path.relative_to(folder).as_posix().encode() + b"\0"
                         + hashlib.sha256(content).digest())
    except (OSError, UnicodeError):
        return None
    return h.hexdigest()


def load_list(strict: bool = True) -> dict:
    """Upgrade verified legacy install records before any caller interprets their preferences."""
    listed = _read_list(strict)
    verified = {}
    for pid, line in listed["plugins"].items():
        if not ID.fullmatch(pid) or not isinstance(line, dict):
            continue
        old = line.get("files")
        if (not isinstance(old, str) or not SHA256.fullmatch(old)
                or (line.get("source") or {}).get("kind") != "link"):
            continue
        folder = yours_root() / pid
        digest = fingerprint(folder)
        if digest and legacy_fingerprint(folder) == old:
            verified[pid] = (line, digest)
    if not verified:
        return listed
    with locked():
        # Re-read under the writer lock. Preserve intervening installs and every
        # preference; compare the exact tree again in case an editor was saving.
        current = _read_list(strict)
        changed = False
        for pid, (prior, digest) in verified.items():
            line = current["plugins"].get(pid)
            if line == prior and fingerprint(yours_root() / pid) == digest:
                line["files"] = digest
                changed = True
        if changed:
            _save_list(current)
        return current


def fingerprint(folder: Path) -> str | None:
    """A hash of a folder's paths, modes and contents, so the
    list can say a plugin installed from a link has been edited since."""
    if not folder.is_dir() or folder.is_symlink():
        return None
    h, total = hashlib.sha256(), 0

    def failed(exc):
        raise exc

    try:
        h.update(str(folder.stat().st_mode).encode() + b"\0")
        for parent, dirs, files in os.walk(folder, onerror=failed):
            dirs.sort()
            for name in sorted(dirs + files):
                path = Path(parent) / name
                mode = path.lstat().st_mode
                if stat.S_ISLNK(mode):
                    content = os.fsencode(os.readlink(path))
                elif stat.S_ISDIR(mode):
                    content = b""
                elif stat.S_ISREG(mode) and path.stat().st_size <= HASH_MAX - total:
                    content = path.read_bytes()
                else:
                    return None
                total += len(content)
                if total > HASH_MAX:
                    return None
                h.update(os.fsencode(path.relative_to(folder)) + b"\0")
                h.update(str(mode).encode() + b"\0" + hashlib.sha256(content).digest())
    except OSError:
        return None
    return "v2:" + h.hexdigest()


def _appearance_in_use() -> dict[str, list[str]]:
    try:
        import loam_appearance  # noqa: PLC0415
        # The stored themes alone: the whole reading asks this list for the
        # themes' options, which would ask this function back.
        value = loam_appearance.read_base()
    except Exception:  # noqa: BLE001 -- an unreadable settings file is not the list's failure
        return {}
    used: dict[str, list[str]] = {}
    for kind in ("phone", "desktop"):
        theme = (value.get(kind) or {}).get("theme")
        if theme:
            used.setdefault(theme, []).append(kind)
    return used


def copy_folder(pid: str) -> Path:
    return yours_root() / "_copies" / pid


def update_preference(pid: str, line: dict) -> dict:
    """The saved preference plus the state of the exact folder it would replace."""
    folder = yours_root() / pid
    digest = fingerprint(folder) if folder.is_dir() and not folder.is_symlink() else None
    edited = not digest or digest != line.get("files")
    copied = os.path.lexists(copy_folder(pid))
    changed = edited or copied
    paused = changed or line.get("updates_paused", False)
    return {"auto_update": bool(line.get("auto_update", True) and not paused),
            "updates_paused": bool(paused), "edited": bool(edited),
            "custom_copy": copied, "digest": digest}


def _remember_pauses(listed: dict) -> None:
    dirty = False
    for pid, line in listed["plugins"].items():
        if not isinstance(line, dict) or not ID.fullmatch(pid) or not line.get("source"):
            continue
        pref = update_preference(pid, line)
        if pref["updates_paused"] and not line.get("updates_paused"):
            line.update(auto_update=False, updates_paused=True)
            line.pop("update_consent", None)
            dirty = True
    if dirty:
        _save_list(listed)


def set_auto_update(pid: str, on, replace_changes=False) -> dict:
    _need_id(pid)
    if not isinstance(on, bool):
        raise PluginError("`auto_update` is true or false")
    with locked():
        listed = load_list()
        line = listed["plugins"].get(pid)
        if not line or (line.get("source") or {}).get("kind") != "link":
            raise PluginError("Only a Market or link install has automatic updates.", 409)
        pref = update_preference(pid, line)
        line.pop("update_consent", None)
        if on and (pref["edited"] or pref["custom_copy"] or pid in listed.get("copies", {})):
            raise PluginError("Your changes stay yours. Review a new version with your agent.", 409)
        line.update(auto_update=on, updates_paused=False)
        _save_list(listed)
    return one(pid)


def _row(folder: Path, layer: str, listed: dict) -> dict:
    row = {"id": folder.name, "layer": layer, "folder": str(folder),
           "broken": None, "warnings": []}
    try:
        if not ID.match(folder.name):
            raise ManifestError(f"the folder's name {folder.name!r} is not an id "
                                f"(lower-case letters, digits and dashes)")
        found = read_folder(folder, folder.name)
    except ManifestError as exc:
        row["broken"] = str(exc)
        found = None
    except Exception as exc:  # noqa: BLE001 -- one folder never breaks the list
        row["broken"] = f"it cannot be read ({exc})"
        found = None
    if found:
        row.update(found["manifest"])
        row.update({"format": found["format"], "has": found["has"],
                    "warnings": found["warnings"],
                    "declaration": declaration(found["manifest"], found["has"])})
    if layer == "shipped":
        row["source"] = {"kind": "loam"}
    else:
        line = listed["plugins"].get(folder.name)
        if isinstance(line, dict) and isinstance(line.get("source"), dict):
            src = dict(line["source"])
            row["source"] = src
            row["installed"] = {k: line.get(k) for k in
                                ("installed_at", "installed_by", "version")}
            row["installed"]["confirmed"] = line.get("declaration")
            pref = update_preference(folder.name, line)
            row.update({k: v for k, v in pref.items() if k != "digest"})
            if folder == copy_folder(folder.name):
                row.update(source={"kind": "built"}, upstream=src)

        else:
            row["source"] = {"kind": "built"}
        personal = listed.get("copies", {}).get(folder.name, {})
        if personal.get("base"):
            if personal.get("confirmed"):
                row.setdefault("installed", {})["confirmed"] = personal["confirmed"]
            row.update(personal=True, auto_update=False, updates_paused=True)
    return row


def _rows(listed: dict | None = None) -> list[dict]:
    listed = listed if listed is not None else load_list(strict=False)
    rows: list[dict] = []
    seen_shipped: set[str] = set()
    for root in shipped_roots():
        for folder in _folders(root):
            row = _row(folder, "shipped", listed)
            if folder.name in seen_shipped:
                row["broken"] = row["broken"] or (
                    "another shipped folder has this id; the first one is used")
            seen_shipped.add(folder.name)
            rows.append(row)
    yours = yours_root()
    present = set()
    folders = {p.name: p for p in _folders(yours)}
    folders.update({p.name: p for p in _folders(yours / "_copies")})
    for folder in folders.values():
        present.add(folder.name)
        rows.append(_row(folder, "yours", listed))
    for pid, line in sorted(listed["plugins"].items()):
        if pid not in present and isinstance(line, dict):
            if not ID.fullmatch(pid):
                continue
            missing = _row(yours / pid, "yours", listed)
            missing["broken"] = "it is in the installed list but its folder is gone"
            rows.append(missing)
    # Which row is in force for each id: Yours over Shipped, never a broken one.
    effective: dict[str, dict] = {}
    for row in rows:
        if row["broken"]:
            continue
        held = effective.get(row["id"])
        if held is None or (held["layer"] == "shipped" and row["layer"] == "yours"):
            effective[row["id"]] = row
    in_use = _appearance_in_use()
    for row in rows:
        best = effective.get(row["id"])
        row["effective"] = best is row
        row["shadows"] = None
        row["shadowed_by"] = None
        if best is not None and best is not row and not row["broken"]:
            row["shadowed_by"] = best["layer"]
        if row["effective"] and row["layer"] == "yours":
            under = next((r for r in rows if r["id"] == row["id"]
                          and r["layer"] == "shipped" and not r["broken"]), None)
            if under:
                row["shadows"] = {"layer": "shipped", "version": under.get("version"),
                                  "folder": under["folder"]}
        if row["broken"]:
            row.update({"on": False, "switch": None, "in_use": [], "files": None})
            continue
        theme_only = row["kind"] == ["theme"] and not row["overrides"]
        row["in_use"] = in_use.get(row["id"], []) if (
            row["effective"] and "theme" in row["kind"]) else []
        if any(k in LISTED_ONLY for k in row["kind"]):
            # An agent or a skill (2026-11-13): listed, never on, no control.
            row["switch"] = "listed"
            row["on"] = False
        elif theme_only:
            row["switch"] = "appearance"
            row["on"] = bool(row["in_use"])
        else:
            row["switch"] = "plugin"
            default = row["source"].get("kind") != "built"
            # A shipped plugin's own default (loam-ui, 2026-11-14: read aloud
            # and dictation ship off): `default_on`, read only in that layer.
            # An install is the yes and stays on; built stays off.
            if row["layer"] == "shipped" and isinstance(row.get("default_on"), bool):
                default = row["default_on"]
            row["on"] = row["effective"] and listed["on"].get(row["id"], default)
        row["files"] = f"/v1/plugins/{row['id']}/files/" if row["effective"] else None
    return rows


def listing() -> dict:
    """``GET /v1/plugins``: every plugin folder of both layers."""
    listed = load_list(strict=False)
    out = {"api": API,
           "layers": {"shipped": [str(p) for p in shipped_roots()],
                      "yours": str(yours_root())},
           "plugins": _servers().annotate(_rows(listed))}
    if listed.get("error"):
        out["error"] = listed["error"]
    return out


def one(pid: str) -> dict:
    """``GET /v1/plugins/<id>``: the row in force for an id (or its first
    broken row when none is), with the others under ``rows``."""
    rows = [r for r in _rows() if r["id"] == pid]
    if not rows:
        raise PluginError(f"no plugin {pid!r} in either layer", 404)
    _servers().annotate(rows, logs=True)
    best = next((r for r in rows if r["effective"]), rows[0])
    folder = Path(best["folder"])
    guide = None
    if not best["broken"] and best.get("format") == "PLUGIN.md":
        try:
            guide = parse_frontmatter((folder / "PLUGIN.md").read_text(
                encoding="utf-8"))[1]
        except (OSError, ManifestError, UnicodeDecodeError):
            guide = None
    return {"plugin": {**best, "guide": guide}, "rows": rows}


def themes() -> list[str]:
    """The themes Appearance may choose: the id of every plugin in force
    whose kind includes theme, Latte first. Latte and Plain are always in
    it (loam-ui will not start without them)."""
    found = set(SHIPPED_THEMES)
    for row in _rows():
        if row["effective"] and "theme" in (row.get("kind") or []):
            found.add(row["id"])
    return sorted(found, key=lambda t: (t != "latte", t))


def theme_options() -> dict[str, list[dict]]:
    """Each theme in force that has options, its options as its manifest
    gives them (``options_of``): ``{"latte": [{"id": "agent-shapes",
    "label": ..., "choices": [{"id", "label"}, ...], "default": ...}]}``.
    Appearance validates and fills a theme's option values against this."""
    return {row["id"]: row["options"] for row in _rows()
            if row["effective"] and "theme" in (row.get("kind") or [])
            and row.get("options")}


# ---------------------------------------------------------------------------
# On and off, remove
# ---------------------------------------------------------------------------

def _servers():
    import loam_plugin_servers  # noqa: PLC0415 -- it imports this module
    return loam_plugin_servers


def wanted_servers() -> dict[str, dict]:
    """Every plugin whose server should be running now: in force, on, with
    ``server/`` and something to start it with. ``{id: {"run", "folder",
    "settings", "default"}}``."""
    out = {}
    for row in _rows():
        if not (row["effective"] and row.get("on") and row.get("switch") == "plugin"):
            continue
        spec = server_spec(row)
        if spec:
            out[row["id"]] = {**spec, "folder": row["folder"],
                              "settings": row.get("settings")}
    return out


def _contributions_follow(reason: str) -> dict | None:
    """Bring every lane into line with the plugins now in force (timers,
    the personas' assembled files). Never raises, as _servers_follow."""
    try:
        import loam_contributions  # noqa: PLC0415
        if not CONTRIBUTIONS_FOLLOW:
            return None
        return loam_contributions.apply(reason)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"the contributions could not be brought into line: {exc}"}


# Off in a test that has no lanes to render (the env var, not a constant
# edit: lib/loam_contributions.apply runs bin/provision for every persona).
CONTRIBUTIONS_FOLLOW = os.environ.get("LOAM_CONTRIBUTIONS_FOLLOW", "1") != "0"


def _servers_follow(pid: str, restart: bool = False) -> dict | None:
    """Bring one plugin's server to what it should be: started when wanted
    (``restart`` makes it a fresh start even if it runs: an update), stopped
    when not. Never raises: a server that cannot start is the row's status,
    not the switch's failure."""
    try:
        want = wanted_servers().get(pid)
        srv = _servers()
        if want is None:
            return srv.stop(pid)
        return srv.start(pid, want, fresh=restart)
    except Exception as exc:  # noqa: BLE001 -- the switch is written; the unit is reported
        return {"error": f"its server could not be brought into line: {exc}"}


def restart_server(pid: str, by: str | None = None) -> dict:
    """``POST /v1/plugins/<id>/restart``: a fresh start of its server, and
    only its server (after an edit in place, or to clear a failing one)."""
    _need_id(pid)
    rows = [r for r in _rows() if r["id"] == pid]
    if not rows:
        raise PluginError(f"no plugin {pid!r} in either layer", 404)
    row = next((r for r in rows if r["effective"]), None)
    if row is None:
        raise PluginError(f"{pid} is broken: {rows[0]['broken']}", 409)
    if not server_spec(row):
        raise PluginError(f"{pid} has no server to restart (no server/ with "
                          f"server.run, server/main.py or server/main.js)", 409)
    if not row.get("on"):
        raise PluginError(f"{pid} is off; turn it on and its server starts", 409)
    _servers().start(pid, wanted_servers()[pid], fresh=True)
    return one(pid)


def _need_id(pid) -> str:
    if not isinstance(pid, str) or not ID.match(pid):
        raise PluginError(f"{pid!r} is not a plugin id", 404)
    return pid


def set_on(pid: str, on, by: str | None = None) -> dict:
    """``PATCH /v1/plugins/<id>`` ``{"on": true|false}``."""
    _need_id(pid)
    if not isinstance(on, bool):
        raise PluginError("`on` is true or false")
    with locked():
        rows = [r for r in _rows() if r["id"] == pid]
        if not rows:
            raise PluginError(f"no plugin {pid!r} in either layer", 404)
        row = next((r for r in rows if r["effective"]), None)
        if row is None:
            raise PluginError(f"{pid} is broken, so it cannot be turned on or off: "
                              f"{rows[0]['broken']}", 409)
        if row["switch"] == "listed":
            raise PluginError(
                f"{pid} is {'an' if row['kind'][0] == 'agent' else 'a'} "
                f"{row['kind'][0]} package: Loam lists agents and skills and "
                f"installs or runs none yet, so it has no switch", 409)
        if row["switch"] == "appearance":
            raise PluginError(
                f"{pid} is a theme: it is on where Appearance has chosen it. "
                f"Choose it with PATCH /v1/settings/appearance "
                f"(appearance.desktop.theme, appearance.phone.theme)", 409)
        data = load_list()
        data["on"][pid] = on
        _save_list(data)
    _servers_follow(pid)
    if row.get("contributes"):
        _contributions_follow(f"{pid} turned {'on' if on else 'off'}")
    return one(pid)


def remove(pid: str, by: str | None = None, *, retiring_source: dict | None = None) -> dict:
    """``DELETE /v1/plugins/<id>``: the Yours copy goes (its history stays in
    the folder's git); a shipped plugin cannot be removed, only turned off. A
    theme Appearance had chosen goes back to Latte on that device kind,
    unless a shipped plugin of the same id takes its place. The id's switch
    stays when a shipped copy takes over, and goes with the last copy."""
    _need_id(pid)
    with locked():
        rows = [r for r in _rows() if r["id"] == pid]
        yours = next((r for r in rows if r["layer"] == "yours"), None)
        if yours is None:
            if rows:
                raise PluginError(f"{pid} is shipped with Loam, and an update would "
                                  f"bring it back: turn it off instead", 409)
            raise PluginError(f"no plugin {pid!r} in either layer", 404)
        folder = Path(yours["folder"])
        data = load_list()
        _remember_pauses(data)
        if retiring_source is not None:
            line = data["plugins"].get(pid) or {}
            if (yours.get("source") != retiring_source or yours.get("edited")
                    or yours.get("personal") or os.path.lexists(copy_folder(pid))):
                raise PluginError("Your copy changed and was kept.", 409)
        # Its server stops before its folder goes (2026-11-13).
        _servers().stop(pid)
        if folder.exists() or folder.is_symlink():
            trash = Path(tempfile.mkdtemp(prefix="remove-", dir=_staging()))
            os.replace(folder, trash / pid)
            if retiring_source is not None and (
                    not line.get("files") or fingerprint(trash / pid) != line["files"]
                    or os.path.lexists(copy_folder(pid))):
                os.replace(trash / pid, folder)
                _servers_follow(pid)
                raise PluginError("Your copy changed and was kept.", 409)
            shutil.rmtree(trash, ignore_errors=True)
        data.get("copies", {}).pop(pid, None)
        kept_original = folder == copy_folder(pid) and (yours_root() / pid).is_dir()
        line = None if kept_original else data["plugins"].pop(pid, None)
        # The switch is the id's: it stays for the shipped copy that takes over.
        stays = kept_original or any(r["layer"] == "shipped" and not r["broken"] for r in rows)
        if not stays:
            data["on"].pop(pid, None)
        _save_list(data)
        reset = []
        if not stays and yours.get("in_use"):
            import loam_appearance  # noqa: PLC0415
            loam_appearance.update({k: {"theme": "latte"} for k in yours["in_use"]})
            reset = list(yours["in_use"])
    # A shipped copy that takes over runs its own server, if it is on.
    _servers_follow(pid)
    after = [r for r in _rows() if r["id"] == pid]
    if yours.get("contributes") or any(r.get("contributes") for r in after):
        _contributions_follow(f"{pid} removed")
    return {"removed": pid, "was": {"version": yours.get("version"),
                                    "source": yours.get("source")},
            "appearance_reset": reset,
            "plugin": next((r for r in after if r["effective"]), None),
            "installed_line": bool(line)}


def seed_release_copy(source: Path, pid: str) -> None:
    from loam_files import _renameat2

    with locked(), tempfile.TemporaryDirectory(dir=_staging()) as tmp:
        if os.path.lexists(copy_folder(pid)):
            raise PluginError("Your copy is already here.", 409)
        staged = Path(tmp) / pid
        shutil.copytree(source, staged, symlinks=True)
        _renameat2(-100, str(staged), -100, str(yours_root() / pid))


def _staging() -> Path:
    d = yours_root() / STAGING
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Install from a link
# ---------------------------------------------------------------------------
#
# A person pastes what they have (2026-11-18, "if someone wants to sideload
# something, they should be able to do it fairly easily"): a repository's
# address, `owner/repo`, a `.git` or `git@` address, a `/tree/<ref>[/<folder>]`,
# a `/blob/…/PLUGIN.md`, a `/commit/<sha>`, a release page, with or without a
# trailing slash, query or fragment. What the link leaves out is worked out:
# with no tag, the newest release tag for that plugin (`<id>-v1.2.0`, else a
# plain `v1.2.0`), else the commit the default branch is on; with no folder,
# the one plugin in the repository, or the ones to pick from. A branch is
# installed at the commit it points at now, and the answer says so in `note`.
# Every refusal is a sentence a person can act on, with a `reason`
# (empty, not-github, unreachable, timeout, no-folder, no-plugin, manifest,
# short-commit, no-ref) for a page to draw by.
#
# What is installed stays pinned: the source records the tag or the commit,
# and the confirm token is the commit's, so nothing here moves the wall.

GITHUB = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)"
    r"(?:\.git)?(?:/(.*?))?/?$")
RAW = re.compile(r"^(?:https?://)?raw\.githubusercontent\.com/([A-Za-z0-9_.-]+)/"
                 r"([A-Za-z0-9_.-]+)/(.+)$")
SSH = re.compile(r"^(?:ssh://)?git@github\.com[:/]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)"
                 r"(?:\.git)?/?$")
SHA = re.compile(r"^[0-9a-f]{40}$")
# A release tag: a version, alone or after the plugin's name (`v1.2.0`,
# `1.2`, `foundry-v1.0.1`, `foundry@1.0.1`). A pre-release is never newest.
RELEASE = re.compile(r"^(?:(?P<name>.+?)[-_@/])?v?(?P<ver>\d+(?:\.\d+){0,3})$")
FIND_DEPTH = 3
CHOICES_MAX = 24
LINK_PICTURE_MAX = 600 * 1024


def _not_github(what: str) -> PluginError:
    return PluginError("That isn’t a GitHub link. Paste the address of the plugin’s "
                       "repository on GitHub, like github.com/owner/repo.",
                       400, {"reason": "not-github"})


def parse_source(body: dict) -> dict:
    """``{"url"}`` (and optionally ``ref``, ``path``) into a repository, a ref
    and a folder in it. ``ref`` is None when the link names none (``fetch``
    finds the newest release) and ``path`` is "" when it names no folder
    (``fetch`` finds the plugin's). A ``/tree/<ref>/<path>`` whose ref has a
    slash in it is split at its first one here and rejoined by ``fetch``,
    which knows the repository's refs."""
    url = body.get("url") if isinstance(body.get("url"), str) else body.get("repo")
    if not isinstance(url, str) or not url.strip():
        raise PluginError("Paste a GitHub link to the plugin: its repository, like "
                          "github.com/owner/repo, is enough.", 400, {"reason": "empty"})
    url = url.strip().strip("<>\"'").strip()
    url = re.split(r"[?#]", url, maxsplit=1)[0]
    if re.match(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+/(?:tree|blob|commit|releases)/", url):
        url = "https://github.com/" + url      # a link with its address left off
    owner = repo = None
    how, ref, path = None, None, ""
    if re.match(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+$", url):
        owner, repo = url.split("/")
        repo = repo.removesuffix(".git")
    elif m := SSH.match(url):
        owner, repo = m.groups()
    elif m := RAW.match(url):
        owner, repo, rest = m.groups()
        parts = [p for p in rest.split("/") if p]
        how, ref = "blob", parts[0]
        path = "/".join(parts[1:])
    elif m := GITHUB.match(url):
        owner, repo, rest = m.groups()
        parts = [p for p in (rest or "").split("/") if p]
        if parts[:1] in (["tree"], ["blob"]) and len(parts) > 1:
            how, ref, path = parts[0], parts[1], "/".join(parts[2:])
        elif parts[:1] == ["commit"] and len(parts) > 1:
            how, ref = "commit", parts[1]
        elif parts[:2] == ["releases", "tag"] and len(parts) > 2:
            how, ref = "release", "/".join(parts[2:])
        # Anything else of the repository's own pages (its releases list,
        # `/releases/latest`, tags, issues) is the repository.
    else:
        raise _not_github(url)
    if not owner or owner in (".", "..") or repo in (".", "..", ""):
        raise _not_github(url)
    if how == "blob":
        # A file's link: the folder it is in (PLUGIN.md's is the plugin's).
        path = path.rsplit("/", 1)[0] if "/" in path else ""
    if isinstance(body.get("ref"), str) and body["ref"].strip():
        ref = body["ref"].strip()
    if isinstance(body.get("path"), str):
        path = body["path"]
    path = (path or "").strip().strip("/")
    parts = [p for p in path.split("/") if p]
    if any(p in (".", "..") for p in parts):
        raise PluginError("The folder in that link isn’t a path inside the repository.",
                          400, {"reason": "not-github"})
    return {"repo": f"{owner}/{repo}", "ref": ref, "path": "/".join(parts)}


def _git_base() -> str:
    return (os.environ.get("LOAM_PLUGIN_GIT_BASE") or "https://github.com/").rstrip("/") + "/"


def _git_env(token: str | None = None) -> dict:
    if token:
        import loam_github_app  # noqa: PLC0415
        env = loam_github_app.git_token_env(token)
    else:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("GIT_CONFIG", "GIT_SSH"))}
    # No rewrite or helper of this box's own git config applies: a link is
    # fetched as the link says, from the address it names.
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/true",
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"})
    return env


def _git(args: list[str], cwd: Path | None, env: dict) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True,
                              text=True, timeout=GIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise PluginError("GitHub didn’t answer in time. Try again in a moment.",
                          504, {"reason": "timeout"}) from None


def _github_connected() -> bool:
    try:
        import loam_github_app  # noqa: PLC0415
        return loam_github_app.status() in ("installed", "authorized", "unhealthy")
    except Exception:  # noqa: BLE001 -- no integration here
        return False


def _remote(src: dict) -> tuple[str, dict]:
    """The repository's address and a git environment that can read it:
    anonymous first, then the owner's GitHub connection (the App's read
    token) for a private repository it is installed on."""
    url = _git_base() + src["repo"]
    env = _git_env()
    probe = _git(["ls-remote", "--symref", url], None, env)
    if probe.returncode == 0:
        return url, env | {"_refs": probe.stdout}
    connected = False
    if _git_base() == "https://github.com/":
        try:
            import loam_github_app  # noqa: PLC0415
            token = loam_github_app.read_token(src["repo"])["token"]
        except Exception:  # noqa: BLE001 -- no app, or not installed there
            token = None
        connected = token is not None or _github_connected()
        if token:
            env = _git_env(token)
            probe = _git(["ls-remote", "--symref", url], None, env)
            if probe.returncode == 0:
                return url, env | {"_refs": probe.stdout}
    said = (probe.stderr or "").strip().splitlines()
    print(f"plugins: {src['repo']} cannot be read: {said[-1] if said else 'no answer'}",
          file=sys.stderr, flush=True)
    raise PluginError(
        f"Nothing could be read at github.com/{src['repo']}. Check the link. If the "
        "repository is private, " + (
            "your GitHub connection can’t see it yet: add it to the Loam app’s "
            "repositories in Settings › Connections › GitHub, then try again."
            if connected else
            "connect GitHub in Settings › Connections first, then try again."),
        404, {"reason": "unreachable", "repo": src["repo"], "github": connected})


def _refs(text: str) -> dict:
    """``ls-remote --symref`` as tags (peeled), heads and the default branch."""
    tags: dict[str, str] = {}
    heads: dict[str, str] = {}
    default = head = None
    for line in text.splitlines():
        if line.startswith("ref: "):
            target, _, name = line[5:].partition("\t")
            if name.strip() == "HEAD":
                default = target.strip().removeprefix("refs/heads/")
            continue
        sha, _, name = line.partition("\t")
        name, sha = name.strip(), sha.strip()
        if name.startswith("refs/tags/"):
            tag = name[len("refs/tags/"):]
            if tag.endswith("^{}"):
                tags[tag[:-3]] = sha
            else:
                tags.setdefault(tag, sha)
        elif name.startswith("refs/heads/"):
            heads[name[len("refs/heads/"):]] = sha
        elif name == "HEAD":
            head = sha
    if default not in heads:
        default = next((b for b, s in heads.items() if s == head), None) \
            or ("main" if "main" in heads else next(iter(heads), None))
    return {"tags": tags, "heads": heads, "default": default}


def newest_tag(tags, names=()) -> str | None:
    """The newest release tag: of one of ``names`` (`<name>-v1.2.0`) if any
    is, else a plain version (`v1.2.0`); None when there is neither."""
    named, plain = [], []
    wanted = {n.lower() for n in names if n}
    for tag in tags:
        m = RELEASE.match(tag)
        if not m:
            continue
        key = tuple(int(x) for x in m.group("ver").split("."))
        key = key + (0,) * (4 - len(key))
        name = (m.group("name") or "").lower()
        if not name:
            plain.append((key, tag))
        elif name in wanted:
            named.append((key, tag))
    pick = named or plain
    return max(pick)[1] if pick else None


def _ref_of(src: dict, refs: dict) -> tuple[str, str, str, str, str | None]:
    """A named ref as (ref, commit, kind, path, note). A `/tree/` ref with a
    slash in it is the longest prefix the repository has. A branch is its
    commit now, and the note says so; the install is pinned to that commit."""
    segs = [src["ref"], *[p for p in src["path"].split("/") if p]]
    if src.get("reviewed") and SHA.fullmatch(src["ref"]):
        return src["ref"], src["ref"], "commit", src["path"], None
    for i in range(len(segs), 0, -1):
        name, rest = "/".join(segs[:i]), "/".join(segs[i:])
        if name in refs["tags"]:
            return name, refs["tags"][name], "tag", rest, None
        if name in refs["heads"]:
            sha = refs["heads"][name]
            return sha, sha, "commit", rest, (
                f"{name} is a branch, so this installs the commit it is on now "
                f"({sha[:7]}). It stays at that commit until you install it again.")
    ref = src["ref"]
    if SHA.match(ref.lower()):
        return ref.lower(), ref.lower(), "commit", src["path"], None
    if re.match(r"^[0-9a-f]{7,39}$", ref.lower()):
        raise PluginError(f"That link names a short commit ({ref}). Use the link to "
                          "the whole commit, or to a release.", 400,
                          {"reason": "short-commit"})
    raise PluginError(f"github.com/{src['repo']} has no release or branch called "
                      f"{ref}. Paste the repository’s own address and Loam finds "
                      "its newest release.", 404, {"reason": "no-ref"})


def _checkout(url: str, env: dict, what: str, into: Path) -> str:
    """The repository at one tag or commit, under ``into``; its commit."""
    into.mkdir(parents=True)
    for args in (["init", "-q"],
                 ["fetch", "-q", "--depth", "1", "--no-tags", url, what],
                 ["-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD"]):
        done = _git(args, into, env)
        if done.returncode != 0:
            said = (done.stderr or "").strip().splitlines()
            print(f"plugins: fetching {url} {what}: "
                  f"{said[-1] if said else done.returncode}", file=sys.stderr, flush=True)
            raise PluginError("GitHub couldn’t send the plugin just now. Try again "
                              "in a moment.", 502, {"reason": "unreachable"})
    return _git(["rev-parse", "HEAD"], into, env).stdout.strip()


def plugin_folders(root: Path, depth: int = FIND_DEPTH) -> list[str]:
    """The folders under ``root`` (itself first) holding a PLUGIN.md, as paths
    relative to it ("" for itself); dot-folders and node_modules are skipped,
    and a plugin's own folder is not looked in further."""
    found: list[str] = []

    def walk(d: Path, rel: str, left: int) -> None:
        if (d / "PLUGIN.md").is_file():
            found.append(rel)
            return
        if left == 0:
            return
        try:
            kids = sorted(p for p in d.iterdir() if p.is_dir() and not p.is_symlink())
        except OSError:
            return
        for k in kids:
            if k.name.startswith(".") or k.name in ("node_modules", "__pycache__"):
                continue
            walk(k, f"{rel}/{k.name}".lstrip("/"), left - 1)

    walk(root, "", depth)
    return found


def _data_uri(folder: Path, rel, pattern, limit: int) -> str | None:
    """A picture in the plugin's folder as a data: address, for a page to
    show before anything is installed; None when it is not one."""
    if not isinstance(rel, str) or not pattern.match(rel):
        return None
    f = folder / rel
    try:
        if f.is_symlink() or not f.is_file() or f.stat().st_size > limit \
                or folder.resolve() not in f.resolve().parents:
            return None
        data = f.read_bytes()
    except OSError:
        return None
    import base64  # noqa: PLC0415
    ext = rel.rsplit(".", 1)[-1].lower()
    mime = {"svg": "image/svg+xml", "jpg": "image/jpeg"}.get(ext, f"image/{ext}")
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def pictures_of(folder: Path, manifest: dict) -> dict:
    """Its icon and cover (a theme's `image`, `image_dark`), as data: addresses."""
    out = {}
    icon = _data_uri(folder, manifest.get("icon"), ICON, 256 * 1024)
    if icon:
        out["icon"] = icon
    for key, field in (("cover", "image"), ("cover_dark", "image_dark")):
        got = _data_uri(folder, manifest.get(field), IMAGE, LINK_PICTURE_MAX)
        if got:
            out[key] = got
    return out


def _choice(folder: Path, rel: str) -> dict:
    try:
        m = read_folder(folder, None)["manifest"]
    except ManifestError:
        return {"path": rel, "id": None, "name": rel.rsplit("/", 1)[-1] or rel,
                "broken": True}
    icon = pictures_of(folder, m).get("icon")
    return {"path": rel, "id": m["id"], "name": m["name"], "version": m["version"],
            "author": m["author"], "kind": m["kind"],
            "description": m.get("description"), **({"icon": icon} if icon else {})}


def _folder_of(base: Path, path: str) -> str:
    """Determine the plugin folder named by a link. A file URL can use either /tree/ or
    /blob/, and both refer to its parent directory.
    """
    if path and (base / path).is_file():
        return path.rsplit("/", 1)[0] if "/" in path else ""
    return path


def fetch(src: dict, into: Path) -> dict:
    """The plugin a link names, checked out under ``into``: ``{"folder",
    "source", "note"}``; or, when the repository holds several plugins and
    the link names none of them, ``{"choose": [...], "repo", "ref"}``."""
    url, env = _remote(src)
    refs = _refs(env.pop("_refs"))
    note = None
    path = src["path"]
    if src["ref"]:
        ref, commit, ref_kind, path, note = _ref_of(src, refs)
        head = _checkout(url, env, f"refs/tags/{ref}" if ref_kind == "tag" else commit,
                         into / "repo")
        if ref_kind == "commit" and head != commit:
            raise PluginError("GitHub sent a different commit than the link names. "
                              "Try again in a moment.", 502, {"reason": "unreachable"})
    else:
        # No ref: read the default branch to see what is there, then go to
        # the newest release of the plugin found.
        branch = refs["default"]
        if not branch:
            raise PluginError(f"github.com/{src['repo']} is empty: there’s nothing in "
                              "it to install yet.", 404, {"reason": "no-plugin"})
        look = into / "look"
        head = _checkout(url, env, refs["heads"][branch], look)
        path = _folder_of(look, path)
        if path and not (look / path).is_dir():
            raise PluginError(f"github.com/{src['repo']} has no folder {path}.", 404,
                              {"reason": "no-folder"})
        found = [f"{path}/{f}".strip("/") for f in plugin_folders(look / path)]
        if len(found) > 1:
            choices = [_choice(look / f, f) for f in found[:CHOICES_MAX]]
            for c in choices:
                # Each one's own newest release, as a link that names it.
                tag = newest_tag(refs["tags"], [c["path"].rsplit("/", 1)[-1], c.get("id")])
                c["link"] = f"https://github.com/{src['repo']}/tree/{tag or head}/{c['path']}"
            return {"choose": choices, "repo": src["repo"], "ref": None}
        if found:
            path = found[0]
        names = [src["repo"].split("/")[1], path.rsplit("/", 1)[-1]]
        if found:
            try:
                names.append(read_folder(look / path, None)["manifest"]["id"])
            except ManifestError:
                pass
        tag = newest_tag(refs["tags"], names)
        if tag:
            ref, ref_kind = tag, "tag"
            if refs["tags"][tag] == head:
                os.replace(look, into / "repo")
            else:
                head = _checkout(url, env, f"refs/tags/{tag}", into / "repo")
                shutil.rmtree(look, ignore_errors=True)
        else:
            ref, ref_kind = head, "commit"
            os.replace(look, into / "repo")
            note = (f"It has no release yet, so this installs the newest commit on "
                    f"{branch} ({head[:7]}). It stays at that commit until you "
                    "install it again.")
    repo_dir = into / "repo"
    path = _folder_of(repo_dir, path)
    if path and not (repo_dir / path).is_dir():
        raise PluginError(f"github.com/{src['repo']} has no folder {path} at "
                          f"{ref if ref_kind == 'tag' else ref[:7]}.", 404,
                          {"reason": "no-folder"})
    if not (repo_dir / path / "PLUGIN.md").is_file() \
            and not (repo_dir / path / "manifest.json").is_file():
        found = [f"{path}/{f}".strip("/") for f in plugin_folders(repo_dir / path) if f]
        # A tag named for one of them (`foundry-v1.0.1`) is that one's.
        named = RELEASE.match(ref) if ref_kind == "tag" else None
        mine = [f for f in found if named and named.group("name")
                and named.group("name").lower() == f.rsplit("/", 1)[-1].lower()]
        if len(mine) == 1:
            found = mine
        if len(found) > 1:
            choices = [_choice(repo_dir / f, f) for f in found[:CHOICES_MAX]]
            for c in choices:
                c["link"] = f"https://github.com/{src['repo']}/tree/{ref}/{c['path']}"
            return {"choose": choices, "repo": src["repo"], "ref": ref}
        if not found:
            where = f"{path} in github.com/{src['repo']}" if path \
                else f"github.com/{src['repo']}"
            raise PluginError(f"There’s no Loam plugin in {where}. A Loam plugin is a "
                              "folder with a PLUGIN.md in it; check the link with "
                              "whoever shared it.", 422,
                              {"reason": "no-plugin", "repo": src["repo"]})
        path = found[0]
    folder = repo_dir / path if path else repo_dir
    web = f"https://github.com/{src['repo']}/tree/{head}" + (f"/{path}" if path else "")
    return {"folder": folder, "note": note,
            "source": {"kind": "link", "repo": src["repo"], "path": path,
                       "ref": ref, "ref_kind": ref_kind, "commit": head, "url": web}}


def pinned_link(source: dict) -> str:
    """The link a source is pinned at: its tag, or its whole commit."""
    return (f"https://github.com/{source['repo']}/tree/"
            f"{source['ref'] if source.get('ref_kind') == 'tag' else source['commit']}"
            + (f"/{source['path']}" if source.get("path") else ""))


def _token(pid: str, version: str, source: dict, decl: dict, replaces) -> str:
    blob = json.dumps({"id": pid, "version": version, "repo": source["repo"],
                       "path": source["path"], "commit": source["commit"],
                       "declaration": decl, "replaces": replaces},
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# Only immutable reviewed sources use this small, process-local preview cache.
# Every confirmation still checks current consent, edits and update preferences.
_preview_inputs = OrderedDict()


def _install_input(got):
    folder, source = got["folder"], got["source"]
    try:
        found = read_folder(folder, None)
    except ManifestError as exc:
        where = f"{source['path'] or source['repo'].split('/')[1]}"
        raise PluginError(f"The plugin in {where} has a PLUGIN.md Loam can’t read, "
                          f"so it can’t be installed until its maker fixes it: "
                          f"{exc}.", 422,
                          {"reason": "no-plugin" if "no PLUGIN.md" in str(exc)
                           else "manifest", "repo": source["repo"]}) from None
    return {"source": source, "found": found, "pictures": pictures_of(folder, found["manifest"]),
            "note": got.get("note")}


def install(body: dict, by: str | None = None, *, automatic: dict | None = None) -> dict:
    """``POST /v1/plugins``: the first call answers what the plugin is and
    declares, and a ``confirm`` token; the second, with that token, installs.

    A token is the hash of the id, version, repository, folder, commit, the
    declaration and what it would replace, so it confirms exactly what the
    first answer showed: if any of it is different by the second call (a tag
    moved, the Yours copy changed), the second call is a 409 with the new
    answer and installs nothing."""
    if not isinstance(body, dict):
        raise PluginError("send {\"url\": ...}")
    confirm = body.get("confirm")
    if confirm is not None and not isinstance(confirm, str):
        raise PluginError("`confirm` is the token the first answer gave")
    src = parse_source(body)
    work = Path(tempfile.mkdtemp(prefix="install-", dir=_staging()))
    try:
        import loam_market_asset
        asset = None
        pin = (src["repo"], src["ref"], src["path"])
        if body.get("via") == "market" and SHA.fullmatch(src["ref"] or ""):
            import loam_marketplace as market
            src["reviewed"] = True
            asset = market.asset_for(src)
        with _threads:
            prepared = _preview_inputs.get(pin) if asset and confirm is not None else None
        folder = None
        if prepared is None:
            got = fetch(src, work)
            if "choose" in got:
                if confirm is not None:
                    raise PluginError("That repository holds several plugins; pick one first.",
                                      409, {"reason": "choose", **got})
                return {"installed": False, **got}
            folder = got["folder"]
            prepared = _install_input(got)
            if asset:
                shutil.rmtree(folder / ".git", ignore_errors=True)
                prepared["fingerprint"] = fingerprint(folder)
        found, source = prepared["found"], prepared["source"]
        manifest = found["manifest"]
        pid = manifest["id"]
        decl = declaration(manifest, found["has"])
        with locked():
            listed = load_list()
            rows = [r for r in _rows(listed) if r["id"] == pid]
            mine = next((r for r in rows if r["layer"] == "yours"), None)
            shipped = next((r for r in rows if r["layer"] == "shipped"
                            and not r["broken"]), None)
            replaces = None
            if mine:
                replaces = {"version": mine.get("version"), "source": mine.get("source"),
                            "edited": mine.get("edited", mine["source"].get("kind")
                                               == "built"),
                            "broken": mine["broken"]}
            previous = listed["plugins"].get(pid) or {}
            if mine and mine.get("updates_paused"):
                previous.update(auto_update=False, updates_paused=True)
            if previous and mine and mine.get("edited"):
                replaces["files"] = fingerprint(yours_root() / pid)
            if mine and (mine.get("personal") or mine.get("custom_copy")):
                replaces["personal"] = True
            if previous and mine and mine.get("custom_copy"):
                # Installs always target the original, beneath the custom copy.
                replaces.update(source=previous.get("source"), version=previous.get("version"))
            prior = previous.get("declaration")
            preview = {
                "installed": False, "id": pid, "name": manifest["name"],
                "version": manifest["version"], "author": manifest["author"],
                "api": manifest["api"], "kind": manifest["kind"],
                "places": manifest["places"], "source": source,
                "declaration": decl, "added": added(prior, decl),
                "replaces": replaces,
                "shadows": ({"layer": "shipped", "version": shipped.get("version")}
                            if shipped else None),
                "warnings": found["warnings"],
                "description": manifest.get("description"),
                # What was worked out from the link, said in a line (a branch
                # is its commit now), and the request that names exactly this
                # one, for the yes to send back.
                "note": prepared["note"],
                "request": {"url": source["repo"], "path": source["path"],
                            "ref": source["ref"]},
                "link": pinned_link(source),
                "pictures": prepared["pictures"],
                "confirm": _token(pid, manifest["version"], source, decl, replaces)}
            listed_only = [k for k in manifest["kind"] if k in LISTED_ONLY]
            if listed_only:
                # Parsed and shown, never installed yet (2026-11-13).
                preview["installable"] = False
                preview["why"] = (f"Loam reads and lists {listed_only[0]} packages "
                                  f"and installs none yet")
            if confirm is None:
                if asset and prepared.get("fingerprint") and source["ref_kind"] == "commit":
                    _preview_inputs[pin] = prepared
                    _preview_inputs.move_to_end(pin)
                    if len(_preview_inputs) > 8:
                        _preview_inputs.popitem(last=False)
                return preview
            if listed_only:
                raise PluginError(f"{pid} is {'an' if listed_only[0] == 'agent' else 'a'}"
                                  f" {listed_only[0]} package: {preview['why']}. "
                                  f"Nothing was installed", 409, {"preview": preview})
            if confirm != preview["confirm"]:
                raise PluginError(
                    f"`confirm` is not what {pid} answers now (its declaration, "
                    f"its commit and what it would replace give "
                    f"{preview['confirm']}). Nothing was installed; read the "
                    f"answer under `preview` and confirm it if it is a yes", 409,
                    {"preview": preview})
            if mine and (replaces["edited"] or replaces.get("personal") or mine.get("broken")):
                raise PluginError("Your changes stay yours. Review this version with your agent.", 409)
            if automatic is not None:
                pref = update_preference(pid, previous)
                if (pid != automatic["id"] or previous.get("source") != automatic["base"]
                        or source["commit"] != automatic["commit"] or not prior
                        or preview["added"] or not pref["auto_update"] or not pref["digest"]):
                    raise PluginError("The install or its update preference changed. Review the update.", 409)
            if asset:
                release = loam_market_asset.verified(asset, work, prepared.get("fingerprint"), fingerprint)
                if release is not None:
                    folder = release
            if folder is None:
                folder = fetch(src, work)["folder"]
            shutil.rmtree(folder / ".git", ignore_errors=True)
            files = fingerprint(folder)
            if files:
                import loam_plugin_review
                loam_plugin_review.snapshot(folder, pid)
            target = yours_root() / pid
            from loam_files import _renameat2
            import loam_plugin_review as review
            replaced = bool(mine)
            if os.path.lexists(copy_folder(pid)):
                raise PluginError("Your copy changed during the update.", 409)
            if replaced:
                # Keep both trees named throughout the swap and inspect the exact
                # old inode before recording this update or discarding anything.
                review.exchange(folder, target)
                if (not previous.get("files") or fingerprint(folder) != previous["files"]
                        or os.path.lexists(copy_folder(pid))):
                    review.exchange(folder, target)
                    raise PluginError("Your changes stay yours. Review this version with your agent.", 409)
            else:
                try:
                    _renameat2(-100, str(folder), -100, str(target))
                except FileExistsError:
                    raise PluginError("A local copy appeared during the update. Your files were kept.", 409)
            # Where the yes was given (2026-11-18): `market` or `link`, so the
            # page can mark a plugin installed from a link someone shared.
            via = body.get("via") if body.get("via") in ("market", "link") else None
            listed["plugins"][pid] = {
                "source": {**source, **({"via": via} if via else {})},
                "version": manifest["version"],
                "declaration": decl, "confirmed": preview["confirm"],
                "installed_at": _now(), "installed_by": by or lc.OWNER_ID,
                "files": files, "auto_update": previous.get("auto_update", True)}
            if previous.get("updates_paused"):
                listed["plugins"][pid]["updates_paused"] = True
            if automatic is not None:
                import uuid
                listed.setdefault("update_history", []).append({
                    "kind": "plugin-update", "id": uuid.uuid4().hex, "ts": _now(),
                    "actor": "loam", "agent": "loam", "title": manifest["name"],
                    "from_version": previous["version"], "version": manifest["version"],
                    "plugin": pid, "source": source["url"],
                    "outcome": f"updated automatically to {manifest['version']}"})
            if mine is None:
                # A new copy in Yours is on: the yes to install it was the yes.
                # An update keeps whatever its switch was.
                listed["on"][pid] = True
            try:
                _save_list(listed)
            except BaseException:
                if replaced:
                    review.exchange(folder, target)
                else:
                    os.replace(target, folder)
                raise
        if any(k.get("source") for k in manifest.get("keys", [])):
            import loam_keys  # noqa: PLC0415
            loam_keys.sync(refresh_sources=True)
        server = _servers_follow(pid, restart=True)
        out = one(pid)
        if (mine or {}).get("contributes") or (out.get("plugin") or {}).get("contributes"):
            _contributions_follow(f"{pid} installed")
        return {"installed": True, "id": pid, "plugin": out["plugin"],
                "replaced": replaces, "source": source, "server": server}
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------------
# A plugin's files, for the page
# ---------------------------------------------------------------------------

def file_for(pid: str, rel: list[str]) -> tuple[Path, str]:
    """``GET /v1/plugins/<id>/files/<path>``: the file and its type, from
    the folder in force for the id. Only ``page/``, ``assets/``,
    ``theme.css``, ``PLUGIN.md``, the files a theme names as its
    ``image`` and ``image_dark``, and a theme's ``fonts.css`` and the
    ``.woff2``/``.woff`` files beside it; a path that leaves the folder (``..``,
    a link pointing out) is not found."""
    _need_id(pid)
    if not rel or any(p in ("", ".", "..") or "\0" in p or "/" in p or "\\" in p
                      for p in rel):
        raise PluginError("no such file", 404)
    row = next((r for r in _rows() if r["id"] == pid and r["effective"]), None)
    shots = {s.get(k) for s in ((row or {}).get("screenshots") or [])
             for k in ("src", "src_dark")}
    cover = "/".join(rel) in ({(row or {}).get(k)
                               for k in ("image", "image_dark", "icon")} | shots) - {None}
    font = (len(rel) == 1 and "theme" in ((row or {}).get("kind") or [])
            and (rel[0] in THEME_FILES or rel[0].lower().endswith(FONT_SUFFIXES)))
    if not ((len(rel) > 1 and rel[0] in SERVED_DIRS)
            or (len(rel) == 1 and rel[0] in SERVED_FILES) or cover or font):
        raise PluginError("a plugin serves its page/ and assets/ files, theme.css, "
                          "PLUGIN.md, its icon, its screenshots, a theme's cover "
                          "image and its fonts, nothing else", 404)
    if row is None:
        raise PluginError(f"no plugin {pid!r} is in force", 404)
    base = Path(row["folder"]).resolve()
    path = base.joinpath(*rel)
    try:
        real = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise PluginError("no such file", 404) from None
    if not real.is_relative_to(base) or not real.is_file():
        raise PluginError("no such file", 404)
    if len(rel) > 1 and not cover and not real.is_relative_to(base / rel[0]):
        raise PluginError("no such file", 404)
    if real.stat().st_size > FILE_MAX:
        raise PluginError("that file is bigger than a plugin file is served", 413)
    ctype = TYPES.get(real.suffix.lower(), "application/octet-stream")
    return real, ctype


# ---------------------------------------------------------------------------
# Per-agent settings (2026-11-13): what a plugin keeps for each agent, shown
# on the agent's profile (place `agent.profile`). Read aloud's voice is the
# first; nothing here knows it. Kept in Yours' reserved settings folder,
# `_settings/<id>/agents.json`, outside the plugin's own folder, so an update
# that replaces the folder never touches them, and in Yours' git history.
# ---------------------------------------------------------------------------

def agents_path(pid: str) -> Path:
    return yours_root() / SETTINGS_DIR / pid / AGENTS_FILE


def _read_agents(pid: str) -> dict:
    try:
        data = json.loads(agents_path(pid).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise PluginError(f"{pid}'s agent settings cannot be read ({exc})", 500) \
            from None
    agents = data.get("agents") if isinstance(data, dict) else None
    return agents if isinstance(agents, dict) else {}


def _in_force(pid: str) -> dict:
    _need_id(pid)
    rows = [r for r in _rows() if r["id"] == pid]
    if not rows:
        raise PluginError(f"no plugin {pid!r} in either layer", 404)
    row = next((r for r in rows if r["effective"]), None)
    if row is None:
        raise PluginError(f"{pid} is broken: {rows[0]['broken']}", 409)
    return row


def _filled(fields: list[dict], stored: dict) -> dict:
    """Every declared field at its stored value or its default, then any
    undeclared value stored."""
    out = {f["id"]: stored.get(f["id"], f.get("default")) for f in fields}
    out.update({k: v for k, v in stored.items() if k not in out})
    return out


def agent_values(pid: str, agent: str | None = None) -> dict:
    """``GET /v1/plugins/<id>/agents[/<agent>]``: what the plugin declares
    (``fields``) and its value for every agent, or for one."""
    row = _in_force(pid)
    fields = row.get("agent_settings") or []
    stored = _read_agents(pid)
    head = {"plugin": pid, "name": row.get("name"), "on": row.get("on"),
            "fields": fields}
    if agent is not None:
        mine = stored.get(agent) if isinstance(stored.get(agent), dict) else {}
        return {**head, "agent": agent, "values": _filled(fields, mine),
                "stored": mine}
    return {**head, "agents": {a: _filled(fields, v) for a, v in sorted(stored.items())
                               if isinstance(v, dict)}}


def set_agent_values(pid: str, agent: str, values, by: str | None = None) -> dict:
    """``PATCH /v1/plugins/<id>/agents/<agent>`` ``{"values": {key: value}}``:
    merged key by key; null removes a key (back to the field's default). A
    plugin that declares fields takes only those, and a field with choices
    only one of them; one that declares none takes any key (a slug) with
    text, a number or true/false. Nothing is written on a refusal."""
    if not isinstance(values, dict) or not values:
        raise PluginError("`values` is an object of the fields to change, "
                          "like {\"voice\": \"Kore\"}")
    with locked():
        row = _in_force(pid)
        fields = {f["id"]: f for f in row.get("agent_settings") or []}
        for key, value in values.items():
            if fields and key not in fields:
                raise PluginError(f"{pid} keeps no {key!r} for an agent; its fields "
                                  f"are {', '.join(fields)}")
            if not isinstance(key, str) or not ID.match(key) or len(key) > OPTION_ID_MAX:
                raise PluginError(f"{key!r} is not a field name (a slug)")
            if value is None:
                continue
            field = fields.get(key)
            if field and field.get("choices") is not None:
                ids = [c["id"] for c in field["choices"]]
                if value not in ids:
                    raise PluginError(f"{key} is one of {', '.join(ids)}; not {value!r}")
            elif isinstance(value, str):
                if len(value) > AGENT_TEXT_MAX:
                    raise PluginError(f"{key} is at most {AGENT_TEXT_MAX} characters")
            elif field or not isinstance(value, (bool, int)) or isinstance(value, float):
                raise PluginError(f"{key} is text" + ("" if field else
                                                      ", a whole number or true/false"))
        stored = _read_agents(pid)
        mine = dict(stored.get(agent) or {})
        for key, value in values.items():
            if value is None:
                mine.pop(key, None)
            else:
                mine[key] = value
        if len([k for k in mine]) > AGENT_SETTINGS_MAX:
            raise PluginError(f"an agent keeps at most {AGENT_SETTINGS_MAX} values "
                              f"for one plugin")
        if mine:
            stored[agent] = mine
        else:
            stored.pop(agent, None)
        target = agents_path(pid)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps({"version": 1, "agents": stored}, indent=2,
                                  ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, target)
    return agent_values(pid, agent)


def profile_rows(agent: str) -> list[dict]:
    """Every plugin in force that fills `agent.profile` or keeps settings for
    agents, with its values for this agent: the profile's plugin rows. A
    plugin that is off is listed with ``on: false``, for the page to leave
    out; nothing here draws anything."""
    out = []
    for row in _rows():
        if not row.get("effective") or row.get("broken"):
            continue
        fields = row.get("agent_settings") or []
        if "agent.profile" not in (row.get("places") or []) and not fields:
            continue
        try:
            stored = _read_agents(row["id"]).get(agent) or {}
        except PluginError:
            stored = {}
        out.append({"id": row["id"], "name": row.get("name"), "on": row.get("on"),
                    "places": row.get("places") or [], "fields": fields,
                    "values": _filled(fields, stored if isinstance(stored, dict) else {})})
    return out
