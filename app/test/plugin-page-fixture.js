// A host-owned page package and gateway, served only by a local fixture.
import { registerHooks } from 'node:module'
import { readFile } from 'node:fs/promises'
import { pathToFileURL } from 'node:url'
import path from 'node:path'

export function nodePluginPage(id, root) {
  const prefix = `/api/plugins/${id}/files/`
  return registerHooks({ resolve(specifier, context, next) {
    if (specifier.startsWith(prefix)) return next(pathToFileURL(path.resolve(root, specifier.slice(prefix.length).split('?')[0])).href, context)
    if (specifier.startsWith('/') && !specifier.startsWith('file:') && ['time.js', 'clipboard.js'].includes(specifier.slice(1))) return next(pathToFileURL(path.resolve('public', specifier.slice(1))).href, context)
    return next(specifier, context)
  } })
}
export function pluginPageRoutes(routes, row, root, files = ['page/main.js', 'page/view.js', 'page/style.css']) {
  routes['GET /v1/plugins'] = (req, res) => {
    res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ plugins: [row] }))
  }
  routes[`GET /v1/plugins/${row.id}`] = (req, res) => {
    res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ plugin: { ...row, guide: 'A fixture page package.' }, rows: [row] }))
  }
  for (const file of files) for (const method of ['GET', 'HEAD']) {
    routes[`${method} /v1/plugins/${row.id}/files/${file}`] = async (req, res) => {
      res.writeHead(200, { 'content-type': file.endsWith('.css') ? 'text/css' : 'text/javascript', 'cache-control': 'no-store' })
      res.end(method === 'HEAD' ? '' : await readFile(path.join(root, file)))
    }
  }
}
export function fixtureGateway(app, prefix, routes) {
  const seen = [], [serve] = app.server.listeners('request')
  app.server.removeListener('request', serve)
  app.server.on('request', async (req, res) => {
    const url = new URL(req.url, app.url)
    if (!url.pathname.startsWith(prefix)) return serve(req, res)
    const chunks = []; for await (const chunk of req) chunks.push(chunk)
    const body = Buffer.concat(chunks).toString('utf8')
    seen.push({ method: req.method, path: url.pathname, auth: req.headers.authorization, body })
    const handler = routes[`${req.method} ${url.pathname}`]
    if (handler) return handler(req, res, url, body)
    res.writeHead(405, { 'content-type': 'application/json' }); res.end(JSON.stringify({ error: 'No fixture route.' }))
  })
  return seen
}
