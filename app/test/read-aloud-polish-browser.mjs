// Real Chromium, real page and CSS, fixture broker/audio. No paid synthesis.
// Optional: LOAM_PLAYWRIGHT and LOAM_CHROME name separately installed tools.
import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { mkdir, writeFile } from 'node:fs/promises'
import { once } from 'node:events'
import { createApp } from '../src/server.js'
import { readConfig } from '../src/config.js'
import { fakeBroker, TOKEN, shut } from './harness.js'
import { filesHost } from './files-host.js'
import { row, readAnswer, listAnswer } from './fixtures/files-contract.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright-core')
const output = process.env.READ_ALOUD_EVIDENCE
if (!output) throw new Error('Set READ_ALOUD_EVIDENCE to an evidence directory outside the repository')
const repository = fileURLToPath(new URL('..', import.meta.url))
const relativeOutput = path.relative(repository, path.resolve(output))
if (!relativeOutput || (!relativeOutput.startsWith(`..${path.sep}`) && !path.isAbsolute(relativeOutput))) {
  throw new Error('READ_ALOUD_EVIDENCE must be outside the repository')
}
await mkdir(output, { recursive: true })
const note = '# A little room to think\n\n' + Array.from({ length: 310 }, () => 'Make time for the work that matters and leave room.').join('\n\n')
const { routes } = filesHost()
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
routes['GET /v1/files/stat'] = (req, res, url) => json(res, row('vault', url.searchParams.get('path')))
routes['GET /v1/files/read'] = (req, res, url) => {
  const name = url.searchParams.get('path')
  json(res, readAnswer(row('vault', name), name.endsWith('tiny.md') ? 'Keep a little room.' : note, 'sha256:fixture'))
}
routes['GET /v1/files/list'] = (req, res) => json(res, listAnswer(row('vault', 'Notes'), [row('vault', 'Notes/A little room.md')]))
const broker = await fakeBroker(routes)
let phase = 'none'
let fail = false
let cached = false
const requests = []
const speech = {
  async status() { return { available: true } },
  async peek() { return { status: 200, body: { status: 'none' } } },
  async request(body) {
    requests.push(body)
    if (cached) return { status: 200, body: { id: 'fixture-audio', status: 'ready', duration_s: 2, url: '/x/read-aloud/speech/fixture-audio/audio.mp3' } }
    phase = 'preparing'
    return { status: 202, body: { id: 'fixture-audio', status: phase, stage: 'voicing', progress: 0 } }
  },
  async lookup() { return fail ? { id: 'fixture-audio', status: 'failed', error: "Couldn't prepare this cleanly — try again." } : { id: 'fixture-audio', status: phase, duration_s: 180, url: '/x/read-aloud/speech/fixture-audio/audio.mp3' } },
  async audio() { return null },
  stop() {},
}
const { server } = createApp({ config: readConfig({ BROKER_URL: broker.url, BROKER_TOKEN: TOKEN, LOG_LEVEL: 'off' }), speech })
server.listen(0, '127.0.0.1')
await once(server, 'listening')
const url = `http://127.0.0.1:${server.address().port}`
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-gpu'] })
const results = []
const focusResults = []
const geometry = (page) => page.evaluate(() => {
  const rect = (selector) => {
    const el = document.querySelector(selector)
    if (!el) return null
    const r = el.getBoundingClientRect()
    return { x: r.x, y: r.y, width: r.width, height: r.height, bottom: r.bottom, overflow: el.scrollWidth > el.clientWidth }
  }
  return { page: rect('.desk'), dialog: rect('.dialog.file-speech'), sheet: rect('.sheet.file-speech'), focus: document.activeElement?.className }
})
const paint = async (page, name) => page.screenshot({ path: path.join(output, `${name}.png`) })
try {
  for (const width of [1440, 390]) {
    cached = false
    const page = await browser.newPage({ viewport: { width, height: 900 }, isMobile: width === 390, hasTouch: width === 390 })
    const errors = []
    page.on('pageerror', (e) => errors.push(e.message))
    await page.addInitScript(() => { globalThis.__plays = 0; HTMLMediaElement.prototype.play = function () { globalThis.__plays++; return Promise.resolve() } })
    await page.goto(`${url}/files/vault/Notes/A%20little%20room.md`)
    await page.locator('.fs-speak').waitFor()
    const before = await geometry(page)
    const trigger = await page.locator('.fs-speak').boundingBox()
    await page.locator('.fs-speak').click()
    await page.locator('.file-speech-slider').waitFor()
    await page.waitForTimeout(300)
    const opened = await geometry(page)
    if (process.env.CHECK_BEFORE) {
      const underPointer = await page.evaluate(({ x, y }) => {
        const el = document.elementFromPoint(x, y)
        return { class: el.className, title: el.getAttribute('title'), focused: document.activeElement === el }
      }, { x: trigger.x + trigger.width / 2, y: trigger.y + trigger.height / 2 })
      await paint(page, `before-${width}`)
      results.push({ width, before, opened, underPointer })
      await page.keyboard.press('Escape')
      await page.goto(url)
      await page.getByRole('button', { name: 'Settings', exact: true }).first().click()
      if (width === 390) await page.locator('.setrow').filter({ hasText: 'Files voice' }).click()
      else await page.locator('.navrow').filter({ hasText: 'Read aloud' }).click()
      await page.locator('#files-voice').click()
      await paint(page, `before-select-open-${width}`)
      await page.close()
      continue
    }
    const slider = page.locator('.file-speech-slider')
    const sheet = page.locator('.sheet.file-speech')
    const button = page.locator('.file-speech-action')
    const choice = page.locator('.file-speech-value:not(.previous)')
    // Focus paint belongs to the thumb; opening focus is separate from a
    // person using the keyboard. Exercise both against the real stylesheet.
    const focusPaint = async (kind) => {
      await page.waitForTimeout(250)
      const style = await slider.evaluate((el) => {
        const range = el.closest('.file-speech-range')
        return { focused: document.activeElement === el, visible: el.matches(':focus-visible'),
          outline: getComputedStyle(range).outlineStyle,
          shadow: getComputedStyle(range.querySelector('.file-speech-thumb')).boxShadow }
      })
      assert.equal(style.focused, true)
      if (!process.env.FOCUS_BASELINE) {
        assert.equal(style.outline, 'none', 'range rectangle returned')
        assert.equal(style.shadow.includes('6px'), kind === 'keyboard', `thumb halo for ${kind}`)
      }
      const name = `${process.env.FOCUS_BASELINE ? 'before' : 'after'}-focus-${kind}-${width}`
      await paint(page, name)
      focusResults.push({ width, kind, name: `${name}.png`, ...style })
    }
    await focusPaint('pointer-open')
    await page.keyboard.press('End')
    await focusPaint('keyboard')
    if (!process.env.FOCUS_BASELINE) {
      await page.emulateMedia({ forcedColors: 'active' })
      assert.equal(await page.locator('.file-speech-thumb').evaluate((el) => getComputedStyle(el).outlineStyle), 'solid', 'forced colors lost keyboard focus')
      await page.emulateMedia({ forcedColors: 'none' })
    }
    await slider.click({ position: { x: 16, y: 22 } })
    await focusPaint('pointer')
    // Tab away and back must restore visibility even at the same stop.
    await page.keyboard.press('Tab')
    await page.keyboard.press('Shift+Tab')
    if (!process.env.FOCUS_BASELINE) {
      const shadow = await page.locator('.file-speech-thumb').evaluate((el) => getComputedStyle(el).boxShadow)
      assert.ok(shadow.includes('6px'), 'Tab return lost the halo')
      await page.keyboard.press('Escape')
      await sheet.waitFor({ state: 'detached' })
      await page.locator('.fs-speak').press('Enter')
      await slider.waitFor()
      await focusPaint('keyboard-open')
      await page.keyboard.press('ArrowRight')
      assert.ok(await page.locator('.file-speech-thumb').evaluate((el) => getComputedStyle(el).boxShadow.includes('6px')), 'keyboard use after opening lost the halo')
    }
    await page.keyboard.press('Home')
    await page.keyboard.press('ArrowRight')
    await page.waitForFunction(() => document.querySelector('.file-speech-slider').value === '1')
    assert.equal(await slider.inputValue(), '1', 'three-minute default')
    assert.equal(opened.focus, 'file-speech-slider')
    assert.equal(opened.sheet.overflow, false)
    assert.ok(opened.sheet.y >= 0 && opened.sheet.bottom <= 900)
    if (width === 1440) {
      assert.equal(opened.page.height, before.page.height, 'dialog shrank the page')
      assert.equal(opened.dialog.height, 900)
      assert.ok(Math.abs(opened.sheet.y + opened.sheet.height / 2 - 450) < 1, 'not vertically centered')
    } else assert.equal(opened.sheet.bottom, 900)
    assert.deepEqual(await page.locator('.file-speech-stops span').allTextContents(), ['1 min', '3 min', '5 min', '10 min', 'Full'])
    assert.equal(await page.locator('.file-speech-scrim').getAttribute('title'), null)
    assert.equal(await page.locator('.file-speech-close').getAttribute('title'), null)
    assert.equal(await page.locator('.file-speech-close').evaluate((el) => getComputedStyle(el, '::after').visibility), 'hidden')
    await paint(page, `sheet-default-${width}`)
    if (width === 1440) {
      await page.locator('.file-speech-close').hover()
      await page.waitForTimeout(150)
      assert.equal(await page.locator('.file-speech-close').evaluate((el) => getComputedStyle(el, '::after').visibility), 'hidden', 'tooltip appeared without its delay')
      await page.waitForTimeout(650)
      assert.equal(await page.locator('.file-speech-close').evaluate((el) => getComputedStyle(el, '::after').visibility), 'visible', 'delayed hover tooltip did not appear')
      await slider.hover()
    }
    // A native range owns arrows, Home and End, even behind the custom track.
    await page.keyboard.press('End')
    await page.waitForFunction(() => document.querySelector('.file-speech-value:not(.previous)').textContent.includes('Full'))
    await page.waitForTimeout(250)
    assert.match(await choice.textContent(), /Full · 21 minWord for word/)
    await paint(page, `sheet-full-${width}`)
    await page.keyboard.press('Home')
    await page.keyboard.press('ArrowRight')
    await page.waitForFunction(() => document.querySelector('.file-speech-value:not(.previous)').textContent.startsWith('3 min'))
    const buttonBefore = await button.boundingBox()
    const prepare = () => Promise.all([page.waitForResponse((r) => r.url() === `${url}/x/read-aloud/speech` && r.request().method() === 'POST'), button.click()])
    await prepare()
    await page.locator('.file-speech-action[data-phase=making]').waitFor()
    await page.waitForTimeout(250)
    await paint(page, `sheet-preparing-${width}`)
    assert.equal(await page.evaluate(() => globalThis.__plays), 0)
    assert.deepEqual(await button.boundingBox(), buttonBefore, 'button moved while preparing')
    const requested = requests.length
    await button.click({ force: true })
    assert.equal(requests.length, requested, 'a second prepare request was sent')
    assert.equal(requests.at(-1).minutes, 3)
    assert.equal(requests.at(-1).mode, 'brief')
    assert.equal(requests.at(-1).voice, 'Aoede')
    phase = 'ready'
    await page.locator('.file-speech-action[data-phase=ready]').waitFor()
    assert.equal(await page.evaluate(() => globalThis.__plays), 0, 'audio auto-played')
    assert.deepEqual(await button.boundingBox(), buttonBefore, 'button moved when ready')
    // Keyboard focus stays inside the sheet and goes back to its trigger.
    await page.keyboard.press('Tab')
    assert.equal(await page.locator('.file-speech-close').evaluate((el) => document.activeElement === el), true)
    await page.keyboard.press('Shift+Tab')
    assert.equal(await button.evaluate((el) => document.activeElement === el), true)
    await button.click()
    await sheet.waitFor({ state: 'detached' })
    assert.equal(await page.evaluate(() => globalThis.__plays), 1, 'Play did not play')
    assert.equal(await page.locator('.fs-speak').evaluate((el) => document.activeElement === el), true)
    // Clean failure remains visible with a retry, in the same dialog.
    await page.locator('.fs-speak').click()
    await slider.waitFor()
    await slider.press('End')
    fail = true
    await prepare()
    await page.getByRole('alert').filter({ hasText: "Couldn't prepare this cleanly" }).waitFor()
    assert.equal(await button.getAttribute('aria-label'), 'Try again')
    fail = false
    await prepare()
    await page.locator('.file-speech-action[data-phase=making]').waitFor()
    phase = 'ready'
    await page.locator('.file-speech-action[data-phase=ready]').waitFor()
    assert.equal(requests.at(-1).minutes, 'full')
    assert.equal(requests.at(-1).mode, 'verbatim')
    await page.keyboard.press('Escape')
    await sheet.waitFor({ state: 'detached' })
    await page.locator('.fs-speak').click()
    await slider.waitFor()
    await page.waitForTimeout(260)
    if (width === 390) {
      // Real touch events through CDP: a slider gesture stays a slider;
      // the grab handle follows the finger and dismisses the sheet.
      const cdp = await page.context().newCDPSession(page)
      const drag = async (x, y, distance) => {
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] })
        for (let i = 1; i <= 5; i++) {
          await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x, y: y + distance * i / 5 }] })
          await page.waitForTimeout(20)
        }
      }
      const range = await slider.boundingBox()
      await drag(range.x + range.width / 2, range.y + 20, 20)
      assert.equal(await sheet.evaluate((el) => getComputedStyle(el).transform), 'none', 'slider dragged the sheet')
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
      const grip = await page.locator('.file-speech .grip').boundingBox()
      await drag(grip.x + 20, grip.y + 2, 180)
      assert.notEqual(await sheet.evaluate((el) => getComputedStyle(el).transform), 'none', 'handle did not follow the finger')
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
      await sheet.waitFor({ state: 'detached' })
      await cdp.detach()
    } else {
      await page.locator('.dialog.file-speech').click({ position: { x: 10, y: 10 } })
      await sheet.waitFor({ state: 'detached' })
    }
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.locator('.fs-speak').click()
    await slider.waitFor()
    assert.equal(await sheet.evaluate((el) => getComputedStyle(el).animationName), 'none')
    assert.equal(await page.locator('.file-speech-thumb').evaluate((el) => getComputedStyle(el).transitionDuration), '0s')
    await page.keyboard.press('Escape')
    await sheet.waitFor({ state: 'detached' })
    await page.emulateMedia({ reducedMotion: 'no-preference' })
    // Voice selection is the browser's native control, with app-owned chrome.
    await page.goto(url)
    await page.getByRole('button', { name: 'Settings', exact: true }).first().click()
    if (width === 390) await page.locator('.setrow').filter({ hasText: 'Files voice' }).click()
    else await page.locator('.navrow').filter({ hasText: 'Read aloud' }).click()
    const voice = page.locator('#files-voice')
    await voice.waitFor()
    assert.equal(await voice.locator('option').count(), 30)
    const selectGeometry = await page.locator('.settings-select').evaluate((el) => {
      const box = el.getBoundingClientRect(), chevron = el.querySelector('svg').getBoundingClientRect()
      return { width: box.width, rightInset: box.right - chevron.right, centered: Math.abs(box.y + box.height / 2 - chevron.y - chevron.height / 2) < 1, overflow: el.scrollWidth > el.clientWidth }
    })
    assert.equal(selectGeometry.rightInset, 16)
    assert.equal(selectGeometry.centered, true)
    assert.equal(selectGeometry.overflow, false)
    await paint(page, `settings-select-closed-${width}`)
    await voice.click()
    await paint(page, `settings-select-open-${width}`)
    await page.keyboard.press('Escape')
    await voice.focus()
    await page.keyboard.press('End')
    await page.keyboard.press('Enter')
    assert.equal(await voice.inputValue(), 'Sulafat', `native keyboard selection at ${width}`)
    assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('loam-ui.read-aloud'))), { voice: 'Sulafat' })
    await Promise.all([page.waitForResponse((r) => r.url() === `${url}/x/read-aloud/speech` && r.request().method() === 'POST'), page.locator('.read-aloud-preview').click()])
    phase = 'ready'
    await page.locator('.read-aloud-sample .player-play').waitFor()
    assert.equal(requests.at(-1).voice, 'Sulafat')
    assert.deepEqual(errors, [], 'browser exceptions')
    // A note with Full alone still receives focus. Even an immediate cache
    // hit waits for Play, rather than treating Prepare as permission to play.
    await page.goto(`${url}/files/vault/Notes/tiny.md`)
    await page.locator('.fs-speak').click()
    await slider.waitFor()
    assert.equal(await slider.getAttribute('max'), '0')
    assert.equal(await slider.getAttribute('aria-disabled'), 'true')
    assert.equal(await slider.evaluate((el) => document.activeElement === el), true)
    await slider.press('End')
    assert.equal(await slider.inputValue(), '0')
    cached = true
    await prepare()
    await page.locator('.file-speech-action[data-phase=ready]').waitFor()
    assert.equal(await page.evaluate(() => globalThis.__plays), 0, 'cached Prepare auto-played')
    assert.equal(requests.at(-1).minutes, 'full')
    await page.locator(width === 1440 ? '.dialog.file-speech' : '.file-speech-scrim').click({ position: { x: 10, y: 10 } })
    await sheet.waitFor({ state: 'detached' })
    results.push({ width, before, opened, selectGeometry, checks: ['default', 'full', 'preparing', 'ready', 'no autoplay', 'play', 'failure and retry', 'focus trap and restore', width === 390 ? 'swipe and slider touch' : 'backdrop', 'Escape', 'reduced motion', 'native select keyboard', 'voice persistence and sample', 'single-stop focus', 'cached Prepare waits for Play'], errors })
    await page.close()
  }
  assert.equal(broker.seen.some((r) => r.method === 'POST' && r.path.startsWith('/v1/sessions')), false, 'read aloud started an agent')
  await writeFile(path.join(output, process.env.FOCUS_BASELINE ? 'focus-before.json' : process.env.CHECK_BEFORE ? 'before.json' : 'browser.json'), JSON.stringify({ browser: browser.version(), fixtures: true, results, focusResults, requests: requests.map(({ source, mode, minutes, voice }) => ({ source, mode, minutes, voice })) }, null, 2) + '\n')
  console.log(JSON.stringify({ widths: [1440, 390], results }))
} finally {
  await browser.close()
  await shut(server)
  await shut(broker.server)
}
