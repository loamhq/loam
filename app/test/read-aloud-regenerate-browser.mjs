// Real page/plugin; isolated speech responses, no host messages or vendor calls.
import assert from 'node:assert/strict'
import { readFile, readdir, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
import { observeAudio } from './audio-probe.mjs'
import { contrast } from '../tools/theme-contrast.mjs'
import { shippedPlugins } from '../src/shipped-plugins.js'
import { filesHost } from './files-host.js'
import { row, readAnswer } from './fixtures/files-contract.js'
const out = process.env.READ_ALOUD_REGENERATE_EVIDENCE
if (!out) throw new Error('Set READ_ALOUD_REGENERATE_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const source = await readFile('public/app.js', 'utf8'), reports = []
const response = 'Keep a little room for the week. Gather the loose ends before adding something new. '.repeat(20).trim()
const native = process.argv.includes('--native')
const themes = ['latte', 'plain', 'yosemite', 'mist-trail', 'deep-forest', 'autumn', 'foundry']
const selected = process.argv.find(arg => arg.startsWith('--themes='))?.slice('--themes='.length).split(',')
if (selected) assert.ok(selected.length && selected.every(id => themes.includes(id)), 'Unknown theme selection')
const looks = native ? ['latte'] : selected ?? (process.argv.includes('--core-themes') ? ['latte', 'plain'] : themes)
const wav = Buffer.alloc(44 + 24000 * 2 * 60)
wav.write('RIFF'); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8); wav.writeUInt32LE(16, 16)
wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22); wav.writeUInt32LE(24000, 24); wav.writeUInt32LE(48000, 28)
wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write('data', 36); wav.writeUInt32LE(wav.length - 44, 40)
try {
  for (const look of looks) {
    for (const width of [390, 1440]) for (const mode of native ? ['light'] : ['light', 'dark']) {
      const app = await oneProductFixture(), settings = { theme: look, mode, size: 100 }
      for (const [key, route] of Object.entries(filesHost().routes)) if (key.includes('/v1/files/')) app.routes[key] = route
      app.routes['GET /v1/files/read'] = (req, res, url) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(readAnswer(row('vault', url.searchParams.get('path')), response, 'sha256:fixture'))) }
      const plugins = await shippedPlugins(path.resolve('public'))
      for (const p of plugins) if (p.id === 'read-aloud') p.on = true
      app.routes['GET /v1/plugins'] = (req, res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ plugins })) }
      for (const file of await readdir('../plugins/_listed/read-aloud/page')) app.routes[`GET /v1/plugins/read-aloud/files/page/${file}`] = async (req, res) => {
        res.writeHead(200, { 'content-type': file.endsWith('.css') ? 'text/css' : 'text/javascript' }); res.end(await readFile(`../plugins/_listed/read-aloud/page/${file}`))
      }
      app.routes['GET /v1/settings/appearance'] = (req, res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ phone: settings, desktop: settings })) }
      app.routes['GET /v1/threads/a-quieter-week'] = async (req, res) => {
        const record = await app.f.api('GET', '/threads/a-quieter-week')
        record.messages = Array.from({ length: 10 }, (_, i) => ({ id: `question-${i}`, author: 'person', kind: 'message', text: 'Leave a little room for the week.' }))
        record.messages.push({ id: 'audio-reply', author: 'sprout', kind: 'message', text: response })
        res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(record))
      }
      const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode, isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce', serviceWorkers: 'block' })
      await observeAudio(context)
      await context.route('**/app.js', r => r.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__regen = { threads, state, schedule, goChats, goFiles, get speech() { return __audio["read-aloud"] } };' }))
      await context.route('**/api/health', r => r.fulfill({ json: { configured: true, ok: true, speech: true } }))
      await context.route('**/x/read-aloud/status', r => r.fulfill({ json: { speech: true } }))
      let serial = 0, pending = true, failure = false
      const posts = [], jobs = new Map(), known = new Map()
      const key = b => JSON.stringify([b.source, b.minutes, b.voice, b.style, b.mode, b.text])
      const ready = id => ({ id, status: 'ready', duration_s: 179, url: `/x/read-aloud/speech/${id}/audio.mp3` })
      await context.route(/\/x\/read-aloud\/speech(?:\/.*)?$/, async route => {
        const req = route.request(), u = new URL(req.url())
        if (u.pathname.endsWith('/audio.mp3')) return route.fulfill({ contentType: 'audio/wav', body: wav })
        if (u.pathname.endsWith('/lookup')) {
          const b = req.postDataJSON(), k = key(b)
          if (!known.has(k) && b.mode === 'verbatim') known.set(k, ready('original'))
          return route.fulfill({ json: known.get(k) ?? { status: 'none' } })
        }
        if (u.pathname === '/x/read-aloud/speech') {
          const b = req.postDataJSON(); posts.push(b)
          const id = `replacement-${++serial}`; jobs.set(id, b)
          return route.fulfill({ status: 202, json: { id, status: 'preparing', stage: 'voicing' } })
        }
        const id = u.pathname.split('/')[3]
        if (pending) return route.fulfill({ json: { id, status: 'preparing', stage: 'voicing' } })
        if (failure) return route.fulfill({ json: { id, status: 'failed', error: 'Fixture voice service refused this attempt.' } })
        const b = jobs.get(id), answer = ready(id)
        if (b) known.set(key(b), answer)
        return route.fulfill({ json: answer })
      })
      if (native) await context.addInitScript(() => {
        globalThis.__players = []
        const NativeAudio = Audio
        globalThis.Audio = class extends NativeAudio {
          constructor() { super(); this.plays = 0; __players.push(this) }
          play() { this.plays++; return super.play() }
        }
      })
      else await context.addInitScript(() => {
        globalThis.__players = []
        globalThis.Audio = class {
          constructor() { Object.assign(this, { paused: true, ended: false, currentTime: 0, duration: 179, buffered: { length: 0 }, events: new Map(), plays: 0 }); __players.push(this) }
          addEventListener(k, f) { if (!this.events.has(k)) this.events.set(k, []); this.events.get(k).push(f) }
          emit(k) { for (const f of this.events.get(k) ?? []) f() }
          play() { this.plays++; this.paused = false; this.emit('play'); return Promise.resolve() }
          pause() { this.paused = true; this.emit('pause') }
          load() {} removeAttribute() {}
        }
      })
      const page = await context.newPage(), errors = []
      page.setDefaultTimeout(12000); page.on('pageerror', e => errors.push(e.message))
      const hit = node => width === 390 ? node.tap() : node.click()
      const shot = async state => { if (state === 'ready' || ['latte', 'plain'].includes(look)) await page.screenshot({ path: path.join(out, `${state}-${look}-${width}-${mode}.png`), animations: 'disabled' }) }
      const openMenu = async () => { await page.locator('.speakbtn').last().scrollIntoViewIfNeeded(); await page.locator('.speakbtn').last().click(); await page.locator('.speakmenu').waitFor() }
      try {
        await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
        await page.evaluate(() => __regen.threads.open('a-quieter-week'))
        await page.locator('.speakbtn.ready').waitFor(); await openMenu()
        const menu = page.locator('.speakmenu'), choices = menu.locator('.speech-choice')
        assert.equal(await choices.count(), 2)
        assert.match(await choices.first().innerText(), /Ready to play · 2:59/)
        assert.match(await choices.last().innerText(), /to make/)
        assert.equal(await choices.last().locator('.speech-regenerate').count(), 0)
        assert.equal(await menu.locator('button button').count(), 0)
        const paint = await choices.first().evaluate(n => {
          const s = getComputedStyle(n), b = n.querySelector('button'), t = n.querySelector('.setrow-sub'), r = n.querySelector('.speech-regenerate'), rect = r.getBoundingClientRect()
          const menu = n.closest('.speakmenu'), unmade = menu.querySelector('.speech-choice:not(.available)'), tile = n.querySelector('.icon-tile')
          const canvas = document.createElement('canvas'), c = canvas.getContext('2d')
          const rgb = value => { c.fillStyle = value; c.fillRect(0, 0, 1, 1); return '#' + [...c.getImageData(0, 0, 1, 1).data].slice(0, 3).map(v => v.toString(16).padStart(2, '0')).join('') }
          const iconPaint = el => { const s = getComputedStyle(el); return { background: rgb(s.backgroundColor), ink: rgb(s.color) } }
          return { background: rgb(getComputedStyle(menu).backgroundColor), rowBackground: s.backgroundColor, rowBorder: s.borderWidth, rowShadow: s.boxShadow,
            unmadeBackground: getComputedStyle(unmade).backgroundColor, unmadeBorder: getComputedStyle(unmade).borderWidth,
            readyIcon: iconPaint(tile), unmadeIcon: iconPaint(unmade.querySelector('.icon-tile')),
            action: { background: rgb(s.getPropertyValue('--latte-foam')), ink: rgb(s.getPropertyValue('--latte-foam-ink')) },
            neutral: { background: rgb(s.getPropertyValue('--paper-card')), ink: rgb(s.getPropertyValue('--paper-ink-2')) },
            ink: rgb(getComputedStyle(t).color), regenInk: rgb(getComputedStyle(r).color), width: rect.width, height: rect.height, mainHeight: b.getBoundingClientRect().height, mainRight: b.getBoundingClientRect().right, regenLeft: rect.left }
        })
        assert.equal(paint.rowBackground, 'rgba(0, 0, 0, 0)', 'ready row must use the menu surface')
        assert.equal(paint.unmadeBackground, paint.rowBackground)
        assert.equal(paint.rowBorder, '0px'); assert.equal(paint.unmadeBorder, '0px'); assert.equal(paint.rowShadow, 'none')
        assert.deepEqual(paint.readyIcon, paint.action, 'ready icon uses this theme’s action pair')
        assert.deepEqual(paint.unmadeIcon, paint.neutral, 'unmade icon remains Newspaper')
        assert.notDeepEqual(paint.readyIcon, paint.unmadeIcon)
        assert.ok(contrast(paint.readyIcon.ink, paint.readyIcon.background) >= 3, JSON.stringify(paint))
        assert.ok(contrast(paint.ink, paint.background) >= 4.5, JSON.stringify(paint))
        assert.ok(contrast(paint.regenInk, paint.background) >= 4.5)
        assert.ok(paint.width >= 44 && paint.height >= 44 && paint.mainHeight >= 44)
        assert.ok(paint.mainRight <= paint.regenLeft + 1, 'separate targets overlap')
        await page.evaluate(() => document.fonts.ready); await shot('ready')
        // The keyboard reaches the independent regeneration action.
        await choices.first().locator('.setrow').focus(); await page.keyboard.press('ArrowDown')
        assert.equal(await page.getByRole('menuitem', { name: 'Regenerate Listen audio', exact: true }).evaluate(n => n === document.activeElement), true)
        await page.keyboard.press('Enter')
        await page.waitForFunction(() => document.querySelector('.speech-regenerate')?.getAttribute('aria-disabled') === 'true')
        await page.keyboard.press('Enter'); await page.waitForTimeout(80)
        assert.equal(posts.length, 1); assert.equal(posts[0].force, true); assert.equal(posts[0].text, response)
        assert.equal(posts[0].mode, 'verbatim'); assert.equal(posts[0].agent, 'sprout')
        assert.equal(await page.evaluate(() => __players.length), 0, 'regeneration played audio')
        await shot('regenerating')
        failure = true; pending = false
        await menu.getByRole('status').filter({ hasText: 'Could not regenerate' }).waitFor()
        const noteInk = await menu.getByRole('status').evaluate(n => {
          const c = document.createElement('canvas').getContext('2d'); c.fillStyle = getComputedStyle(n).color; c.fillRect(0, 0, 1, 1)
          return '#' + [...c.getImageData(0, 0, 1, 1).data].slice(0, 3).map(v => v.toString(16).padStart(2, '0')).join('')
        })
        assert.ok(contrast(noteInk, paint.background) >= 4.5)
        await shot('failed')
        assert.match(await choices.first().innerText(), /Current audio is still available/)
        await choices.first().locator('.setrow').click(); await page.locator('.player.playing').waitFor()
        if (native) await page.waitForFunction(() => __players.at(-1)?.currentTime > 0.2 && !__players.at(-1).paused)
        const old = await page.evaluate(native => { const a = __players.at(-1); if (!native) a.currentTime = 37; a.emit?.('timeupdate'); globalThis.__oldPlayer = a; globalThis.__oldRow = document.querySelector('.player'); return { count: __players.length, src: a.src, position: a.currentTime } }, native)
        await openMenu(); pending = true; failure = false
        await hit(page.getByRole('menuitem', { name: 'Regenerate Listen audio', exact: true }))
        assert.equal(posts.length, 2)
        await page.waitForFunction(() => document.querySelector('.speech-regenerate')?.getAttribute('aria-disabled') === 'true')
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
        const scroll = await page.locator('.msgs').evaluate(n => n.scrollTop)
        await page.evaluate(() => { for (let i = 0; i < 20; i++) __regen.schedule() })
        assert.equal(await page.locator('.msgs').evaluate(n => n.scrollTop), scroll)
        pending = false
        await menu.getByRole('status').filter({ hasText: 'New audio ready' }).waitFor()
        assert.equal(await choices.first().locator('.icon-tile').evaluate(n => getComputedStyle(n).backgroundColor), await choices.first().evaluate(n => {
          const probe = document.createElement('span'); probe.style.backgroundColor = getComputedStyle(n).getPropertyValue('--latte-foam'); return probe.style.backgroundColor
        }), 'playing take retains its ready icon treatment')
        const continuity = await page.evaluate(() => ({ same: __oldPlayer === __players.at(-1), row: __oldRow === document.querySelector('.player'), position: __oldPlayer.currentTime, playing: !__oldPlayer.paused, count: __players.length, src: __oldPlayer.src }))
        assert.ok(continuity.position >= old.position && continuity.position < old.position + 10, JSON.stringify({ continuity, old }))
        assert.deepEqual({ ...continuity, position: 37 }, { same: true, row: true, position: 37, playing: true, count: old.count, src: old.src })
        // The other variant prepares normally, then gets its own Regenerate.
        await choices.last().locator('.setrow').click(); await page.locator('.player.brief .player-play').waitFor()
        assert.equal(posts.at(-1).mode, 'brief'); assert.equal(posts.at(-1).force, undefined)
        await openMenu(); await hit(page.getByRole('menuitem', { name: 'Regenerate Brief audio', exact: true }))
        await menu.getByRole('status').filter({ hasText: 'New audio ready to play' }).waitFor()
        assert.equal(posts.at(-1).mode, 'brief'); assert.equal(posts.at(-1).force, true)
        await page.keyboard.press('Escape'); await page.locator('.speakmenu').waitFor({ state: 'hidden' })
        assert.equal(await page.evaluate(() => __oldPlayer.paused), false)
        // Files' current Full/Brief stop shares the force request lifecycle.
        await page.evaluate(() => __regen.goFiles('vault', 'Example.md', { isFile: true }))
        await page.locator('.fs-speak').waitFor(); await page.locator('.fs-speak').click()
        await page.locator('.file-speech-regenerate').waitFor(); await shot('files-full')
        assert.match(await page.locator('.file-speech-regenerate').getAttribute('aria-label'), /^Regenerate Full/)
        await hit(page.locator('.file-speech-regenerate'))
        await page.locator('.file-speech .speech-regeneration-note').filter({ hasText: 'New audio ready' }).waitFor()
        assert.equal(posts.at(-1).source, 'document'); assert.equal(posts.at(-1).minutes, 'full'); assert.equal(posts.at(-1).force, true)
        assert.equal(posts.at(-1).voice, 'Aoede')
        assert.deepEqual(errors, [])
        assert.equal(app.broker.seen.filter(r => r.method === 'POST' && r.path.endsWith('/messages')).length, 0)
        reports.push({ look, width, mode, native, paint: { ...paint, noteInk }, continuity, posts: posts.map(({ text, ...b }) => ({ ...b, words: text.split(/\s+/).length })), errors })
        console.log(`PASS ${look} ${width} ${mode}: plain rows, themed ready/neutral unmade icons, independent targets, contrast, force, duplicate guard, failure, continuity, Brief, Files`)
      } catch (error) {
        console.error({ look, width, mode, native, errors, url: page.url(), body: (await page.locator('body').innerText()).slice(0, 600) })
        await shot('unexpected-failure')
        throw error
      } finally { await context.close(); await app.close() }
    }
  }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify(reports, null, 2) + '\n')
} finally { await browser.close() }
