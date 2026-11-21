import { threadPage } from './thread-page.js'
// Files, driven on a phone-width page against a host that answers in the
// verified contract's shapes (`test/files-host.js`): an address loaded cold,
// paging, previews (markdown, image, PDF, HTML), the toolbar, save with
// provenance, a conflict and a confirmed overwrite, the page's own leave
// sheet, links found / ambiguous / missing / external, retired System soft
// landings, creation, and files picked for a draft before its first send.
//
// Fixture-only, like every page test here: `test/fakedom.js` has no layout, so
// what is proved is what the page does and asks for, not what it paints.

import test from 'node:test'
import assert from 'node:assert/strict'

import { fakeBroker, appAgainst, closing, until } from './harness.js'
import { browser, find, all, text } from './browser.js'
import { writes, clearWrites } from './fakedom.js'
import { filesHost } from './files-host.js'
import { rendered } from './layout.js'

const { host, routes } = filesHost()
routes['GET /v1/plugins/daily-notes'] = (req, res) => res.end(JSON.stringify({ plugin: { id: 'daily-notes', on: true } }))
routes['GET /v1/plugins/daily-notes/settings'] = (req, res) => res.end(JSON.stringify({ id: 'daily-notes', entries: [], values: { folder: '10_DAILY' } }))
threadPage(routes, { sent: host.sent, started: host.started })
const broker = await fakeBroker(routes)
const app = await appAgainst(broker)

const pushed = [], entries = [{ url: new URL('/files/vault/Notes', app.url), state: null }]
let cursor = 0
const winListeners = new Map()
globalThis.addEventListener = (type, fn) => {
  if (!winListeners.has(type)) winListeners.set(type, [])
  winListeners.get(type).push(fn)
}
function point() { globalThis.location = entries[cursor].url }
function popped() {
  let stopped = false
  const event = { stopImmediatePropagation() { stopped = true } }
  for (const listener of winListeners.get('popstate') ?? []) {
    listener(event)
    if (stopped) break
  }
}
globalThis.history = {
  get state() { return entries[cursor].state },
  pushState(state, title, href) {
    const url = new URL(href, location)
    pushed.push(url.pathname + url.search + url.hash)
    entries.splice(cursor + 1, Infinity, { url, state })
    cursor++
    point()
  },
  replaceState(state, title, href = '') {
    entries[cursor] = { url: new URL(href, location), state }
    point()
  },
  go(delta) {
    if (!delta || !entries[cursor + delta]) return
    cursor += delta
    point()
    popped()
  },
}
point()
const popTo = pathname => {
  const at = entries.findLastIndex((entry, i) => i <= cursor && entry.url.pathname === pathname && entry.state?.workspace && entry.state.panelIndex > 0)
  if (at >= 0 && at !== cursor) history.go(at - cursor)
  else if (at < 0) {
    history.replaceState({ panelIndex: history.state.panelIndex }, '', pathname)
    popped()
  }
}
// The browser's own dialog is never the way an unsaved edit asks.
const nativeAsked = []
globalThis.confirm = (q) => {
  nativeAsked.push(q)
  return true
}
const opened = []
globalThis.open = (...args) => opened.push(args)
const revoked = []
const realRevoke = URL.revokeObjectURL
URL.revokeObjectURL = (u) => {
  revoked.push(u)
  realRevoke(u)
}

const page = browser(app.url, { innerWidth: 430 })
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
const fileField = () => find(root, 'fs-field')
const type = (value) => {
  fileField().value = value
  fileField().fire('input', {})
}

test('an address loaded cold is asked about, listed from rows, and paged by next_cursor', async () => {
  await until(() => rowNamed('plan.md'))
  assert.ok(broker.seen.some((s) => s.path === '/v1/files/stat' && s.search === '?root=vault&path=Notes'))
  assert.equal(text(find(root, 'fs-title')), 'Notes')
  assert.equal(find(root, 'panel-history-back').getAttribute('aria-label'), 'Back')
  assert.deepEqual(all(root, 'fs-name').map(text), ['Old', 'page.html', 'pic.png', 'fake.png', 'plan.md'])
  assert.equal(text(find(rowNamed('Old'), 'fs-meta')).split(' · ')[0], 'Folder', 'a folder has no count')
  assert.match(text(find(rowNamed('plan.md'), 'fs-meta')), /^2 KB/)
  assert.ok(find(root, 'fs-add'), 'the folder row says create')
  const more = find(root, 'fs-more')
  assert.equal(text(more), 'Show more (5 of 8)')
  click(more)
  await until(() => rowNamed('sheet.bin'))
  const lists = broker.seen.filter((s) => s.path === '/v1/files/list' && s.search.includes('path=Notes'))
  assert.equal(lists.at(-1).search, '?root=vault&path=Notes&cursor=5&limit=200')
  assert.equal(find(root, 'fs-more'), null, 'the last page has no next')
})

