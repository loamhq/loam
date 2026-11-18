# Settings › Market

*Built 2026-11-18. The owner named it and set its shape that day: the
marketplace is a place of its own, reached from Settings; Plugins and
Appearance show only what is installed.* The list it reads, and its format,
are in `docs/plugins.md` at the repository's root, "The marketplace".

## Where it is

- **Settings** has a **Market** row, set a little apart at the end: below a
  rule in the desktop's navigation, in its own group on a phone.
- Opening it fills the window (the Settings dialog steps aside): a way back
  to Settings at the top left, a close at the top right. On a phone it is the
  whole screen. Escape goes back one page, and closes it from the front page.
- **Plugins** and **Appearance** end with a **Find more** row, which opens the
  Market at Extensions and at Themes.
- On a desktop the Market has an address, like Settings:
  `?modal=market`, with `&listing=<id>`, `&kind=<kind>` or `&q=<words>`.
- From code (a thread's listing card, the next piece of work):
  `host.openMarket({ id })` (a listing's page), `host.openMarket({ kind, q })`,
  `host.openMarketInstall(id)` (the install sheet alone, over anything), and
  the commands `market.open` and `market.install`.

## The pages

- **The front page:** the masthead "Market" and a quiet line (the date, how
  many things are listed, each read by hand); one search field; the kinds
  that have listings (Everything, Themes, Extensions, Apps, Agents, Skills);
  the lead story; a section per kind with listings, each with See all; and,
  last, "Nothing quite right?".
- **The lead story** is the listing marked `featured`, else one made by Loam
  (author "Loam…"; the list has no dates, so the highest version of Loam's
  own leads). Its cover is the listing's `image`, else a drawn one. It is not
  repeated in its kind's section.
- **Themes** are swatch cards drawn from the theme's own tokens: Appearance's
  miniature inside `.theme-preview[data-look=<id>]`, which the theme's CSS
  scopes (`/api/marketplace/<id>/theme.css`). Each has **Get** and **Try it**.
- **A kind's page** lists every listing of that kind and ends with
  "Nothing quite right?".
- **A listing's page:** what it is, a cover (a theme's is Loam in miniature,
  wearing it), what it touches in plain words (`plainTouches`, from the
  declaration the broker read at its link), who made it, version, source,
  the maker's own words, and the action.
- **Search is ask-first.** As a person types, the best five drop down under
  the field: arrows move, Enter opens the one chosen, Escape closes; the last
  row is "See all results". Enter with nothing chosen is the results page:
  the best match large, "Also close" below, "Nothing quite right?" last.
  Matching (`public/market.js`) reads name, description, author and kind,
  meets a word in any of its forms ("write" finds "writing"), drops small
  words, and knows a few near words ("phone" meets "mobile"). The words a
  person typed are marked wherever they meet.

## Nothing quite right? Ask one of your agents to build it

It ends every results page and kind page, and is the whole of an empty
Market. It is the friendliest moment in the Market, not an error: a drawn
picture (an agent at a bench, a new piece growing from the soil, a gap where
it will go), a line, and **Ask an agent to build it**, which shows the
agents' faces to choose from (one agent: its name is on the button). The
choice opens a new thread with that agent, the search words already in the
composer as a request to build it as the person's own plugin
(`buildRequest`). Nothing is sent until the person sends it.

## Installing: one sheet

Every Get and Install goes through one sheet, the front of the
install-from-link path (`POST /api/plugins`, twice): the name, the maker and
what it touches, in plain words, from the declaration the first call answers;
"You say yes once, here"; Install or Not now; progress; then where it went
(Appearance, Apps, Plugins) and a next step (Wear it now, Open Apps, See it in
Plugins). The listing's button then reads In use, Use, Open or Installed.
Agents and skills are listed and read but not installed yet (the host's
`LISTED_ONLY`): their button is See it, and their page says so.

A persona sets the same things through the same routes: `plugin.install`
and `appearance.<kind>.theme` (`bin/settings`).

## Try it

**Try it** wears a theme without choosing it (`theme.try(id)`, public/theme.js):
the whole app takes its look until Stop, Get and Wear, or leaving the Market.
Nothing is saved. A listed theme's fonts are not loaded until it is
installed, so its display face falls back to the serif.

## How it looks in every theme

Every colour, face, radius and motion in `public/market.css` is a token, and
the pictures (`public/market-art.js`) are SVG painted with tokens through the
CSSOM, so the Market is at home in every theme, light and dark, and under
Sun. Twinkles and the agent's bob stop under reduced motion.

## What checks it

- `test/market.test.js`: matching ("write" → writing, "sync with my phone" →
  the sync listing, no match → nothing, so the build-it panel), the lead
  story, sections, the empty list, the button by install state, plain words,
  and a listed theme's CSS held to the installed themes' rules.
- `test/market-browser.mjs`: the whole walk in a real browser at 390 and
  1440, light and dark, against `test/market-fixture.mjs` (an invented list:
  nothing in it is in the public marketplace), with colour families, control
  styles and no sideways scroll checked.
- `tools/capture-market.mjs`: the pictures, in Latte and Autumn.
