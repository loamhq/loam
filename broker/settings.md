# Settings: every one the owner can change, a persona can change (2026-11-10)

The owner, 2026-11-10 20:40 PT: "any setting that we have as part of Loam UI, I
want the personas to be able to do that as well … If I can do it, I would
want to be able to just tell my persona to go and do it … and in the future,
we always build in the capability for personas to be able to do the
settings changes that a human is going to be able to do."

## The mechanism

One registry, `lib/loam_settings.py`, and one way through it:

- **`ENTRIES`** — one per thing the page can change: its key
  (`thread.icon`), the page route (`PATCH /api/threads/{thread}`), the
  broker route the page's relay sends it to (`PATCH /v1/threads/{thread}`),
  the body that route takes, the GET its current value is read back from,
  its allowed values, and its **reach**: `persona`, `owner` (the owner's
  hands only), `device` (kept in the browser, so on no host route at all) or
  `own-tools` (a file write a persona already makes with its own tools).
- **`bin/settings`** (`describe`, `get`, `set`, `do`) calls the entry's
  broker route with the broker's token, exactly as loam-ui's relay does, and
  adds `by: <persona>`. **`mcp__engineer__settings`** is the same command as
  a tool; like every engineer tool it decides nothing and hands off to the
  CLI, so a conversation opened before a registry change gets the new entry.
- **`by`** is the broker's one addition (`author()` in `bin/session-broker`).
  Absent, empty or `owner` is the owner — every write the page sends. A
  persona's name makes the write that persona's: a thread gets a system line
  naming it for every change (the default and a member brought in say
  "Ada made Iris the default" / "Ada brought in Iris" where the page's own
  change says "<owner> …"; a rename, an icon, a member taken out, an engine,
  a model, a pin or an archive say themselves too, which the owner's own clicks
  do not, because they know what they clicked); a schedule change is logged as
  the persona's on the lane it ran in, held by `loam_schedule.validate()` to
  its own records and a reason; a session's end, an end-idle, a
  board write and a delete are logged with the name in the broker's journal.
  It is attribution, not authority: the token is what lets a caller in, and
  every holder of the token is a process on this box.
- **Unattended lanes** (a wake or a cron; `LOAM_WAKE` set to anything but a
  conversation or an engineer job) may not delete a thread, end a session,
  end the idle ones, delete a schedule, or run one now; the CLI refuses them
  before any call, and the schedule route refuses a persona's Run now from
  any lane but `conversation` (Boundary 3 bends only where the owner is).
- **`GET /v1/settings`** answers the table (keys, words, routes, reach; no
  values), so the page can draw from the same source once its relay passes
  the route. It does not yet; that is a loam-ui change.

**The future is enforced.** `PAGE_RELAY` in the registry names every route
family of loam-ui's relay (`src/server.js`, the `api()` allowlist) and what
it carries. `tests/test_settings.py` reads the relay from the loam-ui
checkout (`LOAM_UI_SOURCE`, default `~/projects/loam-ui`) and
fails when the relay has a route the registry does not classify, or the
registry names one the relay lost — so the day the page gains a setting is
the day this repo's suite asks for its entry. The same test fails when a
registry key is missing from the table below.

## The inventory

Read from loam-ui at `57a0275` (`public/`, `src/server.js`) on 2026-11-10.
"Before" is what a persona could already do that day without this.

A classic chat's name, pin, archive, read mark, model and effort, and
"mark all read" (`session.title`, `.pinned`, `.archived`, `.unread`,
`.model`, `.effort`, `sessions.seen`) retired with the classic chat view on
2026-11-12: the broker no longer answers `PATCH /v1/sessions/<id>` or
`POST /v1/sessions/seen`, and a classic chat is now an archived thread whose
own `thread.*` rows cover the same things.

