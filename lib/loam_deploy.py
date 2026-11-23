"""Render deployment configuration from the install and app records. bin/gen-deploy
installs the rendered files. bin/check-drift compares the live copies against the
same renderers. See deploy/README.md for formats and procedures.

App aliases inherit the app's exposure policy. Each cookie domain needs its own login
portal and session. An explicitly shared route still enforces its token. A content
host serves only signed content on a separate origin and strips credentials on both
sides of the proxy. Keep secrets in their provisioned files outside the checkout.
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
# Render the login assets beside the portal configuration.
LOGIN_SRC = Path(__file__).resolve().parent.parent / "deploy" / "login"
LOGIN_DIR = ETC / "login"
LOGIN_ASSETS = "/_loam/login"

# Modes for the files gen-deploy writes; anything not named here is 0644.
# sudo refuses a sudoers file that is writable by anyone, so this one is
# not a default. The firewall script is run by systemd, so it is executable.
MODES = {SUDOERS: 0o440, SIGHT_FIREWALL_SCRIPT: 0o755}

# Pinned images. An upgrade is an edit here, a `gen-deploy`, and a line in
# the receipt; never a `:latest` that moves under the box.
AUTHELIA_IMAGE = "authelia/authelia:4.39.23"
# Persist login sessions across container restarts. Configure the store to flush on
# shutdown.
AUTHELIA_REDIS_IMAGE = "redis:7.4.12-alpine"
# The pinned browser server supports page evaluation and timer-independent settling.
# Keep its advertised tools aligned with the configured browser clients.
BROWSER_IMAGE = "mcr.microsoft.com/playwright/mcp:v0.0.85"

# Loopback ports the runtime's own containers publish on. Apps choose their
# own in their compose file; the generator refuses a collision.
AUTHELIA_PORT = 9091
# Route server plugins through the loopback gateway.
PLUGIN_GATEWAY_PORT = 8933
# Only the gateway handles token-scoped share requests on an app-shell host.
PLUGIN_DOOR_PORT = 8936
BROWSER_PORT = 8931
BROWSER_URL = f"http://127.0.0.1:{BROWSER_PORT}/mcp"
BROWSER_SERVER_NAME = "browser"   # the MCP server's name in every persona
BROWSER_OUTPUT = lc.BROWSER_OUTPUT
# Evict oldest browser-tool outputs once the shared folder exceeds 256 MiB.
BROWSER_OUTPUT_MAX_BYTES = 256 * 1024 * 1024
# The Playwright MCP image runs as `node` (1000:1000) inside its user namespace.
BROWSER_UID = 1000
BROWSER_GID = 1000

# Use a host header the browser server accepts for its loopback MCP endpoint.
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

# The browser needs public assets while rendering pages. Enforce the internal-network
# boundary on resolved addresses, for both forwarded traffic and traffic to the host.
# Allow established replies so the MCP connection remains usable. The container's own
# loopback requires a separate browser-side policy.
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

# Previews use an internal listener restricted to the host and broker network. Address
# previews by hostname so root-relative assets resolve normally; the renderer supplies
# the private hostname mapping.
PREVIEW_ADDR = loam_broker.BIND_ADDR
PREVIEW_PORT = 8935
# The host-run app uses a loopback preview listener so it need not connect through an
# external interface.
PREVIEW_LOCAL_ADDR = "127.0.0.1"
PREVIEW_LOCAL_NET = "127.0.0.0/8"
PREVIEW_SUFFIX = "preview.internal"
PREVIEW_LOG = "preview"           # /var/log/caddy/access/preview.log
# Caddy binds PREVIEW_ADDR, which exists only once docker has raised the
# broker's bridge, and a listener that cannot bind fails Caddy's whole
# start. Caddy and docker are not ordered at boot, so the kernel is told to
# allow a bind to an address not (yet) on an interface. Nothing becomes
# reachable by it: a packet for an address the box does not carry is never
# delivered to the socket.
PREVIEW_SYSCTL = Path("/etc/sysctl.d/60-loam-preview.conf")
PREVIEW_SYSCTL_KEY = "net.ipv4.ip_nonlocal_bind"

# The private static host for "local files": a persona writes a file under
# this directory (the commons, writable by every persona, expired after
# seven days like everything else there) and opens it in the browser as
# https://files.<domain>/<name>. The browser image blocks file:// unless
# told otherwise and has no path into this box anyway.
FILES_ROOT = AI_LABS / "commons" / "www"

# Load agent-owned sites from the site registry. Their records carry ownership, expiry
# and explicit publication state.
SITES_DIR = lc.STATE / "sites"

# Authelia needs at least one two-factor rule to offer enrollment. Put it on a
# reserved, unrouted hostname so actual apps keep their configured policy.
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


# Resolve the install's public edge from its configuration. Preserve support for
# existing deployment records on installs without an edge definition.
EDGE: dict = lc.get("edge") or {}
EDGE_STATE = Path("/var/lib/loam/edge.json")     # root's; loam-await-dns writes it
CLAIM_FILE = ETC / "claim"                       # root-only: the claim's token
CLAIMED_FILE = ETC / "claimed"                   # who claimed the install, and when
CLAIM_PORT = 8934                                # the claim service, loopback
CLAIM_PATH = "/claim"
CLAIM_MARKER = "# UNCLAIMED:"
RELAY_PATH = "/_loam/login-code"
# Relay the signed-in user's passkey operations through the app shell to the login
# service.
PASSKEYS_PATH = "/_loam/passkeys"
# The login's session cookie: the one cookie the passkeys relay forwards.
SESSION_COOKIE = "loam_auth"


def edge_state() -> dict:
    """What loam-await-dns last found ({"dns": "resolved", ...}), or {}."""
    try:
        data = json.loads(read_root_text(EDGE_STATE) or "{}")
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


# A moved hostname redirects the full path and query to its configured destination.
# Keep rendering redirects past the review date until the operator removes the matching
# DNS records.

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


# Preserve registered callback URLs that cannot be updated through an API. Redirect
# only the configured hosts and paths.

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


# A standalone landing host serves an explicitly configured directory. Require the
# owner's approval in its record before generating an ungated public host.

def landings() -> list[dict]:
    """[{"host", "root", "public"}] from deploy/loam.toml's `[[landing]]`,
    or []."""
    if EDGE.get("domain") or not CONFIG_PATH.is_file():
        return []
    raw = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8")).get("landing")
    if raw is None:
        return []
    where = f"{CONFIG_PATH}: [[landing]]"
    if not isinstance(raw, list):
        raise DeployError(f"{where} is an array of tables")
    dom = domain()
    m = moved()
    taken = {f"{h}.{m['from']}": "[moved]" for h in m["hosts"]} if m else {}
    taken |= {h: "[[forward]]" for h in forward_hosts()}
    out: list[dict] = []
    for e in raw:
        e = e if isinstance(e, dict) else {}
        host, root, public = e.get("host"), e.get("root"), e.get("public")
        if set(e) - {"host", "root", "public"}:
            raise DeployError(f"{where}: {host}: only host, root and public")
        if not isinstance(host, str) or not DOMAIN_RE.match(host):
            raise DeployError(f"{where}: host {host!r} is not a hostname")
        if host == dom or host.endswith("." + dom):
            raise DeployError(f"{where}: {host} is under {dom}; a site or a record serves it")
        if host in taken:
            raise DeployError(f"{where}: {host} is already {taken[host]}'s")
        if (not isinstance(root, str) or not SITE_ROOT_RE.match(root)
                or ".." in root.split("/") or root == "/"):
            raise DeployError(f"{where}: {host}: root {root!r} must be an absolute "
                              f"path of letters, digits and ._/@+- only")
        if not isinstance(public, str) or not public.strip():
            raise DeployError(f"{where}: {host}: public is {lc.OWNER_NAME}'s yes quoted; "
                              f"without one the host is not served")
        taken[host] = "[[landing]]"
        out.append({"host": host, "root": root.rstrip("/"), "public": " ".join(public.split())})
    return out


def caddy_landing_block(e: dict) -> str:
    """One public static folder on its own name: no gate, no listing."""
    host = e["host"]
    return "\n".join([
        f"# PUBLIC: {lc.OWNER_NAME}'s yes in [[landing]] (deploy/loam.toml): {e['public']}. No gate.",
        f"{host} {{",
        access_log_line(host),
        f"\troot * {e['root']}",
        "\tfile_server {",
        f"\t\thide {' '.join(SITE_HIDE)}",
        "\t}",
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
    # Identify a site by the agent recorded as its owner. Site records have a separate
    # lifecycle from deployed apps.
    site_owner: str | None = None
    site_expires: dt.date | None = None
    site_public: str | None = None
    # App-shell hosts receive the shell's relay routes and origin settings.
    app_shell: bool = False
    # The edge app or a record with apex enabled serves the install domain itself.
    apex: bool = False
    context: str | None = None
    # Service records run the app under systemd from a release directory.
    service: bool = False

    @property
    def unit(self) -> str | None:
        """The systemd instance a service app runs as, else None."""
        return f"{SERVICE_UNIT_PREFIX}{self.name}.service" if self.service else None

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

# Give every app service the same log driver and rotation settings so its request logs
# have bounded retention.
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
    """Resolve each Compose network alias to its actual Docker network name before
    validating it. Permit only the project's default network and the broker network.
    Check even unused declarations because compose up can create or look them up.
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


