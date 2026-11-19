"""check-drift reads each project's test command (lib/loam_testcmd.py).

A checkout in a temp directory, the command shapes the registered projects
use (an npm suite after a `cd`, a validate-and-check chain with a for loop,
a script run by its interpreter), each first with everything there and then
with the thing it names taken away. PATH is a stub: only the programs this
test says exist do.

Run: python3 tests/test_testcmd.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import loam_testcmd as tc  # noqa: E402

fails = 0


def check(label, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:62} {got!r}"[:240]
          + ("" if ok else f"\n      wanted {want!r}"))


ON_PATH = {"npm", "node", "python3", "caddy", "sh"}
which = lambda prog: f"/usr/bin/{prog}" if prog in ON_PATH else None  # noqa: E731

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    (root / "app").mkdir()
    (root / "app" / "package.json").write_text(json.dumps({"scripts": {"test": "node --test"}}))
    (root / "tests").mkdir()
    (root / "tests" / "run-all").write_text("#!/usr/bin/env python3\n")
    (root / "site" / "js").mkdir(parents=True)
    (root / "site" / "js" / "a.js").write_text("1\n")
    (root / "Caddyfile").write_text(":80\n")

    def f(cmd):
        return tc.findings(cmd, root, which=which)

    npm = "cd app && npm test"
    chain = ("caddy validate --config Caddyfile --adapter caddyfile && test -s site/index.html"
             " && for f in site/js/*.js; do node --check \"$f\" || exit 1; done")
    runner = "python3 tests/run-all"
    check("npm after cd: all there", f(npm), [])
    check("chain with a for loop: all there", f(chain), [])
    check("interpreter and script: all there", f(runner), [])
    check("python -m is not judged as a file", f("python3 -m pytest -q"), [])
    check("a $VAR is not judged", f("sh $SCRIPT"), [])

    check("no command", len(f("")), 1)
    check("cd into a folder that is gone", f("cd web && npm test"),
          ["`cd web`: no such folder in the checkout"])
    (root / "app" / "package.json").write_text(json.dumps({"scripts": {"lint": "x"}}))
    check("npm test with no test script", f(npm),
          ["`npm test`: package.json in `app/` has no `test` script"])
    check("npm run of a missing script", f("cd app && npm run check"),
          ["`npm run check`: package.json in `app/` has no `check` script"])
    (root / "tests" / "run-all").unlink()
    check("the script the interpreter runs was removed", f(runner),
          ["`python3 tests/run-all`: not in the checkout"])
    check("a program not on PATH", f("pytest -q"), ["`pytest`: not on this box's PATH"])
    check("a relative program missing", f("./check.sh"), ["`./check.sh`: not in the checkout"])
    (root / "site" / "js" / "a.js").unlink()
    check("a glob matching nothing", f("node site/js/*.js"),
          ["`node site/js/*.js`: matches no file in the checkout"])

print(f"\nFAILURES: {fails}")
sys.exit(1 if fails else 0)
