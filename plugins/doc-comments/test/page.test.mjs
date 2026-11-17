// The page module through Loam's plugin interface, with a stand-in for the
// page: it registers its one Files viewer only on a page whose Files can let
// an editor hold the open file (`files.own`), draws only Markdown, and takes
// its registration away when disposed -- which is what turning it off does.
//
// Run: node --test plugins/doc-comments/test/page.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import { activate } from '../page/main.js'

function fakeLoam({ own = true } = {}) {
  const regs = []
  const styles = []
  const loam = {
    places: { files: { viewer: { add(r) { regs.push(r); return { remove() { regs.splice(regs.indexOf(r), 1) } } } } } },
    ui: { h: () => null, host: { files: own ? { own() { return { remove() {} } } } : {} }, styles: { add: (f) => styles.push(f) } },
    events: { on: () => ({ remove() {} }) },
    settings: { get: (k, d) => d, set: () => {} },
  }
  return { loam, regs, styles }
}

test('an older page (no files.own): nothing is registered', () => {
  const { loam, regs, styles } = fakeLoam({ own: false })
  const dispose = activate(loam)
  assert.equal(regs.length, 0)
  assert.equal(styles.length, 0)
  assert.equal(dispose, undefined)
})

test('a current page: one viewer, for Markdown only; disposing removes it', () => {
  const { loam, regs, styles } = fakeLoam()
  const dispose = activate(loam)
  assert.equal(regs.length, 1)
  assert.deepEqual(styles, ['assets/doc-comments.css'])
  const r = regs[0]
  const file = (path, view = 'markdown', extra = {}) => ({ file: { root: 'vault', path, view, loading: false, error: '', ...extra } })
  assert.equal(r.when(file('a.md')), true)
  assert.equal(r.when(file('a.markdown')), true)
  assert.equal(r.when(file('a.txt', 'text')), false)
  assert.equal(r.when(file('a.html', 'html')), false)
  assert.equal(r.when(file('a.md', 'markdown', { loading: true })), false)
  assert.equal(r.when(file('a.md', 'markdown', { error: 'gone' })), false)
  dispose()
  assert.equal(regs.length, 0)
})
