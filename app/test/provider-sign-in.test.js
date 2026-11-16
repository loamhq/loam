import test from 'node:test'
import assert from 'node:assert/strict'
import { installDom } from './fakedom.js'
import { find, all, text } from './browser.js'
import { until } from './harness.js'
import { morph } from '../public/ui.js'
import { createProviderSignIn, signInRoute } from '../public/provider-sign-in.js'
import { providerFixture, flowFixture } from './fixtures/provider-sign-in.js'

installDom()
function setup(provider = providerFixture(), override = {}) {
  const { api: customApi, ...options } = override
  let row = structuredClone(provider), state = 'pending', changed = 0
  const root = document.createElement('div')
  const calls = [], tabs = [], copies = []
  const controller = createProviderSignIn({
    api: async (method, path, body) => {
      calls.push({ method, path, body })
      if (customApi) return customApi(method, path, body)
      return path.endsWith('/logout') ? { login: providerFixture().login, removed: true }
        : flowFixture(row, state)
    }, changed: () => changed++, refresh: () => {},
    update: (id, login, done) => { row = { ...row, login, connected: login.connected ?? done } },
    openTab: () => { const tab = { opener: 'original', closed: false, close() { this.closed = true }, location: { replace(url) { tab.url = url } } }; tabs.push(tab); return tab },
    copy: async (value) => { copies.push(value); return true }, ...options,
  })
  return { controller, calls, tabs, copies, get changed() { return changed }, card: () => { const card = controller.card(row); morph(root, card ? [card] : []); return root.firstChild },
    setState(value) { state = value } }
}
const click = (node) => node.fire('click')

test('descriptor routes remain local and restricted to provider endpoints', () => {
  assert.equal(signInRoute('/v1/providers/new-provider/login/code'), '/providers/new-provider/login/code')
  for (const path of ['https://example.invalid/v1/providers/test/login', '/api/providers/test/login', '/v1/threads', '/v1/providers/test/login?other', null]) assert.equal(signInRoute(path), null)
})

for (const [id, kind] of [['antigravity', 'paste-code'], ['claude-code', 'paste-code'], ['codex', 'device-code'], ['future', 'paste-code']]) {
  test(`${id}: one descriptor card handles signed out, waiting, failure and signed in`, async () => {
    const f = setup(providerFixture(id, kind))
    assert.equal(text(find(f.card(), 'signline')), 'Not signed in')
    assert.equal(text(find(f.card(), 'primary')), 'Sign in')
    assert.equal(find(f.card(), 'loginflow'), null)
    click(find(f.card(), 'primary'))
    assert.equal(f.tabs.length, 1, 'the new tab must be opened during the tap')
    assert.equal(f.tabs[0].opener, null)
    await until(() => find(f.card(), 'loginlink'))
    assert.equal(f.tabs[0].url, `https://sign-in.example.invalid/${id}`)
    assert.deepEqual(f.calls[0], { method: 'POST', path: `/providers/${id}/login`, body: {} })
    const flow = find(f.card(), 'loginflow')
    assert.doesNotMatch(text(flow), /steps|documentation/)
    if (kind === 'paste-code') {
      assert.equal(find(flow, 'flat').childNodes[1].getAttribute('aria-label'), 'Paste the code you were given')
      find(flow, 'flat').childNodes[1].value = 'fixture-only-wrong-code'
      f.setState('failed')
      flow.fire('submit', { preventDefault() {} })
    } else {
      assert.equal(find(flow, 'flat'), null)
      assert.equal(text(find(flow, 'devcode')), 'ABCD-EFGH')
      f.setState('failed')
      click(find(flow, 'primary'))
    }
    await until(() => text(f.card()).includes('That code was not accepted.'))
    assert.equal(text(find(f.card(), 'primary')), 'Try again')
    f.setState('pending')
    click(find(f.card(), 'primary'))
    await until(() => find(f.card(), 'loginflow') && find(f.card(), 'loginlink'))
    assert.equal(f.calls.filter((c) => c.method === 'POST' && c.path.endsWith('/login')).length, 2)
    f.setState('done')
    await f.controller.poll()
    assert.equal(find(f.card(), 'loginflow'), null)
    assert.equal(text(find(f.card(), 'signline')), id === 'claude-code' ? 'Signed in' : 'Signed in as person@example.invalid')
    const actions = all(f.card(), 'quietlink').map(text)
    assert.deepEqual(actions, id === 'antigravity' ? ['Sign in again', 'Sign out'] : ['Sign in again'])
    if (id === 'antigravity') {
      click(all(f.card(), 'quietlink')[1])
      await until(() => text(find(f.card(), 'signline')) === 'Not signed in')
      assert.deepEqual(f.calls.at(-1), { method: 'POST', path: '/providers/antigravity/logout', body: {} })
    }
  })
}

test('a provider with no descriptor offers no sign-in; optional descriptor words need no provider code', () => {
  const f = setup({ ...providerFixture(), sign_in: null })
  assert.equal(f.card(), null)
  const provider = providerFixture('future')
  provider.sign_in.label = 'Sign in with Google'
  assert.equal(text(find(setup(provider).card(), 'primary')), 'Sign in with Google')
})

