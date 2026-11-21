import assert from 'node:assert/strict'
import test from 'node:test'
import { appAgainst, closing, fakeBroker, TOKEN, until } from './harness.js'
import { browser, all, find, text } from './browser.js'
import { oneProductFixture } from './one-product-fixture.mjs'
import { deleteHost } from './fixtures/files-delete.js'
import { readAnswer, row } from './fixtures/files-contract.js'
import { createFiles } from '../public/files.js'

test('file deletion relays only a vault address and preserves the broker response', async () => {
  const { routes, state } = deleteHost()
  const broker = await fakeBroker(routes)
  const app = await appAgainst(broker)
  const url = `${app.url}/api/files/delete`
  const post = (body, headers = {}) => fetch(url, { method: 'POST', headers, body: JSON.stringify(body) })
  const address = { root: 'vault', path: 'Notes/plan.md' }
  try {
    assert.equal((await fetch(url)).status, 405)
    assert.equal((await fetch(url, { method: 'POST', body: '{' })).status, 400)
    assert.equal((await post({ ...address, root: 'spaces' })).status, 400)
    assert.equal((await post({ ...address, path: '' })).status, 400)
    assert.equal((await post({ ...address, path: '/outside.md' })).status, 400)
    assert.equal((await post({ ...address, path: '../outside.md' })).status, 400)
    assert.equal((await post(address, { origin: 'https://example.com' })).status, 403)
    assert.equal(state.calls.length, 0)
    state.refuse = true
    const denied = await post(address)
    assert.equal(denied.status, 403)
    assert.deepEqual(await denied.json(), { error: 'The vault refused this deletion.' })
    state.refuse = false
    const confirmed = await post({ ...address, by: 'ignored' })
    assert.equal(confirmed.status, 200)
    const deleted = await confirmed.json()
    assert.equal(deleted.ok, true)
    assert.match(deleted.restore_id, /^[0-9a-f]{32}$/)
    const call = broker.seen.at(-1)
    assert.equal(call.auth, `Bearer ${TOKEN}`)
    assert.deepEqual(JSON.parse(call.body), address)
  } finally { await closing(app, broker)() }
})

test('Undo relays only a vault restore token and preserves the restored row', async () => {
  const { routes } = deleteHost()
  const broker = await fakeBroker(routes)
  const app = await appAgainst(broker)
  const address = { root: 'vault', path: 'Notes/plan.md' }
  try {
    const deleted = await fetch(`${app.url}/api/files/delete`, { method: 'POST', body: JSON.stringify(address) }).then(res => res.json())
    const restoreUrl = `${app.url}/api/files/restore`
    const restore = (body, headers = {}) => fetch(restoreUrl, { method: 'POST', headers, body: JSON.stringify(body) })
    const undo = { root: 'vault', restore_id: deleted.restore_id }
    assert.equal((await restore({ ...undo, restore_id: '../somewhere' })).status, 400)
    assert.equal((await restore(undo, { origin: 'https://example.com' })).status, 403)
    const restored = await restore({ ...undo, path: 'ignored' })
    assert.equal(restored.status, 200)
    assert.equal((await restored.json()).path, address.path)
    assert.equal(broker.seen.at(-1).auth, `Bearer ${TOKEN}`)
    assert.deepEqual(JSON.parse(broker.seen.at(-1).body), undo)
  } finally { await closing(app, broker)() }
})

test('a save settles before deletion and a queued save cannot write the deleted file', async () => {
  const file = row('vault', 'Notes/plan.md')
  let settleSave, settleDelete, writes = 0
  const files = createFiles({ schedule() {}, timers: { set: () => 1, clear() {} }, api: async (method, path, body) => {
    if (method === 'PUT') {
      writes++
      return new Promise(resolve => { settleSave = () => resolve(readAnswer(file, body.text, 'sha256:next')) })
    }
    return readAnswer(file, 'Original', 'sha256:current')
  } })
  await files.openFile(file.root, file.path, file)
  files.edit('First edit')
  const saving = files.save()
  const deletion = new Promise(resolve => { settleDelete = resolve })
  const hold = files.holdSavesForDelete(file, deletion)
  assert.equal(hold.ready, files.st.file.inflight)
  files.edit('Edit during the save')
  const queued = files.save()
  settleSave()
  await hold.ready
  assert.equal(writes, 1)
  assert.equal(files.dirty(), true)
  files.removeDeletedFile(file)
  hold.release()
  settleDelete()
  await Promise.all([saving, queued])
  assert.equal(writes, 1)
  assert.equal(files.st.file, null)
})

