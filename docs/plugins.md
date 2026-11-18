# Plugins: the host's side

*Since 2026-11-12. The code is `lib/loam_plugins.py`; the routes are in
`bin/session-broker` (`plugins_route`); the three settings are in
`lib/loam_settings.py`; the tests are `tests/test_plugins.py`. This is the
machine's half of the plugin interface, spec v0's build-order steps 2 (the
manifest, the two layers, the list) and 5 (install from a link). The page's
half — Settings › Plugins and the loader — is loam-ui's; its contract is the
last section here.*

## A plugin is a folder

```
my-plugin/           the folder's name is the plugin's id
  PLUGIN.md          the manifest (frontmatter) and the guide (body): required
  theme.css          a theme's token values, light and dark
  page/main.js       an ES module the page imports, no build step
  page/…             anything it imports, relative to itself
  assets/…           images, fonts, sounds
  server/…           code for the machine: run as its own supervised program
                     when the plugin is on (2026-11-13, "Server plugins")
```

`PLUGIN.md`'s frontmatter is the manifest:

```yaml
---
id: pet                     # lower-case letters, digits, dashes; the folder's name
name: A pet                 # required
version: 1.0.0              # required: major.minor.patch (a -pre or +build suffix allowed)
author: A friend            # optional
api: 0                      # required: the interface version it was written for
kind: [extension]           # required: theme, extension, app, agent, skill
places: [app.overlay]       # the named places it fills
overrides: []               # core component ids it replaces (core.…)
touches:                    # what it reaches; shown before install
  network:
    - host: sprites.example.com
      why: fetches the pet's sprite sheet
  record: none              # none, read, write, bypass
  memory: none              # none, read, write
  server: false
settings: settings.md       # a path inside the plugin
forked_from: pet@0.9.0      # id@version, when it is a changed copy
options:                    # a theme's own variants (kind theme only)
  - id: agent-shapes
    label: Agent shapes
    choices: [{id: distinct, label: Each its own}, {id: round, label: Round}]
    default: distinct
image: cover.webp           # a theme's cover picture, a path inside it (kind theme only)
image_position: 62% 45%     # where the cover is cropped: two percentages
image_dark: cover-dark.webp # dark mode's cover, if it differs (falls back to image)
image_position_dark: 50% 30%  # dark mode's crop (falls back to image_position)
server:                     # how its server starts (only with server/; 2026-11-13)
  run: python3 server/main.py
icon: assets/icon.svg       # its picture in Apps and Settings > Plugins (2026-11-13)
agent_settings:             # what it keeps for each agent, on the agent's profile
  - id: voice
    label: Voice
    choices: [{id: Kore, label: Kore}, {id: Puck, label: Puck}]
    default: Kore
---
```

