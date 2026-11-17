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

function fakeLoam({ own = true } = {}) {
  const regs = []
  const roots = []
  const styles = []
  const saved = {}
  const place = (list) => ({ add(r) { list.push(r); return { remove() { list.splice(list.indexOf(r), 1) } } } })
  const loam = {
    places: { files: { viewer: place(regs), root: place(roots) } },
    ui: { h: () => null, host: { files: own ? { own() { return { remove() {} } } } : {}, schedule() {} }, styles: { add: (f) => styles.push(f) } },
    events: { on: () => ({ remove() {} }) },
    settings: { get: (k, d) => (k in saved ? saved[k] : d), set: (k, v) => { saved[k] = v } },
  }
  return { loam, regs, roots, styles }
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
