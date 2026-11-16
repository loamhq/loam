// Contract fixture only: no timer, service, file or host process is touched.
import { schedule, change, refusal, grammar, TS } from './schedule-contract-fixtures.js'
export function createFakeSchedules(agents = ['scout']) {
  const rows = new Map(), activities = new Map(), before = new Map()
  const copy = (v) => structuredClone(v)
  const key = (agent, name) => `${agent}/${name}`
  let serial = 0
  for (const agent of agents) {
    const reflection = schedule(agent, { name: 'reflection', kind: 'reflection', schedule: 'daily at 03:20 PT', words: 'daily at 03:20 PT',
      body: 'The nightly reflection wake. Its instruction is `_reflection.md`; this record holds only when it runs and whether it is on.',
      instruction: '_reflection.md', timer: `loam-wake-${agent}.timer`, last_change: change(agent, 'reflection', { id: `migration-${agent}`, ts: '2026-10-15T00:16:31Z', op: 'create', actor: 'engineer', lane: 'engineer', reason: 'migration from approved: to enabled:',
        diff: { header: { enabled: [null, 'true'], lane: [null, 'reflection'], schedule: [null, 'daily at 03:20 PT'] }, body_changed: true, created: true, deleted: false }, seen: true }) })
    const recurring = schedule(agent, { last_change: change(agent, 'weekly-review'), unseen: [`change-${agent}`],
      last_fire: { id: `fire-${agent}`, ts: TS, outcome: 'killed', stage: 'died_mid_run' } })
    const once = schedule(agent, { name: 'prepare-notes', kind: 'one-off', schedule: '2026-10-16 14:30 PT', words: 'once, 2026-10-16 14:30 PT', note: 'did the PR merge?', body: 'Check whether the PR merged; say so in comms.', timer: `loam-cron-${agent}-prepare-notes.timer` })
    for (const row of [reflection, recurring, once]) rows.set(key(agent, row.name), row)
    activities.set(reflection.last_change.id, reflection.last_change)
    activities.set(recurring.last_change.id, recurring.last_change)
    before.set(recurring.last_change.id, { ...copy(recurring), schedule: 'Sundays at 05:00 PT', words: 'Sundays at 05:00 PT', last_change: null, unseen: [] })
  }
  function sync(row) {
    row.unseen = [...activities.values()].filter((e) => e.agent === row.agent && e.record === row.name && !e.seen && ['change', 'refused'].includes(e.kind)).map((e) => e.id)
    row.changed_since_seen = row.unseen.length > 0
    if (row.last_change) row.last_change.seen = activities.get(row.last_change.id)?.seen ?? row.last_change.seen
    return row
  }
  const unread = () => [...activities.values()].filter((e) => !e.seen).length
  const answer = (status, body) => ({ status, body: copy(body) })
  const fail = (error, status = 400) => answer(status, { error })
  function changed(agent, name, old, row, op, reason, undoes) {
    const header = {}
    for (const field of ['enabled', 'schedule', 'at', 'note']) {
      const value = (r) => !r ? null : field === 'at' ? (r.kind === 'one-off' ? r.schedule : null) : field === 'schedule' ? (r.kind === 'one-off' ? null : r.schedule) : String(r[field] ?? '')
      if (value(old) !== value(row)) header[field] = [value(old), value(row)]
    }
    const event = change(agent, name, { id: `write-${++serial}`, actor: 'owner', lane: 'app', op, reason: reason ?? null, seen: true,
      diff: { header, body_changed: old?.body !== row?.body, created: !old, deleted: !row }, ...(undoes ? { undoes } : {}) })
    activities.set(event.id, event)
    before.set(event.id, copy(old))
    if (row) {
      row.last_change = event; row.refused = null
      row.state = row.enabled && !row.done ? 'live' : 'off'
      row.running = row.state === 'live' ? 'this' : 'none'
      row.state_words = row.state === 'live' ? 'Live.' : 'Off: the file says it is not enabled.'
      row.last_edit = { id: event.id, ts: event.ts, actor: event.actor, by: 'owner', lane: 'app', taken: true }
      sync(row)
    }
    const { seen, ...mutation } = event
    return answer(200, { event: mutation, schedule: row })
  }
  function validation(body, creating) {
    const allowed = ['schedule', 'at', 'note', 'body', 'enabled', 'reason', ...(creating ? ['name'] : [])]
    const extra = Object.keys(body).filter((k) => !allowed.includes(k))
    if (extra.length) return `a schedule has no field ${extra.join(', ')}; the fields are schedule, at, note, body, enabled`
    if (creating && !/^[a-z0-9-]+$/.test(body.name ?? '')) return 'a new schedule needs a name: lower-case letters, digits and dashes'
    if ('enabled' in body && typeof body.enabled !== 'boolean') return '`enabled` is true or false'
    if ('schedule' in body && !/^(daily|weekdays|weekends|(Sundays|Mondays|Tuesdays|Wednesdays|Thursdays|Fridays|Saturdays)( and (Sundays|Mondays|Tuesdays|Wednesdays|Thursdays|Fridays|Saturdays))?) at ([01]\d|2[0-3]):[0-5]\d PT$/.test(body.schedule)) return grammar(body.schedule)
    if ('at' in body && !/^\d{4}-\d{2}-\d{2} ([01]\d|2[0-3]):[0-5]\d PT$/.test(body.at)) return '`at` is written as words, like "2026-10-20 14:30 PT"'
    return null
  }
  function handleAnswer(method, p, url, body = {}) {
    const undo = /^\/v1\/activity\/([^/]+)\/undo$/.exec(p)
    if (undo && method === 'POST') {
      const event = activities.get(undo[1])
      if (!event || event.kind !== 'change') return fail(`there is no schedule change '${undo[1]}' in the log`, 404)
      const row = rows.get(key(event.agent, event.record))
      // Deletion is also undoable when it is the record's latest change.
      const latest = [...activities.values()].filter((e) => e.kind === 'change' && e.agent === event.agent && e.record === event.record).at(-1)
      if (latest.id !== event.id) return fail(`${event.agent}/${event.record} has changed since that change; undo the later change first`)
      const restored = copy(before.get(event.id))
      if (restored) rows.set(key(event.agent, event.record), restored)
      else rows.delete(key(event.agent, event.record))
      const op = !restored ? 'delete' : !row ? 'create' : row.enabled !== restored.enabled ? restored.enabled ? 'resume' : 'pause' : row.schedule !== restored.schedule ? 'move' : 'edit'
      return changed(event.agent, event.record, row ?? null, restored, op, `undo of ${event.op} by ${event.actor} at ${event.ts}`, event.id)
    }
    if (p === '/v1/schedules' && method === 'GET') {
      const agent = url.searchParams.get('agent')
      if (agent && !agents.includes(agent)) return fail(`'${agent}' is not an agent on this box`, 404)
      return answer(200, { schedules: [...rows.values()].filter((r) => (!agent || r.agent === agent) && !(r.kind === 'one-off' && r.done)).map(sync), unread: unread() })
    }
    const route = /^\/v1\/schedules\/([^/]+)(?:\/([^/]+)(?:\/(run|seen))?)?$/.exec(p)
    if (!route) return null
    const [, agent, name, action] = route
    if (!agents.includes(agent)) return fail(`'${agent}' is not an agent on this box`, 404)
    if (!name && method === 'GET') return handleAnswer('GET', '/v1/schedules', new URL(`http://fixture/v1/schedules?agent=${agent}`))
    const row = rows.get(key(agent, name))
    if (!name && method === 'POST') {
      const error = validation(body, true)
      if (error) return fail(error)
      if (rows.has(key(agent, body.name))) return fail(`${agent} already has a schedule named ${body.name}`)
      const value = body.at ?? body.schedule
      const created = schedule(agent, { name: body.name, kind: body.at ? 'one-off' : 'recurring', schedule: value, words: body.at ? `once, ${value}` : value, body: body.body ?? '', note: body.note ?? '', enabled: body.enabled ?? true, timer: `loam-cron-${agent}-${body.name}.timer` })
      if (!created.enabled) created.next_fire = null
      rows.set(key(agent, body.name), created)
      const result = changed(agent, body.name, null, created, 'create', body.reason)
      return { ...result, status: 201 }
    }
    if (!row) return fail(`there is no schedule ${agent}/${name}`, 404)
    if (method === 'GET' && !action) return answer(200, sync(row))
    if (method === 'PATCH' && !action) {
      if (row.state === 'broken') return fail(row.error)
      const error = validation(body, false)
      let why = error
      if (row.kind === 'reflection' && 'body' in body) why = `the reflection's instruction is ${agent}'s own _reflection.md; this record holds only when it runs and whether it is on`
      const old = copy(row)
      if (!why) {
        if (body.schedule !== undefined || body.at !== undefined) {
          row.kind = row.kind === 'reflection' ? 'reflection' : body.at ? 'one-off' : 'recurring'
          row.schedule = body.at ?? body.schedule
          row.words = row.kind === 'one-off' ? `once, ${row.schedule}` : row.schedule
          row.done = ''
          if (body.schedule !== undefined) row.note = ''
        }
        for (const field of ['body', 'enabled', 'note']) if (field in body) row[field] = body[field]
        row.next_fire = row.enabled ? '2026-10-21T12:00:00Z' : null
        if (JSON.stringify(old) === JSON.stringify(row)) why = 'that changes nothing in the schedule'
      }
      if (why) {
        Object.assign(row, old)
        const event = refusal(agent, name, { id: `refusal-${++serial}`, actor: 'owner', lane: 'app', why, seen: true })
        activities.set(event.id, event); row.refused = event
        return fail(why)
      }
      return changed(agent, name, old, row, 'enabled' in body ? row.enabled ? 'resume' : 'pause' : 'schedule' in body || 'at' in body ? 'move' : 'edit', body.reason)
    }
    if (method === 'DELETE' && !action) {
      if (row.kind === 'reflection') return fail("the nightly reflection's record is not deleted; pause it with `enabled: false`")
      rows.delete(key(agent, name))
      return changed(agent, name, row, null, 'delete', body.reason)
    }
    if (method === 'POST' && action === 'seen') {
      const ids = sync(row).unseen
      for (const id of ids) activities.get(id).seen = true
      sync(row)
      return answer(200, { marked: ids.length, unread: unread() })
    }
    if (method === 'POST' && action === 'run') {
      if (!row.enabled) return fail(`${agent}/${name} is paused, and a paused schedule does not run; resume it first`)
      if (row.kind === 'one-off' && row.done) return fail(`${agent}/${name} is a one-off that has already run (${row.done})`)
      const event = { id: `run-${++serial}`, ts: TS, agent, record: name, actor: 'owner', lane: 'app', unit: row.timer.replace(/\.timer$/, '.service'), kind: 'run', seen: true }
      activities.set(event.id, event)
      const { seen, ...run } = event
      return answer(200, { event: run })
    }
    return null
  }
  return { rows, activities, sync, handleAnswer,
    fire(agent, name, outcome = 'ran') {
      const row = rows.get(key(agent, name))
      const event = { id: `fire-${++serial}`, ts: TS, agent, record: name, outcome, lane: `cron:${name}`, started: TS, finished: TS, kind: 'fire', actor: 'loam', stage: null, produced: null, seen: false }
      activities.set(event.id, event)
      row.last_fire = { id: event.id, ts: TS, outcome, stage: null }
      if (row.kind === 'one-off') { row.done = `${TS} ${outcome}`; row.next_fire = null }
      return event
    },
    handle(req, res, url, body = {}) {
      const result = handleAnswer(req.method, decodeURIComponent(url.pathname), url, body)
      if (!result) return false
      res.writeHead(result.status, { 'content-type': 'application/json', 'cache-control': 'no-store' })
      res.end(JSON.stringify(result.body))
      return true
    } }
}
