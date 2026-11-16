# Renaming files and folders

An open file's title is an editable button with a quiet pencil when its latest
host row has `capabilities.rename === true`. Tap it to edit the full filename.
The stem is selected, keeping the extension ready to use. If the replacement
contains no dot, the existing extension is retained; a typed extension replaces
it. Folders keep exactly the name typed. Enter or blur submits, Escape or the
cancel mark cancels, and the save mark also submits. Unchanged names close
without a request. The field retains typing focus and the host's error sentence
on a refusal. Errors appear below it, with no native alert.

Rename also appears in the file's ⋯ menu, file and folder row menus, the current
folder menu and shortcut menus. Shortcuts store addresses, so their menu reads
`stat` before offering a capability. A missing shortcut can still be removed.
Unknown or false rename capability never implies permission from `write`.
HTML's compact header opens the ordinary title area while its name is edited,
leaving enough room on a phone for the field, confirmation and error.

## Relay contract

`POST /api/files/rename` relays JSON to `POST /v1/files/rename`, authenticated
with the server's broker token. The browser never receives that token.

```json
{"root":"vault","path":"Inbox/draft.md","name":"Final draft.md"}
```

The host also accepts `to` for a new path within the same root, instead of
`name`. Exactly one is required. The relay passes either shape through; the
page's rename interaction sends `name` only. Moves are not a separate page
interaction. System remains absent from the Files browser; the host's rename
refusals, including its 403 for System, pass through this new route.

Success is HTTP 200 with the ordinary flat Files row at its new address,
including `capabilities.rename`, plus:

```json
{"from":{"root":"vault","path":"Inbox/draft.md","link":"/files/vault/Inbox/draft.md"}}
```

The returned `name`, `path`, `link` and capabilities are authoritative. The host
normalizes the name to NFC and trims it. Invalid/secret/hidden names are 400;
authentication failures 401; forbidden or pinned resources 403; missing items
or destination folders 404; occupied destinations 409. All use
`{"error":"a sentence"}`, optionally with authority/pinned detail. These
statuses and details pass through. Other relay methods return 405.

The host moves the item without rewriting its contents or modified time. A
viewer-only rename posts no text write. The editor's normal autosave still
applies: an in-flight save settles before rename; a save requested during rename
waits and uses the returned path. Draft text, revision and editing mode remain
on the open document. Changing an extension updates its viewer type.

## Following the address

The response's `from` updates the open document, listed row and share/download
links, replaces the current browser address (preserving its fragment), and
updates device-saved shortcuts and hidden shortcut dismissals. Folder renames
update descendant paths too, with root and segment boundaries. Inbox/daily
folder preferences follow if they point inside that folder. No file content
or embedded note link is rewritten by the page.

A renamed host suggestion becomes a personal shortcut at its new path and the
old suggestion is dismissed. It therefore stays usable after reload even if
the host's next roots answer still suggests the old address.

Other open tabs on the same origin receive the accepted answer through
`BroadcastChannel` and update their open documents, addresses and shortcuts.
This is browser-tab continuity; the host has no external file-change stream.
There is currently one open file per page and no recent-file list or document
tab registry to migrate. Files shortcuts remain device preferences under the
existing storage contract, rather than becoming a new server setting.

The originating page emits `file.renamed` with the host's complete answer for
plugins. The existing `file.changed` event remains a text-save notification.

Run `npm test` for the standard suite, including rename relay and controller
tests. The [evidence guide](evidence/files-rename/README.md) names the optional
Chromium interaction/capture command and its scratch dependencies.
