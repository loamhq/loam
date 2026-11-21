# Wide panels

The navigator chooses a list. Main and side each own an explicitly chosen item.
This implements the two-panel design approved on 2026-11-13. It adds device
view state, with no broker route, server setting or deployment change.

## Navigation

From **1180 layout pixels** (after UI scale), a thread row, a file row or file
shortcut, a frameable app row, and a file link in a document offer a small
panel action. Alt/Option-click does the same. Existing file menus and the thread
actions menu also offer **Open to the side**. Touch devices show the small
action without requiring hover. Files folders continue to browse the list;
only files open in a pane. External apps retain their existing browser-tab
behaviour.

A plain list click opens in main. A file link in a chat message opens in the
side panel when the layout has room. ⌘-click on Mac or Ctrl-click elsewhere
opens it in main. On a phone the file fills the screen and Back returns to
the chat. Ordinary web links open in a new tab. Links in a Files preview
navigate the containing panel.
Either pane can open the same item independently. Promote and Swap are available for distinct destinations. Opening a row in main
leaves the side document mounted, even when both now show that row.
The side header offers Promote, Swap, item actions and Close. The file menu
contains view modes, copy and download actions where available. For HTML
previews it also offers **Reload**, plus **Open in new tab** when a live URL
is available. On a phone these HTML actions are in **This file** when the preview
was opened from chat. Thread Board and Tasks are in the thread actions menu.
Selected rows still mean main. A quiet panel mark identifies the visible side item.

Apps uses the same list column as Chats and Files. Its grouped inventory stays
on the left while a chosen app fills main. An empty main panel offers the same
invitation to choose an item across all lists. The selected row identifies main and a quiet panel mark identifies
side. Opening main's app in side creates an independently mounted frame. A site which could not be embedded in main cannot be embedded in side.
Phone Apps retains its catalog and browser-tab behavior.
On desktop, Activity and Settings open as app-wide dialogs above both panels.
Their links out use main-panel navigation and its save guard.
Board and Tasks retain their existing sheet components.

With two panes, Latte action tiers follow the last-used pane. Pointer down or
keyboard focus entering either pane transfers attention, including an embedded
app or file preview. Opening side focuses it; closing side returns attention
to main. A restored side starts focused. The other pane keeps its controls
in supplementary Newspaper paint with identical geometry. A 140ms paint
transition respects reduced motion; no panel ring or border is added.
Navigation and single-pane/phone behavior retain their existing treatment.
Attention is transient and is not saved in the device store. See the
[focus check and captures](evidence/panel-focus/README.md).

## Layout and device state

The divider starts at **56%**, clamps to **32–68%**, and supports pointer drag,
Left/Right arrow keys, and double-click/Home/Enter to reset. Below 1700 layout
pixels, opening side automatically tucks the existing Chats/Files sidebar.
Show sidebar overrides that tuck until side closes. This includes Apps.
An automatic tuck does
not overwrite the device's manual hide preference. Below 1180 there is no
side affordance or Alt-side action. The side surface disappears, retains its
item and thread transport, and returns when enough width is available.
Messages arriving while side is hidden remain unread until it is shown again.
File and app iframes remount on returning from a hidden side surface.

Choosing a rail section reveals a hidden desktop sidebar in that same action,
using the same override as Show sidebar, so Files and Chats always expose the
list used to choose the next item. Chats, Files and Apps choose the list while
the current main document stays mounted: revealing or switching lists does
not close its thread, file or app, change its address, or ask to leave an
unsaved file. Selecting an actual item still navigates main through its usual
save guard. The browser entry remembers the list choice for reload.
Promotion uses a left arrow, distinct from
the sidebar and swap marks. A side file's second header line names its folder
as spaced breadcrumbs, without repeating the filename in the title.

Named container queries apply only while split. Headers, messages, composer,
details and file tools adapt to their pane's width. The single-pane layout
below 1180 keeps its prior rules. Opening and closing use existing motion
durations; reduced motion removes those transitions.
At pane widths of 700 pixels or less, author labels stay in normal flow and
their sticky backdrops disappear, so they scroll away without covering prose.

`localStorage["loam.panels"]` holds the split, the sidebar override and a minimal
side descriptor for address-less hosts. Browser URLs are authoritative for both slots. A file stores root/path, a thread stores its ID, and an app
stores its URL. No file capabilities, revisions, app inventory, messages or
credentials are persisted there. Apps are looked up again on reload; removed
or no-longer-frameable apps show an explanation. A missing or invalid device
store falls back to the ordinary single pane.

The address bar follows the main view. Threads and Files retain their paths.
Apps use `/apps?url=<encoded app URL>`, and plugin items use
`/panel?kind=<kind>&id=<id>`. The `side` query identifies the side item. The
active place and each main visit are stored in the browser entry. Opening or
closing an overlay can update its query on the current entry. When a link
leaves the underlying view, the app clears that overlay query before it records
the destination.

## Back and Forward

