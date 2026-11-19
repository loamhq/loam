#!/usr/bin/env python3
"""The plugin-host seams (2026-11-14, first release L4): `contributes:` read,
declared, filtered by lane, rendered into timers; and arming a timer that has
stood before without starting it as it stands (lib/loam_units.arm_timer, which
loam_schedule.Systemd.arm calls).

Run: python3 tests/test_contributions.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import loam_contributions as c  # noqa: E402
import loam_plugins  # noqa: E402
import loam_units  # noqa: E402

results: list[bool] = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:66}  | {str(detail)[:220]}")


GOOD = {
    "agent.tool": [{"name": "hello", "run": "python3 hello.py",
                    "lanes": ["persona", "engineer"]}],
    "agent.prompt": [{"text": "You can say hello.", "lanes": ["persona", "reviewer"]}],
    "schedule.kind": [{"name": "tick", "schedule": "daily at 05:00 PT", "run": "true"}],
    "settings.entry": [{"key": "greeting", "label": "What it says",
                        "choices": ["hello", "hi"], "default": "hello"}],
    "engineer.env": [{"name": "HELLO_MODE", "setting": "greeting"},
                     {"name": "HELLO_KEY", "secret": "HELLO_KEY", "projects": ["Loam"]}],
    "drift.check": {"run": "true"},
    "box.listener": [{"process": "hellod", "why": "a test"}],
    "settings.secret": [{"name": "HELLO_API_KEY", "label": "Hello's key", "check": "builtin:gemini"}],
    "vault.folder": [{"setting": "greeting"}],
    "engineer.job": [{"run": "python3 job.py", "projects": ["acme"]}],
    "web.door": [{"why": "visitors by a link reach one doc"}],
    "made.up": [{"x": 1}],
}

# -- reading ---------------------------------------------------------------------

warnings: list[str] = []
got = c.contributes_of(GOOD, None, warnings)
check("every place read", set(got) == set(c.PLACES), sorted(got))
check("an unknown place is a warning, left out",
      "made.up" not in got and any("made.up" in w for w in warnings), warnings)
check("the schedule is Loam's grammar, compiled",
      got["schedule.kind"][0]["calendar"] == "*-*-* 05:00:00 America/Los_Angeles",
      got["schedule.kind"][0])
check("lanes default to persona",
      c.contributes_of({"agent.tool": [{"name": "x", "run": "true"}]}, None, [])
      ["agent.tool"][0]["lanes"] == ["persona"])
for bad, why in (({"agent.tool": [{"name": "X", "run": "true"}]}, "a bad name"),
                 ({"agent.tool": [{"name": "x", "run": "a\nb"}]}, "a two-line run"),
                 ({"agent.tool": [{"name": "x", "run": "t", "lanes": ["wake"]}]}, "a lane that is none"),
                 ({"schedule.kind": [{"name": "x", "schedule": "hourly", "run": "t"}]}, "a schedule off the grammar"),
                 ({"engineer.env": [{"name": "X", "value": "1", "secret": "Y"}]}, "two sources"),
                 ({"agent.prompt": [{"text": "x" * 2001}]}, "words past the limit"),
                 ({"engineer.job": [{"run": "t", "timeout": 600}]}, "a job hook past two minutes"),
                 ({"engineer.job": [{"run": "t", "projects": []}]}, "a job hook for no project"),
                 ({"engineer.job": [{"projects": ["acme"]}]}, "a job hook with nothing to run")):
    try:
        c.contributes_of(bad, None, [])
        check(f"refused: {why}", False, "accepted")
    except c.ContributionError as exc:
        check(f"refused: {why}", True, exc)

# -- in a manifest ------------------------------------------------------------------

manifest, warns = loam_plugins.manifest_of(
    {"id": "hello", "name": "Hello", "version": "0.1.0", "api": 0,
     "kind": ["extension"], "contributes": {k: v for k, v in GOOD.items() if k != "made.up"}},
    "hello")
check("a manifest carries its contributions", set(manifest["contributes"]) == set(c.PLACES))
check("...and fills each place it contributes to",
      set(c.PLACES) <= set(manifest["places"]), manifest["places"])
decl = loam_plugins.declaration(manifest, {})
t = decl["touches"]
check("the declaration says what it adds, computed",
      t["agents"]["tools"] == ["hello (persona, engineer)"]
      and t["agents"]["prompt"] == ["persona", "reviewer"]
      and t["schedule"] == ["tick: daily at 05:00 PT"]
      and t["secrets"] == ["HELLO_API_KEY", "HELLO_KEY"] and t["inbound"] == ["hellod: a test"]
      and t["vault"] == ["(its greeting setting)"]
      and t["engineer_job"] == ["python3 job.py (acme)"]
      and t["open_web"] == "visitors by a link reach one doc", t)
later = dict(manifest)
later["contributes"] = dict(manifest["contributes"])
later["contributes"]["agent.tool"] = manifest["contributes"]["agent.tool"] + [
    {"name": "bye", "run": "true", "lanes": ["persona"], "agents": None}]
more = loam_plugins.added(decl, loam_plugins.declaration(later, {}))
check("a later version with another tool asks again", "agents" in more, more)
try:
    loam_plugins.manifest_of({"id": "t", "name": "T", "version": "1.0.0", "api": 0,
                              "kind": ["theme"], "contributes": {"drift.check": {"run": "true"}}}, "t")
    check("a theme cannot contribute", False, "accepted")
except loam_plugins.ManifestError as exc:
    check("a theme cannot contribute", True, exc)

# -- by lane --------------------------------------------------------------------------

with tempfile.TemporaryDirectory() as tmp:
    plug = [{"id": "hello", "folder": Path(tmp), "contributes": got,
             "settings": Path(tmp) / "s", "data": Path(tmp) / "d"},
            {"id": "only", "folder": Path(tmp), "settings": Path(tmp), "data": Path(tmp),
             "contributes": {"agent.tool": [{"name": "only", "run": "true",
                                             "lanes": ["persona"], "agents": ["iris"]}]}}]
    check("persona tools: the plugin's server, named by id",
          list(c.lane_servers("persona", "ada", plug)) == ["hello"],
          c.lane_servers("persona", "ada", plug))
    check("...an agents: entry reaches its persona only",
          set(c.lane_servers("persona", "iris", plug)) == {"hello", "only"})
    check("the engineer gets its tool", list(c.lane_servers("engineer", None, plug)) == ["hello"])
    check("the reviewer gets none", c.lane_servers("reviewer", None, plug) == {})
    srv = c.lane_servers("engineer", None, plug)["hello"]
    check("a server runs in the plugin's folder, with its environment",
          srv["command"] == "/bin/sh" and srv["args"][1].endswith("exec python3 hello.py")
          and srv["env"]["LOAM_PLUGIN_ID"] == "hello", srv)
    check("persona words, headed by the plugin",
          "**hello** (a plugin): You can say hello." in c.lane_prompt("persona", "ada", plug))
    check("the engineer has no words from it", c.lane_prompt("engineer", None, plug) == "")
    units = c.desired_units(plug)
    check("one timer pair, named by plugin and entry",
          set(units) == {"loam-plugin-hello--tick.service", "loam-plugin-hello--tick.timer"}, sorted(units))
    timer = units["loam-plugin-hello--tick.timer"]
    check("...zoned, not persistent, never a catch-up",
          "OnCalendar=*-*-* 05:00:00 America/Los_Angeles\nPersistent=false" in timer, timer)
    check("...its service is plugin-run, which holds it to its calendar",
          "/bin/plugin-run hello tick" in units["loam-plugin-hello--tick.service"])

# -- L5: a raw calendar, a boot entry, a tool's secrets, Codex jobs, pressure ----

more = c.contributes_of({
    "schedule.kind": [{"name": "tick", "calendar": "*:*:30", "run": "true", "timeout": 120},
                      {"name": "boot", "boot": True, "run": "true"}],
    "agent.tool": [{"name": "cf", "run": "true", "secrets": ["CLOUDFLARE_API_KEY"]}]}, None, [])
check("a raw calendar is kept as written", more["schedule.kind"][0]["calendar"] == "*:*:30")
with tempfile.TemporaryDirectory() as tmp:
    plug = [{"id": "hr", "folder": Path(tmp), "contributes": more,
             "settings": Path(tmp) / "s", "data": Path(tmp) / "d"}]
    units = c.desired_units(plug)
    check("a boot entry is a service wanted at the manager's start, with no timer",
          "WantedBy=default.target" in units["loam-plugin-hr--boot.service"]
          and "loam-plugin-hr--boot.timer" not in units, sorted(units))
    check("...a calendar entry has its timer, at its own line",
          "OnCalendar=*:*:30\n" in units["loam-plugin-hr--tick.timer"])
    check("...and its timeout", "TimeoutStartSec=120" in units["loam-plugin-hr--tick.service"])
    env = c.lane_servers("persona", "ada", plug)["hr-cf"]["env"]
    check("a tool's secrets are named, never given", env.get("LOAM_PLUGIN_SECRETS") == "CLOUDFLARE_API_KEY"
          and not any("KEY" in k and k != "LOAM_PLUGIN_SECRETS" for k in env), env)
    check("...and it is told its private folder and Loam's lib",
          env["LOAM_PLUGIN_PRIVATE"].endswith("/plugins/private/hr") and env["LOAM_LIB"].endswith("/lib"))
check("a tool's secrets are in the declaration", c.declared(more).get("secrets") == ["CLOUDFLARE_API_KEY"])
for bad, why in (({"schedule.kind": [{"name": "x", "run": "t"}]}, "a schedule entry with no when"),
                 ({"schedule.kind": [{"name": "x", "run": "t", "boot": True, "calendar": "daily"}]}, "two whens"),
                 ({"agent.tool": [{"name": "x", "run": "t", "secrets": ["lower"]}]}, "a secret that is no name")):
    try:
        c.contributes_of(bad, None, [])
        check(f"refused: {why}", False, "accepted")
    except c.ContributionError as exc:
        check(f"refused: {why}", True, exc)

import loam_codex_job  # noqa: E402
import loam_provider_memory  # noqa: E402
flags = loam_codex_job.mcp_flags("hr-cf", {"command": "/bin/sh", "args": ["-c", "x"], "env": {"A": "1"}})
check("a Codex job gets a plugin's server as -c overrides",
      flags == ["-c", 'mcp_servers.hr-cf.command="/bin/sh"', "-c", 'mcp_servers.hr-cf.args=["-c", "x"]',
                "-c", 'mcp_servers.hr-cf.default_tools_approval_mode="approve"',
                "-c", 'mcp_servers.hr-cf.env={A = "1"}'], flags)
argv = loam_codex_job.argv(workdir=Path("/w"), tree=Path("/t"), job_dir=Path("/j"), prompt="p",
                           extra_dirs=[], fork_from=None, instructions_text="i")
check("...and the memory pins, which the job's ignored home config held",
      all(kv in argv for kv in loam_provider_memory.CODEX_OVERRIDES))

import loam_pressure as lp  # noqa: E402
state, post = {}, None
for minute in range(3):
    state, post = lp.pressure_step(state, {"ram_pct": 90, "ram_used_mb": 9, "ram_total_mb": 10,
                                           "swap_free_pct": 50, "swap_free_mb": 1, "over": True},
                                   1_800_000_000 + 60 * minute)
check("core pressure: an episode begins after three minutes, said once",
      post and post.startswith("memory pressure on the box") and "headroom" not in post, post)
state, post = lp.pressure_step(state, {"ram_pct": 90, "ram_used_mb": 9, "ram_total_mb": 10,
                                       "swap_free_pct": 50, "swap_free_mb": 1, "over": True}, 1_800_000_200)
check("...and not again while it lasts", post is None)


# -- engineer.job: a command run at a job's launch (L6) --------------------------

with tempfile.TemporaryDirectory() as tmp:
    folder = Path(tmp) / "plug"
    folder.mkdir()
    (folder / "hook.sh").write_text(
        'echo "SEEN_PROJECT=$LOAM_PROJECT"\n'
        'echo "SEEN_JOB=$LOAM_JOB_ID"\n'
        'echo "SEEN_SCRATCH=$LOAM_JOB_SCRATCH"\n'
        'echo "SEEN_PLUGIN=$LOAM_PLUGIN_ID"\n'
        'echo "made" > "$LOAM_JOB_DIR/made.txt"\n'
        'echo "not a variable line"\n'
        'echo "WITH_EQUALS=a=b"\n')
    (folder / "fail.sh").write_text('echo "LEAKED=1"; echo "it broke" >&2; exit 3\n')
    (folder / "slow.sh").write_text('sleep 5; echo "LATE=1"\n')
    job_dir = Path(tmp) / "job"
    job_dir.mkdir()

    def hook(run, projects=None, timeout=30, pid="plug"):
        return {"id": pid, "folder": folder, "settings": folder / "s", "data": folder / "d",
                "contributes": {"engineer.job": [{"run": run, "projects": projects,
                                                  "timeout": timeout}]}}

    env, notes = c.engineer_job_env("acme", "20261114-000000-aaaa", job_dir,
                                    Path("/tmp/loam-job-20261114-000000-aaaa"),
                                    [hook("sh hook.sh", ["acme"])])
    check("engineer.job: its NAME=value lines are the job's environment",
          env.get("SEEN_PROJECT") == "acme" and env.get("SEEN_JOB") == "20261114-000000-aaaa"
          and env.get("SEEN_PLUGIN") == "plug" and env.get("WITH_EQUALS") == "a=b", env)
    check("...it is told the job's scratch, and writes in the job's folder",
          env.get("SEEN_SCRATCH") == "/tmp/loam-job-20261114-000000-aaaa"
          and (job_dir / "made.txt").read_text().strip() == "made", env)
    check("...a line that is no variable is dropped, and said",
          "not a variable line" not in str(env)
          and any("not NAME=value" in n for n in notes), notes)
    env, notes = c.engineer_job_env("other", "20261114-000000-aaaa", job_dir, Path("/x"),
                                    [hook("sh hook.sh", ["acme"])])
    check("...another project's job runs nothing of it", env == {} and notes == [], (env, notes))
    env, notes = c.engineer_job_env("other", "20261114-000000-aaaa", job_dir, Path("/x"),
                                    [hook("sh hook.sh")])
    check("...with no projects it runs for every project", env.get("SEEN_PROJECT") == "other", env)
    env, notes = c.engineer_job_env("acme", "20261114-000000-aaaa", job_dir, Path("/x"),
                                    [hook("sh fail.sh")])
    check("...a hook that fails gives nothing, and the note says why",
          env == {} and any("exited 3" in n and "it broke" in n for n in notes), notes)
    env, notes = c.engineer_job_env("acme", "20261114-000000-aaaa", job_dir, Path("/x"),
                                    [hook("sh slow.sh", timeout=1)])
    check("...a hook past its timeout gives nothing", env == {}
          and any("within 1 s" in n for n in notes), notes)

# -- arming without firing -------------------------------------------------------------


class Fake:
    """systemctl, recorded, with a timer's state as it is asked."""
    def __init__(self, active: str, stood: str):
        self.state = {"ActiveState": active, "InactiveExitTimestampMonotonic": stood}
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args):
        self.calls.append(args)
        if args[0] == "show":
            return subprocess.CompletedProcess(args, 0, self.state.get(args[2], "") + "\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")


