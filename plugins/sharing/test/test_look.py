#!/usr/bin/env python3
"""Check Sharing styles against Loam's design tokens and the standalone share page's Latte copy."""

import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
APP = PLUGIN.parent.parent / "app" / "public"
SHEETS = [PLUGIN / "assets" / "sharing.css", PLUGIN / "page" / "share" / "share.css"]
COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(|\boklch\(")


def strip_comments(s: str) -> str:
    return re.sub(r"/\*.*?\*/", "", s, flags=re.S)


def without_fallbacks(s: str) -> str:
    out, i = [], 0
    while True:
        j = s.find("var(", i)
        if j < 0:
            out.append(s[i:])
            return "".join(out)
        out.append(s[i:j])
        depth, k = 0, j + 3
        while k < len(s):
            if s[k] == "(":
                depth += 1
            elif s[k] == ")":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        inner = s[j + 4:k]
        out.append("var(" + without_fallbacks(inner.split(",", 1)[0]) + ")")
        i = k + 1


def token_blocks(s: str) -> dict:
    out = {}
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", strip_comments(s)):
        out.setdefault(sel.strip(), {}).update(
            {k: v.strip() for k, v in re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", body)}
        )
    return out


def share_root_blocks(text: str) -> tuple[dict, dict]:
    text = strip_comments(text)
    m = re.search(r"@media \(prefers-color-scheme: dark\)\s*\{\s*:root\s*\{([^{}]*)\}\s*\}", text)
    dark = dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", m.group(1))) if m else {}
    light_body = re.search(r"(?<![\w-]):root\s*\{([^{}]*)\}", text.replace(m.group(0), "") if m else text)
    light = dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", light_body.group(1))) if light_body else {}
    return {k: v.strip() for k, v in light.items()}, {k: v.strip() for k, v in dark.items()}


class Look(unittest.TestCase):
    def test_colours_are_tokens(self):
        for f in SHEETS:
            text = strip_comments(f.read_text())
            if f.name == "share.css":
                text = re.sub(r"@media \(prefers-color-scheme: dark\)\s*\{\s*:root\s*\{[^{}]*\}\s*\}", "", text)
                text = re.sub(r"(?<![\w-]):root\s*\{[^{}]*\}", "", text)
            bare = without_fallbacks(text)
            for line in bare.splitlines():
                self.assertIsNone(COLOUR.search(line), f"{f.name}: non-token colour: {line.strip()[:140]}")
                self.assertNotRegex(line, r"(?<![\w-])999px", f"{f.name}: hardcoded pill radius: {line.strip()[:140]}")

    def test_tokens_exist(self):
        known = set()
        for css in [APP / "tokens.css", *sorted(APP.glob("themes/*/theme.css"))]:
            for block in token_blocks(css.read_text()).values():
                known |= set(block)
        for f in SHEETS:
            text = strip_comments(f.read_text())
            own = set(re.findall(r"(--[a-z0-9-]+)\s*:", text))
            for name in set(re.findall(r"var\((--[a-z0-9-]+)", text)):
                if name.startswith("--sh-") or name in own:
                    continue
                self.assertIn(name, known, f"{f.name} reads {name}, which core does not define")

    def test_share_page_carries_latte(self):
        t = token_blocks((APP / "tokens.css").read_text())
        latte = token_blocks((APP / "themes" / "latte" / "theme.css").read_text())

        def value(name, dark):
            chain = [
                latte.get(':root[data-look="latte"][data-theme="%s"]' % ("dark" if dark else "light"), {}),
                latte.get(':root[data-look="latte"]', {}),
            ]
            chain += [t.get(':root[data-theme="dark"]', {})] if dark else []
            chain += [t.get(":root", {})]
            return next((b[name] for b in chain if name in b), None)

        light, dark = share_root_blocks((PLUGIN / "page" / "share" / "share.css").read_text())
        self.assertGreater(len(light), 40)
        for name, v in light.items():
            self.assertEqual(v, value(name, False), f"share.css {name} (light) is not Latte's")
        for name, v in dark.items():
            self.assertEqual(v, value(name, True), f"share.css {name} (dark) is not Latte's")
        for name in light:
            if value(name, True) != value(name, False):
                self.assertIn(name, dark, f"share.css has no dark value for {name}")

    def test_faces_are_persona_hues(self):
        dom = (PLUGIN / "page" / "dom.js").read_text()
        tones = re.search(r"const TONES = (\[.*?\])\s*\n", dom, flags=re.S).group(1)
        hues = re.findall(r"\['([a-z]+)', '(#[0-9A-Fa-f]{6})'\]", tones)
        self.assertGreaterEqual(len(hues), 6)
        core = token_blocks((APP / "tokens.css").read_text())[":root"]
        for hue, hex_ in hues:
            self.assertEqual(core.get(f"--persona-{hue}"), hex_)
            self.assertNotIn(f"--persona-{hue}-ink", core)
        for js in [*PLUGIN.glob("page/*.js"), *PLUGIN.glob("page/share/*.js")]:
            if js.name == "dom.js":
                continue
            self.assertNotRegex(js.read_text(), r"color: '#fff'|'#[0-9A-Fa-f]{6}'", f"{js.name} hardcodes a face colour")


if __name__ == "__main__":
    unittest.main()
