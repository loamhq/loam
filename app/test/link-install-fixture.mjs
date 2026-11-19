// Install from a link against a fixture broker (2026-11-18): the Market's
// fixture (test/market-fixture.mjs) with POST /v1/plugins answering links as
// lib/loam_plugins.install does (tests/test_plugins.py proves those answers
// against real git repositories): a repository of several plugins is a
// `choose`; a pick, or a link to one, is a preview with its pictures as
// data: addresses; a branch or a repository with no release says so in
// `note`; and each refusal is a sentence with its `reason`. The plugins are
// Loam's own listed ones (plugins/_listed/), whose pictures are here.
import { readFile } from 'node:fs/promises'
import { marketFixture } from './market-fixture.mjs'
import { linkShape } from '../public/market.js'

const LISTED = new URL('../../plugins/_listed/', import.meta.url)
const webp = async file => `data:image/webp;base64,${(await readFile(new URL(file, LISTED))).toString('base64')}`
const svg = async file => `data:image/svg+xml;base64,${(await readFile(new URL(file, LISTED))).toString('base64')}`
const none = { network: [], record: 'none', memory: 'none', server: false }

export const REPO = 'loamhq/plugins'
export async function linkPlugins() {
  return [
    { path: 'autumn', id: 'autumn', name: 'Autumn', version: '1.0.1', kind: ['theme'], description: 'Rust, amber and paper.', tag: 'autumn-v1.0.1',
      declaration: { touches: none, overrides: [], page_code: false }, pictures: { cover: await webp('autumn/cover.webp'), cover_dark: await webp('autumn/cover-dark.webp') } },
    { path: 'foundry', id: 'foundry', name: 'Foundry', version: '1.0.1', kind: ['theme'], description: 'Iron, soot and a warm forge light.', tag: 'foundry-v1.0.1',
      declaration: { touches: none, overrides: [], page_code: false }, pictures: { cover: await webp('foundry/cover.webp') } },
    { path: 'images', id: 'images', name: 'Images', version: '1.1.1', kind: ['extension'], description: 'Lets your agents make and edit pictures.', tag: 'images-v1.1.1',
      declaration: { touches: { network: [{ host: 'api.openai.com', why: 'Sends the prompt and any source images to OpenAI’s image models' }], record: 'none', memory: 'none', server: true,
        keys: [{ provider: 'openai', name: 'OpenAI', why: 'Makes and changes images', optional: true }] }, overrides: [], page_code: false },
      pictures: { icon: await svg('images/assets/icon.svg') } },
    { path: 'syncthing', id: 'syncthing', name: 'Syncthing', version: '1.0.0', kind: ['extension'], description: 'Keeps the vault in step with your other devices.', tag: 'syncthing-v1.0.0',
      declaration: { touches: { network: [{ host: 'your own devices', why: 'syncs vault files between them' }], record: 'none', memory: 'none', server: true }, overrides: [], page_code: false },
      pictures: { icon: await svg('syncthing/assets/icon.svg') } },
  ]
}

const refusal = {
  'friend/secret-plugin': [404, 'unreachable', 'Nothing could be read at github.com/friend/secret-plugin. Check the link. If the repository is private, connect GitHub in Settings › Connections first, then try again.'],
  'friend/notes': [422, 'no-plugin', 'There’s no Loam plugin in github.com/friend/notes. A Loam plugin is a folder with a PLUGIN.md in it; check the link with whoever shared it.'],
  'friend/half-done': [422, 'manifest', 'The plugin in half-done has a PLUGIN.md Loam can’t read, so it can’t be installed until its maker fixes it: PLUGIN.md line 3: `version` is not major.minor.patch.'],
}

