"""Public, attributable work. No token, file names or chat content leave this host."""
import json
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import build_opener, HTTPRedirectHandler, Request

import loam_config as config
import loam_feedback as feedback
import loam_git_identity as identity
import loam_marketplace as market


class NoRedirect(HTTPRedirectHandler):
    # A redirect may point outside the hosts named in the declaration.
    def redirect_request(self, *args):
        return None


def get(url):
    request = Request(url, headers={"User-Agent": "Loam-activity-facts"})
    with build_opener(NoRedirect).open(request, timeout=5) as answer:
        data = answer.read(2_000_001)
    if len(data) > 2_000_000:
        raise ValueError("Public response too large")
    return json.loads(data)


def read():
    account = identity.stored() or {}
    login = account.get("login") if account.get("source") == "github" else None
    repo = feedback.repo()
    result = dict(publications=None, reports=None, contributions=None)
    if not login or not re.fullmatch(r"[A-Za-z0-9-]+", login) or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        return result
    # Only the standard public Market is in this group's network declaration.
    url = market.list_url()
    key = [login, repo, url]
    root = config.STATE / "activity-facts"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = root / "public-work.json"
    try:
        cache = json.loads(path.read_text())
        if cache["key"] == key and time.time() < cache["until"]:
            return cache["value"]
    except (OSError, KeyError, ValueError):
        pass
    if url and url.startswith("https://raw.githubusercontent.com/"):
        try:
            rows, _ = market.parse(json.dumps(get(url)))
            result["publications"] = [dict(id=p["id"], name=p["name"], url=p["link"])
                                      for p in rows if p["repo"].split("/")[0].lower() == login.lower()]
        except (OSError, ValueError, KeyError):
            pass
    for kind, query in (("reports", "is:issue"), ("contributions", "is:pr is:merged")):
        try:
            items = []
            for page in range(1, 11):
                params = urlencode(dict(q=f"repo:{repo} author:{login} {query}", per_page=100, page=page))
                body = get("https://api.github.com/search/issues?" + params)
                if body.get("incomplete_results") or body["total_count"] > 1000:
                    break
                items.extend(dict(id=str(i["number"]), name=i["title"], url=i["html_url"],
                                  shipped=any(label["name"].lower() == "shipped" for label in i["labels"]))
                             for i in body["items"])
                if len(items) >= body["total_count"]:
                    result[kind] = items
                    break
        except (OSError, ValueError, KeyError):
            pass
    with tempfile.NamedTemporaryFile("w", dir=root, delete=False) as fh:
        json.dump(dict(key=key, value=result, until=time.time()+3600), fh)
    os.replace(fh.name, path)
    return result
