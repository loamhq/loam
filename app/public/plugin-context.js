// A local controller can share page operations without putting its own state
// or methods into core. The same adapter is available to every extension.
export function extensionContext(page, { state = {}, store = {}, helpers = {}, host: parent = page } = {}) {
  let controller = {}
  const stateView = new Proxy({}, {
    get: (_, key) => key in state ? state[key] : parent.state?.[key],
    set: (_, key, value) => { (key in state ? state : parent.state)[key] = value; return true },
  })
  const host = new Proxy({}, {
    get: (_, key) => key === 'state' ? stateView : key === 'store' ? store : key in helpers ? helpers[key] : key in controller ? controller[key] : parent[key],
    set: (_, key, value) => { parent[key] = value; return true },
  })
  return { host, attach(value) { controller = value; return value } }
}
