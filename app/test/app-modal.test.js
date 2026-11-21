import test from 'node:test'
import assert from 'node:assert/strict'
import { modalFromURL, modalURL, settingsParent, createAppModalHistory } from '../public/app-modal-history.js'
import { createWorkspaceHistory } from '../public/panel-history.js'
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
  let at = 0, current = null
  const win = { location: { href: entries[0].url }, addEventListener: (type, listener) => listeners.push(listener) }
  function move(index) { at = index; win.location.href = entries[at].url; return { stopped: false, stopImmediatePropagation() { this.stopped = true } } }
  function pop(index) {
    const event = move(index)
    for (const listener of listeners) {
      listener(event)
      if (event.stopped) break
    }
    return event
  }
  win.history = {
    get state() { return entries[at].state },
    pushState(state, _, url) { entries.splice(at + 1); entries.push({ url: String(url), state }); move(at + 1) },
    replaceState(state, _, url = win.location.href) { entries[at] = { url: String(url), state }; move(at) },
    go(delta) { pop(at + delta) },
  }
  const navigation = createWorkspaceHistory({ win, guard: () => true, apply() {},
    address: () => navigation.current('main')?.href ?? navigation.current('list')?.href ?? 'https://app.example/threads/notes',
    overlay: () => current ? () => { current = current.kind === 'settings' ? settingsParent(current) : null; history.sync() } : null })
  navigation.start('chats', win.location.href)
  const history = createAppModalHistory({ window: win, navigation, sheet: () => current, restore: value => { current = value } })
  history.restore()
  return { history, navigation, win, entries, pop, get current() { return current }, set current(v) { current = v } }
}
test('a popup and its pages replace the current entry, and Back closes it before leaving the panel', () => {
  const f = fixture()
  f.navigation.visit('main', 'https://app.example/threads/next')
  f.current = { kind: 'settings', page: 'provider', provider: 'claude-code' }; f.history.sync()
  assert.equal(f.entries.length, 3)
  assert.equal(f.pop(1).stopped, true)
  assert.deepEqual(f.current, { kind: 'settings', page: 'connections' })
  assert.equal(f.pop(1).stopped, true)
  assert.deepEqual(f.current, { kind: 'settings', page: 'root' })
  assert.equal(f.pop(1).stopped, true)
  assert.equal(f.current, null)
  assert.equal(f.win.location.href, 'https://app.example/threads/next')
  assert.equal(f.pop(1).stopped, true)
})
test('opening a file from Settings clears the overlay from the source before leaving', () => {
  const f = fixture()
  f.current = { kind: 'settings', page: 'agents', detail: { kind: 'agent', id: 'test-agent' } }; f.history.sync()
  f.history.leave()
  f.current = null
  f.navigation.visit('main', 'https://app.example/files/vault/Notes/plan.md')
  assert.equal(f.entries.length, 3)
  assert.equal(f.pop(1).stopped, true)
  assert.equal(f.current, null)
  assert.equal(f.win.location.href, 'https://app.example/threads/notes')
})
test('tab selection discards folder steps and leaves the main trail available to Back and Forward', () => {
  const f = fixture(), nav = f.navigation
  nav.visit('main', 'https://app.example/threads/new')
  nav.select('files'); nav.visit('list', 'https://app.example/files/vault')
  nav.select('apps')
  assert.equal(nav.can(-1, 'list'), false)
  assert.equal(nav.can(-1, 'main'), true)
  nav.go(-1); assert.equal(nav.current('main'), undefined)
  nav.go(1); assert.equal(nav.current('main').href, 'https://app.example/threads/new')
  assert.equal(nav.state.tab, 'apps')
})
test('surface arrows can undo out of order and keep the other surfaces eligible for system Back', () => {
  const { navigation: nav } = fixture()
  nav.visit('main', '/threads/first')
  nav.select('files'); nav.visit('list', '/files/vault')
  nav.visit('side', '/threads/companion')
  nav.visit('main', '/files/vault/plan.md')
  nav.go(-1, 'list')
  assert.equal(nav.current('list'), undefined)
  assert.equal(nav.current('main').href, '/files/vault/plan.md')
  nav.go(-1)
  assert.equal(nav.current('main').href, '/threads/first')
  assert.equal(nav.current('side').href, '/threads/companion')
  nav.go(-1)
  assert.equal(nav.current('side'), undefined)
  nav.visit('main', '/threads/new')
  nav.go(1, 'side')
  assert.equal(nav.current('side').href, '/threads/companion')
  assert.equal(nav.current('main').href, '/threads/new')
  assert.equal(nav.state.tab, 'files')
})
test('typed modal link restores its destination and closes without requiring an earlier app entry', () => {
  const f = fixture()
  f.win.history.replaceState(f.win.history.state, '', 'https://app.example/threads/notes?modal=settings&section=providers')
  f.history.restore()
  assert.equal(f.current.page, 'providers')
  f.current = null; f.history.sync()
  assert.equal(f.entries.length, 2)
  assert.equal(f.win.location.href, 'https://app.example/threads/notes')
})
test('normalizing a direct Activity URL keeps unrelated query and fragment without claiming a previous document entry', () => {
  const f = fixture()
  f.win.history.replaceState(f.win.history.state, '', 'https://app.example/threads/notes?modal=activity&keep=yes#next')
  f.history.restore(); f.history.sync()
  f.current = null; f.history.sync()
  assert.equal(f.win.location.href, 'https://app.example/threads/notes?keep=yes#next')
  assert.equal(f.entries.length, 2)
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