test('a markdown file previews safely, and a link to a heading lands on it', async () => {
  click(rowNamed('plan.md'))
  await until(() => find(root, 'fs-preview'))
  assert.equal(pushed.at(-1), '/files/vault/Notes/plan.md')
  const preview = find(root, 'fs-preview')
  assert.match(text(preview), /<script>alert\(1\)<\/script>/, 'markup in a note is characters')
  assert.match(text(preview), /\[bad\]\(javascript:alert\(1\)\)/, 'a javascript: link is not a link')
  clearWrites()
  const toHeading = all(preview, 'filelink').find((a) => text(a) === 'plan#Next steps')
  click(toHeading)
  await until(() => writes.some((w) => w.what === 'scroll-to' && w.node.getAttribute('data-anchor') === 'next-steps'))
})

test('a link found elsewhere opens it; several is a choice; nothing says what was looked for', async () => {
  const link = (words) => all(find(root, 'fs-preview'), 'filelink').find((a) => text(a) === words)
  click(link('Twin'))
  const sheet = await until(() => find(root, 'fs-sheet'))
  assert.equal(text(find(sheet, 'sheettitle')), 'Which file?')
  assert.match(text(sheet), /matches 7 files; the 2 nearest the top are shown/)
  assert.deepEqual(all(sheet, 'fs-candidate').map((c) => text(find(c, 'fs-meta'))), ['Vault / a', 'Vault / b / c'])
  click(find(sheet, 'iconbtn'))
  await until(() => !find(root, 'fs-sheet'))
  click(link('Gone'))
  await until(() => /There is no note called “Gone”\./.test(text(root)))
  assert.equal(globalThis.location.pathname, '/files/vault/Notes/plan.md', 'a missing link goes nowhere')
})

test('a web link from the resolver opens only with a safe scheme, in a new tab', async () => {
  const anchors = []
  const walk = (n) => {
    if (n.nodeName === 'A') anchors.push(n)
    for (const c of n.childNodes ?? []) walk(c)
  }
  walk(find(root, 'fs-preview'))
  const external = anchors.find((x) => text(x) === 'a site')
  assert.equal(external.getAttribute('href'), 'https://example.org/x')
  assert.equal(external.getAttribute('rel'), 'noopener noreferrer')
  assert.equal(external.getAttribute('target'), '_blank')
})

const saveKey = () => fileField().fire('keydown', { key: 's', ctrlKey: true, preventDefault() {} })
const saved = () => text(find(root, 'fs-saved'))

test('the toolbar marks the selection; the editor saves itself after a pause, against the revision, and takes the host’s stored text', async () => {
  click(find(root, 'segmented').childNodes[1])
  await until(() => fileField())
  const tools = all(find(root, 'fs-tools'), 'fs-tool').map((n) => n.getAttribute('title'))
  assert.deepEqual(tools, ['Heading', 'Bold', 'Italic', 'List', 'Task', 'Link', 'Link to a note', 'Code', 'Quote'])
  type('one two')
  fileField().selectionStart = 4
  fileField().selectionEnd = 7
  click(find(root, 'fs-tool-bold'))
  assert.equal(fileField().value, 'one **two**')
  fileField().selectionStart = 0
  fileField().selectionEnd = 0
  click(find(root, 'fs-tool-quote'))
  assert.equal(fileField().value, '> one **two**')
  await until(() => saved() === 'Edited')
  assert.equal(host.puts.length, 0, 'nothing is sent while typing')
  // No button: the pause is the save.
  await until(() => host.puts.length === 1 && /^Saved/.test(saved()))
  assert.deepEqual(host.puts[0], { root: 'vault', path: 'Notes/plan.md', text: '> one **two**', base_revision: 'sha256:r1' })
  await until(() => fileField().value.startsWith('---\nlast_modified_by'))
  assert.match(saved(), /the host stamped it/)
  // The next save is made against the revision the save answered, and the
  // keyboard saves at once.
  type(`${fileField().value}\nmore`)
  saveKey()
  await until(() => host.puts.length === 2 && /^Saved/.test(saved()))
  assert.equal(host.puts[1].base_revision, 'sha256:r2')
})

