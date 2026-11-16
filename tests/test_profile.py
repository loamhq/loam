#!/usr/bin/env python3
"""Settings > Agents (2026-11-13): an agent's profile, the engineers, the
nightly reviewer and a plugin's per-agent settings, through a real broker
listener on loopback and through `bin/settings` as a persona.

  - GET /v1/agents/<agent> answers identity, defaults (as set, as each lane
    uses them, what is not honoured), the files of who it is, its schedule
    rows with a label and plain words (the wake first), its status (turns in
    flight, jobs it briefed, next wake) and the plugin rows for it
  - PATCH: a display name (agent.json in its one order; an @ matches it; the
    cards say it; another agent's name is a 409, a reserved word a 400; null
    is the id's own), a face, a provider, a model and a thinking level
    (checked as the provider takes them; a provider change clears both; a
    Codex provider says wakes and crons are not honoured); nothing is
    written on a refusal
  - bin/agent.json: provision's renderer keeps what was set; lane_choice is
    the wake's rule
  - a persona's change through `bin/settings` reads back and is said in
    General; from an unattended lane it is refused before any call
  - engineers: Loam first and Claude Code only; a project's own setting, the
    default for every other, and back; what the launcher reads
    (engineer_choice, choice_for, deploy_default); deploy_on_land refused
    where there is nothing to deploy
  - the reviewer: a schedule becomes its plugin's setting (its timer follows
    it), the default puts it back, and with the plugin off it is a 409; the unit's own
    schedule removes it; a bad schedule and another provider are 400s;
    reviewer_choice is what bin/review reads
  - plugins: `agent_settings` in a manifest, `agent.profile` a known place;
    values per agent stored in Yours' _settings/<id>/agents.json, defaults
    filled, a choice not offered and an undeclared key refused; a plugin
    that declares nothing keeps any slug

Run: python3 tests/test_profile.py
"""

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="loam-profile-test-"))
LABS = TMP / "ai-labs"
os.environ["LOAM_VAULT"] = str(TMP / "vault")
os.environ["LOAM_AI_LABS"] = str(LABS)
os.environ["LOAM_THREADS_STATE"] = str(TMP / "state")
os.environ["LOAM_HOST_SETTINGS"] = str(TMP / "broker" / "settings.json")
os.environ["LOAM_SCHEDULE_STATE"] = str(TMP / "schedules")
# The runtime's own first-party plugins too (2026-11-14, L9): the reviewer is
# one, and its schedule is its plugin's setting.
os.environ["LOAM_PLUGIN_SHIPPED"] = f"{TMP / 'shipped'}:{AGENTS / 'plugins'}"
os.environ["LOAM_PLUGINS_DIR"] = str(TMP / "yours")
os.environ["LOAM_UI_CORE_OPTIONS"] = "none"
os.environ["LOAM_UI_APPEARANCE_REGISTRY"] = "none"
os.environ.pop("LOAM_WAKE", None)

for who, emoji in (("ada", "🧱"), ("iris", "🪻"), ("cleo", "🪐")):
    ws = LABS / who
    (ws / "crons").mkdir(parents=True)
    (ws / "skills" / "a-skill").mkdir(parents=True)
    (ws / "skills" / "notes.md").write_text("a skill\n")
    (ws / "skills" / "_private.md").write_text("not a skill\n")
    for f in ("soul.md", "config.md", "_always.md", "_active.md", "_reflection.md"):
        (ws / f).write_text(f"{who} {f}\n")
    (ws / "agent.json").write_text(json.dumps({
        "name": who, "display": who.capitalize(), "emoji": emoji,
        "workspace": str(ws), "vault": str(TMP / "vault"),
        "wake_schedule": "03:20", "wake_timezone": "America/Los_Angeles"},
        indent=2, ensure_ascii=False) + "\n")
    (ws / "crons" / "reflection.md").write_text(
        "---\nlane: reflection\nschedule: daily at 03:20 PT\nenabled: true\n"
        "reason: the test's fixture\n---\n"
        "The nightly reflection wake.\n")
(LABS / "ada" / "crons" / "infra-watch.md").write_text(
    "---\nschedule: Wednesdays at 05:00 PT\nenabled: true\n"
    "reason: the test's fixture\n---\n# Cron\nWatch.\n")
