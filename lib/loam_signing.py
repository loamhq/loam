"""Signed releases (2026-11-19): what a release tag must carry before an
install runs it, and the one check every side of it uses.

**A release is an SSH-signed git tag.** The tag object (``git tag -s`` with
``gpg.format=ssh``) names the release, the commit, and carries a signature
made with the maintainer's key. Checking it needs only ``git`` and
``ssh-keygen`` (OpenSSH 8.1 or newer, on every Linux and macOS box an
install runs on): no keyserver, no GnuPG keyring, no network.

**The keys a box trusts** are the lines of ``keys/release-signers`` in the
release the box is already running (``HEAD``'s committed copy, never the
working tree and never the incoming release's). The file is OpenSSH's
``allowed_signers`` format. A release that adds a key is accepted because an
old key signed it; the next one may then be signed by the new key. That is
rotation (docs/signing.md).

**While the file holds no key** nothing is checked, and every side says so
in words: the updater's status, a cut release's receipt. That is the state
of every release up to the one that first ships a key; the first hop onto
that release is trusted the way every update was before (TLS to the place
releases come from, and whoever can push a tag there).

The installer (``install.sh``) cannot import this module, because this
module comes from the clone it has not checked yet; it carries the same
check in shell, and its own copy of the keys, which a test keeps equal to
``keys/release-signers``.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

SIGNERS_FILE = "keys/release-signers"
NAMESPACE = "git"
SSH_ARMOR = b"-----BEGIN SSH SIGNATURE-----"
# Words for each way a check can fail; `reason` in a result.
REASONS = {
    "unsigned": "it is not signed",
    "not-ssh": "it is signed, but not with an SSH key",
    "untrusted-key": "it is signed, but by a key this install does not trust",
    "bad-signature": "its signature does not match its contents (it was "
                     "changed after it was signed)",
    "wrong-name": "the signed tag inside names a different release",
    "cannot-check": "this install cannot check signatures (ssh-keygen is "
                    "missing or too old)",
    "missing": "there is no such tag",
}


def key_lines(text: str | None) -> list[str]:
    """The key lines of an ``allowed_signers`` text: not blank, not ``#``."""
    return [line.strip() for line in (text or "").splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          check=False)


def signers_at(repo: Path, rev: str, prefix: str = "") -> str | None:
    """``keys/release-signers`` as committed at ``rev``, or None."""
    done = _git(repo, "show", f"{rev}:{prefix}{SIGNERS_FILE}")
    return done.stdout.decode("utf-8", "replace") if done.returncode == 0 else None


def split_tag(raw: bytes) -> tuple[bytes, bytes] | None:
    """A tag object's bytes as (payload, signature), cut where git cuts it:
    at the first line that opens an SSH signature. None when there is none."""
    at = 0
    while True:
        i = raw.find(SSH_ARMOR, at)
        if i < 0:
            return None
        if i == 0 or raw[i - 1:i] == b"\n":
            return raw[:i], raw[i:]
        at = i + 1


