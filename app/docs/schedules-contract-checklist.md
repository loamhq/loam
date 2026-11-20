# Schedules and Activity contract closure

The numbered rows correspond exactly to the mismatch list in the
[supplied contract](broker-schedules-and-activity-api.md). That document is
copied verbatim from the brief; its installation-specific examples are supplied
reference material, not application configuration. Production and fixture code
uses configured or parameterized agent identities.

Evidence: [schedule HTTP/controller tests](../test/schedules.test.js),
[Activity tests](../test/activity.test.js),
[phone DOM tests](../test/schedules-ui.test.js),
[demo integration test](../tools/fake-broker.test.js), and
[browser assertions and screenshots](evidence/schedules-activity/README.md).
The browser checks run real app/relay code against contract fixtures, with no
authenticated broker, service start, real job cancellation or deployment.

| # | Status | Page behavior and evidence |
| --- | --- | --- |
| 1 | Closed | Reads `{schedules, unread}`; host owns the count. List test. |
| 2 | Closed | 400 sentences stay visible; missing agent/detail/change/job retain 404 and sentence through the relay. 405 identifies an unavailable route. HTTP tests. |
| 3 | Closed | Displays `name`, requires its lower-case/digit/dash form on create; no title is sent. Both-create test and browser. |
| 4 | Closed | Uses recurring, one-off, reflection, unknown. List and unknown/error browser checks. |
| 5 | Closed | Days are words in the schedule input with visible grammar help; no numeric days payload. Exact edit/create assertions. |
| 6 | Closed | Time is in schedule/at words, never a separate wire key. Exact payload assertions. |
| 7 | Closed | One-off sends `at: YYYY-MM-DD HH:MM PT`; row display uses schedule/words. Create/edit tests. |
| 8 | Closed | Visible PT-only help; no IANA timezone field sent. Next/last instants display in PT. Browser and payload tests. |
| 9 | Closed | Create sends name, schedule or at, body, note as applicable. PATCH sends changed allowed fields; toggle sends enabled. Exact payload tests. |
| 10 | Closed | Reflection protection/order checks kind, without a boolean reflection field. DOM/browser tests. |
| 11 | Closed | Instruction field reads/writes body; instruction is only the broker's file reference. Body edit test. |
| 12 | Closed | Reflection instruction lives in the editable schedule record. |
| 13 | Closed | Note is a string, including empty; schedule/at edits follow host normalization on the next read. One-off edit test. |
| 14 | Closed | Done one-off disappears on a later list read while detail and Activity remain. Fire/visible refresh tests and browser. |
| 15 | Closed | Reads next_fire, null when paused. Pause/list tests and screenshots. |
| 16 | Closed | Reads last_fire.ts/outcome/stage; no invented reason. List/render tests and screenshots. |
| 17 | Closed | Paused/completed UI explanations; host's run refusal shown verbatim, including service-not-installed sentences if returned. Paused/run controller test. |
| 18 | Closed | Run now shows Run requested, without manufacturing a successful fire. Separate later fire in fixtures, tests and browser. |
| 19 | Closed | Named actors resolve through agents; owner → you, engineer → the engineer, loam → Loam. Shared actor/render tests. |
| 20 | Closed | Change timestamps read ts. DOM/browser change block and Activity tests. |
| 21 | Closed | Shared component renders each diff.header raw before → after pair and body_changed/created/deleted. No full record snapshots expected. DOM/render tests. |
| 22 | Closed | No undoable or undo_reason expected. Undo asks the host; older-change 400 stays inline. Controller/browser tests. |
| 23 | Closed | Reads agent/record/op/lane/kind; undo success is a new event with undoes. Undo tests. |
| 24 | Closed | Marker uses unseen IDs; refusal-only marker works and opening marks those IDs. Shared-seen test and live browser check. |
| 25 | Closed | Latest refused.why and named actor are visible even if saved fields did not change. Refusal browser screenshot and controller tests. |
| 26 | Closed | Reads words, agent and error; unknown rows remain visible. Timer stays broker bookkeeping. Browser unknown/error screenshot. |
| 27 | Closed | Run/seen/undo POST `{}`; reads `{marked, unread}` via subsequent list and `{event, schedule}` mutation result. HTTP tests. |
| 28 | Closed | Unchanged save reaches host, displays “that changes nothing in the schedule” and retains editor. Controller and browser tests. |
| 29 | Closed | Feed GET, seen POST and jobs cancel POST were already allowlisted at job start. Verified bearer/query/body/status forwarding. Added GET agent and DELETE reason forwarding. HTTP/demo tests. |
| 30 | Page closed; live host unverified | The supplied contract says the running host predates these routes and a restart was scheduled. This checkout has neither BROKER_URL nor BROKER_TOKEN configured. No deployment/restart was authorized. Page handles unavailable routes; fixture/browser evidence establishes compatibility, not current host availability. |

Both pages use the existing five-second heartbeat while open and visible;
Activity's badge refreshes every thirty seconds elsewhere. No supplied stream
carries these domains. Poll tests and the browser cause a remote change while
the page stays open, verify it appears, and verify it remains unseen until an
explicit seen action. Schedule polling preserves unsaved drafts.

Two corrections beyond renamed fields: the cancel dialog now explains that a
running process is not killed, and Activity remains readable when jobs is null
and jobs_error is present. Refreshing an opened feed longer than 200 items uses
multiple bounded requests, preserving its earlier items. Each has a controller
test against the contract fixture.
