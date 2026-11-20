# Threads and boards

Chats is a conversation shared by personas, with a markdown board inside its card.
Threads are the sole chat view as of 2026-11-11. Earlier session conversations
are archived threads; legacy addresses resolve through the import mapping.
See [retirement and route inventory](classic-retirement.md). References below
to Classic describe the shared visual ancestry, not an available screen.

Each completed, repliable human or persona message has a quiet icon footer at
every width: **Reply**, **Branch into a new thread**, **Copy**, then the existing
read-aloud glyph when available for an agent message. The timestamp sits at
the left edge; actions group at the right edge with even 2 px gaps, in the
same muted tone.
The row spans the prose width, or the bubble width for a human message.
See [paired captures and alignment checks](evidence/thread-footer-time-left/README.md).
There is no top-edge
hover toolbar and no visible label on the footer buttons. Reply uses the app's
curved-arrow glyph; Copy copies the original Markdown and becomes a check for
one second after success, without a toast. The icons use the read-aloud glyph's
15 px size and 2.2 stroke, muted at rest and full ink on hover/keyboard focus,
with no background or border. Hit targets retain the shared 44 px minimum. Accessible
names stay Reply and Copy; hover-capable pointers get native tooltips.
On touch screens, swipe mostly horizontally right 56 px and release to reply.
A stationary hold, vertical movement or text selection keeps its native behavior;
a hold cancels swipe recognition after 480 ms. Long press and right-click use
the browser’s native selection and copy actions. Message bodies remain selectable.
A resisted slide and small snap mark the swipe threshold;
devices that support vibration give one short pulse. Vertical scrolling,
links, code scrolling and interactive controls retain their native gestures.
System lines, streaming drafts and unaccepted sends offer no reply action.

The reply chip shows the original author's face, name and first spoken line
(or attachment names), above the shared composer and below the recipient faces.
Choosing or cancelling a reply returns focus to the existing draft and keeps its
text and attachments. Reply intent follows that draft across thread visits in
this page; it clears after an accepted send. Failed sends keep it for review.
The POST sends `reply_to` beside `text` and optional attachment paths. Replying
does not change the default persona or add an implicit mention.

Human and persona messages carrying `reply_to` show the same slim quote,
with the quoted author's tone and a single line of text. Tapping it reuses the
thread-opening history anchor, loads/prepends older pages as needed, focuses the
original and highlights it for two seconds. The offset is 96 px below the
scroll area's top, clamped naturally at the beginning of history. Older quote
previews resolve through `before` pages without expanding the reading window or
marking an unseen tail read; the jump reuses those pages. Missing, deleted or
unresolved originals say **Earlier message**, without a link. An unavailable
lookup stays quiet until the next thread visit; it never loops on a failed read.
All reply state belongs to the page's draft or the broker's message record;
no browser storage, second composer or reply routing is introduced.
See [captures and verification](evidence/thread-replies/README.md).
Human reply bubbles share the ordinary bubble's width limit; long quotes
ellipsis inside it while the body wraps. Persona quotes and composer reply
chips retain the same single-line truncation. Long code scrolls within its
block, without widening the transcript. See the
[width regression and paired captures](evidence/thread-quote-width/README.md).
The earlier [footer and sheen evidence](evidence/thread-footer-sheen/README.md)
records why a top-edge hover action disappears while reading the end of a long
reply. The current [icon footer evidence](evidence/thread-message-icons/README.md)
records the replacement at all widths, including copying and its check state.
The [native-selection evidence](evidence/thread-native-selection/README.md)
records the removal of the custom message menu and held-touch/selection guards.

The composer is shared by threads and classic chats: rich markdown, dictation,
the terms-note lookup, attachments, the + menu, Files picking and the expanded
editor. Classic read-aloud, cost/context details and session management remain
available there.

The screen renders through the classic chat components in `public/chat-ui.js`:
list heading/grid/rows, responsive header and card, unboxed prose, user bubbles,
step folds, opening/empty states, floating dock and settings/model controls.
See [one product](one-product.md) and the [component inventory](design-system/shared-chat-components.md). `public/app.js` supplies the existing
composer and its lifecycle through a small adapter; no second composer is
maintained. `public/threads.js` owns the screen;
`public/thread-record.js` validates response bodies, reduces events and makes
byte-preserving board edits. `public/permission-cards.js` supplies the same
approval and question cards to threads and classic chats.

Pinned and Recent are matching headings above a continuous, unboxed list.
Each section is newest activity first, except that General (the thread whose
id is `general`, never one that only shares its title) always leads Pinned
while it is pinned (2026-11-18). Unpinned, it takes its place in Recent like
any other thread. `compareThreads` in `public/thread-record.js` is the one
comparator the page uses; `listing` in `lib/loam_threads.py` answers in the
same order.
The current desktop thread uses a rounded 1.5 px rule in 85% ink beneath its
text column, from the title's start to the row's right padding. One retained
indicator serves Pinned and Recent in their shared flow. It retracts toward
the old title's start, changes position only at zero width, then draws beneath
the newest target. The nominal 160 ms retract hands off its last 20 ms to the
260 ms draw: 400 ms for the stroke, with ease-in then ease-out. An interruption
retracts whatever is currently drawn before drawing at the latest target;
there is never a second rule. Titles ease from weight 500 to 650 over 420 ms
(`cubic-bezier(.22,.9,.24,1)`), using a thread-only registration
of the existing variable Figtree font. Hover uses a faint 1.5% ink tint.
A separate 2 px focus ring stays visible on selected and unselected rows.
Selection never borrows a persona's state colour. Phone returns, including
browser Back, keep the same rule for one second, then fade it over 200 ms.
Opening another thread or pausing the thread UI clears that timer.
Reduced motion removes the transitions. Icon state, unread, time, default
persona faces and two-line quiet titles retain their existing behavior.
Classic chats retain their pinned card and selection treatment. See
[stroke frames, title-collision checks and verification](evidence/thread-stroke/README.md).

Working and waiting thread-row subtitles carry a soft sheen through their
words, using muted text and ink from the current theme. A working pass takes
2.4 seconds; waiting takes 5 seconds. Asking, replied and quiet rows stay still;
asking takes precedence even when another member is working. Reduced motion
and forced colours retain ordinary, static text. No extra indicator is added.
See [motion frames and browser checks](evidence/thread-footer-sheen/README.md).

Read aloud belongs to the last nonempty prose segment of each completed
message, inside its footer, before any trailing tools or following messages.
The shared speech controls still read all that message's prose and apply the
same device thresholds. Consecutive replies each keep their own controls.

