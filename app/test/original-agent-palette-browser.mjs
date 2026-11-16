// Real page/server/relay, synthetic conversations only. Optional audit dependency.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
import { loadThemes } from '../src/themes.js'
import { appearanceValues } from '../src/appearance.js'
import { contrast } from '../tools/theme-contrast.mjs'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.PALETTE_EVIDENCE || path.join(process.env.TMPDIR, 'original-agent-palette')
await mkdir(out, { recursive: true })
const expected = ['#6E7A52', '#4F6478', '#B0684A', '#7A5A72', '#4E7A72', '#9C4F44', '#8A7D4A', '#5C6E8A', '#8C6A4E', '#6B8A6E', '#8A5E80']
const rgb = hex => `rgb(${hex.slice(1).match(/../g).map(n => parseInt(n, 16)).join(', ')})`
const app = await oneProductFixture(), themes = (await loadThemes()).themes
const values = appearanceValues(null, themes), results = []
app.routes['GET /v1/settings/appearance'] = (req, res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(values)) }
const source = await readFile('public/app.js', 'utf8')
const browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
try {
  for (const width of [390, 1440]) for (const theme of ['latte', 'plain']) for (const mode of ['light', 'dark']) {
    const kind = width === 390 ? 'phone' : 'desktop'
    values[kind] = { mode, theme, size: 100, options: { latte: { 'agent-colours': 'palette', 'agent-shapes': 'distinct' } } }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode, isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce' })
    try {
      await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__paletteTest={theme};' }))
      const page = await context.newPage(), errors = []
      page.on('pageerror', e => errors.push(e.message))
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor(); await page.evaluate(() => document.fonts.ready)
      const faces = await page.evaluate(async () => {
        const { blob } = await import('/ui.js')
        const { agentLook, setAvatarColours } = await import('/sessions.js')
        const slots = [49, 51, 52, 54, 56, 58, 60, 62, 64, 66, 68]
        const ids = ['c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm']
        const measure = look => {
          const face = blob(look, { size: 60, font: 24 }); document.body.append(face)
          const s = getComputedStyle(face), result = { hue: s.backgroundColor, ink: s.color }
          face.remove(); return result
        }
        const fallback = ids.map(id => measure(agentLook(id)))
        const aliases = slots.map(slot => { setAvatarColours({ sample: `c${slot}` }); return measure(agentLook('sample')) })
        setAvatarColours()
        return { fallback, aliases }
      })
      const paint = expected.map(hue => ({ hue: rgb(hue), ink: contrast('#FFFFFF', hue) < 4.5 ? 'rgb(11, 11, 11)' : 'rgb(255, 255, 255)' }))
      for (const hue of expected) assert.ok(contrast(contrast('#FFFFFF', hue) < 4.5 ? '#0B0B0B' : '#FFFFFF', hue) >= 4.5, `small avatar contrast: ${hue}`)
      assert.deepEqual(faces.fallback, paint)
      assert.deepEqual(faces.aliases, paint)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      await page.screenshot({ path: path.join(out, `chats-${width}-${theme}-${mode}.png`) })
      // Existing DOM follows mode and colour options immediately.
      const face = page.locator('[data-agent-menu] .blob').first()
      const before = await face.evaluate(n => { globalThis.__retainedFace = n; const s = getComputedStyle(n); return [s.backgroundColor, s.color] })
      await page.evaluate(mode => __paletteTest.theme.set({ mode }), mode === 'light' ? 'dark' : 'light')
      assert.deepEqual(await face.evaluate(n => [getComputedStyle(n).backgroundColor, getComputedStyle(n).color]), before)
      assert.ok(await face.evaluate(n => n === __retainedFace))
      if (theme === 'latte') {
        await page.evaluate(() => __paletteTest.theme.set({ options: { latte: { 'agent-colours': 'latte-tones' } } }))
        assert.equal(await face.evaluate(n => getComputedStyle(n).backgroundColor), 'rgb(244, 236, 224)')
        assert.equal(await face.evaluate(n => getComputedStyle(n).color), 'rgb(42, 28, 19)')
        await page.evaluate(() => __paletteTest.theme.set({ options: { latte: { 'agent-colours': 'palette' } } }))
        assert.deepEqual(await face.evaluate(n => [getComputedStyle(n).backgroundColor, getComputedStyle(n).color]), before)
      }
      assert.deepEqual(errors, [])
      results.push({ width, theme, mode, ...faces, retainedFace: true, latteTonesPreserved: theme === 'latte' ? true : null, overflow: false, errors })
    } finally { await context.close() }
  }
  await writeFile(path.join(out, 'checks.json'), JSON.stringify({ cases: results, contrast: expected.map(hue => ({ hue, initials: contrast(contrast('#FFFFFF', hue) < 4.5 ? '#0B0B0B' : '#FFFFFF', hue), minimum: 4.5 })) }, null, 2) + '\n')
  console.log('PASS: 8 chat-list captures; all 11 fallback hues and all 11 legacy aliases are identical across Latte/Plain and light/dark, with readable initials; retained faces survive mode changes; Latte tones preserved; no overflow or page errors.')
} finally { await browser.close(); await app.close() }
