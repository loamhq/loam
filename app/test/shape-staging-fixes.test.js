import test from 'node:test'
import assert from 'node:assert/strict'
import { fakeBroker, appAgainst, closing } from './harness.js'
import { createFiles, FILES_LOCATION_HIDDEN } from '../public/files.js'
import { activityWords } from '../public/activity.js'
import { nodePluginPage } from './plugin-page-fixture.js'
import { rootsAnswer } from './fixtures/files-contract.js'
const hook = nodePluginPage('acme', 'test/fixtures/acme-page')
const { ACME_USES } = await import('./fixtures/acme-page/page/view.js')
hook.deregister()

test('System-specific file requests are rejected without contacting the broker; shared Files routes remain', async t => {
  const broker = await fakeBroker({ 'GET /v1/files/roots': (req, res) => res.end(JSON.stringify(rootsAnswer())) })
  const app = await appAgainst(broker); t.after(closing(app, broker))
  for (const name of ['list', 'stat', 'read', 'download', 'preview', 'live', 'link']) {
    const response = await fetch(`${app.url}/api/files/${name}?root=system&path=Deployment.md`)
    assert.equal(response.status, 404, name); assert.equal((await response.json()).error, FILES_LOCATION_HIDDEN)
  }
  assert.equal((await fetch(`${app.url}/api/files/resolve?from_root=system&ref=Logs.md`)).status, 404)
  for (const [method, name] of [['PUT', 'text'], ['POST', 'file'], ['POST', 'folder']]) {
    const response = await fetch(`${app.url}/api/files/${name}`, { method, headers: { 'content-type': 'application/json' }, body: JSON.stringify({ root: 'system', path: 'new.md', text: '' }) })
    assert.equal(response.status, 404, name)
  }
  assert.equal((await fetch(`${app.url}/api/files/upload?root=system`, { method: 'POST', body: 'file envelope' })).status, 404)
  assert.equal(broker.seen.length, 0)
  assert.equal((await fetch(`${app.url}/api/files/roots`)).status, 200)
  assert.equal(broker.seen.length, 1)
})
test('old System browsing and saved shortcuts land quietly at home and never stat the removed tree', async () => {
  const calls = [], files = createFiles({ schedule() {}, api: async (method, path) => { calls.push(path); return rootsAnswer() } })
  await files.show('system', 'Engineer/Role.md')
  assert.equal(files.st.root, null); assert.equal(files.st.file, null)
  assert.equal(files.st.notice, FILES_LOCATION_HIDDEN)
  let target
  await files.openShortcut({ root: 'system', path: 'Logs.md' }, (root, path) => { target = { root, path } })
  assert.deepEqual(target, { root: 'system', path: 'Logs.md' })
  assert.ok(calls.every(path => !path.includes('root=system')))
})
test('cut-off and failed outcomes are explicit failures; landed remains quiet', () => {
  for (const outcome of ['killed', 'cut_off', 'cut off']) {
    const words = activityWords({ kind: 'job', title: 'A task', outcome })
    assert.equal(words.action, 'cut off'); assert.equal(words.failed, true)
  }
  assert.equal(activityWords({ kind: 'job', outcome: 'failed' }).failed, true)
  assert.equal(activityWords({ kind: 'job', outcome: 'completed', landing: 'landed' }).failed, false)
})
test('Acme describes acting as the person without claiming a signature', () => {
  assert.equal(ACME_USES, 'The agents read and write Acme as you.')
})
