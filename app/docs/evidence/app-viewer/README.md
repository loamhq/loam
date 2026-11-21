# App viewer header

Chromium captures of the app shell and a local fixture app. The baseline is
`e0d5f2d`. CSS viewport sizes are 390 × 844 and 1440 × 900, at 100% interface
size. The original main viewer placed its actions directly in the header.

| View | Before | After | Open menu |
| --- | --- | --- | --- |
| 390 light | [Viewer](before-viewer-390-light.webp) | [Viewer](after-viewer-390-light.webp) | [Menu](after-menu-390-light.webp) |
| 390 dark | [Viewer](before-viewer-390-dark.webp) | [Viewer](after-viewer-390-dark.webp) | [Menu](after-menu-390-dark.webp) |
| 1440 light | [Viewer](before-viewer-1440-light.webp) | [Viewer](after-viewer-1440-light.webp) | [Menu](after-menu-1440-light.webp) |
| 1440 dark | [Viewer](before-viewer-1440-dark.webp) | [Viewer](after-viewer-1440-dark.webp) | [Menu](after-menu-1440-dark.webp) |
| 1440 side panel, light | [Viewer](before-side-1440-light.webp) · [Menu](before-side-menu-1440-light.webp) | [Viewer](after-side-1440-light.webp) | [Menu](after-side-menu-1440-light.webp) |
| 1440 side panel, dark | [Viewer](before-side-1440-dark.webp) · [Menu](before-side-menu-1440-dark.webp) | [Viewer](after-side-1440-dark.webp) | [Menu](after-side-menu-1440-dark.webp) |

The Chromium regression measures the 64px header, tests truncation with an
unbroken 168-character name, checks the clipboard and new tab URL, and verifies
that the menu backdrop intercepts a tap aimed at an iframe control. It also
walks Back from Apps, from a chat link, and from a side app to the side chat.

Run `node test/app-viewer-browser.mjs` with `LOAM_PLAYWRIGHT` pointing to an
installed Playwright module and `LOAM_CHROME` to a Chromium executable. Set
`APP_VIEWER_EVIDENCE` to save screenshots. Set `APP_VIEWER_BASELINE=e0d5f2d` to
capture the original viewer using the same fixture.
