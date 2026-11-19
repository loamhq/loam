"""The host's own setup, kept as a history on this machine and put back on
request: ``loam host`` (``bin/loam-host``).

The vault, the agents' workspaces and the plugins' Yours layer each have a
history of their own (lib/loam_autocommit.py). The host's setup around them
had none: an agent with sudo could break the web server's config, a unit or
a setting and there was nothing to restore from. Agents have full control
of the box, so the answer is undo rather than more guards: what Loam
installs and depends on is committed, on this machine, whenever it changes,
and one command puts it back. docs/host-history.md is the page for people.

**What is covered** is ``rules()``: Loam's own files under ``/etc`` (its
config, the web server's config, its system units and their enablement
links, its sudoers, sysctl and Docker daemon files), the install user's
Loam units and their enablement links, its crontab and linger flag, its key
files and the small state files that are settings rather than data. What is
not, and why, is the page's "Not covered".

**Where it is kept**: one git repository, ``paths.host_history``
(``/var/lib/loam/host-history`` by default), owned by root and readable by
root only, so a key file copied into it is no more readable than it was.
Each covered file is mirrored under ``files/<its absolute path>``; git keeps
contents and symlinks, and ``meta.tsv`` keeps each file's mode, owner and
group by name, and whether it is treated as a secret. ``scope.json`` is the
covered rules as they were resolved for that state, so a restore needs no
config: it reads what to put back, and where, from the state itself.

**What it costs**: the timer (``loam-host-history.timer``, root's) compares
the covered files with the mirror every fifteen minutes. When nothing
differs nothing is written and no commit is made (about 0.2 s of one CPU);
a state is a commit only when something changed. No agent is woken and nothing is posted.

**Restore** (``restore``) reads the named state, says every file it will
create, change or remove and what it will reload or restart, refuses a
web-server config or sudoers file that does not validate, saves the present
state first (so the restore can itself be undone), writes, and then reloads
systemd, the web server and anything whose unit or link changed. It uses
git, systemd and this file only: no broker, no app, no network.

Nothing here imports ``loam_config`` at module level: a broken
``/etc/loam/loam.toml`` is one of the things a restore is for. It is asked
for the covered paths when a snapshot is taken, and when it cannot be read
the last state's own scope is used.

Assumed of the host: Linux with systemd (the system manager, and a user
manager for Loam's user), git and Python 3.11. Caddy is checked and
reloaded when it is there; Docker Compose files are re-applied when Docker
is there. Nothing assumes a provider, a cloud API or this box's paths.

Stdlib only. Tests: tests/test_host_history.py.
"""

from __future__ import annotations

import contextlib
import fcntl
import fnmatch
import grp
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_HISTORY = "/var/lib/loam/host-history"
FILES = "files"
META = "meta.tsv"
SCOPE = "scope.json"
# A covered file larger than this is not kept (and named in the commit):
# setup files are small, and a big one is data that wandered in.
MAX_BYTES = 2 * 1024 * 1024
# A name that says "secret" is one, whatever its mode. A file the box keeps
# from other users (no read bit for "other") is one too.
SECRET_NAME = re.compile(r"(secret|token|password|credential|\.env$|\.key$|\.pem$)", re.I)
ENV = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
       "LC_ALL": "C.UTF-8", "HOME": "/root", "GIT_TERMINAL_PROMPT": "0"}
GIT_ID = ("Loam host history", "loam-host@localhost")


class HostError(Exception):
    """A refusal or failure, said in a sentence for the person at the terminal."""


# -- the covered rules ------------------------------------------------------------

@dataclass(frozen=True)
class Rule:
    """Files under ``root`` whose path relative to it matches ``pattern``
    (``*`` stays inside one folder, ``**`` matches anything below). With
    ``untracked`` set, only files that git repository does not track: the
    checkout's own files have its history, and a restore must never put an
    old release's file over a newer one."""
    root: str
    pattern: str
    why: str
    untracked: str = ""

    def as_dict(self) -> dict:
        d = {"root": self.root, "pattern": self.pattern, "why": self.why}
        if self.untracked:
            d["untracked"] = self.untracked
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Rule":
        return cls(d["root"], d["pattern"], d.get("why", ""), d.get("untracked", ""))