test('the shared signed-in card retains the host’s expiry warning and renewal', () => {
  const provider = providerFixture('claude-code')
  provider.connected = true
  provider.login = { source: 'credentials-file', days_left: 1, expires_at: '2026-11-13T09:00:00Z' }
  const card = setup(provider).card()
  assert.equal(text(find(card, 'signline')), 'Signed in')
  assert.match(text(all(card, 'signline')[1]), /Signed in · expires/)
  assert.equal(all(card, 'signline')[1].getAttribute('class'), 'note signline warn')
  assert.equal(text(find(card, 'quietlink')), 'Sign in again')
})

test('a blocked popup retains a new-tab link; a pasted landing URL is sent whole', async () => {
  const f = setup(providerFixture(), { openTab: () => null })
  click(find(f.card(), 'primary'))
  await until(() => find(f.card(), 'loginlink'))
  const link = find(f.card(), 'loginlink')
  assert.equal(link.getAttribute('target'), '_blank')
  assert.equal(link.getAttribute('rel'), 'noopener noreferrer')
  const form = find(f.card(), 'loginflow'), field = find(form, 'flat').childNodes[1]
  field.value = 'https://landing.example.invalid/?code=fixture-code&state=fixture-state'
  f.setState('done')
  form.fire('submit', { preventDefault() {} })
  await until(() => !find(f.card(), 'loginflow'))
  assert.deepEqual(f.calls.at(-1).body, { code: 'https://landing.example.invalid/?code=fixture-code&state=fixture-state' })
  assert.equal(field.value, '')
})

test('leaving cancels the pending flow and a delayed start cannot reopen it', async () => {
  let answer
  const f = setup(providerFixture(), { api: (method) => method === 'POST' ? new Promise((resolve) => { answer = resolve }) : Promise.resolve({ state: 'idle' }) })
  click(find(f.card(), 'primary'))
  f.controller.cancel()
  answer(flowFixture(providerFixture()))
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(find(f.card(), 'loginflow'), null)
  assert.equal(f.tabs[0].closed, true)
  assert.equal(f.tabs[0].url, undefined)
})

test('a stale poll cannot replace success after a submitted code', async () => {
  let pollAnswer
  const row = providerFixture()
  const f = setup(row, { api: async (method, path) => method === 'GET' ? new Promise((resolve) => { pollAnswer = resolve }) : flowFixture(row, path.endsWith('/code') ? 'done' : 'pending') })
  click(find(f.card(), 'primary'))
  await until(() => find(f.card(), 'loginlink'))
  const poll = f.controller.poll()
  const form = find(f.card(), 'loginflow')
  find(form, 'flat').childNodes[1].value = 'fixture-code'
  form.fire('submit', { preventDefault() {} })
  await until(() => !find(f.card(), 'loginflow'))
  pollAnswer(flowFixture(row))
  await poll
  assert.equal(find(f.card(), 'loginflow'), null)
  assert.equal(text(find(f.card(), 'signline')), 'Signed in as person@example.invalid')
})

test('a code timeout offers a new flow; a network failure retains the field for retry', async () => {
  const row = providerFixture()
  let status = 503
  const f = setup(row, { api: async (method, path) => {
    if (!path.endsWith('/code')) return flowFixture(row)
    throw Object.assign(new Error(status === 409 ? 'This sign-in has expired. Try again.' : 'Could not reach the provider. Try again.'), { status })
  } })
  click(find(f.card(), 'primary'))
  await until(() => find(f.card(), 'loginlink'))
  const form = find(f.card(), 'loginflow'), field = find(form, 'flat').childNodes[1]
  field.value = 'fixture-code'
  form.fire('submit', { preventDefault() {} })
  await until(() => text(f.card()).includes('Could not reach the provider.'))
  assert.equal(field.value, 'fixture-code')
  assert.equal(find(f.card(), 'flat').childNodes[1], field)
  status = 409
  find(f.card(), 'loginflow').fire('submit', { preventDefault() {} })
  await until(() => text(f.card()).includes('This sign-in has expired.'))
  assert.equal(find(f.card(), 'loginflow'), null)
  assert.equal(text(find(f.card(), 'primary')), 'Try again')
})

test('only accepts_code allows submission and an empty code never posts', async () => {
  const row = providerFixture()
  let accepts = false
  const f = setup(row, { api: async () => ({ ...flowFixture(row), accepts_code: accepts }) })
  click(find(f.card(), 'primary'))
  await until(() => find(f.card(), 'loginlink'))
  assert.equal(find(f.card(), 'flat'), null)
  assert.equal(find(find(f.card(), 'loginflow'), 'primary').hasAttribute('disabled'), true)
  accepts = true
  await f.controller.poll()
  const form = find(f.card(), 'loginflow'), field = find(form, 'flat').childNodes[1]
  field.value = '   '
  form.fire('submit', { preventDefault() {} })
  assert.match(text(f.card()), /Paste the code you were given\./)
  assert.equal(f.calls.some((c) => c.path.endsWith('/code')), false)
})
