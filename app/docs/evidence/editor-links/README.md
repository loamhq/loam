# Markdown editor links

These captures use Chromium with the Loam app, a fixture broker, an isolated
HTML preview origin, a configured published vault URL prefix, and the real
`plugins/comments` Markdown editor mounted in Files main and beside a chat.

| Width | Light | Dark |
| --- | --- | --- |
| 390 px phone | [Phone light](phone-light.webp) | [Phone dark](phone-dark.webp) |
| 1440 px desktop | [Desktop light](desktop-light.webp) | [Desktop dark](desktop-dark.webp) |

Run `node plugins/comments/test/editor-links-browser.mjs` from the repository
root with `LOAM_PLAYWRIGHT` and `LOAM_CHROME` pointing to Playwright and
Chromium (set `EDITOR_LINKS_EVIDENCE=<dir>` to save individual frames).

The suite verifies across Editing, Suggesting, and Viewing modes at 390 px and
1440 px in light and dark:
- pointer cursor and `data-url` / `data-wiki` attributes on collapsed links in
  every mode;
- plain click on wiki-links, vault Markdown links, published vault URLs, and
  web links routing through `host.openFileRef` (opening beside when wide,
  full-screen with Back on phone, new tab for web links, and in-app for
  published vault URLs);
- Cmd/Ctrl-click opening in main from both Files main and the side panel;
- desktop hover and keyboard-caret link chip (`target | Edit`) in Editing and
  Suggesting (never in Viewing or on touch), editing via the chip and `Mod-K`
  for both Markdown links and wiki-links, and touch keyboard-bar Link editing;
- chat links unchanged.
