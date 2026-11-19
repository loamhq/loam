#!/usr/bin/env python3
"""A session's own model and effort: chosen at the start, changed mid-turn,
and listed by the providers route.

A session runs on the persona's own setting until something picks: a thread
member's model (PATCH /v1/threads/<id> `model`), which starts and changes the
member's session through the code below. (The classic chat's + menu and its
PATCH /v1/sessions/<id> retired on 2026-11-12.) Four things:

  1. a start carries `model` and `effort` onto the launch line and onto the
     record, and a session that asked for neither launches with neither flag
     -- which is what makes the persona's settings.json still the answer
  2. `Session.set_choices` on a RUNNING session sends the CLI `/model <value>`
     and `/effort <value>` on its own stdin, and on an ENDED one writes the
     record for the next launch
  3. what `choices()` refuses
  4. GET /v1/providers: models with display names and the persona's default
     ticked, efforts, and empty lists on the providers nobody has wired

**No model call is spent here.** The process is a stand-in, so what is proved
is the argv and the bytes written to stdin, not the answer that would come
back. The answer that comes back was proved by causing, on the live broker:
see broker/README.md.

Run: python3 tests/test_broker_model.py
"""

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
os.environ["LOAM_USAGE_READS"] = "off"  # no real CLI reads; see loam_provider_usage
import loam_broker as cfg  # noqa: E402
# Admission reads no real box here: a fixture session must not be refused
# because the machine running the suite is short of memory (a landing
# got 429 for one, 2026-11-09). test_broker_seats is where admission is tested.
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}

# Admission reads the box's real memory since 2026-11-09, so on a full box a
# start here was refused and this test failed for a reason it is not about
# (two landings refused on 11-09 at ~2.8 GB free). The box always has room
# here; test_broker_seats is the test of admission.
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}

