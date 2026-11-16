# The deployment layer — what the box exposes, and how

*Built 2026-09-08 (Move 10 of the phase-5 closing plan). This directory and
one operator-owned file outside every repo are the whole source of what the
internet can reach on this box. Everything live is rendered from here by
`bin/gen-deploy`, and `bin/check-drift` diffs the render against the live
files and against the running box.*

## The shape

```
deploy/loam.toml            the domain, in exactly one place
deploy/<app>/deploy.toml    the app's record: exposure, host, policy, share, checkout
deploy/<app>/compose.yaml   how the app runs (image, loopback publish, limits, volumes)
                            -- or `[service]` in the record, for Loam's own app:
                            a systemd unit, no container ("Loam's own app")
/etc/loam/public            OPERATOR-authored, root-owned, outside every repo:
                            the apps that are public. The owner writes it; nothing
                            here does. It need not exist: absent means no app is
                            public, which is the state of the box at the Phase 5
                            close (2026-09-09).
```

Records on the box: `files/` and `vault/`, both static hosts, both
private, and since 2026-09-10 `owner-site/`, the first app container
since probe-clock (retired 2026-09-08): the owner's site on
`owner-site.<domain>` and, by the record's `[domains]` table, on
`owner-site.example` and `www.owner-site.example` with its portal at
`auth.owner-site.example`, private on every name; see "Hosts on an app's
own domain". Its compose file is the shape to copy for a static site, with
one wrinkle its comments explain: the official `caddy` image's binary
carries a file capability, so `cap_drop` lists every default but
`NET_BIND_SERVICE` instead of `[ALL]`.

Since 2026-09-16, `loam-ui/` is the fourth: the slot for Loam's own web app
(since 2026-11-16 not a container at all; see "Loam's own app" below, and
read the rest of this paragraph as the history it is),
private on `loam-ui.<domain>` with no `[domains]` table, because the
wildcard A record already answers for it. What runs behind it today is a
placeholder — one static page and a `GET /up`, from the project's own repo —
deployed only so the slot was provable before there was an app. Its compose
file is the shape to copy for an app with state: a **named** volume at
`/storage`, which is the app-deployment convention, with the trap its
comments explain. A fresh named volume is created `root:root` 0755 and an
unprivileged container cannot write it (caused here 2026-09-16: `touch
/storage/probe` as uid 65534 is `Permission denied`); the fix belongs in the
image, which must `mkdir` and `chown` the directory at build time so Docker
seeds the volume's ownership from it. Fixing the live volume by hand would
be state no generator knows about.

Since 2026-11-12, `loam-ui-next/` is the fifth: loam-ui's staging copy on
`next.<domain>`; see "Staging: a second copy of loam-ui" below.

**Three apps, three loopback ports.** `owner-site` publishes 8080,
`loam-ui` listens on 8081 and `loam-ui-next` on 8082. `_loopback_port` checks the address and the count but not
collisions across records, so `ss -ltn` before writing a compose file is the
check until something does it properly.

Rendered by `sudo bin/gen-deploy` into, and diffed by `bin/check-drift` against:

| Live file | Rendered by |
|---|---|
| `/etc/caddy/Caddyfile` | `loam_deploy.caddyfile()` |
| `/etc/docker/daemon.json` | `loam_deploy.daemon_json()` |
| `/etc/loam/authelia/configuration.yml` | `loam_deploy.authelia_config()` |
| `/etc/loam/authelia/compose.yaml` | `loam_deploy.authelia_compose()` |
| `/etc/loam/browser/compose.yaml` | `loam_deploy.browser_compose()` |
| `/etc/loam/browser/firewall.sh` | `loam_deploy.sight_firewall_script()`; check-drift also asks the kernel for every rule |
| `/etc/systemd/system/loam-sight-firewall.service` | `loam_deploy.sight_firewall_unit()` |
| `/etc/sudoers.d/loam-deploy` | `loam_deploy.sudoers()` |
| `/etc/sysctl.d/60-loam-preview.conf` | `loam_deploy.preview_sysctl()`; check-drift also reads the kernel's value |

Three things are never rendered: Authelia's secrets (`/etc/loam/authelia/secrets.env`,
root-only, written once by `gen-deploy --init-login`), the users file (in the
`authelia-data` volume), and a share token (`/etc/loam/share/<app>.token`,
root-only, minted by `gen-deploy --share`). The generator reads them; nothing
prints them.

**Logs** (2026-09-28; the whole map is `docs/logs.md`). Every host block
imports `access_log <host>`, so each host writes JSON access lines to
`/var/log/caddy/access/<host>.log`, rolled by Caddy (50 MiB, 14 files, 14
days) with the query string, credentials, Referer and response headers
filtered out. `gen-deploy` creates that directory as `caddy:caddy` and runs
`caddy validate` as the `caddy` user, because validating opens every log
writer and a root-run validate leaves files the caddy process cannot open.
Every app service's compose file carries `logging: {driver: journald,
options: {tag: "{{.Name}}"}}`, exactly `loam_deploy.APP_LOGGING`, and the
generator refuses any other: a json-file log is deleted with its container,
and `bin/deploy <app> up` makes a new container on every commit.
`check-drift` names a host block without its log and a running container
whose log config is not that one.

The proxy is **Caddy**, a Debian package running as a system unit under its
own user, on 80 and 443, with Let's Encrypt certificates per host over
HTTP-01. It is the only process on the box listening on the internet besides
sshd and what a plugin that is on declares (`box.listener`; on the first install,
the owner's file sync's transfer and discovery ports); check-drift says so if that
changes.
Every app publishes on `127.0.0.1` only, and the daemon's default publish
address is loopback too, so a compose file that forgets the address still
does not leave the box. The generator refuses any other address.

The login is **Authelia** in a container on loopback, one user, file
backend with an argon2id hash, passkey login on, password plus TOTP as the
fallback, a month-long remember-me. Caddy's `forward_auth` sends every
request on a private host to it first.

**Sessions live in Redis, not in Authelia** (since 2026-11-09). Authelia
kept them in its own memory, so every reboot, and a resize is two, signed
the owner out on every device. `loam-authelia-redis` is the login project's
second container: on the project's default network only, no published
port, no password because only Authelia can reach it, running as the
image's `redis` user (999:1000, the owner of `/data`), and snapshotting to
the `authelia-sessions` volume every minute there was a change and on the
SIGTERM a shutdown sends. That volume is not external: losing it costs one
sign-in per device and nothing else.

**Every host compresses** (`import compress`, zstd then gzip), on a match
list that is Caddy's default minus `text/event-stream`, so an SSE stream is
never held back in an encoder's buffer (`COMPRESS_TYPES` in
`lib/loam_deploy.py`).

**Every app host is `one_factor`, and one host that does not exist is
`two_factor`.** Authelia 4.39 shows the enrolment panels (Settings → Two-
Factor Authentication: WebAuthn Credentials with its Add button, One-Time
Password) only when some rule or the default policy says `two_factor`;
otherwise the page says "There are no protected applications that require
a second factor method" and there is nowhere to add a passkey (found
2026-09-08, in `handler_configuration.go`). So the generator emits a first
rule for `second-factor.<domain>` at `two_factor` — no Caddy block, no
certificate, a request to it fails the TLS handshake — and nothing else
changes. Apps stay at one factor because a passkey login is a single factor
in Authelia's counting: on a `two_factor` host it asks for the password
after the passkey unless `experimental_enable_passkey_uv_two_factors` is
set, which the docs mark unsupported and say a future version will refuse
at startup. One tap per device per month needs `one_factor`. The name is
reserved; a record may not take it.

## deploy.toml, every key

```toml
exposure = "private"        # or "shared". There is no third value.
# host = "<app>"            # <host>.<domain>; default is the directory name
# policy = "one_factor"     # Authelia's policy; "two_factor" is the other, and
#                           # it means passkey THEN password on a new device
#                           # (a passkey login counts as one factor)
# checkout = "/abs/path"    # where bin/deploy builds from; default is the
#                           # project engineer's checkout from agents/projects/<app>.json.
#                           # @ROOT@ is Loam's own checkout
# context = "app"           # the folder of the checkout the app is (Loam's app)
# static_root = "/abs/dir"  # a directory Caddy serves instead of a container
# hide = [".git"]           # static_root only: entries the file server pretends
#                           # are absent. A bare name matches at any depth; a
#                           # name with a slash is a path under the root.
[share]
expires = 2026-10-08        # required when shared: the link dies at the end of
                            # this date, UTC, whether or not anyone regenerates
