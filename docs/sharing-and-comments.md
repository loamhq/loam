# Sharing and Comments & suggestions

Two independent built-in plugins replace the earlier combined `doc-comments`
plugin and generalize both sharing and commenting to **any file in the vault**
(not only Markdown):

- **`plugins/sharing/` ("Sharing")**: share any vault file (never folders)
  behind a password-protected link (`/s/<token>`), view it on a clean
  standalone page, browse **Files › Shared** (*Shared by me* and *Shared with
  me*), and manage links from agents.
- **`plugins/comments/` ("Comments & suggestions")**: whole-file comments on
  any non-Markdown file (PDF, images, SVG, HTML, audio, video, code, archives),
  plus the calm Markdown editor with anchored comments, suggestions,
  accept/reject, authorship tinting, and save history — privately with your
  agents, and collaboratively on shared links when Sharing is also enabled.

Each plugin works completely on its own with zero dead controls in all four
on/off combinations, and neither imports the other's code or reads the other's
private data.

---

## Architecture and public inter-plugin interfaces

Both plugins coordinate strictly through public, general Loam interfaces:

### 1. Backend plugin services (*new*)

- **Manifest**: `contributes.plugin.service` declares a named, versioned
  loopback HTTP mount (`{interface, version, mount, page?: {entry, styles}}`).
- **Broker discovery**: `GET /v1/plugin-services?interface=<name>` returns only
  currently **enabled and running** plugins that declare that interface, with
  each plugin's loopback URL (`http://127.0.0.1:<port><mount>`).
- **Loopback only**: plugin servers reject `/collab/...` and `/remote/...`
  whenever `X-Forwarded-Prefix` is present, so browser `/x/<id>/` requests
  cannot reach internal service mounts.

Two interfaces are defined at version 1:

1. **`share.collaboration` v1** (provided by `comments` at `/collab`, consumed
   by `sharing`):
   - `GET /collab/capabilities` — supported access modes (`markdown: [view,
     comment, edit]`, `other: [view, comment]`) and share-page ES module/CSS
     paths.
   - `POST /collab/summary` — open/resolved/new counts for shared files in
     Files › Shared.
   - `GET /collab/appear?root=…&path=…` — effective agent-appearance mode
     (`names` or `one`) and agent list so Sharing's group-link name check does
     not disclose hidden agent names.
   - `GET /collab/asset/<rel>` — serves the transitively discovered ES module
     graph of `page/share-collab.js` and `assets/comments.css`.
   - `GET/POST/PATCH/DELETE /collab/doc…` — visitor collaboration with
     `X-Loam-Share-Actor` (verified actor and effective mode) and
     `X-Loam-Share-File` (vault file resolved by Sharing). Comments strips
     Markdown frontmatter on output, preserves it on save, refuses owner-only
     history routes (`404`), and masks agent names when `appear` is `one`.
2. **`share.remote` v1** (provided by `sharing` at `/remote`, consumed by
   `comments`):
   - `GET/POST/PATCH/DELETE /remote/<wid>/doc…` — proxies collaboration calls
     to another Loam's share door for a file in *Shared with me*
     (`shared:w-…`), obeying `X-Loam-Appear: names|one` to decide whether to
     send `X-Loam-Agent-Name`.

### 2. Page places (*new slots*)

