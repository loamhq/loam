import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fixtureApp, NOW } from '../docs/design-system/fixture.mjs'

const out = process.env.BUTTON_SCREENS_EVIDENCE
if (!out) throw new Error('Set BUTTON_SCREENS_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const source = await readFile('public/app.js', 'utf8'), reports = []
const original = JSON.parse(await readFile('docs/evidence/button-tiers/screens/controls.json', 'utf8'))
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const app = await fixtureApp(), errors = []
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode, isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce' })
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__screens={theme,threads,state,files,picker,activity,schedules,goHome,goFiles,goApps,goActivity,goSettings,closeSheet,openSheet,openEditor,schedule};' }))
    const page = await context.newPage(); page.setDefaultTimeout(10000)
    page.on('pageerror', e => errors.push(e.message)); await page.clock.setFixedTime(new Date(NOW))
    const close = async () => { await page.evaluate(() => { __screens.closeSheet(); __screens.state.pop = null; __screens.files.st.sheet = null; __screens.activity.st.confirm = null; __screens.schedule() }); await page.waitForTimeout(100) }
    const check = async (name, expectedPrimary, expectedSecondary = 0) => {
      await page.evaluate(() => document.fonts.ready); await page.waitForTimeout(220)
      const controls = await page.evaluate(() => {
        const rgb = token => { const n = document.createElement('span'); n.style.color = `var(${token})`; document.body.append(n); const c = getComputedStyle(n).color; n.remove(); return c }
        const espresso = ['--latte-espresso','--latte-espresso-hover','--latte-espresso-pressed'].map(rgb), foam = ['--latte-foam','--latte-hover'].map(rgb)
        return [...document.querySelectorAll('button,a.pillbtn')].filter(n => n.getClientRects().length && getComputedStyle(n).visibility !== 'hidden' && !n.closest('[inert]')).map(n => {
          const s = getComputedStyle(n), r = n.getBoundingClientRect()
          return { label: n.getAttribute('aria-label') || n.title || n.textContent.trim(), class: n.className, tier: n.closest('.nav,.rail') || n.matches('.accounttile') ? 'navigation' : espresso.includes(s.backgroundColor) ? 'primary' : foam.includes(s.backgroundColor) ? 'secondary' : 'Newspaper', disabled: n.disabled || n.getAttribute('aria-disabled') === 'true', color: s.color, background: s.backgroundColor, rect: { x: r.x, y: r.y, width: r.width, height: r.height } }
        })
      })
      const primary = controls.filter(c => c.tier === 'primary' && !c.disabled), secondary = controls.filter(c => c.tier === 'secondary' && !c.disabled)
      assert.equal(primary.length, expectedPrimary, `${name}: ${JSON.stringify(controls)}`)
      assert.equal(secondary.length, expectedSecondary, `${name}: ${JSON.stringify(secondary)}`)
      if (name.startsWith('chats')) {
        assert.equal(controls.find(c => c.label === 'New thread').tier, 'secondary')
        assert.equal(controls.find(c => c.label === 'Search threads').tier, 'Newspaper')
        const reference = original.find(r => r.name === name && r.width === width && r.mode === mode)
        for (const label of ['New thread', 'Search threads']) {
          const a = reference.controls.find(c => c.label === label), b = controls.find(c => c.label === label)
          for (const key of ['x', 'y', 'width', 'height']) assert.ok(Math.abs(a.rect[key] - b.rect[key]) < .1, `${name} ${width} ${mode} ${label}: ${key} must match 395f191`)
        }
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, name)
      if (process.env.BUTTON_CAPTURES !== '0' && (process.env.BUTTON_CAPTURES !== 'chats' || name === 'chats')) await page.screenshot({ path: path.join(out, `${name}-${width}-${mode}.png`), animations: 'disabled' })
      reports.push({ name, width, mode, controls }); console.log(`PASS ${name} ${width} ${mode}: ${primary.length} primary, ${secondary.length} secondary${name.startsWith('chats') ? '; header geometry matches 395f191' : ''}`)
    }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      await page.evaluate(mode => __screens.theme.set({ mode, theme: 'latte' }), mode)
      await check('chats', 0, 1)
      await page.getByRole('button', { name: 'Search threads', exact: true }).click(); await check('chats-search', 0, 1)
      await page.evaluate(() => __screens.goFiles(null, '')); await page.locator('.fs-rootcard').first().waitFor(); await check('files', 0)
      await page.evaluate(() => __screens.goFiles('vault', 'Notes/plan.md', { isFile: true })); await page.locator('.fs-edtitle').waitFor(); await check('file-preview', 0)
      await page.getByRole('button', { name: 'Edit', exact: true }).last().click(); await check('file-edit', 0)
      await page.evaluate(() => __screens.goApps()); await page.locator('.apps-page').waitFor(); await check('apps', 1)
      await page.evaluate(() => __screens.goActivity()); await page.locator('.activity-page').waitFor(); await check('activity', 0)
      await page.evaluate(() => { const a = __screens.activity; a.st.confirm = { job: a.st.now.slots.find(s => !s.free), queued: false, busy: false, error: '' }; __screens.schedule() }); await page.locator('dialog[open]').waitFor(); await check('cancel-job', 1)
      await close()
      for (const section of ['root', 'appearance', 'agents', 'providers', 'files', 'plugins', 'voice']) {
        await page.evaluate(section => __screens.goSettings(section), section); await page.locator('.sheet.settings').waitFor(); await page.waitForTimeout(200)
        await check(`settings-${section}`, section === 'agents' ? 1 : 0)
      }
      await close(); await page.evaluate(() => __screens.goHome()); await page.locator('.tt-row').first().waitFor()
      await page.evaluate(() => __screens.threads.open('a-quieter-week')); await page.locator('#draft').waitFor()
      await page.locator('#draft').fill('The next step.'); await page.evaluate(() => __screens.openEditor()); await page.locator('#editor-draft').waitFor(); await check('expanded-composer', 1)
      assert.deepEqual(errors, [])
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, 'controls.json'), JSON.stringify(reports, null, 2) + '\n')
} finally { await browser.close() }
