#!/usr/bin/env python3
"""Three model tiers per engineer (2026-11-15), held without a model.

  the setting      `tiers` on an engineer's row (a project's or `default`'s):
                   any of quick / standard / hard, each checked as `model` is;
                   null unsets one; a provider change drops them; the view
                   shows `own.tiers` and `effective.tiers` with each one's
                   source; the settings tool has a key per tier.
  the resolution   a tier set runs on its model; unset, on `engineer.model`,
                   else the built-in -- so an engineer nobody set is as before;
                   a continuation keeps its parent's model unless it names a
                   tier; a tier is `standard` when left out.
  the brief        `tier` is accepted, written into the header, read back;
                   an unknown one is refused with the three named.
  the quota retry  out of quota on an Antigravity job: once, on the tier
                   below, said in the receipt's `model` row; never past
                   `quick`, never onto the same model, never twice.

Run: python3 tests/test_engineer_tiers.py
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
TMP = Path(tempfile.mkdtemp(prefix="loam-engineer-tiers-"))
os.environ["LOAM_HOST_SETTINGS"] = str(TMP / "broker" / "settings.json")
os.environ["LOAM_PROJECTS_DIR"] = str(TMP / "projects")
os.environ["LOAM_AGY_CLI_HOME"] = str(TMP / "agy-home")
os.environ["LOAM_AGY_JOB_ROOTS"] = str(TMP / "jobs")
os.environ.pop("LOAM_WAKE", None)
(TMP / "projects").mkdir(parents=True)
for name, runtime in (("webapp", "codex"), ("gemapp", "antigravity")):
    (TMP / "projects" / f"{name}.json").write_text(json.dumps({
        "name": name, "repo": f"someone/{name}", "checkout": str(TMP / name),
        "runtime": runtime}))

sys.path.insert(0, str(AGENTS / "lib"))
import loam_agy_job as aj  # noqa: E402
import loam_engineer as eng  # noqa: E402
import loam_profile as profile  # noqa: E402
import loam_settings as settings  # noqa: E402

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:64} {got!r}"[:240]
          + ("" if ok else f"   want {want!r}"))


def load(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


job = load("engineer_job_under_test_tiers", AGENTS / "bin" / "engineer-job")


def fake_check(provider, field, value):
    """The broker's check, as a list: a model this provider lists."""
    listed = {"codex": {"gpt-a", "gpt-b", "gpt-c"},
              "antigravity": {"gemini-4-hydrogen-high", "gemini-4-hydrogen-low",
                              "gemini-4-hydrogen-xhigh"},
              "claude-code": {"fable", "sonnet"}}[provider]
    if field == "model" and value not in listed:
        raise profile.ProfileError(f"{value!r} is not a {provider} model", 400)
    return value


def write_settings(value: dict) -> None:
    path = Path(os.environ["LOAM_HOST_SETTINGS"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"engineers": value}))


def project(name: str) -> eng.Project:
    return eng.projects()[name]


