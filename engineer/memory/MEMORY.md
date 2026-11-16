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
