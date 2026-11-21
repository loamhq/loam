# Panel history

Each image pairs two full Chromium screenshots. The original is on the left
and the updated app is on the right. Desktop viewports are 1440 × 900 and
phone viewports are 390 × 844, both at native resolution in Latte light and dark.
The page uses a local fixture broker, the shipped Markdown editor, and
synthetic conversation and file contents.

| View | Desktop light | Desktop dark | Phone light | Phone dark |
| --- | --- | --- | --- | --- |
| Conversation with a draft | [1440 light](chat-1440-light.webp) | [1440 dark](chat-1440-dark.webp) | [390 light](chat-390-light.webp) | [390 dark](chat-390-dark.webp) |
| Document opened from a conversation | [1440 light](doc-from-chat-1440-light.webp) | [1440 dark](doc-from-chat-1440-dark.webp) | [390 light](doc-from-chat-390-light.webp) | [390 dark](doc-from-chat-390-dark.webp) |
| Document opened from a folder | [1440 light](doc-from-list-1440-light.webp) | [1440 dark](doc-from-list-1440-dark.webp) | [390 light](doc-from-list-390-light.webp) | [390 dark](doc-from-list-390-dark.webp) |
| HTML preview | [1440 light](web-1440-light.webp) | [1440 dark](web-1440-dark.webp) | [390 light](web-390-light.webp) | [390 dark](web-390-dark.webp) |
| Embedded app on desktop, catalog on phone | [1440 light](apps-1440-light.webp) | [1440 dark](apps-1440-dark.webp) | [390 light](apps-390-light.webp) | [390 dark](apps-390-dark.webp) |
| Document beside an HTML preview | [1440 light](side-1440-light.webp) | [1440 dark](side-1440-dark.webp) | — | — |
| Embedded app beside a conversation | [1440 light](side-chat-1440-light.webp) | [1440 dark](side-chat-1440-dark.webp) | — | — |
| Empty main after moving its file to side | [1440 light](empty-1440-light.webp) | [1440 dark](empty-1440-dark.webp) | — | — |
| Thread board | [1440 light](board-1440-light.webp) | [1440 dark](board-1440-dark.webp) | [390 light](board-390-light.webp) | [390 dark](board-390-dark.webp) |
| Board document in Files | [1440 light](board-file-1440-light.webp) | [1440 dark](board-file-1440-dark.webp) | [390 light](board-file-390-light.webp) | [390 dark](board-file-390-dark.webp) |
| Thread details | [1440 light](thread-details-1440-light.webp) | [1440 dark](thread-details-1440-dark.webp) | [390 light](thread-details-390-light.webp) | [390 dark](thread-details-390-dark.webp) |
| Profile | [1440 light](profile-1440-light.webp) | [1440 dark](profile-1440-dark.webp) | [390 light](profile-390-light.webp) | [390 dark](profile-390-dark.webp) |
| Settings | [1440 light](settings-1440-light.webp) | [1440 dark](settings-1440-dark.webp) | [390 light](settings-390-light.webp) | [390 dark](settings-390-dark.webp) |
| Phone Activity | — | — | [390 light](activity-390-light.webp) | [390 dark](activity-390-dark.webp) |
| Phone folder | — | — | [390 light](folder-390-light.webp) | [390 dark](folder-390-dark.webp) |
| Phone Chats list | — | — | [390 light](chats-list-390-light.webp) | [390 dark](chats-list-390-dark.webp) |

## Tab and rail switches

These comparisons show the results of selecting each destination by its
visible navigation control. On desktop, the selected rail changes while the
open pane stays in place. Settings uses its Close control at its root.

| Destination | Desktop light | Desktop dark | Phone light | Phone dark |
| --- | --- | --- | --- | --- |
| Chats | [1440 light](tab-chats-1440-light.webp) | [1440 dark](tab-chats-1440-dark.webp) | [390 light](tab-chats-390-light.webp) | [390 dark](tab-chats-390-dark.webp) |
| Files | [1440 light](tab-files-1440-light.webp) | [1440 dark](tab-files-1440-dark.webp) | [390 light](tab-files-390-light.webp) | [390 dark](tab-files-390-dark.webp) |
| Apps | [1440 light](tab-apps-1440-light.webp) | [1440 dark](tab-apps-1440-dark.webp) | [390 light](tab-apps-390-light.webp) | [390 dark](tab-apps-390-dark.webp) |
| Settings | [1440 light](tab-settings-1440-light.webp) | [1440 dark](tab-settings-1440-dark.webp) | [390 light](tab-settings-390-light.webp) | [390 dark](tab-settings-390-dark.webp) |

The left frame uses the app at `d5c3dd6`. The right frame uses the updated trail
boundaries. The browser test also sends Chromium's Back mouse input at both
viewport sizes.

## Earlier panel navigation comparisons

The two board rows compare the cards-only board at `9dff3ff` (left) with the
merged panel history behavior (right). On desktop, Open in Files reveals the
chat and opens the document beside it. Back can reopen the board while the
side editor stays mounted. On the phone, the document’s single Back to board
arrow uses the same history entry as browser Back.

The chat link and Files row both open the same document. The single arrow on
the phone returns to the conversation or the folder according to how the
document was opened. The side examples show each panel's own Back control.

Run `node test/panel-history-browser.mjs` from the app with `LOAM_PLAYWRIGHT`
pointing to the Playwright module and `LOAM_CHROME` to the Chromium executable.
Set `PANEL_HISTORY_EVIDENCE` to a directory for the full-size PNG captures.
The same harness ran against the original app with `PANEL_HISTORY_BASELINE=1`.