# A service app runs an immutable release under a per-app systemd unit. Keep releases
# and the current link separate from the canonical checkout so an update can switch or
# roll back the running version.

SERVICE_UNIT_PREFIX = "loam-app@"
SERVICE_UNIT = Path("/etc/systemd/system/loam-app@.service")
# Put releases where the unprivileged service user can traverse their parent
# directories.
SERVICE_RELEASES = Path("/var/lib/loam-releases")
SERVICE_ENV_DIR = Path("/run/loam/app")
SERVICE_STATE = Path("/var/lib/loam-app")      # StateDirectory=loam-app/<app>
SERVICE_NODE = "/usr/bin/node"
# Root's Python bytecode, outside the checkout (bin/loam-install's too).
ROOT_PYCACHE = "/var/cache/loam/pycache"
# The listener's address. Caddy is the box's one public listener; the app
# answers it on loopback only.
SERVICE_HOST = "127.0.0.1"
SERVICE_PORTS = range(1024, 65536)


def _service_port(path: Path, raw) -> int:
    """The `[service]` table's one key: the loopback port the app listens on."""
    if not isinstance(raw, dict) or set(raw) != {"port"}:
        raise DeployError(f"{path}: [service] holds `port = <loopback port>` and "
                          f"nothing else; how it runs is lib/loam_deploy's "
                          f"service_unit, the same for every record")
    port = raw["port"]
    if not isinstance(port, int) or isinstance(port, bool) or port not in SERVICE_PORTS:
        raise DeployError(f"{path}: [service] port {port!r} is not a port of "
                          f"{SERVICE_PORTS.start}-{SERVICE_PORTS.stop - 1}")
    return port


