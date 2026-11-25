# Box usage recorder and wire format (`loam-usage/1`)

Every Loam install keeps a local, numbers-only record of its resource use and
Loam activity under `<state>/usage/` (`lib/loam_usage_recorder.py`), carried
inside `bin/session-broker` with no extra always-on process and no database
server.

**Sending is off by default.** Core ships with no collector address. A
self-installed Loam keeps its usage record on the server and never attempts an
outbound connection. Outbound pushing runs only when `/etc/loam/loam.toml`
explicitly sets both `usage.collector_url` and a readable `usage.token_file`.
**Settings › About Loam** (`This server`) shows whether usage data is kept on
this server only or sent to a collector host.

## Local storage and rotation

- Active file: `<state>/usage/usage.jsonl` (compact JSONL, one sample or event
  per line, monotonically increasing integer `seq`).
- Archives: `<state>/usage/archive/<YYYY-MM-DD>-<first_seq>-<last_seq>.jsonl.gz`
  (gzip-compressed on UTC day rollover or when `usage.jsonl` reaches 2 MiB).
- Retention: raw one-minute samples kept for **90 days** (~4–6 MiB compressed),
  with a hard **16 MiB** total-size cap across `usage.jsonl` and `archive/`.
- State files: `state.json` (sampling cursors and service process identities)
  and `push.json` (outbound delivery cursor, backoff timer and sanitized error
  code; never stores the token).
- CLI: `loam usage status`, `loam usage tail`, `loam usage sample`,
  `loam usage push`.

## Privacy contract (numbers only)

Every written sample and event is validated against a closed schema allowlist
(`validate_record()`). Records contain **only** numbers, booleans, UTC
timestamps and closed enum words — never file names, thread titles, message
text, agent names, vault paths, prompts, device IP addresses, hostnames, owner
names, project names, cron names, job IDs or session IDs.

## Sample fields

Recorded about once a minute (`type: "sample"` on disk; placed in `samples` on
the wire without `type`):

| Field | Type | Description |
|---|---|---|
| `seq` | integer (`>= 1`) | Monotonic per-box sequence number (shared stream with events) |
| `ts` | string (UTC ISO-8601) | Sample timestamp (`YYYY-MM-DDTHH:MM:SSZ`) |
| `interval_s` | integer or `null` | Seconds elapsed since previous sample (`null` on first sample) |
| `mem_total_mb` | integer (MiB) | Physical RAM total (`/proc/meminfo` `MemTotal`) |
| `mem_used_mb` | integer (MiB) | `max(0, mem_total_mb - mem_available_mb)` |
| `mem_available_mb` | integer (MiB) | Kernel `MemAvailable` |
| `swap_total_mb` | integer (MiB) | Total configured swap (`SwapTotal`) |
| `swap_used_mb` | integer (MiB) | `max(0, swap_total_mb - SwapFree)` |
| `cpu_pct` | number (`0.0–100.0`) or `null` | Average busy CPU percentage across the interval from `/proc/stat` deltas (`null` on first sample or after reboot) |
| `vcpus` | integer or `null` | Online logical CPUs (`os.cpu_count()`) |
| `load_1m` | number or `null` | 1-minute load average (`/proc/loadavg`) |
| `load_5m` | number or `null` | 5-minute load average (`/proc/loadavg`) |
| `disk_root_total_mb` | integer (MiB) or `null` | Root filesystem (`/`) total space |
| `disk_root_used_mb` | integer (MiB) or `null` | Root filesystem (`/`) used space |
| `disk_data_total_mb` | integer (MiB) or `null` | Separate data disk (`storage.data_dir` or separate vault mount) total space, or `null` on a single-disk install |
| `disk_data_used_mb` | integer (MiB) or `null` | Separate data disk used space, or `null` on a single-disk install |
| `chats_live` | integer (`>= 0`) | Live resident chat sessions held in broker memory |
| `chats_busy` | integer (`>= 0`) | Chat sessions currently running a turn / asking / background work, or queued thread turns |
| `jobs` | integer (`>= 0`) | Active `loam-engineer-*` units |
| `wakes` | integer (`>= 0`) | Active `loam-wake-*` units |
| `crons` | integer (`>= 0`) | Active `loam-cron-*` and reviewer units |
| `browser_up` | boolean | Whether `loam-browser` is awake (read from `/run/loam-browser/state.json` without waking Docker or the proxy) |
| `owner_services_up` | integer (`>= 0`) | Running non-core owner services: live `loam-app@<name>.service` cgroups excluding core apps (`loam-ui`, `files`, `vault`, configured `edge.app`), plus live `always_on` plugin servers from `<state>/plugins/gateway.json` (count only, never names; never wakes Docker or runs `systemctl`) |
| `owner_sites` | integer (`>= 0`) | Non-expired site records in `<state>/sites/*.toml` plus non-core app records in `deploy/*/deploy.toml` (count only, never names) |

## Event fields

Recorded as events occur (`type: "event"` on disk; placed in `events` on the
wire without `type`). Limit events are projected from `<state>/limits/events.jsonl`
with all subject/identity fields stripped; kernel OOM kills are counted from
`/proc/vmstat` (`oom_kill` delta); service restarts are detected from
`(btime, pid, starttime)` transitions of `broker`, `session_host` and `app`.

