import test from 'node:test'
import assert from 'node:assert/strict'
import { oneProductFixture } from './one-product-fixture.mjs'
import { deleteHost } from './fixtures/files-delete.js'

// Pick a visible part of the real control outside the surface. A locator click
// would wait for the overlay to disappear instead of testing its hit target.
async function outsidePoint(control, surface) {
  const panel = await surface.boundingBox()
  for (const node of await control.all()) {
    const box = await node.boundingBox()
    if (!box) continue
    const { width, height } = control.page().viewportSize()
    for (const fx of [.5, .1, .9]) for (const fy of [.5, .1, .9]) {
      const x = Math.round(box.x + fx * box.width), y = Math.round(box.y + fy * box.height)
      if (x <= 0 || y <= 0 || x >= width || y >= height) continue
      if (x < panel.x || y < panel.y || x > panel.x + panel.width || y > panel.y + panel.height) return { node, x, y }
    }
  }
  assert.fail(`No exposed part of ${control} outside ${JSON.stringify(panel)}`)
}

for (const width of [390, 1440]) test(`outside dismissal owns the gesture at ${width}px`, {
  skip: !process.env.LOAM_PLAYWRIGHT, timeout: 90000,
}, async t => {
  const { chromium } = await import(process.env.LOAM_PLAYWRIGHT)
  const phone = width === 390
  const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
  t.after(() => browser.close())
  const page = await browser.newPage({ viewport: { width, height: phone ? 920 : 900 }, hasTouch: phone, isMobile: phone,
    serviceWorkers: 'block', reducedMotion: 'reduce' })
  page.setDefaultTimeout(5000)
  const app = await oneProductFixture(), files = deleteHost()
  t.after(() => app.close())
  Object.assign(app.routes, files.routes)
  const json = (res, value) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(value)) }
  // At the supported 75% phone scale the Settings sheet leaves a chat row exposed.
  app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, { phone: { size: 75 }, desktop: { size: 100 } })
  app.routes['GET /v1/apps'] = (req, res) => json(res, { sources: ['yours'], apps: ['Notes', 'Calendar'].map((name, i) => ({
    name, url: `https://app-${i}.example.com/`, source: 'yours', access: 'private', frame: true,
  })) })
  app.routes['GET /v1/activity'] = (req, res) => json(res, {
    items: [{ id: 'report', kind: 'job', project: 'Notebook', title: 'Finished review', outcome: 'completed', ts: new Date().toISOString() }],
    total: 1, next_offset: null, jobs: { slots: [], queue: [{ job: 'review', title: 'Review notes' }], landing: [] },
  })
  const button = name => page.getByRole('button', { name, exact: true })
  const surfaces = page.locator('[data-menu-surface]:popover-open, dialog[open], [data-menu-inputs]')
  async function dismiss(surface, control, expectedURL, remaining = 0, content = surface) {
    await surface.waitFor()
    await surface.evaluate(node => Promise.all(node.getAnimations().map(a => a.finished.catch(() => {}))))
    const { node, x, y } = await outsidePoint(control, content)
    await node.evaluate(el => {
      const events = window.backgroundEvents = []
      for (const type of ['pointerdown', 'click']) el.addEventListener(type, () => events.push(type), { once: true })
    })
    if (phone) await page.touchscreen.tap(x, y)
    else await page.mouse.click(x, y)
    await surface.waitFor({ state: 'hidden' })
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
    assert.deepEqual(await page.evaluate(() => window.backgroundEvents), [], `gesture reached ${control}`)
    assert.equal(page.url(), expectedURL)
    assert.equal(await surfaces.count(), remaining)
    assert.equal(page.context().pages().length, 1)
  }
  async function open(name) {
    if (phone) await button(name).tap()
    else await button(name).click()
  }

  // Ensure the Chats list really can scroll for the backdrop gesture checks.
  await Promise.all(Array.from({ length: 24 }, () => app.f.api('POST', '/threads', {})))
  await page.goto(app.url)
  const rows = page.locator('.tt-row')
  await rows.first().waitFor()
  const chatsURL = page.url()
  await open('Settings')
  await dismiss(page.locator('.sheet.settings'), rows, chatsURL)
  // Settings plugin screenshots open the shared image viewer above the sheet.
  await open('Settings')
  const settings = page.locator('.sheet.settings')
  await settings.waitFor()
  const settingsURL = page.url()
  await page.evaluate(async () => {
    const { openImageView } = await import('/image-view.js')
    openImageView([{ src: '/icon-32.png', name: 'Preview' }])
  })
  const viewer = page.locator('.image-view')
  await dismiss(viewer, settings.locator(phone ? '.setrow' : '.navrow'), settingsURL, 1, viewer.locator('img'))
  await page.keyboard.press('Escape')
  await settings.waitFor({ state: 'hidden' })
  if (!phone) {
    await open('Settings')
    await dismiss(page.locator('.sheet.settings'), button('Activity'), chatsURL)
  }
  await page.locator('[data-agent-menu]').first().click()
  await dismiss(page.locator('.tt-persona-card'), rows, chatsURL)
  await open('Thread list actions')
  const listMenu = page.locator('.tt-list-menu')
  await dismiss(listMenu, rows, chatsURL)
  await open('Thread list actions')
  await listMenu.waitFor()
  await listMenu.evaluate(n => Promise.all(n.getAnimations().map(a => a.finished)))
  const scroller = page.locator('.tt-list .scroll')
  const initialScroll = await scroller.evaluate(n => {
    if (n.scrollHeight <= n.clientHeight) throw Error('Expected scrollable Chats list')
    return n.scrollTop
  })
  const { x, y } = await outsidePoint(rows, listMenu)
  if (phone) {
    const touch = await page.context().newCDPSession(page)
    await touch.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] })
    for (const dy of [12, 32, 64]) await touch.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x, y: y - dy }] })
    await touch.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
    await touch.detach()
  } else {
    await page.mouse.move(x, y)
    await page.mouse.wheel(0, 420)
  }
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  assert.equal(await scroller.evaluate(n => n.scrollTop), initialScroll)
  assert.equal(await listMenu.isVisible(), true)
  await page.keyboard.press('Escape')
  await listMenu.waitFor({ state: 'hidden' })
  await open('Thread list actions')
  await listMenu.waitFor()
  await page.evaluate(() => history.back())
  await listMenu.waitFor({ state: 'hidden' })
  assert.equal(page.url(), chatsURL)

  await page.goto(app.url + '/threads/a-quieter-week')
  const threadURL = page.url(), header = button('Thread actions'), draft = page.locator('#draft')
  await open('Thread actions')
  await dismiss(page.locator('.tt-header-menu'), button('Providers & models').or(header), threadURL)
  await open('Thread actions')
  await open('Delete thread')
  await dismiss(page.locator('.tt-header-confirm'), header, threadURL)
  await open('Providers & models')
  const configuration = page.locator('.tt-configuration')
  await configuration.locator('.tt-model-select').first().click()
  await dismiss(page.locator('.tt-configuration-options'), draft, threadURL, 1)
  assert.equal(await draft.evaluate(n => n.contains(document.activeElement)), false)
  const provider = configuration.locator('.tt-engine-select').first()
  await provider.click()
  await dismiss(page.locator('.tt-configuration-options'), configuration.getByRole('button', { name: 'Done' }), threadURL, 1)
  await dismiss(configuration, phone ? header : draft, threadURL)
  assert.equal(await draft.evaluate(n => n.contains(document.activeElement)), false)

  const attach = page.getByTitle('Attach a file', { exact: true })
  await attach.click()
  await dismiss(page.locator('[data-menu-title="Add to this message"]'), header, threadURL)
  await attach.click()
  await page.getByRole('button', { name: /^Pick from Files/ }).click()
  await dismiss(page.locator('.fs-picker'), header, threadURL)

  await open('Mention an agent')
  await dismiss(page.locator('.tt-mentions'), header, threadURL)


  await page.goto(app.url + '/files/vault/Notes')
  const folderURL = page.url(), otherRow = page.locator('.fs-name').getByText('Taken.md', { exact: true })
  await open('Actions for plan.md')
  await dismiss(page.locator('.fs-sheet'), otherRow, folderURL)
  await open('Actions for plan.md')
  await open('Delete')
  await dismiss(page.locator('.fs-delete-confirm'), otherRow, folderURL)
  assert.deepEqual(files.state.calls, [])
  await otherRow.click()
  await button('This file').waitFor()
  const fileURL = page.url()
  await open('This file')
  await dismiss(page.locator('.fs-sheet'), button('This file'), fileURL)

  // An embedded preview has its own document, beyond page event delegation.
  await page.goto(app.url + '/files/vault/Notes/page.html')
  const preview = page.locator('iframe.fs-iframe')
  const previewBody = page.frameLocator('iframe.fs-iframe').locator('body')
  await previewBody.evaluate(body => body.addEventListener('pointerdown', () => { body.dataset.pressed = 'yes' }))
  const htmlURL = page.url()
  await open('This file')
  await dismiss(page.locator('.fs-sheet'), preview, htmlURL)
  assert.equal(await previewBody.getAttribute('data-pressed'), null)

  await page.goto(app.url + '/files')
  const shortcut = button('Shortcut actions for Reading note')
  await shortcut.click()
  await dismiss(page.locator('.fs-sheet'), shortcut, app.url + '/files')

  await page.goto(app.url + '/?modal=activity')
  const activityURL = page.url(), backgroundButton = page.locator(phone ? '.activity-jump' : '.activity-modal .app-modal-head button')
  await open('Cancel Review notes')
  await dismiss(page.locator('dialog.activity-confirm[open]'), backgroundButton, activityURL, phone ? 0 : 1)
  await page.locator('.activity-item').filter({ hasText: 'Finished review' }).click()
  await dismiss(page.locator('dialog.activity-detail[open]'), backgroundButton, activityURL, phone ? 0 : 1)

  await page.goto(app.url + '/?modal=market')
  const marketURL = page.url()
  await page.locator('.market-q').fill('calendar')
  await dismiss(page.locator('.market-suggest'), page.locator('.market-close'), marketURL)

  await page.goto(app.url + '/apps')
  await open('Details for Notes')
  await dismiss(page.locator('.apps-details'), page.locator('.apps-row[title=Calendar]'), app.url + '/apps')
})
