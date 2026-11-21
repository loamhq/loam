import { renameHost } from './files-rename.js'

export function deleteHost({ supported = true } = {}) {
  const host = renameHost()
  const state = { calls: [], refuse: false, hold: null }
  if (!supported) return { ...host, state }

  for (const name of ['plan.md', 'Taken.md', 'page.html', 'pic.png']) {
    const file = host.entries.get(`Notes/${name}`)
    file.capabilities.delete = true
  }
  // A folder remains excluded even if an old or incompatible host marks it.
  host.entries.get('Notes/Old').capabilities.delete = true
  host.routes['POST /v1/files/delete'] = async (req, res, url, raw) => {
    const { root, path } = JSON.parse(raw)
    state.calls.push({ root, path })
    if (state.hold) await state.hold
    const file = host.entries.get(path)
    const permitted = root === 'vault' && file?.kind === 'file' && file.capabilities.delete === true && !state.refuse
    res.writeHead(permitted ? 200 : 403, { 'content-type': 'application/json' })
    if (!permitted) return res.end(JSON.stringify({ error: 'The vault refused this deletion.' }))
    host.entries.delete(path)
    host.texts.delete(path)
    res.end(JSON.stringify({ ok: true }))
  }
  return { ...host, state }
}
