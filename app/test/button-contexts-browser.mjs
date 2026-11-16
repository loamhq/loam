// Competing inline actions: real app/controllers, synthetic provider/profile data.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { u4Fixture } from './release-u4-fixture.mjs'
const out = process.env.BUTTON_CONTEXTS_EVIDENCE
if (!out) throw new Error('Set BUTTON_CONTEXTS_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const source = await readFile('public/app.js', 'utf8'), reports = []
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) for (const fresh of [true, false]) {
    const app = await u4Fixture({ fresh }), errors = []
    app.routes['GET /v1/integrations/github/accounts'] = (req, res) => {
      res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ accounts: [{ account: 'example-account', kind: 'personal', status: 'authorized' }] }))
    }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode, reducedMotion: 'reduce' })
    await context.addInitScript(() => { globalThis.SpeechRecognition ??= class {} })
    await context.route('https://provider.example/**', route => route.fulfill({ contentType: 'text/html', body: '<p>Synthetic sign-in</p>' }))
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__contexts={theme,state,profiles,threads,goProfile,goSettings,schedule};' }))
    const page = await context.newPage(); page.setDefaultTimeout(10000); page.on('pageerror', e => errors.push(e.message))
    const check = async (name, primary) => {
      await page.evaluate(() => document.fonts.ready); await page.waitForTimeout(200)
      const controls = await page.evaluate(() => {
        const n = document.createElement('span'); document.body.append(n)
        const color = token => { n.style.color = `var(${token})`; return getComputedStyle(n).color }
        const paints = ['--latte-espresso', '--latte-espresso-hover', '--latte-espresso-pressed'].map(color); n.remove()
        return [...document.querySelectorAll('button,a.pillbtn')].filter(n => n.getClientRects().length && !n.disabled && n.getAttribute('aria-disabled') !== 'true').map(n => ({ label: n.getAttribute('aria-label') || n.textContent.trim(), class: n.className, navigation: !!n.closest('.nav,.rail') || n.matches('.accounttile'), primary: !n.closest('.nav,.rail') && !n.matches('.accounttile') && paints.includes(getComputedStyle(n).backgroundColor) }))
      })
      assert.equal(controls.filter(c => c.primary).length, primary, `${name}: ${JSON.stringify(controls.filter(c => c.primary))}`)
      if (process.env.BUTTON_CAPTURES !== '0') await page.screenshot({ path: path.join(out, `${name}-${width}-${mode}.png`) })
      reports.push({ name, width, mode, controls }); console.log(`PASS ${name} ${width} ${mode}: ${primary} primary`)
    }
    try {
      await page.goto(app.url); await page.waitForFunction(() => globalThis.__contexts?.state.agentsReady)
      await page.evaluate(mode => __contexts.theme.set({ mode, theme: 'latte' }), mode)
      if (fresh) {
        await page.locator('.welcome').waitFor(); await check('welcome-choices', 0)
        const provider = page.getByRole('region', { name: 'Claude Code sign-in', exact: true })
        await provider.getByRole('button', { name: 'Sign in', exact: true }).click()
        await page.locator('#login-code').waitFor(); await check('welcome-sign-in', 1)
        await page.locator('#login-code').fill('fixture-only')
        await provider.getByRole('button', { name: 'Done', exact: true }).click()
        await page.getByRole('button', { name: 'Meet your first agent', exact: true }).waitFor(); await check('welcome-connected', 1)
      } else {
        await page.evaluate(() => __contexts.goProfile('sprout')); await page.locator('.profile-name').waitFor(); await check('profile', 1)
        assert.ok(await page.locator('.profile-actions .primary').evaluate(n => n.textContent.includes('New thread')), 'Profile New thread remains primary')
        if (width === 1440) {
          for (const label of ['New thread', 'Search threads']) assert.equal(await page.locator('.listcol').getByRole('button', { name: label, exact: true }).evaluate(n => getComputedStyle(n).backgroundColor), 'rgba(0, 0, 0, 0)', 'supplementary list stays Newspaper')
        }
        await page.locator('.profile-name').click(); await page.locator('#profile-display').waitFor(); await check('profile-rename', 1)
        await page.getByRole('button', { name: 'Cancel', exact: true }).click(); await check('profile-cancel-edit', 1)
        await page.evaluate(() => __contexts.goSettings('voice')); await page.locator('.plugin-secret-settings input').first().waitFor(); await check('keys-empty', 0)
        await page.locator('.plugin-secret-settings input').first().fill('synthetic-unsent-value'); await check('key-ready', 1)
        await page.locator('.plugin-secret-settings input').last().focus(); await check('key-empty-focus', 0)
        await page.locator('.plugin-secret-settings input').last().fill('synthetic-unsent-value'); await check('other-key-ready', 1)
        await page.evaluate(() => __contexts.goSettings('update')); await page.locator('.update-control').waitFor(); await page.waitForTimeout(250); await check('update', 1)
        await page.getByRole('button', { name: 'Roll back…', exact: true }).click(); await check('rollback-confirm', 1)
        await page.evaluate(() => __contexts.goSettings('integration', 'github')); await page.locator('.github-account-add').waitFor(); await check('github-accounts', 1)
        const add = page.getByRole('button', { name: 'Add account', exact: true }), size = await add.boundingBox()
        await page.getByRole('button', { name: 'Disconnect example-account', exact: true }).click(); await check('github-disconnect', 1)
        const quiet = await add.boundingBox()
        assert.equal(quiet.width, size.width); assert.equal(quiet.height, size.height, 'demotion preserves Add account typography and target')
        await page.evaluate(() => __contexts.threads.open('a-quieter-week')); await page.locator('#draft').waitFor()
        await page.waitForFunction(() => !__contexts.threads.agentDrafts.loading)
        await page.evaluate(() => {
          const t = __contexts.threads
          Object.assign(t.agentDrafts, { thread: t.st.open.thread.id, open: true, rows: [1, 2].map(n => ({ draft: `layout-${n}`, name: `Draft ${n}`, rev: 1, soul: 'A synthetic draft.', config: 'A synthetic configuration.', ready: true, problems: [] })) })
          __contexts.schedule()
        })
        await page.locator('.agent-drafts').waitFor(); await check('agent-drafts-empty', 0)
        await page.locator('#draft').fill('One clear next step.'); await check('agent-drafts-typing', 1)
      }
      assert.deepEqual(errors, [])
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, 'contexts.json'), JSON.stringify(reports, null, 2) + '\n')
} finally { await browser.close() }
