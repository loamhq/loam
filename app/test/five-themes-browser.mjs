// Local synthetic host, real page and browser. A separate image per phone pane
// preserves the actual UI; chat uses the shared segmented control.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { inspectColourFamilies } from '../tools/colour-families.mjs'
const inspect = Function(`return (${inspectColourFamilies.toString().replace("if (document.documentElement.dataset.look !== 'latte') return []", '')})`)()
const root = process.env.THEME_ROOT || process.cwd()
const { oneProductFixture } = await import(pathToFileURL(path.join(root, 'test/one-product-fixture.mjs')))
const { shippedPlugins } = await import(pathToFileURL(path.join(root, 'src/shipped-plugins.js')))
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.FIVE_THEME_EVIDENCE || path.join(process.env.TMPDIR, 'five-themes')
const looks = (process.env.THEME_LOOKS || 'deep-forest,autumn,yosemite,mist-trail,foundry').split(',')
const fixed = Date.parse('2026-11-13T20:00:00Z'), OriginalDate = Date
globalThis.Date = class extends OriginalDate { constructor(...args) { super(...(args.length ? args : [fixed])) } static now() { return fixed } }
await mkdir(out, { recursive: true })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const app = await oneProductFixture(), results = []
const rows = (await shippedPlugins(path.join(root, 'public'))).filter(r => r.kind.includes('theme'))
const values = { phone: {}, desktop: {}, location: { latitude: 0, longitude: 0, timeZone: 'UTC', source: 'geolocation' } }
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, values)
app.routes['PATCH /v1/settings/appearance'] = (req, res, url, raw) => { const body = JSON.parse(raw); for (const [key, value] of Object.entries(body)) values[key] = { ...values[key], ...value }; json(res, values) }
app.routes['GET /v1/plugins'] = (req, res) => json(res, { plugins: rows })
const source = await readFile(path.join(root, 'public/app.js'), 'utf8')
try {
  assert.equal((await fetch(app.url + '/up')).status, 200)
  const plugins = await (await fetch(app.url + '/api/plugins')).json()
  assert.ok(rows.every(r => !r.broken))
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) for (const look of looks) {
    const kind = width === 390 ? 'phone' : 'desktop'
    values[kind] = { theme: look, mode, size: 100 }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode === 'dark' ? 'light' : 'dark', isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce', locale: 'en-US', timezoneId: 'UTC' })
    const errors = [], fontRequests = []
    try {
      await context.addInitScript(({ fixed }) => {
        const Original = Date
        globalThis.Date = class extends Original { constructor(...args) { super(...(args.length ? args : [fixed])) } static now() { return fixed } }
        new PerformanceObserver(list => { if (list.getEntries().some(e => e.name === 'first-contentful-paint')) {
          globalThis.__firstTheme = { look: document.documentElement.dataset.look, mode: document.documentElement.dataset.theme, page: getComputedStyle(document.documentElement).getPropertyValue('--paper-page').trim() }
        } }).observe({ type: 'paint', buffered: true })
      }, { fixed })
      await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__five={theme,threads,goSettings,closeSheet,schedule};' }))
      const page = await context.newPage(); page.setDefaultTimeout(15000)
      page.on('pageerror', e => errors.push(e.message))
      page.on('request', r => { if (/\/themes\/[^/]+\/(?:fonts\.css|[^/]+\.woff2)/.test(r.url())) fontRequests.push(new URL(r.url()).pathname) })
      const settle = async () => { await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) }); await page.mouse.move(0, 0) }
      const surfaceChecks = []
      const shot = async label => {
        await settle(); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
        // The opaque-ground checker cannot resolve photograph/scrim composites.
        // Their unchanged neutral caption pair is covered by the card checks.
        const violations = (await page.evaluate(inspect)).filter(v => v.classes !== 'theme-choice-name')
        surfaceChecks.push({ label, violations }); assert.deepEqual(violations, [], `${look}/${mode}/${label}`)
        await page.screenshot({ path: path.join(out, `${look}-${width}-${mode}-${label}.png`), animations: 'disabled' })
      }
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor(); await page.waitForFunction(() => globalThis.__firstTheme)
      const first = await page.evaluate(() => __firstTheme)
      assert.deepEqual({ look: first.look, mode: first.mode }, { look, mode })
      const ground = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--paper-page').trim())
      assert.equal(first.page, ground)
      await shot('list')
      await page.evaluate(() => __five.threads.open('a-quieter-week'))
      await page.waitForFunction(() => !__five.threads.st.open.loading && !__five.threads.st.open.refreshing)
      await page.locator('.tt-message').first().waitFor(); await settle()
      assert.ok(await page.locator('#draft').isVisible())
      await shot('thread')
      await shot('done')
      await page.getByRole('button', { name: 'Done', exact: true }).click()
      await page.evaluate(() => __five.goSettings('appearance'))
      const selected = page.getByRole('button', { name: rows.find(r => r.id === look).name, exact: true }); await selected.waitFor(); await selected.scrollIntoViewIfNeeded()
      if (look !== 'plain') await page.locator(`.theme-preview[data-look="${look}"] .theme-cover`).evaluate(async n => { if (!n.complete) await new Promise((resolve, reject) => { n.onload = resolve; n.onerror = reject }); await n.decode() })
      await shot('appearance')
      if (!['latte', 'plain'].includes(look)) {
        await page.locator('.theme-agent-preview').scrollIntoViewIfNeeded()
        await shot('faces')
        assert.equal(await page.getByRole('button', { name:look === 'foundry' ? 'Rounded square' : 'Circle', exact:true }).getAttribute('aria-pressed'), 'true')
        assert.equal(await page.getByRole('group', {name:'Inside',exact:true}).count(),0)
        await page.getByRole('button', { name:'Signature', exact:true }).click()
        await page.getByRole('button', { name:'Initials', exact:true }).click()
        await page.evaluate(() => __five.theme.save())
        assert.equal(values[kind].options[look]['face-content'], 'initials')
        const face = page.locator('.theme-agent-preview .blob').first()
        const material = await face.evaluate((el, look) => ({
          material: getComputedStyle(el.querySelector(`[data-material="${look}"]`)).display,
          initial: getComputedStyle(el,'::after').content,
          letter: getComputedStyle(el.querySelector('.blob-letter')).display,
        }), look)
        assert.notEqual(material.material, 'none'); assert.notEqual(material.initial, 'none'); assert.equal(material.letter,'none')
        await shot('initials')
        await page.getByRole('button', { name:'Mark', exact:true }).click()
        await page.evaluate(() => __five.theme.save())
      }
      await page.getByRole('group', { name: 'Mode', exact: true }).scrollIntoViewIfNeeded()
      await shot('controls')
      const covers = await page.locator('.theme-cover').evaluateAll(nodes => nodes.map(n => ({ look: n.parentElement.dataset.look, src: new URL(n.src).pathname, position: getComputedStyle(n).objectPosition })))
      const fonts = await page.evaluate(() => [...document.fonts].map(f => ({ family: f.family, status: f.status })))
      const requested = [...new Set(fontRequests)]
      assert.ok(requested.every(url => url.startsWith(`/themes/${look}/`)), `Inactive theme fonts fetched: ${requested}`)
      if (!['latte', 'plain'].includes(look)) assert.ok(requested.some(url => url.endsWith('display-latin.woff2')), 'Active face must load')
      const autumn = covers.find(c => c.look === 'autumn')
      if (autumn) assert.ok(autumn.src.endsWith(mode === 'dark' ? '/cover-dark.webp' : '/cover.webp'))
      if (look === 'autumn') {
        // Real offline solar lighting at 12:00/00:00 on the equator, with
        // the system preference deliberately opposite to the desired mode.
        await page.evaluate(mode => { const instant = Date.parse(mode === 'light' ? '2026-11-13T12:00:00Z' : '2026-11-13T00:00:00Z'); Date.now = () => instant; __five.theme.set({ mode: 'sun' }) }, mode)
        await page.waitForFunction(mode => document.querySelector('.theme-preview[data-look="autumn"] .theme-cover')?.src.endsWith(mode === 'dark' ? '/cover-dark.webp' : '/cover.webp'), mode)
        assert.equal(await page.evaluate(() => __five.theme.current), mode)
        await selected.scrollIntoViewIfNeeded(); await shot('sun-cover')
        const solar = await page.evaluate(() => [LoamSun.state(Date.parse('2026-11-13T12:00:00Z'), { latitude: 0, longitude: 0, timeZone: 'UTC' }).mode, LoamSun.state(Date.parse('2026-11-13T00:00:00Z'), { latitude: 0, longitude: 0, timeZone: 'UTC' }).mode])
        assert.deepEqual(solar, ['light', 'dark'])
      }
      // A settings click uses the same persona-writable appearance endpoint.
      await page.getByRole('button', { name: 'Plain', exact: true }).click()
      await selected.click(); await page.evaluate(() => __five.theme.save())
      assert.equal(values[kind].theme, look)
      if (!['latte', 'plain'].includes(look)) {
        await page.getByRole('button', { name: 'Circle', exact: true }).click(); await page.evaluate(() => __five.theme.save())
        assert.equal(values[kind].options[look]['agent-shapes'], 'circle')
        assert.equal(await page.getByRole('group', {name:'Inside',exact:true}).count(),0)
        await page.reload(); await page.waitForFunction(()=>globalThis.__five && document.documentElement.dataset.optionAgentShapes==='circle')
        assert.equal(await page.evaluate(()=>document.documentElement.dataset.optionAgentShapes),'circle')
      }
      assert.deepEqual(errors, [])
      results.push({ look, width, mode, first, covers, fonts, fontRequests: requested, surfaceChecks, errors })
      console.log(`PASS ${look} ${width} ${mode}: first paint, thread, Done, cover and active-only font`)
    } finally { await context.close() }
  }
  await writeFile(path.join(out, 'checks.json'), JSON.stringify({ browser: browser.version(), plugins: plugins.plugins.map(({ id, broken, warnings }) => ({ id, broken, warnings })), results }, null, 2) + '\n')
} finally { await browser.close(); await app.close() }
