# What this project knows (the runtime)

Started fresh for the first release, 2026-11-15. Loam was built for months
on one person's server before it was cut into this repository; that history
is not here. What it taught that a newcomer needs is below, and the rest is
in the code's comments and `docs/`. Add to this file as you learn by
building: what you found, which premise expired when, what a change costs.
Write for a reader you have never met.

**This file ships.** It is in every release and in the public history, so
write here only what is true of Loam on any install. Notes about one
install's own machine -- its hosts, its people, its migrations, its remotes
-- go in `engineer/memory/box.md` beside this file, which the checkout keeps
untracked (`.git/info/exclude`) and which never leaves the box with the code.

## The shape

- **One config file.** `/etc/loam/loam.toml` (or `$LOAM_CONFIG`) holds every
  path, the user, the owner, the vault's folder names, the time zone and the
  commit identity, read through `lib/loam_config.py` (`docs/config.md`).
  Tests and rehearsals point `LOAM_CONFIG` at a scratch copy.
- **State is outside the checkout.** `paths.state`, `paths.logs` and the
  jobs live elsewhere, because `loam update` refuses a checkout with any
  file a release did not put there (`git status --porcelain --ignored`
  empty). Entry points set `sys.dont_write_bytecode` and units carry
  `PYTHONPYCACHEPREFIX` for the same reason.
- **Generated files have two homes**, the live file and its generator.
  `bin/check-drift` compares them; the generator is the truth.
- **What ships and what is the person's** is `docs/update.md`: an update
  replaces the checkout and `<vault>/System/Loam/` and nothing else.

## Proving things

- **A whole install can be rehearsed on a throwaway VM** with
  `install.sh --source <bundle> --name sam --base loam-proof.test --resolver system
  --local-certs --address <ip>`; `docs/install.md`, "How it was proved",
  has the recipe, including booting systemd in a PID namespace where the
  VM's PID 1 is not systemd.
- **A scratch install can run the real provision and a real broker** with a
  `LOAM_CONFIG` whose `paths.home` is scratch, and `XDG_RUNTIME_DIR` plus
  `DBUS_SESSION_BUS_ADDRESS` pointed at an empty folder, so no
  `systemctl --user` call reaches the real user manager.
- **Caddy orders `handle /path` ahead of `handle @named`** in one block,
  wherever they are written; check with `caddy adapt`, not by reading.
- **`PrivateTmp=yes` breaks anything that hands Docker a path in /tmp**: the
  daemon resolves it in the host's /tmp. No unit that runs `gen-deploy` may
  have it.
- **uv reads `uv.toml` from its working directory**: commands run as another
  user start in that user's home.
- **Redact by value shape, not by key name.** A redaction keyed on
  `token|key|secret` misses `pem`.

## Working on a live install

- **Inside a job, the checkout and the other job trees are read-only bind
  mounts.** The checkout's untracked provisioned files and a kept tree's
  removal both go through `systemd-run --user --wait --pipe`, which runs in
  the host mount namespace; so does any change to the checkout's
  `.git/config`.
- **A tree can be proved at the live path without moving anything:**
  `sudo unshare -m --propagation private`, `mount --bind NEW ROOT`, then run
  `check-drift`, `gen-deploy --dry-run` and a `docker compose config` as the
  install's user inside. The generators read the new code with every live
  path true, and nothing outside the namespace sees it.
- **A change to the generators must be re-rendered, not just compared.**
  `drift-watch` compares against the previous night's state, so a
  comment-only change to every rendered unit is posted as new drift unless
  the units are regenerated (`gen-units`, `gen-rc-units`, each agent's wake
  unit, `provision --reviewer`) and `loam-check-drift.service` is run once.
  Comment-only unit changes need a `daemon-reload` and no restart.
- **`gen-rc-units` has no `--help`.** Any argument starting with `-` is
  ignored and the run is live: `gen-rc-units --help` writes every rc unit.
  Use `--dry-run`.

## 2026-11-16 — a correct record can still draw in the wrong order

- **When the page and the record disagree about order, replay the broker's
  own stream through the page's reducer before guessing.** The thread
  stream gives every `message.done` in posting order with rising ids
  (`tests/test_thread_completion_order.py`, through `thread_stream` itself;
  set `LOAM_ORDER_DUMP` to keep the events). loam-ui's `applyThreadEvent`
  can be imported in plain node from its `public/` and fed those events. That
  is how the "final reply above my second message" report was pinned to the
  page: it put a reply into the slot it reserved at `turn.started`, above
  anything sent during the turn.
- **A reply's place in the record is its `result`, not its first word.**
  Words streamed before a person's message can belong to a reply posted after
  it (the end-of-turn race). Expect that order; it is the record's.
- **Chromium for loam-ui's browser tests** needs a dozen libraries the box
  lacks: `apt-get download` + `dpkg-deb -x` into the job's scratch, plus a
  wrapper that sets `LD_LIBRARY_PATH`, named by `LOAM_CHROME` (loam-ui's own
  memory has the list). Playwright 1.65.1 matches the cached chromium-1234.
- **The suite must not read the box's engineer settings.** `tests/run-all`
  gives every test an empty `LOAM_HOST_SETTINGS`. Without it, a test that
  builds a project engineer resolves its runtime from the live
  `state/broker/settings.json`: when the owner made Antigravity the default
  mid-landing (2026-11-16), `test_engineer_walled` launched a real Antigravity
  session in place of its Claude stub, and a branch failed for a setting.
  Inside a job session `test_engineer.py` also fails (it writes the real,
  read-only jobs directory); the landing runs it outside and it passes.

