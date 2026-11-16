// Compatibility belongs only at the old address boundary. No session is opened
// or resumed here; history is an ordinary archived thread after resolution.
export function sessionFromAddress(href) {
  const url = new URL(href, 'http://placeholder')
  const match = /^\/sessions\/([^/]+)\/?$/.exec(url.pathname)
  try { return match ? decodeURIComponent(match[1]) : url.searchParams.get('session') || null } catch { return null }
}

export function rememberedSession(storage = globalThis.localStorage) {
  try {
    const value = JSON.parse(storage?.getItem('loam-ui.session') ?? 'null')
    return typeof value?.id === 'string' ? value.id : typeof value === 'string' ? value : null
  } catch { return null }
}

// Accept explicit IDs only. Some import inventories carry a thread summary,
// others name the mapping directly. Never use a classic session ID as a thread
// ID when the mapping is absent.
export function importedThreadId(body, session) {
  if (typeof body?.thread_id === 'string') return body.thread_id
  if (typeof body?.thread?.id === 'string') return body.thread.id
  if (typeof body?.thread === 'string') return body.thread
  const rows = body?.imported ?? body?.imports ?? body?.threads
  if (!Array.isArray(rows)) return null
  const source = (row) => row.session_id ?? row.session ?? row.source_session_id ?? row.imported_from?.session_id
  const row = rows.find((r) => source(r) === session) ?? (rows.length === 1 && source(rows[0]) == null ? rows[0] : null)
  if (!row) return null
  const id = row.thread_id ?? (typeof row.thread === 'string' ? row.thread : row.thread?.id) ?? row.id
  return typeof id === 'string' && id ? id : null
}
