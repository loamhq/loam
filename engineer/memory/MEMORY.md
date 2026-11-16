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
  old commits. The landing notices (the job's recorded base is not in
  `main`) and replays only the job's own commits onto the new `main`
  (`replay_if_rewritten`), so a merge never brings the old history back.

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
