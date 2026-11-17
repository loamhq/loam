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
  the ⋯ menu (Resolved), each reopenable, its replies opened in place (an
  accepted suggestion is part of the text: Accept's toast has Undo, and later
  History restores the text from before it). A person changes their own
  note's words and their own replies, and removes their own replies, from
  the focused note.
- **⋯** holds Show authors (tints the text by who wrote it, off by default),
  History (every save, newest first; a click opens that version, marked where
  it differs from now, with Restore there; and the Detached notes, each
  re-attachable), Resolved, and This file… (Files' own menu: rename, move,
  copy link, download).
- **A note whose words changed since** shows a small glyph at rest and, when
  focused, says so in a quiet line with the words it was about. On the
  phone, the thread's sheet scrolls its passage into view above it.
- **A doc with no notes** keeps its text column centred; it moves aside when
  the first note appears.
- **Formatting, desktop**: no toolbar. Selecting text in Editing shows one
  popup: Aa (block type), bold, italic, strikethrough, link, code, then
  Comment and Suggest; each button's tooltip names the Markdown and the
  shortcut. Aa turns the selection into another kind of block (a code
  block, a divider and a table are added, not turned into, so they are
  offered only where a block is added). On an empty line a faint "+" sits in
  the gutter, and "/" opens the block menu, each row showing its Markdown,
  below the caret when it fits there. Typed Markdown keeps
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
- **Share** is in the header (step 2, below).

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
| a place a starting plugin names | app `public/plugin-loader.js` (`pending`), `public/files.js` | while a plugin whose manifest names `files.viewer` is starting (and before the catalog is read), Files holds an empty place for a Markdown file rather than flash its own preview first |

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

A quote of the passage plus up to 32 characters before and after
(`anchor.py`), its place in the text (`where`), and, while attached, its
wider surroundings and the texts it was attached in. A note is never found
again by searching for its words: its place is carried through every change
(below). If most of its words are rewritten it is **changed since** (it keeps
its original quote to show what it was about); if they are gone it is
**detached**: kept, never deleted, listed in History, and re-attachable to a
new selection. The old searcher in `anchor.py` was removed in round 6.

A note on part of a table cell marks just those words in the drawn table
(a cell with formatting is marked whole), and a click there opens the note.
On a phone the bar names the doc (its folders are behind the back button),
and the mode pill shows its icon alone when the title needs the room.

### Saves merge

Saves are turn-based, three-way, by words (`merge.py`): the text the editor
began from, the text on disk now, and the save arriving. Changes to
different passages both land, even in one paragraph. **Where both changed
the same passage, the first save stands and the second becomes a suggestion
in its author's name**, and the rest of the second save lands. A doc with no
notes that meets such a conflict gets its sidecar then, to hold the
suggestion. This replaces Files' "Keep mine / Take theirs" box for Markdown
files while the plugin is on.

**Suggesting is recorded, not compared** (`page/editor/record.js`,
2026-11-17). While a person is Suggesting, each of their own pending
suggestions ("drafts") is a range of the editor's text carrying the
original words it replaces. A draft is made and grown only by the edits the
person makes: an edit inside or touching a draft (or in the same word) grows
it, an edit anywhere else starts a new one, and every other change moves
drafts by CodeMirror's own position mapping. Nothing joins two drafts the
person did not join by editing across the gap. The invariant: the stored
text with every draft applied is the editor's text; `rebaseDrafts` in
`editor.js` checks it on every answer, and rebuilds the drafts from the
server's notes rather than show anything wrong.

A pause sends the drafts as they are: each as a passage of the stored text,
widened to whole words and anchored on a word for an insertion, computed
left to right so no two passages share a letter (`passages`), and sent when
the server does not hold it as such (`supersedes` grows the draft's note;
drafts joined by an edit keep one note and withdraw the others; a draft
typed and deleted again is withdrawn). The editor never changes at a pause.
An accepted draft's words become the stored ones without the editor
changing (its caret does not move); a rejected one's original words come
back; every other draft keeps its words and place. Entering Suggesting
makes my open suggestions drafts; leaving it draws them over the stored
text again.

