#!/usr/bin/env python3
"""Check Comments & suggestions styles against Loam's design tokens and WCAG contrast."""

import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
APP = PLUGIN.parent.parent / "app" / "public"
SHEETS = [PLUGIN / "assets" / "comments.css"]
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


class Look(unittest.TestCase):
    def test_colours_are_tokens(self):
        for f in SHEETS:
            bare = without_fallbacks(strip_comments(f.read_text()))
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
                if name.startswith("--dc-") or name in own:
                    continue
                self.assertIn(name, known, f"{f.name} reads {name}, which core does not define")

    def test_faces_are_persona_hues(self):
        dom = (PLUGIN / "page" / "editor" / "dom.js").read_text()
        tones = re.search(r"const TONES = (\[.*?\])\s*\n", dom, flags=re.S).group(1)
        hues = re.findall(r"\['([a-z]+)', '(#[0-9A-Fa-f]{6})'\]", tones)
        self.assertGreaterEqual(len(hues), 6)
        core = token_blocks((APP / "tokens.css").read_text())[":root"]
        for hue, hex_ in hues:
            self.assertEqual(core.get(f"--persona-{hue}"), hex_)
            self.assertNotIn(f"--persona-{hue}-ink", core)
        for js in [*PLUGIN.glob("page/*.js"), *PLUGIN.glob("page/editor/*.js")]:
            if js.name == "dom.js":
                continue
            self.assertNotRegex(js.read_text(), r"color: '#fff'|'#[0-9A-Fa-f]{6}'", f"{js.name} hardcodes a face colour")


def _lin(c):
    return c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4


def _gamma(c):
    c = max(0.0, min(1.0, c))
    return 12.92 * c if c <= .0031308 else 1.055 * c ** (1 / 2.4) - .055


def _oklab(rgb):
    r, g, b = (_lin(x) for x in rgb)
    l, m, s = (
        (.4122214708 * r + .5363325363 * g + .0514459929 * b) ** (1 / 3),
        (.2119034982 * r + .6806995451 * g + .1073969566 * b) ** (1 / 3),
        (.0883024619 * r + .2817188376 * g + .6299787005 * b) ** (1 / 3),
    )
    return (
        .2104542553 * l + .7936177850 * m - .0040720468 * s,
        1.9779984951 * l - 2.4285922050 * m + .4505937099 * s,
        .0259040371 * l + .7827717662 * m - .8086757660 * s,
    )


def _rgb(lab):
    L, a, b = lab
    l, m, s = (
        (L + .3963377774 * a + .2158037573 * b) ** 3,
        (L - .1055613458 * a - .0638541728 * b) ** 3,
        (L - .0894841775 * a - 1.2914855480 * b) ** 3,
    )
    return tuple(_gamma(x) for x in (
        4.0767416621 * l - 3.3077115913 * m + .2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - .3413193965 * s,
        -.0041960863 * l - .7034186147 * m + 1.7076147010 * s,
    ))


def _ratio(x, y):
    lx, ly = (.2126 * _lin(c[0]) + .7152 * _lin(c[1]) + .0722 * _lin(c[2]) for c in (x, y))
    return (max(lx, ly) + .05) / (min(lx, ly) + .05)


class SignalWords(unittest.TestCase):
    def test_signal_words_read_in_every_theme(self):
        css = strip_comments((PLUGIN / "assets" / "comments.css").read_text())
        m = re.search(r"--dc-accent-ink:\s*color-mix\(in oklab, var\(--dc-accent\) (\d+)%, var\(--dc-ink\)\)", css)
        self.assertIsNotNone(m)
        share = float(m.group(1)) / 100
        core = token_blocks((APP / "tokens.css").read_text())
        hexes = lambda h: tuple(int(h.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
        for look in ["plain", *sorted(p.parent.name for p in APP.glob("themes/*/theme.css") if p.parent.name != "plain")]:
            theme = token_blocks((APP / "themes" / look / "theme.css").read_text()) if look != "plain" else {}
            for mode in ("light", "dark"):
                chain = [theme.get(f':root[data-look="{look}"][data-theme="{mode}"]', {}), theme.get(f':root[data-look="{look}"]', {})]
                chain += [core.get(':root[data-theme="dark"]', {})] if mode == "dark" else []
                chain += [core[":root"]]
                v = lambda n: hexes(next(b[n] for b in chain if n in b and b[n].startswith("#")))
                crema, ink = _oklab(v("--signal-crema")), _oklab(v("--paper-ink"))
                words = _rgb(tuple(share * a + (1 - share) * b for a, b in zip(crema, ink)))
                for ground in ("--paper-raised", "--paper-pane", "--paper-page", "--paper-card"):
                    self.assertGreaterEqual(_ratio(words, v(ground)), 4.5, f"{look} {mode}: contrast on {ground}")


if __name__ == "__main__":
    unittest.main()