[domains."owner-site.example"]                          # hostnames on a domain of the
hosts = ["owner-site.example", "www.owner-site.example"] # app's own; see below
[[bypass]]                  # one exact request that passes the login; the app
method = "POST"             # checks its own credential there. Repeatable.
path = "/api/dictate"       # See "One request past the login".
# content_host = "<label>"  # a second origin on the same container, serving
#                           # /content/* only; see "A content host"
# app_shell = true          # this record serves Loam's own page: its origin may
#                           # frame the agents' static sites, and the Apps list
#                           # leaves it out; see "Framing a site in the app"
[service]                   # Loam's own app, run by Loam as loam-app@<app>.service
port = 8081                 # instead of a compose.yaml; see "Loam's own app"
```

## Loam's own app

*Since 2026-11-16. Until then the app was a project of its own (the
`loam-ui` repository, with its own engineer) deployed as a container like
any other, with a compose file here; its code is now Loam's `app/` folder,
and Loam runs it.*

The app is Node's standard library with no dependency and no build step, so
the container gave it a wall and little else, and the wall had a cost: held
to 128 MB and 64 pids, every page preview it took timed out (2026-11-15). A
record with a `[service]` table is a third kind of app beside a container
and a static host, and runs the way the broker does:

```
/etc/systemd/system/loam-app@.service      the unit, one template (gen-deploy,
                                           lib/loam_deploy.service_unit)
/var/lib/loam-releases/<app>/releases/<sha>/    the app's folder at a commit, root's
/var/lib/loam-releases/<app>/current            -> the release that runs
/var/lib/loam-releases/<app>/previous           -> the one before it
/var/lib/loam-app/<app>/                   its state: speech audio, page previews
/run/loam/app/<app>.env                    its environment, root-only, in memory
```

```sh
sudo bin/deploy loam-ui up <commit>     # unpack <commit>:app/, point current at it,
                                        # restart, wait for GET /up; not healthy,
                                        # the release before goes back
sudo bin/deploy loam-ui up <earlier>    # a rollback is the same command
sudo bin/deploy loam-ui status          # the unit, the commit, its health
sudo bin/deploy loam-ui logs 200        # journalctl -u loam-app@loam-ui
```

- **A named commit, never the working tree.** `up` unpacks
  `git archive <commit>:<context>` and nothing else of the repository, as
  the container was built from `git archive <commit>`; a commit with no
  `src/server.js` there is refused before anything moves. The commit must be
  on the checkout's `main`, or, for Loam's own checkout, in what it has
  checked out (a stock install sits on its release tag). The two newest
  releases stay on disk and the rest are pruned.
- **The same result line.** `up` prints the same `deploy-result:` JSON a
  container's does (`deployed`, `carried`, `rolled_back`, `unhealthy`,
  `build_failed`), so a job's `deploy: true` and `--forward-only` work
  unchanged (lib/loam_autodeploy.py).
- **The wall is systemd's.** A dynamic uid of its own (no login, nothing on
  the box owned by it), the whole file system read-only but its state
  directory, `/home` not there at all, its own `/tmp` and `/dev`, no
  capabilities, no new privileges, and a loopback listener (`APP_HOST`):
  Caddy is the only thing that reaches it. It reaches the broker and the
  previews' internal route at the broker's address, which the host's own
  processes reach on `lo` (broker/firewall.sh).
- **Its environment is bin/deploy's `service_env`,** value for value what
  the compose file handed the container: the broker's token (`broker_env`)
  and the vendor key on the allowlist (`app_secrets`) read from the box as
  the unit starts, by `bin/deploy <app> env` in `ExecStartPre` as root, into
  `/run/loam/app/<app>.env` (0600, memory). Never in the repository or on
  disk.
- **No memory ceiling,** like every agent run since 2026-11-16, and the
  core's OOM score beside the broker. Its page previews' Chromium (Debian's
  `chromium-headless-shell`, installed on the host) starts at the agents'
  score through `choom`, so a page that balloons is what the kernel ends.
- **Logs** are the journal under the app's name (`SyslogIdentifier`), so
  `journalctl -t loam-ui` and `bin/logs app loam-ui` read it as they read
  the container's lines.
- **Moving from the container.** The first `up` of a `[service]` record
  stops the container of the same name (and sets it not to restart), so the
  port is free, and starts the container again if the unit does not answer.
  The container and its image are left, stopped; `check-drift` reports a
  container of that name only if it runs again.

Docker is still on the box, for the login, the agents' shared browser and
an agent's own project; only the app left it.

## One request past the login

*Added 2026-09-28 for loam-ui, when an Obsidian plugin on a phone needed to
post dictation to it: a plugin inside Obsidian cannot perform a passkey
ceremony.*

Each `[[bypass]]` entry renders one more `handle` in each of the app's host
blocks, after the browser's and any share's and before the gate:

```
@bypass0 expression `{http.request.method} == "POST" && {http.request.orig_uri} == "/api/dictate"`
handle @bypass0 {
	reverse_proxy 127.0.0.1:8081
}
```

`orig_uri` is the request URI as the client sent it, query included, and
the comparison is whole-string and case-sensitive. So `POST /api/dictate?x`,
`POST /API/dictate`, `POST /api/dictate/`, `POST /api/x/../dictate` and a
`GET` on the path all meet the gate like every other request on the host.
The path in the record is refused unless it is literal segments of letters,
digits, `_` and `-`. The request is proxied unchanged, so the app sees
exactly the path the matcher saw; an app with a bypass should still refuse a
`.` or `..` segment itself rather than normalise it.

What it is not: an exposure. The host stays private, `check-drift` still
reads the block as gated (it imports the gate), and the operator's public
file is untouched. The login is simply not asked about this one request;
the app's own token is the lock on it, which is why the app must refuse
the route outright when its token is unset. Authelia's configuration does
not change and is not recreated, so nobody's session ends.

Proved 2026-09-28 from outside the browser's subnet, against the public
address: `POST /api/dictate` reached the app, `GET /` and `GET
/api/transcribe` still answered 302 to the portal.

No record carries a bypass since 2026-10-01: loam-ui's, the only one, left
with the route it opened when the Obsidian plugin moved to its own key. The
feature and its tests stay; the examples above are the shape it had.

## Hosts on an app's own domain

*Added 2026-09-10 for the owner's personal site: they want to see it on their
real domain, with a real certificate, behind the login, before it is
public.*

A record may carry `[domains."<domain>"] hosts = [...]`, one table per
domain. Every listed host renders as **one more host block on the same
upstream with the same body** as `<host>.<domain>`: private is private on
every name, a share's matchers are on every name, and the operator's
public file ungates every name of the app at once. The hosts must be the
domain itself or names under it, unique across every record; the domain may
not be the runtime's own (use `host =` for that); and `<auth_host>.<domain>`
is reserved, because it is the portal:

- **One login portal per extra domain.** Authelia's session cookie is
  scoped to one registrable domain, so a gated host on `owner-site.example`
  cannot see a `<domain>` session. The generator adds a cookie entry
  for each extra domain (`session.cookies`, Authelia 4.38+), with its
  portal at `auth.<domain>` as one more Caddy block proxying to Authelia,
  and one access-control rule naming the record's hosts at its policy.
  Proved 2026-09-10 on a throwaway record (`loam-proof.test`) through a
  throwaway Caddy on loopback with local certificates and the live
  Authelia: `https://loam-proof.test/` and `https://www.loam-proof.test/`
  both answered 302 to
  `https://auth.loam-proof.test/?rd=https%3A%2F%2Floam-proof.test%2F&rm=GET`,
  the same shape as `vault.<domain>` → `auth.<domain>`, no body;
  the portal on the extra domain answered `/api/state` 200.
