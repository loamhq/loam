# Back from an agent’s files

The browser fixture starts with the built-in Iris agent, a connected provider
and the untouched, pinned Hello thread. It serves the real app and simulates
the broker’s file and thread responses. The desktop Settings path opens over
Hello. On a phone, Back from Hello reaches Chats, where Settings is available.

On the baseline, the desktop file’s Back reaches the Settings profile’s URL,
then the thread router closes the restored Settings dialog. The capture shows
Hello by itself. When the fixture deletes Hello while its soul file is open,
Back lands on the deleted-thread error. After the fix, Back first returns to
the profile and the originating tab. The Agents chevron on desktop or Back on
a phone returns to the directory. A confirmed missing thread returns to Chats
and repairs the history entry so Forward and Back can still be used.

The test also exercises native browser Back from Config, all the profile’s
file and folder rows, thread links opened in main and beside, a stale direct
thread link, and a missing side thread. A separate desktop case keeps the
Settings profile available when the thread beneath it is deleted.

The browser run uses an external Playwright installation and Chromium.

```sh
LOAM_PLAYWRIGHT=/path/to/playwright/index.mjs \
LOAM_CHROME=/path/to/chromium \
AGENT_FILE_EVIDENCE=/path/to/captures \
node test/agent-file-navigation-browser.mjs
```

For the before images, run the same script with `AGENT_FILE_BASELINE=1`
against an archive of the pre-fix checkout. Images are full viewport
Chromium captures at 1440 × 900 and 390 × 844, converted to WebP.

