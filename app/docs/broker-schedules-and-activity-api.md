# The broker's Schedules and Activity API — the real contract

What the broker answers on `/v1/schedules…`, `/v1/activity…` and `/v1/jobs/<id>/cancel`, as of the `agents` commit `73462b2` ("The broker serves schedules and activity", 2026-10-14). Written for the loam-ui engineer, who cannot read the `agents` repo: the Schedule tab (loam-ui `0b0f6f9`) was built against a guessed contract, and the Activity page is being built the same way. The mismatches with loam-ui's `docs/schedules.md` are listed at the end.

**How every response here was produced.** Each route was called in process through the broker's own `Server.route` (the same dispatch the HTTP server uses), on 2026-10-15 ~01:08 UTC:

- **Reads against the live box, read-only.** The live records (`ai-labs/*/crons/*.md`), the schedule log and the seen store hashed the same before and after (`117c71d5966942b5` both times). Timers are the real user manager's.
- **Writes against a scratch copy** of Ada's records and the schedule state, with a stand-in `systemctl` that starts nothing and answers every standing timer's next fire as `2026-10-21T12:00:00Z`. That is why every `next_fire` in the write examples is the same instant; on the box it is the real timer's. The job cancel used a scratch project with one queued job.
- **Fire items** come from `record_fire()` (what `bin/wake` calls) in the scratch log, matched to two real lines of the live `logs/wake-ada.jsonl`. The live log has no fire, run or refusal yet: it began at 00:16 UTC with the migration.

Long bodies are cut with `…`. No field was removed. Where a list repeats one shape, one entry is shown and the rest are counted.

**Not live yet.** The running broker started at 15:48 PDT on 10-14, before `73462b2` landed at 17:44, so it does not serve these routes yet. They go live when the broker restarts. One is scheduled for 22:00 PDT 10-14 (`loam-broker-restart-20261014-2200.timer`).

## General rules

- Every write is `loam_schedule.change()` with actor `owner` and lane `app`. It is validated, logged and reconciled the same way an agent's or an operator's change is.
- **Errors** are `{"error": "<sentence>"}`. **400**: a refusal; the sentence is meant to be shown to the owner as it is. **404**: the agent, schedule, change or job named does not exist. **405**: no such route (`{"error": "no route for PUT /v1/schedules/ada/infra-watch"}`).
- Times are ISO UTC (`2026-10-21T12:00:00Z`) everywhere except `schedule` / `at`. Those are the record's own words, in PT.
- **Request bodies are JSON.** A key the route doesn't know is refused on create and edit (see refusals). A body on a route that takes none is ignored, so `{}` is fine.

## Routes

| method | path | body | answers |
|---|---|---|---|
| GET | `/v1/schedules[?agent=<agent>]` | — | `{schedules: [row], unread}` |
| GET | `/v1/schedules/<agent>` | — | the same, for one persona |
| POST | `/v1/schedules/<agent>` | `{name, schedule \| at (+note), body?, enabled?, reason?}` | `{event, schedule: row}` |
| GET | `/v1/schedules/<agent>/<name>` | — | one row, bare (a fired one-off too) |
| PATCH | `/v1/schedules/<agent>/<name>` | any of `{schedule, at, note, body, enabled, reason}` | `{event, schedule: row}` |
| DELETE | `/v1/schedules/<agent>/<name>` | `{reason?}` | `{event, schedule: null}` |
| POST | `/v1/schedules/<agent>/<name>/run` | — | `{event: run item}` |
| POST | `/v1/schedules/<agent>/<name>/seen` | — | `{marked, unread}` |
| GET | `/v1/activity?agent=a,b&kind=k1,k2&limit=50&offset=0` | — | `{items, total, next_offset, unread, jobs, jobs_error}` |
| POST | `/v1/activity/seen` | `{ids: [...]}` or `{all: true}` | `{marked, unread}` |
| POST | `/v1/activity/<event-id>/undo` | — | `{event, schedule: row}` |
| POST | `/v1/jobs/<job-id>/cancel` | — | `{job, said}` |

## The schedule row

These are every field, in the order the broker writes them:

| field | type | what it is |
|---|---|---|
| `agent` | string | the persona (`ada`) |
| `name` | string | the record's name and the route id: lower-case letters, digits, dashes. There is no separate title. |
| `kind` | `"recurring"` \| `"one-off"` \| `"reflection"` \| `"unknown"` | `unknown` only for a record that doesn't parse (see `error`) |
| `schedule` | string \| null | the record's words. For a recurring row it is the `schedule:` line (`"Wednesdays at 05:00 PT"`). For a one-off it is the `at:` line (`"2026-10-16 14:30 PT"`). |
| `words` | string \| null | for display: the same as `schedule`, but a one-off reads `"once, 2026-10-16 14:30 PT"` |
| `note` | string | a one-off's note. It is `""` when there is none, never null. |
| `enabled` | bool | the switch |
| `done` | string \| null | `""` until a one-off has fired, then `"<ISO> <outcome>"`. A done one-off is left out of the list; `GET …/<name>` still returns it. |
| `body` | string | the instruction text. For the reflection it is the record's stock sentence, not the instruction. |
| `instruction` | null | Use `body` for the instruction text. |
| `timer` | string | the systemd timer (`loam-cron-ada-infra-watch.timer`, `loam-wake-ada.timer` for a reflection) |
| `last_fire` | `{id, ts, outcome, stage}` \| null | the latest fire in the log. `ts` is when the fire was logged. The start time is on the Activity fire item. |
| `last_change` | change item + `seen` \| null | the record's latest change (Activity shape, below) |
| `unseen` | [id] | ids of the record's unseen changes **and refusals**. A non-empty list is the row's marker. |
| `refused` | refused item \| null | set when the record's latest event is a refusal |
| `error` | string \| null | why the record doesn't parse |
| `next_fire` | ISO \| null | the user manager's next elapse; null when paused |

### `GET /v1/schedules/ada` (live)

