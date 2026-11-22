"""Read enabled plugins’ host contributions from their manifests.

A plugin says what it adds under `contributes:` in its manifest, one list per
place. Every entry is in force exactly while the plugin is on (in force, its
switch on); turning it off takes every entry away. The places:

| place | an entry | where it lands |
|---|---|---|
| `agent.tool` | `{name, run, lanes?, agents?}`: a stdio MCP server, `run` by `/bin/sh -c` in the plugin's folder | the lanes' MCP servers |
| `agent.prompt` | `{text \| file, lanes?, agents?}`: what the plugin lets an agent do, in words | the lanes' instructions |
| `schedule.kind` | `{name, schedule, run}`: a run on Loam's schedule grammar (`daily at 05:00 PT`) | a timer, `loam-plugin-<id>-<name>` |
| `settings.entry` | `{key, label, choices?, default?, labels?, note?, advanced?, setup?}`: a value an agent may read and set | the settings registry, `plugin.<id>.<key>` |
| `engineer.env` | `{name, value \| setting \| secret, projects?}` | an engineer job's environment |
| `engineer.job` | `{run, projects?, timeout?}`: a command run at each job's launch, in the plugin's folder; each `NAME=value` line it prints is the job's environment | an engineer job's environment, and files in its folder |
| `drift.check` | `{run, timeout?}`: exit 0 is "nothing wrong", else each line of its output is a finding | `check-drift` |
| `box.listener` | `{process, why}`: a process allowed to answer off loopback | `check-drift`'s listener check |
| `settings.secret` | `{name, label, check?}`: a credential the person pastes (an API key), checked on entry | the plugin's private `secrets.env`, through `PUT /v1/plugins/<id>/secrets/<name>` |
| `vault.folder` | `{path \| setting}`: a vault folder the plugin uses | made when the plugin is on (never removed) |

A `schedule.kind` entry may also name `setting:`, one of its own
`settings.entry` keys: the timer then follows that value when it is a valid
schedule, and `schedule` is its default.

**Lanes** are `persona` (a persona's conversation, wakes and crons: they
share one workspace and so one set of tools and instructions), `engineer`
and `reviewer`. The default is `[persona]`. `agents:` narrows a persona entry
to named personas. Why not one lane per persona lane: a conversation and a
wake read the same `.mcp.json` and `CLAUDE.md`, and splitting them would
need an appended system prompt on the conversation lane, which the broker's
design deliberately has none of (broker/README.md, "What a session is").

**When an entry lands.** On the next run of each lane it targets. A live
conversation keeps the tools it started with; it is never restarted for a
switch. The persona's assembled files are re-rendered by `bin/provision` on
every switch (`apply()`); the engineer's and the reviewer's committed files
are not touched, and their launchers pass the plugin servers and words at
launch from `lane_mcp()` and `lane_prompt()`.

**A plugin's own routes** are its server's, under `/x/<id>/` through the
gateway (docs/plugins.md, "Server plugins"). There is deliberately no seam
that adds a route to the broker: the broker is the core's, and a plugin
reaches it the way the page does.

**Timers obey the rule in docs/config.md:** a standing timer is never stopped
and started; a changed one is moved by `daemon-reload`; a new one is armed
fresh (loam_units.arm_timer). The service runs `bin/plugin-run`, which holds
the run to the timer's own calendar (lib/loam_due.py) and to the plugin
still being on.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
from contextlib import contextmanager, nullcontext
from pathlib import Path

import loam_config as lc

PLACES = ("agent.tool", "agent.prompt", "schedule.kind", "settings.entry",
          "engineer.env", "drift.check", "box.listener",
          # First release L9 (2026-11-14): a key the person pastes, checked on
          # entry; a vault folder the plugin uses.
          "settings.secret", "vault.folder", "thread.file",
          # First release L6 (2026-11-14): a command run at a job's launch
          # whose output is that job's environment: what carries a project's
          # cloud credentials, written fresh for each job, which a fixed
          # value cannot.
          "engineer.job",
          # 2026-11-16 (doc-comments' share links): the one path, /s/, that
          # reaches a plugin without the owner's login (docs/plugins.md, "The
          # share door").
          "web.door",
          # 2026-11-22: a named loopback HTTP service a server plugin offers
          # to other enabled plugins via GET /v1/plugins/services (docs/plugins.md).
          "plugin.service")
# The checks the host runs itself for a `settings.secret` (`check: builtin:<n>`),
# so a plugin needs no code of its own to have its key checked:
# lib/loam_keys.py.
BUILTIN_CHECKS = ("gemini",)
SECRET_MAX = 4096
LANES = ("persona", "engineer", "reviewer")
NAME = re.compile(r"^[a-z][a-z0-9-]{0,30}$")
INTERFACE = re.compile(r"^[a-z][a-z0-9-]{0,30}(\.[a-z][a-z0-9-]{0,30})*$")
MOUNT = re.compile(r"^/[A-Za-z0-9_/-]{1,64}$")
VAR = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
KEY = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")
PROMPT_MAX = 2000
DRIFT_TIMEOUT_S = 60
JOB_HOOK_TIMEOUT_S = 30
UNIT_PREFIX = "loam-plugin-"


class ContributionError(Exception):
    pass


# ---------------------------------------------------------------------------
# Reading a manifest's `contributes:`
# ---------------------------------------------------------------------------

def _lanes(entry: dict, where: str, default=("persona",)) -> list[str]:
    lanes = entry.get("lanes", list(default))
    if isinstance(lanes, str):
        lanes = [lanes]
    if not isinstance(lanes, list) or not lanes or any(l not in LANES for l in lanes):
        raise ContributionError(f"{where}: `lanes` is a list of {', '.join(LANES)}")
    return sorted(set(lanes), key=LANES.index)


def _agents(entry: dict, where: str) -> list[str] | None:
    agents = entry.get("agents")
    if agents is None:
        return None
    if isinstance(agents, str):
        agents = [agents]
    if not isinstance(agents, list) or not all(isinstance(a, str) and NAME.match(a)
                                               for a in agents):
        raise ContributionError(f"{where}: `agents` is a list of persona names")
    return agents


def _run(entry: dict, where: str) -> str:
    run = entry.get("run")
    if not isinstance(run, str) or not run.strip() or "\n" in run:
        raise ContributionError(f"{where}: `run` is one line, run by /bin/sh -c "
                                f"in the plugin's folder")
    return run.strip()


def _entries(value, place: str) -> list[dict]:
    if value is None:
        return []
    if isinstance(value, dict) and place == "drift.check":
        value = [value]
    if not isinstance(value, list) or not all(isinstance(e, dict) for e in value):
        raise ContributionError(f"contributes.{place} is a list of entries")
    return value


def _secret_names(entry: dict, where: str) -> list[str]:
    names = entry.get("secrets", [])
    if not isinstance(names, list) or not all(isinstance(n, str) and VAR.match(n)
                                              for n in names):
        raise ContributionError(f"{where}: `secrets` is a list of names, like "
                                f"SERVICE_API_KEY")
    return names


def contributes_of(value, folder: Path | None, warnings: list[str]) -> dict:
    """The manifest's `contributes:` checked and normalised; a
    ContributionError for anything that makes it unusable. An unknown place
    is a warning, kept out."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ContributionError("`contributes` maps a place to its entries")
    out: dict = {}
    for place, raw in value.items():
        if place not in PLACES:
            warnings.append(f"contributes: {place!r} is not a place the host "
                            f"reads ({', '.join(PLACES)}); left out")
            continue
        items = []
        names: set[str] = set()
        for i, e in enumerate(_entries(raw, place)):
            where = f"contributes.{place}[{i}]"
            if place in ("agent.tool", "schedule.kind"):
                name = e.get("name")
                if not isinstance(name, str) or not NAME.match(name):
                    raise ContributionError(f"{where}: `name` is lower-case "
                                            f"letters, digits and dashes")
                if name in names:
                    raise ContributionError(f"{where}: two entries named {name!r}")
                names.add(name)
            if place == "agent.tool":
                items.append({"name": e["name"], "run": _run(e, where),
                              "lanes": _lanes(e, where),
                              "agents": _agents(e, where),
                              "secrets": _secret_names(e, where)})
            elif place == "thread.file":
                if items:
                    raise ContributionError(f"{where}: one file per thread per plugin")
                label = e.get("label")
                suffix = e.get("suffix", "")
                initial = e.get("initial", "# {title}")
                if not isinstance(label, str) or not re.fullmatch(r"[\w -]{1,60}", label):
                    raise ContributionError(f"{where}: label needs a short file name")
                if not isinstance(suffix, str) or not re.fullmatch(r"[\w –—-]{0,40}", suffix):
                    raise ContributionError(f"{where}: suffix needs plain filename characters")
                if not isinstance(initial, str) or len(initial) > 160 or "\n" in initial:
                    raise ContributionError(f"{where}: initial needs one short heading")
                items.append({"label": label, "suffix": suffix, "initial": initial})
            elif place == "agent.prompt":
                text, file = e.get("text"), e.get("file")
                if (text is None) == (file is None):
                    raise ContributionError(f"{where}: `text` or `file`, one of them")
                if file is not None:
                    if not isinstance(file, str) or file.startswith("/") or ".." in file:
                        raise ContributionError(f"{where}: `file` is a path inside the plugin")
                    if folder is not None:
                        try:
                            text = (folder / file).read_text(encoding="utf-8")
                        except OSError:
                            raise ContributionError(f"{where}: {file} is not in the plugin")
                if text is not None:
                    if not isinstance(text, str) or not text.strip():
                        raise ContributionError(f"{where}: `text` is words")
                    text = text.strip()
                    if len(text) > PROMPT_MAX:
                        raise ContributionError(f"{where}: at most {PROMPT_MAX} "
                                                f"characters; it is {len(text)}")
                items.append({"text": text, "file": file, "lanes": _lanes(e, where),
                              "agents": _agents(e, where)})
            elif place == "schedule.kind":
                forms = [k for k in ("schedule", "calendar", "boot") if e.get(k)]
                if len(forms) != 1:
                    raise ContributionError(
                        f"{where}: one of `schedule` (Loam's grammar, `daily at "
                        f"05:00 PT`), `calendar` (a systemd OnCalendar line, for "
                        f"what the grammar cannot say, like `*:*:30`) or `boot: true`")
                timeout = e.get("timeout", 300)
                if not isinstance(timeout, int) or not 1 <= timeout <= 3600:
                    raise ContributionError(f"{where}: `timeout` is seconds, 1 to 3600")
                item = {"name": e["name"], "run": _run(e, where), "timeout": timeout}
                if e.get("setting") is not None:
                    if forms[0] != "schedule" or not (isinstance(e["setting"], str)
                                                      and KEY.match(e["setting"])):
                        raise ContributionError(
                            f"{where}: `setting` is one of its own settings.entry "
                            f"keys, beside a `schedule` that is its default")
                    item["setting"] = e["setting"]
                if forms[0] == "schedule":
                    import loam_cron  # noqa: PLC0415
                    try:
                        raw_s, spec = loam_cron.parse_schedule(e["schedule"])
                    except loam_cron.CronError as exc:
                        raise ContributionError(f"{where}: {exc}")
                    item.update(schedule=raw_s, calendar=spec)
                elif forms[0] == "calendar":
                    spec = e["calendar"]
                    if not isinstance(spec, str) or "\n" in spec or not spec.strip():
                        raise ContributionError(f"{where}: `calendar` is one OnCalendar line")
                    item.update(schedule=spec.strip(), calendar=spec.strip())
                else:
                    if e["boot"] is not True:
                        raise ContributionError(f"{where}: `boot` is true")
                    item.update(schedule="at each start of the user's manager",
                                calendar=None, boot=True)
                items.append(item)
            elif place == "settings.entry":
                key = e.get("key")
                if not isinstance(key, str) or not KEY.match(key):
                    raise ContributionError(f"{where}: `key` is a lower-case slug")
                label = e.get("label")
                if not isinstance(label, str) or not label.strip():
                    raise ContributionError(f"{where}: `label` says what it is")
                choices = e.get("choices")
                if choices is not None and (not isinstance(choices, list) or not choices
                                            or not all(isinstance(c, str) for c in choices)):
                    raise ContributionError(f"{where}: `choices` is a list of words")
                default = e.get("default")
                if choices and default is not None and default not in choices:
                    raise ContributionError(f"{where}: `default` is one of its choices")
                item = {"key": key, "label": label.strip(), "choices": choices,
                        "default": default}
                # How it reads for a person (2026-11-18, Images 1.1.0): a
                # label per choice, a sentence under it, and `advanced` to
                # keep it behind the page's Advanced. Older hosts ignore all
                # three, so a plugin that uses them installs on them too.
                labels = e.get("labels")
                if labels is not None:
                    if (not choices or not isinstance(labels, dict)
                            or not all(isinstance(k, str) and k in choices
                                       and isinstance(v, str) and v.strip()
                                       and len(v) <= 80 for k, v in labels.items())):
                        raise ContributionError(
                            f"{where}: `labels` maps some of its choices to words "
                            f"of at most 80 characters")
                    item["labels"] = {k: v.strip() for k, v in labels.items()}
                note = e.get("note")
                if note is not None:
                    if not isinstance(note, str) or not note.strip() or len(note) > 300:
                        raise ContributionError(f"{where}: `note` is a sentence of at "
                                                f"most 300 characters")
                    item["note"] = note.strip()
                advanced = e.get("advanced")
                if advanced is not None:
                    if not isinstance(advanced, bool):
                        raise ContributionError(f"{where}: `advanced` is true or false")
                    item["advanced"] = advanced
                # What setting it up means (2026-11-19): the plugin is set up
                # once one entry marked `setup` has a value (Git backup's
                # repositories). Its page leads with its controls from then
                # on. Older hosts drop the key, as they drop any they don't know.
                setup = e.get("setup")
                if setup is not None:
                    if not isinstance(setup, bool):
                        raise ContributionError(f"{where}: `setup` is true or false")
                    item["setup"] = setup
                items.append(item)
            elif place == "engineer.env":
                name = e.get("name")
                if not isinstance(name, str) or not VAR.match(name):
                    raise ContributionError(f"{where}: `name` is an environment "
                                            f"variable's name, like HELLO_MODE")
                sources = [k for k in ("value", "setting", "secret") if k in e]
                if len(sources) != 1:
                    raise ContributionError(f"{where}: one of `value`, `setting` "
                                            f"(a key of its own) or `secret` (a "
                                            f"name in the secrets file)")
                projects = e.get("projects")
                if projects is not None and not (isinstance(projects, list) and
                                                 all(isinstance(p, str) for p in projects)):
                    raise ContributionError(f"{where}: `projects` is a list of names")
                items.append({"name": name, sources[0]: str(e[sources[0]]),
                              "projects": projects})
            elif place == "engineer.job":
                timeout = e.get("timeout", JOB_HOOK_TIMEOUT_S)
                if not isinstance(timeout, int) or not 1 <= timeout <= 120:
                    raise ContributionError(f"{where}: `timeout` is seconds, 1 to 120")
                projects = e.get("projects")
                if projects is not None and not (isinstance(projects, list) and projects and
                                                 all(isinstance(p, str) for p in projects)):
                    raise ContributionError(f"{where}: `projects` is a list of names")
                items.append({"run": _run(e, where), "projects": projects,
                              "timeout": timeout})
            elif place == "drift.check":
                timeout = e.get("timeout", DRIFT_TIMEOUT_S)
                if not isinstance(timeout, int) or not 1 <= timeout <= 600:
                    raise ContributionError(f"{where}: `timeout` is seconds, 1 to 600")
                items.append({"run": _run(e, where), "timeout": timeout})
            elif place == "settings.secret":
                name = e.get("name")
                if not isinstance(name, str) or not VAR.match(name):
                    raise ContributionError(f"{where}: `name` is the secret's name, "
                                            f"like SERVICE_API_KEY")
                if any(x["name"] == name for x in items):
                    raise ContributionError(f"{where}: two secrets named {name}")
                label = e.get("label")
                if not isinstance(label, str) or not label.strip():
                    raise ContributionError(f"{where}: `label` says what it is, for "
                                            f"the person pasting it")
                check = e.get("check")
                if check is not None:
                    if not isinstance(check, str) or not check.strip() or "\n" in check:
                        raise ContributionError(f"{where}: `check` is one line run in "
                                                f"the plugin's folder, or builtin:<name>")
                    if check.startswith("builtin:") and check[8:] not in BUILTIN_CHECKS:
                        raise ContributionError(f"{where}: the host's checks are "
                                                + ", ".join(f"builtin:{c}" for c in BUILTIN_CHECKS))
                items.append({"name": name, "label": label.strip(),
                              "check": check.strip() if check else None})
            elif place == "vault.folder":
                path, setting = e.get("path"), e.get("setting")
                if (path is None) == (setting is None):
                    raise ContributionError(f"{where}: `path` or `setting`, one of them")
                if path is not None and not _vault_rel(path):
                    raise ContributionError(f"{where}: `path` is a folder inside the vault")
                if setting is not None and not (isinstance(setting, str) and KEY.match(setting)):
                    raise ContributionError(f"{where}: `setting` is one of its own "
                                            f"settings.entry keys")
                item = {"path": path, "setting": setting}
                when = e.get("when")
                if when is not None:
                    if (not isinstance(when, dict)
                            or not (isinstance(when.get("setting"), str) and KEY.match(when["setting"]))
                            or not (isinstance(when.get("in"), list) and when["in"]
                                    and all(isinstance(v, str) for v in when["in"]))):
                        raise ContributionError(
                            f"{where}: `when` is `{{setting: <key>, in: [<values>]}}`")
                    item["when"] = {"setting": when["setting"], "in": list(when["in"])}
                items.append(item)
            elif place == "web.door":
                why = e.get("why")
                if not isinstance(why, str) or not why.strip():
                    raise ContributionError(f"{where}: `why` says who comes in through "
                                            f"the door and what they reach")
                if items:
                    raise ContributionError(f"{where}: a plugin has one door")
                items.append({"why": why.strip()})
            elif place == "plugin.service":
                iface = e.get("interface")
                if not isinstance(iface, str) or not INTERFACE.match(iface) or len(iface) > 64:
                    raise ContributionError(f"{where}: `interface` is a dotted lower-case name "
                                            f"(e.g. share.collaboration)")
                ver = e.get("version", 1)
                if not isinstance(ver, int) or isinstance(ver, bool) or not 1 <= ver <= 99:
                    raise ContributionError(f"{where}: `version` is an integer, 1 to 99")
                mount = e.get("mount")
                if not isinstance(mount, str) or not MOUNT.match(mount) or "//" in mount or ".." in mount:
                    raise ContributionError(f"{where}: `mount` is a route prefix starting with '/'")
                if any(x["interface"] == iface for x in items):
                    raise ContributionError(f"{where}: duplicate service interface {iface!r}")
                svc = {"interface": iface, "version": ver, "mount": mount.rstrip("/") or "/"}
                page = e.get("page")
                if page is not None:
                    if not isinstance(page, dict):
                        raise ContributionError(f"{where}: `page` is a mapping with `entry` and optional `styles`")
                    entry = page.get("entry")
                    if not isinstance(entry, str) or not _plugin_rel(entry) or not entry.endswith(".js"):
                        raise ContributionError(f"{where}: `page.entry` is a relative .js path inside the plugin")
                    styles = page.get("styles", [])
                    if not isinstance(styles, list) or not all(isinstance(s, str) and _plugin_rel(s) and s.endswith(".css") for s in styles):
                        raise ContributionError(f"{where}: `page.styles` is a list of relative .css paths inside the plugin")
                    svc["page"] = {"entry": entry, "styles": list(styles)}
                items.append(svc)
            elif place == "box.listener":
                proc = e.get("process")
                why = e.get("why")
                if not isinstance(proc, str) or not re.match(r"^[A-Za-z0-9._-]{1,32}$", proc):
                    raise ContributionError(f"{where}: `process` is the listening "
                                            f"program's name, as ss shows it")
                if not isinstance(why, str) or not why.strip():
                    raise ContributionError(f"{where}: `why` says what reaches it")
                items.append({"process": proc, "why": why.strip()})
        if items:
            out[place] = items
    return out


