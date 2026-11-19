#!/usr/bin/env python3
"""One Loam engineer, with an app-only scope (2026-11-19).

Until then the app (`app/` of Loam's checkout) had an engineer of its own,
`loam-ui`. It is now the Loam engineer's `scope: app`, and every safeguard
the folder project had is held here, through the same code:

  the registry     the scope is in `projects()` (its jobs are found) and not
                   in `engineers()` (Settings shows one engineer); built in on
                   an install with an app and no record, the record read when
                   one exists (the first install's, with its history); none
                   without an app.
  the brief        `Loam` + `scope: app`, the old name `loam-ui`, and `Loam`
                   continuing one of the scope's jobs all run as the scope;
                   `Loam` alone is the whole of Loam; a scope on another
                   engineer, an unknown scope and `loam-ui` + `all` are
                   refused before anything is minted. The header says whose
                   job it is (`engineer: Loam`, `scope: app`).
  who may brief    the scope is open to every persona, as the app engineer
                   was; a full Loam brief is the briefers' alone, and its
                   refusal names the scope.
  the safeguards   its session in `app/`, its tests `cd app && npm test`, a
                   landing that refuses a path outside `app/`, no deploy key
                   asked, Loam's live lock, the app's deploy at landing
                   (`bin/deploy loam-ui up <sha> --forward-only`: the app's
                   unit, never the broker).
  the provider     the scope runs on the Loam engineer's setting, not on its
                   old row and not on the default; the old row is kept.
  deploy toggle    Loam's row; absent, the old row's (migration); said off,
                   off.
  Settings         one Loam row with the app's toggle and its label; the old
                   name reads Loam's row; a provider for the old name alone is
                   refused with where it is set; its toggle lands on Loam.
  Activity         a scope job reads as Loam's, `scope: app`.
  provisioning     `app/`'s harness is the Loam engineer's role verbatim and
                   the shipped app project file.

Run: python3 tests/test_app_scope.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="loam-app-scope-"))
os.environ["LOAM_HOST_SETTINGS"] = str(TMP / "broker" / "settings.json")
os.environ["LOAM_PROJECTS_DIR"] = str(TMP / "projects")
os.environ.pop("LOAM_WAKE", None)
(TMP / "projects").mkdir(parents=True)

sys.path.insert(0, str(AGENTS / "lib"))
import loam_autodeploy  # noqa: E402
import loam_config as lc  # noqa: E402
import loam_engineer as eng  # noqa: E402
import loam_profile as profile  # noqa: E402
import loam_worktree as wt  # noqa: E402

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:70} {got!r}"[:260]
          + ("" if ok else f"   want {want!r}"))


def load(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_settings(value: dict) -> None:
    path = Path(os.environ["LOAM_HOST_SETTINGS"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"engineers": value}))


def read_settings() -> dict:
    return json.loads(Path(os.environ["LOAM_HOST_SETTINGS"]).read_text()).get("engineers") or {}


job = load("engineer_job_under_test_app_scope", AGENTS / "bin" / "engineer-job")
prov = load("provision_under_test_app_scope", AGENTS / "bin" / "provision")

try:
    has_app = (lc.ROOT / "app" / "package.json").is_file()
    check("this checkout has the app (the suite's root is the tree)", has_app, True)

    # -- the registry -------------------------------------------------------
    write_settings({})
    lane = eng.app_lane()
    check("built in with no record: the scope, named as the app's deploy record",
          (lane.name, lane.subdir, lane.lane_of, lane.platform, lane.record.get("built_in")),
          ("loam-ui", "app", "Loam", False, True))
    check("...its tests are the app's", wt.test_command(lane), "cd app && npm test")
    check("...its jobs are where the app engineer's were",
          lane.jobs, eng.PROJECT_STATE / "loam-ui" / "jobs")
    check("...in projects(), so its jobs are found", "loam-ui" in eng.projects(), True)
    check("...not in engineers(): one engineer", sorted(eng.engineers()), ["Loam"])
    check("...and Loam itself is no scope", eng.platform_project().lane_of, None)
    check("display: `Loam (app)`", eng.display_name(lane), "Loam (app)")

    # The first install's record, with its history: read, still the scope.
    record = {"name": "loam-ui", "checkout": str(lc.ROOT), "subdir": "app",
              "test_command": "cd app && npm test", "runtime": "codex",
              "provisioned": "2026-09-16", "folded_into_loam": "2026-11-16"}
    (TMP / "projects" / "loam-ui.json").write_text(json.dumps(record))
    (TMP / "projects" / "webapp.json").write_text(json.dumps({
        "name": "webapp", "repo": "someone/webapp", "checkout": str(TMP / "webapp"),
        "runtime": "codex"}))
    lane = eng.app_lane()
    check("a registered record is the scope, read as written",
          (lane.lane_of, lane.record.get("provisioned"), lane.record.get("built_in")),
          ("Loam", "2026-09-16", None))
    check("...engineers(): Loam and a real project engineer, no app engineer",
          sorted(eng.engineers()), ["Loam", "webapp"])
    other = eng.Project("loam-ui", TMP / "elsewhere", TMP / "j", platform=False,
                        record={"subdir": "app"})
    check("a folder of another repository is no scope of Loam's", other.lane_of, None)
    (TMP / "projects" / "loam-ui.json").write_text(json.dumps({
        "name": "loam-ui", "repo": "someone/loam-ui", "checkout": str(TMP / "loam-ui")}))
    check("a separate project already named loam-ui keeps its name; no scope then",
          (eng.app_lane(), eng.projects()["loam-ui"].lane_of, "loam-ui" in eng.engineers()),
          (None, None, True))
    (TMP / "projects" / "loam-ui.json").write_text(json.dumps(record))

    # -- the brief: which project it runs as --------------------------------
    def target(name, scope="", cont=""):
        project, why = eng.brief_target(name, scope, cont)
        return (project.name if project else None, bool(why))

    check("`Loam` + scope app: the scope", target("Loam", "app"), ("loam-ui", False))
    check("...`App` in any case", target("Loam", "App"), ("loam-ui", False))
    check("`loam-ui` (the old name): the scope", target("loam-ui"), ("loam-ui", False))
    check("`loam-ui` + scope app: the scope", target("loam-ui", "app"), ("loam-ui", False))
    check("`Loam` alone: the whole of Loam", target("Loam"), ("Loam", False))
    check("`Loam` + scope all: the whole of Loam", target("Loam", "all"), ("Loam", False))
    check("a scope on another engineer is refused, not ignored",
          target("webapp", "app"), (None, True))
    check("an unknown scope is refused", target("Loam", "runtime"), (None, True))
    check("`loam-ui` + scope all is refused (it asks for both)",
          target("loam-ui", "all"), (None, True))
    check("an unknown project: neither, for the launcher's sentence",
          target("nope"), (None, False))
    old_job = "20261118-203030-08dd33be"
    eng.job_dir(lane, old_job).mkdir(parents=True)
    check("`Loam` continuing one of the scope's jobs: the scope (its tree)",
          target("Loam", "", old_job), ("loam-ui", False))
    check("...a Loam job id that is not one: Loam",
          target("Loam", "", "20261118-203030-00000000"), ("Loam", False))
    check("locate_job finds a scope job from before", eng.locate_job(old_job).name, "loam-ui")

    # -- who may brief -------------------------------------------------------
    only = ("ada",)
    check("the scope is open to every persona, as the app engineer was",
          eng.brief_refusal(lane, "otto", "Fix the composer", only), None)
    said = eng.brief_refusal(eng.platform_project(), "otto", "Fix the broker", only)
    check("a full Loam brief is still the briefers' alone", said is None, False)
    check("...and its refusal names the app-only scope",
          "`scope: app`" in said, True)
    check("`Deploy loam-ui` is still the deploy lane", eng.deploy_target("Deploy loam-ui at abc1234"),
          "loam-ui")

    # -- the safeguards, the folder project's --------------------------------
    tree = TMP / "tree"
    check("its session runs in app/ of its tree", wt.session_dir(lane, tree), tree / "app")
    check("...its memory is the app's", wt.memory_rel(lane), "app/memory/MEMORY.md")
    check("...its harness is app/'s", wt.harness_names(lane), tuple(f"app/{n}" for n in wt.HARNESS))
    check("...it lands under Loam's live lock", lane.live_lock, "Loam")
    check("...it is no platform job: no sudo (NoNewPrivileges), no account token",
          lane.platform, False)
    source = (AGENTS / "bin" / "engineer-job").read_text(encoding="utf-8")
    check("...no deploy key is asked of a folder project (its push is Loam's)",
          "not project.subdir and (" in source, True)

    import subprocess  # noqa: PLC0415
    repo = TMP / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    for rel in ("app/src/server.js", "bin/deploy", "lib/loam_broker.py"):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text("x\n")

    def git(*a):
        return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t",
                               "-c", "user.email=t@t", *a],
                              capture_output=True, text=True, check=True).stdout.strip()
    git("add", "-A"); git("commit", "-q", "-m", "base")
    base = git("rev-parse", "HEAD")
    git("checkout", "-q", "-b", "job")
    (repo / "app/src/server.js").write_text("changed\n")
    git("commit", "-qam", "app")
    check("a scope branch that changes only app/ is in scope",
          wt.outside_scope(lane, repo, base, git("rev-parse", "HEAD")), [])
    (repo / "lib/loam_broker.py").write_text("broker change\n")
    git("commit", "-qam", "runtime")
    check("...one that touches the runtime is refused, the path named",
          wt.outside_scope(lane, repo, base, git("rev-parse", "HEAD")), ["lib/loam_broker.py"])
    check("...the same branch as a full Loam job has no scope to keep",
          wt.outside_scope(eng.platform_project(), repo, base, git("rev-parse", "HEAD")), [])
    check("a full Loam job's tests are the whole suite",
          wt.test_command(eng.platform_project()), "python3 tests/run-all")

    # The deploy at landing: the app's own record and command.
    real_app_for = loam_autodeploy.app_for
    check("deploy: the scope's app is `loam-ui`'s record",
          real_app_for(lane)[0] if (lc.ROOT / "deploy" / "loam-ui" / "deploy.toml").is_file()
          else "loam-ui", "loam-ui")
    check("...a full Loam job is not deployed by the launcher",
          real_app_for(eng.platform_project())[0], None)
    check("...the command puts the landed commit on the app alone, never backwards",
          loam_autodeploy.command("loam-ui", "abc1234"),
          ["sudo", "-n", str(loam_autodeploy.DEPLOY_BIN), "loam-ui", "up", "abc1234",
           "--forward-only"])
    check("...and restarts no broker: nothing in it names one",
          any("broker" in a for a in loam_autodeploy.argv("loam-ui", "abc1234", "j")), False)

    # -- the provider: the Loam engineer's -----------------------------------
    write_settings({"default": {"provider": "codex"},
                    "projects": {"loam-ui": {"provider": "antigravity",
                                             "tiers": {"hard": "claude-opus-5-5-high"}}}})
    choice = eng.engineer_choice(lane)
    check("nothing set on Loam: the scope runs as Loam does (Claude Code, built in)",
          (choice["runtime"], choice["source"]),
          (eng.engineer_choice(eng.platform_project())["runtime"], "record"))
    check("...not on its old row, and not on the default",
          choice["runtime"] in ("antigravity", "codex"), False)
    check("...its tiers are Loam's (none set)", eng.tier_models(lane),
          {"quick": None, "standard": None, "hard": None})
    write_settings({"projects": {"Loam": {"provider": "claude-code", "model": "fable",
                                          "tiers": {"hard": "opus"}},
                                 "loam-ui": {"provider": "antigravity"}}})
    check("Loam's own setting: the scope's job runs on it",
          (eng.engineer_choice(lane)["model"], eng.tier_choice(lane, "claude", "hard")["model"]),
          ("fable", "opus"))
    check("...and the old row is kept in the file, read by nothing",
          read_settings()["projects"]["loam-ui"], {"provider": "antigravity"})

    # -- the deploy toggle ---------------------------------------------------
    write_settings({"projects": {"loam-ui": {"provider": "antigravity", "deploy_on_land": True}}})
    check("migration: the old row's toggle carries over until Loam's says",
          eng.deploy_default(lane), True)
    write_settings({"projects": {"Loam": {"deploy_on_land": False},
                                 "loam-ui": {"deploy_on_land": True}}})
    check("...Loam's said off is off", eng.deploy_default(lane), False)
    write_settings({"projects": {"Loam": {"deploy_on_land": True}}})
    check("...Loam's on is on for the scope", eng.deploy_default(lane), True)
    check("...a full Loam brief never defaults to a launcher deploy",
          real_app_for(eng.platform_project())[0], None)

    # -- Settings ------------------------------------------------------------
    loam_autodeploy.app_for = lambda p: (("loam-ui", None) if p.name == "loam-ui"
                                         else (None, f"{p.name} has no app"))
    write_settings({"projects": {"loam-ui": {"provider": "antigravity"}}})
    view = profile.engineers({"Loam": 2})
    names = [r["project"] for r in view["engineers"]]
    check("Settings lists one Loam engineer and no app engineer", names, ["Loam", "webapp"])
    loam = view["engineers"][0]
    check("...Loam's row carries the app's toggle, labelled for what it does",
          (loam["deploy_app"], loam["deploy_on_land"], loam["deploy_label"], loam["scopes"]),
          ("loam-ui", False, "Deploy the app when an app-only job lands", ["app"]))
    check("...its jobs, the scope's among them, count as Loam's", loam["jobs_running"], 2)
    check("...a project engineer's toggle reads as before",
          view["engineers"][1]["deploy_label"], None)
    check("the old name reads Loam's row (a persona's `get`)",
          profile.engineer("loam-ui")["project"], "Loam")
    try:
        profile.update_engineer("loam-ui", {"provider": "codex"}, lambda p, f, v: v)
        refused = None
    except profile.ProfileError as exc:
        refused = (exc.status, "set them on Loam" in exc.message)
    check("a provider for the old name alone is refused, saying where it is set",
          refused, (409, True))
    check("...and nothing was written", read_settings(),
          {"projects": {"loam-ui": {"provider": "antigravity"}}})
    profile.update_engineer("loam-ui", {"deploy_on_land": True}, lambda p, f, v: v)
    check("the old name's toggle lands on Loam's row",
          read_settings()["projects"].get("Loam"), {"deploy_on_land": True})
    profile.update_engineer("Loam", {"deploy_on_land": False}, lambda p, f, v: v)
    check("...turned off on Loam, said off (absent would read the old row)",
          read_settings()["projects"]["Loam"], {"deploy_on_land": False})
    loam_autodeploy.app_for = real_app_for

    # -- the brief, through the launcher ------------------------------------
    os.environ["LOAM_AGENT"] = "otto"
    base_args = {"title": "Composer polish", "brief": "Tidy the composer.",
                 "involvement": "go and do it"}
    out = job.accept({**base_args, "project": "Loam", "scope": "app"}, "otto")
    check("accept: `Loam` + scope app from any persona is the scope's job",
          (out[0].name, out[2]["dir"].parent) if isinstance(out, tuple) else out,
          ("loam-ui", lane.jobs))
    head = out[2]["brief"].read_text() if isinstance(out, tuple) else ""
    check("...its header says whose job it is",
          ("project: loam-ui\n" in head, "engineer: Loam\n" in head, "scope: app\n" in head),
          (True, True, True))
    label = job.job_label(out[0], out[1]) if isinstance(out, tuple) else {}
    check("Activity reads it as Loam's, app-only",
          (label.get("project"), label.get("scope")), ("Loam", "app"))
    check("...a full Loam job has no scope",
          job.job_label(eng.platform_project(), "20260101-000000-aaaaaaaa")["scope"], None)
    out2 = job.accept({**base_args, "project": "loam-ui"}, "otto")
    check("accept: the old name still reaches the scope",
          out2[0].name if isinstance(out2, tuple) else out2, "loam-ui")
    said = job.accept({**base_args, "project": "webapp", "scope": "app"}, "otto")
    check("accept: a scope on another engineer is a sentence, nothing minted",
          isinstance(said, str) and "no scope" in said, True)
    said = job.accept({**base_args, "project": "nope"}, "otto")
    check("accept: an unknown project names the engineers and the scope",
          ("Loam, webapp" in said, "`scope: app`" in said, "loam-ui," in said),
          (True, True, False))
    # -- provisioning --------------------------------------------------------
    role = "# The engineer\n\nthe Loam engineer's role\n"
    files = prov.app_scope_render(role)
    check("app/'s harness: the four files, none of them memory",
          sorted(files), [".claude/settings.json", "CLAUDE.md", "project.md", "role.md"])
    check("...the role is the Loam engineer's, verbatim (one engineer)", files["role.md"], role)
    check("...the project file is the one Loam ships",
          files["project.md"], (AGENTS / "engineer" / "install" / "app-project.md").read_text())
    check("...CLAUDE.md imports the two", files["CLAUDE.md"].split(),
          ["@role.md", "@project.md"])
    check("...settings deny editing the harness",
          "Edit(/project.md)" in json.loads(files[".claude/settings.json"])["permissions"]["deny"],
          True)
    shipped = (AGENTS / "engineer" / "install" / "app-project.md").read_text()
    check("the shipped project file names no install (no home path)",
          "/home/" in shipped, False)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nFAILURES: {fails}")
sys.exit(1 if fails else 0)
