// Real page/relay, synthetic settings host. All captures contain fixture data.
import assert from 'node:assert/strict'
import { readFile, mkdir, mkdtemp, writeFile, rm } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
import { loadThemes } from '../src/themes.js'
import { appearanceValues } from '../src/appearance.js'
import { mergeAppearance } from '../public/theme-options.js'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.OPTIONS_EVIDENCE || path.join(process.env.TMPDIR, 'theme-options-evidence')
await mkdir(out, { recursive: true })
const browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const app = await oneProductFixture(), results = [], themes = (await loadThemes()).themes
const values = appearanceValues(null, themes)
const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, values)
app.routes['PATCH /v1/settings/appearance'] = (req, res, url, raw) => { for (const [kind, patch] of Object.entries(JSON.parse(raw))) values[kind] = mergeAppearance(values[kind], patch); json(res, values) }
const source = await readFile('public/app.js', 'utf8')
const expose = '\nglobalThis.__optionsTest={theme,goSettings,closeSheet,threads,state,schedule,blob,agentLook};'
async function contextFor(width, mode) {
  const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode === 'light' ? 'dark' : 'light', isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce' })
  await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + expose }))
  return context
}
const faceStyle = page => page.locator('.theme-agent-preview .blob').first().evaluate(n => { const s = getComputedStyle(n); return { color: s.color, background: s.backgroundColor, shape: s.borderRadius } })
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) for (const colours of ['palette', 'latte-tones']) for (const shape of ['distinct', 'circle', 'rounded-square']) {
    const kind = width === 390 ? 'phone' : 'desktop'
    values[kind] = { mode, size: 100, theme: 'latte', options: { latte: { 'agent-colours': colours, 'agent-shapes': shape } } }
    const context = await contextFor(width, mode), errors = [], cssRequests = []
    try {
      const page = await context.newPage()
      page.on('pageerror', e => errors.push(e.message))
      page.on('request', r => { if (r.url().endsWith('.css')) cssRequests.push(r.url()) })
      await page.addInitScript(() => new PerformanceObserver(list => {
        if (list.getEntries().some(e => e.name === 'first-contentful-paint')) {
          const root = document.documentElement
          globalThis.__firstOptions = { colours: root.getAttribute('data-option-agent-colours'), shape: root.getAttribute('data-option-agent-shapes'), mode: root.dataset.theme }
        }
      }).observe({ type: 'paint', buffered: true }))
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor(); await page.evaluate(() => document.fonts.ready)
      await page.waitForFunction(() => globalThis.__firstOptions)
      assert.deepEqual(await page.evaluate(() => __firstOptions), { colours, shape, mode })
      const label = `${width}-${mode}-${colours}-${shape}`
      await page.screenshot({ animations: 'disabled', path: path.join(out, `chats-${label}.png`) })
      // Click the real Settings button to anchor the existing surface system.
      await page.getByRole('button', { name: 'Settings', exact: true }).click()
      if (width === 390) await page.getByRole('button', { name: /Appearance/ }).click()
      await page.getByRole('group', { name: 'Agent colours', exact: true }).waitFor()
      const face = await faceStyle(page)
      if (shape === 'circle') assert.equal(face.shape, '50%')
      if (shape === 'rounded-square') assert.equal(face.shape, '24%')
      if (colours === 'latte-tones') assert.equal(face.background, 'rgb(244, 236, 224)')
      const sizes = await page.locator('.theme-option-choices button').evaluateAll(nodes => nodes.map(n => { const r = n.getBoundingClientRect(); return { width: r.width, height: r.height, overflow: n.scrollWidth > n.clientWidth } }))
      assert.ok(sizes.every(r => r.height >= 44 && !r.overflow), JSON.stringify(sizes))
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      await page.screenshot({ animations: 'disabled', path: path.join(out, `appearance-${label}.png`) })
      if (shape === 'rounded-square') {
        await page.getByRole('button', { name: 'Reset to 100%', exact: true }).scrollIntoViewIfNeeded()
        await page.screenshot({ animations: 'disabled', path: path.join(out, `appearance-lower-${label}.png`) })
      }
      const beforeRequests = cssRequests.length
      await page.getByRole('group', { name: 'Agent shapes', exact: true }).getByRole('button', { name: 'Circle', exact: true }).click()
      assert.equal((await faceStyle(page)).shape, '50%')
      await page.getByRole('group', { name: 'Agent colours', exact: true }).getByRole('button', { name: colours === 'palette' ? 'Latte tones' : 'Palette', exact: true }).click()
      const switchedFace = await faceStyle(page)
      assert.notEqual(switchedFace.background, face.background)
      await page.getByRole('group', { name: 'Theme', exact: true }).getByRole('button', { name: 'Plain', exact: true }).click()
      await page.waitForFunction(() => document.documentElement.dataset.look === 'plain')
      assert.equal(await page.getByRole('group', { name: 'Agent shapes', exact: true }).count(), 0)
      assert.equal(await page.evaluate(() => document.documentElement.hasAttribute('data-option-agent-shapes')), false)
      await page.getByRole('group', { name: 'Theme', exact: true }).getByRole('button', { name: 'Latte & Newspaper', exact: true }).click()
      assert.equal((await faceStyle(page)).shape, '50%')
      assert.equal(cssRequests.length, beforeRequests)
      await page.waitForFunction(() => !__optionsTest.theme.status.saving)
      assert.equal(values[kind].options.latte['agent-shapes'], 'circle')
      // Retained message/mention DOM must follow options even without rebuilding.
      await page.evaluate(() => __optionsTest.closeSheet())
      await page.evaluate(() => __optionsTest.threads.open('small-details'))
      await page.locator('.tt-message .mention[data-face] .blob').first().waitFor()
      const mention = page.locator('.tt-message .mention[data-face] .blob').first()
      assert.equal(await mention.evaluate(n => getComputedStyle(n).borderRadius), '50%')
      const textBefore = await mention.locator('..').textContent()
      await page.evaluate(() => __optionsTest.theme.set({ options: { latte: { 'agent-shapes': 'rounded-square' } } }))
      assert.equal(await mention.evaluate(n => getComputedStyle(n).borderRadius), '24%')
      assert.equal(await mention.locator('..').textContent(), textBefore)
      assert.equal(await page.locator('.tt-speaker .blob').first().evaluate(n => getComputedStyle(n).borderRadius), '24%')
      await page.waitForFunction(() => !__optionsTest.theme.status.saving)
      await page.reload(); await page.waitForFunction(() => globalThis.__optionsTest)
      assert.equal(await page.evaluate(() => document.documentElement.getAttribute('data-option-agent-shapes')), 'rounded-square')
      assert.deepEqual(errors, [])
      results.push({ width, mode, colours, shape, firstPaint: true, face, instantSwitch: true, freshNavigation: true, retainedMention: true, errors })
    } finally { await context.close() }
  }
  console.log(`PASS: ${results.length} combinations; Appearance and chats captured; pre-paint options, instant switches, deep persistence, retained mention/byline faces, no overflow or page errors.`)
  // A theme loaded from an extra root declares an unrelated CSS-only option.
  const extraRoot = await mkdtemp(path.join(process.env.TMPDIR, 'browser-theme-'))
  try {
    await mkdir(path.join(extraRoot, 'example'))
    const example = { id: 'example', name: 'Example', version: '1.0.0', author: 'Fixture contributors', api: 0, kind: ['theme'], options: [{ id: 'texture', label: 'Texture', default: 'smooth', choices: [{ id: 'smooth', label: 'Smooth' }, { id: 'grain', label: 'Grain' }] }] }
    await writeFile(path.join(extraRoot, 'example/PLUGIN.md'), `---\nid: example\nname: Example\nversion: 1.0.0\nauthor: Fixture contributors\napi: 0\nkind: [theme]\noptions: ${JSON.stringify(example.options)}\n---\n`)
    await writeFile(path.join(extraRoot, 'example/theme.css'), ':root[data-look="example"][data-option-texture="grain"] { --radius-md: 17px; }')
    const plugin = await loadThemes([new URL('../public/themes/', import.meta.url), extraRoot])
    for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
      const kind = width === 390 ? 'phone' : 'desktop'
      values[kind] = { mode, size: 100, theme: 'example', options: { example: { texture: 'grain' } } }
      const context = await contextFor(width, mode)
      try {
        await context.route('**/appearance-state.js', route => route.fulfill({ contentType: 'text/javascript', body: 'globalThis.__loamAppearance=' + JSON.stringify({ available: true, themes: plugin.themes, values: appearanceValues(values, plugin.themes) }) }))
        await context.route('**/themes.css*', route => route.fulfill({ contentType: 'text/css', body: plugin.css }))
        await context.route('**/api/plugins', route => route.fulfill({ json: { api: 0, plugins: plugin.themes.map(theme => ({ ...theme, api: 0, kind: ['theme'], effective: true, layer: theme.id === 'example' ? 'yours' : 'shipped', on: true, switch: 'appearance', has: { theme: true } })) } }))
        await context.route('**/api/plugins/example/files/theme.css', route => route.fulfill({ contentType: 'text/css', body: ':root[data-look="example"][data-option-texture="grain"] { --radius-md: 17px; }' }))
        const page = await context.newPage()
        await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
        assert.equal(await page.evaluate(() => document.documentElement.getAttribute('data-option-texture')), 'grain')
        assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--radius-md').trim()), '17px')
        await page.getByRole('button', { name: 'Settings', exact: true }).click()
        if (width === 390) await page.getByRole('button', { name: /Appearance/ }).click()
        await page.getByRole('group', { name: 'Texture', exact: true }).waitFor()
        await page.screenshot({ animations: 'disabled', path: path.join(out, `plugin-appearance-${width}-${mode}.png`) })
        await page.getByRole('group', { name: 'Texture', exact: true }).getByRole('button', { name: 'Smooth', exact: true }).click()
        assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--radius-md').trim()), '14px')
        await page.getByRole('group', { name: 'Texture', exact: true }).getByRole('button', { name: 'Grain', exact: true }).click()
        await page.getByRole('group', { name: 'Theme', exact: true }).getByRole('button', { name: 'Latte & Newspaper', exact: true }).click()
        assert.equal(await page.evaluate(() => document.documentElement.hasAttribute('data-option-texture')), false)
        await page.getByRole('group', { name: 'Theme', exact: true }).getByRole('button', { name: 'Example', exact: true }).click()
        assert.equal(await page.evaluate(() => document.documentElement.getAttribute('data-option-texture')), 'grain')
        await page.waitForFunction(() => !__optionsTest.theme.status.saving)
      } finally { await context.close() }
    }
  } finally { await rm(extraRoot, { recursive: true, force: true }) }
  console.log('PASS: third-party manifest option renders beside shipped themes at both widths/modes; CSS changes immediately; inactive options clear and saved choices restore.')
  // Older hosts that silently drop an unknown field must not claim it saved.
  const get = app.routes['GET /v1/settings/appearance']
  const patch = app.routes['PATCH /v1/settings/appearance']
  app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, { phone: { mode: 'light', size: 100, theme: 'latte' }, desktop: { mode: 'light', size: 100, theme: 'latte' } })
  app.routes['PATCH /v1/settings/appearance'] = app.routes['GET /v1/settings/appearance']
  const context = await contextFor(390, 'light')
  try {
    const page = await context.newPage()
    await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
    assert.equal(await page.evaluate(() => document.documentElement.getAttribute('data-option-agent-colours')), 'palette')
    await page.getByRole('button', { name: 'Settings', exact: true }).click()
    await page.getByRole('button', { name: /Appearance/ }).click()
    await page.getByRole('group', { name: 'Agent colours', exact: true }).getByRole('button', { name: 'Latte tones', exact: true }).click()
    await page.waitForFunction(() => __optionsTest.theme.status.error?.includes('not saved'))
    assert.equal((await faceStyle(page)).background, 'rgb(244, 236, 224)')
    assert.match(await page.locator('.appearance-status').innerText(), /not saved/)
    app.routes['GET /v1/settings/appearance'] = get; app.routes['PATCH /v1/settings/appearance'] = patch
    await page.getByRole('button', { name: 'Try again', exact: true }).click()
    await page.waitForFunction(() => !__optionsTest.theme.status.error && !__optionsTest.theme.status.saving)
  } finally { await context.close() }
  console.log('PASS: older host defaults render, a silently dropped write remains visible as unsaved, and retry persists the live choice.')
  await writeFile(path.join(out, 'checks.json'), JSON.stringify({ combinations: results, pluginCases: 4, oldHostDefaultsAndRetry: true }, null, 2) + '\n')
} finally { await browser.close(); await app.close() }