def rules(cfg=None) -> tuple[list[Rule], dict]:
    """The covered rules for this install, and what else a restore needs to
    know about it (the install's user and its user unit folder). ``cfg`` is
    ``loam_config`` (imported here when not given)."""
    if cfg is None:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import loam_config as cfg  # noqa: PLC0415
    user, home = cfg.USER, str(cfg.HOME)
    units = str(cfg.USER_UNIT_DIR)
    state = str(cfg.STATE)
    root = str(cfg.ROOT)
    config_file = Path(str(cfg.CONFIG_FILE))
    out = [
        # Loam's own configuration, and everything it renders under /etc/loam:
        # the deploy config, the login's and the browser's compose files and
        # walls, Authelia's config and its secrets file, the share links.
        Rule(str(config_file.parent), "**", "Loam's config and what it renders there"),
        Rule("/etc/caddy", "**", "the web server's config (rendered by gen-deploy)"),
        Rule("/etc/systemd/system", "loam*", "Loam's system units"),
        Rule("/etc/systemd/system", "loam*.d/*", "drop-ins on Loam's system units"),
        Rule("/etc/systemd/system", "*.wants/loam*", "which of Loam's system units are enabled"),
        Rule("/etc/systemd/system", "caddy.service.d/*", "drop-ins on the web server's unit"),
        Rule("/etc/systemd/system", "docker.service.d/*", "drop-ins on Docker's unit"),
        Rule("/etc/sudoers.d", "loam*", "the install user's sudo rules"),
        Rule("/etc/sysctl.d", "*loam*", "kernel settings Loam sets"),
        Rule("/etc/docker", "daemon.json", "Docker's daemon settings (gen-deploy's)"),
        Rule("/etc/claude-code", "managed-settings.json", "Claude Code's machine-wide settings"),
        Rule("/etc/profile.d", "loam.sh", "the login shell's Loam environment"),
        Rule("/usr/local/bin", "loam", "the `loam` command"),
        Rule("/var/lib/systemd/linger", user, "the user manager runs without a login"),
        Rule("/var/spool/cron/crontabs", user, "the install user's crontab (Debian layout)"),
        Rule("/var/spool/cron", user, "the install user's crontab (Red Hat layout)"),
        # The user manager's units: Loam's only, so a person's own units on a
        # machine they also use are not Loam's to put back.
        Rule(units, "loam*", "Loam's user units"),
        Rule(units, "loam*.d/*", "drop-ins on Loam's user units"),
        Rule(units, "*.wants/loam*", "which of Loam's user units are enabled"),
        Rule(str(Path(str(cfg.SECRETS_ENV)).parent), Path(str(cfg.SECRETS_ENV)).name,
             "the runtime's key file"),
        Rule(str(Path(str(cfg.WAKE_ENV)).parent), Path(str(cfg.WAKE_ENV)).name,
             "the agents' sign-in token"),
        Rule(f"{state}/broker", "settings.json", "the owner's settings"),
        Rule(f"{state}/plugins", "*.json", "which built-in plugins are on, ports, kept themes"),
        Rule(f"{state}/plugins", "keys/*", "the plugins' keys"),
        Rule(f"{state}/plugins", "lanes/*", "the plugins' tools for each lane"),
        Rule(f"{state}/sites", "*.toml", "the agents' sites"),
        Rule(f"{state}/github", "*.json", "the GitHub connection"),
        Rule(f"{root}/projects", "*.json", "project records", untracked=root),
        Rule(f"{root}/deploy", "*/*", "this install's own app records", untracked=root),
        Rule(f"{home}/.ssh", "config", "which key reaches which git host"),
    ]
    about = {"user": user, "user_units": units, "config_dir": str(config_file.parent)}
    return out, about


def _match(rel: str, pattern: str) -> bool:
    """``rel`` (a/b/c) against ``pattern``, folder by folder; ``**`` takes the rest."""
    parts, pats = rel.split("/"), pattern.split("/")
    for i, pat in enumerate(pats):
        if pat == "**":
            return True
        if i >= len(parts) or not fnmatch.fnmatchcase(parts[i], pat):
            return False
    return len(parts) == len(pats)


def _tracked(repo: str, cache: dict) -> set[str] | None:
    if repo not in cache:
        done = subprocess.run(["git", "-c", "safe.directory=*", "-C", repo, "ls-files", "-z"],
                              capture_output=True, check=False, env=ENV)
        cache[repo] = ({str(Path(repo) / p) for p in done.stdout.decode().split("\0") if p}
                       if done.returncode == 0 else None)
    return cache[repo]