export async function linkFixture(options = {}) {
  const app = await marketFixture(options), plugins = await linkPlugins(), delay = { ms: options.slow ?? 250 }
  const market = app.routes['POST /v1/plugins']
  const json = (res, status, body) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
  const installedRows = new Map()
  const read = app.routes['GET /v1/plugins']
  app.routes['GET /v1/plugins'] = (req, res, url, raw) => {
    const end = res.end.bind(res)
    res.end = body => { try { const out = JSON.parse(body); out.plugins.push(...installedRows.values()); return end(JSON.stringify(out)) } catch { return end(body) } }
    return read(req, res, url, raw)
  }
  app.routes['POST /v1/plugins'] = async (req, res, url, raw) => {
    const body = JSON.parse(raw)
    const shape = linkShape(body.url)
    if (!shape?.ok || ![REPO, 'friend/lantern', ...Object.keys(refusal)].includes(shape.repo)) return market(req, res, url, raw)
    app.writes.push({ method: 'POST', path: '/v1/plugins', body, link: true })
    await new Promise(resolve => setTimeout(resolve, delay.ms))
    if (refusal[shape.repo]) { const [status, reason, error] = refusal[shape.repo]; return json(res, status, { error, reason, repo: shape.repo }) }
    let plugin, ref, refKind = 'tag', note = null
    if (shape.repo === 'friend/lantern') {
      plugin = { path: '', id: 'lantern', name: 'Lantern', version: '0.3.0', kind: ['extension'], description: 'A small light in the corner that glows while an agent is working.', declaration: { touches: none, overrides: [], page_code: true }, pictures: {} }
      ref = '9c1e0d4b7a5f3e2d1c0b9a8f7e6d5c4b3a291807'; refKind = 'commit'
      note = 'It has no release yet, so this installs the newest commit on main (9c1e0d4). It stays at that commit until you install it again.'
    } else {
      const tree = /\/tree\/([^/?#]+)(?:\/([^?#]+))?/.exec(body.url)
      const path = body.path ?? tree?.[2]?.replace(/\/$/, '') ?? (tree?.[1]?.match(/^(.+)-v\d/)?.[1]) ?? ''
      plugin = plugins.find(p => p.path === path)
      if (!plugin) return json(res, 200, { installed: false, repo: REPO, ref: null, choose: plugins.map(({ path, id, name, version, kind, description, pictures }) => ({ path, id, name, version, kind, author: 'Loam contributors', description, ...(pictures.icon ? { icon: pictures.icon } : {}) })) })
      ref = body.ref ?? plugin.tag
      if (tree?.[1] === 'main') { ref = '0fa482df374d64a21e62a52b3ba15f80fce19078'; refKind = 'commit'; note = 'main is a branch, so this installs the commit it is on now (0fa482d). It stays at that commit until you install it again.' }
    }
    const repo = shape.repo
    const source = { kind: 'link', repo, path: plugin.path, ref, ref_kind: refKind, commit: refKind === 'commit' ? ref : 'c'.repeat(40), url: `https://github.com/${repo}/tree/${'c'.repeat(40)}/${plugin.path}` }
    const preview = { installed: false, id: plugin.id, name: plugin.name, version: plugin.version, author: 'Loam contributors', api: 0, kind: plugin.kind, places: [], source,
      declaration: plugin.declaration, added: {}, replaces: installedRows.has(plugin.id) ? { version: installedRows.get(plugin.id).version, source: installedRows.get(plugin.id).source, edited: false, broken: null } : null,
      shadows: null, warnings: [], description: plugin.description, note, request: { url: repo, path: plugin.path, ref }, link: `https://github.com/${repo}/tree/${ref}/${plugin.path}`,
      pictures: plugin.pictures, confirm: `ok-${plugin.id}-${ref}` }
    if (!body.confirm) return json(res, 200, preview)
    if (body.confirm !== preview.confirm) return json(res, 409, { error: 'It changed.', preview })
    const row = { id: plugin.id, name: plugin.name, version: plugin.version, author: 'Loam contributors', api: 0, kind: plugin.kind, places: [], overrides: [], layer: 'yours', folder: `fixtures/${plugin.id}`,
      effective: true, on: true, switch: plugin.kind.includes('theme') ? 'appearance' : 'plugin', broken: null, warnings: [], has: { page_main: false, theme: plugin.kind.includes('theme') },
      description: plugin.description, touches: plugin.declaration.touches, declaration: plugin.declaration, source: { ...source, ...(body.via ? { via: body.via } : {}) } }
    installedRows.set(plugin.id, row)
    app.routes[`GET /v1/plugins/${plugin.id}`] = (req, res) => json(res, 200, { plugin: row })
    json(res, 200, { installed: true, id: plugin.id, plugin: row, replaced: null, source, server: null })
  }
  return { ...app, linkPlugins: plugins, installedRows, delay }
}
