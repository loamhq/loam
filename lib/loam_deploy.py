"""The deployment layer: what the box exposes, generated from one directory.

Move 10 of the phase-5 closing plan (decisions 8-10), built 2026-09-08.
Everything the internet can reach on this box is rendered here from
``agents/deploy/`` and nowhere else:

  deploy/loam.toml          the domain, the one place its name lives
  deploy/<app>/deploy.toml  the app's record: its exposure, its host, its share,
                            and any hostnames it also answers on under a
                            domain of its own (``[domains."<domain>"]``)
  deploy/<app>/compose.yaml how the app runs (image, loopback publish, limits)
  /etc/loam/public          OPERATOR-authored, outside every repo: the apps
                            that are public. Read here, written by the owner.

and rendered into the live files ``bin/gen-deploy`` writes and
``bin/check-drift`` diffs:

  /etc/caddy/Caddyfile                 one host block per app, always gated
  /etc/docker/daemon.json              the daemon's loopback-by-default publish
  /etc/loam/authelia/configuration.yml the login, minus its secrets
  /etc/loam/authelia/compose.yaml      the login's container
  /etc/loam/browser/compose.yaml       the personas' shared headless browser
  /etc/loam/browser/firewall.sh        the wall around the browser's network
  /etc/systemd/system/loam-sight-firewall.service   raises it after docker
  /etc/sudoers.d/loam-deploy           bin/deploy, per app, and nothing else

**Exposure has two values and the generator has no third.** ``private`` is a
host block behind ``forward_auth``; ``shared`` is the same block plus a
matcher that lets a request carrying the share token, or the cookie the
tokened request set, through. A host block without a gate is emitted for
exactly one reason: its name is in ``/etc/loam/public``, a file the engineer
is denied ``Edit`` and ``Write`` on and that the owner writes themselves. That is
what "content leaves the box only by an operator act" is enforced by
(boundary 6 of the charter), together with check-drift reporting any live
host block that has no gate and is not named there.

**An app may answer on a domain of its own** (2026-09-10, for the owner's
personal site: he wants to see it on his real domain, with a real
certificate, behind the login, before it is public). The record says::

    [domains."owner-site.example"]
    hosts = ["owner-site.example", "www.owner-site.example"]

and every listed host renders as one more host block on the same upstream,
gated exactly like ``<host>.<domain>``: private is private on every name,
shared is shared on every name, and the operator's public file ungates every
name at once. The key is the cookie domain, spelled out rather than derived,
because Authelia needs it as a value: a session cookie is scoped to one
registrable domain, so each extra domain gets its own entry in
``session.cookies`` with its own portal at ``<auth_host>.<domain>`` (one
more Caddy block, one more A record), and its hosts get an access-control
rule at the record's policy. Authelia keeps one session PER cookie domain;
the same user and the same users file, but a login on one domain is not a
login on the other, and a passkey is bound to the domain it was enrolled on.

**A private host may let one exact request past its login** (2026-09-28, for
a phone plugin that posts dictation to loam-ui and cannot hold a passkey).
The record says::

    [[bypass]]
    method = "POST"
    path = "/api/dictate"

and each entry renders as one more ``handle`` ahead of the gate, matched by
an expression on the method and the request URI as the client sent it,
compared whole: no query, no prefix, no case folding, no ``..``. Every other
path and method on the host stays behind the login. The app behind it is
expected to check its own credential on that route; the proxy lets the
request reach it and does nothing else. It is not an exposure: the host is
still private, and still gated for everything else.

**An app may have a content host** (2026-10-23, for loam-ui's live HTML
previews, which must run on an origin that is not the app's). The record
says ``content_host = "<label>"`` and one more host block renders at
``<label>.<domain>``: only ``/content/*`` reaches the app, with the Host
preserved and Cookie and Authorization stripped; every other path is a 404
before the login is asked; no response from it may set a cookie. It is
always gated like a private host, whatever the app's exposure. The app
does its own dispatch on the Host and checks its own signed grant there.

Secrets are never rendered by anything here. Authelia's three secrets and
the users file are written once by ``gen-deploy --init-login`` into
root-owned files outside every repo, and a share token lives in
``/etc/loam/share/<app>.token``; this module reads them, and reads them as
root or through ``sudo -n cat``, so that check-drift running as the box's user
can compute the same Caddyfile the generator did.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import loam_config as lc

import yaml

# The broker's network name, subnet and port have one home and it is not this
# file. Imported rather than repeated because the network check below names
# `loam-broker` as one of the two an app may join, and a second spelling of it
# here is exactly the two-homes defect the runtime is built against. The
# import is one way: nothing in loam_broker reads this module.
import loam_broker

AGENTS = lc.ROOT
AI_LABS = lc.WORKSPACES
DEPLOY = AGENTS / "deploy"
# The deployment configuration (the domain, `[moved]`, `[[forward]]`): the
# checkout's own `deploy/loam.toml` by default, or the file `deploy.config`
# in /etc/loam/loam.toml names. A box set up by hand keeps its names in a file
# of its own, outside the checkout, so an update never replaces them and the
# checkout stays the release's (docs/config.md).
CONFIG_PATH = (Path(lc.get("deploy.config")).expanduser() if lc.get("deploy.config")
               else DEPLOY / "loam.toml")

ETC = Path("/etc/loam")
PUBLIC_FILE = ETC / "public"
SHARE_DIR = ETC / "share"
CADDYFILE = Path("/etc/caddy/Caddyfile")
DAEMON_JSON = Path("/etc/docker/daemon.json")
AUTHELIA_DIR = ETC / "authelia"
AUTHELIA_CONFIG = AUTHELIA_DIR / "configuration.yml"
AUTHELIA_COMPOSE = AUTHELIA_DIR / "compose.yaml"
AUTHELIA_SECRETS = AUTHELIA_DIR / "secrets.env"
AUTHELIA_FIRST_LOGIN = AUTHELIA_DIR / "first-login.txt"
BROWSER_DIR = ETC / "browser"
BROWSER_COMPOSE = BROWSER_DIR / "compose.yaml"
SIGHT_FIREWALL_SCRIPT = BROWSER_DIR / "firewall.sh"
SIGHT_FIREWALL_UNIT = Path("/etc/systemd/system/loam-sight-firewall.service")
SUDOERS = Path("/etc/sudoers.d/loam-deploy")
DEPLOY_BIN = AGENTS / "bin" / "deploy"

# Modes for the files gen-deploy writes; anything not named here is 0644.
# sudo refuses a sudoers file that is writable by anyone, so this one is
# not a default. The firewall script is run by systemd, so it is executable.
MODES = {SUDOERS: 0o440, SIGHT_FIREWALL_SCRIPT: 0o755}

# Pinned images. An upgrade is an edit here, a `gen-deploy`, and a line in
# the receipt; never a `:latest` that moves under the box.
AUTHELIA_IMAGE = "authelia/authelia:4.39.23"
# The login's session store (added 2026-11-09). Without it Authelia kept
# sessions in its own memory, so every reboot -- and a resize is two --
# signed the owner out on every device at once. Redis keeps them in a volume:
# an RDB snapshot every minute there was a change, and one on the SIGTERM a
# shutdown sends, so a clean reboot loses nothing and a crash loses a minute.
AUTHELIA_REDIS_IMAGE = "redis:7.4.12-alpine"
# v0.0.85 (2026-11-09; v0.0.84 never had an image): tool calls no longer
# hang in their settling delay when page timers cannot run, and
# browser_evaluate returns BigInt, NaN, Infinity and circular objects
# readably (measured 2026-11-14: `10n ** 20n` was "Do not know how to
# serialize a BigInt" on v0.0.83, `100000000000000000000n` here). Same 25
# tool names and `--no-webmcp` as v0.0.83; new parameters stay in the page
# (browser_tabs isolatedContext, browser_find maxResults,
# browser_network_requests all). Rollback: pin v0.0.83 again and `gen-deploy`.
BROWSER_IMAGE = "mcr.microsoft.com/playwright/mcp:v0.0.85"

# Loopback ports the runtime's own containers publish on. Apps choose their
# own in their compose file; the generator refuses a collision.
AUTHELIA_PORT = 9091
# Server plugins (2026-11-13): bin/plugin-gateway on 127.0.0.1, which passes
# /x/<id>/ to each plugin's own port (lib/loam_plugin_servers.py). Every
# record that says `app_shell` (Loam's page and its staging copy) sends /x/*
# there, inside the same handles as the page, so the same login guards it.
PLUGIN_GATEWAY_PORT = 8933
BROWSER_PORT = 8931
BROWSER_URL = f"http://127.0.0.1:{BROWSER_PORT}/mcp"
BROWSER_SERVER_NAME = "browser"   # the MCP server's name in every persona

# The Playwright MCP server checks the request's Host header against the
# host it was bound to, and bound to 0.0.0.0 inside the container it
# accepts only `localhost:<port>`. A client dialling 127.0.0.1 -- which is
# what every persona's .mcp.json does, and what the isolation probe did on
# 2026-09-08 -- gets 403 "Access is only allowed at localhost:8931" and the
# MCP client hangs on it. Both spellings of loopback are allowed and nothing
# else; the publish address on the box is still 127.0.0.1 only.
BROWSER_ALLOWED_HOSTS = f"127.0.0.1:{BROWSER_PORT},localhost:{BROWSER_PORT}"

# The browser's own Docker network. Caddy lets a request from this subnet
# through a private host's gate: the browser is the personas' sight, and a
# persona cannot hold the owner's passkey. The subnet is the browser's alone
# -- no other container joins it -- so the bypass is exactly one container
# wide. Not a way in: the wall below (sight_firewall_script) is what keeps
# this subnet off everything on the box but Caddy's public 80/443, and it
# is keyed on the subnet, so the two must move together.
SIGHT_NETWORK = "loam-sight"
SIGHT_SUBNET = "172.30.0.0/24"

# The wall around that network. The browser reaches the open web (a page's
# fonts, scripts and images come from wherever the page says; the owner,
# 2026-09-09: designs must render "correctly and without errors") and
# nothing inside this box. Measured before the wall (2026-09-09, from
# inside the container): sshd answered on the gateway, on every other
# bridge's gateway and on the public address; a file-sync daemon's 22000 answered;
# the provider's metadata service at 169.254.169.254 connected. The origin
# flag was the only thing between a page and those, and it matches URLs,
# not addresses: a public name that resolves inward walks past it. So the
# kernel is the layer. Two chains in the filter table, both keyed on the
# subnet: LOAM-SIGHT from DOCKER-USER (forwarded traffic: other bridges,
# the provider's network, the internet) rejects every internal range;
# LOAM-SIGHT-HOST from INPUT (traffic to this box itself, which never
# passes FORWARD) rejects the same ranges, returns 80/443 so the domain's
# own hosts answer at the public address, and rejects the rest (22, 22000,
# the Caddy admin port, anything a future service binds). Reject, not
# drop: a page's fetch fails at once instead of hanging to its timeout.
# Replies to connections the host opened (docker-proxy dialling the MCP
# port) are returned first, or the browser could not answer its clients.
# The container's own loopback is inside its network namespace, where no
# host rule reaches; there the origin flag and the MCP server's own Host
# check are the layers (see browser_blocked_origins).
SIGHT_INTERNAL_RANGES = (
    ("10.0.0.0/8", "RFC 1918"),
    ("172.16.0.0/12", "RFC 1918; every Docker bridge on this box, and the gateway"),
    ("192.168.0.0/16", "RFC 1918"),
    ("100.64.0.0/10", "shared address space (RFC 6598), never public"),
    ("169.254.0.0/16", "link-local, including the metadata service at 169.254.169.254"),
    ("127.0.0.0/8", "loopback, should it ever be routed"),
)
SIGHT_HOST_TCP_PORTS = (80, 443)   # Caddy at the public address, by name
SIGHT_HOST_UDP_PORTS = (443,)      # HTTP/3, same
SIGHT_WALL_CHAIN = "LOAM-SIGHT"
SIGHT_WALL_HOST_CHAIN = "LOAM-SIGHT-HOST"
SIGHT_WALL_SERVICE = "loam-sight-firewall.service"
_REJECT = "-j REJECT --reject-with icmp-admin-prohibited"

# The private static host for "local files": a persona writes a file under
# this directory (the commons, writable by every persona, expired after
# seven days like everything else there) and opens it in the browser as
# https://files.<domain>/<name>. The browser image blocks file:// unless
# told otherwise and has no path into this box anyway.
FILES_ROOT = AI_LABS / "commons" / "www"

# The agents' own sites (2026-11-08, the owner: behind the login agents stand
# things up freely, on subdomains of their own). One TOML file per site,
# written by bin/site and nothing else, outside the repo because a site is
# an agent's working state, not a decision of the runtime's. Read here by
# the generator and by check-drift alike, so the render is the same for both.
SITES_DIR = lc.STATE / "sites"

# The one access-control rule that says two_factor, on a host that does not
# exist. Authelia 4.39.23 offers second-factor enrolment (Settings -> Two-
# Factor Authentication: the WebAuthn Credentials and One-Time Password
# panels, with their Add buttons) only when at least one rule or the default
# policy is `two_factor`: `handler_configuration.go` returns an empty
# `available_methods` otherwise and the page says "There are no protected
# applications that require a second factor method". With every app at
# one_factor there was nowhere to enrol the passkey (found 2026-09-08).
#
# Apps stay at one_factor on purpose. A passkey login is a single factor in
# Authelia's counting ("The user will be prompted for their password by
# default if the request requires multi-factor authentication"), so a
# two_factor app would cost passkey AND password on every new device unless
# `experimental_enable_passkey_uv_two_factors` were set, which the docs mark
# unsupported and say a future version will refuse at startup. One tap per
# device per month is the promise, so the two_factor rule sits on this name
# instead. No Caddy block, no DNS beyond the wildcard, no certificate: a
# request to it fails the TLS handshake. A record may not take the name.
SECOND_FACTOR_HOST = "second-factor"

SHARE_COOKIE = "loam_share"
SHARE_COOKIE_MAX_AGE = 30 * 24 * 3600   # the deadline matcher bounds it anyway
EXPOSURES = ("private", "shared")
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
# A bare domain (`owner-site.example`) or a hostname under one. Lowercase
# only: Caddy and Authelia both match hosts case-insensitively, but the
# Caddyfile is diffed as text, so one spelling.
DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")


class DeployError(Exception):
    """A record or a live file is wrong in a way that must not be rendered."""


# ---------------------------------------------------------------------------
# An install's edge (first release L12, 2026-11-14)
# ---------------------------------------------------------------------------
#
# A fresh install is one person's name, `<name>.<base domain>`, with its app
# at that name itself. Its `[edge]` table in /etc/loam/loam.toml says so, and
# a box without the table (the first install) renders exactly what it rendered before
# the table existed:
#
#   [edge]
#   name = "sam"                        # the label the installer was given
#   domain = "sam.example.com"          # every host is under this
#   address = "203.0.113.7"             # the box's public IPv4: DNS must say it
#   app = "loam-ui"                     # the record served at the domain itself
#   app_checkout = "@ROOT@"             # what it builds from, and the subfolder
#   app_context = "app"                 #   of that tree that is the build context
#   apps = ["loam-ui", "files", "vault"]   # the records this install serves
#   resolver = "public"                 # how DNS is asked: "public" | "system"
#   tls = "acme"                        # "internal": Caddy's own CA (rehearsals)
#   dns_gate = true                     # no site block until the name resolves
#
# Three things follow from the table and nothing else does:
# - **No site block before DNS.** Until loam-await-dns has seen the name and
#   `probe.<name>` resolve to `address` and written EDGE_STATE, the Caddyfile
#   carries no host at all: Caddy would ask Let's Encrypt for every name it
#   loads and fail, and failed validations are limited per host per hour.
# - **The claim.** Until CLAIMED_FILE exists, the login has no user, and the
#   app's host answers `/claim` from the claim service, outside the login,
#   and sends every other path there. The claim writes CLAIMED_FILE and
#   deletes the token (CLAIM_FILE).
# - **The relay.** `/_loam/login-code` on the app's host goes through the
#   gate to the same service, which hands the signed-in owner the one-time
#   code the login has just written to its file notifier (deploy/README.md,
#   "The claim and the relay").
EDGE: dict = lc.get("edge") or {}
EDGE_STATE = Path("/var/lib/loam/edge.json")     # root's; loam-await-dns writes it
CLAIM_FILE = ETC / "claim"                       # root-only: the claim's token
CLAIMED_FILE = ETC / "claimed"                   # who claimed the install, and when
CLAIM_PORT = 8934                                # the claim service, loopback
CLAIM_PATH = "/claim"
RELAY_PATH = "/_loam/login-code"


def edge_state() -> dict:
    """What loam-await-dns last found ({"dns": "resolved", ...}), or {}."""
    try:
        data = json.loads(EDGE_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def edge_ready() -> bool:
    """Whether host blocks may be rendered: always on a box without the
    gate; on an install, once its name has been seen resolving here."""
    if not EDGE.get("dns_gate"):
        return True
    state = edge_state()
    return state.get("dns") == "resolved" and state.get("domain") == EDGE.get("domain")


def unclaimed() -> bool:
    """An install whose login has no user yet: nobody has claimed it. Only
    an install with an app at its domain is ever unclaimed."""
    return bool(EDGE.get("app")) and not CLAIMED_FILE.exists()




# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def config() -> dict:
    """The domain and the portal's label. An install's `[edge]` table in
    /etc/loam/loam.toml names its own (`edge.domain`, first release L12);
    without one, deploy/loam.toml does, as it always has."""
    if EDGE.get("domain"):
        data = {"domain": EDGE["domain"], "auth_host": EDGE.get("auth_host", "auth")}
        where = lc.CONFIG_FILE
    else:
        data = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        where = CONFIG_PATH
    domain = data.get("domain", "")
    if not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", domain):
        raise DeployError(f"{where}: domain {domain!r} is not a bare domain")
    data.setdefault("auth_host", "auth")
    return data


def domain() -> str:
    return config()["domain"]


# ---------------------------------------------------------------------------
# Old names (2026-11-15, first release L14)
# ---------------------------------------------------------------------------
#
# When the domain changes, the names under the old one can keep answering for
# a while with nothing but a redirect to the same label under the new one.
# deploy/loam.toml says which, and until when:
#
#   [moved]
#   from = "example.net"
#   until = 2026-12-15
#   hosts = ["auth", "files", "vault", "loam-ui", ...]
#
# Each host renders one Caddy block with no login (it serves no byte of its
# own, only `redir ... 308`, path and query kept) and its own certificate.
# The apex app's old host goes to the domain itself. Each name also needs
# its own DNS record once the old wildcard is gone, so the list is written
# out, never derived: a name added here without a record would fail Let's
# Encrypt on every load. Past `until` the blocks still render, and
# check-drift says the redirects are due to be removed (the table here and
# their DNS records), because dropping them on a date by itself would take
# names away with nobody looking.

def moved() -> dict | None:
    """{"from", "until", "hosts"} from deploy/loam.toml's `[moved]`, or None.
    An install's edge has no old names."""
    if EDGE.get("domain") or not CONFIG_PATH.is_file():
        return None
    raw = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8")).get("moved")
    if raw is None:
        return None
    where = f"{CONFIG_PATH}: [moved]"
    if not isinstance(raw, dict):
        raise DeployError(f"{where} is a table")
    old, until, hosts = raw.get("from"), raw.get("until"), raw.get("hosts")
    dom = domain()
    if not isinstance(old, str) or not DOMAIN_RE.match(old) or old == dom:
        raise DeployError(f"{where}: from {old!r} is not a bare domain other than {dom}")
    if isinstance(until, dt.datetime):
        until = until.date()
    if not isinstance(until, dt.date):
        raise DeployError(f"{where}: until must be a TOML date (YYYY-MM-DD)")
    if (not isinstance(hosts, list) or not hosts
            or not all(isinstance(h, str) and NAME_RE.match(h) for h in hosts)
            or len(set(hosts)) != len(hosts)):
        raise DeployError(f"{where}: hosts is a list of distinct labels")
    # A label whose old name is the new domain, or a name under it, would
    # redirect a live host to itself.
    for h in hosts:
        if dom == f"{h}.{old}" or dom.endswith(f".{h}.{old}"):
            raise DeployError(f"{where}: {h}.{old} is {dom} or under it")
    return {"from": old, "until": until, "hosts": tuple(hosts)}


