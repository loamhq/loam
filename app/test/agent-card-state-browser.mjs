import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { oneProductFixture } from './one-product-fixture.mjs'
import { auditControls, assertControls } from './control-style-guard.mjs'
import { inspectColourFamilies } from '../tools/colour-families.mjs'
import { execFileSync } from 'node:child_process'
const baseline = process.env.AGENT_CARD_BASELINE
const out = process.env.AGENT_FACE_MENU_EVIDENCE
if (!out) throw new Error('Set AGENT_FACE_MENU_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8'), results = []
try {
 for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
  const app = await oneProductFixture(), body = await app.f.api('GET', '/threads'), errors = []
  const working = body.threads.find(t => t.id === 'a-quieter-week')
  working.members.find(m => m.persona === 'sprout').working = true
  working.archived = true
  working.title = 'Loam: architecture and the next steps'
  body.agents[0].description = 'Brings order to plans, projects and the day ahead.'
  const json = (res, value) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(value)) }
  app.routes['GET /v1/threads'] = (req, res) => json(res, body)
  app.routes['GET /v1/threads/events'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n') }
  const ctx = await browser.newContext({ viewport: { width, height: 900 }, isMobile: width === 390, hasTouch: width === 390, colorScheme: theme })
  await ctx.route('**/app.js', r => r.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__cards = { threads, schedule };' }))
  if (baseline) for (const file of ['threads.js', 'threads.css']) await ctx.route(`**/${file}`, r => r.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: execFileSync('git', ['show', `${baseline}:public/${file}`], { encoding: 'utf8' }) }))
  const page = await ctx.newPage(); page.setDefaultTimeout(10000); page.on('pageerror', e => errors.push(e.message))
  const card = page.locator('.tt-persona-card'), face = page.getByRole('button', { name: 'Sprout menu', exact: true })
  const shot = async (name) => { await page.evaluate(() => document.fonts.ready); await page.waitForTimeout(250); if (!baseline) assert.deepEqual(await page.evaluate(inspectColourFamilies), [], 'Agent card uses paired family ink'); await page.screenshot({ path: `${out}/card-${name}-${width}-${theme}.png` }) }
  const controls = () => auditControls(page, '.tt-persona-card :is(button, a[href], input, select, textarea, [role=button])')
  const metrics = () => card.evaluate(n => {
    const css = getComputedStyle(n), face = n.querySelector('.blob').getBoundingClientRect(), about = n.querySelector('.tt-persona-about').getBoundingClientRect()
    const current = n.querySelector('.tt-persona-current'), link = current && getComputedStyle(current)
    const box = n.getBoundingClientRect(), last = n.lastElementChild.getBoundingClientRect()
    const probe = document.createElement('span'); n.append(probe); probe.style.color = 'var(--paper-ink-2)'; const secondary = getComputedStyle(probe).color; probe.remove()
    return { paddingBottom: css.paddingBottom, gap: css.rowGap, bottomClearance: box.bottom - last.bottom,
      faceWidth: face.width, centreDelta: (face.y + face.height / 2) - (about.y + about.height / 2),
      working: link && { border: link.borderTopWidth, background: link.backgroundColor, color: link.color, secondary, font: link.fontFamily, size: link.fontSize, height: current.getBoundingClientRect().height } }
  })
  const checkCard = async (safeArea = 0) => {
    await page.waitForTimeout(250)
    const rows = await controls(); assertControls(rows)
    const m = await metrics()
    assert.equal(m.faceWidth, 56); assert.ok(Math.abs(m.centreDelta) < .6, JSON.stringify(m))
    assert.equal(m.gap, '14px')
    if (width === 390) { assert.equal(parseFloat(m.paddingBottom), 28 + safeArea); assert.ok(m.bottomClearance >= 28 + safeArea, JSON.stringify(m)) }
    if (m.working) {
      assert.equal(m.working.border, '0px'); assert.equal(m.working.background, 'rgba(0, 0, 0, 0)')
      assert.equal(m.working.color, m.working.secondary); assert.equal(m.working.size, '13px'); assert.ok(m.working.height >= 44)
      assert.equal(await card.locator('.tt-persona-working-mark').evaluate(n => getComputedStyle(n).animationName), 'tt-subtitle-sheen')
    }
    return { controls: rows, ...m }
  }
  try {
    await page.goto(app.url); await page.locator('.tt-row').first().waitFor(); await face.click(); await card.waitFor()
    if (baseline) {
      const described = { controls: await controls(), ...await metrics() }; await shot('before-description-working')
      delete body.agents[0].description
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor(); await face.click(); await card.waitFor()
      const fallback = { controls: await controls(), ...await metrics() }; await shot('before-working-fallback')
      results.push({ width, theme, baseline, described, fallback }); continue
    }
    const describedStyles = await checkCard()
    // The shared guard must catch native styling on every individual control.
    const visibleControls = card.locator('button:visible'), guarded = await visibleControls.count()
    for (let i = 0; i < guarded; i++) {
      await visibleControls.nth(i).evaluate(n => { n.style.background = 'revert'; n.style.border = 'revert'; n.style.fontFamily = 'revert' })
      const guardRows = await controls(); assert.throws(() => assertControls(guardRows), 'native styling on each card control must fail the guard')
      await visibleControls.nth(i).evaluate(n => { n.style.removeProperty('background'); n.style.removeProperty('border'); n.style.removeProperty('font-family') })
    }
    assert.equal(await card.locator('.tt-persona-description').textContent(), body.agents[0].description)
    assert.equal(await card.getByRole('button', { name: /^Working in Loam:/ }).count(), 1)
    await shot('description-working')
    await card.getByRole('button', { name: /^Working in Loam:/ }).click()
    await page.waitForFunction(() => __cards.threads.st.open?.thread.id === 'a-quieter-week')
    assert.equal(await card.count(), 0, 'work link opens its thread and closes card')
    delete body.agents[0].description
    await page.goto(app.url); await page.locator('.tt-row').first().waitFor(); await face.click(); await card.waitFor()
    assert.match(await card.locator('.tt-persona-description').textContent(), /^Working in Loam:/, 'working fallback uses actual thread')
    const fallbackStyles = await checkCard()
    assert.equal(await card.locator('.tt-persona-current-label').evaluate(n => getComputedStyle(n).textDecorationLine), 'none')
    await shot('working-fallback')
    if (width === 1440) {
      await card.locator('.tt-persona-current').hover()
      assert.equal(await card.locator('.tt-persona-current-label').evaluate(n => getComputedStyle(n).textDecorationLine), 'underline')
      await page.mouse.move(0, 0)
      assert.equal(await card.locator('.tt-persona-current-label').evaluate(n => getComputedStyle(n).textDecorationLine), 'none')
    }
    await page.keyboard.press('Tab'); await card.locator('.tt-persona-current').focus()
    assert.equal(await card.locator('.tt-persona-current-label').evaluate(n => getComputedStyle(n).textDecorationLine), 'underline')
    let safeAreaStyles = null
    if (width === 390) {
      const cdp = await ctx.newCDPSession(page)
      await cdp.send('Emulation.setSafeAreaInsetsOverride', { insets: { bottom: 34 } })
      safeAreaStyles = await checkCard(34); await shot('safe-area')
      await cdp.send('Emulation.setSafeAreaInsetsOverride', { insets: { bottom: 0 } })
    }
    await page.emulateMedia({ reducedMotion: 'reduce' })
    assert.equal(await card.locator('.tt-persona-working-mark').evaluate(n => getComputedStyle(n).animationName), 'none')
    await page.emulateMedia({ reducedMotion: 'no-preference' })
    // A live summary ending work removes the link; explicit false beats busy.
    await page.evaluate(() => { const t = __cards.threads.st.threads.find(t => t.id === 'a-quieter-week'); Object.assign(t.members.find(m => m.persona === 'sprout'), { working: false, busy: true }); __cards.schedule() })
    await page.waitForFunction(() => !document.querySelector('.tt-persona-current'))
    assert.match(await card.locator('.tt-persona-description').textContent(), /Last reply/)
    await page.keyboard.press('Escape')
    await page.getByRole('button', { name: 'Pip menu', exact: true }).click()
    assert.equal(await card.locator('.tt-persona-description').textContent(), 'Start a thread together', 'unknown reply time is not invented')
    await shot('new-conversation')
    assert.deepEqual(errors, [])
    results.push({ width, theme, describedStyles, fallbackStyles, safeAreaStyles, guardedControls: guarded, description: true, workingLink: true, workingFallback: true, liveStop: true, unknownReply: true })
    console.log(`PASS ${width} ${theme}: all ${guarded} controls styled, native-control counterfactuals, spacing/centring/safe area, work navigation, live stop`)
  } finally { await ctx.close(); await app.close() }
 }
 await writeFile(`${out}/card-states.json`, JSON.stringify(results, null, 2) + '\n')
} finally { await browser.close() }
