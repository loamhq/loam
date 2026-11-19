# Every log on the box, where it lives, how long it stays

*Written 2026-09-28, when an audit could not count the web app's errors or its
transcription calls because nothing on the box logged a web request. That is
fixed (Caddy's access logs, below); this page is the map, so the next question
of that kind starts here.*

`bin/logs` opens any of these by name (`bin/logs` alone lists the names) and
prints the command it ran, so the command is what you learn. Times below are
the box's; Caddy's `ts` is Unix seconds.

## The journal, which most of this rides on

systemd's journal is persistent (`/var/log/journal/`) with no configuration of
ours: journald's default cap applies, **4 GB for system and user journals
together**, oldest entries dropped first, no age limit. At 3.5 GB on
2026-09-28 it reached back to 2026-05-05, about five months. The box's user is in
`systemd-journal`, so it reads the system journal without sudo.
`journalctl --disk-usage` says where it stands.

## The map

| Log | Where | Kept | Read it |
|---|---|---|---|
| **Web requests, per host** (every host Caddy serves: `loam-ui`, `vault`, `files`, `auth` and each site host) | `/var/log/caddy/access/<host>.log`, one JSON line per request; rolled files beside it, gzipped | Rolled by Caddy at 50 MiB; at most 14 rolled files, none older than 14 days | `bin/logs caddy loam-ui` · `sudo tail -F /var/log/caddy/access/loam-ui.example.com.log` |
| Caddy itself (reloads, certificates, upstream errors) | journal, `caddy.service` | journal | `bin/logs caddy-errors` · `journalctl -u caddy -f` |
| **An app container's stdout** (`loam-ui` and any other app; where the app's own request lines go) | journal, tag = container name | journal: survives a restart **and** a redeploy | `bin/logs app loam-ui` · `journalctl -t loam-ui -f` |
| The login (Authelia) | the container's json-file log | 3 × 10 MB, and gone when the container is recreated | `bin/logs authelia` · `sudo docker logs loam-authelia` |
| The persona browser | the container's json-file log | 3 × 10 MB, as above | `bin/logs browser` · `sudo docker logs loam-browser` |
| The session broker | journal, user unit `loam-session-broker` | journal | `bin/logs broker` · `journalctl --user -u loam-session-broker -f` |
| One persona session's events | `agents/state/broker/sessions/<id>.jsonl` (with its record `<id>.json`) | **forever**: it is the conversation's record, not a log, and nothing prunes it but a fork's duplicate | `bin/logs broker-session <id>` |
| Wakes and crons, one line per run | `agents/logs/wake-<agent>.jsonl` (a cron's line carries `"lane": "cron:<name>"`) | **forever**, append-only; ~15–30 KB per agent a month | `bin/logs wake ada` · `jq -c . agents/logs/wake-ada.jsonl` |
| A wake's stderr and its unit's life | journal, user unit `loam-wake-<agent>` | journal | `bin/logs wake-journal ada` |
| A cron (`<agent>/<name>`) | journal, user unit `loam-cron-<agent>-<name>`; its result line in `wake-<agent>.jsonl` | journal; the line forever | `bin/logs cron <agent>-<name>` |
| The reviewer | `agents/logs/reviewer.jsonl`, one line per run; each run's files in `agents/state/reviewer/runs/<run>/` | **forever** | `bin/logs reviewer` |
| Engineer jobs | `agents/logs/engineer.jsonl`, one line per job; each job's brief, receipt, `result.json` and `session-stderr.log` in `agents/engineer/jobs/<id>/`, and its `release-tag` when it asked for one (below); the unit's stderr in the journal (`StandardOutput=null` on purpose: the CLI's stdout filled the journal once) | line and job directory **forever** (113 jobs, 8 MB on 2026-09-28); unit stderr: journal | `bin/logs engineer` · `bin/logs engineer <job-id>` |
| The provider CLIs at the release's pin (`bin/provider-pin apply`, by hand or as `loam update`'s `providers` step; no timer since 2026-11-19, when the daily follower was retired) | what it did is said on its own output, and in the update's record when the update ran it; Antigravity's week-old conversation files, cleared by `bin/clear-agy-files` after the nightly drift check, one line each in `agents/logs/agy-files.jsonl`. The retired follower's `logs/provider-follow.jsonl` and `state/provider-follow/` are left as they were, read by nothing | **forever** | `bin/provider-pin status` · `jq -c . agents/logs/agy-files.jsonl` |
| The transcript writer | `agents/logs/transcript-capture.log`, a line per capture; the transcripts themselves are in the vault and are not a log | **forever** | `bin/logs transcripts` |
| **The box's memory and disk history** (box RAM and swap; every `loam-*` unit's cgroup memory, and its MemoryHigh where one is set (no agent run has one since 2026-11-16): the broker, any running wake, cron, engineer job or reviewer; used per mount; once a day the `du` of the big holders) | `agents/logs/memsample.jsonl`, one JSON line per sample (`"v": 2`; the older lines without it are the one-night instrument of 2026-09-02), written every 5 minutes by `bin/memsample` from `loam-memsample.timer` | Rotated by the sampler at 4 MiB to `memsample.jsonl.1`, replacing the last: never more than 8 MiB, which is 9–18 days | `bin/logs memory` · `tail -n1 agents/logs/memsample.jsonl \| jq .units` |
| The daily memory check (01:55: headroom, sessions, and from the history above the last 24 h peak per unit and the disk delta per mount and holder) | `agents/logs/memory-check.log`, a timestamped report per run | **forever** (~2 KB a day) | `bin/logs memory-check` · `agents/bin/check-memory` runs it now |
| The nightly drift check (01:40, `loam-check-drift.timer`: `bin/check-drift` whole, via `bin/drift-watch`, which posts one Important `comms.md` line per finding the previous run lacked and a failure-log line if the check itself breaks) | `agents/logs/drift-check.log`, the stamped output and exit code of every run; the last run's findings in `agents/state/drift-watch.json` | **forever** (~1.5 KB a day) | `bin/logs drift-check` · `systemctl --user start loam-check-drift.service` runs it now |
| Image generation, title debugging | `agents/logs/image-gen.jsonl`, `title-debug.log` | **forever** (`title-debug.log` last written 2026-09-03) | `tail agents/logs/<file>` |
| The Claude Code CLI's own session transcripts (every lane's) | `~/.claude/projects/<dir>/<session>.jsonl` | the CLI deletes them after 30 days (its default `cleanupPeriodDays`; we set nothing); the oldest on 2026-09-28 was from 08-25 | `ls -t ~/.claude/projects/<dir>/` |

