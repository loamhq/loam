#!/usr/bin/env python3
"""Resolve images embedded in agent replies into attachments while preserving their
positions.

Run: python3 tests/test_reply_images.py
"""

import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import types
import zlib
from pathlib import Path
from unittest.mock import patch

scratch = tempfile.TemporaryDirectory(prefix="loam-reply-images-")
TMP = Path(scratch.name)
VAULT, SPACES = TMP / "vault", TMP / "ai-labs"
BROWSER_OUT = TMP / "browser-out"
BROWSER_OUT.mkdir()
os.environ["LOAM_VAULT"] = str(VAULT)
os.environ["LOAM_AI_LABS"] = str(SPACES)
os.environ["LOAM_THREADS_STATE"] = str(TMP / "state")
os.environ["LOAM_BROWSER_OUTPUT"] = str(BROWSER_OUT)
AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))
import loam_threads as threads  # noqa: E402
import loam_thread_engine as engine  # noqa: E402
import loam_broker as cfg  # noqa: E402
import loam_files as files  # noqa: E402

cfg.VAULT, cfg.AI_LABS = VAULT, SPACES
threads.lc.JOBS = TMP / "jobs"

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
(SPACES / "iris" / "agent.json").write_text("{}")
(SPACES / "iris" / "art" / "cover.png").write_bytes(png(8, 5))
conv = VAULT / "15_CONVERSATIONS"
conv.mkdir()
(conv / "x.png").write_bytes(png(1, 1))
(TMP / "outside.png").write_bytes(png(1, 1))
(TMP / "link.png").symlink_to(TMP / "outside.png")
blocked_job = threads.lc.JOBS / "123" / "private.png"
blocked_job.parent.mkdir(parents=True)
blocked_job.write_bytes(png(1, 1))

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
                   ("a symbolic link", str(TMP / "link.png")),
                   ("a job file outside captures", str(blocked_job)),
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


def downloaded(root, path):
    try:
        fd, size, _ = files.download(root, path)
    except files.FilesError as exc:
        return exc.status, b""
    with os.fdopen(fd, "rb") as f:
        return 200, f.read(size)


scratch_image = SPACES / "iris" / "scratch" / "cover.png"
scratch_image.parent.mkdir()
payload = png(16, 9)
scratch_image.write_bytes(payload)
scratch_reply = f"Preview below.\n\n![Scratch preview]({scratch_image})"
text, extra = engine.reply_images(scratch_reply, {})
posted = threads.append(thread, "iris", text, **extra)
scratch_image.unlink()
_, msgs, _ = threads.read(thread)
back = threads.find(msgs, posted["id"])["attachments"][0]
check("a posted scratch image still loads from Files after its source is removed",
      back["root"] == "vault"
      and downloaded(back["root"], back["path"]) == (200, payload)
      and back["refs"] == [str(scratch_image)], back)

scratch_image.write_bytes(payload)
check("Files still hides the scratch source",
      downloaded("spaces", "iris/scratch/cover.png")[0] == 404)

legacy = threads.create(title="Legacy pictures")["id"]
gone = {"path": "iris/scratch/gone.png", "root": "spaces", "type": "image/png",
        "alt": "Lost view"}
old = {**gone, "path": "iris/scratch/cover.png", "alt": "Scratch preview"}
first = threads.append(legacy, "iris", scratch_reply, attachments=[old, gone])
capture = blocked_job.parent / "captures" / "cover.png"
capture.parent.mkdir()
capture.write_bytes(payload)
temp_image = TMP / "temporary.png"
temp_image.write_bytes(payload)
second = threads.append(legacy, "iris", f"![Capture]({capture})\n\n![Temp]({temp_image})",
                        reply_to=first["id"])
owner = threads.append(legacy, threads.OWNER, "Owner attachment", attachments=[old])
record = threads.record_path(legacy)
before = record.read_bytes()
check("dry-run finds the recoverable images without editing the record",
      threads.backfill_reply_images(legacy, dry_run=True) == [("m1", 1, 1), ("m2", 2, 0)]
      and record.read_bytes() == before)
store = VAULT / threads.REPLY_ATTACHMENTS
files_before = set(store.rglob("*.png"))
with patch.object(threads, "_write_atomic", side_effect=OSError("test failed write")):
    try:
        threads.backfill_reply_images(legacy)
    except OSError:
        check("a failed record write removes its unreferenced copies",
              set(store.rglob("*.png")) == files_before and record.read_bytes() == before)
    else:
        check("a failed record write is reported", False)
check("backfill repairs existing attachments and previously ignored captures",
      threads.backfill_reply_images(legacy) == [("m1", 1, 1), ("m2", 2, 0)])