The workspace has three histories. The selected tab owns the list history.
Main and side each own a panel history. Each navigation step names its surface,
destination and visit ID. The order of these steps is also the order for
system Back. Focus controls keyboard shortcuts and pane styling.

| Action | Result |
| --- | --- |
| Select Chats, Files, Apps or a plugin section | Reveal that list at its root. Discard the previous list trail. Both panel histories, mounted contents, drafts and reading positions stay in place. Selecting the current tab also returns its list to root. |
| Enter a folder or Archived chats from the list | Add one list step. The list arrow returns to the previous listing. At the tab root it disappears. |
| Open a row, item link, Board, or Thread details | Add a step to the chosen panel. Its arrow restores the preceding panel visit. From its first item, main returns to empty and side closes. |
| Open the same item in the other pane | The receiving pane opens its own view. Use **Open as the main panel** to promote, **Swap** to exchange contents, or **Close side panel** to end that side trail. |
| Open Settings, Activity, the Market, a menu, picker or sheet | Keep all three histories. Back follows the overlay's local parent or dismisses it. Closing a viewer, confirmation or expanded composer reveals the same workspace. |
| Follow a link out of an overlay | Close the overlay and open the item in main. A linked workspace directory or Apps catalog can also be a panel destination. Its browser or listing stays separate from the tab list. Back restores the previous panel and, on phone, the surface that was visible underneath the overlay. |
| Browser Back, Android system Back or history gesture | Handle an open overlay first. Otherwise undo the newest remaining navigation step across all surfaces, even if focus has moved. At a list root a remaining panel step is still eligible. With all three histories at root, Back stays in place within the app document. Navigation to another document is browser-owned. |
| Cmd+[ / Cmd+] or Ctrl+[ / Ctrl+] | Walk Back or Forward in the surface containing focus. Text fields and embedded sites handle their own editing shortcuts. |

On the phone the main panel fills the column. Its tabs can reveal a fresh list
while the panel stays mounted. The panel button beside the tabs returns to that
open item. Moving between these presentations adds no visit. Opening a file
from a nested folder pushes main. At the bottom of main's stack, its arrow
reveals the same tab and folder. The list arrow then walks the folder steps.
Phone app rows that launch an external browser tab keep that behavior.

A main file, thread or app keeps its shareable address on desktop while the
list changes. On phone the address follows the visible surface. The current
browser entry also stores the selected tab, the phone presentation, and the
ordered visits and Forward steps for the three surfaces. Reload can therefore
restore a chat, its companion and a folder unrelated to either. A pasted URL
starts a new workspace. A file opens above its folder ancestry, including its
originating plugin list when supplied. A board or details URL starts above
its conversation. A directory URL opens the list at that directory. The
`side` query opens the companion. An overlay query can restore an explicitly
addressed popup.

### Implementation

`panel-history.js` owns visits and native traversal. An arrow removes the
newest visit for the requested surface. Browser Back removes the newest visit
overall. Forward reapplies an undone visit. Tab selection removes only list
visits. A native traversal that needs a save decision returns to its source
entry until the panel's guard finishes. A refused save keeps its editor and
draft. A confirmed missing thread is removed from its panel's trail.

`app.js` projects the visits onto the list, main and side controllers.
`page/layout.js` keeps stable desktop slots and `panels.js` owns side mounting,
focus, sizing, Promote and Swap. A list change never calls a panel's open or
clear operation. `panel-items.js` describes and renders items. Editors,
transports, selection and scroll positions stay with the mounted controller.

Core and plugins register `rail.section.list({host,wide,collapse})` and
`panel.item.render({host,item,panel,wide})` through the same public registry.
Legacy section `render` remains the desktop list fallback and the phone screen;
`frame`, `open` and `selected` retain their phone meaning. A desktop section
cannot replace the workspace. `panel.open` opens `{item,panel:'main'|'side'}`.
Plugin items use `{kind,id}` identities and receive the owning mount as `host`.
An unavailable kind keeps its identity and displays an explanation until its
plugin registers again. See [the contract](plugins/places.md).

## Mounts and former single-item assumptions

Discovery at `3a4d94c` found factories for threads, files and apps, but their
page integration still assumed one open item:

| Assumption | Resolution |
| --- | --- |
| One thread controller and one stream generation | `page/conversation.js` mounts the same controller and integration in either slot. Each has its own open record, stream controller, cursor, retry, pending permissions and read tracking. The broker processes keep running during navigation and swaps. |
| One composer field and shared attachment/menu/editor state | Both mounts use `page/composer.js`. Side owns its fields, attachment state, expanded editor and menus. Input handlers close over that mount. DOM IDs are distinct. |
| Closing a view parks drafts locally | Handover transfers composer, attachment, quick-add and unsaved Board drafts, plus a data-only visit and reading anchor. Prose DOM is rebuilt in the receiving mount so link callbacks cannot retain the previous destination. File transfers save through the existing edit guard and retain Preview/Edit mode. |
| Thread focus, scrolling and selection use document-wide selectors | `panelDOM()` scopes stream, dock, listener, selection, focus and menu queries. The shared sidebar and global overlays belong to main. |
| A section change replaces the desktop shell; removing Show sidebar moves its following pane | One keyed desktop shell, sidebar slot, main/side slot and divider keep unrelated iframe ancestors in place. The sidebar control has its own key. Browser checks keep a script's edited state through main section changes and opening/closing side. |
| The Files page owns history, default file links and unload | `mountFiles({navigation:false})` provides a second complete viewer/editor without a second history listener. One unload handler checks both mounts. Both retain the original save/conflict guard, source links, embeds, image/PDF validation and live HTML sandbox. |
| Dictation and file speech close over the first field | Dictation owns a local controller per panel through `ui.extension({host, state, store, helpers})`; composer render contexts and field lifecycle events name their host. Core reads only the generic field-activity slot, and audio state stays inside the plugins. File speech receives the actual file, with one shared playback controller. |
| Audio engine belongs to the page | It still does. Both mounts use the same takes and playback engine; starting one pauses the other. A recording in either mount suppresses playback. Visible side rows register before main asks for the floating player. Reveal finds a thread already on side instead of moving it. |
| Sidebar hidden is one boolean | Manual hidden state is preserved; automatic tuck and its override are panel view state. |
| The desktop breakpoint alone determines content layout | The window controls availability; named pane containers control split content. |

`panels.js` owns explicit panel placement; `panel-items.js` owns panel rendering and addresses. The side runtime composes
the existing page mounts and delegates shared host facts, settings and audio
to the application. It does not reproduce a reduced transcript, editor or
composer. No new provider-specific interpretation or broker vocabulary was
added.

## Verification

`node --test test/panels.test.js` covers main/side navigation, moves, swaps,
independent duplicate opens, guarded edits, width boundaries, sidebar tuck and device restore.
The optional Chromium test in that suite runs `test/panels-browser.mjs`.

The browser matrix covers 1180, 1440 and 1920, Latte and Plain, light and dark,
plus 390 in both themes and modes. It uses local fixture brokers and an
isolated content origin. It sends through both real composers, injects separate
streams, uploads a side attachment, exercises mentions and dictation, changes a
side model, opens its Board, edits/saves a file, executes HTML and app scripts,
checks the shared audio engine, moves/swaps items and drafts, drags the divider
across redraws, checks scaled availability, hides/restores side and checks read
marks, then reloads with thread and app in either slot. Audio and speech
recognition are deterministic browser doubles; this does not claim a live
microphone or speech-provider check.

Set `PANELS_EVIDENCE` to a directory to save the three combinations at every
desktop theme/mode/width and the four phone states. Captures return fixture
prose to a consistent reading after the interaction assertions. The committed
[gallery and result](evidence/wide-panels/README.md) record this run.

The continuation that merged the plugin-ownership and authenticated file-link
changes from `97bb4ce` has separate [integration checks](evidence/wide-panels-merge/README.md).
Those checks also start dictation in both panels, cancel one with a scoped
Escape, and verify that neither panel stores audio state in core. The original
gallery above remains the visual record from the first panel implementation.

The subsequent [modal/panel merge checks](evidence/app-modal-panels-merge/README.md)
verify both live conversations and a side iframe beneath Settings/Activity,
including focus, Back/Forward, reload, promotion and refused file saves.

The four staging-review fixes have their own [six-context check and gallery](evidence/wide-panels-review/README.md).
Run it with `PANELS_REVIEW=1` and the same browser command above; it checks
1180/1440/1920 in Latte light/dark, including sidebar selection, promotion,
folder breadcrumbs and author/prose geometry at three scroll positions.

The [follow-up review merge record](evidence/app-modal-review-merge/README.md)
verifies that the review mode and width filter work together alongside the
app-wide dialogs.

The [Apps list and Activity checks](evidence/apps-list-activity/README.md)
cover app selection/moves/swaps/reload, external tabs, all desktop Activity
entry points and conversion from phone Activity when the window widens.

The [independent-surface gallery](evidence/workspace-navigation/README.md) pairs the previous and current behavior at 390 and 1440 in both modes.

The [sidebar/main captures](evidence/sidebar-main/README.md) record the earlier
rail behavior. The [navigation comparison](evidence/navigation-model/README.md)
shows the fresh place roots and the overlay handoff on phone and desktop.

The [panel ownership record](evidence/panel-items/README.md) supersedes the
section-owned desktop frame described in earlier implementation notes. It
includes the delayed Files/Apps opening reproduction, all ordered rail pairs,
plugin item reloads and independent main and side navigation.

The [panel history gallery](evidence/panel-history/README.md) pairs the original
and current headers at desktop and phone widths in both color modes. The
interactive checks run with `node test/panel-history-browser.mjs` when
`LOAM_PLAYWRIGHT` and `LOAM_CHROME` point to Playwright and Chromium.

The [chat link and preview gallery](evidence/chat-file-links/README.md) compares
the chat links, file previews and file actions at phone and desktop widths.
