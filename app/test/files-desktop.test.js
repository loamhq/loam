import { threadPage } from './thread-page.js'
// Files on the desktop layout (1440 wide): the list column beside the pane,
// a file opened in the pane, the formatting bar, save and conflict, the
// page's own leave dialog over a row click, and the picker as a dialog.
//
// `test/fakedom.js` has no layout; the measurements here are the stylesheet's
// own declarations for the nodes the page drew, resolved by `resolved()` in
// `test/layout.js` at this width — what a browser would be told, not what it
// painted. Where `LOAM_CHROME` names a Chromium, `rendered()` paints it too.

import test from 'node:test'
import assert from 'node:assert/strict'

import { fakeBroker, appAgainst, closing, until } from './harness.js'
import { browser, find, all, text } from './browser.js'
import { resolved, rendered } from './layout.js'
import { filesHost } from './files-host.js'

const { host, routes } = filesHost()
threadPage(routes, { sent: host.sent, started: host.started })
const broker = await fakeBroker(routes)
const app = await appAgainst(broker)

globalThis.location = { pathname: '/files/vault/Notes', search: '' }
globalThis.history = {
  pushState(data, title, href) {
    globalThis.location.pathname = href
  },
  replaceState(data, title, href) {
    globalThis.location.pathname = href
  },
}
globalThis.addEventListener = () => {}
const nativeAsked = []
globalThis.confirm = (q) => {
  nativeAsked.push(q)
  return true
}

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
const click = (node) => node.fire('click', { preventDefault() {}, stopPropagation() {} })
const rowNamed = (name) => all(root, 'setrow').find((r) => text(find(r, 'fs-name')) === name)
const W = { width: 1440 }

test('the folder is the list column and a file opens in the pane beside it', async () => {
  await until(() => rowNamed('plan.md'))
  const col = find(root, 'listcol')
  assert.ok(find(col, 'fs-folder'), 'the folder is drawn in the list column')
  assert.match(find(col, 'fs-folder').getAttribute('class'), /\bwide\b/)
  assert.equal(resolved(find(col, 'fs-title'), W)['font-size'], '28px')
  assert.equal(resolved(rowNamed('plan.md'), W)['border-radius'], '20px')
  click(rowNamed('plan.md'))
  const file = await until(() => find(root, 'fs-file'))
  assert.match(file.getAttribute('class'), /\bwide\b/)
  assert.ok(find(col, 'fs-folder'), 'the list stayed beside the file')
  assert.match(rowNamed('plan.md').getAttribute('class'), /\bon\b/, 'the open file’s row is lit')
  assert.equal(resolved(find(file, 'fs-edtitle'), W)['font-size'], '26px')
})

test('the formatting bar is the rail’s 44px targets, under the title, and edits the selection', async () => {
  click(find(root, 'segmented').childNodes[1])
  const field = await until(() => find(root, 'fs-field'))
  const bold = find(root, 'fs-tool-bold')
  const css = resolved(bold, W)
  assert.equal(css.height, '44px')
  assert.equal(css['min-width'], '44px')
  assert.equal(css['border-radius'], '11px', 'rounded squares, as the rail’s buttons are since 3b1cec9')
  assert.equal(css['font-weight'], '700')
  assert.equal(resolved(find(root, 'fs-tool-italic'), W)['font-style'], 'italic')
  assert.equal(resolved(find(root, 'fs-toolrow'), W)['max-width'], '860px')
  assert.ok(find(root, 'fs-toolrow').parentNode === find(root, 'fs-file'), 'on the desktop the bar is its own row, not under the field')
  const kids = [...find(root, 'fs-file').childNodes]
  assert.ok(kids.indexOf(find(root, 'fs-toolrow')) > kids.indexOf(find(root, 'fs-titles')), 'the bar sits under the file’s title, above the text')
  for (const kind of ['list', 'task']) {
    const glyph = find(root, `fs-tool-${kind}`).childNodes[0]
    assert.equal(String(glyph?.nodeName ?? glyph?.tagName).toLowerCase(), 'svg', `${kind} is a drawn icon, not a dot or a box`)
  }
  field.value = 'a heading'
  field.fire('input', {})
  field.selectionStart = 0
  field.selectionEnd = 0
  click(find(root, 'fs-tool-heading'))
  assert.equal(field.value, '## a heading')
  const out = await rendered(root, { width: 1440 }, () => {
    const b = document.querySelector('.fs-tool-bold').getBoundingClientRect()
    return { h: b.height }
  })
  if (out) assert.equal(out.h, 44)
})

test('a click on another row over an edit that cannot be saved asks in the page’s dialog; Leave goes on', async () => {
  // The way out saves first; refused, it asks.
  host.revision = 'sha256:desk-moved'
  find(root, 'fs-field').value = '## a heading, again'
  find(root, 'fs-field').fire('input', {})
  click(rowNamed('page.html'))
  const dialog = await until(() => find(root, 'fs-dialog'))
  assert.equal(text(find(dialog, 'sheettitle')), 'Leave without saving?')
  assert.equal(nativeAsked.length, 0)
  assert.equal(find(root, 'fs-field').value, '## a heading, again', 'still on the edit while it asks')
  click(find(dialog, 'fs-leave'))
  await until(() => find(root, 'fs-iframe'))
  assert.equal(globalThis.location.pathname, '/files/vault/Notes/page.html')
  assert.equal(find(root, 'fs-dialog'), null)
})

