"""The write boundary, asserted against the hook itself.

Runs boundary_guard.py the way the runtime runs it -- a real subprocess fed
a real PreToolUse payload -- rather than importing its internals, because
what matters is the exit code Claude Code actually acts on. Exit 0 is
allow, exit 2 is refuse.

The cases that earn their place are the asymmetric ones. A move is checked
at BOTH ends, so moving *out of* a sibling workspace has to fail exactly as
moving into one does. And the Android character rule applies to the
destination only, so renaming a file that already carries an illegal
character has to SUCCEED -- that is the repair, and an earlier version of
this guard refused it.

Run: python3 tests/test_boundary_guard.py
"""

import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

# The guard beside this test, so a job's worktree tests its own copy.
GUARD = str(Path(__file__).resolve().parent.parent / "hooks" / "boundary_guard.py")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_config as lc  # noqa: E402
from loam_memory import LIMIT_CHARS  # noqa: E402

# The box's own roots, as the guard reads them from the install's config.
H = str(lc.HOME)
RUNTIME = str(lc.ROOT)
CWD = f"{lc.WORKSPACES}/iris"
W = f"{lc.WORKSPACES}/iris"
V = str(lc.VAULT)
L = str(lc.WORKSPACES)

CASES = [
    # (label, tool, input, expect_allowed)
    ("move within own workspace",   "mcp__loam__move_file", {"source": f"{W}/a.md", "destination": f"{W}/b.md"}, True),
    ("move within vault",           "mcp__loam__move_file", {"source": f"{V}/00_INBOX/a.md", "destination": f"{V}/10_NOTES/a.md"}, True),
    ("move vault -> own workspace", "mcp__loam__move_file", {"source": f"{V}/00_INBOX/a.md", "destination": f"{W}/a.md"}, True),
    ("move -> commons",             "mcp__loam__move_file", {"source": f"{W}/a.md", "destination": f"{L}/commons/a.md"}, True),
    ("move OUT of sibling",         "mcp__loam__move_file", {"source": f"{L}/ada/a.md", "destination": f"{W}/a.md"}, False),
    ("move INTO sibling",           "mcp__loam__move_file", {"source": f"{W}/a.md", "destination": f"{L}/ada/a.md"}, False),
    ("move INTO 15_CONVERSATIONS",  "mcp__loam__move_file", {"source": f"{W}/a.md", "destination": f"{V}/15_CONVERSATIONS/a.md"}, False),
    ("move OUT of 15_CONVERSATIONS","mcp__loam__move_file", {"source": f"{V}/15_CONVERSATIONS/a.md", "destination": f"{W}/a.md"}, False),
    ("move -> illegal dest name",   "mcp__loam__move_file", {"source": f"{W}/a.md", "destination": f'{W}/what: now.md'}, False),
    ("RENAME illegal -> legal",     "mcp__loam__move_file", {"source": f'{V}/00_INBOX/Jobs "Plan".md', "destination": f"{V}/00_INBOX/Jobs Plan.md"}, True),
    ("move traversal escape",       "mcp__loam__move_file", {"source": f"{W}/a.md", "destination": f"{W}/../../.ssh/authorized_keys"}, False),
    ("move src outside roots",      "mcp__loam__move_file", {"source": "/etc/hostname", "destination": f"{W}/a.md"}, False),
    ("delete outside roots",        "mcp__loam__delete_file", {"path": f"{RUNTIME}/wake.env"}, False),
    ("generate_pdf outside roots",  "mcp__composition__generate_pdf", {"target_path": f"{H}/x.pdf"}, False),
    ("Write outside roots",         "Write", {"file_path": f"{H}/.ssh/authorized_keys"}, False),
    # regressions on the pre-existing tools
    ("Write own workspace",         "Write", {"file_path": f"{W}/x.md"}, True),
    ("Write sibling",               "Write", {"file_path": f"{L}/loom/x.md"}, False),
    ("Write illegal name",          "Write", {"file_path": f'{V}/00_INBOX/a"b.md'}, False),
    ("Edit vault",                  "Edit",  {"file_path": f"{V}/00_INBOX/x.md"}, True),
    ("delete own workspace",        "mcp__loam__delete_file", {"path": f"{W}/x.md"}, True),
    ("delete sibling",              "mcp__loam__delete_file", {"path": f"{L}/ada/x.md"}, False),
    ("delete file w/ illegal name", "mcp__loam__delete_file", {"path": f'{V}/00_INBOX/Jobs "Plan".md'}, True),
    ("generate_pdf sibling",        "mcp__composition__generate_pdf", {"target_path": f"{L}/pax/x.pdf"}, False),
    ("unrelated tool ignored",      "Read", {"file_path": "/etc/passwd"}, True),
    # generate_image was extended on 2026-09-02 -- one tool now covering
    # from-scratch, edit, style-reference, combine and masked region edit.
    # A rebuilt MCP tool that writes files inherits no enforcement, so the
    # guard is re-asserted against the NEW argument shape rather than
    # assumed to still match. The output field is still target_path, which
    # is what keeps these cases meaningful: if a later edit renames it,
    # these fail loudly instead of the boundary going quiet.
    ("gen_image own workspace",     "mcp__images__generate_image", {"target_path": f"{W}/art/x.png"}, True),
    ("gen_image sibling",           "mcp__images__generate_image", {"target_path": f"{L}/ada/x.png"}, False),
    ("gen_image outside roots",     "mcp__images__generate_image", {"target_path": f"{H}/x.png"}, False),
    ("gen_image 15_CONVERSATIONS",  "mcp__images__generate_image", {"target_path": f"{V}/15_CONVERSATIONS/x.png"}, False),
    ("gen_image traversal escape",  "mcp__images__generate_image", {"target_path": f"{W}/../ada/x.png"}, False),
    ("gen_image illegal name",      "mcp__images__generate_image", {"target_path": f"{W}/cover: v2.png"}, False),
    # Sources and masks are READS, and reads are open across the vault and
    # all of ai-labs by design -- an agent using a sibling's cover art as a
    # style reference is the capability working, not a breach. The guard
    # must not start policing them because they appear in the same call.
    ("gen_image sibling as SOURCE", "mcp__images__generate_image", {"target_path": f"{W}/x.png", "source_images": [f"{L}/ada/cover.png"], "mask": f"{L}/ada/m.png"}, True),
    ("gen_image bad target ok src", "mcp__images__generate_image", {"target_path": f"{L}/ada/x.png", "source_images": [f"{W}/mine.png"]}, False),
    # The Images plugin takes a path relative to the vault (2026-11-18): it is
    # checked where it writes, not under the agent's working folder.
    ("gen_image vault-relative",    "mcp__images__generate_image", {"target_path": "Images/x.png"}, True),
    ("gen_image relative 15_CONV",  "mcp__images__generate_image", {"target_path": "15_CONVERSATIONS/x.png"}, False),
    ("gen_image no target at all",  "mcp__images__generate_image", {"prompt": "a fox"}, True),
    # A session started before Images left composition still has the old
    # tool; the guard keeps checking it.
    ("old gen_image sibling",       "mcp__composition__generate_image", {"target_path": f"{L}/ada/x.png"}, False),
    # delete_file was extended on 2026-09-03 to remove EMPTY directories.
    # The argument shape did not change -- still one `path` -- but the kind
    # of thing that path names did, and a changed write tool gets its
    # boundary re-asserted rather than assumed to carry over. A directory
    # target has to hit exactly the same walls a file target does; the
    # emptiness rule is the server's alone, and lives below this layer.
    ("delete DIR own workspace",    "mcp__loam__delete_file", {"path": f"{W}/outbox/juno"}, True),
    ("delete DIR sibling",          "mcp__loam__delete_file", {"path": f"{L}/ada/outbox/juno"}, False),
    ("delete DIR 15_CONVERSATIONS", "mcp__loam__delete_file", {"path": f"{V}/15_CONVERSATIONS/2026"}, False),
    ("delete DIR outside roots",    "mcp__loam__delete_file", {"path": f"{RUNTIME}/state"}, False),
    ("delete DIR traversal escape", "mcp__loam__delete_file", {"path": f"{W}/../ada/outbox"}, False),
    ("delete DIR in commons",       "mcp__loam__delete_file", {"path": f"{L}/commons/scratch"}, True),
    ("delete DIR trailing slash",   "mcp__loam__delete_file", {"path": f"{L}/ada/outbox/"}, False),
    # Codex's apply_patch (2026-10-01): one body, paths relative to the cwd,
    # unpacked into the calls above. Every hunk is held to the same rule.
    ("codex add own workspace",     "apply_patch", {"command": "*** Begin Patch\n*** Add File: notes/x.md\n+hi\n*** End Patch"}, True),
    ("codex add vault",             "apply_patch", {"command": f"*** Begin Patch\n*** Add File: {V}/00_INBOX/x.md\n+hi\n*** End Patch"}, True),
    ("codex add 15_CONVERSATIONS",  "apply_patch", {"command": f"*** Begin Patch\n*** Add File: {V}/15_CONVERSATIONS/x.md\n+hi\n*** End Patch"}, False),
    ("codex add sibling",           "apply_patch", {"command": "*** Begin Patch\n*** Add File: ../ada/x.md\n+hi\n*** End Patch"}, False),
    ("codex second file sibling",   "apply_patch", {"command": "*** Begin Patch\n*** Add File: ok.md\n+hi\n*** Delete File: ../ada/soul.md\n*** End Patch"}, False),
    ("codex move INTO sibling",     "apply_patch", {"command": "*** Begin Patch\n*** Update File: a.md\n*** Move to: ../ada/a.md\n@@\n-x\n+y\n*** End Patch"}, False),
    ("codex patch CLAUDE.md",       "apply_patch", {"command": "*** Begin Patch\n*** Update File: CLAUDE.md\n@@\n-a\n+b\n*** End Patch"}, False),
    ("codex patch .codex/",         "apply_patch", {"command": "*** Begin Patch\n*** Add File: .codex/config.toml\n+x\n*** End Patch"}, False),
    ("codex patch as argv",         "apply_patch", {"command": ["apply_patch", f"*** Begin Patch\n*** Add File: {V}/15_CONVERSATIONS/x.md\n+hi\n*** End Patch"]}, False),
    ("Write agent.json",            "Write", {"file_path": f"{W}/agent.json"}, False),
]