def service_unit() -> str:
    """`loam-app@.service`: one template, an instance per `[service]` record.
    Everything that differs between instances is the record's, read by
    `bin/deploy <app> env` when the instance starts."""
    deploy = f"{lc.ROOT}/bin/deploy"
    return f"""\
# GENERATED by bin/gen-deploy (lib/loam_deploy.service_unit). Do not edit.
#
# Loam's own app, one instance per deployment record with a [service]
# table: loam-app@<app>.service. Started, restarted and rolled back by
# `sudo bin/deploy <app> up <commit>`, which unpacks that commit's app into
# {SERVICE_RELEASES}/<app>/releases/<sha>/ and points `current` at it; this unit
# runs whatever `current` names. deploy/README.md, "Loam's own app".
[Unit]
Description=Loam's app: %i
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
# The environment, the broker's token and any vendor key among it, written
# as root to {SERVICE_ENV_DIR}/%i.env (memory, root-only) before every start,
# and read by systemd for the process below. Never on disk. Optional (`-`):
# systemd reads it before ExecStartPre too, when a fresh boot has none yet,
# and reads it again for ExecStart, after ExecStartPre has written it
# (caused 2026-11-16: without the `-` the first start failed on it).
ExecStartPre=+/usr/bin/env PYTHONPYCACHEPREFIX={ROOT_PYCACHE} /usr/bin/python3 {deploy} %i env
EnvironmentFile=-{SERVICE_ENV_DIR}/%i.env
WorkingDirectory={SERVICE_RELEASES}/%i/current
ExecStart={SERVICE_NODE} src/server.js
Restart=always
RestartSec=2s
TimeoutStopSec=15s
# Its own lines in the journal under the app's name: `journalctl -t %i`,
# `bin/logs app %i`.
SyslogIdentifier=%i
# No memory ceiling (2026-11-16), and the core's OOM score, as the broker.
OOMScoreAdjust={lc.CORE_OOM_SCORE_ADJ}
# The wall. A uid of its own and nothing of the box: its state directory is
# the one place it writes (speech audio, page previews), and /home is not
# there at all.
DynamicUser=yes
StateDirectory=loam-app/%i
Environment=HOME={SERVICE_STATE}/%i
UMask=0077
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
LockPersonality=yes
CapabilityBoundingSet=
AmbientCapabilities=
NoNewPrivileges=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK
SystemCallArchitectures=native

[Install]
WantedBy=multi-user.target
"""


def service_apps(apps: list[App] | None = None) -> list[App]:
    return [a for a in (records() if apps is None else apps) if a.service]


def service_findings(apps: list[App] | None = None) -> list[tuple[str, str]]:
    """Report service apps whose expected unit is inactive or whose old container still owns
    the app name.
    """
    out = []
    for app in service_apps(apps):
        res = _sudo(["systemctl", "is-active", app.unit])
        state = res.stdout.strip() or "unknown"
        if state != "active":
            out.append((app.name, f"{app.unit} is {state}; "
                                  f"`sudo bin/deploy {app.name} up <commit>` starts it"))
        res = _sudo(["docker", "ps", "-q", "--filter", f"name=^{app.name}$"])
        if res.returncode == 0 and res.stdout.strip():
            out.append((app.name, f"a container named {app.name} is running beside "
                                  f"{app.unit}; the app runs from its unit now"))
    return out


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
        if "service" in rec and (static_root is not None or compose.is_file()):
            raise DeployError(f"{path}: [service] is a kind of app of its own; a "
                              f"record has a static_root, a compose.yaml or a "
                              f"[service], never two")
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
        elif "service" in rec:
            port = _service_port(path, rec["service"])
        elif compose.is_file():
            doc = _compose_doc(compose)
            port = _loopback_port(compose, name, doc)
            containers = _compose_networks(compose, name, doc)
        else:
            raise DeployError(f"{path}: neither compose.yaml, [service] nor static_root")
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
        # Prefer the app record's build source, then the registered project checkout. Static
        # hosts do not need a checkout.
        rec_apex = rec.get("apex", False)
        if not isinstance(rec_apex, bool):
            raise DeployError(f"{path}: apex is true or false")
        apex = (EDGE["app"] == name) if EDGE.get("app") else rec_apex
        # Use the edge app_context for the edge app, otherwise the record's context.
        context = ((EDGE.get("app_context") if apex and EDGE.get("app") else None)
                   or rec.get("context"))
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
            raise DeployError(f"{path}: the app's context {context!r} (the record's "
                              f"`context`, or edge.app_context in {lc.CONFIG_FILE}) "
                              f"is not a relative folder of the checkout")
        apps.append(App(name=name, exposure=exposure, host=host, policy=policy,
                        port=port, static_root=static_root, share_expires=expires,
                        compose=compose if compose.is_file() else None,
                        checkout=checkout, hide=tuple(hide), domains=domains,
                        containers=containers, bypass=bypass,
                        content_host=content_host, record=rec,
                        app_shell=app_shell, apex=apex, context=context,
                        service="service" in rec))
    at_apex = [a.name for a in apps if a.apex]
    if len(at_apex) > 1:
        raise DeployError(f"{DEPLOY}: {' and '.join(at_apex)} both say apex = true; "
                          f"one record is served at {domain()} itself")
    return apps


