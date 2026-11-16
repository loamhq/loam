// Actual app, clipboard and new tabs; published hosts and broker are fixtures.
import assert from 'node:assert/strict'
import path from 'node:path'
import { mkdir, writeFile } from 'node:fs/promises'
import { oneProductFixture } from './one-product-fixture.mjs'
import { filesHost } from './files-host.js'
import { appAgainst, closing } from './harness.js'
import { row, readAnswer } from './fixtures/files-contract.js'

const out = process.env.FILES_LINK_EVIDENCE || path.join(process.env.TMPDIR, 'file-links')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const host = await oneProductFixture()
Object.assign(host.routes, filesHost().routes)
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
host.routes['GET /v1/files/stat'] = (req, res, url) => json(res, row(url.searchParams.get('root'), url.searchParams.get('path')))
host.routes['GET /v1/files/read'] = (req, res, url) => json(res, readAnswer(row(url.searchParams.get('root'), url.searchParams.get('path')), '<h1>Preview</h1>', 'sha256:fixture'))
const app = await appAgainst(host.broker, { FILES_VAULT_URLS: 'https://notes.example.org', FILES_WEB_URLS: JSON.stringify([{ root: 'spaces', path: 'shared/www', url: 'https://files.example.org' }]) })
const reports = []
const filename = 'Plans with spaces/Café — 東京/My #1 100%.html'
const encoded = filename.split('/').map(encodeURIComponent).join('/')
try {
  for (const width of [390, 1440]) {
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 900 }, permissions: ['clipboard-read', 'clipboard-write'], reducedMotion: 'reduce' })
    const page = await context.newPage(), errors = [], requests = []
    page.setDefaultTimeout(15000)
    page.on('pageerror', e => errors.push(e.message))
    // These answer only in Chromium: a server-side unauthenticated probe
    // cannot succeed, reproducing the old fallback without a real login.
    await context.route(/^https:\/\/(notes|files)\.example\.org\//, async route => {
      const url = new URL(route.request().url()); requests.push(url.href)
      const asset = url.pathname.split('/').at(-1)
      const bodies = {
        'page.js': ['text/javascript', "document.body.dataset.script='running';fetch('data.json').then(r=>r.json()).then(d=>document.body.dataset.data=d.ok)"],
        'page.css': ['text/css', 'body{margin:0;padding:24px;font:18px system-ui;background:#f6f3ed}h1{color:rgb(21, 90, 60)}'],
        'data.json': ['application/json', '{"ok":"loaded"}'],
      }
      const [contentType, body] = bodies[asset] || ['text/html', '<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="page.css"><h1>A standalone page</h1><p>Scripts and relative assets work here.</p><script src="page.js"></script>']
      await route.fulfill({ contentType, body })
    })
    for (const [root, filePath, expected] of [
      ['vault', filename, `https://notes.example.org/${encoded}`],
      ['spaces', `shared/www/${filename}`, `https://files.example.org/${encoded}`],
    ]) {
      await page.goto(`${app.url}/files/${root}/${filePath.split('/').map(encodeURIComponent).join('/')}`)
      await page.locator('.fs-filemenu:visible').click()
      await page.getByRole('button', { name: 'Copy link', exact: true }).waitFor()
      await page.screenshot({ animations: 'disabled', path: path.join(out, `${root}-menu-${width}.png`) })
      await page.getByRole('button', { name: 'Copy link', exact: true }).click()
      await page.locator('.listnotice.inline').filter({ hasText: 'Link copied' }).waitFor()
      const copied = await page.evaluate(() => navigator.clipboard.readText())
      assert.equal(copied, expected)
      const standalone = await context.newPage()
      const response = await standalone.goto(copied)
      assert.equal(response.status(), 200)
      await standalone.locator('body[data-script=running][data-data=loaded]').waitFor()
      assert.equal(await standalone.locator('h1').evaluate(n => getComputedStyle(n).color), 'rgb(21, 90, 60)')
      assert.equal(await standalone.locator('.fs-file').count(), 0)
      assert.equal(await standalone.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      await standalone.screenshot({ animations: 'disabled', path: path.join(out, `${root}-standalone-${width}.png`) })
      await standalone.close()
      reports.push({ width, root, copied, standalone: 200, relativeAssets: 'JS, CSS, JSON loaded', fixtures: true })
    }
    await page.goto(`${app.url}/files/spaces/agent/private.md`)
    await page.locator('.fs-filemenu:visible').click()
    await page.locator('.fs-menu-copy').filter({ hasText: 'Copy app link' }).waitFor()
    await page.locator('.fs-menu-copy').click()
    await page.locator('.listnotice.inline').filter({ hasText: 'Link copied' }).waitFor()
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), `${app.url}/files/spaces/agent/private.md`)
    assert.equal(await page.locator('.fs-copylink').getAttribute('aria-label'), 'Copy app link')
    await page.screenshot({ animations: 'disabled', path: path.join(out, `app-link-menu-${width}.png`) })
    assert.deepEqual(errors, [])
    assert.equal(requests.some(url => url.endsWith('/page.js')), true)
    await context.close()
  }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify({ browser: browser.version(), productionAccess: false, reports }, null, 2) + '\n')
  console.log(JSON.stringify({ contexts: 2, standalonePages: reports.length, clipboard: 'passed', relativeAssets: 'passed', appLinkLabels: 'passed', productionAccess: false }))
} finally {
  await browser.close()
  await closing(app)()
  await host.close()
}
