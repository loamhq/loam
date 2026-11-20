"""Every setting loam-ui's page can change, and the one way a persona changes it.

Decided 2026-11-10 (the owner, in a thread, to a persona): "any setting that we have
as part of Loam UI, I want the personas to be able to do that as well ... If I
can do it, I would want to be able to just tell my persona to go and do it",
and for every feature built after it too. So this module is a **registry**,
not a list of tools: one entry per thing the page can change, saying which
broker route the page's relay sends it to, what body that route takes, how
its current value is read back, what values it takes, and whose hands it is
in. ``bin/settings`` (and the ``settings`` tool in mcp/engineer_server.py,
which only hands off to it) reads this table and calls **the same broker
route the page's relay calls**, with the same token, so a persona's change is
validated by the same code, written by the same code, and drawn live on an
open page by the same events as the owner's click. The one thing a persona's
call adds is ``by``: the broker attributes the change (a thread gets a line
saying who did it) and the schedule path holds the persona to its own records
and a reason (``author()`` in bin/session-broker, ``_actor`` in
lib/loam_activity.py).

**Reach** is one of four words:

- ``persona`` -- any persona may do it through ``bin/settings``.
- ``OWNER``, the owner's (a legacy wire word) -- their hands only: a
  sign-in that needs their browser, a credential they paste, an account they
  authorise, or a *Permission first* item of the
  personas' config. ``set``/``do`` refuses with the entry's ``why``.
- ``device`` -- the page keeps it in the browser's own storage
  (loam-ui ``public/store.js``), so there is no host route for anyone,
  persona or engineer, to call. Listed so ``describe`` can say so.
- ``own-tools`` -- the page's Files editor writes a file the persona can
  already write with its own tools, under its own rules (the boundary guard,
  *Permission first*); a second path through the broker would be a way
  around those rules, not a convenience.

**The future is enforced, not remembered.** ``PAGE_RELAY`` names every route
family in loam-ui's ``src/server.js`` relay (the page's allowlist: "a route
the broker grows later is a line added here on purpose") and says which
entries it carries or why it carries no setting. tests/test_settings.py reads
the relay and fails when it has a route this table does not classify, so the
day the page gains a setting is the day the suite asks for its entry here.

The broker serves this table at ``GET /v1/settings`` (keys, words, routes and
reach; no values), so a client -- the page included, once its relay passes
the route -- can draw from the same source.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import loam_config as lc
import loam_broker as cfg

# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

PERSONA, OWNER, DEVICE, OWN_TOOLS = "persona", "owner", "device", "own-tools"

DEVICE_WHY = ("the page keeps this in the browser's own storage (loam-ui "
              "public/store.js), on each device separately, so nothing on the "
              "host can change it; it becomes a persona's to change only when "
              "loam-ui keeps it on the host")
OWN_TOOLS_WHY = ("this is a file write; do it with your own file tools, under "
                 "your own rules (another agent's workspace, a soul or a config "
                 "is Permission first)")


@dataclass(frozen=True)
class Entry:
    key: str
    kind: str                       # "setting" (get/set) or "action" (do)
    label: str
    page: str                       # what the page calls: "PATCH /api/threads/{thread}"
    reach: str = PERSONA
    route: str | None = None        # the broker route: "PATCH /v1/threads/{thread}"
    target: str = ""                # what `target` names, "" for none
    values: str = ""                # the allowed values, in words
    member: bool = False            # needs `member` (a thread member's engine/model)
    body: Callable[..., dict] | None = None   # (value, member) -> payload
    read: str | None = None         # the GET route its current value is read from
    pick: Callable[..., Any] | None = None    # (GET answer, member) -> value
    why: str = ""                   # for reach other than persona: the refusal
    unattended: bool = True         # may a wake or a cron do it
    options: str | None = None      # a named list `describe` fills in live
    note: str = ""
    timeout: int = 60               # seconds the route may take (an install fetches)


def _member_row(summary: dict, member: str) -> dict:
    for row in summary.get("members") or []:
        if row.get("persona") == member:
            return row
    raise SettingsError(f"{member} is not a member of this thread")


def _thread(answer: dict) -> dict:
    return answer.get("thread") or answer


THREAD_READ = "GET /v1/threads/{thread}?limit=1&seen=0"
SCHEDULE_READ = "GET /v1/schedules/{agent}/{name}"

ENTRIES: list[Entry] = [
    # -- a thread's details page -------------------------------------------
    Entry("thread.title", "setting", "A thread's title",
          "PATCH /api/threads/{thread}", route="PATCH /v1/threads/{thread}",
          target="thread id", values="text; General can't be renamed",
          body=lambda v, m: {"title": v}, read=THREAD_READ,
          pick=lambda a, m: _thread(a)["title"]),
    Entry("thread.icon", "setting", "A thread's icon",
          "PATCH /api/threads/{thread}", route="PATCH /v1/threads/{thread}",
          target="thread id", values="one emoji (16 characters at most), or null for none",
          body=lambda v, m: {"icon": v}, read=THREAD_READ,
          pick=lambda a, m: _thread(a).get("icon")),
    Entry("thread.default", "setting", "Who answers a thread's untagged messages",
          "PATCH /api/threads/{thread}", route="PATCH /v1/threads/{thread}",
          target="thread id", values="a persona, or null for nobody",
          options="personas", body=lambda v, m: {"default": v}, read=THREAD_READ,
          pick=lambda a, m: _thread(a).get("default")),
    Entry("thread.members.add", "action", "Bring a persona into a thread",
          "PATCH /api/threads/{thread}", route="PATCH /v1/threads/{thread}",
          target="thread id", values="a persona", options="personas",
          body=lambda v, m: {"add": v if isinstance(v, list) else [v]}),
    Entry("thread.members.remove", "action", "Take a persona out of a thread",
          "PATCH /api/threads/{thread}", route="PATCH /v1/threads/{thread}",
          target="thread id", values="a member other than the default",
          body=lambda v, m: {"remove": v if isinstance(v, list) else [v]}),
    Entry("thread.members", "setting", "A thread's members",
          "GET /api/threads/{thread}", read=THREAD_READ, target="thread id",
          values="read only here; change it with thread.members.add / .remove",
          why="read only: change it with thread.members.add and "
              "thread.members.remove",
          pick=lambda a, m: [r["persona"] for r in _thread(a).get("members") or []]),
    # The thread's tasks panel (2026-11-13): the engineer jobs briefed from
    # it, from the launcher's job state (lib/loam_activity.job_rows).
    Entry("thread.jobs", "setting", "A thread's engineer jobs (its tasks panel)",
          "GET /api/threads/{thread}", read=THREAD_READ, target="thread id",
          values="read only: queued, running, landing, and finished in the "
                 "last ten minutes; a job is changed by briefing or cancelling it",
          why="read only: the jobs are the launcher's; brief or cancel one "
              "with the engineer tools",
          pick=lambda a, m: _thread(a).get("jobs") or []),
    Entry("thread.engine", "setting", "Which harness a member answers on in a thread",
          "PATCH /api/threads/{thread}", route="PATCH /v1/threads/{thread}",
          target="thread id", member=True, options="engines",
          values="an engine (switching puts the member back on its own model)",
          body=lambda v, m: {"engine": {m: v}}, read=THREAD_READ,
          pick=lambda a, m: _member_row(_thread(a), m).get("engine")),
    Entry("thread.model", "setting", "A member's model in a thread",
          "PATCH /api/threads/{thread}", route="PATCH /v1/threads/{thread}",
          target="thread id", member=True, options="models",
          values="a model the member's engine lists, or null for the persona's own",
          body=lambda v, m: {"model": {m: v}}, read=THREAD_READ,
          pick=lambda a, m: _member_row(_thread(a), m).get("model")),
    Entry("thread.pinned", "setting", "A thread pinned to the top of the list",
          "PATCH /api/threads/{thread}", route="PATCH /v1/threads/{thread}",
          target="thread id", values="true or false; General is always pinned",
          body=lambda v, m: {"pinned": v}, read=THREAD_READ,
          pick=lambda a, m: _thread(a).get("pinned")),
    Entry("thread.archived", "setting", "A thread archived out of the list",
          "PATCH /api/threads/{thread}", route="PATCH /v1/threads/{thread}",
          target="thread id", values="true or false; General can't be archived",
          body=lambda v, m: {"archived": v}, read=THREAD_READ,
          pick=lambda a, m: _thread(a).get("archived")),
    Entry("thread.seen", "action", "Mark a thread read",
          "GET /api/threads/{thread} (opening it)", route="PATCH /v1/threads/{thread}",
          target="thread id", values="true",
          body=lambda v, m: {"seen": True},
          note="The page marks a thread read by opening it; there is no unread "
               "for a thread."),
    Entry("thread.board", "setting", "A thread's board (its markdown)",
          "PUT /api/threads/{thread}/board", route="PUT /v1/threads/{thread}/board",
          target="thread id", values="the whole board as markdown",
          body=lambda v, m: {"markdown": v}, read="GET /v1/threads/{thread}/board",
          pick=lambda a, m: a.get("markdown"),
          note="Editing the board's file directly is the same thing; it is "
               "here because the page does it too."),
    Entry("thread.create", "action", "Start a thread",
          "POST /api/threads", route="POST /v1/threads",
          values='{"title": ..., "persona": <default or null>}, or a title',
          body=lambda v, m: (v if isinstance(v, dict) else {"title": v})),
    Entry("thread.delete", "action", "Delete a thread, its record and its board",
          "DELETE /api/threads/{thread}", route="DELETE /v1/threads/{thread}",
          target="thread id", values="true", unattended=False,
          body=lambda v, m: {},
          note=f"Only on {lc.OWNER_NAME}'s word in the conversation; the page confirms "
               "first. General can't be deleted."),

    # -- Sessions (Running now) ---------------------------------------------
    # A classic chat's name, pin, archive, read mark, model and effort, and
    # "mark every classic chat read", retired with the classic chat view on
    # 2026-11-12 (the owner, 2026-11-11): the broker no longer answers PATCH
    # /v1/sessions/<id> or POST /v1/sessions/seen. A thread's own are above.
    Entry("session.end", "action", "End a running session (Running now's End)",
          "DELETE /api/sessions/{session}", route="DELETE /v1/sessions/{session}",
          target="session id", values="true", unattended=False,
          body=lambda v, m: {},
          note="A thread member's session mid-turn ends that turn with one "
               f"line in the thread. Only on {lc.OWNER_NAME}'s word."),
    Entry("sessions.end_idle", "action", "End every idle session (Running now)",
          "POST /api/sessions/end-idle", route="POST /v1/sessions/end-idle",
          values="true", unattended=False, body=lambda v, m: {}),

    # -- Schedules and Activity ----------------------------------------------
    Entry("schedule.enabled", "setting", "A schedule on or paused",
          "PATCH /api/schedules/{agent}/{name}",
          route="PATCH /v1/schedules/{agent}/{name}", target="agent/name",
          values="true or false; your own schedules only, with a reason. Your "
                 "daily reflection is the owner's to turn off: only on their "
                 'word, as {"enabled": false, "thread": <the thread they asked '
                 'in>, "yes": <the id of their message asking>}',
          body=lambda v, m: (
              {k: v[k] for k in ("enabled", "thread", "yes") if k in v}
              if isinstance(v, dict) else {"enabled": v}),
          read=SCHEDULE_READ, pick=lambda a, m: a.get("enabled")),
    Entry("schedule.schedule", "setting", "When a recurring schedule runs",
          "PATCH /api/schedules/{agent}/{name}",
          route="PATCH /v1/schedules/{agent}/{name}", target="agent/name",
          values=f'words, like "daily at 05:00 {lc.ZONE_TOKEN}"; your own, with a reason',
          body=lambda v, m: {"schedule": v}, read=SCHEDULE_READ,
          pick=lambda a, m: a.get("schedule")),
    Entry("schedule.at", "setting", "When a one-off runs",
          "PATCH /api/schedules/{agent}/{name}",
          route="PATCH /v1/schedules/{agent}/{name}", target="agent/name",
          values=f'words, like "2026-11-20 14:30 {lc.ZONE_TOKEN}", 10 min to 30 days ahead',
          body=lambda v, m: {"at": v}, read=SCHEDULE_READ,
          pick=lambda a, m: a.get("at")),
    Entry("schedule.note", "setting", "A one-off's note",
          "PATCH /api/schedules/{agent}/{name}",
          route="PATCH /v1/schedules/{agent}/{name}", target="agent/name",
          values="text", body=lambda v, m: {"note": v}, read=SCHEDULE_READ,
          pick=lambda a, m: a.get("note")),
    Entry("schedule.body", "setting", "A schedule's instruction",
          "PATCH /api/schedules/{agent}/{name}",
          route="PATCH /v1/schedules/{agent}/{name}", target="agent/name",
          values="text; the daily reflection's too, which starts as Loam's default",
          body=lambda v, m: {"body": v}, read=SCHEDULE_READ,
          pick=lambda a, m: a.get("body")),
    Entry("schedule.create", "action", "Make a schedule",
          "POST /api/schedules/{agent}", route="POST /v1/schedules/{agent}",
          target="agent",
          values='{"name", "schedule" or "at", "body", "note"?, "enabled"?}',
          body=lambda v, m: dict(v) if isinstance(v, dict) else {}),
    Entry("schedule.delete", "action", "Delete a schedule",
          "DELETE /api/schedules/{agent}/{name}",
          route="DELETE /v1/schedules/{agent}/{name}", target="agent/name",
          values="true", unattended=False, body=lambda v, m: {}),
    Entry("schedule.run", "action", "Run a schedule now",
          "POST /api/schedules/{agent}/{name}/run",
          route="POST /v1/schedules/{agent}/{name}/run", target="agent/name",
          values="true", unattended=False, body=lambda v, m: {},
          note="Your own, and only from a conversation: Boundary 3 bends only "
               f"where {lc.OWNER_NAME} is present."),
    Entry("schedule.seen", "action", "Mark a schedule's activity seen",
          "POST /api/schedules/{agent}/{name}/seen",
          route="POST /v1/schedules/{agent}/{name}/seen", target="agent/name",
          values="true", body=lambda v, m: {}),
    Entry("activity.seen", "action", "Mark Activity seen",
          "POST /api/activity/seen", route="POST /v1/activity/seen",
          values='true for all, or a list of item ids',
          body=lambda v, m: ({"ids": v} if isinstance(v, list) else {"all": True})),
    Entry("activity.undo", "action", "Undo a schedule change (Activity's Undo)",
          "POST /api/activity/{item}/undo", route="POST /v1/activity/{item}/undo",
          target="activity item id", values="true", body=lambda v, m: {},
          note="Your own schedules' changes only."),
    Entry("job.cancel", "action", "Cancel an engineer job",
          "POST /api/jobs/{job}/cancel", reach=PERSONA, target="job id",
          values="use mcp__engineer__cancel", why=(
              "an engineer job is cancelled with mcp__engineer__cancel, which "
              "is this same route's call with your name on it"),
          note="Reached through the engineer tool, not this one."),

    # -- Settings: providers and integrations --------------------------------
    Entry("provider.login", "action", "Sign a provider in again, or out",
          "POST /api/providers/{provider}/login, .../logout", reach=OWNER,
          why=f"a provider sign-in finishes only in {lc.OWNER_NAME}'s browser, with the "
              "code the provider's page gives them, and a sign-out ends every "
              "chat's login on that provider"),
    # A plugin's key (2026-11-14, first release L9): a key that belongs to
    # one plugin and no provider, pasted by the owner in the plugin's
    # settings and checked on entry. lib/loam_contributions.py, `settings.secret`.
    Entry("plugin.secret", "action", "Paste a plugin's API key (checked on entry)",
          "PUT/DELETE /api/plugins/{plugin}/secrets/{name}", reach=OWNER,
          why=f"a key is a credential {lc.OWNER_NAME} pastes themselves, and it is "
              "billed to them"),
    # Shared keys (2026-11-18): Settings › Connections › API keys, API keys by provider,
    # each checked on entry and handed only to the plugins that declared the
    # provider (lib/loam_keys.py). A persona's to change too (the owner,
    # 2026-11-10: every setting); never to read back.
    Entry("keys.set", "action", "Save a provider's shared API key (Settings › Connections › API keys; checked on entry)",
          "PUT /api/keys/{provider}", route="PUT /v1/keys/{provider}",
          target="provider id (openai, google, hetzner, …)", unattended=False,
          values='the key as text, or {"values": {"NAME": "…"}} for a provider of '
                 'two fields (cloudflare: CLOUDFLARE_API_KEY and CLOUDFLARE_EMAIL)',
          body=lambda v, m: dict(v) if isinstance(v, dict) else {"value": v},
          read="GET /v1/keys/{provider}",
          pick=lambda a, m: {k: a[k] for k in ("set", "source", "checked", "plugins", "projects") if k in a},
          options="providers",
          note="Only when the owner gave you the key to save. The value is never "
               "read back by anyone; the answer says whether it was accepted."),
    Entry("keys.forget", "action", "Forget a provider's shared API key",
          "DELETE /api/keys/{provider}", route="DELETE /v1/keys/{provider}",
          target="provider id", unattended=False, values="true",
          body=lambda v, m: {}, options="providers"),
    Entry("keys.check", "action", "Check a provider's shared API key again",
          "POST /api/keys/{provider}/check", route="POST /v1/keys/{provider}/check",
          target="provider id", values="true", body=lambda v, m: {},
          options="providers"),
    Entry("keys.own", "action", "Give one plugin a different key of a provider (\"use a different key\")",
          "PUT /api/keys/{provider}/plugins/{plugin}",
          route="PUT /v1/keys/{provider}/plugins/{plugin}", target="provider/plugin",
          unattended=False, values="the key, as keys.set takes it",
          body=lambda v, m: dict(v) if isinstance(v, dict) else {"value": v}),
    Entry("keys.shared", "action", "Put a plugin back on the provider's shared key",
          "DELETE /api/keys/{provider}/plugins/{plugin}",
          route="DELETE /v1/keys/{provider}/plugins/{plugin}", target="provider/plugin",
          unattended=False, values="true", body=lambda v, m: {}),
    Entry("keys.project.set", "action", "Save or replace a named Hetzner Cloud project token",
          "PUT /api/keys/hetzner/projects/{project}", route="PUT /v1/keys/hetzner/projects/{project}",
          target="project name", unattended=False, values="the token as text",
          body=lambda v, m: {"value": v}, timeout=30,
          note="Only when the owner gave you the token to save. Use keys.set for personal."),
    Entry("keys.project.forget", "action", "Forget a named Hetzner Cloud project token",
          "DELETE /api/keys/hetzner/projects/{project}", route="DELETE /v1/keys/hetzner/projects/{project}",
          target="project name", unattended=False, values="true", body=lambda v, m: {}),
    Entry("integration.github", "action", "Connect or disconnect GitHub",
          "POST/DELETE /api/integrations/github/...", reach=OWNER,
          why=f"GitHub is authorised on {lc.OWNER_NAME}'s account, in their browser"),
    # persona.provider (the owner's hands only: "agent.json is operator-
    # authored config") retired 2026-11-13, when the owner asked for the
    # provider on each agent's profile; it is agent.provider below.

    # -- Settings > Agents: an agent's profile (2026-11-13) ----------------
    # lib/loam_profile.py. Each is the agent's agent.json; done only in a
    # conversation, on the owner's word (a persona's config is Permission
    # first), and a persona's change is said in General. agent.emoji retired
    # 2026-11-16 (the owner: "We should remove the concept of persona face
    # emoji"): a face is the theme's or agent.face's image.
    *[Entry(f"agent.{field}", "setting", label,
            "PATCH /api/agents/{agent}", route="PATCH /v1/agents/{agent}",
            target="agent", values=values, options=opts, unattended=False,
            body=(lambda v, m, field=field: {field: v}),
            read="GET /v1/agents/{agent}",
            pick=(lambda a, m, field=field, part=part: a[part[0]][part[1]]
                  if len(part) == 2 else a[part[0]][part[1]][part[2]]))
      for field, label, values, opts, part in (
          ("display", "An agent's display name (its id never changes)",
           "1 to 40 characters, not another agent's id or name; null for the "
           "id's own", None, ("identity", "display")),
          ("provider", "An agent's default provider (new threads, wakes, crons)",
           "claude-code, codex or antigravity, or null to select a connected "
           "provider automatically. A provider change clears its model and thinking level",
           "engines", ("defaults", "effective", "provider")),
          ("model", "An agent's default model",
           "a model its provider lists (describe lists them), or null for the "
           "provider's own", "models", ("defaults", "effective", "model")),
          ("effort", "An agent's default thinking level",
           "low, medium, high, xhigh or max (Codex: its own levels; "
           "Antigravity: only beside a model id that does not end in its "
           "level), or null",
           None, ("defaults", "effective", "effort")))],

    # -- Settings > Agents: making, archiving, restoring, the face ---------
    # (2026-11-14, first release L10; lib/loam_agents.py). Each is done in a
    # conversation only, and an agent's call carries the person's yes: the
    # thread and the id of their message, which the route checks in the
    # record. The page's own calls are the person's.
    Entry("agent.face", "setting", "An agent's face (an uploaded image)",
          "PUT /api/agents/{agent}/face", route="PUT /v1/agents/{agent}/face",
          target="agent", unattended=False,
          values="the path of a PNG, JPEG, WebP or GIF file (2 MB at most) in a "
                 "workspace or the vault; null to go back to the theme's face",
          body=lambda v, m: {"path": v}, read="GET /v1/agents/{agent}",
          pick=lambda a, m: a["identity"].get("face"),
          note="Holds through any theme; a theme's faces apply only to agents "
               "without one. Rename is agent.display."),
    Entry("agent.draft", "action", "Draft a new agent, to show the person",
          "POST /api/agents/drafts", route="POST /v1/agents/drafts",
          unattended=False,
          values='{"thread": <this thread\'s id>, "name": "Juniper", '
                 '"soul": <text> or "soul_file": <path>, "config": <text> '
                 'or "config_file": <path>}; optional "id" (the folder name, '
                 'from the name), "about" (one plain line for the person, in the '
                 'third person: "A warm, practical cook who keeps Sam company '
                 'in the kitchen"; the draft card shows it), "provider", '
                 '"wake" (HH:MM), "active"/"reflection" (their loading files; '
                 'plain starters otherwise)',
          body=lambda v, m: dict(v) if isinstance(v, dict) else {"name": v},
          note="How to make an agent: read the library skill "
               "loam-making-an-agent first (a character, never a job). Draft "
               "their name, soul and config here before you describe them; "
               "the face is the theme's until the person uploads one "
               "(agent.face). Then show the person a short version of this "
               "draft (and the whole soul if they ask), so their one yes is "
               "to it; change it with agent.revise on their word and show "
               "it again, and once they say yes to the version shown, "
               "agent.confirm with that revision and the id of their yes "
               "message. A yes given before a revision does not make it. The answer's `problems` say what "
               "still stands in the way. Their files are theirs from the "
               "moment they are made."),
    Entry("agent.revise", "action", "Change a draft on the person's word",
          "PATCH /api/agents/drafts/{draft}", route="PATCH /v1/agents/drafts/{draft}",
          target="draft id (d-...)", unattended=False,
          values="any of the draft's fields, as agent.draft takes them",
          body=lambda v, m: dict(v) if isinstance(v, dict) else {},
          note="Each change is a new revision; a yes is to the revision shown."),
    Entry("agent.confirm", "action", "Make the agent, on the person's yes",
          "POST /api/agents/drafts/{draft}/confirm",
          route="POST /v1/agents/drafts/{draft}/confirm",
          target="draft id (d-...)", unattended=False,
          values='{"rev": <the revision they said yes to>, "yes": <the id of '
                 'their message, like "m12">}',
          body=lambda v, m: dict(v) if isinstance(v, dict) else {},
          timeout=1900,
          note="Can wait up to 20 minutes behind a landing of Loam (its live "
               "lock). Makes the workspace, provisions it on every provider, its "
               "wake, its face and name, and a first thread; it is in Chats "
               "and every picker at once."),
    Entry("agent.archive", "action", "Archive an agent (never delete)",
          "POST /api/agents/{agent}/archive", route="POST /v1/agents/{agent}/archive",
          target="agent", unattended=False,
          values='{"thread": <this thread\'s id>, "yes": <the id of their yes '
                 'message, within 30 minutes>}',
          body=lambda v, m: dict(v) if isinstance(v, dict) else {},
          note="It leaves Chats and every picker, its wakes and crons stop, "
               "its threads stay readable and its files stay. agent.restore "
               "brings all of it back."),
    Entry("agent.restore", "action", "Restore an archived agent",
          "POST /api/agents/{agent}/restore", route="POST /v1/agents/{agent}/restore",
          target="agent", unattended=False,
          values='{"thread": <this thread\'s id>, "yes": <the id of their yes '
                 'message, within 30 minutes>}',
          body=lambda v, m: dict(v) if isinstance(v, dict) else {}),

    # -- Settings > Agents > Engineers (2026-11-13) ------------------------
    # The target is a project's name (Loam, loam-ui, ...) or `default`, for
    # every project engineer without its own. From the next job.
    *[Entry(f"engineer.{field}", "setting", label,
            "PATCH /api/engineers/{engineer}", route="PATCH /v1/engineers/{engineer}",
            target="project name, or default", values=values, unattended=False,
            body=(lambda v, m, field=field: {field: v}),
            read="GET /v1/engineers/{engineer}",
            pick=(lambda a, m, field=field: a.get(field) if field == "deploy_on_land"
                  else (a.get("effective") or {}).get(field)))
      for field, label, values in (
          ("provider", "An engineer's provider",
           "claude-code, codex or antigravity; null to follow the default "
           "(or the project's record; for Loam, its built-in Claude Code)"),
          ("model", "An engineer's model",
           "a model its provider lists, or null for the built-in "
           "(Antigravity's: gemini-4-hydrogen-high; its ids carry the level)"),
          ("effort", "An engineer's thinking level",
           "a level its provider takes, or null for the built-in "
           "(Antigravity takes none: the level is in its model id)"),
          ("deploy_on_land", "Deploy when a job lands (a project with an app)",
           "true or false; a brief's own deploy still wins"))],
    # Three tiers per engineer (2026-11-15): the model a brief at each tier
    # runs on. A brief names the tier (the brief tool's `tier`), never the
    # model; a tier left unset runs on engineer.model.
    *[Entry(f"engineer.tiers.{tier}", "setting", label,
            "PATCH /api/engineers/{engineer}", route="PATCH /v1/engineers/{engineer}",
            target="project name, or default", unattended=False,
            values=("a model its provider lists (on Antigravity the id carries "
                    "its level), or null to run this tier on engineer.model"),
            body=(lambda v, m, tier=tier: {"tiers": {tier: v}}),
            read="GET /v1/engineers/{engineer}",
            # What a brief at this tier runs on next: {"model", "from"},
            # `from` "tier" when set, "model" when it falls back.
            pick=(lambda a, m, tier=tier: (((a.get("effective") or {}).get("tiers"))
                                           or {}).get(tier)))
      for tier, label in (
          ("quick", "An engineer's quick-tier model (small, mechanical, "
                    "well-specified jobs)"),
          ("standard", "An engineer's standard-tier model (most jobs; a brief "
                       "that names no tier)"),
          ("hard", "An engineer's hard-tier model (design-heavy work, subtle "
                   "bugs, wide refactors)"))],

    # -- Settings > Agents: who hears when something in Loam needs
    # attention (2026-11-19, batch B step 5; lib/loam_attention.py) --------
    Entry("system.persona", "setting",
          "Which agent hears when something in Loam needs attention",
          "PATCH /api/system/persona", route="PATCH /v1/system/persona",
          values="an agent's id, or null for the first agent",
          body=lambda v, m: {"persona": v}, read="GET /v1/system/persona",
          pick=lambda a, m: a.get("persona"), options="personas",
          unattended=False,
          note="Loam hands this agent each problem that is no one agent's "
               "(drift, memory pressure, the account refusing runs, a "
               "plugin's event, an update) as the input of a run, told once; "
               "it decides what to do and whether to tell the owner, in "
               "General, in its own words. A persona's own failed run is "
               "still told to that persona at its next turn."),

    # -- Settings > Agents > System: the nightly reviewer (2026-11-13) ------
    *[Entry(f"reviewer.{field}", "setting", label,
            "PATCH /api/system/reviewer", route="PATCH /v1/system/reviewer",
            values=values, unattended=False,
            body=(lambda v, m, field=field: {field: v}),
            read="GET /v1/system/reviewer",
            pick=(lambda a, m, field=field: a.get("schedule") if field == "schedule"
                  else (a.get("effective") or {}).get(field)))
      for field, label, values in (
          ("schedule", "When the nightly reviewer runs",
           f'words, like "daily at 04:20 {lc.ZONE_TOKEN}" (the schedules\' grammar); null for '
           '04:20'),
          ("provider", "The reviewer's provider",
           "claude-code or antigravity, or null to select a connected provider automatically"),
          ("model", "The reviewer's model",
           "a model its provider lists, or null for its default (fable on "
           "Claude Code, gemini-4-hydrogen-high on Antigravity)"),
          ("effort", "The reviewer's thinking level",
           "low, medium, high, xhigh or max, or null"))],

    # -- A plugin's per-agent settings (2026-11-13), shown on the profile --
    Entry("plugin.agent", "setting", "A plugin's settings for one agent "
          "(Read aloud's voice, ...)",
          "PATCH /api/plugins/{plugin}/agents/{agent}",
          route="PATCH /v1/plugins/{plugin}/agents/{agent}", target="plugin/agent",
          values='{"field": value, ...}: the fields the plugin declares '
                 '(GET lists them); null puts one back',
          unattended=False, body=lambda v, m: {"values": v},
          read="GET /v1/plugins/{plugin}/agents/{agent}",
          pick=lambda a, m: a.get("values")),

    # -- Settings: Appearance (2026-11-11), kept on the host ---------------
    # One value for each device kind (the page's primary pointer: coarse is
    # the phone), stored by lib/loam_appearance.py. A persona's set changes
    # it for every browser of that kind; an open page reads it again when it
    # comes back into focus. The theme (2026-11-12) takes the ids of the
    # installed themes, which `describe` lists live from the plugin host's
    # list (options "themes"; lib/loam_plugins.py), so a theme added to
    # either layer needs no line here.
    *[Entry(f"appearance.{kind}.{field}", "setting",
            f"{'The phone' if kind == 'phone' else 'The desktop'}'s "
            + {"mode": "mode: light, dark, by the sun, or the system's",
               "transition": "Sun mode transition: how long the shift "
                             "between light and dark takes",
               "size": "page size (zoom)",
               "theme": "theme (its look: Latte, Plain, ...)"}[field],
            "PATCH /api/settings/appearance",
            route="PATCH /v1/settings/appearance",
            values={"mode": "a mode loam-ui declares (describe lists them: "
                             "light, dark, sun -- light from 30 min before "
                             "sunrise to 30 min after sunset at "
                             "appearance.location -- or system, the device's "
                             "own); default system",
                    "transition": "fast (3 s), normal (10 s) or long (20 s); "
                                  "default normal",
                    "size": "a whole percent, 75 to 150 in steps of 5; default 100",
                    "theme": "an installed theme's id (describe lists them); "
                             "default latte"}[field],
            options={"theme": "themes", "mode": "modes",
                     "transition": "transitions"}.get(field),
            body=(lambda v, m, kind=kind, field=field: {kind: {field: v}}),
            read="GET /v1/settings/appearance",
            pick=(lambda a, m, kind=kind, field=field: a[kind][field]))
      for kind in ("phone", "desktop")
      for field in ("mode", "transition", "size", "theme")],
    # The saved place Sun mode reads (2026-11-13; loam-ui 29a1506), one for
    # both device kinds so the phone and the desktop agree.
    Entry("appearance.location", "setting",
          "The saved place Sun mode times sunrise and sunset by (both devices)",
          "PATCH /api/settings/appearance",
          route="PATCH /v1/settings/appearance",
          values='{"latitude": -90..90, "longitude": -180..180 (both rounded '
                 'to 0.1), "source": "geolocation" or "timezone", "timeZone": '
                 'an IANA zone}, all four; or null to clear it; default null',
          body=lambda v, m: {"location": v},
          read="GET /v1/settings/appearance",
          pick=lambda a, m: a.get("location"),
          note="shared by phone and desktop; with Sun chosen and no place "
               "saved, the page uses its time zone's"),

    # Whose name agents' commits carry (2026-11-18, lib/loam_git_identity.py):
    # Settings › GitHub, "Your name on agents' work". One value.
    Entry("git.identity", "setting",
          "Your name on agents' work: the name and address new commits by "
          "your agents carry, instead of the engineer's or a provider's",
          "PATCH /api/settings/git",
          route="PATCH /v1/settings/git",
          values='null (the default: your one connected GitHub account\'s '
                 'name and private no-reply address; with none, "<project> '
                 'engineer" and a note saying how to set yours), "github" (that '
                 'account, chosen: pull requests on its repositories may then '
                 'be opened as you), {"name": …, "email": …}, or "neutral" '
                 '(always "<project> engineer", no note); default null',
          body=lambda v, m: {"identity": v},
          read="GET /v1/settings/git",
          pick=lambda a, m: a.get("identity"),
          unattended=False,
          note="a project's own person (commit_identity) and a repository's "
               "own identity come first; history is never rewritten"),

    # -- Settings: Plugins (2026-11-12), the plugin host -------------------
    # lib/loam_plugins.py; docs/plugins.md. On and off is one setting per
    # plugin; a theme that is only a theme has none (it is on where
    # appearance.*.theme chose it, so there is one truth), and its PATCH is
    # the broker's 409 saying so. Installing is two calls: the first answers
    # what the plugin declares and a `confirm` token, the second sends the
    # same link with that token. All three happen on the owner's word, in a
    # conversation.
    Entry("plugin.on", "setting", "A plugin turned on or off",
          "PATCH /api/plugins/{plugin}", route="PATCH /v1/plugins/{plugin}",
          target="plugin id", options="plugins", unattended=False,
          values="true or false; a theme is chosen with appearance.*.theme instead",
          body=lambda v, m: {"on": v}, read="GET /v1/plugins/{plugin}",
          pick=lambda a, m: a["plugin"]["on"]),
    Entry("plugin.auto_update", "setting", "Update this Market or link plugin automatically",
          "PATCH /api/plugins/{plugin}", route="PATCH /v1/plugins/{plugin}",
          target="plugin id", options="plugins", unattended=False,
          values='true or false. After local changes, explain that the next update replaces '
                 'edits in the installed folder. On the owner’s yes send '
                 '{"auto_update":true,"replace_changes":true}.',
          body=lambda v, m: v if isinstance(v, dict) and set(v) <= {"auto_update", "replace_changes"}
          and "auto_update" in v else {"auto_update": v},
          read="GET /v1/plugins/{plugin}", pick=lambda a, m: a["plugin"].get("auto_update")),
    Entry("plugin.install", "action", "Install a plugin from a GitHub link",
          "POST /api/plugins", route="POST /v1/plugins", unattended=False,
          values='any GitHub link to it (the repository\'s address or owner/repo '
                 'is enough: the newest release and the plugin\'s folder are '
                 'found): first alone, which answers what it declares, a '
                 '`request` naming exactly that and a `confirm` token (or '
                 '`choose`, the plugins of a repository that holds several, each '
                 'with its own `link`); then {**request, "confirm": <the token>} '
                 'to install',
          body=lambda v, m: dict(v) if isinstance(v, dict) else {"url": v},
          timeout=420,
          note="Nothing is refused for what a linked plugin declares; the "
               f"first answer is what {lc.OWNER_NAME} says yes to."),
    Entry("plugin.remove", "action", "Remove a plugin from your layer",
          "DELETE /api/plugins/{plugin}", route="DELETE /v1/plugins/{plugin}",
          target="plugin id", values="true", unattended=False,
          body=lambda v, m: {},
          note="Only a plugin in your layer; a shipped one is turned off. Its "
               "folder stays in the Yours folder's git history."),
    # A server plugin's restart (2026-11-13; lib/loam_plugin_servers.py): a
    # fresh start of that plugin's own unit and nothing else, after an edit
    # in place or to give a failing one another go. Its state is the row's
    # `service`, read with GET.
    Entry("plugin.restart", "action", "Restart a plugin's server",
          "POST /api/plugins/{plugin}/restart",
          route="POST /v1/plugins/{plugin}/restart",
          target="plugin id", values="true", unattended=False,
          body=lambda v, m: {},
          note="Only that plugin's server restarts; the broker and every "
               "conversation are untouched. Turning it off stops it."),

    # -- Settings: this device -------------------------------------------------
    *[Entry(f"device.{k}", "setting", label, "(browser storage)", reach=DEVICE,
            why=DEVICE_WHY)
      for k, label in (
          ("sidebar", "The desktop list column hidden"),
          ("dictation", "How dictation works, its longest take, its microphone"),
          ("context_warning", "Where a chat's context warning shows"),
          ("chat_bubbles", "Which unread states show a bubble in chat lists"),
          ("speech", "Read aloud: when Listen and Brief are offered, the rate"),
          ("read_aloud", "Files' read-aloud voice"),
          ("files_shortcuts", "Files home's shortcuts and its two folders"))],

    # -- Files ---------------------------------------------------------------
    Entry("files.write", "action", "Edit, create, upload, rename in Files",
          "PUT /api/files/text, POST /api/files/file|folder|upload|rename",
          reach=OWN_TOOLS, why=OWN_TOOLS_WHY),
]

BY_KEY = {e.key: e for e in ENTRIES}


def _option_entry(kind: str, theme: str, opt: dict) -> Entry:
    ids = [c["id"] for c in opt["choices"]]
    return Entry(
        f"appearance.{kind}.options.{theme}.{opt['id']}", "setting",
        f"{'The phone' if kind == 'phone' else 'The desktop'}'s {theme} theme: "
        f"{opt['label']}",
        "PATCH /api/settings/appearance", route="PATCH /v1/settings/appearance",
        values=(", ".join(f"{c['id']} ({c['label']})" for c in opt["choices"])
                + f"; default {opt['default']}"),
        options=f"theme-option:{theme}:{opt['id']}",
        body=(lambda v, m, kind=kind, theme=theme, o=opt["id"]:
              {kind: {"options": {theme: {o: v}}}}),
        read="GET /v1/settings/appearance",
        pick=(lambda a, m, kind=kind, theme=theme, o=opt["id"]:
              ((a.get(kind) or {}).get("options") or {}).get(theme, {}).get(o)),
        note=f"kept per theme: choosing another theme keeps it ({len(ids)} choices "
             f"from the theme's manifest)")


def option_entries() -> list[Entry]:
    """Appearance's theme options (2026-11-12): one setting per device kind,
    installed theme and option, ``appearance.<kind>.options.<theme>.<option>``,
    read live from the themes' manifests (lib/loam_plugins.py,
    ``theme_options``), so a theme installed with options is settable with
    no line here. Unreadable theme folders give none."""
    try:
        import loam_plugins  # noqa: PLC0415
        cat = loam_plugins.theme_options()
    except Exception:  # noqa: BLE001 -- the table never fails for a theme folder
        return []
    return [_option_entry(kind, theme, opt) for kind in ("phone", "desktop")
            for theme, opts in cat.items() for opt in opts]


def plugin_entries() -> list[Entry]:
    """What plugins that are on contribute (`settings.entry`, 2026-11-14,
    first release L4): ``plugin.<id>.<key>``, read and set through
    ``/v1/plugins/<id>/settings``. Read live, so a plugin turned on is
    settable with no line here, and one turned off is not."""
    try:
        import loam_contributions  # noqa: PLC0415
        active = loam_contributions.active()
    except Exception:  # noqa: BLE001 -- the table never fails for a plugin
        return []
    out = []
    for p in active:
        pid = p["id"]
        for e in p["contributes"].get("settings.entry") or []:
            out.append(Entry(
                f"plugin.{pid}.{e['key']}", "setting", e["label"],
                f"PATCH /api/plugins/{pid}/settings",
                route=f"PATCH /v1/plugins/{pid}/settings",
                values=(", ".join(e["choices"]) if e.get("choices") else "text")
                       + (f"; default {e['default']}" if e.get("default") is not None else ""),
                body=lambda v, m, key=e["key"]: {"values": {key: v}},
                read=f"GET /v1/plugins/{pid}/settings",
                pick=lambda a, m, key=e["key"]: (a.get("values") or {}).get(key),
                note=f"contributed by the plugin {pid}"))
    return out


def entries() -> list[Entry]:
    """Every entry: the table above, the live theme options, and what the
    plugins that are on contribute."""
    return ENTRIES + option_entries() + plugin_entries()


# Every route family the page's relay passes (loam-ui src/server.js, the
# `api()` allowlist), exactly as written there, and what it carries. A list
# is the entries above; a string is why it carries no setting.
PAGE_RELAY: dict[str, list[str] | str] = {
    "/^\\/threads(?:\\/([^/]+)(?:\\/(messages|events|board|attachments|steps))?)?$/": [
        "thread.title", "thread.icon", "thread.default", "thread.members.add",
        "thread.members.remove", "thread.engine", "thread.model",
        "thread.pinned", "thread.archived", "thread.seen", "thread.board",
        "thread.create", "thread.delete"],
    "rest === '/threads/events'": "a stream",
    "rest === '/threads/imported'": "a read: which thread a classic chat was "
                                    "imported as (2026-11-11)",
    "/^\\/threads\\/([^/]+)\\/branch$/": "branching a conversation into a new "
                                         "thread (2026-11-11): starting a "
                                         "conversation, not a setting",
    # GET and DELETE of one session are Running now's; its PATCH (a classic
    # chat's name, pin, archive, read mark, model, effort) is retired.
    "/^\\/sessions\\/([^/]+)$/": ["session.end"],
    "rest === '/sessions/end-idle'": ["sessions.end_idle"],
    # The classic chat's own routes (a session's events, messages,
    # attachments, compact and permission answers, and /sessions/seen) left
    # the page's relay at loam-ui d132dc3 (2026-11-12), as did its POST of
    # /sessions; their lines went with them. Permission answers came back at
    # loam-ui 2537b6e the same day, for a thread's permission cards.
    "/^\\/sessions\\/([^/]+)\\/permissions\\/([^/]+)$/":
        "answering a permission card in a thread (POST only; loam-ui "
        "2537b6e, 2026-11-12): a person's answer, not a setting",
    "rest === '/sessions'": "a list (Running now's fallback reads it)",
    "rest === '/sessions/events'": "a stream",
    "rest === '/sessions/live'": "a read",
    "rest === '/sessions/live/events'": "a stream",
    "/^\\/schedules\\/([^/]+)(?:\\/([^/]+)(?:\\/(run|seen))?)?$/": [
        "schedule.enabled", "schedule.schedule", "schedule.at", "schedule.note",
        "schedule.body", "schedule.create", "schedule.delete", "schedule.run",
        "schedule.seen"],
    "rest === '/schedules'": "a read",
    "rest === '/activity'": "a read",
    # "rest === '/activity/seen'" left the relay at loam-ui d132dc3: the page
    # no longer marks Activity seen; activity.seen stays, as the broker's
    # route does, for a persona's call.
    "/^\\/activity\\/([^/]+)\\/undo$/": ["activity.undo"],
    "/^\\/jobs\\/([^/]+)\\/cancel$/": ["job.cancel"],
    "rest === '/providers'": "a read",
    "/^\\/providers\\/([^/]+)\\/login$/": ["provider.login"],
    "/^\\/providers\\/([^/]+)\\/login\\/code$/": ["provider.login"],
    "/^\\/providers\\/([^/]+)\\/logout$/": ["provider.login"],
    "rest === '/integrations/github'": ["integration.github"],
    "/^\\/integrations\\/github\\/(manifest|register|install|callback|refresh)$/":
        ["integration.github"],
    "rest === '/settings/git'": ["git.identity"],
    "rest === '/settings/appearance'": [
        "appearance.phone.mode", "appearance.phone.transition",
        "appearance.phone.size", "appearance.phone.theme",
        "appearance.desktop.mode", "appearance.desktop.transition",
        "appearance.desktop.size", "appearance.desktop.theme",
        "appearance.location"],
    # Plugins (2026-11-12; docs/plugins.md), passed by loam-ui's relay
    # since its plugin host (moved up from PAGE_RELAY_AHEAD).
    "rest === '/plugin-updates'": "read installed plugin update status",
    "rest === '/plugins'": ["plugin.install"],
    "/^\\/plugins\\/([^/]+)$/": ["plugin.on", "plugin.auto_update", "plugin.remove"],
    "/^\\/plugins\\/([^/]+)\\/files\\/(.+)$/":
        "a read: a plugin's page/ and assets/ files, theme.css and PLUGIN.md",
    "rest === '/apps'": "a read: the Apps section's list, GET /v1/apps "
                        "(2026-11-12; broker/README.md, \"Apps\")",
    "rest === '/apps/icon'": "a read: an app's site icon for the Apps "
                             "launcher (loam-ui 58d593f5, 2026-11-15)",
    "rest === '/apps/media'": "a read: every app's icon and cached page "
                              "preview for the Apps launcher, and one preview "
                              "image (loam-ui 11535026, 2026-11-15)",
    "rest === '/agents'": "a read",
    "rest === '/health'": "a read",
    "rest === '/agent-availability'": "a read",
    "rest === '/limits'": "a read",
    "/^\\/files\\/(roots|list|stat|read|resolve)$/": "a read",
    "rest === '/files/config'": "a read",
    "rest === '/files/link'": "a read",
    "rest === '/files/live'": "a read",
    "rest === '/files/preview'": "a read",
    "rest === '/files/download'": "a read",
    "rest === '/files/archive'": ("a read: a Vault or AI Spaces folder as one zip, "
                                  "the signed-in owner's only (loam-ui, 2026-11-18)"),
    "rest === '/files/text'": ["files.write"],
    "rest === '/files/upload'": ["files.write"],
    "/^\\/files\\/(file|folder)$/": ["files.write"],
    # Moved up from PAGE_RELAY_AHEAD on 2026-11-14, once loam-ui's relay
    # carried them (loam-ui 8e8f98a and earlier). The relay spells the
    # account route's refresh group as a capturing one, `(\/refresh)?`.
    "rest === '/files/rename'": ["files.write"],
    "rest === '/system/persona'": ["system.persona"],
    "rest === '/system/reviewer'": ["reviewer.schedule", "reviewer.provider",
                                    "reviewer.model", "reviewer.effort"],
    "rest === '/integrations/github/accounts'": ["integration.github"],
    "/^\\/integrations\\/github\\/accounts\\/([^/]+)(\\/refresh)?$/":
        ["integration.github"],
    "/^\\/plugins\\/([^/]+)\\/restart$/": ["plugin.restart"],
}


# Routes the broker serves for the page before its relay passes them, as the
# relay is expected to spell them. Classified like PAGE_RELAY, so the day the
# relay gains one the suite still passes; and not required to be in the relay,
# so the broker can land first. Once the relay carries one, move its line up
# into PAGE_RELAY (tests/test_settings.py prints a NOTE until it is moved).
PAGE_RELAY_AHEAD: dict[str, list[str] | str] = {
    # Settings > Agents (2026-11-13; broker/README.md, "Agent profiles,
    # engineers and the reviewer"). `/agents` itself is in PAGE_RELAY.
    "/^\\/agents\\/([^/]+)$/": ["agent.display", "agent.provider",
                              "agent.model", "agent.effort"],
    # Making, archiving, restoring and the face (2026-11-14, first release L10).
    "rest === '/agents/new'": "New agent: opens a thread with the starter agent "
                              "(the person's own click; an agent drafts in its thread)",
    "rest === '/agents/drafts'": ["agent.draft"],
    "/^\\/agents\\/drafts\\/([^/]+)$/": ["agent.revise"],
    "/^\\/agents\\/drafts\\/([^/]+)\\/confirm$/": ["agent.confirm"],
    "/^\\/agents\\/([^/]+)\\/(archive|restore)$/": ["agent.archive", "agent.restore"],
    "/^\\/agents\\/([^/]+)\\/face$/": ["agent.face"],
    "rest === '/engineers'": "a read: every engineer and the default",
    "/^\\/engineers\\/([^/]+)$/": ["engineer.provider", "engineer.model",
                                   "engineer.effort", "engineer.deploy_on_land"],
    "/^\\/plugins\\/([^/]+)\\/agents(?:\\/([^/]+))?$/": ["plugin.agent"],
    # A plugin's own settings (2026-11-14, first release L4).
    "/^\\/plugins\\/([^/]+)\\/settings$/": "a plugin's contributed settings (plugin.<id>.<key>)",
    # A plugin's keys (2026-11-14, first release L9). Never the value route,
    # `/plugins/<id>/secrets/<name>/value`: the app's server asks the broker
    # for that itself, and the relay must not pass it to a browser.
    "/^\\/plugins\\/([^/]+)\\/secrets(?:\\/([^/]+))?$/": ["plugin.secret"],
    "/^\\/plugins\\/([^/]+)\\/secrets\\/([^/]+)\\/check$/": ["plugin.secret"],
    # The owner (2026-11-14, first release L2): who this install is for.
    "rest === '/owner'": "a read: the owner's id, the ids accepted as theirs, the name",
    # Settings › Account and About Loam (2026-11-18): answered by the app
    # itself, not relayed; nothing to change.
    "rest === '/account'": "a read: who is signed in, from the login's own headers",
    "rest === '/server'": "a read: the machine Loam runs on (processors, memory, storage)",
    # Loam's update, as the page shows it (loam-ui 2d2eace, 2026-11-14). The
    # page also relays POST /update/check, /apply and /rollback, from an
    # `includes([...])` list `relay_routes` does not read; they are the
    # owner's buttons on lib/loam_update.py, not agent settings.
    "rest === '/update'": "a read: the installed release, the newest, and whether an update is running",
    "rest === '/update/progress'": "a read: the running update's steps",
    # The marketplace's list (2026-11-17, lib/loam_marketplace.py). Installing
    # from it is `rest === '/plugins'`, plugin.install, by the entry's link.
    "rest === '/marketplace'": "a read: the marketplace's list, each entry marked installed or not",
    # A listing's own page (2026-11-18, Settings › Market): its declaration,
    # icon, cover and theme, read from its link before anything is installed.
    # Its screenshots and a theme's fonts came on 2026-11-19.
    "/^\\/marketplace\\/([a-z][a-z0-9-]*)(?:\\/(icon|image|cover|cover-dark|theme\\.css|fonts\\.css|shot\\/[0-5](?:-dark)?|font\\/[A-Za-z0-9_-]+\\.woff2?|readme\\/(?:[A-Za-z0-9_-]+\\/)*[A-Za-z0-9_-]+\\.(?:webp|avif|jpe?g|png)))?$/":
        "a read: one listing's declaration, README, icon, covers, screenshots, theme and its fonts, before it is installed",
    # Shared keys (2026-11-18): Settings › Connections › API keys. Never a value.
    "/^\\/keys(?:\\/([^/]+))?$/": ["keys.set", "keys.forget"],
    "/^\\/keys\\/([^/]+)\\/check$/": ["keys.check"],
    "/^\\/keys\\/([^/]+)\\/plugins\\/([^/]+)$/": ["keys.own", "keys.shared"],
    "/^\\/keys\\/hetzner\\/projects\\/([^/]+)$/": ["keys.project.set", "keys.project.forget"],
}


def classified(route: str) -> bool:
    """Whether the registry accounts for a route family the relay passes."""
    return route in PAGE_RELAY or route in PAGE_RELAY_AHEAD


def relay_routes(server_js: str) -> list[str]:
    """The route families of the page's relay, as PAGE_RELAY spells them:
    every `rest === '...'` and every `/^\\/.../` regex literal inside
    loam-ui's ``api()`` function."""
    start = server_js.find("async function api(req, res, url, rest)")
    if start < 0:
        raise ValueError("no `async function api(req, res, url, rest)` in "
                         "server.js: the relay moved, and this reader with it")
    end = server_js.find("\n  }\n", start)
    body = server_js[start:end if end > 0 else None]
    found = re.findall(r"rest === '[^']+'", body)
    found += re.findall(r"/\^\\/.*?\$/(?=\.exec)", body)
    return sorted(set(found))


