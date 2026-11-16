// Real page/server/relay, synthetic appearance host, Chromium geolocation/clock.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
import { mergeAppearance } from '../public/theme-options.js'
const out = process.env.SUN_EVIDENCE
if (!out) throw new Error('Set SUN_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const app = await oneProductFixture(), results = [], writes = []
const place = { latitude: 37.8, longitude: -122.4, source: 'geolocation', timeZone: 'America/Los_Angeles' }
let values, refuse = false
const reset = (mode = 'sun') => { values = { phone: { mode, size: 100, theme: 'latte' }, desktop: { mode, size: 100, theme: 'latte' }, location: { ...place } }; writes.length = 0; refuse = false }
const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, values)
app.routes['PATCH /v1/settings/appearance'] = (req, res, url, raw) => {
  const patch = JSON.parse(raw); writes.push(patch)
  if (refuse && (patch.location || Object.values(patch).some(v => v.mode === 'sun'))) return json(res, { error: 'sun is not supported' }, 400)
  for (const kind of ['phone', 'desktop']) if (patch[kind]) values[kind] = mergeAppearance(values[kind], patch[kind])
  if (patch.location) values.location = patch.location
  json(res, values)
}
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
async function context(width, instant, denied = false) {
  const ctx = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, isMobile: width === 390, hasTouch: width === 390, timezoneId: 'America/Los_Angeles', locale: 'en-US', colorScheme: 'dark', reducedMotion: 'reduce', permissions: denied ? [] : ['geolocation'], geolocation: { latitude: 37.7749, longitude: -122.4194 } })
  await ctx.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__sunTest = { theme, goSettings };' }))
  await ctx.addInitScript(({ denied }) => {
    globalThis.__locationAsks = 0
    const original = navigator.geolocation.getCurrentPosition.bind(navigator.geolocation)
    navigator.geolocation.getCurrentPosition = (ok, fail, options) => { __locationAsks++; if (denied) fail({ code: 1 }); else original(ok, fail, options) }
    new PerformanceObserver(list => {
      if (list.getEntries().some(e => e.name === 'first-contentful-paint')) globalThis.__sunFirstPaint = document.documentElement.dataset.theme
    }).observe({ type: 'paint', buffered: true })
  }, { denied })
  const page = await ctx.newPage()
  page.setDefaultTimeout(10000)
  await page.clock.setFixedTime(new Date(instant))
  return { ctx, page }
}
async function open(page) {
  await page.goto(app.url); await page.waitForFunction(() => globalThis.__sunTest)
  await page.evaluate(() => __sunTest.goSettings('appearance'))
  await page.getByRole('button', { name: 'Sun', exact: true }).waitFor()
  await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame) })
}
async function saved(page) { await page.waitForFunction(() => !__sunTest.theme.status.saving && !__sunTest.theme.status.locating) }
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    reset()
    const instant = `2026-11-13T${mode === 'light' ? '12' : '23'}:00:00-08:00`
    const { ctx, page } = await context(width, instant), errors = []
    page.on('pageerror', e => errors.push(e.message))
    try {
      await open(page)
      await page.waitForFunction(() => globalThis.__sunFirstPaint)
      assert.equal(await page.evaluate(() => __sunFirstPaint), mode)
      assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), mode)
      assert.equal(await page.evaluate(() => __locationAsks), 0)
      await page.getByRole('button', { name: 'Sun', exact: true }).click(); await saved(page)
      assert.equal(await page.evaluate(() => __locationAsks), 0)
      await page.locator('.appearance-sun').scrollIntoViewIfNeeded()
      const geometry = await page.locator('.sun-modes button').evaluateAll(nodes => nodes.map(n => { const r = n.getBoundingClientRect(), s = getComputedStyle(n); return { label: n.textContent, x: r.x, y: r.y, width: r.width, height: r.height, right: r.right, background: s.backgroundColor, border: s.borderWidth } }))
      assert.equal(geometry.length, 4)
      assert.ok(geometry.every(r => Math.abs(r.y - geometry[0].y) < 1 && r.x >= 0 && r.right <= width && r.height >= 44), JSON.stringify(geometry))
      assert.ok(geometry.filter(r => r.label !== 'Sun').every(r => r.background === 'rgba(0, 0, 0, 0)' && r.border === '0px'))
      assert.equal(await page.getByRole('button', { name: 'Sun', exact: true }).getAttribute('aria-pressed'), 'true')
      const times = await page.locator('.sun-times').innerText()
      assert.match(times, /Light 6:18 AM – 5:29 PM/)
      assert.equal(await page.locator('.sun-place').innerText(), '37.8°, −122.4°')
      await page.screenshot({ path: path.join(out, `appearance-sun-${width}-${mode}.png`) })
      for (const size of [75, 125, 150]) {
        await page.evaluate(size => __sunTest.theme.set({ size }), size); await saved(page)
        await page.locator('.appearance-sun').scrollIntoViewIfNeeded()
        const bounds = await page.locator('.sun-modes button').evaluateAll(nodes => nodes.map(n => {
          const button = n.getBoundingClientRect(), range = document.createRange(); range.selectNodeContents(n)
          const text = range.getBoundingClientRect()
          const style = getComputedStyle(n), card = n.closest('.appearance-card')
          return { left: button.left, right: button.right, height: button.height, textLeft: text.left, textRight: text.right, font: style.fontSize, padding: style.paddingInline, container: getComputedStyle(card).containerType, cardWidth: getComputedStyle(card).width }
        }))
        assert.ok(bounds.every(r => r.left >= 0 && r.right <= width && r.textLeft >= r.left && r.textRight <= r.right && (width !== 390 || r.height >= 43.9)), JSON.stringify({ size, bounds }))
      }
      await page.evaluate(() => __sunTest.theme.set({ size: 100 })); await saved(page)
      // Shared control retains keyboard operation.
      await page.getByRole('button', { name: 'Sun', exact: true }).focus()
      await page.keyboard.press('Tab'); await page.keyboard.press('Space'); await saved(page)
      assert.equal(await page.evaluate(() => __sunTest.theme.value.mode), 'system')
      await page.keyboard.press('Shift+Tab'); await page.keyboard.press('Space'); await saved(page)
      assert.equal(await page.evaluate(() => __sunTest.theme.value.mode), 'sun')
      // Persona changes are read through the same route, including shared place.
      values.location = { latitude: 51.5, longitude: -.1, source: 'timezone', timeZone: 'Europe/London' }
      await page.evaluate(() => __sunTest.theme.refresh())
      await page.waitForFunction(() => document.querySelector('.sun-place')?.textContent.startsWith('51.5°'))
      assert.equal(await page.locator('.sun-place').innerText(), '51.5°, −0.1°')
      // Live timer, including an exact boundary without focus or reload.
      values.location = place; await page.evaluate(() => __sunTest.theme.refresh())
      const boundary = await page.evaluate(() => __sunTest.theme.solar.end)
      await page.clock.install({ time: new Date(Math.ceil(boundary) - 1000) })
      await page.evaluate(() => __sunTest.theme.apply())
      assert.equal(await page.evaluate(() => __sunTest.theme.current), 'light')
      await page.clock.runFor(1100)
      assert.equal(await page.evaluate(() => __sunTest.theme.current), 'dark')
      await page.clock.setSystemTime(new Date('2026-11-14T12:00:00-08:00'))
      await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')))
      assert.equal(await page.evaluate(() => __sunTest.theme.current), 'light')
      await page.clock.setSystemTime(new Date('2026-11-14T23:00:00-08:00'))
      await page.evaluate(() => window.dispatchEvent(new Event('focus')))
      assert.equal(await page.evaluate(() => __sunTest.theme.current), 'dark')
      assert.deepEqual(errors, [])
      results.push({ width, mode, firstPaint: mode, geometry, times, boundaryTimer: true, wakeAndFocus: true, errors })
      console.log(`PASS ${width} ${mode}: first paint, layout, saved place, keyboard, persona refresh, timer and wake`)
    } finally { await ctx.close() }
  }
  for (const denied of [false, true]) {
    reset('system'); delete values.location
    const { ctx, page } = await context(390, '2026-11-13T12:00:00-08:00', denied)
    try {
      await open(page); await page.getByRole('button', { name: 'Sun', exact: true }).click(); await saved(page)
      assert.equal(await page.evaluate(() => __locationAsks), 1)
      assert.equal(values.location.source, denied ? 'timezone' : 'geolocation')
      assert.deepEqual([values.location.latitude, values.location.longitude], [37.8, -122.4])
      if (denied) await page.getByText('Using your time zone ·', { exact: true }).waitFor()
      await page.reload(); await page.waitForFunction(() => globalThis.__sunTest)
      assert.equal(await page.evaluate(() => __locationAsks), 0)
      await page.evaluate(() => __sunTest.goSettings('appearance'))
      await page.getByRole('button', { name: 'Use my location', exact: true }).click(); await saved(page)
      assert.equal(await page.evaluate(() => __locationAsks), 1)
      console.log(`PASS location ${denied ? 'denied' : 'granted'}: rounded save, no reload prompt, explicit retry`)
    } finally { await ctx.close() }
  }
  // A host from before Sun: persist locally, paint correctly on reload, no loop.
  reset('system'); delete values.location; refuse = true
  const { ctx, page } = await context(1440, '2026-11-13T23:00:00-08:00', true)
  try {
    await open(page); await page.getByRole('button', { name: 'Sun', exact: true }).click(); await saved(page)
    assert.equal(await page.evaluate(() => __sunTest.theme.status.error), null)
    await page.getByText('Sun saved on this device until the host supports it.', { exact: true }).waitFor()
    assert.equal(writes.length, 1)
    for (let i = 0; i < 3; i++) await page.evaluate(() => __sunTest.theme.refresh())
    assert.equal(writes.length, 1)
    await page.reload(); await page.waitForFunction(() => globalThis.__sunTest); await saved(page)
    await page.waitForFunction(() => globalThis.__sunFirstPaint)
    assert.equal(await page.evaluate(() => __sunFirstPaint), 'dark')
    assert.equal(await page.evaluate(() => __sunTest.theme.value.mode), 'sun')
    assert.equal(await page.evaluate(() => __locationAsks), 0)
    assert.equal(writes.length, 2)
    refuse = false
    await page.reload(); await page.waitForFunction(() => globalThis.__sunTest); await saved(page)
    assert.equal(values.desktop.mode, 'sun'); assert.ok(values.location)
    assert.equal(await page.evaluate(() => localStorage.getItem('loam.appearance.sun')), null)
    console.log('PASS older host: local boot, no error loop, one retry per navigation, migration')
  } finally { await ctx.close() }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify({ fixtureOnly: true, results }, null, 2) + '\n')
} finally { await browser.close(); await app.close() }
