// The one place in this app that knows the broker's address and holds its
// token. Everything above it speaks to these functions; the browser speaks to
// the routes in server.js, never to the broker, and never sees the token.

import { Readable } from 'node:stream'

export class BrokerNotConfigured extends Error {
  constructor() {
    super('BROKER_URL is not set, so this app cannot reach any agent.')
    this.name = 'BrokerNotConfigured'
  }
}

export function createBroker(config, { fetchImpl = fetch } = {}) {
  function url(path) {
    if (!config.brokerUrl) throw new BrokerNotConfigured()
    return config.brokerUrl + path
  }

  function headers(extra = {}) {
    const h = { accept: 'application/json', ...extra }
    if (config.brokerToken) h.authorization = `Bearer ${config.brokerToken}`
    return h
  }

  return {
    // The broker's own health. The one route it serves without a token, and
    // the one thing this app can honestly report about it.
    async health() {
      if (!config.brokerUrl) return { configured: false, ok: false }
      try {
        const res = await fetchImpl(config.brokerUrl + '/up', {
          headers: { accept: 'application/json' },
          signal: AbortSignal.timeout(5000),
        })
        return { configured: true, ok: res.ok, status: res.status }
      } catch (err) {
        return { configured: true, ok: false, error: String(err.message ?? err) }
      }
    },

    // A JSON call. The broker's status and body are handed back whole — a 429
    // over the session cap carries a sentence the page is meant to show, and
    // rewording it here would be inventing a second vocabulary for it.
    async json(method, path, body, { signal } = {}) {
      const res = await fetchImpl(url(path), {
        method,
        headers: headers(body === undefined ? {} : { 'content-type': 'application/json' }),
        body: body === undefined ? undefined : JSON.stringify(body),
        signal,
      })
      const text = await res.text()
      return {
        status: res.status,
        contentType: res.headers.get('content-type') ?? 'application/json',
        body: text,
      }
    },

    // A body that is not JSON: the bytes a browser put in a `multipart/form-data`
    // upload, passed through with their own content type and the token added.
    //
    // It is a second method rather than a parameter on `json()` because the two
    // differ in what they may not do: this one must never look inside the body.
    // The app does not parse the multipart envelope, does not name the file and
    // does not decide where the file lands — the broker writes the bytes down,
    // in the agent's own workspace, and answers with the path it used.
    async upload(path, { contentType, body, method = 'POST' }) {
      const res = await fetchImpl(url(path), {
        method,
        headers: headers(contentType ? { 'content-type': contentType } : {}),
        body,
      })
      const text = await res.text()
      return {
        status: res.status,
        contentType: res.headers.get('content-type') ?? 'application/json',
        body: text,
      }
    },

    // A file's bytes, for a download. The status, the type, the name the host
    // gave it and the stream itself, untranslated; what a browser is told
    // about them is `server.js`'s to decide.
    async download(path) {
      const res = await fetchImpl(url(path), { headers: headers({ accept: '*/*' }) })
      if (!res.ok) {
        return { ok: false, status: res.status, contentType: res.headers.get('content-type') ?? 'application/json', body: await res.text() }
      }
      return {
        ok: true,
        status: res.status,
        contentType: res.headers.get('content-type'),
        disposition: res.headers.get('content-disposition'),
        length: res.headers.get('content-length'),
        stream: res.body ? Readable.fromWeb(res.body) : null,
      }
    },

    // Plugin modules/assets are bytes. HEAD must remain HEAD upstream too.
    async file(path, { method = 'GET', signal } = {}) {
      const res = await fetchImpl(url(path), { method, headers: headers({ accept: '*/*' }), signal })
      return { status: res.status, contentType: res.headers.get('content-type'), cacheControl: res.headers.get('cache-control'),
        stream: res.body ? Readable.fromWeb(res.body) : null }
    },

    // An event stream. The caller gets the broker's response and a Node stream
    // of its bytes, unframed and untranslated: whatever the broker wrote, the
    // browser reads. Two streams use this — one session's events, opened at a
    // sequence number, and the list of every session's record, which has no
    // position at all and must not be given one.
    async stream(path, { signal } = {}) {
      const res = await fetchImpl(url(path), {
        headers: headers({ accept: 'text/event-stream' }),
        signal,
      })
      if (!res.ok) {
        return { ok: false, status: res.status, body: await res.text() }
      }
      return { ok: true, status: res.status, stream: Readable.fromWeb(res.body) }
    },
  }
}
