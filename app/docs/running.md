# Running now

Live sessions now live in **Activity → Now**, beside jobs and the queue.
The duplicate Chats header button and Settings section are removed. On the
phone Activity has its own utility beside the tab bar; desktop uses the rail
foot. The icon shows a small live dot while a session or job runs, with no count.
See [application shape](app-shape.md).

The machine's one line shows used/total memory and the runtime's “room for
about N more”. When supplied, job/wake memory totals remain available by
expanding that line. Unknown memory or capacity is hidden; the page never
calculates admission capacity or estimates session RAM.

Rows group **Working** (including background work), **Waiting on you**, then
**Idle**. Permission requests and questions outrank other activity. Each row
has the agent, conversation title, state/age, RSS when supplied, and End.
Tapping a row opens its thread; an older conversation resolves through the imported-thread mapping. A thread member's dot means a
turn is in progress: the live view says `working` or `asking`. Warm idle and
background-only sessions have no member dot. Thread list rows use the same
rule. **End session** remains available for every resident member session,
independently of the dot.

An idle End acts immediately. Working, waiting and background sessions ask
in their own row: “This stops <Agent>’s turn in progress.” The loss signal from
the runtime also requires confirmation, even if its state is unfamiliar. End
stops the process; it never deletes, archives, unpins or rewrites a thread or
chat. The toast uses “They’ll pick up where they left off next time” because
the agent catalog has no pronoun field. **End all idle** appears at two idle
sessions and uses the host's atomic action. Sessions it reports as just active
stay running; a zero-end result explains that rather than silently doing nothing.

## Runtime contract

Implemented against the runtime's Live sessions interface at `f0bc5d3`, dated
2026-11-10. The browser calls `/api`; the authenticated relay uses matching `/v1`
paths. No credential or host path reaches the browser.

| Route | Meaning |
| --- | --- |
| `GET /sessions/live` | `{sessions, box, at}`; only resident CLI sessions |
| `GET /sessions/live/events` | `sessions.live`, the entire answer above, on open and when it changes; no cursor |
| `DELETE /sessions/:id` | Stops one process; its durable id still resumes. Thread turns settle on the host. |
| `POST /sessions/end-idle` | `{ended:[{id,agent}], kept:[{id,agent,why}]}`; host checks activity, thread turns and its recent-activity grace before each stop |

Each live row carries `id`, `agent`, `engine`, `model`, `kind`, `thread_id`,
`thread_title`, `title`, `state` (`working`, `asking`, `background`, `idle`),
`last_active`, `idle_since`, `started_at`, `rss_mb`, and `ending:{loses_work,text}`.
Thread mapping is explicit; no title matching, agent matching or CLI id inference
is used for navigation. The gauge reads `box.ram_used_mb`, `ram_total_mb`,
`can_start`, `sessions.live`, and `units.engineer_jobs`/`wakes` (`count`, `mb`).
Reserve and admission arithmetic stay on the host. The interface does not yet
supply an idle timeout/deadline, so the page does not invent “steps out at 10”.

One slim live stream stays connected across the page. The session service
retains its shared ordinary record subscription seam for older-runtime readers.
The retired classic page no longer subscribes. Neither stream
uses a cursor. A `sessions.live` snapshot replaces the whole resident set, so
ended processes disappear. The shared connection watchdog reconnects after a
drop and on returning to a stale page. Healthy connections do not cause JSON
polling. Authentication expiry stops both. A failed initial read can retry on
the existing page heartbeat until it can attach its stream.

## Older runtime fallback

A 404 or 405 on the live route selects `GET /sessions?archived=1` and the
ordinary `/sessions/events?archived=1` stream. This includes archived thread
harnesses. Classic subscribes to that same stream without duplicating it.
Listing `live_count` (older name `live`) and `broker.residency` supply the badge;
record `live` controls inclusion, with process `state` as the oldest fallback.

Memory and deadlines remain hidden. The older record's `thread_id` is a **CLI
thread id**, not a Loam thread id. Archived rows can be ended but have no
conversation link or inferred member dot; ordinary classic rows resolve to their imported thread.
Before idle ending, the page re-reads the record. A new active turn makes a
single End ask for confirmation and makes bulk idle ending skip it. Old bulk
ending uses individual DELETEs; its read/DELETE race cannot be eliminated on
that older interface. The new atomic route removes that bulk race.

The implementation is `public/running.js` and `public/running-record.js`.
[Evidence](evidence/running/README.md) distinguishes live verification from
synthetic visual data and lists the reproducible checks.
