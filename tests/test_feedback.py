#!/usr/bin/env python3
"""lib/loam_feedback.py and bin/loam-report: a report to Loam's makers,
with what is personal taken out (2026-11-17).

A scratch config names an owner, a user, a domain and agents, and a scratch
checkout has a release tag and one commit of its own. Checks:

  - scrub takes out the owner's name, an agent's name (not the starter's),
    the server's user, the install's address and any host under it, every
    path of the install (and anyone's home), e-mail and IP addresses (not
    loopback), keys by shape and by keyword, a link's query and a note's
    name; and keeps commit hashes and long code names;
  - draft: the link is GitHub's new-issue page on the upstream repository
    with the scrubbed title, body and label, and decodes back to exactly what
    the owner is shown; a body too long for a link is pasted instead;
  - a patch: the diff of the checkout's own commits since the release,
    scrubbed too; none to send is refused;
  - the command: `loam report bug` prints what would go and the link, and
    sends nothing; `--json` is the same as data; a missing title is exit 2;
    `loam report idea` is a kind of its own with the same link; there is
    no `send` (the private route of 0.2.7 was taken out in 0.2.11).

Run: python3 tests/test_feedback.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import pwd
import tempfile
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USER = pwd.getpwuid(os.getuid()).pw_name   # the user must exist; it is this one
TMP = Path(tempfile.mkdtemp(prefix="test-feedback-"))
HOME = TMP / "home" / "samuser"
CHECKOUT = TMP / "loam"
CONFIG = TMP / "loam.toml"
CONFIG.write_text(f'''
[owner]
id = "sam"
name = "Sam Rivera"
[edge]
domain = "sam.example.test"
[paths]
home = "{HOME}"
root = "{CHECKOUT}"
vault = "{HOME}/vault"
workspaces = "{HOME}/ai-labs"
plugins = "{HOME}/plugins"
state = "{HOME}/.local/state/loam"
''')
os.environ["LOAM_CONFIG"] = str(CONFIG)
os.environ.pop("LOAM_AI_LABS", None)
os.environ.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.test",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.test",
                   "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"})
for agent, name in (("iris", "Iris"), ("wren", "Wren")):
    (HOME / "ai-labs" / agent).mkdir(parents=True)
    (HOME / "ai-labs" / agent / "agent.json").write_text(json.dumps({"id": agent, "name": name}))

sys.path.insert(0, str(ROOT / "lib"))
import loam_feedback as fb  # noqa: E402

FAILURES = 0


def check(label: str, got, want) -> None:
    global FAILURES  # noqa: PLW0603
    ok = got == want
    FAILURES += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}"
          + ("" if ok else f"\n      got  {got!r}\n      want {want!r}"))


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(CHECKOUT), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def report(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "bin" / "loam-report"), *args],
                          input=stdin, capture_output=True, text=True, timeout=60)


TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
RANDOM = "aB3dE5fG7hI9jK1lM3nO5pQ7rS9tU1vW"
COMMIT = "8b09cd3e0b1c7f8a8e2c0f9d2b2f9d5b0e8a8c11"

try:
    text = (f"Sam Rivera asked Wren to open {HOME}/vault/Journal/today.md and "
            f"/home/someone/x.txt; Iris saw it at https://app.sam.example.test/t?share=abc. "
            f"Mail sam@example.com from 203.0.113.9 (not 127.0.0.1). Key {TOKEN}, "
            f"password=hunter2hunter2, {RANDOM}. Commit {COMMIT}, "
            f"test_engineer_queue_wait_for_the_slot_now. See [[Therapy notes]]. "
            f"{USER} ran it.")
    clean, removed = fb.scrub(text)
    # The install's user is `loam` on an install made by install.sh: the
    # product's name, which a report keeps.
    for gone in ("Sam", "Rivera", "Wren",
                 *([f" {USER} ran"] if USER.lower() != "loam" else []),
                 "sam.example.test", "Journal/today",
                 "someone", "share=abc", "sam@example.com", "203.0.113.9", TOKEN,
                 "hunter2", RANDOM, "Therapy notes"):
        check(f"scrub takes out {gone!r}", gone in clean, False)
    for kept in ("Iris", "127.0.0.1", COMMIT, "test_engineer_queue_wait_for_the_slot_now",
                 "<vault>/<note>", "/home/<user>/x.txt", "https://<domain>/t?<query>"):
        check(f"scrub keeps {kept!r}", kept in clean, True)
    check("Loam's own vault folders stay, the note's name goes",
          fb.scrub(f"{HOME}/vault/Inbox/boards/Wedding plans.md and {HOME}/vault/System/Loam/README.md")[0],
          "<vault>/Inbox/boards/<note> and <vault>/System/Loam/README.md")
    check("what was taken out is counted by kind",
          {k: removed.get(k) for k in ("your name", "an agent's name", "a key or a token",
                                       "an e-mail address", "an IP address",
                                       "the name of a note")},
          {"your name": 1, "an agent's name": 1, "a key or a token": 3,
           "an e-mail address": 1, "an IP address": 1, "the name of a note": 2})

    # -- the draft and its link ----------------------------------------------------
    subprocess.run(["git", "init", "-q", "-b", "main", str(CHECKOUT)], check=True)
    (CHECKOUT / "manifest.json").write_text('{"version": "1.0.0"}\n')
    (CHECKOUT / "lib").mkdir()
    (CHECKOUT / "lib" / "x.py").write_text("A = 1\n")
    git("add", "-A")
    git("commit", "-qm", "Release 1.0.0")
    git("tag", "-a", "1.0.0", "-m", "Loam 1.0.0")

    d = fb.draft("bug", "Threads lose a message for Sam",
                 f"It happened in {HOME}/ai-labs/wren at 21:15.")
    check("the title is scrubbed", d["title"], "Threads lose a message for <name>")
    check("the body is scrubbed", d["body"].splitlines()[0],
          "It happened in <workspaces>/<agent> at 21:15.")
    check("the body says the release and the kind of install",
          "Loam 1.0.0 (stock)" in d["body"], True)
    url = urllib.parse.urlsplit(d["url"])
    query = urllib.parse.parse_qs(url.query)
    check("the link is the upstream repository's new-issue page",
          (url.netloc, url.path), ("github.com", "/loamhq/loam/issues/new"))
    check("it carries exactly what the owner is shown",
          (query["title"][0], query["body"][0], query["labels"][0]),
          (d["title"], d["body"], "bug"))
    check("a feature is labelled enhancement",
          fb.draft("feature", "Dark mode for boards", "Please.")["label"], "enhancement")
    long = fb.draft("bug", "Long", "word " * 3000)
    check("a body too long for a link is pasted instead",
          (long["fits"], "pasted below" in urllib.parse.unquote(long["url"])), (False, True))
    check("show() tells the agent to give the link on the owner's yes",
          "On the owner's yes" in fb.show(d) and d["url"] in fb.show(d), True)
    for bad in (("bug", "", "x"), ("bug", "x", " "), ("praise", "x", "y")):
        try:
            fb.draft(*bad)
            check(f"refused: {bad}", "no error", "ValueError")
        except ValueError:
            check(f"refused: {bad}", "ValueError", "ValueError")

    # -- a patch --------------------------------------------------------------------
    try:
        fb.draft("bug", "x", "y", patch=True)
        check("no local commit: nothing to send", "no error", "ValueError")
    except ValueError as exc:
        check("no local commit: nothing to send", "no local change" in str(exc), True)
    (CHECKOUT / "lib" / "x.py").write_text(f"A = 2  # fixed for Sam in {HOME}\n")
    git("commit", "-qam", "Fix A")
    p = fb.draft("bug", "A is wrong", "A was 1.", patch=True)
    check("the patch names the local commits", p["patch"], ["Fix A"])
    check("and carries the diff", "+A = 2" in p["body"] and "```diff" in p["body"], True)
    check("scrubbed like the rest", ("Sam" in p["body"], "# fixed for <name> in ~" in p["body"]),
          (False, True))

    # -- the command --------------------------------------------------------------
    out = report("bug", "--title", "Sam's boards are empty", stdin="Nothing on the board.\n")
    check("loam report bug: exit 0", out.returncode, 0)
    check("it shows the title as it would go", "Title: <name>'s boards are empty" in out.stdout,
          True)
    check("and the link", "https://github.com/loamhq/loam/issues/new?" in out.stdout, True)
    js = report("feature", "--title", "Calendar", "--body", "A calendar.", "--json")
    check("--json is the draft as data", json.loads(js.stdout)["label"], "enhancement")
    check("no title: exit 2", report("bug", "--body", "x").returncode, 2)
    idea = report("idea", "--title", "A calmer morning", "--body", "I wish it said less.",
                  "--json")
    d = json.loads(idea.stdout)
    check("an idea is a kind of its own, to GitHub like the others",
          (idea.returncode, d["kind"], d["label"],
           d["url"].startswith("https://github.com/loamhq/loam/issues/new?")),
          (0, "idea", "idea", True))
    check("and is shown as an idea", "To Loam's makers, as an idea:" in fb.show(d), True)
    check("there is no other way to send it: `send` is not a command",
          report("send", "0123456789ab").returncode, 2)
    sc = report("scrub", "--json", stdin="Wren at sam.example.test\n")
    check("loam report scrub", json.loads(sc.stdout)["text"], "<agent> at <domain>\n")
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{'FAILED' if FAILURES else 'OK'}: {FAILURES} failure(s)")
sys.exit(1 if FAILURES else 0)
