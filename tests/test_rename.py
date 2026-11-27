#!/usr/bin/env python3
"""End-to-end tests for full agent rename and already-renamed migration.

Covers every contract of lib/loam_rename.py, bin/session-broker PATCH /v1/agents,
bin/provision --migrate-renamed-agents, alias resolution across untouched vault
thread records, resumable sessions across folder moves, wake/cron unit moves,
drift detection of stray old units, busy/collision refusals, starter agent
rename, and carry-over of all per-agent registries.

Run via: python3 tests/run-all test_rename.py
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import loam_activity as activity  # noqa: E402
import loam_adopt  # noqa: E402
import loam_agents  # noqa: E402
import loam_antigravity as agy  # noqa: E402
import loam_attention  # noqa: E402
import loam_broker as cfg  # noqa: E402
import loam_codex  # noqa: E402
import loam_config as lc  # noqa: E402
import loam_cron  # noqa: E402
import loam_deploy as dep  # noqa: E402
import loam_engineer as eng  # noqa: E402
import loam_feedback  # noqa: E402
import loam_profile as profile  # noqa: E402
import loam_recent  # noqa: E402
import loam_rename  # noqa: E402
import loam_schedule as ls  # noqa: E402
import loam_skills  # noqa: E402
import loam_thread_engine as engine_mod  # noqa: E402
import loam_thread_inputs as thread_inputs  # noqa: E402
import loam_threads as threads  # noqa: E402
import loam_waiters  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="loam-rename-test-"))
LABS = TMP / "ai-labs"
VAULT = TMP / "vault"
STATE = TMP / "state"
LOGS = STATE / "logs"
PLUGINS = TMP / "plugins"
CLAUDE_HOME = TMP / ".claude"
CLAUDE_JSON = TMP / ".claude.json"
CODEX_HOME = STATE / "broker" / "codex"
UNIT_DIR = TMP / "systemd-user"
SITES_DIR = TMP / "sites"
GARDEN_FILE = STATE / "plugins" / "private" / "garden" / "garden.json"
REVIEWER_STATE = STATE / "reviewer"
WAITERS_DIR = STATE / "engineer-waiters"

for d in (LABS, LABS / "_shared", LABS / "commons", VAULT / "15_CONVERSATIONS",
          VAULT / "99_SYSTEM" / "Loam" / "skills", VAULT / "99_SYSTEM" / "skills",
          STATE / "broker" / "sessions", LOGS, PLUGINS / "_settings",
          CLAUDE_HOME / "projects", CODEX_HOME / "instructions",
          UNIT_DIR, SITES_DIR, GARDEN_FILE.parent,
          REVIEWER_STATE / "memory", REVIEWER_STATE / "notes", WAITERS_DIR):
    d.mkdir(parents=True, exist_ok=True)
(LABS / "_shared" / "reach.md").write_text("# Reach\n", encoding="utf-8")
CLAUDE_JSON.write_text(json.dumps({"projects": {}}, indent=2) + "\n", encoding="utf-8")

# Initialize ai-labs as a real git repo so git mv + git log --follow are verified.
subprocess.run(["git", "-C", str(LABS), "init", "-q"], check=True)
subprocess.run(["git", "-C", str(LABS), "config", "user.name", "Test"], check=True)
subprocess.run(["git", "-C", str(LABS), "config", "user.email", "test@example.com"], check=True)

# Point all module globals at the isolated scratch box.
lc.WORKSPACES = LABS
lc.VAULT = VAULT
lc.STATE = STATE
lc.LOGS = LOGS
lc.PLUGINS = PLUGINS
lc.CLAUDE_HOME = CLAUDE_HOME
lc.USER_UNIT_DIR = UNIT_DIR
lc.REVIEWER_STATE = REVIEWER_STATE
os.environ["LOAM_CLAUDE_JSON"] = str(CLAUDE_JSON)
os.environ["LOAM_GARDEN_STATE"] = str(GARDEN_FILE)
os.environ["LOAM_REVIEWER_STATE"] = str(REVIEWER_STATE)
os.environ["LOAM_WAITERS_DIR"] = str(WAITERS_DIR)
os.environ["LOAM_PLUGINS_DIR"] = str(PLUGINS)
os.environ["LOAM_HOST_SETTINGS"] = str(STATE / "broker" / "settings.json")

cfg.AI_LABS = LABS
cfg.SESSION_DIR = STATE / "broker" / "sessions"
cfg.TOKEN = "test-token"
loam_cron.AI_LABS = LABS
loam_cron.UNIT_DIR = UNIT_DIR
ls.STATE = STATE / "schedules"
ls.WAKE_STATE = STATE / "wake"
ls.WRITE_PAGE = False
loam_codex.CODEX_HOME = CODEX_HOME
loam_adopt.PROJECTS = CLAUDE_HOME / "projects"
threads.VAULT = VAULT
threads.AI_LABS = LABS
threads.STATE = STATE / "broker" / "threads"
thread_inputs.STATE = threads.STATE / "inputs"
loam_attention.STATE = STATE / "attention"
loam_waiters.STATE = WAITERS_DIR
dep.SITES_DIR = SITES_DIR
activity.WAKE_LOGS = LOGS
loam_skills.AI_LABS = LABS
loam_skills.VAULT = VAULT
loam_skills.CLAUDE_SHELF = TMP / ".claude-shelf"


class FakeSystemd(ls.Systemd):
    def __init__(self) -> None:
        super().__init__(unit_dir=UNIT_DIR)
        self.armed: set[str] = set()
        self.calls: list[tuple[str, str]] = []

    def reload(self) -> None:
        self.calls.append(("reload", ""))

    def arm(self, unit: str) -> None:
        self.armed.add(unit)
        self.calls.append(("arm", unit))

    def disarm(self, unit: str) -> None:
        self.armed.discard(unit)
        self.calls.append(("disarm", unit))

    def timers(self, units: list[str]) -> dict[str, dict]:
        return {u: {"next": "2026-11-28T11:20:00Z" if u in self.armed else None,
                    "running_since": None} for u in units}


SYSTEMD = FakeSystemd()
activity.SYSTEMD = SYSTEMD
profile.SYSTEMD = SYSTEMD
eng.HOST_SETTINGS = STATE / "broker" / "settings.json"


def _load(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


broker_mod = _load("session_broker_rename", ROOT / "bin" / "session-broker")
drift_mod = _load("check_drift_rename", ROOT / "bin" / "check-drift")
drift_mod.AI_LABS = LABS
drift_mod.UNIT_DIR = UNIT_DIR


def make_agent(name: str, *, display: str | None = None, face: bool = False,
               archived: str | None = None, extra_card: dict | None = None,
               with_cron: bool = False, git_commit: bool = True) -> Path:
    ws = LABS / name
    (ws / ".claude" / "output-styles").mkdir(parents=True, exist_ok=True)
    (ws / ".codex").mkdir(parents=True, exist_ok=True)
    (ws / ".agents").mkdir(parents=True, exist_ok=True)
    (ws / "skills").mkdir(parents=True, exist_ok=True)
    (ws / "crons").mkdir(parents=True, exist_ok=True)
    (ws / "memory").mkdir(parents=True, exist_ok=True)
    (ws / "notes").mkdir(parents=True, exist_ok=True)

    shown = display or name.capitalize()
    card_data = {
        "name": name,
        "display": shown,
        "workspace": str(ws),
        "vault": str(VAULT),
        "face_slot": len(list(LABS.glob("*/agent.json"))),
        **(extra_card or {}),
    }
    if archived:
        card_data["archived"] = archived
    if face:
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        (ws / "face.png").write_bytes(png)
        card_data["face"] = {
            "file": "face.png",
            "type": "image/png",
            "sha": hashlib.sha256(png).hexdigest(),
            "bytes": len(png),
            "at": "2026-11-20T00:00:00Z",
        }
    (ws / "agent.json").write_text(profile.render_agent(card_data), encoding="utf-8")
    (ws / "soul.md").write_text(f"# {shown}\nSelf-authored soul for {name}.\n", encoding="utf-8")
    (ws / "understanding.md").write_text(f"# Understanding\nPrivate notes for {name}.\n", encoding="utf-8")
    (ws / "map.md").write_text(f"# Map\nMap for {name}.\n", encoding="utf-8")
    (ws / "config.md").write_text(f"# Config\nConfig for {name}.\n", encoding="utf-8")
    (ws / "notes" / "diary.md").write_text(f"Diary of {name}.\n", encoding="utf-8")
    (ws / "CLAUDE.md").write_text(f"You are {shown}.\n\n@soul.md\n", encoding="utf-8")
    (ws / ".claude" / "settings.json").write_text(
        json.dumps({"model": "fable", "permissions": {"defaultMode": "acceptEdits"}}) + "\n",
        encoding="utf-8")
    (ws / ".claude" / "settings.local.json").write_text(
        json.dumps({"autoMemoryDirectory": str(ws / "memory")}) + "\n", encoding="utf-8")
    (ws / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"loam": {"env": {"LOAM_AGENT": name, "LOAM_AGENT_WORKSPACE": str(ws)}}}}) + "\n",
        encoding="utf-8")
    (ws / ".codex" / "config.toml").write_text(
        f'model_instructions_file = "{CODEX_HOME / "instructions" / f"{name}.md"}"\n',
        encoding="utf-8")
    (ws / ".agents" / "mcp_config.json").write_text(
        json.dumps({"mcpServers": {"loam": {"env": {"LOAM_AGENT": name}}}}) + "\n",
        encoding="utf-8")
    loam_codex.write_instructions(name, ws)
    agy.write_instructions(ws)

    # Seed reflection & optional named cron
    refl = ls.reflection_record("03:20")
    cron_md = "---\nschedule: Wednesdays at 05:00 PT\nenabled: true\n---\nWeekly digest.\n"
    if archived:
        (ws / "crons" / "reflection.md").write_text(refl, encoding="utf-8")
        if with_cron:
            (ws / "crons" / "digest.md").write_text(cron_md, encoding="utf-8")
    else:
        ls.change(name, "reflection", refl, actor="engineer", lane="engineer",
                  reason="seed", systemd=SYSTEMD)
        if with_cron:
            ls.change(name, "digest", cron_md, actor="engineer", lane="engineer",
                      reason="seed", systemd=SYSTEMD)

    # Add Claude trust entry
    cj = json.loads(CLAUDE_JSON.read_text(encoding="utf-8"))
    cj.setdefault("projects", {})[str(ws)] = {
        "hasTrustDialogAccepted": True,
        "hasClaudeMdExternalIncludesApproved": True,
        "hasClaudeMdExternalIncludesWarningShown": True,
    }
    CLAUDE_JSON.write_text(json.dumps(cj, indent=2) + "\n", encoding="utf-8")

    # Add Codex trust entry
    cfg_toml = CODEX_HOME / "config.toml"
    existing_toml = cfg_toml.read_text(encoding="utf-8") if cfg_toml.is_file() else ""
    cfg_toml.write_text(
        existing_toml + f'\n[projects."{ws}"]\ntrust_level = "trusted"\n',
        encoding="utf-8")

    if git_commit:
        subprocess.run(["git", "-C", str(LABS), "add", name], check=True)
        subprocess.run(["git", "-C", str(LABS), "commit", "-q", "-m", f"seed {name}"], check=True)
    return ws


class FakeResumableSession:
    """Simulates a resident Claude Code session whose history lives in
    ``CLAUDE_HOME/projects/<slug(cwd)>/<sid>.jsonl`` and can only resume
    if the file exists at the session's current ``cwd`` slug."""

    def __init__(self, sid: str, agent: str, cwd: Path, *, busy_reason: str | None = None):
        self.id = sid
        self.agent = agent
        self.cwd = cwd
        self.engine = "claude-code"
        self.state = "idle"
        self._busy_reason = busy_reason
        self._live = True
        self.record = {"id": sid, "agent": agent, "engine": "claude-code",
                       "workspace": str(cwd), "state": "idle"}

    def is_live(self) -> bool:
        return self._live

    def busy(self) -> str | None:
        return self._busy_reason

    async def stop(self) -> None:
        self._live = False
        self.state = "stopped"

    def jsonl_path(self) -> Path:
        return CLAUDE_HOME / "projects" / loam_adopt.project_slug(self.cwd) / f"{self.id}.jsonl"

    async def run_turn(self, prompt: str) -> str:
        p = self.jsonl_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        history = []
        if p.is_file():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    history.append(json.loads(line))
        # Recall any CODEWORD=... mentioned earlier in this session's JSONL!
        recalled = None
        for entry in history:
            text = entry.get("text", "")
            if "CODEWORD:" in text:
                recalled = text.split("CODEWORD:", 1)[1].strip().split()[0]
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"role": "user", "text": prompt}) + "\n")
        if "what was the codeword" in prompt.lower():
            reply = f"The codeword is {recalled}." if recalled else "I do not remember."
        else:
            reply = f"Noted by {self.agent}."
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"role": "assistant", "text": reply}) + "\n")
        return reply