Consecutive messages by one agent on the same provider and model share an author run. Its existing 24 px
avatar/name line uses native `position: sticky` within that run: the browser
pushes it up at the end as the next author approaches. `thread-authors.js`
observes its original position only to fade a compact blurred pill behind the
line and a short vertical fade over the prose behind its row. The pill has
breathing room, a tint matching the reading surface and a faint persona-colour
edge. The prose fade dissolves below the marker, so words beside the label
cannot read as its continuation. No duplicate author, fixed overlay or
scroll-driven positioning is used.
The scroll area starts below the header and any docked player; the board and
its seen strip keep their own overlay. Human messages and Classic chats have
no sticky author. Reduced motion removes the backdrop transition.
See [captures and checks](evidence/thread-authors/README.md) and the
[floating-marker follow-up](evidence/thread-authors/floating-review.md).

The natural author line shows the reply’s provider as its existing monochrome
12 px mark. It shares its short model resolver with the composer pill and saved
member controls. Exact catalog names win; Codex/OpenAI catalog names retain
variants such as “GPT-6.1-Sol”. A concrete Claude record such as
`claude-fable-5-1` resolves through the catalog’s `fable` alias and uses the
recorded version: “Fable 5.1”. A newer alias default never renames an older
reply. Conventional Claude IDs can still be named without a catalog; an
unlisted `gpt-6.1-sol` falls back to “GPT-6.1”. Truly opaque IDs remain verbatim.
The full recorded ID stays in the focusable tooltip and accessible label,
alongside the resolved name. This is display only: saved IDs and run boundaries
never change, including variants whose compact names happen to match.

A missing model shows only the provider mark; an unknown provider uses the
existing letter fallback. These are the reply’s recorded values, independent
of the member’s current choices. At phone widths up to 480 px the mark stays
and model text clamps to 16 ch. Sticky markers keep the avatar, name and mark;
they drop the model at phone widths and retain it on desktop (up to 24 ch).
See [the integrated alias-catalog captures and checks](evidence/thread-model-labels/README.md)
and the earlier [chip and timestamp evidence](evidence/thread-reply-meta/README.md).

Each completed message's `at` appears at the right end of a quiet footer,
including when speech has no eligible take: short replies retain their timestamp.
Human timestamps sit outside their
bubble/clamp; attachments-only messages have one too. The reader's local
calendar determines the format: time today, weekday/time for the prior six
days, month/day/time beyond that (adding the year when different). The tooltip
contains the full date, time and time zone. Pending/streaming messages and
missing/invalid dates get no invented timestamp.

Sent image attachments use the authenticated Files download route with a
vault-relative path. A stored absolute path under `98_ATTACHMENTS/` uses that
subtree's suffix; the message's original path remains unchanged. Unknown
absolute paths remain filename chips. Gallery nodes and load/failure state
survive redraws: a failed image becomes a filename chip and is not retried by
stream or clock updates. [Evidence and request-count checks](evidence/message-media-boot/README.md).

The former bottom model label was the full record's `result` passing through
Classic's turn renderer. Threads now gathers those recorded facts into a
disclosure on the timestamp. Tapping it reveals the model, usage, context,
duration and costs that are available, one line per recorded result; no totals
are recomputed. A record with facts but no date uses a quiet Details control.
The shared app adapter supplies the same facts and speech controls. Classic
retains its existing model pill, cost/context disclosure and footer layout.
Footer metadata never enters the prose sent to read aloud.

The ready thread's composer dock shares layer 2 with the author row and paints
above it by coming later in the DOM. Its mention popover remains at layer 10
inside that context. Header/player (3), details head (4), board scrim (7) and
board (8) retain their layers. Threads now keep the composer available while
their first page arrives, with a quiet line in the message area instead of a
cover. Classic's shared dock stays at 1.
See the [composer overlap regression](evidence/thread-composer-stack/README.md).

## Availability and preview

The browser probes `GET /api/threads?archived=1` on opening Chats, then every
15 seconds while Chats is visible and its live list stream is unavailable. An unavailable runtime, including a 405 from one that
has not restarted with thread support yet, keeps the existing session list.
A successful `{threads, agents}` enables threads automatically without a page
reload. Thread failures remain on the thread list; there is no session-screen
fallback. Running now retains its own session listing/ending connection.

There is no refresh button. `GET /api/threads/events?archived=1` opens a list
stream: `threads.snapshot {threads,agents}`, then `thread.changed {thread,summary}`
and `thread.removed {thread}`. The relay routes this reserved path to SSE before
matching individual thread IDs. All summaries are read; Archived threads lives
in the Chats header's three-dot menu, with its count. Archived uses a leading
back arrow to Chats; Escape and the Chats tab also leave it. The first 50
matching archived rows render immediately, then another 50 as the list nears
its end. A Load more control provides the same action for keyboard users and
browsers without IntersectionObserver. Search checks all summaries before this
render limit, so an older match remains reachable. Query and view changes reset
the limit and scroll position. No scroll handler measures rows.

The broker currently sends all summaries in both the JSON listing and the
opening list snapshot. For larger archives it should offer cursor paging by
activity plus ID, an archive count, server search, and a compact row projection
without per-member background-task descriptions. A paged snapshot subscription
must avoid resending the whole archive and still deliver changes/removals for
loaded rows. Those are broker recommendations, not routes this page assumes.
The page change and 500-row measurements are recorded in
[the archive evidence](evidence/archived-threads/README.md).

The 15-second list refresh stops while the stream is healthy. Unsupported streams
back off for 15 seconds; disconnects use the shared connection watchdog and retry
while JSON polling provides a fallback. A fresh snapshot restores current rows.
Older JSON reads cannot overwrite streamed changes or the open record. The list
stream is the open record's metadata source while connected; rapid local writes
queue, and their HTTP answers hold until the ordered stream confirms them. This
prevents older streamed rows from undoing a default/model edit.

A `thread.removed` event or a thread-opening/stream 404 starts a removal check;
it does not stop the open conversation. The page confirms absence in two fresh,
valid `GET /api/threads?archived=1` listings, 2.5 seconds apart, before saying
**This thread was deleted.** Failed or malformed reads reset the count and
retry quietly. Any listing containing the thread, a successful opening read,
or a conversation snapshot/message/turn cancels the check. Archived threads
are included, and confirmation never uses the newest-page GET that marks
unread replies read. Pausing cancels checks and late responses are ignored.
Transient opening failures retry automatically; opening and stream interruptions
show only **Reconnecting…**, which clears when the connection recovers. A
missing `/steps` record affects that reply's details alone. See the
[removal regression and evidence](evidence/thread-removal/README.md).

For review without a runtime, use `npm start` and open `/?threads=fixture`, or
**Settings → Preview threads** when support is unavailable. The preview uses
synthetic personas and data in memory; no sample content reaches the broker.
Exit returns to the thread list. Reload resets the preview. Unsent messages,
quick-add text and raw board drafts survive navigation within this page only;
closing the page warns about unsaved raw board edits. The app has no database
or browser storage for thread records, boards or read marks.

## Branching from a message (2026-11-11)

Completed human and persona messages have a forking-path footer icon, named
**Branch into a new thread**, beside Reply and Copy. System lines, streaming
replies and optimistic sends have none. It uses the existing footer dimensions
and tone at every width. The shared `menuSurface` anchors **New thread with**
to that message's button from 700 px up, and opens a bottom sheet below it.
The source default is selected; source members precede the remaining roster.
One tap selects a different persona, including while the opening is loading.

