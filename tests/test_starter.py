#!/usr/bin/env python3
"""lib/loam_starter.py: Iris and the hello thread on a blank install (first
release L11).

A throwaway install (LOAM_CONFIG names a scratch config before anything
imports loam_config), with provisioning and the Remote Control unit faked:
the real ones are proved on a scratch root in the job that built this.

Checks: the starter agent is made from docs/starter/ in the map's shape
(2026-11-18: soul, config, map, the two files her map points at, agent.json,
and none of `_always.md`, `_active.md`, `_reflection.md`), with the
install's names filled in and no placeholder token left; the hello thread is pinned, marked
`hello: true`, has the agent as its default and one message, the starter's
text, posted as the agent and marked `"preset": true` in the record and in
what the routes read; the agent's turn is told the opening was preset; the
marker is written; a second seed does nothing; and an install that already
has an agent of the starter's id (the owner's box) gets nothing at all, its
soul untouched and no thread made.

Run: python3 tests/test_starter.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="test-starter-"))
CONFIG = TMP / "loam.toml"
CONFIG.write_text(f'''
[owner]
id = "sam"
name = "Sam"
[paths]
home = "{TMP}"
vault = "{TMP}/vault"
workspaces = "{TMP}/agents"
state = "{TMP}/state"
''')
os.environ["LOAM_CONFIG"] = str(CONFIG)
for var in ("LOAM_VAULT", "LOAM_AI_LABS", "LOAM_THREADS_STATE", "LOAM_AGENTS_STATE"):
    os.environ.pop(var, None)
sys.path.insert(0, str(ROOT / "lib"))
import loam_config as lc  # noqa: E402
import loam_skills  # noqa: E402
import loam_starter as st  # noqa: E402
import loam_loading  # noqa: E402
import loam_threads as threads  # noqa: E402

FAILURES = 0


def check(label: str, got, want) -> None:
    global FAILURES  # noqa: PLW0603
    ok = got == want
    FAILURES += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f"\n      got  {got!r}\n      want {want!r}"))


provisioned = []


def fake_provision(agent, wake):
    provisioned.append((agent, wake))
    return "provisioned"


def main() -> int:
    lc.WORKSPACES.mkdir(parents=True)
    made = loam_skills.seed_vault(lc.VAULT)
    about = lc.VAULT / "About me.md"
    check("seed-vault writes About me.md at the top of the vault",
          ("About me.md" in made, about.read_text().splitlines()[0]), (True, "# About me"))
    check("...and the vault's README names it",
          "`About me.md`" in (lc.VAULT / "README.md").read_text(), True)
    about.write_text("# About me\n\nSam's own words.\n")
    loam_skills.seed_vault(lc.VAULT)
    check("...and never writes it again", about.read_text(), "# About me\n\nSam's own words.\n")

    # The page draws a single newline as a line break, so the hello message
    # is one line per paragraph: hard-wrapped, it broke mid-sentence on a
    # phone (the first-day walk, 2026-11-16).
    _, hello = st.read_hello()
    check("the hello message has no line break inside a paragraph",
          [p for p in hello.split("\n\n") if "\n" in p], [])

    # A first try whose provisioning fails (first release L12): the half-made
    # workspace is left with agent.json.failed, and the next run, with no
    # hand clearing it, sets it aside and makes Iris properly.
    def failing_provision(agent, wake):
        raise RuntimeError("the provider's CLI was not there yet")
    import loam_agents  # noqa: PLC0415
    try:
        st.seed(provision=failing_provision, units=lambda a, on: "active")
        failed = None
    except loam_agents.AgentsError as exc:
        failed = str(exc)
    ws = lc.WORKSPACES / "iris"
    check("a failed make raises and leaves agent.json.failed",
          (failed is not None, (ws / "agent.json.failed").exists(), (ws / "agent.json").exists()),
          (True, True, False))
    check("...with the marker saying the starter is part made",
          (st.status() or {}).get("state"), "making")

    out = st.seed(provision=fake_provision, units=lambda a, on: "active")
    aside = sorted((loam_agents.state() / "failed-starts").iterdir())
    check("the next run sets the failed start aside, kept, out of the workspaces",
          (len(aside), aside[0].name.startswith("iris-"), (aside[0] / "agent.json.failed").exists()),
          (1, True, True))
    check("...and says so", "set aside" in out.get("resumed", ""), True)
    ws = lc.WORKSPACES / "iris"
    check("the starter agent is made and provisioned once", provisioned, [("iris", out["wake"])])
    check("...from docs/starter, in the map's shape",
          sorted(p.name for p in ws.iterdir()),
          ["agent.json", "config.md", "crons", "how-i-am-here.md", "map.md",
           "memory", "nights.md", "skills", "soul.md", "threads"])
    check("her map points at her two files; her nights keep About me.md",
          ("`how-i-am-here.md`" in (ws / "map.md").read_text(),
           "`nights.md`" in (ws / "map.md").read_text(),
           "About me.md" in (ws / "nights.md").read_text()), (True, True, True))
    # Loam's text no longer names About me.md (the owner's short text,
    # 2026-11-19); her nights.md does, checked above.
    check("the owner's shared page is made, in this install's workspaces",
          loam_loading.owner_page(), lc.WORKSPACES / "_shared" / "owner.md")
    check("...and exists", loam_loading.owner_page().is_file(), True)
    card = json.loads((ws / "agent.json").read_text())
    check("agent.json: its id and name, and no emoji face (retired 2026-11-16)",
          (card["name"], card["display"], "emoji" in card), ("iris", "Iris", False))
    texts = "".join((ws / n).read_text() for n in (
        "soul.md", "config.md", "map.md", "how-i-am-here.md", "nights.md"))
    check("no placeholder token is left", [t for t in ("{owner}", "@VAULT@", "@WORKSPACES@", "@SYSTEM@") if t in texts], [])
    check("...the install's names are filled in", "Sam" in texts, True)
    check("the soul is the starter's, word for word but the names",
          (ws / "soul.md").read_text(), st.fill((st.STARTER_DIR / "iris" / "soul.md").read_text()))

    skill = "loam-bringing-context-from-another-ai"
    check("her how-i-am-here.md offers bringing context from another AI, by "
          "the library skill, and the seeded vault's library has that skill",
          (f"`{skill}`" in (ws / "how-i-am-here.md").read_text(),
           (lc.VAULT / "System" / "Loam" / "skills" / skill / "SKILL.md").is_file(),
           (lc.VAULT / "System" / "Loam" / "skills" / skill / "inspect_export.py").is_file()),
          (True, True, True))

    meta, msgs, path = threads.read(out["thread"])
    check("the hello thread: titled, pinned, hello, Iris its default and member",
          (meta["title"], meta.get("pinned"), meta.get("hello"), meta["default"], meta["members"]),
          ("Hello", True, True, "iris", ["iris"]))
    check("...holding one message, by Iris, the starter's text, marked preset",
          [(m["id"], m["author"], m["kind"], m.get("preset"), m["text"]) for m in msgs],
          [("m1", "iris", "message", True, st.read_hello()[1])])
    check("...and the record's own comment says so",
          '"preset": true' in path.read_text(), True)
    check("...in the vault's conversations folder", path.parent, lc.VAULT / "Conversations")
    row = threads.summary(out["thread"])
    check("the thread's row says hello and is unread",
          (row["hello"], row["pinned"], row["unread"]), (True, True, 1))
    page = threads.page(out["thread"])
    first = page["messages"][0] if "messages" in page else None
    check("the page's read carries `preset`", (first or {}).get("preset"), True)
    told = threads.transcript(msgs)
    check("a turn is told the opening was Loam's", "(preset: Loam posted this opening" in told, True)
    check("the marker names the agent and the thread",
          {k: st.status()[k] for k in ("agent", "thread", "message")},
          {"agent": "iris", "thread": out["thread"], "message": "m1"})

    again = st.seed(provision=fake_provision, units=lambda a, on: "active")
    check("a second seed does nothing", ("skipped" in again, len(provisioned)), (True, 1))

    # A run that made the agent and stopped before the thread (the marker
    # still says making): the next one writes no second agent and finds,
    # rather than duplicates, the thread it may have begun.
    record = st.status()
    loam_agents._write_json(st.marker_path(), {"state": "making", "agent": "iris",
                                               "wake": out["wake"], "at": record["at"]})
    resumed = st.seed(provision=fake_provision, units=lambda a, on: "active")
    check("a made agent with its marker at making is finished, not remade",
          (len(provisioned), resumed.get("thread"), resumed.get("message")),
          (1, out["thread"], "m1"))
    check("...one hello thread still, holding one message",
          (sum(1 for t in threads.rebuild_index() if threads.read(t)[0].get("hello")),
           len(threads.read(out["thread"])[1])), (1, 1))

    # An install whose agent of that id is its own (the owner's box): nothing.
    st.marker_path().unlink()
    (ws / "soul.md").write_text("their own soul\n")
    before = sorted(threads.index())
    third = st.seed(provision=fake_provision, units=lambda a, on: "active")
    check("an existing agent of the starter's id is left alone",
          ("exists" in third.get("skipped", ""), (ws / "soul.md").read_text(), len(provisioned)),
          (True, "their own soul\n", 1))
    check("...and no thread is made", sorted(threads.index()), before)
    check("...and no marker written", st.status(), None)

    print(f"\n{'OK' if not FAILURES else f'{FAILURES} FAILED'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
