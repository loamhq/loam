---
name: loam-building-a-plugin
description: How to build a Loam plugin - a theme, page code, a server, tools or an app - its folder, its PLUGIN.md manifest, where it goes, and how to try it. Use when the owner wants Loam to look or behave differently in a way a plugin could provide, or asks to build one.
---

# Building a plugin

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
a row in Apps. A plugin can also add tools for agents, timers and settings
through `contributes:`. The whole interface, field by field, is
`docs/plugins.md` in Loam's repository; a small working example is
`docs/examples/plugins/echo/`.

**Where it goes.** The owner's plugins live in their own plugins folder
(`paths.plugins` in `bin/loam-config show`), a git repository committed
every 15 minutes, which updates never touch. A plugin there with the id of
one Loam ships wins over it. Put a new one there, then turn it on in
Settings > Plugins or with `mcp__engineer__settings`. A wrong manifest is
listed as broken with its reason, line by line, and breaks nothing else.

If a change needs Loam itself to change, that is a feature for the engineer
(see `loam-filing-a-bug-or-feature`), not a plugin.