with tempfile.TemporaryDirectory() as tmp:
    d = Path(tmp)
    (d / "loam-x.timer").write_text("[Timer]\nOnCalendar=daily\n")
    f = Fake("active", "123")
    check("a standing timer: left to the reload, not started",
          loam_units.arm_timer("loam-x.timer", d, run=f) == "standing"
          and not any(a[0] == "start" for a in f.calls), f.calls)
    f = Fake("inactive", "0")
    check("a new timer: enabled and started",
          loam_units.arm_timer("loam-x.timer", d, run=f) == "started"
          and [a[0] for a in f.calls if a[0] != "show"] == ["enable", "start"], f.calls)
    f = Fake("inactive", "373094343313")
    did = loam_units.arm_timer("loam-x.timer", d, run=f)
    seq = [a[0] for a in f.calls if a[0] != "show"]
    check("a timer that stood before: made fresh, then started",
          did == "made fresh, started"
          and seq == ["disable", "daemon-reload", "daemon-reload", "enable", "start"], seq)
    check("...and its file is back where it was", (d / "loam-x.timer").is_file()
          and not (d / ".loam-x.timer.aside").exists())

import loam_schedule  # noqa: E402

class Recorder(loam_schedule.Systemd):
    def __init__(self, unit_dir):
        super().__init__(unit_dir=unit_dir, runtime=True)
        self.fake = Fake("inactive", "99")

    def run(self, *args):
        return self.fake(*args)


