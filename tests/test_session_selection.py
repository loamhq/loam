#!/usr/bin/env python3
"""Exercise split-service selection against commits in a real Git repository."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import loam_session_services as services
import loam_update as update


def run(*args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def commit():
    run("add", "-A")
    run("commit", "-qm", "fixture")
    return run("rev-parse", "HEAD")


def check(description, paths, old, expected):
    for path, content in paths.items():
        (repo / path).write_text(content)
    new = commit()
    got = services.selected({"api_revision": old, "host_revision": old}, new)
    assert got == expected, (description, got, expected)
    return new


with tempfile.TemporaryDirectory() as td:
    repo = Path(td)
    run("init", "-q")
    run("config", "user.name", "Test")
    run("config", "user.email", "test@example.invalid")
    update.runtime = lambda: repo
    for directory in ("bin", "lib", "systemd/session"):
        (repo / directory).mkdir(parents=True)
    entry = "bin/session-host"
    script = '#!/usr/bin/env python3\n"""Help."""\nfrom loam_math import count\nprint(__doc__, count())\n'
    (repo / entry).write_text(script)
    (repo / "bin/session-broker").write_text("pass\n")
    path = "lib/loam_math.py"
    definition = 'def count():\n    """Count records."""\n    return 1\n'
    (repo / path).write_text(definition)
    base = commit()
    host = {services.HOST}
    prose = '# Records.\n' + definition.replace("Count records.", "Return the count.").replace("return 1", "return   1")
    clean = check("private doc, comments and whitespace", {path: prose}, base, set())
    check("one executable token against the older running process", {path: prose.replace("return   1", "return   2")}, base, host)
    check("module help is observable", {path: prose, entry: script.replace("Help.", "Host help.")}, clean, host)
    for label, reader in (
            ("direct doc", "print(count.__doc__)"),
            ("callback", "print(list(map(str, [count])))"),
            ("dynamic lookup", 'print(globals()["cou" + "nt"].__doc__)'),
            ("aliased import", "from loam_math import count as helper\nprint(helper.__doc__)"),
            ("namespace", 'import loam_math as m\nprint(getattr(m, "count").__doc__)')):
        (repo / entry).write_text(script + reader + "\n")
        (repo / path).write_text(definition)
        old = commit()
        check(label, {path: prose}, old, host)
    (repo / entry).write_text(script)
    (repo / path).write_text('"""Internal catalog."""\n' + definition)
    old = commit()
    check("unread internal module doc", {path: '"""Static catalog."""\n' + definition}, old, set())
    (repo / entry).write_text('from loam_math import __doc__ as description\nprint(description)\n')
    old = commit()
    check("module documentation imported under a different name",
          {path: '"""Help read by another module."""\n' + definition}, old, host)
    (repo / entry).write_text(script)
    old = commit()
    (repo / path).write_text('@staticmethod\n' + definition)
    decorated = commit()
    check("decorator can consume function metadata", {path: '@staticmethod\n' + prose.replace('# Records.\n', '')},
          decorated, host)
    (repo / entry).write_text("import loam_math as numbers\nprint(numbers.count())\n")
    (repo / path).write_text(definition)
    qualified = commit()
    check("namespace used only for a qualified call", {path: prose}, qualified, set())
    (repo / entry).write_text("import loam_math as numbers\nreader = numbers.count\n")
    (repo / path).write_text(definition)
    escaped = commit()
    check("a qualified function escapes the namespace", {path: prose}, escaped, host)
    (repo / entry).write_text(script)
    old = commit()
    (repo / path).write_text(definition + "@register\nclass Metadata: pass\n")
    registered = commit()
    check("a registration decorator can inspect sibling documentation",
          {path: prose + "@register\nclass Metadata: pass\n"}, registered, host)
    (repo / path).write_text(definition)
    old = commit()
    check("unparseable source", {path: "return 1\n"}, old, host)
    (repo / path).write_text(definition)
    old = commit()
    os.chmod(repo / path, 0o755)
    new = commit()
    assert services.selected({"host_revision": old, "api_revision": old}, new) == host
    (repo / path).unlink()
    (repo / path).symlink_to("elsewhere")
    new = commit()
    assert services.selected({"host_revision": old, "api_revision": old}, new) == host
    (repo / path).unlink()
    (repo / path).write_text(definition)
    (repo / entry).write_text("import math, loam_math\n")
    old = commit()
    check("second module in an import list", {path: definition.replace("return 1", "return 2")}, old, host)
    old = run("rev-parse", "HEAD")
    (repo / path).rename(repo / "retired-module")
    new = commit()
    assert services.selected({"host_revision": old, "api_revision": old}, new) == host
    check("unit template", {"systemd/session/" + services.HOST: "# unit\n"}, new, host)
    for folder in ("plugins/_listed/reader", "plugins/reader"):
        (repo / folder).mkdir(parents=True)
    old = run("rev-parse", "HEAD")
    old = check("Market reference copies do not replace either service",
                {"plugins/_listed/reader/PLUGIN.md": "updated copy"}, old, set())
    check("shipped plugin changes retain their API refresh",
          {"plugins/reader/PLUGIN.md": "updated builtin"}, old, {services.API})
print("ok: session service selection across Git revisions")
