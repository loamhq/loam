import { setOwner } from '../public/owner.js'
setOwner({ id: 'you', name: 'Sam', accepts: ['you', 'owner', 'person'] })
import { formatTime, formatDate } from '../public/time.js'
import test from 'node:test'
import assert from 'node:assert/strict'
import { fakeBroker, appAgainst, closing, TOKEN } from './harness.js'
import { installDom } from './fakedom.js'
import { all, text } from './browser.js'
import { h } from '../public/ui.js'
import { createActivity, readActivity, elapsed, ACTIVITY_KINDS, ACTIVITY_FILTERS, activityTime, activityDay, activityWords } from '../public/activity.js'
import { scheduleChange, actorName } from '../public/schedule-change.js'
import { createSchedules } from '../public/schedules.js'
import { activityFixture } from '../tools/activity-fixture.js'
import { schedule, change, TS } from '../tools/schedule-contract-fixtures.js'

installDom()
const agents = [{ id: 'test-agent', name: 'Test agent' }, { id: 'review-agent', name: 'Reviewer' }]
function apiFor(fixture) {
  return async (method, path, body) => {
    const url = new URL(`/v1${path}`, 'http://fixture')
    const answer = fixture.handle(method, decodeURIComponent(url.pathname), url, body) ?? fixture.schedules.handleAnswer(method, decodeURIComponent(url.pathname), url, body)
    if (!answer) throw new Error('No fixture route')
    if (answer.status >= 400) throw Object.assign(new Error(answer.body.error), { status: answer.status })
    return answer.body
  }
}
function controller(fixture = activityFixture(agents), over = {}) {
  return createActivity({ api: apiFor(fixture), redraw() {}, agents: () => agents,
    avatar: () => h('span', { text: 'Avatar' }), openRef() {}, ...over })
}
test('relay allowlists feed, undo and cancel; bearer, queries, 400 and 404 preserved', async (t) => {
  const fixture = activityFixture(agents)
  const routes = new Proxy({}, { get: () => (req, res, url, raw) => {
    const answer = fixture.handle(req.method, decodeURIComponent(url.pathname), url, raw ? JSON.parse(raw) : {})
    res.writeHead(answer.status, { 'content-type': 'application/json' })
    res.end(JSON.stringify(answer.body))
  } })
  const broker = await fakeBroker(routes), app = await appAgainst(broker)
  t.after(closing(app, broker))
  const call = (method, path, body) => fetch(`${app.url}/api${path}`, { method, headers: { 'content-type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
  const listing = await call('GET', '/activity?agent=test-agent,review-agent&kind=change,refused&limit=2&offset=0&secret=discard')
  assert.equal(listing.status, 200)
  const body = await listing.json()
  assert.equal(body.items.length, 2)
  assert.equal(body.jobs.slots.length, 3)
  assert.equal(body.jobs.queue.length, 1)
  assert.equal(body.jobs_error, null)
  assert.equal(broker.seen[0].search, '?agent=test-agent%2Creview-agent&kind=change%2Crefused&limit=2&offset=0')
  assert.equal(broker.seen[0].auth, `Bearer ${TOKEN}`)
  assert.equal((await call('POST', '/activity/change-refused/undo', {})).status, 400)
  assert.equal((await call('POST', '/activity/nope/undo', {})).status, 404)
  const forwarded = broker.seen.length
  assert.equal((await call('POST', '/activity/seen', { all: true })).status, 404)
  assert.equal(broker.seen.length, forwarded)
  const id = fixture.queue[0].job
  assert.equal((await call('POST', `/jobs/${id}/cancel`, {})).status, 200)
  assert.equal(fixture.queue.length, 0)
  assert.equal((await call('POST', `/jobs/${id}/cancel`, {})).status, 400)
  assert.equal((await call('POST', '/jobs/abc/cancel', {})).status, 404)
  assert.equal((await call('GET', `/jobs/${id}/cancel`)).status, 404)
  assert.equal((await call('GET', '/activity?kind=wake')).status, 400)
})
test('compact feed names the agent, shows two lines, failure reasons without seen state, and opens receipts', async () => {
  const fixture = activityFixture(agents), opened = [], feed = controller(fixture, { openRef: (ref) => opened.push(ref) })
  await feed.load()
  const view = feed.view(), entries = all(view, 'activity-item')
  assert.deepEqual(ACTIVITY_KINDS.map(([k]) => k), ['change', 'refused', 'fire', 'run', 'job'])
  assert.deepEqual(all(view, 'segmented')[0].childNodes.map(text), ['All', 'Chats', 'Runs', 'Jobs', 'Changes'])
  for (const row of entries) {
    assert.equal(all(row, 'activity-summary').length, 1)
    assert.equal(all(row, 'activity-time').length, 1)
    assert.equal(all(row, 'activity-seen').length, 0)
    assert.equal(all(row, 'activity-link').length, 0)
    assert.equal(all(row, 'schedule-change').length, 0)
    assert.doesNotMatch(text(row), /Loam|Scheduled runs|Started|Finished|Unseen/)
  }
  assert.equal(all(view, 'activity-unread').length, 0)
  assert.equal('unread' in feed.st, false)
  const landed = fixture.items.find((i) => i.id === 'job-landed')
  feed.open(landed)
  assert.deepEqual(opened, ['Receipts/search.md'])
  // Activity carries no separate seen state.
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal('seen' in fixture.items.find((i) => i.id === landed.id), false)
  const refused = entries.find((row) => text(row).includes('reflection') && all(row, 'activity-action').some(node => text(node) === 'refused'))
  assert.match(text(refused), /the nightly reflection/)
  assert.ok(all(view, 'activity-failed').length >= 5)
  const change = fixture.items[0]
  feed.open(change)
  assert.match(text(feed.dialogNodes()[0].__keep), /Sundays at 05:00 PT.*→.*Sundays at 06:00 PT/)
  feed.closeDetail()
  feed.open(fixture.items.find((i) => i.id === 'scheduled-ran'))
  assert.match(text(feed.dialogNodes()[0].__keep), /No receipt or output was supplied/)
})
test('time spans use completion for jobs, never invent an end for fires, and group local days', () => {
  const start = '2026-10-15T11:00:00', end = '2026-10-15T11:05:00'
  assert.equal(activityTime({ kind: 'fire', started: start, finished: end, ts: end }), `${formatTime(start)}–${formatTime(end)} · 5 min`)
  assert.equal(activityTime({ kind: 'job', started: start, ts: end }), `${formatTime(start)}–${formatTime(end)} · 5 min`)
  assert.equal(activityTime({ kind: 'fire', started: start, finished: null, ts: end }), formatTime(end))
  assert.equal(activityTime({ kind: 'job', started: end, ts: start }), formatTime(start))
  assert.equal(activityTime({ started: null, finished: null, ts: null }), 'Time unknown')
  assert.equal(activityDay(end, new Date('2026-10-15T23:00:00')), 'Today')
  assert.equal(activityDay(end, new Date('2026-10-16T00:01:00')), 'Yesterday')
  assert.equal(activityDay('2026-10-13T11:00:00', new Date(end)), formatDate('2026-10-13T11:00:00', { weekday: 'short', day: 'numeric' }))
  assert.deepEqual(activityWords({ kind: 'fire', record: 'reflection', outcome: 'failed', stage: 'died_mid_run' }),
    { summary: 'reflection failed', subject: 'reflection', action: 'failed', failed: true, reason: null })
  assert.equal(activityWords({ kind: 'fire', outcome: 'failed', produced: { line: { error: 'Permission refused' } } }).reason, 'Permission refused')
  assert.equal(activityWords({ kind: 'job', title: 'A change', outcome: 'completed', landing: 'landed_earlier' }).summary, 'A change landed')
})
test('kind chips filter events and threads share the same history row in main and scoped feeds', async () => {
  const fixture = activityFixture(agents), opened = []
  const chat = { id: 'chat-one', title: 'A thread', last_activity: TS }
  const feed = controller(fixture, { chats: () => ({ pinned: [chat], recent: [{ ...chat, id: 'chat-two' }] }),
    chatFace: () => h('span', { class: 'shared-face' }), chatInfo: () => ({ secondary: 'A reply' }), openChat: row => opened.push(row.id) })
  await feed.load()
  for (const embedded of [false, true]) {
    const view = feed.view({ embedded })
    assert.equal(all(view, 'shared-face').length, 2)
    assert.equal(all(view, 'activity-trailing-time').length, 2)
    assert.equal(all(view, 'tt-row').length, 0)
  }
  await feed.filter('kind', ACTIVITY_FILTERS[2][0])
  assert.match(fixture.calls.at(-1).query, /kind=fire%2Crun/)
  assert.ok(feed.st.items.every(i => ['fire', 'run'].includes(i.kind)))
  assert.equal(all(feed.view(), 'shared-face').length, 0)
  await feed.filter('kind', 'chat')
  const rows = all(feed.view(), 'activity-item')
  assert.equal(rows.length, 2)
  rows[0].fire('keydown', { key: 'Enter', preventDefault() {} })
  assert.deepEqual(opened, ['chat-one'])
})
test('scoped history never marks events or chats, even when opening a receipt', async () => {
  const fixture = activityFixture(agents), feed = controller(fixture, { scoped: true })
  feed.st.agent = agents[0].id
  await feed.load()
  for (const item of feed.st.items) feed.open(item)
  assert.equal(fixture.calls.some(call => call.method === 'POST'), false)
  assert.equal(all(feed.view({ embedded: true }), 'activity-unread').length, 0)
  assert.doesNotMatch(text(feed.view()), /Mark all seen/)
})
test('launcher failure leaves the feed readable and shows its sentence; landing and non-job slots render', async () => {
  const fixture = activityFixture(agents), feed = controller(fixture)
  fixture.jobsError = "the launcher's status could not be read: unavailable"
  await feed.load()
  assert.equal(feed.st.now, null)
  assert.ok(feed.st.items.length)
  assert.match(text(feed.view()), /the launcher's status could not be read: unavailable/)
  fixture.jobsError = null
  fixture.slots.push({ slot: 4, free: false, since: TS, kind: 'maintenance' })
  fixture.landing.push({ job: '20991231-000002-abcd1234', project: 'demo', title: 'Landing the search', briefed_by: 'engineer' })
  await feed.load()
  assert.match(text(feed.view()), /Landing the search/)
  assert.doesNotMatch(text(feed.view()), /Slot 2 · Available/)
})
test('schedule reading changes do not alter Activity presentation', async () => {
  const fixture = activityFixture(agents), feed = controller(fixture)
  await feed.load()
  const before = text(feed.view())
  const schedules = createSchedules({ api: apiFor(fixture), redraw() {} })
  await schedules.load(agents[0].id)
  await schedules.seeFile({ ...schedules.st.rows[1].file, authority: 'schedule' })
  await feed.load()
  assert.equal(text(feed.view()), before)
  assert.equal('unread' in feed.st, false)
})
test('Undo refusal remains in the opened detail; successful Undo is a new change with undoes', async () => {
  const fixture = activityFixture(agents), feed = controller(fixture)
  await feed.load()
  feed.open(feed.st.items.find((i) => i.id === 'change-refused'))
  await feed.undo(feed.st.items.find((i) => i.id === 'change-refused'))
  assert.match(text(feed.dialogNodes()[0].__keep), /undo the later change first/)
  await feed.undo(feed.st.items.find((i) => i.id === `change-${agents[1].id}`))
  const event = feed.st.items.find((i) => i.undoes)
  assert.equal(event.undoes, `change-${agents[1].id}`)
  assert.equal(event.actor, 'owner')
  assert.equal(fixture.schedules.rows.get(`${agents[1].id}/weekly-review`).schedule, 'Sundays at 05:00 PT')
})
test('cancel requires confirmation, uses job (not id), and prints broker said; running process stays', async () => {
  const fixture = activityFixture(agents), feed = controller(fixture)
  await feed.load()
  const before = fixture.calls.length
  all(feed.view(), 'activity-cancel')[0].fire('click', { currentTarget: null })
  assert.equal(fixture.calls.length, before)
  assert.equal(feed.st.confirm.job.job, fixture.slots[0].job)
  assert.match(text(feed.dialogNodes()[0].__keep), /not killed/)
  feed.dismiss()
  all(feed.view(), 'activity-cancel')[1].fire('click', { currentTarget: null })
  await feed.cancel()
  assert.equal(fixture.queue.length, 0)
  assert.match(text(feed.view()), /it had not started and never will/)
  all(feed.view(), 'activity-cancel')[0].fire('click', { currentTarget: null })
  await feed.cancel()
  assert.equal(fixture.slots[0].free, false)
  all(feed.view(), 'activity-cancel')[0].fire('click', { currentTarget: null })
  await feed.cancel()
  assert.match(feed.st.confirm.error, /already has a verdict/)
})
test('pagination uses next_offset, respects 200 max and retains loaded history on heartbeat', async () => {
  const fixture = activityFixture(agents)
  for (let i = 0; i < 225; i++) fixture.items.push({ ...fixture.items[1], id: `older-${i}`, ts: new Date(Date.parse(TS) - (100 + i) * 60000).toISOString() })
  const feed = controller(fixture)
  await feed.load()
  assert.equal(feed.st.items.length, 30)
  await feed.load({ more: true })
  assert.equal(feed.st.items.length, 60)
  assert.match(fixture.calls.at(-1).query, /offset=30/)
  while (feed.st.next !== null) await feed.load({ more: true })
  const count = feed.st.items.length
  assert.equal(count, fixture.all().length)
  feed.st.lastFetch = 0
  await feed.tick(true)
  assert.equal(feed.st.items.length, count)
  assert.ok(fixture.calls.filter((c) => c.method === 'GET').every((c) => Number(new URLSearchParams(c.query).get('limit')) <= 200))
})
test('live refresh shows new fire, queue and badge without marking them; stale filters cannot overwrite', async () => {
  const fixture = activityFixture(agents), feed = controller(fixture)
  await feed.load()
  const count = feed.st.items.length
  fixture.schedules.fire(agents[0].id, 'prepare-notes', 'missed')
  fixture.queue.length = 0
  feed.st.lastFetch = 0
  await feed.tick(true)
  assert.equal(feed.st.items.length, count + 1)
  assert.equal(feed.st.now.queue.length, 0)
  assert.equal(fixture.calls.some((c) => c.path.endsWith('/seen')), false)
  const pending = []
  const slow = controller(fixture, { api: () => new Promise((r) => pending.push(r)) })
  const first = slow.load()
  slow.st.agent = agents[1].id
  const second = slow.load()
  const answer = fixture.handle('GET', '/v1/activity', new URL('http://fixture/v1/activity?agent=review-agent')).body
  pending[1](answer); await second
  pending[0]({ ...answer, items: [fixture.items[0]] }); await first
  assert.ok(slow.st.items.every((i) => i.agent === agents[1].id))
})
test('diff uses raw strings as text; actor names and unknown values remain honest', () => {
  const node = scheduleChange(change('a', 'review', { diff: { header: { body: ['<script>x</script>', 'after'] }, body_changed: true } }), { undo() {} })
  assert.match(text(node), /<script>x<\/script>/)
  assert.match(text(node), /Instruction changed/)
  assert.match(text(node), /Undo/)
  assert.equal(actorName('owner'), 'you')
  assert.equal(actorName('engineer'), 'the engineer')
  assert.equal(actorName('loam'), 'Loam')
  assert.equal(actorName('iris', [{ id: 'iris', name: 'Iris' }]), 'Iris')
  assert.throws(() => readActivity({ items: [] }), /format/)
  assert.equal('unread' in readActivity({ items: [], jobs: null, jobs_error: 'Unavailable', next_offset: null }), false)
  assert.equal(elapsed(null), 'Time unknown')
})
test('missing host, malformed response and empty filter differ', async () => {
  const feed = controller(undefined, { api: async () => { throw Object.assign(new Error('no route'), { status: 404 }) } })
  await feed.load()
  assert.match(text(feed.view()), /not available from this host yet/)
  const empty = controller()
  empty.st.agent = 'nobody'
  await empty.load()
  assert.match(text(empty.view()), /No activity matches these filters/)
})
