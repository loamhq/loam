// Every doc-comments surface, photographed in Loam's real page (the app
// against its synthetic fake host, app/test/harness.js) with the plugin's
// real server on seeded docs (look_server.py): every installed theme, light
// and dark, at 1440 and 430. With AUDIT=1, core's own contract checks run
// over each frame (app/tools/colour-families.mjs, every look; and
// app/test/control-style-guard.mjs's palette check) and land in the report.
// Nothing real is opened; Shared with me is answered in the page.
//
//   LOAM_PLAYWRIGHT=…/playwright/index.mjs [LOAM_CHROME=…] OUT=<dir> \
//     node plugins/doc-comments/test/look-browser.mjs [surface,…]
// TREE=<checkout> photographs another checkout's app and plugin (a before);
// LOOKS, MODES, WIDTHS narrow the set; NO_SHOTS=1 audits only. On a shared
// box run it under a memory cap (docs/doc-comments.md, Working on it).
import path from 'node:path'
import fs from 'node:fs/promises'
import { spawn } from 'node:child_process'
import { pathToFileURL, fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
// Not a test: run by hand (or picked up by a `node --test test/*.mjs` glob,
// where it says so and stops).
if (!process.env.OUT || !process.env.LOAM_PLAYWRIGHT) {
  console.log('look-browser: skipped (set OUT and LOAM_PLAYWRIGHT to photograph every surface)')
  process.exit(0)
}
const TREE = path.resolve(process.env.TREE || path.join(HERE, '../../..'))
const OUT = path.resolve(process.env.OUT)
const PLUGIN = path.join(TREE, 'plugins/doc-comments')
const SERVER_PLUGIN = path.resolve(process.env.SERVER_PLUGIN || PLUGIN)
const imp = (p) => import(pathToFileURL(path.join(TREE, p)))
const { filesHost } = await imp('app/test/files-host.js')
const { row, listAnswer } = await imp('app/test/fixtures/files-contract.js')
const { threadPage } = await imp('app/test/thread-page.js')
const { fakeBroker, appAgainst, closing } = await imp('app/test/harness.js')
const { shippedPlugins } = await imp('app/src/shipped-plugins.js')
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT)
// Core's own contract checks (app/tools/colour-families.mjs, app/test/control-style-guard.mjs),
// run over every frame when AUDIT is set; the families check made to run in every look.
const AUDIT = Boolean(process.env.AUDIT)
const { inspectColourFamilies } = await import(pathToFileURL(path.join(process.env.AUDIT_APP || path.join(TREE, 'app'), 'tools/colour-families.mjs')))
const inspect = Function(`return (${inspectColourFamilies.toString().replace("if (document.documentElement.dataset.look !== 'latte') return []", '').replace("document.querySelectorAll('body *')", "document.querySelectorAll(':is(.dc, .dc-dlg-scrim, .dc-sd-sections, .dc-wm-state, .dc-sd-tip, .dc-list-toast) *')")})`)()
const { auditControls } = await import(pathToFileURL(path.join(process.env.AUDIT_APP || path.join(TREE, 'app'), 'test/control-style-guard.mjs')))
const CONTROLS = ':is(.dc, .dc-dlg, .dc-sd, .dc-wm, .dc-wm-state, .dc-list-toast, .dc-ap-foot) :is(button, select, input, textarea, [role=button]):not(.cm-content *)'

const ONLY = (process.argv[2] || '').split(',').filter(Boolean)
const LOOKS = (process.env.LOOKS || 'latte,plain,deep-forest,autumn,yosemite,mist-trail,foundry').split(',')
const MODES = (process.env.MODES || 'light,dark').split(',')
const WIDTHS = (process.env.WIDTHS || '1440,430').split(',').map(Number)
const FIXED = Date.parse('2026-11-17T18:00:00Z')