test('save and a conflict on the desktop: Cmd-S, then a refused save keeps the edit until Keep mine', async () => {
  const saveKey = () => find(root, 'fs-field').fire('keydown', { key: 's', metaKey: true, preventDefault() {} })
  click(rowNamed('plan.md'))
  await until(() => find(root, 'fs-preview'))
  click(find(root, 'segmented').childNodes[1])
  const field = await until(() => find(root, 'fs-field'))
  const before = host.puts.length
  field.value = 'desk edit'
  field.fire('input', {})
  saveKey()
  await until(() => host.puts.length === before + 1 && /^Saved/.test(text(find(root, 'fs-saved'))))
  assert.match(find(root, 'fs-field').value, /desk edit$/)
  // The list column's row follows the save: the size the host stored, not
  // the one the folder was listed with.
  const stored = Buffer.byteLength('---\nlast_modified_by: "[[Someone]]"\n---\ndesk edit')
  assert.match(text(find(rowNamed('plan.md'), 'fs-meta')), new RegExp(`^${stored} B · `))
  host.revision = 'sha256:elsewhere'
  find(root, 'fs-field').value = 'desk edit 2'
  find(root, 'fs-field').fire('input', {})
  saveKey()
  await until(() => find(root, 'fs-conflict'))
  // The host's sentence is its own quiet line, not the list's empty state.
  assert.equal(text(find(find(root, 'fs-conflict'), 'fs-hostline')), 'The file changed since that revision.')
  assert.equal(find(find(root, 'fs-conflict'), 'fs-quiet'), null)
  click(find(root, 'fs-keepmine'))
  await until(() => !find(root, 'fs-conflict'))
  // The overwrite named the revision the host's 409 gave, and nothing guessed.
  assert.equal(host.puts.at(-1).base_revision, `sha256:theirs${host.puts.length - 1}`)
  assert.equal(host.puts.at(-1).text, 'desk edit 2')
})

test('Upload on the desktop: the add dialog’s fourth row, and the stored file opens in the pane beside the list', async () => {
  await until(() => !find(root, 'fs-conflict') && find(root, 'fs-add'))
  click(find(root, 'fs-add'))
  const dialog = await until(() => find(root, 'fs-dialog'))
  const up = find(dialog, 'fs-upload')
  assert.ok(up, 'Upload is in the dialog')
  assert.equal(text(find(up, 'fs-meta')), 'Photos, PDFs, anything from your phone')
  click(up)
  const input = all(root, 'picker').find((n) => n.getAttribute('id') === 'pick-upload')
  assert.match(input.accept, /\.pdf/)
  input.files = [new File(['%PDF-1.4\n'], 'report.pdf', { type: 'application/pdf' })]
  input.fire('change')
  // `report.pdf` is taken in Notes, so the host stored `report 2.pdf`.
  await until(() => globalThis.location.pathname === '/files/vault/Notes/report%202.pdf' && find(root, 'fs-file'))
  assert.equal(host.uploads.at(-1).search, '?root=vault&path=Notes')
  assert.ok(find(find(root, 'listcol'), 'fs-folder'), 'the list stayed beside the file')
  // Waited for, not read at once: the address moves the moment the upload
  // settles and the dialog goes on the next draw, and the pane already held a
  // file from the test above — so under load the wait above could be met by the
  // old file before that draw, and a bare assert read the dialog still up.
  await until(() => find(root, 'fs-dialog') === null)
  await until(() => find(root, 'fs-edtitle') && text(find(root, 'fs-edtitle')) === 'report 2.pdf')
})

test('the picker is a dialog over the chat, with a check per file and a count', async () => {
  // Out to the chats, a new chat, and Pick from Files.
  click(find(root, 'railtab'))
  await until(() => all(root, 'persona').length > 0)
  click(all(root, 'persona')[0])
  click(await until(() => all(root, 'pillbtn').find((r) => text(r) === 'New thread')))
  await until(() => find(root, 'plusbtn'))
  click(find(root, 'plusbtn'))
  const menu = await until(() => all(root, 'setrow').find((r) => /Pick from Files/.test(text(r))) ?? all(root, 'menurow').find((r) => /Pick from Files/.test(text(r))))
  click(menu)
  const dialog = await until(() => find(root, 'fs-picker'))
  assert.match(find(root, 'fs-dialog').getAttribute('class'), /\bdialog\b/)
  assert.equal(resolved(dialog, W).width, '520px')
  assert.equal(resolved(dialog, W)['border-radius'], '30px')
  await until(() => all(root, 'fs-pickroot').length === 2)
  click(all(root, 'fs-pickroot')[0])
  await until(() => rowNamed('Notes'))
  click(rowNamed('Notes'))
  await until(() => rowNamed('plan.md'))
  click(rowNamed('plan.md'))
  const check = await until(() => /\bon\b/.test(find(rowNamed('plan.md'), 'fs-check')?.getAttribute('class') ?? '') && find(rowNamed('plan.md'), 'fs-check'))
  assert.equal(resolved(check, W).width, '22px')
  assert.match(text(find(root, 'fs-pickfoot')), /^1 selected/)
  click(find(root, 'fs-attach'))
  await until(() => !find(root, 'fs-picker') && find(root, 'attachstrip'))
})
