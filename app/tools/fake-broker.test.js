// The fake broker checks a question's answer as the real one does: a malformed
// answer is a `400` with a sentence naming the field, nothing is resolved, and
// the question is still pending to be answered again. The page's own tests use
// a recorded broker of their own; this one runs the tool itself, so what a
// person sees running it by hand is what is tested.

import test from 'node:test'
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import path from 'node:path'

const TOOL = path.join(import.meta.dirname, 'fake-broker.js')

const broker = spawn(process.execPath, [TOOL], { env: { ...process.env, PORT: '0', BROKER_TOKEN: 'dev', FAKE_PACE: '0.02', FAKE_WAIT_MS: '1000' }, stdio: ['ignore', 'pipe', 'inherit'] })
const base = await new Promise((resolve, reject) => {
  let out = ''
  broker.stdout.on('data', (chunk) => {
    out += chunk
    const at = /listening on (http:\/\/[^\s]+)/.exec(out)
    if (at) resolve(at[1])
  })
  broker.on('exit', (code) => reject(new Error(`fake-broker exited ${code}`)))
})
broker.stdout.resume()
test.after(() => broker.kill())

const call = async (method, p, body) => {
  const res = await fetch(`${base}${p}`, {
    method,
    headers: { authorization: 'Bearer dev', ...(body ? { 'content-type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  })
  return { status: res.status, body: await res.json() }
}

test('Activity and Schedule coexist in the demo and share Undo without intercepting each other', async () => {
  const feed = await call('GET', '/v1/activity?limit=30&offset=0')
  assert.equal(feed.status, 200)
  assert.ok(feed.body.items.length >= 10)
  assert.deepEqual([...new Set(feed.body.items.map((item) => item.kind))].sort(), ['change', 'fire', 'job', 'refused', 'run'])
  assert.equal(feed.body.jobs.slots[0].free, false)
  assert.equal(feed.body.jobs.slots[1].free, true)
  const schedules = await call('GET', '/v1/schedules?agent=scout')
  assert.equal(schedules.status, 200)
  const changed = schedules.body.schedules.find((row) => row.unseen.length)
  assert.ok(changed)
  const restored = await call('POST', `/v1/activity/${changed.last_change.id}/undo`, {})
  assert.equal(restored.status, 200)
  assert.equal(restored.body.schedule.schedule, 'Sundays at 05:00 PT')
  assert.equal(restored.body.event.undoes, changed.last_change.id)
  const refused = await call('POST', '/v1/activity/change-refused/undo', {})
  assert.equal(refused.status, 400)
  assert.match(refused.body.error, /undo the later change first/)
  const updated = await call('GET', '/v1/activity')
  assert.ok(updated.body.items.some((item) => item.undoes === changed.last_change.id))
  assert.equal((await call('POST', '/v1/activity/seen', { ids: [changed.last_change.id] })).status, 404)
  await call('POST', `/v1/schedules/scout/${changed.name}/seen`, {})
  const seen = await call('GET', '/v1/schedules?agent=scout')
  assert.equal(seen.body.schedules.find((row) => row.name === changed.name).unseen.length, 0)
  const repeated = await call('POST', `/v1/activity/${changed.last_change.id}/undo`, {})
  assert.equal(repeated.status, 400)
  assert.match(repeated.body.error, /undo the later change first/)
})

// What the session is asking, as the page learns it: the record lists the ids
// still pending, as the host's does, and the requests themselves are the
// logged `broker.permission_request` events.
async function asking(id) {
  const { body: record } = await call('GET', `/v1/sessions/${id}`)
  if (record.pending_permissions.length === 0) return []
  assert.ok(record.pending_permissions.every((x) => typeof x === 'string'), 'the record lists request ids')
  const res = await fetch(`${base}/v1/sessions/${id}/events?from=0`, { headers: { authorization: 'Bearer dev' } })
  const reader = res.body.getReader()
  let text = ''
  const until = Date.now() + 2000
  let found = []
  while (Date.now() < until && found.length < record.pending_permissions.length) {
    const next = await Promise.race([reader.read(), new Promise((r) => setTimeout(() => r(null), until - Date.now()))])
    if (!next || next.done) break
    text += new TextDecoder().decode(next.value)
    const events = text.slice(0, text.lastIndexOf('\n')).split('\n').filter((l) => l.startsWith('data: ')).map((l) => JSON.parse(l.slice(6)))
    found = events.filter((e) => e.type === 'broker.permission_request' && record.pending_permissions.includes(e.request_id))
  }
  await reader.cancel()
  return found
}

async function asked(shape) {
  const { body: started } = await call('POST', '/v1/sessions', { agent: 'scout' })
  const id = started.session_id
  await call('POST', `/v1/sessions/${id}/messages`, { text: `/ask ${shape}` })
  for (let i = 0; i < 100; i++) {
    const q = (await asking(id)).find((x) => x.kind === 'question')
    if (q) return { id, q }
    await new Promise((r) => setTimeout(r, 50))
  }
  throw new Error(`no question raised for /ask ${shape}`)
}

const pending = async (id, requestId) =>
  (await call('GET', `/v1/sessions/${id}`)).body.pending_permissions.includes(requestId)

test('a Claude question is keyed by its full question text', async () => {
  const { id, q } = await asked('single')
  assert.equal(q.questions[0].id, q.questions[0].question)
  await call('DELETE', `/v1/sessions/${id}`)
})

test('the form shape: a message, a Yes/No boolean and an optional field, which may be left out', async () => {
  const { id, q } = await asked('form')
  assert.equal(q.message, 'The release tool wants two things before it publishes.')
  assert.deepEqual(q.questions[0].options.map((o) => o.label), ['Yes', 'No'])
  assert.equal(q.questions[1].required, false)

  // A required question with no answer: refused, and still pending.
  const missing = await call('POST', `/v1/sessions/${id}/permissions/${q.request_id}`, { behavior: 'allow', answers: { note: ['soon'] } })
  assert.equal(missing.status, 400)
  assert.match(missing.body.error, /answers\.publish is required/)
  assert.ok(await pending(id, q.request_id), 'a refused answer resolved the question')

  // The optional one left out goes through.
  const ok = await call('POST', `/v1/sessions/${id}/permissions/${q.request_id}`, { behavior: 'allow', answers: { publish: ['Yes'] } })
  assert.equal(ok.status, 200)
  assert.ok(!(await pending(id, q.request_id)))
  await call('DELETE', `/v1/sessions/${id}`)
})

test('two typed values on one question are refused, and the question can be answered again', async () => {
  const { id, q } = await asked('three')
  const twice = await call('POST', `/v1/sessions/${id}/permissions/${q.request_id}`, {
    behavior: 'allow',
    answers: { q1: ['Stable'], q2: ['Fixes', 'Smoke test', 'Staging'] },
  })
  assert.equal(twice.status, 400)
  assert.match(twice.body.error, /answers\.q2 carries 2 typed values/)
  assert.ok(await pending(id, q.request_id))
  const ok = await call('POST', `/v1/sessions/${id}/permissions/${q.request_id}`, {
    behavior: 'allow',
    answers: { q1: ['Stable'], q2: ['Fixes', 'Smoke test'] },
  })
  assert.equal(ok.status, 200)
  const { body: log } = await call('GET', '/v1/fake/answers')
  assert.equal(log.answered.filter((a) => a.request_id === q.request_id && a.session_id === id).length, 1, 'a refused answer was kept as an answer')
  await call('DELETE', `/v1/sessions/${id}`)
})

// A turn's result lands with one background task still going: the session is
// `waiting`, with the count, and `done` when that task comes in.
test('a turn ends waiting on one background task, then is done', async () => {
  // A new session plays the first recorded turn, which pauses on a permission;
  // answering it lets the turn run to its result.
  const { body: started } = await call('POST', '/v1/sessions', { agent: 'scout' })
  const id = started.session_id
  const statuses = []
  let waiting = null
  let done = null
  for (let i = 0; i < 300 && !done; i++) {
    const { body: record } = await call('GET', `/v1/sessions/${id}`)
    if (statuses.at(-1) !== record.status) statuses.push(record.status)
    const ask = record.pending_permissions.length > 0 ? (await asking(id)).find((x) => x.kind !== 'question') : null
    if (ask) await call('POST', `/v1/sessions/${id}/permissions/${ask.request_id}`, { behavior: 'allow' })
    if (record.status === 'waiting') waiting ??= record
    if (waiting && record.status === 'done') done = record
    await new Promise((r) => setTimeout(r, 25))
  }
  assert.ok(waiting, `never waiting: ${statuses.join(' → ')}`)
  assert.equal(waiting.background_tasks, 1)
  assert.ok(done, `never done after waiting: ${statuses.join(' → ')}`)
  assert.equal(done.background_tasks, 0)
  await call('DELETE', `/v1/sessions/${id}`)
})

test('/skill raises a Skill approval naming the skill', async () => {
  const { body: started } = await call('POST', '/v1/sessions', { agent: 'scout' })
  const id = started.session_id
  await call('POST', `/v1/sessions/${id}/messages`, { text: '/skill' })
  let asked = null
  for (let i = 0; i < 100 && !asked; i++) {
    asked = (await asking(id)).find((x) => x.tool_name === 'Skill') ?? null
    if (!asked) await new Promise((r) => setTimeout(r, 50))
  }
  assert.ok(asked, 'no Skill approval raised')
  assert.equal(asked.kind, 'approval')
  assert.equal(asked.input.skill, 'pdf')
  await call('DELETE', `/v1/sessions/${id}`)
})

// The empty chat's 400 is not caused here: a new session here has events in it
// before a request can reach it. test/select.test.js's broker answers it.
test('a chat is marked unread with the flag, and only with it', async () => {
  const { body: started } = await call('POST', '/v1/sessions', { agent: 'scout' })
  const id = started.session_id
  await call('POST', `/v1/sessions/${id}/messages`, { text: 'hello' })
  let record
  for (let i = 0; i < 100; i++) {
    record = (await call('GET', `/v1/sessions/${id}`)).body
    if (record.record_seq > 1) break
    await new Promise((resolve) => setTimeout(resolve, 20))
  }
  const end = record.record_seq
  assert.equal((await call('PATCH', `/v1/sessions/${id}`, { seen_seq: end })).body.seen_seq, end)
  // Lower without the flag: ignored, as ever.
  assert.equal((await call('PATCH', `/v1/sessions/${id}`, { seen_seq: end - 1 })).body.seen_seq, end)
  // With it, lowered to what was sent; and the flag alone is one short of the end.
  assert.equal((await call('PATCH', `/v1/sessions/${id}`, { seen_seq: end - 1, unread: true })).body.seen_seq, end - 1)
  await call('PATCH', `/v1/sessions/${id}`, { seen_seq: end })
  const alone = (await call('PATCH', `/v1/sessions/${id}`, { unread: true })).body
  assert.equal(alone.seen_seq, alone.record_seq - 1)
})

// The log of an ended session, whole: the replay closes where the log ends.
const logOf = async (id) => {
  const res = await fetch(`${base}/v1/sessions/${id}/events?from=0`, { headers: { authorization: 'Bearer dev' } })
  return (await res.text()).split('\n').filter((l) => l.startsWith('data: ')).map((l) => JSON.parse(l.slice(6)))
}

async function several() {
  const { body: started } = await call('POST', '/v1/sessions', { agent: 'scout' })
  const id = started.session_id
  await call('POST', `/v1/sessions/${id}/messages`, { text: '/ask several' })
  for (let i = 0; i < 100; i++) {
    const { body: record } = await call('GET', `/v1/sessions/${id}`)
    if (record.pending_permissions.length === 3) return { id, asked: await asking(id) }
    await new Promise((r) => setTimeout(r, 50))
  }
  throw new Error('/ask several raised fewer than three')
}

test('/ask several raises three approvals at once, each proposing a session rule', async () => {
  const { id, asked } = await several()
  assert.deepEqual(asked.map((a) => a.tool_name), ['Read', 'Grep', 'Glob'])
  for (const a of asked) assert.equal(a.permission_suggestions[0].destination, 'session')
  await call('DELETE', `/v1/sessions/${id}`)
})

test('a rule for any destination but session is the real broker’s 400, and the request stays answerable', async () => {
  const { id, asked } = await several()
  const mixed = asked[1].permission_suggestions
  assert.deepEqual(mixed.map((s) => s.destination), ['session', 'localSettings'])
  const refused = await call('POST', `/v1/sessions/${id}/permissions/${asked[1].request_id}`, { behavior: 'allow', updatedPermissions: mixed })
  assert.equal(refused.status, 400)
  assert.equal(refused.body.error, "`updatedPermissions` may only carry suggestions whose destination is 'session'")
  assert.equal((await call('POST', `/v1/sessions/${id}/permissions/${asked[1].request_id}`, { behavior: 'deny', updatedPermissions: [mixed[0]] })).status, 400, 'rules on a deny')
  assert.equal(await pending(id, asked[1].request_id), true)
  assert.equal((await call('POST', `/v1/sessions/${id}/permissions/${asked[1].request_id}`, { behavior: 'allow', updatedPermissions: [mixed[0]] })).status, 200)
  await call('DELETE', `/v1/sessions/${id}`)
})

test('an id the broker does not hold is a 404 in its words, as the real broker answers', async () => {
  const { id, asked } = await several()
  const first = await call('POST', `/v1/sessions/${id}/permissions/${asked[0].request_id}`, { behavior: 'allow' })
  assert.equal(first.status, 200)
  const again = await call('POST', `/v1/sessions/${id}/permissions/${asked[0].request_id}`, { behavior: 'allow' })
  assert.equal(again.status, 404)
  assert.equal(again.body.error, `no permission request ${asked[0].request_id} is pending`)
  assert.equal((await call('POST', `/v1/sessions/${id}/permissions/never`, { behavior: 'deny' })).status, 404)
  await call('DELETE', `/v1/sessions/${id}`)
})

test('an allow with updatedPermissions is resolved with scope; /expire and /cancel close; an exit ends the rest before broker.exit', async () => {
  const { id, asked } = await several()
  const rule = asked[0].permission_suggestions
  assert.equal((await call('POST', `/v1/sessions/${id}/permissions/${asked[0].request_id}`, { behavior: 'allow', updatedPermissions: rule })).status, 200)
  await call('POST', `/v1/sessions/${id}/messages`, { text: '/expire' })
  assert.equal(await pending(id, asked[1].request_id), false)
  await call('DELETE', `/v1/sessions/${id}`)
  const log = await logOf(id)
  const resolved = log.filter((e) => e.type === 'broker.permission_resolved')
  assert.deepEqual(resolved.map((e) => [e.request_id, e.behavior]), [
    [asked[0].request_id, 'allow'],
    [asked[1].request_id, 'expired'],
    [asked[2].request_id, 'ended'],
  ])
  assert.equal(resolved[0].scope, 'session')
  assert.deepEqual(resolved[0].updatedPermissions, rule)
  const exitAt = log.findIndex((e) => e.type === 'broker.exit')
  assert.ok(exitAt > log.indexOf(resolved[2]), 'the exit came before the request it ended was closed')

  const other = await several()
  await call('POST', `/v1/sessions/${other.id}/messages`, { text: '/cancel' })
  await call('DELETE', `/v1/sessions/${other.id}`)
  assert.deepEqual((await logOf(other.id)).filter((e) => e.type === 'broker.permission_resolved').map((e) => e.behavior), ['cancelled', 'ended', 'ended'])
})

test('demo Schedule files open in Files and return supplied good/broken save states without provenance', async () => {
  const schedules = await call('GET', '/v1/schedules?agent=scout')
  const row = schedules.body.schedules.find((r) => r.name === 'weekly-review')
  const query = new URLSearchParams(row.file)
  const read = await call('GET', `/v1/files/read?${query}`)
  assert.equal(read.body.path, row.file.path)
  assert.equal(read.body.authority, 'schedule')
  assert.equal(read.body.capabilities.write, true)
  let base = read.body.revision
  for (const [text, state] of [['---\nschedule: weekdays at 07:00 PT\nenabled: true\n---\nWrite the morning digest.\n', 'live'], ['---\nschedule: weekdays at 7am\nenabled: true\n---\nWrite the morning digest.\n', 'broken']]) {
    const saved = await call('PUT', '/v1/files/text', { ...row.file, text, base_revision: base })
    assert.equal(saved.status, 200)
    assert.equal(saved.body.text, text)
    assert.equal(saved.body.provenance, false)
    assert.equal(saved.body.schedule.state, state)
    assert.equal(saved.body.schedule.name, row.name)
    base = saved.body.revision
  }
})
