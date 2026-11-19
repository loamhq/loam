"""Whether what a project's test command names is there (since 2026-11-19).

`test_command` is hand-written in a project's record (`projects/<name>.json`)
and until this nothing checked it: when a project removed the script it
named, every landing of that project failed as "tests fail", and the
automatic continuation was told to make them pass from inside the
repository, which it cannot honestly do (engineer memory, 2026-11-16).
bin/check-drift reports what ``findings`` finds.

The command is read, never run: the programs it calls are on PATH, each
`cd` lands in a folder, a script an interpreter is given is a file, and
`npm test` / `npm run X` has that script in package.json. A word with a `$`
in it is not judged, and a glob must match something.
"""

from __future__ import annotations

import glob
import json
import re
import shlex
import shutil
from pathlib import Path

# The words of a shell command that are its control, not a program.
SHELL_WORDS = {"for", "in", "do", "done", "if", "then", "else", "elif", "fi",
               "while", "until", "case", "esac", "!", "{", "}", "time", "exec"}
BUILTINS = {"test", "[", "true", "false", "echo", "exit", "set", "export", ":"}
INTERPRETERS = {"python", "python3", "node", "sh", "bash", "ruby", "perl"}
SEPARATORS = (";", "&&", "||", "|")
GLOB = "*?["


def _words(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
    lexer.whitespace_split = True
    return list(lexer)


def findings(command: str | None, root: Path, *, which=shutil.which) -> list[str]:
    """What is missing, one sentence each; empty when everything it names is
    there. ``root`` is where the command runs (the project's checkout)."""
    if not command or not command.strip():
        return ["no `test_command` in its record: every landing stops as untested. "
                "Register the lightest honest check (engineer memory, 2026-11-16)."]
    try:
        words = _words(command)
    except ValueError as exc:
        return [f"`test_command` does not parse as a shell command: {exc}"]
    found: list[str] = []
    lost: list[str] = []
    cwd = root

    def where() -> str:
        return "the checkout" if cwd == root else f"`{cwd.relative_to(root)}/`"

    def judge(seg: list[str]) -> None:
        nonlocal cwd
        if lost:
            return
        if seg[:1] in (["for"], ["case"]):
            return      # `for f in a b` names a variable and words, no program
        seg = [w for w in seg if w not in SHELL_WORDS]
        while seg and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", seg[0]):
            seg = seg[1:]
        if not seg or "$" in seg[0] or any(c in seg[0] for c in GLOB):
            return
        prog, args = seg[0], seg[1:]
        if prog == "cd":
            target = args[0] if args else None
            if target and "$" not in target:
                if not (cwd / target).is_dir():
                    found.append(f"`cd {target}`: no such folder in {where()}")
                    lost.append(target)     # what follows would run elsewhere
                    return
                cwd = (cwd / target)
            return
        if prog in BUILTINS:
            return
        if "/" in prog:
            if not (cwd / prog).exists():
                found.append(f"`{prog}`: not in {where()}")
            return
        if which(prog) is None:
            found.append(f"`{prog}`: not on this box's PATH")
            return
        if prog in INTERPRETERS:
            if any(flag in args for flag in ("-m", "-c", "-e")):
                return
            script = next((a for a in args if not a.startswith("-")), None)
            if script is None or "$" in script:
                return
            if any(c in script for c in GLOB):
                if not glob.glob(str(cwd / script)):
                    found.append(f"`{prog} {script}`: matches no file in {where()}")
            elif not (cwd / script).exists():
                found.append(f"`{prog} {script}`: not in {where()}")
            return
        if prog == "npm" and args:
            name = ("test" if args[0] in ("test", "t") else
                    args[1] if args[0] in ("run", "run-script") and len(args) > 1 else None)
            if name is None:
                return
            try:
                scripts = json.loads((cwd / "package.json").read_text(encoding="utf-8")
                                     ).get("scripts") or {}
            except (OSError, ValueError):
                found.append(f"`npm {' '.join(args[:2])}`: no readable package.json "
                             f"in {where()}")
                return
            if name not in scripts:
                found.append(f"`npm {' '.join(args[:2])}`: package.json in {where()} "
                             f"has no `{name}` script")

    segment: list[str] = []
    for word in [*words, ";"]:
        if word in SEPARATORS or set(word) <= set(";&|"):
            judge(segment)
            segment = []
        else:
            segment.append(word)
    return found
