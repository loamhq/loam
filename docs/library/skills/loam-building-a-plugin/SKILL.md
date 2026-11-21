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
  server/main.py   a program Loam runs while the plugin is on
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
a row in Apps. A plugin that calls a paid service names its provider in
`keys:` (`- provider: openai`, with a `why`) instead of asking for a key of
its own: the owner enters each key once in Settings › Connections › API keys, and Loam
hands the plugin the keys it declared, in the file `$LOAM_PLUGIN_KEYS`
names. A plugin can also add tools for agents, timers and settings
through `contributes:`. The whole interface, field by field, is
`docs/plugins.md` in Loam's repository; a small working example is
`docs/examples/plugins/echo/`, and a real one with keys, an agent tool and
settings is Images, built into Loam at `plugins/images/`.

For the Syncthing plugin, `keys.source` names Syncthing’s settings file on this server. Loam shows the read before install and supplies the key through `LOAM_PLUGIN_KEYS` after confirmation.

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

**When it needs the owner to know something** (a check failed, a spend
passed a mark, a credential is about to expire), a plugin never posts in a
thread or on a board. It tells the agent the owner chose to hear when
something in Loam needs attention: `loam tell "what happened" --by
<plugin id> --evidence "<where to look>"` from any command, or
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
The full groups and the privacy declaration are in docs/plugins.md.

### Your Market downloads

A plugin declaring `public-work` in `touches.facts`, with `api.github.com`
and `raw.githubusercontent.com` in `touches.network`, can read
`public-work.publications`. Each listing in the owner’s chosen GitHub account
has an `id`, `name`, `url` and `downloads`. The total includes updates and
direct GitHub release-file downloads. Zero means none recorded. A missing or
null value means the total is unavailable. The Market refreshes it nightly.