def moved_targets(apps: list[App] | None = None) -> list[tuple[str, str]]:
    """(old host, new origin) for every old name, in the table's order."""
    m = moved()
    if not m:
        return []
    apps = records() if apps is None else apps
    dom = domain()
    apex_host = next((a.host for a in apps if a.apex), None)
    return [(f"{h}.{m['from']}", f"https://{dom if h == apex_host else f'{h}.{dom}'}")
            for h in m["hosts"]]


def moved_overdue(today: dt.date | None = None) -> dt.date | None:
    """The `until` date once it has passed, else None."""
    m = moved()
    today = today or dt.date.today()
    return m["until"] if m and today > m["until"] else None


def caddy_moved_block(old: str, new: str, until: dt.date) -> str:
    """One old name: its access log and the redirect, nothing else."""
    return "\n".join([
        f"# MOVED: {old} is now {new.removeprefix('https://')}. No gate: this block",
        f"# serves only the redirect. Remove after {until.isoformat()} ([moved] in deploy/loam.toml).",
        f"{old} {{",
        access_log_line(old),
        f"\tredir {new}{{uri}} 308",
        "}",
    ]) + "\n"


# An old name's paths can outlive `[moved]` (2026-11-15, first release
# L15). A GitHub App's callback and setup URLs have no API, so an app
# registered under the old domain calls back there until its owner types the
# new URLs on GitHub. `[[forward]]` keeps those paths answering after the
# 30 days are up:
#
#   [[forward]]
#   host = "loam-ui.example.net"
#   paths = ["/integrations/github/*"]
#   why = "my-loam-app's callback and setup URLs name the old host"
#
# While `[moved]` still lists the host, its whole-host redirect already covers
# these paths and nothing more renders. Once `[moved]` is gone (or lists it no
# longer), the host renders one ungated block: the paths 308 to the same path
# on `to` (the domain itself unless it says), and everything else 404s. Its
# DNS record stays; check-drift's "due to go" list leaves it out.

def forwards() -> list[dict]:
    """[{"host", "paths", "to", "why"}] from deploy/loam.toml's
    `[[forward]]`, or [] (an install's edge has none)."""
    if EDGE.get("domain") or not CONFIG_PATH.is_file():
        return []
    raw = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8")).get("forward")
    if raw is None:
        return []
    where = f"{CONFIG_PATH}: [[forward]]"
    if not isinstance(raw, list):
        raise DeployError(f"{where} is an array of tables")
    dom, out, seen = domain(), [], set()
    for f in raw:
        host, paths = (f or {}).get("host"), (f or {}).get("paths")
        to, why = f.get("to", dom), f.get("why")
        if not isinstance(host, str) or not DOMAIN_RE.match(host) or host in seen:
            raise DeployError(f"{where}: host {host!r} is a distinct hostname")
        if host == dom or host.endswith("." + dom):
            raise DeployError(f"{where}: {host} is under {dom}, which serves itself")
        if (not isinstance(paths, list) or not paths or not all(
                isinstance(p, str) and re.fullmatch(r"/[A-Za-z0-9._~/-]*\*?", p)
                for p in paths)):
            raise DeployError(f"{where}: {host}: paths is a list like [\"/a/b/*\"]")
        if not isinstance(to, str) or not DOMAIN_RE.match(to):
            raise DeployError(f"{where}: {host}: to {to!r} is not a hostname")
        if not isinstance(why, str) or not why.strip():
            raise DeployError(f"{where}: {host}: why says what still calls it")
        seen.add(host)
        out.append({"host": host, "paths": tuple(paths), "to": to, "why": why.strip()})
    return out


def forward_hosts() -> set[str]:
    """Every `[[forward]]` host, rendered on its own or inside `[moved]`."""
    return {f["host"] for f in forwards()}


def caddy_forward_block(f: dict) -> str:
    """One old host kept for some paths: those redirect, the rest 404."""
    return "\n".join([
        f"# FORWARD: {f['host']} {' '.join(f['paths'])} to {f['to']}. No gate: this",
        f"# block serves only the redirect ([[forward]] in deploy/loam.toml). {f['why']}",
        f"{f['host']} {{",
        access_log_line(f["host"]),
        f"\t@forward path {' '.join(f['paths'])}",
        f"\tredir @forward https://{f['to']}{{uri}} 308",
        "\trespond 404",
        "}",
    ]) + "\n"