def matched(rule_list: list[Rule]) -> list[str]:
    """Every live path the rules cover: regular files and symlinks, never
    followed into a linked folder."""
    found: set[str] = set()
    tracked_cache: dict = {}
    for rule in rule_list:
        base = Path(rule.root)
        if not base.is_dir() or base.is_symlink():
            continue
        skip = None
        if rule.untracked:
            skip = _tracked(rule.untracked, tracked_cache)
            if skip is None:
                continue        # can't tell tracked from not: cover none of it
        deep = "**" in rule.pattern or rule.pattern.count("/") > 0
        for top, dirs, names in os.walk(base, followlinks=False):
            rel_top = os.path.relpath(top, base)
            depth = 0 if rel_top == "." else rel_top.count("/") + 1
            if not deep:
                dirs[:] = []
            elif "**" not in rule.pattern and depth >= rule.pattern.count("/"):
                dirs[:] = []
            entries = list(names) + [d for d in os.listdir(top)
                                     if os.path.islink(os.path.join(top, d)) and d not in names]
            for name in entries:
                full = os.path.join(top, name)
                rel = name if rel_top == "." else f"{rel_top}/{name}"
                if not _match(rel, rule.pattern):
                    continue
                if not (os.path.islink(full) or os.path.isfile(full)):
                    continue
                if skip is not None and full in skip:
                    continue
                found.add(full)
    return sorted(found)


# -- one file's record ------------------------------------------------------------------

@dataclass
class Entry:
    path: str
    kind: str        # "file" | "link"
    mode: int
    owner: str
    group: str
    secret: bool
    data: bytes      # the contents, or the link's target

    def meta_line(self) -> str:
        return "\t".join([self.path, self.kind, f"{self.mode:04o}", self.owner, self.group,
                          "secret" if self.secret else "-"])

    def same(self, other: "Entry") -> bool:
        return (self.kind, self.data, self.mode, self.owner, self.group) == \
               (other.kind, other.data, other.mode, other.owner, other.group)

    def differences(self, other: "Entry") -> list[str]:
        out = []
        if self.kind != other.kind:
            out.append(f"{other.kind} -> {self.kind}")
        elif self.data != other.data:
            out.append("link target" if self.kind == "link" else "contents")
        if self.mode != other.mode and self.kind == "file":
            out.append(f"mode {other.mode:04o} -> {self.mode:04o}")
        if (self.owner, self.group) != (other.owner, other.group) and self.kind == "file":
            out.append(f"owner {other.owner}:{other.group} -> {self.owner}:{self.group}")
        return out


def _name(fn, ident: int) -> str:
    try:
        return fn(ident)[0]
    except KeyError:
        return str(ident)


def read_live(path: str) -> Entry | None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    owner, group = _name(pwd.getpwuid, st.st_uid), _name(grp.getgrgid, st.st_gid)
    if stat.S_ISLNK(st.st_mode):
        return Entry(path, "link", 0o777, owner, group, False, os.readlink(path).encode())
    if not stat.S_ISREG(st.st_mode):
        return None
    mode = stat.S_IMODE(st.st_mode)
    secret = not (mode & 0o004) or bool(SECRET_NAME.search(os.path.basename(path)))
    if st.st_size > MAX_BYTES:
        return Entry(path, "big", mode, owner, group, secret, b"")
    with open(path, "rb") as fh:
        return Entry(path, "file", mode, owner, group, secret, fh.read())


# -- the history repository ----------------------------------------------------------------

def history_dir() -> Path:
    env = os.environ.get("LOAM_HOST_HISTORY")
    if env:
        return Path(env)
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import loam_config as cfg  # noqa: PLC0415
        value = cfg.get("paths.host_history")
        if value:
            return Path(value)
    except Exception:  # noqa: BLE001 -- a broken config must not stop a restore
        pass
    return Path(DEFAULT_HISTORY)


def git(h: Path, *args: str, check: bool = True, input: bytes | None = None,
        text: bool = True) -> subprocess.CompletedProcess:
    done = subprocess.run(["git", "-c", f"safe.directory={h}", "-C", str(h), *args],
                          capture_output=True, input=input, check=False, env=ENV, text=False)
    if check and done.returncode != 0:
        raise HostError(f"git {args[0]} failed: {done.stderr.decode(errors='replace').strip()}")
    if text:
        done.stdout = done.stdout.decode(errors="replace")
        done.stderr = done.stderr.decode(errors="replace")
    return done


def ensure_repo(h: Path) -> None:
    old = os.umask(0o077)
    try:
        h.mkdir(parents=True, exist_ok=True)
        os.chmod(h, 0o700)
        if not (h / ".git").exists():
            git(h, "init", "-q", "-b", "main")
            git(h, "config", "core.autocrlf", "false")
            git(h, "config", "gc.auto", "256")
    finally:
        os.umask(old)


@contextlib.contextmanager
def locked(h: Path):
    h.parent.mkdir(parents=True, exist_ok=True)
    with open(f"{h}.lock", "a") as fh:
        os.chmod(fh.name, 0o600)
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield


def mirror_path(h: Path, path: str) -> Path:
    return h / FILES / path.lstrip("/")


