"""The provider CLIs this release of Loam was tested with, and putting them
on the box.

**``PINS`` is the one place.** A release names the build of each CLI it was
tested with here. A fresh install (``bin/loam-install``, step 7) and an
update to a new release (``lib/loam_update.py``, the ``providers`` step) put
exactly these builds on the box, and nothing else moves a CLI between
releases: no timer, no follower, no CLI updating itself (each lane's
environment turns the CLIs' own updaters off; ``SELF_UPDATE_OFF``). The
owner, 2026-11-19, on each release carrying the versions it was tested with
and a new version arriving with the next release: "Sgtm". It replaced the
daily follower of 2026-11-11 (``provider-follow``), which twice in the week
of 11-16 read Loam's own wake failures as a bad Claude Code build, rolled
back, and restarted everything overnight.

**Moving a pin** is a change to ``PINS`` and nothing else. ``bin/provider-pin
test`` installs the pinned builds beside the live ones and runs the live
parity tests with every CLI path pointed at them by their full paths, so a
branch that moves a pin is tested on the new build before it lands, with the
links untouched. Once landed, ``bin/provider-pin apply`` on a box (or ``loam
update`` on an install) moves the links.

**The layout**, one per provider: a versioned build under ``versions/``
beside the others, and the link the lanes run (``~/.local/bin/<cli>``)
pointing at one of them. A build is installed beside, checked by its own
``--version``, and only then is the link moved, atomically.

**Antigravity** has no install-a-version: its release server answers only
its latest build. Its pin is therefore the build's own address and SHA-512,
read off that server's manifest the day the pin was set, and the install
fetches that file and checks it. While Google keeps the file, the pin
installs; once it does not, an install of this release fails at that step
and says so (it never quietly takes the latest). Its own updater is off in
every lane (``AGY_CLI_DISABLE_AUTO_UPDATE=true``; ``1`` is not read, found
2026-11-19 off its log), but an ``agy`` the owner runs by hand in a shell
without the variable can still update itself: ``bin/check-drift`` reports a
link whose ``--version`` is not the pin, and ``provider-pin apply`` puts it
back.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

import loam_config as lc

# --- the pin ------------------------------------------------------------------

# The builds this release was tested with. Set 2026-11-19 at what this box
# ran and had passed on: Claude Code 2.1.328 (live since 02:51 PT that day
# after its smoke test and a clean hour), Codex 0.170.0 (live since 11-18,
# a clean hour), Antigravity 1.5.12 (what it had updated itself to on 11-18).
PINS = {
    "claude-code": {"version": "2.1.328"},
    "codex": {"version": "0.170.0"},
    "antigravity": {
        "version": "1.5.12",
        # From https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/<platform>.json
        "builds": {
            "linux-x64": {
                "url": "https://storage.googleapis.com/antigravity-public/antigravity-cli/"
                       "1.5.12-5281398513336320/linux-x64/cli_linux_x64.tar.gz",
                "sha512": "26de25fccb3fbf0b093e090ce14a14ac1df1ec3e90ef4ea6106887d3a81b0cc9"
                          "edb0dadea7b9101d07f9606eba159062a6fb7b78e91b73f7520684a24db73428",
            },
            "linux-arm64": {
                "url": "https://storage.googleapis.com/antigravity-public/antigravity-cli/"
                       "1.5.12-5281398513336320/linux-arm/cli_linux_arm64.tar.gz",
                "sha512": "1030d89ce9e5cb39509e272cb89b71fde0e0d5d6527b7392525d5ef2683da54f"
                          "b49230449107486d91c02ded39a678faaa649b2041304a6cf82a3a332d5819d9",
            },
        },
    },
}

# What turns each CLI's own updater off, in every lane's environment (the
# units, the broker's children, engineer jobs). Claude Code's was already
# there; Antigravity's since 2026-11-19. Codex installed from npm does not
# update itself.
SELF_UPDATE_OFF = {"DISABLE_AUTOUPDATER": "1", "AGY_CLI_DISABLE_AUTO_UPDATE": "true"}

# Builds kept on disk besides the pinned one: the most recent others, so a
# rollback of a release (`loam update rollback`) finds its pin still there.
KEEP_OTHERS = 2

VERSION_RE = re.compile(r"^\d+(\.\d+)*$")


def vkey(version: str) -> tuple[int, ...]:
    return tuple(int(x) for x in version.split("."))


def is_version(text: str | None) -> bool:
    return bool(text and VERSION_RE.match(text))


def arch() -> str:
    return {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64",
            "arm64": "arm64"}.get(platform.machine(), platform.machine())


def fetch(url: str, timeout: float = 600) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "loam-provider-pin"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read()


def _partial(versions: Path, version: str) -> Path:
    return versions / f".{version}.pin-partial-{os.getpid()}"


# --- one provider ---------------------------------------------------------------

class Provider:
    """One CLI: its link, its builds beside each other, and how to install one."""
    name = ""
    label = ""

    def __init__(self, link: Path, versions: Path) -> None:
        self.link, self.versions = Path(link), Path(versions)

    @property
    def pin(self) -> str:
        return PINS[self.name]["version"]

    def build(self, version: str) -> Path:
        """A version's binary, by its full path."""
        raise NotImplementedError

    def entry(self, version: str) -> Path:
        """What lives in ``versions/`` for a version."""
        return self.versions / version

    def install(self, version: str) -> None:
        raise NotImplementedError

    def live(self) -> str | None:
        """The version the link points at, or None (no link, or not one of ours)."""
        try:
            target = Path(os.readlink(self.link))
        except OSError:
            return None
        if not target.is_absolute():
            target = self.link.parent / target
        try:
            rel = target.relative_to(self.versions)
        except ValueError:
            return None
        return rel.parts[0] if rel.parts else None

    def installed(self) -> list[str]:
        try:
            return sorted((p.name for p in self.versions.iterdir()
                           if is_version(p.name) and self.build(p.name).exists()),
                          key=vkey)
        except OSError:
            return []

    def version_of(self, binary: Path) -> str | None:
        try:
            done = subprocess.run([str(binary), "--version"], capture_output=True,
                                  text=True, timeout=60, stdin=subprocess.DEVNULL,
                                  env={**os.environ, **SELF_UPDATE_OFF})
        except (OSError, subprocess.SubprocessError):
            return None
        m = re.search(r"\d+\.\d+\.\d+", done.stdout)
        return m.group(0) if done.returncode == 0 and m else None

    def present(self) -> bool:
        """This CLI is on the box at all (a link or a file where the lanes run it)."""
        return self.link.exists() or self.link.is_symlink()

    def ensure(self, version: str | None = None) -> Path:
        """The build of ``version`` (default: the pin) on disk, installed
        beside the others if it was not; the link is never touched."""
        version = version or self.pin
        if not self.build(version).exists():
            self.install(version)
        got = self.version_of(self.build(version))
        if got != version:
            raise RuntimeError(f"{self.build(version)} --version says {got!r}, "
                               f"not {version}")
        return self.build(version)

    def repoint(self, version: str) -> None:
        """Move the link to a version's build, atomically."""
        target = self.build(version)
        if not target.exists():
            raise FileNotFoundError(target)
        self.link.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.link.with_name(f".{self.link.name}.pin-{os.getpid()}")
        tmp.unlink(missing_ok=True)
        os.symlink(target, tmp)
        os.replace(tmp, self.link)

    def prunable(self) -> list[str]:
        """Builds to remove: all but the pinned one, the live one, and the
        KEEP_OTHERS highest others."""
        keep = {self.pin, self.live()}
        others = [v for v in self.installed() if v not in keep]
        return others[:-KEEP_OTHERS] if KEEP_OTHERS else others

    def remove(self, version: str) -> None:
        entry = self.entry(version)
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink(missing_ok=True)


