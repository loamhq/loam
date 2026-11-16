# Classic chat retirement

Follow-up: the [application shape](app-shape.md) consolidates navigation and
removes the remaining classic-only relay routes and Activity seen action.
The inventory below records the original retirement.

Decision: retire the session-based chat page in favor of threads (2026-11-11).
The history migration is a broker responsibility; this change does not migrate,
archive, end or rewrite a session. Its scope is the page and one relay route.

Chats always renders the thread list or a thread. A thread API refusal remains
on that list with its sentence; it cannot open the classic page. Settings has
no Classic chats entry. Classic-only Chat preferences (session preview bubbles,
context warning/compaction controls) also go; Read aloud retains its dedicated
Settings page and thresholds. Profiles, schedules, Activity, Providers,
integrations, Files, Appearance and Running now remain.

The archive includes all archived thread summaries from the normal thread API.
It sorts by `last.at`, falling back to `created`, descending regardless of pins,
so old pinned imports do not leap ahead of newer history. Dates on rows use
those original instants and expose their full local date/time on hover. Search
matches titles and last text. The page carries no copy of the migration count.

## Legacy references

`GET /api/threads/imported[?session=<id>]` relays to the same broker path under
`/v1`, with bearer auth, no cache and only the optional `session` query. This
reserved route is matched before the generic thread-ID route; POST is refused.

A `/sessions/<id>` address, `?session=<id>`, pre-retirement `loam-ui.session`
choice or service-worker message carrying `session_id` goes through this lookup.
Successful resolution opens `/threads/<mapped-id>`. Explicit modern thread and
Files addresses outrank old storage. The obsolete storage key is still removed.
A missing mapping or failed lookup shows an error on the thread list. It never
uses the session ID as an invented thread ID and never fetches a classic log.
Later navigation supersedes an outstanding lookup. Unsaved Files edits retain
the existing leave guard.

The brief supplied the route, but no response schema, accessible migration
receipt or live broker reading. `imported-threads.js` therefore accepts explicit
`thread_id`, `thread.id`/`thread`, and inventory entries under `imports`,
`imported` or `threads`, matching their session identifier. A single filtered
thread summary can also identify the result. These shapes are compatibility
handling, not a claim that each is emitted by the live broker. Confirm its
actual response with the migration owner before treating the live redirect as
verified. Evidence here uses synthetic mappings and 153 synthetic archived
threads; no private conversation text or installation inventory was copied.

## Removed code

Production modules removed in full:

- `public/seen.js` — classic session seen-mark throttle/lifecycle.
- `public/select.js` — classic multi-session selection and bulk actions.

`public/app.js` loses `goClassic`, `startClassicListing`, `state.classic`, the
classic list/read-mark subscription, list headings/filters/selection, classic
chat screen/context/loading/history window, new-session draft/send/resume path,
classic action sheets, classic model menus, session attachment upload branch
and fallback navigation. Old classic headings are removed from `running.css`.

`chat-ui.js`, `permission-cards.js`, `thread-steps.js`, `transcript.js`, `link.js`,
`sse.js` and `sessions.js` remain shared. In particular, nested subagent steps
still fold recursively and retain full inputs/outputs. `sessions.js` still
supplies identity/time helpers and `link.js` still drives thread/Running now
connections. Activity's Chats rows now come from the thread list; its mark-all
operation reads unread threads through the existing thread interface.

Classic-only test entry points and their helpers are removed; the exact file
inventory is [removed-files.txt](evidence/retire-classic/removed-files.txt).
Mixed browser drivers retain their thread checks and lose classic comparisons.
Shared recording, Files picking, provider, frame and schedules tests now enter
through threads or Settings → Agents. `test/thread-page.js` supplies the small
synthetic thread runtime for shared feature tests.

## Broker route handoff

The relay still allowlists these routes for a separate broker/relay retirement.
The page no longer calls:

| Route | Retired use |
|---|---|
| `POST /v1/sessions` | Create or resume a classic conversation |
| `PATCH /v1/sessions/<id>` | Classic title, pin, archive, model, effort, seen/unseen |
| `POST /v1/sessions/seen` | Mark every classic session read |
| `GET /v1/sessions/<id>/events` | Classic opening, reconnect and earlier turns (`last`, `from`, `before`) |
| `POST /v1/sessions/<id>/messages` | Classic composer send/retry |
| `POST /v1/sessions/<id>/attachments` | Classic composer upload |
| `POST /v1/sessions/<id>/compact` | Classic manual compaction |
| `GET /v1/sessions` without `archived=1` | Classic home/profile listing |

Do **not** remove these shared session routes:

- `GET /v1/sessions?archived=1`, `GET /v1/sessions/events?archived=1`,
  `GET /v1/sessions/<id>` and `DELETE /v1/sessions/<id>` — Running now's
  compatibility listing and ending/rechecking.
- `GET /v1/sessions/live`, `GET /v1/sessions/live/events`,
  `POST /v1/sessions/end-idle` — richer Running now support.
- `POST /v1/sessions/<id>/permissions/<request_id>` — thread permission cards.

Agent/provider/limits routes, thread routes, speech, files, schedules, Activity,
login and integration routes remain in use. The new imported-thread lookup is
also retained for old links and notifications. Removing a broker route may
require checking clients outside this page; this inventory states this page's
calls only.

## Evidence

[Captures, commands and results](evidence/retire-classic/README.md) cover Settings,
153 archived fixtures, title search, imported history, legacy references and
failure paths at 390/1440 in both themes. No deployment or release is part of
this job. Existing history/evidence remains historical; the previous README is
kept in [session-ui-history.md](session-ui-history.md).
