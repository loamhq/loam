import { profileHost } from './agent-profile-host.mjs'
import { row, rootsAnswer, listAnswer } from './fixtures/files-contract.js'
import { shippedPlugins } from '../src/shipped-plugins.js'
export async function u3Fixture() {
  const app = await profileHost()
  const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
  const rows = await shippedPlugins('public')
  for (const row of rows.filter(r => ['dictation', 'read-aloud'].includes(r.id))) row.on = true
  const daily = { id: 'daily-notes', name: 'Daily notes', on: true, effective: true, switch: 'plugin', layer: 'yours', key: 'daily-notes', kind: ['extension'], has: { page_main: false }, source: { kind: 'built' } }
  rows.push(daily)
  const state = { filesCreated: [], daily, folder: 'Journal', keys: { dictation: { set: true, source: 'box' }, 'read-aloud': { set: true, source: 'box' } }, drafts: [], archiveHeld: null, throwaway: null, archived: false, lifecycle: [] }
  let appearance = { phone: { theme: 'latte', mode: 'system', size: 100 }, desktop: { theme: 'latte', mode: 'system', size: 100 } }
  app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, appearance)
  app.routes['PATCH /v1/settings/appearance'] = (req, res, url, raw) => { const body = JSON.parse(raw); for (const kind of ['phone', 'desktop']) if (body[kind]) appearance[kind] = { ...appearance[kind], ...body[kind] }; json(res, appearance) }
  app.routes['GET /v1/plugins'] = (req, res) => json(res, { plugins: rows })
  app.routes['GET /v1/files/read'] = (req, res, url) => json(res, { ...row(url.searchParams.get('root'), url.searchParams.get('path')), text: '# A soul\n\nI make room for good questions. I follow the small details until the whole thing feels clear. A third sentence stays in the file.', revision: '1' })
  app.routes['GET /v1/files/roots'] = (req, res) => json(res, rootsAnswer())
  app.routes['GET /v1/files/stat'] = (req, res, url) => json(res, row(url.searchParams.get('root'), url.searchParams.get('path')))
  app.routes['GET /v1/files/list'] = (req, res, url) => json(res, listAnswer(row(url.searchParams.get('root'), url.searchParams.get('path'), { kind: 'folder' }), []))
  app.routes['POST /v1/files/file'] = (req, res, url, raw) => { const body = JSON.parse(raw); state.filesCreated.push(body.path); json(res, { ...row(body.root, body.path), text: body.text || '', revision: '1' }, 201) }
  app.routes['GET /v1/agents/drafts'] = (req, res, url) => json(res, { drafts: state.drafts.filter(d => d.thread === url.searchParams.get('thread')) })
  app.routes['GET /v1/plugins/daily-notes'] = (req, res) => json(res, { plugin: daily })
  app.routes['PATCH /v1/plugins/daily-notes'] = (req, res, url, raw) => { daily.on = JSON.parse(raw).on; json(res, { plugin: daily }) }
  app.routes['GET /v1/plugins/daily-notes/settings'] = (req, res) => json(res, { id: 'daily-notes', entries: [{ name: 'folder', label: 'Folder' }], values: { folder: state.folder } })
  app.routes['PATCH /v1/plugins/daily-notes/settings'] = (req, res, url, raw) => { state.folder = JSON.parse(raw).values.folder; json(res, { id: 'daily-notes', values: { folder: state.folder } }) }
  app.routes['GET /v1/sessions/live'] = (req, res) => json(res, { sessions: [], live_count: 0, cap: 10 })
  app.routes['GET /v1/sessions/live/events'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open ping=15000\n\n') }
  app.routes['GET /v1/limits'] = (req, res) => json(res, { attachment_max_bytes: 10485760, attachment_types: [] })
  app.routes['GET /v1/integrations/github'] = (req, res) => json(res, { configured: false, connected: false })
  app.routes['GET /v1/integrations/github/accounts'] = (req, res) => json(res, { accounts: [] })
  app.routes['GET /v1/dictation-terms'] = (req, res) => json(res, { text: '# Terms\nFixture', path: 'Terms.md', obsidian_url: 'obsidian://open' })
  app.routes['GET /v1/agents'] = (req, res, url) => json(res, { agents: [...app.f.agents, ...(state.throwaway && (!state.archived || url.searchParams.has('archived')) ? [state.throwaway] : [])] })
  app.routes['GET /v1/threads'] = async (req, res, url) => { const body = await app.f.api('GET', '/threads' + url.search); body.agents = [...app.f.agents, ...(state.throwaway && !state.archived ? [state.throwaway] : [])]; json(res, body) }
  const profile = () => app.profiles.get('throwaway')
  app.routes['POST /v1/agents/new'] = async (req, res) => {
    const thread = await app.f.api('POST', '/threads', { persona: 'sprout' }); app.addThread(thread.id)
    state.lifecycle.push('conversation')
    app.routes[`GET /v1/threads/${thread.id}/steps`] = (req, res) => json(res, { steps: [] })
    const send = app.routes[`POST /v1/threads/${thread.id}/messages`]
    app.routes[`POST /v1/threads/${thread.id}/messages`] = (...args) => {
      state.drafts = [{ draft: 'draft-one', thread: thread.id, rev: 2, name: 'Throwaway', emoji: '🌱', soul: 'I ask small questions. I make useful things.', config: 'A synthetic profile.', ready: true, problems: [] }]
      return send(...args)
    }
    json(res, thread, 201)
  }
  app.routes['POST /v1/agents/drafts/draft-one/confirm'] = (req, res, url, raw) => {
    if (JSON.parse(raw).rev !== 2) return json(res, { error: 'This draft has changed.' }, 409)
    const agent = state.throwaway = { id: 'throwaway', name: 'Throwaway', emoji: '🌱', face: null, archived: null }
    const data = structuredClone(app.profiles.get('sprout')); data.agent = agent; data.identity = { id: agent.id, display: agent.name, emoji: agent.emoji, face: null, archived: null }
    app.profiles.set(agent.id, data); state.drafts = []; state.lifecycle.push('created'); json(res, { agent, first_thread: null, draft: 'draft-one' }, 201)
  }
  app.routes['GET /v1/agents/throwaway'] = (req, res) => json(res, profile())
  app.routes['PATCH /v1/agents/throwaway'] = (req, res, url, raw) => { const body = JSON.parse(raw); if (body.display) { profile().identity.display = body.display; state.throwaway.name = body.display }; json(res, profile()) }
  app.routes['POST /v1/agents/throwaway/archive'] = async (req, res) => {
    if (state.archiveHeld) await state.archiveHeld
    const archived = { at: '2026-11-15T00:00:00Z', by: 'person' }
    state.archived = true; state.throwaway.archived = archived; profile().identity.archived = archived; state.lifecycle.push('archived')
    json(res, { agent: state.throwaway, archived })
  }
  app.routes['POST /v1/agents/throwaway/restore'] = (req, res) => { state.archived = false; state.throwaway.archived = null; profile().identity.archived = null; state.lifecycle.push('restored'); json(res, { agent: state.throwaway, event: {} }) }
  const pixel = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZtpEAAAAASUVORK5CYII=', 'base64')
  app.routes['PUT /v1/agents/throwaway/face'] = (req, res) => { const face = { type: 'image/png', sha: 'fixture', url: '/v1/agents/throwaway/face?sha=fixture' }; state.throwaway.face = face; profile().identity.face = face; json(res, { face }) }
  app.routes['GET /v1/agents/throwaway/face'] = (req, res) => { res.writeHead(200, { 'content-type': 'image/png', 'cache-control': 'public, max-age=31536000, immutable' }); res.end(pixel) }
  app.routes['DELETE /v1/agents/throwaway/face'] = (req, res) => { state.throwaway.face = null; profile().identity.face = null; json(res, { face: null }) }
  for (const id of ['dictation', 'read-aloud']) {
    const key = state.keys[id], secret = () => ({ name: 'GEMINI_API_KEY', label: 'Gemini API key', ...key, checked: { ok: true, why: 'Google accepted the key.' }, refused: null })
    app.routes[`GET /v1/plugins/${id}/secrets`] = (req, res) => json(res, { id, secrets: [secret()] })
    app.routes[`PUT /v1/plugins/${id}/secrets/GEMINI_API_KEY`] = (req, res) => { key.set = true; key.source = 'plugin'; json(res, secret()) }
    app.routes[`POST /v1/plugins/${id}/secrets/GEMINI_API_KEY/check`] = (req, res) => json(res, secret())
    app.routes[`DELETE /v1/plugins/${id}/secrets/GEMINI_API_KEY`] = (req, res) => { key.source = 'box'; json(res, { name: 'GEMINI_API_KEY', ...key }) }
  }
  return { ...app, state, pixel, cleanupThrowaway() { state.lifecycle.push('removed'); app.profiles.delete('throwaway'); state.throwaway = null } }
}
