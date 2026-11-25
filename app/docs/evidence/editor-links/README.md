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
  Suggesting (never in Viewing or on touch): opaque popover surface placed just
  below the link line, flipping above near the bottom of the viewport, hovered
  link winning when the caret is in another link, hiding promptly on mouse
  leave unless moving onto the chip; editing via the chip and `Mod-K` for both
  Markdown links and wiki-links, and touch keyboard-bar Link editing;
- single unified header row when a Markdown note is open in the side panel
  (beside another note or beside a chat), with title and path, mode, primary
  actions (Share), a single More menu holding editor actions plus Swap and
  Open as main panel, and Close; HTML side panel header unchanged;
- chat links unchanged.
