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
  record: none         # none, read, write
  memory: none
---
What it does and how to use it.
```

`kind: theme` needs `theme.css`; an `app` is a server plugin that also has
a row in Apps. A plugin that calls a paid service names its provider in
`keys:` (`- provider: openai`, with a `why`) instead of asking for a key of
its own: the owner enters each key once in Settings › Accounts, and Loam
hands the plugin the keys it declared, in the file `$LOAM_PLUGIN_KEYS`
names. A plugin can also add tools for agents, timers and settings
through `contributes:`. The whole interface, field by field, is
`docs/plugins.md` in Loam's repository; a small working example is
`docs/examples/plugins/echo/`.

**Where it goes.** The owner's plugins live in their own plugins folder
(`paths.plugins` in `bin/loam-config show`), a git repository committed
every 15 minutes, which updates never touch. A plugin there with the id of
one Loam ships wins over it. Put a new one there, then turn it on in
Settings > Plugins or with `mcp__engineer__settings`. A wrong manifest is
listed as broken with its reason, line by line, and breaks nothing else.

**A theme** is the quickest plugin to make. Copy one Loam ships (in
`app/public/themes/` of the checkout, `paths.root`; `plain` is the
simplest, `autumn` a full one) into the plugins folder under a new id,
change `id` and `name` in its `PLUGIN.md`, rename the id in every
`:root[data-look="…"]` selector of its `theme.css`, and change the token
values, light and dark. Keep to the tokens the original sets: the page drops
any it does not know. It shows in Settings › Appearance to be chosen; look
at it at a phone width and a desktop width, light and dark, before calling
it done.

**Sharing it** with someone, by link or in the marketplace, is
`loam-publishing-to-the-marketplace`.

If a change needs Loam itself to change, because no plugin can reach it, it
is a feature: report it to Loam's makers, and fix it here only if it cannot
wait (`loam-filing-a-bug-or-feature`).
