# Installing Loam: one command, eleven steps, the claim

*First release, job L12 (2026-11-14). The design is §3 of the first-release
plan; the decisions are "one person, one server, one command" (decision 1)
and the names under the base domain. The code: `install.sh` (the bootstrap),
`bin/loam-install` (the steps), `lib/loam_install.py` (what is rendered),
`bin/loam-claim`, `bin/loam-await-dns`, `bin/loam-status`, and the `[edge]`
table in `lib/loam_deploy.py`. Tests: `tests/test_install.py`,
`tests/test_starter.py`.*

## The command

On a fresh Debian 12 (or Ubuntu 24.04) server with a public IPv4 address,
at least 4 GB of memory and 15 GB of free disk, as root:

```sh
curl -fsSL https://<where the release is>/install.sh | sudo bash -s -- --domain loam.example.com
```

or `--name sam` in place of `--domain` (below, "Where your Loam lives").
It asks for anything not given (where it lives, `--owner "Sam"`,
`--timezone Europe/Berlin`), from the terminal even though the script came
down a pipe.
It takes a few minutes (about two on a fresh Debian 12 or
Ubuntu 24.04 server in the rehearsals of 2026-11-16), and says how long it
took when it is done. Every line it prints is meant for someone
who has never used a terminal; what it runs goes to
`/var/log/loam-install.log` (root, 0600).

**What it checks** (docs/signing.md): the release it clones must be a tag
signed by one of the maintainer keys written into `install.sh`, checked
with `git` and `ssh-keygen` before anything from the clone runs. If the check
fails, the clone is removed and it stops (exit 3) with the reason. The script
itself is trusted as it was served. While `install.sh` carries no key, it
says the release is not checked.

**Run it again at any time.** Each step writes
`/var/lib/loam/install/<n>-<step>.done` when it has finished, and a run
starts at the first step without one. A step that stopped says what to do,
and that running the same command again carries on from there. When every
step has finished, a run changes nothing and prints the link or the address
again. Every step is also safe to repeat on its own.

**While the repository is private** the fetch needs an install token,
passed in the environment and written nowhere:

```sh
curl -fsSL -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.github.raw" \
  "https://api.github.com/repos/loamhq/loam/contents/install.sh?ref=<tag>" \
  | sudo LOAM_INSTALL_TOKEN="$TOKEN" bash -s -- --name sam
```

`install.sh` hands the token to git as an `http.extraHeader` in its own
environment (`GIT_CONFIG_COUNT`), so it is in no URL, no `.git/config` and
no file. How a token is made: a fine-grained personal access token on the
Loam HQ organization, repository `loamhq/loam` only, permission
`Contents: read`, expiring in 7 days, made by the owner on github.com; or an
installation token of the Loam HQ GitHub App (`lib/loam_github_app.py`
mints those; one lives an hour, which is enough, because a re-run uses the
release already fetched). A token is for one install and is never stored on
the server. Once the repository is public, none of this is needed.

## Where your Loam lives: your own domain, or a name we give you

*Loam 0.2.8 (2026-11-18). Before it, an own domain was a hidden `--base`
flag that made `<name>.<base>`; that flag is still there, for rehearsals
only.*

Given neither `--domain` nor `--name`, step 1 asks first:

```
Where will your Loam live on the web? Either:
    1. On a domain you have, for example loam.example.com (or example.com
    itself). You add two DNS records where you bought the domain; the
    installer says which, and checks them.
    2. On a name under <BASE_DOMAIN>, like sam.<BASE_DOMAIN>. Loam's makers
    add its records, so ask them for a name first, in an issue at
    https://github.com/loamhq/loam/issues/new
Type 1 or 2:
```

**A domain of the owner's own** (`--domain loam.example.com`;
`lib/loam_install.valid_domain`): any domain name, a host under one or the
apex itself, except names under `BASE_DOMAIN`, which are the other way.
The `[edge]` table gets `own_domain = true`, `domain` as given (lowercase,
no trailing dot) and `name` its first label. The person makes two records
at their DNS provider, both type A, not proxied:

```
loam.example.com     A  <the server's IPv4>
*.loam.example.com   A  <the server's IPv4>
```

`lib/loam_install.domain_check` asks for both (the wildcard as
`loam-check.<domain>`), their A and their AAAA records, and returns `here`
or `waiting` with one sentence per thing still wrong, saying what to do:

