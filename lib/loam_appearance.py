"""Appearance: a theme, a mode and the page's size per device kind, and one saved place.

Decided 2026-11-11 (the owner, to a persona): Settings -> Appearance with a mode and a
zoom ("I really enjoy it at a 90% zoom"), kept on the host so that every
browser he opens agrees and a persona can change it on his ask (his 11-10
rule: every setting the page can change, a persona can too). The page half is
loam-ui's (`docs/appearance.md`); this is the host half it asked for.

The value is two device kinds, each a mode, a Sun transition (2026-11-16),
a size, a theme and the options of every theme that has any (2026-11-12)::

    {"phone":   {"mode": "system", "transition": "normal", "size": 100,
                 "theme": "latte",
                 "options": {"latte": {"agent-colours": "palette",
                                       "agent-shapes": "distinct"}}},
     "desktop": {"mode": "system", "transition": "normal", "size": 90,
                 "theme": "latte",
                 "options": {"latte": {...}}}}

``mode`` is one of the modes loam-ui declares (``light``, ``dark``, ``sun``
and ``system`` on 2026-11-13; see *Modes are loam-ui's* below); ``size`` an
integer percent from 75 to 150 in steps of 5; ``theme`` the id of an
installed theme; ``transition`` one of ``fast``, ``normal`` and ``long``.
A missing field reads as its default (``system``, ``normal``, 100,
``latte``). A patch names any of the kinds' fields, and ``location``, and
merges them in one write; anything else -- an unknown kind or field, an empty
patch, a value out of range, a boolean for a size, a theme that is not
installed -- is refused whole with a sentence and nothing is written.

**Sun mode and the saved place** (2026-11-13; the owner asked a persona that
morning for a sunrise/sunset mode; loam-ui ``29a1506``, its contract in
loam-ui's ``docs/appearance.md``, *Sun mode*). ``sun`` is light from 30
minutes before sunrise to 30 minutes after sunset at a saved place, which
the page computes; the host only keeps the mode and the place. The place is
one value beside the two kinds, **shared**, so the phone and the desktop
agree on where home is rather than each taking its own GPS reading::

    {"phone": {...}, "desktop": {"mode": "sun", ...},
     "location": {"latitude": 37.8, "longitude": -122.4,
                  "source": "geolocation", "timeZone": "America/Los_Angeles"}}

``latitude`` is -90 to 90 and ``longitude`` -180 to 180, both rounded here to
tenths of a degree the way the page rounds them (``Math.round(x * 10) / 10``,
half up); ``source`` is one of the sources loam-ui declares (``geolocation``
or ``timezone``); ``timeZone`` an IANA zone this machine knows. All four are
required and nothing else is taken; ``"location": null`` clears it. A reading
always carries ``location``, ``null`` when none is saved; with Sun chosen and
no place saved, the page uses its time-zone fallback without asking the
browser for one.

**The Sun transition** (2026-11-16; the owner asked a persona that evening
for a Sun-mode Transition setting, loam-ui's ``sun-shift.js``). Each kind
also keeps ``transition``, how long the page takes to shift between light
and dark at a Sun boundary: ``fast`` (3 s), ``normal`` (10 s, the default)
or ``long`` (20 s). The durations are the page's; the host keeps the id. It
sits beside ``mode`` in each kind, so a phone and a desktop may differ, and
a write of anything else is refused like any bad field. The three ids are
fixed here like the size's range, and the tests hold them to loam-ui's
handoff (the enum of ``appearance.<kind>.transition``); a stored id outside
them reads as ``normal``.

**Modes are loam-ui's** (2026-11-13). The modes a write may choose, and the
location sources, are read from loam-ui's machine-readable handoff
``docs/appearance-settings.registry.json`` (the enum of
``appearance.<kind>.mode`` and of ``appearance.location``'s ``source``), the
file loam-ui generates from its own source and drift-checks in its tests, in
the checkout the page is built from (``LOAM_UI_APPEARANCE_REGISTRY``
overrides it; ``none`` for no file), cached by mtime -- never a copy here,
the way 2026-11-12 taught for theme options. When the file cannot be read
the modes are the three the page has always had and a location is refused.
A stored mode is kept on read as long as it is shaped like an id, declared
or not, so a briefly unreadable handoff does not turn Sun into System on the
next write of anything else; only a write is held to the declared list.

**Theme options** (2026-11-12; the owner, to a persona: "Every theme should be able
to have its own different variants"). A theme's manifest may declare
options, each with its choices and a default (``loam_plugins.options_of``,
loam-ui's ``docs/appearance.md``); Latte's are whatever its ``PLUGIN.md``
declares (four on 2026-11-12), Plain has none. Each device kind keeps a choice per option per
theme, so switching to Plain and back keeps Latte's. A reading gives every
installed theme with options, each option its stored choice or, when there
is none or the theme no longer offers it, the manifest's default; a stored
entry for a theme or option no longer installed is kept in the file (it
comes back with the theme) but not shown. A patch names only the leaves it
changes, ``{"desktop": {"options": {"latte": {"agent-shapes": "circle"}}}}``,
and they merge leaf by leaf: never the whole map, never a whole theme's
options. An options map that is not an object, empty, or names a theme
without options, an option the theme does not have, or a choice it does not
offer, is refused like any other bad field.

**Themes are learned, not listed** (2026-11-12). The themes a write may
choose are the theme plugins of the plugin host's one list
(``lib/loam_plugins.py``, ``themes()``): loam-ui's shipped theme folders,
read the way loam-ui's ``src/themes.js`` reads them, and any theme plugin in
the Yours layer (``paths.plugins``), which wins over a shipped one of the
same id. They are read each time a theme is written or the list is asked
for, so a theme added to either layer is settable with no change here. A
theme is on where this file has chosen it: the plugin list derives a theme's
on/off from here, and has no switch of its own for one, so there is one
truth. When no folder can be read the list is the two the page always has,
``latte`` and ``plain``.

A stored theme is kept on read as long as it is shaped like an id, installed
or not: the page draws Latte for an id it does not have, and the choice comes
back when the theme does, instead of being lost to a folder that was briefly
unreadable. Only a write is held to the installed list.

**Where it lives.** ``state/broker/settings.json`` (``LOAM_HOST_SETTINGS``
overrides it, for the tests), beside the broker's other state
(``provider-usage.json``, ``codex-models.json``, the session records). It is
the broker's settings file, keyed by section, and ``appearance`` is its first
section: the next host-kept setting gets a key beside it rather than a second
file. Before this, every host setting lived on the thing it set (a thread's
record, a session's record, a schedule file); there was no place for one that
belongs to nobody but the owner, so this is that place.

Writes are atomic (a temp file and ``os.replace``) and serialised by a lock,
so two patches cannot interleave a read and a write; the broker is one
process, and nothing else writes this file.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
from pathlib import Path

import loam_broker as cfg

KINDS = ("phone", "desktop")
LOCATION = "location"
# The modes the page has always drawn, for when loam-ui's handoff cannot be
# read; the declared list is `modes()`.
BASE_MODES = ("light", "dark", "system")
REGISTRY = "docs/appearance-settings.registry.json"
LOCATION_FIELDS = ("latitude", "longitude", "source", "timeZone")
LAT_MAX, LON_MAX, ZONE_MAX = 90, 180, 100
# An IANA zone's shape; zoneinfo also opens `posix/...` and `right/...`
# copies, which a browser's Intl does not take.
ZONE = re.compile(r"^(?!posix/|right/)[A-Za-z][A-Za-z0-9_+-]*(?:/[A-Za-z0-9_+-]+)*$")
SIZE_MIN, SIZE_MAX, SIZE_STEP = 75, 150, 5
# The Sun transition's ids, the page's (app/public/sun-shift.js).
TRANSITIONS = ("fast", "normal", "long")
DEFAULT = {"mode": "system", "transition": "normal", "size": 100, "theme": "latte"}
# A theme id as loam-ui's loader accepts one (src/themes.js, `slug`).
THEME_ID = re.compile(r"^[a-z][a-z0-9-]*$")

_lock = threading.Lock()


class AppearanceError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def path() -> Path:
    return Path(os.environ.get("LOAM_HOST_SETTINGS")
                or cfg.STATE_DIR / "settings.json")


def _load_all() -> dict:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        # A file that cannot be read is not silently replaced by defaults on
        # the next write: that would throw the owner's choices away unseen.
        raise AppearanceError(f"the host settings file cannot be read ({exc})", 500) \
            from None
    return data if isinstance(data, dict) else {}


def themes() -> list[str]:
    """The installed theme ids, Latte first: the theme plugins in force in
    either layer, read from the plugin host's one list (lib/loam_plugins.py).
    Latte and Plain are always in it."""
    import loam_plugins  # noqa: PLC0415 -- it reads this module back for in-use themes
    return loam_plugins.themes()


def _theme_shaped(v) -> bool:
    return isinstance(v, str) and bool(THEME_ID.match(v))


_registry_cache: dict = {}


def registry_path() -> Path | None:
    """loam-ui's appearance handoff: ``LOAM_UI_APPEARANCE_REGISTRY`` (a path,
    or ``none``), or the loam-ui checkout's ``docs/appearance-settings.registry.json``."""
    env = os.environ.get("LOAM_UI_APPEARANCE_REGISTRY")
    if env is not None:
        return None if env in ("", "none") else Path(env)
    import loam_plugins  # noqa: PLC0415
    ui = loam_plugins.ui_checkout()
    return ui / REGISTRY if ui else None


