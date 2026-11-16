#!/usr/bin/env python3
"""How an app is handed a vendor API key, against bin/deploy's reader.

The property under test is a negative one, and it is the whole point of the
mechanism: a key an app uses is never a literal in this repo. It lives in
the box's secret store, `bin/deploy` reads it at the moment it runs compose,
and the app's compose file interpolates it by name. So the cases here are:

  - the reader returns the named variables and ONLY those, out of a file
    with comments, blanks, quoting and nine keys the caller did not ask for;
  - an absent name, an empty value, a missing file and an unreadable file
    all yield nothing rather than an empty string, because the compose
    file's `:?` says what is wrong better than a blank ever could;
  - the allowlist is the gate: an app not named in APP_SECRETS gets nothing,
    however its own compose file is written, because that file is edited by
    that app's engineer and the store holds keys that are not that app's;
  - no Google-shaped key literal is anywhere in `agents/deploy/`, which is
    the thing a future edit is most likely to get wrong.

Runs against throwaway files in a temp directory. The box's real secret
store is never opened here, and no test in this file prints a secret --
including the one that greps the deploy tree, which reports a path only.

Run: python3 tests/test_deploy_secrets.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import re
import sys
import tempfile
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


# A store shaped like the real one: a header comment, a section comment, blank
# lines, a quoted value, an empty value, and keys the caller must not receive.
STORE = """\
# Production secrets for the chassis service.
# Loaded by systemd via EnvironmentFile=

TELEGRAM_BOT_TOKEN=telegram-value
ANTHROPIC_API_KEY=anthropic-value

GEMINI_API_KEY=gemini-value

# Langfuse tracing (added 2026-07-21)
LANGFUSE_HOST="https://cloud.langfuse.com"
EMPTY_KEY=
NOT_AN_ASSIGNMENT
"""


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        store = tmp / "secrets.env"
        store.write_text(STORE, encoding="utf-8")

        got = deploy.read_secrets(store, ("GEMINI_API_KEY",))
        check("the named key is read", got == {"GEMINI_API_KEY": "gemini-value"}, str(sorted(got)))

        got = deploy.read_secrets(store, ("GEMINI_API_KEY", "LANGFUSE_HOST"))
        check("surrounding quotes are stripped",
              got.get("LANGFUSE_HOST") == "https://cloud.langfuse.com")

        # The one that matters: asking for one key does not hand over the rest.
        check("the nine keys not asked for stay in the store",
              set(deploy.read_secrets(store, ("GEMINI_API_KEY",))) == {"GEMINI_API_KEY"})

        check("an absent name yields nothing",
              deploy.read_secrets(store, ("NOPE_API_KEY",)) == {})
        check("an empty value yields nothing, not an empty string",
              deploy.read_secrets(store, ("EMPTY_KEY",)) == {})
        check("a line with no `=` is skipped, not crashed on",
              deploy.read_secrets(store, ("NOT_AN_ASSIGNMENT",)) == {})
        check("a missing store yields nothing",
              deploy.read_secrets(tmp / "absent.env", ("GEMINI_API_KEY",)) == {})

        if os.geteuid() != 0:
            store.chmod(0o000)
            check("an unreadable store yields nothing, not a traceback",
                  deploy.read_secrets(store, ("GEMINI_API_KEY",)) == {})
            store.chmod(0o600)
        else:
            check("an unreadable store yields nothing, not a traceback", True,
                  "skipped as root, which can read any mode")

    # The allowlist, against the real table rather than a fixture: this is the
    # gate, and a test that built its own table would not notice it emptying.
    check("loam-ui is entitled to the Gemini key, and nothing else",
          deploy.APP_SECRETS.get("loam-ui") == ("GEMINI_API_KEY",),
          str(deploy.APP_SECRETS.get("loam-ui")))

    class FakeApp:
        name = "some-other-app"

    check("an app not on the allowlist is handed nothing",
          deploy.app_secrets(FakeApp()) == {})

    # The negative property, over the whole deployment tree: a Google API key
    # is `AIza` plus 35 characters, and none belongs in a repo. Reports the
    # path only -- a test that printed the match would be the leak it guards.
    shaped = re.compile(r"AIza[0-9A-Za-z_-]{35}")
    leaked = [str(p.relative_to(AGENTS)) for p in sorted((AGENTS / "deploy").rglob("*"))
              if p.is_file() and shaped.search(p.read_text(encoding="utf-8", errors="ignore"))]
    check("no Google-shaped key literal anywhere in agents/deploy/",
          not leaked, ", ".join(leaked) or "clean")

    # And the app's unit is handed what the allowlist gives it, by name, and
    # nothing more (since 2026-11-16 the app is a unit, not a container: the
    # compose file that interpolated the key is gone, and bin/deploy's
    # service_env is its environment). Read as text: the point is that the
    # key comes from app_secrets and from nowhere else.
    source = (AGENTS / "bin" / "deploy").read_text(encoding="utf-8")
    body = source[source.index("def service_env("):source.index("def env_file_text(")]
    # Optional since 2026-11-14: dictation ships off and a blank install has
    # no key until its person pastes one (first release, decision 2).
    check("loam-ui's environment takes its keys from app_secrets, the allowlist",
          "env.update(app_secrets(app))" in body and "GEMINI_API_KEY" not in body)
    # Retired 2026-10-01 with the app's POST /api/dictate.
    check("...and nothing hands it DICTATION_TOKEN", "DICTATION_TOKEN" not in body)

    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
