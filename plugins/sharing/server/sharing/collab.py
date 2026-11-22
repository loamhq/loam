"""Client for the public ``share.collaboration`` v1 loopback plugin service.

Sharing discovers any enabled collaboration plugin (e.g. Comments & suggestions)
through the broker's ``GET /v1/plugin-services?interface=share.collaboration``.
When no collaboration plugin is enabled, Sharing stays cleanly view-only with
zero errors and zero hardcoded knowledge of any other plugin.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import urlencode

from .store import ShareError


class CollabClient:
    def __init__(self, store, timeout: float = 20):
        self.store = store
        self.timeout = timeout

    def service(self) -> dict | None:
        svcs = self.store.plugin_services("share.collaboration")
        for s in svcs:
            if s.get("version") == 1 and s.get("url"):
                return s
        return None

    def available(self) -> bool:
        return self.service() is not None

    def capabilities(self) -> dict | None:
        svc = self.service()
        if not svc:
            return None
        url = svc["url"].rstrip("/") + "/capabilities"
        req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                if res.status == 200:
                    data = json.loads(res.read() or b"{}")
                    if isinstance(data, dict):
                        data.setdefault("plugin", svc.get("plugin"))
                        return data
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            pass
        return None

    def allowed_modes(self, *, is_markdown: bool) -> list[str]:
        caps = self.capabilities()
        if not caps:
            return ["view"]
        modes = (caps.get("modes") or {}).get("markdown" if is_markdown else "other")
        if isinstance(modes, list) and modes:
            return [m for m in modes if m in ("view", "comment", "edit")] or ["view"]
        return ["view"]

    def effective_mode(self, link_mode: str, *, is_markdown: bool) -> str:
        """Clamp a link's stored mode to what is active right now (e.g. if
        Comments was turned off, a ``comment`` or ``edit`` link cleanly
        downgrades to ``view`` until Comments is turned back on)."""
        allowed = self.allowed_modes(is_markdown=is_markdown)
        if link_mode in allowed:
            return link_mode
        if "comment" in allowed and link_mode == "edit":
            return "comment"
        return "view"

    def summary(self, items: list[dict]) -> dict[str, dict]:
        svc = self.service()
        if not svc or not items:
            return {}
        url = svc["url"].rstrip("/") + "/summary"
        data = json.dumps({"items": items}).encode()
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as res:
                if res.status == 200:
                    return (json.loads(res.read() or b"{}").get("items")) or {}
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            pass
        return {}

    def appear(self, root: str, path: str) -> dict | None:
        svc = self.service()
        if not svc:
            return None
        url = svc["url"].rstrip("/") + "/appear?" + urlencode({"root": root, "path": path})
        req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                if res.status == 200:
                    return json.loads(res.read() or b"{}")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            pass
        return None

    def fetch_asset(self, plugin_id: str, rel: str) -> tuple[int, bytes, str]:
        svc = self.service()
        if not svc or svc.get("plugin") != plugin_id:
            return 404, b"not found", "text/plain; charset=utf-8"
        url = svc["url"].rstrip("/") + "/asset/" + rel.lstrip("/")
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                return res.status, res.read(), res.headers.get("Content-Type") or "application/octet-stream"
        except urllib.error.HTTPError as exc:
            return exc.code, b"not found", "text/plain; charset=utf-8"
        except (urllib.error.URLError, TimeoutError, OSError):
            return 502, b"service unavailable", "text/plain; charset=utf-8"

    def proxy_doc(
        self,
        method: str,
        subpath: str,
        query: str,
        body: bytes,
        actor: dict,
        file_desc: dict,
    ) -> tuple[int, bytes, str]:
        svc = self.service()
        if not svc:
            raise ShareError(404, "collaboration is not enabled on this Loam")
        tail = subpath.lstrip("/")
        url = svc["url"].rstrip("/") + "/doc" + (f"/{tail}" if tail else "")
        if query:
            url += "?" + query
        headers = {
            "Accept": "application/json",
            "X-Loam-Share-Actor": json.dumps(actor, ensure_ascii=True),
            "X-Loam-Share-File": json.dumps(file_desc, ensure_ascii=True),
        }
        data = None
        if method in ("POST", "PATCH", "PUT"):
            headers["Content-Type"] = "application/json"
            data = body or b"{}"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                return res.status, res.read(), res.headers.get("Content-Type") or "application/json; charset=utf-8"
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), exc.headers.get("Content-Type") or "application/json; charset=utf-8"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ShareError(502, f"collaboration service did not answer ({exc})") from None