def read_root_text(path: Path) -> str | None:
    """A file that may be root-only. Direct when possible, `sudo -n cat`
    otherwise, None when absent either way."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except PermissionError:
        pass
    run = subprocess.run(["sudo", "-n", "cat", str(path)],
                         capture_output=True, text=True, check=False)
    if run.returncode != 0:
        if "No such file" in run.stderr:
            return None
        raise DeployError(f"cannot read {path}: {run.stderr.strip()}")
    return run.stdout


def public_names() -> set[str]:
    """The operator file: one app name per line, `#` comments. Absent means
    nothing is public, which is also what an empty file means."""
    text = read_root_text(PUBLIC_FILE)
    if text is None:
        return set()
    names = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.add(line)
    return names


def share_token(name: str) -> str | None:
    text = read_root_text(SHARE_DIR / f"{name}.token")
    token = (text or "").strip()
    return token or None


@dataclass
class App:
    name: str
    exposure: str
    host: str
    policy: str                       # Authelia policy for the host
    port: int | None                  # loopback publish, from compose.yaml
    static_root: Path | None          # a directory instead of a container
    share_expires: dt.date | None     # from the record; the token is elsewhere
    compose: Path | None
    checkout: Path | None             # the repo bin/deploy builds from
    hide: tuple[str, ...] = ()        # static host: names file_server pretends are absent
    # {container name: the Docker networks that service joins}, from the
    # compose file, resolved to real names. Empty for a static host, which
    # has no container. Read by check-drift against the running box.
    containers: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Hostnames on a domain of the app's own: cookie domain -> hosts under
    # it, from `[domains."<domain>"] hosts = [...]`. Each renders as one
    # more host block on the same upstream, gated the same way.
    domains: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # (method, path) pairs that pass the login, from `[[bypass]]`. Each is
    # one exact request; see _bypasses.
    bypass: tuple[tuple[str, str], ...] = ()
    # The label of the app's content host, from `content_host`; see
    # caddy_content_block. None for an app without one.
    content_host: str | None = None
    record: dict = field(default_factory=dict)
    # An agent's own site (2026-11-08), from `state/sites/<name>.toml`
    # rather than a deploy record; see sites(). `site_owner` is the agent
    # that stood it up, `site_expires` the last day it answers (UTC), and
    # `site_public` the owner's yes, quoted, when they have said one. None on
    # every deployment record.
    site_owner: str | None = None
    site_expires: dt.date | None = None
    site_public: str | None = None
    # `app_shell = true` in a record (2026-11-12): this record serves Loam's
    # own page (loam-ui, and its staging copy). Two things follow and
    # nothing else: its origin is one an agent's static site may be framed
    # by (frame_ancestors), and the Apps list leaves it out (lib/loam_apps).
    app_shell: bool = False
    # The app served at the domain itself: an install's `edge.app`, or a
    # record's own `apex = true` on a box without an edge (the first install, since
    # 2026-11-15). An install's may build from a subfolder of its checkout
    # (`edge.app_context`).
    apex: bool = False
    context: str | None = None

    @property
    def fqdn(self) -> str:
        return domain() if self.apex else f"{self.host}.{domain()}"

    @property
    def content_fqdn(self) -> str | None:
        return f"{self.content_host}.{domain()}" if self.content_host else None

    @property
    def extra_hosts(self) -> list[str]:
        """Every hostname from `domains`, in record order."""
        return [h for hosts in self.domains.values() for h in hosts]

    @property
    def hostnames(self) -> list[str]:
        """The app's own host under the runtime's domain, then the extras."""
        return [self.fqdn, *self.extra_hosts]

    @property
    def backend(self) -> str:
        if self.static_root is not None:
            lines = [f"root * {self.static_root}",
                     "file_server browse" + (" {" if self.hide else "")]
            if self.hide:
                # A bare name (no slash) hides every entry of that name at
                # any depth; a name with a slash is a path under the root.
                lines += [f"\thide {' '.join(self.hide)}", "}"]
            return "\n\t\t".join(lines)
        return f"reverse_proxy 127.0.0.1:{self.port}"

    def share_active(self, today: dt.date) -> tuple[str, dt.date] | None:
        """(token, expires) when the record says shared, the date has not
        passed, and a token has been minted. Otherwise None, and the host is
        rendered private -- an expired or unminted share is not a hole."""
        if self.exposure != "shared" or self.share_expires is None:
            return None
        if self.share_expires < today:
            return None
        token = share_token(self.name)
        if not token:
            return None
        return token, self.share_expires


# Compose keys that hand a container the box. bin/deploy runs an app's
# compose file as root on a project's say-so, so the file is checked here,
# by the generator and by check-drift, before anything is started.
FORBIDDEN_SERVICE_KEYS = ("privileged", "cap_add", "devices", "sysctls",
                          "userns_mode", "cgroup", "ipc")

# Where an app container's stdout goes, which is where the app's own request
# lines go. Every service of every app's compose file carries exactly this
# block; the generator refuses one that does not, and check-drift asks the
# running container, so the constant here and the copy in each compose file
# are the two homes and the drift check is the proof they agree.
#
# journald and not the daemon's json-file default (added 2026-09-28). A
# json-file log lives inside the container and is deleted with it, and
# `bin/deploy <app> up <commit>` recreates the container on every new commit,
# so a json-file history ends at the last deploy -- whatever its size cap.
# journald keeps the lines across a restart AND a redeploy, under the box's
# journal and its retention (deploy/README.md, docs/logs.md). The tag is the
# container's name, so `journalctl -t <app>` reads one app; `docker logs`
# and `bin/deploy <app> logs` still work, from Docker's local read cache.
APP_LOGGING = {"driver": "journald", "options": {"tag": "{{.Name}}"}}

# The runtime's own two containers, by the name each compose project and each
# container carries. One home for each: `authelia_compose` and
# `browser_compose` below render from these, and so does the table beneath
# them, so a rename cannot leave the drift check looking for the old name.
AUTHELIA_PROJECT = "loam-authelia"
AUTHELIA_REDIS = f"{AUTHELIA_PROJECT}-redis"
BROWSER_PROJECT = "loam-browser"

# The runtime's own containers and the network each belongs on. Read with the
# app records below to answer one question of the running box: is anything on
# a network it has no record for? The login has no `networks:` in its compose
# file, so it is on that project's implicit default; the browser names its own.
RUNTIME_CONTAINER_NETWORKS = {
    AUTHELIA_PROJECT: (f"{AUTHELIA_PROJECT}_default",),
    AUTHELIA_REDIS: (f"{AUTHELIA_PROJECT}_default",),
    BROWSER_PROJECT: (SIGHT_NETWORK,),
}

# The networks the runtime owns and hands out. A container on one of these
# without a record saying so is the finding this pair exists for.
RUNTIME_NETWORKS = (SIGHT_NETWORK, loam_broker.NETWORK)


def _compose_networks(compose_path: Path, name: str, doc: dict) -> dict[str, tuple[str, ...]]:
    """{container: the real Docker networks that service joins}, having
    refused any network but the app's own `default` and the broker's.

    **Why this is a refusal and not a note** (added 2026-09-25, on a persona's
    brief). Caddy lets a request from the browser's subnet through a private
    host's gate -- the personas' sight cannot hold the owner's passkey, so the
    bypass is keyed on `loam-sight` and is exactly one container wide. It is
    one container wide because no other container joins that network, and
    until this function existed nothing checked: an app's compose file is in
    this repo, `bin/deploy` runs it as root, and a line joining `loam-sight`
    would have put the app's own container inside the bypass. Same shape for
    `loam-authelia_default`, where the login's own container lives.

    So the list is two long and both entries are earned: the app's own project
    default, which is the proxy's route to it, and `loam-broker`, which is the
    whole of how an app reaches the session broker (deploy/README.md, "How an
    app reaches the session broker"). Everything else -- a third network of
    the app's own included -- is refused by name, because "the two known
    networks" is the rule and an app with a bridge nobody has asked for is a
    shape to settle before it is running rather than after.

    **Aliases are resolved, not trusted.** A service's `networks:` entries are
    keys into the file's own top-level table, and that table decides what the
    network is really called: `name:` spells it out, `external: true` with no
    `name:` means the key IS the network's name (which is how a compose file
    joins something the runtime created), and anything else is Docker's
    `<project>_<key>`. So `default` can be pointed at `loam-sight` with three
    words, and checking the keys would have missed it. A network the file
    declares but no service joins is refused too: `docker compose up` creates
    or looks it up either way, and a declaration that reads as authorised is
    not a thing to leave lying in the file.
    """
    project = str(doc.get("name") or name)
    own_default = f"{project}_default"
    allowed = (own_default, loam_broker.NETWORK)
    declared = doc.get("networks") or {}
    if not isinstance(declared, dict):
        raise DeployError(f"{compose_path}: top-level `networks` is a table of "
                          f"<key>: <definition> entries")

    def real(alias: str) -> str:
        spec = declared.get(alias) or {}
        if not isinstance(spec, dict):
            spec = {}
        if spec.get("name"):
            return str(spec["name"])
        if spec.get("external"):
            return str(alias)       # external and unnamed: the key is the name
        return f"{project}_{alias}"

    def refuse(where: str, alias: str, net: str) -> None:
        raise DeployError(
            f"{compose_path}: {where} joins network {net!r}"
            + (f" (`{alias}`)" if net != alias else "")
            + f". An app joins {own_default} — its own `default`, which is the "
            f"proxy's route to it — and {loam_broker.NETWORK}, and nothing "
            f"else. {SIGHT_NETWORK} is the personas' browser's, and Caddy lets "
            f"that subnet past a private host's login; a second container on "
            f"it widens the one bypass this box has.")

    containers: dict[str, tuple[str, ...]] = {}
    for svc_name, svc in (doc.get("services") or {}).items():
        aliases = svc.get("networks")
        if aliases is None:
            joins = [("default", own_default)]   # no `networks:` is the default
        elif isinstance(aliases, (list, dict)):
            joins = [(str(a), real(str(a))) for a in aliases]
        else:
            raise DeployError(f"{compose_path}: service {svc_name} has "
                              f"`networks: {aliases!r}`; it is a list of keys")
        for alias, net in joins:
            if net not in allowed:
                refuse(f"service {svc_name}", alias, net)
        container = str(svc.get("container_name") or f"{project}-{svc_name}-1")
        containers[container] = tuple(dict.fromkeys(net for _, net in joins))
    for alias in declared:
        net = real(str(alias))
        if net not in allowed:
            refuse(f"`networks.{alias}`", str(alias), net)
    return containers


def _compose_doc(compose_path: Path) -> dict:
    """The compose file, parsed once and handed to both checks below. Read in
    one place because a file parsed twice is a file that can be edited between
    the two reads."""
    doc = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
    if not (doc.get("services") or {}):
        raise DeployError(f"{compose_path}: no services")
    return doc


def _loopback_port(compose_path: Path, name: str, doc: dict) -> int:
    """The one loopback publish an app's compose file declares, refusing
    anything that would leave the box, mount a home directory, or give the
    container more than a container gets."""
    services = doc.get("services") or {}
    ports: list[int] = []
    for svc_name, svc in services.items():
        if svc.get("logging") != APP_LOGGING:
            raise DeployError(
                f"{compose_path}: service {svc_name} has logging "
                f"{svc.get('logging')!r}; every app service carries exactly "
                f"{APP_LOGGING!r} (lib/loam_deploy.APP_LOGGING), so its stdout "
                f"outlives a redeploy")
        for key in FORBIDDEN_SERVICE_KEYS:
            if svc.get(key):
                raise DeployError(
                    f"{compose_path}: service {svc_name} sets {key!r}. An app "
                    f"gets a container and nothing of the box")
        for key in ("network_mode", "pid"):
            if str(svc.get(key, "")).startswith(("host", "container:")):
                raise DeployError(
                    f"{compose_path}: service {svc_name} sets {key}: "
                    f"{svc[key]!r}; an app stays in its own namespaces")
        for entry in svc.get("ports") or []:
            if isinstance(entry, dict):
                host_ip, published = entry.get("host_ip"), str(entry.get("published", ""))
            else:
                parts = str(entry).split(":")
                if len(parts) != 3:
                    raise DeployError(
                        f"{compose_path}: service {svc_name} publishes {entry!r}; "
                        f"the form is 127.0.0.1:<port>:<port>, address included")
                host_ip, published = parts[0], parts[1]
            if host_ip != "127.0.0.1":
                raise DeployError(
                    f"{compose_path}: service {svc_name} publishes on "
                    f"{host_ip or '0.0.0.0'}; only 127.0.0.1 leaves the proxy "
                    f"as the box's one listener")
            ports.append(int(published.split("/")[0]))
        for vol in svc.get("volumes") or []:
            src = vol.get("source", "") if isinstance(vol, dict) else str(vol).split(":")[0]
            typ = vol.get("type", "") if isinstance(vol, dict) else ""
            if typ == "bind" or src.startswith(("/", ".", "~")):
                raise DeployError(
                    f"{compose_path}: service {svc_name} bind-mounts {src!r}. "
                    f"App data is a named volume, never a path on the box "
                    f"(and never the Docker socket)")
    if len(ports) != 1:
        raise DeployError(f"{compose_path}: {len(ports)} loopback publishes for "
                          f"{name}; the proxy needs exactly one")
    return ports[0]


def records() -> list[App]:
    """Every `deploy/<app>/deploy.toml`, sorted by name. Refuses to return
    anything it could not render safely."""
    apps: list[App] = []
    seen_hosts: dict[str, str] = {}
    seen_ports: dict[int, str] = {AUTHELIA_PORT: "authelia", BROWSER_PORT: "browser"}
    if not DEPLOY.is_dir():
        return apps
    served = EDGE.get("apps")
    for path in sorted(DEPLOY.glob("*/deploy.toml")):
        name = path.parent.name
        if not NAME_RE.match(name):
            raise DeployError(f"{path}: {name!r} is not a valid app name")
        if isinstance(served, list) and name not in served:
            continue   # an install serves the records its edge names, no others
        rec = tomllib.loads(path.read_text(encoding="utf-8"))
        exposure = rec.get("exposure")
        if exposure not in EXPOSURES:
            raise DeployError(
                f"{path}: exposure is {exposure!r}; it is one of "
                f"{' | '.join(EXPOSURES)} and there is no third value")
        host = rec.get("host", name)
        if not NAME_RE.match(host):
            raise DeployError(f"{path}: host {host!r} is not a valid label")
        if host == SECOND_FACTOR_HOST:
            raise DeployError(f"{path}: host {host!r} is reserved for the "
                              f"two_factor rule that unlocks passkey enrolment")
        policy = rec.get("policy", "one_factor")
        if policy not in ("one_factor", "two_factor"):
            raise DeployError(f"{path}: policy {policy!r}")
        static_root = rec.get("static_root")
        if isinstance(static_root, str):
            static_root = lc.expand(static_root)
        compose = path.parent / "compose.yaml"
        port = None
        containers: dict[str, tuple[str, ...]] = {}
        hide = rec.get("hide", [])
        if static_root is not None:
            static_root = Path(static_root)
            if not static_root.is_absolute():
                raise DeployError(f"{path}: static_root must be absolute")
            if (not isinstance(hide, list)
                    or not all(isinstance(h, str) and h and not h.isspace()
                               and not any(c in h for c in " \t\"{}") for h in hide)):
                raise DeployError(f"{path}: hide must be a list of names or "
                                  f"paths without spaces, braces or quotes")
        elif hide:
            raise DeployError(f"{path}: hide is for a static_root host only")
        elif compose.is_file():
            doc = _compose_doc(compose)
            port = _loopback_port(compose, name, doc)
            containers = _compose_networks(compose, name, doc)
        else:
            raise DeployError(f"{path}: neither compose.yaml nor static_root")
        share = rec.get("share") or {}
        expires = share.get("expires")
        if expires is not None and not isinstance(expires, dt.date):
            raise DeployError(f"{path}: share.expires must be a TOML date (YYYY-MM-DD)")
        if isinstance(expires, dt.datetime):
            expires = expires.date()
        if exposure == "shared" and expires is None:
            raise DeployError(f"{path}: shared without share.expires")
        if host in seen_hosts:
            raise DeployError(f"{path}: host {host!r} already used by {seen_hosts[host]}")
        if port is not None and port in seen_ports:
            raise DeployError(f"{path}: port {port} already used by {seen_ports[port]}")
        seen_hosts[host] = name
        if port is not None:
            seen_ports[port] = name
        domains = _extra_domains(path, rec.get("domains"), seen_hosts, name)
        bypass = _bypasses(path, rec.get("bypass"))
        app_shell = rec.get("app_shell", False)
        if not isinstance(app_shell, bool):
            raise DeployError(f"{path}: app_shell is true or false")
        if app_shell and port is None:
            raise DeployError(f"{path}: app_shell is for the app's own container, "
                              f"not a static host")
        content_host = rec.get("content_host")
        if content_host is not None:
            if not isinstance(content_host, str) or not NAME_RE.match(content_host):
                raise DeployError(f"{path}: content_host {content_host!r} is not a valid label")
            if port is None:
                raise DeployError(f"{path}: content_host is for an app with a "
                                  f"container; a static host has nothing to dispatch on it")
            if content_host == SECOND_FACTOR_HOST:
                raise DeployError(f"{path}: content_host {content_host!r} is reserved")
            if content_host in seen_hosts:
                raise DeployError(f"{path}: content_host {content_host!r} already "
                                  f"used by {seen_hosts[content_host]}")
            seen_hosts[content_host] = name
        if _lands_by_pull_request(name):
            raise DeployError(
                f"{path}: {name} lands by pull request on a repository Loam does "
                f"not own, and its owners deploy it their own way; it has no "
                f"deploy lane here")
        # Where bin/deploy builds from: the record may say, and otherwise it
        # is the project engineer's checkout from the registry. A static host
        # has no checkout and no build.
        # The app at the domain itself: an install's `edge.app` names it, and
        # where there is no edge (the first install, since 2026-11-15, L14) the
        # record says `apex = true`. With an edge, the edge decides alone.
        rec_apex = rec.get("apex", False)
        if not isinstance(rec_apex, bool):
            raise DeployError(f"{path}: apex is true or false")
        apex = (EDGE["app"] == name) if EDGE.get("app") else rec_apex
        context = EDGE.get("app_context") if apex else None
        checkout = (EDGE.get("app_checkout") if apex else None) or rec.get("checkout")
        if checkout is not None:
            checkout = Path(lc.expand(checkout))
            if not checkout.is_absolute():
                raise DeployError(f"{path}: checkout must be absolute")
        elif port is not None:
            checkout = _registered_checkout(name)
        if context is not None and (not isinstance(context, str)
                                    or not re.fullmatch(r"[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*", context)
                                    or ".." in context.split("/")):
            raise DeployError(f"{lc.CONFIG_FILE}: edge.app_context {context!r} is "
                              f"not a relative folder of the checkout")
        apps.append(App(name=name, exposure=exposure, host=host, policy=policy,
                        port=port, static_root=static_root, share_expires=expires,
                        compose=compose if compose.is_file() else None,
                        checkout=checkout, hide=tuple(hide), domains=domains,
                        containers=containers, bypass=bypass,
                        content_host=content_host, record=rec,
                        app_shell=app_shell, apex=apex, context=context))
    at_apex = [a.name for a in apps if a.apex]
    if len(at_apex) > 1:
        raise DeployError(f"{DEPLOY}: {' and '.join(at_apex)} both say apex = true; "
                          f"one record is served at {domain()} itself")
    return apps


def frame_ancestors(apps: list[App] | None = None) -> tuple[str, ...]:
    """The origins an agent's static site may be framed by: the page's own,
    every record that says `app_shell`, on its host under the runtime's
    domain (2026-11-12, for the Apps section opening a site in the main
    pane). Sorted, so the render is deterministic."""
    apps = records() if apps is None else apps
    return tuple(sorted(f"https://{a.fqdn}" for a in apps if a.app_shell))


def frame_header(origins: tuple[str, ...]) -> str:
    """The one header line a static site's block carries: framing by the
    site itself and by the page's origins, by nobody else. Caddy is the
    whole server of a static site and sends no other CSP, so this policy is
    the only one and restricts nothing but framing. A site that proxies to a
    port is left to its own container's headers, because a second CSP from
    here would override a container's own X-Frame-Options in every modern
    browser (frame-ancestors wins over it), which widens what it asked for."""
    value = " ".join(["frame-ancestors 'self'", *origins])
    return f'\theader Content-Security-Policy "{value}"'


# A site's static root goes into the Caddyfile as one token; nothing in it
# may need quoting, and the file server hides these at any depth.
SITE_ROOT_RE = re.compile(r"^/[A-Za-z0-9._/@+-]*$")
SITE_HIDE = (".git", ".env")
SITE_PORTS = range(1024, 65536)


def sites(apps: list[App] | None = None) -> list[App]:
    """The agents' own sites, `state/sites/<name>.toml`, as Apps sorted by
    name. Private unless the record carries `public`, which bin/site writes
    only with the owner's yes quoted in it: forgetting a line renders a gated
    host, because there is no line whose absence opens one. A record that
    collides with a deployment record, a reserved name or another site is
    refused, and so is the whole render: a bad site is a generator error, as
    a bad deployment record is, so check-drift names it.

        owner = "ada"                   # who stood it up
        root = "/abs/dir"               # a directory Caddy serves; or
        port = 8090                     # a loopback port Caddy proxies to
        expires = 2026-11-09            # optional: gone after this date, UTC
        public = "Sam, 2026-11-08, in chat: \"yes\""   # optional
    """
    apps = records() if apps is None else apps
    out: list[App] = []
    if not SITES_DIR.is_dir():
        return out
    cfg = config()
    # A record's name too, not only its host: the operator's public file
    # names records, and a site must never answer to a name in it.
    taken = {a.name: a.name for a in apps} | {a.host: a.name for a in apps}
    taken |= {a.content_host: a.name for a in apps if a.content_host}
    taken |= {cfg["auth_host"]: "the login portal",
              SECOND_FACTOR_HOST: "the two_factor rule"}
    ports = {a.port: a.name for a in apps if a.port is not None}
    ports |= {AUTHELIA_PORT: "authelia", BROWSER_PORT: "browser",
              loam_broker.PORT: "the session broker", CLAIM_PORT: "the claim service"}
    for path in sorted(SITES_DIR.glob("*.toml")):
        name = path.stem
        if not NAME_RE.match(name):
            raise DeployError(f"{path}: {name!r} is not a valid site name")
        if name in taken:
            raise DeployError(f"{path}: {name}.{cfg['domain']} is already {taken[name]}")
        try:
            rec = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise DeployError(f"{path}: {exc}") from exc
        owner = rec.get("owner")
        if not isinstance(owner, str) or not NAME_RE.match(owner):
            raise DeployError(f"{path}: owner {owner!r} is not an agent name")
        root, port = rec.get("root"), rec.get("port")
        if (root is None) == (port is None):
            raise DeployError(f"{path}: a site has exactly one of root or port")
        if root is not None:
            if not isinstance(root, str) or not SITE_ROOT_RE.match(root) or ".." in root.split("/"):
                raise DeployError(f"{path}: root {root!r} must be an absolute path "
                                  f"of letters, digits and ._/@+- only")
            root = Path(root)
        if port is not None:
            if not isinstance(port, int) or isinstance(port, bool) or port not in SITE_PORTS:
                raise DeployError(f"{path}: port {port!r} is not a port 1024-65535")
            if port in ports:
                raise DeployError(f"{path}: port {port} is {ports[port]}'s")
            ports[port] = name
        expires = rec.get("expires")
        if isinstance(expires, dt.datetime):
            expires = expires.date()
        if expires is not None and not isinstance(expires, dt.date):
            raise DeployError(f"{path}: expires must be a TOML date (YYYY-MM-DD)")
        public = rec.get("public")
        if public is not None and (not isinstance(public, str) or not public.strip()):
            raise DeployError(f"{path}: public, when present, is {lc.OWNER_NAME}'s yes quoted; "
                              f"an empty one is not a yes")
        taken[name] = f"the site {path.name}"
        out.append(App(name=name, exposure="private", host=name, policy="one_factor",
                       port=port, static_root=root, share_expires=None,
                       compose=None, checkout=None,
                       hide=SITE_HIDE if root is not None else (),
                       record=rec, site_owner=owner, site_expires=expires,
                       site_public=public))
    return out


# A bypass path: slash-separated segments of letters, digits, `_` and `-`.
# No dot (so no `..`), no `%`, no query, no trailing slash, and nothing a
# Caddy expression string would need escaped.
BYPASS_PATH_RE = re.compile(r"^(/[A-Za-z0-9_-]+)+$")
BYPASS_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE")


def _bypasses(path: Path, raw) -> tuple[tuple[str, str], ...]:
    """`[[bypass]]` entries as (method, path), refusing anything but one
    method and one literal path per entry."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise DeployError(f"{path}: bypass must be an array of tables ([[bypass]])")
    out: list[tuple[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) != {"method", "path"}:
            raise DeployError(f"{path}: each [[bypass]] has exactly method and path")
        method, p = entry["method"], entry["path"]
        if method not in BYPASS_METHODS:
            raise DeployError(f"{path}: bypass method {method!r} is not one of "
                              f"{' | '.join(BYPASS_METHODS)}")
        if not isinstance(p, str) or not BYPASS_PATH_RE.match(p):
            raise DeployError(f"{path}: bypass path {p!r} must be a literal path like "
                              f"/api/x: segments of letters, digits, _ and -, no "
                              f"query, no dots, no trailing slash")
        if (method, p) in out:
            raise DeployError(f"{path}: bypass {method} {p} is listed twice")
        out.append((method, p))
    return tuple(out)


def _extra_domains(path: Path, raw, seen_hosts: dict[str, str],
                   name: str) -> dict[str, tuple[str, ...]]:
    """`[domains."<domain>"] hosts = [...]`, validated. The key is the cookie
    domain Authelia scopes a session to; every host is that domain or a name
    under it. `<auth_host>.<domain>` is the portal's and may not be listed.
    Hostnames are unique across every record, primary hosts included, and
    `seen_hosts` is keyed on the full name for the extras so the two
    namespaces cannot collide either."""
    if raw is None:
        return {}
    if not isinstance(raw, dict) or not raw:
        raise DeployError(f"{path}: domains is a table of "
                          f"[domains.\"<domain>\"] hosts = [...] entries")
    cfg = config()
    primary, auth = cfg["domain"], cfg["auth_host"]
    out: dict[str, tuple[str, ...]] = {}
    for dom, entry in raw.items():
        if not DOMAIN_RE.match(dom):
            raise DeployError(f"{path}: domains.{dom!r} is not a bare domain")
        if dom == primary or dom.endswith("." + primary):
            raise DeployError(f"{path}: domains.{dom!r} is under the runtime's "
                              f"own domain {primary}; use `host = ` for that")
        hosts = (entry or {}).get("hosts") if isinstance(entry, dict) else None
        if (not isinstance(hosts, list) or not hosts
                or not all(isinstance(h, str) for h in hosts)):
            raise DeployError(f"{path}: domains.{dom!r} needs hosts = [\"<name>\", ...]")
        portal = f"{auth}.{dom}"
        for h in hosts:
            if not DOMAIN_RE.match(h) or not (h == dom or h.endswith("." + dom)):
                raise DeployError(f"{path}: host {h!r} is not {dom} or a name under it")
            if h == portal:
                raise DeployError(f"{path}: host {h!r} is reserved for the login "
                                  f"portal on {dom}")
            if h in seen_hosts:
                raise DeployError(f"{path}: host {h!r} already used by {seen_hosts[h]}")
            seen_hosts[h] = name
        out[dom] = tuple(hosts)
    return out


def extra_domains(apps: list[App] | None = None) -> list[str]:
    """Every cookie domain any record adds, sorted. Each gets a portal block
    in the Caddyfile and a cookie entry in Authelia's session config."""
    apps = records() if apps is None else apps
    return sorted({dom for a in apps for dom in a.domains})


def box_addresses() -> set[str]:
    """This box's global-scope IPv4 addresses, from the kernel."""
    run = subprocess.run(["ip", "-j", "-4", "addr", "show", "scope", "global"],
                         capture_output=True, text=True, check=False)
    if run.returncode != 0:
        return set()
    try:
        return {a["local"] for i in json.loads(run.stdout) for a in i.get("addr_info", [])}
    except (ValueError, KeyError, TypeError):
        return set()


def unresolved_hosts(apps: list[App] | None = None) -> list[tuple[str, str]]:
    """(hostname, why) for every extra host, and every extra domain's portal,
    that does not resolve to this box. Caddy asks for a certificate for
    every host block it loads and a name that does not resolve fails the
    challenge, retried with backoff and logged; the generator warns rather
    than refuses, because a DNS record landing later needs no regenerate.
    The runtime's own domain is not checked: it is wildcarded here."""
    import socket
    apps = records() if apps is None else apps
    here = box_addresses()
    auth = config()["auth_host"]
    names = []
    for app in apps:
        names += app.extra_hosts
    names += [f"{auth}.{dom}" for dom in extra_domains(apps)]
    out = []
    for name in names:
        try:
            addrs = {ai[4][0] for ai in socket.getaddrinfo(name, 443, socket.AF_INET)}
        except OSError:
            out.append((name, "does not resolve"))
            continue
        if here and not addrs & here:
            out.append((name, f"resolves to {', '.join(sorted(addrs))}, not this box"))
    return out


def _lands_by_pull_request(name: str) -> bool:
    """Whether agents/projects/<name>.json says `"landing": "pull_request"`
    (since 2026-10-06): such a project has no deploy lane."""
    path = AGENTS / "projects" / f"{name}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("landing") == "pull_request"
    except (OSError, ValueError, AttributeError):
        return False


def _registered_checkout(name: str) -> Path | None:
    """The project engineer's checkout for an app of the same name, from
    agents/projects/<name>.json, or None when there is no such engineer."""
    path = AGENTS / "projects" / f"{name}.json"
    if not path.is_file():
        return None
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DeployError(f"{path}: {exc}")
    checkout = rec.get("checkout")
    return Path(checkout) if checkout else None


def record_path(name: str) -> Path:
    return DEPLOY / name / "deploy.toml"


# ---------------------------------------------------------------------------
# The Caddyfile
# ---------------------------------------------------------------------------

GATE_SNIPPET = """\
# The login. Every private host imports this; the request goes to Authelia
# first and reaches the app only with a session.
(gate) {
	forward_auth 127.0.0.1:%d {
		uri /api/authz/forward-auth
		copy_headers Remote-User Remote-Groups Remote-Email Remote-Name
	}
}
""" % AUTHELIA_PORT

# The access log: every host block imports this with its own hostname, so
# each host writes one JSON file under one directory (added 2026-09-28, when
# an audit could not count loam-ui's errors because nothing logged a web
# request). Caddy rolls the file itself: a new file at ROLL_SIZE, at most
# ROLL_KEEP rolled files (gzipped), none older than ROLL_KEEP_FOR. So a host
# holds at most ~15 x 50 MiB before compression, and a rolled file older than
# 14 days is deleted whatever the size.
#
# What a line carries is Caddy's access record: method, host, uri, proto,
# remote and client address, the request headers, status, size, bytes read
# and duration. Never a body; Caddy does not log one. What is taken OUT, by
# the filter, is everything that can carry a credential:
#   - the query string, replaced by `?redacted`: a share link is
#     `/?share=<token>`, and a query is where any other token would ride;
#   - the segment after a leading `/content/`, replaced by `redacted`: on a
#     content host it is a signed read grant (2026-10-23, loam-ui's live
#     HTML), and the rest of the path stays so a line still says which file;
#   - Cookie, Authorization, Proxy-Authorization: the Authelia session, the
#     share cookie, any bearer. (Caddy already redacts these unless a server
#     sets `log_credentials`; deleted here so the log does not depend on it);
#   - Referer, because a page opened from a share link sends that link,
#     token and all, as the Referer of every request it makes after;
#   - every response header, because Set-Cookie is the session itself and
#     Location carries the login's `rd=` URL with its query.
# The user agent stays, as `request.headers.User-Agent`.
# Access lines go ONLY to these files: the adapter excludes them from the
# default logger, so none reach the journal.
ACCESS_LOG_DIR = Path("/var/log/caddy/access")
ACCESS_LOG_ROLL_SIZE = "50MiB"
ACCESS_LOG_ROLL_KEEP = 14
ACCESS_LOG_ROLL_KEEP_FOR = "336h"   # 14 days
ACCESS_LOG_SNIPPET = f"""\
# The access log. Every host block imports this with its own name: one JSON
# file per host, rolled by Caddy, credentials filtered out. See
# lib/loam_deploy.py for what each filter is for.
(access_log) {{
	log {{
		output file {ACCESS_LOG_DIR}/{{args[0]}}.log {{
			roll_size {ACCESS_LOG_ROLL_SIZE}
			roll_keep {ACCESS_LOG_ROLL_KEEP}
			roll_keep_for {ACCESS_LOG_ROLL_KEEP_FOR}
		}}
		format filter {{
			wrap json
			fields {{
				request>uri multi_regexp {{
					regexp ^/content/[^/?]* /content/redacted
					regexp \\?.* ?redacted
				}}
				request>headers>Cookie delete
				request>headers>Authorization delete
				request>headers>Proxy-Authorization delete
				request>headers>Referer delete
				resp_headers delete
			}}
		}}
	}}
}}
"""


def access_log_line(fqdn: str) -> str:
    return f"\timport access_log {fqdn}\n\timport compress"


# Compression (added 2026-11-09: loam-ui sent a 3.5 MB session list
# uncompressed to a phone that asked for zstd and gzip, and every request
# behind it queued). Every host imports it. The match list is Caddy's default
# with ONE type taken out: text/event-stream. Caddy's default matches text/*,
# which takes in an SSE stream, and a stream must reach the client event by
# event; an encoder that holds bytes until its buffer fills is a stalled
# stream. A matcher with several `header` lines on one field matches any.
COMPRESS_TYPES = (
    "application/json*", "application/javascript*", "application/xhtml+xml*",
    "application/atom+xml*", "application/rss+xml*", "application/wasm*",
    "application/xml*", "image/svg+xml*", "text/html*", "text/css*",
    "text/plain*", "text/javascript*", "text/markdown*", "text/xml*",
    "text/csv*",
)
COMPRESS_SNIPPET = "\n".join([
    "# Compression for every host, never for an event stream (lib/loam_deploy.py).",
    "(compress) {",
    "\tencode zstd gzip {",
    "\t\tmatch {",
    *(f"\t\t\theader Content-Type {t}" for t in COMPRESS_TYPES),
    "\t\t}",
    "\t}",
    "}",
    "",
])


def _deadline_unix(expires: dt.date) -> int:
    """The link works through the end of its expiry date, UTC."""
    end = dt.datetime.combine(expires + dt.timedelta(days=1), dt.time(0),
                              tzinfo=dt.timezone.utc)
    return int(end.timestamp())


def caddy_host_block(app: App, public: bool, today: dt.date,
                     fqdn: str | None = None,
                     framed_by: tuple[str, ...] | None = None) -> str:
    """One host block. `fqdn` names which of the app's hostnames; the body
    is the same for every one of them, so an extra host on the app's own
    domain is gated, shared, or public exactly as `<host>.<domain>` is.
    `framed_by`, for an agent's static site only, is the page's origins
    (frame_ancestors); None leaves framing to whatever the backend sends."""
    fq = fqdn or app.fqdn
    backend = app.backend
    if app.app_shell:
        # Caddy orders the two by their matchers, /x/* first, wherever each
        # handle carries them; the page's own proxy stays the fallback.
        backend = (f"reverse_proxy /x/* 127.0.0.1:{PLUGIN_GATEWAY_PORT}\n\t\t"
                   + backend)
    head: list[str] = []
    if framed_by is not None and app.site_owner and app.static_root is not None:
        head += ["\t# Framed by itself and Loam's page only; see lib/loam_deploy.frame_header.",
                 frame_header(framed_by)]
    if app.site_expires is not None:
        # Dead at the deadline whether or not the prune has run yet, so a
        # throwaway site does not outlive its date by a timer's lateness.
        head += [
            f"\t# Expires at the end of {app.site_expires.isoformat()} UTC; bin/site prune removes it.",
            f"\t@expired expression `int({{time.now.unix}}) >= {_deadline_unix(app.site_expires)}`",
            "\thandle @expired {",
            "\t\trespond \"expired\" 410",
            "\t}",
        ]
    if public:
        why = (f"{lc.OWNER_NAME}'s yes in the site record: {app.site_public}"
               if app.site_public else f"named in {PUBLIC_FILE} by the operator")
        why = " ".join(why.split())   # one comment line, whatever was quoted
        return "\n".join([f"# PUBLIC: {why}. No gate.", f"{fq} {{",
                          access_log_line(fq), *head, f"\t{backend}", "}"]) + "\n"
    lines = [f"{fq} {{", access_log_line(fq), *head]
    if app.apex:
        # Ahead of the browser's subnet on purpose: that subnet passes the
        # gate on every other path, and the code is the owner's alone.
        lines += [
            "\t# The login's one-time codes, to the signed-in owner only; see deploy/README.md.",
            f"\thandle {RELAY_PATH} {{",
            "\t\timport gate",
            f"\t\treverse_proxy 127.0.0.1:{CLAIM_PORT}",
            "\t}",
        ]
    lines += [
        "\t# The personas' browser, by its own network. See lib/loam_deploy.py.",
        f"\t@sight remote_ip {SIGHT_SUBNET}",
        "\thandle @sight {",
        f"\t\t{backend}",
        "\t}",
    ]
    share = app.share_active(today)
    if share:
        token, expires = share
        deadline = _deadline_unix(expires)
        lines += [
            f"\t# Shared until the end of {expires.isoformat()} UTC: the link carries the",
            "\t# token once, the cookie it sets carries it after. Revoked by regenerating",
            "\t# without the token; dead at the deadline whether or not anyone regenerates.",
            f"\t@share_link expression `{{http.request.uri.query.share}} == \"{token}\" && int({{time.now.unix}}) < {deadline}`",
            "\thandle @share_link {",
            f"\t\theader >Set-Cookie \"{SHARE_COOKIE}={token}; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age={SHARE_COOKIE_MAX_AGE}\"",
            f"\t\t{backend}",
            "\t}",
            f"\t@share_cookie expression `{{http.request.cookie.{SHARE_COOKIE}}} == \"{token}\" && int({{time.now.unix}}) < {deadline}`",
            "\thandle @share_cookie {",
            f"\t\t{backend}",
            "\t}",
        ]
    for i, (method, path) in enumerate(app.bypass):
        # The URI as the client sent it, compared whole: `/api/x?q`,
        # `/API/X` and `/api/y/../x` are not `/api/x` and meet the gate.
        lines += [
            f"\t# {method} {path} passes the login; the app checks its own credential there.",
            f"\t@bypass{i} expression `{{http.request.method}} == \"{method}\" && {{http.request.orig_uri}} == \"{path}\"`",
            f"\thandle @bypass{i} {{",
            f"\t\t{backend}",
            "\t}",
        ]
    lines += [
        "\thandle {",
        "\t\timport gate",
        f"\t\t{backend}",
        "\t}",
        "}",
    ]
    return "\n".join(lines) + "\n"


def caddy_content_block(app: App) -> str:
    """The app's content host: a second origin for bytes the app will not
    serve on its own (loam-ui's live HTML previews, 2026-10-23). Only
    `/content/*` reaches the app; anything else is a 404 here, before the
    login, so the host never shows the app's shell, API or assets. The Host
    goes through as the client sent it, because the app dispatches on it,
    and Caddy replaces any client X-Forwarded-Host with its own. Cookie and
    Authorization are stripped on the way up: the login's cookie is sent by
    the browser (it is a parent-domain cookie and the gate reads it) and
    goes no further than Authelia. No response here may set a cookie.

    Always gated, whatever the app's exposure: no share matcher (the share
    cookie is host-only and never arrives here) and no operator-public
    form. The browser's subnet passes as it does on every private host."""
    fq = app.content_fqdn
    proxy = "\n".join([
        f"reverse_proxy 127.0.0.1:{app.port} {{",
        "\t\t\theader_up Host {http.request.hostport}",
        "\t\t\theader_up -Cookie",
        "\t\t\theader_up -Authorization",
        "\t\t}",
    ])
    lines = [
        f"# {app.name}'s content host: /content/* only, no cookies either way.",
        f"{fq} {{",
        access_log_line(fq),
        "\theader {",
        "\t\t-Set-Cookie",
        "\t\tdefer",
        "\t}",
        "\t@not_content not path /content/*",
        "\thandle @not_content {",
        "\t\trespond \"not found\" 404",
        "\t}",
        "\t# The personas' browser, by its own network. See lib/loam_deploy.py.",
        f"\t@sight remote_ip {SIGHT_SUBNET}",
        "\thandle @sight {",
        f"\t\t{proxy}",
        "\t}",
        "\thandle {",
        "\t\timport gate",
        f"\t\t{proxy}",
        "\t}",
        "}",
    ]
    return "\n".join(lines) + "\n"


def caddy_claim_block(fq: str) -> str:
    """The app's host while the install has no login yet: `/claim` from the
    claim service, outside the login, and every other path sent there. The
    token rides in the link's fragment, which a browser never sends, so no
    log line carries it; the page posts it. Gone at the next render after
    the claim, which deletes CLAIM_FILE."""
    return "\n".join([
        f"# UNCLAIMED: no {CLAIMED_FILE} yet, so the login has no user. The claim",
        "# page answers outside the login until the person claims this install.",
        f"{fq} {{",
        access_log_line(fq),
        f"\thandle {CLAIM_PATH} {{",
        f"\t\treverse_proxy 127.0.0.1:{CLAIM_PORT}",
        "\t}",
        "\thandle {",
        f"\t\tredir * {CLAIM_PATH} 302",
        "\t}",
        "}",
    ]) + "\n"


def caddyfile(today: dt.date | None = None) -> str:
    """The whole live Caddyfile. Deterministic for a given day and set of
    inputs, which is what lets check-drift diff it."""
    today = today or dt.date.today()
    cfg = config()
    dom, auth = cfg["domain"], cfg["auth_host"]
    apps = records()
    public = public_names()
    unknown = public - {a.name for a in apps}
    out = [
        "# GENERATED by agents/bin/gen-deploy from agents/deploy/ and",
        f"# {PUBLIC_FILE}. Do not edit: a hand edit is drift and bin/check-drift",
        "# says so. One host block per app; every block is gated unless the",
        "# operator file names the app. Apps publish on loopback; this is the",
        "# box's only listener on the internet.",
        "",
    ]
    if EDGE.get("tls") == "internal":
        out += ["# A rehearsal install: certificates from Caddy's own authority, never",
                "# Let's Encrypt (edge.tls = \"internal\" in /etc/loam/loam.toml).",
                "{", "\tlocal_certs", "}", ""]
    out += [
        GATE_SNIPPET,
        ACCESS_LOG_SNIPPET,
        COMPRESS_SNIPPET,
    ]
    if not edge_ready():
        out += [f"# No host yet: {dom} does not resolve to this box. loam-await-dns",
                "# renders the hosts once it does; a block for a name that does not",
                "# resolve here would fail Let's Encrypt on every load.", ""]
        return "\n".join(out)
    out += [
        f"# The login portal.",
        f"{auth}.{dom} {{",
        access_log_line(f"{auth}.{dom}"),
        f"\treverse_proxy 127.0.0.1:{AUTHELIA_PORT}",
        "}",
        "",
    ]
    # One portal per extra domain: Authelia's session cookie is scoped to a
    # domain, so a gated host on another domain redirects to a portal on
    # that domain, and the cookie it sets is sent back to the host.
    for extra in extra_domains(apps):
        out += [f"# The login portal on {extra}, for the hosts below that carry it.",
                f"{auth}.{extra} {{",
                access_log_line(f"{auth}.{extra}"),
                f"\treverse_proxy 127.0.0.1:{AUTHELIA_PORT}",
                "}",
                ""]
    claim = unclaimed()
    for app in apps:
        for fq in app.hostnames:
            if claim and app.apex and fq == app.fqdn:
                out.append(caddy_claim_block(fq))
                continue
            out.append(caddy_host_block(app, app.name in public, today, fq))
        if app.content_host:
            out.append(caddy_content_block(app))
    agent_sites = sites(apps)
    if agent_sites:
        out.append(f"# The agents' own sites, from {SITES_DIR}/ (bin/site). "
                   f"Gated unless the\n# site's record carries {lc.OWNER_NAME}'s yes.\n")
    framed_by = frame_ancestors(apps)
    for site in agent_sites:
        out.append(caddy_host_block(site, bool(site.site_public), today,
                                    framed_by=framed_by))
    m = moved()
    moved_hosts = set()
    for old_host, new in moved_targets(apps):
        moved_hosts.add(old_host)
        out.append(caddy_moved_block(old_host, new, m["until"]))
    for f in forwards():
        if f["host"] not in moved_hosts:
            out.append(caddy_forward_block(f))
    if unknown:
        out.append(f"# {PUBLIC_FILE} names {', '.join(sorted(unknown))}, which "
                   f"no deployment record defines. Nothing emitted for them.\n")
    return "\n".join(out)


def live_host_blocks(text: str) -> list[tuple[str, bool]]:
    """(host, gated) for every site block of a Caddyfile, read the way a
    reviewer would: a block is gated if its body imports the gate or is the
    portal itself. Used by check-drift on the LIVE file, so it parses text
    rather than trusting the generator."""
    return [(host, "import gate" in body
             or f"reverse_proxy 127.0.0.1:{AUTHELIA_PORT}" in body)
            for host, body in _live_blocks(text)]


def live_hosts_unlogged(text: str) -> list[str]:
    """Every site block of a live Caddyfile that does not write its own
    access log: no `import access_log <its own host>` line, or no
    `(access_log)` snippet for it to import. Read from the text, like
    live_host_blocks, so a hand edit that drops a host's log is a finding."""
    has_snippet = re.search(r"^\(access_log\)\s*\{", text, re.M) is not None
    return [host for host, body in _live_blocks(text)
            if not has_snippet or f"import access_log {host}" not in body.splitlines()]


def _live_blocks(text: str) -> list[tuple[str, str]]:
    """(host, body) for every site block, the body stripped line by line."""
    blocks: list[tuple[str, str]] = []
    current: str | None = None
    depth = 0
    body: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if current is None:
            m = re.match(r"^([a-z0-9.-]+\.[a-z]{2,})\s*\{$", stripped)
            if m:
                current, depth, body = m.group(1), 1, []
            continue
        depth += stripped.count("{") - stripped.count("}")
        body.append(stripped)
        if depth <= 0:
            blocks.append((current, "\n".join(body)))
            current = None
    return blocks


# ---------------------------------------------------------------------------
# Docker's daemon.json
# ---------------------------------------------------------------------------

def daemon_json() -> str:
    """Hand-written on 2026-09-07, generated since 2026-09-08. The values
    and their reasons are in deploy/README.md; the one that carries the
    exposure story is `ip`: a port published without an address binds to
    loopback, so "nothing leaves the box" is the daemon's default."""
    return json.dumps({
        "ip": "127.0.0.1",
        "userns-remap": "default",
        "no-new-privileges": True,
        "live-restore": True,
        "log-driver": "json-file",
        "log-opts": {"max-size": "10m", "max-file": "3"},
    }, indent=2) + "\n"


# ---------------------------------------------------------------------------
# Authelia
# ---------------------------------------------------------------------------

def authelia_config() -> str:
    cfg = config()
    dom, auth = cfg["domain"], cfg["auth_host"]
    # First the sentinel that turns second-factor enrolment on (see
    # SECOND_FACTOR_HOST), then any record that asks for two_factor, then
    # every other host at one factor. A passkey login satisfies one_factor.
    rules = [{"domain": f"{SECOND_FACTOR_HOST}.{dom}", "policy": "two_factor"}]
    apps = records()
    for app in apps:
        if app.policy != "one_factor":
            rules.append({"domain": app.fqdn, "policy": app.policy})
    if any(a.apex for a in apps):
        # An install's app is its domain itself, which `*.<domain>` does not
        # cover: one rule names both.
        rules.append({"domain": [dom, f"*.{dom}"], "policy": "one_factor"})
    else:
        rules.append({"domain": f"*.{dom}", "policy": "one_factor"})
    # Hosts on an app's own domain: no wildcard covers them, so each record
    # names its hosts in one rule at its policy. Default policy is deny, so
    # a name on an extra domain that no record lists is refused by Authelia
    # as well as unserved by Caddy.
    for app in apps:
        if app.extra_hosts:
            rules.append({"domain": list(app.extra_hosts), "policy": app.policy})
    cookies = [{"domain": dom, "authelia_url": f"https://{auth}.{dom}"}]
    for extra in extra_domains(apps):
        cookies.append({"domain": extra, "authelia_url": f"https://{auth}.{extra}"})
    doc = {
        "theme": "auto",
        "server": {"address": "tcp://0.0.0.0:9091"},
        "log": {"level": "info"},
        "totp": {"issuer": dom},
        "webauthn": {
            "disable": False,
            "enable_passkey_login": True,
            "display_name": "Loam",
            "attestation_conveyance_preference": "indirect",
            "timeout": "60s",
            "selection_criteria": {
                "discoverability": "preferred",
                "user_verification": "preferred",
            },
        },
        "identity_validation": {
            "reset_password": {"jwt_lifespan": "5m", "jwt_algorithm": "HS256"},
            # Managing passkeys needs an elevated session. Default is a
            # one-time code by email, which this box cannot deliver (the
            # notifier writes to a file). A session that did password +
            # TOTP counts instead, which is what the pre-generated TOTP
            # device in `gen-deploy --init-login` is for.
            "elevated_session": {"skip_second_factor": True},
        },
        "authentication_backend": {
            "password_reset": {"disable": False},
            "password_change": {"disable": False},
            "file": {"path": "/data/users_database.yml", "watch": True},
        },
        "access_control": {"default_policy": "deny", "rules": rules},
        "session": {
            # loam_auth since 2026-11-15 (was loam_session). The L14 domain move
            # left browsers holding a loam_session cookie on the old parent
            # domain, which every request to a host under the new one (a name
            # under the old) still carries; Authelia read it before the new one, so every login
            # succeeded and then bounced back to the portal. A new name cannot
            # collide. Rename it again on any future domain move.
            "name": "loam_auth",
            "same_site": "lax",
            # 1d / 7d since 2026-11-09 (were 1h / 12h): the owner leaves phone
            # tabs open all day, and their standing ask is that it works
            # smoothly. With the sessions in Redis a restart no longer
            # resets these clocks, so they are the only ones that sign out.
            "inactivity": "1d",
            "expiration": "7d",
            "remember_me": "1M",
            # One entry per domain. Authelia keeps a session per cookie
            # domain: the same user, the same users file, one login per
            # domain per device (per month, with remember_me), and a passkey
            # is bound to the domain it was enrolled on.
            "cookies": cookies,
            # The sessions outlive the container and the box (2026-11-09).
            # Redis is the login project's own second container, reached by
            # its service name on the project's default network, which no
            # other container joins (network_findings says so if one does)
            # and which publishes no port. No password, for that reason:
            # the only thing that can reach it is Authelia.
            "redis": {"host": "redis", "port": 6379},
        },
        "regulation": {"max_retries": 5, "find_time": "2m", "ban_time": "10m"},
        "storage": {"local": {"path": "/data/db.sqlite3"}},
        # The file notifier is Authelia's test-only notifier and it is the
        # right one here: no mail leaves this box. A password reset or a
        # one-time code lands in this file inside the volume, readable by
        # the platform engineer on a brief.
        "notifier": {"disable_startup_check": False,
                     "filesystem": {"filename": "/data/notification.txt"}},
    }
    header = (
        "# GENERATED by agents/bin/gen-deploy (lib/loam_deploy.authelia_config).\n"
        "# Do not edit. No secret is in this file: the three Authelia needs are\n"
        f"# environment variables from {AUTHELIA_SECRETS} (root-only), and the\n"
        "# users file with its argon2id hash lives in the authelia-data volume.\n"
    )
    return header + yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)


