import { u3Fixture } from './release-u3-fixture.mjs'
export async function u4Fixture({ fresh = false } = {}) {
  const app = await u3Fixture()
  const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' }); res.end(JSON.stringify(body)) }
  const providers = [
    { id: 'claude-code', name: 'Claude Code', connected: !fresh, models: [{ id: 'fable', name: 'Fable' }] },
    { id: 'codex', name: 'Codex', connected: false, models: [] },
    { id: 'antigravity', name: 'Antigravity', connected: false, models: [] },
  ]
  const state = { providers, signed: null, progressReads: 0, apply: [], rollback: 0, checks: 0, missing: false, dirty: false, library: false, restart: false, progress: null }
  for (const p of providers) p.sign_in = { kind: p.id === 'claude-code' ? 'paste-code' : 'device-code', label: 'Sign in', start: `/v1/providers/${p.id}/login`, status: `/v1/providers/${p.id}/login`, code: `/v1/providers/${p.id}/login/code`, cancel: `/v1/providers/${p.id}/login` }
  app.routes['GET /v1/providers'] = (req, res) => json(res, { providers })
  for (const p of providers) {
    const flow = () => state.signed === p.id ? { state: 'done', login: { connected: true } } : { state: 'pending', accepts_code: p.id === 'claude-code', url: 'https://provider.example/sign-in', code: p.id === 'claude-code' ? null : 'DEVICE12', instructions: 'Sign in with your own account, then return here.' }
    app.routes[`POST /v1/providers/${p.id}/login`] = (req, res) => json(res, flow(), 201)
    app.routes[`GET /v1/providers/${p.id}/login`] = (req, res) => json(res, flow())
    app.routes[`POST /v1/providers/${p.id}/login/code`] = (req, res) => { state.signed = p.id; p.connected = true; json(res, flow()) }
    app.routes[`DELETE /v1/providers/${p.id}/login`] = (req, res) => json(res, { state: 'idle' })
  }
  const base = await app.f.api('GET', '/threads/a-quieter-week'), id = base.thread.id
  const iris = { id: 'iris', name: 'Iris', emoji: '🌸' }
  const hello = { ...base.thread, hello: true, default: 'iris', title: 'Hello, you', pinned: true, members: [{ persona: 'iris', engine: 'claude-code', model: 'fable', busy: false }], last: { author: 'iris', text: 'Hello! I’m Iris.', at: new Date().toISOString() } }
  const record = { ...base, thread: hello, messages: [{ id: 'hello-preset', author: 'iris', kind: 'message', preset: true, at: new Date().toISOString(), text: 'Hello! I’m Iris. I’m here to help you find your feet.\n\nWhat would you like me to call you?' }] }
  if (fresh) {
    app.routes['GET /v1/agents'] = (req, res) => json(res, { agents: [iris] })
    app.routes['GET /v1/threads'] = (req, res) => json(res, { threads: [hello], agents: [iris] })
    app.routes['GET /v1/threads/events'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(`: open ping=15000\n\ndata: ${JSON.stringify({ type: 'threads.snapshot', threads: [hello], agents: [iris] })}\n\n`) }
    app.routes[`GET /v1/threads/${id}`] = (req, res) => json(res, record)
    app.routes[`POST /v1/threads/${id}/messages`] = (req, res, url, raw) => {
      const message = { id: 'person-one', author: 'you', kind: 'message', text: JSON.parse(raw).text, at: new Date().toISOString() }
      record.messages.push(message); json(res, { accepted: true, message })
      app.emit(id, { type: 'message.done', message })
    }
    app.routes[`GET /v1/threads/${id}/events`] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(`: open ping=15000\n\ndata: ${JSON.stringify({ type: 'thread.snapshot', thread: id, turn: null })}\n\n`) }
  }
  const library = { any: true, installed: true, edited: ['README.md'], added: [], deleted: [], diff: '--- shipped/README.md\n+++ yours/README.md\n@@ -1 +1 @@\n-Shipped words\n+My own words' }
  const data = () => ({ current: state.missing ? { version: null } : { version: '1.0.0', kind: 'stock', commit: 'fixture' }, check: state.missing ? { error: { code: 'no-version', message: 'There is no manifest.json yet.' } } : { latest: { version: '1.1.0' }, available: true, checked_at: new Date().toISOString(), changes: { added: ['A small improvement.md'], changed: ['README.md'], removed: [] }, library: state.library ? library : null, blocked: state.dirty ? { code: 'dirty', message: 'There are uncommitted changes in this install.', files: ['local.md'] } : null }, running: ['running', 'starting'].includes(state.progress?.outcome), run: state.progress, rollback: { to_version: '0.9.0', from_version: '1.0.0' } })
  app.routes['GET /v1/update'] = (req, res) => json(res, data())
  app.routes['POST /v1/update/check'] = (req, res) => { state.checks++; json(res, data()) }
  app.routes['POST /v1/update/apply'] = (req, res, url, raw) => {
    const body = JSON.parse(raw); state.apply.push(body)
    if (state.dirty) return json(res, { error: 'There are uncommitted changes in this install.', refusal: { code: 'dirty', files: ['local.md'] } }, 409)
    if (state.library && !body.accept_library_edits) return json(res, { error: 'The library has your edits.', refusal: { code: 'library-edits', library } }, 409)
    state.progress = { id: 'fixture-run', action: 'apply', outcome: 'starting', from_version: '1.0.0', to_version: '1.1.0', steps: [{ name: 'preflight', words: 'Checking the install', status: 'done' }, { name: 'broker', words: 'Restarting the broker when every session is idle', status: 'running', detail: 'Waiting for an agent to finish' }] }; json(res, { run: state.progress }, 202)
  }
  app.routes['POST /v1/update/rollback'] = (req, res) => { state.rollback++; state.progress = { id: 'rollback', action: 'rollback', outcome: 'rolled-back', steps: [{ name: 'restore', words: 'Restoring the previous release', status: 'done' }] }; json(res, { run: state.progress }, 202) }
  app.routes['GET /v1/update/progress'] = (req, res) => {
    state.progressReads++
    if (state.restart && state.progressReads <= 2) return json(res, { error: 'Restarting' }, 503)
    state.progress = { ...state.progress, outcome: state.progressReads > 2 ? 'applied' : 'running' }; json(res, state.progress)
  }
  return { ...app, u4: state, hello, record }
}
