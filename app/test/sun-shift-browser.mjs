// Real page/server/relay, synthetic appearance host, live Chromium clock & contrast sampling.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
import { mergeAppearance } from '../public/theme-options.js'

const out = process.env.SUN_SHIFT_EVIDENCE || path.join(process.env.TMPDIR, 'sun-shift-evidence')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({
  executablePath: process.env.LOAM_CHROME,
  headless: true,
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
})

const place = { latitude: 37.8, longitude: -122.4, source: 'geolocation', timeZone: 'America/Los_Angeles' }
const app = await oneProductFixture()
let values, refuseTransition = false
const writes = []
const reset = (over = {}) => {
  values = {
    phone: { mode: 'sun', transition: 'normal', size: 100, theme: 'latte', ...over },
    desktop: { mode: 'sun', transition: 'normal', size: 100, theme: 'latte', ...over },
    location: { ...place },
  }
  writes.length = 0
  refuseTransition = false
}
const json = (res, body, status = 200) => {
  res.writeHead(status, { 'content-type': 'application/json' })
  res.end(JSON.stringify(body))
}
app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, values)
app.routes['PATCH /v1/settings/appearance'] = (req, res, url, raw) => {
  const patch = JSON.parse(raw)
  writes.push(patch)
  if (refuseTransition && Object.values(patch).some((v) => v?.transition !== undefined)) {
    return json(res, { error: 'transition is not supported' }, 400)
  }
  for (const kind of ['phone', 'desktop']) {
    if (patch[kind]) values[kind] = mergeAppearance(values[kind], patch[kind])
  }
  if (patch.location) values.location = patch.location
  json(res, values)
}

function parseRgb(str) {
  const m = str.match(/[\d.]+/g).map(Number)
  return [m[0], m[1], m[2]]
}
const lin = (v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)
const lum = ([r, g, b]) => 0.2126 * lin(r / 255) + 0.7152 * lin(g / 255) + 0.0722 * lin(b / 255)
const contrast = (a, b) => {
  const la = lum(a), lb = lum(b)
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
}

const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
async function makeContext(width, { reducedMotion = 'no-preference' } = {}) {
  const ctx = await browser.newContext({
    viewport: { width, height: width === 390 ? 844 : 1000 },
    isMobile: width === 390,
    hasTouch: width === 390,
    timezoneId: 'America/Los_Angeles',
    locale: 'en-US',
    colorScheme: 'light',
    reducedMotion,
  })
  await ctx.route('**/app.js', (route) =>
    route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__shiftTest = { theme, threads, goSettings, schedule };' })
  )
  const page = await ctx.newPage()
  page.setDefaultTimeout(15000)
  return { ctx, page }
}

