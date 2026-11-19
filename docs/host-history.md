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
never shown.

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

## Not covered, and why

- **The vault, AI Spaces, the plugins' Yours layer**: each is its own
  history (docs/backup.md).
- **Loam's checkout**: a git checkout of a release; `loam update` and git
  put it back.
- **Runtime data** (sessions, threads, jobs, logs, schedules' records):
  what Loam writes as it runs, not setup; large and changing every minute.
- **Plugins' private data** (`<state>/plugins/private/`): each plugin's
  own.
- **Authelia's accounts and passkeys**: a live SQLite database inside a
  Docker volume whose path depends on Docker's storage layout; a safe copy
  needs Authelia's cooperation. The next thing worth adding.
- **Caddy's certificates and data**: private keys it re-issues by itself.
- **Docker images, containers, volumes**: rebuilt from the covered compose
  files.
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

The installer installs and enables it (step 9) and takes the first state;
`loam update` installs it on an install made before it (the `host` step),
and `sudo loam host install` does it by hand. `check-drift` reports the
timer off or its units stale.

## Assumed of the machine

Linux with systemd: the system manager, and a user manager for Loam's user
(`systemctl --user -M <user>@`, systemd 248 or later). git, Python 3.11 and
`tar`. Caddy is validated and reloaded only when it is installed; Docker
Compose files are re-applied only when Docker is. Nothing assumes a cloud
provider, a snapshot API or one machine's paths: every path comes from
`/etc/loam/loam.toml`, the user's home and the standard places (`/etc`,
`/var/lib`, `/var/spool/cron`).

## Off the machine: not yet

The history stays on the machine on purpose. It holds key files, and the
Git backup plugin pushes in the clear to a repository on someone else's
host, so pushing this as it is would put the box's secrets there. What it
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