def _vault_rel(path) -> bool:
    """A folder path relative to the vault: no absolute path, no `..`, no
    control character, at most 200 characters."""
    if not isinstance(path, str) or not path.strip() or len(path) > 200:
        return False
    parts = path.strip().strip("/").split("/")
    return (not path.startswith("/") and all(p and p not in (".", "..") for p in parts)
            and not any(ord(c) < 32 or c in "\\" for c in path))


def _plugin_rel(path) -> bool:
    """A file path relative to a plugin's folder: no leading slash, no `..`,
    plain segments only."""
    if not isinstance(path, str) or not path or len(path) > 200 or path.startswith("/"):
        return False
    parts = path.split("/")
    return all(re.fullmatch(r"[A-Za-z0-9._-]+", p) and p not in (".", "..") for p in parts)


def declared(contrib: dict) -> dict:
    """What `contributes:` adds to the declaration shown before install, in
    the shape `added()` compares: a later version that adds a tool, a lane,
    a timer, a secret or a listener asks again."""
    out: dict = {}
    tools = contrib.get("agent.tool") or []
    prompts = contrib.get("agent.prompt") or []
    if tools or prompts:
        out["agents"] = {"tools": sorted(f"{t['name']} ({', '.join(t['lanes'])})" for t in tools),
                         "prompt": sorted({l for p in prompts for l in p["lanes"]})}
    if contrib.get("schedule.kind"):
        out["schedule"] = sorted(f"{s['name']}: {s['schedule']}" for s in contrib["schedule.kind"])
    env = contrib.get("engineer.env") or []
    if env:
        out["engineer_env"] = sorted(e["name"] for e in env)
    jobs = contrib.get("engineer.job") or []
    if jobs:
        out["engineer_job"] = sorted(
            f"{j['run']} ({', '.join(j['projects']) if j['projects'] else 'every project'})"
            for j in jobs)
    secrets = sorted({e["secret"] for e in env if "secret" in e}
                     | {n for t in tools for n in t.get("secrets") or []}
                     | {s["name"] for s in contrib.get("settings.secret") or []})
    if secrets:
        out["secrets"] = secrets
    if contrib.get("settings.entry"):
        out["settings"] = sorted(s["key"] for s in contrib["settings.entry"])
    if contrib.get("drift.check"):
        out["drift_check"] = True
    if contrib.get("vault.folder"):
        out["vault"] = sorted(f.get("path") or f"(its {f['setting']} setting)"
                              for f in contrib["vault.folder"])
    if contrib.get("thread.file"):
        out.setdefault("vault", []).append("Manages a Markdown file for each thread")
    if contrib.get("box.listener"):
        out["inbound"] = sorted(f"{l['process']}: {l['why']}" for l in contrib["box.listener"])
    if contrib.get("web.door"):
        out["open_web"] = contrib["web.door"][0]["why"]
    if contrib.get("plugin.service"):
        out["services"] = sorted(f"{s['interface']} (v{s['version']})"
                                 for s in contrib["plugin.service"])
    return out


