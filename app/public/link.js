import { authenticatedFetch, authExpired, authFailure, expireAuth, failureSentence } from './auth.js'
// The live connection to one session's stream: opening it, noticing when it has
// gone, and reopening it at the position the transcript has reached.
//
// Three things make this its own file rather than a loop inside the page.
//
// 1. **A phone closes connections and does not say so.** The app is
//    backgrounded, the socket goes, and nothing arrives to tell the page: no
//    error, no end of stream, no event. So the page cannot wait to be told. It
//    watches the clock instead — every byte and every `: ping` is the stream
//    saying it is still there, and silence longer than a ping plus a margin is
//    a connection that no longer exists whatever the browser thinks. The ping
//    interval is not assumed: this app's own server announces it on the
//    stream's opening comment, and that number is what is read here.
// 2. **Coming back is an event the page must act on.** `visibilitychange`,
//    `pageshow`, `online`, `focus` — the page wires all four to `wake()`, and
//    `wake()` reconnects at once if the stream is closed or stale and does
//    nothing at all if it is fine.
// 3. **None of it touches the DOM**, so it is testable against this app's own
//    `/api` with no browser: give it a `fetch`, a clock and a pair of timers,
//    and cause the drop rather than reason about it.
//
// What this file does not do is decide what an event means, and it does not
// hold a position of its own: `from()` asks the transcript where it has read
// to, freshly, at every attempt. The transcript moves that number on an event's
// `seq` and never on a snapshot, which is the rule the whole stream rests on.

import { readEvents } from './sse.js'

// What to assume until the stream's opening comment says otherwise. The real
// number comes off the wire; this is only what to use if an old cached page
// meets a server that does not announce one.
export const DEFAULT_PING_MS = 15000

// Silence longer than one ping plus this is a stream that has gone. The margin
// is for a slow link, not for a dead one.
export const STALE_MARGIN_MS = 10000

// How much of a conversation a chat opens with, in **turns** — the host's unit,
// not the page's: a turn runs from a person's message through the turn's
// result, and ten turns is not ten events. A chat with a year in it used to
// open at `?from=0` and draw nothing until the whole of it had been sent.
export const TURNS_AT_OPEN = 10

// First retry immediate — the common case is a connection that died while the
// screen was off, and by the time anyone is looking the wait has already
// happened. Then a short climb, capped at a few seconds, forever.
export const BACKOFF_MS = [0, 500, 1000, 2000, 4000]

export function backoffFor(attempt) {
  const n = Number.isFinite(attempt) ? Math.max(0, Math.trunc(attempt)) : 0
  return BACKOFF_MS[Math.min(n, BACKOFF_MS.length - 1)]
}

// The broker's own words when it refuses, from wherever it put them. Shared
// with the page's ordinary `/api` calls: a refusal reads the same whether it
// arrived as a JSON body or as the answer to a stream.
export function sentenceOf(body) {
  if (!body || typeof body !== 'object') return null
  for (const key of ['error', 'message', 'detail', 'reason']) {
    if (typeof body[key] === 'string' && body[key].trim() !== '') return failureSentence(body[key].trim())
  }
  return null
}

// Waiting must be visible and never a silent stall, so every phase of the
// connection has a word. `caughtUp` is the page's: the stream replays from disk
// before it goes live, and the record says where the log ends, so the page can
// tell reading history from watching it happen.
export function statusLine(phase, { caughtUp = true, detail = '' } = {}) {
  switch (phase) {
    case 'connecting':
      return 'connecting…'
    case 'open':
      return caughtUp ? 'live' : 'catching up…'
    case 'retrying':
      return detail ? `disconnected, retrying… (${detail})` : 'disconnected, retrying…'
    case 'stopped':
      return detail || 'not connected'
    default:
      return detail || ''
  }
}

