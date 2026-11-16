import http from 'node:http'
import { once } from 'node:events'
import { readFile, readdir } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
import { shippedPlugins } from '../src/shipped-plugins.js'
export async function pluginHostFixture({ mode = 'light', look = 'latte' } = {}) {
  const remote = http.createServer((req, res) => { res.writeHead(200, { 'content-type': 'text/plain', 'access-control-allow-origin': '*' }); res.end('A plugin can read another origin.') })
  remote.listen(0, '127.0.0.1'); await once(remote, 'listening')
  const remoteURL = `http://127.0.0.1:${remote.address().port}/data`
  const app = await oneProductFixture(), writes = [], json = (res, status, body) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
  const appearance = { phone: { mode, size: 100, theme: look }, desktop: { mode, size: 100, theme: look } }
  app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, 200, appearance)
  app.routes['PATCH /v1/settings/appearance'] = (req, res, url, raw) => { for (const [kind, patch] of Object.entries(JSON.parse(raw))) Object.assign(appearance[kind], patch); json(res, 200, appearance) }
  const base = await shippedPlugins('public')
  const source = { kind: 'link', repo: 'example/proof', ref: 'v1.0.0', ref_kind: 'tag', commit: '1'.repeat(40), url: 'https://github.com/example/proof/tree/' + '1'.repeat(40) }
  const proof = { id: 'proof', name: 'Proof plugin', version: '1.0.0', author: 'Fixture contributors', api: 0, kind: ['extension', 'override'], places: ['rail.section', 'app.overlay', 'command', 'message.renderer'], overrides: ['core.message'], touches: { network: [], record: 'read', memory: 'none', server: false }, source,
    effective: true, on: true, switch: 'plugin', has: { page_main: true }, layer: 'yours', folder: 'fixtures/proof', warnings: [], broken: null }
  proof.declaration = { touches: proof.touches, overrides: proof.overrides, page_code: true }
  const wrong = { id: 'wrong', name: 'A broken folder', layer: 'yours', folder: 'fixtures/wrong', effective: false, switch: null, broken: 'The folder and manifest id do not match.', warnings: [], source: { kind: 'built' } }
  const bad = { ...proof, id: 'bad', folder: 'fixtures/bad', name: 'A failing extension', overrides: [], declaration: { ...proof.declaration, overrides: [] }, source: { kind: 'built' } }
  let rows = [...base.map(row => row.id === 'latte' ? { ...row, effective: false, shadowed_by: 'yours' } : row),
    { ...base.find(row => row.id === 'latte'), layer: 'yours', folder: 'fixtures/latte', effective: true, source: { kind: 'built' }, shadows: { layer: 'shipped', version: '1.0.0' } }, proof, wrong, bad]
  const originalCSS = (await readFile('public/themes/latte/theme.css', 'utf8')).replace(/(:root\[data-look="latte"\]\s*\{)/, '$1\n  --radius-md: 17px;')
  let themeCSS = originalCSS, confirmation = 0
  app.routes['GET /v1/plugins'] = (req, res) => json(res, 200, { api: 0, layers: { shipped: [], yours: 'fixture' }, plugins: rows })
  for (const id of ['latte', 'plain', 'read-aloud', 'dictation', 'proof', 'bad', 'wrong']) {
    app.routes[`GET /v1/plugins/${id}`] = (req, res) => { const all = rows.filter(row => row.id === id); json(res, all.length ? 200 : 404, all.length ? { plugin: { ...(all.find(row => row.effective) ?? all[0]), guide: 'A fixture guide.' }, rows: all } : { error: `No such plugin: ${id}` }) }
    app.routes[`PATCH /v1/plugins/${id}`] = (req, res, url, raw) => { const patch = JSON.parse(raw); writes.push({ method: 'PATCH', id, body: patch }); rows = rows.map(row => row.id === id && row.effective ? { ...row, on: patch.on } : row); json(res, 200, { plugin: rows.find(row => row.id === id && row.effective) }) }
    app.routes[`DELETE /v1/plugins/${id}`] = (req, res) => { writes.push({ method: 'DELETE', id }); const yours = rows.find(row => row.id === id && row.layer === 'yours'); if (!yours) return json(res, 409, { error: 'This plugin is shipped; turn it off instead.' }); rows = rows.filter(row => row !== yours).map(row => row.id === id ? { ...row, effective: true, shadowed_by: null } : row); const reset = []; for (const kind of ['phone', 'desktop']) if (appearance[kind].theme === id) { appearance[kind].theme = 'plain'; reset.push(kind) }; json(res, 200, { removed: id, plugin: rows.find(row => row.id === id) ?? null, appearance_reset: reset }) }
  }
  const preview = () => ({ ...proof, installed: false, replaces: { version: '1.0.0', edited: true }, added: { network: [{ host: 'new.example.invalid', why: 'fetches an optional fixture asset' }], ...(confirmation ? { server: true } : {}) }, declaration: { ...proof.declaration, touches: { ...proof.touches, network: [{ host: 'new.example.invalid', why: 'fetches an optional fixture asset' }], server: confirmation > 0 } }, confirm: confirmation ? 'two' : 'one' })
  app.routes['POST /v1/plugins'] = (req, res, url, raw) => { const body = JSON.parse(raw); writes.push({ method: 'POST', body }); if (body.url.includes('refusal')) return json(res, 400, { error: 'Use a tag, not a moving branch.' }); if (!body.confirm) return json(res, 200, preview()); if (body.confirm === 'one') { confirmation++; return json(res, 409, { error: 'The declaration changed. Review it again.', preview: preview() }) }; rows = rows.filter(row => row.id !== 'proof'); rows.push({ ...proof, version: '1.1.0', on: true }); json(res, 200, { installed: true, id: 'proof', plugin: rows.at(-1) }) }
  for (const row of base.filter(row => row.has.page_main)) for (const file of ['PLUGIN.md', ...(await readdir(path.join('public', row.folder, 'page'))).map(file => `page/${file}`)]) {
    for (const method of ['GET', 'HEAD']) app.routes[`${method} /v1/plugins/${row.id}/files/${file}`] = async (req, res) => { res.writeHead(200, { 'content-type': file.endsWith('.js') ? 'text/javascript; charset=utf-8' : file.endsWith('.css') ? 'text/css; charset=utf-8' : 'text/plain', 'cache-control': 'no-cache' }); res.end(method === 'HEAD' ? '' : await readFile(path.join('public', row.folder, file))) }
  }
  for (const file of ['page/main.js', 'page/label.js', 'assets/proof.svg']) for (const method of ['GET', 'HEAD']) app.routes[`${method} /v1/plugins/proof/files/${file}`] = async (req, res) => { res.writeHead(200, { 'content-type': file.endsWith('.js') ? 'text/javascript; charset=utf-8' : 'image/svg+xml', 'cache-control': 'no-cache' }); res.end(method === 'HEAD' ? '' : await readFile(path.join('test/fixtures/plugins/proof', file))) }
  app.routes['GET /v1/plugins/latte/files/theme.css'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/css; charset=utf-8', 'cache-control': 'no-cache' }); res.end(themeCSS) }
  app.routes['GET /v1/plugins/bad/files/page/main.js'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/javascript; charset=utf-8' }); res.end("export function activate(loam) { loam.places.app.overlay.add({id:'bad',render:()=>loam.ui.h('p',{class:'leaked-plugin'},'leaked')}); throw new Error('Fixture activation failed.'); }") }
  return { ...app, remoteURL, close: async () => { await app.close(); remote.closeAllConnections(); await new Promise(resolve => remote.close(resolve)) }, writes, appearance, rows: () => rows, setRows: value => { rows = value }, setCSS: value => { themeCSS = value }, restoreCSS: () => { themeCSS = originalCSS } }
}
