// The page module through Loam's plugin interface, with a stand-in for the
// page: it registers its Files viewer and its Shared Docs root only on a page
// whose Files can let an editor hold the open file (`files.own`); it draws
// only Markdown, and only where the `reach` setting allows (staging, until it
// is set to everywhere); disposing takes its registrations away, which is
// what turning it off does.
//
// Run: node --test plugins/doc-comments/test/page.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import { activate } from '../page/main.js'

let reachAnswer = 'staging'
globalThis.fetch = async () => ({ ok: true, json: async () => ({ reach: reachAnswer }) })
const tick = () => new Promise(r => setTimeout(r, 5))

// A node as the fake `h` builds it: enough to read what was drawn.
const el = (tag, props, ...children) => ({ tag, props: props || {}, children: children.flat(Infinity).filter(c => c !== null && c !== undefined && c !== false) })
const textOf = (n) => (n === null || n === undefined ? '' : typeof n === 'string' ? n : `${n.props?.text ?? ''}${(n.children || []).map(textOf).join('')}`)
const byClass = (n, cls, out = []) => {
  if (!n || typeof n !== 'object') return out
  if (String(n.props?.class || '').split(' ').includes(cls)) out.push(n)
  for (const c of n.children || []) byClass(c, cls, out)
  return out
}

function fakeLoam({ own = true, files = null } = {}) {
  const regs = []
  const roots = []
  const actions = []
  const styles = []
  const saved = {}
  const place = (list) => ({ add(r) { list.push(r); return { remove() { list.splice(list.indexOf(r), 1) } } } })
  const loam = {
    places: { files: { viewer: place(regs), root: place(roots), action: place(actions) } },
    ui: { h: el, host: { files: own ? { own() { return { remove() {} } }, ...(files || {}) } : {}, schedule() {} }, styles: { add: (f) => styles.push(f) } },
    events: { on: () => ({ remove() {} }) },
    settings: { get: (k, d) => (k in saved ? saved[k] : d), set: (k, v) => { saved[k] = v } },
  }
  return { loam, regs, roots, actions, styles }
}

const md = (path = 'a.md', view = 'markdown', extra = {}) => ({ file: { root: 'vault', path, view, loading: false, error: '', ...extra } })

test('an older page (no files.own): nothing is registered', () => {
  globalThis.location = { hostname: 'next.example.test' }
  const { loam, regs, roots, styles } = fakeLoam({ own: false })
  assert.equal(activate(loam), undefined)
  assert.equal(regs.length + roots.length + styles.length, 0)
})

test('on staging: one viewer for Markdown only, and Shared Docs; disposing removes both', async () => {
  globalThis.location = { hostname: 'next.example.test' }
  const { loam, regs, roots, styles } = fakeLoam()
  const dispose = activate(loam)
  await tick()
  assert.deepEqual([regs.length, roots.length], [1, 1])
  assert.deepEqual(styles, ['assets/doc-comments.css'])
  const r = regs[0]
  assert.equal(r.when(md('a.md')), true)
  assert.equal(r.when(md('a.markdown')), true)
  assert.equal(r.when(md('a.txt', 'text')), false)
  assert.equal(r.when(md('a.md', 'markdown', { loading: true })), false)
  assert.equal(roots[0].name, 'Shared Docs')
  dispose()
  assert.deepEqual([regs.length, roots.length], [0, 0])
})

test('elsewhere: off while reach is staging, on once the setting says everywhere', async () => {
  globalThis.location = { hostname: 'loam.example.test' }
  reachAnswer = 'staging'
  let w = fakeLoam()
  activate(w.loam)
  await tick()
  assert.equal(w.regs[0].when(md()), false)
  assert.equal(w.roots[0].when(), false)
  reachAnswer = 'everywhere'
  w = fakeLoam()
  activate(w.loam)
  await tick()
  assert.equal(w.regs[0].when(md()), true)
  reachAnswer = 'staging'
})