test('a conflict keeps the edit and saves nothing until the person picks; Keep mine sends only the host’s named revision and is refused again if it moved', async () => {
  host.revision = 'sha256:moved'
  type('mine')
  saveKey()
  await until(() => find(root, 'fs-conflict'))
  assert.equal(fileField().value, 'mine', 'the edit is still there')
  assert.match(text(find(root, 'fs-conflict')), /changed on the host since you opened it/)
  assert.match(saved(), /Not saved/)
  const current = host.revision
  const puts = host.puts.length
  type('mine')
  await new Promise((r) => setTimeout(r, 1500))
  assert.equal(host.puts.length, puts, 'no autosave writes over a conflict')
  // It moves again before Keep mine lands: refused, asked again.
  host.conflicts = 1
  click(find(root, 'fs-keepmine'))
  await until(() => host.puts.length === puts + 1 && text(find(root, 'fs-keepmine')) === 'Keep mine')
  assert.equal(host.puts[puts].base_revision, current, 'Keep mine named the revision the host gave')
  assert.equal(fileField().value, 'mine')
  click(find(root, 'fs-keepmine'))
  await until(() => host.puts.length === puts + 2 && !find(root, 'fs-conflict'))
  assert.equal(host.puts[puts + 1].base_revision, `sha256:theirs${puts + 1}`, 'the second Keep mine named the newer revision the second refusal gave')
  assert.match(fileField().value, /mine$/)
})

test('Take theirs drops the edit and reads the file again', async () => {
  host.revision = 'sha256:moved-again'
  type('to be dropped')
  saveKey()
  await until(() => find(root, 'fs-taketheirs'))
  click(find(root, 'fs-taketheirs'))
  await until(() => !find(root, 'fs-conflict') && find(root, 'fs-preview'))
  assert.doesNotMatch(text(find(root, 'fs-preview')), /to be dropped/)
})

test('leaving an edit saves it on the way out; one that cannot be saved asks in the page’s own sheet, never the browser’s', async () => {
  click(find(root, 'segmented').childNodes[1])
  await until(() => fileField())
  const puts = host.puts.length
  type('saved on leaving')
  click(find(root, 'panel-history-back'))
  await until(() => !find(root, 'fs-file') && text(find(root, 'fs-title')) === 'Notes')
  assert.equal(host.puts.length, puts + 1)
  assert.equal(host.puts[puts].text, 'saved on leaving')
  assert.equal(find(root, 'fs-sheet'), null, 'nothing asked')
  click(rowNamed('plan.md'))
  await until(() => find(root, 'segmented'))
  click(find(root, 'segmented').childNodes[1])
  await until(() => fileField())
  host.revision = 'sha256:moved-under'
  type('unsaved words')
  click(find(root, 'panel-history-back'))
  const sheet = await until(() => find(root, 'fs-sheet'))
  assert.equal(text(find(sheet, 'sheettitle')), 'Leave without saving?')
  assert.match(text(sheet), /plan\.md has changes that are not saved/)
  assert.equal(nativeAsked.length, 0)
  click(find(sheet, 'fs-keepediting'))
  await until(() => !find(root, 'fs-sheet'))
  assert.equal(fileField().value, 'unsaved words', 'Keep editing kept it')
  // Back in the browser: the address goes back to the file while the sheet asks.
  popTo('/files/vault')
  await until(() => find(root, 'fs-leave'))
  assert.equal(globalThis.location.pathname, '/files/vault/Notes/plan.md')
  click(find(root, 'fs-leave'))
  await until(() => !find(root, 'fs-file') && text(find(root, 'fs-title')) === 'Vault')
  assert.equal(globalThis.location.pathname, '/files/vault')
  assert.equal(nativeAsked.length, 0)
  // A reload over an unsaved edit is the one place the browser asks.
  assert.equal((winListeners.get('beforeunload') ?? []).length, 1)
})