def read_mirror(h: Path) -> dict[str, Entry]:
    """The last state, from the work tree (not git): what the next snapshot
    compares the live files with."""
    out: dict[str, Entry] = {}
    meta = h / META
    if not meta.exists():
        return out
    for line in meta.read_text().splitlines():
        path, kind, mode, owner, group, secret = line.split("\t")
        m = mirror_path(h, path)
        try:
            data = (os.readlink(m).encode() if kind == "link" else
                    b"" if kind == "big" else m.read_bytes())
        except OSError:
            continue
        out[path] = Entry(path, kind, int(mode, 8), owner, group, secret == "secret", data)
    return out


def write_mirror(h: Path, live: dict[str, Entry], last: dict[str, Entry]) -> list[str]:
    """Bring the work tree to ``live``; returns what changed, as status lines."""
    changed = []
    old = os.umask(0o077)
    try:
        for path in sorted(set(last) - set(live)):
            with contextlib.suppress(FileNotFoundError):
                mirror_path(h, path).unlink()
            changed.append(f"D {path}")
        for path, e in sorted(live.items()):
            prev = last.get(path)
            if prev is not None and prev.same(e) and prev.secret == e.secret:
                continue
            m = mirror_path(h, path)
            m.parent.mkdir(parents=True, exist_ok=True)
            if m.is_symlink() or m.exists():
                m.unlink()
            if e.kind == "link":
                os.symlink(e.data.decode(), m)
            elif e.kind == "big":
                m.write_bytes(b"")
            else:
                m.write_bytes(e.data)
            changed.append(f"{'A' if prev is None else 'M'} {path}")
        (h / META).write_text("".join(e.meta_line() + "\n" for _, e in sorted(live.items())))
    finally:
        os.umask(old)
    return changed


def checks(entries: dict[str, Entry], root: Path | None = None) -> list[str]:
    """What is wrong with these files as config, in a phrase each: the web
    server's config validates, each sudoers file parses, each TOML and JSON
    file reads. ``root`` is where the files are (a restore's unpacked state);
    by default, where they live."""
    bad = []
    for path, e in sorted(entries.items()):
        if e.kind != "file":
            continue
        where = str(root / path.lstrip("/")) if root else path
        name = os.path.basename(path)
        try:
            if name.endswith(".toml"):
                tomllib.loads(e.data.decode())
            elif name.endswith(".json"):
                json.loads(e.data.decode() or "null")
        except (ValueError, UnicodeDecodeError) as exc:
            bad.append(f"{path}: does not parse ({str(exc).splitlines()[0]})")
        if name == "Caddyfile" and shutil.which("caddy", path=ENV["PATH"]):
            done = subprocess.run(["caddy", "validate", "--config", where, "--adapter",
                                   "caddyfile"], capture_output=True, text=True, env=ENV,
                                  check=False, timeout=120, cwd=os.path.dirname(where))
            if done.returncode:
                msg = [ln for ln in done.stderr.splitlines() if "Error" in ln or "error" in ln]
                bad.append(f"{path}: caddy validate fails ({(msg or ['?'])[-1][-300:]})")
        if path.startswith("/etc/sudoers.d/") and shutil.which("visudo", path=ENV["PATH"]):
            done = subprocess.run(["visudo", "-cf", where], capture_output=True, text=True,
                                  env=ENV, check=False)
            if done.returncode:
                bad.append(f"{path}: visudo -c fails ({done.stdout.strip()[-200:]})")
    return bad


def _scope_for_snapshot(h: Path) -> tuple[list[Rule], dict, str]:
    try:
        rule_list, about = rules()
        return rule_list, about, ""
    except Exception as exc:  # noqa: BLE001
        scope = h / SCOPE
        if not scope.exists():
            raise HostError(f"Loam's config could not be read ({exc}) and there is no "
                            f"earlier state to take the covered paths from") from exc
        data = json.loads(scope.read_text())
        return ([Rule.from_dict(r) for r in data["rules"]], data["about"],
                f"Loam's config could not be read ({exc}); the last state's scope was used")