def door_owner(plugins: list[dict] | None = None) -> str | None:
    """Return the first enabled plugin by ID that supplies web.door for /s/, or None."""
    for p in sorted(active() if plugins is None else plugins, key=lambda p: p["id"]):
        if p["contributes"].get("web.door"):
            return p["id"]
    return None


def services_for(interface: str | None = None, *, plugins: list[dict] | None = None,
                 ports: dict[str, int] | None = None) -> list[dict]:
    """Enabled plugins declaring ``contributes.plugin.service`` (optionally
    filtered by ``interface``), with each running plugin's loopback URL.

    Used by ``GET /v1/plugins/services`` so plugins discover and call each
    other over documented HTTP interfaces without importing each other's code
    or hardcoding ports or plugin ids."""
    if ports is None:
        import loam_plugin_servers as servers  # noqa: PLC0415
        ports = servers.read_ports()
    out = []
    for p in sorted(active() if plugins is None else plugins, key=lambda p: p["id"]):
        if p.get("on") is False or p.get("effective") is False:
            continue
        port = ports.get(p["id"])
        if not port:
            continue
        for svc in p["contributes"].get("plugin.service") or []:
            if interface and svc["interface"] != interface:
                continue
            row = {"plugin": p["id"], "interface": svc["interface"],
                   "version": svc["version"], "mount": svc["mount"],
                   "url": f"http://127.0.0.1:{port}{svc['mount']}"}
            if svc.get("page"):
                row["page"] = svc["page"]
            out.append(row)
    return out