```json
{
  "schedules": [
    {
      "agent": "ada",
      "name": "infra-watch",
      "kind": "recurring",
      "schedule": "Wednesdays at 05:00 PT",
      "words": "Wednesdays at 05:00 PT",
      "note": "",
      "enabled": true,
      "done": "",
      "body": "# Cron: infra-watch\n\nThis is a task lane, not reflection. One job: the weekly check of the tools this server runs. …",
      "instruction": null,
      "timer": "loam-cron-ada-infra-watch.timer",
      "last_fire": null,
      "last_change": {
        "id": "37fc0473f11a",
        "ts": "2026-10-15T00:16:31Z",
        "agent": "ada",
        "record": "infra-watch",
        "op": "edit",
        "actor": "engineer",
        "lane": "engineer",
        "reason": "migration from approved: to enabled:",
        "kind": "change",
        "diff": {
          "header": {"approved": ["2026-10-06", null], "enabled": [null, "true"]},
          "body_changed": false,
          "created": false,
          "deleted": false
        },
        "seen": true
      },
      "unseen": [],
      "refused": null,
      "error": null,
      "next_fire": "2026-10-21T12:00:00Z"
    },
    {
      "agent": "ada",
      "name": "reflection",
      "kind": "reflection",
      "schedule": "daily at 03:20 PT",
      "words": "daily at 03:20 PT",
      "note": "",
      "enabled": true,
      "done": "",
      "body": "This is your daily wake. Use it to keep learning and keep your workspace up.",
      "instruction": null,
      "timer": "loam-wake-ada.timer",
      "last_fire": null,
      "last_change": {
        "id": "be3a59d026ae",
        "ts": "2026-10-15T00:16:31Z",
        "agent": "ada",
        "record": "reflection",
        "op": "create",
        "actor": "engineer",
        "lane": "engineer",
        "reason": "migration from approved: to enabled:",
        "kind": "change",
        "diff": {
          "header": {"enabled": [null, "true"], "lane": [null, "reflection"], "schedule": [null, "daily at 03:20 PT"]},
          "body_changed": true,
          "created": true,
          "deleted": false
        },
        "seen": true
      },
      "unseen": [],
      "refused": null,
      "error": null,
      "next_fire": "2026-10-15T10:20:00Z"
    },
    {
      "agent": "ada",
      "name": "strategic-watch",
      "kind": "recurring",
      "schedule": "Sundays at 05:00 PT",
      "words": "Sundays at 05:00 PT",
      "note": "",
      "enabled": true,
      "done": "",
      "body": "# Cron: strategic-watch\n\nThis is a task lane, not reflection. One job: a weekly sweep. …",
      "instruction": null,
      "timer": "loam-cron-ada-strategic-watch.timer",
      "last_fire": null,
      "last_change": {"id": "34d1190812a8", "ts": "2026-10-15T00:16:31Z", "agent": "ada", "record": "strategic-watch", "op": "edit", "actor": "engineer", "lane": "engineer", "reason": "migration from approved: to enabled:", "kind": "change", "diff": {"header": {"approved": ["2026-10-06", null], "enabled": [null, "true"]}, "body_changed": false, "created": false, "deleted": false}, "seen": true},
      "unseen": [],
      "refused": null,
      "error": null,
      "next_fire": "2026-10-18T12:00:00Z"
    }
  ],
  "unread": 3
}
```

The live `GET /v1/schedules` returns ten rows of this shape: a `reflection` for each agent, plus each agent's own crons. Its `unread` is `3` (three finished jobs). `GET /v1/schedules?agent=iris` returns Iris's two rows.

### `GET /v1/schedules/ada/infra-watch` (live)

The bare row: the first object in the list above, byte for byte.

### A row carrying an agent's unseen change (scratch)

Ada's wake moved `strategic-watch` (actor `ada`, lane `wake`). Then `GET /v1/schedules/ada/strategic-watch`:

```json
{
  "agent": "ada",
  "name": "strategic-watch",
  "kind": "recurring",
  "schedule": "Sundays at 06:00 PT",
  "words": "Sundays at 06:00 PT",
  "note": "",
  "enabled": true,
  "done": "",
  "body": "# Cron: strategic-watch\n\nThis is a task lane, not reflection. …",
  "instruction": null,
  "timer": "loam-cron-ada-strategic-watch.timer",
  "last_fire": null,
  "last_change": {
    "id": "c7addb3d2224",
    "ts": "2026-10-15T01:08:50Z",
    "agent": "ada",
    "record": "strategic-watch",
    "op": "move",
    "actor": "ada",
    "lane": "wake",
    "reason": "the market sweep reads better after the digest",
    "kind": "change",
    "diff": {
      "header": {"schedule": ["Sundays at 05:00 PT", "Sundays at 06:00 PT"]},
      "body_changed": false,
      "created": false,
      "deleted": false
    },
    "seen": false
  },
  "unseen": ["c7addb3d2224"],
  "refused": null,
  "error": null,
  "next_fire": "2026-10-21T12:00:00Z"
}
```

## Writes (scratch)

### `POST /v1/schedules/ada`: recurring

Request: `{"name": "morning-digest", "schedule": "weekdays at 06:30 PT", "body": "Write the morning digest.", "reason": "asked for it in chat"}`

```json
{
  "event": {
    "id": "b3810f419ac8",
    "ts": "2026-10-15T01:08:50Z",
    "agent": "ada",
    "record": "morning-digest",
    "op": "create",
    "actor": "owner",
    "lane": "app",
    "reason": "asked for it in chat",
    "kind": "change",
    "diff": {
      "header": {"enabled": [null, "true"], "schedule": [null, "weekdays at 06:30 PT"]},
      "body_changed": true,
      "created": true,
      "deleted": false
    }
  },
  "schedule": {
    "agent": "ada",
    "name": "morning-digest",
    "kind": "recurring",
    "schedule": "weekdays at 06:30 PT",
    "words": "weekdays at 06:30 PT",
    "note": "",
    "enabled": true,
    "done": "",
    "body": "Write the morning digest.",
    "instruction": null,
    "timer": "loam-cron-ada-morning-digest.timer",
    "last_fire": null,
    "last_change": {"id": "b3810f419ac8", "ts": "2026-10-15T01:08:50Z", "agent": "ada", "record": "morning-digest", "op": "create", "actor": "owner", "lane": "app", "reason": "asked for it in chat", "kind": "change", "diff": {"header": {"enabled": [null, "true"], "schedule": [null, "weekdays at 06:30 PT"]}, "body_changed": true, "created": true, "deleted": false}, "seen": true},
    "unseen": [],
    "refused": null,
    "error": null,
    "next_fire": "2026-10-21T12:00:00Z"
  }
}
```

