# Chat file links and preview actions

These comparisons use Chromium with the Loam app, a fixture broker and an
isolated HTML preview origin. Each row pairs the same state before and after
the change. The phone preview is full screen and its actions open as a bottom
sheet. The desktop comparison includes pointer hover and a Markdown note.

| Width | Light | Dark |
| --- | --- | --- |
| 390 px phone | [Before and after](phone-light.webp) | [Before and after](phone-dark.webp) |
| 1440 px desktop | [Before and after](desktop-light.webp) | [Before and after](desktop-dark.webp) |

Run `node test/chat-file-links-browser.mjs` from `app` with `LOAM_PLAYWRIGHT`
and `LOAM_CHROME` pointing to Playwright and Chromium. Set `CHAT_LINKS_EVIDENCE`
to save the individual frames. The test checks link geometry on hover, plain
click and tap, Command- and Ctrl-click, middle-click, external tabs, wiki-link
headings, Reload, Open in new tab, the side note's Edit and Preview actions,
and the file menus for image and PDF previews.
