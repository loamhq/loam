#!/usr/bin/env python3
"""Permanent theme face slots: allocation, stability, backfill and AVATAR_VARIANTS.

Run: python3 tests/test_face_slots.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import loam_agents  # noqa: E402
import loam_broker  # noqa: E402
import loam_profile  # noqa: E402
import loam_schedule  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail="") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"PASS  {label}")
    else:
        FAIL += 1
        print(f"FAIL  {label} | {detail}")


def write_legacy_agent(root: Path, name: str, *, mtime: float | None = None,
                       archived: bool = False) -> Path:
    ws = root / name
    ws.mkdir(parents=True, exist_ok=True)
    soul = ws / "soul.md"
    soul.write_text(f"# {name}\n", encoding="utf-8")
    cfg = {
        "name": name,
        "display": name.capitalize(),
        "workspace": str(ws),
        "vault": str(root.parent / "vault"),
    }
    if archived:
        cfg["archived"] = {"at": "2026-11-20T00:00:00Z", "by": "owner"}
    aj = ws / "agent.json"
    aj.write_text(loam_profile.render_agent(cfg), encoding="utf-8")
    if mtime is not None:
        os.utime(soul, (mtime, mtime))
        os.utime(aj, (mtime, mtime))
    return ws


def draft_for(name: str) -> dict:
    return {
        "id": name,
        "name": name.capitalize(),
        "soul": f"# {name}\n",
        "config": f"# {name}\n",
    }


def make_env(tmp: Path) -> Path:
    labs = tmp / "ai-labs"
    state = tmp / "state" / "agents"
    sched = tmp / "state" / "schedules"
    vault = tmp / "vault"
    for p in (labs, state, sched, vault):
        p.mkdir(parents=True, exist_ok=True)
    os.environ["LOAM_AI_LABS"] = str(labs)
    os.environ["LOAM_AGENTS_STATE"] = str(state)
    os.environ["LOAM_SCHEDULE_STATE"] = str(sched)
    os.environ["LOAM_VAULT"] = str(vault)
    os.environ.pop("AVATAR_VARIANTS", None)
    loam_schedule.STATE = sched
    loam_broker.AI_LABS = labs
    loam_agents._CARDS.clear()
    return labs


def find_ids_with_fnv(target: int, n: int) -> list[str]:
    out = []
    i = 0
    while len(out) < n:
        cand = f"bot-{i}"
        if loam_agents.fnv_face_slot(cand, 8) == target:
            out.append(cand)
        i += 1
    return out


def test_fresh_creation_4_8_9_and_delete() -> None:
    with tempfile.TemporaryDirectory(prefix="loam-face-fresh-") as td:
        labs = make_env(Path(td))
        noop_provision = lambda agent, wake: "ok"

        # Four agents whose FNV hashes ALL collide on slot 6 still get 4 distinct slots.
        colliders = find_ids_with_fnv(6, 4)
        for name in colliders:
            loam_agents.make(name, draft_for(name), "03:00", provision=noop_provision)

        slots4 = [loam_agents.face_slot_of(n, root=labs) for n in colliders]
        check("four colliding-hash agents never collide",
              len(set(s % 8 for s in slots4)) == 4 and slots4 == [0, 1, 2, 3], slots4)

        # Four more fill all eight slots.
        more4 = [f"extra-{i}" for i in range(4)]
        for name in more4:
            loam_agents.make(name, draft_for(name), "03:00", provision=noop_provision)
        all8 = colliders + more4
        slots8 = [loam_agents.face_slot_of(n, root=labs) for n in all8]
        check("eight agents fill all eight slots",
              set(s % 8 for s in slots8) == set(range(8)), slots8)

        # Ninth repeats modulo 8, and stays distinct for a 10-face theme.
        loam_agents.make("ninth", draft_for("ninth"), "03:00", provision=noop_provision)
        ninth = loam_agents.face_slot_of("ninth", root=labs)
        check("ninth repeats mod 8 evenly and gets slot 8 for larger themes",
              ninth == 8 and (ninth % 8) == 0 and (ninth % 10) == 8, ninth)

        # Renaming an agent moves its folder/id and changes nobody's slot.
        before_rename = {n: loam_agents.face_slot_of(n, root=labs) for n in all8 + ["ninth"]}
        loam_profile.update_agent(colliders[0], {"display": "Renamed"}, lambda *a: a[2])
        all8_after = ["renamed", *all8[1:]]
        expected_after = {("renamed" if n == colliders[0] else n): slot
                          for n, slot in before_rename.items()}
        after_rename = {n: loam_agents.face_slot_of(n, root=labs) for n in all8_after + ["ninth"]}
        check("renaming an agent changes nobody's face slot",
              expected_after == after_rename and not (labs / colliders[0]).exists(),
              (expected_after, after_rename))

        # Deleting one agent changes nobody else's slot.
        deleted = colliders[1]  # slot 1
        shutil.rmtree(labs / deleted)
        loam_agents._CARDS.clear()
        survivors = [n for n in all8_after + ["ninth"] if n != deleted]
        after_delete = {n: loam_agents.face_slot_of(n, root=labs) for n in survivors}
        check("deleting one agent changes nobody else",
              all(after_delete[n] == expected_after[n] for n in survivors), after_delete)

        # Adding a replacement after deleting slot 1 fills the free mod-8 face (1),
        # keeping all 8 faces represented even though slot 8 (mod 8 == 0) exists.
        loam_agents.make("tenth", draft_for("tenth"), "03:00", provision=noop_provision)
        tenth = loam_agents.face_slot_of("tenth", root=labs)
        check("new agent after deletion fills freed face 1",
              tenth == 1 and set(loam_agents.face_slot_of(n, root=labs) % 8 for n in survivors + ["tenth"]) == set(range(8)),
              tenth)

        # loam_profile.kept preserves face_slot across re-provisioning.
        cfg = loam_agents.card("renamed", labs)
        check("loam_profile.kept preserves face_slot",
              loam_profile.kept(cfg).get("face_slot") == expected_after["renamed"],
              loam_profile.kept(cfg))


def test_backfill_collided_roster() -> None:
    with tempfile.TemporaryDirectory(prefix="loam-face-backfill-") as td:
        labs = make_env(Path(td))
        # Build a 4-agent legacy roster:
        # - oldest_a (fnv=6) and newer_b (fnv=6) COLLIDE
        # - unique_c (fnv=0) is NEWER than both, and its face 0 is unique!
        # - unique_d (fnv=3) is unique!
        c6 = find_ids_with_fnv(6, 2)
        u0 = find_ids_with_fnv(0, 1)[0]
        u3 = find_ids_with_fnv(3, 1)[0]
        oldest_a, newer_b = c6[0], c6[1]

        write_legacy_agent(labs, oldest_a, mtime=1_700_000_100)
        write_legacy_agent(labs, newer_b, mtime=1_700_000_200)
        write_legacy_agent(labs, unique_c := u0, mtime=1_700_000_300)
        write_legacy_agent(labs, unique_d := u3, mtime=1_700_000_400)

        cards = loam_broker.roster_cards()
        by_id = {c["id"]: c["face_slot"] for c in cards}

        check("older colliding agent keeps its FNV face", by_id[oldest_a] == 6, by_id)
        check("newer unique agent keeps its FNV face (not stolen by mover)", by_id[unique_c] == 0, by_id)
        check("other unique agent keeps its FNV face", by_id[unique_d] == 3, by_id)
        check("only newer colliding agent moves, to first free slot (1)", by_id[newer_b] == 1, by_id)
        check("all four backfilled agents are distinct", len(set(by_id.values())) == 4, by_id)

        # Persisted on disk so a second read is identical even if an agent is deleted.
        shutil.rmtree(labs / oldest_a)
        loam_agents._CARDS.clear()
        cards2 = {c["id"]: c["face_slot"] for c in loam_broker.roster_cards()}
        check("backfilled slots persist across deletion of older agent",
              cards2 == {newer_b: 1, unique_c: 0, unique_d: 3}, cards2)


def test_backfill_events_tiebreak_and_avatar_variants() -> None:
    with tempfile.TemporaryDirectory(prefix="loam-face-events-") as td:
        labs = make_env(Path(td))
        # Both agents share fnv=6; starter event puts iris first even if mtimes match.
        other = [x for x in find_ids_with_fnv(6, 3) if x != "iris"][0]
        write_legacy_agent(labs, "iris", mtime=1_700_000_500)
        write_legacy_agent(labs, other, mtime=1_700_000_500)
        loam_agents.log({"kind": "starter", "agent": "iris", "at": "2026-11-20T10:00:00Z"})
        loam_agents.log({"kind": "created", "agent": other, "at": "2026-11-20T10:00:00Z"})

        slots = loam_agents.ensure_face_slots(labs)
        check("events.jsonl order keeps older starter on its FNV face",
              slots["iris"] == 6 and slots[other] == 0, slots)

    with tempfile.TemporaryDirectory(prefix="loam-face-fixed-") as td:
        labs = make_env(Path(td))
        os.environ["AVATAR_VARIANTS"] = json.dumps({"iris": 2})
        # Another agent whose FNV is 2 should yield to AVATAR_VARIANTS["iris"] = 2.
        fnv2 = find_ids_with_fnv(2, 1)[0]
        write_legacy_agent(labs, "iris", mtime=1_700_000_200)
        write_legacy_agent(labs, fnv2, mtime=1_700_000_100)

        slots = loam_agents.ensure_face_slots(labs)
        check("AVATAR_VARIANTS wins during backfill and avoids collision",
              slots["iris"] == 2 and slots[fnv2] != 2, slots)
        os.environ.pop("AVATAR_VARIANTS", None)


def test_archived_agent_slot_preserved_and_live_spread() -> None:
    with tempfile.TemporaryDirectory(prefix="loam-face-archive-") as td:
        labs = make_env(Path(td))
        noop_provision = lambda agent, wake: "ok"
        for i in range(4):
            loam_agents.make(f"a{i}", draft_for(f"a{i}"), "03:00", provision=noop_provision)
        # Archive a3 (slot 3): a4 should take 4 so restoring a3 stays distinct.
        cfg3 = loam_agents.card("a3", labs)
        cfg3["archived"] = {"at": "2026-11-22T00:00:00Z", "by": "owner"}
        loam_agents._write_agent("a3", cfg3, root=labs)
        loam_agents.make("a4", draft_for("a4"), "03:00", provision=noop_provision)
        check("archived agent keeps slot 3 and new agent picks unused 4",
              loam_agents.face_slot_of("a3", root=labs) == 3
              and loam_agents.face_slot_of("a4", root=labs) == 4,
              (loam_agents.face_slot_of("a3", root=labs), loam_agents.face_slot_of("a4", root=labs)))


if __name__ == "__main__":
    test_fresh_creation_4_8_9_and_delete()
    test_backfill_collided_roster()
    test_backfill_events_tiebreak_and_avatar_variants()
    test_archived_agent_slot_preserved_and_live_spread()
    print(f"\n{PASS}/{PASS + FAIL} passed")
    sys.exit(1 if FAIL else 0)
