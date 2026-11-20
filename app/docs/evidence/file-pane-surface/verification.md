# File pane verification

Chromium 156 ran the live app shell with fixture broker responses. The Markdown
editor loaded the repository's Comments & suggestions plugin and its seeded
document server. The baseline app came from `a8b6e4d`.

## Reading surfaces

The editor, its toolbar and its scrolling body resolved to the chat reading
surface in all 28 combinations of width, mode and theme. The themes were Latte,
Plain, Autumn, Deep Forest, Foundry, Mist Trail and Yosemite.

| Theme and mode | Editor before | 1440 px editor and chat after | 390 px editor and chat after |
| --- | --- | --- | --- |
| Latte dark | `#302E2A` | `#22211F` | `#1A1917` |
| Latte light | `#FAF8F4` | `#F6F3EE` | `#EEEAE3` |
| Plain dark | `#2D2D2D` | `#252525` | `#1E1E1E` |
| Plain light | `#F6F6F6` | `#F0F0F0` | `#E8E8E8` |

All 60 core surface checks passed across Latte and Plain at those widths and
modes. They covered Chats, Markdown preview and editing, HTML, image, PDF,
download-only files and the desktop Apps frame. The PDF check measured the
frame and the app's download fallback. Chromium also kept light-only HTML on
a white document canvas when the app was set to Latte dark and the browser
preferred light. The Files and Apps iframe backdrops still matched the shell.

## Document contrast

These are the lowest measured contrast ratios from the rendered fixture states.
Measurements use the resolved foreground and the composited background.

| Detail | Minimum | Configurations |
| --- | --- | --- |
| Properties summary | 4.82:1 | Seven themes, both widths and modes |
| Expanded Properties text | 5.27:1 | Latte and Plain, both widths and modes |
| Inline code text | 10.83:1 | Seven themes, both widths and modes |
| Code block text and delimiters | 4.53:1 | Seven themes, both widths and modes |
| Table heading text | 6.87:1 | Seven themes, both widths and modes |
| Callout label | 4.67:1 | Seven themes, both widths and modes |
| Comment and suggestion underline | 3.12:1 | Seven themes, both widths and modes |
| Suggested text | 4.82:1 | Seven themes, both widths and modes |
| Selected body text | 6.65:1 | Latte and Plain, both widths and modes |
| Editing caret | 13.07:1 | Latte and Plain, both widths and modes |

Latte light selection at 390 px measured 1.38:1 against the page, up from
1.34:1 against the raised surface in the baseline. The comment card uses the
raised token when focused. [Before and after screenshots](README.md)

## Test output

`APP_PREVIEW_CHROME=/usr/bin/chromium-headless-shell npm test` exited 0.

```text
ℹ tests 1323
ℹ pass 1315
ℹ fail 0
ℹ skipped 8
ℹ tests 21
ℹ pass 21
ℹ fail 0
ℹ skipped 0
```

An opt-in run of `test/panels.test.js` and `test/shape-staging-style.test.js`
with `LOAM_PLAYWRIGHT` and `LOAM_CHROME` reproduced three failures on both
the baseline and the branch.

- The two-thread test timed out waiting for a side-pane `.speakbtn`.
- The panel focus test's fixture had no `dictation` plugin row.
- The Activity staging test expected `landed · 09:50–10:17 · 27 min` and received `09:50–10:17 · 27 min` followed by `landed` on the next line.
