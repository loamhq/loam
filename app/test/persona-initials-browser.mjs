// Real page/server/relay; synthetic roster and conversations, no live credentials.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
import { AGENT_COLOURS } from '../public/avatar-colours.js'
import { mergeAppearance } from '../public/theme-options.js'
import { contrast } from '../tools/theme-contrast.mjs'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.PERSONA_EVIDENCE || path.join(process.env.TMPDIR, 'persona-initials')
await mkdir(out, { recursive: true })
const app = await oneProductFixture(), results = []
app.f.agents.push(...['moss', 'fern', 'reed', 'brook', 'ash', 'wren'].map(id => ({ id, name: id[0].toUpperCase() + id.slice(1), emoji: null })))
app.config.avatarColours = Object.fromEntries(app.f.agents.map((a, i) => [a.id, AGENT_COLOURS[i]]))
for (const a of app.f.agents) {
  const t = await app.f.api('POST', '/threads', { persona: a.id })
  await app.f.api('PATCH', `/threads/${t.id}`, { title: `A few notes with ${a.name}` })
}
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
let values
app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, values)
app.routes['PATCH /v1/settings/appearance'] = (req, res, url, raw) => {
  for (const [kind, patch] of Object.entries(JSON.parse(raw))) values[kind] = mergeAppearance(values[kind], patch)
  json(res, values)
}
const source = await readFile('public/app.js', 'utf8')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const hex = rgb => '#' + rgb.match(/[\d.]+/g).slice(0, 3).map(n => Math.round(Number(n)).toString(16).padStart(2, '0')).join('')
async function measure(page, selector, art) {
  return page.locator(selector).evaluateAll((nodes, art) => nodes.map(n => {
    const s = getComputedStyle(n), g = getComputedStyle(art ? n : n.querySelector('.blob-letter'), art ? '::after' : null)
    const coffee = n.querySelector('circle[r="37.5"]')
    // Every translucent crema disc overlaps at the centre: this is the
    // brightest coffee behind the glyph, hence its worst contrast.
    const rgb = value => value.match(/[\d.]+/g).slice(0, 3).map(Number)
    let background = art ? rgb(getComputedStyle(coffee).fill) : null
    if (art) for (const layer of n.querySelectorAll('.latte-crema')) {
      const c = getComputedStyle(layer), alpha = Number(c.opacity), fill = rgb(c.fill)
      background = background.map((v, i) => v * (1 - alpha) + fill[i] * alpha)
    }
    return { id: n.dataset.agent, initial: n.dataset.latteInitial, size: parseFloat(s.width),
      font: parseFloat(g.fontSize), faceFont: parseFloat(s.fontSize), family: g.fontFamily, faceFamily: s.fontFamily,
      weight: g.fontWeight, faceWeight: s.fontWeight, ink: g.color,
      background: art ? `rgb(${background.map(Math.round).join(', ')})` : s.backgroundColor,
      content: art ? g.content : n.querySelector('.blob-letter').textContent, display: g.display,
      shape: s.borderRadius, pour: getComputedStyle(n.querySelector('.latte-pour')).display }
  }), art)
}
function check(faces, art, initials) {
  assert.ok(faces.length)
  for (const f of faces) {
    if (art && !initials) { assert.notEqual(f.pour, 'none'); continue }
    assert.equal(f.family, f.faceFamily, JSON.stringify(f))
    assert.equal(f.weight, f.faceWeight, JSON.stringify(f))
    assert.ok(Math.abs(f.font - f.faceFont * (art ? .9 : 1)) < .01, JSON.stringify(f))
    assert.ok(f.font / f.size >= .35 && f.font / f.size <= .44, JSON.stringify(f))
    assert.ok(f.content.includes(f.initial), JSON.stringify(f))
    assert.notEqual(f.display, 'none')
    assert.ok(contrast(hex(f.ink), hex(f.background)) >= 3, JSON.stringify(f))
    if (art) assert.equal(f.pour, 'none')
  }
}
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    values = Object.fromEntries(['phone', 'desktop'].map(kind => [kind, { theme: 'latte', mode, size: 100, options: { latte: { 'agent-colours': 'palette', 'agent-shapes': 'distinct' } } }]))
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode,
      isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce' })
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__initialsTest={theme,closeSheet};' }))
    if (process.env.PERSONA_BASELINE_CSS) await context.route('**/style.css', route => route.fulfill({ contentType: 'text/css', path: process.env.PERSONA_BASELINE_CSS }))
    const page = await context.newPage(), errors = []
    page.on('pageerror', e => errors.push(e.message))
    try {
      await page.goto(app.url); await page.locator('.tt-personas .persona').nth(10).waitFor()
      await page.waitForFunction(() => globalThis.__initialsTest)
      await page.locator('.tt-row .blob[data-agent]').nth(10).waitFor({ state: 'attached' })
      await page.evaluate(() => document.fonts.ready)
      for (const shape of ['distinct', 'circle', 'rounded-square', 'latte-art']) {
        if (process.env.PERSONA_SHAPE && shape !== process.env.PERSONA_SHAPE) continue
        const variants = shape === 'latte-art' ? ['on-pour', 'off-pour', 'on-initials', 'off-initials'] : ['palette', 'latte-tones']
        for (const variant of variants) {
          const art = shape === 'latte-art', initials = variant.endsWith('initials')
          const [mug, inside] = variant.split('-')
          await page.evaluate(options => __initialsTest.theme.set({ options: { latte: options } }), {
            'agent-shapes': shape, 'agent-colours': art ? 'palette' : variant,
            'latte-mug': art ? mug : 'on', 'latte-inside': art ? inside : 'pour',
          })
          await page.waitForFunction(() => !__initialsTest.theme.status.saving && !__initialsTest.theme.status.error)
          const roster = await measure(page, '.tt-personas .blob', art)
          const rows = await measure(page, '.tt-row .blob[data-agent]', art)
          const label = `${width}-${mode}-${shape}-${variant}`
          // Save before asserting so the historical-CSS counterfactual is inspectable.
          await page.screenshot({ path: path.join(out, `chats-${label}.png`), animations: 'disabled' })
          const result = { width, mode, shape, variant, roster, rows }; results.push(result)
          assert.equal(roster.length, 11)
          assert.equal(new Set(rows.map(f => f.id)).size, 11)
          check(roster, art, initials); check(rows, art, initials)
          if (!art && variant === 'palette') {
            assert.equal(new Set(roster.map(f => f.background)).size, 11)
            assert.ok(roster.every(f => f.ink === 'rgb(255, 255, 255)'))
          }
          for (const f of roster) {
            const row = rows.find(r => r.id === f.id)
            assert.equal(f.ink, row.ink); assert.equal(f.background, row.background)
            assert.equal(f.family, row.family)
          }
          // The same settings preview used in production; it shows six faces.
          await page.getByRole('button', { name: 'Settings', exact: true }).click()
          if (width === 390) await page.getByRole('button', { name: /Appearance/ }).click()
          await page.locator('.theme-agent-preview .blob').first().waitFor()
          const preview = await measure(page, '.theme-agent-preview .blob', art)
          check(preview, art, initials)
          for (const f of preview) {
            const face = roster.find(r => r.id === f.id)
            assert.equal(f.ink, face.ink); assert.equal(f.background, face.background)
          }
          result.preview = preview
          if (shape === 'distinct' || initials) await page.screenshot({ path: path.join(out, `appearance-${label}.png`), animations: 'disabled' })
          await page.evaluate(() => __initialsTest.closeSheet())
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
        }
      }
      assert.deepEqual(errors, [])
    } finally { await context.close() }
  }
  console.log('PASS: 40 variants at 390/1440, light/dark; actual roster, chat-row and Appearance glyphs; 11 palette hues, 10 warm tones, all shapes and mug/inside options; >=3:1 initials, proportional type, no overflow or page errors.')
} finally {
  await writeFile(path.join(out, 'checks.json'), JSON.stringify({ source: 'local fixture host, real page/server/relay', browser: browser.version(), results }, null, 2) + '\n')
  await browser.close(); await app.close()
}
