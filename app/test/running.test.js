import test from 'node:test'
import assert from 'node:assert/strict'
import { liveSession, readLiveSession, readBox, groupSessions, sessionLine } from '../public/running-record.js'
import { createRunning as controller } from '../public/running.js'
// These compatibility cases deliberately model a broker before the live route.
const createRunning = (options) => controller({ ...options, api: async (method, path, body) => {
  if (path === '/sessions/live') throw Object.assign(new Error('no route'), { status: 404 })
  return options.api(method, path, body)
} })
const record = (more = {}) => ({ session_id: 'one', agent: 'sprout', title: 'A thought', live: true, state: 'running', status: 'done', ...more })
test('residency is the host fact; ended processes and unknown memory are not made up', () => {
  assert.equal(liveSession(record({ live: false })), null)
  assert.equal(liveSession(record({ live: undefined, state: 'exited' })), null)
  assert.equal(liveSession(record()).memoryMb, null)
  assert.equal(liveSession(record({ live: undefined })).state, 'idle')
})
test('permissions outrank working and background, and background never becomes safe idle', () => {
  const waiting = liveSession(record({ status: 'working', background_tasks: 1, pending_permissions: [{}] }))
  const background = liveSession(record({ background_tasks: 1 }))
  const working = liveSession(record({ status: 'working' }))
  assert.equal(waiting.state, 'waiting'); assert.equal(background.state, 'background')
  assert.deepEqual(groupSessions([waiting, liveSession(record()), working, background]).map((g) => [g.title, g.rows.length]), [['Working', 2], ['Waiting on you', 1], ['Idle', 1]])
})
test('a CLI thread id must never link to a Loam thread; archived harnesses await mapping', () => {
  const row = liveSession(record({ thread_id: 'cli-thread', archived: true }))
  assert.equal(row.threadId, undefined); assert.equal(row.mapped, false)
  assert.equal(liveSession(record()).mapped, true)
})
test('unknown ages and idle deadlines do not draw invented numbers', () => {
  assert.equal(sessionLine({ state: 'idle' }), 'Idle')
  assert.equal(sessionLine({ state: 'idle', idleSince: '2026-01-01T00:00:00Z', idleTimeoutSeconds: 600 }, Date.parse('2026-01-01T00:08:00Z')), 'Idle · 8 min, steps out at 10 min')
})
function setup(current) {
  const calls = [], panel = []
  const running = createRunning({ agents: () => [{ id: 'sprout', name: 'Sprout' }], redraw() {}, openPanel: () => panel.push(true), openConversation() {},
    api: async (method, path) => { calls.push([method, path]); if (path.startsWith('/sessions?')) return { sessions: [current] }; if (method === 'DELETE') { current = { ...current, live: false }; return {} }; return current },
  })
  return { running, calls, panel }
}
test('an idle session that starts working before End asks first and makes no DELETE', async () => {
  const { running, calls, panel } = setup(record({ status: 'working' }))
  await running.end(liveSession(record()))
  assert.deepEqual(calls, [['GET', '/sessions/one']]); assert.equal(running.st.confirm, 'one'); assert.equal(panel.length, 1)
  running.stop()
})
test('idle ending preserves the durable conversation and refreshes residency; active end needs explicit confirmation', async () => {
  const { running, calls } = setup(record())
  await running.end(liveSession(record()))
  assert.ok(calls.some(([method, path]) => method === 'DELETE' && path === '/sessions/one'))
  assert.equal(running.rows().length, 0); assert.match(running.st.toast, /Sprout stepped out/)
  assert.ok(calls.every(([, path]) => !path.includes('/threads/')))
  running.stop()
  const active = setup(record({ status: 'needs_input' }))
  await active.running.end(liveSession(record({ status: 'needs_input' })), true)
  assert.ok(active.calls.some(([method]) => method === 'DELETE')); active.running.stop()
})
test('one shared snapshot stream feeds classic subscribers without a reconnect cursor', async () => {
  let options, starts = 0, stops = 0
  const running = createRunning({ api: async () => ({ sessions: [] }), redraw() {}, agents: () => [], linkFactory: (opts) => { options = opts; return { start: async () => { starts++ }, stop: () => stops++, wake() {} } } })
  const seen = [], subscription = running.subscribe((e) => seen.push(e))
  running.start(); await running.load()
  options.onEvent({ type: 'broker.session', ...record(), record_seq: 400 })
  assert.equal(running.rows().length, 1); assert.equal(seen.length, 1)
  subscription.stop(); options.onEvent({ type: 'broker.session', ...record({ live: false }) })
  assert.equal(seen.length, 1); assert.equal(stops, 0); assert.equal(starts, 1)
  assert.equal(options.href(), '/api/sessions/events?archived=1'); running.stop()
})
test('End all idle skips a session that has begun working and never ends background work', async () => {
  const held = [record(), record({ session_id: 'two' }), record({ session_id: 'background', background_tasks: 1 })], calls = []
  const running = createRunning({ agents: () => [], redraw() {}, openPanel() {}, api: async (method, path) => {
    calls.push([method, path])
    if (path.includes('?')) return { sessions: held }
    const row = held.find((r) => path.endsWith('/' + r.session_id))
    if (row.session_id === 'two') row.status = 'working'
    if (method === 'DELETE') row.live = false
    return row
  } })
  await running.load(); await running.endIdle()
  assert.deepEqual(calls.filter(([method]) => method === 'DELETE'), [['DELETE', '/sessions/one']])
  assert.equal(held[1].live, true); assert.equal(held[2].live, true); running.stop()
})
test('a refused end remains visible through a successful refresh and unrelated snapshots', async () => {
  let opened = 0
  const running = createRunning({ agents: () => [], redraw() {}, openPanel: () => opened++, api: async (method, path) => {
    if (method === 'DELETE') throw new Error('The session could not be ended.')
    return path.includes('?') ? { sessions: [record()] } : record()
  } })
  await running.end(liveSession(record()))
  await running.load(); running.receive({ type: 'broker.session', ...record({ session_id: 'other' }) })
  assert.equal(running.st.actionError, 'The session could not be ended.'); assert.equal(opened, 1); running.stop()
})
test('an old JSON read cannot restore a session or resident count after a new stream snapshot', async () => {
  let resolve
  const running = createRunning({ agents: () => [], redraw() {}, api: () => new Promise((r) => resolve = r) })
  const pending = running.load()
  await Promise.resolve(); await Promise.resolve()
  running.receive({ type: 'broker.session', ...record({ live: false }) })
  running.receive({ type: 'broker.residency', live_count: 0 })
  resolve({ sessions: [record()], live_count: 1 }); await pending
  assert.equal(running.rows().length, 0); assert.equal(running.st.count, 0); running.stop()
})
const live = (more = {}) => ({ id: 'one', agent: 'sprout', engine: 'codex', kind: 'thread', thread_id: 'garden', thread_title: 'A garden', title: null, state: 'idle', rss_mb: 200, ending: { loses_work: false, text: 'resumes later exactly where it was' }, ...more })
const box = { ram_total_mb: 32768, ram_used_mb: 18432, can_start: 20, sessions: { live: 1 }, units: { engineer_jobs: { count: 1, mb: 700 }, wakes: { count: 2, mb: 80 } } }
test('the exact live route supplies thread identity, asking state, memory, and loss rather than CLI inference', () => {
  const row = readLiveSession(live({ state: 'asking', ending: { loses_work: true } }))
  assert.equal(row.threadId, 'garden'); assert.equal(row.title, 'A garden'); assert.equal(row.memoryMb, 200)
  assert.equal(row.state, 'waiting'); assert.equal(row.losesWork, true); assert.equal(row.mapped, true)
  assert.equal(readLiveSession(live({ rss_mb: null })).memoryMb, null)
  assert.equal(readLiveSession({ kind: 'engineer-job', id: 'job' }), null)
  assert.deepEqual(readBox(box), { usedMb: 18432, totalMb: 32768, room: 20, jobs: { count: 1, memoryMb: 700 }, wakes: { count: 2, memoryMb: 80 } })
})
test('rich live snapshots replace the whole resident set and open the exact thread member', async () => {
  const running = controller({ redraw() {}, agents: () => [], api: async () => ({ sessions: [live()], box }) })
  await running.load()
  assert.equal(running.forMember('garden', 'sprout').id, 'one')
  assert.equal(running.forMember('other', 'sprout'), undefined)
  assert.equal(running.st.count, 1); assert.equal(running.st.memory.room, 20)
  running.receive({ type: 'sessions.live', sessions: [], box: { ...box, sessions: { live: 0 } } })
  assert.equal(running.rows().length, 0); assert.equal(running.st.count, 0); running.stop()
})
test('rich End all idle uses the host atomic route and never loops through live ids', async () => {
  const calls = []
  const running = controller({ redraw() {}, agents: () => [], api: async (method, path) => {
    calls.push([method, path]); return method === 'POST' ? { ended: [{ id: 'one', agent: 'sprout' }], kept: [] } : { sessions: [live()], box }
  } })
  await running.load(); await running.endIdle()
  assert.deepEqual(calls.filter(([method]) => method !== 'GET'), [['POST', '/sessions/end-idle']])
  assert.match(running.st.toast, /1 agent stepped out/); running.stop()
})
test('rich stream is always on; the classic record stream exists only while subscribed', async () => {
  const connections = []
  const running = controller({ redraw() {}, agents: () => [], api: async () => ({ sessions: [], box }), linkFactory: (opts) => {
    const connection = { path: opts.href(), starts: 0, stops: 0 }; connections.push(connection)
    return { start: async () => connection.starts++, stop: () => connection.stops++, wake() {} }
  } })
  running.start(); await running.load(); await running.load()
  assert.deepEqual(connections.map((c) => [c.path, c.starts]), [['/api/sessions/live/events', 1]])
  const classic = running.subscribe(() => {})
  assert.equal(connections[1].path, '/api/sessions/events?archived=1')
  classic.stop(); assert.equal(connections[1].stops, 1); assert.equal(connections[0].stops, 0)
  running.stop()
})
