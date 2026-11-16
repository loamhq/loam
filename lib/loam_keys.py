"""The host's own checks of a key a person pastes (2026-11-14, first release
L9): `check: builtin:<name>` in a plugin's `settings.secret`
(lib/loam_contributions.py). For plugins with no code of their own on the
machine, read aloud and dictation, whose key is Google's Gemini API key.

A check answers ``(ok, sentence)``: the sentence is shown to the person as it
is, so it says what happened in plain words and never repeats the key. The
key goes in a header to the provider's own host and nowhere else.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


def _gemini_base() -> str:
    # The same knob loam-ui reads (GEMINI_API_URL), so a test points both at
    # one fake.
    return (os.environ.get("LOAM_GEMINI_URL") or os.environ.get("GEMINI_API_URL")
            or "https://generativelanguage.googleapis.com").rstrip("/")


def gemini(key: str) -> tuple[bool, str]:
    """Lists the models the key may use: the cheapest call that proves a key
    and costs nothing."""
    req = urllib.request.Request(f"{_gemini_base()}/v1beta/models?pageSize=1",
                                 headers={"x-goog-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()
            return True, "Google accepted the key."
    except urllib.error.HTTPError as exc:
        try:
            err = json.loads(exc.read()).get("error") or {}
        except (ValueError, AttributeError):
            err = {}
        reasons = {d.get("reason") for d in err.get("details") or [] if isinstance(d, dict)}
        if exc.code == 429:
            return True, ("Google accepted the key, and says its quota is used up "
                          "for now; it will work again when the quota resets.")
        if "API_KEY_INVALID" in reasons or exc.code == 400:
            return False, "Google says this is not a valid API key. Check that it was pasted whole."
        if exc.code == 403:
            said = (err.get("message") or "").split("\n")[0][:200]
            return False, ("Google refused this key" + (f": {said}" if said else "")
                           + ". It may need the Generative Language API turned on.")
        return False, f"Google answered {exc.code} when the key was checked; try again."
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return False, f"Could not reach Google to check the key ({reason}); try again."


CHECKS = {"gemini": gemini}


def check(name: str, value: str) -> tuple[bool, str]:
    fn = CHECKS.get(name)
    if fn is None:
        return False, f"the host has no check named {name!r}"
    return fn(value)
