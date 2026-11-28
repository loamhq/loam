"""Rename an agent everywhere: id, workspace folder, units, sessions and registries.

Vault thread records (``15_CONVERSATIONS/*.md``) are history and are never
rewritten. Instead, ``agent.json`` records ``former_ids`` so past ids found in
thread records, logs and cached links resolve to the agent they became, and no
other agent may reuse a past alias.

All three providers resume across a folder move:
- Claude Code: move/merge ``~/.claude/projects/<old-slug>`` to ``<new-slug>``.
- Codex: ``thread/resume`` accepts the new ``cwd`` and updates ``state_5.sqlite``
  (we also update ``threads.cwd`` best-effort when the DB is present).
- Antigravity: ``agy --conversation <id>`` resumes directly from the new cwd.
"""

from __future__ import annotations

import datetime as dt
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tomllib
import unicodedata
from pathlib import Path

import loam_agents
import loam_config as lc
import loam_offline
import loam_profile


class RenameError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _default_ai_labs() -> Path:
    return loam_profile._ai_labs()


def _default_vault() -> Path:
    return loam_profile._vault()


def _is_live_box(ai_labs: Path) -> bool:
    if loam_offline.offline():
        return False
    try:
        live_ws = lc._install_live_paths()["workspaces"]
        return ai_labs.resolve() == live_ws.resolve()
    except OSError:
        return False


def _under(path: Path, root: Path) -> bool:
    try:
        p, r = path.resolve(), root.resolve()
        return p == r or r in p.parents
    except OSError:
        return False


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return str(a) == str(b)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _atomic_write_json(path: Path, data: dict, *, indent: int = 2) -> None:
    _atomic_write_text(path, json.dumps(data, indent=indent, ensure_ascii=False) + "\n")


def _pid_alive(pid: int | None) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        rparen = stat.rfind(")")
        if rparen != -1 and stat[rparen + 2:rparen + 3] == "Z":
            return False
    except OSError:
        pass
    return True


def wake_running_busy(agent: str, wake_state: Path | None) -> bool:
    """True when ``<wake_state>/<agent>.running`` exists and belongs to a live
    process (or has no readable PID, e.g. a manual/test marker)."""
    if wake_state is None:
        return False
    marker = wake_state / f"{agent}.running"
    if not marker.is_file():
        return False
    try:
        mdata = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        mdata = {}
    pid = mdata.get("pid") if isinstance(mdata, dict) else None
    return pid is None or _pid_alive(pid)


def validate_rename(old_id: str, new_display, *,
                    ai_labs: Path | None = None) -> tuple[str, str, bool]:
    """Validate a display name update for ``old_id``.

    Returns ``(new_id, clean_display, id_changed)``. Raises ``RenameError``
    (400/404/409) without modifying anything on disk.
    """
    labs = ai_labs or _default_ai_labs()
    if not isinstance(old_id, str) or not old_id or not (labs / old_id / "agent.json").is_file():
        raise RenameError(f"no agent named {old_id!r} on this box", 404)

    if new_display is None:
        return old_id, old_id.capitalize(), False

    if not isinstance(new_display, str):
        raise RenameError("`display` is a name, or null for the agent's own", 400)

    clean = " ".join(new_display.split())
    if not clean or len(clean) > loam_profile.DISPLAY_MAX:
        raise RenameError(f"`display` is 1 to {loam_profile.DISPLAY_MAX} characters", 400)
    if any(unicodedata.category(c).startswith("C") for c in clean):
        raise RenameError("`display` has a control character in it", 400)

    low = clean.lower()
    if low in loam_profile.RESERVED_NAMES or low in loam_agents.RESERVED_IDS:
        raise RenameError(f"{clean!r} is a word Loam keeps for itself", 400)

    new_id = loam_agents.slug(clean)
    if not loam_agents.ID_RE.match(new_id):
        raise RenameError(
            f"{clean!r} does not produce a valid agent folder name "
            f"(needs 2 to 24 letters, digits or dashes starting with a letter)", 400)
    if new_id in loam_agents.RESERVED_IDS:
        raise RenameError(f"{clean!r} ({new_id!r}) is a word Loam keeps for itself", 400)

    taken = loam_agents._names_taken(except_agent=old_id, root=labs)
    for cand in (low, new_id):
        if cand in taken:
            owner = taken[cand]
            raise RenameError(
                f"{clean!r} is {owner}'s name or past alias; an @ would not know "
                f"which of them it meant", 409)

    if new_id != old_id and (labs / new_id).exists():
        raise RenameError(
            f"there is already a folder named {new_id!r} in the agents folder", 409)

    return new_id, clean, new_id != old_id


def _move_or_merge_dir(src: Path, dst: Path, *, git_root: Path | None = None) -> bool:
    """Move ``src`` to ``dst``. Uses ``git mv`` when ``src`` is tracked under
    ``git_root`` and ``dst`` does not yet exist; merges when ``dst`` exists."""
    if not src.exists():
        return False
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        if git_root is not None and (git_root / ".git").exists():
            try:
                rel_src = src.resolve().relative_to(git_root.resolve()).as_posix()
                rel_dst = dst.resolve().relative_to(git_root.resolve()).as_posix()
            except ValueError:
                rel_src = rel_dst = ""
            if rel_src and rel_dst:
                tracked = subprocess.run(
                    ["git", "-C", str(git_root), "ls-files", "--", rel_src],
                    capture_output=True, text=True, check=False)
                if tracked.returncode == 0 and tracked.stdout.strip():
                    moved = _git_mv(git_root, rel_src, rel_dst)
                    if moved:
                        return True
        os.rename(src, dst)
        return True

    # Destination already exists (e.g. agent renamed back to a former name): merge.
    for entry in sorted(src.iterdir()):
        target = dst / entry.name
        if not target.exists():
            os.rename(entry, target)
        elif entry.is_dir() and not entry.is_symlink() and target.is_dir() and not target.is_symlink():
            _move_or_merge_dir(entry, target, git_root=None)
        elif entry.is_file() and not entry.is_symlink():
            # Preserve existing destination file if same name; avoid silent loss by keeping newer.
            try:
                if entry.stat().st_mtime > target.stat().st_mtime:
                    os.replace(entry, target)
                else:
                    entry.unlink()
            except OSError:
                pass
    try:
        shutil.rmtree(src)
    except OSError:
        pass
    return True


