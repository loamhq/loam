#!/usr/bin/env python3
"""Verify that a fresh install seeds its starter and preset thread once and can resume a failed provision."""

from __future__ import annotations

import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="test-starter-"))
CONFIG = TMP / "loam.toml"
CONFIG.write_text(f'''
timezone = "America/Los_Angeles"
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

    # Each paragraph needs one source line because the page renders single newlines as
    # breaks.
    _, hello = st.read_hello()
    check("the hello message has no line break inside a paragraph",
          [p for p in hello.split("\n\n") if "\n" in p], [])

    # A retry sets aside the partial workspace left by a failed provision.
    def failing_provision(agent, wake):
        raise RuntimeError("the provider's CLI was not there yet")
    import loam_agents  # noqa: PLC0415
    try:
        st.seed(provision=failing_provision)
        failed = None
    except loam_agents.AgentsError as exc:
        failed = str(exc)
    ws = lc.WORKSPACES / "iris"
    check("a failed make raises and leaves agent.json.failed",
          (failed is not None, (ws / "agent.json.failed").exists(), (ws / "agent.json").exists()),
          (True, True, False))
    check("...with the marker saying the starter is part made",
          (st.status() or {}).get("state"), "making")

    out = st.seed(provision=fake_provision)
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
    making_skill = "loam-making-an-agent"
    check("her map points to the installed agent-making skill for any thread",
          (f"`{making_skill}`" in (ws / "map.md").read_text(),
           loam_skills.seen_by(ws).get(making_skill)),
          (True, ("loam", loam_skills.loam_dir() / "skills" / making_skill)))
    # The workspace loading map supplies the path to the owner's page.
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

    import loam_identity as identity
    (ws / "CLAUDE.md").write_text("@soul.md\n@config.md\n@map.md\n")
    decoy, source = identity.pick_decoy(ws)
    check("a lone starter still probes its real soul against a held-out greeting phrase",
          (source, decoy in st.read_hello()[1], decoy in texts,
           identity.pick_canary(ws) in (ws / "soul.md").read_text()),
          ("starter greeting", True, False, True))
    other = lc.WORKSPACES / "second"
    other.mkdir()
    (other / "agent.json").write_text("{}")
    try:
        identity.pick_decoy(ws)
        refused = False
    except identity.IdentityError:
        refused = True
    check("a second agent missing its soul still refuses the wake", refused, True)
    shutil.rmtree(other)
    (ws / "CLAUDE.md").unlink()

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
    board = threads.board_snapshot(meta)
    cards = [line for line in board["markdown"].splitlines() if line.startswith("- [ ] ")]
    check("Hello seeds six ordinary Markdown checkboxes with the starter's exact words",
          (board["markdown"], len(cards), st.status().get("first_weeks")),
          ((st.STARTER_DIR / "hello-board.md").read_text(), 6, True))
    ticked = board["markdown"].replace("- [ ]", "- [x]", 1)
    check("...an ordinary board save can tick the first card",
          threads.write_board(meta, ticked, board["revision"])["markdown"], ticked)
    board = threads.board_snapshot(meta)
    check("...holding one message, by Iris, the starter's text, marked preset",
          [(m["id"], m["author"], m["kind"], m.get("preset"), m["text"]) for m in msgs],
          [("m1", "iris", "message", True, st.read_hello()[1])])
    check("...greeting the owner by the name from setup",
          [m["text"].startswith("Welcome to your Loam, Sam 👋 I'm Iris, your first agent.\n\n![Chats.") for m in msgs], [True])
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

    import loam_check as check_runtime
    from unittest.mock import patch
    born = check_runtime.parse_iso(st.status()["at"])
    with patch.object(check_runtime, "now", return_value=born + 1):
        check("before the first tick is due, availability is pending",
              check_runtime.current_run_status(), {"agents_can_run": None, "why": [], "action": None})
        check_runtime.STATE_FILE.write_text("broken")
        check("an unreadable first check still raises the stopped warning",
              check_runtime.current_run_status()["agents_can_run"], False)
        check_runtime.STATE_FILE.unlink()
    with patch.object(check_runtime, "now", return_value=born + check_runtime.PERIOD_S):
        check("a first tick missing for a full period raises the stopped warning",
              check_runtime.current_run_status()["why"], ["Loam's own check has stopped."])

    check("new starter has first-use hints", st.first_use(), [])
    st.use_control("Board")
    st.use_control("Settings")
    st.use_control("Board")
    check("using a control dismisses only its own caption, once",
          st.first_use(), ["Board", "Settings"])

    personal = "## My move\n\n- [ ] Pack the kettle.\n"
    threads.write_board(meta, personal, board["revision"])
    again = st.seed(provision=fake_provision)
    check("a second seed does nothing", ("skipped" in again, len(provisioned)), (True, 1))

    # A run that made the agent and stopped before the thread (the marker
    # still says making): the next one writes no second agent and finds,
    # rather than duplicates, the thread it may have begun.
    record = st.status()
    loam_agents._write_json(st.marker_path(), {"state": "making", "agent": "iris",
                                               "wake": out["wake"], "at": record["at"]})
    resumed = st.seed(provision=fake_provision)
    check("a made agent with its marker at making is finished, not remade",
          (len(provisioned), resumed.get("thread"), resumed.get("message")),
          (1, out["thread"], "m1"))
    check("...one hello thread still, holding one message",
          (sum(1 for t in threads.rebuild_index() if threads.read(t)[0].get("hello")),
           len(threads.read(out["thread"])[1])), (1, 1))

    check("retrying a seed preserves a person's board after they remove the list",
          threads.board_text(meta), personal)
    check("an interrupted seed from before week-one support stays legacy",
          st.status().get("week_one"), None)

    # 03:00 UTC is still the previous date in the installation's zone.
    with patch.object(lc, "TIMEZONE", "America/Los_Angeles"):
        loam_agents._write_json(st.marker_path(), {**record, "at": "2026-11-21T03:00:00Z"})
        tid = out["thread"]
        threads.update(tid, title="Our first chat")
        threads.append(tid, lc.OWNER_ID, "I've been here every day.")
        wake = runpy.run_path(str(ROOT / "bin" / "wake"))
        day6 = "2026-11-27T07:59:59Z"
        days = [f"2026-{month}-{day:02d}T08:00:00Z"
                for month, day in [(11, 27), (11, 28), (11, 29), (11, 30),
                                   (12, 1), (12, 2), (12, 3), (12, 4)]]
        day7, day8, day14 = days[0], days[1], days[-1]

        def occasion(at, cron=None):
            return "The first week on Loam has ended." in wake["lane_prompt"](
                "iris", lc.VAULT, at, cron, ws)[0]

        check("the unsent occasion reaches ordinary reflections on local days seven through thirteen",
              [occasion(day6), *[occasion(day) for day in days], occasion(day7, "other")],
              [False, *([True] * 7), False, False])
        check("the occasion follows the original Hello even after a rename",
              f" post {tid} " in st.week_one_prompt("iris", day7), True)
        check("other agents get no occasion", st.week_one_prompt("other", day7), "")
        threads.update(tid, archived=True)
        check("archiving Hello ends the occasion", occasion(day7), False)
        try:
            threads.append_once(tid, "iris", "One week.", occasion=st.WEEK_ONE)
            refused = False
        except threads.ThreadError as exc:
            refused = exc.status == 409
        check("the append lock also checks an archived chat", refused, True)
        threads.update(tid, archived=False)

        env = {**os.environ, "LOAM_WAKE": "reflection", "CLAUDE_PROJECT_DIR": str(ws)}
        command = [str(ROOT / "bin" / "thread"), "post", tid, "--by", "iris",
                   "--starter-week", "--", "One week of making together."]
        outside = subprocess.run(command, env=env, capture_output=True, text=True)
        check("the week-one post needs a running reflection", outside.returncode, 1)
        wake["mark_running"]("iris", day7, lc.VAULT, "reflection")
        try:
            first = subprocess.run(command, env=env, capture_output=True, text=True)
            retry = subprocess.run(command, env=env, capture_output=True, text=True)
        finally:
            wake["clear_running"]("iris")
        marks = [m["text"] for m in threads.read(tid)[1] if m.get("occasion") == st.WEEK_ONE]
        check("retried anniversary posts produce one stored message and no new prompt",
              (first.returncode, retry.returncode, marks, occasion(day7), occasion(day8)),
              (0, 0, ["One week of making together."], False, False))
        # Independently exercise a missed anniversary at both ends of the window.
        post_results = []
        for at in (day8, days[6], day14):
            existing = next((m["id"] for m in threads.read(tid)[1]
                             if m.get("occasion") == st.WEEK_ONE), None)
            if existing:
                threads.replace_message(tid, existing, [])
            wake["mark_running"]("iris", at, lc.VAULT, "reflection")
            try:
                post_results.append(subprocess.run(
                    command, env=env, capture_output=True, text=True).returncode)
            finally:
                wake["clear_running"]("iris")
        check("a missed occasion can post on days eight and thirteen, but not fourteen",
              post_results, [0, 0, 1])

    threads.delete(out["thread"])
    st.seed(provision=fake_provision)
    check("a deleted Hello stays deleted", out["thread"] in threads.index(), False)

    # An install whose agent of that id is its own (the owner's box): nothing.
    st.marker_path().unlink()
    (ws / "soul.md").write_text("their own soul\n")
    st.use_control("Board")
    check("an existing install never acquires a first-use marker", st.status(), None)
    before = sorted(threads.index())
    third = st.seed(provision=fake_provision)
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
