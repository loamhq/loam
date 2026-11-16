// Real page/relay, synthetic agents. No agent processes or production writes.
import assert from 'node:assert/strict'
import path from 'node:path'
import { mkdir, writeFile } from 'node:fs/promises'
import { profileHost } from './agent-profile-host.mjs'
import { createFakeSchedules } from '../tools/fake-schedules.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.AGENT_FACE_MENU_EVIDENCE
if (!out) throw new Error('Set AGENT_FACE_MENU_EVIDENCE to an evidence directory')
await mkdir(out, { recursive: true })
const app = await profileHost(), results = []
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
const schedules = createFakeSchedules(app.f.agents.map(a => a.id))
app.routes['GET /v1/schedules'] = (req, res, url) => json(res, schedules.handleAnswer('GET', '/v1/schedules', url).body)
for (const agent of app.f.agents) app.routes[`POST /v1/schedules/${agent.id}/weekly-review/seen`] = (req, res, url) => json(res, schedules.handleAnswer('POST', url.pathname, url, {}).body)
app.routes['GET /v1/activity'] = (req, res) => json(res, { items: [], jobs: { slots: [], queue: [], landing: [] }, total: 0, next_offset: null })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: { width, height: 900 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390 })
    const page = await context.newPage(), errors = []
    page.setDefaultTimeout(10000)
    page.on('pageerror', e => errors.push(e.message))
    const shot = async name => {
      await page.evaluate(() => document.fonts.ready)
      await page.waitForTimeout(250)
      await page.screenshot({ path: path.join(out, `${name}-${width}-${theme}.png`) })
    }
    const writes = () => app.broker.seen.filter(r => r.method !== 'GET')
    const menu = page.getByRole('dialog', { name: 'Sprout', exact: true })
    const face = page.getByRole('button', { name: 'Sprout menu', exact: true })
    const choose = menu.getByRole('button', { name: 'New thread', exact: true })
    const profile = menu.getByRole('button', { name: 'Profile', exact: true })
    const tap = locator => width === 390 ? locator.tap() : locator.click()
    await page.goto(app.url)
    await page.locator('.tt-row').first().waitFor()
    const start = writes().length
    assert.equal(await face.getAttribute('aria-haspopup'), 'dialog')
    await tap(face)
    await menu.waitFor()
    assert.equal(await face.getAttribute('aria-expanded'), 'true')
    assert.deepEqual(await menu.locator('.tt-persona-actions button').allTextContents(), ['New thread', 'Profile'])
    assert.equal(await menu.locator('h2').textContent(), 'Sprout')
    assert.match(await menu.locator('.tt-persona-description').textContent(), /Last reply/)
    assert.equal(await menu.locator('.blob').evaluate(n => n.offsetWidth), 56)
    assert.equal(await menu.locator('.chevron').count(), 0)
    await shot('menu')
    const a = await face.boundingBox(), b = await menu.boundingBox()
    assert.ok(b.x >= 0 && b.x + b.width <= width + 1)
    if (width === 390) assert.ok(Math.abs(b.y + b.height - 900) < 1, 'phone uses bottom sheet')
    else assert.ok(Math.abs(b.y - a.y - a.height - 8) < 1, 'desktop anchors to face')
    assert.equal(writes().length, start, 'opening starts nothing')
    // Escape and outside taps both dismiss and return focus without a write.
    await page.keyboard.press('Escape')
    await menu.waitFor({ state: 'hidden' })
    assert.equal(await face.evaluate(n => n === document.activeElement), true)
    assert.equal(await face.getAttribute('aria-expanded'), 'false')
    await tap(face)
    await menu.waitFor()
    if (width === 390) await page.touchscreen.tap(5, 5)
    else await page.mouse.click(5, 5)
    await menu.waitFor({ state: 'hidden' })
    assert.equal(writes().length, start)
    await shot('dismissed')
    // Native button Enter/Space, arrows in both directions and Enter to pick.
    await face.focus()
    await page.keyboard.press('Space')
    await menu.waitFor()
    assert.equal(await choose.evaluate(n => n === document.activeElement), true)
    await page.keyboard.press('ArrowDown')
    assert.equal(await profile.evaluate(n => n === document.activeElement), true)
    await page.keyboard.press('ArrowUp')
    assert.equal(await choose.evaluate(n => n === document.activeElement), true)
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('Enter')
    await page.locator('.profile-hero').waitFor()
    assert.equal(await page.locator('.profile-hero .display').textContent(), 'Sprout')
    assert.equal(await page.getByRole('button', { name: 'Open workspace', exact: true }).count(), 1)
    assert.equal(writes().length, start, 'profile starts no thread')
    await page.getByRole('button', { name: 'Manage schedules', exact: true }).click()
    await page.getByRole('region', { name: 'Sprout schedules', exact: true }).waitFor()
    assert.ok(app.broker.seen.some(r => r.path === '/v1/schedules' && new URLSearchParams(r.search).get('agent') === 'sprout'))
    await shot('profile')
    // A different face must open its own profile and name its own menu.
    await page.goto(app.url)
    const quill = page.getByRole('button', { name: 'Quill menu', exact: true })
    await tap(quill)
    await page.getByRole('dialog', { name: 'Quill', exact: true }).getByRole('button', { name: 'Profile' }).click()
    await page.locator('.profile-hero').waitFor()
    assert.equal(await page.locator('.profile-hero .display').textContent(), 'Quill')
    // New thread retains the original POST with this face as its persona.
    await page.goto(app.url)
    const beforeCreate = writes().length
    await face.focus()
    await page.keyboard.press('Enter')
    await menu.waitFor()
    assert.equal(writes().length, beforeCreate)
    await page.keyboard.press('Enter')
    await page.getByRole('heading', { name: 'A new thread with Sprout', exact: true }).waitFor()
    const posts = writes().slice(beforeCreate).filter(r => r.method === 'POST' && r.path === '/v1/threads')
    assert.equal(posts.length, 1)
    assert.deepEqual(JSON.parse(posts[0].body), { persona: 'sprout' })
    await shot('new-thread')
    assert.deepEqual(errors, [])
    results.push({ width, theme, keyboard: true, dismissal: ['Escape', 'outside'], correctProfile: ['sprout', 'quill'], created: JSON.parse(posts[0].body), geometry: { face: a, menu: b }, errors })
    console.log(`PASS ${width} ${theme}: both choices, no premature writes, Enter/Space/arrows, Escape/outside, correct profiles, menu geometry; no page errors`)
    await context.close()
  }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify({ browser: browser.version(), results }, null, 2) + '\n')
} finally { await browser.close(); await app.close() }
