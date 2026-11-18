#!/usr/bin/env python3
"""An agent's reply can show images (2026-11-18): `![words](path)` in what it
says, a file in the vault or the workspaces, up to three, taken off the text
onto the message as attachments the page draws in the bubble.

Run: python3 tests/test_reply_images.py
"""

import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="loam-reply-images-"))
VAULT, SPACES = TMP / "vault", TMP / "ai-labs"
os.environ["LOAM_VAULT"] = str(VAULT)
os.environ["LOAM_AI_LABS"] = str(SPACES)
AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_threads as threads  # noqa: E402
import loam_thread_engine as engine  # noqa: E402

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:66}{'  | ' + str(detail)[:300] if detail != '' else ''}")


PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
for rel in ("Images/a fox.png", "Images/b.jpg", "Images/c.webp", "Images/d.png", "notes.md"):
    (VAULT / rel).parent.mkdir(parents=True, exist_ok=True)
    (VAULT / rel).write_bytes(PNG)
(SPACES / "iris" / "art").mkdir(parents=True)
(SPACES / "iris" / "art" / "cover.png").write_bytes(PNG)
conv = VAULT / "15_CONVERSATIONS"
conv.mkdir()
(conv / "x.png").write_bytes(PNG)
(TMP / "outside.png").write_bytes(PNG)

text, found = threads.reply_images(
    f"Here it is.\n\n![a fox at dawn]({VAULT}/Images/a fox.png)\n\nWhat do you think?")
check("an absolute vault path on its own line comes off, line and all",
      text == "Here it is.\n\nWhat do you think?"
      and found == [{"path": "Images/a fox.png", "root": "vault", "name": "a fox.png",
                     "type": "image/png", "size": len(PNG), "alt": "a fox at dawn"}], (text, found))
text, found = threads.reply_images("See ![](Images/b.jpg) and ![c](<Images/c.webp>).")
check("vault-relative paths and <angle> paths, inline, keep the sentence",
      [f["path"] for f in found] == ["Images/b.jpg", "Images/c.webp"]
      and text == "See and.", (text, found))
text, found = threads.reply_images(f"![cover]({SPACES}/iris/art/cover.png)")
check("a workspace image rides with root spaces; nothing else said is fine",
      found and found[0]["root"] == "spaces" and found[0]["path"] == "iris/art/cover.png"
      and text == "", (text, found))
refs = "\n".join(f"![{n}](Images/{n})" for n in ("a fox.png", "b.jpg", "c.webp", "d.png"))
text, found = threads.reply_images(refs)
check("at most three; the fourth stays as written",
      len(found) == 3 and text == "![d.png](Images/d.png)", (text, len(found)))
for label, ref in (("a web address", "https://example.com/x.png"),
                   ("not an image", "notes.md"),
                   ("a missing file", "Images/none.png"),
                   ("outside the vault and workspaces", str(TMP / "outside.png")),
                   ("the conversations folder", f"{conv}/x.png"),
                   ("a way out by ..", "../outside.png")):
    t = f"![x]({ref})"
    check(f"{label}: left in the text, nothing attached", threads.reply_images(t) == (t, []))
text, found = threads.reply_images("![a](Images/b.jpg)\n![again](Images/b.jpg)")
check("the same file twice is one attachment", len(found) == 1 and text == "", (text, found))

text, extra = engine.reply_images("And the last one: ![d](Images/d.png)",
                                  {"segments": [{"text": "First ![a](Images/b.jpg)"},
                                                {"did": [{"label": "Read"}]}]})
check("a reply's earlier words give theirs too, after its last words'",
      [f["path"] for f in extra["attachments"]] == ["Images/d.png", "Images/b.jpg"]
      and extra["segments"][0]["text"] == "First" and text == "And the last one:", extra)
check("an attachment is named by its place on disk to an agent",
      threads.attachment_path({"path": "iris/art/cover.png", "root": "spaces"})
      == str(SPACES / "iris" / "art" / "cover.png")
      and threads.attachment_path({"path": "Images/b.jpg"}) == str(VAULT / "Images" / "b.jpg"))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