def authelia_compose() -> str:
    doc = {
        "name": AUTHELIA_PROJECT,
        "services": {
            "authelia": {
                "image": AUTHELIA_IMAGE,
                "container_name": AUTHELIA_PROJECT,
                "restart": "unless-stopped",
                # The image's entrypoint chowns /config and drops to PUID via
                # su-exec, which needs CAP_SETGID; with every capability
                # dropped it loops on "setgroups: Operation not permitted".
                # Running the binary directly as the container's root is the
                # daemon's remapped uid 200000 with no capability at all.
                "entrypoint": ["authelia"],
                "command": ["--config", "/config/configuration.yml"],
                "ports": [f"127.0.0.1:{AUTHELIA_PORT}:9091"],
                "volumes": [
                    f"{AUTHELIA_CONFIG}:/config/configuration.yml:ro",
                    "authelia-data:/data",
                ],
                "env_file": [str(AUTHELIA_SECRETS)],
                # Not read_only: Authelia's startup checks fail on a read-only
                # root even with /tmp and /var/tmp on tmpfs (caused 2026-09-08,
                # bisected against cap_drop, which is fine). The root is the
                # image's own layer, written by a capability-less remapped uid.
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                "mem_limit": "256m",
                "pids_limit": 128,
                "init": True,
                "depends_on": ["redis"],
            },
            "redis": {
                "image": AUTHELIA_REDIS_IMAGE,
                "container_name": AUTHELIA_REDIS,
                "restart": "unless-stopped",
                # The binary directly, as Authelia is run: the image's
                # entrypoint chowns /data and drops to its own user, which
                # needs capabilities this container does not have. So it
                # starts as that user: the image's /data, copied into the
                # volume when it was created, is owned by redis (999:1000),
                # and the container's root with every capability dropped
                # cannot write it (found 2026-11-09: "Failed opening the
                # temp RDB file ... Permission denied", a restart lost all).
                "user": "999:1000",
                "entrypoint": ["redis-server"],
                "command": ["--save", "60", "1", "--appendonly", "no",
                            "--dir", "/data", "--protected-mode", "no",
                            "--maxmemory", "48mb", "--maxmemory-policy", "volatile-lru"],
                "volumes": ["authelia-sessions:/data"],
                "read_only": True,
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                "mem_limit": "64m",
                "pids_limit": 64,
                "init": True,
            },
        },
        # External: the volume is state (the users file, the SQLite store,
        # every passkey) and `compose down -v` must not be able to take it.
        # `gen-deploy --init-login` creates it. The sessions volume is not
        # external: losing it costs one login per device, nothing more.
        "volumes": {"authelia-data": {"external": True, "name": "authelia-data"},
                    "authelia-sessions": {"name": "authelia-sessions"}},
    }
    header = ("# GENERATED by agents/bin/gen-deploy (lib/loam_deploy.authelia_compose).\n"
              "# Do not edit. The login container: loopback publish only, behind\n"
              "# Caddy's forward_auth and the auth.<domain> portal block.\n")
    return header + yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)


