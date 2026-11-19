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

*The store (2026-11-18). The owner: "I want the design of the initial
market home page and each item's page to be beautiful and look amazing,
like a luxury and high quality store." Luxury here is restraint, space and
photography: large pictures, generous room, hairline rules, the display
face for names, slow hover (a picture eases to 1.05 over 1.2s), and no
badges, counts or chips beyond what is named below. The design reference
is the mock `market-store.html` the designer drew from the real covers.*

- **The front page:** the masthead, a large "Market" with one line on the
  right (`TAGLINE`); one search field; the kinds that have listings
  (Everything, Themes, Extensions, Apps, Agents, Skills) as quiet text tabs,
  an accent line under the one shown; the lead story; a section per kind
  with listings; and, last, "Nothing quite right?" as a band. A section's
  head is its name in the display face, a quiet subtitle (`KIND_LINE`),
  See all, and a hairline under them. A kind with nothing is not shown.
- **The lead story** is the listing marked `featured`, else one made by Loam
  (author "Loam…"; the list has no dates, so the highest version of Loam's
  own leads; "by Loam" is a listing from the `loamhq` organisation, or an
  author starting "Loam"). It is one photograph across the page, about
  16:7, with its words over a soft gradient at its foot: a kicker, the name
  large, one line, "by" the maker, and Try it (glass) and Get or Install on
  the right. The words over a photograph are the one paint that is not the
  theme's: `--theme-cover-ink` on `--theme-cover-shade`, the core pair for
  captions, so they read on any picture. Without a cover it is a plate in
  the page's own colours, the theme wearing itself (or the plugin's mark)
  on the right and the words in ink. It is not repeated in its kind's
  section.
- **A listing's picture** (2026-11-18, `picture` in `page/market.js`): its
  cover (the listing's `cover`, else the plugin's own `image`; the dark one,
  `cover_dark` or `image_dark`, while Loam is dark), at the plugin's
  `image_position`. Without one, a theme shows itself: Appearance's own
  preview (`public/theme-preview.js`, the same component the theme picker
  draws) inside `.theme-preview[data-look=<id>]`, which the theme's CSS
  scopes (`/api/marketplace/<id>/theme.css`), larger in the big frames. Any
  other kind shows its icon (else its letter tile), large, on a quiet
  `--paper-card` field. Nothing is drawn as clip-art.
- **Themes** are large photographs in rows (`tileRows`): two wide, then
  three tall, again. One alone is one wide tile, and a row never ends with
  one small tile alone (a lone last one joins the row before). Under each,
  the name in the display face, the maker on the right, and one line;
  **Try it** sits on the picture's corner, shown on hover (always on touch).
  The tile opens the listing; Get is on the listing's page.
- **Extensions, apps, agents and skills** are calm cards, two to a row: the
  listing's mark in Loam's blob shape (its icon, else its letter), the name,
  one line, the maker and how far it reaches, and its button (Install, or
  See it for agents and skills). One alone, or an odd last one, takes the
  whole row.
- **A kind's page** lists every listing of that kind and ends with
  "Nothing quite right?".