- a record that is missing ("has no A record yet: make one pointing to …");
- a record pointing elsewhere ("points to 192.0.2.9, not to this server:
  change its A record to …"). There is no "taken" on an own domain: the
  name is the person's, so another address is an old server or a parking
  page for them to change, and the install never stops for it;
- an AAAA (IPv6) record that is not one of the box's own addresses: Let's
  Encrypt asks over IPv6 first when a name has one, and its challenge would
  reach the other server;
- answers inside Cloudflare's published ranges: the records are proxied (the
  orange cloud); they must be "DNS only".

Step 2 prints what DNS says now and the two records, and carries on (the
records can be made during the install's few minutes). Step 10, with a
person at the terminal (not `--yes`), **waits** for them: it checks every
15 seconds for up to 15 minutes, says what is still missing whenever that
changes, and puts Loam on the web the moment both are right. Enter stops
the wait. Either way `loam-await-dns.timer` keeps checking every 30 seconds
as on the other path, writes the sentences into `/var/lib/loam/edge.json`
(`problems`), and `loam status` shows them under *Name*.

**Certificates**: Caddy's own default, one Let's Encrypt certificate per
host by the HTTP-01 / TLS-ALPN-01 challenge on ports 80 and 443, which the
installer already requires. The app, `auth.`, `vault.`, `files.`,
`loam-ui-content.` and every agent's site are each their own block in the
Caddyfile, and the wildcard A record makes each of them resolve, so a new
agent site gets its certificate the first time it is rendered. A wildcard
certificate would need DNS-01, which needs an API key for the person's DNS
provider and a Caddy built with that provider's module: a second thing to
set up and keep secret, for nothing the per-host route does not give. The
cost is one certificate per host against Let's Encrypt's limit of 50 new
certificates per registered domain per week, which one person's Loam does
not come near. A CAA record on the domain that names another authority
would refuse Let's Encrypt; the domain's DNS page is where to check.

**A name under `BASE_DOMAIN`** (`--name sam`) is exactly as before: the
makers make `sam.<base>` and `*.sam.<base>`, and "The name, and DNS later"
below is its account.

What else knows the domain does not care which way it came: the deploy
layer, the app (`APPS_DOMAIN`), `reach.md`, and `loam update` (which never
reads it).

## The eleven steps

| # | Step | What it does |
|---|---|---|
| — | bootstrap (`install.sh`) | root; git, curl, Python and certificates if missing; finds the newest release tag (`git ls-remote`), clones it to `/var/lib/loam/install/release`; runs that release's `bin/loam-install` |
| 1 | preflight | Debian 12 or Ubuntu 24.04; memory, disk; ports 80 and 443 free (Caddy itself excepted, for a re-run); the box's public IPv4 from its route; where it lives (`--domain`, `--name`, or asked) and the owner's name and the zone; writes `/etc/loam/loam.toml` (never rewritten after: it is the person's) |
| 2 | the name | a name: `lib/loam_install.name_check` (below). Taken: stop. Here: go on. Not yet: print the two records and go on. An own domain: `domain_check`; never stops; prints what is missing and the two records |
| 3 | packages | git, python3 and python3-yaml, sqlite3, ripgrep, ffmpeg, nftables, iptables, sudo, dnsutils and the rest, with `fonts-noto-core` and a Chromium headless shell for the app's page previews (Debian's `chromium-headless-shell` package; on Ubuntu, which has none, Playwright's at a pinned version, linked as `/usr/local/bin/chromium-headless-shell`); Caddy, Docker CE (the login and the agents' browser run in it; the app does not) and Node 24 from their own apt repositories |
| 4 | the user | `loam`, with lingering, `sudo` (NOPASSWD: the agents get the whole machine) and `docker`; the zone; `PYTHONPYCACHEPREFIX` for its services, shells and sudo, so Python never writes into the checkout; Docker's `daemon.json` before anything uses Docker |
| 5 | Loam | the release cloned to `paths.root` (`/home/loam/loam`), `origin` set to the source; the vault, workspaces, projects and plugins folders (plugins a git repo); uv; the MCP servers' Python; `/usr/local/bin/loam` |
| 6 | the vault | `bin/library seed-vault`; the vault and workspaces made git repos; **`<workspaces>/_shared/reach.md` written** (below) |
| 7 | providers | Claude Code, Codex and Antigravity at the builds this release pins (`lib/loam_pins.py`, `bin/provider-pin apply`): each installed beside under `~/.local/share/<cli>/versions/`, checked by its own `--version`, and linked from `~/.local/bin/`; Antigravity by the address and SHA-512 the pin recorded, since its release server serves only its latest. Then `provider-memory pin` and `check` |
| 8 | Iris | `bin/library seed-starter`: Iris and the hello thread (docs/starter.md); then `bin/provision --engineer`, the engineer that changes Loam itself (docs/update.md, "A change to Loam's own parts") |
| 9 | units | the broker's bridge (`loam-broker`) and its wall; the system units, `loam-user-manager.service` enabled (it starts Loam's user manager at boot where lingering alone does not); the user units from the templates; the broker and the plugin gateway started; every timer armed without firing (`loam_units.arm_timer`); waits for the broker to answer; the host history's timer installed and its first state taken (`loam host`, docs/host-history.md) |
| 10 | the edge | `gen-deploy --init-secrets` (the login's secrets, no user), `gen-deploy` (with it the inbound wall: default-deny, SSH, 80 and 443 open, or standing aside for a firewall the server already has; one line says which), the app (`bin/deploy loam-ui up <commit>`: the commit's `app/` unpacked to `/var/lib/loam-releases/loam-ui/` and run as `loam-app@loam-ui.service`, no container), `loam-claim.service`; then one `loam-await-dns` run: if the name resolves here the hosts go up now, otherwise the timer is left polling and the records are printed; on an own domain with someone at the terminal it first waits up to 15 minutes for them. Exits 0 either way |
| 11 | the claim | mints the one-time token (`/etc/loam/claim`, root 0600) and prints the link. On an already claimed install, the closing instructions point to sign-in |

