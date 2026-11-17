"""How the owner's agents appear to people outside this Loam.

Two ways, chosen by the owner (2026-11-17): **names**, each agent by its own
name ("Iris · Sam's agent" on another Loam, "Iris" on the owner's own shared
doc), or **one**, every agent as one identity, "Sam's agent", so nobody
outside can tell which agents the owner has, or how many.

The plugin's ``appear`` setting is the default; each shared doc (links.py)
and each doc in Shared with me (withme.py) may choose its own.

Hiding is done where an answer leaves this Loam, never in what is kept: the
sidecar keeps the true persona on every note, reply and save, and the
owner's own view still says which agent did what. On the owner's own docs
the door masks every answer it gives a visitor or a visitor's agent
(``mask``), so a change of choice applies to past notes too. On a doc in
Shared with me, the agent's name is simply never sent (withme.py, ``_auth``);
what was sent under a name before is the other Loam's and cannot be recalled.
"""

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
