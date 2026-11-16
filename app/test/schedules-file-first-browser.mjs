// Real page, Files editor, styles and relay; supplied broker fixtures only.
import assert from 'node:assert/strict'
import path from 'node:path'
import { mkdir, writeFile } from 'node:fs/promises'
import { appAgainst, closing, until } from './harness.js'
import { schedulesHost } from './schedules-host.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright-core')
const output = process.env.SCHEDULE_FILE_EVIDENCE
if (!output) throw new Error('Set SCHEDULE_FILE_EVIDENCE to a directory outside the repository')
const relative = path.relative(process.cwd(), path.resolve(output))
if (!relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative)) throw new Error('Evidence must stay outside the repository')
await mkdir(output, { recursive: true })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-gpu'] })
const results = []
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    const broker = await schedulesHost({ fileFirst: true }), app = await appAgainst(broker)
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, timezoneId: 'America/Los_Angeles', locale: 'en-US', isMobile: width === 390, hasTouch: width === 390 })
    try {
      const page = await context.newPage(), errors = [], checks = []
      page.on('pageerror', (e) => errors.push(e.message))
      const shot = async (name) => {
        await page.evaluate(() => document.fonts.ready)
        await page.screenshot({ path: path.join(output, `${name}-${width}-${theme}.png`) })
      }
      const noOverflow = async () => {
        const overflow = await page.evaluate(() => [...document.querySelectorAll('body, .schedule-item, .fs-file, .fs-schedule-save')].filter((n) => n.scrollWidth > n.clientWidth + 1).map((n) => ({ class: n.className, width: n.clientWidth, scroll: n.scrollWidth, text: n.innerText })))
        if (overflow.length) await shot('overflow')
        assert.deepEqual(overflow, [])
      }
      assert.equal((await page.request.get(`${app.url}/up`)).status(), 200)
      await page.goto(app.url)
      await page.locator('.persona').filter({ hasText: 'Scout' }).click()
      await page.getByRole('button', { name: 'Profile', exact: true }).click()
      await page.locator('.segmented button').filter({ hasText: /^Schedule$/ }).click()
      await page.locator('.schedule-item').nth(3).waitFor()
      await page.waitForFunction(() => !document.querySelector('.schedule-dot'))
      assert.equal(broker.fake.activities.size > 0, true)
      assert.equal([...broker.fake.activities.values()].every((e) => e.seen), true)
      checks.push('tab entry marks existing changes/refusals on the host')
      const live = page.locator('.schedule-item').filter({ has: page.locator('.schedule-title', { hasText: /^reflection$/ }) })
      // A fresh edit arriving after tab entry should raise one dot, without
      // another seen write. Keep timestamps current for the evidence only.
      for (const r of broker.fake.rows.values()) if (r.last_edit) r.last_edit.ts = new Date(Date.now() - 180000).toISOString()
      const row = broker.fake.rows.get('scout/drafts')
      const event = { id: 'later-edit', agent: row.agent, record: row.name, kind: 'change', seen: false }
      broker.fake.activities.set(event.id, event)
      row.last_edit = { id: event.id, ts: new Date(Date.now() - 60000).toISOString(), actor: 'scout', by: 'agent', lane: 'conversation', taken: true }
      await page.locator('.schedule-item.off .schedule-dot').waitFor({ timeout: 12000 })
      assert.equal(await page.locator('.schedule-item .schedule-dot').count(), 1)
      assert.equal(broker.fake.activities.get(event.id).seen, false)
      assert.equal(await page.locator('.schedule-editor').count(), 0)
      assert.equal(await page.getByText('Edit schedule', { exact: true }).count(), 0)
      const broken = page.locator('.schedule-item.broken')
      assert.match(await broken.innerText(), /Broken · line 2 · previous version still runs/)
      assert.match(await page.locator('.schedules').innerText(), /Edited .* by (you|the agent|an operator)/)
      const colors = await page.locator('.schedule-item').evaluateAll((nodes) => nodes.map((n) => ({ state: n.className, color: getComputedStyle(n.querySelector('.schedule-state')).color })))
      assert.notEqual(colors.find((r) => r.state.includes('broken')).color, colors.find((r) => r.state.includes('live')).color)
      const rows = await page.locator('.schedule-item').evaluateAll((nodes) => nodes.map((n) => ({ name: n.querySelector('.schedule-title').textContent, height: n.getBoundingClientRect().height, lineHeight: n.querySelector('.schedule-open').getBoundingClientRect().height })))
      if (width === 1440) for (const row of rows) assert.ok(row.lineHeight <= 34, `Expected one desktop line: ${JSON.stringify(row)}`)
      await shot('schedules'); await noOverflow()
      checks.push('live/off/broken/refused words, previous version, relative edit actors, one later-change dot, error color, no detail form')
      const toggle = live.getByRole('switch')
      await toggle.focus(); await page.keyboard.press('Space')
      await page.locator('.schedule-item.off').filter({ hasText: 'reflection' }).waitFor()
      assert.equal(await toggle.evaluate((n) => n === document.activeElement), true)
      await page.keyboard.press('Enter')
      await page.locator('.schedule-item.live').filter({ hasText: 'reflection' }).waitFor()
      checks.push('keyboard switch sends enabled and retains focus')
      await live.getByRole('button', { name: 'Run reflection now', exact: true }).click()
      await live.getByText('Run requested.', { exact: true }).waitFor()
      assert.equal(broker.fake.rows.get('scout/reflection').last_fire, null)
      checks.push('Run now requests only; no invented outcome')
      await broken.getByRole('switch').click()
      await broken.getByRole('alert').waitFor()
      assert.match(await broken.getByRole('alert').innerText(), /cannot parse schedule 'weekdays at 7am'/)
      assert.equal(await broken.getByRole('switch').getAttribute('aria-checked'), 'true')
      checks.push('broken toggle shows exact refusal and keeps the switch on')
      await broken.locator('.schedule-open').click()
      await page.locator('.fs-file').waitFor()
      assert.match(page.url(), /\/files\/spaces\/scout\/crons\/morning-digest.md$/)
      await page.getByRole('button', { name: 'Edit', exact: true }).click()
      const field = page.locator('.fs-field')
      await field.fill(broker.files.goodScheduleText)
      await field.press('Control+s')
      await page.locator('.fs-schedule-save').getByText('Saved · runs weekdays at 07:00 PT', { exact: true }).waitFor()
      assert.equal(broker.files.puts.at(-1).text, broker.files.goodScheduleText)
      await field.fill(broker.files.brokenScheduleText)
      await field.press('Control+s')
      const feedback = page.locator('.fs-schedule-save')
      await feedback.getByText('Line 2 · schedule: weekdays at 7am', { exact: true }).waitFor()
      assert.match(await feedback.innerText(), /Saved · Not running this file.*previous version still runs/s)
      assert.equal(await field.inputValue(), broker.files.brokenScheduleText)
      assert.equal(await page.locator('.banner-retry').count(), 0)
      assert.equal(broker.files.puts.at(-1).text.includes('last_modified_by'), false)
      await noOverflow(); await shot('files-broken-save')
      checks.push('row opens exact Files path; revisioned good/broken saves keep bytes, no stamp, saved failure line and prior-version status')
      // Direct URL opens also mark seen (including a change made while Files
      // was closed); a read-only instruction remains governed by capabilities.
      const before = broker.seen.filter((r) => r.path.endsWith('/seen')).length
      await page.goto(`${app.url}/files/spaces/scout/crons/drafts.md`)
      await page.locator('.fs-file').waitFor()
      await page.waitForFunction(() => !document.querySelector('.fs-quiet'))
      await until(() => broker.fake.activities.get(event.id).seen)
      assert.equal(broker.fake.activities.get(event.id).seen, true)
      assert.ok(broker.seen.filter((r) => r.path.endsWith('/seen')).length > before)
      checks.push('direct Files URL marks schedule seen')
      assert.deepEqual(errors, [])
      results.push({ width, theme, checks, errors, colors, rows,
        calls: broker.seen.filter((r) => /schedules|files/.test(r.path)).map(({ method, path, search, body }) => ({ method, path, search, body })) })
    } finally {
      await context.close()
      for (const stream of broker.streams) stream.end()
      await closing(broker, app)()
    }
  }
  await writeFile(path.join(output, 'checks.json'), JSON.stringify(results, null, 2) + '\n')
  console.log(JSON.stringify({ passed: results.length, screenshots: 8, errors: results.flatMap((r) => r.errors) }))
} finally { await browser.close() }
