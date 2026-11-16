// Browser proof for a large embedded-image prototype. Fixture data only;
// no production vault, broker or agent is read or written.
import assert from 'node:assert/strict'
import http from 'node:http'
import path from 'node:path'
import { once } from 'node:events'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { createApp } from '../src/server.js'
import { readConfig } from '../src/config.js'
import { fakeBroker, TOKEN, shut } from './harness.js'
import { filesHost } from './files-host.js'
import { row, readAnswer, listAnswer } from './fixtures/files-contract.js'
import { LARGE_HTML, LARGE_HTML_BYTES, LARGE_HTML_PATH, TEXT_MAX } from './fixtures/large-html.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.LARGE_HTML_EVIDENCE || path.join(process.env.TMPDIR || '.', 'large-html')
await mkdir(out, { recursive: true })
const source = await readFile('public/app.js', 'utf8')
const json = (res, status, body) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
const { routes } = filesHost()
const originalRead = routes['GET /v1/files/read']
const resource = p => row('vault', p, { size: LARGE_HTML_BYTES })
routes['GET /v1/files/stat'] = (req, res, url) => json(res, 200, resource(url.searchParams.get('path')))
routes['GET /v1/files/list'] = (req, res, url) => json(res, 200, listAnswer(row('vault', url.searchParams.get('path') || '', { kind: 'folder' }), [resource(LARGE_HTML_PATH), resource('Prototypes/Long notes.txt')]))
routes['GET /v1/files/read'] = (req, res, url) => {
  const p = url.searchParams.get('path')
  if (p === LARGE_HTML_PATH || p.endsWith('.txt')) return json(res, 413, { error: 'file is 3232179 bytes; text read limit 2097152', size: LARGE_HTML_BYTES, max: TEXT_MAX })
  if (p.endsWith('Unsupported.json')) return json(res, 415, { error: 'not UTF-8 text' })
  if (p.endsWith('Small.html')) return json(res, 200, readAnswer(row('vault', p), '<h1>Small page</h1>', 'rev1'))
  return originalRead(req, res, url)
}
routes['GET /v1/files/download'] = (req, res) => { res.writeHead(200, { 'content-type': 'application/octet-stream', 'content-length': LARGE_HTML_BYTES }); res.end(LARGE_HTML) }
const broker = await fakeBroker(routes)
let appUrl
const cookies = []
const proxy = http.createServer((req, res) => {
  cookies.push(req.headers.cookie ?? null)
  const upstream = http.request(`${appUrl}${req.url}`, { method: req.method, headers: req.headers }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res) })
  upstream.on('error', () => res.destroy())
  req.pipe(upstream)
})
proxy.listen(0, '127.0.0.1'); await once(proxy, 'listening')
const contentOrigin = `http://localhost:${proxy.address().port}`
const { server } = createApp({ config: readConfig({ BROKER_URL: broker.url, BROKER_TOKEN: TOKEN, LOG_LEVEL: 'off', FILES_CONTENT_ORIGIN: contentOrigin }) })
server.listen(0, '127.0.0.1'); await once(server, 'listening')
appUrl = `http://127.0.0.1:${server.address().port}`
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const results = []
const textReads = () => broker.seen.filter(r => r.path === '/v1/files/read' && new URLSearchParams(r.search).get('path') === LARGE_HTML_PATH).length
const luma = rgb => rgb.map(v => { v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4 }).reduce((a, v, i) => a + v * [.2126, .7152, .0722][i], 0)
try {
  assert.equal((await fetch(`${appUrl}/up`)).status, 200)
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) for (const side of width === 1440 ? [false, true] : [false]) {
    routes['GET /v1/settings/appearance'] = (req, res) => json(res, 200, { phone: { theme: 'latte', mode, size: 100 }, desktop: { theme: 'latte', mode, size: 100 } })
    const label = `${width}-${mode}${side ? '-side' : ''}`
    const context = await browser.newContext({ viewport: { width, height: 900 }, colorScheme: mode, reducedMotion: 'reduce' })
    await context.addCookies([{ name: 'app-only', value: 'fixture-session', url: appUrl, httpOnly: true, sameSite: 'Lax' }])
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__largeHtml = { panels, page };' }))
    const page = await context.newPage(), errors = []
    page.setDefaultTimeout(15000)
    page.on('pageerror', error => errors.push(error.message))
    await page.addInitScript(app => { if (window === top && location.origin === app) localStorage.setItem('isolation-secret', 'fixture') }, appUrl)
    const readsBefore = textReads()
    if (side) {
      await page.goto(`${appUrl}/files/vault/Notes/plan.md`)
      await page.locator('[data-panel=main] .fs-preview').waitFor()
      await page.evaluate(p => { window.__keptMain = document.querySelector('[data-panel=main] .fs-file'); __largeHtml.panels.openSide({ kind: 'file', root: 'vault', path: p }) }, LARGE_HTML_PATH)
    } else await page.goto(`${appUrl}/files/vault/${LARGE_HTML_PATH.split('/').map(encodeURIComponent).join('/')}`)
    const pane = side ? page.locator('[data-panel=side]') : page.locator('.fs-file')
    const frame = pane.frameLocator('.fs-iframe')
    await frame.locator('#options').waitFor()
    await page.waitForFunction(mode => document.documentElement.dataset.look === 'latte' && document.documentElement.dataset.theme === mode, mode)
    const imageSizes = await frame.locator('img').evaluateAll(async images => { await Promise.all(images.map(img => img.decode())); return images.map(img => ({ complete: img.complete, width: img.naturalWidth, height: img.naturalHeight })) })
    assert.equal(imageSizes.length, 4)
    assert.ok(imageSizes.every(img => img.complete && img.width === 512 && img.height === 384))
    assert.equal(textReads(), readsBefore, 'opening live HTML performs no text read')
    await frame.locator('#options').click()
    assert.equal(await frame.locator('body').getAttribute('data-layout'), 'gallery')
    const contentFrame = page.frames().find(f => f.url().startsWith(contentOrigin))
    const isolation = await contentFrame.evaluate(async app => {
      const result = {}
      for (const [name, attempt] of [['parentDOM', () => parent.document.title], ['parentStorage', () => parent.localStorage.getItem('isolation-secret')]]) {
        try { result[name] = attempt() } catch (e) { result[name] = e.name }
      }
      try { result.relay = (await fetch(`${app}/api/agents`)).status } catch (e) { result.relay = e.name }
      return result
    }, appUrl)
    assert.deepEqual(isolation, { parentDOM: 'SecurityError', parentStorage: 'SecurityError', relay: 'TypeError' })
    const geometry = await pane.locator('.fs-iframe').evaluate(el => { const r = el.getBoundingClientRect(), p = el.closest('.fs-file').getBoundingClientRect(); return { width: r.width, paneWidth: p.width, bottom: r.bottom, paneBottom: p.bottom, height: r.height, overflow: document.documentElement.scrollWidth > innerWidth } })
    assert.equal(geometry.width, geometry.paneWidth)
    assert.equal(geometry.bottom, geometry.paneBottom)
    assert.ok(geometry.height > 500)
    assert.equal(geometry.overflow, false)
    await page.screenshot({ path: path.join(out, `preview-${label}.png`) })
    if (side) await pane.getByRole('button', { name: 'Item actions', exact: true }).click()
    else await pane.locator('.fs-filemenu').click()
    await page.locator('.fs-menu-source').click()
    const error = pane.getByRole('alert')
    await error.getByText('Source unavailable', { exact: true }).waitFor()
    const message = await error.textContent()
    assert.match(message, /3.1 MB.*2.0 MB/)
    assert.doesNotMatch(message, /3232179|2097152|text read limit/)
    await error.getByRole('link', { name: 'Download', exact: true }).waitFor()
    assert.equal(textReads(), readsBefore + 1)
    const paint = await error.locator('.fs-error-detail p').evaluate(el => {
      const s = getComputedStyle(el), b = getComputedStyle(el.closest('.banner'))
      return { text: s.color, background: b.backgroundColor, button: getComputedStyle(el.parentElement.querySelector('.fs-error-action')).color }
    })
    const rgb = value => value.match(/[\d.]+/g).slice(0, 3).map(Number)
    const lum = [luma(rgb(paint.text)), luma(rgb(paint.background))].sort((a, b) => b - a)
    const contrast = (lum[0] + .05) / (lum[1] + .05)
    assert.ok(contrast >= 4.5, `${label}: ${contrast}`)
    assert.equal(paint.button, paint.text)
    await page.screenshot({ path: path.join(out, `source-limit-${label}.png`) })
    await error.getByRole('button', { name: 'Back to preview' }).click()
    await frame.locator('#options').click()
    assert.equal(await frame.locator('body').getAttribute('data-layout'), 'gallery')
    if (side) assert.equal(await page.evaluate(() => window.__keptMain === document.querySelector('[data-panel=main] .fs-file')), true)
    if (!side) {
      await page.goto(`${appUrl}/files/vault/Prototypes/Long%20notes.txt`)
      await page.getByRole('alert').getByText('Text view unavailable', { exact: true }).waitFor()
      await page.getByRole('alert').getByRole('link', { name: 'Download', exact: true }).waitFor()
      await page.screenshot({ path: path.join(out, `text-limit-${label}.png`) })
      await page.goto(`${appUrl}/files/vault/Prototypes/Unsupported.json`)
      await page.getByRole('alert').getByText('This file can’t be shown as text.', { exact: true }).waitFor()
      assert.doesNotMatch(await page.getByRole('alert').textContent(), /UTF-8|undefined|NaN/)
      // Force the existing unconfigured-origin branch, exercising the real
      // static route and download fallback with the same 3.2 MB document.
      await context.route('**/api/files/live?*', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'Live preview is not configured.' }) }))
      await page.goto(`${appUrl}/files/vault/${LARGE_HTML_PATH.split('/').map(encodeURIComponent).join('/')}`)
      const staticFrame = page.frameLocator('.fs-iframe')
      await staticFrame.locator('img').first().waitFor()
      assert.ok((await staticFrame.locator('img').evaluateAll(async images => { await Promise.all(images.map(img => img.decode())); return images.every(img => img.naturalWidth === 512) })))
      await staticFrame.locator('#options').click()
      assert.equal(await staticFrame.locator('body').getAttribute('data-layout'), 'grid', 'static fallback stays script-free')
      await page.screenshot({ path: path.join(out, `static-${label}.png`) })
    }
    assert.deepEqual(errors, [])
    results.push({ width, mode, side, bytes: LARGE_HTML_BYTES, imageSizes, geometry, source: 'read only on request; limit explained; preview remains usable', contrast, isolation, mainRetained: side || null, static: !side || null, pageErrors: errors })
    await context.close()
  }
  assert.ok(cookies.every(cookie => cookie === null), 'app-only cookie never reaches content origin')
  assert.equal(broker.seen.some(r => ['POST', 'PUT', 'PATCH', 'DELETE'].includes(r.method)), false, 'zero broker writes')
  await writeFile(path.join(out, 'browser.json'), JSON.stringify({ contexts: results, contentCookieLeak: false, brokerWrites: 0 }, null, 2))
  console.log(JSON.stringify({ contexts: results.length, bytes: LARGE_HTML_BYTES, embeddedImages: 4, contentCookieLeak: false, brokerWrites: 0, minimumErrorContrast: Math.min(...results.map(r => r.contrast)) }))
} finally { await browser.close(); await Promise.all([shut(server), shut(proxy), shut(broker.server)]) }
