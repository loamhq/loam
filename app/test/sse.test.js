// The page's own reader of server-sent events. Frames split across chunks,
// comments, multi-line data and an unfamiliar event name all have to survive,
// because a phone on a slow link produces every one of them.

import test from 'node:test'
import assert from 'node:assert/strict'

import { parseSseChunks, readEvents } from '../public/sse.js'

test('a frame split across chunks is read once, whole', () => {
  const feed = parseSseChunks()
  assert.deepEqual(feed('data: {"type":"assi'), [])
  assert.deepEqual(feed('stant","seq":7}\n'), [], 'one newline is not the end of a frame')
  const events = feed('\n')
  assert.equal(events.length, 1)
  assert.deepEqual(JSON.parse(events[0].data), { type: 'assistant', seq: 7 })
})

test('several frames in one chunk come out in order', () => {
  const feed = parseSseChunks()
  const events = feed('data: {"seq":1}\n\ndata: {"seq":2}\n\ndata: {"seq":3}\n\n')
  assert.deepEqual(events.map((e) => JSON.parse(e.data).seq), [1, 2, 3])
})

test('keepalive comments and \\r\\n are not events', () => {
  const feed = parseSseChunks()
  const events = feed(': ping\r\n\r\n: open\r\n\r\ndata: {"seq":9}\r\n\r\n')
  assert.equal(events.length, 1)
  assert.equal(JSON.parse(events[0].data).seq, 9)
})

test('a keepalive is heard even though it is not an event', () => {
  // How the page tells a quiet stream from one that is gone, and where it reads
  // the interval it measures against.
  const comments = []
  const feed = parseSseChunks({ onComment: (text) => comments.push(text) })
  const events = feed(': open ping=15000\n\n: ping\n\ndata: {"seq":9}\n\n')
  assert.deepEqual(comments, ['open ping=15000', 'ping'])
  assert.equal(events.length, 1, 'and it is still not an event')
})

test('an event the broker gave a name to is still read', () => {
  const feed = parseSseChunks()
  const [event] = feed('event: broker.permission_request\ndata: {"request_id":"req-7"}\n\n')
  assert.equal(event.name, 'broker.permission_request')
  assert.equal(JSON.parse(event.data).request_id, 'req-7')
})

test('data spread over several lines is rejoined', () => {
  const feed = parseSseChunks()
  const [event] = feed('data: one\ndata: two\n\n')
  assert.equal(event.data, 'one\ntwo')
})

test('readEvents yields objects from a response body', async () => {
  const body = new ReadableStream({
    start(controller) {
      const encode = (s) => new TextEncoder().encode(s)
      controller.enqueue(encode(': open\n\ndata: {"type":"broker.session","seq":41}\n\ndata: {"type":"res'))
      controller.enqueue(encode('ult","seq":42}\n\n'))
      controller.close()
    },
  })
  const seen = []
  for await (const event of readEvents({ body })) seen.push(event)
  assert.deepEqual(seen, [
    { type: 'broker.session', seq: 41 },
    { type: 'result', seq: 42 },
  ])
})

test('a frame that is not JSON is handed on rather than dropped', async () => {
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('data: not json at all\n\n'))
      controller.close()
    },
  })
  const seen = []
  for await (const event of readEvents({ body })) seen.push(event)
  assert.deepEqual(seen, [{ type: 'unparsed', raw: 'not json at all' }])
})
