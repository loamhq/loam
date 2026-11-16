# loam-ui

A private web front door to coding agents on your own machine. Chats are
threads: shared conversations with mentions, one provider/model per member,
a changeable default agent and a markdown board. The working product name
may change.

Threads are the only chat view. The former session-based page, Settings →
Classic chats, session composer and automatic classic fallback were retired
on 2026-11-11. Earlier conversations with content are archived threads. Open
**Archived threads** at the bottom of Chats; they keep their original dates,
newest first, and title search works there. An old `/sessions/<id>` address,
`?session=<id>`, remembered classic choice or session notification resolves
through `/api/threads/imported?session=<id>` to its archived thread.
Missing mappings and thread API failures show their sentence on the thread
list. See [retirement inventory and evidence](docs/classic-retirement.md).

Threads retain the shared rich composer, dictation, file/image uploads,
Files picker, expanded editor, permission/question cards, detailed nested
steps, Reply/Branch/Copy and Read aloud. The three sections are **Chats, Files,
Apps**. **Activity** combines live sessions, jobs and history; **Settings** has
Appearance, Agents, Providers, Integrations, Files and Plugins. The phone puts Activity
first inside Settings, beside the three tabs; desktop keeps Activity and Settings
at the foot of the rail.
See [application shape and runtime handoff](docs/app-shape.md).
The composer edge fades softly above the field, growing behind recipient faces.
See [audio-scroll checks and visual evidence](docs/evidence/composer-edge/README.md).

