import test from 'node:test'
import assert from 'node:assert/strict'
import { fakeBroker, appAgainst, closing, TOKEN } from './harness.js'
import { createFiles, capsOf, renameName, renamedResource } from '../public/files.js'
import { row, readAnswer } from './fixtures/files-contract.js'

const resource = (path, options = {}) => { const r = row('vault', path, options); return { ...r, capabilities: { ...r.capabilities, rename: true } } }
const moved = (r, path) => ({ ...resource(path, { kind: r.kind }), from: { root: r.root, path: r.path, link: r.link } })
const json = (res, status, body) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }

test('rename relay passes name/to, host capability, from, token and error detail unchanged', async () => {
  const broker = await fakeBroker({ 'POST /v1/files/rename': (req, res, url, raw) => {
    const body = JSON.parse(raw)
    if (body.status) return json(res, body.status, { error: 'The host’s sentence.', authority: 'user', pinned: true })
    if (body.root === 'system') return json(res, 403, { error: 'This belongs to the host.', authority: 'runtime' })
    json(res, 200, moved(resource(body.path), body.to ?? `Notes/${body.name}`))
  } })
  const app = await appAgainst(broker)
  try {
    for (const body of [{ root: 'vault', path: 'Notes/plan.md', name: 'Final draft.md' }, { root: 'vault', path: 'Notes/plan.md', to: 'Other/final.md' }]) {
      const res = await fetch(`${app.url}/api/files/rename`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })
      assert.equal(res.status, 200)
      const answer = await res.json()
      assert.equal(answer.from.path, body.path)
      assert.equal(answer.capabilities.rename, true)
      assert.deepEqual(JSON.parse(broker.seen.at(-1).body), body)
      assert.equal(broker.seen.at(-1).auth, `Bearer ${TOKEN}`)
    }
    for (const status of [400, 401, 403, 404, 409]) {
      const res = await fetch(`${app.url}/api/files/rename`, { method: 'POST', body: JSON.stringify({ status }) })
      assert.equal(res.status, status)
      assert.deepEqual(await res.json(), { error: 'The host’s sentence.', authority: 'user', pinned: true })
    }
    const before = broker.seen.length
    assert.equal((await fetch(`${app.url}/api/files/rename`)).status, 405)
    assert.equal((await fetch(`${app.url}/api/files/rename`, { method: 'POST', body: '{bad' })).status, 400)
    assert.equal(broker.seen.length, before)
    const refused = await fetch(`${app.url}/api/files/rename`, { method: 'POST', body: JSON.stringify({ root: 'system', path: 'x', name: 'y' }) })
    assert.equal(refused.status, 403)
    assert.deepEqual(await refused.json(), { error: 'This belongs to the host.', authority: 'runtime' })
  } finally { await closing(app, broker)() }
})

test('capability is explicit; filename extension is retained unless supplied; folder dots are names', () => {
  assert.equal(capsOf({ capabilities: { rename: 'true', write: true } }).rename, false)
  const r = resource('Notes/plan.md')
  for (const [value, expected] of [['Final draft', 'Final draft.md'], ['Final.txt', 'Final.txt'], ['plan', 'plan.md'], ['', ''], ['  ', ''], ['.', '.'], ['..', '..'], ['bad/name', 'bad/name.md']]) assert.equal(renameName(r, value), expected)
  assert.equal(renameName(resource('Notes/research.v2', { kind: 'folder' }), 'Research'), 'Research')
})

test('folder descendants follow from, with segment/root boundaries and unrelated objects preserved', () => {
  const answer = moved(resource('Notes', { kind: 'folder' }), 'Ideas')
  assert.equal(renamedResource({ root: 'vault', path: 'Notes/one.md' }, answer).path, 'Ideas/one.md')
  for (const r of [{ root: 'spaces', path: 'Notes/one.md' }, { root: 'vault', path: 'Notes elsewhere/one.md' }]) assert.equal(renamedResource(r, answer), r)
})

function controller(api, onRename) {
  return createFiles({ api, onRename, schedule() {}, timers: { set: () => 1, clear() {} } })
}