(TMP / "vault" / "20_ATLAS").mkdir(parents=True)
(TMP / "vault" / "20_ATLAS" / "Ada.md").write_text("---\nemoji: 🧱\n---\n")
(TMP / "vault" / "15_CONVERSATIONS").mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents").mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents" / "comms.md").write_text("# From agents\n")

# Two plugins in Yours: one that declares a voice for each agent and fills
# the profile, one that declares nothing.
VOICES = TMP / "yours" / "voices"
VOICES.mkdir(parents=True)
(VOICES / "PLUGIN.md").write_text(
    "---\nid: voices\nname: Voices\nversion: 1.0.0\napi: 0\nkind: [extension]\n"
    "places: [agent.profile]\nagent_settings:\n  - id: voice\n    label: Voice\n"
    "    choices: [{id: Kore, label: Kore}, {id: Puck, label: Puck}]\n"
    "    default: Kore\n  - id: style\n    label: Style\n---\n\nVoices.\n")
LOOSE = TMP / "yours" / "loose"
LOOSE.mkdir(parents=True)
(LOOSE / "PLUGIN.md").write_text(
    "---\nid: loose\nname: Loose\nversion: 1.0.0\napi: 0\nkind: [extension]\n"
    "places: [agent.profile]\n---\n\nKeeps anything.\n")
for plugin in (VOICES, LOOSE):
    (plugin / "page").mkdir()
    (plugin / "page" / "main.js").write_text("export function activate() {}\n")
(TMP / "shipped").mkdir()

# Two projects: one with an app, one that lands by pull request.
PROJECTS = TMP / "projects"
PROJECTS.mkdir()
for name, extra in (("webapp", {}), ("prproj", {"landing": "pull_request"})):
    (PROJECTS / f"{name}.json").write_text(json.dumps({
        "name": name, "repo": f"someone/{name}", "checkout": str(TMP / name),
        "runtime": "codex", **extra}))

sys.path.insert(0, str(AGENTS / "lib"))
import loam_broker as cfg  # noqa: E402
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}
cfg.AI_LABS = LABS
cfg.VAULT = TMP / "vault"
import loam_threads as threads  # noqa: E402
cfg.roster = threads.roster
import loam_cron  # noqa: E402
import loam_schedule as ls  # noqa: E402
import loam_activity as activity  # noqa: E402
import loam_engineer as eng  # noqa: E402
import loam_autodeploy  # noqa: E402
import loam_profile as profile  # noqa: E402
import loam_plugins  # noqa: E402

UNITS = TMP / "units"
UNITS.mkdir()
loam_cron.AI_LABS = LABS
loam_cron.UNIT_DIR = UNITS
ls.STATE = TMP / "schedules"
ls.WAKE_STATE = TMP / "wake"
ls.BROKER_SESSIONS = TMP / "sessions"
ls.WRITE_PAGE = False
activity.WAKE_LOGS = TMP / "logs"
eng.PROJECTS_DIR = PROJECTS
eng.PROJECT_STATE = TMP / "project-state"
loam_autodeploy.app_for = lambda p: (("webapp", None) if p.name == "webapp"
                                     else (None, f"{p.name} has no app"))


class FakeSystemd(ls.Systemd):
    def __init__(self):
        super().__init__(unit_dir=UNITS, runtime=True)
        self.calls = []

    def run(self, *args):
        self.calls.append(args)
        out = ""
        if args[0] == "show" and args[2].startswith("Id,NextElapseUSecRealtime,"):
            out = "\n\n".join(
                f"Id={u}\nNextElapseUSecRealtime=Thu 2026-11-14 03:20:00 PST\n"
                f"LastTriggerUSec=Wed 2026-11-13 03:20:00 PST\nSubState=waiting\n"
                f"Unit={u.removesuffix('.timer')}.service" for u in args[3:])
        return subprocess.CompletedProcess(args, 0, out, "")


SYSTEMD = FakeSystemd()
activity.SYSTEMD = SYSTEMD
profile.SYSTEMD = SYSTEMD
for who in ("ada", "iris", "cleo"):
    ls.reconcile([who], systemd=SYSTEMD)
