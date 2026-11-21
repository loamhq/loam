"""Resolve shared provider keys for plugins that declare the provider. A plugin's own
key takes precedence. Store credentials in restricted local state and return only
metadata to settings clients.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import loam_config as lc
import loam_hetzner
import loam_syncthing

# ---------------------------------------------------------------------------
# The providers Loam knows
# ---------------------------------------------------------------------------

# `shown`: drawn in API keys even before any plugin declares it, because a
# person setting up Loam reaches for these first. The rest appear once a
# plugin in either layer declares them or a key is saved.
PROVIDERS: dict[str, dict] = {
    "openai": {"name": "OpenAI", "shown": True, "check": "openai",
               "where": "https://platform.openai.com/api-keys",
               "fields": [{"name": "OPENAI_API_KEY", "label": "API key", "secret": True}]},
    "google": {"name": "Google Gemini", "shown": True, "check": "gemini",
               "where": "https://aistudio.google.com/apikey",
               "fields": [{"name": "GEMINI_API_KEY", "label": "API key", "secret": True}]},
    "hetzner": {"name": "Hetzner Cloud", "check": "hetzner",
                "where": "https://console.hetzner.cloud/",
                "fields": [{"name": "HCLOUD_TOKEN", "label": "API token", "secret": True}]},
    "cloudflare": {"name": "Cloudflare", "check": "cloudflare",
                   "where": "https://dash.cloudflare.com/profile/api-tokens",
                   "fields": [{"name": "CLOUDFLARE_API_KEY", "label": "API key or token",
                               "secret": True},
                              {"name": "CLOUDFLARE_EMAIL", "label": "Account email",
                               "secret": False, "optional": True}]},
    "modal": {"name": "Modal", "where": "https://modal.com/settings/tokens",
              "fields": [{"name": "MODAL_TOKEN_ID", "label": "Token ID", "secret": False},
                         {"name": "MODAL_TOKEN_SECRET", "label": "Token secret",
                          "secret": True}]},
    "syncthing": {"name": "Syncthing", "check": "syncthing",
                  "fields": [{"name": loam_syncthing.KEY, "label": "API key", "secret": True},
                             {"name": loam_syncthing.GUI, "label": "Local GUI URL",
                              "secret": False, "optional": True}]},
}

PROVIDER_ID = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
FIELD_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
KEYS_MAX = 8                 # providers one plugin may declare
WHY_MAX, LABEL_MAX = 300, 60
VALUE_MAX = 4096
PROJECT_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,47}")
HETZNER_PLUGIN = "hetzner-cloud"


class KeyError_(Exception):
    """A refusal, said in a sentence for the person or the agent."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# The manifest field: `keys:`
# ---------------------------------------------------------------------------

