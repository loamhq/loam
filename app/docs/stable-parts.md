# Stable parts, API 0

You may change anything in Loam: page modules, DOM, styles, behavior, defaults.
The public API is a promise, not a wall. The parts below are what Loam promises
to keep compatible across updates within one `api` version. Going beyond them
is allowed; those changes may need repair after an update. Keep your work in
Yours, apart from shipped defaults, so an update never overwrites it.

| Part | Promise |
| --- | --- |
| Places | All 14 names, registration/lifecycle rules and place contexts in [places.md](plugins/places.md). Both dotted and nested names work. New optional fields may be added. `thread.kind` is reserved. |
| `loam` | The API 0 signatures in [loam.d.ts](../public/loam.d.ts): `places`, `data`, `events`, `ui`, `settings`, `commands`, `own`, `dispose`, `api`. Named methods and explicitly listed `ui.host` members are stable; its open compatibility members are available without that promise. |
| Design tokens | Semantic CSS custom properties in [tokens.css](../public/tokens.css) and their [roles](design-system/tokens.md). Values vary with themes, mode and user CSS. Token names retain their roles. |
| Component ids | `core.thread-list`, `core.message`, `core.composer`, `core.files.viewer`, `core.files.editor`, `core.rail`, `core.activity`, `core.apps`, `core.settings`, with the owning places and contexts in [places.md](plugins/places.md#component-overrides). |
| CSS classes | `.pillbtn`, `.primary`, `.content-mark`, `.iconbtn`, `.ghost`, `.card`, `.note`, `.kicker`, `.display`, `.sheet`, `.setrow`, `.settings-section`. These retain their primitive meanings. No other class or DOM nesting is promised in API 0. |
| Layering | Shipped defaults load first. Yours runs afterward, wins equal component/command registrations, and its styles follow shipped styles. Host `effective` rows select same-id replacements. Disable/remove releases owned contributions and restores defaults. |

An override may name any component id; `overrides` in a manifest explains
intent, rather than granting permission. Settings marks both declared and
active replacements. Use the documented component ids for compatibility;
ordinary DOM/CSS edits remain possible. Native browser APIs and arbitrary
network/CORS access remain available to trusted code.

`loam.settings` is device-local, with memory fallback when storage is refused.
`device(key, normalize)` retains an existing device storage address; normal
`get/set` keys are scoped to the plugin id. Per-agent fields stay on the host
through `loam.data.agentSettings`, including the same route used by agents.
Nothing about titles, read marks or pins moves into device storage.

Public lifecycle events include `rendering`, `rendered`, `field.reset`,
`field.closed` (`{target}`), `field.input`, `settings.opened` (`{page, opening?}`),
`sheet.changing` (`{previous, sheet}`),
`key.escape` (the browser event; preventDefault to consume), `page.visible`,
`page.leaving`, and `health.changed`. `audio.capture` asks cooperating audio
extensions to pause playback. Existing thread, message, turn, file and section
events remain documented in [places.md](plugins/places.md). Listeners can emit
custom events through `loam.events.emit`; closed scopes do nothing.
Field lifecycle events may include `host`, identifying the owning panel;
without it they apply to main. `key.escape` includes `panel` on a two-panel
page. Composer-tool and file-action render contexts also identify their `host`. The open page
facade's `panelHosts()` lists the mounts; these addresses are views, not a
place for an extension's private state.

The `composer.tool` state slot returns `{active, phase?, level?}` for a target
(`draft`, `editor`, `file`). It lets any extension temporarily own a field's
surface and disable conflicting controls. Core reads this state and renders
`take`, `notice` and `microphone` contributions; it never calls that extension.
A sheet outside the core kinds renders through `app.overlay` slot `sheet`,
with `{sheet}`. A sheet's optional `dismiss()` owns its closing transition;
otherwise `closeSheet()` clears it immediately.

`ui.extension({state,store,helpers,host?})` creates a local controller adapter:
owned state keys stay in the extension, other state accesses delegate to the
page; helpers and attached controller methods take precedence over page
methods. An optional `host` selects a panel; omitting it uses main, preserving
single-panel extensions. It does not publish a controller to core. `ui.styles.add(file)`
loads a package-relative stylesheet, orders it by layer, returns a removable
handle and owns it for disable/failure. CSS is ordinary CSS, without a
selector or token allowlist. `data.messages.parts(message)` returns the same
normalized prose/tool segments the page uses.

The standard tests compare the type file's place/component names and data
methods against the runtime; browser checks exercise these contracts with
plain-file packages. This is a compatibility guideline, not a claim that every
internal module or nested UI control is individually registered.