test('the Files home starts as Chats does, and its shortcuts are theirs: empty, then added from a folder’s menu, then removed', async () => {
  popTo('/files')
  await until(() => all(root, 'fs-rootcard').length === 2)
  // The Chats header, class for class: no back row above it, no line under it.
  const head = find(root, 'fs-homehead')
  assert.match(head.getAttribute('class'), /\bhome-head\b/)
  assert.equal(text(find(head, 'display')), 'Files')
  assert.equal(find(root, 'fs-titles'), null, 'no subtitle under Files')
  // Painted where a Chromium is named: the header is the screen's first thing,
  // as Chats' is, and the title is Chats' 34px at Chats' 22px from the top.
  const painted = await rendered(root, { width: 430 }, () => {
    const screen = document.querySelector('.screen.files').getBoundingClientRect()
    const head = document.querySelector('.fs-homehead').getBoundingClientRect()
    const title = document.querySelector('.fs-homehead .display')
    return { head: head.top - screen.top, pad: getComputedStyle(document.querySelector('.fs-homehead')).paddingTop, size: getComputedStyle(title).fontSize }
  })
  if (painted) assert.deepEqual(painted, { head: 0, pad: '22px', size: '34px' })
  assert.deepEqual(all(root, 'fs-rootname').map(text), ['Vault', 'AI Spaces'])
  assert.match(text(all(root, 'fs-rootdesc')[0]), /inbox, daily notes/, 'the page’s own description, not the host’s')
  assert.equal(find(root, 'fs-rootcount'), null, 'no count is invented')
  // Not the host's shortcuts: theirs, and they have none yet.
  assert.deepEqual(all(find(root, 'fs-shortcuts'), 'fs-name').map(text), ['System notes', 'App'])
  assert.equal(find(root, 'fs-noshort'), null)
  assert.ok(find(root, 'fs-newinbox') && find(root, 'fs-daily'), 'the two actions are at the top of the home')
  // A folder's menu adds it.
  click(all(root, 'fs-rootcard')[0])
  await until(() => rowNamed('Notes'))
  click(rowNamed('Notes'))
  await until(() => text(find(root, 'fs-title')) === 'Notes' && rowNamed('plan.md'))
  click(find(root, 'fs-menu'))
  const toggle = await until(() => find(root, 'fs-shortcuttoggle'))
  assert.match(text(toggle), /^Add to shortcuts/)
  click(toggle)
  popTo('/files')
  await until(() => find(root, 'fs-shortcut'))
  assert.deepEqual(all(find(root, 'fs-shortcuts'), 'fs-name').map(text), ['Notes', 'System notes', 'App'])
  assert.deepEqual(JSON.parse(globalThis.localStorage.getItem('loam-ui.files')).shortcuts, [{ root: 'vault', path: 'Notes' }])
  // And its menu, opened from the shortcut, takes it off again.
  click(find(root, 'fs-shortcut'))
  await until(() => text(find(root, 'fs-title')) === 'Notes' && find(root, 'fs-menu'))
  click(find(root, 'fs-menu'))
  click(await until(() => find(root, 'fs-shortcuttoggle')))
  popTo('/files')
  await until(() => all(root, 'fs-shortcut').length === 0 && all(root, 'fs-host-shortcut').length === 2)
})

test('host-suggested folders use the personal shortcut menu and stay removed on the next home visit', async () => {
  popTo('/files')
  await until(() => all(root, 'fs-host-shortcut').length === 2)
  click(all(root, 'fs-shortcutmenu')[0])
  const toggle = await until(() => find(root, 'fs-shortcuttoggle'))
  assert.match(text(toggle), /^Remove from shortcuts/)
  click(toggle)
  await until(() => all(root, 'fs-host-shortcut').length === 1)
  assert.deepEqual(JSON.parse(localStorage.getItem('loam-ui.files')).hiddenShortcuts, [{ root: 'vault', path: '00_System' }])
  popTo('/files/vault')
  await until(() => rowNamed('Notes'))
  popTo('/files')
  await until(() => all(root, 'fs-host-shortcut').length === 1)
  assert.equal(text(find(all(root, 'fs-host-shortcut')[0], 'fs-name')), 'App')
})

