// Local app and relay with contract fixtures; no host broker or real agent.
import assert from 'node:assert/strict'
import path from 'node:path'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { oneProductFixture } from './one-product-fixture.mjs'
import { filesHost } from './files-host.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const output = process.env.ACTIVITY_EVIDENCE
if (output) await mkdir(output, { recursive: true })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const app = await oneProductFixture(), results = []
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
const values = { phone: {}, desktop: {} }, streams = new Set()
let sessions = [], jobs = { slots: [], landing: [], queue: [] }, jobCount = 0
const live = () => ({ sessions, box: { sessions: { live: sessions.length }, units: { engineer_jobs: { count: jobCount } } } })
app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, values)
app.routes['GET /v1/sessions/live'] = (req, res) => json(res, live())
app.routes['GET /v1/sessions/live/events'] = (req, res) => {
  streams.add(res); res.on('close', () => streams.delete(res))
  res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n')
}
app.routes['GET /v1/activity'] = (req, res) => json(res, { jobs, next_offset: null, total: 0, items: [] })
app.routes['GET /v1/apps'] = (req, res) => json(res, { apps: [], sources: ['yours'] })
for (const [key, handler] of Object.entries(filesHost().routes)) if (key.includes('/v1/files/')) app.routes[key] = handler
const emit = () => { for (const stream of streams) stream.write(`data: ${JSON.stringify({ type: 'sessions.live', ...live() })}\n\n`) }
const sections = ['Appearance', 'Agents', 'Accounts', 'Voice', 'Files', 'Plugins']
try {
  for (const width of [320, 390, 1440]) for (const theme of ['latte', 'plain']) for (const mode of ['light', 'dark']) {
    const phone = width < 1100, label = `${width}-${theme}-${mode}`
    values[phone ? 'phone' : 'desktop'] = { theme, mode, size: 100 }
    jobs = { slots: [], landing: [], queue: [] }; jobCount = 0
    sessions = ['working', 'working', 'idle', 'asking', 'background', 'idle', 'background'].map((state, i) => ({ id: `working-${i}`, kind: 'thread', thread_id: 'a-quieter-week', thread_title: 'A quieter week', agent: app.f.agents[i % app.f.agents.length].id, state }))
    const context = await browser.newContext({ viewport: { width, height: phone ? 844 : 1000 }, colorScheme: mode, isMobile: phone, hasTouch: phone, reducedMotion: 'reduce' })
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__activityNavigation = { activity };' }))
    const page = await context.newPage(), errors = [], checks = []
    page.setDefaultTimeout(10000)
    page.on('pageerror', error => errors.push(error.message))
    const settings = page.locator('.settings-utility'), activityRow = page.locator('.settings-place .setrow')
    const quietNavigation = async () => {
      assert.equal(await page.locator('.activity-live').count(), 0)
      assert.equal(await page.getByRole('img', { name: 'Work running', exact: true }).count(), 0)
      const buttons = page.locator(phone ? '.settings-utility' : '.rail-foot button')
      assert.deepEqual(await buttons.evaluateAll(nodes => nodes.map(node => [...node.children].map(child => child.tagName.toLowerCase()))), phone ? [['svg']] : [['svg'], ['svg']])
    }
    const shot = async name => {
      await page.evaluate(() => document.fonts.ready)
      if (output) await page.screenshot({ path: path.join(output, `${name}-${label}.png`), animations: 'disabled' })
    }
    const openActivity = async (keyboard = false) => {
      if (phone) {
        await settings.click(); await activityRow.waitFor()
        assert.equal(await activityRow.locator('.setrow-sub').innerText(), sessions.filter(row => row.state === 'working').length + jobCount ? `${sessions.filter(row => row.state === 'working').length + jobCount} working` : 'Nothing running')
        if (keyboard) { await activityRow.focus(); await page.keyboard.press('Enter') }
        else await activityRow.click()
      } else await page.locator('.railround[title="Activity"]').click()
      await page.locator('.activity-page').waitFor()
      await page.locator('.sheet.settings').waitFor({ state: 'detached' })
      assert.equal(await page.locator(phone ? '.settings-utility.on' : '.railround.on[title="Activity"]').count(), 1)
    }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      assert.equal((await page.request.get(`${app.url}/up`)).status(), 200)
      assert.equal(await page.locator('html').getAttribute('data-theme'), mode)
      if (phone) {
        assert.deepEqual(await page.locator('.navpill button').allTextContents(), ['Chats', 'Files', 'Apps'])
        assert.equal(await page.locator('.nav button').count(), 4)
        assert.equal(await page.getByRole('button', { name: 'Activity', exact: true }).count(), 0)
        const geometry = await page.locator('.nav button').evaluateAll(nodes => nodes.map(node => { const r = node.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height, right: r.right } }))
        assert.ok(geometry.every((r, i) => r.x >= 12 && r.right <= width - 12 + 1 && r.width >= 44 && r.height >= 44 && (!i || r.x >= geometry[i - 1].right)), JSON.stringify(geometry))
        checks.push({ fourControls: geometry })
        await shot('bar')
        await settings.click(); await activityRow.waitFor()
        assert.deepEqual(await page.locator('.settings .setrow-title').allTextContents(), ['Activity', ...sections])
        assert.equal(await activityRow.locator('.icon-tile svg').count(), 1)
        await page.waitForFunction(() => document.querySelector('.settings-place .setrow-sub')?.textContent === '2 working')
        assert.equal(await activityRow.locator('.setrow-sub').innerText(), '2 working')
        await quietNavigation()
        const spacing = await page.locator('.settings-place').evaluate(node => node.nextElementSibling.getBoundingClientRect().top - node.getBoundingClientRect().bottom)
        assert.ok(spacing >= 14 && spacing <= 32, String(spacing))
        await shot('settings')
        await activityRow.focus(); await page.keyboard.press('Enter')
        await page.locator('.activity-page').waitFor()
        await page.locator('.sheet.settings').waitFor({ state: 'detached' })
        assert.equal(await settings.getAttribute('aria-current'), 'page')
        assert.equal(await settings.getAttribute('aria-expanded'), 'false')
        assert.ok(await settings.evaluate(node => node.classList.contains('on')))
        await shot('activity')
        await page.getByRole('button', { name: 'Back from Activity' }).click()
        await page.locator('.tt-row').first().waitFor()
        assert.equal(await settings.getAttribute('aria-current'), null)
        assert.equal(await settings.evaluate(node => node.classList.contains('on')), false)
        checks.push('clock row first and separated, quiet summary, keyboard opens and closes sheet, Settings selected, Back returns to Chats')
      } else {
        assert.equal(await page.locator('.rail-foot button').count(), 2)
        assert.deepEqual(await page.locator('.rail-foot button').evaluateAll(nodes => nodes.map(node => node.title)), ['Activity', 'Settings'])
        await settings.click(); await page.locator('.settings .navrow').first().waitFor()
        assert.deepEqual(await page.locator('.settings .navrow').allTextContents(), sections)
        assert.equal(await page.locator('.settings-place').count(), 0)
        await page.getByRole('button', { name: 'Close settings' }).click()
        await openActivity()
        assert.equal(await page.getByRole('button', { name: 'Back from Activity' }).count(), 0)
        assert.equal(await settings.evaluate(node => node.classList.contains('on')), false)
        await page.locator('.run-row').first().waitFor()
        await quietNavigation()
        await shot('desktop')
        checks.push('desktop retains separate Activity and Settings, six Settings sections and Activity selection; no indicator while sessions work')
      }
      // Resident idle/asking/background sessions do not count as working.
      if (phone) {
        await settings.click(); await activityRow.waitFor()
        sessions = sessions.map(row => ({ ...row, state: 'idle' })); emit()
        await page.waitForFunction(() => document.querySelector('.settings-place .setrow-sub')?.textContent === 'Nothing running')
        await quietNavigation()
        await page.locator('.settings .sheet-x').click()
        checks.push('seven resident sessions count only two working; all idle says Nothing running; navigation stays quiet')
      }
      sessions = []; emit()
      if (phone) {
        await settings.click(); await activityRow.waitFor()
        await page.waitForFunction(() => document.querySelector('.settings-place .setrow-sub')?.textContent === 'Nothing running')
        jobs = { slots: [{ free: false, job: 'fixture-job', title: 'Review notes', agent: app.f.agents[0].id }], landing: [], queue: [] }; jobCount = 1; emit()
        await page.evaluate(() => __activityNavigation.activity.load())
        await page.waitForFunction(() => document.querySelector('.settings-place .setrow-sub')?.textContent === '1 working')
        assert.equal(await activityRow.locator('.setrow-sub').innerText(), '1 working')
        await quietNavigation()
        assert.equal(await settings.locator('.activity-count').count(), 0)
        jobs = { slots: [], landing: [{ job: 'fixture-job', title: 'Review notes' }], queue: [] }
        await page.evaluate(() => __activityNavigation.activity.load())
        await page.waitForFunction(() => document.querySelector('.settings-place .setrow-sub')?.textContent === '1 working')
        assert.equal(await activityRow.locator('.setrow-sub').innerText(), '1 working')
        await quietNavigation()
        jobs = { slots: [{ free: true }], landing: [], queue: [{ job: 'queued', title: 'Queued work' }] }; jobCount = 0; emit()
        await page.evaluate(() => __activityNavigation.activity.load())
        await page.waitForFunction(() => document.querySelector('.settings-place .setrow-sub')?.textContent === 'Nothing running')
        assert.equal(await activityRow.locator('.setrow-sub').innerText(), 'Nothing running')
        await quietNavigation()
        await page.locator('.settings .sheet-x').click()
        checks.push('SSE clears sessions; jobs and landing update the summary without an indicator; duplicate host totals, free slots, queue and old history add no work')
        await page.locator('.navbtn').filter({ hasText: /^Apps$/ }).click()
        await page.locator('.apps-page').waitFor()
        await openActivity(true)
        // Opening Settings again from Activity must keep the original return place.
        await openActivity()
        await page.getByRole('button', { name: 'Back from Activity' }).click()
        await page.locator('.apps-page').waitFor()
        for (const destination of ['/files/vault', '/files/vault/Notes']) {
          await page.goto(app.url + destination)
          await page.locator('.fs-entry').first().waitFor()
          await openActivity()
          await page.getByRole('button', { name: 'Back from Activity' }).click()
          await page.waitForURL(app.url + destination)
          await page.locator('.fs-entry').first().waitFor()
        }
        checks.push('Back returns to Apps and two Files folder locations; reopening from Activity preserves original return place')
      }
      assert.equal(app.broker.seen.some(call => call.path === '/v1/activity/seen'), false)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      assert.deepEqual(errors, [])
      results.push({ width, theme, mode, checks, errors })
    } finally { await context.close() }
  }
  if (output) await writeFile(path.join(output, 'navigation-checks.json'), JSON.stringify(results, null, 2) + '\n')
  console.log(JSON.stringify({ passed: results.length, errors: results.flatMap(row => row.errors), source: 'local contract fixtures', result: 'passed' }))
} finally { await browser.close(); await app.close() }
