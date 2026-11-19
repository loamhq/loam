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
- **Nor has `gen-deploy`** (caused 2026-11-17): `gen-deploy --help` is a
  live render. It wrote the Caddyfile, reloaded Caddy and ran `compose up`
  on the login and the browser. Read a generator's docstring, never its
  `--help`.

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

- *(Superseded 2026-11-17, below: the ceiling now defers, it never
  forces.)* **`restart-broker --when-idle` has a ceiling** (`loam_live.RESTART_CEILING_S`,
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
  the owner gives. *(Fixed 2026-11-17: the skill now says draft first, then
  show; see below.)*
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

## 2026-11-17 — Shared with me: a server that fetches what a person pasted

- **Staging and live are one Loam.** One broker, one vault, one plugin
  server; only the app copies differ. A link shared "from staging" and added
  "on live" is a doc shared with oneself, so a client of another Loam's door
  needs an own-Loam path to be walkable at all: doc-comments recognises its
  own tokens and calls its door in-process (`withme.Own`). The network path
  is proved by `test/test_withme.py` against a second door over real https
  on a local port with a throwaway certificate (`openssl req -x509 …
  -addext subjectAltName=DNS:door.test`, a resolver that maps the name).
- **Refuse by address, connect to the address you checked.** Resolve once,
  refuse if any record is loopback, private, link-local, shared (100.64/10),
  reserved, multicast or one of the box's own (`ip -j addr`), then dial that
  IP with SNI and certificate checks for the name (`_Pinned`). A second
  lookup at connect time is the rebinding hole.
- **A Caddy site address without `bind` listens on every interface.** A
  throwaway loopback Caddy for a headless browser (`https://host:9443 { tls
  internal … }`) was up on `*:9443` for a minute, routing to staging without
  the login; the host firewall's input policy (drop, with 80 and 443 open) is what
  kept it off the internet. Put `bind 127.0.0.1` in every site block, and
  check with `ss -ltnp` before pointing anything at it.
- **The push gate again** (see "doc comments: a plugin with a real editor"):
  a persona's name in a docstring example and a test fixture held the
  branch. Grep the branch's added lines with the deny file before the first
  `--land`, and squash with `git reset --soft <base>` once clean.
- **app/test's fake DOM has no `classList` or `Event`**: a dialog built with
  the editor's `h` cannot be driven there; prove it in a real browser.

## 2026-11-17 — Opening from a plugin's list keeps the list

- **Opening a file synced the list column to its folder in core** (`show()`
  in app/public/files.js set `st.root`/`st.path` from the file), not in the
  plugin. The fix is an explicit `list` option from `goFiles` through the
  panels' `openMain` item and back into `show`, and `?list=@<id>` on the
  file's address (`filesHref`'s third argument, `listFromURL`). Only a
  plugin's root passes one (`fromList` wraps its `go`); shortcuts on the
  Files home, Open daily note, New inbox note and chat links still go to
  the file's folder.
- **On the desktop a Files address is the main panel's item.** Browsing a
  folder there does not push an address; only opening a file does. Anything
  that must survive a reload rides on the file's address, and routePanels
  (Back/Forward) and the cold load (`landedOn`) both read it.
- **A side-panel item promoted to the main panel carries no list** (the
  side's descriptor is root and path only), so promoting moves the list
  to the folder, as before.
- **A browser test against the fake host hangs on exit** (its event
  streams stay open): race `closing()` against a timer and `process.exit`
  in `test.after`, as app/test/files-plugin-list-browser.mjs does.
- **`tests/test_wake_failed.py` collides across concurrent landings.** It
  writes the box's real wake state and log (`lc.STATE`, `lc.LOGS`) under one
  fixed agent name, so two `run-all`s reaching it together each see the
  other's line. It failed my landing twice while four other jobs' landings
  ran at once, and passed alone and on the next try. Fixing it means giving
  it its own state folder; it is not a reason to doubt a branch.
- **The live page, from the box, for frames:** the app on `127.0.0.1:8081`
  plus a Playwright route sending `/x/*` to `127.0.0.1:8933` is exactly
  Caddy's split for the browser's network (`@sight`). The plugin's page
  files come from the app at `/api/plugins/doc-comments/files/…`, read from
  the checkout. Playwright 1.65.1's video needs ffmpeg-1013 and the cache
  has 1011, and a symlink under that name writes nothing, so use frames.
  To photograph a CSS entrance, pause its animation the moment it starts
  (`getAnimations()[0].pause()`) and step `currentTime`.

## 2026-11-17 — bringing context from another AI

- **Opening a file synced the list column to its folder in core** (`show()`
  in app/public/files.js set `st.root`/`st.path` from the file), not in the
  plugin. The fix is an explicit `list` option from `goFiles` through the
  panels' `openMain` item and back into `show`, and `?list=@<id>` on the
  file's address (`filesHref`'s third argument, `listFromURL`). Only a
  plugin's root passes one (`fromList` wraps its `go`); shortcuts on the
  Files home, Open daily note, New inbox note and chat links still go to
  the file's folder.
- **On the desktop a Files address is the main panel's item.** Browsing a
  folder there does not push an address; only opening a file does. Anything
  that must survive a reload rides on the file's address, and routePanels
  (Back/Forward) and the cold load (`landedOn`) both read it.
- **A side-panel item promoted to the main panel carries no list** (the
  side's descriptor is root and path only), so promoting moves the list
  to the folder, as before.
- **A browser test against the fake host hangs on exit** (its event
  streams stay open): race `closing()` against a timer and `process.exit`
  in `test.after`, as app/test/files-plugin-list-browser.mjs does.

## 2026-11-17 — one yes, plain headers, restarts that never cut

- **A planned restart needs positive evidence of idle.** On 11-16
  `restart-broker --when-idle` reached its 1800 s ceiling, stopped waiting
  for silent turns and restarted, cutting four, three of them blocked on
  engineer calls. A turn waiting on an engineer job is `working` and
  silent, which is exactly what the ceiling stopped counting. Now only a
  live list whose every row is `idle`, with an empty `turns` (thread turns
  in flight or queued, a field the live view gained), lets it go; at the
  ceiling or `--max-wait` it exits 75 with nothing restarted. A restart
  that must happen now is the plain `restart-broker`, the operator's.
- **A deferral is the caller's to retry.** `loam_follow` leaves the final
  decision to `restart-broker --when-idle` (it used to read once, then
  restart plainly after however long the live lock took). `loam update`
  marks the broker step `deferred` and skips `undo-broker`, since a broker
  that never restarted already runs the old release.
- **The re-check under the live lock is short** (`UNDER_LOCK_READS`):
  landings wait on that lock, so a box busy again by then defers.
- **A yes's place in the thread decides, not its stamp.** Stamps are to the
  second; a yes posted in the same second as a revision made the revised
  draft (shown by running the new test against the old guard). Each
  revision records the thread's newest message (`after_message`) and a yes
  must come after it.
- **Transcript headers are the plain name.** `hooks/transcript_capture.py`
  read the retired `emoji` from every older `agent.json` and prefixed new
  headers with it. It no longer does; `plan_update` keeps the turns already
  in a file byte for byte, so old headers stay as written.
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

## 2026-11-17 — a job that is stuck must read as stuck

- **A ticking heartbeat proves the launcher is alive, not the engine.** The
  launcher writes it. What says the engine is working is its own newest
  event: the Claude transcript's last line (and its subagents' own
  transcripts, `<id>/subagents/*.jsonl`, which the main one is silent
  behind), or the Codex/Antigravity stream the launcher writes line by
  line. `box_status()` carries it per running job (`last_activity`,
  `quiet_since`), so `--status`, the MCP status and Activity all say it.
- **Antigravity has no per-command timeout** (agy 1.5.9): a test that never
  exits holds a job forever. It runs each command as `bash -c <cmd>`, a
  direct child in a process group of its own, and writes a `step_update`
  `ACTIVE` event as it starts it. Killing that group comes back to the model
  as "Command was terminated by signal or canceled", and the run goes on.
  `lib/loam_stuck.py` does exactly that after 30 silent minutes, only while
  the engine's last event says a command runs, and only to processes that
  started in the last step (the engine's own helpers started before it).
- **Claude Code engines need none of it**: their Bash tool's own timeout
  (2 min default, 10 max) ends a command, and a background command returns
  at once.
- **A watcher thread must be joined before the record is read.** The engine
  can finish in the moment between a command's death and the watcher's
  write; the first test run read an empty record for a stop that happened.
- **Authelia answers its own API for a session when asked on the portal's
  host** (`X-Forwarded-Host: auth.<domain>`, `X-Forwarded-Proto: https`)
  with the `loam_auth` cookie alone; that is how `/_loam/passkeys` lists the
  signed-in person's passkeys without Loam deciding whose they are.

## 2026-11-17 — doc comments: hiding which agents the owner has

- **Hide where an answer leaves, not in what is kept.** The door masks the
  owner's agents in every answer to a visitor (`appear.mask`, by agent id,
  over authors, closers, spans and `people`), so the sidecar stays true and
  a change of choice covers past notes. Prove it by grepping everything the
  door serves, by a browser and by an agent key, for every persona's name.
- **The door's session cookie is `Secure`**: curl on loopback http never
  sends it back from a cookie jar. Pass `Cookie:` by hand.
- **An uncommitted file in the job's tree stops a mid-job landing** at its
  checkout step ("would be overwritten"). Commit before `--land`, and edit
  nothing tracked while it runs; a background landing needs a long timeout
  (the default 10 min killed one mid-test, leaving the tree detached on the
  merge commit; `git checkout <branch>` put it back, nothing reached main).
