# Session UI history (retired 2026-11-11)

This is the README before the classic page was retired. Its session-screen
behavior, test inventory and commands are historical, not the current run
contract. Shared dictation, speech and file implementation notes remain useful;
use the root README and dedicated feature docs for current navigation. Classic
source and removed test drivers remain available in git history.

# loam-ui

A web application for talking to the coding agents that run on your own
machine. A browser page, installable on a phone, through which you start a
session with one of your agents, watch it work, answer it, and stop it — the
same sessions you have today at a terminal on that machine, reached from a
device that is not it.

`loam-ui` is a working name. The product has not been named.

Classic chats and thread members can run on **Claude Code, Codex or
Antigravity**, as offered by the runtime's `/v1/providers` catalog. New-chat
choices, provider filters and thread engine pickers read that catalog, including
its display names. **Settings → Providers** uses one sign-in card driven by
each row's `sign_in` descriptor: paste-code for Claude Code and Antigravity,
device-code for Codex. Sign in opens the provider's page in a new tab; paste
the returned code (or landing-page address) and press Done. Failed codes leave
a sentence and Try again; success shows the account when the host names it.
Sign out appears only when the descriptor offers it, currently Antigravity.
No setup instructions are printed on the card. See the
[shared-card contract and evidence](evidence/provider-sign-in/README.md).
Antigravity's context sheet offers no compaction, as with Codex.

**Chats now supports threads and boards:** shared conversations with persona
mentions, a changeable default agent, an engine per member, and a markdown
board beside each thread. General stays pinned. When the runtime's thread
API answers, Chats opens on threads automatically; until then the existing
session list remains the default. **Settings → Classic chats** keeps the
existing sessions available. For the new experience without a runtime, open
`/?threads=fixture` (clearly labelled sample data) or Settings → Preview
threads. The relay uses the runtime’s `/v1/threads` interface, with message-ID
reconnection, earlier-message paging and shared permission/question cards. See [threads, boards and the runtime handoff](threads-and-boards.md).

Threads use the **same composer as classic chats**: markdown while typing,
dictation, file/image upload, Files picking and the expanded editor, with a
mention picker added beside the model chip. Threads and classic chats now share
the list, wrapping agent grid, header, inset card, prose, step pills, composer
and settings controls. Tap the thread icon or members for details, and its title
to rename. The board opens inside the card. See the [one-product design](one-product.md)
and [paired visual evidence](evidence/one-product/README.md).

Every board list ends with a visible **+ Add**, including items above the first
heading. List titles show item counts. Drag an item on desktop, or hold its grip
on touch; **Move to…** remains in the item menu. Markdown editing uses **Done**
and **Cancel**. Changes still save through the board file route. See
[board discoverability and checks](evidence/board-discovery/README.md).

Each thread keeps a provider and model per member. The composer chip shows the
default agent’s provider mark and saved model; it opens the same **Providers &
models** sheet as the member controls in Thread details. Changes persist until
changed again. “Agent’s default” is an explicit model choice. There is no
per-message model override. See [the sheet, persistence and keyboard/drag
checks](evidence/thread-models/README.md).

Thread reply chips and the composer use the same short model label. Concrete
Claude versions resolve through catalog aliases (“Fable 5.1”); exact Codex
catalog names are kept. Full model IDs stay in tooltips. See
[the integrated sheet, chips and timestamp checks](evidence/thread-model-labels/README.md).

Thread rows keep one state subtitle and an icon layer, with one unread dot
beside the time in its own column. They carry no message preview, working dot or background-task
number. Background counts remain in Details and the header's task-list control.
Automatic solo faces omit the duplicate default corner avatar. See the
[paired row captures and Classic regression checks](evidence/thread-row-signals/README.md).
Pinned and Recent now share one unboxed thread list. The open thread has a
fine ink rule beneath its text, with its title easing from weight 500 to 650.
The rule spans only the title/subtitle column, leaving the row's existing gap
before its time/default-face column. See [clearance captures and box checks](evidence/thread-underscore-clearance/README.md).
The rule retracts under the old row, then draws beneath the new one; it changes
rows only at zero width. Reduced motion makes the change instant. Hover keeps a
faint tint and keyboard focus its ring. On a phone, returning to the list marks the thread just visited
for a second, then fades. See [stroke frames and collision checks](evidence/thread-stroke/README.md).
Reply author chips show a small
provider mark and compact recorded model; message footers show action icons
at the start and a timestamp at the end. Tap the timestamp for recorded turn details. See
[chip and timestamp evidence](evidence/thread-reply-meta/README.md). The mention
picker's first row, **Everyone**, inserts `@all ` to reach every thread member.
See [paired captures and checks](evidence/thread-presence/README.md).
Listening faces stay through pauses, blur/refocus and stream redraws. If an
edited mention temporarily resolves nobody, the last resolved faces stay until
another set resolves or the draft ends. The Everyone row says “everyone in this
thread”; `@all` still addresses the thread's members. See
[the redraw reproduction and paired captures](evidence/thread-draft-lifetime/README.md).