## Where a landing pushes

- **`origin` gets `main` on every landing; `backup`, when a checkout has
  one, gets it too** (`lib/loam_worktree.py`, `_land_locked`,
  `push_backup`). They differ on purpose: `lib/loam_update.py` reads release
  tags from `origin`, so an off-box copy is never named that. `backup` takes
  `main` only, by its `remote.backup.push` refspec, and is never fetched;
  `refs/loam/backup-pushed` marks what it last took, so the recovery sweep
  retries a failed backup push without touching the network otherwise.
- **A checkout whose `origin` is shared turns the push gate on**
  (`git config loam.pushGate true`, and `loam.pushDeny` naming a deny list in
  `bin/scrub-check`'s format, kept outside the repository). Every commit
  `origin` lacks must then be the checkout's own identity, with no denied
  word in what it adds, its paths or its message; otherwise the push is held
  and `main` stays landed locally. A word added and later removed is still
  held: the history keeps it.
- **A history rewritten before its first push** leaves running jobs on the
  old commits. The landing notices and replays only the job's own commits
  onto the new `main` (`replay_if_rewritten`), so a merge never brings the
  old history back. **It reads the canonical reflog of `main`, not the
  job's recorded base**: a commit the branch would bring in that `main`
  once pointed at and no longer contains marks the old history, and the
  newest one is where the job's own commits begin. The base alone failed
  on 11-16. A replay moves the branch off the base and the record is never
  rewritten, and mid-job landings make the branch *be* `main`. So after a
  second rewrite the base was in neither the branch nor `main`, and the
  merge brought two scrubbed commits back. Reflog expiry (90 days) bounds
  it; the base is still asked as a fallback.
- **The replay is cherry-picks, planned once** (`replay_plan`). It skips
  any of the job's commits whose change `main` already has, by patch id,
  and drops any pick that still comes out empty. `rebase --exec 'commit
  --amend'` was wrong: an emptied pick stopped it ("would make it empty"),
  and a dropped pick left the amend rewriting the commit before it.
  `rev-list --cherry-mark main...tip` marks the matches with `=`.
- **A landing at session end runs the launcher's code as it was when the
  job started**, not what the job landed mid-job. To fix the landing
  itself, land mid-job, then rebase your own branch onto `main`, so the
  final landing never needs the code you just changed.
- **The landing runs the push gate first**, on what it would add to
  `main` (`main..merged`), before the tests. A refused branch is reason
  `gate`, and the automatic continuation rewrites it. A brief's title
  that trips the deny list is dropped from the merge message. Before
  11-16 the gate ran at the push alone, so a held commit sat on `main`
  and blocked every later push until `main` was rewritten by hand.
- **Rewriting canonical `main` from a job** means holding the live lock
  yourself, from the host's mount namespace (the job's wall mounts the
  checkout read-only). When the rewrite range starts at `origin/main`, the
  push afterwards is a fast-forward, never a force.

## 2026-11-16 — memory: no ceilings on agents, an OOM order instead

- **No agent run has a memory or swap ceiling.** Jobs, wakes, crons, RC
  servers, the reviewer, the provider smoke runs and the broker's chats.
  A per-run cap killed a job at 20% of RAM on a box with 20 GB free, and
  the owner asked for none. Admission by memory still decides when a run
  starts. Don't add a cap back; `tests/test_memory_order.py` renders every
  generator and fails if one does.