BOX = {"slots": [{"slot": 1, "kind": "engineer job", "job": "20261113-x",
                  "title": "A job", "project": "webapp", "briefed_by": "ada",
                  "since": "2026-11-13T20:00:00Z"}],
       "queue": [], "landing": []}
activity.live_jobs = lambda: BOX

loader = importlib.machinery.SourceFileLoader(
    "session_broker", str(AGENTS / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker_mod = importlib.util.module_from_spec(spec)
loader.exec_module(broker_mod)
engine_mod = broker_mod.loam_thread_engine

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:70}"
          f"{'  | ' + str(detail)[:240] if detail != '' else ''}")


class FakeBroker:
    def __init__(self):
        self.sessions = {}
        self.token = "test-token"

    async def start_session(self, *a, **k):
        raise AssertionError("no turn is started in this test")


TOKEN = TMP / "token"
TOKEN.write_text("test-token")


async def http(port, method, path, body=None):
    import urllib.error  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    def go():
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": "Bearer test-token",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())
    return await asyncio.to_thread(go)


async def cli(port, *argv, lane=None):
    env = dict(os.environ, LOAM_BROKER_URL=f"http://127.0.0.1:{port}",
               LOAM_BROKER_TOKEN_FILE=str(TOKEN), LOAM_AGENT="ada")
    env.pop("LOAM_WAKE", None)
    if lane:
        env["LOAM_WAKE"] = lane
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(AGENTS / "bin" / "settings"), *argv, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    try:
        return json.loads(out)
    except ValueError:
        return {"ok": False, "error": f"no JSON: {out!r} {err[-400:]!r}"}


def card(who):
    return json.loads((LABS / who / "agent.json").read_text(encoding="utf-8"))


def general_lines():
    try:
        return [m["text"] for m in threads.read(threads.GENERAL_ID)[1]
                if m.get("kind") == "system"]
    except Exception:  # noqa: BLE001
        return []


