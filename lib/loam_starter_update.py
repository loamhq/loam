"""Refresh each still-stock part of the original starter on release updates."""

from __future__ import annotations

import re
from pathlib import Path
from tempfile import TemporaryDirectory

import loam_agents
import loam_file_revision
import loam_map_migration
import loam_starter as starter
import loam_threads as threads
import loam_update as update


def versions(name: str):
    """Distinct versions of this template in the installed release's ancestry."""
    path = update.prefix() + "docs/starter/" + name
    commits = update.git("log", "--format=%H", "HEAD", "--", ":/" + path).stdout.splitlines()
    for commit in commits:
        text = update.show(commit, path)
        if text is not None:
            yield text


def refresh_file(path: Path, replacement: str, originals) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    data = path.read_bytes()
    if data == replacement.encode() or not any(data == text.encode() for text in originals):
        return False
    try:
        loam_file_revision.replace_text(path, replacement, loam_file_revision.revision(data))
    except loam_file_revision.RevisionConflict:
        return False
    return True


def refresh_legacy_layers(ws: Path, draft: dict) -> None:
    """Pre-map installs kept the starter guidance under two different filenames."""
    target = ws / "map.md"
    initial = loam_map_migration.initial_map(ws)
    if target.is_symlink() or not target.is_file() or target.read_bytes() != initial.encode():
        return
    style = "---\nname: conversation\ndescription: Iris's conversation lane.\nkeep-coding-instructions: true\n---\n\n"
    stock_layers = True
    for old, new in (("_active.md", "how-i-am-here.md"), ("_reflection.md", "nights.md")):
        src = ws / old
        if src.is_symlink() or not src.is_file():
            stock_layers = False
            continue
        data = src.read_bytes()
        if old == "_active.md":
            data = data.removeprefix(style.encode())
        if not any(data == starter.fill(t).encode() for t in versions("iris/" + old)):
            stock_layers = False
            continue
        dest = ws / new
        if not dest.exists() and not dest.is_symlink():
            with TemporaryDirectory(dir=ws) as tmp:
                staged = Path(tmp) / new
                staged.write_text(starter.own_files()[new], encoding="utf-8")
                try:
                    dest.hardlink_to(staged)
                except FileExistsError:
                    pass
    if stock_layers:
        always = ws / "_always.md"
        if always.is_symlink():
            return
        if always.is_file():
            # Only the original empty memory-index seed can cease to load.
            original = starter.fill(
                "This file loads into every run you have — with {owner}, and alone. It is "
                "yours: write it, cut it, restructure it however serves you.\n"
                "Seeded DATE with your `memory/` index copied verbatim, because that "
                "layer no longer loads by itself; the notes it points at are still on "
                "disk under `memory/`.\n")
            if not re.fullmatch(re.escape(original).replace("DATE", r"\d{4}-\d{2}-\d{2}"),
                                always.read_bytes().decode("utf-8", errors="replace")):
                return
        refresh_file(target, draft["map"], [initial])


def refresh() -> None:
    with loam_agents.locked():
        seeded = starter.status()
        draft = starter.draft()
        if not isinstance(seeded, dict) or seeded.get("agent") != draft["id"]:
            return
        agent, tid = seeded["agent"], seeded.get("thread")
        if not tid:
            return
        ws = loam_agents.ai_labs() / agent
        if ws.is_symlink() or not (ws / "agent.json").is_file():
            return
        refresh_legacy_layers(ws, draft)
        files = {name + ".md": draft[name] for name in ("soul", "map")}
        for name, replacement in (files | starter.own_files()).items():
            refresh_file(ws / name, replacement,
                         (starter.fill(t) for t in versions("iris/" + name)))

        with threads.locked(threads.VAULT):
            try:
                meta, messages, path = threads.read(tid)
            except threads.ThreadError as exc:
                if exc.status != 404:
                    raise
                return
            if (not meta.get("hello") or meta.get("archived") or meta["default"] != agent
                    or any(threads.is_owner(m["author"]) for m in messages)):
                return
            if len(messages) != 1:
                return
            msg = messages[0]
            if (not msg.get("preset") or msg["id"] != seeded.get("message")
                    or msg["author"] != agent):
                return
            _, current = starter.read_hello()
            if msg["text"] != current and not any(
                    starter.parse_hello(t)[1] == msg["text"] for t in versions("hello.md")):
                return
            clear_engine = (not seeded.get("inherits_engine") and meta["engines"] == {agent: "claude-code"}
                            and not meta["models"] and not threads.provider_card(agent).get("provider"))
            if clear_engine:
                meta["engines"].clear()
            if clear_engine or msg["text"] != current:
                threads._write_atomic(path, threads.render_front(meta)
                                      + threads.render_message({**msg, "text": current}))
            upgraded = {"inherits_engine": True, "first_weeks": True, "week_one": True,
                        "used_controls": seeded.get("used_controls", [])}
            if any(seeded.get(k) != v for k, v in upgraded.items()):
                loam_agents._write_json(starter.marker_path(), seeded | upgraded)