# ---------------------------------------------------------------------------
# What is in force
# ---------------------------------------------------------------------------

def active() -> list[dict]:
    """Every plugin whose contributions are in force: in force, on, a plugin
    (not a theme, an agent or a skill). ``[{id, folder, contributes,
    settings, data}]``."""
    import loam_plugins  # noqa: PLC0415
    out = []
    for row in loam_plugins._rows():
        if not (row["effective"] and row.get("on") and row.get("switch") == "plugin"):
            continue
        contrib = row.get("contributes") or {}
        if contrib:
            out.append({"id": row["id"], "folder": Path(row["folder"]),
                        "contributes": contrib,
                        "settings": loam_plugins.yours_root() / "_settings" / row["id"],
                        "settings_file": Path(row.get("settings") or "settings.md").name,
                        "data": loam_plugins.yours_root() / "_data" / row["id"],
                        "has_server": bool((row.get("has") or {}).get("server")),
                        "keys": bool(row.get("keys"))})
    return out


def _targets(entry: dict, lane: str, agent: str | None) -> bool:
    if lane not in entry["lanes"]:
        return False
    if lane == "persona" and entry.get("agents") is not None:
        return agent in entry["agents"]
    return True


def private_dir(pid: str) -> Path:
    """Keep the plugin's local credentials, caches and operational state in a mode-0700
    directory outside the backed-up Yours layer.
    """
    import loam_plugin_servers  # noqa: PLC0415
    return loam_plugin_servers.state_dir() / "private" / pid


def _env(plugin: dict, secrets: list[str] | None = None) -> dict[str, str]:
    """What a plugin's processes are told. `LOAM_LIB` is Loam's own lib/, for
    a plugin that chooses to import it: no promise is made about anything in
    it (docs/plugins.md), and a marketplace plugin should not."""
    env = {"LOAM_PLUGIN_ID": plugin["id"], "LOAM_PLUGIN_DIR": str(plugin["folder"]),
           # The settings file a server plugin is given, and the folder it is
           # in, which holds `values.json` (settings.entry) beside it.
           "LOAM_PLUGIN_SETTINGS": str(plugin["settings"] / (plugin.get("settings_file") or "settings.md")),
           "LOAM_PLUGIN_SETTINGS_DIR": str(plugin["settings"]),
           "LOAM_PLUGIN_DATA": str(plugin["data"]),
           "LOAM_PLUGIN_PRIVATE": str(private_dir(plugin["id"])),
           "LOAM_PLUGIN_FACTS": str(lc.ROOT / "bin" / "plugin-facts"),
           "LOAM_LIB": str(lc.ROOT / "lib"),
           "PYTHONPYCACHEPREFIX": str(lc.PYCACHE)}
    if secrets:
        # Names only, never values: the process reads them itself
        # (secret_for), so no rendered file carries a secret.
        env["LOAM_PLUGIN_SECRETS"] = ",".join(secrets)
    if plugin.get("keys"):
        # The file of its declared providers' keys, resolved (2026-11-18,
        # shared keys; lib/loam_keys.py): a path, never a value.
        import loam_keys  # noqa: PLC0415
        env["LOAM_PLUGIN_KEYS"] = str(loam_keys.keys_file(plugin["id"]))
    return env


def server_name(pid: str, name: str) -> str:
    return pid if name == pid else f"{pid}-{name}"