def keys_of(value, warnings: list[str], plugin_id: str | None = None) -> list[dict]:
    """A manifest's ``keys:`` into ``[{provider, name, why, optional,
    fields}]``. A provider Loam knows takes its fields from Loam; another
    must say its own ``name`` and ``fields``. Raises ValueError for anything
    that makes the manifest wrong."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("`keys` is a list of providers, like "
                         "`keys: [{provider: openai, why: makes the images}]`")
    if len(value) > KEYS_MAX:
        raise ValueError(f"`keys` names at most {KEYS_MAX} providers")
    out, seen = [], set()
    for i, raw in enumerate(value, 1):
        if isinstance(raw, str):
            raw = {"provider": raw}
        if not isinstance(raw, dict):
            raise ValueError(f"keys: entry {i} is a mapping with `provider` and `why`")
        pid = raw.get("provider")
        if not isinstance(pid, str) or not PROVIDER_ID.match(pid):
            raise ValueError(f"keys: entry {i}'s `provider` is a short id in lower "
                             f"case, like openai or google")
        if pid in seen:
            raise ValueError(f"keys: {pid} is named twice")
        seen.add(pid)
        if pid == loam_syncthing.PROVIDER and plugin_id != pid:
            raise ValueError("keys: the Syncthing GUI key is reserved for the Syncthing plugin")
        source = raw.get("source")
        if "source" in raw and (pid != loam_syncthing.PROVIDER or source != loam_syncthing.SOURCE):
            raise ValueError("keys: source must name the supported Syncthing GUI settings and gui/apikey")
        why = raw.get("why")
        if why is None:
            warnings.append(f"keys: {pid} says no `why`; the person is shown none")
            why = ""
        if not isinstance(why, str) or len(why) > WHY_MAX:
            raise ValueError(f"keys: {pid}'s `why` is a sentence of at most {WHY_MAX} "
                             f"characters")
        optional = raw.get("optional", False)
        if not isinstance(optional, bool):
            raise ValueError(f"keys: {pid}'s `optional` is true or false")
        known = PROVIDERS.get(pid)
        if known is not None:
            if "fields" in raw or "name" in raw:
                warnings.append(f"keys: {pid} is a provider Loam knows; its own name "
                                f"and fields are used, not the manifest's")
            fields = [dict(f) for f in known["fields"]]
            name = known["name"]
        else:
            name = raw.get("name")
            if not isinstance(name, str) or not name.strip() or len(name) > LABEL_MAX:
                raise ValueError(f"keys: {pid} is not a provider Loam knows, so it "
                                 f"needs a `name` (at most {LABEL_MAX} characters) "
                                 f"and its `fields`")
            name = name.strip()
            fields = _fields_of(pid, raw.get("fields"))
        out.append({"provider": pid, "name": name, "why": why.strip(),
                    "optional": optional, "fields": fields,
                    **({"source": dict(source)} if source else {})})
    return out


def _fields_of(pid: str, value) -> list[dict]:
    if not isinstance(value, list) or not 1 <= len(value) <= 4:
        raise ValueError(f"keys: {pid}'s `fields` is a list of 1 to 4 names, like "
                         f"`fields: [{{name: {pid.upper().replace('-', '_')}_API_KEY, "
                         f"label: API key}}]`")
    out = []
    for f in value:
        if isinstance(f, str):
            f = {"name": f}
        if not isinstance(f, dict) or not isinstance(f.get("name"), str) \
                or not FIELD_NAME.match(f["name"]):
            raise ValueError(f"keys: {pid}'s fields are names in capitals, like "
                             f"REPLICATE_API_TOKEN")
        label = f.get("label") or "API key"
        if not isinstance(label, str) or len(label) > LABEL_MAX:
            raise ValueError(f"keys: {pid}'s {f['name']} label is at most {LABEL_MAX} "
                             f"characters")
        secret = f.get("secret", True)
        optional = f.get("optional", False)
        if not isinstance(secret, bool) or not isinstance(optional, bool):
            raise ValueError(f"keys: {pid}'s {f['name']}: `secret` and `optional` "
                             f"are true or false")
        out.append({"name": f["name"], "label": label, "secret": secret,
                    "optional": optional})
    names = [f["name"] for f in out]
    if len(set(names)) != len(names):
        raise ValueError(f"keys: {pid} names a field twice")
    return out


def declaration_of(keys: list[dict]) -> list[dict]:
    """What the install question shows: provider, its name, why, optional."""
    return [{"provider": k["provider"], "name": k["name"], "why": k["why"],
             **({"optional": True} if k["optional"] else {}),
             **({"source": k["source"]} if k.get("source") else {})} for k in keys]


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def _state() -> Path:
    import loam_plugin_servers  # noqa: PLC0415
    return loam_plugin_servers.state_dir()


def keys_dir() -> Path:
    return _state() / "keys"


def _private(pid: str) -> Path:
    return _state() / "private" / pid


def _read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        k, sep, v = line.partition("=")
        if sep and k.strip() and not k.strip().startswith("#"):
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _write_env(path: Path, values: dict[str, str], keep_other: bool = True,
               drop: set[str] | None = None) -> None:
    """Rewrite ``path`` with ``values`` set and ``drop`` removed, keeping
    every other line as it was (comments included)."""
    lines: list[str] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            k = line.partition("=")[0].strip()
            if k in values or (drop and k in drop) or not keep_other:
                continue
            lines.append(line)
    except OSError:
        pass
    lines += [f"{k}={v}" for k, v in values.items()]
    if not any(l.strip() and not l.strip().startswith("#") for l in lines):
        try:
            path.unlink()
        except OSError:
            pass
        return
    _write_private(path, "".join(f"{l}\n" for l in lines))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _checks_path() -> Path:
    return keys_dir() / "checks.json"


def _checks() -> dict:
    try:
        data = json.loads(_checks_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _record(slot: str, rec: dict | None) -> None:
    data = _checks()
    if rec is None:
        if data.pop(slot, None) is None:
            return
    else:
        data[slot] = rec
    _write_private(_checks_path(), json.dumps(data, indent=2, sort_keys=True) + "\n")


def _secrets_env() -> Path:
    return Path(os.environ.get("LOAM_SECRETS_FILE") or lc.SECRETS_ENV)


# ---------------------------------------------------------------------------
# Who declares what
# ---------------------------------------------------------------------------

def _rows() -> list[dict]:
    import loam_plugins  # noqa: PLC0415
    return [r for r in loam_plugins._rows() if r.get("effective") and not r.get("broken")]


def declared_by(rows: list[dict] | None = None) -> dict[str, list[dict]]:
    """``{provider: [{id, name, on, optional, why, fields}]}`` for every
    plugin in force (on or off) that declares it."""
    out: dict[str, list[dict]] = {}
    for row in _rows() if rows is None else rows:
        for k in row.get("keys") or []:
            out.setdefault(k["provider"], []).append(
                {"id": row["id"], "name": row.get("name") or row["id"],
                 "on": bool(row.get("on")), "optional": k["optional"],
                 "why": k["why"], "fields": k["fields"], "provider_name": k["name"]})
    return out


def provider(pid: str, rows: list[dict] | None = None) -> dict:
    """A provider's definition: Loam's own, or the first declaring plugin's."""
    if pid in PROVIDERS:
        p = PROVIDERS[pid]
        return {"id": pid, "name": p["name"], "fields": p["fields"],
                "check": p.get("check"), "where": p.get("where"), "shown": bool(p.get("shown"))}
    users = declared_by(rows).get(pid) or []
    if not users:
        raise KeyError_(f"no provider {pid!r}: Loam knows "
                        f"{', '.join(sorted(PROVIDERS))}, and no plugin here declares "
                        f"another", 404)
    return {"id": pid, "name": users[0]["provider_name"], "fields": users[0]["fields"],
            "check": None, "where": None, "shown": False}


