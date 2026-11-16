# Appearance

Settings opens on Appearance on desktop; Appearance is the first entry on the
phone. Theme preview cards show each installed theme’s paper, ink, controls and
display face. Latte & Newspaper (the default, id `latte`), Plain (the neutral
core fallback), Deep Forest, Autumn, Yosemite, Mist Trail and Foundry share the
picker with installed themes. The chosen theme’s named option group follows the
picker, then Mode and Size. Choices update immediately without a reload or
palette stylesheet fetch. A new display face loads on first selection. Latte & Newspaper offers Agent colours (Palette or Latte tones) and Agent
shapes (Each its own, Circle, Rounded square or Latte art). Circle is the
default; Latte art reveals its Inside and In a mug controls. Mode offers Light, Dark, Sun and
System; the duplicate rail toggle is removed. Size changes
live from 75% to 150%, in 5% steps; Reset restores 100%. Desktop Ctrl/Cmd plus,
minus and zero use the same setting, including while composing.

The device kind is the primary pointer: coarse is `phone`, everything else is
`desktop`. Tablets with a coarse primary pointer share phone preferences. A
resized desktop window keeps desktop preferences. This classification is separate
from the page's width-based responsive layout. Both modes default to `system`,
both sizes to 100, and both themes to `latte`; 90% is a choice, not a new default.

Theme cards support optional package-relative `image` and `image_position`,
plus `image_dark` and `image_position_dark`. Dark covers follow the resolved
lighting, including Sun and System; absent dark fields reuse the default.
Latte & Newspaper ships a 25 KB, 640×427 WebP; Plain demonstrates
the component fallback. Captions use each theme's display face, with a bottom
gradient over photos and a two-line clamp. The saved theme ID remains `latte`,
and options remain under `options.latte`. The [manifest contract](design-system/tokens.md)
and [captures and verification](evidence/theme-covers/README.md) include the
host-validation handoff.

The five additional first-party themes use the approved reading/control/primary
roles in both modes, with photo-tinted paper, Figtree body and JetBrains Mono.
Each offers **Agent faces: Signature, Each its own, Circle or Rounded
square**. Circle is the default, except Foundry, which defaults to Rounded
square. All explicitly saved choices keep their values, including Signature
and Latte art; only absent choices use these defaults (2026-11-14). Plain has
no face option and retains its existing per-agent shapes. Signature reveals
Inside: Mark (default) / Initials. The eleven original agent colours remain
available on the ordinary faces; signature faces use their material colours.
Foundry tightens radius tokens and component corners. Their display faces are:

| ID | Name | Display face |
| --- | --- | --- |
| `deep-forest` | Deep Forest | Fraunces 550, optical size 24 |
| `autumn` | Autumn | Young Serif 400 |
| `yosemite` | Yosemite | Zilla Slab 600 |
| `mist-trail` | Mist Trail | DM Serif Display 400 |
| `foundry` | Foundry | Big Shoulders Display 800 |

Latin and Latin-ext WOFF2 files and each SIL OFL license live inside the theme.
`theme-fonts.js` registers a face only when that theme is active. Inactive cover
captions use the declared serif fallback until their face has been activated;
opening Appearance does not download five fonts. The saved active theme's font
stylesheet enters the parser before first paint. No vendor is contacted by the app.
Autumn alone has separate daylight and evening photographs.

Theme selection, `options.<theme-id>.agent-shapes` and
`options.<theme-id>.face-content` use the existing
`GET/PATCH /api/settings/appearance` route for each device kind. Regenerate
the persona handoff with `node tools/appearance-registry.js --write`; verify it
with `--check`. [Palette derivations, captures and verification](evidence/five-themes/README.md)
record the original approved colours and deployment limits. The subsequent
[material refinement and two visual passes](evidence/theme-materials/README.md)
record the current paper roles, semantic red/amber/green, local signature faces,
static textures and font refinements.

`agent-face.js` supplies both Latte art and signature SVGs to the one `blob()`
component. Eight permanent hash slots per material provide identity; no agent
pigment tints them. Retained mentions switch through CSS without text nodes,
network requests or selection changes. Images take priority. Fine detail drops
at 26px and below; initials use material ink with AA contrast.