An engineer job's directory also holds `release-tag` when the job asked for
a release (since 2026-10-02): the engineer writes the tag alone into it, and
the landing, after the merge, the tests and the push of `main`, tags the
landed commit and pushes the tag -- or refuses it, leaving the merge landed,
when it does not equal the `version` in the landed tree's `manifest.json` or
`package.json` (each that exists). What became of it is `result.json`'s
`release_tag` field and the engineer log line's, `{"tag", "state":
"pushed" | "refused" | "failed", "sha", "detail"}`, and a sentence in the
receipt's landing paragraph; `null` on a job that asked for none. The job
prompt in `bin/engineer-job` (`RELEASE_NOTE`) is where an engineer learns
it; `lib/loam_worktree.py` `cut_release` is the rule, `tests/test_release_tag.py`
the proof.

"Forever" means nothing rotates it. Every file in `agents/logs/` together was
1.4 MB on 2026-09-28, so that is a note for later, not a problem.

## What a web request line holds, and what it never holds

Caddy's access record: `request.method`, `request.host`, `request.uri`,
`request.remote_ip` / `client_ip`, `request.headers.User-Agent` (and the other
request headers), `status`, `size` (bytes sent), `bytes_read`, `duration`
(seconds). Never a body. The filter in `lib/loam_deploy.py` (`ACCESS_LOG_SNIPPET`)
takes out everything that can carry a credential: the **query string** (it
becomes `?redacted`, because a share link is `/?share=<token>`), the
**Cookie**, **Authorization** and **Proxy-Authorization** headers, the
**Referer** (a page opened from a share link sends the link as its Referer),
and **every response header** (Set-Cookie is the session). A path is kept, so
don't put a secret in one.

A request line, caused 2026-09-28 with a fake cookie, bearer, Referer and
query, none of which landed:

```json
{"level":"info","ts":1790647237.3585634,"logger":"http.log.access.log3","msg":"handled request","request":{"remote_ip":"203.0.113.7","remote_port":"38906","client_ip":"203.0.113.7","proto":"HTTP/2.0","method":"GET","host":"loam-ui.example.com","uri":"/up?redacted","headers":{"User-Agent":["loam-log-probe/2026-09-28"],"Accept":["*/*"]},"tls":{"resumed":false,"version":772,"cipher_suite":4865,"proto":"h2","server_name":"loam-ui.example.com","ech":false}},"bytes_read":0,"user_id":"","duration":0.002447637,"size":139,"status":302}
```

## Questions this answers

`zcat -f` reads the live file and the gzipped rolled ones alike.

How many loam-ui requests answered 5xx (the window is the log's, up to 14 days):

```sh
sudo sh -c 'zcat -f /var/log/caddy/access/loam-ui.example.com*' | jq -s 'map(select(.status >= 500)) | length'
```

How many transcription calls, and how each ended:

```sh
sudo sh -c 'zcat -f /var/log/caddy/access/loam-ui.example.com*' | jq -r 'select(.request.method == "POST" and (.request.uri | startswith("/api/transcribe"))) | .status' | sort | uniq -c
```

The two above count what crossed the proxy. What the app did inside a request
(which vendor call failed, how long transcription took) is the app's own
stdout, `journalctl -t loam-ui`, in whatever shape the app writes it.

The same by time, for one day:
`... | jq -r 'select(.ts >= (now - 86400)) | ...'`.

The session broker's memory over the history (its `high` is null since
2026-11-16, when the unit's MemoryHigh went)
(`ts` is ISO-8601 with the box's offset here, not Unix seconds):

```sh
cat agents/logs/memsample.jsonl.1 agents/logs/memsample.jsonl 2>/dev/null | jq -r 'select(.v == 2 and .units) | [.ts, .units["loam-session-broker"].cur, .units["loam-session-broker"].high] | @tsv'
```

What grew the disk, day by day (the daily `du` lines):

```sh
cat agents/logs/memsample.jsonl.1 agents/logs/memsample.jsonl 2>/dev/null | jq -c 'select(.holders) | {ts, holders, root: .mounts["/"].used}'
```

## Where this is enforced

- The Caddyfile is rendered by `bin/gen-deploy` from `lib/loam_deploy.py`;
  every host block imports `access_log <its own host>`, and `bin/check-drift`
  reads the live file and names any host that does not.
- Every app service's compose file carries the same `logging:` block as
  `lib/loam_deploy.APP_LOGGING` (journald, tag = container name); the
  generator refuses one that does not, and `bin/check-drift` asks each running
  container, because a log config only changes when the container is
  recreated (`sudo bin/deploy <app> up <commit>`).
- The runtime's own two containers (Authelia, the browser) are still on the
  daemon's json-file default. Moving the login to the journal means
  recreating it, and its sessions are in memory, so that is one fresh login
  on every device; it was not done as a side effect.