| Key | What | Page route | Reach | Before |
|---|---|---|---|---|
| `thread.title` | A thread's title | PATCH /api/threads/{id} | persona | yes, `thread` tool `rename` (no line) |
| `thread.icon` | A thread's icon (emoji or none) | PATCH /api/threads/{id} | persona | no |
| `thread.default` | Who answers untagged messages | PATCH /api/threads/{id} | persona | no |
| `thread.members` | The members (read) | GET /api/threads/{id} | persona (read) | by `thread list` |
| `thread.jobs` | The engineer jobs briefed from it: queued, running, landing, finished in the last 10 min (read; 2026-11-13) | GET /api/threads/{id} | persona (read) | by `status` (every job, not by thread) |
| `thread.members.add` | Bring a persona in | PATCH /api/threads/{id} | persona | only by posting (self) |
| `thread.members.remove` | Take a member out | PATCH /api/threads/{id} | persona | no |
| `thread.engine` | A member's harness in a thread | PATCH /api/threads/{id} | persona | no |
| `thread.model` | A member's model in a thread | PATCH /api/threads/{id} | persona | no |
| `thread.pinned` | Pinned | PATCH /api/threads/{id} | persona | no |
| `thread.archived` | Archived | PATCH /api/threads/{id} | persona | no |
| `thread.seen` | Marked read (the page: opening it) | GET /api/threads/{id} | persona | no |
| `thread.board` | The board's markdown | PUT /api/threads/{id}/board | persona | yes, as a file |
| `thread.create` | Start a thread | POST /api/threads | persona | no |
| `thread.delete` | Delete a thread and its board | DELETE /api/threads/{id} | persona, conversation only | no |
| `session.end` | Running now: End | DELETE /api/sessions/{id} | persona, conversation only | no |
| `sessions.end_idle` | Running now: End idle | POST /api/sessions/end-idle | persona, conversation only | no |
| `schedule.enabled` | On or paused | PATCH /api/schedules/{agent}/{name} | persona, own, with a reason | yes, editing its own record file |
| `schedule.schedule` | When it recurs | PATCH /api/schedules/{agent}/{name} | persona, own, with a reason | the same |
| `schedule.at` | When a one-off runs | PATCH /api/schedules/{agent}/{name} | persona, own, with a reason | the same |
| `schedule.note` | A one-off's note | PATCH /api/schedules/{agent}/{name} | persona, own, with a reason | the same |
| `schedule.body` | The instruction | PATCH /api/schedules/{agent}/{name} | persona, own, with a reason | the same |
| `schedule.create` | Make one | POST /api/schedules/{agent} | persona, own, with a reason | the same |
| `schedule.delete` | Delete one | DELETE /api/schedules/{agent}/{name} | persona, own, conversation only | the same |
| `schedule.run` | Run now | POST /api/schedules/{agent}/{name}/run | persona, own, conversation only | no (the platform engineer, on a brief) |
| `schedule.seen` | A schedule's activity seen | POST /api/schedules/{agent}/{name}/seen | persona | no |
| `activity.seen` | Activity marked seen (the page's relay dropped this route at loam-ui `d132dc3`; the broker's stays) | POST /api/activity/seen | persona | no |
| `activity.undo` | Activity: Undo a schedule change | POST /api/activity/{id}/undo | persona, own records | no |
| `job.cancel` | Cancel an engineer job | POST /api/jobs/{id}/cancel | persona, by `mcp__engineer__cancel` | yes, that tool |
| `provider.login` | Sign a provider in again, or out | POST /api/providers/{p}/login (+ /code, DELETE), POST /api/providers/{p}/logout | owner | — |
| `integration.github` | Connect / disconnect GitHub | /api/integrations/github/… | owner | — |
| `plugin.secret` | A plugin's API key (read aloud, dictation), pasted and checked on entry (2026-11-14) | PUT/DELETE /api/plugins/{plugin}/secrets/{name} | owner | — |
| `agent.display` | An agent's display name; its id never changes (2026-11-13) | PATCH /api/agents/{agent} | persona, conversation only | no |
| `agent.emoji` | An agent's face, its emoji | PATCH /api/agents/{agent} | persona, conversation only | no |
| `agent.provider` | An agent's default provider: new threads, members brought in; wakes and crons only on Claude Code | PATCH /api/agents/{agent} | persona, conversation only | no (was `persona.provider`, the owner's hands) |
| `agent.model` | An agent's default model, on its provider | PATCH /api/agents/{agent} | persona, conversation only | no |
| `agent.effort` | An agent's default thinking level, on its provider | PATCH /api/agents/{agent} | persona, conversation only | no |
| `agent.face` | An agent's face, an uploaded image (PNG, JPEG, WebP, GIF; 2 MB) over its emoji and any theme's (2026-11-14) | PUT /api/agents/{agent}/face | persona, conversation only | no |
| `agent.draft` | Draft a new agent (name, face, soul, config) in a thread, to show the person (2026-11-14) | POST /api/agents/drafts | persona, conversation only | no |
| `agent.revise` | Change a draft on the person's word; each change is a revision | PATCH /api/agents/drafts/{draft} | persona, conversation only | no |
| `agent.confirm` | Make the agent, on the person's yes to the revision shown (their message's id) | POST /api/agents/drafts/{draft}/confirm | persona, conversation only | no |
| `agent.archive` | Archive an agent, never delete: out of Chats and pickers, wakes and crons stopped | POST /api/agents/{agent}/archive | persona, conversation only, on the person's yes | no |
| `agent.restore` | Restore an archived agent as it was | POST /api/agents/{agent}/restore | persona, conversation only, on the person's yes | no |
| `engineer.provider` | An engineer's provider (target a project, or `default` for every other) | PATCH /api/engineers/{engineer} | persona, conversation only | no |
| `engineer.model` | An engineer's model | PATCH /api/engineers/{engineer} | persona, conversation only | no |
| `engineer.effort` | An engineer's thinking level | PATCH /api/engineers/{engineer} | persona, conversation only | no |
| `engineer.deploy_on_land` | Deploy when a job lands, for a project with an app | PATCH /api/engineers/{engineer} | persona, conversation only | no (per brief only) |
| `engineer.tiers.quick` | An engineer's quick-tier model: small, mechanical, well-specified jobs (2026-11-15); unset runs on `engineer.model` | PATCH /api/engineers/{engineer} | persona, conversation only | no |
| `engineer.tiers.standard` | An engineer's standard-tier model: most jobs, and a brief that names no tier | PATCH /api/engineers/{engineer} | persona, conversation only | no |
| `engineer.tiers.hard` | An engineer's hard-tier model: design-heavy work, subtle bugs, wide refactors | PATCH /api/engineers/{engineer} | persona, conversation only | no |
| `reviewer.schedule` | When the nightly reviewer runs | PATCH /api/system/reviewer | persona, conversation only | no |
| `reviewer.provider` | The reviewer's provider (Claude Code or Antigravity) | PATCH /api/system/reviewer | persona, conversation only | no |
| `reviewer.model` | The reviewer's model | PATCH /api/system/reviewer | persona, conversation only | no |
| `reviewer.effort` | The reviewer's thinking level | PATCH /api/system/reviewer | persona, conversation only | no |
| `plugin.agent` | A plugin's settings for one agent (Read aloud's voice, once loam-ui reads it) | PATCH /api/plugins/{plugin}/agents/{agent} | persona, conversation only | no |
| `appearance.phone.mode` | The phone's mode: light, dark, sun or system (the modes loam-ui's handoff declares) | PATCH /api/settings/appearance | persona | no (was `device.theme`, in the browser) |
| `appearance.phone.size` | The phone's page size, 75–150 % in 5s | PATCH /api/settings/appearance | persona | no |
| `appearance.desktop.mode` | The desktop's mode: light, dark, sun or system (the modes loam-ui's handoff declares) | PATCH /api/settings/appearance | persona | no (was `device.theme`) |
| `appearance.desktop.size` | The desktop's page size, 75–150 % in 5s | PATCH /api/settings/appearance | persona | no |
| `appearance.phone.theme` | The phone's theme (Latte, Plain, any installed) | PATCH /api/settings/appearance | persona | no |
| `appearance.desktop.theme` | The desktop's theme (Latte, Plain, any installed) | PATCH /api/settings/appearance | persona | no |
| `appearance.location` | The saved place Sun mode times sunrise and sunset by, one for both kinds: `{latitude, longitude, source, timeZone}` rounded to 0.1°, or null | PATCH /api/settings/appearance | persona | no |
| `appearance.{phone,desktop}.options.<theme>.<option>` | A theme's option on that device kind, one key per installed theme option, read live from the manifests (on 2026-11-12 Latte's four: `agent-colours`, `agent-shapes`, `latte-mug`, `latte-inside`) | PATCH /api/settings/appearance | persona | no |
| `plugin.on` | A plugin on or off (not a theme-only plugin: that is `appearance.*.theme`) | PATCH /api/plugins/{id} | persona, conversation only | no |
| `plugin.install` | Install from a GitHub link: a preview, then the same link with its `confirm` | POST /api/plugins | persona, conversation only | no |
| `plugin.remove` | Remove a plugin from the Yours layer | DELETE /api/plugins/{id} | persona, conversation only | no |
| `plugin.restart` | Restart a server plugin's own unit (2026-11-13) | POST /api/plugins/{id}/restart | persona, conversation only | no |
| `device.sidebar` | Desktop list column hidden | browser storage | device | — |
| `device.dictation` | Dictation mode, longest take, microphone | browser storage | device | — |
| `device.context_warning` | Where a chat's context warning shows | browser storage | device | — |
| `device.chat_bubbles` | Which unread states show a bubble | browser storage | device | — |
| `device.speech` | Read aloud: Listen/Brief thresholds, rate | browser storage | device | — |
| `device.read_aloud` | Files' read-aloud voice | browser storage | device | — |
| `device.files_shortcuts` | Files home's shortcuts and folders | browser storage | device | — |
| `files.write` | Files: edit, new file/folder, upload, rename or move | PUT /api/files/text, POST /api/files/file, folder, upload, rename | own-tools | yes, its own file tools |

