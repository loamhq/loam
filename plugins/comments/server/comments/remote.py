"""Client for the public ``share.remote`` plugin service interface.

When a sharing plugin is enabled, it provides ``share.remote`` so Comments can
proxy operations on files shared from other Loams (``shared:w-…``) without
importing or hardcoding anything about the sharing plugin.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import urlencode

from .sidecar import NoteError


class RemoteClient:
    def __init__(self, store, timeout: float = 30):
        self.store = store
        self.timeout = timeout

    def _service_url(self) -> str | None:
        svcs = self.store.plugin_services("share.remote")
        for s in svcs:
            if s.get("version") == 1 and s.get("url"):
                return s["url"].rstrip("/")
        return None

    def available(self) -> bool:
        return self._service_url() is not None

    def call(self, wid: str, who: dict, method: str, route: str,
             args: dict | None = None, *, appear: str = "names") -> tuple[int, dict]:
        base = self._service_url()
        if not base:
            raise NoteError(503, "Turn on the Sharing plugin to work on files shared from another Loam.")
        route = route.lstrip("/")
        url = f"{base}/{wid}/{route}" if route else f"{base}/{wid}"
        data = None
        if method in ("GET", "DELETE"):
            if args:
                q = {k: v for k, v in args.items() if isinstance(v, (str, int))}
                if q:
                    url += "?" + urlencode(q)
        else:
            data = json.dumps(args or {}).encode()
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "X-Loam-Appear": appear}
        if who.get("kind") == "agent" and who.get("id"):
            headers["X-Loam-Agent"] = who["id"]
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                return res.status, json.loads(res.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read() or b"{}")
            except ValueError:
                body = {}
            raise NoteError(exc.code, body.get("error") or f"remote share answered {exc.code}",
                            code=body.get("code")) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise NoteError(502, f"the sharing service did not answer ({exc})") from None
