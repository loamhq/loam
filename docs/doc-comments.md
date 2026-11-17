# Comments and suggestions on docs

*Added 2026-11-16. The plugin is `plugins/doc-comments/`; its tests are
`tests/test_doc_comments.py` and the plugin's own `test/`. This page is the
design as it was decided and built, so the project carries its own reasons.*

Google-Docs-style comments and suggestions on any Markdown file in the
vault, for the owner and their agents, in a calm editor inside Loam. It is
built in two steps. **Step 1 (this)**: everything inside Loam, no sharing.
**Step 2**: share links for outside people and their agents, a standalone
page for them, and a Shared Docs section in Files; step 1 is built so step 2
adds and does not rewrite (below, "What step 2 adds").

## Principles

1. **The `.md` file is the truth and stays clean.** Nothing about comments,
   suggestions or authorship is written into its body. One id line goes into
   its frontmatter at the first note.
2. **One model, many clients.** The editor, the agents' tools and (step 2)
   the share page and an outside agent's HTTP calls all go through the same
   operations, on the plugin's server.
3. **The file changes underneath.** Agents edit on disk and the owner edits
   in other apps, so a note points at a quote that finds itself again, and
   nothing is ever lost.
4. **Calm.** Nothing pings anyone; nothing new is visible at rest. Nothing
   in the editor wakes an agent, and no dot or badge appears in Files or the
   rail for any of this.

## What the person sees

- **Three ways of working** on one pill in the header: **Editing** (change
  the text), **Suggesting** (typing becomes suggestions), **Viewing** (read;
  the margin folds away and small marks in the gutter open a note).
- **Notes at rest** are open text in the margin beside their passages: a
  small face, the name, two lines, "1 reply". Only the focused thread lifts
  onto a soft surface with its time, replies, reply field and actions; the
  others dim. Passages carry a thin underline at rest and a soft fill when
  focused. Suggestions show the old words faint and struck and the new words
  in the theme's accent: no red, no green.