# Nor an engineer job's scope: run from inside a job, the guard would hold
# every case to that job's worktree, which is a different rule.
ENV = {k: v for k, v in os.environ.items()
       if k != "CLAUDE_PROJECT_DIR" and not k.startswith("LOAM_JOB_")}


def run_case(tool, inp, cwd):
    payload = {"tool_name": tool, "tool_input": inp, "cwd": cwd}
    r = subprocess.run([sys.executable, GUARD], input=json.dumps(payload),
                       capture_output=True, text=True, env=ENV)
    return r.returncode == 0, (r.stderr or "").strip()


fails = 0
for label, tool, inp, expect in CASES:
    allowed, err = run_case(tool, inp, CWD)
    ok = allowed == expect
    fails += not ok
    mark = "PASS" if ok else "FAIL"
    detail = "" if allowed else "  | " + err.replace("\n", " ")[:95]
    print(f"{mark}  {label:34} allowed={allowed}{detail}")


# ---------------------------------------------------------------------------
# The size limit on auto-loaded files, and the `.claude/` refusal.
#
# These need a workspace whose files the test may actually write, so they run
# against a throwaway agent rather than a live one -- but still through the
# hook as a subprocess, on real files on disk, because what is being asserted
# is the exit code the runtime acts on. The guard derives ai-labs as the
# workspace's PARENT and the vault from agent.json, so a fake tree is a
# complete world as far as it is concerned.
#
# Every branch is asserted from both sides. The one that matters most is the
# shrink exemption: an agent handed an over-limit file must always be able to
# make it smaller, or the limit locks it out of the only repair.
# ---------------------------------------------------------------------------
print()
root = Path(tempfile.mkdtemp(prefix="loam-guard-test-"))
try:
    labs = root / "ai-labs"
    vault = root / "vault"
    ws = labs / "testagent"
    (ws / ".claude" / "output-styles").mkdir(parents=True)
    (ws / "crons").mkdir()
    (vault / "00_INBOX").mkdir(parents=True)
    (ws / "agent.json").write_text(json.dumps({
        "name": "testagent", "display": "Testagent",
        "workspace": str(ws), "vault": str(vault),
    }))

    small = "x" * 100
    big = "y" * (LIMIT_CHARS + 500)
    bigger = "z" * (LIMIT_CHARS + 5000)

    (ws / "_always.md").write_text(small)
    (ws / "_reflection.md").write_text(bigger)          # already over
    (ws / "notes.md").write_text(small)
    (ws / "over-source.md").write_text(big)
    (ws / "_active.md").write_text(
        "---\nname: conversation\ndescription: d\n"
        "keep-coding-instructions: true\n---\n\nbody line\n"
    )
    link = ws / ".claude" / "output-styles" / "conversation.md"
    link.symlink_to("../../_active.md")
    # Skills as provision lays them out: `.claude/skills -> ../skills`. Two
    # links inside `skills/` that must not become a way through the first:
    # one out of the workspace to a place otherwise writable, one back into
    # `.claude/`.
    (ws / "skills" / "x").mkdir(parents=True)
    (ws / "skills" / "x" / "SKILL.md").write_text(small)
    (ws / ".claude" / "skills").symlink_to("../skills")
    (ws / "skills" / "out").symlink_to(vault / "00_INBOX")
    (ws / "skills" / "back").symlink_to("../.claude")
    (ws / "skills" / "up").symlink_to("../notes.md")
    CS = f"{ws}/.claude/skills"

    A = f"{ws}/_always.md"
    R = f"{ws}/_reflection.md"
    AC = f"{ws}/_active.md"

    LIMIT_CASES = [
        # (label, tool, input, expect_allowed)
        ("write under limit -> _always",   "Write",
         {"file_path": A, "content": small * 2}, True),
        ("write OVER limit -> _always",    "Write",
         {"file_path": A, "content": big}, False),
        ("write over limit -> notes.md",   "Write",
         {"file_path": f"{ws}/notes.md", "content": bigger}, True),
        ("write OVER limit -> crons lane", "Write",
         {"file_path": f"{ws}/crons/nightly.md", "content": big}, False),
        # The shrink exemption, both sides. _reflection.md is already over.
        ("SHRINK an over-limit file",      "Write",
         {"file_path": R, "content": big}, True),
        ("grow an over-limit file",        "Write",
         {"file_path": R, "content": bigger + "more"}, False),
        ("rewrite over-limit file as-is",  "Write",
         {"file_path": R, "content": bigger}, False),
        ("edit that shrinks below limit",  "Edit",
         {"file_path": R, "old_string": "z" * (LIMIT_CHARS + 4000),
          "new_string": "z"}, True),
        ("edit that grows past limit",     "Edit",
         {"file_path": A, "old_string": "x" * 100, "new_string": big}, False),
        ("edit that stays under limit",    "Edit",
         {"file_path": A, "old_string": "x" * 100, "new_string": "ok"}, True),
        # move_file: destination is checked, source deliberately is not --
        # moving an over-limit file AWAY is the index/body split repair.
        ("move over-limit file -> lane",   "mcp__loam__move_file",
         {"source": f"{ws}/over-source.md", "destination": A}, False),
        ("move over-limit file AWAY",      "mcp__loam__move_file",
         {"source": R, "destination": f"{ws}/_body.md"}, True),
        # The style frontmatter _active.md cannot lose.
        ("edit below the frontmatter",     "Edit",
         {"file_path": AC, "old_string": "body line", "new_string": "new body"}, True),
        ("edit that drops frontmatter",    "Edit",
         {"file_path": AC, "old_string": "name: conversation\n", "new_string": ""}, False),
        ("edit that renames the style",    "Edit",
         {"file_path": AC, "old_string": "name: conversation",
          "new_string": "name: active"}, False),
        ("write that drops frontmatter",   "Write",
         {"file_path": AC, "content": "just a body\n"}, False),
        # .claude/ is the runtime's. The deny rule covers Edit; move_file was
        # gated by neither it nor this hook until 2026-09-04.
        ("move INTO .claude",              "mcp__loam__move_file",
         {"source": f"{ws}/notes.md",
          "destination": f"{ws}/.claude/settings.json"}, False),
        ("move onto the style SYMLINK",    "mcp__loam__move_file",
         {"source": f"{ws}/notes.md", "destination": str(link)}, False),
        ("write onto the style symlink",   "Write",
         {"file_path": str(link), "content": "hi"}, False),
        ("move into a SIBLING .claude",    "mcp__loam__move_file",
         {"source": f"{ws}/notes.md",
          "destination": f"{labs}/other/.claude/settings.json"}, False),
        # Skills are the agent's own at the path the CLI shows them, and the
        # link is the only thing excused: where it lands decides (2026-10-13).
        ("edit skill via .claude/skills",  "Edit",
         {"file_path": f"{CS}/x/SKILL.md", "old_string": "x", "new_string": "y"}, True),
        ("write new skill file via link",  "Write",
         {"file_path": f"{CS}/x/notes.md", "content": small}, True),
        ("edit skill at its real path",    "Edit",
         {"file_path": f"{ws}/skills/x/SKILL.md", "old_string": "x", "new_string": "y"}, True),
        ("edit .claude/settings.json",     "Edit",
         {"file_path": f"{ws}/.claude/settings.json", "old_string": "a", "new_string": "b"}, False),
        ("write onto the skills LINK",     "Write",
         {"file_path": CS, "content": "hi"}, False),
        ("dot-dot out of the skills link", "Write",
         {"file_path": f"{CS}/../settings.json", "content": "{}"}, False),
        ("link in skills/ OUT of ws",      "Write",
         {"file_path": f"{CS}/out/note.md", "content": small}, False),
        ("link in skills/ BACK to .claude", "Write",
         {"file_path": f"{CS}/back/settings.json", "content": "{}"}, False),
        ("link in skills/ to ws root",     "Write",
         {"file_path": f"{CS}/up", "content": small}, False),
        ("move INTO .claude via skills",   "mcp__loam__move_file",
         {"source": f"{ws}/notes.md", "destination": f"{CS}/back/x.json"}, False),
        ("ordinary write is untouched",    "Write",
         {"file_path": f"{ws}/scratch.md", "content": small}, True),
    ]

    for label, tool, inp, expect in LIMIT_CASES:
        allowed, err = run_case(tool, inp, str(ws))
        ok = allowed == expect
        fails += not ok
        mark = "PASS" if ok else "FAIL"
        detail = "" if allowed else "  | " + err.replace("\n", " ")[:95]
        print(f"{mark}  {label:34} allowed={allowed}{detail}")
