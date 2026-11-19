#!/usr/bin/env python3
"""What a box set up by hand keeps outside the checkout (first release L16).

  - `deploy.config` in the install's loam.toml names the deployment
    configuration (domain, `[moved]`, `[[forward]]`); without it the
    checkout's `deploy/loam.toml` is read, as before;
  - `[app]` in loam.toml gives the app its agents' voices and avatar colours
    through bin/deploy's environment, and the compose file falls back to its
    generic defaults without it;
  - Loam's own engineer's harness (`engineer/CLAUDE.md`, role, project file,
    agent.json, .claude/) is seeded into a job's tree when the checkout
    carries it untracked, and never when it is tracked.

Each case runs in a child process with LOAM_CONFIG at a scratch file, or
against a scratch git repository. Nothing on the box is read or written.

Run: python3 tests/test_install_own_config.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
results: list[bool] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:62}{'  | ' + str(detail)[:90] if detail else ''}")


TMP = Path(tempfile.mkdtemp(prefix="loam-own-config-test-"))


def child(config: str, code: str) -> str:
    cfg = TMP / f"loam-{abs(hash(config))}.toml"
    cfg.write_text(config, encoding="utf-8")
    env = {**os.environ, "LOAM_CONFIG": str(cfg), "PYTHONDONTWRITEBYTECODE": "1"}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                         text=True, cwd=str(ROOT), check=False)
    if out.returncode != 0:
        return "ERROR " + out.stderr.strip()[-300:]
    return out.stdout.strip()


PRELUDE = f"import sys; sys.path.insert(0, {str(ROOT / 'lib')!r}); "

# -- deploy.config -----------------------------------------------------------
own = TMP / "deploy.toml"
own.write_text('domain = "own.example.test"\nauth_host = "auth"\n'
               '[moved]\nfrom = "old.example.test"\nuntil = 2026-12-15\nhosts = ["auth"]\n',
               encoding="utf-8")
got = child(f'[deploy]\nconfig = "{own}"\n',
            PRELUDE + "import loam_deploy as d; print(d.CONFIG_PATH); print(d.domain()); "
            "print(d.moved()['from'])")
check("deploy.config names the deployment configuration",
      got.splitlines() == [str(own), "own.example.test", "old.example.test"], got)
got = child("", PRELUDE + "import loam_deploy as d; print(d.CONFIG_PATH)")
check("without it, the checkout's deploy/loam.toml",
      got == str(ROOT / "deploy" / "loam.toml"), got)

# -- [app] -------------------------------------------------------------------
LOAD_DEPLOY = (PRELUDE + "import importlib.machinery as m, importlib.util as u; "
               f"l = m.SourceFileLoader('deploy_cmd', {str(ROOT / 'bin' / 'deploy')!r}); "
               "s = u.spec_from_loader('deploy_cmd', l); c = u.module_from_spec(s); l.exec_module(c); "
               "import json; print(json.dumps(c.app_env(), sort_keys=True))")
got = child('[app]\nspeech_voices = "a=Aoede:warm;b=Charon:calm"\n'
            '[app.avatar_colours]\nb = "c54"\n', LOAD_DEPLOY)
try:
    env = json.loads(got)
except ValueError:
    env = {}
check("[app] reaches the compose environment: the colours, and no voices (Read aloud's own since 0.2.12)",
      env == {"LOAM_AVATAR_COLOURS": '{"b":"c54"}'}, got)
got = child("", LOAD_DEPLOY)
check("without [app], nothing is passed", got == "{}", got)
source = (ROOT / "bin" / "deploy").read_text(encoding="utf-8")
body = source[source.index("def service_env("):source.index("def env_file_text(")]
check("the app's environment hands it no voices",
      "SPEECH_VOICES" not in body)
check("...and the colours, none by default",
      '"AVATAR_COLOURS": own.get("LOAM_AVATAR_COLOURS", "")' in body)

# -- the platform engineer's harness -----------------------------------------
repo = TMP / "repo"
(repo / "engineer" / "memory").mkdir(parents=True)
(repo / "engineer" / "memory" / "MEMORY.md").write_text("# memory\n")
(repo / "README.md").write_text("x\n")


def git(where: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(where), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


git(repo, "init", "-q", "-b", "main")
git(repo, "add", "-A")
git(repo, "commit", "-q", "-m", "root")
for name in ("CLAUDE.md", "role.md", "project.md", "agent.json"):
    (repo / "engineer" / name).write_text(f"{name}\n")
(repo / "engineer" / ".claude").mkdir()
(repo / "engineer" / ".claude" / "settings.json").write_text("{}\n")
tree = TMP / "tree"
git(TMP, "clone", "-q", str(repo), str(tree))
got = child("", PRELUDE + "import loam_engineer as e, loam_worktree as w; from pathlib import Path; "
            f"p = e.Project('Loam', Path({str(repo / 'engineer')!r}), Path({str(TMP)!r}), platform=True); "
            f"print(','.join(w.seed_harness(p, {{'path': {str(tree)!r}}})))")
check("an untracked engineer harness is seeded into the tree",
      got == "engineer/CLAUDE.md,engineer/role.md,engineer/project.md,engineer/agent.json,engineer/.claude"
      and (tree / "engineer" / ".claude" / "settings.json").is_file()
      and not (tree / "engineer" / "memory" / "MEMORY.md").read_text() != "# memory\n", got)
git(repo, "add", "-A")
git(repo, "commit", "-q", "-m", "track the harness")
tree2 = TMP / "tree2"
git(TMP, "clone", "-q", str(repo), str(tree2))
got = child("", PRELUDE + "import loam_engineer as e, loam_worktree as w; from pathlib import Path; "
            f"p = e.Project('Loam', Path({str(repo / 'engineer')!r}), Path({str(TMP)!r}), platform=True); "
            f"print(repr(w.seed_harness(p, {{'path': {str(tree2)!r}}})))")
check("a tracked harness is left to the clone", got == "[]", got)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