test('Open daily note uses the enabled plugin’s folder and makes today’s YYYY-MM-DD.md and opens it; New inbox note makes one in 00_INBOX', async () => {
  popTo('/files')
  await until(() => find(root, 'fs-daily'))
  const before = host.creates.length
  click(find(root, 'fs-daily'))
  await until(() => host.creates.length > before)
  const d = new Date()
  const today = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  assert.deepEqual(host.creates.at(-1), { root: 'vault', path: `10_DAILY/${today}.md`, text: '' })
  await until(() => globalThis.location.pathname === `/files/vault/10_DAILY/${today}.md`)
  popTo('/files')
  await until(() => find(root, 'fs-newinbox'))
  click(find(root, 'fs-newinbox'))
  await until(() => host.creates.length > before + 1)
  assert.match(host.creates.at(-1).path, /^00_INBOX\/\d{4}-\d{2}-\d{2} \d{4}\.md$/)
  await until(() => globalThis.location.pathname.startsWith('/files/vault/00_INBOX/'))
  popTo('/files')
  await until(() => find(root, 'fs-daily'))
})

test('old System URLs return to Files home with a quiet notice and no system request', async () => {
  const before = broker.seen.length
  for (const path of ['/files/system', '/files/system/Engineer/Role.md']) {
    popTo(path)
    await until(() => all(root, 'fs-rootcard').length === 2 && text(find(root, 'inline')) === 'That location is no longer shown.')
    assert.equal(globalThis.location.pathname, '/files')
    assert.equal(find(root, 'fs-host-records'), null)
    assert.equal(all(root, 'inline').length, 1)
  }
  assert.ok(broker.seen.slice(before).every(call => !call.search.includes('root=system')))
})

test('an image is drawn from checked bytes through an object URL, and given back when the file closes', async () => {
  popTo('/files/vault/Notes')
  await until(() => rowNamed('pic.png'))
  click(rowNamed('pic.png'))
  const img = await until(() => find(root, 'fs-img'))
  const src = img.getAttribute('src')
  assert.match(src, /^blob:/)
  assert.ok(host.downloads.includes('Notes/pic.png'))
  click(find(root, 'panel-history-back'))
  await until(() => !find(root, 'fs-file'))
  assert.ok(revoked.includes(src), 'the object URL was not revoked')
})

test('a file whose bytes are not the picture its name says is not drawn', async () => {
  click(rowNamed('fake.png'))
  const fallback = await until(() => find(root, 'fs-media'))
  assert.equal(find(root, 'fs-img'), null)
  assert.match(text(fallback), /not the picture its name says/)
  assert.ok(find(root, 'fs-dlbtn'))
  click(find(root, 'panel-history-back'))
  await until(() => !find(root, 'fs-file'))
})

test('a PDF is framed from an object URL with no scripts and no same-origin, and a download beside it', async () => {
  click(find(root, 'fs-more'))
  await until(() => rowNamed('report.pdf'))
  click(rowNamed('report.pdf'))
  const frame = await until(() => find(root, 'fs-pdfframe'))
  assert.equal(frame.getAttribute('sandbox'), '')
  assert.match(frame.getAttribute('src'), /^blob:/)
  assert.match(text(find(root, 'fs-pdf')), /will not show PDFs in a sandbox/)
  assert.equal(find(root, 'fs-dlbtn').getAttribute('href'), '/api/files/download?root=vault&path=Notes%2Freport.pdf')
  click(find(root, 'panel-history-back'))
  await until(() => !find(root, 'fs-file'))
})

test('a binary file is a download; a text file the host cannot read as text says so and offers one', async () => {
  if (find(root, 'fs-more')) click(find(root, 'fs-more'))
  await until(() => rowNamed('sheet.bin'))
  click(rowNamed('sheet.bin'))
  const fallback = await until(() => find(root, 'fs-media'))
  assert.ok(find(fallback, 'fs-dlbtn'))
  assert.ok(!broker.seen.some((s) => s.path === '/v1/files/read' && s.search.includes('sheet.bin')), 'a binary is not read as text')
  click(find(root, 'panel-history-back'))
  await until(() => !find(root, 'fs-file'))
  click(rowNamed('odd.txt'))
  await until(() => find(root, 'banner-retry'))
  assert.match(text(find(root, 'banner-retry')), /Text view unavailable/)
  assert.match(text(find(root, 'banner-retry')), /can’t be shown as text/)
  assert.ok(find(find(root, 'banner-retry'), 'fs-dlbtn'))
  click(find(root, 'panel-history-back'))
  await until(() => !find(root, 'fs-file'))
})

