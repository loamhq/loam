// Shared fake for the relay, controller, DOM and real-browser schedule checks.
import { createFakeSchedules } from '../tools/fake-schedules.js'
import { fakeBroker } from './harness.js'
import { activityFixture } from '../tools/activity-fixture.js'
import { fileFirstSchedules } from './fixtures/schedules-file-first.js'
import { scheduleFilesFixture } from '../tools/schedule-files-fixture.js'

export async function schedulesHost({ fileFirst = false } = {}) {
  const fake = createFakeSchedules(['scout', 'alfred'])
  // The second agent has only its reflection, to exercise that empty state.
  fake.rows.delete('alfred/weekly-review')
  fake.rows.delete('alfred/prepare-notes')
  const activity = activityFixture([{ id: 'scout', name: 'Scout' }, { id: 'alfred', name: 'Alfred' }], fake)
  if (fileFirst) {
    fake.rows.clear(); fake.activities.clear()
    for (const row of fileFirstSchedules('scout').schedules) {
      fake.rows.set(`${row.agent}/${row.name}`, row)
      for (const id of row.unseen) fake.activities.set(id, { id, agent: row.agent, record: row.name, kind: 'change', seen: false })
    }
  }
  const files = scheduleFilesFixture(fake)
  const streams = []
  const json = (res, value) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(value)) }
  const common = {
    'GET /v1/agents': (req, res) => json(res, { agents: [{ id: 'scout', name: 'Scout', emoji: null }, { id: 'alfred', name: 'Alfred', emoji: null }] }),
    'GET /v1/providers': (req, res) => json(res, { providers: [{ id: 'claude-code', name: 'Claude Code', connected: true, usage: null }] }),
    'GET /v1/limits': (req, res) => json(res, { attachment_max_bytes: 10000000, attachment_types: [] }),
    'POST /v1/sessions/seen': (req, res) => json(res, { marked: 0 }),
    'GET /v1/sessions': (req, res) => json(res, { sessions: [], live_count: 0, cap: 4 }),
    'GET /v1/sessions/events': (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n'); streams.push(res) },
  }
  for (const id of ['scout', 'alfred']) common[`GET /v1/agents/${id}`] = (req, res) => json(res, {
    agent: { id, name: id === 'scout' ? 'Scout' : 'Alfred', emoji: null },
    identity: { display: id === 'scout' ? 'Scout' : 'Alfred', emoji: null }, defaults: { effective: { provider: 'claude-code' } },
    status: {}, files: { workspace: { root: 'spaces', path: id }, memory: {} },
    schedule: [...fake.rows.values()].filter(row => row.agent === id), plugins: [],
  })
  const handlers = new Proxy(common, { get(target, name) {
    return target[name] || ((req, res, url, text) => {
      const body = text ? JSON.parse(text) : {}
      const answer = files.handle(req.method, url, body) ?? activity.handle(req.method, decodeURIComponent(url.pathname), url, body) ?? activity.files(req.method, url.pathname, url)
      if (answer) { res.writeHead(answer.status, { 'content-type': 'application/json' }); res.end(JSON.stringify(answer.body)); return }
      if (!fake.handle(req, res, url, body)) { res.writeHead(404); res.end('{"error":"No such fixture route."}') }
    })
  } })
  return { ...await fakeBroker(handlers), fake, activity, files, streams }
}