# ---------------------------------------------------------------------------
# The browser
# ---------------------------------------------------------------------------

def browser_blocked_origins() -> str:
    """The origin flag: belt, where the wall is the braces.

    Playwright MCP matches `--blocked-origins` against the URL of every
    request the page makes (navigation, fetch, script, image; not a service
    worker's, not a WebSocket's) and aborts a match with
    ERR_BLOCKED_BY_CLIENT before it leaves Chromium. An entry is a host glob
    (`*` is any run of characters but `/`, `{a,b}` alternates, `[` is
    literal), an exact origin, or `scheme://host:*` for every port of one
    host -- though that last form's regex refuses a bracketed IPv6 host, so
    the plain `host:*` glob is used for every port of every loopback
    spelling and `host` alone for the default port. It sees names,
    not the addresses they resolve to, which is why the kernel wall exists;
    but it is the only layer that reaches the container's OWN loopback,
    where the MCP server listens, so the loopback spellings are here on
    every port. (Behind them stands the server's own `--allowed-hosts`
    check, which 403s any Host header but the two it was bound for.) The
    box's public address by number is not listed: Caddy answers a bare-IP
    Host with a 308 to an https URL it holds no certificate for, and every
    other port there is the wall's.
    """
    entries = []
    for host in ("localhost", "127.0.0.1", "[::1]", "0.0.0.0"):
        entries += [host, f"{host}:*"]   # the default port, and every other
    entries += [
        "127.*",
        "10.*",
        "172.{" + ",".join(str(n) for n in range(16, 32)) + "}.*",
        "192.168.*",
        "100.{" + ",".join(str(n) for n in range(64, 128)) + "}.*",
        "169.254.*",
    ]
    return ";".join(entries)


