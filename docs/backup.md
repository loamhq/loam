# Backing up: local history, and the copy off the machine

*Since 2026-11-18. Two halves: the routine local commit, in core
(`lib/loam_autocommit.py`, `bin/autocommit`, `systemd/loam-autocommit.*`),
and the **Git backup** plugin, built in and off by default
(`plugins/git-backup/`). Tests: `tests/test_git_backup.py`. A copy taken by
hand, as one zip, is Files' Download as zip: docs/download.md.*

## The floor: every change, committed on this machine

The vault, AI Spaces (the agents' workspaces) and the plugins' Yours layer
are each their own git repository. Every fifteen minutes
`loam-autocommit.timer` commits each one, on this machine, as
`<tree>: <UTC time>` under the install's `git.autocommit` identity
(docs/config.md). Nothing is sent anywhere. This is the history every agent
is told a regretted change can be got back from, and it runs whether or not
any backup is set up.

- The installer makes the vault's and AI Spaces' repositories on `main`
  with one commit, `Loam: the starting point` (`bin/loam-install`, step 6).
  `bin/autocommit` makes any missing one the same way.
- **A file over 100 MB is kept out** (`git.autocommit.max_file_mb`). Git
  hosts refuse such files (GitHub at 100 MiB), and one in history would stop
  every later push. It stays on disk, uncommitted; a tracked file that grows
  past the limit keeps its last committed version. Each tree's record,
  `<state>/autocommit/<tree>.json`, names them (`held`), and the Git backup
  page lists them for the owner.
- **Runtime churn is kept out of AI Spaces' commits**: `scratch/`, `logs/`,
  `cache/`, `.locks/`, `node_modules/`, `__pycache__/`, `.venv/`. They are
  written into the repository's own `.git/info/exclude`, between marker
  lines, never into a tracked file.
- **A hand-made routine wins.** A box whose crontab runs
  `bin/vault-autocommit`, `ai-labs-autocommit` or `plugins-autocommit`
  (the first install's own, from before this existed) keeps that line for
  that tree; the core routine leaves the tree alone and says so in its
  record, and `check-drift` does not count the timer as missing. Taking the
  line out hands the tree over at the next quarter hour.
- `loam update` installs the timer and arms it once on an install made
  before it: `gen-units` arms any timer it installs for the first time
  (since 2026-11-19), which is what reaches an install whose older release
  runs the update, and `lib/loam_update.py`'s `units` step arms it too.

**The host's own setup** (the web server's config, Loam's units, its
config and settings, and the login's accounts and passkeys) has a history
of its own, root's alone and kept on the machine: `loam host`,
docs/host-history.md.

## The copy off the machine: the Git backup plugin

Settings › Plugins shows when off-box backup is off. Open Git backup there
to turn it on and connect one private repository for the vault and one for
AI Spaces, on any git host
(GitHub, GitLab, Codeberg, Gitea, their own), and from then on each is
pushed there on a schedule.

**Connecting needs no terminal.**

1. They make an empty private repository and paste its address. A web or
   HTTPS address becomes the SSH one (`git@host:owner/repo.git`).
2. Loam checks the repository is not public (below), makes an ed25519 key
   pair for that tree alone (`<state>/plugins/private/git-backup/keys/`,
   0600, never in a tree), and shows the public half with the host's own
   steps and a link straight to its deploy-key page.
3. They add it as a deploy key with write access and choose *test*. The test
   is `git ls-remote` and then `git push --dry-run`, which the host answers
   with its write permission and which changes nothing: a key added without
   write access is caught here, in those words. On yes, the first backup
   starts.

A deploy key reaches that one repository and nothing else in the account,
works on every host, and needs no token. *Why not "sign in with GitHub and
let Loam make the repositories"*: it would be friendlier for someone who has
never made one, but it is GitHub only and needs a token with `repo` scope,
which reaches every private repository the person has. Not built; the
deploy-key path is the one that works everywhere.

**No host is assumed** (2026-11-19). Loam may run on a personal server, a
laptop or any provider's VPS, so the plugin's words name GitHub as one host
among several and three cases are first-class:

- *A git host* (GitHub, GitLab, Codeberg, Gitea, Forgejo): its own
  deploy-key steps and a link to its page, as above.
- *A server of the person's own* over SSH (`ssh://git@nas.home:2222/srv/git/vault.git`,
  `git@backup.example:vault.git`): the key goes in as a deploy key if the
  server runs a git host, or as one line of the owning account's
  `~/.ssh/authorized_keys` if it is plain SSH. No page link; the steps say both.
- *A folder on this machine* or a drive attached to it (`/mnt/usb/vault.git`,
  `~/backups/vault.git`, `file://…`): no key is made and there is nothing to
  add. A new or empty folder is made an empty bare repository at connect; a
  folder that is already a bare repository is used as it is; one holding
  anything else is refused, so nobody's files are written into. Its failures
  say the folder is missing, detached or not writable, never "the host did
  not accept the key".

**Agents set it up the same way**, on the owner's word: `backup_connect`,
`backup_test`, `backup_status`, `backup_now`, `backup_disconnect`,
`backup_restore` (the plugin's `git-backup` tool), and the settings through
the one route everything uses:

```
bin/settings set plugin.git-backup.vault_remote  git@github.com:sam/vault.git
bin/settings set plugin.git-backup.spaces_remote git@github.com:sam/ai-spaces.git
bin/settings set plugin.git-backup.every 1h        # 15m, 1h, 6h, 1d
bin/settings set plugin.on git-backup true        # or false
```

A remote set this way without `backup_connect` is pushed with whatever SSH
identity reaches it (an alias of the owner's own, below); when none does,
its failure says the key was not accepted, and the page offers to make one
for that repository, which is `backup_connect` again.

**The push.** `loam-plugin-git-backup--push.timer` fires every five
minutes; a tree is pushed when its interval has passed (`every`, default 15
minutes). Each push first runs the core commit for that tree, then
`git push <address> HEAD:refs/heads/main` with the tree's own key. It is
never forced: a repository with history that is not this machine's is left
as it is, and the owner is told. With nothing new, it still talks to the
host every six hours, so a revoked key is found on a quiet day.

**It refuses a public repository.** Before connecting and before every
push, Loam asks the host, signed out and with none of the owner's git
config or credentials, for the repository at its HTTPS address. If anyone
can read it, nothing is sent and the owner is told at once. If the host
asks who is reading, it is private; if it cannot be asked (no HTTPS on that
host, no network), the push goes ahead. Tests stand in for the signed-out
address with `LOAM_GIT_BACKUP_ANON`.

**The owner sees the health.** The page shows, for each tree, when it was
last backed up, how many changes wait, files too large to send, and a
failure in plain words: the key was not accepted (removed, or never added),
read only, the repository has other history, the host or account is out of
space, a file too large, the host's key changed, the host cannot be reached.
`backup_status` says the same to an agent. **A failing backup is not
silent**: at its second failed attempt in a row (a network failure after two
hours; a public repository at once) the selected attention agent receives a failure fact and then a recovery fact when backup works again.

Where things are: the keys and `known_hosts` (first contact is trusted and
remembered) in `<state>/plugins/private/git-backup/`, with `state.json`
(each tree's last push and failure); the addresses and how often in
`<plugins>/_settings/git-backup/values.json`, which is backed up with the
plugins' Yours layer.

## Getting everything back

On a new install:

1. Turn on Git backup and connect the same two repositories (each gets a
   new key; add both as deploy keys with write access).
2. Ask an agent to restore them ("restore my vault and AI Spaces from the
   backup"), which calls `backup_restore` for each. From a shell:
   `python3 plugins/git-backup/tools/backup.py restore vault` (and `spaces`).

A restore fetches the repository's `main` and puts it in place **in the
same folder**, so nothing that points at the folder needs to change. What
was there is kept: the old commit as `refs/loam/before-restore` in the
tree's history, and the files as they were in `<tree>.before-restore-<time>/`
beside it. On a tree that is still exactly the installer's starting point it
just runs; on any other it refuses until asked again with `replace`, which
an agent passes only once the owner has said yes. After AI Spaces is back,
each agent is provisioned again for this machine (`bin/provision`, as an
update does), which rewrites the paths in its rendered files to this
install's own.

What a restore does not bring back: files that were too large to back up,
anything outside the two trees (the plugins' Yours layer, the keys in
`~/.config/loam/`, sign-ins), and the agents' run history in `<state>/`.

**How it was proved** (2026-11-18): two scratch installs on one box, each
with its own `LOAM_CONFIG`, two private GitHub repositories, deploy keys
added through the API in place of the three clicks. Connect, a read-only
key refused, the first and the routine push arriving, the key deleted on
GitHub and a failure notification on the second attempt,
the recovery line, a public repository refused, and both trees restored
onto the second install, the vault byte for byte.

## Adopting a hand-made setup

A box whose trees were already pushed by hand-made scripts (a crontab
running `bin/vault-autocommit` and `ai-labs-autocommit`, an `origin`
remote reached through SSH host aliases in `~/.ssh/config`) moves over
without new keys or repositories:

1. Turn Git backup on and set each remote to the address the tree's
   `origin` already uses, alias and all
   (`bin/settings set plugin.git-backup.vault_remote git@github-vault:owner/vault.git`).
   With no key of its own for that tree, the push uses the alias's
   `IdentityFile` from `~/.ssh/config`, as the old script did. Nothing is
   pushed twice: the remote already has the history. The push goes to the
   address, not to `origin` by name; after each one the plugin moves
   `origin/main` (any remote whose address is the same repository) to what
   it sent, so `git status` does not read "ahead" (since 2026-11-19).
2. Take the autocommit lines out of the crontab. The core timer commits the
   trees from the next quarter hour; `gen-units` and arming
   `loam-autocommit.timer` once makes it run (`check-drift` then expects it).
3. The old scripts can stay in `bin/` until no box runs them.
