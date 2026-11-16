// Server-sent events, parsed by hand.
//
// `EventSource` would be shorter, but it only hands over events it was told
// the name of, it re-opens the exact URL it was given, and this page must
// re-open at the last `seq` it saw and must see every event whatever the
// broker chose to call it. So: one reader, one parser, and reconnection that
// is this app's own.

// `onComment` is how a keepalive is heard. A comment is not an event and never
// becomes one, but `: ping` is the only thing a silent stream sends, so it is
// the difference between a connection that is quiet and one that is gone.
export function parseSseChunks({ onComment } = {}) {
  let buffer = ''

  // Feed it text, get back whatever complete events that text finished.
  return function feed(text) {
    buffer += text
    const events = []
    // An event ends at a blank line. \r\n and \n both count.
    for (;;) {
      const boundary = /\r?\n\r?\n/.exec(buffer)
      if (!boundary) break
      const raw = buffer.slice(0, boundary.index)
      buffer = buffer.slice(boundary.index + boundary[0].length)
      const parsed = parseEvent(raw, onComment)
      if (parsed) events.push(parsed)
    }
    return events
  }
}

function parseEvent(raw, onComment) {
  let name = 'message'
  const data = []
  let id = null
  for (const line of raw.split(/\r?\n/)) {
    if (line === '') continue
    if (line.startsWith(':')) {
      // A comment, which on this stream means a keepalive or the opening line.
      onComment?.(line.slice(1).trim())
      continue
    }
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    let value = colon === -1 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'event') name = value
    else if (field === 'data') data.push(value)
    else if (field === 'id') id = value
  }
  if (data.length === 0) return null
  return { name, id, data: data.join('\n') }
}

// The events of one session, as objects, until the caller aborts or the
// stream ends. The `data` of each frame is the broker's JSON; a frame that is
// not JSON is handed on as text rather than dropped, so it can be seen.
export async function* readEvents(response, { onComment, onBytes } = {}) {
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  const feed = parseSseChunks({ onComment })
  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      // A large frame can take many keepalive intervals to finish on a phone.
      // Bytes prove the connection is alive before there is an event to yield.
      if (value.byteLength > 0) onBytes?.(value.byteLength)
      for (const frame of feed(decoder.decode(value, { stream: true }))) {
        try {
          yield JSON.parse(frame.data)
        } catch {
          yield { type: frame.name === 'message' ? 'unparsed' : frame.name, raw: frame.data }
        }
      }
    }
  } finally {
    reader.cancel().catch(() => {})
  }
}