def snapshot(reason: str = "", h: Path | None = None, scope: tuple | None = None) -> dict:
    """Commit the covered files if anything differs from the last state.
    Returns {"changed": [...], "commit": sha|None, "checks": [...], "note": str}."""
    h = h or history_dir()
    with locked(h):
        ensure_repo(h)
        rule_list, about, note = (*scope, "") if scope else _scope_for_snapshot(h)
        live = {p: e for p in matched(rule_list) if (e := read_live(p)) is not None}
        last = read_mirror(h)
        scope_text = json.dumps({"rules": [r.as_dict() for r in rule_list], "about": about},
                                indent=1) + "\n"
        scope_file = h / SCOPE
        scope_same = scope_file.exists() and scope_file.read_text() == scope_text
        has_head = git(h, "rev-parse", "-q", "--verify", "HEAD", check=False).returncode == 0
        unchanged = (has_head and scope_same and set(live) == set(last)
                     and all(last[p].same(e) and last[p].secret == e.secret
                             for p, e in live.items()))
        if unchanged:
            return {"changed": [], "commit": None, "checks": [], "note": note}
        changed = write_mirror(h, live, last)
        if not scope_same:
            old = os.umask(0o077)
            scope_file.write_text(scope_text)
            os.umask(old)
        problems = checks(live)
        big = sorted(p for p, e in live.items() if e.kind == "big")
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        lines = [f"host: {stamp}" + (f" ({reason})" if reason else ""), ""]
        lines += changed
        if not changed:
            lines.append("(the covered paths changed)")
        if big:
            lines += ["", "Not kept, larger than 2 MB: " + ", ".join(big)]
        lines += ["", "Checks: " + ("ok" if not problems else "failed")]
        lines += [f"  {p}" for p in problems]
        if note:
            lines += ["", note]
        git(h, "add", "-A")
        name, email = GIT_ID
        git(h, "-c", f"user.name={name}", "-c", f"user.email={email}",
            "commit", "-q", "--allow-empty", "-F", "-", input="\n".join(lines).encode() + b"\n")
        sha = git(h, "rev-parse", "HEAD").stdout.strip()
        return {"changed": changed, "commit": sha, "checks": problems, "note": note}


# -- the states -------------------------------------------------------------------------------

@dataclass
class State:
    sha: str
    when: datetime
    subject: str
    ok: bool
    changed: list[str]


def states(h: Path, limit: int = 30) -> list[State]:
    if git(h, "rev-parse", "-q", "--verify", "HEAD", check=False).returncode:
        return []
    out = git(h, "log", f"-{limit}", "--format=%x1e%H%x1f%ct%x1f%B").stdout
    found = []
    for rec in out.split("\x1e")[1:]:
        sha, ct, body = rec.split("\x1f", 2)
        lines = body.strip().splitlines()
        changed = [ln for ln in lines if re.match(r"^[ADM] /", ln)]
        found.append(State(sha, datetime.fromtimestamp(int(ct), timezone.utc), lines[0],
                           "Checks: failed" not in body, changed))
    return found


AGO = re.compile(r"^(\d+)\s*([mhd])(?:in|ins|our|ours|ay|ays)?(?:\s*ago)?$", re.I)


def resolve(h: Path, name: str | None) -> tuple[str, str]:
    """A state's commit and how it was chosen. None: the newest state whose
    checks passed. A duration (``30m``, ``2h``, ``1d``) or a time
    (``2026-11-19 09:00``, local): the newest state at or before it. Anything
    else: a commit, as git names one (``a1b2c3d``, ``HEAD~2``)."""
    if git(h, "rev-parse", "-q", "--verify", "HEAD", check=False).returncode:
        raise HostError(f"there is no host history yet at {h}: `sudo loam host snapshot` "
                        f"takes the first state")
    if not name:
        for s in states(h, 500):
            if s.ok:
                return s.sha, "the newest state whose checks passed"
        raise HostError("no state in the history passed its checks; name one (`loam host log`)")
    when = None
    m = AGO.match(name.strip())
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        when = datetime.now(timezone.utc) - timedelta(**{{"m": "minutes", "h": "hours",
                                                           "d": "days"}[unit]: n})
    else:
        with contextlib.suppress(ValueError):
            when = datetime.fromisoformat(name.strip()).astimezone(timezone.utc)
    if when is not None:
        sha = git(h, "rev-list", "-1", f"--before={int(when.timestamp())}", "HEAD").stdout.strip()
        if not sha:
            raise HostError(f"the history has no state from before {when:%Y-%m-%d %H:%M} UTC")
        return sha, f"the newest state at or before {when.astimezone():%Y-%m-%d %H:%M %Z}"
    done = git(h, "rev-parse", "-q", "--verify", f"{name}^{{commit}}", check=False)
    if done.returncode:
        raise HostError(f"{name!r} names no state: give a commit from `loam host log`, a "
                        f"duration like 2h, or a time like '2026-11-19 09:00'")
    return done.stdout.strip(), f"state {name}"