async def main():
    server = broker_mod.Server.__new__(broker_mod.Server)
    fake = FakeBroker()
    server.broker = fake
    server.threads = engine_mod.ThreadEngine(fake)
    server.threads.turns[("t-abc", "ada")] = {"persona": "ada",
                                               "started_at": "2026-11-13T20:01:00Z"}
    tcp = await asyncio.start_server(server.handle, "127.0.0.1", 0)
    port = tcp.sockets[0].getsockname()[1]
    try:
        # -- the profile ------------------------------------------------------
        status, body = await http(port, "GET", "/v1/agents/ada")
        check("GET /v1/agents/ada answers 200", status == 200, body)
        check("identity: display, face, the id's own as defaults",
              body["identity"]["display"] == "Ada" and body["identity"]["emoji"] == "🧱"
              and body["identity"]["default_display"] == "Ada"
              and body["identity"]["default_emoji"] == "🧱", body["identity"])
        d = body["defaults"]
        check("defaults: nothing set, Claude Code effective, wakes on fable",
              d["provider"] is None and d["model"] is None
              and d["effective"]["provider"] == "claude-code"
              and d["wakes"]["model"] == "fable" and d["not_honoured"] == [], d)
        f = body["files"]
        check("files: soul, config, three memory files, as Files paths",
              f["soul"] == {"root": "spaces", "path": "ada/soul.md", "exists": True}
              and f["memory"]["always"]["path"] == "ada/_always.md"
              and f["memory"]["reflection"]["exists"], f)
        check("files: skills counted (a folder and a file; _ is not one)",
              f["skills"]["count"] == 2 and f["skills"]["path"] == "ada/skills",
              f["skills"])
        sched = body["schedule"]
        check("schedule: the wake first, labelled, in plain words",
              [r["name"] for r in sched] == ["reflection", "infra-watch"]
              and sched[0]["label"] == "Nightly wake"
              and sched[0]["plain"] == "Every day at 3:20 AM"
              and sched[1]["label"] == "Infra watch"
              and sched[1]["plain"] == "Wednesdays at 5:00 AM", sched)
        check("schedule rows are the schedules route's (enabled, next_fire, file)",
              sched[0]["enabled"] is True and sched[0]["next_fire"]
              and sched[1]["file"]["path"] == "ada/crons/infra-watch.md", sched[0])
        st = body["status"]
        check("status: the thread in flight, the job it briefed, next wake",
              st["threads"] and st["threads"][0]["thread"] == "t-abc"
              and st["jobs"] and st["jobs"][0]["job"] == "20261113-x"
              and st["next_wake"] == sched[0]["next_fire"], st)
        ids = [p["id"] for p in body["plugins"]]
        check("plugins: both agent.profile plugins, with their values",
              ids == ["loose", "voices"]
              and body["plugins"][1]["values"] == {"voice": "Kore", "style": None},
              body["plugins"])
        status, body = await http(port, "GET", "/v1/agents/nobody")
        check("an agent nobody has is a 404", status == 404, body)

        # -- identity ---------------------------------------------------------
        status, body = await http(port, "PATCH", "/v1/agents/ada",
                                  {"display": "  Clayton  ", "emoji": "🦊"})
        check("PATCH display and emoji: 200, the profile back",
              status == 200 and body["identity"]["display"] == "Clayton"
              and body["identity"]["emoji"] == "🦊", body.get("identity", body))
        text = (LABS / "ada" / "agent.json").read_text()
        check("agent.json in its one order, the id unchanged",
              text == profile.render_agent(card("ada")) and card("ada")["name"] == "ada"
              and list(card("ada"))[:3] == ["name", "display", "emoji"], text)
        check("an @ matches the new name and the id",
              threads.mentions("@Clayton hi") == ["ada"]
              and threads.mentions("@ada hi") == ["ada"])
        status, body = await http(port, "GET", "/v1/agents")
        check("the cards say the new name",
              {"id": "ada", "name": "Clayton", "emoji": "🦊", "face": None} in body["agents"], body)
        before = text
        for patch, code, label in (
                ({"display": "Iris"}, 409, "another agent's name"),
                ({"display": "iris"}, 409, "another agent's id"),
                ({"display": "all"}, 400, "a word the threads keep"),
                ({"display": ""}, 400, "an empty name"),
                ({"emoji": "a b"}, 400, "a face with a space"),
                ({"colour": "red"}, 400, "a field there is none of"),
                ({}, 400, "nothing named")):
            status, body = await http(port, "PATCH", "/v1/agents/ada", patch)
            check(f"refused: {label} ({code}), nothing written",
                  status == code and (LABS / "ada" / "agent.json").read_text() == before,
                  body)
        status, body = await http(port, "PATCH", "/v1/agents/ada",
                                  {"display": None, "emoji": None})
        check("null puts the id's name and the atlas face back",
              status == 200 and card("ada")["display"] == "Ada"
              and card("ada")["emoji"] == "🧱", body.get("identity", body))

        # -- defaults ---------------------------------------------------------
        status, body = await http(port, "PATCH", "/v1/agents/ada",
                                  {"model": "opus", "effort": "high"})
        d = body.get("defaults", {})
        check("model and thinking level on Claude Code: stored and effective",
              status == 200 and card("ada").get("model") == "opus"
              and card("ada").get("effort") == "high"
              and d["effective"] == {"provider": "claude-code", "model": "opus",
                                     "effort": "high"}
              and d["wakes"] == {"provider": "claude-code", "model": "opus",
                                 "effort": "high"}, d)
        check("a wake runs on them (lane_choice), a pinned probe on its own",
              profile.lane_choice(card("ada"), "fable") == ("opus", "high", None)
              and profile.lane_choice(card("ada"), "haiku", pinned=True)[0] == "haiku")
        check("a thread turn reads them (persona_defaults)",
              threads.persona_defaults("ada") == {"provider": "claude-code",
                                                   "model": "opus", "effort": "high"})
        status, body = await http(port, "PATCH", "/v1/agents/ada",
                                  {"model": "not a model!"})
        check("a model the provider does not take is a 400, nothing written",
              status == 400 and card("ada")["model"] == "opus", body)
        status, body = await http(port, "PATCH", "/v1/agents/ada", {"effort": "huge"})
        check("a level the provider does not take is a 400",
              status == 400 and card("ada")["effort"] == "high", body)
        status, body = await http(port, "PATCH", "/v1/agents/ada", {"provider": "codex"})
        d = body.get("defaults", {})
        check("a provider change clears the model and level; wakes run on Codex",
              status == 200 and card("ada").get("provider") == "codex"
              and "model" not in card("ada") and "effort" not in card("ada")
              and d["wakes"] == {"provider": "codex", "model": None, "effort": None}
              and d["not_honoured"] == [], d)
        check("a thread member on codex starts on Codex (default_engine)",
              threads.default_engine("ada") == "codex")
        check("a Codex wake takes the persona's Codex model and level (lane_choice)",
              profile.lane_choice({"provider": "codex", "model": "gpt-x",
                                   "effort": "high"}, "fable")
              == ("gpt-x", "high", None)
              and profile.lane_provider({"provider": "codex"}) == "codex")
        status, body = await http(port, "PATCH", "/v1/agents/ada",
                                  {"provider": "antigravity"})
        d = body.get("defaults", {})
        check("Antigravity: wakes still on Claude Code, and the profile says why",
              status == 200 and d["wakes"]["provider"] == "claude-code"
              and d["wakes"]["model"] == "fable" and d["not_honoured"]
              and "Antigravity" in d["not_honoured"][0], d)
        status, body = await http(port, "PATCH", "/v1/agents/ada",
                                  {"provider": "chatgpt"})
        check("a provider there is none of is a 400", status == 400, body)
        status, body = await http(port, "PATCH", "/v1/agents/ada", {"provider": None})
        check("provider null: back to Claude Code, the key gone",
              status == 200 and "provider" not in card("ada"), card("ada"))

        # provision keeps what was set
        kept = profile.kept({**card("ada"), "display": "Clayton", "model": "opus"})
        check("provision keeps display, emoji and the defaults (kept)",
              kept == {"display": "Clayton", "emoji": "🧱", "model": "opus"}, kept)

        # -- a persona, through bin/settings ----------------------------------
        out = await cli(port, "set", "agent.model", "sonnet", "--target", "iris")
        check("bin/settings set agent.model as Ada: read back",
              out.get("ok") and out.get("value") == "sonnet"
              and card("iris").get("model") == "sonnet", out)
        check("...and said in General",
              "Ada changed Iris's model to sonnet" in general_lines(), general_lines())
        out = await cli(port, "set", "agent.effort", "low", "--target", "iris",
                        lane="cron:infra-watch")
        check("from an unattended lane: refused before any call",
              not out.get("ok") and "conversation" in out.get("error", "")
              and "effort" not in card("iris"), out)
        out = await cli(port, "set", "agent.display", "Violet", "--target", "iris")
        check("bin/settings set agent.display: read back",
              out.get("ok") and out.get("value") == "Violet", out)
        out = await cli(port, "describe", "agent.provider")
        check("describe agent.provider lists the engines",
              out.get("setting", {}).get("allowed") == list(threads.ENGINES), out)
        out = await cli(port, "describe", "persona.provider")
        check("persona.provider is retired", not out.get("ok"), out)

        # -- engineers --------------------------------------------------------
        status, body = await http(port, "GET", "/v1/engineers")
        rows = {r["project"]: r for r in body.get("engineers", [])}
        check("GET /v1/engineers: Loam first, Claude Code only, built-in model",
              status == 200 and body["engineers"][0]["project"] == "Loam"
              and rows["Loam"]["providers"] == ["claude-code"]
              and rows["Loam"]["effective"]["model"] == eng.ENGINEER_MODEL, body)
        check("a project on its record: Codex, its built-in model and level",
              rows["webapp"]["effective"] == {"provider": "codex",
                                              "model": eng.CODEX_ENGINEER_MODEL,
                                              "effort": eng.CODEX_ENGINEER_EFFORT,
                                              "source": "record"}
              and rows["webapp"]["deploy_on_land"] is False
              and rows["prproj"]["deploy_on_land"] is None
              and rows["webapp"]["jobs_running"] == 1, rows.get("webapp"))
        status, body = await http(port, "PATCH", "/v1/engineers/Loam",
                                  {"provider": "codex"})
        check("Loam's engineer on Codex is a 400", status == 400, body)
        status, body = await http(port, "PATCH", "/v1/engineers/Loam",
                                  {"model": "claude-opus-5-5", "effort": "max"})
        loam = eng.engineer_choice(eng.platform_project())
        check("Loam's model and level: what its next job runs on",
              status == 200 and loam == {"runtime": "claude", "model": "claude-opus-5-5",
                                         "effort": "max", "source": "own"}, loam)
        status, body = await http(port, "PATCH", "/v1/engineers/webapp",
                                  {"provider": "claude-code", "model": "opus",
                                   "effort": "high"})
        web = eng.projects()["webapp"]
        check("webapp on Claude Code: runtime, model and level for its next job",
              status == 200 and web.runtime == "claude"
              and eng.choice_for(web, "claude") == ("opus", "high"), body)
        check("...a continuation on Codex runs on Codex's built-in",
              eng.choice_for(web, "codex") == (eng.CODEX_ENGINEER_MODEL,
                                               eng.CODEX_ENGINEER_EFFORT))
        status, body = await http(port, "PATCH", "/v1/engineers/default",
                                  {"provider": "claude-code"})
        pr = eng.projects()["prproj"]
        check("the default: a project without its own follows it",
              status == 200 and eng.engineer_choice(pr)["source"] == "default"
              and pr.runtime == "claude"
              and eng.engineer_choice(eng.platform_project())["source"] == "own",
              eng.engineer_choice(pr))
        status, body = await http(port, "GET", "/v1/engineers/default")
        check("GET /v1/engineers/default answers the default",
              status == 200 and body["own"]["provider"] == "claude-code", body)
        status, body = await http(port, "PATCH", "/v1/engineers/webapp",
                                  {"deploy_on_land": True})
        check("deploy_on_land on a project with an app: what the launcher reads",
              status == 200 and eng.deploy_default(eng.projects()["webapp"]), body)
        for name, patch, label in (("prproj", {"deploy_on_land": True}, "landing by PR"),
                                   ("Loam", {"deploy_on_land": True}, "Loam"),
                                   ("default", {"deploy_on_land": True}, "the default"),
                                   ("webapp", {"provider": "antigravity"}, "Antigravity"),
                                   ("webapp", {"speed": 1}, "a setting there is none of")):
            status, body = await http(port, "PATCH", f"/v1/engineers/{name}", patch)
            check(f"refused: {label}", status == 400, body)
        status, body = await http(port, "PATCH", "/v1/engineers/nope", {"provider": None})
        check("an engineer nobody has is a 404", status == 404, body)
        status, body = await http(port, "PATCH", "/v1/engineers/webapp", {"provider": None})
        web = eng.projects()["webapp"]
        check("provider null: back on the default, deploy_on_land kept",
              status == 200 and eng.engineer_choice(web)["source"] == "default"
              and eng.deploy_default(web), eng.engineer_settings())
        out = await cli(port, "set", "engineer.effort", "medium", "--target", "prproj")
        check("bin/settings set engineer.effort: read back, said in General",
              out.get("ok") and out.get("value") == "medium"
              and "Ada changed the prproj engineer's effort to medium" in general_lines(),
              out)

        # -- the reviewer -----------------------------------------------------
        status, body = await http(port, "GET", "/v1/system/reviewer")
        check("GET /v1/system/reviewer: 04:20 on fable, Claude Code",
              status == 200 and body["schedule"] == "daily at 04:20 PT"
              and body["plain"] == "Every day at 4:20 AM"
              and body["effective"]["model"] == "fable", body)
        SYSTEMD.calls.clear()
        status, body = await http(port, "PATCH", "/v1/system/reviewer",
                                  {"schedule": "Mondays at 05:00 PT", "effort": "high"})
        import loam_contributions as contrib  # noqa: PLC0415
        timer = contrib.desired_units().get(profile.REVIEWER_TIMER, "")
        check("a schedule: the plugin's setting, and its timer follows it",
              status == 200 and body.get("on") is True
              and contrib.setting_values("reviewer").get("schedule") == "Mondays at 05:00 PT"
              and "OnCalendar=Mon *-*-* 05:00:00 America/Los_Angeles\n" in timer
              and "schedule" not in profile.reviewer_stored(), (body, timer))
        check("...it reads back in plain words, and bin/review's choice has the level",
              body.get("plain") == "Mondays at 5:00 AM"
              and profile.reviewer_choice("fable") == ("fable", "high"), body)
        for patch, label in (({"schedule": "sometimes"}, "a schedule that does not parse"),
                             ({"provider": "codex"}, "another provider"),
                             ({"model": "gpt-6"}, "a model Claude Code does not take")):
            status, body = await http(port, "PATCH", "/v1/system/reviewer", patch)
            check(f"refused: {label}", status == 400, body)
        status, body = await http(port, "PATCH", "/v1/system/reviewer",
                                  {"schedule": "daily at 04:20 PT"})
        timer = contrib.desired_units().get(profile.REVIEWER_TIMER, "")
        check("the default schedule puts the timer back at 04:20",
              status == 200 and "OnCalendar=*-*-* 04:20:00 America/Los_Angeles\n" in timer
              and "schedule" not in profile.reviewer_stored(), timer)
        import loam_plugins as lp  # noqa: PLC0415
        lp.set_on("reviewer", False, None)
        status, body = await http(port, "PATCH", "/v1/system/reviewer",
                                  {"schedule": "Mondays at 05:00 PT"})
        check("with the reviewer plugin off, a schedule is a 409 and no timer is wanted",
              status == 409 and profile.REVIEWER_TIMER not in contrib.desired_units(), body)
        lp.set_on("reviewer", True, None)

        # -- plugins ----------------------------------------------------------
        status, body = await http(port, "GET", "/v1/plugins/voices")
        check("agent_settings and agent.profile are spec, not warnings",
              status == 200 and body["plugin"]["warnings"] == []
              and body["plugin"]["agent_settings"][0]["id"] == "voice", body)
        status, body = await http(port, "PATCH", "/v1/plugins/voices/agents/ada",
                                  {"values": {"voice": "Puck", "style": "warm"}})
        stored = json.loads((TMP / "yours" / "_settings" / "voices" /
                             "agents.json").read_text())
        check("PATCH a plugin's values for an agent: stored in Yours' _settings",
              status == 200 and body["values"] == {"voice": "Puck", "style": "warm"}
              and stored == {"version": 1, "agents": {"ada": {"voice": "Puck",
                                                               "style": "warm"}}}, body)
        for values, label in (({"voice": "Nova"}, "a choice not offered"),
                              ({"pitch": "low"}, "a field not declared"),
                              ({"style": 3}, "a number for a text field")):
            status, body = await http(port, "PATCH", "/v1/plugins/voices/agents/ada",
                                      {"values": values})
            check(f"refused: {label}", status == 400, body)
        status, body = await http(port, "PATCH", "/v1/plugins/voices/agents/nobody",
                                  {"values": {"voice": "Kore"}})
        check("an agent nobody has is a 404", status == 404, body)
        status, body = await http(port, "GET", "/v1/plugins/voices/agents")
        check("GET every agent's values",
              status == 200 and body["agents"] == {"ada": {"voice": "Puck",
                                                            "style": "warm"}}, body)
        status, body = await http(port, "PATCH", "/v1/plugins/loose/agents/iris",
                                  {"values": {"anything": "goes", "count": 3}})
        check("a plugin that declares nothing keeps any slug",
              status == 200 and body["values"] == {"anything": "goes", "count": 3}, body)
        status, body = await http(port, "PATCH", "/v1/plugins/voices/agents/ada",
                                  {"values": {"voice": None}})
        check("null puts a value back to its default",
              status == 200 and body["values"]["voice"] == "Kore", body)
        status, body = await http(port, "GET", "/v1/agents/ada")
        check("the profile's plugin rows carry the agent's values",
              [p["values"] for p in body["plugins"] if p["id"] == "voices"]
              == [{"voice": "Kore", "style": "warm"}], body["plugins"])
        out = await cli(port, "set", "plugin.agent", '{"voice": "Puck"}',
                        "--target", "voices/iris")
        check("bin/settings set plugin.agent: read back",
              out.get("ok") and out.get("value", {}).get("voice") == "Puck", out)

        status, body = await http(port, "GET", "/v1/settings")
        keys = {r["key"] for r in body["settings"]}
        check("GET /v1/settings lists the new keys",
              {"agent.display", "agent.provider", "engineer.deploy_on_land",
               "reviewer.schedule", "plugin.agent"} <= keys
              and "persona.provider" not in keys)
    finally:
        tcp.close()
        await tcp.wait_closed()


asyncio.run(main())
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