def declared() -> dict:
    """What loam-ui's handoff declares for the fields this module cannot
    learn from a theme: ``{"modes": (...), "sources": (...) or None}``.
    ``sources`` is None when the handoff declares no location (or cannot be
    read), and the modes are then ``BASE_MODES`` if it declares none."""
    where = registry_path()
    fallback = {"modes": BASE_MODES, "sources": None}
    if where is None:
        return fallback
    try:
        stamp = where.stat().st_mtime_ns
    except OSError:
        return fallback
    hit = _registry_cache.get(str(where))
    if hit and hit[0] == stamp:
        return hit[1]
    out = dict(fallback)
    try:
        rows = {r.get("key"): r for r in
                json.loads(where.read_text(encoding="utf-8")).get("settings") or []
                if isinstance(r, dict)}
    except (OSError, ValueError, AttributeError):
        rows = {}
    modes = [tuple(m for m in (rows.get(f"appearance.{k}.mode") or {}).get("enum") or []
                   if _theme_shaped(m)) for k in KINDS]
    if modes[0] and all(m == modes[0] for m in modes):
        out["modes"] = modes[0]
    loc = rows.get(f"appearance.{LOCATION}") or {}
    src = (((loc.get("properties") or {}).get("source") or {}).get("enum")
           if isinstance(loc, dict) else None) or []
    src = tuple(x for x in src if _theme_shaped(x))
    if src:
        out["sources"] = src
    _registry_cache[str(where)] = (stamp, out)
    return out


