// Before/after captures use the same synthetic answers through the real relay.
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { profileHost } from './agent-profile-host.mjs'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const stage = process.env.PROFILE_POLISH_STAGE || 'after'
const out = `${process.env.PROFILE_POLISH_EVIDENCE || process.env.TMPDIR + '/profile-polish'}/${stage}`
await mkdir(out, { recursive: true })
const app = await profileHost(), browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const source = await readFile('public/app.js', 'utf8'), reports = []
const baseline = process.env.PROFILE_POLISH_BASELINE || 'edcec29'
const original = stage === 'before' ? ['agent-profile.js', 'activity.js', 'threads.js', 'style.css'].map(file => ({ file, body: execFileSync('git', ['show', `${baseline}:public/${file}`], { encoding: 'utf8' }) })) : []
const rawApplies = 'New threads and members brought in start on the provider. The model and thinking level apply to every turn on that provider for which the thread chose no model.'
const rawInstructions = "in the project's own repository, which its engineer edits; Files does not reach it"
for (const p of app.profiles.values()) {
  p.defaults.applies = rawApplies
  p.defaults.not_honoured = ['Wakes and crons stay on Claude Code for other providers.']
}
app.engineers.engineers.find(row => row.project === 'Notebook').instructions.note = rawInstructions
// More than four mixed entries, including a long job title and nested-thread context.
const jobs = Array.from({ length: 6 }, (_, i) => ({ id: `polish-job-${i}`, kind: 'job', agent: 'sprout', briefed_by: 'sprout', project: 'Notebook', thread: 'a-quieter-week', thread_title: 'A little room for the week',
  title: i === 0 ? 'A calmer project page with room for all the details and a very long engineering title' : `A useful improvement ${i + 1}`, outcome: 'completed', landing: 'landed', ts: new Date(Date.now() - (i + 1) * 60000).toISOString(), engine: 'codex', model: 'code-fast' }))
app.routes['GET /v1/activity'] = (req, res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ items: jobs, jobs: { slots: [], queue: [], landing: [] }, total: jobs.length, next_offset: null })) }
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: { width, height: 940 }, colorScheme: mode, timezoneId: 'America/Los_Angeles' })
    const page = await context.newPage(), errors = []
    page.on('pageerror', e => errors.push(e.message))
    await page.route('**/app.js*', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__polish={state,goProfile,goSettings,profiles,profileActivity,schedule};' }))
    for (const { file, body } of original) await page.route(`**/${file}*`, route => route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body }))
    await page.goto(app.url)
    await page.waitForFunction(() => globalThis.__polish?.state.agentsReady)
    await page.evaluate(() => __polish.goProfile('sprout'))
    await page.getByRole('button', { name: 'Rename Sprout', exact: true }).waitFor()
    await page.waitForFunction(() => __polish.profileActivity.st.loaded)
    await page.evaluate(() => document.fonts.ready)
    const group = name => page.getByRole('region', { name, exact: true })
    const shot = async (name, locator) => { await locator.evaluate(node => node.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' })); await locator.screenshot({ path: `${out}/${name}-${width}-${mode}.png` }) }
    await shot('runs-on', group('Runs on'))
    await shot('who', group('Who Sprout is'))
    await shot('schedule', group('Schedule'))
    await shot('manage-schedules', page.getByRole('button', { name: 'Manage schedules', exact: true }))
    await shot('recent', group('Recent'))
    await page.locator('.profile-body').evaluate(n => n.scrollTop = n.scrollHeight)
    await page.screenshot({ path: `${out}/profile-lower-${width}-${mode}.png` })
    if (stage === 'after') {
      assert.equal(await group('Runs on').locator('.footnote').first().textContent(), 'The default for new threads, wakes and scheduled runs. Any thread can still choose its own.')
      assert.equal(await group('Runs on').locator('.profile-limit-note').textContent(), 'Wakes and scheduled runs stay on Claude Code for now.')
      assert.equal(await page.getByText(rawApplies, { exact: false }).count(), 0)
      assert.equal(await group('Recent').locator('.profile-recent-row').count(), 4)
      assert.equal(await group('Recent').locator('.activity-day-title, .activity-job-context, a, strong, button button').count(), 0)
      assert.equal(await group('Schedule').getByRole('button', { name: 'Manage schedules', exact: true }).count(), 1)
      const who = group('Who Sprout is')
      const filePath = await who.getByRole('button', { name: 'Soul', exact: true }).locator('svg path').first().getAttribute('d')
      assert.equal(filePath, await who.getByRole('button', { name: 'Config', exact: true }).locator('svg path').first().getAttribute('d'))
      const folderPath = await who.getByRole('button', { name: 'Skills 4', exact: true }).locator('svg path').first().getAttribute('d')
      assert.notEqual(filePath, folderPath)
      assert.equal(folderPath, await who.locator('.profile-memory summary svg path').first().getAttribute('d'))
      const geometry = await group('Recent').locator('.profile-recent-row').evaluateAll(rows => rows.map(row => {
        const r = row.getBoundingClientRect(), t = row.querySelector('time').getBoundingClientRect(), title = row.querySelector('.setrow-title'), sub = row.querySelector('.setrow-sub')
        return { rightInset: r.right - t.right, titleHeight: title.getBoundingClientRect().height, subHeight: sub.getBoundingClientRect().height, height: r.height }
      }))
      assert.ok(geometry.every(r => r.rightInset >= 12 && r.titleHeight < 28 && r.subHeight < 24 && r.height >= 44), JSON.stringify(geometry))
      // The existing thread and Activity detail destinations still open.
      await group('Recent').locator('.profile-recent-row').first().click()
      await page.locator('.tt-conversation').waitFor()
      await page.evaluate(() => __polish.goProfile('sprout'))
      await page.waitForFunction(() => __polish.profileActivity.st.loaded)
      await group('Recent').locator('.profile-recent-row').filter({ hasText: 'Engineer job' }).first().click()
      await page.locator('.activity-detail[open]').waitFor()
      await page.getByRole('button', { name: 'Close', exact: true }).last().click()
      await page.getByRole('button', { name: 'All activity', exact: true }).click()
      await page.getByRole('group', { name: 'Activity filters', exact: true }).waitFor()
      assert.ok(await page.locator('.activity-day-title').count() > 0)
    }
    await page.evaluate(() => __polish.goSettings('agents'))
    await page.getByRole('button', { name: 'Notebook Codex', exact: false }).click()
    await page.getByText('Notebook engineer', { exact: true }).waitFor()
    await shot('engineer-instructions', group('Instructions'))
    await shot('engineer-runs-on', page.locator('.settings').getByRole('region', { name: 'Runs on', exact: true }))
    if (stage === 'after') {
      const instructions = group('Instructions')
      assert.equal(await instructions.locator('button, a, svg, [role="button"]').count(), 0)
      assert.equal(await instructions.locator('.setrow-sub').textContent(), "Kept in the project's code, outside Files")
      assert.equal(await instructions.getByText(rawInstructions, { exact: false }).count(), 0)
    }
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)
    assert.equal(overflow, false); assert.deepEqual(errors, [])
    reports.push({ width, mode, overflow, errors })
    await context.close()
  }
  await writeFile(`${out}/checks.json`, JSON.stringify({ stage, ...(stage === 'before' ? { baseline } : {}), browser: browser.version(), reports }, null, 2) + '\n')
  console.log(`PASS ${stage}: 390/1440 light/dark; eight captures per combination; ${stage === 'after' ? 'copy, compact rows, navigation, spacing and static instructions verified' : 'landed baseline captured'}`)
} finally { await browser.close(); await app.close() }
