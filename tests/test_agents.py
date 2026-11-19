#!/usr/bin/env python3
"""Making, archiving and restoring agents, and their faces (2026-11-14, first
release L10), through a real broker listener on loopback and through
`bin/settings` as a persona. lib/loam_agents.py holds the rules.

  - New agent opens a thread with the starter agent, with a line saying how
  - a draft: a persona's needs its thread; a name or id taken, a reserved id
    and a missing face are `problems`; each revision is numbered and said in
    the thread; souls can come from a file in a workspace, not from /etc
  - confirm: refused without the person's yes, with a yes that is not the
    owner's, older than the revision, from a thread the agent is not in, for
    a stale revision, or from an unattended lane; on a good yes the agent is
    made (files verbatim, agent.json's name and face, provisioned, its unit
    on), on the roster, in /v1/agents, with a first thread; the yes is taken
    once
  - archive: out of /v1/agents, threads' roster and mentions, in
    ?archived=1; its timers are removed and its records untouched; its
    profile and files stay readable; a turn for it is refused with a line;
    an agent mid-turn waits; a persona's archive needs a fresh yes
  - restore: the same timers back byte for byte, back in /v1/agents
  - the face: multipart and a path, PNG/JPEG/WebP/GIF only, served back,
    on the card; null goes back to the theme's; provision keeps face and the
    archive mark
  - the emoji face is retired (2026-11-16): a draft refuses it, a made
    agent has none, and an older agent.json's is kept but never served
  - no route deletes an agent

Run: python3 tests/test_agents.py
"""

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="loam-agents-test-"))
LABS = TMP / "ai-labs"
os.environ["LOAM_VAULT"] = str(TMP / "vault")
os.environ["LOAM_AI_LABS"] = str(LABS)
os.environ["LOAM_THREADS_STATE"] = str(TMP / "state")
os.environ["LOAM_AGENTS_STATE"] = str(TMP / "agents-state")
os.environ["LOAM_HOST_SETTINGS"] = str(TMP / "broker" / "settings.json")
os.environ["LOAM_SCHEDULE_STATE"] = str(TMP / "schedules")
os.environ["LOAM_PLUGIN_SHIPPED"] = str(TMP / "shipped")
os.environ["LOAM_PLUGINS_DIR"] = str(TMP / "yours")
os.environ["LOAM_UI_CORE_OPTIONS"] = "none"
os.environ["LOAM_UI_APPEARANCE_REGISTRY"] = "none"
os.environ.pop("LOAM_WAKE", None)

for who, emoji in (("ada", "🧱"), ("iris", "🪻")):
    ws = LABS / who
    (ws / "crons").mkdir(parents=True)
    for f in ("soul.md", "config.md", "_always.md", "_active.md", "_reflection.md"):
        (ws / f).write_text(f"{who} {f}\n")
    (ws / "agent.json").write_text(json.dumps({
        "name": who, "display": who.capitalize(), "emoji": emoji,
        "workspace": str(ws), "vault": str(TMP / "vault"),
        "wake_schedule": "03:20", "wake_timezone": "America/Los_Angeles"},
        indent=2, ensure_ascii=False) + "\n")
    (ws / "crons" / "reflection.md").write_text(
        "---\nlane: reflection\nschedule: daily at 03:20 PT\nenabled: true\n"
        "reason: the test's fixture\n---\nThe nightly reflection wake.\n")
(LABS / "ada" / "crons" / "infra-watch.md").write_text(
    "---\nschedule: Wednesdays at 05:00 PT\nenabled: true\n"
    "reason: the test's fixture\n---\n# Cron\nWatch.\n")
(TMP / "vault" / "15_CONVERSATIONS").mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents").mkdir(parents=True)
(TMP / "vault" / "00_INBOX" / "from-agents" / "comms.md").write_text("# From agents\n")
(TMP / "shipped").mkdir()
(TMP / "yours").mkdir()