- **The order is two numbers in `lib/loam_config.py`:** agents 800, core
  (the broker's own process and the plugins gateway) 100. The kernel kills
  the highest (per-mille share of RAM + swap) + `oom_score_adj`, so a core
  process goes first only if it alone holds 70% of the box more than the
  agent does. Caddy, the claim service, Authelia and the app are at the
  system's 0.
- **100 is a floor, not a choice.** The user manager runs at 100
  (systemd 252), and an unprivileged process can't go below what it
  inherited. Caused: `systemd-run --user -p OOMScoreAdjust=50` ran nothing
  and printed nothing, while 100 printed 100. Until 11-16 the broker was at
  500, *above* every job and wake (200), so a global OOM took the broker
  first. Read the order from `/proc/<pid>/oom_score_adj`, not from the
  comments.
- **Making it live without a restart.** `daemon-reload` re-applies
  `MemoryMax=`/`MemoryHigh=` to running units, but `OOMScoreAdjust=` only
  applies at exec. Raising a live process is `choom -p PID -n 800`, which
  needs no privilege; lowering one is `sudo choom`.
  `systemctl --user set-property --runtime` clears a running transient
  unit's caps.
- **A gated landing re-authors the engineer's commits** (2026-11-16). A
  tree cloned before the checkout's identity was set commits as the old
  one; the landing sets the tree's identity and re-authors `main..branch`
  before merging (`adopt_identity`). A commit already landed under another
  identity is re-authored by the next push (`reauthor_landed`) only when
  `main`'s reflog shows nothing but landings' fast-forwards since `origin`'s
  head; a hand commit stays held. Re-authoring keeps trees, messages and
  dates, so a commit already right keeps its hash; running jobs based on a
  re-authored commit take the replay path above.

## Antigravity's files

- **`agy` asked to resume a conversation whose files are gone** prints
  `conversation "<id>" not found` and starts an empty one (1.5.9). It does
  not fail, so a caller that means to continue must check the `.db` itself;
  the thread engine does (`agy_conversation_gone`).
- **Implicit files are not read on resume**: a conversation with its turn's
  implicit file moved away resumed with what it had been told, and each turn
  writes a new one. Idle is the newest mtime of the `.db`/`brain/` files.

## 2026-11-16 — the app left Docker and joined the repository

- **The app is `app/` and runs as `loam-app@<app>.service`** from a release
  of the checkout (`bin/deploy <app> up <commit>`; deploy/README.md,
  "Loam's own app"). A record with `[service]` is that kind of app; its
  environment is `bin/deploy`'s `service_env`, written to `/run` by the
  unit's `ExecStartPre`. `tests/test_app.py` runs the app's own suite.
- **systemd reads `EnvironmentFile=` before `ExecStartPre` as well as
  before `ExecStart`.** A file the pre step writes must be optional (`-`) or
  the first start, and every boot, fails before the step can run.
- **A host process that connects to the broker bridge's address arrives
  with the box's public address as its source**, though `ip route get` says
  otherwise. Caddy's `remote_ip` check refused it; the previews' internal
  route binds loopback too for that reason. Check a source with the access
  log, not with the routing table.
- **Debian's `chromium-headless-shell` no longer falls back to software
  WebGL**: a WebGL page stalls `Page.captureScreenshot` until the capture
  times out. `--use-angle=swiftshader --enable-unsafe-swiftshader` draws it.
  Compare a host capture with what the container gets the same day, not
  with its cached picture: a site changes under its cache.
- **A deploy-layer change is read by the broker too.** It parses the records
  for the Apps list (`/v1/apps`), so a record kind the running broker does
  not know breaks the Apps section until the broker restarts on the new
  code. Land, then `restart-broker --when-idle`, before anything else.
- **A folder project** (`"subdir"` in a project record) is one folder of a
  larger repository, briefed like any project engineer: its session runs in
  the folder, its landing refuses a branch that touches anything outside it
  (`outside_scope`, reason `scope`), and a folder of Loam's own checkout
  lands under Loam's live lock and pushes Loam's `main`, with no deploy key.

## 2026-11-16 — landing tests outside the live lock

- **A landing merges and tests in the job's tree, then takes the lock only
  to check `main`, fast-forward and push** (`_land_tested` in
  lib/loam_worktree.py). A `main` that moved meanwhile means merging and
  testing again; after `OUTSIDE_TRIES` (3) lost races it tests under the
  lock, the old order. Telemetry: `lock_held`, `retests`,
  `tested_under_lock`. Before, a Loam landing held the lock 385-866 s (one
  or two ~7-minute suites); the branch-only run is now made only when the
  merged tree fails, to say which tree is broken.
- **`loam_lock.live` polls every 5 s.** A waiter on a short hold sleeps the
  full poll after it; landings pass `poll=LAND_POLL_S` (0.5 s).
- **A second systemd user manager runs fine as the same uid**, timers fire,
  even inside the landing's `unshare -U` namespace: `XDG_RUNTIME_DIR` and
  `XDG_CONFIG_HOME`/`XDG_DATA_HOME` in a temp dir, `SYSTEMD_UNIT_PATH` =
  its runtime dir + `/usr/lib/systemd/user` (without that, "Unit
  basic.target not found" or "Result: resources"), no
  `DBUS_SESSION_BUS_ADDRESS`. It ignores SIGTERM; `systemctl --user exit`
  stops it. **It moves every process of the cgroup it starts in into its
  `init.scope`**: start it in a child cgroup of its own
  (tests/private_systemd.py does), or it reorganises the calling unit.

## 2026-11-16 — a new person's first day, walked on a fresh install

- **Walking the UI on a throwaway VM without exposing it:** a Modal
  `runtime="vm"` sandbox with systemd booted in a PID namespace (as the
  release's rehearse.py does), the names in the VM's `/etc/hosts`,
  `--local-certs`, and Playwright's Chromium *on the VM* driven by a small
  aiohttp server on 127.0.0.1 there (one exec per batch of ops; screenshots
  pulled back as a base64 tarball). No tunnel, so nothing is on the open
  web. `--use-fake-device-for-media-stream` gives the VM a microphone; CDP
  `WebAuthn.addVirtualAuthenticator` gives it a passkey, which dies with the
  browser process.
- **The release source for a rehearsal of `main`:** a bare clone served by
  `git daemon` on loopback (`git://127.0.0.1/loam.git`), `main` tagged as
  the manifest's version. Newer tags pushed there make `loam update` see a
  release. A bare repo owned by another uid fails "dubious ownership", and
  install.sh then says only that no release was found.
- **The page's CSP blocks an injected `<style>`;** a constructed
  stylesheet (`new CSSStyleSheet()` + `adoptedStyleSheets`) is allowed, which
  is how to try a CSS fix in the live page before landing it.
- **A JS `el.click()` leaves a focus ring on the section heading** that a
  real mouse click does not; shoot with Playwright's real click.
- **Linger did not bring Loam's user manager back after a reboot of that
  VM** (logind started, never started user@1000). Installs now enable
  `loam-user-manager.service`. Not proven either way on a real kernel boot.
- **The archive and restore routes answer `{"agent": "<id>"}`, a string;**
  the page's tests had assumed a row. Test page code against the broker's
  real answer shapes.
- **The first install's vault layout leaks into installs** wherever a path
  is a literal: `00_INBOX` (lib/loam_inbox.py, loam_activity's failure log),
  `40_PROJECTS` (engineer receipts, RENAME_PINNED, SYSTEM_VAULT_SOURCES).
  The app, the Files shortcuts and the schedule page now read `vault.*`;
  those others still do not.
## 2026-11-16 — restarts that end, probes that leave nothing

- **`restart-broker --when-idle` has a ceiling** (`loam_live.RESTART_CEILING_S`,
  30 min). Before it, a `working` or `asking` session holds the restart
  (three clear reads); past it only a `working` row whose `last_event_at`
  (or turn start) is under `RESTART_OUTPUT_S` (60 s) old does, and one
  clear read is enough. A landing holds it through the live lock either way;
  engineer jobs are units of their own and outlive it. `background` has
  never held it since 11-12; what could wait forever was a silent turn or
  an unanswered question.
- **A live probe cleans up after itself.** `bin/broker-probe` deletes the
  thread it created (its member's process first), in a `finally`, so a run
  that stops early does too; archiving left them in the owner's Archived
  list. Deleting a thread removes its record from the vault's conversations
  folder, so only the broker does it, on the owner's click or a probe's own
  thread; an engineer never deletes the owner's threads by hand.
- **`tests/run-all` names a scratch `LOAM_THREADS_STATE` and `LOAM_VAULT`**
  for every test unless the caller did, the floor under a thread test that
  forgets its own.
- **check-drift's model check reads Claude sessions only.** A lane on another
  provider (a persona's, or the reviewer's) is a note, never a family
  comparison: its newest Claude session predates the switch.

## 2026-11-16 — a project's test command outlives the files it names

- **`test_command` in `projects/<name>.json` is hand-written and nothing
  checks it** — not provision (which only carries it over a re-provision),
  not check-drift. When a project removes the file it names, every landing
  of that project fails as "tests fail", and the automatic continuation is
  told to make them pass from inside the repo, which it cannot honestly do
  (one began rebuilding the deleted check script; it was cancelled). Read a
  "tests fail" landing's command before reading the branch.
- **A project with no tests left** gets the lightest honest check, not
  none: for a static site served by Caddy, `caddy validate` of its
  Caddyfile, its pages present, `node --check` on its scripts.
- **The records sit behind a job's wall.** `projects/` is untracked runtime
  state inside the canonical checkout, which the wall mounts read-only, so
  an engineer job cannot edit a record in place. It was written with
  `sudo nsenter --mount=/proc/1/ns/mnt -- sudo -u <the box user> …` (the
  host's mount namespace, as the owning user). That is the "deliberate
  remount" the wall's comment allows, and it touched that one file.
- **A finished job's unlanded branch lands through a continuation**:
  `engineer-job --brief` with `continue_job` set to the original job, told
  to change nothing. The session ends, and the launcher tests, merges and
  pushes the reopened branch.
## 2026-11-16 — doc comments: a plugin with a real editor

- **A plugin's page code is served from the checkout, not from the app's
  deployed release.** The broker's files route reads the shipped plugin
  folders from Loam's checkout, so a landed plugin reaches live and staging
  alike the moment it is on, whatever commit each app copy runs. To put a
  page change on staging first, gate it on a capability only the new app has
  (doc-comments waits for `files.own`); it turns on for live when live is
  deployed from that release. **That is not enough on its own**: on 11-16
  another job deployed live from a `main` that already had the capability,
  and the editor reached live before the staging walk. A staging-only page
  change needs an explicit gate (doc-comments: `STAGING_ONLY` and the
  `next.` host), lifted by a later change.
- **The personas' browser is one Chromium with a 1 GB cap, shared by every
  session.** Driving it over its MCP HTTP endpoint for screenshots leaves a
  browser context per session unless it is closed (`browser_close`, then an
  HTTP `DELETE` with the `Mcp-Session-Id`). A dozen contexts holding Loam's
  page (which keeps its streams open) took it to 758 MB, after which every
  call from anyone hung until `docker restart loam-browser`.
- **CodeMirror 6 measures block widgets without their CSS margins.** A
  margin on a block widget (the Properties line, a table) leaves the height
  map short by that much, and everything placed from `lineBlockAt` (gutter
  marks, margin notes) sits that far too high. Use padding inside widgets.
- **`ChangeSet.of(changes, length)` splits inserted text on any line ending**
  unless given the separator as its third argument; without it a merged
  CRLF quietly became LF. And CodeMirror counts a CRLF as one position, in
  UTF-16, while Python counts code points of the raw text: convert at every
  boundary (`plugins/doc-comments/page/editor/textdiff.js`, `posMap`).
- **`node.replaceChildren(null)` draws the text "null".** Filter first.
- **The push gate's deny list covers fixtures and comments too.** Names of
  the install's people and agents in a test fixture hold the whole branch,
  and the history keeps them after a fix. Before landing, grep the branch's
  added lines with the deny file's patterns (`git diff main | grep '^+' |
  grep -oE -f <patterns>`), use synthetic names (the starter's Iris, or
  Ada, Sam), and squash your own unpushed commits once they are clean.

## 2026-11-16 — an app handoff can land with the host's parity test red

- **The app folder's landing runs only its own tests** (`projects/loam-ui.json`,
  `test_command: cd app && npm test`), not `tests/run-all`. So an app change
  to `app/docs/appearance-settings.registry.json` lands even when
  `tests/test_settings.py`'s parity check (host registry keys == handoff
  keys) then fails on `main`: the Sun `transition` key (bb293fb) did this
  until the host half landed (2d52cb0). After an app job adds a setting,
  expect the next Loam landing to be red until the host side lands.
- **"Deploy the host" is land plus `restart-broker --when-idle`.** The host
  (the broker) runs from the canonical checkout; `bin/deploy <app>` is
  for the deployed apps (`deploy/loam-ui`, `files`, `vault`) only.
- **Sun transition ids are fixed host-side** (`loam_appearance.TRANSITIONS`,
  like the size's range), not read from the handoff like the modes are; the
  parity test holds them equal to the handoff's enum.

## 2026-11-17 — the share door

- **`bin/gen-deploy` has no `--help` either.** Like `gen-rc-units`, any
  argument it does not know is ignored and the run is live: it rewrites
  /etc/caddy/Caddyfile from the tree you run it in and reloads Caddy.
- **Check a new port against every `*_PORT` constant, not against `ss`.**
  The door's first port, 8934, looked free in the code beside it and was
  the claim service's (`CLAIM_PORT`, further down lib/loam_deploy.py); for
  four minutes `/s/*` reached the claim service without the login (it
  answered its own 404). `tests/test_doc_comments.py` now checks the door's
  port against every constant.
- **A door past the login is narrowest as a port of its own.** Caddy sends
  one path, ungated, to a port that answers nothing else; the gateway
  passes an allowlist of headers (never the login's cookie) and marks the
  request, and strips that mark on the gated port.
## 2026-11-16 — the sign-in page, in front of Authelia

- **Authelia's theming cannot make it Loam's.** 4.39's `server.asset_path`
  overrides the favicon, `logo.png` and the locale files only (read in
  `internal/server/handlers.go`); colours, type and layout are its fixed
  MUI themes. So the portal's routes are Loam's own page (`deploy/login/`),
  calling Authelia's API on the same origin, with Caddy routing the rest to
  Authelia. Same origin means the same WebAuthn RP ID: existing passkeys
  keep working (`GET /api/firstfactor/passkey` answers `rpId` = the portal
  host before and after).
- **`GET /api/user/info` is stale; `POST` refreshes it.** After a passkey
  is added, the GET still says `has_webauthn: false`; Authelia's own page
  POSTs. Elevation (`/api/user/session/elevation`) offers
  `can_skip_second_factor` only when the user has a second factor; a
  passkey sign-in is one factor with `factor_knowledge: false`, so the
  password (`/api/secondfactor/password`) is what lifts it to two.
- **A new top-level folder is not in a landing's test view**
  (`platform_argv` binds only entries the canonical checkout already has):
  tests that read it fail as "No such file" on the landing and pass in the
  tree. Put new files under an existing top-level folder.
- **The push gate crashed on any binary in a commit** (it diffs with
  `--text`; a woff2 is not UTF-8). `git()` now decodes with
  `errors="replace"`, but the landing runs the canonical checkout's copy, so
  the fix only helps once landed: a branch adding a binary before that had
  to drop it (the fonts were already in `app/public/fonts/`).
- **Another job's unlanded generator change can be live.** On 11-16 the
  doc-comments step-2 job had rendered its share door into the live
  Caddyfile from its own tree; a `gen-deploy` from `main` would have torn it
  out. Read `gen-deploy --dry-run` for lines that are not yours before
  applying, and apply only your blocks if there are any.
- **A proof user on the owner's box** (to sign in behind the real login):
  append it to the users file inside the volume (Authelia's watcher reloads
  it), without `admins` so the code relay stays the owner's, read its
  one-time code from the notifier as root, then `authelia storage user
  webauthn delete <user> --all` and remove its lines.
- **`pgrep -f` in an until-loop matches the loop itself**; wait on a file
  or a PID instead.
## 2026-11-17 — doc comments: the staging walk's fixes

- **Loam's page is zoomed** (`body { zoom: var(--ui-scale) }`, Settings ›
  Size; the owner's desktop is at 90%). Inside it, `getBoundingClientRect`
  and CodeMirror's `lineBlockAt` heights are screen pixels, while a `top` you
  set is layout pixels: everything placed from a line drifts by (1 − zoom) of
  its distance. Divide by `root.getBoundingClientRect().width /
  root.offsetWidth`. The plugin harness has `?zoom=0.9`; a test at 100%
  alone never sees it. The earlier "height map" fix (widget margins) was
  real but not this.
- **CodeMirror estimates heights it has not drawn** (a table or picture
  counts as one line until it renders), so anything placed from
  `lineBlockAt` for an off-screen line is wrong until scrolled near; it heals
  ~1000 px before it shows. Measure positions only for drawn lines, and tell
  CodeMirror (`requestMeasure`) when a picture loads.
- **Prove a fix by failing the test on the old code first**: put `main`'s
  `page/editor` and `server` back, run the browser suite, see the reported
  numbers (−118 px, the editor reverting after an accept), restore.
- **Playwright 1.65.1 asks for chromium-1255**; the box caches 1234. Pass
  `executablePath` to the cached headless shell (it runs without extra
  libraries). The Write tool cannot write `/tmp` (the guard); scratch files
  go there through the shell.
- **`pkill -f <pattern>` matches its own shell's command line** when the
  pattern is in the same command, and kills the session's shell. Keep a pid
  file.

## 2026-11-16 — a public page on a name outside the domain

- **`[[landing]]` is the way**, not a site record and not `[domains]`: a
  site is always `<name>.<domain>`, and an app's extra domain brings a
  login portal on that domain, which would collide with `[moved]`'s `auth`
  on the old one. The entry carries the owner's quoted yes and check-drift
  reads it as the host's public file.
- **The push gate refuses this box's own names** in any commit's message or
  added lines, on every commit of the branch, not only the last. Write the
  docs and the test generically (`example.test`); the box's own entry
  goes in the deployment config outside the checkout (`deploy.config`).
- **`bin/gen-deploy` takes no `--help`**: it ran for real. Read the
  script's head before passing it a flag.
- **Caddy reads agent workspaces in place**: `/home/<user>` and
  `ai-labs/<agent>/sites/` are world-readable, so a folder served there is
  live as the agent edits it, with no copy and no deploy.

## 2026-11-17 — doc comments: Suggesting as real text

- **An editor that swaps typed text for a widget will corrupt it.** The
  Suggesting pause used to revert the typed words and draw them as a widget;
  CodeMirror has no caret position inside a widget, so the caret landed
  beside it and the next keystrokes went to the wrong side. Keep one's own
  pending edits as real, decorated text and treat the server as bookkeeping
  (`plugins/doc-comments/page/editor/drafts.js`).
- **`ChangeSet` composition does not cancel.** A change composed with its
  own undoing is a replacement of the words by themselves; mapping another
  change over it drops that change's deletion and leaves both texts side by
  side. Recount from a diff after undoing anything.
- **`classList.toggle(name, undefined)` flips** instead of forcing; always
  pass a boolean.
- **A Playwright handle in a failing `assert.equal`** makes node's test
  runner serialise it and get killed for memory (exit 137, empty log). Assert
  on a count or a boolean.
- **A merge can silently drop a commit that was meant to land at session
  end**: `9d381d7` was on the job branch when the session ended, but the
  launcher found "nothing left to merge" (it compared against a landing made
  mid-job) and it never reached `main`. After a mid-job `--land`, land again
  before ending, or check `git branch --contains` on `main`.
## 2026-11-16 — a real hello conversation on a scratch install

- **A scratch broker inherits the shell it is started from.** Started from
  a job session, its agents saw the job's `LOAM_JOB_*`, `LOAM_WAKE` and
  `CLAUDE_CODE_*` variables (and one went reading them). Start it with
  `env -i HOME=… PATH=… LOAM_CONFIG=… XDG_RUNTIME_DIR=… DBUS_SESSION_BUS_ADDRESS=…`.
- **`bin/settings` reaches the broker at `loam_broker.PORT` (8932), not the
  scratch broker's `--port`**, so a scratch agent's settings call lands on
  the box's real broker with the scratch token and gets a 401. Set
  `LOAM_BROKER_URL=http://127.0.0.1:<port>` in the scratch broker's
  environment; its children inherit it.
- **The CLI ignores a workspace's permissions until it is trusted**
  (`projects[<path>].hasTrustDialogAccepted` in the real `~/.claude.json`).
  Add the scratch workspace for the run, keep the file's 0600 mode when
  rewriting it, and remove the entry and its `~/.claude/projects/` folder
  after. Other jobs add their own entries meanwhile: compare your own keys.
- **A yes must come after the draft's revision** (`check_yes(after=…)`), so
  an agent that shows a short version, hears yes, and only then calls
  `agent.draft` has to ask again. Iris did, gracefully; it is a second yes
  the owner gives.
- **One config proves nothing shared.** `fleet_clauses` keeps lines most
  configs carry unmasked; with a single agent that was every line, so the
  next agent's config said it was Iris. It now masks everything when there
  is only one config.
- **`tests/test_engineer.py` fails inside a job's wall** (the jobs folder is
  read-only there): same on unchanged `main`. The landing's run is the one
  that counts.

## 2026-11-17 — doc comments round 3: record, do not compare; bound the browser

- **Tracked changes are recorded, never derived by diffing.** Three rounds
  of Suggesting bugs came from working out "what the person typed" by
  comparing the editor's text with the stored text. A StateField that grows
  ranges from the person's own transactions and maps them through every
  other change (`plugins/doc-comments/page/editor/record.js`) fixed the
  class, not the cases. Fuzz with several open at once: a fuzz that kept one
  draft open passed 600 runs while all of it was broken.
- **Trimming shared letters off a range's edge puts the edge inside a
  word**, and the next keystroke there starts a second range. Keep ranges
  as the edits made them.
- **A browser suite on a shared box runs under a cap**:
  `systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0` around
  node, `--test-concurrency=1`, a few seeded runs per browser context,
  every context closed. The previous job's unit peaked at 24.5 GB and the
  box ran out of memory five times, ending the owner's conversations; a
  failing `assert.equal(playwrightHandle, …)` serialising the handle was
  one way there.
- **A served-file allowlist goes stale.** The share door's hand-kept list
  of page modules missed one, and every visitor saw a blank page with no
  test failing. Derive it (follow the imports) and test the page through
  the door in a real browser, failing on any 404.
- **Loam's page enlarges buttons on a phone** (touch targets), so anything
  absolutely placed by a fixed height must measure its real height.

## 2026-11-17 — doc comments round 4: carry places; check an edit landed

- **The same lesson twice: carry, do not search.** Note places were found
  again by searching the text for their quote (scored by context), so on a
  doc that repeats itself a note jumped to another occurrence. Places are now
  carried through every change; a known change (the editor sends its own
  change list) is used exactly, a disk change is diffed first. A diff cannot
  tell two equal runs apart; the editor's own record can.
- **An edit script that fails its first assertion writes nothing.** Round 3
  claimed a client change (resolving one's own agent's thread) that a
  failed `assert` had skipped; only a browser test through the visitor's page
  found it. Re-run the whole script, and test the claim, not the code.
- **A page's first poll is not a baseline.** A note made between load and
  the first poll was never shown; every answer now carries the notes'
  revision.
- **`pkill -f <pattern>` in the same command line kills its own shell**
  (again). Kill by the pid from `ss -ltnp`, or keep a pid file.
- **No allowance either (round 5).** Round 4 still let a note whose words
  changed take its quote "within the size of the change"; a two-section
  deletion is a big change, so a note hopped to section 1. Now nothing is
  looked for at any distance: a detached note remembers the removed run
  (`gone`) and comes back only when that run is re-inserted at its point.
  For a diffed change both equal readings (`textdiff.lean_left`) are
  carried, and the one keeping the note's words wins.
- **`map_pos` puts any position inside a deleted run at the run's start**,
  so an `after` flag there does nothing; an edge inside a replaced run is
  mapped from the run's own edge instead (B8: a comment lost the word an
  accept had just rewritten).
- **Two `.dc-…` rules in two files at equal intent lose on specificity.** The
  share page's phone bar fix of round 4 never applied: `.dc.phone .dc-head`
  (3 classes) beat `.dc-sharepage .dc-head` (2). Measure the computed style
  in a browser test, never read the CSS and assume.
- **Places worked out over a drawn text go stale when drafts enter or
  leave it.** After a mode change, `applyAnswer` runs again on the last
  answer (`redrawPlaces`), or marks land on the wrong letters.
- **Carried onto the same words is not proof (round 6).** A comparison sees
  twin sections as one text with a few digits changed, so a note "carried"
  through it lands on the twin, and a later change makes that a different
  passage. After a disk edit or restore a note on repeated words must also
  keep its 200-character surroundings, and each note remembers the texts it
  was attached in (body hash → place), so a byte-for-byte return is exact. A
  unique quote can still follow a twin swap: indistinguishable from an edit.
- **The repeat check must look at the text before the change too.** With one
  of two twin paragraphs deleted, the quote is unique afterwards; counting
  only the new text let the note sit on the survivor.
- **A set-aside draft has to come back as a draft.** When its note returns
  attached while Suggesting, the editor adopts it again (`adoptReturned`),
  or the editor and the stored text with my drafts disagree.

## Reply markers: three places, not one (2026-11-17)

- **Stripping a marker off the posted message is not stripping it off the
  page.** `reply_mark` cleaned the message, but the page rebuilds a reply's
  words from the full record (`steps` jsonl, raw events) once the reply is
  near the viewport, and from raw `message.delta` drafts while it streams;
  both still said `↩ m111`. Any transform on a reply's text has to hold for
  all three: the message, the full record as served, and the live draft.
- **Strip at read, not in the file.** The steps file stays as the events
  came; `steps.read` applies `unmark_steps` only when the entry's own
  `reply_to` names the marker. That fixes old records with no backfill.
- **A streamed marker must be held, not shown and taken back.** `↩ m11` is a
  whole match for the regex while `↩ m111` is still arriving; the stream
  sends nothing until words follow the marker (`streamed_opening`).
- **Browser checks:** `~/pw` (the shared Playwright) can vanish mid-job;
  install `playwright@<same version>` in the job's `$TMPDIR` with
  `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1` and point `LOAM_CHROME` at
  `~/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome`. A fixture
  script must `process.exit(0)`: its servers hold the event loop.
## 2026-11-17 — doc comments: no dictation in Viewing

- **What a host hands an editor is drawn by the editor's rule, not the
  host's.** Dictation's mic was one of the header's `headExtras`, drawn in
  every mode. A control with a rule of its own (here: only while the doc
  takes typed text) is passed apart (`mic`) so the component that knows the
  mode decides, and it says when the rule flips (`onCanType`) so the host
  can end what was running (dictation's `field.closed`).
- **A bar hidden by CSS still holds its last buttons.** The phone's
  keyboard bar kept the mic inside it while off; a test counting the mic in
  the page saw it. Empty what is off, and only when it holds something.
- **`classList.remove(x)` rewrites `class` even when `x` is absent**, which
  is a mutation: the idle-page test caught `fitHead` doing it on every
  render (20 in 6 s, from `f2c2357`). Check `contains` first.
- **The live app answers on loopback without the login** (`127.0.0.1:8081`,
  `loam-app@loam-ui`; `next.` on its own port): Caddy holds the login, so a
  host-side Playwright there drives the real live page for screenshots with
  no proof user. Use a scratch doc of invented text, so nothing real is
  open in Editing, and delete it and its sidecar after.

## 2026-11-17 — doc comments: the selection popup waits

- **Never wrap `engineer-job --land` in `timeout`.** A landing tests the
  branch, then tests the merge again whenever `main` moved meanwhile (it did
  twice in one hour); each `run-all` took ~10 min with a browser suite
  beside it. `timeout 900` killed it mid-merge-test. It left nothing behind
  (the canonical checkout was clean), but run it in the background with no
  limit of its own.
- **Several jobs on one plugin all append tests at the end of
  `browser.test.mjs`**, so their landings conflict there. Both sides are
  whole tests: keep both blocks and run both sets on the merged tree.
- **"Is the user still selecting?" is a gesture, not a timer.** CodeMirror
  marks its transactions (`select.pointer`, `select`, `input.format`, the
  plugin's `remote` annotation), so the popup's own commands and the
  server's answers can redraw in place while anything the person does sends
  it away. Track the mouse button on `window` in the capture phase (the
  release can land outside the editor), and expect one last
  `select.pointer` transaction just after the button is up.
- **`coordsAtPos` is null outside the drawn viewport.** A popup placed from
  a selection's ends never appears for Select All on a long doc. Clamp the
  range to the part on screen (`posAtCoords` at the scroller's top and
  bottom) before measuring.
- **Playwright's synthetic `new Event('touchstart')` throws inside
  CodeMirror** (it reads `e.touches[0]`). Drive real touches with a
  `hasTouch` context and CDP `Input.dispatchTouchEvent`; a held touch
  doesn't turn into a click.
## 2026-11-17 — Shared Docs on core's own row

- **A plugin list that copies a core row drifts.** Shared Docs drew its own
  row and lost the ⋯ menu and open-beside within a day. Core now exports
  the row (`fileEntry` in app/public/files.js); a plugin root gets it as
  `entry(row, extras)` and a menu item through `files.action`, slot `menu`
  (app/docs/plugins/places.md).
- **A plugin viewer is drawn once per Files, and there are two** (the
  main panel's and the side panel's). One `cur` slot made each panel's draw
  close the other's editor; key per-panel state by the `files` object.
- **Opening a doc through the plugin marks it seen** (GET `doc` calls
  `mark_seen`). Anything that only needs its text reads through Files
  (`files.readText`).
- **Core's reconciler keeps handlers only from `on*` props** (`node.__on`);
  an `addEventListener` on a node built in a draw is lost when the node is
  patched into an older one.
- **Seeing the live page from the host, without the login or the personas'
  browser:** Playwright with `context.route` sending the live domain to
  127.0.0.1:8081 (the app) and `/x/*` to 127.0.0.1:8933 (the plugin
  gateway), passing the original headers with `host` set to the domain (the
  app's same-origin check refuses otherwise). Serving a tree's
  `app/public` and `plugins/<id>` from the same route previews a change
  against live data before it lands.
- **`$TMPDIR` can be empty in a job's shell.** A `cp x $TMPDIR/y` that
  fails leaves the next step (a `git show HEAD:x > x`) to overwrite work.
  Write the job's scratch path out, and `git add` before experiments.

## 2026-11-17 — bringing context from another AI

- **The library can ship a helper beside a skill.** `install_library`
  copies the whole folder (only dotfiles and `__pycache__` are skipped), so
  `docs/library/skills/<name>/inspect_export.py` reaches every install's
  `<vault>/<system>/Loam/skills/<name>/`, and an agent runs it with
  `python3 -I <path>`. Keep such a helper stdlib-only: it runs under
  whatever Python the box has.
- **Python 3.11's `zipfile` does not notice overlapping entries** (3.12
  added a check). `inspect_export.py` compares each entry's local header
  offset plus packed size with the next one itself; the test makes an
  overlap by rewriting a central-directory offset (byte 42 of a
  `PK\x01\x02` record).
- **Both upload routes cap a file at 10 MiB, held in memory**
  (`ATTACHMENT_MAX_BYTES`; Files uses the same cap). `.zip` is accepted on
  both since this date, so a small export arrives whole. A typical ChatGPT
  export with images is bigger than that, and taking it needs a streaming
  upload, not a raised number.
- **OpenAI's help centre answers automated fetches with 403** (a
  JavaScript challenge). The Wayback Machine's snapshot of the article
  (`/web/<ts>id_/<url>`, fetched with `--compressed`) is the page's own text.
  Claude's help centre serves `<article>.md`.
- **A model walk of a skill can run without the broker**: `claude -p` in a
  scratch folder whose `.claude/skills/` holds the skill, with `--add-dir`
  pointing at a synthetic vault. It shows what one model did once. It
  proves no behaviour.
- **Coverage must count characters, not items** (corrected the same day,
  on review). The first ledger counted a conversation as read when any of
  it was printed, so one cut at `--max-chars` claimed full coverage. The
  ledger now keeps per-conversation character spans, keyed by the source's
  sha256 and member, not its file name. The text is redacted before it is
  cut, so offsets are stable and a credential is never split across two
  reads. A read that stops prints the exact `--from I --offset C` to carry
  on. A tree-shaped conversation's other branches are counted as not read
  unless `--all-branches` is used.
- **The provenance convention (`created_by`/`last_modified_by`, quoted
  wiki-links) is not shipped in core.** Files stamps Markdown it saves
  (`with_provenance`). An agent's own writes rely on its instructions, so a
  library skill that writes notes says so itself.