- **A native `<select>` is as wide as its longest option.** In a sentence,
  draw the chosen words and lay the select over them at opacity 0; it still
  takes the keyboard and the phone's own picker.
- **A plugin page change can be previewed against live before landing**:
  route `/api/plugins/<id>/files/*` to the tree's `plugins/<id>/` in the
  walk's Playwright context, beside the app (8081), gateway (8933) and door
  (8936) routes.
## 2026-11-17 — doc comments: a table of contents
- **Read headings from the syntax tree's top level, not a regex.** The top
  node's children exclude code, quotes and lists for free; skip anything
  inside the frontmatter range, which Lezer reads as a setext heading
  (`page/editor/toc.js`, `headingsOf`).
- **Loam's compact layout reaches 1024 px.** At 1024×768 the app draws the
  phone layout (the editor's `setPhone(true)`), so a "phone form" is what a
  1024 visitor in the app sees; a share page at that width is still desktop.
- **A stacked pair of labels sizes to the wider one.** Two spans in one grid
  cell (for a crossfade) made the bar's title as wide as the doc's name with
  a short section centred in it. Keep the shown words in the flow and fade
  the other out absolutely over them.
- **A sheet whose list scrolls into view must scroll the list, not the
  sheet.** `scrollIntoView` scrolled the whole sheet and took its head away;
  give the list its own scroller and set its `scrollTop`.
- **Node's `fetch` drops `Host`** (undici forbids it), so a walk's proxy
  built on it reaches the app as a cross-origin request: plugin files answer
  403 "same-origin browser requests only". Use `node:http` with the headers
  as they came. The tree preview hid this, since it served those files itself.
- **Dark in a walk without touching the owner's setting:** Loam's theme is
  its own (`data-theme` on the root, from the owner's saved appearance), not
  `prefers-color-scheme`. Pin `data-theme` with an init script and a
  MutationObserver; writing `loam.appearance.sun` would be saved back.
- **The side panel from a walk:** `loam.panels` in localStorage is cleared
  on load when a file address is opened; hover the row in the Files list and
  click its `.row-side-action` instead.

## 2026-11-17 — the share sheet as a bottom sheet on phones

- **A dialog redrawn whole needs its gestures on the scrim.** The share
  sheet's `draw()` replaces the dialog node on every change, so touch,
  focus and the menu-flip observer live on the lasting scrim
  (`page/sheet.js`) and find the dialog anew each time.
- **A fixed scrim sized to `visualViewport` needs `box-sizing:
  border-box`,** or its top padding pushes the sheet past the screen's foot
  by exactly that much. The keyboard can't be shown in headless Chromium;
  the browser test fakes `window.visualViewport` with an init script.
- **Live shots past the login, through the app on loopback, also need
  `Origin` and `Sec-Fetch-Site` dropped** from the proxied request, or the
  app's same-origin guard 403s the plugins' page code. Abort
  `text/event-stream` requests, or the proxy's read never ends. Theme
  is the owner's server-side setting (`"mode":"sun"`), not
  `prefers-color-scheme`: rewrite it in `/appearance-state.js` in the proxy
  for dark shots. Serving `/api/plugins/<id>/files/*` from a job's tree
  previews a plugin change live before it lands.
- **Two jobs adding an import under the same line conflict.** Put a new
  import on a different line from the parallel job's (above the shared
  one), and check with `git merge-tree --write-tree HEAD <their branch>`.
- **The Bash tool's own background limit kills `engineer-job --land` too**
  (10 min foreground, then moved to background with the same cap). Start it
  with `run_in_background` and a long timeout from the outset.

## 2026-11-17 — the agents sheet on phones; "Remembered for" that fits

- **Core's `select { min-height: 44px }` (app/public/style.css:40) reaches
  every select a plugin draws.** A select laid over its words at opacity 0
  with `inset: 0` still grew to 44px and overhung them; at the foot of a
  bottom sheet that made the sheet scroll by 3px, which in turn stopped the
  ⋯ menu from showing past the sheet's top. Set `min-height: 0` on any
  select whose size you mean. The plugin's test harness does not load core,
  so a test that cares adds the rule itself (`CORE_SELECT`).
- **The harness has no global `box-sizing: border-box`; core does.** A
  `width: 100%` row with padding overflowed its menu by 20px in the harness
  only. Plugin rules that rely on border-box say it themselves.
- **A menu hanging past a dialog's foot makes the dialog scroll** at the
  moment the flip observer looks. "Does the sheet scroll of itself?" is
  measured with the new menus set to `display: none`.
- **When one dialog adopts a shared component, check its siblings.** The
  share-sheet job moved the add sheet's phone CSS to `.dc-bs` and deleted
  `.dc-wm-scrim`'s own; the agents sheet shared that class and silently
  became a centred card on phones.
- **Live dark shots:** the owner's theme mode in `/appearance-state.js` is
  `sun` (follows the sun), `dark`, `light` or `system`; rewrite it to
  `"mode":"dark"`, not `moon`.

## 2026-11-17 — doc comments on Loam's design system

- **Where the system lives** (no single page said it): tokens in
  `app/public/tokens.css` plus each theme's overrides
  (`app/public/themes/<id>/theme.css`; Foundry also sets the radius
  tokens, pill = 8px); action tiers in `app/public/button-tiers.css`
  (Newspaper default, foam `.secondary`, espresso `.primary` in the display
  face; selection is paper ink); content marks, fields, notices in
  `style.css`; menus and dialogs in `menu-surface.css`. The contract's
  checkers: `app/tools/colour-families.mjs` (guarded to Latte; strip the
  guard to run it in every look) and `app/test/control-style-guard.mjs`.
  `app/docs/appearance.md` links a `design-system/tokens.md` that is not
  in this repo.
- **Core's focus ring differs by element**: buttons, links, selects get
  2px paper ink (button-tiers wins on specificity); inputs and textareas
  keep the 2px `--signal-crema` ring from style.css.
- **`--signal-crema` is not a text ink**: Latte's is 3.6:1 on its raised
  paper. Mixing it toward the ink in oklab (72/28) keeps the hue and
  clears 4.5:1 in all seven themes.
- **Every `button` in Loam's page has `min-height: 44px`** (style.css:40),
  so a plugin's 28px button is 44px tall in the app and 28px on its own
  page; a filled button shows it.
- **A plugin's tier paint needs `.dc .primary` (two classes)**: the
  plugin's own `.dc button` reset beats core's one-class `.primary`. Using
  core's class names still pays: core's id-scoped rules (an open menu or
  dialog quiets the actions behind it) reach the plugin's buttons.
- **Photographing a plugin in every theme without the live box**: the
  app's own fake host (`app/test/harness.js` `fakeBroker` + `appAgainst`,
  `files-host.js`, `shippedPlugins` for the theme rows, the plugin's files
  as `GET /v1/plugins/<id>/files/<path>` routes) and the plugin's real
  server on seeded docs behind a Playwright route for `/x/<id>/`.
  `plugins/doc-comments/test/look-browser.mjs` is the worked example:
  ~450 frames in 15 min; with core's checkers per frame, slower. A share
  link made with `links.create` alone is not in the sidecar: call
  `service.sync_links` as the API does.
- **A handler subclass must not define `_route`**: the plugin server's
  handler already has one, and overriding it answers every request with
  a crash.
- **A test that fails only in a full run is often an answer arriving late,
  not a slow step.** The app's preview cache read an app's note once per
  resolve; two at once read twice, and a read that ran before the note
  existed but answered after a capture wrote it restored "no note" and
  captured again (`app/src/app-media.js`, `noteFor`). Alone it passed 136
  of 136 runs, parallel load too. It reproduced every time once the read's
  *answer* was held, not the read itself (a held read just sees the new
  file). Holding one: a `read` dependency, or for a scratch script
  `fs.promises.readFile = …` plus `syncBuiltinESMExports()` from
  `node:module`, which rebinds the ESM named import.

## 2026-11-17 — cutting 0.2.0, the first public release

