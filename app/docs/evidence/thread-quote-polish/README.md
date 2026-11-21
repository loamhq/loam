# Thread quote polish

Full-viewport Chromium screenshots from the app and a fixture broker in Latte
light and dark. The branch opener contains three lines of text. The jump frames
show the destination while its arrival wash is visible.

| Viewport and mode | Branch opener | Jump to branch source | Jump within the same thread |
| --- | --- | --- | --- |
| 390 × 844 light | [Before](before-new-thread-390-light.webp) · [After](after-new-thread-390-light.webp) | [Before](before-source-highlight-390-light.webp) · [After](after-source-highlight-390-light.webp) | [Before](before-local-highlight-390-light.webp) · [After](after-local-highlight-390-light.webp) |
| 390 × 844 dark | [Before](before-new-thread-390-dark.webp) · [After](after-new-thread-390-dark.webp) | [Before](before-source-highlight-390-dark.webp) · [After](after-source-highlight-390-dark.webp) | [Before](before-local-highlight-390-dark.webp) · [After](after-local-highlight-390-dark.webp) |
| 1440 × 900 light | [Before](before-new-thread-1440-light.webp) · [After](after-new-thread-1440-light.webp) | [Before](before-source-highlight-1440-light.webp) · [After](after-source-highlight-1440-light.webp) | [Before](before-local-highlight-1440-light.webp) · [After](after-local-highlight-1440-light.webp) |
| 1440 × 900 dark | [Before](before-new-thread-1440-dark.webp) · [After](after-new-thread-1440-dark.webp) | [Before](before-source-highlight-1440-dark.webp) · [After](after-source-highlight-1440-dark.webp) | [Before](before-local-highlight-1440-dark.webp) · [After](after-local-highlight-1440-dark.webp) |

The [browser journey](../../../test/thread-branch-browser.mjs) reads a source
chat, sends a branch opener and an in-thread reply, and follows both quotes
by pointer and keyboard. It checks the direct message URL after the wash
clears, and expands and refolds matching long messages with and without a
quote. It also checks Back, the source marker, and sending with the quote removed.

Run from `app` with an external Playwright install and Chromium executable.

```sh
LOAM_PLAYWRIGHT=/path/to/playwright/index.mjs \
LOAM_CHROME=/path/to/chromium \
THREAD_BRANCH_EVIDENCE="$TMPDIR/thread-quote-polish" \
node test/thread-branch-browser.mjs
```

For the before captures, copy the browser script into an archive of `c3a7715`
and run it there with `THREAD_BRANCH_BASELINE=1`. The browser script writes
PNG frames and measurements to the evidence directory. The frames here are
stored losslessly as WebP.
