// Paired token-only captures of the real page; host records/audio are synthetic.
import assert from 'node:assert/strict'
import { readFile, writeFile, mkdir } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import path from 'node:path'
import { oneProductFixture } from '../test/one-product-fixture.mjs'
import { contrast, resolvedTheme } from './theme-contrast.mjs'

const out = process.env.LATTE_LIGHT_EVIDENCE
if (!out) throw new Error('Set LATTE_LIGHT_EVIDENCE')
await mkdir(out, { recursive: true })
const baseline = process.env.LATTE_LIGHT_BASELINE || 'fcd34741fa29832cd8a213616d8008181a19c95c'
const oldCSS = execFileSync('git', ['show', `${baseline}:public/themes/latte/theme.css`], { encoding: 'utf8' })
const newCSS = await readFile('public/themes/latte/theme.css', 'utf8')
// Neither shared pigments (including avatars), dark overrides nor reading
// colours may change. Compare their source, independent of the screenshots.
const withoutLight = css => css.replace(/:root\[data-look="latte"\]\[data-theme="light"\]\s*\{[^}]*\}/, '')
assert.equal(withoutLight(newCSS), withoutLight(oldCSS))
const paper = css => css.match(/--paper-[^;]+;/g)
assert.deepEqual(paper(newCSS), paper(oldCSS))
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const source = await readFile('public/app.js', 'utf8'), results = [], captures = new Map()
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
const at = '2026-11-12T20:45:00Z', id = 'a-quieter-week'
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) for (const phase of ['before', 'after']) {
    const app = await oneProductFixture(), errors = []
    const record = await app.f.api('GET', `/threads/${id}`)
    app.routes[`GET /v1/threads/${id}`] = (req, res) => json(res, { ...record, messages: [
      { id: 'question', author: 'you', kind: 'message', at, text: 'Let’s leave a little room for the week.' },
      { id: 'answer', author: 'sprout', kind: 'message', at, text: 'Keep one clear place for the next step, leave a little space between commitments, and gather the loose ends before adding something new.\n\nWe can choose a slower morning and bring the notes together then. There is room to think, review the useful details, and decide what matters most together.' },
    ], has_more: false, turn: null, turns: [] })
    const options = { 'agent-shapes': 'latte-art', 'latte-mug': 'on', 'latte-inside': 'pour' }
    const settings = { theme: 'latte', mode, size: 100, options: { latte: options } }
    app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, { phone: settings, desktop: settings })
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 },
      colorScheme: mode, isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce', timezoneId: 'UTC' })
    await context.route('**/themes.css*', async route => {
      const response = await route.fetch(), body = await response.text()
      await route.fulfill({ response, body: phase === 'before' ? body.replace(newCSS, oldCSS) : body })
    })
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__latteLight={threads,goChats,draft,growDraft,schedule};' }))
    await context.route('**/api/health', route => route.fulfill({ json: { configured: true, ok: true, speech: true } }))
    await context.route('**/x/read-aloud/status', r => r.fulfill({ json: { speech: true } }))
    await context.route(/\/x\/read-aloud\/speech(?:\/.*)?$/, route => route.fulfill({ json: route.request().url().endsWith('/lookup') ? { status: 'none' } : { id: 'a'.repeat(32), status: 'ready', duration_s: 90, url: '/x/read-aloud/speech/' + 'a'.repeat(32) + '/audio.mp3' } }))
    await context.addInitScript(() => {
      globalThis.Audio = class {
        constructor() { Object.assign(this, { paused: true, ended: false, duration: 90, currentTime: 30, buffered: { length: 0 }, listeners: new Map() }) }
        addEventListener(k, fn) { if (!this.listeners.has(k)) this.listeners.set(k, []); this.listeners.get(k).push(fn) }
        play() { this.paused = false; for (const fn of this.listeners.get('play') || []) fn(); return Promise.resolve() }
        pause() { this.paused = true; for (const fn of this.listeners.get('pause') || []) fn() }
        load() {}
        removeAttribute() {}
      }
    })
    const page = await context.newPage(); page.setDefaultTimeout(12000)
    await page.clock.setFixedTime(new Date(at))
    page.on('pageerror', e => errors.push(e.message))
    const shot = async surface => {
      await page.mouse.move(0, 0)
      await page.evaluate(async () => { document.activeElement?.blur(); await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
      // Let retained-node hover/focus transitions settle before comparing paint.
      await page.waitForTimeout(300)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      const file = `${phase}-${surface}-${width}-${mode}.png`
      const buffer = await page.screenshot({ path: path.join(out, file), animations: 'disabled' })
      const hash = createHash('sha256').update(buffer).digest('hex')
      captures.set(file, hash)
      return { file, sha256: hash }
    }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      await page.evaluate(id => __latteLight.threads.open(id), id)
      await page.locator('[data-message-id="answer"] .speakbtn').click()
      await page.locator('.speakmenu [role=menuitem]').filter({ hasText: 'Listen' }).click()
      await page.locator('.player.ready').waitFor()
      await page.locator('.player-play').click()
      await page.evaluate(() => { __latteLight.draft.value = 'That sounds good. Let’s keep Thursday open.'; __latteLight.growDraft(); __latteLight.schedule() })
      await page.locator('.roundsend:not(:disabled)').waitFor()
      const thread = await shot('thread')
      const send = await page.locator('.roundsend').evaluate(n => ({ fill: getComputedStyle(n).backgroundColor, ink: getComputedStyle(n).color }))
      if (mode === 'light' && phase === 'before') assert.equal(send.fill, 'rgb(47, 32, 23)', 'baseline theme was served')
      const marks = await page.locator('.tt-footer-action,.speakbtn').evaluateAll(nodes => nodes.map(n => getComputedStyle(n).backgroundColor))
      assert.ok(marks.every(c => c === 'rgba(0, 0, 0, 0)'))
      await page.evaluate(() => __latteLight.goChats())
      await page.locator('.playerbar').waitFor()
      const chats = await shot('chats')
      const controls = await page.locator('.playerbar-play,.playerbar-fill,.playerbar-rate,.navbtn,.roundbtn,.topbar .iconbtn').evaluateAll(nodes => nodes.map(n => ({ class: n.className, fill: getComputedStyle(n).backgroundColor, ink: getComputedStyle(n).color })))
      if (mode === 'light' && phase === 'after') {
        assert.equal(send.fill, 'rgb(145, 104, 68)')
        assert.equal(send.ink, 'rgb(255, 248, 238)')
        assert.ok(controls.some(c => c.class.includes('playerbar-fill') && c.fill === send.fill))
      }
      assert.deepEqual(errors, [])
      results.push({ width, mode, phase, thread, chats, send, controls, bareMarks: true, overflow: false, errors })
      console.log(`PASS ${phase} ${width} ${mode}: thread/player, active Send, bare marks, chats/read-aloud bar; no overflow or page errors`)
    } finally { await context.close(); await app.close() }
  }
  for (const width of [390, 1440]) for (const surface of ['thread', 'chats']) {
    assert.equal(captures.get(`before-${surface}-${width}-dark.png`), captures.get(`after-${surface}-${width}-dark.png`), `${width} ${surface}: dark pixels changed`)
  }
  const tokens = await resolvedTheme('latte', 'light'), ratios = {}
  for (const fill of ['latte-espresso', 'latte-espresso-hover', 'latte-espresso-pressed', 'latte-foam', 'latte-track', 'latte-hover']) {
    ratios[fill] = { hex: tokens[fill], ink: tokens[`${fill}-ink`], label: contrast(tokens[fill], tokens[`${fill}-ink`]), page: contrast(tokens[fill], tokens['paper-page']) }
    assert.ok(ratios[fill].label >= 4.5)
  }
  assert.ok(ratios['latte-foam'].page >= 1.25)
  await writeFile(path.join(out, 'browser.json'), JSON.stringify({ baseline, darkPixelsIdentical: true, sharedAndDarkSourceIdentical: true, paperSourceIdentical: true, ratios, results }, null, 2) + '\n')
  console.log('PASS dark screenshots are byte-identical at both widths; shared/dark/paper tokens unchanged; light labels >=4.5:1 and foam/page >=1.25:1')
} finally { await browser.close() }
