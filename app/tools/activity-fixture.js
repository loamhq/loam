// Real broker response shapes from docs/broker-schedules-and-activity-api.md.
// No jobs or timers run; mutations affect these fixture records only.
import { row, rootsAnswer, readAnswer, listAnswer, RO, FOLDER_RO } from '../test/fixtures/files-contract.js'
import { createFakeSchedules } from './fake-schedules.js'
import { refusal, TS } from './schedule-contract-fixtures.js'

export function activityFixture(agents = [{ id: 'test-agent', name: 'Test agent' }, { id: 'review-agent', name: 'Reviewer' }], schedules = createFakeSchedules(agents.map((a) => a.id))) {
  const first = agents[0], second = agents[1] ?? first
  const items = [
    { ...schedules.activities.get(`change-${first.id}`), id: 'change-refused', record: 'old-review' },
    { id: 'scheduled-ran', kind: 'fire', agent: second.id, actor: 'loam', record: 'weekly-review', outcome: 'ran', lane: 'cron:weekly-review', started: '2026-10-14T12:00:27Z', finished: '2026-10-14T12:38:35Z', stage: null, produced: { wake_log: 'logs/wake.jsonl', line: { ts: '2026-10-14T12:00:27Z', finished: '2026-10-14T12:38:35Z', ok: true, vault_ref: null, total_cost_usd: 8.969986500000001 } } },
    refusal(first.id),
    { id: 'job-landed', kind: 'job', job: '20261014-000000-abcd1234', agent: second.id, actor: second.id, project: 'demo', title: 'The search improvements', ok: true, stage: 'done', outcome: 'completed', landing: 'landed', landed_sha: '0'.repeat(40), receipt: 'Receipts/search.md', job_dir: 'jobs/20261014-000000-abcd1234', started: TS },
    ...['failed', 'skipped', 'missed', 'killed'].map((outcome, index) => ({ id: `fire-${outcome}`, kind: 'fire', agent: index % 2 ? second.id : first.id, actor: 'loam', record: 'weekly-review', outcome, lane: 'cron:weekly-review', started: TS, finished: null, stage: outcome === 'killed' ? 'died_mid_run' : null, produced: ['failed', 'missed', 'killed'].includes(outcome) ? { wake_log: 'logs/wake.jsonl', line: { ts: TS, ok: false }, failure_log: 'Inbox/wake-failures.md' } : null })),
    { id: 'job-not-landed', kind: 'job', job: '20261014-000001-abcd1234', agent: first.id, actor: first.id, project: 'demo', title: 'The export changes', ok: false, stage: 'done', outcome: 'failed', landing: 'not_landed', landed_sha: null, receipt: 'Receipts/export.md', job_dir: 'jobs/20261014-000001-abcd1234', started: TS },
    { id: 'run-request', kind: 'run', agent: first.id, actor: 'owner', record: 'weekly-review', lane: 'app', unit: `loam-cron-${first.id}-weekly-review.service` },
  ].map((item, i) => ({ ts: new Date(Date.parse(TS) - i * 60000).toISOString(), ...item }))
  const slots = [{ slot: 1, free: false, since: TS, pid: 123, kind: 'engineer job', job: '20991231-000000-abcd1234', project: 'demo', title: 'Improve the document search', briefed_by: first.id }, { slot: 2, free: true }, { slot: 3, free: true }]
  const queue = [{ position: 1, joined: TS, job: '20991231-000001-abcd1234', project: 'demo', title: 'Review the export flow', briefed_by: second.id }]
  const landing = [], calls = [], cancelled = new Set()
  const ok = (body) => ({ status: 200, body: structuredClone(body) })
  const fail = (error, status = 400) => ({ status, body: { error } })
  const all = () => [...items, ...schedules.activities.values()].filter((e, i, list) => list.findIndex((v) => v.id === e.id) === i).map(({ seen, ...item }) => item)
  const fixture = { items, slots, queue, landing, calls, schedules, jobsError: null, all }
  function handle(method, path, url, body = {}) {
    if (!path.startsWith('/v1/activity') && !/^\/v1\/jobs\/[^/]+\/cancel$/.test(path)) return null
    calls.push({ method, path, query: url.search, body: structuredClone(body) })
    if (path === '/v1/activity' && method === 'GET') {
      const q = url.searchParams
      const limit = q.has('limit') ? Number(q.get('limit')) : 50
      const offset = q.has('offset') ? Number(q.get('offset')) : 0
      if (!Number.isInteger(limit) || limit < 1 || limit > 200) return fail('`limit` is a number between 1 and 200')
      if (!Number.isInteger(offset) || offset < 0) return fail('`offset` is a number')
      const kinds = q.get('kind')?.split(',')
      if (kinds?.some((k) => !['change', 'refused', 'fire', 'run', 'job'].includes(k))) return fail(`${q.get('kind')} is not a kind of activity; the kinds are change, refused, fire, run, job`)
      const filtered = all().filter((i) => (!q.get('agent') || q.get('agent').split(',').includes(i.agent)) && (!kinds || kinds.includes(i.kind))).sort((a, b) => Date.parse(b.ts) - Date.parse(a.ts))
      return ok({ items: filtered.slice(offset, offset + limit), total: filtered.length, next_offset: offset + limit < filtered.length ? offset + limit : null,
        jobs: fixture.jobsError ? null : { at: TS, slots, landing, queue }, jobs_error: fixture.jobsError })
    }
    const undo = /^\/v1\/activity\/([^/]+)\/undo$/.exec(path)
    if (undo && method === 'POST') {
      if (undo[1] === 'change-refused') return fail(`${first.id}/old-review has changed since that change; undo the later change first`)
      return schedules.handleAnswer(method, path, url, body)
    }
    const cancel = /^\/v1\/jobs\/([^/]+)\/cancel$/.exec(path)
    if (cancel && method === 'POST') {
      const id = cancel[1]
      if (!/^\d{8}-\d{6}-[a-f0-9]{8}$/.test(id)) return fail(`'${id}' is not a job id`, 404)
      if (cancelled.has(id)) return fail(`job ${id} already has a verdict; nothing to cancel`)
      const slot = slots.find((j) => j.job === id && !j.free), queued = queue.findIndex((j) => j.job === id)
      if (!slot && queued < 0) return fail(`no job '${id}' in any project`, 404)
      cancelled.add(id)
      if (queued >= 0) queue.splice(queued, 1)
      // A running process remains resident: durable cancel is not a kill.
      return ok({ job: id, said: `cancelled ${id}: ${slot ? 'it will not continue or recover' : 'it had not started and never will'}` })
    }
    return null
  }
  function files(method, path, url) {
    if (method !== 'GET' || !path.startsWith('/v1/files/')) return null
    const note = row('vault', 'Notes/plan.md', { capabilities: RO })
    const folder = row('vault', 'Notes', { capabilities: FOLDER_RO })
    if (path === '/v1/files/roots') return ok({ ...rootsAnswer(), shortcuts: [] })
    if (path === '/v1/files/resolve') return ok({ status: 'found', resource: note })
    if (path === '/v1/files/read') return ok(readAnswer(note, '# Engineer receipt\n\nThe search improvements landed. All checks passed.\n', 'sha256:activity-fixture'))
    if (path === '/v1/files/stat') return ok(url.searchParams.get('path')?.endsWith('.md') ? note : folder)
    if (path === '/v1/files/list') return ok(listAnswer(folder, [note]))
    return null
  }
  return Object.assign(fixture, { handle, files })
}