class ClaudeCode(Provider):
    name, label = "claude-code", "Claude Code"
    DOWNLOADS = "https://downloads.claude.ai/claude-code-releases"

    def __init__(self, link: Path | None = None, versions: Path | None = None) -> None:
        super().__init__(link or lc.CLAUDE_BIN,
                         versions or lc.HOME / ".local/share/claude/versions")

    def build(self, version: str) -> Path:
        return self.versions / version

    def install(self, version: str) -> None:
        """Straight from the release bucket, checked against its manifest's
        checksum. The vendor's installer is not used: it moves the link."""
        plat = f"linux-{arch()}"
        manifest = json.loads(fetch(f"{self.DOWNLOADS}/{version}/manifest.json", 60))
        want = manifest["platforms"][plat]["checksum"]
        blob = fetch(f"{self.DOWNLOADS}/{version}/{plat}/claude")
        if hashlib.sha256(blob).hexdigest() != want:
            raise ValueError(f"Claude Code {version}: checksum does not match its manifest")
        self.versions.mkdir(parents=True, exist_ok=True)
        partial = _partial(self.versions, version)
        partial.write_bytes(blob)
        partial.chmod(0o755)
        os.replace(partial, self.build(version))


class Codex(Provider):
    name, label = "codex", "Codex"

    def __init__(self, link: Path | None = None, versions: Path | None = None) -> None:
        super().__init__(link or lc.CODEX_BIN,
                         versions or lc.HOME / ".local/share/codex/versions")

    def build(self, version: str) -> Path:
        return self.versions / version / "node_modules" / ".bin" / "codex"

    def install(self, version: str) -> None:
        """An npm prefix holding ``@openai/codex@<v>`` and nothing else."""
        self.versions.mkdir(parents=True, exist_ok=True)
        partial = _partial(self.versions, version)
        shutil.rmtree(partial, ignore_errors=True)
        done = subprocess.run(["npm", "install", "--prefix", str(partial), "--no-audit",
                               "--no-fund", "--loglevel=error", f"@openai/codex@{version}"],
                              capture_output=True, text=True, timeout=900,
                              stdin=subprocess.DEVNULL)
        if done.returncode != 0:
            shutil.rmtree(partial, ignore_errors=True)
            raise RuntimeError(f"npm install exited {done.returncode}: {done.stderr[-300:]}")
        shutil.rmtree(self.entry(version), ignore_errors=True)
        os.replace(partial, self.entry(version))


