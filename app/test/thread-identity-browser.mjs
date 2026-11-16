import { observeAudio } from './audio-probe.mjs'
// Real page/relay, synthetic broker. Baseline assets come from the named commit.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile, readdir } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
const out = process.env.THREAD_IDENTITY_EVIDENCE
if (!out) throw new Error('Set THREAD_IDENTITY_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'] })
const baseline = '9e0d66e', id = 'a-quieter-week', results = []
const old = (file) => execFileSync('git', ['show', `${baseline}:public/${file}`], { encoding: 'utf8' })
const appSource = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
try {
  // Inspect the supplied clickable design as part of the same browser run.
  const design = await browser.newPage()
  await design.goto(new URL('../design/Loam App.dc.html', import.meta.url).href)
  await design.screenshot({ path: path.join(process.env.TMPDIR, 'design-reference.png') }); await design.close()
  for (const width of [1440, 390].filter((v) => !process.env.IDENTITY_WIDTH || +process.env.IDENTITY_WIDTH === v)) for (const theme of ['light', 'dark'].filter((v) => !process.env.IDENTITY_THEME || process.env.IDENTITY_THEME === v)) for (const phase of ['before', 'after'].filter((v) => !process.env.IDENTITY_PHASE || process.env.IDENTITY_PHASE === v)) {
    const app = await oneProductFixture(), errors = []
    await app.f.api('PATCH', `/threads/${id}`, { add: ['ledger', 'pip'] })
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, timezoneId: 'America/Los_Angeles', isMobile: width === 390, hasTouch: width === 390 })
    if (process.env.LOAM_EMOJI) {
      const dir = process.env.LOAM_EMOJI
      const css = (await readFile(path.join(dir, 'index.css'), 'utf8')).replaceAll('./files/', '/fixture-emoji/').replaceAll('Noto Color Emoji', 'FixtureEmoji').replaceAll('Noto Emoji', 'FixtureEmoji')
      await context.route('**/fixture-emoji/*', async (route) => route.fulfill({ contentType: 'font/woff2', body: await readFile(path.join(dir, 'files', new URL(route.request().url()).pathname.split('/').at(-1))) }))
      await context.route('**/fonts/fonts.css', async (route) => route.fulfill({ contentType: 'text/css', body: await readFile(new URL('../public/fonts/fonts.css', import.meta.url), 'utf8') + '\n' + css + '\n.tt-icon-choice,.thread-tile { font-family:Figtree,FixtureEmoji,sans-serif; }' }))
    }
    if (phase === 'before') for (const file of ['threads.js', 'threads.css', 'compose.js']) await context.route(`**/${file}`, (route) => route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: old(file) }))
    await observeAudio(context)
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: (phase === 'before' ? old('app.js') : appSource) + '\nglobalThis.__identity = { threads, state, schedule, draft };' }))
    const page = await context.newPage(); page.setDefaultTimeout(10000); page.on('pageerror', (e) => errors.push(e.message))
    await page.clock.install({ time: new Date('2026-11-11T02:35:00Z') })
    const settle = async (ms = 400) => { await page.clock.runFor(ms); await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) }) }
    const capture = async (state) => { await settle(); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); await page.screenshot({ path: path.join(out, `${phase}-${state}-${width}-${theme}.png`), animations: 'disabled' }) }
    const setDraft = async (value) => { await page.locator('#draft').fill(value); await page.evaluate(() => { __identity.threads.st.mention = null; __identity.schedule() }); await settle() }
    const faces = () => page.locator('.tt-listener:not(.leaving)')
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      await page.evaluate((id) => __identity.threads.open(id), id)
      await page.waitForFunction(() => !__identity.threads.st.open.loading && !__identity.threads.st.open.refreshing)
      await page.locator('.tt-prose-content:not(.tt-prose-plain)').first().waitFor(); await settle()
      await capture('no-tasks')
      if (phase === 'after') {
        assert.equal(await page.locator('.tt-head .blob, .tt-avatar-stack').count(), 0)
        assert.equal(await page.locator('.tt-head .tt-tasks-button').count(), 0)
        assert.equal(await page.locator('.tt-head').innerText().then((s) => s.includes('Sprout')), false)
        assert.equal(await page.locator('#draft').getAttribute('data-placeholder'), 'Message Sprout')
        assert.equal(await page.locator('.tt-row .thread-tile').count(), width === 1440 ? 4 : 0)
      }
      await page.evaluate(() => {
        const o = __identity.threads.st.open
        o.thread.members = o.thread.members.map((m, i) => ({ ...m, working: false, background: i < 2 ? 1 : 0, background_tasks: i < 2 ? [{ id: `task-${i}`, description: i ? 'Review the next steps and return a short recommendation' : 'Check the current version and reply labels', started_at: '2026-11-11T02:33:00Z' }] : [] }))
        __identity.schedule()
      }); await page.getByRole('button', { name: '2 background tasks', exact: true }).waitFor(); await capture('two-tasks')
      await page.getByRole('button', { name: '2 background tasks', exact: true }).click(); await capture('tasks-open')
      if (phase === 'after') { assert.equal(await page.locator('.tt-task-group').count(), 2); assert.equal(await page.locator('.tt-task-row').count(), 2) }
      await page.keyboard.press('Escape'); await page.locator('.tt-tasks-panel').waitFor({ state: 'hidden' })
      await page.getByTitle('Thread details', { exact: true }).click(); await capture('icon-picker')
      if (phase === 'after') {
        await page.getByRole('button', { name: 'Use Conversation icon', exact: true }).click()
        await page.waitForFunction(() => document.querySelector('[aria-label="Use Conversation icon"]')?.getAttribute('aria-pressed') === 'true' && !__identity.threads.st.open.patching)
        assert.equal((await app.f.api('GET', `/threads/${id}`)).thread.icon, 'glyph:chat')
        await capture('icon-chosen')
        await page.reload(); await page.locator('.tt-head .thread-tile svg').waitFor(); await page.waitForFunction(() => !__identity.threads.st.open.loading && !__identity.threads.st.open.refreshing)
        await page.getByTitle('Thread details', { exact: true }).click()
        assert.equal(await page.getByRole('button', { name: 'Use Conversation icon', exact: true }).getAttribute('aria-pressed'), 'true')
        await page.getByRole('button', { name: 'Default icon', exact: true }).click()
        await page.waitForFunction(() => !__identity.threads.st.open.patching && __identity.threads.st.open.thread.icon === null)
      }
      await page.getByRole('button', { name: 'Chat', exact: true }).click()
      await settle()
      await page.evaluate(() => { for (const row of __identity.threads.st.threads) { if (row.id === 'small-details') { row.working = 1; row.unread = 1 } }; __identity.schedule() })
      if (width === 390) { await page.getByRole('button', { name: 'Back to threads', exact: true }).click(); await capture('thread-list'); await page.evaluate((id) => __identity.threads.open(id), id); await page.locator('#draft').waitFor(); await page.waitForFunction(() => !__identity.threads.st.open.refreshing) }
      else { await capture('thread-list'); if (phase === 'after') assert.equal(await page.locator('.tt-row.on').evaluate((n) => getComputedStyle(n).getPropertyValue('--agent-color').trim() === getComputedStyle(n).getPropertyValue('--latte-espresso').trim()), true) }
      const composer = page.locator('.composer-box')
      await setDraft('A thought for the week')
      if (phase === 'after') assert.deepEqual(await faces().evaluateAll((nodes) => nodes.map((n) => n.dataset.persona)), ['sprout'])
      await capture('listening-default')
      if (phase === 'after') {
        const face = faces().first()
        if (width === 1440) { await face.hover(); assert.equal(await face.evaluate((n) => getComputedStyle(n, '::after').opacity), '1'); await page.mouse.move(0, 0) }
        else {
          const rect = await face.boundingBox(), cdp = await context.newCDPSession(page)
          await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: rect.x + 15, y: rect.y + 15 }] })
          await page.clock.runFor(500)
          assert.equal(await face.evaluate((n) => getComputedStyle(n, '::after').opacity), '1')
          await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }); await cdp.detach()
        }
      }
      await setDraft('Could @quill and @scout find a little room for this?')
      if (phase === 'after') {
        assert.deepEqual(await faces().evaluateAll((nodes) => nodes.map((n) => n.dataset.persona)), ['quill', 'scout'])
        assert.equal(await page.locator('#draft .mention[data-face]').count(), 2)
        assert.equal(await page.locator('#draft').evaluate((el) => el.value), 'Could @quill and @scout find a little room for this?')
        const geometry = await page.evaluate(() => {
          const composer = document.querySelector('.composer-box'), stream = document.querySelector('.tt-stream'), listeners = document.querySelector('.tt-listeners')
          const before = [composer.getBoundingClientRect().y, stream.scrollHeight, stream.scrollTop]
          listeners.style.display = 'none'
          const hidden = [composer.getBoundingClientRect().y, stream.scrollHeight, stream.scrollTop]
          listeners.style.display = ''
          return { before, hidden }
        })
        assert.deepEqual(geometry.before, geometry.hidden)
      }
      await capture('listening-two')
      // Picker insertion followed by rapid typing must preserve the exact text/caret.
      await page.locator('#draft').fill('@qui'); await page.locator('.tt-mentions [role="option"]').filter({ hasText: 'Quill' }).click()
      await page.keyboard.type('please check'); assert.equal(await page.locator('#draft').evaluate((el) => el.value), '@quill please check')
      if (phase === 'after') assert.equal(await page.locator('#draft .mention').getAttribute('data-face'), 'Q')
      await setDraft('@all Let’s keep this simple.')
      if (phase === 'after') { assert.equal(await faces().count(), 5); assert.equal(await page.locator('.tt-listener-more').innerText(), '+1') }
      await capture('listening-all')
      await setDraft(''); await page.evaluate(() => { (globalThis.__audio?.dictation?.state ?? __identity.state).rec = { mode: 'live', phase: 'recording', target: 'draft', started: Date.now(), elapsed: 0, said: '', guess: '' }; __identity.schedule() }); await settle()
      if (phase === 'after') assert.deepEqual(await faces().evaluateAll((nodes) => nodes.map((n) => n.dataset.persona)), ['sprout'])
      await capture('listening-dictation')
      await page.evaluate(() => { (globalThis.__audio?.dictation?.state ?? __identity.state).rec = null; __identity.schedule() }); await settle()
      if (phase === 'after') {
        assert.equal(await faces().count(), 0)
        // Actual editable token makes the trip through the synthetic runtime once.
        await setDraft('@quill Please check the wording.')
        await page.getByRole('button', { name: 'Send', exact: true }).click()
        await page.locator('.tt-message.mine .mention[data-face]').filter({ hasText: '@quill' }).last().waitFor()
        await capture('sent-mention')
        assert.ok(app.broker.seen.some((r) => r.path === `/v1/threads/${id}/messages` && JSON.parse(r.body).text === '@quill Please check the wording.'))
        // Reduced motion: no infinite animation, only a fade, at both widths/themes.
        await page.emulateMedia({ reducedMotion: 'reduce' }); await setDraft('A quiet thought')
        assert.equal(await faces().locator('.blob').first().evaluate((n) => getComputedStyle(n).animationName), 'none')
        assert.equal(await faces().first().evaluate((n) => getComputedStyle(n).animationName), 'tt-listener-fade')
        await capture('reduced-motion')
        await page.emulateMedia({ reducedMotion: 'no-preference' }); await setDraft('')
        // Frame sequence: sample the actual arrival/leave animations at known times.
        const frame = async (name, at) => {
          await page.evaluate((at) => { for (const el of document.querySelectorAll('.tt-listener')) for (const animation of el.getAnimations()) { animation.pause(); animation.currentTime = at } }, at)
          const r = await page.locator('.tt-composer').boundingBox()
          await page.screenshot({ path: path.join(out, `frame-${name}-${width}-${theme}.png`), clip: { x: Math.max(0, r.x), y: r.y - 55, width: Math.min(r.width, width - r.x), height: Math.min(r.height + 55, (width === 390 ? 844 : 1000) - r.y + 55) } })
        }
        await page.locator('#draft').fill('Hello'); await page.evaluate(() => __identity.schedule()); await page.clock.runFor(16)
        await frame('arrive-0', 0); await frame('arrive-140', 140); await frame('arrive-280', 280)
        await page.locator('#draft').fill(''); await page.evaluate(() => __identity.schedule()); await page.clock.runFor(16)
        await frame('leave-0', 0); await frame('leave-120', 120); await frame('leave-240', 240); await settle()
      }
      assert.deepEqual(errors, [])
      results.push({ width, theme, phase, errors, fixturesOnly: true, ...(phase === 'after' ? { header: true, groupedTasks: true, persistedGlyphAfterReload: true, recipients: true, inlineCaretAndPayload: true, reducedMotion: true, stableComposer: true } : {}) })
      console.log(`PASS ${phase} ${width} ${theme}`)
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify({ baseline, results }, null, 2) + '\n')
  const captures = []
  for (const file of (await readdir(out)).filter((f) => f.endsWith('.png')).sort()) captures.push({ file, sha256: createHash('sha256').update(await readFile(path.join(out, file))).digest('hex') })
  await writeFile(path.join(out, 'captures.json'), JSON.stringify(captures, null, 2) + '\n')
} finally { await browser.close() }
