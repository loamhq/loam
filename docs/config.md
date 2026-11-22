# The install's configuration: `/etc/loam/loam.toml`

One file says where everything of one install is, who it is for, and when
it is. Every part of the runtime reads its paths, its user, its owner, the
vault's folder names, its time zone and its commit identity from it, through
`lib/loam_config.py`. Nothing in `bin/`, `lib/`, `mcp/` or `hooks/` names a
place on a particular machine. Added 2026-11-14 (first release, job L1).

- **Where it is.** `/etc/loam/loam.toml`, or the file `$LOAM_CONFIG` names
  (the tests and a rehearsal use that). It is root's, mode 644, and holds no
  secret. It names the files that do (`paths.secrets_env`, `paths.wake_env`).
- **A missing key** takes the fresh install's value (the table below). A
  missing file means every key does. On an install that predates the file,
  the file carries that install's own values, so nothing moved when the
  code started reading it.
- **Reading it.** `bin/loam-config show` prints every key as this install
  resolves it. `bin/loam-config get paths.vault` prints one, for shell
  scripts.

## The keys

| Key | What | Fresh install |
|---|---|---|
| `user` | the Unix user the runtime runs as | the running user |
| `timezone` | the owner's zone: the clock line, the engineer's job ids, the cron grammar's `PT` until L3 | `UTC` |
| `owner.id`, `owner.name`, `owner.link` | the person, on the wire and in files (`owner.link` is the provenance value Files writes) | `owner`, `the owner` |
| `paths.home` | the user's home | from the password file |
| `paths.root` | this checkout, the live runtime | this file's own checkout |
| `paths.vault` | the record | `~/vault` |
| `paths.workspaces` | the agents' workspaces | `~/ai-labs` |
| `paths.projects` | project engineers' checkouts | `~/projects` |
| `paths.plugins` | the Yours plugin layer | `~/plugins` |
| `paths.state` | everything the runtime writes: the broker, locks, threads, worktrees, schedules, … | `~/.local/state/loam` |
| `paths.logs` | its logs | `<state>/logs` |
| `paths.jobs` | the platform engineer's job folders | `<state>/jobs` |
| `paths.reviewer_state` | the reviewer's notes and memory (its own key: `reviewer/`'s committed config names it) | `<state>/reviewer` |
| `paths.secrets_env`, `paths.wake_env` | the key files (wake.env: the agents' OAuth token) | `~/.config/loam/` |
| `paths.mcp_venv` | the MCP servers' Python | `~/.local/share/loam/mcp-venv` |
| `paths.host_history` | the host's own setup, kept (root's alone; docs/host-history.md) | `/var/lib/loam/host-history` |
| `paths.pycache` | Python's bytecode cache (`PYTHONPYCACHEPREFIX`). Nightly housekeeping removes bytecode whose source directory is gone or whose last access and modification are both over 14 days old. `bin/sweep-pycache --dry-run` previews the sweep. | `~/.cache/loam/pycache` |
| `bin.claude`, `bin.codex`, `bin.agy`, `bin.uv` | the CLIs | `~/.local/bin/<name>` |
| `vault.inbox`, `vault.boards`, `vault.general_board`, `vault.conversations`, `vault.projects`, `vault.attachments`, `vault.system` | the vault's own folders, relative to it | `Inbox`, `Inbox/boards`, `Inbox/boards/general.md`, `Conversations`, `Projects`, `Attachments`, `System` |
| `git.autocommit.name`, `.email` | the identity of the 15-minute vault, workspace and plugin commits (`bin/autocommit`, docs/backup.md) | `loam`, `loam@<user>` |
| `git.autocommit.max_file_mb` | a file larger than this many megabytes is kept out of those commits and named instead, so a git host's size limit never stops a backup (docs/backup.md) | `100` |
| `git.identity.name`, `.email` | the Loam HQ bot that makes releases and the `loam` repository's root commit. Never a person's address. | unset until that bot exists |
| `firewall.open` | ports the inbound wall lets in beside SSH, 80 and 443, as `"22000/tcp"`, `"21027/udp"` or a bare number for TCP (deploy/README.md, "The inbound wall") | absent: none |
| `firewall.manage` | `false` takes Loam's inbound wall out, for a server whose provider's firewall does the job; a firewall the server already has (ufw, firewalld) is left alone either way | absent: `true` |
| `engineer.briefers` | the personas who may brief the platform engineer, a list of names (`["ada"]`); a project engineer is open to every persona whatever this says (lib/loam_engineer.py, "Who may brief which project") | absent: every persona |
| `owner.legacy_ids` | ids an install used for its owner before `owner.id` existed; records that carry one still read as the owner's | absent: none |
| `deploy.config` | the deployment configuration (domain, `[moved]`, `[[forward]]`) of a box set up by hand, kept outside the checkout so an update never replaces it; same format as `deploy/loam.toml` | absent: `deploy/loam.toml` |
| `app.avatar_colours` | avatar colour overrides (a table of agent id = palette token), passed by `bin/deploy` to the app | absent: no override |
| `app.speech_voices` | the install's agents' read-aloud voices (`agent=Voice:character;...`), read once since 0.2.12 to seed Read aloud's own `voices` setting (`lib/loam_to_market.py`); change voices there, or on an agent's profile | absent: Iris's voice |
| `feedback.repo` | the repository whose new-issue page `loam report` links a bug, a feature request or an idea to (lib/loam_feedback.py) | absent: `loamhq/loam` |
| `keys.providers` | provider definitions for this host’s plugins, including optional key checks and named projects (docs/plugins.md, “Keys”) | empty |
| `marketplace.list`, `marketplace.repo` | the marketplace's list (an https URL; `""` turns the section off) and the repository a listing is asked for in (docs/plugins.md, "The marketplace") | absent: `loamhq/marketplace`'s `list.json` on `main` |
| `edge.*` | an install's name on the web: its domain, address, the app at the domain itself, the records it serves, how DNS is asked, local certificates for a rehearsal, the DNS gate, and `own_domain` when the domain is the owner's own rather than a name under Loam's (first release L12; 0.2.8; docs/install.md) | absent: the deploy layer reads `deploy/loam.toml` and serves every record, as the first install always has |