const results = []
try {
  // 1. Settings pane control visibility, geometry, persistence & screenshots on phone (390) and desktop (1440).
  for (const width of [390, 1440]) {
    reset({ mode: 'system' })
    const { ctx, page } = await makeContext(width)
    const errors = []
    page.on('pageerror', (e) => errors.push(e.message))
    try {
      await page.clock.install({ time: new Date('2026-11-13T12:00:00-08:00') })
      await page.goto(app.url)
      await page.waitForFunction(() => globalThis.__shiftTest)
      await page.evaluate(() => __shiftTest.goSettings('appearance'))
      await page.getByRole('button', { name: 'Sun', exact: true }).waitFor()
      assert.equal(await page.locator('.sun-transition').count(), 0)

      await page.getByRole('button', { name: 'Sun', exact: true }).click()
      await page.locator('.sun-transition').waitFor()
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame) })
      await page.locator('.sun-transition').scrollIntoViewIfNeeded()
      assert.equal(await page.getByRole('button', { name: 'Normal · 10 s', exact: true }).getAttribute('aria-pressed'), 'true')
      await page.screenshot({ path: path.join(out, `settings-sun-transition-${width}.png`) })

      for (const size of [100, 150]) {
        await page.evaluate((s) => __shiftTest.theme.set({ size: s }), size)
        await page.waitForFunction(() => !__shiftTest.theme.status.saving)
        await page.locator('.sun-transition').scrollIntoViewIfNeeded()
        const bounds = await page.locator('.sun-transition-modes button').evaluateAll((nodes) =>
          nodes.map((n) => {
            const b = n.getBoundingClientRect(), r = document.createRange()
            r.selectNodeContents(n)
            const t = r.getBoundingClientRect()
            return { label: n.textContent, left: b.left, right: b.right, height: b.height, textLeft: t.left, textRight: t.right }
          })
        )
        assert.equal(bounds.length, 3)
        assert.ok(bounds.every((b) => b.left >= 0 && b.right <= width && b.textLeft >= b.left && b.textRight <= b.right && (width !== 390 || b.height >= 43.9)), JSON.stringify({ width, size, bounds }))
      }
      await page.evaluate(() => __shiftTest.theme.set({ size: 100 }))
      await page.waitForFunction(() => !__shiftTest.theme.status.saving)

      await page.getByRole('button', { name: 'Fast · 3 s', exact: true }).click()
      await page.waitForFunction(() => !__shiftTest.theme.status.saving)
      const kind = width === 390 ? 'phone' : 'desktop'
      assert.equal(values[kind].transition, 'fast')

      // Persona update arrives via refresh().
      values[kind].transition = 'long'
      await page.evaluate(async () => { await __shiftTest.theme.refresh(); await new Promise(requestAnimationFrame) })
      assert.equal(await page.getByRole('button', { name: 'Long · 20 s', exact: true }).getAttribute('aria-pressed'), 'true')
      assert.deepEqual(errors, [])
      console.log(`PASS settings ${width}: visibility, bounds at 100/150%, save, persona refresh`)
    } finally {
      await ctx.close()
    }
  }

  // 2. Live 10 s boundary contrast sampling + before/mid/after screenshots at 390 and 1440 wide.
  for (const width of [390, 1440]) {
    reset({ mode: 'sun', transition: 'normal', theme: 'latte' })
    const { ctx, page } = await makeContext(width)
    const errors = []
    page.on('pageerror', (e) => errors.push(e.message))
    try {
      await page.clock.install({ time: new Date('2026-11-13T12:00:00-08:00') })
      await page.goto(app.url)
      await page.waitForFunction(() => globalThis.__shiftTest)
      await page.evaluate(() => __shiftTest.threads.open('a-quieter-week'))
      await page.locator('.tt-message').first().waitFor()
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame) })

      const boundary = await page.evaluate(() => __shiftTest.theme.solar.end)
      await page.clock.pauseAt(new Date(Math.ceil(boundary) - 200))
      await page.evaluate(() => __shiftTest.theme.apply())
      assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), 'light')
      assert.equal(await page.evaluate(() => __shiftTest.theme.shifting), false)
      await page.screenshot({ path: path.join(out, `shift-${width}-before.png`) })

      // Cross the boundary by 250ms to start the 10 s shift.
      await page.clock.runFor(250)
      assert.equal(await page.evaluate(() => __shiftTest.theme.shifting), true)
      assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), 'light')

      const samples = []
      let midCaptured = false
      const stepMs = 50
      for (let elapsed = 0; elapsed <= 10200; elapsed += stepMs) {
        const sample = await page.evaluate(() => {
          const bodyStyle = getComputedStyle(document.body)
          const textEl = document.querySelector('.tt-message .md p, .tt-message, body')
          const textStyle = getComputedStyle(textEl)
          return {
            theme: document.documentElement.dataset.theme,
            shifting: __shiftTest.theme.shifting,
            bg: bodyStyle.backgroundColor,
            fg: textStyle.color,
            meta: document.querySelector('meta[name="theme-color"]')?.getAttribute('content'),
          }
        })
        const ratio = contrast(parseRgb(sample.fg), parseRgb(sample.bg))
        samples.push({ elapsed, ratio, ...sample })
        if (!midCaptured && elapsed >= 3800) {
          midCaptured = true
          // At ~3.8s: surface has shifted visibly toward grey, ink has not flipped yet.
          assert.equal(sample.theme, 'light')
          assert.ok(ratio >= 3.5, `Expected high contrast before flip at 3.8s, got ${ratio}`)
          await page.screenshot({ path: path.join(out, `shift-${width}-mid.png`) })
        }
        await page.clock.runFor(stepMs)
      }

      const below3Samples = samples.filter((s) => s.ratio < 3)
      const secondsBelow3 = (below3Samples.length * stepMs) / 1000
      const minContrast = Math.min(...samples.map((s) => s.ratio))
      const midpointFlip = samples.find((s) => s.theme === 'dark')
      assert.ok(midpointFlip && midpointFlip.elapsed >= 4800 && midpointFlip.elapsed <= 5200, `Unexpected midpoint flip at ${midpointFlip?.elapsed}`)
      assert.ok(secondsBelow3 < 0.5, `Expected < 0.5s below 3:1, got ${secondsBelow3}s`)

      const finalState = await page.evaluate(() => ({
        theme: document.documentElement.dataset.theme,
        shifting: __shiftTest.theme.shifting,
        styleAttr: document.documentElement.getAttribute('style'),
        meta: document.querySelector('meta[name="theme-color"]')?.getAttribute('content'),
      }))
      assert.equal(finalState.theme, 'dark')
      assert.equal(finalState.shifting, false)
      assert.equal(finalState.styleAttr, '--ui-scale: 1;')
      assert.equal(finalState.meta, '#1A1917')
      await page.screenshot({ path: path.join(out, `shift-${width}-after.png`) })
      assert.deepEqual(errors, [])

      results.push({ width, secondsBelow3, minContrast: Number(minContrast.toFixed(2)), midpointAtMs: midpointFlip.elapsed, sampleCount: samples.length })
      console.log(`PASS shift ${width}: 10s shift, ${secondsBelow3.toFixed(2)}s below 3:1 (min ${minContrast.toFixed(2)}:1), midpoint at ${midpointFlip.elapsed}ms, clean final state`)
    } finally {
      await ctx.close()
    }
  }

  // 3. Plugin theme (Autumn) with cover image & non-core tokens switches image at midpoint and cleans up.
  reset({ mode: 'sun', transition: 'fast', theme: 'autumn' })
  {
    const { ctx, page } = await makeContext(1440)
    try {
      await page.clock.install({ time: new Date('2026-11-13T12:00:00-08:00') })
      await page.goto(app.url)
      await page.waitForFunction(() => globalThis.__shiftTest)
      await page.evaluate(() => __shiftTest.goSettings('appearance'))
      await page.locator('.theme-preview[data-look="autumn"] .theme-cover').waitFor()
      const boundary = await page.evaluate(() => __shiftTest.theme.solar.end)
      await page.clock.pauseAt(new Date(Math.ceil(boundary) - 100))
      await page.evaluate(() => __shiftTest.theme.apply())
      const coverSrc = () => page.locator('.theme-preview[data-look="autumn"] .theme-cover').getAttribute('src')
      assert.match(await coverSrc(), /cover\.webp$/)
      await page.clock.runFor(150)
      assert.equal(await page.evaluate(() => __shiftTest.theme.shifting), true)
      await page.clock.runFor(1000) // ~1.0s of 3s: pre-midpoint
      assert.match(await coverSrc(), /cover\.webp$/)
      await page.clock.runFor(800) // ~1.8s of 3s: past midpoint
      assert.match(await coverSrc(), /cover-dark\.webp$/)
      await page.clock.runFor(1500) // done
      assert.equal(await page.evaluate(() => __shiftTest.theme.shifting), false)
      assert.equal(await page.evaluate(() => document.documentElement.getAttribute('style')), '--ui-scale: 1;')
      console.log('PASS plugin theme (autumn): tokens shift, dark cover switches at midpoint, clean end state')
    } finally {
      await ctx.close()
    }
  }

  // 4. Reduced motion snaps immediately; manual change mid-shift cancels cleanly; older host caches locally.
  {
    reset({ mode: 'sun', transition: 'normal', theme: 'latte' })
    const { ctx, page } = await makeContext(1440, { reducedMotion: 'reduce' })
    try {
      await page.clock.install({ time: new Date('2026-11-13T12:00:00-08:00') })
      await page.goto(app.url)
      await page.waitForFunction(() => globalThis.__shiftTest)
      const boundary = await page.evaluate(() => __shiftTest.theme.solar.end)
      await page.clock.pauseAt(new Date(Math.ceil(boundary) - 100))
      await page.evaluate(() => __shiftTest.theme.apply())
      await page.clock.runFor(150)
      assert.equal(await page.evaluate(() => __shiftTest.theme.shifting), false)
      assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), 'dark')
      assert.equal(await page.evaluate(() => document.documentElement.getAttribute('style')), '--ui-scale: 1;')
    } finally {
      await ctx.close()
    }

    reset({ mode: 'sun', transition: 'normal', theme: 'latte' })
    const { ctx: ctx2, page: page2 } = await makeContext(1440)
    try {
      await page2.clock.install({ time: new Date('2026-11-13T12:00:00-08:00') })
      await page2.goto(app.url)
      await page2.waitForFunction(() => globalThis.__shiftTest)
      const boundary = await page2.evaluate(() => __shiftTest.theme.solar.end)
      await page2.clock.pauseAt(new Date(Math.ceil(boundary) - 100))
      await page2.evaluate(() => __shiftTest.theme.apply())
      await page2.clock.runFor(150)
      await page2.clock.runFor(2000)
      assert.equal(await page2.evaluate(() => __shiftTest.theme.shifting), true)
      await page2.evaluate(() => __shiftTest.theme.set({ mode: 'light' }))
      assert.equal(await page2.evaluate(() => __shiftTest.theme.shifting), false)
      assert.equal(await page2.evaluate(() => document.documentElement.dataset.theme), 'light')
      assert.equal(await page2.evaluate(() => document.documentElement.getAttribute('style')), '--ui-scale: 1;')
      await page2.clock.resume()

      // Older host refuses transition: saves locally without error and migrates later.
      refuseTransition = true
      await page2.evaluate(() => __shiftTest.theme.set({ mode: 'sun', transition: 'fast' }))
      await page2.waitForFunction(() => !__shiftTest.theme.status.saving)
      assert.equal(await page2.evaluate(() => __shiftTest.theme.status.error), null)
      assert.equal(await page2.evaluate(() => __shiftTest.theme.status.localOnly), true)
      assert.equal(await page2.evaluate(() => JSON.parse(localStorage.getItem('loam.appearance.sun')).desktop.transition), 'fast')
      refuseTransition = false
      await page2.reload()
      await page2.waitForFunction(() => globalThis.__shiftTest && !__shiftTest.theme.status.saving)
      assert.equal(values.desktop.transition, 'fast')
      assert.equal(await page2.evaluate(() => localStorage.getItem('loam.appearance.sun')), null)
      console.log('PASS reduced motion, manual interruption cleanup, and older-host transition fallback')
    } finally {
      await ctx2.close()
    }
  }

  await writeFile(path.join(out, 'sun-shift.json'), JSON.stringify({ fixtureOnly: true, results }, null, 2) + '\n')
} finally {
  await browser.close()
  await app.close()
}
