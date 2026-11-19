"""One install of Loam: what the installer writes, and what `loam status` reads.

First release, job L12 (2026-11-14). `install.sh` at the repository's root is
the one command a person runs; it does the eleven steps of the design in
order and calls this module for everything that is rendered rather than run:
the install's `/etc/loam/loam.toml`, its `_shared/reach.md`, the name check,
the claim's token and link, and the status of every part. `bin/loam-await-dns`,
`bin/loam-claim` and `bin/loam-status` are its other callers. docs/install.md
is the whole account.

Nothing here prints a secret. The claim's token is the one secret this module
makes; it is written root-only to `/etc/loam/claim` and read back only by
`claim_link()`, whose callers (`loam status`, the installer's last line, the
DNS timer's post) show it to the person who ran the install as root.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
import os
import re
import secrets
import socket
import ssl
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

# The provider builds this release was tested on are lib/loam_pins.py's
# PINS, the one place (2026-11-19); step 7 installs exactly those.

# The base domain every person's name lives under (decision 1, 11-14). The one
# place the product's domain is written; every other file reads `edge.domain`.
BASE_DOMAIN = "myloam.space"
# Where a name under BASE_DOMAIN is asked for: an issue on Loam's own
# repository, the one public way to reach Loam's makers.
ASK_FOR_A_NAME = "https://github.com/loamhq/loam/issues/new"

USER = "loam"                                  # the Unix user an install runs as
STATE_DIR = Path("/var/lib/loam")              # root's: the installer's own record
STEPS_DIR = STATE_DIR / "install"              # one `<n>-<name>.done` per finished step
CONFIG = Path("/etc/loam/loam.toml")
CLAIM_FILE = Path("/etc/loam/claim")           # root-only, while unclaimed
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}[a-z0-9]$")
# Names that are this product's own hosts, or would read as them.
RESERVED = {"www", "auth", "api", "app", "admin", "mail", "files", "vault", "docs",
            "status", "loam", "help", "support", "blog", "next", "test", "dev",
            "staging", "second-factor", "ns1", "ns2"}
# The label the wildcard check asks for. A record for `*.<name>` answers it.
PROBE_LABEL = "loam-check"

STEPS = (
    (1, "preflight", "Checking this server"),
    (2, "name", "Checking the name"),
    (3, "packages", "Installing the system's software"),
    (4, "user", "Making Loam's user"),
    (5, "loam", "Putting Loam in place"),
    (6, "vault", "Making your vault"),
    (7, "providers", "Installing the AI tools"),
    (8, "starter", "Making Iris and your first conversation"),
    (9, "units", "Starting Loam's services"),
    (10, "edge", "Putting Loam on the web"),
    (11, "claim", "Making your sign-in link"),
)


# ---------------------------------------------------------------------------
# The steps' record
# ---------------------------------------------------------------------------

def took(seconds: float) -> str:
    """How long the install took, in the words the last line says it in.
    The installer used to promise "about fifteen minutes"; both rehearsals
    of 2026-11-16 took about two (the first-day walk), so it now promises
    "a few minutes" and says what it actually took."""
    minutes = round(seconds / 60)
    if seconds < 60:
        return "under a minute"
    if minutes <= 1:
        return "about a minute"
    return f"about {minutes} minutes"


def step_file(n: int) -> Path:
    name = dict((k, v) for k, v, _ in STEPS)[n]
    return STEPS_DIR / f"{n:02d}-{name}.done"


def first_unfinished() -> int | None:
    for n, _, _ in STEPS:
        if not step_file(n).exists():
            return n
    return None


# ---------------------------------------------------------------------------
# The configuration an install starts with
# ---------------------------------------------------------------------------

def valid_name(name: str) -> str | None:
    """Why a name cannot be used, or None."""
    if not NAME_RE.match(name or ""):
        return ("a name is 3 to 32 characters: lowercase letters, digits and "
                "hyphens, starting with a letter and not ending with a hyphen")
    if "--" in name:
        return "a name may not have two hyphens in a row"
    if name in RESERVED:
        return f"\"{name}\" is reserved"
    return None


def toml_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_config(*, name: str, owner: str, timezone: str, address: str,
                  home: str, user: str = USER, base: str = BASE_DOMAIN,
                  resolver: str = "public", tls: str = "acme",
                  root: str | None = None, domain: str | None = None) -> str:
    """The install's /etc/loam/loam.toml. Every key docs/config.md lists that
    a fresh install needs spelled out, and the `[edge]` table that makes the
    box one person's name. With `domain`, the install is on a domain its
    owner has (`own_domain = true`): `name` is then only that domain's first
    label, kept for anything that wants a short name."""
    own = bool(domain)
    if own:
        domain = domain.lower().rstrip(".")
        name = domain.split(".", 1)[0]
    else:
        domain = f"{name}.{base}"
    root = root or f"{home}/loam"
    today = dt.date.today().isoformat()
    return f"""\
