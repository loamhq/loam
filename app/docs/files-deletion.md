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
A status message offers **Undo** for five seconds. Undo restores the file
to its original path and refreshes the folder listing. The panel stays on
the previous visit it returned to after deletion. Undo restores the saved
file, with its original bytes, permissions and modified time.

## Broker contract

The app and the persona's `delete_file` and `restore_file` tools use the
same authenticated broker operations for vault files.

| Request | Expected response |
| --- | --- |
| Files list, stat and read rows | `capabilities.delete: true` for a deletable file |
| `POST /api/files/delete` with `{"root":"vault","path":"Notes/draft.md"}` | Relays to `POST /v1/files/delete` with the broker token |
| Successful broker deletion | HTTP `200` with `{"ok":true,"restore_id":"…"}` |
| `POST /api/files/restore` with `{"root":"vault","restore_id":"…"}` | Relays to `POST /v1/files/restore` and returns the restored row with `"ok":true` |
| Broker refusal | Status and `{"error":"…"}` are relayed to the confirmation |

Files trash is kept under the vault's `.trash/loam-files-trash/` directory
until restored or cleared by an operator. Restoration requires the original
folder and a free filename. A collision keeps the entry in trash. Only
regular vault files eligible for rename have the delete capability. The
browser holds saves from either mounted pane while a confirmed delete is pending.

`npm test` covers the relay and the Files interaction with a fixture broker.
The [browser captures](evidence/files-delete/README.md) show the rendered app
against the same contract.
