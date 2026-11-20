"""The supported local key source for the owner's Syncthing GUI."""

from __future__ import annotations

import http.client
import os
import re
import ssl
import stat
import xml.etree.ElementTree as ET
from pathlib import Path

PROVIDER = "syncthing"
KEY = "SYNCTHING_API_KEY"
GUI = "SYNCTHING_GUI_URL"
DEFAULT_GUI = "http://127.0.0.1:8384"
SOURCE = {
    "path": "~/.local/state/syncthing/config.xml",
    "fallback": "~/.config/syncthing/config.xml",
    "xml": "gui/apikey",
}
MAX_CONFIG = 2 * 1024 * 1024


class SourceError(ValueError):
    """A local configuration problem safe to show in Settings."""


def _read(path: Path) -> bytes:
    # This source is the daemon's own regular file in the owner's home.
    if path.resolve() != path:
        raise SourceError("Syncthing's settings must be a regular file at the declared path.")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > MAX_CONFIG:
            raise SourceError("Syncthing's settings must be a regular file owned by this user.")
        raw = file.read(MAX_CONFIG + 1)
    if len(raw) > MAX_CONFIG:
        raise SourceError("Syncthing's settings file is too large to import.")
    return raw


def read_source(source: dict) -> dict[str, str]:
    """Read the validated declaration and supply the API key and loopback URL."""
    for field in ("path", "fallback"):
        name = source.get(field)
        if not name:
            continue
        try:
            root = ET.fromstring(_read(Path.home() / name.removeprefix("~/")))
        except FileNotFoundError:
            continue
        except (OSError, ET.ParseError, RuntimeError):
            raise SourceError("Loam could not read Syncthing's settings on this server.") from None
        gui = root.find("gui")
        if gui is None or gui.get("enabled") == "false":
            raise SourceError("Enable Syncthing's GUI on 127.0.0.1 to connect it to Loam.")
        host, _, port = (gui.findtext("address") or "127.0.0.1:8384").strip().rpartition(":")
        if host.lower() not in ("127.0.0.1", "localhost") or not _port(port):
            raise SourceError("Syncthing's GUI address must use 127.0.0.1 or localhost and a valid port.")
        key = (root.findtext(source["xml"]) or "").strip()
        if not key or not key.isascii():
            raise SourceError("Syncthing's settings have no API key yet.")
        scheme = "https" if gui.get("tls") == "true" else "http"
        return {KEY: key, GUI: f"{scheme}://127.0.0.1:{int(port)}"}
    raise SourceError("Start Syncthing on this server so Loam can connect it.")


def _port(port: str) -> bool:
    return bool(re.fullmatch(r"[0-9]{1,5}", port)) and 1 <= int(port) <= 65535


def probe(values: dict[str, str]) -> tuple[bool, str, bool]:
    """Check the local GUI key. The last value distinguishes a rejected key."""
    target = re.fullmatch(r"(https?)://127\.0\.0\.1:([0-9]{1,5})", values.get(GUI) or DEFAULT_GUI)
    if not target or not _port(target[2]):
        return False, "Use a Syncthing GUI URL on 127.0.0.1 with a valid port.", False
    if not values[KEY].isascii():
        return False, "Use Syncthing's API key as shown in its GUI settings.", True
    if target[1] == "https":
        connection = http.client.HTTPSConnection(
            "127.0.0.1", int(target[2]), timeout=2, context=ssl._create_unverified_context())  # noqa: S323
    else:
        connection = http.client.HTTPConnection("127.0.0.1", int(target[2]), timeout=2)
    try:
        connection.request("GET", "/rest/system/status", headers={"X-API-Key": values[KEY]})
        with connection.getresponse() as response:
            if 200 <= response.status < 300:
                return True, "Syncthing accepted the key.", False
            if response.status in (401, 403):
                return False, "Syncthing rejected the API key.", True
            return False, f"Syncthing answered HTTP {response.status} when Loam checked the key.", False
    except (OSError, http.client.HTTPException):
        return False, "Loam could not reach Syncthing's GUI on 127.0.0.1.", False
    finally:
        connection.close()
