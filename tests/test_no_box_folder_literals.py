#!/usr/bin/env python3
"""No code names the first box's vault folders as string literals.

A fresh vault says `Conversations/`, `Inbox/`, `System/` (docs/config.md,
`[vault]`); the first box says `15_CONVERSATIONS/`, `00_INBOX/`,
`99_SYSTEM/`. A literal of the box's name in a deny rule or an exclusion
fences a folder a fresh vault does not have, and fails open (caused: until
2026-11-19 the files tree, the reviewer's exclusion and two MCP servers named
`15_CONVERSATIONS`). Code reads `lc.vault_folder(...)`. Comments and
docstrings may name the box's folders; string literals in code may not,
except the one that quotes the role file's own words.

Run: python3 tests/test_no_box_folder_literals.py
"""

from __future__ import annotations

import io
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAMES = ("15_CONVERSATIONS", "00_INBOX", "99_SYSTEM")
# (file, the literal's text): quoted from role.md, matched against it.
ALLOWED = {("bin/provision", '"15_CONVERSATIONS"'),
           ("bin/provision", '"Touch `15_CONVERSATIONS/`"'),
           # A drill's own scratch vault, laid out as the drill reads it.
           ("bin/provider-follow", '"00_INBOX"'),
           # A fallback only: the runtime passes LOAM_VAULT_DATA, the system
           # folder as this install names it (lib/loam_plugin_servers.py).
           ("plugins/doc-comments/server/doccomments/store.py", '"99_SYSTEM/Loam/data/doc-comments"')}


def python_files():
    for top in ("lib", "bin", "mcp", "hooks", "broker", "plugins"):
        for p in sorted((ROOT / top).rglob("*")):
            if not p.is_file() or {".venv", "node_modules", "test", "tests"} & set(p.parts):
                continue
            if p.suffix == ".py" or (p.suffix == "" and p.read_bytes()[:40].startswith(b"#!/usr/bin/env python")):
                yield p


found = []
for path in python_files():
    rel = str(path.relative_to(ROOT))
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline))
    except (tokenize.TokenError, SyntaxError, UnicodeDecodeError):
        continue
    prev = None
    for tok in toks:
        is_doc = tok.type == tokenize.STRING and (prev is None or prev.type in (
            tokenize.NEWLINE, tokenize.NL, tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING))
        if tok.type == tokenize.STRING and not is_doc and any(n in tok.string for n in NAMES) \
                and (rel, tok.string) not in ALLOWED:
            found.append(f"{rel}:{tok.start[0]}: {tok.string[:70]}")
        if tok.type not in (tokenize.COMMENT,):
            prev = tok
for f in found:
    print("FAIL ", f)
print("OK" if not found else f"{len(found)} literal(s)")
sys.exit(1 if found else 0)