with tempfile.TemporaryDirectory() as tmp:
    (Path(tmp) / "loam-cron-x-y.timer").write_text("[Timer]\nOnCalendar=daily\n")
    sd = Recorder(Path(tmp))
    did = sd.arm("loam-cron-x-y.timer")
    check("Systemd.arm goes through arm_timer and its own run",
          did == "made fresh, started"
          and ("enable", "--runtime", "loam-cron-x-y.timer") in sd.fake.calls, sd.fake.calls)

# settings.entry reads for a person (2026-11-18, Images 1.1.0): a label per
# choice, a note, and `advanced`. Older hosts ignore all three.
nice = c.contributes_of({"settings.entry": [
    {"key": "backend", "label": "Made with", "choices": ["auto", "openai"],
     "labels": {"auto": " Whichever key you have "}, "note": "A sentence.", "default": "auto"},
    {"key": "model", "label": "Model", "advanced": True, "default": "m"}]}, None, [])["settings.entry"]
check("labels, note and advanced are kept",
      nice[0].get("labels") == {"auto": "Whichever key you have"} and nice[0].get("note") == "A sentence."
      and nice[1].get("advanced") is True and "labels" not in nice[1], nice)
for bad in ({"labels": {"other": "x"}, "choices": ["a"]}, {"labels": {"a": ""}, "choices": ["a"]},
            {"labels": {"a": "x"}}, {"labels": ["a"], "choices": ["a"]},
            {"note": ""}, {"note": "x" * 301}, {"advanced": "yes"}, {"setup": "yes"}):
    try:
        c.contributes_of({"settings.entry": [{"key": "k", "label": "K", **bad}]}, None, [])
        check(f"settings.entry {bad} is refused", False)
    except c.ContributionError:
        check(f"settings.entry {sorted(bad)} is refused", True)

# `setup` (2026-11-19): what setting the plugin up means; the page leads with
# its controls once one such entry has a value.
marked = c.contributes_of({"settings.entry": [{"key": "remote", "label": "Repository", "default": "", "setup": True},
                                              {"key": "every", "label": "Every", "default": "1h"}]}, None, [])["settings.entry"]
check("setup is kept where given, absent elsewhere", (marked[0].get("setup"), "setup" in marked[1]), (True, False))

failed = results.count(False)
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