**An agent's profile** (2026-11-13) is a place, `agent.profile`, and a
plugin keeps settings for each agent in `agent_settings`: at most 16 fields,
each an `id` (a slug of at most 48 characters), a `label` (at most 80), and
optionally `choices` (1 to 100 `{id, label}`, ids any text of at most 48, as a
voice's name is) and a `default`; a field without choices is text of at most
200 characters. The values live in Yours, `_settings/<id>/agents.json`, and
are read and written by `GET`/`PATCH /v1/plugins/<id>/agents[/<agent>]`; the
profile (`GET /v1/agents/<agent>`) lists each such plugin with the agent's
values. Contract: `broker/README.md`, "Agent profiles, engineers and the
reviewer". Any plugin may; first-party ones get nothing more.

The body after the closing `---` is the guide, for a person and an agent.

**One package shape for everything shared** (2026-11-13; the owner, m261 and
m263): the marketplace shares themes, extensions, apps and later agents and
skills, and each is this folder with its `PLUGIN.md`; `kind` names what it
is.

| `kind` | what it is | what the host does with it |
|---|---|---|
| `theme` | `theme.css` (required), token values | Appearance chooses it |
| `extension` | `page/` and/or `server/` code | on/off switch; its page code is served, its server run |
| `app` | a server plugin that also lists itself in Apps | as an extension, and a row in `GET /v1/apps` at `/x/<id>/`; **an app with no `server/` is broken** |
| `agent` | a persona: soul, config, skills | **read and listed only**, never installed or run (`switch: "listed"`); **broken if it carries memory**: any folder named `memory` or any `MEMORY.md`, at any depth ("a shared agent never carries its memory", m263) |
| `skill` | a `SKILL.md` and what it uses | read and listed only (`switch: "listed"`); a warning without `SKILL.md` |

`theme`, `extension` and `app` combine (`kind: [theme, extension]`); an
`agent` or a `skill` is a package alone. Installing an `agent` or `skill`
from a link answers the first call with `installable: false` and a `why`, and
the confirm is a 409 that installs nothing.

**`server`** is `{run: "<one line>"}` (a bare string is the same), run by
`/bin/sh -c` in the plugin's folder. Without it, `server/main.py` is run with
`python3`, or `server/main.js` with `node`; a `server/` with neither and no
`run` is a warning and nothing runs. Another key under `server` is kept with
a warning. **`icon`** is a path inside the folder to an `.svg`, `.webp`,
`.avif`, `.jpg` or `.png`; wrong is a warning and left out, and the files
route serves it.

**The YAML is read without a dependency** (`parse_frontmatter`), and the reader
is strict: block mappings and lists by indentation (spaces), `- key: value`
items, flow `[a, b]` and `{a: 1}` (over several lines too), plain, `'single'`
and `"double"` quoted text, `|` and `>` block text, and `#` comments. A plain
value is `true`/`false`, `null`/`~`/nothing, a whole number, or text: there
are no floats, so `version: 1.2` is the text `1.2`. Anchors, aliases, tags,
complex keys, tabs in indentation, a duplicate key and a `: ` inside plain
text are refused with the line number. It is about 300 lines; that is the
whole cost of `PLUGIN.md` over a `plugin.json`.

**A manifest that is wrong is a broken row, not a missing one.** The list
shows it with its reason (`"broken": "line 3: 'id' is given twice"`). Nothing
in one folder can stop another from being listed. What spec v0 does not name
(an unknown place, a field, a `touches` key, an `api` newer than this host's)
is kept as given and listed under `warnings`.

**loam-ui's theme folders are read as they are.** A folder with a
`manifest.json` of `schemaVersion` 1 and no `PLUGIN.md` is a theme plugin
(`format: "manifest.json"`), held to the rule of loam-ui's own loader
(`src/themes.js`): its `id` the folder's name, a name and an author, a
`major.minor.patch` version, modes `["light", "dark"]`, `theme.css`. Where a
folder has both, `PLUGIN.md` is read. A plugin whose kind includes `theme`
needs a `theme.css`.

**A theme's options** (2026-11-12) are its `options` list, in either shape
(`manifest.json` or `PLUGIN.md`), held to loam-ui's loader
(`validateThemeOptions` in `public/theme-css.js`): at most 8 options, each an
`id` (a slug of at most 48 characters), a `label` (at most 80), 2 to 8
`choices` of `{id, label}` and a `default` among them; an option the core page
draws itself offers only choices it knows. Which options those are, and their
choices, is loam-ui's `coreOptions` table in that same file, which the host
reads from the loam-ui checkout each time (`core_options()`;
`LOAM_UI_CORE_OPTIONS` names another file, or `none`), never a copy here: a
copy went stale on 2026-11-12, when loam-ui `6100882` gave Latte `latte-art`,
`latte-mug` and `latte-inside`, and Latte read as broken. When the file cannot
be read there is no core restriction and each theme's own choices stand. A
wrong list breaks the theme's row. The list row carries them as `options`;
`theme_options()` is the catalog Appearance stores and validates choices
against (`broker/settings.md`, "Theme options"), and the settings registry
has one key per kind and option, `appearance.<kind>.options.<theme>.<option>`,
read live from it, so a theme installed with options needs no code here.

**A theme's cover** (2026-11-13, loam-ui `329b219`) is `image`, the picture
its card in Appearance shows, and `image_position`, the CSS `object-position`
the card crops it at (the page's default is `50% 50%`). Both are optional and
held to loam-ui's own rule (`themeImage` in `public/theme-image.js`), because
the page throws on anything else and so fails the whole theme: `image` is a
path inside the theme's folder of `A-Z a-z 0-9 _ -` segments ending in
`.webp`, `.avif`, `.jpg`, `.jpeg` or `.png` (so no `..`, no absolute path, no
URL, no space), and it must name a file there that does not lead out of the
folder by a link; `image_position` is two percentages from `0%` to `100%`
(`62% 45%`), at most 32 characters, and only with a usable `image`. That is
stricter than CSS: `center` or `50px 10px` would be valid CSS but the page
refuses them. A wrong value is a warning and the field left out of the row;
it never breaks the theme. Kept, they are the row's `image` and
`image_position` (absent, not `null`, when there is none, so the page's merge
of a shipped theme's `/shipped-plugins.json` entry with its row keeps the
shipped value). On a plugin that is not a theme both are a warning and left
out. The files route serves the file `image` names, wherever in the folder it
is, so a Yours theme's cover is at `/api/plugins/<id>/files/<image>`.
**A dark-mode cover** (2026-11-13) is `image_dark` and `image_position_dark`,
held to the same rules as `image` and `image_position` and kept or left out
the same way; the page shows them in dark mode and falls back to the light
pair for whichever is absent, and both need a usable `image` (the page throws
on a dark cover with no default one). The files route serves `image_dark` as
it does `image`.

## Two layers

| Layer | Where | Who writes it |
|---|---|---|
| **Shipped** | loam-ui's `public/themes/` and `public/plugins/`, in the checkout both copies of the page are built from (`projects/loam-ui.json` → `checkout`) | Loam's updates. Never edited in place. |
| **Yours** | `/home/loam/plugins/`, its own git repository | You and your agents. Updates never touch it. |

`LOAM_PLUGIN_SHIPPED` (folders joined by `:`) and `LOAM_PLUGINS_DIR` replace
them, for the tests and for another install.

**First-party plugins whose halves are the machine's** ship in this
runtime's own `plugins/` (2026-11-14, first release L9), the third shipped
folder after loam-ui's two: `reviewer` (on by default: its timer, its
instructions, what every agent is told about it) and `daily-notes` (off by
default: its folder; the page's button). `default_on` is a shipped plugin's
default and nothing more: any saved choice in `installed.json` wins, on or
off.

**A plugin in Yours with the id of a shipped one wins**, and its row says what
it shadows; the shipped row says `shadowed_by: "yours"`. A broken copy never
wins, so a bad copy in Yours cannot take a shipped plugin away. Two shipped
folders of one id: the first is in force and the second is broken.

**Why Yours is its own folder** (the owner, 2026-11-12: plugins are not
ai-labs, which "has a very specific usecase"; a persona chose a separate folder
over the vault). It sits beside the vault and ai-labs, outside the code
repositories (this one and loam-ui), so no update touches it. It is not the
vault, because the vault is notes that Obsidian indexes and syncs, and
plugin code and its churn are not notes. It is **not a Files root**: the app
reaches plugins through Settings › Plugins, and an agent through its own
shell and `bin/settings`.

It is **its own git repository**, committed every 15 minutes by
`bin/plugins-autocommit` (cron, beside `vault-autocommit` and
`ai-labs-autocommit`), so every install, edit and removal has its history
there. The folder appears with the first plugin change; the first run after
that makes it a repository on `main`. It is pushed only when it has an
`origin`. **On the first install it has one since 2026-11-13**: a private
repository of the owner's, reached as `git@github-plugins:<owner>/<repo>.git`
through a deploy key with write access to that one repository (host
`github-plugins` in `~/.ssh/config`, the way ai-labs and the vault are
reached). Another install
adds its own `origin`. A plugin is shared as its own repository (install
from a link), not by sharing the layer.

Yours holds the plugin folders, `installed.json`, a `README.md` written on the
first change, `.staging/` (in `.gitignore`), where an install is fetched
before it is moved into place, and two reserved folders for when plugin
settings and data are built: **`_settings/<id>/`** for a plugin's own
settings file (the manifest's `settings:` names a file there) and
**`_data/<id>/`** for what it saves. Both sit outside the plugin's folder, so
an update that replaces the folder whole never touches them. A name
beginning with `_` or `.` is never a plugin.

## What a plugin adds to the machine: `contributes:` (2026-11-14)

The page's places are filled by page code. The machine's are filled by
`contributes:`, one list per place. Every entry is in force exactly while the
plugin is on (in force, its switch on), and turning it off takes every entry
away. The code is `lib/loam_contributions.py`.

```yaml
contributes:
  agent.tool:                  # a stdio MCP server, run in the plugin's folder
    - name: hello
      run: python3 tools/hello_mcp.py
      lanes: [persona, engineer, reviewer]   # default [persona]
      agents: [ada]            # optional: only these personas
  agent.prompt:                # what it lets an agent do, in its own words
    - lanes: [persona]
      text: You can say hello with the hello tool.   # or file: prompt.md
  schedule.kind:               # a run on Loam's schedule grammar
    - name: tick
      schedule: daily at 05:00 PT
      run: python3 tick.py
  settings.entry:              # bin/settings plugin.<id>.<key>
    - key: greeting
      label: What it says
      choices: [hello, hi]     # or no choices: text, at most 500 characters
      default: hello
  engineer.env:                # an engineer job's environment
    - name: HELLO_MODE
      setting: greeting        # or value: <text>, or secret: <name in the secrets file>
      projects: [Loam]         # optional
  engineer.job:                # a command run at each job's launch (L6)
    - run: python3 job_env.py  # prints NAME=value lines: the job's environment
      projects: [widget]       # optional; timeout: seconds, default 30, at most 120
  drift.check:                 # check-drift: exit 0 is fine, else each line a finding
    run: python3 drift.py
  box.listener:                # a process allowed to answer off loopback
    - process: example-sync
      why: devices on the network sync a folder
  settings.secret:             # a key the person pastes (2026-11-14, L9)
    - name: GEMINI_API_KEY
      label: Gemini API key
      check: builtin:gemini    # or a line run in the folder, the key on stdin
  vault.folder:                # a vault folder it uses (2026-11-14, L9)
    - setting: folder          # or path: Daily
```

**`schedule.kind` may name `setting:`**, one of its own `settings.entry`
keys: the timer follows that value whenever it is a valid schedule in the
grammar, and its `schedule` is the default (the reviewer's schedule is a
setting on its page).

**`settings.secret`** is a credential the person pastes, an API key above
all, which is theirs and billed to them; no shared key ships. It is pasted in
the plugin's settings (`PUT /v1/plugins/<id>/secrets/<name>`,
broker/README.md, "A plugin's keys"), **checked on entry** and kept only when
the check passes, in the plugin's private folder (`secrets.env`, 0600), never
in Yours, so never in a repository. `check:` is a line run by `/bin/sh -c` in
the plugin's folder with the key on stdin (exit 0 is good; the first line of
its output is the sentence the person reads), or `builtin:<name>`, a check
the host makes itself for a plugin with no machine code (`builtin:gemini`,
lib/loam_keys.py: lists Google's models with the key in a header). Without
`check:` the key is kept as given. A plugin's processes read it with
`secret_for`, which falls back to the box's own secrets file (an install that
set a key there before this, like the first box's). An agent cannot set,
check or read one.

**`engineer.job`** is a command run at each engineer job's launch, in the
plugin's folder, as the launcher's user, before the job's unit starts
(2026-11-14, first release L6). It is how a project's cloud credentials reach
its jobs: written fresh for each job, which `engineer.env`'s fixed values
cannot be. Besides what every plugin process is told (below) it gets
`LOAM_PROJECT`, `LOAM_JOB_ID`, `LOAM_JOB_DIR` (the job's folder: it exists,
outlives the session, and is the place for a file the job reads, like a
credential config) and `LOAM_JOB_SCRATCH` (the job's scratch, made when the
session starts and removed when it ends, killed or not: the place for a
cache, so no cleanup step is needed). Each line it prints as `NAME=value` is
a variable of the job's, in the same 0600 file `engineer.env` uses.
- **It fails closed and is never fatal.** A hook that exits non-zero or
  outlasts its timeout gives nothing; a line that is not `NAME=value` is
  dropped. The job still runs, without it.
- **A name the launcher sets is the launcher's** (`PATH`, `HOME`, `TMPDIR`,
  the git helper's, the deploy key's, …), for `engineer.env` too: a unit's
  environment file would otherwise win over the launcher's own values.
- Why anything offered was not used goes to `plugin-env.log` in the job's
  folder. No value is written there.
- A hook that needs root (a signer holding a key the user must not read) is
  the owner's to install by hand; the plugin carries the units and an
  install script, and its `drift.check` says when the installed copies have
  drifted (below, "The box overlay").

**`vault.folder`** is a folder in the vault the plugin uses, a `path` or the
value of one of its settings. It is made when the plugin is on (at the
switch, at a setting's change) and never removed: turning a plugin off takes
its button away and leaves the notes.

**What an agent's tool is told** (2026-11-16, first for doc-comments; any
plugin's `agent.tool` gets it), besides what every plugin process is told:
`LOAM_LANE`; `LOAM_AGENT`, the persona it serves (persona lane only);
`LOAM_VAULT` and `LOAM_WORKSPACES`, so a tool can take the absolute paths an
agent knows; and, for a plugin with a server, `LOAM_PLUGIN_URL`, that
server's loopback address (`http://127.0.0.1:<port>/`), so the tool can be a
thin client of the one process that writes the plugin's data. A plugin
server trusts what it is told on loopback the way the box trusts its own
user: a tool says which agent it is with a header of the plugin's own
(doc-comments: `X-Loam-Agent`).

**Lanes.** `persona` covers a persona's conversation, wakes and crons: the
three share one workspace, and so one set of tools and one `CLAUDE.md`.
`engineer` and `reviewer` are the other two. A conversation and a wake are
not split further: that would need an appended system prompt on the
conversation lane, and the broker deliberately has none.

**When an entry lands.**
- **Persona lanes.** The persona's assembled files (`.mcp.json`, the Codex
  and Antigravity renderings, the allow list, and a paragraph at the end of
  `CLAUDE.md`) are re-rendered by `bin/provision` on every switch, install
  and removal. Each lane's next run has them.
- **Engineer and reviewer.** Their committed files are never touched:
  `bin/engineer-job` and `bin/review` pass the servers as `--mcp-config`
  and the words as `--append-system-prompt` at launch.
- **A live conversation** keeps what it started with, and is never
  restarted for a switch.
- **Codex engineer jobs** get the words, not yet the tools.

**A server's name** in a lane is the plugin's id, or `<id>-<name>` when the
entry's name is not the id.

**What every plugin process is told** (a server, a tool, a scheduled run, a
drift check):

| variable | what |
|---|---|
| `LOAM_PLUGIN_ID`, `LOAM_PLUGIN_DIR` | its id and folder |
| `LOAM_PLUGIN_SETTINGS`, `LOAM_PLUGIN_SETTINGS_DIR` | its settings file, and the folder holding it and `values.json` (`settings.entry`) |
| `LOAM_PLUGIN_DATA` | `_data/<id>/` in Yours: its data, in open formats, committed and backed up with Yours |
| `LOAM_PLUGIN_PRIVATE` | its machine-local folder, `<state>/plugins/private/<id>/` (0700, since 2026-11-14): credentials, caches, binaries, operational state. Never in Yours, so never in a repository or its backup. A plugin's own secrets go in its `secrets.env` there |
| `LOAM_PLUGIN_SECRETS` | for a tool that declares `secrets: [NAME, ...]`: the names it may read from its `secrets.env`. Names, never values, so no rendered file carries a secret |
| `LOAM_LIB` | Loam's own `lib/`, for a plugin that chooses to import it. **No promise is made about anything in it**: an update may change any of it, and a marketplace plugin should not use it |

**`schedule.kind` takes three forms**: `schedule:` in Loam's grammar
(`daily at 05:00 PT`); `calendar:`, a systemd OnCalendar line for what the
grammar cannot say (`*:*:30`, `*-*-* 00/6:10:00 UTC`); or `boot: true`, a run
at each start of the user's manager (a service wanted by `default.target`,
no timer). `timeout:` is seconds (default 300).

**An integration a plugin owns** is the plugin's own server, at `/x/<id>/`
through the gateway. The broker forwarded `/v1/integrations/<id>/...` to it
for the page's old relay from 2026-11-14 until loam-ui `1c4ec9b` moved the
page, and the forward is gone (broker/README.md). GitHub's are the broker's
own.

**Timers** are `loam-plugin-<id>--<name>.{service,timer}`, rendered into the
user unit directory and kept in line by the switch and by the five-minute
reconcile.
- They obey docs/config.md's rule. A standing timer is never stopped and
  started; a changed one is moved by `daemon-reload`; a new one is armed
  fresh (`loam_units.arm_timer`).
- The service runs `bin/plugin-run <id> <name>`, which goes no further when
  the timer fired at a moment its calendar does not name
  (`lib/loam_due.py`), or when the plugin is off.

**A plugin's routes** are its own server's, at `/x/<id>/` (below). There is
deliberately no seam that adds a route to the broker: the broker is the
core's, and a plugin reaches it as the page does.

**The declaration.** What `contributes:` adds is computed into the
declaration shown before install (`touches.agents`, `.schedule`, `.settings`,
`.engineer_env`, `.engineer_job`, `.secrets`, `.drift_check`, `.inbound`), so a plugin cannot
declare less than it does. A later version that adds a tool, a lane, a timer
or a secret asks again, as a new network host does. Side-loaded plugins get
the same display and no rules: what Loam stamps is held to the principles,
and nothing else is.

## The installed list: `installed.json`

```json
{
  "version": 1,
  "plugins": {
    "pet": {
      "source": {"kind": "link", "repo": "friend/loam-pet", "path": "pet",
                 "ref": "v1.0.0", "ref_kind": "tag",
                 "commit": "25acaa810d3e9d08c79d2736f749873a56607717",
                 "url": "https://github.com/friend/loam-pet/tree/25acaa8…/pet"},
      "version": "1.0.0",
      "declaration": {"touches": {…}, "overrides": [], "page_code": true},
      "confirmed": "cd383308cd35f365",
      "installed_at": "2026-11-12T19:06:18Z",
      "installed_by": "ada",
      "files": "<sha-256 of the folder's paths and contents>"
    }
  },
  "on": {"pet": true, "read-aloud": false}
}
```

A line per plugin installed from a link; `on` for every id anyone has
switched. A Yours folder with no line is *built here*. A line whose folder is
gone is a broken row (`it is in the installed list but its folder is gone`)
until it is removed. `files` lets the list say a linked plugin has been
`edited` since it was installed, so an update that would replace those edits
says so before the yes. An unreadable `installed.json` leaves the list drawn
(with `error` saying why) and refuses every write rather than overwrite it.

## On and off

- **The switch is the id's.** A shipped plugin is on until turned off. A
  plugin installed from a link is on (the yes to install it was the yes); an
  update keeps whatever its switch was. A plugin built here is **off until
  turned on**: an agent writing a folder does not put code into the page by
  itself. Removing a Yours copy that shadowed a shipped one leaves the switch
  for the shipped one; removing the last copy forgets it.
- **A theme has no switch.** A plugin whose kind is only `theme` (and which
  overrides nothing) is on where Appearance has chosen it (`appearance.phone.theme`,
  `appearance.desktop.theme`; `lib/loam_appearance.py`): its row's `on` is
  derived from there, `in_use` names the device kinds, `switch` is
  `"appearance"`, and its `PATCH` is a 409 that says so. "Which theme is on"
  is one truth. A plugin that is both theme and extension has a switch for
  its code; its theme is chosen in Appearance like any other.
- **Appearance's themes are this list's.** `loam_appearance.themes()` is the
  id of every theme plugin in force in either layer, Latte first, with Latte
  and Plain always present. Removing a theme through `plugin.remove` puts
  each device kind that used it back on Latte, unless a shipped copy of that
  id takes over.

## Install from a link

`POST /v1/plugins` with a GitHub link to a folder at a tag or a commit:
`https://github.com/<owner>/<repo>/tree/<tag or commit>/<folder>`, a
`/commit/<sha>` or `/releases/tag/<tag>` link, or `owner/repo`, with `ref`
and `path` beside it if the link does not carry them. The folder may be the
repository's root; the installed folder is named by the manifest's `id`.

1. **The first call** fetches it (a shallow `git fetch` of that commit into
   `.staging/`), reads it, and answers what it is and what it declares,
   with a `confirm` token. Nothing is written.
2. **The second call** sends the same link with `"confirm": <token>`. It
   fetches again and recomputes the token from the id, version, repository,
   folder, commit, declaration and what it would replace. Equal: it is moved
   into Yours and the line is written. Not equal (the tag moved, a later
   version, the Yours copy changed): a 409 with the new answer under
   `preview`, and nothing installed.

Every install takes the second call, updates included. A later version's
first answer says under `added` exactly what it declares beyond what was
confirmed before (new hosts, a higher `record` or `memory`, server code, new
overrides, page code); the earlier token never installs it.

- **A branch is refused**, with the commit it is on now: a branch moves, and
  an install is pinned. A short commit is refused; name all 40 characters.
- **The declaration** is the manifest's `touches` and `overrides`, with
  `touches.server` true when `server/` is there whether or not it is
  declared, and `page_code` when `page/` is there. The host shows what it
  sees beside what is declared.
- **Side-loaded plugins have no rules** (the owner, 2026-11-12). Nothing is
  refused for what it declares or holds; the list shows where each came from.
- **Private repositories**: anonymous first; then the GitHub App's read
  token for that repository, when the app is installed on its owner
  (`loam_github_app.read_token`). The box's own git config (rewrites,
  helpers) is not used.