The broker's branch extension supplies the context and writes the records;
this app supplies the text-model call. These paths are relative to
`/v1/threads` at the broker and `/api/threads` in the browser:

| Method and path | Request and response |
| --- | --- |
| `GET /:id/branch?message=m12[&persona=<id>]` | Broker: `{thread,title,message,record,window:[messages],persona,people:[persona ids],summarizer:{system_instruction,input,answer},fallback:{title,draft}}`. Browser: `{thread,message,persona,people,title,draft}` after drafting. This read creates no thread or agent turn. |
| `POST /:id/branch` | `{message,persona?,title?,draft?,by?}` → `201 {thread:<summary>,from:<system message>,into:<system message>,window}`. The sheet sends the chosen persona, generated title and edited opening. |
| `PATCH /:id` | Also accepts `draft:<string or null>`; the host stores this convenience beside the record. |

`src/thread-branch.js` sends the broker's `system_instruction` and `input` to
the existing Gemini Interactions text endpoint using `SPEECH_BRIEF_MODEL` and
`GEMINI_API_KEY`, with `store:false`. It reads JSON `{title,draft}` from model
output only. A missing key, rejected call, 20-second timeout, incomplete answer
or invalid JSON uses the broker's `fallback` without a second call. Prompts,
the context window and credentials do not reach the browser. No new settings,
application dependency or app-owned durable state are introduced.

While the read runs the sheet shows a soft four-line shimmer; reduced motion
keeps it still. The opening is a retained textarea, so live redraws keep its
selection. An empty opening disables Start. Read failures offer Try again;
write failures retain the persona and edited opening. Dismissing the sheet
retires late reads. A pending Start is guarded against repeated clicks.

Starting branches without sending a message or starting a turn. The returned
summary carries `draft` and `branched_from:{thread,message}`. The page opens
that thread with the edited opening in its shared composer, focused at the end
and scrolled there when needed. A fresh read restores `summary.draft` when no
page-local draft exists. Existing local edits, including a cleared field, take
precedence. The source text, attachments and reply intent stay with the source
thread. The broker consumes its stored opening on the first accepted message;
normal subsequent composer edits follow the existing page-local draft lifetime.

Both system messages render `link:{thread,message?,title}` as real anchors.
An ordinary tap opens the other thread; the source link also reveals and
highlights the chosen message using the existing history/jump machinery.
Modifier clicks retain native link handling. The source notice carries
`after:<source message id>`; `message.done` inserts it beneath that message and
its prior branch notices rather than appending it at the live tail. HTTP/SSE
duplicates update one identity. An off-window notice advances the reconnect
cursor but appears only when its source is loaded by ordinary history reads.

[Branch evidence](evidence/thread-branch/README.md) includes stubbed-route
checks, full-suite output and 390/1440 light/dark captures. This is web-half
work; deployment and the broker's own context/window behavior are separate.

## Runtime contract

The authority is the runtime's Threads interface, extended at `5da25bf` with parallel member turns and named background tasks,
following `8c8e3e3` with background/working readings, concrete reply models and Everyone mentions.
The management/upload contract was recorded at revision `7dcd478`.
The older `34b5668` recording remains as a backward-compatibility fixture.
The browser uses `/api/threads`; the existing authenticated relay maps it to
**`/v1/threads`**, just like `/api/sessions` maps to `/v1/sessions`. This
corrects the initial implementation's `/api/threads` upstream assumption.
`BROKER_URL` and `BROKER_TOKEN` remain the only connection settings. JSON is
`no-store`; SSE passes through without buffering; the browser never receives
the bearer token.

The following paths are relative to `/v1/threads` on the runtime and
`/api/threads` in the browser:

| Method and path | Request and response |
| --- | --- |
| `GET /` | `{threads, agents}`; summaries carry `id`, `title`, `default`, `members[{persona,engine,model,last_spoke_at,busy,running,background,background_tasks,working}]`, `background` (sum), `working` (members mid-turn), `last`, `unread`, `board_new`, `board` (path), `pinned`, `icon`, `archived`, `general`, `created` |
| `POST /` | `{persona?, text?, title?}` → 201 and a bare summary |
| `GET /:id?limit=50` | `{thread, messages, has_more, turn, turns, board:{path,markdown,revision}}`; messages carry `id`, `author`, `at`, `kind`, `text`, `did[{label}]`, and optional `engine`, `model`, `attachments`, prefix `segments` |
| `GET /:id?before=<message id>&limit=30` | Same envelope, earlier messages, with `has_more` controlling the next page |
| `GET /:id/steps?message=<message id>` | `{thread, steps:[entry]}`; one reply's full ordered record, or 404 when none exists |
| `GET /:id?steps=full` | Optional bulk reading, with `message.steps` arrays (null for messages without a full record); the page keeps its initial read light |
| `PATCH /:id` | `{title?, default?, add?, remove?, engine?:{persona:engine}, engines?:{persona:engine}, model?:{persona:model\|null}, icon?, pinned?, archived?, seen?:true}` → bare summary |
| `POST /:id/messages` | `{text, attachments?:[path], model?}` → `{accepted:true, message}`; the runtime parses mentions and schedules turns |
| `DELETE /:id` | Delete the thread after confirmation |
| `POST /:id/attachments` | Multipart `file` parts, forwarded unchanged → `{attachments:[{path,name,size,type}]}`; present since `7dcd478` |
| `GET /:id/board` | `{path, markdown, revision, board_new}` |
| `PUT /:id/board` | `{markdown, base_revision}` → `{path, markdown, revision, board_new}`, or HTTP 409 on a stale revision |
| `GET /events?archived=1` | List SSE: `threads.snapshot`, `thread.changed`, `thread.removed` |
| `GET /:id/events?after=<message id>[&steps=full]` | Live SSE; reconnect from the last completed message ID; the optional flag adds `message.steps` to completed replies |

The optional persona, nullable default, management, models, uploads and list
stream are implemented in runtime `7dcd478`. Summary members include `model`,
`last_spoke_at`, `busy` and `running`; rows include `icon`, `archived` and `general`.
Deployment must pair this UI with that broker revision or newer. Models remain
hidden on older records, unsupported list streams fall back to JSON, and other
runtime refusals remain visible. A PATCH ignored by an old runtime does not
pretend it saved. A thread upload starts no agent session.

### Full reply records (2026-11-10)

Each reply has its own durable message ID. Its full-record entry carries
`message`, `thread`, `author`, `engine`, `model`, `at`, `session_id`, `seq`,
`ended` and ordered `steps`. A recovered/backfilled entry can also carry
`recovered`, `backfilled` and `replies`; these do not change its rendering.
Steps include text/thinking with an optional subagent `parent`, tool calls
with `id`, `name`, `input`, untrimmed `output` and `is_error`, permission and
answer events, other harness events, and an `end` event with usage and cost.
Unknown event types are ignored. Unspoken entries have no message and are
not attached to another reply by the page.

