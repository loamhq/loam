# Open question and approval cards — keeping sent messages in view

Chromium opened the real Loam app against a loopback fixture at phone (390 px)
and desktop (1440 px) widths in light and dark themes. Each contact sheet pairs
the baseline (serving `public/` from commit `7f95f29`, where an unmatched
`broker.permission_request` card stayed at the end of `.msgcol` and pushed a
newly sent message and reply above the viewport) with the anchored flow after
the fix.

| Width | Light | Dark |
| --- | --- | --- |
| 390 px phone | [Before and after](phone-light.webp) | [Before and after](phone-dark.webp) |
| 1440 px desktop | [Before and after](desktop-light.webp) | [Before and after](desktop-dark.webp) |

Frames in each sheet:

1. **Before (`7f95f29`)**: an open `AskUserQuestion` card without `message_id`
   stays last in `.msgcol`, pushing the sent message (and on 390 px, the
   streaming reply too) off the top of the viewport.
2. **After — open question card on load**: the card opens cleanly at the bottom
   with its options and action buttons in view.
3. **After — sent message & streaming reply in view**: the question card stays
   anchored where it was raised (`after_message`), while the sent message and
   streaming reply land below it in full view at the bottom.
4. **After — card answered & thread complete**: scrolling up to answer the card
   posts the answer, clears the card, and lets the agent finish in order.

Run `LOAM_CHROME=/usr/bin/chromium-headless-shell node test/permission-card-scroll-browser.mjs`
from `app/` (no external Playwright dependency required). Pass
`PERMISSION_EVIDENCE=<dir>` to save PNG frames and
`PERMISSION_BEFORE_PUBLIC=<dir>` to run the baseline check.