const server = spawn('python3', [path.join(HERE, 'look_server.py'), SERVER_PLUGIN, '0'], { stdio: ['ignore', 'pipe', 'inherit'] })
const gbase = await new Promise((resolve, reject) => {
  let buf = ''
  server.stdout.on('data', (d) => { buf += d; const m = /look: (http:\/\/127\.0\.0\.1:\d+)\//.exec(buf); if (m) resolve(m[1]) })
  server.on('exit', (c) => reject(new Error(`look_server exited ${c}`)))
})
const shareLinks = await (await fetch(`${gbase}/look/links`)).json()
const getDoc = async (p) => (await (await fetch(`${gbase}/x/doc-comments/doc?root=vault&path=${encodeURIComponent(p)}`)).json())
const docs = {}
for (const p of ['Essays/drafts/On staying.md', 'Essays/drafts/Long.md', 'Essays/drafts/Fresh.md']) docs[p] = (await getDoc(p)).text

const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
const texts = Object.fromEntries(Object.entries(docs).map(([p, t]) => [`vault:${p}`, t]))
const { routes } = filesHost({ texts })
threadPage(routes, { initial: [{ id: 't1', title: 'Essay notes', text: 'The essay is in Shared Docs.' }] })
const extra = {
  'vault:': [row('vault', 'Essays'), row('vault', 'Notes')],
  'vault:Essays': [row('vault', 'Essays/drafts')],
  'vault:Essays/drafts': Object.keys(docs).map((p) => row('vault', p, { size: docs[p].length })),
}
const find = (root, p) => Object.values(extra).flat().find((r) => r.root === root && r.path === p)
for (const key of ['GET /v1/files/stat', 'GET /v1/files/list']) {
  const orig = routes[key]
  routes[key] = (req, res, url, body) => {
    const root = url.searchParams.get('root'), p = url.searchParams.get('path') ?? ''
    if (key.endsWith('stat') && root === 'vault' && find('vault', p)) return json(res, find('vault', p))
    if (key.endsWith('list') && extra[`${root}:${p}`]) {
      const folder = p === '' ? { ...row(root, ''), capabilities: { read: true, download: false, write: false, create: true } } : find(root, p) ?? row(root, p)
      return json(res, listAnswer(folder, extra[`${root}:${p}`]))
    }
    return orig(req, res, url, body)
  }
}
const values = { phone: { theme: 'latte', mode: 'light', size: 100 }, desktop: { theme: 'latte', mode: 'light', size: 100 }, location: { latitude: 0, longitude: 0, timeZone: 'UTC', source: 'geolocation' } }
routes['GET /v1/settings/appearance'] = (req, res) => json(res, values)
routes['PATCH /v1/settings/appearance'] = (req, res, url, raw) => { const b = JSON.parse(raw); for (const [k, v] of Object.entries(b)) values[k] = { ...values[k], ...v }; json(res, values) }
const themes = (await shippedPlugins(path.join(TREE, 'app/public'))).filter((r) => r.kind.includes('theme'))
const dc = { id: 'doc-comments', name: 'Comments & suggestions', version: '1.0.0', api: 0, kind: ['extension'], places: ['files.viewer', 'files.root'], overrides: [], layer: 'shipped', effective: true, on: true, switch: 'plugin', broken: null, warnings: [], has: { page_main: true, theme: false } }
routes['GET /v1/plugins'] = (req, res) => json(res, { api: 0, plugins: [...themes, dc] })
async function walk(dir, rel = '') {
  for (const e of await fs.readdir(path.join(dir, rel), { withFileTypes: true })) {
    const r = rel ? `${rel}/${e.name}` : e.name
    if (e.isDirectory()) { if (!['node_modules', 'server', '__pycache__'].includes(e.name)) await walk(dir, r) } else {
      routes[`GET /v1/plugins/doc-comments/files/${r}`] = async (req, res) => {
        const ct = /\.m?js$/.test(r) ? 'text/javascript' : r.endsWith('.css') ? 'text/css' : r.endsWith('.svg') ? 'image/svg+xml' : r.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream'
        res.writeHead(200, { 'content-type': ct }); res.end(await fs.readFile(path.join(dir, r)))
      }
    }
  }
}
await walk(PLUGIN)
const broker = await fakeBroker(routes)
const app = await appAgainst(broker)
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] })

const WITHME = [
  { id: 'w-garden', address: 'shared:w-garden', url: 'https://kim.example.org/s/x', host: 'kim.example.org', title: 'The garden plan', owner: 'Kim', mode: 'comment', name: 'Sam', state: 'ok', problem: '', new: true, added: '2026-11-16T10:00:00Z', seen_at: '2026-11-16T12:00:00Z', checked: '2026-11-17T17:00:00Z', appear: 'default' },
  { id: 'w-old', address: 'shared:w-old', url: 'https://lee.example.org/s/y', host: 'lee.example.org', title: 'Trip budget', owner: 'Lee', mode: 'view', name: 'Sam', state: 'gone', problem: 'The link was turned off.', new: false, added: '2026-11-10T10:00:00Z', seen_at: null, checked: '2026-11-17T17:00:00Z', appear: 'default' },
]

