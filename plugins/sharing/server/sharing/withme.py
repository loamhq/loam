"""Shared with me: files others shared from their own Loams, opened here.

Works for any file type — Markdown, images, SVG, PDF, HTML mockups, audio,
video, code, archives — without copying them into this vault.

Safety:
- HTTPS-only ``https://<host>[:port]/s/<token>``; only the door's own routes.
- DNS resolution vetted against loopback, private, link-local, CGNAT,
  multicast, reserved and this box's own addresses; socket pinned to the
  checked IP; no redirects; strict timeouts and size limits.
- Every binary/HTML/SVG response proxied into this Loam's origin has its
  ``Content-Type``, magic bytes, and strict sandbox ``Content-Security-Policy``
  re-enforced locally by ``files.py``, so a malicious remote host can never
  execute script in this Loam's origin.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import os
import re
import secrets
import socket
import ssl
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from . import files as ftypes
from .store import ShareError, now_iso

CLIENT_PROTOCOL = 2
JSON_MAX = 8 * 1024 * 1024
BINARY_MAX = 64 * 1024 * 1024
TIMEOUT = 10
DEADLINE = 30
LIST_TIMEOUT = 6
UA = "Loam-sharing/2"
LINK_RE = re.compile(r"https://[^\s<>\"'“”‘’]+?/s/[A-Za-z0-9_-]{32,64}/?(?=[\s<>\"'“”‘’)\].,;:!?]|$)")
TOKEN_RE = re.compile(r"^/s/([A-Za-z0-9_-]{32,64})/?$")
ID_RE = re.compile(r"^w-[a-z0-9]{10}$")
DOOR_PATHS = re.compile(
    r"^/(loam\.json|open|raw|download|frame|api/(name|agent-key|status|me|file|collab/doc(/[A-Za-z0-9_./-]*)?))$"
)


class Refused(ShareError):
    def __init__(self, status: int, message: str, code: str):
        super().__init__(status, message, code=code)


def parse_invite(text: str) -> dict:
    text = text if isinstance(text, str) else ""
    found = LINK_RE.search(text)
    pw = re.search(r"(?im)^\s*password\s*[:：]\s*(\S+)\s*$", text)
    return {"link": found.group(0) if found else None, "password": pw.group(1) if pw else None}


def parse_link(raw) -> dict:
    if not isinstance(raw, str) or not raw.strip():
        raise Refused(400, "Paste the link you were sent.", "not-link")
    raw = raw.strip()
    found = parse_invite(raw)["link"] if not raw.startswith("https://") or " " in raw or "\n" in raw else raw
    raw = found or raw
    try:
        u = urlsplit(raw)
        port = u.port
    except ValueError:
        raise Refused(400, "That isn’t a Loam share link. It looks like https://…/s/…", "not-link") from None
    if u.scheme == "http":
        raise Refused(400, "That link isn’t https. Loam only opens share links over https.", "not-link")
    m = TOKEN_RE.match(u.path or "")
    if u.scheme != "https" or not u.hostname or u.username or u.password or not m:
        raise Refused(400, "That isn’t a Loam share link. It looks like https://…/s/…", "not-link")
    host = u.hostname.rstrip(".").lower()
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise Refused(400, "That link’s address can’t be read.", "not-link") from None
    if not re.fullmatch(r"[a-z0-9.-]+|\[[0-9a-f:.]+\]", ascii_host) and not _is_ip(ascii_host):
        raise Refused(400, "That link’s address can’t be read.", "not-link")
    port = port or 443
    origin = f"https://{ascii_host}" + ("" if port == 443 else f":{port}")
    return {
        "origin": origin, "host": ascii_host, "port": port,
        "token": m.group(1), "url": f"{origin}/s/{m.group(1)}",
    }


def _is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text.strip("[]"))
        return True
    except ValueError:
        return False


_BOX: dict = {"at": 0.0, "addrs": frozenset()}


def box_addresses() -> frozenset:
    if time.monotonic() - _BOX["at"] < 300 and _BOX["addrs"]:
        return _BOX["addrs"]
    found = set()
    try:
        out = subprocess.run(["ip", "-j", "addr"], capture_output=True, text=True, timeout=5).stdout
        for iface in json.loads(out or "[]"):
            for a in iface.get("addr_info", []):
                if a.get("local"):
                    found.add(ipaddress.ip_address(a["local"]))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    try:
        for a in socket.getaddrinfo(socket.gethostname(), None):
            found.add(ipaddress.ip_address(a[4][0].split("%")[0]))
    except (OSError, ValueError):
        pass
    _BOX.update(at=time.monotonic(), addrs=frozenset(found))
    return _BOX["addrs"]


def vet_addresses(addrs, box=frozenset()) -> None:
    if not addrs:
        raise Refused(502, "That address doesn’t lead anywhere.", "unreachable")
    for a in addrs:
        ip = ipaddress.ip_address(a)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if ip in box or ip.is_loopback:
            raise Refused(400, "That address is this server. Loam opens share links from other servers only.", "private")
        if (
            not ip.is_global or ip.is_private or ip.is_link_local or ip.is_multicast
            or ip.is_reserved or ip.is_unspecified or ip in ipaddress.ip_network("100.64.0.0/10")
        ):
            raise Refused(400, "That address is on a private network. Loam opens share links on the open internet only.", "private")


class Answer:
    def __init__(self, status: int, body: bytes, ctype: str = "", cookie: str | None = None, headers: dict | None = None):
        self.status = status
        self.body = body
        self.ctype = ctype
        self.cookie = cookie
        self.headers = headers or {}

    def json(self):
        try:
            data = json.loads(self.body)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def loam_page(self) -> bool:
        return b"Made with Loam" in self.body[:20000] or b"sh-gate" in self.body[:20000]


def _cookie_of(headers) -> str | None:
    for k, v in headers:
        if k.lower() == "set-cookie":
            m = re.match(r"\s*loam_share=([^;]*)", v)
            if m:
                return m.group(1)
    return None


class Remote:
    def __init__(self, resolve=None, context: ssl.SSLContext | None = None, box=box_addresses, allow_private: bool = False):
        self.resolve = resolve or (lambda host, port: [a[4][0] for a in socket.getaddrinfo(
            host, port, type=socket.SOCK_STREAM)])
        self.context = context or ssl.create_default_context()
        self.box = box
        self.allow_private = allow_private

    def call(self, link: dict, method: str, rest: str, headers: dict, body: bytes | None, *, max_bytes: int = JSON_MAX) -> Answer:
        if not DOOR_PATHS.match(rest.split("?", 1)[0]):
            raise Refused(400, "that is not one of a share door's own addresses", "not-link")
        host, port = link["host"], link["port"]
        try:
            addrs = sorted({a.split("%")[0] for a in self.resolve(host.strip("[]"), port)})
        except (OSError, UnicodeError):
            raise Refused(502, f"Couldn’t find {host}. Check the link, or try again later.", "unreachable") from None
        if not self.allow_private:
            vet_addresses(addrs, self.box())
        elif not addrs:
            raise Refused(502, "That address doesn’t lead anywhere.", "unreachable")
        conn = _Pinned(host, port, addrs[0], self.context, TIMEOUT)
        started = time.monotonic()
        try:
            conn.request(
                method, f"/s/{link['token']}{rest}", body=body,
                headers={"Host": host if port == 443 else f"{host}:{port}", "User-Agent": UA, **headers},
            )
            res = conn.getresponse()
            if 300 <= res.status < 400:
                raise Refused(502, f"{host} sent this link somewhere else. Loam doesn’t follow that.", "redirect")
            chunks, size = [], 0
            while True:
                if time.monotonic() - started > DEADLINE:
                    raise Refused(504, f"{host} took too long to answer.", "unreachable")
                chunk = res.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise Refused(502, f"{host} answered with a file larger than Loam accepts here.", "too-big")
                chunks.append(chunk)
            hdr_list = res.getheaders()
            return Answer(
                res.status, b"".join(chunks), res.getheader("Content-Type") or "",
                _cookie_of(hdr_list), {k.lower(): v for k, v in hdr_list},
            )
        except Refused:
            raise
        except ssl.SSLError:
            raise Refused(502, f"{host}’s certificate isn’t one Loam can trust.", "unreachable") from None
        except (OSError, http.client.HTTPException):
            raise Refused(502, f"Couldn’t reach {host} just now.", "unreachable") from None
        finally:
            conn.close()


class _Pinned(http.client.HTTPSConnection):
    def __init__(self, host, port, ip, context, timeout):
        super().__init__(host, port, timeout=timeout, context=context)
        self._ip = ip
        self._context = context

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class Own:
    """In-process transport for links belonging to this Loam itself."""

    def __init__(self, door):
        self.door = door

    def call(self, link: dict, method: str, rest: str, headers: dict, body: bytes | None, *, max_bytes: int = JSON_MAX) -> Answer:
        hdrs = {k.lower(): v for k, v in headers.items()}
        hdrs["host"] = link["host"]
        ans = self.door.handle(method, f"/door/{link['token']}{rest}", hdrs, body or b"", "loam-self")
        return Answer(ans.status, ans.body, ans.ctype, _cookie_of(ans.headers), {k.lower(): v for k, v in ans.headers})


def public(entry: dict) -> dict:
    latest, seen = entry.get("latest") or {}, entry.get("seen") or {}
    new = bool(latest) and bool(seen) and latest != seen
    filename = entry.get("filename") or entry.get("title") or "file"
    ext = ftypes.ext_of(filename)
    if ext and not re.fullmatch(r"\.[a-z0-9_-]{1,16}", ext):
        ext = ""
    return {
        "id": entry["id"],
        "address": f"shared:{entry['id']}",
        "plugin_path": f"with-me/{entry['id']}{ext}",
        "url": entry["url"],
        "host": entry["host"],
        "title": entry.get("title") or filename,
        "filename": filename,
        "kind": entry.get("kind") or ftypes.classify(filename),
        "label": entry.get("label") or ftypes.label_of(filename),
        "size": entry.get("size"),
        "owner": entry.get("owner") or "",
        "mode": entry.get("mode") or "view",
        "collab": bool(entry.get("collab", False)),
        "name": entry.get("name") or "",
        "state": entry.get("state") or "ok",
        "problem": entry.get("problem") or "",
        "new": new,
        "added": entry.get("added"),
        "seen_at": entry.get("seen_at"),
        "checked": entry.get("checked"),
    }


class WithMe:
    def __init__(
        self,
        folder,
        *,
        own_tokens=lambda: set(),
        door=None,
        remote: Remote | None = None,
        owner=lambda: {"id": "owner", "name": "Owner"},
        agent=lambda aid: None,
    ):
        self.folder = Path(folder)
        self.path = self.folder / "with-me.json"
        self.own_tokens = own_tokens
        self.local = Own(door) if door is not None else None
        self.remote = remote or Remote()
        self.owner = owner
        self.agent = agent
        self._lock = threading.RLock()

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {}
        data.setdefault("items", {})
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

    def _get(self, wid: str) -> dict:
        if not isinstance(wid, str) or not ID_RE.match(wid):
            raise ShareError(404, "no such file in Shared with me")
        with self._lock:
            e = self._read()["items"].get(wid)
        if not e:
            raise ShareError(404, "no such file in Shared with me")
        return e

    def _update(self, wid: str, **changes) -> dict:
        with self._lock:
            data = self._read()
            e = data["items"].get(wid)
            if not e:
                return {}
            e.update(changes)
            self._write(data)
            return dict(e)

    def _transport(self, link: dict):
        if self.local is not None and link["token"] in self.own_tokens():
            return self.local
        return self.remote

    def _call(self, link, method, rest, headers=None, body=None, *, max_bytes: int = JSON_MAX) -> Answer:
        data = json.dumps(body).encode() if isinstance(body, dict) else body
        hdrs = {"Accept": "application/json", **(headers or {})}
        if isinstance(body, dict):
            hdrs.setdefault("Content-Type", "application/json")
        return self._transport(link).call(link, method, rest, hdrs, data, max_bytes=max_bytes)

    def _probe(self, link: dict) -> None:
        ans = self._call(link, "GET", "/loam.json")
        got = ans.json() if ans.status == 200 else None
        if got is None or not isinstance(got.get("loam_share"), int):
            if ans.loam_page():
                raise Refused(426, f"The Loam at {link['host']} is too old for this. Until it updates, open the link in a browser.", "too-old")
            raise Refused(400, f"That isn’t a Loam share link: {link['host']} doesn’t answer as a Loam.", "not-link")
        if got.get("min", 1) > CLIENT_PROTOCOL:
            raise Refused(426, f"The Loam at {link['host']} is newer than this one. Update Loam to open it here.", "too-new")
        if got["loam_share"] < CLIENT_PROTOCOL:
            raise Refused(426, f"The Loam at {link['host']} is too old for this. Until it updates, open the link in a browser.", "too-old")

    def _open(self, link: dict, password: str) -> tuple[dict, str]:
        ans = self._call(link, "POST", "/open", body={"password": password})
        got = ans.json() or {}
        if ans.status == 200 and ans.cookie:
            return got, ans.cookie
        if ans.status == 403:
            raise Refused(403, "That password doesn’t open this link.", "password")
        if ans.status == 429:
            raise Refused(429, got.get("error") or "Too many wrong passwords. Try again in a few minutes.", "throttled")
        if ans.status == 404:
            raise Refused(410, "This link is turned off or has expired. Ask the person who sent it for a new one.", "gone")
        raise Refused(502, f"{link['host']} answered in a way Loam doesn’t understand ({ans.status}).", "unreachable")

    def _name(self, link, cookie, name) -> str | None:
        ans = self._call(link, "POST", "/api/name", {"Cookie": f"loam_share={cookie}", "X-Loam-Share": "1"}, {"name": name})
        if ans.status == 200 and ans.cookie:
            return ans.cookie
        got = ans.json() or {}
        if ans.status == 409:
            raise Refused(409, got.get("error") or "That name is taken there. Try another.", "name-taken")
        raise Refused(ans.status if ans.status >= 400 else 502, got.get("error") or "That name wasn’t taken.", "name")

    def add(self, link_text, password, name=None, by: dict | None = None) -> dict:
        pasted = parse_invite(link_text) if isinstance(link_text, str) else {}
        link = parse_link(link_text)
        password = password if isinstance(password, str) and password.strip() else pasted.get("password")
        if not isinstance(password, str) or not password.strip():
            raise Refused(400, "Type the password that came with the link.", "password")
        password = password.strip()
        if len(password) > 200:
            raise Refused(400, "That password is longer than any Loam makes.", "password")
        with self._lock:
            for e in self._read()["items"].values():
                if e["token"] == link["token"] and e["host"] == link["host"]:
                    if e.get("state") == "ok" and e.get("password") == password:
                        raise Refused(409, "That file is already in Shared with me.", "already")
                    break
        self._probe(link)
        got, cookie = self._open(link, password)
        if got.get("need") == "name":
            clean = " ".join(str(name or "").split())[:60]
            if not clean:
                raise Refused(409, "This link isn’t for one person. What name do they know you by?", "need-name")
            cookie = self._name(link, cookie, clean) or cookie
            known = clean
        else:
            known = got.get("name") or ""
        filename = str(got.get("filename") or got.get("title") or "file")[:200]
        entry = {
            "url": link["url"], "origin": link["origin"], "host": link["host"], "port": link["port"],
            "token": link["token"], "password": password, "name": known, "named": bool(got.get("name")),
            "session": cookie, "agent_key": None,
            "title": str(got.get("title") or filename)[:200],
            "filename": filename,
            "kind": str(got.get("kind") or ftypes.classify(filename)),
            "label": str(got.get("label") or ftypes.label_of(filename)),
            "size": got.get("size"),
            "owner": str(got.get("owner") or "")[:80],
            "mode": got.get("mode") if got.get("mode") in ("view", "comment", "edit") else "view",
            "collab": bool(got.get("collab", False)),
            "state": "ok", "problem": "", "added": now_iso(), "added_by": (by or {}).get("id"),
            "seen": {}, "latest": {}, "checked": now_iso(), "seen_at": None,
        }
        with self._lock:
            data = self._read()
            old = next((k for k, e in data["items"].items() if e["token"] == link["token"] and e["host"] == link["host"]), None)
            wid = old or ("w-" + "".join(secrets.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(10)))
            if old:
                entry["added"] = data["items"][old].get("added") or entry["added"]
                entry["seen"] = data["items"][old].get("seen") or {}
            entry["id"] = wid
            data["items"][wid] = entry
            self._write(data)
        try:
            self.status(wid)
        except ShareError:
            pass
        return public(self._get(wid))

    def remove(self, wid: str) -> dict:
        self._get(wid)
        with self._lock:
            data = self._read()
            data["items"].pop(wid, None)
            self._write(data)
        return {"ok": True, "removed": wid}

    def _auth(self, e: dict, who: dict, *, fresh: bool = False, appear: str = "names") -> dict:
        if who.get("kind") == "agent":
            key = e.get("agent_key")
            if not key or fresh:
                key = self._new_key(e)
            if appear == "one":
                return {"Authorization": f"Bearer {key}"}
            label = who.get("name") or who.get("id") or ""
            try:
                label.encode("latin-1")
            except UnicodeEncodeError:
                label = who.get("id") or ""
            return {"Authorization": f"Bearer {key}", "X-Loam-Agent-Name": label}
        cookie = e.get("session")
        if not cookie or fresh:
            cookie = self._reopen(e)
        return {"Cookie": f"loam_share={cookie}", "X-Loam-Share": "1"}

    def _link(self, e: dict) -> dict:
        return {k: e[k] for k in ("origin", "host", "port", "token", "url")}

    def _reopen(self, e: dict) -> str:
        try:
            got, cookie = self._open(self._link(e), e["password"])
        except Refused as exc:
            self._mark(e, exc)
            raise
        if got.get("need") == "name" and e.get("name"):
            cookie = self._name(self._link(e), cookie, e["name"]) or cookie
        self._update(e["id"], session=cookie)
        e["session"] = cookie
        return cookie

    def _new_key(self, e: dict) -> str:
        for attempt in (0, 1):
            ans = self._call(
                self._link(e), "POST", "/api/agent-key",
                {"Cookie": f"loam_share={e.get('session') or self._reopen(e)}", "X-Loam-Share": "1"}, {},
            )
            if ans.status == 401 and attempt == 0:
                self._reopen(e)
                continue
            got = ans.json() or {}
            if ans.status == 201 and isinstance(got.get("key"), str):
                self._update(e["id"], agent_key=got["key"])
                e["agent_key"] = got["key"]
                return got["key"]
            self._fail(e, ans)
        raise Refused(502, "The other Loam didn’t give a key for the owner’s agents.", "unreachable")

    def _mark(self, e: dict, exc: Refused) -> None:
        state = {
            "gone": "gone", "unreachable": "unreachable", "password": "password",
            "too-old": "version", "too-new": "version", "redirect": "unreachable",
            "too-big": "unreachable", "throttled": "unreachable",
        }.get(exc.code)
        if state:
            self._update(e["id"], state=state, problem=exc.message, checked=now_iso())

    def _fail(self, e: dict, ans: Answer):
        if ans.status == 404 and not ans.json():
            raise Refused(410, "This file is no longer shared with you.", "gone")
        got = ans.json() or {}
        raise ShareError(
            ans.status if ans.status >= 400 else 502,
            got.get("error") or f"{e['host']} answered {ans.status}.",
        )

    def read_file(self, wid: str, who: dict, *, appear: str = "names") -> dict:
        e = self._get(wid)
        try:
            for attempt in (0, 1):
                ans = self._call(self._link(e), "GET", "/api/file", self._auth(e, who, fresh=attempt == 1, appear=appear))
                if ans.status == 401 and attempt == 0:
                    continue
                if ans.status == 404 and not ans.json():
                    raise Refused(410, "This file is no longer shared with you.", "gone")
                break
        except Refused as exc:
            self._mark(e, exc)
            raise
        got = ans.json()
        if got is None or ans.status >= 400:
            raise ShareError(ans.status if ans.status >= 400 else 502, (got or {}).get("error") or f"{e['host']} answered {ans.status}.")
        changes = {
            "state": "ok", "problem": "", "checked": now_iso(),
            "title": str(got.get("title") or e.get("title") or "")[:200],
            "filename": str(got.get("filename") or e.get("filename") or "file")[:200],
            "kind": str(got.get("kind") or e.get("kind") or "other"),
            "label": str(got.get("label") or e.get("label") or "File"),
            "size": got.get("size"),
            "collab": bool(got.get("collab", False)),
        }
        if got.get("mode") in ("view", "comment", "edit"):
            changes["mode"] = got["mode"]
        if who.get("kind") != "agent" and got.get("revision"):
            rev = {"revision": got["revision"], "notes": (e.get("latest") or {}).get("notes")}
            changes.update(seen=rev, latest=rev, seen_at=now_iso())
        self._update(wid, **changes)
        return got

    def fetch_binary(self, wid: str, who: dict, endpoint: str, *, range_header: str | None = None) -> tuple[int, bytes, str, list[tuple[str, str]]]:
        """Proxy ``/raw``, ``/download``, or ``/frame`` with local CSP/magic re-enforcement."""
        if endpoint not in ("raw", "download", "frame"):
            raise ShareError(404, "no such route")
        e = self._get(wid)
        filename = e.get("filename") or "file"
        kind = ftypes.classify(filename)
        if endpoint == "frame" and kind != "html":
            raise ShareError(404, "not an HTML file")
        try:
            for attempt in (0, 1):
                hdrs = self._auth(e, who, fresh=attempt == 1)
                hdrs["Accept"] = "*/*"
                if range_header and endpoint == "raw" and kind in ("audio", "video"):
                    hdrs["Range"] = range_header
                ans = self._call(self._link(e), "GET", f"/{endpoint}", hdrs, max_bytes=BINARY_MAX)
                if ans.status == 401 and attempt == 0:
                    continue
                if ans.status == 404:
                    raise Refused(410, "This file is no longer shared with you.", "gone")
                break
        except Refused as exc:
            self._mark(e, exc)
            raise
        if ans.status == 416:
            rh = []
            if ans.headers.get("content-range"):
                rh.append(("Content-Range", ans.headers["content-range"]))
            rh.append(("Accept-Ranges", "bytes"))
            return 416, b"", "text/plain; charset=utf-8", rh
        if ans.status not in (200, 206):
            raise ShareError(ans.status if ans.status >= 400 else 502, f"{e['host']} answered {ans.status}.")
        if endpoint == "download":
            ctype, out_hdrs = ftypes.download_response_meta(filename)
            return 200, ans.body, ctype, out_hdrs
        if endpoint == "frame":
            ctype, out_hdrs = ftypes.frame_response_meta()
            return 200, ans.body, ctype, out_hdrs
        ctype, out_hdrs = ftypes.raw_response_meta(filename, ans.body, is_range=(ans.status == 206))
        if kind in ("audio", "video"):
            out_hdrs.append(("Accept-Ranges", "bytes"))
            if ans.status == 206 and ans.headers.get("content-range"):
                out_hdrs.append(("Content-Range", ans.headers["content-range"]))
        return ans.status, ans.body, ctype, out_hdrs

    def collab_request(self, wid: str, who: dict, method: str, route: str, args: dict | None = None, *, appear: str = "names"):
        e = self._get(wid)
        rest = "/api/collab/" + route.strip("/")
        if not DOOR_PATHS.match(rest):
            raise ShareError(404, "no such route")
        args = {k: v for k, v in (args or {}).items() if k not in ("root", "path")}
        query = ""
        body = None
        if method in ("GET", "DELETE"):
            query = ("?" + urlencode({k: v for k, v in args.items() if isinstance(v, (str, int))})) if args else ""
        else:
            body = args
        try:
            for attempt in (0, 1):
                ans = self._call(self._link(e), method, rest + query, self._auth(e, who, fresh=attempt == 1, appear=appear), body)
                if ans.status == 401 and attempt == 0:
                    continue
                if ans.status == 404 and not ans.json():
                    raise Refused(410, "This file is no longer shared with you.", "gone")
                break
        except Refused as exc:
            self._mark(e, exc)
            raise
        got = ans.json()
        if got is None:
            raise Refused(502, f"{e['host']} answered in a way Loam doesn’t understand.", "unreachable")
        if ans.status >= 400:
            raise ShareError(ans.status, got.get("error") or f"{e['host']} answered {ans.status}.")
        changes = {"state": "ok", "problem": "", "checked": now_iso(), "collab": True}
        me = got.get("me") if isinstance(got.get("me"), dict) else {}
        if me.get("mode") in ("view", "comment", "edit"):
            changes["mode"] = me["mode"]
        if who.get("kind") != "agent" and method == "GET" and route.strip("/") in ("doc", "doc/revision"):
            rev = {
                "revision": got.get("revision"),
                "notes": got.get("notes") if route.strip("/") == "doc/revision" else got.get("notes_revision"),
            }
            if rev["revision"]:
                changes.update(seen=rev, latest=rev, seen_at=now_iso())
        if who.get("kind") != "agent" and method == "GET" and route.strip("/") == "doc":
            got["seen"] = e.get("seen_at")
        if route.strip("/") == "doc" and method == "GET" and isinstance(got.get("text"), str):
            m = re.search(r"(?m)^#\s+(.+?)\s*#*\s*$", got["text"])
            if m:
                changes["title"] = m.group(1)[:200]
        self._update(wid, **changes)
        return ans.status, got

    def status(self, wid: str) -> dict:
        e = self._get(wid)
        who = {"kind": "owner"}
        try:
            for attempt in (0, 1):
                ans = self._call(self._link(e), "GET", "/api/status", self._auth(e, who, fresh=attempt == 1))
                if ans.status == 401 and attempt == 0:
                    continue
                break
            if ans.status == 404 and not ans.json():
                raise Refused(410, "This file is no longer shared with you.", "gone")
            got = ans.json()
            if ans.status != 200 or not got:
                raise Refused(502, f"{e['host']} answered in a way Loam doesn’t understand.", "unreachable")
        except Refused as exc:
            self._mark(e, exc)
            return public(self._get(wid))
        latest = {"revision": got.get("revision"), "notes": got.get("notes")}
        changes = {
            "state": "ok", "problem": "", "checked": now_iso(), "latest": latest,
            "title": str(got.get("title") or e.get("title") or "")[:200],
            "filename": str(got.get("filename") or e.get("filename") or "file")[:200],
            "kind": str(got.get("kind") or e.get("kind") or "other"),
            "label": str(got.get("label") or e.get("label") or "File"),
            "size": got.get("size"),
            "owner": str(got.get("owner") or e.get("owner") or "")[:80],
            "collab": bool(got.get("collab", False)),
        }
        if got.get("mode") in ("view", "comment", "edit"):
            changes["mode"] = got["mode"]
        if not e.get("seen"):
            changes["seen"] = latest
        self._update(wid, **changes)
        return public(self._get(wid))

    def list(self, refresh: bool = True) -> list[dict]:
        with self._lock:
            items = list(self._read()["items"].values())
        if refresh and items:
            pool = ThreadPoolExecutor(max_workers=min(8, len(items)))
            futs = [pool.submit(self.status, e["id"]) for e in items]
            deadline = time.monotonic() + LIST_TIMEOUT
            for f in futs:
                try:
                    f.result(timeout=max(0.1, deadline - time.monotonic()))
                except Exception:  # noqa: BLE001
                    pass
            pool.shutdown(wait=False)
        with self._lock:
            items = list(self._read()["items"].values())
        items.sort(key=lambda e: e.get("added") or "", reverse=True)
        return [public(e) for e in items]

    def get(self, wid: str) -> dict:
        return public(self._get(wid))
