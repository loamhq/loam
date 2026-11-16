// Compare the real pre-tier page assets with the current page, using one fixture.
// Navigation owns its original paint; it does not spend the action tier budget.
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fixtureApp, NOW } from '../docs/design-system/fixture.mjs'
import { sharp } from '../docs/design-system/deps.mjs'

const out = process.env.BUTTON_NAV_EVIDENCE
if (!out || !process.env.TMPDIR) throw new Error('Set BUTTON_NAV_EVIDENCE and TMPDIR')
await mkdir(out, { recursive: true })
const reference = '423fbfa', referenceRoot = await mkdtemp(path.join(process.env.TMPDIR, 'nav-reference-'))
execFileSync('tar', ['-x', '-C', referenceRoot], { input: execFileSync('git', ['archive', reference, 'public'], { maxBuffer: 64 * 1024 * 1024 }) })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const reports = [], navSelector = '.nav,.navpill,.nav button,.rail,.rail button,.accounttile'
const properties = ['backgroundColor', 'color', 'borderColor', 'borderWidth', 'borderRadius', 'boxShadow', 'textDecorationLine', 'textUnderlineOffset', 'outlineColor', 'outlineStyle', 'outlineWidth', 'outlineOffset', 'opacity']
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.png': 'image/png' }
try {
  for (const look of ['latte', 'plain', 'yosemite', 'mist-trail', 'deep-forest', 'autumn', 'foundry']) for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const runs = [], captures = [], errors = []
    for (const before of [true, false]) {
      const app = await fixtureApp()
      const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode, isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce' })
      try {
        if (before) await context.route('**/*', async route => {
          const url = new URL(route.request().url())
          if (url.origin !== app.url || url.pathname.startsWith('/api/')) return route.continue()
          const file = path.join(referenceRoot, 'public', url.pathname === '/' ? 'index.html' : url.pathname)
          try { await route.fulfill({ contentType: mime[path.extname(file)] || 'application/octet-stream', body: await readFile(file) }) }
          catch (e) { if (e.code === 'ENOENT') return route.continue(); throw e }
        })
        const source = await readFile(before ? path.join(referenceRoot, 'public/app.js') : 'public/app.js', 'utf8')
        await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__nav={theme,goHome,goFiles,goApps,goActivity,goSettings,closeSheet};' }))
        const page = await context.newPage(); page.setDefaultTimeout(10000)
        page.on('pageerror', e => errors.push(e.message)); await page.clock.setFixedTime(new Date(NOW))
        await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
        await page.evaluate(({ look, mode }) => __nav.theme.set({ theme: look, mode }), { look, mode })
        await page.evaluate(() => document.fonts.ready)
        const rows = []
        const measure = async state => {
          rows.push({ state, controls: await page.locator(navSelector).evaluateAll((nodes, properties) => nodes.filter(n => n.getClientRects().length).map(n => {
            const css = getComputedStyle(n), rect = n.getBoundingClientRect()
            return { class: n.className, label: n.getAttribute('aria-label') || n.title || n.textContent.trim(), paint: Object.fromEntries(properties.map(p => [p, css[p]])), rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height } }
          }), properties) })
        }
        for (const destination of ['Chats', 'Files', 'Apps', 'Activity', 'Settings']) {
          await page.mouse.move(width - 2, 2)
          await page.evaluate(destination => {
            __nav.closeSheet()
            if (destination === 'Chats') __nav.goHome()
            if (destination === 'Files') __nav.goFiles(null, '')
            if (destination === 'Apps') __nav.goApps()
            if (destination === 'Activity') __nav.goActivity()
            if (destination === 'Settings') { __nav.goHome(); __nav.goSettings('root') }
            document.activeElement?.blur()
          }, destination)
          await page.waitForTimeout(250)
          await measure(`${destination}:rest`)
          if (destination === 'Chats' && look === 'latte') {
            const nav = page.locator(width === 390 ? '.nav' : '.rail')
            captures.push(await nav.screenshot({ animations: 'disabled' }))
          }
          // Settings and desktop Activity are modal surfaces over the rail.
          // Compare their selected paint without reaching through the modal.
          if (destination === 'Settings' || destination === 'Activity' && width === 1440) continue
          const buttons = page.locator(width === 390 ? '.nav button' : '.rail button')
          for (let i = 0; i < await buttons.count(); i++) {
            const button = buttons.nth(i)
            await button.hover(); await measure(`${destination}:${i}:hover`)
            await page.mouse.down(); await measure(`${destination}:${i}:active`)
            // Release away from the target to avoid navigating during the probe.
            await page.mouse.move(width - 2, 2); await page.mouse.up()
            await page.keyboard.press('Tab'); await button.focus(); await measure(`${destination}:${i}:focus`)
            await button.evaluate(n => n.blur())
          }
        }
        runs.push(rows)
      } finally { await context.close(); await app.close() }
    }
    assert.deepEqual(errors, [])
    assert.deepEqual(runs[1], runs[0], `${look} ${width} ${mode}: navigation must match ${reference}`)
    if (look === 'latte') {
      // Nav-only paired captures stay small: reference left, result right.
      const images = await Promise.all(captures.map(buffer => sharp(buffer).ensureAlpha().raw().toBuffer({ resolveWithObject: true })))
      assert.deepEqual(images[1], images[0], `${width} ${mode}: nav pixels must match`)
      const { info } = images[0]
      await sharp({ create: { width: info.width * 2, height: info.height, channels: 4, background: '#00000000' } }).composite(captures.map((input, i) => ({ input, left: i * info.width, top: 0 }))).png().toFile(path.join(out, `nav-pair-${width}-${mode}.png`))
    }
    reports.push({ look, width, mode, states: runs[1].length, matched: true })
    console.log(`PASS ${look} ${width} ${mode}: ${runs[1].length} nav states match ${reference}${look === 'latte' ? '; capture pixels identical' : ''}`)
  }
  await writeFile(path.join(out, 'comparison.json'), JSON.stringify({ reference: execFileSync('git', ['rev-parse', reference], { encoding: 'utf8' }).trim(), reports }, null, 2) + '\n')
} finally { await browser.close(); await rm(referenceRoot, { recursive: true, force: true }) }
