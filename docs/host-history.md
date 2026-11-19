# The host's own setup: kept, and put back

*Since 2026-11-19. `lib/loam_host.py`, `bin/loam-host` (`loam host`),
`systemd/system/loam-host-history.{service,timer}`. Tests:
`tests/test_host_history.py`.*

Agents have full control of the machine Loam runs on: a shell and sudo. So
rather than more guards, Loam makes what they can break something it can
put back. The vault, AI Spaces and the plugins' Yours layer are each
committed every fifteen minutes already (docs/backup.md). This is the same
for the host's own setup: the web server's config, Loam's units and which
of them are enabled, its config and settings.

## Putting it back

```
sudo loam host restore              # the newest state whose checks passed
sudo loam host restore 2h           # as it was two hours ago
sudo loam host restore '2026-11-19 09:00'
sudo loam host restore a1b2c3d4     # a state from `loam host log`
sudo loam host restore 2h --only /etc/caddy     # just the web server
sudo loam host restore 2h --except <the login's folder>   # all but the login
sudo loam host show 2h --diff       # what would change, changing nothing
sudo loam host log                  # the states, newest first, with what changed
```

`restore` first says every file it will create, change or remove (with
what differs: contents, mode, owner, link target) and what it will do
after: `daemon-reload`, starting a unit whose enablement link comes back,
stopping one whose link or file goes, restarting a unit whose file changed
if it is running, reloading the web server, `sysctl --system`, and
`docker compose up -d` for a compose file under `/etc/loam`. Then it asks
(`--yes` skips the question; with no terminal and no `--yes` it changes
nothing). `--diff` shows each text file's change; a secret's contents are
never shown. `--only PATH` limits it to a file or folder and `--except
PATH` leaves one out; both repeat.

Before writing, it checks the state it is about to write: a Caddyfile must
pass `caddy validate`, a sudoers file `visudo -c`, a TOML or JSON file must
parse. A state that fails is refused unless `--force`. It then saves the
present state, so a restore can itself be undone: the last line names the
state to restore to undo it.

**It works when Loam is down.** It needs git, systemd, Python and this
one file, which imports only the standard library. It does not talk to the
broker, the app or the network, and it does not need `/etc/loam/loam.toml`
to be readable: each state carries the list of what it covers
(`scope.json`) and each file's place, mode and owner (`meta.tsv`), so the
state itself says what to put back where. Proved 2026-11-19 on the first
install with the staging app stopped and the command run with no network
at all (`unshare -n`).

**Choosing the state.** A break is saved too, at the next quarter hour, so
"the newest state" can be the broken one. The default is the newest state
whose checks passed, which skips a Caddyfile that does not validate; a
break that is still valid config (a unit disabled, a wrong port) needs a
time or a state from `log`. When nothing differs from the state chosen, the
command says so and lists the newest states to choose from.

## What is covered

`sudo loam host covered` prints the rules and every path they match now.
The rules (`rules()` in lib/loam_host.py):

| Where | What |
|---|---|
| `/etc/loam/**` | Loam's config (`loam.toml`, `deploy.toml`) and what `gen-deploy` renders there: the login's and the browser's compose files and walls, Authelia's config and secrets file, the login page, share links |
| `/etc/caddy/**` | the web server's config |
| `/etc/systemd/system/loam*`, `loam*.d/*`, `*.wants/loam*`, `caddy.service.d/*`, `docker.service.d/*` | Loam's system units, their drop-ins, and which are enabled |
| `/etc/sudoers.d/loam*`, `/etc/sysctl.d/*loam*`, `/etc/docker/daemon.json`, `/etc/claude-code/managed-settings.json`, `/etc/profile.d/loam.sh`, `/usr/local/bin/loam` | the system settings Loam installs |
| `/var/lib/systemd/linger/<user>`, the user's crontab (`/var/spool/cron/crontabs/<user>` or `/var/spool/cron/<user>`) | that Loam's user manager runs without a login; the user's cron lines |
| `~/.config/systemd/user/loam*`, `loam*.d/*`, `*.wants/loam*` | Loam's user units and which are enabled: only Loam's, so the person's own units on a machine they also use are left alone |
| `paths.secrets_env`, `paths.wake_env` | the runtime's key files |
| `<state>/broker/settings.json`, `<state>/plugins/*.json`, `keys/*`, `lanes/*`, `<state>/sites/*.toml`, `<state>/github/*.json` | the owner's settings, which built-in plugins are on, the plugins' keys, the agents' sites, the GitHub connection |
| the checkout's `projects/*.json` and `deploy/*/*` that git does not track | this install's own project and app records (a tracked file has the checkout's history, and an old one must never be put over a newer release's) |
| `~/.ssh/config` | which key reaches which git host |
| the login's volume (`authelia-data`): `users_database.yml`, `db.sqlite3` | the login's accounts and their password hashes; its passkeys and one-time-password secrets. Only where Docker and that volume are found; see below |