The initial thread GET and message stream stay light. After paint, an
IntersectionObserver rooted in the transcript requests each visible reply's
`/steps?message=…` (with 120 px of lookahead), at most two requests at a time.
Opening a short-label fold can start the same request sooner. Reads are
deduplicated/cached only for the open thread and never mark a reply read.
Loading does not hold the initial prose. Closing the thread retires observers,
queued work and late redraws. A scroll-back reading anchor survives enrichment.
A 404/405 retains the readable `did` labels; other failures offer Try again.
There is no bulk full-record read, polling or browser record store.

`public/thread-steps.js` adapts the steps through Classic's transcript reducer.
Its text segments and step runs retain their original order. Subagent content
belongs to its parent tool; questions and resolved permissions use Classic's
answered/allowed views. `public/app.js` supplies the very same fold, tool,
plan/diff and turn renderers to Threads. Closed tool rows show a name and
summary; opened rows expose input/output. Long content is clamped at 240 px
with Show more/Show less, retaining all bytes, and interrupted calls finish
without a spinner. Shared tool rendering gives Classic the same input/output
controls, selectable text and readable subagent prose. Raw input remains
available on plan and diff cards too. Unknown cost stays unknown; a process
total is labelled total and is never treated as this turn's cost.

Full steps override live prefix segments and short labels, so a completed live
reply and a reopened one use the same view. The readable text remains the
fallback when an entry has no top-level prose. The short record remains the
fallback for older replies without entries. See
[paired captures and checks](evidence/thread-steps/README.md).

New creation opens a local empty conversation immediately, preserving text
while POST runs; sending is enabled after the returned ID arrives. A persona
uses `{persona}`; New thread sends `{}`. Failed creation retains the words and
offers an explicit retry. No message is auto-resent after a lost response.

There is exactly one live stream per open thread. A thread opens by attaching
the stream before its record read and buffering events across that read.
Completed messages deduplicate by their durable IDs. The reconnect cursor
advances only for completed messages, never from a draft, a snapshot or a
harness session's `seq`. A failure system line checks the host’s active turn
through a `before` history read (which does not mark read), so typing settles
without inferring failure from free text. Earlier pages prepend without advancing that cursor
or moving the person's reading position. They use the first displayed durable
message ID for `before`, and stop when `has_more` is false.

The thread read and opening `thread.snapshot` carry `turns[]`, every turn in
flight in start order: `{persona, engine, model, started_at, on_background?,
waiting?}`. Several members may be `running`/`busy` at once. The legacy `turn`
is the earliest; the page reads it only when `turns` is absent. An explicit
empty array means no turns. A snapshot states liveness, not replacement text:
continuing members keep their independent drafts and accumulated prose; ended
or replaced turns lose only their own draft. `turn.started` opens that member's
live reply before its first text, below the record.
**Durable messages stand in posting order; live replies follow them
(2026-11-16).** The owner, in t-153f9c43: "if the final message actually gets
posted after other messages, then it should be ordered that way." A message
posted while a member works -- sent here, from another device, or replayed
on a reconnect -- goes above that member's live reply, and a message being
sent is drawn there too. A finished reply settles after the last durable
message posted before it, which is where the thread read puts it, so the live
page and a reload agree. Words streamed before a message can post after it:
the broker posts a reply at its harness turn's end.
The composer stays available while other members answer; the runtime queues
additional messages per member and admits different members in parallel. `message.delta {persona,text,draft}` replaces that
persona's displayed draft with `draft`. `turn.step {persona,label,steps}`
keeps that draft as a segment, followed by a step fold. Later deltas
follow that group. Subagent (`↳`) and background-wait steps preserve the growing
main draft, so later replacement deltas do not duplicate the words already on
screen. Background waits never create a reply step or an empty reply slot.
Only an actually generating member gets a reply slot before prose. Its own
author chip and Classic's rotating words occupy that reserved position; prose
replaces the words in place. A parked member with no content gets no reply
slot, whether the record is streaming or already completed with tool labels.
Files and actionable permission cards remain content. Parked members occupy no
space in the message flow or above the composer. Background work is available
only through the count beside Board in the header.
Interleaved steps keep each live reply in place; completions settle in
posting order.
A completed reply stays in the record while another member streams. Repeated
or older replayed completions cannot erase another turn's draft. Cached
revisits deduplicate by ID when adopting the host's fresh tail in durable
completion order. `turn.step {waiting:true,label}` creates no reply step;
`turn.waiting {persona,text}` retains the host's seat-wait sentence for a
member that is generating. `turn.finished {persona}` clears live turn state,
including failed turns with only a system line. The older non-marking record
check remains only when the list stream is unavailable.

`message.done {message}` gives the draft its durable ID at its posting place. A separate,
page-local view key keeps the article, author run and prose mounted across
completion, repeated snapshots and the lazy steps read. The open tool fold
keeps its identity and disclosure state when full steps arrive. Newly streamed
prose stays formatted through completion rather than returning to a plain-text
opening placeholder. Its `segments`
are authoritative, including full text before tools; they replace partial local
segments and survive replay and repeated done events. The message's final `text`
follows those prefix segments once. Older `Said:` labels remain a fallback only
for records without durable segments.
A reply ending does not end its thread turn: a turn announced by `turn.started`
or the snapshot stays working through intermediate completed replies until
`turn.finished` or a later idle snapshot. Completed prose and the continuing
work indicator can therefore appear together.
Unknown event types are ignored. `member.joined {persona,by,engine?}` adds the
member; the host follows it with one durable system message per join. The page
groups recognized join notices attributable to mentions in the same preceding
visible human message into one sentence. Separate messages, unrecognized joins
and history windows without their originating message remain separate. This is
only a display view: durable messages, IDs and reconnect cursors are unchanged.
`thread.changed {summary}` updates the summary directly.

`members {members,background,working}` replaces the entire member array,
including removals and cleared counts. The opening snapshot's member array
does the same. Each member's integer `background` is outstanding work in this
thread; `working` is whether the member is mid-turn. A parked member can be
`busy:true, working:false, background:2`. It draws no waiting line, bordered pill
or empty reply. The header’s small play/count beside Board sums the members’
`background` integers, including work behind members who are generating too.
It has no continuous animation. The count fades over 240 ms when it reaches
zero; reduced motion removes that transition.

Tap the count to open the app's desktop popover or phone bottom sheet. Tasks
are grouped beneath their creator's small avatar/name. Each
`background_tasks[]` entry has a plain title
with the leading dispatch prefix (“Brief Loam: ”, “Brief loam-ui: ” or “↳”)
removed, and a muted elapsed time at the right. Rows key on creator plus task
ID. Times repaint locally each minute, never by polling the broker. Missing
times stay unknown. Tap a row for the full original description and start time.
When the task supplies `message`, details adds “Started in this reply”. It
closes the panel, loads older pages until that ID is reached, and uses the
thread's reading anchor to land on the reply with keyboard focus and a brief
highlight. Formatting retains that landing until the reader scrolls. An absent
or null `message` shows no link; a later `members` event can add it in place.
History failures are shown and the link can be tried again. A missing reply is
reported rather than silently landing on another message.
The list stays live while open. A removed selected task returns to the list.
At zero it says “Nothing running” for 1.4 seconds, then closes and returns
focus to the header (Board if the count has gone). New work cancels closing.
Long lists scroll within a viewport-sized panel; the background is inert,
Escape/outside tap dismiss, Tab stays inside, and the phone handle drags closed.

