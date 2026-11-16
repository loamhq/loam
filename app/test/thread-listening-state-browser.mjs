import { observeAudio } from './audio-probe.mjs'
// Local real page and relay; every record and microphone reading is synthetic.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
const out = process.env.THREAD_LISTENING_EVIDENCE
if (!out) throw new Error('Set THREAD_LISTENING_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'] })
const baseline = 'ef2043b', id = 'a-quieter-week', results = []
const old = (file) => execFileSync('git', ['show', `${baseline}:public/${file}`], { encoding: 'utf8' })
try {
  const design = await browser.newPage()
  await design.goto(new URL('../design/Loam App.dc.html', import.meta.url).href)
  await design.screenshot({ path: path.join(process.env.TMPDIR, 'listening-design-reference.png') }); await design.close()
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) for (const phase of ['before', 'after']) {
    const app = await oneProductFixture(), errors = []
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390 })
    if (phase === 'before') for (const file of ['threads.js', 'threads.css', 'thread-identity.js']) await context.route(`**/${file}`, (route) => route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: old(file) }))
    const source = phase === 'before' ? old('app.js') : await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
    await observeAudio(context)
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__listening = { threads, state, schedule };' }))
    const page = await context.newPage(); page.setDefaultTimeout(10000); page.on('pageerror', (e) => errors.push(e.message))
    await page.clock.install({ time: new Date('2026-11-11T04:30:00Z') })
    const settle = async (ms = 350) => { await page.clock.runFor(ms); await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame) }) }
    const capture = async (state, ms = 350) => {
      await settle(ms)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      // Still captures show settled faces; the frame sequence below samples motion.
      await page.evaluate(() => {
        for (const node of document.querySelectorAll('.tt-listener')) for (const anim of node.getAnimations()) { anim.pause(); anim.currentTime = 350 }
      })
      await page.screenshot({ path: path.join(out, `${phase}-${state}-${width}-${theme}.png`) })
    }
    const faces = () => page.locator('.tt-listener:not(.leaving)')
    const setDraft = async (text) => { await page.locator('#draft').fill(text); await page.evaluate(() => { __listening.threads.st.mention = null; __listening.schedule() }); await settle() }
    const frame = async (state, animationTime = 120) => {
      await page.evaluate((time) => {
        for (const node of document.querySelectorAll('.tt-listener, .tt-listener-ripple, .tt-listener-look')) for (const anim of node.getAnimations()) { anim.pause(); anim.currentTime = node.classList.contains('tt-listener-look') ? 220 : node.classList.contains('tt-listener') && !node.classList.contains('leaving') ? 350 : time }
      }, animationTime)
      const r = await page.locator('.tt-composer').boundingBox()
      await page.screenshot({ path: path.join(out, `${phase}-${state}-${width}-${theme}.png`), clip: { x: Math.max(0, r.x), y: r.y - 58, width: Math.min(r.width, width - r.x), height: Math.min(r.height + 58, (width === 390 ? 844 : 1000) - r.y + 58) } })
    }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor(); await settle()
      // Draw all state and identity rules through the production row renderer.
      for (const group of ['priority', 'identity']) {
        await page.evaluate((group) => {
          const t = __listening.threads, seed = t.st.threads[0]
          const member = (persona, extra = {}) => ({ persona, engine: 'claude-code', working: false, busy: false, background: 0, ...extra })
          const row = (id, title, members, extra = {}) => ({ ...seed, id, title, members, default: members[0]?.persona ?? null, last: null, pinned: false, unread: 0, working: members.filter((m) => m.working).length, icon: null, ...extra })
          t.st.threads = group === 'priority' ? [
            row('question', 'Before the next step', [member('sprout', { working: true, asking: true })]),
            row('working', 'Finding a little clarity', [member('sprout', { working: true }), member('quill', { working: true })]),
            row('waiting', 'Checking the loose ends', [member('scout', { busy: true, background: 2 })]),
            row('mixed', 'A shared review', [member('sprout', { working: true }), member('quill', { background: 1 })]),
          ] : [
            row('solo', 'One clear next step', [member('sprout')]),
            row('group', 'A room for shared thoughts', [member('sprout'), member('quill')]),
            row('chosen', 'An icon of your own', [member('quill')], { icon: 'glyph:code' }),
            row('reply', 'A fresh perspective', [member('scout')], { unread: 1, last: { author: 'scout', text: 'The next step is ready whenever you are.', at: '2026-11-11T04:25:00Z' } }),
            row('quiet', 'Room for the unexpected', [], { default: null }),
          ]; __listening.schedule()
        }, group)
        await capture(`list-${group}`)
        if (phase === 'after') {
          const texts = await page.locator('.tt-row .srow-name').allTextContents()
          assert.deepEqual(texts, group === 'priority' ? ['Sprout is asking you', 'Sprout and Quill working', 'Scout waiting', 'Sprout working · Quill waiting'] : ['Quiet', 'Quiet', 'Quiet', 'Scout replied', 'Quiet'])
          assert.equal(await page.locator('.tt-row .badge, .tt-row .ring').count(), 0)
          assert.equal(await page.locator('.tt-row .tt-default-face').count(), 2)
          assert.ok(await page.locator('.tt-row .srow-name').evaluateAll((nodes) => nodes.every((n) => n.getBoundingClientRect().height < 24)))
          if (group === 'identity') {
            assert.equal(await page.getByTitle('One clear next step', { exact: true }).locator('.avatar-wrap > .blob').count(), 1)
            assert.equal(await page.getByTitle('A room for shared thoughts', { exact: true }).locator('.thread-tile').count(), 1)
            assert.equal(await page.getByTitle('An icon of your own', { exact: true }).locator('.thread-tile svg').count(), 1)
          }
        }
      }
      await page.evaluate((id) => __listening.threads.open(id), id)
      await page.locator('#draft').waitFor(); await page.waitForFunction(() => !__listening.threads.st.open.loading && !__listening.threads.st.open.refreshing); await settle()
      await capture('faces-empty')
      await setDraft('A little room to think')
      await capture('faces-rest', 0)
      if (phase === 'after') {
        assert.deepEqual(await faces().evaluateAll((nodes) => nodes.map((n) => n.dataset.persona)), ['sprout'])
        assert.equal(await page.locator('.tt-listening-label').count(), 0)
        const style = await faces().first().evaluate((n) => ({ border: getComputedStyle(n).borderWidth, shadow: getComputedStyle(n).boxShadow, bg: getComputedStyle(n).backgroundColor }))
        assert.deepEqual(style, { border: '0px', shadow: 'none', bg: 'rgba(0, 0, 0, 0)' })
        await settle(2100); assert.equal(await page.locator('.tt-listening-label').count(), 0)
        await page.keyboard.type(' today'); await settle(20)
        assert.equal(await page.locator('.tt-listening-label').count(), 0, 'typing alone does not repeat label')
      }
      // Same draft and caret at each edge. Sample the real CSS ripple over time.
      await setDraft('A quiet thought, with room to keep thinking about the next step.')
      await page.keyboard.press('Home'); await settle(300); await page.keyboard.type('H'); await settle(16)
      let leanLeft, leanRight
      if (phase === 'after') { leanLeft = await page.locator('.tt-listeners').evaluate((n) => parseFloat(n.style.getPropertyValue('--listener-lean'))); assert.ok(leanLeft < 0); assert.equal(await page.locator('.tt-listener-ripple').count(), 1) }
      await frame('typing-left-0', 0); await frame('typing-left-180', 180); await frame('typing-left-500', 500)
      await settle(700); await page.keyboard.press('End'); await page.keyboard.type('!'); await settle(16)
      if (phase === 'after') { leanRight = await page.locator('.tt-listeners').evaluate((n) => parseFloat(n.style.getPropertyValue('--listener-lean'))); assert.ok(leanRight > leanLeft) }
      await frame('typing-right-180', 180)
      await setDraft('@quill @scout Could you review this?')
      if (phase === 'after') {
        assert.deepEqual(await faces().evaluateAll((nodes) => nodes.map((n) => n.dataset.persona)), ['quill', 'scout'])
        assert.equal(await page.locator('.tt-listening-label').count(), 0)
        assert.equal(await page.locator('#draft .mention[data-face]').count(), 2)
      }
      await capture('faces-mentions', 0)
      await setDraft('')
      await page.evaluate(() => { (globalThis.__audio?.dictation?.state ?? __listening.state).rec = { mode: 'live', phase: 'recording', target: 'draft', started: Date.now(), elapsed: 0, levels: [0], said: '', guess: '' }; __listening.schedule() }); await settle()
      await capture('dictation-silent', 0)
      if (phase === 'after') assert.equal(await page.locator('.tt-listener-ripple').count(), 0)
      await page.evaluate(() => { (globalThis.__audio?.dictation?.state ?? __listening.state).rec.levels = [.8]; __listening.schedule() }); await settle(16)
      if (phase === 'after') { assert.deepEqual(await faces().evaluateAll((nodes) => nodes.map((n) => n.dataset.persona)), ['sprout']); assert.equal(await page.locator('.tt-listener-ripple').count(), 1); assert.equal(await page.locator('.tt-listener').first().evaluate((n) => n.style.getPropertyValue('--listener-strength')), '0.8') }
      await frame('dictation-voice-120', 120)
      await page.evaluate(() => { (globalThis.__audio?.dictation?.state ?? __listening.state).rec = null; __listening.schedule() }); await settle(700)
      await setDraft('Please keep the next step simple.')
      // Delay only the synthetic acceptance, preserving the real Send path.
      const route = app.routes[`POST /v1/threads/${id}/messages`]
      let release
      app.routes[`POST /v1/threads/${id}/messages`] = async (...args) => { await new Promise((r) => { release = r }); await route(...args) }
      await page.getByRole('button', { name: 'Send', exact: true }).click(); await settle(16)
      if (phase === 'after') { assert.equal(await faces().count(), 0); assert.equal(await page.locator('.tt-listeners.sending .tt-listener.leaving').count(), 1) }
      await frame('sending-0', 0); await frame('sending-120', 120); await frame('sending-240', 240)
      release(); await page.waitForFunction(() => !__listening.threads.st.open.sending); await settle(500)
      assert.ok(app.broker.seen.some((r) => r.path === `/v1/threads/${id}/messages` && JSON.parse(r.body).text === 'Please keep the next step simple.'))
      await page.emulateMedia({ reducedMotion: 'reduce' }); await setDraft('A thought without motion'); await settle(2400)
      if (phase === 'after') {
        assert.equal(await page.locator('.tt-listening-label').count(), 0)
        assert.equal(await page.locator('.tt-listener-ripple').count(), 0)
        assert.equal(await page.locator('.tt-listener-look').first().evaluate((n) => getComputedStyle(n).transform), 'none')
        await page.keyboard.type('!'); await settle(16); assert.equal(await page.locator('.tt-listener-ripple').count(), 0)
      }
      await capture('reduced-motion', 0)
      assert.deepEqual(errors, [])
      results.push({ width, theme, phase, errors, fixturesOnly: true, ...(phase === 'after' ? { subtitlePriority: true, identity: true, noRings: true, noLabel: true, recipients: true, leanLeft, leanRight, audioRipple: true, sendHandoff: true, reducedMotion: true } : {}) })
      console.log(`PASS ${phase} ${width} ${theme}`)
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify({ baseline, results }, null, 2) + '\n')
} finally { await browser.close() }