## What an install's configuration says: `[edge]`

`render_config` writes the keys docs/config.md lists, spelled out, and one
table that makes the box one person's name. Without the table (the first
install) the deploy layer renders exactly what it rendered before it existed.

```toml
[edge]
name = "sam"
domain = "sam.example.com"           # every host is under this
address = "203.0.113.7"               # the box's public IPv4: what DNS must say
app = "loam-ui"                       # this record is served at the domain itself
app_checkout = "@ROOT@"               # built from the release's own checkout
app_context = "app"                   #   its app/ folder is the build context
apps = ["loam-ui", "files", "vault"]  # the records this install serves, no others
resolver = "public"                   # DNS over HTTPS to 1.1.1.1 / 8.8.8.8 ("system": /etc/hosts too)
tls = "acme"                          # "internal": Caddy's own authority (rehearsals)
dns_gate = true                       # no host block until the name resolves here
own_domain = true                     # only on an own domain (--domain); absent for a name
```

The hosts: the app at `sam.example.com`, the portal at `auth.`, the
vault at `vault.`, the commons' web folder at `files.`, the app's content
host at `loam-ui-content.`, and every agent's site at `<site>.` — all
covered by the two records below. Authelia's one-factor rule names the
domain and `*.<domain>` together, because the wildcard does not cover the
name itself. The app's compose file is handed the install's own origins by
`bin/deploy` (`origins_env`: `LOAM_VAULT_ORIGIN`, `LOAM_FILES_ORIGIN`,
`LOAM_CONTENT_ORIGIN_<APP>`), which on the first install are the literals
they replaced.

## The name, and DNS later (a second user's case)

Two A records, not proxied, per person: `<name>.<base>` and
`*.<name>.<base>`, both to the box. The check asks for `<name>` and
for `loam-check.<name>` (which the wildcard answers), and for a made-up
name under the base (`loam-nobody-<random>`):

- both answer with this box's address: **here**;
- `<name>` answers with another address, and not the one the made-up name
  gets: **taken**, and the install stops;
- nothing answers, or `<name>` answers just as the made-up name does:
  **unset**. Today `*.<base>` answers every name with the first install
  (Pushback 2), so every unclaimed name reads as unset, never as taken. The
  "taken" refusal means something only once that wildcard is replaced by
  explicit records; until then two people could each be told their name is
  free. Nothing here changes DNS.

Steps 1 to 9 need no DNS. At step 10, when the name does not resolve here,
the Caddyfile carries **no host block at all** (`edge_ready()` is false), so
Caddy asks Let's Encrypt for nothing: a block for a name that does not
resolve fails its challenge on every load, and failed validations are
limited per host per hour, against a registered domain every install
shares. `loam-await-dns.timer` (root, every 30 seconds) runs
`bin/loam-await-dns`, which writes what it found to `/var/lib/loam/edge.json`
for `loam status`. When both names resolve here it records `"dns":
"resolved"` (kept from then on, so a DNS blip never takes the hosts away),
runs `gen-deploy`, waits up to three minutes for the certificate (a TLS
handshake on loopback with the name as SNI, verified), records it, and
turns its own timer off. `loam status` then shows the claim link as ready.