async function page({ width, look, mode }) {
  const phone = width < 600
  values[phone ? 'phone' : 'desktop'] = { theme: look, mode, size: 100 }
  const ctx = await browser.newContext({ viewport: { width, height: phone ? 932 : 900 }, isMobile: phone, hasTouch: phone, deviceScaleFactor: 1, reducedMotion: 'reduce', locale: 'en-US', timezoneId: 'UTC', colorScheme: mode })
  await ctx.addInitScript(({ fixed }) => {
    const O = Date
    globalThis.Date = class extends O { constructor(...a) { super(...(a.length ? a : [fixed])) } static now() { return fixed } }
  }, { fixed: FIXED })
  await ctx.route('**/x/doc-comments/**', async (route) => {
    const req = route.request(), u = new URL(req.url())
    const wm = /^\/x\/doc-comments\/withme(\/.*)?$/.exec(u.pathname)
    if (wm) {
      const rest = wm[1] || ''
      if (req.method() === 'GET' && (rest === '' || rest === '/')) return route.fulfill({ json: { docs: WITHME, appear: 'names', owner: 'Sam', alias: "Sam's agent" } })
      if (req.method() === 'POST' && (rest === '' || rest === '/')) return route.fulfill({ status: 400, json: { error: 'That isn’t a share link. A share link ends in /s/ and a long code.', code: 'link' } })
      const id = rest.split('/')[1]
      if (/\/doc/.test(rest)) return route.fulfill({ status: 410, json: { error: 'The link was turned off.', code: 'gone' } })
      return route.fulfill({ json: { doc: WITHME.find((x) => x.id === id) } })
    }
    const r = await fetch(gbase + u.pathname + u.search, { method: req.method(), headers: { 'content-type': 'application/json' }, body: ['GET', 'HEAD'].includes(req.method()) ? undefined : req.postData() })
    route.fulfill({ status: r.status, headers: { 'content-type': r.headers.get('content-type') || 'application/json' }, body: Buffer.from(await r.arrayBuffer()) })
  })
  const p = await ctx.newPage()
  p.setDefaultTimeout(12000)
  p.errors = []
  p.on('pageerror', (e) => p.errors.push(e.message))
  p.done = () => ctx.close()
  p.phone = phone
  return p
}

const settle = async (p, ms = 250) => { await p.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) }); await p.waitForTimeout(ms) }
const DOC = (name = 'On staying.md') => `${app.url}/files/vault/Essays/drafts/${encodeURIComponent(name)}`
async function openDoc(p, name) {
  await p.goto(DOC(name))
  await p.waitForSelector('.dc .cm-content')
  await settle(p, 700)
}
const more = (p) => p.locator('.dc-head .dc-ddw > .dc-gbtn').last().click()
async function selectWords(p, words) {
  const s = await p.evaluate((words) => {
    for (const n of (function* () { const w = document.createTreeWalker(document.querySelector('.cm-content'), NodeFilter.SHOW_TEXT); let x; while ((x = w.nextNode())) yield x })()) {
      const i = n.textContent.indexOf(words)
      if (i < 0) continue
      const r = document.createRange(); r.setStart(n, i); r.setEnd(n, i + words.length)
      const b = r.getBoundingClientRect(); return { x1: b.left + 1, x2: b.right - 1, y: b.top + b.height / 2 }
    }
    return null
  }, words)
  await p.mouse.move(s.x1, s.y); await p.mouse.down(); await p.mouse.move(s.x2, s.y, { steps: 8 }); await p.mouse.up()
  await p.waitForTimeout(1000); await settle(p)
}