# This Loam's configuration: where everything is, who it is for, and its
# name on the web. Written by install.sh on {today}; yours from then on (an
# update never rewrites it). Every key is in docs/config.md and
# docs/install.md. No secret is in this file.

user = {toml_str(user)}
timezone = {toml_str(timezone)}

[owner]
id = "owner"
name = {toml_str(owner)}

[paths]
home = {toml_str(home)}
root = {toml_str(root)}
vault = {toml_str(home + "/vault")}
workspaces = {toml_str(home + "/agents")}
projects = {toml_str(home + "/projects")}
plugins = {toml_str(home + "/plugins")}

[git.autocommit]
name = "loam"
email = {toml_str("loam@" + domain)}

[edge]
name = {toml_str(name)}
domain = {toml_str(domain)}
address = {toml_str(address)}
app = "loam-ui"
app_checkout = "@ROOT@"
app_context = "app"
apps = ["loam-ui", "files", "vault"]
resolver = {toml_str(resolver)}
tls = {toml_str(tls)}
dns_gate = true
""" + ("own_domain = true\n" if own else "")


def reach_text(lc=None) -> str:
    """`<workspaces>/_shared/reach.md` for a new install: the pointer to the
    library page every agent reads, and this install's own names. Written
    once by the installer; the person's and the agents' file from then on,
    and an update never touches it."""
    if lc is None:
        import loam_config as lc  # noqa: PLC0415
    import loam_deploy as dep  # noqa: PLC0415
    domain = dep.domain()
    vault, ws, root = lc.VAULT, lc.WORKSPACES, lc.ROOT
    system = vault / lc.vault_folder("system")
    skill = system / "Loam" / "skills" / "loam-what-you-can-reach" / "SKILL.md"
    today = dt.date.today().isoformat()
    return f"""\
# What you can reach

Every agent loads this file on every run. It is this Loam's own: any agent
may edit it to keep it true (add a capability, retire one that is gone) and
leave a dated line under *Changes*. An update of Loam never touches it. Keep
it short: it is paid for on every run.

**What you can reach and use, and how** (your reach, your own subdomains,
threads and boards, settings, the Market, the reviewer) is the shared skill
`loam-what-you-can-reach`, at `{skill}`. Read it when you need to know
whether or how you can do something.

**This install's names for it:**

- Workspaces: an agent's is `{ws}/<name>/`; the shared `commons/` is
  `{ws}/commons/`. The vault is `{vault}/`. Full shell and sudo, on every
  lane.
- The domain is `{domain}`: the app at https://{domain}/, the vault
  read-only at https://vault.{domain}/, the commons' `www/` at
  https://files.{domain}/, your own sites at `<site>.{domain}`
  (`mcp__engineer__site`, or `{root}/bin/site`).
- Thread records are in `{vault / lc.vault_folder("conversations")}/` (never
  edit one). Boards are in `{vault / lc.vault_folder("boards")}/`; General's is
  `{vault / lc.vault_folder("general_board")}`. Post with `mcp__engineer__thread`
  or `{root}/bin/thread post general "…"`.
- Settings: `mcp__engineer__settings`, or `{root}/bin/settings`.
- Whether everything is running: `loam status` in a shell.

## Changes

- {today}, the installer: written from this install's configuration.
"""


# ---------------------------------------------------------------------------
# The name on the web
# ---------------------------------------------------------------------------

def primary_address() -> str | None:
    """The IPv4 address this box sends from, from the kernel's route."""
    run = subprocess.run(["ip", "-4", "-j", "route", "get", "1.1.1.1"],
                         capture_output=True, text=True, check=False)
    try:
        return json.loads(run.stdout)[0].get("prefsrc")
    except (ValueError, IndexError, KeyError, TypeError):
        return None