loader = importlib.machinery.SourceFileLoader(
    "session_broker", str(AGENTS / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker_mod = importlib.util.module_from_spec(spec)
loader.exec_module(broker_mod)

results = []


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:64} got={got!r}")
    if not ok:
        print(f"      wanted {want!r}")


class FakeStdin:
    def __init__(self) -> None:
        self.lines: list[dict] = []

    def write(self, data: bytes) -> None:
        self.lines.append(json.loads(data.decode()))

    async def drain(self) -> None:
        return None


class FakeStream:
    """A pipe that is already at end of file, which is what the pumps read
    when the process behind them is a stand-in."""

    async def readline(self) -> bytes:
        return b""


class FakeProc:
    def __init__(self) -> None:
        self.stdin = FakeStdin()
        self.stdout = FakeStream()
        self.stderr = FakeStream()
        self.returncode = None

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else 0


class FakeWriter:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.chunks.append(data)

    async def drain(self) -> None:
        return None

    def body(self):
        head, _, payload = b"".join(self.chunks).decode().partition("\r\n\r\n")
        return int(head.split()[1]), json.loads(payload)


def running(broker, agent="probe", model=None, effort=None):
    session = broker.adopt(broker_mod.Session(agent, model=model,
                                              effort=effort))
    session.state = "running"
    session.proc = FakeProc()
    broker.sessions[session.id] = session
    return session


def ended(broker, agent="probe", model=None, effort=None):
    """A record with no process behind it, which is most of a home screen."""
    session = broker.adopt(broker_mod.Session(agent, model=model,
                                             effort=effort))
    session.save()
    return session


async def route(server, method, path, params=None, payload=None):
    writer = FakeWriter()
    try:
        await server.route(method, path, params or {}, payload or {}, writer)
    except broker_mod.BrokerError as exc:
        return exc.status, {"error": exc.message}
    return writer.body()


def commands(session) -> list[str]:
    """The text of every user message written to this process's stdin."""
    return [line["message"]["content"] for line in session.proc.stdin.lines
            if line.get("type") == "user"]


async def main() -> None:
    broker = broker_mod.Broker()
    server = broker_mod.Server(broker)

    # -- 1. the launch line ------------------------------------------------
    plain = broker_mod.Session("probe")
    check("a session nobody chose for passes no --model",
          "--model" in plain.argv(resume=False), False)
    check("...and no --effort, so the persona's settings answer both",
          "--effort" in plain.argv(resume=False), False)
    check("...and its record says null rather than a guess",
          (plain.meta()["model"], plain.meta()["effort"]), (None, None))

    picked = broker_mod.Session("probe", model="opus", effort="high")
    argv = picked.argv(resume=False)
    check("a session started on a model carries it as --model",
          argv[argv.index("--model") + 1], "opus")
    check("...and its effort as --effort",
          argv[argv.index("--effort") + 1], "high")
    check("...and both are on the record",
          (picked.meta()["model"], picked.meta()["effort"]), ("opus", "high"))

    picked.save()
    again = broker_mod.Session("probe", picked.id)
    check("...and both survive the process, read back off the record",
          (again.model, again.effort), ("opus", "high"))
    check("...which is what puts them on the resumed launch line",
          "--model" in again.argv(resume=True), True)

    # -- 2. changing a session's model mid-conversation -------------------
    # Through `Session.set_choices`, which is what a thread member's model
    # change runs (lib/loam_thread_engine.py). The classic PATCH route that
    # also called it retired with the classic view on 2026-11-12.
    live = running(broker, model="sonnet")
    sent = await live.set_choices({"model": "opus"})
    check("a running session takes a model change", live.model, "opus")
    check("...and the CLI was told, in its own command, with the value",
          commands(live), ["/model opus"])
    check("...and says what it sent", sent, ["/model opus"])

    await live.set_choices({"effort": "max"})
    check("effort goes the same way", commands(live)[-1], "/effort max")
    check("...and lands on the record beside the model",
          (live.meta()["model"], live.meta()["effort"]), ("opus", "max"))

    sent = await live.set_choices({"model": None})
    check("null puts the session back on the persona's setting",
          live.model, None)
    check("...without sending /model with no value into a picker "
          "nobody can answer", (commands(live)[-1], sent), ("/effort max", []))

    cold = ended(broker, model=None)
    sent = await cold.set_choices({"model": "haiku", "effort": "low"})
    check("an ended session takes the change on its record, sending nothing",
          (cold.model, cold.effort, sent), ("haiku", "low", []))
    stored = json.loads((cfg.SESSION_DIR / f"{cold.id}.json")
                        .read_text(encoding="utf-8"))
    check("...and the record on disk agrees",
          (stored["model"], stored["effort"]), ("haiku", "low"))

    # -- 3. what is refused ------------------------------------------------
    def refused(payload):
        try:
            broker_mod.choices(payload)
        except broker_mod.BrokerError as exc:
            return exc.status, exc.message
        return None, ""
    status, error = refused({"model": "gpt-5"})
    check("a model this CLI does not take is a 400", status, 400)
    check("...whose message says what it does take",
          "claude-fable-5" in error, True)
    check("ultracode is not on offer: the CLI pins it behind a terminal",
          refused({"effort": "ultracode"})[0], 400)
    status, error = refused({"effort": "mild"})
    check("an effort that is not a level is a 400", status, 400)
    check("...and names the five", "xhigh" in error, True)

    check("a full model id is accepted, as the CLI's help says it is",
          broker_mod.choices({"model": "claude-fable-5-1"}),
          {"model": "claude-fable-5-1"})

    # -- 4. GET /v1/providers ----------------------------------------------
    status, answer = await route(server, "GET", "/v1/providers",
                                 params={"agent": "probe"})
    rows = {row["id"]: row for row in answer["providers"]}
    wired = rows["claude-code"]
    check("the wired provider lists the aliases this CLI takes",
          [m["id"] for m in wired["models"]],
          [alias for alias, _ in cfg.CLAUDE_CODE_MODELS])
    check("...each with a display name", wired["models"][0]["name"], "Fable")
    check("...and the five effort levels",
          wired["efforts"], ["low", "medium", "high", "xhigh", "max"])
    check("...with ultracode kept off the list",
          "ultracode" in wired["efforts"], False)
    check("the persona's own model is the one marked default",
          [m["id"] for m in wired["models"] if m["default"]], ["fable"])
    check("...read off that persona's profile, not stated here",
          cfg.persona_model("probe"), "fable")

    # `resolves_to` (2026-11-10): what each alias runs on with the installed
    # CLI, read out of the build the symlink points at, and read again when
    # the symlink is moved to another build.
    builds = root / "cli-versions"
    builds.mkdir(exist_ok=True)

    def build(version: str, fable: str, best: str) -> Path:
        path = builds / version
        path.write_bytes(
            b"\x00junk" * 1000 + b'max_output_tokens:{default:64000}}],aliases:{'
            b'opus:{default:"claude-opus-5-5",per_provider:{bedrock:"claude-opus-5-5"}},'
            b'sonnet:{default:"claude-sonnet-5-5",per_provider:{}},'
            b'haiku:{default:"claude-haiku-5-5",per_provider:{}},'
            b'fable:{default:"' + fable.encode() + b'",per_provider:{gateway:"claude-fable-5"}}},'
            b'defaults:{},best:"' + best.encode() + b'",latest_per_family:{fable:"x"},'
            b'alias_migration:{}};' + b"\x00tail" * 1000)
        return path

    link = root / "claude-link"
    link.symlink_to(build("2.1.322", "claude-fable-5-2", "opus"))
    saved_claude = cfg.CLAUDE
    cfg.CLAUDE = str(link)
    try:
        status, answer = await route(server, "GET", "/v1/providers",
                                     params={"agent": "probe"})
        wired = {row["id"]: row for row in answer["providers"]}["claude-code"]
        by_id = {m["id"]: m for m in wired["models"]}
        check("resolves_to: fable is the id this build maps it to",
              (by_id["fable"]["resolves_to"], wired["cli_version"]),
              ("claude-fable-5-2", "2.1.322"))
        check("...[1m] is the same model, best follows the build's choice",
              (by_id["fable[1m]"]["resolves_to"], by_id["best"]["resolves_to"],
               by_id["sonnet"]["resolves_to"]),
              ("claude-fable-5-2", "claude-opus-5-5", "claude-sonnet-5-5"))
        check("...opusplan names both halves",
              (by_id["opusplan"]["resolves_to"],
               by_id["opusplan"]["resolves_to_after_plan"]),
              ("claude-opus-5-5", "claude-sonnet-5-5"))
        link.unlink()
        link.symlink_to(build("2.1.319", "claude-fable-5-1", "fable"))
        status, answer = await route(server, "GET", "/v1/providers",
                                     params={"agent": "probe"})
        wired = {row["id"]: row for row in answer["providers"]}["claude-code"]
        by_id = {m["id"]: m for m in wired["models"]}
        check("...the symlink moved: read again from the new build",
              (by_id["fable"]["resolves_to"], by_id["best"]["resolves_to"],
               wired["cli_version"]),
              ("claude-fable-5-1", "claude-fable-5-1", "2.1.319"))
        (builds / "2.1.400").write_bytes(b"no table in this one")
        link.unlink()
        link.symlink_to(builds / "2.1.400")
        status, answer = await route(server, "GET", "/v1/providers",
                                     params={"agent": "probe"})
        wired = {row["id"]: row for row in answer["providers"]}["claude-code"]
        check("...a build with no table this reads says null, not a guess",
              {m["resolves_to"] for m in wired["models"]}, {None})
    finally:
        cfg.CLAUDE = saved_claude

    status, answer = await route(server, "GET", "/v1/providers",
                                 params={"agent": "other"})
    rows = {row["id"]: row for row in answer["providers"]}
    check("a persona on another model has another default ticked",
          [m["id"] for m in rows["claude-code"]["models"] if m["default"]],
          ["sonnet"])
    check("an unwired provider offers an empty list, not a missing key",
          (rows["grok"]["models"], rows["grok"]["efforts"]), ([], []))

    status, answer = await route(server, "GET", "/v1/providers")
    rows = {row["id"]: row for row in answer["providers"]}
    check("with no agent named and the fleet split, nothing is ticked",
          [m["id"] for m in rows["claude-code"]["models"] if m["default"]], [])

    # A start on a chosen model and effort, as a thread turn starts one.
    session = await broker.start_session("probe", model="sonnet", effort="high")
    check("a started session's record carries what it was started on",
          (session.meta()["model"], session.meta()["effort"]), ("sonnet", "high"))
    await session.stop()


root = Path(tempfile.mkdtemp(prefix="loam-broker-model-test-"))
saved = {name: getattr(cfg, name) for name in
         ("STATE_DIR", "SESSION_DIR", "TOKEN_PATH", "USAGE_PATH", "AI_LABS",
          "CREDENTIALS_PATH")}
try:
    cfg.STATE_DIR = root / "state"
    cfg.SESSION_DIR = cfg.STATE_DIR / "sessions"
    cfg.TOKEN_PATH = cfg.STATE_DIR / "token"
    cfg.USAGE_PATH = cfg.STATE_DIR / "provider-usage.json"
    cfg.AI_LABS = root / "agents"
    cfg.CREDENTIALS_PATH = root / "credentials.json"
    # Two personas, on two models, because "the default" is a fact about a
    # persona's own profile (agent.json, 2026-11-19; settings.json before,
    # which provision now writes from it) and the fleet's answer is only the
    # one they share. "probe" names none, so it is the wake's default; a test
    # that could not tell the two apart would pass on a hardcoded default.
    for name, model in (("probe", None), ("other", "sonnet")):
        (cfg.AI_LABS / name / ".claude").mkdir(parents=True)
        (cfg.AI_LABS / name / "agent.json").write_text(
            json.dumps({"model": model} if model else {}), encoding="utf-8")
        (cfg.AI_LABS / name / ".claude" / "settings.json").write_text(
            json.dumps({"model": model or "fable"}), encoding="utf-8")

    # The launch itself is never made: what this test proves is the argv and
    # the stdin, and a real `claude` here would cost a model call per case.
    async def no_process(*argv, **kwargs):
        return FakeProc()
    asyncio.subprocess.create_subprocess_exec = no_process
    asyncio.create_subprocess_exec = no_process

    asyncio.run(main())
finally:
    for name, value in saved.items():
        setattr(cfg, name, value)
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", results.count(False))
sys.exit(1 if False in results else 0)
