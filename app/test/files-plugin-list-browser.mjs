// A file opened from a plugin's own list in Files (`files.root`, such as
// doc-comments' Shared Docs) leaves the list column on that list: on the
// desktop and the phone, and across a reload, Back and Forward, because the
// file's address carries the list (`?list=@<id>`). A file opened from its own
// folder, or by an address without a list, still shows its folder.
//
// Real Chromium against the fake host, through the real relay. Run:
//   LOAM_PLAYWRIGHT=/path/to/node_modules/playwright/index.mjs \
//   [LOAM_CHROME=/path/to/chrome-headless-shell] \
//   node --test app/test/files-plugin-list-browser.mjs
// `FILES_LIST_SHOTS=<dir>` keeps a screenshot of each step.
import test from 'node:test'
import assert from 'node:assert/strict'
import path from 'node:path'
import { filesHost } from './files-host.js'
import { row } from './fixtures/files-contract.js'
import { threadPage } from './thread-page.js'
import { fakeBroker, appAgainst, closing } from './harness.js'

const PW = process.env.LOAM_PLAYWRIGHT
const skip = PW ? false : 'LOAM_PLAYWRIGHT is not set (a Playwright install to drive Chromium)'
const SHOTS = process.env.FILES_LIST_SHOTS || ''

// A plugin with a list of its own: one row, a file that lives in Notes/,
// drawn as core's own file row (`entry`), as Shared Docs draws its rows.
const PLUGIN = `export function activate(loam) {
  const root = loam.places.files.root.add({
    id: 'shelf', name: 'Shelf', desc: 'Docs picked by hand',
    render({ entry }) {
      return loam.ui.h('div', { class: 'card card-group shelf' }, entry(${JSON.stringify(row('vault', 'Notes/plan.md', { size: 2048 }))}))
    },
  })
  return () => root.remove()
}`

const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
let broker = null, app = null, browser = null

async function start() {
  if (browser) return
  const { routes } = filesHost()
  threadPage(routes, {})
  routes['GET /v1/plugins'] = (req, res) => json(res, { api: 0, plugins: [{ id: 'shelf', name: 'Shelf', version: '0', api: 0, kind: ['extension'], places: ['files.root'], overrides: [], layer: 'yours', effective: true, on: true, switch: 'plugin', broken: null, warnings: [], has: { page_main: true, theme: false } }] })
  routes['GET /v1/plugins/shelf/files/page/main.js'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/javascript' }); res.end(PLUGIN) }
  broker = await fakeBroker(routes)
  app = await appAgainst(broker)
  const { chromium } = await import(PW)
  browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME || undefined, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
}

test.after(async () => {
  await browser?.close()
  if (broker) await closing(broker, app)()
})

async function page({ width = 1440, height = 900 } = {}) {
  await start()
  const phone = width < 600
  const ctx = await browser.newContext({ viewport: { width, height }, reducedMotion: 'reduce', isMobile: phone, hasTouch: phone })
  const p = await ctx.newPage()
  p.errors = []
  p.on('pageerror', (e) => p.errors.push(e.message))
  p.done = () => ctx.close()
  return p
}

const shot = async (p, name) => { if (SHOTS) await p.screenshot({ path: path.join(SHOTS, `${name}.png`) }) }
const listTitle = (p) => p.locator('.listcol .fs-title').first().textContent()
const fileOpen = (p) => p.waitForFunction(() => /The plan/.test(document.querySelector('.pane[data-panel="main"]')?.textContent || ''), null, { timeout: 8000 })
const listIsShelf = (p) => p.waitForFunction(() => document.querySelector('.listcol .fs-title')?.textContent === 'Shelf' && document.querySelector('.listcol .shelf .setrow'), null, { timeout: 8000 })

test('desktop: opening a file from a plugin\'s list keeps the list; reload, Back and Forward too', { skip }, async () => {
  const p = await page()
  await p.goto(`${app.url}/files/@shelf`)
  await listIsShelf(p)
  await p.click('.listcol .shelf .setrow')
  await fileOpen(p)
  assert.equal(await listTitle(p), 'Shelf', 'the list column stays on the plugin\'s list')
  assert.equal(await p.locator('.listcol .shelf .setrow.on').count(), 1, 'its row is the open one')
  const url = new URL(p.url())
  assert.equal(url.pathname, '/files/vault/Notes/plan.md')
  assert.equal(url.searchParams.get('list'), '@shelf', 'the address names the list')
  await shot(p, 'desktop-open')

  await p.reload()
  await fileOpen(p)
  await listIsShelf(p)
  assert.equal(await p.locator('.listcol .shelf .setrow.on').count(), 1)
  await shot(p, 'desktop-reload')

  await p.goBack()
  await p.waitForFunction(() => location.pathname === '/files/@shelf')
  await listIsShelf(p)
  await p.goForward()
  await fileOpen(p)
  await listIsShelf(p)
  assert.equal(new URL(p.url()).searchParams.get('list'), '@shelf')

  // The row's ⋯ menu goes to the file's own folder, one step away; the file
  // stays open.
  await p.click('.listcol .shelf .fs-rowmenu')
  await p.click('.fs-menu-folder')
  await p.waitForFunction(() => document.querySelector('.listcol .fs-title')?.textContent === 'Notes', null, { timeout: 8000 })
  assert.match(await p.locator('.pane[data-panel="main"]').textContent(), /The plan/)
  assert.deepEqual(p.errors, [])
  await p.done()
})

test('desktop: a file opened by its own address, without a list, still shows its folder', { skip }, async () => {
  const p = await page()
  await p.goto(`${app.url}/files/vault/Notes/plan.md`)
  await fileOpen(p)
  await p.waitForFunction(() => document.querySelector('.listcol .fs-title')?.textContent === 'Notes', null, { timeout: 8000 })
  assert.deepEqual(p.errors, [])
  await p.done()
})

test('phone: back from a file opened from a plugin\'s list is that list, and a reload keeps it', { skip }, async () => {
  const p = await page({ width: 430, height: 900 })
  await p.goto(`${app.url}/files/@shelf`)
  await p.waitForSelector('.shelf .setrow')
  await p.click('.shelf .setrow')
  await p.waitForFunction(() => /The plan/.test(document.body.textContent))
  assert.equal(new URL(p.url()).searchParams.get('list'), '@shelf')
  await p.reload()
  await p.waitForFunction(() => /The plan/.test(document.body.textContent))
  const back = p.locator('.fs-edhead .fs-back')
  assert.equal((await back.textContent()).trim(), 'Shelf', 'the way back names the list')
  await shot(p, 'phone-open')
  await back.click()
  await p.waitForSelector('.shelf .setrow')
  assert.equal(await p.locator('.fs-title').first().textContent(), 'Shelf')
  assert.deepEqual(p.errors, [])
  await p.done()
})
