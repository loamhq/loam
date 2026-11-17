import { messageSegments } from './thread-record.js'
import { createPluginStyles } from './plugin-styles.js'
import { extensionContext } from './plugin-context.js'
// The generic settings adapter is device-local. Per-agent values use the
// separate host-backed data.agentSettings API; conversation facts stay there.
export function pluginSettings(id, storage) {
  if (storage === undefined) { try { storage = globalThis.localStorage } catch {} }
  const memory = new Map(), devices = new Map(), prefix = `loam.plugin.${id}.`
  return { device(key, normalize = value => value) {
    return { get value() {
      try { const raw = storage?.getItem(key); if (raw !== null && raw !== undefined) return normalize(JSON.parse(raw)) } catch {}
      return normalize(devices.get(key))
    }, set value(value) {
      value = normalize(value); devices.set(key, value)
      try { storage?.setItem(key, JSON.stringify(value)) } catch {}
    } }
  }, get(key, fallback = null) {
    try { const raw = storage?.getItem(prefix + key); if (raw !== null && raw !== undefined) return JSON.parse(raw) } catch {}
    return memory.has(key) ? memory.get(key) : fallback
  }, set(key, value) {
    memory.set(key, value)
    try { storage?.setItem(prefix + key, JSON.stringify(value)) } catch {}
    return value
  } }
}
const segment = value => encodeURIComponent(value)
export function pluginData(request) {
  return { request,
    agentSettings: { get: (plugin, agent) => request('GET', `/plugins/${segment(plugin)}/agents${agent ? `/${segment(agent)}` : ''}`), patch: (plugin, agent, values) => request('PATCH', `/plugins/${segment(plugin)}/agents/${segment(agent)}`, { values }) },
    threads: { list: () => request('GET', '/threads?archived=1'), get: id => request('GET', `/threads/${segment(id)}`), create: body => request('POST', '/threads', body), patch: (id, body) => request('PATCH', `/threads/${segment(id)}`, body) },
    messages: { parts: messageSegments, list: (id, query = '') => request('GET', `/threads/${segment(id)}/messages${query ? `?${query}` : ''}`), send: (id, body) => request('POST', `/threads/${segment(id)}/messages`, body) },
    boards: { get: id => request('GET', `/threads/${segment(id)}/board`), put: (id, body) => request('PUT', `/threads/${segment(id)}/board`, body) },
    files: { read: (root, path) => request('GET', `/files/read?${new URLSearchParams({ root, path })}`), write: body => request('PUT', '/files/text', body), list: (root, path = '') => request('GET', `/files/list?${new URLSearchParams({ root, path })}`) },
  }
}
export function createPluginLoader({ registry, request, ui, changed = () => {}, importer = url => import(url), themes = async () => {}, fallback = [], activationMs = 15000 }) {
  const st = { rows: [], loading: false, loaded: false, known: false, error: '', failures: new Map(), writing: new Set() }
  // Plugins whose page module is still starting, by id. A place a starting
  // plugin names in its manifest (`places`) is "pending": the page may hold
  // its own drawing there for that moment, so a plugin's view is not preceded
  // by a flash of core's (Files' Markdown preview before an editor plugin).
  const settling = new Set()
  const active = new Map(), settings = new Map(), css = new Map()
  const styles = globalThis.document?.head ? createPluginStyles() : null
  let flight = null, reread = false, serial = 0, disposed = false
  const rowKey = (row, index) => `${row.layer}:${row.id ?? index}:${row.folder ?? ''}`
  function scope(row, local = false) {
    const registrations = registry.scope(row)
    if (!settings.has(row.id)) settings.set(row.id, pluginSettings(row.id))
    const host = ui.host ? Object.create(ui.host, { provide: { value: (...args) => registrations.own(ui.host.provide(...args)) } }) : undefined
    return { ...registrations, data: pluginData(request), ui: { ...ui, styles: { add: file => registrations.own(styles?.add(row, file, local) ?? { remove() {} }) }, ...(host ? { host, extension: options => extensionContext(host, options) } : {}) }, settings: settings.get(row.id), api: 0 }
  }
  const bounded = async (promise, phase) => {
    let timer
    try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`Plugin ${phase} did not finish within ${activationMs / 1000} seconds.`)), activationMs) })]) }
    finally { clearTimeout(timer) }
  }
  async function stop(id) {
    const entry = active.get(id); if (!entry) { registry.removeOwner(id); return }
    active.delete(id); entry.cancelled = true; entry.scope.dispose()
    try { if (entry.cleanup) await bounded(Promise.resolve().then(entry.cleanup), 'cleanup') } catch (error) { st.failures.set(id, error.message) }
  }
  function failed(id, error) { st.failures.set(id, error.message || String(error)); stop(id); changed() }
  async function apply(rows, local = false) {
    const eligible = rows.filter(row => row.effective && row.on && row.switch === 'plugin' && row.has?.page_main && !row.broken)
    const wanted = new Map(eligible.map(row => [row.id, row]))
    for (const [id, entry] of active) if (!wanted.has(id) || entry.signature !== JSON.stringify(wanted.get(id))) await stop(id)
    const themeWork = Promise.resolve().then(() => themes(rows)).catch(error => { st.error = error.message })
    if (disposed) return
    const styled = rows.filter(row => row.layer === 'yours' && row.effective && !row.broken && row.has?.theme && (row.on || row.switch === 'appearance'))
    for (const [key, entry] of css) if (!styled.some(row => JSON.stringify(row) === key)) { entry.remove(); css.delete(key) }
    for (const row of styled.sort((a,b) => (a.layer === 'yours') - (b.layer === 'yours'))) {
      const key = JSON.stringify(row)
      if (!css.has(key) && styles) css.set(key, styles.add(row, 'theme.css', local))
    }
    for (const row of eligible) if (!active.has(row.id)) settling.add(row.id)
    const activate = async row => {
      try { await activateRow(row) } finally { if (settling.delete(row.id)) changed() }
    }
    const activateRow = async row => {
      if (disposed || active.has(row.id)) return
      if (!Number.isInteger(row.api) || row.api > 0) { st.failures.set(row.id, `This plugin needs page API ${row.api}; this page supports API 0.`); return }
      const api = scope(row, local), entry = { signature: JSON.stringify(row), scope: api, cleanup: null, cancelled: false }
      active.set(row.id, entry); st.failures.delete(row.id)
      const cleanupFor = value => {
        const fn = typeof value === 'function' ? value : value?.dispose?.bind(value)
        let done = false
        return async () => { if (done) return; done = true; await fn?.() }
      }
      try {
        const url = local && row.local ? row.local : `/api/plugins/${segment(row.id)}/files/page/main.js?load=${++serial}`
        const mod = await bounded(Promise.resolve().then(() => importer(url)), 'import')
        if (disposed || entry.cancelled) return
        if (typeof mod.activate !== 'function') throw new Error('The page module must export activate(loam).')
        const activation = Promise.resolve().then(() => mod.activate(api))
        activation.then(value => { entry.cleanup = cleanupFor(value); if (entry.cancelled) entry.cleanup().catch(error => { st.failures.set(row.id, error.message); changed() }) }, () => {})
        await bounded(activation, 'activation')
        if (disposed || entry.cancelled || st.failures.has(row.id)) { await entry.cleanup?.(); api.dispose() }
      } catch (error) {
        if (!entry.cancelled) st.failures.set(row.id, error.message || String(error))
        entry.cancelled = true; api.dispose()
        if (active.get(row.id) === entry) active.delete(row.id)
        try { await entry.cleanup?.() } catch (cleanupError) { st.failures.set(row.id, cleanupError.message) }
      }
      changed()
    }
    // Healthy defaults start independently; Yours executes after all defaults
    // settle (including their bounded failures), so user commands win too.
    await Promise.all(eligible.filter(row => row.layer !== 'yours').map(activate))
    await Promise.all([themeWork, ...eligible.filter(row => row.layer === 'yours').map(activate)])
    styles?.refresh()
    changed()
  }
  async function read() {
    st.loading = true; changed()
    do {
      reread = false
      let rows, local = false
      try {
        const out = await request('GET', '/plugins')
        if (!Array.isArray(out?.plugins)) throw new Error('Plugins could not be read from this host.')
        rows = out.plugins; st.error = out.error ?? ''
      } catch (error) {
        st.error = error.message
        // Old/unreachable hosts retain shipped capabilities and core theme
        // fallback. Effective host rows never use this local import path.
        if (st.loaded) { st.loading = false; changed(); return }
        try { rows = typeof fallback === 'function' ? await fallback() : fallback } catch { rows = [] }
        if (![404, 405].includes(error.status)) rows = rows.map(row => row.switch === 'plugin' ? { ...row, on: false, switch: null, warnings: [...(row.warnings ?? []), 'Enabled state is unknown while the host catalog is unavailable.'] } : row)
        local = true
      }
      if (disposed) break
      st.rows = rows.map((row, index) => ({ ...row, key: rowKey(row, index) }))
      st.known = true
      await apply(rows, local)
      st.loaded = true
    } while (reread && !disposed)
    st.loading = false; changed()
  }
  function refresh() {
    if (disposed) return Promise.resolve()
    if (flight) { reread = true; return flight }
    flight = read().finally(() => { flight = null })
    return flight
  }
  async function write(method, path, body) {
    st.writing.add(path); changed()
    try {
      const out = await request(method, path, body)
      if (method === 'PATCH' && typeof out?.plugin?.on === 'boolean') {
        const plugin = out.plugin
        st.rows = st.rows.map(row => row.effective && row.id === plugin.id ? { ...row, ...plugin, key: row.key } : row)
        if (!plugin.on) await stop(plugin.id)
      }
      if (method === 'DELETE' && out?.removed) await stop(out.removed)
      return out
    }
    finally { await refresh(); st.writing.delete(path); changed() }
  }
  // Whether a place may still get a plugin's view: before the catalog is read
  // at all, or while a plugin that names the place is starting.
  function pending(place) {
    if (disposed) return false
    if (!st.known) return true
    for (const id of settling) {
      const row = st.rows.find(r => r.id === id && r.effective !== false)
      if (row && Array.isArray(row.places) && row.places.includes(place)) return true
    }
    return false
  }
  return { st, refresh, failed, scope, pending,
    toggle: (id, on) => write('PATCH', `/plugins/${segment(id)}`, { on }),
    install: body => write('POST', '/plugins', body),
    remove: id => write('DELETE', `/plugins/${segment(id)}`),
    async dispose() { disposed = true; for (const entry of css.values()) entry.remove(); css.clear(); await Promise.all([...active.keys()].map(stop)); await flight },
  }
}
