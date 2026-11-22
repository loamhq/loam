import { setOwner } from '../public/owner.js'
import { createThreadFixture } from '../public/threads-fixture.js'
import { fakeBroker, appAgainst, closing } from './harness.js'
export async function oneProductFixture(env = {}, overrides = {}) {
  setOwner({ id: 'you', name: 'Sam', accepts: ['you', 'owner', 'person'] })
  const f = createThreadFixture(), streams = new Map(), json = (res, value, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(value)) }
  const decorate = (row) => {
    if (row.thread?.id === 'a-quieter-week' && !row.messages.some((m) => m.id === 'm2-scout')) {
      row.messages[0].text += ' @scout'
      row.messages.splice(2, 0, { id:'m2-scout',author:'system',kind:'system',text:'You brought in Scout' })
    }
    return row
  }
  const record = decorate(await f.api('GET', '/threads/a-quieter-week'))
  // Opening a fixture record marks it read, as the runtime does. Build both
  // comparison listings from that same reading, so unread treatments agree.
  const threads = (await f.api('GET', '/threads')).threads
  const records = threads.map((t, i) => ({ session_id: `classic-${t.id}`, agent: t.default ?? f.agents[0].id, provider: 'claude-code', title: t.title,
    created: t.created, last_activity: t.last?.at ?? t.created, last_event_at: t.last?.at ?? t.created, state: 'running', status: 'done', live: true,
    record_seq: 7, seen_seq: t.unread ? 0 : 7, last_message: t.last?.text, pinned: t.pinned, archived: false, background_tasks: 0, turns: [], pending_permissions: [] }))
  const events = [
    { type: 'broker.user_message', text: record.messages[0].text },
    { type: 'assistant', message: { id: 'intro', content: [{ type: 'text', text: 'Let’s leave a little room for the unexpected. I’ll read the notes and gather the next steps.' }] } },
    { type: 'assistant', message: { id: 'read', content: [{ type: 'tool_use', id: 'read', name: 'Read', input: { file_path: 'notes.md' } }] } },
    { type: 'user', message: { content: [{ type: 'tool_result', tool_use_id: 'read', content: 'The notes are ready.' }] } },
    { type: 'assistant', message: { id: 'reply', content: [{ type: 'text', text: record.messages[3].text }] } },
    { type: 'assistant', message: { id: 'follow-up', content: [{ type: 'text', text: record.messages[4].text }] } },
    { type: 'result', subtype: 'success', is_error: false },
  ].map((e, i) => ({ ...e, seq: i + 1 }))
  const routes = {
    'GET /v1/owner': (req, res) => json(res, { id: 'you', name: 'Sam', accepts: ['you', 'owner', 'person'] }),
    'GET /v1/agents': (req, res) => json(res, { agents: f.agents }),
    'GET /v1/providers': (req, res) => json(res, { providers: f.providers }),
    'GET /v1/sessions': (req, res) => json(res, { sessions: records, live_count: records.length, cap: 10 }),
    'GET /v1/sessions/events': (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n'); for (const r of records) res.write(`data: ${JSON.stringify({ type: 'broker.session', ...r })}\n\n`) },
    'GET /v1/threads': async (req, res, url) => json(res, await f.api('GET', '/threads' + url.search)),
    'GET /v1/threads/events': (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n'); const stop = f.subscribeList((e) => res.write(`data: ${JSON.stringify(e)}\n\n`)); res.on('close', stop) },
    'POST /v1/threads': async (req, res, url, raw) => { const row = await f.api('POST', '/threads', JSON.parse(raw)); addThread(row.id); json(res, row, 201) },
  }
  for (const r of records) {
    routes[`PATCH /v1/sessions/${r.session_id}`] = (req, res, url, raw) => { Object.assign(r, JSON.parse(raw)); json(res, r) }
    routes[`GET /v1/sessions/${r.session_id}/events`] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n'); for (const e of [{ type: 'broker.session', ...r, window_first_seq: 1, turns_before: 0 }, ...events]) res.write(`data: ${JSON.stringify(e)}\n\n`) }
  }
  function addThread(id) {
    for (const [method, suffix] of [['GET', ''], ['PATCH', ''], ['DELETE', ''], ['POST', '/branch'], ['POST', '/messages'], ['PUT', '/messages']]) routes[`${method} /v1/threads/${id}${suffix}`] = async (req, res, url, raw) => {
      try {
        const body = await f.api(method, `/threads/${id}${suffix}${url.search}`, raw ? JSON.parse(raw) : undefined)
        if (suffix === '/branch') addThread(body.id)
        json(res, method === 'GET' && !suffix ? decorate(body) : body, suffix === '/branch' ? 201 : 200)
      }
      catch (e) { json(res, { error: e.message }, e.status ?? 400) }
    }
    routes[`GET /v1/threads/${id}/events`] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n'); if (!streams.has(id)) streams.set(id, new Set()); streams.get(id).add(res); const stop = f.subscribe(id, (e) => res.write(`data: ${JSON.stringify(e)}\n\n`)); res.on('close', () => { stop(); streams.get(id).delete(res) }) }
  }
  for (const t of threads) addThread(t.id)
  const broker = await fakeBroker(routes), app = await appAgainst(broker, env, overrides)
  return { ...app, f, broker, records, events, routes, addThread, emit: (id, e) => { for (const stream of streams.get(id) ?? []) stream.write(`data: ${JSON.stringify(e)}\n\n`) }, close: closing(app, broker) }
}
