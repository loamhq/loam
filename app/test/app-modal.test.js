import test from 'node:test'
import assert from 'node:assert/strict'
import { modalFromURL, modalURL, createAppModalHistory } from '../public/app-modal-history.js'
import { mount as mountSettings } from '../public/page/settings.js'
import { installDom } from './fakedom.js'

test('modal addresses retain thread/file path, other query keys and fragment; detail round trips', () => {
  const original = 'https://app.example/files/vault/Notes/plan.md?keep=yes#next'
  const sheet = { kind: 'settings', page: 'agents', provider: null, account: null, detail: { kind: 'engineer', id: 'A project & notes' } }
  const url = modalURL(original, sheet)
  assert.equal(url.pathname, '/files/vault/Notes/plan.md')
  assert.equal(url.searchParams.get('keep'), 'yes'); assert.equal(url.hash, '#next')
  assert.deepEqual(modalFromURL(url), sheet)
  assert.equal(modalURL(url, null).href, original)
  assert.deepEqual(modalFromURL(modalURL(url, { kind: 'activity' })), { kind: 'activity' })
  assert.equal(modalFromURL(original + '&modal=unknown'), null)
})

function fixture() {
  const entries = [{ url: 'https://app.example/threads/notes', state: null }], listeners = []
  let at = 0, current = null, restored = 0, closed = 0, width = 1440
  const win = { location: { href: entries[0].url }, addEventListener: (type, listener) => listeners.push(listener) }
  function move(index) { at = index; win.location.href = entries[at].url; return { stopped: false, stopImmediatePropagation() { this.stopped = true } } }
  win.history = {
    get state() { return entries[at].state },
    pushState(state, _, url) { entries.splice(at + 1); entries.push({ url: String(url), state }); move(at + 1) },
    replaceState(state, _, url) { entries[at] = { url: String(url), state }; move(at) },
    back() { const event = move(at - 1); listeners.forEach(l => l(event)) },
  }
  const history = createAppModalHistory({ window: win, wide: () => width >= 1100, sheet: () => current,
    restore: value => { restored++; current = value }, close: () => { closed++; current = null }, changed() {} })
  history.restore()
  return { history, win, entries, get current() { return current }, set current(v) { current = v },
    get closed() { return closed }, get restored() { return restored }, narrow() { width = 1099 },
    pop(index) { const event = move(index); listeners.forEach(l => l(event)); return event } }
}
test('opening adds one history entry, section changes and modal replacement reuse it, Back closes without page navigation', () => {
  const f = fixture()
  f.current = { kind: 'settings', page: 'root' }; f.history.sync()
  assert.equal(f.entries.length, 2)
  f.current = { kind: 'settings', page: 'plugins' }; f.history.sync()
  f.current = { kind: 'activity' }; f.history.sync()
  assert.equal(f.entries.length, 2)
  assert.equal(f.pop(0).stopped, true); assert.equal(f.current, null)
  assert.equal(f.pop(1).stopped, true); assert.deepEqual(f.current, { kind: 'activity' })
  f.history.dismiss(); assert.equal(f.current, null)
})
test('a link out removes the modal query, and Back to a different document reaches the normal router', () => {
  const f = fixture()
  f.current = { kind: 'activity' }; f.history.sync()
  f.current = null
  f.win.history.pushState(null, '', 'https://app.example/threads/another')
  f.history.sync()
  assert.equal(f.win.location.href, 'https://app.example/threads/another')
  assert.equal(f.pop(0).stopped, false)
})
test('document-only Back before the next redraw still reaches the document router', () => {
  const f = fixture()
  f.win.history.pushState(null, '', 'https://app.example/threads/another')
  assert.equal(f.pop(0).stopped, false)
  assert.equal(f.closed, 0)
})
test('phone Settings returns to the same document through browser history', () => {
  const f = fixture(); f.narrow(); f.current = { kind: 'settings', page: 'agents' }; f.history.sync()
  assert.equal(f.entries.length, 2)
  assert.equal(f.pop(0).stopped, true)
  assert.equal(f.current, null)
  assert.equal(f.win.location.href, 'https://app.example/threads/notes')
})
test('typed modal link restores its destination and closes without requiring an earlier app entry', () => {
  const f = fixture()
  f.win.history.replaceState(null, '', 'https://app.example/threads/notes?modal=settings&section=providers')
  f.history.restore()
  assert.equal(f.current.page, 'providers')
  f.history.dismiss(); f.current = null; f.history.sync()
  assert.equal(f.entries.length, 1)
  assert.equal(f.win.location.href, 'https://app.example/threads/notes')
})
test('normalizing a direct Activity URL keeps unrelated query and fragment without claiming a previous document entry', () => {
  const f = fixture()
  f.win.history.replaceState(null, '', 'https://app.example/threads/notes?modal=activity&keep=yes#next')
  f.history.restore(); f.history.sync()
  assert.equal(f.win.history.state.appModal, false)
  f.history.dismiss(); f.current = null; f.history.sync()
  assert.equal(f.win.location.href, 'https://app.example/threads/notes?keep=yes#next')
  assert.equal(f.entries.length, 1)
})
test('Settings profile detail scopes Recent to its agent or engineer, including after a detail switch', () => {
  installDom()
  const loads = [], host = { state: {}, settingsProfiles: { load: (...args) => loads.push(args) },
    engineerActivity: { st: {}, load() {} }, schedule() {} }
  const settings = mountSettings(host)
  settings.goSettingsDetail('engineer', 'Notebook')
  assert.equal(host.engineerActivity.st.project, 'Notebook')
  assert.equal(host.engineerActivity.st.kind, 'job')
  settings.goSettingsDetail('agent', 'test-agent')
  assert.deepEqual(host.state.sheet, { kind: 'settings', page: 'agents', detail: { kind: 'agent', id: 'test-agent' } })
  assert.equal(host.engineerActivity.st.project, null)
  assert.equal(host.engineerActivity.st.kind, '')
  assert.equal(host.engineerActivity.st.agent, 'test-agent')
  assert.deepEqual(loads, [['engineer', 'Notebook', undefined], ['agent', 'test-agent', undefined]])
})
