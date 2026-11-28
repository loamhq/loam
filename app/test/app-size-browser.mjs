// Browser regression test and before/after captures for app sizing:
// full-width main surfaces at every size, 0.9-base default (100% = --ui-scale 0.9),
// and usable 60%–200% range across Chats, Thread (long message + composer),
// Files, Settings > Appearance, and pop-up sheets at 390x844 and 1440x900,
// light and dark.
//
// Run from app/:
//   LOAM_PLAYWRIGHT=/path/to/playwright/index.mjs \
//   LOAM_CHROME=/usr/bin/chromium-headless-shell \
//   APP_SIZE_EVIDENCE=docs/evidence/app-size node test/app-size-browser.mjs
//
// Pass APP_SIZE_BASELINE=1 against a git archive of main to capture pre-change
// baseline screenshots (75% narrow-column bug and old 100% scale).
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
import { filesHost } from './files-host.js'
import { mergeAppearance } from '../public/theme-options.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.APP_SIZE_EVIDENCE
const baseline = process.env.APP_SIZE_BASELINE === '1'
if (out) await mkdir(out, { recursive: true })

const LONG_REPLY = [
  '## Weekly synthesis and next steps',
  '',
  'Here is the full recap after walking through the notes, the open checklist items, and the sizing adjustments we discussed earlier today. Every paragraph should wrap cleanly inside the reading column at 60%, 100%, and 200% without forcing horizontal scroll or pushing controls off-screen.',
  '',
  '- **Full-width surfaces:** Remove the legacy 460px ceiling on the app shell so Chats, Threads, Files, Apps, and the bottom tab bar always reach the viewport edges when zoomed out.',
  '- **Calibrated default:** Make 100% correspond to 0.9 of the previous scale (`--ui-scale: 0.9`), keeping the layout calm and airy while letting users scale from 60% up to 200%.',
  '- **High-zoom usability:** Allow the composer action row and size slider controls to wrap cleanly on a 390px phone at 200% while keeping every target reachable.',
  '',
  'Inline code such as `sizeToScale(100) === 0.9` and a longer token like `appearance.phone.size=60..200` should also stay contained inside the message column.',
].join('\n')

const app = await oneProductFixture()
for (const [key, handler] of Object.entries(filesHost().routes)) {
  if (key.includes('/v1/files/')) app.routes[key] = handler
}

const baseRecord = await app.f.api('GET', '/threads/a-quieter-week')
const longRecord = {
  ...baseRecord,
  messages: [
    ...baseRecord.messages,
    { id: 'm-long', author: baseRecord.thread.members[0].id, kind: 'message', at: '2026-11-27T17:10:00Z', text: LONG_REPLY },
  ],
}
const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
app.routes['GET /v1/threads/a-quieter-week'] = (req, res) => json(res, longRecord)

let refuseExtended = false
const values = { phone: { mode: 'light', size: 100, theme: 'latte' }, desktop: { mode: 'light', size: 100, theme: 'latte' } }
app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, values)
app.routes['PATCH /v1/settings/appearance'] = (req, res, url, raw) => {
  const patch = JSON.parse(raw)
  for (const [kind, fields] of Object.entries(patch)) {
    if (refuseExtended && fields?.size !== undefined && (fields.size < 75 || fields.size > 150)) {
      return json(res, { error: `${kind} size is a whole percent from 75 to 150 in steps of 5, not ${fields.size}` }, 400)
    }
  }
  for (const [kind, fields] of Object.entries(patch)) values[kind] = mergeAppearance(values[kind], fields)
  json(res, values)
}

const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })

async function settle(page) {
  await page.evaluate(async () => {
    await document.fonts.ready
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))
  })
}