Threads open with a small recent page and an available composer. Readable text
paints before visible prose gains its markdown formatting; full reply steps
remain lazy. Returning in the same page session restores the reading position.
Scrolling up loads earlier messages without moving the reading anchor. A thread
address (`/threads/<id>`) reloads that conversation; `/` still opens the list.
The existing runtime supplies an unread count rather than an exact boundary,
so first-unseen landing uses that count saved before the opening read clears
it. See [opening behavior and the requested runtime additions](threads-and-boards.md#opening-a-thread)
and [measurements, captures and checks](evidence/thread-opening/README.md).

Past thread replies now use the same detailed steps as Classic: ordered prose,
folded tools with input/output, nested subagents, answered cards and expandable
long outputs. Full records load for visible replies after the initial light
read; older replies retain their short step labels. A thread member’s dot means
a turn is working or asking, while **End session** remains available for a warm
idle session. See [reply-step evidence and checks](evidence/thread-steps/README.md).
Before prose, a generating member's reply slot uses Classic's playful words,
12-second first phrase, nine-second rotation and fade. Streaming prose replaces
the waiting words. A parked member has no empty reply slot, including when the
runtime supplies a completed task-only record. Background work has a quiet count
beside Board in the header, shown only while work is outstanding. Tap it for a
desktop popover or phone sheet with each task’s creator, description and elapsed
time; tap a row for its full description and start time. Nothing appears in the
message flow or above the composer. When work ends, the indicator fades and an
open list briefly says “Nothing running” before closing. See
[header task captures and checks](evidence/thread-tasks/README.md).
Streaming replies keep their DOM identity through completion and full
steps, while scrolling preserves the text near the reader's eye through
steps, history and image loads. A composer model alias shows the concrete
version from the latest matching reply, with the saved choice in its tooltip.
See [before/after captures and regression evidence](evidence/thread-continuity/README.md).
Completed agent messages use the same **Read aloud** glyph, Listen/Brief menu,
threshold settings and docked player as classic chats. One thread message is
one response: its prose segments are read in order, excluding tool steps.
Takes are identified by thread, agent and message; the server selects that
agent’s configured voice. Optional persona `voice`/`style` metadata is forwarded
when supplied. Making audio stops at Ready until tapped, and the player follows
across scrolling and chats. See [paired checks](evidence/thread-waiting-speech/README.md).

**New thread** opens immediately without a default; a persona starts one with
that persona. With the live-sessions API, neither action downloads the classic session list;
older runtimes read that listing for Running now’s compatibility view. Model controls
appear only when the runtime exposes member models and the provider offers
choices. The runtime extension at `7dcd478` supplies thread uploads, management, models,
durable ordered segments, waiting and turn completion. The list is live over SSE;
the 15-second refresh remains only as a fallback. See [round-two evidence and the
deployment handoff](evidence/thread-round-two/README.md). A no-default thread
shows its recent members in the header and its own icon when empty. Leading
mentions are removed from a new thread’s first-message title. See the
[live round-three checks](evidence/thread-round-three/README.md).

**Running now** opens from the quiet power icon in the Chats header or from
Settings. See live sessions grouped by activity and end them without leaving
threads. Idle sessions step out immediately; active turns ask first. See
[runtime compatibility and behavior](running.md) and the
[paired visual and live checks](evidence/running/README.md).

An expired portal session stops API calls and stream retries and offers one
**Sign in again** link back through the protected page. Session-cap errors that
contain internal routes are rendered as a short, actionable sentence.

The classic session experience follows the approved design in `design/`, built on the broker's own fields:
a Chats home with an agents strip, pinned and recent rows, search and filters;
a chat thread with the agent's reply as it is written, its tool calls, its
plans, its diffs and the card that asks for your OK; an agent's profile; a
Settings sheet with your agents, your **Providers** and their quota, and
**Integrations** — GitHub, set up and reconnected from the page, and any
service a plugin adds; and a
light and a dark theme. Chats can be renamed, pinned, archived and ended, and
all four of those are on the record rather than in this browser. Several chats
can be chosen at once — "Select" in Chat actions, a held finger on a row,
Ctrl/Cmd-click, or on the desktop the check circle a row shows under the
pointer, with Shift-click choosing a range — and marked read, marked unread, archived, pinned, unpinned or
ended together, and one chosen chat renamed where it stands on the list
(`public/select.js`; each is the per-chat request — the `PATCH`, or for End the
header's own `DELETE` — a few at a time). The design
bundle is the source of truth for every colour, radius and word on the screen,
and `design/README.md` is where it is described.

The conversations you have had in the Claude Code app itself are in that
list too. They run on the same host in the same agent workspaces, so the
broker keeps a record of each and replays its history out of the app's own
session file — a person's turns, the agent's replies, its tool calls, in the
same events a session started here sends. Write into one and it carries on
from there: the host forks the conversation into a session of its own rather
than resuming the app's, so the file the Claude Code app may still be using
is only ever read, and the row keeps the id it always had (`cli_session_id`
and `forked_from` on the record say what happened underneath; a page needs
neither). They are ordinary rows and an ordinary thread; what is different
about them is the host's doing and is written down in `project.md`.

**Activity** opens from Settings on phones and above Settings at the foot of
the desktop sidebar. Its day-grouped feed uses two-line event rows with the
agent's name, one action, a time span and duration. Failures stand out, unseen
items have a dot, and opening a row marks it seen and opens its receipt or
details. All / Chats / Runs / Jobs / Changes chips filter the feed. Current engineer
slots and the queue appear above its filtered feed; Cancel asks first, Undo
keeps host refusals visible, and receipts open in Files. Unseen counts and
marks come from the host. Both pages use the supplied
[broker Schedules and Activity contract](broker-schedules-and-activity-api.md),
including refusals and the launcher's current work. See [Activity](activity.md).

The agent profile's **Schedule** tab lists schedule files with on/off switches,
next runs, relative last edits, host state and changed-since-seen dots. Tapping
one opens its `.md` file in Files; saves show whether the file runs, or the
problem line and whether the previous version still runs. Creation and Run now
stay on the tab. Opening the tab or file marks it seen on the host; background
refreshes leave new changes unseen. Schedule words stay in PT. See
[Agent schedules](schedules.md) and the
[file-first broker contract](broker-schedules-and-activity-api.md#a-schedule-is-its-file-the-one-line-row-and-the-files-save-2026-10-15).

What is deliberately not here yet: Account, Applications, push and making an
agent. Files is implemented below.

The agent profile keeps **Open workspace** beside its identity as a quiet
link, available on either tab. Its former Chats tab is now **Activity**, using
the same event rows scoped to that agent. New chat and pinned chats stay at
the top. Its chat rows use the main list's
`sessionRow()` and pinned group, including Settings → Chat preferences bubble choices,
provider marks and the selected chat treatment. The shared selection gestures
use the existing actions bar; starting selection from a desktop profile reveals
the sidebar if hidden. Unsent messages retain the ordinary thread's Resend/Edit
behavior when opened from either list.

The optional `test/agent-page-chats-browser.mjs` checks both lists against the
same records, switches Working off through Settings, exercises selected and
pinned rows, preserves a refused message, and checks the workspace destination.
It saves screenshots at 390 and 1440 px in light and dark:

```sh
AGENT_PAGE_CHATS_EVIDENCE=/tmp/agent-page-chats node test/agent-page-chats-browser.mjs
```

Install Playwright separately; `LOAM_PLAYWRIGHT` can name its module and
`LOAM_CHROME` a Chromium executable. Evidence must be outside the repository.
The compact Activity browser check covers both pages at both widths and themes:

```sh
ACTIVITY_COMPACT_EVIDENCE=/tmp/activity-compact node test/activity-compact-browser.mjs
```

It checks two-line geometry, failure reasons, chips, bottom-bar clearance,
receipt opening and seen marks, Undo details, and shared profile chat rows.
These are contract fixtures through the real relay, not live host evidence.

Appearance is the first Settings section: Light, Dark or Match system, plus live
75–150% app size in 5% steps. Desktop Ctrl/Cmd +, − and 0 use the same setting.
Phone and desktop preferences are separate host values. **The new broker routes
and registry entries still need integration**; the web half and its relay are
implemented here. [Contract, scaling choice and evidence](appearance.md).

## Where the agent actually runs

The question this app exists around is answered in `project.md`, and the
answer is load-bearing enough to repeat here:

**The agent process does not run in this container.** It runs on the host, as
an ordinary process of the host's own user, in the agent's own working
directory, with the coding CLI's real login and the host's session hooks —
which is what makes it the same session you would get at a terminal there. A
component on the host called the **session broker** starts those sessions and
speaks HTTP; this app is its client.

Two things follow, and they are the shape of the whole app:

- **This app never holds the coding CLI's credential.** It holds a bearer
  token for the broker, and nothing else.
- **This app never decides what a session may write.** The host's write guard
  does, inside the session, exactly as it does today.

The broker is not in this repository and this repository names nothing about
any particular machine. Everything this app needs to know about it is two
environment variables and the routes in `project.md`.

## The stack, and why

| choice | why |
|---|---|
| **Node, standard library only** | Zero dependencies: no lockfile, no install step in the image, no supply chain, nothing to explain. `node:http` serves the page, `fetch` calls the broker, `node --test` runs the tests. |
| **No front-end framework, no build step** | The page is a handful of ES modules the browser loads directly. What is in `public/` is what runs — no bundle, no source map, nothing to rebuild before a change is visible. The whole of the "view layer" is `public/ui.js`: `h()` turns a tag and some properties into a DOM node, and `morph()` puts a freshly built screen into the page **by keeping the nodes already there**. A screen is still built whole on every change, which keeps one code path per row; what reaches the DOM is only the difference, so a stream tick that changes nothing touches nothing and the element under a finger is the same element a second later. A deployed build that replaced the page on every tick lost two automated taps in a row, and a thumb would have lost them too. `key` says which row is which so a row that moves up the list moves rather than being rebuilt; everything else is matched by position. A framework would buy the same diffing and cost an install step, a lockfile and a build stage in the image. |
| **A markdown renderer written here** | The agents answer in markdown and the thread has to render it. Every well-known library renders to a *string of HTML*, which would mean `innerHTML` at the one place in this app that must never have it — and a lockfile and an install step in an image that has neither. `public/markdown.js` builds DOM nodes instead, so a reply cannot become markup by construction. About four hundred and sixty lines of code, with `test/markdown.test.js` beside it. [More below](#the-agent-writes-markdown-and-no-reply-is-ever-html). |
| **The three typefaces are served by this app** | Caprasimo, Figtree and JetBrains Mono are in `public/fonts/`, fetched once by `tools/fetch-fonts.js` and committed. Not from a font vendor at run time: this page sets `default-src 'self'`, so a stylesheet from elsewhere is refused, and a phone on a bad connection should not wait on somebody else's CDN to know what the app looks like. 116 KB, latin and latin-ext only. |
| **Server-sent events, browser ← app ← broker** | The broker already speaks SSE, so a websocket would mean translating one stream into another for no gain. SSE is one-directional and messages go back over ordinary `POST`s, which is what the broker's own interface expects. It also survives a reverse proxy without an upgrade handshake, and reconnects by asking for a position — which is exactly the `?from=<seq>` mechanism the broker gives us. |
| **Events passed through unchanged** | The app relays the broker's stream byte for byte and reduces it in the browser. Nothing invents a second vocabulary for the CLI's events. |
| **No durable state of this app's own** | Sessions, costs and transcripts are the broker's, and the page draws them from the stream every time it opens one. This browser keeps device preferences, composer drafts and unsent messages per chat: whether the desktop's list column is hidden, how this device takes dictation (Settings → Dictation), and where it warns about a chat's context (Settings → Chat preferences). Pin, archive and title are **not** among them — they are on the record, so a pin made on the phone is a pin on the laptop. `/storage` holds one thing: the read-aloud audio, under `/storage/speech`, which is a cache and not a record — swept after 48 hours unplayed, and made again on asking. [Read aloud](#read-aloud-a-reply-as-speech). |

## Run it

One command, with a broker to talk to:

```sh
docker build -t loam-ui .
docker run --rm -p 8080:8080 --read-only -v loam-ui-storage:/storage \
  -e BROKER_URL=http://<broker-address>:<port> \
  -e BROKER_TOKEN=<token> \
  loam-ui
```

The named volume is where read-aloud audio is kept; without it the root is
read-only, speech reports `no storage`, and nothing else changes.

Natively, with no container, from a fresh clone and with nothing installed
but Node 22 or newer:

```sh
BROKER_URL=http://<broker-address>:<port> BROKER_TOKEN=<token> npm start
```

### With no agent anywhere

To see the page work without a host, a broker or a coding CLI, run the
recorded broker in `tools/`. It speaks the same routes and replays a recorded
session with the pauses left in:

```sh
node tools/fake-broker.js &                                   # 127.0.0.1:8932
BROKER_URL=http://127.0.0.1:8932 BROKER_TOKEN=dev npm start   # 127.0.0.1:8080
```

Open `http://127.0.0.1:8080`, tap an agent in the strip, pick a provider and
start. It plays two recorded turns: the first has a background subagent and
asks you for a permission part way through, waiting for your answer because the
real one does; the second is a real session's stream, sanitised — a sentence, a
call, its result, a sentence, a call, its result, a sentence, then **five calls
in a row** and a closing sentence, which is what makes the thread's fold
something to look at rather than only something a test asserts. Replying plays
that second turn again. A reply that is `/ask single`, `/ask multi`,
`/ask text`, `/ask secret`, `/ask three` or `/ask form` asks a question of that shape
instead; `/ask several` raises three approvals at once, each with the session
rule the CLI would propose, and `/expire` and `/cancel` close the oldest open
request as the host does when nobody answers or the CLI withdraws one. Ending
the session closes every open request as `ended` before `broker.exit`, an
answer for an id the fake does not hold is the real broker's `404`, and an
allow carrying `updatedPermissions` is resolved with `scope: "session"` — or
refused `400` in the real broker's words when any entry's `destination` is not
`session` (the second of `/ask several`'s three also proposes a
`localSettings` rule, so the page's filter is checked by the host), and `GET /v1/fake/answers` on the fake broker shows the answer bodies
the page sent. The round button by the tab bar opens
Settings, where the agents, the Providers and their quota, and Appearance are.

The recorded broker offers three models and three effort levels on Claude Code
and none on the providers that are not wired, and it takes them on `PATCH` and
on a new chat — so the model button, the context pill, the New-chat sheet's own
pill and the refusal of a model a provider does not offer can all be exercised
here.
Codex is there signed out, in the runtime's shapes: its chip on the New-chat
sheet is drawn and disabled until you sign in on its Providers page, where
*Sign in* hands out a device code and the recorded broker plays the person
entering it eight seconds later — the card turns to *"Signed in"* by itself,
and Codex becomes a chip you can start a chat on (it plays the same recorded
turns).
That is the **only** place they can be: see "The + menu and the model button"
below for what the host's own interface does and does not yet say about these
fields.

It starts with three chats already on the home screen, the way a real one does
after a restart: sessions whose process is long gone, carrying the title, the
last message and the status the host backfills from their own logs — and one
with nothing said in it at all. Opening any of them replays its log and ends
the stream where the log ends; sending to one brings it back under the same id.
Two of its four agents share a first letter, which is what makes the avatar's
two-letter initial ("Sc" for Scout) visible at all.

Four things added on 2026-09-23 are all visible here. Every turn it plays
carries two background tasks for the length of the turn and drops them to zero
at the `result`, so the row's "WORKING · 2 IN BACKGROUND" kicker and the chat's
own "Working · 2 in background" line — and the sheet or popover it opens — are
things to look at. Its turn records carry a
`context` reading, so the line under a turn opens to "Opus 5 · 35k of 1M …".
One of the seeded chats is an imported conversation with a `history_bytes` on
it — though with the sign-in below two days from running out, the sign-in rung
outranks it until that is renewed, which is the ladder to look at. And its wired provider's
sign-in is **two days from running out**, so the warning is up from the first
screen: signing in again on the Providers page is a real flow against it — the
URL, the code field, and a new expiry thirty days out that takes the warning
away. Pasting the code `no` is the one it refuses, so the failed state can be
looked at too.

The pinned chat's answer is **written in markdown the way a real one is** —
bold, a numbered list, a link, a fenced `sh` block — and ends with an
`<img onerror=…>` and a `<script>` so that "a reply is never HTML" is something
to look at in a browser and not only something a test asserts. Its row on the
home screen shows the same words with the marks taken off.

Nothing in the app needs the network at build or start: the typefaces are
committed. `node tools/fetch-fonts.js` is how they got there and is run by
hand when the design's type changes, never by the app.

### Check it

```sh
curl -i http://127.0.0.1:8080/up          # -> 200, body: ok
curl -s http://127.0.0.1:8080/api/health  # -> whether the broker is answering
```

`/up` answers 200 the moment this app can serve. It is deliberately **not** a
claim about the broker: if the broker is down, the page still loads and says
so, and that is a more useful failure than a container that refuses to start.

## Configuration

| Variable | Default | What it is |
|---|---|---|
| `APP_PORT` | `8080` | the one HTTP port the container listens on |
| `BROKER_URL` | — | the session broker's base URL, e.g. `http://<address>:<port>`. Unset, the app still serves and the page says no broker is configured. |
| `AVATAR_COLOURS` | `{}` | optional JSON object of persona IDs to existing palette tokens, e.g. `{"persona-id":"c54"}`. Only named personas change; all other colours keep their original ID mapping. See [thread round two](evidence/thread-round-two/README.md). |
| `BROKER_TOKEN` | — | the bearer token for the broker, supplied at run time. **Never in this repo, never in the image.** The browser never sees it: every call the page makes goes to this app's own `/api`, and the token is added on the way out. |
| `FILES_VAULT_URLS` | — | optional: comma-separated `https://` prefixes of the published vault; a link to one opens in Files instead of leaving the app. Anything not `https://` is ignored. |
| `FILES_CONTENT_ORIGIN` | — | optional: dedicated HTTPS content origin on a different hostname; enables scripted HTML previews and relative assets. Requires the host proxy setup in [Live HTML: host handoff](files-content-origin.md). Unset, HTML uses the existing static preview with an explanation. |
| `GEMINI_API_KEY` | — | the key dictation's transcription is billed to, supplied at run time. **Never in this repo, never in the image**, and never sent to the browser: the page posts a recording to this app's `/api/transcribe` and gets words back. Unset, the app serves and the mic records, and a recording comes back with a sentence saying transcription is not set up. |
| `GEMINI_API_URL` | `https://generativelanguage.googleapis.com` | where Gemini's API is. There to point the tests at a fake; nobody else needs it. |
| `DICTATION_POLISH_MODEL` | `gemini-3.5-flash-lite` | the text model that polishes a recorded take, on the same key. [Polish](#polish-a-second-pass-over-the-words). |
| `SPEECH_TTS_MODEL` | `gemini-3.8-flash-tts` | the model a reply is voiced with. Google has no tracking alias for its TTS models (checked 2026-10-02); the app lists the models once at start and logs any `…-flash-tts-latest` it finds, and uses it **only when this is unset**. [Read aloud](#read-aloud-a-reply-as-speech). |
| `SPEECH_BRIEF_MODEL` | `gemini-flash-latest` | the model that writes a spoken brief: Google's alias for the newest Flash text model |
| `SPEECH_VOICES` | — | which prebuilt voice each agent speaks in, and a few words of its character: `agent=Voice:character;agent=Voice:character`, e.g. `scout=Iapetus:clear and direct`. The agents are this installation's, so they are configuration and never a literal here. An agent not listed — or every agent, when this is unset — reads in `Charon`, informative. A voice that is not one of the thirty prebuilt ones is dropped at start with a log line. |
| `SPEECH_PACES` | — | measured word equivalents/minute per prebuilt voice, e.g. `Aoede=147;Aoede.brief=121;Charon=155`. `.brief` and `.verbatim` override one mode; an unsuffixed voice sets the base pace. Aoede defaults to the supplied Full measurement of 147 and brief measurement of about 121; its brief uses 121 unless `.brief` explicitly overrides it. Other voices inherit their base pace in either mode, provisionally 150 when unlisted. Invalid entries are logged and dropped. Synthesis and cache checks use the same pace. |
| `SPEECH_DIR` | `/storage/speech` | where the MP3s and their sidecars are kept. Not writable, and speech says `no storage`. |
| `SPEECH_DOCUMENT_MAX_CHARS` | `250000` | the character cap for a Files note, separate from the chat reply cap. Full reads retain document seams when chunking; briefs pass the document to the writer in one request. |
| `SPEECH_TTL_HOURS` | `48` | how long an MP3 is kept **from its last play**. Swept at start and hourly. |
| `SPEECH_MAX_CHARS` | `60000` | the longest text one request will read; over it is a `413` |
| `SPEECH_CHUNK_CHARS` | `1000` | the target ceiling for one TTS call; a single indivisible sentence may exceed it. Full documents prefer headings, paragraphs, list items, then sentences near a balanced target. Chat and brief prose retain balanced paragraph/sentence chunks. |
| `SPEECH_CONCURRENCY` | `4` | how many chunks are voiced at once **per job**; several jobs can exceed that total. Tune against the project's model quota, not just document length. |
| `SPEECH_BRIEF_MAX_WORDS` | `300` | the brief's hard ceiling; it is asked for about 220 |
| `SPEECH_FFMPEG` | `ffmpeg` | the encoder's binary. The image installs it; set this to run the real-encoder tests against one that is not on the path. |
| `LOG_LEVEL` | `info` | how much the app writes to stdout: `debug` adds a line per static file, `info` is every other request and every transcription, `warn`, `error`, or `off`. Anything else reads as `info`. [Logging](#logging). |

## Logging

The app writes **one JSON object per line to stdout** and nothing else there;
the container keeps it. `src/log.js` is the writer, and `track()` in
`src/server.js` is the only thing that writes a request's line.

**One line per request**, written when the response is done with — finished,
or cut off, which for an event stream is when the page went away, so `ms` on a
stream is how long it was open:

```json
{"ts":"2026-09-29T02:04:25.349Z","level":"info","event":"request","id":"fac9a888","method":"GET","path":"/api/sessions","query":"agent=a","status":200,"bytes":32,"ms":14}
```

A stream the page closed adds `"aborted": true`. `id` is eight hex characters, and the same id is on the response as
`x-request-id`, so a failure the page reports can be found in the log. `query`
carries only the parameters the page is allowed to send (`agent`, `archived`,
`from`, `last`, `before`); any other is named with its value left out. `bytes`
is the response body. **Never** a request or response body, a cookie, an
authorization header, the broker token or the Gemini key.

Levels: a static file that was served is `debug`, so a page load's score of
files does not bury the rest at the default level; a static miss, `/up` and
everything under `/api` is `info`; anything that answered 5xx is `error`, and
carries the failure's sentence as `error` on the same line — the broker's own
when the broker failed, and, for an exception this app caught, its message and
`stack`. One line per request either way, so there is never a second line to
match to the first.

**One more line per transcription**, beside that request's line and under the
same `id`:

```json
{"ts":"2026-09-29T02:04:25.365Z","level":"info","event":"transcribe","id":"846fb89e","status":200,"bytes":240000,"mime":"audio/webm;codecs=opus","gemini_ms":6,"gemini_status":200,"timeout_ms":150000,"chars":31,"biased":true,"terms":2,"retried_without_terms":false,"outcome":"ok"}
```

`outcome` is `ok`, `heard-no-words` or `error`, and an `error` carries the
sentence the page was shown (at `warn`). `gemini_ms` is every call to Gemini
for the take, a retry without the terms included; `gemini_status` is the last
one's, and `null` when the take was refused before Gemini was asked. `chars` is
the transcript's length. Never the audio, never the words, never the terms
themselves.

**One line per synthesis** — written when the audio is ready or has failed,
which is after the `POST` that started it has long answered, so it carries
the speech id rather than a request's:

```json
{"ts":"2026-10-02T10:12:03.511Z","level":"info","event":"speech","id":"5d0c…","agent":"scout","mode":"brief","chars":8412,"spoken_chars":1290,"chunks":1,"duration_s":94.3,"tts_model":"gemini-3.8-flash-tts","voice":"Iapetus","gemini_statuses":[200,200],"outcome":"ok","ms":21890}
```

`chars` is the text the page sent, `spoken_chars` what was voiced (the brief,
for a brief). `chunk_checks` records each WAV's `chunk` (one-based), `attempt`
(1 or 2), raw `words`, estimated `spoken_words`, `duration_s`, `pace_wpm`, `expected_s`, `ratio` and
`accepted`, including rejected attempts. It holds no transcript.
`gemini_statuses` is every call in order — the writer's first,
for a brief, and each retry — with `timeout` or `unreachable` where there was
no status. An `error` outcome carries the sentence the page was shown, at
`warn`. Never the text, never the brief, never the key.

Other lines: `{"event":"listening","port":8080}` at startup, one
`{"event":"config","message":…}` warning per unset variable the app can run
without, one `{"event":"speech-config",…}` warning for each thing speech is
missing (`no key`, `no encoder`, `no storage`), and one
`{"event":"speech-models","tracking_alias":…,"tts_model":…}` saying which TTS
model is in use and whether the models list held a tracking alias.

## Test

```sh
npm test
```

Six hundred and fifty-nine tests, no agent, no browser and no network beyond
loopback, then two more for the icons in `tools/make-icons.test.js`, which run
after the rest because the rasteriser they run takes a core for several seconds
and the page's tests are timed. Those two need `python3`. Every test has twenty seconds (`--test-timeout=20000` in
`package.json`) and fails rather than hanging past it; the slowest today takes
about four. What they cover:

- **`test/speech.js`** — Listen and Brief through the real page, run at 430px
  (`speech.test.js`) and 1280px (`speech-desktop.test.js`), with the speech
  routes stubbed at the page's fetch to the server's contract and a stand-in
  audio element: the exact body posted (the three-turn set with a fold, a
  thought, a tool result and a question card between its turns), the glyphs
  by threshold, 202 → 202 → 200 with the stages, elapsed / total, a seek by
  tap, drag and arrow key, the rate cycling and stored, one player at a time,
  a glyph toggling, ended, a refused `play()`, a failure and Retry, a `404`
  re-posted, the brief fold, the Settings steppers, and leaving the chat.
  `speech-off.test.js` is the app's own `/api/health` answering `speech: false`, and no
  glyphs; `speech-set.test.js` is `public/speech.js` alone.
- **`test/log.test.js`** — the app's log: one line per request of the right
  shape and nothing in it that was sent (body, cookie, authorization, token),
  the id on the line and on `x-request-id`, static files at `debug`, a broker
  5xx and a caught exception each one `error` line with the sentence, an event
  stream logged when it closes, one transcription line with every field and
  none of the words, and the two dictation limits against the page's own
  numbers.
- **`test/transcribe.test.js`** — dictation's server half against a fake
  Gemini: the request it sends (inline audio, smart mode, the vocabulary, the
  key in its header and never in the answer), the retry without vocabulary if
  the pair is refused, every refusal as a status and a sentence, and the take
  as a form with a thousand terms in its body beside the audio.
- **`test/polish.test.js`** — Polish, as Spoken tests it: the prompt and its
  numbers held to Spoken's own source; the request (the key, every term past
  the transcriber's cap, the fences, `store: false`); the trap transcript (a
  question, an instruction, a listed name misspelled three ways) with the
  hand-written Light answer landing and the bad answers — the question
  answered, the instruction obeyed, a preface, a sentence dropped — falling
  back; the 10% and 25% thresholds at their edges, and the one- and two-word
  floor on a four-word take; and the fallbacks for a
  timeout, a 500, a 429, a wrong key, an unknown model, no server and an
  unfinished interaction; then the same through `/api/transcribe`, with
  nothing of the answer in the log.
- **`test/speech-server.test.js`** — read aloud, against a fake Gemini (a TTS model
  answering a short real WAV, a writer answering words) and a stand-in
  encoder: the transform on `test/fixtures/speech-reply.md`, the brief written
  and voiced and returned, the id stable and changing when it should, `202`
  through the stages to `200`, a second `POST` joining the job, `Range` and
  `416`, the last-played bump and a sweep that removes only the expired, `503`
  and `speech: false` for the key, the encoder and the directory each, `413`,
  retries and failures as one sentence, the log line, and the tracking alias.
  **Two tests need a real `ffmpeg` with libmp3lame** — on the path, or named
  by `SPEECH_FFMPEG` — and skip with that reason when there is none:
  `SPEECH_FFMPEG=/path/to/ffmpeg npm test` runs them.
- **`test/dictate.test.js`** — dictation's page half with a fake recorder, a
  fake microphone, a recorded buzzer, a fake wake lock and a clock the test
  moves: the tap at the start, the two pulses thirty seconds out, the long pulse
  at the cap and nothing on a pressed stop, the soft tone where `vibrate` is
  refused, the screen held from the press and let go by every way out and asked
  for again after the page is hidden and shown, the stop at the cap, the biasing terms taken
  from the chat, the take's DOM in the field in each state and the row under it
  quiet, the words merged at the caret (before, into the middle of and into an
  empty field), ✕ while recording, ✕ while cleaning and Escape each leaving the
  field as it was, a failed take with one Retry, no words heard, a refused
  microphone, dictating in the full-screen editor, that no node of the old
  card above the composer remains, the Polish row round-tripping through the
  page (Light by default, Full's prompt sent, Off sending no polish call), and
  a polish that fell back landing the words as heard with its reason on the
  quiet line. Playback checks hold the recorder's final event and the
  transcription response separately: ✓ and Cancel release mic tracks at once,
  Play works while transcription is pending, the returned text and field focus
  leave playback running, and the next recording pauses it. Audio/session
  fixtures establish page behavior; phone audio routing still needs a device.

- **`test/sessions.test.js`** — the home screen's model, which is the join this
  app is made of: a record of the interface in, a row of the design out. That an
  ended session draws as **Done and not as a failure**; that `needs_input`
  outranks `working`; that a session which says it is working and has been
  silent for minutes stops breathing and says **"quiet for N min"** instead, in
  the row and in the thread; that archived rows leave the screen the
  moment the stream says they are archived, and that one still running says so,
  because archiving is not ending; that the filters are the record's
  own fields and the search reads the host's words; and the one rule a row is:
  that **every state is shown until the row is seen and a seen row is the
  minimal row**, with each of the four states going when the row is seen and
  coming back when `record_seq` moves past the mark — which is a position and
  not a clock, is on the record rather than in this browser, and only ever
  moves forward. And what a working row is waiting on: that the kicker reads
  **"Working · 2 in background"** and plain "Working" at zero, that one task is
  not "1 tasks", that a row gone quiet still says it, and that a record with no
  count — or a nonsense one — is a row with no count. And `waiting` as its own
  state: that it shows the dot and not the breathing, that its kicker reads
  **"Waiting · 2 in background"**, that it is never drawn as quiet because its
  silence is the result it is parked after, that the dot outlives the seen rule
  for `working` and `waiting` alike where the box does not, that `done`, `failed`,
  `idle` and `needs_input` have no dot at all, and that a status the row has never
  met is drawn as working. And the ladder the notice
  row in the composer is: every rung on its own, every rung losing to the one
  above it, the settled statuses that make an unknown word read as working, the
  wake cost rounded to a whole dollar, and that a **draft** skips the "not
  running" rung while keeping every rung above it. And residency: that the Live
  filter is the broker's own boolean and an absent one narrows nothing in, that
  the chip is offered only once some record has stated it, and that the count is
  the host's two numbers or is not drawn at all.
- **`test/integrations.test.js`**, **`test/github.test.js`** and
  **`test/github-register.test.js`** — Settings → Integrations. The card in
  each of GitHub's five states against the broker's own shapes; the manifest
  form's method, target and hidden field, and no form at all for a target that
  is not GitHub; both return paths handing their query to the broker and
  reopening Settings on Integrations; a refused `state` and a GitHub refusal
  shown as the broker's sentence with the pill to retry; Install in the same
  visit using the register answer's address; Refresh, Disconnect's one
  question and its three links; and the notice row and the tab dot on an
  unhealthy state. The two page files are one process per return path, because
  the page reads where it was opened once, at boot.
- **`test/acme-view.test.js`** and **`test/acme-integration.test.js`** — a
  sample plugin integration's card, device-code sign-in and alternative token
  sheet, against fixtures (`test/fixtures/acme.js`, the page in
  `test/fixtures/acme-page/`): pending link/code and both Copy buttons; polling
  to connected, accounts, expiry and denial; the login picker POSTing `{account_id}`
  while the token picker PUTs `{token, account_id}`; Cancel and dismissal with no
  further GET; source-specific card copy, warning and notice; both disconnect
  endings. The token sentinel still checks both storages, console output and
  DOM after the PUT. That core relays no integration route of its own is in
  `test/server.test.js`. Tests print DOM outlines as diagnostics for review.
- **`test/github-orgs.test.js`** and **`test/github-org-register.test.js`** —
  organizations' apps, against the broker's `org_apps` shape
  (`test/fixtures/github.js`): no Connect against a broker without the key; an
  org card in each of its four states beside an unchanged first card; Connect
  posting `{org, name?}` and the form to the org's own create page, and
  refusing a bad login or a personal create page; the org's Install asking with
  `{org}`; *Disconnect all* and its org link; and the org's register return
  offering its own install address while the first app is connected.
- **`test/providers.test.js`** — the Providers screens. That `utilization` is a
  **fraction**, so `0.06` is a bar 6% wide and reads "about 6% used"; that a
  window this page has never heard of is still drawn, after the two the
  interface names; that no reading is **not** a reading of zero, and that a
  provider which is not wired gets the host's own sentence rather than an empty
  quota bar; and that the reading says when it was taken, because a figure from
  four hours ago is not a current quota. And the chat header's ring: the highest
  of all stated windows and buckets, the three bands at 0, 69, 70, 89, 90 and 100, an empty
  track where there is no reading, and a `rate_limit_event` read as the same
  reading arriving earlier. And the sign-in: that the warning starts at
  `days_left <= 3` and counts down in the host's own number, that `0` means
  *less than a day* and a negative number means *has expired*, that a provider
  which is not wired has no sign-in to warn about at all, and that "connected,
  expiry unknown" is an answer rather than a warning — a page that printed
  "expires in null days" would be inventing one. And Codex's shapes: the default
  model is the one flagged, efforts are the model's own, the New-chat choices
  are the wired providers with a signed-out one not choosable, and Codex signed
  in has no expiry while never signed in is not expired.
- **`test/codex.test.js`** — Codex on the page, against a stub broker in the
  runtime's quoted shapes: the New-chat sheet with Codex signed out (drawn,
  disabled, "Sign in on the Providers page" under it) and signed in (its own
  models, efforts per model, an effort reset when a model stops short of it);
  the Codex Providers card signed out, in its device-code view (URL, code, two
  Copies, Done, Cancel) and signed in (plan and account, no expiry); each row's
  and each header's provider mark; and the context sheet on a Codex chat with
  its window in the numbers and no Compact now, while a Claude chat keeps it.
  The weekly-only `prolite` reading also checks the exact 5% ring title,
  the Providers row and tap card with and without stale replay, extra buckets
  and model windows, all three usage-source lines, and `usage_reason`.
  `PRINT_CARDS=1` prints the sheet and the cards as markup.
- **`test/marks.test.js`** — the marks and the arc, with no browser near them.
  That the arc's length **is** the percentage — the circle is drawn to a
  circumference of 100, so `69 100` is 69% and nothing converts anything — that
  the band is a class the page computes once, that no reading draws a track and
  no arc at all, and that a provider with no mark falls back to its letter.
  **Fixture-only**: nothing here proves a mark looks like its vendor's.
- **`test/transcript.test.js`** — the seam. A recorded event stream in
  `test/fixtures/`, and assertions about what the page would draw: text
  accumulating while it streams, the whole message replacing it without
  doubling, a tool call collapsed with its result and drawn exactly once, a
  subagent nested under the call that spawned it, both sides of the
  conversation, a permission card that goes when anyone answers it, a retry
  becoming a sentence, the cost and model of a turn arriving on the snapshot
  that records it, and — the ones that matter most — that an event delivered
  twice changes nothing, that `seq` is never assumed to start at 1, and that a
  replay reaches its last event however a drop in the middle is resumed from.
  It also covers what a tool call *is* on the page — a plan card, a diff row or
  a command row — including that an edit which has not happened yet is not
  drawn as a diff, because a diff says a file now reads differently. And, on a
  second fixture which is a **real session's stream**, sanitised: that a
  message the CLI states in two events keeps both the sentence and the call it
  introduced, that every sentence of a turn is still in the transcript after
  every single event of it, and that no step of a run — the call in flight, the
  call that failed, the thinking — is drawn outside the fold over it. And the
  readings the "not running" notice is made of: that an exit is cleared by
  anything numbered after it and by a record that says `running`, that a
  repeated exit is still one line, that in flight is the record's `status`
  with an exit clearing it and a local send unable to start it, that a turn's context comes off the `result`
  and off the record with either half allowed to be unknown, and that a model
  id is read as a name — `claude-haiku-4-5-20251001` as "Haiku 4.5", because
  eight digits are a release and not a version.
- **`test/server.test.js`** — the web half against a stand-in broker: the
  token reaching the broker and never the browser, a `429` over the session
  cap arriving in the broker's own words, events reaching the browser as they
  happen rather than when the stream closes, a pin going out as a `PATCH` and
  the broker's `400` coming back in its own words, the live list asked for
  **without a position** because it has none, the four provider sign-in routes
  wired to the broker routes they mean with the code going through as written,
  a `409` for a code posted when nothing was waiting for one and a `404` for a
  sign-in asked for on a provider that is not wired, and no route the page has
  not been given. And the header the page is written against: `default-src
  'self'`, with no `unsafe-inline` and no source of its own for styles, which
  is the half of "no inline anything" that lives on the server.
- **`test/ui.test.js`** — what a redraw is allowed to do to the page. That a
  tick which changes nothing writes nothing at all; that a row which changed
  changes only itself and stays the same element; that a row which moves up the
  list is moved and not rebuilt; that the node which stays answers to the
  closure of the redraw that kept it; and that a field a cursor lives in is
  handed back untouched. And what it may do with a **style**: carried across
  one declaration at a time, taken off when the fresh node stops naming it,
  written not at all when it has not changed, and **never as an attribute** —
  which a browser under this app's content security policy refuses. The DOM it
  runs against is written for the test (`test/fakedom.js`), records every
  mutation, and enforces that one policy by throwing on a style attribute or a
  `<style>` element, which is what makes both "touched nothing" and "nothing
  the browser will refuse" things a test can fail on. **Fixture-only**: it
  proves the reconciler's arithmetic, not that a phone draws what the
  stylesheet says.
- **`test/app.test.js`** — the page itself, driven: the real `app.js` against
  the real `/api`, a broker that is a recording, and the same fixture DOM. That
  a row shows the host's `title` and `last_message` and reads "New chat" only
  when the title is `null`; that it shows the agent's **name** and the design's
  one-or-two-letter initial; that a snapshot for one session leaves every other
  row the same DOM node; that a tap on a row opens the chat and draws its
  replayed transcript with the composer last on the screen; that a tick
  inside a chat leaves the back button, the header and the composer alone; that
  a reply arrives as **markdown** — a `strong`, a list, a fenced block, a link
  with `target` and `rel` — and that a reply made of HTML arrives as characters
  with no `img` and no `script` anywhere in the tree; and that a **delta
  redraws only the message being written**, with every mutation it caused
  landing inside that one node and the finished message above it still the same
  element. And the one the phone asked for: a recorded turn — a sentence, a
  call, its result, a sentence, a call, its result, a sentence, then five calls
  in a row — replayed into the page **one event at a time**, with every
  sentence said so far asserted to still be in the DOM after each of them, the
  run showing the call in flight and one line for the three behind it while it
  runs, and one line for all five when it ends. And the seen rule as the DOM
  has it: a row's classes and children before and after its thread is opened,
  after a new turn, after a new permission request and after mark-all-read; that
  a seen row holds **no text node** carrying `last_message` anywhere in it; and
  that the thread header's pin button after a pin has exactly the classes and
  title it has when a pinned thread is opened fresh — which is what proved the
  pin defect was not the page's. And the window a chat opens at: that the
  **Earlier messages** marker is drawn from the host's `turns_before` and from
  nothing the page counts, that tapping it asks for
  `?before=<window_first_seq>&last=10`, that the page that comes back goes in
  **above** what is being read, and that a `turns_before` of 0 takes the marker
  away. And the one line a chat draws above its composer: that **three things
  being true at once draws only the highest**, with the two that lost simply not
  drawn; that an ended chat says it is not running and **stops the instant a
  message is sent**, staying quiet while the resumed session answers; that a
  session the host calls `working` never says it, whatever its `state`; that a
  status this page has never heard of reads as working and `waiting` reads as
  itself; that the working line says how many are in background and opens them
  as a sheet on a tap, and that a chat simply answering says nothing at all;
  that an imported chat says what the first reply will cost, and stops saying it
  when `history_bytes` comes back `null`; and the sign-in warning through to the
  code posted and the new expiry redrawn with nothing re-fetched. And the
  composer around it: that the transcript's bottom padding is the dock's own
  measured height and follows it when the draft grows, that the expand control
  round-trips a draft through the full-screen editor and a send from either is
  one send, and that an image pasted in **either** of the two shapes an Android
  keyboard uses attaches and rides on the next message. And the line
  under a turn: the model's short name alone, one tap to the whole line with
  the context on it, a second tap back, and nothing at all on a turn whose model
  the host could not name. And the fold: a step that failed still inside it with
  the count saying so quietly, and a working fold reading the latest line of the
  agent's thinking.
- **`test/list.test.js`** — the same page and the same kind of recorded broker,
  in a second process, because these tests need a list they can archive rows out
  of without taking the chat and composer tests above them with it. The archive
  end to end: that the sheet's **Archive** writes `{"archived": true}` and takes
  the row off the list at once, that a snapshot for an archived chat does not
  bring it back, that the **Archived** list shows it with "still running" and the
  broker's own note when its process is alive, and that unarchiving moves it the
  other way. That a **pin moves a row rather than copying it** — through the
  write, the snapshot that follows it, and the re-read listing. That a chat opens
  **on the last message the person sent**, once the window has reached the
  record's `record_seq`, and that a chat with nothing said in it is not scrolled
  at all. That the scroll happens **before the card lifts** — as an order in
  the write log, for a chat of several exchanges and for a chat of one long
  message, which is the case that looks like a missed scroll and is not. And
  that a stream ending short of its own stated `record_seq` still lands rather
  than leaving the card up to the give-up timer. And the draft: that what is typed in one chat is parked under that
  chat's id and is not in the next one opened, that it comes back with its
  uploaded files as the **host's own chips** when that chat is opened again, and
  that sending clears it and sends the paths. The card a chat opens under: that
  it is up while the window is arriving and lifts once the window is drawn, that
  a snapshot on the open chat never puts it back, and that **a stream that drops
  and reconnects never puts it back either** — a chat already on the screen is
  not being opened. And that every row on an agent's profile carries the same
  provider mark the home rows carry, under the phone's layout as under the
  desktop's. The **draft a new chat is** until it is spoken to: that Start asks
  the host for nothing and offers no rename, pin or three-dot sheet, that
  abandoning one leaves no session and no row and takes the words with it, that
  the first message is `POST /v1/sessions` and *then* the message into the id it
  answered with, that a refusal leaves the draft and what was typed exactly
  where they were with the host's sentence above them, and that a file attached
  to a draft is held rather than uploaded and goes up the moment the chat
  exists. And **residency**: that no Live chip is offered until some record
  says whether it is live, that the counter reads the listing's own number, that
  the chip narrows the list to `live: true`, and that a snapshot flipping one
  moves the count. **Fixture-only**, like the two above: `test/fakedom.js` has no
  layout, so "opens on the last message" is proved as *which node the page
  scrolled to*.
- **`test/thread.test.js`** — a third process, in a **1280px window**, for the
  three things about a chat that need the list and the thread on the screen at
  once. That an agent's words are drawn with **no bubble** and the person's still
  has one; that a long message of the person's is **clamped with a Show
  more/Show less** that opens and closes and survives the host's next snapshot,
  and that nothing else on the screen grows one; and the caused sequence
  `working` → `waiting` → `done`: that the header's blob breathes, then holds a
  steady halo with no animation, then stops; that the row's dot and its
  background count are there for the first two and gone at the third; that the
  dot **survives the chat being read**, which is the half of the owner's complaint
  the seen rule caused; and that a status this page has never heard of reads as
  working. **Fixture-only**: there is no layout, so the height a message is
  measured at is written onto the node by the test, which is the one thing a
  browser would have supplied.
- **`test/frame.test.js`** — the whole page again, against a browser whose
  `requestAnimationFrame` **never calls back at all**: the screen still arrives
  and later changes still land, because a redraw waiting on a frame is raced
  against a timer. A page that draws with no frames draws with some, and one
  that waits for a frame it is never given is frozen until it is reloaded.
  **Fixture-only** in the same way as the two above.
- **`test/viewport.test.js`** — the keyboard, as arithmetic. The app's height
  and position from the numbers `window.visualViewport` reports: that a keyboard
  shortens the app to what can be seen, that a browser scrolling the visual
  viewport moves the app with it, that a pinch-zoom is not a keyboard, and that
  an address bar collapsing is not one either. Also that the viewport meta
  carries `interactive-widget=resizes-content`, and that a browser which honours
  it — window and visual viewport both 508 — reaches the same box as one that
  does not. **Fixture-only**, and more so than any other file here: it proves
  the app asks for the right box, not that a phone draws it.
- **`test/keys.test.js`** — what Enter does. That bare Enter, Shift+Enter and
  Alt+Enter send nothing; that Ctrl+↩ and ⌘↩ both send on either platform; that
  an input method's composing Enter belongs to the input method; and which
  `navigator` fields name a Mac. **Fixture-only**: these are objects shaped like
  a keydown, not a keyboard.
- **`test/style.test.js`** — the stylesheet, read back. That every `:hover` is
  inside `@media (hover: hover)` and every `:active` is not, because a tap on a
  phone leaves a hover stuck on the thing it landed on; and that the composer is
  a rounded rectangle of two stacked rows whose draft scrolls inside itself, with
  the six-line ceiling the same number in the stylesheet and in `app.js`. And
  that **row two is a row at every width**: that it has a class of its own, that
  no rule in the file gives that class a column direction, that no width query
  touches it at all, and that its three buttons are the design's 40px — which is
  the defect of 2026-09-22, where row two shared a class with the action sheet's
  card and was drawn as the sheet's column on a phone. And, since 2026-09-25:
  that the **agent's words have no fill and no radius** while the person's keep
  both, which is the one place the departure from the design bundle could be
  undone by habit; that the measure for prose is stated in characters and is on
  the prose blocks and not on a table or a code block; that the height a long
  message folds at is the same figure in the stylesheet and in `app.js`, with the
  fade drawn in the bubble's own fill rather than as a mask that would take the
  bubble's corner with it; and that the waiting halo carries no animation, because
  an animated one would read as a turn generating. Also **fixture-only**: it
  reads the CSS, it does not paint it.
- **`test/awaiting.test.js`** — the line where the reply will be, read against
  a fake clock: "Thinking…" at 0 s and 5 s, "Digging in…" at 20 s, the fade
  just before each turn-over, and the phrases going round on a long wait.
- **`test/seen.test.js`** — how far a chat has been read, on its way to the
  host: that an event while the thread is open marks it, that a burst of events
  is one write and not one per event, and that **leaving flushes a mark the
  throttle has not sent yet** — which is the defect the owner found, a chat drawn
  unread the moment they left it. The clock and the timers are injected, so the
  test leaves a thread a millisecond after an event, which is what a finger
  does. **Fixture-only**: it proves the arithmetic and what is sent, not a
  browser closing a tab.
- **`test/markdown.test.js`** — the renderer, read back node by node: every
  block and inline form it claims, the two payloads rendered as text, the
  scheme rule on `href` including `java\tscript:` and a relative path, a fence
  that has not closed yet, and `strip()` for the status box. **Fixture-only** in
  the same way as the two above: it proves which nodes the page is handed, not
  how a browser paints them.
- **`test/compose.test.js`** — the composer's field: the marks as they are
  typed, starting from the bare text node a browser leaves in an empty field;
  Enter and Backspace as pure functions of the markdown and a caret offset into
  it; a paste asked for `text/plain` and nothing else, with the `<img onerror=…>`
  payload coming back as characters and no element of its own in the field; and
  the round trip — a bullet list and a bold word typed, serialised, rendered by
  `markdown.js`, the same words on both sides. **Fixture-only, and one part of
  it more than the rest**: there is no selection in the fake DOM, so where a
  caret *lands* after a redraw is not proved here. The selection in the file is
  a stub that carries a key into the transform.
- **`test/sse.test.js`** — the page's own reader of the event stream: frames
  split across chunks, keepalive comments, `\r\n`, named events.
- **`test/link.test.js`** — the live connection, caused rather than reasoned
  about: a browser holding a stale position opens a session and its first
  request is `?from=0`; a stream that has said nothing past the threshold is
  hung up on and reopened at the last `seq` read; a page parked between retries
  reconnects within a second of coming back; the backoff climbs from nothing to
  its cap and no further; and an ended chat — whose stream ends where its log
  ends — is drawn whole and then **settled**, rather than reconnected to for as
  long as the screen is open, with a send bringing it back at the position the
  page has read to. The clock and the timers are injected in two of them,
  because a test cannot wait fifteen seconds for a keepalive that never comes —
  the drops themselves are real streams, really hung up on.

## Opening, waiting, and coming back

The page is reached from a phone, which means it spends most of its life
backgrounded and most of its connections die without saying so. Four rules,
and `public/link.js` is all four:

- **Opening a session draws its end.** The first connection asks `?last=10` —
  ten *turns*, the host's unit, not ten events — every time: from the list, from
  the remembered session on load, after a resume. The owner, 2026-09-22: *"When
  entering a chat, it loads the entire chat from the beginning. That is not a
  good UX"*, which is what `?from=0` did and what it cost on a chat with a year
  in it. The snapshot leading that answer carries `window_first_seq` and
  `turns_before`; while `turns_before` is greater than zero the thread draws a
  quiet **Earlier messages** marker, and reaching it fetches
  `?before=<window_first_seq>&last=10`, one page that closes itself and is
  prepended without moving what is being read. Those two fields describe *that
  answer* and are never merged into a record. A position is real and still
  needed, but only to catch a *reconnect* up within one page lifetime, so it
  lives in the transcript, in memory, and dies with the page — a reconnect still
  asks `?from=<the highest seq seen>`. Nothing positional is persisted, and
  nor is which chat was open.
- **The page opens on the list, never on a chat** (2026-10-05). A load, a
  reload and the installed app launched from the home screen land on Chats
  with nothing chosen — the list on a phone, the list and *Pick a chat* on the
  desktop — and ask the host for no chat's history until a row is tapped. Until
  then the browser kept the open chat under `loam-ui.session` and reopened it on
  load; the key is no longer written and is dropped on the way past. An address
  is a choice and a remembered chat is not, so a `/files/…` address and the two
  GitHub return paths still open what they name. Inside one page lifetime
  nothing changed: Files and back to Chats on the desktop, *Back to chat* and
  the browser's Back out of Files all return to the chat that was open.
  `test/opens-on-list*.test.js`.
- **Coming back reconnects at once.** `visibilitychange` to visible,
  `pageshow`, `online` and window `focus` all mean the same thing, and all four
  call `wake()`: reopen immediately if the stream is closed or stale, do
  nothing at all if it is healthy. Waiting for a connection's own retry is what
  this replaces — on a phone it can be slow, or not happen until something else
  wakes the page.
- **Stale is measured, not guessed.** Every event and every `: ping` is the
  stream saying it is still there. Silence longer than one ping plus a margin
  is a connection that no longer exists whatever the browser thinks, and the
  page hangs up on it and reopens at the last `seq` it read. The interval is
  not a number in the page: this app's server announces it on the stream's
  opening comment (`: open ping=<ms>`) and the page reads it from there.
- **Waiting is visible.** A status line, and four words for the four things
  that can be happening: `connecting…`, `catching up…`, `live`, `disconnected,
  retrying… (why)`. "Catching up" is the one place `record_seq` is read — not
  as a position, which it never is, but as the answer to whether what is being
  drawn is history or is happening now. Retries are immediate first, then a
  short climb, capped at a few seconds.

## The screens, and where each thing on them comes from

The design is in `design/` and is the source of truth for the look. What
matters here is the rule underneath it: **nothing on a screen is inferred that
the record already states.**

**Where this app departs from the bundle, it is written down.** There is one
such departure and it is the owner's own instruction: the agent's side of a thread
has no bubble, where the bundle draws a surface one. It is described below — "the
agent speaks as a page" — held by a test in `test/style.test.js`, and named in the
code and in the stylesheet at the place it is made. Everything else on a screen is
the bundle's.

- **An agent is an id, a name and a mark, and each belongs to somebody.**
  `GET /v1/agents` answers `{id, name, emoji}`. The id is what starts a session
  and what the page computes the blob's colour and shape from, so renaming an
  agent on the host does not move its colour. The name is the host's to state
  and is what every screen shows. The avatar is the design's blob with a
  Caprasimo initial — one letter, and two when two agents share the first, the
  design's "Sc" for Scout — which is a fact about the *set* and so is worked out
  here, from the whole list. The emoji is read and is deliberately drawn
  nowhere: the design has no place for it, and `null` is an answer rather than a
  gap to fill with a guess.
- **A row leads with the chat's name.** The title is the row's own 17px line
  and the agent's name is the muted one under it, which is the other way round
  from where this started. The owner, 2026-09-22: *"in the chat list, let's have
  the chat Title be the Title of the item and the agent name be the subtitle,
  since the agent can already be identified via the icon."* The blob beside the
  row is the agent — its colour, its shape and its initial all come from the
  agent's id and from nothing else — so leading with the name said the same
  thing twice and left the one line that tells two of Scout's chats apart in
  the smaller type. It is the same row on both layouts, and the agent's profile
  already leads with the title because every chat on that page is the same
  agent's.
- **A row is a record.** `status` is a field — `working`, `waiting`,
  `needs_input`, `done`, `failed`, `idle` — and the page reads it rather than
  guessing from "an event arrived recently", which is what it used to do and
  which could not tell a paused turn from a slow one. `needs_input` outranks
  `working`, because a spinner would hide the one thing a person has to do. An
  ended session draws as **Done**, not as a failure: it has an answer to read and
  its id resumes.
- **Working, waiting and done are three states, and every animated cue reads the
  record.** The owner, 2026-09-25, watching their own row while two background jobs
  ran: *"the dot isn't happening today when the main agent stops and is waiting
  for the background agents … the chat list shows as if you are fully done. Maybe
  we should have a waiting state as well as a separate state?"* The broker now
  says `waiting` — the turn's result is in, nothing is being generated, and a
  background task or its notification is still live — and the page draws it as
  its own case:

  | cue | `working` | `waiting` | `done` / `failed` |
  |---|---|---|---|
  | the chat header's blob | breathes | one steady halo, no animation | the blob alone |
  | the row's time dot | pulsing | pulsing | absent |
  | the count beside it | `background_tasks`, when there is one | the same | absent |
  | the row's kicker | *"WORKING · 2 IN BACKGROUND"* | *"WAITING · 2 IN BACKGROUND"* | *"DONE"* |

  Two things made the old row lie, and both are fixed here. The cues were keyed
  to the clock — a working session silent for two minutes stops breathing, which
  is right for a slow turn and wrong for a session parked on background work — so
  the quiet treatment is `working`'s alone now: a waiting session explains its own
  silence. And **the dot is no longer gated by the seen rule**: reading a chat
  marks it read, so the row of the session they were watching was the minimal row by
  the time they looked at the list. Whether an agent is still going is a fact about
  the machine, like *"still running"* beside it, not a highlight this device
  having looked at a row can clear.

  A status this page has never met reads as **working**, never as done, so a word
  the broker grows can only ever be a spinner that stays a moment too long.
- **A working session that has gone quiet is drawn as quiet.** `last_event_at` is
  the only field that can tell "mid-sentence" from "silent for twenty minutes",
  and a breathing blob would say the wrong one. Past two minutes a **working**
  row stops breathing and says **"quiet for 20 min"** in place of "working" — a
  `waiting` row does neither, because its last event is the result it is parked
  after and its silence is already explained — and the
  thread's status line says the same thing beside the connection's own state —
  a connection that is live says nothing about a turn that has stalled. The
  threshold is the page's; the fact is the record's.
- **The composer floats, and one row inside it speaks only when it must.** The
  transcript fills the screen under the chat's header and the composer sits on
  the frosted ground along the bottom edge, so the last message passes *behind*
  it. The owner, 2026-09-25: *"make the Chat input box and the chat session page
  cleaner looking by having the chat when scrolled through feel like it is going
  behind the input box area without that gap on top of the box."* The scroller is
  padded by the dock's own **measured** height, because the composer is one row
  tall with an empty draft and three times that with a long one — a constant
  would be a gap under every short chat or a last message nobody can reach under
  a long draft.

  Inside it there is at most **one** notice, as the box's first row — 12.5px,
  muted, left to right, with a dot where the state has one (green while
  working, amber or red for a warning) — and most of the time there is none at
  all and the box is its two rows. The owner, the same day: *"the usage of text
  and words can be designed much better and cleaner while still having the same
  functionality."* It was a centred line on the dock's ground **above** the box
  until 2026-09-30, and the transcript scrolling under that ground made it a
  notch stuck to the box (*"It looks like an ugly text notch above the box and
  it doesn't make the text box feel like it is floating above the text when
  scrolling"*); inside the box the composer is one floating object. The row
  grows in and shrinks out over 160ms, and the dock is measured again when it
  has, so the padding under the transcript includes it. It is a ladder and the
  first rung that holds is the whole answer:

  | rung | when | says |
  |---|---|---|
  | a problem | the last thing this page tried did not happen, or the stream is retrying | the failure, in the error colour |
  | the sign-in | `login.days_left <= 3` | *"Claude Code sign-in expires in 1 day · Sign in again"*, amber |
  | the wake cost | an import that has not been forked (`history_bytes`) | *"Your first message loads this conversation's history once, about $12"* |
  | working | the record's status is in flight **and** there is background work or it has gone quiet | *"Working · 2 in background"*, which opens the list |
  | not running | the process is dead and nothing is in flight | *"Not running · your message picks it up"* |

  The ladder is `quietLine()` in `public/sessions.js` and takes facts rather
  than sentences, so it is asserted against records with no screen anywhere near
  it. The problem rung is above the sign-in — which is the one departure from
  what the design asked for, and the reason is that a problem is about the last
  thing a person did and is gone as soon as it clears, where a sign-in warning is
  three days wide and will still be there afterwards.

  Two things that used to be on this strip are **gone on purpose**: the
  connection narrating itself (*"live"*, *"connecting…"*, *"catching up…"* — the
  loading card covers the one moment those were worth reading, and only
  *retrying* is something to act on), and the permanent *"this chat is not
  running — send a message and it picks up where it left off"*.
- **"Not running" is two live facts, and never a state read once.** It used to
  be keyed to a `broker.exit` this stream had ever seen — which the replay of an
  ended chat's log always ends with, and which nothing cleared — so a chat
  resumed by typing into it went on saying it was not running for as long as the
  screen stayed open. The owner, 2026-09-22: *"Even when I am actively in the chat
  and talking to the agent and receiving responses, above the text input box
  still says 'this chat is not running'."*
  It now needs **both** of: the process is not alive (the record's `state`, and
  no exit as the last thing this stream said), and nothing is in flight (the
  record's `status` is in the settled set, and this page has no message of its
  own waiting to be accepted). An exit stops being the last thing the moment
  anything later arrives — a resumed session keeps its old numbering, so its
  first event is the proof — and the resume's own answer says so before that.
  The line the page draws for its own message is what takes the notice off the
  screen the instant Send is pressed.
- **The settled statuses are the ones this page names, so a word it has never
  heard reads as working.** `needs_input`, `done`, `failed` and `idle` are
  listed; `working` and `waiting` are drawn with their own word and everything
  else is drawn as *Working*. `waiting` arrived exactly that way — the broker
  added it between working and done on 2026-09-25 and this page was already
  drawing it as busy — and the host will grow others, where a page that listed
  the *busy* statuses instead would go silent on the day it found a new thing to
  say.
- **A busy session says what it is waiting on.** The broker holds a session at
  `working` or `waiting` for as long as a background task is live, so "Working"
  alone was hiding the reason it was still saying it. The owner, 2026-09-22: *"I can't see
  that a background agent has been dispatched. I want some sort of indicator
  that subtly tells me the number of background agents or tasks that are
  running or the agent is waiting on to finish."* A row's kicker reads
  **"WORKING · 2 IN BACKGROUND"**; in a chat that *is* the notice row inside the
  composer, and tapping it opens the tasks by the descriptions the host gave
  them — the app's own bottom sheet on the phone, a popover on the desktop, each
  description at full width. It was a small dark pill beside the line with a
  260px card behind it until 2026-09-25, and a background task's description is
  a sentence, so the card was cutting off the only thing it had to say. The count
  is `background_tasks` on the record, so a home-screen row can say it with no
  stream open; the names are `broker.background_tasks` on the session's own
  stream. Nothing is polled and the page counts nothing of its own. Nothing at
  all is drawn at zero.
- **The line under a turn is one word until you ask for the rest.** The owner,
  2026-09-22: *"I like the model and information shown under a response from
  the model. I want to add to that how many tokens of context was used, how
  many tokens of context is supported with that session's set up. Also, I want
  the design to be less visible and in my face and more available easily if I
  want it, maybe with one click or something."* So the default is one muted
  11px token at the end of a turn — the model's short name, `claude-opus-5`
  read as **"Opus 5"** — and a tap opens it in place to the whole line: the
  model, the effort if the turn record names one, the context as **"35.3k of 1M
  (3.5%)"**, the tokens in and out, the duration and what the turn cost. A
  second tap closes it. **Collapsed it is the model's name or it is nothing**: a
  turn the host named no model for draws no line at all rather than leading with
  the first figure it happens to have, which is how a turn came to read
  "0 in / 0 out" under a message on 2026-09-23. **And it is only ever under
  something the agent said**: a turn that ended with nothing of the agent's on
  the page — an error before the first word — has its `result` straight after
  the person's message, and draws no line there (2026-09-30). It is a tap and not a hover: every `:hover` in this app
  is behind `@media (hover: hover)` and a phone has none, so a reveal that
  needed a pointer would be a reveal a phone could never get at. The chat's
  latest context reading is also one line in the three-dot sheet, beside what
  the chat runs on, so the whole conversation's headroom is one look away.
- **The context is a hairline along the composer's top edge** (2026-09-30). A
  3px track inside the box, clipped by its radius, **as long as the model's
  window** and filled from the left by the latest turn's `context.used` over its
  `context.window` (2026-10-07; the provider row's `models[].context_window`
  when the turn states none, and the warning point when neither does). It keeps
  growing past the warning point all the way to the window. **The warning point
  only colours it** (Settings → Chat preferences, below; W, clamped to the window): muted
  ink below 75% of W, the quota ring's amber from 75% of W, its red from W on.
  A tap reads *"Context 312k of 1M · warning at 400k"*, and the notice and the
  sheet say the same words. Nothing at all on a chat with no reading, and a
  model change mid-chat rescales the bar with the turn's new window. No mark is
  drawn at W: at 3px it would be a speck. The arithmetic is `contextFill()` in
  `public/transcript.js`, and **it never reads the record's `compact_window`**
  (2026-09-30): the host stopped compacting at 400k — the provider compacts on
  its own only near a full window — so an older host's `compact_window` and a
  newer one's absence draw the same fill.
- **Settings → Chat preferences: "Warn about context at"** (2026-09-30). Two choices: **A
  number**, a stepper in 50k steps from 100k to 1M, 400k by default; or **the
  model's context length**, the turn's `context.window`. This device's, in
  `localStorage` under `loam-ui.context` as `{mode, at}` — the number is kept
  while the window is chosen. `store.contextWarning` in `public/store.js`.
  Nothing compacts at the warning point; it only decides when the page says so.
- **Settings → Chat preferences: "Chat list bubbles"** (2026-10-14). Five switches,
  each with a small bubble sample: **Working**, **Waiting**, **Done**,
  **Needs your input**, and **Failed** (`failed` on the record). Everything
  defaults on. Turning one off hides that state's unread message bubble in
  the home list (including pinned, filtered and archived rows) and the agent's
  chat list. It does not mark a chat read. Avatar rings, animation, error
  badges, live dots and background counts keep their usual rules; so do the
  open chat, its header and notifications. Done is still shown only when
  unread, and Working, Waiting and Done still need a last message to show a
  bubble. Needs your input and Failed can show their label alone.
  This browser stores the switches in `localStorage` under
  `loam-ui.chat-bubbles`, like the context and read-aloud preferences. They
  survive reloads but do not sync across browsers or devices. Syncing would
  need a host preference record and authenticated read/write routes; the web
  app would then read that record instead of the local store. No such broker
  change is part of this setting.
  `npm test` covers defaults, invalid storage, per-state isolation and the
  existing seen/empty rules. The optional real-browser check runs with
  `timeout 90s env LOAM_PLAYWRIGHT=<playwright-module> LOAM_CHROME=<chromium-executable> CHAT_BUBBLES_EVIDENCE=<directory-outside-repository> node test/chat-bubbles-browser.mjs`.
  It checks keyboard switches, focus, touch-sized controls, reload persistence,
  separate-browser defaults and the working thread/header at 1440 and 390 px,
  and saves light/dark desktop and light phone Settings screenshots plus list
  screenshots with Working and Waiting off. Samples carry short preview lines;
  their failure badge sits at the compact avatar's corner, clear of its initials.
- **A tap on the fill, or on the notice row, opens the context sheet**
  (2026-09-30) — a bottom sheet on the phone, a 400px dialog on the desktop —
  holding four things and no more: the numbers (*"312k of 1M · warning at
  400k"*, the tap line's own words),
  two sentences — that nothing compacts until the person asks or the window is
  nearly full, and what compacting does — the last turn's cost from the record (*"last turn
  $0.42"*, the host's `cost_delta_usd`; no line when unknown), and two actions.
  **Compact now** posts `POST /api/sessions/<id>/compact`; on `202` the notice
  row reads *"Compacting…"* until the chat's stream carries `broker.compaction`,
  then *"Compacted · 312k → 2k"* for four seconds. A `409` while a turn is in
  flight reads *"The turn has to end first · then compact"*; any other `409` is
  the host's own sentence. The button is drawn whether or not the record
  carries `compact_window` (it was gated on it until 2026-09-30, and the host
  stops sending it); a broker without the route answers `404`, which takes the
  button away for the rest of the page load and says nothing.
  **Continue in a new chat** opens the New-chat sheet with this chat's agent,
  provider, model and effort chosen, and Start opens a draft whose composer
  already reads *"Continuing from “‹title›”."*, which the person can edit. The
  session is born on the first send as always, and the old chat is untouched.
- **The context notice, once.** The first time a chat's bar is drawn amber or
  red (75% of the warning point or more) in a page load, the notice row reads
  *"Context 312k of 1M · warning at 400k"* for six seconds — when the row has nothing else to
  say, or only "Not running" — and not again for that chat until the page is
  reloaded. No modal, no sound, no haptic.
- **A compaction is a divider in the thread** at its `seq`: *"Compacted · 312k →
  2k"*, or *"Compacted"* when the host could not say, in the turn line's ink and
  size. The CLI's own `compact_boundary` is in the log too and is not drawn. The
  turn record a compaction leaves (its context is `{used: null, window: null}`)
  draws no model line, and until the next real turn the context is the
  compaction's `tokens_after`.
- **Picking up a long, quiet chat says what it re-reads.** Any chat that is not
  working, whose record's `last_event_at` (or `last_activity`, when that is
  missing) is more than an hour ago and whose context holds over 100k tokens,
  reads *"Picking this up re-reads about 312k tokens once."* in the notice row —
  in tokens, since the page cannot see the provider's cache. The imported-chat
  dollar line outranks it; it goes the moment the chat starts working.
  `resumeTokens()` in `public/sessions.js`.
- **Replying to an imported chat costs something, and the page says how much
  first.** A conversation that began in the Claude Code app is forked when it is
  first written into, which sends its whole history to the model once. Where the
  record carries `history_bytes`, the notice row in the composer reads *"Your first
  message loads this conversation's history once, about $12"* — `history_bytes`
  at **$2.55 a megabyte**, a constant named once in `public/sessions.js`,
  rounded to a whole dollar and reading "under $1" below one. It outranks the
  "not running" rung, which would be true and beside the point, and it
  disappears when `history_bytes` comes back `null` on the next snapshot, which
  is the host saying the fork has happened. Nothing about it is on a row.
- **The sign-in is warned about before it runs out, and renewed from the page.**
  The owner, 2026-09-22: *"I want to be able to re login into Claude Code from
  loam-ui. I should also have a notification telling me when I am 3 days away
  from needing to log in again."* When a login expires **every session the host
  starts stops working**, and nothing this app does can fix it. So `GET
  /api/providers` → the provider's `login: {expires_at, days_left, source}` is
  drawn in `--quota-warn` as the top rung of the notice row in the composer
  of every chat on that provider, and as a bar at the top of the Providers list:
  *"Claude Code sign-in expires in 1 day · Sign in again"*, the last words a
  link into that provider's page.
  Past the expiry, or on a wired row the host reports as not connected, the same
  line reads *"has expired"* in the design's error colour. `days_left` is the
  host's number — whole days rounded down, negative once it has gone — and this
  page does no arithmetic on a date. `expires_at` is `null` for some
  credentials, which is "connected, expiry unknown" and is not a warning.

  The provider's own page draws one shared sign-in card from `sign_in`.
  Its `kind` selects a paste field or a device code; `start`, `status`,
  `cancel`, `code` and `sign_out` supply the broker routes. Null means no
  sign-in to offer. The relay maps these `/v1/providers/...` paths to the
  protected `/api/providers/...` routes and allowlists their methods.

  Sign in reserves a new tab during the tap (before the asynchronous POST),
  then opens the returned URL there. If the browser blocks it, Open sign-in
  page remains a normal new-tab link, with Copy beside it. Paste-code draws
  one labelled field and Done only while `accepts_code` is true; a landing
  URL is posted whole as `code`. Device-code draws the host's code and Copy,
  with Done re-reading the state. Both poll only while pending. The card
  never renders the host's `instructions` or documentation links.

  A `200` with `state: failed` displays `message` verbatim and Try again
  starts a new flow. A timed-out code (`409`) also offers a new flow.
  Leaving the provider or closing Settings cancels a pending flow; delayed
  answers cannot reopen it. A result's `login` updates the account and expiry
  immediately, followed by one catalog refresh after success or sign-out.
  No code or flow is persisted in browser storage.

  Success reads **Signed in as ‹login.account›**, or **Signed in** where no
  account is supplied (Claude Code). Expiry notices and quiet Sign in again
  remain; Sign out is present only when `sign_in.sign_out` names its POST
  route. This is currently Antigravity only. Unknown expiry stays unknown.
  Never signed in (`login.source: none`) has no expiry warning.

  The documented descriptor has no button text or identity-provider field.
  The default button therefore says **Sign in**; an optional `sign_in.label`
  supplies provider wording (such as **Sign in with Google**) without page
  changes. The app does not infer Google from a provider ID. See
  [fixture tests and 390/1440 light/dark captures](evidence/provider-sign-in/README.md).
- **Codex, the second provider (2026-10-01).** Its card uses the same component
  with the descriptor's device-code kind. The host names its account and plan,
  with no expiry; the page asks the catalog again after success so Codex is
  choosable. `POST …/login/code` is not used for device-code sign-in.
  **Compact now is not offered on a Codex chat** (`compactsOn()`): the
  broker's compact route answers `409` on one. Its context fill and sheet
  read `context` as they do for Claude.
- **One glyph means "actions on this thing".** The Chats header's second round
  button opens the actions on the *list* — pin, mark all read, the archived
  list — and it draws the three-dot mark the thread's own header already draws
  for the actions on a chat. It read "Edit" in a pill until 2026-09-22;
  the owner: *"at the top of the chats list screen, we need to find a
  representative and meaningful icon to replace the word Edit."* The word was
  also wrong about what the sheet does, which is not editing. Its accessible
  name is "Chat actions".
- **Settings starts with Appearance**, for mode and app size, with separate
  phone and desktop values held on the host once its new settings contract is
  installed. Agents, Providers, Integrations, Chat preferences, Dictation,
  Files, Read aloud and session controls remain available. Account and push
  notifications are omitted. [Appearance contract](appearance.md).
- **A provider's page is the host's reading, and never a page of zeroes.**
  `utilization` is a fraction, so the bar is `0.06 → 6%`; every window in the
  reading is drawn, named or not, because the host copies whatever the provider
  reports; the figure says when it was last seen, because a quota from four
  hours ago is not a current quota; and a provider with no reading yet says so
  in words. `usage: null` is not a full bar and not an empty one. A provider
  that is not wired shows the host's own sentence for why, and there is no
  connect flow: connecting happens on the server. The design also names a
  *plan* — "Claude Pro" — and the page draws one when a provider carries a
  `plan`; the session interface has no such field today, so that line is
  absent rather than guessed at from anything else.
- **The agent speaks as a page; the person speaks from a bubble.** The owner,
  2026-09-25: *"it might make sense for the agent responses in chat to not have
  a bubble and take more advantage of a wider width."* So an agent's words have
  no fill, no radius and no inset: they sit on the ground at the full width of
  the reading column. Prose is held to a **measure of about 70–75 characters**,
  because a line that runs the whole 760px column is a line nobody can follow,
  and a table, a code block and the steps fold are free to run past that measure
  to the edge of the column — which is the half of this that a wider width was
  wanted for. The person's own messages keep the design's right-aligned bubble.
  As of 2026-10-14, that bubble uses the existing warm surface `--c4` with ink
  `--c2`: a quiet step off the chat ground in both themes. Its folded-message
  fade uses the same surface; attachments remain lifted chips, links inherit
  the ink, and selected text uses `--c12` / `--c7`. Pending sends dim to 80%
  opacity to keep body text and inline code readable. Primary buttons retain
  their own colors.

  **This is a deliberate departure from the design bundle**, which draws the
  agent side as a surface bubble with the radius mirrored (`design/README.md`,
  "Message types"; `design/desktop/README.md`, "Messages"). It is the owner's
  instruction over the bundle, and `test/style.test.js` fails if the fill comes
  back by habit. The class both sides share is `.say` — it was `.bubble` until
  that day, and a bubble is now one of the two things it can be.
- **A long message of mine folds.** The owner, the same day: *"my inputs, if they
  are long, should be collapsed to a shorter height with the option to expand."*
  Past six lines a sent message clamps to that height with a soft fade over its
  last line and a **Show more** under it; expanding shows all of it and offers
  **Show less**. The height is one figure with two homes — `--mine-max` in the
  stylesheet, which does the clamping, and `MINE_MAX_PX` in `app.js`, which
  measures against it — and a test fails if they ever disagree. It is the same
  six lines the composer's own draft grows to, so what a person typed in six
  lines reads back in six.

  **Which messages are long is measured, not guessed.** How many lines a message
  takes is its font, its width, its words and its markdown all at once, so the
  page reads the node's height after the draw, exactly as it measures the dock:
  a rule counting characters would clamp on a phone and not on a laptop. The
  markdown inside a clamped message still renders — the clamp is on height, not
  on content — and whether one is open is this browser's, not the record's, since
  it is a way of looking at a message rather than a fact about it.
- **A thread keeps every sentence, and folds the work between them.** Each text
  an agent writes in a turn is a line of its own and stays on the screen for
  good — a phone showed only the last paragraph of a turn before, because the
  page read each `assistant` event as the whole of its message. Everything
  between those sentences is a **step**, and every step is inside one
  collapsible card reading "5 steps" that opens to the rows themselves. A lone
  call folds like any other — "1 step" — which is the owner, 2026-09-22: *"Even
  when there is only one step, the action should be in a collapsable step
  design."*

  **Nothing that is a tool call is drawn in the thread.** The owner, 2026-09-25:
  *"still some of the tool calls are showing outside of the steps drop down and
  in the chat. Please fix."* Three kinds used to be: the call running now, the
  call that failed, and the agent's thinking, which was an italic paragraph of
  its own. All three are steps, and the fold's one line is where each of them is
  said instead:

  - **while the work is going on** the line reads the latest line of the agent's
    own thinking, clipped to one line, with the breathing dot the plan card uses
    — so a closed fold still shows progress. When nothing is running it goes back
    to the count and the tick.
  - **a failure is kept and counted**: after the count, "· 1 failed", in the
    header's own muted ink, no icon and no fill, and never the error colour.
    The owner, 2026-09-25: *"Maybe just have a small and discrete indicator that
    the dropdown includes a failure. It shouldn't be too attracting… it is
    usually not a big deal."* Open the fold and the step itself is there in the
    error colour with what it said on it, without a second tap.
  - **a result whose call is above the window** is a step too, reading "earlier
    step" with its output. It was dropped before, which made it the one thing
    that happened and was on no line at all.
  - **before the agent has drawn anything at all**, there is no fold yet to say
    so, and under the person's newest message one quiet line stands where the
    reply will be: a calm dot and a short phrase — *Thinking…, Digging in…,
    Turning it over…, Working it through…, Gathering the threads…, Finding the
    words…* — changing every 3.2 seconds. The reply's first words, or the fold
    its first step opens, take its place in the same draw. It is not drawn while
    a permission card is asking or after a send that failed. The owner,
    2026-09-30: *"if in working mode but there is no message to show after the
    user message, don't show an old response, just say thinking or something
    fun and dynamic."* Never a spinner; under reduced motion the dot is still.

  Two things in a thread are deliberately not steps. **A permission card stays a
  card**: it is a question to the person, not a line about work. **A plan card
  stays a card** — the design's own transcript draws one and the page draws it
  whole, opening to its steps — so it is the one tool call in this app that is
  not inside a fold, and that is the design's decision rather than an oversight.
  Reading what the agent said is the point of a thread; what it ran is secondary.
- **A chat opens under a card while its window arrives.** The owner, 2026-09-22:
  *"When loading the chat history, show a nicely playful animated loading box
  over the chat and input and show the details once loaded."* The agent's own
  blob, breathing on the design's `loamBreathe` the way a working row does, one
  muted line, and the design's wave under it — over the transcript and the
  composer, never over the header, so the way back out of a chat is a button the
  whole time. It belongs to the chat being *opened*: a reconnect, a snapshot or
  a new turn never brings it back, and there is a guard under it so a broker
  that answers nothing leaves a usable screen rather than a card forever.

  **The scroll happens under the card, and the card is lifted after it.**
  The owner, 2026-09-22: *"when going into a chat, it manually scrolls to the
  last message after the loading animation has finished. Please have it do the
  scrolling before the loading animation ends, so when it shows up everything
  is ready (the desktop view sometimes doesn't scroll)."* So the order is the
  feature — draw the window, put the reader on the last thing they said, and
  only then start the fade — and `test/list.test.js` holds it as an order in
  the DOM's own write log: the scroll is written before the card is marked as
  going and before the card leaves the tree.

  Two things were making the card outlive the scroll. It waited for the
  transcript to reach the record's `record_seq`, and read that number off **the
  list's copy of the record** rather than off the snapshot that leads the
  stream it is reading; and it had no answer at all for a stream that ends
  short of the number its own snapshot states. Either way the chat never
  landed, the eight-second give-up is what eventually took the card away, and
  the reader was left at the wrong end of the conversation with nothing to say
  why. The reading is now the stream's own, and **a stream that has stopped is
  a stream that has said everything**, so the chat lands on it.

  **And the card covers the whole window, not the first part of it.** The owner,
  2026-09-25: *"sometimes when I load a chat, it still shows me the chat messages
  showing like a stream and loading one by one instead of a loading screen and
  showing me the chat once it is fully loaded."* A record is only as new as when
  it was written, and a session with a turn in flight has logged events since —
  so reaching the number it named is not the same as the stream having finished
  talking, and the card was lifting part way through the replay with the rest of
  the history arriving in front of the reader. The page now waits for the stream
  to go **quiet** as well: a replayed burst arrives back to back, so a gap of
  150ms is the window being over, and a cap of 900ms is what gets a person into a
  chat whose live turn will never go quiet. Both openings are held in
  `test/list.test.js` against a broker whose record is two events behind its log
  — one chat still running, one ended — and against the old page both of them
  drew the last two turns with no card over them.

  **Replaced on 2026-09-29: the card lifts once, when the chat is whole and
  placed, and no short timer decides it.** The owner: *"It shows a bit of the
  loading screen, then brings the chat while it's still loading … Then at the
  end it scrolls back up to the last message I sent."* Two causes. The eight
  second give-up lifted the card over a large window that was still arriving
  and did not land, so the landing scroll ran later, in front of them. And the
  reconciler rebuilt the whole transcript the moment the card came off — a
  keyed node leaving stood at the cursor, so no unkeyed sibling after it could
  match — which is a redraw after the scroll. Now: the window is not drawn at
  all until the transcript has reached the `record_seq` the stream's own
  snapshot states (or the stream has stopped); it is then drawn in one draw,
  the reader is put on their last message, and the card lifts. The 150ms quiet
  wait and the 900ms cap are gone. A 20-second last resort remains for a
  stream that never delivers its window; if it fires, it opens the chat the
  same way and writes a `loam:` warning to the browser console. Held in
  `test/list.test.js` as an order in the write log for a large ended chat, a
  running chat with a long replay and a small one.

  **A chat with one long message opens at the top of it, and that is the rule
  working.** Reviewing the deployed app on a 1280px desktop, a chat was seen to
  open "at the very top with the reply below the fold", which could not be
  told from a missed scroll: that chat has exactly one message from the person,
  it is long, and the rule puts its first line at the top. A test now says
  which of the two it is.
- **A provider is a mark, not a letter.** The owner, 2026-09-22: *"instead of
  saying running on Claude Code and having 'c', can we have the anthropic logo
  instead?"* Every tile that named a provider — the 18px badge on a home row
  and on a profile row, the New-chat provider chips, the Providers rows in Settings and
  the hero on one provider's page — draws the vendor's own mark, inline SVG,
  monochrome, taking the ink of the tile it sits in. `public/marks.js` is the
  only place any of them is drawn, and it says where each came from:
  **Anthropic's, OpenAI's and OpenCode's are the published marks, path data
  unchanged** (the simple-icons distribution of each vendor's brand asset);
  **xAI's is a faithful simplified redraw**, because its mark could not be
  fetched from this box. A provider this app has never seen falls back to the
  letter, which is what every tile drew before.
- **The chat header is a ring, and the ring is the subscription.** The owner, the
  same day: *"instead of running on Claude Code when I am in the chat, just show
  a circle version of the logo, with a band around it showing the percentage of
  usage of my subscription. The band should get yellow once I am at 70% and red
  once I am at 90%."* So the header's "Running on Claude Code · Opus" pill is
  gone and in its place is the provider's mark in a circle — 28px on the phone,
  32px on the desktop — with the usage drawn round it. The figure is **the
  highest utilization of every stated window and bucket** from `GET /api/providers`,
  because a person is stopped by whichever runs out first. Below 70% the arc is
  the design's muted ink, from 70% it is `--quota-warn` (a warm amber, named
  rather than numbered: the design bundle has no warning colour, and the band
  above it is the bundle's own error colour `--c13`), and from 90% it is that
  error colour. **No reading yet is an empty track, which is not a ring at 0%**
  — one says nothing is known, the other says nothing is used. Tapping it opens
  **that provider's own page** in Settings — the plan, every window drawn by
  name with what it resets at — and not the list of providers, which was one
  tap short of the answer the mark had already named. The owner, 2026-09-22:
  *"when I click on the Anthropic logo in the chat, it should take me to the
  Anthropic metrics in the provider settings, not just the provider settings
  with the list of providers."* It is the one way into Settings that does not
  re-fetch the providers on the way in: the page it opens has to show the
  number the ring is showing, and a refresh would replace the reading the ring
  is drawn from. The model a chat is set to is still on the model button in the
  composer's second row; the header names neither it nor the provider.
- **The ring moves while the quota is being spent.** The agent CLI volunteers a
  reading mid-turn — `rate_limit_event`, carrying
  `rate_limit_info.unifiedWindows` — and the broker passes it through, so a
  reading arriving on the open thread is put exactly where `GET /api/providers`
  puts one and everything drawn from it redraws. Nothing is fetched again.
  A Providers fetch replaces usage whole: a missing five-hour window stays
  absent. An event only moves windows it names, and only when its reading is
  newer than the held `seen_at`. Timestamped history uses its `ts`; replay
  without a timestamp is ignored, because the opening snapshot's `record_seq`
  identifies history but its `last_event_at` cannot date individual events.
  Previously `openThread()` → `applyRateLimit()` → `usageFromRateLimit()`
  stamped replay with the browser's current time, and `mergeUsage()` could
  resurrect a five-hour window removed by a fresh read.
  `seven_day_<model>` reads "Weekly · <Model>"; `window_<n>_min` reads by
  length; `buckets` follows with each `limit_name` or id. With a week alone,
  the Providers row reads "Prolite · about 5% used this week", and the card
  shows "Weekly · 5% · resets …". `prolite` is capitalised as "Prolite".
  `source: read` says "Last read 2 min ago"; `source: event` says
  "Last seen 2 min ago, from a chat"; absent source keeps the existing
  "Last read 2 min ago". The host's `usage_reason`, when present, appears
  verbatim beside the usage card, including when no reading exists.
- **New content follows the reader only from the very bottom.** The owner,
  2026-09-22: *"please autoscroll when I am fully at the bottom of the page and
  there is new content coming in. Don't autoscroll if I am not fully at the
  bottom."* A delta, a message, a tool row or a snapshot redraw scrolls the
  transcript to its end only when the reader was at the end — within a couple of
  pixels, which is what "fully" means once a browser's sub-pixel scroll
  positions are allowed for. It was 80px, most of a message, so a reader one
  line up was thrown back down. A page of earlier messages goes in *above* what
  is being read, so what is held there is the distance from the bottom and the
  same line stays under the same finger; and a chat landing on the last thing
  the person said is not at the end, which needs no exception because the scroll
  has already moved before the next draw reads it.

  **On the desktop layout none of this happened at all until 2026-09-22, and
  the cause was one selector.** Two columns scroll and both are on the screen
  at once. The draw reads the pane before it writes and puts it back after,
  and the two halves asked for it differently: the first asked for `.msgs` and
  fell back to `.scroll`, the second asked for `.msgs, .scroll` in one
  selector — which answers the **list column**, because that is first in the
  document. So the second half found a pane that was not the one it had
  measured, gave up, and wrote nothing. Both halves ask one function now.
- **Rename, archive and end are three different acts.** A rename writes `title`
  and outranks the host's titler, which will not overwrite it; an empty name
  puts the opening words back. Archiving only puts a chat away — **it does not
  end the process**, so an archived chat that is still running says so and the
  host's own note about the cap is shown. Ending stops the process, asks once
  first, and loses nothing: the id resumes with its context.
- **Pin, archive, title and seen are on the record, not in this browser.** A
  pin made on the phone is a pin on the laptop; a `PATCH` writes it and the
  live list stream tells every other device. Seen is on the record too, since
  2026-09-22 — one person, one box, one broker, so `seen_seq` against
  `record_seq` is the unified answer and this browser keeps no reading of its
  own.
- **Every state is shown until the row is seen, and a seen row is the minimal
  row.** That is one rule and it has its own section below.
- **The live list is one stream and no polling.** `GET /api/sessions/events`
  sends the same `broker.session` snapshot the per-session stream leads with,
  whenever any session's record changes. It has **no `?from=`** and must not:
  a snapshot is not an event, has no `seq`, and reopening the stream re-sends
  the whole list, which is by definition current.
- **Agent colour and shape are computed here, from the name.** The broker has
  no opinion about colour and should not be asked for one; the same agent is
  the same blob on every device and after every restart.
- **The refusals shown are the host's own sentences** — over the session cap,
  or for a provider that is not wired. The page writes none of its own.

## The desktop layout, and where it begins

At **1100px and above** the page draws the desktop handoff's three columns —
rail 80 · list column 372 · main pane — and below it the phone's screens stand
exactly as they were. The two are not two apps: the rows are `sessionRow()`,
the transcript is `threadLines()`, the composer is `composer()`, the list is
`listBody()`, and the reconciler is the one in `ui.js`. What differs is the
frame around them and the chat's own header.

- **Which layout is drawn is read from the window on every draw**, through
  `matchMedia` where there is one and `innerWidth` where there is not. A window
  dragged across the threshold is one redraw and no listener that has to
  remember which side it was on.
- **The rail** carries the brand, **Chats**, and **Files** — present, disabled
  and saying "later" in its own tooltip, because Files is out of scope and a
  person told "later" stops looking for it. At the foot, Settings and the
  account tile, both opening the Settings dialog. The account tile carries a
  glyph rather than the design's "Y": that letter is a person's initial and this
  app has no account to read one from, so it would have had to be invented.
- **The list column** is the phone's list under its own header — Search, the
  list's actions, and the button that hides the column. The chat the pane is showing is the
  highlighted row, which is a class a phone never has occasion to draw.
- **Hiding the column is remembered on this device**, unlike a pin: it is how one screen is being looked at, not something true of
  the sessions. With it hidden the way back sits in the pane and the headers
  carry a spacer so nothing of theirs is underneath it.
- **The chat header renames in place**, and so does the strip under the
  phone's own header — same field, same two functions, so the two layouts
  cannot drift apart on what a rename means. Click or tap the title and it
  becomes a field: Enter or blur saves the trimmed name, Esc cancels, and **an
  empty name keeps the old title** — clearing a title back to the opening words
  is `{"title": null}`, which is a different thing to ask for and is asked for
  in the Rename sheet, which is still there on both layouts. A name that says
  what the title already says is not a `PATCH`. The list column updates because
  the host answers the record and the stream carries it, not because the header
  tells the row anything.
- **The context pill is the record's provider and the record's model** —
  "Running on Claude Code · Opus", and the provider alone while the chat is on
  the agent's own default. The workspace is its tooltip. On the phone the same
  line is the left half of the strip under the header. Messages, the composer
  and the hint line share one centred 760px column.
- **Dialogs replace the bottom sheets**, and the card inside each is the same
  card: a wrapper and a few rules, not a second set of screens. **Every one of
  them closes on Escape and on the space around it.** Found on the deployed app
  at 1280px, 2026-09-22: the New-chat dialog could not be dismissed with Escape
  and shows no close control, so a click outside was the only way out of it. The key is
  bound once, on the document, because a dialog is not focused — the composer's
  popover goes first when one is open, because it is the thing on top, and a
  key pressed with nothing open is left alone. The phone's sheets are the same
  sheets and answer the same key. Settings is the
  handoff's two-pane dialog — Agents, Providers, Theme on the left, the same
  page bodies on the right, without the back button a phone needs to leave
  them.
- **The agent's page opens in the main pane**, with "Back to chat" returning to
  the chat it was opened from, the list column still beside it. Setup and
  Schedule are disabled there as they are on the phone.

- **The composer's menus are the design's popover**, anchored above row two and
  closing on a choice, on a click outside them, or on the button they came from
  again. See the section below: they are the phone's sheets in a second chrome,
  from one builder.

What is **not** built, and is named here rather than left to be discovered:
"Pick from Files", which is drawn and disabled because the app's own file store
is out of scope; the **Files** tab and everything under it; and Account,
Applications and push notifications, which are out of scope by the owner's own
note at the top of `design/desktop/README.md`.

The desktop layout is driven in `test/desktop.test.js`, which is the same page
and the same recorded broker as `test/app.test.js` in a 1440px window — the
page has one module instance, so the window's width is a process and not a
variable. What the column owes the pane is driven there too: **the open chat's
row keeps its fill** through a record arriving on the live list and through a
page of history landing in the pane, because which row is filled is answered on
every draw from what is open and not written once at the click; and a **pinned
chat is in the Pinned card and nowhere else**, open or not, through the write
and through the snapshot that follows it. Both are **fixture-only**:
`test/fakedom.js` has no layout and no stylesheet, so what is proved is what the
page does, never what a screen paints. The selected row takes a faint wash of
its avatar's hue and a 4px rounded bar at its right edge, centred vertically
and 35% of the row's height (28px on a compact 80px row). The marker grows
with preview cards; a ResizeObserver measures only selected rows so its height
can ease over 180ms even when the row's automatic height changes. The marker
fades over the same interval when selection moves, without shifting row content.
There is no elevation shadow. The wash uses relative OKLCH colour, retaining
the agent's hue and lightness with chroma .16 at 12% opacity so it reads as a
colour over the warm grounds in both themes. Selected Done previews use their
usual `--c4` surface, including inside Pinned, to keep the card readable.
The treatment is the same in Recent, Pinned and an agent's chat list;
on the agent's page it marks the chat that **Back to chat** returns to. Fill
and keyboard focus ease over 180ms, with no transition under reduced motion. Hover
stays quieter; keyboard focus has its own soft halo and a system outline under
forced colors. The empty circle remains the desktop multi-select control:
hover or selection mode reveals it, opening a chat alone does not.

`test/style.test.js` checks the selection style. The optional real-browser
driver `test/selected-row-browser.mjs` uses a fixture broker to check Recent,
Pinned, plain and Done rows, proportional marker growth and its intermediate
animation height, metadata clearance at 1280/1100px, avatar rings, hover, multi-select, Tab focus, the agent
list, distinct agent hues in both themes, Settings state samples, forced colors,
reduced motion and the phone layout. With Playwright installed separately, run:

```sh
SELECTED_ROW_EVIDENCE=/tmp/selected-row-evidence node test/selected-row-browser.mjs
```

`LOAM_PLAYWRIGHT` can name its module and `LOAM_CHROME` a browser executable.
The output directory must be outside this repository. It saves light/dark
1440px screenshots, a 390px phone screenshot and measured styles in JSON.
`SELECTED_ROW_AGENTS` can supply two broker-shaped agent objects as JSON for
the visual fixtures. Settings → Chat preferences previews reuse the row's view, avatar
and status-card builders: Working and Waiting carry their own labels and live
dots, Done carries its ring, and the label and card colours match the list.

## Seen and unseen, which is the whole of what a row shows

The design draws four states on a home-screen row — Working, Needs your input,
Failed, Done — and one line about when they go: *"tapping a session opens the
thread and clears its unread/state highlight."* Read together with the design's
plain row, that is **one rule**, and the owner stated it again on 2026-09-20 after
a screen where every row carried its box for good:

> **Every state is shown until the row is seen, and a seen row is the minimal
> row.**

- An **unseen** row is the design's state, whole. Working: the breathing blob
  and the pulsing dot. Needs your input: the primary-filled box quoting the
  question, and the ring. Failed: the failed box, the ring, the mark on the
  avatar. Done: the Done box quoting `last_message`, and the agent-colour ring.
- A **seen** row is the avatar without its ring, the chat's title, the agent's
  name under it, the time and the provider mark. **No message text anywhere on it** —
  no box, and no muted last line either. That is the design's plain row.
- There is no separate unread dot and the design has never had one. **The state
  is the mark.** A row with something to say says it; a row that has been looked
  at is the plain row underneath, and the Unread chip counts them.

**A row is unseen when `seen_seq < record_seq`, and both numbers are on the
record.** `record_seq` is the number of the last event in the session's log — a
turn's result, a permission request, a failure, a turn starting are all events,
so all of them move it, and the page never has to enumerate them or ask what
kind of change it was. `seen_seq` is how far it has been read.

**Unread lives on the host, not in this browser.** The owner, 2026-09-22: *"When I
open the website in a new browser, all of my chats become unseen again. The
status of the chats should be saved on the server so it has a unified state."*
So the page keeps no `localStorage` reading of what has been read, any more than
it keeps its own copy of a title or a pin. After the transcript is drawn and
its loading card has gone, the page sends `PATCH /api/sessions/<id>` with
`{"seen_seq": …}` using the highest event sequence it actually received and
drew. Opening alone sends nothing: the snapshot's `record_seq` is never a read
position. A hidden tab advances nothing. Further draws advance the mark at
most once every three seconds, and **leaving flushes
whatever the throttle has not sent yet** — the back button, another chat, the
tab hidden, the page going away — because a mark still in a timer when the list
is drawn is a row drawn unread that was just read. `public/seen.js` is that
throttle and its flush, and `test/seen.test.js` drives it with a clock and
timers it moves by hand. **The throttle never sends on its own**: its timer is
set only when an event newer than the last mark has arrived, so a chat sitting
open with nothing arriving sends nothing, and a `PATCH` every three seconds is a
stream still producing events — a turn, or work in the background — at the
throttle's rate (`test/seen-quiet.test.js` counts the fake host's `PATCH`es over
a quiet ten seconds). Each one is answered with the whole record, which on a
long chat is mostly `turns`. "Mark all as read" is `POST /api/sessions/seen`, which
is the host's own route and answers how many rows moved. Both come back on the
live list stream as a changed record, so the page applies the snapshot rather
than patching its own copy.

**An incomplete load stays unread.** If the opening wait gives up after 20
seconds, or the stream disconnects before the snapshot's end, the partial chat
says “Couldn't load the rest of this chat” with **Retry**. A clean EOF on an
ended session only settles after its final event has arrived. Automatic
reconnection continues; Retry immediately replaces the connection at the last
complete event, and the warning clears when the transcript catches up. Bytes
of an unfinished event count as connection liveness, so a large frame arriving
over a slow link is not repeatedly discarded by the watchdog. The opening
deadline remains bounded even if the relay keeps sending pings. Tool-result
image blocks, including the broker's `omitted_chars` stubs, read as `[image]`
beside their text rather than transport JSON.

`test/chat-load.test.js` covers leaving under the loading card and a short EOF
followed by Retry. The optional Chromium phone regression generates a 17 MB
image event, throttles download to 32 KiB/s, checks the warning and unread row
after leaving, then retries a short stream and renders image stubs:

```sh
timeout 110s env LOAM_PLAYWRIGHT=<playwright-module> LOAM_CHROME=<chromium-executable> CHAT_LOAD_EVIDENCE=<directory-outside-repository> node test/chat-load-browser.mjs
```

This uses synthetic conversations through the actual relay, not a live agent.

**Mark unread** (selection on the list, `public/select.js`) sends
`{"seen_seq": record_seq − 1, "unread": true}` — the flag is what tells the host
this lower mark is on purpose, and a host without it answers the record
unchanged, which the page says plainly. **A chat marked unread stays unread
until it is opened again**, and opening again means a click on its row: on the
desktop it may be the chat in the pane beside the list, whose next event would
otherwise mark it read a moment later, so `seen.hold()` stops every mark for it
— events, the throttle's timer, leaving it — until `seen.open()`. The window
regaining focus is not opening it. "Mark all as read" still marks everything,
held chats included.

The Unread chip and the **Needs OK** chip read the same fact — Needs OK is the
unseen rows whose `status` is `needs_input`. The card itself is in the thread,
and **the thread is unaffected by any of this**: a pending permission is always
drawn inside the chat, seen row or not.

It was a timestamp until 2026-09-20 and compared `last_event_at`, which is a
different question — the host bumps a record for its own housekeeping, and a
clock cannot tell what moved. Then it was this device's own `record_seq` in
`localStorage` until 2026-09-22. A browser still carrying either is read as
having seen nothing, and `loam-ui.reads` is dropped on the way past.

### The ring is a node, and a redraw is not owed a frame

The owner, on a phone, 2026-09-21: *"when I mark all as read, the border line
around the icon stays until I refresh the page"*. Two things were wrong at once,
and both are the kind of thing that cannot happen in `node --test` — the page's
own fixture cleared the ring correctly both before and after this change, which
is exactly why both are written down here rather than only in a test.

- **The ring is its own element now.** It was a `box-shadow` among the blob's
  other inline declarations, so "this row still has something to say" was one
  declaration inside a style the reconciler carries across wholesale, while
  the status box beside it was a node that simply went. A mark that is a node
  goes the way the box goes. Every part of the avatar — the two breathing
  copies, the ring, the blob, the failure mark — now carries a `key` as well, so
  a part that appears or disappears cannot be patched into the part beside it,
  which is what unkeyed siblings of the same tag do.
- **A redraw is raced against a timer.** The page draws whole screens, so it
  coalesces changes until the next animation frame. It used to set a flag, ask
  for a frame, and clear the flag in the callback — and **a callback that never
  runs leaves that flag set for the life of the page**: every later redraw, for
  anything at all, returns at its first line and the screen is frozen until it
  is reloaded. A tab in the background, a screen that goes off, a page restored
  from the back/forward cache: a phone swallows frames as a matter of course.
  `schedule()` now asks for a frame *and* sets a 250 ms timer, and whichever
  arrives first draws, once. `test/frame.test.js` loads the whole page against a
  browser whose `requestAnimationFrame` never calls back at all and asks whether
  the screen still arrives.

## The composer: two rows, and what Enter does

**Enter makes a new line. It never sends.** The owner, 2026-09-22: "When I press
enter on keyboard it should go newline and not send message. On desktop we can
have enter with another key send the message". A composer whose Enter sends is
one you cannot write a paragraph in, and on a phone — where the on-screen
return key is the only thing between two thoughts — it is a message sent
half-written.

So sending is a deliberate act and there are exactly two of them:

- **the send button**, on the right of row two, which is the whole of it on a
  phone. It is always drawn and is *disabled* until there is something to send,
  rather than appearing and disappearing with the draft: a button you have to
  discover is not a way to send a message.
- **Ctrl+↩ or ⌘↩**, which both work on either platform. The desktop's hint line
  under the box names the one your machine prints — `⌘↩ to send` on a Mac,
  `Ctrl+↩ to send` anywhere else.

Enter on its own, with Shift, with Alt, or ending an input method's composition
is a new line in every case — or the next item of the list it is pressed in, see
the field below. What it is never is a send. `public/keys.js` is that decision
and nothing else — an event's fields in, a boolean out, no DOM — and
`test/keys.test.js` asserts every case of it without a keyboard anywhere near
the box this is built on.

**The box is one rounded rectangle of two rows**, on both layouts. The owner, the
same day: "the text box should have two rows. Row one is the text input. Row 2
is all the actions. The box design should be a rectangle with rounded edges, not
half circles." So:

- **Row one** is the field, the full width of the box, and it **shows the
  markdown it is holding** — see "The composer writes markdown" below. It grows
  from one line to six at the design's 16px type and then scrolls inside itself.
  The ceiling is `DRAFT_MAX_PX` in `public/app.js` and `max-height` in the
  stylesheet, and `test/style.test.js` holds the two numbers together — a box
  grown past what CSS will draw is a box that clips what is being typed.
- **The expand glyph sits in row one's top-right corner**, on the field it
  expands (the owner, 2026-09-25, moving it off row two: *"put in a more
  intelligent and UX strategic place"*). It shows while the field has the caret
  or holds text and is faded out and untappable otherwise, so an empty composer
  at rest is clean; faded rather than removed, so it never reflows the text,
  and the field keeps its right padding clear of it. A press on it does not
  blur the field.
- **Row two** is the actions: the + and the **model button** on the left, the mic and the send button on the right, at the
  design's 40px, with the gap between them a node rather than a
  `justify-content`, so a button added to either side lands beside its own
  group. It is **one horizontal row at every width**, and its class is
  `composer-actions`.

  The owner, 2026-09-22: "Please have the microphone icon to the right and
  instead have a model button next to the plus that opens the model and effort
  options." The model button is a pill naming what this chat runs as — the
  record's model, or "Agent's default" when the record names none — and it
  opens the THIS CHAT menu the + used to hold. See "The + menu and the model
  button" below.

  The class is the point. It was `actions` until 2026-09-22, which is also the
  class the action sheet gives its card — and that rule, further down the same
  stylesheet, is `flex-direction: column`. Later rule, same class, so on a phone
  at 390px the +, the mic and the send stacked vertically in a block about 150px
  tall and the composer filled a third of the screen. There was no media query
  to delete: this stylesheet has exactly one width query, `min-width: 1100px`,
  and no `max-width` query at all. `test/style.test.js` now holds the two
  classes apart and refuses a column direction on row two at any width, and
  `test/app.test.js` asserts all three buttons are children of that one row.
- **The attachment chips are inside the box**, above row one, because the files
  belong to the message being written and not to the screen behind it.
- **A take is drawn in row one itself**, in the field's own footprint, and row
  two goes quiet under it — see "The mic, and what it does with what you say".
  There is no chip or card above the box.

The radius is 18px — the design's own card radius, which the rest of the page is
made of — and not the 999px pill it replaces. Everything else the design bundle
settles about the composer is unchanged: its tokens, its type, its spacing, the
lifted fill and the 1px border. The box has **no fill of its own on the strip it
sits on**: it floats on the dock's frosted ground, and an opaque rectangle over
the blur would be a hard edge where the design has a soft one.

### The draft, full screen

The owner, 2026-09-25: *"Have an option in the text box to open a bigger full
screen view to work on the message that makes it easier to work on longer
prompts."* Row one's six-line ceiling is right for a question and wrong for a
brief, so the quiet expand glyph in the field's top-right corner opens the draft on a screen of its
own: a header with the way out, the field with no ceiling and the page's measure
round it, and a send.

**There is one draft and this is a second view of it, not a second value.** Every
keystroke in the big field writes straight through to the composer's own and
parks it, so `submitDraft()` — which reads the composer and knows nothing about
this screen — sends the same text whichever of the two it was typed in, and a
phone that dies with the editor open reopens with the words in the composer.
Done, Escape and the back chevron all do the same thing and none of them
discards anything. It is the same rich field, so the markdown is drawn as it is
typed exactly as row one draws it.

**Attachments and the model belong to the chat, not to the editor**, so the
editor is text only — no chips, no +, no pill. A send from it is the chat's send,
files and all, and the screen goes with it so a person lands back in the
conversation their message has just joined.

One thing a phone still has to confirm: the hardware **back** button is the
browser's, and this app keeps no history entries, so back leaves the page rather
than closing the editor. The chevron in the editor's own header and Escape are
what close it.

## The composer writes markdown

The owner, 2026-09-22: *"please make sure bullet points and numbered lists render
as MD in the text input"* and, asked where they meant, *"I meant both in the input
box and my messages after sent."* The bubble half was already true — a person's
message goes through the same `markdown()` the agent's does. This is row one.

It is a `contenteditable` region and not a `<textarea>`, because a textarea can
only ever show its own characters. A small, fixed set of marks is drawn as it is
typed and nothing else is:

- a line starting with `- ` or `* ` is a bullet, `1. ` a numbered item;
- Enter inside a list continues it, carrying the tail of the line into the new
  item; Enter on an item with nothing in it ends the list and leaves a line of
  plain text behind;
- Backspace at the start of an item turns that item back into a line of text;
- `**bold**` and `` `code` `` render the moment they close, and are their own
  characters until then — the same rule `markdown.js` applies to a message still
  being written;
- everything else is text. There are no headings, no links, no nesting: a
  backtick inside a `**` run is a backtick.

**The markdown is the truth and the drawing is a drawing of it.** The field is
read back as markdown (`readField`), every transform is a pure function of that
string and a caret offset into it (`enterAt`, `backspaceAt`, `insertAt`), and
the field is then rebuilt from the string (`setMarkdown`). Nothing parses HTML:
not the tree the browser made — it is re-derived, never interpreted — and not
the clipboard, which is asked for `text/plain` and nothing else and pasted as
characters at the caret. So the `<img src=x onerror=…>` payload is characters in
the field, characters in what is sent, and characters in the bubble, and
`test/compose.test.js` asserts no element of its own exists anywhere in the
field.

**What is sent is that markdown**, which is what makes the two halves one thing:
type a bullet list and a bold word, serialise the field, render the result with
`public/markdown.js`, and you get the list and the bold word back. That round
trip is a test.

Two consequences worth stating rather than discovering:

- **`* milk` is sent as `- milk`**, and `5.` as the number the item actually is.
  A `<ul>` does not remember which character made it and an `<ol>` draws its own
  numbers, so the marks are re-derived from the list rather than remembered
  beside it. Same list to every reader of markdown there is.
- **The redraw happens only when the drawing and the markdown disagree**, which
  is exactly when a mark has just closed or a list has just begun. Ordinary
  typing leaves the tree — and with it the caret, the undo stack and any input
  method's composition — alone, and nothing is redrawn at all while a
  composition is in flight.

Everything outside `public/compose.js` still talks to row one as the textarea it
was: `draft.value` is the markdown it holds and `draft.placeholder` is what it
says when empty, both accessors on the node, so the per-chat drafts, dictation
and the send are unchanged code. Row one carries a reconciler `key` now, which
the textarea did not need: it is a `div` among `div`s, and an unkeyed node is
matched by position and tag — so the chip strip appearing above it matched the
field and patched it into an attachment strip.

**What a fake DOM cannot prove**, and is not claimed: where the caret lands
after the field is rebuilt. The arithmetic under it is pure and is tested
(`visibleCaret`); the placing of it is a `Range` on a real selection, and there
is no selection in the fixture. The first things to try in a real browser are
the two that depend on it: typing `- ` mid-draft on a phone, and an Android
keyboard's word completion inside a list item, where the composition events are
the browser's own.

## The mic, and what it does with what you say

The mic has two ways of working, and **which one it is, is decided in one
function**, `dictationMode()` in `public/app.js`:

- **Record and clean**, the default: recorded whole and cleaned up by Gemini.
  Described below.
- **Live**: the browser's own speech recognition, the words appearing in the
  field as you speak. Described under "The browser's own transcriber" further
  down.

**Either way, the field is the take.** The owner, 2026-09-29: "When I'm doing
audio input, I'm not going to be typing at the same time, so the input box
should become the audio UI. It should just have a check mark or an X to
discard. If I hit the check mark, it should just insert the text into the input
box, and then I do the normal submit." So pressing the mic turns row one of the
composer into the take — the same rounded box, the same footprint — with a
clock on the left, the take in the middle and two 32px round controls on the
right: **✕** (Discard) and **✓** (Keep). Row two goes quiet: the +, the model
button and the mic disabled at the design's disabled strength, and the send
hidden, so ✕ and ✓ are the only live controls. **Escape is ✕** on a keyboard.
Whatever was typed is hidden under the take, never changed by it, and is what
the field holds again after ✕ at any point. ✓ puts the words into the field
**at the caret where the mic was pressed** (the end if there was none), joined
by one space to the text either side where one is needed — never two, and never
a trailing line break — with the caret after them and the send ready. Then you
edit and send as you always do; nothing sends by itself. The full-screen
editor has the same mic in its foot, and a take there stands in the editor's
own field and lands at that caret.

Which one is a person's choice, in **Settings → Dictation**. A choice the
browser cannot honour gives way to the one it can — a browser that cannot
record (`MediaRecorder` and `getUserMedia`) uses the recogniser whatever was
chosen — and a browser with neither gets no mic. The mode is read at every
press, so a change takes effect on the next press without a reload; a take or a
listen already under way finishes the way it started.

### Settings → Integrations: GitHub

GitHub is the first integration: the host registers a GitHub app through
GitHub's manifest flow and holds the app, its installation and the user's
authorization, which it uses for new project repos and the engineers' pushes.
This page shows the state (`GET /v1/integrations/github`) and drives the flow.
**Nothing is pasted anywhere.** The model is `public/integrations.js`; the
card is `githubCard()` in `public/app.js`.

- **The card**, in the order the state gains them: GitHub's mark and name;
  *Not set up*, or *Registered as ‹slug›* (a link to the app on GitHub),
  *Installed on ‹account›, all repositories* (or how many, when the host
  counts them), *Signed in as ‹login›*; one quiet line — *token renews itself ·
  authorization good until ‹date›*, or the host's `health.last_error` in the
  warning ink; what Loam uses it for; and only the pills that apply: *Set up on
  GitHub*, *Install*, *Reconnect*, *Refresh*, *Disconnect*. Reconnect is also
  offered on a connected card that is unhealthy or whose authorization ends
  within 14 days, because reinstalling is what fixes both.
- **Set up on GitHub** asks the broker for `{manifest, post_url, state}` and
  submits a hidden form — the manifest in a field named `manifest` — to
  `post_url`, which must be `https://github.com/…`; the policy above allows no
  other target. GitHub sends the browser back to
  **`/integrations/github/registered?code&state`**, which the server answers
  with the page; the page posts both to `register`, puts the address bar back
  to `/`, and opens Settings on Integrations at *Registered*. The register
  answer's `install.url` is used as it is if Install is tapped in the same
  visit.
- **Install** and **Reconnect** ask `install` for `{url, state}` and send the
  browser there. GitHub comes back to
  **`/integrations/github/installed?code&installation_id&setup_action&state`**,
  which goes to `callback` the same way.
- **A refusal** — a stale code, a `state` the broker did not issue (`400`),
  "register first" (`409`), GitHub refusing (`502`) — is the broker's
  sentence on the card in the warning ink, and the state is read again so the
  pills under it are the right ones to retry with.
- **Refresh** posts `refresh` and redraws from its answer. **Disconnect** asks
  once, `DELETE`s, and ends on the three GitHub links the broker returns —
  uninstall, revoke, delete the app — which only the person can follow.
- **The warning**: an `unhealthy` state, or a refresh token within 14 days of
  its end, puts *GitHub needs reconnecting · Settings* in the notice row above a
  chat's composer (under the provider sign-in's rung) and a dot on the
  Integrations tab. The Providers tab takes the same dot when a sign-in is
  warning.
- A broker from before the integration answers `404`, and the card says *This
  broker does not offer GitHub yet.*
- **An app an organization owns** (2026-10-06). Beside the first app the host
  can hold one private app per GitHub organization, answering for that
  organization's repositories alone; the state carries them as `org_apps:
  [{org, status, app, installed, user, health}]`. Each is **a card of its
  own** under the first app's, titled *GitHub · ‹org›* with one word for where
  it is (*Created, not installed yet*, *Installed, not authorized yet*,
  *Connected*, *Needs reconnecting*), its app linked, *Installed on ‹org›,
  selected repositories*, *Authorized by ‹login›*, its own date or its own
  `health.last_error`, and one pill: *Install* (`install` with `{org}`) until
  it is installed, *Reconnect* when it is unhealthy, unauthorized or within 14
  days of its authorization's end. Its warning is the notice row's too, as
  *GitHub for ‹org› needs reconnecting*. Refresh and Disconnect stay on the
  first card, because the host's refresh checks every app and its forget
  forgets every app: with an org app present the pill reads **Disconnect
  all**, the question says the organization's app goes too, and the last step
  adds the org app's own page on GitHub. There is no per-organization forget
  on the broker's routes, so none here.
- **Connect an organization** is a card under the others, on whatever the
  first app's state: the organization's login and an optional app name, then
  `manifest` with `{org, name?}` and the same hidden form, which must target
  **that organization's** create page (`/organizations/‹org›/settings/apps/new`)
  and is otherwise refused before anything is posted. It is drawn only when the
  state has an `org_apps` key: a broker from before ignores `org` and answers
  the first app's manifest, and creating that would replace the first app. The
  return is the same `/integrations/github/registered`; the register answer
  says nothing of which app it was, so the page reads the slug out of its
  `install.url` and, if it is an organization's, that card offers the address
  with *Created on GitHub. Install it on ‹org› next.*

The return paths sit behind the login in front of the app like every page, so
that login has to carry the query string across a sign-in for a return to
work when the session has lapsed; that is the proxy's behaviour, not this
app's.

### Settings → Dictation

Four controls, all of them **this device's** — kept in `localStorage` under
`loam-ui.dictation` beside the drafts (`store.dictation` in
`public/store.js`, read whole and cleaned, so a bad value is a default and not
a broken mic). A phone and a laptop have different microphones, so nothing
here is on the broker's record.

| control | is | default | read by |
|---|---|---|---|
| **Mode** | Record and clean, or Live | Record and clean | `dictationMode()`, at each press |
| **Polish** | Off, Light or Full, with one line under it saying what the chosen level does | Light | `transcribeTake()`, sent with the take as the form's `polish` field. Record and clean only — Live has no server pass. [Polish](#polish-a-second-pass-over-the-words). |
| **Longest recording** | a − / + stepper, whole minutes from 1 to 15 | 5 min | `startRecording()`, at the press: the cap, the clock, the 30-second warning and its pulses all follow it for that take |
| **Microphone** | System default, or one of the browser's audio inputs | System default | `openMicrophone()`: `getUserMedia({audio: {deviceId: {exact}}})`. A chosen device that has gone falls back to the system default rather than failing the take. Record and clean only — the browser's recogniser has no device choice. |

A fourth card, **Names and terms**, is not this device's and has nothing to
edit: the terms live in one note in the vault, which the Obsidian plugin reads
too (below, "The terms note"). It has three states (`termsCard()` in
`public/app.js`):

- **Connected.** The note's path — a link that opens Obsidian when the host
  sends an `obsidian_url`, plain text when it does not — then "11 terms · read
  under a minute ago", and one line: *Edit the note in Obsidian; every take
  reads it fresh.* The terms are read afresh every time the page opens.
- **Not connected.** The host answers `404` (it has no such route yet, or no
  note) or `405` (older still): *The vault's terms are not connected on this
  app yet.* Not drawn as a fault.
- **Unreadable.** Anything else that fails is said as a warning in the card,
  and a path and count read earlier in the page load stay above it.

Until 2026-09-30 this card was a list typed on each device, and the lists
drifted. **What is left of a device's old list** is shown under the card, in
any state, as **Saved on this device before**, with **Copy** (one to a line,
ready to paste into the note) and **Forget** (drops it from this device's
storage). Nothing reads it any more — no take sends it — and it is shown until
it is forgotten.

Before the page has been allowed the microphone, a browser lists its inputs
without names (Chrome lists one with no id at all), so the Microphone card shows
only System default and an **Allow the microphone** button, which asks once,
lets go at once, and lists them again by name. `default` and `communications`
are Chrome's aliases for the system's pick and are not listed twice.

### Recorded whole, and cleaned up by Gemini

Press the mic and the whole take is recorded; stop it and it goes, once, to
this app's server, which sends it to Gemini's transcription model and hands
back the words — with the ums, false starts and "wait, no" taken out, and
nothing else changed. They land in the field.

**Where your voice goes.** The browser records the take into memory. When it
stops, the page posts the bytes to this app's `POST /api/transcribe`; the
server sends them to Google's Gemini API under `GEMINI_API_KEY` and answers
the text. Nothing is written to disk anywhere in this app, nothing goes to the
broker or the agent, and the key never reaches the browser. What reaches the
agent is the text, when you send the message it landed in.

**The states, in order** (`takeSurface()` in `public/app.js`):

1. **Recording.** The field shows the clock where the placeholder would be —
   `0:42`, the muted 13px — and across the middle the microphone's loudness:
   the design's `loamWave` bars made live, one per tenth of a second
   (`LEVEL_BARS`, 96, sampled through an `AnalyserNode`), newest on the right,
   centred on the field's middle line, in the primary ink at reduced strength.
   Silence is a row of low bars, not an empty field. A phone's narrower field
   shows the newest and cuts the oldest off the left.
2. **Nearly out of time.** Thirty seconds before the cap (`RECORD_WARN_MS`)
   the clock and the bars turn to the warning amber (`--quota-warn`, the same
   amber as the quota ring; the design has no warning colour of its own), and
   the phone gives two firm pulses (`HAPTICS.closing`, below).
3. **The cap** — at the longest recording set in Settings (five minutes unless
   changed; `recordMaxMs()` in `public/dictate.js`) — **is ✓**: the take stops
   itself, with one long pulse (`HAPTICS.capped`), and goes to be cleaned. A ✓
   the person pressed gets no buzz. Either way the
   microphone is released and the browser's recording light goes out.
4. **Cleaning.** The bars settle flat and faint and a soft band of light sweeps
   slowly across them — no spinner — while the clock's slot reads `Cleaning…`
   and the two controls become one ✕ (Cancel), which aborts the request.
   Read-aloud Play works throughout this wait. ✓ and Cancel stop the mic
   tracks and disconnect the meter immediately, before the recorder delivers
   its final bytes. Where `navigator.audioSession` is supported, capture uses
   `play-and-record` and stopping returns it to `playback`; browsers without
   that API manage their own category. Transcription holds no capture resources.
   A new recording pauses read-aloud and keeps its position; playback stays
   blocked while recording. Inserting the returned words does not restart audio.
5. **Landed.** The field is a field again with the words in it at the caret,
   and nothing else appears: no card, no toast, no second action. Polish runs
   inside **Cleaning**, so at Light or Full that state lasts a second or two
   longer; if Polish fell back, the words still land, as heard, and the
   notice row says `Polish did not run: <why>` in the quiet tone, with nothing
   to offer.
6. **Or it did not land.** The field is exactly what it was, and the notice row
   in the composer says so in one line: `Couldn’t clean that take · Retry`
   (the server's own sentence is the line's tooltip), or `Heard no words · Try
   again`. **Retry** sends the same recording once more; if that fails too the
   line says so with nothing to offer and the recording is gone. **Try again**
   starts a new take. Typing, sending, the mic or leaving the chat drop the
   line. A microphone the browser refuses is the same line, with its sentence
   and no offer.

Leaving the chat throws away a take in any of these states: words dictated in
one conversation are not carried into the next.

**What the model is asked.** `gemini-3.5-transcribe`, through the Interactions
API (`POST /v1beta/interactions`), with the audio **inline** as base64 — a take
under the cap fits in the body, and the Files API would be an upload, a
reference and a file left on Google's side for one request.
`transcription_config` is `{"mode": "smart"}` and, when there are any,
`custom_vocabulary`. **There is no prompt and no system instruction**: smart
mode's cleanup is the whole of the instruction, so there is nothing that gives
the model licence to rephrase or summarise. Gemini documents smart mode and
custom vocabulary as compatible (vocabulary is incompatible only with
diarisation and timestamps). If it ever refuses the pair, the server sends the
take again without the vocabulary rather than failing it, and answers
`biased: false`.

### Polish: a second pass over the words

Smart mode strips fillers and punctuates but never reshapes a sentence, and
acoustic biasing misses short invented names even when they are on the list.
So at **Light** or **Full** (Settings → Dictation, Light by default) the server
makes a second call once the words are back: `src/polish.js`, a fast text
model (`DICTATION_POLISH_MODEL`, `gemini-3.5-flash-lite`) reads the transcript
with **every** term the take was sent with — the transcription keeps its cap of
`VOCABULARY_MAX_TERMS`; polish has none — and

- **Light** spells names as the terms list does where the sound matches, and
  fixes grammar, punctuation and capitals; every sentence stays in its place.
- **Full** also reshapes it to read well: run-ons split, paragraphs separated
  by a blank line, a spoken list written as a Markdown list.
- **Off** is the transcript as heard, and no second call.

**It is the Obsidian plugin Spoken's pass, ported**: `src/polish.ts` at
`20117399` (Spoken 0.5.0), so a take polished there and one polished here are
polished alike. The prompt is Spoken's word for word — `test/polish.test.js`
reads it out of a verbatim copy of that file in
`test/fixtures/spoken-polish.ts.txt`, so a change to either is a failing test,
and a deliberate change is a new copy of Spoken's file. The transcript and the
terms go in the input, fenced in `<terms>` and `<transcript>`; the prompt says
the transcript is text to edit and never a message, so a question in it is
kept and not answered, an instruction kept and not obeyed. `store: false`.

**A deterministic guard reads the answer before it lands**, and anything it
refuses lands the transcript as heard: the word count within **10%** of the
transcript's at Light and **25%** at Full (list dashes are not words); no line
that opens like a reply ("Sure,", "Here's the polished transcript", "As an
AI", a fence tag) unless the speaker said those words; no capitalised word
that is in neither the transcript nor the terms (an answer looks like
"Paris."). An error, a wrong key, a rate limit, an unknown model, an
unfinished interaction or no answer in **30 seconds** does the same. **A take
never fails for Polish**: `/api/transcribe` answers `200` with the words and
`polish: {level, ran}`, and `why` beside `ran: false` — the sentence the
quiet line shows. A take that names no level (any client but this page) is
not polished and its answer has no `polish` key. The log line carries the
level, `polish_ms`, `polish_ran` and the reason — except a new-name reason,
which would quote the answer, and is logged as "a name that wasn’t said".

**A floor under the guard, for a composer's short messages.** 10% of four
words is less than one, so without a floor a four-word take at Light could
not gain or lose a single word ("gonna send it" → "Going to send it."). Light
may always move by **one** word and Full by **two** (`WORD_FLOOR`), the larger
of that and the fraction — Spoken 0.6.0's floor, kept identical so the two
products judge alike.

**Biasing.** Names are what speech models get wrong, so each take is sent with
a list of words it should expect: the agent's name, the chat's title, and the
names in the last few turns — capitalised words away from the start of a
sentence, words with a capital inside (`MuseBot`), and short acronyms, the most
often and most recently said first — and, ahead of all of them, the terms
note's. That list is made in one function, `dictationVocabulary()` in
`public/dictate.js`: the note's terms first, then at most 100 from the
automatic seed, each term once whatever its case, at most 1000 terms
(`VOCABULARY_MAX_TERMS`, Gemini's own limit). Whatever gives way is from the
end, so it is the seed before any of the note's. The list goes in the take's
body, so no header budget cuts it (until 2026-09-30 it rode in `x-vocabulary`
and 6000 bytes held about 380 terms). **The server keeps as many as 1000**
(`VOCABULARY_MAX_TERMS` in `src/transcribe.js`, the same number since
2026-09-28; it was 100 and silently dropped the rest), so what the page sends
is what reaches Gemini.

**The limits, each one constant:**

| constant | where | is |
|---|---|---|
| `RECORD_MAX_MS` | `public/dictate.js` | the longest take when Settings says nothing: 5 minutes |
| `RECORD_MINUTES_MIN`, `RECORD_MINUTES_MAX` | `public/dictate.js` | what Settings may set it to: 1 to 15 minutes |
| `RECORD_WARN_MS` | `public/dictate.js` | when the warning starts: 30 s before the cap |
| `RECORD_BITS_PER_SECOND` | `public/dictate.js` | what the recorder is asked for: 32 kbit/s Opus, about 1.2 MB for five minutes |
| `TRANSCRIBE_MAX_BYTES` | `src/transcribe.js` | the largest take the server accepts: 12 MB, enforced as the body is read |
| `GEMINI_TIMEOUT_FLOOR_MS`, `GEMINI_TIMEOUT_PER_MINUTE_MS`, `GEMINI_TIMEOUT_MAX_MS` | `src/transcribe.js` | how long Gemini is given: 120 s, plus 30 s for every minute of the take, to at most 600 s. A fifteen-minute take gets 570 s. |
| `AUDIO_BYTES_PER_MINUTE` | `src/transcribe.js` | how a take's length is read off its size: 240 kB a minute, the page's 32 kbit/s. A browser recording at a higher rate reads as longer and gets more time, never less. |

The server enforces size and not duration: it does not decode audio, and at
the bitrate asked for, 12 MB is more than half an hour, so fifteen minutes
(about 3.6 MB) is well inside it. The duration is the page's.

**The terms note.** `GET /api/dictation-terms` relays the host's
`GET /v1/dictation-terms`, read fresh on every call, in either of two shapes:
`{terms, source}`, the note already read by its rule, or — from a host of
2026-09-30 — `{path, obsidian_url, modified, text}`, the note whole, which the
page reads by the same rule in `parseTermsNote()`. The rule is the Obsidian
plugin's: one term per line; YAML frontmatter, headings, `%% %%` comments and
blank lines ignored; a leading list marker stripped; a line with commas is
several terms; nothing over 80 characters. `vaultTerms()` in
`public/dictate.js` reads both shapes to one list. A `404` (no route on the
host yet, or no note) and a `405` (a host older still) are no vault terms.
Any other failure keeps the last good answer for the page load.

Each Record-and-clean take asks for the note at its press and waits for the
answer only when the take is ready to send, for at most three seconds
(`NOTE_WAIT_MS`). **A take is never held for the note**: refused or
unreachable, the take goes with the terms last read in this page load, and
with none read, with the automatic seed alone — and the quiet line above the
composer says **No terms note yet**, once a page load. Live takes no
terms: the browser's own recogniser has no biasing input this page can rely on.

**`POST /api/transcribe`.** Two bodies. What the page sends is
`multipart/form-data`: an `audio` file part under the content type the recorder
named (`audio/webm;codecs=opus`, `audio/ogg`, `audio/mp4`, …) and a `terms`
field holding the biasing terms as a JSON list, read with the platform's own
form parser. The other is the audio itself as the body, with the terms in an
`x-vocabulary` header as URI-encoded JSON — what the Obsidian plugin sends, and
kept for one release after the page stopped. A form with no audio part is a
`400`. It answers
`200 {"text", "biased", "model"}`, or an `{"error": "<sentence>"}` with `503`
(no key), `400` (empty take), `413` (over the cap), `415` (a type Gemini will not
read), `429` (Gemini refusing more for now), `502` (Gemini failing, unfinished
or unreachable), `504` (Gemini taking longer than the take's timeout, above) or
`422` (no words heard).

### The retired door for Obsidian

From 2026-09-28 to 10-01 the Obsidian plugin dictated through this app, on a
token door (`POST /api/dictate`, its settings at `GET /api/dictation-setup`,
its token in `DICTATION_TOKEN`). The plugin now transcribes on its own key and
the door is gone: both paths are unknown routes, and `DICTATION_TOKEN` is read
by nothing. **A path with a `.` or `..` segment is still a `400`, on every
route, before anything is normalised**, so a path naming one route can never
be served as another — no browser sends one. The `x-vocabulary` header body of
`/api/transcribe` is the plugin's old shape and is still taken.

What a phone sends: Chrome on Android records `audio/webm;codecs=opus`, which is
sent to Gemini as `audio/webm` exactly as desktop Chrome's is. An iPhone sends `audio/mp4`,
mapped to `audio/m4a`; **that mapping is unproven against Gemini** and is left
as it is.

**What a take feels like, and a screen that stays on.** Dictation is done
walking, looking ahead and not at the phone, so the three moments that matter
are felt, and they are the only three (`HAPTICS` in `public/dictate.js`, the one
place they are written, and the numbers the Loam Dictate plugin for Obsidian
copies):

| moment | `navigator.vibrate()` pattern |
|---|---|
| `started` — the mic is live | `40` — one short tap |
| `closing` — thirty seconds before the cap | `[150, 100, 150]` — two firm pulses |
| `capped` — the app stopped the take itself | `500` — one long pulse, then cleaning |

`started` fires in both modes, when the recorder (or the browser's recogniser)
has actually started. Where a phone cannot buzz — iOS has no `vibrate`, and
Chrome answers `false` to a page that is hidden or has never been tapped —
`closing` and `capped` are instead a short soft tone on the same on/off
pattern (`TONES`, a sine at low gain on the take's own `AudioContext`, which is
kept open until the cap's tone has finished). The start gets no tone, because
it would be the first thing recorded. There is no setting for the tone; the
Longest recording card says it is there. A phone with vibration switched off
in its own settings still answers `true`, so it gets neither, and nothing a
page can read says so.

The **screen is held on** (`navigator.wakeLock.request('screen')`, through
`screenKeeper()`) from the press until the take stops recording — ✓, ✕,
Escape, the cap, a microphone that would not open, a live recogniser that
stopped by itself, or the chat being left. Chrome drops a wake lock whenever the
page is hidden, so a take still recording when the page is shown again asks
for it again. Where there is no `wakeLock`, nothing happens and nothing is
said. This matters for more than the dark screen: Chrome does not vibrate a
hidden page, so before 2026-09-29 a take long enough for the phone's screen
timeout ran its warning into a screen that was off, and nobody felt it.

**Tested without a microphone or Gemini.** `test/transcribe.test.js` runs the
route against a fake Gemini on loopback and asserts the request it gets —
endpoint, key header, model, inline audio, smart mode, vocabulary — and every
refusal above. `test/dictate.test.js` drives the page with a fake
`MediaRecorder`, a fake `getUserMedia`, a recorded `navigator.vibrate` and a
clock it moves: the three haptic moments and the tone where `vibrate` is
refused, the screen's wake lock on every way in and out, the auto-stop at the cap, the
words added beside a typed draft, a failure retried, Send, a refused
microphone, and a take discarded mid-recording that is never sent — and the
Settings page: the stepper and a two-minute take that warns and stops at two,
the microphone list nameless until allowed and the chosen device in the
constraints, a device gone falling back to the default, the terms note's terms
sent in the body ahead of the seed and none when there is no note (said once,
quietly), the Names and terms card with a note and without, a device's old
terms shown once with a Copy and then dropped, and Live then Record and clean each taking the
next press. What none of that proves is that a phone records, draws the meter,
buzzes hard enough to notice, keeps its screen on, or names its microphones.

### The browser's own transcriber

Where the mic uses the browser's own speech recognition, it is that and nothing
else. Written out, because "the browser does it" is not an answer to
"where does my voice go":

- **Which API.** `SpeechRecognition`, or `webkitSpeechRecognition` where that is
  the name it has (Chrome and Safari). The page reads whichever exists at load
  and keeps no other path.
- **What this app sends, records or stores: nothing.** No audio reaches this
  app's server, none is written anywhere, and none goes to the broker or to the
  agent. This app has no durable state at all. What crosses the wire from here
  is the text you send, when you press send, as an ordinary message.
- **What the browser does with the audio is the browser's own business, and it
  is not always on the device.** Chrome sends audio to Google's speech service
  and Safari may send it to Apple's; that is a property of the browser you chose
  and of the permission it asks you for, not of this page. If that matters for
  what you are about to dictate, it is the browser's setting to change, not this
  app's.
- **How it runs.** `lang` is `navigator.language`, `continuous` and
  `interimResults` are both on, so words appear as they are recognised — in the
  take, in the field, where the bars would be in Record and clean. The field is
  the live transcript: settled words in full ink, the recogniser's running
  guess muted (so a correction is visible as it happens), and a blinking caret
  after them while it listens. What it says
  is **rewritten from two parts on every event**, never added to: the results
  the browser has settled, and its current guess at what is being said right
  now. The guess is replaced each time; a settled result is folded into the
  ones before it. ✓ puts the words into the field at the caret, as ordinary
  text; ✕ throws them away.

  Appending was the defect the owner found on 2026-09-22: a recogniser re-sends
  its interim result for the same utterance as it sharpens it, so adding every
  result to the field it was already in turned "Hi, how's it going" into "HiHi
  howHi how isHi how itHi how's it going". `test/app.test.js` replays that
  sequence — five interim results, the final, then a second utterance — and
  asserts what the take says after each. Only the spoken part has its
  whitespace collapsed, so a draft typed before the mic was pressed keeps its
  line breaks.
- **How it ends.** ✓ or ✕ (or Escape). If the browser ends the run by itself —
  a long silence, an error — the clock stops and what it heard stays in the
  field for ✓ or ✕; it does not decide for you which it was.
- **A browser with neither this nor a recorder gets no mic at all**, which is
  the honest empty state: a button that opened nothing would be a button that
  lied.

## Read aloud: a reply as speech

A reply can be listened to instead of read, in one of two renderings of the
same text:

- **verbatim** — the reply read as written, by a deterministic pass over its
  markdown (`src/speakable.js`) and no model;
- **brief** — a short spoken briefing, written by `SPEECH_BRIEF_MODEL` from
  the raw markdown and voiced. It speaks as the agent, summarises what was
  said first, then closes with whatever needs the person's attention or
  decision. If nothing needs them, it ends on the summary. It adds nothing of
  its own and is about 220 words, never over `SPEECH_BRIEF_MAX_WORDS`.

The page decides what text to send and when to offer it; this app turns that
text into one MP3 and serves it. Both halves are Gemini, on `GEMINI_API_KEY`,
and the browser never holds the key.

### The contract with the page

```
POST /api/speech
  body  {"chat_id", "agent", "mode": "verbatim" | "brief", "title", "text": "<markdown>",
         "voice"?: "<one of the thirty prebuilt voices>", "style"?: "<a short phrase>"}
  200   {"id", "status": "ready", "duration_s", "url": "/api/speech/<id>/audio.mp3", "brief_text": "…" | null}
  202   {"id", "status": "preparing", "stage": "writing" | "voicing" | "encoding", "progress": 0.0–1.0}
  400   {"error"}   a missing chat_id, agent, mode or text; an unknown mode; a reply with nothing a voice could read;
                    a "voice" that is not prebuilt, or a "style" that is not a phrase of at most 120 characters (the error names the field)
  413   {"error"}   text over SPEECH_MAX_CHARS
  503   {"error": "speech unavailable", "reason": "no key" | "no encoder" | "no storage"}

POST /api/speech/lookup             the same body as POST /api/speech; starts nothing
  200   the POST's own "ready" or "preparing" object, "failed" for a take that failed
        in the last minute, or {"id", "status": "none"}; Cache-Control: no-store
  400 / 413 / 503   the POST's own refusals

GET  /api/speech/<id>
  200   the same object; "status": "failed" carries "error": "<one plain sentence>"
  404   unknown, expired, or a job that was running when the app restarted — POST again

GET  /api/speech/<id>/audio.mp3      (and HEAD)
  200 / 206   audio/mpeg, Accept-Ranges: bytes, Range honoured; every request is a play
  416         a range past the end
  404         gone
```

An unknown agent is not an error; it reads in the default voice.

**`voice` and `style` are the seam for a voice per agent.** Each one present
overrides the agent's `SPEECH_VOICES` entry for that request alone — the voice
matched without regard to case, the style standing where the entry's character
does; each one absent (or `null`) falls back to it. The page sends neither
today. When agents have settings of their own, the voice will be one of them,
the page will send it, and nothing on this side changes. A request's own
`style` is part of the file's id; an omitted style uses the configured character.

`title` may
be absent or `null` (a chat not yet named); it only tells the brief's writer
what the chat is called.

`id` is 32 hex characters of a SHA-256 over the TTS model, the voice, the
mode, the validation version, and the text with its line endings and trailing
spaces normalised. The
same reply asked for twice is one file; a reply that has grown since is a new
one; the page never computes it. A `POST` for an id already being made answers
that job's `202` and starts nothing. `progress` is chunks voiced over chunks;
`writing` reports `0` and `encoding` `0.95`.

**Whether speech exists** is `speech` on `GET /api/health`, beside the
broker's own `configured` and `ok`: `false` when the key, the encoder or the
storage directory is missing, and the page then draws nothing for it.

### What the voice is given

| in the reply | said as |
|---|---|
| a heading | a sentence of its own |
| `**emphasis**`, `_emphasis_` | the words |
| `~~struck through~~` | nothing — it was taken back |
| `[text](url)`, `[[Note\|alias]]`, `[[Note#Heading]]` | the text, the alias, "Note Heading" |
| a bare URL | "a link" |
| `` `inline code` `` | its text |
| a list | one sentence per item, a paragraph break after the list |
| a blockquote | its text |
| a horizontal rule | a paragraph break, which is a pause |
| emoji | nothing |
| a fenced code block | "A code block of twelve lines, skipped." |
| a table | "A table of four rows, skipped." (header not counted) |

Code and tables are skipped because their contents belong to the brief, whose
writer can say what they meant. Whether code should be read aloud has not been
decided; `READ_CODE_BLOCKS` in `src/speakable.js` is the one place to change.

### The pipeline

1. **brief only:** one call to `SPEECH_BRIEF_MODEL` with the agent's name, the
   chat's title and the reply's markdown; the answer is passed through the
   same transform (so any markup it leaks is said as words) and cut at a
   sentence if it ran over the cap. It is kept and returned as `brief_text`.
2. Full documents retain their heading, paragraph and list-item seams through
   markdown conversion. Near a balanced target (normally 650–1,000 characters),
   the chunker prefers headings, then paragraphs, list items and sentences.
   A heading stays with following text when it fits. Preserving sections can
   add a chunk instead of crossing a heading to minimise calls. Long blocks split between
   sentences; an indivisible sentence may exceed `SPEECH_CHUNK_CHARS`.
   Chat and brief prose keep balanced paragraph/sentence chunks: a 400-word
   reply (2,284 spoken characters) is still three chunks of about 760.
3. The chunks are voiced `SPEECH_CONCURRENCY` at a time, each one unary call to
   `SPEECH_TTS_MODEL` through the Interactions API: the text in `input`, the
   style direction as a `speech_metadata` annotation, the voice in
   `generation_config.speech_config`, WAV (24 kHz mono 16-bit) back. A `429`,
   a `5xx`, a timeout or a dropped connection is retried three times at 1, 2
   and 4 s; a chunk is given 60 s; the whole job two minutes plus 15 s per
   thousand characters, to at most twenty. The first chunk that fails stops
   the rest. **Before retaining any chunk**, its PCM sample count gives its
   duration. Expected seconds are estimated spoken word equivalents × 60 ÷
   the voice/mode pace. `src/spoken-length.js` splits snake/kebab/camelCase
   identifiers, counts short initialisms as letters, expands integers and
   decimal digits, and counts spoken operators such as × and %.
   `pixel_watch_api34` estimates seven words and `454×454` nine, rather than
   one each. The accepted ratio is **0.65–1.35 inclusive**; 1.9× fails.
   An out-of-band chunk is
   resynthesised once with identical text; another miss fails the whole take
   with “Couldn't prepare this cleanly — try again.” Nothing is encoded,
   cached or playable on that failure. Transport retries remain separate.
4. The samples are joined end to end with 350 ms between chunks — losslessly;
   nothing is re-encoded until the one encode — and the duration is read off
   them.
5. `ffmpeg` encodes once: MP3, mono, 64 kbit/s, `loudnorm` to −16 LUFS.
6. The sidecar (`created`, `agent`, `mode`, `chat_id`, `title`, `duration_s`,
   `brief_text`, the model and voice, and versioned per-chunk duration checks)
   is written, then the MP3 is renamed into
   place — its being there is what makes the id ready.

The style direction contains the voice's character and delivery attributes:
*natural conversational pace, even and clear* for verbatim; *unhurried pace,
even and clear* for a brief. It contains no request to write or present a
briefing. The transcript is unchanged. This follows
[Google's speech metadata guidance](https://ai.google.dev/gemini-api/docs/speech-generation);
whether the older wording caused the observed invention remains unproved.
No vocal tags go into the text.

The 2026-10-14 change uses supplied live observations: Aoede Full at 147 wpm,
briefs about 121 wpm, faithful brief chunks at 1.22–1.30 of the Full reference,
and an invented take at about 1.9×. At the brief reference, that faithful
envelope becomes about 1.00–1.07; the old invented ratio remains about 1.56.
The upper band adds five percentage points of pronunciation/pause margin.
This margin and the technical-token estimate are heuristics, not a newly
measured distribution: no Gemini key or source documents were supplied to
the implementation job. Other voices remain provisional at 150 wpm.
The listening-time picker and writer's word budgets still use their existing
150-wpm estimate; a selected brief time is approximate.
This is a duration check, not a transcription check; replacement or invented
words that fit the duration can pass.

**Parallel preparation.** Chunks already synthesise concurrently, four per
job by default, and are joined in document order after validation. Keep that
default pending deployment measurements. Google's limits apply per project
and model, vary by tier, and include requests/minute, tokens/minute and
requests/day; four workers is not a guarantee of quota safety. The existing
429/backoff handling remains. Inspect the project's limits before increasing
concurrency; several simultaneous reads share them. See
[Google's rate-limit documentation](https://ai.google.dev/gemini-api/docs/rate-limits).

**Deployment measurement.** `tools/measure-speech.mjs` is also included in
the image. With documents supplied at runtime, the offline command reports
Full chunk counts and lengths before/after this change:

```sh
timeout 30s node tools/measure-speech.mjs <document.md> full
```

With `GEMINI_API_KEY`, an explicit writable `SPEECH_DIR` and ffmpeg available,
add `--synthesise` to run the production pipeline. Use a fresh speech directory
to measure generation rather than an existing cache:

```sh
timeout 1300s node tools/measure-speech.mjs <document.md> full --synthesise
timeout 1300s node tools/measure-speech.mjs <document.md> 3 --synthesise
```

The comparison and `speech` lines print counts, timings and every chunk's
ratio/attempt, never source text or credentials. Independently listen to
`SPEECH_DIR/<id>.mp3` against the note or the sidecar's `brief_text` before
calling a take faithful. Confirm the affected identifier-heavy note's Full
read, the architecture proposal's three-minute brief, and that proposal's
Full counts; those exact documents were unavailable in the implementation
job. Fixture tests cover their supplied failure mechanisms and reject a
synthetic 1.9× take, but cannot establish those documents' real synthesis.

**Storage and expiry.** One `<id>.mp3` and one `<id>.json` per id under
`SPEECH_DIR`. Every audio request sets the MP3's modification time to now, and
that is its last play; the sweep — at start and hourly — removes an MP3 and its
sidecar once it has gone `SPEECH_TTL_HOURS` unplayed. A job being made is in
memory only, so after a restart a `GET` for one is a `404` and the page asks
again; a failed job is remembered for ten minutes so a page polling it reads
the sentence.

Every lookup and audio request rechecks the sidecar's chunk measurements
against the current pace and band, and its total against the checked chunks
plus joining pauses. Startup and hourly sweeps do the same. Missing or
invalid validation removes both files, as does the known unfaithful take
`2bd0bee386eea59a7d19dbc1f4c3bec6`, regardless of its measurements. Each
removal logs `speech-cache-purge` with the id and reason. **All older,
unchecked cache entries are regenerated**, including ones that might have
been faithful: their sidecars cannot prove individual chunk durations.
Checked synthesis has validation version 2 and a new cache identity.
Version-1 sidecars lack spoken-length estimates and are regenerated once.

**What has not been caused from here.** No real Gemini call was made by the
engineer who wrote this: the key is in the container's environment, not the
engineer's. The request shapes are Google's documentation of 2026-10-02, and
the tests prove what this app sends and what it does with the documented
answers — not that Google accepts them.

## Listen and Brief: a reply read aloud

One quiet speaker glyph at the right end of the turn line under a reply, named
**Read aloud**. A tap opens a small menu anchored to it — the composer's
popover card and rows, as wide as its words (260px to 360px, never wider than
the screen less its gutters), its right edge on the glyph's so it opens
leftward into the thread — with two rows, each a mark, a word and a quiet line:

- **Listen** — "The reply, read as written · about 7 min · takes about 1 min
  to make": the length from the reply's word count at about 150 words a
  minute, said as a person says it ("about 40 s", "about 1½ min", "about 7
  min"), and how long making it takes (`makeEstimate()` in `public/speech.js`).
- **Brief** — "Summary first, attention items last · about 1 to 2 min · takes
  about 30 s to make", and **absent** (not greyed) below the device's Brief threshold (200
  words unless Settings says otherwise).

**Each row says the take's state** (2026-10-04, the owner: "there's no way to
know if it's instantly available because it was created before, or if it's
going to be created once I tap"). Every reply has a take per mode, and a take
is *none*, *making*, *ready*, *playing*, *paused*, *ended* or *failed*. The
lines above are *none*. *Making* is "Making · it waits for your tap when it is
ready"; a take that exists is "Ready · 2:09"; *failed* is "It could not be made
· tap to try again". The glyph says it before the menu opens: a quiet dot at
its shoulder when a take is ready, a small turning ring while one is being made
(stilled under reduced motion), nothing when there is none.

**What exists is the server's to say**, through `POST /api/speech/lookup` with
the body a POST would send: it answers ready, preparing or none and starts
nothing. The page asks it for each finished reply's glyph as the chat draws
(again after five minutes), and afresh every time the menu opens — so a take
made on the phone reads Ready on the desktop, and a take the server has swept
(48 hours unplayed) goes back to none before a tap could find it gone. The
lookup and not an id computed in the page, because the id is made of the TTS
model the server chose at start and the agent's configured voice, neither of
which the page knows. Nothing about takes is stored on the device.

**Nothing plays except a tap.** Choosing a row whose take is ready plays it at
once, inside the tap; a playing one pauses, a paused one plays. Choosing a row
with no take makes it, and **it stops at ready**: the row shows ▶ and the
length, and the page makes no sound — whether the person is on that chat,
another, Files, or recording a dictation. (A take the server already had,
answered `200` straight back to the tap, plays as a ready one would.) Several
takes can be made and held at once, each with its own row under its reply;
only one plays, and starting one pauses the other where it is rather than
discarding it. Starting a dictation pauses whatever is playing: a voice in the
room is a voice in the microphone. Escape, a tap outside and a second tap on
the glyph close the menu. The server half — `POST /api/speech`, `GET
/api/speech/:id`, `GET /api/speech/:id/audio.mp3` — is [the section
above](#read-aloud-a-reply-as-speech); the page is written to that contract and
its tests stub it.

**What is read is the response set**: every agent turn after one of your
messages, up to your next message or the end of the thread. Its text is the
agent's own words as the page drew it, in order, joined by blank lines —
never a steps fold, thinking, a tool result, a permission or question card, or
a harness line. It is computed from the thread every time (`responseSets()` in
`public/speech.js`), so an old reply is as listenable as the newest, and a tap
while the agent is still writing reads what exists at that moment. The glyph
sits under the set's last turn line, or at its end while it is still being
written. It appears from 50 words. Nothing about a tap sends a message or wakes
a chat: an ended chat is as listenable as a running one. A subagent's own
words, inside a fold, are never offered.

**Whether to draw any of it** is the boolean `speech` on `GET /api/health`,
beside `configured`. Absent or `false`, nothing of this is drawn.

**The player row** is the composer's two-row box, with the composer's air (16px
in, 12px down; 8px between the rows). Row one: the kicker ("Listen · Ada",
"Brief · Ada") and ✕. Row two, in turn:

- *preparing* — the stage the server reports ("Writing the brief…",
  "Voicing…", "Encoding…") and the track as a calm moving band while
  `progress` is absent or `0`, then a fill growing from the left once it is
  above nought. Polled about once a second. No spinner.
- *ready* — ▶/⏸ (44 px on a touch screen), the track with its fill, knob and
  buffered shade (tap or drag to seek), `elapsed / total` in tabular digits,
  and the rate pill: 1× → 1.5× → 2× → 1×, pitch kept. A take just made waits
  here at `0:00 / 2:09` with ▶. Where a browser refuses a play (iOS can, when
  a request came between the tap and it), the row simply shows ▶, and the next
  tap plays. No error. Below
  1100px it is two rows: ▶ and the track; the time and the rate, 8px below.
- *a brief* has "Show the brief" folded under the row, the summary in the
  thread's own type.
- *ended* — the knob at the end, ▶ replays from the start.
- *failed* — the server's one sentence and Retry. A `404` on a poll or on the
  audio (the file expired) is asked for again without a word, twice, and only
  then said.

**The docked bar: the take follows you.** Each take has two surfaces. The row
is the take while it is on the screen. When it
scrolls out of view (an `IntersectionObserver`; the bottom 96px, under the
composer, counts as out of view) or the chat is left for the list, another chat
or an agent's page, a bar appears directly under that screen's header — the
chat's header in a chat, the Chats header on the phone's list, the top of the
desktop's pane otherwise — taking its own height rather than covering anything:
▶/⏸, one line ("Brief · Ada · Loam phase six planning", the title giving way
first), the rate pill, ✕, and a hairline of progress along its foot that seeks
where it is tapped. A tap on its words opens that chat and scrolls the row into
view, and the bar goes; position, rate and play state carry over because it is
the same audio. ✕ on either surface stops it and closes the row; the take stays
ready on the glyph.

The bar has two more forms, for a take made here: **"Making · Brief · Ada ·
‹title›"** with a turning ring and ✕, so the person can leave and know it is
coming; and **"Ready · Listen · Ada · ‹title›"** with ▶ and ✕, once it is
made — ▶ plays it, the words go to its row, ✕ lets the bar go and leaves the
take ready on its row. Which take has the bar: one playing or paused owns it;
otherwise the most recent ready take nobody has played yet; otherwise the most
recent being made; otherwise the one that ended. So **when a take ends, the
bar offers the next ready one with ▶ and does not play it.** In every form the
bar is drawn only while that take's row is off the screen. **A reload ends
playback**: nothing of a take is stored. A reply older than the window a
reopened chat loads has no row to go back to, and the bar stays.

With the row focused, space plays and pauses and ←/→ move five seconds. The
lock screen and headphone buttons get play, pause and seek through the Media
Session API, with the chat's title and the kicker, where the browser has it.

**The voice outlives the redraws.** The thread is redrawn on every stream
event; the `Audio` objects (one per take, made at its first play) are never on
the page, and the row and the bar are one node each per take, placed with
`kept()` and keyed by the take — so a
stream tick neither restarts nor moves what is playing.

**Settings → Chat preferences → Read aloud**: "Listen from" (10 to 500 words, in tens)
and "Brief from" (100 to 1,000, in fifties). With the rate, stored on this
device under `loam-ui.speech`. Voices are the server's, per agent; there is no
chooser here yet — `POST /api/speech` takes `voice` and `style` for the day
agents have settings of their own.

The design — the glyph and its menu, the player in every state, the docked
bar — at a phone's width and the desktop's column:
[`design/player-mock.html`](design/player-mock.html) — open it in a browser.

## The keyboard, and the box the app lives in

**A phone's keyboard does not make the page shorter.** On Android Chrome it
slides over the bottom of it and the *layout* viewport — which is what `100vh`
and `100dvh` measure — does not move. So an app laid out to the full height of
the window puts its composer in the part of itself the keyboard is now
covering: the send button half off the screen and no room beneath it, which is
what the owner saw on 2026-09-20.

**One line asks Chrome not to do that.** The viewport meta carries
`interactive-widget=resizes-content`, which is the browser's own switch for
shortening the *layout* viewport when the keyboard comes up instead of sliding
the keyboard over a page that still believes it is full height. Where it is
honoured, `100dvh` is already the box above the keyboard and there is nothing
for the page to correct. It is not a replacement for the model below — Safari
and older Chromes ignore it — so both are in the build, and they agree: the
same 336px keyboard on an 844px window gives the same 508px app whichever
route the browser takes.

What does shrink where the meta is ignored is the **visual** viewport, and
`window.visualViewport` is the only thing that reports it. The page listens to
its `resize` and `scroll`, asks `public/viewport.js` what box the app should
be, and writes that onto `#app`:

- the app's **height is the visual viewport's height**, so the composer's own
  bottom edge is the bottom of what can actually be seen;
- the app is **moved down by `visualViewport.offsetTop`**, for the browsers that
  lift a focused field clear by scrolling the visual viewport over the layout
  one rather than resizing anything;
- the transcript is **kept at its end** across the change if the reader was
  already at the end, which is the same rule a streamed delta follows;
- a **pinch-zoom is not a keyboard**. It shrinks the visual viewport in exactly
  the same way, and resizing the page under a magnifying glass moves what is
  under the glass, so above `scale` 1.02 the app keeps its height and stays put.

Under the composer is the design's 14px plus `env(safe-area-inset-bottom)`, so
the send button is not sitting on the phone's own home strip; the same is true
of the floating tab pill and of both bottom sheets. `100dvh` stays in the
stylesheet as what a browser with no visual viewport to read is left with.

The composer is `flex: none` and the transcript is the one part of the screen
that gives its height away, so a box grown to six lines with the keyboard up
shrinks the transcript rather than pushing itself off the bottom — and the
transcript stays at its end if the reader was already there. Past six lines the
draft scrolls **inside** the box, which is why the box has a ceiling at all.

`viewport.js` is a pure function of four numbers and touches nothing, which is
the only way any of this could be tested at all: there is no browser on the box
this app is built on. `test/viewport.test.js` is that arithmetic and is
**fixture-only** in the strongest sense — it proves the app asks for the right
box, not that a phone draws it.

## A tap is not a hover, and a stylesheet has to know it

A touch screen has no pointer, so a browser on a phone fakes one: **a tap leaves
`:hover` stuck on whatever it landed on** until something else is tapped. A
hover style is therefore a style that stays on after a tap, and that is what
made the thread's pin button come back from a tap in the wrong colours —
`.iconbtn.ghost:hover` is one class more specific than `.iconbtn.on`, so the
button a finger had just pinned kept the ghost's near-transparent fill under the
on state's near-white glyph. It looked right again only when the thread was left
and reopened, because leaving moved the stuck hover to the back button.

So **every `:hover` in `public/style.css` is inside `@media (hover: hover)`**,
and touch gets `:active`, which is what the design asks for anyway
("Hover/pressed (touch highlight): rows tint `--c5` / `--c6`"). The pinned icon
button also carries `.iconbtn.ghost.on` beside `.iconbtn.on`, so it outranks
that hover for a real pointer too. `test/style.test.js` holds the rule: it reads
the stylesheet back and fails on a hover style that escapes the media query.

## The agent writes markdown, and no reply is ever HTML

The personas answer in markdown, so a page that drew their text verbatim showed
the marks: `**Shaun, Monday.**` with the asterisks, list dashes as dashes,
backticks around a path. `public/markdown.js` is the renderer, and two rules
are the whole of it.

**It builds nodes, never markup.** There is no string of HTML anywhere in that
file and nothing is ever assigned to `innerHTML`: a block becomes
`document.createElement` and its words become `document.createTextNode`. So a
reply containing `<img src=x onerror=alert(1)>` or `<script>alert("xss")</script>`
is a run of characters in a text node and is drawn as the characters it is —
not because something escaped it, but because there is no parser anywhere in
the path that could have read it as a tag. `test/markdown.test.js` asserts both
halves: that the characters are on the screen, and that no `img` or `script`
element exists in the tree.

**Both sides of the thread go through it.** The owner, 2026-09-22: *"Please make
sure bullet points and numbered lists render as MD in the text input."* A
message a person types is rendered by the same `markdown()` and drawn in the
same `.md` inside their own bubble — the colours are `.say.user`'s, and the
two rules that name a surface colour, the link and inline code, are answered in
the bubble's own ink instead. One renderer means one answer in this app to what
markdown is, and it means the paragraph above is true of a person's message
word for word: `test/list.test.js` sends the same `<img src=x onerror=alert(1)>`
payload through the composer and asserts the characters are on the screen with
no `img` and no `script` in the tree.

**The one value a node can carry away is a link's `href`.** Only `http`,
`https` and `mailto` become links at all. `javascript:`, `data:`, a scheme with
a control character hidden in it (`java\tscript:`) and a destination with no
scheme — every relative path an agent writes about its own files — are left as
the literal markdown they were written as, so a reader sees
`[click me](javascript:…)` rather than a link they cannot see the inside of.
Links carry `rel="noopener noreferrer"` and open in a new tab, because this
page is a live session and leaving it loses one. An image is drawn as the link
it points at and is never fetched.

**Written here rather than taken from a library**, and the reasons are this
app's own: every well-known markdown library renders to a *string of HTML*, so
using one would mean `innerHTML` at the one place in this app that must never
have it, and would mean a lockfile and an install step in an image that has
neither. It is four hundred and sixty lines of code, with a test file beside
it.

What it renders: paragraphs and line breaks, bold, italic, both, strikethrough,
inline code, fenced and indented code blocks in the design's JetBrains Mono,
ordered and unordered lists including nested and loose ones, headings at the
design's type scale one step down (a heading in a bubble is not a screen
title), links and autolinks, blockquotes, rules, and tables. What it
deliberately does not: footnotes, reference-style links, and raw HTML — which
is not an omission but the rule above.

**A table, since 2026-09-22.** The owner: "render tables in the chat
beautifully." It is the GitHub shape — a header row, a row of dashes that says
how wide the table is and how each column lines up, and body rows — and a
table may follow a sentence with no blank line between them, because that is
how an agent writes one as often as not. A cell is inline markdown like
anything else, and a `\|` is a pipe rather than a cell boundary.

Two things about how one is drawn are worth stating, because neither is in the
design bundle, which has no table in it:

- **A column of nothing but numbers is lined up on the right without being
  asked.** The dashes can say so — `--:` — and where they say nothing, a
  column whose every filled cell is a figure, a percentage, money or a signed
  delta is right-aligned, and one cell of prose in it makes the whole column
  prose. The guess is only ever made where it cannot be wrong.
- **A wide table scrolls sideways inside the message it is in.** A table will not
  wrap, so a three-column table of prose is wider than a phone; it sits in a
  scroller of its own, so the message keeps its width and the thread and the
  composer are never pushed past the edge of the screen. On the agent's side that
  scroller is also what lets a table run past the measure its prose is held to.
  The header row is
  told apart by a fill and the rows by one hairline, in the colour the rest of
  a reply rules in.

**Every code block and every table has a copy button**, since 2026-09-30.
The owner: *"it doesn't have an easy copy text or code button and because it is
its own separate scrollable container, copying becomes hard."* It sits in the
block's top-right corner, outside the scroller so it stays put while the block
slides sideways: shown on hover where there is a pointer, always shown and muted
on a phone. It copies the block's **source** — the code as written, and a table
as the markdown rows it was written in, since what it is pasted into is as
likely as not the composer — and reads "Copied" for a moment. `public/clipboard.js`
is the one way this page copies anything (the old terms' Copy uses it too):
`navigator.clipboard`, and an off-screen textarea where that is absent. The
button is on the agent's side only. **A code block scrolls sideways and never
up and down** — it is drawn whole — so a vertical swipe that lands on code or a
table scrolls the thread, and a long-press still selects inside it.

**It renders while the message is still being written.** Text arrives as
deltas, and the message is re-parsed whole on each one, which is affordable
because `morph()` writes only what changed: `test/app.test.js` streams a reply
into a running chat and asserts that **every mutation the delta caused landed
inside that one message's node** and that the finished message above it is the
same element it was. An unclosed fence is already a code block, and `**bold`
is the characters `**bold` until the closing mark arrives and turns it bold.

Three things stay plain, on purpose. **A person's own message** is text the
person typed, and marking it up would change what they wrote. **The home
screen's status box** shows `last_message` with the marks taken off rather than
rendered (`strip()`), because it is two clamped lines of prose under a row, not
a transcript. **A plan card's steps** are the same: one line each, stripped.

## No inline anything, and what that means for a style

The app serves one policy on every response, and the page is written to it
rather than the other way round:

```
default-src 'self'; connect-src 'self'; img-src 'self' data:;
base-uri 'none'; form-action https://github.com; frame-ancestors 'none'
```

`form-action` names GitHub and nothing else: the one form this page ever
submits is the GitHub app manifest (Settings → Integrations, below), posted
from the browser to GitHub's own create page.

There is deliberately **no `style-src` and no `script-src`**: everything falls
back to `default-src 'self'`, which means a file this app serves and nothing
else. No inline script — which is why the theme is set before the first paint
by `/appearance-state.js` and `public/theme-boot.js`, external files. The first
reads public host preferences; the second applies them before styles load. No
font from a vendor — which is why the three typefaces are in `public/fonts/`.
And **no inline style**, which is the one that cost something:

**A `style="..."` attribute is an inline style, and `element.style.foo = ...`
is not.** To a browser enforcing this policy the first is refused — the
declarations never land, the element goes on drawing what it was drawing, and
all the page gets is a line in the console. The second is not policed at all.
Setting a property, or adding a class, is therefore the only way this app
writes a style, and there are exactly two places that do it: `h()`, when a
screen is built, and `restyle()` in `public/ui.js`, when a redraw carries a
style onto a node already on the page.

`restyle()` exists because of a real defect. The reconciler copied attributes,
`style` is an attribute, so it copied that one too — and the deployed app
logged *"Applying inline style violates the following Content Security Policy
directive 'default-src 'self''"* on every chat that was opened, 2026-09-25. Any
node the reconciler *kept* whose style then changed went stale: the ring that
follows a session's status, a blob that changes colour, a quota bar's width,
the transcript's padding under the composer when it changes through the tree
rather than through `fitDock()`'s own property write. Nothing was broken here,
because there was no policy in the fixture to break it — which is the reason
the fix is three things and not one:

- **The reconciler carries a style across declaration by declaration**
  (`restyle()`), never as an attribute, and still writes nothing that already
  reads the same.
- **It compares what the page asked for, never what the browser reads back.**
  `dress()` keeps a node's declarations on it as `__css`, and `restyle()`
  diffs those two records. The first version read the declarations back
  through `item()`/`getPropertyValue()`, and every agent colour is a custom
  property — `background: var(--c49)` — which a browser stores as
  *pending-substitution* longhands that each read back as `''`. So a redraw
  took the colour off every blob and left the shape (`border-radius` holds no
  `var()`): the owner, 2026-09-25, *"the icons shapes keep disappearing"*. A
  style put by hand on a node the tree also draws goes through `dress()` for
  the same reason — `fitDock()`'s padding, an icon's `flex`, the quota ring's
  size — and `test/fakedom.js` models the pending-substitution read-back for the
  shorthands this app writes, so the suite fails on it.
- **`h()` refuses a style that is not an object.** A string would have become
  an attribute, so it throws rather than drawing something a browser will
  silently decline.
- **`test/fakedom.js` enforces the policy the browser enforces.** A
  `setAttribute('style', ...)`, or a `<style>` element the page builds, throws
  in the fixture — so the mechanism cannot come back anywhere in the app
  without the suite going red, rather than a phone going quietly wrong.
  `test/server.test.js` holds the other end: the header still says
  `default-src 'self'` and still contains no `unsafe-inline`.

**Conforming was chosen over loosening**, and it was not close: the alternative
is `style-src 'unsafe-inline'`, which would take the policy off inline styles
permanently — for a page whose whole job is to draw text that an agent wrote —
in exchange for a padding value. A hash or a nonce was not needed either,
because no value here has to be a *style attribute*; every one of them is a
property assignment. **A browser is still what confirms the console is silent**:
there is none on the box this project is built on, so the suite proves the
mechanism and not the rendering.

## What the page reads, and what it ignores

`public/transcript.js` is the only file that knows what an agent's events mean.
Twelve rules hold it together, and every one of them is a test:

- **An `assistant` event carries *some* of a message's content blocks, not all
  of them.** A reply that says a sentence and then calls a tool arrives as two
  of them under one message id — `[text]`, then `[tool_use]` — and the first
  lands before the tool block has begun streaming. So an event's content is
  written at the point the message has been told about so far and what came
  before it is left alone. Replacing the message with each event is what made
  every sentence an agent said before a tool call vanish the moment the call
  arrived, leaving a turn with nothing in it but its last paragraph.
- **A run of consecutive steps folds, and every step is inside it.** A step is a
  tool call or a block of the agent's thinking — the machinery of a turn, as
  against what the turn said to the person. `foldFacts()` is the rule and it is
  the seam's because it is one rule: how many steps the fold holds, how many of
  them failed, whether one is still going, and the latest line of thinking for
  the header to read while one is. **One call or five**, since 2026-09-22: a run
  of one folds to "1 step". **All of them, including the one running and the one
  that failed**, since 2026-09-25: the header says both of those, so neither
  needs a row in the thread to say it.
- **A tool result whose call this page never saw is still a step.** A chat opens
  at `?last=<n>` and a page of history ends where it ends, so a window can begin
  above the call a result belongs to. It used to be dropped; it is now a step of
  its own at the point it arrived, reading "earlier step" with its output.
- **A step dies with its process** (2026-10-05). On `broker.exit`, every call
  still without a result is marked `ended`, and a block of thinking still being
  written is finished: `foldFacts()` counts an ended call as neither running nor
  failed, and its row reads *ended with the chat*, the words its request's line
  already says. A replay reads the same, because the exit comes later in the
  log; a page of history prepended above an exit this page has read ends its
  unanswered calls the same way, and a result the newer window drew as an
  "earlier step" is put back on its call instead of being drawn twice. A result
  that does arrive later by `tool_use_id` is attached and is what the row says.
  Whether a chat is working stays the record's `status` alone (`inFlight()`):
  no step makes the header or the row breathe.
- **A plan stops with its process** (2026-10-06). A `TodoWrite` card spins
  while an item is `in_progress`, which is the plan's own state and not a
  missing result, so the rule above did not reach it. `broker.exit` marks plan
  calls `ended` too, and a plan card with an item in flight then reads
  *Planned N steps* · *k of N done · ended with the chat*, neither running nor
  failed, its items as the plan left them. A later plan update is a card of its
  own from the running process and is the one that spins.
- **`broker.session` is not an event.** It is the session's record, sent first
  on every stream and again whenever a turn record is written. It carries no
  `seq`, it is never deduplicated by number, and its `record_seq` is where the
  *session* is — the last event in the log — never where this page got to. The
  page reconnects at the last `seq` it read and at nothing else.
- **A reply the server could not save says so.** The live-only, unnumbered
  `broker.write_failed` carries `{reason, from_seq}`. One quiet notice follows
  the streaming reply: the host's reason plus “This reply may not be there
  after a reload.” It uses the existing error colour when the turn ends.
  The turn record's `not_saved` reason restores the same line on reopening,
  including when the reply and its `result` are absent from replay. Missing
  results leave a turn slot at the person's message, keeping later turn
  details aligned. A broker without either field behaves as before.
  `test/write-failed.test.js` covers the reducer; the optional Chromium check
  uses the real relay with fixtures at 390/1440 in light/dark:

  ```sh
  WRITE_FAILED_EVIDENCE="$TMPDIR/write-failed-notice" node test/write-failed-browser.mjs
  ```

  Install Playwright separately; `LOAM_PLAYWRIGHT` and `LOAM_CHROME` can name
  its module and browser. Captured fixture evidence is in
  [write-failed notices](evidence/write-failed-notice/README.md).
- **An event it does not recognise is ignored.** The broker is not a filter and
  the set of event types grows whenever the agent CLI grows. An unknown type is
  not an error and is not a line on the page — but its `seq` still counts, so
  the reconnect position keeps moving.
- **A `user` event is a tool result, never a person speaking.** What a person
  sent is on the session's record as `broker.user_message`, which reaches every
  client watching including the one that sent it. The page draws its own line
  when you press send, labels it with the `seq` the messages route answers
  with, and so draws it once whichever arrives first.
- **A card lives exactly as long as its request** (2026-10-05). The page drops
  a card on any of five events and one answer:
  - `broker.permission_resolved`, whatever its `behavior` — `allow`, `deny`,
    `expired` (nobody answered within the host's limit and the model went on),
    `cancelled` (the CLI withdrew it), or `ended` (the process ended with it open);
  - `broker.exit`, which takes every card of the session;
  - a `broker.session` snapshot whose `pending_permissions` no longer lists
    it. This holds only if the record was written at or after the request: a
    record older than the request cannot speak for it. The list and its
    `record_seq` are kept, so a request replayed *after* the snapshot is judged
    the same way on arrival. That covers the opening window, a reload, and
    "load earlier" over an old log that no event ever closed. **The host's
    list is of request ids, as strings, not of requests.** For one day the page
    read objects out of it, got no ids, and dropped every live card the first
    time a record passed its request (2026-10-06). `tools/fake-broker.js` now
    sends ids too;
  - a `404` from the answer route. The host holds no such request, so the card
    goes and the host's sentence shows once, quietly, on the notice line. It
    is not reported as a failed send.

  Any other failure puts the card back, answerable, with "the answer did not
  reach the session: <the host's sentence>". While an answer is in flight its
  buttons stay disabled across every redraw. Whatever closed it, a card leaves
  one quiet line where it stood: *Allowed*, *Allowed for this chat*, *Denied*,
  *Not answered · Ada went on without it*, *Withdrawn* or *Ended with the
  chat*, then the tool and its target, e.g. `Allowed · Read ·
  agents/bin/session-broker`. A path is shown relative to the agent's
  workspace, or to what it shares with the workspace. A line the page drew
  first, from its own answer, a `404` or a snapshot, is corrected in place by
  the host's event and never drawn twice. *Allowed for this chat* is the
  host's to say: it is drawn only when the resolved event carries `scope`. A
  request that closed with no event at all reads *Ended with the chat*.
  Before the broker closed its own requests, that was nearly always the reason.

  **An approval's line is a step and folds with the calls around it**, in the
  place the request stood, and the fold's count includes it ("3 steps" for two
  calls and the *Allowed* between them; a chat whose only steps are approval
  lines still gets its fold). Drawn between folds, every past approval split a
  run in two, and a busy chat reopened as folds and lines in turn. A
  question's line (*Answered*, *Declined to answer*, or a question's *Not
  answered*) is what the person said, and stays in the thread. The rule is
  `foldsAsStep()` in `public/transcript.js`.
- **Everything a session is asking is one card.** The CLI raises requests
  concurrently. The card is drawn below the newest message under "Needs your
  OK", headed *Ada is asking for 3 things* when there is more than one. Each
  request is a row: the tool and its target (the file, the directory, the
  pattern and where it looks, the command), and the reason in plain words.
  `decision_reason_type: "workingDir"` reads *outside Ada's folders*; any
  other reason is the host's sentence. The raw input is folded under
  *Details*. Each row has Allow and Deny. With two or more approvals the foot
  adds *Allow all* and *Deny all*, which answer each approval as its own row
  would. A question among approvals is a row with its form, and alone it is
  the question card. A row closed from anywhere leaves its line, and the card
  shrinks, going when it is empty.
- **"Allow for this chat" is the CLI's own session rules, sent back verbatim.**
  The CLI's `permission_suggestions` can name any destination, and it does
  propose `localSettings` rules (mostly for `Skill`), which would have it write
  the persona's own settings file from a tap. The broker refuses any
  `updatedPermissions` entry whose `destination` is not `"session"` with
  `400` "`updatedPermissions` may only carry suggestions whose destination is
  'session'", by design. So the reducer keeps only the `session` entries, and
  everything reads from those: a row gets the third action only when at least
  one survives, *Allow all for this chat* is drawn only when every approval on
  the card has one, the sub-line says what they grant (`For this chat: Read
  under agents/bin`), and the body is `{"behavior": "allow",
  "updatedPermissions": <those entries as given>}`. A request whose rules are
  all of another scope has Allow and Deny alone. A session rule holds for the
  chat's process and not past it: after a resume, or a broker restart, the same
  read asks again. There is no "always": a persona's standing reach is its
  configuration, not a tap.
- **A question is a form, not a permission.** `broker.permission_request`
  carries `kind`. `"question"` — the one shape the host turns every provider's
  asking into, Claude Code's `AskUserQuestion` and Codex's alike — is drawn as
  a form: each question under its header, its options as rows (one choice, or
  several when `multi`), an Other row with a field when `other` (the field is
  the whole answer when there are no options), masked when `secret`, and one
  Send that waits for every `required` question. It answers at the same route
  as `{"behavior": "allow", "answers": {"<question id>": ["<label or typed
  value>", ...]}}`, carrying only the questions that have an answer — an
  optional one left empty has no key. Decline sends `{"behavior": "deny"}` and
  the model is told the person declined. The host checks an answer before it
  leaves pending, and a `400` is not a failed send: the card stays, answerable,
  with the host's `error` sentence on it until the next change. An answered question leaves one quiet line in the
  thread with what was chosen (a secret is not drawn back). `"approval"`, no
  `kind` at all, a kind not met yet, and a question with no questions in it
  are all the Allow/Deny card, so nothing the host asks is undrawable. The
  page reads no vendor's own format.
- **A turn's cost and model are the turn record's, and arrive on the snapshot
  that records it** — a moment after the turn's own `result` event, which
  carries neither. Until then the line shows neither, and then fills in. The
  cost is `cost_delta_usd`; `total_cost_usd` is the whole *process's* running
  total, which climbs across a turn and falls back to near zero at a resume, so
  it is only ever shown with the word "total" beside it. The app computes
  neither — only the host knows where each process began. An unknown cost is
  shown as nothing, never as zero, and the model is never taken from
  `modelUsage`, which names every model the process billed including a
  subagent's and the CLI's own small-model calls.
- **A retry is `system` with `subtype: "api_retry"`, and nothing else.** Its
  `error` is a category from a fixed list, not a sentence; `retrySentence()` is
  the one place in the app that writes words about a retry, from the category,
  the HTTP status and the wait.
- **A `broker.exit` is a line of history, not a state.** It says the process
  ended at that point in the log, and a session resumed under its own id keeps
  its old numbering — so anything numbered after it is the new process, and the
  exit this page is holding is cleared by it. A record written at or after the
  exit that says `running` clears it too, which is the other device's resume;
  and `resumed()` clears it when this page asked for the resume itself. The
  exit **line** stays in the transcript either way, because it did happen.
- **A turn's context is on the `result` event and on the turn record.** `{used,
  window}`, either half of which may be `null`. It is on the event as well as
  the record so the turn a page has just watched end can say what it held
  without waiting for the snapshot — which is the one thing `cost_delta_usd`
  cannot do, being the host's own arithmetic. `contextWords()` is the only
  place in the app that puts a reading into words, so the line under a turn and
  the line in the chat's sheet cannot say it differently.
- **`broker.background_tasks` names what the session is waiting on.** It is not
  a line in the timeline: a background task is a thing that is happening, not a
  thing that was said. The count a page draws is `background_tasks` on the
  record, which every snapshot carries and a home-screen row can read with no
  stream open; this event is the names behind it.

## Attaching files and images

Two requests, never one: **upload, then send the paths.** The + in the composer
opens a sheet with *Upload a file*, *Upload an image* and *Pick from Files* —
the last drawn and disabled, because the app's own file store is out of scope
and a person told "later" stops looking for it.

`POST /api/sessions/<id>/attachments` — `multipart/form-data` with one or more
**`file`** parts, which is exactly what the composer sends. This app relays the
body untouched: nothing here reads inside the envelope, names the file or
decides where it lands. The broker answers `201` with
`{"attachments": [{"path", "name", "size", "type"}]}`.

**The name that comes back is the name from now on.** The host sanitises it and
makes it unique where it stores it — `Board notes (final).md` comes back as
`Board-notes--final-.md`, a second `shot.png` as `shot-1.png` — so a chip that
went on saying what the person's own disk called it would be naming a file that
is not there.

`POST /api/sessions/<id>/messages` — `{"text", "attachments": ["<path>", …]}`,
the paths exactly as the upload answered them. A message may be nothing but
attachments. `broker.user_message` on the stream carries the same files back as
objects, which is what a reopened chat draws its chips from: nothing of a
message's files is kept in the browser that sent it.

**The cap is the host's number and this app keeps no copy of it.**
`GET /api/limits` relays the broker's own `GET /v1/limits` —
`attachment_max_bytes`, `inline_image_max_bytes`, `attachment_types` — so the
number the page checks against and the number the broker enforces cannot drift.
The page refuses an over-size file before a byte of it moves, because a phone on
a slow connection should be told in the composer and not after a minute; if the
limits route has not answered, the page refuses nothing and the host does.

Every refusal is the host's own sentence, shown as it is: `413` over the cap,
`415` for a type it does not carry, `400` for a path it did not store, `404` for
one that has gone. This app writes no words of its own for any of them — the
broker reads the body before refusing, so the sentence always arrives.

`tools/fake-broker.js` answers all of this, so the sheet, the chips and a sent
message can be exercised with no agent and no host anywhere.

### Three ways in, one intake

The + is one of them. The owner, 2026-09-22: "I also want the ability to paste an
image into the input box as well as drag and drop a file here."

- **A paste, in the two shapes a phone actually uses.** A screenshot on the
  clipboard is a file, and goes up the way a chosen one does — same cap, same
  chip, same route. A clipboard with no name for it is answered with one:
  `pasted-<the UTC minute>.png`, the extension read from the type the clipboard
  gave. Text on the clipboard is still text, and HTML on it is still stripped to
  text — a paste carrying both does both, in that order. The field itself never
  draws an image: `public/compose.js` builds its nodes one `createElement` at a
  time from characters, and a pasted file is handed out to the composer rather
  than into the field.

  The owner, 2026-09-25: *"I can't paste in an image into the chat on my mobile."*
  Android's keyboards have **two** paths into a `contenteditable` and the desktop
  shape — `clipboardData.items` — is neither of the common ones. A long-press
  "paste" hands the file over on `clipboardData.files`, which the same reader
  takes because it reads `files` before `items`; and some keyboards fire no
  `paste` event at all and deliver it as a **`beforeinput` whose `inputType` is
  `insertFromPaste`**, with the file on a `dataTransfer`. Both are listened for
  and both hand the file to the same callback. A `beforeinput` carrying no file
  is left entirely alone: an `insertFromPaste` of plain text is the ordinary
  paste, the `paste` handler already has it, and preventing this one would take a
  person's words away on the browsers that send both.
- **A drop.** The whole chat is the target on both layouts — the thread and the
  composer are one thing to drag onto, which is what the tint over the pane and
  the "Drop to attach" line say. Several files at once is ordinary and each one
  goes up. A drag carrying anything else — text, a link, a selection — is left
  to the browser and nothing highlights.
- **The document refuses the default everywhere else**, which is the part that
  is not decoration: a browser's own answer to a file dropped on a page is to
  *navigate to the file*, and the draft, the chips and the open stream go with
  it. So `dragover` and `drop` are both prevented at the document, and the chat
  takes the ones meant for it.

All three land in one function (`intake()` in `public/app.js`), so a pasted
image is refused for the same reason and with the same sentence as a chosen
one. `public/attach.js` reads the clipboard and the drag in one place, because
a `paste` and a `drop` hand the page the same `DataTransfer` under two names.

**What is fixture here, said plainly.** The events are. There is no browser on
the box this is built on, so what the tests fire are a `paste` and a `drop`
carrying the shapes a browser puts on them — that the page reads those shapes,
uploads what it finds and draws the tint is proved (`test/app.test.js`,
`test/attach.test.js`, `test/compose.test.js`); that a real clipboard, a phone's
long-press menu or a Finder drag produces them is not. The first things to try
in a real browser are a screenshot pasted into the field and three files dragged
onto the thread.

## The + menu and the model button: attach, and what this chat runs as

**Two menus, two chromes, one builder.** Each of the two buttons on the left of
row two opens the design's popover on the desktop (300px, above row two, the
button turning primary while its menu is open) and the bottom sheet on the
phone. The content is one function — `composerMenu()` in `public/app.js` — so a
menu cannot drift apart between the layouts it is drawn in:

- **The +** is **ATTACH** — *Upload a file*, *Upload an image*, and *Pick from
  Files*, the last drawn and disabled. See the section above for what happens
  to a file.
- **The model button** is **THIS CHAT** — a **Model** row and an **Effort** row,
  each naming what the chat is set to and drilling into a page of the same menu:
  a back button, a kicker, and one row per value with a check on the current
  one. Picking closes the menu and is a `PATCH` on the record, and the button's
  own label follows that record at once.

They were one menu under two kickers until 2026-09-22. The owner that day:
"instead have a model button next to the plus that opens the model and effort
options" — a setting a person changes mid-conversation is not something to go
looking for under a plus sign, and the button says what the chat is set to
without being opened. Clicking the other button while one menu is open swaps
the menu rather than closing it; clicking the open menu's own button closes it,
which is the design's answer for the +.

**Both are the record's, like the title and the pin and for the same reason: a
model chosen on the phone is the model on the laptop.** `PATCH
/api/sessions/<id>` takes `{"model": "<id>"}` and `{"effort": "<id>"}` beside
`title`, `pinned` and `archived`; the page draws what the host answers, never
what was clicked, and nothing about either is kept in the browser.

**Only what `GET /api/providers` offers appears.** A provider may carry `models`
and `efforts` — a list of `{id, name}`, or of bare ids, both read in one place
(`choicesOf()` in `public/providers.js`) because `GET /v1/agents` answered
strings until it answered objects and the lesson from that change is that a page
given only an id has to invent a display name. A provider that is not wired, or
one the host offers no list for, offers nothing here: the page draws the row and
the page it opens says so in a sentence. **The design's model names are its
sample data and are not in this app** — a list baked in here would be wrong the
week a model is renamed, and it would be this app deciding something only the
host can state.

A chat whose record names no model is on **the agent's own default**, drawn as
"Agent's default" and never as a guess at which model that is. The New-chat
sheet and dialog offer the same list, behind the same pill, with `{Agent}'s
default` first and selected; picking nothing sends no `model` key at all,
because the default is the absence of one.

**What is fixture here, said plainly.** `project.md`'s interface section does
not yet name `model` or `effort` on a session record, on `PATCH`, or `models`
and `efforts` on `GET /v1/providers` — this app is written against the shape
above and it is exercised end to end only against `tools/fake-broker.js`, which
answers all of it. Against a broker that does not carry those fields yet: the
menu draws both rows, each page says the provider offers nothing to choose from,
the context pill names no model, and the New-chat pill reads the agent's own
default with nothing behind it — nothing
breaks and nothing is invented, but nothing can be set either. The one thing
that would need changing if the host names these differently is the two key
names in `chooseFor()` and the two list names in `modelsOf()`/`effortsOf()`.

### Closing a sheet

Every bottom sheet on the phone opens with a handle row: the grip, and a quiet
✕ at its right, 32px, in the ghost style. The ✕ closes; so does a tap on the
scrim. **The grip does what it looks like it does**: a drag that starts on the
handle row or the sheet's header follows the finger, and released past a third
of the sheet's height — or flicked down at 0.5 px/ms or faster over at least
24px — the sheet slides off and closes; released short of that, it snaps back.
In the sheet's content a drag only takes the sheet when the content is at its
top and the finger pulls down, so a long Settings page scrolls as a page does.
The rule is `public/sheetdrag.js` and has no DOM.

**Nothing in the app can start the browser's pull-to-refresh.** `html` and
`body` are `overscroll-behavior: none`, and every rule in `public/style.css`
that makes something scroll carries `overscroll-behavior: contain` —
`test/style.test.js` fails on one that does not. The desktop's dialogs are
unchanged: no handle row, and their own Close.

### The New-chat sheet

The same card, a bottom sheet on the phone and a centred dialog on the desktop.
Since 2026-09-23 it is **four things and no sentences**, and what it does not
say is as much the design as what it does. The owner, 2026-09-22: "That modal
feels crowded and too text heavy even when the model options are collapsed. How
can we improve the design of it to be more strategic and intelligent and
conserved with the use of text?" and "I want a small and non primary action for
the user to be able to, instead of creating a new chat, go to the agent profile
page."

1. **Who the chat is with**, and under the name a quiet **"Profile ›"** — a line
   of text in the design's link colour, never a second button beside Start. It
   closes the card and opens that agent's page. Tapping an agent in the strip
   has always meant "start a chat"; this is the way out of that for a person who
   meant "tell me about it", and a long press is the other one.
2. **One row of chips: the providers the host has wired**, each with its mark,
   and at the end of it the design's own idiom for "add" — the dashed blob from
   the end of the agents strip, as a chip with a `+` and no label (its
   accessible name is "Set up more providers"). A wired provider nobody has
   signed in to — Codex before its first sign-in — **is drawn and cannot be
   pressed**, and under the row a quiet line per such provider reads
   *"Codex · Sign in on the Providers page"* and goes to that provider's page
   (2026-10-01; the owner: "It will be another option for the main persona
   agents"). "Wired" is the host's: a row that is connected or carries a `sign_in` or `login`
   block (`startChoices()` in providers.js). A provider that is not wired at all
   is not a chip: the row for it, and the host's own sentence for why, are on the
   Providers page, which the dashed chip opens. Nothing connected is that chip
   alone, and **Start chat is disabled**.
3. **The model pill** — the same `modelPill()` as row two of the composer,
   reading "Sonnet" or "{Agent}'s default", opening the same Model and Effort
   pages from the same `composerMenu()` builder. It replaced a line reading
   "{Agent}'s default model · default effort  Change".
4. **Start chat**, which opens the chat and **asks the host for nothing** — see
   the next section.

So the whole of the card's copy is: the title, the chips' names, the pill's
label, "Profile ›" and "Start chat". The kicker "RUN THIS CHAT ON", the sentence
about subscriptions, the "Set up more providers ›" link and the "Change" line
all came off, and `test/desktop.test.js` asserts their **absence** — a test that
only held what is there would pass on a card that had grown them back.

- **The pages the pill opens are a page of this same card**, under a back button
  reading "This chat", and not a popover or a second sheet. A popover hung
  inside a scrolling card is a popover with its top cut off, and a second sheet
  over the first has nothing to go back to.
- **It offers "{Agent}'s default" ahead of the values, and a running chat does
  not.** Before a chat exists, the absence of a value is a thing that can still
  be sent; once it exists, the host has been told a value and this page has no
  name for taking it back. That is the target's `fallback`, which is the only
  thing the two callers of `composerMenu()` differ in.
- **The preselection is the host's.** `GET /v1/providers` may carry
  `default_model` and `default_effort` for a provider — or, Codex's shape, the
  model entry flagged `default: true` — and only a value that provider actually
  offers is read; a host that names neither is a pill reading "Alfred's
  default". This app invents no default and names no model of its own.
- **Efforts are per model where the host says so.** Codex lists `efforts` on
  each model (one goes to `ultra`, the next stops at `max`), and the row's own
  `efforts` is their union, read only for a model that names none. Choosing a
  model that does not go as far as the effort already chosen takes the effort
  back to the default rather than sending something the host would refuse.
- **Only what moved goes out.** `POST /v1/sessions` carries `model` or `effort`
  only where the choice differs from that provider's default — the host's own
  default is not a thing the host needs to be told, and the agent's default is
  the absence of the key.

### A refused message stays unsent

In an existing chat, a refusal to resume the session or accept the message
leaves a full-opacity bubble marked **Not sent**, with the broker's reason
and **Resend** and **Edit**. Resend makes a new attempt; another refusal
returns it to unsent. Edit removes the bubble and puts its words and stored
attachments back in the composer, preserving anything already written there.
Files references return too and are saved with the edited draft.

Unsent messages live locally under `loam-ui.unsent.<session id>`, separately
from the broker transcript. Reloading and reopening that chat restores them;
freeing a slot or reopening the page never sends them automatically. A local
copy is retained while an attempt is underway and removed after acceptance.
Browsers that disable local storage cannot retain them across reloads, as with
composer drafts. Uploaded files stay on the host; only their stored references
are kept here. The new-chat draft behavior below is unchanged.

The waiting-for-a-reply line requires the broker's working status and an
accepted user message. Local pending messages cannot start it. A process exit
clears it, including before a stale snapshot catches up; a later event or a
current running snapshot can establish a resumed process. The live list's
snapshot reaches the transcript reducer too, so a resume on another device
can clear the old exit.

`test/unsent.test.js` drives cap refusal, repeated Resend, message-route refusal,
Edit with both attachment kinds, acceptance and exit through the real relay.
The optional Chromium driver checks reloads, both arrival orders and layout:

```sh
timeout 90s env LOAM_PLAYWRIGHT=<playwright-module> LOAM_CHROME=<chromium-executable> UNSENT_EVIDENCE=<directory-outside-repository> node test/unsent-browser.mjs
```

It creates fixture paints at 1440 and 390 px, plus short and long read-aloud
stop checks. `UNSENT_PUBLIC` can point at an earlier `public/` snapshot to
reproduce the original dimmed bubble and false working line.

### A new chat is a draft until you send the first message

The owner, 2026-09-25: *"when I create a new chat, the session should be created
and started after I send the first message. In case I change my mind and don't
send a message for whatever reason."*

**Start chat opens a draft and makes no request at all.** The agent, the
provider, the model and the effort are held in the page; `POST /v1/sessions`
happens on the **first message**, immediately followed by that message. Until
then there is no session on the host, no record, and no row anywhere — a
New-chat sheet you open and think better of costs nothing, where it used to
spend one of the four slots the broker will run at once and leave a "New chat"
row behind it. Those rows were exactly this.

- **It is `state.open` like any other chat, with `draft: true` on it**, because
  the whole of what a person does in a draft is type into the composer. A screen
  of its own would have been the thread screen with a second composer, second
  attachments, a second model pill and a second dictation under it.
  `threadContext()` gives a draft a **synthetic record** carrying the chosen
  values and honest nulls for everything that has not happened, so the header,
  the pill and the menu each stay one code path.
- **What a draft does not draw**: the rename, the pin, and the three-dot sheet.
  All three act on a record, and there is none — the header says "New chat" and
  the host names the session after its first turn, a moment later.
- **The notice row in the composer says nothing.** A chat that has not started
  is not a chat that stopped, so `quietLine()`'s last rung ("Not running · your
  message picks it up") is skipped for a draft. Everything above it still holds:
  a sign-in about to expire is worth reading *before* the first message, and a
  refusal is the whole of what this screen has to say.
- **A refused first message keeps the draft, the words and all.** If the cap is
  full or the broker is down, nothing is cleared: the composer still has what
  was typed and the files it was carrying, and the host's own sentence is above
  it. Losing a brief somebody has just written because the fourth session was
  already running would be the worst possible answer to a cap.
- **A file attached to a draft is held in the page and goes up when the chat
  exists.** The attachments route is a session's, so there is nowhere to send
  bytes to yet: `addFile()` keeps the `File` on the item (`held`), the chip is
  the same chip, `uploading()` does not wait on it — the thing that starts the
  upload is the very send that would be blocked — and the first message uploads
  them all and then carries the paths the host answered with. This is the one
  case in the app where a file is held in the browser at all, and it lasts
  exactly as long as the draft.
- **Abandoning is leaving.** Back, another chat, the profile, a reload: the
  draft goes, with whatever was typed in it. Nothing is parked, because a draft
  has no id to park it under — `store.setDraft()` is per session.

### The Live filter, and how many are live

The owner, 2026-09-25: *"since we have a ram limit on number of running sessions,
we need a filter in search on active sessions that are active in Ram atm."*

The search row's chips have a **Live** one, and at the end of the row the count
**"2 of 4 live"**. Both read the broker and neither is worked out here:

| what | from |
|---|---|
| which rows are live | `live`, a boolean on each session's record |
| how many are live | `live_count` on `GET /v1/sessions` (or `live`, the older name for the same number) |
| the most that run at once | `cap` on the same listing |

- **`live` is not `state`.** `state: running` is what the record says about a
  process; `live` is the host saying that session is resident right now, which
  is the thing the cap is about.
- **An absent field is unknown, never `false`.** The broker states `live` from a
  change riding a restart, so before that restart the chip is **not offered at
  all** — a filter that narrowed the list to nothing would read as "no chats
  match" about a field that had simply not arrived. `liveKnown()` is that
  condition: some record has said `true` or `false`. And a listing with no count
  on it draws **no counter**, rather than "0 of 4", which would be a claim about
  the machine made out of a missing field.
- **The count follows the stream without polling.** `live_count` is on the
  listing and no record change can carry it — the same shape as the cap `note`.
  So when a snapshot on the live list changes a session's `live` from a boolean
  this page already held, the listing is read again, once. The page counts no
  residency of its own: a number derived from the records it happens to be
  holding would be a second opinion about the host's own, wrong the moment a
  session it has not been told about becomes resident.

## The mark, and installing the app

The mark is **Strata**: three layers of earth cut to the organic blob the
agents are drawn in, with its own fills (`#CDA57F`, `#7A5236`, `#4A3A2E`) so it
reads the same on either theme. A placeholder, as the name is. It is drawn in
four places and no others: the tab's favicon, the iOS home-screen icon, the
install icons in the manifest, and the top of the desktop rail.

Every file of it is generated by `tools/make-icons.py`, which holds the three
paths once and writes the SVG and every PNG from them with the standard library
alone — a small scanline rasteriser, because the image has no dependencies and
the box that makes the icons should not need one either:

| file | is |
|---|---|
| `icon.svg` | the mark, transparent round the blob — the favicon and the rail |
| `icon-32.png` | the same, for a browser that takes no SVG favicon |
| `icon-192.png`, `icon-512.png`, `icon-1024.png` | the same, the manifest's `any` icons; the 1024 is for a high-density splash screen |
| `icon-maskable-192.png`, `icon-maskable-512.png`, `icon-maskable-1024.png` | the mark on the ground colour `#E6E1DA`, its farthest point at 36% of the width from the centre — inside the 40% safe zone Android masks to |
| `apple-touch-icon.png` | 180px, opaque on the ground colour, because iOS fills transparency with black |

**What Chrome wants before it offers to install**, and where each is met: a
manifest with `name` or `short_name`, a `start_url`, `display` of `standalone`
(or `fullscreen`/`minimal-ui`), a 192px and a 512px icon, and no
`prefer_related_applications: true` — all in `public/manifest.webmanifest`,
held by `test/server.test.js`; served over HTTPS, which is the host's proxy;
and a person who has tapped the page and stayed on it a little. A maskable icon
is not required but without one Android draws the mark on a white disc. A
service worker is no longer required and `sw.js` is kept anyway.

**The splash screen draws a PNG large**, which is where a coarse one shows
(the owner, 2026-10-01: "very low quality when it shows up on the app startup
screen"). Chrome's own page on the manifest says only that it "chooses the icon
that most closely matches the device resolution for the splash screens"
([web.dev/articles/add-manifest](https://web.dev/articles/add-manifest)) and
names no purpose, so both purposes go up to 1024px and all of them are drawn
the same way: each cubic flattened to 256 segments (at 32, the old outline sat
up to 0.16px off the curve at 1024 in 13px facets), each pixel covered exactly
across its width on 16 scanlines down it. The script's output is the same bytes
on every run, which `tools/make-icons.test.js` holds. Nothing caches the icons on the
way — `sw.js` keeps no cache and every static file is served `no-cache` — but an
installed app keeps its own copy, which Chrome refreshes when it next finds the
manifest changed ([web.dev/articles/manifest-updates](https://web.dev/articles/manifest-updates):
at most once a day, on launch).

**The manifest is linked with `crossorigin="use-credentials"`, and that is not
decoration.** A browser fetches a manifest with no cookies unless it is told
otherwise — even one on its own origin — so behind a login portal the manifest
request is answered with a redirect to sign in, it never parses, and Chrome
falls back to offering a shortcut. That is the symptom this line exists for.

## Files: the vault, AI Spaces and System

Added 2026-10-02. The design's second tab, now real on both widths: the
phone's tab pill and the desktop rail. Three roots, by the owner's names —
**Vault**, **AI Spaces**, **System** (the design said "Agent Spaces") — then
folders, then a file previewed or edited. Everything about what may be read or
written is the host's; the page draws the host's `capabilities` and decides
nothing.

On a phone, a file opened from a chat has one **‹ Chat** back control. It
returns to the same reading position with any earlier loaded turns still in
memory, and reconnects from the last received event. The folder path below
the title, or **Open folder** in ⋯, opens the file's folder. Opening a file
from a folder uses ordinary folder back instead. On desktop, the rail's Chats keeps the same return path.
Narrow file headers replace Preview/Edit with one labelled icon toggle before
the controls crowd; save and read-only status sit with the title on phones.
Wide Markdown tables scroll inside their own keyboard-focusable region, with
a soft fade only at edges that have more content. This affects file previews;
chat table rendering keeps its existing behavior.

The optional `test/file-from-chat-header-browser.mjs` checks the real page at
360, 390, 412 and 1440 px in both themes. It uses a synthetic broker and note,
loads earlier turns, opens their file link, verifies the reading position on
return, and checks header geometry, folder navigation and table scrolling.
Set `LOAM_PLAYWRIGHT` to an installed Playwright module and optionally
`LOAM_CHROME` to Chromium. Captures default to
`$TMPDIR/file-from-chat-header/`; `FILE_HEADER_EVIDENCE` overrides that path.
See [the retained evidence](evidence/file-from-chat-header/README.md).

**The relay** (`src/server.js`) allowlists eight routes and rebuilds each query
from the names that route takes:

| page calls | becomes |
|---|---|
| `GET /api/files/roots` | `GET /v1/files/roots` |
| `GET /api/files/list?root=&path=&cursor=&limit=` | `GET /v1/files/list` |
| `GET /api/files/stat?root=&path=` | `GET /v1/files/stat` |
| `GET /api/files/read?root=&path=` | `GET /v1/files/read` |
| `GET /api/files/resolve?ref=&from_root=&from_path=` | `GET /v1/files/resolve` |
| `GET /api/files/download?root=&path=` | `GET /v1/files/download` |
| `PUT /api/files/text` `{root,path,text,base_revision}` | `PUT /v1/files/text` |
| `POST /api/files/file` `{root,path,text?}`, `POST /api/files/folder` `{root,path}` | the same under `/v1` |
| `POST /api/files/upload?root=&path=` (multipart, one `file` part) | `POST /v1/files/upload?root=&path=`, the body untouched |

A `path` with a `.`/`..` segment, a backslash or a control character is a `400`
here and never reaches the host. A save body is capped at 13 MiB (2 MiB of text,
escaped) and over it is a `413` with a sentence. The host's own `401`, `403`,
`404`, `409`, `413` and `415` come back as they came.

**App-owned routes** sit beside them: `GET /api/files/preview?root=&path=`
(the script-free fallback), `GET /api/files/live?root=&path=` (an expiring
read-only URL on the separate content origin), `GET /api/files/link?root=&path=`
(the served vault web URL or app deep link with its kind), and
`GET /api/files/config` (`{vault_urls}`, from `FILES_VAULT_URLS`).

**A download is never a page of this origin.** It is always
`Content-Disposition: attachment`, its type is passed only when a browser draws
it inertly (PNG, JPEG, GIF, WebP, AVIF, PDF, plain text, MP3/MP4) and is
`application/octet-stream` otherwise, and it carries
`Content-Security-Policy: sandbox; default-src 'none'`.

**The host's contract** (verified, 2026-10-02) is what `public/files.js` reads,
each shape in one function — `rootsOf`, `shortcutsOf`, `rowsOf`, `nextOf`,
`readOf`, `resolveOf` — and what `test/fixtures/files-contract.js` answers:

- **One flat row for everything**: `{root, path, name, kind: "file"|"folder",
  size, modified, media_type, authority, capabilities: {read, download, write,
  create}, link}`, plus `source: {root, path, link}` on the two System files
  whose editable copy is a vault note. A folder's `size` and `media_type` are
  `null`; there is **no count**, and none is shown.
- `roots` → `{roots: [{root, name, description, link, capabilities}],
  shortcuts: [{label, resource: row}]}`. Keyed by `root`; the host's `name` is
  drawn. The shortcuts (vault folders) are drawn under the root cards and at the
  top of System.
- `list` → `{folder: row, entries: [row], total, cursor, next_cursor,
  truncated}`. `next_cursor` (a string, or `null` on the last page) is sent back
  verbatim; the answer's own `cursor` is this page's offset and is never it.
- `stat` → a bare row. `read` → a bare row plus `text` and `revision`. `PUT
  text` and `POST file` → the same plus `provenance`.
- `resolve` → by `status`: `found` (`resource`, `fragment`; the resource can be
  a folder), `ambiguous` (`candidates`, `candidates_total`, `truncated`,
  `fragment`), `missing` (any of `target`, `root`+`path`, `reason`,
  `truncated` — `root`/`path` there are what was looked for, **never a hit**),
  `external` (`url`).
- Errors are `{error, …extras}`: `403` with `authority`, `409` with
  `current_revision`, `413` with `size`/`max`, `415` with `media_type`.
- `path` in a query is percent-encoded per value (`encodeURIComponent`), so a
  literal `+` goes as `%2B` and never becomes the space the host's
  `unquote_plus` would read.

**Drawn from the flags, never from a name.** Save and the formatting bar appear
where `write` is true; "+" where the folder row says `create`; a Download where
`download`; everywhere else "Read-only", with a line saying why from the row's
`authority` (transcripts, the agent's own files, the host's runtime files). A
System file with `source` carries an "Edit the source" link to its vault note.

**Addresses.** `/files/<root>/<segment>/<segment>`, each segment
percent-encoded on its own, names a folder or a file; the server answers the
page for any of them, so a direct load, a refresh and Back land where they
were. Which of the two an address names is asked of the host (`stat`), never
guessed from a dot.

**Leaving an unsaved edit** — another row, Back in the page or the browser, a
tab, a link — asks in the page's own sheet ("Leave without saving?": *Keep
editing* / *Leave without saving*), never the browser's `confirm()`. While it
asks, the address stays on the file. Closing or reloading the tab is the one
case the browser's own prompt is used (`beforeunload`), because nothing else
can run there.

**Editing.** Markdown opens in Preview with Edit beside it, and its editor has
the design's formatting bar — **B**, *I*, Heading, • List, Link, Quote — each
applied to the field's selection (`formatText()`; line marks act on every line
the selection touches and come off again when all of them have it). HTML opens
in Preview with Source; other text in a field. Save sends the revision the edit
was made against; the host answers what it **stored** — with provenance stamped
into a vault note's frontmatter — and the field is replaced with that text, and
its revision is the next save's base.

**A conflict** (`409`) keeps the edit on screen and offers *Load theirs, drop
mine* (re-read) or *Overwrite with mine…*, which first says what will be lost
and asks again. The overwrite names only the `current_revision` the host's
`409` gave — never a guess at the latest — so if the file moved again in
between, that save is refused too and the person is asked again.

**Images and PDFs are drawn from bytes this page fetched**: the download, with
the page's own credentials, checked to start with the bytes its extension says
(PNG, JPEG, GIF, WebP, AVIF, PDF — never SVG), and shown from a `Blob` object
URL of that one type, which is revoked when the file closes. A PDF is framed
`<iframe sandbox="">` (no scripts, no same-origin); a browser that will not run
its PDF viewer in a sandbox shows it blank, and a Download sits beside it. The
app's policy retains `img-src blob:` and `object-src 'none'`; live HTML
additionally permits its configured content origin in `frame-src`, below.

**HTML uses the full pane and runs on a different content origin.** With
`FILES_CONTENT_ORIGIN` configured, `src/content.js` serves the saved HTML and
its relative assets unchanged through the broker's read-only download route,
on a dedicated hostname. Both vault and workspace files use this route. One
slim header holds the file name, HTML mark, Reload, Open in new tab and the
file menu; Source is in that menu. The iframe fills the available width and
remaining height, with no browser frame or document-width cap.

The separate authority has no app or API routes. Its iframe and HTTP CSP allow
scripts, same-origin content access, forms, popups and modals; the app authority
never serves scripted file bytes. The app also rejects cross-origin browser
API requests before the relay. Preview URLs are root-scoped, signed for one
hour, and revoked by restart; no broker token reaches the browser. The content
CSP limits fetch connections to that origin while allowing HTTPS CDN resources.
A malicious page can still read other files in its granted root and send data
through allowed images, scripts, links or forms. See [the exact host handoff,
cookie requirements, CSP and residual powers](files-content-origin.md).
The app job does not change the host proxy or activate a new hostname.

Without that host setup, `/api/files/preview` (`src/preview.js`) preserves the
existing static preview: scripts, base tags and refreshes are removed, bounded
local raster images/styles are inlined, network requests are denied, and both
iframe and response are sandboxed without scripts or same-origin access. A
quiet note explains why live HTML is unavailable. The preview always shows the
saved version and says so while there are unsaved edits.

**Links.** A reply's or a note's `[x](/files/vault/a.md)`, `obsidian://open?…`,
an absolute path in backticks, a `[[wikilink]]` (with `#heading`), and — inside
a Files preview — a relative path, are asked of `/api/files/resolve` with the
note they are written in. `found` opens the row and lands on the heading the
`fragment` names; `ambiguous` is a choice ("matches 7 files; the 2 nearest the
top are shown" — "Which one?" and "places" when a folder is among them, drawn
with a folder tile); `missing` says what the host looked for; `external` is opened
in a new tab only if it is `http`, `https` or `mailto`. **A published vault
URL** — one of the `FILES_VAULT_URLS` prefixes — is read as the vault path it
names (with an extension, exactly; without, as a wiki name) and resolved the
same way, rather than leaving the app. Only the exact configured prefix counts,
and a `.`/`..` segment or a query refuses it.

**A folder is a link too.** `[[40_PROJECTS/Loam]]` is sent as written — no
`.md` added — and so is a bare `[[Loam]]`; when the host answers a row with
`kind: 'folder'` the folder's listing opens, never a preview, an editor or a
download. An alias (`[[path|label]]`) shows its label; a wikilink in inline
code or a fenced block is code. Opened from a chat, Files carries **Back to
chat** — on the folder, on a phone's open note, and in the desktop's list
column — and the browser's Back returns to the chat as well. A reload on the
address keeps the place but not the chat it came from (that is not in the
address). `test/chat-links*.test.js` drives all of this through the real chat
at 430px and 1440px.

| variable | is |
|---|---|
| `FILES_VAULT_URLS` | optional; comma-separated `https://` URL prefixes of the published vault whose links open in Files; the first prefix is used for Copy link when that exact file answers successfully |
| `FILES_CONTENT_ORIGIN` | optional; dedicated HTTPS content hostname for live HTML; [proxy handoff](files-content-origin.md) |

**Pick from Files** (the composer's +) opens "Attach from Files" over the chat
— a sheet on the phone, a dialog on the desktop — browsing the roots with a
check per file and a count. *Attach* puts chips in the composer. **It works in
a draft**: the chips are held under the draft, and when the first message
creates the session they go with that message; a draft abandoned takes its
chips with it. The message carries them as `resources: [{root, path}]`.
**Open workspace** on an agent's page goes to `/files/spaces/<agent id>`.

The host reads it (confirmed 2026-10-02): `200 {accepted, seq}`, and a file
outside the agent's read scope or in System is a `403`, a secret file a `404`,
a traversal a `400`/`404` — and in each refused case nothing is sent. Published
vault URLs are served by file path, not slug, which is what `vaultUrlRef()`
reads: `…/From%20app/new#New` is `[[From app/new]]` at `New`; a path with an
extension is `/files/vault/<path>`.

**Upload** (added 2026-10-02) is the add sheet's fourth row, as the design has
it — "Upload · Photos, PDFs, anything from your phone", `↑` — and is there only
where the folder row says `create`, so a read-only folder has no Upload at all.
It opens the browser's chooser (`#pick-upload`, `multiple`, `accept` = the
host's `files_upload_extensions` from `/api/limits`). Each chosen file is
**its own request** (`FormData` with one `file` part, through `put()` for
progress) into the folder that was on screen when it was chosen:

- The sheet becomes "Uploading" with a bar per file, then "Uploaded", or
  "*n* of *m* not uploaded" with each failure's own sentence — the host's
  words, or this page's for a file over `files_upload_max_bytes` or of a type
  not in the list (refused before a byte moves, and only once the host has
  said its numbers). A batch is never reported as one success.
- **Retry** is offered only where trying again could go differently: no
  status (unreachable), a `5xx`, `408` or `429`. A `4xx` refusal of the bytes
  is final. Retry sends that file alone. *Done* closes the sheet.
- **An upload never overwrites.** A taken name is stored as `name 2.ext`; the
  page draws and opens the host's `path`/`name`/`link`, never the chosen name,
  and says "saved as …" on the sheet when they differ.
- When every file has an answer, the folder is listed again if it is still on
  screen. One file that went up alone opens (the design's behaviour), unless
  the person has gone somewhere since; several stay listed on the sheet, each
  stored row opening its file.

The relay checks the address and the size and nothing else: `root` must be a
bare name and `path` a safe Files path (else `400`, nothing relayed; `path=`
empty is the root's top and is sent as such); the body is read to at most
10 MiB + 8 KiB (the host's cap plus the envelope) and past that is **drained
unread and answered `413`** `{error, max}` rather than the connection reset.
Type, name, bytes, duplicates and read-only folders are the host's: its `400`
(unsafe name, bad envelope, traversal), `403` (read-only, with `authority`),
`404` (missing folder), `409` (999 copies), `413` and `415` come back as given.

| `201` from `POST /v1/files/upload` | |
|---|---|
| the stored file's row | `root, path, name, kind, size, media_type, authority, capabilities, link` |
| `revision` | sha256 of the bytes on disk |
| `requested_name`, `renamed` | the chosen name; whether the stored one differs |
| `provenance` | `true` when a Markdown file was stamped with `created_by` |

**Not built, because it is not in the design's scope:** rename, move and
delete (`405` on the host).

### The Files home (2026-10-07)

The home starts as Chats does: the same `.home-head`, 34px, nothing above it
and no line under "Files". On a desktop the list column has no "Back to chat"
— the rail's Chats goes back to the chat Files was opened from. At the top,
two actions: **New inbox note** makes `<inbox>/YYYY-MM-DD HHmm.md` (the next
free name if the minute's is taken) and opens it; **Open daily note** opens
`<daily>/YYYY-MM-DD.md` — the name the vault's daily folder already uses,
flat, Obsidian's default — and makes it empty first if it is not there. Then
the three roots, each with this page's own line about what it holds. Then
**Shortcuts**: files and folders the person chose, in the order chosen, from
their ⋯ menus (*Add to shortcuts* / *Remove from shortcuts*). A file's open
view has the same menu. File shortcuts show their type and open the file
directly; existing folder preferences remain compatible. A missing target is
quietly explained on the home and its menu still removes the shortcut. An
empty card says how to add one. The host's own `shortcuts` are drawn only in
the System root, where they lead to editable copies.

The shortcuts and the two folders (Settings → Files; defaults `00_INBOX` and
`10_DAILY`) are kept in `localStorage` under `loam-ui.files`. They are the
person's and would follow them on the record if the host had a route for a
person's preferences; it has none, and this app keeps no durable state of its
own, so for now they are **this device's**. `store.files` is the one place
that would move. A file shortcut adds `kind: "file"`; existing entries without
that key still resolve their kind from the host when opened.

**Copy link** sits beside Download in each file menu, and on a non-HTML desktop
file header too. The served vault web URL is preferred; an unavailable web
path or a workspace file uses the app's deep link instead. A small “Link
copied” status names “Vault web link” or “Loam link.” The clipboard never
receives an expiring preview grant. The vault check's exact behavior is in the
[content-origin handoff](files-content-origin.md).

### Files read aloud

A Markdown note's Preview has one speaker glyph. It opens a listening-time
sheet with one slider. The right end is **Full**, the entire speakable note;
every other stop is a spoken document summary, labelled **Document summary**.
Three minutes is selected on opening
when that stop exists; otherwise Full is selected. The title, file name,
large duration readout and track ticks are the entire picker. Full's duration
is rounded to the nearest minute (or seconds for a note under a minute).
The same button moves from Prepare through a calm waveform to Play, and
the sheet stays open while audio is prepared. Preparation always waits for a
Play tap, including a cached response. It uses the existing player, including its brief transcript, rate,
lock-screen controls, and dock when the note is left. Reading a note never
starts an agent session.

`public/document-speech.js` computes the stops from the exact same speech
conversion the server uses, at 150 words per minute. Frontmatter is dropped;
headings, lists, links and wiki-link titles become prose; tables and code
blocks are named with their size instead of recited. The candidate times are
30 seconds (only below three minutes Full), then 1, 3, 5, 10, 20, 30, 45,
60, 90, 120, 180 and 240 minutes. Keep only times at most three quarters of
Full, then append Full. Thus 200 words offers 30 seconds, one minute and
Full (about 1.3 minutes); 15,000 offers 1, 3, 5, 10, 20, 30, 45, 60 and
Full (100 minutes). Every minute stop names its unit
(`1 min · 3 min · 5 min · 10 min · Full` for a roughly 21-minute note),
with no coverage fractions. More than seven stops stagger their labels across
three rows so the units fit on a 390 px phone. A tiny note has Full
alone; its range stays focusable and announces that it has no other choice.

On desktop this is a centered dialog outside the page's layout; on a phone
it is a bottom sheet with a safe-area inset and a working grab handle. The
backdrop fades with a light blur and the card rises/scales over 240 ms;
closing reverses it over 220 ms. The slider snaps between stops, its thumb
grows while held, and the readout and button states transition in place.
Reduced motion removes the travel and animated waveform. Focus starts at
the slider, stays within the dialog, and returns to the speaker on dismissal.
Escape, backdrop, Close and a downward swipe on the phone dismiss it.
Only hovering the Close button for 600 ms shows its tooltip; the backdrop
has no tooltip. All motion uses CSS, with no additional dependency.

Files reuses `POST /api/speech` and `/api/speech/lookup`, adding
`source: "document"` and `minutes: <number> | "full"` to the text, title,
mode and optional voice/style. The server validates the selected stop against
the note's speech word count. No chat or agent id is needed for a document.
Full uses `mode: "verbatim"`; all shorter stops use `mode: "brief"`. The
summary's budget is minutes times 150 words. Its separate prompt covers the
whole document in the document's own order, keeping names, numbers, dates
and decisions exactly as written. It adds no attention section, advice,
priority judgement or information of its own. It uses plain spoken sentences.
Its prompt requires preserving qualifiers, scope, conditions, negation and
range bounds that change meaning, including who may do what and words such
as “only”, “per”, “as”, “not” and “unless”. When the budget cannot fit everything, it asks the
writer to omit whole points while keeping every retained point's meaning.
The existing audio-duration guard does not compare summary claims with the
source: detecting changed scope reliably would require semantic comparison,
not a word-presence check. These are writer instructions, not a semantic
validation guarantee.
The server checks the result against ±10%
and asks for a rewrite up to twice; a brief still outside that range fails
before any TTS is purchased, rather than cutting off its ending.

The document seams, concurrent TTS, MP3 encoding, Range support
and 48-hour cache apply to documents too. Cache identity includes the note's
content, listening stop, voice, style and document conversion version (and
the writer model for a brief). Edited content gets a new take; unchanged
content at the same stop and voice reuses its MP3, even from another device.
Both chat briefs and document summaries version their prompt shape in the
cache identity, so older attention-first audio is regenerated once. Full and
Listen keep their existing cache identities. Document summaries also version
the qualifier rule, so summaries from before that rule are regenerated. The
shape contracts and fixture examples are in
[summary-shapes evidence](evidence/summary-shapes/README.md).
Files has a separate `SPEECH_DOCUMENT_MAX_CHARS` cap, 250,000 by default,
because a 15,000-word draft can exceed the chat reply cap. Briefs use one
writer request; a 15,000-word fixture verifies the complete input reaches
that request. The largest real vault drafts still need a live capacity check
before deciding whether a multipart brief is necessary.

**Settings → Read aloud** holds the Files voice choice and a short sample.
It defaults to Aoede, with the style “warm, natural and unhurried”, and offers
the same thirty prebuilt voices as chat speech. Samples prepare and then
offer Play, using the same cache and player. The voice choice is this device's,
stored under `loam-ui.read-aloud` through `store.readAloud`, following the
dictation preference pattern. Chat voices remain per-agent configuration.
Settings selects use the shared `.settings-select` wrapper: input tokens,
an app-drawn chevron inset 16 px, and the native select for keyboard,
type-ahead and the device's option picker.

The deterministic and server checks run with `npm test`:
`test/document-speech.test.js` covers the short and long stops, conversion
and persisted voice; `test/speech-server.test.js` causes cache hits and edit
misses, budget rewrites, chunking, and refusals. The optional real-browser
driver `test/files-speech-browser.mjs` uses an installed Playwright module
(`LOAM_PLAYWRIGHT`) and Chromium executable (`LOAM_CHROME`) to exercise the
real page against fixtures. It produces the six 390/1280-pixel paints and
checks the document requests, preparation without autoplay, Settings voice
selection, and the player returning to its note. See
[the browser evidence and its limits](evidence/files-read-aloud/README.md).
The polish regression driver, `test/read-aloud-polish-browser.mjs`, checks
the real layout, keyboard/focus, touch dismissal, reduced motion, preparation,
retry and Settings select at 1440 and 390 px. Run it with
`timeout 90s env LOAM_PLAYWRIGHT=<playwright-module> LOAM_CHROME=<chromium-executable> READ_ALOUD_EVIDENCE=<directory-outside-repository> node test/read-aloud-polish-browser.mjs`.
The evidence directory is required. Screenshots and measurements use fixture
notes and audio responses, with no paid synthesis. The driver also checks the
thumb's keyboard halo, quiet pointer/opening focus, and Tab return. The initial
polish evidence was moved out of the repository to the installation's vault.

## What is where

```
src/server.js       one HTTP server: the page, /up, and /api -> the broker
public/files.js     Files: addresses, the host's answers read in one place, the folder and file views
src/broker.js       the only file that knows the broker's address or token
src/config.js       every environment variable, read once
src/transcribe.js   dictation's server half: a recording in, Gemini's cleaned words out
src/polish.js       Polish, Spoken's second pass: the prompt, the call, the guard, the fallback
src/speech.js       read aloud: a reply in, one MP3 out — the brief, the chunks, the voice, the encode, the sweep
public/speakable.js  markdown as it is said aloud: deterministic, shared with the server
public/document-speech.js  document speech text, listening-time stops and Files voices
src/speakable.js    server import seam for the shared conversion
public/transcript.js  THE SEAM: the CLI's events -> what one chat draws
public/sessions.js  the home screen's model: a record -> what one row says
public/providers.js the Providers screens' model: a quota reading -> a bar, a sentence, a ring
public/provider-sign-in.js one descriptor-driven sign-in card and temporary flow
public/marks.js     the providers' marks (and GitHub's), and the ring round one: the only place either is drawn
public/integrations.js  GitHub's state -> the card, the notice, the two return paths, the manifest form
public/sse.js       reading an event stream: frames, comments, keepalives
public/link.js      the live connection: opening it, noticing it has gone, reopening it
public/viewport.js  the keyboard: visual-viewport numbers in, the app's box out
public/keys.js      what Enter does: a keydown in, sends-or-not out
public/attach.js    what a message is carrying: chips, the cap, the host's paths
public/seen.js      how far a chat has been read: the throttle, and the flush on leaving
public/store.js     what this browser remembers, and why it is only preferences
public/theme.js     host appearance controller; theme-boot.js applies it before paint
public/markdown.js  the agent writes markdown: text -> nodes, and never markup
public/clipboard.js putting text on the clipboard, with the textarea fallback
public/dictate.js   dictation's arithmetic: the cap, the clock, the meter, the biasing terms, the merge
public/compose.js   the composer's field: markdown in, nodes out, and the caret arithmetic
public/ui.js        h() and morph(): building a screen, and keeping the one on the page
public/style.css    the design's token table, and every screen derived from it
public/app.js       the screens. Knows nothing of any agent's dialect.
public/fonts/       generated — see tools/fetch-fonts.js
test/fakedom.js     a DOM small enough to assert against, and the one policy it enforces
test/browser.js     what a browser would have given the page — including the window's width
test/fixtures/      two recorded event streams (the second a real session's, sanitised), and GitHub's five states and an org app's four
tools/fake-broker.js  a broker that is only a recording, for running it dry
tools/fetch-fonts.js  generates public/fonts/ — edit here, never there
tools/make-icons.py   generates public/icon* and apple-touch-icon.png — edit here, never there
```

`public/transcript.js` is the part worth keeping honest. Claude Code is the
first agent this drives and others follow; when a second one arrives it is a
second reducer beside that file, and no change to the page.

## The conventions this app is held to

They are the host's, they are binding, and they are cheap to keep:

1. **One app, one container.** Nothing installs onto the host.
2. **`GET /up` returns 200** when the app is ready to serve, and not before.
3. **Durable state is SQLite under a single mounted `/storage`.** This version
   has none, and says so rather than inventing some. What it does keep under
   `/storage` is a cache — the read-aloud MP3s, each made again on asking —
   which is files and not a database because there is nothing to query: one
   id, one file, one sidecar.
4. **Configuration by environment variable.** No secret in the image, none in
   this repo.
5. **One HTTP port, and no assumptions about the host.** No hard-coded
   hostname, no TLS inside the app.


Thread member activity follows the parallel-turn contract: replies stream in
independent positions, and the header count opens named background tasks
and running times. See the [behavior and interface](threads-and-boards.md)
and [paired captures and verification](evidence/thread-parallel/README.md).

Thread icons use the sole default persona when there is one member; chosen icons
win, and groups keep their own glyph. Row subtitles state who is asking, working,
waiting, or has replied; quiet threads say Quiet. The header's play/count
opens background tasks grouped by creator; Details saves thread glyphs; previously saved emoji remain readable.
Typing or dictating reveals small recipient faces on a protected shelf above the composer, and
mentions carry inline persona marks. See the [behavior](threads-and-boards.md)
and [before/after captures, motion frames and checks](evidence/thread-identity/README.md).

The [listening-face clearance check](evidence/thread-listening-clearance/README.md)
records end-of-thread clearance and stationary reading positions at both widths/themes.

Thread icons share a quiet state layer in the list, header and Details:
steady attention glow, otherwise a slow working breath. Background-only waiting
and quiet have no layer; waiting remains in the subtitle and header task count.
Both glows follow the avatar or tile shape. Reduced motion uses static tones.
See [behavior](threads-and-boards.md) and
[before/after captures and checks](evidence/thread-icon-no-ring/README.md).

Recipient faces react to typing, the caret and dictation levels without a
“listening” label. Reduced motion keeps the faces still. See the
[current paired captures and checks](evidence/thread-faces-without-label/README.md).

Menus now use one shared shell: beside their button on desktop/tablet, and a
consistent titled bottom sheet on phones. They flip/shift inside the viewport,
follow scroll/resize and support keyboard navigation and focus return, including
Appearance zoom. See the [inventory and positioning rule](menu-surfaces.md)
and [before/after browser evidence](evidence/menu-surfaces/README.md).

Threads support replying to a particular message: at every width, quiet reply,
copy and available read-aloud icons sit at the start of a full-width footer;
the timestamp sits at its far end. Human footers span their right-aligned bubble.
Copy preserves Markdown and shows a check for one second. Phone swipe-to-reply
remains; long press and right-click use native text selection and copy actions.
A cancellable quote chip preserves the draft. Human and persona replies show a quote that
jumps to the original through older history. See the
[behavior](threads-and-boards.md) and
[390/1440 light/dark captures and checks](evidence/thread-replies/README.md).
See the [390/1440/1920 footer layout captures and alignment checks](evidence/thread-footer-layout/README.md),
the earlier [icon-footer captures and checks](evidence/thread-message-icons/README.md)
and current [native selection and swipe checks](evidence/thread-native-selection/README.md).

Working and waiting thread subtitles have a soft text sheen, at 2.4 and 5
seconds per pass. Asking and quiet rows stay still; reduced motion disables
the sheen. See [footer captures, motion frames and verification](evidence/thread-footer-sheen/README.md).