## Appearance: the one setting kept on the host for nobody but the owner (2026-11-11)

Settings → Appearance (loam-ui `c674ecb`, its contract in loam-ui's
`docs/appearance.md`): a mode, a size and (since `c326299`) a theme for each device kind, `phone`
(a coarse primary pointer) and `desktop`. It replaced `device.theme`, which
lived in each browser; the page discards that old key. The broker keeps it in
`state/broker/settings.json` under `appearance` (`lib/loam_appearance.py`),
the broker's settings file for anything that belongs to no thread, session or
schedule; the next such setting is a key beside it. `GET /v1/settings/appearance`
fills defaults (`system`, 100, `latte`); `PATCH` merges named fields atomically and
answers the whole value; a malformed patch is a 400 sentence and writes
nothing. An open page reads it again when it regains focus or becomes
visible (no stream). The parity test also reads loam-ui's
`docs/appearance-settings.registry.json` and fails when its keys, route,
values or defaults part from the registry's or the store's.

**Themes** (2026-11-12, loam-ui `c326299`): each device kind also has a
`theme`, the id of an installed theme, default `latte`; a record from before
themes reads as `latte` and its first write adds the field. The installed ids
are not listed in this repo: they are the theme plugins of the plugin host's
one list (`lib/loam_plugins.py`, `docs/plugins.md`), in force in either
layer. Shipped, that is the app's `public/themes/<id>/` folders, in Loam's
own `app/` since 2026-11-16 (`projects/loam-ui.json`'s `checkout` and
`subdir` where the box develops it as a project; `lib/loam_plugins.ui_checkout`), read by the rule of the page's own loader (`src/themes.js`:
`manifest.json`, `schemaVersion` 1, its `id` the folder's name, modes light
and dark, `theme.css`), or a `PLUGIN.md` whose `kind` includes `theme`; in
Yours (`~/plugins`), the same shapes, and a Yours theme with a
shipped id wins. The list is read on every theme write and every `describe`
of a theme key, and always counts `latte` and `plain`, which the page will
not start without. `LOAM_PLUGIN_SHIPPED` and `LOAM_PLUGINS_DIR` replace the
two layers' folders for the tests (they replace `LOAM_THEME_DIRS`, retired
with the second mechanism). A theme is on where this setting chose it: the
plugin list derives a theme's `on` from here and has no switch of its own
for one, so there is one truth. A write of a theme not installed is a 400
that lists the installed ones. A stored theme later uninstalled by hand is
kept on read (the page draws Latte for an id it lacks) and comes back with
its folder; removing one through `plugin.remove` sets the device kinds that
used it back to `latte` unless a shipped one of that id remains. Only a value
not shaped like an id is replaced by `latte`.