async function measure(page) {
  return page.evaluate(() => {
    const W = innerWidth, H = innerHeight
    const appBox = document.querySelector('#app').getBoundingClientRect()
    const issues = []
    if (Math.abs(appBox.left) > 1.5 || Math.abs(appBox.right - W) > 1.5) {
      issues.push(`#app width mismatch: left=${appBox.left.toFixed(1)} right=${appBox.right.toFixed(1)} W=${W}`)
    }
    if (Math.abs(appBox.height - H) > 2) {
      issues.push(`#app height mismatch: h=${appBox.height.toFixed(1)} H=${H}`)
    }
    if (document.documentElement.scrollWidth > W + 1) {
      issues.push(`doc overflowX: ${document.documentElement.scrollWidth} > ${W}`)
    }
    if (document.documentElement.scrollHeight > H + 1) {
      issues.push(`doc overflowY: ${document.documentElement.scrollHeight} > ${H}`)
    }
    const sheet = document.querySelector('[data-menu-surface]:popover-open, .sheet.settings')
    if (sheet) {
      const sr = sheet.getBoundingClientRect()
      if (sr.left < -1 || sr.right > W + 1) issues.push(`sheet horiz out: ${sr.left.toFixed(1)}..${sr.right.toFixed(1)}`)
      if (sr.top < -1 || sr.bottom > H + 1) issues.push(`sheet vert out: ${sr.top.toFixed(1)}..${sr.bottom.toFixed(1)}`)
    }
    for (const b of document.querySelectorAll('.nav button')) {
      const r = b.getBoundingClientRect()
      if (!r.width) continue
      if (r.left < -1 || r.right > W + 1) issues.push(`nav btn out: ${b.textContent.trim()}`)
      if (b.scrollWidth > b.clientWidth + 1) issues.push(`nav btn clip: ${b.textContent.trim()} ${b.scrollWidth}>${b.clientWidth}`)
    }
    const compBtns = [...document.querySelectorAll('.composer-actions > button')].filter((b) => b.getBoundingClientRect().width > 0)
    for (let i = 0; i < compBtns.length; i++) {
      const a = compBtns[i].getBoundingClientRect()
      if (a.left < -1 || a.right > W + 1) issues.push(`composer btn ${i} out`)
      if (compBtns[i].scrollWidth > compBtns[i].clientWidth + 2) issues.push(`composer btn ${i} inner clip`)
      for (let j = i + 1; j < compBtns.length; j++) {
        const b = compBtns[j].getBoundingClientRect()
        if (a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1) {
          issues.push(`composer overlap ${i} & ${j}`)
        }
      }
    }
    const slider = document.querySelector('#appearance-zoom')
    if (slider) {
      const sr = slider.getBoundingClientRect()
      if (sr.width < 60) issues.push(`slider too narrow: ${sr.width.toFixed(1)}px`)
      if (sr.left < -1 || sr.right > W + 1) issues.push(`slider out: ${sr.left.toFixed(1)}..${sr.right.toFixed(1)}`)
    }
    return {
      width: W,
      height: H,
      scale: getComputedStyle(document.documentElement).getPropertyValue('--ui-scale').trim(),
      app: { x: Math.round(appBox.x), right: Math.round(appBox.right), width: Math.round(appBox.width), height: Math.round(appBox.height) },
      issues,
    }
  })
}