def modes() -> tuple[str, ...]:
    """The modes a write may choose, as loam-ui declares them now."""
    return declared()["modes"]


def _valid_mode(v) -> bool:
    return isinstance(v, str) and v in modes()


def _round_tenth(x: float) -> float:
    """As the page rounds: ``Math.round(x * 10) / 10``, half up."""
    return math.floor(x * 10 + 0.5) / 10


def _degrees(v, limit: int) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v) and -limit <= v <= limit)


def _zone_ok(v) -> bool:
    if not isinstance(v, str) or len(v) > ZONE_MAX or not ZONE.match(v):
        return False
    try:
        import zoneinfo  # noqa: PLC0415
        zoneinfo.ZoneInfo(v)
    except (ValueError, KeyError, OSError, zoneinfo.ZoneInfoNotFoundError):
        return False
    return True


def _stored_location(raw) -> dict | None:
    """The saved place, shape-checked and nothing more (its source need not
    be one the handoff declares now); None for nothing or nonsense."""
    if not isinstance(raw, dict):
        return None
    lat, lon = raw.get("latitude"), raw.get("longitude")
    if not (_degrees(lat, LAT_MAX) and _degrees(lon, LON_MAX)
            and _theme_shaped(raw.get("source"))
            and isinstance(raw.get("timeZone"), str) and raw["timeZone"]
            and len(raw["timeZone"]) <= ZONE_MAX):
        return None
    return {"latitude": _round_tenth(lat), "longitude": _round_tenth(lon),
            "source": raw["source"], "timeZone": raw["timeZone"]}


