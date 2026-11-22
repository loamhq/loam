// Real page and HTTP relay; all records, writes and stream failures are synthetic.
import assert from 'node:assert/strict'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { mkdir, writeFile, readFile, readdir } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { oneProductFixture } from './one-product-fixture.mjs'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.THREAD_PUNCH_EVIDENCE
if (!out) throw new Error('Set THREAD_PUNCH_EVIDENCE to a retained evidence directory')
const baseline = 'af6be9f', phase = process.env.THREAD_PUNCH_PHASE || 'current'
assert.ok(['main', 'current'].includes(phase))
await mkdir(out, { recursive: true })
const results = []
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-gpu'] })
try {
  for (const width of [390, 1440]) for (const theme of phase === 'main' ? ['light'] : ['light', 'dark']) {
    const app = await oneProductFixture(), errors = [], listStreams = new Set()
    const json = (res, value) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(value)) }
    app.f.agents.push(...['brook', 'fern', 'arbor'].map((id) => ({ id, name: id[0].toUpperCase() + id.slice(1), emoji: null })))
    await app.f.api('PATCH', '/threads/general', { add: ['brook', 'fern'] })
    const empty = await app.f.api('POST', '/threads', {})
    app.routes[`GET /v1/threads/${empty.id}`] = async (req, res) => json(res, await app.f.api('GET', `/threads/${empty.id}`))
    app.routes[`PATCH /v1/threads/${empty.id}`] = async (req, res, url, raw) => json(res, await app.f.api('PATCH', `/threads/${empty.id}`, JSON.parse(raw)))
    app.routes['GET /v1/threads/events'] = (req, res) => {
      res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n')
      listStreams.add(res)
      const stop = app.f.subscribeList((e) => res.write(`data: ${JSON.stringify(e)}\n\n`))
      res.on('close', () => { stop(); listStreams.delete(res) })
    }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390 })
    if (phase === 'main') for (const file of ['threads.js', 'threads.css']) await context.route(`**/${file}`, (route) => route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: execFileSync('git', ['show', `${baseline}:public/${file}`], { encoding: 'utf8' }) }))
    const page = await context.newPage(); page.setDefaultTimeout(12000)
    page.on('pageerror', (e) => errors.push(e.message))
    const captures = []
    const shot = async (state) => {
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${state}: no horizontal overflow`)
      const file = `${phase}-${state}-${width}-${theme}.png`; captures.push(file)
      await page.screenshot({ path: path.join(out, file), animations: 'disabled' })
    }
    try {
      await page.goto(app.url)
      await page.locator('.tt-row').first().waitFor()
      assert.equal(await page.getByRole('button', { name: 'Refresh threads', exact: true }).count(), 0)
      await shot('chats')
      // Prove automatic recovery from an ended stream with a fresh list snapshot.
      const connections = () => app.broker.seen.filter((r) => r.path === '/v1/threads/events').length
      await assertEventually(() => listStreams.size > 0)
      const beforeDrop = connections()
      for (const stream of listStreams) stream.end()
      await app.f.api('PATCH', '/threads/weekend', { title: 'Recovered without refresh' })
      await page.getByTitle('Recovered without refresh', { exact: true }).waitFor()
      assert.ok(connections() > beforeDrop, 'list reconnects without a refresh control')
      // Also exercise a socket that stays open but stops sending bytes. The page
      // heartbeat wakes its stale link; no test calls the link directly.
      await page.clock.install()
      const beforeSilence = connections()
      await page.clock.runFor(28000)
      await assertEventually(() => connections() > beforeSilence)
      await app.f.api('PATCH', '/threads/weekend', { title: 'Recovered after silence' })
      await page.getByTitle('Recovered after silence', { exact: true }).waitFor()
      await page.clock.resume()
      await page.getByTitle('General', { exact: true }).click()
      await page.locator('.tt-stream .tt-message').first().waitFor()
      assert.equal(await page.locator('.tt-member-strip, .tt-head .tt-engine-select, .tt-head .tt-engine-chip').count(), 0)
      assert.equal(await page.locator('.tt-head').getByText('Claude Code', { exact: true }).count(), 0)
      assert.equal(await page.locator('.tt-head').evaluate((n) => n.scrollWidth > n.clientWidth), false)
      await shot('general-header')
      await page.getByTitle('Thread details', { exact: true }).click()
      await page.locator('.tt-member').first().waitFor()
      assert.equal(await page.locator('.tt-member').count(), 7)
      const bring = page.getByRole('heading', { name: 'Bring someone in', exact: true })
      await bring.scrollIntoViewIfNeeded()
      await page.getByRole('button', { name: 'Add Arbor', exact: true }).scrollIntoViewIfNeeded()
      const geometry = await bring.evaluate((n) => {
        const r = n.getBoundingClientRect(), p = n.closest('.tt-details-scroll').getBoundingClientRect()
        return { top: r.top, bottom: r.bottom, left: r.left, right: r.right, paneTop: p.top, paneBottom: p.bottom,
          covered: !n.contains(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)) }
      })
      assert.ok(geometry.top >= geometry.paneTop && geometry.bottom <= geometry.paneBottom && geometry.left >= 0 && geometry.right <= width)
      assert.equal(geometry.covered, false, 'Bring someone in is painted above the pane, not clipped')
      assert.equal(await page.locator('.tt-stream').isVisible(), false, 'chat is replaced by details')
      await shot('members')
      await page.getByRole('button', { name: 'Add Arbor', exact: true }).click()
      await page.getByRole('button', { name: 'Remove Arbor', exact: true }).waitFor()
      await page.getByRole('button', { name: 'Remove Arbor', exact: true }).click()
      await page.getByRole('button', { name: 'Add Arbor', exact: true }).waitFor()
      await page.getByRole('button', { name: 'Chat', exact: true }).click()
      assert.deepEqual(errors, [])
      results.push({ width, theme, phase, members: 7, geometry, noEngineStrip: true, endedStreamRecovered: true, silentStreamRecovered: true,
        captures, errors })
      console.log('PASS', phase, width, theme, 'thread membership and stream recovery')
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, `${phase}-browser.json`), JSON.stringify({ baseline, browser: browser.version(), fixturesOnly: true, results }, null, 2) + '\n')
  const captures = []
  for (const file of (await readdir(out)).filter((f) => f.endsWith('.png')).sort()) captures.push({ file, sha256: createHash('sha256').update(await readFile(path.join(out, file))).digest('hex') })
  await writeFile(path.join(out, 'captures.json'), JSON.stringify(captures, null, 2) + '\n')
} finally { await browser.close() }

async function assertEventually(test) {
  const deadline = Date.now() + 12000
  while (!test()) {
    assert.ok(Date.now() < deadline, 'expected browser network activity')
    await new Promise((r) => setTimeout(r, 20))
  }
}
