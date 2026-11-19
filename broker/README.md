# The session broker

The host-side half of a web front end for the personas. It starts a persona
session on this box — the same session a conversation is today — and hands an
app a narrow, authenticated interface to it. Built 2026-09-16.

The server is `bin/session-broker`, its constants are `lib/loam_broker.py`,
its proof is `bin/broker-probe`, and its units are
`systemd/loam-session-broker.service` (user) and
`systemd/system/loam-broker-firewall.service` (system).

## Why it is here and not in the app

It holds the authority `bin/wake` holds: it launches a persona in that
persona's own workspace with the real login at `~/.claude`. An unprivileged
project engineer's job must never be able to change how a persona is
launched. So the launcher is a runtime component under the platform
engineer, and an app gets messages across a wire and nothing else — no path
of this box, no credential, and no say in what a session may write.

## What a session is

`claude -p` in the persona's workspace, with **no** `--settings` override,
**no** appended system prompt, and **no** `--model` unless this chat has
asked for one (the + menu — "A chat's own model and effort" below; the
default is still nothing on the launch line). That is the whole trick:
everything that makes the session that persona comes from the persona's own
`.claude/settings.json`, exactly as it does at a terminal —

| what | where it comes from | proved by |
|---|---|---|
| `CLAUDE.md` and its `@` imports | the working directory | canary from `soul.md` answered YES, a sibling's decoy NO |
| the model | `settings.json` → `"model": "fable"` | result envelopes report `claude-fable-5-1` |
| `_active.md` in context, `_reflection.md` not | `outputStyle: conversation` | a phrase from each, answered YES and NO |
| the transcript record | `Stop`/`SessionEnd` hooks | three files appeared under the vault's `15_CONVERSATIONS/` |
| the write boundary | `PreToolUse` → `boundary_guard.py` | a write into a sibling's workspace was refused, with the guard's own words |
| the login | `~/.claude`, used in place | the session ran |

`LOAM_WAKE` is deliberately not set. `hooks/transcript_capture.py` treats an
absent value as `conversation`, and a session a person is talking to is one.

## The turn shape: one process per session

`--input-format stream-json` keeps the process alive across turns: user
messages are newline-delimited JSON on stdin for as long as it lives, and its
events come back on stdout. The alternative — one `claude -p --resume` per
turn — pays a cold start every turn (CLAUDE.md, three MCP servers, the
session file re-read) and cannot stream a partial message usefully.

The cost is memory, which is what the cap is for. Measured on this box
2026-09-16, in the broker's own cgroup:

- one session, idle: ~280 MB
- one session, mid-turn on a persona's full context: **peak 326 MB**
- four sessions, idle: 1,126 MB

That is *below* the ~440 MB the 09-04 memory investigation measured for a
Remote Control session (retired 2026-11-19); a Remote Control server was a
supervisor plus its sessions, and this is one session and its three MCP
servers.

Until 2026-11-09 this measurement set a count: `MAX_SESSIONS = 4` against a
unit sized for four. Since then sessions are admitted by the box's memory and
the number is a high backstop — "Admission by memory, not by count" below.
The ~450 MB is still what a start is assumed to take (`SESSION_START_MB`).

## How a container reaches it

A Unix socket is the obvious answer and is unavailable: giving one to a
container means bind-mounting it, and the deployment layer refuses a bind
mount absolutely (`lib/loam_deploy.py`, `_loopback_port`). Host networking
and published-off-loopback ports are refused for the same reason. So:

- a Docker bridge network the host owns, **`loam-broker`, `172.31.0.0/24`**,
  created once (`docker network create --subnet 172.31.0.0/24 --gateway
  172.31.0.1 -o com.docker.network.bridge.name=br-loambroker loam-broker`);
- the broker binds **only** to that network's gateway, `172.31.0.1:8932`. No
  interface outside this box carries the address and the proxy never
  publishes it;
- the app joins that network **in addition** to its own, which in compose is
  a `networks:` entry and nothing else — no bind mount, no capability, no
  host namespace, nothing the generator refuses;
- a wall in the kernel says only that network may speak to the port, because
  binding to a bridge gateway is not a wall by itself: every other container
  routes to the host perfectly well. `/etc/loam/broker/firewall.sh` (this
  directory's `firewall.sh`, installed) raises `LOAM-BROKER-HOST` in **INPUT**
  — container-to-host traffic arrives there, not in `FORWARD`, so
  `DOCKER-USER`, where the personas' sight wall lives, never sees it. The
  `loam-broker-firewall.service` unit is `PartOf=docker.service`, so a docker
  restart re-raises it.
  Since 2026-11-15 the same chain has a second jump, for 8935, Caddy's
  internal route for the Apps section's previews, which must be walled
  exactly as the broker is (deploy/README.md, "Previews for the Apps
  section").

Caused, 2026-09-16, and this is the test to repeat after any change:

```
$ docker run --rm --network loam-broker      alpine:3 wget -qO- http://172.31.0.1:8932/up
{"ok": true, "service": "session-broker"}
$ docker run --rm --network loam-ui_default  alpine:3 wget -qO- http://172.31.0.1:8932/up
wget: download timed out
```

The box's own processes reach it on `lo`, which the wall allows: a process
here runs as the box's user or root, both of whom can read the token anyway.

**Authentication.** Authelia sits in front of the app, not in front of a host
socket, so the interface authenticates itself: every route but `/up` requires
`Authorization: Bearer <token>`. The token is `agents/state/broker/token`,
mode 0600, generated on first start. `state/` is gitignored, so it is in no
repo and no image; the app receives it at run time as an environment
variable, the way every other credential on this box is mounted at job time.
`/up` is unauthenticated on purpose and says nothing but that the broker is
listening — a health check that needs a credential is a health check nobody
runs.

## The interface

`http://172.31.0.1:8932`, JSON in and out, `Authorization: Bearer <token>`
on everything but `/up`.

| route | does |
|---|---|
| `GET /up` | `{"ok": true}`. No token. |
| `GET /v1/limits` | the numbers a client must know before it does something this interface would refuse: `attachment_max_bytes`, `inline_image_max_bytes`, `attachment_types` — "Attachments" below |
| `GET /v1/dictation-terms` | the dictation terms note, read fresh → `{path, obsidian_url, modified, text}`, `Cache-Control: no-store`; **`404`** when there is no note. No parameter names a file — "The dictation terms note" below |
| `GET /v1/agents` | the personas, derived from disk, each with its name and uploaded face |
| `GET /v1/providers[?agent=<name>]` | the four providers, which are connected, the quota, when the login expires (`login`), and what each offers a chat: its `models` and its `efforts`. `?agent=` only decides whose default is ticked. |
| `POST /v1/providers/claude-code/login` | begins a fresh Claude Code sign-in → **`201`** with `{state: "pending", url, code: null, instructions, login}`. Idempotent: a second POST while one is pending answers `200` with the same URL. "Making the login again" below. |
| `GET /v1/providers/claude-code/login` | how that flow is going: `idle \| pending \| done \| failed`, with `message` when it failed and `login` always |
| `POST /v1/providers/claude-code/login/code` | `{"code": "..."}` — the code the sign-in page gave the person → `200` with the settled flow (`done` or `failed`) |
| `DELETE /v1/providers/claude-code/login` | calls off a pending flow. The login on the box is untouched; it was never written to. |
| `POST/GET/DELETE /v1/providers/<id>/login`, `POST …/login/code` | the same four routes for every wired provider (`codex`, `antigravity`), described by the row's `sign_in` — "Signing in to any provider: the contract" below |
| `POST /v1/providers/<id>/logout` | removes that provider's login from the box, where the row's `sign_in.sign_out` names this route (every wired provider since 2026-11-11) → `200 {removed, login}`, plus `message` when the CLI refused; **`409`** while a chat on that provider is working, asking or waiting on its children; **`405`** on a wired provider that offers none, **`404`** on an unwired one |
| `GET /v1/sessions[?agent=<name>][?turns=all]` | every session this broker knows, archived or not, newest activity first, with `live_count` and `cap`. `?archived=1` is accepted and changes nothing (2026-11-12: the classic list's archive filter retired; thread members' sessions are saved archived, and Running now, the one reader, always asked for them). Each row's `turns` is its tail, with `turns_total` — "List rows carry the tail of `turns`" below; `?turns=all` answers them whole. Refreshes each adopted record whose session file has grown, and looks for app sessions it has never seen at most once a minute — "Staying in step with the app's own file" below. |
| `GET /v1/sessions/events[?turns=all]` | SSE: every session record (archived or not, as the list), whenever any changes, as the same rows as the list, and `broker.residency` whenever the resident count moves. Running now's fallback when `/v1/sessions/live` is absent |
| `POST /v1/sessions/adopt` | scans every persona's Claude Code app sessions and makes a record of each → the report. The full rescan, which re-reads every adopted record nobody has written into. Idempotent; also run at every broker start. |
| `GET /v1/sessions/<id>` | that session's record, running or not. Running now reads it before an End |
| `POST /v1/sessions/<id>/permissions/<request_id>` | a thread's permission and question cards, answered on the member's session (`session_id` on the thread's `broker.permission_request`). `{"behavior": "allow"\|"deny", "message"?, "updatedInput"?, "updatedPermissions"?}` for an approval (`updatedPermissions`: "Closing a request" below); `{"behavior": "allow", "answers": {"<question id>": ["<label or text>", …]}}` or `{"behavior": "deny"}` for a question. An allow of a question without `answers`, or with answers that do not fit it, is **`400`** naming the field, and the card stays answerable — "Questions" below |
| `DELETE /v1/sessions/<id>` | ends the process; the id still resumes. On a thread member's session mid-turn, the turn ends with one system line in the thread ("Sam ended Iris's turn") and the answer carries `ended_turn_in: <thread id>` — "Live sessions" below |
| `GET /v1/sessions/live` | what is in RAM now, thread members and any classic session still resident from before 2026-11-12, and the box's totals → `{sessions, box, at}` — "Live sessions" below |
| `GET /v1/sessions/live/events` | SSE: `sessions.live`, the whole answer above, on open, within a second of any record change, and every 10 s when the numbers moved |
| `POST /v1/sessions/end-idle` | ends every live session that is idle → `{ended: [{id, agent}], kept: [{id, agent, why}]}`. Never one that is working, asking, waiting on background work or carrying a thread's turn, nor one active in the last 15 s |
| `GET /v1/files/roots`, `list`, `stat`, `read`, `download`, `archive`, `resolve` | the vault, the agents' spaces and the runtime's documents, by root and path -- "Files" below |
| `PUT /v1/files/text`, `POST /v1/files/file`, `POST /v1/files/folder`, `POST /v1/files/rename` | The owner's saves, creations and renames, revision-conditional, never deleting or replacing -- "Files" below |
| `GET /v1/apps` | the Apps section's list: every agent site, every project's deployed container and every app plugin, each with its URL, owner, access, expiry, what it serves and whether the page may frame it → `{apps, sources}`, `Cache-Control: no-store`; **`503`** naming the record when one cannot be read; **`405`** to anything but `GET` -- "Apps" below |

### The classic chat routes, retired (2026-11-12)

The owner, 2026-11-11: "we're fully ready and in a good place to sunset and
remove the session-based chat view that we have and fully replace it with the
current thread-based chat." The page stopped calling the classic routes at
loam-ui `1681c16` (live at `c5fbcc0`), the 153 classic chats had been
imported as archived threads the day before ("Classic chats imported as
threads" below), and on 2026-11-12 the broker stopped answering them. Each
now answers `405 no route`:

| Retired | What it was | Where that is now |
|---|---|---|
| `POST /v1/sessions` | start a classic chat, or resume one by id | a thread starts and resumes its members' sessions itself (`lib/loam_thread_engine.py`); `POST /v1/threads` starts a conversation |
| `PATCH /v1/sessions/<id>` | a chat's title, pin, archive, read mark, model, effort | `PATCH /v1/threads/<id>` (title, pin, archive, a member's model, read) |
| `POST /v1/sessions/seen` | mark every classic chat read | a thread is read by opening it |
| `GET /v1/sessions/<id>/events` (`from=`, `last=`, `before=`) | open a chat, reconnect, page up | `GET /v1/threads/<id>` and `/events?after=` |
| `POST /v1/sessions/<id>/messages` | send into a chat | `POST /v1/threads/<id>/messages` |
| `POST /v1/sessions/<id>/attachments` | upload for a chat | `POST /v1/threads/<id>/attachments`, the same store |
| `POST /v1/sessions/<id>/compact` | compact a chat now | none: the CLI still compacts by itself near its window, and a thread rotates its session at a context threshold |

With them went the code only they reached: the per-session stream and its
pages (`Server.stream`, `page`, `_framed`, `Session.window`, `turn_starts`),
the follower that kept an open adopted chat in step with the app's own file
(`Session._follow`, `Broker.watch`/`watched`, `FOLLOW_POLL_S`), `compact` on
every session class, `choices_applied`, the `resources` path of a message
(`loam_files.attachable`, `read_scope`), and the list's archive filter. The
settings tool lost `session.title`, `.pinned`, `.archived`, `.unread`,
`.model`, `.effort` and `sessions.seen` (broker/settings.md).

**Kept, and why.** Running now reads `GET /v1/sessions/live` and its stream,
`POST /v1/sessions/end-idle`, `GET` and `DELETE` on one session, and, on a
broker without the live view, `GET /v1/sessions?archived=1` and
`/v1/sessions/events?archived=1`. A thread's cards are answered on
`POST /v1/sessions/<id>/permissions/<request_id>`. Adoption (`POST
/v1/sessions/adopt`, the scan at start and on a list read, the refresh on a
record read) stays: it keeps the records the importer reads in step, and a
rerun of `bin/import-classic-chats` adds what was said since. `Session.replay`
stays as the one reading of a session's whole numbered history. The fork of
an adopted session (`--fork-session`, `cli_session_id`) is reached by no
route now, but stays: it is woven into record identity, the scan's
`prune_fork_duplicates` and the transcript writer's id mapping for the forked
records already on disk. Session records and logs on disk are untouched.

The sections below that describe the retired routes (the seen mark, opening
a chat at its end, attachments to a chat, compaction by route, a chat's own
model and effort by PATCH, following an open adopted chat, pin and archive)
are the record of how they worked; each is marked where it starts.


A session record carries `session_id`, `agent`, `provider`, `model`,
`effort`, `workspace`,
`created`, `last_activity`, `last_event_at`, `state`, `live`, `status`, `title`,
`title_source`,
`last_message`, `pinned`, `archived`, `record_seq`, `seen_seq`,
`background_tasks`, `pending_permissions`,
`origin`, `source`, `replayed`, `source_size`, `source_mtime`,
`history_bytes`, `cli_session_id` and `forked_from` (see
"Sessions from the Claude Code app" below),
and `turns` — one entry per turn holding what the CLI's result envelope
reported: `model`, `usage`, `total_cost_usd`, `duration_ms`, `num_turns`,
`session_id` and `permission_denials`, plus two the broker computes because
only it can: `cost_delta_usd` and `context`. See "`total_cost_usd` is a
running total" and "What a turn cost in context" below, and "What a row on a
screen is made of" for the rest.

**`record_seq` is the record's position — the number of the last event in the
session's log — and it was called `seq` before 2026-09-16.** It is the same
number and means the same thing; only the name changed, because the record
is also sent on the event stream as `broker.session` and a field called `seq`
there was read as an event's identity. See "The snapshot is not an event"
below for what that cost. Session files written before the rename still say
`seq` on disk and are read either way; everything the interface answers says
`record_seq`.

### Live sessions (2026-11-10)

For a screen that shows what is running and ends it, without opening each
thread or chat. `GET /v1/sessions/live` answers one row per session with a
process behind it (`Broker.live()`, the same set admission counts), newest
activity first:

| field | is |
|---|---|
| `id`, `agent`, `engine`, `engine_label` | the session id, the persona, the provider (`claude-code`, `codex`, `antigravity`) and its name for a person (`Claude Code`, `Codex`, `Antigravity`) |
| `model`, `model_asked` | since 2026-11-12: `model` is the model the CLI says it runs (Claude's `system/init`, e.g. `claude-opus-5-5`; Codex's thread start), else the ask; `model_asked` is the ask (`opus`, a pinned id, or `null` for the persona's default). Until then `model` was the ask alone |
| `run_kind` | since 2026-11-12, one vocabulary with Activity's job and run rows: `thread` (a thread member's turn session) or `conversation` (any other chat). Wakes, crons, the reviewer and jobs are not broker sessions; they are rows of `GET /v1/activity`'s `jobs.slots` |
| `kind` | `thread` or `classic`. A thread row carries `thread_id` and `thread_title` (`title` null); a classic row carries `title` (thread fields null). A session is a thread member's when `state/threads/sessions.json` names it for a thread, or a turn is in flight on it |
| `state` | `working` (a turn is open), `asking` (waiting on a permission or a question), `background` (its turn ended, work it launched is still running), `idle` |
| `last_active`, `idle_since` | ISO times; `idle_since` only when `idle` |
| `started_at` | when its process started (from `/proc`) |
| `rss_mb` | the RSS of its process tree: the CLI and its MCP servers. Shared pages count once per process, so it reads a little high |
| `ending` | `{loses_work, text}`: `true` with "ends a turn in progress", "ends a turn waiting on your answer" or "ends the background work it is waiting on"; `false` with "resumes later exactly where it was" |

`box` is for a small gauge: `ram_total_mb`, `ram_used_mb`,
`ram_available_mb`, `reserve_mb` (what admission keeps free), `start_mb`
(what a start is assumed to take), `can_start` (how many more sessions
admission would let start now: the free memory past the reserve, the broker
unit's own room and the backstop, each at `start_mb`), `sessions` (`live`,
`mb`, `idle`), and `units`: the memory outside the broker by kind,
`engineer_jobs`, `wakes` and `crons`, each `{count, mb,
units: [{name, mb}]}`. Everything is read from `/proc` and the user
manager's cgroups (lib/loam_live.py) on each ask.

**Ending.** `DELETE /v1/sessions/<id>` is the end of one; its id resumes.
A thread survives the end of a member's session: the member's record in
`sessions.json` stays, so the next mention resumes the same session. Mid-turn,
the turn stops, what the persona had already said is kept as its message,
no "(no reply)" is written, a mention in it starts no turn, and one system
line says "Sam ended Iris's turn". `POST /v1/sessions/end-idle` ends every
`idle` one and lists the rest in `kept` with why.

**Restarting when idle (2026-11-12; its rule replaced 2026-11-17).**
`bin/restart-broker --when-idle` restarts only on positive evidence that
nothing would be cut: every row of this list reads `idle`, and the body's
`turns` (every thread turn in flight or queued, `{"thread_id", "persona",
"queued"}`) is empty, on three clear reads 20 s apart, and on three more once
the live lock is its own, the last just before the restart. Anything else
holds it: `working` (a silent turn in a long tool call or a call waiting on
an engineer job included), `asking`, `background` (a subagent ends with the
session's process tree, and a waiting call is cut), a state the rule does
not know, and a failed read. The rule is `lib/loam_live.restart_blockers`,
which `bin/move-state` uses too (`loam_live.restart_read`). At its ceiling
(`loam_live.RESTART_CEILING_S`, 30 min) or `--max-wait` it **defers**:
exit 75, nothing restarted, and the caller tries again later. On 11-16 the
ceiling used to let it go past silent turns, and it cut four, three of them
waiting on engineer jobs. `restart-broker` without `--when-idle` is the
operator's explicit restart and waits only for the live lock. A broker
older than `turns` answers rows alone, and they are the evidence then.

### List rows carry the tail of `turns` (2026-11-09)

`GET /v1/sessions` was 3,590,592 bytes on this box on 11-09 (139 rows, 2,174
turns), and the live list's opening burst 2,255,948; on a phone link they
queued every other request behind them. A row now carries `turns` cut to its
tail (`list_row` in `bin/session-broker`): from the latest turn back to the
latest one naming a `model` and the latest context reading — past a
compaction (both figures null) to the turn before it with a `window` — at
most `LIST_TURNS_MAX` (10), plus `turns_total`, the full count. Those are
the three things a page reads off a list row's `turns` (loam-ui at
`bde6a48`: `chatModel`, `lastTurnCost`, `contextLine`/`contextNow`), so each
answers as it did with the whole list. Measured on the same data: the list
401,247 bytes, the burst 294,869; 141 turns kept of 2,174. The per-session
route and the session's own stream still carry `turns` whole, and
`?turns=all` on either list route answers them whole for a client that
needs it.

### The two listings, literally

Both answer an **object**, never a bare array. A client that reads either
"an array or an object with a key" is guessing at something that has one
answer, so here is the answer:

```jsonc
GET /v1/agents
{ "agents": [                                   // objects, sorted by id
    { "id": "cleo", "name": "Cleo", "face": null },
    { "id": "ada",   "name": "Ada",   "face": null }, ... ] }

GET /v1/sessions
{ "sessions": [ <session record>, ... ],        // newest last_activity first
  "live_count": 2,                              // processes running right now
  "cap":  4,                                    // MAX_SESSIONS; the client keeps no copy
  "live": 2 }                                   // the older spelling of live_count
```

`?agent=<name>` filters `sessions` and leaves `live_count` and `cap` alone:
they are the box's numbers, not the filter's. Both were caused through the
app on 2026-09-16 and are what the page reads.

**`live_count` was called `live` until 2026-09-25, and both are answered.**
The rename is not cosmetic: each session record now carries a `live` of its
own — a boolean, whether that one session has a process behind it — and one
word cannot be a count at the top level and a flag on a row. `live_count` is
the name from here on; `live` is still on the body, equal to it, because
removing a field a client may be reading is a break, and nothing has asked
for that break. Whether it goes is the owner's call; the default is that it
stays until the app is known to be off it. See "Which sessions are in RAM"
below.

**`GET /v1/agents` answered a list of bare strings until 2026-09-18, and this
is a breaking change to that route's shape** — the only one this interface
has had. `{"agents": ["ada", ...]}` is now `{"agents": [{"id": "ada",
"name": "Ada", "emoji": "🧱"}, ...]}`, and a client that read the old shape
reads a dict where it expected a name. It was made rather than added beside
because the page draws an agent's name and mark on every row and in the
agents strip, and a page given only `ada` had to invent both — which means
inventing them differently from the record the moment the owner renames one.

`name` is **read** from the persona's own `agent.json` in its workspace,
the file the runtime renders: `display`. The fallback is the capitalised id,
which is what an agent with no `display` gets. `face` is an uploaded image
or `null`, and with `null` the client draws the theme's face.

**`emoji` left every row on 2026-11-16** (the owner: "We should remove the
concept of persona face emoji. That was a legacy feature from the Telegram
days"). An older `agent.json` may still hold the key; nothing serves it, and
a client that read it reads nothing there, which it already handled as an
agent with no emoji. Nothing else from
`agent.json` leaves the box — it also holds the workspace, the vault path and
the wake schedule, and none of that is an app's business. `id` is the
session-starting name and is unchanged: `POST /v1/sessions` still takes
`{"agent": "ada"}`.

### What a row on a screen is made of

Added 2026-09-18, for the high-fidelity design: the home screen draws a row
per session with a title, one word for what it is doing, the last thing the
persona said, how long ago anything happened, a pin and an archive. Before
this the record said `state: running | exited` and nothing else, so a page
had a spinner and no way to say what it was spinning about.

| field | is |
|---|---|
| `title` | the session's name. See below. |
| `title_source` | what named it: `null` the stand-in opening words, `"vault"` the transcript writer's filed title, `"person"` a rename. |
| `status` | one of `working`, `waiting`, `needs_input`, `done`, `failed`, `idle`. |
| `last_message` | the last **top-level** assistant text, cut to 400 characters (`LAST_MESSAGE_CHARS`). `null` until the persona has said something. |
| `last_event_at` | when the last event was numbered, UTC. `null` for a session that has had none. |
| `pinned`, `archived` | booleans, on the record and not in a browser. |
| `provider` | which vendor account the session runs on; `claude-code` today. |
| `background_tasks` | an **integer**: how many background tasks are running behind this session right now. `0` when there are none, never absent. See below. |
| `live` | a **boolean**: is a process of this broker's behind this session right now — is it in RAM. Residency and not turn state, and `false` on every record read off disk. See below. |
| `history_bytes` | for an adopted record nobody has spoken to here yet, the size in bytes of the app's own session file — what a first message would re-send to the model. `null` for every other kind. See below. |

Every record the interface answers carries every one of these keys. A record
written before 2026-09-18 is filled in on the way out (`normalise`), so a
client never has to ask whether a key is missing because the session has no
title or because the session predates titles.

#### An older record is filled from its own log

Filling the *keys* is not filling the *content*, and the difference was a
home screen on which every row read "New chat" and none had a status box —
seen in a real browser at the deployed app on 2026-09-18, a few hours after
the fields were added. Thirteen sessions, all of them from before that
morning, all of them nameless.

The conversation was never lost: `state/broker/sessions/<id>.jsonl` is the
durable log and holds both sides of it. So **`backfill_records()` reads the
three fields out of the log once, at broker start, and writes them back** —
once rather than on every read, because the answer cannot change (a log this
broker is not writing to does not grow) and because `GET /v1/sessions` is
what a phone hits on every pull-to-refresh, with one log on this box already
725 events long. The startup line says what it did:
`backfill: 9 of 13 records filled from their logs`.

| field | derived from |
|---|---|
| `title` | the opening words of the first `broker.user_message`, by the same `first_words` rule a live session uses. Failing that, the first `user` event whose content is a person's prose and not a `tool_result`. Failing that it stays `null`: a session logged before `broker.user_message` existed (2026-09-16) has only tool results in it, and a title taken from one would name the conversation after a file the agent happened to read. |
| `last_message` | the last **top-level** `assistant` text, cut to `LAST_MESSAGE_CHARS`. A subagent's sentence is machinery. |
| `status` | the last `result` envelope's verdict, judged exactly as a live one is: `done`, or `failed` if it was an error. A log with no result leaves the status to `recovered_status`, which says `done` if the record has turns and `idle` if it has none. |

What a person or a live broker already wrote outranks all of it: a title
someone typed, a last message the broker recorded and a status it stored are
facts about the session, and a backfill only fills what is empty. A record
with nothing to fill is not rewritten at all.

**`record_seq` is corrected upward here too, and this is the one thing the
backfill changes rather than fills.** The record's position is defined as the
number of the last event in its log, and one session on this box says `1`
over a log that ends at `51` — a probe killed mid-turn on 2026-09-16, when a
second process for one session restarted its numbering at zero (the defect
the `__init__` comment describes, fixed the same day). Resumed as it stood,
it would have renumbered from 1 again and put two events under one number,
which is the one thing that makes reconnection worthless. Raised to the log's
end, never lowered.

**`status` is not `state`, and both are here on purpose.** `state` is whether
a process exists (`running`, `exited`). `status` is what the session *is*, in
the page's own vocabulary:

- `working` — the CLI's turn is open and the model is generating. Set the
  moment the message is accepted, not when the model gets round to answering,
  and cleared by that turn's `result`.
- `waiting` — the turn's `result` has arrived and the session still has a
  live background task it launched, or a task notification that has not
  landed yet. **Nothing is being generated**: the agent has stopped and is
  parked until its background work reports, after which the CLI opens another
  turn by itself and the session is `working` again. See below.
- `needs_input` — a permission is pending. It **outranks `working`**: a
  paused turn is in flight, and a spinner would hide the one thing the person
  has to do for it to continue.
- `done` — idle after a result that succeeded. An ended session whose last
  turn succeeded is `done`, not `failed`: it has an answer to read and its id
  resumes. **However the process ended**, including from outside — see below.
- `failed` — the last result was an error (`is_error`, or a `subtype` other
  than `success`), **or** a turn was in flight when the process went, **or**
  the process ended before it had ever answered anything and nobody asked it
  to. A turn in flight is `failed` even when the stop was asked for: the
  person sent a message and got no answer, and `done` would be a lie about
  that.
- `idle` — started, no turn yet.

**A process killed from outside while the session was idle is not a failure**,
and until 2026-09-22 it was. A persona's conversation
(`49db688c`) answered at 19:27:13Z, sat idle, and had its process killed at
19:49:02Z with exit 143 when systemd restarted the broker — the unit sets no
`KillMode`, so SIGTERM goes to every process in the cgroup and the CLI child
gets it at the same instant the broker does. `_reap` ran before `stop()` had
recorded that the end was asked for, the broker broadcast `failed`, and the
home screen drew the red failure ring on a conversation that was complete and
fine. The rule now matches the sentence above it: the session has an answer to
read and its id resumes, so it is `done`, and the race cannot decide it either
way. A turn that was actually in flight is untouched by this and is still
`failed`.

**A record whose broker is gone does not keep saying `working`.** The listing
reads records off disk, including ones this broker never started. A record
caught mid-turn by a restart says `working` on disk forever, and a page
reading it draws a spinner for a process that stopped three days ago. So a
stored `working` or `needs_input` is answered as `failed`; a stored `waiting`
is answered as `done`; anything else is answered as stored; and a record older
than statuses is `done` if it has turns and `idle` if it has none, which is
the whole of what can honestly be said about it.

`waiting` is `done` and not `failed` on purpose, and it is the same rule two
lines up (2026-09-25): a record stored `waiting` is a record whose last turn
*ended* — `result` arrived, it succeeded, the answer is there to read — and
the only thing outstanding was a background child of a process that no longer
exists. Nothing more is coming, which is what `done` says. A turn that was
genuinely in flight was stored `working`, not `waiting`, and is still
`failed`.

**`last_event_at` is not `last_activity`.** `last_activity` is bumped by a
client answering a permission and is what the idle reaper reads; this is when
the session last showed a sign of life on its own stream. A session that says
`working` and whose last event was twenty minutes ago is a different thing
from one that is mid-sentence, and a spinner cannot say which — so the page
is given the number and says it.

#### Working means working: the tasks running behind a turn

The owner, 2026-09-22: *"The chat shows a done state sometimes when the model is
still thinking or running a command and still working. It should be in a
working state until it is fully done executing. Meaning there is no additional
response coming."*

The cause is on the box's own stream and the broker was not reading it. A
persona can launch a subagent with `run_in_background`; the CLI then **closes
the turn** — a real `result`, not an error — and goes on running the subagent
behind it. When the subagent stops, the CLI hands the persona a *task
notification*, which opens another turn with no message from anybody, and text
arrives under a row that has said `done` for twenty minutes. Read off session
`940380fd` (a persona's planning conversation) on 2026-09-22, seqs 10152–10393:

```
10152  result  success          <- the broker used to write `done` here
10153  user                     <- the subagent's last tool result
10154  assistant
10155  system/background_tasks_changed   tasks: []
10156  system/task_updated               status: completed
10157  system/task_notification          status: completed
10158  system/init              <- a new turn, opened by the CLI
 ...   assistant, tool calls, assistant
10393  result  success          <- and NOW it is done
```

So the session is held off `done` for as long as any of these holds, and is
`done` only when none of them does:

- **the CLI's turn is open** — from the moment a message is accepted, or the
  moment the CLI writes the `system`/`init` that opens a turn, until that
  turn's `result`. This one and only this one is `working`; the three below
  are `waiting`.
- **a background task is live** — `background_tasks` is not zero.
- **a task has left the CLI's list and not yet notified.** The list empties one
  event *before* the notification (10155, then 10157 above), and a status
  computed in between would flicker to `done` and back inside a millisecond.
- **a notification has fired and no result has answered it.** It is the CLI
  handing the persona another turn.

**`system`/`init` opens a turn, and this is not obvious.** In
`--input-format stream-json` the CLI writes an `init` ahead of *every* turn,
not once per process: 26 of them across five processes of one session, one
before each turn, and none at a launch nobody has spoken to yet — which is why
this cannot make an untouched session read `working`. It is load-bearing
beyond the resumed turn: session `940380fd` emitted a `result` at seq 1579 and
opened the real turn at `init` 1580, with its result 900 events later, and
without this the row said `done` for all of it.

**The page reads one field.** It does not add these up, does not watch for a
notification and does not time anything: `status` says which of the three it
is and the page draws that word.

**Nothing pins a row on `waiting` forever.** A task that leaves the CLI's list
and never notifies — a shape nothing on this box has produced, and one no
reading of the stream can rule out — is dropped after
`BACKGROUND_SETTLE_S` (20 s) and the record is rewritten, because a spinner
that never stops is this same defect with its sign flipped. And the existing
rule is unchanged: a record caught mid-turn by a broker restart is answered
`failed`, not `working`.

`tests/test_broker_background.py` is the unit, and every CLI event in it is
verbatim off this box with its session id and seq in the comment.

#### Waiting is not working: the state between them

The owner, 2026-09-25, watching their own chat while its agent had ended its turn
and two background jobs were still running: *"The dot is absent when the main
agent is just waiting for background task completion … the chat list shows as
if you are fully done. Maybe we should have a waiting state as well as a
separate state?"*

The hold above was doing its job — the record said `working` and not `done`,
verified on their own live session that morning. What it got wrong was the
word. The page's working cues (the breathing blob, the pulsing dot) are keyed
to the live stream, and the stream had gone quiet at the `result`, so a state
that says *generating* was being drawn by a row where nothing was being
generated. An agent that is writing and an agent that has finished its turn
and is only blocked on its children are two things a person reads
differently, so since 2026-09-25 they are two words:

| | the CLI's turn | what is happening |
|---|---|---|
| `working` | open | the model is generating |
| `waiting` | closed by `result` | nothing is being generated; a background task is live or its notification is pending |
| `done` | closed | nothing further is coming |

`needs_input` and `failed` are untouched, and `needs_input` still outranks
both of the first two.

**One field, and the count is a different field.** `waiting` will almost
always carry a non-zero `background_tasks`, but a page must not compute the
state from the count and does not have to: the count can be `0` for the
moment between a task leaving the CLI's list and its notification arriving,
and that moment is `waiting` too. `background_tasks` says what is being waited
*on*; `status` says what the session *is*.

On the wire, a session whose agent has stopped and whose two background jobs
are still running — the shape the owner was watching, `6cef8566` on 2026-09-25:

```jsonc
{ "type": "broker.session", "session_id": "6cef8566-…",
  "status": "waiting",        // the turn's `result` landed at 18:41:54Z
  "state": "running",         // the process is alive, generating nothing
  "background_tasks": 2, ... }
```

The three-way sequence across one turn that launches a background task, off
the record file: `working` on send → `working` while the model runs → the
CLI's `result` → **`waiting`** for as long as the task runs → the
notification's own turn → `working` again → its `result` → `done`.

#### `background_tasks`, and `history_bytes`

`background_tasks` is an **integer**, the number of background tasks (a
subagent, a background brief — anything the CLI reports as running behind the
turn) alive right now. It is copied from the CLI's own
`system`/`background_tasks_changed`, which states the **whole list** every
time, `[]` included; the broker keeps no tally of its own, because a tally
drifts from the CLI's the first time an event is missed and the CLI is the
only party that knows. When it changes, `broker.background_tasks` goes out on
the session's stream — see *Events* — so an open thread shows a count without
polling.

It is `0` on every record no process of this broker's is behind, whatever the
file on disk says. A background task is a child of a CLI process: a record
left claiming two by a broker that was killed would draw a badge for two
subagents that stopped days ago.

**On a Codex chat the background tasks are its subagents** (2026-10-02).
Codex states no list, so the broker keeps it from the one signal it has: the
app-server puts every subagent's notifications on the root's stream under the
subagent's own `threadId`, and a thread other than the record's
`thread_id` is in `background_tasks` from its `turn/started` to its
`turn/completed` (or a `thread/status/changed` to idle, or `thread/closed`).
Neither of those is the root's turn opening or closing, and the subagent's
token usage is not the row's context. Its messages and tool calls are on the
stream with `parent_tool_use_id` set to the id of the `subAgentActivity`
call that spawned it (2026-11-13; `codex-subagent:<thread id>` when that call
was not seen), as a Claude subagent's events carry their `Agent` call's id,
so a subagent's words are never the persona's: until then they went out with
a null parent and a thread could post a subagent's sentence as the persona's
reply. `broker.background_tasks` carries each as
`{task_id: <thread id>, task_type: "subagent", description: "/root/<name>"}`.
So the statuses are the Claude ones with nothing added: `working` while the
root's turn is open, `waiting` once it has ended with a subagent still
running. Before this, Ada's chat 7bcccd28 read `done`, `0`, while its
subagent held an engineer call for half an hour after the root's turn ended.

**Subagents are on** (2026-11-13). Every thread is started and resumed with
`developerInstructions` set to Loam's explicit ask to delegate when it helps
(`loam_codex.SUBAGENT_ASK`), because Codex's own rule (`explicitRequestOnly`)
spawns only when instructions explicitly ask, and its `multiAgentMode:
"proactive"` is accepted but not honoured on 0.169.0. Whether a turn
delegates is the model's judgment, as on Claude Code.

**A subagent that finishes after the root's turn wakes the root**
(2026-10-09), because Codex does not: on its own it hands the report to the
root's *next* turn, which waits for the person's next message (proved
2026-10-02, job 20261002-083948-afa17a01). The broker does what the Claude
CLI does instead. It puts a `system`/`task_notification` on the stream
(`task_id` the subagent's thread, `summary` its last message), then starts a
root turn whose input is a `<task-notification>` envelope carrying that
message. The page sees what a Claude notification gives it: no
`broker.user_message`, then an `init`, the turn's work and a `result`. The
row goes `waiting`/1 → `working`/0 → `done`. Subagents that end within one
second of each other share one woken turn. If the root's turn is still open
when a subagent ends, nothing new starts, because Codex hands the report to
that turn. Only the root's own children wake it.

The same applies to an MCP call the root left running when its turn
completed. On Codex the model calls tools from a code cell, and Ada's
a9c8ec2c ended a turn with a `brief` still in flight. Such a call is in
`background_tasks` as `{task_type: "mcp_call", description:
"mcp__<server>__<tool>"}` until its `item/completed`, which wakes the root
with its result. So does a command the root left running: a code cell
yields after 30 s and leaves its command going (probe f35cc554's `sleep 30`
read `done`/0 while it ran). Such a command is in `background_tasks` as
`{task_type: "local_bash", description: <the command, out of Codex's
/bin/bash -lc wrapper, cut at 60 characters>}` and its end wakes the root
with its output. One that a later turn finishes (the model polled it) leaves
the list and wakes nothing. Subagent bookkeeping (`subAgentActivity`,
`collabAgentToolCall`) never does either: it straddles the root's turn end
by an event or two as a matter of course.

```jsonc
{ "session_id": "940380fd-…", "status": "waiting",
  "background_tasks": 2, ... }
```

`history_bytes` is the other number a page cannot work out for itself: for an
adopted record that has **not yet been forked**, the size in bytes of the
Claude Code app's session file behind it — which is what a first message into
that chat re-sends to the model before a word of the new turn is answered (see
*Sending into one: the fork*). It is the same reading `source_size` is taken
from, so the two can never disagree, and it is `null` for a session the broker
started (its history is this broker's log and a resume does not re-send it)
and for one already forked (it runs on a file of the CLI's own). The broker
reports bytes; turning them into a dollar estimate is the page's.

```jsonc
{ "session_id": "330129f0-…", "origin": "claude-code-app",
  "cli_session_id": null, "history_bytes": 4812733, ... }
```

#### Which sessions are in RAM: `live`, `live_count` and `cap`

Added 2026-09-25, from the owner: "since we have a ram limit on number of
running sessions, we need a filter in search on active sessions that are
active in Ram atm."

A session's process costs the box a few hundred megabytes — "The turn shape"
above has the cgroup measurements, which are the ones to trust — and how
many fit is the box's memory's to say (below). So "is this conversation resident right now" is a
question only this broker can answer, and it answers it in two places:

| | where | what it is |
|---|---|---|
| `live` | on every session record | `true` while a process of this broker's is behind that session, `false` otherwise |
| `live_count` | on `GET /v1/sessions` | how many are resident right now |
| `cap` | on `GET /v1/sessions` | `MAX_SESSIONS`, the ceiling that count is against |

```jsonc
{ "sessions": [
    { "session_id": "6cef8566-…", "state": "running",
      "live": true,  "status": "done",   ... },   // a warm idle process: in RAM
    { "session_id": "a95bba23-…", "state": "exited",
      "live": false, "status": "done",   ... } ], // an answer to read, nothing running
  "live_count": 1, "cap": 4, "live": 1 }          // "1 of 4 live"
```

**It is residency, not turn state.** A `done` session can be live — that is
the row above, and it is the common case: a chat someone has finished
speaking to still holds a warm process until the idle reaper takes it at
`CLASSIC_IDLE_S` (never while it is busy or a request is pending: *Closing a
request* below). A `waiting` or `working` session is live by definition. And
`live` is read off the process object (`Session.is_live`) and never inferred
from `status`: both halves of that read matter, because a process whose
`returncode` is set is gone before `_reap` has recorded it, and `stop()` sets
`state` before the kill it may have to fall back on. 450 MB the box cannot
use is not 450 MB in use.

**A record off disk is never live.** `false` without consulting the file, for
the same reason `background_tasks` is `0` there: a record this broker has no
process for cannot be resident, and a record left saying `true` by a broker
that was killed would put a row in the "in RAM" filter for a process that
died days ago.

**`live` is beside `state`, not instead of it.** `state` is the lifecycle
word the record was *written* with (`starting`, `running`, `exited`) and goes
stale in a file; `live` is this broker's answer now. A listing rewrites a
stale `running` to `exited` and `live` to `false` on the same read, so the
two never disagree on the wire.

**The count is answered rather than counted.** A page cannot get it from the
rows: `?agent=` narrows them and the cap belongs to the whole box. And `cap`
is answered rather than configured into the app so that one number — the one
in `lib/loam_broker.py` — is the only copy on this box. Since 2026-11-09 it
is the backstop (30 on the CX33, 123 on the CX53), not a limit a person
meets.

**When it changes, the live list says so** — `broker.residency`, below.

#### Admission by memory, not by count (2026-11-09)

The owner, 11-09: "I want to make sure that I can have as many sessions as I
need when I'm trying to run different things in parallel, and I'm not
bottlenecked by the number of sessions or anything else. And also, my
sessions can grow to have as much RAM and size as they need."

`start_session` asks, in this order:

1. **Is the session named already running?** Then it is returned and
   nothing is admitted.
2. **Is there room?** (`Broker.room`) The box's `MemAvailable`, less
   `SESSION_START_MB` (450) for each start in flight, must leave
   `MEMORY_RESERVE_MB` for everything that is not a chat plus 450 for the new
   process; and the broker cgroup's own room under its `MemoryMax`, less the
   same, must hold 450. The count is checked only against `MAX_SESSIONS`, a
   sanity backstop of one per 256 MB of RAM.
3. **If not, end the least recently used idle session** (`make_room`) --
   thread or classic, one pool -- and ask again, as often as it takes.
   Idle is `Session.busy()` answering None (not mid-turn, not asking, not
   waiting on work it launched) and nothing for `EVICT_GRACE_S` (15 s). Its
   id resumes; the cost is a respawn on its next turn. Logged as `room:
   ending <agent>/<id> (idle N s) for <agent>: memory, N MB available,
   reserve N`.
4. **Only when nothing idle is left, `429`:**

```jsonc
{ "error": "There's no room for Ada right now: the box is short of memory and all four sessions on it are busy (Iris and Pax are mid-turn, Juno is waiting on you). Try again when one finishes.",
  "reason": "memory",                       // or "backstop"
  "cap": 30, "live_count": 4,               // `cap` is the backstop
  "memory": { "available_mb": 2210, "cgroup_room_mb": null, "reserve_mb": 2048, "need_mb": 450, "starting": 0 },
  "busy": [ { "session_id": "…", "agent": "iris", "status": "working" }, … ],  // working | needs_input | waiting | starting
  "hint": "4 sessions are running and every one is busy, and the box has 2210 MB available against a 2048 MB reserve and 450 MB to start one. Try again when one finishes, or end one with DELETE /v1/sessions/<id>; its id still resumes." }
```

| | CX33, 7,750 MB | CX53, ~31,600 MB |
|---|---|---|
| `MEMORY_RESERVE_MB` = max(2048, 12% of RAM) | 2,048 | 3,792 |
| `MAX_SESSIONS` (backstop) = max(16, RAM / 256) | 30 | 123 |
| simulated, every session busy (tests/test_broker_seats.py) | 3 admitted with 4,100 MB used elsewhere | 48 admitted with 6,000 MB used elsewhere |

**Idle sessions leave memory.** A thread member's after `THREAD_IDLE_S`
(600 s), a classic chat after `CLASSIC_IDLE_S` (1,800 s; it was an hour),
both only when not busy -- not mid-turn, not asking, nothing it launched
still out -- and never for age alone: the hour backstop (`IDLE_TIMEOUT_S`)
that ended every session went 2026-11-10 (*A turn has no clock*, under
Threads). **Under pressure** -- `MemAvailable` below the
reserve because sessions grew -- the reaper also ends the least recently
used idle session, one per minute's pass (`Broker.relieve`), before the
kernel has to choose.

**No ceiling, and an order.** A session grows as large as it needs, and
since 2026-11-16 nothing bounds the chats together either:
`loam-session-broker.service` has no `MemoryHigh` or `MemoryMax`. What
stands if the box truly runs out is the kernel's OOM order
(`lib/loam_config.py`): every chat's CLI is started under `choom -n 800`
(`CHILD_OOM_SCORE_ADJ`, the agents' `AGENT_OOM_SCORE_ADJ`), which its MCP
servers inherit, as is every other agent run on the box -- engineer jobs,
wakes, crons, the reviewer. The broker itself runs at the
unit's 100 (`CORE_OOM_SCORE_ADJ`, the lowest a user unit may take), the
plugins gateway too, and Caddy, Authelia, the app and the claim service at
the system's 0. So the kernel ends an agent run first, and reaches the
broker and the rest of Loam's core only if one of them is itself the
runaway. Until 11-16 the unit was `MemoryHigh=80%`/`MemoryMax=88%` at
`OOMScoreAdjust=500`, which ranked the broker above every job and wake;
until 11-09 `MemoryHigh=28%`/`MemoryMax=32%`, sized for a cap of four.

#### The title, which is the transcript's title

A session's name is **the title the transcript writer filed it under**, read
out of the vault transcript's frontmatter (`loam_adopt.transcript_title`,
keyed on the CLI session id — the fork's id where there is one). The broker
makes no model call of its own for a name.

It used to make one, and that was the defect. Calling
`loam_transcript.generate_title` here was meant to give one conversation one
name, on the reasoning that one function is one answer — and it is not, since
the two calls read different text at different moments. Caused 2026-09-22:
the home screen said "Household note and image attachment" and the vault said
"Attachment check". There is no arbitrating between two model calls, so one
of them stopped; the writer's stayed, because the vault is the record and a
transcript is named once and never renamed.

**When it is read.** After a turn's result, in a thread, polling
`cfg.TITLE_POLL_WAITS` (2, 4, 8, 15, 20, 30 seconds). The writer files this
same conversation on the Stop hook the same turn fires and its titler has a
45-second budget, so a single read straight after the result would miss on
the first turn every time. A session still unnamed when the waits run out is
tried again at the end of the next turn — a file read costs nothing, and a
session that is never spoken to again keeps its stand-in.

Until it answers — and if it never does — the title is **the opening words of
the first message**, cut at a word to 60 characters with an ellipsis. A
session is never nameless: the design draws a title on every row, and the
first thing a person recognises their own session by is what they said to
start it.

`title_source` on the record says what named it: `null` for the stand-in,
`"vault"` for the filed title, `"person"` for a rename. It is what stops the
title being asked about again, and what the one-time sync reads.

`PATCH /v1/sessions/<id>` with `{"title": "..."}` renames, a person's name
outranks the vault's and is never overwritten by anything here.
`{"title": null}` clears it, which puts the opening words back rather than
blanking the row, and puts the session back in the hands of the vault's
title.

**Once, at start**, `sync_titles()` gives every record the vault's title for
its session, skipping any marked `"person"`, and prints each change with both
names. That is what the records named by the old titler get. A record written
before `title_source` existed carries no mark, and the honest reading is that
nothing on it records a rename — every title on the box when this landed came
from one of the two titlers — so they are synced and the lines in the log are
what makes a wrong one recoverable.

#### Which surface a conversation was held on

Every process the broker launches carries `LOAM_SURFACE=loam-ui`, and a
session adopted from the Claude Code app and forked carries
`LOAM_ORIGIN=claude-code-app` beside it. The transcript writer records both
in the frontmatter it files (`surface:`, and `origin:` when there is one).

The environment is the only place this can be said. A broker session is the
same CLI, in the same workspace, under the same settings and hooks as a
conversation the owner has in the app or at a terminal — that is the whole
trick of the broker — so nothing in the session file distinguishes them, and
every transcript written before 2026-09-22 says `surface: claude-code`. The
launcher knows because it is the launcher. Absent means `claude-code`, which
is what an unbrokered session is; existing files are not rewritten, because a
surface is a fact recorded at write time and not one to be inferred later.

#### Pin and archive

*The PATCH that set these retired 2026-11-12 ("The classic chat routes, retired"); the fields stay on the records, and a thread has its own pin and archive.*

`PATCH` with `{"pinned": bool}` and `{"archived": bool}`, either or both,
alongside `title`. They are on the record so they follow the owner from the
phone to the laptop; a pin in a browser is a pin on one device.

**The route answers for a session whose process ended weeks ago**, which is
most of what a home screen lists — the record is loaded from disk, no process
is started and nothing counts against the cap. `GET /v1/sessions/<id>` does
the same, for the same reason.

Archived sessions are omitted from `GET /v1/sessions` unless `?archived=1`
(`1`, `true` or a bare `?archived`; `?archived=0` is off). **Archiving does
not end a process.** A session put out of sight is still one of the four the
box can run, so when the archived session is still running the answer carries
a `note` saying so and naming `DELETE` — a page that hid a running agent
without saying so would be hiding a running agent.

A `PATCH` that asks for nothing is a `400`, as is a `title` that is not a
string or null, or a `pinned`/`archived` that is not a boolean.

#### The live list: `GET /v1/sessions/events`

*Since 2026-11-12 the stream carries every record, archived or not; `?archived=1` changes nothing.*

One SSE stream of **the same `broker.session` snapshot** the per-session
stream leads with, sent whenever any session's record is written: a status, a
title, a last message, a pin, an archive, a brand-new session. Same bearer
token, same opening comment.

It is fed from `Session.save()` — every change to a record goes through
there — rather than from each caller, which is what stops the live list
silently missing the next field somebody adds.

It opens with the listing as it stands, newest first, so a page that connects
before it fetches anything already has the screen; `?archived=1` widens that
opening burst the way it widens `GET /v1/sessions`. After the burst, **every**
record change goes out, archived or not: a page that filters archived rows
still has to be told the moment a row becomes archived, or it keeps drawing
one.

**`broker.residency` is the one item on this stream that is not a record**
(2026-09-25): `{"type": "broker.residency", "live_count": 2, "cap": 4}`, the
count against the cap. It closes the opening burst — so a client that only
ever opens this stream has the whole screen, rows and total, and a reconnect
re-states it — and goes out again whenever the count moves: a process
starting, one exiting, one ended or reaped for idleness. It does **not** go
out on a record write that changed no residency, which is most of them (a
pin, a title, a seen mark).

Every row carries its own `live`, so a client filtering to what is in RAM
needs nothing more than the snapshots. This event exists because the total
against the cap is a fact about the box and not about any row.

There is no `?from=` here and there cannot be. The snapshot is not an event:
it carries no `seq`, it is never logged, and there is nothing to reconnect
*at*. A client applies whichever snapshot arrives last per `session_id`, and
reopening the stream re-sends the whole list, which is by definition current.
Everything in "The snapshot is not an event" below applies here word for
word.

#### Both streams open the same way

```
: open ping=15000

data: {"type": "broker.session", ...}
```

The opening comment states the stream's own keepalive interval in
milliseconds, and a stream with nothing to say writes `: ping` every
`KEEPALIVE_S`. A client measuring staleness needs the number this broker
actually uses rather than one copied into a document that will disagree with
the code one day. **Changed 2026-09-18**: the per-session stream used to
write `: keepalive` and no opening comment. SSE comments are inert — no
reader can be broken by this — and one shape across both streams is one
reader in the app.

### Providers, which is what the vendor accounts are called

The owner's naming, 2026-09-18: **Providers**, not "coding accounts", on the
page, in this interface and in the designs. One word.

`POST /v1/sessions` accepts `{"agent", "provider"}`. `claude-code` is the
default; `codex` is the second accepted value since 2026-10-01 (see "Codex"
below). Any other value — one of the two named below, or one nobody has heard
of — is a `400` whose message says which are accepted and points at
`GET /v1/providers`. The record carries `provider`, and a record written
before 2026-09-18 is answered as `claude-code`. A resume that names no
provider resumes on the record's own, and one that names a different provider
is a `409`: neither CLI can read the other's conversation.

```jsonc
GET /v1/providers?agent=ada
{ "providers": [
  { "id": "claude-code", "name": "Claude Code",
    "connected": true, "reason": null,
    "usage": { ... },                         // or null; see below
    "login": { "expires_at": "2026-09-24T09:07:49Z",   // or null
               "days_left": 1,                         // or null
               "source": "credentials-file",
               "account": "someone@example.com" },     // or null; "Signing in to any provider"
    "cli_version": "2.1.322",                 // the build `resolves_to` was read from
    "models": [
      { "id": "fable",  "name": "Fable",  "default": true,
        "resolves_to": "claude-fable-5-2" },  // on every row; see below
      { "id": "opus",   "name": "Opus",   "default": false },
      { "id": "sonnet", "name": "Sonnet", "default": false },
      { "id": "haiku",  "name": "Haiku",  "default": false },
      { "id": "best",   "name": "Best available", "default": false },
      { "id": "opusplan", "name": "Opus while planning, Sonnet after", "default": false },
      { "id": "fable[1m]",  "name": "Fable (1M context)",  "default": false },
      { "id": "opus[1m]",   "name": "Opus (1M context)",   "default": false },
      { "id": "sonnet[1m]", "name": "Sonnet (1M context)", "default": false }
    ],
    "efforts": ["low", "medium", "high", "xhigh", "max"] },
  { "id": "codex", "name": "Codex", ... },   // see "Codex" below
  { "id": "grok",     "name": "Grok",     "connected": false, "reason": "not wired",
    "models": [], "efforts": [] },
  { "id": "opencode", "name": "OpenCode", "connected": false, "reason": "not wired",
    "models": [], "efforts": [] }
] }
```

The two that are not wired are named rather than left out: the page has a
row for each, and "Grok — not wired" is an answer where a missing row is a
bug report. They carry **no `usage` key and no `login` key at all**, so there
is no zeroed object to be drawn as a full quota bar and no null date to be
drawn as an expiry. `models` and `efforts` they do carry, as **empty lists** —
a client reads "nothing on offer here" without a branch.

**The models are the CLI's own aliases**, read off the binary on this box
(2.1.270, 2026-09-21): `sonnet`, `opus`, `haiku`, `fable`, `best`, the three
`[1m]` long-context variants, and `opusplan`. The four families come first
because they are what a person picks; a client is free to draw only the head
of the list. `--model` also takes a full model id (`claude-fable-5-1`), which
is why this interface accepts one and why the list does not enumerate them:
ids age out with every release, and a menu of stale ids is worse than four
families that never move. The **display names are this interface's** — the
CLI has none — and `default: true` marks the model that persona's own
`.claude/settings.json` names, read from that file rather than restated here.
With no `?agent=`, the answer is the model every persona shares, and nothing
is ticked when they disagree.

**`resolves_to` says what each alias runs on today** (2026-11-10), on every
Claude Code model: the concrete id the installed CLI maps it to — `fable` →
`claude-fable-5-2` on 2.1.322, where 2.1.319 said `claude-fable-5-1`. A page
draws it where the alias alone would leave a person guessing ("Fable 5.2"),
without waiting for a reply to have been recorded on it. It is **read from
the installed build**, not probed: the CLI carries its alias table baked into
its binary (`aliases:{opus:{default:"claude-opus-5-5",…},…,fable:{default:
"claude-fable-5-2",…}},defaults:{},best:"opus"`), and `cfg.alias_table()`
finds it in the file `~/.local/bin/claude` points to (~0.25 s over 340 MB,
no session, no model call), once per build: it is kept until the symlink's
target, or that file's size or mtime, changes, so a CLI bump shows on the next
read without a broker restart. `cli_version` on the row is that build. The
`[1m]` variants resolve to the same model (the long context is a flag, not an
id); `best` resolves through the family the build names (opus on 2.1.322,
fable on 2.1.319); `opusplan` resolves to opus's id and adds
`resolves_to_after_plan` (sonnet's). `per_provider` (Bedrock, Vertex, …) is
not read: these sessions are first-party. A build whose table this cannot
find answers `resolves_to: null`, never a guess. Codex has no aliases: its
models are the concrete ids `model/list` answers, so its rows carry no
`resolves_to` (read `id`). The app's `/api/providers` relays the answer whole.

**The efforts are the five `--effort` takes**: `low`, `medium`, `high`,
`xhigh`, `max`. There is a sixth in the CLI, `ultracode`, and it is
deliberately not offered: the CLI pins it behind `xhigh` and refuses it
outside an interactive terminal ("Run /effort ultracode in an interactive
terminal to release the pin"), and this surface is neither.

**`connected` for `claude-code` is judged on the login the sessions actually
use** — the CLI's own `~/.claude/.credentials.json`, in place, which is what
`bin/wake` and every broker session authenticate with. The *refresh* token's
expiry is the line that matters: the access token is refreshed whenever the
CLI runs, so an expired access token is an ordinary Tuesday and an expired
refresh token is a login that has to be made again by hand. `reason` is
`null` when connected and a sentence when not (`no login on the box`, `the
login has expired and must be made again`). No token, and no piece of one, is
returned, logged or kept.

#### `login`: when it expires, and which credential it is

```jsonc
"login": { "expires_at": "2026-09-24T09:07:49Z",   // ISO UTC, or null
           "days_left": 1,                          // whole days, or null
           "source": "credentials-file",
           "account": "someone@example.com" }       // 2026-11-11; or null
```

`account` is whose login it is, out of the CLI's settings file, and only
under the login file; "Signing in to any provider: the contract" says where
it comes from and when it is null.

Added 2026-09-22 so that a page can say **"Claude Code login expires in 3
days"** without knowing that a login is a file. It is on the wired row only,
and it is **read from the credential on every call and never cached** — the
CLI rewrites that file whenever it refreshes an access token, so anything
held in memory here would be a copy that disagrees with the box.

**`expires_at` is the *refresh* token's expiry**, which is the date a person
has to act on. The access token beside it expires every few hours and the CLI
renews it silently; the refresh token does not renew itself. Measured on this
box: `refreshTokenExpiresAt` read `2026-09-24T09:07:49Z` on 09-09, on 09-11
and again on 09-22 — the same instant each time, while the file itself was
being rewritten daily. **It does not roll forward with use.** When it passes,
every session this broker starts stops working, and `connected` goes false
with the sentence above.

**`days_left` is whole days, rounded down.** `1` means at least one full day
remains; `0` means less than a day; it goes **negative** once the login has
expired, rather than resting at zero, so "how overdue" is readable too. A
page warning at `days_left <= 3` therefore warns from a little under four
days out — early rather than late, which is the right way round for a thing
that can only be fixed by a person.

**`source` is which credential a session would actually authenticate with**,
read off the broker process's own environment, because that environment is
what a session inherits. One of:

| `source` | what it is | has an expiry? |
|---|---|---|
| `credentials-file` | the CLI's own `~/.claude/.credentials.json`, in place | yes |
| `oauth-token` | `CLAUDE_CODE_OAUTH_TOKEN` in the environment — what `bin/wake` runs the nightly wakes on | no |
| `api-key` | `ANTHROPIC_API_KEY` in the environment | no |
| `none` | no login on the box | no |

Today it is always `credentials-file`: the broker's unit passes no credential
at all (see "Why it is here and not in the app"), so its sessions read the
file, and **the nightly wakes and the app do not share a credential** — the
wakes' token expires in 2027 and cannot cover for the file expiring. `source`
is derived rather than hard-coded so that the day somebody does put a token
in the unit, this says so instead of going on naming a file nobody reads.
`expires_at` and `days_left` are `null` for every source but the file:
nothing on this box can read an expiry out of an opaque token, and a made-up
date is worse than no date.

### Making the login again

`~/.claude/.credentials.json` is the one thing here that only a person can
renew, and until 2026-09-22 renewing it meant a terminal on the box. These
four routes put it on the page. **What they are built around was measured,
not assumed** (CLI 2.1.270, pinned 09-16, on a pty on this box, 09-22):

* **Without a TTY there is no flow at all.** `claude auth login --claudeai`
  with stdin at `/dev/null` and stdout a pipe prints **nothing** and hangs
  until it is killed (`timeout` exit 124). There is no `--headless`, no
  `--no-browser`, and no device-code branch. A route that shelled out to it
  the ordinary way would hang forever with nothing to show. So the broker
  gives it a pty and makes that pty its controlling terminal.
* **Under a pty it prints, in order:** `Opening browser to sign in…`; then
  `If the browser didn't open, visit: <url>` with the URL written **twice** —
  once as an OSC-8 hyperlink target and once as visible coloured text; then
  `Paste code here if prompted > `, and blocks on a person. Stripping the OSC
  sequence whole leaves exactly one URL to find, which is what `login_url`
  does.
* **The person is the browser.** The URL is an ordinary OAuth PKCE authorize
  URL whose `redirect_uri` is `platform.claude.com/oauth/code/callback` and
  whose `code=true` means "show the code rather than call anything back". The
  box never receives a callback; the person copies a code off that page and
  pastes it back. Hence `code: null` in the answer — **this flow has no code
  to show, it asks for one.** The `code?` field is kept in the shape anyway,
  for the day a CLI version prints one.
* **A wrong or expired code** answers `Login failed: Request failed with
  status code 400` and the process exits 1.
* **Starting a flow does not disturb the login already on the box.** Proved
  with a decoy credentials file under `CLAUDE_CONFIG_DIR`: started, given a
  bad code, failed — and the file was **byte-identical** afterwards (same
  md5). The real flow was then driven on the real login to the point where a
  human must act and cancelled, and `~/.claude/.credentials.json` was
  byte-identical, same mtime, same size. So this is safe to call on a login
  that still works, which is the only way anybody sane would call it.

```jsonc
POST /v1/providers/claude-code/login            // 201
{ "state": "pending",
  "url": "https://claude.com/cai/oauth/authorize?code=true&client_id=…&code_challenge=…&state=…",
  "code": null,                                  // this CLI asks for one
  "instructions": "Open the URL, sign in, and copy the code the page gives you. Paste it back with POST /v1/providers/claude-code/login/code as {\"code\": \"…\"} within 15 minutes.",
  "message": null,
  "started_at": "2026-09-22T23:34:50Z", "finished_at": null,
  "login": { "source": "credentials-file", "expires_at": "2026-09-24T09:07:49Z",
             "days_left": 1, "connected": true, "reason": null } }

POST /v1/providers/claude-code/login/code       // 200
{ "code": "the code off the sign-in page" }
→ { "state": "done", "message": null, "finished_at": "…",
    "login": { …, "expires_at": "2027-09-22T…Z", "days_left": 364, … } }
→ { "state": "failed",
    "message": "Login failed: Request failed with status code 400", … }
```

**`login` rides on every one of these answers**, in its full five-field form
(`connected` and `reason` as well as the three on the provider row), so a page
that has just finished a flow can redraw the expiry from the same response
rather than going back for the provider list.

**States.** `idle` — nothing running. `pending` — the URL is out and the CLI
is blocked on a person. `done`, `failed` — settled, with `finished_at` and,
on a failure, the CLI's own last line in `message`.

**One flow at a time, for the whole box.** Each start mints its own PKCE
verifier, so a second concurrent flow would silently invalidate the code the
person is at that moment pasting. `POST` while one is pending is therefore
**idempotent**: it answers `200` with the flow already running, same URL, same
`started_at` — which is exactly what a phone that lost its connection and
posted again needs.

**How `done` is decided, and the false positive that shaped it.** Not "the
credentials file changed since the flow began": the CLI rewrites that file
every time it refreshes an access token, so any session taking a turn during
the flow would have made a *failed* login read as a finished one. What a real
renewal does is mint a new refresh token with a new expiry, so the test is
**the CLI exited 0, the login is connected, and `expires_at` has moved** (or
there was no working login to begin with). It is judged on the credential the
sessions will authenticate with, never on what a terminal printed.

**Nothing about a credential reaches the page.** The answer carries a URL, a
state, two dates and — only on failure — the CLI's own last line. That line is
taken from a terminal buffer the person's pasted code was echoed into, so the
code is scrubbed out of it before anything is answered, and the prompt line
and the URL line are never used as the message. The raw buffer never leaves
this box.

**An abandoned flow ends itself** after 15 minutes: the process is killed and
the state becomes `failed` with `nobody finished the login in 15 minutes;
start another one`. `DELETE` ends it sooner. Either way the login on the box
is untouched — it was never written to.

**If the app is not reachable**, the same thing is done on the box with
`claude auth login` at a terminal as the box's user; it is the identical flow,
and this route is only that flow with a pty and an HTTP shape around it.

#### `usage`, and the `rate_limit_event` it is read from

No route asks the CLI for the quota. It volunteers it mid-turn as a
`rate_limit_event`, which the broker passes through verbatim like everything
else and also reads. **Read off this box's own session logs on 2026-09-18,
not guessed** — one event, verbatim, from
`state/broker/sessions/9bea9b02-….jsonl`:

```jsonc
{ "type": "rate_limit_event",
  "rate_limit_info": {
    "status": "allowed",
    "resetsAt": 1789690200,
    "rateLimitType": "five_hour",
    "overageStatus": "allowed",
    "overageResetsAt": 1790812800,
    "isUsingOverage": false,
    "unifiedWindows": {
      "five_hour":  { "utilization": 0.25, "resetsAt": 1789690200 },
      "seven_day":  { "utilization": 0.24, "resetsAt": 1790136000 },
      "seven_day_overage_included": { "utilization": 0.39,
                                      "resetsAt": 1790136000 }
    } },
  "uuid": "b0f237a8-…", "session_id": "9bea9b02-…", "seq": 41 }
```

`utilization` is a fraction, not a percentage: `0.25` is a quarter of the
five-hour window used. `resetsAt` is a **unix epoch in seconds** (the CLI's
`.credentials.json` uses milliseconds; these do not).

**The windows are copied by name, never enumerated.** In that one real
session `seven_day_overage_included` was absent at `seq 4` and present at
`seq 41` — a fourth window appeared mid-session — and a reader that listed the
windows it knew about would have dropped it silently. So `usage` carries
whatever `unifiedWindows` carries:

```jsonc
"usage": {
  "provider": "claude-code",
  "seen_at": "2026-09-18T21:40:12Z",     // when the broker saw it
  "session_id": "<the session it arrived on>",
  "status": "allowed", "is_using_overage": false,
  "five_hour": { "utilization": 0.25,
                 "resets_at": "2026-09-18T00:10:00Z",   // the epoch, as an instant
                 "resets_at_epoch": 1789690200 },       // and as the number
  "seven_day": { ... },
  "seven_day_overage_included": { ... }   // when the event carried one
}
```

The reset times are answered both ways on purpose: a page showing "resets at
midnight" wants the instant and a page counting down wants the number, and
neither should have to convert an epoch it was handed without units.

The last reading seen on **any** session's stream is merged into
`state/broker/provider-usage.json` (since 2026-10-14 a merge, not a
replacement, and stamped `source: "event"`; an on-demand read replaces it
whole -- see "Usage on demand" under Codex), and the route reads that file rather than
anything held in memory, so a restart — or a day on which nobody takes a
turn — does not blank the page. `usage` is
`null` until one has been seen, which is not a zeroed page and must not be
drawn as one.

### A chat's own model and effort

*`POST /v1/sessions` and `PATCH /v1/sessions/<id>` retired 2026-11-12; `Session.set_choices` below is what a thread member's model change still goes through. See "The classic chat routes, retired".*

The design's + menu has "This chat: Model, Effort", stored per chat. Both are
`null` on a new record, and **`null` is not a gap**: it means the persona's
own setting, which is the state every session was in before 2026-09-21.
Nothing is defaulted in this broker, because a default written here would pin
a session to whatever this file said on the day it started — the owner changing
a persona's model would then change nothing they could see.

**At the start.** `POST /v1/sessions` takes `{"model"?, "effort"?}`. What is
given goes on the launch line as `--model` and `--effort`, and onto the
record; what is not given puts no flag there at all and the CLI reads the
persona's `.claude/settings.json` itself. A value the CLI would not take is a
`400` that names what it does take, rather than a launch that dies.

**Mid-chat.** `PATCH /v1/sessions/<id>` takes the same two fields, and what
it does depends on whether a process is up.

- **Running**: a live CLI does not re-read its launch flags, so it is told
  the only way it can be told — its own commands, sent as a user message on
  the same stdin a person's words go down: `/model opus`, `/effort high`,
  value and all. The record updates and the answer says
  `"applied": "sent to the running session: /model opus"`.
- **Ended**: the record updates and the flags go on the next launch. The
  answer says `"applied": "on the record; applies at the next launch"`.
- **`null`** puts the chat back on the persona's setting, and it takes effect
  at the next launch even on a running session: `/model` with no value opens
  a picker, and there is nobody at this end of the pipe to answer it.

The command is **not** logged as `broker.user_message` — a command is not
something a person said, and drawing `/model opus` in the transcript would
read as the owner having typed it. What a client learns from is the record,
which arrives on the live list as `broker.session`.

**What the CLI does with the command is visible in the chat, and that is
worth knowing before you draw it.** Caused on this box, 2026-09-21: an Ada
session started on `sonnet` answered its first turn on `claude-sonnet-5`; a
PATCH to `opus` produced an assistant message of the CLI's own —

> Set model to `Opus 5` for this session only
> `.claude/settings.json` pins `Fable 5.1` — that applies on restart

— and a turn record whose `model` is `<synthetic>` and whose cost is `0`; the
next message then answered on `claude-opus-5`. So a model change costs one
extra turn record on the session and moves `last_message` to the CLI's
acknowledgement until the persona next speaks. Left as it is on purpose: the
acknowledgement is true, and it is how a person sees that the change landed.

### Signing in to any provider: the contract

*(2026-11-11.)* Every provider signs in from the app through one set of
routes and one answer, so a page draws every provider's card with one
component and a provider added later needs no new page code. Each row of
`GET /v1/providers` carries **`sign_in`**:

```jsonc
"sign_in": {
  "kind": "paste-code",                       // or "device-code"
  "label": "Sign in with Google",             // the button's words
  "account": "someone@example.com",           // whose login it is; null when signed out or unnamed
  "start":  "/v1/providers/antigravity/login",   // POST
  "status": "/v1/providers/antigravity/login",   // GET
  "cancel": "/v1/providers/antigravity/login",   // DELETE
  "code":   "/v1/providers/antigravity/login/code",  // POST {"code"}; null for device-code
  "sign_out": "/v1/providers/antigravity/logout"     // POST; null where not offered
}
```

`sign_in` is `null` on a row with no login to make (an unwired provider).
The kinds:

| kind | the person | the box |
|---|---|---|
| `paste-code` | opens `url`, signs in, pastes back the code the landing page shows | the flow turns `done` or `failed` on the code |
| `device-code` | opens `url` and enters `code` there | the CLI polls and the flow turns `done` by itself; GET to see it |

Every flow route answers the same snapshot:

```jsonc
{ "provider": "antigravity", "kind": "paste-code",
  "state": "pending",            // idle | pending | done | failed
  "url": "https://accounts.google.com/o/oauth2/auth?…",
  "code": null,                  // device-code: the code to show, while pending
  "accepts_code": true,          // a pasted code is taken now
  "instructions": "Open the link, sign in with Google, and paste back …",
  "message": null,               // the CLI's own words when failed; never the code
  "started_at": "…", "finished_at": null,
  "expires_at": "…",             // when this flow gives up (15 minutes)
  "login": { …the row's login object… } }
```

Rules every provider keeps: `POST start` is idempotent (`201` when it began a
flow, `200` with the same URL while one is pending) because a second flow
would void the first one's code; one flow per provider at a time; a wrong
code is a `200` with `state: "failed"` and the CLI's sentence, not an error;
`POST code` with nothing waiting is `409`, on a `device-code` provider
`405`; the credential stays where the CLI keeps it, and no token, code or
piece of either is ever answered or logged.

**The button's words** (`label`, 2026-11-11) are each provider's own: the
account a person signs in with, which is what each CLI's flow says.

| provider | `label` | the flow's own words |
|---|---|---|
| `claude-code` | Sign in with Claude | `claude auth login --claudeai`: a Claude account with a subscription |
| `codex` | Sign in with ChatGPT | the Codex CLI's own "Sign in with ChatGPT" |
| `antigravity` | Sign in with Google | `agy acp --login`: "log in with Google" |

**"Signed in as …"** is `sign_in.account`, which is the row's own
`login.account` while the row is `connected` and null otherwise, so the
card reads it from the one object it draws. Every source is a local read
with no network call and no process:

| provider | where the account comes from |
|---|---|
| `claude-code` | `oauthAccount.emailAddress` in `~/.claude.json`, the CLI's settings file, which `claude auth login` writes and `claude auth status` prints as `email` (2.1.322, measured 2026-11-11: the two agree). Named only when the sessions run on the login file (`login.source` `credentials-file`): under a token or key in the environment the settings file's account may be someone else's, so it is null. Cached on the file's mtime and size; nothing else in the file is read. |
| `codex` | the `email` claim of the id token in `auth.json` (unverified, never used for access), as before |
| `antigravity` | the `Logged in as <email>.` line `agy acp --login` printed, kept in `state/broker/antigravity/account.json`. Its token file is not read for one: no login has been made on this box, so its format has never been seen, and reading an unseen credential file for an email is a guess about a secret. A login made outside the app (the TUI) leaves `account` null. |

**Signing out** is offered on every wired provider since the afternoon of
2026-11-11, each through its CLI's own command, measured under a decoy
home so the box's logins were never touched:

| provider | what `POST …/logout` runs | what it removes |
|---|---|---|
| `claude-code` | `claude auth logout` (2.1.322) | `~/.claude/.credentials.json`, and `oauthAccount` from `~/.claude.json`. It also calls Anthropic, and succeeded against a fake token. |
| `codex` | `codex logout` in `CODEX_HOME` (0.167.1) | `auth.json`; no network on the measured run; a second run prints `Not logged in` and exits 0 |
| `antigravity` | (its CLI has no headless logout) | the token file and `account.json` |

What a sign-out stops: every app chat on that provider, from its next turn,
until a sign-in from the same card. For **Claude Code**, the
wakes, crons, the reviewer and engineer jobs run on the long-lived token
in `wake.env` and are untouched. The route answers **`409`** while any live
chat on that provider is working, asking or waiting on its children, so a
sign-out never lands under a turn. `removed` is whether the login was there
and is gone; a CLI that refuses answers `200` with `removed: false` and its
own words in `message`.

### Signing in to Antigravity

`agy acp --login`, under `antigravity`, kind `paste-code`. **What the CLI
does, measured on this box, 1.5.6, 2026-11-11, under a decoy `HOME`** so the
box's `~/.gemini` was never touched:

* It needs **no pty** (unlike `claude auth login`). With stdout a pipe it
  prints in 0.2 s `Open this link in a browser to log in with Google:`, a
  Google OAuth URL (PKCE, `redirect_uri=https://antigravity.google/oauth-callback`),
  then `Then paste the authorization code, or the full URL of the page you
  land on, and press Enter:` and reads one line from stdin. The broker
  answers `pending` once both the URL and that prompt are out.
* **There is no device-code branch** and no headless `logout`: the only
  other ways in are an API key (`GEMINI_API_KEY`, which bills the Gemini
  API, not their account) and gcloud's credentials (`AGY_ADC_AUTH`), both
  stripped from a session's environment. So this is Claude Code's flow, one
  link and one code, without the pty.
* A fake code, bare or inside a landing URL, exits 1 in about 0.1 s with
  `couldn't log in: token exchange failed: oauth2: "invalid_grant" "Bad
  Request"`, which is the flow's `message`. The code is not in the CLI's log.
  Stdin at EOF is `login cancelled: no authorization code was entered`.
* On success it prints `Logged in as <email>.` and exits 0 (read out of the
  binary; no real sign-in has been made here). The flow is `done` only when
  it exited 0 **and** a fresh `agy models` answers signed in; the email is
  kept in `state/broker/antigravity/account.json` as `login.account`.
* The token, with no Secret Service keyring on this box, is
  `~/.gemini/antigravity-cli/antigravity-oauth-token` (the path the CLI
  opens, read with strace), owned by the box's user, which every lane runs as.
  `POST /v1/providers/antigravity/logout` removes it and the account file;
  the CLI's own `/logout` exists only in its TUI and is refused in print
  mode.

### Codex, the second provider (2026-10-01)

A chat can run on Codex instead of Claude Code, chosen per chat with
`"provider": "codex"` on `POST /v1/sessions`. The protocol is Codex's own
long-lived one, `codex app-server` over stdio (JSON-RPC 2.0, one object per
line), translated onto this interface's existing events so the page draws a
Codex thread with the code that draws every other. The machinery is
`CodexSession` in `bin/session-broker`; the binary, home, login, models and
translation are `lib/loam_codex.py`; `tests/test_broker_codex.py` proves it
(add `--live` for the real binary, which spends nothing and mints one device
code).

**What is not here yet:** the persona. A Codex thread runs in the persona's
workspace on Codex's own defaults — no persona instructions, no MCP servers,
no hooks, no sandbox choice, and so no transcript in the vault and no vault
title (the row keeps the opening words). Compaction is refused (`409`).
Those are the next brief's, and it builds on the names below.

**The binary** is pinned the way the Claude CLI is: `@openai/codex` installed
into its own npm prefix at `~/.local/share/codex/versions/<version>/`, and
`~/.local/bin/codex` a symlink to that prefix's `node_modules/.bin/codex`.
Moving the pin is moving the symlink; rollback is one `ln -sfn`. No global
package. 0.159.3 since 2026-10-01.

**`CODEX_HOME` is `agents/state/broker/codex/`**, private (0700), gitignored
with the rest of `state/`. Every Codex process the broker starts gets it, so
the login (`auth.json`), Codex's config, its thread rollouts and its sqlite
state live there and never in a persona's workspace or `~/.codex`.
`OPENAI_API_KEY`, `CODEX_API_KEY` and `OPENAI_BASE_URL` are removed from a
Codex child's environment: the only credential a Codex session has is the
ChatGPT login in that home.

**Processes are ended by group.** `codex` is a node wrapper around a native
binary; SIGKILL on the wrapper orphans the binary. Every Codex process here is
started as its own session leader and signalled with `killpg`.

#### The provider row

```jsonc
{ "id": "codex", "name": "Codex",
  "connected": false, "reason": "no login on the box",   // or true, null
  "usage": null,                                          // or a reading; below
  "usage_reason": "no reading yet: the read failed and no Codex turn has run",  // or null
  "login": { "expires_at": null, "days_left": null,
             "source": "none",            // or "chatgpt", "api-key"
             "account": null,             // the id token's email, when signed in
             "plan": null,                // its chatgpt_plan_type
             "status": "Not logged in" }, // what `codex login status` prints
  "models": [
    { "id": "gpt-6.1-sol", "name": "GPT-6.1-Sol", "default": true,
      "efforts": ["low", "medium", "high", "xhigh", "max", "ultra"],
      "context_window": 258400 },
    { "id": "gpt-6-luna", "name": "GPT-6-Luna", "default": false,
      "efforts": ["low", "medium", "high", "xhigh", "max"],
      "context_window": 258400 },
    ...
  ],
  "efforts": ["low", "medium", "high", "xhigh", "max", "ultra"] }
```

`expires_at` and `days_left` are null on a Codex login always, not just
today: the file states no refresh-token expiry, and the access token's own
`exp` is refreshed in use and is the wrong date to warn on. `models` is what
the app-server's `model/list` answered (it needs no login), cached in
`state/broker/codex-models.json`, read at broker start and again at every
Codex session's start; hidden models are left out. Efforts differ per model,
so each model carries its own and the row's list is their union. `default`
is the model Codex calls its default: no persona's settings name a Codex
model. `context_window` is the window a turn on that model states, and null
when unknown: `model/list` states none, so it is read out of Codex's own
catalogue, `CODEX_HOME/models_cache.json`, as `context_window` ×
`effective_context_window_percent` (272000 at 95% is the 258400 every turn
reported on 2026-10-07, Luna and Astra alike). A turn's own `context.window`
wins over it on the page. `model` and `effort` on `POST` and `PATCH` are checked against this
list for a Codex chat.

#### Codex's `usage`, and `account/rateLimits/updated`

Codex states its quota on its own notification, which the app-server sends
on a turn. One, verbatim, from a `gpt-6-luna` turn on 2026-10-01
(0.159.3; the schema is `codex app-server generate-json-schema`,
`AccountRateLimitsUpdatedNotification`):

```jsonc
{ "method": "account/rateLimits/updated",
  "params": { "rateLimits": {
    "limitId": "codex", "limitName": null, "normalModelSlug": null,
    "primary":   { "usedPercent": 2, "windowDurationMins": 300,   "resetsAt": 1790894969 },
    "secondary": { "usedPercent": 0, "windowDurationMins": 10080, "resetsAt": 1791481769 },
    "credits": { "hasCredits": false, "unlimited": false, "balance": "0" },
    "individualLimit": null, "spendControlReached": null,
    "planType": "plus", "rateLimitReachedType": null } },
  "emittedAtMs": 1790878302611 }
```

It is an **account** reading, so it is kept once for the login, not per
thread, in `state/broker/codex-usage.json`, and the row reads the file: a
restart, or a day nobody takes a Codex turn, does not blank it. It is served
in **the Claude row's shape**, so the page draws it without a second reader:

```jsonc
"usage": {
  "provider": "codex", "seen_at": "2026-10-01T18:11:42Z",
  "session_id": "<the session it arrived on>",
  "account": "<the login's email>",      // the login it was read on
  "status": null,                        // rateLimitReachedType; null = none reached
  "is_using_overage": null,              // Codex has no such flag
  "five_hour": { "utilization": 0.02,    // usedPercent / 100: a FRACTION, as Claude's
                 "resets_at": "2026-10-01T22:49:29Z", "resets_at_epoch": 1790894969,
                 "window_mins": 300 },
  "seven_day": { "utilization": 0.0, "resets_at": "2026-10-08T17:49:29Z",
                 "resets_at_epoch": 1791481769, "window_mins": 10080 },
  "credits": { "hasCredits": false, "unlimited": false, "balance": "0" },  // verbatim
  "planType": "plus" }                                                     // verbatim
```

- **Windows are named by their length**: 300 minutes is `five_hour`, 10080
  is `seven_day`, any other length is `window_<n>_min` (so a plan whose
  primary window is not five hours is not drawn as one), and a window with
  no length falls back to its slot (`primary` → `five_hour`, `secondary` →
  `seven_day`).
- **The update is sparse**, by the schema's own description: a null window,
  credits or plan means "not stated this time" and keeps the last value.
  `rateLimitReachedType` is taken whenever present, null included, because
  null is how a reading says no limit is reached.
- **`limitId` `codex` (or none) is the row's windows.** Every other limit
  (a per-model one) is kept beside them under `buckets`, keyed by its id, in
  the same window shape plus its `limit_name`; plan and credits stay on the
  row (since 2026-10-14; before, they were dropped).
- **A reading belongs to the login it was taken on.** After a sign-in as
  someone else the row answers `usage: null` until that login's first read.
- **`source`** is `"read"` or `"event"`: which of the two below took it.

#### Antigravity's `usage`, and `/quota` (2026-11-15)

A headless Antigravity turn states tokens and no quota, so this row has no
`observe`: its reading moves only by `read()`, which is the CLI's own
`agy -p /quota --output-format stream-json` and then `agy -p /credits` the
same way (`lib/loam_antigravity.py`, `read_quota`). Both answer with no turn:
`num_turns` 0, every token count 0, an empty `conversation_id`. Kept in
`state/broker/antigravity/usage.json`, keyed on the login's account as
Codex's is.

Antigravity meters by **pool** (today `Gemini Models`, and `Claude and GPT
models`), each with a weekly and a five-hour window. No pool is the row's own,
so every window is under `buckets`, keyed by Antigravity's own id, with
`limit_name` the label the page draws. `remaining_fraction` is what is
**left**. `utilization` is what is **used**, `1 - remaining`, as on the other
two rows, and `remaining` is kept beside it as Antigravity said it:

```jsonc
"usage": {
  "provider": "antigravity", "source": "read", "seen_at": "2026-11-16T06:00:33Z",
  "account": "…", "session_id": null, "status": null,
  "is_using_overage": null, "planType": null,
  "credits": {"hasCredits": false, "unlimited": false, "balance": "0",
              "upgrade_uri": "https://antigravity.google/g1-upgrade"},
  "buckets": {
    "gemini-5h":     {"utilization": 0.010597, "remaining": 0.9894033,
                      "resets_at": "2026-11-16T10:36:17Z", "resets_at_epoch": 1794825377,
                      "window_mins": 300,   "limit_name": "Gemini Models · 5-hour"},
    "gemini-weekly": {"…": "…", "window_mins": 10080, "limit_name": "Gemini Models · Weekly"},
    "3p-5h":         {"…": "…", "limit_name": "Claude and GPT models · 5-hour"},
    "3p-weekly":     {"…": "…", "limit_name": "Claude and GPT models · Weekly"}
  }
  // "unparsed": ["…"]  only when the printed fallback met a line it could not read
}
```

Buckets go pool by pool in the order Antigravity lists them, with the shorter
window first in each pool. A pool or window the mapping does not know is still
kept: an unknown window has `window_mins` null. When the structured
`command.data` is absent, the printed form (`<pool>\t<window>\t<N>% left\t<reset>`)
is read instead, and a line it cannot read goes under `unparsed`. `credits`
is in Codex's shape, so a balance of 0 is `hasCredits: false`, which the page
draws as no line. `credits` is null when `/credits` failed. A failed `/quota`
is a failed read and keeps the last reading.

#### Usage on demand: `read()` and the refresh loop (2026-10-14)

Until 2026-10-14 both rows were observe-only, and the merge above kept a
plan's five-hour window at 80% for two days after an upgrade to `prolite`,
whose only window is the week; the ring drew the stale one because it is the
higher. Now each provider's reader (`lib/loam_provider_usage.py`) has
`observe(event)`, the sparse merge, and an optional `read()`, a snapshot
that **replaces** the kept reading whole, plan and credits included.

| provider | `read()` | cost |
|---|---|---|
| Codex | `account/rateLimits/read` on a short-lived `codex app-server` on the broker's `CODEX_HOME`; every limit from `rateLimitsByLimitId` | ~1 s; no turn, no model call |
| Claude Code | the stream-json control request `{"subtype": "get_usage", "skip_behaviors": true}` to a short-lived `claude -p`, no prompt sent: what `/usage` draws. `utilization` there is a percent and is divided by 100; a model's own weekly limit (`rate_limits.limits[]`, `kind: weekly_scoped`) is `seven_day_<model>` | ~2–3.5 s; `total_cost_usd` 0, `total_api_duration_ms` 0. The SDK calls it `usage_EXPERIMENTAL_MAY_CHANGE_DO_NOT_RELY_ON_THIS_API_YET`: an answer the mapping does not know is a failed read |
| Antigravity | `agy -p /quota` then `agy -p /credits`, each `--output-format stream-json`: every pool's windows under `buckets`, `utilization` = 1 − `remaining_fraction` (above) | ~1.9 s + ~1.5 s; `num_turns` 0, tokens 0, and eleven reads on 2026-11-15 left the Claude-and-GPT pool unchanged in its last digit |
| Grok, OpenCode | none; not wired | — |

One loop, which knows no provider, decides when: on every `GET
/v1/providers` (at most once a minute per provider, inside the route's
thread, so the answer carries the read); every five minutes while the
provider has a live session or a running engineer job (a heartbeat under
120 s and no `result.json`, its `runtime` file naming the CLI: `claude`,
`codex` or `antigravity`); and once on
the first 30-second look after a job ends. Never otherwise. A failed read
keeps the last reading. A wired provider with no `read()` says
`observe-only: …` in `usage_reason`. `LOAM_USAGE_READS=off` turns every read
off; the tests set it, because a read is a real CLI on the real login.

**Mid-chat**, the same reading is put on that chat's stream as a
`rate_limit_event` in the Claude CLI's shape — `rate_limit_info.unifiedWindows`
holding each window as above plus the CLI's `resetsAt` — with `credits` and
`planType` inside `rate_limit_info` and `provider: "codex"` on the event. A
page that already moves the ring on a Claude chat's `rate_limit_event` moves it
on a Codex chat's with nothing new. The broker does not read that event back
into Claude's file: only a Claude session's `rate_limit_event` writes
`provider-usage.json`.

#### Signing in from the app

`codex login --device-auth`, driven from the same routes as the Claude Code
login, under `codex`:

| Route | Does |
|---|---|
| `POST /v1/providers/codex/login` | Starts a flow (`201`), or answers the one running (`200`). |
| `GET /v1/providers/codex/login` | Reads it: the URL and code while `pending`, and the state, which turns `done` by itself once the code is entered. |
| `DELETE /v1/providers/codex/login` | Cancels it; the code dies with the process polling for it. |
| `POST /v1/providers/codex/login/code` | `405`: a device code is entered on OpenAI's page, and there is nothing to paste back here. |

```jsonc
{ "state": "pending",
  "url": "https://auth.openai.com/codex/device",
  "code": "XXXX-XXXXX",
  "instructions": "Open the URL, sign in to ChatGPT, and enter the one-time code. This page notices the login by itself: GET this route again for the state.",
  "message": null, "started_at": "…", "finished_at": null,
  "expires_at": "…",                  // 15 minutes, as the CLI prints
  "login": { …the row's login reading… } }
```

The code is answered only while the flow is `pending`; a finished, failed or
cancelled flow answers `code: null`. Measured 2026-10-01 on 0.159.3: unlike
`claude auth login`, the device flow needs no pty — it prints the URL and the
code to a pipe and polls.

#### A Codex thread

`POST /v1/sessions {"agent", "provider": "codex"}` starts `codex app-server`
in the persona's workspace and sends `initialize`, `initialized`,
`model/list`, then `thread/start` with `cwd` the workspace (and `model`, if
this chat picked one). The thread's id is on the record as **`thread_id`**
(null on every Claude Code record), and a resume is `thread/resume` by it.
Each message is one `turn/start` with the text — attachments named by path,
an image small enough also given as `localImage` — and this chat's `model`
and `effort` when it has them. A `PATCH` of model or effort is on the record
and rides the next `turn/start` (`applied` says so); nothing is sent to the
process at the moment of the change. `DELETE` sends `turn/interrupt` for a
turn in flight, waits for its `turn/completed`, then ends the process.

What the page receives, Codex notification → event:

| Codex | Broker event |
|---|---|
| `turn/started` | `system` / `init` (with `provider`, `model`, `turn_id`, `thread_id`) — opens the turn |
| `item/started` (agentMessage) | `stream_event` `message_start`, `content_block_start` |
| `item/agentMessage/delta` | `stream_event` `content_block_delta` / `text_delta` |
| `item/completed` (agentMessage) | `stream_event` `content_block_stop`, `message_stop`, then `assistant` with the text |
| `item/started` (commandExecution) | `assistant` with `tool_use` `Bash` `{command, cwd}` |
| `item/started` (fileChange) | `assistant` with `tool_use` `apply_patch` `{changes}` |
| `item/started` (mcpToolCall) | `assistant` with `tool_use` `mcp__<server>__<tool>` and the arguments |
| `item/started` (dynamicToolCall) | `assistant` with `tool_use` named by its tool (`<namespace>__<tool>` when it has one) and the arguments |
| `item/started` (webSearch, imageGeneration) | nothing while the item is still empty; a `tool_use` as soon as it says what it is (below) |
| `item/started` (any other tool item) | `assistant` with `tool_use` named by the item type, input the item's fields that are not empty |
| `item/completed` (a tool item) | `user` with `tool_result` (`is_error` on failure or a non-zero exit) |
| `item/completed` (webSearch) | the `tool_use` `webSearch` `{query, queries?}` (or `{url, pattern?}` for a page it opened or searched) if not drawn yet, then a `tool_result`: `N results`, then `title — url` for the first 8 and `… and K more`; an error entry is the result's text and `is_error` |
| `item/completed` (imageGeneration) | the `tool_use` `imageGeneration` `{prompt}` if not drawn yet, then `saved to <path>`, or the failure (`usageLimitExceeded <limitId>`) as an error; the image itself is never the text |
| `item/commandExecution/requestApproval`, `item/fileChange/requestApproval`, `item/permissions/requestApproval` | `broker.permission_request` (`request_id` `codex-<rpc id>`), answered on the existing permission route: `accept`/`decline`, or the requested permissions granted for the turn / none |
| `serverRequest/resolved` | the pending card is dropped |
| `thread/tokenUsage/updated` | nothing on its own; it fills the `result` |
| `account/rateLimits/updated` | kept as the row's `usage`, and `rate_limit_event` (above) |
| `turn/completed` | `result`: `success`, or `error_during_execution` / `error_interrupted` with Codex's sentence as `result`; `context {used, window}` from the last token usage and `modelContextWindow` |
| `error` | `system` / `api_retry` when Codex will retry, `system` / `provider_error` when not |

**A web search fills in at completion.** 0.159.3 starts one as
`{query: "", action: null, results: null}` and sends the query, the action
and the results only on `item/completed`; the schema has no `item/updated`.
Drawn at start, the step read `webSearch {"query":"","action":null,
"results":null}` for a search that had run and returned 19 results
(2026-10-02). So a web search or an image generation draws its call when it
says what it is, at completion at the latest, just before its result. The
cost: no running step while the search is in flight (about three seconds),
and a search in a turn interrupted before it completes is not drawn at all.

Reasoning, plans and the other notification types are not drawn. A
question for the user (`item/tool/requestUserInput`) and an MCP elicitation
are cards, the first always a question and the second a question when it is
a form with fields ("Questions" below); a server request that asks no person
anything is answered with a JSON-RPC error rather than left to hang the turn.
`total_cost_usd` is null on every Codex turn: a subscription turn has no
price to state. Proved with no login on 2026-10-01: a turn opens, Codex
retries twice five times and the turn closes as a failed `result` carrying
OpenAI's 401, and the row reads `failed`.

#### The same persona on Codex

A Codex thread runs the persona Claude Code runs; only the provider differs.

- **Instructions.** At every thread start and resume the broker writes
  `CODEX_HOME/instructions/<agent>.md` (`loam_codex.write_instructions`):
  one provider line, then `CLAUDE.md`, its `@` imports in order, and
  `_active.md` without its loader frontmatter -- the files Claude Code loads,
  in its order. The persona's `.codex/config.toml` points
  `model_instructions_file` at it; a resume also passes it as
  `baseInstructions`, because a resumed thread otherwise keeps the
  instructions it started with.
- **Tools, hooks, sandbox.** `.codex/config.toml` is rendered by
  `bin/provision --codex` from the same functions as `.mcp.json` and
  `.claude/settings.json`, and `bin/check-drift` compares it. The MCP servers
  are the same four by the same names; a tool the Claude settings deny by
  name is `disabled_tools`. The guard (`PreToolUse`) and the transcript
  writer (`Stop`) are the same scripts. `.codex/skills` links `../skills`
  as `.claude/skills` does.
- **The wall** is a permission profile (`default_permissions = "loam"`):
  writes to the workspace, the commons and the vault; `15_CONVERSATIONS/` and
  the runtime's files in the workspace read-only; reads limited to the
  system, Codex's install, the vault and ai-labs, so the shell cannot read
  `secrets.env`, `wake.env` or Codex's own login; shell network off. The
  guard also unpacks `apply_patch` into the calls it already checks, so the
  size limit and the workspace rule hold for Codex's file tool too.
  `approval_policy = "on-request"` turns an escalation into a permission
  card.
- **Trust.** Codex loads a project's `.codex/` only for a trusted project
  and runs a project hook only once its exact definition is trusted by hash.
  The broker writes both into `CODEX_HOME/config.toml` before each thread
  (`loam_codex.write_trust`): the persona workspaces, with
  `project_root_markers = []` because ai-labs is one git repository, and the
  runtime's own hooks by the hash `hooks/list` reports. A hook not written
  by the generator stays untrusted and does not run.
- **Transcript.** Codex's `Stop` hook gives the writer the thread's rollout;
  `loam_transcript.parse_codex_rollout` reads it and the file gains
  `provider: codex`. Titled by the same titler.
- **Proof:** `tests/test_codex_parity.py --live [agent]`.

### Sessions from the Claude Code app

Added 2026-09-20, because the owner asked for the conversations they had been
having in the Claude Code app — on claude.ai/code and on their phone — to be
in the app the broker serves as well. Remote Control, which served those
conversations, was retired on 2026-11-19 (the owner: "I don't use it through
app anymore"); the sessions it left are still adopted and shown, and no new
ones arise.

**They were never anywhere else.** A Remote Control session was a Claude Code
session *on this box*: same login, same persona workspace, same hooks, and
the same session file under `~/.claude/projects/<slug of the workspace>/`
that a session started at a terminal here leaves. Nothing is imported from
anywhere; the broker simply did not know about files it had not written. So
adoption is a scan and a record, and the conversation itself stays where it
is — there is no second copy of it on this box.

`lib/loam_adopt.py` is the whole of it, and it only ever **reads** a session
file. The one thing that must never happen is resuming an app session in
place: the Remote Control server that owned it could still be running, and two
processes appending to one session file is a corrupted conversation of
The owner's. Nothing on this interface does that — sending into an adopted
session forks it instead, which is *Sending into one: the fork* below.

#### What is adopted, and what is passed over

Run at every broker start and by `POST /v1/sessions/adopt`, over every
persona in the roster. A session file is adopted when all five are true:

| test | why |
|---|---|
| the broker has no record of it | an adopted session is adopted once; the route is idempotent |
| **it is not a fork of a session the broker holds** | *Not the same conversation twice*, below. Two tests in one: its id is no record's `cli_session_id`, and its first entry's uuid is no record's — a fork copies its parent's entries, uuids and all. |
| a person typed into it | the session file states the origin of every prompt, and only a person's says `{"kind": "human"}`. A wake, a cron, a probe and a turn the broker itself sent all carry no origin at all. Read off this box 2026-09-20: 855 session files, and every wake and cron prompt in them is `promptSource: "sdk"` with no origin. The other kinds that appear — `task-notification` (a subagent reporting back) and `peer` (another session writing in) — are machinery too. |
| its **last entry** is inside 14 days | The owner's "the past two weeks", and one number, `loam_adopt.WINDOW_DAYS` |
| it holds at least one message | a file with nothing in it is not a conversation |

**The last entry's timestamp, not the file's mtime, and this is not
pedantry.** Something on this box touches old session files: measured
2026-09-20, three sessions whose last turns were on 09-04, 09-05 and 08-28
carry mtimes of 09-20 01:31, 09-08 01:30 and 09-09 01:30, and dozens of
files share that same 01:30 minute on days after they were last spoken to.
mtime therefore runs **late**, which makes it a safe cheap first cut (a file
too old by mtime is certainly too old) and a false answer to "when did this
conversation last happen". Adopting on mtime would have put a conversation
from two weeks ago at the top of the home screen, dated today.

The scan is cheap by construction, because a persona has up to 230 session
files and one of them is 25 MB: mtime first, then the last entry read off
the tail of the file, then a byte-scan for the human-origin marker, and only
then is anything parsed. Measured on this box 2026-09-20: 576 files across
the seven personas, 425 MB of them, 26 adopted, 0.9 s — and that is the
expensive case, a rescan that re-reads all 26 in full.

`POST /v1/sessions/adopt` answers what it did — `adopted`, `refreshed`,
`agents` (a count per persona), `skipped` (a near miss and its reason),
`new_only` (whether it was looking for unknown files only) and the window it
used. **A record nobody has written into is refreshed rather than left
alone**, so a conversation the owner is still having in the app keeps its last
message and its place on the home screen; a record holding events of this
broker's own is settled and the scan does not touch it. Since 2026-09-21 the
refreshing is mostly done elsewhere and per record — on a read of it, and on a
poll while its thread is open, for one file instead of 27. See *Staying in step
with the app's own file*.

#### Not the same conversation twice

Added 2026-09-22, because the home screen drew one conversation twice — "Loam
phase six planning" pinned and in Recent at once, both "Now", and before it
"MuseBot and GrokBot user experience analysis". The page was right and so were
its tests: those were **two records**.

Replying to an adopted session forks it (*Sending into one: the fork*), and the
fork is a session file of the CLI's own, in the same folder, with a person in
it and today's date on it. Every test above passed, so the next scan adopted it
as a second conversation wearing the first one's title and history.

Two nets, both in `loam_adopt.candidates`:

| net | catches |
|---|---|
| **the fork ids** — every record's `cli_session_id` | the ordinary case, by id and for free, out of records the scan reads anyway |
| **the heads** — the first uuid of the file behind each record, against that record's id | a fork whose id no record carries: one the CLI minted for a turn that died before the broker read the id back |

**The CLI writes no fork mark, and the entries are the mark.** Measured on
this box 2026-09-22: `--fork-session` copies the resumed conversation into the
new file and rewrites `sessionId` on every entry to the fork's own, so nothing
in a fork's file names the session it came out of. What it does not rewrite is
the entries: the first eight uuids of a fork read here were its parent's, byte
for byte. So two files whose first entry wears one uuid are one conversation
written down twice. `loam_adopt.first_uuid` is that reading, off the head of
the file — the bookkeeping the CLI opens a file with carries no uuid, so it
walks a few lines rather than taking the first.

**The heads map to an id rather than being a set of uuids**, because the
commonest file carrying a record's head is that record's own file, which a full
rescan re-reads on purpose; a set there made every adopted record a copy of
itself and the scan refreshed nothing. Cost, measured 2026-09-22: 49 records,
30 with a source, **24 ms** warm, inside a scan that costs 240 ms at its
cheapest.

**The duplicates already made were removed once**, at start, by
`prune_fork_duplicates` in `bin/session-broker` — a record is one when another
record names it as the fork it drives, it says it came from the app, it has
never been spoken into through this broker (no log of its own) and it has never
been forked itself. Only the record goes: the session file it points at is the
CLI's, and it is also the surviving record's own history now. Four went on
2026-09-22, and each is printed with its id and title at start, because a
record removed without its name is a thing nobody can check afterwards.

#### What an adopted record says

Two fields, on **every** record the interface answers, adopted or not:

| field | is |
|---|---|
| `origin` | `claude-code-app` for an adopted session, `loam-ui` for one the broker started. A record written before 2026-09-20 has neither and is answered as `loam-ui`, which is the only kind there was. |
| `source` | the CLI's own session file this conversation is in. `null` for a session the broker started. |

The record's `session_id` is the CLI's own id for that session, which is
also what the transcript in the vault carries in its frontmatter. One
conversation, one id, wherever it is looked at. It stays that id after the
session has been forked and written into; what the fork runs under is
`cli_session_id` beside it (*Sending into one: the fork*), and a transcript
the writer files for a turn taken here is filed under the fork's id, because
that is the session the CLI ran.

`title` is the name the transcript writer already filed it under, read from
that transcript's frontmatter via the sidecar in
`agents/state/transcripts/<id>.json` — the same rule as everywhere else
here, that one conversation has one name in the app and in the vault. The
titler is not run again: it answered days ago, and asking it twice spends a
model call to maybe disagree with the record. Failing a transcript, the
opening words of the first message stand in, as they do for any session.
`status` is `done` and `state` is `exited`: it is a conversation that
happened, and no process of this broker's is behind it.

`turns` is empty: a turn record is made out of the CLI's result envelope,
which a session file does not keep, and a cost or a token count that was
never stated is not one to invent. So an adopted row shows no spend, which
is honest — that conversation's spend was never on this interface's books.

`replayed` is the third field adoption added, and it is on every record too
(`null` for a session the broker started): **how many of this session's
events come out of that file.** `record_seq` is the same number for an
adopted record, because those events are the whole of its history so far.
The next section is what it is for.

`cli_session_id` and `forked_from` are the fourth and fifth, and they are
`null` until somebody sends into the session: *Sending into one: the fork*.

`source_size` and `source_mtime` are the sixth and seventh, added
2026-09-21: **the reading of that file this record is** — how many bytes it
held and when it was last touched, at the moment it was read. They are on the
wire because they are on the record, and they are on the record because the
next reader compares them against the file to decide whether the record still
holds. `null` for a session the broker started, and `null` on a record written
before that date, which reads as "never measured" and is re-read once.

#### What an adopted session replays

Added 2026-09-20, the same day as the scan. `GET
/v1/sessions/<id>/events?from=0` on an adopted session answers the snapshot
and then its conversation, translated out of the CLI's own session file by
`loam_adopt.iter_events`. There is no log behind that record and there is
nothing to write one from — the file is the record of that conversation, and
it stays the only copy.

The translation, in this interface's own vocabulary and nothing new:

| in the session file | on the stream |
|---|---|
| a `user` entry with `origin: {"kind": "human"}` | `broker.user_message`, with the prose the person typed in `text` |
| an `assistant` entry | one `assistant` event **per content block** — the sentence said before the tools, and each `tool_use` — each carrying the message it came in with that one block in it. It is what the CLI streams live, and therefore what a client already reads. |
| a `user` entry holding a `tool_result` | `user`, verbatim |
| the end of a turn | `result` — see below |
| a `thinking` block | nothing. Private in the file and private on the wire. |
| `isSidechain` | nothing. A subagent's traffic is machinery and never the persona's own words. |
| a `user` entry nobody typed and with no tool result in it — `/clear`, the local-command caveat, a subagent's `task-notification` | nothing. The CLI talking to itself is not the conversation. |
| `attachment`, `ai-title`, `last-prompt`, `queue-operation`, `system`, `mode`, `atis-latch`, `custom-title`, `agent-name`, `cost-state` | nothing. The CLI's bookkeeping. |

A block type the translation has no name for is **passed through inside its
assistant event rather than dropped** — `fallback` (the CLI's note that it
swapped the model mid-turn; 3 of them across the 27 adopted files on this
box) is the one such today. A client ignores what it does not know; a
translation that dropped it would lose something the file states and nobody
would find out.

The `result` at each turn boundary carries what the file knows and `null`
where it knows nothing: `model` (the last model a top-level assistant
message in that turn named — never `<synthetic>`, which is the CLI's mark on
a message it wrote itself and not a model), `started_at` and `ts` from the
file's own timestamps, `duration_ms` between them, `num_turns` (the
assistant messages in the turn), and **`total_cost_usd: null` and `usage:
null`**. The session file records no per-turn spend; the only total it ever
holds is a `cost-state` entry for the whole session, in 1 of the 27 files
here. `is_error` and `subtype` say the turn failed when the file ends it
with a message the CLI wrote in the model's place — a monthly spend limit
reached mid-conversation is the real case on this box.

Every replayed event carries `ts` (the file's timestamp) and `adopted:
true`. Neither is on a live event: a client needs no special case for them,
and anyone reading a stream should be able to tell which half of it came out
of a file this broker did not write.

**`adopted: true` on an event, `replayed: 22` on the record**, and the two
words are different on purpose. One stream carries both objects; a field
that meant "how many" on the snapshot and "yes" on the next line would be
the `seq` / `record_seq` confusion of 2026-09-16 wearing a new hat, and that
one cost a client the last event of every session it opened.

**The numbering, which is why `replayed` is on the record.** The translated
events are numbered 1..`replayed`, and the broker's own log for that session
begins at `replayed + 1` — `record_seq` is set to the same count at the
scan, and `Session.seq` starts from `record_seq`. So when the fork lands, a
turn taken in an adopted session appends to its history instead of
renumbering on top of it, and `?from=<seq>` lands in the right place in
whichever half the client had reached.

The replay is cut to `replayed` deliberately. The owner may have gone on
talking in the app since the record was last read, and a 22nd translated
event would wear a number the broker's own log has already given to
something else. What they have said there since is picked up by the refresh that
precedes the replay and by the follower that runs while the thread is open —
*Staying in step with the app's own file* — so `replayed` is current as of a
moment ago and the cut is at the end of the conversation rather than hours
short of it. Both of those, like the scan, only touch a session nothing here
has written into, which is why that question is settled on **having a log of
our own** rather than on `record_seq`, which an adopted record carries from
birth.

Cost of all this: the file is parsed at every replay, and the biggest
adopted file on this box is 8.5 MB. Nothing is cached, because a cache of a
file another process is still appending to is a second copy that can be
wrong.

#### Staying in step with the app's own file

*Part 2 below, following an open chat's file, retired 2026-11-12 with the per-session stream; the refresh on a read and the scan stay.*

Added 2026-09-21, because the owner asked: *"Some of the latest messages in this
session are not showing up in the Loam UI. How is it syncing and how can we
keep them synced?"* They were still talking to Ada in the Claude Code app, and
the record was the reading taken at the last broker start — hours old. An
adopted record is a **reading of a file this broker does not write**, and a
reading has a date on it. So it is taken again, in three places, and all three
are the same read of the same file.

**1. On a read of the record.** `GET /v1/sessions` and `GET /v1/sessions/<id>`
stat the file behind every adopted record and compare it with the `source_size`
and `source_mtime` the record stores:

| what the stat says | what happens |
|---|---|
| both unchanged | nothing. This is the answer for 26 of the 27 adopted records on any given read. |
| the mtime moved, the size did not | the new mtime is written down and **the file is not read**. Something on this box stamps old session files — dozens share one 01:30 minute, and *What is adopted* has the measurements — and a touch is not a turn. |
| the size grew | the file is read again, and `title`, `last_message`, `last_activity`, `last_event_at` and `replayed`/`record_seq` are made again out of it. |

The stat is the cost and the parse is the exception, and the ratio is why it is
built this way. Measured on this box 2026-09-21: **27 adopted records are
0.36 ms of stats, against 0.69 s to parse all 27 of their files** (127 MB).
A read of the list that refreshes everything unconditionally would be a
thousand times the price of one that asks first.

A record the person renamed keeps its name. The stand-in title — the opening
words — is re-derived, and a title that is not the stand-in is somebody's and
is left alone. And `replayed` and `record_seq` move together, exactly as the
scan sets them: a record promising a different number than the stream delivers
is a record whose next turn overwrites the end of its own history.

`Session.save()` does the writing, so **the live list stream sends the changed
snapshot** and a home screen open in a browser redraws the row without asking.

**2. While the thread is open.** `GET /v1/sessions/<id>/events` on an adopted
record no longer ends where the history does. For as long as at least one
client holds that stream, the broker polls the file every
`FOLLOW_POLL_S` (1.5 s) and what has been appended arrives translated by
`loam_adopt.iter_events` — the same shapes as the replay, `adopted: true`,
numbered on from `replayed` with no gap and no repeat — followed by the record
as a fresh `broker.session`. So the turn the owner takes on their phone lands in a
thread somebody has open in Loam UI within about two seconds.

- **Polled, not watched.** There is no inotify in the standard library, a
  dependency would have to be installed on this box and explained, and a stat
  at that interval is nothing next to the parse it decides against. The file
  is only re-read when it has grown, which is once per turn.
- **Nothing is followed for nobody.** The follower starts on the first
  listener and is cancelled with the last. One follower per session and not
  one per client: two pollers on one file would each translate the appended
  turn and only the first would find it, so the second client would sit and
  watch nothing arrive. That is also why a session somebody is streaming is
  held as one object (`Broker.watched`) — two objects for one id are two
  writers of one record file.
- **What is appended is handed out and never logged.** Writing it into the
  session's own log would give the record a log of this broker's own, and that
  is the one thing that settles it: the file would stop being the only copy of
  the conversation and the next turn taken here would number on top of it.
- **A forked record is not followed**, and neither is one with a log of this
  broker's own or one a process of ours is running. The first is the design —
  the branches diverged, see *Sending into one: the fork* — and the other two
  are the numbering: `replayed + 1` is already spoken for.

**3. The scan runs on a read of the list too**, at most once a minute
(`SCAN_INTERVAL_S`), so a conversation started on the owner's phone becomes a
row without waiting for a restart. That scan looks **only for files no record
exists for** — the records that exist are kept current by point 1, per record
and only when the file moved. Measured 2026-09-21: **0.24 s over 576 session
files**, against 0.93 s for the full rescan that `POST /v1/sessions/adopt` and
every broker start still run. It is paid on the event loop and at most once a
minute, which is a pause no reader of a stream can see; a thread would buy that
back at the price of writing records from one.

`tests/test_broker_follow.py` is all three, over a fixture file that grows
between calls, and it **counts the parses** rather than asserting about the
cost in prose.

#### Sending into one: the fork

*No route reaches this since 2026-11-12 (`POST /v1/sessions/<id>/messages` and `resume` retired); the code stays for the forked records on disk. See "The classic chat routes, retired".*

Added 2026-09-20, the same day as the scan and the replay. An adopted
session is **never resumed in place**, and that is the whole shape of this.
Its `session_id` *is* the app's session id, so `claude --resume` on it would
point a second process at a session file the Remote Control server could
still have been writing — a corrupted conversation of the owner's, and not the kind of
failure that announces itself.

So the first launch of an adopted record is:

```
claude -p --resume <the app's session id> --fork-session   (+ the usual flags)
```

`--fork-session` reads that conversation and continues it in a **new**
session file of the CLI's own. The app's file is read, before the fork and
after it, and never written to.

Two fields on the record say what happened, `null` on every session the
broker started itself:

| field | is |
|---|---|
| `cli_session_id` | the id the CLI minted for the fork, and the session every later launch resumes (`--resume <cli_session_id>`, no `--fork-session`) |
| `forked_from` | the app session it was forked out of — the same id this record wears, said where a reader of the record will look for it |

**The record keeps the app's id as its identity.** The row, the event log,
the URL, the replayed history and every event on the stream stay under it. A
record that re-identified itself as the fork would be a second row on the
home screen with the history under the first one. The CLI states its own id
on each envelope it writes, and an event naming the fork is rewritten to
name the record on the way out — one conversation wears one id on the wire.
What is *not* rewritten is the turn record's `session_id`: that is the id of
the process that ran the turn, and for a forked session it is the fork's.

The id is read back, not asked for. `--session-id` alongside `--resume
--fork-session` is accepted by the CLI's parser and what it does with it is
undocumented; the fork's id arrives in the `system`/`init` message either
way, and that is where the broker takes it from and saves it.

**After the fork, the app's file is history and nothing more.** The session
now has a log of this broker's own, so the scan leaves it settled (see *What
is adopted*), its first `replayed` events keep coming out of the app's file,
and anything the owner goes on to say in the app on that same session is a
branch this record does not follow.

`POST /v1/sessions/<id>/messages` still answers `404` for an adopted record
nothing is running, as it does for any ended session — that route resolves
an id in memory, and `POST /v1/sessions` with `resume: true` is what brings
one back. The one `409` left is an adopted record whose session file is gone
from `~/.claude/projects/`: there is nothing to fork from, and a CLI that
started and died would read on the page as a turn that failed.

### Events

**The vocabulary is the CLI's.** Every message the CLI emits is passed
through verbatim with `seq` added, a per-session counter — so a client reads
the same `system`, `assistant`, `user`, `stream_event` and `result` objects
the headless docs describe. Translating them into a second vocabulary would
mean maintaining that translation forever.

One other field is added, to one type only: a `result` carries `context`,
the turn's two token figures. It is on the event that closes the turn as well
as on the turn record so that a page can draw "used of window" as the answer
lands, without re-fetching anything. See *What a turn cost in context* below.

Passed through verbatim means *verbatim*, including types not in any list
here. A real session on 2026-09-16 carried `rate_limit_event` (a
`rate_limit_info` object with `unifiedWindows`, five-hour and seven-day
utilisation), `system`/`status` (`requesting`, `compacting`) and
`system`/`thinking_tokens`. **A client must ignore what it does not know
rather than treat it as an error**; this list will grow every time the CLI
does, and the broker is not a filter.

The same vocabulary carries a conversation the broker never ran: an adopted
session's history is translated into these very types, one event per content
block, each marked `adopted: true`. See *What an adopted session replays*
above — there is nothing new for a client to read.

Eight types are the broker's own, all prefixed. **All eight are FLAT**: every
field sits at the top level of the object beside `type`, and nothing is
nested under a `payload`, `data` or `body` key. A client reads
`event.request_id`, never `event.payload.request_id`. Six of the eight are
events and carry `seq`; `broker.session` and `broker.residency` are not
events, belong to the live list, and carry none.

- `broker.session` — the session record, sent first on every stream and again
  whenever a turn record is written. Its fields are the record's fields,
  spread flat beside `type`. **It carries no `seq`**; its position field is
  `record_seq`. It is a snapshot and not an event — see the two sections
  below.
- `broker.user_message` — what a person sent. `session_id`, `text`, `seq`,
  and nothing else. Emitted and logged when `POST /v1/sessions/<id>/messages`
  accepts the message, **before** anything the CLI produces in response, so a
  replay reads in the order the conversation happened. Added 2026-09-16;
  "Whether a user message comes back on the stream" below is why it has to
  exist.
- `broker.permission_request` — `session_id`, `request_id`, `tool_name`,
  `display_name`, `input`, `description`, `decision_reason`,
  `decision_reason_type`, `permission_suggestions`, `tool_use_id`, and
  `kind`: `"approval"` (Allow/Deny on a tool call) or `"question"`, which
  also carries `message` (the provider's sentence, or `null`) and
  `questions` ("Questions" below). Answer it at the permissions route; the
  turn is paused until you do.
- `broker.permission_resolved` — `session_id`, `request_id`, `behavior`,
  `tool_name`, `kind`, and `message` (the deny reason, or `null` on an
  allow), and `scope: "session"` on an allow that carried
  `updatedPermissions`. `behavior` is one of five, and **every pending
  request ends in exactly one of them** — see *Closing a request* below. An
  allowed question also carries `answers`, as
  given, except a `secret` question's, which is `null` because this event is
  written to the log. Emitted when an answer has been accepted and put on
  the CLI's control channel. **It exists for the client that did not
  answer**: two devices watching one session both raise a card, and without
  this the second holds a card for a request that no longer exists until the
  next `broker.session` happens to reconcile it. Added 2026-09-16;
  `tests/test_broker_permissions.py` is its unit and the live smoke test of
  the same day is its proof.

  **Closing a request** (2026-10-05). Until that day a request could leave
  `pending` without a word on the stream — the idle reaper ended a session an
  hour after its last card, `_reap` cleared `pending`, and the log was left
  holding requests that never closed, which a page replays as live cards
  answering 404 forever. Now every way a request closes writes this event,
  before `broker.exit` where there is one:

  | `behavior` | when | sent to the provider | `message` |
  |---|---|---|---|
  | `allow` | a person allowed it on the route | allow, with `updatedInput`, and `updatedPermissions` if the body had them | `null` |
  | `deny` | a person denied it on the route | deny, with the message | the deny reason |
  | `expired` | nobody answered within `PERMISSION_WAIT_S` (3600, `lib/loam_broker.py`) | **a deny**, so the turn finishes on its own and the process lives | `Nobody answered this request within an hour. Ask Sam in words, or go on without it.` |
  | `cancelled` | the provider withdrew it: Claude's `control_cancel_request`, Codex's `serverRequest/resolved` | nothing | `null` |
  | `ended` | the process ended with it open, for any reason: the idle reaper, `DELETE`, a crash, the broker's own shutdown (which stops every live session, and `stop()` writes these itself rather than leaving them to `_reap`) | nothing | `null` |

  All of them carry the request's `request_id` and `kind`. A client drops the
  card on any of them, and on `broker.exit`, and on a `broker.session` whose
  `pending_permissions` lacks it. **An answer to a request that is no longer
  pending is `404` with `no permission request <id> is pending`**, whatever
  closed it; a page may rely on that sentence to drop a card it still holds.

  **A session that is asking is not idle.** The reaper never ends a session
  with anything in `pending`, however long ago its `last_activity` was; it
  expires each request once that request has waited `PERMISSION_WAIT_S` from
  when it was raised, and the ordinary idle clock runs again from that
  answer. Questions are requests like any other and expire the same way.

  **`updatedPermissions`** on an allow is the CLI's own `PermissionUpdate`
  list, as it proposed it on the request's `permission_suggestions`, put on
  the `control_response` beside `updatedInput` **unaltered**; the broker never
  makes one up. Only suggestions whose `destination` is `"session"` are
  accepted — a `localSettings` suggestion (the CLI offers them, e.g. for
  `Skill`) would have the CLI write the persona's own settings file from a tap,
  and persona configs are operator-authored — so a body carrying any other
  destination, carrying them on a deny, or not a list of objects, is `400` and
  the card stays answerable. Send the `session` ones, or none. The resolved
  event then carries `scope: "session"`: the rule holds for the rest of that
  process **and not past it**. Measured 2026-10-05 on
  `claude-haiku-4-5-20251001` through this route: after an allow that echoed
  the suggestion `Read(//<dir>/**)`, a second `Read` under the same directory
  in the same process raised no card (a plain allow, the control, raised
  one); after the process was ended and resumed by id, a third `Read` there
  raised a card again. So "Allowed for this chat" is true until the chat's
  process ends — the idle reaper, a restart, `DELETE` — and not after. Codex offers no suggestions; the field is not passed to it and no
  scope is claimed. `tests/test_broker_permission_close.py` is the unit for
  all of this section.
- `broker.background_tasks` — how many tasks are running behind this session,
  emitted whenever the count changes and not when the CLI merely re-states an
  unchanged list. `session_id`, `count` (the integer the record carries), and
  `tasks`, one `{task_id, task_type, description}` per live task, because the
  CLI names them — a description somebody wrote in the brief that launched
  them — and a client that wants to say *which* subagent is running should not
  have to parse the CLI's own event for it. A client that only wants a badge
  reads `count`. Numbered and logged like any event, so a reconnecting client
  is replayed it. Added 2026-09-22.

  ```jsonc
  { "type": "broker.background_tasks", "session_id": "940380fd-…",
    "count": 2,
    "tasks": [ { "task_id": "a2225e8aa387690af", "task_type": "local_agent",
                 "description": "Four briefs: broker tail/seen, page x2, deploy" },
               { "task_id": "a7914659c9d784ff0", "task_type": "local_agent",
                 "description": "Page audit: two capped jobs' points" } ],
    "seq": 8689 }
  ```
- `broker.residency` — how many sessions are resident in RAM and how many may
  be: `live_count` and `cap`, and nothing else. It is on the live list only
  (`GET /v1/sessions/events`), closes that stream's opening burst, and goes
  out again whenever the count moves. Like `broker.session` it is not an
  event: no `seq`, never logged, and a client applies the latest one it has
  seen. Added 2026-09-25; see *Which sessions are in RAM* above.

  ```jsonc
  { "type": "broker.residency", "live_count": 2, "cap": 4 }
  ```
- `broker.compaction` — the session's history was compacted: the divider a
  page draws. `session_id`, `at`, `trigger` (`"manual"` for `/compact`,
  `"auto"` when the window was reached), `tokens_before`, `tokens_after`,
  `duration_ms`, `seq`. Emitted right after the CLI's own
  `system`/`compact_boundary`, which stays in the log verbatim beside it; any
  number that marker does not carry is `null`. Added 2026-09-30; see
  *Compaction* below.

  ```jsonc
  { "type": "broker.compaction", "session_id": "…", "at": "2026-09-30T18:15:30Z",
    "trigger": "manual", "tokens_before": 22230, "tokens_after": 1810,
    "duration_ms": 9306, "seq": 57 }
  ```
- `broker.exit` — the process ended, with its exit code.
- `broker.write_failed` — the broker could not write this session's event
  log (2026-10-22: `/` filled and one failed write killed a session's reader).
  `session_id`, `reason` — a sentence a page can show as it is, `couldn't save
  this reply: the server's disk is full` for ENOSPC — and `from_seq`, the
  first event that missed the log. Like `broker.session` it is **not an
  event**: no `seq`, never logged, live stream only, sent once per turn at the
  first failure. Every event still reaches the pages that are watching; what
  is lost is the replay. The turn it fell in ends `failed` whatever its
  `result` said, and its turn record carries the same sentence as
  `not_saved`. The log never keeps a torn line's damage past one event: the
  next write after a failure, and the first after a broker start, begins a
  fresh line if the file does not end on one. `tests/test_broker_log_full.py`
  is its unit.

  ```jsonc
  { "type": "broker.write_failed", "session_id": "…",
    "reason": "couldn't save this reply: the server's disk is full",
    "from_seq": 127914 }
  ```

#### The snapshot is not an event, and carries no `seq`

`broker.session` is the one item on this stream that is **not** a logged
event. It is not numbered, it is never written to the `.jsonl`, and it has no
`seq` field at all. Its position field is `record_seq`, and that number is
the record's — the last event's number — not this item's identity.

**Every `seq` on this stream is an event's identity and nothing else.** That
sentence is now true without exception, and it was not before 2026-09-16.
The snapshot used to carry `seq`, which was by definition the number of the
last event already in the log, so a client that applies each `seq` once — as
this README tells it to — discarded the real event wearing that number when
the replay reached it. **Every session a page opened lost its last event**,
whatever it happened to be: a `result` for a session that had just finished a
turn, a `broker.exit` for an ended one, the event it connected on for a page
joining mid-turn. And a client that keeps the highest `seq` it has seen had
the end of the log at its *first* line, so a stream that dropped mid-replay
reconnected with `?from=<end>`, got nothing, and left the transcript in
between unreachable.

Caused by counterfactual on a real 144-event log, 2026-09-16, and the same
counterfactual is `tests/test_broker_snapshot.py`: seven events, the same
client rule, against this broker and the one before it —

```
--- before: the leading broker.session carries seq=7
    dropped as a seq already seen: [('result', 7)]      # the last turn
    five items in, the client would reconnect at ?from=7 (log ends at 7)

--- after:  the leading broker.session carries no seq, record_seq=7
    dropped as a seq already seen: (none)
    five items in, the client would reconnect at ?from=4 (log ends at 7)
```

So a client does the obvious thing: apply a snapshot whenever it arrives,
keep the latest, and never let it touch the `seq` it reconnects at.

#### The snapshot is re-sent when a turn is recorded

`broker.session` goes out again — to every subscriber, not to the log —
**each time a turn record is written**, immediately after the `result` event
for that turn. The turn's two most useful numbers do not exist on any event:
`cost_delta_usd` is the broker's own arithmetic and `model` is read back from
the turn's assistant messages, and both live on the record. Before this a
page that wanted to price the turn that just ended had to re-fetch
`GET /v1/sessions/<id>` and match a record entry to an event by hand, which
is the one place a client was made to infer a correspondence instead of being
told one; asked for by the app's engineer, 2026-09-16.

It is a re-send and not a new event on purpose. Logging it would replay a
stale record in the middle of a transcript, and numbering it would put the
collision above straight back. A client that was not connected loses nothing:
the snapshot it gets when it connects is by definition newer.

#### The retry event, which is the CLI's and not the broker's

There is no `broker.retry` and there should not be one. A retry is the CLI's
own `system` message with `subtype: "api_retry"`, passed through like every
other. Its literal shape, read from the CLI binary's own schema on 2.1.270
(the pinned version) rather than inferred:

```jsonc
{ "type": "system", "subtype": "api_retry",
  "attempt": 1, "max_retries": 10,
  "retry_delay_ms": 8000,
  "error_status": 429,          // the HTTP status, or null
  "error": "rate_limit",        // a CATEGORY from the fixed list below
  "no_response": { "waited_ms": 0, "retry_wait_ms": 0 },  // optional; present
                                // only when the API sent no response headers
                                // inside the first-byte window
  "seq": 17 }
```

`error` is one of exactly thirteen strings, and a client may switch on them:
`authentication_failed`, `oauth_org_not_allowed`, `account_on_hold`,
`verification_required`, `billing_error`, `rate_limit`, `overloaded`,
`invalid_request`, `model_not_found`, `server_error`, `unknown`,
`max_output_tokens`, `cloud_credential_error`. It is a category, not a
sentence: a client that wants a sentence writes its own from `error`,
`error_status` and `retry_delay_ms`. This means a client does **not** need
to pattern-match a free-text message to recognise a retry — match
`type === "system" && subtype === "api_retry"` and nothing else.

Not caused here: forcing a real rate limit to see one on the wire would mean
deliberately exhausting the owner's quota. The shape above is the CLI's own
declared schema, which is a stronger source than one observed example.

#### Whether a user message comes back on the stream: not the CLI's, the broker's

The CLI does **not** echo a message a person sent. It has a flag for that,
`--replay-user-messages` (which requires `--input-format stream-json` and
`--output-format stream-json`, both of which this broker passes), and
**this broker deliberately does not pass it** — see `Session.argv`.

So: a `user` event on this stream is a **tool result**, not a person
speaking. Three real sessions on 2026-09-16 were read back end to end and
every `user` event in all three carried `content` as a list of
`tool_result` blocks; not one carried the text a person had typed. A client
must therefore render the typed message itself, and must not wait for the
CLI to send one back.

That left the durable log holding one side of the conversation. A client
replaying a session it did not watch live — which is what reopening a
session in an app is — got answers with no questions; caused in a real
browser on 2026-09-16. So **the broker logs the person's message itself**,
as `broker.user_message`, which is the broker's own event and not an echo of
the CLI's:

```jsonc
{ "type": "broker.user_message",
  "session_id": "<id>",
  "text": "what did you do yesterday?",
  "seq": 12 }
```

Those four keys and no others. It is emitted before the text is written to
the CLI's stdin, so it is numbered below every event of the turn it started
and a replay from `?from=0` reads in order, both sides. A message the CLI
never received is never logged: the 409 a stopped session raises is raised
before the event is written.

**It goes to every subscriber, the sender included**, because it is the
session's record and not a message addressed to anyone. So the sender does
see an event for the line it just drew, and `POST
/v1/sessions/<id>/messages` answers `{"accepted": true, "seq": <n>}` with
**that event's own seq** so the sender can recognise it: `send_message`
returns the number rather than the route reading `session.seq` back, which
would be the wrong number the moment the CLI's pump numbered something in
between. A client still renders its own message when it sends
it rather than waiting; `seq` is how it avoids drawing it twice, which is
the same idempotence the rest of this stream already asks for.

#### `total_cost_usd` is a running total; `cost_delta_usd` is the turn's cost

The trap in the `turns` list, found by causing on 2026-09-16 and the one
number on this interface that is easy to display wrongly:

| field | scope |
|---|---|
| `usage` | **this turn.** `input_tokens`, `output_tokens`, the cache counters. |
| `total_cost_usd` | **the whole process, so far.** Cumulative across turns, as the CLI reported it. |
| `cost_delta_usd` | **this turn.** The broker's arithmetic; see below. |
| `process_start` | whether this turn was its process's first (since 2026-10-13). |
| `modelUsage` | the whole process, so far, per model. Cumulative. |
| `duration_ms`, `num_turns` | this turn. |
| `not_saved` | this turn, only when something in it could not be written to the log: the reason, the sentence `broker.write_failed` carried (since 2026-10-22). |

Measured: turn one reported `$0.34253275`, turn two `$0.35690825` — turn two
did not cost $0.357, it cost the $0.0144 difference, and `modelUsage`'s
output tokens went `136` then `212`, which is `136 + 76`. **And the counters
reset when a session is resumed**, because a resume is a new process: turn
three, the first after a resume, reported `$0.01428225`. A client showing
`total_cost_usd` per turn shows a number that climbs wrongly and then falls
off a cliff at a resume.

**So the broker computes the turn's cost and records it as
`cost_delta_usd`** — this turn's `total_cost_usd` minus the previous turn's
*within the same process*, and the total itself for the first turn after a
process starts. It is not an invented number, it is a subtraction; and the
broker is the only party that can do it, because the turn records carry
across a resume on purpose (they are the session's, not the process's) and
nothing in the list says where the second process began. A client that tried
would produce a negative number at every resume. `total_cost_usd` stays
beside it exactly as reported.

**Corrected 2026-10-13: a resume does not always reset the total.** The CLI
now usually restores a resumed session's saved total into the new process:
of 99 first turns after a start in the broker's records, 86 continued from
the session's earlier total (f3069d8e: $81.57, then $82.79 on the first turn
after a resume) and 13 started over, from zero or from an older saved total.
Recording the first turn's total as its cost booked the whole session again
at every restore, and a week's deltas summed to about 7× the spend. Since
then the first turn of a process subtracts the largest base it could have
resumed from that is not above its total — zero, or the total at which one
of the session's earlier processes ended — and each turn record says
`process_start: true|false`, which is how those ends are found after a broker
restart (a turn from before the key is a start when its delta equals its
total, which the old rule wrote for exactly those). Exact for a restore and a
reset alike; it undercounts only a reset whose one turn cost more than an
earlier process's whole running total. Turns already recorded keep the
deltas they were written with. `tests/test_broker_turn_log.py` holds the
restored and the stale-restore cases.

Two details worth knowing rather than rediscovering: the result is rounded
to ten decimal places, because `0.35690825 - 0.34253275` is
`0.014375500000000018` in doubles and `$0.0143755` in fact — ten places is
two more than the CLI reports, so it rounds representation and never a real
digit. The CLI's own totals carry that noise: a real turn on 2026-09-16 came
back `$0.021594750000000003`, and `0.021594750000000003 - 0.0108765` is
`$0.01071825` in the record. And an envelope that reports no
`total_cost_usd` gets `cost_delta_usd: null` rather than a guess.
`tests/test_broker_turn_log.py` holds both, with the measured numbers above
as its fixture.

**Turns recorded before 2026-09-16 have no `cost_delta_usd` key at all** —
the field cannot be backfilled, because the process boundaries it needs were
never written down. A client reads a missing key and a `null` the same way:
unknown, show nothing. Every turn recorded from now on has it.

#### `model` on a turn record, which is always a key

**Every turn record has a `model` key.** Its value is the model named by the
turn's **top-level `assistant` messages** — the last one, if several named
different models — and `null` if none of them named one. It is never absent,
so a client needs no fallback of its own: `null` is the broker saying nothing
in that turn stated a model, which is an answer and not a gap.

*Top-level* is load-bearing. An `assistant` message with a
`parent_tool_use_id` came from a subagent, which the persona's settings may
run on a different model entirely, and the turn's model is the turn's own.

What is deliberately **not** consulted: `modelUsage`, which lists models the
*session* never ran on — the CLI's own small-model calls appear there, and a
real turn as ada (on `claude-fable-5-1`) billed
`claude-haiku-4-5-20251001` too. Picking out of it is a guess; picking
alphabetically, which this code did until 2026-09-16, answered "fable" only
because f sorts before h and would have called an Opus session Haiku. The
persona's `settings.json` is not consulted either: "the settings say fable"
and "this turn ran on fable" are different claims, and only the second is
evidence. `tests/test_broker_snapshot.py` holds the rule, including a
subagent's message being ignored and a turn not inheriting the previous
turn's model.

A turn whose `model` is `<synthetic>` is the CLI answering one of its own
commands rather than the model answering a person — what a `/model` or
`/effort` sent by `PATCH /v1/sessions/<id>` produces, at a cost of `0`. See
"A chat's own model and effort".

#### What a turn cost in context: `context.used` and `context.window`

The owner, 2026-09-22: *"how many tokens of context was used, how many tokens of
context is supported with that session's set up."* Every turn record carries
it, and so does the `result` event that closes the turn:

```jsonc
{ "type": "result", "subtype": "success",
  "context": { "used": 35271, "window": 1000000 },   // <- added by the broker
  "usage": { ... }, "modelUsage": { ... }, "seq": 321 }
```

| field | is |
|---|---|
| `context.used` | how many tokens the model's context held at the **last request of that turn**: `input_tokens + cache_read_input_tokens + cache_creation_input_tokens`, from the CLI's own numbers. `null` when the turn made no request that stated them. |
| `context.window` | the context window of the model that turn ran on, as the CLI states it. `null` when the turn named no model or the envelope carries no row for it. |

The page renders "used of window". **The broker does the arithmetic and the
page does none** — and the arithmetic is two sums over numbers the CLI
volunteered, never an estimate.

**Where `used` comes from, and the one place it must not come from.** It is
the `usage` of the last **top-level** `assistant` message of the turn — each
of those is a request the model answered, and the last one is the turn's high
water mark. The result envelope's own `usage` is *not* this and cannot be used
for it: it is the turn's requests **added up**. A three-request turn on this
box (Juno, session `49db688c`, 2026-09-22 19:27Z) reported

```
result.usage       input 66 + cache_read 97,999 + cache_creation 3,840 = 101,905
last assistant     input 32 + cache_read 34,691 + cache_creation    548 =  35,271
```

and the second number is the one that means anything: the first counts the
same cached prefix once per request, so a twenty-round turn would report three
million tokens in a one-million-token window. *Top-level* is load-bearing for
the same reason it is on `model` — a subagent's request is not the turn's
context.

Output tokens are deliberately not in the sum. The context a request occupied
is what went *in*; the answer that came back is counted in the next request's
`input`/`cache_read`, which is where a page adding output would double it.

**Where `window` comes from.** The result envelope's `modelUsage`, read **by
name**. That dict lists every model the process billed, including the CLI's
own small-model calls, and the same real envelope carried

```jsonc
"modelUsage": {
  "claude-haiku-4-5-20251001": { "contextWindow":  200000, ... },
  "claude-fable-5-1":          { "contextWindow": 1000000, ... } }
```

— so picking out of it without a name would have called a 1M session a 200k
one. The 1M window is a fact about that model and about the `[1m]` aliases
(`fable[1m]`, `opus[1m]`, `sonnet[1m]`), and the broker never asserts either:
it reports the number the CLI printed for the model the turn actually ran on.
The persona's `settings.json` is not consulted, for the reason `model` gives.

**A turn replayed out of the Claude Code app's own file carries the same key**,
with the half that file can answer: `used` is the same sum over the same last
assistant message, and `window` is `null`, because nothing in a session file
says what window the model had. One stream, two halves, **one shape** — a page
must not branch on where a turn came from. See *What an adopted session
replays*.

**Turns recorded before 2026-09-22 have both as `null`**, filled in on the way
out like every other key: the figures cannot be backfilled, because the
per-request usage they come from is in the event log and not in the record.
A client reads `null` as "unknown, show nothing".

### Compaction

*`POST /v1/sessions/<id>/compact` retired 2026-11-12. The CLI's own compaction and the `broker.compaction` divider in the log are unchanged.*

**The broker sets no compaction window.** A session compacts automatically
only where the CLI does by default, near the model's window being full (about
967k of a 1M window on Fable); anything earlier is a person pressing compact,
through the route below. A page makes the size of the context visible and
leaves the decision to the person. There is no `compact_window` on the record:
the CLI's threshold is its own arithmetic and not something the process states,
so a page warns against the model's window (`context.window` of the last turn).

*From its restart at 12:49 (-0700) on 2026-09-30 until the next one, the broker
launched its sessions with `CLAUDE_CODE_AUTO_COMPACT_WINDOW=400000` and stated
it as `compact_window`. The owner undid it the same day: "Do not automatically
compact at 400k. Just the UI making me aware is good enough. Then I can
manually ask for compaction. The only automatic compaction should be what the
provider does near the context getting full."*

The system prompt — CLAUDE.md and its imports, the output style — is reloaded
after a compaction, so a compacted persona is still itself (proved on this box
by the memory-routes investigation of 2026-09-03; the wake lanes rely on it).

**What a compaction looks like on the stream** (CLI 2.1.281, a haiku probe on
this box, 2026-09-30, four short turns and then `/compact`), in order:
`system`/`status` with `"status": "compacting"`; `system`/`status` with
`"status": null, "compact_result": "success"`; an `init`; the marker —

```jsonc
{ "type": "system", "subtype": "compact_boundary", "session_id": "…", "uuid": "…",
  "compact_metadata": { "trigger": "manual", "pre_tokens": 22230, "post_tokens": 1810,
                        "cumulative_dropped_tokens": 20420, "duration_ms": 9306,
                        "preserved_segment": { … }, "preserved_messages": { … } },
  "logical_parent_uuid": "…" }
```

— two `user` events (the summary the CLI wrote, and a replayed
`<local-command-stdout>Compacted </local-command-stdout>`), and a `result`.
The broker passes all of it through and adds `broker.compaction` after the
marker. **A page draws the divider from `broker.compaction`** and can ignore
the two `user` events, which, like every `user` event on this stream, are not
something a person said (*Whether a user message comes back on the stream*).

**`POST /v1/sessions/<id>/compact`** sends `/compact` on the CLI's stdin the
way a PATCH sends `/model`, and not as `broker.user_message`: a command is not
something a person said. The session reads `working` from the press until
that `result`. **A chat whose process has ended is resumed and then compacted**
(2026-10-01): until then it was a `409`, and that is what the owner's press on
fac1b2dc hit, an hour and a half after the idle reaper had ended it. A Codex
session is refused before anything is launched. A fresh session with too little in it answers "Not enough messages to
compact" instead, and no marker comes (the CLI's documented answer, not
re-caused here).

**A `/compact` makes a turn record**, because it ends in a `result`: its
`total_cost_usd` moves (the summary is a model call, so `cost_delta_usd` is
what compacting cost), and its `context` is `{used: tokens_after, window: …}`:
no assistant message states a usage, so `used` is the marker's `post_tokens`
and `window` is read out of the envelope's `modelUsage` for the model the
`init` after the marker named (2026-10-01; before then it was
`{used: null, window: null}`, and a record read by its last turn went on
looking as full as it was before the compaction). The turn's `model` stays
null, because no message named one.
An automatic compaction should land inside the turn that reached the window,
with `trigger: "auto"`, and make no record of its own; that one was not caused
here, because causing it costs close to a full window.

The transcript writer is untouched by any of it: the summary is
`isCompactSummary`, the caveat `isMeta`, and the `/compact` and `Compacted`
entries carry no `promptSource`, so none is filed as a turn.
`fixtures/compacted-session.jsonl` is the probe's own session file and
`tests/test_broker_compact.py` proves all of the above without a model call.

### Opening a chat at its end

*Retired 2026-11-12 with the classic chat view; see "The classic chat routes, retired". Kept as the record of how it worked.*

Until 2026-09-22 every open of a thread was the whole conversation from
event one. The owner, that day: "When entering a chat, it loads the entire
chat from the beginning. That is not a good UX" — and the chats worth
reopening are exactly the long ones, so the cost fell where it hurt most.

**A turn** runs from a `broker.user_message` — or from the `system`/`init`
the CLI writes ahead of it on a launch or a resume — through the `result`
that ends it. The init counts as the turn's start, not as a thing of its
own: a window cut between the two would open on an `init` with no question
above it. A starter seen while a turn is still open does not begin a second
one, and whatever precedes the first starter belongs to the first turn, so
walking back to the beginning ends with everything and no orphan above it.

```jsonc
GET /v1/sessions/<id>/events?last=10
// SSE, and it stays open exactly as ?from= does.
data: { "type": "broker.session", …the record…,
        "window_first_seq": 62,     // the seq of the first event below
        "turns_before": 20 }        // how many turns are above this window
data: { "type": "broker.user_message", "seq": 62, … }
…the last ten turns…
// then live, as ever.

GET /v1/sessions/<id>/events?before=62&last=10
// SSE in shape, so a client parses one format; it closes at the end.
data: { "type": "broker.session", …, "window_first_seq": 32,
        "turns_before": 10 }
…the ten turns below seq 62…
// closed.
```

The two fields are on the **snapshot** and not on the record: they are facts
about this answer, not about the session, and a record on the live list
carrying them would be one client's scroll position broadcast to every other
client. `turns_before` is `0` when the window reaches the beginning, which
is how a page knows to stop asking. `window_first_seq` is what the next
`before=` is sent with, so the page below ends exactly where the page above
begins — no gap, because the cut is on the turn boundary under that seq, and
no overlap, because the comparison is strict.

**With neither parameter the route is exactly what it was**, `?from=`
included: `?from=` is still how a reconnect resumes, and its snapshot still
carries no window fields at all. `before=` without a `last=` is a `400`; so
is a `last=` that is not a number, or is less than 1. A `last=` bigger than
the conversation is the conversation, with `turns_before: 0`.

An adopted session windows like any other. `replay` is where the app's own
file and this broker's log became one stream (see "Sessions from the Claude
Code app"), and the window cuts that stream rather than either half of it,
so a window can straddle the seam and a thread opened at `last=2` can show
one turn from the app and one taken here. `tests/test_broker_window.py`
covers a 30-turn fixture: `last=10` is the right ten turns and
`turns_before: 20`, and two `before=` pages chain to seq 1 with no gap and
no overlap.

The whole log is read to cut it. That is the deliberate trade: the parse is
of a file the log already is, and the saving is in what crosses the wire and
what the page has to lay out.

**Pictures stay in the log and off the wire** (2026-10-15). A turn is not a
size: a persona's `Read` of a folder of PDFs put 16 MB of base64 page images into
seven `user` events of a two-turn, 17 MB chat, ahead of both replies, and a
phone opening it gave up on the window before reaching them. Every event a
stream sends now goes through `for_the_wire`, which drops the `data` of a
base64 image block in a `user` event (a tool result's or the message's own)
and leaves `"omitted_chars": N` in its `source`, and drops the second copy an
image `Read` carries in `tool_use_result.file.base64`, leaving
`"base64_omitted_chars": N`. Nothing else in the event changes and the log
keeps everything. That chat's stream is 1.6 MB now; across the box's 100
logs the largest `last=10` window went from 74.6 MB to 6.9 MB.
`tests/test_broker_wire_images.py`.

### The seen mark

*Retired 2026-11-12 with the classic chat view; see "The classic chat routes, retired". Kept as the record of how it worked.*

`seen_seq` on every record — how far down that conversation has been read,
against `record_seq`, which is how far it goes. Unread is
`seen_seq < record_seq`, and a client does that subtraction itself rather
than being handed a boolean that would be wrong the moment the next event
landed.

It is on the server, and that is the point. The owner, 2026-09-22: "When I
open the website in a new browser, all of my chats become unseen again. The
status of the chats should be saved on the server so it has a unified
state." One person, one box, one broker — so **this is the unified state**.
There is no per-device reading of it and no merge: a chat read on the phone
is read on the laptop, the same way a title, a pin and an archive already
followed the person across devices.

```jsonc
PATCH /v1/sessions/<id>   { "seen_seq": 62 }                   → the record
PATCH /v1/sessions/<id>   { "seen_seq": 61, "unread": true }   → the record, marked unread
PATCH /v1/sessions/<id>   { "unread": true }                   → the same, at record_seq − 1
POST  /v1/sessions/seen                                        → { "marked": 7 }
```

It only ever goes up. Two devices on one conversation means a stale mark
arriving after a fresh one, and a page that scrolled back would otherwise
mark a read chat unread — so a value below the stored one is **ignored**,
answered `200` with the record unchanged rather than refused. A value above
`record_seq` is a `400`: it is a client bug, and accepting it would hide
unread turns. `0` is "nothing read", which is what every record written
before 2026-09-22 reads as and what a chat nobody has opened actually is.

**Mark unread** is the one way down, and it has to say so (2026-10-04, for
the list's select-and-mark): `"unread": true` sets the mark to the
`seen_seq` given — lower or higher than the stored one — or, with no
`seen_seq`, to `record_seq − 1`, so the last turn reads unread. The bare
lower number is still ignored, because a stale mark from a second device and
a deliberate one would otherwise be the same request. A `seen_seq` at or
past `record_seq` with the flag is a `400` (it would not be unread), and so
is the flag on a chat with nothing in it (`record_seq` 0), naming `unread`:
there is no turn to be unread, and a `200` that changed nothing would read
to a page as done. `"unread": false` is the same as leaving it out.

The broker cannot tell a page's throttled "I have read to here" from a
person's "read" — both are a plain `seen_seq` — so the next plain mark above the
lowered one raises it again, by design, and one at `record_seq` reads it. A page that marks
a chat unread while holding it open in a pane must stop sending seen marks
for it until the person reads it again; nothing on the broker side holds
the unread mark against that.

`POST /v1/sessions/seen` is the "mark all read" button: every **non-archived**
record set to its own `record_seq`, answering how many actually moved.
Archived sessions are left alone — nothing draws them unread, and marking
them would quietly destroy the one signal that says what is in them. Like
`/v1/sessions/adopt`, it is a fixed three-part path checked before the
per-session block, so a session whose id were literally `seen` is still
reached at its own path and by `PATCH`; ids are UUIDs the broker mints.

Both paths go through `Session.save`, which is the one place the live list is
fed from, so **`GET /v1/sessions/events` emits the changed snapshot on
both** and every open page restacks itself without asking.

### Reconnecting

`seq` is the whole mechanism. Events are written to
`state/broker/sessions/<id>.jsonl` **before** they are fanned out, so the
durable copy is never behind the live one. A client sends the last `seq` it
saw; the broker registers the live queue first and replays the log second, so
an event landing between the two is duplicated rather than lost — a duplicate
a client can see by its `seq` is a smaller problem than a hole it cannot.

A resumed session keeps its log, its numbering and its turn records: it is
the same session. Resuming from `0` and getting turn one again was a real
defect, caused and fixed on 2026-09-16.

**`GET /v1/sessions/<id>/events` answers for a session whose process is
gone**, which is what opening a chat from a home screen usually is: the
record and the log are on disk, the replay is read from the log, the stream
ends where the log ends, nothing is started and nothing counts against the
cap. Archived is no different — archiving hides a row, not a log.

It did not, until 2026-09-18, and the failure was worth writing down. The
route resolved the id through `Server._session`, which knows only the
processes *this broker started and still holds in memory*. A session that had
merely ended still answered, because the object was still there; the moment
the broker restarted, every session on the box became a row that could be
listed and not opened — `404 no session <id>`, caused in a real browser. So
the defect was invisible for two days of live use and total after any
restart, which is the shape of bug that a test written against a live object
cannot see. The route now resolves through `Broker.load`, the same door `GET`
and `PATCH` on a record already used, and `tests/test_broker_replay.py`
covers an ended session, an archived one, a reconnect at `?from=`, and a live
session still streaming live. The suspect at the time was
`GET /v1/sessions/events`, the live list added that morning; it was not the
cause and cannot be, because it matches a three-part path exactly and cannot
shadow a path with a tail. Its one real consequence: a session whose id is
literally `events` is reached at `/v1/sessions/events/events` and streams
like any other, but `GET /v1/sessions/events` is the live list rather than
that session's record. Ids are UUIDs the broker mints, so this is a note and
not a hazard; `PATCH` and `DELETE` on such an id are unaffected.

**The `broker.session` snapshot is outside this mechanism entirely.** It has
no `seq`, so it cannot be the highest number a client has seen and cannot be
what a client reconnects at; `record_seq` on it is the log's end, which is
where the session is, not where the client is. A client that reconnected at
`record_seq` would skip everything it had not read yet — which is precisely
the failure the rename removed.

### Questions

*2026-10-01.* A provider asking the person something — "which one?", "what
should it be called?" — reached the page as an ordinary permission card
until this date, so an Allow answered it with nothing. Three formats ask,
and the broker owns every one of them both ways (`lib/loam_questions.py`,
unit `tests/test_broker_questions.py`): the page draws one card.

A question card is a `broker.permission_request` with `kind: "question"`,
`message`, and one to four `questions`:

```json
{"id": "q1", "header": "Name", "question": "What should the plugin be called?",
 "options": [{"label": "Parlance (Recommended)", "description": "…", "preview": null}],
 "multi": false, "other": true, "secret": false, "required": true}
```

`options` empty means free text. `other` means a typed value is allowed
beside the options. `secret` means do not echo it. The answer is
`{"behavior": "allow", "answers": {"q1": ["Parlance (Recommended)"]}}`: a list
of strings per id, an option by its label as given, a typed value as the
string. Checked before anything is sent: an unknown id, two answers to a
single-select, a typed value where `other` is false, a missing required
answer or a number that does not parse is a `400` naming the field.
`{"behavior": "deny"}` declines.

| Format (pinned build) | In | Out | Deny |
|---|---|---|---|
| Claude Code `AskUserQuestion` (CLI 2.1.281), a `can_use_tool` for that tool | `questions[]`; **the id is the question text**, because the CLI keys answers by it; `multiSelect` → `multi`; `other` always true; an extended `kind` `"text"`/`"number"` question is free text | `control_response` allow, `updatedInput` = the model's input unchanged plus `answers`: a string, or for a multi-select the list, which the CLI joins itself with `", "` as its own prompt does | deny with `User declined to answer questions`, the CLI's own line for it, which the model receives as the tool result |
| Codex `item/tool/requestUserInput` (app-server 0.159.3) | `questions[]` field for field (`isOther`, `isSecret`); no options is free text | `{"answers": {"<id>": {"answers": [...]}}}` | `{"answers": {}}`: the schema has no way to decline |
| Codex `mcpServer/elicitation/request`, form mode with fields | `message`; one question per property: `title` or the name as header, `description` as question; `enum`, `oneOf` and `enumNames` are options, an array of enum is a multi-select, a boolean is `Yes`/`No`, string/number/integer are free text; `required` from the schema | `{"action": "accept", "content": {…}}`, each value back in its type: an option to its value (`const`, `true`/`false`), a number parsed, a multi-select a list | `{"action": "decline"}` |

Codex's own approval before an MCP tool call is also an elicitation
(`_meta.codex_approval_kind`, an empty form). It stays an approval card and
is accepted with empty `content`, as before. So does a `url` elicitation.

**Codex does not ask `request_user_input` in its Default mode.** On 0.159.3
the tool exists only in plan mode unless the feature
`default_mode_request_user_input` is on. Without it the model is told
`request_user_input is unavailable in Default mode`. The persona config does
not turn it on, so on this box a Codex persona does not ask this way today.
The live proof ran with `-c features.default_mode_request_user_input=true`
on the probe's own process.

Proved live on 2026-10-01 through `Server.route`, on the code in the checkout
(`engineer/jobs/20261001-155351-0b9031aa/work/probe.py`). Haiku called
`AskUserQuestion`; `Amber` was posted; the CLI's tool result read `Your
questions have been answered: "Which colour should the probe use?"="Amber"`.
A deny came back as an `is_error` tool result `User declined to answer
questions`. gpt-6-luna asked `request_user_input` with the flag on, and a
typed `Vermilion` reached it.

### Permission prompts

`--permission-prompt-tool stdio` is what turns a prompt into a
`control_request` with subtype `can_use_tool` on stdout. It is not in
`--help` — `--permission-prompts` mentions it in prose — and it is the flag
the Agent SDK passes when a host supplies `canUseTool`. **Without it the CLI
does not prompt at all: it denies anything that would have prompted and
reports it in the envelope's `permission_denials`.** Caused both ways,
2026-09-16, on 2.1.270.

The persona's own permission settings are untouched and are what decides
whether a prompt happens. Two things worth knowing before writing a client:

- **`WebFetch` does not prompt for a persona.** It is in `permissions.allow`
  in every persona's settings, as is `WebSearch`, `Task` and every MCP tool
  they have. Edits are `acceptEdits`.
- **A write outside the roots does not prompt either** — the boundary guard
  is a `PreToolUse` hook and refuses it first, with its own words, before the
  permission layer is reached. A deny is not promptable, which is the guard
  working as designed.
- What does prompt: a **read** outside `additionalDirectories` (the vault and
  `ai-labs`), which the guard does not govern. `decision_reason` comes back
  as `Path is outside allowed working directories`.

### Attachments

*The classic upload route and `resources` on a message retired 2026-11-12; the store, the cap, the types and the message shape below are what `POST /v1/threads/<id>/attachments` and a thread message use.*

A person hands a file to a conversation; the persona reads it, and sees it if
it is a picture. Added 2026-09-21, for the app's composer.

**An agent's reply can carry images too** (2026-11-18): `![a few words](path)`
in what it says, a file in the vault (absolute, or relative to the vault)
or in the workspaces, goes onto the message as `attachments`, at most three:
`{"path": <relative to its root>, "root": "vault"|"spaces", "name", "type",
"size", "width"?, "height"?, "alt"?, "refs": [<destinations as written>]}`.
**The reference stays in the text** (since that evening): it is where the
image goes. The page draws the picture at each `![…](dest)` whose `dest` is
in an attachment's `refs`, in the message, in its full record and while it
streams alike, with `width`/`height` (read off the file's header) holding its
space. Every engine can, being words; the turn prompt says so, and `bin/thread
post` does the same. A reference to anything else (a web address, a missing
file, the conversations folder, a fourth image) is just text. A message from
the morning before has its references taken off the text and no `refs`; the
page draws those images under its words. `lib/loam_threads.py`
(`reply_images`, `image_size`), `app/public/chat-ui.js` (`replyImages`),
`tests/test_reply_images.py`, `app/test/reply-images.test.js`,
`app/tools/capture-reply-images.mjs`.

**Upload, then send.** Two requests, never one:

```
POST /v1/sessions/<id>/attachments        multipart/form-data, `file` parts
  → 201 {"attachments": [{"path": "/home/loam/vault/98_ATTACHMENTS/loam-ui/2026-09-21/notes.md",
                          "name": "notes.md", "size": 30, "type": "text/markdown"}]}
POST /v1/sessions/<id>/messages           {"text": "...", "attachments": ["<that path>"]}
```

**The multipart field name is `file`**, which is what `loam-ui`'s composer
sends (`public/app.js`, `form.append('file', file, name)`); `files` and
`files[]` are read too, and a part with no `filename` is ignored. More than one
`file` part in a body is allowed and answered in order, so `attachments` is
always a list even for one file. `201`, because a file now exists that did not.
The upload is answered for a session whose process has ended — a record is
enough — so a file can be attached and the session resumed afterwards.

**Where the bytes go: the vault, not the workspace.**
`98_ATTACHMENTS/<app>/YYYY-MM-DD/<sanitised-name>`, where `<app>` is
`?app=<name>` on the upload and `loam-ui` when it is not given. The vault
because a file a person hands over is a thing they own, it outlives the
session, and every persona already has the vault as a working directory. The
day is in the path because a folder of every file anybody ever attached is a
folder nobody can read; the app is in it so two front ends never collide.

- **The name is sanitised into a name.** Directory parts go (both separators —
  a Windows browser sends backslashes), anything that is not a letter, digit,
  dot, dash or underscore becomes a dash, and leading dots go, so an upload
  can write neither a dotfile nor `..`. `Board notes (final).md` is stored as
  `Board-notes--final-.md`; the `name` in the answer is the stored name, and
  the page shows that rather than what the person's disk called it.
- **A second file of the same name lands beside the first**, as
  `shot-1.png`, never over it.
- **The type is read off the extension, not off the browser's content type**,
  which is whatever the operating system guessed and on a phone is often
  nothing at all. An extension this interface does not carry is **`415`** and
  nothing is written. The list is `ATTACHMENT_TYPES` in `lib/loam_broker.py`
  and is answered by `GET /v1/limits`: images (png, jpg, jpeg, gif, webp),
  documents (pdf, docx, xlsx, pptx, odt, epub, rtf) and the text and code a
  persona is asked to read (txt, md, csv, tsv, json, yaml, toml, html, xml,
  css, sql, diff, py, js, ts, sh, c, cpp, rs, go, rb, java, kt, swift, php
  and their kin), and zip, for an export from another AI service, stored as
  sent and never unpacked by the interface (2026-11-17).
- **Two caps, because they are two limits.** `attachment_max_bytes` is
  10 MB — this box's, a body held in memory and then written down — and a
  request over it is **`413`** in a sentence with the number in it, refused
  before the bytes are kept (the body is read and thrown away rather than the
  socket cut: a client whose connection was cut mid-upload never reads the
  reason). `inline_image_max_bytes` is 5 MB, which is the API's own per-image
  ceiling; see below.
- **The multipart envelope is parsed by hand, on bytes.** Both obvious answers
  are wrong: `cgi.FieldStorage` is gone from the standard library after 3.12,
  and `email.message_from_bytes` moves a part's payload through a `str`, which
  silently corrupts a PNG. `parse_multipart` in `bin/session-broker` reads the
  boundaries and the part headers and never touches what is between them.

**What the guard makes of the write: nothing, and it could not.** The boundary
guard is a `PreToolUse` hook on the CLI's own tools
(`hooks/boundary-guard.py`); this write is the broker's, a Python process
writing a file as the box's user, the one that owns the vault. No hook is in that
path and none can be. What keeps the write inside the lines is the route
itself — one directory under `98_ATTACHMENTS`, a sanitised name, an
allow-list of types, a cap — and it is said here so that nobody reads the
guard as covering it. On the other side, the persona reading the file **is**
governed as usual: the vault is one of its working directories, so a `Read` of
an attachment does not prompt.

**What the CLI is sent.** `content` stays a plain string when a message has no
attachments — exactly as it was before this existed — and becomes a list of
blocks when it has:

```json
{"type": "user", "message": {"role": "user", "content": [
  {"type": "text", "text": "attachment check\nAttached to this message (files on disk, readable with Read):\n- /home/loam/vault/98_ATTACHMENTS/loam-ui/2026-09-21/shot.png (image/png, 69 B) -- the image itself is attached below\n- /home/loam/vault/98_ATTACHMENTS/loam-ui/2026-09-21/notes.md (text/markdown, 30 B)"},
  {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0K…"}}]}}
```

**Every file is named by its path, and an image is *also* shown.** The path is
what a persona can act on: it reads a PDF, a CSV or a file of code with `Read`,
in its own time, and a 9 MB document never has to cross this pipe to be
useful. An image is the exception, because a persona cannot see a PNG by
reading it, so an image is base64'd into the turn as an image content block —
the shape verified against the CLI 2.1.270's own input handling. An image over
`inline_image_max_bytes` is still stored and still named, with "too big to show
inline" beside it rather than being dropped in silence.

**`broker.user_message` carries `attachments`** — the same objects the upload
answered — so a client replaying a conversation it did not watch live shows
the files with the question. `text` on that event is what the person typed and
nothing the broker added; the sentence naming the paths exists on the wire to
the CLI only. A message may be nothing but attachments, in which case `text`
is `""` and the session's stand-in title is `Attached <name>`.

**What a client may not do:** name a path this interface did not store. An
`attachments` entry is resolved and must come out inside `98_ATTACHMENTS`
(**`400`** otherwise, symlinks and `..` included) and must exist
(**`404`**). Making a persona read an arbitrary file on the box is a
capability the app has never had, and it does not gain it here.

### The dictation terms note

The names and terms that bias dictation live in one vault note,
`99_SYSTEM/Dictation terms.md`. The Obsidian plugin reads it from the vault;
an app's container cannot see the vault, so it asks here. Added 2026-09-30.

```
GET /v1/dictation-terms
  → 200 {"path": "99_SYSTEM/Dictation terms.md",
         "obsidian_url": "obsidian://open?vault=vault&file=99_SYSTEM%2FDictation%20terms",
         "modified": "2026-09-30T18:13:57Z",
         "text": "---\ncreated_by: ...\n# Dictation terms\n..."}
  → 404 {"error": "no dictation terms note on this box"}
```

- **One file, and no client can name another.** The path is
  `DICTATION_TERMS_PATH` in `lib/loam_broker.py`, overridable only by
  `LOAM_DICTATION_TERMS` in the broker's own environment (the unit, or a
  test). The route reads no query parameter, and any longer path is not this
  route (`405`, "no route").
- **The text is sent whole, frontmatter and headings included.** Turning it
  into terms is the reader's job, by the note's own rule: one term per line,
  headings, blank lines and frontmatter ignored, a line with commas is several
  terms. One rule in two readers (the plugin and the page) is kept honest by
  the note saying it, not by this route parsing it a third way.
- **`modified` is the file's mtime**, UTC, to the second. **`no-store`** on
  both answers, because the note is edited by hand and the next take must see
  the edit.
- **`obsidian_url`'s vault name is the vault's directory name, `vault`.**
  Obsidian keeps a vault's name nowhere in `.obsidian/`; it is the folder's
  name on the device that opened it. If the phone's copy is named otherwise
  the link opens nothing, and this is the line to change. A path pointed
  outside the vault answers its absolute path and `obsidian_url: null`.

### Files

The vault, the personas' workspaces and the runtime's own documents, for an
app whose container can see none of them. Added 2026-10-02; the rules are
`lib/loam_files.py`, the routes `files_route` in `bin/session-broker`, the
proof `tests/test_broker_files.py` (over real HTTP, against temp trees),
and `tests/e2e_files_app.py` with loam-ui's real server in front (run by
hand; it needs that checkout and Node). Nothing here deletes or takes a host
path; a vault file or folder may be renamed or moved (2026-11-13), never
over anything.

**A resource is a root and a path.** Three roots, named by
`GET /v1/files/roots`:

| root | is | The owner may |
|---|---|---|
| `vault` | the vault, whole | read everything visible; save and create text files and folders, upload files, and rename or move files and folders, anywhere but `15_CONVERSATIONS/` (renames: not the pinned names below) |
| `spaces` | each persona's workspace (a folder with an `agent.json`) and `commons` | read everything visible; save `<agent>/soul.md` and `<agent>/config.md` |
| `system` | a curated list of the runtime's Markdown (below) | read |

`path` is relative to the root: names joined by `/`, a leading or trailing
`/` forgiven, `""` or absent meaning the root itself. In a query it is
percent-encoded (`encodeURIComponent`, or `URLSearchParams` -- a `+` is read
as a space, so a literal `+` must be `%2B`) and **decoded exactly once**.

**What is never there.** These answer `404 {"error": "nothing at <root>/<path>"}`
when named, exactly as a file that does not exist, and are absent from every
listing: any name starting with `.` (in `spaces`, except `<agent>/.claude`,
`<agent>/.codex` and `<agent>/.mcp.json`, the persona's rendered
configuration, shown read-only); secret-shaped names anywhere (`*.env`,
`.env*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `id_rsa*` and kin,
`auth.json`, `credentials.json`, `token`, `*.token`, `.netrc`); in `spaces`,
the runtime's churn (`logs`, `cache`, `scratch`, `__pycache__`, `venv`,
`node_modules`, `*.log`, `*.pyc`, swap files) and any top-level folder that is
not an agent's or `commons`; every symbolic link, at any depth, and anything
that is not a regular file or a folder. The box's real secrets
(`agents/wake.env`, `secrets.env`, `state/`, `~/.claude`) are in no root at
all.

**Containment is the kernel's.** `.`, `..`, empty parts, backslashes and NUL
are refused (`400`) before the disk is touched. The walk opens each component
with `openat(..., O_NOFOLLOW)` from a descriptor on the root, so a folder
swapped for a link between a listing and a read is refused at the read
(proved: 300 reads against a thread swapping the folder, nothing from outside;
150 uploads the same, nothing written outside).

**Every row** (`list` entries, `stat`, `read`, and the answer to a write):

```json
{"root": "vault", "path": "40_PROJECTS/Loam/state.md", "name": "state.md",
 "kind": "file", "size": 1203, "modified": "2026-10-02T19:20:11Z",
 "media_type": "text/markdown", "authority": "user",
 "capabilities": {"read": true, "download": true, "write": true, "create": false,
                  "rename": true},
 "link": "/files/vault/40_PROJECTS/Loam/state.md"}
```

`kind` is `file` or `folder` (`size` and `media_type` null on a folder).
`capabilities` is what the owner may do here through this interface, decided on
the host; the page draws from it and never infers from a name. `write` means a
text save in place; `create` (folders only) means new files and folders in it;
`rename` (2026-11-13) means `POST /v1/files/rename` will rename or move it.
`authority` says whose the file is:

| authority | where | through Files |
|---|---|---|
| `user` | the vault outside `15_CONVERSATIONS/`; `spaces/<agent>/soul.md`, `config.md` | The owner writes text (`.md .markdown .txt .csv .tsv .json .jsonl .yaml .yml .toml .base .canvas .html .htm`) |
| `transcripts` | `vault/15_CONVERSATIONS/**` | read-only: the transcript writer's |
| `schedule` | `spaces/<agent>/crons/<name>.md` (added 2026-10-15) | The owner saves it in place (no create here); the save goes through the schedule log as their change -- see "Schedules and Activity" |
| `persona` | everything else in a workspace (`_always.md`, `_active.md`, `_reflection.md`, `skills/`, `memory/`, notes, the `crons/` folder itself), and `commons/**` | read-only: the persona edits its own, with its own tools |
| `runtime` | `spaces/<agent>/CLAUDE.md`, `agent.json`, `.mcp.json`, `.claude/**`, `.codex/**`, `spaces/README.md`; all of `system` | read-only: rendered by `bin/provision` or kept by the runtime, so an edit here would be reverted |

A persona's authority is not touched by any of this: Files writes are the
broker's, on the owner's behalf, and a persona still writes only through its
own tools under its own guard.

**`link`** is the app's deep link, `/files/<root>/<path>` with each name
percent-encoded on its own, the same shape the resolver reads back.

#### Reading

| route | answers |
|---|---|
| `GET /v1/files/roots` | `{"roots": [{"root", "name", "description", "link", "capabilities"}], "shortcuts": [{"label", "resource": <row>}]}` -- `shortcuts` (added 2026-10-02) are the vault folders that document the system (`99_SYSTEM`, `99_SYSTEM/conventions`, `40_PROJECTS/Loam` and its `architecture`, `infrastructure`, `decisions`), only those that exist; each is an ordinary vault row with the vault's capabilities, a way in and not a second copy |
| `GET /v1/files/list?root=&path=&cursor=&limit=` | `{"folder": <row>, "entries": [<row>…], "total", "cursor", "next_cursor", "truncated"}`. Folders first, then files, by name case-insensitively. `limit` defaults to 200 and is capped at 1000; `next_cursor` is a string to send back as `cursor`, or `null` on the last page. `truncated` is true only for a folder of over 50,000 names. A file named here is `400`. |
| `GET /v1/files/stat?root=&path=` | the row, file or folder |
| `GET /v1/files/read?root=&path=` | the row plus `"text"` and `"revision"` (`"sha256:<hex>"` of the exact bytes). UTF-8 text up to **2 MiB**: over it `413 {"error", "size", "max"}`; not UTF-8 or containing NUL `415 {"error", "media_type"}` -- download it instead. A folder is `400`. |
| `GET /v1/files/download?root=&path=` | the bytes, up to **50 MiB** (`413` over it), streamed, with `Content-Type` from the extension, `Content-Disposition: attachment; filename*=UTF-8''<name>`, `X-Content-Type-Options: nosniff`, `Content-Security-Policy: sandbox; default-src 'none'`, `Last-Modified`, `Cache-Control: no-store`. Vault HTML is untrusted: preview it only in an isolated, sandboxed origin. |
| `GET /v1/files/resolve?ref=&from_root=&from_path=` | what a link points at -- below |

Every JSON answer is `Cache-Control: no-store`. Errors are
`{"error": "<sentence>"}` with any named extras: `400` a malformed path or
parameter, `401` no token, `403` read-only (with `authority`), `404` absent or
not visible, `405` no such route (there is no delete), `409` a conflict, `413`
too big, `415` not text / not a type Files creates or uploads.

#### Archiving a folder as one zip (2026-11-18)

`GET /v1/files/archive?root=<vault|spaces>&path=<folder>` is the owner's own
export: the vault, the AI Spaces, or one folder in either, as one ZIP built
while it is sent. The app's *Download* calls it and keeps its own
list-and-download zip only as the fallback for a broker without the route
(`405`). Code: `archive` and `archive_stream` in `lib/loam_files.py`,
`files_archive` in `bin/session-broker`; tests in `tests/test_broker_files.py`
(`test_archive`).

**Request.** The bearer token, as everywhere (`401` without it); any other
method `405`. The query is decoded once and `path` follows the other routes'
rules (absent, empty or `/` is the top of the root). `system` or an unknown
root `404`. `path` must be a folder Files can see: absent, hidden,
secret-shaped or reached through a link `404 {"error": "nothing at …"}`; a
file `400`. Every refusal is answered before a byte of zip, as JSON with
`Cache-Control: no-store`.

**Answer `200`.** `Content-Type: application/zip`, `Content-Disposition:
attachment; filename*=UTF-8''<vault|ai-spaces|folder>.zip`,
`X-Content-Type-Options: nosniff`, `Content-Security-Policy: sandbox;
default-src 'none'`, `Cache-Control: no-store`, `Connection: close`, and
`Transfer-Encoding: chunked` with no `Content-Length`. The body is one ZIP:

- entry names relative to the requested folder, `/`-joined; a name that is
  not ASCII is UTF-8 with general-purpose bit 11 set (an ASCII name needs no
  flag; it reads the same either way);
- every folder is a `name/` entry, empty ones included, and every entry keeps
  its modified time: the DOS field in local time, clamped to 1980–2107, and
  an extended-timestamp field (`0x5455`) with the exact second in UTC;
- DEFLATE for text (`loam_files.ARCHIVE_DEFLATE`: the types Files edits and a
  few more text kinds), STORE for everything else, which is compressed
  already; data descriptors throughout, since the output is never seeked;
- Zip64 where it is needed: an entry over 4 GiB from its first byte, and the
  Zip64 end records for an archive over 4 GiB or 65,535 entries.

Memory is flat: the walk and the reads run on a worker thread one 64 KiB
read at a time, and the loop sends each chunk and waits on `drain()` before
the next. No temp file, no size cap per file, no 50,000-name cutoff.

**Never a quietly incomplete zip.** Each entry goes in whole or the archive
fails. A file that cannot be opened or read (other than one gone, or swapped
for a link, between the scan and its open), a name that is not UTF-8, or a
file that grows past what its entry was opened for: the broker logs
`files: archive of <root>/<path> failed after <n> bytes …` and resets the
connection, with neither the zip's central directory nor HTTP's last chunk
sent. A client sees a failed download, and what it holds is not a valid zip.
A finished archive logs its file, folder and byte counts and its time.

**What goes in** (`archive_keeps`, not `visible`: an export holds everything
that is the person's and nothing that is a credential, where browsing also
hides what is only noise to look at):

- Never a symbolic link (not followed, not stored), a socket, device or FIFO.
- Left out at any depth, in both roots: `.git` (history is the Git backup's
  to carry; the vault's was 4.9 GB on 2026-11-18), `.stversions` and
  `.stfolder` (Syncthing's old copies and marker), every `SECRET_NAME` match,
  and `secret.*`/`secrets.*` in `.json`, `.yaml`/`.yml`, `.toml`, `.env`,
  `.txt` (`ARCHIVE_SECRET`).
- In `spaces`: only agents, `commons` and `README.md` at the top, as
  `visible` has it; `SPACES_PRIVATE` folders, `SPACES_PRIVATE_SUFFIX` files,
  and `.locks` and `.venv` (`ARCHIVE_SPACES_CHURN`) left out.
- Every other dotfile is kept: the vault's `.obsidian/` (so the zip opens in
  Obsidian with its settings), `.trash/`, `.gitignore`, `.stignore`; a
  workspace's `.claude/`, `.codex/`, `.agents/`, `.mcp.json` and
  `skills/.usage.jsonl`. The rendered configurations name key files and
  secret names, never their values.

Names are judged, not contents: a credential pasted into a note or a
transcript goes into the export as it is in the vault.

On this box, 2026-11-18, through a scratch broker: the whole vault, 5.0 GB
(5,003,261,181 bytes), 8,861 entries (8,427 files, 434 folders), 36 s, the
broker's peak resident memory 53 MB (36 MB idle); the AI Spaces, 507 MB and
1,263 entries in 4 s.

#### Writing: the owner's, conditional, atomic

| route | body | answers |
|---|---|---|
| `PUT /v1/files/text` | `{"root", "path", "text", "base_revision", "by"?}` | `200` the row plus `text`, `revision`, `provenance` |
| `POST /v1/files/file` | `{"root", "path", "text"?}` | `201` the same; `409` if anything has that name |
| `POST /v1/files/folder` | `{"root", "path"}` | `201` the folder's row; `409` if it exists |

- **A save may say whom it was made for** (`by`, since 2026-11-16): a live
  persona's id, from a plugin acting for it (doc-comments, when a persona
  comments or accepts through its tool). The provenance stamp then carries
  that persona's link (`last_modified_by: "[[Name]]"`, the name from its
  `agent.json` as its transcripts spell it) instead of the owner's. The
  owner's id, or none, is the owner; anything else is `400`. It changes the
  stamp only: who may save what is the same.
- **A save names the revision it was edited from**, as `read` answered it.
  If the file is no longer that revision -- a persona wrote it meanwhile --
  the answer is `409 {"error", "current_revision"}` and the file is untouched:
  read it again and redo the edit on top. Missing `base_revision` is `400`.
- **Atomic**: the new bytes go to a temp file beside the target, are
  fsynced, the revision is checked again, and the temp file is renamed over
  the target (mode kept). A reader sees the old file or the new, never half.
  The one window left is between that second check and the rename,
  microseconds; agents take no lock this could share.
- **Creation never replaces**: the new file is linked into place with
  `link(2)`, which fails on any existing name, a dangling link included.
  Names that are hidden, secret-shaped or runtime-private are `400`; an
  extension outside the text list is `415`; the parent must exist (`404`
  otherwise) and have `create`.
- **Text is capped at 1 MiB** per save (`413`), because the JSON body that
  carries it is capped at 4 MiB and escaping can double it.
- **Provenance**: a Markdown file the owner saves or creates in the vault gets
  `last_modified_by: "[[Sam]]"` in its frontmatter (a frontmatter
  is made if there is none), and a created one `created_by` too unless the
  text already has one -- the convention in
  `99_SYSTEM/conventions/provenance.md`, with the link their own notes carry.
  `created_by` on an existing note is never changed. So the stored text can
  differ from what was sent: the answer's `text` and `revision` are what is on
  disk, `provenance: true` says it was changed, and the editor continues from
  those. Workspace files (`soul.md`, `config.md`) are written verbatim.
- **HTML is stored text** (`.html`, `.htm`, since 2026-10-02): read, saved
  and created under exactly the gates above -- authority, revision, size,
  UTF-8 -- and written verbatim, with no provenance frontmatter. The broker
  never serves it as a page: `download` stays `attachment` with
  `CSP: sandbox`. Previewing it is the app's, in an isolated origin; an
  editor shows its source.

#### Renaming and moving (2026-11-13)

The owner, 2026-11-13: "When I open a specific file, I couldn't find a way to
easily edit the name of the file." A vault file or folder, to a new name in
its folder or a new path in the vault. The rules are `rename` in
`lib/loam_files.py`; the proof is `test_rename` in
`tests/test_broker_files.py`.

```
POST /v1/files/rename
Authorization: Bearer <token>
Content-Type: application/json

{"root": "vault", "path": "Inbox/draft.md", "name": "Final draft.md"}   rename in its folder
{"root": "vault", "path": "Inbox/draft.md", "to": "Notes/Final draft.md"}  move (and rename) in the root
```

Exactly one of `name` and `to`. `200` answers the row at its new place, plus
`"from": {"root", "path", "link"}` naming where it was:

```json
{"root": "vault", "path": "Inbox/Final draft.md", "name": "Final draft.md",
 "kind": "file", "size": 1203, "modified": "2026-10-02T19:20:11Z",
 "media_type": "text/markdown", "authority": "user",
 "capabilities": {"read": true, "download": true, "write": true, "create": false,
                  "rename": true},
 "link": "/files/vault/Inbox/Final%20draft.md",
 "from": {"root": "vault", "path": "Inbox/draft.md",
          "link": "/files/vault/Inbox/draft.md"}}
```

- **Never over anything.** The kernel decides, at the instant of the rename
  (`renameat2` with `RENAME_NOREPLACE`): a name taken by a file, a folder or
  a dangling link is `409 {"error": "vault/<path> already exists; choose
  another name"}`, and neither is touched.
- **The content is not touched.** No provenance is stamped (a rename is not
  an edit), the bytes and the modified time are the file's own, and a folder
  moves whole. Links elsewhere in the vault that name the old path are not
  rewritten; they resolve as they would in Obsidian with link-updating off.
- **The name** is the person's, NFC and trimmed as an upload's is, and is
  refused (`400 {"error"}`, never rewritten) when it is empty or blank, holds
  `/` or `\`, is `.` or `..`, holds a control character, is over 255 bytes,
  is hidden (leading `.`), or is secret-shaped (`*.env`, `*.pem`...). The
  extension may change. The same name as now is `400`; a folder into itself
  is `400`; neither or both of `name` and `to` is `400`.
- **`to`** is a path in the same root, decoded as a body string (no
  percent-decoding), with the same rules as `path`; its folder must exist
  (`404`) and have `create` (`403`, e.g. `15_CONVERSATIONS/`). There is no
  move across roots.
- **What is renamed** is what the row's `capabilities.rename` says, and the
  page should draw the action from it: a visible vault file or folder with
  authority `user`. Everything else is `403 {"error", "authority"}`:
  transcripts (`15_CONVERSATIONS/` and everything in it), every path in
  `spaces` (a workspace's files are its persona's and the runtime finds
  them by name; `soul.md` and `config.md` included), and all of `system`.
- **Pinned names** are `403 {"error": "vault/<path> keeps its name: <why>",
  "authority": "user", "pinned": true}`: every top-level vault folder (the
  vault's structure), and the vault paths the runtime reads or writes by name
  (`RENAME_PINNED` in `lib/loam_files.py`: `00_INBOX/boards/*`,
  `00_INBOX/from-agents/comms.md` and `loam-wake-failures.md`,
  `00_INBOX/archive`, `40_PROJECTS/*/receipts`, the Loam engineer notes
  `bin/provision` copies, `40_PROJECTS/Loam/infrastructure/schedule.md`,
  `architecture`, `decisions`, `reports`, `99_SYSTEM/Dictation terms.md`,
  `99_SYSTEM/conventions`) and any folder holding one that exists -- so
  `40_PROJECTS/<project>` is pinned once it has `receipts/`. Files inside a
  pinned folder that are not themselves pinned (a receipt, a decision) can
  be renamed.
- **Links are absent**, here as everywhere in Files: a symbolic link as the
  source, or on the way to it or to `to`, is `404 {"error": "nothing at
  …"}`, and nothing it points at is touched. (The persona tools' `move_file`
  and `delete_file` refuse links since 2026-10-13, after a link's target was
  deleted through one on 10-02.) A path outside the root, by `..` or
  otherwise, is `400` before the disk is touched.

#### Uploading a file into a vault folder

Added 2026-10-02 (the design's fourth Add action, "Upload"). One file per
request, into a folder that exists and has `create` -- in practice any vault
folder outside `15_CONVERSATIONS/`; `spaces` and `system` are `403`.

```
POST /v1/files/upload?root=vault&path=<folder, percent-encoded; "" = the vault's top>
Authorization: Bearer <token>
Content-Type: multipart/form-data; boundary=<...>

one part:  Content-Disposition: form-data; name="file"; filename="<name>"
           <the file's bytes>
```

What a browser's `FormData` with `form.append('file', file)` sends, relayed
untouched (the app's `broker.upload()` is the relay its attachment route
already uses). The part's own `Content-Type` is not read: the extension
decides. `201` answers the stored file's row plus:

```json
{"root": "vault", "path": "Inbox/IMG 2041 2.jpg", "name": "IMG 2041 2.jpg",
 "kind": "file", "size": 2412311, "modified": "2026-10-02T20:31:07Z",
 "media_type": "image/jpeg", "authority": "user",
 "capabilities": {"read": true, "download": true, "write": false, "create": false},
 "link": "/files/vault/Inbox/IMG%202041%202.jpg",
 "revision": "sha256:<hex of the bytes on disk>",
 "requested_name": "IMG 2041.jpg", "renamed": true, "provenance": false}
```

- **Never an overwrite.** The file is linked into place with `link(2)`; a
  name taken by anything (file, folder, link, a racing upload) becomes
  `name 2.ext`, `name 3.ext` -- the design's numbering -- up to 999, then
  `409`. `renamed` says it happened; `path`/`link` are where it went.
- **The name is the person's**, after one step: only the last part of what
  the browser sent is kept (`/` and `\` both separate), NFC. It is refused
  (`400`), never rewritten, when it is empty, `.`/`..`, hidden (leading
  `.`), holds a control character, is over 240 bytes, or is secret-shaped
  (`*.env`, `*.pem`, `id_rsa`...). A browser sends `"` in a name as `%22`,
  and that is stored as typed.
- **Types**: a chat attachment's (`GET /v1/limits` `attachment_types`), the
  text types Files edits, and `.heic`/`.heif`; the list is
  `GET /v1/limits` `files_upload_extensions`. Anything else, or no
  extension, is `415 {"error", "types"}`. For `png jpg jpeg gif webp heic
  heif pdf docx xlsx pptx odt epub zip` the first bytes must be that format's
  (`415` otherwise), so a name never lies about what a preview will get.
- **Size**: up to `files_upload_max_bytes` (10 MiB, the attachment cap). Over
  it by up to 8 KiB, `413 {"error", "size", "max"}`; further over, the body
  is discarded unread and the answer is `413 {"error"}`.
- **Bytes are stored exactly**, mode `0644`, never executable, never served
  as a page (a download is `attachment` with `CSP: sandbox`). The one change:
  a `.md`/`.markdown` file must be UTF-8 (`415` otherwise), loses a leading
  BOM, and gains the owner's provenance as a created note does
  (`provenance: true`, `revision` of what is on disk).
- **Envelope**: not `multipart/form-data` is `415`; no boundary, no part, a
  part not named `file`, or more than one file is `400`. Every refusal is
  before a byte is written, and no temp file is left behind.

#### Links: `resolve`

`ref` is any link as written; `from_root`/`from_path` are the note it is
written in, when there is one. The answer's `status`:

- `found`, with `resource` (a row) and `fragment` (heading or block, or null);
- `ambiguous`, with `candidates` (rows) -- a short note name matching more
  than one file is a choice for the person, never a guess;
- `missing`, with what was looked for; also any path outside the roots or
  not visible, without saying which;
- `external`, with `url`: a web link stays a web link.

Read, in order: `/files/<root>/<path>` (and `https://loam-ui.<domain>/files/…`);
`obsidian://open?vault=…&file=…` (`.md` added when the name has no
extension); an absolute host path inside the vault, the spaces or a System
source; then a wiki-link (`[[target]]`, `![[embed]]`, with `|alias`, `\|`
as a table writes it, `#heading` or `#^block`) or a relative Markdown link
(`../x.md`, percent-decoded once), alike:

1. **Relative** -- any `.` or `..` in the target: from the linking note's
   folder, in `from_root` (vault or spaces), climbing only inside that root.
   A climb past the root's top is `missing` with `reason`; a relative link
   to a file that is not there is `missing` with the `path` it named, and
   is never searched for elsewhere. With no linking note, or from a System
   document, there is no folder to climb from: `missing`.
2. **From the linking note's folder**, then **from the root's top** (with
   `.md` added when the last name has no `.md`).
3. **Partial path, by Obsidian's rule**: every visible vault file whose path
   *ends with* the target's names, whole names only, case-insensitively --
   `[[state]]`, `[[Loam/state]]` and `[[40_PROJECTS/Loam/state]]` all name
   `40_PROJECTS/Loam/state.md`. One match is `found`. More are `ambiguous`
   with `candidates` (shallowest first, at most 50) and `candidates_total`.
   The walk skips hidden, secret-shaped and linked names exactly as `list`
   does, and stops after 50,000 names: an answer it cut short carries
   `truncated: true` and is never `found`. (~3,600 files, 0.04 s, 2026-10-02.)

`[[#heading]]` with an empty target is the linking note itself. Every answer
row comes from the same descriptor walk as `stat`, so nothing a lookup finds
is outside a root or hidden.

**Folders** *(2026-10-02)*. A link may name a folder, and the answer is the
same `found` with the folder's row (`kind: "folder"`, `size: null`, `link`
`/files/<root>/<path>`, its own `authority` and `capabilities`); several are
`ambiguous` with folder rows. `/files/<root>/<folder>`, an absolute host path
to a folder inside a root, and `obsidian://open?file=<folder>` were always
or are now read so. A wiki-link or relative link reaches a folder by the
same steps as a note -- `[[40_PROJECTS/Loam]]`, `[[40_PROJECTS/Loam/|Projects]]`,
`[[Loam/]]`, `[[research]]`, `[[loam-ui/research]]`, `[[../Loam]]`, `../`,
`[[..]]` (`[[../..]]` from two deep is the root itself) -- under one rule:

- **A note always wins.** A folder is looked for only once no file was found
  by any step above, the partial-path search included: a folder can only
  answer a link that was `missing` before folders were read, and no link that
  found a note changes. `[[Loam]]` in the live vault is a choice between
  two notes (`40_PROJECTS/Loam/Loam.md`, `…/engineer/projects/loam.md`),
  never the folder -- `[[Loam/]]` is the folder; `[[Topics]]` beside
  `Topics.md` and `Topics/` is the note; `[[Many]]` is a `Many.md` anywhere
  before a `Many/` at the top. `obsidian://open?file=X` is `X.md` when there
  is one, else the folder `X`.
- **A trailing `/` asks for a folder alone** (`[[Topics/]]`,
  `[[40_PROJECTS/Loam/]]`, `../`): no file is looked for.
- Folder steps, after the note steps: the folder from the linking note's
  folder, then from the root's top, then every visible vault folder whose
  path ends with the target's names (whole names, case-insensitive): one is
  `found`, more are `ambiguous` (shallowest first, at most 50,
  `candidates_total`). A relative folder link stays inside its root exactly
  as a relative note link does.
- The walk is the note walk (one pass, 50,000 names): hidden, secret-shaped
  (`archive.key/`) and linked folders are absent, never listed nor descended,
  and a cut-short walk is never `found` -- even for an exact folder path,
  since a note it did not reach might outrank it.

#### Attaching a file picked in Files

`POST /v1/sessions/<id>/messages` takes `"resources": [{"root", "path"}, …]`
beside `attachments`. Each must be a visible file, and **inside the session
agent's read scope** -- its workspace and the `additionalDirectories` of its
own `.claude/settings.json`, resolved -- or the message is refused whole:
`403` naming the agent and the file, `404` for anything absent or hidden,
`400` for a malformed list. Picking a file never extends what an agent may
read; today every persona's scope is the vault and `ai-labs`, so `system`
resources are refused to all of them. An accepted resource is carried exactly
like an upload: named by its host path in the text the CLI receives (an image
also shown inline), and on `broker.user_message`'s `attachments` with a
`resource` field naming its root and path. It is the live file, not a
snapshot.

#### The System root, inventoried 2026-10-02

Read from the live checkout (`/home/loam/agents`, `SYSTEM_FILES` in
`lib/loam_files.py`); a file missing from the box is missing from the root.

| System path | source |
|---|---|
| `Session broker.md` | `broker/README.md` |
| `Deployment.md` | `deploy/README.md` |
| `Logs.md` | `docs/logs.md` |
| `Engineer/Role.md` | `engineer/role.md` |
| `Engineer/Platform project.md` | `engineer/project.md` |
| `Engineer/Runtime memory.md` | `engineer/memory/MEMORY.md` |
| `Engineer/Project template/README.md`, `CLAUDE.md`, `project.md`, `MEMORY.md` | `engineer-template/` |
| `Reviewer/CLAUDE.md` | `reviewer/CLAUDE.md` |

`Engineer/Role.md` and `Engineer/Platform project.md` are byte-for-byte
copies `bin/provision` makes of vault notes (`40_PROJECTS/Loam/engineer/role.md`,
`.../engineer/projects/loam.md`; `check-drift` holds them equal). Their rows
carry `"source": {"root": "vault", "path", "link"}`: the vault note is the one
that is edited, and the System copy follows on the next provision.

Reviewed against the checkout again on 2026-10-02: every tracked Markdown file
in the runtime is in the table, except `engineer/CLAUDE.md`, which is the two
`@` imports of the files above and nothing else.

Not in it, on purpose: the vault's `99_SYSTEM/` (conventions, cron records,
schemas) is under `vault` already and would otherwise be one file with two
sets of capabilities; each persona's rendered configuration is under
`spaces/<agent>/` (`CLAUDE.md`, `.claude/settings.json`, `.codex/config.toml`,
`.mcp.json`), read-only; the persona defaults themselves are code in
`bin/provision`, not Markdown, so there is no defaults file to show; the
Codex instruction files under `state/broker/codex/` are generated state, and
`state/` is runtime-private. Settings that change behaviour belong on typed
routes, not on editing these.

What System does not show because it is not Markdown: the deployment's
`deploy/loam.toml` and each app's `deploy/<app>/deploy.toml` and
`compose.yaml`; the project registry `projects/*.json`; the unit files in
`systemd/`; the engineer's and reviewer's `.claude/settings.json` and
`agent.json`; and the constants in code (`lib/loam_broker.py`, `bin/provision`,
`lib/loam_cron.py`, `lib/loam_schedule.py`). The prose that explains them is `Deployment.md` and this
file.

### Apps (2026-11-12)

The Apps section of the page (the vault's `40_PROJECTS/loam-ui/The app's
shape — three sections (11-12).md`) lists what the user has built. This is
its source: one read-only route, read fresh from the deployment layer on
every call (`lib/loam_apps.py`), so the list is exactly what Caddy serves.
Nothing needs restarting for it to change: a `bin/site up` or a new
deployment record is in the next answer.

```
GET /v1/apps            ->  200  {"apps": [<row>, ...], "sources": ["yours", "installed", "loam"]}
                            Cache-Control: no-store
```

Sites first, then projects, then app plugins (2026-11-13), each group by
name. A row, every key always present:

| key | value |
|---|---|
| `name` | the label: `light`, `owner-site` |
| `url` | `https://<name-or-host>.<domain>/`, behind the same login as the page |
| `also_at` | other URLs the same app answers on (a project's own domain: `["https://owner-site.example/", "https://www.owner-site.example/"]`), each with **its own** login; `[]` for most |
| `source` | `yours` for a site, a project, and a plugin built here; `installed` for a plugin installed from a link; `loam` for one shipped with Loam |
| `kind` | `site` (an agent's own subdomain, `bin/site`), `project` (a project's deployed container, `deploy/<app>/`), or `plugin` (an app plugin, below) |
| `owner` | the agent that stood a site up (`otto`, `ada`; draw it from `GET /v1/agents`), or `owner` for a project |
| `project` | the project's name for a `project` row, else `null` |
| `access` | `private` (behind the login), `shared` (a project with a share link; the link itself is not here), or `public` (on the owner's yes) |
| `expires` | a site's last day, `YYYY-MM-DD` UTC (it answers `410` after), or `null` |
| `shared_until` | a shared project's link's last day, or `null` |
| `serves` | `static` (files Caddy serves) or `container` (a process behind a port) |
| `frame` | `true` when Caddy itself sends this app a frame policy that admits the page's origin (an agent's static site; deploy/README.md, "Framing a site in the app"); `false` when nothing here can say, which means **open it in the browser** |
| `created` | when a site was stood up (or a plugin installed), ISO UTC, or `null` |
| `icon` | (2026-11-13) a picture's address, or `null`: an app plugin's manifest `icon`, served at `/v1/plugins/<id>/files/<icon>` (the page's `/api/plugins/…`) |

**App plugins** (2026-11-13; docs/plugins.md, "Server plugins"): every
plugin in force whose `kind` includes `app`. Its `url` is **relative**,
`/x/<id>/`, because it is served on whichever host the page is on (Caddy
sends `/x/*` there to the plugin gateway, behind the same login), so `frame`
is `true` (same origin); `serves` is `plugin`, `access` `private`. Three more
keys: `plugin` (its id, for `/v1/plugins/<id>`), `on`, and `state` (its
server's: `running`, `starting`, `failing`, `stopped`, from the plugin
row's `service`). An app plugin that is off is still listed, with `on:
false`; turning it on is `PATCH /v1/plugins/<id>`.

Left out on purpose: the runtime's own static hosts (`files`, `vault`), which
nobody built, and every record with `app_shell = true`, which is the page
itself (`loam-ui`) and its staging copy (`loam-ui-next`).

Example, 2026-11-12:

```json
{
  "name": "rows", "url": "https://rows.example.com/", "also_at": [],
  "source": "yours", "kind": "site", "owner": "ada", "project": null,
  "access": "private", "expires": "2026-11-25", "shared_until": null,
  "serves": "static", "frame": true, "created": "2026-11-11T20:16:50Z"
}
```

**For the page.** The relay passes it as `GET /api/apps` with `rest ===
'/apps'`; `lib/loam_settings.PAGE_RELAY_AHEAD` already classifies that
spelling, so adding it to the relay does not fail this repository's suite
(move its line into `PAGE_RELAY` after). To frame a `frame: true` row the
page's own CSP must list the site's origin in `frame-src`; that is the page's
change. A `503` (`{"error": "the apps list cannot be read: <record>: <why>"}`)
means a site or deployment record the generator would also refuse; show the
sentence.


## GitHub (2026-09-30)

GitHub is an integration the owner connects from the app: a **GitHub App
registered from a manifest**, so nothing is pasted and nothing expires while
the box is in use. The flow and its store are `lib/loam_github_app.py`; the
routes below only dispatch to it, each GitHub call on a worker thread.

| route | body | answers |
|---|---|---|
| `GET /v1/integrations/github` | | the state the page draws (below) |
| `POST /v1/integrations/github/manifest` | `{name?}` | `{manifest, post_url, state}` -- the page form-posts `manifest` (JSON) to `post_url` |
| `POST /v1/integrations/github/register` | `{code, state}` | the state, plus `install: {url, state}` |
| `POST /v1/integrations/github/install` | | `{url, state}` -- install again / reconnect |
| `POST /v1/integrations/github/callback` | `{code, installation_id, setup_action, state}` | the state |
| `POST /v1/integrations/github/refresh` | | refreshes the user token now, re-checks both; the state |
| `POST /v1/integrations/github/token` | `{repository: "owner/name"}` | `{token, expires_at, repository}` -- for `bin/git-credential-loam` |
| `DELETE /v1/integrations/github` | `org?` (body or query) | `{forgotten, uninstall_url, revoke_url, delete_app_url}`; with `org`, that organization's app alone, plus `org`, its pages on the org (404 when no app is held for it) |

GitHub redirects the browser to the app, never to the broker:
`https://loam-ui.<domain>/integrations/github/registered?code&state` after
Create, and `.../integrations/github/installed?code&installation_id&setup_action&state`
after install. The page forwards those query parameters to `register` and
`callback`. A `state` is single-use and lives an hour; a `callback` with no
state is accepted only for `setup_action=update` on the installation already
recorded (GitHub starts that redirect itself when the repository selection
changes on its side).

`GET` answers `status` (`unregistered`, `registered`, `installed`,
`authorized`, `unhealthy`), `registered`, `app {id, slug, url,
registered_at}`, `installed {installation_id, account, repository_selection,
installed_at}`, `user {login, token_expires_at, refresh_expires_at,
authorized_at}`, `health {last_ok_at, last_error}` and `uses`, with `null`
for a part that is not there yet. No token, key or secret is ever in an
answer except the one-repo installation token from `token`.

**The manifest's permissions**: administration write (create a repository,
its deploy key, delete it on retire), contents write (clone, fetch, push),
metadata read (required by GitHub for any repository permission), workflows
write (a push carrying `.github/workflows/*` is refused without it). No
webhook (`active: false`), no events.

**Two tokens, each for what only it can do.** The *user* token (eight
hours, refreshed by the module within an hour of expiry, each refresh a new
pair good for six months) creates repositories on the personal account:
`lib/loam_github.token()` returns it once the integration is authorized.
*Installation* tokens (an hour, minted with an RS256 app JWT on every ask,
never cached -- a cached one outlived a repo deleted and recreated under its
name on 2026-10-01 -- and never written to disk) are scoped to
one repository and carry every permission the installation has accepted
except `administration` (read live on each mint; administration can make a
repo public, which is never a project engineer's), and are what a project
engineer pushes and works its issues and pull requests with: `bin/engineer-job` adds `loam_github.helper_env()` to
the job's unit when the app is installed, which rewrites `git@github.com:` to
https and makes `bin/git-credential-loam` the only credential helper. The
helper answers for the job's own repository and nothing else.

**The store** is `state/github/integration.json`, mode 600, outside git and
outside every container; `integration.lock` beside it serialises every
read-modify-write across the broker, `bin/provision` and `bin/lane-check`,
because a refresh token is single-use.

**Nightly**: the first `bin/lane-check` of each date (run by `bin/drift-watch`
at 01:40) calls
`loam_github_app.nightly_warnings()` (`state/github/checked-on` holds the
date) and writes one Important line in `comms.md` if the refresh token is
within 14 days of expiry or GitHub says the installation is gone. That check
also refreshes a stale user token, which is what keeps the six-month clock
rolling on a box nobody touched.

Tests: `tests/test_github_app.py`, against a fake GitHub that verifies the
JWT signature.

### GitHub accounts, one at a time (2026-11-13)

Settings' refresh and disconnect acted on every GitHub account at once
(`POST /refresh` checks every app; `DELETE` without `org` forgets the whole
store, organizations' apps included). These four routes act on one account.
An **account** is a GitHub login Loam reaches, of one of three kinds:

| kind | what it is | its disconnect |
|---|---|---|
| `personal` | the account the Loam app was connected from (its first installation) | the Loam app's own, so the app goes, and every `installation` with it (listed beforehand in `disconnect_also`) |
| `installation` | another account the Loam app is installed on | that installation's record alone |
| `org` | an organization with an app of its own | that organization's app alone |

Organizations' apps are never touched by another account's disconnect. On
GitHub, uninstalling and revoking stay the owner's clicks; each answer names
the page.

| route | body | answers |
|---|---|---|
| `GET /v1/integrations/github/accounts` | | `{accounts: [<account>…]}`: personal, its other installations, then each org |
| `POST /v1/integrations/github/accounts` | `{}`, `{org}`, or `{org, own_app: true}` | where to go to add one: `{step: "install", url, state}` (the Loam app's install screen, where GitHub lets them pick any account; with `org`, that org's own registered app's), or `{step: "register", manifest, post_url, state}` (no Loam app yet, or `own_app`: a new app owned by `org`, posted as `POST /manifest`'s answer is) |
| `POST /v1/integrations/github/accounts/<login>/refresh` | | `{account: <account>, accounts: […]}`: that account's installation checked with its app's JWT, its authorization's token refreshed (an `installation` shares the personal account's), its health recorded on it |
| `DELETE /v1/integrations/github/accounts/<login>` | | `{forgotten: true, account, kind, forgot: [logins], uninstall_url, revoke_url, delete_app_url, accounts: […]}`; the store file goes when nothing is left |

`<login>` is case-insensitive; one not connected is a 404
`{"error": "no GitHub account 'x' is connected here"}`. An **account** is:

```json
{"account": "octo-owner", "kind": "personal", "status": "authorized",
 "app": {"id": 5143053, "slug": "my-loam-app", "url": "https://github.com/apps/my-loam-app",
         "owner": "octo-owner", "registered_at": "2026-10-01T00:42:50Z"},
 "installation": {"installation_id": 166702005, "repository_selection": "all",
                  "installed_at": "2026-10-01T00:43:18Z"},
 "user": {"login": "octo-owner", "token_expires_at": "…", "refresh_expires_at": "…",
          "authorized_at": "…"},
 "health": {"last_ok_at": "…", "last_error": null, "checked_at": "…"},
 "disconnect_also": [],
 "manage_url": "https://github.com/settings/installations"}
```

`status` is `authorized`, `installed` (no authorization), `registered` (no
installation) or `unhealthy` (`health.last_error` says why). `health` is the
account's own last check; before its first, its app's. The whole check
(`POST /refresh`, the nightly) now records each installation's health too,
and an app's health is the first error among its installations' and its
authorization's. The old routes are unchanged. Credentials stay the owner's
hands: these are under the registry's `integration.github`, `reach`
`owner`, so no persona changes them.

### A repository Loam does not own (2026-10-06)

**More than one installation.** The first stays `installation`; one made
since on another account (an organization, selected repositories) goes in
`added_installations`, and `GET` lists all of them as `installations` beside
the unchanged `installed`. A new installation is recorded only through a
`state` this box issued, and when a user is authorized (in that redirect or
before it) only if `GET /user/installations` with that user's token lists it.
A redirect authorized by a different GitHub user records nothing, so the user
token provisioning acts with is never replaced by someone else's. Once the
app is public anyone can install it; their redirect has no state of ours and
is refused, and a token is minted only from the recorded installation whose
account owns the repository.

**A project that lands by pull request** (`"landing": "pull_request"` in
`agents/projects/<name>.json`, written by `bin/provision --project <name>
--adopt <owner>/<name>`): its job's token is everything the installation
grants less `administration` and `workflows`, with `contents: read`. The
engineer fetches and cannot push. The landing asks the broker:

| route | body | answers |
|---|---|---|
| `POST /v1/integrations/github/pull-request` | `{project, branch, title, body}` | `{url, number, sha, branch, head, base, created}` |

The broker pushes `refs/heads/<branch>` of the project's canonical checkout
to the same name on the remote with a full installation token, then opens the
pull request against the record's `default_branch`, or finds the open one
for that head. `branch` must be a job's (`job/<id>`); nothing else is ever
pushed, so a remote's default branch cannot be named. Caused against GitHub
on 2026-10-06 on a throwaway private repo: a push to `main` with the job's
token was `403 Write access to repository not granted`, and so was a push of
`job/<id>`; `ls-remote` with the same token worked; the broker's function
pushed the job branch and opened PR #1, a second call found it
(`created: false`), and `main` did not move.

**Where its receipts go.** `vault/40_PROJECTS/<name>/receipts/`, unless the
record carries `"vault_folder"` (since 2026-10-07), written by `--adopt ...
--vault-folder <Folder>` when the vault already has the project's folder
under another spelling: project `widget` files into `Widget/`. Adopting refuses a
name whose folder would be a case-only twin of one that exists, because the
Mac and the phone cannot hold both, and says which `--vault-folder` to pass.

**Adopting from inside an engineer job** (since 2026-10-07): the job's wall
mounts `agents/` read-only, so provision writes the record to
`$LOAM_JOB_TREE/projects/<name>.json` instead and says so. Everything else
completes in the one run; commit the record and the project is registered
when the job lands (`engineer-job --land`, or at the session's end).

**Adding an installation needs an install URL with our state.** The page
offers *Install* only while nothing is installed and *Reconnect* only when
unhealthy, so a second account is added with a state minted on the box
(`loam_github_app.start_install()`, good for an hour), until the page draws a
pill for it.

### An app owned by an organization (2026-10-06)

The first app is private, and GitHub lets a private app be installed only on
the account that owns it, so an organization's repositories get **an app of
their own**: registered from a manifest posted to
`https://github.com/organizations/<org>/settings/apps/new` (GitHub's
"registering a GitHub App from a manifest"; only an owner of the org can
click Create there), private, owned by the org, which can widen, narrow or
delete it without touching the first app. In the store it is
`org_apps.<org>`: its own `app`, `installation`, `user` and `health`. The
first app stays at the top of the store, unmoved.

- **Routing.** A token for `owner/name` comes from `org_apps[owner]` when
  there is one, from its installation on `owner`, and from nothing else --
  not from the first app, even if the first app holds an installation on
  that org. Every other owner is the first app's, as before. An org app's
  installation on any account but its org is refused and records nothing.
  `route(data, repo)` returns the app and installation; everything that
  mints (job tokens, `read_token`, `open_pull_request`,
  `add_repo_to_installation`) goes through it, so the issued-state rule,
  GitHub's word on the installation, read-only job tokens and the PR route
  hold per app.
- **Permissions** (`ORG_PERMISSIONS`): contents write (the broker pushes a
  job's branch; a job's own token is `contents: read`), metadata read
  (mandatory), pull requests write (the landing opens one), issues write,
  actions read and checks read (the engineer reads CI). No
  `administration`: Loam never creates, deletes or configures a repository
  it does not own. No `workflows`: a job branch that changes
  `.github/workflows/*` is refused at the broker's push until the org grants
  it.
- **Its user authorization** is a pair of its own (a user token belongs to
  the app that issued it), checked and refreshed like the first, and must be
  the same GitHub user the first app is connected as.
- **States.** The org's are `register-org` and `install-org`, carrying the
  org. GitHub returns to the same two loam-ui paths and the page forwards the
  query unchanged, so the state alone says which app a return is for; a
  stateless `setup_action=update` goes to the app that recorded that
  installation. A broker on the code from before this refuses the org
  purposes rather than registering an org app over the first.
- **Health is per app.** `GET` answers `org_apps: [{org, status, app,
  installed, user, health}]` beside the unchanged fields; an org removing
  its app makes its own entry `unhealthy`, never the first app's card. The
  nightly check covers each.
- **Routes**: `manifest` and `install` take an optional `org`.

Until loam-ui draws an org app, its links are minted on the box with
`bin/github-app`: `org-manifest <org>` prints a `data:` URL of a one-button
form that posts the manifest to the org's create page (pasted into a desktop
browser's address bar; a page cannot link to a data: URL), `org-install
<org>` prints the install page, both with a state good for an hour;
`status`; `org-forget <org>`. The two minting commands refuse unless the
running broker answers with `org_apps`, since GitHub sends the browser back
through the broker. Tests: `tests/test_org_app.py`.

**A link for a person who clicks later (2026-11-15, Loam HQ's app).** A
`data:` URL is pasted, and the person whose clicks these are never pastes.
`org-manifest <org> --page DIR --for 48h` writes the same form as
`DIR/index.html`, posting itself to GitHub when opened (its button is there
if scripts are off), and `bin/site up <name> --root DIR` serves it behind the
login as a plain https link. `--for` gives the state a lifetime of its own,
kept on the state's entry as `ttl` (at most a week; an hour when absent):
GitHub's manifest code still lives an hour, but it is made at Create, so the
state that waits for the click can wait longer. The broker is what spends the
state, so a broker started before this refuses one past its hour.
`org-watch <org> --note PATH [--repo NAME] [--site NAME]`, run as a transient
user unit, waits for the org app's installation and authorization in the
store and writes a short note of what GitHub answers then: the app's slug,
the installation's account, repositories and permissions, and with `--repo`
that repository's visibility (read with the org app's own token) and its
collaborators, invitations and org members (read with the box's `gh`). With
`--site` the page goes down after. No token, key or secret is written.

### A project whose commits are a person's (2026-10-06)

On a repository shared with other people, a commit or a pull request has one
accountable author, and it is a person, not the engineer or an app. A project
record opts in with

    "commit_identity": {"name": "...", "email": "...", "github_login": "..."}

written by `bin/provision --project <name> --commit-as <login>` (or with
`--adopt`): the name from the GitHub profile unless `--commit-name`, the email
the account's noreply address `<id>+<login>@users.noreply.github.com` (id read
from the API) unless `--commit-email` gives one verified on the account. A
record without it is unchanged: the engineer commits as `<name> engineer
<engineer+<name>@noreply.local>` and a pull request is the installation's.
Everything is in `lib/loam_commit_identity.py`.

- **Commits.** A job's clone sets `user.name`/`user.email` to the person.
  The job's CLI gets attribution off in its `--settings` (Claude Code
  2.1.281: `attribution: {commit: "", pr: "", sessionUrl: false}`;
  `includeCoAuthoredBy` is deprecated but still read, so it is set false too).
  Caused 2026-10-06 with `claude -p` on haiku in a scratch repo: with the
  setting the commit was `Add a.txt` alone; without it the same prompt
  committed `Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>`.
- **The refusal** (`problems`): any commit on the branch whose author or
  committer is not the person, or whose message has a `Co-Authored-By`,
  `Claude-Session` or `Generated-By` trailer, "Generated with", Claude,
  Codex, Loam, or a job id, as whole words: "Claudette" passes and
  "loam-ui" does not. The tools' names, not our vocabulary: "engineer" and
  the personas' names were dropped on 2026-10-06, because they are plausible
  words in a product's own code and an honest commit refused costs more than
  they protect. It
  runs at landing (reason `identity`) and again in the broker right before
  the push, where nothing the job runs can reach. It does not depend on which
  CLI made the commit, so a project engineer on Codex is held to it too.
- **The pull request** is pushed and opened with the person's user-to-server
  token for the app that reaches the repository (`person_token`), and only
  after `GET /user` with that token answers `github_login`. No installation
  token is minted for it. The job's own installation token on such a
  repository is read-only on everything, so nothing the job does on GitHub
  appears as the app's bot.
- **Its text** is the job's own: `pull-request.md` in the job directory, a
  `# ` title line and plain prose for the repository's readers, plus the
  launcher's `Tested with `<command>`.` or `**Untested.**`. Never the
  receipt. If there is no such file the landing is refused (`pr_body`), and
  a title or body that names the machinery is refused too (`pr_text`).

**What GitHub returned**, caused on a throwaway private repo
(`octo-owner/loam-probe-identity-62067586`, id 1408079451, deleted
afterwards) through the first app:

- `GET /commits/<sha>`: `author.login: octo-owner`, `committer.login:
  octo-owner`, `commit.author` and `commit.committer` both `Owner Name
  <12345678+octo-owner@users.noreply.github.com>`, `verification.reason:
  unsigned`.
- `GET /pulls/1`: `user.login: octo-owner`, `user.type: User`.
- `GET /issues/1`: `performed_via_github_app: my-loam-app`. **The pull
  request is the user's, and GitHub records the app that made it**; that
  field is what an app mark beside the author would be drawn from. The
  rendered page was not seen: the repository was private and no browser
  here is signed in to GitHub. A personal access token should open one with
  no app recorded; that was not caused.
- A branch whose commit carried `Co-Authored-By: Claude Opus 5.5` was refused
  at landing and by the broker called directly, and `GET
  /branches/job/<id>` answered 404.

**The PR's head branch carries no job id** (2026-10-06). The broker still
pushes only the job's own `job/<id>` (`JOB_BRANCH_RE`), but on a
`commit_identity` project to a plain name made from the PR title
(`pr_head`: lowercase words joined by hyphens, about 48 characters), with
`-2`, `-3`... until no branch on the remote, no other job's recorded head and
not the default branch has it. So it never lands on a branch it did not make,
the default included, and refuses outright if the head ever equals the
default. The name is recorded in the canonical checkout's git config as
`branch.job/<id>.prhead`; a continuation reuses it and finds the same pull
request (`created: false`) even under a new title, and deleting the job
branch deletes the record with it. Other pull-request projects keep
`job/<id>` as the head. Tests: `tests/test_commit_identity.py`.

**A continuation reuses its head only while that is safe** (2026-10-21,
`settle_head`). Before the push the broker reads the recorded head on the
remote and its pull requests (`state=all`). The commit fast-forwards it and
no pull request from it was ever closed or merged: the same head, so an open
pull request is updated. A pull request from it is open and the commit does
not fast-forward it (the work was rebuilt on a newer default branch): refused
as `open_pr_diverged`, nothing pushed, because an open pull request is never
force-pushed and never silently forked. Otherwise (closed or merged, or no
pull request and rebuilt; closed or merged counts even when its branch
has since been deleted): a fresh head (`fresh_head`, the title's slug with
`-2`... on a `commit_identity` project, `job/<id>-2`... on another), recorded
in place of the old one, and a new pull request; the answer's `replaces`
names the old head, its pull request and why, and the receipt says so. The
old branch is left as it was. Caused by an adopted project's #46: closed, its
branch kept, and three landings of a rebuilt continuation rejected as
non-fast-forward. Tests: `tests/test_commit_identity.py`,
`tests/test_adopted_repo.py`.

**A job can update an open pull request instead of opening one**
(2026-10-09, `lib/loam_pr_target.py`). The field a brief uses is its
title: **a title that begins `Updates #<n>`** (`Updates #152: rebase onto
main`) names the pull request the job updates. The title, because it is the
one field every persona's tool already sends; a new tool parameter would
reach a conversation only after its MCP server restarted. Nothing in the
brief's body is read for it, and a title naming none lands exactly as above.

- **At brief time** (`accept`, and `preflight` for a hand-written brief) the
  title is refused unless the project lands by pull request and `#<n>` is
  open on its repository with its head branch on that same repository, read
  with the job's own read token from the `token` route. Refused before a job
  directory exists: *"Refused before starting: the title names pull request
  #8, which is closed on acme-co/backend; a job updates an open pull
  request or opens a new one. Name an open one, or drop `Updates #8` from
  the title to open a new one."*
- **The job** is told to start from the PR's head (`git fetch origin
  pull/<n>/head`, `git reset --hard FETCH_HEAD`), not to write
  `pull-request.md` (the PR keeps its title and description), and that a
  reply it writes to **`pull-request-reply.md`** in its job directory is
  posted on the PR. On a `commit_identity` project the reply is held to the
  same `text_problems` as a description.
- **At landing** the tests run on the branch as for any pull request, then
  `POST /v1/integrations/github/pull-request-update` `{project, branch,
  number, reply}` (`update_pull_request`). The broker reads the PR with the
  same token it pushes with (the person's on a `commit_identity` project,
  after `person_token`'s checks), refuses one that is not open or whose head
  is on another repository, fetches its head branch, and pushes the job's
  commit to it **fast-forward only**: if the head GitHub reports or the head
  fetched is not an ancestor of the job's commit it answers `{pushed: false,
  refused: "not_fast_forward", detail}` with nothing pushed and nothing
  posted, and the push itself is never forced, so the remote refuses a race
  too. No pull request is opened and the default branch is never a target.
  The landing's reason is `not_fast_forward`; its continuation says to merge
  `pull/<n>/head` and to title the brief `Updates #<n>` again.
- **The reply** is posted only after the push succeeded, as `POST
  /repos/<repo>/issues/<n>/comments` with that same token. GitHub refusing it
  (403 or 404) posts nothing another way: the answer's `reply` is `{state:
  "refused", detail, grant}`, `grant` naming the permission and the app and
  installation it is missing from (`Pull requests: Read and write`, or
  `Issues`, on `loam-for-acme-co` accepted by its installation on
  `acme-co`). Widening an app is the owner's click on GitHub.
- **The receipt** names the PR, its head branch, the head before and after,
  and the reply's URL or why there is none.

Why the job could not just reply itself (caused 2026-10-08, a 403 on
an adopted project's #152): the job's own token on a `commit_identity` repository is
cut to read on everything (`job_permissions`), by design, so nothing it
posts is the app's bot. The app and its installation both held
`pull_requests: write` and `issues: write` on 2026-10-09 (`GET /app` and
`GET /app/installations` with the app JWT). Tests:
`tests/test_pr_update.py`.

## Shared keys: Settings › Connections › API keys (2026-11-18)

API keys by provider, entered once, handed only to the plugins that
declared the provider in `keys:`. Every rule, the routes and their answers
are docs/plugins.md, "Keys"; the code is `lib/loam_keys.py`, the tests
`tests/test_keys.py`. `GET /v1/keys`, `GET|PUT|DELETE /v1/keys/<provider>`,
`POST /v1/keys/<provider>/check`, `PUT|DELETE /v1/keys/<provider>/plugins/<id>`.
Unlike the per-plugin routes below, a `by` is accepted and attributed: a
key is a setting, every setting is an agent's to change too (the owner,
2026-11-10), and no route answers a value. The `/value` route below resolves
a field of a declared provider the same way, for the app's own server.

## A plugin's keys, and daily notes (2026-11-14)

First release L9. The rules are `lib/loam_contributions.py` (`settings.secret`,
`vault.folder`) and `lib/loam_keys.py`; the tests are
`tests/test_first_party.py`. JSON, `Cache-Control: no-store`, errors
`{"error": "<a sentence>"}`. **A key is the owner's**: any of these routes
with a `by` is a 403.

### `GET /v1/plugins/<id>/secrets`

```json
{"id": "read-aloud",
 "secrets": [{"name": "GEMINI_API_KEY", "label": "Gemini API key", "checks": true,
              "set": true, "source": "plugin",
              "checked": {"at": "2026-11-15T04:10:00Z", "ok": true,
                          "why": "Google accepted the key.", "source": "plugin"},
              "refused": null}]}
```

`source` is `plugin` (pasted here), `box` (the install's own secrets file,
where the first box's keys were before this), or null (none). `checked` is
the last check of the key in force, or null; `refused` is the last paste
that failed its check (it kept nothing, so the key in force is unchanged),
or null, and a good paste clears it. A value is never in this answer. 404 for a plugin
nobody has.

### `PUT /v1/plugins/<id>/secrets/<name>` — paste a key

`{"value": "<the key>"}`. Trimmed; checked by the plugin's `check:` before
anything is kept. 200 `{"name", "set": true, "source": "plugin", "checked"}`.
**422 `{"error": "<the check's sentence>"}`** for a key that fails, and
nothing is kept. The host's Gemini check answers one of:

- 200: "Google accepted the key." (also when Google says the quota is used
  up: the key is good, and the sentence says so)
- 422: "Google says this is not a valid API key. Check that it was pasted whole."
- 422: "Google refused this key: <Google's reason>. It may need the
  Generative Language API turned on."
- 422: "Could not reach Google to check the key (<why>); try again."

Also 422 for an empty value, or one too long or with a quote or a control
character. 404 for a secret the plugin does not declare.

### `DELETE /v1/plugins/<id>/secrets/<name>` and `POST …/<name>/check`

DELETE forgets the pasted key; the answer's `source` says when the box's own
is still in force. POST `check` checks the key in force again, wherever it
comes from, and answers as PUT does (`checked.ok` false with the sentence,
status 200).

### `GET /v1/plugins/<id>/secrets/<name>/value` — the app's server only

`{"name", "value", "source"}`: for loam-ui's own server, which makes the
provider's call. **The page's relay must never pass this route.** 404 when
none is set.

### Daily notes

The plugin `daily-notes` (this runtime's `plugins/`, off by default). Its
switch is `PATCH /v1/plugins/daily-notes {"on": true|false}`; its folder is
`GET`/`PATCH /v1/plugins/daily-notes/settings` (`{"values": {"folder":
"Daily"}}`, a path relative to the vault, default `Daily`; a 400 while it is
off). Turning it on, and changing the folder, makes the folder if the vault
has none; nothing ever removes one. Today's note is `<folder>/YYYY-MM-DD.md`
in the person's own time zone, opened through Files (`root: vault`).

## An integration a plugin owns (2026-11-14)

The broker serves one integration of its own, GitHub (above). Any other is a
plugin's: its sign-in and status are the plugin's own server, at `/x/<id>/`
through the gateway (docs/plugins.md, "Server plugins"). Until loam-ui
`1c4ec9b` (live 2026-11-14 19:31 PT) the page's relay asked for
`/v1/integrations/<id>/...` and the broker forwarded it to the plugin's
server; the forward was retired the same evening (first release L11), and
`/v1/integrations/<id>` for any id but `github` is answered as any unknown
route is (405, `no route for GET /v1/integrations/<id>`, measured live).

## Schedules and Activity (2026-10-14)

What the page's Schedule tab and Activity screen call (loam-ui's
`design/schedules-and-activity.md`, step 2). The routes only dispatch to
`lib/loam_activity.py`, each call on a worker thread. Every write is
`lib/loam_schedule.change()` with actor `owner` and lane `app`, so it is
validated, logged in `state/schedules/events.jsonl` and reconciled exactly
as an agent's or an operator's change is; a refusal is that path's own
sentence as a 400 `{error}`, and a 404 `{error}` when the schedule, change
or job named is not there. A record has no key for tools: every lane has
the persona's whole tool list, the engineer included (2026-11-09).

| route | body | answers |
|---|---|---|
| `GET /v1/schedules[?agent=]` | | `{schedules: [row], unread}`, every persona's or one's |
| `GET /v1/schedules/<agent>` | | the same, for one persona |
| `POST /v1/schedules/<agent>` | `{name, schedule \| at+note, body?, enabled?, reason?}` | a new record (enabled unless `enabled: false`): `{event, schedule: row}` |
| `GET /v1/schedules/<agent>/<name>` | | one row (a fired one-off too) |
| `PATCH /v1/schedules/<agent>/<name>` | any of `{schedule, at, note, body, enabled, reason}` | `{event, schedule}`. Keys not sent stay byte for byte; `schedule` and `at` replace each other. Pause and resume are `enabled` |
| `DELETE /v1/schedules/<agent>/<name>` | `{reason?}` | `{event, schedule: null}`; the reflection's record is refused (pause it) |
| `POST /v1/schedules/<agent>/<name>/run` | | Run now: `systemctl --user start --no-block` of the lane's service, logged as a `run` by the owner. bin/wake's lock and enabled check apply as from a timer; a paused record or a fired one-off is refused here |
| `POST /v1/schedules/<agent>/<name>/seen` | | marks the record's unseen changes and refusals seen: `{marked, unread}` |
| `GET /v1/activity` | `?agent=a,b&kind=change,refused,fire,run,job&limit=50&offset=0` | `{items, total, next_offset, unread, jobs, jobs_error}` |
| `POST /v1/activity/seen` | `{ids: [...]}` or `{all: true}` | `{marked, unread}` |
| `POST /v1/activity/<event-id>/undo` | | the record put back as it was before that change, as a new change by the owner naming it (`undoes`): `{event, schedule}` |
| `POST /v1/jobs/<job-id>/cancel` | | the launcher's durable cancel, by `owner`: `{job, said}`. A queued job never starts. A running one is stopped (2026-11-10; until then it was only marked, and would still have landed): its session is signalled, its own launcher records `stage: cancelled` with how long it ran and what it had committed, lands nothing, keeps the branch and tree, and ends its unit; with no verdict in 60 s the unit is stopped and the verdict written by the cancel. A job already landing is not killed: the landing reads the mark after the live lock and again right before it moves `main` or pushes, and lands nothing, unless its merge was already past that check, which `said` reports. The recovery sweep never resumes a cancelled job and the status drops it at once. Answers within about a minute (the route runs in a thread). The same cancel is `engineer-job --cancel <id> --by <name>` and the engineer MCP server's `cancel` tool (`job_id`, `by`) |

**A row** is `agent, name, kind` (`recurring`, `one-off`, `reflection`),
`schedule` as the file says it and `words` for the page, `note`, `enabled`,
`done`, `body` (the reflection's instruction is `_reflection.md`, named in
`instruction`, and its body is not editable here), `timer`, `next_fire` (the
user manager's, ISO UTC; null when nothing runs; while a run is in flight,
when systemd 252 blanks the timer's own, the next elapse of its
`OnCalendar` by `systemd-analyze calendar`), `running_since` (added
2026-10-23: when the run of this record in flight now began, ISO UTC, from
the service's `InactiveExitTimestamp`; null when none is. `last_fire` is
written when a run ends, so this is what tells a run in flight from a
record that has not fired since the log began), `last_fire` (`id, ts,
outcome, stage`), `last_change` (an Activity `change` item with `seen`),
`unseen` (ids of the record's unseen changes and refusals: the row's
marker), `refused` (the latest event, when it is a refusal), `error` (a
record that does not parse, the parser's own message).

**The line** (added 2026-10-15; a schedule's detail and editing are its
file, opened in Files, and the row says what the file cannot):
`state` is the file's -- `live`, `off`, `broken` (does not parse),
`refused` (parses, and the path would not take it), `pending` (written
outside the path, not read yet; the reconciler reads it within a minute);
`running` is the box's -- `this` file, the `previous` version accepted
before it, or `none`; `state_words` says both in one sentence; `problem` is
`{why, line, text}` for `broken` and `refused` (`line` 1-based and `text`
the offending header line, null when the fault is in no one line); `enabled`
is what the file says (null when `enabled:` itself is unreadable);
`last_edit` is `{id, ts, actor, by, lane, taken}`, the file's last edit
whichever path made it (`taken: false` for a direct edit refused); `by` is
`owner`, `agent`, `operator` or `runtime` (also on Activity's `change` and
`refused` items); `changed_since_seen` is `unseen` as a bool; `file` is
`{root: "spaces", path}` for Files. A broken or refused file never stops a
schedule: the reconciler renders only what it accepted.

**A Files save of a record** (`PUT /v1/files/text` on
`spaces/<agent>/crons/<name>.md`, authority `schedule`) is the owner's: Files
logs a `claim` (the bytes' digest, actor `owner`, lane `app`) just before
its rename, reconciles the agent at once, and answers the row as
`schedule` beside the file's own fields. The reconciler takes a file a
claim names as that actor's change, so no `reason:` is asked of them (an
agent's direct edit still needs one). A save that does not parse is saved,
refused as the owner's, logged once, and the previous version keeps running.
Files stamps no provenance key on a record: a header carries only
`loam_cron.HEADER_KEYS`, and the log already names who saved it.

**Activity items**, newest first, each with `id`, `ts`, `kind`, `agent`,
`actor`, `seen`:
- `change`: `record, op, lane, reason, diff` (`{header: {key: [before,
  after]}, body_changed, created, deleted}`), `undoes` on an undo;
- `refused`: the same, with `why` and `direct` (an edit of the file itself);
- `fire`: `record, outcome` (`ran`, `failed`, `skipped`, `missed`,
  `killed`), `lane, started, finished, stage`, and `produced`: the wake's
  own line in `logs/wake-<agent>.jsonl` (its vault commit, cost, stage) and
  the failure log for a run that did not finish well. A wake has no
  transcript; transcripts are conversations';
- `run`: the owner's Run now;
- `job`: a finished engineer job (`job, project, title, ok, stage, outcome,
  landing, landed_sha, receipt, job_dir`), its id `job:<job-id>`.

`jobs` is the launcher's `box_status()` (what is running, landings, the queue
in order, and since 2026-11-10 `memory`, the box as admission reads it), the same function `engineer-job --status` and so
`mcp__engineer__status` print, called in process from `bin/engineer-job`
(reloaded when the file changes) so the two cannot disagree. `slots` keeps
its name, but since 2026-11-10 there is no count: it is one row per run going,
numbered, every row `free: false` (admission is by memory,
`lib/loam_lock.py`). A row held
by a wake or a cron is `kind: "wake"` with `agent`, `lane` (`reflection`,
`cron:<name>`), `name` (the lane without `cron:`), `label` (`Ada ·
reflection`, the name from the agent's `agent.json`) and `project` and
`briefed_by` null (added 2026-10-23); a job's slot keeps `job, project,
title, briefed_by`. A line for an
agent not on the roster is left out.

**Who, what and where (2026-11-12).** Every job row -- a running slot, a
landing, a queued job, and a finished `job` item -- carries the launcher's
`job_label`: `run_kind: "job"`, `engine` and `model` (and Codex's `effort`)
as the session was *launched*, read off its command line into the job's
`engine.json`; `briefed_by`; `caller`, the lane the brief came from
(`conversation`, `wake`, `cron`, `reviewer`, `job`); `caller_session`, the
broker session that called; `thread` and `thread_title`, the thread that
session is a member's turn in; `continues`, the job it continues (an
automatic continuation's parent or a `continue_job`), and `chain`, the
chain's first job, so a page folds a chain into one row; and
`auto_continuation` (0, 1, 2). Unknown is `null`: a job from before that
day has no `engine.json` (its `runtime` file still gives `engine`, with
`model` null) and no caller. A wake, cron or reviewer slot carries
`run_kind` (`wake`, `cron`, `reviewer`; `kind` stays `"wake"` for the
readers built on it) and `engine`/`model` from its own lock record. The
caller is found by `lib/loam_waiters.caller()`: the broker puts
`LOAM_SESSION_ID` in every CLI it starts, and the launcher reads it from
its own environment or an ancestor's.

**A receipt no waiting call carried.** The waiting half of a `brief` call
lives in the calling session's process tree, so a broker restart ends it
while the job runs on. Each call leaves `state/engineer-waiters/<job>.json`
and removes it once it hands a verdict back; every 30 s the broker looks for
a record whose waiter is gone and whose job has a `result.json`, posts one
plain Loam line into the caller's thread (`An engineer job Ada started
finished: <title> (landed).` -- no receipt body, id or path), and gives the
briefing persona a turn whose input ends with a note naming the receipt's
path, asking it to read the receipt and tell the owner in its own words
(`ThreadEngine.deliver_job`, `Turn.note`; `loam_waiters.delivery_text` and
`handoff_text`). Until 2026-11-12 20:30 PT the line pasted the receipt's
body; the owner said twice that day they do not want receipts in threads. A
turn the restart cut off posts no "<persona>'s turn ended: the broker
restarted" line while a waiting call's record still names its thread and
persona (`loam_waiters.pending_for`): the delivery line says what became of
it. The skip is logged in the journal. Logged in
`logs/engineer-deliveries.jsonl` and the journal (`deliver: …`).

**Seen** is `state/schedules/seen.json`: the ids marked and the moment the
store began, before which everything counts as seen. The owner's own changes
are born seen. A row's marker and its Activity item are one id, so marking
either clears both, and `unread` is the count over everything.

**Two attributions the log makes** (closing step 1's gaps): a run killed by
SIGTERM, SIGKILL or a unit timeout has its `fire` line, outcome `killed`,
written by `bin/wake-failed` from the marker the run left; a run cut by its
lane's wall clock is `killed` from bin/wake itself. An agent's direct edit
of its own record is lane `conversation` when no wake of it is running and
one of its chats is mid-turn, or spoke within three minutes, by this
broker's own session records (`state/broker/sessions/*.json`).

Tests: `tests/test_broker_schedules.py`, through `Server.route` in process.

## Settings from a persona (2026-11-10)

Every setting the page can change, a persona can change through the same
route, with `by` naming it: the registry is `lib/loam_settings.py`, the
command `bin/settings`, the tool `mcp__engineer__settings`, and the route
`GET /v1/settings` answers the table. The inventory, the reach of each
setting and what `by` does on each route are in [settings.md](settings.md).

Appearance (2026-11-11): `GET`/`PATCH /v1/settings/appearance`, a mode,
a size and (2026-11-12) a theme for the phone and the desktop, kept in
`state/broker/settings.json` by `lib/loam_appearance.py`; the theme ids are
the theme plugins of the plugin host's list (`lib/loam_plugins.py`), not
listed here; and (2026-11-12) each theme's option choices, per kind, from
the themes' manifests. See settings.md, "Appearance".

Plugins (2026-11-12): `GET`/`POST /v1/plugins` (the list; install from a
GitHub link, a preview and then a confirm), `GET`/`PATCH`/`DELETE
/v1/plugins/<id>` (one; `{"on"}`; remove from Yours), and `GET
/v1/plugins/<id>/files/<path>` (a plugin's `page/`, `assets/`, `theme.css`
and `PLUGIN.md`, for the page's loader). `lib/loam_plugins.py` holds every
rule; the interface, the two layers and the page's contract are
[../docs/plugins.md](../docs/plugins.md). Tests: `tests/test_plugins.py`.
Since 2026-11-13 a row with a server carries `service` (its state, port,
address `/x/<id>/`, process, restarts, and its log when failing), and `POST
/v1/plugins/<id>/restart` starts that one server fresh
(`lib/loam_plugin_servers.py`; tests `tests/test_plugin_servers.py`).

## Agent profiles, engineers and the reviewer (2026-11-13)

Settings > Agents: an agent's profile, the engineers, the nightly reviewer, and
a plugin's per-agent settings. The rules are `lib/loam_profile.py` (and
`lib/loam_plugins.py` for plugins); the tests are `tests/test_profile.py`.
Every route takes and answers JSON, `Cache-Control: no-store`. Every write
takes an optional `by` (a persona's name, as on every other write: absent or
`owner` is the owner). A persona's write, made through `bin/settings` /
`mcp__engineer__settings`, is said as one system line in General ("Ada
changed Iris's model to sonnet"); the owner's clicks say nothing. Every error is
`{"error": "<a sentence>"}` with the status given. The page's relay does not
pass these routes yet (`PAGE_RELAY_AHEAD` in `lib/loam_settings.py`); the
relay should spell them `/^\/agents\/([^/]+)$/`, `rest === '/engineers'`,
`/^\/engineers\/([^/]+)$/`, `rest === '/system/reviewer'` and
`/^\/plugins\/([^/]+)\/agents(?:\/([^/]+))?$/`, passing `by` through.

### `GET /v1/agents/<agent>` — the profile

`<agent>` is the id (`ada`). 404 for an agent nobody has.

```json
{
  "agent": {"id": "ada", "name": "Ada", "face": null},
  "identity": {"id": "ada", "display": "Ada",
               "face": null, "archived": null,
               "default_display": "Ada",
               "rename": "display name only: ..."},
  "defaults": {
    "provider": null, "model": null, "effort": null,
    "effective": {"provider": "claude-code", "model": "fable", "effort": null},
    "threads":   {"provider": "claude-code", "model": "fable", "effort": null},
    "wakes":     {"provider": "claude-code", "model": "fable", "effort": null},
    "applies": "New threads and members brought in start on the provider. ...",
    "not_honoured": []
  },
  "files": {
    "workspace": {"root": "spaces", "path": "ada", "exists": true},
    "soul":   {"root": "spaces", "path": "ada/soul.md", "exists": true},
    "config": {"root": "spaces", "path": "ada/config.md", "exists": true},
    "memory": {"always":     {"root": "spaces", "path": "ada/_always.md", "exists": true},
               "active":     {"root": "spaces", "path": "ada/_active.md", "exists": true},
               "reflection": {"root": "spaces", "path": "ada/_reflection.md", "exists": true},
               "folder":     {"root": "spaces", "path": "ada/memory", "exists": true}},
    "skills": {"root": "spaces", "path": "ada/skills", "exists": true,
               "count": 4, "names": ["essay-writing", "canvas-plans", "..."]}
  },
  "schedule": [
    {"agent": "ada", "name": "reflection", "kind": "reflection",
     "label": "Nightly wake", "plain": "Every day at 3:20 AM",
     "schedule": "daily at 03:20 PT", "enabled": true,
     "next_fire": "2026-11-14T11:20:00Z",
     "last_fire": {"id": "...", "ts": "...", "outcome": "ran", "stage": null},
     "running_since": null, "state": "live", "file": {"root": "spaces", "path": "ada/crons/reflection.md"},
     "...": "every other field GET /v1/schedules/<agent> answers"},
    {"name": "infra-watch", "label": "Infra watch", "plain": "Wednesdays at 5:00 AM", "...": "..."}
  ],
  "status": {
    "threads": [{"thread": "t-6daf5a6f", "title": "Loam: ...", "since": "2026-11-13T20:01:00Z"}],
    "jobs":    [{"job": "...", "title": "...", "project": "Loam", "thread": "t-...",
                 "state": "running", "phase": null, "since": "..."}],
    "queued":  [],
    "run": null,
    "next_wake": "2026-11-14T11:20:00Z",
    "box_read": true
  },
  "plugins": [
    {"id": "read-aloud", "name": "Read aloud", "on": true,
     "places": ["...", "agent.profile"],
     "fields": [{"id": "voice", "label": "Voice", "choices": [{"id": "Kore", "label": "Kore"}], "default": "Kore"}],
     "values": {"voice": "Kore"}}
  ]
}
```

- `identity.display` and `.face` are what every card and line shows (no
  face: the theme's). `emoji` and `default_emoji` were retired 2026-11-16.
  Renaming changes the display name only; the id never changes (it is the
  workspace, the units and every old `@`), and an `@` matches the id and the
  display name.
- `defaults.provider|model|effort` are as set (null: not set).
  `effective` is what applies: provider `claude-code` when none is set, model
  the persona's own (`.claude/settings.json`, `fable`) for Claude Code when
  none is set, null otherwise (the provider's own). `threads` is what a thread
  turn uses, `wakes` what a wake or cron runs on: the persona's own provider
  when it is Claude Code or Codex (since 2026-11-13; on Codex a null `model`
  is Codex's own default), else Claude Code. `not_honoured` is a list of
  sentences, empty when everything set is honoured; today the one case is
  Antigravity, whose wakes and crons still run on Claude Code because no
  Antigravity turn has ever run on the box (no sign-in), so a lane could not
  be built against what it really sends.
- `schedule` rows are exactly `GET /v1/schedules/<agent>`'s, the wake first,
  plus `label` and `plain` (Pacific, 12-hour). Change one with the existing
  `PATCH /v1/schedules/<agent>/<name>` (`enabled`, `schedule`; a persona must
  give a `reason`), unchanged: one run of a persona at a time still holds.
- `status`: the agent's turns in flight by thread; the engineer jobs it
  briefed, from `loam_activity.job_rows()` (`state` `running`, `landing` or
  `queued`); `run`, a wake or cron of its running now (`{lane, name, since}`)
  or null; `next_wake`, the wake row's `next_fire`. `box_read: false` means the
  job reading failed and `jobs` is unknown, not empty.
- `plugins`: every plugin in force that names the place `agent.profile` or
  declares `agent_settings`, with this agent's values (each declared field at
  its stored value or its default). A plugin that is off is listed with
  `on: false`; the page leaves it out.

### `PATCH /v1/agents/<agent>` — identity and defaults

Body: any of `display`, `provider`, `model`, `effort` (and `by`). `emoji`
is refused as a field the profile does not have (retired 2026-11-16).
Answers 200 with the whole profile, as GET.

| Field | Takes | null |
|---|---|---|
| `display` | 1–40 characters (spaces collapsed, no control characters), not another agent's id or display name, not `owner`, `all`, `everyone`, `system` | the id's own (`Ada`) |
| `provider` | `claude-code`, `codex`, `antigravity` | Claude Code |
| `model` | a model the (new) provider takes: Claude Code's aliases or a `claude-…` id, a model Codex's `model/list` gives, one Antigravity lists | the provider's own |
| `effort` | Claude Code: `low` `medium` `high` `xhigh` `max`; Codex: its listed levels; Antigravity: the same five, passed only beside a model id that does not end in its own level (`-low` ... `-xhigh`; the id wins, since 2026-11-15) | the provider's own |

A provider that changes clears `model` and `effort` unless the same body
names them. Errors, each writing nothing: 400 for a field there is none of,
an empty body, or a value above not taken (the sentence says which); 409 for
a display name that is another agent's; 404 for an agent nobody has.

Where they apply: a thread member starts on the agent's provider when it
joins (new threads, a member brought in; members already in a thread keep
their engine); every turn on that provider for which the thread chose no
model runs on the agent's model and thinking level, from the next turn; a
thread's own model still wins. Wakes and crons run on the provider too,
Claude Code or Codex, on its model and level (bin/wake; the Codex half is
`lib/loam_codex_wake.py`, and every wake log line names its `provider`); an
Antigravity persona's run on Claude Code on `fable` and log
`provider_not_honoured`.

### `GET /v1/engineers` — every engineer and the default

```json
{
  "engineers": [
    {"project": "Loam", "platform": true, "repo": null,
     "own": null,
     "effective": {"provider": "claude-code", "model": "claude-opus-5-5", "effort": null, "source": "record"},
     "providers": ["claude-code"],
     "deploy_on_land": null, "deploy_app": null, "lands_by": "main",
     "instructions": {"root": "vault", "path": "40_PROJECTS/Loam/engineer/projects/loam.md",
                      "system": {"root": "system", "path": "Engineer/Platform project.md"},
                      "host_path": "/home/loam/agents/engineer/project.md"},
     "jobs_running": 1},
    {"project": "loam-ui", "platform": false, "repo": "octo-owner/loam-ui",
     "own": {"provider": "codex", "model": null, "effort": "high"},
     "effective": {"provider": "codex", "model": "gpt-6.1-sol", "effort": "high", "source": "own"},
     "providers": ["claude-code", "codex"],
     "deploy_on_land": false, "deploy_app": "loam-ui", "lands_by": "main",
     "instructions": {"root": null, "path": null,
                      "host_path": "/home/loam/projects/loam-ui/project.md",
                      "note": "in the project's own repository, which its engineer edits; Files does not reach it"},
     "jobs_running": 0}
  ],
  "default": {"own": null,
              "effective": {"provider": "codex", "model": "gpt-6.1-sol", "effort": "high", "source": "record"},
              "providers": ["claude-code", "codex"],
              "note": "Every project engineer without its own setting. ..."},
  "applies": "From the next job; a job already running finishes on what it started with."
}
```

Loam's engineer is first, then the projects by name. `own` is the engineer's
own setting (null: it follows the default, or its record); `effective` is
what its next job runs on, `source` `own`, `default` or `record`.
`deploy_on_land` is null where it cannot apply (Loam's engineer, a project
landing by pull request, a project with no app). `GET /v1/engineers/<project
or default>` answers one row (the default as `{"project": "default", own,
effective, providers, note, applies}`); 404 for an engineer nobody has.

### `PATCH /v1/engineers/<project or default>`

Body: any of `provider`, `model`, `effort`, `tiers`, `deploy_on_land`.
Answers 200 with the whole `GET /v1/engineers`.

- `provider`: `claude-code`, `codex` or `antigravity` (Loam's: `claude-code`
  only; 400 otherwise). Antigravity since 2026-11-15, as a choice no engineer
  is on until the owner moves one (`lib/loam_agy_job.py`). null clears the
  engineer's own setting: it follows the default again (or its record). A
  change clears `model` and `effort` unless named.
- `model`, `effort`: as the provider takes them (the same checks as an
  agent's); null is the provider's built-in (Claude Code `claude-opus-5-5`
  and the CLI's level; Codex `gpt-6.1-sol`, `high`; Antigravity
  `gemini-4-hydrogen-high`). Set without a provider of its own, the
  engineer's current provider becomes its own. **Antigravity's level is in
  its model id** (`gemini-4-hydrogen-high`, `-xhigh`): `agy` refuses an
  `--effort` that disagrees with the id, so an Antigravity engineer's
  `effort` is a 400 and the level is chosen by choosing the id. The ids are
  `agy models`' (the provider row, cached a minute; the last list is also
  kept in `state/broker/antigravity/models.json` for `bin/check-drift`).
- `tiers` (2026-11-15): an object of any of `quick`, `standard`, `hard`,
  each a model the provider lists (the same check as `model`; on
  Antigravity the id carries its level), or null to unset that one; the
  tiers not named are kept. A brief names a tier (`mcp__engineer__brief`'s
  `tier`, `standard` when left out), never a model; a tier unset runs on
  `model` (or the built-in), so an engineer nobody set runs as before. A
  provider change drops them. Rows carry `own.tiers` (`{quick, standard,
  hard}`, null where unset) and `effective.tiers` (`{tier: {"model",
  "from": "tier" | "model"}}`, what a brief at each runs on).
- `deploy_on_land`: true or false, a project with an app only (400 for
  Loam, `default`, a project landing by pull request; 409 for a project with
  no app). A brief's own `deploy` still wins; a brief that leaves it out
  takes this.

The launcher reads all of it on every job (`lib/loam_engineer.py`,
`engineer_choice`, `tier_choice`); a running job is unchanged. A
continuation that names no tier keeps the model its parent ran on
(`engine.json`). An Antigravity job out of quota (`lib/loam_agy_job.py`
`QUOTA_RE`) runs once more on the tier below, never past `quick` nor onto
the same model; the receipt's `model` row says the tier, the model and any
retry.

### `GET /v1/system/reviewer` and `PATCH /v1/system/reviewer`

```json
{"id": "reviewer", "name": "Nightly reviewer",
 "description": "Reads one agent's workspace a night ...",
 "schedule": "daily at 04:20 PT", "plain": "Every day at 4:20 AM",
 "default_schedule": "daily at 04:20 PT",
 "next_fire": "2026-11-14T12:20:00Z", "last_fire": "2026-11-13T12:20:04Z",
 "running_since": null,
 "provider": "claude-code", "providers": ["claude-code", "antigravity"],
 "model": null, "effort": null,
 "effective": {"provider": "claude-code", "model": "fable", "effort": null},
 "instructions": {"root": "system", "path": "Reviewer/CLAUDE.md"},
 "applies": "From the next run."}
```

PATCH takes any of `schedule` (the schedules' grammar, `daily at 04:20 PT`,
`Mondays and Thursdays at 05:00 PT`, `weekdays at …`; null for 04:20),
`provider` (`claude-code` or, since 2026-11-15, `antigravity`; null for
`claude-code`), `model` (one its provider lists; null for `fable` on Claude
Code, `gemini-4-hydrogen-high` on Antigravity), `effort` (Claude Code's; on
Antigravity the level is part of the model id, so `effort` is a 400). A
change of provider drops the other provider's model and effort unless the
same PATCH names them. `providers` lists `["claude-code", "antigravity"]`;
`provider` is the stored choice, `effective` what the next run uses. How a
run on Antigravity is held to the same reach is `bin/review`'s docstring.
Answers 200 with the GET. 400, writing nothing, for a schedule that does not
parse, another provider, or a value not taken. The schedule is applied at once as a systemd drop-in beside the
tracked timer and a reload (the timer moves, nothing fires).

### `GET /v1/plugins/<id>/agents[/<agent>]` and `PATCH /v1/plugins/<id>/agents/<agent>`

A plugin's settings for each agent. GET of one agent:

```json
{"plugin": "read-aloud", "name": "Read aloud", "on": true,
 "fields": [{"id": "voice", "label": "Voice",
             "choices": [{"id": "Kore", "label": "Kore"}, {"id": "Puck", "label": "Puck"}],
             "default": "Kore"}],
 "agent": "ada", "values": {"voice": "Puck"}, "stored": {"voice": "Puck"}}
```

Without `<agent>`, `"agents": {"ada": {...values}, ...}` for every agent
with something stored. PATCH body `{"values": {"voice": "Puck"}}`, merged key
by key, null removing a key (back to its default); answers the one-agent
GET. 400 for a field the plugin does not declare (when it declares any), a
choice it does not offer, text over 200 characters, or a body that is not
`{"values": {...}}`; 404 for an unknown plugin or agent; 409 for a broken one.
A plugin that declares no fields keeps any slug key with text, a whole number
or true/false. Stored in Yours, `_settings/<id>/agents.json`.

A plugin declares its fields in `PLUGIN.md` (`docs/plugins.md`):

```yaml
places: [..., agent.profile]
agent_settings:
  - id: voice
    label: Voice
    choices: [{id: Kore, label: Kore}, {id: Puck, label: Puck}]
    default: Kore
```

At most 16 fields; an `id` slug of at most 48 characters, a `label` of at most
80, optionally `choices` (1–100 `{id, label}`, ids any text of at most 48)
and a `default`. A field without choices is text. `agent.profile` is the
place on the profile a plugin's page code fills with a row or a section.
First-party plugins get nothing more than this.

### `bin/settings` / `mcp__engineer__settings`

`agent.display`, `agent.provider`, `agent.model`,
`agent.effort` (target the agent); `engineer.provider`, `engineer.model`,
`engineer.effort`, `engineer.deploy_on_land`, `engineer.tiers.quick`,
`engineer.tiers.standard`, `engineer.tiers.hard` (target a project or
`default`; a tier reads back `{"model", "from"}`); `reviewer.schedule`, `reviewer.provider`, `reviewer.model`,
`reviewer.effort`; `plugin.agent` (target `<plugin>/<agent>`, value a JSON
object). All conversation-only: a wake or cron is refused before any call.
`persona.provider` is retired into `agent.provider`.

## Making, archiving and restoring agents (2026-11-14)

Settings › Agents › *New agent* and *Archived*, an agent's *Archive* menu
item, and its uploaded face (first release, decisions 6 and 7). The rules are
`lib/loam_agents.py`; the tests are `tests/test_agents.py`. JSON in and out,
`Cache-Control: no-store`, errors `{"error": "<a sentence>"}` (a 409 on a
draft also carries `"draft"`, the draft as it stands). Every write takes an
optional `by`, as elsewhere. **No route deletes an agent**: `DELETE
/v1/agents/<agent>` is a 405.

**Whose yes.** A call with no `by` is the person's own click, and is the yes.
A call with `by` (an agent, through `bin/settings` / `mcp__engineer__settings`)
must carry the person's yes: `thread` (the thread the agent is talking in)
and `yes` (the id of the person's message there, `m12`). The route reads the
thread's record and refuses (403/404/409) unless the agent is a member of
that thread, the message is the owner's own `message`, it was written at or
after the draft's current revision (confirm) or within 30 minutes (archive,
restore), and it has not been taken for another change. Nothing an agent
posts is recorded under the owner's name, so the check holds against a
mistake and an unattended run; it is not a wall against a process with the
broker's token. The settings entries are conversation-only: a wake or a cron
is refused before the call.

### `POST /v1/agents/new` — New agent

Opens a thread with the starter agent (`agents.starter` in loam.toml, default
`iris`), titled "New agent", with one Loam line saying what happens and
naming the thread. 201, the thread's summary (as `POST /v1/threads`). 409
when the starter agent is not awake. The person writes first.

### Drafts

- `POST /v1/agents/drafts` — `{"thread", "name", "soul", "config"}`
  and optionally `"id"` (the folder name; from the name when absent),
  `"provider"` (`claude-code`, `codex`, `antigravity`), `"wake"` (`HH:MM`; a
  free slot when absent), `"active"` and `"reflection"` (the loading files;
  plain structural starters when absent). `soul`, `config`, `active` and
  `reflection` may instead be `<field>_file`, a path in a workspace or the
  vault, read at the call. `thread` is required of an agent. 201:

  ```json
  {"draft": "d-3f9c2a1b", "rev": 1, "by": "iris", "thread": "t-6daf5a6f",
   "id": "juniper", "name": "Juniper",
   "soul": "...", "config": "...", "wake": null, "provider": null,
   "created": "2026-11-14T23:10:00Z", "updated": "2026-11-14T23:10:00Z",
   "revisions": [{"rev": 1, "at": "...", "by": "iris", "fields": ["config", "name", "soul", "thread"]}],
   "problems": [], "ready": true,
   "fleet_clauses": ["Namespace boundaries", "Deletion judgment", "..."]}
  ```

  `problems` says what stands between the draft and an agent (an id or name
  taken by any agent, archived ones included; a reserved id; no soul or
  config); an `emoji` field is refused, 400 (retired 2026-11-16: the face
  is the theme's until an image is uploaded); `ready` is true when there are none. `fleet_clauses` are
  the clauses every agent's config carries, which the made `config.md`
  starts with (below). The thread gets a Loam line: "Iris drafted a new
  agent: Juniper (draft d-3f9c2a1b, revision 1)". How to draft one (a
  character, never a job) is the library skill `loam-making-an-agent`.
- `GET /v1/agents/drafts[?thread=<id>]` — `{"drafts": [...]}`, open drafts
  only.
- `GET /v1/agents/drafts/<draft>` — one draft, made or not.
- `PATCH /v1/agents/drafts/<draft>` — any of the fields above; the revision
  goes up by one and the thread gets a line naming the fields. 409 on a made
  or discarded draft.
- `DELETE /v1/agents/drafts/<draft>` — set the draft aside (a draft, not an
  agent).

### `POST /v1/agents/drafts/<draft>/confirm` — make it

`{"rev": <the revision the person said yes to>}`, and from an agent
`"yes": "m12"` (`thread` defaults to the draft's). 409 for another revision,
a draft with problems, or one already made. 201:

```json
{"agent": {"id": "juniper", "name": "Juniper", "face": null},
 "first_thread": "t-1a2b3c4d", "workspace": "/home/…/ai-labs/juniper",
 "wake": "01:20", "fleet_clauses": ["..."],
 "draft": {"...": "the draft, with made, made_at and yes"}}
```

Made: the workspace with `soul.md` (verbatim), `config.md` (the fleet's
shared clauses, copied from the configs on the box with the new agent's
name, then the draft's own text under "Its own rules" and its own `##`
clauses; a clause of the draft that has a fleet clause's title gives way to
the fleet's), `_active.md` and `_reflection.md`, `memory/`, `skills/`,
`crons/`, `threads/`; `agent.json` with the name and face; then
`bin/provision <id> <wake>` (Claude Code, Codex and Antigravity files, the
reflection record and its timer). Provision holds Loam's live lock, so a confirm made while a landing
of Loam is in progress waits for it (up to 20 minutes) before it answers. It
is on the roster at once: `GET /v1/agents`, every picker, `@` mentions,
General's members. Its first thread has it as the default and a line saying
who made it on whose yes; the drafting thread gets "Made Juniper (juniper):
their first thread is t-…". If provisioning fails, the half-made agent's
`agent.json` is set aside as `agent.json.failed` (off the roster) and the
answer is a 500 that says so.

### `POST /v1/agents/<agent>/archive` and `/restore`

Body `{}` from the page; `{"thread", "yes"}` from an agent. Archive answers
`{"agent", "archived": {"at", "by", "yes"?}, "event"}`; 409 when
already archived, when it is the last agent awake, or (unless an agent
archives itself) while it is mid-turn or in a wake or cron. Restore answers
`{"agent", "event"}`; 409 when not archived, or when its name has since been
given to another agent.

Archived means `archived` in its `agent.json`: out of `GET /v1/agents`,
every picker, `@` mentions, thread membership, `by`, the reviewer's choice,
the Schedules list and new sessions; the schedule reconciler removes its
wake and cron timers (its records and their accepted copies are untouched),
and `bin/wake` refuses it. A thread
whose default or member it is stays readable; a turn that would be its
gets the line "Juniper is archived, so it won't answer. Tag another agent,
or restore Juniper in Settings › Agents › Archived." Its profile
(`GET /v1/agents/<agent>`, `identity.archived`) and its files (Files'
`spaces` root) stay. Restore removes the mark; the reconciler puts back the
same timers from the same records, armed without firing.

`GET /v1/agents?archived=1` — Settings › Agents › Archived:
`{"agents": [{"id", "name", "face", "archived": {"at", "by", ...}}]}`.

### `/v1/agents/<agent>/face` — an uploaded face

- `PUT`, `multipart/form-data` with one `file` part (the page), or JSON
  `{"path": "<a file in a workspace or the vault>"}` (an agent, through
  `agent.face`); `{"path": null}` removes it. PNG, JPEG, WebP or GIF by
  their bytes (SVG is refused, 415), 2 MB at most (413). Answers
  `{"agent", "face": {"type", "sha", "bytes", "at", "url"}}`.
- `GET` — the image, with its own `Content-Type`, cacheable forever: the URL
  carries the digest (`/v1/agents/iris/face?v=<12 hex>`), so a new face is a
  new URL.
- `DELETE` — back to the theme's face.

Kept in the agent's workspace as `face.<ext>` and named in `agent.json`
(`face`). Every card (`GET /v1/agents`, a thread's agents) and the profile's
`identity` carry `face` (null without one); the page draws it over any theme's
face. Provision keeps `face` and `archived` when it
renders `agent.json` again.

### Rename

Unchanged: `PATCH /v1/agents/<agent> {"display": "..."}` (above). The id
never changes. An agent renames itself, or changes its face, with
`agent.display` and `agent.face`.

### `bin/settings` / `mcp__engineer__settings`

`agent.draft` (value: the draft's JSON), `agent.revise` and `agent.confirm`
(target the draft id), `agent.archive` and `agent.restore` (target the
agent, value `{"thread", "yes"}`), `agent.face` (target the agent, value a
path or null). `settings describe agent.draft` carries the instruction an
agent follows to make one. The page's relay should spell the routes
`rest === '/agents/new'`, `rest === '/agents/drafts'`,
`/^\/agents\/drafts\/([^/]+)$/`, `/^\/agents\/drafts\/([^/]+)\/confirm$/`,
`/^\/agents\/([^/]+)\/(archive|restore)$/` and `/^\/agents\/([^/]+)\/face$/`
(`PAGE_RELAY_AHEAD`), with `?archived=1` passed on `rest === '/agents'`, and
the face's multipart body passed as bytes.

## Updates (2026-11-14)

`GET /v1/update` reads the update state. `POST /v1/update/check` checks for
a newer release. `POST /v1/update/apply` and `POST /v1/update/rollback`
start a run as its own transient unit, answering 202, or 409 with the
refusal. `GET /v1/update/progress` reads the run. These serve Settings ›
Update. Every rule is `lib/loam_update.py`. The page contract and the rules
(what an update replaces, the clean-tree rule, System/Loam's diff, rollback)
are `docs/update.md`.

The delivery pass that carries owed receipts also carries the updater's
notices (`<state>/update/notices/`). Each notice becomes one Loam line in
General and a turn for General's default agent, whose input names the
release notes to tell the owner about in its own words
(`ThreadEngine.deliver_note`).

## Running it

```
systemctl --user enable --now loam-session-broker.service   # the broker
sudo systemctl enable --now loam-broker-firewall.service    # the wall
python3 bin/broker-probe                                    # the proof
```

The probe starts a real session as the lead agent (`--agent` names
another), checks identity the way the wake preflight does, answers a real permission prompt, drops its own connection
mid-turn, reconnects and catches up, ends the process, resumes by id and asks
a question only a session carrying the earlier turns can answer. It costs
about $0.80 of quota and files a transcript under the vault's
`15_CONVERSATIONS/`, because it is a conversation.

**A stop that hangs writes what it is waiting on** (2026-10-13). A clean
stop takes a second or two. If the broker is still here
`SHUTDOWN_DUMP_S` (20 s, `lib/loam_broker.py`) after SIGTERM, it writes to
stderr, and so to the journal, a block of lines that all start with
`shutdown:`: one per live session with its state and status, every thread's
stack by name, and every asyncio task still pending. Then it goes on
stopping as it was, so systemd still SIGKILLs it at 90 s. Read it with
`journalctl --user -u loam-session-broker | grep -A400 'shutdown: SIG'`.
Two shapes to look for: a thread `(_do_shutdown)` joining an `asyncio_N`
worker means `asyncio.run` is waiting on a `to_thread` call that never
returned, and the worker's own stack names that call. A task in the list
marked `cancelling` is one that would not end when cancelled. If no Python
thread can run, `faulthandler` writes unnamed stacks at
`SHUTDOWN_FALLBACK_S` (40 s) instead. `tests/test_broker_shutdown_dump.py`
causes the first shape and reads the dump back.

**The stop ends itself, inside systemd's 90 s** (2026-11-10). The 11-10 11:11
stop was SIGKILLed at 90 s; its dump showed no live session and one task,
`ThreadEngine._work`, cancelled inside `create_subprocess_exec` while starting
a session it had begun *after* SIGTERM, and `asyncio.run`'s cancel of it never
returned. Now the first SIGTERM sets `Broker.stopping`: `start_session`
answers 503 and a queued thread turn does not start (its input stays on
disk for the next start since 2026-11-19; until then it wrote "Ada's turn
didn't start: the broker restarted"). `shutdown()` then stops the live
sessions together (`SHUTDOWN_SESSIONS_S`, 15 s), gives each thread's turn
`SHUTDOWN_TASKS_S` (10 s) to write its last line, cancels every other task
and waits `SHUTDOWN_TASKS_S` for them; a task that will not end is named
(`shutdown: task … did not end when cancelled`) and the broker exits without
it. Past `SHUTDOWN_EXIT_S` (60 s) a backstop thread names any task and worker
thread still there (`shutdown: left: …`) and exits. Every record is written
whole as it goes, so an exit here loses nothing of ours. A stop that needs
none of this still writes no `shutdown:` line at all.

## How the app is wired to it

Done 2026-09-16, the job after this one, and it is two places in
`deploy/loam-ui/compose.yaml` and one function in `bin/deploy`:

- the service joins `[default, broker]`, with `broker` a top-level
  `external: true` network named `loam-broker` — the runtime owns it, compose
  only joins it. Naming any network means naming `default` too, or the
  service silently leaves it and the proxy loses its route to the container;
- `BROKER_URL` and `BROKER_TOKEN` in the service's `environment:`, both
  `${...:?}` with no default and neither a literal;
- `bin/deploy`'s `broker_env()` supplies both to every `docker compose` it
  runs: the URL from `lib/loam_broker`'s constants, the token read then and
  there from `state/broker/token`. Never rendered to a file, never printed.
  It does not call `read_token()`, which would CREATE the token: `bin/deploy`
  is root, and a root-owned 0600 token is one the broker, running as
  the box's user, could not read. The broker mints its own on first start.

Caused from inside the running container, 2026-09-16: `/up` answers without
a token and `/v1/agents` answers with it. The recipe is in
`deploy/README.md`, "How an app reaches the session broker, and gets its
token", for the next app that needs one.

## Threads (2026-11-08)

A thread is a vault record, `15_CONVERSATIONS/<title>.md`, written only by
the runtime (`lib/loam_threads.py` holds its format). A harness session is
the engine one persona used for one turn (`lib/loam_thread_engine.py`): a
turn goes through `start_session` like any chat, resumes that persona's
session for the thread while its engine is unchanged and its context is
small, and otherwise starts fresh with a small window of the record (see
"What a persona carries into a turn" below). Those sessions are archived records, so Classic
chats (every `/v1/sessions` route, unchanged) lists what it listed before.
Runtime-only caches are in `state/threads/` (id → file, sessions per persona,
the read mark); the vault files are the truth.

**Members answer in parallel.** (2026-11-10; the owner, 14:06 PT, after a
message to Iris waited for Ada's turn to finish: "While Ada is working, I
can also send a message to Iris and have my conversation continue with Iris
while Ada is still working.") Each member of a thread has its own queue of
turns, keyed by (thread, persona) in `ThreadEngine`: a turn for one member
never waits on another member's. A second message to a member already
mid-turn is steered into that turn (below, 2026-11-14) where its engine
takes input mid-turn, and otherwise waits behind that member's own turn and
becomes its next turn. (Until 2026-11-14 it always waited, because only
Claude Code was known to take input mid-turn and a reply that answered two
messages would not say which; Codex 0.169 has `turn/steer`, and a reply now
carries `steered`, the ids it took in.) Mentions in one message and `@all` start their turns at
once, each on its own queue. Admission is unchanged: each turn asks for its
seat by memory, as before. Every turn reads the thread as it stands when its
session is open (the record is read again after the seat is had); a member
whose turn started before another member's reply landed does not see that
reply in that turn, and its next turn is told it: a member's `through` in
`state/threads/sessions.json` is now the last message id its turn was shown,
never its own reply's id (its own messages are never news to it). The
writer is safe under interleaving: an append to the record and an entry in
the full record are each one synchronous call under a file lock
(`threads.locked`, `flock` on the JSONL), so replies whose `result`s arrive
together all land, in the order their `result`s were heard, with distinct
ids. A turn that starts while another member's runs logs `threads:
<thread> <persona>: turn starts while <others> is mid-turn; they run in
parallel` and is counted `parallel` on `GET /v1/threads/writer`. The read
and the stream's snapshot carry `turns[]`, every turn in flight
(`{persona, engine, model, started_at, on_background?, waiting?,
ended_by?}`), earliest first; `turn` stays, the earliest of them, for the
clients that read one. Proved by `tests/test_thread_parallel.py`.

**Replying to a message.** (2026-11-11; the owner, 10:53 PT: "I also want
the capability to be able to reply to a specific message in the thread so
the agent knows exactly what message I'm referring to.") Any message can
name one earlier message of the same thread as the one it answers, by id:
`reply_to: "m12"`. The contract:

- **Stored.** The record's `<!-- loam {...} -->` comment carries
  `reply_to`, and the readable markdown opens the message with one quote
  line, `> ↩ Ada, 2026-11-11 17:47 (m12): "first 80 characters…"` -- the
  referenced message's author, its time as its own `###` heading writes it
  (UTC, so a reader in Obsidian finds it by its heading), its id and its
  first 80 characters. No link: a message's id lives in a comment, not in
  an anchor, and Obsidian's heading links break on the `:` and `·` the
  headings carry and are not unique within a minute; the heading time and
  the id are what find it. The parser drops the quote line again, so a
  message's `text` is only what its author wrote. Validated in
  `threads.append`, under the record lock: an id that is not `m<n>` is
  `400`, one this thread does not hold (another thread's, or none) is
  `404`, and nothing is written.
- **Served.** `reply_to` is on the message wherever a message is: the
  thread read (`GET /v1/threads/:id`), `message.done` on the stream, the
  POST's own reply, and the full record's entry for a persona's reply
  (`GET /v1/threads/:id/steps`). A client shows the quote by looking the id
  up in what it has, or paging back with `before`.
- **The persona sees it.** Every message in a turn's prompt carries its id
  (`[Wed 2026-11-11 10:53 PST] Sam (m14):`), and a reply is shown with
  the message it answers immediately above its words: `Sam is replying
  to this earlier message, Ada's m12 of [<local time>]:`, that message's
  whole text as a `>` quote (segments included), then `Sam's words in
  reply:` and the words. The referenced message is looked up in the whole
  record, so it is shown whether or not it is in the turn's new-since-last-
  seen window, and also when it is. Past 4,000 characters
  (`PROMPT_QUOTE_CHARS`) it is cut, with a line giving its full length and
  saying the full text is in the thread file (its path) at that id.
- **A persona replies to a message** by opening its reply with `↩ m12` (or
  `<- m12`, for an engine that will not write the arrow). The writer
  (`ThreadEngine.reply_mark`) strips the marker -- from the reply's first
  words, its first text segment when it spoke before working -- and
  records `reply_to`. Every turn's prompt ends by saying so. An id the
  thread does not hold leaves the words as written, marker and all, and is
  logged (`reply marker names m99, which this thread does not hold; kept
  as text`): a reply is never refused for a bad id. Counted `reply_to` on
  `GET /v1/threads/writer`. A marker rather than a tool action, because a
  turn's reply *is* its message -- whatever the persona writes is posted
  -- so the only way to qualify that message from inside it is something
  in its text; a tool call would post a second message or need the engine
  to hold state between the call and the reply. From a wake, a cron or
  outside its own turn, a persona posts with `bin/thread post <thread>
  <text> --reply-to m12` or `mcp__engineer__thread` `post` with
  `reply_to`; a text opening with `↩ m12` means the same there.
- **The id is never words on the page** (2026-11-17; the owner: replies
  "point to words, not message IDs"). Ids stay Loam's linkage -- the
  record's comment, the prompt, `reply_to` -- and the reader sees one
  quote of the message's words with its author above the reply, which
  jumps to the message and marks it. Three places had still shown a
  persona's `↩ m111` under its own quote, and each now holds it back:
  - *The full record.* It keeps every event as it came, so a reply's first
    words in it still open with the marker; the page builds a reply's
    words from that record once its steps are read. `thread_steps.read`
    (and so `GET /v1/threads/:id/steps` and `?steps=full`) serves an
    entry's first top-level words without the marker when the entry's
    `reply_to` names that same id (`threads.unmark_steps`). The file is
    not rewritten; any other id, arrow or mention of `m12` in the words
    stays as written.
  - *The live draft.* `message.delta` used to carry the raw draft. While a
    reply's opening may still become a marker (`↩`, `↩ m1`, nothing after
    it yet) no delta is sent (`threads.streamed_opening`); once it is
    whole and names a message of this thread, the draft goes out without
    it and the delta carries `reply_to`, so the page shows the quote as
    the words come in, in the place the posted reply keeps
    (`ThreadEngine.shown_draft`). A marker naming an id the thread lacks
    streams as written, as it is posted.
  - *The page.* `messageSegments` (`app/public/thread-record.js`) takes off
    an opening marker naming the message's own `reply_to`, and nothing
    else, so a full record from an older broker reads the same; and a
    quote is the first line of the words read as words, its markdown marks
    off (`plainLine`, `app/public/thread-replies.js`).

  An original that is gone shows as a quiet, unlinked "Earlier message",
  never its id. The turn prompt's last lines now also say ids are Loam's
  bookkeeping: a persona names an earlier message by who said it and what
  ("your point about subtlety"), never by its id. Nothing rewrites ids in
  what anyone wrote: a bare `m111` in prose, code or a quote is the
  writer's own words.

Proved by `tests/test_thread_reply_to.py`; the page by
`app/test/thread-reply-marker.test.js` and, in a real browser at 390 and
1440 px in light and dark, `app/test/thread-reply-marker-browser.mjs`.

**A member only waiting on background work is not mid-turn.** (2026-11-10;
The owner, 15:41 PT, after m53 and m54 in t-6daf5a6f started no turn while
Ada sat `busy`, not `working`, on one background subagent: "I am not
getting a response. Is it because the agent itself is working or because
there is a background task it is waiting on?") A turn whose `result` came
with background work still out is *parked* (`on_background` on its turn).
A message for that member -- arriving while it is parked, or queued while
it was generating and still waiting when it parks -- is handed over at
once: the engine puts its own `loam.hand_over` on the parked turn's watch
queue, that turn ends quietly (no system line, no after-result watch; its
words were posted at its `result`), and the member's next turn sends the
message into the same live session (`start_session` returns the running
one). That turn is a new turn in the record with its own reply, and since
it watches the session, the background task's notification later comes in
as its own reply as before. Logged as `threads: <thread> <persona>: a
message arrived while its turn waited on background work (<task>);
delivered now into session <id>` and counted `delivered_while_waiting` on
`GET /v1/threads/writer`. A member that is generating (`working: true`)
still queues. Per engine (`TAKES_INPUT_WHILE_BACKGROUND` in
`lib/loam_thread_engine.py`): **Claude Code** reads stdin between turns, so
the message is answered at once and the task notification hands the
persona another turn later. **Codex**: the root thread's turn is over (its
subagents are other threads, root calls outliving the turn are tracked by
the broker), so `turn/start` is accepted at once; a subagent that ends
while that answer is being generated has its report handed to that turn by
Codex (`CodexSession._wake` does not wake a root mid-turn), so it is folded
into that answer rather than a reply of its own; one that ends after it
wakes the root as its own reply. **Antigravity** runs one process per turn
and states no background tasks, so a member on it is never parked; were one
to be, the message stays queued and the member reads `note: "will answer
when its background task returns"` until it does, never silence. Members
carry `queued` (turns waiting behind theirs) and `note` (null, or that
sentence, or `"will answer when this turn ends"` while generating). Proved
by `tests/test_thread_handover.py`.

**A message sent while a member works is steered into its turn.**
(2026-11-14; the owner in t-153f9c43: a message sent while a persona is "in
the middle of doing an action or a task ... gets queued up, but it doesn't
always go to the agent once they're done"; what they asked for: "when the
current action ... is done before it moves on to the next action, it
actually sees my new message".) The case on the record was m6 in
t-232b9184: Ada's turn launched a background build and went on writing,
so the turn was already parked on it (`on_background`) while it generated;
m6 arrived, the hand-over was refused because the session was `working`,
and nothing asked again -- not at the turn's `result` (it was already
parked) and not at the parked turn's 5 s looks. m6 sat queued until
The owner re-sent it as m8 90 s later; then the queued turn for m6 ran with
nothing new and said so (m10, "Nothing new has come in since my last
reply"). Three changes, in `lib/loam_thread_engine.py`:

- **Steering.** `schedule` hands a message over to a parked turn as before;
  when the member's turn is generating, `ThreadEngine._steer` claims the
  leading run of message turns from the member's queue (so no turn can
  start for them meanwhile), sends the new messages -- `threads.steer_prompt`,
  only what is new to the turn, each under its id -- through the session's
  `steer`, and moves the turn's `read_through` past them, so its `through`
  says they were seen and its reply is posted with `steered: [ids]`. The
  stream gets `turn.steered {persona, messages, seq}` and the writer counts
  `steered`. A refusal puts the claimed turns back at the front, in order,
  with a worker to run them (`steer_refused`); a turn asking for another
  model, a job's receipt turn, and everything behind either keep their
  place. Per engine (`Session.STEERS`, `Session.steer` in
  `bin/session-broker`): **Claude Code** (2.1.325, probed 2026-11-14) takes
  a stdin user message mid-turn after the running tool's result, in the
  same turn and `result`; one written after the model's last words opens a
  second harness turn at once (`init` 0.1 s after the `result`), so a
  steered turn watches `STEER_FOLLOW_S` (5 s) past its `result` for that
  `init` and posts the answer as its next message. **Codex** (0.169.0)
  `turn/steer` with `expectedTurnId`: accepted, the words are a user
  message at the turn's next sampling boundary, answered in the same turn
  (probed mid-command and after the last agent message); refused when that
  turn is no longer the active one, so a steer racing the end becomes a
  queued message, never a lost one, and `broker.user_message` (`steered:
  true`) is logged only once Codex accepted. **Antigravity** cannot (one
  process per turn): queued, `note` "will answer when this turn ends".
  Nothing is cancelled: a steer lands at a boundary the harness chooses,
  after the step that is running. A tool that blocks for a long time (an
  engineer `brief` called in the foreground on Claude Code) holds the
  steered message until it returns; the brief's own instructions put long
  briefs in a background subagent, where the turn parks and the hand-over
  delivers at once.
- **The hand-over is asked again** at the turn's `result` when it was
  already parked, and at every `PARKED_CHECK_S` look while parked, not only
  at the moment it parked.
- **A queued turn whose message was already shown does not run**
  (`already_heard`): its `m<n>` is at or below the member's `through`. And a
  turn that dies (`exit`, `closed`, the broker stopping) before any reply no
  longer moves `through`: the system line says it ended, and the next turn
  is shown those messages again rather than reading them as answered.

Proved by `tests/test_thread_steer.py` (each case above; the m6 and m10
shapes fail on the code before it). What was not durable then -- a turn
still queued when the broker stopped wrote "…'s turn didn't start: the
broker restarted. Tag @… to ask again." -- is since 2026-11-19, below.

#### Input to a member is durable and ordered (2026-11-19, batch B step 2)

Each (thread, member) has an ordered list of input on disk,
`state/threads/inputs/<thread id>/<persona>.json`
(`lib/loam_thread_inputs.py`): the owner's messages, a job's result, another
agent's mention, an update's notice -- whatever `ThreadEngine.schedule`
queues. An input is written before it is queued, marked `sent` the moment
its turn's prompt goes into the session (or it is steered into a turn in
flight), and removed once its turn has dealt with it.

- **The stop posts nothing for input it did not answer.** A queued turn does
  not start and its input stays; a turn cut before it said a word posts no
  line and its input is kept, unmarked, to be given again. A cut turn that
  had said something posts its words and `Ada's turn ended: the broker
  restarted`, as before, and its input is settled. The line "didn't start:
  the broker restarted. Tag @Ada to ask again." is gone.
- **The start gives it again** (`ThreadEngine.resume_inputs`, called in
  `amain` before the delivery pass; the journal says `inputs: N scheduled
  again for M thread member(s), K dropped`): every list in order, once each.
  Each replayed turn's prompt ends with when the input was queued ("this
  input was queued for you at Thu 2026-11-19 08:12 PST and was still
  waiting when Loam's session host restarted …") so the persona can judge
  whether it still matters; a job's handoff note also says when the job
  finished.
- **Once only.** A message is given again only while the member's `through`
  has not passed it (`already_heard`), so a crash after the turn and before
  its mark runs no second turn. A result or notice a crash left marked
  `sent` was in front of the session already and is dropped, logged ("not
  given twice"); a job is also delivered once by its key
  (`system_turn_allowed`). Input for a thread that is gone is dropped.
- **A result can reach a turn generating about the same work.** `joins`
  lets a job's result into the member's turn in flight when that turn was
  under way before the brief was accepted (the waiter record's `since`,
  to the second): the turn that briefed it. It is steered in on Claude Code
  and Codex (`result_steer_text`: "a result for work you briefed in this
  turn …, not from the owner, nothing is posted for it") and settled with
  that turn. A result for a turn about something else -- one started after
  the brief, typically an answer to a newer message -- waits behind it and
  has its own quiet turn, so an answer does not jump topic; every turn of a
  member is of the same thread by key, so a result never goes into another
  thread's turn. A turn parked on background work hands the session over to
  a result as it does to a message (`hand_over`). Antigravity cannot steer:
  the result waits for the turn to end.

Proved by `tests/test_thread_inputs.py` and the stop case of
`tests/test_thread_replies.py`.

**A turn has no clock.** (2026-11-10; the owner, after `Ada's turn ended: it
ran past 30 minutes` at 13:09 PT while Ada's session worked on: "Please
remove any limitations on how long a turn or session can run. I don't want
any limitations at all.") Nothing in the broker ends, abandons or stops
watching a turn or a session because of elapsed time or a count. A thread
turn is watched until its engine's `result`, its session's exit or close,
the broker stopping, or a person ending it from the app; after the `result`
the after-result watch (below) holds for as long as the session lives. A
classic turn never had a clock of its own. What went: the engine's 30-minute
`TURN_TIMEOUT_S` and its `timeout` cause; the reaper's one-hour
`IDLE_TIMEOUT_S`, which ended any session, mid-turn or waiting on its
children, after an hour with no event; and the unused `TURN_TIMEOUT_S` in
`lib/loam_broker.py`. What stays, and why it cuts nothing: the idle stops
(`THREAD_IDLE_S`, `CLASSIC_IDLE_S`) and the pressure stop end only a session
with `busy()` None -- no turn in flight, no request pending, no background
task or awaited notification out -- and its id resumes at the next message;
a parked turn ends (`PARKED_CHECK_S`) only when nothing is out, and the
after-result watch posts anything that comes later; an empty `result`
(`EMPTY_RESULT_GRACE_S`) hands over to the same watch. Three clocks that do
not time a running turn are kept: the seat wait (`CAP_WAIT_S`, 300 s, a turn
that has not started because every session on the box is busy), the
unanswered-permission deny (`PERMISSION_WAIT_S`, an hour; the turn goes on),
and the agent-to-agent mention chain (`AGENT_CHAIN_CAP`). A wedged turn is
ended by a person. Proved by `tests/test_thread_no_clock.py` (a harness turn
two hours long, then parked two hours on a background task, clock advanced:
watched throughout, its replies posted, no system line) and
`tests/test_broker_seats.py` section 6 (busy and waiting sessions kept two
hours on; an idle one ended and resumed by id).

| route | what |
|---|---|
| `GET /v1/threads[?archived=1]` | `{threads: [{id, title, default, icon, members[{persona, engine, model, last_spoke_at, running, busy, background, background_tasks, working}], background, working, last{author,text,at}, unread, board_new, board, pinned, archived, general, created}], agents}`; `background` and `working` per member and on the row since 2026-11-10 (see "Who is working, and on what" below); General pinned first, made on first read. `default` is null for a thread nobody answers untagged; `model` a member's own choice, null for the persona's default; `last_spoke_at` the member's last message or wake bulletin (null if never); `running` (and its other name `busy`) true for each member whose turn is in flight (several at once since 2026-11-10). Archived threads only with `?archived=1`, beside the rest (2026-11-09) |
| `GET /v1/threads/events` | SSE, the list live: `threads.snapshot{threads, agents}`, then `thread.changed{thread, summary}` whenever a row moves (the engine's own events, and a stat of every record every 2 s for `bin/thread` posts and Obsidian edits) and `thread.removed{thread}` (2026-11-09) |
| `POST /v1/threads {persona?, text?, title?}` | 201 and the row; titled by the first message unless named. With no `persona` the thread has no default and no members (2026-11-09) |
| `DELETE /v1/threads/:id` | removes the record and its board from the vault and the thread's runtime state → `{deleted, thread, removed[paths]}`. The owner's explicit action only (the app confirms); `409` mid-turn; General `400` |
| `POST /v1/threads/:id/attachments` | multipart `file` parts → `201 {attachments[{path, name, size, type}]}`; starts nothing |
| `GET /v1/threads/:id?limit=&before=<msg id>` | `{thread, messages[{id, author, at, kind, text, did[{label}], segments?, attachments?, model?, engine?, late?, reply_to?}], has_more, turn, turns, pending_permissions, board{path, markdown}}`, newest page; `turns` every turn in flight, earliest first, `turn` the earliest (2026-11-10); a read without `before` marks the thread read, unless `?seen=0` (2026-11-10: a read that is not the owner looking -- a page's background refresh -- writes nothing). `before=<msg id>` is a position: the messages earlier **in the record** than that one, in record order (2026-11-10; a recovered late reply sits where it was recovered, so ids are not in record order); an id the record lacks falls back to the number cut. `thread` is the row, members with `background` and `working` (2026-11-10); `model` on a persona message is the id the engine ran it on (see "Model and provider on every reply") |
| `PATCH /v1/threads/:id {title?, icon?, pinned?, archived?, default?: persona\|null, add?, remove?, engine{persona: engine}?, model{persona: model\|null}?, seen?}` | the row. A changed default is said in the thread as a system line ("the owner made Iris the default", or that nobody is). A `model` is checked against the member's engine (Claude Code's aliases and ids, Codex's `model/list`, Antigravity's `agy models`); an engine switch clears it. Archive moves nothing on disk. General can't be renamed, archived or unpinned (`400`) |
| `POST /v1/threads/:id/messages {text, attachments?: [path], model?, reply_to?}` | mentions join and get turns, else the default, else nobody; `@all`/`@everyone` gives every member a turn (2026-11-10, below). `attachments` are paths the upload route stored; they go on the message and to the turns it starts. `model` is those turns' model over each member's own, checked against their engines. Nothing moves the default but a PATCH `reply_to` (2026-11-11) is the id of an earlier message in this thread that this one answers: `400` if it is not an id like `m12`, `404` if this thread has no such message, and nothing is written or started either way (see "Replying to a message" below) |
| `PUT /v1/threads/:id/board {markdown}` (and `GET`) | the board, saved whole |
| `GET /v1/threads/:id?steps=full` | the same page, each message with `steps`: its full record's steps (below), `null` for a message with none (a person's, a system line, a reply from before the record). Off by default, so the default read stays light (2026-11-10) |
| `GET /v1/threads/:id/steps[?message=<msg id>]` | `{thread, steps: [entry]}`: the full record, one entry per reply, for the whole thread or one message (`404` for a message with none) (2026-11-10) |
| `GET /v1/threads/writer` | the record writer's counts since the broker started: `{since, replies, late_recovered, late_prompt, fanout, parallel, delivered_while_waiting, reply_to, carried, rotated, steps_written, steps_failed, last_failure}` (2026-11-10; `rotated` 2026-11-11: turns that started a fresh session because the old one passed the context threshold). `parallel`: turns started while another member's ran; `late_prompt`: replies the after-result watch posted as they finished; `fanout`: turns `@all` made beyond its message's own mentions |
| `GET /v1/threads/imported[?session=<id>]` | classic chats imported as threads (2026-11-11, below): `{imported: {session id: thread id}}`, or for one session `{session, thread}` and `404` for one never imported. What a link or bookmark to a classic chat is sent through. Each imported thread's row also carries `imported_from` (the session id) and `source` (its transcript, vault-relative); both null on every other thread |
| `GET /v1/threads/:id/events?after=<msg id>[&steps=full]` | SSE: `thread.snapshot{turn, turns, pending, members}`, `members{members, background, working}` (2026-11-10), `turn.started{persona, engine}`, `message.delta{persona, text, draft}`, `turn.step{persona, label, steps, after_result?}`, `message.done{message}` (with `message.steps` on `?steps=full`), `member.joined{persona, by}`, `board.changed{board_new}`, `thread.changed{summary}`, `turn.waiting{persona, text}`, `turn.finished{persona}`, and `broker.permission_request`/`broker.permission_resolved` with `persona` and `session_id`, answered on `POST /v1/sessions/<session_id>/permissions/<request_id>` as today |

The stream opens with `thread.snapshot`: `turns` is every turn in flight,
earliest first, and `turn` the earliest (`{persona, engine}`, or null), and
`pending` (since 2026-11-08) is every request those turns are still waiting
on (each carries its `persona`), each exactly the
`broker.permission_request` the stream would have published live, with
`persona`, `session_id` and `thread`. A page opened after a persona asked
something draws the card from it; a thread's stream has no log to replay,
so without it the question was invisible until answered elsewhere. It is
`[]` when no turn is running: a request lives only as long as the turn
that raised it is watched. The session keeps each request's event as it
went out (`Session.pending_events()`), which is where this reads from.

**Room (2026-11-09).** A thread turn's session carries
`idle_timeout_s = THREAD_IDLE_S` (600 s, `lib/loam_broker.py`), so the reaper
ends it ten minutes after the turn instead of the hour a classic chat keeps,
and only when it is not busy; the next turn resumes it by `session_id`. When
the box is full even after `start_session` has ended an idle session (see
"Admission by memory" above), the turn posts one system line in a person's
words — "Cleo is waiting for a free seat: four others are mid-turn. I'll
post as soon as one frees up." — publishes `turn.waiting` with the same
`text`, keeps it on `turn.waiting` in `GET`'s `turn`, and retries every
`CAP_RETRY_S` for `CAP_WAIT_S` (300 s). Then it answers, or fails with
"Cleo couldn't get a seat: the box stayed full for five minutes (…). Tag
@Cleo to try again." The broker's route-level text never reaches a thread.

**Every turn is told the time.** The prompt's head carries "It is now
Monday, 2026-11-09, 9:25 AM PST (America/Los_Angeles)", fresh or resumed,
and the transcript's message stamps are in the same zone
(`Mon 2026-11-09 09:25 PST`). Until 11-09 a turn had only UTC stamps and the
harness's own date without a weekday — Claude Code's `Today's date is
2026-11-09.`, Codex's `<current_date>` and `<timezone>` — and two members'
first replies on a Monday said "Sunday afternoon". **A classic chat is told
the same since 2026-11-09** (`lib/loam_clock.py`): the text its harness
receives opens with `<loam-clock>It is now Monday, 2026-11-09, 10:25 AM PST
(America/Los_Angeles).</loam-clock>`, on Claude Code, Codex and Antigravity
alike; `broker.user_message` keeps what the person typed, a slash command is
sent as typed, and the transcript writer (`person_text`) and the adoption
replay (`loam_clock.strip`) take the envelope out of the harness's file. A
thread session sets `tell_time = False`: its prompt has its own line.

**A reply in order (2026-11-09).** A reply that speaks, works and speaks
again is stored as `segments` in its `<!-- loam … -->` comment:
`[{"text": "Let me look first."}, {"did": [{"label": "Read: beds.md"}]}, …]`,
everything up to the last step, whole; the message's `text`, and the
markdown body under it, is what it said last. `did` is kept as it was
(steps, with earlier speech as `Said: <160 chars>`), so a reader of the older
field loses nothing. A `-->` inside the JSON is written `--\u003e`.

**Waiting on a background task.** When the harness says work the turn
launched is still out -- a `broker.background_tasks` with a count, or a
`result` while `working_behind()` -- the stream gets one
`turn.step{persona, label: "Waiting on a background task: <description>",
waiting: true}` and the turn keeps listening for the follow-up turns the
harness takes when that work reports.

**Every reply is its own message (2026-11-10).** One harness turn -- from a
prompt, a mention, or a background task's notification, up to its top-level
`result` -- is one message in the record, with its own id and time, in the
order said, and its own `message.done` on the stream. Until 11-10 a turn
that went on after its own `result` was ONE message: its `did` the whole
run's steps and its text the LAST reply's, so the earlier replies were lost
from the record (thread `t-6daf5a6f` `m4`, three replies, 11-10 09:47–09:52
PT). A harness turn that said nothing while more is still coming carries its
steps to the next reply (counted as `carried`). A subagent's step (`↳ …`)
no longer ends what the persona said before it: only a top-level step does.
A reply the session made while no turn was watching (a notification after a
`result` that `working_behind()` called final; since the evening of 11-10,
only one no after-result watch heard either, which means a broker restart:
see "Late replies, who is working, model, `@all`") is found in the session log
past the `seq` the thread last recorded (`state/threads/sessions.json`),
posted at the persona's next turn before that turn's own reply, marked
`late: true`, logged and counted as `late_recovered`. Every one of these, and
a full record that fails to write, is a line in the broker's log
(`threads: …`) and a number on `GET /v1/threads/writer`.

**The stream's order is posting order (2026-11-16).** A message's id is
taken, and its `message.done` published, when it is posted: a person's when
it is sent, a reply at its harness turn's `result`, not when its words
began. So a message sent while a member works (steered into its turn, or
queued) has a lower id than the reply that took it in, and comes first on
the stream, in the thread read and on a reconnect's `after=` replay. Words
streamed before that message can still post after it: they belong to a
reply whose `result` had not come. `turn.started` and the first
`message.delta` can therefore come before messages that the reply will
settle below; a client that reserves a reply's place at `turn.started` has
to move the finished reply to its posting place, which the read gives.
`tests/test_thread_completion_order.py` proves this through the stream route
(the owner, 11-16 in t-153f9c43: "if the final message actually gets posted
after other messages, then it should be ordered that way").

**A turn that said nothing is not a reply (2026-11-10).** A persona message
exists in the record and on the stream only when the persona said
something; its text is what it said after its last step, or, when steps
followed its last words, those last words. There is no "(no reply)" text any
more. A harness turn with steps and no words carries them to the next reply;
when no words come, they go into the full record alone, as an entry with
`"message": null, "unspoken": true, "after": "<the id it follows>"`. A turn
that ends without its `result` -- `exit`, `closed`, the broker stopping --
gets one quiet system line, `Ada's turn ended: the broker restarted`
(`…: its session exited`, `…: its session closed`; the time-based `…: it ran
past 30 minutes` went 2026-11-10), and a turn that finished saying nothing at all gets `Ada's
turn ended without a reply`. A turn queued while the broker is stopping
never starts and posts nothing; its input is given at the next start
(2026-11-19; it wrote `Ada's turn didn't start: …` before), and a turn cut
before any word posts no line either, for the same reason. Two causes found in `t-6daf5a6f`: a resumed Claude Code session
can open with an empty `result` (`num_turns: 0`, nothing asked of the model,
closing the background tasks the last process left), which was taken for the
end of the turn (`m13`, "(no reply)", and the real answer went unwatched);
it is now passed over, and the turn watches on (30 s at most, if nothing
follows). And the broker's own "Waiting on a background task" step ended the
words said before it (`m11` lost "Status only: …"); it ends nothing now.

**The full record (2026-11-10).** Beside the readable record, every reply's
every step is kept, untrimmed: `state/threads/steps/<thread id>.jsonl`, one
JSON line per reply, appended and never rewritten (`lib/loam_thread_steps.py`
holds the format). Not in the vault because tool outputs go in whole -- a
`cat` of a config, an `env` listing -- and the vault is pushed to GitHub and
served at vault.<domain>; not a cache, unlike the rest of
`state/threads/`. An entry:

```json
{"message": "m4", "thread": "t-6daf5a6f", "author": "ada",
 "engine": "claude-code", "model": null, "at": "2026-11-10T17:47:55Z",
 "session_id": "ede89dc4-…", "seq": [1, 2447], "ended": "result",
 "steps": [
  {"type": "text", "text": "I'll read the record first…", "parent": null,
   "message": {"model": "…", "usage": {…}}},
  {"type": "thinking", "thinking": "…", "parent": null},
  {"type": "tool", "id": "toolu_…", "name": "Bash",
   "input": {"command": "…", "description": "…"}, "parent": null,
   "output": "…the whole output…", "is_error": false,
   "result": {"tool_use_result": {…}}},
  {"type": "permission", "event": {"type": "broker.permission_request", …}},
  {"type": "permission_resolved", "event": {…}},
  {"type": "event", "event": {"type": "system", "subtype": "task_notification", …}},
  {"type": "end", "event": {"type": "result", "usage": {…}, "total_cost_usd": …}}]}
```

`parent` is the `Agent` call a subagent's step ran under (null at the top);
`output` is the tool result's content as the harness returned it, a string
or a list of content blocks, with no size cap; `message` on a turn's first
block is the rest of the assistant message (model, usage). Every event the
harness emitted is a step, in order, except `stream_event`: the token deltas
the `assistant` events repeat whole, which stay in the session log that
`seq` points into. `ended` is `result`, `ended` (stopped from the app),
`timeout`, `closed`, `exit`, `stopping` (the broker's own stop) or `late`.
`GET /v1/threads/:id/steps` lists the `unspoken` entries with the rest;
`?message=` and `?steps=full` never carry one, since no message is theirs. Entries written by
`bin/backfill-thread-steps` (every thread reply made before the record, from
the broker's session logs, mapped turn by turn and proved against each
message's text) say `backfilled: true` and `replies: n`, the number of
harness turns in it -- `t-6daf5a6f` `m4` says 3, and its steps hold the two
replies its readable message lost. The app renders steps folded under the
message, as the classic view does; that is loam-ui's.

**Classic chats already have their full record**: the broker's session event
log, `state/broker/sessions/<session id>.jsonl`, every event the harness
emitted with its `seq`, replayed by `GET /v1/sessions/<id>/events?from=0` (the table at the top). A thread entry's `session_id` and `seq` point into the same log.

**The second transcript (2026-11-10).** Until the record, a thread turn was
also filed by the transcript hook as a classic transcript in
`15_CONVERSATIONS/` (`surface: loam-ui`, title "Thread · …"), a duplicate of
the thread. A thread turn now writes `state/threads/steps/sessions/<session
id>` as it starts, and the hook files nothing for a session that has one: it
stops for exactly the sessions the full-record writer is behind.

**Models.** A turn runs on the message's `model`, else the member's, else the
persona's own; a running session is told by `set_choices`, as a classic
chat's PATCH tells it, and clearing goes back to the persona's at its next
launch. What the reply records is below: since 2026-11-10 it is the model
the engine ran on, not the one chosen.

### What a persona carries into a turn (2026-11-11)

The owner, 2026-11-11, in `t-6daf5a6f`: "it doesn't make sense for the entire
history of the thread to be uploaded or added to the context ... if they
ever need information from earlier on in the conversation, they can always
go refer to the file", and on the summary: "the summary should actually be
done per persona ... they can each have their own summary of a thread and
they can keep it in their own workspace." Until then a (thread, persona)
session was handed the last 200 messages when it was new and kept
everything it had seen until the CLI compacted it: Ada's session on
`t-6daf5a6f` carried a median 429k tokens into each of its 43 turns, 693k
into the last.

**The turn's input** (`threads.turn_prompt`, chosen in
`ThreadEngine.run_turn`) is, and nothing else from the thread:

1. **The persona's own summary of the thread**, on a fresh session: the file
   `ai-labs/<persona>/threads/<thread id>.md`, shown whole inside
   `<your-summary>` (past `SUMMARY_CHARS`, 8,000, cut with where the rest
   is). A resumed session is not handed it again: it was given it when it
   began, and keeps it itself. **Missing means a first turn**: the prompt says
   there is none yet, names the path, and asks for one before the turn ends.
2. **The recent exchange** (`threads.recent_window`): from the newest message
   in the record back to the last real pause -- a gap of more than 30
   minutes (`PAUSE_S`) between two consecutive messages -- or 20 messages
   (`WINDOW_MAX`), whichever comes first. The newest message is always in; a
   gap that cannot be read (a time missing, a recovered late reply out of
   order) is not a pause, so the window is a pure function of the record.
   Counted from the end of the record rather than from the owner's message, so
   another member's reply that landed while this turn waited is in it. A
   fresh session gets the whole window, its own messages included; a resumed
   one gets what in the window is new to it (after its `through`, not its
   own), and a line saying how many new messages fell outside the window
   and are in the file.
3. **Any message a message in the window replies to** (`reply_to`), in full,
   right above the words that reply to it, wherever it is in the record
   ("Replying to a message", above; past `PROMPT_QUOTE_CHARS`, 4,000, cut
   with where the rest is, as before).
4. **One line pointing at the record**: `[Earlier history is in the thread
   file, /home/loam/vault/15_CONVERSATIONS/<title>.md: every message
   under its id. Read it when you need something from before what is shown
   here.]`

**The summary is the persona's.** The broker only reads it
(`threads.read_summary`); it never creates the file, its folder or anything
else in a persona's workspace. Every turn's prompt says the file exists (or
not), that it is theirs, what it is for -- a few lines on what the thread is
about from their perspective, the decisions made, what is open, anything
they want to remember -- and to update it before the turn ends when
something material changed. Each persona in a thread keeps its own, so two
members of one thread hold two views of it. Summaries are not served by any
route and not shown in the app.

**Sessions rotate instead of growing.** A member's session for a thread is
resumed while it is small. Its size is the last `context.used` it stated
(the broker's sum over the CLI's own numbers, "What a turn cost in
context"; the same field on Claude Code, Codex and Antigravity, so all three
follow one rule), read from the resident session's turn records, else the
broker's record of it, else the `context` `sessions.json` remembered at the
turn's end. Once it reaches **40% of the model's window or 150,000 tokens,
whichever is smaller** (`ROTATE_SHARE`, `ROTATE_TOKENS` in
`lib/loam_thread_engine.py`; 150,000 alone when the window is not stated, as
on Antigravity), the next turn starts a fresh session seeded with 1-4. The
cap is there because the windows here are 1M: 40% of one is 400k tokens
re-read on every request. The rotation is invisible in the thread: no system
line; a reply the old session made that no watch heard is posted first,
marked `late`, as at any resumed turn (`recover_late`); then the turn's own
reply; the record goes on as one. **A session with work out is never
rotated**: background tasks listed, a notification still to land, or the
session mid-turn or asking -- its reply would land in a session no turn
reads; it is resumed, logged `session … is past the threshold (n tokens) but
has work out; resumed`, and rotated at the first turn after it settles. A
rotation is logged `threads: <thread> <persona>: session <id> holds <n>
tokens (rotates at <m>); this turn starts a fresh one` and counted `rotated`
on `GET /v1/threads/writer`. The old session is left to the idle reaper
(`THREAD_IDLE_S`), like any idle thread session.

**Measuring it**: `bin/thread-context-report <thread id> [--since <UTC>]`
lists every turn of a thread from the broker's session logs -- the tokens
the turn's first request held (`message_start` usage, the `context_used`
sum), the CLI's own `ttft_ms` on it, whether it opened its session, and the
prompt's size. Proved by `tests/test_thread_context.py`.

### Late replies, who is working, model, `@all` (2026-11-10)

**A reply after the turn's `result` is posted the moment it finishes.** When
a turn ends on its `result`, the engine keeps a light watch on that member's
session -- a queue on the session, nothing polled -- for as long as the
session is alive: the reaper ends an idle thread session after
`THREAD_IDLE_S`, and its exit ends the watch; the member's next turn takes
the session over. A harness turn the watch hears through to its top-level
`result` -- a background task's notification handing the persona another
turn after `working_behind()` said nothing was out -- is posted then, within
the event's own breath: its own message, its own `message.done` on the
stream, no `late` flag, `ended: "notification"` on its full record entry,
counted as `late_prompt` and logged `threads: <thread> <persona>: a reply
after the turn's result (session …) posted as it finished`. Steps it takes
on the way go out as `turn.step` with `after_result: true`. Until 11-10 such
a reply waited for the persona's next turn and came in marked `late`
(the owner saw their answer only when they sent the next message). The `late` path
stays for what no watch heard -- a broker restart drops every watch -- and
is unchanged: at the next turn, before that turn's reply, `late: true`,
counted as `late_recovered`. A harness turn on the same session that
somebody asked from outside the thread (it has a `broker.user_message`: a
message sent from the classic view) is never posted into the thread, by
either path.

**A turn parked on background work ends when that work has settled.** The
CLI sends a task's `task_notification` before it empties its task list as
often as after (396 of 684 tasks in this box's session logs, read
2026-11-10). A task notified first used to go into the session's
`_awaiting` anyway and hold `working_behind()` true for `BACKGROUND_SETTLE_S`
(20 s) after the answer's own `result`; a thread turn read that `result` as
"more to come", and the settle timer that cleared it emits no event, so the
turn sat open until the hour reaper or the 30-minute timeout (both gone since). Both ends are
fixed: a task already notified is not awaited (`Session._notified`), and a
parked turn asks the session every `PARKED_CHECK_S` (5 s) whether anything is
still out, ending on its last reply when nothing is (`threads: … nothing is
out any more; the parked turn ends`). Caused on a real Claude Code turn on
11-10 (a 25 s background `sleep`): before, the turn stayed open after
FINISHED; after, it ended 0.0 s after it.

#### Who is working, and on what

Each member on `GET /v1/threads/:id` (`thread.members`), on every row of
`GET /v1/threads`, on `thread.changed` and `threads.snapshot`, and on the
thread stream's `thread.snapshot` and `members`, carries:

| field | what |
|---|---|
| `running` / `busy` | this member has a turn in flight in this thread; several members can at once (2026-11-10) |
| `background` | integer, never absent: background tasks or subagents out behind this member's session in this thread right now -- the harness's own list (`background_tasks` on the session record; Codex subagents and long-running calls count, as on the classic row since 10-09), and `1` while that list has emptied but the notification has not landed (`working_behind()`), so a member is never `0` while `GET /v1/sessions/live` says `state: background` for the same session. `0` when the session is not resident |
| `working` | boolean: the member is mid-turn -- its turn is in flight and not parked on background work, or its session is generating on its own (a notification's turn after the result) |
| `background_tasks` | array, never absent (2026-11-10): `[{id, description, started_at, message, launched_at_seq}]`, the tasks `background` counts by name -- `description` is the task's own, as the harness states it (`system`/`background_tasks_changed` and `task_started` for Claude Code: a subagent's `description`, e.g. "Brief loam-ui: chip logo, timestamp row"; Codex's long-running calls carry the broker's description of the command); `started_at` (UTC ISO) is when the broker first saw the task listed, since the harness's events carry no time. `[]` when nothing is out or the session is not resident. It can be shorter than `background` in the moment a task has left the list and its notification has not landed. **`message`** (2026-11-10) is the thread message id (`m12`) of the reply whose harness turn launched the task, so a page can link the task's row to it: the task appears in the harness's list mid-turn, before that reply is written, so `message` is `null` until the reply is posted and is filled in then (a `members` event says so); a task listed before its turn has done or said anything was launched by the reply already posted, and gets that one's id. It stays `null` for a task no thread turn saw launched (a classic-view turn, or one launched before a broker restart) and for a harness turn that ended saying nothing (its steps are in the full record only). **`launched_at_seq`** is the session event (`seq`) that first listed the task, or `null` likewise |

A row adds `background` (the sum over members) and `working` (the number of
members with `working` true). A turn parked on its background tasks reads
`busy: true, working: false, background: n`; the follow-up turn the
notification opens reads `working: true` again. **That state is not
"thinking"**: the member's words are done and it waits on
`background_tasks`, so a page says so ("Ada is waiting on 2 background
tasks: Brief loam-ui …") rather than drawing a thinking indicator. Verified
on the live broker 2026-11-10 22:22Z on `t-6daf5a6f`: Ada replied at
22:11:32Z with two subagents out, and the read said `busy: true, working:
false, background: 2`, `turn.on_background: true`. The same shape held the
long stretch the owner saw ("Ada · Thinking…") from 13:49:53 to 14:07:58 PT:
in Ada's session log the turn's `result` (seq 16055) came with four
subagents out, and the next top-level event is the `task_notification` at
seq 16059, 18 minutes later -- nothing was generating, and the broker
running then (started 13:36, with `8c8e3e3`) computes `working` as above.

The thread's stream publishes **`members`** whenever the members array would
read differently: `{"type": "members", "thread", "members": [...the whole
array, as on the row...], "background", "working"}`. Replace the members you
hold with it; never patch. It goes out on a turn starting or finishing, a
member joining, each change of the harness's task list, a notification, the
start and end of every harness turn (in a turn and under the after-result
watch), and the watch ending. The thread list hears the same change as that
row's `thread.changed`. `thread.snapshot` carries `members` too, so a page
has the array before the first change.

#### Model and provider on every reply

A persona message's `model` -- in its `<!-- loam {...} -->` comment, on its
full record entry (`model`), on `message.done` and on the thread read -- is
the model id the engine said it ran that reply on: the `model` of the
reply's `system/init`, else of its top-level assistant messages (Claude
Code states both; Codex's and Antigravity's translators state the same
fields), else the session's last `init`, else the model the turn asked for.
"The agent's default" (no model chosen) resolves to the concrete id the
engine reported: `claude-fable-5-1`, not `fable`. A real Claude Code turn on
11-10 recorded `claude-fable-5-1` with no model chosen; a real Codex turn
recorded `gpt-6.1-sol`. Until 11-10 `model` was written only when one was
chosen, and was the choice (an alias such as `opus`); messages written
before are left as they are -- no `model`, or the alias -- never guessed.
An `unspoken` full-record entry carries the same `model`.

`engine` is the provider: `claude-code`, `codex`, `antigravity`. **For the
page:** the provider's display name is the `name` of the row in
`GET /v1/providers` whose `id` is the message's `engine` ("Claude Code",
"Codex", "Antigravity"). The broker has no display mapping for concrete
model ids -- `GET /v1/providers`'s `models` lists the aliases a person picks
(`fable` → "Fable", `opus` → "Opus") and Codex's and Antigravity's own lists,
each `{id, name}` -- so show `name` when `model` equals a listed `id` (an
older message's alias, or a Codex model, whose list carries concrete ids),
else the id as it is.

#### `@all`

A message from the owner containing `@all` or `@everyone` (any case, as a
mention: not inside a word, not an email) gives every member of the thread a
turn -- the default too, should it not be a member -- exactly as if each
had been tagged: one turn each, all started at once like several mentions
are (each member's turns run on its own queue since 2026-11-10), mentions in the same
message first and never doubled (`@all @ada` is one turn for Ada). Members
already in the thread get no "brought in" line, as with any mention. In a
thread with no default and no members it starts nothing and writes one
system line, `Nobody to reach: bring someone in first`. Logged
`threads: <thread>: @all from Sam, n turn(s) (ada, iris, …)` and counted
as `fanout` (turns beyond the message's own mentions). **From a persona,
`@all` is plain text**: a reply or a `bin/thread post` saying it starts no
turn, so two agents cannot set every member answering every other.

#### The transcript marker for Codex

The marker that stops the duplicate transcript is now written under every
id a member's session goes by: the broker's id, a fork's `cli_session_id`,
and `thread_id` -- for Codex the Codex thread id, which is the `session_id`
Codex's Stop hook payload carries. Read off the box on 11-10: all 23 Codex
broker records with a `thread_id` had their transcript sidecar
(`state/transcripts/<id>.json`) under the `thread_id` and none under the
broker id, so until then a Codex thread turn was still filed twice. Caused
on a real Codex thread turn: the hook logged `01a1c740-…: a thread's session,
kept by the thread; not filed`. The marker is written as the turn starts and
again as it ends, since Codex names its thread once the session is open.

Agent-to-agent mentions are capped at `AGENT_CHAIN_CAP` (lib/loam_threads.py)
turns since the owner last wrote. Agents post with `bin/thread post` or
`mcp__engineer__thread`. Since 0.2.1 a post is told to the broker
(`POST /v1/threads/<id>/posted {"message", "by"}`, `ThreadEngine.posted`):
it goes on the thread's stream at once, and its @mentions wake members as a
reply's do, under the same cap, counting the agents' posts since the owner
last wrote; before, a post gave nobody a turn. They rename one with
`bin/thread rename <id> <title>` (the tool's `rename` action, 2026-11-10),
which is the details page's PATCH path, `loam_threads.update(title=)`: the
record's `title:` and file name and the board's file and `board:` key move
together (a board name another thread holds gets ` (2)`), General refuses,
no line is left in the thread, and an open app sees it on the list stream's
2-second stat. `bin/sweep-boards`
(`loam-board-expiry.timer`, hourly) removes items whose `until` has passed.

### Classic chats imported as threads (2026-11-11)

Classic chats (the session-based view) are retired; threads are the one way
to talk to a persona. `bin/import-classic-chats [--dry-run]`
(`lib/loam_classic_import.py`) makes each one an **archived thread**: the
same title, the persona its only member and its default, its messages in
order with their authors and the times they were said, and every step of
every reply in the full record where the broker's session log has them
(entries marked `imported: true`, `ended: "imported"`). A classic chat is a
session record with no `thread_id` and a title not starting `Thread ·`
(a thread turn's own session). Nothing is moved or deleted: the transcript in
`15_CONVERSATIONS/` and the session's files stay, the thread's record is
written beside the transcript (`<title> (2).md` where the names collide), and
its frontmatter names both: `imported_from: <session id>`,
`source: "15_CONVERSATIONS/<title>.md"`. The read mark is set at the end.

- **Where the messages come from.** The session's event stream as the
  classic view replayed it (the app's file for an adopted chat, cut at
  `replayed`, then the broker's log), cut into the owner's messages at each
  `broker.user_message` and into one persona message per harness turn by
  the live writer's own `Reply`. The broker's log does not stamp a person's
  message, so its time is the transcript's for the same words, else the
  first event after it. The **transcript** is the record instead when the
  stream is not whole -- an adopted chat whose app file Claude Code has
  pruned, or a September log from before `broker.user_message` -- and a
  logged reply's steps then go to the transcript turn whose words contain
  it.
- **Idempotent.** A session that is some record's `imported_from` is not
  imported again. What the chat said after its import is appended on the
  next run, while the thread holds exactly what the import wrote
  (`threads.json` `imported`, the count); once anyone writes in the thread
  it is the record and the chat no longer feeds it.
- **Continuing one.** The owner's message in an archived imported thread takes
  it out of the archive and its persona answers, as in any thread: a fresh
  session handed the recent exchange and the thread file's path, as any
  fresh session is (it does not resume the classic session). A thread archived by hand stays archived when written in.

On the box, 2026-11-11: 159 classic chats, 153 imported, 6 not (empty
sessions with nothing said). Proved by `tests/test_classic_import.py`.

### Branching a thread (2026-11-11)

(the owner, 16:05 PT: "take a specific message or a topic … and be able to
branch that off into a separate thread … infer a summary of what this new
thread is going to be about … pre-made into the text box so I can add
additional questions.") Any message can start a new thread of its own, with
any persona, carrying the stretch of conversation that led to it. The record
side is `lib/loam_threads.py` (`branch_window`, `branch`, `branch_context`);
the routes are the broker's; the draft is written by the app.

- **The window** (`branch_window`). The messages ending at the chosen one,
  in record order. Walking back from it, a message joins while the window
  has fewer than 12 (`BRANCH_MAX`) and no gap over 30 minutes
  (`BRANCH_PAUSE_S`) lies between it and the chosen message; when the pause
  comes before 3 messages (`BRANCH_MIN`), it is crossed only to make up
  three. System lines are not in it. A gap of exactly 30 minutes is not a
  pause. Only the record's times and order go in, so the same message
  always gives the same window. Branching from a system line is `400`, from
  an id the thread lacks `404`.
- **`GET /v1/threads/:id/branch?message=m12[&persona=ada]`**, the sheet's
  first read, writes nothing: `{thread, title, message, record, window:
  [messages], persona, people, summarizer, fallback}`. `people` is who the
  branch can be with, the source's default first, then its other members,
  then everyone; `persona` is the one preselected (`?persona=`, else the
  source's default). `summarizer` is `{system_instruction, input, answer}`:
  one text call whose answer is a JSON object `{"title", "draft"}`, a title
  of three to seven words and an opening of at most 90 words in the owner's
  voice (what the branch is about, the context from the window it needs,
  ending with an open line for them to finish). `fallback` is
  `{title, draft}` made from the record alone, for when that call fails.
- **Who writes the draft: the app, not the broker.** The app already holds
  the Gemini key and writes read-aloud briefs with its brief model
  (`SPEECH_BRIEF_MODEL`, default `gemini-flash-latest`), and the broker holds
  no vendor credential by design (its unit says so). So the app sends
  `summarizer` to that model, parses the JSON, and falls back to `fallback`;
  the prompt's words live here, beside the window they describe, so every
  caller asks the same thing.
- **`POST /v1/threads/:id/branch`** `{message, persona?, title?, draft?,
  by?}` → `201 {thread: <summary>, from, into, window}`. The new thread has
  `persona` (default: the source's default) as its default and only member,
  is titled `title` (else the draft's first line, else `From <source
  title>`), and its frontmatter records `branched_from: <source id>`,
  `branched_message: m12` and `branched_window: [ids]`. Its first message
  is a system line, `Branched from <source title>`, with `link: {thread,
  message, title}` on its `loam` comment (`from` in the reply). The source
  gets a system line `Branched into <new title>` with `link: {thread,
  title}` and `after: m12`, placed **under the chosen message** in the
  record (`threads.insert_after`, touching no other message's bytes; a
  second branch from the same message goes under the first one's line), with
  the next free id (`into`; also sent as `message.done` on the source's
  stream, so a page places it by `after`). If the source had been read
  through its last message, its read mark moves past the new line: the line
  is not news to whoever branched. With `by` (a persona), both lines say who:
  `Ada branched this from …`, `Ada branched into …`. Nothing is sent and
  no turn starts.
- **The draft.** `draft` waits as the new thread's composer opening:
  `summary.draft` on every row and read, kept beside the record in
  `state/threads/threads.json` (a convenience, like the placeholder-title
  flag) and not in it. The owner's first message spends it; `PATCH
  /v1/threads/:id {"draft": null}` clears it and a string replaces it.
  `summary.branched_from` is `{thread, message}` on a branch, `null`
  otherwise.
- **The persona's first turn in a branch.** A fresh session there is
  handed, after the usual head, `This thread was branched from the thread
  "<title>" (id …) at its message m12`, the window's messages in full (the
  same transcript lines, ids and reply quotes included) and the source
  file's path for anything earlier, then the branch's own messages. Not the
  whole source thread. Any later fresh session in the branch gets the same.
- **A persona branches** with `bin/thread branch <thread> <message>
  [--persona p] [--title t] [--text draft] --by <persona>` or
  `mcp__engineer__thread` action `branch` (`thread`, `message`, `persona`,
  `title`, `text`, `by`): the same `threads.branch`, its draft waiting in
  the owner's composer. With no text the draft is the `fallback`'s. It writes
  the files directly, as `post` does; an open page sees the new thread on
  its list stream's look at the records.

Proved by `tests/test_thread_branch.py` (the heuristic, both routes, both
lines and their places, the draft's life, the first turn's prompt,
`bin/thread branch`) and `tests/test_engineer_server.py` (the tool's
hand-off).

### A thread's engineer jobs (2026-11-13)

(the owner, 11-13 12:19 PT, on a thread whose tasks panel showed nothing while
two jobs it had briefed were running: "We should definitely get to fixing
this.") A thread's tasks panel showed only a member session's own background
subagents (`members[].background_tasks`). A persona briefs engineer jobs from
such a subagent so it can keep talking, and a job outlives both the subagent
and the session; once they were gone the panel was empty while the jobs ran.
The thread's record now carries the jobs themselves.

- **Which thread a job is from** is derived when the brief is accepted
  (`bin/engineer-job` `accept`, `lib/loam_waiters.caller`, since 2026-11-12),
  never passed by the caller: the launcher walks up its own process tree to
  the broker session it runs under (`LOAM_BROKER_SESSION` in an ancestor's
  environment, or a Claude CLI's `--session-id`/`--resume`), and that
  session's thread from `state/threads/steps/sessions/<id>` or
  `sessions.json`. A subagent runs inside its session's CLI, so a brief from
  any subagent of a thread turn records the same thread. The brief's header
  says `caller`, `caller_session` and `thread`. A wake, a cron and the shell
  record no thread, and their jobs show on no thread.
- **The list** is `lib/loam_activity.job_rows()`, the one reader of job state
  for anything that lists jobs by thread or by agent: built from the
  launcher's `box_status()` (running, landing, queued; what
  `mcp__engineer__status` reads) and each job's `result.json` (finished),
  never from a persona's session. Cached for 3 s; `thread_jobs(id)` filters
  it. A job with a verdict is finished even while its admission is being let
  go.
- **`GET /v1/threads/{id}`** answers `thread.jobs` (and `thread.jobs_error`,
  null unless the job state could not be read, when `jobs` is `[]`). Only
  this read carries it: the list stream's rows, the `members` events and
  the snapshot are unchanged, and so are `members[].background*` and
  `mcp__engineer__status`. A persona reads the same list with
  `bin/settings get thread.jobs --target <thread>`.

`thread.jobs`, active first (running and landing by start, then queued in
line), then the finished, newest first. Every row has every key:

```json
{
  "job": "20261113-120317-0a880088",
  "project": "Loam",
  "title": "Theme manifests may carry a dark-mode cover",
  "briefed_by": "ada",
  "thread": "t-780b66bd",
  "state": "running | queued | landing | finished",
  "phase": "landing | ci | deploy, while state is landing; else null",
  "since": "ISO UTC: when it entered this state (queued: joined the line)",
  "started": "ISO UTC: when its session started; null while queued",
  "finished": "ISO UTC; null until finished",
  "elapsed_seconds": "int: started to now, or to finished; null while queued",
  "outcome": "null until finished; then one of: landed, deployed, deploy failed, pull request, not landed, finished, failed, cut off, cancelled, interrupted",
  "outcome_detail": "null until finished; the launcher's own words (e.g. \"cut off at its 60-minute wall clock\")",
  "ok": "bool once finished; else null",
  "receipt": "vault-relative path of the receipt once finished (e.g. 40_PROJECTS/Loam/receipts/….md); null before, or when none was published",
  "position": "int while queued (1 is next); else null",
  "held_by": "what a queued job waits on, in words; else null",
  "last_activity": "ISO UTC while running: the engine's newest event (the Claude transcript's last line, its subagents' included, or the Codex/Antigravity stream's), as the heartbeat last read it; else null",
  "quiet_since": "ISO UTC while running and the engine has written nothing for 15 minutes: when the silence began; else null",
  "child_stops": "int: commands the launcher stopped because they never returned (lib/loam_stuck.py); 0 on nearly every job"
}
```

**The last three (2026-11-17)** are there because a job's start alone said
nothing about whether it was working: an Antigravity job frozen on a test that
never exited read as "running for 13 h" with its heartbeat ticking (the
launcher writes the heartbeat, not the engine). They come from the launcher's
`box_status()` slot for the job (`activity_of`), so `engineer-job --status`,
`mcp__engineer__status` and Activity say the same: Activity and the thread's
Tasks panel show "active 3 min ago", or "No activity for 13 hr" once
`quiet_since` is set.

Finished rows stay for 10 minutes after `finished` (`RECENT_SECONDS`), so a
finish is seen. The outcome words: `cancelled` and `interrupted` from the
stage; `cut off` when the launcher's outcome says so; `failed` for any other
verdict that is not ok (refused, launch failed, exited non-zero); then for
an ok job, `deployed` / `deploy failed` when it deployed at landing,
`landed` (landed or landed earlier), `pull request`, `not landed`, and
`finished` for a job that lands nothing (read-only, nothing to land).

Proved by `tests/test_thread_jobs.py`: briefs accepted from a thread
member's session, from a cron and from the shell; the caller walk from a
subagent's tool call; every state on the thread's record through the
broker's route with a real run lock held; another thread's job, a cron's and
one finished 20 minutes ago absent; `bin/settings get thread.jobs`; the
outcome words.