- **A listing's page:** a wide cover at the top, about 21:8 (the dark one
  while Loam is dark; a theme without one wears itself at the same size; any
  other kind without one has no frame, and its mark sits beside its name).
  Then the header: a kicker (kind · version), the name very large, its
  line, and on the right Get or Install, **Try it first** for a theme, and
  one fine line from its declaration (`fineLine`: "Free · changes only how
  Loam looks", "Free · talks to one service"). Then the maker's row: their
  mark, their name, "Made N things in the Market" (`madeBy`), and the links
  they chose to show as pills, each with its own mark
  (`public/creator-links.js`; the kind comes from the host, `link_kind`);
  with no links, only the name. Then the body: the plugin's README at the
  tag as an article about 640px wide ("From the maker's README"; its
  `PLUGIN.md` guide, "In its maker's words", when it has none), drawn by the
  app's Markdown renderer with its folder's pictures, headings in the
  display face, and a first heading that only repeats the name left out.
  Beside it, a sticky column: what it touches in plain words
  (`plainTouches`), Details (version, kind, light and dark for a theme,
  source), and a theme's Options. Without Loam's note there is no review
  claim anywhere.
- **Loam's note** (2026-11-19). The owner: when a plugin comes onto the
  Market "we review the code, we review its description … we also have a
  section … where we talk about where it touches, what are the different
  things that it might change, what are some of the things that are in the
  code that might be interesting to the user", and an update is reviewed
  and the note rewritten. A listing whose list entry carries `review`
  (docs/plugins.md, "The list") shows it right after the maker's row, above a
  theme's showcase, the screenshots and the README, as wide as the README
  (`loamNote`): Loam's own mark,
  "Loam's note", "Loam's review of version N · date. Not the maker's
  words.", the summary, then **New since N** set apart on a quiet plate for
  an update, then What it touches, What it may change and Worth knowing.
  The side column's touches are then titled **What it declares**, since
  they are the plugin's own declaration. A listing without a review shows
  none of this. Its Get or Install asks for the reviewed commit
  (`pinned_link`), not the tag.
- **On a phone** every page is one column. The lead story is taller and its
  name smaller, theme tiles are full width, and a listing's buttons sit
  under its words, then stay docked at the foot of the screen once they
  have scrolled away (`dock`, `watchDock`).
- **Search is ask-first.** As a person types, the best five drop down under
  the field: arrows move, Enter opens the one chosen, Escape closes; the last
  row is "See all results". Enter with nothing chosen is the results page:
  the best match large, "Also close" below, "Nothing quite right?" last.
  Matching (`public/market.js`) reads name, description, author and kind,
  meets a word in any of its forms ("write" finds "writing"), drops small
  words, and knows a few near words ("phone" meets "mobile"). The words a
  person typed are marked wherever they meet.

## Nothing quite right? Ask one of your agents to build it

It ends the front page, every results page and kind page as a band (the
sprout, the words and the button in a row on a quiet plate; centred, with
the lens, when a search found nothing), and is the
whole of an empty Market. It is the friendliest moment in the Market, not an error. It is the
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

**From a link, nobody reviewed it** (2026-11-19, the owner's yes that day).
A plugin installed from a link was read by nobody at Loam, and the sheet
says so in one sentence before the yes, on a quiet plate above "Before you
say yes": "Nobody at Loam has looked at this. Once installed, it can do
anything you can on this machine." For a theme with no page or server code
the second sentence is "A theme changes only how Loam looks." The Market's
own sheet does not carry it.

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
  story, sections, the tile rows, the fine line, a maker's count, the empty list, the button by install state, plain words,
  and a listed theme's CSS held to the installed themes' rules.
- `test/market-browser.mjs`: the whole walk in a real browser at 390 and
  1440, light and dark, against `test/market-fixture.mjs` (an invented list:
  nothing in it is in the public marketplace), with colour families, control
  styles and no sideways scroll checked.
- `tools/capture-market.mjs`: the pictures, in Latte and Autumn, with
  whole-page captures of the front page and of a theme's and an
  extension's listing, a lead story with a photograph, and a phone's docked
  buttons.
- `tools/capture-market-note.mjs`: Loam's note on a listing's page and its
  install sheet at 390 and 1440, light and dark, in Latte and Autumn, with a
  note from the reviewer's test set (`REVIEWED` in the fixture); it checks the
  note opens the body, says whose words it is, shows New since for an
  update, and that Install asks for the reviewed commit.
- `test/market-card.test.js`: the card's form (what is one and what is
  not, in order, three at most, never in code), an unknown id drawn as its
  words, and the agent's search ranking every query as this page does.
- `test/market-card-browser.mjs`: a reply with one card and with two, the
  install sheet from a card, Installed after, the listing and its way back,
  at 390 and 1440, light and dark; `tools/capture-market-cards.mjs` runs it
  with pictures, in Latte and Autumn.
