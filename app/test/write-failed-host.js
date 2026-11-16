// Contract fixtures only: the failed tail is absent on replay, including result.
import { fakeBroker } from './harness.js'

export const saveReason = "couldn't save this reply: the server's disk is full"
export const saveSentence = `${saveReason}. This reply may not be there after a reload.`
const frame = (event) => `data: ${JSON.stringify(event)}\n\n`
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }

export async function writeFailedHost() {
  const now = new Date().toISOString()
  const record = {
    session_id: 'save-fixture', agent: 'example', provider: 'claude-code',
    created: now, last_activity: now, last_event_at: now,
    state: 'running', live: true, status: 'working', title: 'Review the release plan',
    last_message: null, pinned: false, archived: false,
    record_seq: 1, seen_seq: 0, turns: [], pending_permissions: [], background_tasks: 0,
  }
  const saved = [{ type: 'broker.user_message', seq: 1, text: 'Please review the release plan.' }]
  const threads = new Set(), lists = new Set()
  const broker = await fakeBroker({
    'GET /v1/agents': (req, res) => json(res, { agents: [{ id: 'example', name: 'Reviewer', emoji: null }] }),
    'GET /v1/providers': (req, res) => json(res, { providers: [{ id: 'claude-code', name: 'Claude Code', connected: true, usage: null }] }),
    'GET /v1/limits': (req, res) => json(res, { attachment_max_bytes: 10000000, attachment_types: [] }),
    'GET /v1/sessions': (req, res) => json(res, { sessions: [record], live_count: 1, cap: 4 }),
    'GET /v1/sessions/events': (req, res) => {
      res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n')
      lists.add(res); res.on('close', () => lists.delete(res))
      res.write(frame({ type: 'broker.session', ...record }))
    },
    'GET /v1/sessions/save-fixture/events': (req, res, url) => {
      res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n')
      res.write(frame({ type: 'broker.session', ...record, window_first_seq: 1, turns_before: 0 }))
      for (const event of saved) if (event.seq > Number(url.searchParams.get('from') ?? 0)) res.write(frame(event))
      threads.add(res); res.on('close', () => threads.delete(res))
    },
    'PATCH /v1/sessions/save-fixture': (req, res, url, body) => {
      const change = JSON.parse(body)
      if (Number.isFinite(change.seen_seq)) record.seen_seq = Math.max(record.seen_seq, change.seen_seq)
      for (const list of lists) list.write(frame({ type: 'broker.session', ...record }))
      json(res, record)
    },
  })
  const write = (event) => { for (const stream of threads) stream.write(frame(event)) }
  return { ...broker, record, write,
    fail() {
      write({ type: 'assistant', seq: 2, message: { id: 'live-reply', content: [{ type: 'text', text: 'The release plan covers the main changes. I’m checking the final details.' }] } })
      write({ type: 'broker.write_failed', reason: saveReason, from_seq: 2 })
    },
    more() {
      write({ type: 'assistant', seq: 3, message: { id: 'live-followup', content: [{ type: 'text', text: 'The rollback step is covered, too.' }] } })
    },
    finish() {
      write({ type: 'result', seq: 4, is_error: false, usage: null })
      record.status = 'failed'
      record.turns = [{ model: null, not_saved: saveReason }]
      write({ type: 'broker.session', ...record })
      for (const list of lists) list.write(frame({ type: 'broker.session', ...record }))
    },
  }
}
