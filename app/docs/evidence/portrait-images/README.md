# Portrait images and captions in chat messages

These captures verify the sizing, caption measure, multi-image row packing,
owner attachment framing, and full-size viewer for portrait images in threads
(`public/chat-ui.js` and `public/style.css`).

## What changed

- **Root cause:** `.reply-figure` used `flex: var(--ratio) 1 0%`. Under CSS
  Flexbox §9.7, whenever the sum of `flex-grow` values in a row is below `1`
  (a single portrait image or two narrow portraits), Flexbox allocates only
  `sum × free space` instead of filling the row — multiplying the aspect ratio a
  second time. A single `390×844` phone screenshot (`0.4621`) shrank from
  `203 px` down to `94 × 203 px`, a `390×2000` full-page capture shrank to
  `17 × 86 px`, and captions inherited that sliver width.
- **Sizing rule:**
  - Scale `flex-grow` by `100` (`calc(var(--frame-ratio) * 100)`) so multi-image
    rows always distribute 100% of their width.
  - Floor each figure's frame ratio at `0.45` (9:20) with `object-position: top
    center`: standard phone viewport screenshots (`>= 0.45`) stay uncropped,
    while ultra-tall full-page captures show a readable top-aligned preview and
    tap open full size.
  - Portrait-only rows (`.portrait-row`, all items `< 0.85`) use
    `--figure-h: min(520px, 58vh)` on desktop (`240 × 520 px` for a `390×844`
    phone screenshot, `234 × 520 px` for `390×2000`, capped at `380 px` wide for
    single 3:4 portraits); landscape and square rows keep `min(440px, 62vh)`.
  - Pack multi-image groups in order into rows so no image is squeezed below
    `190 px` in the `760 px` desktop reading column (two `1440×900` shots + one
    `390×844` phone become a two-up desktop row followed by a readable portrait
    row; two or three phones stay side by side).
  - Single-image and stacked rows size `.reply-image` directly and let `<figure>`
    span the column so `.reply-caption` reads at up to `62ch` (the body prose
    measure), never squeezed to a narrow image's width.
  - Single attached images in message bubbles (`.say .bubble-files:has(.message-image:only-child)`)
    size to their aspect ratio up to `min(460px, 56vh)` (`213 × 460 px` for an
    `1179×2556` phone screenshot) with all four corners rounded.

## Captures (1440×900 and 390×844, light and dark)

| Viewport | Mode | Portrait (before) | Portrait (after) | Landscape & square | Multi-image | Owner attachment | Full-size viewer |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1440×900 | Light | [before](before-1440-light-portrait.webp) | [after](after-1440-light-portrait.webp) | [landscape](after-1440-light-landscape.webp) | [multi](after-1440-light-multi.webp) | [owner](after-1440-light-owner.webp) | [viewer](after-1440-light-viewer.webp) |
| 1440×900 | Dark | [before](before-1440-dark-portrait.webp) | [after](after-1440-dark-portrait.webp) | [landscape](after-1440-dark-landscape.webp) | [multi](after-1440-dark-multi.webp) | [owner](after-1440-dark-owner.webp) | [viewer](after-1440-dark-viewer.webp) |
| 390×844 | Light | [before](before-390-light-portrait.webp) | [after](after-390-light-portrait.webp) | [landscape](after-390-light-landscape.webp) | [multi](after-390-light-multi.webp) | [owner](after-390-light-owner.webp) | [viewer](after-390-light-viewer.webp) |
| 390×844 | Dark | [before](before-390-dark-portrait.webp) | [after](after-390-dark-portrait.webp) | [landscape](after-390-dark-landscape.webp) | [multi](after-390-dark-multi.webp) | [owner](after-390-dark-owner.webp) | [viewer](after-390-dark-viewer.webp) |

Reproduce from `app/` with `LOAM_PLAYWRIGHT` and `LOAM_CHROME` set:
`PORTRAIT_IMAGES_EVIDENCE=/tmp/out node test/portrait-images-browser.mjs`
(pass `PORTRAIT_IMAGES_BASELINE=1` against an archive of the prior commit to
reproduce the pre-fix baseline).
