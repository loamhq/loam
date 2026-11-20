// Real computed-style and interaction checks against a local fixture server.
// No staging or live broker access. Optional Playwright stays outside the repo.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
import { filesHost } from './files-host.js'
import { auditControls, assertControls } from './control-style-guard.mjs'

const out = process.env.SHAPE_EVIDENCE
if (out) await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const app = await oneProductFixture(), reports = []
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
const values = { phone: { mode: 'light', size: 100, theme: 'latte' }, desktop: { mode: 'light', size: 100, theme: 'latte' } }
app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, values)
app.routes['PATCH /v1/settings/appearance'] = (req, res, url, raw) => { for (const [kind, patch] of Object.entries(JSON.parse(raw))) Object.assign(values[kind], patch); json(res, values) }
for (const [key, handler] of Object.entries(filesHost().routes)) if (key.includes('/v1/files/')) app.routes[key] = handler
const owner = app.f.agents[0].id
const title = "The app's shape: Chats, Files, Apps; one Activity; six settings sections"
app.routes['GET /v1/activity'] = (req, res) => json(res, { jobs: { slots: [], landing: [], queue: [] }, next_offset: null, total: 2, items: [
  { id: 'landed', agent: owner, kind: 'job', title, outcome: 'completed', landing: 'landed', started: '2026-11-12T17:50:00Z', ts: '2026-11-12T18:17:00Z' },
  { id: 'cut-off', agent: owner, kind: 'job', title: 'A stopped task', outcome: 'killed', started: '2026-11-12T16:50:00Z', ts: '2026-11-12T17:00:00Z' },
] })
app.routes['GET /v1/sessions/live'] = (req, res) => json(res, { sessions: [{ id: 'resident', agent: owner, kind: 'thread', thread_id: 'a-quieter-week', thread_title: 'A quieter week', state: 'working', rss_mb: 256 }], box: { ram_total_mb: 32768, ram_used_mb: 16384, can_start: 12 } })
app.routes['GET /v1/sessions/live/events'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n') }
const catalog = [
  { name: 'Garden', url: 'https://garden.example.com/', owner, source: 'yours', access: 'private', frame: true, expires: '2026-11-25', also_at: [] },
  { name: 'Journal', url: 'https://journal.example.org/', owner: 'owner', source: 'yours', access: 'public', frame: false, expires: null, also_at: [] },
]
let appFailure = false, emptyApps = false
app.routes['GET /v1/apps'] = (req, res) => appFailure ? (res.writeHead(503), res.end('{"error":"Apps unavailable."}')) : json(res, { sources: ['yours'], apps: emptyApps ? [] : catalog })
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')

async function screenshot(page, name) { if (out) await page.screenshot({ path: path.join(out, `${name}.png`), animations: 'disabled' }) }
try {
  for (const width of [390, 1440]) for (const theme of ['latte', 'plain']) for (const mode of ['light', 'dark']) {
    const kind = width === 390 ? 'phone' : 'desktop'
    values[kind] = { theme, mode, size: 100 }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode, isMobile: width === 390, hasTouch: width === 390, timezoneId: 'America/Los_Angeles', locale: 'en-GB', reducedMotion: 'reduce' })
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__shapeWalk = { goActivity, goFiles, goSettings, closeSheet, apps, threads, schedule };' }))
    await context.route('https://garden.example.com/**', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><title>Garden</title><p>Fixture app</p>' }))
    const page = await context.newPage(), errors = [], checks = [], label = `${width}-${theme}-${mode}`
    page.on('pageerror', error => errors.push(error.message))
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      await page.evaluate(() => {
        const t = __shapeWalk.threads.st.threads.find(t => t.id === 'a-quieter-week')
        t.members[0].working = true; __shapeWalk.schedule()
      })
      await page.locator('[data-agent-menu]').first().click()
      await page.locator('.tt-persona-current').waitFor()
      const cardStyles = await auditControls(page); assertControls(cardStyles)
      assert.ok(cardStyles.filter(row => row.card).length >= (width === 390 ? 4 : 3), 'all card controls including Close and working line are guarded')
      await screenshot(page, `agent-card-${label}`)
      await page.keyboard.press('Escape')
      checks.push('Every agent-card control: palette, native-button reset, type and 44px hit area')
      const destination = name => page.locator(width === 390 ? '.navbtn' : '.railtab').filter({ hasText: new RegExp(`^${name}$`) })
      const settings = page.locator('.settings-utility')
      await destination('Apps').click(); await page.locator('.apps-row').first().waitFor()
      const listStyles = await auditControls(page); assertControls(listStyles)
      assert.ok(listStyles.filter(row => row.name.startsWith('Open ')).every(row => row.height >= 44))
      assert.match(await page.locator('.apps-secondary').first().textContent(), /.+ · Private/)
      assert.equal(await page.locator('.apps-group-list').evaluate(node => getComputedStyle(node).display), width === 390 ? 'grid' : 'flex')
      await screenshot(page, `apps-${label}`)
      // A mutation recreating the staging defect must fail the same audit.
      await page.locator('.apps-row').evaluateAll(nodes => nodes.forEach(node => { node.style.background = 'revert'; node.style.border = 'revert' }))
      assert.ok((await auditControls(page)).some(row => row.uaButton), 'the computed-style guard failed to detect the original browser-button defect')
      await page.locator('.apps-row').evaluateAll(nodes => nodes.forEach(node => { node.style.removeProperty('background'); node.style.removeProperty('border') }))
      emptyApps = true; await page.evaluate(() => __shapeWalk.apps.load())
      assertControls(await auditControls(page))
      assert.ok(await page.getByRole('button', { name: 'Build an app' }).isVisible())
      appFailure = true; await page.evaluate(() => __shapeWalk.apps.load())
      assertControls(await auditControls(page))
      assert.ok(await page.getByRole('button', { name: 'Try again', exact: true }).isVisible())
      emptyApps = false; appFailure = false; await page.evaluate(() => __shapeWalk.apps.load())
      checks.push('Apps computed styles, empty/error controls and defect counterfactual')
      if (width === 1440) {
        const firstRow = page.locator('.apps-row').first()
        const idleBackground = await firstRow.evaluate(node => getComputedStyle(node).backgroundColor)
        await firstRow.hover()
        assert.notEqual(await firstRow.evaluate(node => getComputedStyle(node).backgroundColor), idleBackground)
        await page.mouse.move(0, 0)
        await firstRow.focus(); await page.keyboard.press('Tab'); await page.keyboard.press('Shift+Tab')
        assert.equal(await firstRow.evaluate(node => node.matches(':focus-visible')), true)
        assert.equal(await firstRow.evaluate(node => getComputedStyle(node).outlineOffset), '-2px')
        assert.equal(await firstRow.evaluate(node => getComputedStyle(node).outlineStyle), 'solid')
        await page.locator('.apps-row').first().click(); await page.locator('.apps-embed').waitFor()
        const pills = await page.locator('.apps-toolbar .pillbtn').evaluateAll(nodes => nodes.map(node => { const css = getComputedStyle(node), r = node.getBoundingClientRect(); return { y: r.y, height: r.height, decoration: css.textDecorationLine, display: css.display, align: css.alignItems } }))
        assert.equal(pills[0].y, pills[1].y); assert.equal(pills[0].height, pills[1].height)
        assert.ok(pills.every(pill => pill.decoration === 'none' && pill.height >= 44 && pill.align === 'center'))
        assertControls(await auditControls(page))
        await screenshot(page, `app-bar-${label}`)
        await page.getByRole('button', { name: 'Close app', exact: true }).click()
      }
      // Pointer-open Settings focuses its heading, never Appearance.
      await settings.click(); await page.locator('.settings:popover-open').waitFor()
      assert.equal(await settings.getAttribute('aria-expanded'), 'true')
      assert.equal(await page.evaluate(() => document.activeElement.classList.contains('sheet-title')), true)
      await screenshot(page, `settings-${label}`)
      assert.equal(await page.locator('.settings .navrow:focus-visible, .settings .setrow:focus-visible').count(), 0)
      await page.locator('.settings .dialog-x, .settings .sheet-x').filter({ visible: true }).first().click()
      await page.locator('.sheet.settings').waitFor({ state: 'detached' })
      assert.equal(await settings.getAttribute('aria-expanded'), 'false')
      assert.equal(await settings.evaluate(node => node.classList.contains('on')), false)
      const closed = await settings.evaluate(node => ({ background: getComputedStyle(node).backgroundColor, outline: getComputedStyle(node).outlineStyle, focused: node.matches(':focus-visible') }))
      if (width === 1440) assert.equal(closed.background, 'rgba(0, 0, 0, 0)')
      assert.equal(closed.focused, false); assert.equal(closed.outline, 'none')
      // Keyboard focus still gets its ring after pointer focus return.
      await page.keyboard.press('Tab'); await page.keyboard.press('Shift+Tab')
      assert.equal(await settings.evaluate(node => node.matches(':focus-visible')), true)
      assert.equal(await settings.evaluate(node => getComputedStyle(node).outlineStyle), 'solid')
      checks.push('Settings selection, heading focus, pointer return and keyboard ring')

      if (width === 390) {
        await settings.click()
        await page.locator('.settings-place .setrow').click()
      } else await page.locator('.railround[aria-label="Activity"]').click()
      await page.locator('.activity-action').first().waitFor()
      if (width === 390) assert.equal(await settings.evaluate(node => node.classList.contains('on')), true)
      const activityStyles = await auditControls(page); assertControls(activityStyles)
      assert.equal(await page.locator('.activity-items .tt-row').count(), 0)
      const shapes = await page.locator('.activity-items > .activity-item').evaluateAll(nodes => nodes.map(node => ({ face: node.querySelector('.activity-face').getBoundingClientRect().width, rule: getComputedStyle(node).borderBottomStyle, ruleWidth: getComputedStyle(node).borderBottomWidth })))
      assert.ok(shapes.length > 2); assert.ok(shapes.every(row => row.face === 36 && row.rule === 'solid' && row.ruleWidth === '1px'))
      const event = page.locator('.activity-item').filter({ has: page.locator('.activity-action', { hasText: /^landed$/ }) })
      const wrapping = await event.locator('.activity-summary').evaluate(node => ({ height: node.getBoundingClientRect().height, clamp: getComputedStyle(node).webkitLineClamp, text: node.textContent }))
      assert.equal(wrapping.clamp, '2'); assert.ok(wrapping.height <= 44.1); if (width === 390) assert.ok(wrapping.height > 22)
      assert.ok(wrapping.text.includes(title)); assert.ok(!wrapping.text.endsWith('landed'))
      assert.equal(await event.locator('.activity-time').innerText(), 'landed · 09:50–10:17 · 27 min')
      const colors = await page.locator('.activity-action').evaluateAll(nodes => nodes.map(node => ({ text: node.textContent, color: getComputedStyle(node).color })))
      assert.notEqual(colors.find(row => row.text === 'landed').color, colors.find(row => row.text === 'cut off').color)
      const labels = await page.locator('.activity-day-title, .run-group > .kicker').evaluateAll(nodes => nodes.map(node => { const css = getComputedStyle(node); return [css.fontSize, css.fontWeight, css.letterSpacing, css.textTransform] }))
      assert.ok(labels.length >= 2); assert.ok(labels.every(style => JSON.stringify(style) === JSON.stringify(labels[0])))
      assert.ok((await page.locator('.activity-trailing-time').allTextContents()).every(value => /^\d{1,2}:\d\d$/.test(value)))
      await screenshot(page, `activity-${label}`)
      await event.evaluate(node => node.scrollIntoView({ block: 'center' }))
      if (width === 390) assert.ok((await event.boundingBox()).y + (await event.boundingBox()).height < (await page.locator('.nav').boundingBox()).y)
      await screenshot(page, `earlier-${label}`)
      await page.locator('.activity-item').filter({ has: page.locator('.activity-trailing-time') }).first().press('Enter')
      await page.locator('.tt-thread').waitFor()
      checks.push('Unified history, title wrapping, outcomes, section labels, local times and keyboard thread opening')

      await page.goto(app.url + '/files/system/Engineer/Role.md')
      await page.locator('.listnotice.inline').filter({ hasText: 'That location is no longer shown.' }).waitFor()
      assert.equal(new URL(page.url()).pathname, '/files')
      await page.locator('.fs-rootcard').nth(1).waitFor()
      assert.equal(await page.locator('.fs-rootcard').count(), 2)
      assert.equal(await page.locator('.fs-host-records').count(), 0)
      await page.waitForFunction(() => globalThis.__shapeWalk)
      // A saved shortcut goes through the same soft landing, without a stat.
      await page.evaluate(() => { localStorage.setItem('loam-ui.files', JSON.stringify({ shortcuts: [{ root: 'system', path: 'Logs.md' }] })); __shapeWalk.goFiles(null, '') })
      await page.locator('.fs-shortcut').filter({ hasText: 'Logs.md' }).click()
      await page.locator('.listnotice.inline').filter({ hasText: 'That location is no longer shown.' }).waitFor()
      assert.equal(new URL(page.url()).pathname, '/files')
      assert.equal(await page.locator('.listnotice.inline').count(), 1)
      checks.push('Legacy System URL and shortcut soft landing')
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      assert.deepEqual(errors, [])
      reports.push({ width, theme, mode, controls: listStyles, cardControls: cardStyles, checks, errors })
    } finally { await context.close() }
  }
  // Explicit second zone proves the UI formats instants in the browser's zone.
  const context = await browser.newContext({ timezoneId: 'Asia/Tokyo', locale: 'en-GB' }), page = await context.newPage()
  await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\ngoActivity();' }))
  await page.goto(app.url)
  await page.locator('.activity-action').first().waitFor()
  assert.match(await page.locator('.activity-time').filter({ hasText: 'landed' }).innerText(), /02:50–03:17/)
  await context.close()
  if (out) await writeFile(path.join(out, 'browser.json'), JSON.stringify({ reports, timezoneChecks: ['America/Los_Angeles', 'Asia/Tokyo'] }, null, 2) + '\n')
  console.log(JSON.stringify({ variants: reports.length, timezoneChecks: 2, computedStyles: 'passed', result: 'passed' }))
} finally { await app.close(); await browser.close() }