def frame_ancestors(apps: list[App] | None = None) -> tuple[str, ...]:
    """Return the origins allowed to frame an agent site, including the site itself and the
    install's app shells.
    """
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
    """Read state/sites/<name>.toml as Apps sorted by name. A private static site can
    use a record like this::

        owner = "agent-a"
        root = "/srv/example"

    A proxied site specifies a loopback port instead of a root. An optional expires
    date is in UTC. Public access requires a record carrying the owner's quoted
    approval. Refuse the whole render if a site conflicts with a reserved name,
    another site or a deployment.
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
    """Return whether this project lands through pull requests and therefore has no direct
    deployment lane.
    """
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

# Rotate access logs per host. Strip the query, signed content grant, credential
# headers, Referer and response headers before writing. Keep access lines out of the
# default journal logger.
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


# Compress large responses when the client supports it.
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


# Browser previews may read gated pages. For writes or protocol upgrades, require
# same-origin browser evidence, refuse ambiguous requests and strip client-supplied
# identity headers before proxying.
SIGHT_UNSAFE = (
    "({http.request.method} != \"GET\" && {http.request.method} != \"HEAD\""
    " && {http.request.method} != \"OPTIONS\" || {http.request.header.Upgrade} != \"\")"
    " && !({http.request.header.Sec-Fetch-Site} == \"same-origin\""
    " || ({http.request.header.Sec-Fetch-Site} == \"\""
    " && {http.request.header.Origin} == \"https://\" + {http.request.host}))"
)
SIGHT_IDENTITY_HEADERS = ("Remote-User", "Remote-Groups", "Remote-Email", "Remote-Name")


def sight_lines(backend: str) -> list[str]:
    """The browser's subnet on a private host: refused if it would change
    something from another origin, else passed without the login."""
    return [
        "\t# The personas' browser, by its own network: it reads past the login, and",
        "\t# changes nothing from another page's origin. See lib/loam_deploy.SIGHT_UNSAFE.",
        "\t@sight_cross {",
        f"\t\tremote_ip {SIGHT_SUBNET}",
        f"\t\texpression `{SIGHT_UNSAFE}`",
        "\t}",
        "\thandle @sight_cross {",
        "\t\theader Vary \"Sec-Fetch-Site, Origin\"",
        "\t\trespond \"The agents' browser changes nothing here from another site's page.\" 403",
        "\t}",
        f"\t@sight remote_ip {SIGHT_SUBNET}",
        "\thandle @sight {",
        *(f"\t\trequest_header -{h}" for h in SIGHT_IDENTITY_HEADERS),
        f"\t\t{backend}",
        "\t}",
    ]


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
    if app.app_shell:
        # Also ahead of the browser's subnet: a passkey list is a signed-in
        # person's own, never the personas' browser's.
        lines += [
            "\t# The signed-in person's own passkeys, from the login's own list; see deploy/README.md.",
            f"\thandle {PASSKEYS_PATH}* {{",
            "\t\timport gate",
            f"\t\treverse_proxy 127.0.0.1:{CLAIM_PORT}",
            "\t}",
        ]
    if app.app_shell:
        # Ahead of everything else on these hosts (a `handle /path` is ordered
        # before every `handle @named`): the one path of the app's host that
        # does not meet the login. The gateway's door port answers only share
        # links, and only for the plugin holding the door; see deploy/README.md,
        # "The share door". robots.txt says no to every crawler on every path.
        lines += [
            "\t# The share door: /s/* passes the login to the plugin gateway's door only.",
            "\thandle /s/* {",
            f"\t\treverse_proxy 127.0.0.1:{PLUGIN_DOOR_PORT}",
            "\t}",
            "\thandle /robots.txt {",
            "\t\theader Content-Type \"text/plain; charset=utf-8\"",
            "\t\trespond <<ROBOTS",
            "\t\t\tUser-agent: *",
            "\t\t\tDisallow: /",
            "\t\t\tROBOTS 200",
            "\t}",
        ]
    lines += sight_lines(backend)
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
    """Serve signed content from a distinct origin. Authenticate the request, forward only
    the content path, strip credentials and suppress response cookies.
    """
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
        *sight_lines(proxy),
        "\thandle {",
        "\t\timport gate",
        f"\t\t{proxy}",
        "\t}",
        "}",
    ]
    return "\n".join(lines) + "\n"


def previewable(apps: list[App] | None = None,
                agent_sites: list[App] | None = None) -> list[App]:
    """What the Apps list shows that has a host of its own: every agent's
    site, and every record with a container that is not Loam's page
    (lib/loam_apps.listing, the same two filters). Sorted by host label, so
    the render is deterministic. A plugin's row is served on the page's own
    host and is not here."""
    apps = records() if apps is None else apps
    agent_sites = sites(apps) if agent_sites is None else agent_sites
    rows = list(agent_sites) + [a for a in apps if a.compose is not None
                                and not a.app_shell and not a.apex]
    return sorted(rows, key=lambda a: a.host)


def preview_url(app: App) -> str:
    """The app's address on the internal route, as the renderer loads it."""
    return f"http://{app.host}.{PREVIEW_SUFFIX}:{PREVIEW_PORT}/"


def caddy_preview_block(rows: list[App]) -> str:
    """The internal route: one plain-HTTP site on PREVIEW_ADDR:PREVIEW_PORT,
    one host matcher per previewable app, serving exactly what that app's
    public block serves past its gate (the same static root with the same
    hidden names, or the same loopback upstream), and a 404 for any other
    name. No gate, no share, no frame header (a preview is a top-level
    page), and a request from outside the broker's subnet is refused here
    as well as in the kernel. An expired site answers 410, as it does on its
    public name. Rendered even when nothing is previewable, so the listener
    does not come and go with the list."""
    out = [
        "# The Apps section's previews: internal only, on the broker bridge's",
        "# gateway and on loopback, past the login. Served to the broker's",
        f"# bridge ({loam_broker.SUBNET}) and the box itself (Loam's app runs on",
        "# the host) only; broker/firewall.sh holds the bridge port in the kernel.",
        "# See deploy/README.md, \"Previews for the Apps section\".",
        f"http://:{PREVIEW_PORT} {{",
        f"\tbind {PREVIEW_ADDR} {PREVIEW_LOCAL_ADDR}",
        access_log_line(PREVIEW_LOG),
        f"\t@outside not remote_ip {loam_broker.SUBNET} {PREVIEW_LOCAL_NET}",
        "\thandle @outside {",
        "\t\trespond \"forbidden\" 403",
        "\t}",
    ]
    for app in rows:
        # A host label is [a-z0-9-] (NAME_RE), so these two names cannot
        # collide across apps or with each other.
        fq = f"{app.host}.{PREVIEW_SUFFIX}"
        out.append(f"\t@app_{app.host} host {fq}")
        out.append(f"\thandle @app_{app.host} {{")
        if app.site_expires is not None:
            out.append(f"\t\t@gone_{app.host} expression `int({{time.now.unix}}) >= "
                       f"{_deadline_unix(app.site_expires)}`")
            out.append(f"\t\trespond @gone_{app.host} \"expired\" 410")
        out.append(f"\t\t{app.backend}")
        out.append("\t}")
    out += [
        "\thandle {",
        "\t\trespond \"no such app\" 404",
        "\t}",
        "}",
    ]
    return "\n".join(out) + "\n"


