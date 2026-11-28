#!/usr/bin/env python3
"""Verify inline visual support across thread snippets, provisioning, and the shared library."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

AGENTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS / "lib"))

import loam_threads as threads  # noqa: E402

fails = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}{'' if cond else '  | ' + detail}")


def load_provision():
    path = AGENTS / "bin" / "provision"
    loader = importlib.machinery.SourceFileLoader("provision_visuals_test", str(path))
    spec = importlib.util.spec_from_loader("provision_visuals_test", loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    # 1. Thread list snippet replaces visual fences with plain labels, leaving ordinary fences alone.
    visual_reply = (
        "Here is the comparison.\n\n"
        "```visual Pour-over grinders\n"
        "<table><tr><td>Ode Gen 2</td><td>$345</td></tr></table>\n"
        "```\n\n"
        "Ode Gen 2 wins for filter coffee."
    )
    snip = threads._snippet(visual_reply)
    check(
        "thread snippet replaces visual fence with plain title and keeps prose",
        "Visual: Pour-over grinders" in snip
        and "Ode Gen 2 wins for filter coffee." in snip
        and "<table" not in snip,
        repr(snip),
    )

    visual_only = "```visual\n<h3>Quarterly spend</h3><svg viewBox='0 0 100 40'></svg>\n```"
    snip_only = threads._snippet(visual_only)
    check(
        "visual-only snippet derives heading label without raw tags",
        snip_only == "Visual: Quarterly spend",
        repr(snip_only),
    )

    ordinary_html = "Example:\n\n```html\n<div class='demo'>hi</div>\n```"
    snip_html = threads._snippet(ordinary_html)
    check(
        "ordinary ```html code block is not treated as a visual fence",
        "<div class='demo'>hi</div>" in snip_html and "Visual:" not in snip_html,
        repr(snip_html),
    )

    nested_example = "Use this:\n\n````md\n```visual Demo\n<p>hi</p>\n```\n````"
    snip_nested = threads._snippet(nested_example)
    check(
        "visual fence inside an outer non-visual code fence is left intact",
        "```visual Demo" in snip_nested,
        repr(snip_nested),
    )

    # 2. Provisioned CLAUDE.md carries one sentence pointing to loam-inline-visuals.
    prov = load_provision()
    rendered = prov.claude_md("Iris", Path("/tmp/iris"))
    check(
        "provision claude_md Surface paragraph points to loam-inline-visuals",
        "`visual`" in rendered and "`loam-inline-visuals`" in rendered,
        repr(rendered[:600]),
    )

    # 3. Shared library carries the new skill and updated index / docs.
    skill = AGENTS / "docs" / "library" / "skills" / "loam-inline-visuals" / "SKILL.md"
    readme = AGENTS / "docs" / "library" / "README.md"
    reach = AGENTS / "docs" / "library" / "skills" / "loam-what-you-can-reach" / "SKILL.md"
    how = AGENTS / "docs" / "library" / "skills" / "loam-how-it-works" / "SKILL.md"
    check("loam-inline-visuals skill exists with frontmatter",
          skill.is_file() and "name: loam-inline-visuals" in skill.read_text("utf-8"))
    check("library README lists loam-inline-visuals",
          "loam-inline-visuals" in readme.read_text("utf-8"))
    check("loam-what-you-can-reach mentions inline visuals and skill",
          "loam-inline-visuals" in reach.read_text("utf-8"))
    check("loam-how-it-works describes inline visuals",
          "loam-inline-visuals" in how.read_text("utf-8"))

    print(f"\n{'OK' if not fails else 'FAILED'}: {fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