A record is enabled unless the request sends `enabled: false`. The owner's own changes are created already seen.

### `POST /v1/schedules/ada`: one-off

Request: `{"name": "check-pr", "at": "2026-10-16 14:30 PT", "note": "did the PR merge?", "body": "Check whether the PR merged; say so in comms."}`

```json
{
  "event": {
    "id": "fd1d4bf7eecd", "ts": "2026-10-15T01:08:50Z", "agent": "ada", "record": "check-pr",
    "op": "create", "actor": "owner", "lane": "app", "reason": null, "kind": "change",
    "diff": {"header": {"at": [null, "2026-10-16 14:30 PT"], "enabled": [null, "true"], "note": [null, "did the PR merge?"]}, "body_changed": true, "created": true, "deleted": false}
  },
  "schedule": {
    "agent": "ada",
    "name": "check-pr",
    "kind": "one-off",
    "schedule": "2026-10-16 14:30 PT",
    "words": "once, 2026-10-16 14:30 PT",
    "note": "did the PR merge?",
    "enabled": true,
    "done": "",
    "body": "Check whether the PR merged; say so in comms.",
    "instruction": null,
    "timer": "loam-cron-ada-check-pr.timer",
    "last_fire": null,
    "last_change": {"id": "fd1d4bf7eecd", "…": "the event above, with seen: true"},
    "unseen": [],
    "refused": null,
    "error": null,
    "next_fire": "2026-10-21T12:00:00Z"
  }
}
```

### `PATCH /v1/schedules/ada/morning-digest`

Keys the request doesn't send are left byte for byte. `schedule` and `at` replace each other: setting either one clears the other, and also clears `done`. Pause and resume are `enabled`.

Request `{"schedule": "daily at 07:00 PT"}` returns the `event` below. Its `schedule` is the full row, as in the create example.

```json
{"event": {"id": "78111b99a1c1", "ts": "2026-10-15T01:08:50Z", "agent": "ada", "record": "morning-digest", "op": "move", "actor": "owner", "lane": "app", "reason": null, "kind": "change", "diff": {"header": {"schedule": ["weekdays at 06:30 PT", "daily at 07:00 PT"]}, "body_changed": false, "created": false, "deleted": false}}, "schedule": {"…": "row"}}
```

Request `{"enabled": false}` returns an `event` with `"op": "pause"` and `"diff": {"header": {"enabled": ["true", "false"]}, …}`. In its `schedule` row, `"enabled": false` and `"next_fire": null`. `{"enabled": true}` returns `"op": "resume"`.

### `DELETE /v1/schedules/ada/morning-digest`

Request: `{"reason": "not needed"}`

```json
{"event": {"id": "be8244d31e11", "ts": "2026-10-15T01:08:50Z", "agent": "ada", "record": "morning-digest", "op": "delete", "actor": "owner", "lane": "app", "reason": "not needed", "kind": "change", "diff": {"header": {"enabled": ["false", null], "schedule": ["daily at 07:00 PT", null]}, "body_changed": true, "created": false, "deleted": true}}, "schedule": null}
```

### `POST /v1/schedules/ada/morning-digest/run`

```json
{"event": {"id": "a0865bf68894", "ts": "2026-10-15T01:08:50Z", "agent": "ada", "record": "morning-digest", "actor": "owner", "lane": "app", "unit": "loam-cron-ada-morning-digest.service", "kind": "run"}}
```

This starts the service without waiting (`systemctl --user start --no-block`). The answer carries no outcome: the run's `fire` shows up later in Activity and in the row's `last_fire`. bin/wake's lock and its enabled check apply, as they do for a timer.

### `POST /v1/schedules/ada/strategic-watch/seen`

```json
{"marked": 1, "unread": 3}
```

### `POST /v1/activity/seen`

`{"all": true}` returns `{"marked": 3, "unread": 0}`. `{"ids": ["nope"]}` returns `{"marked": 1, "unread": 3}`: an id that matches no item is still stored and counted.

### `POST /v1/activity/d7d25b708681/undo`

This undoes the latest change, here a resume. The record goes back to how it was, as a new change by the owner that names the one it undoes:

```json
{"event": {"id": "d1960db3fcd4", "ts": "2026-10-15T01:08:50Z", "agent": "ada", "record": "morning-digest", "op": "pause", "actor": "owner", "lane": "app", "reason": "undo of resume by owner at 2026-10-15T01:08:50Z", "kind": "change", "diff": {"header": {"enabled": ["true", "false"]}, "body_changed": false, "created": false, "deleted": false}, "undoes": "d7d25b708681"}, "schedule": {"…": "row, enabled false, next_fire null"}}
```

Only a record's latest change can be undone. The row's `last_change` is always that change, so the broker sends no `undoable` flag. An older id is refused (below).

### `POST /v1/jobs/20991231-000000-abcd1234/cancel`

```json
{"job": "20991231-000000-abcd1234", "said": "cancelled 20991231-000000-abcd1234: it had not started and never will"}
```

This is the launcher's durable cancel, by `owner`. A queued job never starts. A running job is not killed, and nothing continues or recovers it.

## Refusals (every one produced by a call)

