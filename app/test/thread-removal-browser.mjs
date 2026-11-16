// Actual page + HTTP relay + synthetic broker SSE, including transient 404s.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { openingFixture, openingId as id } from './thread-opening-fixture.mjs'

const out = process.env.THREAD_REMOVAL_EVIDENCE
if (!out) throw new Error('Set THREAD_REMOVAL_EVIDENCE')
await mkdir(out, { recursive: true })
const baseline = process.argv.includes('--baseline')
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox'] })
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8'), results = []
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    const app = await openingFixture(), clients = new Set(), errors = [], reads = []
    let listMode = 'present', readFailures = 0, deleted = false
    const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
    const emit = (event) => { for (const res of clients) res.write(`data: ${JSON.stringify(event)}\n\n`) }
    app.routes['GET /v1/threads/events'] = (req, res) => {
      res.writeHead(200, { 'content-type': 'text/event-stream' })
      res.write(`data: ${JSON.stringify({ type: 'threads.snapshot', threads: [app.row], agents: app.f.agents })}\n\n`)
      clients.add(res); res.on('close', () => clients.delete(res))
    }
    app.routes['GET /v1/threads'] = (req, res, url) => {
      reads.push({ query: url.search, mode: listMode })
      if (listMode === 'failed') return json(res, { error: 'Restarting' }, 503)
      json(res, { threads: listMode === 'absent' ? [] : [app.row], agents: app.f.agents })
    }
    const originalRead = app.routes[`GET /v1/threads/${id}`]
    app.routes[`GET /v1/threads/${id}`] = (req, res, url) => deleted || readFailures-- > 0 ? json(res, { error: 'Thread not found' }, 404) : originalRead(req, res, url)
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce' })
    if (baseline) await context.route('**/threads.js', (route) => route.fulfill({ contentType: 'text/javascript', body: execFileSync('git', ['show', 'a8c4dde:public/threads.js'], { encoding: 'utf8' }) }))
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source.replace('\nawait pluginsReady', '\nglobalThis.__removal = { threads, schedule };\nawait pluginsReady') }))
    const page = await context.newPage(); page.setDefaultTimeout(10000); page.on('pageerror', (e) => errors.push(e.message))
    const capture = async (state) => { await page.screenshot({ path: path.join(out, `${state}-${width}-${theme}.png`), animations: 'disabled' }) }
    const healthy = async () => {
      assert.equal(await page.getByRole('button', { name: 'Refresh thread', exact: true }).count(), 0)
      assert.equal(await page.locator('.banner').filter({ hasText: 'This thread was deleted.' }).count(), 0)
      assert.equal(await page.evaluate(() => Boolean(__removal.threads.st.open.readFailed)), false)
      assert.equal(await page.locator('#draft').innerText(), 'An unsent thought')
      assert.equal(await page.evaluate(() => document.querySelector('.tt-stream') === globalThis.__heldThread), true)
    }
    try {
      await page.goto(app.url)
      await page.locator('.tt-row').first().waitFor()
      await page.waitForFunction(() => __removal.threads.st.listLive)
      await page.evaluate((id) => __removal.threads.open(id), id)
      await page.waitForFunction(() => !__removal.threads.st.open.refreshing)
      await page.locator('#draft').fill('An unsent thought')
      await page.evaluate(() => { globalThis.__heldThread = document.querySelector('.tt-stream') })
      emit({ type: 'thread.removed', thread: id })
      if (baseline) {
        await page.locator('.banner').filter({ hasText: 'This thread was deleted.' }).waitFor()
        await capture('before-false-deletion')
        results.push({ width, theme, falseDeletionReproduced: true, errors }); continue
      }
      await page.waitForTimeout(150)
      await healthy(); await capture('after-false-removal')
      const checkCount = reads.length
      listMode = 'absent'; emit({ type: 'thread.removed', thread: id })
      await page.waitForTimeout(200); await healthy()
      listMode = 'present'
      await page.waitForFunction(() => __removal.threads.st.open.thread.title === 'Room for the next thought')
      await page.waitForTimeout(2700); await healthy()
      assert.ok(reads.length >= checkCount + 2)
      listMode = 'failed'; emit({ type: 'thread.removed', thread: id })
      await page.waitForTimeout(2800); await healthy()
      listMode = 'present'
      await page.waitForTimeout(2700); await healthy()
      readFailures = 1
      await page.evaluate((id) => __removal.threads.open(id), id)
      await page.getByText('Reconnecting…', { exact: true }).waitFor()
      assert.equal(await page.getByRole('button', { name: 'Refresh thread' }).count(), 0)
      await capture('reconnecting')
      await page.waitForFunction(() => !__removal.threads.st.open.refreshing && !__removal.threads.st.open.connection && __removal.threads.st.open.eventVersion > 0)
      assert.equal(await page.locator('#draft').innerText(), 'An unsent thought')
      const newestReads = app.reads.filter((q) => q.startsWith('?limit=')).length
      deleted = true; listMode = 'absent'; emit({ type: 'thread.removed', thread: id })
      await page.locator('.banner').filter({ hasText: 'This thread was deleted.' }).waitFor()
      assert.equal(await page.evaluate(() => __removal.threads.st.open.readFailed), true)
      assert.equal(app.reads.filter((q) => q.startsWith('?limit=')).length, newestReads, 'confirmation never marks unread replies seen')
      assert.ok(reads.every((q) => q.query === '?archived=1'))
      await capture('confirmed-deletion')
      assert.deepEqual(errors, [])
      results.push({ width, theme, falseRemovalRecovered: true, emptyReadRecovered: true, failuresRetried: true, opening404Recovered: true, realDeletionConfirmed: true, errors })
    } catch (error) {
      console.error(JSON.stringify({ width, theme, reads, listMode, clients: clients.size, state: await page.evaluate(() => {
        const { error, connection, eventVersion, readFailed } = __removal.threads.st.open
        return { error, connection, eventVersion, readFailed, listLive: __removal.threads.st.listLive }
      }) }))
      throw error
    } finally { await context.close(); await app.close() }
  }
} finally { await browser.close() }
await writeFile(path.join(out, 'results.json'), JSON.stringify(results, null, 2) + '\n')
console.log(JSON.stringify(results, null, 2))
