// Real Chromium + the real app, against a broker that knows nothing (0.2.1).
//
// The 0.2.0 walk: right after a passkey was added, the tab said "Your session
// has ended. Sign in again" on a bare page, although a reload opened the app
// signed in. The login renews its session when a person proves who they are
// again (adding a passkey asks for it), and the one request that left with the
// old cookie came back anonymous: one refusal latched the whole page.
//
// Proved here: one anonymous answer the probe calls fine is sent again and the
// page carries on; a refusal that holds still ends it; and the ended screen
// wears the sign-in look (the mark, the display face, one primary pill) in
// light and dark, at phone and desk widths.
//
// Run: LOAM_CHROME=<chromium> LOAM_PLAYWRIGHT=<playwright index.mjs> \
//      SESSION_EVIDENCE=<dir> node test/session-renewal-browser.mjs
import assert from 'node:assert/strict'
import path from 'node:path'
import { mkdir } from 'node:fs/promises'
import { fakeBroker, appAgainst, closing } from './harness.js'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.SESSION_EVIDENCE
if (!out) throw new Error('Set SESSION_EVIDENCE to a retained evidence directory')
await mkdir(out, { recursive: true })

const broker = await fakeBroker({}), app = await appAgainst(broker)
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-gpu'] })
try {
  for (const width of [390, 1440]) {
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 } })
    const page = await context.newPage()
    await page.goto(app.url)
    await page.waitForFunction(() => document.querySelector('#app, body')?.children.length > 0)

    let asked = 0
    await page.route('**/_loam/login-code', (route) => asked++ === 0
      ? route.fulfill({ status: 302, headers: { location: '/login' } })
      : route.fulfill({ status: 200, contentType: 'application/json', body: '{"code":"123456","age_seconds":3,"at":"2026-11-17T00:00:00Z"}' }))
    const renewed = await page.evaluate(async () => {
      const { authenticatedFetch, authExpired } = await import('/auth.js')
      const r = await authenticatedFetch('/_loam/login-code', { cache: 'no-store', credentials: 'same-origin' })
      return [r.status, authExpired()]
    })
    assert.deepEqual(renewed, [200, false], 'one anonymous answer during a session renewal does not end the page')
    assert.equal(asked, 2, 'the refused request was sent once more, and only once')
    assert.equal(await page.getByRole('link', { name: 'Sign in again', exact: true }).count(), 0)

    await page.route('**/x/read-aloud/speech/lookup', (route) => route.fulfill({ status: 401, contentType: 'application/json', body: '{}' }))
    await page.route('**/up', (route) => route.fulfill({ status: 302, headers: { location: '/login' } }))
    await page.evaluate(async () => { const { authenticatedFetch } = await import('/auth.js'); await authenticatedFetch('/x/read-aloud/speech/lookup', { method: 'POST' }).catch(() => {}) })
    await page.getByRole('link', { name: 'Sign in again', exact: true }).waitFor({ timeout: 20000 })
    for (const theme of ['light', 'dark']) {
      await page.emulateMedia({ colorScheme: theme })
      await page.evaluate((t) => document.documentElement.setAttribute('data-theme', t), theme)
      const look = await page.evaluate(() => {
        const m = document.querySelector('main.signin-again'), h1 = m?.querySelector('h1'), a = m?.querySelector('a.pillbtn')
        const cs = h1 && getComputedStyle(h1), bg = m && getComputedStyle(m).backgroundColor
        return { mark: !!m?.querySelector('svg.loam-mark'), heading: h1?.textContent, big: !!cs && parseFloat(cs.fontSize) >= 30,
          painted: !!bg && bg !== 'rgba(0, 0, 0, 0)', pill: !!a && a.getBoundingClientRect().height >= 44,
          overflow: document.documentElement.scrollWidth > innerWidth }
      })
      assert.deepEqual(look, { mark: true, heading: 'Your session has ended', big: true, painted: true, pill: true, overflow: false }, `${width} ${theme}`)
      await page.screenshot({ path: path.join(out, `session-ended-${width}-${theme}.png`) })
    }
    await context.close()
  }
  console.log('OK: a renewal does not end the page; the ended screen wears the sign-in look')
} finally {
  await browser.close()
  await closing(app, broker)()
}