def preview_sysctl() -> str:
    return "\n".join([
        "# GENERATED by agents/bin/gen-deploy (lib/loam_deploy.py, PREVIEW_SYSCTL).",
        f"# Caddy binds the Apps previews' route to {PREVIEW_ADDR}, the broker",
        "# bridge's gateway, which exists only once docker is up; Caddy and docker",
        "# are not ordered at boot, and one listener that cannot bind fails Caddy's",
        "# whole start. This lets the bind happen first. It makes nothing",
        "# reachable: a packet for an address the box does not carry is never",
        "# delivered.",
        f"{PREVIEW_SYSCTL_KEY} = 1",
    ]) + "\n"


def preview_sysctl_live() -> bool:
    """Whether the kernel allows the previews' early bind right now."""
    try:
        key = PREVIEW_SYSCTL_KEY.replace(".", "/")
        return Path(f"/proc/sys/{key}").read_text().strip() == "1"
    except OSError:
        return False


def claim_edge_backup() -> Path:
    return CADDYFILE.with_name(".loam-claim.previous")


def caddy_claim_block(fq: str) -> str:
    """The app's host while the install has no login yet: `/claim` from the
    claim service, outside the login, and every other path sent there. The
    token rides in the link's fragment, which a browser never sends, so no
    log line carries it; the page posts it. Gone at the next render after
    the claim, which deletes CLAIM_FILE."""
    return "\n".join([
        f"{CLAIM_MARKER} no {CLAIMED_FILE} yet, so the login has no user. The claim",
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


# The portal can serve Loam's sign-in UI, Authelia's UI or the staged selection.
# Authelia continues to own its API and remains available for enrollment.

LOGIN_MODES = ("loam", "authelia", "staged")
# Every route of Authelia's portal a person lands on while signing in, out,
# or in its settings (web/src/constants/Routes.ts in Authelia 4.39). The
# others (a password reset, a revoked code, OpenID consent) stay Authelia's.
LOGIN_ROUTES = ("/", "/2fa", "/2fa/*", "/authenticated", "/logout",
                "/settings", "/settings/*")
LOGIN_FILES = ("index.html", "login.css", "login.js", "icon.svg")
# The app's own copies of Loam's two faces (app/public/fonts), the same files
# the claim page embeds: one copy of each in the repository.
LOGIN_FONT_DIR = LOGIN_SRC.parent.parent / "app" / "public" / "fonts"
LOGIN_FONTS = (("Caprasimo", "400", "caprasimo-400-latin.woff2"),
               ("Figtree", "300 900", "figtree-400-latin.woff2"))
LATIN = ("U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, "
         "U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, "
         "U+2212, U+2215, U+FEFF, U+FFFD")
# The page's own policy, sent by Caddy with it: its script and stylesheet
# from the portal itself, fonts embedded, requests to the portal only.
LOGIN_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; "
             "img-src 'self' data:; font-src data:; connect-src 'self'; "
             "form-action 'self'; frame-ancestors 'none'; base-uri 'none'")


def login_mode() -> tuple[str, str | None]:
    """(mode, the staged host's label or None) from `login.*`."""
    mode = lc.get("login.page") or "loam"
    if mode not in LOGIN_MODES:
        raise DeployError(f"{lc.CONFIG_FILE}: login.page is one of "
                          f"{', '.join(LOGIN_MODES)}, not {mode!r}")
    if mode != "staged":
        return mode, None
    host = lc.get("login.staged_host") or "next"
    if not isinstance(host, str) or not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", host):
        raise DeployError(f"{lc.CONFIG_FILE}: login.staged_host {host!r} is one label")
    return mode, host


def login_files() -> dict[Path, str]:
    """The page, its stylesheet (fonts inside), its script and its icon, as
    LOGIN_DIR holds them. Rendered whatever `login.page` says, so turning it
    on or back is a Caddyfile change alone."""
    import base64  # noqa: PLC0415
    import hashlib  # noqa: PLC0415
    src = {name: (LOGIN_SRC / name).read_text(encoding="utf-8") for name in LOGIN_FILES}
    faces = []
    for family, weight, file in LOGIN_FONTS:
        data = base64.b64encode((LOGIN_FONT_DIR / file).read_bytes()).decode()
        faces.append(f"@font-face{{font-family:'{family}';font-style:normal;"
                     f"font-weight:{weight};font-display:swap;"
                     f"src:url(data:font/woff2;base64,{data}) format('woff2');"
                     f"unicode-range:{LATIN}}}")
    css = src["login.css"].replace("/*@@FONTS@@*/", "\n".join(faces))
    js = src["login.js"]
    version = hashlib.sha256((css + js).encode()).hexdigest()[:12]
    apex = next((a for a in records() if a.apex), None)
    mode, _ = login_mode()
    marks = {
        "@@VERSION@@": version,
        "@@HOME@@": f"https://{apex.fqdn}/" if apex else "",
        "@@REMEMBER@@": "on",            # session.remember_me is set below
        # An install's reset is off (no one reads the file notifier's mail).
        "@@RESET@@": "off" if EDGE else "on",
        "@@PASSKEYS@@": "on",            # webauthn.enable_passkey_login
        "@@KEEP@@": "loam" if mode == "staged" else "",
    }
    html = src["index.html"]
    for mark, value in marks.items():
        html = html.replace(mark, value)
    return {LOGIN_DIR / "index.html": html, LOGIN_DIR / "login.css": css,
            LOGIN_DIR / "login.js": js, LOGIN_DIR / "icon.svg": src["icon.svg"]}


def caddy_portal_block(fq: str) -> str:
    """The login portal on one name: Loam's sign-in page for the portal's own
    routes (unless `login.page` says otherwise), Authelia for everything
    else, its API above all."""
    mode, staged = login_mode()
    out = [f"{fq} {{", access_log_line(fq)]
    if mode != "authelia":
        when = ""
        if staged:
            target = f"https://{staged}.{domain()}/"
            when = (f'\t\texpression `{{query.rd}}.startsWith("{target}") '
                    f'|| {{query.page}} == "loam"`\n')
        out += [
            "\t# Loam's sign-in page (deploy/login/, rendered into "
            f"{LOGIN_DIR}). login.page = {mode!r}.",
            f"\thandle_path {LOGIN_ASSETS}/* {{",
            f"\t\troot * {LOGIN_DIR}",
            "\t\theader Cache-Control \"no-cache\"",
            "\t\theader X-Content-Type-Options nosniff",
            "\t\tfile_server",
            "\t}",
            "\t@loam_page {",
            f"\t\tpath {' '.join(LOGIN_ROUTES)}",
            "\t\tnot query authelia=*",
            when.rstrip("\n"),
            "\t}",
            "\thandle @loam_page {",
            f"\t\troot * {LOGIN_DIR}",
            "\t\trewrite * /index.html",
            "\t\theader {",
            f"\t\t\tContent-Security-Policy \"{LOGIN_CSP}\"",
            "\t\t\tCache-Control \"no-store\"",
            "\t\t\tX-Content-Type-Options nosniff",
            "\t\t\tX-Frame-Options DENY",
            "\t\t\tReferrer-Policy no-referrer",
            "\t\t}",
            "\t\tfile_server",
            "\t}",
            "\thandle {",
            f"\t\treverse_proxy 127.0.0.1:{AUTHELIA_PORT}",
            "\t}",
        ]
        out = [line for line in out if line]
    else:
        out.append(f"\treverse_proxy 127.0.0.1:{AUTHELIA_PORT}")
    out.append("}")
    return "\n".join(out) + "\n"


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
    out += ["# The login portal.", caddy_portal_block(f"{auth}.{dom}")]
    # One portal per extra domain: Authelia's session cookie is scoped to a
    # domain, so a gated host on another domain redirects to a portal on
    # that domain, and the cookie it sets is sent back to the host.
    for extra in extra_domains(apps):
        out += [f"# The login portal on {extra}, for the hosts below that carry it.",
                caddy_portal_block(f"{auth}.{extra}")]
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
    for e in landings():
        out.append(caddy_landing_block(e))
    out.append(caddy_preview_block(previewable(apps, agent_sites)))
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
    """Configure Docker's default port publishing on loopback. See deploy/README.md for the
    daemon settings.
    """
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
            # An install without mail delivery cannot offer working password-reset emails.
            "password_reset": {"disable": bool(EDGE)},
            "password_change": {"disable": False},
            "file": {"path": "/data/users_database.yml", "watch": True,
                     "search": {"case_insensitive": True}},
        },
        "access_control": {"default_policy": "deny", "rules": rules},
        "session": {
            # Use a distinct login cookie name so a cookie scoped to a former parent domain cannot
            # shadow the new session.
            "name": SESSION_COOKIE,
            "same_site": "lax",
            # Persist browser logins for the configured inactivity and remember-me intervals.
            "inactivity": "1d",
            "expiration": "7d",
            "remember_me": "1M",
            # One entry per domain. Authelia keeps a session per cookie
            # domain: the same user, the same users file, one login per
            # domain per device (per month, with remember_me), and a passkey
            # is bound to the domain it was enrolled on.
            "cookies": cookies,
            # Persist Authelia sessions in the login stack's private Redis service.
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
                # Allow the filesystem operations required by Authelia's startup checks.
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
                # Run the service binary directly under its configured user. Its image entrypoint
                # expects ownership-changing privileges.
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


# Apply the inbound policy after the broker and browser rules. Preserve established
# connections, SSH listeners, web traffic, required network control traffic and
# configured exceptions. Yield to an existing host firewall per address family.
# Container forwarding follows a separate policy.

INBOUND_DIR = ETC / "inbound"
INBOUND_FIREWALL_SCRIPT = INBOUND_DIR / "firewall.sh"
INBOUND_FIREWALL_UNIT = Path("/etc/systemd/system/loam-inbound-firewall.service")
INBOUND_WALL_SERVICE = "loam-inbound-firewall.service"
INBOUND_CHAIN = "LOAM-INBOUND"
INBOUND_TCP = (80, 443)
INBOUND_UDP = (443,)
MODES[INBOUND_FIREWALL_SCRIPT] = 0o755
MODES[loam_broker.FIREWALL_SCRIPT] = 0o755


def inbound_open() -> list[tuple[int, str]]:
    """The owner's extra ports (`firewall.open`, "22000/tcp" or 22000 for
    TCP), checked; a bad entry is a DeployError naming it."""
    raw = lc.get("firewall.open") or []
    if isinstance(raw, (str, int)):
        raw = [raw]
    out = []
    for item in raw:
        m = re.fullmatch(r"\s*(\d{1,5})(?:/(tcp|udp))?\s*", str(item))
        if not m or not 0 < int(m.group(1)) < 65536:
            raise DeployError(f"firewall.open: {item!r} is not a port like 22000 or 22000/udp")
        out.append((int(m.group(1)), m.group(2) or "tcp"))
    return sorted(set(out))


def inbound_managed() -> bool:
    return lc.get("firewall.manage", True) is not False


def inbound_firewall_script() -> str:
    tcp = sorted(set(INBOUND_TCP) | {p for p, proto in inbound_open() if proto == "tcp"})
    udp = sorted(set(INBOUND_UDP) | {p for p, proto in inbound_open() if proto == "udp"})
    lines = [
        "#!/bin/sh",
        "# GENERATED by agents/bin/gen-deploy (lib/loam_deploy.inbound_firewall_script).",
        "# Do not edit; `firewall.open` and `firewall.manage` in loam.toml are the",
        "# knobs. Default-deny inbound: SSH, 80 and 443, ping, replies, and the",
        "# owner's own ports in; everything else dropped. Stands aside, per family,",
        "# for a firewall this server already has (ufw, firewalld, an INPUT policy",
        "# of DROP), and takes its own chain out when it does.",
        "#   firewall.sh          apply (what the unit runs)",
        "#   firewall.sh status   one line: guarded by Loam / by <other> / open",
        "set -u",
        f"CHAIN={INBOUND_CHAIN}",
        f"MANAGE={'yes' if inbound_managed() else 'no'}",
        f"TCP=\"{' '.join(map(str, tcp))}\"",
        f"UDP=\"{' '.join(map(str, udp))}\"",
        "",
        "ssh_ports() {",
        "  { echo 22",
        "    sshd -T 2>/dev/null | awk '$1==\"port\"{print $2}'",
        "    ss -Hltnp 2>/dev/null | awk '/\"sshd\"/{n=split($4,a,\":\"); print a[n]}'",
        "  } | grep -E '^[0-9]+$' | sort -un",
        "}",
        "",
        "other() {  # who else guards this family's inbound, if anyone",
        "  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then",
        "    echo ufw; return; fi",
        "  if systemctl is-active --quiet firewalld 2>/dev/null; then echo firewalld; return; fi",
        "  if $1 -w 5 -S INPUT 2>/dev/null | grep -q '^-P INPUT DROP'; then",
        "    echo own-rules; return; fi",
        "}",
        "",
        "remove() {",
        "  while $1 -w 5 -C INPUT -j $CHAIN 2>/dev/null; do $1 -w 5 -D INPUT -j $CHAIN; done",
        "  $1 -w 5 -F $CHAIN 2>/dev/null; $1 -w 5 -X $CHAIN 2>/dev/null",
        "  return 0",
        "}",
        "",
        "build() {  # $1 iptables or ip6tables, $2 the ICMP protocol name",
        "  T=\"$1 -w 5\"",
        "  $T -N $CHAIN 2>/dev/null || $T -F $CHAIN",
        "  $T -A $CHAIN -i lo -j ACCEPT",
        "  $T -A $CHAIN -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT",
        "  $T -A $CHAIN -m conntrack --ctstate INVALID -j DROP",
        "  $T -A $CHAIN -p $2 -j ACCEPT",
        "  if [ \"$1\" = iptables ]; then",
        "    $T -A $CHAIN -p udp --sport 67 --dport 68 -j ACCEPT",
        "  else",
        "    $T -A $CHAIN -p udp --sport 547 --dport 546 -j ACCEPT",
        "  fi",
        "  for P in $(ssh_ports) $TCP; do $T -A $CHAIN -p tcp --dport \"$P\" -j ACCEPT; done",
        "  for P in $UDP; do $T -A $CHAIN -p udp --dport \"$P\" -j ACCEPT; done",
        "  $T -A $CHAIN -j DROP",
        "  # Last in INPUT, once: Loam's other walls sit above it.",
        "  while $T -C INPUT -j $CHAIN 2>/dev/null; do $T -D INPUT -j $CHAIN; done",
        "  $T -A INPUT -j $CHAIN",
        "}",
        "",
        "said=\"\"",
        "for FAM in iptables ip6tables; do",
        "  command -v $FAM >/dev/null 2>&1 || continue",
        "  [ $FAM = iptables ] && ICMP=icmp || ICMP=ipv6-icmp",
        "  [ $FAM = iptables ] && V=IPv4 || V=IPv6",
        "  if [ \"${1:-}\" = status ]; then",
        "    if $FAM -w 5 -C INPUT -j $CHAIN 2>/dev/null; then said=\"$said $V:loam\"",
        "    else o=$(other $FAM); said=\"$said $V:${o:-open}\"; fi",
        "    continue",
        "  fi",
        "  o=$(other $FAM)",
        "  if [ \"$MANAGE\" = no ]; then remove $FAM; said=\"$said $V:off\"",
        "  elif [ -n \"$o\" ]; then remove $FAM; said=\"$said $V:$o\"",
        "  else build $FAM $ICMP || exit 1; said=\"$said $V:loam\"; fi",
        "done",
        "echo \"inbound:$said\"",
    ]
    return "\n".join(lines) + "\n"


def inbound_firewall_unit() -> str:
    return (
        "# GENERATED by agents/bin/gen-deploy (lib/loam_deploy.inbound_firewall_unit).\n"
        "# Do not edit. Raises the default-deny wall on this server's own ports\n"
        "# at boot, before the network is up, and stands aside for a firewall the\n"
        "# server already has (lib/loam_deploy.py, \"The inbound wall\").\n"
        "[Unit]\n"
        "Description=Loam: default-deny inbound, SSH, 80 and 443 open\n"
        f"Documentation=file://{lc.ROOT}/deploy/README.md\n"
        "DefaultDependencies=no\n"
        "After=local-fs.target ufw.service firewalld.service\n"
        "Before=network-pre.target docker.service shutdown.target\n"
        "Wants=network-pre.target\n"
        "Conflicts=shutdown.target\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        "RemainAfterExit=yes\n"
        f"ExecStart={INBOUND_FIREWALL_SCRIPT}\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def inbound_wall_status() -> str:
    """One line: who guards inbound, per family, as the live script says
    (``inbound: IPv4:loam IPv6:loam``). Empty when the script is absent."""
    if not INBOUND_FIREWALL_SCRIPT.exists():
        return ""
    run = _sudo([str(INBOUND_FIREWALL_SCRIPT), "status"])
    return run.stdout.strip()


def inbound_wall_words(status: str) -> str:
    """The install's and update's one line about the wall, from a status
    or apply line."""
    fams = dict(x.split(":", 1) for x in status.removeprefix("inbound:").split() if ":" in x)
    if not fams:
        return "Inbound firewall: not checked (no iptables here)."
    vals = set(fams.values())
    if vals == {"loam"}:
        return ("Inbound firewall on: only SSH, 80 and 443 reach this server from "
                "outside, so nothing an agent starts is on the open web by accident.")
    if vals == {"off"}:
        return ("Inbound firewall: left off (firewall.manage = false in loam.toml); "
                "this server's ports are as its provider leaves them.")
    if "open" in vals:
        return (f"Inbound firewall: NOT on ({status}); check "
                f"`sudo {INBOUND_FIREWALL_SCRIPT} status`.")
    names = {"own-rules": "its own rules, INPUT policy DROP"}
    others = sorted(names.get(v, v) for v in vals if v not in ("loam", "off"))
    return (f"Inbound firewall: this server's own ({', '.join(others)}) already guards it; "
            "Loam left it as it is.")


def _subid_base(path: Path, name: str = "dockremap") -> int:
    """Base host id for Docker's default user namespace, or 0 if unremapped."""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.strip().split(":")
            if len(parts) >= 3 and parts[0] == name and parts[1].isdigit():
                return int(parts[1])
    except OSError:
        pass
    return 0


def docker_root_owner(subuid: Path = Path("/etc/subuid"),
                      subgid: Path = Path("/etc/subgid")) -> tuple[int, int]:
    """Host (uid, gid) that maps to root (0:0) inside userns-remapped containers."""
    return (_subid_base(subuid), _subid_base(subgid))


def login_data_dir() -> Path | None:
    """Host directory backing `authelia-data` when `storage.data_dir` is set,
    or None on an ordinary install (which lets Docker manage the volume)."""
    if lc.DATA_DIR is None:
        return None
    import loam_data_disk as dd  # noqa: PLC0415
    return dd.login_data_dir(lc.DATA_DIR)


def browser_output_owner(subuid: Path = Path("/etc/subuid"),
                         subgid: Path = Path("/etc/subgid")) -> tuple[int, int]:
    """Host (uid, gid) that maps to `node` (1000:1000) inside loam-browser."""
    return (_subid_base(subuid) + BROWSER_UID,
            _subid_base(subgid) + BROWSER_GID)


def browser_compose() -> str:
    output = str(BROWSER_OUTPUT)
    doc = {
        "name": BROWSER_PROJECT,
        "services": {
            "browser": {
                "image": BROWSER_IMAGE,
                "container_name": BROWSER_PROJECT,
                "restart": "unless-stopped",
                "working_dir": output,
                "entrypoint": ["node", "/app/cli.js"],
                "command": [
                    "--headless", "--browser", "chromium", "--no-sandbox",
                    "--isolated",
                    # Keep page-advertised WebMCP tools out of the persona's browser tool set.
                    "--no-webmcp",
                    # Write all screenshots and snapshots into the shared host-mounted output
                    # directory and always report their absolute host path on every provider lane.
                    "--ignore-roots",
                    "--output-dir", output,
                    "--file-paths", "absolute",
                    "--output-max-size", str(BROWSER_OUTPUT_MAX_BYTES),
                    "--host", "0.0.0.0", "--port", str(BROWSER_PORT),
                    "--allowed-hosts", BROWSER_ALLOWED_HOSTS,
                    # Use the browser's origin restrictions alongside the kernel network boundary. The
                    # browser policy also covers its own loopback.
                    "--blocked-origins", browser_blocked_origins(),
                ],
                "ports": [f"127.0.0.1:{BROWSER_PORT}:{BROWSER_PORT}"],
                "volumes": [f"{output}:{output}"],
                "networks": ["sight"],
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                "ulimits": {"core": 0},
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
        loam_broker.FIREWALL_SCRIPT: (Path(__file__).resolve().parents[1] / "broker/firewall.sh").read_text(),
        CADDYFILE: caddyfile(today),
        DAEMON_JSON: daemon_json(),
        AUTHELIA_CONFIG: authelia_config(),
        AUTHELIA_COMPOSE: authelia_compose(),
        BROWSER_COMPOSE: browser_compose(),
        SIGHT_FIREWALL_SCRIPT: sight_firewall_script(),
        SIGHT_FIREWALL_UNIT: sight_firewall_unit(),
        INBOUND_FIREWALL_SCRIPT: inbound_firewall_script(),
        INBOUND_FIREWALL_UNIT: inbound_firewall_unit(),
        SUDOERS: sudoers(),
        PREVIEW_SYSCTL: preview_sysctl(),
        SERVICE_UNIT: service_unit(),
        **login_files(),
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
