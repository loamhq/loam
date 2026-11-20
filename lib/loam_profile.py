"""Read and update agent, engineer and reviewer profiles. Agent display preferences
belong in agent.json. A rename keeps the workspace ID used by units, sessions
and existing mentions. Engineer and reviewer choices belong in locked runtime
settings so a page action cannot dirty the checkout. The reviewer timer applies
its schedule through a drop-in. See broker/README.md for the route contract."""

from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path

import loam_config as lc
import loam_provider_defaults as providers

PROVIDERS = ("claude-code", "codex", "antigravity")
PROVIDER_NAMES = {"claude-code": "Claude Code", "codex": "Codex",
                  "antigravity": "Antigravity"}
# `wake_schedule` and `wake_timezone` left 2026-11-19: the daily wake's time
# is its reflection record's alone (lib/loam_schedule.wake_time).
BASE_KEYS = ("name", "display", "emoji", "workspace", "vault")
DEFAULT_KEYS = ("provider", "model", "effort")
SETTABLE = ("display", *DEFAULT_KEYS)
# Kept over a provision besides the settable ones: an uploaded face and the
# archive mark (lib/loam_agents.py, 2026-11-14), and the retired emoji an
# older file still holds (2026-11-16), so its bytes do not change.
KEPT = (*SETTABLE, "face", "archived", "self_name", "emoji")
DISPLAY_MAX = 40
# Words a display name may not be: the owner's, and `@all` / `@everyone`.
RESERVED_NAMES = tuple(dict.fromkeys((lc.OWNER_ID, *lc.LEGACY_OWNER_IDS, "all", "everyone", "system")))

# bin/wake's model when the environment names none: a wake's and a cron's
# when the persona's own default is not a Claude Code one.
WAKE_MODEL_DEFAULT = "fable"

# The reviewer is a first-party plugin since 2026-11-14 (first release L9,
# plugins/reviewer/): its timer is the plugin's `nightly`, and its schedule is
# the plugin's `schedule` setting. The legacy static timer and its drop-in
# are what that replaced.
REVIEWER_PLUGIN = "reviewer"
REVIEWER_TIMER = "loam-plugin-reviewer--nightly.timer"
LEGACY_REVIEWER_TIMER = "loam-reviewer.timer"
REVIEWER_DEFAULT_SCHEDULE = f"daily at 04:20 {lc.ZONE_TOKEN}"   # plugins/reviewer/PLUGIN.md
REVIEWER_DROPIN = "schedule.conf"
# The engineers' providers: the CLIs a job runs on (lib/loam_engineer.py).
# Antigravity since 2026-11-15 (lib/loam_agy_job.py), offered and chosen by
# nobody until the owner's trial says so.
ENGINEER_PROVIDERS = ("claude-code", "codex", "antigravity")
# The providers a persona's wake and cron run on (bin/wake): Codex since
# 2026-11-13 (lib/loam_codex_wake.py). Antigravity is not one yet; see
# ``lane_choice``.
LANE_PROVIDERS = ("claude-code", "codex")
# The reviewer's providers (bin/review): Antigravity since 2026-11-15, a
# choice beside Claude Code, chosen by nobody until the owner says so. On
# Antigravity its model is an id `agy models` lists, its thinking level in
# the id (lib/loam_antigravity.py, LEVEL_RE), so there is no separate effort.
REVIEWER_PROVIDERS = ("claude-code", "antigravity")
REVIEWER_AGY_MODEL = os.environ.get("LOAM_AGY_REVIEWER_MODEL", "gemini-4-hydrogen-high")


class ProfileError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


# ---------------------------------------------------------------------------
# What a lane runs on. Light on purpose: bin/wake and bin/review import it.
# ---------------------------------------------------------------------------

def lane_choice(config: dict, wake_model: str, pinned: bool = False) -> dict:
    """The next wake, cron, or background delivery's provider and choices."""
    explicit = providers.configured(config)
    provider = (explicit if explicit in LANE_PROVIDERS else None) if explicit else (
        providers.automatic(LANE_PROVIDERS))
    choice = providers.agent_choice(config, provider)
    if provider == "claude-code":
        choice["model"] = wake_model if pinned else choice["model"] or wake_model
    if provider is None:
        choice["note"] = (f"Wakes and scheduled runs do not support {PROVIDER_NAMES[explicit]}."
                          if explicit else
                          "Connect Claude Code or Codex to run wakes and scheduled runs.")
    else:
        choice["note"] = None
    return choice


def claude_model(config: dict | None) -> str:
    """Use the persona's Claude Code model when its profile selects that provider,
    otherwise use WAKE_MODEL_DEFAULT. Provisioning and the providers row share this
    default.
    """
    config = config if isinstance(config, dict) else {}
    if (config.get("provider") or "claude-code") != "claude-code":
        return WAKE_MODEL_DEFAULT
    return providers.agent_choice(config, "claude-code")["model"] or WAKE_MODEL_DEFAULT


