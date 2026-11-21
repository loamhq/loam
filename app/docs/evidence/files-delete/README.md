# Files delete menu and confirmation

Chromium rendered the real app against a fixture broker. Before captures use
the parent commit and its existing Files contract. After captures use the
proposed delete capability and route. The baseline contained no delete
confirmation.

| State | 390 light | 390 dark | 1440 light | 1440 dark |
| --- | --- | --- | --- | --- |
| File row menu | [Before](before/row-menu-390-light.webp) · [After](after/row-menu-390-light.webp) | [Before](before/row-menu-390-dark.webp) · [After](after/row-menu-390-dark.webp) | [Before](before/row-menu-1440-light.webp) · [After](after/row-menu-1440-light.webp) | [Before](before/row-menu-1440-dark.webp) · [After](after/row-menu-1440-dark.webp) |
| Open file menu | [Before](before/header-menu-390-light.webp) · [After](after/header-menu-390-light.webp) | [Before](before/header-menu-390-dark.webp) · [After](after/header-menu-390-dark.webp) | [Before](before/header-menu-1440-light.webp) · [After](after/header-menu-1440-light.webp) | [Before](before/header-menu-1440-dark.webp) · [After](after/header-menu-1440-dark.webp) |
| Confirmation from row | [Capture](after/row-confirm-390-light.webp) | [Capture](after/row-confirm-390-dark.webp) | [Capture](after/row-confirm-1440-light.webp) | [Capture](after/row-confirm-1440-dark.webp) |
| Confirmation from header | [Capture](after/header-confirm-390-light.webp) | [Capture](after/header-confirm-390-dark.webp) | [Capture](after/header-confirm-1440-light.webp) | [Capture](after/header-confirm-1440-dark.webp) |

Run the Chromium check from `app/` with Playwright and Chromium available.
`FILES_DELETE_EVIDENCE` is optional and requests PNG captures at 390 × 844
and 1440 × 900. The committed images are lossless WebP conversions.

```sh
LOAM_PLAYWRIGHT=/path/to/playwright-core/index.mjs \
LOAM_CHROME=/path/to/chromium \
FILES_DELETE_EVIDENCE=/path/to/captures \
node test/files-delete-browser.mjs
```

For the before captures, set `FILES_DELETE_BASELINE` to `app/` in an archive
of the parent commit. Keep its `plugins/` alongside `app/` for the fixture.
