"""Seed the starter agent and its pinned hello thread once per installation.
The preset greeting is stored without opening a provider session. The seed
marker lets a retry finish its own interrupted work and protects an existing
agent or a deliberately removed thread. See docs/starter.md."""

from __future__ import annotations

import json
from pathlib import Path

import loam_agents
import loam_config as lc

STARTER_DIR = Path(__file__).resolve().parent.parent / "docs" / "starter"
MARKER = "starter.json"
# Additional workspace files referenced by the starter’s map.
OWN_FILES = ("how-i-am-here.md", "nights.md")


def marker_path() -> Path:
    return loam_agents.state() / MARKER


def fill(text: str) -> str:
    """A starter file's placeholders, for this install."""
    system = lc.VAULT / lc.vault_folder("system")
    return (lc.expand(text).replace("@SYSTEM@", str(system))
            .replace("{owner}", lc.OWNER_NAME))


def read_hello(root: Path | None = None) -> tuple[str, str]:
    """(title, first message) from hello.md."""
    text = ((root or STARTER_DIR) / "hello.md").read_text(encoding="utf-8")
    title = "Hello"
    if text.startswith("---\n"):
        head, _, text = text[4:].partition("\n---\n")
        for line in head.splitlines():
            key, _, value = line.partition(":")
            if key.strip() == "title" and value.strip():
                title = value.strip().strip('"')
    return title, fill(text).strip()


def draft(root: Path | None = None) -> dict:
    """Read the starter's draft and substitute the install's configured names."""
    src = (root or STARTER_DIR) / "iris"
    card = json.loads((src / "agent.json").read_text(encoding="utf-8"))
    out = {"id": card["id"], "name": card["name"], "provider": card.get("provider")}
    for field, name in (("soul", "soul.md"), ("config", "config.md"),
                        ("map", "map.md")):
        out[field] = fill((src / name).read_text(encoding="utf-8"))
    return out


def own_files(root: Path | None = None) -> dict[str, str]:
    """{name: text} of the starter's files beside soul, config and map."""
    src = (root or STARTER_DIR) / "iris"
    return {name: fill((src / name).read_text(encoding="utf-8"))
            for name in OWN_FILES if (src / name).is_file()}


def status() -> dict | None:
    try:
        return json.loads(marker_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def inherit_legacy_hello() -> bool:
    """Clear the engine preselected by seeding an untouched legacy hello.

    Old records did not label their generated engines. The completed seed
    marker was written after the preset. A later thread write could have
    chosen an engine, so leave those records alone.
    """
    import loam_threads as threads  # noqa: PLC0415

    seeded = status()
    if not isinstance(seeded, dict) or not seeded.get("thread") or not seeded.get("agent"):
        return False
    agent, tid = seeded["agent"], seeded["thread"]
    with threads.locked(threads.VAULT):
        try:
            meta, messages, path = threads.read(tid)
            if path.stat().st_mtime_ns >= marker_path().stat().st_mtime_ns:
                return False
        except (OSError, threads.ThreadError):
            return False
        if (meta.get("hello") is not True or meta.get("default") != agent
                or meta["engines"] != {agent: "claude-code"} or meta["models"]
                or threads.provider_card(agent).get("provider")
                or len(messages) != 1 or messages[0].get("preset") is not True
                or messages[0].get("id") != seeded.get("message")):
            return False
        meta["engines"].clear()
        threads._rewrite(path, meta)
        return True


def _hello_thread(agent: str) -> str | None:
    """The id of a hello thread with this agent already on disk, if any."""
    import loam_threads as threads  # noqa: PLC0415
    for tid in threads.rebuild_index():
        try:
            meta, _, _ = threads.read(tid)
        except Exception:  # noqa: BLE001 -- an unreadable record is not the hello thread
            continue
        if meta.get("hello") in (True, "true") and meta.get("default") == agent:
            return tid
    return None


def _set_aside(ws: Path) -> Path:
    """A half-made starter's workspace, moved out of the workspaces so the
    next try starts clean. Kept, never deleted: it says what failed."""
    aside = loam_agents.state() / "failed-starts" / f"{ws.name}-{loam_agents.now_iso().replace(':', '')}"
    aside.parent.mkdir(parents=True, exist_ok=True)
    ws.rename(aside)
    return aside


def seed(*, root: Path | None = None, provision=None) -> dict:
    """Create the starter workspace and hello thread, resuming an interrupted seed.
    A making marker identifies a workspace awaiting its thread. Set a failed
    provision aside before retrying and leave unrelated workspaces alone."""
    import loam_threads as threads  # noqa: PLC0415
    with loam_agents.locked():
        done = status()
        if done and done.get("state") != "making":
            return {"skipped": f"the starter was seeded at {done.get('at')} "
                               f"({done.get('agent')}, thread {done.get('thread')})"}
        d = draft(root)
        agent = d["id"]
        ws = loam_agents.ai_labs() / agent
        resumed = None
        failed_make = (ws / "agent.json.failed").exists() and not (ws / "agent.json").exists()
        if ws.exists():
            if failed_make:
                # The failed-provision marker identifies a partial workspace even without a seed marker.
                resumed = f"set aside a failed start at {_set_aside(ws)}"
            elif not (done and done.get("agent") == agent and (ws / "agent.json").exists()):
                return {"skipped": f"{ws} exists: an agent named {agent} is already "
                                   f"this install's, and its files are its own"}
        first_weeks = not done or done.get("first_weeks") is True
        if not ws.exists():
            wake = loam_agents.free_wake()
            loam_agents._write_json(marker_path(), {"state": "making", "agent": agent,
                                                    "wake": wake, "first_weeks": first_weeks,
                                                    "at": loam_agents.now_iso()})
            loam_agents.make(agent, d, wake, provision=provision)
        else:
            import loam_schedule  # noqa: PLC0415
            wake = (done or {}).get("wake") or loam_schedule.wake_time(agent)
            resumed = resumed or "the agent was made by an earlier run; finishing its thread"
        for name, text in own_files(root).items():
            if not (ws / name).exists():
                (ws / name).write_text(text, encoding="utf-8")
        title, text = read_hello(root)
        tid = _hello_thread(agent)
        if tid is None:
            board = ((root or STARTER_DIR) / "hello-board.md").read_text(encoding="utf-8") if first_weeks else None
            tid = threads.create(agent, title, pinned=True, extra={"hello": True},
                                 initial_board=board)["id"]
        _, msgs, _ = threads.read(tid)
        preset = next((m for m in msgs if m.get("preset")), None)
        msg_id = (preset or threads.append(tid, agent, text, preset=True))["id"]
        record = {"agent": agent, "thread": tid, "message": msg_id,
                  "at": loam_agents.now_iso()}
        if first_weeks:
            record["first_weeks"] = True
        loam_agents._write_json(marker_path(), record)
        loam_agents.log({"kind": "starter", "agent": agent, "thread": tid,
                         "by": "loam", "wake": wake, **({"resumed": resumed} if resumed else {})})
    out = {**record, "wake": wake, "workspace": str(ws)}
    if resumed:
        out["resumed"] = resumed
    return out
