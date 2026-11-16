"""delete_file removes a symlink, never what it points at.

Caused on 2026-10-02: the vault root's README.md was a symlink to
40_PROJECTS/Loam/architecture/overview.md, a persona asked delete_file to
remove README.md, and the tool resolved the path before acting -- so it
unlinked overview.md and reported that path back with `kind: "file"`, leaving
the link dangling. This test fails if that shape comes back: a link is the
target, and its referent is untouched wherever it lies.

Run: mcp/.venv/bin/python tests/test_delete_symlink.py

NOT bare `python3`: this imports mcp/read_epub_server.py, which imports the
`mcp` package, which lives only in mcp/.venv.
"""

import asyncio
import importlib.util
import os
import tempfile
from pathlib import Path

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

# The incident: a vault-root link to a note in the vault.
note = V / "notes" / "overview.md"
note.write_text("keep me\n")
link = V / "README.md"
link.symlink_to("notes/overview.md")
r = delete(link)
check("link to a vault note: ok", r.get("ok") is True, str(r))
check("link to a vault note: kind is symlink", r.get("kind") == "symlink", str(r))
check("link to a vault note: reports the link's path", r.get("deleted") == str(link), str(r))
check("link to a vault note: link is gone", not link.is_symlink() and not link.exists())
check("link to a vault note: referent untouched", note.read_text() == "keep me\n")

# A link whose referent is outside every writable tree is still a link in the vault.
secret = Path(ROOT, "outside", "wake.env")
secret.write_text("x\n")
link = V / "env-link"
link.symlink_to(secret)
r = delete(link)
check("link to outside: removed as a link", r.get("ok") is True and not link.is_symlink(), str(r))
check("link to outside: referent untouched", secret.exists())

# A link into another agent's workspace, sitting in the vault.
sibling = Path(ROOT, "ai-labs", "ada", "soul.md")
sibling.write_text("soul\n")
link = V / "ada-soul"
link.symlink_to(sibling)
r = delete(link)
check("link to sibling workspace: removed", r.get("ok") is True and not link.is_symlink(), str(r))
check("link to sibling workspace: referent untouched", sibling.exists())

# A link to a directory is removed as a link, not refused as a populated directory.
link = V / "notes-link"
link.symlink_to(V / "notes")
r = delete(link)
check("link to directory: removed as a link", r.get("kind") == "symlink" and not link.is_symlink(), str(r))
check("link to directory: directory and contents untouched", note.exists())

# A dangling link is removable, not "not found".
link = V / "dangling"
link.symlink_to(V / "nowhere.md")
r = delete(link)
check("dangling link: removed", r.get("ok") is True and not link.is_symlink(), str(r))

# No path boundary since 2026-11-08 ("the authority move"): a link anywhere
# is removed as a link, and its referent is still never touched.
link = Path(ROOT, "ai-labs", "ada", "into-vault")
link.symlink_to(note)
r = delete(link)
check("link in sibling workspace: removed, referent kept",
      r.get("ok") is True and not link.is_symlink() and note.exists(), str(r))

link = V / "15_CONVERSATIONS" / "x.md"
link.symlink_to(note)
r = delete(link)
check("link in 15_CONVERSATIONS: removed, referent kept",
      r.get("ok") is True and not link.is_symlink() and note.exists(), str(r))

# Plain files still delete as files.
plain = V / "plain.md"
plain.write_text("x")
r = delete(plain)
check("plain file: kind is file", r.get("ok") is True and r.get("kind") == "file" and not plain.exists(), str(r))

print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
raise SystemExit(1 if fails else 0)