sys.path.insert(0, str(AGENTS / "lib"))
import loam_broker as cfg  # noqa: E402
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}
cfg.AI_LABS = LABS
cfg.VAULT = TMP / "vault"
# And the install's vault as loam_agents reads it: a fresh install's has an
# About me.md, which the make would read (found 2026-11-18).
import loam_config  # noqa: E402
loam_config.VAULT = TMP / "vault"
import loam_threads as threads  # noqa: E402
import loam_cron  # noqa: E402
import loam_schedule as ls  # noqa: E402
import loam_activity as activity  # noqa: E402
import loam_profile as profile  # noqa: E402
import loam_agents  # noqa: E402
import loam_loading  # noqa: E402

UNITS = TMP / "units"
UNITS.mkdir()
loam_cron.AI_LABS = LABS
loam_cron.UNIT_DIR = UNITS
ls.STATE = TMP / "schedules"
ls.WAKE_STATE = TMP / "wake"
ls.BROKER_SESSIONS = TMP / "sessions"
ls.WRITE_PAGE = False
activity.WAKE_LOGS = TMP / "logs"
activity.live_jobs = lambda: {"slots": [], "queue": [], "landing": []}


class FakeSystemd(ls.Systemd):
    def __init__(self):
        super().__init__(unit_dir=UNITS, runtime=True)
        self.calls = []

    def run(self, *args):
        self.calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")


SYSTEMD = FakeSystemd()
activity.SYSTEMD = SYSTEMD
profile.SYSTEMD = SYSTEMD
for who in ("ada", "iris"):
    ls.reconcile([who], systemd=SYSTEMD)

# The box's seams: provision writes what bin/provision would that the routes
# read (the reflection record, through the schedule path), the units record.
PROVISIONED, UNIT_CALLS = [], []


def fake_provision(agent, wake):
    PROVISIONED.append((agent, wake))
    ws = LABS / agent
    (ws / "CLAUDE.md").write_text(f"You are {agent.capitalize()}\n")
    ls.change(agent, "reflection", ls.reflection_record(wake), actor="engineer",
              lane="engineer", reason=f"provisioned with its reflection at {wake}",
              systemd=SYSTEMD)
    return f"provisioned {agent}"


loam_agents._provision = fake_provision
loam_agents._units = lambda agent, on: (UNIT_CALLS.append((agent, on)) or
                                        ("active" if on else "inactive"))
loam_agents._systemctl = lambda *a: subprocess.CompletedProcess(a, 0, "enabled\n", "")
loam_agents._reconcile = lambda agent: ls.reconcile([agent], systemd=SYSTEMD)

loader = importlib.machinery.SourceFileLoader(
    "session_broker", str(AGENTS / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker_mod = importlib.util.module_from_spec(spec)
loader.exec_module(broker_mod)
engine_mod = broker_mod.loam_thread_engine

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:72}"
          f"{'  | ' + str(detail)[:300] if detail != '' else ''}")


class FakeBroker:
    def __init__(self):
        self.sessions = {}
        self.token = "test-token"
        self.stopping = False

    async def start_session(self, *a, **k):
        raise AssertionError("no turn is started in this test")


TOKEN = TMP / "token"
TOKEN.write_text("test-token")
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
       b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f"
       b"\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


async def http(port, method, path, body=None, raw=None, ctype="application/json"):
    import urllib.error  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    def go():
        data = raw if raw is not None else (
            json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", method=method, data=data,
            headers={"Authorization": "Bearer test-token", "Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                got = r.read()
                if r.headers.get("Content-Type", "").startswith("image/"):
                    return r.status, got
                return r.status, json.loads(got)
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())
    return await asyncio.to_thread(go)


async def cli(port, *argv, who="iris", lane=None):
    env = dict(os.environ, LOAM_BROKER_URL=f"http://127.0.0.1:{port}",
               LOAM_BROKER_TOKEN_FILE=str(TOKEN), LOAM_AGENT=who)
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


def system_lines(tid):
    return [m["text"] for m in threads.read(tid)[1] if m.get("kind") == "system"]


def unit_files(agent):
    return {p.name: p.read_text() for p in sorted(UNITS.glob(f"*{agent}*"))}


def multipart(data, name="face.png", ctype="image/png"):
    boundary = "loamtestboundary"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{name}\"\r\nContent-Type: {ctype}\r\n\r\n").encode() \
        + data + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


