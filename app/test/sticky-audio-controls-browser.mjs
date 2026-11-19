// Real app/plugin and native Audio; synthetic records and local WAV, no provider.
import assert from 'node:assert/strict'
import { readFile, readdir, mkdir, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
import { observeAudio } from './audio-probe.mjs'
import { shippedPlugins } from '../src/shipped-plugins.js'
import { contrast } from '../tools/theme-contrast.mjs'

const out = process.env.STICKY_AUDIO_EVIDENCE
if (!out) throw new Error('Set STICKY_AUDIO_EVIDENCE')
await mkdir(out, { recursive: true })
const before = process.argv.includes('--before')
const available = (await readdir('public/themes', { withFileTypes: true })).filter(e => e.isDirectory()).map(e => e.name)
const selected = process.argv.find(arg => arg.startsWith('--themes='))?.slice(9).split(',')
const looks = selected ?? (before ? ['latte'] : available)
assert.ok(looks.every(id => available.includes(id)))
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const source = await readFile('public/app.js', 'utf8'), reports = []
const response = 'Keep room for the week. Gather the loose ends before adding something new. '.repeat(24)
const wav = Buffer.alloc(44 + 24000 * 2 * 120)
wav.write('RIFF'); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8); wav.writeUInt32LE(16, 16)
wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22); wav.writeUInt32LE(24000, 24); wav.writeUInt32LE(48000, 28)
wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write('data', 36); wav.writeUInt32LE(wav.length - 44, 40)
try {
  for (const look of looks) for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const app = await oneProductFixture(), settings = { theme: look, mode, size: 100 }
    await app.f.api('PATCH', '/threads/a-quieter-week', { title: 'A little room for the week and a deliberately long title that should give way to the listening controls' })
    app.routes['GET /v1/settings/appearance'] = (req, res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ phone: settings, desktop: settings })) }
    app.routes['GET /v1/threads/a-quieter-week'] = async (req, res) => {
      const record = await app.f.api('GET', '/threads/a-quieter-week')
      record.thread.title = 'A little room for the week and a deliberately long title that should give way to the listening controls'
      record.messages = Array.from({ length: 24 }, (_, i) => ({ id: `question-${i}`, author: 'person', kind: 'message', text: 'Leave room for the week.' }))
      record.messages.push({ id: 'audio-reply', author: 'sprout', kind: 'message', text: response })
      res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(record))
    }
    const plugins = await shippedPlugins(path.resolve('public'))
    for (const p of plugins) if (p.id === 'read-aloud') p.on = true
    app.routes['GET /v1/plugins'] = (req, res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ plugins })) }
    for (const file of await readdir('../plugins/_listed/read-aloud/page')) app.routes[`GET /v1/plugins/read-aloud/files/page/${file}`] = async (req, res) => {
      res.writeHead(200, { 'content-type': file.endsWith('.css') ? 'text/css' : 'text/javascript' })
      res.end(before && file === 'style.css' ? execFileSync('git', ['show', '6a816704:public/plugins/read-aloud/page/style.css']) : await readFile(`../plugins/_listed/read-aloud/page/${file}`))
    }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode, isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce', serviceWorkers: 'block' })
    await observeAudio(context)
    if (before) await context.route('**/button-tiers.css', r => r.fulfill({ contentType: 'text/css', body: execFileSync('git', ['show', '6a816704:public/button-tiers.css'], { encoding: 'utf8' }) }))
    if (before) await context.route('**/style.css', r => new URL(r.request().url()).pathname === '/style.css'
      ? r.fulfill({ contentType: 'text/css', body: execFileSync('git', ['show', '6a816704:public/style.css'], { encoding: 'utf8' }) }) : r.fallback())
    await context.route('**/app.js', r => r.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__sticky = { threads, panels, schedule, goChats };' }))
    await context.route('**/api/health', r => r.fulfill({ json: { configured: true, ok: true, speech: true } }))
    await context.route('**/x/read-aloud/status', r => r.fulfill({ json: { speech: true } }))
    let made = false
    const posts = [], ready = { id: 'fixture-audio', status: 'ready', duration_s: 120, url: '/x/read-aloud/speech/fixture-audio/audio.mp3' }
    await context.route(/\/x\/read-aloud\/speech(?:\/.*)?$/, route => {
      const req = route.request(), pathname = new URL(req.url()).pathname
      if (pathname.endsWith('/audio.mp3')) return route.fulfill({ contentType: 'audio/wav', body: wav })
      if (pathname.endsWith('/lookup')) return route.fulfill({ json: made && req.postDataJSON().mode === 'verbatim' ? ready : { status: 'none' } })
      assert.equal(pathname, '/x/read-aloud/speech')
      posts.push(req.postDataJSON()); made = true
      return route.fulfill({ status: 202, json: ready })
    })
    await context.addInitScript(() => {
      globalThis.__players = []
      const NativeAudio = Audio
      globalThis.Audio = class extends NativeAudio { constructor() { super(); __players.push(this) } }
    })
    const page = await context.newPage(), errors = [], states = []
    page.setDefaultTimeout(12000); page.on('pageerror', e => errors.push(e.message))
    const hit = node => width === 390 ? node.tap() : node.click()
    const shot = async state => { await page.evaluate(() => document.fonts.ready); await page.screenshot({ path: path.join(out, `${before ? 'before-' : ''}${state}-${look}-${width}-${mode}.png`), animations: 'disabled' }) }
    const paint = async () => page.locator('.playerbar').evaluate(n => {
      const s = getComputedStyle(n), c = document.createElement('canvas').getContext('2d')
      const rgb = value => { c.fillStyle = value; c.fillRect(0, 0, 1, 1); return '#' + [...c.getImageData(0, 0, 1, 1).data].slice(0, 3).map(v => v.toString(16).padStart(2, '0')).join('') }
      const control = selector => {
        const el = n.querySelector(selector)
        if (!el) return null
        const s = getComputedStyle(el)
        return { background: s.backgroundColor, ink: rgb(s.color), radius: s.borderRadius, rect: el.getBoundingClientRect().toJSON() }
      }
      const title = n.querySelector('.playerbar-title'), text = n.querySelector('.playerbar-text')
      return { look: document.documentElement.dataset.look, mode: document.documentElement.dataset.theme,
        background: s.backgroundColor, shadow: s.boxShadow, rect: n.getBoundingClientRect().toJSON(),
        surface: rgb(s.getPropertyValue('--paper-card')), surfaceCSS: s.getPropertyValue('--paper-card').trim(), ink: rgb(s.getPropertyValue('--paper-card-ink')),
        play: control('.playerbar-play'), speed: control('.playerbar-rate'), close: control('.playerbar-close'),
        title: { width: title.clientWidth, full: title.scrollWidth, overflow: getComputedStyle(title).textOverflow }, text: text.getBoundingClientRect().toJSON(),
        overflow: document.documentElement.scrollWidth > innerWidth, parent: n.closest('[data-panel]')?.getAttribute('data-panel') }
    })
    const check = async state => {
      await page.mouse.move(0, 0)
      const p = await paint(); states.push({ state, ...p })
      assert.equal(p.look, look); assert.equal(p.mode, mode); assert.equal(p.overflow, false)
      if (before) return p
      const normalized = await page.locator('.playerbar').evaluate(n => { const probe = document.createElement('span'); probe.style.backgroundColor = getComputedStyle(n).getPropertyValue('--paper-card'); return probe.style.backgroundColor })
      assert.equal(p.play.background, normalized); assert.equal(p.play.ink, p.ink); assert.equal(p.play.radius, '999px')
      assert.ok(contrast(p.play.ink, p.surface) >= 3)
      assert.equal(p.close.background, 'rgba(0, 0, 0, 0)'); assert.ok(p.close.rect.width >= 44 && p.close.rect.height >= 44)
      assert.equal(p.shadow, 'none'); assert.ok(p.play.rect.width >= 44 && p.play.rect.height >= 44)
      if (p.speed) {
        assert.equal(p.speed.background, normalized); assert.equal(p.speed.ink, p.ink); assert.equal(p.speed.radius, '999px')
        assert.ok(contrast(p.speed.ink, p.surface) >= 4.5)
        assert.ok(p.speed.rect.width >= 44 && p.speed.rect.height >= (width === 390 ? 44 : 32))
        assert.ok(p.text.right <= p.speed.rect.left + 1); assert.ok(p.speed.rect.right <= p.close.rect.left)
      }
      assert.ok(p.play.rect.right <= p.text.left)
      assert.ok(p.title.width > 0 && p.title.overflow === 'ellipsis', 'title must yield to controls')
      if (width === 390 || state.startsWith('focused-')) assert.ok(p.title.full > p.title.width, 'narrow panes truncate the title')
      return p
    }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      await page.evaluate(() => __sticky.threads.open('a-quieter-week'))
      const glyph = page.locator('.speakbtn').last()
      await glyph.waitFor(); await glyph.scrollIntoViewIfNeeded(); await hit(glyph)
      await hit(page.locator('.speakmenu .setrow').first()); await page.locator('.player.ready').waitFor()
      assert.equal(await page.evaluate(() => __players.length), 0)
      // The previous job's icon-only menu is carried through on the real page.
      await hit(glyph); await page.locator('.speech-choice.available').waitFor()
      const readyIcon = await page.locator('.speech-choice.available').evaluate(n => ({ background: getComputedStyle(n).backgroundColor, border: getComputedStyle(n).borderWidth, icon: getComputedStyle(n.querySelector('.icon-tile')).backgroundColor }))
      assert.equal(readyIcon.background, 'rgba(0, 0, 0, 0)'); assert.equal(readyIcon.border, '0px'); assert.notEqual(readyIcon.icon, readyIcon.background)
      await page.keyboard.press('Escape'); await page.locator('.speakmenu').waitFor({ state: 'hidden' })
      await page.locator('.msgs').evaluate(n => { n.scrollTop = 0; n.dispatchEvent(new Event('scroll')) })
      await page.locator('.playerbar.ready').waitFor(); const initial = await check('ready')
      if (look === 'latte') await shot('ready')
      await hit(page.locator('.playerbar-play'))
      await page.locator('.playerbar.playing').waitFor(); await page.waitForFunction(() => __players.at(-1)?.currentTime > .1 && !__players.at(-1).paused)
      await check('playing'); await shot('playing')
      if (before) { reports.push({ look, width, mode, before, states, errors }); continue }
      await page.evaluate(() => { globalThis.__oldAudio = __players.at(-1); globalThis.__oldBar = document.querySelector('.playerbar') })
      const top = (await paint()).rect.top
      await page.locator('.msgs').evaluate(n => { n.scrollTop = 80; n.dispatchEvent(new Event('scroll')) })
      assert.equal((await paint()).rect.top, top, 'sticky bar must not scroll with the messages')
      await hit(page.locator('.playerbar-play')); await page.getByRole('button', { name: 'Play', exact: true }).filter({ has: page.locator('svg') }).first().waitFor()
      await page.waitForFunction(() => __oldAudio.paused)
      await check('paused'); if (look === 'latte') await shot('paused')
      await hit(page.locator('.playerbar-rate')); assert.equal(await page.locator('.playerbar-rate').innerText(), '1.5×')
      assert.equal(await page.evaluate(() => __oldAudio.playbackRate), 1.5)
      await hit(page.locator('.playerbar-play')); await page.waitForFunction(() => !__oldAudio.paused)
      // Every button is keyboard-reachable and visibly focused.
      for (const selector of ['.playerbar-play', '.playerbar-rate', '.playerbar-close']) {
        await page.locator(selector).focus(); await page.keyboard.press('Tab'); await page.keyboard.press('Shift+Tab')
        assert.equal(await page.locator(selector).evaluate(n => n === document.activeElement && getComputedStyle(n).outlineStyle === 'solid'), true)
      }
      await page.locator('.playerbar-rate').focus(); await page.keyboard.press('Enter')
      assert.equal(await page.evaluate(() => __oldAudio.playbackRate), 2)
      if (width === 1440) {
        await page.evaluate(() => __sticky.panels.openSide({ kind: 'thread', id: 'general' }))
        await page.locator('[data-panel="side"] .tt-thread').waitFor()
        for (const focused of ['side', 'main']) {
          await page.evaluate(focused => __sticky.panels.focus(focused), focused)
          await page.waitForFunction(focused => document.querySelector('.panel-pair').dataset.focusedPanel === focused, focused)
          await check(`focused-${focused}`); await shot(`focused-${focused}`)
        }
        for (const selector of ['.playerbar-play', '.playerbar-rate', '.playerbar-close']) {
          await page.locator(selector).hover()
          assert.notEqual(await page.locator(selector).evaluate(n => getComputedStyle(n).backgroundColor), 'rgba(0, 0, 0, 0)')
        }
      }
      await page.mouse.move(0, 0)
      const stable = await page.evaluate(() => ({ audio: __oldAudio === __players.at(-1), bar: __oldBar === document.querySelector('.playerbar'), playing: !__oldAudio.paused, position: __oldAudio.currentTime, count: __players.length }))
      assert.ok(stable.audio && stable.bar && stable.playing && stable.position > .1 && stable.count === 1)
      await hit(page.locator('.playerbar-close')); await page.locator('.playerbar').waitFor({ state: 'detached' })
      assert.equal(await page.evaluate(() => __oldAudio.paused && !__oldAudio.getAttribute('src')), true)
      assert.equal(posts.length, 1); assert.equal(posts[0].force, undefined)
      assert.equal(app.broker.seen.filter(r => r.method === 'POST' && r.path.endsWith('/messages')).length, 0)
      assert.deepEqual(errors, [])
      reports.push({ look, width, mode, before, states, readyIcon, stable, initialTop: initial.rect.top, posts: posts.length, errors })
      await writeFile(path.join(out, 'browser.json'), JSON.stringify(reports, null, 2) + '\n')
      console.log(`PASS ${look} ${width} ${mode}: ready/playing/paused, neutral circle/pill, quiet close, targets, title, sticky, native audio, speed, keyboard, panel focus, close, ready icon retained`)
    } catch (error) {
      await shot('unexpected-failure'); console.error({ look, width, mode, before, errors }); throw error
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify(reports, null, 2) + '\n')
} finally { await browser.close() }
