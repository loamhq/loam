// Actual app, stylesheet and HTTP relay; synthetic provider descriptors only.
// All external navigation is fulfilled locally. No live sign-in is possible.
import assert from 'node:assert/strict'
import path from 'node:path'
import { mkdir, writeFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { fakeBroker, appAgainst, closing } from './harness.js'
import { providerFixture, flowFixture } from './fixtures/provider-sign-in.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const output = process.env.PROVIDER_SIGN_IN_EVIDENCE
if (!output) throw new Error('Set PROVIDER_SIGN_IN_EVIDENCE')
await mkdir(output, { recursive: true })
const initial = [providerFixture(), providerFixture('claude-code'), providerFixture('codex', 'device-code'), providerFixture('future')]
let providers, flows
function reset() {
  providers = structuredClone(initial)
  const claude = providers.find((p) => p.id === 'claude-code')
  claude.connected = true; claude.login = flowFixture(claude, 'done').login
  flows = new Map()
}
reset()
const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
const routes = {
  'GET /v1/agents': (req, res) => json(res, { agents: [{ id: 'scout', name: 'Scout', emoji: null }] }),
  'GET /v1/providers': (req, res) => json(res, { providers }),
  'GET /v1/sessions': (req, res) => json(res, { sessions: [], live_count: 0, cap: 4 }),
  'GET /v1/sessions/events': (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n') },
}
for (const p of initial) {
  routes[`POST ${p.sign_in.start}`] = (req, res) => {
    const flow = flowFixture(providers.find((r) => r.id === p.id))
    flows.set(p.id, flow); json(res, flow, 201)
  }
  routes[`GET ${p.sign_in.status}`] = (req, res) => json(res, flows.get(p.id) || flowFixture(p, 'idle'))
  routes[`DELETE ${p.sign_in.cancel}`] = (req, res) => { flows.delete(p.id); json(res, flowFixture(p, 'idle')) }
  if (p.sign_in.code) routes[`POST ${p.sign_in.code}`] = (req, res, url, raw) => {
    const code = JSON.parse(raw).code
    const flow = flowFixture(providers.find((r) => r.id === p.id), code === 'fixture-correct-code' || code === 'https://landing.example.invalid/?code=fixture-correct-code' ? 'done' : 'failed')
    flows.set(p.id, flow)
    if (flow.state === 'done') providers = providers.map((r) => r.id === p.id ? { ...r, connected: true, reason: null, login: flow.login } : r)
    json(res, flow)
  }
  if (p.sign_in.sign_out) routes[`POST ${p.sign_in.sign_out}`] = (req, res) => {
    providers = providers.map((r) => r.id === p.id ? structuredClone(p) : r)
    flows.delete(p.id); json(res, { removed: true, login: p.login })
  }
}
const broker = await fakeBroker(routes), app = await appAgainst(broker)
const results = [], captures = []
let browser
try {
  browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-gpu'] })
  const reference = await browser.newPage()
  await reference.goto(new URL('../design/Loam App.dc.html', import.meta.url).href)
  await reference.close()
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    reset()
    const context = await browser.newContext({ viewport: { width, height: 900 }, colorScheme: theme,
      isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce' })
    const page = await context.newPage(), errors = [], popups = []
    page.on('pageerror', (error) => errors.push(error.message))
    await context.route('https://**/*', (route) => route.fulfill({ contentType: 'text/html', body: '<p>Synthetic sign-in page. No real provider contacted.</p>' }))
    context.on('page', (popup) => { if (popup !== page) popups.push(popup) })
    const card = page.locator('.provider-sign-in')
    const shot = async (state, provider = 'antigravity') => {
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame) })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      const bounds = await card.boundingBox()
      assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width + 1)
      assert.equal(await card.locator('input').count() > 0 ? await card.locator('input').evaluate((n) => parseFloat(getComputedStyle(n).fontSize)) >= 16 : true, true)
      assert.doesNotMatch(await card.innerText(), /documentation|steps|on the box/i)
      const file = `${provider}-${state}-${width}-${theme}.png`
      const bytes = await page.screenshot({ path: path.join(output, file), animations: 'disabled' })
      captures.push({ file, sha256: createHash('sha256').update(bytes).digest('hex') })
    }
    const openProvider = async (name) => {
      await page.getByRole('button', { name: 'Settings', exact: true }).first().click()
      await page.getByRole('button', { name: /Providers/ }).first().click()
      await page.locator('.setrow').filter({ has: page.locator('.setrow-title', { hasText: name }) }).click()
      await card.waitFor()
    }
    const back = () => page.locator('.backbtn').filter({ hasText: 'Providers' }).click()
    const begin = async (label = 'Sign in') => {
      const popupPromise = context.waitForEvent('page')
      await card.getByRole('button', { name: label, exact: true }).click()
      const popup = await popupPromise
      await popup.waitForURL('https://sign-in.example.invalid/**')
      assert.equal(await popup.evaluate(() => window.opener), null)
      await popup.close()
      await page.bringToFront()
    }
    await page.goto(app.url)
    await openProvider('Antigravity')
    await shot('signed-out')
    await begin()
    await card.getByRole('textbox').waitFor()
    await shot('waiting-for-code')
    await card.getByRole('textbox').fill('fixture-wrong-code')
    await card.getByRole('button', { name: 'Done', exact: true }).click()
    await card.getByRole('alert').filter({ hasText: 'That code was not accepted. Try again.' }).waitFor()
    await shot('wrong-code')
    await begin('Try again')
    await card.getByRole('textbox').fill('https://landing.example.invalid/?code=fixture-correct-code')
    await card.getByRole('textbox').press('Enter')
    await card.getByText('Signed in as person@example.invalid', { exact: true }).waitFor()
    assert.equal(await card.getByRole('textbox').count(), 0)
    await shot('signed-in')
    await card.getByRole('button', { name: 'Sign out', exact: true }).click()
    await card.getByText('Not signed in', { exact: true }).waitFor()
    await back()
    await page.locator('.setrow').filter({ has: page.locator('.setrow-title', { hasText: 'Claude Code' }) }).click()
    await card.getByText('Signed in', { exact: true }).waitFor()
    assert.equal(await card.getByRole('button', { name: 'Sign out', exact: true }).count(), 0)
    await shot('signed-in', 'claude-code')
    // Both other flow kinds and a provider unknown to the page use this card.
    await back()
    await page.locator('.setrow').filter({ has: page.locator('.setrow-title', { hasText: 'Codex' }) }).click()
    await begin()
    await card.getByText('ABCD-EFGH', { exact: true }).waitFor()
    assert.equal(await card.getByRole('textbox').count(), 0)
    await shot('device-code', 'codex')
    const codex = providers.find((p) => p.id === 'codex'), settled = flowFixture(codex, 'done')
    codex.connected = true; codex.login = settled.login; flows.set(codex.id, settled)
    await card.getByText('Signed in as person@example.invalid', { exact: true }).waitFor()
    await shot('signed-in', 'codex')
    await back()
    await page.locator('.setrow').filter({ has: page.locator('.setrow-title', { hasText: 'Future provider' }) }).click()
    await begin()
    await card.getByRole('textbox').fill('fixture-correct-code')
    await card.getByRole('button', { name: 'Done', exact: true }).click()
    await card.getByText('Signed in as person@example.invalid', { exact: true }).waitFor()
    await back()
    await page.locator('.setrow').filter({ has: page.locator('.setrow-title', { hasText: 'Antigravity' }) }).click()
    await begin()
    const before = broker.seen.filter((r) => r.method === 'DELETE').length
    await Promise.all([
      page.waitForResponse((r) => r.request().method() === 'DELETE' && r.url().endsWith('/antigravity/login')),
      back(),
    ])
    assert.ok(broker.seen.filter((r) => r.method === 'DELETE').length > before)
    assert.deepEqual(errors, [])
    results.push({ width, theme, externalNavigationIntercepted: true, pasteRetry: true, landingURL: true,
      signOut: true, deviceCode: true, futureProvider: true, leaveCancels: true, popups: popups.length, errors })
    await context.close()
  }
  assert.ok(broker.seen.some((r) => r.path === '/v1/providers/antigravity/logout'))
  await writeFile(path.join(output, 'browser.json'), JSON.stringify({ synthetic: true, results, captures }, null, 2) + '\n')
  console.log(JSON.stringify({ results, captureCount: captures.length }))
} finally { await browser?.close(); await closing(app, broker)() }
