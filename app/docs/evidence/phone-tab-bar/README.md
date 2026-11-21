# Phone tab bar

Each pair shows the same visit in Chromium before the fix (left, `a51e4b2`) and after it (right). The phone viewport is 390 × 844 and desktop is 1440 × 900. Both use Latte with local fixture data.

| Screen | Phone light | Phone dark | Desktop light | Desktop dark |
| --- | --- | --- | --- | --- |
| Chats root | [Light](chats-root-390-light.webp) | [Dark](chats-root-390-dark.webp) | [Light](chats-root-1440-light.webp) | [Dark](chats-root-1440-dark.webp) |
| Open thread | [Light](chat-390-light.webp) | [Dark](chat-390-dark.webp) | [Light](chat-1440-light.webp) | [Dark](chat-1440-dark.webp) |
| Files root with a mounted thread | [Light](files-root-390-light.webp) | [Dark](files-root-390-dark.webp) | [Light](files-root-1440-light.webp) | [Dark](files-root-1440-dark.webp) |
| Nested Files folder | [Light](browse-390-light.webp) | [Dark](browse-390-dark.webp) | [Light](browse-1440-light.webp) | [Dark](browse-1440-dark.webp) |
| Open file | [Light](file-390-light.webp) | [Dark](file-390-dark.webp) | [Light](file-1440-light.webp) | [Dark](file-1440-dark.webp) |
| File opened from Settings | [Light](settings-file-390-light.webp) | [Dark](settings-file-390-dark.webp) | [Light](settings-file-1440-light.webp) | [Dark](settings-file-1440-dark.webp) |
| Apps root | [Light](apps-390-light.webp) | [Dark](apps-390-dark.webp) | [Light](apps-1440-light.webp) | [Dark](apps-1440-dark.webp) |
| Open app | [Light](app-panel-390-light.webp) | [Dark](app-panel-390-dark.webp) | [Light](app-panel-1440-light.webp) | [Dark](app-panel-1440-dark.webp) |
| Archived chats | [Light](archived-390-light.webp) | [Dark](archived-390-dark.webp) | [Light](archived-1440-light.webp) | [Dark](archived-1440-dark.webp) |
| Agent profile | [Light](profile-390-light.webp) | [Dark](profile-390-dark.webp) | [Light](profile-1440-light.webp) | [Dark](profile-1440-dark.webp) |
| Activity | [Light](activity-390-light.webp) | [Dark](activity-390-dark.webp) | [Light](activity-1440-light.webp) | [Dark](activity-1440-dark.webp) |

The Files root shows the round button for returning to a mounted thread. The embedded app was opened on desktop and then narrowed to 390. Ordinary phone app rows launch a browser tab.

The [browser test](../../../test/navigation-model-browser.mjs) follows a file link from Settings, uses browser Back to restore the root and taps the round button to return to the thread. Set `LOAM_PLAYWRIGHT` and `LOAM_CHROME` to Playwright and Chromium, then run `node test/navigation-model-browser.mjs`. Set `NAVIGATION_EVIDENCE` to save the captures.
