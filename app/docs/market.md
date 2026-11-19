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
- From code (a thread's listing card does this): `host.openMarket({ id })`
  (a listing's page; with `from: { label }` its way back is to there),
  `host.openMarket({ kind, q })`,
  `host.openMarketInstall(id)` (the install sheet alone, over anything), and
  the commands `market.open` and `market.install`.

## The pages

- **The front page:** the masthead "Market" and a quiet line (the date and how
  many things are listed); one search field; the kinds
  that have listings (Everything, Themes, Extensions, Apps, Agents, Skills);
  the lead story; a section per kind with listings, each with See all; and,
  last, "Nothing quite right?".
- **The lead story** is the listing marked `featured`, else one made by Loam
  (author "Loam…"; the list has no dates, so the highest version of Loam's
  own leads; "by Loam" is a listing from the `loamhq` organisation, or an
  author starting "Loam"). Its picture is the listing's cover (below). It is
  not repeated in its kind's section.
- **A listing's picture** (2026-11-18, `picture` in `page/market.js`): its
  cover (the listing's `cover`, else the plugin's own `image`; the dark one,
  `cover_dark` or `image_dark`, while Loam is dark), at the plugin's
  `image_position`. Without one, a theme shows itself: Appearance's own
  preview (`public/theme-preview.js`, the same component the theme picker
  draws) inside `.theme-preview[data-look=<id>]`, which the theme's CSS
  scopes (`/api/marketplace/<id>/theme.css`), larger in the big frames. Any
  other kind shows its icon (else its letter tile), large, on a quiet
  `--paper-card` field. Nothing is drawn as clip-art.
- **Themes** are cards with that picture, the name and line, **Get** and
  **Try it**.
- **A kind's page** lists every listing of that kind and ends with
  "Nothing quite right?".
- **A listing's page:** what it is, its picture (above), what it touches in
  plain words (`plainTouches`, from the declaration the broker read at its
  link), **Made by**: the creator's name and the links they chose to show,
  each with its own mark (`public/creator-links.js`; the kind comes from the
  host, `link_kind`), version, kind, source, then **About it**: the
  plugin's README at the tag, drawn by the app's Markdown renderer with its
  folder's pictures (the plugin's `PLUGIN.md` guide when it has no README),
  and the action. There is no review claim anywhere: being listed is a
  merged pull request, not an audit.
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
Market. It is the friendliest moment in the Market, not an error. It is the
app's one empty state (`docs/empty-states.md`): a sprout in Loam's blob,
whose stem draws in once (a lens with the sprout's leaf in it when a search
found nothing), a line, and **Ask an agent to build it**, which shows the
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

## A listing card in a thread

*Built 2026-11-18. The owner: "Agents should be able to go navigate the
Marketplace and create that card in chat to let the user install something
if they wanted to … It should just be a capability that the agents have
access to, and the UI supports it too."*

An agent finds a listing with its Market tool (`loam market search`, which
ranks with this page's own `search`) and puts the result's `card` line in
its reply, on a line of its own:

    [Pocket Sync](https://<the app>/?modal=market&listing=pocket-sync)

- **Where it is drawn:** `public/threads.js` cuts an agent's words at such
  lines (`splitCards`, `public/market-card.js`) and draws each run of them
  with `marketCards` (`page/market.js`) in its place, between the words
  before and after. The words stay in the cached prose; the cards are drawn
  on every redraw, so a card reads Installed the moment it is.
- **The card:** the listing's tile (its cover, else its icon, a theme's own
  colours, else its letter), a quiet "Market · Kind" kicker with the spark, the name in
  the display face, one line (two at most), the maker, how far it reaches
  (`reachOf`, from the declaration the broker reads at the listing's link:
  only colours and type, stays on your Loam, stays on your server, talks to
  a service), and the Market's button for it (`actionOf`): Install, or Get
  for a theme, opens **the Market's install sheet** over the thread, so the
  yes is the same yes; then Installed, Use or In use, or Open for an app.
  Agents and skills say See it. Two or three stack, each a step behind the
  last as they appear.
- **The name** (and the whole card, the name's hit area stretched over it)
  opens the listing's page; its way back names the thread and returns there,
  as Escape does.
- **Hard to make by accident:** the link alone on its line, outside fenced
  code, to the app's root with exactly `modal=market` and a listing id. A
  line in a sentence, a list item or code is the Markdown it is. At most
  three cards in a message; a fourth line stays a link.
- **A listing the Market does not have** (an id it never listed, one that
  left, or a Market that is off or out of reach) is drawn as the link's
  words, plain: no card, no link, no error. While the list is first read,
  a quiet card holds the place with the name.
- **The thread record** (`15_CONVERSATIONS/`) keeps the line as written:
  the name, linked to the listing's page in the app.

## Try it

**Try it** wears a theme without choosing it (`theme.try(id)`, public/theme.js):
the whole app takes its look until Stop, Get and Wear, or leaving the Market.
Nothing is saved. A listed theme's fonts are not loaded until it is
installed, so its display face falls back to the serif.

## How it looks in every theme

Every colour, face, radius and motion in `public/market.css` is a token, so
the Market is at home in every theme, light and dark, and under Sun; a
cover is the maker's photograph, and a theme without one is drawn in its own
tokens. The drawn covers (a puzzle piece under twinkles, `market-art.js`)
went on 2026-11-18. A preview's loading breath and the sprout's growing stop
under reduced motion.

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
- `test/market-card.test.js`: the card's form (what is one and what is
  not, in order, three at most, never in code), an unknown id drawn as its
  words, and the agent's search ranking every query as this page does.
- `test/market-card-browser.mjs`: a reply with one card and with two, the
  install sheet from a card, Installed after, the listing and its way back,
  at 390 and 1440, light and dark; `tools/capture-market-cards.mjs` runs it
  with pictures, in Latte and Autumn.
