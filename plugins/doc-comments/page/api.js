// The plugin server's operations as a client calls them. `base` is where the
// server answers: `/x/doc-comments/` inside Loam (the plugin gateway, behind
// the login); step 2's share page passes its own. Transport only: the rules
// are the server's.

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status }
}

export function docApi({ base, root, path, headers = {}, fetchImpl = (...a) => fetch(...a) }) {
  const b = base.endsWith('/') ? base : `${base}/`
  async function req(method, route, body = null) {
    const inQuery = method === 'GET' || method === 'DELETE'
    const q = inQuery ? `?${new URLSearchParams({ root, path, ...(body || {}) })}` : ''
    let res
    try {
      res = await fetchImpl(`${b}${route}${q}`, {
        method,
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json', ...headers },
        body: inQuery ? undefined : JSON.stringify({ root, path, ...(body || {}) }),
      })
    } catch (err) {
      throw new ApiError(0, 'Comments could not reach their server.')
    }
    let data = null
    try { data = await res.json() } catch { data = null }
    if (!res.ok) throw new ApiError(res.status, data?.error || (res.status === 502 ? 'The comments server is not answering.' : `The comments server answered ${res.status}.`))
    return data
  }
  const note = (id, action, body = {}) => req('POST', `doc/notes/${encodeURIComponent(id)}/${action}`, body)
  return {
    req,
    get: () => req('GET', 'doc'),
    revision: () => req('GET', 'doc/revision'),
    deleteReply: (id, reply) => req('DELETE', `doc/notes/${encodeURIComponent(id)}/replies/${encodeURIComponent(reply)}`),
    save: (body) => req('POST', 'doc/save', body),
    add: (body) => req('POST', 'doc/notes', body),
    reply: (id, text) => note(id, 'replies', { text }),
    resolve: (id) => note(id, 'resolve'),
    reopen: (id) => note(id, 'reopen'),
    accept: (id) => note(id, 'accept'),
    reject: (id) => note(id, 'reject'),
    reattach: (id, body) => note(id, 'reattach', body),
    withdraw: (id) => note(id, 'withdraw'),
    removeNote: (id) => note(id, 'remove'),
    edit: (id, reply, text) => req('PATCH', `doc/notes/${encodeURIComponent(id)}`, { reply, text }),
    history: () => req('GET', 'doc/history'),
    version: (save) => req('GET', 'doc/version', { save }),
    restore: (save) => req('POST', 'doc/restore', { save }),
  }
}
