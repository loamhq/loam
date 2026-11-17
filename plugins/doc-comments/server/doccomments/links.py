"""Share links: who outside Loam may open which doc, and how they prove it.

Kept in the plugin's private folder (``LOAM_PLUGIN_PRIVATE``, 0700, never
in the vault or a repository), in ``links.json``:

- each link: its id, the doc's id, its **token** (the long random part of
  ``/s/<token>``, kept so the owner can copy the link again), its mode
  (``view``, ``comment``, ``edit``), the person it is for (or none: a group
  link), its password **hashed** (PBKDF2-SHA256, 200,000 rounds, a salt of
  its own), how many days a browser is remembered, whether it is on, a
  generation number (bumped when it is turned off, so a session from before
  is refused even after Undo... no: Undo is the same generation, see
  ``set_on``), the people who joined a group link, and its agents' keys,
  **hashed** (SHA-256 of a 32-byte random key);
- one secret, which signs the browsers' session cookies (HMAC-SHA256);
- the failed-password record, for throttling.

The sidecar keeps what anyone reading the vault may see of a link: its id,
mode, person, days, whether it is on, and who joined. Never a token, hash or
key.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

from .sidecar import NoteError, now_iso

MODES = ("view", "comment", "edit")
PBKDF2_ROUNDS = 200_000
DEFAULT_DAYS = 30
MAX_DAYS = 365
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}$")

# Throttling of wrong passwords (per link and client address):
#   from the 4th wrong try in 15 minutes, each answer waits 2^(n-3) seconds
#   (capped at 8); the 10th in 15 minutes blocks that address for that link
#   for 15 minutes. Across all addresses, 100 wrong tries in an hour lock the
#   link for an hour (it still says so to the owner, who can change the
#   password, which clears the lock).
FAIL_WINDOW = 15 * 60
FAIL_SLOW_AFTER = 3
FAIL_BLOCK_AT = 10
FAIL_BLOCK_FOR = 15 * 60
LINK_FAIL_WINDOW = 3600
LINK_FAIL_LOCK_AT = 100
LINK_LOCK_FOR = 3600

# Limits on what one link may write, so a link cannot fill the disk.
WRITES_PER_HOUR = 120
WRITES_PER_DAY = 600
SIDECAR_MAX_BYTES = 4 * 1024 * 1024

WORDS = ("amber acorn apple aspen autumn badger basil beacon birch bramble breeze "
         "brook cedar cherry cider clover cobalt comet copper coral cotton crane "
         "cricket daisy dawn delta dune ember fennel fern fig finch flint fog "
         "forest fox garnet ginger glade granite hazel heron honey indigo iris "
         "ivy jasper juniper kelp kestrel kettle lantern larch lark laurel lemon "
         "lichen lilac linen lotus lumen maple marble meadow mint moss nectar "
         "nutmeg oak oat olive opal orchard otter pebble pepper pine plum poppy "
         "quartz quill rain raven reed river robin rose rowan saffron sage salt "
         "shell sorrel sparrow spruce stone storm sumac sun swift thistle thyme "
         "tide tulip velvet violet walnut wave willow wren yarrow").split()


def make_password() -> str:
    return f"{secrets.choice(WORDS)}-{secrets.choice(WORDS)}-{secrets.randbelow(90) + 10}"


def _hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return salt.hex(), digest.hex()


def _key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "guest"


def clean_name(name) -> str:
    if not isinstance(name, str):
        raise NoteError(400, "a name is text")
    name = " ".join(name.split())
    name = "".join(ch for ch in name if ch.isprintable())
    if not 1 <= len(name) <= 60:
        raise NoteError(400, "a name is 1 to 60 characters")
    return name


class Links:
    def __init__(self, folder: str | Path, clock=time.time):
        self.folder = Path(folder)
        self.path = self.folder / "links.json"
        self.clock = clock
        self._lock = threading.RLock()

    # -- the file ------------------------------------------------------------------

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {}
        data.setdefault("secret", secrets.token_hex(32))
        data.setdefault("links", {})
        data.setdefault("fails", {})
        data.setdefault("writes", {})
        data.setdefault("seen", {})
        return data

    def _write(self, data: dict) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.folder, 0o700)
        except OSError:
            pass
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, self.path)

    def _mutate(self, fn):
        with self._lock:
            data = self._read()
            out = fn(data)
            self._write(data)
            return out

    # -- links ---------------------------------------------------------------------

    def create(self, did: str, mode: str, name: str | None = None, days: int = DEFAULT_DAYS,
               password: str | None = None) -> tuple[dict, str, str]:
        """A new link: ``(link, token, password)``; the two secrets are
        returned once and stored as the token (the owner's to copy again) and
        a hash (nobody's to read back)."""
        if mode not in MODES:
            raise NoteError(400, "a link is view, comment or edit")
        days = self._days(days)
        name = clean_name(name) if name not in (None, "") else None
        password = self._password(password) if password else make_password()
        token = secrets.token_urlsafe(24)          # 32 characters, 192 bits
        salt, digest = _hash_password(password)
        link = {"id": "l-" + secrets.token_hex(5), "did": did, "token": token, "mode": mode,
                "name": name, "days": days, "salt": salt, "hash": digest, "on": True,
                "gen": 1, "created": now_iso(), "off_at": None, "people": [], "agents": [],
                "locked_until": 0}
        self._mutate(lambda d: d["links"].__setitem__(link["id"], link))
        return link, token, password

    @staticmethod
    def _days(days) -> int:
        if not isinstance(days, int) or not 1 <= days <= MAX_DAYS:
            raise NoteError(400, f"a browser is remembered 1 to {MAX_DAYS} days")
        return days

    @staticmethod
    def _password(password) -> str:
        if not isinstance(password, str) or not 6 <= len(password) <= 100:
            raise NoteError(400, "a password is 6 to 100 characters")
        return password

    def get(self, lid: str) -> dict | None:
        with self._lock:
            return self._read()["links"].get(lid)

    def for_doc(self, did: str) -> list[dict]:
        with self._lock:
            return [l for l in self._read()["links"].values() if l["did"] == did]

    def all(self) -> list[dict]:
        with self._lock:
            return list(self._read()["links"].values())

    def by_token(self, token: str) -> dict | None:
        if not isinstance(token, str) or not TOKEN_RE.match(token):
            return None
        with self._lock:
            for link in self._read()["links"].values():
                if hmac.compare_digest(link["token"], token):
                    return link
        return None

    def update(self, lid: str, mode=None, days=None, on=None, name=None, new_password=None):
        """Change a link. ``new_password``: True for a fresh one Loam makes,
        or the text of one; answers ``(link, password or None)``."""
        made = {}

        def fn(d):
            link = d["links"].get(lid)
            if not link:
                raise NoteError(404, "no such link")
            if mode is not None:
                if mode not in MODES:
                    raise NoteError(400, "a link is view, comment or edit")
                link["mode"] = mode
            if days is not None:
                link["days"] = self._days(days)
            if name is not None:
                link["name"] = clean_name(name) if name else None
            if on is not None:
                link["on"] = bool(on)
                link["off_at"] = None if on else now_iso()
            if new_password is not None:
                pw = make_password() if new_password is True else self._password(new_password)
                link["salt"], link["hash"] = _hash_password(pw)
                link["locked_until"] = 0
                made["password"] = pw
            return dict(link)
        link = self._mutate(fn)
        return link, made.get("password")

    # -- passwords and throttling -----------------------------------------------------

    def check_password(self, link: dict, password: str, client: str) -> tuple[bool, float, str | None]:
        """``(ok, wait_seconds, refusal)``: wait before answering, and the
        sentence to give when the address or the link is blocked."""
        now = self.clock()
        key = f"{link['id']}|{client}"
        with self._lock:
            data = self._read()
            live = data["links"].get(link["id"]) or link
            if live.get("locked_until", 0) > now:
                return False, 0, "This link is locked for a while after many wrong passwords."
            fails = [t for t in data["fails"].get(key, []) if now - t < FAIL_WINDOW]
            blocked = [t for t in data["fails"].get(key + "|blocked", []) if now - t < FAIL_BLOCK_FOR]
            if blocked:
                return False, 0, "Too many wrong passwords. Try again in a few minutes."
            ok = isinstance(password, str) and 0 < len(password) <= 200 and hmac.compare_digest(
                _hash_password(password, bytes.fromhex(live["salt"]))[1], live["hash"])
            if ok:
                data["fails"].pop(key, None)
                self._write(data)
                return True, 0, None
            fails.append(now)
            data["fails"][key] = fails
            if len(fails) >= FAIL_BLOCK_AT:
                data["fails"][key + "|blocked"] = [now]
                data["fails"].pop(key, None)
            link_key = f"{link['id']}|*"
            all_fails = [t for t in data["fails"].get(link_key, []) if now - t < LINK_FAIL_WINDOW]
            all_fails.append(now)
            data["fails"][link_key] = all_fails
            if len(all_fails) >= LINK_FAIL_LOCK_AT:
                live["locked_until"] = now + LINK_LOCK_FOR
                data["fails"][link_key] = []
            # forget stale entries so the file never grows without bound
            data["fails"] = {k: v for k, v in data["fails"].items()
                             if v and now - max(v) < max(FAIL_WINDOW, FAIL_BLOCK_FOR, LINK_FAIL_WINDOW)}
            self._write(data)
            n = len(fails)
            wait = 0 if n <= FAIL_SLOW_AFTER else min(8, 2 ** (n - FAIL_SLOW_AFTER))
            return False, wait, None

    # -- sessions ----------------------------------------------------------------------

    def _secret(self) -> bytes:
        with self._lock:
            data = self._read()
            if not self.path.exists():
                self._write(data)
            return bytes.fromhex(data["secret"])

    def session_cookie(self, link: dict, visitor: str | None) -> tuple[str, int]:
        """A signed session for this browser: ``(value, max_age)``."""
        exp = int(self.clock()) + link["days"] * 86400
        body = _b64(json.dumps({"l": link["id"], "g": link["gen"], "v": visitor, "e": exp},
                               separators=(",", ":")).encode())
        sig = _b64(hmac.new(self._secret(), body.encode(), hashlib.sha256).digest())
        return f"{body}.{sig}", link["days"] * 86400

    def read_session(self, link: dict, value: str | None) -> dict | None:
        """The session a cookie carries, if it is good for this link now."""
        if not value or "." not in value or len(value) > 2000:
            return None
        body, sig = value.rsplit(".", 1)
        want = _b64(hmac.new(self._secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, want):
            return None
        try:
            s = json.loads(_unb64(body))
        except ValueError:
            return None
        if s.get("l") != link["id"] or s.get("g") != link["gen"] or s.get("e", 0) < self.clock():
            return None
        if not link["on"]:
            return None
        return s

    # -- people and agents ----------------------------------------------------------------

    def join(self, lid: str, name: str) -> None:
        def fn(d):
            link = d["links"][lid]
            if not any(p["name"] == name for p in link["people"]):
                link["people"].append({"name": name, "joined": now_iso()})
                link["people"] = link["people"][-200:]
        self._mutate(fn)

    def new_agent_key(self, lid: str, visitor: str) -> str:
        key = "lak_" + secrets.token_urlsafe(32)

        def fn(d):
            link = d["links"][lid]
            link["agents"] = [a for a in link["agents"] if a["visitor"] != visitor]
            link["agents"].append({"hash": _key_hash(key), "visitor": visitor, "created": now_iso()})
            link["agents"] = link["agents"][-200:]
        self._mutate(fn)
        return key

    def agent_for(self, link: dict, key: str | None) -> str | None:
        """The visitor whose agent holds this key, if it is good."""
        if not key or not key.startswith("lak_") or len(key) > 200 or not link["on"]:
            return None
        h = _key_hash(key)
        for a in link.get("agents", []):
            if hmac.compare_digest(a["hash"], h):
                return a["visitor"]
        return None

    # -- what one link may write ------------------------------------------------------------

    def count_write(self, lid: str) -> None:
        """Count one write by a link, refusing past the hourly or daily limit."""
        now = self.clock()

        def fn(d):
            ts = [t for t in d["writes"].get(lid, []) if now - t < 86400]
            if len(ts) >= WRITES_PER_DAY or sum(1 for t in ts if now - t < 3600) >= WRITES_PER_HOUR:
                raise NoteError(429, "This link has written a lot today; try again later.")
            ts.append(now)
            d["writes"][lid] = ts
        self._mutate(fn)

    # -- what the owner has seen --------------------------------------------------------------

    def seen(self, did: str) -> str | None:
        with self._lock:
            return self._read()["seen"].get(did)

    def mark_seen(self, did: str) -> None:
        self._mutate(lambda d: d["seen"].__setitem__(did, now_iso()))


def public(link: dict) -> dict:
    """What the sidecar and the owner's page are shown of a link (the page
    also gets the token, to copy the link)."""
    return {k: link.get(k) for k in ("id", "mode", "name", "days", "on", "created", "off_at")} | {
        "people": [p["name"] for p in link.get("people", [])],
        "agents": len(link.get("agents", [])),
        "locked": link.get("locked_until", 0) > time.time()}
