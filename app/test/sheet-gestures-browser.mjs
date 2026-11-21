// Actual shared surfaces and Chromium touch input; synthetic broker only.
import assert from 'node:assert/strict'
import { readFile, readdir, mkdir, writeFile } from 'node:fs/promises'
import { acmePluginFixture } from './acme-plugin-fixture.mjs'
import { pluginPageRoutes } from './plugin-page-fixture.js'
import { shippedPlugins } from '../src/shipped-plugins.js'
import path from 'node:path'
const out = process.env.SHEET_EVIDENCE
if (!out) throw new Error('Set SHEET_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const app = await acmePluginFixture(), results = []
// This gesture walk needs the audio surfaces as well as the integration page.
const plugins = (await shippedPlugins('public')).map(row => row.switch === 'plugin' ? { ...row, on: true } : row)
for (const row of plugins.filter(row => row.has.page_main)) {
  const root = path.join('public', row.folder)
  pluginPageRoutes(app.routes, row, root, (await readdir(path.join(root, 'page'))).map(file => `page/${file}`))
}
app.routes['GET /v1/plugins'] = (req, res) => json(res, { plugins: [...plugins, app.plugin] })
const height = Number(process.env.SHEET_HEIGHT) || 900
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
const json = (res, value) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(value)) }
const record = await app.f.api('GET', '/threads/a-quieter-week')
record.messages.push({ id: 'long-reply', author: 'sprout', kind: 'message', text: 'Leave a little room between plans and gather the useful details for a calmer week. '.repeat(20) })
record.thread.members[0].background = 1
record.thread.members[0].background_tasks = [{ id: 'layout', description: 'Check sheet gestures', started_at: new Date().toISOString() }]
app.routes['GET /v1/threads/a-quieter-week'] = (req, res) => json(res, record)
app.routes['GET /v1/speech/health'] = (req, res) => json(res, { available: true })
try {
 for (const theme of ['light', 'dark']) {
  const ctx = await browser.newContext({ viewport: { width: 390, height }, isMobile: true, hasTouch: true, colorScheme: theme })
  await ctx.route('**/app.js', r => r.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__sheets = { threads, state, files, picker, schedule, openSheet, goSettings };' }))
  await ctx.route('**/api/health', r => r.fulfill({ json: { configured: true, ok: true, speech: true } }))
  await ctx.route('**/x/read-aloud/status', r => r.fulfill({ json: { speech: true } }))
  await ctx.route('**/api/files/roots', r => r.fulfill({ json: { roots: [{ root: 'vault', name: 'Vault', writable: true }] } }))
  const page = await ctx.newPage(), errors = []
  page.setDefaultTimeout(10000)
  page.on('pageerror', e => errors.push(e.message))
  const cdp = await ctx.newCDPSession(page)
  const settle = () => page.waitForTimeout(300)
  const touch = (type, x, y, timestamp) => cdp.send('Input.dispatchTouchEvent', { type, ...(timestamp === undefined ? {} : { timestamp }), touchPoints: type === 'touchEnd' || type === 'touchCancel' ? [] : [{ x, y, id: 1 }] })
  async function pull(surface, distance, { target = '.sheet-top', flick = false, cancel = false, redraw = false } = {}) {
    const box = await surface.boundingBox(), handle = target ? await surface.locator(target).first().boundingBox() : null
    const x = handle ? handle.x + handle.width / 2 : box.x + box.width / 2
    const y = handle ? handle.y + 8 : box.y + 100
    // Explicit input times keep flick velocity independent of driver/CI latency.
    let timestamp = flick ? Date.now() / 1000 : undefined
    await touch('touchStart', x, y, timestamp)
    const steps = flick ? 2 : 5
    for (let i = 1; i <= steps; i++) {
      await page.waitForTimeout(flick ? 10 : 45)
      if (flick) timestamp += .012
      await touch('touchMove', x, y + distance * i / steps, timestamp)
    }
    if (!flick) { await page.waitForTimeout(90); await touch('touchMove', x, y + distance) }
    if (redraw) { await page.evaluate(() => __sheets.schedule()); await page.waitForTimeout(30) }
    const during = await surface.evaluate(n => ({ transform: getComputedStyle(n).transform, scrim: getComputedStyle(n, '::backdrop').opacity, inline: n.style.transform }))
    if (flick) timestamp += .012
    await touch(cancel ? 'touchCancel' : 'touchEnd', x, y + distance, timestamp)
    return during
  }
  async function check(name, opener, selector) {
    await opener()
    const surface = page.locator(selector)
    await surface.waitFor()
    if (name === 'agent-card') {
      const opening = await surface.evaluate(n => {
        n.style.animationName = 'none'; void n.offsetHeight; n.style.animationName = 'menuSheetArrive'
        const a = n.getAnimations().find(a => a.animationName === 'menuSheetArrive')
        a.pause(); a.currentTime = a.effect.getTiming().duration / 4
        const frame = { transform: getComputedStyle(n).transform, duration: a.effect.getTiming().duration,
          token: getComputedStyle(n).getPropertyValue('--motion-normal').trim() }
        a.finish(); return frame
      })
      assert.ok(new DOMMatrixFallback(opening.transform).y > 0, 'opening actually slides up')
      assert.equal(opening.duration, parseFloat(opening.token), 'opening uses motion token')
      results.push({ theme, name: 'opening-motion', ...opening })
    }
    await settle()
    assert.equal(await surface.getAttribute('data-menu-layout'), 'sheet', name)
    if (name === 'file-read-aloud') {
      const range = await pull(surface, 30, { target: '.file-speech-range' })
      assert.equal(range.inline, '', 'the range keeps its own gesture')
    }
    const box = await surface.boundingBox()
    assert.ok(box.x >= -1 && box.y >= -1 && box.x + box.width <= 391 && box.y + box.height <= height + 1, `${name} stays inside the visible viewport: ${JSON.stringify(box)}`)
    const short = Math.min(30, box.height / 5)
    const during = await pull(surface, short, { redraw: true })
    assert.match(during.inline, /translateY/, name + ' takes finger')
    assert.ok(Math.abs(Number(new DOMMatrixFallback(during.transform).y) - short) < 2, name + ' computed transform follows finger')
    assert.ok(Number(during.scrim) < 1 && Number(during.scrim) > 0, name + ' backdrop follows finger')
    await settle()
    assert.ok(await surface.isVisible(), name + ' short drag stays open')
    assert.ok(Math.abs((await surface.boundingBox()).y - box.y) < 1, name + ' springs back')
    await page.screenshot({ path: `${out}/sheet-${name}-${theme}.png` })
    const long = await pull(surface, box.height * .38)
    assert.ok(Number(long.scrim) < .7, name + ' backdrop fades')
    await surface.waitFor({ state: 'hidden' })
    results.push({ theme, name, shortDrag: 'spring back', threshold: 'dismiss', during, height: box.height })
    console.log(`PASS ${theme} ${name}: finger/paint, scrim fade, redraw, short spring back, threshold dismiss`)
  }
  // No window.DOMMatrix in the node process; Chromium's 2D matrix's last item is Y.
  function DOMMatrixFallback(matrix) { this.y = matrix === 'none' ? 0 : matrix.slice(matrix.indexOf('(') + 1, -1).split(',').map(Number).at(-1) }
  try {
    await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
    await check('agent-card', () => page.getByRole('button', { name: 'Sprout menu', exact: true }).tap(), '.tt-persona-card')
    await check('thread-list', () => page.getByRole('button', { name: 'Thread list actions', exact: true }).tap(), '.tt-list-menu')
    await check('settings', () => page.getByRole('button', { name: 'Settings', exact: true }).first().tap(), '.sheet.settings')
    await check('voice-picker', async () => {
      await page.getByRole('button', { name: 'Settings', exact: true }).first().tap()
      await page.evaluate(() => __sheets.goSettings('read-aloud')); await page.locator('#files-voice').tap()
    }, '.voice-menu')
    await page.keyboard.press('Escape')
    const thread = async () => { await page.evaluate(() => __sheets.threads.open('a-quieter-week')); await page.locator('.tt-title').waitFor(); await settle() }
    await thread()
    await check('thread-actions', () => page.getByRole('button', { name: 'Thread actions', exact: true }).tap(), '.tt-header-menu')
    await check('delete-confirm', async () => { await page.getByRole('button', { name: 'Thread actions', exact: true }).tap(); await page.getByRole('button', { name: 'Delete thread', exact: true }).tap() }, '.tt-header-confirm')
    await check('providers-models', () => page.getByRole('button', { name: 'Providers & models', exact: true }).tap(), '.tt-configuration')
    await check('model-options', async () => { await page.getByRole('button', { name: 'Providers & models', exact: true }).tap(); await page.locator('.tt-model-select').first().tap() }, '.tt-configuration')
    await check('provider-options', async () => { await page.getByRole('button', { name: 'Providers & models', exact: true }).tap(); await page.locator('.tt-engine-select').first().tap() }, '.tt-configuration')
    await check('background-tasks', () => page.locator('.tt-tasks-button').tap(), '.tt-tasks-panel')
    await check('task-details', async () => { await page.locator('.tt-tasks-button').tap(); await page.locator('.tt-task-row').first().tap() }, '.tt-tasks-panel')
    await check('branch-new-thread', () => page.locator('.tt-footer-branch').last().tap(), '.tt-branch-panel')
    await check('composer-add', () => page.getByRole('button', { name: 'Attach a file', exact: true }).tap(), '[data-menu-title="Add to this message"]')
    await check('attachment-picker', async () => { await page.getByRole('button', { name: 'Attach a file', exact: true }).tap(); await page.getByRole('button', { name: /Pick from Files/ }).tap() }, '.fs-picker')
    await check('read-aloud', () => page.locator('.tt-message .speakbtn').last().tap(), '.speakmenu')
    await page.locator('.tt-board-button').tap()
    await check('board-item', () => page.locator('.tt-item-actions > button').first().tap(), '.tt-item-menu')
    await check('board-move', async () => { await page.locator('.tt-item-actions > button').first().tap(); await page.getByRole('button', { name: 'Move to…', exact: true }).tap() }, '.tt-item-menu')
    await page.getByRole('button', { name: 'Close board', exact: true }).tap()
    await check('file-actions', () => page.evaluate(() => { __sheets.files.st.sheet = { kind: 'folder', name: 'Notes', toggle() {} }; __sheets.schedule() }), '.fs-sheet')
    await check('file-name', () => page.evaluate(() => { __sheets.files.st.sheet = { kind: 'name', what: 'folder', name: 'New folder' }; __sheets.schedule() }), '.fs-sheet')
    await check('file-leave', () => page.evaluate(() => { __sheets.files.st.sheet = { kind: 'leave', name: 'Notes.md', proceed() {} }; __sheets.schedule() }), '.fs-sheet')
    await check('acme', () => page.evaluate(() => __sheets.openSheet({ kind: 'acme' })), '.sheet.acme')
    await check('acme-login', () => page.evaluate(() => __sheets.openSheet({ kind: 'acme-login' })), '.sheet.acme-login')
    await check('files-add', () => page.evaluate(() => { __sheets.files.st.sheet = { kind: 'add' }; __sheets.schedule() }), '.fs-sheet')
    await check('file-upload', () => page.evaluate(() => { __sheets.files.st.sheet = { kind: 'upload', root: 'vault', folder: 'Notes', items: [] }; __sheets.schedule() }), '.fs-sheet')
    await check('file-matches', () => page.evaluate(() => { __sheets.files.st.sheet = { kind: 'choose', ref: 'Notes', total: 0, candidates: [] }; __sheets.schedule() }), '.fs-sheet')
    await check('file-read-aloud', () => page.evaluate(async () => {
      const { documentStops } = await import('/plugins/read-aloud/page/document-speech.js')
      const stops = documentStops(100)
      __sheets.openSheet({ kind: 'file-speech', file: { root: 'vault', path: 'Notes.md' }, title: 'Notes.md', text: 'A short note. '.repeat(30), words: 100, stops, at: stops.length - 1 })
    }), '.sheet.file-speech')
    // A separate controller/root exercises the same shell with nested scrolling.
    const mountShared = () => page.evaluate(async () => {
      const { h } = await import('/ui.js'), { menuSurface, createMenuSurfaces } = await import('/menu-surface.js')
      const node = menuSurface(h('section', { id: 'gesture-fixture' }, h('div', { class: 'gesture-body', style: { height: '350px', overflow: 'auto' } }, h('p', { text: 'Scrollable content', style: { height: '900px' } }))), { title: 'Shared sheet', close: () => node.remove() })
      const host = document.createElement('div'); document.body.append(host); host.append(node)
      globalThis.__gestureHost = host; globalThis.__gestureSurfaces = createMenuSurfaces(host); __gestureSurfaces.sync()
    })
    await check('shared-shell', mountShared, '#gesture-fixture')
    await page.evaluate(() => __gestureHost.remove())
    await mountShared()
    const shared = page.locator('#gesture-fixture'); await settle()
    const initial = await shared.boundingBox()
    await shared.locator('.gesture-body').evaluate(n => n.scrollTop = 100)
    const scroll = await pull(shared, 40, { target: '.gesture-body' })
    assert.equal(scroll.inline, '', 'scrolled body leaves gesture with scrolling')
    await shared.locator('.gesture-body').evaluate(n => n.scrollTop = 0)
    const body = await pull(shared, 30, { target: '.gesture-body' }); assert.match(body.inline, /translateY/, 'body at top drags'); await settle()
    assert.ok(Math.abs((await shared.boundingBox()).y - initial.y) < 1, 'body short pull springs back')
    const header = await pull(shared, 30, { target: '.surface-heading' }); assert.match(header.inline, /translateY/, 'header drags'); await settle()
    const cancel = await pull(shared, 100, { cancel: true }); assert.match(cancel.inline, /translateY/); await settle()
    assert.ok(Math.abs((await shared.boundingBox()).y - initial.y) < 1, 'cancel springs back')
    await pull(shared, 80, { flick: true }); await shared.waitFor({ state: 'hidden' })
    console.log(`PASS ${theme} shared shell: nested scroll, body-at-top, cancellation, flick`)
    await page.evaluate(() => __gestureHost.remove())
    // Reduced motion fades only, on opening and on dismissal.
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.evaluate(() => __sheets.goSettings('root'))
    const reduced = page.locator('.sheet.settings'); await reduced.waitFor()
    assert.equal(await reduced.evaluate(n => getComputedStyle(n).animationName), 'menuFade')
    const reducedFrame = await reduced.evaluate(n => {
      const a = n.getAnimations().find(a => a.animationName === 'menuFade')
      if (!a) return null
      a.pause(); a.currentTime = a.effect.getTiming().duration / 4
      const frame = { transform: getComputedStyle(n).transform, opacity: getComputedStyle(n).opacity }
      a.finish(); return frame
    })
    if (reducedFrame) { assert.equal(reducedFrame.transform, 'none'); assert.ok(Number(reducedFrame.opacity) > 0 && Number(reducedFrame.opacity) < 1) }
    await settle()
    const fade = await pull(reduced, (await reduced.boundingBox()).height * .4)
    assert.equal(fade.transform, 'none', 'reduced motion never slides'); await reduced.waitFor({ state: 'hidden' })
    results.push({ theme, name: 'shared-shell-extras', scroll: true, bodyAtTop: true, cancel: true, flick: true, reducedMotion: true })
    assert.deepEqual(errors, [])
  } catch (e) { await page.screenshot({ path: `${out}/failure-${theme}.png` }); throw e }
  finally { await ctx.close() }
 }
 await writeFile(`${out}/gestures.json`, JSON.stringify({ browser: browser.version(), results }, null, 2) + '\n')
} finally { await browser.close(); await app.close() }