- **`files.viewer` (`slot: 'panel'`, *new*)**: attaches a companion panel to
  any file viewer (`app/public/files.js` and Sharing's `@sharing` viewer).
  Returns `{action, node}` — on desktop (`wide: true`), `node` sits in the side
  column beside the file; on phones (`wide: false`), `action` draws a header
  button that opens a bottom sheet.
- **`files.action` (`slot: 'action'`)**: draws header actions on open files
  (both core `builtinFileView` and Comments' Markdown editor); Sharing uses it
  for the `Share` button with recipient faces.
- **`files.share` (*new*)**: UI extension points inside a sharing plugin's
  dialogs and lists (`slot: 'sheet'` for invite-card notes notice & per-file
  `appear` picker, `slot: 'footer'` for the default `appear` selector at the
  foot of Files › Shared, `slot: 'withme-menu'` for per-remote `appear`).

### 3. Standalone share page (`/s/<token>`)

When `share.collaboration` v1 is active, Sharing proxies its declared assets at
`/s/<token>/_/ext/<plugin>/<rel>`, loads its stylesheet, and dynamically
imports `page/share-collab.js` (`mountMarkdown` for Markdown, `mountPanel` for
every other file type). When no collaboration plugin is active, Sharing serves
view-only pages with zero collaboration scripts or controls.

---

## Unified comment model

All comments live in `<system>/Loam/data/comments/<id>.json` (`loam-comments`
v1 schema, preserved across Loam updates):

- **Markdown files (`.md`, `.markdown`)**: anchored comments and suggestions
  (`anchor: {quote, prefix, suffix}`), accept/reject/withdraw/reattach,
  three-way merge saves, authorship tinting, and save history. One `loam_doc_id`
  frontmatter line is added on the first note.
- **All other file types**: whole-file comments using the exact same thread
  record with `anchor: null`, replies, inline edit/delete of own words,
  resolve/reopen. Non-Markdown files are **never modified**.

---

## Tracking across rename, move, delete, and restore

Core `lib/loam_files.py` records vault mutations (`move`, `delete`, `restore`)
to a bounded local journal (`GET /v1/files/events`) and provides non-symlink
`GET /v1/files/fingerprint` (`dev`, `ino`, `sha256`, `size`, optional Markdown
`doc_id`) and `GET /v1/files/locate`. `mcp/read_epub_server.py`'s `move_file`
writes to the same journal.

Both plugins keep private path-tracking indexes in `LOAM_PLUGIN_PRIVATE`:

- **Rename or move** (Files `⋯` Rename/Move, `mcp__loam__move_file`, parent
  folder rename, or external disk `mv`): both comments and active share links
  follow the file cleanly.
- **Delete**: active share links immediately return `404`; comment sidecars
  remain dormant. Creating a brand-new unrelated file at the deleted path does
  **not** inherit old links or old comments.
- **Restore**: restoring the trashed file re-activates its share links and
  re-attaches its comments.
- **Copy**: copies start clean with zero comments and zero share links.

---

## Active-content security and containment

Shared files are served from the existing app host at `/s/<token>` (no new
public hostnames or DNS required). Every response enforces `X-Content-Type-Options:
nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`, and
`X-Robots-Tag: noindex, nofollow, noarchive, nosnippet`:

- **HTML (`.html`, `.htm`)**: rendered exclusively inside `<iframe
  sandbox="allow-scripts allow-forms" referrerpolicy="no-referrer"
  src="/s/<token>/frame">`. The `/frame` response carries
  `Content-Security-Policy: sandbox allow-scripts allow-forms; default-src
  'none'; script-src 'unsafe-inline' 'unsafe-eval' data: blob:; style-src
  'unsafe-inline' data: blob:; img-src data: blob:; font-src data: blob:;
  media-src data: blob:; connect-src 'none'; frame-src 'none'; object-src
  'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'` and
  `X-Frame-Options: SAMEORIGIN`. Omitting `allow-same-origin` guarantees an
  opaque `null` origin both framed and on direct top-level navigation to
  `/frame` (`document.cookie`, `localStorage`, `window.parent`, and every
  `fetch`/`XHR` fail), while single-file interactive HTML mockups continue to
  work. `/raw` for HTML always forces `application/octet-stream` download.
- **SVG (`.svg`)**: displayed in `<img src="/s/<token>/raw">` and served with
  `Content-Security-Policy: sandbox; default-src 'none'; style-src
  'unsafe-inline'; img-src data:` (no `allow-scripts`, no `allow-same-origin`),
  blocking `<script>` and `onload` both inline and on direct navigation.
- **PDF and raster images**: leading magic bytes are verified on the server
  before inline serving; spoofed files fall back to `application/octet-stream`
  with `Content-Disposition: attachment` and `Content-Security-Policy: sandbox;
  default-src 'none'`.
- **Audio and video**: served via `/raw` with `Accept-Ranges: bytes` and `206
  Partial Content` range forwarding.
- **Path and symlink containment**: the target file is resolved solely from the
  link's private `fid`; URL segments, query parameters, traversal attempts, and
  symlinks escaping the vault are rejected.
- **Shared with me**: every remote `/frame`, `/raw`, and `/download` response
  is re-classified, magic-checked, and wrapped in the same local sandbox CSPs
  before reaching this Loam's browser.

---

## Agent tools

- **Sharing (`plugins/sharing/tools/sharing_mcp.py`)**: `share_list`,
  `share_links`, `share_create`, `share_update`, `shared_with_me`,
  `shared_add`, `shared_remove`, `shared_read`.
- **Comments (`plugins/comments/tools/comments_mcp.py`)**: `file_notes`,
  `file_comment`, `file_reply`, `file_resolve`, `file_reopen`, `doc_suggest`,
  `doc_accept`, `doc_reject`, `doc_save`, `comments_appear`.

---

## Migration notes for existing installs

When updating an install that previously enabled `doc-comments`:

1. **Back up notes first** if the owner wants a readable copy of pre-split
   comments from `<system>/Loam/data/doc-comments/*.json`.
2. **Disable/remove old `doc-comments` state** (`loam-plugin-doc-comments.service`
   and `plugin.doc-comments.*` settings).
3. **Enable `sharing` and/or `comments`** independently in Settings › Plugins
   (or `bin/plugins enable sharing` / `bin/plugins enable comments`).
4. **Protocol version**: the share door speaks `loam_share` protocol `2`
   (`min: 2`) to support all file types and separate collaboration endpoints;
   links shared from older v1 installs still open in a browser until updated.
5. **Existing share links**: pre-split links stored under
   `state/plugins/private/doc-comments/links.json` are not imported
   automatically; recreate links under Sharing so they are tracked by
   `FileTracker` across all file types.