try:
    webapp, gemapp = project("webapp"), project("gemapp")

    # -- the resolution, nothing set: exactly as before ---------------------
    write_settings({})
    check("tiers: three, standard the default",
          (eng.TIERS, eng.DEFAULT_TIER), (("quick", "standard", "hard"), "standard"))
    check("tier below: hard→standard→quick→none",
          [eng.tier_below(t) for t in ("hard", "standard", "quick", "x", None)],
          ["standard", "quick", None, None, None])
    for tier in (None, "quick", "standard", "hard"):
        got = eng.tier_choice(webapp, "codex", tier)
        check(f"nothing set, tier {tier}: the built-in, as choice_for",
              (got["model"], got["effort"], got["from"]),
              (*eng.choice_for(webapp, "codex"), "model"))
    check("nothing set: left out is standard",
          eng.tier_choice(webapp, "codex", None)["tier"], "standard")
    check("an unknown tier is standard", eng.tier_choice(webapp, "codex", "huge")["tier"],
          "standard")

    # -- a model set, no tiers: every tier on it ----------------------------
    write_settings({"projects": {"webapp": {"provider": "codex", "model": "gpt-b",
                                            "effort": "high"}}})
    check("model set, no tiers: every tier on engineer.model",
          {t: eng.tier_choice(webapp, "codex", t)["model"] for t in eng.TIERS},
          {"quick": "gpt-b", "standard": "gpt-b", "hard": "gpt-b"})

    # -- tiers set ----------------------------------------------------------
    write_settings({"projects": {"webapp": {"provider": "codex", "model": "gpt-b",
                                            "effort": "high",
                                            "tiers": {"quick": "gpt-a", "hard": "gpt-c"}}}})
    got = {t: (eng.tier_choice(webapp, "codex", t)["model"],
               eng.tier_choice(webapp, "codex", t)["from"]) for t in eng.TIERS}
    check("tiers set: each its own, standard unset falls back",
          got, {"quick": ("gpt-a", "tier"), "standard": ("gpt-b", "model"),
                "hard": ("gpt-c", "tier")})
    check("tiers set: the thinking level is the engineer's",
          eng.tier_choice(webapp, "codex", "hard")["effort"], "high")
    check("a tier set for codex does not apply to a claude continuation",
          eng.tier_choice(webapp, "claude", "hard")["model"], eng.ENGINEER_MODEL)

    # -- the default's tiers reach an engineer that follows it --------------
    write_settings({"default": {"provider": "antigravity",
                                "tiers": {"hard": "gemini-4-hydrogen-xhigh",
                                          "quick": "gemini-4-hydrogen-low"}}})
    check("default's tiers reach a follower",
          [eng.tier_choice(gemapp, "antigravity", t)["model"] for t in eng.TIERS],
          ["gemini-4-hydrogen-low", eng.AGY_ENGINEER_MODEL, "gemini-4-hydrogen-xhigh"])
    check("...and webapp too, which follows it onto antigravity",
          eng.tier_choice(webapp, "antigravity", "hard")["model"],
          "gemini-4-hydrogen-xhigh")
    loam = eng.platform_project()
    check("never Loam's (claude only, its own row or none)",
          eng.tier_choice(loam, "claude", "hard")["model"], eng.ENGINEER_MODEL)

    # -- the setting: PATCH /v1/engineers/<x> {"tiers": ...} -----------------
    write_settings({})
    view = profile.update_engineer("webapp", {"tiers": {"hard": "gpt-c"}}, fake_check)
    row = next(r for r in view["engineers"] if r["project"] == "webapp")
    check("PATCH tiers: a provider row made for it (the one it runs on)",
          (row["own"]["provider"], row["own"]["tiers"]),
          ("codex", {"quick": None, "standard": None, "hard": "gpt-c"}))
    check("view: effective tiers, each with its source",
          row["effective"]["tiers"],
          {"quick": {"model": eng.CODEX_ENGINEER_MODEL, "from": "model"},
           "standard": {"model": eng.CODEX_ENGINEER_MODEL, "from": "model"},
           "hard": {"model": "gpt-c", "from": "tier"}})
    profile.update_engineer("webapp", {"tiers": {"quick": "gpt-a"}}, fake_check)
    stored = json.loads(Path(os.environ["LOAM_HOST_SETTINGS"]).read_text())["engineers"]
    check("PATCH one tier keeps the others",
          stored["projects"]["webapp"]["tiers"], {"quick": "gpt-a", "hard": "gpt-c"})
    profile.update_engineer("webapp", {"tiers": {"quick": None}}, fake_check)
    stored = json.loads(Path(os.environ["LOAM_HOST_SETTINGS"]).read_text())["engineers"]
    check("null unsets one", stored["projects"]["webapp"]["tiers"], {"hard": "gpt-c"})
    for patch, words in (({"tiers": {"hard": "gpt-z"}}, "not a codex model"),
                         ({"tiers": {"huge": "gpt-a"}}, "no tier huge"),
                         ({"tiers": "gpt-a"}, "is an object"),
                         ({"tiers": {}}, "is an object")):
        try:
            profile.update_engineer("webapp", patch, fake_check)
            check(f"refused: {patch}", "accepted", words)
        except profile.ProfileError as exc:
            check(f"refused: {patch}", words in str(exc), True)
    stored = json.loads(Path(os.environ["LOAM_HOST_SETTINGS"]).read_text())["engineers"]
    check("a refusal writes nothing", stored["projects"]["webapp"]["tiers"], {"hard": "gpt-c"})
    profile.update_engineer("webapp", {"provider": "antigravity"}, fake_check)
    stored = json.loads(Path(os.environ["LOAM_HOST_SETTINGS"]).read_text())["engineers"]
    check("a provider change drops its tiers",
          stored["projects"]["webapp"], {"provider": "antigravity"})
    profile.update_engineer("webapp", {"provider": "codex",
                                       "tiers": {"hard": "gpt-c"}}, fake_check)
    profile.update_engineer("webapp", {"provider": None}, fake_check)
    stored = json.loads(Path(os.environ["LOAM_HOST_SETTINGS"]).read_text()).get("engineers") or {}
    check("provider null clears the row, tiers with it",
          "webapp" in (stored.get("projects") or {}), False)
    profile.update_engineer("default", {"provider": "antigravity",
                                        "tiers": {"standard": "gemini-4-hydrogen-low"}},
                            fake_check)
    d = profile.engineer("default")
    check("default: own and effective tiers",
          (d["own"]["tiers"]["standard"], d["effective"]["tiers"]["standard"],
           d["effective"]["tiers"]["hard"]),
          ("gemini-4-hydrogen-low", {"model": "gemini-4-hydrogen-low", "from": "tier"},
           {"model": eng.AGY_ENGINEER_MODEL, "from": "model"}))
    try:
        profile.update_engineer("Loam", {"tiers": {"hard": "gpt-c"}}, fake_check)
        check("Loam's tiers are Claude Code models", "accepted", "refused")
    except profile.ProfileError as exc:
        check("Loam's tiers are Claude Code models", "claude-code" in str(exc), True)
    except KeyError:
        check("Loam's tiers are Claude Code models (no Loam in the scratch registry)",
              True, True)

    # -- the settings tool: one key per tier ---------------------------------
    for tier in eng.TIERS:
        e = settings.entry(f"engineer.tiers.{tier}")
        check(f"settings: engineer.tiers.{tier} on the engineers route",
              (e.route, e.body("m", ""), e.unattended),
              ("PATCH /v1/engineers/{engineer}", {"tiers": {tier: "m"}}, False))
    check("settings: a tier reads back what a brief at it runs on",
          settings.entry("engineer.tiers.hard").pick(
              {"effective": {"tiers": {"hard": {"model": "x", "from": "tier"}}}}, ""),
          {"model": "x", "from": "tier"})

    # -- the brief: header in, header out ------------------------------------
    jd = TMP / "jobs" / "20261115-000000-bbbbbbbb"
    jd.mkdir(parents=True)
    p = job.paths(eng.Project("gemapp", TMP / "gemapp", TMP / "jobs", platform=False,
                              record={"runtime": "antigravity"}),
                  "20261115-000000-bbbbbbbb")
    job.write_brief(jd, gemapp, "T", "ada", "go", None, "", "Body.",
                    {"tier": "hard"})
    check("the brief's tier is in its header and read back",
          job.read_brief_meta(p)["tier"], "hard")
    job.write_brief(jd, gemapp, "T", "ada", "go", None, "", "Body.")
    check("no tier: empty (standard at launch)", job.read_brief_meta(p)["tier"], "")
    (jd / "brief.md").write_text("---\ntier: enormous\n---\n\nBody.\n")
    check("a hand-written unknown tier reads as none", job.read_brief_meta(p)["tier"], "")
    refusal = job.accept({"project": "webapp", "title": "T", "brief": "B",
                          "involvement": "go", "tier": "enormous"}, "ada")
    check("accept: an unknown tier is refused with the three named",
          isinstance(refusal, str) and all(t in refusal for t in eng.TIERS), True)

    # -- a continuation keeps its parent's model -----------------------------
    write_settings({"projects": {"gemapp": {"provider": "antigravity",
                                            "tiers": {"hard": "gemini-4-hydrogen-xhigh",
                                                      "quick": "gemini-4-hydrogen-low"}}}})
    parent_id = "20261115-000000-cccccccc"
    pdir = TMP / "jobs" / parent_id
    pdir.mkdir()
    (pdir / "engine.json").write_text(json.dumps(
        {"engine": "antigravity", "model": "gemini-4-hydrogen-xhigh", "tier": "hard"}))
    (pdir / "runtime").write_text("antigravity\n")
    gem = eng.Project("gemapp", TMP / "gemapp", TMP / "jobs", platform=False,
                      record={"runtime": "antigravity"})
    got = job.job_choice(gem, "antigravity", {"tier": ""}, parent_id)
    check("continuation, no tier: the parent's model and tier",
          (got["model"], got["tier"], got["from"]),
          ("gemini-4-hydrogen-xhigh", "hard", "parent"))
    got = job.job_choice(gem, "antigravity", {"tier": "quick"}, parent_id)
    check("continuation naming a tier: that tier's model",
          (got["model"], got["from"]), ("gemini-4-hydrogen-low", "tier"))
    (pdir / "engine.json").unlink()
    got = job.job_choice(gem, "antigravity", {"tier": ""}, parent_id)
    check("continuation of a job with no engine record: standard, as before",
          (got["model"], got["from"]), (eng.AGY_ENGINEER_MODEL, "model"))
    got = job.job_choice(gem, "antigravity", {"tier": "hard"}, None)
    check("a fresh job at hard", got["model"], "gemini-4-hydrogen-xhigh")
    check("engine.json records the tier",
          (job.write_engine(p, "antigravity", ["agy", "--model", "m"], "hard"),
           job.job_engine(p))[1],
          {"engine": "antigravity", "model": "m", "tier": "hard"})

    # -- the receipt's model row ---------------------------------------------
    check("model row: a set tier",
          job.model_text({"tier": "hard", "model": "m", "from": "tier"}),
          "tier `hard` → `m` (the tier's setting)")
    check("model row: an unset tier says it fell back",
          "no standard model set" in job.model_text(
              {"tier": "standard", "model": "m", "from": "model"}), True)
    check("model row: a kept parent model",
          "kept from job `J`" in job.model_text(
              {"tier": "hard", "model": "m", "from": "parent"}, "J"), True)

    # -- the quota fallback --------------------------------------------------
    down, why = job.quota_fallback(gem, "antigravity",
                                   {"tier": "hard", "model": "gemini-4-hydrogen-xhigh"})
    check("hard out of quota: standard, the built-in here",
          (down["tier"], down["model"]), ("standard", eng.AGY_ENGINEER_MODEL))
    down, why = job.quota_fallback(gem, "antigravity",
                                   {"tier": "quick", "model": "gemini-4-hydrogen-low"})
    check("quick: nothing below", (down, "no tier below" in why), (None, True))
    down, why = job.quota_fallback(gem, "antigravity",
                                   {"tier": "standard", "model": "gemini-4-hydrogen-low"})
    check("a tier below on the same model: no retry", (down, "same model" in why),
          (None, True))
    down, _ = job.quota_fallback(gem, "antigravity",
                                 {"tier": None, "model": "gemini-4-hydrogen-xhigh"})
    check("no recorded tier counts as standard", down["tier"], "quick")

    # -- the retry itself, on stubbed sessions --------------------------------
    def stub(name: str, body: str) -> Path:
        path = TMP / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)
        return path

    def event(obj: dict) -> str:
        return "echo '" + json.dumps(obj) + "'\n"

    quota = stub("agy-quota", event({"event": "init", "conversation_id": "Q-1", "init": {}})
                 + event({"event": "result", "result": {
                     "conversation_id": "Q-1", "status": "ERROR",
                     "error": "RESOURCE_EXHAUSTED: quota"}}) + "exit 1\n")
    good = stub("agy-good", event({"event": "init", "conversation_id": "G-1", "init": {}})
                + event({"event": "result", "result": {
                    "conversation_id": "G-1", "status": "SUCCESS", "num_turns": 1}}))
    (TMP / "agy-home" / "implicit").mkdir(parents=True)
    (TMP / "agy-home" / "conversations").mkdir(parents=True)
    rdir = TMP / "jobs" / "20261115-000000-dddddddd"
    rdir.mkdir()
    rp = job.paths(gem, "20261115-000000-dddddddd")
    calls = []

    def argv_for(model, effort, text="P"):
        calls.append((model, text))
        return [str(good if model == eng.AGY_ENGINEER_MODEL else quota), "--model", model]

    choice = {"tier": "hard", "model": "gemini-4-hydrogen-xhigh", "effort": None,
              "from": "tier"}
    rc, run, timed_out, argv, model, _ = job.run_antigravity(
        gem, "antigravity", choice, argv_for, argv_for(choice["model"], None),
        str(TMP), {"max_minutes": None}, rp, None, "THE PROMPT")
    check("retry: ran once more, on the tier below, and finished",
          (rc, run.failed, model, choice["retry"]["tier"], choice["retry"]["model"]),
          (0, None, eng.AGY_ENGINEER_MODEL, "standard", eng.AGY_ENGINEER_MODEL))
    check("retry: the session told why, its first model named",
          "stopped out of quota" in calls[-1][1] and "gemini-4-hydrogen-xhigh" in calls[-1][1],
          True)
    check("retry: two sessions, no more", len(calls), 2)
    check("retry: the job's conversations are both recorded",
          json.loads((rdir / "antigravity.json").read_text())["conversations"],
          ["Q-1", "G-1"])
    check("retry: the session id is the retry's", rp["session"].read_text().strip(), "G-1")
    check("retry: said in the receipt row",
          "retried once on tier `standard`" in job.model_text(choice), True)

    # Out of quota on the retry too: it stops there.
    write_settings({"projects": {"gemapp": {"provider": "antigravity",
                                            "tiers": {"hard": "gemini-4-hydrogen-xhigh",
                                                      "standard": "gemini-4-hydrogen-high"}}}})
    calls.clear()

    def all_quota(model, effort, text="P"):
        calls.append((model, text))
        return [str(quota), "--model", model]

    choice = {"tier": "hard", "model": "gemini-4-hydrogen-xhigh", "effort": None,
              "from": "tier"}
    rc, run, _, _, model, _ = job.run_antigravity(
        gem, "antigravity", choice, all_quota, all_quota(choice["model"], None),
        str(TMP), {"max_minutes": None}, rp, None, "P")
    check("quota twice: one retry and no loop",
          (len(calls), run.quota, model), (2, True, "gemini-4-hydrogen-high"))
    calls.clear()
    choice = {"tier": "quick", "model": "gemini-4-hydrogen-low", "effort": None,
              "from": "tier"}
    job.run_antigravity(gem, "antigravity", choice, all_quota,
                        all_quota(choice["model"], None), str(TMP),
                        {"max_minutes": None}, rp, None, "P")
    check("quota at quick: not retried, and why",
          (len(calls), "no tier below" in choice.get("no_retry", "")), (1, True))
    check("not-retried: said in the receipt row",
          "not retried" in job.model_text(choice), True)
    calls.clear()
    (rdir / "cancelled").write_text("{}")
    choice = {"tier": "hard", "model": "gemini-4-hydrogen-xhigh", "effort": None,
              "from": "tier"}
    job.run_antigravity(gem, "antigravity", choice, all_quota,
                        all_quota(choice["model"], None), str(TMP),
                        {"max_minutes": None}, rp, None, "P")
    check("a cancelled job is not retried", len(calls), 1)

    # -- the brief tool's signature ------------------------------------------
    src = (AGENTS / "mcp" / "engineer_server.py").read_text()
    check("brief tool: an optional tier, passed to the launcher",
          ('tier: str = ""' in src, '"tier": tier' in src, "**`tier`" in src),
          (True, True, True))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{'OK' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