const SURFACES = {
  async editor(p) { await openDoc(p) },
  async 'mode-menu'(p) { await openDoc(p); await p.click('.dc-mpill'); await settle(p) },
  async 'more-menu'(p) { await openDoc(p); await more(p); await settle(p) },
  async 'selection-popup'(p) { await openDoc(p); await selectWords(p, 'The baker learned my order') },
  async 'card-focused'(p) {
    await openDoc(p)
    if (p.phone) await p.locator('.dc-mk').first().click(); else await p.locator('.dc-margin .dc-card').first().click()
    await settle(p, 600)
  },
  async suggestion(p) {
    await openDoc(p)
    if (p.phone) await p.locator('.dc-mk').nth(1).click(); else await p.locator('.dc-margin .dc-card').nth(1).click()
    await settle(p, 600)
  },
  async history(p) {
    await openDoc(p); await more(p); await settle(p, 150)
    await p.locator('.dc-menu .dc-mi', { hasText: 'History' }).first().click(); await settle(p, 500)
    const r = p.locator('.dc-hrow:not(.now)').first()
    if (await r.count()) { await r.click(); await settle(p, 400) }
  },
  async authors(p) {
    await openDoc(p); await more(p); await settle(p, 150)
    await p.locator('.dc-menu .dc-mi', { hasText: 'Show authors' }).first().click(); await settle(p, 200)
    await p.keyboard.press('Escape'); await settle(p, 400)
  },
  async 'share-sheet'(p) { await openDoc(p); await p.click('.dc-sharebtn'); await settle(p, 600) },
  async 'share-rowmenu'(p) { await openDoc(p); await p.click('.dc-sharebtn'); await settle(p, 400); await p.locator('.dc-sw-row .dc-ib').first().click(); await settle(p, 300) },
  async 'invite-card'(p) { await openDoc(p); await p.click('.dc-sharebtn'); await settle(p, 400); await p.locator('.dc-sh-go').click(); await settle(p, 500); await p.fill('.dc-iv-name', 'Morgan'); await p.locator('.dc-iv-name').blur(); await settle(p, 200) },
  async 'invite-modes'(p) { await openDoc(p); await p.click('.dc-sharebtn'); await settle(p, 400); await p.locator('.dc-sh-go').click(); await settle(p, 500); await p.click('.dc-iv-row .dc-modepill'); await settle(p, 300) },
  async 'shared-docs'(p) { await p.goto(`${app.url}/files/@doc-comments`); await p.waitForSelector('.dc-sd .setrow'); await settle(p, 800) },
  async 'withme-add'(p) { await p.goto(`${app.url}/files/@doc-comments`); await p.waitForSelector('.dc-wm-add'); await settle(p, 400); await p.click('.dc-wm-add'); await settle(p, 600) },
  async 'withme-error'(p) {
    await p.goto(`${app.url}/files/@doc-comments`); await p.waitForSelector('.dc-wm-add'); await settle(p, 400)
    await p.click('.dc-wm-add'); await settle(p, 400)
    await p.locator('.dc-wm-in').nth(0).fill('https://kim.example.org/garden'); await p.locator('.dc-wm-in').nth(1).fill('pass-word')
    await p.click('.dc-wm-go'); await settle(p, 700)
  },
  async 'withme-gone'(p) {
    await p.goto(`${app.url}/files/@doc-comments`); await p.waitForSelector('.dc-wm-entry'); await settle(p, 400)
    await p.locator('.dc-wm-entry .setrow', { hasText: 'Trip budget' }).first().click(); await settle(p, 1000)
  },
  async toc(p) {
    await openDoc(p, 'Long.md')
    await p.evaluate(() => { document.querySelector('.dc-scroll').scrollTop = 2600 }); await settle(p, 600)
    if (p.phone) await p.locator('.dc-secbtn').click(); else await p.locator('.dc-ticks').hover()
    await settle(p, 600)
  },
  async 'appear-sheet'(p) {
    await p.goto(`${app.url}/files/@doc-comments`); await p.waitForSelector('.dc-wm-entry'); await settle(p, 400)
    await p.locator('.dc-wm-entry', { hasText: 'The garden plan' }).locator('.iconbtn').last().click(); await settle(p, 400)
    await p.locator('[data-menu-surface] :is(button, .setrow)', { hasText: 'How your agents appear' }).first().click(); await settle(p, 700)
  },
  async 'share-gate'(p) { await p.goto(`${gbase}${shareLinks.comment.path}`); await settle(p, 700) },
  async 'share-page'(p) {
    await p.goto(`${gbase}${shareLinks.comment.path}`); await settle(p, 400)
    await p.locator('.dc-gate-in').fill(shareLinks.comment.password); await p.keyboard.press('Enter')
    await p.waitForSelector('.dc .cm-content'); await settle(p, 900)
  },
}

const names = ONLY.length ? ONLY : Object.keys(SURFACES)
const report = []
for (const name of names) {
  const share = name.startsWith('share-') && !['share-sheet', 'share-rowmenu'].includes(name)
  for (const look of share ? ['latte'] : LOOKS) for (const mode of MODES) for (const width of WIDTHS) {
    const dir = path.join(OUT, name); await fs.mkdir(dir, { recursive: true })
    const file = path.join(dir, `${look}-${mode}-${width}.jpg`)
    const p = await page({ width, look, mode })
    try {
      await SURFACES[name](p)
      await p.mouse.move(0, 0).catch(() => {})
      let audit = null
      if (AUDIT) {
        const fam = await p.evaluate(inspect)
        const rows = (await auditControls(p, CONTROLS)).filter(r => !(r.paletteBackground && r.paletteText && r.paletteBorders && r.paletteFont) || r.uaButton)
        audit = { families: fam, controls: rows }
      }
      if (!process.env.NO_SHOTS) await p.screenshot({ path: file, type: 'jpeg', quality: 82, animations: 'disabled' })
      report.push({ name, look, mode, width, ok: true, errors: p.errors, audit })
    } catch (e) {
      await p.screenshot({ path: file, type: 'jpeg', quality: 82 }).catch(() => {})
      report.push({ name, look, mode, width, ok: false, error: String(e.message).split('\n')[0], errors: p.errors })
      console.error(`FAIL ${name} ${look} ${mode} ${width}: ${String(e.message).split('\n')[0]} ${p.errors.join(' | ')}`)
    } finally { await p.done() }
  }
  console.log(`done ${name}`)
}
await fs.writeFile(path.join(OUT, `report-${Date.now()}.json`), JSON.stringify(report, null, 1))
await browser.close(); server.kill(); await closing(broker, app)()
process.exit(0)
