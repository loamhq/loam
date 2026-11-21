import { renameHost } from './files-rename.js'

export function deleteHost({ supported = true } = {}) {
  const host = renameHost()
  const state = { calls: [], restores: [], refuse: false, hold: null }
  const trash = new Map()
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
    const restoreId = state.calls.length.toString(16).padStart(32, '0')
    trash.set(restoreId, { path, file, text: host.texts.get(path) })
    host.entries.delete(path)
    host.texts.delete(path)
    res.end(JSON.stringify({ ok: true, restore_id: restoreId }))
  }
  host.routes['POST /v1/files/restore'] = (req, res, url, raw) => {
    const { root, restore_id } = JSON.parse(raw)
    state.restores.push({ root, restore_id })
    const entry = trash.get(restore_id)
    if (root !== 'vault' || !entry) {
      res.writeHead(404, { 'content-type': 'application/json' })
      return res.end(JSON.stringify({ error: 'No matching deletion.' }))
    }
    host.entries.set(entry.path, entry.file)
    if (entry.text !== undefined) host.texts.set(entry.path, entry.text)
    trash.delete(restore_id)
    res.writeHead(200, { 'content-type': 'application/json' })
    res.end(JSON.stringify({ ...entry.file, ok: true }))
  }
  return { ...host, state }
}