class FakeBroker:
    def __init__(self) -> None:
        self.token = "test-token"
        self.sessions: dict[str, FakeResumableSession] = {}
        self.stopped_agents: list[str] = []

    def summary(self, sid: str) -> dict:
        s = self.sessions.get(sid)
        return s.record if s else {}

    def start_or_resume(self, sid: str, agent: str) -> FakeResumableSession:
        ws = LABS / agent
        s = self.sessions.get(sid)
        if s is not None and s.is_live() and s.agent == agent and s.cwd == ws:
            return s
        # Starting/resuming a fresh resident process at the agent's current workspace!
        s = FakeResumableSession(sid, agent, ws)
        self.sessions[sid] = s
        return s

    async def stop_agent_idle(self, agent: str) -> list[str]:
        ended: list[str] = []
        for sid, s in list(self.sessions.items()):
            if s.agent != agent:
                continue
            if s.is_live() and s.busy():
                raise RuntimeError(f"{agent} is busy ({s.busy()})")
            if s.is_live():
                await s.stop()
                ended.append(sid)
            self.sessions.pop(sid, None)
        self.stopped_agents.append(agent)
        return ended


async def http(port: int, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    raw = json.dumps(body).encode("utf-8") if body is not None else b""
    req = (f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
           f"Authorization: Bearer {cfg.TOKEN}\r\n"
           f"Content-Type: application/json\r\n"
           f"Content-Length: {len(raw)}\r\nConnection: close\r\n\r\n").encode() + raw
    writer.write(req)
    await writer.drain()
    data = await reader.read()
    writer.close()
    await writer.wait_closed()
    head, _, payload = data.partition(b"\r\n\r\n")
    status = int(head.splitlines()[0].split()[1])
    if payload:
        try:
            return status, json.loads(payload)
        except ValueError:
            return status, {"raw": payload}
    return status, {}


results: list[bool] = []


def check(name: str, ok: bool, detail=None) -> None:
    results.append(bool(ok))
    tag = "PASS" if ok else "FAIL"
    extra = f" | {detail}" if not ok and detail is not None else ""
    print(f"{tag:5s} {name}{extra}", flush=True)


async def main() -> None:
    # Seed agents: iris (starter), ines (to be renamed -> Astrid), cleo (peer),
    # and an archived agent `milo` with former_id `oldmilo`.
    make_agent("iris", display="Iris", extra_card={"effort": "medium"})
    ines_ws = make_agent("ines", display="Ines", face=True, with_cron=True,
                         extra_card={"provider": "claude-code", "model": "opus",
                                     "effort": "high", "verbosity": "low"})
    make_agent("cleo", display="Cleo")
    make_agent("milo", display="Milo", archived="2026-11-15",
               extra_card={"former_ids": ["oldmilo"]})

    # Capture self-authored file bytes before rename (must remain byte-for-byte identical).
    authored_before = {
        rel: (ines_ws / rel).read_bytes()
        for rel in ("soul.md", "understanding.md", "map.md", "config.md", "notes/diary.md")
    }

    # Seed commons inbox for ines (tracked in ai-labs git).
    (LABS / "commons" / "ines").mkdir(parents=True, exist_ok=True)
    (LABS / "commons" / "ines" / "note.md").write_text("from cleo\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(LABS), "add", "commons/ines"], check=True)
    subprocess.run(["git", "-C", str(LABS), "commit", "-q", "-m", "commons ines"], check=True)

    # Seed Codex state_5.sqlite with a thread row for ines.
    codex_db = CODEX_HOME / "state_5.sqlite"
    con = sqlite3.connect(str(codex_db))
    con.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, cwd TEXT)")
    con.execute("INSERT INTO threads VALUES (?, ?)", ("codex-th-1", str(ines_ws)))
    con.commit()
    con.close()

    # Seed wake state, lane-notice, wake log, and historical schedule event for ines.
    ls.WAKE_STATE.mkdir(parents=True, exist_ok=True)
    (ls.WAKE_STATE / "ines.json").write_text('{"last_ref": "abc123"}\n', encoding="utf-8")
    (STATE / "lane-notice").mkdir(parents=True, exist_ok=True)
    (STATE / "lane-notice" / "ines.json").write_text('{"n": 1}\n', encoding="utf-8")
    (LOGS / "wake-ines.jsonl").write_text(
        json.dumps({"ts": "2026-11-26T11:20:00Z", "lane": "wake", "ok": True,
                    "vault_ref": "deadbeef"}) + "\n", encoding="utf-8")
    ls.log({"kind": "fire", "agent": "ines", "record": "reflection",
            "outcome": "ok", "lane": "wake", "started": "2026-11-26T11:20:00Z"})

    # Seed broker session record + thread + resumable Claude Code session.
    sid = "sess-resume-001"
    fake = FakeBroker()
    sess = fake.start_or_resume(sid, "ines")
    reply1 = await sess.run_turn("Remember CODEWORD: SAFFRON-4411 please.")
    check("pre-rename turn ran and wrote session file under old project slug",
          "ines" in reply1 and sess.jsonl_path().is_file(), sess.jsonl_path())

    (cfg.SESSION_DIR / f"{sid}.json").write_text(json.dumps({
        "id": sid, "agent": "ines", "engine": "claude-code",
        "workspace": str(ines_ws), "source": str(sess.jsonl_path()),
        "state": "idle",
    }, indent=2) + "\n", encoding="utf-8")

    # Create a vault thread with ines (default) + cleo, messages authored by ines & reply_source.
    meta = threads.create("ines", "Project talk")
    tid = meta["id"]
    threads.append(tid, "owner", "Hey @ines remember CODEWORD: SAFFRON-4411")
    m2 = threads.append(tid, "ines", "Noted by ines.", persona="ines")
    threads.append(tid, "owner", "Follow up", reply_to=m2["id"])
    vault_thread_path = threads.record_path(tid)
    vault_bytes_before = vault_thread_path.read_bytes()

    # Bind the resumable session & handoff & mark in thread runtime state.
    threads.remember_session(tid, "ines", sid, "claude-code", m2["id"])
    threads.save_state("handoffs.json", {tid: {"ines": {"summary": "talked about saffron"}}})
    (threads.STATE / "marks.json").write_text(json.dumps({tid: {"ines": "ok"}}), encoding="utf-8")

    # Seed registries: starter.json, system.persona, waiters, plugins (agents.json + read-aloud),
    # garden.json, attribution.sqlite, reviewer memory/notes, and sites/*.toml.
    (STATE / "broker" / "settings.json").write_text(
        json.dumps({"system": {"persona": "ines"}}) + "\n", encoding="utf-8")
    (WAITERS_DIR / "job-1.json").write_text(
        json.dumps({"job": "job-1", "persona": "ines", "thread": tid}) + "\n", encoding="utf-8")
    (PLUGINS / "_settings" / "voices").mkdir(parents=True, exist_ok=True)
    (PLUGINS / "_settings" / "voices" / "agents.json").write_text(
        json.dumps({"version": 1, "agents": {"ines": {"voice": "Kore"}, "cleo": {"voice": "Puck"}}}) + "\n",
        encoding="utf-8")
    (PLUGINS / "_settings" / "read-aloud").mkdir(parents=True, exist_ok=True)
    (PLUGINS / "_settings" / "read-aloud" / "values.json").write_text(
        json.dumps({"voices": "ines=Kore:warm;cleo=Puck:bright"}) + "\n", encoding="utf-8")
    GARDEN_FILE.write_text(json.dumps({
        "previous": {"agent": {"ines": "snap-1"}, "bed": {"ines": "bed-1"}},
        "ideas": {"idea-1": {"agent": "ines", "title": "Plant herbs"}},
    }) + "\n", encoding="utf-8")
    loam_recent.db_path().parent.mkdir(parents=True, exist_ok=True)
    recent_con = sqlite3.connect(str(loam_recent.db_path()))
    recent_con.execute(
        "CREATE TABLE writes (id INTEGER PRIMARY KEY, path TEXT, kind TEXT, actor TEXT, at TEXT)")
    recent_con.execute("INSERT INTO writes (path, kind, actor, at) VALUES (?, ?, ?, ?)",
                       ("note.md", "agent", "ines", "2026-11-26T00:00:00Z"))
    recent_con.commit()
    recent_con.close()
    (REVIEWER_STATE / "memory" / "ines.md").write_text("reviewer memory of ines\n", encoding="utf-8")
    (REVIEWER_STATE / "notes" / "ines").mkdir(parents=True, exist_ok=True)
    (REVIEWER_STATE / "notes" / "ines" / "2026-11-26.md").write_text("review note\n", encoding="utf-8")
    (SITES_DIR / "ines.toml").write_text(
        f'owner = "ines"\nroot = "{ines_ws}/site"\n', encoding="utf-8")
    (SITES_DIR / "gallery.toml").write_text(
        f'owner = "ines"\nroot = "{ines_ws}/gallery"\n', encoding="utf-8")

    # Hook deploy_regen inside rename_agent so we verify gen-deploy would be invoked.
    deploy_regen_calls: list[int] = []
    orig_rename = loam_rename.rename_agent

    def wrapped_rename(*args, **kwargs):
        kwargs.setdefault("systemd", SYSTEMD)
        kwargs.setdefault("deploy_regen", lambda: deploy_regen_calls.append(1))
        return orig_rename(*args, **kwargs)

    loam_rename.rename_agent = wrapped_rename

    # Start in-process session-broker HTTP server.
    server = broker_mod.Server.__new__(broker_mod.Server)
    server.broker = fake
    server.threads = engine_mod.ThreadEngine(fake)
    server.profile_check = lambda provider, model, effort: None
    tcp = await asyncio.start_server(server.handle, "127.0.0.1", 0)
    port = tcp.sockets[0].getsockname()[1]

    try:
        # -- 1. Busy refusals (409) -------------------------------------------
        server.threads.turns[(tid, "ines")] = {"persona": "ines", "started_at": "2026-11-27T08:00:00Z"}
        st, body = await http(port, "PATCH", "/v1/agents/ines",
                              {"display": "Astrid", "by": "ines"})
        check("self-rename while mid-turn refused with 409 and helpful hint",
              st == 409 and "profile page" in body.get("error", "")
              and (LABS / "ines").is_dir(), body)
        server.threads.turns.pop((tid, "ines"))

        thread_inputs.add(tid, "ines", {"kind": "message", "text": "queued msg"})
        st, body = await http(port, "PATCH", "/v1/agents/ines", {"display": "Astrid"})
        check("rename with queued thread input refused with 409",
              st == 409 and "queued" in body.get("error", ""), body)
        thread_inputs.drop_member(tid, "ines")

        fake.sessions[sid]._busy_reason = "working"
        st, body = await http(port, "PATCH", "/v1/agents/ines", {"display": "Astrid"})
        check("rename with busy resident session refused with 409",
              st == 409 and "working" in body.get("error", ""), body)
        fake.sessions[sid]._busy_reason = None

        marker = ls.WAKE_STATE / "ines.running"
        marker.write_text(json.dumps({"pid": os.getpid(), "lane": "wake"}), encoding="utf-8")
        st, body = await http(port, "PATCH", "/v1/agents/ines", {"display": "Astrid"})
        check("rename during active wake/cron refused with 409",
              st == 409 and "wake" in body.get("error", ""), body)
        # Dead PID marker should NOT falsely block rename!
        marker.write_text(json.dumps({"pid": 9999999, "lane": "wake"}), encoding="utf-8")
        check("stale dead-PID .running marker does not count as busy",
              not loam_rename.wake_running_busy("ines", ls.WAKE_STATE))
        marker.unlink(missing_ok=True)

        # -- 2. Collision & reserved refusals ---------------------------------
        (LABS / "takenfolder").mkdir()
        for bad_display, code, label in (
            ("Cleo", 409, "another agent's id/display"),
            ("Milo", 409, "archived agent's name"),
            ("OldMilo", 409, "archived agent's former_id"),
            ("TakenFolder", 409, "existing folder in ai-labs"),
            ("engineer", 400, "reserved id"),
            ("all", 400, "reserved mention word"),
            ("a", 400, "too short slug"),
            ("!!!", 400, "no slug letters"),
        ):
            st, body = await http(port, "PATCH", "/v1/agents/ines", {"display": bad_display})
            check(f"refused ({code}): {label}",
                  st == code and (LABS / "ines" / "agent.json").is_file(), (st, body))
        (LABS / "takenfolder").rmdir()

        # -- 3. Perform full rename: ines -> Astrid ---------------------------
        check("pre-rename wake and cron units exist under ines",
              (UNIT_DIR / "loam-wake-ines.timer").is_file()
              and (UNIT_DIR / "loam-cron-ines-digest.timer").is_file())

        st, body = await http(port, "PATCH", "/v1/agents/ines", {"display": "Astrid"})
        check("PATCH /v1/agents/ines -> Astrid returns 200 with new id & renamed_from",
              st == 200 and body["agent"]["id"] == "astrid"
              and body["agent"]["name"] == "Astrid"
              and body["identity"]["id"] == "astrid"
              and body["identity"]["former_ids"] == ["ines"]
              and body.get("renamed_from") == "ines", body)

        astrid_ws = LABS / "astrid"
        check("workspace folder moved from ines to astrid",
              not (LABS / "ines").exists() and astrid_ws.is_dir())
        check("resident idle session was cleanly unloaded before folder move",
              "ines" in fake.stopped_agents and sid not in fake.sessions)

        # Agent-authored files untouched byte-for-byte.
        check("all self-authored files preserved byte-for-byte",
              all((astrid_ws / rel).read_bytes() == raw for rel, raw in authored_before.items()))

        # Git mv history preserved (commit + git log --follow).
        subprocess.run(["git", "-C", str(LABS), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(LABS), "commit", "-q", "-m", "after rename"], check=True)
        hist = subprocess.run(
            ["git", "-C", str(LABS), "log", "--follow", "--oneline", "--", "astrid/soul.md"],
            capture_output=True, text=True, check=True).stdout
        check("git log --follow preserves pre-rename history",
              "seed ines" in hist, hist)

        # Settings & face preserved in agent.json; rendered runtime files updated.
        acard_raw = (astrid_ws / "agent.json").read_text(encoding="utf-8")
        acard = json.loads(acard_raw)
        expected_acard = profile.render_agent({
            "name": "astrid", "display": "Astrid",
            "workspace": str(astrid_ws), "vault": str(VAULT),
            **profile.kept(acard),
        })
        check("agent.json carries over face, face_slot, provider, model, effort, verbosity, former_ids in check-drift order",
              acard_raw == expected_acard
              and acard["name"] == "astrid" and acard["display"] == "Astrid"
              and acard["workspace"] == str(astrid_ws)
              and acard["former_ids"] == ["ines"]
              and acard.get("face", {}).get("file") == "face.png"
              and isinstance(acard.get("face_slot"), int)
              and acard.get("provider") == "claude-code"
              and acard.get("model") == "opus"
              and acard.get("effort") == "high"
              and acard.get("verbosity") == "low", (acard_raw, expected_acard))

        mcp_text = (astrid_ws / ".mcp.json").read_text(encoding="utf-8")
        codex_toml = (astrid_ws / ".codex" / "config.toml").read_text(encoding="utf-8")
        agy_mcp = (astrid_ws / ".agents" / "mcp_config.json").read_text(encoding="utf-8")
        claude_md_text = (astrid_ws / "CLAUDE.md").read_text(encoding="utf-8")
        cj_after = json.loads(CLAUDE_JSON.read_text(encoding="utf-8"))["projects"]
        codex_trust = (CODEX_HOME / "config.toml").read_text(encoding="utf-8")
        check("rendered runtime files & trust updated to astrid",
              claude_md_text.startswith("You are Astrid.\n")
              and str(astrid_ws) in claude_md_text
              and '"LOAM_AGENT": "astrid"' in mcp_text
              and str(astrid_ws) in mcp_text
              and "instructions/astrid.md" in codex_toml
              and (CODEX_HOME / "instructions" / "astrid.md").is_file()
              and not (CODEX_HOME / "instructions" / "ines.md").exists()
              and '"LOAM_AGENT": "astrid"' in agy_mcp
              and "You are Astrid." in (astrid_ws / agy.RULES_FILE).read_text(encoding="utf-8")
              and str(astrid_ws) in cj_after and str(ines_ws) not in cj_after
              and str(astrid_ws) in codex_trust and str(ines_ws) not in codex_trust)

        # -- 4. Resumable session across rename + follow-up turn --------------
        old_cl_dir = CLAUDE_HOME / "projects" / loam_adopt.project_slug(ines_ws)
        new_cl_dir = CLAUDE_HOME / "projects" / loam_adopt.project_slug(astrid_ws)
        check("Claude Code project dir moved to new workspace slug",
              not old_cl_dir.exists() and (new_cl_dir / f"{sid}.jsonl").is_file())

        # Codex sqlite updated
        con = sqlite3.connect(str(codex_db))
        cwd_row = con.execute("SELECT cwd FROM threads WHERE id='codex-th-1'").fetchone()
        con.close()
        check("Codex state_5.sqlite threads.cwd updated",
              cwd_row == (str(astrid_ws),), cwd_row)

        # Broker session record + thread session map updated
        srec = json.loads((cfg.SESSION_DIR / f"{sid}.json").read_text(encoding="utf-8"))
        tsess = threads.load_state("sessions.json").get(tid, {})
        check("broker session record & thread sessions.json rekeyed to astrid",
              srec["agent"] == "astrid" and srec["workspace"] == str(astrid_ws)
              and srec["source"] == str(new_cl_dir / f"{sid}.jsonl")
              and (tsess.get("astrid") or {}).get("session_id") == sid
              and "ines" not in tsess, (srec, tsess))

        # Resume the SAME session under astrid and prove prior context survived!
        resumed = fake.start_or_resume(tsess["astrid"]["session_id"], "astrid")
        reply2 = await resumed.run_turn("What was the codeword I gave you?")
        check("follow-up turn in resumed session recalls pre-rename codeword",
              "SAFFRON-4411" in reply2, reply2)

        # -- 5. Vault thread record UNTOUCHED yet resolves to astrid ----------
        check("vault thread record (15_CONVERSATIONS/*.md) is byte-for-byte untouched",
              vault_thread_path.read_bytes() == vault_bytes_before)

        rmeta, rmsgs, _ = threads.read(tid)
        check("thread meta & past messages resolve old id ines -> astrid in memory",
              rmeta["default"] == "astrid"
              and rmeta["members"] == ["astrid"]
              and rmsgs[1]["author"] == "astrid"
              and rmsgs[1]["persona"] == "astrid"
              and rmsgs[2].get("reply_to") == m2["id"],
              (rmeta, rmsgs))
        st_th, body_th = await http(port, "GET", f"/v1/threads/{tid}")
        check("GET /v1/threads/<tid> serves resolved astrid default, members & messages",
              st_th == 200 and body_th["thread"]["default"] == "astrid"
              and any(m["persona"] == "astrid" for m in body_th["thread"]["members"])
              and not any(m["persona"] == "ines" for m in body_th["thread"]["members"])
              and body_th["messages"][1]["author"] == "astrid", body_th)
        check("mentions match both @Astrid, @astrid, and past alias @ines",
              threads.mentions("@Astrid hi") == ["astrid"]
              and threads.mentions("@astrid hi") == ["astrid"]
              and threads.mentions("@ines hi") == ["astrid"])

        # Old URL alias resolution on GET /v1/agents/<old> and /face
        st_old, body_old = await http(port, "GET", "/v1/agents/ines")
        check("GET /v1/agents/ines resolves to astrid",
              st_old == 200 and body_old["agent"]["id"] == "astrid", body_old)
        st_face, _ = await http(port, "GET", "/v1/agents/ines/face")
        check("GET /v1/agents/ines/face resolves via alias (200)", st_face == 200, st_face)

        # -- 6. Wake & cron units + check-drift stray detection ---------------
        unit_names = sorted(p.name for p in UNIT_DIR.iterdir())
        check("wake & cron units exist under astrid and NONE remain under ines",
              (UNIT_DIR / "loam-wake-astrid.timer").is_file()
              and (UNIT_DIR / "loam-wake-astrid.service").is_file()
              and (UNIT_DIR / "loam-cron-astrid-digest.timer").is_file()
              and "loam-wake-astrid.timer" in SYSTEMD.armed
              and "loam-cron-astrid-digest.timer" in SYSTEMD.armed
              and not list(UNIT_DIR.glob("loam-wake-ines.*"))
              and not list(UNIT_DIR.glob("loam-cron-ines-*"))
              and "loam-wake-ines.timer" not in SYSTEMD.armed
              and "loam-cron-ines-digest.timer" not in SYSTEMD.armed,
              (unit_names, sorted(SYSTEMD.armed)))

        # Plant a stray old wake timer and verify check-drift catches it.
        drift_mod.user_systemctl = lambda *a: "enabled"
        stray = UNIT_DIR / "loam-wake-ines.timer"
        stray.write_text("[Timer]\n", encoding="utf-8")
        findings: list = []
        drift_mod.check_crons(findings, loam_cron.roster())
        check("check-drift flags stray old wake unit for former_id",
              any("loam-wake-ines.timer" in f[0] for f in findings), findings)
        stray.unlink()
        findings2: list = []
        drift_mod.check_crons(findings2, loam_cron.roster())
        check("check-drift clean once stray old unit is removed",
              not any("ines" in f[0] for f in findings2), findings2)

        # -- 7. Activity, wake logs, commons, and every registry --------------
        check("commons inbox moved from commons/ines to commons/astrid",
              not (LABS / "commons" / "ines").exists()
              and (LABS / "commons" / "astrid" / "note.md").read_text(encoding="utf-8") == "from cleo\n")
        check("wake state, lane-notice, and wake log moved to astrid",
              (ls.WAKE_STATE / "astrid.json").is_file()
              and not (ls.WAKE_STATE / "ines.json").exists()
              and (STATE / "lane-notice" / "astrid.json").is_file()
              and (LOGS / "wake-astrid.jsonl").is_file()
              and not (LOGS / "wake-ines.jsonl").exists())

        act = activity.activity(agent="astrid")
        check("pre-rename schedule fire & wake log visible on astrid's Activity",
              any(i.get("agent") == "astrid" and i.get("kind") == "fire"
                  and (i.get("produced") or {}).get("line", {}).get("vault_ref") == "deadbeef"
                  for i in act["items"]), act["items"])

        sched_rows = activity.schedules("astrid")
        refl_row = next((r for r in sched_rows if r["name"] == "reflection"), {})
        check("astrid's schedule row preserves pre-rename last_fire",
              (refl_row.get("last_fire") or {}).get("outcome") == "ok", refl_row)

        voices_agents = json.loads((PLUGINS / "_settings" / "voices" / "agents.json").read_text())
        ra_vals = json.loads((PLUGINS / "_settings" / "read-aloud" / "values.json").read_text())
        garden_after = json.loads(GARDEN_FILE.read_text())
        sys_settings = json.loads((STATE / "broker" / "settings.json").read_text())
        waiter_after = json.loads((WAITERS_DIR / "job-1.json").read_text())
        rcon = sqlite3.connect(str(loam_recent.db_path()))
        rrow = rcon.execute("SELECT actor FROM writes WHERE path='note.md'").fetchone()
        rcon.close()
        check("registries carried over: plugins, read-aloud, garden, system.persona, waiters, attribution, reviewer, sites",
              "astrid" in voices_agents["agents"] and "ines" not in voices_agents["agents"]
              and voices_agents["agents"]["astrid"]["voice"] == "Kore"
              and "astrid=Kore:warm" in ra_vals["voices"] and "ines=" not in ra_vals["voices"]
              and garden_after["previous"]["agent"].get("astrid") == "snap-1"
              and garden_after["previous"]["bed"].get("astrid") == "bed-1"
              and "ines" not in garden_after["previous"]["agent"]
              and garden_after["ideas"]["idea-1"]["agent"] == "astrid"
              and sys_settings["system"]["persona"] == "astrid"
              and waiter_after["persona"] == "astrid"
              and rrow == ("astrid",)
              and (REVIEWER_STATE / "memory" / "astrid.md").is_file()
              and (REVIEWER_STATE / "notes" / "astrid" / "2026-11-26.md").is_file()
              and (SITES_DIR / "astrid.toml").is_file() and not (SITES_DIR / "ines.toml").exists()
              and 'owner = "astrid"' in (SITES_DIR / "astrid.toml").read_text()
              and str(astrid_ws) in (SITES_DIR / "astrid.toml").read_text()
              and 'owner = "astrid"' in (SITES_DIR / "gallery.toml").read_text()
              and str(astrid_ws) in (SITES_DIR / "gallery.toml").read_text()
              and len(deploy_regen_calls) == 1,
              (voices_agents, ra_vals, garden_after, sys_settings, waiter_after, rrow, deploy_regen_calls))

        # Feedback scrubber scrubs both new name and former_id
        scrubbed = loam_feedback.scrub("Astrid used to be ines in logs")
        check("loam_feedback.scrub redacts both new name and former_ids",
              "Astrid" not in scrubbed and "ines" not in scrubbed, scrubbed)

        # Another agent cannot claim astrid's past alias `ines`
        st_col, body_col = await http(port, "PATCH", "/v1/agents/cleo", {"display": "Ines"})
        check("another agent cannot claim astrid's past alias ines (409)",
              st_col == 409 and (LABS / "cleo").is_dir(), (st_col, body_col))

        # -- 8. Starter agent (iris) rename works cleanly everywhere ----------
        st_s, body_s = await http(port, "PATCH", "/v1/agents/iris", {"display": "Lyra"})
        check("renaming starter agent iris -> Lyra succeeds",
              st_s == 200 and body_s["agent"]["id"] == "lyra"
              and body_s["agent"].get("starter") is True
              and not (LABS / "iris").exists()
              and loam_agents.starter_agent() == "lyra", body_s)
        st_new, body_new = await http(port, "POST", "/v1/agents/new", {})
        check("POST /v1/agents/new starts thread with renamed starter agent lyra",
              st_new == 201 and body_new.get("default") == "lyra", body_new)
        # Clear system.persona to verify default_persona() falls back to renamed starter/lead
        (STATE / "broker" / "settings.json").write_text("{}\n", encoding="utf-8")
        lc.LEAD_AGENT = "iris"
        check("loam_attention.default_persona() resolves renamed starter/lead agent",
              loam_attention.default_persona() == "lyra", loam_attention.default_persona())

        # -- 9. Idempotent migration of already-renamed agents ----------------
        # Simulate pre-feature renamed agents (name="vera", display="Solstice"
        # and an archived agent name="bren", display="Kestrel").
        make_agent("vera", display="Solstice")
        make_agent("bren", display="Kestrel", archived="2026-11-10")
        check("needs_migration detects both awake and archived already-renamed agents",
              {(x["agent"], x["target"]) for x in loam_rename.needs_migration(LABS)}
              == {("vera", "solstice"), ("bren", "kestrel")})

        dry_res = loam_rename.migrate_renamed_agents(
            dry_run=True, use_broker=False, ai_labs=LABS, systemd=SYSTEMD)
        check("migrate --dry-run reports targets without moving anything",
              len(dry_res["migrated"]) == 2 and (LABS / "vera").is_dir()
              and (LABS / "bren").is_dir(), dry_res)

        run1 = loam_rename.migrate_renamed_agents(
            dry_run=False, use_broker=False, ai_labs=LABS, systemd=SYSTEMD)
        check("1st migration moves vera->solstice and bren->kestrel cleanly",
              len(run1["migrated"]) == 2 and not run1["skipped"]
              and (LABS / "solstice" / "agent.json").is_file()
              and not (LABS / "vera").exists()
              and (LABS / "kestrel" / "agent.json").is_file()
              and not (LABS / "bren").exists()
              and "loam-wake-solstice.timer" in SYSTEMD.armed
              and "loam-wake-vera.timer" not in SYSTEMD.armed
              and "loam-wake-kestrel.timer" not in SYSTEMD.armed
              and loam_agents.is_archived("kestrel", LABS), run1)

        run2 = loam_rename.migrate_renamed_agents(
            dry_run=False, use_broker=False, ai_labs=LABS, systemd=SYSTEMD)
        check("2nd migration is a clean idempotent no-op",
              run2["migrated"] == [] and run2["skipped"] == [], run2)

        # -- 10. CLI: bin/provision --migrate-renamed-agents (run twice) ------
        # Uses run-all's scratch LOAM_CONFIG workspace so the real CLI entry point
        # runs end-to-end in its own subprocess without touching the live box.
        cli_cfg_path = os.environ.get("LOAM_CONFIG")
        if cli_cfg_path:
            import tomllib  # noqa: PLC0415
            cli_cfg = tomllib.loads(Path(cli_cfg_path).read_text(encoding="utf-8"))
            cli_labs = Path(cli_cfg["paths"]["workspaces"])
            cli_ws = cli_labs / "oldbot"
            (cli_ws / "crons").mkdir(parents=True, exist_ok=True)
            (cli_ws / "soul.md").write_text("# Oldbot\n", encoding="utf-8")
            (cli_ws / "CLAUDE.md").write_text("You are Newbot.\n\n@soul.md\n", encoding="utf-8")
            (cli_ws / "agent.json").write_text(profile.render_agent({
                "name": "oldbot", "display": "Newbot",
                "workspace": str(cli_ws), "vault": cli_cfg["paths"]["vault"],
                "archived": "2026-11-15",
            }), encoding="utf-8")
            p1 = subprocess.run(
                [sys.executable, str(ROOT / "bin" / "provision"), "--migrate-renamed-agents"],
                capture_output=True, text=True, check=False)
            p2 = subprocess.run(
                [sys.executable, str(ROOT / "bin" / "provision"), "--migrate-renamed-agents"],
                capture_output=True, text=True, check=False)
            check("bin/provision --migrate-renamed-agents migrates on 1st run and is no-op on 2nd",
                  p1.returncode == 0 and "migrated oldbot -> newbot" in p1.stdout
                  and (cli_labs / "newbot" / "agent.json").is_file()
                  and not cli_ws.exists()
                  and p2.returncode == 0 and "already match their display name" in p2.stdout,
                  (p1.returncode, p1.stdout, p1.stderr, p2.returncode, p2.stdout, p2.stderr))

    finally:
        tcp.close()
        await tcp.wait_closed()


asyncio.run(main())
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