def is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.version == 4 and ip.is_global


def _doh(name: str) -> set[str] | None:
    """A records by DNS over HTTPS from two public resolvers, so the answer
    is the internet's and not a cache on this box. None if neither answered."""
    for base in ("https://1.1.1.1/dns-query", "https://dns.google/resolve"):
        url = f"{base}?{urllib.parse.urlencode({'name': name, 'type': 'A'})}"
        req = urllib.request.Request(url, headers={"accept": "application/dns-json",
                                                   "user-agent": "loam-install"})
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
        except (OSError, ValueError):
            continue
        if data.get("Status") not in (0, 3):    # NOERROR, NXDOMAIN
            continue
        return {a["data"] for a in data.get("Answer") or [] if a.get("type") == 1}
    return None


def resolve(name: str, resolver: str = "public") -> set[str] | None:
    """The name's IPv4 addresses (empty: it does not resolve), or None when
    no resolver could be asked. `system` asks this box's own resolver, which
    reads /etc/hosts: a rehearsal's `.test` names live there."""
    if resolver == "system":
        try:
            return {ai[4][0] for ai in socket.getaddrinfo(name, None, socket.AF_INET)}
        except socket.gaierror as exc:
            if exc.errno in (socket.EAI_NONAME, getattr(socket, "EAI_NODATA", -5)):
                return set()
            return None
    return _doh(name)


def name_check(name: str, address: str, *, base: str = BASE_DOMAIN,
               resolver: str = "public") -> dict:
    """What DNS says about `<name>.<base>` and `*.<name>.<base>`.

    `state` is one of:
      here       both resolve to this box: the edge can go up now
      taken      the name points somewhere else: someone else's
      unset      nothing points it anywhere yet, or only the base domain's
                 wildcard does (Pushback 2: `*.<BASE_DOMAIN>` answers every
                 name today, so a name that answers like a made-up one is
                 not anybody's)
      unknown    no resolver could be asked
    """
    domain = f"{name}.{base}"
    apex = resolve(domain, resolver)
    probe = resolve(f"{PROBE_LABEL}.{domain}", resolver)
    nobody = resolve(f"loam-nobody-{secrets.token_hex(6)}.{base}", resolver)
    out = {"domain": domain, "address": address,
           "apex": sorted(apex) if apex is not None else None,
           "wildcard": sorted(probe) if probe is not None else None,
           "base_wildcard": sorted(nobody) if nobody else []}
    if apex is None or probe is None:
        return {**out, "state": "unknown"}
    if apex == {address} and probe == {address}:
        return {**out, "state": "here"}
    if apex and apex != {address} and apex != (nobody or set()):
        return {**out, "state": "taken"}
    return {**out, "state": "unset"}


def records_to_ask_for(name: str, address: str, base: str = BASE_DOMAIN) -> list[str]:
    return [f"{name}.{base}  A  {address}", f"*.{name}.{base}  A  {address}"]


# ---------------------------------------------------------------------------
# A domain of the owner's own (Loam 0.2.8)
# ---------------------------------------------------------------------------
#
# Instead of a name under BASE_DOMAIN, which Loam's makers make the records
# for, an install can live on a domain its owner has: `loam.example.com`, or
# `example.com` itself. The owner makes the same two A records at their DNS
# provider; the installer says which, checks them, says plainly what is
# still missing, and goes on the web by itself once they are right.
#
# Certificates are Caddy's default: one Let's Encrypt certificate per host,
# by the HTTP-01 / TLS-ALPN-01 challenges on ports 80 and 443. No wildcard
# certificate is needed, because every host (the app, auth., vault., files.,
# each agent's site) is its own block in the Caddyfile, and the wildcard A
# record makes every one of them resolve. A wildcard certificate would need
# DNS-01, which needs an API key for the owner's DNS provider and a Caddy
# built with that provider's module: a second thing to set up, for nothing
# the per-host route does not already give.