| View | Desktop light | Desktop dark | Phone light | Phone dark |
| --- | --- | --- | --- | --- |
| Settings profile opens Read soul | [Before](before/settings-read-soul-1440-light.webp) / [After](after/settings-read-soul-1440-light.webp) | [Before](before/settings-read-soul-1440-dark.webp) / [After](after/settings-read-soul-1440-dark.webp) | [Before](before/settings-read-soul-390-light.webp) / [After](after/settings-read-soul-390-light.webp) | [Before](before/settings-read-soul-390-dark.webp) / [After](after/settings-read-soul-390-dark.webp) |
| First Back from Read soul | [Before](before/settings-read-soul-back-1440-light.webp) / [After](after/settings-read-soul-back-1440-light.webp) | [Before](before/settings-read-soul-back-1440-dark.webp) / [After](after/settings-read-soul-back-1440-dark.webp) | [Before](before/settings-read-soul-back-390-light.webp) / [After](after/settings-read-soul-back-390-light.webp) | [Before](before/settings-read-soul-back-390-dark.webp) / [After](after/settings-read-soul-back-390-dark.webp) |
| Second Back from Read soul | [Before](before/settings-read-soul-back-again-1440-light.webp) / [After](after/settings-read-soul-back-again-1440-light.webp) | [Before](before/settings-read-soul-back-again-1440-dark.webp) / [After](after/settings-read-soul-back-again-1440-dark.webp) | [Before](before/settings-read-soul-back-again-390-light.webp) / [After](after/settings-read-soul-back-again-390-light.webp) | [Before](before/settings-read-soul-back-again-390-dark.webp) / [After](after/settings-read-soul-back-again-390-dark.webp) |
| Files tab opens Soul | [Before](before/settings-soul-1440-light.webp) / [After](after/settings-soul-1440-light.webp) | [Before](before/settings-soul-1440-dark.webp) / [After](after/settings-soul-1440-dark.webp) | [Before](before/settings-soul-390-light.webp) / [After](after/settings-soul-390-light.webp) | [Before](before/settings-soul-390-dark.webp) / [After](after/settings-soul-390-dark.webp) |
| Files tab opens Config | [Before](before/settings-config-1440-light.webp) / [After](after/settings-config-1440-light.webp) | [Before](before/settings-config-1440-dark.webp) / [After](after/settings-config-1440-dark.webp) | [Before](before/settings-config-390-light.webp) / [After](after/settings-config-390-light.webp) | [Before](before/settings-config-390-dark.webp) / [After](after/settings-config-390-dark.webp) |
| Files tab opens Every run | [Before](before/settings-map-1440-light.webp) / [After](after/settings-map-1440-light.webp) | [Before](before/settings-map-1440-dark.webp) / [After](after/settings-map-1440-dark.webp) | [Before](before/settings-map-390-light.webp) / [After](after/settings-map-390-light.webp) | [Before](before/settings-map-390-dark.webp) / [After](after/settings-map-390-dark.webp) |
| Files tab opens workspace | [Before](before/settings-workspace-1440-light.webp) / [After](after/settings-workspace-1440-light.webp) | [Before](before/settings-workspace-1440-dark.webp) / [After](after/settings-workspace-1440-dark.webp) | [Before](before/settings-workspace-390-light.webp) / [After](after/settings-workspace-390-light.webp) | [Before](before/settings-workspace-390-dark.webp) / [After](after/settings-workspace-390-dark.webp) |
| Back from workspace | [Before](before/settings-workspace-back-1440-light.webp) / [After](after/settings-workspace-back-1440-light.webp) | [Before](before/settings-workspace-back-1440-dark.webp) / [After](after/settings-workspace-back-1440-dark.webp) | [Before](before/settings-workspace-back-390-light.webp) / [After](after/settings-workspace-back-390-light.webp) | [Before](before/settings-workspace-back-390-dark.webp) / [After](after/settings-workspace-back-390-dark.webp) |
| Files tab opens Memory folder | [Before](before/settings-memory-1440-light.webp) / [After](after/settings-memory-1440-light.webp) | [Before](before/settings-memory-1440-dark.webp) / [After](after/settings-memory-1440-dark.webp) | [Before](before/settings-memory-390-light.webp) / [After](after/settings-memory-390-light.webp) | [Before](before/settings-memory-390-dark.webp) / [After](after/settings-memory-390-dark.webp) |
| Files tab opens Skills | [Before](before/settings-skills-1440-light.webp) / [After](after/settings-skills-1440-light.webp) | [Before](before/settings-skills-1440-dark.webp) / [After](after/settings-skills-1440-dark.webp) | [Before](before/settings-skills-390-light.webp) / [After](after/settings-skills-390-light.webp) | [Before](before/settings-skills-390-dark.webp) / [After](after/settings-skills-390-dark.webp) |
| Thread opens Soul in main | [Before](before/thread-soul-1440-light.webp) / [After](after/thread-soul-1440-light.webp) | [Before](before/thread-soul-1440-dark.webp) / [After](after/thread-soul-1440-dark.webp) | [Before](before/thread-soul-390-light.webp) / [After](after/thread-soul-390-light.webp) | [Before](before/thread-soul-390-dark.webp) / [After](after/thread-soul-390-dark.webp) |
| Thread opens Config in main | [Before](before/thread-config-1440-light.webp) / [After](after/thread-config-1440-light.webp) | [Before](before/thread-config-1440-dark.webp) / [After](after/thread-config-1440-dark.webp) | [Before](before/thread-config-390-light.webp) / [After](after/thread-config-390-light.webp) | [Before](before/thread-config-390-dark.webp) / [After](after/thread-config-390-dark.webp) |
| Thread opens the memory map in main | [Before](before/thread-map-1440-light.webp) / [After](after/thread-map-1440-light.webp) | [Before](before/thread-map-1440-dark.webp) / [After](after/thread-map-1440-dark.webp) | [Before](before/thread-map-390-light.webp) / [After](after/thread-map-390-light.webp) | [Before](before/thread-map-390-dark.webp) / [After](after/thread-map-390-dark.webp) |
| First Back from the thread file | [Before](before/thread-soul-back-1440-light.webp) / [After](after/thread-soul-back-1440-light.webp) | [Before](before/thread-soul-back-1440-dark.webp) / [After](after/thread-soul-back-1440-dark.webp) | [Before](before/thread-soul-back-390-light.webp) / [After](after/thread-soul-back-390-light.webp) | [Before](before/thread-soul-back-390-dark.webp) / [After](after/thread-soul-back-390-dark.webp) |
| Second Back from the thread file | [Before](before/thread-soul-back-again-1440-light.webp) / [After](after/thread-soul-back-again-1440-light.webp) | [Before](before/thread-soul-back-again-1440-dark.webp) / [After](after/thread-soul-back-again-1440-dark.webp) | [Before](before/thread-soul-back-again-390-light.webp) / [After](after/thread-soul-back-again-390-light.webp) | [Before](before/thread-soul-back-again-390-dark.webp) / [After](after/thread-soul-back-again-390-dark.webp) |
| Side-panel Back from Config to Soul | [Before](before/thread-side-back-1440-light.webp) / [After](after/thread-side-back-1440-light.webp) | [Before](before/thread-side-back-1440-dark.webp) / [After](after/thread-side-back-1440-dark.webp) | — | — |
| Back after Hello is deleted | [Before](before/missing-thread-back-1440-light.webp) / [After](after/missing-thread-back-1440-light.webp) | [Before](before/missing-thread-back-1440-dark.webp) / [After](after/missing-thread-back-1440-dark.webp) | [Before](before/missing-thread-back-390-light.webp) / [After](after/missing-thread-back-390-light.webp) | [Before](before/missing-thread-back-390-dark.webp) / [After](after/missing-thread-back-390-dark.webp) |