def plugin_keys(plugin_id: str, rows: list[dict] | None = None) -> list[dict]:
    """The providers ``plugin_id`` declared (its manifest's ``keys``)."""
    for row in _rows() if rows is None else rows:
        if row["id"] == plugin_id:
            return row.get("keys") or []
    raise KeyError_(f"no plugin {plugin_id!r} on this box", 404)


def _required(fields: list[dict]) -> list[str]:
    return [f["name"] for f in fields if not f.get("optional")]


def _complete(values: dict[str, str], fields: list[dict]) -> bool:
    return all(values.get(n) for n in _required(fields))


def _pick(values: dict[str, str], fields: list[dict]) -> dict[str, str]:
    return {f["name"]: values[f["name"]] for f in fields if values.get(f["name"])}


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def shared_values(pid: str, fields: list[dict]) -> dict[str, str]:
    return _pick(_read_env(keys_dir() / f"{pid}.env"), fields)


def own_values(plugin_id: str, fields: list[dict]) -> dict[str, str]:
    return _pick(_read_env(_private(plugin_id) / "secrets.env"), fields)


def box_values(fields: list[dict]) -> dict[str, str]:
    return _pick(_read_env(_secrets_env()), fields)


def shared_source(pid: str, fields: list[dict]) -> str | None:
    """``saved`` (API keys), ``box`` (the box's own secrets file), or None."""
    if _complete(shared_values(pid, fields), fields):
        return "saved"
    if _complete(box_values(fields), fields):
        return "box"
    return None


def _shared_only(plugin_id: str, provider_id: str) -> bool:
    return plugin_id == HETZNER_PLUGIN and provider_id == "hetzner"


def resolve(plugin_id: str, pid: str, fields: list[dict]) -> tuple[dict[str, str], str | None]:
    """The values ``plugin_id`` gets for provider ``pid``, and where they come
    from: ``own``, ``saved`` (shared), ``box``, or nothing."""
    own = {} if _shared_only(plugin_id, pid) else own_values(plugin_id, fields)
    if _complete(own, fields):
        return own, "own"
    shared = shared_values(pid, fields)
    if _complete(shared, fields):
        return shared, "saved"
    if _shared_only(plugin_id, pid):
        return {}, None
    box = box_values(fields)
    if _complete(box, fields):
        return box, "box"
    return {}, None