- **What it costs the owner.** Authelia keeps a session per cookie
  domain: the same user and users file, but a login on one domain is not a
  login on the other, so the first visit to the new domain on each device
  is one more login (and with `remember_me: 1M`, one a month after that).
  A passkey is bound to the domain it was enrolled on, so on the new
  domain they log in with password + TOTP once and enrols a passkey there,
  or keeps using the password. One-factor hosts need no TOTP.
- **DNS the domain needs, all A records to this box, proxy off:** each
  listed host (`@` and `www` for the site) **and `auth`** for the portal.
  Or a wildcard `*` as the runtime's own domain has. Until a name resolves here,
  Caddy asks Let's Encrypt for its certificate on every load and fails the
  challenge, retrying with backoff and logging `tls.obtain … will retry`;
  nothing else breaks. `gen-deploy` prints a warning per unresolved name
  and does not refuse, because the record is the decision and DNS landing
  later needs no regenerate. The recommendation is to write the record
  only once `dig +short <host>` answers this box's address, so the first
  load issues cleanly. On-demand TLS was rejected: it needs an `ask`
  endpoint to be safe against anyone pointing a name here, which is more
  machinery than the problem.
- **`www`** is served, gated, like the apex; a redirect to the apex is
  not rendered. When the site goes public and a canonical host matters,
  that is a small generator addition (a `redirect` key and a block the
  drift check reads as a redirect, not as an ungated host).
- `gen-deploy` now runs `authelia config validate` on the rendered
  configuration before writing it (Authelia does not reload its file, so a
  bad render was a force-recreate into a crash loop), and `check-drift`
  maps every hostname of every app, so an extra host with no gate is a
  finding like any other.

## A content host

*Added 2026-10-23 for loam-ui's live HTML previews (loam-ui `2132b85`; the
app's side is its repo's `docs/files-content-origin.md`).*

loam-ui runs an HTML file, scripts and all, in a sandboxed iframe with
`allow-same-origin`, and that is safe only on an origin that is not the
app's. `content_host = "loam-ui-content"` in its record renders one more
block, `loam-ui-content.<domain>`, on the same container:

- **Only `/content/*` reaches the app.** Every other path is `404` from
  Caddy, ahead of the login, so the host never serves the app's shell, API
  or assets even if the app's own Host dispatch were wrong.
- **The Host goes up as sent**, because the app dispatches on it; Caddy
  overwrites any client `X-Forwarded-Host` with its own (no
  `trusted_proxies` is set).
- **No cookie either way.** `header_up -Cookie` and `-Authorization` on the
  proxy; a deferred `-Set-Cookie` on every response.
- **Behind the login, always,** whatever the app's exposure: no share
  matcher and no public form. The browser's subnet passes as on every
  private host. Authelia's `*.<domain>` rule already covers the name and
  the wildcard A record already answers for it, so neither changes.
- **The grant is logged as `redacted`.** The segment after a leading
  `/content/` is a signed read grant for an hour; the shared access-log
  snippet replaces it on every host, the query as before.

**The login's cookie does reach this host,** and has to: `loam_auth` is
Authelia's cookie on the parent domain (`Domain=<domain>`, Secure,
HttpOnly, SameSite=Lax), and the gate reads it. It stops at Caddy and
Authelia. A page on the content host cannot read it (HttpOnly) and the app
never receives it there. What a page there *can* do with it is what any
page on any `*.<domain>` host can already do: cause a same-site
request that the browser sends it with. Against loam-ui that is refused
by the app (from `2132b85`: an `/api/*` request whose `Sec-Fetch-Site` is
not `same-origin`/`none`, or whose `Origin` is not the app's own, is a
`403` before anything is relayed); against the vault and files hosts it can
load a resource but not read the answer, since `connect-src` keeps fetch on
the content origin. The vault and files hosts already serve unsandboxed
HTML on same-site origins, so the content host adds no new kind of power
over the cookie; it adds a sandboxed one.

**Why not `vault.<domain>`.** It was the owner's first preference,
because it is the authenticated viewer already in use. It fails the app's
requirement that everything but `/content/*` is a 404 on the content
origin, and the failure is not cosmetic: the vault host is a file server
of the whole vault with directory listing, behind the same login. A
preview running there with `allow-same-origin` and `connect-src 'self'`
is same-origin with that file server, so a hostile HTML file from any root
(an agent workspace included) could fetch every vault note with the
viewer's session, where the accepted risk is a read of its own granted
root only. The reverse holds too: every vault HTML file opened in the
viewer runs unsandboxed on the same origin as the previews and their
storage. Making it safe would mean taking the file server off the vault
host, which is taking the viewer away. So the previews have their own
name.