// -- Shared Docs and the Share item (2026-11-17) --------------------------------

const { installDom } = await import('../../../app/test/fakedom.js')
installDom()
globalThis.document.body = globalThis.document.createElement('body')
globalThis.document.addEventListener = () => {}
globalThis.document.removeEventListener = () => {}
globalThis.requestAnimationFrame = (f) => setTimeout(f, 0)

const SHARED = {
  docs: [
    { id: 'd-1', root: 'vault', path: 'Notes/Plan.md', new: 2, last: '2026-11-17T09:00:00Z', people: [],
      links: [{ id: 'a', on: true, mode: 'edit', name: 'Ada', people: [] }, { id: 'b', on: true, mode: 'comment', name: 'Sam', people: [] },
        { id: 'c', on: true, mode: 'view', name: '', people: ['Iris'] }, { id: 'd', on: true, mode: 'view', name: 'Lee', people: [] }, { id: 'e', on: false, mode: 'edit', name: 'Gone', people: [] }] },
    { id: 'd-2', root: 'vault', path: 'Notes/Quiet.md', new: 0, last: '2026-11-16T09:00:00Z', people: [], links: [{ id: 'f', on: true, mode: 'view', name: '', people: [] }] },
  ],
}

function sharedFetch(calls = []) {
  return async (url, opts = {}) => {
    calls.push(`${opts.method || 'GET'} ${url}`)
    if (String(url).endsWith('/shared')) return { ok: true, json: async () => structuredClone(SHARED) }
    if (String(url).includes('doc/links/password')) return { ok: true, json: async () => ({ password: 'pw-pw-pw' }) }
    if (String(url).includes('doc/links')) return { ok: true, json: async () => ({ links: [], owner: 'You' }) }
    if (String(url).includes('doc?')) return { ok: false, status: 503, json: async () => ({ error: 'down' }) }
    return { ok: true, json: async () => ({ reach: 'everywhere' }) }
  }
}

const statFiles = (stats = []) => ({
  stat: async (root, path) => { stats.push(path); return { root, path, kind: 'file', name: path.split('/').pop(), size: 2048, modified: '2026-11-10T00:00:00Z', capabilities: { read: true, write: true, rename: true, download: true } } },
  ago: (iso) => `ago(${iso.slice(5, 10)})`,
  readText: async () => '# Plan for spring\n\nText.',
})

test("Shared Docs draws each doc with core's own row: the file's row, its people, its modes, a dot only where something is new", async () => {
  globalThis.location = { hostname: 'next.example.test' }
  const stats = []
  globalThis.fetch = sharedFetch()
  const files = statFiles(stats)
  const { loam, roots } = fakeLoam({ files })
  activate(loam)
  await tick()
  await roots[0].load()
  assert.deepEqual(stats.sort(), ['Notes/Plan.md', 'Notes/Quiet.md'])
  const drawn = []
  const entry = (row, extras) => { drawn.push({ row, extras }); return el('div', { class: 'fs-entry' }) }
  roots[0].render({ go() {}, files, entry, wide: true })
  assert.equal(drawn.length, 2)
  const [plan, quiet] = drawn
  // The row is the file's own (as stat answered it), so core draws its tile,
  // size and capabilities, its open-beside and its menu.
  assert.equal(plan.row.name, 'Plan.md')
  assert.equal(plan.row.capabilities.write, true)
  assert.equal(plan.row.kind, 'file')
  // The second line: what is new, the modes open, the last activity.
  assert.equal(plan.extras.meta.map(textOf).join(''), '2 new · ago(11-17)')
  assert.equal(quiet.extras.meta.map(textOf).join(''), 'View · ago(11-16)')
  assert.equal(/Nothing new/.test(quiet.extras.meta.map(textOf).join('')), false, 'a quiet row says nothing about news')
  // The dot sits on the tile for the doc with news, and nowhere else.
  assert.match(plan.extras.badge.props.class, /dc-sd-dot/)
  assert.equal(quiet.extras.badge, null)
  // People: three faces, then +N; a group link is the people glyph; a link
  // that is off is nobody. Each says who and what on hover or a long press.
  const faces = byClass(plan.extras.trail, 'dc-sd-face')
  assert.deepEqual(faces.map(f => textOf(f)), ['A', 'S', '', '+1'])
  assert.equal(byClass(plan.extras.trail, 'grp').length, 1)
  assert.deepEqual(faces.map(f => f.props['data-tip']), ['Ada · can edit', 'Sam · can comment', 'Group link · can view · Iris', 'Lee · can view'])
  assert.equal(typeof faces[0].props.onpointerenter, 'function')
  assert.equal(typeof faces[0].props.onpointerdown, 'function')
  assert.match(plan.extras.trail.props['aria-label'], /Ada \(can edit\)/)
  // Opening marks it seen at once: the dot goes, the words "2 new" go.
  plan.extras.onopen()
  drawn.length = 0
  roots[0].render({ go() {}, files, entry, wide: true })
  assert.match(drawn[0].extras.badge.props.class, /\bseen\b/)
  assert.equal(drawn[0].extras.meta.map(textOf).join(''), 'ago(11-17)')
})

