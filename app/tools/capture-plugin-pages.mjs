// Settings › Plugins pages in both states (2026-11-19): set up (on, keys
// saved, repositories connected) and not set up (off, no key, nothing
// connected), for Git backup, Images and Comments & suggestions, at 390 and
// 1440, light and dark, in the demo world (the fixture app; no person's
// data). Rows are the real Python reader's; Git backup's own page code runs,
// its server's answers stood in for at /x/git-backup/.
//
//   PAGES_OUT=<folder> LOAM_PLAYWRIGHT=<playwright/index.mjs> LOAM_CHROME=<chromium> node tools/capture-plugin-pages.mjs
//
// Fails on a page error. Each page is photographed in frames down its scroll.
import { execFileSync } from 'node:child_process'
import { mkdir, readFile, readdir } from 'node:fs/promises'
import path from 'node:path'
import { marketFixture } from '../test/market-fixture.mjs'

const out = process.env.PAGES_OUT
if (!out) throw new Error('Set PAGES_OUT')
await mkdir(out, { recursive: true })
const ROOT = new URL('../../', import.meta.url).pathname
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
const rows = JSON.parse(execFileSync('python3', ['-c', `import sys, json, pathlib; sys.path.insert(0, ${JSON.stringify(ROOT + 'lib')}); import loam_plugins as P
out = {}
for pid in ['git-backup', 'images', 'comments', 'sharing']:
    f = pathlib.Path(${JSON.stringify(ROOT + 'plugins')}) / pid; r = P.read_folder(f, pid); m = r['manifest']
    out[pid] = {**m, 'guide': r['guide'], 'has': r['has'], 'declaration': P.declaration(m, r['has']), 'folder': str(f)}
print(json.dumps(out))`]))
const tree = (id, name, set) => set
  ? { id, name, remote: `git@github.com:sam-rivera/${id === 'vault' ? 'notes' : 'agents'}.git`, web: `https://github.com/sam-rivera/${id === 'vault' ? 'notes' : 'agents'}`, host: 'github', visibility: 'private', level: 'ok', line: 'Backed up 4 minutes ago.', last_pushed_ago: '4 minutes ago', waiting_commits: 0, failing: null, test: { ok: true }, public_key: null, steps: [] }
  : { id, name, remote: '', host: null, level: 'off', line: 'Not connected.', waiting_commits: 0, failing: null, test: null, public_key: null, steps: [] }
const backupStatus = set => ({ ok: true, every: '15m', every_words: 'every 15 minutes', choices: { '15m': 'every 15 minutes', '1h': 'every hour', '6h': 'every 6 hours', '1d': 'once a day' },
  summary: set ? { level: 'ok', words: 'Everything is backed up.' } : { level: 'off', words: 'Nothing is backed up off this machine yet.' },
  trees: [tree('vault', 'Vault', set), tree('spaces', 'AI Spaces', set)], large_files: [] })
const LABEL = { 'git-backup': 'Git backup', images: 'Images', comments: 'Comments & suggestions', sharing: 'Sharing' }
const errors = []
for (const set of [true, false]) for (const width of [1440, 390]) for (const mode of ['light', 'dark']) {
  const app = await marketFixture({ mode, look: 'latte' })
  for (const [pid, r] of Object.entries(rows)) {
    const { guide, folder, ...m } = r
    const on = set || pid === 'git-backup' || pid === 'images'  // not set up: Git backup on with nothing connected, Images on with no key, Comments off
    const row = { ...m, layer: 'shipped', folder: `plugins/${pid}`, source: { kind: 'loam' }, effective: true, on, switch: 'plugin', broken: null, warnings: [], files: `/v1/plugins/${pid}/files/`,
      ...((pid === 'comments' || pid === 'sharing') && on ? { service: { state: 'running' } } : {}), ...(pid === 'git-backup' ? { service: { state: 'running' } } : {}) }
    app.installed.set(pid, row)
    app.routes[`GET /v1/plugins/${pid}`] = (req, res) => json(res, { plugin: { ...row, guide }, rows: [row] })
    const entries = row.contributes?.['settings.entry'] ?? []
    const values = pid === 'git-backup' && set ? { vault_remote: 'git@github.com:sam-rivera/notes.git', spaces_remote: 'git@github.com:sam-rivera/agents.git', every: '15m' } : {}
    app.routes[`GET /v1/plugins/${pid}/settings`] = (req, res) => json(res, { id: pid, entries, values })
    const files = ['assets/icon.svg', ...(row.screenshots ?? []).flatMap(s => [s.src, s.src_dark].filter(Boolean)), ...(await readdir(path.join(folder, 'page'), { recursive: true }).catch(() => [])).map(f => `page/${f}`)]
    for (const f of files) app.routes[`GET /v1/plugins/${pid}/files/${f}`] = async (req, res) => {
      try { const body = await readFile(path.join(folder, f)); res.writeHead(200, { 'content-type': { svg: 'image/svg+xml', webp: 'image/webp', js: 'text/javascript', mjs: 'text/javascript', css: 'text/css', json: 'application/json', woff2: 'font/woff2' }[f.split('.').pop()] ?? 'application/octet-stream' }); res.end(body) } catch { json(res, { error: 'no file' }, 404) }
    }
  }
  app.routes['GET /v1/keys'] = (req, res) => json(res, { providers: [{ id: 'openai', name: 'OpenAI', set }, { id: 'google', name: 'Google Gemini', set }] })
  const ctx = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, deviceScaleFactor: width === 390 ? 2 : 1, colorScheme: mode, isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce' })
  await ctx.route('**/app.js', r => r.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__cap={page,state,schedule};' }))
  await ctx.route('**/x/git-backup/**', r => r.fulfill({ contentType: 'application/json', body: JSON.stringify({ ok: true, status: backupStatus(set), ...backupStatus(set) }) }))
  const page = await ctx.newPage(); page.setDefaultTimeout(20000)
  page.on('pageerror', e => errors.push(`${set} ${width} ${mode}: ${e.message}`))
  await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
  const sc = "[...document.querySelectorAll('.settings *, .app-modal *, body *')].filter(n => n.scrollHeight > n.clientHeight + 4 && ['auto','scroll'].includes(getComputedStyle(n).overflowY)).sort((a, b) => b.scrollHeight - a.scrollHeight)[0]"
  for (const pid of Object.keys(rows)) {
    await page.evaluate(() => __cap.page.goSettings('plugins')); await page.waitForTimeout(500)
    if (await page.locator('.plugin-back').count()) { await page.locator('.plugin-back').click(); await page.waitForTimeout(300) }
    await page.locator(`.plugin-row[aria-label="About ${LABEL[pid]}"]`).click(); await page.waitForTimeout(2200)
    const total = await page.evaluate(sc + '?.scrollHeight ?? 0'), view = await page.evaluate(sc + '?.clientHeight ?? 1')
    for (let i = 0, y = 0; i < 6; i++, y += view - 60) {
      await page.evaluate('(() => { const n = ' + sc + '; if (n) n.scrollTop = ' + y + ' })()'); await page.waitForTimeout(300)
      await page.screenshot({ path: path.join(out, `page-${pid}-${set ? 'set-up' : 'not-set-up'}-${width}-${mode}-${i + 1}.png`) })
      if (y + view >= total) break
    }
  }
  await ctx.close(); await app.close()
  console.log(`${set ? 'set up' : 'not set up'} ${width} ${mode}`)
}
await browser.close()
if (errors.length) { console.error(errors.join('\n')); process.exit(1) }