def lane_servers(lane: str, agent: str | None = None,
                 plugins: list[dict] | None = None) -> dict[str, dict]:
    """The MCP servers a lane gets from plugins, in `.mcp.json`'s shape."""
    out = {}
    for p in active() if plugins is None else plugins:
        for t in p["contributes"].get("agent.tool") or []:
            if _targets(t, lane, agent):
                out[server_name(p["id"], t["name"])] = {
                    "command": "/bin/sh",
                    "args": ["-c", f"cd {_q(str(p['folder']))} && exec {t['run']}"],
                    "env": {**_env(p, t.get("secrets")), **_tool_env(p, lane, agent)}}
    return out


def _tool_env(plugin: dict, lane: str, agent: str | None) -> dict[str, str]:
    """Supply the tool's agent, lane, vault and workspace paths. For a plugin with a
    server, include its loopback URL so the tool can use the same writer.
    """
    env = {"LOAM_LANE": lane, "LOAM_VAULT": str(lc.VAULT),
           "LOAM_WORKSPACES": str(lc.WORKSPACES)}
    if agent and lane == "persona":
        env["LOAM_AGENT"] = agent
    if plugin.get("has_server"):
        try:
            import loam_plugin_servers  # noqa: PLC0415
            env["LOAM_PLUGIN_URL"] = f"http://127.0.0.1:{loam_plugin_servers.port_for(plugin['id'])}/"
        except Exception:  # noqa: BLE001 -- no port table: the tool says it has no address
            pass
    return env


def lane_prompt(lane: str, agent: str | None = None,
                plugins: list[dict] | None = None) -> str:
    """The words a lane is given about what its plugins let it do; empty when
    none. One paragraph per plugin, headed by its id."""
    parts = []
    for p in active() if plugins is None else plugins:
        texts = [e["text"] for e in p["contributes"].get("agent.prompt") or []
                 if _targets(e, lane, agent) and e.get("text")]
        if texts:
            parts.append(f"**{p['id']}** (a plugin): " + "\n\n".join(texts))
    if not parts:
        return ""
    return ("**What your plugins let you do.** Each is a plugin the owner turned "
            "on; its tools are in your tool list.\n\n" + "\n\n".join(parts))


def lane_mcp_file(lane: str) -> Path | None:
    """For a launcher (`engineer`, `reviewer`): a `--mcp-config` file with the
    lane's plugin servers, rewritten at each launch; None when there are none."""
    servers = lane_servers(lane)
    path = lc.STATE / "plugins" / "lanes" / f"{lane}.mcp.json"
    if not servers:
        path.unlink(missing_ok=True)
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"mcpServers": servers}, indent=2) + "\n")
    os.replace(tmp, path)
    return path


