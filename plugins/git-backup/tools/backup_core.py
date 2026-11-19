"""Git backup: the vault and AI Spaces, pushed to the owner's own repositories.

Loam commits each tree on this machine every fifteen minutes whether this
plugin is on or not (lib/loam_autocommit.py, the recovery floor). This adds
the copy off the machine: one private repository per tree, on any git host,
reached over SSH with a deploy key this plugin makes for that one repository.
docs/backup.md is the whole design; this file is its machine half, shared by
the settings page's server (server/main.py), the agents' tool
(tools/backup_mcp.py), the timer and the shell (tools/backup.py).

It is first-party and ships with Loam, so it imports Loam's own lib/ (the
commit routine's lock and records, the settings, the owner's inbox).

Where things are:
  <private>/keys/<tree>, .pub   the deploy key pair (0600; never in a tree)
  <private>/known_hosts         the hosts' keys, learnt on first contact
  <private>/state.json          each tree's last push, its failure, what was told
  _settings/git-backup/values.json  the remotes and how often (plugin.git-backup.*)
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.dont_write_bytecode = True
if os.environ.get("LOAM_LIB"):
    sys.path.insert(0, os.environ["LOAM_LIB"])
else:  # run from the checkout without the host's environment
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
import loam_autocommit as ac  # noqa: E402
import loam_config as lc  # noqa: E402

PID = "git-backup"
TREES = {"vault": "Vault", "spaces": "AI Spaces"}
NOUN = {"vault": "vault", "spaces": "AI Spaces"}  # in a sentence: "your vault"
SETTING = {"vault": "vault_remote", "spaces": "spaces_remote"}
EVERY = {"15m": 15 * 60, "1h": 3600, "6h": 6 * 3600, "1d": 86400}
EVERY_WORDS = {"15m": "every 15 minutes", "1h": "every hour",
               "6h": "every 6 hours", "1d": "once a day"}
DEFAULTS = {"vault_remote": "", "spaces_remote": "", "every": "15m"}
# A push with nothing new still talks to the host this often, so a revoked
# key is found even on a quiet day.
VERIFY_S = 6 * 3600
# How long a network failure may last before the owner hears of it; any
# other failure is told at its second attempt in a row.
NETWORK_GRACE_S = 2 * 3600
PUSH_TIMEOUT_S = 45 * 60
PROBE_TIMEOUT_S = 25
LINK = "Settings › Plugins › Git backup"
BRANCH = "main"


class BackupError(Exception):
    """Something the person or agent asked for that cannot be done; the
    message is the sentence they read."""


# -- where --------------------------------------------------------------------

def private() -> Path:
    raw = os.environ.get("LOAM_PLUGIN_PRIVATE")
    path = Path(raw) if raw else Path(lc.STATE) / "plugins" / "private" / PID
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def settings_file() -> Path:
    raw = os.environ.get("LOAM_PLUGIN_SETTINGS_DIR")
    if raw:
        return Path(raw) / "values.json"
    import loam_plugins  # noqa: PLC0415
    return loam_plugins.yours_root() / "_settings" / PID / "values.json"


def tree_path(tree: str) -> Path:
    return ac.trees()[tree]


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(when: datetime | None = None) -> str:
    return (when or now()).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str | None) -> datetime | None:
    try:
        return datetime.strptime(text or "", "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# -- settings -----------------------------------------------------------------

def values() -> dict:
    try:
        data = json.loads(settings_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    out = dict(DEFAULTS)
    if isinstance(data, dict):
        out.update({k: v for k, v in data.items() if k in DEFAULTS and isinstance(v, str)})
    if out["every"] not in EVERY:
        out["every"] = DEFAULTS["every"]
    return out


def set_value(key: str, value: str) -> None:
    """Through the host's own setter, the one `bin/settings set
    plugin.git-backup.<key>` reaches, so the page, the agents and the shell
    write one file one way."""
    import loam_contributions  # noqa: PLC0415
    try:
        loam_contributions.set_setting(PID, key, value)
    except loam_contributions.ContributionError as exc:
        raise BackupError(f"Git backup is off, so its settings cannot be changed ({exc}).")


# -- addresses ----------------------------------------------------------------

SCP = re.compile(r"^(?:(?P<user>[A-Za-z0-9._-]+)@)?(?P<host>[A-Za-z0-9._-]+):(?P<path>(?!/)[^\s]+|/[^\s]+)$")
SSH_URL = re.compile(r"^ssh://(?:(?P<user>[^@/\s]+)@)?(?P<host>[A-Za-z0-9._-]+)(?::(?P<port>\d+))?/(?P<path>[^\s]+)$")
HTTPS_URL = re.compile(r"^https?://(?:[^@/\s]+@)?(?P<host>[A-Za-z0-9._-]+)(?::\d+)?/(?P<path>[^\s?#]+?)/?$")


def normalise(text: str) -> str:
    """What a person pastes, as the address the push uses. A web or HTTPS
    address on any host becomes `git@host:owner/repo.git`, because a deploy
    key works over SSH only. A folder on this machine or a drive attached to
    it (`/srv/backups/vault.git`, `~/backups/vault.git`, `file://...`) is
    kept as it is, with `~` spelled out."""
    raw = (text or "").strip()
    if not raw:
        return ""
    if raw == "~" or raw.startswith("~/"):
        return str(Path(raw).expanduser())
    if raw.startswith("file://") or raw.startswith("/"):
        return raw
    m = HTTPS_URL.match(raw)
    if m:
        path = m["path"]
        # A web page inside the repository (/tree/main, /-/settings, ...).
        parts = [p for p in path.split("/") if p]
        if "-" in parts:
            parts = parts[:parts.index("-")]
        if len(parts) > 2 and m["host"] in ("github.com", "www.github.com"):
            parts = parts[:2]
        if len(parts) < 2:
            raise BackupError("That address names no repository: it should end in "
                              "owner/repository.")
        path = "/".join(parts)
        if not path.endswith(".git"):
            path += ".git"
        host = m["host"].removeprefix("www.")
        return f"git@{host}:{path}"
    if SSH_URL.match(raw) or SCP.match(raw):
        return raw
    raise BackupError("That is not a repository address. Paste the repository's SSH "
                      "address (it looks like git@host:you/vault.git), its page's "
                      "address, or the path of a folder on this machine "
                      "(like /srv/backups/vault.git).")


def is_folder(url: str) -> bool:
    """A remote that is a folder on this machine (or a drive attached to it),
    which needs no key and no host."""
    return (url or "").startswith(("/", "file://"))


def folder_path(url: str) -> Path:
    return Path(url.removeprefix("file://"))


def prepare_folder(url: str) -> None:
    """A folder remote made ready to receive: a new or empty folder becomes
    an empty bare repository; one that is already a repository is left as
    it is; anything else is refused, so nobody's files are written into."""
    where = folder_path(url)
    try:
        if where.exists():
            if not where.is_dir():
                raise BackupError(f"{where} is a file, not a folder. Choose a folder, or "
                                  f"a new name for Loam to make one.")
            if (where / "HEAD").is_file() and (where / "objects").is_dir():
                return
            if (where / ".git").exists():
                raise BackupError(f"{where} is a working copy, which cannot be pushed to. "
                                  f"Choose an empty folder, or a new name for Loam to make one.")
            if any(where.iterdir()):
                raise BackupError(f"{where} already holds other files. Choose an empty "
                                  f"folder, or a new name for Loam to make one.")
        elif not where.parent.is_dir():
            raise BackupError(f"There is no folder {where.parent} on this machine. If it "
                              f"is on a drive, attach it and try again.")
        where.mkdir(exist_ok=True)
    except PermissionError:
        raise BackupError(f"Loam is not allowed to write in {where}. Choose a folder it "
                          f"can write to.") from None
    done = subprocess.run(["git", "init", "-q", "--bare", "-b", BRANCH, str(where)],
                          capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise BackupError(f"Could not make a repository in {where}: "
                          f"{done.stderr.strip()[-200:] or 'no reason given'}")


def parts(url: str) -> dict:
    """{user, host, port, path} of an SSH address; {} for a local folder."""
    if is_folder(url):
        return {}
    m = SSH_URL.match(url)
    if m:
        return {"user": m["user"], "host": m["host"], "port": m["port"], "path": m["path"]}
    m = SCP.match(url)
    if m:
        return {"user": m["user"], "host": m["host"], "port": None,
                "path": m["path"].lstrip("/")}
    return {}


def real_host(host: str) -> str:
    """The host an SSH alias stands for (`github-vault` -> `github.com`), by
    asking ssh itself; the name as given when it is no alias."""
    if not host:
        return host
    try:
        done = subprocess.run(["ssh", "-G", host], capture_output=True, text=True,
                              timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return host
    for line in done.stdout.splitlines():
        if line.lower().startswith("hostname "):
            return line.split(None, 1)[1].strip()
    return host


def web_address(url: str) -> str | None:
    """The repository's page, for a link: https://<host>/<owner>/<repo>."""
    p = parts(url)
    if not p:
        return None
    host = real_host(p["host"])
    path = p["path"].removesuffix(".git").strip("/")
    return f"https://{host}/{path}" if path else None


def host_kind(url: str) -> str:
    p = parts(url)
    host = real_host(p["host"]).lower() if p else ""
    if host.endswith("github.com"):
        return "github"
    if "gitlab" in host:
        return "gitlab"
    if "codeberg" in host or "gitea" in host or "forgejo" in host:
        return "gitea"
    return "other" if p else "folder"


def deploy_key_page(url: str) -> str | None:
    """Where the person adds the deploy key, when the host is one we know."""
    web, kind = web_address(url), host_kind(url)
    if not web:
        return None
    return {"github": f"{web}/settings/keys/new",
            "gitlab": f"{web}/-/settings/repository#js-deploy-keys-settings",
            "gitea": f"{web}/settings/keys"}.get(kind)


# -- keys ---------------------------------------------------------------------

def key_path(tree: str) -> Path:
    return private() / "keys" / tree


def public_key(tree: str) -> str | None:
    try:
        return key_path(tree).with_suffix(".pub").read_text(encoding="utf-8").strip()
    except OSError:
        return None


def make_key(tree: str) -> str:
    """The tree's deploy key, made once. Returns the public half."""
    path = key_path(tree)
    if path.exists() and path.with_suffix(".pub").exists():
        return public_key(tree) or ""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    for p in (path, path.with_suffix(".pub")):
        p.unlink(missing_ok=True)
    import socket  # noqa: PLC0415
    comment = f"loam-backup-{tree}@{socket.gethostname()}"
    done = subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment,
                           "-f", str(path)], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise BackupError(f"Could not make a key: {done.stderr.strip()[-200:]}")
    os.chmod(path, 0o600)
    return public_key(tree) or ""


def forget_key(tree: str) -> None:
    for p in (key_path(tree), key_path(tree).with_suffix(".pub")):
        p.unlink(missing_ok=True)


def fingerprint(tree: str) -> str | None:
    pub = key_path(tree).with_suffix(".pub")
    if not pub.exists():
        return None
    done = subprocess.run(["ssh-keygen", "-l", "-f", str(pub)], capture_output=True,
                          text=True, check=False)
    return done.stdout.split()[1] if done.returncode == 0 and done.stdout.split() else None


def git_env(tree: str) -> dict:
    """git's environment for talking to the tree's remote: our key when we
    made one (an adopted remote reached through an SSH alias of the owner's
    own brings its own), the hosts we have met, and never a prompt."""
    known = private() / "known_hosts"
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
           "-o", "StrictHostKeyChecking=accept-new",
           "-o", f"UserKnownHostsFile={known} {Path.home() / '.ssh' / 'known_hosts'}"]
    if key_path(tree).exists():
        ssh += ["-i", str(key_path(tree)), "-o", "IdentitiesOnly=yes"]
    return {**os.environ, "GIT_SSH_COMMAND": " ".join(shlex.quote(x) for x in ssh),
            "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/false", "LC_ALL": "C"}


