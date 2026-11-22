# Agent creation in thread history

Chromium opened the real app against a loopback fixture, confirmed a draft,
and received twelve later messages in the same conversation. All captures
use that conversation. The baseline serves the page from commit `6729b04`.

| View | 390 light | 390 dark | 1440 light | 1440 dark |
| --- | --- | --- | --- | --- |
| Before, latest messages | [View](before-bottom-390-light.webp) | [View](before-bottom-390-dark.webp) | [View](before-bottom-1440-light.webp) | [View](before-bottom-1440-dark.webp) |
| After, latest messages | [View](after-bottom-390-light.webp) | [View](after-bottom-390-dark.webp) | [View](after-bottom-1440-light.webp) | [View](after-bottom-1440-dark.webp) |
| After, at the creation message | [View](after-history-390-light.webp) | [View](after-history-390-dark.webp) | [View](after-history-1440-light.webp) | [View](after-history-1440-dark.webp) |
| After, agent deleted | [View](after-deleted-390-light.webp) | [View](after-deleted-390-dark.webp) | [View](after-deleted-1440-light.webp) | [View](after-deleted-1440-dark.webp) |

Run `node test/agent-creation-browser.mjs` from the app folder with
`LOAM_PLAYWRIGHT` set to a Playwright module and `LOAM_CHROME` to a Chromium
executable. Set `AGENT_CREATION_EVIDENCE` to a directory to save PNGs.
For the baseline, set `AGENT_CREATION_BEFORE_PUBLIC` to the extracted
`public/` directory from the starting commit.

The walk also reloads the page, fetches earlier history, changes the
live display name and face, and checks the agent ID sent by **Start a chat**.