**Proved 2026-10-23**, with the app still at `cfe1d49` (before `2132b85`
was deployed). From the public address, without a session:
`/content/x/index.html` is 302 to the portal; `/`, `/up`, `/api/agents`,
`/api/sessions`, `/app.js`, `/files` and `POST /api/sessions` are 404 from
Caddy; the access log shows `/content/redacted/index.html`. From the
browser's subnet, past the gate: every one of those, `/content/*`
included, is 404 from the app, and nothing sets a cookie. The header policy
was proved on the rendered block under a throwaway Caddy against an echo
upstream: `Cookie` and `Authorization` sent by the client did not arrive,
`Host` arrived as sent, a client `X-Forwarded-Host: loam-ui.<domain>`
arrived as the content host, and the upstream's `Set-Cookie` did not reach
the client. The app's refusal was proved on `2132b85` run from an archive
on loopback: `/api/*` with `Sec-Fetch-Site: same-site`, with `Origin:
https://loam-ui-content.<domain>` alone, or with `Origin: null` is 403;
`same-origin` is relayed. **The live app at `cfe1d49` does not refuse**
(a same-site request with the content origin reached `/api/agents`, 200);
the refusal is live once `2132b85` or later is deployed, and the content
host serves no page until then.

## The agents' own sites

*Added 2026-11-08 (the owner: behind the login, agents stand things up freely
on subdomains of their own; the open web waits for the owner's yes each time).*

`bin/site up <name> --by <agent>` puts `<name>.<domain>` behind the login in
seconds, with no engineer job: one TOML file in `state/sites/` (outside the
repo, gitignored; the agent's working state, not a runtime decision), then
`sudo bin/gen-deploy`, which renders it as one more host block and reloads
Caddy. Nothing else moves: Authelia's `*.<domain>` rule already covers every
name, the wildcard A record already answers, and Caddy gets the certificate
over HTTP-01 on the reload (Let's Encrypt allows 50 new certificates per
registered domain per week, the one ceiling a busy week of throwaway names
could meet). Personas reach it as `mcp__engineer__site`, which hands off to
the same command; with a shell they can run the command itself.

- **Private by construction.** The record has no exposure key. It renders
  through `caddy_host_block` like every private host (the browser's subnet,
  then the gate) unless it carries `public`, and only `bin/site publish
  <name> --yes "<the owner's words, date>"` writes that key, with the words.
  An empty `public` is refused. check-drift accepts an ungated site block
  only when its record says `public`.
- **What it serves.** A static root (default `ai-labs/<agent>/sites/<name>/`,
  made with a placeholder page; `.git` and `.env` hidden at any depth), or
  `--port N`, a proxy to `127.0.0.1:N` where the agent runs its own container
  under the app conventions. It cannot take a record's name or host, a
  content host, `auth`, `second-factor`, or the login's, browser's or
  broker's port.
- **Expiry.** `--expires YYYY-MM-DD` or `--for 2d` renders a `410` ahead of
  everything at the end of that date, so the site is dead on time, and
  `loam-site-expiry.timer` (hourly, `bin/site prune`) removes the record and
  regenerates. The render does not depend on the day, so an expired site is
  never drift.
- `site down <name>` removes the name and leaves the files; `site private`
  undoes a publish; `site list` shows them all.

## Staging: a second copy of loam-ui

*Added 2026-11-12, on a persona's brief for a long run of large page changes
(the owner that day: "Please take your time, run long, validate everything
and iterate until it is excellent"), so a landed commit can be looked at in a
browser before it reaches the address the owner uses.*

`deploy/loam-ui-next/` is the same app at `next.<domain>`, behind the login
like every record; since 2026-11-16 a second instance of the same unit,
`loam-app@loam-ui-next.service`, from Loam's checkout (`checkout = "@ROOT@"`,
`context = "app"`, `[service] port = 8082`). Two commands, siblings, each
printing the commit it leaves running (`<app>: healthy on <short> (was <short>)`):

```sh
sudo bin/deploy loam-ui-next up main       # staging, main's tip
sudo bin/deploy loam-ui-next up <commit>   # staging, a landed commit
sudo bin/deploy loam-ui up <commit>        # live
sudo bin/deploy loam-ui-next status        # "running commit: <short>"
```

A persona runs them from its own shell (its config: "the shell, sudo, ...
without asking"); no brief is needed for either. Both build only a commit on
Loam's `main`, because `resolve_commit` refuses a job's branch. Each takes its own app's live lock, so a staging deploy does not wait
on a loam-ui landing and a live one still does.

**What the two copies share, and what each has of its own.** Read off
loam-ui at `c5fbcc0` before the record was written:

- **Shared, because it is the broker's:** threads, boards, schedules,
  Activity, providers, integrations, files, and the appearance settings
  (`GET/PATCH /v1/settings/appearance`, kept in `state/broker/settings.json`).
  The app is a relay: every stream it serves is opened per browser request
  and ends with it, and nothing in it polls, writes or pushes on a timer of
  its own, so a second copy is one more client of the broker and nothing is
  done twice. Both hold the same token and reach the broker over the same
  bridge; the broker's wall admits the whole `loam-broker` subnet. So
  staging shows the owner's real data, and **anything done on staging is done
  for real**: a message sent there is sent, an End there ends the session.
- **Its own, on purpose:** its state directory (`/var/lib/loam-app/loam-ui-next`),
  which holds only the read-aloud and preview caches, swept by each copy's own timer;
  and the content host, `next-content.<domain>`, because a preview URL is
  signed with a key the process makes at start (loam-ui `src/content.js`), so
  only the copy that issued it can serve it. The service worker and the
  browser's own storage (`public/store.js`'s device settings) are per origin,
  so the device settings of `next.` start at their defaults.
- **Live's, whichever copy starts it:** an integration's sign-in. The GitHub
  app's manifest is built from the broker's idea of the app's URL
  (`loam_github_app.app_url`), so its callbacks return to live.

Staging cannot drift from live: both are instances of one unit template and
take their environment from one function (`service_env`), so the only
differences are the record's own (the host, the port, the content host).

## Framing a site in the app

*Added 2026-11-12, for the Apps section opening a site in the main pane.*

What is sent, read that day from inside the browser's subnet (which passes
the gate, so it sees what a logged-in request sees) and from outside with no
session:

- **Without a session**, every private host answers the gate's `302` to the
  portal with Authelia's `X-Frame-Options: DENY`, and the portal itself sends
  `frame-ancestors 'none'`. A login cannot happen inside a frame.
- **With one**, the response is the backend's alone: `forward_auth` copies
  nothing of Authelia's answer to the client. Caddy's `file_server` sends no
  framing header, so the agents' static sites (`light`, `wall`, `rows`) were
  frameable by any origin. `owner-site` sends `frame-ancestors 'none'`
  from its own server, and loam-ui sends the same for its page.
- **The cookie.** `loam_auth` (was `loam_session` until 2026-11-15) is `Domain=<domain>; Secure; HttpOnly;
  SameSite=Lax`. A frame of `light.<domain>` inside
  `loam-ui.<domain>` is same-site, so the cookie goes with it in every
  browser (third-party cookie rules and partitioning are about cross-site
  frames), and the session that loaded the page is the one the frame gets.

So since 2026-11-12 **every agent's static site carries one header**, from
`loam_deploy.frame_header`:

```
header Content-Security-Policy "frame-ancestors 'self' https://loam-ui.example.com https://next.example.com"
```

The origins are every record with `app_shell = true`, on its own host. It
narrows what was open: before it, any page on any origin could frame a site
(only a same-site one with the session, since a cross-site frame meets the
login). Caddy is the whole server of a static site, so this is the only CSP
those responses carry and it restricts nothing but framing. Not on a site
that proxies to a port, nor on a deployment record's hosts: a second CSP from
Caddy would override a container's own `X-Frame-Options` (browsers ignore it
when `frame-ancestors` is present), widening what that app asked for. Those
stay as their own server says, and the Apps list says `frame: false` for
them.

