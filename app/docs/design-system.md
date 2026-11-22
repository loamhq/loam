# The design system: where it lives

Loam's look is a set of CSS custom properties (tokens) and a few shared
stylesheets that use them. Themes change token values; components read
tokens and never a colour of their own. This page says where each part is
and what checks it. (Written 2026-11-17. The docs used to link a
`design-system/tokens.md` that was never in this repo; this page replaces
those links.)

## Tokens

`public/tokens.css` defines every token for the default look, light and dark:

- **Two colour families, each with its own inks.** Paper (`--paper-page`,
  `--paper-pane`, `--paper-raised`, `--paper-card`, `--paper-hover`, inked
  with `--paper-ink`, `-2`, `-3`, plus rules, highlight and glass) is the
  reading surface; latte (`--latte-foam`, `--latte-track`, `--latte-espresso`
  and their states, inked with `--latte-coffee`, `-2` and
  `--latte-espresso-ink`) is the control surface. A paper ink never stands
  on a latte ground, nor the reverse.
- **Signal and status**: `--signal-crema` marks (a passage with a note, a
  focus ring on a field, an arrival); it is not a text ink and not a button.
  `--danger`, `--warning`, `--success`.
- **Geometry, type and motion**: `--space-*`, `--radius-*` (`--radius-pill`
  for pills; Foundry tightens it to 8px), `--font-body`, `--font-display`,
  `--font-thread`, `--font-mono`, `--motion-*`, `--ease-*`, `--shadow-float`,
  `--shadow-popover`. These live once in `tokens.css` and are the same light
  and dark (`test/themes.test.js`).
- **Persona palette**: `--persona-<hue>` and `--persona-<hue>-tint`, the
  faces' colours; `AVATAR_COLOURS` (README) maps a persona to one of them
  (`public/avatar-colours.js` validates it).

Each theme (`public/themes/<id>/theme.css`) overrides only what differs; a
redundant override is rejected (`validateThemeCSS`, `test/themes.test.js`).
How themes are made, chosen and given options: [appearance](appearance.md);
the manifest's `options` list: `docs/plugins.md` (repo root), "A theme's
options", and the [host handoff](appearance-options-handoff.md).

## Three tiers of action

`public/button-tiers.css` paints buttons by attention, never by place:
**Newspaper** (no fill, paper ink, `--paper-hover` under a pointer) is the
default; **foam** (`.secondary`) is a secondary action; **espresso**
(`.primary`, in the display face) is the one action that finishes what is
being done. Selection is information, not a tier: a chosen option is paper
ink on `--paper-hover`. Buttons, links and selects focus with a 2px paper-ink
ring; inputs and textareas keep the 2px `--signal-crema` ring from
`style.css`.

## Surfaces and shared parts

- `public/menu-surface.css` and `menu-surface.js`: the one shell for menus,
  sheets and desktop dialogs ([menu surfaces](menu-surfaces.md)).
- `public/style.css`: content marks, fields, notices, tooltips, and the
  shared hit area: every `button`, `select` and text input has
  `min-height: 44px` (and buttons `min-width: 44px`), so a control drawn
  smaller still has room for a finger. A plugin that means a smaller row
  under a mouse says so itself, e.g. `@media (pointer: fine) { … { min-height:
  0 } }`; the `comments` plugin's contents card does this
  (`docs/sharing-and-comments.md` at the repo root).

## Pane lead

The shell places Back at the leading edge of a panel whenever that panel has
history. If the desktop list is hidden, Show sidebar shares that space, after
Back. A phone uses the same slot for returning to the enclosing list on a
direct link.

Specialized file viewers inherit `--pane-lead` from their host. Use
`padding-inline-start: max(<your padding>, var(--pane-lead, 0px))` on the
header to leave space for these controls, including on a phone. The shell
draws the chevron outside the viewer so a kept editor can survive Back and
layout changes. A viewer can use `options.openFolder` for a separate folder
action when the host supplies one.

## Reading surfaces

`--reading-ground` follows the shell, using `--paper-pane` inside a desktop
pane and `--paper-page` on a phone. Use it for a viewer that needs an opaque
backdrop, such as an iframe. Core file viewers can otherwise show the shell
through. Use a local surface for document details such as inline code, table
headers and floating comments.

## What checks it

- `tools/colour-families.mjs`, run inside a page: no paper ink on a latte
  ground or the reverse, text at 4.5:1 and glyphs at 3:1 against the opaque
  ground they stand on. It reads every colour syntax a computed style comes
  back in (`oklab()`, `oklch()`, `color(srgb …)` as well as `rgb()`), by
  converting through a canvas; `test/colour-families-browser.mjs` proves it.
  It is guarded to Latte; strip the guard to run it in every look (as
  `plugins/comments/test/test_look.py` and `plugins/sharing/test/test_look.py` check tokens against core).
- `test/control-style-guard.mjs`: every control's colours, borders and face
  resolve to tokens.
- `test/themes.test.js`: theme CSS overrides only differences; geometry lives
  once.
