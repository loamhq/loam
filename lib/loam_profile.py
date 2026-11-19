"""An agent's profile, and the settings of the engineers and the reviewer.

Decided 2026-11-13 (the owner, on a persona's mockup at its agent-profile site:
"Looks good. Please proceed."; earlier that day: "build out ... the agent
profile and all the different settings for a specific agent ... customizing
and editing the engineers and other systems ... being able for me to be able
to set the provider for them"). This is the host's half; the page is
loam-ui's. The routes are in bin/session-broker; their contract is in
broker/README.md, "Agent profiles, engineers and the reviewer".

**Where each setting lives.**

- An agent's display name and default provider, model and thinking level
  are its ``agent.json`` (``display``, ``provider``, ``model``,
  ``effort``); its face is the theme's, or an uploaded image
  (lib/loam_agents.py). The emoji face is retired (the owner, 2026-11-16):
  an older file's ``emoji`` is kept through a provision, in its old place,
  and served by nothing. That file already held the name, and every reader of an agent's name or face already reads it
  (the page's cards, threads' lines and mentions, the schedule page). The
  id (the folder's name) never changes: it is the workspace, the units, the
  session records and the ``@`` an old message carries, so a rename is the
  display name only, and ``@`` mentions match both. bin/provision keeps
  these five keys when it renders agent.json again, and bin/check-drift
  compares the rest of the file. The persona's own words for itself
  ("You are Ada" in its CLAUDE.md, its style's name) stay on the id.
- The engineers' and the reviewer's are the broker's host settings file
  (``state/broker/settings.json``, keys ``engineers`` and ``reviewer``),
  beside Appearance's, written under the same lock
  (``loam_appearance.mutate``). Not ``projects/<name>.json`` or a unit
  file: those are tracked in this repository, and a page's write must not
  dirty the canonical checkout. The launchers read them on every run
  (lib/loam_engineer.py ``engineer_choice``; ``reviewer_choice`` here).
  The reviewer's schedule is applied as a systemd drop-in beside the
  tracked unit (``loam-reviewer.timer.d/schedule.conf``), and
  check-drift holds the drop-in to the setting.

**Where the defaults apply** (said by ``defaults()``, which the page shows):
an agent's provider is the engine a member starts on when it joins a
thread (new threads, a member brought in); its model and thinking level
are what a turn runs on whenever the thread has not chosen a model for it
and the member is on that provider, existing threads included. A wake and
a cron run on the persona's provider too (2026-11-13, bin/wake): Claude Code
or Codex, on the persona's model and thinking level. Antigravity's lanes are
not built (it has never run a turn on this box: no sign-in, so its event
shapes are unmeasured), so an Antigravity persona's wakes run on Claude Code
on the wake's own model, and the run's log says so
(``provider_not_honoured``).
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path

import loam_config as lc

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

def lane_provider(config: dict) -> str:
    """The provider a wake or a cron of this persona runs on: its own when a
    lane can run there (``LANE_PROVIDERS``), else Claude Code."""
    provider = config.get("provider") or "claude-code"
    return provider if provider in LANE_PROVIDERS else "claude-code"


def lane_choice(config: dict, wake_model: str, pinned: bool = False
                ) -> tuple[str | None, str | None, str | None]:
    """(model, effort, note) a wake or a cron of this persona runs on.

    Its own model and thinking level on its own provider when a lane runs
    there (Claude Code, and Codex since 2026-11-13). On Claude Code an unset
    model is the wake's (``wake_model``); on Codex it is None, Codex's own
    default, as a Codex conversation with no model chosen runs on. On a
    provider no lane runs on (Antigravity) it is the wake's model on Claude
    Code, with a sentence saying why. ``pinned`` (the environment named a
    model, for a probe) keeps ``wake_model`` on Claude Code."""
    provider = config.get("provider") or "claude-code"
    effort = config.get("effort") if isinstance(config.get("effort"), str) else None
    model = config.get("model") if isinstance(config.get("model"), str) else None
    if provider == "codex":
        return (model or None), (effort or None), None
    if provider != "claude-code":
        return (wake_model, None,
                f"this persona's provider is {PROVIDER_NAMES.get(provider, provider)}; "
                f"wakes and crons run on Claude Code ({wake_model}), because "
                f"Antigravity has never run a turn on this box (no sign-in), so "
                f"a lane on it could not be built against what it really sends")
    return (wake_model if pinned or not model else model), (effort or None), None


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


def reviewer_provider() -> str:
    """The provider the nightly reviewer runs on: the owner's setting, else
    Claude Code. ``LOAM_REVIEWER_PROVIDER`` names one for a probe or a
    one-off run, as ``LOAM_REVIEWER_MODEL`` names a model."""
    pinned = os.environ.get("LOAM_REVIEWER_PROVIDER")
    if pinned in REVIEWER_PROVIDERS:
        return pinned
    value = reviewer_stored().get("provider")
    return value if value in REVIEWER_PROVIDERS else "claude-code"


def reviewer_choice(model: str, pinned: bool = False,
                    provider: str | None = None) -> tuple[str, str | None]:
    """(model, effort) the nightly reviewer runs on: the owner's setting over
    ``model`` (bin/review's), unless the environment pinned one. On
    Antigravity the fallback is ``REVIEWER_AGY_MODEL`` and there is no
    effort; a model stored for one provider is never run on the other."""
    provider = provider or reviewer_provider()
    stored = reviewer_stored()
    same = (stored.get("provider") or "claude-code") == provider
    chosen = stored.get("model") if same and isinstance(stored.get("model"), str) else None
    effort = stored.get("effort") if same and isinstance(stored.get("effort"), str) else None
    if provider == "antigravity":
        return (model if pinned else chosen or REVIEWER_AGY_MODEL), None
    return (model if pinned or not chosen else chosen), effort


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
    ``effort``; null puts a field back (the id's name, Claude Code, the provider's own model and level). A provider that
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
        before = config.get("provider") or "claude-code"
        if "provider" in patch:
            p = patch["provider"]
            if p is not None and p not in PROVIDERS:
                raise ProfileError(f"`provider` is one of {', '.join(PROVIDERS)}, "
                                   f"or null for Claude Code")
            if p in (None, "claude-code"):
                new.pop("provider", None)
            else:
                new["provider"] = p
            if (p or "claude-code") != before:
                for k in ("model", "effort"):
                    if k not in patch:
                        new.pop(k, None)
        provider = new.get("provider") or "claude-code"
        for field in ("model", "effort"):
            if field not in patch:
                continue
            if patch[field] is None:
                new.pop(field, None)
            else:
                new[field] = check(provider, field, patch[field])
        if new != config:
            text = render_agent(new)
            target = agent_path(agent)
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, target)
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
    (null where not), as each lane will use them, and what is not honoured."""
    provider = config.get("provider") if config.get("provider") in PROVIDERS else None
    model = config.get("model") if isinstance(config.get("model"), str) else None
    effort = config.get("effort") if isinstance(config.get("effort"), str) else None
    eff_provider = provider or "claude-code"
    own_model = None
    if eff_provider == "claude-code" and persona_model is not None:
        own_model = persona_model(agent)
    lane_model, lane_effort, note = lane_choice(config, wake_model)
    not_honoured = []
    if note:
        not_honoured.append(f"Wakes and crons: {note}.")
    return {
        "provider": provider, "model": model, "effort": effort,
        "effective": {"provider": eff_provider,
                      "model": model or own_model,
                      "effort": effort},
        "threads": {"provider": eff_provider, "model": model or own_model,
                    "effort": effort},
        "wakes": {"provider": lane_provider(config), "model": lane_model,
                  "effort": lane_effort},
        "applies": ("New threads and members brought in start on the provider. "
                    "The model and thinking level apply to every turn on that "
                    "provider for which the thread has not chosen a model, from "
                    "the next turn; a thread's own choice still wins."),
        "not_honoured": not_honoured,
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
    """An engineer's own setting as the page reads it. ``tiers`` (since
    2026-11-15) is always the three keys, null where unset."""
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
    for name, project in eng.projects().items():
        own = own_rows.get(name) if isinstance(own_rows.get(name), dict) else {}
        choice = eng.engineer_choice(project, stored)
        app = None
        if not project.platform and not project.pull_request:
            app, _ = loam_autodeploy.app_for(project)
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
            "effective": {"provider": eng.PROVIDER_OF[choice["runtime"]],
                          "model": choice["model"], "effort": choice["effort"],
                          "tiers": _effective_tiers(eng.tier_models(project, stored),
                                                    choice["model"]),
                          "source": choice["source"]},
            # Loam's own too since 2026-11-19: "It should be like the others."
            "providers": list(ENGINEER_PROVIDERS),
            "deploy_on_land": (own.get("deploy_on_land") is True) if app else None,
            "deploy_app": app,
            "lands_by": "pull_request" if project.pull_request else "main",
            "instructions": instructions,
            "jobs_running": (running or {}).get(name, 0),
        })
    rows.sort(key=lambda r: (not r["platform"], r["project"]))
    default = stored.get("default") if isinstance(stored.get("default"), dict) else {}
    builtin_model, builtin_effort = eng.builtin_choice(eng.DEFAULT_RUNTIME)
    if default.get("provider"):
        runtime = eng.RUNTIME_OF[default["provider"]]
        model, effort = eng.builtin_choice(runtime)
        eff = {"provider": default["provider"], "model": default.get("model") or model,
               "effort": default.get("effort") or effort, "source": "own"}
        tiers = default.get("tiers") if isinstance(default.get("tiers"), dict) else {}
        eff["tiers"] = _effective_tiers(tiers, eff["model"])
    else:
        eff = {"provider": eng.PROVIDER_OF[eng.DEFAULT_RUNTIME], "model": builtin_model,
               "effort": builtin_effort, "source": "record",
               "tiers": _effective_tiers({}, builtin_model)}
    return {"engineers": rows,
            "default": {"own": (_choice_row(default.get("provider"), default.get("model"),
                                            default.get("effort"), default.get("tiers"))
                                if default.get("provider") else None),
                        "effective": eff,
                        "providers": list(ENGINEER_PROVIDERS),
                        "note": "Every project engineer without its own setting. "
                                "Unset, each runs on its record's runtime (a new "
                                "project's is Codex) and that CLI's built-in model."},
            "applies": "From the next job; a job already running finishes on what "
                       "it started with."}