def headers(payload: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in payload.split(b"\n"):
        if not line:
            break
        key, _, value = line.decode("utf-8", "replace").partition(" ")
        out.setdefault(key, value)
    return out


def _fail(tag: str, reason: str, **extra) -> dict:
    return {"ok": False, "tag": tag, "reason": reason,
            "message": f"Release {tag} was refused: {REASONS[reason]}.", **extra}


def verify_tag(repo: Path, tag: str, signers: str | None) -> dict:
    """Check the tag ``refs/tags/<tag>`` in ``repo`` against ``signers``
    (an ``allowed_signers`` text). Never raises.

        ok          True when the signature verifies against a trusted key
        checked     False only when ``signers`` holds no key (nothing to
                    check against; ``ok`` is then True and says nothing)
        commit      the commit the verified tag object names: move to this,
                    never to the tag's name again, which can be re-pointed
        object      the tag object checked
        principal   the signer's name in the signers file
        fingerprint the signing key's
        reason      on failure, a key of REASONS; ``message`` in words
    """
    obj = _git(repo, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}")
    if obj.returncode != 0:
        return _fail(tag, "missing")
    obj_sha = obj.stdout.decode().strip()
    commit = _git(repo, "rev-parse", "--verify", "--quiet", f"{obj_sha}^{{commit}}")
    commit_sha = commit.stdout.decode().strip() if commit.returncode == 0 else None
    keys = key_lines(signers)
    if not keys:
        return {"ok": True, "checked": False, "tag": tag, "object": obj_sha,
                "commit": commit_sha,
                "message": "Releases are not checked: the running release carries "
                           "no maintainer key."}
    base = {"checked": True, "object": obj_sha}
    if _git(repo, "cat-file", "-t", obj_sha).stdout.decode().strip() != "tag":
        return _fail(tag, "unsigned", **base)
    raw = _git(repo, "cat-file", "tag", obj_sha).stdout
    parts = split_tag(raw)
    if parts is None:
        return _fail(tag, "not-ssh" if b"-----BEGIN PGP SIGNATURE-----" in raw
                     else "unsigned", **base)
    payload, sig = parts
    head = headers(payload)
    if head.get("tag") != tag or head.get("type") != "commit":
        return _fail(tag, "wrong-name", **base)
    keygen = shutil.which("ssh-keygen")
    if not keygen:
        return _fail(tag, "cannot-check", **base)
    with tempfile.TemporaryDirectory(prefix="loam-verify-") as tmp:
        allowed, sigfile = Path(tmp) / "allowed", Path(tmp) / "sig"
        allowed.write_text("\n".join(keys) + "\n", encoding="utf-8")
        sigfile.write_bytes(sig)
        found = subprocess.run([keygen, "-Y", "find-principals", "-f", str(allowed),
                                "-s", str(sigfile)], capture_output=True, text=True,
                               check=False)
        if found.returncode == 255 and "unknown option" in found.stderr.lower():
            return _fail(tag, "cannot-check", **base)
        principals = [p for p in found.stdout.split() if p]
        if found.returncode != 0 or not principals:
            return _fail(tag, "untrusted-key", **base)
        for principal in principals:
            done = subprocess.run([keygen, "-Y", "verify", "-f", str(allowed),
                                   "-I", principal, "-n", NAMESPACE, "-s", str(sigfile)],
                                  input=payload, capture_output=True, check=False)
            if done.returncode == 0:
                said = (done.stdout + done.stderr).decode("utf-8", "replace")
                fp = re.search(r"SHA256:[A-Za-z0-9+/=]+", said)
                return {"ok": True, "checked": True, "tag": tag, "object": obj_sha,
                        "commit": head.get("object") or commit_sha,
                        "principal": principal,
                        "fingerprint": fp.group(0) if fp else None,
                        "message": f"Release {tag} is signed by {principal}"
                                   + (f" ({fp.group(0)})" if fp else "") + "."}
    return _fail(tag, "bad-signature", **base)


# -- what a new release must verify against (the release step's side) ----------

TAG_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def _version(name: str) -> tuple[int, int, int] | None:
    m = TAG_RE.match(name)
    return tuple(int(x) for x in m.groups()) if m else None


def previous_release(repo: Path, tag: str) -> str | None:
    """The newest release tag in ``repo`` older than ``tag``."""
    mine = _version(tag)
    if not mine:
        return None
    names = _git(repo, "tag", "-l").stdout.decode().split()
    older = [t for t in names if _version(t) and _version(t) < mine]
    return max(older, key=_version) if older else None


def required_signers(repo: Path, tag: str, rev: str) -> list[tuple[str, str]]:
    """The signers files a release ``tag`` of ``rev`` must verify against,
    as (where, text), each only when it holds a key: the previous release's
    (what every box on it checks with) and the new tree's (what the boxes
    after it will). Empty: the release need not be signed."""
    out = []
    prev = previous_release(repo, tag)
    for where, at in ((f"release {prev}", prev), ("the release's own tree", rev)):
        text = signers_at(repo, at) if at else None
        if key_lines(text):
            out.append((where, text))
    return out
