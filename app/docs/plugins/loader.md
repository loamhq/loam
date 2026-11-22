# Loading, configuring and authoring plugins

Start with [the stable parts](../stable-parts.md), [types](../../public/loam.d.ts),
[personal layers](../plugins.md), [the places contract](places.md) and [module map](modules.md).
A plugin is a folder, not a build artifact. The host parses `PLUGIN.md` and
supplies its catalog; the page never parses that manifest. A module exports
`activate(loam)` and imports its own helpers relative to `page/main.js`.
`new URL('../assets/file.svg', import.meta.url)` addresses a bundled asset.

The initial list and each focus/write refresh use `/api/plugins`. Only
`effective && on && switch === 'plugin' && has.page_main && api <= 0` rows
execute. Shadowed and disabled copies do not execute; broken copies remain
visible. Effective/broken rows appear in Settings, grouped Themes/Extensions.
Source, author/version, overrides, shipped-copy changes, local edits, warnings
and failures remain visible. Themes use Appearance, with no second toggle.

Shipped activations progress independently; Yours starts after they settle.
Registration and stylesheet precedence also retain that layer order. Core boot does not wait to start
its own requests/streams. Import and activation each have a 15-second startup
bound, and cleanup is bounded too. Plugins should register promptly and load
long-running work asynchronously, owning cancellable resources. Failure
releases registrations, commands, listeners and service providers, then marks
the row. Closed scopes reject late registrations. Disabling during activation
releases handles immediately; a late completion cannot restore them. Native
module evaluation itself cannot be forcibly cancelled.

