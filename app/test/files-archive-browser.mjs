import assert from 'node:assert/strict'
import http from 'node:http'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { mkdir, readFile, rm } from 'node:fs/promises'
import { once } from 'node:events'
import path from 'node:path'

import { oneProductFixture } from './one-product-fixture.mjs'
import { filesHost } from './files-host.js'
import { row, rootsAnswer } from './fixtures/files-contract.js'
import { auditControls, assertControls } from './control-style-guard.mjs'
import { closing } from './harness.js'

// Models Caddy's `forward_auth` gate: injects Remote-User/Remote-Name on every
// request (including Chromium DownloadManager requests, which ignore
// Playwright's extraHTTPHeaders).
async function authProxy(targetUrl) {
  const target = new URL(targetUrl)
  const server = http.createServer((req, res) => {
    const headers = { ...req.headers, host: target.host, 'remote-user': 'sam', 'remote-name': 'Sam Rivera' }
    const up = http.request({ host: target.hostname, port: target.port, path: req.url, method: req.method, headers }, (upRes) => {
      res.writeHead(upRes.statusCode, upRes.headers)
      upRes.pipe(res)
    })
    up.on('error', () => res.destroy())
    req.pipe(up)
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  return { server, url: `http://127.0.0.1:${server.address().port}` }
}

const execFileAsync = promisify(execFile)
const watchdog = setTimeout(() => {
  console.error('files-archive-browser timed out after 120s')
  process.exit(1)
}, 120_000)
watchdog.unref()
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.ARCHIVE_EVIDENCE || path.join(process.env.TMPDIR || '/tmp', 'files-archive-evidence')
await mkdir(out, { recursive: true })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const source = await readFile('public/app.js', 'utf8')
const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }

async function makeFixture({ theme, mode, shortcuts = [] }) {
  const app = await oneProductFixture()
  const files = filesHost()
  for (const [k, v] of Object.entries(files.routes)) if (k.includes('/v1/files/') || k.includes('/v1/limits')) app.routes[k] = v
  const origList = app.routes['GET /v1/files/list']
  const origDownload = app.routes['GET /v1/files/download']
  app.routes['GET /v1/files/list'] = (req, res, url) => {
    const r = url.searchParams.get('root'), p = url.searchParams.get('path') ?? ''
    if (r === 'vault' && p === 'Notes/Old') return json(res, { folder: row('vault', 'Notes/Old', { kind: 'folder' }), entries: [], total: 0, cursor: null, next_cursor: null, truncated: false })
    if (r === 'vault' && p === 'Notes') return json(res, { folder: row('vault', 'Notes', { kind: 'folder' }), entries: [row('vault', 'Notes/Old', { kind: 'folder' }), row('vault', 'Notes/plan.md', { size: 28 }), row('vault', 'Notes/pic.png', { size: 16 })], total: 3, cursor: null, next_cursor: null, truncated: false })
    if (r === 'spaces' && p === '') return json(res, { folder: row('spaces', '', { kind: 'folder' }), entries: [row('spaces', 'commons', { kind: 'folder' })], total: 1, cursor: null, next_cursor: null, truncated: false })
    if (r === 'spaces' && p === 'commons') return json(res, { folder: row('spaces', 'commons', { kind: 'folder' }), entries: [row('spaces', 'commons/notes.md', { size: 10 })], total: 1, cursor: null, next_cursor: null, truncated: false })
    return origList(req, res, url)
  }
  app.routes['GET /v1/files/download'] = (req, res, url) => {
    const r = url.searchParams.get('root'), p = url.searchParams.get('path')
    if (r === 'spaces' && p === 'commons/notes.md') { res.writeHead(200, { 'content-type': 'text/markdown' }); return res.end('# Commons\n') }
    return origDownload(req, res, url)
  }
  const roots = rootsAnswer()
  roots.roots = roots.roots.filter(r => r.root !== 'system')
  roots.roots[0].description = 'Your notes — inbox, daily notes, projects, people. The one place you and your agents both write.'
  roots.roots[1].description = 'Each agent’s own home — its soul, memory and skills — and the Commons they share. They write here; you read.'
  roots.shortcuts = shortcuts
  app.routes['GET /v1/files/roots'] = (req, res) => json(res, roots)
  app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, {
    phone: { theme, mode, size: 100, options: {} },
    desktop: { theme, mode, size: 100, options: {} },
  })
  return app
}

