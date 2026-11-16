// A cap refusal before resume, a refusal of the message itself, and the two
// possible HTTP/event arrival orders. No agent processes are started.
import { fakeBroker } from './harness.js'

export const capReason = '4 active sessions (cap 4). End a session before starting another.'
export const storedFile = { path: '98_ATTACHMENTS/fixture/notes.txt', name: 'notes.txt', size: 12, type: 'text/plain' }
export const resource = { root: 'vault', path: 'Notes/Plan.md', name: 'Plan.md' }
const line = (obj) => `data: ${JSON.stringify(obj)}\n\n`
const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }

export async function unsentHost() {
  const now = new Date().toISOString()
  let record = {
    session_id: 'cap-chat', agent: 'scout', provider: 'claude-code',
    created: now, last_activity: now, last_event_at: now,
    state: 'exited', live: false, status: 'done', title: 'A note to send',
    last_message: 'Ready when you are.', pinned: false, archived: false,
    record_seq: 4, seen_seq: 4, pending_permissions: [], background_tasks: 0, turns: [],
  }
  let full = true
  let refuseMessage = false
  let eventFirst = true
  const listStreams = new Set()
  const streams = new Set()
  const events = [
    { type: 'broker.user_message', seq: 1, text: 'Can we continue here?' },
    { type: 'assistant', seq: 2, message: { id: 'a1', content: [{ type: 'text', text: record.last_message }] } },
    { type: 'result', seq: 3, subtype: 'success', is_error: false },
    { type: 'broker.exit', seq: 4, exit_code: 143 },
  ].map((e) => ({ ...e, session_id: record.session_id }))
  const snapshot = () => {
    for (const stream of [...listStreams, ...streams]) stream.write(line({ type: 'broker.session', ...record }))
  }
  const emit = (event) => {
    event = { ...event, session_id: record.session_id, seq: ++record.record_seq }
    events.push(event)
    for (const stream of streams) stream.write(line(event))
    return event.seq
  }
  const broker = await fakeBroker({
    'GET /v1/agents': (req, res) => json(res, { agents: [{ id: 'scout', name: 'Scout', emoji: '🔭' }] }),
    'GET /v1/providers': (req, res) => json(res, { providers: [{ id: 'claude-code', name: 'Claude Code', connected: true, login: { days_left: 20 } }] }),
    'GET /v1/limits': (req, res) => json(res, { attachment_max_bytes: 1024, attachment_types: ['text/plain'] }),
    'GET /v1/sessions': (req, res) => json(res, { sessions: [record], cap: 4, live: full ? 4 : 0, live_count: full ? 4 : 0 }),
    'GET /v1/sessions/events': (req, res) => {
      res.writeHead(200, { 'content-type': 'text/event-stream' })
      res.write(': open ping=15000\n\n')
      listStreams.add(res)
      res.on('close', () => listStreams.delete(res))
      res.write(line({ type: 'broker.session', ...record }))
    },
    'GET /v1/sessions/cap-chat/events': (req, res, url) => {
      res.writeHead(200, { 'content-type': 'text/event-stream' })
      res.write(': open ping=15000\n\n')
      streams.add(res)
      res.on('close', () => streams.delete(res))
      res.write(line({ type: 'broker.session', ...record, window_first_seq: 1, turns_before: 0 }))
      for (const event of events) if (event.seq > Number(url.searchParams.get('from') ?? 0)) res.write(line(event))
      if (record.state === 'exited') res.end()
    },
    'PATCH /v1/sessions/cap-chat': (req, res, url, body) => {
      record = { ...record, ...JSON.parse(body) }
      json(res, record)
    },
    'POST /v1/sessions': (req, res) => {
      if (full) return json(res, { error: capReason }, 429)
      record = { ...record, state: 'running', live: true, status: 'done' }
      json(res, record, 201)
      snapshot()
    },
    'POST /v1/sessions/cap-chat/messages': (req, res, url, body) => {
      if (refuseMessage) return json(res, { error: capReason }, 429)
      const message = JSON.parse(body)
      record = { ...record, status: 'working' }
      const accepted = () => {
        const seq = emit({ type: 'broker.user_message', text: message.text, attachments: message.attachments?.map(() => storedFile) })
        snapshot()
        return seq
      }
      if (eventFirst) json(res, { accepted: true, seq: accepted() })
      else {
        json(res, { accepted: true, seq: record.record_seq + 1 })
        setTimeout(accepted, 80)
      }
    },
  })
  return {
    ...broker,
    freeSlot() { full = false; snapshot() },
    refuse(value) { refuseMessage = value },
    eventFirst(value) { eventFirst = value },
    exit(code = 143) { emit({ type: 'broker.exit', exit_code: code }) },
    finish() { emit({ type: 'result', subtype: 'success' }); record.status = 'done'; snapshot() },
  }
}