def sync_settings_model(workspace: Path, config: dict) -> bool:
    """Set the ``model`` of ``workspace/.claude/settings.json`` to
    ``claude_model(config)``, keeping every other byte as bin/provision
    renders it (``json.dumps(indent=2)`` and a newline; a dict keeps its key
    order). True when it wrote. A file that is missing or unreadable is left
    for the next provision: this never creates one."""
    path = Path(workspace) / ".claude" / "settings.json"
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    want = claude_model(config)
    if not isinstance(settings, dict) or settings.get("model") == want:
        return False
    settings["model"] = want
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return True


def _host() -> dict:
    import loam_engineer  # noqa: PLC0415 -- stdlib-only, and the path's home
    try:
        data = json.loads(loam_engineer.host_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def reviewer_stored() -> dict:
    value = _host().get("reviewer")
    return value if isinstance(value, dict) else {}


def reviewer_provider() -> str | None:
    """The reviewer's explicit setting, or the first connected eligible CLI."""
    pinned = os.environ.get("LOAM_REVIEWER_PROVIDER")
    if pinned in REVIEWER_PROVIDERS:
        return pinned
    value = reviewer_stored().get("provider")
    return value if value in REVIEWER_PROVIDERS else providers.automatic(REVIEWER_PROVIDERS)


def reviewer_choice(model: str, pinned: bool = False,
                    provider: str | None = None) -> tuple[str | None, str | None]:
    """The reviewer's model and effort for the selected provider."""
    provider = provider or reviewer_provider()
    if not provider:
        return None, None
    stored = reviewer_stored()
    choice = providers.agent_choice(stored, provider)
    if provider == "antigravity":
        if pinned or choice["model"]:
            return (model if pinned else choice["model"]), None
        if stored.get("provider") == provider or os.environ.get("LOAM_REVIEWER_PROVIDER") == provider:
            return REVIEWER_AGY_MODEL, None
        return providers.compatible_defaults(provider, REVIEWER_AGY_MODEL, None)
    return (model if pinned else choice["model"] or model), choice["effort"]


# ---------------------------------------------------------------------------
# agent.json
# ---------------------------------------------------------------------------

def render_agent(config: dict) -> str:
    """agent.json as text, in its one order: the provisioned keys, then the
    defaults that are set, then anything else the file carries. bin/provision
    and bin/check-drift render through this too, so a page's write and a
    provision agree byte for byte."""
    ordered = {k: config[k] for k in BASE_KEYS if k in config}
    for k in DEFAULT_KEYS:
        if config.get(k) is not None:
            ordered[k] = config[k]
    for k, v in config.items():
        if k not in ordered and k not in DEFAULT_KEYS:
            ordered[k] = v
    return json.dumps(ordered, indent=2, ensure_ascii=False) + "\n"


def self_name(agent: str, config: dict | None) -> str:
    """The name an agent's own files call it by ("You are Old Sam"): the name
    it was made with (`self_name`, lib/loam_agents.py, 2026-11-14), else its
    id's. A rename is the app's display and never changes this."""
    value = (config or {}).get("self_name") if isinstance(config, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else agent.capitalize()


def kept(config: dict) -> dict:
    """The settable keys a file carries (and its face and archive mark), for a
    provision to keep."""
    return {k: config[k] for k in KEPT if k in config and config[k] is not None}


def _ai_labs() -> Path:
    """``LOAM_AI_LABS`` (as lib/loam_threads.py reads it), else the box's."""
    if os.environ.get("LOAM_AI_LABS"):
        return Path(os.environ["LOAM_AI_LABS"])
    import loam_broker as cfg  # noqa: PLC0415
    return cfg.AI_LABS


def _vault() -> Path:
    if os.environ.get("LOAM_VAULT"):
        return Path(os.environ["LOAM_VAULT"])
    import loam_broker as cfg  # noqa: PLC0415
    return cfg.VAULT


def roster() -> list[str]:
    root = _ai_labs()
    try:
        return sorted(d.name for d in root.iterdir()
                      if d.is_dir() and (d / "agent.json").is_file())
    except OSError:
        return []


def agent_path(agent: str) -> Path:
    return _ai_labs() / agent / "agent.json"


def read_agent(agent: str) -> dict:
    if agent not in roster():
        raise ProfileError(f"no agent named {agent!r} on this box", 404)
    try:
        config = json.loads(agent_path(agent).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProfileError(f"{agent}'s agent.json cannot be read ({exc})", 500) from None
    if not isinstance(config, dict):
        raise ProfileError(f"{agent}'s agent.json is not an object", 500)
    return config


def _clean_display(agent: str, value) -> str:
    if value is None:
        return agent.capitalize()
    if not isinstance(value, str):
        raise ProfileError("`display` is a name, or null for the agent's own")
    name = " ".join(value.split())
    if not name or len(name) > DISPLAY_MAX:
        raise ProfileError(f"`display` is 1 to {DISPLAY_MAX} characters")
    if any(unicodedata.category(c).startswith("C") for c in name):
        raise ProfileError("`display` has a control character in it")
    if name.lower() in RESERVED_NAMES:
        raise ProfileError(f"{name!r} is a word the threads keep for themselves")
    for other in roster():
        if other == agent:
            continue
        try:
            shown = json.loads(agent_path(other).read_text(encoding="utf-8")).get("display")
        except (OSError, ValueError, AttributeError):
            shown = None
        if name.lower() in (other.lower(), str(shown or "").lower()):
            raise ProfileError(f"{name!r} is {other}'s name; an @ would not know "
                               f"which of them it meant", 409)
    return name


def update_agent(agent: str, patch: dict, check) -> dict:
    """Write the named fields of an agent's agent.json in one write.

    ``patch`` takes ``display``, ``provider``, ``model`` and
    ``effort``; null restores the id's name, automatic provider selection, or the provider's own model and level. A provider that
    changes takes the model and level back to its own unless the same patch
    names them. ``check(provider, field, value)`` is the broker's: it
    answers the value as that provider takes it or raises. Nothing is
    written on a refusal. Answers the new agent.json."""
    if not isinstance(patch, dict):
        raise ProfileError("the body is an object")
    unknown = sorted(set(patch) - set(SETTABLE) - {"by"})
    if unknown:
        raise ProfileError(f"an agent's profile has no field {', '.join(unknown)}; "
                           f"it takes {', '.join(SETTABLE)}")
    if not set(patch) - {"by"}:
        raise ProfileError(f"name a field to change: {', '.join(SETTABLE)}")
    import loam_schedule  # noqa: PLC0415 -- its lock is the agent.json writers' lock
    with loam_schedule.locked():
        config = read_agent(agent)
        new = dict(config)
        if "display" in patch:
            new["display"] = _clean_display(agent, patch["display"])
        before = providers.configured(config)
        if "provider" in patch:
            p = patch["provider"]
            if p is not None and p not in PROVIDERS:
                raise ProfileError(f"`provider` is one of {', '.join(PROVIDERS)}, "
                                   f"or null to use a connected provider")
            if p is None:
                new.pop("provider", None)
            else:
                new["provider"] = p
            if p is None or p != (before or "claude-code"):
                for k in ("model", "effort"):
                    if k not in patch:
                        new.pop(k, None)
        for field in ("model", "effort"):
            if field not in patch:
                continue
            if patch[field] is None:
                new.pop(field, None)
            else:
                provider = providers.configured(new) or providers.automatic()
                if provider is None:
                    raise ProfileError(providers.SETUP, 409)
                if not providers.configured(new) and provider != "claude-code":
                    for k in ("model", "effort"):
                        new.pop(k, None)
                new[field] = check(provider, field, patch[field])
                new["provider"] = provider
        target = agent_path(agent)
        if new != config:
            text = render_agent(new)
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, target)
        # The conversational default follows the profile (2026-11-19): a
        # chat started with no model reads settings.json, so the profile's
        # model is written there too, or that chat runs on another model.
        sync_settings_model(target.parent, new)
    return new


# ---------------------------------------------------------------------------
# The profile's parts
# ---------------------------------------------------------------------------

def _face(agent: str, config: dict) -> dict | None:
    import loam_agents  # noqa: PLC0415
    return loam_agents.face_of(agent, config)


def identity(agent: str, config: dict) -> dict:
    display = config.get("display")
    return {"id": agent,
            "display": display if isinstance(display, str) and display.strip()
            else agent.capitalize(),
            "face": _face(agent, config),
            "archived": config.get("archived") or None,
            "default_display": agent.capitalize(),
            "rename": "display name only: the id is the workspace, the units "
                      "and every old @; @ matches the id and the display name"}


def defaults(agent: str, config: dict, wake_model: str = "fable",
             persona_model=None) -> dict:
    """The agent's default provider, model and thinking level: as set
    (null where unset), the effective choices, and any paused scheduled lane."""
    provider = providers.configured(config)
    effective = providers.agent_choice(config, provider or providers.automatic())
    if effective["provider"] == "claude-code" and persona_model is not None:
        effective["model"] = effective["model"] or persona_model(agent)
    lane = lane_choice(config, wake_model)
    note = lane.pop("note")
    stored = providers.agent_choice(config, effective["provider"])
    return {
        "provider": provider, "model": stored["model"], "effort": stored["effort"],
        "effective": effective,
        "threads": effective,
        "wakes": lane,
        "not_honoured": [note] if note else [],
    }


def _ref(root: str, path: str, host: Path) -> dict:
    return {"root": root, "path": path, "exists": host.exists()}


def files(agent: str) -> dict:
    """Paths of who the agent is, as Files opens them (root ``spaces``)."""
    ws = _ai_labs() / agent
    skills = ws / "skills"
    names = []
    if skills.is_dir():
        for entry in sorted(skills.iterdir()):
            if entry.name.startswith((".", "_")):
                continue
            if entry.is_dir() or entry.suffix == ".md":
                names.append(entry.stem if entry.is_file() else entry.name)
    # What loads, in the shape the agent is in (2026-11-18,
    # lib/loam_loading.py): its map once it has one, else the three files
    # it has always had. The old files of an agent with a map are ordinary
    # files of its own and are not listed as loading.
    if (ws / "map.md").is_file():
        memory = {"map": _ref("spaces", f"{agent}/map.md", ws / "map.md")}
    else:
        memory = {
            "always": _ref("spaces", f"{agent}/_always.md", ws / "_always.md"),
            "active": _ref("spaces", f"{agent}/_active.md", ws / "_active.md"),
            "reflection": _ref("spaces", f"{agent}/_reflection.md",
                               ws / "_reflection.md"),
        }
    memory["folder"] = _ref("spaces", f"{agent}/memory", ws / "memory")
    return {
        "workspace": _ref("spaces", agent, ws),
        "soul": _ref("spaces", f"{agent}/soul.md", ws / "soul.md"),
        "config": _ref("spaces", f"{agent}/config.md", ws / "config.md"),
        "memory": memory,
        "skills": {**_ref("spaces", f"{agent}/skills", skills),
                   "count": len(names), "names": names},
    }


DAY_WORDS = {"daily": "Every day", "every day": "Every day",
             "weekdays": "Weekdays", "weekends": "Weekends"}


def clock(hour: int, minute: int) -> str:
    h = hour % 12 or 12
    return f"{h}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


def plain(schedule_text: str | None, kind: str | None = None) -> str | None:
    """A schedule in plain words, Pacific time: ``daily at 03:20 PT`` is
    "Every day at 3:20 AM", ``Wednesdays at 05:00 PT`` "Wednesdays at
    5:00 AM", a one-off ``2026-11-20 14:30 PT`` "Once, Nov 20 at 2:30 PM"."""
    if not schedule_text:
        return None
    text = " ".join(schedule_text.split())
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})\s*[A-Za-z][A-Za-z0-9_/+-]*$", text, re.I)
    if m:
        months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
        return (f"Once, {months[int(m[2]) - 1]} {int(m[3])} at "
                f"{clock(int(m[4]), int(m[5]))}")
    m = re.match(r"^(.+?)\s+at\s+(\d{1,2}):(\d{2})\s*[A-Za-z][A-Za-z0-9_/+-]*$", text, re.I)
    if not m:
        return text
    when = m[1].strip()
    words = DAY_WORDS.get(when.lower(), when[:1].upper() + when[1:])
    return f"{words} at {clock(int(m[2]), int(m[3]))}"