| call | status | `error` |
|---|---|---|
| `GET /v1/schedules/nobody` | 404 | `'nobody' is not an agent on this box` |
| `GET /v1/schedules/ada/nope` (also PATCH, DELETE, run) | 404 | `there is no schedule ada/nope` |
| `GET /v1/activity?kind=wake` | 400 | `wake is not a kind of activity; the kinds are change, refused, fire, run, job` |
| `GET /v1/activity?limit=ten` | 400 | `` `limit` is a number `` |
| `POST /v1/schedules/ada` with no name | 400 | `a new schedule needs a name: lower-case letters, digits and dashes` |
| … with `name: "infra-watch"` | 400 | `ada already has a schedule named infra-watch` |
| … with `{name, days: [0], time: "07:00"}` | 400 | `a schedule has no field days, time; the fields are schedule, at, note, body, enabled` |
| … with `schedule: 7` | 400 | `` `schedule` is written as words, like "daily at 05:00 PT" `` (`at`: `like "2026-10-20 14:30 PT"`; `note`: `like "did the PR merge?"`) |
| … with `schedule: "every blue moon"` | 400 | the grammar, multi-line (below) |
| … with `at: "2026-01-01 09:00 PT"` | 400 | `a one-off is set at least 10 minutes ahead; 2026-01-01 09:00 PT is -412329 min from now` |
| `PATCH …/infra-watch {"enabled": "yes"}` | 400 | `` `enabled` is true or false `` |
| `PATCH …/infra-watch {"enabled": true}` (already on) | 400 | `that changes nothing in the schedule` |
| `PATCH …/reflection {"body": "x"}` | 200 | The reflection record holds its instruction. |
| `DELETE …/reflection` | 400 | `` the nightly reflection's record is not deleted; pause it with `enabled: false` `` |
| `POST …/run` while paused | 400 | `ada/morning-digest is paused, and a paused schedule does not run; resume it first` |
| `POST …/run` on a fired one-off | 400 | `ada/<name> is a one-off that has already run (<done>)` (from the code; not called) |
| `POST …/run` before the reconciler has installed the service | 400 | `ada/<name> has no service installed yet; the reconciler installs it within a minute of a change` (from the code; not called) |
| `POST /v1/activity/<older-id>/undo` | 400 | `ada/morning-digest has changed since that change (resume by owner at 2026-10-15T01:08:50Z); undo the later change first` |
| `POST /v1/activity/nope/undo` | 404 | `there is no schedule change 'nope' in the log` |
| `POST /v1/jobs/<id>/cancel`, cancelled again | 400 | `job 20991231-000000-abcd1234 already has a verdict; nothing to cancel` |
| `POST /v1/jobs/abc/cancel` | 404 | `'abc' is not a job id` |
| `POST /v1/jobs/20991231-000000-ffff0000/cancel` | 404 | `no job '20991231-000000-ffff0000' in any project` |
| `PUT /v1/schedules/ada/infra-watch` | 405 | `no route for PUT /v1/schedules/ada/infra-watch` |

The broker also has other sentences from its validation path, for example how many one-offs may be pending (at most 5), and that a one-off is at most 30 days ahead. Each comes back as a 400 in this same shape.

The grammar sentence, verbatim:

```
cannot parse schedule 'every blue moon'. The grammar is:

  <when> at <HH:MM> PT

  <when> is one of:
    daily
    weekdays                     Monday through Friday
    weekends                     Saturday and Sunday
    <Day>s                       e.g. Sundays
    <Day>s and <Day>s            e.g. Mondays and Thursdays

  The time is 24-hour, zero-padded, and PT is the only zone accepted.
```

A refused write is logged too. It shows up in Activity as a `refused` item and on the row as `refused` and in `unseen`, unless the owner made it, since their own refusals are created already seen.

## Activity

`GET /v1/activity` returns items newest first. They are filtered by `agent` (comma list) and `kind` (comma list of `change, refused, fire, run, job`), and paged by `limit` (1–200, default 50) and `offset`. `next_offset` is null on the last page. `unread` counts everything, ignoring the filter.

### `GET /v1/activity?limit=3` (live)

```json
{
  "items": [
    {
      "id": "job:20261014-175241-ccdd8b19",
      "kind": "job",
      "job": "20261014-175241-ccdd8b19",
      "ts": "2026-10-15T01:07:31Z",
      "started": "2026-10-15T00:52:42Z",
      "agent": "ada",
      "actor": "ada",
      "project": "loam-ui",
      "title": "Activity",
      "ok": true,
      "stage": "done",
      "outcome": "completed",
      "landing": "not_landed",
      "landed_sha": null,
      "receipt": "/home/loam/vault/40_PROJECTS/loam-ui/receipts/2026-10-14-20261014-175241-ccdd8b19-activity.md",
      "job_dir": "/home/loam/agents/state/projects/loam-ui/jobs/20261014-175241-ccdd8b19",
      "seen": false
    },
    {
      "id": "job:20261014-175240-ac1c8a7b", "kind": "job", "job": "20261014-175240-ac1c8a7b",
      "ts": "2026-10-15T01:04:55Z", "started": "2026-10-15T00:52:41Z", "agent": "ada", "actor": "ada",
      "project": "loam-ui", "title": "The Schedule tab", "ok": true, "stage": "done", "outcome": "completed",
      "landing": "landed", "landed_sha": "0b0f6f99828136cacf3110fd58e8e3e3148c81e9",
      "receipt": "/home/loam/vault/40_PROJECTS/loam-ui/receipts/2026-10-14-20261014-175240-ac1c8a7b-the-schedule-tab.md",
      "job_dir": "/home/loam/agents/state/projects/loam-ui/jobs/20261014-175240-ac1c8a7b", "seen": false
    },
    {
      "id": "job:20261014-173201-d37ec10c", "kind": "job", "job": "20261014-173201-d37ec10c",
      "ts": "2026-10-15T00:51:43Z", "started": "2026-10-15T00:32:02Z", "agent": "ada", "actor": "ada",
      "project": "Loam", "title": "The broker serves schedules and activity", "ok": true, "stage": "done",
      "outcome": "completed", "landing": "landed_earlier", "landed_sha": "73462b24be0855cb30962c4fcce34500ffb58482",
      "receipt": "/home/loam/vault/40_PROJECTS/Loam/receipts/2026-10-14-20261014-173201-d37ec10c-the-broker-serves-schedules-and-activity.md",
      "job_dir": "/home/loam/agents/engineer/jobs/20261014-173201-d37ec10c", "seen": false
    }
  ],
  "total": 511,
  "next_offset": 3,
  "unread": 3,
  "jobs": {
    "at": "2026-10-15T01:08:49Z",
    "slots": [
      {"slot": 1, "free": false, "since": "2026-10-15T01:06:46Z", "pid": 3595007, "kind": "engineer job", "job": "20261014-180645-6e931667", "project": "Loam", "title": "The schedules and activity contract in the vault", "briefed_by": "ada"},
      {"slot": 2, "free": false, "since": "2026-10-15T01:08:40Z", "pid": 3596365, "kind": "engineer job", "job": "20261014-180839-dba03d7e", "project": "loam-ui", "title": "Activity — automatic continuation 1 of 2", "briefed_by": "ada"},
      {"slot": 3, "free": true}
    ],
    "landing": [],
    "queue": []
  },
  "jobs_error": null
}
```

The live feed holds 511 items: 501 finished jobs and 10 changes, the 00:16 UTC migration. 498 of the jobs are older than the seen store, which began at 00:38 UTC, and count as seen.