test('a doc whose last link goes off leaves the list gently, then is gone', async () => {
  globalThis.location = { hostname: 'next.example.test' }
  globalThis.fetch = sharedFetch()
  const files = statFiles()
  const { loam, roots } = fakeLoam({ files })
  activate(loam)
  await tick()
  await roots[0].load()
  const quietOnly = SHARED.docs.splice(1, 1)
  try {
    await roots[0].load()
  } finally { SHARED.docs.push(...quietOnly) }
  const drawn = []
  const entry = (row, extras) => { drawn.push({ row, extras }); return el('div') }
  roots[0].render({ go() {}, files, entry, wide: true })
  assert.deepEqual(drawn.map(d => [d.row.path, /dc-sd-leaving/.test(d.extras.className)]), [['Notes/Plan.md', false], ['Notes/Quiet.md', true]])
  await new Promise(r => setTimeout(r, 400))
  drawn.length = 0
  roots[0].render({ go() {}, files, entry, wide: true })
  assert.deepEqual(drawn.map(d => d.row.path), ['Notes/Plan.md'])
})

test("the Share item: first in a Markdown file's menu anywhere, named by whether the doc is shared, and it opens the share sheet without opening the doc", async () => {
  globalThis.location = { hostname: 'next.example.test' }
  const calls = []
  globalThis.fetch = sharedFetch(calls)
  const files = statFiles()
  const { loam, roots, actions } = fakeLoam({ files })
  activate(loam)
  await tick()
  await roots[0].load()
  assert.equal(actions.length, 1)
  const item = actions[0]
  assert.equal(item.slot, 'menu')
  const res = (path, extra = {}) => ({ root: 'vault', path, kind: 'file', name: path.split('/').pop(), capabilities: { write: true }, ...extra })
  assert.equal(item.when({ resource: res('Notes/Plan.md') }), true)
  assert.equal(item.when({ resource: res('Notes/Other.markdown') }), true)
  assert.equal(item.when({ resource: res('Notes/a.txt') }), false)
  assert.equal(item.when({ resource: res('Notes', { kind: 'folder' }) }), false)
  assert.equal(item.when({ resource: res('Notes/Plan.md', { capabilities: { write: false } }) }), false, 'a doc you cannot write is not yours to share')
  assert.equal(item.when({ resource: res('x.md', { root: 'system' }) }), false)
  assert.equal(item.render({ resource: res('Notes/Plan.md') }).label, 'Share settings')
  assert.equal(item.render({ resource: res('Notes/Other.md') }).label, 'Share…')
  // Running it asks for the doc's links (the share sheet's first read) and
  // never reads the doc itself, which would mark it seen.
  // The sheet draws into a DOM of its own here: a permissive stand-in that
  // records what is appended to the page.
  const realDoc = globalThis.document
  const realNode = globalThis.Node
  const el2 = (tag) => ({ tag, nodeType: 1, className: '', textContent: '', kids: [], style: {}, dataset: {}, attrs: {},
    setAttribute(k, v) { this.attrs[k] = v }, getAttribute(k) { return this.attrs[k] }, removeAttribute() {},
    append(...c) { this.kids.push(...c) }, replaceChildren(...c) { this.kids = [...c] }, addEventListener() {}, removeEventListener() {}, remove() {},
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false }, querySelector: () => null, focus() {}, select() {} })
  const body = el2('body')
  globalThis.document = { body, createElement: el2, createElementNS: (_, t) => el2(t), createTextNode: (t) => ({ nodeType: 3, textContent: t }), addEventListener() {}, removeEventListener() {} }
  globalThis.Node = { [Symbol.hasInstance]: (x) => Boolean(x) && typeof x === 'object' && 'nodeType' in x }
  calls.length = 0
  try {
    item.render({ resource: res('Notes/Plan.md') }).run(res('Notes/Plan.md'))
    await tick()
    const scrim = body.kids.find(k => /dc-share-scrim/.test(k.className))
    assert.ok(scrim, 'the share sheet is open over the list')
    assert.match(scrim.kids[0]?.className || '', /dc-dlg/)
  } finally {
    globalThis.document = realDoc
    globalThis.Node = realNode
  }
  assert.ok(calls.some(c => /\/x\/doc-comments\/doc\/links\?root=vault&path=Notes%2FPlan\.md/.test(c)), calls.join('\n'))
  assert.equal(calls.some(c => /\/doc\?/.test(c)), false)
})

