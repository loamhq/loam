# Page modules

The page is native ES modules served directly; no bundler or build step.
`app.js` owns boot, API, the root state, routing, viewport, redraw scheduling and
page lifecycle. Each module mounts once against a shared page service facade;
getters preserve one state owner and initialization callbacks without copying
mutable values. The facade is not a credential or a write-policy boundary.

| Module under `public/` | Owner |
| --- | --- |
| `page/files.js` | Files controller, editing field, link/embedding adapters, picker, navigation guards |
| `page/accounts.js` | Provider sign-in and GitHub request state, polling and temporary fields |
| `page/composer.js` | The single draft, expanded editor, attachments, upload menus and composer rendering |
| `page/transcript.js` | Copy/fold state, tool details, turn facts and normalized step rendering |
| `page/settings.js` | Settings routes and views, account cards, appearance and Files preferences |
| `page/layout.js` | Navigator lists and the permanent desktop workspace; phone navigation and profiles |
| `panel-items.js`, `panels.js` | Shared registered panel renderer, item addresses, moves/uniqueness and split geometry |
| `page/side-panel.js`, `page/conversation.js` | Per-panel runtime services and conversation mounts |
| `page/sheets.js` | Sheet ownership and overlays; shared `menu-surface.js`/`sheet-gesture.js` own phone gestures |

The split preserves the original controller instances, rich draft nodes,
request ordering and lifecycle listeners. Browser fixtures observe existing
controllers through aliases in the shell; those aliases do not own state.
Source-location checks now read the owning module. The standard suite and the
8-variant computed-style/browser tour validate this behavior-preserving step.

Read aloud and Dictation mount their own controllers and keep helpers,
preferences, state and feature styles entirely in their plain-file packages,
which are Market plugins since 0.2.9 (copies in `../plugins/_listed/`;
`engine.js` in each owns capture/transcription and speech/players).
`loam.ui.extension` delegates shared page operations while local state stays
local. Public places, field/sheet contracts and lifecycle events connect them
to the page; no audio controllers or inert audio adapters remain in core.
See [stable parts](../stable-parts.md) and [types](../../public/loam.d.ts).

| Integration module under `public/` | Owner |
| --- | --- |
| `page/registrations.js`, `core-registrations.js` | Core sections/overlays/commands and default component registrations |
| `page/services.js` | Removable service providers, with inactive predecessors excluded |
| `plugin-registry.js` | Places, component dispatch, commands, events and scope ownership |
| `plugin-loader.js` | Catalog refresh, eligibility, imports/activation, failures and write operations |
| `plugin-settings.js` | Plugin list/details, install preview/confirmation and removal UI |
| `github-accounts.js` | Account rows, scoped refresh/disconnect, dependent-account confirmation and GitHub add-account steps |
| `plugin-themes.js`, `theme-css.js`, `plugin-styles.js` | Theme catalog/options, shipped token validation, ordinary ordered plugin CSS |
| `audio-session.js` | Browser audio focus shared by independently enabled audio plugins |

`threads.js`, `files.js`, `activity.js`, `apps.js` and the other existing
controllers keep their own domain state; the split did not move that state
into a second store. The shell imports neither audio package and owns no audio preferences.
The host facade is a compatibility seam with explicit property delegation, not
an independent state container or a special first-party API.
