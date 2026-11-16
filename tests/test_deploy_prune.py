#!/usr/bin/env python3
"""Which image tags a deploy keeps: bin/deploy's tags_to_remove.

The rule is two tags per app -- the running one and the one before it --
and the cases here are the ways "the one before it" can be ambiguous: an
`up` that knows what it replaced, a sweep that does not, a redeploy of the
same commit, a previous tag already gone, and no container at all. Pure
function, nothing on the box is read or written.

Run: python3 tests/test_deploy_prune.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))

loader = importlib.machinery.SourceFileLoader("deploy_mod", str(AGENTS / "bin" / "deploy"))
spec = importlib.util.spec_from_loader(loader.name, loader)
deploy = importlib.util.module_from_spec(spec)
loader.exec_module(deploy)

results: list[bool] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label:58}{'  | ' + detail if detail else ''}")


# Newest first, the way `docker image ls` lists them; created times are
# RFC 3339 as `docker image inspect` gives them.
TAGS = [
    ("0e541b5", "2026-09-29T18:55:36Z"),
    ("f2bd36f", "2026-09-29T18:37:06Z"),
    ("46d38a5", "2026-09-29T18:05:20Z"),
    ("e73c89a", "2026-09-29T02:43:20Z"),
]


def main() -> int:
    gone = deploy.tags_to_remove(TAGS, "0e541b5", "46d38a5")
    check("after an up: running and what it replaced are kept",
          sorted(gone) == ["e73c89a", "f2bd36f"], str(gone))

    gone = deploy.tags_to_remove(TAGS, "0e541b5", None)
    check("a sweep keeps running and the newest other",
          sorted(gone) == ["46d38a5", "e73c89a"], str(gone))

    gone = deploy.tags_to_remove(TAGS, "f2bd36f", None)
    check("the newest other can be newer than the running tag",
          sorted(gone) == ["46d38a5", "e73c89a"], str(gone))

    gone = deploy.tags_to_remove(TAGS, "0e541b5", "0e541b5")
    check("a redeploy of the same commit falls back to the newest other",
          sorted(gone) == ["46d38a5", "e73c89a"], str(gone))

    gone = deploy.tags_to_remove(TAGS, "0e541b5", "1234567")
    check("a previous tag no longer on the box falls back too",
          sorted(gone) == ["46d38a5", "e73c89a"], str(gone))

    check("nothing is removed while nothing is running",
          deploy.tags_to_remove(TAGS, None, "f2bd36f") == [])

    check("a lone running tag stays",
          deploy.tags_to_remove([TAGS[0]], "0e541b5", None) == [])

    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