`jobs` is the launcher's `box_status()`: run slots, landings and the queue in order. It is the same function `engineer-job --status` uses. A queue entry (scratch):

```json
{"position": 1, "joined": "2099-12-31T00:00:00Z", "job": "20991231-000000-abcd1234", "project": "demo", "title": "A job to cancel", "briefed_by": "ada"}
```

If the launcher can't be read, `jobs` is null and `jobs_error` holds a sentence (`the launcher's status could not be read: …`). The feed still returns.

### Item kinds

Every item has `id`, `ts`, `kind`, `agent`, `actor`, `seen`. `actor` is a name: `owner`, `engineer`, `loam` (the runtime), or a persona (`ada`). It is never a role like "agent" or "person".

**`change`** (live, `?kind=change`):

```json
{"id": "59acfa3ec6a0", "ts": "2026-10-15T00:16:31Z", "agent": "pax", "record": "reflection", "op": "create", "actor": "engineer", "lane": "engineer", "reason": "migration from approved: to enabled:", "kind": "change", "diff": {"header": {"enabled": [null, "true"], "lane": [null, "reflection"], "schedule": [null, "daily at 02:40 PT"]}, "body_changed": true, "created": true, "deleted": false}, "seen": true}
```

- `op`: `create`, `delete`, `done` (a one-off marked fired, by `loam`), `pause`, `resume`, `move` (`schedule`/`at` changed), `edit` (anything else).
- `lane`: `conversation`, `wake`, `cron`, `reconciler`, `engineer`, `app`, `unknown`.
- An undo also carries `undoes: <id>`.
- `diff.header` holds only the keys that moved, as `[before, after]` raw header strings (`"true"`, not `true`). The body appears only as `body_changed`.

**`refused`** (scratch):

```json
{"id": "ef03330eaec4", "ts": "2026-10-15T01:08:50Z", "agent": "ada", "record": "reflection", "actor": "owner", "lane": "app", "reason": null, "why": "the nightly reflection's record is not deleted; pause it with `enabled: false`", "kind": "refused", "direct": false, "diff": {"header": {"enabled": ["true", null], "lane": ["reflection", null], "schedule": ["daily at 03:20 PT", null]}, "body_changed": true, "created": false, "deleted": true}, "seen": true}
```

`direct: true` means an agent edited the file itself and the reconciler refused the edit.

**`fire`** (scratch log, matched to the real lines of `logs/wake-ada.jsonl`):

```json
{"id": "a797558a2847", "ts": "2026-10-15T01:09:20Z", "agent": "ada", "record": "infra-watch", "outcome": "killed", "lane": "cron:infra-watch", "started": "2026-10-15T00:16:31Z", "finished": null, "kind": "fire", "actor": "loam", "stage": "died_mid_run", "produced": {"wake_log": "/home/loam/agents/logs/wake-ada.jsonl", "line": {"ts": "2026-10-15T00:16:31Z", "ok": false, "stage": "died_mid_run", "reported_by": "wake-failed"}, "failure_log": "/home/loam/vault/00_INBOX/from-agents/loam-wake-failures.md"}, "seen": false}
{"id": "dd2d25b9a672", "ts": "2026-10-15T01:09:20Z", "agent": "ada", "record": "infra-watch", "outcome": "ran", "lane": "cron:infra-watch", "started": "2026-10-14T12:00:27Z", "finished": "2026-10-14T12:38:35Z", "kind": "fire", "actor": "loam", "stage": null, "produced": {"wake_log": "/home/loam/agents/logs/wake-ada.jsonl", "line": {"ts": "2026-10-14T12:00:27Z", "finished": "2026-10-14T12:38:35Z", "ok": true, "vault_ref": null, "total_cost_usd": 8.969986500000001}}, "seen": false}
```

- `outcome`: `ran`, `failed`, `skipped`, `missed`, `killed`.
- `produced` is null when no wake line matches.
- `produced.line` carries whichever of `ts, finished, ok, stage, vault_ref, total_cost_usd, error, reported_by` the wake wrote.
- `failure_log` is set for `failed`, `killed` and `missed`.
- A wake has no transcript.

The row's `last_fire` after these two: `{"id": "a797558a2847", "ts": "2026-10-15T01:09:20Z", "outcome": "killed", "stage": "died_mid_run"}`.

**`run`**: the owner's Run now, the `event` shown under the run route, plus `seen: true`.

**`job`**: a finished engineer job. Its id is `job:<job-id>`. See the live sample above.

- `ok`: bool.
- `stage` and `outcome`: the launcher's (`done`/`completed`, `cancelled`/`cancelled`, …).
- `landing`: `landed`, `landed_earlier`, `not_landed`, … or null.
- `receipt`: the vault path, or null.
- `agent` and `actor`: who briefed the job.
- `started`: null for a job that never ran.

The cancelled scratch job:

```json
{"id": "job:20991231-000000-abcd1234", "kind": "job", "job": "20991231-000000-abcd1234", "ts": "2026-10-15T01:08:50Z", "started": null, "agent": "ada", "actor": "ada", "project": "demo", "title": "A job to cancel", "ok": false, "stage": "cancelled", "outcome": "cancelled", "landing": null, "landed_sha": null, "receipt": null, "job_dir": "/tmp/contract-scratch-6J3S/demo-jobs/20991231-000000-abcd1234", "seen": false}
```

### Seen

- The seen store is `state/schedules/seen.json`. Everything before the store began counts as seen, and so does anything the owner did themselves.
- A row's marker and its Activity item share one id, so marking either one seen clears both.
- `unread` counts unseen items everywhere: changes and refusals by anyone but the owner, fires, and jobs. Run items are the owner's, so they are always seen.

## Mismatches: loam-ui `0b0f6f9` `docs/schedules.md` against the broker

| # | field / behaviour | the page expects | the broker actually |
|---|---|---|---|
| 1 | list response | `{schedules: [...]}` | `{schedules: [...], unread}`. The extra key is harmless. |
| 2 | 404s | 400 for refusals | 400 for refusals, **404** for a missing agent, schedule, change or job, 405 for no route. Body `{error}` in all three. |
| 3 | `title` | a display name; create sends `title` and the host picks `name` | **no `title`**. `name` (lower-case, digits, dashes) is required on create and is the only name. Sending `title` is refused: `a schedule has no field title; …` |
| 4 | `kind` | `recurring` \| `once` | `recurring` \| `one-off` \| `reflection` (\| `unknown` for an unparseable record) |
| 5 | `days` | integers, Sunday `0` … Saturday `6` | **none.** Days are words inside `schedule`: `daily`, `weekdays`, `weekends`, `Sundays`, `Mondays and Thursdays` (at most two days unless one of the three words covers the set) |
| 6 | `time` | `"HH:MM"` | none. The time is inside `schedule` (`… at 07:00 PT`) or `at` |
| 7 | `date` | `"YYYY-MM-DD"` for a one-off | none. A one-off is `at: "YYYY-MM-DD HH:MM PT"`. On the row it is returned in `schedule`, and `words` reads `"once, …"`. |
| 8 | `timezone` | IANA zone, sent on create | none. **PT is the only zone**, written in the words. Sending `timezone` is refused as an unknown field. |
| 9 | create/edit body | `title, kind, time, instruction, days` or `date`/`note`, `timezone` | only `name` (create), `schedule` \| `at`, `note`, `body`, `enabled`, `reason`. **Every other key is refused**, `kind` included. |
| 10 | `reflection` | bool | none. `kind: "reflection"` (and `name: "reflection"`) |
| 11 | `instruction` | the instruction text | **the text is `body`**. `instruction` is null. |
| 12 | `instruction_readonly_reason` | a sentence | none. The reflection body is stored on its schedule record and can be edited. |
| 13 | `note` | null when absent | `""` when absent, never null. Setting `schedule` clears `note`. |
| 14 | `fired` | bool | `done`: `""` until fired, then `"<ISO> <outcome>"`. A fired one-off is **missing from the list** and only returned by `GET …/<name>`. |
| 15 | `next_run` | ISO \| null | `next_fire` (ISO UTC \| null when paused) |
| 16 | `last_run` | `{at, status, reason}` | `last_fire`: `{id, ts, outcome, stage}`. `outcome` is `ran, failed, skipped, missed, killed`. `stage` is the stage the run stopped at, not a reason. `ts` is when the fire was logged. |
| 17 | `run_disabled_reason` | a sentence | none. Run is refused by a 400 sentence: paused, a fired one-off, or no service installed yet |
| 18 | Run response | the fake reports `ran` at once | `{event: run item}`, no outcome. The fire arrives later, in `last_fire` and Activity. |
| 19 | `last_change.actor` | `"agent"` \| `"person"` | a name: `owner`, `engineer`, `loam`, or a persona (`ada`). Unattended means not `owner`. Today every live `last_change` is the engineer's migration, already seen. |
| 20 | `last_change.at` | ISO | `ts` |
| 21 | `last_change.before` / `after` | full schedule fields | **none.** `diff.header` holds `{key: [before, after]}` for changed header keys only, as raw strings (`enabled: ["false","true"]`, `schedule: ["Sundays at 05:00 PT", …]`), plus `body_changed`, `created`, `deleted`. The full before/after body is not sent. |
| 22 | `last_change.undoable` / `undo_reason` | optional flag and sentence | none. The row's `last_change` is always the latest change, and that is the one undo accepts. An older id gets a 400 sentence. |
| 23 | `last_change` other keys | `id, actor, at, seen, before, after, reason` | also `agent, record, op, lane, kind: "change"`, `undoes` on an undo |
| 24 | the row's marker | `last_change.seen == false` | `unseen` (ids of unseen changes **and refusals**) is the marker. `last_change.seen` agrees for changes, but a refusal by an agent is marked through `unseen`/`refused` only. |
| 25 | `refused` on the row | — | the latest event when it is a refusal (`why`, `direct`, `diff`) |
| 26 | `words`, `timer`, `error`, `agent` on the row | — | present. `error` is set for a record that doesn't parse (`kind: "unknown"`). |
| 27 | seen/run/undo request body | `{}` | ignored, so `{}` is fine. Answers: seen `{marked, unread}`, undo `{event, schedule}` |
| 28 | PATCH with no effect | — | 400 `that changes nothing in the schedule`. A switch sending the current value gets this refusal. |
| 29 | relay allowlist (`src/server.js`) | schedules routes and `/activity/<id>/undo` | the Activity page also needs `GET /v1/activity`, `POST /v1/activity/seen` and `POST /v1/jobs/<id>/cancel`. Those aren't in the relay yet. |
| 30 | live availability | — | the running broker predates `73462b2` and serves none of these until it restarts (scheduled 22:00 PDT 10-14) |

These match: the route paths and methods, `agent`, `name`, `enabled`, a bare row from the detail GET, `{error}` with a 400 for a refusal, the reflection can't be deleted, only the latest change can be undone, and opening a row then POSTing `…/seen` clears the marker on the host.

## A schedule is its file: the one-line row and the Files save (2026-10-15)

The owner, 10-15: Loam is file-first, so a schedule's detail and editing happen in its `.md` file, `spaces/<agent>/crons/<name>.md`, opened in Files. Schedules shrinks to one line per schedule with an on/off switch, and the line reports what the file can't tell them. This section is the broker side of that, as of the `agents` commit that adds it (job `20261015-083406-29fabfd8`). Everything above still holds unless this section says otherwise.

**Not served until the broker restarts.** The fields and the Files save are in the broker's process (`lib/loam_activity.py`, `lib/loam_files.py`), and they arrive at the next broker restart, which the owner names. The reconciler's half (a save taken as the owner's) runs fresh every minute and is live as soon as the commit lands, but nothing makes a claim until the broker restarts, so nothing changes before then.

### Editing in Files

- **`spaces/<agent>/crons/<name>.md` is writable in Files.** Its row's `authority` is the new value **`schedule`**, with `capabilities.write: true`. `crons/reflection.md` is one of these. The reflection instruction is in `crons/reflection.md`. The `crons/` folder itself is still `persona`, `create: false`, so a new schedule is still made with `POST /v1/schedules/<agent>`.
- **The save is `PUT /v1/files/text` as for any file**: `{root: "spaces", path, text, base_revision}`, with the same 409 when the file changed under the editor. The file is written exactly as sent.
- **It is logged as the owner's.** Just before its rename, Files writes a `claim` line in the schedule log (the bytes' digest, actor `owner`, lane `app`). Then it reconciles that agent at once instead of within the minute. The reconciler takes any file a claim names as that actor's change: a `change` event with `actor: "owner"`, `lane: "app"`, `direct: true`. **No `reason:` is asked of the owner.** An agent's own direct edit still needs one, or it is refused.
- **No stamp.** Files stamps no `last_modified_by` / `created_by` on these files (`provenance: false` in the answer). The editor's stamp has only ever been put on vault notes. A record's header carries only `lane, schedule, at, note, enabled, reason, done`, and a stamped key would make the file fail to parse (it would be `broken`, with that line named). The schedule log already says who saved it.
- **A save that does not parse is still saved.** The file is what the owner wrote. The reconciler refuses it as the owner's (`refused`, `direct: true`, logged once) and **the previous version keeps running**. The answer says so at once.
- **The answer** is the file's own row, as before, plus **`schedule`**: the schedule row as `GET /v1/schedules/<agent>/<name>` answers it, read after the reconcile. It is null for a `crons/` folder that belongs to no agent on the roster.

```json
{
  "root": "spaces", "path": "scratch/crons/morning-digest.md", "name": "morning-digest.md",
  "kind": "file", "size": 74, "modified": "2026-10-15T15:42:05Z", "media_type": "text/markdown",
  "authority": "schedule",
  "capabilities": {"read": true, "download": true, "write": true, "create": false},
  "link": "/files/spaces/scratch/crons/morning-digest.md",
  "revision": "sha256:b5f9f7c7…",
  "text": "---\nschedule: weekdays at 7am\nenabled: true\n---\nWrite the morning digest.\n",
  "provenance": false,
  "schedule": {"state": "broken", "running": "previous", "…": "the whole row"}
}
```

### The new fields on every row

These are added to the row above (`GET /v1/schedules`, `GET /v1/schedules/<agent>`, `GET …/<name>`, and every `schedule` a write answers). No field was removed.

| field | type | what it is |
|---|---|---|
| `state` | `"live"` \| `"off"` \| `"broken"` \| `"refused"` \| `"pending"` | **the file's state.** `live`: the box runs this file. `off`: the box has taken this file and it says `enabled: false`, or it is a one-off that has run. `broken`: **the file doesn't parse.** `refused`: the file parses, but the path would not take it (an agent's edit with no `reason:`, a one-off past its caps). `pending`: written outside the path and not read yet; the reconciler reads it within a minute. |
| `running` | `"this"` \| `"previous"` \| `"none"` | **what the box runs.** `this` file; the `previous` version, the last one accepted before this file; or `none`. **A broken or refused file never stops a schedule:** the reconciler renders only versions it accepted, so `broken` + `previous` means the old version is still firing. |
| `state_words` | string | both of the above in one sentence, ready for the line. `"Live."`; `"Off: the file says it is not enabled."`; `"Not running this file. Line 2: cannot parse schedule 'weekdays at 7am'. … The previous version still runs (weekdays at 07:00 PT)."`; `"… Nothing runs."` when nothing does. |
| `problem` | `{why, line, text}` \| null | set for `broken` and `refused`. `why` is the reason in plain words: no host path, and the grammar block is cut to one sentence. `line` is the 1-based line of the header key at fault and `text` that line as written. Both are null when the fault is in no single line: a missing key, an empty body, the size, or a refusal. |
| `enabled` | bool \| null | **changed:** what the file's `enabled:` says, even when the file is broken. It is null only when `enabled:` itself is unreadable (`enabled: maybe`). |
| `next_fire` | ISO \| null | **changed:** the timer's next elapse whenever `running` is not `none`, so a broken file with the previous version running still has one. Null when nothing runs. |
| `last_edit` | `{id, ts, actor, by, lane, taken}` \| null | **the file's last change, whichever path made it**: a route, a Files save, the agent's tool, or a direct edit of the file (a hand edit included). `taken: false` when that edit was refused, and then it is not what runs. `id` is the log event's, so it matches Activity. |
| `by` | `"owner"` \| `"agent"` \| `"operator"` \| `"runtime"` | inside `last_edit`, and now also on `last_change` and on Activity's `change` and `refused` items. `actor` names who (`owner`, `ada`, `engineer`, `loam`), and `by` says which of the four that is. `operator` is the engineer (`bin/schedule`); `runtime` is Loam marking a one-off done. |
| `changed_since_seen` | bool | `unseen` as a yes/no: there is a change or refusal the owner has not marked seen. Their own changes are born seen. `POST …/<name>/seen` clears it. |
| `file` | `{root: "spaces", path}` | where Files opens it: `link` is `/files/spaces/<path>` |

**`kind` changed for a broken file**: it is the kind of the version that runs (`recurring`, `one-off`, `reflection`), and `unknown` only when nothing was ever accepted. Before, it was always `unknown`. `schedule`, `words` and `note` stay the file's own, so they are null when the file is broken; the schedule that still runs is named in `state_words`. `error` (the parser's raw message, grammar block and all) and `refused` (the record's latest event, when it is a refusal) are unchanged.

**Who the actor is for a direct edit.** A Files save is the owner's, by its claim. An edit of the file by any other means is attributed to the agent, with the lane of whatever run of it is live: `wake`, `cron`, `conversation` (one of its chats is mid-turn or spoke in the last three minutes), or `unknown` (no run at all). It is refused if it has no `reason:` line. An operator changes a record through `bin/schedule` (actor `engineer`), never by hand.

### The switch

Unchanged: `PATCH /v1/schedules/<agent>/<name>` with `{"enabled": true|false}` writes the `enabled:` line in the file, leaves every other byte as it was, and is logged as the owner's change (actor `owner`, lane `app`). On a broken file it is refused, because the whole record is validated, and the file is untouched:

```json
400 {"error": "cannot parse schedule 'weekdays at 7am'. The grammar is:\n\n  <when> at <HH:MM> PT\n …"}
```

The line can know this beforehand: a switch on a `broken` row cannot be flipped until the file is fixed in Files.

### Example: `GET /v1/schedules/scratch`

This was produced in process through the broker's `Server.route` on a scratch agent: `morning-digest` created by the route and then saved twice in Files (good, then broken); `inbox-sweep` hand-edited by the "agent" with no `reason:`; `drafts` hand-edited, off; and the reflection. The stand-in `systemctl` answers every standing timer as `2026-10-16T14:00:00Z`. `last_change` and `refused` are cut to their ids; they are the Activity items described above.

```json
{
  "schedules": [
    {
      "agent": "scratch", "name": "morning-digest", "kind": "recurring",
      "state": "broken", "running": "previous",
      "state_words": "Not running this file. Line 2: cannot parse schedule 'weekdays at 7am'. A schedule reads `<when> at HH:MM PT`, where <when> is daily, weekdays, weekends, or days such as `Sundays` or `Mondays and Thursdays`. The previous version still runs (weekdays at 07:00 PT).",
      "problem": {
        "why": "cannot parse schedule 'weekdays at 7am'. A schedule reads `<when> at HH:MM PT`, where <when> is daily, weekdays, weekends, or days such as `Sundays` or `Mondays and Thursdays`.",
        "line": 2, "text": "schedule: weekdays at 7am"
      },
      "schedule": null, "words": null, "note": null, "enabled": true, "done": null,
      "body": "Write the morning digest.", "instruction": null,
      "timer": "loam-cron-scratch-morning-digest.timer",
      "file": {"root": "spaces", "path": "scratch/crons/morning-digest.md"},
      "last_fire": null,
      "last_change": {"id": "493ccc598d83", "…": "Activity change item, by owner"},
      "last_edit": {"id": "8253cc34cae0", "ts": "2026-10-15T15:42:05Z", "actor": "owner", "by": "owner", "lane": "app", "taken": false},
      "changed_since_seen": false, "unseen": [],
      "refused": {"id": "8253cc34cae0", "…": "Activity refused item, direct, by owner"},
      "error": "cannot parse schedule 'weekdays at 7am'. The grammar is:\n\n …",
      "next_fire": "2026-10-16T14:00:00Z"
    },
    {
      "agent": "scratch", "name": "inbox-sweep", "kind": "recurring",
      "state": "refused", "running": "previous",
      "state_words": "Not running this file: the change was refused. A change by scratch carries a reason, and this one has none. Say why the schedule is changing. The previous version still runs (daily at 12:00 PT).",
      "problem": {"why": "a change by scratch carries a reason, and this one has none. Say why the schedule is changing.", "line": null, "text": null},
      "schedule": "daily at 12:30 PT", "words": "daily at 12:30 PT", "note": "", "enabled": true, "done": "",
      "body": "Sweep the inbox.", "instruction": null,
      "timer": "loam-cron-scratch-inbox-sweep.timer",
      "file": {"root": "spaces", "path": "scratch/crons/inbox-sweep.md"},
      "last_fire": null,
      "last_change": {"id": "e08b3c32e44e", "…": "the agent's earlier, accepted edit"},
      "last_edit": {"id": "38e9397d910e", "ts": "2026-10-15T15:42:05Z", "actor": "scratch", "by": "agent", "lane": "unknown", "taken": false},
      "changed_since_seen": true, "unseen": ["e08b3c32e44e", "38e9397d910e"],
      "refused": {"id": "38e9397d910e", "…": "Activity refused item, direct"},
      "error": null,
      "next_fire": "2026-10-16T14:00:00Z"
    },
    {
      "agent": "scratch", "name": "drafts", "kind": "recurring",
      "state": "off", "running": "none",
      "state_words": "Off: the file says it is not enabled.",
      "problem": null,
      "schedule": "Sundays at 05:00 PT", "words": "Sundays at 05:00 PT", "note": "", "enabled": false, "done": "",
      "body": "Tidy the drafts.", "instruction": null,
      "timer": "loam-cron-scratch-drafts.timer",
      "file": {"root": "spaces", "path": "scratch/crons/drafts.md"},
      "last_fire": null,
      "last_change": {"id": "2fe62eb15a8e", "…": "Activity change item"},
      "last_edit": {"id": "2fe62eb15a8e", "ts": "2026-10-15T15:42:05Z", "actor": "scratch", "by": "agent", "lane": "unknown", "taken": true},
      "changed_since_seen": true, "unseen": ["2fe62eb15a8e"],
      "refused": null, "error": null,
      "next_fire": null
    },
    {
      "agent": "scratch", "name": "reflection", "kind": "reflection",
      "state": "live", "running": "this", "state_words": "Live.", "problem": null,
      "schedule": "daily at 02:00 PT", "words": "daily at 02:00 PT", "note": "", "enabled": true, "done": "",
      "body": "This is your daily wake. Use it to keep learning and keep your workspace up.",
      "instruction": null,
      "timer": "loam-wake-scratch.timer",
      "file": {"root": "spaces", "path": "scratch/crons/reflection.md"},
      "last_fire": null,
      "last_change": {"id": "d4d66a2c26d0", "…": "Activity change item, by engineer"},
      "last_edit": {"id": "d4d66a2c26d0", "ts": "2026-10-15T15:42:05Z", "actor": "engineer", "by": "operator", "lane": "engineer", "taken": true},
      "changed_since_seen": true, "unseen": ["d4d66a2c26d0"],
      "refused": null, "error": null,
      "next_fire": "2026-10-16T14:00:00Z"
    }
  ],
  "unread": 4
}
```

### The parse-failure shape, in short

```json
{
  "state": "broken",
  "running": "previous",
  "state_words": "Not running this file. Line <n>: <why> The previous version still runs (<its schedule>).",
  "problem": {"why": "<plain words>", "line": 2, "text": "schedule: weekdays at 7am"},
  "enabled": true,
  "next_fire": "<the previous version's next run>"
}
```

With nothing accepted before it, `running` is `"none"`, `next_fire` is null and the words end `"Nothing runs."`. When the fault is in no single line (`line: null`), the words say `"Not running this file. <Why>"` with no line number. A one-line row can show `state_words` as it is, or build its own from `state`, `running`, `problem.line` and `problem.why`.

### For the page

- **The line**: name and `words`, the switch (`enabled`), `next_fire`, and, when `state` is not `live`, `state_words` (or a short form of it). Add `last_edit.ts` and `last_edit.by`, and a marker when `changed_since_seen`.
- **Tapping the line** opens `file` in Files. Detail and editing are there.
- **After a Files save** of a `schedule` file, `schedule.state` in the answer tells the editor at once whether the save runs: `live` / `off`, or `broken` / `refused` with `problem.line` to point at.