def sight_wall_rules() -> dict[str, list[str]]:
    """The wall as data: chain -> rule specs, in order, without `-A chain`.
    The script appends them; check-drift asks `iptables -C` about each."""
    reject_ranges = [f"-d {cidr} {_REJECT}" for cidr, _ in SIGHT_INTERNAL_RANGES]
    tcp = ",".join(str(p) for p in SIGHT_HOST_TCP_PORTS)
    return {
        SIGHT_WALL_CHAIN: reject_ranges,
        SIGHT_WALL_HOST_CHAIN: [
            "-m conntrack --ctstate ESTABLISHED,RELATED -j RETURN",
            *reject_ranges,
            f"-p tcp -m multiport --dports {tcp} -j RETURN",
            *[f"-p udp --dport {p} -j RETURN" for p in SIGHT_HOST_UDP_PORTS],
            _REJECT,
        ],
    }


def sight_wall_jumps() -> list[tuple[str, str]]:
    """(parent chain, rule spec): the one rule in each built-in path that
    hands the browser subnet's traffic to the wall."""
    return [
        ("DOCKER-USER", f"-s {SIGHT_SUBNET} -j {SIGHT_WALL_CHAIN}"),
        ("INPUT", f"-s {SIGHT_SUBNET} -j {SIGHT_WALL_HOST_CHAIN}"),
    ]