def _check_location(value) -> dict | None:
    """A patch's location, rounded; None clears it."""
    if value is None:
        return None
    example = ('{"location": {"latitude": 37.8, "longitude": -122.4, '
               '"source": "timezone", "timeZone": "America/Los_Angeles"}}')
    sources = declared()["sources"]
    if not sources:
        raise AppearanceError("location: the page's appearance handoff declares "
                              "no saved place, so there is none to set")
    if not isinstance(value, dict):
        raise AppearanceError(f"location is a place or null: {example}, for one")
    extra = [k for k in value if k not in LOCATION_FIELDS]
    if extra:
        raise AppearanceError(f"location: {extra[0]!r} is not a location field; "
                              f"they are latitude, longitude, source and timeZone")
    missing = [k for k in LOCATION_FIELDS if k not in value]
    if missing:
        raise AppearanceError(f"location needs all of latitude, longitude, source "
                              f"and timeZone; it has no {', '.join(missing)}")
    if not _degrees(value["latitude"], LAT_MAX):
        raise AppearanceError(f"location latitude is a number from -90 to 90, "
                              f"not {value['latitude']!r}")
    if not _degrees(value["longitude"], LON_MAX):
        raise AppearanceError(f"location longitude is a number from -180 to 180, "
                              f"not {value['longitude']!r}")
    if value["source"] not in sources:
        raise AppearanceError(f"location source is {' or '.join(sources)}, not "
                              f"{value['source']!r}")
    if not _zone_ok(value["timeZone"]):
        raise AppearanceError(f"location timeZone is an IANA time zone, like "
                              f"America/Los_Angeles, not {value['timeZone']!r}")
    return _stored_location(value)


def _valid_size(v) -> bool:
    return (isinstance(v, int) and not isinstance(v, bool)
            and SIZE_MIN <= v <= SIZE_MAX and v % SIZE_STEP == 0)


def catalog() -> dict[str, dict[str, dict]]:
    """The installed themes' options, as validation wants them:
    ``{theme: {option: {"choices": [ids], "default": id}}}``."""
    import loam_plugins  # noqa: PLC0415 -- it reads this module back for in-use themes
    return {theme: {o["id"]: {"choices": [c["id"] for c in o["choices"]],
                              "default": o["default"]} for o in opts}
            for theme, opts in loam_plugins.theme_options().items()}


def _stored_options(row: dict) -> dict[str, dict[str, str]]:
    """A kind's stored choices, shape-checked and nothing more: every theme's,
    installed or not, so a theme that comes back finds its choices."""
    raw = row.get("options")
    out: dict[str, dict[str, str]] = {}
    if not isinstance(raw, dict):
        return out
    for theme, opts in raw.items():
        if _theme_shaped(theme) and isinstance(opts, dict):
            kept = {o: c for o, c in opts.items()
                    if _theme_shaped(o) and _theme_shaped(c)}
            if kept:
                out[theme] = kept
    return out


def _fill(stored) -> dict:
    """Mode, transition, size and theme of each kind, defaults where the file has nothing
    (or nonsense), and the kind's stored option choices as stored."""
    stored = stored if isinstance(stored, dict) else {}
    out = {}
    for kind in KINDS:
        row = stored.get(kind) if isinstance(stored.get(kind), dict) else {}
        out[kind] = {
            # Kept if id-shaped, declared or not: only a write is held to modes().
            "mode": row["mode"] if _theme_shaped(row.get("mode")) else DEFAULT["mode"],
            "transition": (row["transition"] if row.get("transition") in TRANSITIONS
                           else DEFAULT["transition"]),
            "size": row["size"] if _valid_size(row.get("size")) else DEFAULT["size"],
            "theme": (row["theme"] if _theme_shaped(row.get("theme"))
                      else DEFAULT["theme"]),
            "options": _stored_options(row),
        }
    out[LOCATION] = _stored_location(stored.get(LOCATION))
    return out


def _public(value: dict, cat: dict) -> dict:
    """The reading: each installed theme with options, each of its options
    the stored choice where the theme still offers it, else its default."""
    out = {}
    for kind in KINDS:
        row = dict(value[kind])
        stored = row.get("options") or {}
        row["options"] = {
            theme: {opt: (stored.get(theme, {}).get(opt)
                          if stored.get(theme, {}).get(opt) in spec["choices"]
                          else spec["default"])
                    for opt, spec in opts.items()}
            for theme, opts in cat.items()}
        out[kind] = row
    out[LOCATION] = value.get(LOCATION)
    return out


def read_base() -> dict:
    """Mode, transition, size and theme of each kind, without reading the theme catalog
    (the plugin list's in-use themes)."""
    value = _fill(_load_all().get("appearance"))
    return {kind: {k: v for k, v in value[kind].items() if k != "options"}
            for kind in KINDS}


def read() -> dict:
    return _public(_fill(_load_all().get("appearance")), catalog())