test('rename waits for an in-flight save, preserves newer draft/revision and applies the host name', async () => {
  const r = resource('Notes/plan.md'), calls = []
  let finishSave
  const files = controller(async (method, path, body) => {
    calls.push({ method, path, body })
    if (path.startsWith('/files/read')) return readAnswer(r, 'original', 'rev1')
    if (method === 'PUT' && !finishSave) return new Promise(resolve => { finishSave = () => resolve(readAnswer(r, body.text, 'rev2')) })
    if (method === 'PUT') return readAnswer(resource(body.path), body.text, 'rev3')
    if (path === '/files/rename') return moved(r, 'Notes/Final draft.md')
    return { url: '/files/vault/Notes/plan.md', kind: 'Loam link' }
  }, answer => { assert.equal(answer.from.path, r.path) })
  await files.openFile(r.root, r.path, r)
  files.st.listing.rows = [r]
  files.edit('first edit')
  const save = files.save()
  files.edit('newer edit')
  files.beginRename(r)
  files.editRename('  final draft  ')
  const rename = files.rename()
  assert.equal(calls.filter(c => c.path === '/files/rename').length, 0)
  finishSave()
  await Promise.all([save, rename])
  assert.equal(files.st.file.path, 'Notes/Final draft.md')
  assert.equal(files.st.file.draft, 'newer edit')
  assert.equal(files.st.file.saved, 'first edit')
  assert.equal(files.st.file.revision, 'rev2')
  assert.equal(files.st.listing.rows[0].path, files.st.file.path)
  assert.equal(files.st.renaming, null)
  assert.deepEqual(calls.find(c => c.path === '/files/rename').body, { root: 'vault', path: r.path, name: 'final draft.md' })
  await files.save()
  assert.equal(calls.filter(c => c.method === 'PUT').at(-1).body.path, 'Notes/Final draft.md')
})

test('host refusals keep draft/name/old path, retry succeeds; cancel and unchanged names send nothing', async () => {
  let fail = true, requests = 0
  const r = resource('Notes/plan.md')
  const files = controller(async (method, path) => {
    if (path.startsWith('/files/read')) return readAnswer(r, 'original', 'rev1')
    if (path !== '/files/rename') return { url: r.link }
    requests++
    if (fail) throw Object.assign(new Error('vault/Notes/Taken.md already exists; choose another name'), { status: 409 })
    return moved(r, 'Notes/Final.md')
  })
  await files.openFile(r.root, r.path, r)
  files.beginRename({ ...r, capabilities: { write: true } })
  assert.equal(files.st.renaming, null)
  files.beginRename(r); await files.rename()
  assert.equal(requests, 0)
  files.beginRename(r); files.editRename('changed'); files.cancelRename()
  assert.equal(requests, 0)
  files.edit('unsaved words')
  files.beginRename(r); files.editRename('Taken'); await files.rename()
  assert.match(files.st.renaming.error, /already exists/)
  assert.equal(files.st.file.path, r.path)
  assert.equal(files.st.file.draft, 'unsaved words')
  assert.equal(files.st.renaming.value, 'Taken')
  fail = false; files.editRename('Final'); await files.rename()
  assert.equal(files.st.file.path, 'Notes/Final.md')
  assert.equal(files.st.file.draft, 'unsaved words')
})

test('a save requested during rename waits, then writes the same draft at the returned path', async () => {
  const r = resource('Notes/plan.md'), puts = []
  let finishRename
  const files = controller(async (method, path, body) => {
    if (path.startsWith('/files/read')) return readAnswer(r, 'original', 'rev1')
    if (path === '/files/rename') return new Promise(resolve => { finishRename = () => resolve(moved(r, 'Notes/New.md')) })
    if (method === 'PUT') { puts.push(body); return readAnswer(resource(body.path), body.text, 'rev2') }
    return { url: r.link }
  })
  await files.openFile(r.root, r.path, r)
  files.edit('keep these words')
  files.beginRename(r); files.editRename('New')
  const rename = files.rename(), save = files.save()
  assert.equal(puts.length, 0)
  finishRename()
  await Promise.all([rename, save])
  assert.equal(puts.length, 1)
  assert.equal(puts[0].path, 'Notes/New.md')
  assert.equal(puts[0].text, 'keep these words')
  assert.equal(files.dirty(), false)
})