Contract clarification: `background_tasks` is an array, not a count. Use the
sum of the host's `background` readings, rather than array lengths, because a
task may leave that list before its notification lands. The list can therefore
briefly show “Waiting for results…” without a fabricated task row. Details
retains the member state/count. Thread rows express waiting in their subtitle
and icon layer; numeric task counts stay in the header control and Details.
A notification can become working without `turn.started`; that member reading
is enough to show its reply in place. No count is reconstructed from steps.
An explicit `working:true` outranks an older `on_background` turn hint when a
notification begins generating. Empty task-only records do not outrank the
latest spoken reply when resolving the composer's model version.

The list reads activity for its subtitle and icon layer without a separate
background number or working dot beside the time. The members and list streams
deliver these changes without polling. Absent fields mean an older runtime;
no background count is invented.
See [parallel member captures and checks](evidence/thread-parallel/README.md).
See [header task evidence](evidence/thread-tasks/README.md) for the current
presentation, lifecycle checks and captures. The earlier
[waiting-state redraw](evidence/thread-waiting-redraw/README.md) is historical.

### Engineer jobs in Tasks (2026-11-13)

The header count and Tasks panel also include this thread's engineer jobs.
Only `GET /api/threads/:id` carries `thread.jobs` and `thread.jobs_error`.
The opening read seeds them; while the thread is open, the page's five-second
heartbeat reads `?limit=1&seen=0`. The relay preserves `seen=0`, so checking
jobs cannot mark replies read. That reading updates jobs alone; it never
replaces messages, member state, the board, or the reading position. List
snapshots and member events carry no jobs and cannot clear the last reading.
There is no new preference or setting; the host's `thread.jobs` setting
already exposes the same information to personas.

Each job carries `job`, `project`, `title`, `briefed_by`, `thread`, `state`,
`phase`, `since`, `started`, `finished`, `elapsed_seconds`, `outcome`,
`outcome_detail`, `ok`, `receipt`, `position` and `held_by`. The page preserves
the host's order: active jobs first, then recent completions. Running, queued
and landing jobs each add one to the header's member background count, even
when the persona session has ended. Jobs have their creator's face, plain
title, quiet project line, status and elapsed time from `started`. Queued
jobs show `held_by`; landing explains whether changes are being checked or
made live. Member task details and their launching-reply links still work.

Finished rows remain for ten minutes after `finished`, with frozen duration
from `elapsed_seconds` (or the start/end timestamps). They leave the active
count immediately. When only completions remain, the header keeps its Tasks
control without a numeric badge. A row with a `receipt` opens that vault
reference through the existing Files resolver; rows without one remain plain
text. The ten-minute limit is a display window, not a deletion of host history.

| Host outcome | Page words |
| --- | --- |
| `deployed` | Landed and live |
| `landed` | Landed |
| `not landed`, `failed` | Didn’t land |
| `deploy failed` | Landed, not live |
| `cut off` | Stopped before finishing |
| `cancelled` | Cancelled |
| `interrupted` | Interrupted |
| `pull request` | Ready for review |
| `finished` | Done |

An older host without the keys adds nothing. A non-null `jobs_error` hides
jobs while leaving the member panel operational. A transport failure keeps
the last successful reading and retries on the next heartbeat. Hidden pages
do not poll. Elapsed labels and the completion window use the page's clock;
no new stream, job status route, or client-side job state is invented.
See [checks and eight visual combinations](evidence/thread-jobs/README.md).

`board.changed {board_new}` refreshes **only** `GET /:id/board`. It must not
re-fetch the thread: a GET without `before` marks it read. Reconnects likewise
use only SSE, preserving unread replies while browsing older messages.
Opening reads the newest page; reaching the live end sends `PATCH {seen:true}`.
The runtime owns `unread` and `board_new`; no local read mark is persisted.

## Opening a thread

The first read asks for `?limit=12`. All twelve messages paint as readable text
in order, with the composer available to type into. Send waits for the reading
to succeed. Formatting happens after paint, one visible prose block per task,
newest eligible block first. Formatted DOM nodes are kept across redraws and
visits, including their copy/link handlers. Full reply steps still use their
own visibility observer and two-request limit. Neither markdown nor full steps
hold up the initial messages after a long reply.

After that first paint, earlier pages of 30 fill in any missing context back
to the last human message, or the unread boundary when it is earlier. History
outside that opening context waits for scrolling up or the keyboard-accessible
Earlier messages control. Loading earlier… occupies the same quiet top slot
during a read, and the slot disappears at the beginning. Errors leave a retry
control there. The anchor is the text near a point one third down the scroll
area, with its message ID, stable view key and viewport offset. A short text
fragment and its occurrence preserve the actual reading point even within a
long reply containing repeated paragraphs. Message offset is the fallback when
no text can be located. Draws compensate for formatting, steps and history;
a resize observer compensates for image loads that happen without a draw.
Following the bottom and first-unread landing retain their existing rules.

The page saves the listing's unread count **before** its opening GET. On the
current interface it treats that many trailing agent replies as the unread
window, pages back if necessary, and places the new-messages strip with some
previous context visible above it. A newer answer can supply
`first_unseen_message_id` at the response's top level to override that fallback.
The strip stays anchored through enrichment until the reader scrolls. An
already-read conversation opens at its end. Returning to a previously open
thread in this page session restores its message anchor instead; loaded light
pages and formatted prose stay in memory until reload. Full step reads remain
per-open. There is no browser-persisted read mark, pin, or transcript.

Only after reading the tail does the live stream attach, with `after` equal to
the newest message ID **in that response**. Completions between the read and
the attachment are replayed and deduplicated by the reducer. Older pages never
change this cursor. A revisit paints its saved reading immediately, refreshes
the tail, and fills a gap between saved history and the new tail if needed.

`/threads/<id>` is an explicit conversation address. Cold loads draw its shell
and composer immediately, share the startup listing read, then use precisely
the same opening path as a row tap. Reload, Back and Forward work, including
Back from Files with scroll restoration. Unsaved file edits retain their
guard. `/` continues to open the list; no remembered conversation is reopened
on an ordinary launch. Sample preview threads do not write real addresses.

### Runtime additions needed for an exact opening

The current contract has two limits: the newest-page GET marks the **whole**
thread read, and the summary states a count rather than the read boundary or
the last human message. Consequently the count-based landing is a fallback,
not an authoritative first-unseen ID, and the latest exchange may require
background pages before all of it is available. The page cannot preserve the
host's unread mark while somebody is still reading the opening window.

