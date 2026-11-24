---
name: loam-building-a-plugin
description: How to build a Loam plugin - a theme, page code, a server, tools or an app - its folder, its PLUGIN.md manifest, where it goes, and how to try it. Use when the owner wants Loam to look or behave differently in a way a plugin could provide, asks to build one, or wants to customise Loam at all (a plugin is the first way to try).
---

# Building a plugin

**A plugin first.** Any change the owner wants in how Loam looks or behaves
is a plugin when a plugin can do it, not a change to Loam's own code. A
plugin lives in the owner's plugins folder, survives every update, can be
turned off in one tap, and can be shared by link. A change to Loam's code
stays on this install, has to be merged with every release, and may stop an
update until someone sorts it out (`loam-filing-a-bug-or-feature`).

**Customising someone else’s plugin starts with a copy.** Find its current folder
in Settings › Plugins. For a shipped plugin, copy it to `<owner plugins>/<id>/`.
For a Market or link install, copy it to `<owner plugins>/_copies/<id>/`.
Keep the folder name and manifest id to take over. If the owner wants both
side by side, use a new folder directly under the owner’s plugins folder
and give its manifest the same new id. Run `loam plugin track COPY_ID ORIGINAL_ID`
as soon as you make the copy. This remembers the exact starting version for a
Market, linked, preinstalled or shipped original. For an older copy, use
`--link URL` with the exact starting release if the original has moved.
Keep its `forked_from: original-id@version` in the manifest when sharing it.
Loam recognises edits to an installed folder and same-id Market copies.
A renamed copy made before tracking needs its original identified.

**When a newer version is available**, Loam tells the owner's chosen attention
agent. Run `loam plugin prepare COPY_ID`. It returns the starting folder,
the person's folder as it was when you began, the incoming folder and a
separate editable `folder`. The file lists show each side's changes, overlap
and choices still to make. Read the files, using `git diff --no-index`
between the snapshots for detail. Separate changes are already in the draft.
For overlapping text, resolve the marked lines in the draft. For a binary,
deletion or type change that needs a choice, the draft starts with the person's
version. Preserve the copy's own id, theme selectors and settings. Try the
draft's normal entry point. Run `loam plugin preview COPY_ID` to validate it
and see any added access. Review that access with the owner too.

Bring this to the owner in one chat. Say what they would see, what would
stay theirs, and what you need them to choose. For example, “The new version
adds reading tips. You chose green paper and the maker chose lavender.
Shall I add the tips and keep your green, or leave everything as it is?”
On their yes, finish the agreed draft, preview again, and run
`loam plugin apply COPY_ID CONFIRM` with the returned token. Check the
plugin in Settings and try it. If the files changed since the conversation,
prepare or preview again and confirm any new choices. The response names
the recovery folder containing the exact previous plugin and its record.
On no, run `loam plugin skip COPY_ID VERSION`. The same version stays quiet.
Either choice keeps the connection to the original for future releases.

A plugin is a folder whose name is its id. Its `PLUGIN.md` is the manifest
(frontmatter) and the guide (the body, for a person and an agent).

```
my-plugin/
  PLUGIN.md        required
  theme.css        a theme's token values, light and dark
  page/main.js     an ES module the app imports; no build step
  server/main.py   a program Loam wakes on first use and sleeps when idle
  assets/          images, fonts, sounds
```

```yaml
---
id: my-plugin          # lower case, digits, dashes; the folder's name
name: My plugin
version: 1.0.0         # major.minor.patch
api: 0                 # the plugin interface version
kind: [extension]      # theme, extension, app, agent or skill
touches:               # what it reaches, shown before install
  network: []
  record: none         # none, read, write, bypass
  memory: none
---
What it does and how to use it.
```