after = record.read_bytes()
comments = rb"(?m)^<!-- loam [^\n]+$"
check("backfill preserves every byte outside the metadata comments",
      re.sub(comments, b"", before) == re.sub(comments, b"", after))
_, msgs, _ = threads.read(legacy)
first, second, owner = msgs
copies = [first["attachments"][0], *second["attachments"]]
check("backfilled images load, including two sources with the same filename",
      len({a["path"] for a in copies}) == 3
      and all(downloaded(a["root"], a["path"]) == (200, payload) for a in copies))
check("missing source and owner upload metadata are preserved",
      first["attachments"][1] == gone and owner["attachments"] == [old])
check("running the backfill twice leaves the record unchanged on the second run",
      threads.backfill_reply_images(legacy) == [("m1", 0, 1)] and record.read_bytes() == after)
check("the broker's migration remembers completion",
      threads.migrate_reply_images() == {
          "errors": {}, "threads": {legacy: {"m1": {"copied": 0, "unavailable": 1}}},
      } and threads.migrate_reply_images() is None)

# -- browser screenshots and post-time feedback --------------------------------
shot = BROWSER_OUT / "page-2026-11-23T03-55-03-150Z.png"
shot_bytes = png(390, 844)
shot.write_bytes(shot_bytes)
shot_reply = f"Week five on a phone:\n\n![Week five on a phone]({shot})"
text, extra, unkept = engine.reply_images_checked(shot_reply, {})
check("a browser-tool screenshot resolves with no unkept warnings",
      not unkept and len(extra.get("attachments") or []) == 1
      and extra["attachments"][0]["path"] == str(shot)
      and extra["attachments"][0]["width"] == 390, (extra, unkept))
posted_shot = threads.append(thread, "iris", text, **extra)
shot.unlink()
_, msgs, _ = threads.read(thread)
saved_shot = threads.find(msgs, posted_shot["id"])["attachments"][0]
check("a browser-tool screenshot stays loadable after the browser output is evicted",
      saved_shot["root"] == "vault"
      and saved_shot["path"].startswith(f"{threads.REPLY_ATTACHMENTS}/")
      and downloaded(saved_shot["root"], saved_shot["path"]) == (200, shot_bytes)
      and saved_shot["refs"] == [str(shot)], saved_shot)

# Simulate Docker userns-remapped node uid (201000) owning BROWSER_OUTPUT and its files.
shot.write_bytes(shot_bytes)
real_open_regular_at = files.open_regular_at
real_stat = os.stat


def _fake_stat(uid):
    st = real_stat(shot)
    return types.SimpleNamespace(st_size=st.st_size, st_uid=uid, st_mode=st.st_mode)


with patch.object(files, "open_regular_at",
                  side_effect=lambda base, parts: (real_open_regular_at(base, parts)[0], _fake_stat(201000))), \
     patch.object(os, "stat", side_effect=lambda p, **kw: _fake_stat(201000) if Path(p) == BROWSER_OUT else real_stat(p, **kw)):
    _, found_remapped, unkept_remapped = threads.reply_images_checked(f"![Remapped]({shot})")
check("a browser screenshot owned by the container's remapped uid is accepted",
      len(found_remapped) == 1 and not unkept_remapped, (found_remapped, unkept_remapped))

with patch.object(files, "open_regular_at",
                  side_effect=lambda base, parts: (real_open_regular_at(base, parts)[0], _fake_stat(999999))), \
     patch.object(os, "stat", side_effect=lambda p, **kw: _fake_stat(201000) if Path(p) == BROWSER_OUT else real_stat(p, **kw)):
    _, found_stranger, unkept_stranger = threads.reply_images_checked(f"![Stranger]({shot})")
check("a browser-dir file owned by an unrelated user is refused",
      not found_stranger and len(unkept_stranger) == 1
      and "another user" in unkept_stranger[0]["reason"], (found_stranger, unkept_stranger))

# Dotted subdirectory under /tmp (such as /tmp/.playwright-mcp) owned by this user is accepted;
# secret names under /tmp remain blocked.
pw_tmp = TMP / ".playwright-mcp"
pw_tmp.mkdir()
pw_shot = pw_tmp / "page-2026-11-23T03-55-03-150Z.png"
pw_shot.write_bytes(shot_bytes)
pw_secret = pw_tmp / ".env.png"
pw_secret.write_bytes(shot_bytes)
_, pw_found, pw_unkept = threads.reply_images_checked(f"![Old path]({pw_shot})")
check("a user-owned image under /tmp/.playwright-mcp/ is accepted for backfill",
      len(pw_found) == 1 and not pw_unkept, (pw_found, pw_unkept))