def label(name: str, kind: str | None) -> str:
    if kind == "reflection" or name == "reflection":
        return "Nightly wake"
    return name.replace("-", " ").capitalize()


def schedule(agent: str) -> list[dict]:
    """The agent's schedule rows, as ``GET /v1/schedules/<agent>`` answers
    them (lib/loam_activity.py), each with a ``label`` and its ``plain``
    words; the wake first. Writes stay on that route."""
    import loam_activity  # noqa: PLC0415
    rows = loam_activity.schedules(agent)
    for row in rows:
        row["label"] = label(row["name"], row.get("kind"))
        row["plain"] = plain(row.get("schedule"), row.get("kind"))
    return sorted(rows, key=lambda r: (r.get("kind") != "reflection", r["name"]))


# ---------------------------------------------------------------------------
# Engineers
# ---------------------------------------------------------------------------

def _choice_row(provider, model, effort, tiers=None) -> dict:
    """Read an engineer's own settings. Include all three tier keys with null for each
    unset override.
    """
    tiers = tiers if isinstance(tiers, dict) else {}
    return {"provider": provider, "model": model, "effort": effort,
            "tiers": {t: tiers.get(t) or None for t in ("quick", "standard", "hard")}}


def tier_words(patch) -> str:
    """A tiers change as General says it: "hard tier to X, quick tier back
    to the engineer's model"."""
    if not isinstance(patch, dict):
        return f"tiers to {patch}"
    return ", ".join(f"{t} tier to {v}" if v else f"{t} tier back to the engineer's model"
                     for t, v in patch.items())


