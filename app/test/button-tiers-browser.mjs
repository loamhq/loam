// Real page and relay; synthetic records, no host sessions or paid audio.
import assert from 'node:assert/strict'
import { readFile, readdir, mkdir, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
import { observeAudio } from './audio-probe.mjs'
import { contrast, resolvedTheme } from '../tools/theme-contrast.mjs'
import { shippedPlugins } from '../src/shipped-plugins.js'

const out = process.env.BUTTON_TIERS_EVIDENCE
if (!out) throw new Error('Set BUTTON_TIERS_EVIDENCE')
const before = process.argv.includes('--before')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const source = await readFile('public/app.js', 'utf8'), reports = []
const selectors = '.tt-head .iconbtn,.composer-actions button,.modelbtn,.navbtn,.railtab,.railround'
const settle = async page => { await page.evaluate(() => document.fonts.ready); await page.waitForTimeout(180) }
try {
  for (const look of before ? ['latte', 'plain'] : ['latte', 'plain', 'yosemite', 'mist-trail', 'deep-forest', 'autumn', 'foundry']) {
    for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
      const app = await oneProductFixture(), errors = []
      app.routes['GET /v1/threads/a-quieter-week'] = async (req, res) => {
        const record = await app.f.api('GET', '/threads/a-quieter-week')
        for (const message of record.messages) message.at = '2026-11-15T17:30:00Z'
        res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(record))
      }
      app.routes['POST /v1/threads/a-quieter-week/attachments'] = (req, res) => {
        res.writeHead(201, { 'content-type': 'application/json' }); res.end(JSON.stringify({ attachments: [{ path: 'Attachments/layout.txt', name: 'layout.txt', size: 7, type: 'text/plain' }] }))
      }
      const plugins = await shippedPlugins(path.resolve('public'))
      for (const plugin of plugins.filter(p => ['dictation', 'read-aloud'].includes(p.id))) plugin.on = true
      app.routes['GET /v1/plugins'] = (req, res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ plugins })) }
      for (const plugin of ['dictation', 'read-aloud']) for (const file of await readdir(`../plugins/_listed/${plugin}/page`)) {
        app.routes[`GET /v1/plugins/${plugin}/files/page/${file}`] = async (req, res) => {
          res.writeHead(200, { 'content-type': file.endsWith('.css') ? 'text/css' : 'text/javascript' }); res.end(await readFile(`../plugins/_listed/${plugin}/page/${file}`))
        }
      }
      const settings = { mode, theme: look, size: 100 }
      app.routes['GET /v1/settings/appearance'] = (req, res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ phone: settings, desktop: settings })) }
      const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode, isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce' })
      await observeAudio(context)
      await context.addInitScript(() => { globalThis.SpeechRecognition ??= class {} })
      if (before) {
        await context.route('**/button-tiers.css', route => route.fulfill({ contentType: 'text/css', body: '' }))
        await context.route('**/threads.js', route => route.fulfill({ contentType: 'text/javascript', body: execFileSync('git', ['show', '423fbfa:public/threads.js'], { encoding: 'utf8' }) }))
      }
      await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__buttons={threads,state,theme,draft,growDraft,schedule,goHome,goFiles,goApps,goSettings,goActivity,closeSheet};' }))
      await context.route('**/api/health', route => route.fulfill({ json: { configured: true, ok: true, speech: true } }))
      await context.route('**/x/read-aloud/status', r => r.fulfill({ json: { speech: true } }))
      await context.route('**/x/read-aloud/speech/lookup', route => route.fulfill({ json: { status: 'none' } }))
      const page = await context.newPage(); page.setDefaultTimeout(10000)
      await page.clock.setFixedTime(new Date('2026-11-15T17:47:20Z'))
      page.on('pageerror', e => errors.push(e.message))
      const report = { look, width, mode, states: [] }
      const shot = async name => { if (process.env.BUTTON_CAPTURES !== '0' && ['latte', 'plain'].includes(look)) await page.screenshot({ path: path.join(out, `${name}-${look}-${width}-${mode}.png`), animations: 'disabled' }) }
      try {
        await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
        if (!before) {
          const pencil = page.getByRole('button', { name: 'New thread', exact: true }), search = page.getByRole('button', { name: 'Search threads', exact: true })
          assert.ok(await pencil.evaluate(n => n.classList.contains('secondary')))
          assert.equal(await pencil.evaluate(n => n.classList.contains('primary')), false)
          assert.equal(await search.evaluate(n => n.classList.contains('secondary') || n.classList.contains('primary')), false)
          assert.equal(await search.evaluate(n => getComputedStyle(n).backgroundColor), 'rgba(0, 0, 0, 0)')
        }
        await page.evaluate(() => __buttons.threads.open('a-quieter-week'))
        await page.locator('#draft').waitFor(); await page.locator('.roundmic').waitFor(); await settle(page)
        for (const draft of ['', 'One clear next step.']) {
          await page.locator('#draft').fill(draft); await settle(page)
          await shot(draft ? 'typing' : 'empty')
          const controls = await page.locator(selectors).evaluateAll(nodes => nodes.filter(n => n.getClientRects().length).map(n => {
            const c = getComputedStyle(n), r = n.getBoundingClientRect()
            return { name: n.getAttribute('aria-label') || n.title || n.textContent, class: n.className, navigation: !!n.closest('.nav,.rail') || n.matches('.accounttile'), disabled: n.disabled, background: c.backgroundColor, color: c.color, border: c.borderWidth, rect: { x: r.x, y: r.y, width: r.width, height: r.height } }
          }))
          report.states.push({ draft: !!draft, controls })
          if (!before) {
            const colors = await page.evaluate(() => {
              const css = getComputedStyle(document.documentElement), n = document.createElement('span'); document.body.append(n)
              const color = token => { n.style.color = css.getPropertyValue(token); return getComputedStyle(n).color }
              const result = { foam: color('--latte-foam'), espresso: color('--latte-espresso') }; n.remove(); return result
            })
            assert.equal(controls.filter(n => !n.navigation && n.background === colors.espresso && !n.disabled).length, draft ? 1 : 0)
            assert.equal(controls.filter(n => !n.navigation && n.background === colors.foam && !n.disabled).length, 1, 'only the mic is secondary; navigation is exempt')
            for (const c of controls.filter(n => !n.navigation && /iconbtn|plusbtn|modelbtn/.test(n.class))) {
              assert.ok(c.background === 'rgba(0, 0, 0, 0)' || !/iconbtn|plusbtn|modelbtn/.test(c.class), JSON.stringify(c))
              assert.equal(c.border, '0px'); assert.ok(c.rect.width >= 44 && c.rect.height >= 44, c.name)
            }
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
          }
        }
        // A saved take uses the actual plugin's player renderer. Audio is never
        // requested or played; the fixture supplies ready metadata only.
        await page.waitForFunction(() => [...(__audio?.['read-aloud']?.takes?.values() ?? [])].some(t => t.agent === 'sprout'))
        await page.evaluate(() => {
          const t = [...__audio['read-aloud'].takes.values()].find(t => t.agent === 'sprout' && t.mode === 'verbatim')
          Object.assign(t, { phase: 'ready', duration: 90, open: true, readyAt: Date.now(), checkedAt: Date.now() })
          __buttons.schedule()
        })
        await page.locator('.player-play').first().waitFor(); await page.locator('.player-play').first().scrollIntoViewIfNeeded(); await settle(page)
        await shot('listen-card')
        report.player = await page.locator('.player-play,.player-rate').evaluateAll(nodes => nodes.map(n => { const c = getComputedStyle(n), r = n.getBoundingClientRect(); return { class: n.className, color: c.color, background: c.backgroundColor, width: r.width, height: r.height } }))
        if (!before) {
          const player = page.locator('.player-play').first(), rate = page.locator('.player-rate').first()
          const paper = await rate.evaluate(n => getComputedStyle(n).backgroundColor)
          assert.equal(paper, 'rgba(0, 0, 0, 0)', 'speed is Newspaper')
          assert.notEqual(await player.evaluate(n => getComputedStyle(n).backgroundColor), paper, 'play is secondary')
          for (const control of [player, rate]) {
            await control.focus(); assert.equal(await control.evaluate(n => getComputedStyle(n).outlineStyle), 'solid')
          }
          if (width === 1440) {
            await rate.hover(); assert.equal(await rate.evaluate(n => getComputedStyle(n).color), await page.locator('.plusbtn').first().evaluate(n => getComputedStyle(n).color))
          }
        }
        if (!before) {
          await page.keyboard.press('Tab')
          for (const sel of ['.plusbtn', '.modelbtn', '.roundmic', '.roundsend']) {
            const control = page.locator(sel).first(); await control.focus()
            const outline = await control.evaluate(n => { const c = getComputedStyle(n); return [c.outlineStyle, parseFloat(c.outlineWidth), n === document.activeElement, n.matches(':focus-visible')] })
            assert.ok(outline[0] !== 'none' && outline[1] >= 2, `${sel} has visible keyboard focus: ${JSON.stringify(outline)}`)
          }
          await page.locator('#draft').fill('')
          await page.locator('#pick-file').setInputFiles({ name: 'layout.txt', mimeType: 'text/plain', buffer: Buffer.from('Layout.') })
          await page.locator('.attachment-chip-name').filter({ hasText: 'layout.txt' }).waitFor()
          await page.waitForFunction(() => !document.querySelector('.roundsend').disabled)
          assert.equal(await page.locator('.roundsend').isEnabled(), true, 'attachment alone activates Send')
          await page.locator('.attachment-chip-x').click(); await page.locator('#draft').fill('One clear next step.'); await settle(page)
          const tokens = await resolvedTheme(look, mode)
          report.contrast = []
          for (const surface of ['paper-page', 'paper-pane', 'paper-card', 'paper-raised', 'paper-hover']) {
            const ratio = contrast(tokens['paper-ink'], tokens[surface]); assert.ok(ratio >= 4.5)
            report.contrast.push({ ink: 'paper-ink', surface, ratio })
          }
          for (const surface of ['latte-foam', 'latte-hover', 'latte-espresso', 'latte-espresso-hover', 'latte-espresso-pressed']) {
            const ratio = contrast(tokens[`${surface}-ink`] ?? tokens['latte-espresso-ink'], tokens[surface]); assert.ok(ratio >= 4.5)
            report.contrast.push({ ink: `${surface}-ink`, surface, ratio })
          }
        }
        assert.deepEqual(errors, []); reports.push(report)
        console.log(`PASS ${before ? 'before' : 'after'} ${look} ${width} ${mode}`)
      } finally { await context.close(); await app.close() }
    }
  }
  await writeFile(path.join(out, 'measurements.json'), JSON.stringify(reports, null, 2) + '\n')
} finally { await browser.close() }
