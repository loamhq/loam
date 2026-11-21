# Independent list and panel navigation

Each image pairs the previous implementation at `196d2ba` on the left with the new behavior on the right. Phone captures are 390 × 844 CSS pixels and desktop captures are 1440 × 900. All use the Latte theme.

| Sequence | Phone light | Phone dark | Desktop light | Desktop dark |
| --- | --- | --- | --- | --- |
| Open a chat in main | [Light](chat-390-light.webp) | [Dark](chat-390-dark.webp) | [Light](chat-1440-light.webp) | [Dark](chat-1440-dark.webp) |
| Browse Files → Vault → Notes → Old with the chat open | [Light](browse-390-light.webp) | [Dark](browse-390-dark.webp) | [Light](browse-1440-light.webp) | [Dark](browse-1440-dark.webp) |
| Open outline.md in main | [Light](file-390-light.webp) | [Dark](file-390-dark.webp) | [Light](file-1440-light.webp) | [Dark](file-1440-dark.webp) |
| Use main Back after opening the outline | [Light](panel-back-390-light.webp) | [Dark](panel-back-390-dark.webp) | [Light](panel-back-1440-light.webp) | [Dark](panel-back-1440-dark.webp) |
| Open a document in side | — | — | [Light](side-1440-light.webp) | [Dark](side-1440-dark.webp) |
| Visit a profile in side | — | — | [Light](side-profile-1440-light.webp) | [Dark](side-profile-1440-dark.webp) |
| Open a file from Settings → Agents | [Light](settings-file-390-light.webp) | [Dark](settings-file-390-dark.webp) | [Light](settings-file-1440-light.webp) | [Dark](settings-file-1440-dark.webp) |
| Open a file URL, reload, then use main Back | [Light](deep-back-390-light.webp) | [Dark](deep-back-390-dark.webp) | [Light](deep-back-1440-light.webp) | [Dark](deep-back-1440-dark.webp) |
| Switch from the chat to Apps | [Light](apps-390-light.webp) | [Dark](apps-390-dark.webp) | [Light](apps-1440-light.webp) | [Dark](apps-1440-dark.webp) |

The earlier phone interface required returning from the chat before switching tabs. The new tab bar lets the reader browse the list while the chat stays mounted. Its panel button brings the chat back into view. On desktop, the third folder and Apps both show the difference directly in main.

For the side comparison, the earlier build opens the weekly plan from Notes. The new build opens the outline from Old, reloads both panes, then follows a side profile’s file and returns to its Files tab while the chat stays in main. Side is available at desktop width.

The Settings file belongs to a different workspace from the browsed folder. The updated desktop capture keeps Old in the list. The cold-link capture starts with a fresh file URL, so Back reveals its containing folder on both widths.

Run `node test/navigation-model-browser.mjs` with `LOAM_PLAYWRIGHT` and `LOAM_CHROME` pointing to Playwright and Chromium. Set `NAVIGATION_EVIDENCE` to a directory to capture the sequence. The same harness with `NAVIGATION_BASELINE=1` captured the previous build using its available controls. The navigation rules live in [Wide panels](../../wide-panels.md#back-and-forward).
