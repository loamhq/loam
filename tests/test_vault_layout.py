#!/usr/bin/env python3
"""Exercise the configured vault folder names across the runtime paths that read and
write them.

Run: python3 tests/test_vault_layout.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="test-vault-layout-"))
FAILURES = 0

FIRST_INSTALL = """\
user = "nobody"
[vault]
inbox = "00_INBOX"
failure_log = "00_INBOX/from-agents/loam-wake-failures.md"
conversations = "15_CONVERSATIONS"
projects = "40_PROJECTS"
attachments = "98_ATTACHMENTS"
system = "99_SYSTEM"
"""

AN_INSTALL = 'user = "nobody"\n'

PROBE = r"""
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1] + "/lib")
import importlib.machinery, importlib.util
import loam_config as lc, loam_inbox, loam_files, loam_activity, loam_engineer as eng
vault = Path(sys.argv[2])
loam_inbox.record_failure("ada", vault, "the probe's failure")
loader = importlib.machinery.SourceFileLoader("sweep_inbox", sys.argv[1] + "/bin/sweep-inbox")
spec = importlib.util.spec_from_loader(loader.name, loader)
sweep = importlib.util.module_from_spec(spec); loader.exec_module(sweep)
proj = eng.Project("widget", vault, vault, platform=False, record={})
print(json.dumps({
    "failure_log": loam_inbox.FAILURE_LOG,
    "activity_log": str(loam_activity.FAILURE_LOG.relative_to(lc.VAULT)),
    "receipts": str(proj.receipts_dir(vault).relative_to(vault)),
    "pinned": list(loam_files.RENAME_PINNED),
    "system_sources": loam_files.SYSTEM_VAULT_SOURCES,
    "sweep": sweep.ARCHIVE,
    "written": sorted(str(p.relative_to(vault)) for p in vault.rglob("*.md")),
}))
"""


def check(label: str, got, want=True) -> None:
    global FAILURES  # noqa: PLW0603
    ok = got == want
    FAILURES += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f"\n      got  {got!r}\n      want {want!r}"))


def probe(name: str, config: str) -> dict:
    cfg = TMP / f"{name}.toml"
    home = TMP / f"{name}-home"
    home.mkdir(exist_ok=True)
    cfg.write_text(f'[paths]\nhome = "{home}"\n' + config, encoding="utf-8")
    vault = TMP / f"{name}-vault"
    vault.mkdir()
    env = {**os.environ, "LOAM_CONFIG": str(cfg), "LOAM_VAULT": str(vault),
           "PYTHONDONTWRITEBYTECODE": "1"}
    res = subprocess.run([sys.executable, "-c", PROBE, str(ROOT), str(vault)],
                         capture_output=True, text=True, env=env, check=False)
    if res.returncode != 0:
        print(res.stderr)
        raise SystemExit(f"FAIL  the probe under {name} did not run")
    return json.loads(res.stdout.strip().splitlines()[-1])


def main() -> int:
    first = probe("first-install", FIRST_INSTALL)
    check("the failure ledger and activity reader agree",
          (first["failure_log"], first["activity_log"]), ("00_INBOX/from-agents/loam-wake-failures.md",) * 2)
    check("first install: receipts under 40_PROJECTS", first["receipts"],
          "40_PROJECTS/widget/receipts")
    check("first install: RENAME_PINNED is the list it was", first["pinned"], [
        "00_INBOX/from-agents/loam-wake-failures.md", "00_INBOX/archive",
        "40_PROJECTS/*/receipts", "40_PROJECTS/Loam/engineer/role.md",
        "40_PROJECTS/Loam/engineer/projects/loam.md",
        "40_PROJECTS/Loam/infrastructure/schedule.md", "40_PROJECTS/Loam/architecture",
        "40_PROJECTS/Loam/decisions", "40_PROJECTS/Loam/reports",
        "99_SYSTEM/Dictation terms.md", "99_SYSTEM/conventions"])
    check("first install: SYSTEM_VAULT_SOURCES as it was", first["system_sources"], {
        "Engineer/Role.md": "40_PROJECTS/Loam/engineer/role.md",
        "Engineer/Platform project.md": "40_PROJECTS/Loam/engineer/projects/loam.md"})
    check("first install: the inbox sweep's paths as they were", first["sweep"],
          "00_INBOX/archive")
    check("first install: a failure lands in from-agents",
          first["written"], ["00_INBOX/from-agents/loam-wake-failures.md"])

    inst = probe("an-install", AN_INSTALL)
    flat = json.dumps(inst)
    check("an install: no path names the first install's folders",
          [w for w in ("00_INBOX", "40_PROJECTS", "99_SYSTEM", "from-agents") if w in flat], [])
    check("an install: the failure log is in Loam files (System)",
          (inst["failure_log"], inst["activity_log"]),
          ("System/loam-wake-failures.md", "System/loam-wake-failures.md"))
    check("an install: receipts under Projects", inst["receipts"], "Projects/widget/receipts")
    check("an install: pinned paths are the install's",
          inst["pinned"][:3], ["System/loam-wake-failures.md", "Inbox/archive",
                               "Projects/*/receipts"])
    check("an install: the inbox sweep reads the install's folders", inst["sweep"],
          "Inbox/archive")
    check("an install: a failure lands there", inst["written"],
          ["System/loam-wake-failures.md"])

    print(f"\n{'OK' if not FAILURES else f'{FAILURES} FAILED'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
