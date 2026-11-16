// Real layout regression with synthetic conversations, no runtime writes.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'

const out = process.env.MENTION_ANCHOR_EVIDENCE
if (!out) throw new Error('Set MENTION_ANCHOR_EVIDENCE')
await mkdir(out, { recursive: true })
const baseline = process.env.MENTION_ANCHOR_BASELINE
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const results = []
try {
  for (const width of [2560, 390]) for (const theme of ['light', 'dark']) {
    const app = await oneProductFixture()
    app.routes['GET /v1/settings/appearance'] = (req, res) => {
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ phone: { mode: theme, size: 100 }, desktop: { mode: theme, size: 100 } }))
    }
    const height = width === 390 ? 844 : 1440
    const context = await browser.newContext({ viewport: { width, height }, colorScheme: theme,
      isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce' })
    for (const name of ['app.js', 'ui.js', 'threads.js', 'threads.css']) {
      let body = baseline ? execFileSync('git', ['show', `${baseline}:public/${name}`], { encoding: 'utf8' }) : await readFile(new URL(`../public/${name}`, import.meta.url), 'utf8')
      if (name === 'app.js') body += '\nglobalThis.__anchor = { threads, state, schedule, goChats };'
      await context.route(`**/${name}`, route => route.fulfill({ contentType: name.endsWith('.css') ? 'text/css' : 'text/javascript', body }))
    }
    const page = await context.newPage(), errors = [], sends = []
    page.on('pageerror', e => errors.push(e.message))
    page.on('request', req => { if (req.method() === 'POST' && req.url().endsWith('/messages')) sends.push(req.url()) })
    const settle = async () => {
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
    }
    const draft = page.locator('#draft'), picker = page.locator('.tt-mentions')
    const geometry = async () => page.evaluate(() => {
      const rect = selector => { const r = document.querySelector(selector).getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height, bottom: r.bottom, right: r.right } }
      const input = rect('#draft')
      return { picker: rect('.tt-mentions'), input, textLeft: input.x + parseFloat(getComputedStyle(document.querySelector('#draft')).paddingLeft), box: rect('.composer-box'), viewport: { width: innerWidth, height: innerHeight } }
    })
    const aligned = async () => {
      await picker.waitFor(); await settle()
      const g = await geometry()
      if (!baseline) {
        assert.ok(Math.abs(g.picker.x - g.textLeft) <= 2, `input text alignment: ${JSON.stringify(g)}`)
        assert.ok(Math.abs(g.box.y - g.picker.bottom - 8) <= 2, `above input box: ${JSON.stringify(g)}`)
        assert.ok(g.picker.x >= 0 && g.picker.right <= g.viewport.width && g.picker.y >= 0, 'picker inside viewport')
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
      }
      return g
    }
    const open = async () => { await draft.fill(''); await draft.pressSequentially('@qui'); await picker.waitFor() }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      if (width === 2560) {
        const rail = page.locator('.rail'), apps = rail.getByRole('button', { name: 'Apps', exact: true })
        if (!baseline) {
          assert.equal(await apps.locator('svg rect').count(), 4)
          assert.equal(await apps.locator('svg').getAttribute('width'), '20')
          assert.equal(await apps.locator('svg').getAttribute('stroke-width'), await rail.getByRole('button', { name: 'Chats', exact: true }).locator('svg').getAttribute('stroke-width'))
        }
        await rail.screenshot({ path: path.join(out, `${baseline ? 'before' : 'after'}-rail-${theme}.png`) })
      } else {
        assert.equal(await page.locator('.rail').count(), 0)
        assert.deepEqual(await page.locator('.navbtn').allTextContents(), ['Chats', 'Files', 'Apps'])
        await page.locator('.nav').screenshot({ path: path.join(out, `${baseline ? 'before' : 'after'}-phone-nav-${theme}.png`) })
      }
      await page.locator('.tt-row').filter({ hasText: 'A little room for the week' }).click()
      await draft.waitFor(); await page.waitForFunction(() => !__anchor.threads.st.open.loading)
      await open()
      for (const collapsed of [false, true]) {
        // The phone has separate list/thread screens; this preference has no
        // visible sidebar there. Exercise both persisted desktop preferences.
        await page.evaluate(value => { __anchor.state.collapsed = value; __anchor.schedule() }, collapsed)
        const g = await aligned()
        const file = `${baseline ? 'before' : 'after'}-mentions-${width}-${collapsed ? 'collapsed' : 'open'}-${theme}.png`
        await page.screenshot({ path: path.join(out, file) })
        results.push({ width, theme, collapsed, geometry: g, screenshot: file })
      }
      if (!baseline) {
        // Keep the picker open while its current layout resizes. Switching
        // layouts rebuilds the field and retains its existing blur dismissal.
        for (const size of width === 390 ? [430, 360, width] : [1920, 1440, width]) {
          await page.setViewportSize({ width: size, height }); await aligned()
        }
        // Choosing from inside the form must only insert, never send.
        await picker.getByRole('option').filter({ hasText: 'Quill' }).click()
        await picker.waitFor({ state: 'detached' })
        assert.equal(await draft.evaluate(el => el.value), '@quill ')
        await open(); await draft.press('Enter'); await picker.waitFor({ state: 'detached' })
        assert.equal(await draft.evaluate(el => el.value), '@quill ')
        await open(); await draft.press('Escape'); await picker.waitFor({ state: 'detached' })
        assert.equal(await draft.evaluate(el => el.value), '@qui')
        await draft.fill(''); await page.locator('.tt-mention-button').click(); await aligned()
        assert.equal(await draft.evaluate(el => el.value), '@')
        await page.locator('.tt-mention-button').click(); await picker.waitFor({ state: 'detached' })
        assert.equal(await draft.evaluate(el => el.value), '')
        for (const scenario of ['general', 'new-thread']) {
          await page.evaluate(async scenario => {
            if (scenario === 'general') await __anchor.threads.open('general')
            else await __anchor.threads.create('sprout')
            __anchor.schedule()
          }, scenario)
          await open()
          const g = await aligned()
          const file = `after-${scenario}-${width}-${theme}.png`
          await page.screenshot({ path: path.join(out, file) })
          results.push({ width, theme, scenario, geometry: g, screenshot: file })
          await draft.press('Escape')
        }
        assert.deepEqual(sends, [], 'mention choice never submits the composer')
      }
      assert.deepEqual(errors, [])
      console.log(`${baseline ? 'CAPTURE' : 'PASS'} ${width} ${theme}: both sidebar preferences, rail/nav${baseline ? '' : ', resizing, mention choice'}`)
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, baseline ? 'before-browser.json' : 'browser.json'), JSON.stringify(results, null, 2) + '\n')
} finally { await browser.close() }