_, sec_found, sec_unkept = threads.reply_images_checked(f"![Secret]({pw_secret})")
check("a secret-named file under /tmp is still refused",
      not sec_found and len(sec_unkept) == 1, (sec_found, sec_unkept))

# Code spans and fenced code blocks are ignored as image references.
code_text = (
    "Show an image with `![caption](/tmp/does-not-exist.png)`.\n\n"
    "```markdown\n![example](/var/lib/loam/browser/not-real.png)\n```\n"
)
_, code_found, code_unkept = threads.reply_images_checked(code_text)
check("image examples inside inline code or fenced blocks are not flagged",
      not code_found and not code_unkept, (code_found, code_unkept))

# Unkept local images report clear reasons; web URLs and non-images do not.
_, mix_found, mix_unkept = threads.reply_images_checked(
    f"![web](https://example.com/a.png)\n![doc](notes.md)\n"
    f"![gone](/tmp/definitely-absent-12345.png)\n![bad]({blocked_job})"
)
check("reply_images_checked reports unkept local images and ignores web/non-image refs",
      not mix_found and [u["ref"] for u in mix_unkept]
      == ["/tmp/definitely-absent-12345.png", str(blocked_job)]
      and "not found" in mix_unkept[0]["reason"]
      and "outside" in mix_unkept[1]["reason"], mix_unkept)

# bin/thread post refuses unkept images at post time and accepts a real browser screenshot.
bin_thread = str(AGENTS / "bin" / "thread")
env = {**os.environ, "LOAM_VAULT": str(VAULT), "LOAM_AI_LABS": str(SPACES),
       "LOAM_THREADS_STATE": str(TMP / "state"), "LOAM_BROWSER_OUTPUT": str(BROWSER_OUT)}
before_count = len(threads.read(thread)[1])
bad_post = subprocess.run(
    [sys.executable, bin_thread, "post", thread,
     "Here it is:\n\n![Missing](/tmp/not-here-99999.png)", "--by", "iris"],
    capture_output=True, text=True, env=env,
)
bad_json = json.loads(bad_post.stdout)
check("bin/thread post refuses a message with an unkept local image at post time",
      bad_post.returncode == 1 and bad_json.get("ok") is False
      and "/tmp/not-here-99999.png" in bad_json.get("error", "")
      and len(threads.read(thread)[1]) == before_count, (bad_post.returncode, bad_json))

good_post = subprocess.run(
    [sys.executable, bin_thread, "post", thread,
     f"Here is the browser shot:\n\n![Shot]({shot})", "--by", "iris"],
    capture_output=True, text=True, env=env,
)
good_json = json.loads(good_post.stdout)
shot.unlink()
saved_via_bin = (good_json.get("message") or {}).get("attachments") or []
check("bin/thread post copies a browser screenshot into attachments before returning ok",
      good_post.returncode == 0 and good_json.get("ok") is True
      and len(saved_via_bin) == 1 and saved_via_bin[0]["root"] == "vault"
      and downloaded("vault", saved_via_bin[0]["path"]) == (200, shot_bytes),
      (good_post.returncode, good_json))

# ThreadEngine.post_reply schedules one immediate follow-up notice when a reply has an unkept image.
te = engine.ThreadEngine.__new__(engine.ThreadEngine)
te.turns = {}
te.publish = lambda *a, **kw: None
te.summary_due = lambda *a, **kw: 0
te.agent_mentions = lambda *a, **kw: None
scheduled = []
te.schedule = lambda t, **kw: scheduled.append(t)
fake_session = types.SimpleNamespace(id="sess-1", provider="claude-code", seq=1, turns=[])
bad_reply = types.SimpleNamespace(
    spoken=lambda: "Look here:\n\n![Broken](/tmp/missing-reply-shot.png)",
    before=lambda: [],
    did=[],
    events=[{"seq": 10}],
    launched=[],
    model=None,
)
turn1 = engine.Turn(thread, "iris", 0, "m1")
m_bad = te.post_reply(turn1, fake_session, bad_reply, "claude-code", None, "end_turn", mentions=False)
check("post_reply notifies the persona once when an image in its reply cannot be kept",
      len(scheduled) == 1
      and scheduled[0].persona == "iris"
      and scheduled[0].reason == f"unkept-image:{m_bad['id']}"
      and "/tmp/missing-reply-shot.png" in (scheduled[0].note or "")
      and "file not found" in (scheduled[0].note or ""), scheduled)
# A reply to an unkept-image notice must not trigger another loop.
turn2 = engine.Turn(thread, "iris", 0, f"unkept-image:{m_bad['id']}")
te.post_reply(turn2, fake_session, bad_reply, "claude-code", None, "end_turn", mentions=False)
check("an unkept-image follow-up turn does not loop",
      len(scheduled) == 1, len(scheduled))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