async function verifyZipFile(zipPath) {
  const { stdout } = await execFileAsync('python3', [
    '-c',
    `import json, sys, zipfile
with zipfile.ZipFile(sys.argv[1], 'r') as z:
  print(json.dumps({'bad': z.testzip(), 'names': z.namelist()}))`,
    zipPath,
  ])
  const parsed = JSON.parse(stdout)
  assert.equal(parsed.bad, null, `corrupt zip at ${zipPath}`)
  return parsed.names
}

try {
  // 1. Empty Shortcuts captures in Latte and Plain, 390 and 1440, light and dark.
  for (const theme of ['latte', 'plain']) {
    for (const width of [390, 1440]) {
      for (const mode of ['light', 'dark']) {
        const app = await makeFixture({ theme, mode, shortcuts: [] })
        const context = await browser.newContext({
          viewport: { width, height: width === 390 ? 844 : 900 },
          colorScheme: mode,
          isMobile: width === 390,
          hasTouch: width === 390,
          reducedMotion: 'reduce',
          extraHTTPHeaders: { 'Remote-User': 'sam', 'Remote-Name': 'Sam Rivera' },
        })
        await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__arch={store,files,goFiles,goSettings};' }))
        const page = await context.newPage()
        const errors = []
        page.on('pageerror', e => errors.push(e.message))
        try {
          await page.goto(`${app.url}/files`)
          await page.locator('.fs-rootcard').first().waitFor()
          await page.locator('.fs-noshort').waitFor()
          await page.evaluate(() => document.fonts.ready)

          // Verify no card surface or icon tile on empty Shortcuts.
          const check = await page.evaluate(() => {
            const box = document.querySelector('.fs-shortcuts')
            const noshort = box.querySelector('.fs-noshort')
            const kicker = box.querySelector('.fs-kicker')
            return {
              hasCard: Boolean(box.querySelector('.card')),
              hasIcon: Boolean(box.querySelector('.icon-tile')),
              text: noshort.textContent,
              kickerLeft: Math.round(kicker.getBoundingClientRect().left),
              noshortLeft: Math.round(noshort.getBoundingClientRect().left),
              overflow: document.documentElement.scrollWidth > innerWidth,
            }
          })
          assert.equal(check.hasCard, false)
          assert.equal(check.hasIcon, false)
          assert.equal(check.text, 'Nothing pinned yet. Add any file or folder from its ⋯ menu.')
          assert.equal(check.kickerLeft, check.noshortLeft)
          assert.equal(check.overflow, false)
          assert.deepEqual(errors, [])
          await page.screenshot({ path: path.join(out, `shortcuts-empty-${theme}-${width}-${mode}.png`), animations: 'disabled' })
        } finally {
          await context.close()
          await app.close()
        }
      }
    }
  }

  // 2. Populated Shortcuts + Download UI (Vault root ⋯ menu & Settings › Files) + real download.
  for (const width of [390, 1440]) {
    for (const mode of ['light', 'dark']) {
      const app = await makeFixture({
        theme: 'latte',
        mode,
        shortcuts: [
          { label: 'Notes', resource: row('vault', 'Notes', { kind: 'folder' }) },
          { label: 'plan.md', resource: row('vault', 'Notes/plan.md', { kind: 'file' }) },
        ],
      })
      const proxy = await authProxy(app.url)
      const context = await browser.newContext({
        viewport: { width, height: width === 390 ? 844 : 900 },
        colorScheme: mode,
        isMobile: width === 390,
        hasTouch: width === 390,
        reducedMotion: 'reduce',
        acceptDownloads: true,
      })
      await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__arch={store,files,goFiles,goSettings};' }))
      const page = await context.newPage()
      const errors = []
      page.on('pageerror', e => errors.push(e.message))
      try {
        await page.goto(`${proxy.url}/files`)
        await page.locator('.fs-host-shortcut').nth(1).waitFor()
        await page.evaluate(() => document.fonts.ready)
        await page.screenshot({ path: path.join(out, `shortcuts-two-latte-${width}-${mode}.png`), animations: 'disabled' })

        // Open Vault root -> ⋯ menu -> verify Download as zip.
        await page.locator('.fs-rootcard').first().click()
        await page.locator('.fs-menu').waitFor()
        await page.locator('.fs-menu').click()
        await page.locator('.fs-menu-archive').waitFor()
        await page.evaluate(() => document.fonts.ready)
        assert.equal(await page.locator('.fs-menu-archive').innerText(), 'Download as zip')
        await page.screenshot({ path: path.join(out, `files-vault-menu-${width}-${mode}.png`), animations: 'disabled' })

        // Actually download Vault zip from the ⋯ menu on one run.
        if (width === 1440 && mode === 'light') {
          const [download] = await Promise.all([
            page.waitForEvent('download'),
            page.locator('.fs-menu-archive').click(),
          ])
          assert.equal(download.suggestedFilename(), 'vault.zip')
          const dlPath = path.join(out, 'browser-vault.zip')
          await download.saveAs(dlPath)
          const names = await verifyZipFile(dlPath)
          assert.ok(names.includes('Notes/plan.md'), `unexpected names: ${names}`)
          await rm(dlPath, { force: true })
        }

        // Open Settings › Files -> verify Your data row and control-style-guard.
        await page.evaluate(() => __arch.goSettings('files'))
        await page.locator('.fs-export-row').waitFor()
        await page.evaluate(() => document.fonts.ready)
        const rows = await auditControls(page, '.fs-export-btn')
        assertControls(rows)
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth || [...document.querySelectorAll('.setmain, .sheet.settings')].some(n => n.scrollWidth > n.clientWidth + 1))
        assert.equal(overflow, false)
        await page.screenshot({ path: path.join(out, `settings-files-export-${width}-${mode}.png`), animations: 'disabled' })

        if (width === 390 && mode === 'dark') {
          const [download] = await Promise.all([
            page.waitForEvent('download'),
            page.getByRole('button', { name: 'Download AI Spaces' }).click(),
          ])
          assert.equal(download.suggestedFilename(), 'ai-spaces.zip')
          const dlPath = path.join(out, 'browser-spaces.zip')
          await download.saveAs(dlPath)
          await verifyZipFile(dlPath)
          await rm(dlPath, { force: true })
        }
        assert.deepEqual(errors, [])
      } finally {
        await context.close()
        await closing(proxy)()
        await app.close()
      }
    }
  }

  // 3. Unauthenticated browser context (no Remote-User) gets plain-words refusal.
  const app = await makeFixture({ theme: 'latte', mode: 'light', shortcuts: [] })
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'light' })
  await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__arch={goSettings};' }))
  const page = await context.newPage()
  try {
    await page.goto(app.url)
    await page.waitForFunction(() => globalThis.__arch)
    await page.evaluate(() => __arch.goSettings('files'))
    await page.locator('.fs-export-row').waitFor()
    await page.getByRole('button', { name: 'Download the Vault' }).click()
    await page.locator('.fs-export-feedback.warn').waitFor()
    assert.equal(await page.locator('.fs-export-feedback.warn').innerText(), 'Sign in as the owner to download an archive.')
  } finally {
    await context.close()
    await app.close()
  }

  console.log(`PASS: browser tests and screenshots written to ${out}`)
} finally {
  clearTimeout(watchdog)
  await browser.close()
}
