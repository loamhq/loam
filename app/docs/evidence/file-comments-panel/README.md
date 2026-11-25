# Non-Markdown file Comments panel and Files 404

Before-and-after Chromium captures at `1440×900` and `390×844` in light and dark modes (`public/file-panel.js`, `public/files.js`, `public/page/files.js`, `public/page/side-panel.js`, `public/store.js`, `public/auth.js`, `public/style.css`, `public/panels.css`, `public/button-tiers.css`).

## What changed

- **One open/close control for every non-Markdown file:** `files.viewer` (`slot: 'panel'`) is now hosted by core (`public/file-panel.js`). Every non-Markdown viewer — HTML live preview, PDF, images, audio, video, text/code, and binary files, in both the main pane and the split-view side panel beside a chat — gets a consistent toolbar **Comments** toggle with the live unresolved count badge.
- **Remembered per device with calm defaults:** `store.filePanel` (`loam-ui.file-panel`) remembers explicit open or closed choices across files and reloads. With no explicit choice stored, desktop views stay closed on files with zero comments and open automatically once unresolved comments load (until closed); phone views always open the file first with the count badge on the Comments button.
- **Clear close paths and focus handoff:** the panel header includes a close (`×`) button, pressing `Escape` inside the panel closes it (allowing inline comment edits to cancel first) and returns focus to the toolbar toggle, and opening the panel focuses the composer only when there are no open comments.
- **Responsive chrome without squeezing previews:** roomy desktop panes (`> 780px`) animate a side-by-side column without text reflow; narrower desktop split panes (`<= 780px`, such as an HTML artifact beside a chat) keep the preview at full width and slide Comments in as a docked overlay drawer; phones (`390px`) open Comments as a bottom sheet with drag handle, scrim tap, `×`, and `Escape`. All transitions obey `prefers-reduced-motion: reduce`.
- **Warmer, calmer panel presentation:** Loam's shell defines the `--dc-*` token bridge on the panel root, restores compact desktop control geometry, replaces the bare empty line with Loam's speech empty state (*No comments yet* / *All caught up*), hides the redundant *Whole-file comment* label, keeps the resting *Comment* button legible before typing, aligns reply rows, and clears the toast above the composer.
- **Plain Files 404:** missing files, missing folders, and wrong-root URLs (e.g. `/files/40_PROJECTS/...`) render a calm *Not found* empty state with a **Back to Your files** action instead of a red danger card blaming a busy box, and `failureSentence()` no longer maps 404 transport strings to the busy-box message. Retry buttons on genuine `.banner-retry` errors also use high-contrast `--text-inverse` ink.

## Captures (`1440×900` and `390×844`, light and dark)

| Viewport | Mode | HTML empty (before) | HTML empty closed (after) | HTML empty open (after) | Populated (before) | Populated + reply (after) | Resolved / All caught up (after) | Bad-URL 404 (before) | Bad-URL 404 (after) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1440×900 | Light | [before](before-html-empty-initial-1440-light.webp) | [closed](after-html-empty-initial-1440-light.webp) | [open](after-html-empty-open-1440-light.webp) | [before](before-comments-populated-1440-light.webp) | [replied](after-comments-replied-1440-light.webp) | [resolved](after-comments-resolved-empty-1440-light.webp) | [before](before-bad-url-404-1440-light.webp) | [after](after-bad-url-404-1440-light.webp) |
| 1440×900 | Dark | [before](before-html-empty-initial-1440-dark.webp) | [closed](after-html-empty-initial-1440-dark.webp) | [open](after-html-empty-open-1440-dark.webp) | [before](before-comments-populated-1440-dark.webp) | [replied](after-comments-replied-1440-dark.webp) | [resolved](after-comments-resolved-empty-1440-dark.webp) | [before](before-bad-url-404-1440-dark.webp) | [after](after-bad-url-404-1440-dark.webp) |
| 390×844 | Light | [before](before-html-empty-initial-390-light.webp) | [closed](after-html-empty-initial-390-light.webp) | [sheet](after-html-empty-open-390-light.webp) | [before](before-comments-populated-390-light.webp) | [replied](after-comments-replied-390-light.webp) | [resolved](after-comments-resolved-empty-390-light.webp) | [before](before-bad-url-404-390-light.webp) | [after](after-bad-url-404-390-light.webp) |
| 390×844 | Dark | [before](before-html-empty-initial-390-dark.webp) | [closed](after-html-empty-initial-390-dark.webp) | [sheet](after-html-empty-open-390-dark.webp) | [before](before-comments-populated-390-dark.webp) | [replied](after-comments-replied-390-dark.webp) | [resolved](after-comments-resolved-empty-390-dark.webp) | [before](before-bad-url-404-390-dark.webp) | [after](after-bad-url-404-390-dark.webp) |

### Split view (Chat beside HTML file at `1440×900`) & PDF / Image

| Mode | Split view (before) | Split view closed (after) | Split view drawer open (after) | PDF empty (after) | Image empty (after) |
| --- | --- | --- | --- | --- | --- |
| Light | [before](before-split-chat-html-closed-1440-light.webp) | [closed](after-split-chat-html-closed-1440-light.webp) | [open](after-split-chat-html-open-1440-light.webp) | [pdf](after-pdf-empty-1440-light.webp) | [image](after-image-empty-1440-light.webp) |
| Dark | [before](before-split-chat-html-closed-1440-dark.webp) | [closed](after-split-chat-html-closed-1440-dark.webp) | [open](after-split-chat-html-open-1440-dark.webp) | [pdf](after-pdf-empty-1440-dark.webp) | [image](after-image-empty-1440-dark.webp) |

Reproduce from `app/` with `LOAM_PLAYWRIGHT` and `LOAM_CHROME` set:
`COMMENTS_PANEL_EVIDENCE=/tmp/out node test/file-comments-panel-browser.mjs`
(pass `COMMENTS_PANEL_BASELINE=<archive-app-dir>` against an archive of the prior commit for the baseline captures).