test('a doc open beside another keeps its own editor: one per Files', async () => {
  globalThis.location = { hostname: 'next.example.test' }
  const calls = []
  globalThis.fetch = sharedFetch(calls)
  const { loam, regs } = fakeLoam({ files: statFiles() })
  activate(loam)
  await tick()
  const main = { name: 'main' }
  const side = { name: 'side' }
  const ctx = (files, path) => ({ files, file: { root: 'vault', path, view: 'markdown', loading: false, error: '' }, options: { wide: true }, defaultView: () => 'files-own' })
  const opens = () => calls.filter(c => /\/x\/doc-comments\/doc\?/.test(c)).length
  regs[0].render(ctx(main, 'A.md'))
  regs[0].render(ctx(side, 'B.md'))
  regs[0].render(ctx(main, 'A.md'))
  regs[0].render(ctx(side, 'B.md'))
  await tick()
  assert.equal(opens(), 2, 'each panel opened its doc once; neither closed the other')
})

// -- Shared with me (2026-11-17) ------------------------------------------------------

const WITHME = { docs: [
  { id: 'w-aaaaaaaaaa', address: 'shared:w-aaaaaaaaaa', url: 'https://ada.example/s/x', host: 'ada.example', title: 'Garden plan', owner: 'Ada', mode: 'edit', state: 'ok', new: true },
  { id: 'w-bbbbbbbbbb', address: 'shared:w-bbbbbbbbbb', url: 'https://lee.example/s/y', host: 'lee.example', title: 'Old notes', owner: 'Lee', mode: 'view', state: 'gone', new: false },
] }

