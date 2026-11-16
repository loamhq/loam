// Real page and relay, synthetic host with no saved appearance options.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
import { shippedPlugins } from '../src/shipped-plugins.js'
import { mergeAppearance } from '../public/theme-options.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.FACE_DEFAULTS_EVIDENCE || path.join(process.env.TMPDIR, 'face-defaults')
await mkdir(out, { recursive: true })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const app = await oneProductFixture(), results = [], writes = []
const rows = (await shippedPlugins('public')).filter(row => row.kind.includes('theme'))
let values
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, values)
app.routes['PATCH /v1/settings/appearance'] = (req, res, url, raw) => {
  const patch = JSON.parse(raw); writes.push(patch)
  for (const [kind, value] of Object.entries(patch)) values[kind] = mergeAppearance(values[kind], value)
  json(res, values)
}
app.routes['GET /v1/plugins'] = (req, res) => json(res, { plugins: rows })
const source = await readFile('public/app.js', 'utf8')
const expose = '\nglobalThis.__faceDefaults = {theme,goSettings,closeSheet};'
const special = id => id === 'latte' ? 'latte-art' : 'signature'
const defaultShape = id => id === 'foundry' ? 'rounded-square' : 'circle'
const shapeLabel = id => id === 'foundry' ? 'Rounded square' : 'Circle'
const settle = page => page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
async function saved(page) {
  await page.evaluate(() => __faceDefaults.theme.save())
  await page.waitForFunction(() => !__faceDefaults.theme.status.saving)
  assert.equal(await page.evaluate(() => __faceDefaults.theme.status.error), null)
}
const styles = locator => locator.evaluateAll(nodes => nodes.map(n => ({
  radius: getComputedStyle(n).borderRadius,
  art: [...n.querySelectorAll('.latte-art,.signature-art')].filter(art => getComputedStyle(art).display !== 'none').length,
})))

try {
  const health = await fetch(app.url + '/up'); assert.equal(health.status, 200)
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const kind = width === 390 ? 'phone' : 'desktop'
    values = { phone: { mode, size: 100, theme: 'latte' }, desktop: { mode, size: 100, theme: 'latte' } }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode, isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce' })
    const page = await context.newPage(), errors = [], checks = []
    page.on('pageerror', e => errors.push(e.message))
    await context.addInitScript(() => { globalThis.__freshStorage = localStorage.length === 0 })
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + expose }))
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      assert.equal(await page.evaluate(() => __freshStorage), true)
      for (const row of rows) {
        await page.evaluate(() => __faceDefaults.goSettings('appearance'))
        await page.getByRole('group', { name: 'Theme', exact: true }).getByRole('button', { name: row.name, exact: true }).click()
        await saved(page); await settle(page)
        assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), mode)
        assert.equal(values[kind].options, undefined, 'trying themes must not save implicit defaults')
        assert.equal(await page.getByRole('group', { name: 'Inside', exact: true }).count(), 0)
        assert.equal(await page.getByRole('switch', { name: 'In a mug', exact: true }).count(), 0)
        let faces
        if (row.id !== 'plain') {
          const selected = page.getByRole('button', { name: shapeLabel(row.id), exact: true })
          assert.equal(await selected.getAttribute('aria-pressed'), 'true')
          await selected.evaluate(n => n.scrollIntoView({ block: 'center' }))
          faces = await styles(page.locator('.theme-agent-preview .blob'))
          assert.ok(faces.length > 1)
          assert.ok(faces.every(face => face.radius === (row.id === 'foundry' ? '24%' : '50%') && face.art === 0), JSON.stringify(faces))
        } else assert.equal(await page.locator('.theme-options').count(), 0, 'Plain has no options')
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
        await page.screenshot({ path: path.join(out, `${row.id}-${width}-${mode}-appearance.png`), animations: 'disabled' })
        await page.evaluate(() => __faceDefaults.closeSheet()); await settle(page)
        const listFaces = await styles(page.locator('.tt-row .blob'))
        assert.ok(listFaces.length > 1)
        if (row.id !== 'plain') assert.ok(listFaces.every(face => face.radius === (row.id === 'foundry' ? '24%' : '50%') && face.art === 0), JSON.stringify(listFaces))
        await page.screenshot({ path: path.join(out, `${row.id}-${width}-${mode}-chats.png`), animations: 'disabled' })
        checks.push({ theme: row.id, selected: row.id === 'plain' ? null : defaultShape(row.id), faces, listFaces, hiddenSuboptions: true })
      }
      // Explicit special choices still save, reload and restore sub-options.
      for (const row of rows.filter(row => row.id !== 'plain')) {
        await page.evaluate(() => __faceDefaults.goSettings('appearance'))
        await page.getByRole('group', { name: 'Theme', exact: true }).getByRole('button', { name: row.name, exact: true }).click()
        await page.getByRole('button', { name: row.id === 'latte' ? 'Latte art' : 'Signature', exact: true }).click()
        await page.getByRole('group', { name: 'Inside', exact: true }).getByRole('button', { name: 'Initials', exact: true }).click()
        if (row.id === 'latte') await page.getByRole('switch', { name: 'In a mug', exact: true }).click()
        await saved(page)
        await page.getByRole('button', { name: shapeLabel(row.id), exact: true }).click()
        assert.equal(await page.getByRole('group', { name: 'Inside', exact: true }).count(), 0)
        assert.equal(await page.getByRole('switch', { name: 'In a mug', exact: true }).count(), 0)
        await page.getByRole('button', { name: row.id === 'latte' ? 'Latte art' : 'Signature', exact: true }).click()
        assert.equal(await page.getByRole('button', { name: 'Initials', exact: true }).getAttribute('aria-pressed'), 'true')
        await saved(page); await page.reload()
        await page.waitForFunction(id => globalThis.__faceDefaults && document.documentElement.dataset.look === id, row.id)
        assert.equal(await page.evaluate(() => document.documentElement.dataset.optionAgentShapes), special(row.id))
        await page.evaluate(() => __faceDefaults.goSettings('appearance')); await settle(page)
        assert.equal(await page.getByRole('button', { name: 'Initials', exact: true }).getAttribute('aria-pressed'), 'true')
        if (row.id === 'latte') assert.equal(await page.getByRole('switch', { name: 'In a mug', exact: true }).getAttribute('aria-checked'), 'false')
        assert.ok((await styles(page.locator('.theme-agent-preview .blob'))).every(face => face.art === 1))
        await page.locator('.theme-agent-preview').evaluate(n => n.scrollIntoView({ block: 'start' }))
        await settle(page)
        await page.screenshot({ path: path.join(out, `${row.id}-${width}-${mode}-saved-special.png`), animations: 'disabled' })
        await page.evaluate(() => __faceDefaults.closeSheet())
      }
      assert.deepEqual(errors, [])
      results.push({ width, mode, kind, freshStorage: true, checks, savedSpecialChoices: true, errors })
      console.log(`PASS ${width} ${mode}: seven theme switches, six simple defaults, Plain preserved, saved special faces and conditional sub-options`)
    } finally { await context.close() }
  }
  await writeFile(path.join(out, 'checks.json'), JSON.stringify({ fixtureOnly: true, health: 200, browser: browser.version(), results, writes }, null, 2) + '\n')
} finally { await browser.close(); await app.close() }
