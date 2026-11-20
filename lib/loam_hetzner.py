"""Hetzner Cloud transport and the Loam host's server protection."""

from __future__ import annotations

import http.client
import json
import re
import urllib.error
import urllib.request
from urllib.parse import quote, unquote, urlsplit

import loam_offline

API = "https://api.hetzner.cloud/v1"
METADATA = "http://169.254.169.254/hetzner/v1/metadata/instance-id"
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
HOST_ACTIONS = {"poweroff", "shutdown", "rebuild", "change_type"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def opener():
    # Keep the Authorization header at the configured API origin.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def _redact(value, token: str):
    if isinstance(value, str):
        return value.replace(token, "[redacted]")
    if isinstance(value, list):
        return [_redact(item, token) for item in value]
    if isinstance(value, dict):
        return {_redact(k, token): _redact(v, token) for k, v in value.items()}
    return value


def request(token: str, method: str, target: str, body: dict | None = None,
            *, base: str | None = None) -> dict:
    """Send one request and return JSON with no headers or unfiltered errors."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json",
               "User-Agent": "Loam"}
    try:
        url = (base or API) + target
        loam_offline.guard_url(url, "Hetzner Cloud")
        data = None
        if body is not None:
            data = json.dumps(body, allow_nan=False).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data, headers, method=method)
        try:
            response = opener().open(req, timeout=20)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            code = response.code
            raw = response.read()
    except (OSError, ValueError, http.client.HTTPException):
        return {"ok": False, "error": "Could not complete the request to Hetzner Cloud."}
    try:
        result = _redact(json.loads(raw), token) if raw else None
    except (ValueError, RecursionError):
        return {"ok": False, "status": code, "error": "Hetzner Cloud returned a non-JSON response."}
    return {"ok": 200 <= code < 300, "status": code, "body": result}


def api_target(path: str) -> tuple[str, list[str]]:
    """Use the same decoded segments for the guard and the outbound path."""
    if (not path.startswith("/") or path.startswith("//") or "#" in path
            or any(ord(c) < 33 or ord(c) >= 127 for c in path)):
        raise ValueError("Use a Hetzner API path such as /servers or /pricing.")
    parsed = urlsplit(path)
    segments = unquote(parsed.path).strip("/").split("/")
    if segments[0] == "v1":
        segments = segments[1:]
    if not segments or any(not part or part in {".", ".."} or "%" in part or "\\" in part
                           or any(ord(c) < 33 or ord(c) == 127 for c in part) for part in segments):
        raise ValueError("Use a Hetzner API path such as /servers or /pricing.")
    target = "/" + "/".join(quote(part, safe="@*:+") for part in segments)
    return target + ("?" + parsed.query if parsed.query else ""), segments


def server_id() -> int | None:
    try:
        loam_offline.guard_url(METADATA, "Hetzner instance metadata")
        with opener().open(METADATA, timeout=3) as resp:
            raw = resp.read(64).strip()
        if re.fullmatch(rb"[1-9][0-9]*", raw):
            return int(raw)
    except (OSError, http.client.HTTPException):
        pass
    return None


def host_refusal(method: str, segments: list[str]) -> str | None:
    if len(segments) < 2 or segments[0] != "servers":
        return None
    action = segments[2:]
    if method == "DELETE" and not action:
        verb = "delete"
    elif method == "POST" and len(action) == 2 and action[0] == "actions" and action[1] in HOST_ACTIONS:
        verb = {"change_type": "resize", "poweroff": "power off"}.get(action[1], action[1])
    else:
        return None
    own_id = server_id()
    if own_id is None:
        return "I could not identify the server running Loam, so I did not send this server action."
    if segments[1].removeprefix("+").lstrip("0") == str(own_id):
        return ("Use headroom to resize the server running Loam." if verb == "resize"
                else f"This tool cannot {verb} the server running Loam.")
    return None


def call(project: str, method: str, path: str, body: dict | None = None) -> dict:
    from loam_keys import KeyError_, hetzner_project_token

    try:
        if not isinstance(method, str) or method.upper() not in METHODS:
            raise ValueError("Use GET, POST, PUT, PATCH or DELETE.")
        if not isinstance(path, str):
            raise ValueError("Give a Hetzner API path as text.")
        if body is not None and not isinstance(body, dict):
            raise ValueError("Give the request body as a JSON object.")
        if not isinstance(project, str):
            raise ValueError("Give the saved Hetzner project name.")
        method = method.upper()
        target, segments = api_target(path)
        if refusal := host_refusal(method, segments):
            return {"ok": False, "error": refusal}
        token = hetzner_project_token(project)
        if not token:
            raise ValueError("There is no token for that project. Save it in Settings › "
                             "Connections › API keys › Hetzner Cloud.")
    except (KeyError_, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    return request(token, method, target, body)
