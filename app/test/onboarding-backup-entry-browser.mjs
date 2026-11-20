// Run against a loopback scratch install seeded by bin/library seed-starter,
// with Git backup off and a stand-in Claude CLI that accepts fixture-code.
// Use a fresh install for each walk. Sign the stand-in CLI in before the
// already-connected walk. No real provider should be contacted.
//
//   LOAM_PLAYWRIGHT=<module> LOAM_CHROME=<chromium> BACKUP_EVIDENCE=<directory> \
//     node test/onboarding-backup-entry-browser.mjs <app-url> <path> <width> <mode>
//   path: welcome or already-connected; mode: light or dark
import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import { join } from 'node:path'

const [address, entry, widthText, mode] = process.argv.slice(2)
const origin = new URL(address)
const width = Number(widthText)
const out = process.env.BACKUP_EVIDENCE
assert.equal(origin.hostname, '127.0.0.1')
assert.ok(['welcome', 'already-connected'].includes(entry))
assert.ok([390, 1440].includes(width))
assert.ok(['light', 'dark'].includes(mode))
assert.ok(out, 'Set BACKUP_EVIDENCE')
await mkdir(out, { recursive: true })

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
try {
  const context = await browser.newContext({
    viewport: { width, height: width === 390 ? 1000 : 960 },
    isMobile: width === 390, hasTouch: width === 390, colorScheme: mode,
    reducedMotion: 'reduce', serviceWorkers: 'block',
  })
  await context.route('**/*', route => {
    const url = new URL(route.request().url())
    if (url.origin === origin.origin) return route.continue()
    if (url.hostname === 'claude.com' && url.pathname === '/cai/oauth/authorize') {
      return route.fulfill({ contentType: 'text/plain', body: 'Scratch sign-in code: fixture-code' })
    }
    return route.abort()
  })
  // Check for a briefly rendered interstitial as well as the settled screen.
  await context.addInitScript(() => {
    window.onboardingScreens = new Set()
    new MutationObserver(() => {
      const screen = document.querySelector('.welcome h1')
      if (screen) window.onboardingScreens.add(screen.textContent)
    }).observe(document, { subtree: true, childList: true, characterData: true })
  })
  const page = await context.newPage()
  page.setDefaultTimeout(15_000)
  const errors = [], backupRequests = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('request', request => {
    const { pathname } = new URL(request.url())
    if (/^\/(api\/plugins|x)\/git-backup(?:\/|$)/.test(pathname)) {
      backupRequests.push(`${request.method()} ${pathname}`)
    }
  })
  const shot = async name => {
    await page.evaluate(() => document.fonts.ready)
    await page.waitForFunction(theme => document.documentElement.dataset.theme === theme, mode)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
    await page.screenshot({ path: join(out, `${name}-${width}-${mode}.png`), animations: 'disabled' })
  }

  await page.goto(origin.href)
  if (entry === 'welcome') {
    await page.getByRole('heading', { name: 'Welcome to Loam', exact: true }).waitFor()
    const meet = page.getByRole('button', { name: 'Meet your first agent', exact: true })
    assert.ok(await meet.isDisabled())
    assert.doesNotMatch(await page.locator('.welcome').innerText(), /back ?up/i)
    await shot('welcome-provider-picker')
    const claude = page.getByRole('region', { name: 'Claude Code sign-in' })
    const [popup] = await Promise.all([
      page.waitForEvent('popup'),
      claude.getByRole('button', { name: 'Sign in with Claude', exact: true }).click(),
    ])
    await popup.getByText('Scratch sign-in code: fixture-code', { exact: true }).waitFor()
    await popup.close()
    await page.bringToFront()
    const code = claude.getByRole('textbox', { name: 'Paste the code you were given' })
    await code.waitFor()
    await shot('welcome-provider-sign-in')
    await code.fill('fixture-code')
    await claude.getByRole('button', { name: 'Done', exact: true }).click()
    await claude.getByText('✓ Connected', { exact: true }).waitFor()
    assert.doesNotMatch(await page.locator('.welcome').innerText(), /back ?up/i)
    await shot('welcome-provider-connected')
    await meet.click()
  }
  await page.locator('[data-message-id="m1"]').getByText(/Hi Sam,/).waitFor()
  await page.locator('.welcome').waitFor({ state: 'detached' })
  const screens = await page.evaluate(() => [...window.onboardingScreens])
  assert.deepEqual(screens.filter(screen => screen !== 'Making room for you…'),
    entry === 'welcome' ? ['Welcome to Loam'] : [])
  assert.doesNotMatch(await page.locator('body').innerText(), /back ?up/i)
  assert.deepEqual(backupRequests, [])
  await shot(`${entry}-hello`)

  if (entry === 'already-connected') {
    if (width === 390) await page.getByRole('button', { name: 'Back to threads' }).click()
    await page.getByRole('button', { name: 'Settings', exact: true }).first().click()
    await page.getByRole('button', { name: /^Plugins\b/ }).first().click()
    const backup = page.getByRole('button', { name: 'About Git backup' })
    await backup.getByText('Off-box backup is off', { exact: true }).waitFor()
    await backup.scrollIntoViewIfNeeded()
    await shot('settings-backup-off')
    await backup.click()
    await page.getByText('Off-box backup is off', { exact: true }).waitFor()
    await page.getByText(/Turn on Git backup to back up your vault and agents/).waitFor()
    const toggle = page.getByRole('switch', { name: 'Enable Git backup' })
    assert.equal(await toggle.getAttribute('aria-checked'), 'false')
    await shot('settings-backup-guide')
    assert.ok(backupRequests.every(request => request.startsWith('GET ')), 'opening Settings keeps backup off')
  }
  assert.deepEqual(errors, [])
  console.log(`PASS ${entry}, ${width}px, ${mode}: Hello reached directly`)
} finally {
  await browser.close()
}