const summary = []
try {
  if (baseline) {
    // Baseline mode against main: capture the 75% narrow-column bug on phone
    // and the old 100% default on phone and desktop (light and dark).
    const cases = [
      [390, 844, 'light', 75],
      [390, 844, 'dark', 75],
      [390, 844, 'light', 100],
      [390, 844, 'dark', 100],
      [1440, 900, 'light', 100],
      [1440, 900, 'dark', 100],
    ]
    for (const [width, height, mode, size] of cases) {
      const isPhone = width < 1100
      const kind = isPhone ? 'phone' : 'desktop'
      values[kind] = { mode, size, theme: 'latte' }
      const ctx = await browser.newContext({ viewport: { width, height }, colorScheme: mode, isMobile: isPhone, hasTouch: isPhone, reducedMotion: 'reduce' })
      const page = await ctx.newPage()
      await page.goto(app.url)
      await page.locator('.tt-row').first().waitFor()
      await settle(page)
      const m = await measure(page)
      summary.push({ phase: 'before', width, mode, size, screen: 'chats', ...m })
      if (out) await page.screenshot({ path: path.join(out, `before-${width}-${mode}-${size}-chats.png`), animations: 'disabled' })
      await ctx.close()
    }
  } else {
    const expectedScale = { 60: '0.54', 100: '0.9', 200: '1.8' }
    for (const [width, height] of [[390, 844], [1440, 900]]) {
      const isPhone = width < 1100
      const kind = isPhone ? 'phone' : 'desktop'
      for (const mode of ['light', 'dark']) {
        for (const size of [60, 100, 200]) {
          values[kind] = { mode, size, theme: 'latte' }
          const ctx = await browser.newContext({ viewport: { width, height }, colorScheme: mode, isMobile: isPhone, hasTouch: isPhone, reducedMotion: 'reduce' })
          const page = await ctx.newPage()
          const errors = []
          page.on('pageerror', (e) => errors.push(e.message))
          page.setDefaultTimeout(10000)

          const check = async (screen) => {
            await settle(page)
            const m = await measure(page)
            assert.equal(m.scale, expectedScale[size], `${width}-${mode}-${size}-${screen} scale`)
            assert.deepEqual(m.issues, [], `${width}-${mode}-${size}-${screen}: ${m.issues.join('; ')}`)
            summary.push({ phase: 'after', width, mode, size, screen, ...m })
            if (out) await page.screenshot({ path: path.join(out, `after-${width}-${mode}-${size}-${screen}.png`), animations: 'disabled' })
          }

          await page.goto(app.url)
          await page.locator('.tt-row').first().waitFor()
          await check('chats')

          // Open the thread containing the long markdown reply + composer.
          await page.locator('.tt-row', { hasText: 'A little room for the week' }).click()
          await page.locator('.tt-message', { hasText: 'Weekly synthesis and next steps' }).waitFor()
          await page.locator('.tt-message', { hasText: 'Weekly synthesis and next steps' }).scrollIntoViewIfNeeded()
          await check('thread')

          // Open a pop-up sheet (Thread actions).
          await page.locator('.tt-head button[aria-label="Thread actions"]').click()
          await page.locator('[data-menu-surface]:popover-open').waitFor()
          await check('sheet')
          await page.keyboard.press('Escape')
          await settle(page)

          // Files screen.
          if (isPhone) {
            await page.locator('.tt-head button[aria-label="Back"]').first().click()
            await settle(page)
            await page.locator('.nav button', { hasText: 'Files' }).click()
          } else {
            await page.locator('.rail button', { hasText: 'Files' }).click()
          }
          await page.locator('.fs-folder, .fs-entry').first().waitFor()
          await check('files')

          // Settings > Appearance (SIZE card visible).
          if (isPhone) {
            await page.locator('.nav button[aria-label="Settings"]').click()
            await page.getByText('Appearance', { exact: true }).click()
          } else {
            await page.locator('.rail button[aria-label="Settings"]').click()
            await page.locator('.setnav').getByText('Appearance', { exact: true }).click()
          }
          await page.locator('#appearance-size').scrollIntoViewIfNeeded()
          await page.locator('.appearance-reset').scrollIntoViewIfNeeded()
          await check('settings')

          assert.deepEqual(errors, [], `${width}-${mode}-${size} page errors`)
          console.log(`PASS ${width} ${mode} ${size}% (--ui-scale: ${expectedScale[size]})`)
          await ctx.close()
        }
      }
    }

    // Verify older-host refusal fallback for 60% and 200%, reload persistence,
    // and automatic migration once the host accepts 60..200.
    values.phone = { mode: 'light', size: 100, theme: 'latte' }
    refuseExtended = true
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: 'light', isMobile: true, hasTouch: true, reducedMotion: 'reduce' })
    const page = await ctx.newPage()
    await page.goto(app.url)
    await page.locator('.nav button[aria-label="Settings"]').click()
    await page.getByText('Appearance', { exact: true }).click()
    await page.locator('#appearance-zoom').evaluate((el) => {
      el.value = '60'
      el.dispatchEvent(new Event('input', { bubbles: true }))
      el.dispatchEvent(new Event('change', { bubbles: true }))
    })
    await page.waitForFunction(() => document.querySelector('.appearance-status')?.textContent.includes('Size saved on this device'))
    assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--ui-scale').trim()), '0.54')
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('loam.appearance.size')).phone), 60)

    await page.reload()
    await page.locator('.tt-row').first().waitFor()
    assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--ui-scale').trim()), '0.54')

    refuseExtended = false
    await page.reload()
    await page.waitForFunction(() => localStorage.getItem('loam.appearance.size') === null)
    assert.equal(values.phone.size, 60)
    await ctx.close()
    console.log('PASS older-host extended-size fallback and migration')
  }

  if (out) {
    await writeFile(path.join(out, baseline ? 'baseline.json' : 'after.json'), JSON.stringify({ fixtureOnly: true, summary }, null, 2) + '\n')
  }
} finally {
  await browser.close()
  await app.close()
}