## The claim

While `/etc/loam/claimed` does not exist, the app's host is one block:

```
<domain> {
	handle /claim { reverse_proxy 127.0.0.1:8934 }
	handle { redir * /claim 302 }
}
```

outside the login, served by `bin/loam-claim` (`loam-claim.service`, root,
loopback). The other hosts are rendered as ever, behind the gate, and
Authelia is not started: it has no user (`gen-deploy` says so and skips it).

- The link is `https://<domain>/claim#<token>`. The token rides in the
  fragment, which a browser never sends, so no access log line or proxy
  carries it; the page reads it and posts it.
- `POST /claim {token, username, password}`: a wrong token is a 403 after a
  second's pause, and twenty wrong ones in ten minutes are refused for the
  rest of the window (the token is 192 bits; this is manners). The
  username is 2 to 32 lowercase letters, digits, `-` or `_`; the password at
  least 10 characters. The password is hashed by Authelia's own CLI with
  argon2id, handed in on stdin. Then the users file is written into the
  `authelia-data` volume, `/etc/loam/claimed` is written and the token
  deleted, and the answer names the portal with the app as its return
  address. A moment later the claim service runs `gen-deploy`, which renders
  the app's host behind the login and starts Authelia.
- After that, `GET /claim` is a 410 ("This Loam already has its owner"),
  and no route reaches it: the Caddyfile has none.
- Without the token, whoever reached the name first could own the box.
  With it, the person who ran the install (the owner, for a friend) hands
  the link over.

### The page contract for the claim (U4)

The claim page is the one page outside the login. `bin/loam-claim` serves
the app's own **`public/claim.html`** at `GET /claim` when the app's build
folder has one (`<checkout>/<app_context>/public/claim.html`), and its own
plain page otherwise. Nothing else of the app is served outside the login:

- the page is **one self-contained file**: inline CSS and JS, no images,
  fonts or scripts from anywhere (its CSP is `default-src 'none';
  style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src
  'self'; form-action 'self'; frame-ancestors 'none'`);
- it reads the token from `location.hash` (and should take it out of the
  address bar, `history.replaceState`);
- it posts `application/json` to `/claim`: `{"token", "username",
  "password"}`;
- **200** `{"ok": true, "username", "next", "wait_seconds"}`: say it worked,
  wait `wait_seconds` (the login is starting), then go to `next`, the
  portal, which returns to the app after sign-in;
- **400** / **403** / **429** / **500** `{"error": "<a sentence for the
  person>"}`: show the sentence; 403 is a wrong or incomplete link;
- **410** `{"error"}`: already claimed; offer a link to `https://<domain>/`.

### The relay: a passkey without a shell (option a)

Adding a passkey in Authelia needs an elevated session, and on an install
with password-only sign-in that means a one-time code Authelia "emails".
There is no mail: its file notifier writes the message to
`/data/notification.txt` in its volume, which only root can read. So the
code is relayed to the signed-in owner through the app's own host:

```
<domain> {
	handle /_loam/login-code { import gate; reverse_proxy 127.0.0.1:8934 }
	@sight remote_ip 172.30.0.0/24          # the agents' browser passes the gate…
	…
}
```

The relay's handle is **ahead of the browser's subnet**, and Caddy matches
it first (`tests/test_install.py` asks `caddy adapt`): the agents' browser
passes the login on every other path, so without that order a persona
could read the owner's codes. Through the gate, the request carries
`Remote-User` from Authelia (Caddy's `copy_headers` replaces any the client
sent); the claim service answers only when that user is the users file's
admin, which on an install is the one person who claimed it.

### The page contract for the relay (U4)