A runtime follow-up should provide:

- A non-marking newest/context read, for example an explicitly documented
  `mark_seen=0`. Unknown query parameters must not be assumed to have this
  effect. No such parameter is sent by this page today.
- `first_unseen_message_id` and the pre-read unread count with that answer,
  captured atomically before any mark changes. The page already consumes the
  optional ID and never derives it from message numbering.
- A context window from the earlier of the last human message and the first
  unseen message, with one message of previous context, through the current
  end. Return ordinary `messages`, `has_more`, `turn`, permissions and board,
  plus that boundary. This removes the backward reads for opening context.
- A read around a named message for restoration after a reload, and a seen
  operation through a specific message ID rather than the whole current end.

These are requests, not claims about routes available now. No new broker
route, parameter or write behavior is required by this page change. The
existing board's new-items strip remains independent of conversation unread.
See [opening evidence](evidence/thread-opening/README.md), which distinguishes
synthetic timing and layout proof from the unavailable live timing check.

Permissions and questions use the existing `broker.permission_request` and
`broker.permission_resolved` events, with `persona` and `session_id`. The
classic transcript normalizer validates them, and the shared cards retain
single/multiple choice, Other, optional and secret answers, approval details,
session-only permission suggestions, and refusal handling. Answers go through
`POST /api/sessions/:session_id/permissions/:request_id` to the existing
`/v1/sessions/...` route. Resolution from another device removes the card.
The current runtime's opening snapshot includes `pending`, containing the same
permission events. The reducer restores those cards on a fresh page and drops
cards no longer pending on reconnect. Older snapshots without `pending` keep
requests observed while connected until resolved.
Cards attach to a reply if a `message_id` is supplied; otherwise they appear
at the active end. Closing a thread discards secret and other answer drafts.

Engine choices and labels come from `/v1/providers`, shared with classic chats:
currently `claude-code`, `codex` and `antigravity`. Signed-out choices are
visible and disabled. A saved engine absent from the catalog retains its id
as its label until the catalog returns.
The thread composer's @ button inserts a literal @ at the caret, with a space
before it when needed, and opens the same picker as typing @. While open, the
button says “Remove mention”; activating it removes the active @ and partial
query. Deleting the @ closes the picker too. Escape, a pointer press outside
the composer, or leaving the draft for anything except the picker or @ button
closes it while keeping the text. A chosen agent replaces the active query and
returns focus to the draft. Thread hints show only the send chord on desktop;
phones omit the hint row. Classic hints keep their existing reassurance.
On both layouts the thread icon or actions menu opens details. The header has no persona stack or default-persona subtitle.
The title itself renames inline. Engine/model choices use the shared model pill
and keyboard-navigable settings rows in one shared sheet rather than native selects.
General has no rename, unpin, archive or delete action. Other thread headings
are tap-to-rename: Enter or blur saves, Escape cancels. No duplicate heading/field
or Save button remains. They retain the icon picker and pin/archive/delete;
header deletion confirms in a small popover without navigating into details,
and a 409 says who must finish first. Details has its own inline confirmation. The
page scrolls independently below an opaque header, and its nav scrolls that
content area alone. The repeated nav title is omitted; add-member chips have
a space after +. Its final actions remain reachable on a phone.
Avatar chips select the default, including Nobody. Only Nobody shows the line
“Nobody replies unless you @mention someone.” A thread with exactly one member
who is also its default uses that persona's
own avatar in the list, header, opening/empty view and details. A chosen icon
always wins; other threads use their chosen icon or the group glyph. General always keeps the group
glyph and has no icon picker. Other threads can choose six existing app glyphs
in Details; the picker omits emoji whose small system-font shapes vary by
platform. `PATCH {icon}` persists the selection:
`null` is automatic (solo default persona or Group), `glyph:chat`, `glyph:folder`, `glyph:list`, `glyph:code` and
`glyph:pencil` name app glyphs, and an emoji remains its literal string.
Existing emoji selections continue to render. Unsupported writes keep the
runtime's failure visible; no browser settings or broker change is needed.
Desktop selection uses an ink rule and stronger title weight, independent of
members. Unread is a small solid dot centred below the time in the trailing column,
aligned with the secondary text line. The empty notification slot preserves
row height and keeps times on the same right edge and baseline in read and
unread rows, including pinned and archived lists. Group rows have no trailing
default persona face; the default remains in the row's accessible name. Solo
faces and group icons keep their existing identity and state layer.

Row subtitles use `threadSubtitle()` and names from the roster. Pending
questions/permissions (`asking`, member pending requests, the open thread's
pending map, or the live session's normalized asking state) take priority.
Then `working:true` means working, `background > 0` means waiting,
and the older `busy`/`running` alias means working only without an explicit
`working:false`. Generating and parked groups combine as “Sprout working · Quill
waiting”; a group of three or more shortens to its first name and +N. With no
activity, an unread latest agent message says “Sprout replied”; everything else
says “Quiet”. Text stays on one line with ellipsis; the full state is also the
row's accessible description. Time stays in its own column. The thread header
has no subtitle.

Thread list rows, including General, have no latest-message bubble, beating
time dot or background-task number. The subtitle says the state, the icon
shows it at a glance, the time dates the activity, and one solid unread dot
marks unread replies. The header task count remains the task-list entry point.
Classic rows retain their existing previews, working dots and counts.
See [before/after captures and checks](evidence/thread-row-signals/README.md).

The thread's main icon adds one quiet layer, shared by rows, the header,
Details and the empty view. Asking takes precedence: a steady soft glow in
the app's warm attention amber (`--quota-warn`). Otherwise a generating member
gives it a 3-second breath (2.5% scale, 3.5% luminance), in that member's tone,
or neutral for several. A member only waiting on background tasks adds no icon
layer, the same as quiet: the subtitle names who is waiting and the header's
count opens the task list. The unread dot stays separate and stronger;
selection keeps its separate ink rule. Both glow layers inherit the
persona avatar's organic corners or the group/chosen tile's rounded corners;
neither draws a circle around a different shape. Reduced motion keeps the
working tone static and the asking glow steady.
[Before/after captures and checks](evidence/thread-icon-no-ring/README.md)
cover persona and group rows in all four states at both widths/themes, plus
header/Details consistency and preserved motion. The earlier background ring,
its page-local progress batches, arc arithmetic and easing are removed.

The default is named in the composer placeholder (“Message …”). While a draft
has text or dictation is active, small persona faces appear immediately above
its top edge: the default, explicit mentions instead, or all thread members
(and a missing default) for `@all`/`@everyone`. Explicit mentions lead and IDs
are deduplicated. Four faces fit before a +N overflow. This is a preview only;
the runtime still parses the one sent message and schedules the turns.
Faces have no inactivity or focus timeout. While a nonempty draft temporarily
resolves nobody (for example, editing `@all` to `@al` without a default), the
last resolved face set stays. A newly resolved set replaces it. Emptying the
draft without dictation clears that memory; sending fades the faces, a failed
send restores them, and leaving the thread discards the face state. The held
faces do not add mentions or alter message routing. See the
[redraw reproduction and before/after checks](evidence/thread-draft-lifetime/README.md).
Faces rise softly on arrival and fade down when recipients disappear. They
carry no “listening” label in any state. Keystrokes trigger a faint 650 ms ripple
in each face's own tone and shape, at most once every 280 ms. Dictation uses the
existing microphone meter (no second capture); silent samples do not pulse and
louder samples increase the ripple's opacity, capped at 16%. Faces ease at most
3 px horizontally toward the actual DOM caret. Sending lifts and fades them over
240 ms as the existing reply/working UI takes over. Failed sends retain the draft
and restore its recipients. The
absolute layer and retained keys keep the composer fixed. While any face is
visible (including departure), a 44 px opaque shelf protects it from scrolling
prose and timestamps. A matching temporary transcript inset rises over 280 ms:
the reader at the end follows that rise, and a scrolled-up reading position
stays anchored. When faces leave while reading above the end, the invisible
extra inset remains until the reader returns to the end; reclaiming it sooner
could clamp the scroll position. The shelf is outside the composer, so its
field never shifts. Reduced motion uses a simple fade with no ripple or lean,
and the faces remain still once visible, with no label. Names
appear on hover, keyboard focus or a held touch. Faces show their own blob
shapes with a soft shadow and no surrounding circle or ring. Persona mentions in drafts and sent prose carry a tiny matching avatar,
drawn with CSS so copy, markdown, caret offsets and the sent text are unchanged.
Code and links retain their own rendering.

The member's explicit `working` boolean owns its turn dot when supplied.
On older readings, the live sessions view marks a member as Working only for `working` or `asking`
(normalized to `waiting` by the page). `idle` and `background` have no turn dot;
their resident sessions still offer End session. When no mapped live row is
available, the thread's active turn/permissions and summary `busy` (older alias
`running`) describe the turn. Thread list activity dots use the same function;
Classic residency dots keep their original meaning.
Original ID-based colours are restored, independent of roster order, additions
or display names. `AVATAR_COLOURS` can override one persona with an existing
palette token. Its matching tint follows that token; shapes stay unchanged.
Configuration is served as `avatar_colours` on app health only when nonempty;
installation-specific identities never become code literals.

