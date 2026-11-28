# App size: full-width scaling, 0.9-base default, and 60%–200% range

## Summary

- **Full-width `#app` at every scale:** Removed `#app`'s legacy `max-width: 460px; margin: 0 auto;` ceiling. Previously, zooming a 390px phone below ~85% made `390 / scale > 460`, shrinking the Chats list, thread view, Files, Apps, and bottom tab bar into a centred column with empty side bands (`345px` wide at `75%`, `248px` wide at `0.54`), while top-layer sheets stayed edge-to-edge.
- **0.9-base default:** `100%` in the UI now applies `--ui-scale: 0.9` (`sizeToScale(size) = Math.round(size * 9) / 1000`), spanning CSS `zoom` `0.54` (`60%`) to `1.80` (`200%`). `uiScale()` in `public/scale.js` accepts `0.5`–`2` so `layoutPixels()` stays accurate across the full range.
- **60%–200% range and natural steps:** Slider uses `min: 60`, `max: 200`, `step: 5`. The `−`/`+` buttons and desktop `Ctrl/Cmd` shortcuts step by 5% up to `150%` and by 10% above `150%` (`stepSize()`).
- **High-zoom usability (`200%` on `390px` phone and `1440px` desktop):**
  - `.tt-list` (`container: list / inline-size`): header title and all action buttons (`New`, `Search`, plugin action, `⋯`, and desktop `Hide sidebar`) compact progressively at `<= 330px` and `<= 265px` so the header stays on one composed row with zero stranded buttons or truncated `"Chats"` headings.
  - `.tt-list .tt-row` (`@container list (max-width: 280px)` in `public/threads.css`): on narrow zoomed lists (`390px` phone at high sizes and the trimmed `260px` desktop sidebar at `200%`), moves `.srow-meta` (time + unread dot) below `.srow-who`, slims the avatar to `36px`, clamps `.srow-title` to two lines with `overflow-wrap: break-word` (no mid-word breaks), and places the hover `.tt-row-pin` button on the bottom time row. `public/thread-selection.js` drops the desktop selection pen stroke below stacked `.srow-meta`. Lists wider than `280px` (`60%` and `100%` on both phone and desktop) stay completely unchanged (`80px` base row height).
  - `.strip` / `.persona`: switched to CSS Grid (`repeat(auto-fit, minmax(72px, 1fr))`, compacting to `minmax(58px, 1fr)` with `48px` blobs at `<= 265px`) so 7-agent rows fill evenly across the full width at `60%`, `100%`, and `200%` without leaning left.
  - `.composer-actions` + `.composer-end`: grouped trailing controls (`extra`, `microphone`, `send`) inside `.composer-end { margin-left: auto; flex: none; }` so `Send` always stays beside the microphone at the right edge even when the row wraps on a narrow phone or split pane, and stays on one row on desktop `1440` at `200%`.
  - `.desk` (`container: desk / inline-size`): at `<= 960px` zoomed layout width (`1440px` at `200%` = `800px`), keeps two-pane navigation while trimming `.rail` to `64px` and `.listcol:not(.away)` to `260px`, giving `.pane` `~835px` on screen so thread titles and prose remain generous.
  - `.nav` compacts inside `@container (max-width: 240px)` so `Chats`, `Files`, and `Apps` fit without clipping.
  - `.appearance-card[aria-labelledby="appearance-size"]` stacks the range slider above `−`/`+` with full width inside `@container (max-width: 240px)` so the slider never collapses to zero width.
- **Older-host compatibility:** Extended sizes (`60`–`70` and `155`–`200`) cache locally under `loam.appearance.size` (`public/size-local.js`) if a host still enforces `75`–`150`, persist before first paint on reload, and migrate to the host once accepted.

## Reproduce

```sh
# Baseline against main (into docs/evidence/app-size)
APP_SIZE_BASELINE=1 APP_SIZE_EVIDENCE=docs/evidence/app-size \
  LOAM_PLAYWRIGHT=/path/to/playwright-core/index.mjs \
  LOAM_CHROME=/usr/bin/chromium-headless-shell \
  node test/app-size-browser.mjs

# After (full 390 & 1440, light & dark, 60% / 100% / 200% matrix + fallback check)
APP_SIZE_EVIDENCE=docs/evidence/app-size \
  LOAM_PLAYWRIGHT=/path/to/playwright-core/index.mjs \
  LOAM_CHROME=/usr/bin/chromium-headless-shell \
  node test/app-size-browser.mjs
```

## Key captures

### Before (from `main`)

- `before-390-light-75-chats.png` / `before-390-dark-75-chats.png` — `#app` capped at `345px` (`x: 23..368`) on a `390px` phone at `75%` (`--ui-scale: 0.75`).
- `before-390-light-100-chats.png` / `before-390-dark-100-chats.png` — previous `100%` (`--ui-scale: 1`).
- `before-1440-light-100-chats.png` / `before-1440-dark-100-chats.png` — previous desktop `100%` (`--ui-scale: 1`).
- `before-polish-390-{light,dark}-200-chats.png` — pre-polish 200% phone Chats list showing 3-row wrapped header (`⋯` stranded alone) and 2-column left-hugging agent grid.
- `before-polish-390-{light,dark}-60-chats.png` — pre-polish 60% phone Chats list showing 7 agents leaning left.
- `before-polish-1440-{light,dark}-200-thread.png` — pre-polish 200% desktop thread showing cramped `605px` pane (`A …` title) and wrapped composer with mic/send pushed to the far left under `+`.
- `before-rows-390-{light,dark}-200-chat-rows.png` — pre-row-fix 200% phone chat rows showing titles squeezed to `20–36%` of row width (`Gen...`, `Visual / s...`, `Host / ed...`).
- `before-rows-1440-{light,dark}-200-chats.png` — pre-row-fix 200% desktop trimmed sidebar showing titles squeezed to `34–47%` of row width (`Visuals / inside age...`).

### After (`60%`, `100%`, `200%` × `390` & `1440` × `light` & `dark` × 5 surfaces)

Every capture in `after.json` verified `#app` at full viewport width (`390px` or `1440px`), zero document overflow, zero nav text clipping, zero composer button overlap, zero mid-word breaks in chat list titles, generous narrow-list title share (`0.70` on `390-200`, `0.75` on `1440-200`), zero hover pin overlap, unchanged `80px` base row height at `60%` and `100%`, and full slider reachability:

- Phone (`390x844`, light & dark): `after-390-{light,dark}-{60,100,200}-{chats,thread,sheet,files,settings}.png`, plus scrolled `after-390-{light,dark}-200-chat-rows.png`
- Desktop (`1440x900`, light & dark): `after-1440-{light,dark}-{60,100,200}-{chats,thread,sheet,files,settings}.png`