def _effective_tiers(tiers: dict, model) -> dict:
    """``{tier: {"model", "from"}}``: what a brief at each tier runs on --
    the tier's model when set, else the engineer's model (``from``
    ``model``), as lib/loam_engineer.py ``tier_choice`` resolves it."""
    return {t: ({"model": tiers[t], "from": "tier"} if tiers.get(t)
                else {"model": model, "from": "model"})
            for t in ("quick", "standard", "hard")}


def engineers(running: dict | None = None) -> dict:
    """Every engineer, Loam's first, and the default for every other project.

    ``own`` is the engineer's own setting (null: it follows the default, or
    its record); ``effective`` is what its next job runs on, with
    ``source`` ``own``, ``default`` or ``record``. ``running`` maps a
    project to its jobs running now (the broker's reading)."""
    import loam_autodeploy  # noqa: PLC0415
    import loam_engineer as eng  # noqa: PLC0415
    stored = eng.engineer_settings()
    own_rows = stored.get("projects") or {}
    rows = []
    # One row per engineer: the Loam engineer's app-only scope (2026-11-19)
    # is not a second one. Its deploy toggle is on Loam's row.
    lane = eng.app_lane()
    for name, project in eng.engineers().items():
        own = own_rows.get(name) if isinstance(own_rows.get(name), dict) else {}
        choice = eng.engineer_choice(project, stored)
        app = None
        deploy_on = own.get("deploy_on_land") is True
        if not project.platform and not project.pull_request:
            app, _ = loam_autodeploy.app_for(project)
        elif project.platform and lane is not None:
            app, _ = loam_autodeploy.app_for(lane)
            deploy_on = eng.deploy_default(lane)
        if project.platform:
            instructions = {"root": "vault",
                            "path": "40_PROJECTS/Loam/engineer/projects/loam.md",
                            "system": {"root": "system",
                                       "path": "Engineer/Platform project.md"},
                            "host_path": str(eng.ENGINEER / "project.md")}
        else:
            instructions = {"root": None, "path": None,
                            "host_path": str(project.root / "project.md"),
                            "note": "in the project's own repository, which "
                                    "its engineer edits; Files does not reach it"}
        rows.append({
            "project": name, "platform": project.platform, "repo": project.repo,
            "own": (_choice_row(own.get("provider"), own.get("model"), own.get("effort"),
                                own.get("tiers"))
                    if own.get("provider") else None),
            "effective": {"provider": eng.PROVIDER_OF.get(choice["runtime"]),
                          "model": choice["model"], "effort": choice["effort"],
                          "tiers": _effective_tiers(eng.tier_models(project, stored),
                                                    choice["model"]),
                          "source": choice["source"]},
            # Loam's own too since 2026-11-19: "It should be like the others."
            "providers": list(ENGINEER_PROVIDERS),
            "deploy_on_land": deploy_on if app else None,
            "deploy_app": app,
            # What the toggle does, as the page says it: Loam's deploys the
            # app after an app-only job; a full Loam job deploys itself.
            "deploy_label": ("Deploy the app when an app-only job lands"
                             if project.platform else "Deploy when a job lands")
                            if app else None,
            "scopes": [eng.APP_SCOPE] if project.platform and lane is not None else [],
            "lands_by": "pull_request" if project.pull_request else "main",
            "instructions": instructions,
            "jobs_running": (running or {}).get(name, 0),
        })
    rows.sort(key=lambda r: (not r["platform"], r["project"]))
    default = stored.get("default") if isinstance(stored.get("default"), dict) else {}
    if default.get("provider") in eng.RUNTIME_OF:
        runtime = eng.RUNTIME_OF[default["provider"]]
        model, effort = eng.builtin_choice(runtime)
        eff = {"provider": default["provider"], "model": default.get("model") or model,
               "effort": default.get("effort") or effort, "source": "own"}
        tiers = default.get("tiers") if isinstance(default.get("tiers"), dict) else {}
        eff["tiers"] = _effective_tiers(tiers, eff["model"])
    else:
        runtime = eng.automatic_runtime()
        model, effort = providers.compatible_defaults(
            eng.PROVIDER_OF.get(runtime), *eng.builtin_choice(runtime))
        eff = {"provider": eng.PROVIDER_OF.get(runtime), "model": model,
               "effort": effort, "source": "automatic",
               "tiers": _effective_tiers({}, model)}
    return {"engineers": rows,
            "default": {"own": (_choice_row(default.get("provider"), default.get("model"),
                                            default.get("effort"), default.get("tiers"))
                                if default.get("provider") else None),
                        "effective": eff,
                        "providers": list(ENGINEER_PROVIDERS),
                        "note": "Every project engineer without its own setting. "
                                "Unset, each uses its project record or a connected provider."},
            "applies": "From the next job; a job already running finishes on what "
                       "it started with."}


