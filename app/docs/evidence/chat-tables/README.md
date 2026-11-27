# Chat tables: fit the column, calmer and less dense

These captures verify the redesigned Markdown table layout across phone
(`390×844`, touch) and desktop (`1440×900`, hover) viewports in light and dark
Latte (`public/markdown.js`, `public/style.css`, `public/files.js`,
`public/thread-replies.js`).

## What changed

- **Fit the column first (`public/markdown.js`, `public/style.css`):**
  - Replaced `.md table { width: max-content; min-width: 100% }` with
    `width: 100%` inside a `fit-content` frame (`min-width: min(100%, 44ch); max-width: 100%`).
    At `390 px`, a 3-column prose table (`Operation / Time / What happened`)
    drops from `607 px` (off-screen scroll) to `362 px` (exact fit, zero scroll),
    and a URL/code table drops from `800 px` (overflowing even at `1440 px`) to
    `362 px` at phone width and `760 px` at desktop width.
  - `tableAt()` / `renderTable()` classify each column by visible body text length:
    `data-col="compact"` (`<= 14` chars or right-aligned values `<= 22` chars) stays
    `white-space: nowrap` so values like `4 min 10 s` or `$142.80` never break
    mid-value; `data-col="medium"` (`15–28` chars) and `data-col="prose"` (`> 28`
    chars) wrap naturally with minimum widths (`5rem` / `6.5rem` on phone,
    `7.5rem` / `10.5rem` at `>= 720 px`) so prose columns never collapse to one
    word per line while 3-column mixed prose/medium tables still fit `390 px`
    at both default scale and `--ui-scale: 0.9`. Short cells (`<= 14` chars) inside mixed columns also get
    `data-nowrap`, and headers longer than `18` chars get `data-wrap`.
  - `.md table` sets `overflow-wrap: break-word` so ordinary words stay intact,
    while links and inline code inside cells keep `overflow-wrap: anywhere` so
    long URLs and identifiers wrap cleanly inside their column.
- **Obvious, contained horizontal scrolling (`public/files.js`, `public/style.css`, `public/thread-replies.js`):**
  - Promoted the `data-edges` (`right`, `left`, `both`) edge-fade mask from
    `.fs-preview .tablewrap` to `.md .tablewrap` and generalized
    `fitPreviewTables(root)` to observe every `.md .tablewrap` across chat
    threads, Files preview, agent profiles, and plugin docs.
  - Added `.tablewrap` to `replyGesture`'s exclusion selector so a sideways
    touch swipe on a scrolling table never triggers swipe-to-reply, while
    vertical swipes continue scrolling the thread natively (`overflow-y: hidden`,
    `overscroll-behavior-x: contain`).
- **Calmer document typography (`public/style.css`):**
  - `15 px` body text with `1.45` line-height, `font-variant-numeric: tabular-nums`,
    `10 px` vertical cell padding, flush outer edges with column gaps (no rounded
    box), quiet `12.5 px` weight-`600` muted header labels (`--paper-ink-3`,
    transparent fill) over a single `--paper-rule-strong` rule, and lighter
    `--paper-rule` hairlines between body rows.
  - Owner bubbles (`.say.user.md`) derive header ink and both rule weights from
    `currentColor` via `color-mix()`.
  - Files preview (`.fs-preview.md`) shares the same calmer table styling and
    edge-fade masks.
- **Quiet touch, hover Copy on desktop (`public/style.css`):**
  - Dropped `.md .table-frame { padding-top: 52px }` and hid `.table-frame .copyblk`
    at rest on `@media (hover: none)` (while keeping it reachable via
    `:focus-visible` and keeping full-message Copy in the message footer plus
    native text selection).
  - Desktop (`@media (hover: hover)`) reveals a raised pill Copy button at the
    top-right corner of the table frame on hover, preserving its `44 px` hit
    target without clashing transparently over the rightmost header text.

## Captures (`390×844` and `1440×900`, light and dark)

| Viewport | Mode | Prose + Numeric (before) | Prose + Numeric (after) | Mid-scroll / Hover Copy | 2-col + Owner (before) | 2-col + Owner (after) | URL + Code (before) | URL + Code (after) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 390×844 | Light | [before](before-390-light-prose-numeric.png) | [after](after-390-light-prose-numeric.png) | [mid-scroll](after-390-light-numeric-mid.png) · [scale 0.9](after-390-light-scale90-mixed3col.png) | [before](before-390-light-twocol-owner.png) | [after](after-390-light-twocol-owner.png) | [before](before-390-light-url-code.png) | [after](after-390-light-url-code.png) |
| 390×844 | Dark | [before](before-390-dark-prose-numeric.png) | [after](after-390-dark-prose-numeric.png) | [mid-scroll](after-390-dark-numeric-mid.png) · [scale 0.9](after-390-dark-scale90-mixed3col.png) | [before](before-390-dark-twocol-owner.png) | [after](after-390-dark-twocol-owner.png) | [before](before-390-dark-url-code.png) | [after](after-390-dark-url-code.png) |
| 1440×900 | Light | [before](before-1440-light-prose-numeric.png) | [after](after-1440-light-prose-numeric.png) | [hover copy](after-1440-light-desktop-hover-copy.png) | [before](before-1440-light-twocol-owner.png) | [after](after-1440-light-twocol-owner.png) | [before](before-1440-light-url-code.png) | [after](after-1440-light-url-code.png) |
| 1440×900 | Dark | [before](before-1440-dark-prose-numeric.png) | [after](after-1440-dark-prose-numeric.png) | [hover copy](after-1440-dark-desktop-hover-copy.png) | [before](before-1440-dark-twocol-owner.png) | [after](after-1440-dark-twocol-owner.png) | [before](before-1440-dark-url-code.png) | [after](after-1440-dark-url-code.png) |

Reproduce from `app/` with `LOAM_PLAYWRIGHT` and `LOAM_CHROME` set:
`CHAT_TABLES_EVIDENCE=/tmp/out node test/chat-tables-browser.mjs`
(pass `CHAT_TABLES_BASELINE=1` against a `git archive` of the prior commit to
reproduce the pre-change baseline).
