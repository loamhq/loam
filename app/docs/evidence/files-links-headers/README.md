# Files links and unified file headers

These comparisons use Chromium with the Loam app, a fixture broker, an isolated
HTML preview origin, a fixture Share + Comments-toggle plugin, and the mounted
Markdown editor plugin. Each row pairs the same state before and after the
change.

| Width | Light | Dark |
| --- | --- | --- |
| 390 px phone | [Before and after](phone-light.webp) | [Before and after](phone-dark.webp) |
| 1440 px desktop | [Before and after](desktop-light.webp) | [Before and after](desktop-dark.webp) |

Run `node test/files-links-headers-browser.mjs` from `app` with
`LOAM_PLAYWRIGHT` and `LOAM_CHROME` pointing to Playwright and Chromium. Set
`FILES_LINKS_EVIDENCE` to save individual frames and `FILES_LINKS_BASELINE=1`
against the commit before to capture the baseline column. The test verifies
inline file links without `.filelink-side` buttons, plain-click and Cmd/Ctrl-click
routing across Files main, side panel, chat messages and the mounted Markdown
editor's Viewing mode, web links in new tabs, primary-only headers (`Share`,
`Comments`, `⋯`) across HTML, Markdown, image, PDF, audio, text and binary
viewers in both main and side, `Reload` / `Open in new tab` / `View source` ↔
`Preview` inside `⋯`, and phone Back navigation.