| Field | Type | Description |
|---|---|---|
| `seq` | integer (`>= 1`) | Monotonic per-box sequence number |
| `ts` | string (UTC ISO-8601) | Event timestamp (`YYYY-MM-DDTHH:MM:SSZ`) |
| `kind` | enum string | `chat_wait`, `chat_timeout`, `chat_evicted`, `turn_retried`, `run_wait`, `run_skipped`, `job_queued`, `summary_deferred`, `pressure_episode`, `sustained_swap`, `oom_kill`, `kernel_oom`, or `service_restart` |
| `outcome` | enum string or `null` | `started`, `cancelled`, `timed_out`, `evicted`, `retried`, `recovered`, `failed`, `deferred`, `ended`, `killed`, `restarted`, or `reboot` |
| `mem_available_mb` | integer (MiB) or `null` | Box `MemAvailable` at the event |
| `swap_used_mb` | integer (MiB) or `null` | Box swap used at the event |
| `duration_s` | number (`>= 0`, optional) | Wait or episode duration in seconds |
| `idle_s` | integer (`>= 0`, optional) | Seconds a session was idle before memory eviction (`chat_evicted`) |
| `trigger` | enum string (optional) | `chat_start`, `waiting_run`, or `under_reserve` (`chat_evicted`, `summary_deferred`) |
| `count` | integer (`>= 1`, optional) | Kill count (`oom_kill`, `kernel_oom`) |
| `service` | enum string (optional) | `broker`, `session_host`, or `app` (`service_restart`) |

## Configuration (`/etc/loam/loam.toml`)

```toml
[usage]
collector_url = "https://usage.example.com/v1/ingest"
token_file = "/etc/loam/usage-collector.token"   # 0640 root:<user>, single-line bearer token
box_id = "box_01j9example"                       # opaque box identifier
plan = "4gb"                                     # optional plan size label
```

- `collector_url` must use `https://` (`http://` is accepted only on loopback
  `127.0.0.1`, `::1` or `localhost` for local tests). URLs with embedded
  credentials, query strings or fragments are rejected.
- Both `collector_url` and a readable, non-empty `token_file` are required. If
  either is missing or invalid, sending stays off and no connection is made.
- Redirects (`3xx`) are never followed so bearer tokens cannot be redirected.

## Wire format (`loam-usage/1`)

- **Method & URL:** `POST <usage.collector_url>`
- **Headers:**
  - `Authorization: Bearer <token>`
  - `Content-Type: application/json; charset=utf-8`
  - `User-Agent: loam-usage/<loam_version>`
- **Batch bounds:** at most 250 records and 256 KiB per request, sent in
  strictly ascending `seq` order every ~5 minutes on a dedicated daemon thread
  (10 s HTTP timeout; never blocks chat handling).
- **Request body:**

```json
{
  "format": "loam-usage/1",
  "box_id": "box_01j9example",
  "plan": "4gb",
  "loam_version": "0.2.38",
  "sent_at": "2026-11-24T17:20:00Z",
  "samples": [
    {
      "seq": 101,
      "ts": "2026-11-24T17:15:00Z",
      "interval_s": 60,
      "mem_total_mb": 3920,
      "mem_used_mb": 1142,
      "mem_available_mb": 2778,
      "swap_total_mb": 2048,
      "swap_used_mb": 0,
      "cpu_pct": 3.4,
      "vcpus": 2,
      "load_1m": 0.12,
      "load_5m": 0.09,
      "disk_root_total_mb": 78120,
      "disk_root_used_mb": 11240,
      "disk_data_total_mb": 49152,
      "disk_data_used_mb": 812,
      "chats_live": 1,
      "chats_busy": 0,
      "jobs": 0,
      "wakes": 0,
      "crons": 0,
      "browser_up": false,
      "owner_services_up": 0,
      "owner_sites": 0
    }
  ],
  "events": [
    {
      "seq": 102,
      "ts": "2026-11-24T17:16:12Z",
      "kind": "chat_evicted",
      "outcome": "evicted",
      "mem_available_mb": 612,
      "swap_used_mb": 128,
      "idle_s": 145,
      "trigger": "waiting_run"
    }
  ]
}
```

- **Response & idempotency:**
  - Any `2xx` response acknowledges the batch. If the JSON response includes an
    integer `"acked_seq"` within the batch's range, the cursor advances to that
    value; otherwise it advances to the highest `seq` in the batch.
  - Receivers should deduplicate on `(box_id, seq)` (e.g. `INSERT OR IGNORE` /
    `ON CONFLICT DO NOTHING`) because a dropped response after server write will
    be retried with the same `seq` values.
  - Any non-`2xx` status or network error leaves `acked_seq` untouched and
    backs off exponentially (5 min up to 60 min; `401`/`403` backs off at least
    30 min; `429` honors integer `Retry-After` up to 60 min). Once the
    collector recovers, the pusher streams up to 20 catch-up batches per beat
    across archived `.jsonl.gz` segments and `usage.jsonl` until caught up.