- `GET /_loam/login-code`, same origin, with the session cookie (any
  `fetch` from the app's page has it).
- **200** `{"code": "K7QP2MXA", "subject": "...", "at": "<ISO time>",
  "age_seconds": 12}` when the newest message the login wrote holds a code
  and is under 15 minutes old; **200** `{"code": null}` otherwise.
- **403** `{"error"}` for anyone but the owner. Without a session the gate
  answers first (a 302 to the portal).
- The flow to draw: Settings › Account › *Add a passkey* opens Authelia's
  own settings at `https://auth.<domain>/settings` (Two-Factor
  Authentication › WebAuthn Credentials › Add, which asks for the one-time
  code; the API underneath is `POST /api/user/session/elevation`, which
  sends it, and `PUT` with `{"otc"}`, which takes it);
  beside it, the app polls this route every few seconds while the person
  is there and shows the code large, with "Loam's sign-in sent you this
  code" and its age. A code is shown to the person only, never logged by
  the app and never sent to an agent.

### The page contract for the passkeys (2026-11-17)

- `GET /_loam/passkeys`, same origin, with the session cookie, on the app's
  host or its staging copy's.
- **200** `{"passkeys": [{"id": 3, "description": "Phone", "created_at":
  "<ISO>", "last_used_at": "<ISO or null>"}]}`: the signed-in person's own,
  as the login lists them, and nothing of the key itself; `[]` for none.
- **401** `{"error"}` when the request carries no signed-in session the gate
  passed; **403** when the login refuses the session; **502** when the login
  does not answer. Without a session the gate answers first (a 302).
- `DELETE /_loam/passkeys/<id>`: **200** `{"ok": true}`, or the login's own
  refusal passed on (a **403** when it wants an elevated session first).
- The app (`app/public/passkey.js`) lists them under Settings › Account and
  falls back to a link to the portal's own list on any answer but a 200.

## `loam status`

`loam status` (root; it re-runs itself under sudo) says, part by part, in
plain words with what to do when a part is not right: the install's steps;
the name (with the two records while it does not point here, or "someone
else's" when taken); the certificate; the agents' engine (the broker, its
unit and its `/up`); the plugins (the gateway); the app (its unit,
`loam-app@<app>.service`, and its `/up`); the sign-in (Authelia; "starts when you claim" before then); the
agents' browser; the sign-in helper (`loam-claim`); the timers this release
ships. While the install is unclaimed it ends with the claim link, saying
whether it works yet. `loam status --json` is the same as one object
(`parts[]` of `{key, label, state: ok|problem|waiting, say, fix?}`,
`claim_link`, `claim_ready`, `ok`). Exit 0 when everything is ok.

## reach.md on a blank install

Every agent's `CLAUDE.md` imports `<workspaces>/_shared/reach.md`; a blank
install had none, so the import was dropped silently (L11's surprise). Step
6 writes a short one when it is absent: the pointer to the library page
`loam-what-you-can-reach` (in `<vault>/System/Loam/skills/`) and this
install's own names (the domain and its hosts, the workspaces, the vault,
the thread records and boards, `bin/site`, `bin/thread`, `bin/settings`,
`loam status`), rendered from `loam.toml` by `loam_install.reach_text`. It is
the person's and the agents' file from then on: step 6 never rewrites one
that exists, and an update never touches it.

## A failed starter, finished by the next run

`seed-starter` (step 8) writes its marker as `"state": "making"` before it
makes Iris. If her provisioning fails, the workspace is left with
`agent.json.failed` (L10's rule: a half-made agent leaves the roster); the
next run sets that workspace aside, whole, under
`<state>/agents/failed-starts/iris-<time>/` and makes her again. If she was
made and the hello thread was not, the next run writes the thread (finding,
not duplicating, one it had begun). A workspace of the starter's id with
neither marker nor failure is somebody's and is left alone, as before.

## What an install leaves to others

- **Provider sign-in** (Claude, ChatGPT or Google) is the person's, in the
  app: U4. The installer signs in to nothing.
- **The app's own domain welds** are U1's: `public/installation.js` still
  says `appsDomain: '<base>'`, and the app's CSP names hosts of its
  own; the compose file now hands it the install's origins.
- **Updates** are L13's `loam update` (`loam update` once it lands: `bin/loam`
  runs `bin/loam-<verb>`). An install leaves `git status --porcelain
  --ignored` in the checkout empty, which L13's clean-tree rule needs.
- **The base domain's DNS**: two A records per person, each one the owner's
  yes (it makes a name reachable on the open web).

## How it was proved (2026-11-14)

On throwaway machines from the owner's sandbox plugin (Modal), a fresh
`debian:12` and a fresh `ubuntu:24.04`, each with only what a cloud image
already has (systemd, dbus, python3, curl, certificates; no git, no sudo),
with the name `sam.loam-proof.test`, `--resolver system` (the names live in
`/etc/hosts`), `--local-certs` (Caddy's own authority) and `--address` (the
machine sits behind NAT). The release was this repository at the job's head
with loam-ui's `main` as `app/` and a `manifest.json`, laid out as the cut
will be, in a git bundle tagged `0.1.0`: `install.sh --source <bundle>`.

- **A Modal sandbox as a server**: `modal.Sandbox.create("sleep", "infinity",
  image=..., runtime="vm")` is a KVM guest; boot systemd in a PID namespace
  (`env container=other setsid unshare --pid --fork --mount-proc
  /lib/systemd/systemd --system &`) and run everything through `nsenter -t
  <its pid> -p -m`. Remove the image's `/usr/sbin/policy-rc.d`.
- **Debian 12, one run of three**: npm pointed at 127.0.0.1 stopped step 7
  ("Codex 0.169.0 could not be installed"); with npm back and a junk
  `~/.claude.json`, the next run passed 7 and stopped at 8 with Iris's
  `agent.json.failed`; with the file gone, the third run set the failed start
  aside, made Iris, and finished on the DNS-later path (no host block, the
  timer polling, the link printed). The two names were then added to
  `/etc/hosts`: the timer saw them 13 seconds later, rendered five hosts and
  had a verified certificate the same second, and turned itself off.
- **The claim**: `/` 302 to `/claim`; a wrong token 403, a short password
  400; the claim 200; then the users file (one admin), `/etc/loam/claimed`,
  the token gone, the app's host behind the gate with the relay ahead of
  the browser's subnet, Authelia healthy, `/claim` 302 to the portal, sign-in
  with the claimed password OK and a wrong one refused, the app 200 with
  Iris in its agents.
- **The relay**: Authelia asked for an elevated session wrote its code to the
  notifier; the owner read the same code through `/_loam/login-code`; no
  session and a forged `Remote-User` got the portal; from the agents'
  browser's subnet the app answered 200 (its bypass) and the relay 302; the
  relayed code elevated the session and passkey registration opened.
- **The rest**: a re-run did nothing and exited 0; Claude Code 2.1.325,
  Codex 0.169.0, Antigravity 1.5.9, memory pinned and every store empty;
  the hello thread with Iris's preset `m1`; `git status --porcelain
  --ignored` in the checkout 0 lines; L13's `loam update check` read the
  install as stock 0.1.0, the newest; `loam status` all ok.
- **Ubuntu 24.04**: the same install in one run, and the same proof.

## How an own domain was proved (2026-11-18, 0.2.8)

On a throwaway Debian 12 VM (Modal `runtime="vm"`, systemd in a PID
namespace, as above), with nothing reachable from the internet: the domain
was `loam.example.test`, public DNS was stood in for by **dnsmasq** on the
VM's loopback (`/etc/resolv.conf` pointed at it, so `--resolver system`
asks it), and the certificates were Caddy's own authority
(`--local-certs`). The release was this branch tagged `0.2.8` in a bare
repository served by `git daemon` on 127.0.0.1.

- **The question**: run with neither `--domain` nor `--name`, on a pty, step
  1 asked "Type 1 or 2", then the domain, and wrote `own_domain = true`.
  (Before this, the installer could ask nothing at all; see the release
  notes.)
- **No records yet**: step 2 said both were missing and printed the two
  records; steps 3 to 9 went on.
- **Making them while it waits**: at step 10 the installer waited. A script
  standing in for the owner added the apex record 25 seconds in (the wait
  then said only `*.loam.example.test` was missing) and the wildcard 40
  seconds later (dnsmasq `address=/loam.example.test/…`, a true wildcard);
  within 15 seconds it said both were right, rendered the hosts, had the
  certificate and printed the claim link. About 3 minutes in all.
- **The claim, sign-in and passkeys**: `/` 302 to `/claim`; the claim 200;
  Loam's sign-in page with the claimed password; the app (its welcome
  screen); `vault.` and `files.` listings; an agent's site made with
  `bin/site up walk --by iris` answered at `walk.loam.example.test` behind
  the login. A passkey was added in the sign-in settings with a CDP virtual
  authenticator, the one-time code read through `/_loam/login-code`, and a
  fresh session signed in with the passkey alone.
- **`loam status`** with a stray AAAA on the apex and the wildcard pointing
  elsewhere showed both sentences under *Name*; restored, all ok.
- **`loam update`**: a walk-only `0.2.9` tag in the VM's repository was
  seen, applied, and the hosts answered after.
- **The name path**, on a second VM: `--name sam --base loam-proof.test`
  with the owner and the time zone asked at the terminal, as the README's
  command does; installed as before, no `own_domain` key.

What this does not prove: Let's Encrypt itself on a real domain. The
certificate route is the one every install on a name already uses (one
certificate per host, HTTP-01 / TLS-ALPN-01), and nothing in it depends on
which domain it is.