def engineer(name: str, running: dict | None = None) -> dict:
    """``GET /v1/engineers/<project or default>``: one row of ``engineers()``."""
    view = engineers(running)
    if name == "default":
        return {"project": "default", **view["default"], "applies": view["applies"]}
    import loam_engineer as eng  # noqa: PLC0415
    if name == eng.APP_LANE and eng.app_lane() is not None:
        # The old name of the app's engineer reads the engineer it now is.
        name = "Loam"
    row = next((r for r in view["engineers"] if r["project"] == name), None)
    if row is None:
        raise ProfileError(f"no engineer for a project named {name!r}; GET "
                           f"/v1/engineers lists them", 404)
    return {**row, "applies": view["applies"]}


def update_engineer(name: str, patch: dict, check) -> dict:
    """Apply a partial engineer settings update. A null provider restores inheritance. Null
    model or effort restores the provider default. Patch only named tiers, with null
    clearing that override. Return the updated engineers view.
    """
    import loam_appearance  # noqa: PLC0415
    import loam_autodeploy  # noqa: PLC0415
    import loam_engineer as eng  # noqa: PLC0415
    if not isinstance(patch, dict):
        raise ProfileError("the body is an object")
    fields = set(patch) - {"by"}
    unknown = sorted(fields - {"provider", "model", "effort", "tiers", "deploy_on_land"})
    if unknown:
        raise ProfileError(f"an engineer has no setting {', '.join(unknown)}; it "
                           f"takes provider, model, effort, tiers and deploy_on_land")
    if not fields:
        raise ProfileError("name a setting to change: provider, model, effort, "
                           "tiers, deploy_on_land")
    if "tiers" in patch:
        tiers_patch = patch["tiers"]
        if not isinstance(tiers_patch, dict) or not tiers_patch:
            raise ProfileError("`tiers` is an object of quick, standard and/or "
                               "hard, each a model its provider lists or null, "
                               'like {"hard": "<model>"}')
        bad = sorted(set(tiers_patch) - set(eng.TIERS))
        if bad:
            raise ProfileError(f"there is no tier {', '.join(bad)}; the tiers are "
                               f"quick, standard and hard")
    is_default = name == "default"
    lane = eng.app_lane()
    if lane is not None and name == lane.name:
        # The Loam engineer's app-only scope (2026-11-19) has no setting of
        # its own: its jobs run on Loam's provider and tiers, and its deploy
        # toggle is Loam's. A provider for it alone would be a second
        # engineer again, so that is refused with where it is set now; the
        # toggle, which only ever meant app jobs, is Loam's.
        if fields - {"deploy_on_land"}:
            raise ProfileError(
                f"{name} is the Loam engineer's app-only scope since 2026-11-19, "
                f"and runs on the Loam engineer's provider, model and tiers: set "
                f"them on Loam (they apply to every Loam job, app-only or not)", 409)
        name = "Loam"
    project = None if is_default else eng.engineers().get(name)
    if not is_default and project is None:
        raise ProfileError(f"no engineer for a project named {name!r}; GET "
                           f"/v1/engineers lists them", 404)
    allowed = ENGINEER_PROVIDERS
    if "deploy_on_land" in patch:
        if not isinstance(patch["deploy_on_land"], bool):
            raise ProfileError("`deploy_on_land` is true or false")
        if is_default or (project.platform and lane is None):
            raise ProfileError("deploy_on_land is a project's own: "
                               + ("the default has none" if is_default else
                                  "Loam's engineer deploys by running bin/deploy itself"))
        if project.pull_request:
            raise ProfileError(f"{name} lands by pull request, so a landing does not "
                               f"put a job on main; there is nothing to deploy then")
        app, why = loam_autodeploy.app_for(lane if project.platform else project)
        if not app and patch["deploy_on_land"]:
            raise ProfileError(why or f"{name} has no app to deploy", 409)

    def change(current):
        data = dict(current) if isinstance(current, dict) else {}
        projects = dict(data.get("projects") or {})
        row = dict((data.get("default") if is_default else projects.get(name)) or {})
        if "provider" in patch:
            p = patch["provider"]
            if p is None:
                for k in ("provider", "model", "effort", "tiers"):
                    row.pop(k, None)
            elif p not in allowed:
                raise ProfileError(
                    f"`provider` for an engineer is {' or '.join(allowed)}, or null")
            else:
                if row.get("provider") != p:
                    # A provider's models are its own: its tiers go too.
                    for k in ("model", "effort", "tiers"):
                        if k not in patch:
                            row.pop(k, None)
                row["provider"] = p
        setting_choice = (patch.get("model") or patch.get("effort")
                          or any((patch.get("tiers") or {}).values()))
        if setting_choice and not row.get("provider"):
            # Store the provider a deliberate model or effort belongs to.
            if is_default:
                runtime = eng.automatic_runtime()
            else:
                inherited = {**data, "projects": {k: v for k, v in projects.items() if k != name}}
                runtime = eng.engineer_choice(project, inherited)["runtime"]
            if runtime is None:
                raise ProfileError(providers.SETUP, 409)
            row["provider"] = eng.PROVIDER_OF[runtime]
        for field in ("model", "effort"):
            if field not in patch:
                continue
            if patch[field] is None:
                row.pop(field, None)
            elif field == "effort" and row["provider"] == "antigravity":
                # One mapping (lib/loam_antigravity.py, LEVEL_RE): the
                # model id carries the level, so there is no second knob
                # to disagree with it.
                raise ProfileError(
                    "an Antigravity engineer's thinking level is part of its "
                    "model id (gemini-4-hydrogen-high, -xhigh, ...): set "
                    "`model` to the id with the level you want")
            else:
                row[field] = check(row["provider"], field, patch[field])
        if "tiers" in patch:
            tiers = dict(row["tiers"]) if isinstance(row.get("tiers"), dict) else {}
            for tier, value in patch["tiers"].items():
                if value is None or value == "":
                    tiers.pop(tier, None)
                else:
                    # The same check as `model`: a model this provider lists.
                    tiers[tier] = check(row["provider"], "model", value)
            if tiers:
                row["tiers"] = {t: tiers[t] for t in eng.TIERS if t in tiers}
            else:
                row.pop("tiers", None)
        if "deploy_on_land" in patch:
            if patch["deploy_on_land"]:
                row["deploy_on_land"] = True
            elif project is not None and project.platform:
                # Said, not absent: absent reads the app's old row
                # (lib/loam_engineer.py, `deploy_default`).
                row["deploy_on_land"] = False
            else:
                row.pop("deploy_on_land", None)
        if is_default:
            if row:
                data["default"] = row
            else:
                data.pop("default", None)
        else:
            if row:
                projects[name] = row
            else:
                projects.pop(name, None)
            if projects:
                data["projects"] = projects
            else:
                data.pop("projects", None)
        return data or None

    loam_appearance.mutate("engineers", change)
    return engineers()


