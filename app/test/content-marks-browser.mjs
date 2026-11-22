// Exercise focus, hover and pressed states against the fixture host.
import assert from 'node:assert/strict'
import path from 'node:path'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { oneProductFixture } from './one-product-fixture.mjs'
import { filesHost } from './files-host.js'
import { activityFixture } from '../tools/activity-fixture.js'
import { SHAPES as githubShapes } from './fixtures/github.js'

const out = process.env.CONTENT_MARKS_EVIDENCE
if (!out) throw new Error('Set CONTENT_MARKS_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const id = 'a-quieter-week'
const hash = value => createHash('sha256').update(value).digest('hex')
const appSource = await readFile('public/app.js', 'utf8'), reports = [], captures = []
const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    if (process.env.CONTENT_MARKS_CASE && process.env.CONTENT_MARKS_CASE !== `${width}-${mode}`) continue
    const app = await oneProductFixture(), errors = [], styles = []
    const settings = { theme: 'latte', mode, size: 100 }
    app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, { phone: settings, desktop: settings })
    const activity = activityFixture(app.f.agents)
    app.routes['GET /v1/activity'] = (req, res, url) => { const answer = activity.handle('GET', '/v1/activity', url); json(res, answer.body, answer.status) }
    app.routes['GET /v1/apps'] = (req, res) => json(res, { sources: [], apps: [{ name: 'Garden notes', owner: app.f.agents[0].id, source: 'yours', url: 'https://garden.example.org', access: 'private' }] })
    const files = filesHost()
    for (const [key, handler] of Object.entries(files.routes)) if (key.includes('/v1/files/') || key.includes('/v1/limits')) app.routes[key] = handler
    app.routes['GET /v1/integrations/github'] = (req, res) => json(res, githubShapes.installed)
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode,
      isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce' })
    await context.route('**/app.js', r => r.fulfill({ contentType: 'text/javascript', body: appSource + '\nglobalThis.__marks={threads,state,draft,growDraft,schedule,goChats,goFiles,goSettings,goApps,goActivity,closeSheet,running,activity,loadGithub,store};' }))
    const page = await context.newPage(); page.setDefaultTimeout(12000)
    page.on('pageerror', error => errors.push(error.message))
    const settle = async () => { await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) }) }
    const shot = async state => {
      await settle()
      const name = `${state}-${width}-${mode}.png`
      await page.screenshot({ path: path.join(out, name), animations: 'disabled' })
      captures.push({ file: name, sha256: hash(await readFile(path.join(out, name))) })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, state + ' overflow')
    }
    const mark = async (locator, label) => {
      const paint = () => locator.evaluate(n => {
        const s = getComputedStyle(n), r = n.getBoundingClientRect()
        const token = name => { const p = document.createElement('span'); p.style.color = `var(${name})`; n.append(p); const value = getComputedStyle(p).color; p.remove(); return value }
        return { background: s.backgroundColor, color: s.color, border: s.borderTopWidth, shadow: s.boxShadow, outline: s.outlineStyle,
          width: r.width, height: r.height, resting: token('--paper-mark-ink'), active: token('--paper-mark-active') }
      })
      await page.mouse.move(0, 0); await locator.evaluate(n => n.blur()); await settle()
      const rest = await paint()
      assert.equal(rest.background, 'rgba(0, 0, 0, 0)', label)
      assert.equal(rest.border, '0px', label)
      assert.equal(rest.shadow, 'none', label)
      assert.equal(rest.color, rest.resting, label)
      assert.ok(rest.width >= 44 && rest.height >= 44, label + ' hit area')
      await page.keyboard.press('Tab'); await locator.focus(); const focus = await paint()
      assert.equal(focus.background, rest.background, label)
      assert.equal(focus.color, rest.active, label)
      assert.notEqual(focus.outline, 'none', label + ' focus')
      if (width === 1440) {
        await locator.evaluate(n => n.blur()); await locator.hover(); const hover = await paint()
        assert.equal(hover.background, rest.background, label)
        assert.equal(hover.color, rest.active, label)
        await page.mouse.down(); const press = await paint(); await page.mouse.move(0, 0); await page.mouse.up()
        assert.equal(press.background, rest.background, label)
        assert.equal(press.color, rest.active, label)
      }
      await locator.evaluate(n => n.blur()); await page.mouse.move(0, 0)
      styles.push({ label, rest, focus })
    }
    try {
      assert.equal((await fetch(app.url + '/up')).status, 200)
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      await page.getByTitle('A little room for the week', { exact: true }).click()
      await page.locator('.tt-message').first().waitFor()
      await page.evaluate(() => { __marks.draft.value = 'Keep these notes with the plan.'; __marks.growDraft(); __marks.schedule() })
      await page.locator('.expandbtn.on').waitFor()
      await mark(page.locator('.expandbtn'), 'composer expand')
      await shot('composer')
      await page.getByRole('button', { name: 'Write this in a bigger window' }).click()
      await page.locator('#editor-draft').waitFor()
      await page.getByRole('button', { name: 'Done', exact: true }).click()
      await page.evaluate(() => { __marks.goChats(); __marks.threads.st.personaMenu = __marks.state.agents.list[0].id; __marks.schedule() })
      await page.locator('.tt-persona-card').waitFor()
      await mark(page.locator('.tt-persona-card').getByRole('button', { name: 'Profile', exact: true }), 'agent Profile')
      await shot('agent-card')
      await page.evaluate(() => { __marks.threads.st.personaMenu = null; __marks.goFiles('vault', 'Notes') })
      await page.locator('.fs-rowmenu').first().waitFor()
      await mark(page.locator('.fs-rowmenu').first(), 'Files row menu')
      await mark(page.locator('.fs-more'), 'Files load more')
      await shot('files')
      await page.evaluate(async () => { await __marks.loadGithub(); __marks.goSettings('integration', 'github') })
      await page.getByText('Manage all GitHub accounts', { exact: true }).click()
      await page.locator('.ghpill').first().waitFor()
      await mark(page.locator('.ghpill').first(), 'Settings integration action')
      await shot('settings-detail')
      await page.evaluate(() => { __marks.store.files = { ...__marks.store.files, shortcuts: [{ root: 'vault', path: 'Notes' }] }; __marks.goSettings('files') })
      await page.getByRole('button', { name: 'Remove shortcut to Notes', exact: true }).waitFor()
      await mark(page.getByRole('button', { name: 'Remove shortcut to Notes', exact: true }), 'Settings shortcut removal')
      await shot('settings-files')
      await page.evaluate(() => { __marks.closeSheet(); __marks.goApps() })
      await page.locator('.apps-row').waitFor()
      assert.equal(await page.locator('.apps-row .iconbtn').count(), 0, 'Apps content has no filled auxiliary buttons')
      await shot('apps')
      await page.evaluate(() => __marks.goActivity())
      await page.locator('.activity-cancel').first().waitFor()
      await mark(page.locator('.activity-cancel').first(), 'Activity Cancel')
      if (await page.locator('.run-row-end button').count()) await mark(page.locator('.run-row-end button').first(), 'Activity End')
      await shot('activity')
      if (await page.locator('.run-row-end button').count()) {
        await page.evaluate(() => { __marks.running.st.confirm = __marks.running.rows()[0].id; __marks.schedule() })
        await page.locator('.run-confirm .primary').first().waitFor()
        await mark(page.locator('.run-confirm').first().getByRole('button', { name: 'Keep running', exact: true }), 'Activity keep running')
        assert.ok(await page.locator('.run-confirm .primary').first().evaluate(n => {
          const probe = document.createElement('span'); probe.style.color = 'var(--latte-espresso-ink)'; n.append(probe)
          const correct = getComputedStyle(n).color === getComputedStyle(probe).color; probe.remove(); return correct
        }), 'confirmation uses the Primary’s own ink')
        await shot('activity-confirm')
      }
      assert.deepEqual(errors, [])
      reports.push({ width, mode, styles, pageErrors: errors })
      console.log('PASS', width, mode, 'content controls, focus and hover')
    } catch (error) {
      console.error(await page.locator('body').innerText())
      throw error
    } finally { await context.close(); await app.close() }
  }
} finally { await browser.close() }
await writeFile(path.join(out, 'browser.json'), JSON.stringify(reports, null, 2) + '\n')
await writeFile(path.join(out, 'captures.json'), JSON.stringify(captures, null, 2) + '\n')
