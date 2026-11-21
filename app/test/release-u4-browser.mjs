import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { u4Fixture } from './release-u4-fixture.mjs'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.U4_EVIDENCE || `${process.env.TMPDIR}/u4`
await mkdir(out, { recursive: true })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const source = await readFile('public/app.js', 'utf8'), claim = await readFile('public/claim.html', 'utf8'), reports = []
try {
  for (const width of [390, 1440].filter(width => !process.env.U4_WIDTH || width === Number(process.env.U4_WIDTH))) for (const mode of ['light', 'dark'].filter(mode => !process.env.U4_MODE || mode === process.env.U4_MODE)) {
    const app = await u4Fixture({ fresh: true }), appURL = app.url.replace('127.0.0.1', 'localhost'), errors = [], external = [], codes = []
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 960 }, colorScheme: mode, serviceWorkers: 'block', reducedMotion: 'reduce' })
    const page = await context.newPage()
    let claimBody, claimed = false, relay = true
    await context.route('**/*', async route => {
      const url = new URL(route.request().url())
      if (url.hostname === 'provider.example' || url.hostname.startsWith('auth.')) return route.fulfill({ contentType: 'text/html', body: '<p>Sign in with your own account</p>' })
      if (url.origin !== appURL) { external.push(url.href); return route.abort() }
      if (url.pathname === '/app.js') return route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__u4={state,threads,goSettings,schedule,theme};' })
      if (url.pathname === '/claim') {
        if (route.request().method() === 'POST') {
          claimBody = route.request().postDataJSON(); claimed = true
          return route.fulfill({ status: 202, json: { ok: true } })
        }
        if (url.search === '?status') {
          if (claimed) return route.fulfill({ status: 302, headers: { location: appURL } })
          return route.fulfill({ json: { stage: 'open' } })
        }
        return route.fulfill({ contentType: 'text/html', headers: { 'Content-Security-Policy': claim.match(/http-equiv="Content-Security-Policy" content="([^"]+)"/)[1] + "; frame-ancestors 'none'" }, body: claim })
      }
      if (url.pathname === '/_loam/login-code') {
        codes.push(url.pathname)
        return route.fulfill({ json: relay ? { code: 'K7QP2MXA', at: new Date(Date.now() - 12000).toISOString(), age_seconds: 12 } : { code: null } })
      }
      return route.continue()
    })
    page.on('pageerror', e => errors.push(e.message))
    page.on('console', e => { if (e.type() === 'error') errors.push(e.text()) })
    const shot = async name => { await page.screenshot({ path: `${out}/${name}-${width}-${mode}.png` }); console.log(`${width} ${mode}: ${name}`) }
    try {
      await page.goto(appURL + '/claim#token=fixture-claim')
      assert.equal(new URL(page.url()).hash, '')
      await shot('claim')
      await page.getByLabel('Choose a username').fill('new-person')
      await page.getByLabel('Choose a password', { exact: true }).fill('a synthetic password only')
      await page.getByRole('button', { name: 'Make this my Loam', exact: true }).click()
      await page.getByRole('heading', { name: 'Your Loam is ready' }).waitFor(); await shot('claim-success')
      await page.getByRole('heading', { name: 'Welcome to Loam', exact: true }).waitFor()
      assert.equal(claimed, true); assert.equal(claimBody.token, 'fixture-claim')
      assert.equal(await page.getByRole('button', { name: 'Meet your first agent', exact: true }).isDisabled(), true)
      assert.equal(await page.locator('.welcome-providers .provider-sign-in').count(), 3)
      await shot('welcome')
      // One shared card supports both device-code and pasted-code providers.
      const codex = page.getByRole('region', { name: 'Codex sign-in', exact: true })
      await codex.getByRole('button', { name: 'Sign in', exact: true }).click()
      await codex.getByText('DEVICE12', { exact: true }).waitFor(); await shot('welcome-device-code')
      await codex.getByRole('button', { name: 'Cancel', exact: true }).click()
      const claude = page.getByRole('region', { name: 'Claude Code sign-in', exact: true })
      await claude.getByRole('button', { name: 'Sign in', exact: true }).click()
      await page.getByLabel('Paste the code you were given', { exact: true }).waitFor(); await shot('welcome-sign-in')
      await page.getByLabel('Paste the code you were given', { exact: true }).fill('fixture-provider-code')
      await claude.getByRole('button', { name: 'Done', exact: true }).click()
      await page.getByText('You’re connected. Let’s say hello.', { exact: true }).waitFor(); await shot('welcome-connected')
      await page.getByRole('button', { name: 'Meet your first agent', exact: true }).click()
      await page.getByText('What would you like me to call you?', { exact: false }).waitFor()
      const preset = page.locator('[data-message-id="hello-preset"]')
      assert.equal(await preset.locator('[data-full-steps], .tt-turn-facts, .stepfold').count(), 0)
      assert.equal(await page.locator('.tt-author-meta').count(), 0)
      assert.ok((await page.locator('.tt-author-name').allTextContents()).includes('Iris'))
      await shot('hello')
      await page.locator('#draft').fill('You can call me Alex.')
      await page.getByTitle('Send', { exact: true }).first().click()
      await page.waitForFunction(() => __u4.threads.st.open.messages.some(m => m.id === 'person-one') && !__u4.threads.st.open.sending)
      await page.goto(appURL)
      await page.waitForFunction(() => globalThis.__u4?.state.agentsReady)
      assert.equal(await page.locator('.welcome').count(), 0, 'a replied-to hello never reopens setup')
      await shot('returning-chats')
      await page.evaluate(() => __u4.goSettings('signin'))
      await page.getByRole('link', { name: 'Add a passkey', exact: true }).click()
      await page.getByText('K7QP2MXA', { exact: true }).waitFor(); await shot('passkey-code')
      assert.equal(codes.at(-1), '/_loam/login-code')
      await page.getByRole('button', { name: 'Done', exact: true }).click()
      await page.getByText('K7QP2MXA', { exact: true }).waitFor({ state: 'hidden' })
      relay = false
      await page.getByRole('link', { name: 'Add a passkey', exact: true }).click()
      await page.getByText('Waiting for your sign-in code…', { exact: true }).waitFor(); await shot('passkey-waiting')
      await page.evaluate(() => __u4.goSettings('update'))
      await page.getByText('Version 1.0.0', { exact: true }).waitFor()
      await page.getByRole('button', { name: 'Update Loam', exact: true }).waitFor()
      await shot('update-available')
      app.u4.missing = true
      await page.getByRole('button', { name: 'Check for updates', exact: true }).click()
      await page.getByText('Growing towards our first release', { exact: true }).waitFor(); await shot('update-before-release')
      app.u4.missing = false; app.u4.dirty = true
      await page.getByRole('button', { name: 'Check for updates', exact: true }).click()
      await page.getByText('There are uncommitted changes in this install.', { exact: true }).waitFor(); await shot('update-blocked')
      assert.equal(await page.getByRole('button', { name: 'Update Loam', exact: true }).isDisabled(), true)
      app.u4.dirty = false; app.u4.library = true
      await page.getByRole('button', { name: 'Check for updates', exact: true }).click()
      await page.getByRole('button', { name: 'Update Loam', exact: true }).click()
      await page.getByRole('button', { name: 'Back up and update', exact: true }).waitFor(); await shot('update-library-review')
      await page.getByRole('button', { name: 'Back up and update', exact: true }).scrollIntoViewIfNeeded(); await shot('update-library-consent')
      assert.equal(app.u4.apply.length, 0)
      await page.getByRole('button', { name: 'Cancel', exact: true }).click()
      assert.equal(app.u4.apply.length, 0)
      await page.getByRole('button', { name: 'Check for updates', exact: true }).click()
      await page.getByRole('button', { name: 'Roll back…', exact: true }).click(); await shot('rollback-confirm')
      await page.getByRole('button', { name: 'Restore previous version', exact: true }).click()
      await page.getByText('Previous version restored', { exact: true }).waitFor(); await shot('rollback-done')
      await page.getByRole('button', { name: 'Update Loam', exact: true }).click()
      await page.getByRole('button', { name: 'Back up and update', exact: true }).click()
      await page.getByText('Updating Loam…', { exact: true }).waitFor(); await shot('update-progress')
      assert.equal(app.u4.apply[0].accept_library_edits, true)
      assert.equal(app.u4.apply.length, 1)
      await page.waitForEvent('domcontentloaded', { timeout: 15000 })
      await page.waitForFunction(() => globalThis.__u4?.state.agentsReady)
      await page.getByText('Chats', { exact: true }).first().waitFor()
      await shot('update-reloaded')
      assert.ok(app.u4.progressReads >= 3)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      assert.deepEqual(errors, []); assert.deepEqual(external, [])
      reports.push({ width, mode, claim: true, provider: true, hello: true, returning: true, passkey: true, update: true, rollback: true, apply: app.u4.apply, errors, external })
    } catch (error) { await shot('failure'); throw error }
    finally { await context.close(); await app.close() }
  }
  await writeFile(`${out}/results.json`, JSON.stringify(reports, null, 2))
} finally { await browser.close() }