The checkout can run ahead of the deployed image: a theme merged but not yet
deployed is settable, and the page draws Latte until it is. That direction is
harmless; the other (a theme deployed but not in the checkout) cannot happen,
because `bin/deploy` builds from this checkout.

`describe appearance.desktop.theme` (and `.phone.`) answers the live list as
`allowed`; the test also reads loam-ui's handoff and fails when its theme ids
are not what loam-ui's own folders give.

**Theme options** (2026-11-12; loam-ui's handoff of that day, its contract in
loam-ui's `docs/appearance.md`; the owner: "Every theme should be able to have
its own different variants"). A theme's manifest may declare `options`
(`docs/plugins.md`); Latte's are whatever its `PLUGIN.md` declares, which on
2026-11-12 (loam-ui `6100882`) is `agent-colours` (`palette`, `latte-tones`),
`agent-shapes` (`distinct`, `circle`, `rounded-square`, `latte-art`),
`latte-mug` (`on`, `off`) and `latte-inside` (`pour`, `initials`); Plain has
none. Each device kind keeps
`options`, a map of theme to option to choice, so every theme keeps its own
choices and switching to Plain and back keeps Latte's. A reading lists every
installed theme that has options, each option at its stored choice or its
manifest default (a record from before options, or a choice the theme no
longer offers, reads as the default); a stored theme or option no longer
installed stays in the file, unshown, and comes back with the theme. A patch
names only the leaves it changes, `{"desktop": {"options": {"latte":
{"agent-shapes": "circle"}}}}`, and they merge leaf by leaf under the store's
one lock, so concurrent writes of different leaves all land. An options value
that is not a map or is empty, a theme without options, an unknown option,
and a choice the theme does not offer (or one that is not text) are each a
400 sentence that writes nothing. The registry has one key per kind and
installed option, `appearance.<kind>.options.<theme>.<option>`, built live
from the catalog (`option_entries()` in `lib/loam_settings.py`): `describe`
lists them with their choices as `allowed`, and `set`/`get` go through the
same route the page does. The parity test reads the option keys, choices and
defaults of loam-ui's `docs/appearance-settings.registry.json` against the
manifests in loam-ui's folders.

**Sun mode and the saved place** (2026-11-13; loam-ui `29a1506`, its
contract in loam-ui's `docs/appearance.md`, *Sun mode*; the owner asked that
morning for a sunrise/sunset mode). A kind's `mode` may be `sun`: light from
30 minutes before sunrise to 30 minutes after sunset at a saved place, which
the page computes offline. The place is one value beside the two kinds,
`location`, **shared** so the phone and the desktop agree:
`{"latitude": 37.8, "longitude": -122.4, "source": "geolocation",
"timeZone": "America/Los_Angeles"}`. Latitude -90..90 and longitude
-180..180 are rounded here to tenths, half up as the page rounds; `source`
is `geolocation` or `timezone`; `timeZone` an IANA zone this machine's
tzdata knows (not a `posix/` or `right/` copy). All four are required and
nothing else is taken; `null` clears it. A reading always carries
`location`, `null` when none is saved; the page then uses its time-zone
fallback. The page's own body is `{"desktop": {"mode": "sun"}, "location":
{...}}` in one PATCH, or `{"location": {...}}` alone; a persona's are
`appearance.<kind>.mode` and `appearance.location` (a JSON value, or
`null`).

The **modes and the location's sources are loam-ui's**, never a copy here:
`lib/loam_appearance.py` reads them from loam-ui's
`docs/appearance-settings.registry.json` in the checkout (the enum of
`appearance.<kind>.mode` and of `appearance.location`'s `source`; loam-ui
generates that file from its source and drift-checks it), cached by mtime
(`LOAM_UI_APPEARANCE_REGISTRY` overrides the path; `none` for no file).
`describe appearance.desktop.mode` lists them live as `allowed`. Without a
readable handoff the modes are `light`, `dark` and `system` and a location
is refused; a stored `sun` is kept on read regardless (any id-shaped mode
is), so an unreadable handoff cannot turn Sun into System on the next write
of another field. When loam-ui adds a fifth mode to its handoff, it is
settable here with no change.

Seeded 2026-11-11: desktop size 90 (the owner: "I really enjoy it at a 90%
zoom"), both modes `system`, phone size 100. The browser theme they used before
was in their browser's storage, which nothing on the host can read.

Not settings, and classified so in `PAGE_RELAY`: messages, attachments,
streams, reads, compaction, permission answers, dictation, read-aloud audio.
The send key is not a setting (Enter is a new line, Ctrl/Cmd+Enter sends).
Each persona's read-aloud voice is deploy configuration
(`deploy/loam-ui/compose.yaml`, `SPEECH_VOICES`), not on the page, until
loam-ui's Read aloud reads its own per-agent setting (`plugin.agent`, target
`read-aloud/<agent>`, stored since 2026-11-13; see "Settings > Agents" below).

## Settings > Agents (2026-11-13)

An agent's profile (display name, face, default provider, model and
thinking level), the engineers' provider, model, thinking level and
"Deploy when a job lands", the nightly reviewer's schedule and model, and a
plugin's per-agent settings. The routes and their contract are in
`broker/README.md`, "Agent profiles, engineers and the reviewer"; the rules
are `lib/loam_profile.py`. Every write a persona makes through
`bin/settings` is said in General ("Ada changed Iris's model to opus"),
and is done only in a conversation: a persona's config (and so its
provider) is Permission first, so the owner is in the room. The page's relay
does not pass these routes yet; they sit in `PAGE_RELAY_AHEAD`.
