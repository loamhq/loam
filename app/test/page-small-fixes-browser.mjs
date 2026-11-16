// Four page regressions against synthetic host answers through the real relay.
// Optional browser packages and the historical public snapshot live in TMPDIR.
import assert from 'node:assert/strict'
import path from 'node:path'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { filesHost } from './files-host.js'
import { rootsAnswer, row } from './fixtures/files-contract.js'
import { threadPage } from './thread-page.js'
import { fakeBroker, appAgainst, closing } from './harness.js'
import { shippedPlugins } from '../src/shipped-plugins.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const baseline = process.env.PAGE_FIXES_BASELINE_PUBLIC
const publicRoot = baseline || 'public'
const out = process.env.PAGE_FIXES_EVIDENCE || `${process.env.TMPDIR}/page-fixes`
await mkdir(out, { recursive: true })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
const markdown = '# Restart report\n\nThe fix is ready.\n\n- **What changed:** a _clearer_ `system` line.\n- Read [the notes](https://example.org/notes).'
const plain = 'Restart report The fix is ready. What changed: a clearer system line. Read the notes.'
const receipt = ('The update is ready to review. The conversation keeps its history, and the page shows the next useful step. Checks passed with the recorded fixture. ').repeat(13)
const reports = []
try {
  for (const [width, theme] of [[390, 'latte'], [1440, 'latte'], [390, 'plain'], [1440, 'plain']]) {
    const height = width === 390 ? 844 : 900, label = `${width}-${theme}-light`
    const fixture = filesHost(), routes = fixture.routes
    const tp = threadPage(routes, { initial: [{ id: 'review', title: 'Restart report', text: markdown }] })
    const record = tp.records.get('review')
    record.messages = [
      { id: 'short', kind: 'system', author: 'system', text: 'The session resumed.' },
      { id: 'receipt', kind: 'system', author: 'system', text: receipt },
      { id: 'end', kind: 'message', author: 'scout', text: 'Ready for the next step.' },
    ]
    routes['GET /v1/settings/appearance'] = (req, res) => json(res, { phone: { theme, mode: 'light', size: 90 }, desktop: { theme, mode: 'light', size: 100 } })
    routes['GET /v1/activity'] = (req, res) => json(res, { items: [], total: 0, jobs: { slots: [], landing: [], queue: [] }, jobs_error: null })
    const roots = rootsAnswer()
    roots.roots = roots.roots.filter(r => r.root !== 'system')
    roots.roots[0].description = 'Your shared notes, projects and reference material, ready to open with your agents.'
    roots.roots[1].description = 'One home per agent, with their notes, skills and working files.'
    roots.shortcuts = ['Inbox', 'Daily notes', 'Projects', 'Reference', 'Meeting notes', 'Reading'].map(label => ({ label, resource: row('vault', label) }))
    routes['GET /v1/files/roots'] = (req, res) => json(res, roots)
    const plugins = await shippedPlugins(publicRoot)
    routes['GET /v1/plugins'] = (req, res) => json(res, { plugins })
    for (const plugin of plugins) for (const file of ['page/main.js', 'page/engine.js', 'theme.css', 'PLUGIN.md']) {
      routes[`GET /v1/plugins/${plugin.id}/files/${file}`] = async (req, res) => {
        try { const body = await readFile(path.join(publicRoot, plugin.folder, file)); res.writeHead(200, { 'content-type': file.endsWith('.js') ? 'text/javascript' : 'text/css' }); res.end(body) }
        catch { res.writeHead(404); res.end() }
      }
    }
    const broker = await fakeBroker(routes), app = await appAgainst(broker)
    const context = await browser.newContext({ viewport: { width, height }, colorScheme: 'light', reducedMotion: 'reduce', isMobile: width === 390, hasTouch: width === 390 })
    const page = await context.newPage(), errors = [], report = { label, errors }
    page.on('pageerror', e => errors.push(e.message))
    try {
      if (baseline) await context.route('**/*', async route => {
        const url = new URL(route.request().url())
        if (url.origin === app.url && /\.(js|css)$/.test(url.pathname)) {
          try { return await route.fulfill({ path: path.join(publicRoot, url.pathname) }) } catch {}
        }
        return route.fallback()
      })
      const source = await readFile(path.join(publicRoot, 'app.js'), 'utf8')
      await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__fixes = { state, store, threads, files, activity, goSettings, goActivity, closeSheet };' }))
      // Hold the first response to capture a true cold-load state deterministically.
      let answerRoots
      await context.route('**/api/files/roots', async route => { await new Promise(resolve => { answerRoots = resolve }); await route.continue() })
      await page.goto(`${app.url}/files`)
      await page.waitForFunction(() => globalThis.__fixes && document.body.textContent.includes('Loading…'))
      await page.evaluate(() => document.fonts.ready)
      report.coldEmpty = await page.getByText('No shortcuts yet', { exact: true }).count()
      if (!baseline) assert.equal(report.coldEmpty, 0)
      await page.screenshot({ path: `${out}/files-loading-${label}.png` })
      answerRoots()
      await context.unroute('**/api/files/roots')
      await page.locator('.fs-rootcard').nth(1).waitFor()
      await page.locator('.fs-host-shortcut').nth(5).waitFor()
      await page.evaluate(() => document.fonts.ready)
      const geometry = () => [...document.querySelectorAll('.fs-rootcard')].map(node => {
        const r = node.getBoundingClientRect(), words = node.querySelector('.fs-rootwords').getBoundingClientRect()
        return { height: r.height, words: words.height, contains: words.top >= r.top && words.bottom <= r.bottom, shrink: getComputedStyle(node).flexShrink }
      })
      report.cards = await page.evaluate(geometry)
      if (!baseline) assert.ok(report.cards.every(r => r.contains && r.shrink === '0'), JSON.stringify(report.cards))
      await page.screenshot({ path: `${out}/files-home-${label}.png` })
      if (!baseline) {
        // More shortcuts and a shorter viewport must increase scrolling, never crush cards.
        await page.evaluate(() => {
          const shortcuts = __fixes.files.st.roots.shortcuts
          __fixes.files.st.roots.shortcuts = [...shortcuts, ...Array.from({ length: 24 }, (_, i) => ({ label: `Extra folder ${i}`, resource: { ...shortcuts[0].resource, path: `Extra/${i}` } }))]
          dispatchEvent(new Event('resize'))
        })
        await page.locator('.fs-host-shortcut').nth(29).waitFor()
        for (const shortHeight of [360, 560, height]) {
          await page.setViewportSize({ width, height: shortHeight })
          const cards = await page.evaluate(geometry)
          assert.ok(cards.every(r => r.contains))
          assert.deepEqual(cards.map(r => r.height), report.cards.map(r => r.height))
        }
        const scrolled = await page.locator('.fs-scroll').evaluate(node => { node.scrollTop = node.scrollHeight; return node.scrollTop > 0 })
        assert.ok(scrolled)
      }
      // Slow the listing and its initial stream so neither can establish a roster yet.
      let answerThreads, answerAgents
      await context.route(/\/api\/threads(?:\?.*)?$/, async route => { await new Promise(r => { answerThreads = r }); await route.continue() })
      await context.route('**/api/agents', async route => { await new Promise(r => { answerAgents = r }); await route.continue() })
      await page.goto(app.url)
      await page.getByText('Opening chats…', { exact: true }).waitFor()
      report.loadingHint = await page.getByText('Pick an agent above or start a thread.', { exact: true }).count()
      if (!baseline) assert.equal(report.loadingHint, 0)
      await page.screenshot({ path: `${out}/chats-loading-${label}.png` })
      answerThreads(); answerAgents()
      await context.unroute(/\/api\/threads(?:\?.*)?$/); await context.unroute('**/api/agents')
      await page.locator('.tt-row').first().waitFor()
      await page.evaluate(() => __fixes.goActivity())
      await page.locator('.activity-last-line').first().waitFor()
      report.preview = await page.locator('.activity-last-line').first().textContent()
      if (!baseline) assert.equal(report.preview, plain)
      await page.screenshot({ path: `${out}/activity-earlier-${label}.png` })
      await page.goto(`${app.url}/threads/review`)
      const long = page.locator('[data-message-id="receipt"]'), short = page.locator('[data-message-id="short"]')
      await long.waitFor()
      await page.evaluate(() => document.fonts.ready)
      report.shortSystemHeight = await short.evaluate(n => n.getBoundingClientRect().height)
      if (!baseline) {
        await long.getByRole('button', { name: 'Show more', exact: true }).waitFor()
        const body = long.locator('.tt-system-text')
        report.system = await body.evaluate(n => ({ height: n.clientHeight, full: n.scrollHeight, line: parseFloat(getComputedStyle(n).lineHeight), text: n.textContent }))
        assert.equal(report.system.text, receipt)
        assert.ok(report.system.height <= report.system.line * 3 + 1)
        assert.ok(report.system.full > report.system.height)
        assert.equal(await short.locator('button').count(), 0)
      }
      await long.scrollIntoViewIfNeeded()
      await page.screenshot({ path: `${out}/system-collapsed-${label}.png` })
      if (!baseline) {
        const toggle = long.getByRole('button', { name: 'Show more', exact: true })
        await toggle.focus(); await page.keyboard.press('Enter')
        await long.getByRole('button', { name: 'Show less', exact: true }).waitFor()
        assert.equal(await long.locator('.tt-system-text').evaluate(n => n.clientHeight >= n.scrollHeight - 1), true)
        await page.screenshot({ path: `${out}/system-expanded-${label}.png` })
        assert.equal(await long.getByRole('button', { name: 'Show less', exact: true }).evaluate(n => n === document.activeElement), true)
        await page.keyboard.press('Space')
        await long.getByRole('button', { name: 'Show more', exact: true }).waitFor()
        // The same class of bug on Settings, a constrained sheet and a menu:
        // insert real content buttons, constrain each scroller, measure containment.
        report.scrollers = await page.evaluate(() => {
          const names = ['fs-scroll', 'profile-body', 'setnav', 'setmain', 'popover', 'actions-sheet', 'sheet']
          return names.map(className => {
            const scroller = document.createElement('div')
            scroller.className = className
            scroller.style.cssText = 'position:fixed;inset:auto;top:0;left:0;width:280px;height:120px;max-height:120px;display:flex;flex-direction:column;overflow:auto;padding:10px;gap:10px;z-index:1000'
            for (let i = 0; i < 6; i++) {
              const button = document.createElement('button'); button.className = 'fs-rootcard'
              button.innerHTML = '<span>Several lines of useful explanatory text that must stay inside its button, even when there are many rows.</span>'
              scroller.append(button)
            }
            document.body.append(scroller)
            const contained = [...scroller.children].every(n => n.firstChild.getBoundingClientRect().height <= n.getBoundingClientRect().height)
            scroller.scrollTop = scroller.scrollHeight
            const result = { className, contained, scrolls: scroller.scrollTop > 0 }
            scroller.remove(); return result
          })
        })
        assert.ok(report.scrollers.every(r => r.contained && r.scrolls))
      }
      assert.deepEqual(errors, [])
      reports.push(report)
      console.log(`PASS ${label}${baseline ? ' baseline captured' : ''}`)
    } finally { await context.close(); await closing(app, broker)() }
  }
} finally { await browser.close() }
await writeFile(`${out}/browser.json`, JSON.stringify({ baseline: Boolean(baseline), reports }, null, 2) + '\n')