`public/themes/materials.css` is core-owned rendering for these first-party
materials, separate from the token-only extension language. Shipped options
come from the same local package as shipped CSS even if a host catalog is older;
Yours options still come from its manifest. Persistence and persona registry
installation were the original
[handoff](../handoffs/2026-11-13-signature-faces.md). The host now reads defaults
from shipped manifests; this default-only update requires no host code change.
[Default and persistence checks](evidence/face-defaults/README.md) cover fresh
390/1440 browsers in light/dark and explicit special faces after reload.

## Permanent face identity

`AVATAR_VARIANTS` is an installation-supplied, append-only object of permanent
agent IDs to eight material/pour slots, 0–7. It uses the same configuration
boundary as `AVATAR_COLOURS`; installation names never become product defaults.
The server validates unique integer slots and sends them through the existing
parser-blocking `/appearance-state.js`. Both `pourFor()` and `variantFor()` read
one identity module, before any face renders. Names, roster order, tone and
mode cannot change that choice. Unknown IDs keep the original fixed eight-way
FNV fallback. Appending a supplied assignment leaves previous assignments alone.

The [host handoff](../handoffs/2026-11-13-permanent-face-slots.md) explains
configuration; the job receipt carries the deployment-specific values. Existing
saved shape and content choices keep their values; no Appearance preference or
host registry field is added. This is installation identity, not a new picker.

Mist Trail, Deep Forest and Yosemite now vary the whole silhouette and material
tone per slot. Stones lose their outline, with creek-rock colours, quartz and
moss. Cuts vary in bark, wood, ring spacing, heart and cracks. Granite landmarks
have dawn/day/dusk skies; the light reading paper is granite and pale sky.
Autumn keeps its approved style, with different leaf species/tones for the
previously similar slots. Foundry's approved tags remain, with outer panes,
boards, grouped cards and menus brought into its 8/10/14px corner scale.
[Review passes and checks](evidence/distinct-faces/README.md).

## Broker integration required before persistence can be called complete

The supplied broker interface documents no settings store or registry route.
This checkout has no host registry, and the settings tool named in the brief was
not available in the job. No host repository or installation was inspected. The
following is the explicit **new contract required by the app-server half**,
not a claim that the broker already implements it. Adapt its storage to the
host's existing settings store; do not create a second store in this app.

Authenticated `GET /v1/settings/appearance` returns the whole appearance value:

```json
{
  "phone": { "mode": "system", "size": 100, "theme": "latte" },
  "desktop": { "mode": "system", "size": 100, "theme": "latte" }
}
```

Authenticated `PATCH /v1/settings/appearance` atomically merges named fields in
one or both device kinds and returns **200 with the whole value**. For example:

```json
{ "desktop": { "size": 90 } }
```

This must retain the desktop mode and both phone values. Reject unknown device
kinds/fields, an empty patch, modes outside `light|dark|sun|system`, theme IDs outside the installed catalog, booleans or
non-integer sizes, and sizes outside 75–150 or not divisible by 5 with 400 and
`{"error":"a sentence"}`. Both routes use bearer authentication and no-store.
Defaults are filled on GET. Persist in the host's existing durable settings
store and expose these **same base keys** through its persona settings tool:

| Registry key | Values | Default |
| --- | --- | --- |
| `appearance.phone.mode` | `light`, `dark`, `sun`, `system` | `system` |
| `appearance.phone.size` | integer 75–150, step 5 | 100 |
| `appearance.desktop.mode` | `light`, `dark`, `sun`, `system` | `system` |
| `appearance.desktop.size` | integer 75–150, step 5 | 100 |
| `appearance.phone.theme` | installed theme ID (seven first-party themes above) | `latte` |
| `appearance.desktop.theme` | installed theme ID (seven first-party themes above) | `latte` |
| `appearance.location` | shared rounded location object described below, or `null` | `null` |