class Antigravity(Provider):
    name, label = "antigravity", "Antigravity"

    def __init__(self, link: Path | None = None, versions: Path | None = None) -> None:
        super().__init__(link or lc.AGY_BIN,
                         versions or lc.HOME / ".local/share/agy/versions")

    def build(self, version: str) -> Path:
        return self.versions / version / "agy"

    def install(self, version: str) -> None:
        """The pinned build's own file, by the address and SHA-512 the pin
        recorded (the release server offers no other version)."""
        spec = PINS[self.name]
        if version != spec["version"]:
            raise ValueError(f"Antigravity {version}: only the pinned build "
                             f"({spec['version']}) can be installed; its release "
                             f"server serves no other version by name")
        build = spec["builds"].get(f"linux-{arch()}")
        if not build:
            raise ValueError(f"Antigravity {version}: the pin has no build for linux-{arch()}")
        blob = fetch(build["url"])
        if hashlib.sha512(blob).hexdigest() != build["sha512"]:
            raise ValueError(f"Antigravity {version}: the download's SHA-512 is not the pin's")
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
            member = tar.getmember("antigravity")
            data = tar.extractfile(member).read()
        self.versions.mkdir(parents=True, exist_ok=True)
        partial = _partial(self.versions, version)
        shutil.rmtree(partial, ignore_errors=True)
        partial.mkdir()
        (partial / "agy").write_bytes(data)
        (partial / "agy").chmod(0o755)
        shutil.rmtree(self.entry(version), ignore_errors=True)
        os.replace(partial, self.entry(version))


PROVIDERS = (ClaudeCode, Codex, Antigravity)


def providers(names: list[str] | None = None) -> list[Provider]:
    every = [cls() for cls in PROVIDERS]
    if not names:
        return every
    known = {p.name: p for p in every}
    unknown = [n for n in names if n not in known]
    if unknown:
        raise ValueError(f"unknown provider(s) {unknown}; known: {sorted(known)}")
    return [known[n] for n in names]


# --- the box against the pin -------------------------------------------------------

def status(p: Provider) -> dict:
    said = p.version_of(p.link) if p.present() else None
    return {"provider": p.name, "pinned": p.pin, "link": str(p.link),
            "points_at": p.live(), "says": said, "installed": p.installed(),
            "as_pinned": p.live() == p.pin and said == p.pin}


def drift(only_present: bool = True) -> list[str]:
    """What on the box differs from the pin, as sentences; empty when none.
    A provider not on the box at all is not drift (``only_present``)."""
    out = []
    for p in providers():
        if only_present and not p.present():
            continue
        row = status(p)
        if row["as_pinned"]:
            continue
        out.append(f"{p.label}: {p.link} points at {row['points_at'] or 'no pinned build'} "
                   f"and says {row['says'] or 'nothing'}; this release pins "
                   f"{p.pin}. `bin/provider-pin apply` puts the pin in place.")
    return out


def apply(names: list[str] | None = None, dry: bool = False,
          prune: bool = True) -> list[dict]:
    """Every provider at its pin: installed beside if missing, checked by
    its own ``--version``, and the link moved. One row per provider:
    ``before``, ``after``, ``moved``, ``error``. Nothing restarts here; a
    caller that moved a link restarts what holds the old build."""
    rows = []
    for p in providers(names):
        row = {"provider": p.name, "pinned": p.pin, "before": p.live(),
               "before_says": p.version_of(p.link) if p.present() else None,
               "moved": False, "installed": False, "removed": [], "error": None}
        try:
            if row["before"] == p.pin and row["before_says"] == p.pin:
                row["after"] = p.pin
            elif dry:
                row["after"] = row["before"]
                row["would"] = (f"install {p.pin} beside and move the link"
                                if not p.build(p.pin).exists() else "move the link")
            else:
                row["installed"] = not p.build(p.pin).exists()
                p.ensure()
                p.repoint(p.pin)
                row["after"], row["moved"] = p.live(), True
                if p.version_of(p.link) != p.pin:
                    raise RuntimeError(f"{p.link} --version does not say {p.pin} "
                                       f"after the move")
            if prune and not dry:
                for v in p.prunable():
                    p.remove(v)
                    row["removed"].append(v)
        except Exception as exc:  # noqa: BLE001 -- the row says it
            row["error"] = f"{type(exc).__name__}: {exc}"
            row.setdefault("after", p.live())
        rows.append(row)
    return rows