def sight_firewall_script() -> str:
    lines = [
        "#!/bin/sh",
        "# GENERATED by agents/bin/gen-deploy (lib/loam_deploy.sight_firewall_script).",
        "# Do not edit. The wall around the persona browser's network: the",
        f"# {SIGHT_SUBNET} subnet reaches the open web and this box's 80/443 at",
        "# its public address, and nothing else on the box or its bridges.",
        "# Idempotent: each chain is created or flushed and rebuilt, each jump",
        "# is inserted at the top of its built-in chain unless already there.",
        f"# Run by {SIGHT_WALL_SERVICE} after docker.service, which owns",
        "# DOCKER-USER; with no docker there is no chain and this exits 1.",
        "# `ufw reload` flushes every chain here, Docker's included: restart",
        "# docker afterwards and the unit, PartOf it, raises the wall again.",
        "#",
        "# Ranges rejected (from inside the container, before this wall, sshd",
        "# answered on every gateway and 169.254.169.254 connected):",
        *[f"#   {cidr:16} {why}" for cidr, why in SIGHT_INTERNAL_RANGES],
        "set -eu",
        "IPT=iptables",
        "chain() { $IPT -N \"$1\" 2>/dev/null || $IPT -F \"$1\"; }",
        "jump() { $IPT -C \"$@\" 2>/dev/null || $IPT -I \"$@\"; }",
        "",
    ]
    for chain, rules in sight_wall_rules().items():
        lines.append(f"chain {chain}")
        lines += [f"$IPT -A {chain} {rule}" for rule in rules]
        lines.append("")
    lines += [f"jump {parent} {rule}" for parent, rule in sight_wall_jumps()]
    return "\n".join(lines) + "\n"


