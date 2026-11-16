// Synthetic catalog; real app shell, theme CSS, focus and panel routing.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import http from 'node:http'
import { once } from 'node:events'
import { createAppMedia } from '../src/app-media.js'
import { createAppIcons } from '../src/app-icons.js'
import { captureAppPage } from '../src/app-capture.js'
import { previewNames, previewPage, mark } from './fixtures/app-previews/pages.mjs'
import { SHAPES } from './fixtures/github.js'
import path from 'node:path'
import { profileHost } from './agent-profile-host.mjs'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const before = Boolean(process.env.APPS_LAUNCHER_BEFORE), out = process.env.APPS_LAUNCHER_EVIDENCE
const source = await readFile('public/app.js', 'utf8'), reports = []
if (out) await mkdir(out, { recursive: true })
const themes = process.env.APPS_LAUNCHER_ALL ? ['latte', 'plain', 'deep-forest', 'autumn', 'foundry', 'mist-trail', 'yosemite'] : ['latte', 'deep-forest', 'autumn']
const names = previewNames, siteRequests = [], captures = [], captureLogs = []
const internal = Boolean(process.env.APPS_LAUNCHER_INTERNAL), internalRequests = []
const site = http.createServer((req, res) => {
  siteRequests.push(req.url)
  const virtual = /^app-(\d+)\.preview\.example\.test(?::\d+)?$/.exec(req.headers.host)
  if (internal) {
    internalRequests.push({ host: req.headers.host, path: req.url, auth: Boolean(req.headers.authorization), cookie: Boolean(req.headers.cookie) })
    if (!virtual) { res.writeHead(404); return res.end('Unknown preview host') }
    if ([0, 2].includes(Number(virtual[1])) && req.url === '/') { res.writeHead(307, { location: '/enter' }); return res.end() }
    if (req.url === '/preview.css') { res.writeHead(200, { 'content-type': 'text/css' }); return res.end(':root{--internal-route-loaded:1}') }
  }
  res.setHeader('content-type', req.url === '/mark.svg' ? 'image/svg+xml' : 'text/html; charset=utf-8')
  const index = internal ? Number(virtual[1]) : Number(req.url.split('/').at(-1)) || 0
  const html = previewPage(index)
  res.end(req.url === '/mark.svg' ? mark : internal ? html.replace('<meta ', '<link rel="stylesheet" href="/preview.css"><meta ') : html)
})
site.listen(0, '127.0.0.1'); await once(site, 'listening')
const siteURL = `http://127.0.0.1:${site.address().port}`
const media = createAppMedia({ dir: process.env.APPS_LAUNCHER_CACHE || process.env.TMPDIR + '/launcher-media-cache', appsDomain: 'example.com',
  ...(internal ? { previewBase: `http://preview.example.test:${site.address().port}`, previewAddr: '127.0.0.1' } : {}) }, {
  capture: async options => {
    const index = Number(new URL(options.url).hostname.split('.')[0].slice(4)); captures.push(index)
    if ([1, 3].includes(index)) throw new Error('Fixture unavailable graphics context')
    return captureAppPage({ ...options, executable: process.env.APP_PREVIEW_CHROME || process.env.LOAM_CHROME, url: internal ? options.url : `${siteURL}/site/${index}` })
  },
  log: { info: row => captureLogs.push(row), warn: row => captureLogs.push(row) },
  ...(!internal ? { icons: createAppIcons({ fetcher: (url, options) => {
    const at = new URL(url), index = Number(at.hostname.split('.')[0].slice(4))
    return fetch(siteURL + (at.pathname === '/mark.svg' ? '/mark.svg' : `/site/${index}`), options)
  } }) } : {}),
})
try {
  for (const theme of themes) for (const width of [390, 1180, 1440]) for (const mode of ['light', 'dark']) {
    const host = await profileHost({ appOverrides: { appMedia: media } }), phone = width < 1100, label = `${width}-${theme}-${mode}`
    const context = await browser.newContext({ viewport: { width, height: phone ? 844 : 900 }, colorScheme: mode, reducedMotion: 'reduce', serviceWorkers: 'block' })
    const page = await context.newPage(), errors = []
    page.on('pageerror', e => errors.push(e.message)); page.on('console', m => { if (m.type() === 'error') errors.push({ text: m.text(), url: m.location().url }) }); page.setDefaultTimeout(20000)
    const imageRequests = []; page.on('request', r => { if (r.resourceType() === 'image') imageRequests.push(r.url()) })
    const catalog = names.map((name, i) => ({ name, url: `https://app-${i}.example.com/`, owner: host.f.agents[i === 4 ? 1 : 0].id, frame: ![2, 5, 6, 10].includes(i), source: 'yours', kind: i === 2 ? 'project' : 'site', access: i === 6 ? 'public' : i === 4 ? 'shared' : 'private', expires: i === 4 ? new Date(Date.now() + 6 * 864e5).toISOString() : i === 5 ? '2099-01-01' : null }))
    const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
    // Complete the ordinary boot contract so the console assertion covers the
    // whole page, rather than hiding unrelated missing-fixture 404s.
    host.routes['GET /v1/plugins/daily-notes'] = (req, res) => json(res, { plugin: { id: 'daily-notes', on: false } })
    host.routes['GET /v1/sessions/live'] = (req, res) => json(res, { sessions: [], box: { sessions: { live: 0 } } })
    host.routes['GET /v1/sessions/live/events'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n') }
    host.routes['GET /v1/limits'] = (req, res) => json(res, { attachment_max_bytes: 10485760, attachment_types: [] })
    host.routes['GET /v1/integrations/github'] = (req, res) => json(res, SHAPES.unregistered)
    host.routes['GET /v1/integrations/github/accounts'] = (req, res) => json(res, { accounts: [] })
    host.routes['GET /v1/apps'] = (req, res) => json(res, { apps: catalog, sources: ['yours'] })
    host.routes['GET /v1/settings/appearance'] = (req, res) => json(res, { phone: { theme, mode, size: 100 }, desktop: { theme, mode, size: 100 } })
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__launcher = { state, apps, panels, page };' }))
    if (before && process.env.APPS_LAUNCHER_BASELINE_DIR) {
      for (const file of ['apps.js', 'apps.css']) await context.route(`**/${file}`, route => route.fulfill({ contentType: file.endsWith('.js') ? 'text/javascript' : 'text/css', path: path.join(process.env.APPS_LAUNCHER_BASELINE_DIR, file) }))
    }
    await context.route(/^https:\/\/app-\d+\.myloam\.space\//, route => route.fulfill({ contentType: 'text/html', body: previewPage(Number(new URL(route.request().url()).hostname.split('.')[0].slice(4))) }))
    if (before) { await Promise.all(catalog.map(app => media.resolve(app, { origin: host.url }))); await media.idle() }
    const capture = async suffix => {
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, label)
      if (out) await page.screenshot({ path: path.join(out, `${before ? 'before' : 'after'}-${suffix}-${label}.png`), animations: 'disabled' })
    }
    try {
      await page.goto(host.url); await page.waitForFunction(() => globalThis.__launcher?.state.agentsReady)
      await page.getByRole('button', { name: 'Apps', exact: true }).click()
      await page.locator('.apps-row').first().waitFor()
      if (!before) {
        await page.waitForFunction(() => __launcher.apps.st.media.size === 11 && [...__launcher.apps.st.media.values()].every(m => !m.pending), null, { timeout: 90000 })
        await page.locator('.apps-preview-image.loaded').first().waitFor()
      }
      await capture('catalog')
      if (!before) {
        assert.equal(await page.locator('.apps-group > h2').count(), 0)
        assert.equal(await page.locator('.apps-row').count(), names.length)
        const browserOnly = page.getByRole('button', { name: 'Open Reading room in browser', exact: true })
        await browserOnly.locator('.apps-preview-image.loaded').waitFor()
        assert.equal(await browserOnly.locator('.apps-site-icon.loaded').count(), 1)
        assert.equal(await browserOnly.locator('.apps-monogram').evaluate(node => getComputedStyle(node).visibility), 'hidden')
        if (phone) {
          const markBox = await browserOnly.locator('.apps-site-icon').boundingBox(), fieldBox = await browserOnly.locator('.apps-preview').boundingBox()
          assert.ok(markBox.width <= 20 && markBox.height <= 20)
          assert.ok(markBox.x < fieldBox.x + fieldBox.width / 2 && markBox.y > fieldBox.y + fieldBox.height / 2)
        }
        for (const name of ['Atlas-demo', 'A little weather', 'Reading room', 'Playground']) {
          assert.equal(await page.getByRole('button', { name: `Open ${name} in browser`, exact: true }).locator('.apps-preview-image.loaded').count(), 1)
        }
        if (phone) assert.ok(await page.getByRole('button', { name: 'Open Atlas-bench in browser', exact: true }).locator('.apps-monogram').evaluate(node => parseFloat(getComputedStyle(node).fontSize)) <= 32)
        const contrast = await page.evaluate(() => {
          const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1
          const ctx = canvas.getContext('2d', { willReadFrequently: true })
          const rgba = value => { ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = value; ctx.fillRect(0, 0, 1, 1); return [...ctx.getImageData(0, 0, 1, 1).data].map((v, i) => i === 3 ? v / 255 : v) }
          const mix = (front, back) => [...front.slice(0, 3).map((v, i) => v * front[3] + back[i] * (1 - front[3])), 1]
          const luminance = color => color.slice(0, 3).map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4).reduce((n, v, i) => n + v * [.2126, .7152, .0722][i], 0)
          return [...document.querySelectorAll('.apps-monogram,.apps-name,.apps-secondary,.apps-expiry')].filter(n => n.getClientRects().length && getComputedStyle(n).visibility !== 'hidden').map(node => {
            const layers = []; let at = node
            while (at) { layers.push(rgba(getComputedStyle(at).backgroundColor)); at = at.parentElement }
            const bg = layers.reverse().reduce((back, front) => mix(front, back), [255, 255, 255, 1])
            const fg = mix(rgba(getComputedStyle(node).color), bg), a = luminance(bg), b = luminance(fg)
            return { text: node.textContent, ratio: (Math.max(a, b) + .05) / (Math.min(a, b) + .05) }
          })
        })
        assert.ok(contrast.every(pair => pair.ratio >= 4.5), `${label}: ${JSON.stringify(contrast.filter(p => p.ratio < 4.5))}`)
        reports.push({ theme, width, mode, contrastPairs: contrast.length, minimumContrast: Math.min(...contrast.map(p => p.ratio)) })
        if (phone) {
          const boxes = await page.locator('.apps-row').evaluateAll(rows => rows.slice(0, 3).map(n => { const r = n.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height } }))
          assert.ok(boxes[0].x < boxes[1].x && boxes[0].x === boxes[2].x && boxes[2].y > boxes[0].y)
          assert.ok(boxes.every(b => b.width >= 150 && b.height >= 170))
          const action = page.getByRole('button', { name: 'Details for Atlas-maps', exact: true })
          const actionBox = await action.boundingBox(), cardBox = await action.locator('..').locator('..').boundingBox()
          assert.ok(actionBox.width >= 44 && actionBox.height >= 44 && actionBox.x >= cardBox.x && actionBox.y >= cardBox.y && actionBox.x + actionBox.width <= cardBox.x + cardBox.width + 1 && actionBox.y + actionBox.height <= cardBox.y + cardBox.height + 1)
          await action.click()
          const details = page.getByRole('dialog', { name: 'Atlas-maps', exact: true })
          await details.waitFor(); assert.match(await details.innerText(), /Shared/); assert.match(await details.innerText(), /Ends/)
          await details.getByRole('button', { name: 'Copy link', exact: true }).click()
          await details.getByRole('status').filter({ hasText: /Copied|could not be copied/ }).waitFor()
          await capture('details'); await page.keyboard.press('Escape'); await details.waitFor({ state: 'detached' })
          assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), 'Details for Atlas-maps')
          const held = page.getByRole('button', { name: 'Open Atlas-bench in browser', exact: true })
          await held.dispatchEvent('pointerdown', { pointerType: 'touch', clientX: 30, clientY: 150 })
          await page.getByRole('dialog', { name: 'Atlas-bench', exact: true }).waitFor()
          await held.dispatchEvent('pointerup', { pointerType: 'touch' }); await page.keyboard.press('Escape')
          await page.getByRole('dialog', { name: 'Atlas-bench', exact: true }).waitFor({ state: 'detached' })
          const popupPromise = page.waitForEvent('popup')
          await page.getByRole('button', { name: 'Open Atlas in browser', exact: true }).click()
          const popup = await popupPromise; await popup.waitForLoadState('domcontentloaded')
          assert.equal(new URL(popup.url()).hostname, 'app-0.example.com')
          await popup.close(); assert.equal(await page.locator('iframe').count(), 0)
        } else {
          assert.equal(await page.locator('.apps-expiry').count(), 1)
          const heights = await page.locator('.apps-row').evaluateAll(rows => rows.map(n => n.getBoundingClientRect().height))
          assert.ok(Math.max(...heights) <= 80)
          const first = page.getByRole('button', { name: 'Open Atlas', exact: true })
          await first.click(); await page.locator('[data-panel=main] iframe').waitFor()
          assert.equal(await first.getAttribute('aria-current'), 'page')
          await page.locator('[data-panel=main]').getByRole('button', { name: 'Copy link', exact: true }).click()
          await page.locator('[data-panel=main]').getByRole('status').filter({ hasText: /Copied|could not be copied/ }).waitFor()
          await page.locator('[data-panel=main]').frameLocator('iframe').locator('#counter').click()
          await page.evaluate(() => { window.__keptApp = document.querySelector('[data-panel=main] iframe') })
          await page.getByRole('button', { name: 'Apps', exact: true }).click()
          assert.equal(await page.evaluate(() => __keptApp === document.querySelector('[data-panel=main] iframe')), true)
          if (theme === 'latte') await capture('selected')
          const next = page.getByRole('button', { name: 'Open Atlas-bench', exact: true })
          await next.focus(); await page.keyboard.press('Tab')
          assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), 'Open Atlas-bench to the side')
          await page.keyboard.press('Enter')
          await page.locator('[data-panel=side] iframe').waitFor()
          await page.getByRole('button', { name: 'Apps', exact: true }).click()
          assert.equal(await page.locator('.apps-row.in-side').count(), 1)
          if (theme === 'latte') await capture('side')
          const popupPromise = page.waitForEvent('popup')
          await page.getByRole('button', { name: 'Open Reading room in browser', exact: true }).click()
          await (await popupPromise).close()
          assert.equal(await page.evaluate(() => __keptApp === document.querySelector('[data-panel=main] iframe')), true)
        }
      }
      assert.ok(imageRequests.every(url => new URL(url).origin === new URL(host.url).origin), `${label}: external image probe`)
      assert.equal(await page.locator('iframe[src*="preview.example.test"],a[href*="preview.example.test"]').count(), 0)
      if (!before) assert.equal(await page.locator('.apps-preview-image.loaded').count(), 8)
      assert.deepEqual(errors, [], label)
      reports.push({ theme, width, mode, errors, checks: before ? 'before capture' : 'launcher layout, icons, details, browser launch or panel selection and rail continuity' })
    } finally { await context.close(); await host.close() }
  }
  if (!before && captures.length) {
    const successes = captureLogs.filter(row => row.state === 'ok')
    assert.equal(successes.length, 8)
    assert.ok(successes.every(row => Number.isInteger(row.ms) && row.ms >= 0))
    assert.ok(successes.some(row => row.app === 'Reading room'))
  }
  if (out) await writeFile(path.join(out, before ? 'before.json' : 'browser.json'), JSON.stringify(reports, null, 2) + '\n')
  reports.push({ captures, captureLogs, siteRequests, internal, internalRequests, conventionalProbes: siteRequests.filter(url => /apple-touch-icon|favicon/.test(url)) })
  if (out) await writeFile(path.join(out, before ? 'before.json' : 'browser.json'), JSON.stringify(reports, null, 2) + '\n')
  console.log(JSON.stringify(reports, null, 2))
} finally { await media.idle(); site.closeAllConnections(); site.close(); await browser.close() }