# ---------------------------------------------------------------------------
# The reviewer
# ---------------------------------------------------------------------------

SYSTEMD = None   # tests put a fake here, as lib/loam_activity.py's


def _systemd():
    import loam_schedule  # noqa: PLC0415
    return SYSTEMD or loam_schedule.Systemd()


def dropin_dir() -> Path:
    """Locate the legacy static review timer's drop-in directory for removal when applying
    the plugin schedule.
    """
    import loam_cron  # noqa: PLC0415
    return loam_cron.UNIT_DIR / f"{LEGACY_REVIEWER_TIMER}.d"


def reviewer_on() -> bool:
    import loam_plugins  # noqa: PLC0415
    row = next((r for r in loam_plugins._rows()
                if r["id"] == REVIEWER_PLUGIN and r["effective"]), None)
    return bool(row and row.get("on"))


def dropin_text(spec: str, words: str) -> str:
    return ("# Written by the broker from Settings > Agents > System > Nightly\n"
            "# reviewer (lib/loam_profile.py). The schedule's home is\n"
            "# state/broker/settings.json, key `reviewer`; check-drift holds this\n"
            f"# file to it. {words}\n"
            "[Timer]\nOnCalendar=\n"
            f"OnCalendar={spec}\n")


def reviewer_schedule() -> str:
    """Read the plugin schedule, falling back to the legacy reviewer.schedule setting until
    migration and then to the default.
    """
    import loam_contributions  # noqa: PLC0415
    value = loam_contributions.setting_values(REVIEWER_PLUGIN).get("schedule")
    if not (isinstance(value, str) and value.strip()):
        value = reviewer_stored().get("schedule")
    return value if isinstance(value, str) and value.strip() else REVIEWER_DEFAULT_SCHEDULE