export function createLink({
  sessionId,
  from = () => null, // () => number | null — the transcript's position, read fresh each time
  // Which stream, as a function of that position. One session's events is the
  // default. The home screen's live list is the other caller, and it passes an
  // `href` that takes no position at all, because that stream has none: it
  // carries records, which are snapshots, and reopening it re-sends the lot.
  href = null,
  // Whether there is nothing left to wait for. A chat whose process has ended
  // replays its log and the stream ends where the log ends — nothing more will
  // ever arrive on it, so reconnecting is a retry loop against a host that has
  // already answered in full. The caller reads the record for this; the
  // connection only asks, and only when a stream ends cleanly.
  settled = () => false,
  // How many turns the first connection asks for. A reconnect never uses it:
  // it has a position, and a position is what it asks with.
  lastTurns = TURNS_AT_OPEN,
  onEvent,
  onPhase = () => {},
  base = '', // the page speaks to its own origin; a test names one
  fetchImpl = (...args) => authenticatedFetch(...args),
  now = () => Date.now(),
  timers = { set: (fn, ms) => setTimeout(fn, ms), clear: (h) => clearTimeout(h) },
}) {
  let stopped = false
  let connection = null // the AbortController of the attempt in flight
  let open = false // a response is being read right now
  let attempt = 0
  let pingMs = DEFAULT_PING_MS
  let lastHeard = now()
  let waiting = null // { resolve, handle } — the pause between attempts
  let dropped = false // this end hung up because the stream had gone quiet
  let phase = 'connecting'
  let detail = ''

  function setPhase(next, why = '') {
    phase = next
    detail = why
    onPhase(next, why)
  }

  // Anything at all from the other end, including an unfinished frame.
  function heard() {
    lastHeard = now()
    attempt = 0
  }

  function comment(text) {
    heard()
    const announced = /(?:^|\s)ping=(\d+)/.exec(text)
    if (announced) pingMs = Number(announced[1])
  }

  function stale() {
    return open && now() - lastHeard > pingMs + STALE_MARGIN_MS
  }

  function pause(ms) {
    return new Promise((resolve) => {
      const handle = timers.set(() => {
        waiting = null
        resolve()
      }, ms)
      waiting = { resolve, handle }
    })
  }

  // The page is back, or the network is. Reconnect now if there is nothing to
  // reconnect to, and leave a healthy stream alone. Answers whether it acted,
  // which is what the tests read.
  function wake() {
    if (stopped) return false
    if (waiting) {
      // Between attempts: stop waiting and go.
      const pending = waiting
      waiting = null
      attempt = 0
      timers.clear(pending.handle)
      pending.resolve()
      return true
    }
    if (stale()) {
      // A connection the browser still believes in, which has said nothing for
      // longer than it promised. Hang up; the loop reconnects immediately.
      dropped = true
      attempt = 0
      connection?.abort()
      return true
    }
    return false
  }

  async function run() {
    while (!stopped) {
      connection = new AbortController()
      open = false
      setPhase('connecting')
      try {
        // Every attempt asks the transcript where it has read to. On the first
        // one that is nothing — nothing has been read — and a chat opens at its
        // *end*: the last few turns, then live. Every attempt after that has a
        // position, and a position is what catches it up.
        const position = from()
        const query =
          position === null || position === undefined
            ? `last=${encodeURIComponent(lastTurns)}`
            : `from=${encodeURIComponent(position)}`
        const url = href
          ? href(position ?? 0)
          : `${base}/api/sessions/${encodeURIComponent(sessionId)}/events?${query}`
        const res = await fetchImpl(url, {
          headers: { accept: 'text/event-stream' },
          signal: connection.signal,
        })
        if (authFailure(res)) { expireAuth(); stopped = true; setPhase('stopped', 'Sign in again'); return }
        if (!res.ok) {
          const words = await refusal(res)
          // A session the broker does not have is not a session to retry at.
          if (res.status === 404) {
            stopped = true
            setPhase('stopped', words)
            return
          }
          throw new Error(words)
        }
        open = true
        heard()
        setPhase('open')
        for await (const event of readEvents(res, { onComment: comment, onBytes: heard })) {
          if (stopped) return
          heard()
          onEvent(event)
        }
        open = false
        if (stopped) return
        if (settled()) {
          // The log ended and the session is not running. This is not a drop.
          stopped = true
          setPhase('stopped', '')
          return
        }
        setPhase('retrying', 'the stream ended')
      } catch (err) {
        open = false
        if (stopped) return
        if (authExpired() || err?.status === 401) { stopped = true; setPhase('stopped', 'Sign in again'); return }
        setPhase('retrying', dropped ? 'the stream went quiet' : String(err?.message ?? err))
      }
      dropped = false
      const delay = backoffFor(attempt)
      attempt += 1
      await pause(delay)
    }
  }

  async function refusal(res) {
    const text = await res.text().catch(() => '')
    let body = null
    try {
      body = JSON.parse(text)
    } catch {
      /* the broker's plain words */
    }
    return sentenceOf(body) ?? (text.trim() === '' ? `the stream answered ${res.status}` : text.trim())
  }

  return {
    start() {
      return run()
    },
    // A settled stream that has something to wait for again: the session was
    // resumed under the same id, so the same loop opens again at the position
    // the transcript has reached. Nothing else restarts a stopped link.
    restart() {
      if (!stopped) return false
      stopped = false
      attempt = 0
      dropped = false
      run().catch(() => {})
      return true
    },
    stop() {
      stopped = true
      connection?.abort()
      if (waiting) {
        timers.clear(waiting.handle)
        const pending = waiting
        waiting = null
        pending.resolve()
      }
    },
    // An explicit Retry also replaces a socket still waiting on a partial frame.
    retry() {
      if (stopped) return this.restart()
      if (waiting) return wake()
      connection?.abort()
      return true
    },
    wake,
    isStale: stale,
    get phase() {
      return phase
    },
    get detail() {
      return detail
    },
    get pingMs() {
      return pingMs
    },
  }
}
