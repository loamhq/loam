// Synthetic conversation: a large image used to sit between the tool and reply.
// No real agent or host conversation is read by these regressions.
import { fakeBroker } from './harness.js'
import { limitsAnswer } from './fixtures/files-contract.js'

export const loadWarning = "Couldn't load the rest of this chat"
const frame = (event) => `data: ${JSON.stringify(event)}\n\n`
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }

export async function chatLoadHost(mode = 'hold') {
  const now = new Date().toISOString()
  const record = {
    session_id: 'load-fixture', agent: 'example', provider: 'claude-code',
    created: now, last_activity: now, last_event_at: now,
    state: 'exited', live: false, status: 'done', title: 'Review the page images',
    last_message: 'The page review is complete.', pinned: false, archived: false,
    record_seq: 6, seen_seq: 0, turns: [], pending_permissions: [], background_tasks: 0,
  }
  const events = [
    { type: 'broker.user_message', seq: 1, text: 'Please review these pages.' },
    { type: 'assistant', seq: 2, message: { id: 'call', content: [{ type: 'tool_use', id: 'pages', name: 'Read', input: { file_path: 'pages.pdf' } }] } },
    { type: 'user', seq: 3, message: { content: [{ type: 'tool_result', tool_use_id: 'pages', content: [
      { type: 'text', text: 'Two pages.' },
      { type: 'image', source: { type: 'base64', media_type: 'image/png', omitted_chars: 8500000 } },
      { type: 'image', source: { type: 'base64', media_type: 'image/png', omitted_chars: 8500000 } },
    ] }] } },
    { type: 'assistant', seq: 4, message: { id: 'reply', content: [{ type: 'text', text: record.last_message }] } },
    { type: 'result', seq: 5, subtype: 'success', is_error: false },
    { type: 'broker.exit', seq: 6, exit_code: 0 },
  ].map((event) => ({ session_id: record.session_id, ...event }))
  const lists = new Set()
  const threads = new Set()
  const broker = await fakeBroker({
    'GET /v1/agents': (req, res) => json(res, { agents: [{ id: 'example', name: 'Example', emoji: null }] }),
    'GET /v1/providers': (req, res) => json(res, { providers: [] }),
    'GET /v1/limits': (req, res) => json(res, limitsAnswer()),
    'GET /v1/sessions': (req, res) => json(res, { sessions: [record], live_count: 0, cap: 4 }),
    'GET /v1/sessions/events': (req, res) => {
      res.writeHead(200, { 'content-type': 'text/event-stream' })
      res.write(': open ping=15000\n\n' + frame({ type: 'broker.session', ...record }))
      lists.add(res)
      res.on('close', () => lists.delete(res))
    },
    'GET /v1/sessions/load-fixture/events': (req, res, url) => {
      res.writeHead(200, { 'content-type': 'text/event-stream' })
      res.write(': open ping=15000\n\n' + frame({ type: 'broker.session', ...record, window_first_seq: 1, turns_before: 0 }))
      const from = Number(url.searchParams.get('from') ?? 0)
      for (const event of events) {
        if (event.seq <= from) continue
        if (mode !== 'complete' && mode !== 'large' && event.seq > 2) break
        if (mode === 'large' && event.seq === 3) {
          const large = structuredClone(event)
          large.message.content[0].content = [{ type: 'image', source: { type: 'base64', media_type: 'image/png', data: 'A'.repeat(17000000) } }]
          res.write(frame(large))
        } else res.write(frame(event))
      }
      if (mode !== 'hold') res.end()
      threads.add(res)
      res.on('close', () => threads.delete(res))
    },
    'PATCH /v1/sessions/load-fixture': (req, res, url, body) => {
      const change = JSON.parse(body)
      if (Number.isFinite(change.seen_seq)) record.seen_seq = Math.max(record.seen_seq, change.seen_seq)
      for (const list of lists) list.write(frame({ type: 'broker.session', ...record }))
      json(res, record)
    },
  })
  return { ...broker, record, events, mode(next) { mode = next },
    marks: () => broker.seen.filter((c) => c.method === 'PATCH').map((c) => JSON.parse(c.body).seen_seq),
    requests: () => broker.seen.filter((c) => c.path === '/v1/sessions/load-fixture/events'),
    write(event) { for (const res of threads) res.write(frame(event)) },
  }
}
