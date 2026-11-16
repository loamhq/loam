// Actual local page/relay and animation timelines; synthetic records only.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'

const out = process.env.THREAD_STROKE_EVIDENCE
if (!out) throw new Error('Set THREAD_STROKE_EVIDENCE')
await mkdir(out, { recursive: true })
const baseline = process.env.THREAD_STROKE_BASE || '48b2592'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8'), results = []
try {
  for (const width of [1440, 390]) for (const theme of ['light', 'dark']) for (const phase of ['before', 'after']) {
    const app = await oneProductFixture(), errors = []
    for (const id of ['a-quieter-week', 'small-details']) await app.f.api('PATCH', `/threads/${id}`, { pinned: true })
    const { threads } = await app.f.api('GET', '/threads')
    const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
    app.routes['GET /v1/threads'] = (req, res) => json(res, { threads, agents: app.f.agents })
    app.routes['GET /v1/threads/events'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n') }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390 })
    if (phase === 'before') for (const file of ['threads.js', 'threads.css', 'thread-selection.js']) await context.route(`**/${file}`, (route) => route.fulfill({ contentType: file.endsWith('css') ? 'text/css' : 'text/javascript', body: execFileSync('git', ['show', `${baseline}:public/${file}`], { encoding: 'utf8' }) }))
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__selection = { threads, schedule };' }))
    const page = await context.newPage(); page.setDefaultTimeout(10000)
    page.on('pageerror', (e) => errors.push(e.message))
    page.on('console', (m) => { if (m.type() === 'error' && /Content Security Policy|Refused to/.test(m.text())) errors.push(m.text()) })
    // On a phone the list normally leaves while a thread is open. Exercise
    // retargeting of its return cue on the retained list at that actual width;
    // the existing selection suite separately covers real Back and expiry.
    const choose = async (id) => {
      await page.evaluate(({ id, narrow }) => { if (narrow) { __selection.threads.st.returning = id; __selection.schedule() } else __selection.threads.open(id) }, { id, narrow: width === 390 })
      await page.waitForFunction((id) => document.querySelector('.tt-row.on')?.__key === id, id)
    }
    const settle = () => page.waitForTimeout(470)
    const freeze = () => page.evaluate(() => {
      globalThis.__rule = document.querySelector('.tt-selection')
      globalThis.__effects = document.getAnimations().filter((a) => a.effect.target === __rule || a.effect.target.matches('.tt-row .srow-title'))
      __effects.forEach((a) => { a.pause(); a.currentTime = 0 })
    })
    const measure = () => page.evaluate((old) => {
      const rule = document.querySelector('.tt-selection'), rect = rule.getBoundingClientRect(), style = getComputedStyle(rule)
      // The former indicator was a row-sized wrapper. Measure its painted
      // pseudo-element for the counterfactual; the new rule IS its own box.
      const box = { left: rect.left, right: rect.right, top: old ? rect.bottom - 1.5 : rect.top, bottom: rect.bottom, width: rect.width, height: old ? 1.5 : rect.height }
      const overlaps = [...document.querySelectorAll('.tt-row .srow-title')].filter((title) => {
        const t = title.getBoundingClientRect()
        return box.width > .01 && Number(style.opacity) > .01 && box.left < t.right && box.right > t.left && box.top < t.bottom && box.bottom > t.top
      }).map((n) => n.textContent)
      return { box, opacity: style.opacity, overlaps, rules: document.querySelectorAll('.tt-selection').length,
        target: document.querySelector('.tt-row.on')?.__key, weight: getComputedStyle(document.querySelector('.tt-row.on .srow-title')).fontWeight }
    }, phase === 'before')
    const frame = async (ms) => { await page.evaluate((ms) => __effects.forEach((a) => { a.currentTime = ms }), ms); return { ms, ...await measure() } }
    const shot = (label) => page.screenshot({ path: path.join(out, `${phase}-${label}-${width}-${theme}.png`) })
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor(); await page.evaluate(() => document.fonts.ready); await settle()
      if (width === 390) await page.evaluate(() => __selection.threads.pause())
      assert.deepEqual(await page.locator('.tt-row').evaluateAll((rows) => rows.map((r) => r.__key)), ['general', 'a-quieter-week', 'small-details', 'weekend'])
      const runs = []
      for (const [label, id] of [['two-below', 'small-details'], ['pinned-recent', 'weekend']]) {
        await choose('general'); await settle(); await choose(id); await freeze()
        const samples = []
        for (let ms = 0; ms <= 420; ms += 4) samples.push(await frame(ms))
        if (phase === 'before') {
          assert.ok(samples.some((f) => f.overlaps.length), 'baseline reproduces the stroke crossing title text')
          await frame(140); await shot(`${label}-140`)
        } else {
          assert.ok(samples.every((f) => !f.overlaps.length), JSON.stringify(samples.filter((f) => f.overlaps.length)))
          assert.ok(samples.every((f) => f.rules === 1), 'there is always exactly one rule')
          const start = samples[0].box, end = samples.at(-1).box
          assert.ok(samples.every((f) => f.box.width <= .01 || Math.min(Math.abs(f.box.top - start.top), Math.abs(f.box.top - end.top)) < .1), 'a visible rule stays at its source or target')
          assert.ok(samples[20].box.width < start.width, 'old rule retracts')
          assert.ok(samples[35].box.width < .01, 'row changes at zero width')
          assert.ok(samples[50].box.width > .01 && samples[50].box.width < end.width, 'new rule draws from its start')
          assert.equal(samples.at(-1).weight, '650')
          for (const ms of [0, 80, 140, 200, 280, 420]) { await frame(ms); await shot(`${label}-${String(ms).padStart(3, '0')}`) }
        }
        await page.evaluate(() => __effects.forEach((a) => a.finish()))
        runs.push({ label, id, samples })
      }
      const interruptions = []
      if (phase === 'after') {
        // Interrupt both the retract and the draw; a third click supersedes
        // both earlier targets and starts at the currently visible width.
        for (const ms of [80, 240]) {
          await choose('general'); await settle(); await choose('weekend'); await freeze(); const before = await frame(ms)
          await choose('a-quieter-week'); await freeze(); const resumed = await frame(0)
          assert.ok(Math.abs(before.box.width - resumed.box.width) < .1)
          assert.ok(Math.abs(before.box.top - resumed.box.top) < .1)
          const samples = []
          for (let time = 0; time <= 420; time += 4) samples.push(await frame(time))
          assert.ok(samples.every((f) => !f.overlaps.length && f.rules === 1))
          assert.equal(samples.at(-1).target, 'a-quieter-week')
          await page.evaluate(() => __effects.forEach((a) => a.finish()))
          interruptions.push({ ms, before, resumed, samples })
        }
        // Sample actual playback every animation frame as well as dense,
        // deterministic samples of the browser's real effect timeline.
        await choose('general'); await settle(); await choose('weekend')
        const live = await page.evaluate(async () => {
          const samples = [], start = performance.now()
          await new Promise((resolve) => {
            const tick = () => {
              const rule = document.querySelector('.tt-selection'), r = rule.getBoundingClientRect()
              const collision = [...document.querySelectorAll('.tt-row .srow-title')].some((n) => { const t = n.getBoundingClientRect(); return r.width > .01 && r.left < t.right && r.right > t.left && r.top < t.bottom && r.bottom > t.top })
              samples.push({ ms: performance.now() - start, width: r.width, top: r.top, collision })
              if (performance.now() - start < 440) requestAnimationFrame(tick); else resolve()
            }; requestAnimationFrame(tick)
          })
          return samples
        })
        assert.ok(live.length > 8 && live.every((f) => !f.collision))
        runs.push({ label: 'live-playback', samples: live })
        await choose('general'); await settle(); await choose('small-details'); await freeze(); await frame(80)
        await page.emulateMedia({ reducedMotion: 'reduce' }); await page.waitForTimeout(30)
        assert.equal(await page.evaluate(() => document.getAnimations().filter((a) => a.effect.target === __rule).length), 0, 'reduced motion cancels an in-flight stroke')
        await choose('weekend'); await page.waitForTimeout(30)
        const reduced = await measure()
        assert.equal(reduced.target, 'weekend'); assert.deepEqual(reduced.overlaps, [])
        assert.equal(reduced.weight, '650')
        assert.equal(await page.evaluate(() => document.getAnimations().filter((a) => a.effect.target === __rule).length), 0)
        runs.push({ label: 'reduced-motion', ...reduced })
      }
      assert.deepEqual(errors, []); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      results.push({ width, theme, phase, narrowRetargetUsesReturnCue: width === 390, runs, interruptions, errors })
      console.log(`PASS ${phase} ${width} ${theme}: ${phase === 'before' ? 'baseline collision reproduced' : 'no title intersections, one rule, interruptions and reduced motion'}`)
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify({ baseline, browser: browser.version(), fixturesOnly: true, results }, null, 2) + '\n')
} finally { await browser.close() }
