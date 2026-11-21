"""Vault deletion goes through the broker, and symlink targets are preserved.

Run: mcp/.venv/bin/python tests/test_delete_symlink.py
"""

import asyncio
import importlib.util
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = tempfile.mkdtemp(prefix="delsym-")
os.environ.update({
    "LOAM_AGENT": "tester",
    "LOAM_AGENT_WORKSPACE": f"{ROOT}/ai-labs/tester",
    "LOAM_VAULT": f"{ROOT}/vault",
    "LOAM_AI_LABS": f"{ROOT}/ai-labs",
})
for d in ("ai-labs/tester", "ai-labs/ada", "vault/notes", "vault/15_CONVERSATIONS", "outside"):
    Path(ROOT, d).mkdir(parents=True, exist_ok=True)

# The server in this tree, not the live one: the test is of the branch it ships with.
SERVER = Path(__file__).resolve().parent.parent / "mcp" / "read_epub_server.py"
spec = importlib.util.spec_from_file_location("loam_server", SERVER)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

fails = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global fails
    fails += (not cond)
    print(("PASS  " if cond else "FAIL  ") + label + (("  | " + detail) if detail else ""))


def delete(p: Path) -> dict:
    return asyncio.run(m.delete_file(str(p)))


V = Path(ROOT, "vault")


def vault_call(method, route, payload):
    import loam_files
    loam_files.cfg.VAULT = V
    check("vault tool uses authenticated broker client route",
          (method, route, payload["root"]) in {
              ("POST", "/v1/files/delete", "vault"),
              ("POST", "/v1/files/restore", "vault")})
    operation = loam_files.delete if route.endswith("/delete") else loam_files.restore
    try:
        return operation(payload)
    except loam_files.FilesError as exc:
        raise m.SettingsError(exc.message, exc.status) from exc


mock = patch.object(m, "broker_call", vault_call)
mock.start()

# The incident: a vault-root link to a note in the vault.
note = V / "notes" / "overview.md"
note.write_text("keep me\n")
link = V / "README.md"
link.symlink_to("notes/overview.md")
r = delete(link)
check("link to a vault note: Files refuses it", r.get("error") == "not_found", str(r))
check("link to a vault note: link stays", link.is_symlink())
check("link to a vault note: referent untouched", note.read_text() == "keep me\n")

# A link whose referent is outside the vault cannot cross the boundary.
secret = Path(ROOT, "outside", "wake.env")
secret.write_text("x\n")
link = V / "env-link"
link.symlink_to(secret)
r = delete(link)
check("link to outside: refused", r.get("error") == "not_found" and link.is_symlink(), str(r))
check("link to outside: referent untouched", secret.exists())

# A link into another agent's workspace, sitting in the vault.
sibling = Path(ROOT, "ai-labs", "ada", "soul.md")
sibling.write_text("soul\n")
link = V / "ada-soul"
link.symlink_to(sibling)
r = delete(link)
check("link to sibling workspace: refused", r.get("error") == "not_found" and link.is_symlink(), str(r))
check("link to sibling workspace: referent untouched", sibling.exists())

# A link to a directory gets the same refusal.
link = V / "notes-link"
link.symlink_to(V / "notes")
r = delete(link)
check("link to directory: refused", r.get("error") == "not_found" and link.is_symlink(), str(r))
check("link to directory: directory and contents untouched", note.exists())

# A dangling link is refused in the vault too.
link = V / "dangling"
link.symlink_to(V / "nowhere.md")
r = delete(link)
check("dangling link: refused", r.get("error") == "not_found" and link.is_symlink(), str(r))

# The referent survives unlinking even outside the workspace.
link = Path(ROOT, "ai-labs", "ada", "into-vault")
link.symlink_to(note)
r = delete(link)
check("link in sibling workspace: removed, referent kept",
      r.get("ok") is True and not link.is_symlink() and note.exists(), str(r))

link = V / "15_CONVERSATIONS" / "x.md"
link.symlink_to(note)
r = delete(link)
check("link in 15_CONVERSATIONS: refused, referent kept",
      r.get("error") == "boundary" and link.is_symlink() and note.exists(), str(r))

alias = Path(ROOT, "outside", "vault-alias")
alias.symlink_to(V)
r = delete(alias / "notes" / "overview.md")
check("alias into vault: refused, referent kept", r.get("error") == "boundary" and note.exists(), str(r))

# Plain files still delete as files.
plain = V / "plain.md"
plain.write_text("x")
r = delete(plain)
check("plain file: kind is file", r.get("ok") is True and r.get("kind") == "file" and not plain.exists(), str(r))
restored = asyncio.run(m.restore_file(r["restore_id"]))
check("plain file: restored by token", restored.get("ok") is True and plain.read_text() == "x", str(restored))
mock.stop()

print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
raise SystemExit(1 if fails else 0)
