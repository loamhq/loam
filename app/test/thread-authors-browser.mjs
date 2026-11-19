// Actual painted page, isolated records and speech answers. No paid voice calls.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { createHash } from 'node:crypto'
import { oneProductFixture } from './one-product-fixture.mjs'

const out = process.env.THREAD_AUTHORS_EVIDENCE
if (!out) throw new Error('Set THREAD_AUTHORS_EVIDENCE')
// Keep the first review's captures intact when painting a visual follow-up.
const prefix = process.env.THREAD_AUTHORS_CAPTURE_PREFIX ?? ''
const artifact = (name) => path.join(out, prefix + name)
await mkdir(out, { recursive: true })
async function pairCaptures() {
  const sharp = (await import(process.env.LOAM_SHARP || 'sharp')).default, manifest = []
  for (const width of [390, 1440]) for (const state of ['glyph', 'mid-run', 'handover', 'docked-player', 'board-seen']) {
    const a = await readFile(artifact(`${state}-${width}-light.png`)), b = await readFile(artifact(`${state}-${width}-dark.png`))
    const height = a.readUInt32BE(20), file = prefix + `${state}-${width}-pair.png`
    const labels = Buffer.from(`<svg width="${width * 2}" height="32"><text x="14" y="22" font-family="sans-serif" font-size="13" fill="#2A2521">${state} · ${width} · light</text><text x="${width + 14}" y="22" font-family="sans-serif" font-size="13" fill="#2A2521">${state} · ${width} · dark</text></svg>`)
    await sharp({ create: { width: width * 2, height: height + 32, channels: 4, background: '#E6E1DA' } }).composite([{ input: a, left: 0, top: 32 }, { input: b, left: width, top: 32 }, { input: labels, left: 0, top: 0 }]).png().toFile(path.join(out, file))
    const data = await readFile(path.join(out, file))
    manifest.push({ file, width: width * 2, height: height + 32, sha256: createHash('sha256').update(data).digest('hex') })
  }
  await writeFile(artifact('captures.json'), JSON.stringify(manifest, null, 2) + '\n')
}
async function compareCaptures() {
  assert.ok(prefix, 'comparison captures need a prefix to preserve the baseline')
  const sharp = (await import(process.env.LOAM_SHARP || 'sharp')).default, manifest = []
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) for (const state of ['mid-run', 'handover']) {
    const before = await readFile(path.join(out, `${state}-${width}-${theme}.png`)), after = await readFile(artifact(`${state}-${width}-${theme}.png`))
    const height = before.readUInt32BE(20), file = prefix + `${state}-${width}-${theme}-before-after.png`
    const labels = Buffer.from(`<svg width="${width * 2}" height="32"><text x="14" y="22" font-family="sans-serif" font-size="13" fill="#2A2521">Before · 59ae123</text><text x="${width + 14}" y="22" font-family="sans-serif" font-size="13" fill="#2A2521">After · floating author</text></svg>`)
    await sharp({ create: { width: width * 2, height: height + 32, channels: 4, background: '#E6E1DA' } }).composite([{ input: before, left: 0, top: 32 }, { input: after, left: width, top: 32 }, { input: labels, left: 0, top: 0 }]).png().toFile(path.join(out, file))
    const data = await readFile(path.join(out, file))
    manifest.push({ file, width: width * 2, height: height + 32, sha256: createHash('sha256').update(data).digest('hex'), before_sha256: createHash('sha256').update(before).digest('hex'), after_sha256: createHash('sha256').update(after).digest('hex') })
  }
  await writeFile(artifact('comparisons.json'), JSON.stringify(manifest, null, 2) + '\n')
}
if (process.argv.includes('--pair-only')) { await pairCaptures(); process.exit(0) }
if (process.argv.includes('--compare-only')) { await compareCaptures(); process.exit(0) }
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox'] })
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8'), results = [], dockCaptures = []
const paragraph = 'Keep one clear place for the next step. Leave room between commitments, gather the loose ends, and let the useful details stay close to the conversation. A quiet rhythm makes it easier to see what matters and to choose what comes next.'
const prose = (heading, count = 24) => `## ${heading}\n\n` + Array.from({ length: count }, () => paragraph).join('\n\n')
const messages = [
  { id: 'human-before', author: 'person', kind: 'message', text: 'How can we make room for the week?' },
  { id: 'reply-a', author: 'sprout', kind: 'message', text: '', segments: [
    { text: 'I’ll gather the notes first.' }, { did: [{ label: 'Read the weekly notes' }] },
    { text: prose('A little room for the week') }, { did: [{ label: 'Saved the next steps' }] },
  ] },
  { id: 'reply-b', author: 'sprout', kind: 'message', text: prose('Leave a little room for the unexpected') },
  { id: 'reply-c', author: 'scout', kind: 'message', text: prose('A fresh view of the next steps') },
  { id: 'notice', author: 'system', kind: 'system', text: 'The notes are ready.' },
  { id: 'short', author: 'sprout', kind: 'message', text: 'All gathered.' },
  { id: 'human-after', author: 'person', kind: 'message', text: 'Thank you. I’ll take it from here.' },
]
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    const fixture = await oneProductFixture(), captures = [], errors = []
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390 })
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__authors = { threads, schedule };' }))
    await context.route('**/api/health', (route) => route.fulfill({ json: { configured: true, ok: true, speech: true } }))
    await context.route('**/x/read-aloud/status', r => r.fulfill({ json: { speech: true } }))
    const posts = []
    await context.route(/\/x\/read-aloud\/speech(?:\/.*)?$/, (route) => {
      const request = route.request(), url = new URL(request.url())
      if (url.pathname.endsWith('/lookup')) return route.fulfill({ json: { status: 'none' } })
      if (url.pathname === '/x/read-aloud/speech') posts.push(request.postDataJSON())
      return route.fulfill({ json: { id: 'a'.repeat(32), status: 'ready', duration_s: 90, url: '/x/read-aloud/speech/' + 'a'.repeat(32) + '/audio.mp3' } })
    })
    await context.addInitScript(() => {
      globalThis.Audio = class {
        constructor() { Object.assign(this, { paused: true, ended: false, currentTime: 0, duration: 90, buffered: { length: 0 }, listeners: new Map() }) }
        addEventListener(type, fn) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(fn) }
        play() { this.paused = false; for (const fn of this.listeners.get('play') ?? []) fn(); return Promise.resolve() }
        pause() { this.paused = true; for (const fn of this.listeners.get('pause') ?? []) fn() }
        load() {}
        removeAttribute() {}
      }
    })
    const page = await context.newPage(); page.setDefaultTimeout(10000)
    page.on('pageerror', (e) => errors.push(e.message))
    const capture = async (name) => {
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame) })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      const file = prefix + `${name}-${width}-${theme}.png`
      await page.screenshot({ path: path.join(out, file) }); captures.push(file)
      if (name === 'composer-over-author') {
        const data = await readFile(path.join(out, file))
        dockCaptures.push({ file, width: data.readUInt32BE(16), height: data.readUInt32BE(20), sha256: createHash('sha256').update(data).digest('hex') })
        await writeFile(artifact('dock-captures.json'), JSON.stringify(dockCaptures, null, 2) + '\n')
      }
    }
    const scroll = async (selector, offset) => {
      await page.evaluate(({ selector, offset }) => {
        const pane = document.querySelector('.tt-stream'), node = document.querySelector(selector)
        pane.scrollTop += node.getBoundingClientRect().top - pane.getBoundingClientRect().top - offset
        pane.dispatchEvent(new Event('scroll'))
      }, { selector, offset })
      await page.waitForTimeout(240)
    }
    const geometry = () => page.evaluate(() => {
      const rect = (node) => { const r = node.getBoundingClientRect(); return { top: r.top, bottom: r.bottom, height: r.height, left: r.left, right: r.right } }
      return { edge: document.querySelector('.tt-stream').getBoundingClientRect().top + parseFloat(getComputedStyle(document.querySelector('.tt-stream')).paddingTop), pane: rect(document.querySelector('.tt-stream')), header: rect(document.querySelector('.tt-head')),
        bar: document.querySelector('.playerbar') ? rect(document.querySelector('.playerbar')) : null,
        runs: [...document.querySelectorAll('.tt-author-run')].map((run) => ({ author: run.dataset.author, stuck: run.classList.contains('author-stuck'), run: rect(run), marker: rect(run.querySelector('.tt-author-pill')), name: run.querySelector('.tt-speaker').textContent })) }
    })
    const composerOverAuthor = async () => {
      // Put the incoming author's real line through the editor's placeholder.
      await page.evaluate(() => {
        const pane = document.querySelector('.tt-stream'), pill = document.querySelector('.tt-author-run[data-author="scout"] .tt-author-pill'), editor = document.querySelector('#draft')
        pane.scrollTop += pill.getBoundingClientRect().top - editor.getBoundingClientRect().top
        pane.dispatchEvent(new Event('scroll'))
      })
      await page.waitForTimeout(240)
      await capture('composer-over-author')
      const audit = await page.evaluate(() => {
        const dock = document.querySelector('.tt-conversation > .thread-dock'), pill = document.querySelector('.tt-author-run[data-author="scout"] .tt-author-pill'), editor = document.querySelector('#draft')
        const r = pill.getBoundingClientRect(), e = editor.getBoundingClientRect(), x = Math.max(r.left, e.left) + 12, y = Math.max(r.top, e.top) + 16
        return { hitComposer: Boolean(document.elementFromPoint(x, y)?.closest('.composer')), markerZ: getComputedStyle(pill.parentNode).zIndex, dockZ: getComputedStyle(dock).zIndex,
          laterInDOM: Boolean(pill.compareDocumentPosition(dock) & Node.DOCUMENT_POSITION_FOLLOWING),
          pill: { top: r.top, bottom: r.bottom }, editor: { top: e.top, bottom: e.bottom },
          clip: { x: Math.ceil(Math.max(r.left, e.left) + 4), y: Math.ceil(Math.max(r.top, e.top) + 2), width: Math.floor(Math.min(r.right, e.right) - Math.max(r.left, e.left) - 8), height: Math.floor(Math.min(r.bottom, e.bottom) - Math.max(r.top, e.top) - 4) } }
      })
      assert.ok(audit.hitComposer, 'overlapping author cannot intercept the editor')
      assert.ok(audit.clip.width > 20 && audit.clip.height > 20, 'the fixture genuinely overlaps the author and editor')
      const sharp = (await import(process.env.LOAM_SHARP || 'sharp')).default
      const paint = async (clip) => sharp(await page.screenshot({ clip, caret: 'hide', animations: 'disabled' })).raw().toBuffer()
      const difference = async (clip = audit.clip) => {
        const visible = await paint(clip)
        await page.locator('.tt-author-run[data-author="scout"] .tt-speaker').evaluate((el) => { el.style.visibility = 'hidden' })
        const hidden = await paint(clip)
        await page.locator('.tt-author-run[data-author="scout"] .tt-speaker').evaluate((el) => { el.style.visibility = '' })
        return visible.reduce((n, value, i) => n + Number(value !== hidden[i]), 0)
      }
      audit.naturalPaintDifference = await difference()
      await writeFile(artifact(`dock-paint-${width}-${theme}.json`), JSON.stringify(audit, null, 2) + '\n')
      assert.equal(audit.naturalPaintDifference, 0, 'hiding the covered author must not change any editor pixels')
      // Stress the same overlap with both backdrop layers showing too.
      await page.locator('.tt-author-run[data-author="scout"]').evaluate((run) => run.classList.add('author-stuck'))
      await page.waitForTimeout(240)
      audit.decoratedPaintDifference = await difference()
      assert.equal(audit.decoratedPaintDifference, 0, 'the pill backdrop and prose fade must also stay behind the editor')
      await page.locator('.tt-author-run[data-author="scout"]').evaluate((run) => run.classList.remove('author-stuck'))
      await page.getByRole('button', { name: 'Mention an agent', exact: true }).click()
      await page.locator('.tt-mentions').waitFor()
      audit.mention = await page.locator('.tt-mentions').evaluate((list) => {
        const box = list.getBoundingClientRect(), hit = document.elementFromPoint(box.left + 24, box.top + 24)
        return { z: getComputedStyle(list).zIndex, insideDock: Boolean(list.closest('.thread-dock')), hit: Boolean(hit?.closest('.tt-mentions')) }
      })
      assert.deepEqual(audit.mention, { z: '10', insideDock: true, hit: true }, 'the mention popover retains its own layer inside the dock')
      await page.evaluate(() => {
        const pane = document.querySelector('.tt-stream'), pill = document.querySelector('.tt-author-run[data-author="scout"] .tt-author-pill'), list = document.querySelector('.tt-mentions')
        pane.scrollTop += pill.getBoundingClientRect().top - list.getBoundingClientRect().top - 18
        pane.dispatchEvent(new Event('scroll'))
      })
      await page.waitForTimeout(240)
      const mentionClip = await page.evaluate(() => {
        const r = document.querySelector('.tt-author-run[data-author="scout"] .tt-author-pill').getBoundingClientRect(), m = document.querySelector('.tt-mentions').getBoundingClientRect()
        return { x: Math.ceil(Math.max(r.left, m.left) + 8), y: Math.ceil(r.top + 2), width: Math.floor(Math.min(r.right, m.right) - Math.max(r.left, m.left) - 16), height: Math.floor(r.height - 4) }
      })
      assert.ok(mentionClip.width > 20 && mentionClip.height > 20)
      audit.mentionPaintDifference = await difference(mentionClip)
      assert.equal(audit.mentionPaintDifference, 0, 'the incoming author also paints entirely behind the mention list')
      await page.getByRole('button', { name: 'Remove mention', exact: true }).click()
      await page.locator('.tt-mentions').waitFor({ state: 'detached' })
      await page.evaluate(() => { __authors.threads.st.open.loading = true; __authors.schedule() })
      await page.locator('.tt-opening-note').waitFor({ state: 'attached' })
      audit.loading = await page.evaluate(() => {
        const editor = document.querySelector('#draft').getBoundingClientRect(), header = document.querySelector('.tt-head').getBoundingClientRect()
        return { dockZ: getComputedStyle(document.querySelector('.tt-conversation > .thread-dock')).zIndex,
          coverHit: Boolean(document.elementFromPoint(editor.left + 20, editor.top + 16)?.closest('.thread-load')),
          headerHit: Boolean(document.elementFromPoint(header.left + 24, header.top + 24)?.closest('.tt-head')) }
      })
      assert.deepEqual(audit.loading, { dockZ: '2', coverHit: false, headerHit: true }, 'opening leaves the composer and header reachable')
      await page.evaluate(() => { __authors.threads.st.open.loading = false; __authors.schedule() })
      await page.locator('.tt-thread > .thread-load').waitFor({ state: 'detached' })
      await writeFile(artifact(`dock-paint-${width}-${theme}.json`), JSON.stringify(audit, null, 2) + '\n')
      return audit
    }
    try {
      await page.goto(fixture.url); await page.locator('.tt-row').filter({ hasText: 'A little room for the week' }).click()
      await page.waitForFunction(() => __authors.threads.st.open && !__authors.threads.st.open.loading)
      await page.evaluate((messages) => {
        const o = __authors.threads.st.open
        Object.assign(o, { messages, atEnd: false, restoreTop: 0, has_more: false }); o.typing.clear(); o.steps.clear(); o.permissions.clear()
        __authors.schedule()
      }, messages)
      await page.locator('[data-message-id="reply-c"] .speakbtn').waitFor()
      await page.waitForTimeout(240)
      assert.equal(await page.locator('.tt-author-run').count(), 3, 'consecutive messages share one run; system and human messages break it')
      assert.equal(await page.locator('.tt-speaker').count(), 3, 'no extra overlay or human marker')
      assert.equal(await page.locator('.author-stuck').count(), 0, 'natural author lines have no floating backdrop')
      assert.deepEqual(await page.locator('.tt-speaker').first().evaluate((row) => [getComputedStyle(row, '::before').opacity, getComputedStyle(row.firstElementChild, '::before').opacity]), ['0', '0'], 'natural author lines have neither veil nor pill fill')
      for (const id of ['reply-a', 'reply-b', 'reply-c']) {
        const message = page.locator(`[data-message-id="${id}"]`)
        assert.equal(await message.locator('.speakbtn').count(), 1)
        assert.equal(await message.locator('.say.agent').last().locator('.tt-reply-footer .speakbtn').count(), 1)
      }
      assert.equal(await page.locator('[data-message-id="short"] .speakbtn').count(), 0)
      const footer = await page.locator('[data-message-id="reply-a"] .tt-reply-footer').boundingBox()
      const trailing = await page.locator('[data-message-id="reply-a"] > .line').last().boundingBox()
      assert.ok(footer.y + footer.height <= trailing.y, 'glyph ends prose before trailing tool fold')
      await scroll('[data-message-id="reply-a"] .tt-reply-footer', width === 390 ? 340 : 500)
      await capture('glyph')

      await scroll('[data-message-id="reply-b"]', -140)
      let g = await geometry()
      assert.equal(g.runs[0].stuck, true)
      assert.ok(Math.abs(g.runs[0].marker.top - g.edge) < 1, 'author sticks at the reading edge')
      assert.ok(g.runs[0].marker.right - g.runs[0].marker.left < 180, 'compact marker, no column-wide band')
      assert.ok(g.pane.top >= g.header.bottom)
      await capture('mid-run')
      const float = await page.locator('.tt-speaker').first().evaluate((row) => {
        const pill = row.firstElementChild, veil = getComputedStyle(row, '::before'), backdrop = getComputedStyle(pill, '::before'), space = getComputedStyle(pill)
        return { veil: veil.backgroundImage, opacity: veil.opacity, pointerEvents: getComputedStyle(row).pointerEvents, padding: parseFloat(space.paddingTop), border: backdrop.borderTopWidth, tint: backdrop.backgroundColor }
      })
      assert.match(float.veil, /linear-gradient/, 'a dissolving fade softens prose beside the label')
      assert.equal(float.opacity, '1'); assert.equal(float.pointerEvents, 'none', 'the fade does not block prose selection or links')
      assert.ok(float.padding >= 7); assert.equal(float.border, '1px')
      await page.emulateMedia({ reducedMotion: 'reduce' })
      const reducedDurations = await page.locator('.tt-speaker').first().evaluate((row) => [getComputedStyle(row, '::before').transitionDuration, getComputedStyle(row.firstElementChild, '::before').transitionDuration])
      // The shared reduced-motion rule uses a one-microsecond !important floor.
      assert.ok(reducedDurations.every((value) => value.split(',').every((duration) => parseFloat(duration) <= .001)), 'reduced motion suppresses visible transitions on both layers')
      await page.emulateMedia({ reducedMotion: 'no-preference' })

      // Observe the actual push-off, with both authors in the reading column.
      await scroll('.tt-author-run[data-author="scout"]', 34)
      g = await geometry()
      assert.ok(g.runs[0].marker.top < g.pane.top - 5, 'incoming run pushes the outgoing author up')
      assert.ok(g.runs[0].marker.bottom <= g.runs[0].run.bottom + 1, 'sticky stays within its own run')
      assert.ok(g.runs[1].marker.top > g.pane.top, 'incoming real line is still visible naturally')
      await capture('handover')
      await scroll('.tt-author-run[data-author="scout"]', -160)
      g = await geometry()
      assert.equal(g.runs[1].stuck, true)
      assert.ok(Math.abs(g.runs[1].marker.top - g.edge) < 1)
      assert.ok(g.runs[0].marker.bottom <= g.pane.top, 'only the next author remains at the top')
      await scroll('.tt-author-run[data-author="sprout"]', 100)
      assert.equal((await geometry()).runs[0].stuck, false, 'scroll-back restores the real author line')
      const dockAudit = await composerOverAuthor()

      // Make a real shared player, then dock it by reading earlier prose.
      const glyph = page.locator('[data-message-id="reply-a"] .speakbtn')
      await glyph.scrollIntoViewIfNeeded(); await glyph.click()
      await page.locator('.speakmenu [role="menuitem"]').filter({ hasText: 'Listen' }).click()
      await page.locator('.player').waitFor()
      await scroll('[data-message-id="reply-a"] .say.agent', -250)
      await page.locator('.playerbar').waitFor()
      await page.waitForTimeout(240)
      g = await geometry()
      assert.ok(g.bar.bottom <= g.pane.top + 1, 'docked player stays above the reading area')
      assert.ok(g.runs[0].marker.top >= g.bar.bottom - 1, 'marker and player do not overlap')
      assert.equal(g.runs[0].stuck, true)
      await capture('docked-player')
      assert.equal(posts.length, 1)
      assert.equal(posts[0].text, messages[1].segments.filter((part) => part.text).map((part) => part.text).join('\n\n'))

      // The board's independent seen strip never borrows the thread's marker.
      await page.getByRole('button', { name: 'Board', exact: true }).click()
      await page.locator('.tt-seen-strip').waitFor()
      assert.equal(await page.locator('.tt-conversation').getAttribute('inert'), '')
      assert.equal(await page.locator('.tt-board .tt-speaker').count(), 0)
      await page.waitForTimeout(240)
      await capture('board-seen')
      assert.equal(await page.evaluate(() => { const r = document.querySelector('#draft').getBoundingClientRect(); return Boolean(document.elementFromPoint(r.left + r.width / 2, r.top + 16)?.closest('.tt-board,.tt-board-scrim')) }), true, 'the board and its scrim still cover the dock')
      await page.getByRole('button', { name: 'Close board', exact: true }).click()

      // New replies, redraw and prepend preserve the author grouping and marker.
      await page.evaluate(() => __authors.schedule()); await page.waitForTimeout(60)
      assert.equal((await geometry()).runs[0].stuck, true)
      await page.evaluate(() => { __authors.threads.st.open.messages.unshift({ id: 'earlier', author: 'scout', kind: 'message', text: 'An earlier thought.' }); __authors.schedule() })
      await page.waitForTimeout(60)
      assert.equal(await page.locator('[data-message-id="reply-a"] .speakbtn').count(), 1)

      await page.evaluate(() => {
        const o = __authors.threads.st.open
        o.messages.push(...Array.from({ length: 20 }, (_, i) => ({ id: `human-tail-${i}`, author: 'person', kind: 'message', text: 'A thought to come back to later.' })))
        __authors.schedule()
      })
      await page.locator('[data-message-id="human-tail-19"]').waitFor()
      await scroll('[data-message-id="human-tail-19"]', 200)
      g = await geometry()
      assert.ok(g.runs.every((run) => run.marker.bottom <= g.pane.top), 'no author lingers when the reader reaches human messages')

      assert.deepEqual(errors, [])
      results.push({ width, theme, glyphAnchored: true, distinctConsecutiveGlyphs: true, appearAndScrollBack: true, nativeHandover: true, floatingProseFade: true, reducedMotion: true, float, dockAudit, dockedPlayerSeparation: true, boardSeenStrip: true, captures, errors })
      console.log(`PASS ${width} ${theme}`)
    } finally { await context.close(); await fixture.close() }
  }
  await writeFile(artifact('browser.json'), JSON.stringify(results, null, 2) + '\n')
  await pairCaptures()
  if (process.env.THREAD_AUTHORS_COMPARE === '1') await compareCaptures()
} finally { await browser.close() }