test('an HTML file previews from the isolated preview route in a sandboxed frame', async () => {
  click(rowNamed('page.html'))
  const frame = await until(() => find(root, 'fs-iframe'))
  assert.equal(frame.getAttribute('sandbox'), '')
  assert.match(frame.getAttribute('src'), /^\/api\/files\/preview\?root=vault&path=Notes%2Fpage\.html&rev=/)
  assert.equal(frame.getAttribute('srcdoc'), null, 'no srcdoc on this origin')
  click(find(root, 'fs-filemenu'))
  click(await until(() => find(root, 'fs-menu-source')))
  await until(() => fileField())
  assert.equal(text(find(root, 'segmented').childNodes[1]), 'Source')
  assert.match(fileField().value, /<script>/)
  assert.equal(find(root, 'fs-tools'), null, 'the formatting bar is for Markdown')
  click(find(root, 'panel-history-back'))
  await until(() => !find(root, 'fs-file'))
})

test('a new note is created where the folder says create, and opens on what the host stored', async () => {
  click(find(root, 'fs-add'))
  await until(() => find(root, 'fs-sheet'))
  click(all(find(root, 'fs-sheet'), 'sheetrow')[0])
  const field = await until(() => find(root, 'field-input'))
  field.value = 'Idea'
  click(find(find(root, 'fs-sheet'), 'primary'))
  await until(() => host.creates.at(-1)?.path === 'Notes/Idea.md' && globalThis.location.pathname === '/files/vault/Notes/Idea.md')
  assert.deepEqual(host.creates.at(-1), { root: 'vault', path: 'Notes/Idea.md', text: '' })
  await until(() => find(root, 'fs-edtitle') && text(find(root, 'fs-edtitle')) === 'Idea' && !find(root, 'fs-quiet'))
})

const uploadRow = () => (find(root, 'fs-sheet') ? find(find(root, 'fs-sheet'), 'fs-upload') : null)
const listsOfNotes = () => broker.seen.filter((s) => s.path === '/v1/files/list' && s.search === '?root=vault&path=Notes&limit=200').length
const chooser = () => all(root, 'picker').find((n) => n.getAttribute('id') === 'pick-upload')

test('Upload is the add sheet’s fourth row; one file goes up, under the name the host gave it, and opens', async () => {
  popTo('/files/vault/Notes')
  await until(() => !find(root, 'fs-file') && rowNamed('plan.md') && find(root, 'fs-add'))
  click(find(root, 'fs-add'))
  await until(() => uploadRow())
  const rows = all(find(root, 'fs-sheet'), 'sheetrow')
  assert.deepEqual(rows.map((r) => text(find(r, 'fs-name'))), ['New note', 'New text file', 'New folder', 'Upload'])
  assert.equal(text(find(uploadRow(), 'fs-meta')), 'Photos, PDFs, anything from your phone')
  assert.equal(text(find(uploadRow(), 'icon-tile')), '↑')
  // The chooser is in the sheet, never drawn, and offers the host's types.
  clearWrites()
  click(uploadRow())
  const input = chooser()
  assert.ok(input, 'the chooser is on the page')
  assert.ok(writes.some((w) => w.what === 'click' && w.node === input), 'Upload opened the chooser')
  assert.match(input.accept, /^\.png,\.jpg,.*\.heic,.*\.pdf,.*\.md/)
  // A name that is taken: the host stores `plan 2.md` and nothing is overwritten.
  const lists = listsOfNotes()
  input.files = [new File(['# mine\n'], 'plan.md', { type: 'text/markdown' })]
  input.fire('change')
  await until(() => globalThis.location.pathname === '/files/vault/Notes/plan%202.md')
  assert.deepEqual(host.uploads.at(-1), { search: '?root=vault&path=Notes', name: 'plan.md', text: '# mine\n' })
  await until(() => find(root, 'fs-edtitle') && text(find(root, 'fs-edtitle')) === 'plan 2')
  assert.equal(find(root, 'fs-sheet'), null, 'one file that went up alone closes the sheet')
  // The folder is read again for the new row. That read goes through the relay,
  // and on a loaded box it can reach the host after the file has opened — so it
  // is waited for, not asserted on the spot (failed a landing, 2026-10-06).
  await until(() => listsOfNotes() > lists).catch(() => assert.fail('the folder was not read again for the new row'))
  popTo('/files/vault/Notes')
  await until(() => !find(root, 'fs-file') && rowNamed('plan.md') && find(root, 'fs-add'))
})

