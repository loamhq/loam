"""Choose the agent identity shown outside this install.
Per-document choices override the default. Mask local replies as they leave
so a change also affects older notes. Preserve the true author in storage.
Remote documents retain the identity that was sent to their owner."""

from __future__ import annotations

CHOICES = ("names", "one")
DEFAULT = "names"
ALIAS_ID = "owner-agent"


def effective(choice: str | None, default: str | None) -> str:
    """The choice in force: the doc's own, else the default, else names."""
    if choice in CHOICES:
        return choice
    return default if default in CHOICES else DEFAULT


def alias_name(owner_name: str) -> str:
    return f"{(owner_name or 'Owner').strip()}'s agent"


def alias(owner_name: str) -> dict:
    """The one identity every agent appears as."""
    return {"id": ALIAS_ID, "name": alias_name(owner_name), "kind": "agent"}


def mask(obj, agent_ids, one: dict):
    """``obj`` (an answer) with every agent of this Loam replaced by ``one``:
    an author or closer (a dict whose ``id`` is an agent's), an authorship
    span's ``author`` id, and a ``people`` map's entries. Nothing else is
    touched; the words people wrote are theirs."""
    ids = set(agent_ids)
    if not ids:
        return obj

    def walk(o):
        if isinstance(o, list):
            return [walk(x) for x in o]
        if not isinstance(o, dict):
            return o
        if o.get("id") in ids and ("name" in o or "kind" in o):
            return dict(one)
        out = {}
        for k, v in o.items():
            if k == "people" and isinstance(v, dict):
                out[k] = {}
                for pk, p in v.items():
                    if pk in ids:
                        out[k][one["id"]] = dict(one)
                    else:
                        out[k][pk] = walk(p)
            elif k in ("author", "by") and isinstance(v, str) and v in ids:
                out[k] = one["id"]
            else:
                out[k] = walk(v)
        return out
    return walk(obj)