def unpack(h: Path, sha: str, into: Path) -> tuple[dict[str, Entry], list[Rule], dict]:
    """A state's files, written under ``into`` (git archive keeps links), its
    records and its scope."""
    arc = subprocess.run(["git", "-c", f"safe.directory={h}", "-C", str(h), "archive", sha],
                         capture_output=True, check=True, env=ENV)
    subprocess.run(["tar", "-x", "-C", str(into)], input=arc.stdout, check=True, env=ENV)
    scope = json.loads((into / SCOPE).read_text())
    out: dict[str, Entry] = {}
    meta = into / META
    for line in (meta.read_text().splitlines() if meta.exists() else []):
        path, kind, mode, owner, group, secret = line.split("\t")
        m = into / FILES / path.lstrip("/")
        if kind == "big":
            continue        # never kept, so never put back
        data = os.readlink(m).encode() if kind == "link" else m.read_bytes()
        out[path] = Entry(path, kind, int(mode, 8), owner, group, secret == "secret", data)
    return out, [Rule.from_dict(r) for r in scope["rules"]], scope["about"]


# -- the plan ------------------------------------------------------------------------------

@dataclass
class Step:
    action: str          # "create" | "change" | "remove"
    path: str
    want: Entry | None
    have: Entry | None
    how: list[str]


def plan(target: dict[str, Entry], rule_list: list[Rule], only: list[str]) -> list[Step]:
    live_paths = set(matched(rule_list)) | set(target)
    steps = []
    for path in sorted(live_paths):
        if only and not any(path == o or path.startswith(o.rstrip("/") + "/") for o in only):
            continue
        have = read_live(path)
        want = target.get(path)
        if have is not None and have.kind == "big":
            continue        # never kept: left as it is
        if want is None and have is not None:
            steps.append(Step("remove", path, None, have, []))
        elif want is not None and have is None:
            steps.append(Step("create", path, want, None, []))
        elif want is not None and have is not None and not want.same(have):
            steps.append(Step("change", path, want, have, want.differences(have)))
    return steps


UNIT_SUFFIX = re.compile(r"\.(service|timer|path|socket|target|mount)$")


@dataclass
class After:
    system_reload: bool = False
    user_reload: bool = False
    start: list = None          # (scope, unit)
    stop: list = None
    restart: list = None
    caddy: bool = False
    sysctl: bool = False
    compose: list = None
    other: list = None
    user: str = ""


COMPOSE = ("compose.yaml", "compose.yml", "docker-compose.yml")


def _compose_beside(path: str) -> str:
    for c in COMPOSE:
        f = os.path.join(os.path.dirname(path), c)
        if os.path.exists(f):
            return f
    return ""


def consequences(steps: list[Step], about: dict) -> After:
    """What has to follow the writes for them to take effect."""
    user_units = about.get("user_units", "")
    a = After(start=[], stop=[], restart=[], compose=[], other=[], user=about.get("user", ""))
    for s in steps:
        p = s.path
        scope = ("user" if user_units and p.startswith(user_units + "/") else
                 "system" if p.startswith("/etc/systemd/system/") else None)
        if scope:
            if scope == "user":
                a.user_reload = True
            else:
                a.system_reload = True
            parent = os.path.basename(os.path.dirname(p))
            unit = os.path.basename(p)
            if re.search(r"\.(wants|requires)$", parent):
                (a.start if s.action == "create" else a.stop if s.action == "remove"
                 else a.restart).append((scope, unit))
            elif parent.endswith(".d"):
                a.restart.append((scope, parent[:-2]))
            elif UNIT_SUFFIX.search(unit):
                if s.action == "remove":
                    a.stop.append((scope, unit))
                else:
                    a.restart.append((scope, unit.replace("@.", "@*.")))
            continue
        name = os.path.basename(p)
        if name == "Caddyfile" or p.startswith("/etc/caddy/"):
            a.caddy = True
        elif p.startswith("/etc/sysctl.d/"):
            a.sysctl = True
        elif p.startswith(about.get("config_dir", "/etc/loam") + "/") and (
                name in COMPOSE or (name.endswith(".env") and _compose_beside(p))):
            f = p if name in COMPOSE else _compose_beside(p)
            if f not in a.compose:
                a.compose.append(f)
        else:
            a.other.append(p)
    # a unit both started and restarted is only started
    started = set(a.start)
    a.restart = sorted({r for r in a.restart if r not in started})
    return a


def describe(steps: list[Step], after: After, how: str, sha: str) -> str:
    out = [f"Putting back {how}: {sha[:10]}.", ""]
    if not steps:
        return "\n".join(out[:1] + ["", "Nothing differs from it."])
    words = {"create": "create", "change": "change", "remove": "remove"}
    for s in steps:
        tag = " (secret: contents not shown)" if (s.want or s.have).secret else ""
        detail = f" [{', '.join(s.how)}]" if s.how else ""
        out.append(f"  {words[s.action]:<7} {s.path}{detail}{tag}")
    out.append("")
    then = []
    if after.stop:
        then.append("stop " + ", ".join(f"{u} ({sc})" for sc, u in after.stop))
    if after.system_reload:
        then.append("systemctl daemon-reload")
    if after.user_reload:
        then.append(f"systemctl --user daemon-reload (for {after.user or 'the install user'})")
    if after.start:
        then.append("start " + ", ".join(f"{u} ({sc})" for sc, u in after.start))
    if after.restart:
        then.append("restart if running: " + ", ".join(f"{u} ({sc})" for sc, u in after.restart))
    if after.caddy:
        then.append("reload the web server (caddy)")
    if after.sysctl:
        then.append("sysctl --system")
    for f in after.compose:
        then.append(f"docker compose -f {f} up -d")
    if then:
        out.append("Then: " + "; ".join(then) + ".")
    if after.other:
        out.append("Written and read by their programs when they next read them: "
                   + ", ".join(after.other) + ".")
    return "\n".join(out)


