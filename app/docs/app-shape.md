# Application shape

The navigation has three sections, in order: **Chats**, **Files**, **Apps**.
Desktop puts **Activity** and **Settings** at the foot of the rail. The phone
has four bottom-bar controls: the same three tabs and Settings. Activity is
the first Settings row, above and apart from the settings sections, with a
live work summary. Neither Settings nor Activity carries a notification
indicator. Settings stays selected while phone Activity is open; its Back button
returns to the previous destination (including the Files location). Appearance
is reached through Settings; its former rail toggle is removed.

The settings sections are declared in `public/app-shape.js`:

| Section | Contents |
| --- | --- |
| Account | Who is signed in (name, username), the password, passkeys, Sign out. The Loam account will live here later |
| Appearance | Theme, mode, size |
| Agents | Agent profiles, workspace and schedules; the system ones under them |
| Connections | One page, three groups: AI providers (plan, usage, sign-in), Services (GitHub and any a plugin adds, then each service's accounts), API keys (by provider, entered once) |
| Plugins | Themes and extensions; plugin preferences while on; who made it, version and View source in detail; the Market |
| Files | File folders and preferences, shortcut removal; links remain on Files home |
| About Loam | The version and Update, and this server (address, processors, memory, storage) |

Seven since 2026-11-18 (the owner: "love it", to this structure). On a phone
Activity stays Settings' first row and the seven follow in this order. **The
word "Accounts" never names the API keys**, anywhere: it is kept for the Loam
account under Account.

Every id that was a section before still opens (`settingsHome` in
`public/app-shape.js`): `providers`, `integrations` and `accounts` open
Connections at their group (`accounts` with a provider is that provider's API
key page, `api-key`), `signin` opens Account and `update` opens About Loam.
Voice, Dictation and Read aloud resolve to Plugins. Provider, service and API
key pages return to Connections. Old Running now requests resolve to Activity.
The thread provider/model sheet remains about the current thread.
The developer preview remains available at `/?threads=fixture`; its Settings
shortcut is removed so a host outage does not introduce another section.

Plugins uses the validated host catalog. Theme selection uses the Appearance
route; extension enablement uses the existing plugin route. Metadata and
capability declarations live in each detail page, and installation still
requires a reviewed declaration. No new browser-only setting is introduced.

Integrations groups GitHub's personal connection and organization apps under
one service. The account page shows repository scope, use, renewal and the
GitHub App details one level down. Refresh and disconnect currently affect
all GitHub accounts, so they are labelled explicitly and disconnect confirms
that scope. See the [registry handoff](settings-sections.registry.json) for
route gaps; this repository does not change the host.

## Activity

Activity has **Now**, then **Earlier**. Now groups **Conversations**, **Engineers**
and **On their own** (only while a wake, scheduled run or review is active).
Conversations lead with the thread title and use the composer’s model label.
An engineer has a neutral code mark with the briefing persona’s corner badge,
job title, caller/thread link, project, provider, model and elapsed time.
Related rows share the thread link and the conversation says “Waiting on an
engineer”. Groups stay separate so each retains its End or Cancel action.
Empty groups disappear; all empty says “Nothing running”.

Earlier folds explicit job chains into their newest outcome and receipt, with
the number of continuations. Missing old job metadata is omitted.

Now embeds the existing shared live
session service: one used/total memory and capacity line, session title and
agent, state/age, supplied RSS and End. Working/asking/background ending still
requires confirmation, and idle ending still rechecks the host. When the host
supplies job/wake memory totals, the machine line expands to retain that
breakdown without another permanent line. End all idle
remains available. Jobs, landing work and a nonempty queue follow the sessions.
Empty slots and an empty Queue heading are removed.

Earlier merges history and thread activity, newest first. One filter row holds
a single segmented filter and the agent menu. Every history entry uses the same 36px-face event
row and rule. Threads show their title, state or last line, and a trailing local
time. Event titles wrap to two lines; outcome and time span share the secondary
line. Failed/cut-off outcomes use the danger token; landed stays quiet. Day
headings and live group headings share the same small-caps section label.
Neither global nor profile-scoped Activity renders seen state or sends read marks.
Chats retains its ordinary thread unread treatment. Opening an actual thread
still reads that conversation through the existing thread API.

The navigation buttons stay quiet while sessions or jobs run; work information
lives in the phone Settings summary and Activity's Now section.
There is no count or Activity mark-all action. Both `/v1/activity/seen` and the
classic `/v1/sessions/seen` are unused by this app and no longer relayed. The
schedule-specific `/v1/schedules/:agent/:name/seen` remains in use.

## Files: two roots, no System tree

Files home and its attachment picker show only **Vault** and **AI Spaces**.
Host-provided Vault shortcuts and personal shortcuts share one group beneath
those roots. The prior temporary Host records collection has been removed,
including from the picker.

The staging inventory supplied for this follow-up identified `Engineer/`,
`Reviewer/`, `Deployment.md`, `Logs.md` and `Session broker.md` in System.
Removal is now an explicit product decision, replacing the previous preservation
exception. These resources are no longer browsable through the System tree.
No host file is deleted or moved. Vault and AI Spaces remain available.

Old `/files/system...` URLs and saved System shortcuts land on Files home,
replace the stale address with `/files`, and show one quiet line: “That location
is no longer shown.” The normal unsaved-edit guard still applies. Shortcut
removal remains available in Settings. No System stat/list/read is requested.
The relay refuses System-specific queries and body-based writes locally;
shared Files routes remain for the two supported roots. Old System content
capabilities are refused as well.

## Apps

`GET /api/apps` relays authenticated, uncached `GET /v1/apps`. Its supplied
contract is `{apps: [...], sources: ["yours"]}`. Rows carry `name`, `url`,
`also_at`, `source`, `kind`, `owner`, `project`, `access`, `expires`,
`shared_until`, `serves`, `frame`, `created`. No installation inventory is
hard-coded. Yours precedes Installed; empty groups and a lone group heading are
omitted. Desktop rows show the name and one author/access line; a small badge
appears for end dates within fourteen days. A small rounded page thumbnail
identifies each app; the author’s small face appears once in its quiet line. Main selection and the side-panel mark
follow the existing panel controller. The side action appears on hover/focus.
Phones use a two-column grid of rounded 16:10 preview cards with the name and
author below. Browser-only apps have one quiet marker. The ⋯ stays inside the
card with a 44px target. Long-press and right-click also open the shared details
sheet/menu:
author, access, address, full end date, Open in browser and Copy link. No
ownership/access/end-date information is removed from that details view.
The toolbar anchor and button share the same
aligned, non-underlined pill layout. Unknown owners retain
their supplied ID. An empty catalog offers to start a thread to build an app.
404/405 shows an unavailable message and retry, rather than the empty catalog.

At desktop width the catalog occupies the left list column, beside an empty
“Pick an app” main pane or the selected app. A frameable HTTPS site on a
subdomain of the configured apps domain opens in the main pane; its row's
panel icon or Alt/Option-click opens it to the side, following the shared
[panel rules](wide-panels.md). Its bar offers Back to apps, the address, Open in browser and
Copy link, with visible copy success/failure. Other origins, `frame:false`, and
all phone rows open a separate browser tab. Unsafe protocols and credentialed
URLs are rejected. Frames have accessible titles and a sandbox permitting
scripts, same-origin site storage, forms, downloads and external links, while
preventing navigation of this app's top-level page.

The app's `frame-src` adds only `https://*.<domain>` (the configured `APPS_DOMAIN`) beside its existing
self/blob and optional Files content origin. The narrow host platform wildcard
is the supplied contract; no particular deployment hostname is embedded. App
scripts/styles retain their self-only policy. Each site must still permit the
app's origin through its own `frame-ancestors` response. The follow-up staging report confirmed live framing. This job verifies computed
styles and interactions in a local Chromium with fixture apps; it does not
claim another live deployment check.

`GET /api/apps/media` returns same-origin preview/icon URLs and background-job
state for the current catalog. `GET /api/apps/media/image?url=…&kind=…` serves
verified cached bytes after checking the catalog again. The older icon route
returns only a resolved same-origin icon for existing tabs. No conventional
icon paths are probed by either the client or metadata reader.

A single isolated Chromium process captures one frameable page at a time,
with an empty temporary profile and a minimal environment. It loads the
catalog URL from the container’s network, without a browser login or broker
bearer. Failed, inaccessible and blank-canvas captures use a designed tile.
Fallback letters use a distinguishing name part (including a different letter
when sibling suffixes share a prefix); deterministic palette allocation gives
neighboring apps distinct persona-accent colours, with their paired inks.
A published icon may appear as a small mark on the phone card.

See [page capture, caching, configuration and boundaries](app-previews.md) and
[before/after evidence](evidence/apps-previews/README.md). Chromium and fonts
are system packages in the image, with no npm dependency or new broker route.

## Settings tool parity and effort

No new writable setting is introduced by this regrouping. The six Appearance
keys and their route remain in
[appearance-settings.registry.json](appearance-settings.registry.json).
[Settings destinations and parity handoff](settings-sections.registry.json)
records the new homes and existing integration gaps for the runtime owner.
This repository does not contain or install the host settings-tool registry.

Agents links to workspaces and schedules, and directs model selection to the
existing thread controls. The supplied agent catalog has only ID/name/emoji;
it has no per-agent default model or model-write route. A global agent-default
model editor therefore remains a runtime contract requirement, rather than a
model value guessed from a provider default.

The active thread UI can PATCH `engine:{persona:provider}` and
`model:{persona:model|null}` on `/v1/threads/:id`. Null restores the agent's
default model. It exposes **no effort editor**, either per thread or member.
Old comments/provider parsing and historical turn-effort metadata are not
controls. Removing `session.effort` from a host tool does not remove parity with
an active page control. The runtime should expose the same thread provider/model
operations to personas; its current tool coverage cannot be verified here.

Plugin dictation/reading preferences and Files shortcuts/folders remain the preexisting
device-local preferences. They have no broker read/write route in the supplied
contract. Full persona parity for these settings needs a host-owned settings
contract and registry integration, followed by binding the page to that same
route. No guessed persistence API or second store is added here. Appearance
already documents its host integration requirements separately.

## Removed relay routes

The classic page no longer calls these operations, and the relay rejects them
without contacting the broker:

- `POST /v1/sessions`, `POST /v1/sessions/seen`
- `PATCH /v1/sessions/:id`
- `GET /v1/sessions/:id/events`
- `POST /v1/sessions/:id/messages`, `/attachments`, `/compact`
- `POST /v1/sessions/:id/permissions/:request`
- `POST /v1/activity/seen`

Live residency JSON/SSE, ending and the older residency fallback's listing,
record read/delete and list SSE remain: they still have callers. Old conversation
addresses continue through the imported-thread mapping, never the classic log.

## Checks

`npm test` includes a full-page DOM interaction matrix at 390 and 1440 in light
and dark, plus Apps fixtures, relay/CSP, no-seen Activity, retired System soft landings,
theme/runtime-avatar tokens, service-account flows and the existing regressions.
Run the matrix alone with `node --test test/app-shape.test.js`.

The DOM harness checks behavior, structure, state and requests. The follow-up
also supplies `test/shape-staging-browser.mjs`, a real Chromium regression matrix
at both widths in Latte/Plain light/dark. It checks every touched interactive
surface against resolved token colours/borders, and deliberately restores the
old browser-button styling to prove the guard catches it. It checks toolbar
alignment, focus/selection, history wrapping, System soft landings, keyboard
thread opening, and local times in Los Angeles and Tokyo.

Run it with an optional Playwright installation outside the repo:

```sh
LOAM_PLAYWRIGHT=<path-to-playwright-index.mjs> \
  node test/shape-staging-browser.mjs
```

`SHAPE_EVIDENCE` optionally saves screenshots and the computed-style report.
The same check runs in `npm test` when `LOAM_PLAYWRIGHT` is supplied. The browser
and its libraries must be available in the caller's environment. No runtime
dependency, setting, broker change or deployment is introduced by this test.