def git(tree: str, *args: str, timeout: float = PROBE_TIMEOUT_S, where: Path | None = None):
    return subprocess.run(["git", "-C", str(where or tree_path(tree)), *args],
                          capture_output=True, text=True, env=git_env(tree),
                          timeout=timeout, check=False)


# -- is it public? ------------------------------------------------------------

def anonymous_address(url: str) -> str | None:
    """The address anyone at all could read the repository from, if it were
    public: its HTTPS twin. `LOAM_GIT_BACKUP_ANON` (JSON, address -> address)
    stands in for it in a test."""
    table = os.environ.get("LOAM_GIT_BACKUP_ANON")
    if table:
        try:
            mapped = json.loads(table).get(url)
        except ValueError:
            mapped = None
        if mapped is not None:
            return mapped or None
    p = parts(url)
    if not p:
        return None
    path = p["path"].strip("/")
    if not path.endswith(".git"):
        path += ".git"
    return f"https://{real_host(p['host'])}/{path}"


AUTH_WORDS = ("authentication", "could not read username", "terminal prompts disabled",
              "401", "403", "404", "not found", "repository not found", "access denied",
              "permission", "unauthorized", "forbidden")


def visibility(url: str) -> str:
    """`public` when anyone can read the repository without signing in,
    `private` when the host asks who is reading, `unknown` when it could not
    be asked (no HTTPS there, no network). Asked with git itself, with no
    credentials and no config of the owner's, so a saved login cannot make a
    private-looking answer."""
    anon = anonymous_address(url)
    if not anon:
        return "unknown"
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/nonexistent",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/false", "SSH_ASKPASS": "/bin/false",
           "LC_ALL": "C"}
    try:
        done = subprocess.run(["git", "-c", "credential.helper=", "ls-remote", "--heads", anon],
                              capture_output=True, text=True, env=env,
                              timeout=PROBE_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        return "unknown"
    if done.returncode == 0:
        return "public"
    err = done.stderr.lower()
    return "private" if any(w in err for w in AUTH_WORDS) else "unknown"


PUBLIC_WORDS = ("This repository is public: anyone on the internet can read it. "
                "Loam will not back your {name} up there. Make the repository private "
                "on {host} (Settings, then Change visibility), or use another one, and "
                "try again.")


# -- what a failure means -----------------------------------------------------

LARGE_FILE = re.compile(r"File (?P<path>.+?) is (?P<size>[\d.]+ ?[MG]B); this exceeds")


FOLDER_WORDS = ("Loam cannot reach a repository in that folder. Check that it is "
                "still there, that its drive is attached, and that Loam can write to it.")


def classify(stderr: str, url: str = "") -> dict:
    """A push's or a probe's failure, in plain words: {kind, words, files?}.
    A folder remote has no key and no network, so what would be either is
    said as the folder's."""
    why = _classify(stderr)
    if is_folder(url) and why["kind"] in ("key", "network"):
        return {"kind": "folder", "words": FOLDER_WORDS}
    return why


def _classify(stderr: str) -> dict:
    err = stderr or ""
    low = err.lower()
    large = [m.groupdict() for m in LARGE_FILE.finditer(err)]
    if large or "gh001" in low or "large files detected" in low or "exceeds" in low and "file size" in low:
        return {"kind": "too-large",
                "words": "The host refused a file that is too large. Its history holds "
                         "it, so nothing newer can be sent until it is taken out.",
                "files": [f["path"] for f in large]}
    if "read only" in low or "read-only" in low or "write access" in low \
            or "denied to deploy key" in low or "not allowed to push" in low:
        return {"kind": "read-only",
                "words": "The key can read the repository but not write to it. Add it "
                         "again with write access allowed."}
    if "non-fast-forward" in low or "fetch first" in low or "[rejected]" in low \
            or "updates were rejected" in low:
        return {"kind": "diverged",
                "words": "The repository has history that is not this machine's, so "
                         "Loam will not overwrite it. Use an empty repository, or one "
                         "this machine backed up to before."}
    if "quota" in low or "no space" in low or "disk full" in low or "storage" in low \
            and "limit" in low:
        return {"kind": "quota",
                "words": "The host says the repository or the account is out of space."}
    if "permission denied (publickey" in low or "repository not found" in low \
            or "could not read from remote repository" in low \
            or "does not appear to be a git repository" in low \
            or "key is not authorized" in low or "access denied" in low:
        return {"kind": "key",
                "words": "The host did not accept the key for this repository. Check "
                         "that it is added to this repository as a deploy key, and has "
                         "not been removed."}
    if "host key verification failed" in low or "remote host identification has changed" in low:
        return {"kind": "host-key",
                "words": "The host's own key is not the one it had before, so Loam "
                         "stopped. If the host changed its key, forget it here and connect again."}
    if "could not resolve" in low or "timed out" in low or "network is unreachable" in low \
            or "connection refused" in low or "connection reset" in low \
            or "temporary failure" in low or "no route to host" in low:
        return {"kind": "network",
                "words": "Could not reach the host. Loam will try again by itself."}
    tail = err.strip().splitlines()[-1][:300] if err.strip() else "no reason given"
    return {"kind": "other", "words": f"The push failed: {tail}"}


# -- state --------------------------------------------------------------------

def _state_path() -> Path:
    return private() / "state.json"


def load_state() -> dict:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("trees", {})
    for t in TREES:
        data["trees"].setdefault(t, {})
    return data


def save_state(state: dict) -> None:
    path = _state_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# -- connecting ---------------------------------------------------------------

def probe(tree: str, url: str) -> dict:
    """Can this machine write to the repository with the tree's key? Asked
    with a dry-run push, which the host answers with its write permission
    and which changes nothing. {ok, kind, words, empty}."""
    where = tree_path(tree)
    if not (where / ".git").exists():
        raise BackupError(f"The {TREES[tree]} folder is not a repository yet; Loam makes "
                          f"it one within fifteen minutes of starting.")
    try:
        ls = git(tree, "ls-remote", "--heads", url)
    except subprocess.TimeoutExpired:
        return {"ok": False, "kind": "network", "words": "The host did not answer in time."}
    if ls.returncode != 0:
        return {"ok": False, **classify(ls.stderr, url)}
    remote_main = next((line.split()[0] for line in ls.stdout.splitlines()
                        if line.endswith(f"refs/heads/{BRANCH}")), None)
    head = git(tree, "rev-parse", "--verify", "-q", "HEAD").stdout.strip()
    if head:
        try:
            dry = git(tree, "push", "--dry-run", "--porcelain", url,
                      f"HEAD:refs/heads/{BRANCH}", timeout=PROBE_TIMEOUT_S * 2)
        except subprocess.TimeoutExpired:
            return {"ok": False, "kind": "network", "words": "The host did not answer in time."}
        if dry.returncode != 0:
            # A dry run against history it lacks is "rejected" before any
            # write check; say the true thing.
            return {"ok": False, **classify(dry.stderr + dry.stdout, url)}
    return {"ok": True, "empty": not ls.stdout.strip(), "remote_main": remote_main,
            "words": "Connected. Loam can write to this repository."}


def connect(tree: str, remote: str) -> dict:
    """Step one of connecting a tree: the address checked, the key made.
    The remote is saved only once it is not public; the first push waits
    until `test` (or the timer) finds the key works."""
    if tree not in TREES:
        raise BackupError(f"There is no {tree!r}: the trees are vault and spaces.")
    url = normalise(remote)
    if not url:
        raise BackupError("Paste the repository's address first.")
    for other in TREES:
        if other != tree and values()[SETTING[other]] and \
                values()[SETTING[other]].rstrip("/").removesuffix(".git") == url.rstrip("/").removesuffix(".git"):
            raise BackupError(f"That is the repository your {TREES[other]} is backed up to. "
                              f"Each needs its own.")
    seen = visibility(url)
    if seen == "public":
        host = real_host(parts(url).get("host", "")) or "the host"
        raise BackupError(PUBLIC_WORDS.format(name=NOUN[tree], host=host))
    if is_folder(url):
        prepare_folder(url)
        pub = None
    else:
        pub = make_key(tree)
    set_value(SETTING[tree], url)
    state = load_state()
    t = state["trees"][tree]
    if t.get("remote") != url:
        t.clear()
        t["remote"] = url
    t["connected_at"] = iso()
    t["visibility"] = seen
    save_state(state)
    return {"ok": True, "tree": tree, "remote": url, "public_key": pub,
            "visibility": seen, "host": host_kind(url),
            "add_key_at": deploy_key_page(url), "web": web_address(url),
            "steps": steps(url)}


def steps(url: str) -> list[str]:
    kind = host_kind(url)
    if kind == "github":
        return ["Open the repository's Settings, then Deploy keys, then Add deploy key.",
                "Paste the key, give it a name like Loam, and tick Allow write access.",
                "Choose Add key, then come back and test the connection."]
    if kind == "gitlab":
        return ["Open the project's Settings, then Repository, then Deploy keys.",
                "Paste the key, give it a title like Loam, and tick Grant write permissions.",
                "Choose Add key, then come back and test the connection."]
    if kind == "gitea":
        return ["Open the repository's Settings, then Deploy Keys, then Add Deploy Key.",
                "Paste the key, give it a title like Loam, and tick Enable Write Access.",
                "Choose Add Deploy Key, then come back and test the connection."]
    if kind == "folder":
        return ["Nothing to add: this is a folder on this machine, so no key is needed.",
                "Test the connection, and the first backup starts."]
    return ["If the server runs a git host (Gitea, Forgejo, GitLab or another), add this "
            "key to the repository as a deploy key with write access.",
            "If it is a plain SSH server, add the key as one line to "
            "~/.ssh/authorized_keys of the account that owns the repository.",
            "Then come back and test the connection."]


def test(tree: str, push_after: bool = True) -> dict:
    """Step two: does the key work? On yes, the first backup starts now
    (the page's server starts it beside the answer instead: `push_after`)."""
    url = values()[SETTING[tree]]
    if not url:
        raise BackupError(f"Your {TREES[tree]} is not connected to a repository yet.")
    seen = visibility(url)
    if seen == "public":
        host = real_host(parts(url).get("host", "")) or "the host"
        _fail(tree, url, {"kind": "public", "words": PUBLIC_WORDS.format(
            name=NOUN[tree], host=host)})
        return {"ok": False, "kind": "public", "words": PUBLIC_WORDS.format(
            name=NOUN[tree], host=host)}
    out = probe(tree, url)
    state = load_state()
    state["trees"][tree]["visibility"] = seen
    state["trees"][tree]["tested_at"] = iso()
    state["trees"][tree]["test"] = {k: out.get(k) for k in ("ok", "kind", "words")}
    save_state(state)
    if out["ok"] and push_after:
        pushed = push(tree, force=True)
        out["first_push"] = {k: pushed.get(k) for k in ("ok", "words", "kind", "sha")}
    return out


def disconnect(tree: str, forget: bool = False) -> dict:
    """Stop backing a tree up. The repository is left as it is; the key is
    kept unless `forget`, so connecting again to the same repository needs no
    new deploy key."""
    set_value(SETTING[tree], "")
    state = load_state()
    state["trees"][tree] = {}
    save_state(state)
    if forget:
        forget_key(tree)
    return {"ok": True, "tree": tree,
            "words": f"Your {TREES[tree]} is no longer backed up. The repository and "
                     f"everything already in it are untouched."}


# -- pushing ------------------------------------------------------------------

def _notify(text: str, section: str = "Urgent") -> bool:
    """One line on the owner's General board, as Loam's other warnings are
    (lib/loam_inbox.py); written once per (who, words)."""
    try:
        import loam_inbox  # noqa: PLC0415
        vault = Path(os.environ.get("LOAM_GIT_BACKUP_INBOX_VAULT") or lc.VAULT)
        return loam_inbox.append_comms(vault, "loam", text, LINK, section=section)
    except Exception:  # noqa: BLE001 -- a warning never stops a backup
        return False


def _fail(tree: str, url: str, why: dict, state: dict | None = None) -> dict:
    own = state is None
    state = state or load_state()
    t = state["trees"][tree]
    failing = t.get("failing") or {}
    if failing.get("kind") != why["kind"]:
        failing = {"since": iso(), "attempts": 0, "told": None}
    failing.update(kind=why["kind"], words=why["words"], files=why.get("files") or [],
                   detail=why.get("detail"), last=iso())
    failing["attempts"] = int(failing.get("attempts") or 0) + 1
    t["failing"] = failing
    t["last_attempt_at"] = iso()
    t["remote"] = url
    since = parse_iso(failing["since"]) or now()
    due = (failing["attempts"] >= 2 if why["kind"] != "network"
           else now() - since >= timedelta(seconds=NETWORK_GRACE_S)) \
        or why["kind"] == "public"
    if due and not failing.get("told"):
        name = NOUN[tree]
        text = (f"backup of your {name} has been failing since {local_time(since)}: {why['words']}"
                + (f" ({', '.join(why['files'][:3])})" if why.get("files") else ""))
        _notify(text)
        failing["told"] = iso()
        failing["told_text"] = text
    if own:
        save_state(state)
    return failing


def due(tree: str, state: dict, every: str) -> bool:
    t = state["trees"][tree]
    last = parse_iso(t.get("last_attempt_at"))
    return last is None or (now() - last).total_seconds() >= EVERY[every] - 60


def same_repository(a: str, b: str) -> bool:
    """Whether two addresses name one repository: the same text, the same
    web page (an SSH alias and the host it stands for), or one folder."""
    if a == b:
        return True
    wa, wb = web_address(a), web_address(b)
    if wa and wb:
        return wa.lower().removesuffix(".git") == wb.lower().removesuffix(".git")
    if not parts(a) and not parts(b):
        try:
            return Path(a).resolve() == Path(b).resolve()
        except OSError:
            return False
    return False


def track(tree: str, url: str, head: str) -> list[str]:
    """After a push to ``url``, move each remote-tracking `main` of a remote
    with that same repository to ``head``, as `git push <remote>` would
    have. The push goes to the address, not to a remote by name, so until
    2026-11-19 a tree whose `origin` is that repository read "ahead N" in
    `git status` for ever. Returns the remotes moved."""
    moved = []
    names = git(tree, "remote").stdout.split()
    for name in names:
        have = git(tree, "remote", "get-url", name).stdout.strip()
        if have and same_repository(have, url):
            if git(tree, "update-ref", f"refs/remotes/{name}/{BRANCH}", head).returncode == 0:
                moved.append(name)
    return moved


def push(tree: str, force: bool = False) -> dict:
    """One backup of one tree: commit what is new (Loam's own routine), then
    send `main` to the repository. Never forced, never to a public one."""
    vals = values()
    url = vals[SETTING[tree]]
    if not url:
        return {"ok": True, "tree": tree, "skipped": "not connected"}
    state = load_state()
    if not force and not due(tree, state, vals["every"]):
        return {"ok": True, "tree": tree, "skipped": "not due"}
    where = tree_path(tree)
    if not ac.hand_routine(tree):
        ac.commit(tree, where)
    t = state["trees"][tree]
    if t.get("remote") and t["remote"] != url:
        state["trees"][tree] = t = {}
    t["remote"] = url
    seen = visibility(url)
    t["visibility"] = seen
    if seen == "public":
        host = real_host(parts(url).get("host", "")) or "the host"
        failing = _fail(tree, url, {"kind": "public", "words": PUBLIC_WORDS.format(
            name=NOUN[tree], host=host)}, state)
        save_state(state)
        return {"ok": False, "tree": tree, "kind": "public", "words": failing["words"]}
    try:
        with ac.lock(where, timeout=120):
            head = git(tree, "rev-parse", "--verify", "-q", "HEAD").stdout.strip()
            if not head:
                return {"ok": True, "tree": tree, "skipped": "nothing committed yet"}
            last_contact = parse_iso(t.get("last_ok_at"))
            if (not force and head == t.get("last_pushed_sha") and not t.get("failing")
                    and last_contact and (now() - last_contact).total_seconds() < VERIFY_S):
                t["last_attempt_at"] = iso()
                save_state(state)
                return {"ok": True, "tree": tree, "skipped": "up to date", "sha": head[:7]}
            started = time.monotonic()
            try:
                done = git(tree, "push", "--porcelain", url, f"HEAD:refs/heads/{BRANCH}",
                           timeout=PUSH_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                done = subprocess.CompletedProcess([], 1, "", "push timed out")
            if done.returncode == 0:
                track(tree, url, head)
    except TimeoutError:
        return {"ok": True, "tree": tree, "skipped": "busy: Loam's commit is running"}
    if done.returncode != 0:
        why = classify(done.stderr + "\n" + done.stdout, url)
        why["detail"] = done.stderr.strip()[-600:]
        failing = _fail(tree, url, why, state)
        save_state(state)
        return {"ok": False, "tree": tree, "kind": why["kind"], "words": why["words"],
                "files": why.get("files") or [], "attempts": failing["attempts"]}
    was = t.get("failing")
    t.pop("failing", None)
    t.update(last_attempt_at=iso(), last_ok_at=iso(), last_pushed_at=iso(),
             last_pushed_sha=head, seconds=round(time.monotonic() - started, 1))
    save_state(state)
    if was and was.get("told"):
        name = NOUN[tree]
        _notify(f"backup of your {name} works again: everything up to "
                f"{local_time(now())} is in its repository", section="Important")
    return {"ok": True, "tree": tree, "sha": head[:7],
            "words": f"Backed up to {web_address(url) or url}."}


def run(force: bool = False, trees: list[str] | None = None) -> list[dict]:
    out = []
    for tree in trees or list(TREES):
        try:
            out.append(push(tree, force=force))
        except BackupError as exc:
            out.append({"ok": False, "tree": tree, "words": str(exc)})
    return out


# -- what the owner sees ------------------------------------------------------

def local_time(when: datetime) -> str:
    """`Nov 18, 20:47`, in the install's own time zone (lib/loam_config.py)."""
    try:
        from zoneinfo import ZoneInfo  # noqa: PLC0415
        when = when.astimezone(ZoneInfo(lc.TIMEZONE))
    except Exception:  # noqa: BLE001 -- a zone we cannot read: UTC, said so
        return when.strftime("%b %-d, %H:%M UTC")
    return when.strftime("%b %-d, %H:%M")


def ago(when: datetime | None) -> str | None:
    if not when:
        return None
    s = int((now() - when).total_seconds())
    if s < 90:
        return "just now"
    if s < 3600:
        return f"{s // 60} minutes ago"
    if s < 2 * 3600:
        return "an hour ago"
    if s < 86400:
        return f"{s // 3600} hours ago"
    d = s // 86400
    return "yesterday" if d == 1 else f"{d} days ago"


def tree_status(tree: str, state: dict, vals: dict) -> dict:
    t = state["trees"][tree]
    url = vals[SETTING[tree]]
    where = tree_path(tree)
    local = ac.read_record(tree)
    head = ""
    if (where / ".git").exists():
        head = subprocess.run(["git", "-C", str(where), "rev-parse", "--verify", "-q", "HEAD"],
                              capture_output=True, text=True, check=False).stdout.strip()
    waiting = None
    pushed = t.get("last_pushed_sha")
    if head and pushed and head != pushed:
        cnt = subprocess.run(["git", "-C", str(where), "rev-list", "--count", f"{pushed}..{head}"],
                             capture_output=True, text=True, check=False).stdout.strip()
        waiting = int(cnt) if cnt.isdigit() else None
    failing = t.get("failing")
    pushed_at = parse_iso(t.get("last_pushed_at"))
    if not url:
        level, line = "off", "Not backed up off this machine yet."
    elif failing:
        level, line = "fail", failing["words"]
    elif not pushed_at:
        level = "wait"
        line = ("Waiting for the key: add it to the repository, then test the connection."
                if not (t.get("test") or {}).get("ok") else "The first backup is on its way.")
    else:
        level, line = "ok", f"Backed up {ago(pushed_at)}."
    held = local.get("held") or []
    return {"id": tree, "name": TREES[tree], "path": str(where), "remote": url or None,
            "web": web_address(url) if url else None, "host": host_kind(url) if url else None,
            "add_key_at": deploy_key_page(url) if url else None,
            "steps": steps(url) if url else None,
            "public_key": None if is_folder(url) else public_key(tree),
            "fingerprint": None if is_folder(url) else fingerprint(tree),
            "visibility": t.get("visibility"), "level": level, "line": line,
            "last_pushed_at": t.get("last_pushed_at"), "last_pushed_ago": ago(pushed_at),
            "last_pushed_sha": (pushed or "")[:7] or None,
            "last_attempt_at": t.get("last_attempt_at"), "waiting_commits": waiting,
            "failing": failing, "test": t.get("test"),
            "held": held, "limit_bytes": local.get("limit_bytes") or ac.max_bytes(),
            "local": {"at": local.get("at"), "committed": local.get("committed"),
                      "skipped": local.get("skipped"), "error": local.get("error"),
                      "head": head[:7] or None}}


def status() -> dict:
    vals = values()
    state = load_state()
    trees = [tree_status(t, state, vals) for t in TREES]
    levels = [t["level"] for t in trees]
    if "fail" in levels:
        bad = [t["name"] for t in trees if t["level"] == "fail"]
        summary = {"level": "fail", "words": f"{' and '.join(bad)} {'is' if len(bad) == 1 else 'are'} not being backed up."}
    elif all(x == "off" for x in levels):
        summary = {"level": "off", "words": "Nothing is backed up off this machine yet."}
    elif "wait" in levels:
        summary = {"level": "wait", "words": "Almost there: one step left."}
    elif "off" in levels:
        on = [t["name"] for t in trees if t["level"] == "ok"]
        summary = {"level": "part", "words": f"{' and '.join(on)} is backed up; the other is not yet."}
    else:
        summary = {"level": "ok", "words": "Everything is backed up."}
    return {"ok": True, "every": vals["every"], "every_words": EVERY_WORDS[vals["every"]],
            "choices": EVERY_WORDS, "summary": summary, "trees": trees}


# -- restoring ----------------------------------------------------------------

STARTER_SUBJECT = "Loam: the starting point"


def is_fresh(tree: str) -> bool:
    """The tree is still what the installer left: its history is the
    starting point alone, or what is committed now is that same content.
    Anything else is the owner's, and a restore sets it aside only when
    told to (`replace`)."""
    where = tree_path(tree)
    if not (where / ".git" / "HEAD").exists():
        return True
    def out(*args):
        return subprocess.run(["git", "-C", str(where), *args], capture_output=True,
                              text=True, check=False).stdout.strip()
    roots = out("rev-list", "--max-parents=0", "HEAD").splitlines()
    if not roots:
        return True
    if len(roots) != 1 or out("log", "-1", "--format=%s", roots[0]) != STARTER_SUBJECT:
        return False
    dirty = out("status", "--porcelain", "--untracked-files=normal")
    return not dirty and out("rev-parse", "HEAD^{tree}") == out("rev-parse", f"{roots[0]}^{{tree}}")


def restore(tree: str, remote: str | None = None, replace: bool = False) -> dict:
    """Bring a tree back from its repository, in place: what is here now is
    kept aside first (`refs/loam/before-restore` in the tree's own history,
    and the files as they were in `<tree>.before-restore-<time>/` beside it).
    Refused on a tree with history of its own unless `replace`."""
    url = normalise(remote) if remote else values()[SETTING[tree]]
    if not url:
        raise BackupError(f"Which repository? Connect your {TREES[tree]} to it first, or name it.")
    if not replace and not is_fresh(tree):
        raise BackupError(f"Your {NOUN[tree]} here already has its own history. Restoring "
                          f"sets it aside (nothing is deleted) and puts the backup in its "
                          f"place; ask again with replace once the owner has said yes to that.")
    where = tree_path(tree)
    where.mkdir(parents=True, exist_ok=True)
    if not (where / ".git").exists():
        subprocess.run(["git", "-C", str(where), "init", "-q", "-b", BRANCH], check=True)
    stamp = now().strftime("%Y%m%dT%H%M%SZ")
    with ac.lock(where, timeout=300):
        try:
            got = git(tree, "fetch", "--quiet", url,
                      f"+refs/heads/{BRANCH}:refs/loam/restore", timeout=PUSH_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise BackupError("The host did not finish sending the backup in time.")
        if got.returncode != 0:
            raise BackupError(classify(got.stderr, url)["words"])
        aside = where.parent / f"{where.name}.before-restore-{stamp}"
        n = 1
        while aside.exists():
            n += 1
            aside = where.parent / f"{where.name}.before-restore-{stamp}-{n}"
        shutil.copytree(where, aside, symlinks=True, ignore=shutil.ignore_patterns(".git"))
        old = git(tree, "rev-parse", "--verify", "-q", "HEAD").stdout.strip()
        if old:
            git(tree, "update-ref", "refs/loam/before-restore", old)
        co = git(tree, "checkout", "--quiet", "--force", "-B", BRANCH, "refs/loam/restore",
                 timeout=PUSH_TIMEOUT_S)
        if co.returncode != 0:
            raise BackupError(f"Could not put the backup in place: {co.stderr.strip()[-300:]}")
        git(tree, "clean", "-fdq", timeout=PUSH_TIMEOUT_S)
        head = git(tree, "rev-parse", "HEAD").stdout.strip()
    state = load_state()
    state["trees"][tree].update(remote=url, last_pushed_sha=head, restored_at=iso(),
                                restored_from=url)
    save_state(state)
    try:
        if values()[SETTING[tree]] != url:
            set_value(SETTING[tree], url)
    except BackupError:
        pass
    out = {"ok": True, "tree": tree, "sha": head[:7], "set_aside": str(aside),
           "files": int(subprocess.run(["git", "-C", str(where), "ls-files"], capture_output=True,
                                       text=True, check=False).stdout.count("\n"))}
    if tree == "spaces":
        out["agents"] = reprovision()
    out["words"] = (f"Your {TREES[tree]} is back as it was at its last backup "
                    f"({head[:7]}). What was here before is in {aside.name}.")
    return out


def reprovision() -> list[str]:
    """Each restored agent is made runnable on this machine again, as an
    update does it (bin/provision by its name; its wake time is its record's)."""
    import loam_agents  # noqa: PLC0415
    root = Path(lc.ROOT)
    done = []
    for name in loam_agents.live(loam_agents.every()):
        r = subprocess.run([sys.executable, str(root / "bin" / "provision"), name],
                           capture_output=True, text=True, check=False, timeout=600)
        done.append(name if r.returncode == 0 else f"{name} (provision failed: "
                    f"{(r.stderr or r.stdout).strip()[-200:]})")
    return done
