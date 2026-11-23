// A recorded host for the Files page tests, phone and desktop: the Files
// routes answering in the verified contract's shapes (see
// `fixtures/files-contract.js`), and enough of the session routes for a draft
// to be started with files picked for it.

import { row, rootsAnswer, listAnswer, readAnswer, uploadAnswer, limitsAnswer, PNG, PDF, USER } from './fixtures/files-contract.js'

const json = (res, status, body) => {
  res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' })
  res.end(JSON.stringify(body))
}

export function filesHost({ texts = {} } = {}) {
  const roleSource = { root: 'vault', path: 'Projects/App/engineer/role.md', link: '/files/vault/Projects/App/engineer/role.md' }
  const rows = {
    'vault:': [row('vault', '00_System'), row('vault', 'Notes')],
    'vault:Notes': [row('vault', 'Notes/Old'), row('vault', 'Notes/page.html'), row('vault', 'Notes/pic.png', { size: 16 }), row('vault', 'Notes/fake.png', { size: 16 }), row('vault', 'Notes/plan.md', { size: 2048 }), row('vault', 'Notes/report.pdf'), row('vault', 'Notes/sheet.bin'), row('vault', 'Notes/odd.txt', { size: 9 })],
    'vault:Projects/App/engineer': [row('vault', 'Projects/App/engineer/role.md')],
    'vault:00_System': [],
    'system:': [row('system', 'Engineer', { authority: 'runtime' })],
    'system:Engineer': [row('system', 'Engineer/Role.md', { authority: 'runtime', source: roleSource })],
    'spaces:': [],
  }
  const text = {
    'vault:Notes/plan.md': '# The plan\n\nSee [[Daily log]], [[Twin]], [[Gone]], [a site](https://example.org/x), [bad](javascript:alert(1)), [[plan#Next steps]] and <script>alert(1)</script>.\n\n## Next steps\n\nDo it.',
    'vault:Notes/page.html': '<h1>Hi</h1><script>parent.document.title="pwned"</script>',
    'vault:Projects/App/engineer/role.md': '# Role (vault copy)',
    'system:Engineer/Role.md': '# Role',
    'vault:Notes/odd.txt': null,
    ...texts,
  }
  const host = { puts: [], creates: [], started: [], sent: [], revision: 'sha256:r1', conflicts: 0, downloads: [], uploads: [], uploadFailures: {} }
  const find = (root, path) => Object.values(rows).flat().find((r) => r.root === root && r.path === path)
  const routes = {
    'GET /v1/agents': (req, res) => json(res, 200, { agents: [{ id: 'scout', name: 'Scout', emoji: null }] }),
    'GET /v1/providers': (req, res) => json(res, 200, { providers: [{ id: 'claude-code', name: 'Claude Code', connected: true, reason: null, usage: null }] }),
    'GET /v1/limits': (req, res) => json(res, 200, limitsAnswer()),
    'GET /v1/sessions': (req, res) => json(res, 200, { sessions: [], live: 0, live_count: 0, cap: 2 }),
    'GET /v1/sessions/events': (req, res) => {
      res.writeHead(200, { 'content-type': 'text/event-stream' })
      res.write(': open ping=15000\n\n')
    },
    'POST /v1/sessions': (req, res, url, body) => {
      host.started.push(JSON.parse(body || '{}'))
      json(res, 201, { session_id: 'new-1', agent: 'scout', provider: 'claude-code', state: 'running', status: 'idle', title: null, last_message: null, pinned: false, archived: false, record_seq: 0, seen_seq: 0, turns: [], pending_permissions: [], background_tasks: 0, live: true })
    },
    'GET /v1/sessions/new-1/events': (req, res) => {
      res.writeHead(200, { 'content-type': 'text/event-stream' })
      res.write(': open ping=15000\n\n')
    },
    'POST /v1/sessions/new-1/messages': (req, res, url, body) => {
      host.sent.push(JSON.parse(body || '{}'))
      json(res, 200, { accepted: true, seq: 1 })
    },
    'GET /v1/files/roots': (req, res) => json(res, 200, rootsAnswer()),
    'GET /v1/files/recent': (req, res, url) => json(res, 200, {
      who: url.searchParams.get('who') || 'all',
      window: url.searchParams.get('window') || 'default',
      used_fallback: false,
      has_more: false,
      total_shown: 0,
      total_week: 0,
      days: [],
    }),
    'GET /v1/files/stat': (req, res, url) => {
      const r = find(url.searchParams.get('root'), url.searchParams.get('path'))
      if (!r) return json(res, 404, { error: `nothing at ${url.searchParams.get('root')}/${url.searchParams.get('path')}` })
      json(res, 200, r)
    },
    'GET /v1/files/list': (req, res, url) => {
      const root = url.searchParams.get('root')
      const path = url.searchParams.get('path') ?? ''
      if (path === 'Broken') return json(res, 404, { error: 'nothing at vault/Broken' })
      const entries = rows[`${root}:${path}`]
      if (!entries) return json(res, 404, { error: `nothing at ${root}/${path}` })
      const folder = path === '' ? { ...row(root, ''), capabilities: { read: true, download: false, write: false, create: root === 'vault' } } : find(root, path) ?? row(root, path)
      // Two pages for Notes: the answer's own `cursor` is this page's offset,
      // which a page that read it as the next cursor would loop on.
      if (root === 'vault' && path === 'Notes') {
        const at = Number(url.searchParams.get('cursor') ?? 0)
        const page = at === 0 ? entries.slice(0, 5) : entries.slice(5)
        return json(res, 200, listAnswer(folder, page, { cursor: at, next: at === 0 ? '5' : null, total: entries.length }))
      }
      json(res, 200, listAnswer(folder, entries))
    },
    'GET /v1/files/read': (req, res, url) => {
      const key = `${url.searchParams.get('root')}:${url.searchParams.get('path')}`
      if (text[key] === null) return json(res, 415, { error: 'not UTF-8 text', media_type: 'application/octet-stream' })
      if (!(key in text)) return json(res, 404, { error: `nothing at ${key.replace(':', '/')}` })
      const r = find(url.searchParams.get('root'), url.searchParams.get('path'))
      json(res, 200, readAnswer(r, text[key], host.revision))
    },
    'GET /v1/files/download': (req, res, url) => {
      const p = url.searchParams.get('path')
      host.downloads.push(p)
      const bytes = { 'Notes/plan.md': Buffer.from('# Plan\n\nThe next useful step.\n'), 'Notes/pic.png': PNG, 'Notes/fake.png': Buffer.from('<svg onload=alert(1)></svg>'), 'Notes/report.pdf': PDF, 'Notes/fake.pdf': Buffer.from('<html><script>alert(1)</script></html>') }[p]
      if (!bytes) return json(res, 404, { error: `nothing at vault/${p}` })
      res.writeHead(200, {
        'content-type': p.endsWith('.md') ? 'text/markdown' : p.endsWith('.pdf') ? 'application/pdf' : 'image/png',
        'content-disposition': `attachment; filename*=UTF-8''${encodeURIComponent(p.split('/').at(-1))}`,
        'x-content-type-options': 'nosniff',
        'content-security-policy': "sandbox; default-src 'none'",
        'cache-control': 'no-store',
      })
      res.end(bytes)
    },
    'GET /v1/files/resolve': (req, res, url) => {
      const ref = url.searchParams.get('ref')
      if (ref === '[[Daily log]]') return json(res, 200, { status: 'found', resource: row('vault', 'Notes/Daily log.md'), fragment: null })
      if (ref === '[[plan#Next steps]]') return json(res, 200, { status: 'found', resource: find('vault', 'Notes/plan.md'), fragment: 'Next steps' })
      if (ref === '[[Twin]]') return json(res, 200, { status: 'ambiguous', fragment: null, candidates: [row('vault', 'a/Twin.md'), row('vault', 'b/c/Twin.md')], candidates_total: 7, truncated: false })
      if (ref === '[[Gone]]') return json(res, 200, { status: 'missing', target: 'Gone', fragment: null })
      if (ref === '/files/vault/Notes/plan.md') return json(res, 200, { status: 'found', resource: find('vault', 'Notes/plan.md'), fragment: null })
      json(res, 200, { status: 'missing', root: 'vault', path: ref, fragment: null })
    },
    'PUT /v1/files/text': (req, res, url, body) => {
      const b = JSON.parse(body)
      host.puts.push(b)
      if (b.base_revision !== host.revision || host.conflicts > 0) {
        if (host.conflicts > 0) host.conflicts -= 1
        host.revision = `sha256:theirs${host.puts.length}`
        return json(res, 409, { error: 'The file changed since that revision.', current_revision: host.revision })
      }
      const key = `${b.root}:${b.path}`
      text[key] = `---\nlast_modified_by: "[[Someone]]"\n---\n${b.text}`
      host.revision = `sha256:r${host.puts.length + 1}`
      json(res, 200, { ...readAnswer(find(b.root, b.path), text[key], host.revision), provenance: true })
    },
    // The host's upload as the handoff gives it: one `file` part, never an
    // overwrite (a taken name becomes `name 2.ext`), the stored row back.
    // `uploadFailures[name]` makes the next N tries of that name fail as the
    // host would — a test's way to see a retry.
    'POST /v1/files/upload': async (req, res, url, body) => {
      const root = url.searchParams.get('root')
      const folder = url.searchParams.get('path')
      const form = await new Response(Buffer.from(body, 'utf8'), { headers: { 'content-type': req.headers['content-type'] } }).formData()
      const file = form.get('file')
      host.uploads.push({ search: url.search, name: file.name, text: await file.text() })
      const fail = host.uploadFailures[file.name]
      if (fail?.times > 0) {
        fail.times -= 1
        return json(res, fail.status, { error: fail.error })
      }
      const list = rows[`${root}:${folder}`]
      if (!list) return json(res, 404, { error: `nothing at ${root}/${folder}` })
      const dot = file.name.lastIndexOf('.')
      const [base, ext] = dot > 0 ? [file.name.slice(0, dot), file.name.slice(dot)] : [file.name, '']
      let stored = file.name
      for (let i = 2; list.some((r) => r.name === stored); i++) stored = `${base} ${i}${ext}`
      const answer = uploadAnswer(root, folder, stored, file.name, file.size)
      list.push(row(root, answer.path, { size: file.size, capabilities: answer.capabilities }))
      if (/\.(md|txt)$/.test(stored)) text[`${root}:${answer.path}`] = await file.text()
      json(res, 201, answer)
    },
    'POST /v1/files/file': (req, res, url, body) => {
      const b = JSON.parse(body)
      host.creates.push(b)
      const r = row(b.root, b.path, { capabilities: USER })
      rows[`${b.root}:${b.path.split('/').slice(0, -1).join('/')}`]?.push(r)
      text[`${b.root}:${b.path}`] = '---\ncreated_by: "[[Someone]]"\n---\n'
      json(res, 201, { ...readAnswer(r, text[`${b.root}:${b.path}`], 'sha256:new'), provenance: true })
    },
  }
  return { host, routes }
}
