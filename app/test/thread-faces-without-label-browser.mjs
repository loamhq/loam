// Real page and relay, synthetic records. Compare only against the prerequisite.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile, readdir } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
const out = process.env.THREAD_FACES_EVIDENCE
if (!out) throw new Error('Set THREAD_FACES_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const baseline = '77e4921', id = 'a-quieter-week', results = []
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
try {
  const design = await browser.newPage()
  await design.goto(new URL('../design/Loam App.dc.html', import.meta.url).href)
  await design.screenshot({ path: path.join(process.env.TMPDIR, 'faces-design-reference.png') }); await design.close()
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) for (const phase of ['before', 'after']) {
    const app = await oneProductFixture(), errors = [], checks = {}
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390 })
    if (phase === 'before') for (const file of ['threads.js', 'threads.css', 'thread-identity.js']) await context.route(`**/${file}`, (route) => route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: execFileSync('git', ['show', `${baseline}:public/${file}`], { encoding: 'utf8' }) }))
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__faces = { threads, state, schedule };' }))
    const page = await context.newPage(); page.setDefaultTimeout(10000); page.on('pageerror', (e) => errors.push(e.message))
    await page.clock.install({ time: new Date('2026-11-11T06:00:00Z') })
    const settle = async (ms = 350) => { await page.clock.runFor(ms); await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame) }) }
    const recipients = () => page.locator('.tt-listener:not(.leaving)').evaluateAll((nodes) => nodes.map((n) => n.dataset.persona))
    const noLabel = async () => {
      assert.equal(await page.locator('.tt-listening-label').count(), 0)
      assert.doesNotMatch(await page.getByRole('group', { name: 'Message recipients' }).innerText(), /listening/i)
    }
    const fill = async (value) => { await page.locator('#draft').fill(value); await page.keyboard.press('Escape'); await settle() }
    const capture = async (name) => {
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      await page.screenshot({ path: path.join(out, `${phase}-${name}-${width}-${theme}.png`) })
    }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      await page.evaluate((id) => __faces.threads.open(id), id)
      await page.waitForFunction(() => !__faces.threads.st.open.loading && !__faces.threads.st.open.refreshing)
      await page.locator('#draft').fill('@'); await settle(); assert.match(await page.locator('.tt-mentions').innerText(), /everyone in this thread/)
      await fill('@all A thought for everyone in this thread.')
      assert.deepEqual(await recipients(), ['sprout', 'quill', 'scout'])
      await page.keyboard.press('End'); await settle(300); await page.keyboard.type('!'); await settle(16)
      assert.equal(await page.locator('.tt-listener-ripple').count(), 3)
      await page.evaluate(() => {
        for (const node of document.querySelectorAll('.tt-listener, .tt-listener-ripple, .tt-listener-look')) for (const anim of node.getAnimations()) { anim.pause(); anim.currentTime = node.classList.contains('tt-listener-ripple') ? 180 : node.classList.contains('tt-listener-look') ? 220 : 350 }
      })
      if (phase === 'after') await noLabel()
      else assert.equal(await page.locator('.tt-listening-label').innerText(), 'listening')
      checks.typing = await page.locator('.tt-listener').evaluateAll((nodes) => nodes.map((n) => {
        const ripple = n.querySelector('.tt-listener-ripple'), look = n.querySelector('.tt-listener-look'), r = n.getBoundingClientRect(), style = getComputedStyle(n)
        return { persona: n.dataset.persona, x: r.x, y: r.y, width: r.width, height: r.height, animation: style.animationName, opacity: style.opacity, ripple: getComputedStyle(ripple).animationName, lean: getComputedStyle(look).transform }
      }))
      await capture('typing')
      await settle(10000); assert.deepEqual(await recipients(), ['sprout', 'quill', 'scout'])
      await app.f.api('PATCH', `/threads/${id}`, { default: null })
      await page.waitForFunction(() => __faces.threads.st.open.thread.default === null)
      await fill('@al A thought for everyone.')
      app.emit(id, { type: 'message.delta', persona: 'sprout', draft: 'A reply while a draft stays open.' })
      await page.getByText('A reply while a draft stays open.', { exact: true }).waitFor(); await settle()
      assert.deepEqual(await recipients(), ['sprout', 'quill', 'scout'])
      checks.pauseAndMentionRetention = true
      await fill(''); await settle(300); assert.deepEqual(await recipients(), [])
      await page.emulateMedia({ reducedMotion: 'reduce' })
      await fill('@all Faces without motion.'); await settle(2400)
      await page.keyboard.type('!'); await settle(350)
      assert.deepEqual(await recipients(), ['sprout', 'quill', 'scout'])
      assert.equal(await page.locator('.tt-listener-ripple').count(), 0)
      if (phase === 'after') await noLabel()
      else assert.equal(await page.locator('.tt-listening-label').innerText(), 'listening')
      checks.reduced = await page.locator('.tt-listener').evaluateAll((nodes) => nodes.map((n) => ({ persona: n.dataset.persona, opacity: getComputedStyle(n).opacity, lean: getComputedStyle(n.querySelector('.tt-listener-look')).transform, moving: n.getAnimations({ subtree: true }).some((a) => a.playState === 'running') })))
      for (const face of checks.reduced) { assert.equal(face.opacity, '1'); assert.equal(face.lean, 'none'); assert.equal(face.moving, false) }
      await capture('reduced-motion')
      assert.deepEqual(errors, [])
      if (phase === 'after') {
        const before = results.find((r) => r.width === width && r.theme === theme && r.phase === 'before')
        assert.deepEqual(checks, before.checks, 'face geometry, ripple, lean and lifetime match the prerequisite')
      }
      results.push({ width, theme, phase, checks, errors })
      console.log(`PASS ${phase} ${width} ${theme}: typing, @all, pause/mention/SSE retention, reduced motion${phase === 'after' ? ', no label; faces match baseline' : ''}`)
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify({ baseline, results }, null, 2) + '\n')
  const captures = []
  for (const file of (await readdir(out)).filter((f) => f.endsWith('.png')).sort()) captures.push({ file, sha256: createHash('sha256').update(await readFile(path.join(out, file))).digest('hex') })
  await writeFile(path.join(out, 'captures.json'), JSON.stringify(captures, null, 2) + '\n')
} finally { await browser.close() }
