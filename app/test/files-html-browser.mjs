// Real Chromium, fixture broker, two loopback authorities. No host changes.
// LOAM_PLAYWRIGHT names an installed Playwright module; LOAM_CHROME is optional.
import assert from 'node:assert/strict'
import http from 'node:http'
import path from 'node:path'
import { once } from 'node:events'
import { mkdir, writeFile } from 'node:fs/promises'
import { createApp } from '../src/server.js'
import { readConfig } from '../src/config.js'
import { fakeBroker, TOKEN, shut } from './harness.js'
import { filesHost } from './files-host.js'
import { row, listAnswer, readAnswer } from './fixtures/files-contract.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const output = process.env.FILES_HTML_EVIDENCE || path.join(process.env.TMPDIR || '.', 'files-html-shortcuts-links')
await mkdir(output, { recursive: true })
const dashboard = `<!doctype html><html><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="page.css"><body><main><div class="eyebrow">STUDIO / WEEKLY PLAN</div>
<h1>A little more<br>room to think.</h1><p class="intro">A living plan for a calmer, more focused week.</p>
<div class="live" id="live">Waiting for JavaScript</div><section class="cards"><article><span>THIS WEEK</span><strong>24</strong><p>ideas brought to life</p></article><article><span>FOCUS TIME</span><strong>18h</strong><p>space for the good work</p></article><article><span>LOOKING AHEAD</span><strong>3</strong><p>things worth making next</p></article></section>
<section class="chart"><div class="chart-head"><h2>Finding a rhythm</h2><button id="change">Try another view</button></div><div class="canvas"><canvas id="chart"></canvas></div></section>
<footer><span id="sibling">Loading assets…</span><a href="next.html" target="_blank" rel="noopener">Open the next chapter ↗</a></footer>
</main><script>document.querySelector('#live').textContent='Live · updated just now'; document.body.dataset.inline='running'</script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.9/dist/chart.umd.min.js"></script><script src="page.js"></script></body></html>`
const css = `:root{color-scheme:light dark;--bg:#f6f3ed;--ink:#333a32;--card:#fff;--quiet:#6b7367;--line:#e3e5db}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,sans-serif}main{max-width:1400px;padding:clamp(24px,5vw,72px);margin:auto}.eyebrow{font-size:10px;letter-spacing:.18em;color:var(--quiet)}h1{font-family:Georgia,serif;font-size:clamp(36px,5vw,68px);font-weight:400;line-height:1.05;margin:22px 0 16px;letter-spacing:-.035em}.intro{color:var(--quiet);margin:0 0 16px}.live{color:#55794d;font-size:12px}.live:before{content:'●';margin-right:7px}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:32px 0 24px}article{background:var(--card);padding:22px;border-radius:18px}article span{font-size:9px;letter-spacing:.1em;color:var(--quiet)}strong{display:block;font-family:Georgia,serif;font-weight:400;font-size:36px;margin-top:8px}article p{margin:0;font-size:12px;color:var(--quiet)}.chart{background:var(--card);border-radius:18px;padding:24px}.chart-head{display:flex;justify-content:space-between;align-items:center;gap:10px}h2{font:22px Georgia,serif;margin:0}button{border:1px solid var(--line);border-radius:24px;padding:10px 16px;background:transparent;color:inherit;cursor:pointer}.canvas{height:180px;margin-top:22px}footer{display:flex;justify-content:space-between;gap:18px;font-size:11px;color:var(--quiet);margin-top:24px}a{color:inherit}@media(max-width:500px){.cards{gap:8px;margin-top:24px}article{padding:14px 10px}article span{font-size:8px}strong{font-size:28px}article p{font-size:10px}.chart{padding:16px}.chart-head{align-items:start;flex-direction:column}h2{font-size:20px}button{font-size:11px;padding:7px 12px}.canvas{height:160px}footer{flex-direction:column}}@media(prefers-color-scheme:dark){:root{--bg:#222720;--ink:#e8ece0;--card:#2b3228;--quiet:#a4b29a;--line:#46503f}.live{color:#abc695}}`
const js = `document.body.dataset.sibling='running';document.querySelector('#sibling').textContent='Made with a little JavaScript';
const chart=new Chart(document.querySelector('#chart'),{type:'bar',data:{labels:['Mon','Tue','Wed','Thu','Fri'],datasets:[{data:[4,7,5,8,6],backgroundColor:'#90a67d',borderRadius:5}]},options:{responsive:true,maintainAspectRatio:false,animation:false,plugins:{legend:{display:false}},scales:{x:{grid:{display:false}},y:{grid:{color:'#90a67d22'},ticks:{stepSize:2}}}}});document.body.dataset.chart=Chart.version;
document.querySelector('#change').onclick=()=>{chart.data.datasets[0].data=[7,5,8,4,9];chart.update();document.querySelector('#change').textContent='A fresh perspective';document.body.dataset.clicked='yes'};
fetch('data.json').then(r=>r.json()).then(x=>document.body.dataset.data=x.ok);`
const files = { 'Notes/weekly-plan.html': dashboard, 'Notes/page.js': js, 'Notes/page.css': css, 'Notes/data.json': '{"ok":"loaded"}', 'Notes/next.html': '<h1>The next chapter</h1>', 'Notes/private.md': '# Private note' }
const { routes } = filesHost()
const json = (res, status, body) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
routes['GET /v1/files/stat'] = (req, res, url) => {
  const p = url.searchParams.get('path')
  if (p === 'Notes/moved.html') return json(res, 404, { error: 'nothing here' })
  json(res, 200, row(url.searchParams.get('root'), p))
}
routes['GET /v1/files/read'] = (req, res, url) => {
  const p = url.searchParams.get('path')
  json(res, 200, readAnswer(row(url.searchParams.get('root'), p), files[p] ?? '', 'sha256:fixture'))
}
routes['GET /v1/files/list'] = (req, res, url) => {
  const root = url.searchParams.get('root'), p = url.searchParams.get('path') || ''
  json(res, 200, listAnswer(row(root, p, { kind: 'folder' }), p ? [row(root, 'Notes/weekly-plan.html'), row(root, 'Notes/private.md')] : [row(root, 'Notes')]))
}
routes['GET /v1/files/download'] = (req, res, url) => {
  const p = url.searchParams.get('path')
  if (!(p in files)) return json(res, 404, { error: 'missing file' })
  res.writeHead(200, { 'content-type': 'application/octet-stream' })
  res.end(files[p])
}
const broker = await fakeBroker(routes)
let appUrl
const contentRequests = []
const proxy = http.createServer((req, res) => {
  contentRequests.push({ path: req.url.replace(/^(\/content\/)[^/]+/, '$1…'), cookie: req.headers.cookie ?? null })
  const upstream = http.request(`${appUrl}${req.url}`, { method: req.method, headers: req.headers }, (out) => { res.writeHead(out.statusCode, out.headers); out.pipe(res) })
  upstream.on('error', () => res.destroy())
  req.pipe(upstream)
})
proxy.listen(0, '127.0.0.1')
await once(proxy, 'listening')
const contentOrigin = `http://localhost:${proxy.address().port}`
const { server } = createApp({
  config: readConfig({ BROKER_URL: broker.url, BROKER_TOKEN: TOKEN, LOG_LEVEL: 'off', FILES_CONTENT_ORIGIN: contentOrigin, FILES_VAULT_URLS: 'https://notes.example.org' }),
})
server.listen(0, '127.0.0.1')
await once(server, 'listening')
appUrl = `http://127.0.0.1:${server.address().port}`
const browser = await chromium.launch({ ...(process.env.LOAM_CHROME ? { executablePath: process.env.LOAM_CHROME } : {}), headless: true, args: ['--no-sandbox', '--disable-gpu'] })
const results = []
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: { width, height: 900 }, colorScheme: theme, ignoreHTTPSErrors: true, permissions: ['clipboard-read', 'clipboard-write'] })
    await context.addCookies([{ name: 'app-only', value: 'fixture-session', url: appUrl, httpOnly: true, sameSite: 'Lax' }])
    const page = await context.newPage()
    const errors = []
    page.on('pageerror', (e) => errors.push(e.message))
    await page.addInitScript(({ theme, app }) => { if (location.origin === app) { localStorage.setItem('loam-ui.theme', theme); localStorage.setItem('isolation-secret', 'app-storage-fixture') } }, { theme, app: appUrl })
    const capture = async (name) => page.screenshot({ path: path.join(output, `${name}-${width}-${theme}.png`) })
    await page.goto(`${appUrl}/files/vault/Notes/weekly-plan.html`)
    const frame = page.frameLocator('.fs-iframe')
    await frame.locator('body[data-inline=running][data-sibling=running][data-chart="4.4.9"][data-data=loaded]').waitFor({ timeout: 30000 })
    await frame.locator('#change').click()
    await frame.locator('body[data-clicked=yes]').waitFor()
    const measurements = await page.locator('.fs-iframe').evaluate((el) => { const r = el.getBoundingClientRect(), f = el.closest('.fs-file').getBoundingClientRect(); return { paneWidth: f.width, width: r.width, height: r.height, right: r.right, bottom: r.bottom, paneBottom: f.bottom, overflow: document.documentElement.scrollWidth > innerWidth } })
    assert.equal(measurements.width, measurements.paneWidth)
    assert.ok(measurements.height > 650)
    assert.equal(measurements.bottom, measurements.paneBottom)
    assert.equal(measurements.overflow, false)
    await capture('html-live')
    let liveFrame = page.frames().find((f) => f.url().startsWith(contentOrigin))
    let modalOpened = false
    page.once('dialog', async (dialog) => { modalOpened = true; await dialog.dismiss() })
    await liveFrame.evaluate(() => alert('Fixture modal'))
    assert.equal(modalOpened, true, 'the allowed modal opens')
    const isolation = await liveFrame.evaluate(async (app) => {
      const result = {}
      try { result.parentStorage = parent.localStorage.getItem('isolation-secret') } catch (e) { result.parentStorage = e.name }
      try { result.parentDOM = parent.document.title } catch (e) { result.parentDOM = e.name }
      try { await parent.fetch('/api/sessions'); result.parentRelay = 'reachable' } catch (e) { result.parentRelay = e.name }
      result.ownStorage = localStorage.getItem('isolation-secret')
      try { await fetch(`${app}/api/sessions`, { method: 'POST', credentials: 'include', body: '{}' }); result.relay = 'reachable' } catch (e) { result.relay = e.name }
      try { await fetch('https://example.org/collect', { method: 'POST', body: 'fixture' }); result.egress = 'reachable' } catch (e) { result.egress = e.name }
      result.localAPI = (await fetch('/api/sessions', { method: 'POST', body: '{}' })).status
      result.privateVaultRead = await fetch('private.md').then((r) => r.text())
      return result
    }, appUrl)
    assert.equal(isolation.parentStorage, 'SecurityError')
    assert.equal(isolation.parentDOM, 'SecurityError')
    assert.equal(isolation.parentRelay, 'SecurityError')
    assert.equal(isolation.ownStorage, null)
    assert.equal(isolation.relay, 'TypeError')
    assert.equal(isolation.egress, 'TypeError')
    assert.equal(isolation.localAPI, 405)
    assert.equal(isolation.privateVaultRead, '# Private note', 'residual root-scoped read permission is explicit')
    // A target=_blank link works and cannot acquire an opener to the app.
    const opened = context.waitForEvent('page')
    await frame.locator('a[target=_blank]').click()
    const popup = await opened
    await popup.getByRole('heading', { name: 'The next chapter' }).waitFor()
    assert.equal(await popup.evaluate(() => opener === null), true)
    await popup.close()
    // A form is allowed to submit too; the content service itself remains
    // read-only, just as a normal static file host would be.
    await liveFrame.evaluate(() => { const form = document.createElement('form'); form.action = 'next.html'; form.target = '_blank'; form.innerHTML = '<button id="form-submit">Continue by form</button>'; document.body.append(form) })
    const formOpened = context.waitForEvent('page')
    await frame.locator('#form-submit').click()
    const formTab = await formOpened
    await formTab.getByRole('heading', { name: 'The next chapter' }).waitFor()
    await formTab.close()
    // Secondary HTML actions (Open in new tab, Reload) live inside the ⋯ menu.
    assert.equal(await page.locator('.fs-htmlreload, .fs-htmlopen').count(), 0)
    await page.locator('.fs-filemenu').click()
    const tabOpened = context.waitForEvent('page')
    await page.locator('.fs-menu-open').click()
    const tab = await tabOpened
    await tab.locator('body[data-chart="4.4.9"]').waitFor()
    assert.equal(await tab.evaluate(() => opener === null), true)
    await tab.close()
    await page.locator('.fs-filemenu').click()
    await page.locator('.fs-menu-reload').click()
    await frame.locator('#change').filter({ hasText: 'Try another view' }).waitFor()
    await frame.locator('body[data-data=loaded]').waitFor()
    liveFrame = page.frames().find((f) => f.url().startsWith(contentOrigin))
    // Test server-side CSRF defense separately from connect-src, through an
    // allowed popup navigation to the app's relay. No broker call may happen.
    const before = broker.seen.length
    await liveFrame.evaluate((app) => { const a = document.createElement('a'); a.href = `${app}/api/agents`; a.target = '_blank'; a.textContent = 'Try the relay'; a.id = 'attack'; document.body.append(a) }, appUrl)
    const attackOpened = context.waitForEvent('page')
    await frame.locator('#attack').click()
    const attack = await attackOpened
    await attack.waitForLoadState()
    assert.match(await attack.locator('body').innerText(), /same-origin browser requests only/)
    assert.equal(broker.seen.length, before)
    await attack.close()
    // Add a file from its open view, then a folder. Both share the same home.
    await page.locator('.fs-filemenu').click()
    await page.locator('.fs-shortcuttoggle').click()
    await page.goto(`${appUrl}/files/vault/Notes`)
    await page.getByRole('button', { name: 'Actions for private.md', exact: true }).click()
    await page.locator('.fs-shortcuttoggle').click()
    await page.goto(`${appUrl}/files`)
    await page.locator('.fs-shortcut').filter({ hasText: 'private.md' }).click()
    await page.locator('.fs-preview').waitFor()
    await page.locator('.fs-filemenu').click()
    await page.locator('.fs-shortcuttoggle').click()
    await page.goto(`${appUrl}/files/vault/Notes`)
    await page.locator('.fs-menu').click()
    await page.locator('.fs-shortcuttoggle').click()
    await page.goto(`${appUrl}/files`)
    await page.locator('.fs-shortcut').filter({ hasText: 'weekly-plan.html' }).waitFor()
    assert.equal(await page.locator('.fs-shortcut .icon-tile span').first().innerText(), 'HTML')
    await capture('shortcuts')
    await page.locator('.fs-shortcut').filter({ hasText: 'weekly-plan.html' }).click()
    await frame.locator('body[data-chart="4.4.9"]').waitFor()
    // Menu and confirmation, with the clipboard's actual value checked.
    await page.locator('.fs-filemenu').click()
    await page.locator('.fs-menu-copy').waitFor()
    await capture('file-menu')
    await page.locator('.fs-menu-copy').click()
    await page.locator('.fs-sheet .listnotice.inline').filter({ hasText: 'Link copied · Vault web link' }).waitFor()
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), 'https://notes.example.org/Notes/weekly-plan.html')
    await capture('link-copied')
    await page.locator('.fs-menu-source').click()
    await page.locator('.fs-field').waitFor()
    assert.match(await page.locator('.fs-field').inputValue(), /<script src="page.js">/)
    await page.getByRole('button', { name: 'Preview', exact: true }).click()
    await frame.locator('body[data-chart="4.4.9"]').waitFor()
    // Workspace uses the identical preview origin; its copied URL stays an
    // authenticated app deep link, rather than an expiring content grant.
    await page.goto(`${appUrl}/files/spaces/Notes/weekly-plan.html`)
    await frame.locator('body[data-chart="4.4.9"]').waitFor()
    await page.locator('.fs-filemenu').click()
    await page.locator('.fs-menu-copy').click()
    await page.locator('.listnotice.inline').filter({ hasText: 'Link copied · Loam link' }).waitFor()
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), `${appUrl}/files/spaces/Notes/weekly-plan.html`)
    // A moved file stays on the home, quietly explained and removable.
    await page.evaluate(() => { const held = JSON.parse(localStorage.getItem('loam-ui.files')); held.shortcuts.push({ root: 'vault', path: 'Notes/moved.html', kind: 'file' }); localStorage.setItem('loam-ui.files', JSON.stringify(held)) })
    await page.goto(`${appUrl}/files`)
    await page.locator('.fs-shortcut').filter({ hasText: 'moved.html' }).click()
    await page.locator('.listnotice.inline').filter({ hasText: 'has moved or been deleted' }).waitFor()
    await page.getByRole('button', { name: 'Actions for moved.html', exact: true }).click()
    await page.locator('.fs-shortcuttoggle').click()
    await page.locator('.fs-shortcut').filter({ hasText: 'moved.html' }).waitFor({ state: 'detached' })
    assert.equal(await page.locator('.fs-shortcut').filter({ hasText: 'moved.html' }).count(), 0)
    assert.deepEqual(errors, [])
    results.push({ width, theme, measurements, isolation, nativeDialog: modalOpened, form: 'GET submitted in a new tab', reload: 'reset the interactive page', source: 'source view returned to live preview', popup: 'target-blank and Open in new tab opened without an app opener', shortcuts: 'added from file view and row menu; file and folder opened; moved target removed', clipboard: 'vault URL and workspace app link', pageErrors: errors })
    await context.close()
  }
  assert.equal(contentRequests.some((r) => r.cookie?.includes('app-only')), false)
  assert.equal(broker.seen.some((r) => r.method !== 'GET'), false)
  await writeFile(path.join(output, 'browser.json'), JSON.stringify({ browser: browser.version(), fixtures: true, cdn: 'https://cdn.jsdelivr.net/npm/chart.js@4.4.9/dist/chart.umd.min.js', contentCookieLeak: false, agentMutations: 0, results }, null, 2) + '\n')
  console.log(JSON.stringify({ contexts: results.length, captures: results.length * 4, contentCookieLeak: false, agentMutations: 0 }))
} finally {
  await browser.close()
  await shut(proxy)
  await shut(server)
  await shut(broker.server)
}
