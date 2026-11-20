# Activity

Activity opens from the first row of the phone Settings sheet, or from its
own button at the foot of the desktop rail. The phone row uses the shared
Settings row component, with a clock and a current work summary (for example
“2 working” or “Nothing running”), separated from the seven settings sections.
Back returns to the previous destination. It shows **Server resources**, **Running now** and **History**, in that order.
On desktop (1100 px and wider), every Activity entry uses the centered
960 × 720 dialog, matching Settings. There is no desktop Activity page.
Widening an open phone Activity screen restores its previous destination and
opens Activity above it; narrowing the dialog returns to the phone screen.
See [Apps list and Activity validation](evidence/apps-list-activity/README.md).
Phone Settings and desktop Activity have no notification indicator, including
while work is running. The Settings summary counts only
sessions whose normalized state is Working,
plus occupied job slots and landing work. Idle sessions, sessions waiting on
an answer or background work, free slots and queued jobs add nothing. Aggregate
residency totals cannot say which sessions are working and do not enter the
summary. The complete
[application shape](app-shape.md) describes navigation, Settings and Files.

The shared [live session service](running.md) supplies the resource panel.
Memory shows used and total RAM and the estimated number of additional
sessions admission would allow. CPU compares the one-minute Linux load average
with the online vCPU count. It includes tasks waiting for I/O. Storage shows
allocated bytes, filesystem capacity and space available to an ordinary user.
Filesystem-reserved blocks account for the difference. Below 10 GiB free or
at 90% of writable capacity used, the panel explains the risk to saving work.

The disk is the filesystem containing the configured vault. Storage breakdown
sorts allocated space by size, with the time of the survey. The vault excludes
its conversation records, which are grouped with thread and broker session
records. Workspaces and the standard Docker and containerd data directories
count only on this disk. System and
other files is the remainder at survey time. Every 30 minutes the existing
low-priority memory sampler refreshes a small, atomically replaced cache in the
log directory. The live route reads that cache and samples filesystem totals
independently, including before the first breakdown is ready.

Running now shows sessions with their thread, state/age, supplied memory and End,
then occupied job slots, landing work and a nonempty queue. Idle endings recheck
the host; working/asking/background endings require confirmation. End all idle
uses the existing atomic host action. Memory/admission calculations stay on the
host. Free slots and empty Queue headings are omitted.

History combines thread activity and the host history, newest first. One filter
row has All / Chats / Runs / Jobs / Changes chips and an agent menu. Runs requests
`kind=fire,run`; Changes requests `kind=change,refused`. Threads use the same event-row shape as every other history entry, with a
36px face, title, state or last line, trailing local time and divider. Chats
itself keeps its normal unread treatment. Profile-scoped history uses the same
renderer and has no agent menu.

`GET /api/activity` relays agent/kind comma lists, limit and offset to
`GET /v1/activity`. The supplied response is
`{items,total,next_offset,unread,jobs,jobs_error}`. The page ignores `unread` and
item `seen`. Pagination follows `next_offset`; refresh preserves the opened
window in batches of at most 200. Generation checks reject stale filter reads.
The host's jobs error does not hide a successful history feed.

Event titles wrap to two lines. Outcome and local 24-hour time span/duration
share the secondary line. Failed or cut-off outcomes use a heavier
weight, with their reasons in secondary text. Threads use a trailing local time. Every day heading
uses the same small-caps section-label style as the live groups. Browser time
zones are honored, verified in Los Angeles and Tokyo.
Day headings are Today, Yesterday, then weekday/day. Jobs have `started` and a
completion `ts`; fires use `started`/`finished`. A null fire finish does not
invent a duration. Changes/run requests show their timestamp alone. Refusals
carry `why`; fires may carry `produced.line.error`. Failures retain the host's
reason. A `stage` is not a failure reason. Unknown values stay unknown.

Opening an item, by pointer or keyboard, opens its supplied receipt, produced
Vault reference or failure log through Files. No path is made from an ID. With
no supplied reference, a native detail dialog shows the data. Changes/refusals
keep their raw Before → After, reason and Undo there. Undo errors stay visible;
Escape/Close returns focus to the row. Jobs retain their separately confirmed
Cancel action. Cancelling a running job records durable cancellation; it does
not kill the process. The confirmation says this explicitly.

Activity sends no seen marks, on entry, refresh, item opening or profile history.
`POST /v1/activity/seen` and classic `POST /v1/sessions/seen` are no longer relayed.
Schedule-specific seen operations remain in use. Opening a real thread still
uses the ordinary thread reading behavior.

History refreshes every five seconds while Activity/profile history is open,
and every thirty seconds otherwise so the Settings work summary stays current.
Hidden documents do not poll. Session residency is streamed, never counted from
history. A 404/405 says Activity is unavailable; mutation errors retain the
broker's sentence. The [supplied host contract](broker-schedules-and-activity-api.md)
is historical API documentation; its Activity seen fields are unused here.

`npm test` checks fixtures, filters, pagination, job/Undo/refusal handling, no-seen
behavior and the page at both requested widths and modes in the DOM harness.
These checks prove structure/interaction; staging still needs visual review.

The Settings/Activity review separates Now into Conversations, Engineers, and
On their own. Job context, model/provider labels and continuation folding use
the host’s explicit fields. See [the current app shape](app-shape.md#activity)
and [the before/after review](evidence/settings-activity-review/README.md).