Buttons follow three attention tiers: one actionable espresso primary, up to
two foam secondaries, and Newspaper for everything else. The thread reserves
primary for Send and secondary for Dictate and Listen-card play. Chats uses a
secondary New thread and Newspaper Search. Navigation keeps its original
selected Latte treatment, exempt from action tiers; utility actions stay in ink.
See the [rule](docs/design-system/tokens.md#three-tiers-of-action)
and [before/after captures and control inventory](docs/evidence/button-tiers/README.md).
The sticky listening bar keeps a neutral Play/Pause circle and speed pill,
with a quiet Close control. See its [states and checks](docs/evidence/sticky-audio-controls/README.md).

Use **Branch into a new thread** beneath a completed message to take a topic
elsewhere. Choose an agent, edit the drafted opening, then **Start thread**.
The new thread opens with that text in its composer; sending it is a separate
action. “Branched from …” and “Branched into …” link both sides. See the
[branch contract and checks](docs/threads-and-boards.md#branching-from-a-message-2026-11-11).

The thread header's **Tasks** panel combines persona background work and
engineer jobs. Jobs keep their place after the persona session ends; recent
outcomes stay for ten minutes and open their receipt in Files. See
[the thread jobs contract](docs/threads-and-boards.md#engineer-jobs-in-tasks-2026-11-13).

- [Threads, boards, models, replies and opening behavior](docs/threads-and-boards.md)
- [Live sessions](docs/running.md), [Activity](docs/activity.md), [schedules](docs/schedules.md)
- [Appearance](docs/appearance.md), [themes and tokens](docs/design-system/tokens.md), [shared menus](docs/menu-surfaces.md)
  — Light, Dark, Sun and System; Sun follows the saved home's sunrise/sunset offline.
- [Files content and HTML previews](docs/files-content-origin.md)
- [Approved design](design/README.md), [shared components](docs/one-product.md)
- [Historical implementation notes](docs/session-ui-history.md) for shared
  dictation, audio, markdown and file behavior; its classic navigation is retired.

## Where the agent actually runs

The question this app exists around is answered in `project.md`, and the
answer is load-bearing enough to repeat here:

**The agent process does not run in this app's process.** It runs on the host, as
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
| **Node, standard library only** | Zero npm dependencies: no lockfile or npm install step. The image includes system packages for speech encoding and page previews. `node:http` serves the page, `fetch` calls the broker, `node --test` runs the tests. |
| **No front-end framework, no build step** | The page is a handful of ES modules the browser loads directly. What is in `public/` is what runs — no bundle, no source map, nothing to rebuild before a change is visible. The whole of the "view layer" is `public/ui.js`: `h()` turns a tag and some properties into a DOM node, and `morph()` puts a freshly built screen into the page **by keeping the nodes already there**. A screen is still built whole on every change, which keeps one code path per row; what reaches the DOM is only the difference, so a stream tick that changes nothing touches nothing and the element under a finger is the same element a second later. A deployed build that replaced the page on every tick lost two automated taps in a row, and a thumb would have lost them too. `key` says which row is which so a row that moves up the list moves rather than being rebuilt; everything else is matched by position. A framework would buy the same diffing and cost an install step, a lockfile and a build stage in the image. |
| **A markdown renderer written here** | The agents answer in markdown and the thread has to render it. Every well-known library renders to a *string of HTML*, which would mean `innerHTML` at the one place in this app that must never have it — and a lockfile and an install step in an image that has neither. `public/markdown.js` builds DOM nodes instead, so a reply cannot become markup by construction. About four hundred and sixty lines of code, with `test/markdown.test.js` beside it. [More below](#the-agent-writes-markdown-and-no-reply-is-ever-html). |
| **Typefaces are served by this app** | Caprasimo, Figtree and JetBrains Mono are in `public/fonts/`, prepared by `tools/fetch-fonts.js`. Five additional first-party display faces live in their theme folders, prepared by `tools/fetch-theme-fonts.js`, and load only when active. Latin and Latin-ext WOFF2 files are committed; no font vendor is contacted at run time. See [Appearance](docs/appearance.md). |
| **Server-sent events, browser ← app ← broker** | The broker already speaks SSE, so a websocket would mean translating one stream into another for no gain. SSE is one-directional and messages go back over ordinary `POST`s, which is what the broker's own interface expects. It also survives a reverse proxy without an upgrade handshake, and reconnects by asking for a position — which is exactly the `?from=<seq>` mechanism the broker gives us. |
| **Cached app previews** | An isolated Chromium process photographs app pages beside the server, one at a time, including browser-only apps. Its empty profile receives no broker credential or browser login. JPEG previews and verified icons are served from this origin. A Loam install has Debian's `chromium-headless-shell` and Noto fonts on the host; without Chromium the designed fallback is shown. [Preview lifecycle](docs/app-previews.md). |
| **Events passed through unchanged** | The app relays the broker's stream byte for byte and reduces it in the browser. Nothing invents a second vocabulary for the CLI's events. |
| **No durable state of this app's own** | Sessions, costs and transcripts are the broker's, and the page draws them from the stream every time it opens one. This browser keeps device preferences, composer drafts and unsent messages per chat: whether the desktop's list column is hidden, how this device takes dictation (Settings → Plugins → Dictation), the Files voice, and Files shortcuts and inbox folder. Pin, archive and title are **not** among them — they are on the record, so a pin made on the phone is a pin on the laptop. The state directory holds regenerable caches: read-aloud audio under `SPEECH_DIR` (swept after 48 hours unplayed) and app previews/icons under `APP_MEDIA_DIR` (bounded and swept after seven days without refresh); a Loam install puts both under `/var/lib/loam-app/<app>/`. Neither is a user record. [Read aloud](#read-aloud-a-reply-as-speech). |

## Run it

With Node 22 or newer and a configured broker, one command from a fresh clone:

```sh
BROKER_URL=http://<broker-address>:<port> BROKER_TOKEN=<token> npm start
```

Open `http://localhost:8080`. Choose an agent → New thread, or use the header’s New thread button, type a message,
and watch the reply arrive while it runs. The broker must support threads.
Check the web half independently with `curl -i http://localhost:8080/up`:
it returns 200 even when no broker is configured. The page still serves when
configuration is absent and reports the broker failure.

On a Loam install the app is not started by hand: Loam runs it as the
systemd unit `loam-app@<app>.service`, from a release of its checkout at a
named commit, behind the install's proxy, login and TLS
(`sudo bin/deploy loam-ui up <commit>`; the repository's
`deploy/README.md`, "Loam's own app"). The unit gives it a uid of its own, a
read-only system, a state directory for its caches and nothing else of the
host. It listens on `127.0.0.1` (`APP_HOST`); the proxy is the one public
listener.

For an isolated UI preview with synthetic data, run `npm start` and open
`http://localhost:8080/?threads=fixture`. It is labelled Design preview and
writes no sample thread content to the runtime. Exit returns to the thread
list. A missing broker produces a visible error. `tools/fake-broker.js` still
supplies session/provider fixtures for retained relay tests; its session chat
screen is retired.

## Test

```sh
npm test
```

The suite uses Node's test runner, synthetic brokers on loopback and no real
agents or credentials. Tooling checks also need Python 3. Browser checks are
opt-in; supply an external Playwright/Chromium installation. The retirement
capture command and 390/1440 light/dark evidence are documented
[here](docs/evidence/retire-classic/README.md). Existing thread, permission,
provider, dictation, read-aloud, Files, schedules and Running now suites remain.

At 1180 layout pixels and above, rows and file links offer **Open to the side**
(also Alt/Option-click). The navigator selects a list; main and side each hold
the thread, file, app or plugin item explicitly opened there. See
[wide panels](docs/wide-panels.md) for resizing, reload/Back behaviour and the
[1180/1440/1920 and phone captures](docs/evidence/wide-panels/README.md).
The interaction matrix runs with an external browser installation:
`LOAM_PLAYWRIGHT=/path/to/playwright/index.mjs node test/panels-browser.mjs`.
It also joins `npm test` when `LOAM_PLAYWRIGHT` is supplied.

Settings → Agents groups agents, project engineers and the nightly reviewer.
Settings → Agents also offers **New agent**, a conversation, and **Archived**.
The [U3 guide](docs/first-release-u3.md) covers profiles, creation, archive/restore,
daily notes and plugin keys.

The [profile guide](docs/agent-profiles.md) describes identity, execution defaults,
schedules, plugin fields, engineer preferences and the reviewer.

At 1100 px and wider, Settings and Activity open as centered dialogs above the
current document. Their rail buttons toggle or replace the open dialog. Escape,
the scrim and Close return focus to the opener. The current thread/file path
stays in the address; `?modal=settings&section=agents` or `?modal=activity`
describes the overlay. Back closes it, Forward reopens it, and reload restores
it over the addressed document. Agent profiles stay inside desktop Settings;
thread and file links close the dialog and open in the main pane. Smaller
windows retain their existing sheets/pages. See the
[behavior checks and capture gallery](docs/evidence/desktop-app-modals/README.md).
Apps uses the desktop list column too: choose a row for main, or use its panel
icon / Alt-click for side. An empty main says “Pick an app”. Activity always
uses its desktop dialog, including after resizing from phone width, and now
matches Settings' dimensions. See the [current checks and captures](docs/evidence/apps-list-activity/README.md).
Apps show a cached preview of the page itself. Desktop uses small thumbnails;
phones use two-column preview cards, with the name and one quiet author line.
A failed capture uses a smaller distinguishing letter on a softened field, or a centred site icon when one is available. Details
open from the card’s inside ⋯ or a long-press. Icons are resolved and cached
server-side; the browser makes no site-icon probes. See the
[preview contract](docs/app-previews.md) and [current routing/fallback captures](docs/evidence/apps-internal-route/README.md).
Choosing Chats, Files or Apps in the desktop rail reveals that list and keeps
the current main document open until another item is chosen. See the
[panel ownership checks and before/after captures](docs/evidence/panel-items/README.md).
Desktop sections register lists and item kinds register panel contents; both
slots use one renderer. App addresses use `/apps?url=…`; thread and file paths
keep their existing shape. The `side` query carries the other item's identity.
Reload and Back/Forward restore those panels, independently of rail browsing.
Existing stored app/side views are converted to addresses once on upgrade.
Run the complete rail matrix with
`LOAM_PLAYWRIGHT=/path/to/playwright/index.mjs node test/panel-items-browser.mjs`,
and the delayed-opening regression with
`LOAM_PLAYWRIGHT=/path/to/playwright/index.mjs node test/panel-opening-browser.mjs`.
See the [plugin section/item contract](docs/plugins/places.md).

Read aloud and Dictation preferences live in **Settings → Plugins → the plugin**
and show only while it is on. Existing device choices and host-managed keys
keep their storage. See the [setting inventory and remaining host API gap](docs/plugin-preferences.md)
and [390/1440 browser evidence](docs/evidence/plugin-preferences/README.md).

The [Settings and Activity review](docs/evidence/settings-activity-review/README.md)
includes a before/after gallery, the Providers/Integrations split, every Settings
subpage, the remaining component consolidation and the new Activity identity
fields. Its [component decisions](docs/design-system/inventory.md#duplicate-decisions)
and [settings route handoff](docs/settings-sections.registry.json) describe the
shared definitions and remaining host dependencies.

Thread details opens provider and model choices at each member's chip, including
on phones. The composer retains its Providers & models sheet. The
[capture gallery and viewport audit](docs/evidence/thread-details-options/README.md)
cover upward flipping, keyboard and scroll behavior, all themes, and short phones.

[Plugin service states, app plugins and GitHub accounts](docs/evidence/plugin-runtime/README.md)
include 390/1440 light/dark captures, scoped account-action checks and main/side
app framing checks. Settings keeps one desktop Settings heading; its content
names the section. These checks use synthetic host answers and no real account
mutations. Plugin apps use the installation's existing same-origin `/x/:id/`
front end; this app relays only their catalog, icons and JSON control routes.

The [2026-11-12 UI audit](docs/ui-audit-2026-11-12.md) includes the clock-format
inventory, screenshots, remaining design decisions and the next module split's
boundaries. With an external Playwright/Chromium installation, run its full
390/1440 theme matrix alongside the suite with `LOAM_UI_AUDIT=1
LOAM_PLAYWRIGHT=/path/to/playwright/index.mjs npm test`. Set
`UI_AUDIT_EVIDENCE` to a scratch directory to capture the screenshots and report;
`LOAM_CHROME` may name a working Chromium executable.

[Thread update painting checks](docs/evidence/thread-update-paint/README.md)
cover the gray-control flash and header-menu conversation replacement. Run
`LOAM_THREAD_PAINT=1 LOAM_PLAYWRIGHT=/path/to/playwright/index.mjs node --test
test/thread-update-paint.test.js` for the 390/1440 light/dark frame checks;
`THREAD_PAINT_ENGINE=webkit` selects WebKit. All data is synthetic.

The [Files shortcuts and header fixes](docs/evidence/files-header-fixes/README.md)
include the 390/1440 Latte/Plain light/dark measurements and screenshots.
[Renaming files](docs/files-renaming.md) now uses the host's rename capability:
tap an open file's name, or choose Rename from its ⋯ menu. Enter or leaving
the field saves; Escape cancels. File/folder menus offer the same action where
the host allows it. [Rename captures and checks](docs/evidence/files-rename/README.md)
cover both widths, both themes and both modes.

## Plugins

The page loads plain-file extensions and themes from the host
catalog. Read aloud and Dictation are independently switchable first-party
plugins. Settings → Plugins shows declarations, provenance, failures and the
two-step install question. Installed code runs as trusted page code; its
network declaration is informational. No build step or app dependency was added.

Read aloud marks available audio with “Ready to play” and offers a separate
Regenerate action for Listen, Brief and Files listening times. Regeneration
preserves the existing audio until its replacement succeeds. See the
[regeneration contract and playback behavior](docs/read-aloud-regeneration.md).

See the [plugin guide](docs/plugins.md), [stable parts](docs/stable-parts.md),
[API types](public/loam.d.ts), [module map](docs/plugins/modules.md), [places and component contracts](docs/plugins/places.md),
[loader, host follow-ups and remaining core](docs/plugins/loader.md), and
[validation evidence](docs/evidence/plugin-host/README.md). Theme packages now
use `PLUGIN.md`; the host's old `manifest.json` reader should be removed.

Host-owned integrations contribute through the [generic Settings hooks](docs/plugins/places.md#plugin-owned-integrations)
from their enabled page packages and call their own `/x/:id/` server. Their
files belong to those host packages rather than shipped fallbacks.

Read aloud’s **Describe tables and diagrams** preference is on by default,
including existing devices, in Settings → Plugins → Read aloud and Settings →
Voice. Full describes Markdown tables and Mermaid, PlantUML and Graphviz
diagrams in one or two sentences using the plugin’s server-side Gemini key.
Ordinary code keeps its skip notice; Brief is unchanged. Turning the preference
off restores the previous Full reading. Descriptions are cached beside audio;
failures fall back per block. This is a device preference, like the Files voice,
and has no host settings route. See [behavior and verification](docs/read-aloud-descriptions.md).

## Code and state

`public/threads.js` owns thread navigation and state. `thread-record.js` and
`thread-steps.js` normalize thread records and full reply steps;
`transcript.js` remains the event reducer for those detailed steps.
`chat-ui.js`, `permission-cards.js`, the composer and speech adapters are
shared rendering code. `imported-threads.js` is the legacy-address boundary.
`running.js` owns session residency and ending; it still uses session routes.
`src/server.js` serves the page and allowlists authenticated broker relays.

Conversation records, boards, pins, titles, archives and read marks belong
to the broker. The page holds transient thread drafts and reading positions;
device preferences live in browser storage. Durable app data, including audio,
lives under `/storage`; any future structured durable state uses SQLite there.
All configuration is supplied at run time. No credentials belong in this repo
or its image. `GET /up` is the web half's readiness endpoint.

Agent voice labels, sample recording status and regeneration are documented in
the [Read aloud voice guide](public/plugins/read-aloud/README.md).

## Configuration

[Installation identity, paths and fresh plugin defaults](docs/installation.md)
describes the runtime contract and staging checks. Owner identity comes from
`GET /api/owner`, never from an environment variable in the web half.

| Variable | Default | What it is |
|---|---|---|
| `APPS_DOMAIN` | `example.com` | base domain whose HTTPS subdomains Apps may embed; no scheme, wildcard, port or path. Also used in the page CSP. |
| `APP_TIME_ZONE` | `America/Los_Angeles` | installation IANA zone for displayed dates/times, daily/inbox note filenames and the initial Sun fallback. Browser locale/hour cycle and a saved Sun location remain personal choices. |
| `VAULT_INBOX_FOLDER` | `00_INBOX` | default inbox folder; an explicit saved Files choice wins. |
| `VAULT_BOARDS_FOLDER` | `00_INBOX/boards` | boards folder used by the design preview. Real thread board paths come from the broker. |
| `VAULT_GENERAL_BOARD` | `00_INBOX/from-agents/comms.md` | general inbox board path whose quick notes use the runtime’s dated UTC format. |
| `VAULT_ATTACHMENTS_FOLDER` | `98_ATTACHMENTS` | vault upload subtree used to resolve older absolute attachment paths; relative paths from the broker stay intact. |
| `APP_MEDIA_DIR` | `/storage/app-media` | regenerable preview/icon cache and temporary capture profiles. A Loam install sets `/var/lib/loam-app/<app>/app-media`, the unit's state directory. |
| `APP_PREVIEW_CHROME` | `chromium` | capture executable. A Loam install sets Debian's smaller `chromium-headless-shell`. No browser login/profile is loaded. |
| `APP_PREVIEW_OOM_SCORE_ADJ` | unset | the kernel OOM score (-1000..1000) the capture browser starts at, through util-linux's `choom`, so a page that balloons goes before the server. A Loam install sets the agents' 800. |
| `APPS_PREVIEW_BASE` | unset | optional plain HTTP origin of the host’s internal preview route. Sites/projects use `<first-public-host-label>.<preview-host>` with the original path/query/fragment. Browser links retain the public URL. Unset, existing public capture behavior is unchanged. [Routing contract](docs/app-previews.md#optional-internal-route). |
| `APPS_PREVIEW_ADDR` | unset | IP address for that route when its virtual hostnames have no DNS. Chromium receives a scoped resolver rule; the icon reader connects to this address with the virtual Host header. Used only with `APPS_PREVIEW_BASE`. |
| `APP_PREVIEW_TTL_HOURS` | `6` | successful preview/icon refresh interval; catalog version/date changes also refresh. A failed capture retries after at most 30 minutes and retains a previous good preview. [Details](docs/app-previews.md). |
| `APP_PORT` | `8080` | the one HTTP port the server listens on |
| `APP_HOST` | `127.0.0.1` | the address it listens on. Loopback, because the host's proxy is the one public listener; `0.0.0.0` only on purpose. |
| `BROKER_URL` | — | the session broker's base URL, e.g. `http://<address>:<port>`. Unset, the app still serves and the page says no broker is configured. |
| `AVATAR_COLOURS` | `{}` | optional JSON object of persona IDs to semantic persona palette tokens, e.g. `{"persona-id":"persona-plum"}`. Numbered aliases and the six extra Newsprint-era names normalize to the fixed eleven-slot Palette at input. In Palette, only named personas change; all others keep their original ID mapping. Latte tones overrides every agent with its warm ramp. See [palette aliases](docs/design-system/tokens.md#runtime-avatar-references) and [Latte tones](docs/evidence/latte-tones/README.md). |
| `AVATAR_VARIANTS` | `{}` | JSON object of agent IDs to unique permanent face slots 0–7, e.g. `{"persona-a":0,"persona-b":1}`. Shared by Latte pours and signature materials; append assignments without renumbering. Unlisted IDs retain their original hash. Delivered in the existing parser-blocking appearance boot. See [face identity](docs/appearance.md#permanent-face-identity). |
| `BROKER_TOKEN` | — | the bearer token for the broker, supplied at run time. **Never in this repo, never in the image.** The browser never sees it: every call the page makes goes to this app's own `/api`, and the token is added on the way out. |
| `FILES_VAULT_URLS` | — | optional: comma-separated `https://` prefixes of the published vault; the first is the standalone **Copy link** base, and links to any prefix open in Files. No unauthenticated existence probe. Anything not `https://` is ignored. |
| `FILES_WEB_URLS` | — | optional: JSON array of `{root, path, url}` mappings for other published file trees, e.g. `[{"root":"spaces","path":"shared/www","url":"https://files.example.org"}]`. Paths are relative to a Files root; URLs are HTTPS folder bases. The longest matching folder wins. Unmapped files offer **Copy app link**. See [mapping details](docs/files-content-origin.md#standalone-copy-links). |
| `FILES_CONTENT_ORIGIN` | — | optional: dedicated HTTPS content origin on a different hostname; enables scripted HTML previews and relative assets. Requires the host proxy setup in [Live HTML: host handoff](docs/files-content-origin.md). Unset, HTML uses the existing static preview with an explanation. |
| `GEMINI_API_URL` | `https://generativelanguage.googleapis.com` | where Gemini's API is. There to point the tests at a fake; nobody else needs it. |
| `DICTATION_POLISH_MODEL` | `gemini-3.5-flash-lite` | the text model that polishes a recorded take, on the same key. [Polish](docs/session-ui-history.md#polish-a-second-pass-over-the-words). |
| `SPEECH_TTS_MODEL` | `gemini-3.8-flash-tts` | the model a reply is voiced with. Google has no tracking alias for its TTS models (checked 2026-10-02); the app lists the models once at start and logs any `…-flash-tts-latest` it finds, and uses it **only when this is unset**. [Read aloud](docs/session-ui-history.md#read-aloud-a-reply-as-speech). |
| `SPEECH_BRIEF_MODEL` | `gemini-flash-latest` | the text model for spoken briefs, Full’s table/diagram descriptions and thread openings; branch drafting uses the Read aloud plugin key and uses the broker's record-based fallback if generation is unavailable |
| `SPEECH_VOICES` | — | fallback prebuilt voice for each agent (overridden by a stored profile voice), and a few words of its character: `agent=Voice:character;agent=Voice:character`, e.g. `scout=Iapetus:clear and direct`. The agents are this installation's, so they are configuration and never a literal here. An agent not listed — or every agent, when this is unset — reads in `Charon`, informative. A voice that is not one of the thirty prebuilt ones is dropped at start with a log line. |
| `SPEECH_PACES` | — | measured word equivalents/minute per prebuilt voice, e.g. `Aoede=147;Aoede.brief=121;Charon=155`. `.brief` and `.verbatim` override one mode; an unsuffixed voice sets the base pace. Aoede defaults to the supplied Full measurement of 147 and brief measurement of about 121; its brief uses 121 unless `.brief` explicitly overrides it. Other voices inherit their base pace in either mode, provisionally 150 when unlisted. Invalid entries are logged and dropped. Synthesis and cache checks use the same pace. |
| `SPEECH_DIR` | `/storage/speech` (a Loam install: `/var/lib/loam-app/<app>/speech`) | where the MP3s and their sidecars are kept. Not writable, and speech says `no storage`. |
| `SPEECH_DOCUMENT_MAX_CHARS` | `250000` | the character cap for a Files note, separate from the chat reply cap. Full reads retain document seams when chunking; briefs pass the document to the writer in one request. |
| `SPEECH_TTL_HOURS` | `48` | how long an MP3 is kept **from its last play**. Swept at start and hourly. |
| `SPEECH_MAX_CHARS` | `60000` | the longest text one request will read; over it is a `413` |
| `SPEECH_CHUNK_CHARS` | `1000` | the target ceiling for one TTS call; a single indivisible sentence may exceed it. Full documents prefer headings, paragraphs, list items, then sentences near a balanced target. Chat and brief prose retain balanced paragraph/sentence chunks. |
| `SPEECH_CONCURRENCY` | `4` | how many chunks are voiced at once **per job**; several jobs can exceed that total. Tune against the project's model quota, not just document length. |
| `SPEECH_BRIEF_MAX_WORDS` | `300` | the brief's hard ceiling; it is asked for about 220 |
| `SPEECH_FFMPEG` | `ffmpeg` | the encoder's binary. The image installs it; set this to run the real-encoder tests against one that is not on the path. |
| `LOG_LEVEL` | `info` | how much the app writes to stdout: `debug` adds a line per static file, `info` is every other request and every transcription, `warn`, `error`, or `off`. Anything else reads as `info`. [Logging](docs/session-ui-history.md#logging). |

See [logging and request diagnostics](docs/session-ui-history.md#logging).


Latte & Newspaper's optional **Latte art** agent shape draws coffee pours or foam initials,
with an optional bone-white mug. It uses the same host-owned Appearance settings
as the other avatar choices. See [appearance behavior and storage](docs/appearance.md#latte-art-avatars)
and [verification captures](docs/evidence/latte-art/README.md).

Roster initials share the avatar component's typography and ink; the name
label has its own styling. [Rendered regression checks and 390/1440 captures](docs/evidence/persona-initials/README.md)
cover all eleven Palette hues, Latte tones and every shape in light/dark.

Claim, first-provider setup, the hello thread, passkeys and the manual Update
control are described in [the U4 guide](docs/first-release-u4.md).
The standalone claim page embeds the bundled Latin Caprasimo and Figtree faces.
After refreshing `public/fonts/`, run `node tools/embed-claim-fonts.js` to refresh
its generated font block; `npm test` checks for drift. The claim CSP permits
only `data:` fonts and no external assets. [U5 evidence](docs/evidence/first-release-u5/README.md)
covers its typography, Sign-in alignment and the development Update state.