## Server plugins (2026-11-13)

The owner, m259: a plugin with server code **runs as its own program beside
Loam, never inside the broker**; Loam starts it, gives it an address and
restarts it if it dies, and installing, updating, turning off or crashing one
touches only that plugin. No containers for plugins (deferred). The reason
first given, that nobody should need Docker to use Loam, no longer holds:
Docker is a system dependency the installer adds, because the login, the
personas' browser and the app already run as containers (decided
2026-11-14). The deferral now rests on its other reason alone, that a
supervised program is enough until review proves too weak. The code is
`lib/loam_plugin_servers.py`.

**One plugin, one transient user unit**, `loam-plugin-<id>.service`, made by
`systemd-run --user` the way an engineer job is: its own cgroup under the
user manager, never the broker's. *Why a transient unit and not a static
one*: it is the box's existing pattern for "a process Loam starts on
demand" (every engineer job), it needs no unit file to install or drift, and
systemd supervises it (restart, memory cap, journal) with nothing of ours in
between.

| When | What happens to its unit |
|---|---|
| it is on and has `server/` (a switch, an install, `bin/settings plugin.on`) | started, if it is not running |
| turned off | stopped |
| removed | stopped before its folder goes |
| updated (install over it) | stopped and started fresh |
| `POST /v1/plugins/<id>/restart` (`plugin.restart`, `bin/plugin-servers restart <id>`) | stopped and started fresh |
| a reboot, a user-manager restart | `loam-plugins-reconcile.timer` (20 s after start, then every 5 min) starts every wanted unit that is not loaded and stops loaded ones no longer wanted |
| it crashes | systemd restarts it after 2 s; **five starts within 60 s and it gives up**: `failing`, until someone restarts it or turns it off and on. The reconcile never restarts a failed unit, so a crash loop stays visible |