[Machine-readable registry handoff](appearance-settings.registry.json). This is
a declaration to integrate into the host's own registry, **not an installed host
registry**. The platform's page/registry parity check must consume these entries
when adding the routes. The UI must not be declared fully integrated until the
real settings tool can read/write these values and a fresh browser sees them.

The app relays `GET/PATCH /api/settings/appearance` to these routes unchanged.
A no-store `/appearance-state.js` request reads the same host value, limits it to
these public fields and declared theme options, and supplies a parser-blocking external script before the
stylesheets and app module. `theme-boot.js` selects the device kind and sets theme, mode
and size before the first contentful paint. Only unacknowledged Sun/location
edits have the compatibility cache described below; other preferences have none.
The broker read has a 3-second deadline; failure uses Latte/system/100% and shows an
unavailable message in Appearance. Failed writes retain the live local preview,
say it was not saved, and offer retry. They are not silently stored in a browser.
The previous browser theme key is discarded; there is no cross-browser-safe way
to promote competing old preferences into one host value.

Writes coalesce for 180ms, are serialized, and patch only edited fields. Pointer
release, visibility loss and pagehide flush pending changes with keepalive.
Focus/visibility return reads host values, including persona changes, unless a
local edit is pending. There is no new settings poll or stream contract.

## Sun mode

Sun is light from sunrise minus 30 minutes (inclusive) to sunset plus 30 minutes
(exclusive) at the saved place. It uses the same instant token swap as the other
modes. `public/sun.js` implements the NOAA/Meeus solar equations with the standard
−0.833° apparent horizon. The parser-blocking boot and live controller use that
same code. There is no weather, location lookup or astronomy service, and no
runtime dependency. Polar day stays light, polar night stays dark, with explicit
all-day copy instead of invalid switch times. Calculations are approximate;
elevation and local atmospheric conditions are not modeled.

The next boundary has its own timer. Visibility return and focus recheck
immediately, including offline; a minute clock check catches clock adjustments
and wakes. Polar conditions are re-evaluated at most six hours later. Timetable
labels follow the browser's locale/hour-cycle preference through `time.js`, in
the saved place's time zone, rounded to the nearest minute. Calendar arithmetic
uses Gregorian numeric parts, accounts for DST and political date-line zones,
and considers adjacent days so expanded light windows can cross midnight.

First choosing Sun without a saved location requests browser geolocation once,
with high accuracy off and a 10-second timeout. Latitude and longitude are rounded
to 0.1° before storage or transmission. A denial/unavailable browser saves a
representative location from its IANA time zone and offers “Using your time zone ·
Use my location”. The small offline table includes common zones and aliases;
an unlisted zone uses an equatorial point at its UTC-offset longitude. That is an
approximation, openly identified by the same fallback copy. No location request
occurs on load, on a timer or when choosing Sun again with a saved location.
“Use my location” explicitly retries. Coordinates identify the place without a
reverse-geocoding request. A persona selecting Sun without a location uses the
time-zone fallback without opening a browser permission prompt.

Mode remains per device kind. **Location is shared**, so phone and desktop use
one home location, including its calendar/time zone, rather than taking competing
GPS readings. The precise host extension uses `PATCH /api/settings/appearance`:

```json
{
  "desktop": { "mode": "sun" },
  "location": {
    "latitude": 37.8,
    "longitude": -122.4,
    "source": "geolocation",
    "timeZone": "America/Los_Angeles"
  }
}
```

The relay sends the identical body to `PATCH /v1/settings/appearance`. A phone
choice substitutes `phone` for `desktop`. Updating the place alone sends only
`{"location":{...}}`. `GET` and successful `PATCH` return the complete value with
`phone`, `desktop`, and shared `location`. `latitude` is −90…90 and `longitude`
−180…180, rounded to tenths; `source` is `geolocation` or `timezone`; `timeZone`
is a valid IANA identifier. A host/persona may clear the place with `null`.
The same persona registry keys are `appearance.phone.mode`,
`appearance.desktop.mode` and **`appearance.location`**. Generated declarations
include the schema. This repo declares that extension; host installation and
persona-tool parity require verification on the host.

