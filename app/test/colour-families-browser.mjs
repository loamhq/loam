// The colour-families checker reads every colour syntax a computed style can
// come back in. Chromium reports a colour mixed or written in oklab, oklch or
// lab as `oklab(…)`/`oklch(…)`, and one mixed in srgb as `color(srgb …)` with
// 0–1 channels; the checker used to take the first three numbers as 0–255
// bytes, so an ink like `color-mix(in oklab, …)` read as near-black and a
// ground written that way was never recognised (2026-11-17).
//
//   LOAM_PLAYWRIGHT=…/playwright/index.mjs node app/test/colour-families-browser.mjs
import assert from 'node:assert/strict'
import { inspectColourFamilies } from '../tools/colour-families.mjs'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
try {
  const page = await browser.newPage()
  // A Latte-shaped set of tokens: a light paper, its inks, a dark espresso.
  await page.setContent(`<!doctype html><html data-look="latte"><head><style>
    :root { --paper-page: #f7f3ee; --paper-pane: oklab(0.97 0 0.004); --paper-raised: color(srgb 0.99 0.98 0.96);
      --paper-card: #efe8df; --paper-hover: #e9e1d6; --paper-ink: #1c1b19; --paper-ink-2: #4a453f; --paper-ink-3: #6d665e;
      --latte-coffee: #f4e9dc; --latte-coffee-2: #e7d5c0; --latte-espresso-ink: #fffaf3;
      --latte-foam: #e6d8c6; --latte-track: #d9c8b2; --latte-hover: #ddcbb4; --latte-disabled: #cfc4b6;
      --latte-espresso: #5b3a22; --latte-espresso-hover: #4e311c; --latte-espresso-pressed: #432a18; }
    body { margin: 0; background: var(--paper-page); font: 16px sans-serif; }
    div { padding: 4px; }
  </style></head><body>
    <div id="mixed-ink" style="background: var(--paper-page); color: color-mix(in oklab, var(--paper-ink) 85%, var(--paper-page))">Mixed in oklab, dark enough</div>
    <div id="srgb-ink" style="background: var(--paper-page); color: color-mix(in srgb, var(--paper-ink) 80%, var(--paper-page))">Mixed in srgb, dark enough</div>
    <div id="faint-oklab" style="background: var(--paper-page); color: oklab(0.85 0 0.01)">Too faint, written in oklab</div>
    <div id="faint-color" style="background: var(--paper-page); color: color(srgb 0.8 0.78 0.76)">Too faint, written as color()</div>
    <div id="pane" style="background: var(--paper-pane)"><span id="cross-oklab" style="color: var(--latte-espresso-ink)">Latte ink on an oklab paper</span></div>
    <div id="raised" style="background: var(--paper-raised)"><span id="cross-color" style="color: var(--latte-espresso-ink)">Latte ink on a color() paper</span></div>
    <div style="background: var(--latte-espresso)"><span id="good-latte" style="color: var(--latte-espresso-ink)">Latte ink on espresso</span></div>
  </body></html>`)
  const found = await page.evaluate(inspectColourFamilies)
  const by = text => found.find(v => v.text.startsWith(text))
  // Readable inks in either mixing space are not reported (they read as
  // near-black before, which passed by luck; the faint ones below did not fail).
  assert.equal(by('Mixed in oklab'), undefined)
  assert.equal(by('Mixed in srgb'), undefined)
  assert.equal(by('Latte ink on espresso'), undefined)
  // Faint inks are caught whatever their syntax.
  assert.equal(by('Too faint, written in oklab')?.issue, 'contrast')
  assert.equal(by('Too faint, written as color()')?.issue, 'contrast')
  // Grounds written in oklab or color() are recognised as the paper they are.
  assert.equal(by('Latte ink on an oklab paper')?.ground, 'paper-pane')
  assert.equal(by('Latte ink on a color() paper')?.ground, 'paper-raised')
  assert.equal(by('Latte ink on an oklab paper')?.issue, 'family')
  console.log(`colour-families-browser: ok (${found.length} expected findings)`)
} finally { await browser.close() }