function withmeFetch(calls, answer = WITHME) {
  const shared = sharedFetch(calls)
  return async (url, opts = {}) => {
    if (String(url).includes('/withme')) {
      calls.push(`${opts.method || 'GET'} ${url} ${opts.body || ''}`)
      if ((opts.method || 'GET') === 'GET') return { ok: true, json: async () => structuredClone(answer) }
      if (opts.method === 'POST') {
        const body = JSON.parse(opts.body)
        if (body.password === 'wrong') return { ok: false, status: 403, json: async () => ({ error: 'That password doesn’t open this link.', code: 'password' }) }
        if (!body.name && body.link.includes('group')) return { ok: false, status: 409, json: async () => ({ error: 'name?', code: 'need-name', suggest: 'Sam' }) }
        return { ok: true, status: 201, json: async () => ({ doc: { ...answer.docs[0], id: 'w-cccccccccc', title: 'New one', name: body.name || 'Sam' } }) }
      }
    }
    return shared(url, opts)
  }
}

test('Shared Docs has two sections once core can open a plugin root\'s files: Shared by me, then Shared with me with its rows and the Add row', async () => {
  globalThis.location = { hostname: 'next.example.test' }
  const calls = []
  globalThis.fetch = withmeFetch(calls)
  const files = { ...statFiles(), itemsMenu() {}, side: () => ({ available: () => true }) }
  const { loam, roots, styles } = fakeLoam({ files })
  activate(loam)
  await tick()
  assert.ok(styles.includes('assets/withme.css'))
  await roots[0].load()
  const drawn = []
  const entry = (row, extras) => { drawn.push({ row, extras }); return el('div', { class: 'fs-entry' }) }
  const out = roots[0].render({ go() {}, files, entry, wide: true })
  assert.deepEqual(byClass(out, 'dc-sd-kicker').map(textOf), ['Shared by me', 'Shared with me'])
  const mine = drawn.filter(d => d.row.root === '@doc-comments')
  assert.deepEqual(mine.map(d => [d.row.name, d.row.path]), [['Garden plan', 'with-me/w-aaaaaaaaaa.md'], ['Old notes', 'with-me/w-bbbbbbbbbb.md']])
  assert.equal(mine[0].extras.meta, 'Ada’s Loam · Edit')
  assert.match(mine[0].extras.badge.props.class, /dc-sd-dot/)
  assert.equal(mine[1].extras.meta, 'No longer shared')
  assert.match(mine[1].extras.className, /dc-wm-dead/)
  assert.equal(mine[1].extras.badge, null, 'no dot on a row that is no longer shared')
  assert.deepEqual(mine[0].extras.menu().map(i => i.label), ['Open', 'Open to the side', 'Copy link', 'Remove'])
  assert.equal(byClass(out, 'dc-wm-add').length, 1)
  // a cold address: the root's row names the doc by its title
  assert.equal(roots[0].row('with-me/w-aaaaaaaaaa.md').name, 'Garden plan')
  // the password is never in what the page was given
  assert.equal(JSON.stringify(WITHME).includes('password'), false)
})

test('Shared with me, empty: the Add row says what it is for', async () => {
  globalThis.location = { hostname: 'next.example.test' }
  globalThis.fetch = withmeFetch([], { docs: [] })
  const files = { ...statFiles(), itemsMenu() {}, side: () => null }
  const { loam, roots } = fakeLoam({ files })
  activate(loam)
  await tick()
  await roots[0].load()
  const out = roots[0].render({ go() {}, files, entry: () => el('div'), wide: true })
  const add = byClass(out, 'dc-wm-add')[0]
  assert.match(textOf(add), /^Add a shared doc.*Paste its link and password/)
})

test('an older core (no itemsMenu): Shared Docs is Shared by me alone', async () => {
  globalThis.location = { hostname: 'next.example.test' }
  globalThis.fetch = withmeFetch([])
  const files = statFiles()
  const { loam, roots, styles } = fakeLoam({ files })
  activate(loam)
  await tick()
  await roots[0].load()
  const out = roots[0].render({ go() {}, files, entry: () => el('div'), wide: true })
  assert.equal(byClass(out, 'dc-sd-kicker').length, 0)
  assert.equal(styles.includes('assets/withme.css'), false)
})
