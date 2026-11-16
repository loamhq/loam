// Small synthetic thread runtime for shared page-feature tests. No classic
// transport is used to enter a composer, pick a file or dictate a message.
export function threadPage(routes, { agents = [{ id: 'scout', name: 'Scout', emoji: null }], initial = [], sent = [], started = [] } = {}) {
  const records = new Map(), lists = new Set()
  const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
  const line = (e) => `data: ${JSON.stringify(e)}\n\n`
  function add({ id, title = 'New thread', persona = agents[0].id, text = '', created = '2026-10-01T10:00:00Z' }) {
    const thread = { id, title, default: persona, members: [{ persona, engine: 'claude-code', model: null }], pinned: false, archived: false, unread: 0, created, last: text ? { author: persona, text, at: created } : null }
    const record = { thread, has_more: false, turn: null, messages: text ? [{ id: 'm1', author: persona, kind: 'message', text, at: created }] : [], board: { path: `boards/${id}.md`, markdown: '' } }
    records.set(id, record)
    routes[`GET /v1/threads/${id}`] = (req, res) => json(res, record)
    routes[`GET /v1/threads/${id}/events`] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n'); res.write(line({ type: 'thread.snapshot', turn: null, pending: [] })) }
    routes[`GET /v1/threads/${id}/board`] = (req, res) => json(res, record.board)
    routes[`POST /v1/threads/${id}/messages`] = (req, res, url, raw) => {
      const body = JSON.parse(raw); sent.push(body)
      const message = { id: `m${record.messages.length + 1}`, author: 'you', kind: 'message', text: body.text, at: new Date().toISOString() }
      record.messages.push(message); json(res, { accepted: true, message })
    }
    routes[`POST /v1/threads/${id}/attachments`] = (req, res) => json(res, { attachments: [{ path: 'attachments/plan.md', name: 'plan.md', size: 30, type: 'text/markdown' }] }, 201)
    return record
  }
  routes['GET /v1/threads'] = (req, res) => json(res, { threads: [...records.values()].map((r) => r.thread), agents })
  routes['GET /v1/threads/events'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n'); res.write(line({ type: 'threads.snapshot', threads: [...records.values()].map((r) => r.thread), agents })); lists.add(res); res.on('close', () => lists.delete(res)) }
  routes['POST /v1/threads'] = (req, res, url, raw) => { const body = JSON.parse(raw); started.push(body); const r = add({ id: `new-${records.size + 1}`, persona: body.persona }); for (const s of lists) s.write(line({ type: 'thread.changed', thread: r.thread.id, summary: r.thread })); json(res, r.thread, 201) }
  for (const row of initial) add(row)
  return { records, listening: () => lists.size > 0, change: (row) => { for (const s of lists) s.write(line({ type: 'thread.changed', thread: row.id, summary: row })) }, routes }
}
