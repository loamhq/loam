// The app's one empty state in a real browser (2026-11-18,
// public/empty-state.js): the sprout's stem draws in and its leaves unfold
// once, and under `prefers-reduced-motion: reduce` the component renders
// whole and still, with nothing animating; its paints are the theme's own
// tokens in every first-party theme (shipped or listed), light and dark; and the empty Market draws
// it with its words and action.
//
//   LOAM_PLAYWRIGHT=…/playwright/index.mjs LOAM_CHROME=/usr/bin/chromium-headless-shell node test/empty-state-browser.mjs
import assert from 'node:assert/strict'
import { readdir } from 'node:fs/promises'
import { marketFixture } from './market-fixture.mjs'
import { LISTED_THEMES, installListedTheme } from './listed-themes.mjs'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
// The shipped looks and the first-party ones listed in the Market, installed.
const themes = [...(await readdir('public/themes', { withFileTypes: true })).filter(d => d.isDirectory()).map(d => d.name), ...LISTED_THEMES]

// What the sprout is doing, read off the browser, and every paint resolved
// against the token it names.
const read = () => globalThis.__empty.then(async ({ emptyState, EMPTY_OBJECTS }) => {
  const host = document.createElement('div'); host.className = 'empty-host'
  for (const object of EMPTY_OBJECTS) host.append(emptyState({ object, title: object, body: 'One line.' }))
  document.body.append(host)
  await new Promise(requestAnimationFrame)
  const sprout = host.querySelector('.empty-state-sprout')
  const stem = sprout.querySelector('.es-stem'), leaves = [...sprout.querySelectorAll('.es-leaf')]
  const probe = token => { const p = document.createElementNS('http://www.w3.org/2000/svg', 'rect'); p.style.fill = `var(${token})`; sprout.append(p); const v = getComputedStyle(p).fill; p.remove(); return v }
  const paints = { 'es-blob': ['fill', '--latte-foam'], 'es-sheet': ['fill', '--paper-raised'], 'es-line': ['stroke', '--paper-ink-3'], 'es-accent': ['fill', '--latte-espresso'], 'es-accent-line': ['stroke', '--latte-espresso'], 'es-soil': ['fill', '--latte-coffee-2'] }
  const wrong = []
  for (const [cls, [prop, token]] of Object.entries(paints)) for (const node of host.querySelectorAll(`.${cls}`)) {
    const want = probe(token), got = getComputedStyle(node)[prop]
    if (got !== want || got === 'none') wrong.push(`${cls} ${prop} ${got} ≠ ${token} ${want}`)
  }
  const out = {
    animations: document.getAnimations().filter(a => host.contains(a.effect?.target)).map(a => a.animationName),
    stem: { name: getComputedStyle(stem).animationName, offset: getComputedStyle(stem).strokeDashoffset },
    leaves: leaves.map(l => ({ name: getComputedStyle(l).animationName, transform: getComputedStyle(l).transform })),
    drawn: [...host.querySelectorAll('.empty-state')].every(n => n.querySelector('.es-blob') && n.querySelector('.empty-state-title').textContent),
    wrong,
  }
  host.remove()
  return out
})

const checks = []
try {
  for (const motion of ['no-preference', 'reduce']) for (const look of motion === 'reduce' ? themes : ['latte']) for (const mode of ['light', 'dark']) {
    const app = await marketFixture({ mode, look, listings: [] }), errors = []
    if (LISTED_THEMES.includes(look)) await installListedTheme(app, look)
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: mode, reducedMotion: motion })
    const page = await context.newPage(); page.setDefaultTimeout(15000)
    page.on('pageerror', e => errors.push(e.message))
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      await page.waitForFunction(look => document.documentElement.dataset.look === look, look)
      await page.evaluate(() => { globalThis.__empty = import('/empty-state.js') })
      const seen = await page.evaluate(read)
      const label = `${look} ${mode} ${motion}`
      assert.deepEqual(seen.wrong, [], `${label}: every paint is its token`)
      assert.ok(seen.drawn, `${label}: every object renders in its blob, with its title`)
      if (motion === 'reduce') {
        assert.deepEqual(seen.animations, [], `${label}: nothing animates`)
        assert.deepEqual(seen.stem, { name: 'none', offset: '0px' }, `${label}: the stem is drawn whole`)
        assert.deepEqual(seen.leaves, [{ name: 'none', transform: 'none' }, { name: 'none', transform: 'none' }], `${label}: the leaves are open`)
      } else {
        assert.deepEqual([...new Set(seen.animations)].sort(), ['es-draw', 'es-unfold'], `${label}: the stem draws in and the leaves unfold`)
        assert.equal(seen.animations.length, 3, `${label}: the sprout's three parts and nothing else move`)
      }
      if (look === 'latte' && mode === 'light') {
        // The empty Market draws it whole: the sprout, the Market's words, its one action.
        await page.goto(`${app.url}/?modal=market`)
        const empty = page.locator('.market-build-whole .empty-state.empty-state-large')
        await empty.waitFor()
        assert.equal(await empty.locator('.empty-state-sprout').count(), 1)
        assert.equal(await empty.locator('#market-build-title').textContent(), 'The Market is just opening')
        assert.equal(await empty.locator('.empty-state-action .market-build-go').count(), 1)
        if (motion === 'reduce') assert.deepEqual(await empty.evaluate(n => n.getAnimations({ subtree: true }).length), 0, 'the empty Market is still')
        else {
          await page.waitForTimeout(2000)
          assert.equal(await empty.evaluate(n => getComputedStyle(n.querySelector('.es-stem')).strokeDashoffset), '0px', 'the stem has drawn in, and stays')
        }
      }
      assert.deepEqual(errors, [], `${label}: page errors`)
      checks.push(label)
    } finally { await context.close(); await app.close() }
  }
} finally { await browser.close() }
console.log(`PASS ${checks.length}: ${checks.join(', ')}`)
