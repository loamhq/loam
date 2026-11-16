// Paired local captures: real page/relay with synthetic host data only.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { chromium } from '../docs/design-system/deps.mjs'
import { fixtureApp, NOW } from '../docs/design-system/fixture.mjs'

const out = process.env.GROUP_SURFACES_EVIDENCE
if (!out) throw new Error('Set GROUP_SURFACES_EVIDENCE')
const phase = process.env.GROUP_SURFACES_PHASE || 'after'
const baseline = process.env.GROUP_SURFACES_BASELINE || '948a160'
const results = []
await mkdir(out, { recursive: true })
const source = await readFile('public/app.js', 'utf8')
const browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const app = await fixtureApp(), errors = []
    const ctx = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode,
      isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce' })
    await ctx.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__groups={theme,threads,goHome,goFiles,goApps,goSettings,closeSheet,schedule};' }))
    if (phase === 'before') for (const file of ['tokens.css', 'style.css', 'menu-surface.css', 'threads.css', 'apps.css']) {
      const body = execFileSync('git', ['show', `${baseline}:public/${file}`], { encoding: 'utf8' })
      await ctx.route(`**/${file}`, route => route.fulfill({ contentType: 'text/css', body }))
    }
    await ctx.route('**/api/apps', route => route.fulfill({ json: { sources: [], apps: [
      { name: 'Garden notes', owner: 'sprout', source: 'yours', url: 'https://garden.example.org', access: 'private' },
      { name: 'Weekly planner', owner: 'quill', source: 'yours', url: 'https://planner.example.org', access: 'private' },
      { name: 'Reading room', owner: 'scout', source: 'installed', url: 'https://reading.example.org', access: 'shared' },
    ] } }))
    const page = await ctx.newPage()
    page.setDefaultTimeout(10000)
    page.on('pageerror', e => errors.push(e.message))
    await page.clock.setFixedTime(new Date(NOW))
    async function shot(surface) {
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
      const geometry = await page.evaluate(() => {
        const paint = token => {
          const probe = document.createElement('span'); probe.style.background = `var(${token})`; document.body.append(probe)
          const value = getComputedStyle(probe).backgroundColor; probe.remove(); return value
        }
        const selectors = ['.navpill', '.navbtn', '.nav > .settings-utility', '.segmented', '.segmented button', '.appearance-card', '.setmain', '.sheet.settings', '.setrow', '.tt-persona-card', '.tt-persona-actions', '.tt-configuration', '.fs-rootcard', '.apps-group', '.tt-head', '.tt-footer-action']
        const rows = Object.fromEntries(selectors.map(selector => [selector, [...document.querySelectorAll(selector)].filter(n => n.getBoundingClientRect().width).map(n => {
          const r = n.getBoundingClientRect(), s = getComputedStyle(n)
          return { text: n.textContent.slice(0, 50), on: n.classList.contains('on'), x: r.x, y: r.y, width: r.width, height: r.height, background: s.backgroundColor, ink: s.color, border: s.borderTopWidth, gap: s.gap, padding: s.padding, margin: s.margin }
        })]))
        return { overflow: document.documentElement.scrollWidth > innerWidth + 1 || document.documentElement.scrollHeight > innerHeight + 1,
          paints: { paper: paint('--paper-page'), glass: paint('--paper-pane-glass'), selection: paint('--latte-control-selected'), primary: paint('--latte-espresso') }, rows }
      })
      await page.screenshot({ path: path.join(out, `${phase}-${surface}-${width}-${mode}.png`), animations: 'disabled' })
      assert.equal(geometry.overflow, false, `${surface} document overflow`)
      for (const selector of ['.tt-persona-card', '.tt-configuration']) for (const n of geometry.rows[selector]) {
        assert.ok(n.x >= 0 && n.y >= 0 && n.x + n.width <= width + 1 && n.y + n.height <= (width === 390 ? 845 : 1001), JSON.stringify(n))
      }
      if (phase === 'after') {
        for (const n of [...geometry.rows['.navbtn'], ...geometry.rows['.segmented button']]) {
          assert.ok(n.height >= 44, JSON.stringify(n))
          assert.equal(n.border, '0px', JSON.stringify(n))
          if (!n.on) assert.equal(n.background, 'rgba(0, 0, 0, 0)', JSON.stringify(n))
          else assert.equal(n.background, geometry.paints.selection, JSON.stringify(n))
        }
        assert.notEqual(geometry.paints.selection, geometry.paints.primary, 'selection remains quieter than the primary action')
        for (const n of geometry.rows['.segmented']) assert.equal(n.background, 'rgba(0, 0, 0, 0)')
        for (const n of geometry.rows['.navpill']) assert.equal(n.border, '0px')
        for (const n of geometry.rows['.nav > .settings-utility']) assert.equal(n.background, geometry.paints.glass)
        for (const n of geometry.rows['.setrow']) assert.ok(n.height >= 44, JSON.stringify(n))
        for (const n of geometry.rows['.tt-footer-action']) assert.ok(n.width >= 44 && n.height >= 44, JSON.stringify(n))
        if (surface === 'files') {
          const gaps = await page.locator('.fs-rootcard').evaluateAll(nodes => nodes.slice(1).map((n, i) => n.getBoundingClientRect().top - nodes[i].getBoundingClientRect().bottom))
          assert.ok(gaps.every(g => g >= 16), JSON.stringify(gaps))
        }
        if (surface === 'apps') assert.equal(geometry.rows['.apps-group'].length, 2)
        if (surface === 'appearance') {
          assert.ok(geometry.rows['.segmented'].length >= 3)
          for (const n of geometry.rows['.appearance-card']) assert.equal(n.padding, '24px')
          const gaps = await page.locator('.appearance-card').evaluateAll(nodes => nodes.slice(1).map((n, i) => n.getBoundingClientRect().top - nodes[i].getBoundingClientRect().bottom))
          assert.ok(gaps.every(g => Math.abs(g - 24) < 1), JSON.stringify(gaps))
        }
      }
      results.push({ surface, width, mode, ...geometry })
    }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      await page.evaluate(mode => __groups.theme.set({ theme: 'latte', mode, size: 100 }), mode)
      await page.waitForFunction(() => !__groups.theme.status.saving)
      await shot('chats')
      await page.locator('[data-agent-menu]').first().click()
      await page.locator('.tt-persona-card').waitFor()
      await shot('agent-card')
      await page.keyboard.press('Escape')
      await page.evaluate(() => __groups.goSettings('root'))
      await page.locator('.sheet.settings').waitFor()
      await shot('settings')
      await page.evaluate(() => __groups.goSettings('appearance'))
      await page.getByRole('slider', { name: 'App zoom' }).waitFor()
      await page.evaluate(() => { for (const n of document.querySelectorAll('.setmain,.sheet.settings')) n.scrollTop = 0 })
      await shot('appearance')
      await page.evaluate(() => { for (const n of document.querySelectorAll('.setmain,.sheet.settings')) n.scrollTop = n.scrollHeight })
      await shot('appearance-lower')
      await page.evaluate(() => __groups.theme.set({ options: { latte: { 'agent-shapes': 'latte-art' } } }))
      await page.waitForFunction(() => !__groups.theme.status.saving)
      await page.evaluate(() => { for (const n of document.querySelectorAll('.setmain,.sheet.settings')) n.scrollTop = Math.min(260, n.scrollHeight) })
      await shot('appearance-art')
      await page.evaluate(() => __groups.theme.set({ options: { latte: { 'agent-shapes': 'distinct' } } }))
      await page.waitForFunction(() => !__groups.theme.status.saving)
      // Native keyboard activation still selects and saves through the host.
      await page.getByRole('button', { name: 'Circle', exact: true }).focus()
      await page.keyboard.press('Space')
      await page.waitForFunction(() => __groups.theme.value.options.latte['agent-shapes'] === 'circle' && !__groups.theme.status.saving)
      assert.equal(await page.getByRole('button', { name: 'Circle', exact: true }).getAttribute('aria-pressed'), 'true')
      await page.getByRole('button', { name: 'Each its own', exact: true }).click()
      await page.waitForFunction(() => !__groups.theme.status.saving)
      await page.evaluate(() => { __groups.closeSheet(); __groups.goHome(); __groups.goFiles() })
      await page.locator('.fs-rootcard').first().waitFor()
      await shot('files')
      await page.evaluate(() => { __groups.goHome(); __groups.goApps() })
      await page.locator('.apps-page').waitFor()
      await shot('apps')
      await page.evaluate(() => { __groups.goHome(); __groups.threads.open('a-quieter-week') })
      await page.locator('.tt-message').first().waitFor()
      await shot('thread')
      await page.getByRole('button', { name: 'Providers & models', exact: true }).click()
      await page.locator('.tt-configuration').waitFor()
      await shot('menu')
      assert.deepEqual(errors, [])
    } finally { await ctx.close(); await app.close() }
  }
  await writeFile(path.join(out, `${phase}-geometry.json`), JSON.stringify(results, null, 2) + '\n')
  if (phase === 'after') {
    const surfaces = [...new Set(results.map(r => r.surface))]
    const variants = [390, 1440].flatMap(width => ['light', 'dark'].map(mode => `${width}-${mode}`))
    const sections = variants.map((variant, i) => `<section data-variant="${variant}"${i ? ' hidden' : ''}>${surfaces.map(surface => `<h2>${surface}</h2><div class="pair">${['before', 'after'].map(phase => {
      const file = `${phase}-${surface}-${variant}.png`
      return `<figure><figcaption>${phase}</figcaption><a href="${file}"><img loading="lazy" src="${file}" alt="${phase} ${surface} ${variant}"></a></figure>`
    }).join('')}</div>`).join('')}</section>`).join('')
    await writeFile(path.join(out, 'index.html'), `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Group surfaces and spacing — paired evidence</title>
<style>body{margin:24px;font:16px/1.5 system-ui;background:#eee;color:#222}header{position:sticky;top:0;background:#eee;padding:12px 0;z-index:1}select{font:inherit;padding:8px}h1{font-size:24px}h2{font-size:20px;margin-top:32px}.pair{display:flex;gap:24px;align-items:start}figure{margin:0;flex:1;min-width:0}img{width:100%;height:auto}figcaption{margin-bottom:8px;font-weight:bold}[hidden]{display:none}</style>
<header><h1>One surface per group; spacing rhythm</h1><p>Local synthetic hosts; Chromium phone emulation. Baseline ${baseline}. Click an image for full resolution.</p><label>Viewport and mode <select id="variant">${variants.map(v => `<option>${v}</option>`).join('')}</select></label></header>${sections}
<script>document.querySelector('#variant').onchange=e=>document.querySelectorAll('section').forEach(n=>n.hidden=n.dataset.variant!==e.target.value)</script>\n`)
  }
  console.log(`${phase}: ${results.length} captures; 390/1440, light/dark; no document overflow or page errors`)
} finally { await browser.close() }
