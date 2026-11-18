#!/usr/bin/env python3
"""An agent's reply can show images (2026-11-18): `![words](path)` in what it
says, a file in the vault or the workspaces, up to three, onto the message
as attachments. Since that evening the reference stays in the text, where
the page draws its image, and each attachment carries the destinations that
named it (`refs`) and, where the header says, its pixel size.

Run: python3 tests/test_reply_images.py
"""

import os
import struct
import sys
import tempfile
import zlib
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="loam-reply-images-"))
VAULT, SPACES = TMP / "vault", TMP / "ai-labs"
os.environ["LOAM_VAULT"] = str(VAULT)
os.environ["LOAM_AI_LABS"] = str(SPACES)
os.environ["LOAM_THREADS_STATE"] = str(TMP / "state")
AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_threads as threads  # noqa: E402
import loam_thread_engine as engine  # noqa: E402

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:66}{'  | ' + str(detail)[:300] if detail != '' else ''}")


def png(w, h):
    raw = b"".join(b"\0" + b"\0\0\0" * w for _ in range(h))
    chunk = lambda kind, data: (struct.pack(">I", len(data)) + kind + data  # noqa: E731
                                + struct.pack(">I", zlib.crc32(kind + data)))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def jpeg(w, h):
    # SOI, an APP0 to walk past, then SOF0 with the size.
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\0" + b"\1\1\0\0\1\0\1\0\0"
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\1\x11\0"
    return b"\xff\xd8" + app0 + sof + b"\xff\xd9"


def gif(w, h):
    return b"GIF89a" + struct.pack("<HH", w, h) + b"\0\0\0;"


def webp(w, h):
    vp8x = b"VP8X" + struct.pack("<I", 10) + b"\0\0\0\0" + (w - 1).to_bytes(3, "little") \
        + (h - 1).to_bytes(3, "little")
    return b"RIFF" + struct.pack("<I", 4 + len(vp8x)) + b"WEBP" + vp8x


FILES = {"Images/a fox.png": png(4, 3), "Images/b.jpg": jpeg(640, 480),
         "Images/c.webp": webp(300, 600), "Images/d.png": png(2, 2),
         "Images/e.gif": gif(5, 7), "notes.md": b"# notes"}
for rel, data in FILES.items():
    (VAULT / rel).parent.mkdir(parents=True, exist_ok=True)
    (VAULT / rel).write_bytes(data)
(SPACES / "iris" / "art").mkdir(parents=True)
(SPACES / "iris" / "art" / "cover.png").write_bytes(png(8, 5))
conv = VAULT / "15_CONVERSATIONS"
conv.mkdir()
(conv / "x.png").write_bytes(png(1, 1))
(TMP / "outside.png").write_bytes(png(1, 1))

said = f"Here it is.\n\n![a fox at dawn]({VAULT}/Images/a fox.png)\n\nWhat do you think?"
text, found = threads.reply_images(said)
check("the reference stays where it was written; the file rides along",
      text == said
      and found == [{"path": "Images/a fox.png", "root": "vault", "name": "a fox.png",
                     "type": "image/png", "size": len(FILES["Images/a fox.png"]),
                     "width": 4, "height": 3, "alt": "a fox at dawn",
                     "refs": [f"{VAULT}/Images/a fox.png"]}], (text, found))
text, found = threads.reply_images("See ![](Images/b.jpg) and ![c](<Images/c.webp>).")
check("vault-relative and <angle> destinations are named as written",
      [f["path"] for f in found] == ["Images/b.jpg", "Images/c.webp"]
      and [f["refs"] for f in found] == [["Images/b.jpg"], ["<Images/c.webp>"]]
      and "alt" not in found[0], found)
check("a JPEG's size is read past its APP0, a WebP's from VP8X",
      (found[0]["width"], found[0]["height"], found[1]["width"], found[1]["height"])
      == (640, 480, 300, 600), found)
check("a GIF's size", threads.image_size(VAULT / "Images/e.gif") == (5, 7))
check("a file that does not say has no size",
      threads.image_size(VAULT / "notes.md") is None)
text, found = threads.reply_images(f"![cover]({SPACES}/iris/art/cover.png)")
check("a workspace image rides with root spaces",
      found and found[0]["root"] == "spaces" and found[0]["path"] == "iris/art/cover.png",
      found)
