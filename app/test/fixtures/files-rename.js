// Synthetic, stateful Files broker. No host files or credentials are used.
import { row, readAnswer, rootsAnswer, listAnswer, PNG } from './files-contract.js'
export function renameHost() {
  const entries = new Map(), texts = new Map(), renames = [], puts = []
  const add = (path, text, kind = 'file', rename = true) => {
    const r = row('vault', path, { kind })
    r.capabilities = { ...r.capabilities, rename }
    entries.set(path, r)
    if (text !== undefined) texts.set(path, text)
  }
  add('Notes', undefined, 'folder')
  add('Notes/Old', undefined, 'folder')
  add('Notes/Old/child.md', '# A child note\n\nStill here.')
  add('Notes/plan.md', '# The next useful step\n\nA little space to think, then a clear plan.\n\n- Keep the useful details\n- Leave room for what comes next')
  add('Notes/Taken.md', '# Already here')
  add('Notes/pinned.md', '# Keeps its name', 'file', false)
  add('Notes/page.html', '<h1>A quiet page</h1>')
  add('Notes/pic.png')
  const suggestions = [{ label: 'Reading note', resource: entries.get('Notes/Taken.md') }]
  const json = (res, status, body) => { res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' }); res.end(JSON.stringify(body)) }
  const missing = (res, path) => json(res, 404, { error: `nothing at vault/${path}` })
  let revision = 1
  const routes = {
    'GET /v1/files/roots': (req, res) => json(res, 200, { ...rootsAnswer(), shortcuts: suggestions }),
    'GET /v1/files/stat': (req, res, url) => { const p = url.searchParams.get('path'); entries.has(p) ? json(res, 200, entries.get(p)) : missing(res, p) },
    'GET /v1/files/list': (req, res, url) => {
      const p = url.searchParams.get('path') ?? ''
      json(res, 200, listAnswer(entries.get(p) ?? row('vault', ''), [...entries.values()].filter(r => r.path.split('/').slice(0, -1).join('/') === p)))
    },
    'GET /v1/files/read': (req, res, url) => {
      const p = url.searchParams.get('path')
      texts.has(p) ? json(res, 200, readAnswer(entries.get(p), texts.get(p), `rev${revision}`)) : missing(res, p)
    },
    'GET /v1/files/download': (req, res) => { res.writeHead(200, { 'content-type': 'image/png' }); res.end(PNG) },
    'PUT /v1/files/text': (req, res, url, raw) => {
      const b = JSON.parse(raw); puts.push(b)
      if (!entries.has(b.path)) return missing(res, b.path)
      texts.set(b.path, b.text); revision++
      json(res, 200, readAnswer(entries.get(b.path), b.text, `rev${revision}`))
    },
    'POST /v1/files/rename': (req, res, url, raw) => {
      const b = JSON.parse(raw); renames.push(b)
      const current = entries.get(b.path)
      if (!current) return missing(res, b.path)
      if (!current.capabilities.rename) return json(res, 403, { error: `vault/${b.path} keeps its name: it is pinned`, authority: 'user', pinned: true })
      const name = String(b.name ?? '').normalize('NFC').trim()
      if (!name || name === '.' || name === '..' || /[\\/\u0000-\u001f]/.test(name)) return json(res, 400, { error: 'Choose a name without slashes or control characters.' })
      const p = b.path.split('/').slice(0, -1).concat(name).join('/')
      if (entries.has(p)) return json(res, 409, { error: `vault/${p} already exists; choose another name` })
      const result = { ...current, ...row('vault', p, { kind: current.kind }), capabilities: current.capabilities }
      for (const [old, value] of [...entries]) if (old === b.path || (current.kind === 'folder' && old.startsWith(b.path + '/'))) {
        const next = p + old.slice(b.path.length)
        entries.delete(old)
        entries.set(next, old === b.path ? result : { ...value, path: next, link: row('vault', next).link })
        if (texts.has(old)) { texts.set(next, texts.get(old)); texts.delete(old) }
      }
      json(res, 200, { ...result, from: { root: 'vault', path: b.path, link: current.link } })
    },
  }
  return { routes, entries, texts, renames, puts }
}