def _q(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


# -- engineer.env, settings.entry, drift.check, box.listener ---------------------

def settings_path(plugin: dict | str) -> Path:
    import loam_plugins  # noqa: PLC0415
    pid = plugin if isinstance(plugin, str) else plugin["id"]
    return loam_plugins.yours_root() / "_settings" / pid / "values.json"


def setting_values(pid: str) -> dict:
    try:
        data = json.loads(settings_path(pid).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def entries_of(pid: str) -> list[dict]:
    for p in active():
        if p["id"] == pid:
            return p["contributes"].get("settings.entry") or []
    return []


def get_setting(pid: str, key: str):
    for e in entries_of(pid):
        if e["key"] == key:
            return setting_values(pid).get(key, e.get("default"))
    raise ContributionError(f"{pid} has no setting {key!r}, or is off")


def set_setting(pid: str, key: str, value) -> dict:
    entry = next((e for e in entries_of(pid) if e["key"] == key), None)
    if entry is None:
        raise ContributionError(f"{pid} has no setting {key!r}, or is off")
    if entry.get("choices") and value not in entry["choices"]:
        raise ContributionError(f"{pid}.{key} is one of {', '.join(entry['choices'])}")
    if not entry.get("choices") and (not isinstance(value, str) or len(value) > 500):
        raise ContributionError(f"{pid}.{key} is text, at most 500 characters")
    values = setting_values(pid)
    values[key] = value
    path = settings_path(pid)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(values, indent=2) + "\n")
    os.replace(tmp, path)
    out = {"key": key, "value": value}
    # A timer or a vault folder that follows this setting moves with it (L9).
    plugin = next((p for p in active() if p["id"] == pid), None)
    if plugin and any(s.get("setting") == key
                      for s in plugin["contributes"].get("schedule.kind") or []):
        out["units"] = apply_units()
    if plugin and any(f.get("setting") == key or (f.get("when") or {}).get("setting") == key
                      for f in plugin["contributes"].get("vault.folder") or []):
        if real_layer():
            out["folders_made"] = ensure_vault_folders([plugin])
    return out


# -- settings.secret: a key the person pastes, checked on entry (L9) ---------------

def secret_entries(pid: str) -> list[dict]:
    """The plugin's declared secrets, whether or not it is on (a key can be
    pasted before the switch is turned on)."""
    import loam_plugins  # noqa: PLC0415
    for row in loam_plugins._rows():
        if row["id"] == pid and row["effective"]:
            return (row.get("contributes") or {}).get("settings.secret") or []
    raise ContributionError(f"no plugin {pid!r} on this box")


def _secret_entry(pid: str, name: str) -> dict:
    entry = next((e for e in secret_entries(pid) if e["name"] == name), None)
    if entry is None:
        raise ContributionError(f"{pid} declares no secret {name!r}")
    return entry


def _check_state_path(pid: str) -> Path:
    return private_dir(pid) / "checks.json"


def _check_state(pid: str) -> dict:
    try:
        data = json.loads(_check_state_path(pid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def secret_source(pid: str, name: str) -> str | None:
    """Where the key in force comes from: the plugin's own (`plugin`), the
    box's secrets file (`box`, the owner's keys before L9), or nowhere."""
    if _secret_in(private_dir(pid) / "secrets.env", name):
        return "plugin"
    if _secret_in(lc.SECRETS_ENV, name):
        return "box"
    return None


def check_secret(pid: str, entry: dict, value: str) -> tuple[bool, str]:
    """(ok, a plain sentence). A plugin's own check is run in its folder with
    the value on stdin: exit 0 is good, else its first line of output is
    the reason. `builtin:<name>` is the host's own (lib/loam_keys.py). No
    check: accepted as given."""
    check = entry.get("check")
    if not check:
        return True, "saved; this plugin does not check its key"
    if check.startswith("builtin:"):
        import loam_keys  # noqa: PLC0415
        return loam_keys.check(check[8:], value)
    import loam_plugins  # noqa: PLC0415
    plugin = next((r for r in loam_plugins._rows() if r["id"] == pid and r["effective"]), None)
    if plugin is None:
        return False, f"{pid} is not on this box"
    env = {**os.environ, **_env({"id": pid, "folder": Path(plugin["folder"]),
                                 "settings": loam_plugins.yours_root() / "_settings" / pid,
                                 "data": loam_plugins.yours_root() / "_data" / pid})}
    try:
        done = subprocess.run(["/bin/sh", "-c", check], cwd=plugin["folder"], input=value,
                              capture_output=True, text=True, timeout=30, env=env)
    except subprocess.TimeoutExpired:
        return False, "the key's check did not answer within 30 seconds; try again"
    said = ((done.stdout or done.stderr).strip().splitlines() or [""])[0][:300]
    if done.returncode == 0:
        return True, said or "the key works"
    return False, said or "the key was refused"


def secrets_of(pid: str) -> list[dict]:
    """What the page shows for a plugin's keys: never a value."""
    state = _check_state(pid)
    return [{"name": e["name"], "label": e["label"], "checks": bool(e.get("check")),
             "set": secret_source(pid, e["name"]) is not None,
             "source": secret_source(pid, e["name"]),
             # The key in force's last check, and apart from it the last paste
             # that was refused (which kept nothing): a bad paste must not
             # make a working key read as broken.
             "checked": state.get(e["name"]),
             "refused": state.get(f"{e['name']}:refused")} for e in secret_entries(pid)]


def _record_check(pid: str, name: str, ok: bool, why: str, source: str) -> dict:
    from datetime import datetime, timezone  # noqa: PLC0415
    rec = {"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "ok": ok, "why": why, "source": source}
    state = _check_state(pid)
    state[name] = rec
    _write_private(_check_state_path(pid), json.dumps(state, indent=2) + "\n")
    return rec


def set_secret(pid: str, name: str, value) -> dict:
    """Check a pasted key and, only if it is good, keep it in the plugin's
    private `secrets.env` (0600, never in Yours). A refusal keeps nothing and
    raises with the check's sentence."""
    entry = _secret_entry(pid, name)
    if not isinstance(value, str) or not value.strip():
        raise ContributionError(f"paste the {entry['label']} itself")
    value = value.strip()
    if len(value) > SECRET_MAX or any(ord(c) < 32 or c in "\"'" for c in value):
        raise ContributionError(f"that does not look like a {entry['label']}: it "
                                f"is too long or has a character no key has")
    ok, why = check_secret(pid, entry, value)
    if not ok:
        _record_check(pid, f"{name}:refused", False, why, "pasted")
        raise ContributionError(why)
    path = private_dir(pid) / "secrets.env"
    lines = []
    try:
        lines = [l for l in path.read_text(encoding="utf-8").splitlines()
                 if l.partition("=")[0].strip() != name]
    except OSError:
        pass
    _write_private(path, "".join(f"{l}\n" for l in lines + [f"{name}={value}"]))
    rec = _record_check(pid, name, True, why, "plugin")
    state = _check_state(pid)
    if state.pop(f"{name}:refused", None) is not None:
        _write_private(_check_state_path(pid), json.dumps(state, indent=2) + "\n")
    return {"name": name, "set": True, "source": "plugin", "checked": rec}


def recheck_secret(pid: str, name: str) -> dict:
    """Check the key in force again, wherever it comes from."""
    entry = _secret_entry(pid, name)
    source = secret_source(pid, name)
    if source is None:
        raise ContributionError(f"no {entry['label']} is set")
    ok, why = check_secret(pid, entry, secret_for(pid, name) or "")
    rec = _record_check(pid, name, ok, why, source)
    return {"name": name, "set": True, "source": source, "checked": rec}


def delete_secret(pid: str, name: str) -> dict:
    """Forget the plugin's own key. The box's own (`box`) is never touched
    here; the answer says when one is still in force."""
    _secret_entry(pid, name)
    path = private_dir(pid) / "secrets.env"
    try:
        lines = [l for l in path.read_text(encoding="utf-8").splitlines()
                 if l.partition("=")[0].strip() != name]
        _write_private(path, "".join(f"{l}\n" for l in lines))
    except OSError:
        pass
    state = _check_state(pid)
    if state.pop(name, None) is not None or state.pop(f"{name}:refused", None) is not None:
        state.pop(f"{name}:refused", None)
        _write_private(_check_state_path(pid), json.dumps(state, indent=2) + "\n")
    return {"name": name, "set": secret_source(pid, name) is not None,
            "source": secret_source(pid, name)}


# -- vault.folder ------------------------------------------------------------------

def vault_folders(plugins: list[dict] | None = None) -> list[str]:
    """The vault folders the plugins in force use, as paths in the vault."""
    out = []
    for p in active() if plugins is None else plugins:
        entries = {e["key"]: e for e in p["contributes"].get("settings.entry") or []}
        vals = setting_values(p["id"])
        for f in p["contributes"].get("vault.folder") or []:
            when = f.get("when")
            if when:
                wkey = when["setting"]
                current = vals.get(wkey, entries.get(wkey, {}).get("default"))
                if current not in when["in"]:
                    continue
            path = f.get("path")
            if f.get("setting"):
                entry = entries.get(f["setting"], {})
                path = vals.get(f["setting"], entry.get("default"))
            if _vault_rel(path):
                out.append(path.strip().strip("/"))
    return out


def ensure_vault_folders(plugins: list[dict] | None = None) -> list[str]:
    """Make each folder a plugin in force uses; never remove one."""
    made = []
    for rel in vault_folders(plugins):
        folder = Path(os.environ.get("LOAM_VAULT") or lc.VAULT) / rel
        if not folder.is_dir():
            folder.mkdir(parents=True, exist_ok=True)
            made.append(rel)
    return made


def _secret_in(path: Path, name: str) -> str | None:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            if k.strip() == name:
                return v.strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def secret_for(pid: str, name: str) -> str | None:
    """For a declared provider field, resolve the plugin's own key, then the shared key,
    then the host secret. For other secrets check the plugin's private file and then the
    host.
    """
    import loam_keys  # noqa: PLC0415
    if loam_keys.declares_field(pid, name):
        return loam_keys.secret_for(pid, name)
    # A name it never declared is handed to it by no route (2026-11-18):
    # before, any name in the box's secrets file reached any plugin.
    if name not in declared_secret_names(pid):
        return None
    return (_secret_in(private_dir(pid) / "secrets.env", name)
            or _secret_in(lc.SECRETS_ENV, name))


def declared_secret_names(pid: str) -> set[str]:
    """Every secret name ``pid``'s manifest declares by name: its
    `settings.secret`, its tools' `secrets`, its `engineer.env` secrets."""
    import loam_plugins  # noqa: PLC0415
    for row in loam_plugins._rows():
        if row["id"] == pid and row["effective"]:
            contrib = row.get("contributes") or {}
            return ({s["name"] for s in contrib.get("settings.secret") or []}
                    | {n for t in contrib.get("agent.tool") or [] for n in t.get("secrets") or []}
                    | {e["secret"] for e in contrib.get("engineer.env") or [] if "secret" in e})
    return set()


def _secret(name: str) -> str | None:
    return _secret_in(lc.SECRETS_ENV, name)


def engineer_env(project: str) -> dict[str, str]:
    """The variables plugins give an engineer job of ``project``."""
    out: dict[str, str] = {}
    for p in active():
        for e in p["contributes"].get("engineer.env") or []:
            if e.get("projects") is not None and project not in e["projects"]:
                continue
            if "value" in e:
                out[e["name"]] = e["value"]
            elif "setting" in e:
                try:
                    value = get_setting(p["id"], e["setting"])
                except ContributionError:
                    value = None
                if value is not None:
                    out[e["name"]] = str(value)
            elif "secret" in e:
                value = secret_for(p["id"], e["secret"])
                if value is not None:
                    out[e["name"]] = value
    return out


def engineer_job_env(project: str, job_id: str, job_dir: Path, scratch: Path,
                     plugins: list[dict] | None = None) -> tuple[dict[str, str], list[str]]:
    """What plugins' `engineer.job` hooks give a job of ``project`` at its
    launch: ``(env, notes)``.

    Each hook runs by ``/bin/sh -c`` in its plugin's folder, as the launcher's
    user, with the plugin's environment and four of the job's own:
    LOAM_PROJECT, LOAM_JOB_ID, LOAM_JOB_DIR (the job's folder, which exists
    and outlives the session: a file the job reads goes here) and
    LOAM_JOB_SCRATCH (the job's scratch, made when the session starts and
    removed when it ends, killed or not: a cache goes here). Each line of its
    output that reads ``NAME=value`` is a variable of the job's.

    It fails closed and loud: a hook that exits non-zero or outlasts its
    timeout gives nothing, a line that is not ``NAME=value`` is dropped, and
    each is a note the launcher writes in the job's folder. A job is never
    refused for a hook; it runs without what the hook would have given."""
    env: dict[str, str] = {}
    notes: list[str] = []
    for p in (active() if plugins is None else plugins):
        for e in p["contributes"].get("engineer.job") or []:
            if e.get("projects") is not None and project not in e["projects"]:
                continue
            run_env = {**os.environ, **_env(p), "LOAM_PROJECT": project,
                       "LOAM_JOB_ID": job_id, "LOAM_JOB_DIR": str(job_dir),
                       "LOAM_JOB_SCRATCH": str(scratch)}
            try:
                res = subprocess.run(["/bin/sh", "-c", e["run"]], cwd=p["folder"],
                                     env=run_env, capture_output=True, text=True,
                                     timeout=e.get("timeout", JOB_HOOK_TIMEOUT_S),
                                     check=False)
            except subprocess.TimeoutExpired:
                notes.append(f"{p['id']}: `{e['run']}` did not finish within "
                             f"{e.get('timeout', JOB_HOOK_TIMEOUT_S)} s; nothing from it")
                continue
            except OSError as exc:
                notes.append(f"{p['id']}: `{e['run']}` could not run ({exc}); nothing from it")
                continue
            if res.returncode != 0:
                said = (res.stderr or res.stdout or "").strip().splitlines()
                notes.append(f"{p['id']}: `{e['run']}` exited {res.returncode}"
                             + (f" ({said[-1][:300]})" if said else "") + "; nothing from it")
                continue
            for line in res.stdout.splitlines():
                if not line.strip():
                    continue
                name, sep, value = line.partition("=")
                if not sep or not VAR.match(name):
                    notes.append(f"{p['id']}: dropped a line that is not NAME=value")
                    continue
                env[name] = value
    return env, notes


def drift_findings() -> list[tuple[str, str, str]]:
    """Each plugin's own drift check, as check-drift findings."""
    out = []
    for p in active():
        for e in p["contributes"].get("drift.check") or []:
            try:
                res = subprocess.run(["/bin/sh", "-c", e["run"]], cwd=p["folder"],
                                     env={**os.environ, **_env(p)}, capture_output=True,
                                     text=True, timeout=e["timeout"], check=False)
            except subprocess.TimeoutExpired:
                out.append((f"plugin {p['id']} drift check", str(p["folder"]),
                            f"did not answer within {e['timeout']} s"))
                continue
            if res.returncode == 0:
                continue
            lines = [l for l in (res.stdout or res.stderr).splitlines() if l.strip()]
            for line in lines or [f"exit {res.returncode}, nothing said"]:
                out.append((f"plugin {p['id']} drift", str(p["folder"]), line[:400]))
    return out


def listeners() -> dict[str, str]:
    """Processes plugins declare may answer off loopback: {process: plugin}."""
    return {l["process"]: p["id"] for p in active()
            for l in p["contributes"].get("box.listener") or []}


# ---------------------------------------------------------------------------
# Timers: schedule.kind
# ---------------------------------------------------------------------------

def unit_base(pid: str, name: str) -> str:
    return f"{UNIT_PREFIX}{pid}--{name}"


def schedule_of(pid: str, s: dict) -> dict:
    """A timer entry with the schedule in force: its `setting`'s value when
    that is a valid schedule in Loam's grammar, else the manifest's own."""
    if not s.get("setting"):
        return s
    value = setting_values(pid).get(s["setting"])
    if not isinstance(value, str) or not value.strip():
        return s
    import loam_cron  # noqa: PLC0415
    try:
        words, spec = loam_cron.parse_schedule(value)
    except loam_cron.CronError:
        return s
    return {**s, "schedule": words, "calendar": spec, "from_setting": True}


def desired_units(plugins: list[dict] | None = None) -> dict[str, str]:
    """{filename: content} for every contributed timer in force."""
    out = {}
    for p in active() if plugins is None else plugins:
        for s in p["contributes"].get("schedule.kind") or []:
            s = schedule_of(p["id"], s)
            base = unit_base(p["id"], s["name"])
            # A boot entry is a service the user's manager starts at its own
            # start (WantedBy=default.target), with no timer.
            install = "\n[Install]\nWantedBy=default.target\n" if s.get("boot") else ""
            out[f"{base}.service"] = (
                f"# Rendered by lib/loam_contributions.py for plugin {p['id']}; "
                f"edit its PLUGIN.md, not this.\n"
                f"[Unit]\nDescription=Loam plugin {p['id']}: {s['name']} ({s['schedule']})\n\n"
                f"[Service]\nType=oneshot\nMemoryMax=512M\nMemorySwapMax=0\n"
                f"TimeoutStartSec={s.get('timeout', 300)}\n"
                f"Environment=PATH={lc.UNIT_PATH}\n"
                f"ExecStart={lc.ROOT}/bin/plugin-run {p['id']} {s['name']}\n"
                f"StandardOutput=null\nStandardError=journal\n{install}")
            if s.get("boot"):
                continue
            out[f"{base}.timer"] = (
                f"# Rendered by lib/loam_contributions.py for plugin {p['id']}.\n"
                f"[Unit]\nDescription=Loam plugin {p['id']}: {s['name']} ({s['schedule']})\n\n"
                f"[Timer]\nOnCalendar={s['calendar']}\nPersistent=false\n"
                f"Unit={base}.service\n\n[Install]\nWantedBy=timers.target\n")
    return out


def live_units(unit_dir: Path | None = None) -> dict[str, str]:
    base = lc.USER_UNIT_DIR if unit_dir is None else unit_dir
    out = {}
    for path in sorted(base.glob(f"{UNIT_PREFIX}*--*")):
        if path.suffix in (".service", ".timer") and path.is_file():
            out[path.name] = path.read_text(encoding="utf-8")
    return out


def real_layer() -> bool:
    """Identify the installed Yours layer before touching live units or rendering personas.
    A test process, scratch LOAM_CONFIG, scratch Yours layer, or LOAM_PLUGIN_UNITS=0
    must reconcile only its own state and never touch the host's units or workspaces.
    """
    import loam_offline  # noqa: PLC0415
    if loam_offline.offline() or os.environ.get("LOAM_PLUGIN_UNITS") == "0":
        return False
    import loam_plugins  # noqa: PLC0415
    return (lc.is_install_config()
            and loam_plugins.yours_root().resolve() == loam_plugins.YOURS_DEFAULT.resolve())


@contextmanager
def _units_lock():
    import loam_plugin_servers  # noqa: PLC0415
    state = loam_plugin_servers.state_dir()
    state.mkdir(parents=True, exist_ok=True)
    with open(state / "units.lock", "a+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def apply_units(dry: bool = False, *, unit_dir: Path | None = None,
                plugins: list[dict] | None = None, run=None) -> list[dict]:
    """Make the unit directory hold exactly the contributed timers.
    Removed timers and boot services are disabled and stopped before any unit
    file is unlinked (so a reload never sees a timer's service vanish) and
    cleared with reset-failed. Installed and recut services are written
    atomically before their timers, moved by daemon-reload without stopping
    standing timers, and any installed, inactive, or failed wanted timer is
    armed fresh."""
    import loam_units  # noqa: PLC0415
    live_host = unit_dir is None
    if live_host:
        if not real_layer():
            return []
        target_dir = lc.USER_UNIT_DIR
    else:
        target_dir = unit_dir
        if run is None and not dry:
            import loam_offline  # noqa: PLC0415
            if loam_offline.offline():
                raise loam_offline.Offline(
                    "refused in a test: apply_units without a stubbed systemctl")
    ctl = run or loam_units.systemctl
    guard = _units_lock() if (live_host and not dry) else nullcontext()
    with guard:
        want, live = desired_units(plugins), live_units(target_dir)
        actions: list[dict] = []

        # 1. Remove gone units: stop and disable timers/boot units BEFORE
        # unlinking any unit file, so systemd never sees a live timer's
        # triggered service vanish across a reload.
        removed = sorted(set(live) - set(want),
                         key=lambda n: (not n.endswith(".timer"), n))
        for name in removed:
            actions.append({"unit": name, "action": "removed"})
        if removed and not dry:
            for name in removed:
                timer = name.endswith(".timer")
                boot = "WantedBy=default.target" in (live.get(name) or "")
                if timer or boot:
                    ctl("disable", "--now", name)
            for name in removed:
                (target_dir / name).unlink(missing_ok=True)
                for wants in target_dir.glob("*.wants"):
                    (wants / name).unlink(missing_ok=True)

        # 2. Install or recut wanted units: write .service before .timer via
        # atomic rename so a concurrent reload or timer fire always sees a
        # complete service file on disk.
        changed = sorted((n for n in want if want[n] != live.get(n)),
                         key=lambda n: (n.endswith(".timer"), n))
        for name in changed:
            actions.append({"unit": name, "action": "installed" if name not in live else "recut"})
            if not dry:
                target_dir.mkdir(parents=True, exist_ok=True)
                tmp = target_dir / f".{name}.{os.getpid()}.tmp"
                tmp.write_text(want[name], encoding="utf-8")
                os.replace(tmp, target_dir / name)

        # 3. Reload systemd and clear tombstones for removed units.
        if actions and not dry:
            ctl("daemon-reload")
            if removed:
                ctl("reset-failed", *removed)

        # 4. Arm installed timers, recover any inactive/failed wanted timer,
        # and ensure every wanted timer/boot service is enabled without
        # disturbing standing active timers.
        if not dry:
            by_unit = {a["unit"]: a for a in actions}
            for name in sorted(want):
                if name.endswith(".timer"):
                    act = by_unit.get(name)
                    if act and act["action"] == "installed":
                        act["armed"] = loam_units.arm_timer(name, target_dir, run=ctl)
                        continue
                    state = loam_units._show(name, "ActiveState", ctl)
                    if state != "active":
                        armed = loam_units.arm_timer(name, target_dir, run=ctl)
                        if act is not None:
                            act["armed"] = armed
                        else:
                            actions.append({"unit": name, "action": "recovered", "armed": armed})
                    elif loam_units._show(name, "UnitFileState", ctl) != "enabled":
                        ctl("enable", name)
                        if act is not None:
                            act["enabled"] = True
                        else:
                            actions.append({"unit": name, "action": "enabled"})
                elif "WantedBy=default.target" in want[name]:
                    act = by_unit.get(name)
                    if act and act["action"] == "installed":
                        ctl("enable", name)
                        act["enabled"] = "for the next start of the manager"
                    elif loam_units._show(name, "UnitFileState", ctl) != "enabled":
                        ctl("enable", name)
                        if act is not None:
                            act["enabled"] = "for the next start of the manager"
                        else:
                            actions.append({"unit": name, "action": "enabled"})
        return actions


# ---------------------------------------------------------------------------
# A switch, an install, a removal: bring every lane into line
# ---------------------------------------------------------------------------

def apply(reason: str = "") -> dict:
    """Everything a change of plugins moves: the timers, and the personas'
    assembled files (bin/provision, one persona at a time, then its Codex and
    Antigravity renderings). The engineer's and the reviewer's lanes read
    theirs at launch. Never raises: what failed is in the answer."""
    out: dict = {"reason": reason, "units": [], "rendered": [], "errors": []}
    if not real_layer():
        return {**out, "skipped": "Yours is not the box's (a test)"}
    try:
        import loam_thread_files
        loam_thread_files.configure(active())
    except Exception as exc:
        out["errors"].append(f"thread files: {exc}")
    try:
        out["units"] = apply_units()
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"timers: {exc}")
    try:
        out["folders_made"] = ensure_vault_folders()
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"vault folders: {exc}")
    try:
        # Each plugin's resolved keys (2026-11-18, lib/loam_keys.py): a
        # plugin installed, switched or removed gains or loses them with it.
        import loam_keys  # noqa: PLC0415
        out["keys"] = loam_keys.adopt() + [f"{p}: keys.env" for p in loam_keys.sync()]
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"keys: {exc}")
    prov = lc.ROOT / "bin" / "provision"
    for ws in sorted(lc.WORKSPACES.iterdir()):
        cfg = ws / "agent.json"
        if not cfg.is_file():
            continue
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            res = subprocess.run([str(prov), data["name"]],
                                 capture_output=True, text=True, check=False, timeout=120)
            (out["rendered"] if res.returncode == 0 else out["errors"]).append(
                data["name"] if res.returncode == 0
                else f"provision {data['name']}: {(res.stderr or res.stdout)[-300:]}")
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"provision {ws.name}: {exc}")
    for flag in ("--codex", "--antigravity"):
        res = subprocess.run([str(prov), flag], capture_output=True, text=True,
                             check=False, timeout=300)
        if res.returncode:
            out["errors"].append(f"provision {flag}: {(res.stderr or res.stdout)[-300:]}")
    return out
