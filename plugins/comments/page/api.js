// Transport for the Comments & suggestions server.
// `base` is `/x/comments/` inside Loam, `/x/comments/remote/<wid>/` for a file
// shared from another Loam, or `/s/<token>/api/collab/` on a share page.

export class ApiError extends Error {
  constructor(status, message, code = '') {
    super(message)
    this.status = status
    this.code = code
  }
}

export function docApi({ base, root = '', path = '', headers = {}, fetchImpl = (...a) => fetch(...a) }) {
  const b = base.endsWith('/') ? base : `${base}/`
  const hasAddr = Boolean(root && path)
  async function req(method, route, body = null) {
    const inQuery = method === 'GET' || method === 'DELETE'
    const params = { ...(hasAddr ? { root, path } : {}), ...(body || {}) }
    const qs = inQuery && Object.keys(params).length ? `?${new URLSearchParams(params)}` : ''
    let res
    try {
      res = await fetchImpl(`${b}${route}${qs}`, {
        method,
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json', ...headers },
        body: inQuery ? undefined : JSON.stringify(params),
      })
    } catch {
      throw new ApiError(0, 'Comments could not reach its server.')
    }
    let data = null
    try { data = await res.json() } catch { data = null }
    if (!res.ok) {
      throw new ApiError(
        res.status,
        data?.error || (res.status === 502 ? 'The comments server is not answering.' : `The comments server answered ${res.status}.`),
        data?.code || '',
      )
    }
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
