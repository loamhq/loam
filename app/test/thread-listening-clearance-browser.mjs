import { observeAudio } from './audio-probe.mjs'
// Local real page/relay and synthetic records only; no real thread writes.
import assert from 'node:assert/strict'
import { readFile, writeFile, mkdir, readdir } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
const out = process.env.THREAD_CLEARANCE_EVIDENCE
if (!out) throw new Error('Set THREAD_CLEARANCE_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const baseline = '6070889', id = 'a-quieter-week', results = []
const old = (file) => execFileSync('git', ['show', `${baseline}:public/${file}`], { encoding: 'utf8' })
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
try {
  for (const width of [390, 1440].filter((v) => !process.env.CLEARANCE_WIDTH || +process.env.CLEARANCE_WIDTH === v)) for (const theme of ['light', 'dark'].filter((v) => !process.env.CLEARANCE_THEME || process.env.CLEARANCE_THEME === v)) for (const phase of ['before', 'after'].filter((v) => !process.env.CLEARANCE_PHASE || process.env.CLEARANCE_PHASE === v)) {
    const app = await oneProductFixture(), errors = []
    await app.f.api('PATCH', `/threads/${id}`, { add: ['ledger', 'pip'] })
    const record = await app.f.api('GET', `/threads/${id}`)
    record.messages.unshift(...Array.from({ length: 14 }, (_, i) => ({ id: `history-${i}`, author: i % 2 ? 'quill' : 'sprout', kind: 'message', engine: 'claude-code', at: '2026-11-11T02:35:00Z', text: `A little room to think, part ${i + 1}.\n\nKeep the next step clear and leave enough space to read it. The conversation should stay where you left it, even as the draft changes.`, did: [] })))
    app.routes[`GET /v1/threads/${id}`] = (req, res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(record)) }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390, timezoneId: 'America/Los_Angeles' })
    if (phase === 'before') for (const file of ['threads.js', 'threads.css', 'thread-identity.js']) await context.route(`**/${file}`, (route) => route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: old(file) }))
    // Optional capture-only font for the historical emoji choices.
    if (process.env.LOAM_EMOJI) {
      const dir = process.env.LOAM_EMOJI, css = (await readFile(path.join(dir, 'index.css'), 'utf8')).replaceAll('./files/', '/fixture-emoji/').replaceAll('Noto Emoji', 'FixtureEmoji')
      await context.route('**/fixture-emoji/*', async (route) => route.fulfill({ contentType: 'font/woff2', body: await readFile(path.join(dir, 'files', new URL(route.request().url()).pathname.split('/').at(-1))) }))
      await context.route('**/fonts/fonts.css', async (route) => route.fulfill({ contentType: 'text/css', body: await readFile(new URL('../public/fonts/fonts.css', import.meta.url), 'utf8') + '\n' + css + '\n.tt-icon-choice,.thread-tile {font-family:Figtree,FixtureEmoji,sans-serif;}' }))
    }
    await observeAudio(context)
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__clearance = { threads, state, schedule };' }))
    const page = await context.newPage(); page.setDefaultTimeout(12000); page.on('pageerror', (e) => errors.push(e.message))
    const settle = async () => { await page.waitForTimeout(600); await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) }) }
    const shot = async (state) => { await settle(); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); await page.screenshot({ path: path.join(out, `${phase}-${state}-${width}-${theme}.png`), animations: 'disabled' }) }
    const draft = page.locator('#draft')
    const setDraft = async (text) => { await draft.fill(text); await page.evaluate(() => { __clearance.threads.st.mention = null; __clearance.schedule() }); await settle() }
    const measure = () => page.evaluate(() => {
      const pane = document.querySelector('.tt-stream'), composer = document.querySelector('.composer-box'), dock = document.querySelector('.thread-dock')
      const faces = [...document.querySelectorAll('.tt-listener')].map((n) => n.getBoundingClientRect())
      const last = [...pane.querySelectorAll('.tt-message-time')].at(-1)?.getBoundingClientRect()
      const bounds = composer.getBoundingClientRect(), dockBounds = dock.getBoundingClientRect()
      return { scroll: pane.scrollTop, max: pane.scrollHeight - pane.clientHeight, padding: parseFloat(getComputedStyle(pane).paddingBottom), lastBottom: last?.bottom, faceTop: faces.length ? Math.min(...faces.map((r) => r.top)) : null, faceBottom: faces.length ? Math.max(...faces.map((r) => r.bottom)) : null, composerY: bounds.y, composerH: bounds.height, dockTop: dockBounds.top, shelfHeight: parseFloat(getComputedStyle(dock, '::before').height) || 0, atEnd: __clearance.threads.st.open.atEnd }
    })
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor(); await page.evaluate((id) => __clearance.threads.open(id), id)
      await page.waitForFunction(() => __clearance.threads.st.open && !__clearance.threads.st.open.loading && !__clearance.threads.st.open.refreshing)
      await settle(); await page.locator('.tt-prose-content:not(.tt-prose-plain)').last().waitFor()
      // Let progressive formatting finish before measuring the anchored last line.
      await settle(); await setDraft('')
      const empty = await measure()
      const motion = []
      await draft.fill('@all'); await page.evaluate(() => { __clearance.threads.st.mention = null; __clearance.schedule() })
      for (let i = 0; i < 8; i++) { await page.waitForTimeout(45); motion.push(await measure()) }
      await settle(); const all = await measure()
      assert.equal(await page.locator('.tt-listener-more').innerText(), '+1')
      if (phase === 'after') {
        assert.ok(Math.abs(all.composerY - empty.composerY) <= 1, JSON.stringify({ empty, all }))
        assert.ok(all.lastBottom < all.faceTop - 3, JSON.stringify(all))
        assert.ok(Math.abs(all.max - all.scroll) <= 2, JSON.stringify(all))
        assert.ok(Math.abs(all.padding - empty.padding - 44) <= 1, JSON.stringify({ empty, all }))
        assert.ok(motion.every((m) => m.atEnd !== false), JSON.stringify(motion))
        assert.ok(motion.some((m) => m.padding > empty.padding + 2 && m.padding < all.padding - 2), 'the inset interpolates rather than jumps')
      }
      if (phase === 'before') assert.ok(all.lastBottom > all.faceTop, 'the baseline reproduces the collision')
      await shot('all-at-end')
      for (const [state, text] of [['two-at-end', '@quill @scout'], ['all-at-end', '@all']]) {
        await setDraft(text); const geometry = await measure()
        if (phase === 'after') { assert.ok(geometry.lastBottom < geometry.faceTop - 3, JSON.stringify(geometry)); assert.ok(Math.abs(geometry.max - geometry.scroll) <= 2) }
        await shot(state)
      }
      await setDraft(''); await page.evaluate(() => { (globalThis.__audio?.dictation?.state ?? __clearance.state).rec = { mode: 'live', phase: 'recording', target: 'draft', started: Date.now(), elapsed: 0, said: '', guess: '' }; __clearance.schedule() }); await settle()
      const dictation = await measure()
      if (phase === 'after') assert.ok(dictation.lastBottom < dictation.faceTop - 3, JSON.stringify(dictation))
      await shot('dictation-at-end')
      await page.evaluate(() => { (globalThis.__audio?.dictation?.state ?? __clearance.state).rec = null; __clearance.schedule() }); await settle(); await setDraft('')
      // Even 20 px above the end must not be clamped when the inset retires.
      await setDraft('@all'); await page.locator('.tt-stream').evaluate((n) => { n.scrollTop -= 20 }); await settle()
      const near = await measure()
      await setDraft(''); const nearCleared = await measure()
      if (phase === 'after') {
        assert.ok(Math.abs(nearCleared.scroll - near.scroll) <= 1, JSON.stringify({ near, nearCleared }))
        assert.equal(nearCleared.atEnd, false)
        assert.equal(nearCleared.padding, near.padding)
      }
      await shot('near-end-cleared')
      await page.locator('.tt-stream').evaluate((n) => { n.scrollTop = n.scrollHeight }); await settle()
      if (phase === 'after') { const returned = await measure(); assert.ok(Math.abs(returned.padding - empty.padding) <= 1, JSON.stringify(returned)); assert.ok(Math.abs(returned.max - returned.scroll) <= 2) }
      // Protect an actual reading position while faces arrive, change and leave.
      await page.locator('.tt-stream').evaluate((n) => { n.scrollTop -= 500 }); await settle()
      const anchor = () => page.evaluate(async () => { const { readingAnchor } = await import('/thread-opening.js'); return readingAnchor(document.querySelector('.tt-stream')) })
      const beforeAnchor = await anchor(), beforeScroll = (await measure()).scroll
      for (const [state, text] of [['two-scrolled', '@quill @scout'], ['all-scrolled', '@all'], ['empty-scrolled', '']]) {
        await setDraft(text); const current = await anchor()
        if (phase === 'after') { assert.equal(current.id, beforeAnchor.id); assert.ok(Math.abs(current.offset - beforeAnchor.offset) <= 1, JSON.stringify({ beforeAnchor, current })); assert.ok(Math.abs((await measure()).scroll - beforeScroll) <= 1) }
        await shot(state)
      }
      await page.evaluate(() => { (globalThis.__audio?.dictation?.state ?? __clearance.state).rec = { mode: 'live', phase: 'recording', target: 'draft', started: Date.now(), elapsed: 0, said: '', guess: '' }; __clearance.schedule() }); await settle()
      if (phase === 'after') { const current = await anchor(); assert.equal(current.id, beforeAnchor.id); assert.ok(Math.abs(current.offset - beforeAnchor.offset) <= 1) }
      await shot('dictation-scrolled')
      await page.evaluate(() => { (globalThis.__audio?.dictation?.state ?? __clearance.state).rec = null; __clearance.schedule() }); await settle()
      await page.getByTitle('Thread details', { exact: true }).click(); await shot('icon-picker')
      if (phase === 'after') { assert.equal(await page.locator('.tt-icon-choice').count(), 6); assert.equal(await page.locator('.tt-icon-choice svg').count(), 6) }
      await page.getByRole('button', { name: 'Chat', exact: true }).click(); await page.emulateMedia({ reducedMotion: 'reduce' }); await setDraft('@all')
      if (phase === 'after') assert.ok(await page.locator('.tt-stream').evaluate((n) => parseFloat(getComputedStyle(n).transitionDuration) <= .00001))
      await shot('reduced-motion')
      // Narrow/short layout still keeps faces inside their protected shelf.
      if (width === 390) { await page.setViewportSize({ width, height: 600 }); await settle(); await page.evaluate(() => { const o = __clearance.threads.st.open; o.atEnd = true; for (const key of ['replyAnchor', 'readingPosition', 'openingBoundary', 'landAt', 'savedAnchor', 'restoreTop']) delete o[key]; __clearance.schedule() }); await settle(); const compact = await measure(); if (phase === 'after') assert.ok(compact.lastBottom < compact.faceTop - 3, JSON.stringify(compact)); await shot('short-viewport') }
      assert.deepEqual(errors, [])
      results.push({ width, theme, phase, empty, all, dictation, near, nearCleared, motion, beforeAnchor, beforeScroll, errors, ...(phase === 'after' ? { fixedComposer: true, lastLineClear: true, smoothEnd: true, readingAnchorRetained: true, sixGlyphs: true, reducedMotion: true } : {}) })
      console.log(`PASS ${phase} ${width} ${theme}: end clearance, stationary reading, dictation and icon picker`)
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify({ baseline, results }, null, 2) + '\n')
  const captures = []
  for (const file of (await readdir(out)).filter((f) => f.endsWith('.png')).sort()) captures.push({ file, sha256: createHash('sha256').update(await readFile(path.join(out, file))).digest('hex') })
  await writeFile(path.join(out, 'captures.json'), JSON.stringify(captures, null, 2) + '\n')
} finally { await browser.close() }