`kind: theme` needs `theme.css`; an `app` is a server plugin that also has
a row in Apps. A server plugin sleeps by default until the first request
arrives on `/x/<id>/`, the share door `/s/<token>`, or its loopback
`$LOAM_PLUGIN_URL`, and sleeps again after ten quiet minutes with no active
request, stream or WebSocket in flight. Bind the port given in `PORT` /
`LOAM_PLUGIN_PORT` and persist durable state to `$LOAM_PLUGIN_DATA` or
`$LOAM_PLUGIN_PRIVATE`. Use `contributes.schedule.kind` for periodic
background work. If a server genuinely needs to stay running continuously
(for example an outbound bot connection or long-lived in-memory state),
declare `server: { always_on: true }` in `PLUGIN.md` (plugins with an
external `contributes.box.listener` stay on by default). A plugin that calls a paid service names its provider in
`keys:` (`- provider: openai`, with a `why`) instead of asking for a key of
its own: the owner enters each key once in Settings › Connections › API keys, and Loam
hands the plugin the keys it declared, in the file `$LOAM_PLUGIN_KEYS`
names. A plugin can also add tools for agents, timers and settings
through `contributes:`. The whole interface, field by field, is
`docs/plugins.md` in Loam's repository; a small working example is
`docs/examples/plugins/echo/`, and a real one with keys, an agent tool and
settings is Images, built into Loam at `plugins/images/`.

**Keep `agent.tool` servers light.** Every live conversation spawns its own
copy of every enabled `agent.tool` stdio MCP server, so memory multiplies by
open chats. Run the tool on plain `python3` and speak JSON-RPC 2.0 over
stdin/stdout with only the Python standard library (~5–10 MB PSS at rest) —
copy the pattern in `plugins/images/tools/images_mcp.py` or
`plugins/sharing/tools/sharing_mcp.py`. Do not import the `mcp` / `pydantic`
SDK (or run under `bin/mcp-python` just for it): that adds ~45 MB of private
memory per chat before a tool is ever called. Defer heavy per-call imports
(HTTP clients, parsers, SDKs) inside the tool handler so idle servers stay
under ~10 MB, and run slow tool calls in a worker thread so `ping` and
concurrent calls do not block each other.

**How it introduces itself.** `description:` is its promise, one plain line
about what someone gets from it; `screenshots:` are up to six pictures of it
at work in its folder (`{src, src_dark, caption}`); a theme's `palette:`
names its main colours (`{token, name, name_dark}`). Write the guide plain
first: what you can do, how to use it in at most three steps, what leaves
the server; then a `---` rule and `## Technical details`. Settings › Plugins
and its Market page show it in that order. Mark the settings that mean "set
up" with `setup: true` (an address it needs before it can work): once one
has a value, its page leads with its controls. `docs/plugins.md` has each
field; Loam's demo world for screenshots is `app/tools/capture-plugin-shots.mjs`.

**A file beside each thread.** The experimental `thread.file` contribution
registers one named Markdown document per thread. Its path appears in the turn
and follows a rename. Read and save it with its revision through
`/api/threads/<id>/files/<plugin>`. The interface may change. See `docs/plugins.md`.

**When it needs the owner to know something** (a check failed, a spend
passed a mark, a credential is about to expire), a plugin tells the agent
the owner chose in Settings › Agents. Call `loam tell "what happened" --by
<plugin id> --evidence "<where to look>"` from a command, or
`loam_attention.tell(...)` in Python. Say the fact, not what to do about
it; the same fact is told once. That agent decides whether the owner hears
of it. `docs/plugins.md`, "Telling an agent that something needs
attention".

**Where it goes.** The owner's plugins live in their own plugins folder
(`paths.plugins` in `bin/loam-config show`), a git repository committed
every 15 minutes, which updates never touch. A plugin there with the id of
one Loam ships wins over it. Put a new one there, then turn it on in
Settings > Plugins or with `mcp__engineer__settings`. A wrong manifest is
listed as broken with its reason, line by line, and breaks nothing else.