**What it gets.** Its command in its folder, a loopback port of its own kept
across restarts (9100–9299, `state/plugins/ports.json`), and:

| variable | value |
|---|---|
| `PORT`, `LOAM_PLUGIN_PORT` | the port to listen on, on `127.0.0.1` |
| `LOAM_PLUGIN_ID` | its id |
| `LOAM_PLUGIN_DIR` | its folder |
| `LOAM_PLUGIN_BASE` | `/x/<id>/`, where the app reaches it |
| `LOAM_PLUGIN_DATA` | `<Yours>/_data/<id>/`, its own files (made for it) |
| `LOAM_PLUGIN_SETTINGS` | `<Yours>/_settings/<id>/<its settings file, or settings.md>` |
| `LOAM_BROKER_URL` | the broker, as the app reaches it (`http://172.31.0.1:8932`) |
| `LOAM_BROKER_TOKEN_FILE` | the file holding the broker's bearer token: a path, so the token is never a unit property `systemctl show` prints |
| `LOAM_API` | the plugin interface version |
| `LOAM_VAULT_DATA` | `<system>/Loam/data/<id>`, the plugin's folder in the vault, relative to it (for the broker's Files routes); an update keeps it (docs/update.md). Not made until the plugin writes there. Since 2026-11-16 |
| `LOAM_VAULT_DATA_DIR` | the same folder as a host path |