test('Files keeps a refused file, returns to the previous panel on deletion, and restores the row on Undo', async () => {
  const host = deleteHost()
  const app = await oneProductFixture()
  Object.assign(app.routes, host.routes)
  const location = new URL(`${app.url}/files/vault/Notes`)
  globalThis.location = location
  globalThis.history = {
    state: null,
    pushState(data, title, href) { this.replaceState(data, title, href) },
    replaceState(data, title, href) { this.state = structuredClone(data); location.href = new URL(href, location).href },
  }
  const page = browser(app.url, { innerWidth: 1440 })
  const click = node => node.fire('click', { preventDefault() {}, stopPropagation() {} })
  const open = name => all(page.root, 'setrow').find(row => text(find(row, 'fs-name')) === name)
  const menu = name => all(page.root, 'fs-rowmenu').find(button => button.getAttribute('aria-label') === `Actions for ${name}`)
  page.watch()
  try {
    await import('../public/app.js')
    await until(() => menu('plan.md'))
    for (const name of ['Old', 'pinned.md']) {
      click(menu(name))
      const sheet = await until(() => find(page.root, 'fs-sheet'))
      assert.equal(find(sheet, 'fs-menu-delete'), null)
      click(find(page.root, 'scrim'))
      await until(() => !find(page.root, 'fs-sheet'))
    }

    click(open('Taken.md'))
    await until(() => location.pathname.endsWith('/Taken.md') && find(page.root, 'fs-preview'))
    click(open('plan.md'))
    await until(() => location.pathname.endsWith('/plan.md') && text(find(page.root, 'fs-edtitle')) === 'plan' && find(page.root, 'fs-preview'))
    click(find(page.root, 'fs-filemenu'))
    const action = await until(() => find(page.root, 'fs-menu-delete'))
    assert.equal(action, action.parentNode.childNodes.at(-1))
    click(action)
    await until(() => find(page.root, 'fs-delete-submit'))
    assert.match(text(find(page.root, 'fs-delete-copy')), /plan\.md/)
    click(find(page.root, 'scrim'))
    await until(() => !find(page.root, 'fs-delete-submit'))
    assert.equal(host.state.calls.length, 0)

    click(menu('plan.md'))
    click(await until(() => find(page.root, 'fs-menu-delete')))
    host.state.refuse = true
    click(await until(() => find(page.root, 'fs-delete-submit')))
    const error = await until(() => find(page.root, 'fs-delete-error'))
    assert.equal(text(error), 'The vault refused this deletion.')
    assert.ok(open('plan.md'))
    assert.match(location.pathname, /plan\.md$/)

    let release
    host.state.hold = new Promise(resolve => { release = resolve })
    host.state.refuse = false
    const confirm = find(page.root, 'fs-delete-submit')
    click(confirm)
    click(confirm)
    await until(() => host.state.calls.length === 2)
    release()
    await until(() => !open('plan.md') && location.pathname.endsWith('/Taken.md'))
    assert.equal(host.state.calls.length, 2)
    assert.equal(host.entries.has('Notes/plan.md'), false)
    assert.equal(open('Taken.md') !== undefined, true)
    assert.equal(text(find(page.root, 'listnotice-text')), 'Deleted plan.md.')
    assert.ok(!history.state.workspace.visits.some(step => step.href.includes('/plan.md')))
    const undo = find(find(page.root, 'fs-delete-toast'), 'listnotice-act')
    assert.equal(text(undo), 'Undo')
    click(undo)
    await until(() => open('plan.md'))
    assert.equal(host.state.restores.length, 1)
    assert.equal(text(find(page.root, 'listnotice-text')), 'Restored plan.md.')
    assert.match(location.pathname, /Taken\.md$/)
  } finally {
    page.stop()
    await app.close()
    setImmediate(() => process.exit(process.exitCode ?? 0))
  }
})