def _git_mv(git_root: Path, rel_src: str, rel_dst: str) -> bool:
    import loam_autocommit  # noqa: PLC0415
    try:
        with loam_autocommit.lock(git_root, timeout=10):
            done = subprocess.run(
                ["git", "-C", str(git_root), "mv", rel_src, rel_dst],
                capture_output=True, text=True, check=False, timeout=30)
            return done.returncode == 0
    except (OSError, TimeoutError, subprocess.SubprocessError):
        return False


def _load_provision(ai_labs: Path, vault: Path, codex_home: Path | None,
                    claude_json: Path | None):
    """Load bin/provision as an isolated module bound to the exact paths in use."""
    path = Path(__file__).resolve().parent.parent / "bin" / "provision"
    loader = importlib.machinery.SourceFileLoader("loam_provision_rename", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    mod.AI_LABS = ai_labs
    mod.VAULT = vault
    mod.REACH = ai_labs / "_shared" / "reach.md"
    if codex_home is not None:
        mod.CODEX_HOME = codex_home
    if claude_json is not None:
        mod.CLAUDE_JSON = claude_json
    return mod


def _rerender_runtime_files(old_id: str, new_id: str, old_ws: Path, new_ws: Path,
                            config: dict, *, ai_labs: Path, vault: Path,
                            codex_home: Path | None, claude_json: Path | None,
                            update_claude_trust: bool) -> list[str]:
    """Re-render all Loam-owned runtime files in ``new_ws`` without touching
    any agent-authored file (soul.md, understanding.md, map.md, config.md,
    memory/, skills/, crons/)."""
    import loam_antigravity as agy  # noqa: PLC0415
    import loam_codex  # noqa: PLC0415
    import loam_loading  # noqa: PLC0415
    import loam_skills  # noqa: PLC0415

    updated: list[str] = []
    prov = _load_provision(ai_labs, vault, codex_home, claude_json)
    display = loam_profile.display_name(new_id, config)

    # 1. CLAUDE.md
    if (new_ws / "CLAUDE.md").is_file():
        _atomic_write_text(new_ws / "CLAUDE.md", prov.claude_md(display, new_ws))
        try:
            loam_loading.refresh_reach_import(new_ws)
        except OSError:
            pass
        updated.append("CLAUDE.md")

    # 2. .claude/settings.json & .claude/settings.local.json
    settings_json = new_ws / ".claude" / "settings.json"
    if settings_json.is_file():
        if (new_ws / ".mcp.json").is_file():
            _atomic_write_text(settings_json, json.dumps(prov.settings(new_ws), indent=2) + "\n")
        else:
            loam_profile.sync_settings_model(new_ws, config)
        updated.append(".claude/settings.json")

    settings_local = new_ws / ".claude" / "settings.local.json"
    if settings_local.is_file():
        _atomic_write_text(
            settings_local,
            json.dumps({"autoMemoryDirectory": str(new_ws / "memory")}, indent=2) + "\n")
        updated.append(".claude/settings.local.json")

    # 3. .mcp.json
    mcp_path = new_ws / ".mcp.json"
    if mcp_path.is_file():
        _atomic_write_text(
            mcp_path, json.dumps(prov.mcp_config(new_id, new_ws), indent=2) + "\n")
        updated.append(".mcp.json")

    # 4. .codex/config.toml & CODEX_HOME instructions + trust
    codex_cfg = new_ws / ".codex" / "config.toml"
    if codex_cfg.is_file():
        _atomic_write_text(codex_cfg, prov.codex_config(new_id, new_ws))
        link = new_ws / ".codex" / "skills"
        if (new_ws / "skills").is_dir() and not (
                link.is_symlink() and str(link.readlink()) == "../skills"):
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to("../skills")
        updated.append(".codex/config.toml")

    if codex_home is not None:
        instr_dir = codex_home / "instructions"
        old_instr = instr_dir / f"{old_id}.md"
        new_instr = instr_dir / f"{new_id}.md"
        had_old_instr = old_instr.is_file()
        old_instr.unlink(missing_ok=True)
        if (had_old_instr or codex_cfg.is_file()) and (new_ws / "CLAUDE.md").is_file():
            instr_dir.mkdir(parents=True, exist_ok=True)
            _atomic_write_text(new_instr, loam_codex.instructions(new_ws))
            updated.append(f"codex/instructions/{new_id}.md")

        # Update CODEX_HOME/config.toml project trust and drop stale old_ws hooks.
        home_cfg = codex_home / "config.toml"
        if home_cfg.is_file() or codex_cfg.is_file():
            try:
                data = tomllib.loads(home_cfg.read_text(encoding="utf-8")) if home_cfg.is_file() else {}
            except (OSError, ValueError):
                data = {}
            raw_hooks = ((data.get("hooks") or {}).get("state") or {})
            old_prefix = str(old_ws) + "/"
            hooks = {
                k: v["trusted_hash"] for k, v in raw_hooks.items()
                if isinstance(v, dict) and isinstance(v.get("trusted_hash"), str)
                and not k.startswith(old_prefix)
            }
            workspaces = sorted(p.parent.parent for p in ai_labs.glob("*/.codex/config.toml"))
            if workspaces or home_cfg.is_file():
                _atomic_write_text(home_cfg, loam_codex.trust_config(workspaces, hooks))
                updated.append("codex/config.toml")

    # 5. .agents/ (Antigravity)
    agy_mcp = new_ws / ".agents" / "mcp_config.json"
    if agy_mcp.is_file():
        mcp = prov.antigravity_mcp(new_id, new_ws)
        _atomic_write_text(agy_mcp, json.dumps(mcp, indent=2) + "\n")
        if (new_ws / agy.HOOKS_FILE).is_file():
            agy.sync_images_hook(new_ws, "images" in mcp["mcpServers"])
            agy.sync_loading_size_hook(new_ws)
        updated.append(".agents/mcp_config.json")
    if agy.has_instructions(new_ws) and (new_ws / "CLAUDE.md").is_file():
        written, _ = agy.sync_instructions(new_ws)
        updated.extend(str(p.relative_to(new_ws)) for p in written)
    if (new_ws / ".agents" / "skills").exists():
        loam_skills.render_view(new_ws, vault)
        updated.append(".agents/skills")

    # 6. ~/.claude.json trust (add new_ws, remove old_ws)
    if update_claude_trust and claude_json is not None:
        try:
            prov.ensure_trust(new_ws)
            prov.remove_trust(old_ws)
            updated.append(str(claude_json))
        except (SystemExit, OSError, ValueError) as exc:
            print(f"rename: claude trust update warning: {exc}", file=sys.stderr, flush=True)

    return updated


def _move_claude_sessions(old_ws: Path, new_ws: Path,
                          claude_home: Path | None) -> tuple[Path, Path, bool]:
    """Move/merge Claude Code's per-workspace project directory so ``--resume``
    and ``loam_results`` / ``loam_adopt`` / ``check-drift`` find all prior sessions."""
    import loam_adopt  # noqa: PLC0415
    projects_root = (claude_home or lc.CLAUDE_HOME) / "projects"
    old_dir = projects_root / loam_adopt.project_slug(old_ws)
    new_dir = projects_root / loam_adopt.project_slug(new_ws)
    if claude_home is None or not old_dir.is_dir() or old_dir == new_dir:
        return old_dir, new_dir, False
    moved = _move_or_merge_dir(old_dir, new_dir, git_root=None)
    return old_dir, new_dir, moved


def _update_codex_sqlite(old_ws: Path, new_ws: Path, codex_home: Path | None) -> int:
    """Best-effort update of Codex's ``threads.cwd`` column for moved workspaces.
    Codex also updates ``cwd`` itself on ``thread/resume``."""
    if codex_home is None:
        return 0
    db = codex_home / "state_5.sqlite"
    if not db.is_file():
        return 0
    try:
        con = sqlite3.connect(str(db), timeout=5.0)
        try:
            cur = con.execute("UPDATE threads SET cwd = ? WHERE cwd = ?",
                              (str(new_ws), str(old_ws)))
            con.commit()
            return cur.rowcount or 0
        finally:
            con.close()
    except (OSError, sqlite3.Error):
        return 0


def _migrate_schedules(old_id: str, new_id: str, *, ai_labs: Path,
                       systemd=None) -> dict:
    """Move applied snapshots, disarm/remove old systemd units, reconcile new ones."""
    import loam_cron  # noqa: PLC0415
    import loam_schedule as ls  # noqa: PLC0415

    sd = systemd or ls.Systemd()
    # 1. Move applied schedule snapshots (so the reconciler sees them as already accepted).
    old_applied = ls.STATE / "applied" / old_id
    new_applied = ls.STATE / "applied" / new_id
    if old_applied.is_dir():
        _move_or_merge_dir(old_applied, new_applied, git_root=None)

    # 2. Disarm and remove all old units (reflection wake + crons).
    removed_units: list[str] = []
    unit_dir = sd.unit_dir
    if unit_dir.is_dir():
        # Reflection wake units: loam-wake-<old_id>.{timer,service}
        for suffix in (".timer", ".service"):
            name = f"{loam_cron.WAKE_PREFIX}{old_id}{suffix}"
            p = unit_dir / name
            if p.exists():
                if suffix == ".timer":
                    sd.disarm(name)
                p.unlink(missing_ok=True)
                removed_units.append(name)

        # Named cron units: loam-cron-<old_id>-*.{timer,service}
        known = sorted({old_id, *loam_agents.every(ai_labs)})
        cron_units = sorted(unit_dir.glob(f"{loam_cron.UNIT_PREFIX}{old_id}-*"))
        # Disarm timers before unlinking services.
        cron_units.sort(key=lambda u: (0 if u.suffix == ".timer" else 1, u.name))
        for unit in cron_units:
            if unit.suffix not in (".service", ".timer"):
                continue
            try:
                owner = loam_cron.split_unit(unit.name, known)[0]
            except loam_cron.CronError:
                continue
            if owner != old_id:
                continue
            if unit.suffix == ".timer":
                sd.disarm(unit.name)
            unit.unlink(missing_ok=True)
            removed_units.append(unit.name)

    # 3. Reconcile new_id ONLY if awake (an archived agent keeps its schedule
    #    files on disk, but must never have timers installed or armed).
    actions = []
    if not loam_agents.is_archived(new_id, ai_labs):
        actions = ls._reconcile([new_id], now=None, systemd=sd)
    if removed_units and not actions:
        sd.reload()
        if ls.WRITE_PAGE:
            loam_cron.write_schedule_page()
    elif ls.WRITE_PAGE:
        # Refresh schedule page even when only display/name changed.
        loam_cron.write_schedule_page()

    return {"removed": removed_units, "actions": actions}


def _merge_jsonl(src: Path, dst: Path) -> None:
    if not src.is_file():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        os.rename(src, dst)
        return
    existing = dst.read_text(encoding="utf-8")
    incoming = src.read_text(encoding="utf-8")
    combined = incoming + ("" if not incoming or incoming.endswith("\n") else "\n") + existing
    _atomic_write_text(dst, combined)
    src.unlink(missing_ok=True)


def _migrate_wake_and_logs(old_id: str, new_id: str, *,
                           wake_state: Path | None, state_dir: Path | None,
                           logs_dir: Path | None) -> list[str]:
    moved: list[str] = []
    if wake_state is not None and wake_state.is_dir():
        for suffix in (".json", ".vault-changes.txt"):
            src = wake_state / f"{old_id}{suffix}"
            dst = wake_state / f"{new_id}{suffix}"
            if src.is_file():
                os.replace(src, dst)
                moved.append(dst.name)
    if state_dir is not None:
        notice_dir = state_dir / "lane-notice"
        src = notice_dir / f"{old_id}.json"
        if src.is_file():
            os.replace(src, notice_dir / f"{new_id}.json")
            moved.append(f"lane-notice/{new_id}.json")
    if logs_dir is not None and logs_dir.is_dir():
        src = logs_dir / f"wake-{old_id}.jsonl"
        dst = logs_dir / f"wake-{new_id}.jsonl"
        if src.is_file():
            _merge_jsonl(src, dst)
            moved.append(dst.name)
    return moved


def _migrate_broker_sessions(old_id: str, new_id: str, old_ws: Path, new_ws: Path,
                             session_dir: Path | None,
                             old_claude_dir: Path, new_claude_dir: Path) -> int:
    if session_dir is None or not session_dir.is_dir():
        return 0
    old_ws_s, new_ws_s = str(old_ws), str(new_ws)
    old_cl_prefix = str(old_claude_dir) + "/"
    new_cl_prefix = str(new_claude_dir) + "/"
    changed = 0
    for path in sorted(session_dir.glob("*.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict) or rec.get("agent") != old_id:
            continue
        rec["agent"] = new_id
        if rec.get("workspace") == old_ws_s:
            rec["workspace"] = new_ws_s
        src = rec.get("source")
        if isinstance(src, str) and src.startswith(old_cl_prefix):
            rec["source"] = new_cl_prefix + src[len(old_cl_prefix):]
        _atomic_write_json(path, rec)
        changed += 1
    return changed


def _migrate_thread_state(old_id: str, new_id: str) -> dict:
    """Update thread runtime state files under ``threads.STATE``.
    Never reads or modifies vault thread records (``15_CONVERSATIONS/*.md``)."""
    import loam_thread_inputs as thread_inputs  # noqa: PLC0415
    import loam_threads as threads  # noqa: PLC0415

    stats = {"sessions": 0, "handoffs": 0, "marks": 0, "system_turns": 0, "inputs": 0}
    with threads.locked(threads.VAULT):
        # 1. sessions.json
        sessions = threads.load_state("sessions.json")
        if isinstance(sessions, dict):
            dirty = False
            for tid, members in sessions.items():
                if isinstance(members, dict) and old_id in members:
                    row = members.pop(old_id)
                    if new_id not in members:
                        members[new_id] = row
                    dirty = True
                    stats["sessions"] += 1
            if dirty:
                threads.save_state("sessions.json", sessions)

        # 2. handoffs.json
        handoffs = threads.load_state("handoffs.json")
        if isinstance(handoffs, dict):
            dirty = False
            for tid, members in handoffs.items():
                if isinstance(members, dict) and old_id in members:
                    row = members.pop(old_id)
                    if new_id not in members:
                        members[new_id] = row
                    dirty = True
                    stats["handoffs"] += 1
            if dirty:
                threads.save_state("handoffs.json", handoffs)

        # 3. marks.json
        marks_path = threads.STATE / "marks.json"
        if marks_path.is_file():
            try:
                marks = json.loads(marks_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                marks = {}
            if isinstance(marks, dict):
                dirty = False
                for tid, per in marks.items():
                    if isinstance(per, dict) and old_id in per:
                        row = per.pop(old_id)
                        if isinstance(row, dict):
                            row = {**row, "persona": new_id}
                        if new_id not in per:
                            per[new_id] = row
                        dirty = True
                        stats["marks"] += 1
                if dirty:
                    _atomic_write_text(marks_path, json.dumps(marks))

        # 4. system-turns.json
        st_path = threads.STATE / "system-turns.json"
        if st_path.is_file():
            try:
                st = json.loads(st_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                st = {}
            lines = st.get("lines") if isinstance(st, dict) else None
            if isinstance(lines, dict):
                new_lines: dict = {}
                dirty = False
                needle = f" {old_id} "
                repl = f" {new_id} "
                for key, ts in lines.items():
                    if isinstance(key, str) and needle in key:
                        tid, _, rest = key.partition(" ")
                        who, sep, msg = rest.partition(" ")
                        if who == old_id and sep:
                            new_key = f"{tid}{repl}{msg}"
                            new_lines[new_key] = max(ts, new_lines.get(new_key, 0))
                            dirty = True
                            stats["system_turns"] += 1
                            continue
                    new_lines[key] = ts
                if dirty:
                    st["lines"] = new_lines
                    _atomic_write_text(st_path, json.dumps(st))

        # 5. inputs/<tid>/<old_id>.json -> <new_id>.json
        inputs_root = thread_inputs.root()
        if inputs_root.is_dir():
            for tid_dir in sorted(inputs_root.iterdir()):
                if not tid_dir.is_dir():
                    continue
                old_file = tid_dir / f"{old_id}.json"
                if not old_file.is_file():
                    continue
                new_file = tid_dir / f"{new_id}.json"
                if not new_file.exists():
                    os.replace(old_file, new_file)
                else:
                    try:
                        a = json.loads(old_file.read_text(encoding="utf-8"))
                        b = json.loads(new_file.read_text(encoding="utf-8"))
                        if isinstance(a, list) and isinstance(b, list):
                            _atomic_write_text(new_file, json.dumps(a + b))
                    except (OSError, ValueError):
                        pass
                    old_file.unlink(missing_ok=True)
                stats["inputs"] += 1

        threads.clear_cache()

    return stats


def _migrate_plugins(old_id: str, new_id: str, plugins_root: Path | None) -> dict:
    """Update per-agent plugin settings (``_settings/<pid>/agents.json``) and
    Read aloud's ``voices`` string if present."""
    import loam_plugins  # noqa: PLC0415

    res = {"agent_settings": [], "read_aloud_voices": False}
    root = plugins_root or loam_plugins.yours_root()
    settings_root = root / loam_plugins.SETTINGS_DIR
    if not settings_root.is_dir():
        return res

    with loam_plugins.locked():
        for pdir in sorted(settings_root.iterdir()):
            if not pdir.is_dir():
                continue
            ap = pdir / loam_plugins.AGENTS_FILE
            if ap.is_file():
                try:
                    data = json.loads(ap.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    data = None
                agents = data.get("agents") if isinstance(data, dict) else None
                if isinstance(agents, dict) and old_id in agents:
                    val = agents.pop(old_id)
                    if new_id not in agents:
                        agents[new_id] = val
                    elif isinstance(val, dict) and isinstance(agents[new_id], dict):
                        agents[new_id] = {**val, **agents[new_id]}
                    _atomic_write_json(ap, data)
                    res["agent_settings"].append(pdir.name)

        # Legacy/advanced read-aloud voices string: "ada=Iapetus:...;iris=Aoede:..."
        ra_values = settings_root / "read-aloud" / "values.json"
        if ra_values.is_file():
            try:
                vdata = json.loads(ra_values.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                vdata = None
            voices = vdata.get("voices") if isinstance(vdata, dict) else None
            if isinstance(voices, str) and voices.strip():
                parts = [p.strip() for p in voices.split(";") if p.strip()]
                has_new = any(p.partition("=")[0].strip().lower() == new_id for p in parts)
                changed = False
                rebuilt = []
                for part in parts:
                    key, eq, rest = part.partition("=")
                    if eq and key.strip().lower() == old_id:
                        if not has_new:
                            rebuilt.append(f"{new_id}={rest}")
                        changed = True
                    else:
                        rebuilt.append(part)
                if changed:
                    vdata["voices"] = ";".join(rebuilt)
                    _atomic_write_json(ra_values, vdata)
                    res["read_aloud_voices"] = True

    return res


def _migrate_garden(old_id: str, new_id: str, garden_file: Path | None) -> bool:
    """Carry over pending Garden ideas and previous-snapshot tracking so Garden
    neither drops pending ideas nor treats the renamed agent as brand new."""
    if garden_file is None or not garden_file.is_file():
        return False
    try:
        data = json.loads(garden_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    changed = False
    prev = data.get("previous")
    if isinstance(prev, dict):
        for bucket in ("agent", "bed"):
            mapping = prev.get(bucket)
            if isinstance(mapping, dict) and old_id in mapping:
                val = mapping.pop(old_id)
                mapping.setdefault(new_id, val)
                changed = True
    ideas = data.get("ideas")
    if isinstance(ideas, dict):
        for idea in ideas.values():
            if isinstance(idea, dict) and idea.get("agent") == old_id:
                idea["agent"] = new_id
                changed = True
    if changed:
        _atomic_write_json(garden_file, data)
    return changed


def _migrate_sites(old_id: str, new_id: str, old_ws: Path, new_ws: Path,
                   sites_dir: Path | None, *, deploy_regen=None) -> list[str]:
    """Update agent-owned sites: ``owner``, ``root`` under the moved workspace,
    and rename a site literally named ``<old_id>.toml`` to ``<new_id>.toml``
    when the new site name is free."""
    if sites_dir is None or not sites_dir.is_dir():
        return []
    import loam_deploy as dep  # noqa: PLC0415

    old_ws_s, new_ws_s = str(old_ws), str(new_ws)
    changed_sites: list[str] = []

    def toml_val(value) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, dt.date)):
            return value.isoformat() if isinstance(value, dt.date) else str(value)
        return json.dumps(str(value), ensure_ascii=False)

    def write_site(name: str, rec: dict) -> None:
        try:
            dom = dep.domain()
        except Exception:  # noqa: BLE001
            dom = "loam"
        lines = [f"# {name}.{dom} -- updated by agent rename; see `bin/site --help`."]
        lines += [f"{k} = {toml_val(v)}" for k, v in rec.items() if v is not None]
        _atomic_write_text(sites_dir / f"{name}.toml", "\n".join(lines) + "\n")

    # Compute names taken by deployed apps / reserved hosts so we never create a collision.
    taken_hosts: set[str] = set()
    try:
        cfg = dep.config()
        taken_hosts |= {cfg["auth_host"], dep.SECOND_FACTOR_HOST}
        for app in dep.records():
            taken_hosts |= {app.name, app.host}
            if app.content_host:
                taken_hosts.add(app.content_host)
    except Exception:  # noqa: BLE001
        pass

    for path in sorted(sites_dir.glob("*.toml")):
        name = path.stem
        try:
            rec = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            continue
        if not isinstance(rec, dict):
            continue
        owner_match = rec.get("owner") == old_id
        root = rec.get("root")
        root_under_old = (
            isinstance(root, str)
            and (root == old_ws_s or root.startswith(old_ws_s + "/"))
        )
        if not owner_match and not root_under_old:
            continue

        new_rec = dict(rec)
        if owner_match:
            new_rec["owner"] = new_id
        if root_under_old:
            new_rec["root"] = new_ws_s + root[len(old_ws_s):]

        target_name = name
        if (owner_match and name == old_id and dep.NAME_RE.match(new_id)
                and new_id not in taken_hosts
                and not (sites_dir / f"{new_id}.toml").exists()):
            target_name = new_id
            # If the site's static root was the default <ws>/sites/<old_id>, move it too.
            default_old_root = str(new_ws / "sites" / old_id)
            default_new_root = new_ws / "sites" / new_id
            if new_rec.get("root") == default_old_root and Path(default_old_root).is_dir() and not default_new_root.exists():
                os.rename(default_old_root, default_new_root)
                new_rec["root"] = str(default_new_root)

        write_site(target_name, new_rec)
        if target_name != name:
            path.unlink(missing_ok=True)
            changed_sites.append(f"{name}->{target_name}")
        else:
            changed_sites.append(name)

    if changed_sites:
        if deploy_regen is not None:
            deploy_regen()
        else:
            gen_deploy = Path(__file__).resolve().parent.parent / "bin" / "gen-deploy"
            if gen_deploy.is_file() and _same_path(sites_dir, dep.SITES_DIR):
                import loam_lock  # noqa: PLC0415
                argv = ([str(gen_deploy)] if os.geteuid() == 0
                        else ["sudo", "-n", f"--preserve-env={loam_lock.LIVE_HELD_VAR}",
                              str(gen_deploy)])
                done = subprocess.run(argv, capture_output=True, text=True, check=False)
                if done.returncode != 0:
                    print(f"rename: gen-deploy warning: {(done.stderr or done.stdout).strip()[-400:]}",
                          file=sys.stderr, flush=True)
    return changed_sites


def _migrate_registries(old_id: str, new_id: str, old_ws: Path, new_ws: Path, *,
                        plugins_root: Path | None, garden_file: Path | None,
                        sites_dir: Path | None, reviewer_state: Path | None,
                        waiters_dir: Path | None, recent_db: Path | None,
                        deploy_regen=None) -> dict:
    import loam_engineer as eng  # noqa: PLC0415
    import loam_starter  # noqa: PLC0415

    summary: dict = {}

    # 1. starter.json
    sp = loam_starter.marker_path()
    if sp.is_file():
        try:
            marker = json.loads(sp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            marker = None
        if isinstance(marker, dict) and marker.get("agent") == old_id:
            marker["agent"] = new_id
            _atomic_write_json(sp, marker)
            summary["starter"] = True

    # 2. state/broker/settings.json (system.persona)
    hsp = eng.host_settings_path()
    if hsp.is_file():
        try:
            hdata = json.loads(hsp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            hdata = None
        sys_cfg = hdata.get("system") if isinstance(hdata, dict) else None
        if isinstance(sys_cfg, dict) and sys_cfg.get("persona") == old_id:
            sys_cfg["persona"] = new_id
            _atomic_write_json(hsp, hdata)
            summary["system_persona"] = True

    # 3. Engineer waiters
    if waiters_dir is not None and waiters_dir.is_dir():
        waiters_changed = 0
        for p in sorted(waiters_dir.glob("*.json")):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(rec, dict) and rec.get("persona") == old_id:
                rec["persona"] = new_id
                _atomic_write_json(p, rec)
                waiters_changed += 1
        if waiters_changed:
            summary["waiters"] = waiters_changed

    # 4. Per-agent plugin settings & read-aloud voices
    summary["plugins"] = _migrate_plugins(old_id, new_id, plugins_root)

    # 5. Garden plugin state
    if _migrate_garden(old_id, new_id, garden_file):
        summary["garden"] = True

    # 6. Recent files attribution DB
    if recent_db is not None and recent_db.is_file():
        try:
            con = sqlite3.connect(str(recent_db), timeout=5.0)
            try:
                cur = con.execute(
                    "UPDATE writes SET actor = ? WHERE kind = 'agent' AND actor = ?",
                    (new_id, old_id))
                con.commit()
                if cur.rowcount:
                    summary["recent_writes"] = cur.rowcount
            finally:
                con.close()
        except (OSError, sqlite3.Error):
            pass

    # 7. Reviewer memory page & delivered notes archive
    if reviewer_state is not None and reviewer_state.is_dir():
        mem_old = reviewer_state / "memory" / f"{old_id}.md"
        mem_new = reviewer_state / "memory" / f"{new_id}.md"
        if mem_old.is_file() and not mem_new.exists():
            os.rename(mem_old, mem_new)
            summary["reviewer_memory"] = True
        notes_old = reviewer_state / "notes" / old_id
        notes_new = reviewer_state / "notes" / new_id
        if notes_old.is_dir():
            _move_or_merge_dir(notes_old, notes_new, git_root=None)
            summary["reviewer_notes"] = True

    # 8. Agent-owned sites
    sites_changed = _migrate_sites(old_id, new_id, old_ws, new_ws, sites_dir,
                                   deploy_regen=deploy_regen)
    if sites_changed:
        summary["sites"] = sites_changed

    return summary


def rename_agent(old_id: str, new_display, *, by: str | None = None,
                 busy=None, systemd=None, deploy_regen=None,
                 ai_labs: Path | None = None, vault: Path | None = None,
                 update_claude_trust: bool = True,
                 claude_home: Path | None = None,
                 claude_json: Path | None = None,
                 codex_home: Path | None = None,
                 garden_file: Path | None = None,
                 plugins_root: Path | None = None,
                 sites_dir: Path | None = None,
                 reviewer_state: Path | None = None,
                 waiters_dir: Path | None = None,
                 wake_state: Path | None = None,
                 logs_dir: Path | None = None,
                 state_dir: Path | None = None,
                 session_dir: Path | None = None,
                 recent_db: Path | None = None) -> dict:
    """Rename agent ``old_id`` to ``new_display`` everywhere.

    When ``slug(new_display) == old_id`` (or ``new_display is None``), updates
    only ``display`` and refreshes rendered instructions.
    When ``slug(new_display) != old_id``, refuses if the agent is busy, then
    moves the workspace folder, updates ``agent.json`` (recording ``old_id`` in
    ``former_ids``), re-renders runtime files, moves provider stores, commons,
    schedules/units, wake/log state, broker sessions, thread runtime state, and
    registries. Never touches ``15_CONVERSATIONS/*.md`` or agent-authored files.
    """
    import loam_activity as activity  # noqa: PLC0415
    import loam_broker as cfg  # noqa: PLC0415
    import loam_codex  # noqa: PLC0415
    import loam_cron  # noqa: PLC0415
    import loam_deploy as dep  # noqa: PLC0415
    import loam_lock  # noqa: PLC0415
    import loam_recent  # noqa: PLC0415
    import loam_schedule as ls  # noqa: PLC0415
    import loam_thread_inputs as thread_inputs  # noqa: PLC0415
    import loam_threads as threads  # noqa: PLC0415
    import loam_waiters  # noqa: PLC0415

    labs = ai_labs or _default_ai_labs()
    vroot = vault or _default_vault()
    live_box = _is_live_box(labs)

    # Resolve optional paths safely so tests running on a scratch tree never
    # touch the host install's live files unless explicitly pointed there,
    # while tests under a scratch LOAM_CONFIG exercise every path automatically.
    live_paths = lc._install_live_paths()
    live_state = live_paths["state"]
    live_logs = live_paths["logs"]

    def _pick(explicit: Path | None, candidate: Path, *,
              live_root: Path = live_state, env_var: str | None = None) -> Path | None:
        if explicit is not None:
            return explicit
        if env_var and os.environ.get(env_var):
            return Path(os.environ[env_var])
        if live_box or not _under(candidate, live_root):
            return candidate
        return None

    eff_claude_home = _pick(claude_home, lc.CLAUDE_HOME, live_root=live_paths["home"])
    if claude_json is not None:
        eff_claude_json: Path | None = claude_json
    elif os.environ.get("LOAM_CLAUDE_JSON"):
        eff_claude_json = Path(os.environ["LOAM_CLAUDE_JSON"])
    elif live_box:
        eff_claude_json = lc.HOME / ".claude.json"
    else:
        eff_claude_json = None

    eff_codex_home = _pick(codex_home, loam_codex.CODEX_HOME)
    eff_garden = _pick(garden_file,
                       lc.STATE / "plugins" / "private" / "garden" / "garden.json",
                       env_var="LOAM_GARDEN_STATE")
    eff_sites = _pick(sites_dir, dep.SITES_DIR, live_root=lc.ROOT)
    eff_reviewer = _pick(reviewer_state, lc.REVIEWER_STATE, env_var="LOAM_REVIEWER_STATE")
    eff_waiters = _pick(waiters_dir, loam_waiters.STATE, env_var="LOAM_WAITERS_DIR")
    eff_wake = _pick(wake_state, ls.WAKE_STATE)
    eff_logs = _pick(logs_dir, activity.WAKE_LOGS, live_root=live_logs)
    eff_state = _pick(state_dir, lc.STATE)
    eff_sessions = _pick(session_dir, cfg.SESSION_DIR)
    eff_recent_db = _pick(recent_db, loam_recent.db_path())

    with loam_agents.locked():
        with ls.locked():
            new_id, clean_display, id_changed = validate_rename(
                old_id, new_display, ai_labs=labs)
            old_ws = labs / old_id
            config = json.loads((old_ws / "agent.json").read_text(encoding="utf-8"))
            if not isinstance(config, dict):
                raise RenameError(f"{old_id}'s agent.json is not an object", 500)

            if not id_changed:
                new_cfg = dict(config)
                new_cfg["display"] = clean_display
                if new_cfg != config:
                    _atomic_write_text(old_ws / "agent.json",
                                       loam_profile.render_agent(new_cfg))
                    loam_agents.clear_cache()
                loam_profile.sync_display_instructions(old_id, old_ws, new_cfg)
                if ls.WRITE_PAGE:
                    try:
                        loam_cron.write_schedule_page()
                    except Exception:  # noqa: BLE001
                        pass
                return {"old_id": old_id, "new_id": old_id,
                        "display": clean_display, "moved": False,
                        "config": new_cfg, "summary": {}}

            # -- Busy guard (refuse 409; never corrupt a live run) -------------
            shown = loam_profile.display_name(old_id, config)
            if busy is not None:
                why = busy(old_id)
                if why:
                    raise RenameError(
                        f"{shown} is {why}; wait for it to finish and try again", 409)
            try:
                queued = [tid for tid, p, items in thread_inputs.pending()
                          if p == old_id and items]
            except (OSError, ValueError):
                queued = []
            if queued:
                raise RenameError(
                    f"{shown} has queued messages waiting in {', '.join(queued)}; "
                    f"wait for them to finish and try again", 409)

            if wake_running_busy(old_id, eff_wake):
                raise RenameError(
                    f"{shown} is running a wake or cron right now; "
                    f"wait for it to finish and try again", 409)

            # Hold non-blocking per-agent run locks while moving the folder so
            # no wake or cron can start concurrently behind our back.
            me = {"agent": old_id, "lane": "rename", "pid": os.getpid(),
                  "since": loam_lock.now_iso()}
            old_lock_path = loam_lock.LOCK_DIR / f"agent-{old_id}.lock"
            old_handle = loam_lock._try_take(old_lock_path, me)
            if old_handle is None:
                blocker = loam_lock._read_holder(old_lock_path)
                raise RenameError(
                    f"{shown} is busy ({loam_lock.describe(blocker)}); "
                    f"wait for it to finish and try again", 409)
            new_lock_path = loam_lock.LOCK_DIR / f"agent-{new_id}.lock"
            new_handle = loam_lock._try_take(new_lock_path, {**me, "agent": new_id})
            if new_handle is None:
                loam_lock._release(old_handle)
                raise RenameError(
                    f"another run is holding {new_id}'s lock; try again in a moment", 409)

            try:
                new_ws = labs / new_id
                # 1. Move workspace folder (git mv when tracked so git log --follow works)
                _move_or_merge_dir(old_ws, new_ws, git_root=labs)

                # 2. Update agent.json (preserve all user settings + record former_ids)
                new_cfg = dict(config)
                new_cfg["name"] = new_id
                new_cfg["display"] = clean_display
                new_cfg["workspace"] = str(new_ws)
                former = loam_agents.former_ids_of(new_cfg)
                if old_id not in former:
                    former.append(old_id)
                former = [x for x in former if x != new_id]
                if former:
                    new_cfg[loam_agents.FORMER_IDS_KEY] = former
                else:
                    new_cfg.pop(loam_agents.FORMER_IDS_KEY, None)
                _atomic_write_text(new_ws / "agent.json",
                                   loam_profile.render_agent(new_cfg))
                loam_agents.clear_cache()
                threads.clear_cache()

                # 3. Re-render runtime files (CLAUDE.md, .mcp.json, .codex, .agents, trust)
                rendered = _rerender_runtime_files(
                    old_id, new_id, old_ws, new_ws, new_cfg,
                    ai_labs=labs, vault=vroot, codex_home=eff_codex_home,
                    claude_json=eff_claude_json,
                    update_claude_trust=update_claude_trust)

                # 4. Move Claude Code project session dir & update Codex sqlite
                old_cl_dir, new_cl_dir, claude_moved = _move_claude_sessions(
                    old_ws, new_ws, eff_claude_home)
                codex_rows = _update_codex_sqlite(old_ws, new_ws, eff_codex_home)

                # 5. Move commons inbox (ai-labs/commons/<old> -> <new>)
                commons_moved = False
                old_commons = labs / "commons" / old_id
                new_commons = labs / "commons" / new_id
                if old_commons.is_dir():
                    commons_moved = _move_or_merge_dir(old_commons, new_commons, git_root=labs)

                # 6. Schedules & systemd units
                sched_info = _migrate_schedules(old_id, new_id, ai_labs=labs,
                                                systemd=systemd)

                # 7. Wake state, lane-notice, wake logs
                wake_moved = _migrate_wake_and_logs(
                    old_id, new_id, wake_state=eff_wake,
                    state_dir=eff_state, logs_dir=eff_logs)

                # 8. Broker session records
                sessions_updated = _migrate_broker_sessions(
                    old_id, new_id, old_ws, new_ws, eff_sessions,
                    old_cl_dir, new_cl_dir)

                # 9. Thread runtime state (vault records untouched!)
                thread_stats = _migrate_thread_state(old_id, new_id)

                # 10. Registries (starter, system.persona, waiters, plugins, garden,
                #     recent writes, reviewer memory/notes, sites)
                reg_stats = _migrate_registries(
                    old_id, new_id, old_ws, new_ws,
                    plugins_root=plugins_root, garden_file=eff_garden,
                    sites_dir=eff_sites, reviewer_state=eff_reviewer,
                    waiters_dir=eff_waiters, recent_db=eff_recent_db,
                    deploy_regen=deploy_regen)

                loam_agents.clear_cache()
                threads.clear_cache()

                event = loam_agents.log({
                    "kind": "renamed", "from": old_id, "to": new_id,
                    "display": clean_display, "by": by or lc.OWNER_ID,
                })

                return {
                    "old_id": old_id,
                    "new_id": new_id,
                    "display": clean_display,
                    "moved": True,
                    "config": new_cfg,
                    "event": event,
                    "summary": {
                        "rendered": rendered,
                        "claude_sessions_moved": claude_moved,
                        "codex_threads_updated": codex_rows,
                        "commons_moved": commons_moved,
                        "schedules": sched_info,
                        "wake_and_logs": wake_moved,
                        "broker_sessions_updated": sessions_updated,
                        "thread_state": thread_stats,
                        "registries": reg_stats,
                    },
                }
            finally:
                loam_lock._release(new_handle)
                loam_lock._release(old_handle)


# ---------------------------------------------------------------------------
# Migration for already-renamed agents (e.g. name: "ines", display: "Astrid")
# ---------------------------------------------------------------------------

def needs_migration(ai_labs: Path | None = None) -> list[dict]:
    """Return every agent whose stored ``display`` maps to a different valid
    folder id than its current workspace name (e.g. ``ines`` with display
    ``Astrid``)."""
    labs = ai_labs or _default_ai_labs()
    out: list[dict] = []
    for agent in loam_agents.every(labs):
        cfg = loam_agents.card(agent, labs)
        raw_display = cfg.get("display")
        if not isinstance(raw_display, str) or not raw_display.strip():
            continue
        clean = " ".join(raw_display.split())
        target = loam_agents.slug(clean)
        if not target or target == agent:
            continue
        out.append({
            "agent": agent,
            "display": clean,
            "target": target,
            "archived": bool(cfg.get(loam_agents.ARCHIVE_KEY)),
        })
    return out


def _call_live_broker(old_id: str, display: str) -> tuple[bool, str]:
    """Ask a running broker to perform the rename so resident idle sessions and
    in-memory ThreadEngine state stay synchronized. Returns ``(handled, detail)``;
    ``handled=False`` means the broker is unreachable (run offline instead)."""
    import urllib.error  # noqa: PLC0415
    import urllib.parse  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415
    import loam_broker as cfg  # noqa: PLC0415

    token_path = Path(os.environ.get("LOAM_BROKER_TOKEN_FILE") or cfg.TOKEN_PATH)
    if not token_path.is_file():
        return False, "no broker token"
    base = (os.environ.get("LOAM_BROKER_URL")
            or f"http://{cfg.BIND_ADDR}:{cfg.PORT}").rstrip("/")
    try:
        token = token_path.read_text(encoding="utf-8").strip()
    except OSError:
        return False, "unreadable broker token"
    if not token:
        return False, "empty broker token"

    req = urllib.request.Request(
        f"{base}/v1/agents/{urllib.parse.quote(old_id, safe='')}",
        method="PATCH",
        data=json.dumps({"display": display}).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json",
                 "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read() or b"{}")
            got_id = ((data.get("agent") or {}).get("id")
                      or (data.get("identity") or {}).get("id") or "")
            if got_id == old_id:
                return False, "broker did not support folder rename yet"
            return True, f"{old_id} -> {got_id or display}"
    except urllib.error.HTTPError as exc:
        try:
            err = json.loads(exc.read()).get("error") or f"HTTP {exc.code}"
        except (ValueError, AttributeError):
            err = f"HTTP {exc.code}"
        raise RenameError(err, exc.code) from None
    except (urllib.error.URLError, OSError):
        return False, "broker not reachable"


def migrate_renamed_agents(*, dry_run: bool = False, use_broker: bool = True,
                           ai_labs: Path | None = None,
                           **rename_kwargs) -> dict:
    """Bring any previously renamed agents (``slug(display) != id``) into line.

    Idempotent: running twice is a clean no-op on the second run. Uses the live
    broker API when reachable so resident idle sessions and in-memory state are
    coordinated cleanly; falls back to direct on-disk rename when offline.
    """
    labs = ai_labs or _default_ai_labs()
    candidates = needs_migration(labs)
    migrated: list[dict] = []
    skipped: list[dict] = []

    for item in candidates:
        old_id, display, target = item["agent"], item["display"], item["target"]
        try:
            new_id, clean, changed = validate_rename(old_id, display, ai_labs=labs)
        except RenameError as exc:
            skipped.append({**item, "reason": exc.message, "status": exc.status})
            continue
        if not changed:
            continue
        if dry_run:
            migrated.append({**item, "target": new_id, "dry_run": True})
            continue

        via = "disk"
        try:
            handled = False
            if use_broker and _is_live_box(labs):
                handled, _ = _call_live_broker(old_id, clean)
                if handled:
                    via = "broker"
            if not handled:
                rename_agent(old_id, clean, by="loam", ai_labs=labs, **rename_kwargs)
            migrated.append({**item, "target": new_id, "via": via})
        except RenameError as exc:
            skipped.append({**item, "reason": exc.message, "status": exc.status})

    return {
        "checked": len(loam_agents.every(labs)),
        "migrated": migrated,
        "skipped": skipped,
    }
