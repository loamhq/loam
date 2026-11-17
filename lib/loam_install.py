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

# The provider builds this release was tested on. The installer installs
# these exactly (Claude Code and Codex by the same code provider-follow uses);
# Antigravity's own script installs its latest and the CLI updates itself, so
# its number is the one the release was tested with, not a pin.
TESTED = {"claude-code": "2.1.325", "codex": "0.169.0", "antigravity": "1.5.9"}

# The base domain every person's name lives under (decision 1, 11-14). The one
# place the product's domain is written; every other file reads `edge.domain`.
BASE_DOMAIN = "myloam.space"

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


def steps_done() -> list[int]:
    return [n for n, _, _ in STEPS if step_file(n).exists()]


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
                  root: str | None = None) -> str:
    """The install's /etc/loam/loam.toml. Every key docs/config.md lists that
    a fresh install needs spelled out, and the `[edge]` table that makes the
    box one person's name."""
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
"""


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
threads and boards, settings, the reviewer) is the shared skill
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
