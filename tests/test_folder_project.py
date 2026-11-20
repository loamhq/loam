#!/usr/bin/env python3
"""Check app-scoped jobs rooted in a subdirectory of the platform repository, including
their landing boundaries.

Run: python3 tests/test_folder_project.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

TMP = Path(tempfile.mkdtemp(prefix="loam-folder-project-"))
HOME = TMP / "home"
CHECKOUT = HOME / "loam"
CHECKOUT.mkdir(parents=True)
(TMP / "loam.toml").write_text(f"""
user = "{os.environ.get('USER', 'loam')}"
[owner]
id = "owner"
name = "Sam"
[paths]
home = "{HOME}"
root = "{CHECKOUT}"
""")
os.environ["LOAM_CONFIG"] = str(TMP / "loam.toml")

import loam_engineer as eng  # noqa: E402
import loam_worktree as wt  # noqa: E402

results: list[bool] = []


def check(label: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label}"
          + ("" if ok else f"\n      got  {got!r}\n      want {want!r}"))


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def project(**record) -> eng.Project:
    rec = {"name": "loam-ui", "checkout": str(CHECKOUT), **record}
    return eng.Project("loam-ui", CHECKOUT, TMP / "jobs", platform=False, record=rec)


# -- the record ---------------------------------------------------------------------------

app = project(subdir="app")
check("`subdir` names the folder",
      (app.subdir, project(subdir="/app/").subdir, project().subdir), ("app", "app", None))
check("...a path that climbs out of the checkout is no folder",
      (project(subdir="../etc").subdir, project(subdir="app/../..").subdir), (None, None))
check("...and the platform never has one",
      eng.Project("Loam", CHECKOUT / "engineer", TMP / "j", platform=True,
                  record={"subdir": "app"}).subdir, None)
check("a folder of Loam's checkout lands under Loam's live lock; any other project under its own",
      (app.live_lock, project().live_lock,
       eng.Project("x", TMP / "elsewhere", TMP / "j", platform=False,
                   record={"subdir": "app"}).live_lock), ("Loam", "loam-ui", "x"))

# -- the tree -----------------------------------------------------------------------------

tree = TMP / "tree"
check("the session runs in the folder of the tree", wt.session_dir(app, tree), tree / "app")
check("...its memory is the folder's, tracked by the repository",
      wt.memory_rel(app), "app/memory/MEMORY.md")
check("...its harness is the folder's",
      wt.harness_names(app), tuple(f"app/{n}" for n in wt.HARNESS))
check("...and a whole-repository project is as it was",
      (wt.session_dir(project(), tree), wt.memory_rel(project()), wt.harness_names(project())),
      (tree, "memory/MEMORY.md", wt.HARNESS))

# -- the landing's scope ------------------------------------------------------------------

repo = TMP / "repo"
subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
for rel, text in {"app/src/server.js": "a\n", "bin/deploy": "b\n", "README.md": "r\n"}.items():
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(text)
git(repo, "add", "-A")
git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
base = git(repo, "rev-parse", "HEAD")


def branch_with(files: dict[str, str]) -> str:
    git(repo, "checkout", "-q", "-B", "job", base)
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "work")
    return git(repo, "rev-parse", "HEAD")


tip = branch_with({"app/src/server.js": "changed\n", "app/test/new.test.js": "t\n"})
check("a branch that changes only the folder is in scope",
      wt.outside_scope(app, repo, base, tip), [])
tip = branch_with({"app/src/server.js": "changed\n", "bin/deploy": "sudo anything\n",
                   "approot.txt": "x\n"})
check("...one that touches the runtime is not, and the paths are named",
      sorted(wt.outside_scope(app, repo, base, tip)), ["approot.txt", "bin/deploy"])
check("...a whole-repository project has no scope to keep",
      wt.outside_scope(project(), repo, base, tip), [])
git(repo, "checkout", "-q", "main")
check("the refusal has its own reason and its own brief",
      ("scope" in wt.REASONS,
       "outside the folder" in wt.continuation_brief(
           app, "j1", {"branch": "job/j1", "tree": str(tree), "reason": "scope",
                       "files": ["bin/deploy"]})), (True, True))

# -- the launcher and provisioning -------------------------------------------------------

source = (ROOT / "bin" / "engineer-job").read_text(encoding="utf-8")
check("the launcher asks no deploy key of a folder project",
      "not lands_by_pr(project) and not project.subdir and (" in source, True)
check("...and reads its composition from the folder",
      "assert_imports_resolve(project.root / project.subdir if project.subdir else project.root)" in source, True)

loader = importlib.machinery.SourceFileLoader("provision_mod", str(ROOT / "bin" / "provision"))
spec = importlib.util.spec_from_loader(loader.name, loader)
prov = importlib.util.module_from_spec(spec)
loader.exec_module(prov)
prov.exclude_harness(repo, "app")
lines = (repo / ".git" / "info" / "exclude").read_text().splitlines()
check("provisioning excludes the folder's harness, never its memory",
      ([ln for ln in lines if ln.startswith("/app/")], "/app/memory/" in lines),
      (["/app/CLAUDE.md", "/app/role.md", "/app/project.md", "/app/.claude/"], False))
check("...and a tracked harness is asked of the folder",
      prov.harness_tracked(repo, "app"), False)

print(f"\n{'OK' if all(results) else 'FAILED'}: {results.count(False)} failure(s)")
sys.exit(0 if all(results) else 1)
