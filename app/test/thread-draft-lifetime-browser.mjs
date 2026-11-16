// Real page, HTTP relay and SSE; synthetic three-member broker only.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile, readdir } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
const out = process.env.THREAD_DRAFT_EVIDENCE
if (!out) throw new Error('Set THREAD_DRAFT_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const baseline = 'dd39a86', id = 'a-quieter-week', results = []
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
const old = (file) => execFileSync('git', ['show', `${baseline}:public/${file}`], { encoding: 'utf8' })
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) for (const phase of ['before', 'after']) {
    const app = await oneProductFixture(), errors = [], checks = {}
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390 })
    if (phase === 'before') for (const file of ['threads.js', 'threads.css', 'thread-experience.js']) await context.route(`**/${file}`, (route) => route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: old(file) }))
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__lifetime = { threads, state, schedule, draft };' }))
    const page = await context.newPage(); page.setDefaultTimeout(10000); page.on('pageerror', (e) => errors.push(e.message))
    await page.clock.install({ time: new Date('2026-11-11T05:40:00Z') })
    const settle = async (ms = 350) => { await page.waitForTimeout(ms); await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame) }) }
    const capture = async (state) => {
      await settle()
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      await page.screenshot({ path: path.join(out, `${phase}-${state}-${width}-${theme}.png`), animations: 'disabled' })
    }
    const faces = () => page.locator('.tt-listener:not(.leaving)')
    const recipients = () => faces().evaluateAll((nodes) => nodes.map((n) => n.dataset.persona))
    const fill = async (value) => { await page.locator('#draft').fill(value); await page.keyboard.press('Escape'); await settle() }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      const unread = page.locator('.tt-row').filter({ has: page.locator('.tt-unread') }).first()
      const placement = await unread.evaluate((row) => {
        const dot = row.querySelector('.tt-unread'), time = row.querySelector('.srow-time'), avatar = row.querySelector('.avatar-wrap')
        const d = dot.getBoundingClientRect(), t = time.getBoundingClientRect()
        return { timeOwnsDot: time.contains(dot), avatarOwnsDot: avatar.contains(dot), dot: d.toJSON(), time: t.toJSON(), avatarDecoration: getComputedStyle(avatar, '::after').content }
      })
      if (phase === 'after') {
        assert.equal(placement.timeOwnsDot, true); assert.equal(placement.avatarOwnsDot, false)
        assert.equal(placement.dot.width, 6); assert.equal(placement.dot.height, 6)
        assert.ok(Math.abs(placement.dot.y + 3 - (placement.time.y + placement.time.height / 2)) < 1)
        assert.equal(await unread.locator('.tt-unread').getAttribute('aria-label'), 'Unread')
      } else assert.equal(placement.avatarOwnsDot, true)
      checks.placement = placement
      await capture('unread')
      await page.evaluate((id) => __lifetime.threads.open(id), id)
      await page.waitForFunction(() => !__lifetime.threads.st.open.loading && !__lifetime.threads.st.open.refreshing)
      await page.locator('#draft').click()
      await page.locator('#draft').pressSequentially('A thought that needs a little time', { delay: 30 })
      await settle()
      assert.deepEqual(await recipients(), ['sprout'])
      await page.evaluate(() => { globalThis.__heldDraft = document.querySelector('#draft'); globalThis.__heldFace = document.querySelector('.tt-listener') })
      const started = Date.now(); await page.waitForTimeout(10000)
      checks.pauseElapsedMs = Date.now() - started
      assert.deepEqual(await recipients(), ['sprout'])
      assert.equal(await page.locator('.tt-listening-label').count(), 0, 'no listening label remains')
      assert.equal(await faces().first().evaluate((n) => getComputedStyle(n).opacity), '1')
      await capture('pause-10s')
      await page.locator('#draft').blur(); await settle(); assert.deepEqual(await recipients(), ['sprout'])
      await page.locator('#draft').focus(); await settle(); assert.deepEqual(await recipients(), ['sprout'])
      app.emit(id, { type: 'turn.started', persona: 'sprout', engine: 'claude-code', started_at: '2026-11-11T05:40:00Z' })
      app.emit(id, { type: 'message.delta', persona: 'sprout', draft: 'A live reply arrived while the next thought was being written.' })
      await page.getByText('A live reply arrived while the next thought was being written.', { exact: true }).waitFor()
      assert.deepEqual(await recipients(), ['sprout']); await capture('stream-mid-draft')
      const members = (await app.f.api('GET', `/threads/${id}`)).thread.members.map((m) => ({ ...m, working: m.persona === 'sprout', background: m.persona === 'quill' ? 1 : 0 }))
      app.emit(id, { type: 'members', members })
      await page.waitForFunction(() => __lifetime.threads.st.open.thread.members.find((m) => m.persona === 'quill').background === 1)
      assert.deepEqual(await recipients(), ['sprout'])
      assert.equal(await page.evaluate(() => __heldDraft === document.querySelector('#draft') && __heldFace === document.querySelector('.tt-listener')), true)
      checks.pauseBlurFocusStreamMembers = true
      await page.locator('#draft').fill('@'); await settle()
      const everyone = page.locator('.tt-mentions [role="option"]').filter({ hasText: 'Everyone' })
      assert.match(await everyone.innerText(), phase === 'after' ? /everyone in this thread/ : /every agent in this thread/)
      await everyone.click(); await page.keyboard.type('A thought for everyone.'); await settle()
      assert.equal(await page.locator('#draft').evaluate((n) => n.value), '@all A thought for everyone.')
      assert.deepEqual(await recipients(), ['sprout', 'quill', 'scout'])
      checks.all = await recipients(); await capture('all-three')
      // Reproduce the disappearing faces: no default, edit a resolved mention.
      await app.f.api('PATCH', `/threads/${id}`, { default: null })
      await page.waitForFunction(() => __lifetime.threads.st.open.thread.default === null)
      await fill('@all A thought for everyone.')
      await page.locator('#draft').press('Home')
      for (let i = 0; i < 3; i++) await page.keyboard.press('ArrowRight')
      await page.keyboard.press('Delete'); await page.keyboard.press('Escape'); await settle(700)
      assert.equal(await page.locator('#draft').evaluate((n) => n.value), '@al A thought for everyone.')
      const expected = phase === 'after' ? ['sprout', 'quill', 'scout'] : []
      assert.deepEqual(await recipients(), expected)
      app.emit(id, { type: 'message.delta', persona: 'sprout', draft: 'The live reply continues during the mention edit.' })
      app.emit(id, { type: 'members', members })
      await page.getByText('The live reply continues during the mention edit.', { exact: true }).waitFor(); await settle(700)
      assert.deepEqual(await recipients(), expected)
      checks.unresolvedMention = await recipients(); await capture('mention-edit')
      if (width === 390) {
        // Android resizes-content keyboard geometry, not a physical IME test.
        await page.setViewportSize({ width, height: 508 }); await page.locator('#draft').focus(); await settle()
        assert.deepEqual(await recipients(), expected)
        const geometry = await page.evaluate(() => {
          const faces = [...document.querySelectorAll('.tt-listener:not(.leaving)')].map((n) => n.getBoundingClientRect().toJSON())
          return { viewport: innerHeight, composer: document.querySelector('.composer-box').getBoundingClientRect().toJSON(), faces }
        })
        if (phase === 'after') for (const face of geometry.faces) { assert.ok(face.y >= 0); assert.ok(face.bottom <= geometry.composer.top) }
        checks.keyboardResize = geometry; await capture('keyboard-resize')
        await page.setViewportSize({ width, height: 844 }); await settle()
      }
      if (phase === 'after') {
        await fill('@quill A different recipient.'); assert.deepEqual(await recipients(), ['quill'])
        await fill(''); await settle(); assert.deepEqual(await recipients(), [])
        await fill('@al A fresh draft'); assert.deepEqual(await recipients(), [])
        await fill('@all Send this exactly.')
        await page.getByRole('button', { name: 'Send', exact: true }).click(); await settle(700)
        assert.equal(await page.locator('#draft').evaluate((n) => n.value), ''); assert.deepEqual(await recipients(), [])
        assert.ok(app.broker.seen.some((r) => r.path === `/v1/threads/${id}/messages` && JSON.parse(r.body).text === '@all Send this exactly.'))
        await fill('@all Keep while here')
        if (width === 390) await page.getByRole('button', { name: 'Back to threads', exact: true }).click()
        else await page.evaluate(() => { __lifetime.threads.close(); __lifetime.schedule() })
        await settle(); assert.equal(await faces().count(), 0)
        checks.clearReplaceSendLeave = true
      }
      assert.deepEqual(errors, [])
      results.push({ phase, width, theme, checks, errors })
      console.log(`PASS ${phase} ${width} ${theme}: 10s, blur/focus, SSE, members, @all; mention-edit faces=${expected.length}`)
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify({ baseline, results }, null, 2) + '\n')
  const captures = []
  for (const file of (await readdir(out)).filter((f) => f.endsWith('.png')).sort()) captures.push({ file, sha256: createHash('sha256').update(await readFile(path.join(out, file))).digest('hex') })
  await writeFile(path.join(out, 'captures.json'), JSON.stringify(captures, null, 2) + '\n')
} finally { await browser.close() }