def valid_domain(domain: str) -> str | None:
    """Why `domain` cannot be an install's own domain, or None."""
    d = (domain or "").strip().lower().rstrip(".")
    if d.startswith(("http://", "https://")) or "/" in d or ":" in d:
        return "give the domain alone, without https:// or a path (for example loam.example.com)"
    if d.startswith("*."):
        return "give the domain itself, without *. (the installer asks for the wildcard too)"
    labels = d.split(".")
    if len(d) > 200 or len(labels) < 2 or not all(
            re.match(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", x) for x in labels) \
            or not re.match(r"^[a-z][a-z0-9-]*$", labels[-1]):
        return "that is not a domain name (for example loam.example.com)"
    if d == BASE_DOMAIN or d.endswith("." + BASE_DOMAIN):
        return (f"names under {BASE_DOMAIN} are Loam's makers' to give: ask them for "
                f"one in an issue at {ASK_FOR_A_NAME} and run the install with --name "
                f"instead")
    return None


def own_records(domain: str, address: str) -> list[str]:
    """The two records an own domain needs, as the installer prints them."""
    return [f"{domain}  A  {address}", f"*.{domain}  A  {address}"]


def edge_records(edge: dict) -> list[str]:
    """The two records an install's `[edge]` needs, either kind."""
    address = edge.get("address", "?")
    if edge.get("own_domain"):
        return own_records(edge["domain"], address)
    return records_to_ask_for(edge["name"], address, edge["domain"].split(".", 1)[1])


def _resolve6(name: str, resolver: str = "public") -> set[str] | None:
    """The name's IPv6 addresses, asked the same way as `resolve`."""
    if resolver == "system":
        try:
            return {ai[4][0].split("%")[0]
                    for ai in socket.getaddrinfo(name, None, socket.AF_INET6)}
        except socket.gaierror as exc:
            if exc.errno in (socket.EAI_NONAME, getattr(socket, "EAI_NODATA", -5)):
                return set()
            return None
    for base in ("https://1.1.1.1/dns-query", "https://dns.google/resolve"):
        url = f"{base}?{urllib.parse.urlencode({'name': name, 'type': 'AAAA'})}"
        req = urllib.request.Request(url, headers={"accept": "application/dns-json",
                                                   "user-agent": "loam-install"})
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
        except (OSError, ValueError):
            continue
        if data.get("Status") not in (0, 3):
            continue
        return {a["data"] for a in data.get("Answer") or [] if a.get("type") == 28}
    return None


def own_addresses() -> set[str]:
    """Every address on this box's interfaces (`ip -j addr`)."""
    run = subprocess.run(["ip", "-j", "addr"], capture_output=True, text=True, check=False)
    try:
        return {a["local"] for link in json.loads(run.stdout)
                for a in link.get("addr_info", []) if a.get("local")}
    except (ValueError, TypeError, KeyError):
        return set()


# Cloudflare's published IPv4 ranges (https://www.cloudflare.com/ips-v4/).
# A record that answers with one of these is proxied (the orange cloud):
# the person's DNS is right but the proxy hides this server, and Let's
# Encrypt's challenge and the agents' sites need it to say "DNS only".
CLOUDFLARE = tuple(ipaddress.ip_network(n) for n in (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22"))


def _proxied(addrs) -> bool:
    for a in addrs or ():
        try:
            ip = ipaddress.ip_address(a)
        except ValueError:
            continue
        if any(ip in n for n in CLOUDFLARE):
            return True
    return False


def domain_check(domain: str, address: str, *, resolver: str = "public",
                 mine: set[str] | None = None) -> dict:
    """What DNS says about an own domain: `domain` and `*.domain` (asked as
    `loam-check.<domain>`), each A and AAAA.

    `state` is one of:
      here       both A records are this box's, and no AAAA record points
                 anywhere else: the edge can go up now
      waiting    something is missing or points elsewhere; `problems` says
                 what, one plain sentence per record, and what to do
      unknown    no resolver could be asked

    Unlike a name under BASE_DOMAIN there is no "taken": the domain is the
    person's, so a record pointing elsewhere is one for them to change (an
    old server, a parking page, a proxy), not someone else's claim.
    An AAAA record that is not this box's matters because Let's Encrypt
    asks over IPv6 first when a name has one, and the challenge would then
    reach another server."""
    probe = f"{PROBE_LABEL}.{domain}"
    a_apex, a_probe = resolve(domain, resolver), resolve(probe, resolver)
    six_apex, six_probe = _resolve6(domain, resolver), _resolve6(probe, resolver)
    out = {"domain": domain, "address": address,
           "apex": sorted(a_apex) if a_apex is not None else None,
           "wildcard": sorted(a_probe) if a_probe is not None else None,
           "apex6": sorted(six_apex or []), "wildcard6": sorted(six_probe or [])}
    if a_apex is None or a_probe is None:
        return {**out, "state": "unknown", "problems": [
            "DNS could not be asked just now (no answer from the resolvers); "
            "it is asked again in a moment"]}
    mine = own_addresses() if mine is None else mine
    mine = set(mine) | {address}
    problems: list[str] = []
    for label, shown, got, got6 in ((domain, domain, a_apex, six_apex),
                                    (probe, f"*.{domain}", a_probe, six_probe)):
        if not got:
            problems.append(f"{shown} has no A record yet: make one pointing to {address}")
        elif got != {address}:
            problems.append(f"{shown} points to {', '.join(sorted(got))}, not to this "
                            f"server: change its A record to {address}")
        stray6 = sorted(set(got6 or ()) - mine)
        if stray6:
            problems.append(f"{shown} also has an IPv6 (AAAA) record, {', '.join(stray6)}, "
                            "that is not this server: delete it, or the certificate "
                            "cannot be made")
    if _proxied((a_apex or set()) | (a_probe or set())):
        problems.append("these answers are Cloudflare's own addresses, so the records are "
                        "proxied: in Cloudflare's DNS page, set both records to \"DNS only\" "
                        "(the grey cloud)")
    return {**out, "state": "waiting" if problems else "here", "problems": problems}


def edge_check(edge: dict) -> dict:
    """The DNS check an install's `[edge]` needs, either kind. The state is
    the name check's (here, taken, unset, unknown) or the own domain's
    (here, waiting, unknown)."""
    resolver = edge.get("resolver", "public")
    if edge.get("own_domain"):
        return domain_check(edge["domain"], edge.get("address", ""), resolver=resolver)
    return name_check(edge["name"], edge.get("address", ""),
                      base=edge["domain"].split(".", 1)[1], resolver=resolver)


# ---------------------------------------------------------------------------
# The certificate
# ---------------------------------------------------------------------------

CADDY_LOCAL_ROOT = Path("/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt")


def certificate(domain: str, *, internal: bool = False, timeout: float = 6) -> dict:
    """Whether this box's Caddy presents a certificate for `domain` that a
    browser would accept, asked on loopback with the name as SNI. With
    `internal`, the trust is Caddy's own authority (a rehearsal)."""
    ctx = ssl.create_default_context()
    if internal:
        try:
            ctx = ssl.create_default_context(cafile=str(CADDY_LOCAL_ROOT))
        except (OSError, ssl.SSLError) as exc:
            return {"ok": False, "why": f"no local authority yet ({exc.__class__.__name__})"}
    try:
        with socket.create_connection(("127.0.0.1", 443), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=domain) as tls:
                cert = tls.getpeercert()
    except ssl.SSLCertVerificationError as exc:
        return {"ok": False, "why": exc.verify_message or "not trusted"}
    except (OSError, ssl.SSLError) as exc:
        return {"ok": False, "why": str(exc) or exc.__class__.__name__}
    issuer = dict(x[0] for x in cert.get("issuer", ()))
    return {"ok": True, "issuer": issuer.get("organizationName") or issuer.get("commonName"),
            "until": cert.get("notAfter")}


# ---------------------------------------------------------------------------
# The claim
# ---------------------------------------------------------------------------

def mint_claim() -> str:
    """The claim's token, made once and kept root-only until the claim. A
    second call returns the same token, so a re-run prints the same link."""
    try:
        data = json.loads(CLAIM_FILE.read_text(encoding="utf-8"))
        if data.get("token"):
            return data["token"]
    except (OSError, ValueError):
        pass
    token = secrets.token_urlsafe(24)
    CLAIM_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CLAIM_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"token": token, "made": dt.datetime.now(dt.timezone.utc)
                   .isoformat(timespec="seconds")}, fh)
    os.replace(tmp, CLAIM_FILE)
    return token


def claim_token() -> str | None:
    try:
        return json.loads(CLAIM_FILE.read_text(encoding="utf-8")).get("token") or None
    except (OSError, ValueError):
        return None


def claim_link(domain: str) -> str | None:
    token = claim_token()
    return f"https://{domain}/claim#{token}" if token else None