def expected_dropin() -> str | None:
    """None: the legacy timer's drop-in is retired with that timer (L9)."""
    return None


def reviewer(persona_fallback: str = "fable") -> dict:
    stored = reviewer_stored()
    provider = reviewer_provider()
    model, effort = reviewer_choice(persona_fallback, provider=provider)
    choices = providers.agent_choice(stored, provider)
    words = reviewer_schedule()
    try:
        timer = _systemd().timers([REVIEWER_TIMER])[REVIEWER_TIMER]
    except Exception:  # noqa: BLE001 -- a manager that cannot be asked is no times
        timer = {"next": None, "last": None, "running_since": None}
    return {
        "id": "reviewer", "name": "Nightly reviewer",
        "description": "Reads one agent's workspace a night as someone who did "
                       "not write it, and leaves one note.",
        # On or off is the reviewer plugin's switch (Settings › Plugins).
        "plugin": REVIEWER_PLUGIN, "on": reviewer_on(),
        "schedule": words, "plain": plain(words),
        "default_schedule": REVIEWER_DEFAULT_SCHEDULE,
        "next_fire": timer.get("next"), "last_fire": timer.get("last"),
        "running_since": timer.get("running_since"),
        "provider": stored.get("provider"),
        "providers": list(REVIEWER_PROVIDERS),
        "model": choices["model"], "effort": choices["effort"],
        "effective": {"provider": provider, "model": model, "effort": effort},
        "instructions": {"root": "system", "path": "Reviewer/CLAUDE.md"},
        "applies": "From the next run.",
    }