## The login's accounts and passkeys

*Since 2026-11-19 (the same day, a second job).* The login (Authelia) keeps
the accounts and their password hashes in a users file and the passkeys
and one-time-password secrets in a SQLite database, both in the Docker
volume `authelia-data`. Without them in the history, a restore could put
back the login's config while the owner's passkey was gone.

**Found, not assumed.** Each snapshot asks Docker where the volume is
(`docker volume inspect`); with user namespaces remapped it is under
`/var/lib/docker/<uid>.<gid>/`, and elsewhere somewhere else. No Docker, or
no such volume (an install without the login), and there is nothing to keep
and nothing is said; `sudo loam host covered` says which it found. If
Docker is there but does not answer, the folder the last state found is
used while it still exists, so a Docker restart does not drop the login
from a state. A folder Docker names that this machine cannot reach (Docker
running in a VM) is not kept, and `covered` says so.

**Copied without stopping the login.** The database is not copied as a
file: a file copy of a live SQLite database can catch a write half done.
It is read with SQLite's online backup API (Python's
`sqlite3.Connection.backup`), which copies every page under SQLite's own
shared lock, so the copy is the database at one moment whatever Authelia is
doing; a write in progress makes the copy wait (Python retries while the
database is busy), never tear. It opens the database read-only, in a child
process running as the file's owner (the container's user as the host
sees it), so it never leaves a journal or WAL index in the volume owned by
root that the container could not then open. The copy goes to memory and
into the history. If it cannot be taken (the file's owner cannot read it,
it times out), the last copy is kept and the state's message says so: the
database is never recorded as gone because one copy failed.

**No new state when nothing changed.** The online backup of an unchanged
database is the same bytes every time (measured: three copies of this
box's 320 KB database, the same SHA-256; on a scratch login, three
snapshots after the first made no state). It differs from the file on disk
in its header, which is why it is the copy that is compared, not the file.
Authelia writes to the database on a sign-in, a passkey or one-time
password added or removed, and a failed attempt, so a state is made after
those and not otherwise.

**Both are secrets**, whatever their modes: their contents are never shown
by `--diff`.

**Putting them back says so first.** A restore that would change either
ends its list, just above the question (and again below a diff), with:

```
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
!! THIS PUTS BACK THE LOGIN'S ACCOUNTS AND PASSKEYS as they were
!! at 2026-11-19 10:58 PST (state e3ea728c27).
!! Any passkey, account, password or one-time-password app added or
!! changed since then is REMOVED. If that is how you sign in, you will
!! be locked out of Loam.
!! To put back everything else and leave the login as it is now, add:
!!   --except /var/lib/docker/…/volumes/authelia-data/_data
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
```