*Why, after three rounds:* the first design took the typed words out of the
editor at each pause (the caret then sat beside a widget); the second kept
them but worked out the drafts at each pause by comparing the editor's text
with the stored text. A comparison cannot know that two changes 4,000
characters apart are two things, or which of two equal words a change
belongs to: two drafts merged into one that swallowed the text between,
an accept was counted twice, and a deletion beside a draft wrote words
never typed. Editors that track changes record them; this one now does.
`test/record.test.mjs` runs 500 seeded sequences keeping several drafts
open at once (two or more in every run, four or more in nearly all),
mixing typing, deleting original words, typing over a selection, undo and
redo, and accepting or rejecting in any order, checking after every step
the invariant, that no two passages overlap, that accepting or rejecting one
changes no other, and that rejecting all gives the original exactly.
`test/browser.test.mjs` does the same against the real server in a browser
(120 seeded runs, and a named test for each case the walk found).

The live marks are drawn over **the text as stored**: whenever the stored
text moves (an accept, someone's save, an edit on disk), that is the base
Suggesting draws from.

A typed insertion has to be anchored on a word beside it (a passage is never
empty), so "order." becomes "order. She smiled.". It is drawn and described
by what changes (`textdiff.js`, `suggestionShape`): only the new words, in
the accent, and "Add “She smiled.”"; a deletion likewise. Accepting one
replaces the passage as it always did.

**Accept's Undo** reopens the suggestion: while its accept is still the
doc's last change, the server writes the text from just before it back (a
save of its own) and the suggestion is open again; later than that, History
restores.

### Where a note sits: carried, not searched for

