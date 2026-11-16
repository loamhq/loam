import { oneProductFixture } from './one-product-fixture.mjs'

export const openingId = 'a-quieter-week'
const paragraph = 'Keep the useful details close to the conversation. A clear next step leaves room to think, review the notes, and choose what matters. Each small decision should make tomorrow easier to understand. The plan can stay simple while the record keeps all the work behind it.'
export const openingMessages = Array.from({ length: 38 }, (_, i) => ({
  id: `m${i + 1}`, author: i % 5 === 0 ? 'you' : i % 2 ? 'sprout' : 'quill',
  kind: 'message', at: new Date(Date.UTC(2026, 10, 10, 12, i)).toISOString(),
  text: i % 5 === 0 ? `Could you review the next steps? This is thought ${i + 1}.`
    : `## A little clarity · ${i + 1}\n\n${Array.from({ length: 19 }, () => paragraph).join('\n\n')}\n\n**Next:** keep one useful thing within reach.`, did: [],
}))

export async function openingFixture() {
  const app = await oneProductFixture(), clients = new Set(), reads = [], writes = []
  const original = await app.f.api('GET', `/threads/${openingId}`)
  const row = { ...original.thread, title: 'Room for the next thought', unread: 2, last: { author: 'quill', text: 'Keep one useful thing within reach.', at: openingMessages.at(-1).at } }
  let messages = structuredClone(openingMessages), historyRelease, latestRelease, holdHistory = false, holdLatest = false
  const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
  app.routes['GET /v1/threads'] = (req, res) => json(res, { threads: [row], agents: app.f.agents })
  app.routes['GET /v1/threads/events'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(`: open ping=15000\n\ndata: ${JSON.stringify({ type: 'threads.snapshot', threads: [row], agents: app.f.agents })}\n\n`) }
  app.routes[`GET /v1/threads/${openingId}`] = async (req, res, url) => {
    reads.push(url.search)
    const before = url.searchParams.get('before'), limit = Number(url.searchParams.get('limit') ?? 50)
    const end = before ? messages.findIndex((m) => m.id === before) : messages.length
    const body = { ...original, thread: { ...row, unread: before ? row.unread : 0 }, messages: messages.slice(Math.max(0, end - limit), end), has_more: end > limit, turn: null }
    if (before && holdHistory) await new Promise((r) => { historyRelease = r })
    if (!before && holdLatest) await new Promise((r) => { latestRelease = r })
    await new Promise((r) => setTimeout(r, 80))
    json(res, body)
  }
  app.routes[`GET /v1/threads/${openingId}/steps`] = (req, res) => { res.writeHead(404, { 'content-type': 'application/json' }); res.end('{"error":"No full record for this reply"}') }
  app.routes[`PATCH /v1/threads/${openingId}`] = (req, res, url, body) => { writes.push(JSON.parse(body)); json(res, row) }
  app.routes[`GET /v1/threads/${openingId}/events`] = (req, res, url) => {
    reads.push(`events${url.search}`)
    res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n')
    res.write(`data: ${JSON.stringify({ type: 'thread.snapshot', turn: null, pending: [] })}\n\n`)
    const after = url.searchParams.get('after'), at = messages.findIndex((m) => m.id === after)
    if (after) for (const message of messages.slice(at + 1)) res.write(`data: ${JSON.stringify({ type: 'message.done', message })}\n\n`)
    clients.add(res); res.on('close', () => clients.delete(res))
  }
  return { ...app, reads, writes, row,
    setMessages: (next) => { messages = structuredClone(next) },
    holdHistory: () => { holdHistory = true }, releaseHistory: () => { holdHistory = false; historyRelease?.() },
    holdLatest: () => { holdLatest = true }, releaseLatest: () => { holdLatest = false; latestRelease?.() },
    add(message, emit = true) { messages.push(message); if (emit) for (const res of clients) res.write(`data: ${JSON.stringify({ type: 'message.done', message })}\n\n`) },
  }
}