def diff(steps: list[Step]) -> str:
    """A unified diff of each non-secret text file that changes (have -> want)."""
    import difflib  # noqa: PLC0415
    out = []
    for s in steps:
        e = s.want or s.have
        if e.secret or e.kind != "file" or (s.want and s.want.kind != "file") or \
                (s.have and s.have.kind != "file"):
            continue
        try:
            a = (s.have.data if s.have else b"").decode().splitlines(keepends=True)
            b = (s.want.data if s.want else b"").decode().splitlines(keepends=True)
        except UnicodeDecodeError:
            continue
        out.extend(difflib.unified_diff(a, b, f"now{s.path}", f"then{s.path}"))
    return "".join(out)


# -- putting it back ------------------------------------------------------------------------

def _ids(owner: str, group: str) -> tuple[int, int]:
    try:
        uid = pwd.getpwnam(owner).pw_uid
    except KeyError:
        uid = int(owner) if owner.isdigit() else 0
    try:
        gid = grp.getgrnam(group).gr_gid
    except KeyError:
        gid = int(group) if group.isdigit() else 0
    return uid, gid


def write_entry(e: Entry) -> None:
    path = Path(e.path)
    uid, gid = _ids(e.owner, e.group)
    missing = []
    p = path.parent
    while not p.exists():
        missing.append(p)
        p = p.parent
    for d in reversed(missing):          # the folders made here are the file owner's
        d.mkdir(mode=0o700 if e.secret else 0o755)
        os.chown(d, uid, gid)
    tmp = path.parent / f".{path.name}.loam-restore-{os.getpid()}"
    with contextlib.suppress(FileNotFoundError):
        tmp.unlink()
    if e.kind == "link":
        os.symlink(e.data.decode(), tmp)
        os.lchown(tmp, uid, gid)
    else:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(e.data)
        os.chown(tmp, uid, gid)
        os.chmod(tmp, e.mode)
    os.replace(tmp, path)


def _run(argv: list[str], said: list[str]) -> bool:
    done = subprocess.run(argv, capture_output=True, text=True, env=ENV, check=False,
                          timeout=300)
    line = " ".join(argv)
    if done.returncode:
        said.append(f"  FAILED: {line}: {(done.stderr or done.stdout).strip()[-400:]}")
        return False
    said.append(f"  done: {line}")
    return True


def _is_active(unit: str) -> bool:
    return subprocess.run(["systemctl", "is-active", "--quiet", unit], env=ENV,
                          check=False).returncode == 0


def _systemctl(scope: str, user: str) -> list[str]:
    # root asks the install user's own manager (systemd 248 and later)
    return ["systemctl"] if scope == "system" else ["systemctl", "--user", "-M", f"{user}@"]


def apply(steps: list[Step], after: After, about: dict) -> list[str]:
    user = about.get("user", "")
    said: list[str] = []
    have_systemctl = bool(shutil.which("systemctl", path=ENV["PATH"]))
    if have_systemctl:
        for scope, unit in after.stop:
            _run(_systemctl(scope, user) + ["stop", unit], said)
    for s in steps:
        try:
            if s.action == "remove":
                os.unlink(s.path)
            else:
                write_entry(s.want)
            said.append(f"  {s.action}d: {s.path}" if s.action != "change"
                        else f"  changed: {s.path}")
        except OSError as exc:
            said.append(f"  FAILED: {s.action} {s.path}: {exc}")
    if have_systemctl:
        if after.system_reload:
            _run(["systemctl", "daemon-reload"], said)
        if after.user_reload and user:
            _run(_systemctl("user", user) + ["daemon-reload"], said)
        for scope, unit in after.start:
            _run(_systemctl(scope, user) + ["start", unit], said)
        for scope, unit in after.restart:
            _run(_systemctl(scope, user) + ["try-restart", unit], said)
        if after.caddy:
            _run(["systemctl", "reload" if _is_active("caddy") else "restart", "caddy"], said)
    if after.sysctl and shutil.which("sysctl", path=ENV["PATH"]):
        _run(["sysctl", "--system", "-q"], said)
    if after.compose and shutil.which("docker", path=ENV["PATH"]):
        for f in after.compose:
            _run(["docker", "compose", "-f", f, "up", "-d"], said)
    return said