test('several files go one by one; each failure is drawn as its own, a retry sends only it, and the folder stays', async () => {
  host.uploadFailures['flaky.pdf'] = { times: 1, status: 502, error: 'The host could not be reached.' }
  host.uploadFailures['secret.txt'] = { times: 9, status: 400, error: 'That name is not one Files stores.' }
  const before = host.uploads.length
  const lists = listsOfNotes()
  click(find(root, 'fs-add'))
  await until(() => uploadRow())
  click(uploadRow())
  chooser().files = [
    new File(['%PDF-1.4'], 'flaky.pdf', { type: 'application/pdf' }),
    new File(['hi'], 'hello.txt', { type: 'text/plain' }),
    new File(['MZ'], 'tool.exe', { type: 'application/octet-stream' }),
    new File(['x'], 'secret.txt', { type: 'text/plain' }),
  ]
  chooser().fire('change')
  await until(() => find(root, 'fs-uploaddone'))
  const sheet = find(root, 'fs-sheet')
  assert.equal(text(find(sheet, 'sheettitle')), '3 of 4 not uploaded')
  assert.equal(text(find(sheet, 'fs-sub')), 'Into Vault / Notes')
  const line = (name) => all(sheet, 'sheetrow').find((r) => text(find(r, 'fs-name')) === name)
  assert.match(text(find(line('flaky.pdf'), 'fs-meta')), /could not be reached/)
  assert.match(text(find(line('tool.exe'), 'fs-meta')), /not a type Files takes/)
  assert.match(text(find(line('secret.txt'), 'fs-meta')), /not one Files stores/)
  assert.match(text(find(line('hello.txt'), 'fs-meta')), /Uploaded$/)
  assert.ok(find(line('flaky.pdf'), 'fs-retry'), 'a host that failed can be tried again')
  assert.equal(find(line('secret.txt'), 'fs-retry'), null, 'a refusal of these bytes is not retried')
  assert.equal(find(line('tool.exe'), 'fs-retry'), null)
  assert.deepEqual(host.uploads.slice(before).map((u) => u.name).sort(), ['flaky.pdf', 'hello.txt', 'secret.txt'], 'one request per file, and the refused type never left')
  // Retry: only that file, and the sheet stays where the person is.
  click(find(line('flaky.pdf'), 'fs-retry'))
  await until(() => text(find(find(root, 'fs-sheet'), 'sheettitle')) === '2 of 4 not uploaded')
  assert.deepEqual(host.uploads.slice(before + 3).map((u) => u.name), ['flaky.pdf'])
  assert.equal(globalThis.location.pathname, '/files/vault/Notes', 'nothing opened over the folder')
  // Read again once per settled batch: the first answer, and the retry.
  await until(() => listsOfNotes() >= lists + 2)
  // A stored row on the sheet opens the file the host stored.
  click(all(find(root, 'fs-sheet'), 'fs-uploaded').find((r) => text(find(r, 'fs-name')) === 'hello.txt'))
  await until(() => globalThis.location.pathname === '/files/vault/Notes/hello.txt' && !find(root, 'fs-sheet'))
  popTo('/files/vault/Notes')
  await until(() => !find(root, 'fs-file') && find(root, 'fs-add'))
})

test('a folder the host does not let you add to has no Upload anywhere', async () => {
  popTo('/files/spaces')
  await until(() => text(find(root, 'fs-title')) === 'AI Spaces' && text(root).includes('Nothing here yet'))
  assert.equal(find(root, 'fs-add'), null)
  assert.equal(find(root, 'fs-upload'), null)
  popTo('/files/vault/Notes')
  await until(() => rowNamed('plan.md') && find(root, 'fs-add'))
})