def validate(patch) -> dict:
    """The patch, checked whole; a sentence for the first thing wrong."""
    if not isinstance(patch, dict) or not patch:
        raise AppearanceError("name what to change: {\"desktop\": {\"size\": 90}}, "
                              "for one")
    clean: dict = {}
    installed = None
    cat = None
    for kind, fields in patch.items():
        if kind == LOCATION:
            clean[LOCATION] = _check_location(fields)
            continue
        if kind not in KINDS:
            raise AppearanceError(f"{kind!r} is not a device kind or the "
                                  f"location; they are phone, desktop and location")
        if not isinstance(fields, dict) or not fields:
            raise AppearanceError(f"{kind}: name a mode, a transition, a size, "
                                  f"a theme or a theme's options to change")
        for name, value in fields.items():
            if name == "mode":
                if not _valid_mode(value):
                    allowed = modes()
                    raise AppearanceError(
                        f"{kind} mode is {', '.join(allowed[:-1])} or "
                        f"{allowed[-1]}, not {value!r}")
            elif name == "transition":
                if not isinstance(value, str) or value not in TRANSITIONS:
                    raise AppearanceError(
                        f"{kind} transition is fast, normal or long, not {value!r}")
            elif name == "size":
                if not _valid_size(value):
                    raise AppearanceError(
                        f"{kind} size is a whole percent from {SIZE_MIN} to "
                        f"{SIZE_MAX} in steps of {SIZE_STEP}, not {value!r}")
            elif name == "theme":
                installed = installed if installed is not None else themes()
                if not isinstance(value, str) or value not in installed:
                    raise AppearanceError(
                        f"{kind} theme is an installed theme ("
                        f"{', '.join(installed)}), not {value!r}")
            elif name == "options":
                cat = cat if cat is not None else catalog()
                _check_options(kind, value, cat)
            else:
                raise AppearanceError(f"{kind}: {name!r} is not an appearance "
                                      f"field; they are mode, transition, size, "
                                      f"theme and options")
        clean[kind] = dict(fields)
    return clean


def _check_options(kind: str, value, cat: dict) -> None:
    names = list(cat)
    with_options = (
        "no installed theme has options" if not names else
        f"the only installed theme with options is {names[0]}" if len(names) == 1
        else f"the installed themes with options are {', '.join(names[:-1])} "
             f"and {names[-1]}")
    example = (f'{{"{kind}": {{"options": {{"latte": {{"agent-shapes": '
               f'"circle"}}}}}}}}')
    if not isinstance(value, dict) or not value:
        raise AppearanceError(f"{kind} options is a map of theme to option to "
                              f"choice, naming at least one: {example}, for one")
    for theme, opts in value.items():
        if theme not in cat:
            raise AppearanceError(f"{kind} options: {theme!r} is not an installed "
                                  f"theme with options ({with_options})")
        if not isinstance(opts, dict) or not opts:
            raise AppearanceError(f"{kind} options: {theme} is a map of option to "
                                  f"choice, naming at least one")
        for opt, choice in opts.items():
            if opt not in cat[theme]:
                raise AppearanceError(f"{kind} options: {theme} has no option "
                                      f"{opt!r} (it has {', '.join(cat[theme])})")
            choices = cat[theme][opt]["choices"]
            if not isinstance(choice, str) or choice not in choices:
                raise AppearanceError(f"{kind} options: {theme} {opt} is one of "
                                      f"{', '.join(choices)}, not {choice!r}")


def update(patch) -> dict:
    """Merge a patch into the stored value in one write; the whole value back."""
    clean = validate(patch)
    with _lock:
        data = _load_all()
        value = _fill(data.get("appearance"))
        for kind, fields in clean.items():
            if kind == LOCATION:
                value[LOCATION] = fields
                continue
            for name, v in fields.items():
                if name != "options":
                    value[kind][name] = v
                    continue
                # Leaf by leaf: a theme's other options, and other themes', stay.
                for theme, opts in v.items():
                    value[kind]["options"].setdefault(theme, {}).update(opts)
        data["appearance"] = value
        target = path()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, target)
    return _public(value, catalog())


def mutate(key: str, change) -> object:
    """Replace one top-level key of the host settings file with
    ``change(current)`` in one write, under the same lock as Appearance's
    (2026-11-13: the engineers' and the reviewer's settings,
    lib/loam_profile.py, live beside it). ``change`` may raise to write
    nothing; a value of None removes the key. Answers the new value."""
    with _lock:
        data = _load_all()
        value = change(data.get(key))
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
        target = path()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, target)
    return value
