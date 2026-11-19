#!/usr/bin/env python3
"""Git backup from a shell, and what its timer runs.

  backup.py push [--now] [vault|spaces]   back up what is due (--now: whatever is due or not)
  backup.py status                        each tree's health, as JSON
  backup.py connect <vault|spaces> <address>   check the address, make the key, save it
  backup.py test <vault|spaces>           does the key work? On yes, the first backup
  backup.py disconnect <vault|spaces> [--forget-key]
  backup.py restore <vault|spaces> [<address>] [--replace]

Run by the plugin's host with the plugin's environment (bin/plugin-run);
from a shell, `bin/plugin-run` is not needed: everything is found from
Loam's config. docs/backup.md has the design.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import backup_core as core  # noqa: E402


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    cmd, rest = argv[0], argv[1:]
    flags = {a for a in rest if a.startswith("--")}
    args = [a for a in rest if not a.startswith("--")]
    try:
        if cmd == "push":
            out = core.run(force="--now" in flags, trees=args or None)
            print(json.dumps(out, indent=1))
            return 0 if all(r.get("ok") for r in out) else 1
        if cmd == "status":
            out = core.status()
        elif cmd == "connect" and len(args) == 2:
            out = core.connect(args[0], args[1])
        elif cmd == "test" and len(args) == 1:
            out = core.test(args[0])
        elif cmd == "disconnect" and len(args) == 1:
            out = core.disconnect(args[0], forget="--forget-key" in flags)
        elif cmd == "restore" and len(args) in (1, 2):
            out = core.restore(args[0], args[1] if len(args) == 2 else None,
                               replace="--replace" in flags)
        else:
            print(__doc__.strip(), file=sys.stderr)
            return 2
    except core.BackupError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=1))
        return 1
    print(json.dumps(out, indent=1))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