def engineer(name: str, running: dict | None = None) -> dict:
    """``GET /v1/engineers/<project or default>``: one row of ``engineers()``."""
    view = engineers(running)
    if name == "default":
        return {"project": "default", **view["default"], "applies": view["applies"]}
    row = next((r for r in view["engineers"] if r["project"] == name), None)
    if row is None:
        raise ProfileError(f"no engineer for a project named {name!r}; GET "
                           f"/v1/engineers lists them", 404)
    return {**row, "applies": view["applies"]}


def update_engineer(name: str, patch: dict, check) -> dict:
    """One engineer's setting, or ``default``'s. ``provider`` null clears the
    engineer's own setting (it follows the default again); ``model`` and
    ``effort`` null go back to the provider's built-in. ``tiers`` (since
    2026-11-15) is an object of any of ``quick``, ``standard``, ``hard``,
    each a model the provider lists or null to unset that one (it then
    runs on ``model``); the tiers not named are kept. ``deploy_on_land``
    is a project with an app's own. Answers ``engineers()``."""
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
    project = None if is_default else eng.projects().get(name)
    if not is_default and project is None:
        raise ProfileError(f"no engineer for a project named {name!r}; GET "
                           f"/v1/engineers lists them", 404)
    allowed = ENGINEER_PROVIDERS
    if "deploy_on_land" in patch:
        if not isinstance(patch["deploy_on_land"], bool):
            raise ProfileError("`deploy_on_land` is true or false")
        if is_default or project.platform:
            raise ProfileError("deploy_on_land is a project's own: "
                               + ("the default has none" if is_default else
                                  "Loam's engineer deploys by running bin/deploy itself"))
        if project.pull_request:
            raise ProfileError(f"{name} lands by pull request, so a landing does not "
                               f"put a job on main; there is nothing to deploy then")
        app, why = loam_autodeploy.app_for(project)
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
        if ("model" in patch or "effort" in patch or "tiers" in patch) \
                and not row.get("provider"):
            # A model with no provider of its own: the provider it runs on now.
            if is_default:
                row["provider"] = eng.PROVIDER_OF[eng.DEFAULT_RUNTIME]
            else:
                row["provider"] = eng.PROVIDER_OF[eng.engineer_choice(project)["runtime"]]
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
    """The legacy static timer's drop-in folder (until 2026-11-14), removed by
    ``apply_reviewer_schedule``."""
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
    """The plugin's `schedule` setting; before 2026-11-14 the host settings'
    `reviewer.schedule`, read while nothing has been moved; else the default."""
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
        "provider": stored.get("provider") or "claude-code",
        "providers": list(REVIEWER_PROVIDERS),
        "model": stored.get("model"), "effort": stored.get("effort"),
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
    """The reviewer's ``schedule`` (the schedules' grammar, ``daily at 04:20
    PT``), ``provider`` (Claude Code or, since 2026-11-15, Antigravity),
    ``model`` and ``effort``. A change of provider drops the other
    provider's model and effort unless the same patch names them. On
    Antigravity the model id carries the level, so ``effort`` is refused.
    Null puts each back. Answers ``reviewer()``."""
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
                           f"{' or '.join(REVIEWER_PROVIDERS)}, or null for claude-code")
    stored_provider = reviewer_stored().get("provider") or "claude-code"
    provider = (patch.get("provider") or "claude-code") if "provider" in patch \
        else stored_provider
    clean: dict = {}
    if "provider" in patch:
        clean["provider"] = None if provider == "claude-code" else provider
        if provider != stored_provider:
            # A provider's models are its own (as an engineer's).
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
