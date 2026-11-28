# App size: full-width scaling, 0.9-base default, and 60%–200% range

## Summary

- **Full-width `#app` at every scale:** Removed `#app`'s legacy `max-width: 460px; margin: 0 auto;` ceiling. Previously, zooming a 390px phone below ~85% made `390 / scale > 460`, shrinking the Chats list, thread view, Files, Apps, and bottom tab bar into a centred column with empty side bands (`345px` wide at `75%`, `248px` wide at `0.54`), while top-layer sheets stayed edge-to-edge.
- **0.9-base default:** `100%` in the UI now applies `--ui-scale: 0.9` (`sizeToScale(size) = Math.round(size * 9) / 1000`), spanning CSS `zoom` `0.54` (`60%`) to `1.80` (`200%`). `uiScale()` in `public/scale.js` accepts `0.5`–`2` so `layoutPixels()` stays accurate across the full range.
- **60%–200% range and natural steps:** Slider uses `min: 60`, `max: 200`, `step: 5`. The `−`/`+` buttons and desktop `Ctrl/Cmd` shortcuts step by 5% up to `150%` and by 10% above `150%` (`stepSize()`).
- **200% phone usability (`390px`):**
  - `.nav` compacts inside `@container (max-width: 240px)` so `Chats`, `Files`, and `Apps` fit without clipping.
  - `.composer-actions` wraps cleanly and `.composer-box .modelbtn` caps at `calc(100% - 48px)` with `overflow: hidden` so the composer stays on two compact rows with no button overlap.
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

### After (`60%`, `100%`, `200%` × `390` & `1440` × `light` & `dark` × 5 surfaces)

Every capture in `after.json` verified `#app` at full viewport width (`390px` or `1440px`), zero document overflow, zero nav text clipping, zero composer button overlap, and full slider reachability:

- Phone (`390x844`, light & dark): `after-390-{light,dark}-{60,100,200}-{chats,thread,sheet,files,settings}.png`
- Desktop (`1440x900`, light & dark): `after-1440-{light,dark}-{60,100,200}-{chats,thread,sheet,files,settings}.png`
