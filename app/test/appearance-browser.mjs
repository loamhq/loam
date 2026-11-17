import { mergeAppearance } from '../public/theme-options.js'
// Local page and relay, synthetic host settings and conversations. No live host.
import assert from 'node:assert/strict'
import { mkdir, writeFile, readFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
const out = process.env.APPEARANCE_EVIDENCE
if (!out) throw new Error('Set APPEARANCE_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const app = await oneProductFixture(), results = []
const values = { phone: { mode: 'system', size: 100, theme: 'latte' }, desktop: { mode: 'system', size: 100, theme: 'latte' } }
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, values)
app.routes['PATCH /v1/settings/appearance'] = (req, res, url, raw) => { for (const [kind, patch] of Object.entries(JSON.parse(raw))) values[kind] = mergeAppearance(values[kind], patch); json(res, values) }
const listing = await app.f.api('GET', '/threads'), base = listing.threads[0]
const member = { ...base.members[0], working: false, busy: false, running: false, background: 0, asking: false }
const rows = [
  { ...base, id: 'quiet-long', title: 'A little more room for the unexpected ideas we want to keep exploring together', pinned: false, general: false, members: [member], working: 0, background: 0, unread: 0 },
  { ...base, id: 'quiet-short', title: 'Room to think', pinned: false, general: false, members: [member], working: 0, background: 0, unread: 0 },
  { ...base, id: 'working', title: 'Gather the next steps', pinned: false, general: false, members: [{ ...member, working: true }], working: 1, background: 0, unread: 0 },
  listing.threads.find((r) => r.id === 'a-quieter-week'),
]
app.routes['GET /v1/threads'] = (req, res) => json(res, { ...listing, threads: rows })
app.routes['GET /v1/threads/events'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(`: open ping=15000\n\ndata: ${JSON.stringify({ type: 'threads.snapshot', threads: rows, agents: app.f.agents })}\n\n`) }
const taskRecord = await app.f.api('GET', '/threads/a-quieter-week')
app.routes['GET /v1/threads/a-quieter-week'] = (req, res) => json(res, { ...taskRecord, thread: { ...taskRecord.thread,
  background: 2, members: taskRecord.thread.members.map((m, i) => i ? m : { ...m, working: false, background: 2, background_tasks: [{ id: 'check', description: 'Review the next steps', started_at: '2026-11-11T04:50:00Z' }] }) }, turn: null, turns: [] })
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
async function contextFor(width, mode) {
  const ctx = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode, isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce' })
  await ctx.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__appearanceTest = { theme, threads, goSettings, closeSheet, schedule };' }))
  return ctx
}
async function settle(page) {
  await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
}
async function geometry(page) {
  return page.evaluate(() => {
    const box = document.querySelector('#app').getBoundingClientRect()
    return { width: innerWidth, height: innerHeight, docWidth: document.documentElement.scrollWidth, docHeight: document.documentElement.scrollHeight, bodyHeight: document.body.getBoundingClientRect().height, app: { x: box.x, y: box.y, width: box.width, height: box.height }, overflow: document.documentElement.scrollWidth > innerWidth + 1 || document.documentElement.scrollHeight > innerHeight + 1 }
  })
}
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) for (const size of [90, 125]) {
    const kind = width === 390 ? 'phone' : 'desktop'
    values[kind] = { mode, size, theme: 'latte' }
    const ctx = await contextFor(width, mode), page = await ctx.newPage(), errors = []
    page.on('pageerror', (e) => errors.push(e.message))
    page.setDefaultTimeout(10000)
    // Observe the values at first contentful paint, independent of app startup.
    await page.addInitScript(() => new PerformanceObserver((list) => {
      if (list.getEntries().some((e) => e.name === 'first-contentful-paint')) globalThis.__firstAppearance = { theme: document.documentElement.dataset.theme, scale: document.documentElement.style.getPropertyValue('--ui-scale') }
    }).observe({ type: 'paint', buffered: true }))
    const shot = async (label) => {
      await settle(page)
      const g = await geometry(page)
      assert.equal(g.overflow, false, JSON.stringify(g))
      assert.ok(Math.abs(g.app.height - g.height) < 2, JSON.stringify(g))
      await page.screenshot({ path: path.join(out, `${label}-${width}-${mode}-${size}.png`), animations: 'disabled' })
      return g
    }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor(); await page.waitForFunction(() => globalThis.__appearanceTest); await settle(page)
      assert.equal(await page.evaluate(() => __appearanceTest.theme.kind), kind)
      await page.waitForFunction(() => globalThis.__firstAppearance)
      assert.deepEqual(await page.evaluate(() => __firstAppearance), { theme: mode, scale: String(size / 100) })
      const heights = await page.locator('.tt-row').evaluateAll((nodes) => nodes.map((n) => n.getBoundingClientRect().height))
      assert.ok(heights.every((height) => Math.abs(height - 80 * size / 100) < 1), JSON.stringify(heights))
      assert.equal(await page.locator('.tt-quiet .srow-name').count(), 0)
      assert.equal(await page.locator('.tt-row:not(.tt-quiet) .srow-name').count(), 1)
      const short = await page.locator('[title="Room to think"]').evaluate((n) => {
        const title = n.querySelector('.srow-title').getBoundingClientRect(), face = n.querySelector('.avatar-wrap').getBoundingClientRect()
        return { title: title.y + title.height / 2, face: face.y + face.height / 2 }
      })
      assert.ok(Math.abs(short.title - short.face) < 1, JSON.stringify(short))
      assert.equal(await page.locator('.tt-quiet .srow-title').first().evaluate((n) => getComputedStyle(n).webkitLineClamp), '2')
      const list = await shot('list')
      const actionBounds = await page.locator('.tt-list .home-head button, .tt-list .list-head button').evaluateAll((nodes) => nodes.map((n) => { const r = n.getBoundingClientRect(); return { x: r.x, right: r.right } }))
      assert.ok(actionBounds.every((r) => r.x >= 0 && r.right <= width + 1), JSON.stringify(actionBounds))
      await page.evaluate(() => {
        const row = __appearanceTest.threads.st.threads.find((r) => r.id === 'working')
        row.working = 0; row.members.forEach((m) => { m.working = false }); __appearanceTest.schedule()
      })
      await page.locator('[title="Gather the next steps"].tt-quiet').waitFor()
      assert.deepEqual(await page.locator('.tt-row').evaluateAll((nodes) => nodes.map((n) => n.getBoundingClientRect().height)), heights)
      // Restore the working row for the next fixture context.
      rows[2].working = 1; rows[2].members[0].working = true
      await page.evaluate(() => __appearanceTest.threads.open('a-quieter-week'))
      await page.locator('.tt-message').first().waitFor()
      const thread = await shot('thread')
      const composer = await page.locator('.tt-thread .thread-dock').boundingBox()
      assert.ok(composer.y >= 0 && composer.y + composer.height <= (width === 390 ? 844 : 1000) + 1, JSON.stringify(composer))
      await page.getByRole('button', { name: '2 background tasks', exact: true }).click()
      await page.locator('.tt-tasks-panel').waitFor()
      const tasks = await page.locator('.tt-tasks-panel').boundingBox()
      assert.ok(tasks.x >= 0 && tasks.x + tasks.width <= width + 1 && tasks.y >= 0 && tasks.y + tasks.height <= (width === 390 ? 844 : 1000) + 1, JSON.stringify(tasks))
      await page.keyboard.press('Escape')
      await page.locator('.tt-tasks-panel').waitFor({ state: 'hidden' })
      // Fixed desktop popovers stay in the viewport at the selected scale.
      await page.getByRole('button', { name: 'Providers & models', exact: true }).click()
      await page.locator('.tt-configuration').waitFor()
      const configuration = await page.locator('.tt-configuration').boundingBox()
      assert.ok(configuration.x >= 0 && configuration.x + configuration.width <= width + 1 && configuration.y >= 0 && configuration.y + configuration.height <= (width === 390 ? 844 : 1000) + 1, JSON.stringify(configuration))
      await page.keyboard.press('Escape')
      await page.locator('.tt-configuration').waitFor({ state: 'hidden' })
      if (width === 390) {
        await page.setViewportSize({ width, height: 500 }); await settle(page)
        const shortComposer = await page.locator('.tt-thread .thread-dock').boundingBox()
        assert.ok(shortComposer.y + shortComposer.height <= 501, JSON.stringify(shortComposer))
        await page.setViewportSize({ width, height: 844 }); await settle(page)
      }
      await page.evaluate(() => __appearanceTest.goSettings('appearance'))
      await page.getByRole('slider', { name: 'App zoom' }).waitFor()
      const settings = await shot('appearance')
      await page.getByRole('button', { name: 'Zoom in', exact: true }).click()
      await page.waitForFunction((size) => __appearanceTest.theme.value.size === size + 5, size)
      await page.waitForFunction(() => !__appearanceTest.theme.status.saving)
      assert.equal(values[kind].size, size + 5)
      // Input and keyboard operate on the same persisted setting.
      await page.getByRole('slider').focus(); await page.keyboard.press('ArrowLeft')
      await page.waitForFunction(() => !__appearanceTest.theme.status.saving)
      if (width === 1440) {
        await page.keyboard.press('Control+-'); await page.keyboard.press('Meta+='); await page.keyboard.press('Control+0')
        await page.waitForFunction(() => !__appearanceTest.theme.status.saving)
        assert.equal(values.desktop.size, 100)
      }
      for (const explicit of ['Light', 'Dark']) {
        await page.getByRole('button', { name: explicit, exact: true }).click()
        await page.waitForFunction(() => !__appearanceTest.theme.status.saving)
        assert.equal(values[kind].mode, explicit.toLowerCase())
      }
      await page.getByRole('button', { name: 'System', exact: true }).click()
      await page.waitForFunction(() => !__appearanceTest.theme.status.saving)
      await page.emulateMedia({ colorScheme: mode === 'light' ? 'dark' : 'light' })
      await page.waitForFunction((expected) => document.documentElement.dataset.theme === expected, mode === 'light' ? 'dark' : 'light')
      values[kind] = { mode, size, theme: 'latte' }
      await page.reload(); await page.waitForFunction(() => globalThis.__appearanceTest)
      assert.deepEqual(await page.evaluate(() => { const { options, ...value } = __appearanceTest.theme.value; return value }), { mode, transition: 'normal', size, theme: 'latte' })
      // Fresh storage context reads the same host setting.
      const other = await contextFor(width, mode), fresh = await other.newPage()
      await fresh.goto(app.url); await fresh.locator('.tt-row').first().waitFor(); await fresh.waitForFunction(() => globalThis.__appearanceTest)
      assert.deepEqual(await fresh.evaluate(() => { const { options, ...value } = __appearanceTest.theme.value; return value }), { mode, transition: 'normal', size, theme: 'latte' })
      await other.close()
      values[kind] = { mode: mode === 'light' ? 'dark' : 'light', size: 110, theme: 'latte' }
      await page.evaluate(() => __appearanceTest.theme.refresh())
      assert.deepEqual(await page.evaluate(() => { const { options, ...value } = __appearanceTest.theme.value; return value }), { ...values[kind], transition: 'normal' })
      if (width === 1440) {
        await page.evaluate(() => __appearanceTest.closeSheet())
        assert.equal(await page.getByTitle('Appearance', { exact: true }).count(), 0, 'the duplicate rail toggle was removed')
      }
      for (const edge of [75, 150]) {
        await page.evaluate((size) => __appearanceTest.theme.set({ size }), edge)
        await page.evaluate(() => __appearanceTest.goSettings('appearance'))
        await settle(page)
        const edgeGeometry = await geometry(page)
        assert.equal(edgeGeometry.overflow, false, JSON.stringify({ edge, edgeGeometry }))
        assert.ok(Math.abs(edgeGeometry.app.height - edgeGeometry.height) < 2)
        // Cards intentionally scroll horizontally at high zoom; their viewport
        // and the active card must fit, along with all non-scrolling controls.
        const controls = await page.locator('.appearance-card button:not(.theme-choice), .appearance-card input, .theme-picker, .theme-choice.on').evaluateAll((nodes) => nodes.map((n) => { const r = n.getBoundingClientRect(); return { left: r.left, right: r.right } }))
        assert.ok(controls.every((r) => r.left >= 0 && r.right <= width + 1), JSON.stringify({ edge, controls }))
        await page.getByRole('button', { name: 'Reset to 100%', exact: true }).click()
        await page.waitForFunction(() => !__appearanceTest.theme.status.saving)
        assert.equal(values[kind].size, 100)
      }
      assert.deepEqual(errors, [])
      results.push({ width, mode, size, list, thread, settings, heights, firstPaint: true, reload: true, freshBrowser: true, keyboard: width === 1440, errors })
      console.log(`PASS ${width} ${mode} ${size}%`)
    } finally { await ctx.close() }
  }
  // Missing host support is visible, and retry saves the pending preview.
  const getSettings = app.routes['GET /v1/settings/appearance'], patchSettings = app.routes['PATCH /v1/settings/appearance']
  delete app.routes['GET /v1/settings/appearance']; delete app.routes['PATCH /v1/settings/appearance']
  const unavailable = await contextFor(390, 'dark'), page = await unavailable.newPage()
  await page.goto(app.url); await page.locator('.tt-row').first().waitFor(); await page.waitForFunction(() => globalThis.__appearanceTest)
  await page.evaluate(() => __appearanceTest.goSettings('appearance'))
  await page.getByText('Appearance settings are not available on the host yet.', { exact: false }).waitFor()
  assert.equal(await page.getByRole('slider').inputValue(), '100')
  await page.getByRole('button', { name: 'Zoom out', exact: true }).click()
  await page.waitForFunction(() => __appearanceTest.theme.status.error?.includes('until saved') && __appearanceTest.theme.value.size === 95)
  await page.waitForFunction(() => document.querySelector('.appearance-status')?.textContent.includes('until saved'))
  assert.match(await page.locator('.appearance-status').innerText(), /until saved/)
  app.routes['GET /v1/settings/appearance'] = getSettings; app.routes['PATCH /v1/settings/appearance'] = patchSettings
  await page.getByRole('button', { name: 'Try again', exact: true }).click()
  await page.waitForFunction(() => !__appearanceTest.theme.status.saving && !__appearanceTest.theme.status.error)
  assert.equal(values.phone.size, 95)
  await unavailable.close()
  console.log('PASS unavailable host, visible failure and retry')
  await writeFile(path.join(out, 'browser.json'), JSON.stringify({ fixtureOnly: true, results }, null, 2) + '\n')
} finally { await browser.close(); await app.close() }