`lib/loam_broker` and `lib/loam_engineer` re-export the paths under their
historical names (`AGENTS_ROOT`, `AI_LABS`, `VAULT`, `STATE_DIR`, …), so
modules that import those two did not change.

## What uses the keys besides Python

- **Units.** `systemd/` holds templates. Each path in them is a token
  (`@ROOT@`, `@STATE@`, `@LOGS@`, `@WAKE_ENV@`, `@UNIT_PATH@`, …;
  `lib/loam_units.py` lists them). `bin/gen-units` installs the rendering,
  and `bin/check-drift` compares against the same rendering. The units
  generated elsewhere (`bin/provision`, the schedule reconciler) fill the same values. `systemd/system/` is still installed by
  hand.
- **Deploy records.** `static_root` and `checkout` in `deploy/*/deploy.toml`
  take `@VAULT@`, `@WORKSPACES@`, `@PROJECTS@`, `@ROOT@`, `@STATE@`,
  `@HOME@` (`loam_config.expand`).
- **Shell.** The hand-made autocommit scripts (`bin/vault-autocommit`,
  `ai-labs-autocommit`, `plugins-autocommit`) call `bin/loam-config`.
- **The MCP servers' interpreter.** Committed config (the reviewer's
  `.mcp.json`) names `bin/mcp-python`, which execs `paths.mcp_venv`, so
  moving the venv changes nothing tracked.

## Moving the state out of a checkout: `bin/move-state`

The first install predates the file. Its runtime wrote `state/`, `logs/`,
`engineer/jobs/`, `secrets.env`, `wake.env` and `mcp/.venv` inside this
checkout. `bin/move-state` moves them to the fresh-install layout, once:

1. builds the new venv;
2. waits until no engineer job, wake, cron or review runs, no broker
   session is working or asking;
3. stops the timers, the broker and the plugin gateway;
4. renames each place (one filesystem, so each move is a rename);
5. carries what records the old paths: Codex's thread table, and Claude
   Code's session folders for job trees, so a continuation still resumes;
6. rewrites this file;
7. regenerates units, agents' files, cron units and the Caddyfile;
8. starts everything again, then verifies.

If anything fails in steps 3 to 7, everything is put back. `move-state plan`
says what it would do and what holds it. `move-state verify` runs the
checks alone. After the move, `git status --porcelain --ignored` in the
checkout is empty: bytecode goes to `paths.pycache`, set for units, shells,
cron and sudo.

## The owner on the wire (2026-11-14, first release L2)

The app learns who the owner is from the broker and never assumes it.

