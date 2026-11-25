# Agent draft card — keeping new messages in view

Chromium opened the real Loam app against a loopback fixture at phone (390 px)
and desktop (1440 px) widths in light and dark themes. Each contact sheet pairs
the baseline (serving `public/` from commit `44ba9ba`, where an expanded draft
card pushed a newly sent message and reply off the top of the viewport) with
all five verified journeys after the fix.

| Width | Light | Dark |
| --- | --- | --- |
| 390 px phone | [Before and after](phone-light.webp) | [Before and after](phone-dark.webp) |
| 1440 px desktop | [Before and after](desktop-light.webp) | [Before and after](desktop-dark.webp) |

Frames in each sheet:

1. **Before**: expanded draft card at the bottom of `.msgcol` pushes the sent message and reply completely above the viewport.
2. **1a–1c**: sending with the card expanded collapses the card, keeps the composer steady, and keeps the sent message, the agent's thinking indicator, and the reply in view right above the compact card.
3. **2a–2b**: a new draft revision updates the card in place (`2nd draft`) and collapses it; one tap expands cleanly to the card's header.
4. **3a–3b**: the quiet close button hides the card without deleting the draft (persisting across reload), and a later revision (`3rd draft`) brings it back collapsed.
5. **4**: making the agent through chat (even while the card is hidden) ends the draft card and shows the compact **Agent created** card in history.
6. **5**: an ordinary thread with no draft card opens pinned at the end, stays put when scrolled up, and returns to the bottom on send.

Run `node test/agent-draft-card-browser.mjs` from `app/` with `LOAM_PLAYWRIGHT`
and `LOAM_CHROME` set. Pass `AGENT_DRAFT_EVIDENCE=<dir>` to save PNG frames and
`AGENT_DRAFT_BEFORE_PUBLIC=<dir>` to run the baseline check.
