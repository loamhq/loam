import { oneProductFixture } from './one-product-fixture.mjs'

export async function retirementFixture() {
  const app = await oneProductFixture()
  const original = await app.f.api('GET', '/threads/a-quieter-week')
  const archives = Array.from({ length: 153 }, (_, i) => {
    const at = new Date(Date.UTC(2026, 10, 10 - i, 10)).toISOString()
    return { ...structuredClone(original.thread), id: `imported-${i + 1}`, title: i ? `Archived conversation ${String(i + 1).padStart(3, '0')}` : 'A plan worth keeping',
      archived: true, pinned: i === 152, unread: 0, created: at, last: { author: 'sprout', text: 'The original conversation is here, with its questions and replies.', at } }
  })
  const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
  let available = true, mapping = true
  const listing = async () => ({ threads: [...(await app.f.api('GET', '/threads')).threads, ...archives].reverse(), agents: app.f.agents })
  app.routes['GET /v1/threads'] = async (req, res) => available ? json(res, await listing()) : json(res, { error: 'Threads are temporarily unavailable.' }, 503)
  app.routes['GET /v1/threads/events'] = async (req, res) => {
    if (!available) return json(res, { error: 'Threads are temporarily unavailable.' }, 503)
    res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(`: open ping=15000\n\ndata: ${JSON.stringify({ type: 'threads.snapshot', ...await listing() })}\n\n`)
  }
  app.routes['GET /v1/threads/imported'] = (req, res, url) => json(res, { imports: mapping ? [{ session_id: url.searchParams.get('session') ?? 'old-chat', thread_id: 'imported-1' }] : [] })
  for (const thread of archives) {
    app.routes[`GET /v1/threads/${thread.id}`] = (req, res) => json(res, { ...structuredClone(original), thread,
      messages: [{ id: 'old-question', author: 'you', kind: 'message', text: 'Can we keep the original plan?', at: thread.created },
        { id: 'old-answer', author: 'sprout', kind: 'message', text: 'Yes. The plan, its decisions, and the useful details are all here.\n\nWe can pick up from this conversation whenever you are ready.', at: thread.created }] })
    app.routes[`GET /v1/threads/${thread.id}/events`] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\ndata: {"type":"thread.snapshot","turn":null,"pending":[]}\n\n') }
  }
  return { ...app, archives, setAvailable(value) { available = value }, setMapping(value) { mapping = value } }
}
