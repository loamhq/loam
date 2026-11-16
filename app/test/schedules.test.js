import { setOwner } from '../public/owner.js'
setOwner({ id: 'you', name: 'Sam', accepts: ['you', 'owner', 'person'] })
import test from 'node:test'
import assert from 'node:assert/strict'
import { appAgainst, closing, TOKEN } from './harness.js'
import { schedulesHost } from './schedules-host.js'
import { createSchedules, cadence, runReason, unseen, editWords } from '../public/schedules.js'
import { createFiles, saveState } from '../public/files.js'
import { schedule, refusal, grammar } from '../tools/schedule-contract-fixtures.js'

async function setup(t, options = {}) {
  const broker = await schedulesHost(options), app = await appAgainst(broker)
  t.after(closing(broker, app))
  const api = async (method, path, body) => {
    const res = await fetch(`${app.url}/api${path}`, { method, headers: { 'content-type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
    const data = await res.json()
    if (!res.ok) throw Object.assign(new Error(data.error), { status: res.status, body: data })
    return data
  }
  const files = createFiles({ api, schedule() {}, timers: { set() {}, clear() {} }, onRead: (row) => c.seeFile(row) })
  const c = createSchedules({ api, redraw() {}, openFile: (ref) => files.openFile(ref.root, ref.path) })
  await c.load('scout')
  return { broker, app, c, files, api, st: c.st }
}
test('list reads exact file state, prior running version, next fire and last edit through bearer relay', async (t) => {
  const { broker, st } = await setup(t, { fileFirst: true })
  assert.deepEqual(st.rows.map((r) => r.state), ['broken', 'refused', 'off', 'live'])
  assert.equal(st.rows[0].running, 'previous')
  assert.equal(st.rows[0].next_fire, '2026-10-16T14:00:00Z')
  assert.equal(cadence(st.rows[0]), '')
  assert.equal(st.rows[1].last_edit.by, 'agent')
  assert.equal(st.rows[3].last_edit.by, 'operator')
  assert.equal(unseen(st.rows[1]), true)
  const call = broker.seen.find((r) => r.path === '/v1/schedules')
  assert.equal(call.search, '?agent=scout')
  assert.equal(call.auth, `Bearer ${TOKEN}`)
  assert.equal(editWords({ ts: '2026-10-15T00:00:00Z', by: 'owner' }, Date.parse('2026-10-15T00:03:00Z')), 'Edited 3m ago by you')
})
test('tab entry marks existing changes; background reads and subsequent polls do not mark new changes', async (t) => {
  const { c, st, broker } = await setup(t)
  assert.equal(c.hasUnseen(), true)
  assert.equal(broker.seen.some((r) => r.path.endsWith('/seen')), false)
  await c.load('scout', { seen: true })
  assert.equal(c.hasUnseen(), false)
  const refused = refusal('scout', 'weekly-review')
  broker.fake.activities.set(refused.id, refused)
  st.lastFetch = 0
  await c.tick(true)
  assert.equal(c.hasUnseen(), true)
  assert.equal(broker.fake.activities.get(refused.id).seen, false)
})
test('opening a row opens its exact Files resource, marks it seen and creates no schedule detail form', async (t) => {
  const { c, files, st, broker } = await setup(t, { fileFirst: true })
  await c.open(st.rows[1])
  assert.equal(st.editor, null)
  assert.equal(files.st.file.path, 'scout/crons/inbox-sweep.md')
  assert.equal(files.st.file.row.authority, 'schedule')
  assert.equal(files.st.file.caps.write, true)
  assert.equal(unseen(st.rows[1]), false)
  assert.equal(broker.seen.some((r) => r.method === 'GET' && r.path === '/v1/schedules/scout/inbox-sweep'), false)
})
test('direct Files open marks schedule seen; persona instruction does not use schedule route', async (t) => {
  const { c, files, st, broker } = await setup(t, { fileFirst: true })
  await files.openFile('spaces', 'scout/crons/drafts.md')
  assert.equal(unseen(st.rows[2]), false)
  const before = broker.seen.length
  await c.seeFile({ root: 'spaces', path: 'scout/_reflection.md', authority: 'persona' })
  assert.equal(broker.seen.length, before)
})
test('Files saves exact bytes, shows the reconciled schedule and points at a broken line without treating save as failed', async (t) => {
  const { c, files, st, broker } = await setup(t, { fileFirst: true })
  await c.open(st.rows[0])
  files.edit(broker.files.goodScheduleText)
  await files.save()
  let f = files.st.file
  assert.equal(f.schedule.state, 'live')
  assert.equal(f.flash, 'Saved')
  assert.equal(f.text, broker.files.goodScheduleText)
  assert.equal(f.row.authority, 'schedule')
  files.edit(broker.files.brokenScheduleText)
  await files.save()
  f = files.st.file
  assert.equal(f.failed, false)
  assert.equal(files.dirty(), false)
  assert.equal(f.schedule.state, 'broken')
  assert.equal(f.schedule.running, 'previous')
  assert.equal(f.schedule.problem.line, 2)
  assert.equal(f.schedule.problem.text, 'schedule: weekdays at 7am')
  assert.match(f.schedule.state_words, /previous version still runs/)
  assert.equal(f.text, broker.files.brokenScheduleText)
  assert.deepEqual(Object.keys(broker.files.puts.at(-1)), ['root', 'path', 'text', 'base_revision'])
  assert.equal(saveState(f, false).word, 'Saved')
  files.edit(broker.files.goodScheduleText)
  assert.equal(f.schedule, null, 'a newer draft must not show a prior save’s status')
})
test('Files revision conflict keeps schedule draft; no schedule feedback is invented', async (t) => {
  const { c, files, st, broker } = await setup(t, { fileFirst: true })
  await c.open(st.rows[0])
  broker.files.conflict()
  files.edit(broker.files.goodScheduleText)
  await files.save()
  assert.ok(files.st.file.conflict)
  assert.equal(files.st.file.draft, broker.files.goodScheduleText)
  assert.equal(files.st.file.schedule, null)
  assert.equal(saveState(files.st.file, true).tone, 'bad')
})
test('switch uses enabled only, preserves host refusal and never flips a broken file optimistically', async (t) => {
  const { c, files, st, broker } = await setup(t, { fileFirst: true })
  const broken = st.rows[0]
  await c.toggle(broken)
  assert.equal(st.rows[0].enabled, true)
  assert.equal(st.rows[0].uiError, broken.error)
  assert.deepEqual(JSON.parse(broker.seen.at(-1).body), { enabled: false })
  await c.toggle(st.rows[3])
  assert.equal(st.rows[3].state, 'off')
  assert.equal(st.rows[3].next_fire, null)
  assert.match(runReason(st.rows[3]), /Off/)
  await c.toggle(st.rows[3])
  assert.equal(st.rows[3].state, 'live')
  await c.open(st.rows[0])
  files.edit(broker.files.goodScheduleText)
  await files.save()
  st.lastFetch = 0
  await c.tick(true)
  assert.equal(st.rows[0].state, 'live')
  assert.equal(st.rows[0].uiError, null, 'a fixed file must not retain an old switch refusal')
})
test('create both types sends only accepted keys, keeps refused draft, and closes after successful write', async (t) => {
  const { c, st, broker } = await setup(t)
  c.add()
  Object.assign(st.editor.draft, { name: 'morning-digest', schedule: 'every blue moon', body: 'Write the digest.' })
  await c.save(st.editor)
  assert.equal(st.editor.error, grammar('every blue moon'))
  assert.equal(st.editor.draft.schedule, 'every blue moon')
  st.editor.draft.schedule = 'weekdays at 06:30 PT'
  await c.save(st.editor)
  assert.equal(st.editor, null)
  c.add()
  Object.assign(st.editor.draft, { name: 'check-pr', kind: 'one-off', at: '2026-10-16 14:30 PT', note: 'did the PR merge?', body: 'Check the PR.' })
  await c.save(st.editor)
  assert.deepEqual(JSON.parse(broker.seen.filter((r) => r.method === 'POST' && r.path === '/v1/schedules/scout').at(-1).body), { name: 'check-pr', at: '2026-10-16 14:30 PT', note: 'did the PR merge?', body: 'Check the PR.' })
})
test('Run now requests execution without inventing a fire; later fire removes a completed one-off', async (t) => {
  const { c, st, api, broker } = await setup(t)
  const once = st.rows[2]
  await c.act(once, 'POST', '/schedules/scout/prepare-notes/run', {}, 'Run requested.')
  assert.equal(st.rows[2].last_fire, null)
  assert.equal(st.rows[2].uiMessage, 'Run requested.')
  broker.fake.fire('scout', 'prepare-notes')
  st.lastFetch = 0
  await c.tick(true)
  assert.equal(st.rows.length, 2)
  const row = await api('GET', '/schedules/scout/prepare-notes')
  assert.match(runReason(row), /already run/)
  await assert.rejects(api('POST', '/schedules/scout/prepare-notes/run', {}), /already run/)
})
test('missing routes and unsupported hosts retain distinct sentences; failed seen stays unseen', async (t) => {
  const { api, c, broker } = await setup(t)
  await assert.rejects(api('GET', '/schedules/scout/nope'), (e) => e.status === 404 && e.message === 'there is no schedule scout/nope')
  await c.load('nobody')
  assert.equal(c.st.error, "'nobody' is not an agent on this box")
  const absent = createSchedules({ api: async () => { throw Object.assign(new Error('no route'), { status: 405 }) }, redraw() {} })
  await absent.load('scout')
  assert.match(absent.st.error, /not available from this host yet/)
  const row = schedule('scout', { changed_since_seen: true })
  const failed = createSchedules({ api: async (method) => { if (method === 'POST') throw new Error('Try again.'); return { schedules: [row] } }, redraw() {} })
  await failed.load('scout', { seen: true })
  assert.equal(failed.hasUnseen(), true)
  assert.equal(failed.st.error, 'Try again.')
  assert.ok(broker.seen.length)
})
test('obsolete reads do not cross agents and repeated control writes are blocked', async () => {
  let resolve
  const c = createSchedules({ api: (m, p) => p.includes('first') ? new Promise((r) => { resolve = r }) : Promise.resolve({ schedules: [] }), redraw() {} })
  const first = c.load('first')
  await c.load('second')
  resolve({ schedules: [schedule('first')] }); await first
  assert.deepEqual(c.st.rows, [])
  let finish, writes = 0
  const other = createSchedules({ api: async (method) => {
    if (method === 'POST') { writes++; await new Promise((r) => { finish = r }); return { event: {} } }
    return { schedules: [] }
  }, redraw() {} })
  await other.load('scout'); other.add()
  const pending = other.save(other.st.editor)
  await other.save(other.st.editor)
  assert.equal(writes, 1)
  finish(); await pending
  assert.equal(other.st.editor, null)
})
test('reopening a tab during an old switch request does not strand its control as busy', async () => {
  let finish
  const row = schedule('scout')
  const c = createSchedules({ api: async (method) => {
    if (method === 'PATCH') { await new Promise((resolve) => { finish = resolve }); row.enabled = false; row.state = 'off'; return { schedule: row } }
    return { schedules: [structuredClone(row)] }
  }, redraw() {} })
  await c.load('scout')
  const request = c.toggle(c.st.rows[0])
  assert.equal(c.st.rows[0].busy, true)
  await c.load('scout')
  finish(); await request
  assert.equal(c.st.rows[0].busy, false)
  c.st.lastFetch = 0
  await c.tick(true)
  assert.equal(c.st.rows[0].state, 'off')
})
test('delete stays available through relay/Activity; query allowlist and disallowed methods stay narrow', async (t) => {
  const { api, broker, app } = await setup(t)
  await api('DELETE', '/schedules/scout/prepare-notes', { reason: 'not needed' })
  assert.equal(JSON.parse(broker.seen.at(-1).body).reason, 'not needed')
  await fetch(`${app.url}/api/schedules?agent=alfred&secret=discard`)
  assert.equal(broker.seen.at(-1).search, '?agent=alfred')
  const count = broker.seen.length
  for (const [method, path] of [['DELETE', '/schedules/scout'], ['PATCH', '/schedules/scout/reflection/run'], ['GET', '/activity/change-scout/undo']]) assert.equal((await fetch(`${app.url}/api${path}`, { method })).status, 404)
  assert.equal(broker.seen.length, count)
})
