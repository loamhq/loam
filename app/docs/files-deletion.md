# Deleting a vault file

The Files list row and file header share a ⋯ menu. It ends with **Delete** when
the broker reports `capabilities.delete: true` on a vault file row. A phone
opens a bottom sheet and desktop opens a centered dialog. Both confirmations
name the file, focus **Cancel**, and keep the file and the broker’s error in
view if deletion fails. An open draft is called out in the confirmation.

After the broker confirms deletion, the app removes the file row and any
shortcut to it. The app drops visits to that file from both panels’ Back and
Forward trails. Each panel that showed the file returns to its previous
visit, following the [workspace navigation model](wide-panels.md#back-and-forward).
A brief status message announces the removal. Undo needs a broker-managed
restorable trash operation. A Git history may lack the file or its latest
contents, so this confirmation promises no Undo.

## Broker dependency

The current broker has no Files delete endpoint. The persona’s `delete_file`
tool is a separate filesystem entry point. The app is ready for the following
contract and hides Delete until a broker advertises it.

| Request | Expected response |
| --- | --- |
| Files list, stat and read rows | `capabilities.delete: true` for a deletable file |
| `POST /api/files/delete` with `{"root":"vault","path":"Notes/draft.md"}` | Relays to `POST /v1/files/delete` with the broker token |
| Successful broker deletion | HTTP `200` with `{"ok":true}` |
| Broker refusal | Status and `{"error":"…"}` are relayed to the confirmation |

The platform supplies the deletion operation and exposes that same operation
to personas for vault files. The broker owns path validation, vault authority,
symlink handling, and coordination with concurrent writes. Folders, pinned
files and other roots should never advertise this capability. The browser
holds saves from either mounted pane while a confirmed delete is pending.

`npm test` covers the relay and the Files interaction with a fixture broker.
The [browser captures](evidence/files-delete/README.md) show the rendered app
against the same contract.
