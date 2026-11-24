# Open-thread live stream recovery

These captures verify live recovery for an open thread (`public/threads.js`,
`public/link.js`, `public/thread-record.js`, `public/threads.css`,
`public/app.js`) across phone (`390×844`) and desktop (`1440×900`) viewports in
light and dark modes.

## Root cause

The thread list (`listLink` in `public/threads.js`) and Running panel
(`public/running.js`) already used `createLink()` from `public/link.js` with
keepalive monitoring and lifecycle wakeups. The open thread's own event stream
(`connect()` in `public/threads.js`) did not:

- It opened a bare `fetch('/api/threads/:id/events?after=…')` and iterated
  `readEvents(res)` without `onComment` or `onBytes`, ignoring the server's
  `: open ping=15000` and `: ping` keepalives.
- `threads.tick()` and `cameBack()` never woke or resynced the open thread's
  stream. When a mobile OS froze or half-opened the TCP socket on screen lock,
  backgrounding, or network change, `reader.read()` hung silently with no error
  and no status line.
- Sending a message opened a separate HTTP `PUT`/`POST` that succeeded and
  appended the owner's own bubble locally, while `turn.started`, `turn.step`,
  and `message.done` went to the dead SSE socket — leaving a blank area below
  the message until the person left and reopened the thread.
- Accepting an HTTP send also advanced `open.after` unconditionally, which
  could skip a reply posted while the stream was dead before reconnect.

## What changed

- **Thread stream on `createLink` (`public/threads.js`, `public/link.js`):**
  an open thread now streams through `createLink` with `stopOn404: false`
  (preserving transient-404 removal confirmation) and `watchdog: true`
  (detecting both missed keepalives after `pingMs + STALE_MARGIN_MS` and hung
  `connecting` fetches after `CONNECT_TIMEOUT_MS`).
- **Immediate resync on return (`public/app.js`, `public/threads.js`, `public/link.js`):**
  `visibilitychange` (hidden → visible), `resume`, `online`, `pageshow`
  (`persisted !== false`), and `navigator.connection` interface switches trigger
  a clean `resync` that reopens the stream at `?after=<cursor>` without flashing
  a disconnect notice unless the fresh attempt fails, and without issuing a full
  `GET /threads/:id` read (so scroll-back positions are never marked read).
- **Cursor integrity and send-echo watchdog (`public/thread-record.js`, `public/threads.js`):**
  out-of-band HTTP send confirmations pass `advanceCursor: false` so `open.after`
  only advances from the initial read and ordered SSE `message.done` events, and
  `watchSendEcho` resyncs after 3.5 s if an accepted send is not echoed on SSE.
- **Calm, honest disconnected UI (`public/threads.js`, `public/threads.css`):**
  transient drops show `Reconnecting…`; persistent failure or `offline` shows a
  calm warning-toned notice row above the composer (`Can’t reach the server —
  still trying.` or `Offline — updates will resume when you’re back online.`)
  with a `Try again` control aligned to the composer column.

## Captures (`390×844` and `1440×900`, light and dark)

| Viewport | Mode | Before (stalled after sleep + send) | After (recovered thinking) | After (recovered reply after silent stall) | After (disconnected notice) |
| --- | --- | --- | --- | --- | --- |
| 390×844 | Light | [before](before-stalled-390-light.png) | [thinking](after-thinking-390-light.png) | [reply](after-reply-390-light.png) | [disconnected](after-disconnected-390-light.png) |
| 390×844 | Dark | [before](before-stalled-390-dark.png) | [thinking](after-thinking-390-dark.png) | [reply](after-reply-390-dark.png) | [disconnected](after-disconnected-390-dark.png) |
| 1440×900 | Light | [before](before-stalled-1440-light.png) | [thinking](after-thinking-1440-light.png) | [reply](after-reply-1440-light.png) | [disconnected](after-disconnected-1440-light.png) |
| 1440×900 | Dark | [before](before-stalled-1440-dark.png) | [thinking](after-thinking-1440-dark.png) | [reply](after-reply-1440-dark.png) | [disconnected](after-disconnected-1440-dark.png) |

Reproduce from `app/` with `LOAM_PLAYWRIGHT` and `LOAM_CHROME` set:
`THREAD_LIVE_RECOVERY_EVIDENCE=/tmp/out node test/thread-live-recovery-browser.mjs`
(pass `THREAD_LIVE_RECOVERY_BASELINE=1` to reproduce the pre-fix baseline against `HEAD`).