async def main():
    server = broker_mod.Server.__new__(broker_mod.Server)
    fake = FakeBroker()
    server.broker = fake
    server.threads = engine_mod.ThreadEngine(fake)
    tcp = await asyncio.start_server(server.handle, "127.0.0.1", 0)
    port = tcp.sockets[0].getsockname()[1]
    try:
        # -- New agent -------------------------------------------------------
        status, body = await http(port, "POST", "/v1/agents/new", {})
        check("POST /v1/agents/new opens a thread with the starter agent",
              status == 201 and body.get("default") == "iris", body)
        tid = body["id"]
        check("...with a line saying how, naming the thread",
              any("agent.draft" in t and tid in t for t in system_lines(tid)),
              system_lines(tid))

        # -- drafting --------------------------------------------------------
        out = await cli(port, "do", "agent.draft",
                        json.dumps({"name": "Juniper",
                                    "soul": "I am Juniper.", "config": "Config."}))
        check("a persona's draft needs its thread", not out.get("ok")
              and "thread" in out.get("error", ""), out)
        out = await cli(port, "do", "agent.draft",
                        json.dumps({"thread": tid, "name": "Ada", "soul": "x",
                                    "config": "y"}))
        probs = out.get("answer", {}).get("problems", [])
        check("a taken name is the draft's problem, and a face is not asked for",
              out.get("ok") and any("ada" in p.lower() for p in probs)
              and not any("face" in p for p in probs), out)
        out = await cli(port, "do", "agent.draft",
                        json.dumps({"thread": tid, "name": "Juniper", "emoji": "🌿",
                                    "soul": "s", "config": "c"}))
        check("a draft with an emoji face is refused: retired",
              not out.get("ok") and "retired" in out.get("error", ""), out)
        (LABS / "iris" / "draft-soul.md").write_text("# Juniper\n\nI am Juniper. I tend things.\n")
        out = await cli(port, "do", "agent.draft",
                        json.dumps({"thread": tid, "name": "Juniper",
                                    "soul_file": "draft-soul.md",
                                    "config": "Juniper's config.\n"}))
        ans = out.get("answer", {})
        draft = ans.get("draft")
        check("a draft from a soul file in the drafter's workspace is ready",
              out.get("ok") and ans.get("ready") and ans.get("id") == "juniper"
              and ans.get("rev") == 1 and ans.get("problems") == [], out)
        check("...and is said in the thread with its revision",
              any(draft in t and "revision 1" in t for t in system_lines(tid)),
              system_lines(tid))
        out = await cli(port, "do", "agent.draft",
                        json.dumps({"thread": tid, "name": "Etc",
                                    "soul_file": "/etc/hostname", "config": "c"}))
        check("a soul file outside the workspaces and vault is refused",
              not out.get("ok") and "workspace" in out.get("error", ""), out)
        status, body = await http(port, "POST", "/v1/agents/drafts",
                                  {"name": "Commons", "id": "commons",
                                   "soul": "s", "config": "c"})
        check("a reserved id is a problem",
              status == 201 and any("keeps for itself" in p for p in body["problems"]),
              body)
        status, body = await http(port, "POST", "/v1/agents/drafts",
                                  {"name": "Sage", "soul": "s", "config": "c",
                                   "about": "  A calm   gardener who notices things.  "})
        check("a draft keeps its plain `about` line, tidied, for the card",
              status == 201 and body.get("about") == "A calm gardener who notices things.", body)
        status, body = await http(port, "POST", "/v1/agents/drafts",
                                  {"name": "Sage", "soul": "s", "config": "c",
                                   "about": "x" * 201})
        check("...and an `about` longer than one line is refused",
              status == 400 and "about" in body.get("error", ""), (status, body))

        # The person speaks, then the draft is revised: that yes is too old.
        early = server.threads.post_person(tid, "Sounds nice")
        await asyncio.sleep(1.1)
        out = await cli(port, "do", "agent.revise",
                        json.dumps({"config": "Juniper's own config.\n"}), "--target", draft)
        check("a revision is numbered", out.get("ok")
              and out["answer"].get("rev") == 2
              and out["answer"].get("config") == "Juniper's own config.\n", out)
        out = await cli(port, "do", "agent.revise",
                        json.dumps({"emoji": "🌲"}), "--target", draft)
        check("...and a revision with an emoji is refused: retired",
              not out.get("ok") and "retired" in out.get("error", ""), out)

        # -- confirm ---------------------------------------------------------
        out = await cli(port, "do", "agent.confirm", json.dumps({"rev": 2}),
                        "--target", draft)
        check("confirm without a yes is refused", not out.get("ok")
              and "yes" in out.get("error", ""), out)
        out = await cli(port, "do", "agent.confirm",
                        json.dumps({"rev": 2, "yes": early["id"]}), "--target", draft)
        check("a yes older than the revision is refused", not out.get("ok")
              and "before this version" in out.get("error", ""), out)
        mine = threads.append(tid, "iris", "Here is Juniper. Shall I make them?")
        out = await cli(port, "do", "agent.confirm",
                        json.dumps({"rev": 2, "yes": mine["id"]}), "--target", draft)
        check("a yes that is the agent's own message is refused", not out.get("ok")
              and f"is not {loam_agents.lc.OWNER_NAME}'s" in out.get("error", ""), out)
        yes = server.threads.post_person(tid, "Yes, make Juniper")
        out = await cli(port, "do", "agent.confirm",
                        json.dumps({"rev": 1, "yes": yes["id"]}), "--target", draft)
        check("a stale revision is refused", not out.get("ok")
              and "revision 2" in out.get("error", ""), out)
        out = await cli(port, "do", "agent.confirm",
                        json.dumps({"rev": 2, "yes": yes["id"]}), "--target", draft,
                        who="ada")
        check("a yes from a thread the agent is not in is refused",
              not out.get("ok") and "not in thread" in out.get("error", ""), out)
        out = await cli(port, "do", "agent.confirm",
                        json.dumps({"rev": 2, "yes": yes["id"]}), "--target", draft,
                        lane="reflection")
        check("an unattended lane is refused before the route",
              not out.get("ok") and "unattended" in out.get("error", ""), out)
        check("nothing was made by any refusal",
              not (LABS / "juniper").exists() and PROVISIONED == [], PROVISIONED)
        out = await cli(port, "do", "agent.confirm",
                        json.dumps({"rev": 2, "yes": yes["id"]}), "--target", draft)
        ans = out.get("answer", {})
        check("on the person's yes the agent is made",
              out.get("ok") and ans.get("agent", {}).get("id") == "juniper"
              and ans.get("first_thread"), out)
        ws = LABS / "juniper"
        check("the soul is the draft's, verbatim",
              (ws / "soul.md").read_text() == "# Juniper\n\nI am Juniper. I tend things.\n")
        made_config = (ws / "config.md").read_text()
        # Born in the map's shape (2026-11-18): the config is the draft's
        # own, as written (the owner's words for this agent; Loam's text and
        # the shared page load beside it), and a map, and none of the three
        # old loading files.
        check("the config is the draft's own, verbatim (no fleet clauses)",
              made_config == "Juniper's own config.\n", made_config)
        check("a map, seeded with its one line",
              (ws / "map.md").read_text() == loam_loading.MAP_SEED)
        check("none of _always.md, _active.md, _reflection.md",
              not any((ws / f).exists() for f in
                      ("_always.md", "_active.md", "_reflection.md")))
        check("the owner's shared page exists for its import",
              loam_loading.owner_page().is_file()
              and loam_loading.owner_page().is_relative_to(LABS))
        check("agent.json carries its name and no emoji, in the one order",
              card("juniper")["display"] == "Juniper" and "emoji" not in card("juniper")
              and list(card("juniper"))[:3] == ["name", "display", "workspace"], card("juniper"))
        check("provisioned at a free wake, its unit turned on",
              PROVISIONED == [("juniper", card("juniper")["wake_schedule"])]
              and card("juniper")["wake_schedule"] != "03:20"
              and ("juniper", True) in UNIT_CALLS, (PROVISIONED, UNIT_CALLS))
        status, body = await http(port, "GET", "/v1/agents")
        check("it is in /v1/agents (Chats and every picker)",
              {"id": "juniper", "name": "Juniper", "face": None}
              in body["agents"], body)
        check("...and no row serves an emoji, an older agent.json's included",
              all("emoji" not in a for a in body["agents"])
              and card("ada").get("emoji") == "🧱", body)
        first = ans.get("first_thread")
        meta = threads.read(first)[0]
        check("its first thread is its own, and says it was made on a yes",
              meta["default"] == "juniper"
              and any(yes["id"] in t for t in system_lines(first)), system_lines(first))
        check("the yes is recorded", any(e.get("kind") == "created"
              and e.get("yes", {}).get("message") == yes["id"]
              for e in loam_agents.events()))
        check("its wake timer stands", "loam-wake-juniper.timer" in unit_files("juniper"),
              unit_files("juniper"))
        out = await cli(port, "do", "agent.confirm",
                        json.dumps({"rev": 2, "yes": yes["id"]}), "--target", draft)
        check("a made draft is not made twice", not out.get("ok"), out)
        out = await cli(port, "do", "agent.archive",
                        json.dumps({"thread": tid, "yes": yes["id"]}),
                        "--target", "juniper")
        check("a yes is taken once", not out.get("ok")
              and "taken once" in out.get("error", ""), out)

        # -- the skill's order: write the draft, show it, one yes (2026-11-17)
        # A scratch run on 11-16 showed the person a summary before any
        # draft existed, so their yes came before the draft and they had to
        # say it twice. loam-making-an-agent now says: draft, then show.
        status, body = await http(port, "POST", "/v1/agents/new", {})
        t1 = body["id"]
        out = await cli(port, "do", "agent.draft",
                        json.dumps({"thread": t1, "name": "Wren", "soul": "# Wren\n\nDry.\n",
                                    "config": "Wren's config.\n"}))
        d1 = out.get("answer", {}).get("draft")
        threads.append(t1, "iris", "Here is Wren, as drafted: dry, direct. Shall I make her?")
        only = server.threads.post_person(t1, "Yes, make Wren")
        out = await cli(port, "do", "agent.confirm",
                        json.dumps({"rev": 1, "yes": only["id"]}), "--target", d1)
        check("draft, then show, then one yes: the agent is made on it",
              out.get("ok") and out["answer"]["agent"]["id"] == "wren", out)
        check("...and the person said yes once",
              [m["text"] for m in threads.read(t1)[1]
               if loam_agents.lc.is_owner(m.get("author"))], ["Yes, make Wren"])

        # A yes, then a revision in the same second: the yes was to revision
        # 1 and does not make revision 2, which a one-second stamp alone
        # could not tell.
        status, body = await http(port, "POST", "/v1/agents/new", {})
        t2 = body["id"]
        out = await cli(port, "do", "agent.draft",
                        json.dumps({"thread": t2, "name": "Fern", "soul": "# Fern\n\nGentle.\n",
                                    "config": "Fern's config.\n"}))
        d2 = out.get("answer", {}).get("draft")
        threads.append(t2, "iris", "Here is Fern: gentle. Shall I make her?")
        first_yes = server.threads.post_person(t2, "Yes, but a bit less gentle")
        out = await cli(port, "do", "agent.revise",
                        json.dumps({"soul": "# Fern\n\nBlunt.\n"}), "--target", d2)
        check("a revision after the yes", out.get("ok") and out["answer"]["rev"] == 2, out)
        same_second = (loam_agents._utc(first_yes["at"])
                       >= loam_agents._utc(out["answer"]["updated"]))
        out = await cli(port, "do", "agent.confirm",
                        json.dumps({"rev": 2, "yes": first_yes["id"]}), "--target", d2)
        check("a stale yes does not make the revised draft"
              + (" (same second as the revision)" if same_second else ""),
              not out.get("ok") and "before this version" in out.get("error", ""), out)
        out = await cli(port, "do", "agent.confirm",
                        json.dumps({"rev": 1, "yes": first_yes["id"]}), "--target", d2)
        check("...nor does it make the old revision, once revised",
              not out.get("ok") and "revision 2" in out.get("error", ""), out)
        check("...nothing was made", not (LABS / "fern").exists())
        threads.append(t2, "iris", "Here is Fern again: blunt now. Make her?")
        second_yes = server.threads.post_person(t2, "Yes, that's her")
        out = await cli(port, "do", "agent.confirm",
                        json.dumps({"rev": 2, "yes": second_yes["id"]}), "--target", d2)
        check("an explicit yes to the revised draft makes it",
              out.get("ok") and out["answer"]["agent"]["id"] == "fern"
              and (LABS / "fern" / "soul.md").read_text() == "# Fern\n\nBlunt.\n", out)

        # -- archive ---------------------------------------------------------
        clay_thread = threads.create("ada", "Ada's old thread")
        before = unit_files("ada")
        server.threads.turns[("t-busy", "ada")] = {"persona": "ada"}
        status, body = await http(port, "POST", "/v1/agents/ada/archive", {})
        check("an agent mid-turn is not archived under it", status == 409
              and "mid-turn" in body["error"], body)
        server.threads.turns.pop(("t-busy", "ada"))
        status, body = await http(port, "POST", "/v1/agents/ada/archive", {})
        check("the person's archive (no `by`) is its own yes",
              status == 200 and loam_agents.lc.is_owner(body["archived"]["by"])
              and "yes" not in body["archived"], body)
        status, body = await http(port, "GET", "/v1/agents")
        check("out of /v1/agents", "ada" not in [a["id"] for a in body["agents"]], body)
        status, body = await http(port, "GET", "/v1/agents?archived=1")
        check("in ?archived=1, with its mark",
              [a["id"] for a in body["agents"]] == ["ada"]
              and body["agents"][0]["archived"]["at"], body)
        check("out of the threads' roster and their mentions",
              "ada" not in threads.roster() and threads.mentions("@Ada hi") == [])
        check("its timers are removed", unit_files("ada") == {}, unit_files("ada"))
        check("its records are untouched",
              (LABS / "ada" / "crons" / "infra-watch.md").is_file()
              and ls.read_text(ls.applied_path("ada", "infra-watch")) is not None)
        check("its unit is turned off", ("ada", False) in UNIT_CALLS, UNIT_CALLS)
        status, body = await http(port, "GET", "/v1/agents/ada")
        check("its profile still opens, saying it is archived",
              status == 200 and body["identity"]["archived"], body.get("identity"))
        check("its files stay where they are",
              (LABS / "ada" / "soul.md").read_text() == "ada soul.md\n")
        check("the schedules list leaves it out; its own rows stay",
              all(r["agent"] != "ada" for r in activity.schedules())
              and activity.schedules("ada"))
        server.threads.post_person(clay_thread["id"], "Anyone?")
        for _ in range(50):
            if any("archived" in t for t in system_lines(clay_thread["id"])):
                break
            await asyncio.sleep(0.05)
        check("a turn that would be its is refused with a line",
              any("Ada is archived" in t for t in system_lines(clay_thread["id"])),
              system_lines(clay_thread["id"]))
        status, body = await http(port, "POST", "/v1/agents/ada/archive", {})
        check("archiving twice is a 409", status == 409, body)

        # -- restore ---------------------------------------------------------
        status, body = await http(port, "POST", "/v1/agents/ada/restore", {})
        check("restore answers 200", status == 200, body)
        check("the same timers are back, byte for byte", unit_files("ada") == before,
              (sorted(unit_files("ada")), sorted(before)))
        status, body = await http(port, "GET", "/v1/agents")
        check("back in /v1/agents", "ada" in [a["id"] for a in body["agents"]])
        check("the archive mark is gone", "archived" not in card("ada"))

        # A persona's archive: a fresh yes in a thread it is in.
        old = threads.read(tid)[1]
        out = await cli(port, "do", "agent.archive",
                        json.dumps({"thread": tid, "yes": early["id"]}),
                        "--target", "juniper")
        check("a persona's archive takes a yes within 30 minutes (one is fine here)",
              out.get("ok"), out)
        check("...said in General", any("archived Juniper" in t
              for t in system_lines(threads.GENERAL_ID)), system_lines(threads.GENERAL_ID))
        ok2 = server.threads.post_person(tid, "Bring Juniper back")
        out = await cli(port, "do", "agent.restore",
                        json.dumps({"thread": tid, "yes": ok2["id"]}),
                        "--target", "juniper")
        check("a persona's restore on a yes", out.get("ok"), out)
        del old

        # -- the face --------------------------------------------------------
        raw, ctype = multipart(PNG)
        status, body = await http(port, "PUT", "/v1/agents/iris/face", raw=raw, ctype=ctype)
        check("a PNG uploads as a face", status == 200 and body["face"]["type"] == "image/png"
              and (LABS / "iris" / "face.png").read_bytes() == PNG, body)
        url = body["face"]["url"]
        status, got = await http(port, "GET", url)
        check("the face is served back", status == 200 and got == PNG)
        status, body = await http(port, "GET", "/v1/agents")
        iris = [a for a in body["agents"] if a["id"] == "iris"][0]
        check("the card carries the face, and no emoji", iris["face"]
              and iris["face"]["url"] == url and "emoji" not in iris, iris)
        raw, ctype = multipart(b"<svg onload=alert(1)>", "x.svg", "image/svg+xml")
        status, body = await http(port, "PUT", "/v1/agents/iris/face", raw=raw, ctype=ctype)
        check("an SVG (or anything not PNG/JPEG/WebP/GIF) is a 415", status == 415, body)
        jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 64
        (LABS / "iris" / "me.jpg").write_bytes(jpeg)
        out = await cli(port, "set", "agent.face", str(LABS / "iris" / "me.jpg"),
                        "--target", "iris")
        check("an agent sets its own face from a file (bin/settings)",
              out.get("ok") and (out.get("value") or {}).get("type") == "image/jpeg"
              and not (LABS / "iris" / "face.png").exists(), out)
        out = await cli(port, "set", "agent.face", "/etc/hostname", "--target", "iris")
        check("...but not from a file outside the workspaces and vault",
              not out.get("ok"), out)
        kept = profile.kept(card("iris"))
        check("provision keeps the face (and would keep an archive mark)",
              "face" in kept and "archived" in profile.KEPT, kept)
        out = await cli(port, "set", "agent.display", "Irene", "--target", "iris")
        check("an agent renames itself through the same route",
              out.get("ok") and card("iris")["display"] == "Irene", out)
        status, body = await http(port, "DELETE", "/v1/agents/iris/face", {})
        check("DELETE goes back to the theme's face", status == 200 and body["face"] is None
              and "face" not in card("iris")
              and not (LABS / "iris" / "face.jpg").exists(), body)

        # -- the fleet's clauses: a made config passes check-drift's rule -------
        fleet_root = TMP / "fleet"
        for who in ("ash", "birch", "cedar"):
            (fleet_root / who).mkdir(parents=True)
            (fleet_root / who / "agent.json").write_text(json.dumps({"name": who}))
            (fleet_root / who / "config.md").write_text(
                f"# {who.capitalize()} — Config\n\nHard rules.\n\n"
                f"## Permission first\n\nAsk the owner before editing {who}'s soul.\n\n"
                f"## Where things live\n\nYour workspace is ai-labs/{who}.\n"
                + ("\n## Money\n\nNo trades.\n" if who == "ash" else ""))
        text, fleet = loam_agents.compose_config(
            "juniper", "# Juniper\n\nNever prune unasked.\n\n## Permission first\n\nmine\n",
            fleet_root)
        solo = TMP / "solo"
        (solo / "iris").mkdir(parents=True)
        (solo / "iris" / "agent.json").write_text(json.dumps({"name": "iris"}))
        (solo / "iris" / "config.md").write_text(
            "# Iris — Config\n\nHard rules.\n\n## Where things live\n\n"
            "Your workspace is `/x/agents/iris/`.\n")
        solo_text, _ = loam_agents.compose_config("wren", "Wren's own.\n", solo, name="Wren")
        check("a box with one agent: the next agent's config is its own name, not Iris's",
              solo_text.startswith("# Wren — Config") and "/x/agents/wren/" in solo_text
              and "iris" not in solo_text.lower(), solo_text)
        check("the fleet's clauses come first, a minority one is left out",
              fleet == ["Permission first", "Where things live"]
              and "Ask the owner before editing juniper's soul." in text
              and "ai-labs/juniper" in text and "No trades" not in text
              and "mine" not in text and text.endswith("Never prune unasked.\n"), text)
        (fleet_root / "juniper").mkdir()
        (fleet_root / "juniper" / "agent.json").write_text('{"name": "juniper"}')
        (fleet_root / "juniper" / "config.md").write_text(text)
        drift = loam_agents._drift_module()
        drift.AI_LABS = fleet_root
        found = []
        drift.check_config_clauses(found, ["ash", "birch", "cedar", "juniper"])
        check("check-drift finds nothing in the made config", found == [], found)

        # -- provision: no emoji to derive, and an older file's is kept ----------
        loader = importlib.machinery.SourceFileLoader("provision", str(AGENTS / "bin" / "provision"))
        pspec = importlib.util.spec_from_loader("provision", loader)
        prov = importlib.util.module_from_spec(pspec)
        loader.exec_module(prov)
        check("provision derives no emoji (retired 2026-11-16)",
              not hasattr(prov, "emoji_for"))
        before = (LABS / "ada" / "agent.json").read_text()
        check("...and an older agent.json's emoji renders back byte for byte",
              profile.render_agent({**json.loads(before), **profile.kept(json.loads(before))})
              == before, before)

        # -- a name with a space: the id is a slug, its own files say the name --
        status, body = await http(port, "POST", "/v1/agents/drafts",
                                  {"name": "Old Sam", "soul": "I am Old Sam.\n",
                                   "config": "Old Sam keeps the garden.\n"})
        status, made = await http(port, "POST", f"/v1/agents/drafts/{body['draft']}/confirm",
                                  {"rev": 1})
        sam = card("old-sam") if status == 201 else {}
        check("a name with a space: id old-sam, the name kept as its own",
              status == 201 and sam.get("display") == "Old Sam"
              and sam.get("self_name") == "Old Sam"
              and profile.self_name("old-sam", sam) == "Old Sam", (status, made, sam))
        check("...and a name equal to the id's makes no self_name",
              "self_name" not in card("juniper") and profile.self_name("juniper", card("juniper")) == "Juniper")
        check("...a rename does not move it", profile.self_name(
            "old-sam", {**sam, "display": "Sam"}) == "Old Sam")
        check("...and provision keeps it", "self_name" in profile.kept(sam))

        # -- nothing deletes an agent ---------------------------------------
        status, body = await http(port, "DELETE", "/v1/agents/juniper", {})
        check("no route deletes an agent", status == 405 and (LABS / "juniper").is_dir(),
              body)
        status, body = await http(port, "GET", "/v1/settings")
        keys = {r["key"] for r in body["settings"]}
        check("GET /v1/settings lists the six new keys",
              {"agent.face", "agent.draft", "agent.revise", "agent.confirm",
               "agent.archive", "agent.restore"} <= keys)
    finally:
        tcp.close()
        await tcp.wait_closed()


asyncio.run(main())
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
