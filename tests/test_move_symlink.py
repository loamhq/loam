"""move_file refuses a symlink at either end, and never touches what it points at.

delete_file resolved its path before acting and so unlinked the vault's
README.md referent on 2026-10-02 (tests/test_delete_symlink.py). move_file
resolved both endpoints the same way: moving the README.md link would have
carried overview.md away and left the link dangling, and a destination under
a linked directory landed the file wherever that directory pointed. This test
fails if either comes back: a link as source, a source or destination reached
through a linked directory, and a destination that is itself a link are each
refused with `error: symlink`, the referent is where and what it was, and the
link still points where it did.

Run: mcp/.venv/bin/python tests/test_move_symlink.py

NOT bare `python3`: this imports mcp/read_epub_server.py, which imports the
`mcp` package, which lives only in mcp/.venv.
"""

import asyncio
import importlib.util
import os
import tempfile
from pathlib import Path

ROOT = tempfile.mkdtemp(prefix="movesym-")
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


def move(src: Path, dst: Path) -> dict:
    return asyncio.run(m.move_file(str(src), str(dst)))


V = Path(ROOT, "vault")
note = V / "notes" / "overview.md"
note.write_text("keep me\n")

# The incident's shape, moved instead of deleted: a vault-root link to a note.
link = V / "README.md"
link.symlink_to("notes/overview.md")
r = move(link, V / "renamed.md")
check("link as source: refused", r.get("ok") is False and r.get("error") == "symlink", str(r))
check("link as source: error names the link", str(link) in (r.get("detail") or ""), str(r))
check("link as source: referent untouched", note.read_text() == "keep me\n")
check("link as source: link still points where it did",
      link.is_symlink() and os.readlink(link) == "notes/overview.md")
check("link as source: nothing landed at the destination", not (V / "renamed.md").exists())

# A dangling link is still a link, not "not found".
dangling = V / "dangling"
dangling.symlink_to(V / "nowhere.md")
r = move(dangling, V / "elsewhere")
check("dangling link as source: refused as symlink", r.get("error") == "symlink", str(r))
check("dangling link as source: link kept", dangling.is_symlink())

# A destination under a linked directory would land the file where the link points.
Path(ROOT, "ai-labs", "tester", "mine").mkdir()
target_dir = Path(ROOT, "ai-labs", "tester", "mine")
dir_link = V / "mine-link"
dir_link.symlink_to(target_dir)
plain = V / "plain.md"
plain.write_text("plain\n")
r = move(plain, dir_link / "plain.md")
check("destination through a linked dir: refused", r.get("error") == "symlink", str(r))
check("destination through a linked dir: error says passes through",
      "passes through" in (r.get("detail") or ""), str(r))
check("destination through a linked dir: source kept", plain.read_text() == "plain\n")
check("destination through a linked dir: nothing landed behind the link",
      not (target_dir / "plain.md").exists())
check("destination through a linked dir: link still points where it did",
      os.readlink(dir_link) == str(target_dir))

# A missing subdirectory under a linked dir: mkdir(parents=True) would have built it there.
r = move(plain, dir_link / "new" / "plain.md")
check("destination under a linked dir's new subdir: refused", r.get("error") == "symlink", str(r))
check("destination under a linked dir's new subdir: nothing created",
      not (target_dir / "new").exists())

# A source reached through a linked directory moves the referent's file; refused too.
inside = target_dir / "inside.md"
inside.write_text("inside\n")
r = move(dir_link / "inside.md", V / "pulled.md")
check("source through a linked dir: refused", r.get("error") == "symlink", str(r))
check("source through a linked dir: file untouched", inside.read_text() == "inside\n")

# A dangling link as destination: rename() would replace the link, so it is refused.
dst_link = V / "dst-link"
dst_link.symlink_to(V / "not-there.md")
r = move(plain, dst_link)
check("dangling link as destination: refused", r.get("error") == "symlink", str(r))
check("dangling link as destination: link kept", os.readlink(dst_link) == str(V / "not-there.md"))
check("dangling link as destination: source kept", plain.exists())

# A link whose referent is outside every writable tree.
secret = Path(ROOT, "outside", "wake.env")
secret.write_text("x\n")
out_link = V / "env-link"
out_link.symlink_to(secret)
r = move(out_link, V / "env.md")
check("link to outside: refused", r.get("error") == "symlink", str(r))
check("link to outside: referent untouched", secret.read_text() == "x\n")

# Plain files still move.
r = move(plain, V / "notes" / "moved.md")
check("plain file: moved", r.get("ok") is True and (V / "notes" / "moved.md").exists()
      and not plain.exists(), str(r))
r = move(V / "notes" / "moved.md", V / "fresh" / "dir" / "moved.md")
check("plain file into a new real dir: moved", r.get("ok") is True, str(r))

print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
raise SystemExit(1 if fails else 0)
