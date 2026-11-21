# Place history and overlays

Each comparison pairs Chromium screenshots at native resolution. The left
image uses `8c5ba6c`. The right image uses the unified navigation model.
The fixtures use synthetic chats, files, profiles, and apps. Phone viewports
are 390 × 844 and desktop viewports are 1440 × 900.

| View | 390 light | 390 dark | 1440 light | 1440 dark |
| --- | --- | --- | --- | --- |
| Chats after selecting its tab | [390 light](place-chats-390-light.webp) | [390 dark](place-chats-390-dark.webp) | [1440 light](place-chats-1440-light.webp) | [1440 dark](place-chats-1440-dark.webp) |
| Files after selecting its tab | [390 light](place-files-390-light.webp) | [390 dark](place-files-390-dark.webp) | [1440 light](place-files-1440-light.webp) | [1440 dark](place-files-1440-dark.webp) |
| Apps after selecting its tab | [390 light](place-apps-390-light.webp) | [390 dark](place-apps-390-dark.webp) | [1440 light](place-apps-1440-light.webp) | [1440 dark](place-apps-1440-dark.webp) |
| Nested Files folder | [390 light](files-folder-390-light.webp) | [390 dark](files-folder-390-dark.webp) | [1440 light](files-folder-1440-light.webp) | [1440 dark](files-folder-1440-dark.webp) |
| File opened by a deep link | [390 light](deep-file-390-light.webp) | [390 dark](deep-file-390-dark.webp) | [1440 light](deep-file-1440-light.webp) | [1440 dark](deep-file-1440-dark.webp) |
| Board opened by a deep link | [390 light](deep-board-390-light.webp) | [390 dark](deep-board-390-dark.webp) | [1440 light](deep-board-1440-light.webp) | [1440 dark](deep-board-1440-dark.webp) |
| Agent profile inside Settings | [390 light](settings-agent-390-light.webp) | [390 dark](settings-agent-390-dark.webp) | [1440 light](settings-agent-1440-light.webp) | [1440 dark](settings-agent-1440-dark.webp) |
| Connections inside Settings | [390 light](settings-section-390-light.webp) | [390 dark](settings-section-390-dark.webp) | [1440 light](settings-section-1440-light.webp) | [1440 dark](settings-section-1440-dark.webp) |
| File opened from Settings | [390 light](settings-file-390-light.webp) | [390 dark](settings-file-390-dark.webp) | [1440 light](settings-file-1440-light.webp) | [1440 dark](settings-file-1440-dark.webp) |
| Back from the Settings file | [390 light](settings-file-back-390-light.webp) | [390 dark](settings-file-back-390-dark.webp) | [1440 light](settings-file-back-1440-light.webp) | [1440 dark](settings-file-back-1440-dark.webp) |
| Activity overlay | [390 light](activity-390-light.webp) | [390 dark](activity-390-dark.webp) | [1440 light](activity-1440-light.webp) | [1440 dark](activity-1440-dark.webp) |

The Settings-file return shows the originating desktop chat and the phone’s
Files folder. The baseline reopened a Settings profile on desktop and left
the phone on its foreground profile. The route comparison includes browser
Back via Chromium input. The phone viewport uses touch emulation.

From `app/`, run `node test/navigation-model-browser.mjs` with
`LOAM_PLAYWRIGHT` pointing to the Playwright module and `LOAM_CHROME` to the
Chromium executable. Set `NAVIGATION_EVIDENCE` to save the full PNGs.
Set `NAVIGATION_BASELINE=1` when running the harness against the earlier app.