List failures retain already active plugins and their last catalog. An initial
404/405 identifies an older host and loads shipped modules from this app. On
other initial failures, metadata/themes still fall back, but code stays off
with enabled state unknown: a temporary outage must not undo a saved disable. `/shipped-plugins.json` derives that
fallback from the shipped `PLUGIN.md` files, not a second authored catalog.
Each shipped manifest declares a boolean `default_on`: audio extensions are
off, themes (including Latte) on. These are fresh fallback defaults, never
updates to an existing runtime row’s `on`. The runtime must seed absent state
from that declaration and preserve every saved choice; see the
[installation handoff](../installation.md#runtime-handoff).
Its server parser reads our shipped subset; third-party manifests belong to
the host parser. A repaired catalog replaces the fallback on the next refresh.

## Relay

- `GET /api/plugins`, `POST /api/plugins`: list/install. Installs get 420 seconds;
  catalog reads get a five-second upstream bound.
- `GET`, `PATCH {on}`, `DELETE /api/plugins/<id>`: one plugin, enabled state,
  removal. The page rereads the list after writes, including Appearance saves.
- `GET /api/plugins/<id>/agents[/<agent>]`, `PATCH /api/plugins/<id>/agents/<agent>`:
  declared per-agent values in the host store; PATCH sends `{values: {...}}`.
- `GET`/`HEAD /api/plugins/<id>/files/<path>`: byte relay, independently encoded
  path segments, original content type/cache policy and `nosniff`. File reads
  get 30 seconds. Other methods are refused.

The server adds the broker bearer. No CLI or broker credential reaches a
plugin. Browser requests retain same-origin request checks. Module/style code
loads from this origin; inline scripts and eval remain disallowed. Extensions
can package helpers and styles in their own `page/`/`assets/` folder.

Installed page code is trusted, not sandboxed. Network declarations are
information, not an allowlist: HTTP(S)/WebSocket data, images, media, fonts and
frames use ordinary browser rules/CORS. This applies to core and third-party
code equally. The Files HTML preview/content-origin policies remain separate
and restrictive. The normal Apps UI retains its own framing behavior; a plugin
can supply a different view through its public place/component interface.

## Installation and updates

A first POST sends `{url,ref?,path?}` and displays the declaration, source/ref/
commit and replacement/shadow information. For an update, added declarations
come first; the complete declaration is collapsed. Local edits are explicitly
called out before replacement. Install sends the exact original request plus
its `confirm` token. A 409's `preview` replaces the question and requires a new
click; it is never automatically confirmed. Refusal sentences are shown
unchanged. Remove is offered for Yours copies and rereads Appearance when the
host reports `appearance_reset`.

## Themes and options

Both built-ins now use `PLUGIN.md`, `api: 0`, `kind: [theme]` and `theme.css`.
The two `public/themes/*/manifest.json` files were removed; the page/server
have no runtime reader for them. The host should remove its legacy reader.

Shipped token themes and their previews keep the strict token validator.
Effective Yours rows contribute `theme.css` through ordinary stylesheet links,
after defaults; extensions can also load plain CSS through `ui.styles.add`.
Their selectors and values are unrestricted. Appearance still validates theme
options and reads metadata from the host; it never parses user manifests. A
user theme with arbitrary component rules can exceed the server token-preview
format; those rules apply in the page even when the generated preview uses
fallback tokens. This is not a host validation change.

Concurrent theme-options work is preserved. `options` is valid YAML using an
inline JSON array in shipped frontmatter, for example:

```yaml
options: [{"id":"texture","label":"Texture","default":"smooth","choices":[{"id":"smooth","label":"Smooth"},{"id":"grain","label":"Grain"}]}]
```

**Host follow-up:** forward optional `options` metadata on theme rows (and
single-plugin/preview responses) from `PLUGIN.md`. The supplied v0 contract
omits that field. Shipped options can be recovered from this app's own
metadata, but Yours options must come from the effective host row; the page
will not guess or parse a third-party manifest. Unknown option scopes are
rejected by the validator. Existing appearance option values continue through
the host's Appearance route.

## API 0 limits and next interface changes

- Generic `loam.settings` remains scoped browser-local storage with memory
  fallback. Per-agent fields use the host-backed `loam.data.agentSettings` API
  and `agent.profile` place. Existing device audio preferences retain their
  previous storage through the public adapter, preserving upgrades.
- The public `ui.host` facade remains open. Named stable operations and the
  local `ui.extension` adapter are typed; other compatibility members are
  allowed without a promise. Audio uses local adapters, places and events,
  with no privileged or private first-party path.
- List/manifest changes trigger entry reactivation. Native ES modules cache
  relative dependencies for the page lifetime. Reload the page after editing
  dependency files in place. A host-provided tree revision and versioned file
  namespace would let focus/update reload all changed dependencies without
  interrupting unrelated playback/capture. A boolean `edited` is insufficient
  to detect a second edit while already edited.
- Events describe the active thread and this page's successful Files writes.
  External/global file changes need a host event source; this page invents no
  polling or second opinion about those changes.
- `thread.kind` remains reserved. The section API has generic navigation and
  guarded leave behavior; a complete plugin route/lifecycle contract remains
  a useful next seam.

## What is still core

Screen routing, core overlays, navigation entries, Settings sections, component
renderers, footer actions, composer tools, file toolbar/viewers, Activity feed
kinds and Apps groups are dispatched through registrations. Core uses the same
`add`/command calls as plugins. `hidden`, `screens` and `frame:'full'` section
metadata are public too; Activity/profile are hidden destinations, not private
routing branches. Core registers its dialogs/editor/notices in `app.overlay`.
Whole composer/Activity overrides receive a `variant` for editor/dialog/notices
as well as the main view.

The auth-expired gate and platform infrastructure stay outside the registry:
API/auth, routing/history parsing, viewport/redraw/morph, keyboard/page lifecycle,
focus/menu placement and shared primitives/protocol normalization. Activity and
Settings utility buttons are inside the registered rail, rather than separate
`rail.section` entries. Account/sign-in forms, transcript tools/folds, Activity’s mixed-in chat rows and Files format branches are nested implementations of registered
components, not individually registered subcomponents. Built-in Markdown/text/
HTML/image/PDF selection remains inside `core.files.viewer`; specialized plugin
viewers run through `files.viewer` before that default body. This is not a claim
that every leaf control has its own place.

The earlier permission route removal was incorrect for threads: they still
raise cards and POST `/sessions/<id>/permissions/<request>`. That authenticated
relay route is restored, including host refusal sentences. Tests cover the seam.

## Stylesheet attachment during catalog refresh (2026-11-14)

`createPluginStyles.order()` reconciles only links whose position changes.
Reappending an already ordered link detaches its CSS until the browser finishes
another fetch, even when the same node and URL are retained. Catalog refresh
runs after ordinary writes as well as focus, so an unconditional append produced
recurring gray native controls. Insert a new default before Yours, and a pending
replacement directly after its current sheet; leave all correctly placed links
attached. Successful swaps remove only the old sheet. Failed or superseded
replacements and disabled plugins retain the existing cleanup rules.

See [the painted-frame regression](../evidence/thread-update-paint/README.md).