- **A comment is resolved** (✓ in the focused note's corner, with Undo); **a
  suggestion is accepted or rejected**. Closed notes sit behind a count in
  the ⋯ menu (Resolved), each reopenable (an accepted suggestion is part of
  the text: History restores the text from before it).
- **⋯** holds Show authors (tints the text by who wrote it, off by default),
  History (every save, newest first, each restorable, and the Detached
  notes, each re-attachable), Resolved, and This file… (Files' own menu:
  rename, move, copy link, download).
- **Formatting, desktop**: no toolbar. Selecting text in Editing shows one
  popup: Aa (block type), bold, italic, strikethrough, link, code, then
  Comment and Suggest; each button's tooltip names the Markdown and the
  shortcut. On an empty line a faint "+" sits in the gutter, and "/" opens
  the same block menu, each row showing its Markdown. Typed Markdown keeps
  working: the marks show around the caret and hide elsewhere.
- **Phone**: dots in the gutter, a thread in a bottom sheet, no popup over a
  selection (the phone's own menu lives there). The bar above the keyboard
  is the one home for formatting (Aa, bold, italic, link, list, checklist,
  Comment, and dictation's mic); with a selection its end becomes Comment ·
  Suggest. In Viewing a selection brings up a slim bar with Comment.
- **What the old editor drew keeps a home**: the frontmatter is a quiet
  Properties line above the title (click to open the rows; click a row to
  edit the YAML); task boxes are real checkboxes; callouts (`> [!note]`) are
  tinted blocks with their kind; embedded pictures draw in place; tables draw
  as tables until the caret enters them; wiki-links are links (⌘/Ctrl-click,
  or a click in Viewing); dictation's mic is in the header (desktop) and the
  keyboard bar (phone); read aloud's button is in the header.
- **Share** is absent in step 1 rather than inert: a button that does
  nothing is clutter. Step 2 adds it to the header.

## How it is built

```
plugins/doc-comments/
  PLUGIN.md              the manifest: page, server, agent tool and prompt
  server/main.py         the operations over HTTP on the plugin's port
  server/doccomments/    the model: anchor, merge, sidecar, service, store
  tools/notes_mcp.py     the agents' doc_* tools (stdio MCP, no dependencies)
  page/main.js           Loam's adapter: a files.viewer for Markdown
  page/editor/           the editor component, standalone
  page/vendor/           CodeMirror 6, one pinned prebuilt module
  assets/doc-comments.css
  test/                  model, editor and page tests; a standalone harness
```

It is a **first-party plugin on the public interface**, built as a third
party would build it. Where the interface had no place for what it needed,
core gained a general hook any plugin can use:

| Hook | Where | What |
|---|---|---|
| `files.own(file, {dirty, save, edit, field})` | app `public/files.js` | a plugin's editor holds the open file: Files' edits, saves, unsaved-edit guard and dictation's field go to it (app docs/plugins/places.md) |
| an agent tool's environment | lib/loam_contributions.py | `LOAM_AGENT`, `LOAM_LANE`, `LOAM_VAULT`, `LOAM_WORKSPACES`, `LOAM_PLUGIN_URL` (docs/plugins.md) |
| a plugin's kept vault folder | lib/loam_skills.py, lib/loam_update.py, lib/loam_plugin_servers.py | `<system>/Loam/data/<id>/`, kept by updates, told as `LOAM_VAULT_DATA` (docs/update.md) |
| a save made for a persona | lib/loam_files.py | `PUT /v1/files/text` takes `by`, and the provenance stamp names that persona (broker/README.md) |

### The server holds the model

The anchors, the merge and the sidecar live on the plugin's server, not in
the page, because step 2's outside agents call the same operations over
HTTP, and one writer per doc is simpler than two that must agree. The server
reads and writes **only through the broker's Files routes**
(`server/doccomments/store.py`, `BrokerStore`), exactly as the page's own
editor does, so the broker's file guard, its revisions and its provenance
stamp apply. It opens no path of the machine. Every write names the
revision it was made against; a lost race is read again and redone.

Who is calling: a request through Loam's plugin gateway (behind the login)
is the owner's. An agent's tool calls the server on loopback with
`X-Loam-Agent: <id>`, checked against the broker's agents.

Operations (`server/main.py` has the routes): read a doc with its notes;
add a comment or a suggestion (by position at a revision, or by quote, with
an occurrence or the words just before it when it occurs more than once);
reply; change one's own words; resolve, reopen, accept, reject, re-attach;
save (merged); history; one version's text; restore.

### The editor: CodeMirror 6

**Chosen:** CodeMirror 6 over the Markdown source, with decorations that make
it read as the rendered doc (marks hidden away from the caret, headings and
lists drawn, blocks as widgets), and with the notes, suggestions and authors
as decoration layers.

**Why:** the file is the truth and must round-trip byte for byte. An editor
whose document *is* the file's text (CodeMirror) gives that by construction:
loading and reading back changes nothing, and every edit is an ordinary text
change that the merge, the anchors and the history already understand. Its
decorations do the rest of the look, and its change sets map positions
through edits (a save's answer, a concurrent change) so the caret and the
passages never jump.

**The trade, and the alternative:** a rich-text editor (ProseMirror, or a
framework on it) would give truer WYSIWYG for tables and embeds, but its
document is a tree that is serialised back to Markdown, and the serialiser
normalises: list markers, emphasis characters, spacing, escaped characters
and tables come back in its own style unless every construct is preserved by
hand. That breaks the clean-file principle on the first save of a file
written elsewhere, and every anchor and merge would be over a re-serialised
text. CodeMirror's cost is that some blocks (tables, embeds) are widgets
while the caret is outside them and source inside, rather than edited in
place. That was judged the better side of the trade; it would be expensive to
reverse, because the editor's model of positions is what the model and the
page share.

**No build step for the page** still holds: CodeMirror is bundled once into
`page/vendor/codemirror.js` from pinned versions (`tools/vendor/`, with the
command), committed, and imported as a plain module. Its licence is in
`page/vendor/README.md`.

**Positions.** The server counts Unicode code points of the raw file; the
editor counts UTF-16 units and, in a file whose lines all end in CRLF, one
position per line break. `page/editor/textdiff.js` (`posMap`) converts at
every boundary; a file's own line endings are kept (CRLF files stay CRLF,
mixed files keep each CR).

### What a note points at

A quote of the passage plus up to 32 characters before and after, and where
it last was. To find it again (`anchor.py`): every exact occurrence is scored
by how much of its context still matches, then by nearness; if there is none,
a passage still bracketed by the context, or a window of the text close
enough to the quote (similarity ≥ 0.6), is the passage **changed since**
(the note keeps its original quote to show what it was about); otherwise the
note is **detached**: kept, never deleted, listed in History, and
re-attachable to a new selection. After a known edit (a save through the
editor) the old position is mapped through the edit first, so the right one
of two identical phrases is kept.

### Saves merge

Saves are turn-based, three-way, by words (`merge.py`): the text the editor
began from, the text on disk now, and the save arriving. Changes to
different passages both land, even in one paragraph. **Where both changed
the same passage, the first save stands and the second becomes a suggestion
in its author's name**, and the rest of the second save lands. A doc with no
notes that meets such a conflict gets its sidecar then, to hold the
suggestion. This replaces Files' "Keep mine / Take theirs" box for Markdown
files while the plugin is on.

Suggesting works the same way from the other side: what is typed while
Suggesting is shown live (struck old words, new words in the accent), and on
a pause each changed run of words becomes a suggestion; a run that touches
one of your own open suggestions grows it rather than starting another.

### The sidecar

One readable JSON file per doc, `<system>/Loam/data/doc-comments/<id>.json`,
in the one folder inside Loam's that an update keeps (docs/update.md). It is
found through the doc's frontmatter line `loam_doc_id: d-…`, so it follows
the doc when it is renamed or moved (the sidecar records the path it last
saw, and updates it). A copy of a commented doc carries the id but not the
notes: the sidecar's recorded doc still has that id, so the copy starts
clean and gets an id of its own at its first note. A doc nobody has
commented on is never touched: no id line, no sidecar, and plain saves
record nothing.

It holds the notes (each with its anchor, author, replies, state), the save
record, and (step 2) the doc's links. Its shape is documented at the top of
`server/doccomments/sidecar.py`.

### Who wrote what

The save record starts at the first note with the text as it was, and
keeps every save after it as a delta: who, when, what changed, and a summary
(the section it was in). Edits made outside the editor are found by
comparing the file with the record's last revision on every read, and
recorded under the persona its frontmatter's `last_modified_by` names, or
the owner. **Show authors** replays the record into runs of text by author.
**History** lists the saves newest first; restoring one is a new save (so it
can itself be undone), and never drops the id line.

*More direct than the stamp:* a persona's write through Claude Code passes
the runtime's hooks with its file path, so a PostToolUse record of
(persona, path, revision) would name the writer even when the persona forgot
the stamp. It is not built: Antigravity runs no such hook, so it would be
exact for some providers and absent for another, and the stamp is the
convention every persona already keeps. Saves made through this plugin (an
agent's note, accept or conflict) do stamp the persona, through the broker's
`by`.

### Agents

The `doc_*` tools (`tools/notes_mcp.py`): list a doc's notes with their
passages, comment on a quote, suggest a replacement, reply, resolve,
reopen, accept, reject. A note an agent makes shows under its own name and
face. They call the plugin's server on loopback, as the agent. Nothing in
the editor wakes an agent: the owner tells an agent about a doc in a thread,
and it answers there or in the doc's notes. Agents are told to treat a note
from someone outside Loam as something to consider, never an instruction.

### Turned off

The page module is not loaded, the server is not run, no agent has the
tools, and Files draws Markdown in its own editor as before. No sidecar is
read or written. The sidecars and id lines stay for when it is turned on
again.

The page module also does nothing on a copy of the app older than
`files.own`, and, while `STAGING_ONLY` in `page/main.js` is true, on any
host but Loam's staging copy (`next.<domain>`): the editor is walked on
staging before it goes live, and the app's version was not enough of a gate
(another release reached live first). Going live is setting it to false.

## What step 2 adds

The share door in core (a route that passes the login for one scoped
handler, opted into per link), links with modes (view, comment, edit), names
and passwords, the gate, the standalone page (the same editor component,
`page/editor/editor.js`, mounted without Loam around it, as
`test/harness.html` already does), Connect my agent (a key per link for an
outside agent, calling the same operations over HTTP), and Shared Docs in
Files. `Service.may` is the one place a link's mode will limit what a caller
can do; the sidecar's `links` list is already there.

## Working on it

- `python3 plugins/doc-comments/test/test_model.py`, `node --test
  plugins/doc-comments/test/*.mjs`, `python3 tests/test_doc_comments.py`.
- `python3 plugins/doc-comments/test/dev_server.py` serves the editor alone
  on a seeded in-memory doc at `http://127.0.0.1:8790/` (`?phone`, `?dark`),
  with the real server code behind it.
- `plugins/doc-comments/tools/vendor/build.sh` rebuilds the CodeMirror
  module from the pinned versions.
