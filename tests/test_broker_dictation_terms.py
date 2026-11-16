#!/usr/bin/env python3
"""`GET /v1/dictation-terms`: the one vault note of dictation terms, read-only.

Added 2026-09-30. The app's container cannot see the vault, so the note
reaches the page through this route and nothing else. Three things proved:

  1. the route answers the note's text, its vault-relative path, an Obsidian
     link and its mtime, with `Cache-Control: no-store`;
  2. a missing note is `404` in one sentence, still `no-store`;
  3. nothing a client sends can name a file: a path-like query is ignored and
     a longer path is not this route. These go through the real request
     parser (`Server._handle`) with the real bearer check, not just `route`.

The note is a temp file in a temp vault; `LOAM_DICTATION_TERMS` points the broker at it, which
is the override the constant documents. The real note is never read.

Run: python3 tests/test_broker_dictation_terms.py
"""

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_broker as cfg  # noqa: E402
# Admission reads no real box here: a fixture session must not be refused
# because the machine running the suite is short of memory (a landing
# got 429 for one, 2026-11-09). test_broker_seats is where admission is tested.
cfg.memory = lambda: {"available": None, "cgroup_room": None,
                      "reserve": cfg.MEMORY_RESERVE_MB}

loader = importlib.machinery.SourceFileLoader(
    "session_broker", str(AGENTS / "bin" / "session-broker"))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker_mod = importlib.util.module_from_spec(spec)
loader.exec_module(broker_mod)

results = []

NOTE = "# Dictation terms\n\n## People\n\nSam\nRiver, Okafor\n"


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:64} got={got!r}")
    if not ok:
        print(f"      wanted {want!r}")


class FakeWriter:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.chunks.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    def parsed(self):
        head, _, payload = b"".join(self.chunks).decode().partition("\r\n\r\n")
        lines = head.split("\r\n")
        headers = {k.strip().lower(): v.strip() for k, _, v in
                   (line.partition(":") for line in lines[1:])}
        return int(lines[0].split()[1]), headers, payload


async def request(server, target: str, token: str, method: str = "GET"):
    """One request through the real parser: request line, headers, bearer."""
    reader = asyncio.StreamReader()
    reader.feed_data(f"{method} {target} HTTP/1.1\r\n"
                     f"Authorization: Bearer {token}\r\n\r\n".encode())
    reader.feed_eof()
    writer = FakeWriter()
    await server.handle(reader, writer)
    return writer.parsed()


def test_constant() -> None:
    check("the constant is the vault note",
          cfg.DICTATION_TERMS_PATH,
          cfg.VAULT / "99_SYSTEM" / "Dictation terms.md")
    check("the override is one named variable",
          cfg.DICTATION_TERMS_ENV, "LOAM_DICTATION_TERMS")
    # The shape the real note would be answered with, computed without
    # reading it: a vault path gives a vault-relative name and a link.
    tmp_vault = Path(tempfile.mkdtemp(prefix="vault-"))
    keep = cfg.VAULT
    try:
        cfg.VAULT = tmp_vault
        note = tmp_vault / "99_SYSTEM" / "Dictation terms.md"
        note.parent.mkdir()
        note.write_text(NOTE, encoding="utf-8")
        got = cfg.dictation_terms({"LOAM_DICTATION_TERMS": str(note)})
        check("vault-relative path", got["path"], "99_SYSTEM/Dictation terms.md")
        check("obsidian link names the vault directory and the note",
              got["obsidian_url"],
              f"obsidian://open?vault={tmp_vault.name}"
              f"&file=99_SYSTEM%2FDictation%20terms")
        outside = Path(tempfile.mkdtemp(prefix="elsewhere-")) / "terms.md"
        outside.write_text(NOTE, encoding="utf-8")
        got = cfg.dictation_terms({"LOAM_DICTATION_TERMS": str(outside)})
        check("outside the vault: absolute path, no link",
              (got["path"], got["obsidian_url"]), (str(outside), None))
        shutil.rmtree(outside.parent, ignore_errors=True)
    finally:
        cfg.VAULT = keep
        shutil.rmtree(tmp_vault, ignore_errors=True)
    check("the real vault is called 'vault' in the link", cfg.VAULT.name, "vault")


async def test_route(tmp: Path, server) -> None:
    token = server.broker.token
    cfg.VAULT = tmp / "vault"
    note = cfg.VAULT / "99_SYSTEM" / "Dictation terms.md"
    note.parent.mkdir(parents=True)
    note.write_text(NOTE, encoding="utf-8")
    os.utime(note, (1_790_000_000, 1_790_000_000))
    os.environ[cfg.DICTATION_TERMS_ENV] = str(note)

    status, headers, payload = await request(server, "/v1/dictation-terms", token)
    print("--- a 200 body, verbatim ---\n" + payload + "\n---")
    body = json.loads(payload)
    check("200", status, 200)
    check("no-store", headers.get("cache-control"), "no-store")
    check("the note's text, whole", body["text"], NOTE)
    check("the note's mtime", body["modified"], "2026-09-21T14:13:20Z")
    check("vault-relative path", body["path"], "99_SYSTEM/Dictation terms.md")
    check("obsidian link", body["obsidian_url"],
          "obsidian://open?vault=vault&file=99_SYSTEM%2FDictation%20terms")

    status, _, _ = await request(server, "/v1/dictation-terms", "wrong")
    check("no token: 401", status, 401)

    # Every path-like thing a client might try. A query is never read, so the
    # answer is the configured note; a longer path is no route at all.
    for q in ("?path=/etc/passwd", "?file=../../agents/wake.env",
              "?path=%2Fetc%2Fpasswd&vault=x"):
        status, _, payload = await request(server, "/v1/dictation-terms" + q, token)
        check(f"query ignored: {q}", (status, json.loads(payload)["text"]),
              (200, NOTE))
    for tail in ("/etc/passwd", "/..%2F..%2Fwake.env", "/x"):
        status, _, payload = await request(server, "/v1/dictation-terms" + tail, token)
        check(f"segment refused: {tail}", (status, "text" in json.loads(payload)),
              (405, False))
    status, _, _ = await request(server, "/v1/dictation-terms", token, "POST")
    check("POST is not this route", status, 405)

    note.unlink()
    status, headers, payload = await request(server, "/v1/dictation-terms", token)
    check("missing note: 404", status, 404)
    check("missing note: no-store", headers.get("cache-control"), "no-store")
    check("missing note: one sentence", json.loads(payload),
          {"error": "no dictation terms note on this box"})


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="broker-dictation-test-"))
    keep = (cfg.STATE_DIR, cfg.SESSION_DIR, cfg.USAGE_PATH, cfg.TOKEN_PATH,
            cfg.VAULT)
    env_keep = os.environ.get(cfg.DICTATION_TERMS_ENV)
    try:
        cfg.STATE_DIR = tmp
        cfg.SESSION_DIR = tmp / "sessions"
        cfg.USAGE_PATH = tmp / "provider-usage.json"
        cfg.TOKEN_PATH = tmp / "token"
        test_constant()
        server = broker_mod.Server(broker_mod.Broker())
        await test_route(tmp, server)
    finally:
        (cfg.STATE_DIR, cfg.SESSION_DIR, cfg.USAGE_PATH, cfg.TOKEN_PATH,
         cfg.VAULT) = keep
        if env_keep is None:
            os.environ.pop(cfg.DICTATION_TERMS_ENV, None)
        else:
            os.environ[cfg.DICTATION_TERMS_ENV] = env_keep
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