When it goes ahead it stops the login's container (`docker stop
loam-authelia`), removes any `-journal`, `-wal` or `-shm` beside the
database (they belong to the copy being replaced, and replaying them onto
the restored one would corrupt it), writes the files with their owner and
mode, and starts the container again. A login that was not running is
left stopped, and the restore says so. Sessions are in Redis, not in this
volume, so a restore does not sign anyone out.

**One thing a restore cannot do**: a passkey's signature counter. Authelia
refuses a passkey whose counter is lower than the one it stored, as a sign
the key was cloned. A restore puts back an older, lower stored counter,
which a real device's ever-rising (or, for synced passkeys, always zero)
counter passes. Only a copy of a key taken out of a device could fall
behind, and that is the case the check is for.

Proved 2026-11-19 on a scratch login, never the owner's: its own Authelia
container (the same image and config shape, its own volume, on loopback),
a passkey enrolled through Authelia's settings with a virtual authenticator,
a state taken, the passkey removed with Authelia's own
`authelia storage user webauthn delete` and the account removed from the
users file (both sign-ins then refused), `loam host restore` answered at a
terminal, and the passkey alone then signed in.

## Not covered, and why

- **The vault, AI Spaces, the plugins' Yours layer**: each is its own
  history (docs/backup.md).
- **Loam's checkout**: a git checkout of a release; `loam update` and git
  put it back.
- **Runtime data** (sessions, threads, jobs, logs, schedules' records):
  what Loam writes as it runs, not setup; large and changing every minute.
- **Plugins' private data** (`<state>/plugins/private/`): each plugin's
  own.
- **Caddy's certificates and data**: private keys it re-issues by itself.
- **Docker images, containers, and volumes other than the login's two
  files**: rebuilt from the covered compose files; an app's own data is the
  app's. The login's sessions (Redis, `authelia-sessions`) cost one sign-in
  per device if lost; its notification file holds one-time codes, not setup.
- **Packages, the kernel, the network, the base firewall**: the operating
  system's, not Loam's. A whole-machine snapshot is the provider's or the
  person's own tool (and many machines Loam runs on have none).
- **SSH keys**: identity, not setup.
- **A covered file over 2 MB**: named in the state's message, not kept.

## Where it is kept, and who can read it

One git repository, `paths.host_history` (default
`/var/lib/loam/host-history`), owned by root, mode 0700: readable by root
alone, so a copy of a 0600 key file is no more readable than the file. A
file is marked a secret when other users cannot read it, or when its name
says so (`secret`, `token`, `password`, `credential`, `.env`, `.key`,
`.pem`); a secret's contents are never printed by `restore` or `show`.
Nothing is pushed anywhere.

## What it costs

`loam-host-history.timer` (root's, every fifteen minutes, at :04, :19, :34
and :49, and two minutes after boot) runs `loam host snapshot --quiet`. It
compares the covered files with the last state; when nothing differs it
writes nothing and makes no commit (about 0.2 s of one CPU on the first
install, 154 files). It never wakes an agent and never posts. A state is a
commit only when something changed; the first state of the first install,
154 files, was 1.6 MB on disk.

The login's database adds a copy (an online backup in a child process) to
each run. On the first install, with its 320 KB database, an unchanged run
took 0.29 s wall time and made no commit. The history went from 1.7 to
1.9 MB across the state that first took the login's two files and one
other state just before it, and on a scratch login each change to the
311 KB database added about 55 KB.

The installer installs and enables it (step 9) and takes the first state;
`loam update` installs it on an install made before it (the `host` step),
and `sudo loam host install` does it by hand. `check-drift` reports the
timer off or its units stale.

## Assumed of the machine

Linux with systemd: the system manager, and a user manager for Loam's user
(`systemctl --user -M <user>@`, systemd 248 or later). git, Python 3.11 and
`tar`. Caddy is validated and reloaded only when it is installed; Docker
Compose files are re-applied, and the login's files kept and put back, only
when Docker is. Nothing assumes a cloud
provider, a snapshot API or one machine's paths: every path comes from
`/etc/loam/loam.toml`, the user's home and the standard places (`/etc`,
`/var/lib`, `/var/spool/cron`).

## Off the machine: not yet

The history stays on the machine on purpose. It holds key files and the
login's accounts and passkeys, and the Git backup plugin pushes in the
clear to a repository on someone else's host, so pushing this as it is would put the box's secrets there. What it
would take:

1. **A second history without secrets**, derived from this one: each
   secret's contents replaced by a fingerprint, so the push carries the
   web server's config, units and settings and no key. A restore on a new
   machine then needs the keys from the person (they are what the
   installer asks for anyway).
2. **The Git backup plugin pushing it** as a third repository beside the
   vault and AI Spaces, with its own deploy key and the same public-repo
   refusal.
3. **Secrets, later and only encrypted** to a key the person holds off the
   machine (an `age` recipient they paste in), never a key kept on the box
   beside them.

Even the derived history names the box's domains, hosts and app layout,
so it would be opt-in, and off by default.
