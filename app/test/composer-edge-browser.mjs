// Real page and native Audio; synthetic conversation, silent MP3 and delayed upload.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile, readdir } from 'node:fs/promises'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { oneProductFixture } from './one-product-fixture.mjs'
import { shippedPlugins } from '../src/shipped-plugins.js'
import { observeAudio } from './audio-probe.mjs'

const out = process.env.COMPOSER_EDGE_EVIDENCE
if (!out) throw new Error('Set COMPOSER_EDGE_EVIDENCE')
await mkdir(out, { recursive: true })
const engine = process.env.EDGE_ENGINE || 'chromium'
const playback = engine === 'chromium' || process.env.EDGE_PLAYBACK === '1'
const playwright = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await playwright[engine].launch({ headless: true, args: engine === 'chromium' ? ['--no-sandbox', '--autoplay-policy=no-user-gesture-required'] : [] })
// Some headless WebKit builds accept backdrop-filter but never paint it.
// Measure a standalone reference before attributing that limitation to the app.
const sharp = (await import(process.env.LOAM_SHARP || 'sharp')).default
const probe = await browser.newPage({ viewport: { width: 200, height: 200 } })
await probe.setContent('<style>body{margin:0;background:repeating-linear-gradient(45deg,red 0 5px,blue 5px 10px)}div{position:absolute;inset:20px;backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px)}</style><div></div>')
const withFilter = await sharp(await probe.screenshot()).raw().toBuffer()
await probe.locator('div').evaluate(n => { n.style.backdropFilter = 'none'; n.style.webkitBackdropFilter = 'none' })
const withoutFilter = await sharp(await probe.screenshot()).raw().toBuffer()
const referenceBlurChannels = withFilter.reduce((sum, value, i) => sum + (Math.abs(value - withoutFilter[i]) > 2), 0)
await probe.close()
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
const id = 'a-quieter-week', results = []
// Generated silence: ffmpeg -f lavfi -i anullsrc=r=22050:cl=mono -t 120 -c:a libmp3lame -q:a 9 silent-take.mp3
// Native playback and the production MIME type, with no vendor call.
const take = await readFile(new URL('./fixtures/silent-take.mp3', import.meta.url))
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=', 'base64')
try {
  for (const width of [390, 1440].filter(w => !process.env.EDGE_WIDTH || w === +process.env.EDGE_WIDTH)) for (const theme of ['light', 'dark'].filter(t => !process.env.EDGE_THEME || t === process.env.EDGE_THEME)) {
    const app = await oneProductFixture(), errors = [], captures = [], checks = []
    const plugins = await shippedPlugins(path.resolve('public'))
    app.routes['GET /v1/plugins'] = (req, res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ plugins })) }
    for (const plugin of ['dictation', 'read-aloud']) for (const file of await readdir(`public/plugins/${plugin}/page`)) {
      app.routes[`GET /v1/plugins/${plugin}/files/page/${file}`] = async (req, res) => {
        res.writeHead(200, { 'content-type': file.endsWith('.css') ? 'text/css' : 'text/javascript', 'cache-control': 'no-store' })
        res.end(await readFile(`public/plugins/${plugin}/page/${file}`))
      }
    }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, serviceWorkers: 'block', isMobile: width === 390, hasTouch: width === 390 })
    await observeAudio(context)
    await context.addInitScript(() => {
      globalThis.SpeechRecognition ??= class {}
      const NativeAudio = globalThis.Audio
      globalThis.__nativeAudio = []
      globalThis.Audio = new Proxy(NativeAudio, { construct(Target, args) { const audio = new Target(...args); __nativeAudio.push(audio); return audio } })
    })
    await context.route('**/app.js', r => r.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__edge = { threads, schedule, pluginLoader };' }))
    if (process.env.EDGE_BASELINE) for (const file of ['threads.css']) await context.route(`**/${file}`, r => r.fulfill({ contentType: 'text/css', body: execFileSync('git', ['show', `${process.env.EDGE_BASELINE}:public/${file}`]) }))
    await context.route('**/api/health', r => r.fulfill({ json: { configured: true, ok: true, speech: true } }))
    await context.route(/\/api\/speech(?:\/.*)?$/, r => {
      const url = new URL(r.request().url())
      if (url.pathname.endsWith('/audio.mp3')) {
        const range = /^bytes=(\d+)-(\d*)$/.exec(r.request().headers().range ?? '')
        const start = range ? Number(range[1]) : 0, end = range?.[2] ? Math.min(Number(range[2]), take.length - 1) : take.length - 1
        return r.fulfill({ status: range ? 206 : 200, contentType: 'audio/mpeg', headers: { 'accept-ranges': 'bytes', ...(range ? { 'content-range': `bytes ${start}-${end}/${take.length}` } : {}) }, body: take.subarray(start, end + 1) })
      }
      if (url.pathname.endsWith('/lookup')) return r.fulfill({ json: { status: 'none' } })
      if (r.request().method() === 'POST') return r.fulfill({ status: 202, json: { id: 'a'.repeat(32), status: 'preparing', stage: 'voicing' } })
      return r.fulfill({ json: { id: 'a'.repeat(32), status: 'ready', duration_s: 120, url: '/api/speech/' + 'a'.repeat(32) + '/audio.mp3' } })
    })
    let releaseUpload, uploadStarted = false
    const uploadHeld = new Promise(resolve => { releaseUpload = resolve })
    await context.route(/\/api\/(?:threads|sessions)\/[^/]+\/attachments$/, async r => {
      uploadStarted = true; await uploadHeld
      await r.fulfill({ status: 201, json: { attachments: [{ name: 'sample.png', path: '98_ATTACHMENTS/sample.png', type: 'image/png', size: png.length }] } })
    })
    await context.route('**/api/limits', r => r.fulfill({ json: { attachment_max_bytes: 10485760, attachment_types: ['image/png'] } }))
    const page = await context.newPage(); page.setDefaultTimeout(15000)
    page.on('pageerror', e => errors.push(e.message))
    const settle = () => page.waitForTimeout(450)
    const shot = async label => { const file = `${engine}-${width}-${theme}-${label}.png`; await page.screenshot({ path: path.join(out, file) }); captures.push(file) }
    const draft = page.locator('#draft')
    const setDraft = async text => { await draft.fill(text); await page.evaluate(() => { __edge.threads.st.mention = null; __edge.schedule() }); await settle() }
    const geometry = () => page.evaluate(() => {
      const dock = document.querySelector('.thread-dock'), box = document.querySelector('.composer-box'), stream = document.querySelector('.tt-stream'), pseudo = getComputedStyle(dock, '::before')
      const r = box.getBoundingClientRect(), d = dock.getBoundingClientRect()
      return { top: r.top, height: r.height, dockTop: d.top, dockHeight: d.height, fade: getComputedStyle(dock).getPropertyValue('--tt-dock-fade').trim(), pseudoTop: parseFloat(pseudo.top), mask: pseudo.maskImage || pseudo.webkitMaskImage, blur: pseudo.backdropFilter || pseudo.webkitBackdropFilter, parentBlur: getComputedStyle(dock).backdropFilter, touches: pseudo.pointerEvents, scroll: stream.scrollTop, max: stream.scrollHeight - stream.clientHeight }
    })
    const sample = async (label, scroll = false) => {
      const data = await page.evaluate(async scroll => {
        const stream = document.querySelector('.tt-stream'), dock = document.querySelector('.thread-dock'), player = document.querySelector('.player'), bar = document.querySelector('.playerbar')
        let removedStream = 0, removedDock = 0, removedSheets = 0, barChanges = 0, lastBar = !!bar
        const sheets = [...document.querySelectorAll('link[data-plugin]')], mic = document.querySelector('.roundmic')
        const micPaint = () => { const s = mic && getComputedStyle(mic); return s ? [s.borderRadius, s.backgroundColor, s.borderTopWidth].join('|') : null }
        const expectedPaint = micPaint()
        const head = new MutationObserver(records => { for (const r of records) for (const node of r.removedNodes) if (sheets.includes(node)) removedSheets++ })
        head.observe(document.head, { childList: true })
        const samples = [], observer = new MutationObserver(records => { for (const r of records) for (const node of r.removedNodes) { if (node === stream || node.contains?.(stream)) removedStream++; if (node === dock || node.contains?.(dock)) removedDock++ } })
        observer.observe(document.querySelector('#app'), { childList: true, subtree: true })
        for (let i = 0; i < (scroll && globalThis.__edgeUploadPending ? 210 : 90); i++) {
          if (scroll && i < 50) stream.scrollTop += i < 25 ? -4 : 4
          await new Promise(requestAnimationFrame)
          const currentBar = !!document.querySelector('.playerbar')
          if (lastBar !== currentBar) barChanges++
          lastBar = currentBar
          samples.push({ bar: currentBar, top: document.querySelector('.tt-stream').getBoundingClientRect().top, playerBottom: player?.getBoundingClientRect().bottom, scroll: document.querySelector('.tt-stream').scrollTop, sheets: sheets.every(n => n.isConnected && n.sheet), micPaint: micPaint() })
        }
        observer.disconnect(); head.disconnect()
        return { removedStream, removedDock, removedSheets, expectedPaint, barChanges, sameStream: document.querySelector('.tt-stream') === stream, sameDock: document.querySelector('.thread-dock') === dock, samePlayer: document.querySelector('.player') === player, sameBar: !bar || document.querySelector('.playerbar') === bar, samples }
      }, scroll)
      checks.push({ label, ...data })
      if (!process.env.EDGE_DIAGNOSTIC) {
        assert.equal(data.removedStream, 0, label + ': stream stays mounted')
        assert.equal(data.removedDock, 0, label + ': dock stays mounted')
        assert.equal(data.samePlayer, true, label + ': native player keeps its node')
        const still = data.samples.slice(55)
        assert.ok(still.every(f => f.bar === still[0].bar && f.top === still[0].top), label + ': stationary layout settles after crossing the player row')
        assert.equal(data.removedSheets, 0, label + ': plugin CSS stays attached')
        assert.ok(data.samples.every(f => f.sheets && f.micPaint === data.expectedPaint), label + ': no plugin paint loss')
      }
      return data
    }
    const gesture = async label => {
      // Mobile WebKit exposes no wheel input or CDP touch-drag API.
      if (width === 390 && engine === 'webkit') return sample(label + '-programmatic', true)
      const recording = sample(label)
      const bounds = await page.locator('.tt-stream').boundingBox()
      const x = Math.round(bounds.x + bounds.width / 2), y = Math.round(bounds.y + bounds.height / 2)
      try { if (width === 390 && engine === 'chromium') {
        const cdp = await context.newCDPSession(page)
        for (const yDistance of [140, -140]) await cdp.send('Input.synthesizeScrollGesture', { x, y, yDistance, gestureSourceType: 'touch', speed: 500 })
        await cdp.detach()
      } else {
        await page.mouse.move(x, y); await page.mouse.wheel(0, -140); await page.mouse.wheel(0, 140)
      } } finally { await recording }
    }
    const blurAudit = async () => {
      await page.evaluate(() => {
        const stream = document.querySelector('.tt-stream'), prose = document.querySelector('[data-message-id="edge-2"] .tt-prose-content'), dock = document.querySelector('.thread-dock')
        stream.scrollTop += prose.getBoundingClientRect().bottom - (dock.getBoundingClientRect().top - 24)
        stream.dispatchEvent(new Event('scroll'))
      })
      await settle()
      const clip = await page.evaluate(() => {
        for (const animation of document.getAnimations()) animation.pause()
        const dock = document.querySelector('.thread-dock').getBoundingClientRect()
        const x = Math.ceil(dock.x), y = Math.ceil(dock.top - 68)
        const hit = document.elementFromPoint(dock.right - 12, dock.top - 15)
        return { x, y, width: Math.floor(dock.width), height: 68, passesTouches: !!hit?.closest('.tt-stream') }
      })
      assert.ok(clip.passesTouches, 'the decorative fade passes touches to the conversation')
      delete clip.passesTouches
      const normal = await page.screenshot({ clip })
      const index = await page.evaluate(() => {
        const sheet = [...document.styleSheets].find(s => s.href?.endsWith('/threads.css'))
        return sheet.insertRule('.tt-conversation > .thread-dock::before { -webkit-backdrop-filter:none !important; backdrop-filter:none !important; }', sheet.cssRules.length)
      })
      const unblurred = await page.screenshot({ clip })
      await page.evaluate(index => { const sheet = [...document.styleSheets].find(s => s.href?.endsWith('/threads.css')); sheet.deleteRule(index) }, index)
      const a = await sharp(normal).removeAlpha().raw().toBuffer(), b = await sharp(unblurred).removeAlpha().raw().toBuffer()
      let changed = 0
      for (let i = 0; i < a.length; i++) if (Math.abs(a[i] - b[i]) > 2) changed++
      await writeFile(path.join(out, `${engine}-${width}-${theme}-blur.png`), normal)
      await writeFile(path.join(out, `${engine}-${width}-${theme}-blur-disabled.png`), unblurred)
      await page.evaluate(() => { for (const animation of document.getAnimations()) if (animation.playState === 'paused') animation.play() })
      if (referenceBlurChannels > 100) assert.ok(changed > 100, `blur changes actual rendered pixels: ${changed}`)
      return { changedChannels: changed, referenceBlurChannels, blurPaintVerified: changed > 100, clip }
    }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      await page.evaluate(id => __edge.threads.open(id), id)
      await page.waitForFunction(() => __edge.threads.st.open && !__edge.threads.st.open.loading && !__edge.threads.st.open.refreshing)
      await page.evaluate(() => {
        const o = __edge.threads.st.open
        o.messages = Array.from({ length: 16 }, (_, i) => ({ id: `edge-${i}`, author: i % 3 === 0 ? 'you' : i % 2 ? 'sprout' : 'scout', kind: 'message', at: '2026-11-14T15:00:00Z', text: ('Keep a little room for the next step. Scroll this conversation beneath the composer, with the player above it. The field stays easy to reach.\n\n').repeat(3) }))
        o.has_more = false; o.atEnd = false; o.restoreTop = 1100; o.typing.clear(); __edge.schedule()
      })
      await page.evaluate(() => document.fonts.ready); await settle()
      const empty = await geometry(); await shot('no-icons')
      await setDraft('A thought'); const one = await geometry(); await shot('one-icon')
      await setDraft('@all A thought'); const several = await geometry(); await shot('several-icons')
      assert.ok(await page.locator('.tt-listener').count() > 1)
      const blur = process.env.EDGE_BASELINE ? null : await blurAudit()
      if (!process.env.EDGE_BASELINE) {
        assert.equal(empty.pseudoTop, -20); assert.equal(one.pseudoTop, -68); assert.equal(several.pseudoTop, -68)
        assert.equal(one.touches, 'none'); assert.match(one.mask, /gradient/); assert.match(one.blur, /blur/); assert.equal(one.parentBlur, 'none')
        assert.ok(Math.abs(empty.top - one.top) < 1 && Math.abs(one.top - several.top) < 1)
        assert.equal(empty.height, one.height)
      }
      if (process.env.EDGE_CAPTURE_ONLY) {
        results.push({ engine, width, theme, empty, one, several, captures, errors })
        await writeFile(path.join(out, 'browser.json'), JSON.stringify(results, null, 2) + '\n')
        console.log(`${engine} ${width} ${theme}: comparison captures`)
        continue
      }
      await sample('scroll-no-audio', true)
      await gesture(width === 390 && engine === 'chromium' ? 'touch-no-audio' : 'wheel-no-audio')
      // Real UI creates the native player. Play it, then move its row offscreen.
      await setDraft('')
      const glyph = page.locator('[data-message-id="edge-1"] .speakbtn')
      await glyph.scrollIntoViewIfNeeded(); await glyph.click()
      await page.locator('.speakmenu [role="menuitem"]').filter({ hasText: 'Listen' }).click()
      await page.locator('.player.ready').waitFor(); await settle()
      if (playback) await page.locator('.player-play').click()
      await settle()
      const audio = await page.evaluate(() => __nativeAudio.map(a => ({ ready: a.readyState, time: a.currentTime, paused: a.paused, error: a.error?.message })))
      if (playback) assert.ok(audio.some(a => a.ready >= 2 && a.time > 0 && !a.paused), JSON.stringify(audio))
      // Cross the row's upper clipping boundary, where adding a bar changes
      // available height. Stationary frames after the gesture must settle.
      await page.evaluate(() => { const stream = document.querySelector('.tt-stream'), player = document.querySelector('.player'); stream.scrollTop += player.getBoundingClientRect().bottom - stream.getBoundingClientRect().top + 3; stream.dispatchEvent(new Event('scroll')) })
      await sample('audio-row-boundary', true)
      await page.evaluate(() => { const stream = document.querySelector('.tt-stream'); stream.scrollTop = stream.scrollHeight - stream.clientHeight; stream.dispatchEvent(new Event('scroll')) })
      await page.locator('.playerbar').waitFor(); await settle(); await shot('audio-top')
      await sample('audio-scroll', true)
      await gesture(width === 390 && engine === 'chromium' ? 'audio-touch' : 'audio-wheel')
      await setDraft('Send with the audio player present')
      await shot('audio-one-icon')
      const sending = sample('audio-send')
      await page.locator('.roundsend').click(); await sending; await settle(); await shot('after-send')
      // Use the real chooser/upload path, hold its network response while scrolling.
      await page.locator('input[type="file"]').first().setInputFiles({ name: 'sample.png', mimeType: 'image/png', buffer: png })
      await page.waitForFunction(() => document.querySelector('.attachstrip'))
      assert.equal(uploadStarted, true)
      await page.evaluate(() => { globalThis.__edgeUploadPending = true })
      await sample('audio-delayed-upload', true); await shot('upload-pending')
      releaseUpload(); await page.evaluate(() => { globalThis.__edgeUploadPending = false }); await settle()
      await setDraft('@all Keep the icons clear'); await shot('audio-several-icons')
      await sample('audio-after-upload', true)
      // A short visual viewport exercises the existing keyboard-sized layout.
      await page.setViewportSize({ width, height: 500 }); await draft.focus(); await settle()
      const short = await geometry(); assert.ok(short.top >= 0 && short.top + short.height <= 500); await shot('short-viewport')
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      assert.deepEqual(errors, [])
      results.push({ engine, width, theme, playback, audio, blur, empty, one, several, short, checks, captures, errors })
      await writeFile(path.join(out, 'browser.json'), JSON.stringify(results, null, 2) + '\n')
      console.log(`${engine} ${width} ${theme}: ${checks.length} sequences; ${captures.length} captures; ${errors.length} page errors`)
    } catch (error) {
      await page.screenshot({ path: path.join(out, `${engine}-${width}-${theme}-failure.png`) }).catch(() => {})
      await writeFile(path.join(out, 'failure.json'), JSON.stringify({ error: error.message, errors, checks, state: await page.evaluate(() => ({ failures: [...__edge.pluginLoader.st.failures], speechOn: __audio?.['read-aloud']?.state.speechOn, speech: document.querySelectorAll('.speakbtn').length, messages: __edge.threads.st.open.messages.map(m => [m.id, m.author, m.text?.length]), audio: __nativeAudio.map(a => ({ ready: a.readyState, paused: a.paused, error: a.error?.message })) })).catch(() => null) }, null, 2))
      throw error
    } finally { releaseUpload(); await context.close(); await app.close() }
  }
} finally { await browser.close() }