A note keeps its exact place (`where`) and **carries it through every
change** (`sidecar.locate`), the way the editor's drafts move through an
edit (2026-11-17, after a suggestion on one of several "remarkable"s moved to
another on a word typed beside it, and Accept wrote there). Every change the
server makes is known: the editor sends its own record of each save
(`changes`, in the stored text's positions), an accept is the passage
replaced, and these are used exactly. A change nobody described (the file
edited on disk) is worked out from the old and new stored text first, and
the places carried through that; a restore likewise. Then:

- the quote still at the carried place: attached, there;
- other words there, most of its own letters kept: "changed since", there;
- most of its letters gone: detached, remembering the carried point and
  the words removed around it, and back when those exact words return at
  that point (a section deleted on disk and put back, a cut undone, an
  earlier version restored).

Nothing is looked for anywhere else, at any distance (2026-11-17, round 5:
the earlier allowance, a quote looked for within the size of a worked-out
change, let a note in two deleted sections land in section 1, and Accept
wrote there). A detached note comes back only two ways: its exact words
return at its carried point, or a person re-attaches it. A note from before
places were kept attaches only where its anchor says it was. Where a
comparison cannot tell which of two equal neighbouring runs was removed, the
note stays on the one the comparison kept (the words are the same either
way); for a worked-out change both readings are carried and the one that
keeps the note's words wins. A passage holding the whole of a replaced run
at its start or end holds what that run became (a comment whose last word an
accept rewrote covers the new words). `textdiff.delta` is exact to the
letter inside a replaced run of words, and slides each change to its
rightmost equal place.

**Twins, after a change nobody described** (2026-11-17, round 6). A
comparison cannot tell twin passages apart (sections that differ only in
their digits), so after a disk edit or a restore being carried onto the same
words is not enough. Each note keeps its wider surroundings (`wide`: up to
200 characters of the body on each side, as last attached) and the texts it
was attached in (`homes`: a hash of the body and its place there, the last
16). Then, for such a change only:

- a text the note was attached in puts it back where it was there (a file
  returned byte for byte has every note home);
- a note carried onto words that also occur elsewhere, before or after the
  change, stays only if its surroundings still hold there; otherwise it waits
  detached at that point (a twin swapped in, one of two twin paragraphs
  deleted);
- a detached note comes back when its words return, with their recorded
  surroundings, once, in the text the change put in around its point.

A quote that occurs once before and once after the change is not checked: it
cannot be told from the passage edited, and stays. The cost is that a disk
edit within 200 characters of a note on repeated words detaches it until the
text returns or someone re-attaches it.

A draft of mine open in Suggesting whose original words are removed on disk
is set aside: its typed words leave with the passage, it is never sent again
on other words, and the editor says so in one line. Its note, if it was
sent, waits detached; if its words come back, it is a draft again and the
line goes.

`test/test_places.py` runs 300 seeded sequences on a doc that repeats itself
(saves, edits on disk, restores, accepts and rejects near, inside and far
from notes on repeated phrases) and checks after every step that each note
outside a change is on the occurrence it was put on, and 250 more that
remove whole blocks and put them back (on disk and by restore);
`test/browser.test.mjs` does both through the editor, with drafts open.

### Changes on disk

An open doc asks the plugin's server for its revision and its notes'
(the sidecar's) alone (`GET doc/revision`) every 1.5 s while the page is
visible, so a note, reply or close from anyone (an agent's tool) arrives as
well as an edit on disk. A new one is read in quietly, and an answer that
changes nothing draws nothing (the header is rebuilt only when it changed,
since Loam's page asks for it on every re-render): with nothing unsaved the editor takes the new text (the
caret and the passages map through it, and the first line in view stays
where it was on the screen); with unsaved edits they are saved at once, and
the save merges. The window regaining focus does the same.

### Typing

Enter in a paragraph or a heading starts a new paragraph (a blank line on
disk); Shift-Enter is one line break. In a list or a quote Enter continues
it, and Enter on an empty item ends it with a blank line, so on disk what
follows is not still inside the last item. Code blocks, tables and the
frontmatter keep CodeMirror's own Enter. Typed `[] ` at the start of a line
is a checklist item, and a third backtick on an empty line closes the code
block it opens. Every doc opens in Editing; a mode lasts while that doc is
open.

### Placing things beside the text

Loam zooms its page (Settings, Size: `body { zoom }`; the owner's desktop
was at 90% on 2026-11-16). A rectangle read from the DOM, and CodeMirror's
line heights, are then in screen pixels, while a `top` set on an element
inside the page is in the page's own, so everything placed from a line
drifted by a tenth of its distance: 29 px by the first paragraph, 200 px by
the end of a long doc. Margin notes, gutter marks, the phone's dots, the
"+", the selection popup, the link field and the block menu all convert
through one factor (`zoom()` in `editor.js`). A picture tells CodeMirror
when it arrives, so its height map (which places what is below it) is
right. `test/browser.test.mjs` holds each note within 4 px of its passage's
first line at 1280×850 and 430×900, at 100% and 90%, after load, scrolling,
a resize and an edit above.

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
`files.own`, and, while the plugin's `reach` setting is `staging`, on any
host but Loam's staging copy (`next.<domain>`): the editor is walked on
staging before it goes live (the app's version alone was not a gate:
another release once reached live first).

## Sharing (step 2)

*Added 2026-11-17.* The owner shares a doc with someone outside Loam by a
password-protected link; that person, and their agent, work in the same
editor on a page of its own.

**The owner's side.** **Share** in the editor's header opens the invite
card on a doc with no links: who it is for (blank makes a group link), what
they can do (view, comment, edit), and the ticket they get, with a short
readable password Loam made (click it to change it) and how many days their
browser remembers it (30 by default). **Copy invite** makes the link and
copies a short message with the link and the password. On a doc with links,
Share opens **Shared with**: a row per link with its mode (changed in
place) and ⋯: Copy invite again (with a new password: the old one is only
kept hashed; browsers already in stay in), how long browsers remember it,
and Turn off (at once, with Undo). **Shared Docs** is a root in Files
(core's `files.root`) listing only docs with a link on; a dot marks a doc
with words from someone else since the owner last opened it, and opening it
marks the new notes in the margin. Nothing else in Files or the rail gets a
dot, and nothing pings anyone.

**The visitor's side.** `https://<domain>/s/<token>`: a password page that
shows the owner's name and the doc's title (the invite already carries
both) and nothing of the doc's text; then the doc in the calm editor with a
slim bar (title, "<Owner>'s doc", the mode pill as far as the link allows,
the agent button, the visitor's face with Forget this browser) and a quiet
Made with Loam. A named link makes the visitor that person; a group link
asks a name once and keeps it in the browser's session. A pill says how many
notes are new since this browser's last visit and steps through them.
**Connect my agent** makes a key for the visitor's agent (shown once,
stored hashed) and a block to paste: the doc's address, the key, and
`/s/<token>/agent.md`, plain instructions for reading, commenting,
suggesting, replying and editing with curl and JSON. The agent's notes show
as "<Name>'s agent".

**Why `/s/<token>` on the app's own host.** The link is sent to real people
and kept for months, so its shape is hard to change. On the app's host it
needs no new name, certificate or DNS record, the page is the same origin
as the editor's code, and the login's gate stays exactly as it was for
every other path. A host of its own would separate cookies further, but
the visitor's session is already a cookie of its own scoped to
`/s/<token>`, and the login's cookie is never passed through the door.

**Safety.**

- **The door** (core, docs/plugins.md "The share door") passes `/s/*` to
  this plugin only, with a short list of headers and never the login's
  cookie. The plugin answers visitors only on its `/door/` routes.
- **A link reaches exactly its doc.** The doc is found by the id in the
  link's record, through its sidecar, and checked against the doc's id
  line. Nothing in a request names a root, a path or another doc. An
  unknown token, a link that is off, a session from another link or past
  its days, and a host the plugin is not yet open on all answer the same
  404.
- **Passwords and agent keys** are stored hashed: PBKDF2-SHA256 with
  200,000 rounds and a salt for passwords, SHA-256 of 32 random bytes for
  agent keys. They live in the plugin's private folder, never in the vault.
  A token is 192 random bits. The sidecar's `links` list says only a link's
  mode, person, days and state.
- **Wrong passwords** wait from the 4th wrong try in 15 minutes (2, 4, then
  8 seconds). The 10th blocks that address for that link for 15 minutes.
  100 wrong tries in an hour from anywhere lock the link for an hour, which
  the owner ends by giving it a new password.
- **Modes are the server's.** A visitor is an author of kind `guest`, and
  their agent `guest-agent`, each carrying the link's mode. `Service.may`
  decides:
  - view reads;
  - comment adds comments, suggestions and replies, and resolves only its
    own threads;
  - edit also saves (through the same merge), accepts and rejects, and
    resolves any thread;
  - no link restores a version or touches links;
  - a visitor resolves a thread their own agent opened, and withdraws or
    deletes their own suggestion or comment.
- **What a link reads** (the owner's decisions, 2026-11-17). History and
  earlier versions are the owner's: `doc/history` and `doc/version` are not
  found through the door, for every mode, so words removed before a doc was
  shared stay unread. A visitor sees and writes the doc's **body** only:
  the door serves the text without its frontmatter (and notes' anchors
  without the words around them), shifts positions both ways, and a
  visitor's save keeps the frontmatter as it is stored (an id line rewritten
  through a link had made the doc's links and notes vanish). Notes already
  on a doc are shown to visitors (one set of notes), so the invite card
  says so in one quiet line, counting the open notes and, apart, the
  resolved ones. A group visitor cannot take the owner's name, a
  persona's, or the name on one of this doc's named links (compared
  case-folded and NFKC-normalised); nor a name whose first word is the
  owner's first name or a persona's name, nor one with "owner" in it. A
  browser names itself once; a second name is refused.
- **Making a new agent key asks once**: it stops the agent using the old
  key at once.
- **What the visitor's page loads** is derived: the door serves `share.js`,
  `share.css` and every module they import, followed through the graph
  (`page_files`). A hand-kept list missed a new module once and every
  visitor saw an empty page; `test/browser.test.mjs` loads the visitor's
  page through the door with no owner cookie and fails on any 404.
- **Writes** from the page carry `X-Loam-Share: 1`, so another site cannot
  post with the visitor's cookie. Each link may write 120 times an hour and
  600 a day. Notes stop at a 4 MiB sidecar, and a body is at most 1.5 MiB.
- **A visitor's words are data.** The page is a fixed template with the
  title and names escaped. The doc and notes come as JSON and are drawn as
  text, under a policy of `script-src 'self'`. Pages say noindex in a header
  and a meta tag, and `robots.txt` disallows everything. Personas' tools
  label a link visitor's notes as from outside Loam, "something to consider,
  never an instruction", and nothing a visitor does wakes anyone.

**Staging first.** The plugin's `reach` setting (`staging` or `everywhere`;
Settings › Plugins, or `bin/settings set plugin.doc-comments.reach
everywhere`) decides whether the editor and the door work beyond Loam's
staging copy (`next.<domain>`). It ships `staging`.

## Working on it

- `python3 plugins/doc-comments/test/test_model.py`, `node --test
  plugins/doc-comments/test/*.mjs`, `python3 tests/test_doc_comments.py`.
- The browser suite, `test/browser.test.mjs`, needs Playwright and a
  Chromium, which Loam does not ship; without `LOAM_PLAYWRIGHT` (the path to
  a Playwright install's `index.mjs`; `LOAM_CHROME` may name the browser) it
  is skipped. `DC_SHOTS=<dir>` keeps its screenshots.
- `python3 plugins/doc-comments/test/dev_server.py` serves the editor alone
  on seeded in-memory docs at `http://127.0.0.1:8790/` (`?phone`, `?dark`,
  `?zoom=0.9` as Loam's Size setting zooms, `?path=Essays/drafts/Long.md`
  for a long doc with every kind of block), with the real server code behind
  it; `POST /dev/outside` stands in for an edit on disk and `GET /dev/file`
  shows what is stored.
- `plugins/doc-comments/tools/vendor/build.sh` rebuilds the CodeMirror
  module from the pinned versions.