def table() -> list[dict]:
    """The registry as data, for ``GET /v1/settings`` and ``describe``."""
    out = []
    for e in entries():
        row = {"key": e.key, "kind": e.kind, "label": e.label, "page": e.page,
               "reach": e.reach}
        for name in ("route", "target", "values", "why", "note"):
            if getattr(e, name):
                row[name] = getattr(e, name)
        if e.member:
            row["member"] = True
        if e.read:
            row["get"] = True
        if not e.unattended:
            row["unattended"] = False
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# The calls
# ---------------------------------------------------------------------------


class SettingsError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.message = message
        self.status = status


def _base() -> str:
    return (os.environ.get("LOAM_BROKER_URL")
            or f"http://{cfg.BIND_ADDR}:{cfg.PORT}").rstrip("/")


def _token() -> str:
    path = Path(os.environ.get("LOAM_BROKER_TOKEN_FILE") or cfg.TOKEN_PATH)
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SettingsError(f"cannot read the broker's token: {exc.strerror}") from None


def call(method: str, path: str, body: dict | None = None, timeout: int = 60) -> dict:
    """One call to the broker, as loam-ui's relay makes it. A refusal is the
    broker's own sentence."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        _base() + path, method=method, data=data,
        headers={"Authorization": f"Bearer {_token()}",
                 "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        try:
            said = json.loads(exc.read()).get("error")
        except (ValueError, AttributeError):
            said = None
        raise SettingsError(said or f"the broker answered {exc.code}", exc.code) from None
    except (urllib.error.URLError, OSError) as exc:
        raise SettingsError(f"could not reach the broker: {exc}") from None
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        raise SettingsError("the broker answered something that is not JSON") from None


def _fill(route: str, target: str) -> tuple[str, str]:
    """``"PATCH /v1/threads/{thread}"`` and a target into a method and a path."""
    method, _, path = route.partition(" ")
    names = re.findall(r"\{(\w+)\}", path)
    parts = [p for p in (target or "").strip().strip("/").split("/") if p] \
        if len(names) > 1 else ([target.strip()] if target and target.strip() else [])
    if len(parts) != len(names):
        want = "/".join(f"<{n}>" for n in names)
        raise SettingsError(f"`target` is {want}" if names else
                            "this one takes no `target`")
    for name, part in zip(names, parts):
        path = path.replace("{" + name + "}", urllib.parse.quote(part, safe=""), 1)
    return method, path


def entry(key: str) -> Entry:
    key = (key or "").strip()
    found = BY_KEY.get(key)
    live = None
    if found is None and key.startswith("appearance.") and ".options." in key:
        live = {e.key: e for e in option_entries()}
        found = live.get(key)
    if found is None and key.startswith("plugin.") and key.count(".") >= 2:
        # A plugin's contributed setting (2026-11-14), read live like the
        # theme options: on while its plugin is on.
        live = {e.key: e for e in plugin_entries()}
        found = live.get(key)
    if found is None:
        near = [k for k in {**BY_KEY, **(live or {})} if k.split(".")[0] == (key or "").split(".")[0]]
        raise SettingsError(
            f"no setting {key!r}" + (f"; this scope has {', '.join(near)}" if near
                                     else "; `describe` lists them"))
    return found


def _roster() -> list[str]:
    import loam_threads  # noqa: PLC0415 - LOAM_AI_LABS-aware, as the broker's
    return loam_threads.roster()


def unattended_lane() -> str | None:
    """The lane when nobody is in the room: LOAM_WAKE set to anything but a
    conversation or an engineer job (lib/loam_engineer.unattended)."""
    lane = (os.environ.get("LOAM_WAKE") or "").strip().lower()
    if lane and lane != "conversation" and not lane.startswith("engineer:"):
        return lane
    return None


def _refuse_reach(e: Entry) -> None:
    if e.reach == PERSONA and e.route:
        return
    if e.reach == PERSONA:
        raise SettingsError(f"{e.key}: {e.why}")
    who = {OWNER: f"{lc.OWNER_NAME}'s hands only", DEVICE: "not on the host",
           OWN_TOOLS: "not through here"}[e.reach]
    raise SettingsError(f"{e.key} is {who}: {e.why}.")


def get(key: str, target: str = "", member: str = "") -> dict:
    e = entry(key)
    if not e.read:
        if e.reach != PERSONA:
            _refuse_reach(e)
        raise SettingsError(f"{key} is an action; it has no value to read")
    if e.member and not member:
        raise SettingsError(f"{key} needs `member`: which member of the thread")
    method, path = _fill(e.read, target)
    answer = call(method, path)
    return {"ok": True, "setting": key, "target": target or None,
            **({"member": member} if member else {}),
            "value": e.pick(answer, member)}


def parse_value(raw: Any) -> Any:
    """Decode strings containing JSON. Pass other strings and already typed values through."""
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def change(key: str, value: Any, by: str, target: str = "", member: str = "",
           reason: str = "") -> dict:
    """``set`` and ``do``: the entry's broker route with the entry's body and
    ``by``. Refused before any call for reach that is not a persona's, for an
    action nobody is in the room for, and for a missing author."""
    e = entry(key)
    _refuse_reach(e)
    who = (by or "").strip().lower()
    if not who or lc.is_owner(who):
        raise SettingsError("who is changing it? pass `by` (your own name)")
    if who not in _roster():
        raise SettingsError(f"`by`: no persona named {by!r} on this box")
    lane = unattended_lane()
    if lane and not e.unattended:
        raise SettingsError(
            f"{key} is done only in a conversation, on {lc.OWNER_NAME}'s word; this "
            f"is an unattended run ({lane})")
    if e.member and not member:
        raise SettingsError(f"{key} needs `member`: which member of the thread")
    value = parse_value(value)
    method, path = _fill(e.route, target)
    payload = e.body(value, member)
    payload["by"] = who
    if key.startswith("schedule.") or key == "activity.undo":
        payload["lane"] = (lane.split(":")[0] if lane else "conversation")
        if reason:
            payload["reason"] = reason
    answer = call(method, path, payload, timeout=e.timeout)
    out = {"ok": True, "setting": key, "target": target or None, "by": who}
    if member:
        out["member"] = member
    if e.read:
        try:
            out["value"] = get(key, target, member)["value"]
        except SettingsError as exc:
            out["value_unread"] = exc.message
    out["answer"] = _brief(answer)
    return out


# What a tool result keeps whole: a schedule's change, and a plugin's
# declaration, source and row, which are what a persona's yes is asked for.
KEEP = ("last", "board_new", "event", "schedule", "declaration", "added",
        "replaces", "replaced", "source", "shadows", "warnings", "plugin",
        "was", "appearance_reset", "kind", "places",
        # A draft is what the person's yes is asked for (2026-11-14).
        "problems", "revisions", "agent", "face", "archived", "drafts")


def _brief(answer: dict) -> dict:
    """The route's answer without the bulk (a thread's members' background
    task lists, a whole board) a tool result does not need."""
    keep = {}
    for k, v in (answer or {}).items():
        if k in ("members",) and isinstance(v, list):
            keep[k] = [{x: r.get(x) for x in ("persona", "engine", "model")}
                       for r in v if isinstance(r, dict)]
        elif isinstance(v, str) and len(v) > 400:
            keep[k] = v[:400] + "..."
        elif k in KEEP or not isinstance(v, (list, dict)):
            keep[k] = v
    return keep


def options(name: str, member_engine: str | None = None) -> list:
    """A named list of allowed values, read live: personas from the roster,
    engines from the threads module, themes and switchable plugins from the
    plugin host's list (lib/loam_plugins.py),
    models from GET /v1/providers."""
    if name == "personas":
        return _roster()
    if name == "themes":
        import loam_appearance  # noqa: PLC0415
        return loam_appearance.themes()
    if name == "modes":
        import loam_appearance  # noqa: PLC0415
        return list(loam_appearance.modes())
    if name == "transitions":
        import loam_appearance  # noqa: PLC0415
        return list(loam_appearance.TRANSITIONS)
    if name.startswith("theme-option:"):
        import loam_appearance  # noqa: PLC0415
        _, theme, opt = name.split(":", 2)
        return list(((loam_appearance.catalog().get(theme) or {}).get(opt) or {})
                    .get("choices") or [])
    if name == "plugins":
        import loam_plugins  # noqa: PLC0415
        return sorted({r["id"] for r in loam_plugins.listing()["plugins"]
                       if r.get("effective") and r.get("switch") == "plugin"})
    if name == "providers":
        import loam_keys  # noqa: PLC0415
        return [p["id"] for p in loam_keys.listing()["providers"]]
    if name == "engines":
        import loam_threads  # noqa: PLC0415
        return list(loam_threads.ENGINES)
    rows = call("GET", "/v1/providers").get("providers") or []
    if name == "models":
        return {r["id"]: [m.get("id") for m in r.get("models") or []]
                for r in rows if r.get("connected")}
    return []


def describe(key: str = "", target: str = "", member: str = "") -> dict:
    """Every entry, or one with its current value and live allowed values."""
    if not key:
        return {"ok": True, "settings": table(),
                "how": "get/set a `setting` with `target` (and `member` for a "
                       "thread member's engine or model); `do` an action. "
                       "reach `persona` is yours; the rest say why not."}
    e = entry(key)
    row = next(r for r in table() if r["key"] == e.key)
    if e.options:
        try:
            row["allowed"] = options(e.options)
        except SettingsError as exc:
            row["allowed_unread"] = exc.message
    if e.read and target and (member or not e.member):
        try:
            row["value"] = get(key, target, member)["value"]
        except SettingsError as exc:
            row["value_unread"] = exc.message
    return {"ok": True, "setting": row}
