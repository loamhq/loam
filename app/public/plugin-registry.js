// Public registration and lifecycle contract, shared by core and plugins.
export const PLACE_NAMES = Object.freeze(['agent.profile', 'rail.section', 'panel.item', 'app.overlay', 'settings.section', 'thread.list.decoration', 'thread.header.action', 'message.renderer', 'message.footer.action', 'composer.tool', 'files.viewer', 'files.action', 'files.root', 'activity.kind', 'apps.source', 'apps.icon', 'thread.list.line', 'command'])
export const COMPONENT_IDS = Object.freeze(['core.thread-list', 'core.message', 'core.composer', 'core.files.viewer', 'core.files.editor', 'core.rail', 'core.activity', 'core.apps', 'core.settings'])
export function createRegistry({ changed = () => {}, failed = () => {} } = {}) {
  const records = new Map(PLACE_NAMES.map(name => [name, []])), listeners = new Map(), owners = new Map(), scopes = new Map()
  let serial = 0
  function fail(owner, error) {
    if (owner === '@core') throw error
    removeOwner(owner); failed(owner, error); changed()
  }
  function invoke(entry, method, value) {
    try { return typeof entry[method] === 'function' ? entry[method](value) : entry[method] }
    catch (error) { fail(entry.owner, error); return null }
  }
  function entries(name, context = {}) {
    if (!records.has(name)) throw new Error(`Unknown place: ${name}`)
    const all = [...records.get(name)].filter(entry => (!entry.component || entry.component === context.component) && (!entry.slot || entry.slot === context.slot) && (!entry.when || invoke(entry, 'when', context)))
    const replaced = new Set(all.filter(entry => entry.owner !== '@core' && entry.component).map(entry => entry.component))
    return all.filter(entry => entry.owner !== '@core' || !replaced.has(entry.component))
      .sort((a, b) => (a.layer === 'yours') - (b.layer === 'yours') || (a.order ?? 0) - (b.order ?? 0) || a.serial - b.serial)
  }
  function draw(name, context = {}) { return entries(name, context).filter(entry => !entry.component).map(entry => records.get(name).includes(entry) ? invoke(entry, 'render', context) : null).flat().filter(value => value !== null && value !== undefined && value !== false) }
  function component(id, context = {}) {
    for (const name of PLACE_NAMES) {
      const candidates = entries(name, { ...context, component: id }).filter(entry => entry.component === id)
      if (candidates.length) {
        const entry = candidates.at(-1), view = invoke(entry, 'render', context)
        return entry.owner !== '@core' && !records.get(name).includes(entry) ? component(id, context) : view
      }
    }
    return null
  }
  function removeOwner(id) { for (const close of scopes.get(id) ?? []) close(); for (const handle of [...(owners.get(id) ?? [])]) handle.remove(); owners.delete(id); scopes.delete(id) }
  function scope(row) {
    const owner = row.id, local = new Set()
    let closed = false
    const close = () => { closed = true }
    const group = scopes.get(owner) ?? new Set(); group.add(close); scopes.set(owner, group)
    owners.set(owner, owners.get(owner) ?? new Set())
    const track = cleanup => {
      if (closed) { cleanup(); return { remove() {} } }
      const handles = owners.get(owner) ?? new Set(); owners.set(owner, handles)
      let active = true
      const handle = { remove() { if (!active) return; active = false; cleanup(); handles.delete(handle); local.delete(handle); changed() } }
      handles.add(handle); local.add(handle); return handle
    }
    const places = Object.fromEntries(PLACE_NAMES.map(name => [name, { add(registration) {
      if (closed) throw new Error('This plugin is no longer active.')
      if (!registration || typeof registration.id !== 'string' || !registration.id || (registration.render !== undefined && typeof registration.render !== 'function')) throw new Error(`Invalid registration for ${name}`)
      if (records.get(name).some(entry => entry.owner === owner && entry.id === registration.id)) throw new Error(`Duplicate registration: ${name}/${registration.id}`)
      const defaults = { 'app.overlay': 'overlay', 'composer.tool': 'extra', 'message.footer.action': 'leading', 'files.viewer': 'viewer', 'files.action': 'action' }
      const entry = { ...(!registration.component && defaults[name] ? { slot: defaults[name] } : {}), ...registration, owner, layer: row.layer, serial: ++serial }
      records.get(name).push(entry); changed()
      return track(() => { const list = records.get(name); const at = list.indexOf(entry); if (at >= 0) list.splice(at, 1) })
    } }]))
    // Dotted names have both access forms: places['rail.section'] and places.rail.section.
    for (const name of PLACE_NAMES) {
      const parts = name.split('.'); let branch = places
      for (const key of parts.slice(0, -1)) { branch[key] ??= {}; branch = branch[key] }
      branch[parts.at(-1)] = places[name]
    }
    const events = { emit: (name, detail) => { if (!closed) emit(name, detail) }, on(name, callback) {
      if (closed) throw new Error('This plugin is no longer active.')
      if (typeof callback !== 'function') throw new Error('An event listener must be a function.')
      const entry = { owner, render: callback }; const set = listeners.get(name) ?? new Set(); listeners.set(name, set); set.add(entry)
      return track(() => set.delete(entry))
    } }
    const commands = { register(registration) {
      if (typeof registration?.run !== 'function') throw new Error('A command needs run(context).')
      return places.command.add(registration)
    }, run(id, context) {
      const command = entries('command', context).findLast(entry => entry.id === id)
      if (!command) throw new Error(`Unknown command: ${id}`)
      return invoke(command, 'run', context)
    } }
    return { places, commands, events, own: handle => track(() => handle.remove()), dispose: () => { close(); for (const handle of [...local]) handle.remove(); group.delete(close); if (owners.get(owner)?.size === 0) owners.delete(owner); if (!group.size && scopes.get(owner) === group) scopes.delete(owner) } }
  }
  const emit = (name, detail) => { for (const entry of [...(listeners.get(name) ?? [])]) if (listeners.get(name)?.has(entry)) invoke(entry, 'render', detail) }
  const reduce = (name, context, initial) => entries(name, context).filter(entry => !entry.component).reduce((view, entry) => { if (!records.get(name).includes(entry)) return view; const next = invoke(entry, 'render', { ...context, view }); return records.get(name).includes(entry) ? next : view }, initial)
  return { scope, entries, draw, reduce, component, emit, removeOwner, invoke, overrides: owner => [...records.values()].flat().filter(entry => entry.owner === owner && entry.component).map(entry => entry.component), get owners() { return [...owners.keys()] } }
}