refs = "\n".join(f"![{n}](Images/{n})" for n in ("a fox.png", "b.jpg", "c.webp", "d.png"))
text, found = threads.reply_images(refs)
check("at most three; the fourth is not attached and stays text",
      len(found) == 3 and text == refs
      and all("Images/d.png" not in f["refs"] for f in found), (text, len(found)))
for label, ref in (("a web address", "https://example.com/x.png"),
                   ("not an image", "notes.md"),
                   ("a missing file", "Images/none.png"),
                   ("outside the vault and workspaces", str(TMP / "outside.png")),
                   ("the conversations folder", f"{conv}/x.png"),
                   ("a way out by ..", "../outside.png")):
    t = f"![x]({ref})"
    check(f"{label}: left in the text, nothing attached", threads.reply_images(t) == (t, []))
text, found = threads.reply_images(
    f"![a](Images/b.jpg)\n![again](Images/b.jpg)\n![abs]({VAULT}/Images/b.jpg)")
check("the same file twice is one attachment, every name for it in refs",
      len(found) == 1 and found[0]["refs"] == ["Images/b.jpg", f"{VAULT}/Images/b.jpg"],
      found)
text, found = threads.reply_images(
    "![1](Images/a fox.png) ![2](Images/b.jpg) ![3](Images/c.webp) "
    f"![one more name for 1]({VAULT}/Images/a fox.png)")
check("past three, another name for a file already shown is still that file",
      len(found) == 3 and len(found[0]["refs"]) == 2, found)

text, extra = engine.reply_images("And the last one: ![d](Images/d.png)",
                                  {"segments": [{"text": "First ![a](Images/b.jpg)"},
                                                {"did": [{"label": "Read"}]}]})
check("a reply's images, in reading order: its earlier words, then its last",
      [f["path"] for f in extra["attachments"]] == ["Images/b.jpg", "Images/d.png"]
      and extra["segments"][0]["text"] == "First ![a](Images/b.jpg)"
      and text == "And the last one: ![d](Images/d.png)", extra)
text, extra = engine.reply_images("Nothing to see.", {"segments": [{"text": "Hi"}]})
check("no image, no attachments", "attachments" not in extra, extra)
check("an attachment is named by its place on disk to an agent",
      threads.attachment_path({"path": "iris/art/cover.png", "root": "spaces"})
      == str(SPACES / "iris" / "art" / "cover.png")
      and threads.attachment_path({"path": "Images/b.jpg"}) == str(VAULT / "Images" / "b.jpg"))

# -- the place survives the record ------------------------------------------------
# Posted as the engine posts a reply, then read back from the conversation
# file as the page's next load reads it: the words still say where each
# image goes, and the attachment still says which file each one is.
thread = threads.create(title="Pictures")["id"]
reply = ("Two views.\n\n![light](Images/a fox.png)\n![](Images/b.jpg)\n\n"
         "And the tall one, mid-thought: ![tall](Images/c.webp) — see?")
text, extra = engine.reply_images(reply, {"engine": "claude-code"})
posted = threads.append(thread, "system", text, **{k: v for k, v in extra.items()
                                                    if k != "engine"})
_, msgs, path = threads.read(thread)
back = threads.find(msgs, posted["id"])
check("read back, the text still holds each reference at its place",
      back["text"] == reply.strip(), back["text"])
check("read back, each attachment names the reference it fills",
      [(a["path"], a["refs"], a.get("width")) for a in back["attachments"]]
      == [("Images/a fox.png", ["Images/a fox.png"], 4),
          ("Images/b.jpg", ["Images/b.jpg"], 640),
          ("Images/c.webp", ["Images/c.webp"], 300)], back["attachments"])
index = {r: i for i, a in enumerate(back["attachments"]) for r in a["refs"]}
places = [index.get(m.group(2).strip()) for m in threads.IMAGE_REF_RE.finditer(back["text"])]
check("every reference in the read-back text finds its file, in order",
      places == [0, 1, 2], places)
check("an agent reading the thread sees the words and where the files are",
      "![light](Images/a fox.png)" in threads.transcript(msgs)
      and str(VAULT / "Images" / "b.jpg") in threads.transcript(msgs))
check("the conversation file itself carries the reference as Markdown",
      "![light](Images/a fox.png)" in path.read_text())

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