A no-default empty conversation uses its own group icon or saved thread icon in the
soft thread tile. It never renders an unknown-person avatar. Details give the
Nobody help line 20 px of space after the chips and stack the engine and model
controls as aligned model pills with responsive wrapping.

For a new, unnamed thread, the first accepted message can supply its title.
The browser strips leading @mentions and trims the remaining text, then saves
that title through the existing PATCH route. Mentions inside the text and the
sent message itself remain unchanged. A chosen title is preserved. Mention-only
opening messages keep “New thread”. This does not rename existing threads.

Threads and classic chats use the same field and `keys.js`: plain Enter inserts
a newline, including on a phone; the send button or Ctrl/Cmd+Enter sends.

The @ picker starts with **Everyone**, a group glyph and "everyone in this
thread". It inserts `@all ` and filters by Everyone/all/everyone, alongside
matching personas. Typed `@all` and `@everyone` use the same mention accent as
persona tags, both in the field and the sent message; code and links retain
their existing rendering. The runtime schedules one turn per thread member,
plus a missing default, with explicitly tagged agents first and no duplicates.
The page sends the text once and draws the runtime's usual turn/join events.
Persona messages containing these tags are plain text to the runtime and do
not schedule a fanout. Each recipient uses their saved per-member provider and model.

**Providers & models** is the composer's thread-level configuration sheet. The
composer chip always shows the default member’s provider mark and saved model,
independent of mentions. Without a default it says “Providers & models”. That
shortcut opens the member sheet. In Thread details, provider/model pills open
their options directly at the chip, without repeating the members in a second
sheet. Both entry points use the shared anchored listbox on phone and desktop:
it flips above when needed, scrolls long lists, and supports keyboard selection
and dismissal. Every member has an avatar, name and two current values. The
composer sheet keeps its heading/footer visible around a shrinking scroll body.
New members can be added there before sending a mention. See the
[viewport audit and captures](evidence/thread-details-options/README.md).
For a saved Claude alias, the composer
shows its provider catalog entry's `resolves_to`, using the same compact name
as reply chips: “Fable 5.2”, with “Agent's choice: Fable (now Fable 5.2)” as
the tooltip. Only when `resolves_to` is absent does the page fall back to that
member's latest loaded reply on the same provider and in the same family,
then to the catalog name. Explicit `resolves_to: null` is the host's unknown
target and shows the catalog name. Historical reply chips always retain their
own recorded model. The saved alias and
the settings choices stay unchanged. Per-message model overrides are removed;
message POSTs contain no `model` from this UI.

These additions follow the runtime contract at `345d946`: Claude model entries
carry `resolves_to` (including null), while task entries carry `message` (null
until the launching reply exists). The existing relay passes both through.
See [current-alias and task-link evidence](evidence/thread-alias-tasks/README.md).

Changes save immediately through `PATCH engine:{persona:engine}` or
`model:{persona:model|null}`. A provider change lets the runtime reset its model;
selecting the same provider preserves it. Null is visibly “Agent’s default”,
including after reload. Errors and ignored writes remain visible rather than
claiming a save. Signed-out providers stay visible with their supplied reason.
Unknown saved IDs remain visible; labels come from the provider catalog. Older
records without `model` show the default and explain disabled model editing.

**API gap:** the documented runtime accepts an explicit engine ID, but defines
no nullable/inherited provider setting or resolved agent-default provider field.
This page therefore offers explicit providers and nullable models. Supporting
“Agent’s default” for the provider needs a runtime contract for
`PATCH engine:{persona:null}` and a summary field distinguishing inherited from
resolved provider. No new route or composer-level field is needed for the
implemented saved-member behavior.

The sheet is a sibling of the chat pane in the shell, outside the dock’s layout
and stacking context. Desktop positioning anchors to the entry control, clamps
to the viewport and allows the member body to scroll. Phones use a viewport
bottom sheet with a fixed header/footer and scrollable body; the existing shared
drag/cancel behavior applies. Background content is inert. Tab stays in the
sheet; arrows/Home/End navigate options; Escape first closes choices, then the
sheet; close and drag return focus to the opening control. See
[paired captures and reproducible checks](evidence/thread-models/README.md).

`public/auth.js` latches the first 401 or portal redirect. API calls then stop
before reaching the network; stream controllers, retry timers, the page/login
heartbeats and speech timers stop. One Sign in again link navigates to the
current protected URL, letting the portal preserve the return destination.

## Boards

The header’s **Board** button opens the thread’s Markdown file. Cards is the
default. A phone and a desktop each remember their view through the appearance
settings route (`appearance.phone.board_view` and `appearance.desktop.board_view`).