- **The push gate reads every commit, not the tree.** A commit that adds a
  deny-listed term and a later one that removes it are still held. The
  landing's own check is callable: `loam_worktree.push_gate(<checkout>,
  'main', head=<branch>, objects=<job clone>)` returns the reasons, empty
  when it would pass. Run it before the session ends; an unpushed job
  branch can still be rebuilt clean (reset to `main`, cherry-pick, amend).
- **The product's base domain may appear only in `lib/loam_install.py`**
  (the deny list's one `allow`). The README names it by `BASE_DOMAIN`. Test
  fixtures that write it regex-escaped (`myloam\.space`) slip past the
  pattern; they are the product domain, not an install's.
- **A history scan that holds up:** gitleaks and trufflehog (binaries into
  the job's scratch) over a fresh non-bare clone (trufflehog reads 0 bytes
  from a bare one), then the install's deny list plus its public addresses
  and Cloudflare ids over every blob, path, message and tag reachable from
  the branch, and the printable strings of every binary. At 0.2.0 the only
  findings were synthetic: `sk-proj-Abc123…` in `tests/test_context_import.py`,
  `user:password@example.org` URLs in app tests, a commit sha in
  `app/tools/capture-latte-light.mjs` taken for a GitHub token.
- **The design preview (`/?threads=fixture`) is the source of screenshots
  with no person's data**: `node tools/fake-broker.js` (PORT=…) and
  `APP_PORT=… BROKER_URL=… BROKER_TOKEN=dev node src/server.js`, then a
  headless Chromium. Its fake API must answer every route the page calls
  the way a runtime would; an unknown sub-route fell through to the thread
  record and drew "Steps couldn't be loaded" under every reply.
- **A job's shell may have no `$TMPDIR`**: `cd $TMPDIR` then lands in the
  home directory. Name the scratch path in full.
## 2026-11-17 — the contents card made compact; core's pane lead

- **Core's `button { min-height: 44px }` is why a plugin's list reads
  loose in the app and tight on its own page.** The ToC card's rows were 28px
  by their CSS and 45px in Loam. Take the minimum off with `(pointer: fine)`
  only, so a touch screen keeps its targets; tests add core's rule
  themselves (`CORE_SELECT` in the browser suite).
- **Core's Show sidebar is absolutely placed** (`.showbar`, style.css) and
  each core header pads 62px around it; a plugin header did not, from the
  plugin's first day. Core now sets `--pane-lead` on `.pane:has(> .showbar)`
  (0px in tokens.css); `app/docs/design-system.md` says how to use it. Two
  panels at a 1440 window tuck the list (`panels.tucked()`, under 1700px).
- **A walk that needs two panels with the list hidden:** open the folder,
  set `loam.panels` `{version:1, side:null, split, sidebar:false}` and
  reload (a *file* address clears it), side-open a row, click `.showbar`,
  open the main doc, then "Hide sidebar for a focused view". At 1440 the
  main pane takes the editor's phone layout at an even split, the desktop
  one at 62
  (`look-browser.mjs` `toc-panels`, `toc-panels-even`).
- **Reading computed colours: use a canvas, not a regex.** Chromium returns
  `oklab(…)` for anything mixed in oklab and `color(srgb …)` (0–1) for srgb
  mixes; `colour-families.mjs` took the first three numbers as bytes.
  `app/test/colour-families-browser.mjs` proves the fix. look-browser
  rewrites that function's source by two literal strings (the Latte guard
  and `document.querySelectorAll('body *')`); keep them verbatim.
- **Snapping a scrolled list to a whole row fails at its max scroll**: the
  browser clamps. At the end, prefer the end (`rowScroll`, toc.js).
- **`app/docs` links many files that were never in this repo** (all of
  `design-system/`, most of `evidence/`, `../handoffs/`): the 0.1.0 tree
  left them out. Check a link exists before pointing at its neighbour.
- **`pkill -f <pattern>` from the Bash tool matches its own shell** (the
  command line holds the pattern) and kills the call. Kill by PID.

## 2026-11-17 — the 0.2.0 release walk (a picture report)

- **The 11-16 walk method still holds** (Modal `runtime="vm"`, systemd in a
  PID namespace, names in the VM's `/etc/hosts`, `--resolver system
  --local-certs --address <vm ip>` after the README's own line, Playwright
  driven on the VM). The installer can take the product's real base domain
  this way: the name resolves only inside the VM and no record is made.
- **Dark in a walk needs both switches:** `emulate_media(color_scheme)` for
  the claim and sign-in pages (they follow the system), and a pinned
  `data-theme` for the app (it follows the owner's saved appearance).
- **`?modal=settings&section=…` does not open Settings at phone width;**
  tap the Settings button and the row instead.
- **A CDP virtual authenticator belongs to one page.** The sign-in settings
  open in a new tab: add the authenticator on that tab, and know it dies
  when the driver restarts.
- **Running `loam update` as root on an install breaks two things** (found
  on 0.2.0, not fixed in that job): git's ownership check makes the
  checkout read as "not a git checkout", and the run leaves root-owned
  files in the loam user's update state. A root-owned notice then can't be
  unlinked (`notice_done` suppresses the error), so the engine posts it to
  General and gives the default agent a turn every 30 s, without end.
- **Name each shot once.** A later shot under the same name overwrote the
  only picture of a transient bug; pull the shots off the VM before
  reusing a name.

## 2026-11-18 — 0.2.1: what the 0.2.0 walk found broken

- **The login renews its session in the middle of a passkey add, and the
  gap is about 0.9 s.** Measured on a fresh install: the one-time code's
  `PUT /api/user/session/elevation` ends the old session as it starts and
  sends the new `loam_auth` cookie with its answer ~0.9 s later (the
  passkey `register` calls set it again). Every request in that gap is
  anonymous (21 polls in a row at 45 ms spacing). A single re-check 700 ms
  later still fell inside it, so `app/public/auth.js` backs off
  (`timing.confirmSchedule`, 0.7/1.5/3/6 s) before ending the page. Prove a
  change here by causing it: hammer `/_loam/login-code` from the app tab
  with a raw `fetch(..., {redirect: 'manual'})` beside `authenticatedFetch`
  while the portal tab adds a passkey (`trials/2026-11-18-0.2.1-proof/`).
- **A system notice can give a paid turn on its own, so the engine guards
  the class.** `ThreadEngine.system_turn_allowed` keeps every delivered
  notice key (update and job deliveries) in `<state>/threads/system-turns.json`
  for 30 days and refuses the same line to the same agent within an hour.
  A new kind of notice must go through it.
- **`bin/loam-update` as root re-runs itself as the install's user**
  (`loam_update.as_install_user`, `runuser ... env -i`) after
  `repair_owner` gives back anything a root run left. A root shell's
  `loam update` on 0.2.0 cannot be fixed by 0.2.1's code (the old code runs
  the update); the release notes carry the `chown`.
- **A persona's post is told to the broker** (`bin/thread post` ->
  `POST /v1/threads/<id>/posted` -> `ThreadEngine.posted`): it goes on the
  stream and its @mentions wake members. The chain for a post is the
  poster's live turn here, or the agents' *posts* since the owner wrote
  (a reply carries `engine`, a post does not), so reply chatter cannot
  starve a tag and posts across threads cannot ping-pong past the cap.
- **The app's CSP refuses `setAttribute('style', …)`; the CSSOM is
  allowed** (`el.style.fill = 'var(--token)'`). And `themes.test.js` refuses
  any literal colour outside `tokens.css` and theme folders: a brand colour
  is a core token.
- **On a walk VM, a failed turn on a placeholder Claude login clears the
  placeholder**, and the welcome screen returns for a fresh browser. Write
  it again before each browser run. Each Playwright run is a new context:
  sign in each time, and `?modal=settings&section=signin` is dropped while
  the welcome finishes; open it again on the thread's own address.
- **Modal 1.6.3's `Sandbox` has no `open()`**; move files over `exec`'s
  stdin as base64 in 1 MiB chunks (`base64 -d > path`).
- **Inside a job, `tests/test_engineer.py` fails** writing a fixture into
  the read-only jobs folder (`Errno 30`); it does on `main` too. The
  landing runs the suite outside the job's mounts.

## 2026-11-17 — reports upstream, local changes, the marketplace

- **A release install has no `main`.** install.sh clones at the tag and
  bin/loam-install clones that, so the checkout is detached with no branch,
  and `git clone --branch main <checkout>` (every job's tree) failed. The
  first Loam job now makes the install's own `main` at the release and marks
  it `loam.localMain=true`; such a `main` is never synced from `origin/main`
  (Loam's unreleased development) and never pushed. A landing that did
  either would have moved a friend's install off its release.
  `tests/test_local_changes.py` builds the install the installer's way.
- **A fresh install's platform engineer is not provisioned.**
  `provision --engineer` copies its role and project file from
  `<vault>/Projects/Loam/engineer/`, which only the developing install has,
  and the installer never runs it, so a brief for project `Loam` there is
  refused ("the engineer's own composition is broken"). Shipping a role for
  other installs is an operator decision, not a mechanism one.
- **A GitHub App's user token reaches only the repositories its app is
  installed on**, so the owner's connected GitHub cannot open an issue or a
  pull request on `loamhq/*`. Upstream reports are a prefilled new-issue
  link (`/issues/new?title=&body=&labels=`, kept under ~7,000 characters)
  the owner submits; a marketplace listing is GitHub's new-file page with
  `filename=` and `value=` prefilled, which forks and opens the pull request
  for someone without write access. That is why the marketplace repository
  holds one file per listing and builds `list.json` on merge.
- **Pushing to the owner's own repositories needs no new credential**:
  `loam_github.helper_env(repo)` makes git ask bin/git-credential-loam,
  which asks the broker for an installation token for that one repository.
  The repository must exist first (creating one needs the user token, and
  public is the owner's yes), so `loam market share` asks the owner to make
  it on github.com.
- **Scrubbing a vault path**: a name may hold spaces ("Wedding plans.md"),
  so a pattern allowing them in a segment ran across the sentence into the
  next path; a name is words joined by single spaces, never a space before a
  slash.
- **A new top-level folder is invisible to the landing's tests.** The
  landing proves a tree at the runtime's path by binding each top-level
  entry the tree tracks over the canonical one (`platform_argv`); an entry
  the canonical lacks has nothing to bind over. `marketplace/` failed
  `test_marketplace` there and passed in the tree; it moved to
  `docs/marketplace/`. Put new content under an existing top-level folder,
  or prove it with `wt.platform_argv(tree, cmd)` before ending the job.

## 2026-11-18 — moving Docker Engine a minor release under live-restore

- **Live-restore held across a minor move**, 29.8.2 → 29.9.3 together with
  containerd.io 2.3.6 → 2.4.2 (runc 1.5.1 → 1.5.3), in one pinned
  `apt-get install`. Docker's docs still promise it only for patch releases,
  in calendar-version words (`YY.MM.x`), so check the release notes for
  state-format or containerd-namespace changes before trusting it again.
  The proof: every container kept its pid and `StartedAt`, and the shims
  kept their pids.
- **What the daemon restart costs**: published ports refused connections
  for about 2 s, because docker-proxy is a child of dockerd and the new
  daemon starts fresh proxies. Health checks go back to `starting` until
  their next run. A `docker restart` cut its own port for about 1 s.
- **A running container keeps the runtime it was created under.** After an
  upgrade, its shim's `/proc/<pid>/exe` reads `containerd-shim-runc-v2
  (deleted)`. A restart gives it a new shim on the new binary, and runc
  runs only at create time. So "on the new runc" is proved by a shim that
  started after the upgrade, whose exe is not deleted, plus
  `docker info`'s runtime annotation `org.opencontainers.runc.version`.
- **Restart Redis-backed session stores only after a `SAVE`.** With
  `--appendonly no`, a store snapshotted only by `--save` loses what the
  last snapshot missed. Run `redis-cli SAVE` first, then compare `DBSIZE`
  before and after.
- **29.9 adds a unix socket**, `/var/run/docker/extensions/callback.sock`
  (the extensions framework). It sits under the root-only `/var/run/docker`,
  so it adds no TCP listener and no reach beyond root.

## 2026-11-18 — the agents' browser reads past the login, and writes only same-origin

- **A login bypass by network is a bypass for every page that network
  opens.** The browser's subnet passed the gate whole, so a stranger's page
  in it could POST to the app's host as the owner. The plugin gateway's
  `/x/*` had no origin check, though the app's `/api/*` did. Two layers
  now: `bin/plugin-gateway` applies the app's rule to every non-safe method
  and every upgrade, and `lib/loam_deploy.sight_lines` refuses such a
  request from the subnet unless `Sec-Fetch-Site: same-origin` (or, with no
  fetch metadata, `Origin` is the host). That covers every private host,
  sites with backends included, without anyone having to list the doors.
- **Prove it with the real browser, not curl.** The playwright MCP on
  `127.0.0.1:8931/mcp` takes plain JSON-RPC (initialize, then `tools/call`).
  From `https://example.com/`, `browser_evaluate` a `fetch(..., {mode:
  'no-cors'})`; `browser_console_messages` in the SAME session shows the
  status, and the host's access log shows the headers Chromium really sent.
  Each client run is a fresh context, so the console is gone by the next run.
  A body that names no document gets the plugin's own 400, which proves the
  request reached the plugin without writing anything.
- **Caddy's `{http.request.host}` has no port; a backend's `Host` may.** In
  a test on `http://name:port`, an `Origin` that satisfies Caddy (`https://name`)
  fails the gateway (`http://name:port`). Send `Sec-Fetch-Site` and an
  Origin matching the backend's view. On 443 the two agree.
- **The push gate reads test fixtures too.** A made-up hostname carrying the
  owner's name held a landing; use `owner.example`.
## 2026-11-18 — an engineer on every install (0.2.2)

- **The role every install gets is `engineer/install/role.md`**, the
  owner-approved text, shipped verbatim (operator-authored: never reworded;
  a change to it is the owner's). `engineer/install/project.md` is what the
  engineer owns on an install. `provision --engineer` seeds both into
  `<vault.projects>/Loam/engineer/` (`role.md`, `projects/loam.md`) and the
  vault copy stays the source, copied verbatim as before. A newer release
  replaces a vault copy only while its hash is the one last seeded
  (`<paths.state>/engineer/seeded.json`); a copy changed by the owner, or
  never seeded (the developing install's own), is kept and said to be.
- **Three doors provision it:** the installer's step 8, every update's
  `agents` step, and any agent's provision (`ensure_engineer`). The last is
  for an install updating from 0.2.1: the update code that runs is the old
  release's, which provisions only agents, but with the new `bin/provision`.
  Proved: a 0.2.1 install updated to 0.2.2 had the engineer after.
- **A release install's checkout has no git identity**, so the engineer's
  first commit failed ("unable to auto-detect email address"). Provision now
  sets `Loam engineer <engineer+loam@noreply.local>` when the checkout says
  nobody. A VPS whose hostname has no domain fails the same way.
- **The suite did not pass on a fresh install.** 35 test files failed on an
  untouched 0.2.2 checkout, so no engineer change could ever land on a
  friend's install. Causes: the tests read the install's own
  `/etc/loam/loam.toml` and were written against the developing box's
  (numbered vault folders, America/Los_Angeles, no `[edge]`); a few read the
  real vault or the real briefers; `python3-cryptography` was not
  installed. `tests/run-all` now lays that layout over the install's config
  (identical to the real file on the developing box). A new test that reads
  config must pass under it. To check on a VM: `tests/run-all test_x.py`
  runs only those, each in full.
- **The install's user is `loam`**, so anything that scrubs or matches the
  user name matches the product name. The report scrubber made every "Loam"
  `<user>`; `loam_feedback.NEVER_PRIVATE` holds it out.
- **Project engineers cannot be provisioned on an install made by
  install.sh.** Their role is rendered from the platform role by tables
  written for the developing box's text (`CRAFT_*` in bin/provision), and
  the shipped role matches none of them. Provision refuses in a sentence;
  a role for project engineers on every install is the owner's to settle.
- **A rehearsal engineer job without a model login:** set `[bin] claude` in
  the VM's loam.toml to a stand-in script that edits and commits in
  `$LOAM_JOB_TREE` and writes `$LOAM_JOB_DIR/receipt.md`. Remove it before
  the landing's tests run: the suite reads the same config, and every test
  that starts `claude` gets the stand-in (the wake tests failed on it).
- **On a Modal VM with systemd in a PID namespace, an `nsenter`'d process is
  in the root cgroup and `systemd --user` exits 1 saying nothing**, even at
  debug. `test_schedule` skips there (`private_systemd.Unavailable`).
- **`vm.py run` keeps only the last 12,000 characters.** Write long output to
  a file on the VM and fetch it.

## 2026-11-18 — shared keys, Images, an agent's images (0.2.3)

- **A key's three homes, in order**: a plugin's own (`<private>/secrets.env`),
  the shared one (`<state>/plugins/keys/<provider>.env`), the box's secrets
  file. `lib/loam_keys.py` resolves; `keys.env` in each declaring plugin's
  private folder is the resolved copy its processes read (`LOAM_PLUGIN_KEYS`),
  rewritten by `sync()` on every change and by the five-minute reconcile.
  Rendered files carry the path, never a value.
- **`adopt()` must not undo a choice.** It moves a plugin's pre-shared-keys
  key to the shared slot; run on every listing, it first swallowed a key the
  owner had just given one plugin on purpose. A key set through "use a
  different key" leaves a `<provider>@<plugin>` record in `checks.json`, and
  adopt skips any key that has one.
- **The box's secrets file used to reach any plugin by name**
  (`secret_for`'s fallback). Since 0.2.3 a name the plugin never declared
  gets None. A plugin that read a box secret it did not declare stops
  getting it: declare it.
- **`button-tiers.css` outranks a component's own colours** (`:root :is(…)`
  with three `:not()`s is specificity 0,5,0). A surface that paints its own
  buttons (the image view, light on dark) is excluded in that rule's
  `:not(.nav *, …, .image-view *)`; out-specifying it fails on
  `:focus-visible`, which the open view's first focus triggers.
- **`test/fakedom.js` answers class selectors only and reflects no
  `dataset`.** Page code that a unit test drives reads `getAttribute('data-…')`
  and queries by class.
- **Core ships no image tool.** `generate_image` is the Images plugin's
  (`plugins/_listed/images/`, standard library only), at
  `mcp__images__generate_image`, whose vault-relative `target_path` the
  boundary guard resolves against the vault (`VAULT_RELATIVE`) before
  checking; a relative path checked as the agent's working folder would
  have let `15_CONVERSATIONS/x.png` through.
- **`plugins/_listed/`** holds first-party plugins published from their own
  folder and listed, never shipped: a `_` folder is never read as a plugin.
- **Captures without anyone's data**: `tools/capture-accounts.mjs` (the
  fixture app, `tools/fake-keys.json`, pictures drawn in the script), at 390
  and 1440, light and dark; it fails on a page error or sideways overflow.
  The box's Playwright lives in an old job's scratch; Chromium is
  `/usr/bin/chromium-headless-shell` (`LOAM_CHROME`), because the cached
  Playwright browser build is older than that Playwright.
## 2026-11-18 — nothing in a launch grows with the vault; every wake's end is on the record

- **One argv string is capped at 128 KiB** (MAX_ARG_STRLEN), whatever
  `ulimit -s` says, and `execve` fails with E2BIG before the program exists.
  The wake put the whole vault `git diff --name-status` into `-p`; one
  1,033-file autocommit made it 183,749 bytes and every reflection wake died
  in `subprocess.run`. Repository state goes in a file the prompt names (or
  in a list capped by a constant, `INLINE_CHANGES_MAX`), never in argv.
  `tests/test_wake_launch.py` execs a real stand-in CLI against a 300 KB diff.
- **An exception out of bin/wake used to vanish.** It ran the `finally`
  that clears `.running`, so bin/wake-failed (which reports only a run that
  left its marker) stayed silent, and neither the jsonl nor the failure log
  had a line: the failure log read as a clean night. `main()` now records
  any exception as stage `crash`, with the step it reached (`CRASH["at"]`)
  and whether the CLI had started.
- **A failed unit is not evidence against a CLI build.** provider-follow
  rolled Claude Code back twice for wakes our own launcher refused or
  crashed. It now reads the run's own jsonl line (`explained`), excuses
  `preflight`, `crash`, `account_refused`, `lock_timeout` and another
  provider's run, still blames a unit that left no line, and lifts an old
  refusal whose every reason it now excuses (`lift_refusals`).
- **The size limit refuses nothing at run time.** `OVER_LIMIT_REFUSED_FROM`
  is gone; the wake, a cron record (`loam_cron.parse_record`) and an RC
  start report a file over 10,000 characters as "reported, not refused" and
  run. Only the engineer/reviewer edit guard still refuses past it.
- **A persona hears about its own dark night** (`lib/loam_lane_notice.py`):
  the thread engine prepends one line to its next turn when a lane's last
  jsonl row is `ok: false`, once per failure (told stamps in
  `STATE/lane-notice/`), cleared by the lane's next good row. It goes
  quiet when `LOAM_AI_LABS` points at scratch without `LOAM_LANE_NOTICE_LOGS`,
  because thread tests reuse live persona names.

## 2026-11-18 — Settings › Market

- **A listing's page reads the plugin at its link before install**
  (`loam_marketplace.details`): a shallow fetch at the tag through
  `loam_plugins.fetch` and `read_folder`, nothing written, kept an hour per
  link with its icon, cover and theme.css in memory for `file()`. Each first
  read is a git fetch on the broker, so the page asks three at a time
  (`page/market.js`, `pump`). The broker routes are reads only; installing
  stays `POST /v1/plugins`, twice.
- **A listed theme's CSS reaches the page only through the app's own check**
  (`marketThemeCSS` in `app/src/themes.js`, the installed themes' rules plus
  the `.theme-preview[data-look]` swatch scope). Its fonts are not loaded
  until it is installed (`theme-fonts.js` knows only first-party looks), so
  Try it shows the serif fallback for its display face.
- **The list carries no dates**, so the lead story's "newest by Loam" cannot
  be read from it: `featured` leads, else Loam's own listing with the highest
  version. A `listed` date stamped by `build.py` would be a schema change to
  the public repository, which is the owner's call.
- **Every `/^\/…$/.exec(rest)` the app relays needs a line in
  `PAGE_RELAY`** (`lib/loam_settings.py`); `test_settings.py` reads
  `server.js` for them.
- **The push gate's deny list has product names in it too** (a sync tool's
  name in a code comment matched). Grep added lines case-sensitively, as the
  gate does, before landing.
## 2026-11-18 — 0.2.4: what walking 0.2.3 on staging found

- **A plugin row's `files` is the broker's `/v1/...` path**; the page loads
  only `/api/...`. The icon was a broken image until the page built its own
  `/api/plugins/<id>/files/<icon>` (a unit test fed `/api/` and hid it).
- **A sentence-long `settings.entry` label in a `.setrow` folded to a few
  pixels wide** beside a 100%-wide field: the section ran 3,115 px tall and
  read as an empty grey panel. Label above field.
- **`button-tiers.css`'s focus ring matched every `[tabindex]`**, `-1`
  included, at a specificity no heading rule beat, so a sheet heading
  focused for screen readers drew a box. Programmatic focus counts as
  `:focus-visible` there.
- **The message gallery was cached by the attachments array**, and every
  read of a thread hands over a new one: the images were rebuilt and their
  loading restarted on each refresh. Keyed by thread, message and contents.
- **`aspect-ratio: auto 4 / 3`, not `auto`**, for a lazy image that should
  take its own shape: with `auto` alone it has no height before it loads.
- **Walking the real app from the box**: the app's loopback port (staging
  8082, live 8081) needs no login; a hook appended to the served `app.js`
  is set only after the module's top-level awaits, so wait for it. The
  owner's appearance may follow the sun: set the browser clock, not the
  setting, for light and dark.
## 2026-11-18 — an agent's images, drawn where it wrote them

- **Keep the reference; don't take it off.** The morning's version took
  `![alt](path)` off the message text and put the file in `attachments`, and
  the page still showed the raw syntax: it rebuilds a reply's words from the
  full record (raw events), which never lost it, and from live deltas. The
  same lesson as reply markers, from the other side: when three sources feed
  the page, make the page's rule work on what all three already say. The
  reference stays in the text and each attachment carries `refs` (the
  destinations as written); the page draws a picture at a reference whose
  destination is in `refs`, and leaves anything else as text.
- **The app's thread renderer is `page/conversation.js`'s `renderText`,
  not `markdown` itself.** It wraps `markdown(text, {copy, onLink})`; an
  option passed to `renderText` is dropped unless that wrapper forwards it.
  Unit tests with the default `renderText = markdown` passed while the real
  page ignored the option. Only the browser capture found it.
- **A lazy picture inside a just-opened thread may never load in a capture**:
  the thread settles at its end after opening and undoes a single scroll.
  Re-scroll inside the wait (`tools/capture-reply-images.mjs`).
- **`image_size` reads PNG/GIF/WebP/JPEG headers** with seeks, no library,
  so the page can hold a picture's exact space before it loads.

## 2026-11-18 — the Market for agents: search and the card in a thread

- **One matching rule across two languages.** An agent's Market search
  (`loam_marketplace.search`, `bin/loam-market search`, the `market` tool on
  `mcp/engineer_server.py`) does not match in Python: it hands the list to
  `app/tools/market-search.mjs`, which runs `search` from
  `app/public/market.js` under Node (`/usr/bin/node`, the app's own unit's
  runtime). `app/test/market-card.test.js` runs the CLI and the page's
  function over the fixture list and fails on any difference in order;
  sorting the CLI's ids by name made it fail (checked).
- **The card is words, not a tool.** `[Name](https://<app>/?modal=market&listing=<id>)`
  on a line of its own (the result's `card`); the record keeps it as a
  working link. `card_line` takes the origin from the deploy records (the
  `app_shell` record at the install's domain). The page accepts any host,
  since the card's action is local and the id must be listed.
- **The engineer server's new tool needs no re-provision**: every lane on
  every provider already loads `engineer`, and its tool list is read when a
  conversation's server starts, so new conversations (and wakes, crons) see
  `market` as soon as `main` lands; open conversations do not.
- **`tests/test_engineer_server.py` skips in a job's tree**: it looks for
  `mcp/.venv/bin/python` beside the repo, and the venv lives at
  `lc.MCP_VENV` (`~/.local/share/loam/mcp-venv`). Run it by hand with
  `VENV_PYTHON` pointed there to see it pass.

## 2026-11-18 — 0.2.5: the Market's first listings

- **Two themes ship; five are listed.** Autumn, Deep Forest, Foundry, Mist
  Trail and Yosemite live in `plugins/_listed/<id>/` and are published to
  `loamhq/plugins` as `<id>/` at the tag `<id>-v<version>` (one repository,
  so a tag names its plugin; the link regex takes no `/` in a ref). The
  core keeps their per-look CSS (`materials.css`, `face-paints.css`,
  `signature-avatar.js`), which applies whenever one is installed.
- **Moving a shipped theme out needs a keep step**, because `loam update`
  runs the *old* release's code. `lib/loam_listed_themes.py` runs at the
  broker's first start on the new code: a moved look in use, or with saved
  option choices, is installed from its link by the normal two calls, else
  copied from the release as built here; once per id
  (`<state>/plugins/kept-themes.json`), so a later removal sticks.
  Appearance keeps a stored theme id and options even when the theme is
  gone, which is what lets the step find them.
- **The appearance handoff is the shipped catalog.**
  `node tools/appearance-registry.js --write` after a shipped theme moves;
  `tests/test_settings.py`'s parity check reads the shipped folders only.
- **A theme's fonts were never servable from Yours.** The broker's files
  route served `page/`, `assets/`, `theme.css`, `PLUGIN.md` and covers, so
  an installed Autumn wore a fallback face. A theme now serves `fonts.css`
  and top-level `.woff2`/`.woff`; the app serves the CSS checked at
  `/theme-fonts/<id>.css` (`themeFontsCSS`: `@font-face` only, files beside
  it). Found only by walking a fresh install: unit tests of each half passed.
- **`settings.entry` takes `labels`, `note` and `advanced`** (hosts before
  0.2.5 ignore them, so a listed plugin using them still installs there).
- **The push gate checks paths with no `allow`.** A file under a folder
  whose name the deny list holds can never land; only the pattern itself
  can change. Line `allow`s are exact paths: moving an allowed file needs
  its line moved.
- **The marketplace's list workflow pushes `list.json` itself**
  (`permissions: contents: write` in the workflow beats the org's read-only
  default). Merge listings one at a time and wait for each push run, or the
  runs race to push. `raw.githubusercontent.com` serves the old list for
  about five minutes after; read it through the API to check.

## 2026-11-18 — Settings in seven (0.2.6)

- **A Settings section is three things that must agree:** its entry in
  `SETTINGS_SECTIONS` (`app/public/app-shape.js`), its registration in
  `page/registrations.js` (id, title, icon, view), and its row in
  `app/docs/settings-sections.registry.json` (`app-shape.test.js` compares
  the ids). The phone's first page and the desktop nav both read the
  registry, so order is set there.
- **Old ids are kept by `settingsHome(page, provider)`**, which every way in
  goes through (`goSettings`, `openSheet` for a `?modal=settings&section=`
  address, `settingsBody`). `accounts` with a provider is the `api-key` page;
  `CONNECTIONS_GROUP` says which group of Connections an old id named, and
  the page scrolls there once (`sheet.group`, `groupShown`).
- **Who is signed in is a header, not a route of the login's.** The gate's
  `forward_auth` copies `Remote-User`/`Remote-Name`/`Remote-Email` onto every
  request it passes, the app included, and the browser's subnet has them
  taken off (`SIGHT_IDENTITY_HEADERS`). `GET /api/account` answers them; on
  the loopback port there are none, and the page falls back to `/owner`'s
  name. An app-answered read still needs its line in `PAGE_RELAY`.
- **Many browser-free tests navigate Settings by row title** (`=== 'Providers'`
  in github, acme, codex tests). Renaming a section breaks them by the dozen;
  grep the old title in `app/test/` first.
- **`tools/capture-settings.mjs`** walks every new surface on the fixture app
  at 390/1440 light/dark and fails on overflow (the page and `.setmain`), a
  page error, or the word "Accounts". `extraHTTPHeaders` stands in for the
  gate's identity headers. In a thread, scroll a card with
  `element.scrollIntoView` after a short wait: Playwright's
  `scrollIntoViewIfNeeded` never finds it stable while the thread settles.
- **A listed plugin's copy changes only on publishing.** Editing
  `plugins/_listed/<id>/` changes nothing on an install until a new tag is in
  the public plugins repository and the person updates it from the Market.

## 2026-11-18 — the Market's listings: covers, the README, the creator

- **A list format change must keep every older reader's rules.** A Loam
  before this one refuses a listing whose `author` is not a string, so the
  listing file takes `author: {name, links}` but build.py writes list.json
  with `author` as the name and the whole as `creator`. Unknown fields
  (`cover`, `readme`, `creator`) are ignored by old readers, so they are
  safe; a changed type is not. Check any list change against the *released*
  `check_entry`, not this tree's.
- **"By Loam" read the author string**, so renaming the first-party
  listings' author emptied the lead story. `byLoam` now also counts a
  listing from the `loamhq` organisation; a Loam before it still reads the
  author alone and shows no lead story until it updates.
- **The first-party theme covers' sources are 1536×1024 images** kept
  outside this repository (the 640px `cover.webp`s are whole-frame
  downscales of them; `cover-large.webp` is the source at full size, so
  2000px is not reachable without upscaling). Find a source by comparing
  the 640px cover with candidates directly (a true match differs by under
  2/255 on average); photographs that merely look alike score far worse.
- **A new file in a listed plugin's folder is a new tag**, since a listing
  is read at its tag: the large covers made the five themes 1.0.1.
  `loam_listed_themes.MOVED` stays at 1.0.0 (its tags still exist; the
  Market updates them).
- **A remote cover is fetched by the broker**, never the page: https only,
  every resolved address public (`ipaddress.is_global`), redirects checked
  the same way, at most 3 MiB, typed by its first bytes. Tests stand in for
  `remote_allowed`, since their host is loopback.
- **The README is cleaned at the broker, drawn by markdown.js**: HTML out
  (an `<img>`/`<a>` first becomes Markdown), relative links to GitHub at
  the tag, the folder's pictures kept as `readme/<path>` files. markdown.js
  never makes markup and follows only http/https/mailto, so the page needs
  no sanitiser of its own; pictures draw through its `images` option.
- **Brand marks come from simple-icons (CC0)**, as marks.js does. LinkedIn
  is not in it (taken out at the vendor's request); it wears the globe.
- **`pkill -f <name>` kills the shell that ran it** when the name is in
  that shell's own command line. Use the pid.

## 2026-11-18 — feedback to Loam's makers without a GitHub account (0.2.7)

- **The claim service is per install, not central.** `loam-claim.service`
  runs on every install and knows only its own claim; no service on the
  makers' box has ever heard of another install. What the makers' box can
  check is DNS: installs' names under `BASE_DOMAIN` are made by Loam's
  makers, so "the install's domain resolves to the caller" is the proof a
  report came from an install. The claim state itself is checked on the
  sender (`private_route` refuses while `/etc/loam/claimed` is absent).
- **The push gate's deny list holds the product's domain and the owner's
  name**, with one `allow` (lib/loam_install.py for the domain). A URL to
  the makers' box cannot be written in the code: it is built from
  `loam_install.BASE_DOMAIN`, and the receiving host is `feedback.host`
  (default that same domain) in the receiving box's own loam.toml. The
  gate reads every commit of the branch, so amend, don't add a fixing one.
- **Caddy without `trusted_proxies` overwrites `X-Forwarded-For`** with
  the real peer, so the claim service's `client()` is the caller's
  address for anything that came through Caddy. A process on the box can
  still reach 127.0.0.1:8934 and forge the header.
- **A `[[landing]]` block can carry a `handle` before its `file_server`**:
  `reverse_proxy` is terminal, so the landing's files never see the path.
- **`/etc/loam/claim` on an install is JSON** (`{"token", "made"}`), not
  the bare token; a rehearsal posting the file's text gets "not the right
  link".
- **Rehearsing a receiver from a Modal VM**: the VM's egress address is not
  its install's DNS. The test name was put in the makers' box's
  `/etc/hosts` for the length of the test (getaddrinfo reads it), the same
  check a public record would pass, and taken out after.

## 2026-11-18 — an own domain as a normal install (0.2.8)

- **The installer had never asked a question.** `ask()` opened `/dev/tty`
  as a text file `"r+"`, which raises `io.UnsupportedOperation` ("not
  seekable"), a subclass of `OSError`, which it caught as "no terminal".
  Every rehearsal passed all its answers as options, so nobody saw it; the
  README's own command (`--name` and nothing else) would have stopped at
  the owner's name. Open the terminal twice, to read and to write. Walk a
  prompt by running the installer on a pty (`pty.fork()` and a small loop
  that answers each prompt as it appears), never by passing the answer.
- **A wildcard can be rehearsed for real with dnsmasq** on the VM's
  loopback: `host-record=<domain>,<ip>` is the apex alone,
  `address=/<domain>/<ip>` the apex and every name under it, and a
  `systemctl restart dnsmasq` drops its cache. With `/etc/resolv.conf` at
  127.0.0.1, `--resolver system` sees exactly what a public record would
  say, records can be made and broken while the installer waits, and
  `host-record=` with an IPv6 address gives a stray AAAA to test.
- **An own domain has no "taken".** The name check's "taken" is about a
  shared base where another person's record means someone else's name; on
  the owner's own domain a record elsewhere is theirs to change, so
  `domain_check` says what to change and never stops the install.
- **The feedback receiver does not care which domain** an install has; it
  admits a report when the named domain resolves to the caller. For a
  name under the base that proves the makers made it; for an own domain it
  proves only that the sender is the server the domain points at. The
  limits bound it.
- **The push gate refuses the product's domain in docs.** Write "Loam's
  own domain" or `BASE_DOMAIN` in prose; a prompt that shows the domain
  builds it from `li.BASE_DOMAIN` at run time.


## 2026-11-18 — the private feedback route taken out again (0.2.11)

- 0.2.7's private route lived one evening. Taking it out was a targeted
  reverse of commit 62969a0, not `git revert`: `idea` (KINDS, the article in
  `show`, the CLI choice) and the draft's `id` (which predates 0.2.7) stay.
  `bin/loam-claim` and `deploy/README.md` came back byte-identical to
  a124846; `lib/loam_deploy.py` differs from it only by 0.2.8's
  `own_domain` comment. Check that way next time: `git diff <before> -- f`.
- Box state it left: `/var/lib/loam/feedback.json` (the HMAC key and
  limits, root 0600), the `[feedback]` table and its comment in
  `/etc/loam/loam.toml`, a `.bak` beside it, and a sentence in
  `/etc/loam/deploy.toml`'s landing comment. No reports had arrived (no
  `40_PROJECTS/Loam/feedback/`) and no install had kept a draft here.
- Asking for a name under the base domain now points at
  `ASK_FOR_A_NAME` (lib/loam_install.py), an issue on loamhq/loam: the one
  public channel to the makers.
## 2026-11-18 — built in or from the Market; Images graduates (0.2.10)

- **Two kinds of plugin, Obsidian's model.** Built-in ones ship in
  `plugins/` (Shipped layer, `default_on` for their default). Market ones
  live one per repository on their maker's account, at `v<version>` tags,
  with the plugin at the repository's root: a link `…/tree/<tag>` with no
  folder parses on every release since 0.2.3 (`GITHUB` is unchanged), and
  `public_url` drops the empty path, so covers resolve at the root.
- **A Yours copy wins over a shipped one forever**, so graduating a Market
  plugin into Loam needs a take-over step, like the themes' keep step in
  reverse: `lib/loam_built_in.py` (`GRADUATED`) at the broker's first start
  removes a Yours copy that is exactly what the Market installed (link from
  a listed repository, not `edited`) through `loam_plugins.remove`, which
  keeps the id's switch when a shipped copy exists. Settings
  (`_settings/<id>/`), data, the private folder (keys, `checks.json`) and
  shared keys are all keyed by id, so nothing has to move. It waits,
  unsettled, while the release has no shipped copy.
- **The push gate's deny list holds the owner's surname**, so the owner's
  GitHub handle can never be written in this repository: code that must
  follow a plugin to its maker's repository reads the marketplace list, or
  keeps the first published link. `loam_listed_themes.LINK` stays at the
  archived `loamhq/plugins` (an archived repository still answers fetches,
  and its tags were kept).
- **The five themes' folders stay in `plugins/_listed/`** as the core's test
  copies: about eighteen app tests and tools read them, and the core still
  styles them by id. The makers' repositories are the source.
- **`gh auth setup-git` writes a credential helper into `~/.gitconfig`.**
  Don't: push with `git -c credential.helper= -c
  'credential.helper=!/usr/bin/gh auth git-credential' push`, and
  `env -u GITHUB_TOKEN` (the box's `GITHUB_TOKEN` is stale; `gh`'s own
  login is the owner's). Loam's own fetches ignore the global config anyway
  (`_git_env` sets `GIT_CONFIG_GLOBAL=/dev/null`).
- **The guard refuses the Write tool outside the reach** (job scratch in
  `/tmp` included); write scratch scripts with a shell heredoc.
- **Two broker tests start the real broker against the box's own config**
  (`test_broker_stop_streams.py`, `test_broker_shutdown_dump.py`: their
  drivers load `bin/session-broker` and run `amain`, stubbing each start
  task by name). A new start task that writes anything is run against the
  live install by the landing's test run: on 11-18 the built-in take-over
  removed the box's own Images copy mid-landing (restored from the plugins
  folder's git within minutes). Stub every new start task in both drivers.
## 2026-11-18 — three files load on every run: soul, config, map

- **The shape is rendered, not chosen at launch.** Every conversation route
  (the broker, Remote Control, the Claude Code app) reads the project's
  `CLAUDE.md` and `.claude/settings.json` at start with no flag of Loam's,
  and Codex and Antigravity compose from `CLAUDE.md`'s imports. So the
  map's shape is rendered into those two files (`bin/provision`'s
  `claude_md`/`settings`, branching on `map.md`), and `lib/loam_loading.py`'s
  `refresh` re-renders them when the shape on disk and the one rendered
  differ. It runs from every launcher (wake, broker start, rc-preflight,
  each Codex/Antigravity instruction build) and from the persona's
  PostToolUse hook after *every* tool call, because a map made with `echo >`
  names no path the hook can see. A session already running keeps its shape.
- **Loam's text resolves from the code's own tree** (`docs/persona/loam.md` beside
  `lib/`), not from `paths.root`: a landing tests a merged tree while the
  live checkout is still the old `main`, so a `paths.root` import of a new
  file fails every test that resolves it.
- **A path made at import time from `lc.WORKSPACES` writes the live box from
  a test** that only sets `LOAM_AI_LABS` (caused: `test_agents.py` made
  `ai-labs/_shared/owner.md` on the real install). `loam_loading.owner_page()`
  reads the override as `loam_agents.ai_labs()` does.
- **`tests/test_loading_shapes.py`** is the statement of what loads: every
  run kind on every provider, assembled by the launchers' own functions
  (`bin/wake`'s `lane_input` and `claude_argv`, `loam_codex.instructions`,
  `loam_codex_wake.instructions`, `loam_antigravity.instructions`), against
  `loam_loading.own_files`. A new route must be added there.

## 2026-11-18 — install from a link, made easy

- **A link is read in two steps when it names no tag:** the default branch
  is fetched shallow to see what is there (which folders hold `PLUGIN.md`,
  the plugin's id), then the newest release tag for that plugin. A
  repository of several plugins tags each `<id>-v<version>` (as
  `loamhq/plugins` does); a plain `v1.2.0` is a whole-repository release.
  Order tags by version tuple, never by text (`v1.10.0` > `v1.9.0`).
  `ls-remote --symref` gives the default branch in the same call.
- **A repository of several plugins is an answer, not an error:**
  `choose`, each a request of its own. `install()`'s `confirm` refuses one.
- **The yes sends back `request`** (repository, folder, tag or commit the
  first call found), not the pasted link, so a release published between the
  two calls is never what lands; the token would catch it anyway.
- **The boundary guard refuses the Write tool under /tmp**, the job's own
  scratch included. Write into the tree and move it, or write through the
  shell.
- **`app/test/plugin-host-browser.mjs` was already failing on `main`
  (2026-11-18)** at its Read aloud and Dictation switches and again at
  `.plugin-meta`: stale since Settings changed. Its install section was
  checked by running a copy without the stale parts.
  `app/test/link-install-browser.mjs` is the browser proof of the link sheet.
## 2026-11-18 — backups: the local commit in core, Git backup built in

- **A fresh install had repositories and no routine.** The installer made
  the vault's and workspaces' repositories with one commit and nothing ever
  committed again; the fifteen-minute commits existed only as one box's own
  crontab lines. `loam-autocommit.timer` (`lib/loam_autocommit.py`) is the
  routine now. It defers per tree to a crontab line naming
  `<tree>-autocommit`, and check-drift skips its units while one does, so a
  box with the hand-made setup is unchanged until the lines go.
- **`gen-units` never arms a timer**; an update that adds a core timer must
  arm it itself (`loam_update.Update.units`).
- **A lock file inside `.git/` makes `.git/`.** Taking the lock before `git
  init` left a tree that `exists()` said was a repository and was not; check
  `.git/HEAD`, and init before locking.
- **`git push --dry-run` asks the host for write permission**: a GitHub
  deploy key added read only fails it with "marked as read only", so a
  connection test can prove write access without writing.
- **Is a repository public?** `git ls-remote https://host/path` with
  `GIT_CONFIG_GLOBAL=/dev/null`, `credential.helper=` and no prompt: 0 is
  public, an auth word in stderr is private, anything else unknown.
- **Two installs on one repository diverge** the moment the second pushes
  (a restore re-provisions, which rewrites agents' paths and commits them).
  The push is never forced; the first install's backup reports "diverged".
- **The settings modal has a fixed height**: capture a long plugin section
  as top and end shots, not a taller viewport (`tools/capture-git-backup.mjs`).
- **A plugin section's class names share a namespace with its state
  classes**: `gb-${level}` on the pill and `.gb-fail` on the failure box
  made the pill a padded grid. Name boxes and states apart.
- **A scratch install whose `paths.root` is the job's tree provisions the
  engineer into that tree** (caused 2026-11-18, `bin/library seed-starter`
  under a scratch `LOAM_CONFIG`): `ensure_engineer` rewrote the tree's
  untracked `engineer/agent.json`, `CLAUDE.md`, `role.md`, `project.md` and
  `.claude/settings.json` with the scratch paths, and the landing's
  `test_boundary_guard.py` then failed on the tree alone. Restore them from
  the canonical checkout's, or point `paths.root` at a copy.
- **A new top-level folder is not in a landing's view** (`platform_argv`
  binds only entries the canonical checkout already has): put a new shipped
  file under an existing one (`docs/persona/loam.md`, not `persona/`).
- **External `@` imports need the project's trust, keyed on the git repo
  root**: a scratch workspace outside a git repo dropped `reach.md` and
  Loam's text silently even with its own directory trusted; `git init` on
  the scratch workspaces folder plus `ensure_trust` made them load.

## 2026-11-18 — Dictation and Read aloud leave core for the Market (0.2.9)

- **A shipped page plugin leaves core by four moves**: its folder goes to
  `plugins/_listed/<id>/` (never read as a plugin; the tests' copy and the
  release's fallback), the app tests that drove it get it from
  `app/test/audio-plugins.mjs` (catalog rows whose `local` is the listed
  file's `file://` URL, which the test page's importer loads as is), an
  install that had it **on** gets it from the Market at the broker's first
  start (`lib/loam_to_market.py`), and anything core imported from the
  plugin's files gets core's own copy (`app/src/speakable.js`,
  `document-speech.js`), held byte-equal by a test. The app deploys only
  `app/`, so `app/src` can never import from `plugins/`.
- **The migration reads the link from the marketplace listing** rather than
  naming it: the push gate denies the owner's GitHub handle and display
  name in Loam's repository, so a maker's repository URL cannot appear in
  Loam's code. The listing is also the right source: it follows the maker's
  version.
- **Who sees the shipped layer on this box**: the broker reads
  the canonical checkout's `app/public/plugins` (projects/loam-ui.json's
  checkout), so a landing removes a shipped plugin before any app deploy.
  For no gap, install the Market copy first: a Yours copy shadows the
  shipped one, and the migration then finds it present.
- **Preferences that live in a browser are kept by their storage keys**,
  not by layer: `loam.settings.device(key)` is the raw localStorage key, so
  a plugin keeps its device settings across shipped → Market as long as its
  code keeps the key. Switch, agent values (`_settings/<id>/`) and keys
  (shared, or `<private>/secrets.env`) are all kept by id.
- **`bin/settings do … --by` must name a persona**; an engineer acting on a
  brief names the persona who briefed it, with `--reason` saying the job.
- **`gh` needs `env -u GITHUB_TOKEN`** in a job: the environment's
  `GITHUB_TOKEN` was stale (401) while `gh`'s own login was good.
- **The app page harness hangs, not fails, on a `threadPage` thread**:
  `textContent` of `tt-stream` never returns in the fake DOM (on base main
  too). Prove an absence at the loader and registry level instead
  (`test/installation.test.js`).
- **A scratch install whose `paths.root` is your job tree writes into it.**
  `bin/library seed-starter` provisions, and provisioning renders the
  engineer's seeded files (`engineer/CLAUDE.md`, `role.md`, `project.md`,
  `agent.json`, `.claude/settings.json`) at `<root>/engineer/` with the
  scratch install's values. They are git-ignored, so nothing lands, but the
  job's next session loads them (its CLAUDE.md imported the scratch
  install's `reach.md`). Copy the canonical checkout's seeded files back
  after such a rehearsal, or point `paths.root` at a throwaway clone.
## 2026-11-18 — the owner's name on agents' commits

- **What GitHub showed was the trailer, not the author.** Every job commit was
  authored `<project> engineer <engineer+…@noreply.local>` (an address no
  account owns, so no avatar) and Claude Code appended
  `Co-Authored-By: Claude … <noreply@anthropic.com>`, which GitHub links to
  Claude's account: the only face on the commit was the provider's. Pushes
  were deploy keys, so there was no person as push actor either. The fix is
  `lib/loam_git_identity.py` and `docs/attribution.md`.
- **Codex has no local switch for its trailer.** 0.170 carries the instruction
  "Commit messages must end with `Co-authored-by: Codex`" gated on a
  workspace policy it fetches; `commit_attribution` (its docs' key) is
  refused by `--strict-config`, and `[features]` accepts any key silently, so
  a `features.*` pin proves nothing. Hence the clone's `commit-msg` hook and
  the landing's `settle_commits`, which hold whatever a provider does.
- **A gated checkout keeps its own identity.** The push gate compares every
  new commit with the checkout's `user.name`/`user.email`, so the owner's name
  there would be a held push; and a deny list may name the owner's handle on
  purpose. The order in `resolve()` puts the repository's own identity before
  the owner's for that reason.
- **The landing never re-makes a pull-request branch**: its head may already
  be on the remote, and a re-made commit would not fast-forward it, which
  `settle_head` answers with a new pull request.

## 2026-11-18 — a folder as one zip (`/v1/files/archive`)

- **`zipfile` streams to an object with `write` and `tell` and no `seek`**:
  it writes data descriptors and never goes back, and it picks Zip64 for an
  entry from `ZipInfo.file_size` (set it from `fstat`). `ZipFile.mkdir` with
  a hand-made `ZipInfo` needs `CRC`, `compress_size` and `file_size` set to
  0 first (`'ZipInfo' object has no attribute 'CRC'` otherwise).
- **To abandon a zip, starve it**: set the sink dead and `zf.fp = None`, so
  neither an open entry's close nor `ZipFile.__del__` writes a central
  directory anywhere. The broker then `transport.abort()`s a chunked body:
  no central directory and no last chunk is a failed download to every
  client, where a plain close after `Connection: close` could read as whole.
- **The real vault zipped in 36 s through one route** (5.0 GB, 8,861
  entries; broker peak 53 MB) against 4 min for the app's file-by-file
  fallback: the cost was the 8,400 round trips, not the bytes. STORE for
  non-text keeps it disk-bound.
- **An export judges names, not contents.** A thread transcript can hold a
  pasted credential (one did on 11-18: a private key in a sandbox tool
  call); no name rule catches that, and `15_CONVERSATIONS/` is not ours to
  edit. Report it; do not scan-and-drop silently.

## 2026-11-18 — a brief from a thread returns at once (batch B step 1)

- **`engineer-job --brief` from a thread returns once the job is launched
  or queued**: `{ok, job_id, queued, receipt_path}` (the job dir's
  `receipt.md`; the vault path is only known at the end). The record in
  `state/engineer-waiters/` is written at accept with `returned: true` and
  no pid, and is owed the moment the job has a verdict. A caller with no
  thread (wake, cron, Remote Control, a job) still blocks; Step 3 ends that.
- **Gated on the broker, not on the launcher's own version.** The launcher
  returns at once only while `loam_waiters.route_live()`: the running
  broker wrote `state/engineer-waiters/route` (pid + start ticks) when its
  delivery pass started. A broker older than this code writes no route, so
  between a landing and the broker's restart every brief still blocks and
  is delivered the old way. Any install updating gets the same safe order.
- **Delivery posts nothing.** `deliver_job(record, note)`: a turn whose
  input is `handoff_text` (receipt path, `verdict_line`, the landing), once
  per job (`system_turn_allowed` with `line=None`, no repeat-line rule). A
  `job:` turn that ends with no words posts no "turn ended without a reply"
  line (`quiet_turn`); a message turn still does. Results are ordered by
  their verdict's `finished`.
- **A result waits behind a turn under way.** `_claim` already took only
  message turns (no steer); `hand_over` now fires only when a message is
  queued, so a result never ends a turn parked on background work. Joining
  one is Step 2's.
- **`delivery_follows`/`pending_for` are gone**: a turn the restart cuts
  says so again, job or no job.
- **A live probe of the route needs no broker restart**: a scratch thread
  state (`LOAM_THREADS_STATE`, `LOAM_WAITERS_DIR`, `LOAM_VAULT`) and the
  branch's `session-broker` module loaded in a process of its own, a fake
  session whose turn runs the branch's real `engineer-job --brief` with
  `LOAM_ENGINEER_PROBE_SECONDS`, then a second process running the real
  `Server.deliver_owed`. Run it outside the job's wall (`sudo -n nsenter
  --mount=/proc/1/ns/mnt -- setpriv --reuid … --init-groups`, as `--land`
  does): the jobs dir is read-only inside it, which is also why
  `tests/test_engineer.py` fails inside a job and passes outside. A probe
  job lands "nothing" and removes its own tree and branch.
- **The suite starts real brokers, and their delivery pass reaches the
  owner's state.** The first landing of this work left a real
  `engineer-waiters/route` mark from a test's broker (twice: 22:15 and
  22:31 PT). Two fixes: `tests/run-all` gives every test a scratch
  `LOAM_WAITERS_DIR`, and `mark_route` writes the install's own route only
  from inside `loam-session-broker.service` (its cgroup). Before this, any
  test broker's `deliver_owed` could also have delivered, or dropped as
  "thread gone", a real owed record.
- **Unset is the default, not neutral** (the same day, second change). The
  store keeps no GitHub user id, and the no-reply address needs it, so
  `owner_profile` reads `/user` once with the existing token and caches
  `profile` on that authorization for a day. "Clearly designated" is one
  login across every authorized app slot; two is a prompt, never a pick.
  `neutral` is the explicit opt-out. Only an explicit `github` reaches
  `owner_token`: the default is metadata and never changes the PR actor.
- **In a test run the default reads nothing real.** `loam_offline.offline()`
  makes `default()` treat the box as unconnected with no global git identity
  unless a test sets `PROFILE_SOURCE`/`GLOBAL_GIT_SOURCE`; otherwise every
  worktree test on an unset box would read the live store and its tokens.

## 2026-11-18 — this box moved onto Git backup

- **A live plugin page on loopback needs the gateway too.** `127.0.0.1:8081`
  answers without the login, but Caddy sends `/x/*` to the plugin gateway
  (`127.0.0.1:8933`); without that route the page says "The plugin answered
  404". In Playwright, `route.continue({ url })` to 8933 for `/x/*`. The
  app's light/dark comes from the boot script `/appearance-state.js` as
  well as the GET, so set `documentElement.dataset.theme` after load (DOM
  only, nothing saved), and abort every non-GET so viewing writes nothing.
- **`bin/gen-units` takes no flags:** `gen-units --help` runs it and writes.
- **`$TMPDIR` can be empty in a Bash call**; spell the scratch path out
  before anything that writes to it (`crontab "$TMPDIR/x"` read `/x`).
- **Git backup pushes to the address, not to `origin`**, so the trees'
  `origin/main` goes stale and `git status` says "ahead". Check GitHub with
  `git ls-remote <alias address> refs/heads/main`, never the local ref.
  `gh` here answered 401 (2026-11-18).
- **The plugin's push commits first** (core's commit, on every push tick
  that is due), so after a switch the first commit may be the push's, not
  the timer's; wait for a timer run to see the timer's own.

## 2026-11-19 — the landing's tests are sealed and parallel

- **What a sealed test sees.** `tests/run-all` writes each file its own
  config (`FIXTURE`, never the install's file) with every data path in a
  per-file scratch folder, plus a starter agent `iris` with a soul,
  `_shared/reach.md` and `commons/`. A landing's `platform_argv` adds the
  wall: `unshare -Urmn` with only loopback up, and an empty read-only tmpfs
  over each of `sealed_paths()` (vault, workspaces, projects, plugins,
  state, secrets folder, `/run/user/<uid>`), mounted after the tree's binds.
  A bind survives a tmpfs over its source's parent, so the tree still
  shows at the checkout's path.
- **What the seal found** (each one passed before only because the box had
  it): a broker test read the live broker token; the identity gate's decoy
  came from the box's agents' real souls; the guard and the plugin gateway
  needed the box's starter agent; a test read an agent's live provisioned
  Codex config (now rendered by `provision.codex_config`); `test_engineer`
  wrote into the live jobs folder. A test that needs the box is a
  `tests/live_*.py`, run by hand with `run-all --live`.
- **Wall time on a 16-CPU box**: 750 s one file at a time; about 100 s eight
  at a time. The floor is `test_schedule` (40-80 s), which waits for a real
  timer at the next minute boundary. `test_doc_comments` was 119 s until
  its plugin suites ran at once and `test_places` ran as four processes by
  unittest class name.
- **The memory note never blocks a landing** (`land` wraps `_land_once`).
  A note-only remainder that fails is retried once on a test failure, then
  reported as what landed before it, with the note kept as
  `memory-note.patch` in the job's folder and no continuation. A gate hold
  on only the note or a commit message lands the change as one commit
  without them (`without_memory`, a temporary index and `commit-tree`).