def restore(name: str | None, *, only: list[str] | None = None, yes: bool = False,
            force: bool = False, show_diff: bool = False, dry: bool = False,
            h: Path | None = None, confirm=None, out=print, scope: tuple | None = None) -> int:
    """The one command. Returns an exit code; says everything through ``out``."""
    h = h or history_dir()
    if not (h / ".git").exists():
        raise HostError(f"there is no host history at {h}")
    sha, how = resolve(h, name)
    with tempfile.TemporaryDirectory(prefix="loam-host-") as tmp:
        os.chmod(tmp, 0o700)
        target, rule_list, about = unpack(h, sha, Path(tmp))
        steps = plan(target, rule_list, only or [])
        after = consequences(steps, about)
        out(describe(steps, after, how, sha))
        if not steps:
            recent = states(h, 6)
            if recent:
                out("\nThe newest states (name one, a duration like 2h, or a time):")
                for s in recent:
                    out(f"  {s.sha[:10]}  {s.when.astimezone():%Y-%m-%d %H:%M %Z}  "
                        f"{'ok ' if s.ok else 'BAD'}  "
                        f"{', '.join(c[2:] for c in s.changed[:3])}"
                        f"{' …' if len(s.changed) > 3 else ''}")
            return 0
        if show_diff:
            d = diff(steps)
            if d:
                out("\n" + d)
        bad = checks({s.path: s.want for s in steps if s.want}, root=Path(tmp) / FILES)
        if bad:
            out("\nThe state named does not pass its own checks:")
            out("\n".join(f"  {b}" for b in bad))
            if not force:
                out("Nothing was changed. Name another state, or add --force to write it anyway.")
                return 3
        if dry:
            out("\n--dry-run: nothing was changed.")
            return 0
        if not yes:
            if confirm is None:
                if not sys.stdin.isatty():
                    out("\nNothing was changed: run it again with --yes to put this back.")
                    return 2
                confirm = lambda: input("\nPut this back? [y/N] ").strip().lower() in ("y", "yes")  # noqa: E731
            if not confirm():
                out("Nothing was changed.")
                return 1
        before = snapshot(f"before restoring {sha[:10]}", h=h, scope=scope)
        undo = before["commit"] or git(h, "rev-parse", "HEAD").stdout.strip()
        out(f"\nSaved the present state first as {undo[:10]}.")
        said = apply(steps, after, about)
        out("\n".join(said))
        snapshot(f"restored {sha[:10]}", h=h, scope=scope)
        failed = [s for s in said if "FAILED" in s]
        out(f"\n{'Put back, with failures above' if failed else 'Put back'}. "
            f"To undo this restore: sudo loam host restore {undo[:10]}")
        return 4 if failed else 0


# -- its own units ------------------------------------------------------------------------

UNITS = ("loam-host-history.service", "loam-host-history.timer")
TIMER = "loam-host-history.timer"
SYSTEM_UNIT_DIR = Path("/etc/systemd/system")
ROOT_PYCACHE = "/var/cache/loam/pycache"


def render_units(root: Path, pycache: str = ROOT_PYCACHE) -> dict[str, str]:
    """The timer and its service as installed for the checkout at ``root``:
    what ``install_units`` writes and bin/check-drift compares."""
    out = {}
    for name in UNITS:
        src = root / "systemd" / "system" / name
        if src.is_file():        # a checkout from before 2026-11-19 has none
            out[name] = (src.read_text(encoding="utf-8").replace("@ROOT@", str(root))
                         .replace("@PYCACHE@", pycache))
    return out


def install_units(root: Path) -> str:
    """Write the units where they differ and enable the timer. Safe to repeat."""
    changed = []
    for name, text in render_units(root).items():
        dest = SYSTEM_UNIT_DIR / name
        if not dest.exists() or dest.read_text() != text:
            dest.write_text(text)
            changed.append(name)
    if changed:
        subprocess.run(["systemctl", "daemon-reload"], check=True, env=ENV)
    subprocess.run(["systemctl", "enable", "--now", TIMER], check=True, env=ENV,
                   capture_output=True)
    return f"{TIMER} enabled" + (f"; wrote {', '.join(changed)}" if changed else "")


def size(h: Path) -> str:
    done = subprocess.run(["du", "-sh", str(h / ".git")], capture_output=True, text=True,
                          env=ENV, check=False)
    return done.stdout.split()[0] if done.returncode == 0 else "?"