**Cards.** Tap the words to edit a card. Enter or leaving the field saves it.
Links, emphasis and wiki-links render in the card. Dates, authors and deadlines
sit below the words. Each list starts with an add control that names the
section, such as **Add to Urgent…**. Hold a card before dragging on touch,
or drag with a mouse. The item menu can move a card to another list, change
or clear its expiry, or delete it. List headings collapse. A heading followed
by deeper headings reads as a group title when it has no cards of its own.
**About this section** opens the section’s notes, which start collapsed.
The board’s properties stay in the file.

**Doc.** The installed Files Markdown editor opens the same board as a continuous
document. When the rich Files viewer is unavailable, Doc uses the built-in Files
field. Both receive a view of the document with board metadata removed. The board
adapter restores that metadata and each original line ending on save. Board saves
use the thread route for both editors.

### Agent access to the same file

Agents can edit the Markdown directly. For HTTP edits, open the thread or
GET `/v1/threads/:id/board`. Both readings return newline-preserving
`markdown` and a `revision` derived from the exact UTF-8 bytes. PUT the
edited `markdown` and that `base_revision` to the board route. Success returns
the saved bytes as Markdown and their new revision. HTTP 409 means another
writer changed the file. Read it again and apply the intended change to that
reading. The settings tool’s `thread.board` value carries both fields.

The page retains a refused edit in the tab across reloads. It displays the
newer file and offers to reapply the edit. When the edited passage itself
changed, it can keep both versions for review. A broker that predates the
revision field can still display the board until it restarts.

## Verification and deployment

[One-product evidence](evidence/one-product/README.md) contains current paired
thread/classic captures, shared-component checks, functional regressions and
the real two-agent reply/steps/deletion check.

`npm test` covers board byte preservation, UTC/General dates, actual draft and
step semantics, completed-message replay, classic permission normalization and
every allowlisted relay method, bearer authentication, errors and SSE. It
also parses recorded responses from the **baseline runtime routes**, generated
in isolated temporary workspaces with the real route, record and engine code;
no production broker, credentials or agent processes are involved. The
recordings are [runtime-34b5668.json](../test/fixtures/threads/runtime-34b5668.json).
To regenerate with a checkout of that runtime revision available:

```sh
python3 tools/record-thread-contract.py <runtime-repository> test/fixtures/threads/runtime-34b5668.json
```

The real Chromium driver uses a synthetic runtime through the real server,
plus the explicit client preview. It verifies fallback and automatic recovery,
`after` reconnection, live steps, scroll-back without marking unread replies,
board-only reads, shared cards, failed sends/saves, and history position.
Install Playwright outside the repo, then run:

```sh
THREADS_EVIDENCE="$TMPDIR/thread-captures" \
LOAM_PLAYWRIGHT=<playwright-module> LOAM_CHROME=<chromium-executable> \
node test/threads-browser.mjs
```

[Polished screenshots and checks](evidence/threads-runtime-polish/README.md)
include the previous states at 390/1440 in light/dark and a board before/after.
These are isolated fixture captures, not live deployment evidence. The newer
[thread polish checks and 390/1440 captures](evidence/thread-polish/README.md)
cover the shared composer, details, optimistic creation and auth expiry.
Deployment is handled by the platform owner for this job. Pair this code with
runtime `7dcd478` or newer, and apply any explicitly requested colour exception
through `AVATAR_COLOURS`. See [round-two evidence](evidence/thread-round-two/README.md)
for the latest browser results and the running-runtime probe. This job performs
no platform rollout or broker restart.

## Agent card and shared phone sheets (2026-11-12)

A Chats agent face opens a small card with its shared 56px face, display name,
quiet context, New thread and Profile. Desktop anchors the card to the face;
phone uses the shared bottom sheet. Purpose text reads `description`, then a
string `role`, if the roster supplies one. Current rosters supply neither, so
an actual working-thread link or the latest known reply time supplies context;
unknown reply time stays unknown. The broker should add a short `description`
to both agent and thread rosters. Working threads can be opened from the card,
including archived ones; status uses the same classifier as the Chats rows.

Every `menuSurface()` receives its phone handle and gesture binding from the
shared shell. `sheet-gesture.js` reads native touches and paints the sheet and
top-layer backdrop; `sheetdrag.js` decides scroll/drag and release. A downward
pull from content only takes a sheet when that content begins at its top.
Handles/headers drag independently of scroll. Distance or a flick dismisses;
a short pull/cancellation returns. A held pull is no longer a flick. Range
inputs remain interactive. Opening uses the theme's motion token, and finished
animations relinquish their transform; reduced motion fades without sliding.
See the [card matrix, complete sheet audit and reproduction commands](evidence/agent-card-sheets/README.md).

The agent card uses the shared sheet/quiet-link primitives: a secondary-ink
44px working link with a small leading sheen mark, hover/focus underline,
Settings' 14px spacing, 28px plus safe-area bottom padding on phone, and a
name/context block centred against its face. Every visible control is covered
by the [computed-style and spacing guard](evidence/agent-card-spacing/README.md).

## Painting ordinary updates (2026-11-14)

Sends, read marks and thread actions refresh the plugin catalog. Stylesheet
ordering must leave already ordered links attached: moving a loaded link can
remove its CSS while the browser fetches it again. The dictation control then
briefly becomes a native gray rectangle. Personal CSS still loads its replacement
beside the current sheet and swaps only on success.

The thread header menu, delete confirmation and conversation have stable keys.
An unkeyed transient menu before the conversation caused closing that menu to
replace the conversation and lose its native reading/selection state. The shared
menu shell and provider/model choices retain their existing behavior.

[Frame instrumentation and before/after evidence](evidence/thread-update-paint/README.md)
cover send, incoming/status events, catalog refresh, pin and board edits at
390/1440, light/dark. Initial loading, read errors and retry remain visible.

## Composer edge and audio scrolling (2026-11-14)

The composer uses one decorative backdrop with a transparent gradient and a
matching alpha mask on its blur. Its reach above the dock is 20px with no
recipient faces and 68px with faces (the existing 44px recipient inset plus
24px). One and several recipients use the same height. The field keeps its
size and position; the existing inset still protects the last message and
preserves reading position. The mask belongs only to the decoration, so it
cannot clip the mention menu, recipient faces or their hit targets. The
decoration passes touches through. The dock itself has no second blur.

The additional reproduction includes the actual read-aloud plugin/player,
scrolling across its row's clipping boundary, send while its bar is present,
and an image response held through 210 animation frames. It retains the
stylesheet and conversation fixes above. No additional audio runtime change
was justified by the browser measurements.

[Evidence and reproduction](evidence/composer-edge/README.md) distinguish
Chromium's native playback and painted blur from headless WebKit's ready-player
layout coverage. This WebKit environment does not paint even a standalone
reference backdrop blur or play the native take. Physical Safari, its GPU
compositor, keyboard and safe-area behavior remain staging acceptance checks.