finally:
    shutil.rmtree(root, ignore_errors=True)

# ---------------------------------------------------------------------------
# The engineer's rule, against the LIVE engineer project (its agent.json says
# `kind: engineer`, which is what selects the branch). The operator's file of
# public apps (Move 10) is outside the engineer's three roots, so the guard
# refuses it before the settings deny ever gets a say; both are asserted to
# hold, this one here and the deny by provision's render.
# ---------------------------------------------------------------------------
print()
ENGINEER = f"{RUNTIME}/engineer"
if os.path.isfile(f"{ENGINEER}/agent.json"):
    ENGINEER_CASES = [
        ("engineer: Write /etc/loam/public",  "Write", {"file_path": "/etc/loam/public", "content": "x\n"}, False),
        ("engineer: Edit /etc/loam/public",   "Edit",  {"file_path": "/etc/loam/public", "old_string": "a", "new_string": "b"}, False),
        ("engineer: Write under agents/",     "Write", {"file_path": f"{RUNTIME}/deploy/x/deploy.toml", "content": "x\n"}, True),
        ("engineer: Edit a soul",             "Edit",  {"file_path": f"{L}/ada/soul.md", "old_string": "a", "new_string": "b"}, False),
    ]
    for label, tool, inp, expect in ENGINEER_CASES:
        allowed, err = run_case(tool, inp, ENGINEER)
        ok = allowed == expect
        fails += not ok
        mark = "PASS" if ok else "FAIL"
        detail = "" if allowed else "  | " + err.replace("\n", " ")[:95]
        print(f"{mark}  {label:34} allowed={allowed}{detail}")

print()
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