def secret_for(plugin_id: str, name: str, rows: list[dict] | None = None) -> str | None:
    """One field's value for a plugin, or None when it declared no provider
    with that field. Used by loam_contributions.secret_for."""
    try:
        keys = plugin_keys(plugin_id, rows)
    except KeyError_:
        return None
    for k in keys:
        if any(f["name"] == name for f in k["fields"]):
            values, _ = resolve(plugin_id, k["provider"], k["fields"])
            return values.get(name)
    return None


def declares_field(plugin_id: str, name: str, rows: list[dict] | None = None) -> bool:
    try:
        return any(f["name"] == name for k in plugin_keys(plugin_id, rows)
                   for f in k["fields"])
    except KeyError_:
        return False


# ---------------------------------------------------------------------------
# What a plugin's process reads: <private>/keys.env
# ---------------------------------------------------------------------------

def keys_file(plugin_id: str) -> Path:
    return _private(plugin_id) / "keys.env"


@contextmanager
def _shared_write(pid: str):
    """Serialize the daemon import with manual changes to the same key."""
    if pid != loam_syncthing.PROVIDER:
        yield
        return
    path = keys_dir()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (path / ".syncthing.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _refresh_syncthing(rows: list[dict]) -> None:
    pid = loam_syncthing.PROVIDER
    row = next((row for row in rows if row["id"] == pid and row.get("on")), None)
    if row is None:
        return
    keys = declaration_of(row.get("keys") or [])
    confirmed = ((row.get("installed") or {}).get("confirmed") or {}).get("touches", {}).get("keys", [])
    source = next((key["source"] for key in keys if key.get("source") and key in confirmed), None)
    if not source:
        return
    fields = PROVIDERS[pid]["fields"]
    with _shared_write(pid):
        values, used = resolve(pid, pid, fields)
        if values and not loam_syncthing.probe(values)[2]:
            return
        try:
            imported = _clean(fields, {"values": loam_syncthing.read_source(source)})
        except (KeyError_, loam_syncthing.SourceError) as exc:
            slot = f"{pid}:refused"
            why = str(exc)
            if (_checks().get(slot) or {}).get("why") != why:
                _record(slot, {"at": _now(), "ok": False, "why": why})
            return
        if imported == values:
            return
        path = _private(pid) / "secrets.env" if used == "own" else keys_dir() / f"{pid}.env"
        _write_env(path, imported, keep_other=used == "own")
        slot = f"{pid}@{pid}" if used == "own" else pid
        _record(slot, {"at": _now(), "ok": True, "why": "Read from Syncthing’s settings on this server."})
        _record(f"{pid}:refused", None)


def sync(rows: list[dict] | None = None, *, refresh_sources: bool = False) -> list[str]:
    """Write each declaring plugin's ``keys.env`` from what is in force now,
    and remove the file of a plugin that declares none. Answers the ids whose
    file changed. Cheap, idempotent: run after any change and on reconcile."""
    rows = _rows() if rows is None else rows
    if refresh_sources:
        _refresh_syncthing(rows)
    changed = []
    seen = set()
    for row in rows:
        seen.add(row["id"])
        path = keys_file(row["id"])
        keys = row.get("keys") or []
        if not keys:
            if path.exists():
                path.unlink()
                changed.append(row["id"])
            continue
        lines = [f"# Written by Loam for {row['id']}: the keys it declared (keys: in "
                 f"its PLUGIN.md), resolved. Rewritten on every change; do not edit."]
        for k in keys:
            values, source = resolve(row["id"], k["provider"], k["fields"])
            lines.append(f"# {k['provider']}: {source or 'no key'}")
            lines += [f"{n}={v}" for n, v in values.items()]
        text = "".join(f"{l}\n" for l in lines)
        try:
            if path.read_text(encoding="utf-8") == text:
                continue
        except OSError:
            pass
        _write_private(path, text)
        changed.append(row["id"])
    # A plugin removed from both layers leaves no keys behind it.
    private = _state() / "private"
    try:
        for folder in private.iterdir():
            if folder.name not in seen and (folder / "keys.env").exists():
                (folder / "keys.env").unlink()
                changed.append(folder.name)
    except OSError:
        pass
    return changed


def adopt(rows: list[dict] | None = None) -> list[str]:
    """Plugins that kept a key of their own before shared keys: when a
    provider has no shared key yet, the first such key becomes the shared
    one and leaves the plugin; a plugin's own key equal to the shared one is
    dropped as a duplicate. A different one stays, as that plugin's own.
    Answers what it did, in sentences for the log. Idempotent."""
    rows = _rows() if rows is None else rows
    did = []
    checks = _checks()
    for pid, users in sorted(declared_by(rows).items()):
        fields = users[0]["fields"] if pid not in PROVIDERS else PROVIDERS[pid]["fields"]
        for u in users:
            if _shared_only(u["id"], pid):
                continue
            own = own_values(u["id"], fields)
            # A key given through "use a different key" is a choice, never
            # adopted: only a key from before shared keys has no record.
            if not _complete(own, fields) or f"{pid}@{u['id']}" in checks:
                continue
            shared = shared_values(pid, fields)
            if not _complete(shared, fields):
                _write_env(keys_dir() / f"{pid}.env", own)
                _write_env(_private(u["id"]) / "secrets.env", {}, drop=set(own))
                did.append(f"{u['id']}'s own {pid} key became the shared one")
            elif shared == own:
                _write_env(_private(u["id"]) / "secrets.env", {}, drop=set(own))
                did.append(f"{u['id']}'s own {pid} key was the shared one; dropped")
    return did


# ---------------------------------------------------------------------------
# What API keys shows
# ---------------------------------------------------------------------------

def _shown_values(fields: list[dict], values: dict[str, str]) -> dict[str, str]:
    """A field that is not secret (an account email, a token's id) may be
    shown; a secret one is shown as its last four characters at most."""
    out = {}
    for f in fields:
        v = values.get(f["name"])
        if not v:
            continue
        out[f["name"]] = v if not f.get("secret", True) else (
            f"…{v[-4:]}" if len(v) >= 16 else "set")
    return out


def listing(*, include: str | None = None) -> dict:
    """``GET /v1/keys``: every provider worth drawing, its key's state, and
    the plugins that use it. Never a whole secret value."""
    rows = _rows()
    adopt(rows)
    users = declared_by(rows)
    checks = _checks()
    ids = [p for p, d in PROVIDERS.items() if d.get("shown")]
    saved_providers = {f.stem for f in keys_dir().glob("*.env")}
    if include:
        saved_providers.add(include)
    hetzner_projects = _hetzner_projects()
    if hetzner_projects:
        saved_providers.add("hetzner")
    ids += sorted(p for p in set(users) | saved_providers
                  if p not in ids and (p in PROVIDERS or p in users))
    out = []
    for pid in ids:
        prov = provider(pid, rows)
        fields = prov["fields"]
        source = shared_source(pid, fields)
        values = (shared_values(pid, fields) if source == "saved"
                  else box_values(fields) if source == "box" else {})
        plugins = []
        for u in users.get(pid, []):
            _, used = resolve(u["id"], pid, fields)
            plugins.append({"id": u["id"], "name": u["name"], "on": u["on"],
                            "why": u["why"], "optional": u["optional"],
                            "uses": "own" if used == "own" else "shared",
                            "has_key": used is not None,
                            "checked": checks.get(f"{pid}@{u['id']}"),
                            **({"shared_only": True} if _shared_only(u["id"], pid) else {})})
        out.append({"id": pid, "name": prov["name"], "where": prov["where"],
                    "checks": bool(prov["check"]),
                    "fields": [{k: f[k] for k in ("name", "label", "secret")}
                               | ({"optional": True} if f.get("optional") else {})
                               for f in fields],
                    "set": source is not None, "source": source,
                    "shown": _shown_values(fields, values) if pid != "hetzner" else {},
                    "checked": checks.get(pid) if source else None,
                    "refused": checks.get(f"{pid}:refused"),
                    "plugins": plugins,
                    **({"projects": hetzner_projects} if pid == "hetzner" else {})})
    return {"providers": out}


def one(pid: str) -> dict:
    for p in listing(include=pid)["providers"]:
        if p["id"] == pid:
            return p
    provider(pid)          # raises the 404 sentence
    raise KeyError_(f"no provider {pid!r}", 404)


# ---------------------------------------------------------------------------
# Changes
# ---------------------------------------------------------------------------

def _clean(fields: list[dict], body) -> dict[str, str]:
    """A pasted key, as ``{"value": …}`` for a one-field provider or
    ``{"values": {NAME: …}}``, into the field values. Raises with a sentence."""
    if not isinstance(body, dict):
        raise KeyError_("send the key as {\"value\": \"…\"}")
    raw = body.get("values")
    if raw is None and "value" in body:
        secret_fields = [f for f in fields if f.get("secret", True)]
        if len(fields) == 1 or len(secret_fields) == 1 and not _required(
                [f for f in fields if f is not secret_fields[0]]):
            raw = {(fields[0] if len(fields) == 1 else secret_fields[0])["name"]: body["value"]}
    if not isinstance(raw, dict):
        names = ", ".join(f["name"] for f in fields)
        raise KeyError_(f"this provider takes {names}: send {{\"values\": {{…}}}}")
    names = {f["name"]: f for f in fields}
    out = {}
    for n, v in raw.items():
        if n not in names:
            raise KeyError_(f"{n} is not one of this provider's fields "
                            f"({', '.join(names)})")
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        if not isinstance(v, str):
            raise KeyError_(f"{names[n]['label']} is text")
        v = v.strip()
        if len(v) > VALUE_MAX or any(ord(c) < 32 or c in "\"'" for c in v):
            raise KeyError_(f"that does not look like a {names[n]['label']}: it is too "
                            f"long or has a character no key has")
        out[n] = v
    if not out:
        raise KeyError_("paste the key itself")
    return out


def _merge_check(pid: str, prov: dict, values: dict[str, str],
                 before: dict[str, str]) -> tuple[dict[str, str], bool, str]:
    """Fill a partial paste from what was there (an email kept while a key is
    replaced), then check it."""
    merged = {**before, **values}
    missing = [f["label"] for f in prov["fields"] if not f.get("optional")
               and not merged.get(f["name"])]
    if missing:
        raise KeyError_(f"{prov['name']} needs its {' and '.join(missing)} too")
    ok, why = check_values(prov, merged)
    return merged, ok, why


def check_values(prov: dict, values: dict[str, str]) -> tuple[bool, str]:
    name = prov.get("check")
    if name == loam_syncthing.PROVIDER:
        ok, why, _ = loam_syncthing.probe(values)
        return ok, why
    if not name:
        return True, f"Saved. Loam has no check for a {prov['name']} key, so it was not tried."
    return check(name, values if len(prov["fields"]) > 1 else
                 values[prov["fields"][0]["name"]])


def set_shared(pid: str, body, by: str | None = None) -> dict:
    """Check a pasted key and, only if it is good, keep it as the shared key
    of ``pid``. A refusal keeps nothing and raises with the check's sentence."""
    prov = provider(pid)
    values = _clean(prov["fields"], body)
    with _shared_write(pid):
        merged, ok, why = _merge_check(pid, prov, values, shared_values(pid, prov["fields"]))
        if not ok:
            _record(f"{pid}:refused", {"at": _now(), "ok": False, "why": why, "by": by})
            raise KeyError_(why, 422)
        _write_env(keys_dir() / f"{pid}.env", merged, keep_other=False)
        _record(pid, {"at": _now(), "ok": True, "why": why, "by": by})
        _record(f"{pid}:refused", None)
    sync()
    return one(pid)


def forget_shared(pid: str, by: str | None = None) -> dict:
    prov = provider(pid)
    path = keys_dir() / f"{pid}.env"
    with _shared_write(pid):
        if not path.exists():
            raise KeyError_(f"no {prov['name']} key is saved in API keys, in Settings › Connections" + (
                "; the one in use is in this box's own secrets file, which Loam does "
                "not change" if shared_source(pid, prov["fields"]) == "box" else ""), 404)
        path.unlink()
        _record(pid, None)
    sync()
    return one(pid)


def recheck(pid: str, by: str | None = None) -> dict:
    prov = provider(pid)
    if pid == loam_syncthing.PROVIDER:
        sync(refresh_sources=True)
    source = shared_source(pid, prov["fields"])
    if source is None:
        raise KeyError_(f"no {prov['name']} key is set", 404)
    values = shared_values(pid, prov["fields"]) if source == "saved" else box_values(prov["fields"])
    ok, why = check_values(prov, values)
    _record(pid, {"at": _now(), "ok": ok, "why": why, "by": by, "source": source})
    return one(pid)


def _user(pid: str, plugin_id: str) -> dict:
    prov = provider(pid)
    if not any(k["provider"] == pid for k in plugin_keys(plugin_id)):
        raise KeyError_(f"{plugin_id} does not declare a {prov['name']} key, so it "
                        f"is never given one", 409)
    if _shared_only(plugin_id, pid):
        raise KeyError_("Hetzner Cloud uses the shared token for personal. "
                        "Add a project token in API keys to connect another project.", 409)
    return prov


def set_own(pid: str, plugin_id: str, body, by: str | None = None) -> dict:
    """"Use a different key": ``plugin_id``'s own key for ``pid``, checked
    like a shared one, kept in its private ``secrets.env``."""
    prov = _user(pid, plugin_id)
    values = _clean(prov["fields"], body)
    with _shared_write(pid):
        merged, ok, why = _merge_check(pid, prov, values, own_values(plugin_id, prov["fields"]))
        if not ok:
            _record(f"{pid}@{plugin_id}:refused", {"at": _now(), "ok": False, "why": why})
            raise KeyError_(why, 422)
        _write_env(_private(plugin_id) / "secrets.env", merged)
        _record(f"{pid}@{plugin_id}", {"at": _now(), "ok": True, "why": why, "by": by})
    sync()
    return one(pid)


def use_shared(pid: str, plugin_id: str, by: str | None = None) -> dict:
    """Back to the shared key: forget ``plugin_id``'s own key for ``pid``."""
    prov = _user(pid, plugin_id)
    names = {f["name"] for f in prov["fields"]}
    with _shared_write(pid):
        _write_env(_private(plugin_id) / "secrets.env", {}, drop=names)
        _record(f"{pid}@{plugin_id}", None)
    sync()
    return one(pid)


# ---- Named Hetzner project tokens ------------------------------------------

def hetzner_project_id(project: str) -> str:
    name = project.strip().lower()
    if not PROJECT_ID.fullmatch(name):
        raise KeyError_("Use a project name of 1 to 48 letters, digits or dashes, "
                        "starting with a letter or digit.")
    return name


def _project_dir() -> Path:
    return keys_dir() / "hetzner-projects"


def _project_record(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _hetzner_projects() -> list[dict]:
    projects = []
    for path in sorted(_project_dir().glob("*.json")):
        if PROJECT_ID.fullmatch(path.stem) and path.stem != "personal":
            record = _project_record(path)
            if record.get("token"):
                projects.append({"id": path.stem, "checked": record.get("checked")})
    return projects


def hetzner_project_token(project: str) -> str | None:
    name = hetzner_project_id(project)
    if name == "personal":
        fields = PROVIDERS["hetzner"]["fields"]
        return shared_values("hetzner", fields).get("HCLOUD_TOKEN")
    return _project_record(_project_dir() / f"{name}.json").get("token")


def _named_project_path(project: str) -> Path:
    name = hetzner_project_id(project)
    if name == "personal":
        raise KeyError_("Use the shared Hetzner Cloud key for personal.")
    return _project_dir() / f"{name}.json"


def set_hetzner_project(project: str, body, by: str | None = None) -> dict:
    path = _named_project_path(project)
    fields = PROVIDERS["hetzner"]["fields"]
    token = _clean(fields, {"value": body.get("value")})["HCLOUD_TOKEN"]
    ok, why = hetzner(token)
    if not ok:
        raise KeyError_(why, 422)
    checked = {"at": _now(), "ok": True, "why": why, "by": by}
    _write_private(path, json.dumps({"token": token, "checked": checked}) + "\n")
    return one("hetzner")


def forget_hetzner_project(project: str) -> dict:
    try:
        _named_project_path(project).unlink()
    except FileNotFoundError:
        raise KeyError_("There is no saved key for that Hetzner project.", 404) from None
    return one("hetzner")


# ---------------------------------------------------------------------------
# Checks: the cheapest call that proves a key, costing nothing
# ---------------------------------------------------------------------------

def _base(env: str, default: str) -> str:
    return (os.environ.get(env) or default).rstrip("/")


def _gemini_base() -> str:
    # The same knob the app reads (GEMINI_API_URL), so a test points both at
    # one fake.
    return (os.environ.get("LOAM_GEMINI_URL") or os.environ.get("GEMINI_API_URL")
            or "https://generativelanguage.googleapis.com").rstrip("/")


def _get(url: str, headers: dict, who: str) -> tuple[int | None, dict, str]:
    """(status, json body, network failure sentence)."""
    req = urllib.request.Request(url, headers={**headers, "User-Agent": "Loam"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            try:
                return r.status, json.loads(r.read() or b"{}"), ""
            except ValueError:
                return r.status, {}, ""
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read() or b"{}")
        except ValueError:
            body = {}
        return exc.code, body if isinstance(body, dict) else {}, ""
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return None, {}, f"Could not reach {who} to check the key ({reason}); try again."


def gemini(key: str) -> tuple[bool, str]:
    """Lists the models the key may use."""
    code, body, down = _get(f"{_gemini_base()}/v1beta/models?pageSize=1",
                            {"x-goog-api-key": key}, "Google")
    if code is None:
        return False, down
    if code < 300:
        return True, "Google accepted the key."
    err = body.get("error") or {}
    reasons = {d.get("reason") for d in err.get("details") or [] if isinstance(d, dict)}
    if code == 429:
        return True, ("Google accepted the key, and says its quota is used up "
                      "for now; it will work again when the quota resets.")
    if "API_KEY_INVALID" in reasons or code == 400:
        return False, "Google says this is not a valid API key. Check that it was pasted whole."
    if code == 403:
        said = (err.get("message") or "").split("\n")[0][:200]
        return False, ("Google refused this key" + (f": {said}" if said else "")
                       + ". It may need the Generative Language API turned on.")
    return False, f"Google answered {code} when the key was checked; try again."


def openai(key: str) -> tuple[bool, str]:
    """Lists the models: free, and refused for a bad key."""
    code, body, down = _get(f"{_base('LOAM_OPENAI_URL', 'https://api.openai.com/v1')}/models",
                            {"Authorization": f"Bearer {key}"}, "OpenAI")
    if code is None:
        return False, down
    if code < 300:
        return True, "OpenAI accepted the key."
    if code == 401:
        return False, "OpenAI says this is not a valid API key. Check that it was pasted whole."
    if code == 429:
        return True, "OpenAI accepted the key, and says it is over its rate limit for now."
    if code == 403:
        said = ((body.get("error") or {}).get("message") or "")[:200]
        return False, "OpenAI refused this key" + (f": {said}" if said else ".")
    return False, f"OpenAI answered {code} when the key was checked; try again."


def hetzner(key: str) -> tuple[bool, str]:
    """Reads one location: any project token may, read-only or not."""
    result = loam_hetzner.request(key, "GET", "/locations?per_page=1",
                                 base=_base("LOAM_HETZNER_URL", loam_hetzner.API))
    code = result.get("status")
    if code is None:
        return False, "Could not reach Hetzner Cloud to check the key. Try again."
    if result["ok"]:
        return True, "Hetzner accepted the token."
    if code in (401, 403):
        return False, "Hetzner says this is not a valid API token for a project."
    return False, f"Hetzner answered {code} when the token was checked; try again."


def cloudflare(values) -> tuple[bool, str]:
    """A global key with its account's email, or an API token alone."""
    if isinstance(values, str):
        values = {"CLOUDFLARE_API_KEY": values}
    key, email = values.get("CLOUDFLARE_API_KEY", ""), values.get("CLOUDFLARE_EMAIL")
    base = _base("LOAM_CLOUDFLARE_URL", "https://api.cloudflare.com/client/v4")
    if email:
        code, body, down = _get(f"{base}/user", {"X-Auth-Key": key, "X-Auth-Email": email},
                                "Cloudflare")
    else:
        code, body, down = _get(f"{base}/user/tokens/verify",
                                {"Authorization": f"Bearer {key}"}, "Cloudflare")
    if code is None:
        return False, down
    if code < 300 and body.get("success", True):
        return True, "Cloudflare accepted the key."
    if code in (400, 401, 403):
        return False, ("Cloudflare refused this key" + (" and email" if email else "")
                       + ". A global API key needs its account's email; a token does not.")
    return False, f"Cloudflare answered {code} when the key was checked; try again."


CHECKS = {"gemini": gemini, "openai": openai, "hetzner": hetzner, "cloudflare": cloudflare}


def check(name: str, value) -> tuple[bool, str]:
    fn = CHECKS.get(name)
    if fn is None:
        return False, f"the host has no check named {name!r}"
    if isinstance(value, dict) and name != "cloudflare":
        value = next(iter(value.values()), "")
    return fn(value)