An older host may reject these new values or silently omit them from its answer.
Sun/location edits apply immediately and are cached under the one browser key
`loam.appearance.sun` until acknowledged. Its shape is
`{phone?:{mode},desktop?:{mode},location?:{latitude,longitude,source,timeZone}}`.
Only pending edits live there; it is not another store for theme, size, options
or acknowledged settings. Both boot and controller overlay those pending fields.
A refusal shows a quiet local-save message, drops them from the normal retry
queue, and attempts no further unsupported writes on focus or during that page's
lifetime. Other appearance edits still use ordinary host persistence and errors.
The next navigation attempts one migration; an acknowledging host clears the
cache. Without host support, the fallback is device-local and cannot sync across
browsers. With browser storage blocked it lasts only for the current page.

[Solar references, test commands and 390/1440 captures](evidence/sun-mode/README.md).

## Scaling and row layout

CSS `zoom` on the app's body scales fonts, icons, spacing and hit areas together,
without raster transforms. The app's viewport height and keyboard offset divide
by the scale; DOM-rectangle calculations for thread popovers, reading anchors
and sheet dragging convert screen pixels back into layout pixels. Sticky author
labels and the composer retain native CSS layout. Appearance's touch controls
retain at least 44 screen CSS pixels at the small end. Narrow scaled thread-list
headers compact, then wrap their actions rather than losing a button offscreen.

Only thread rows whose existing subtitle resolver returns Quiet omit the subtitle
and let the title clamp to two lines. Their 80px base height matches active rows,
so working/quiet transitions do not move the list. The title/icon center alignment
comes from the shared row component. Classic chat rows keep their existing rule.

## Evidence

`npm test` runs persistence/controller, pre-paint, keyboard, relay and row tests.
The browser runner uses the real page/server/relay and a synthetic broker:

```sh
APPEARANCE_EVIDENCE=docs/evidence/appearance \
  LOAM_PLAYWRIGHT="$TMPDIR/deps/node_modules/playwright/index.mjs" \
  PLAYWRIGHT_BROWSERS_PATH="$TMPDIR/browsers" \
  node test/appearance-browser.mjs
```

Install optional Playwright/Chromium in scratch as in the design-system inventory;
these are not runtime dependencies. The captures and browser assertions cover
390 and 1440, light and dark, at 90% and 125% for list, thread and Appearance;
75%/150% checks cover bounds and reset. All conversations/settings are fixtures.
This proves the web half's contract, not installation of the host persistence or
registry. No deployment belongs to this change.

## Theme persistence extension

A theme uses the existing route, for example
`PATCH /v1/settings/appearance {"desktop":{"theme":"plain"}}`.
The host must accept/persist this field for each device kind, fill `latte` for
older records, merge it without changing mode/size, and expose the two new
registry entries through the same persona settings tool. Unknown theme IDs
should produce the host's ordinary 400 sentence. The installed IDs currently
are `latte` and `plain`; future external theme installation must register new
IDs with the host using the same catalog. This job changes no host files and
cannot verify whether that extension is installed. A refusal remains visible
and retryable in Appearance, as with any failed settings write.

The public pre-paint script includes only sanitized appearance fields and the
installed manifest catalog. Unknown/stale saved IDs fall back to Latte. Both
mode scopes of installed themes are already render-blocking stylesheets at
first paint, so selecting a theme only changes `data-look`. CSS scopes prevent
an inactive theme from overriding the active one. Browser chrome reads the
resolved page-surface token; no JavaScript palette copy exists.

Plugin catalog refreshes load a new stylesheet beside the current one, then
remove the old link only after the new sheet loads. Replacing the loaded link's
URL directly temporarily removes its CSS and exposes Plain's core defaults,
even though the initial link blocks rendering. Failed or superseded refreshes
leave the current sheet active. [Paint captures and regression checks](evidence/message-media-boot/README.md)
cover cold loads and reloads with delayed stylesheets.

See [token/theme contract](design-system/tokens.md). `node test/themes-browser.mjs`
uses the optional Playwright package (set `LOAM_PLAYWRIGHT` and browser paths as
above) to verify the first contentful paint in both themes/modes at both widths,
instant switches without CSS requests, host writes and fresh-navigation reads.