def sight_firewall_unit() -> str:
    return (
        "# GENERATED by agents/bin/gen-deploy (lib/loam_deploy.sight_firewall_unit).\n"
        "# Do not edit. Raises the wall around the persona browser's network once\n"
        "# docker has created DOCKER-USER; restarts with docker (PartOf) so a\n"
        "# daemon restart, or the docker restart that follows a `ufw reload`,\n"
        "# raises it again. check-drift asks the kernel for every rule.\n"
        "[Unit]\n"
        "Description=Loam: the wall around the persona browser's network\n"
        f"Documentation=file://{lc.ROOT}/deploy/README.md\n"
        "After=docker.service\n"
        "Requires=docker.service\n"
        "PartOf=docker.service\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        "RemainAfterExit=yes\n"
        f"ExecStart={SIGHT_FIREWALL_SCRIPT}\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def sight_wall_missing() -> list[str]:
    """Every rule of the wall the kernel does not have, as `iptables -C`
    would be asked, plus the unit if it is not enabled. Empty is the wall
    standing. Raises if iptables itself cannot be asked."""
    missing = []
    checks = [(parent, rule) for parent, rule in sight_wall_jumps()]
    for chain, rules in sight_wall_rules().items():
        checks += [(chain, rule) for rule in rules]
    for chain, rule in checks:
        run = _sudo(["iptables", "-w", "5", "-C", chain, *rule.split()])
        if run.returncode != 0:
            err = run.stderr.strip()
            if "Permission denied" in err or "sudo" in err:
                raise DeployError(f"iptables -C: {err}")
            missing.append(f"iptables -A {chain} {rule}")
    run = _sudo(["systemctl", "is-enabled", SIGHT_WALL_SERVICE])
    if run.stdout.strip() != "enabled":
        missing.append(f"{SIGHT_WALL_SERVICE} is {run.stdout.strip() or run.stderr.strip() or 'absent'}, not enabled")
    return missing


def browser_compose() -> str:
    doc = {
        "name": BROWSER_PROJECT,
        "services": {
            "browser": {
                "image": BROWSER_IMAGE,
                "container_name": BROWSER_PROJECT,
                "restart": "unless-stopped",
                "entrypoint": ["node", "/app/cli.js"],
                "command": [
                    "--headless", "--browser", "chromium", "--no-sandbox",
                    "--isolated",
                    # From v0.0.82 a page's WebMCP tools are listed as MCP
                    # tools (`webmcp_<name>`, the page's own schema), and every
                    # persona allows the browser server whole: a page could
                    # hand a persona a tool it may call without a prompt. The
                    # server reads them from `navigator.modelContext`, which
                    # any page can define without a Chromium flag (proved
                    # 2026-10-06: without this flag a test page's tool was
                    # listed and ran; with it, none).
                    "--no-webmcp",
                    "--host", "0.0.0.0", "--port", str(BROWSER_PORT),
                    "--allowed-hosts", BROWSER_ALLOWED_HOSTS,
                    # The open web is allowed; what is blocked is blocked by
                    # the kernel (sight_firewall_script) and, as a second
                    # layer that also covers the container's own loopback,
                    # by this flag. Until 2026-09-09 this was --allowed-origins
                    # <domain>;*.<domain>, which stopped every page's own
                    # fonts, scripts and images at ERR_BLOCKED_BY_CLIENT.
                    "--blocked-origins", browser_blocked_origins(),
                ],
                "ports": [f"127.0.0.1:{BROWSER_PORT}:{BROWSER_PORT}"],
                "networks": ["sight"],
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                "shm_size": "256m",
                "mem_limit": "1g",
                "pids_limit": 512,
                "init": True,
            }
        },
        "networks": {
            "sight": {
                "name": SIGHT_NETWORK,
                "ipam": {"config": [{"subnet": SIGHT_SUBNET}]},
            }
        },
    }
    header = ("# GENERATED by agents/bin/gen-deploy (lib/loam_deploy.browser_compose).\n"
              "# Do not edit. One headless Chromium for every persona, on its own\n"
              "# network (`networks`), open to the web and walled off from this box\n"
              "# by firewall.sh beside this file; --blocked-origins is the second layer.\n")
    return header + yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)


# ---------------------------------------------------------------------------
# The deploy command's sudoers file
# ---------------------------------------------------------------------------

# The unix user a project engineer's job runs as. Today that is the box's user,
# the same user as everything else on the box, and that user already holds
# `NOPASSWD: ALL` from /etc/sudoers.d/loam-engineer -- so the lines rendered
# below grant nothing that user does not have, and a project engineer's job
# cannot use them anyway: its unit carries NoNewPrivileges. The file is
# rendered now so the SHAPE is generated, drift-checked and read by the next
# decision (a unix user per project, or one for all projects), which is the
# only thing that makes "this command, this app, nothing else" bind.
PROJECT_USER = lc.USER


def sudoers() -> str:
    lines = [
        "# GENERATED by agents/bin/gen-deploy (lib/loam_deploy.sudoers). Do not",
        "# edit. One alias per deployment record: bin/deploy, that app's name,",
        "# and whatever verb follows. The command validates its own arguments;",
        "# the alias is what stops one project from deploying another.",
        "#",
        f"# Inert while every job runs as {PROJECT_USER}, which holds NOPASSWD: ALL",
        "# in loam-engineer and whose project jobs run under NoNewPrivileges. See",
        "# deploy/README.md, 'The deploy loop'.",
        "",
    ]
    for app in records():
        if app.checkout is None:
            continue
        alias = "LOAM_DEPLOY_" + re.sub(r"[^A-Z0-9]", "_", app.name.upper())
        lines.append(f"Cmnd_Alias {alias} = {DEPLOY_BIN} {app.name} *")
        lines.append(f"{PROJECT_USER} ALL=(root) NOPASSWD: {alias}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Everything at once, for the generator and the drift check
# ---------------------------------------------------------------------------

def generated(today: dt.date | None = None) -> dict[Path, str]:
    return {
        CADDYFILE: caddyfile(today),
        DAEMON_JSON: daemon_json(),
        AUTHELIA_CONFIG: authelia_config(),
        AUTHELIA_COMPOSE: authelia_compose(),
        BROWSER_COMPOSE: browser_compose(),
        SIGHT_FIREWALL_SCRIPT: sight_firewall_script(),
        SIGHT_FIREWALL_UNIT: sight_firewall_unit(),
        SUDOERS: sudoers(),
    }


def static_roots() -> list[Path]:
    """Directories the proxy serves. gen-deploy makes sure they exist."""
    return [a.static_root for a in records() if a.static_root is not None]


# ---------------------------------------------------------------------------
# What is live, for check-drift. Each returns data; the check decides.
# ---------------------------------------------------------------------------

def _sudo(argv: list[str]) -> subprocess.CompletedProcess:
    if os.geteuid() != 0:
        argv = ["sudo", "-n", *argv]
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def live_publishes() -> list[tuple[str, str]]:
    """(container, publish) for every port every running container publishes,
    as Docker reports it, e.g. ('probe-clock', '127.0.0.1:8000->8000/tcp')."""
    run = _sudo(["docker", "ps", "--format", "{{.Names}}\t{{.Ports}}"])
    if run.returncode != 0:
        raise DeployError(f"docker ps: {run.stderr.strip()}")
    rows = []
    for line in run.stdout.splitlines():
        name, _, ports = line.partition("\t")
        for pub in [p.strip() for p in ports.split(",") if "->" in p]:
            rows.append((name, pub))
    return rows


def live_container_networks() -> dict[str, tuple[str, ...]]:
    """{container: the networks it is on} for every running container, as
    Docker reports it: `loam-ui` -> ('loam-broker', 'loam-ui_default')."""
    run = _sudo(["docker", "ps", "--format", "{{.Names}}\t{{.Networks}}"])
    if run.returncode != 0:
        raise DeployError(f"docker ps: {run.stderr.strip()}")
    out: dict[str, tuple[str, ...]] = {}
    for line in run.stdout.splitlines():
        name, _, nets = line.partition("\t")
        if name.strip():
            out[name.strip()] = tuple(
                n.strip() for n in nets.split(",") if n.strip())
    return out


def log_findings(apps: list[App] | None = None) -> list[tuple[str, str]]:
    """(container, why) for every running app container whose log config is
    not APP_LOGGING. The compose file is checked by records(); this asks the
    container, because a log config is fixed when the container is created
    and a compose edit reaches it only on the next `bin/deploy <app> up`."""
    apps = records() if apps is None else apps
    out: list[tuple[str, str]] = []
    for app in apps:
        for container in app.containers:
            run = _sudo(["docker", "inspect", "-f", "{{json .HostConfig.LogConfig}}", container])
            if run.returncode != 0:
                continue   # not running; the record is checked, the box has nothing to ask
            try:
                cfg = json.loads(run.stdout)
            except json.JSONDecodeError:
                out.append((container, f"has an unreadable log config: {run.stdout.strip()!r}"))
                continue
            live = {"driver": cfg.get("Type"), "options": cfg.get("Config") or {}}
            if live != APP_LOGGING:
                out.append((container, f"logs with {live!r}, not {APP_LOGGING!r}. "
                                       f"`sudo bin/deploy {app.name} up <its commit>` "
                                       f"recreates it on the compose file's config"))
    return out


def network_findings(apps: list[App] | None = None,
                     live: dict[str, tuple[str, ...]] | None = None
                     ) -> list[tuple[str, str]]:
    """(container, why) for every running container on a network no record
    puts it on. The kernel half of the compose check above.

    The generator refuses a compose file that joins a third network, and that
    is the file; this is the box. They fail apart in both directions -- a
    container started before the check existed, one started by hand, one
    attached with `docker network connect` (which no file records at all) --
    and the one that matters is a stranger on `loam-sight`, because Caddy lets
    that subnet past a private host's login.

    Two questions in one pass. A container a record owns must be on exactly
    the networks its compose file joins, and nothing more. A container no
    record owns is not this check's business UNLESS it is on a network the
    runtime owns and hands out, which is the bypass arriving from outside the
    deployment layer entirely.
    """
    apps = records() if apps is None else apps
    live = live_container_networks() if live is None else live
    owned: dict[str, tuple[str, ...]] = dict(RUNTIME_CONTAINER_NETWORKS)
    for app in apps:
        owned.update(app.containers)
    out: list[tuple[str, str]] = []
    for container, nets in sorted(live.items()):
        expected = owned.get(container)
        for net in nets:
            if expected is not None:
                if net not in expected:
                    out.append((container, (
                        f"is on {net}, which its compose file does not join "
                        f"({', '.join(expected) or 'none'}). A container's "
                        f"networks are its compose file's to decide, and this "
                        f"one is on a network no file put it on.")))
            elif net in RUNTIME_NETWORKS:
                out.append((container, (
                    f"is on {net}, a network the runtime owns, and no "
                    f"deployment record names this container. "
                    f"{SIGHT_NETWORK} is the personas' browser's and Caddy "
                    f"lets its subnet past a private host's login; "
                    f"{loam_broker.NETWORK} reaches the session broker.")))
    return out


def live_listeners() -> list[tuple[str, str]]:
    """(address:port, process) for every TCP listener on the box."""
    run = _sudo(["ss", "-Htlnp"])
    if run.returncode != 0:
        raise DeployError(f"ss: {run.stderr.strip()}")
    rows = []
    for line in run.stdout.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        addr = parts[3]
        m = re.search(r'users:\(\("([^"]+)"', line)
        rows.append((addr, m.group(1) if m else "?"))
    return rows


def is_loopback(addr: str) -> bool:
    host = addr.rsplit(":", 1)[0]
    return host in ("127.0.0.1", "[::1]", "::1") or host.startswith("127.")


def docker_group_members() -> list[str]:
    import grp
    try:
        return list(grp.getgrnam("docker").gr_mem)
    except KeyError:
        return []


# Non-loopback listeners the box is allowed: ssh and the proxy. Anything else
# answering off-loopback is a finding, unless a plugin that is on declares it
# (`box.listener`, lib/loam_contributions.py; the owner's file sync is one).
ALLOWED_PUBLIC_LISTENERS = frozenset({"sshd", "caddy"})

CADDY_ADMIN = "http://localhost:2019/config/"


def caddy_adapted(path: Path = CADDYFILE) -> dict | None:
    """The live Caddyfile as Caddy itself would load it. None if caddy is
    not on the box or the file does not adapt. Adapted from its real path,
    not from a copy: `file_server` hides the Caddyfile's own path in the
    adapted config, so a copy adapts to something the process never loaded."""
    run = subprocess.run(["caddy", "adapt", "--adapter", "caddyfile", "--config", str(path)],
                         capture_output=True, text=True, check=False)
    if run.returncode != 0:
        return None
    try:
        return json.loads(run.stdout)
    except json.JSONDecodeError:
        return None


def caddy_running() -> dict | None:
    """What the caddy process is actually serving, from its admin API on
    loopback. None when nothing answers, which is its own finding."""
    import urllib.request
    try:
        with urllib.request.urlopen(CADDY_ADMIN, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