def apply_reviewer_schedule() -> None:
    """The schedule is the plugin's setting, and its timer follows it
    (lib/loam_contributions.py, ``set_setting``). What is left here is the
    legacy drop-in, removed if it stands, then a reload."""
    folder = dropin_dir()
    path = folder / REVIEWER_DROPIN
    if not path.is_file():
        return
    path.unlink(missing_ok=True)
    try:
        folder.rmdir()
    except OSError:
        pass
    _systemd().reload()


def update_reviewer(patch: dict, check) -> dict:
    """Update the reviewer and return its settings. Changing provider clears incompatible
    model and effort unless supplied together. Antigravity encodes effort in the model
    ID. A null field restores its default.
    """
    import loam_appearance  # noqa: PLC0415
    import loam_cron  # noqa: PLC0415
    if not isinstance(patch, dict):
        raise ProfileError("the body is an object")
    fields = set(patch) - {"by"}
    unknown = sorted(fields - {"schedule", "provider", "model", "effort"})
    if unknown:
        raise ProfileError(f"the reviewer has no setting {', '.join(unknown)}; it "
                           f"takes schedule, provider, model and effort")
    if not fields:
        raise ProfileError("name a setting to change: schedule, provider, model, effort")
    if "provider" in patch and patch["provider"] not in (None, *REVIEWER_PROVIDERS):
        raise ProfileError(f"the reviewer's `provider` is "
                           f"{' or '.join(REVIEWER_PROVIDERS)}, or null to use a connected provider")
    stored = reviewer_stored()
    stored_provider = stored.get("provider")
    provider = patch.get("provider", stored_provider)
    clean: dict = {}
    if "provider" in patch:
        clean["provider"] = provider
        if provider != stored_provider or provider is None:
            for k in ("model", "effort"):
                if k not in patch:
                    clean[k] = None
    if "schedule" in patch:
        if patch["schedule"] is None:
            clean["schedule"] = None
        else:
            if not isinstance(patch["schedule"], str):
                raise ProfileError(f'`schedule` is words, like "daily at 04:20 {lc.ZONE_TOKEN}"')
            try:
                words, spec = loam_cron.parse_schedule(patch["schedule"])
                loam_cron.assert_systemd_parses(spec)
            except loam_cron.CronError as exc:
                raise ProfileError(str(exc)) from None
            clean["schedule"] = None if words == REVIEWER_DEFAULT_SCHEDULE else words
    for field in ("model", "effort"):
        if field in patch:
            if patch[field] is not None and provider is None:
                provider = providers.automatic(REVIEWER_PROVIDERS)
                if provider is None:
                    raise ProfileError(providers.SETUP, 409)
                clean["provider"] = provider
                if provider != "claude-code":
                    for k in ("model", "effort"):
                        clean.setdefault(k, None)
            if patch[field] is not None and field == "effort" and provider == "antigravity":
                raise ProfileError(
                    "on Antigravity the reviewer's thinking level is part of its "
                    "model id (gemini-4-hydrogen-high, claude-opus-5-5-high, ...): "
                    "set `model` to the id with the level you want")
            clean[field] = (None if patch[field] is None
                            else check(provider, field, patch[field]))

    def change(current):
        row = dict(current) if isinstance(current, dict) else {}
        for k, v in clean.items():
            if v is None:
                row.pop(k, None)
            else:
                row[k] = v
        return row or None

    if "schedule" in clean:
        import loam_contributions  # noqa: PLC0415
        if not reviewer_on():
            raise ProfileError("the reviewer is off (its plugin, Settings › Plugins); "
                               "turn it on to give it a schedule", 409)
        try:
            loam_contributions.set_setting(REVIEWER_PLUGIN, "schedule",
                                           clean["schedule"] or REVIEWER_DEFAULT_SCHEDULE)
        except loam_contributions.ContributionError as exc:
            raise ProfileError(str(exc), 409) from None
        # Its home moved to the plugin; the host's copy goes.
        clean["schedule"] = None
    loam_appearance.mutate("reviewer", change)
    if "schedule" in patch:
        apply_reviewer_schedule()
    return reviewer()
