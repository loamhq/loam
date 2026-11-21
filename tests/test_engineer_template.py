"""The project-engineer template: the craft render and the registry.

The craft file is rendered from the LIVE role.md in the vault, because the
property under test is not "the renderer works on a fixture" but "today's
role.md renders into something a stranger's clone may hold". If role.md
changes in a way the substitution table does not cover, this fails here
before it fails in a provision run.

Run: python3 tests/test_engineer_template.py
"""

import importlib.machinery
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_engineer as eng  # noqa: E402

loader = importlib.machinery.SourceFileLoader(
    "provision", str(Path(__file__).resolve().parent.parent / "bin" / "provision"))
spec = importlib.util.spec_from_loader("provision", loader)
prov = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prov)

# Use the shared role that the development substitution tables describe.
_shipped = prov.ENGINEER_SHIPPED / "role.md"
if not prov.ENGINEER_ROLE_SRC.is_file() or (_shipped.is_file() and prov.ENGINEER_ROLE_SRC.read_text(
        encoding="utf-8") == _shipped.read_text(encoding="utf-8")):
    print(f"SKIP  no shared engineer role a project engineer renders from "
          f"({prov.ENGINEER_ROLE_SRC})")
    sys.exit(0)

fails = 0


def check(label: str, got, want) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:52} {got!r}"
          + ("" if ok else f"   want {want!r}"))


source = prov.ENGINEER_ROLE_SRC.read_text(encoding="utf-8")
craft = prov.render_craft(source, "abc1234", "2026-09-07")
low = craft.lower()

# What stays.
for phrase in (
    "**Your project is self-contained.**",
    "**Your memory lives in your working copy**",
    "## How you work",
    "**A brief in, a receipt out.**",
    "**Verify by causing, not by reading.**",
    "**Anything generated has two homes.**",
    "**Git and GitHub are yours inside your repo.**",
    "## What you never do",
    "**Expose anything publicly without the project owner's direct yes.**",
    "**Write a credential into an image, a repo, or the project's documentation.**",
    "**Retire a capability as a side effect.**",
    "**Your repo is your whole world.**",
    "revision `abc1234`, on 2026-09-07",
):
    check(f"keeps {phrase[:40]!r}", phrase in craft, True)

# What goes.
for phrase in (
    "Author an agent's self", f"Reach {prov.lc.OWNER_NAME} directly", "Run unattended",
    "15_CONVERSATIONS", "Start a wake or cron", "You have no vault",
    "You are not a persona", "Drafted 2026-09-05",
):
    check(f"drops {phrase!r}", phrase in craft, False)

for word in prov.CRAFT_FORBIDDEN:
    check(f"no {word!r} anywhere", word.lower() in low, False)
check("no 'his' or 'him' for the owner",
      any(f" {w} " in low for w in ("his", "him")), False)
check("owner is 'the project owner'", "the project owner" in craft, True)
check("mark reads back", prov.craft_mark_of(craft), ("abc1234", "2026-09-07"))

# The forbidden-word check is what makes the render safe to run unread.
try:
    prov.render_craft(source + "\n\n## Extra\n\nA line that names Loam.\n",
                      "abc1234", "2026-09-07")
    check("a new Loam line refuses the render", "raised", "CraftError")
except prov.CraftError as exc:
    check("a new Loam line refuses the render", "names Loam" in str(exc)
          or "Loam" in str(exc), True)

# Describe project memory consistently with whether the repository tracks its harness.
check("untracked: never says committed", "committed with the code" in craft, False)
check("untracked: memory not promised to cloners",
      "your own memory of the project" in craft, False)
check("untracked: says excluded", "excluded from the repository" in craft, True)
tracked_craft = prov.render_craft(source, "abc1234", "2026-09-07", harness_tracked=True)
check("tracked: keeps the committed sentence",
      "**Your memory lives in the repo**, committed with the code" in tracked_craft, True)
check("tracked: no exclusion sentence",
      "excluded from the repository" in tracked_craft, False)
try:
    prov.render_craft(source.replace("committed with the code", "kept with the code"),
                      "abc1234", "2026-09-07")
    check("a reworded memory sentence refuses the render", "rendered", "CraftError")
except prov.CraftError as exc:
    check("a reworded memory sentence refuses the render",
          "CRAFT_UNTRACKED_SUBSTITUTIONS" in str(exc), True)

# The exclude and the first commit, on a real git checkout.
import subprocess  # noqa: E402
with tempfile.TemporaryDirectory() as tmp:
    co = Path(tmp)
    subprocess.run(["git", "init", "-q", str(co)], check=True)
    exclude = co / ".git" / "info" / "exclude"
    check("exclude written", prov.exclude_harness(co), True)
    check("exclude is idempotent", prov.exclude_harness(co), False)
    lines = exclude.read_text().splitlines()
    for path in prov.HARNESS_PATHS:
        check(f"exclude has /{path}", f"/{path}" in lines, True)
    check("git's own comments kept", lines[0].startswith("# git ls-files"), True)
    check("header once", lines.count(prov.EXCLUDE_HEADER), 1)
    for rel, content in prov.project_render("probe", None, "abc1234", "2026-09-07").items():
        (co / rel).parent.mkdir(parents=True, exist_ok=True)
        (co / rel).write_text(content)
    (co / "README.md").write_text(prov.readme_stub("probe"))
    subprocess.run(["git", "-C", str(co), "add", "-A"], check=True)
    staged = subprocess.run(["git", "-C", str(co), "diff", "--cached", "--name-only"],
                            capture_output=True, text=True, check=True).stdout.split()
    check("git add -A stages the README alone", staged, ["README.md"])
    check("README stub is the name only", prov.readme_stub("probe"), "# probe\n")
    check("untracked checkout reads untracked", prov.harness_tracked(co), False)
    subprocess.run(["git", "-C", str(co), "add", "-f", "role.md"], check=True)
    check("a tracked role.md reads tracked", prov.harness_tracked(co), True)