**What the page still needs, which is the page's:** loam-ui's own CSP has
`frame-src 'self' blob: <its content origin>`, so it will not frame a site
until its engineer adds the site origins (or `https://*.<domain>`) there.

## Previews for the Apps section

*Added 2026-11-15, for loam-ui's preview renderer (headless Chromium, then
inside the loam-ui container and since 2026-11-16 beside the app on the
host, empty profile, no credential), which captured a
letter tile for every app because every app's public name is behind the
login and the renderer copies no login on purpose.*

**One more Caddy site, internal only.** `caddy_preview_block` renders a
plain-HTTP site on `172.31.0.1:8935`: the gateway of the broker's bridge
(`loam-broker`, `lib/loam_broker.BIND_ADDR`) and the port
`PREVIEW_PORT`; and since 2026-11-16 on `127.0.0.1:8935` too, for Loam's
app, which runs on the host now and photographs through loopback (a host
process that connects to the bridge's address arrives from the box's public
one and is refused, as the third layer below says). Loopback is not
reachable from off the box. It serves every row of the Apps list that has a host of its
own, past the login and exactly as its public block serves it past the gate:

- an agent's static site: the same root, the same hidden names (`.git`,
  `.env`); an expired site answers 410 here as it does on its name;
- an agent's port site, and a deployment record with a container that is not
  Loam's page (an owner's own site, say): the same `reverse_proxy
  127.0.0.1:<port>`, Host passed as the renderer sent it.

Not on it: the vault, the commons' files, the login, loam-ui and its staging
copy, and a plugin's row (that one is served on the page's own host at
`/x/<id>/` and is the page's to fetch). A name it does not know is a 404.

**Addressed by host**, so a page's root-relative assets resolve as on its
real name: `http://<label>.preview.internal:8935/`, where `<label>` is the
app's host label (the first label of its Apps row's `url`). `.internal` is
reserved for private use and resolves nowhere; the renderer maps it to the
address itself (Chromium `--host-resolver-rules="MAP *.preview.internal
172.31.0.1"`). `bin/deploy` hands the app both halves as `APPS_PREVIEW_BASE`
(`http://preview.internal:8935`) and `APPS_PREVIEW_ADDR` (`127.0.0.1` for
the app on the host, `service_env`; `LOAM_PREVIEW_ADDR`, `172.31.0.1`, for a
container that interpolates it).

**Who can reach it, and the three layers.**

- *Nothing outside the box.* No interface outside the box carries
  172.31.0.1, no DNS name points at it, and Caddy binds that address only
  (the `bind` line), never 0.0.0.0. The public names, their blocks and their
  login are byte-identical with and without the route (tests/test_previews.py).
- *The kernel*: `broker/firewall.sh` puts a second jump, for 8935, into the
  broker's own chain `LOAM-BROKER-HOST`, which accepts the box itself (`lo`)
  and `172.31.0.0/24` and drops the rest. So exactly the containers that
  already reach the broker reach this: loam-ui and loam-ui-next today, and
  only a record whose compose joins `loam-broker` (or a hand `docker network
  connect`, which check-drift names) can add one. The personas' browser is on
  `loam-sight`, whose wall rejects every RFC 1918 address. One chain, two
  jumps, so the two ports cannot disagree about who is let in; check-drift
  asks the kernel for both.
- *Caddy*: `not remote_ip 172.31.0.0/24 127.0.0.0/8` is a 403 before any
  app (loopback since 2026-11-16, for the app on the host). That
  includes a process on the box itself: the kernel lets it in (`lo`), but
  its source address is the box's public one (caused 2026-11-15, `curl
  http://172.31.0.1:8935/` from the box logged the box's public address as `remote_ip` and
  got 403), so a probe from the host is `docker exec loam-ui wget -S -O
  /dev/null --header 'Host: <label>.preview.internal' http://172.31.0.1:8935/`.

What a container on the bridge gains is what the owner's login shows of the
apps, and nothing of the vault or the page. No credential is involved: the
route is the credential, and the bridge is the lock.

**Boot.** The address exists only once docker has raised the bridge, Caddy
and docker are not ordered, and a listener that cannot bind fails Caddy's
whole start. So `gen-deploy` renders `/etc/sysctl.d/60-loam-preview.conf`
(`net.ipv4.ip_nonlocal_bind = 1`) and applies it before reloading Caddy;
Caddy can bind first and the socket answers once the bridge is up. It makes
nothing reachable: a packet for an address the box does not carry is never
delivered. check-drift reads the kernel's value.

Access lines go to `/var/log/caddy/access/preview.log`, with the same
filters as every host.

## Exposure: two values, and public by construction only

**private** — the host block imports the gate; a request reaches the app
only with an Authelia session.

**shared** — the same block, plus two matchers ahead of the gate: a request
whose query carries `?share=<token>` passes and sets a cookie
(`loam_share`, Secure, HttpOnly, SameSite=Lax, 30 days), and a request
carrying that cookie passes. Both matchers also compare the clock against
the record's date, so an expired share is dead even before a regenerate.
The link is `https://<host>.<domain>/?share=<token>`. To share:

```sh
# 1. set exposure = "shared" and [share] expires = <date> in the record
sudo bin/gen-deploy --share <app>       # mints the token, regenerates, prints the link ONCE
sudo bin/gen-deploy --unshare <app>     # deletes the token, regenerates: revoked
```

A record that says shared with no token, or with a date in the past,
renders private. Caddy alone does the cookie dance; no share service was
needed.

**public** — not a value in any record. The operator writes the app's name
into `/etc/loam/public` and asks for a regenerate; the host block is then
emitted with no gate and a `# PUBLIC` comment naming the file. The platform
engineer's settings deny it `Edit` and `Write` on that path (a deny is not
promptable) and its boundary guard refuses the path as outside its roots.
check-drift reports any live host block without a gate whose app is not in
that file. A name in the file with no record emits nothing but a comment.

## The personas' sight

`loam-browser` is one headless Chromium (the Playwright MCP image) in a
container on its own Docker network (`loam-sight`, `172.30.0.0/24`),
published on `127.0.0.1:8931`, 1 GB, no capabilities, restarting like an
app. Every persona has it as the `browser` HTTP MCP server in `.mcp.json`
and the server-wide `mcp__browser` in `permissions.allow`; both rendered by
`bin/provision`. **It is on every lane.** Move 10 dropped it from both
unattended lanes on the reasoning that a wake has no one to show a page
to; the owner reversed that on 2026-09-09 (reading a rendered page is the
half of sight an unattended run needs), and since 2026-11-09 `bin/wake`
withholds no tool from any lane. `browser_run_code_unsafe` is denied by name. The
persona `CLAUDE.md` carries one paragraph on all of this (`sight_paragraph`
in `bin/provision`), rendered from the deployment records.