**Limits**: `MemoryMax=512M`, no swap, `TasksMax=256`, `NoNewPrivileges`
(this user has sudo; a plugin's server never does), `OOMScoreAdjust=700`
(every agent run is 800 and the broker 100: under pressure a plugin goes
after the agents and before the broker). 512 MB on a 32 GB box is room for
a small server and a ceiling on a runaway; raise `MEMORY_MAX` when a real plugin needs more. **Honest limit**:
it runs as the same user as everything else, so the declaration is trust,
not a wall, as for page code (spec v0).

**The address: `/x/<id>/`.** Caddy sends `/x/*` on every `app_shell` host
(the page, `loam-ui.<domain>`, and its staging copy) to `bin/plugin-gateway`
on `127.0.0.1:8933`, inside the same handles as the page, so the same login
guards it (`lib/loam_deploy.PLUGIN_GATEWAY_PORT`). The gateway is its own
unit (`systemd/loam-plugins-gateway.service`, 128 MB) and runs no plugin
code: it reads the port table and passes the request to the plugin's port
with `/x/<id>` taken off the path and given as `X-Forwarded-Prefix`, so a
plugin serves `/` and its relative links work. One request per upstream
connection (`Connection: close`), a WebSocket's upgrade piped through.
`/x/<id>` is a 308 to `/x/<id>/`; an id with no server is a 404; a server
that does not answer is a 502 that says it is off, starting or failing.
**What protects a plugin's server from other pages** (2026-11-18): the
gateway refuses with a 403 any request that could change something -- every
method but GET, HEAD and OPTIONS, and any upgrade -- when the browser says it
came from another origin (`Sec-Fetch-Site` other than `same-origin` or
`none`, or an `Origin` that is not the host's), the app's own rule for
`/api/*`; and Caddy lets the agents' browser past the login for such a
request only when it is same-origin (`deploy/README.md`, "The personas'
sight"). So the app's page and the plugin's own page under `/x/<id>/` can
call it, and a page on any other site, a sibling subdomain included, cannot.
What it does not protect: a GET that changes something (keep every GET
safe), a request with no browser headers at all (a tool on the box), and
the share door, `/s/*`, whose visitors come from anywhere and which a
plugin guards with its own token.
*Why a gateway and not the page's relay or the broker*: the relay is the
page's code and would put every plugin's traffic through the app's
container and the broker; Caddy alone cannot map a path to a port that
changes; a gateway of fifty lines keeps the broker out of a plugin's
traffic and the login exactly where it is.

**Seen in the list.** A row with a server carries `service`:

```json
"service": {"state": "running", "wanted": true, "unit": "loam-plugin-echo.service",
            "port": 9100, "url": "/x/echo/", "pid": 123456, "restarts": 0,
            "since": "Thu 2026-11-13 18:02:11 PST", "memory_max": "512M",
            "run": "python3 server/main.py"}
```

`state` is `running`, `starting`, `failing` (systemd gave up, or it is
crashing and about to retry), `stopping` or `stopped`; `wanted` is whether it
should run (on, in force). A failing row adds `log`, its last 20 journal
lines; `GET /v1/plugins/<id>` always adds them. A row that is not in force
says `stopped` with a `why`. Where units are not managed (a test, with Yours
elsewhere and no `LOAM_PLUGIN_UNITS=1`) the state is `unmanaged`.

`bin/plugin-servers status [<id>]`, `restart <id>`, `reconcile` and `ports`
are the same from a shell. The first-party example is
`docs/examples/plugins/echo/`: an `app` whose server answers with what it
was asked and whose `/crash` exits on purpose.

---

## The page's contract

Everything the page needs, with the broker's real answers. The page reaches
these through its relay as `/api/…` → `/v1/…`, like every other route.

### The relay: three route families

```js
rest === '/plugins'                          // GET the list, POST an install
/^\/plugins\/([^/]+)$/                       // GET one, PATCH {on}, DELETE
/^\/plugins\/([^/]+)\/files\/(.+)$/          // GET a plugin file (bytes)
/^\/plugins\/([^/]+)\/restart$/              // POST: restart its server (2026-11-13)
```

A plugin's own address, `/x/<id>/`, is **not** relayed: Caddy sends it
straight to the plugin gateway on the page's own host, behind the same
login (above, "Server plugins"). The page links to or frames `/x/<id>/` as
it is.

Spell them exactly so (the registry's `PAGE_RELAY` in
`lib/loam_settings.py` classifies them by these strings; they sat in
`PAGE_RELAY_AHEAD` until loam-ui's relay carried all three, 2026-11-12). The first two are JSON: relay
them with `relayJson`, passing `by` through untouched. **Allow an install's
POST several minutes**: it fetches from GitHub, usually in a second or two,
and the persona tool allows 420 s. The files route is **bytes, not JSON**:
relay the body as it comes, with the broker's `Content-Type`,
`Cache-Control: no-cache` and `X-Content-Type-Options: nosniff`, and encode
each path segment (`encodeURIComponent`) on the way. `GET` and `HEAD` only.

### `GET /api/plugins` — the list

```json
{
  "api": 0,
  "layers": {"shipped": ["/home/loam/projects/loam-ui/public/themes",
                         "/home/loam/projects/loam-ui/public/plugins"],
             "yours": "/home/loam/plugins"},
  "plugins": [
    {"id": "latte", "layer": "shipped",
     "folder": "/home/loam/projects/loam-ui/public/themes/latte",
     "broken": null, "warnings": [],
     "name": "Latte", "version": "1.0.0", "author": "Loam contributors", "api": 0,
     "kind": ["theme"], "places": [], "overrides": [],
     "touches": {"network": [], "record": "none", "memory": "none", "server": false},
     "settings": null, "forked_from": null, "format": "manifest.json",
     "has": {"page": false, "page_main": false, "server": false, "assets": false,
             "theme": true},
     "declaration": {"touches": {"network": [], "record": "none", "memory": "none",
                                 "server": false},
                     "overrides": [], "page_code": false},
     "source": {"kind": "loam"},
     "effective": true, "shadows": null, "shadowed_by": null,
     "in_use": ["phone", "desktop"], "switch": "appearance", "on": true,
     "files": "/v1/plugins/latte/files/"},
    {"id": "plain", "layer": "shipped", "…": "as Latte",
     "in_use": [], "switch": "appearance", "on": false,
     "files": "/v1/plugins/plain/files/"},
    {"id": "pet", "layer": "yours",
     "folder": "/home/loam/plugins/pet",
     "broken": null, "warnings": [],
     "name": "A pet", "version": "1.0.0", "author": "A friend", "api": 0,
     "kind": ["extension"], "places": ["app.overlay", "settings.section"],
     "overrides": [],
     "touches": {"network": [{"host": "sprites.example.com",
                              "why": "fetches the pet's sprite sheet"}],
                 "record": "none", "memory": "none", "server": false},
     "settings": "settings.md", "forked_from": null, "format": "PLUGIN.md",
     "has": {"page": true, "page_main": true, "server": false, "assets": true,
             "theme": false},
     "declaration": {"touches": {"network": [{"host": "sprites.example.com",
                                              "why": "fetches the pet's sprite sheet"}],
                                 "record": "none", "memory": "none", "server": false},
                     "overrides": [], "page_code": true},
     "source": {"kind": "link", "repo": "friend/loam-pet", "path": "pet",
                "ref": "v1.0.0", "ref_kind": "tag",
                "commit": "25acaa810d3e9d08c79d2736f749873a56607717",
                "url": "https://github.com/friend/loam-pet/tree/25acaa810d3e9d08c79d2736f749873a56607717/pet"},
     "installed": {"installed_at": "2026-11-12T19:06:18Z", "installed_by": "ada",
                   "version": "1.0.0", "confirmed": {"…": "the declaration said yes to"}},
     "edited": false,
     "effective": true, "shadows": null, "shadowed_by": null,
     "in_use": [], "switch": "plugin", "on": true,
     "files": "/v1/plugins/pet/files/"},
    {"id": "wrong", "layer": "yours", "folder": "/home/loam/plugins/wrong",
     "broken": "`id` is 'other' but the folder is 'wrong'; a plugin's folder is named by its id",
     "warnings": [], "source": {"kind": "built"},
     "effective": false, "shadows": null, "shadowed_by": null,
     "on": false, "switch": null, "in_use": [], "files": null}
  ]
}
```

One row per **folder**, not per id: a shipped plugin a Yours copy shadows is
its own row with `effective: false`, `shadowed_by: "yours"`. Draw the
`effective` rows (and the broken ones), and mark the rest from them.

| Field | Meaning |
|---|---|
| `source.kind` | `loam` (shipped), `link` (installed from a link: show `repo` at `ref`, linking `url`), `built` (written in Yours by hand or by an agent) |
| `effective` | this folder is the one in force for its id |
| `shadows` | (a Yours row) it replaces a shipped plugin: `{layer, version, folder}` — mark it "changes a shipped plugin" |
| `shadowed_by` | (a shipped row) a Yours copy is in force instead |
| `overrides` | core component ids it replaces — mark them wherever the plugin is shown |
| `touches`, `declaration` | what it declares; `declaration` adds what the host sees (`server` when `server/` exists, `page_code` when `page/` does) |
| `switch` | `"plugin"`: a toggle, `PATCH {on}`. `"appearance"`: a theme; "Use" sets `appearance.<device>.theme`. `"listed"` (2026-11-13): an `agent` or `skill` package, shown, no control. `null`: broken, no control |
| `service` | (2026-11-13) a row with a server: its state and address (above, "Server plugins"); show `state`, and `log` when `failing`, with a Restart for `POST /api/plugins/<id>/restart` |
| `icon` | (2026-11-13) a path in the folder, served at `files` + `icon` |
| `on`, `in_use` | on or off; for a theme, on where `in_use` names a device kind |
| `broken` | why it is not a plugin, or `null`. Show it; never hide the row |
| `warnings` | what it names that spec v0 does not, or what it holds and does not declare |
| `edited` | (a linked row) changed in place since it was installed |
| `has.page_main` | it has `page/main.js` for the loader |
| `error` (top level) | the installed list could not be read; the rows are still right about folders |

### `GET /api/plugins/<id>` — one

`{"plugin": <the row in force, or the first broken row>, "rows": [<every row
of that id>]}`; the row adds `guide`, the body of its `PLUGIN.md` (markdown,
or `null`). 404 `{"error": "no plugin 'ghost' in either layer"}`.

### `PATCH /api/plugins/<id>` — on and off

Request `{"on": false}`; answer as `GET /api/plugins/<id>`. Refusals, each
`{"error": "<a sentence>"}`:

- 409 `"plain is a theme: it is on where Appearance has chosen it. Choose it with PATCH /v1/settings/appearance (appearance.desktop.theme, appearance.phone.theme)"`
- 409 `"dusk is broken, so it cannot be turned on or off: …"`
- 400 `"a plugin's one setting is `on`: {\"on\": true}"`; 404 for no such plugin.

### `POST /api/plugins` — install from a link

First call, request `{"url": "https://github.com/friend/loam-pet/tree/v1.0.0/pet"}`:

```json
{
  "installed": false,
  "id": "pet", "name": "A pet", "version": "1.0.0", "author": "A friend",
  "api": 0, "kind": ["extension"], "places": ["app.overlay", "settings.section"],
  "source": {"kind": "link", "repo": "friend/loam-pet", "path": "pet",
             "ref": "v1.0.0", "ref_kind": "tag",
             "commit": "25acaa810d3e9d08c79d2736f749873a56607717",
             "url": "https://github.com/friend/loam-pet/tree/25acaa810d3e9d08c79d2736f749873a56607717/pet"},
  "declaration": {"touches": {"network": [{"host": "sprites.example.com",
                                           "why": "fetches the pet's sprite sheet"}],
                              "record": "none", "memory": "none", "server": false},
                  "overrides": [], "page_code": true},
  "added": {},
  "replaces": null,
  "shadows": null,
  "warnings": [],
  "confirm": "cd383308cd35f365"
}
```

Show the person the declaration — each host and its `why`, `record`,
`memory`, `server`, `overrides`, `page_code` — and where it comes from
(`repo`, `ref`, `commit`). Also, when present:

- `added` — what this version declares beyond what was confirmed for the
  installed one (`{"network": [{"host": "tracker.example", "why": "counts pets"}], "memory": "read", "server": true}`): "This version also…".
- `replaces` — `{version, source, edited, broken}` of the Yours copy it would
  replace; `edited: true` means local changes are replaced (they stay in
  the Yours folder's git history).
- `shadows` — `{layer: "shipped", version}`: it would replace a shipped plugin.

Second call, request `{"url": <the same link>, "confirm": "cd383308cd35f365"}`:

```json
{"installed": true, "id": "pet", "plugin": {<its row, as in the list>},
 "replaced": null, "source": {<as above>}}
```

A token that is not the current answer's: 409
`{"error": "`confirm` is not what pet answers now (its declaration, its commit and what it would replace give cd383308cd35f365). Nothing was installed; read the answer under `preview` and confirm it if it is a yes", "preview": {<a first-call answer>}}`
— show `preview` as the new question.

Other refusals are sentences: 400 for a branch (`"'main' is a branch, and a
branch moves: install at a tag, or at the commit it is on now (7fd1a60…)"`),
a short commit, a link that is not GitHub's or is a file; 404 for no such
tag or folder; 422 for a folder that is not a plugin (`"… is not a plugin:
no PLUGIN.md (a plugin is a folder with one)"`); 502 when GitHub cannot be
read; 504 when it does not answer.

### `DELETE /api/plugins/<id>` — remove from Yours

```json
{"removed": "pet",
 "was": {"version": "1.0.0", "source": {<its source>}},
 "appearance_reset": [],
 "plugin": null,
 "installed_line": true}
```

`plugin` is the row now in force for the id (a shipped one that takes over),
or `null`. `appearance_reset` names the device kinds put back on Latte
because their theme went; re-read Appearance when it is not empty. A shipped
plugin: 409 `"read-aloud is shipped with Loam, and an update would bring it back: turn it off instead"`.

### `GET /api/plugins/<id>/files/<path>` — a plugin's files

The **stable path a plugin's page code is served at** is

```
/api/plugins/<id>/files/page/main.js
```

and everything under `page/` and `assets/`, plus `theme.css`,
`PLUGIN.md` and a theme's covers (the files its `image` and `image_dark` name), beside it. The bytes come from the folder in force for the id
(Yours over Shipped). Types: `.js`/`.mjs` `text/javascript`, `.css`
`text/css`, `.json`, `.svg`, images, fonts, audio, video, `.wasm`; anything
else `application/octet-stream`. `server/`, any other file, `..`, a link
pointing out of the folder, and a broken plugin are 404. Files are served
whether the plugin is on or off; whether to run it is the page's decision.

### What the page must do to load one

1. **Read the list** at start: `GET /api/plugins`.
2. **Code**: for every row with `effective && on && switch === "plugin" &&
   has.page_main && api <= 0`, `const mod = await import('/api/plugins/' + id +
   '/files/page/main.js')`, then `await mod.activate(loam)`. Relative imports
   inside it (`./lib/util.js`, `new URL('../assets/cat.svg', import.meta.url)`)
   resolve under the same path, so a plugin needs no build step and no
   knowledge of where it is served. A failing import or `activate` marks that
   plugin failed in Settings › Plugins and does not stop the page or another
   plugin. This is same-origin: the page's `script-src 'self'` already covers
   it; nothing needs `'unsafe-eval'` or a new origin. Skip a row whose `api`
   is newer than the page's interface.
3. **Themes**: the theme choices are the rows whose `kind` includes `theme`
   and that are `effective` — not only the page's own `public/themes`. For a
   theme whose row is in the Yours layer (including a Yours copy of Latte or
   Plain, which wins over the page's own), fetch
   `/api/plugins/<id>/files/theme.css`, hold it to `validateThemeCSS` as the
   page does its own, and use it in place of any built-in of that id. A
   shipped theme's `theme.css` is the same file the page already has.
4. **Settings › Plugins**: one entry per effective row (and per broken
   row), showing `name`, `version`, `author`, where it came from
   (`source`), what it touches (`declaration`), `overrides` and `shadows`
   marked, `warnings`, `broken`, `edited`; the toggle by `switch`; Remove
   for `layer === "yours"`; and an Install field taking a link, which shows
   the first answer and installs on the person's yes with its `confirm`.
5. **Re-read** the list after any of the three writes, and when the page
   comes back into focus (a persona may have changed it); there is no stream.

The same changes are a persona's through `bin/settings`
(`plugin.on`, `plugin.install`, `plugin.remove`, and since 2026-11-13
`plugin.restart`), calling these routes with `by`; they are refused from a
wake or a cron.

### What loam-ui would change to make its themes `PLUGIN.md` plugins

Nothing is needed now: the host reads `manifest.json` theme folders as they
are. When loam-ui wants one shape, each of `public/themes/latte/` and
`public/themes/plain/` gains a `PLUGIN.md`:

```yaml
---
id: latte
name: Latte
version: 1.0.0
author: Loam contributors
api: 0
kind: [theme]
---

# Latte

Loam's own look, light and dark: the latte-and-newspaper palette.
```

and `src/themes.js`'s `loadThemes` reads `PLUGIN.md` (or takes the theme rows
from `GET /api/plugins`, step 3 above), after which `manifest.json` can go.
Until the loader changes, keep `manifest.json` beside it (the host reads
`PLUGIN.md` first and warns that both are there).

## The box overlay (designed 2026-11-14; nothing built yet)

Some of an install is the owner's alone and runs as root: a signer's socket
and service, anything else a system unit does. A plugin's processes run as
the user, with no sudo (above), so no plugin can install that part itself.
Today the owner installs it by hand from the plugin that carries it (below,
"Still not built"). The design for when that is not enough is **the box
overlay**: a git repository of the owner's own, beside Yours, that Loam
reads and never ships.

```
overlay/
  OVERLAY.md            what it is for, in words, as a plugin's guide is
  system/               system units, installed to /etc/systemd/system
    <name>.service      as written; no tokens, no rendering (they are the owner's)
    <name>.timer
  post-install          optional: run as root after an install or an update
```

- **`paths.overlay`** in `/etc/loam/loam.toml` names it; absent means none.
- **`bin/overlay apply`** (sudo) copies `system/` units that differ, runs
  `daemon-reload`, enables a new timer by the reload-only rule
  (`loam_units.arm_timer`), and runs `post-install`. The installer and the
  updater call it last.
- **`check-drift`** compares the installed units with the overlay's, as it
  compares the repository's own.
- **Nothing in the overlay is a plugin's**, and the overlay holds nothing a
  plugin could hold. A user-level timer, an MCP tool, an engineer variable
  or a listener is a plugin's `contributes:`.

What L5 needs from it is nothing. The owner's own tools (headroom, sandbox,
Cloudflare, a file-sync listener) all run as the user: their tools, timers,
settings, variables and listener are `contributes:` entries in the owner's
own plugins.

**Still not built (2026-11-14, first release L6).** The first root part to
leave the runtime was a federation signer: a socket and a service in
`/etc/systemd/system`, a root-owned program, a policy in `/etc/loam/`. It
went into a plugin of the owner's, not an overlay, because one install
script carried in the plugin, run once by hand with sudo, is all it needs,
and the plugin's `drift.check` compares the installed copies with its own
the way `check-drift` would compare an overlay's. Its job side is the
plugin's `engineer.job` hook. The overlay is worth building when a second
root part needs installing after every update, or when an installer has to
carry one; until then it stays a design.

## The share door (2026-11-16)

The one path of the app's host that does not meet the owner's login:
`/s/*`. It exists for share links (doc-comments: an outside person opens a
doc by a password-protected link) and is general: **a plugin opts in with
`contributes: web.door: [{why: …}]`**, and the first plugin in force by id
that has one holds it. The `why` is shown in the plugin's declaration before
it is installed (`open_web`), because it is the plugin's reach onto the
open web.

- **Caddy** (lib/loam_deploy.py, `caddy_host_block`, `app_shell` hosts only:
  Loam's page and its staging copy) sends `/s/*` to the plugin gateway's door
  port, `PLUGIN_DOOR_PORT` (8936), with no `import gate`. A `handle /path` is
  ordered ahead of every other handle on the host, so nothing else changes
  order. `/robots.txt` on the same hosts answers `Disallow: /`.
- **The gateway** (bin/plugin-gateway, `door`) answers only `/s/<token>…`
  (32 to 64 of `A-Z a-z 0-9 _ -`) and only for the door's holder, as
  `/door/<token>…` on the plugin's own port with `X-Loam-Door: 1`. It passes
  an allowlist of headers: `Host`, `Content-Type`, `Content-Length`,
  `Accept`, `Accept-Language`, `User-Agent`, `Origin`, `X-Forwarded-For`,
  `X-Loam-Share`, the one cookie `loam_share`, and `Authorization` only when
  it is `Bearer lak_…`. Never the login's cookie, never a header naming a
  user. No upgrade, no chunked body, at most 2 MiB. Anything else is a 404.
- **On the login's port** (`/x/…`) the gateway takes `X-Loam-Door` off, so
  only the door's port can say a request came through the door.
- **The plugin** must treat a request marked `X-Loam-Door: 1` as a
  stranger's: it answers only its `/door/` routes for those, decides from
  its own credentials (doc-comments: the link's password and its session,
  or an agent key) and never from a path, a root or a header the visitor
  sent. Unmarked requests on `/door/` are refused.

Put back: remove the plugin's `web.door` (or turn it off) and the door
answers 404 for everything; to take the path itself away, drop the
`app_shell` block that writes `handle /s/*` in `caddy_host_block` and run
`sudo bin/gen-deploy` (it rewrites /etc/caddy/Caddyfile and reloads Caddy;
**it has no `--help`: any run is live**).

## The marketplace (2026-11-17)

Decided 2026-11-13: **one public GitHub repository holds a list file.**
Builders host their own work in their own repositories; they ask to be
listed by pull request; a maintainer reviews every listing by hand; every
Loam reads the list in Settings › Plugins and installs from it by the
listed link, through the same two calls as any link (above). Installing from
any link still works, listed or not. The code is `lib/loam_marketplace.py`
and `bin/loam-market`; the tests are `tests/test_marketplace.py`.

### The list: `list.json`

```json
{
  "version": 1,
  "plugins": [
    {
      "id": "pet",
      "name": "A pet",
      "description": "A small animal that sleeps in the corner of the page.",
      "author": "A friend",
      "kind": ["extension"],
      "version": "1.0.0",
      "link": "https://github.com/friend/loam-pet/tree/v1.0.0/pet",
      "homepage": "https://friend.example/pet"
    }
  ]
}
```

| Field | Rule |
|---|---|
| `version` (top) | the format, `1`. A list of another format is not read (`unreachable`, with why) |
| `id` | the plugin's id, lower case, digits and dashes; once per list |
| `name`, `author` | a line of text, at most 60 characters |
| `description` | one sentence, at most 200 characters (required in the repository; optional to a reader) |
| `kind` | one or more of `theme`, `extension`, `app`, `agent`, `skill` |
| `link` | the plugin's folder at a tag or a full commit, as an install link takes it; a branch is refused when it is installed |
| `version`, `homepage` | optional: the version at the link; an https link about it |

A Loam leaves out an entry that breaks a rule, says why in `skipped`, and
draws the rest, sorted by name.

### Where it is read from

`marketplace.list` in loam.toml, default
`https://raw.githubusercontent.com/loamhq/marketplace/main/list.json`; an
empty string turns the section off. `marketplace.repo` (default
`loamhq/marketplace`) is where a listing is asked for.

### `GET /api/marketplace` (relayed to `GET /v1/marketplace`)

```json
{"url": "https://raw.githubusercontent.com/loamhq/marketplace/main/list.json",
 "state": "listed", "checked_at": "2026-11-18T05:40:00Z", "message": null,
 "repo": "loamhq/marketplace", "skipped": [],
 "plugins": [{"id": "pet", "name": "A pet", "description": "…", "author": "A friend",
              "kind": ["extension"], "link": "…", "repo": "friend/loam-pet",
              "ref": "v1.0.0", "path": "pet",
              "installed": false, "installed_version": null, "installed_from": null}]}
```

| `state` | Meaning | The page |
|---|---|---|
| `listed` | entries to draw | a row each: name, description, author · kind, and **Get** (or *Installed*) |
| `empty` | no list there yet (404) or nothing in it | "Nothing is listed yet. Anything shared as a link installs below." Never an error |
| `unreachable` | the host failed, or the list is not one; `message` says why | the same calm line, saying it can't be reached right now |
| `off` | `marketplace.list` is empty | the section is not drawn |

The broker keeps the answer ten minutes; `?refresh=1` reads again. **Get**
puts the entry's `link` through `POST /api/plugins` without a confirm: the
person sees the same declaration question as for any link and says yes to
install. *Installed* is worked out on the page from the plugin list, so it
is right the moment an install lands.

### Asking to be listed

The repository holds one file per listing, `listings/<id>.json` (an entry
as above), and `list.json`, which its workflow builds from them when a pull
request is merged (`build.py`; `build.py --check` on every pull request,
with the same rules a Loam applies). One file per listing is what lets a
request be one click: `loam market request <link> --description "…"` reads
the plugin from its link (the first call of an install: nothing is written),
and answers the entry and a link to
`https://github.com/loamhq/marketplace/new/main?filename=listings/<id>.json&value=<the entry>`.
The owner opens it signed in to their own GitHub account and presses
Propose; for someone who cannot write to the repository, GitHub makes the
fork and the pull request. Nothing is sent from the box, and the owner's
press is the yes.

The repository's content (README with the review bar, the empty list,
`build.py`, the workflow, the pull request template) is `docs/marketplace/` in
this repository, until the repository exists.

### Sharing by link: `loam market share`

`loam market share <id> <owner>/<name>` pushes a plugin from the owner's
plugins folder to a repository the owner made on github.com, as `<id>/` at
the tag `v<version>`, through the owner's connected GitHub (an installation
token per push, from `bin/git-credential-loam`; never written down). It
answers the install link, and says when the repository is still private.
Only that folder changes in the repository; a tag that exists is refused, so
a new share is a new version. Making the repository public is the owner's
direct yes on github.com; nothing here changes a repository's visibility.
