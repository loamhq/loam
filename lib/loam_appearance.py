"""Store the owner's theme, mode, options and optional Sun location, plus
phone and desktop size and transition. Validate choices against the app and
installed plugins. Preserve stored theme IDs when a plugin is unavailable. Serialize
patches under the shared settings lock.
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
ACCOUNT = "account"
# The modes the page has always drawn, for when loam-ui's handoff cannot be
# read; the declared list is `modes()`.
BASE_MODES = ("light", "dark", "system")
REGISTRY = "docs/appearance-settings.registry.json"
LOCATION_FIELDS = ("latitude", "longitude", "source", "timeZone")
LAT_MAX, LON_MAX, ZONE_MAX = 90, 180, 100
# An IANA zone's shape; zoneinfo also opens `posix/...` and `right/...`
# copies, which a browser's Intl does not take.
ZONE = re.compile(r"^(?!posix/|right/)[A-Za-z][A-Za-z0-9_+-]*(?:/[A-Za-z0-9_+-]+)*$")
SIZE_MIN, SIZE_MAX, SIZE_STEP = 60, 200, 5
LEGACY_SIZE_MIN, LEGACY_SIZE_MAX = 75, 150
SIZE_BASE = 0.9
SIZE_VERSION = 2
SIZE_VERSION_KEY = "size_version"
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


def _valid_legacy_size(v) -> bool:
    return (isinstance(v, int) and not isinstance(v, bool)
            and LEGACY_SIZE_MIN <= v <= LEGACY_SIZE_MAX and v % SIZE_STEP == 0)


def migrate_legacy_size(old_size) -> int:
    """Convert a pre-relabel stored percentage (where 100% was zoom 1.0) onto
    the 0.9-base scale so non-default choices keep the same visual size, while
    100% stays 100% so default installs adopt the 10% smaller default.
    Matches ``migrateLegacySize()`` in ``app/public/scale.js``."""
    if not isinstance(old_size, int) or isinstance(old_size, bool):
        return DEFAULT["size"]
    if old_size == DEFAULT["size"]:
        return DEFAULT["size"]
    stepped = math.floor(old_size / SIZE_BASE / SIZE_STEP + 0.5) * SIZE_STEP
    return max(SIZE_MIN, min(SIZE_MAX, stepped))


def _needs_size_migration(stored) -> bool:
    if not isinstance(stored, dict):
        return False
    ver = stored.get(SIZE_VERSION_KEY)
    return not (isinstance(ver, int) and not isinstance(ver, bool) and ver >= SIZE_VERSION)


def _write_all(data: dict) -> None:
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)


def _migrate_unlocked(data: dict) -> tuple[dict | None, dict[str, tuple[int, int]]]:
    """Run the one-time legacy size migration on ``data['appearance']`` and
    persist ``size_version`` on disk when needed. Caller holds ``_lock``."""
    stored = data.get("appearance")
    if not _needs_size_migration(stored):
        return stored, {}
    migrated = dict(stored)
    changes: dict[str, tuple[int, int]] = {}
    for kind in KINDS:
        raw = migrated.get(kind)
        if isinstance(raw, dict) and "size" in raw and _valid_legacy_size(raw["size"]):
            old = raw["size"]
            new = migrate_legacy_size(old)
            if new != old:
                row = dict(raw)
                row["size"] = new
                migrated[kind] = row
                changes[kind] = (old, new)
    migrated[SIZE_VERSION_KEY] = SIZE_VERSION
    data["appearance"] = migrated
    _write_all(data)
    return migrated, changes


def migrate() -> dict[str, tuple[int, int]]:
    """Run the one-time legacy size migration on disk if needed.

    Returns ``{kind: (old, new)}`` for each device kind whose stored size
    changed; empty dict when already migrated, nothing stored, or sizes stayed
    at 100.
    """
    with _lock:
        data = _load_all()
        _, changes = _migrate_unlocked(data)
        return changes


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


def _merge_options(target: dict, patch: dict) -> None:
    for theme, options in patch.items():
        target.setdefault(theme, {}).update(options)


def _fill(stored) -> dict:
    """Read the account record, or derive it from legacy rows until the first write.

    Desktop wins over phone where both legacy rows name the same field or
    option. Retain the legacy rows on disk and only update the account copy.
    """
    stored = stored if isinstance(stored, dict) else {}
    migrate_sizes = _needs_size_migration(stored)
    account = {"mode": DEFAULT["mode"], "theme": DEFAULT["theme"], "options": {}}
    out = {}
    for kind in KINDS:
        row = stored.get(kind) if isinstance(stored.get(kind), dict) else {}
        raw_size = row.get("size")
        if migrate_sizes and _valid_legacy_size(raw_size):
            size = migrate_legacy_size(raw_size)
        elif _valid_size(raw_size):
            size = raw_size
        else:
            size = DEFAULT["size"]
        out[kind] = {
            **row,
            "transition": (row["transition"] if row.get("transition") in TRANSITIONS
                           else DEFAULT["transition"]),
            "size": size,
        }
    saved = stored.get(ACCOUNT)
    for row in [saved] if isinstance(saved, dict) else [out[kind] for kind in KINDS]:
        for name in ("mode", "theme"):
            if _theme_shaped(row.get(name)):
                account[name] = row[name]
        _merge_options(account["options"], _stored_options(row))
    out[ACCOUNT] = account
    out[LOCATION] = _stored_location(stored.get(LOCATION))
    ver = stored.get(SIZE_VERSION_KEY)
    out[SIZE_VERSION_KEY] = (ver if isinstance(ver, int) and not isinstance(ver, bool)
                             and ver >= SIZE_VERSION else SIZE_VERSION)
    return out


def _device_values(value: dict) -> dict:
    account = value[ACCOUNT]
    return {kind: {"mode": account["mode"], "theme": account["theme"],
                   "size": value[kind]["size"],
                   "transition": value[kind]["transition"]} for kind in KINDS}


def _public(value: dict, cat: dict) -> dict:
    """The reading: each installed theme with options, each of its options
    the stored choice where the theme still offers it, else its default."""
    stored = value[ACCOUNT]["options"]
    options = {
        theme: {opt: (stored.get(theme, {}).get(opt)
                      if stored.get(theme, {}).get(opt) in spec["choices"]
                      else spec["default"])
                for opt, spec in opts.items()}
        for theme, opts in cat.items()}
    out = _device_values(value)
    for row in out.values():
        row["options"] = options
    out[LOCATION] = value.get(LOCATION)
    return out


def read_base() -> dict:
    """Mode, transition, size and theme of each kind, without reading the theme catalog
    (the plugin list's in-use themes)."""
    return _device_values(_fill(_load_all().get("appearance")))


def read() -> dict:
    with _lock:
        data = _load_all()
        stored, _ = _migrate_unlocked(data)
        value = _fill(stored)
    return _public(value, catalog())


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
        if LOCATION in clean:
            value[LOCATION] = clean[LOCATION]
        # The existing device keys remain aliases for shared fields. If an
        # older client sends both rows with conflicting values, desktop wins.
        for kind in KINDS:
            for name, v in clean.get(kind, {}).items():
                if name == "options":
                    _merge_options(value[ACCOUNT]["options"], v)
                else:
                    value[ACCOUNT if name in ("mode", "theme") else kind][name] = v
        data["appearance"] = value
        _write_all(data)
    return _public(value, catalog())


def mutate(key: str, change) -> object:
    """Change one top-level settings key under the shared file lock. A callback exception
    leaves the file intact. Returning None removes the key. Return the new value.
    """
    with _lock:
        data = _load_all()
        value = change(data.get(key))
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
        _write_all(data)
    return value