- **`GET /v1/owner`** (the page's relay: `rest === '/owner'`) answers
  `{"id": "<owner.id>", "name": "<owner.name>", "accepts": [...]}`.
  - `id` is what the runtime writes for the owner: as a thread message's
    `author`, an Activity item's `actor`, a schedule change's actor, a
    plugin's `installed_by`.
  - `accepts` is every id the broker takes as the owner's: `owner.id` and
    any `owner.legacy_ids` the install's config names (a new install has none).
  - `name` is what to show.
- **Coming in**, a request that names the owner (`by`, `actor`) may say
  `owner.id`, say a legacy id, or say nothing; all three mean the owner. A
  persona's `by` is its own name.
- **Records already on disk** keep the id they were written with, so the
  app compares an author against `accepts`, never against a literal.
- **Two wire names name a kind, not a person**: a setting's `reach` value
  `"owner"` (only the owner's hands) and Activity's actor kind `"owner"`.

## The zone (2026-11-14, first release L3)

`timezone` is the owner's IANA zone. Schedules end with a zone token:
`daily at 05:00 PT`. The install writes its own token (`timezone_token`,
derived: `PT`, `MT`, `CT`, `ET`, `UTC`, or the IANA name itself) and
accepts its own, those short names, or any IANA zone spelled out. `PT`
always means America/Los_Angeles: every record before this day says it.
Every calendar timer carries its zone on its `OnCalendar=` line, so no timer
leans on the box's zone.

## Timers: reload, never restart (2026-11-14)

Starting a timer that has stood clears its last trigger. systemd 252
(`src/core/timer.c`, `timer_start`) then dates the next elapse of a timer
without a stamp file (every `Persistent=false` one) from the unit's previous
activation. When that activation is older than the timer's last calendar
time, the next elapse is already past and the timer fires on the spot.
That is what fired every wake, the reviewer and the Remote Control recycle
(retired 2026-11-19) when the state
move restarted the timers, and it was reproduced on `loam-headroom.timer`.

- **Tooling never stops and starts a standing timer.** A changed timer is
  moved by `systemctl --user daemon-reload` alone, which keeps its last
  trigger. `gen-units`, `provision`, the schedule
  reconciler, `move-state` and the installer and updater to come all follow
  this rule.
- **A lane checks for itself.** The wake (reflection and cron), the
  reviewer call `loam_due.not_due()` first.
  A run its timer started when none of the timer's calendar times fell in
  the ten minutes before goes no further, with a line in its journal and
  in `logs/not-due.jsonl`. A run started by hand or by "Run now" is never
  refused.

## Provider memory (2026-11-14, first release L8)

Memory is Loam's on every provider. `lib/loam_provider_memory.py` pins each
provider's own memory off and checks its stores; `bin/provider-memory pin`
is what the installer runs.

- **Claude Code** (user settings): `autoMemoryEnabled: false`,
  `autoDreamEnabled: false`, and `env.CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`,
  the last also in the user manager's environment.
- **Codex** (`CODEX_HOME/config.toml`): `features.memories`, `.chronicle`
  and `.external_agent_memory_import` false; `[memories]` with
  `generate_memories` and `use_memories` false; `history.persistence = "none"`.
- **Antigravity** has no setting that turns its memory off, so for it
  "pinned off" means "checked empty". Its per-conversation files
  (`brain/`, `implicit/`, `conversation_summaries.db`) are accepted working
  state (2026-11-16): counted for the log, never said. What is said is an
  entry under its tree of a kind not known on 2026-11-16
  (`AGY_KNOWN`), which is what a store of its own would look like.
  Those files are cleared after a week by `bin/clear-agy-files`, which
  the nightly housekeeping runs (`lib/loam_agy_files.py`): an engineer job's or reviewer run's a week
  after its last job, and every other conversation, a persona's included,
  once nothing has touched it for a week (2026-11-16). A thread whose
  member's conversation was cleared starts that member on a fresh session,
  given the thread's recent exchange and its summary.

The checks run at every session start, after `provider-pin apply` moves a
CLI, and on `bin/provider-memory check`. A store that grew is told once to
the agent that hears when something in Loam needs attention
(lib/loam_attention.py, 2026-11-19); never in a conversation's thread
(2026-11-16) nor on the board.

### Makers' report relay

`feedback.relay` is the full HTTPS intake URL. Leave it absent until a
maintainer operates an approved endpoint. The default preview offers the
owner's GitHub link. For a private rehearsal use HTTP on loopback.

On the maintainers’ install, `bin/report-relay` binds only to 127.0.0.1
and prints its URL. It dry-runs issue creation by default.
`--file-issues` switches to the GitHub App installation for
`feedback.repo` and requests only issues-write for that one repository.
The upstream labels are the report kind and `sent-from-Loam`. The filing
relay creates missing labels when it receives the first approved report. The relay
accepts a versioned Loam preview, rechecks privacy, and limits the UTF-8
request to 64 KiB, five submissions per random install ID per day and
200 total per day. A public client format cannot authenticate an install.
No route, service, or DNS entry is created by installing Loam.

Requests are recorded in the maintainer's state under `report-relay/`.
A retry returns the original issue. If GitHub's response was lost, the
relay holds the request for verification. Reconcile the issue against
GitHub before clearing a pending row or recording its issue URL.

The install keeps approved issue links in `reports/reports.sqlite3` in
its own state. Nightly housekeeping reads the public issue and comments
without credentials. A closed issue labelled `fixed-in:0.2.19` yields
release news when the matching version tag exists. Ordinary closes stay
quiet. A new comment from a repository owner, member or collaborator
produces one notice with a link to the reply.