# The whole render, and that no file in it names the box.
files = prov.project_render("probe", None, "abc1234", "2026-09-07")
check("renders five files", sorted(files),
      sorted([".claude/settings.json", "CLAUDE.md", "memory/MEMORY.md",
              "project.md", "role.md"]))
check("CLAUDE.md is two imports", files["CLAUDE.md"], "@role.md\n\n@project.md\n")
check("project.md carries the name", "# Project file: probe" in files["project.md"], True)
settings = json.loads(files[".claude/settings.json"])
check("no additionalDirectories", "additionalDirectories" in settings["permissions"], False)
check("no hooks", "hooks" in settings, False)
check("no mcp", settings.get("enableAllProjectMcpServers"), False)
check("Bash allowed", "Bash" in settings["permissions"]["allow"], True)
everything = "\n".join(files.values()).lower()
for word in ("loam", "persona", "vault", prov.lc.OWNER_ID.lower(), prov.lc.USER.lower(), "/home/"):
    check(f"rendered repo never says {word!r}", word in everything, False)

# The registry.
root = Path(tempfile.mkdtemp(prefix="loam-projects-test-"))
saved = eng.PROJECTS_DIR
try:
    eng.PROJECTS_DIR = root
    check("platform engineer alone", sorted(eng.engineers()), ["Loam"])
    (root / "probe.json").write_text(json.dumps({
        "name": "probe", "repo": "o/probe", "remote": "git@github.com:o/probe.git",
        "checkout": str(root / "checkout"), "deploy_key": str(root / "key"),
    }))
    (root / "half.json").write_text("{")
    known = eng.projects()
    check("registered project appears", sorted(eng.engineers()), ["Loam", "probe"])
    check("half-written record is skipped", "half" in known, False)
    probe = known["probe"]
    check("project lock is its own", probe.lock_name, "engineer-probe")
    check("platform lock unchanged", known["Loam"].lock_name, "engineer")
    check("jobs live outside the checkout",
          probe.jobs.is_relative_to(probe.root), False)
    check("resolves by name", eng.resolve_project("probe").name, "probe")
    check("resolves by path (older servers)",
          eng.resolve_project(str(eng.ENGINEER)).name, "Loam")
    check("unknown is None", eng.resolve_project("nope"), None)
    check("job dir under the project's jobs",
          eng.job_dir(probe, "20260907-000000-abcd").parent, probe.jobs)
    check("git_heads for a project is its tree only",
          list(eng.git_heads(probe)), ["probe"])
finally:
    eng.PROJECTS_DIR = saved
    import shutil
    shutil.rmtree(root, ignore_errors=True)

# New records go to state, while old records continue to resolve from the checkout.
with tempfile.TemporaryDirectory() as tmp:
    local = Path(tmp)
    saved = eng.PROJECTS_DIR, eng.PROJECT_STATE, eng.LEGACY_PROJECTS_DIR
    try:
        eng.PROJECT_STATE = local / "state"
        eng.PROJECTS_DIR = eng.PROJECT_STATE / "registry"
        eng.LEGACY_PROJECTS_DIR = local / "legacy"
        legacy = eng.LEGACY_PROJECTS_DIR / "old.json"
        prov.write_record(legacy, {"name": "old", "checkout": str(local)})
        fresh = eng.project_record_path("new")
        prov.write_record(fresh, {"name": "new", "checkout": str(local)})
        check("new records are in state", fresh, eng.PROJECT_STATE / "registry/new.json")
        check("existing records keep their path", eng.project_record_path("old"), legacy)
        check("both registries are read", sorted(eng.engineers()), ["Loam", "new", "old"])
    finally:
        eng.PROJECTS_DIR, eng.PROJECT_STATE, eng.LEGACY_PROJECTS_DIR = saved

# Refuse retirement while a deployment record still exists.
with tempfile.TemporaryDirectory() as tmp:
    deploy_dir = Path(tmp)
    check("no record: retire may proceed",
          prov.deployment_blocker("probe", deploy_dir), None)
    (deploy_dir / "probe").mkdir()
    (deploy_dir / "probe" / "deploy.toml").write_text('exposure = "private"\n')
    blocker = prov.deployment_blocker("probe", deploy_dir) or ""
    check("record present: retire refused", blocker != "", True)
    check("the refusal names the record",
          str(deploy_dir / "probe" / "deploy.toml") in blocker, True)
    check("the refusal says deploy goes first",
          "gen-deploy" in blocker and "then retire" in blocker, True)
    check("--force is not an override", "--force does not skip" in blocker, True)

print()
print("FAILED" if fails else "OK", f"({fails} failure(s))" if fails else "")
sys.exit(1 if fails else 0)
