import { observeAudio } from './audio-probe.mjs'
// Painted comparisons of the actual app; only records and speech are synthetic.
import assert from 'node:assert/strict'
import { readFile, readdir, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { createHash } from 'node:crypto'
import { oneProductFixture } from './one-product-fixture.mjs'
const out = process.env.THREAD_EXPERIENCE_EVIDENCE
if (!out) throw new Error('Set THREAD_EXPERIENCE_EVIDENCE')
await mkdir(out, { recursive: true })
async function writeManifest() {
  const captures = []
  for (const file of (await readdir(out)).filter((name) => name.endsWith('.png')).sort()) {
    const data = await readFile(path.join(out, file))
    captures.push({ file, width: data.readUInt32BE(16), height: data.readUInt32BE(20), sha256: createHash('sha256').update(data).digest('hex') })
  }
  assert.equal(captures.length, 20, 'all paired captures exist')
  await writeFile(path.join(out, 'captures.json'), JSON.stringify(captures, null, 2) + '\n')
}
if (process.argv.includes('--manifest-only')) { await writeManifest(); process.exit(0) }
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const sharp = (await import(process.env.LOAM_SHARP || 'sharp')).default
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox'] })
const results = []
const response = 'There is room to make this week calmer. Keep one clear place for the next step, leave a little space between commitments, and gather the loose ends before adding something new. '.repeat(7).trim()
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    const fixture = await oneProductFixture()
    fixture.routes['GET /v1/threads/a-quieter-week'] = async (req, res) => {
      const record = await fixture.f.api('GET', '/threads/a-quieter-week')
      record.messages = [{ id: 'reply', author: 'sprout', kind: 'message', text: response }]
      res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(record))
    }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390 })
    await observeAudio(context)
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__experience = { threads, state, schedule, goChats, get takes() { return globalThis.__audio?.['read-aloud']?.takes ?? page.takes } };' }))
    const page = await context.newPage(), errors = [], captures = [], posts = [], lookups = [], known = new Map()
    let pending = false, serial = 0
    const key = (b) => JSON.stringify([b.chat_id, b.mode, b.text]), ids = new Map()
    const ready = (id) => ({ id, status: 'ready', duration_s: 90, url: `/x/read-aloud/speech/${id}/audio.mp3` })
    await page.route('**/api/health', (r) => r.fulfill({ json: { configured: true, ok: true, speech: true } }))
    await page.route('**/x/read-aloud/status', r => r.fulfill({ json: { speech: true } }))
    await page.route(/\/x\/read-aloud\/speech(?:\/.*)?$/, async (route) => {
      const req = route.request(), url = new URL(req.url())
      if (url.pathname.endsWith('/lookup')) { const body = req.postDataJSON(); lookups.push(body); return route.fulfill({ json: known.get(key(body)) ?? { status: 'none' } }) }
      if (url.pathname === '/x/read-aloud/speech') {
        const body = req.postDataJSON(); posts.push(body); const id = (++serial).toString(16).padStart(32, '0'); ids.set(id, body)
        return route.fulfill({ status: 202, json: { id, status: 'preparing', stage: 'voicing' } })
      }
      const id = url.pathname.split('/')[3]
      if (pending) return route.fulfill({ status: 202, json: { id, status: 'preparing', stage: 'voicing' } })
      const answer = ready(id); if (ids.has(id)) known.set(key(ids.get(id)), answer)
      return route.fulfill({ json: answer })
    })
    await page.addInitScript(() => {
      const realNow = Date.now; globalThis.__elapsed = 0; Date.now = () => realNow() + __elapsed
      globalThis.__audio = []
      globalThis.Audio = class {
        constructor() { Object.assign(this, { paused: true, ended: false, duration: 90, currentTime: 0, buffered: { length: 0 }, listeners: new Map(), plays: 0 }); __audio.push(this) }
        addEventListener(k, fn) { if (!this.listeners.has(k)) this.listeners.set(k, []); this.listeners.get(k).push(fn) }
        emit(k) { for (const fn of this.listeners.get(k) ?? []) fn() }
        play() { this.plays++; this.paused = false; this.ended = false; this.emit('play'); return Promise.resolve() }
        pause() { this.paused = true; this.emit('pause') }
        load() {}
        removeAttribute() {}
      }
    })
    page.setDefaultTimeout(10000)
    page.on('pageerror', (e) => { errors.push(e.message); console.log('PAGE ERROR', e.message) })
    const hit = (node) => width === 390 ? node.tap() : node.click()
    const thread = async () => {
      await page.evaluate(() => __experience.goChats()); await page.locator('.tt-row').filter({ hasText: 'A little room for the week' }).click()
      await page.waitForFunction(() => __experience.threads.st.open && !__experience.threads.st.open.loading && __experience.threads.st.open.eventVersion > 0)
    }
    const setMessage = async (kind, waiting = false, history = false) => {
      await page.evaluate(async ({ kind, response, waiting, history }) => {
        const x = __experience
        const human = { id: 'human', author: 'person', kind: 'message', text: 'A little room for the week?' }
        const questions = history ? Array.from({ length: 24 }, (_, i) => ({ ...human, id: `human:${i}` })) : [human]
        if (kind === 'thread') {
          const o = x.threads.st.open; o.messages = waiting ? [human] : [...(history ? questions : []), { id: 'reply', author: 'sprout', kind: 'message', text: response }]
          o.typing.clear(); o.steps.clear(); o.permissions.clear()
          if (waiting) (await import('/thread-record.js')).applyThreadEvent(o, { type: 'turn.started', persona: 'sprout' })
        }
        x.schedule()
      }, { kind, response, waiting, history })
      await page.waitForTimeout(60)
    }
    const shot = async () => { await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame) }); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); return page.screenshot({ animations: 'disabled' }) }
    const pair = async (name, render) => { await thread(); await render('thread'); const file = name + '-' + width + '-' + theme + '.png'; const dest = path.join(out, file); await page.screenshot({ path: dest, animations: 'disabled' }); captures.push(file); console.log(file) }
    const glyph = () => page.locator('.speakbtn').last()
    const speak = async (kind, history = false) => { await setMessage(kind, false, history); await glyph().waitFor(); await glyph().scrollIntoViewIfNeeded(); await hit(glyph()); await page.locator('.speakmenu').waitFor() }
    try {
      await page.goto(fixture.url); await page.locator('.tt-row').first().waitFor()
      await pair('waiting', async (kind) => { await setMessage(kind, true); await page.locator('.awaiting-words').waitFor(); assert.match(await page.locator('.awaiting-words').innerText(), /Thinking/) })
      await pair('rotated', async (kind) => { await setMessage(kind, true); await page.evaluate((kind) => { __elapsed += 12000; __experience.schedule() }, kind); await page.locator('.awaiting-words').filter({ hasText: 'Digging in' }).waitFor() })
      await pair('glyph', async (kind) => { await setMessage(kind); await glyph().waitFor(); await glyph().scrollIntoViewIfNeeded() })
      await pair('menu', speak)
      await pair('bar', async (kind) => {
        await speak(kind, true); pending = true; await hit(page.locator('.speakmenu [role=menuitem]').filter({ hasText: 'Listen' })); await page.locator('.player').waitFor()
        await page.evaluate(() => { const p = document.querySelector('.msgs'); p.scrollTop = 0; p.dispatchEvent(new Event('scroll')) })
        await page.locator('.playerbar').waitFor(); pending = false; await page.locator('.playerbar.ready').waitFor()
        assert.equal(await page.evaluate(() => __audio.reduce((n, a) => n + a.plays, 0)), 0)
      })
      // Actual streaming adapter, two concurrent names, text-to-tool-to-text,
      // informational wait and independently found audio on replay.
      await thread(); await setMessage('thread', true)
      fixture.emit('a-quieter-week', { type: 'message.delta', persona: 'sprout', draft: 'Streaming words' })
      await page.locator('.say').filter({ hasText: 'Streaming words' }).waitFor(); assert.equal(await page.locator('.awaiting-words').count(), 0)
      fixture.emit('a-quieter-week', { type: 'turn.step', persona: 'sprout', label: 'Read the notes' })
      await page.locator('.fold-head').last().click(); await page.locator('.awaiting-words').waitFor()
      fixture.emit('a-quieter-week', { type: 'message.delta', persona: 'sprout', draft: 'Back to streaming' })
      await page.locator('.say').filter({ hasText: 'Back to streaming' }).waitFor(); assert.equal(await page.locator('.awaiting-words').count(), 0)
      fixture.emit('a-quieter-week', { type: 'turn.step', persona: 'sprout', label: 'Waiting for background task', waiting: true })
      await page.locator('.awaiting-words').filter({ hasText: 'waiting on a background task' }).waitFor()
      assert.equal(await page.locator('.fold-head').filter({ hasText: 'Waiting for background task' }).count(), 0, 'a wait is never duplicated as a reply step')
      fixture.emit('a-quieter-week', { type: 'turn.started', persona: 'scout' }); await page.locator('.awaiting-words').filter({ hasText: 'Scout' }).waitFor()
      await setMessage('thread'); await page.waitForFunction(() => document.querySelector('.speakbtn')?.dataset.take === 'ready')
      await hit(glyph()); await hit(page.locator('.speakmenu [role=menuitem]').filter({ hasText: 'Listen' })); await page.waitForFunction(() => __audio.some((a) => !a.paused))
      const audioCount = await page.evaluate(() => __audio.length)
      await page.evaluate(() => __experience.goChats()); await page.locator('.playerbar.playing').waitFor(); assert.equal(await page.evaluate(() => __audio.length), audioCount)
      await hit(page.locator('.playerbar-text')); await page.locator('.tt-thread').waitFor(); await page.locator('.player').waitFor()
      assert.equal(await page.evaluate(() => __audio.length), audioCount)
      const beforeReload = posts.length
      await page.reload(); await page.waitForFunction(() => globalThis.__experience?.threads.st.open?.thread.id === 'a-quieter-week' && !__experience.threads.st.open.loading && __experience.threads.st.open.eventVersion > 0)
      await page.waitForFunction(() => document.querySelector('.speakbtn')?.dataset.take === 'ready')
      assert.equal(posts.length, beforeReload, 'reload finds the existing take without generation')
      assert.equal(await page.evaluate(() => __audio.length), 0, 'lookup never starts playback')
      assert.deepEqual(errors, [])
      results.push({ width, theme, wordRotation: true, streamOwnLine: true, stepRestoresLine: true, backgroundWait: true, concurrentNames: true, lookupDot: true, reloadLookup: true, noAutoplay: true, playerAcrossChats: true, returnToThread: true, posts: posts.map(({ text, ...meta }) => ({ ...meta, words: text.split(/\s+/).length })), errors, captures })
      console.log(`PASS ${width} ${theme}`)
    } finally { await context.close(); await fixture.close() }
  }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify(results, null, 2) + '\n')
  await writeManifest()
} finally { await browser.close() }