- **Each MCP session is its own browser context.** Two clients, one
  navigated, the other's tab list still empty (caused 2026-09-08).
- **Open to the web, walled off from the box.** Since 2026-09-09 (the owner:
  designs must render "correctly and without errors", so a page's fonts,
  scripts and images come from wherever the page says) there is no origin
  allow-list. What is blocked, and where:
  - *The kernel* (`/etc/loam/browser/firewall.sh`, rendered by `gen-deploy`
    from `sight_firewall_script`, raised by `loam-sight-firewall.service`
    after docker): from the `loam-sight` subnet, every RFC 1918 range (so
    every Docker bridge and gateway, Authelia by address), 100.64/10,
    169.254/16 (the provider's metadata service), and 127/8 are rejected
    with ICMP admin-prohibited in both paths a packet can take, FORWARD
    (via `DOCKER-USER`) and INPUT (the box itself, where only TCP 80/443
    and UDP 443 return, so the domain's hosts answer at the public address
    and sshd, a plugin's declared listener and every loopback-published port do not). Before
    the wall, from inside the container, sshd answered on every gateway
    and on the public address and 169.254.169.254 connected; the origin
    flag was the only thing in the way, and it matches URLs, not the
    addresses they resolve to. A public name that resolves inward
    (`app-172-30-0-1.nip.io`) now dies of `ERR_ADDRESS_UNREACHABLE`.
    `check-drift` asks the kernel for every rule: `ufw reload` flushes
    every chain (Docker's too), after which `systemctl restart docker`
    re-raises the wall through `PartOf`.
  - *The flag*, `--blocked-origins`: the same ranges as host globs, plus
    `localhost`, `127.0.0.1`, `[::1]` and `0.0.0.0` on every port. Belt to
    the kernel's braces for addresses, and the one layer that reaches the
    container's own loopback, where the MCP server listens; behind it the
    server's `--allowed-hosts` check 403s any other Host header
    (`localtest.me:8931` answered 403). A match is `ERR_BLOCKED_BY_CLIENT`.
  - *Chromium itself*: port 22 and the other restricted ports are
    `ERR_UNSAFE_PORT`; an https page cannot fetch an http target
    (mixed content). The bare public IP by number is not listed anywhere:
    Caddy answers it with a 308 to an https URL it holds no certificate for.
  - `file://` is blocked by the image and cannot be allowed short of a flag
    we do not pass. `browser_run_code_unsafe` is denied by name in every
    persona's settings.
  - *A page's own tools*: from image v0.0.82 the server lists the tools a
    page registers through WebMCP as MCP tools (`webmcp_<name>`), which a
    persona allowing `mcp__browser` whole could call unprompted. The server
    reads them from `navigator.modelContext`, which any page can define, no
    browser flag needed. `--no-webmcp` turns that off: on 2026-10-06 a test
    page's `webmcp_loam_probe` was listed and ran without the flag, and with
    it the tool list stayed at the image's 25 (again on v0.0.83, 2026-10-28,
    and v0.0.85, 2026-11-14).
  - *Chromium's sandbox* is off (`--no-sandbox`, which the image's own
    entrypoint also passes); the container's own walls are `cap_drop: ALL`,
    `no-new-privileges` and the network above. Same on v0.0.80 to v0.0.83,
    read off the live Chromium processes (9 of 11 carry the flag; the other
    two are crashpad handlers). Turning it on is untested.
  - *What this does not change*: nothing inbound. The browser is an
    outbound channel for whatever a persona puts in a URL or a form, which
    WebFetch already was; the vault is still readable only by a session
    the owner logged in, or by this one subnet, which no page can read across
    origins (no CORS headers are sent).
- **It gets through the gate on every private host** by `remote_ip` from
  its subnet, because a persona cannot hold the owner's passkey. The subnet
  is the browser's alone. See the note in `lib/loam_deploy.py`.
- **"Local files" is a URL.** `files.<domain>` serves
  `ai-labs/commons/www/` (a static host record, `deploy/files/`), behind
  the gate for a phone and open to the browser's network; a persona writes
  a file there and opens `https://files.<domain>/<name>`. The commons sweep
  expires files there after seven days like the rest of the square.
- **The vault is a URL too.** `vault.<domain>` serves the whole vault
  read-only with directory listing on (`deploy/vault/`, built 2026-09-09 on
  the owner's word that every persona reads vault HTML at all times). HTML
  renders as HTML, a note is served as `text/markdown` and Chromium shows
  it as text, and a `../` path answers 404 (Caddy cleans the path before
  the file server sees it). The record's `hide` key names `.git`,
  `15_CONVERSATIONS` and `.stversions` (the last two on the owner's word,
  2026-09-09: the transcripts and the file sync's version store stay on disk
  for the agents and off the web); Caddy matches a bare name against every
  path component, so each is hidden at any depth, and nothing else is.
  Caddy follows symlinks and has no switch to stop at the root; the vault
  has one (`README.md`, inside it). Same gate, same browser bypass.
- Screenshots come back inline as image content. A long page's snapshot
  is written to a file inside the container and only linked from
  `browser_navigate`'s reply; `browser_snapshot` returns it inline.

## The deploy loop

```
sudo bin/deploy <app> build <commit>   image <app>:<short> from the checkout at that commit
sudo bin/deploy <app> up <commit>      build if missing, (re)start, wait for healthy, prune;
                                       unhealthy, put the previous image back
sudo bin/deploy <app> up <commit> --forward-only
                                       the same, unless what runs already carries <commit>
sudo bin/deploy <app> prune            keep the running tag and the one before it, remove the rest
sudo bin/deploy <app> status | logs [N] | exec <cmd...>
```

**Two image tags per app, not one per deploy.** A healthy `up` removes
every `<app>:<short>` tag but the one now running and the one it replaced;
`prune` is the same sweep on its own, keeping the running tag and the
newest other. Every tag is a named commit and `up` builds a missing one, so
rolling back further than one step costs a build, never a lost version.
Until 2026-09-30 nothing removed a tag, and loam-ui had 33.

The build context is `git archive <commit>` of the checkout, so an
uncommitted edit cannot reach the image and the tag says what is running.
The Dockerfile lives at the app repo's root; the record here never carries
one. The compose file names its image `${LOAM_COMMIT}` with no default, so
a bare `docker compose up` fails loudly instead of starting whatever was
tagged last.

**The constraint the command exists for:** no project ever holds the Docker
socket, and a project acts only on its own container. The command reads
everything from the record, and `/etc/sudoers.d/loam-deploy` (rendered)
allows `deploy <app> *` per app and nothing else.

**Why that sudoers file is inert on this box today.** Every job runs as
the box's one unix user, which holds `NOPASSWD: ALL` from
`loam-engineer`, and a project engineer's job unit carries
`NoNewPrivileges`, so it cannot sudo at all. A per-app sudoers line only
binds when projects run as a unix user of their own (one per project, or
one for all projects) that holds nothing else. That is a decision about
the shape of a project engineer's session and it is recorded as open in
the Move 10 report; until it is taken, the platform engineer runs the
command on a persona's brief.

**A failed `up` leaves the previous image running (since 2026-11-12).** A
failed build never touches the container. A container that comes up
unhealthy has its last 40 log lines printed and is replaced by the image
that ran before it, which prune has kept; nothing is pruned, and the
command exits 2. Every `up` that read what runs ends with one
`deploy-result: {json}` line on stdout (action, before, after, health),
which is what the launcher reads.

**A project job can deploy itself at landing (since 2026-11-12).** A brief
sent with the brief tool's `deploy: true` carries `deploy: <app>` in its
header, for the project whose record here is named after it (loam-ui).
After the landing has merged, tested and pushed `main`, `bin/engineer-job`
runs `sudo -n bin/deploy <app> up <sha> --forward-only` in a transient user
unit of its own (`loam-autodeploy-<job>`), outside the job unit's
`NoNewPrivileges`, and writes the result into the launcher's half of the
receipt and `deploy.json` in the job directory. `--forward-only` is what
keeps two landings close together from rolling each other back: each
landing's sha carries the ones before it, and a deploy whose commit is
already contained in what runs changes nothing. A failed deploy never fails
the landing. Staging is not deployed this way; see
`lib/loam_autodeploy.py`.

## How an app reaches the session broker, and gets its token

*Added 2026-09-16, for `loam-ui`. The broker is `agents/broker/README.md`:
the host-side half that starts persona sessions, on a Docker bridge the
host owns (`loam-broker`, `172.31.0.0/24`), bound to that bridge's gateway
and walled off from every other container by the kernel.*

An app that needs it changes its compose file in exactly two places, and
nothing else here moves:

```yaml
services:
  web:
    networks: [default, broker]        # BOTH: naming any network drops the
                                       # implicit default, and with it the
                                       # proxy's route to the container
    environment:
      BROKER_URL: "${BROKER_URL:?set by bin/deploy from lib/loam_broker}"
      BROKER_TOKEN: "${BROKER_TOKEN:?read by bin/deploy ...}"
networks:
  broker:
    name: loam-broker
    external: true                     # the runtime owns it; compose only joins
```

`bin/deploy` (`broker_env`) puts those values in the environment of every
`docker compose` it runs — the URL from `lib/loam_broker`'s constants, the
token read at that moment from the
broker's state directory, which is gitignored and 0600. **The token is never rendered to a file, never in a
repo or an image, and never printed.** A compose file that does not name
the variables never sees them; one that does, with no broker on the box,
fails on its own `:?` message instead of starting a container holding an
empty credential. Every verb passes them, not just `up`, because a `:?`
variable is needed by `ps`, `logs` and `exec` too.

This is the layer's third secret path and the first an app's own compose
file can use: Authelia's are an `env_file` at a root-owned path outside
every repo, a share token is read by the generator and rendered into the
Caddyfile, and an app's compose file lives in this repo, where a literal
would be a credential in git.

## How an app gets a vendor API key

*Added 2026-09-25, when loam-ui needed Google's Gemini for dictation.*

The same mechanism as the broker's token, pointed at a different source,
because the problem is the same one: the key must reach the container
without ever being written into this repo.

The source is `/etc/chassis/secrets.env` — the box's secret store, owned
`root:<box user>` at 0640, outside every repo, and older than Loam; it
carries the chassis service's vendor keys. `bin/deploy` runs as root, reads
the named variables out of it at the moment it runs compose (`read_secrets`,
a plain parser and not a shell `source`, which would execute what it read),
and passes them as environment variables. The app's compose file
interpolates them by name:

```yaml
    environment:
      GEMINI_API_KEY: "${GEMINI_API_KEY:?read by bin/deploy from the box's secret store}"
```

**Which app may be handed which key is an allowlist in `bin/deploy`**
(`APP_SECRETS`), not something an app grants itself by naming a variable.
The store holds ten keys, several of them spend on vendors that have
nothing to do with any app here, and an app's compose file is edited by
that app's own engineer. Adding a name to that table is a runtime change,
reviewed like every other one. An app with no entry is handed nothing, and
`tests/test_deploy_secrets.py` holds both halves of that — plus a grep of
this whole directory for a `AIza…`-shaped literal, since the mistake this
guards against is someone pasting a key into a compose file to save a step.

An app's own credential can take the same path when it is minted on the box
rather than issued by a vendor: minted with `secrets.token_urlsafe(32)`
straight into the store, never printed, and rotated by replacing the value
and redeploying the app. The first was loam-ui's `DICTATION_TOKEN`
(2026-09-28), the bearer token its `POST /api/dictate` checked; it was
retired with that route on 2026-10-01 and its line left the store.

A key absent from the store, or present and empty, is not passed, and the
`:?` fails the command with its own message rather than starting a
container holding an empty credential — the same trap, and the same cure,
as the broker's token above.

Being on the network is not an authorisation. The broker answers only
`/up` without `Authorization: Bearer <token>` and 401s every other route,
so the wall, the token and the app's own login are three separate layers.

## The two networks an app may join

*Added 2026-09-25. The generator refused a host network, a bind mount and a
privileged container from the start, and said nothing about which Docker
networks a compose file joined.*

An app joins its own project `default` — the proxy's route to its container —
and `loam-broker`, and nothing else. `lib/loam_deploy._compose_networks`
refuses anything third by name, so the refusal lands at
`sudo bin/gen-deploy` and at `sudo bin/deploy <app> up` both, before a
container starts.

**Why it is a refusal and not a convention.** Every private host block lets a
request from `loam-sight`, the personas' browser's subnet, past the login
(above, "The personas' sight"): a persona cannot hold the owner's passkey, so
the bypass is keyed on that subnet and is exactly one container wide. It is
one container wide because no other container joins that network — and until
this check existed, three words in a compose file in this repo would have put
an app's own container inside the bypass, with `bin/deploy` starting it as
root. `loam-authelia_default` is the same shape one network over.

**The alias is not the network.** A service's `networks:` entries are keys
into the file's own top-level table, and that table decides the real name:

```yaml
networks:
  broker:
    name: loam-broker          # `name:` spells it out
    external: true             # the runtime owns it; compose only joins
  # sight:
  #   name: loam-sight         # refused: the real name is checked
  # loam-sight:
  #   external: true           # refused: external and unnamed, the KEY is the name
  # default:
  #   name: loam-sight         # refused: `default` can be pointed anywhere
  #   external: true
```

So the check resolves every alias before comparing, and refuses a network the
file *declares* even when no service joins it — `docker compose up` looks it
up either way, and a declaration that reads as authorised is not left lying
in the file. An app's own second bridge is refused too: two is the rule, and
an app with a network nobody has asked for is a shape to settle before it is
running.

**And the running box is asked the same question.** `bin/check-drift` reads
`docker ps` and reports a container on a network its compose file does not
join, or a container no record owns at all on `loam-sight` or `loam-broker`.
A network can be joined by a hand `docker network connect`, which no file
records; the file and the kernel are two homes, so both are read.

## Retiring an app

The deployment and the project engineer are two teardowns, and the order
is deploy first. `bin/provision --retire` refuses while `deploy/<app>/`
still holds a record, because the record's build checkout defaults to the
registry entry retire deletes, and a container left behind a host block
with no engineer cannot be rebuilt. Caused on probe-clock, 2026-09-08:

```sh
sudo bin/gen-deploy --unshare <app>                 # if shared: the link dies now
sudo env LOAM_COMMIT=<running short commit> \
  docker compose -f deploy/<app>/compose.yaml down --rmi all --volumes
git rm -r deploy/<app>                              # the record and the compose file
sudo bin/gen-deploy                                 # host block and sudoers alias go; Caddy reloads
bin/check-drift                                     # the deployment line no longer names it
bin/provision --project <app> --retire [--delete-repo]
```

`--volumes` takes the app's named volumes with it; leave it off when the
data outlives the app. Every image tag the app ever had comes off with
`--rmi all`. There is no `bin/deploy <app> down`: the record is the
decision, and removing the record is how an app is retired.

**What the outside sees afterwards.** Caddy keeps no host block for the
name, so an HTTPS request fails the TLS handshake (Caddy answers the
client hello with an `internal error` alert, since it has no certificate
to offer for that server name), and plain HTTP gets Caddy's catch-all 308
to HTTPS, which then fails the same way. A phone shows a "can't provide a
secure connection" page, not a 404. The certificate stays in Caddy's
store (`/var/lib/caddy/.local/share/caddy/certificates/.../<host>/`,
0600, owned by `caddy`); Caddy stops renewing it and its daily storage
clean removes it after it expires. A record with the same host inside the
90 days is served on the stored certificate with no new issuance.

## App data

Named volumes only. The generator refuses a bind mount, a privileged
container, host networking, added capabilities and the rest of
`FORBIDDEN_SERVICE_KEYS`, and any network but the two above. The first
stateful volume on the box is
`authelia-data` (the users file, the SQLite store, every passkey);
`gen-deploy --init-login` creates it as an external volume so `compose
down -v` cannot take it.

**The backup timer, not built yet — its shape for the first stateful
app's brief:** a system unit pair `loam-backup.service` /
`loam-backup.timer`, daily, rendered by gen-deploy from the compose files'
named volumes. The service runs, per volume, `docker run --rm -v
<vol>:/data:ro -v /var/backups/loam:/out <busybox> tar czf
/out/<app>-<vol>-<date>.tgz -C /data .`, keeps fourteen days on the box,
and stops there: an off-box copy is content leaving the box, so it is an
operator act (Boundary 6) and is added on the owner's word with a
destination they name. SQLite volumes are snapshotted through `sqlite3
.backup` inside the app's container first when the app is running.
check-drift compares the rendered units like every other unit.

## Changing the domain

One edit to `deploy/loam.toml`, then `sudo bin/gen-deploy` and
`bin/provision <agent> <wake>` per persona (the browser's allowed origins
and Authelia's cookie domain are rendered from the value). Certificates
are issued on the first request to each new host. Passkeys and the
session cookie are bound to the old name, so the owner re-enrols their passkey
and every shared link dies; nothing else changes. Rename the session cookie
(`session.name` in `authelia_config`) in the same change: a new domain under
the old one still receives the old parent-domain cookie, and Authelia reads
it first, so every login succeeds and bounces back to the portal (what
happened after L14 until the rename to `loam_auth`).

Done once, on 2026-11-15 (first release L14): the base domain became
`<owner>.<base>`, the owner's own name under it. What that took beyond the one edit:

- **The app at the domain itself.** `apex = true` in `deploy/loam-ui/deploy.toml`
  serves the record at `https://<domain>/` rather than `loam-ui.<domain>`. At
  most one record says it, and on an install's `[edge]` the edge's `app`
  decides instead. The apex app's host carries the login-code relay
  (`/_loam/login-code`, below), so `loam-claim.service` runs here too,
  installed from `systemd/system/` the way `bin/loam-install` installs it.
- **The app's own view of the domain.** loam-ui reads `APPS_DOMAIN` for the
  sites its CSP frames and its Apps section opens; both compose files pass
  it from `LOAM_DOMAIN`, so `bin/deploy` must rebuild both after a change.
- **Old names.** `[moved]` in `deploy/loam.toml` keeps every old host
  answering with nothing but a 308 to the same label under the new domain
  (the app's old host to the domain itself), no login, path and query kept.
  The hosts are written out because each needs its own A record once the
  old wildcard is gone. Past `until`, `bin/check-drift` reports them as due;
  removing them is deleting the table, `sudo bin/gen-deploy`, and deleting
  exactly those A records.
- **Paths that outlive the move.** `[[forward]]` keeps named paths of one
  old host (a GitHub App's callback URLs, which no API can change) answering
  with a 308 to the domain after `[moved]` is gone; the rest of that host
  404s. While `[moved]` still lists the host its whole-host redirect covers
  the paths and nothing extra renders. Its A record is not one to delete,
  and check-drift's "due to go" list says so (2026-11-15).
- **What does not follow.** Anything the browser keeps per origin (the
  app's theme and device settings in local storage) starts empty at the new
  name, and a passkey enrolled at `auth.<old>` does not sign in at
  `auth.<new>`. The GitHub App's registered callback URLs name the old app
  host until they are edited on GitHub; `[[forward]]` covers them after the
  redirects go.

## An install's edge, the claim and the relay

*Added 2026-11-14 (first release L12); the whole account is
`docs/install.md`.* A fresh install's `/etc/loam/loam.toml` carries an
`[edge]` table: its own domain (`<name>.<base>`), the app served at
that domain itself, the records it serves (`loam-ui`, `files`, `vault`),
and three rules this file's renders follow only when the table is there:

- **No host before DNS.** Until `bin/loam-await-dns` has seen the name and
  its wildcard resolve to the box (`/var/lib/loam/edge.json`), the Caddyfile
  carries no host block, so Caddy asks Let's Encrypt for nothing.
- **The claim.** Until `/etc/loam/claimed` exists the app's host is the
  claim page alone, outside the login (`loam-claim.service` on
  `127.0.0.1:8934`), and `gen-deploy` does not start Authelia, which has no
  user yet.
- **The relay.** `/_loam/login-code` on the app's host goes through the gate
  to the same service, ahead of the browser's subnet, and hands the
  signed-in owner the one-time code Authelia's file notifier wrote.

`gen-deploy --init-secrets` makes the login's secrets and volume with no
user; the claim writes the user. A box without `[edge]` renders exactly as
before (proved by md5 of all eight renders against the canonical checkout
on 2026-11-14).

## First login

`gen-deploy --init-login <user>` mints Authelia's secrets, the user with a
generated password, and a TOTP device, and writes them ONCE to
`/etc/loam/authelia/first-login.txt` (root, 0600), outside every repo and
the vault. It is read with `sudo cat` from the operator session and deleted
after. Password, then TOTP, then add a passkey and change the password in
Settings; after that the passkey is the login.