**A theme** is the quickest plugin to make. Copy one Loam ships (in
`app/public/themes/` of the checkout, `paths.root`; `plain` is the
simplest, `latte` a full one; a theme from the Market, such as Autumn,
whose repository its Market page links, also carries its own display font)
into the plugins folder under a new id,
change `id` and `name` in its `PLUGIN.md`, rename the id in every
`:root[data-look="…"]` selector of its `theme.css`, and change the token
values, light and dark. Keep to the tokens the original sets: the page drops
any it does not know. It shows in Settings › Appearance to be chosen; look
at it at a phone width and a desktop width, light and dark, before calling
it done.

**Sharing it** with someone, by link (the repository's address is enough: Loam finds the newest release and the folder) or in the marketplace, is
`loam-publishing-to-the-marketplace`.

If a change needs Loam itself to change, because no plugin can reach it, it
is a feature: report it to Loam's makers, and fix it here only if it cannot
wait (`loam-filing-a-bug-or-feature`).

A page plugin can add one compact update above the agents in Chats with
`thread.list.line`, draw its own Apps picture with `apps.icon`, and open an
unsent composer with the `chat.draft` command. A server can declare activity
groups under `touches.facts` and run `LOAM_PLUGIN_FACTS` to read their JSON.
These four interfaces are new and may change in a later release.
The full groups and the privacy declaration are in docs/plugins.md.

Chats shows its rows as soon as they are ready. While a returning line starts,
the host reserves its last measured height in that browser for the current
layout. The space closes if the plugin returns no line. Restore saved display
values during activation and refresh them in the background.

### Your Market downloads

A plugin declaring `public-work` in `touches.facts`, with `api.github.com`
and `raw.githubusercontent.com` in `touches.network`, can read
`public-work.publications`. Each listing in the owner’s chosen GitHub account
has an `id`, `name`, `url` and `downloads`. The total includes updates and
direct GitHub release-file downloads. Zero means none recorded. A missing or
null value means the total is unavailable. The Market refreshes it nightly.

## Thread controls, navigation actions, settings switches, and Markdown editing

These API 0 slots are new and may change.

- Register a `thread.header.action` with `slot: 'control'` to draw a button in the thread header. A side panel gives it `placement: 'menu'` in the thread actions menu. Call `closeMenu()` when choosing that item. Both placements receive the current `thread` and `wide`. A control can open an existing `panel.item` or call `selectView(name)` for a contributed view. A view can supply `dirty(context)` to protect a pending edit when its thread closes. These interfaces are experimental. Omitting the slot keeps an action below the header.
- Register a `thread.list.action` to draw an action button in the Chats list top bar (when not viewing Archived threads). Its `render({wide, archived, state})` returns a button node or `null`.
- Register a `rail.action` to add a workspace action button to the desktop navigation rail (below the section tabs, above Activity and Settings). Provide `{id, title, label?, icon, active?, badge?, onclick}` for a native rail button (or `render({wide, host})` for custom DOM). Use `host.openSheet({kind: '<plugin>:<name>'})` with an `app.overlay` (`slot: 'sheet'`) to open an app-level modal over whatever is on screen without leaving the active chat or list.
- In `contributes['settings.entry']`, add `control: switch` alongside `choices: ["on", "off"]` to draw a native toggle switch in Settings › Plugins instead of a dropdown; changing a plugin's settings emits `plugin.settings.changed` (`{id, values}`). In `contributes['agent.prompt']`, add `when: {setting: <key>, in: [<values>]}` to vary the agent instructions by a plugin setting (changing that setting re-renders persona instructions).
- Register a `files.viewer` with `slot: 'projection'` to edit a Markdown file through a different text view. Match `{root, path, row}` with `when`. Return `{show(raw), write(raw, edited), offsets(raw)}`. The offset map supplies `toSource(offset, assoc)` and `fromSource(offset)` for annotations. The last matching projection is selected when the file opens and stays with that edit. Preserve hidden bytes, line endings and unedited text. Files continues to own the revision check and conflict handling.