test('a folder the host cannot list says so in its own words, with a way to try again', async () => {
  popTo('/files/vault/Broken')
  await until(() => find(root, 'banner-retry'))
  assert.match(text(find(root, 'banner-retry')), /nothing at vault\/Broken/)
  assert.ok(all(find(root, 'banner-retry'), 'pillbtn').some((b) => text(b) === 'Try again'))
})

const composer = () => find(find(root, 'composer-box'), 'draft-row').childNodes.find((n) => n.className === 'draft')

test('files picked for a thread upload and go with its next message', async () => {
  click(all(root, 'navbtn')[0])
  await until(() => !find(root, 'files') && all(root, 'persona').length > 0)
  click(all(root, 'persona')[0])
  click(await until(() => all(root, 'pillbtn').find((r) => text(r) === 'New thread')))
  await until(() => text(find(root, 'headtitle-name')) === 'New thread')
  composer().value = 'read these'
  composer().fire('input')
  click(find(root, 'plusbtn'))
  await until(() => find(root, 'sheet'))
  const pick = all(find(root, 'sheet'), 'setrow').find((r) => /Pick from Files/.test(text(r)))
  assert.equal(pick.hasAttribute('disabled'), false, 'a draft can pick')
  click(pick)
  const picker = await until(() => find(root, 'fs-picker'))
  assert.equal(text(find(picker, 'sheettitle')), 'Attach from Files')
  assert.match(text(picker), /Scout gets read access to what you pick/)
  await until(() => all(root, 'fs-pickroot').length === 2)
  click(all(root, 'fs-pickroot')[0])
  await until(() => rowNamed('Notes'))
  click(rowNamed('Notes'))
  await until(() => rowNamed('plan.md'))
  click(rowNamed('plan.md'))
  click(rowNamed('page.html'))
  await until(() => text(find(root, 'fs-pickfoot')).startsWith('2 selected'))
  click(rowNamed('page.html'))
  await until(() => text(find(root, 'fs-pickfoot')).startsWith('1 selected'))
  click(find(root, 'fs-attach'))
  await until(() => !find(root, 'fs-picker') && find(root, 'attachstrip'))
  assert.equal(text(find(root, 'headtitle-name')), 'New thread', 'the draft was not left')
  assert.equal(composer().value, 'read these', 'the draft kept its words')
  await until(() => all(root, 'attachment-chip').every((n) => !/\bgoing\b/.test(n.getAttribute('class'))))
  assert.equal(host.started.length, 1, 'picking did not create another thread')
  find(root, 'composer').fire('submit', { preventDefault: () => {} })
  await until(() => host.sent.length === 1)
  assert.equal(host.started.length, 1)
  assert.equal(host.sent[0].text, 'read these')
  assert.deepEqual(host.sent[0].attachments, ['attachments/plan.md'])
  await until(() => !find(root, 'attachstrip'))
})

test('files picked for a draft that is abandoned go with it', async () => {
  click(find(root, 'backbtn') ?? find(root, 'iconbtn'))
  await until(() => all(root, 'persona').length > 0)
  click(all(root, 'persona')[0])
  click(await until(() => all(root, 'pillbtn').find((r) => text(r) === 'New thread')))
  await until(() => text(find(root, 'headtitle-name')) === 'New thread')
  click(find(root, 'plusbtn'))
  await until(() => find(root, 'sheet'))
  click(all(find(root, 'sheet'), 'setrow').find((r) => /Pick from Files/.test(text(r))))
  await until(() => all(root, 'fs-pickroot').length === 2)
  click(all(root, 'fs-pickroot')[0])
  await until(() => rowNamed('Notes'))
  click(rowNamed('Notes'))
  await until(() => rowNamed('plan.md'))
  click(rowNamed('plan.md'))
  click(find(root, 'fs-attach'))
  await until(() => find(root, 'attachstrip'))
  click(find(root, 'backbtn') ?? find(root, 'iconbtn'))
  await until(() => all(root, 'persona').length > 0)
  click(all(root, 'persona')[0])
  click(await until(() => all(root, 'pillbtn').find((r) => text(r) === 'New thread')))
  await until(() => text(find(root, 'headtitle-name')) === 'New thread')
  assert.equal(find(root, 'attachstrip'), null, 'a new draft inherited an abandoned one’s files')
})