## Per-theme options

`options` is a per-device map keyed by theme id, then option id. It retains each
theme’s choices while other themes are selected. Missing or obsolete values use
manifest defaults; unknown fields never enter the public boot script. Root
`data-option-<id>` attributes apply the active theme’s choices before paint and
are removed when switching away. See the [manifest contract](design-system/tokens.md#theme-options)
and [precise host/registry handoff](appearance-options-handoff.md).

The schema is data, not a list of Latte switches inside the settings view. A
third-party CSS option uses the same validated manifest catalog, controls,
pre-paint attributes and nested persistence. The avatar option ids are core
semantics that a theme opts into. Their CSS variables update retained message
prose and mention faces without rebuilding it.

The shared `blob()` component in `public/ui.js` owns the face in every surface,
including inline mention chips. Its `look.imageSrc` slot replaces colour and
initials with an image cropped to the selected shape; there is no upload/store
feature. State rings and breathing layers use the same `avatarLook()` paint.

Palette uses an agent-id hash modulo the eleven original hues, independent of
roster order and device. Explicit `AVATAR_COLOURS` entries remain authoritative
in both colour modes. Latte tones alternate foam/espresso, latte/coffee,
caramel/mocha, crema/roast by roster position, so adjacent entries alternate
light and dark instead of walking through similar browns. Unlisted agents use
their hash. Tones may move when the roster changes; Palette hues do not. Each
tone’s assigned coffee or foam initials clear 4.5:1 in both light and dark.

[Current captures and verification](evidence/theme-options/README.md) cover all
six colour/shape combinations at 390/1440 in light/dark.

Palette hues are fixed in Latte and Plain, in both modes. Rust, gold and sage use dark initials so small faces remain readable; the other hues use white initials.
The semantic persona tokens retain the legacy slot order; only their selection
tints change by mode. See [restoration evidence](evidence/original-agent-palette/README.md).


### Latte art avatars

Latte's fourth **Agent shapes** choice is **Latte art**. It hides **Agent
colours** and reveals **In a mug** (on by default) and **Inside: Pour / Initials**
(Pour by default). Other shapes retain the saved colour choice. Switching themes
retains all three art preferences, separately for phone and desktop.

`public/latte-avatar.js` draws eight static inline SVG pours: heart, tulip,
rosetta, monk's head, swirl, twin hearts, leaf and plain round. FNV-1a over the
agent ID selects the pour, independent of name, roster order, configured colour,
mode or device. The design order is persistent identity; append rather than
reorder it. The ceramic uses a 42-unit rim, a 37.5-unit coffee circle, a small
lower-right handle, a faint edge and shadow. Without the mug only the enlarged
coffee disc remains. Six `latte-art-*` tokens define physical pigments, identical
in light and dark. The coffee's soft edge uses translucent vector layers with
no paint-server IDs, external resources or image requests.

All agent faces go through `blob()`, including retained message bylines, mention
chips and agent cards. SVG layers contain no text. Initials use a generated
foam-coloured glyph over the disc so decorative mentions remain empty in
`textContent`, clipboard and composer serialization. Ordinary avatars keep their
original initial text. Root attributes update both layers without rebuilding
retained content; active decorations and mention washes use coffee pigment.

The new stored leaves are `options.latte.latte-mug` (`on`/`off`) and
`options.latte.latte-inside` (`pour`/`initials`); `options.latte.agent-shapes`
also accepts `latte-art`. They use the existing appearance GET/PATCH route,
normalization, merge, pre-paint boot and persona registry declaration. No new
storage or route exists. Host validation/catalog/registry must ingest the
updated manifest and generated declarations; generation alone does not prove
host installation or persona parity.

[Latte art evidence](evidence/latte-art/README.md) contains local browser captures,
checks and deployment limitations. Reproduce the optional browser check with
`node test/latte-art-browser.mjs` and `LATTE_EVIDENCE` set to the capture directory,
using the Playwright environment documented above.
