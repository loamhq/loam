import { threadPage } from './thread-page.js'
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { fakeBroker, appAgainst, closing, until } from './harness.js'
import { browser, find, all, text } from './browser.js'
import { writes, clearWrites } from './fakedom.js'
import { filesHost } from './files-host.js'

const { host, routes } = filesHost()
threadPage(routes, { sent: host.sent, started: host.started })
const broker = await fakeBroker(routes)
const app = await appAgainst(broker)

globalThis.location = { pathname: '/files/vault/Notes', search: '', href: 'http://example.com/files/vault/Notes' }
globalThis.history = {
  pushState(data, title, href) {
    globalThis.location.pathname = String(href).split('?')[0]
    globalThis.location.href = `http://example.com${href}`
  },
  replaceState(data, title, href = globalThis.location.pathname + globalThis.location.search) {
    globalThis.location.pathname = String(href).split('?')[0]
    globalThis.location.href = `http://example.com${href}`
  },
}
globalThis.addEventListener = () => {}

const page = browser(app.url, { innerWidth: 1440 })
page.watch()
await import('../public/app.js')
page.unwatch()

test.after(async () => {
  page.stop()
  await closing(broker, app)()
  setImmediate(() => process.exit(process.exitCode ?? 0))
})

const root = page.root
const click = (node, extra = {}) => node.fire('click', { preventDefault() {}, stopPropagation() {}, ...extra })
const entryNamed = (name) => all(root, 'fs-entry').find((e) => text(find(e, 'fs-name')) === name)
const hasClass = (node, cls) => Boolean(node?.getAttribute('class')?.split(/\s+/).includes(cls))

test('the selected mark follows the open file in the folder list', async () => {
  await until(() => entryNamed('plan.md'))
  assert.equal(hasClass(entryNamed('plan.md'), 'on'), false)

  click(find(entryNamed('plan.md'), 'setrow'))
  await until(() => find(root, 'fs-preview') && hasClass(entryNamed('plan.md'), 'on'))
  assert.equal(find(entryNamed('plan.md'), 'setrow').getAttribute('aria-current'), 'page')
  assert.equal(hasClass(entryNamed('page.html'), 'on'), false)

  click(find(entryNamed('page.html'), 'setrow'))
  await until(() => find(root, 'fs-iframe') && hasClass(entryNamed('page.html'), 'on'))
  assert.equal(find(entryNamed('page.html'), 'setrow').getAttribute('aria-current'), 'page')
  assert.equal(hasClass(entryNamed('plan.md'), 'on'), false)
  assert.equal(hasClass(find(entryNamed('plan.md'), 'setrow'), 'on'), false)
  assert.equal(find(entryNamed('plan.md'), 'setrow').getAttribute('aria-current'), null)
})

test('a different file open in the side panel gets a quieter side mark, and promote/swap update both marks', async () => {
  const planEntry = entryNamed('plan.md')
  const sideBtn = find(find(planEntry, 'row-side-action'), 'iconbtn')
  click(sideBtn)

  await until(() => find(root, 'side-pane') && hasClass(entryNamed('plan.md'), 'in-side'))
  assert.equal(hasClass(entryNamed('plan.md'), 'on'), false)
  assert.ok(find(entryNamed('plan.md'), 'row-side-mark'), 'side row carries the side-panel glyph')
  assert.equal(hasClass(entryNamed('page.html'), 'on'), true, 'main row keeps the main selected mark')
  assert.equal(hasClass(entryNamed('page.html'), 'in-side'), false)

  // Swap the two panels: plan.md becomes main, page.html moves to side.
  click(all(find(root, 'side-pane'), 'iconbtn').find((b) => b.getAttribute('aria-label') === 'Swap the two panels'))
  await until(() => hasClass(entryNamed('plan.md'), 'on') && hasClass(entryNamed('page.html'), 'in-side'))
  assert.equal(hasClass(entryNamed('plan.md'), 'in-side'), false)
  assert.equal(find(entryNamed('plan.md'), 'row-side-mark'), null)
  assert.equal(hasClass(entryNamed('page.html'), 'on'), false)
  assert.ok(find(entryNamed('page.html'), 'row-side-mark'))

  // Promote side (page.html) to main: closes side, page.html becomes main.
  click(all(find(root, 'side-pane'), 'iconbtn').find((b) => b.getAttribute('aria-label') === 'Open as the main panel'))
  await until(() => !find(root, 'side-pane') && hasClass(entryNamed('page.html'), 'on'))
  assert.equal(hasClass(entryNamed('page.html'), 'in-side'), false)
  assert.equal(hasClass(entryNamed('plan.md'), 'on'), false)
})

test('navigating back into a folder containing the open file scrolls the marked row into view', async () => {
  click(find(root, 'fs-back'))
  await until(() => entryNamed('Notes') && !entryNamed('page.html'))

  clearWrites()
  click(find(entryNamed('Notes'), 'setrow'))
  await until(() => entryNamed('page.html') && hasClass(entryNamed('page.html'), 'on'))
  const scrolled = writes.some((w) => w.what === 'scroll-to' && w.node === entryNamed('page.html') && w.options?.block === 'nearest')
  assert.equal(scrolled, true, 'navigating into Notes scrolls the open file row into view')
})

test('shortcut rows mark open main and side files the same way', async () => {
  // Add Notes/page.html (main) and Notes/plan.md (side) as shortcuts.
  click(find(entryNamed('page.html'), 'fs-rowmenu'))
  await until(() => find(root, 'fs-shortcuttoggle'))
  click(find(root, 'fs-shortcuttoggle'))
  await until(() => !find(root, 'fs-sheet'))

  click(find(entryNamed('plan.md'), 'fs-rowmenu'))
  await until(() => find(root, 'fs-shortcuttoggle'))
  click(find(root, 'fs-shortcuttoggle'))
  await until(() => !find(root, 'fs-sheet'))

  click(find(find(entryNamed('plan.md'), 'row-side-action'), 'iconbtn'))
  await until(() => find(root, 'side-pane') && hasClass(entryNamed('plan.md'), 'in-side'))

  // Navigate up to Vault, then up to Files home where Shortcuts live.
  click(find(root, 'fs-back'))
  await until(() => text(find(root, 'fs-title')) === 'Vault')
  click(find(root, 'fs-back'))
  await until(() => find(root, 'fs-shortcuts'))

  const shortcuts = find(root, 'fs-shortcuts')
  const pageShortcut = all(shortcuts, 'fs-entry').find((e) => text(find(e, 'fs-name')) === 'page.html')
  const planShortcut = all(shortcuts, 'fs-entry').find((e) => text(find(e, 'fs-name')) === 'plan.md')
  assert.equal(hasClass(pageShortcut, 'on'), true)
  assert.equal(find(pageShortcut, 'setrow').getAttribute('aria-current'), 'page')
  assert.equal(hasClass(planShortcut, 'in-side'), true)
  assert.ok(find(planShortcut, 'row-side-mark'))
})

test('file-row action buttons share a rounded translucent hover style without a square wrapper patch', () => {
  const panelsCss = readFileSync(new URL('../public/panels.css', import.meta.url), 'utf8')
  const tiersCss = readFileSync(new URL('../public/button-tiers.css', import.meta.url), 'utf8')
  assert.doesNotMatch(panelsCss, /^\.row-side-action:hover/m, 'unscoped .row-side-action:hover must not paint a square patch behind .fs-entry iconbtn')
  assert.match(tiersCss, /:is\(\.fs-entry > \.iconbtn,\s*\.fs-entry > \.row-side-action > \.iconbtn\):hover/, 'both file-row action buttons share one hover rule')
})
