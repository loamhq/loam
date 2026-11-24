# Updating Loam: `loam update`

An install moves to a newer release in one tap (Settings › About Loam) or one
command (`loam update apply`). Only what Loam ships is replaced; everything
that is the person's is never touched. Added 2026-11-14 (first release,
job L13). The rules are all in `lib/loam_update.py`; the command is
`bin/loam-update` (`loam update …` through `bin/loam`).

## What an update replaces, and what it never touches

| Replaced (ships) | Never touched (the person's) |
|---|---|
| the checkout (`paths.root`'s repository), moved to a release tag | every agent workspace (`paths.workspaces`), the starter agent's included |
| `<vault>/<system>/Loam/`, replaced whole from the library (`docs/library/`), except its `data/` | the rest of the vault, `System/skills/` included, and `<system>/Loam/data/` |
| | Yours (`paths.plugins`) |
| | `/etc/loam/` |
| | `paths.state` (except `<state>/update/`, the updater's own), `paths.logs`, jobs, provider homes |
| | `<workspaces>/_shared/reach.md` |

What the new code then regenerates is the generators' own output, as on any
install: the user units (`gen-units`), each live agent's provisioned files
(`provision`), the provider CLIs at the versions the release was tested
with (`provider-pin apply`, `lib/loam_pins.py`: each build installed beside
the others under `~/.local/share/<cli>/versions/` and the link in
`~/.local/bin/` moved; sign-ins and provider homes untouched), the
providers' memory pins (`provider-memory pin`).
An installed reviewer is also regenerated from the incoming reviewer plugin's
instructions during the library step.

## The two kinds of install

Told apart by the checkout, not by a setting:

- **Stock.** HEAD holds no commit the running release's tag lacks. The
  update checks out the tag (detached), or fast-forwards the branch HEAD
  is on.
- **Development** (a contributor's box). HEAD has local commits. The
  update merges the tag into the branch. The local commits stay, and the
  merge commit is named `Merge Loam release <v>`. A box that develops
  Loam itself has `origin` at the development repository: its landings
  push `main` there (through the push gate, `CONTRIBUTING.md`), and the
  update reads only `origin`'s release tags, never its `main`. A tag the box
  cut itself is already in its `main`, so it reads as not newer.

"The running release" is the `version` in `manifest.json` at the
repository's top. A checkout with no `manifest.json` is not a release
install and is never updated this way.

**What the installer must leave** (L12): a git clone of `loam`, with its
`origin` set to where releases come from, checked out at the release tag.
It must keep install state outside that checkout, apart from the generated
engineer harnesses. A tarball cannot be updated.

## Persona maps

The incoming release creates a map for each persona that has none during
the library step. The map points
to any existing instruction files by the runs on which the agent should
read them. Existing maps and the files they point to keep their contents.

## A change to Loam's own parts (2026-11-17)

An install's agent can brief its own engineer to change Loam itself. What
happens to that change:

- **Every install has the engineer** (since 0.2.2). Loam ships its role and
  project file in `engineer/install/`. `bin/provision --engineer` (the
  installer's starter step, every update's `agents` step, and any agent's
  provision) seeds them into Loam files at `<vault.system>/engineer/`
  (`role.md`, `projects/loam.md`) — or `<vault.projects>/Loam/engineer/` on
  installs with an explicit `vault.projects` — and renders the engineer beside the code
  from the vault copies, verbatim. The vault copy is the owner's to change.
  A newer release replaces it only while it is still exactly what was last
  seeded (the hashes are in `<paths.state>/engineer/seeded.json`); a copy
  the owner changed, or one provision never wrote, is kept, and provision
  says so. The rendered files (`engineer/CLAUDE.md`, `role.md`,
  `project.md`, `agent.json`, `.claude/`) are put in the checkout's
  `info/exclude`, and the clean-tree rule passes over them.

- **The first engineer job on Loam makes the install's own `main`.** A
  release install is detached at its tag with no branch, so a job's tree
  could not be made at all before this ("Remote branch main not found").
  The job's setup makes `main` at the release the install runs, puts HEAD on
  it (nothing in the tree moves) and sets `loam.localMain=true` in the
  checkout's git config (`ensure_local_main`, lib/loam_worktree.py).
- **That `main` is the install's alone.** A landing on it is tested like
  any other, and is never pushed, and the canonical is never fast-forwarded
  to `origin/main` (Loam's development, unreleased). The receipt says the
  change stayed on the install. A box that develops Loam already has a
  `main`, never gets the mark, and lands and pushes as before.
- **An update keeps it.** With commits of its own the install reads as
  *development*: the update merges the release into `main`, and the local
  commits stay. If the release changes the same lines, the update is refused
  (`conflict`) before anything moves, and the agent's notice names the files
  and the choices it must offer the owner: send the change to Loam's makers
  (`loam report bug --patch`), or with their yes revert the conflicting
  commits (`git revert`, so the history keeps them), update, and make the
  change again, as a plugin where a plugin can do it. Nothing is ever
  discarded without the owner's word.
- **An uncommitted edit** (by hand, outside a job) refuses the update
  (`dirty` on a stock install, `uncommitted` on a development one) and is
  left exactly as it is.

The skills say the same to every agent: a customisation is a plugin in the
owner's plugins folder wherever a plugin can do it, because a plugin
survives every update untouched; Loam's own code is changed only for what no
plugin can reach (`loam-building-a-plugin`, `loam-filing-a-bug-or-feature`).
`tests/test_local_changes.py` makes an install the way the installer does
and proves each line above.

## The clean-tree rule

The update refuses, and hands the job to the person's agent, when:

- **(a) stock:** `git status --porcelain --ignored` lists a file outside
  the generated harness exceptions, or a staged or tracked change.
  The exceptions cover the platform engineer's harness and these four
  generated app files: `app/CLAUDE.md`, `app/role.md`, `app/project.md`,
  and `app/.claude/settings.json`. The refusal lists the files under code
  `dirty`.
- **(b) development:** the merge with the tag would conflict. This is found
  with `git merge-tree` before anything moves. Code `conflict`; the files
  are listed.
- **Development, uncommitted:** `git status --porcelain` (ignored files
  allowed) is not empty, because a merge would mix those changes in. Code
  `uncommitted`.

An updater from 0.2.13 through 0.2.15 can list the generated app harness
itself in a `dirty` refusal. If those four paths are the only blockers,
move those files aside outside the checkout, apply the update, and put
back any harness file the new release did not regenerate.

Other refusals:

| Code | When |
|---|---|
| `no-version` | the checkout has no release version |
| `no-release` | no release has been published yet |
| `no-such-release` | the requested `to` does not exist |
| `not-newer` | the target is not newer than what is running |
| `signature` | the release's signature does not verify against the maintainer keys the running release carries (`reason`: `unsigned`, `not-ssh`, `untrusted-key`, `bad-signature`, `wrong-name`, `cannot-check`; docs/signing.md) |
| `bad-release` | the tag's `manifest.json` names a different version |
| `busy` | an apply or rollback is already running |
| `unreachable` | `origin` could not be read |
| `library-edits` | System/Loam has edits, and the request did not say yes to them (below) |

Every refusal except `library-edits` leaves a notice for the person's
agent (below). The notice names what was found and asks the agent to
explain it and, with the person's yes, put it right. `library-edits` is the
person looking at their own diff, so the agent gets no notice for it.

## A release must be signed (2026-11-19)

Before anything is read from a fetched release, its tag is checked against
`docs/releases/signers` as the running release committed it
(`lib/loam_signing.py`, `docs/signing.md`). A release that does not verify
is refused as `signature` before anything moves. `check` marks it
`blocked`, the page says it could not be verified, and the person's agent is
told it was refused (once per release and reason), never that it is
available. A release that verifies is moved to by the commit its verified
tag object names. `current.signing` (`{keys, checked, message}`) says
whether this install checks releases at all: while the running release
carries no key, nothing is checked, and the message says so.

## System/Loam: shown first, replaced whole, kept

Before the update, `<system>/Loam/` is compared with the library of the
release that is running now (HEAD's `docs/library/`). Any difference is
somebody's edit:

- `edited`: changed files;
- `added`: files that exist only in the vault;
- `deleted`: library files missing from the vault;
- a unified diff of all of them (`shipped/<file>` against `yours/<file>`),
  cut at 400 lines.

`check`, `diff` and `GET /v1/update` show these edits. An apply with edits
waits for a yes (`--yes`, or `accept_library_edits: true`). Whether or not
there are edits, the whole folder is copied to
`<state>/update/backups/<run>/Loam/` before it is replaced. The last five
copies are kept.

**`<system>/Loam/data/` is kept** (since 2026-11-16): the one folder inside
Loam's that is the person's, where plugins keep data that belongs in the
vault (`data/<plugin id>/`; `comments` sidecars and `sharing` link records). It is never in the library, so it is never counted
as an edit; replacing the folder carries it across (`carry_kept` in
lib/loam_skills.py); an update's backup leaves it out; and undoing an update
keeps it as it is now, never as the backup had it, so a note made after the
update is not lost by the undo. `tests/test_sharing_comments.py` proves the
four.

## After the move

Each step runs from the new code, and only when something it reads has
changed between the two commits. "What it reads" means the script, plus
every `lib/loam_*.py` it imports (followed transitively, read from both
commits), plus the folders listed:

| Step | Script | Also |
|---|---|---|
| `library` | always: `bin/library install` (System/Loam, then the skill views) | |
| `units` | `bin/gen-units` (a timer it installs for the first time it also arms, so a release's new timer runs after an update the older release's code ran) | `systemd/`, except `systemd/system/` (still installed by hand) |
| `agents` | `bin/provision`, once for each live agent, then `bin/provision --engineer` | `engineer-template/`, `engineer/install/` |
| `providers` | `bin/provider-pin apply --no-restart` | |
| `provider-memory` | `bin/provider-memory pin` | `lib/loam_pins.py` |
| `broker` | `bin/session-broker`, `bin/session-host` | `broker/`, `plugins/`, `lib/loam_pins.py` |
| `gateway` | `bin/plugin-gateway` | |
| `deploy` | `sudo bin/gen-deploy` (the web front, the login, the walls: the inbound wall reaches an install that lacks it this way) | `deploy/` |
| `app` | `sudo bin/deploy <app> up <commit>` | `app/`, `deploy/` |

- **The split session services** compare the running revision of each process
  with the target, including the imports in both trees and its unit template.
  Comments and whitespace in regular Python files can leave a service running.
  A changed library docstring can do so when its module and call sites show
  that it is internal. CLI help, decorated functions, class metadata, opaque
  lookups and unreadable inputs are treated as executable changes.
  The revision in the live response identifies what the process loaded. It
  can be older than the checkout after an equivalent change.
  An API change replaces the HTTP process while the provider keeps running.
  A host change closes admission across both units, checks for starts, active
  turns and pending provider work, then replaces the host. A resident provider
  must also have exhausted its normal idle window since its most recent activity
  or event (ten minutes for a thread member, thirty for a classic session).
  The fenced controller waits for the idle reaper and verifies the service has only its host process left.
  Missing session, turn, start or idle evidence defers replacement. A busy install
  defers. The old executable and unit are restored if the replacement fails
  its process identity and health check. Rollback to a combined release
  drains the split services under the same fence before moving the checkout.
- **A combined broker** is restarted by `restart-broker --when-idle`. That waits
  until every session is idle and no thread turn is in flight; after half
  an hour (its ceiling) it defers instead of restarting, and the step fails
  as `deferred`: the apply is undone, and since the broker never restarted
  the undo does not restart it either (`undo-broker` is skipped). The
  wait's lines ("held by iris (working)") become the step's `detail` as
  they come. The step then requires a new main process that answers `/up`
  within 90 s.
- **The gateway** gets `systemctl --user try-restart`.
- **A timer is never stopped or started** (docs/config.md, "Timers: reload,
  never restart"). `gen-units` daemon-reloads.

### A failed apply

A step that fails puts the previous release back:

1. the checkout returns to its old commit (`git reset --hard`, under Loam's
   live lock);
2. System/Loam is restored from the backup, then the skill views are
   refreshed;
3. every regeneration that ran is run again from the old code;
4. the broker is restarted again if it had been restarted, or if its
   restart was the step that failed.

The record's `outcome` is `failed`, with `failed_step`, and `rolled_back`
is true when the undo finished. The message says the old version is
running.

### An apply that stopped without finishing

A run can be killed mid-way, by a reboot or the OOM killer. To cover that,
the rollback point is written to `<state>/update/pending.json` before
anything moves. Three things update it as the run goes: the backup's path,
the merged commit, and whether System/Loam has been replaced yet. It is
removed when the run applies, or when a failure's undo finishes.

While the file stands and no run holds the lock:

- `status` reports the run as `interrupted`;
- it offers a rollback marked `"interrupted": true`;
- `rollback` puts the previous release back from that point. It skips
  restoring System/Loam if the run had not replaced it yet.

### Rollback

`loam update rollback` (or `POST /v1/update/rollback`) puts back the
release the last successful apply replaced, in one command. It refuses
(`moved-since`) when the checkout has moved since that apply, because a
rollback would discard that too. It also refuses `dirty` and
`nothing-to-roll-back`. It restores System/Loam exactly as that apply's
backup has it, the person's edits included. Before doing so, it backs up
the current System/Loam.

## Release notes go to the person's agent

The update never shows the person a changelog. It leaves a notice in
`<state>/update/notices/`. On its 30-second delivery pass, the broker:

- hands it to the agent the owner chose to hear when something in Loam
  needs attention (Settings › Agents; the first agent on a fresh install),
  as a fact (`lib/loam_attention.py`): the notice's `line` is the fact and
  its `note` what it comes with. Nothing is posted in General or anywhere
  else automatically. That agent decides what the owner needs to hear.

For installs with the first-weeks starter marker, available and applied
release notices name the original Hello by its thread ID. Renaming Hello
keeps news there. Deleting the thread sends subsequent news to General.
Older installs keep their previous delivery instructions.
The note names `<state>/update/notes/<version>.md`. The agent reads it and
gets the **For your agent** section inline when the release has one. It
looks at what the owner uses and has asked for, then shares one short
message about the most relevant major change if there is one. A small
change gets a line only if it touches something the owner uses. Developer
details can be skipped, and the owner gets no list of changes. Update
failures and other operational notices keep their usual handling.

A notice is told once: its id is the fact's key, and a key already told
is not told again (`lib/loam_attention.py`). A notice whose file cannot be
removed is logged once (`notice-stuck` in the update log, and the
broker's journal) with the `chown` that fixes it. Until 0.2.1 a notice a
root run had left was posted, and given a paid turn, every 30 seconds.

The notes come from the release's own `docs/releases/<version>.md`. If
an update skips versions, its applied notice also includes agent sections
for those versions from the incoming commit, where available. This lets a
later tag carry revised notes for older tags. Long notes can be read from
the saved file or the Git sources named in the notice. For older releases
without a notes file the fallback is the annotated tag's message, then the
commit subjects since the running release as background for the agent.

| Notice | When | Once? |
|---|---|---|
| `available` | a check finds a newer release | once per version |
| `applied` | an apply landed; names the kept copy if System/Loam had edits, and the rollback command | each time |
| `refused` | an apply was refused (not `library-edits`) | once per version, code and file count |
| `failed` | an apply or rollback failed; says whether the undo finished | each time |
| `rolled-back` | a rollback landed | each time |

## The command

```
loam update                     check; if a newer release can be applied, offer it and apply on a yes
loam update status              the running release, the last check and run, rollback
loam update check [--no-tell]   the newest release; can it be applied now; System/Loam's edits
loam update diff                System/Loam's edits against the running release's library
loam update apply [--to V] [--yes]
loam update rollback
        --json for the record; --foreground runs in this process
```

`apply` and `rollback` start the run as its own transient user unit
(`systemd-run --user --unit=loam-update-<run>`) and then follow it. Started
by the broker, a plain child process would sit in the broker's cgroup and
die in the restart the run ends with. Inside an agent's turn
(`LOAM_SESSION_ID` set), following stops at the broker step, because that
step waits for the turn to end. `--foreground` is what the unit itself
runs; never use it from inside a turn.

Run as root (the shell a person installed from), `loam update` gives
anything in the update state a root run left back to the install's user,
then runs itself again as that user (`runuser`). The checkout and the
state are that user's: git refuses to read a checkout another user owns,
and until 0.2.1 a root run left root-owned files that broke the next run.

Exit codes: 0 done or nothing to do, 1 failed, 3 refused, 4 waiting for a
yes to System/Loam's edits.

## The page contract (U4: the Update control in Settings)

Every route needs the broker's bearer token, like the rest of `/v1`. The
app reaches them through its relay (`rest === '/update'…`). Every answer is
`Cache-Control: no-store`.

### `GET /v1/update`: the state

```json
{
  "current": {"version": "1.0.0", "commit": "…", "branch": null,
              "kind": "stock", "local_commits": 0},
  "check": {
    "checked_at": "2026-11-15T05:11:40Z",
    "latest": {"version": "1.1.0", "tag": "1.1.0", "commit": "…"},
    "available": true,
    "blocked": null,
    "changes": {"added": [], "removed": [], "changed": ["README.md"]},
    "library": {"folder": "…/System/Loam", "any": true, "installed": true,
                "edited": ["README.md"], "added": [], "deleted": [],
                "diff": "--- shipped/README.md\n+++ yours/README.md\n…"},
    "error": null
  },
  "run": { …the last or current run, below… },
  "running": false,
  "rollback": {"to_version": "0.9.0", "from_version": "1.0.0", "at": "…"}
}
```

- `check` is null until the first check.
- `check.available` is worked out against the running version on every
  read. After an apply, it is false without a new check.
- `check.blocked` is a refusal (`{code, message, files}`) that an apply
  would meet now: the clean-tree rule.
- `check.error` is `{code, message}`. Its codes are `no-release`,
  `no-version`, `unreachable` and `error`.
- `rollback` is non-null only while the checkout is still at the commit the
  last apply left.

### `POST /v1/update/check`

This runs a check (a few seconds; it reads `origin`), then answers as
`GET`. The first time a version is seen, the person's agent is told it is
available.

### `POST /v1/update/apply`: `{"to"?: "1.1.0", "accept_library_edits"?: true, "by"?: "<agent>"}`

- **202** `{"run": {…, "outcome": "starting"}}`: the run started as its
  own process. Follow it.
- **409** `{"error": "<sentence>", "refusal": {"code", "message", "files"?,
  "library"?, "to"?}}`: nothing moved. For `library-edits`,
  `refusal.library` is the edits block above. Show the diff, then send
  again with `accept_library_edits: true` once the person says yes.
- **400**: `to` is not a string.
- **503**: the run could not be started as its own unit.

### `POST /v1/update/rollback`: `{"by"?}`

The same answers as apply. Its refusals are `nothing-to-roll-back`,
`moved-since`, `dirty` and `busy`.

### `GET /v1/update/progress`: the run alone

```json
{"id": "20261115-051633-3786eb", "action": "apply", "outcome": "running",
 "phase": "broker", "from_version": "1.0.0", "to_version": "1.1.0",
 "started_at": "…", "ended_at": null, "by": null, "message": null,
 "backup": "…/state/update/backups/…/Loam",
 "steps": [
   {"name": "preflight", "words": "Checking the install", "status": "done", …},
   {"name": "broker", "words": "Restarting the broker when every session is idle",
    "status": "running", "detail": "restart-broker: held by iris (working)"}
 ]}
```

`outcome` is one of:

| Outcome | Meaning |
|---|---|
| `starting` | the run is starting |
| `running` | the run is in progress |
| `applied` | the apply landed |
| `rolled-back` | the rollback landed |
| `refused` | with `refusal` |
| `failed` | with `failed_step` and `rolled_back` |
| `interrupted` | the process ended without finishing (a reboot, a kill); `rollback` is then offered (`"interrupted": true`) and puts the previous release back |

Each step has:

- `words`: the sentence to show;
- `status`: `running`, `done`, `skipped`, `failed` or `refused`;
- `detail`: what it said, or why it was skipped.

Undo steps are named `undo-<step>`.

**Following it.** Poll about every two seconds until `outcome` is neither
`starting` nor `running`. During the `broker` step the broker restarts. For
a moment the relay answers 502 or 503 and the page's own streams drop. Keep
polling; the record is on disk and the new broker serves it. The page
should reload once the run has `applied`, because the app's code may have
changed with it.

## Seams, for tests and rehearsals

- `LOAM_UPDATE_STATE`: the updater's folder (default `<state>/update`).
- `LOAM_UPDATE_RESTART`: a shell command that stands in for the broker step
  (restart and verify).
- `LOAM_UPDATE_NO_UNIT=1`: start a run as a detached process rather than a
  unit.

`tests/test_update.py` builds releases in temporary git repositories. The
job that built this also proved the update on a scratch install of the real
tree, with two local tags.

## Not yet

- **Nothing checks on a schedule.** The agent mentions a new version once a
  check has found it (Settings opening Update, or `loam update check`). A
  daily check timer is one unit away. It waits for the owner's word, because
  "not automatic for now" (11-14) was about applying, and a check is not
  an apply.
- **The app's container** is not rebuilt by an update. The app is not in
  this tree until the cut (L15). When it is, its deploy joins the table
  above as a step that reads `app/`.
- **`systemd/system/`** units that changed are named in the run's
  `warnings` and in the agent's `applied` notice, but they are not
  installed. They still need root and are installed by hand, as today.
