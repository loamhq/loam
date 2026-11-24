import { shippedPlugins } from './shipped-plugins.js'
// loam-ui, the web half.
//
// One HTTP server on one port. It serves the page, and it relays the page's
// calls to the session broker — which runs on the host, holds the coding CLI's
// login, and decides what a session may write. This half does neither.
//
// The browser never sees BROKER_TOKEN: every call it makes lands here, and the
// token is added on the way out.

import http from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { readConfig, configComplaints } from './config.js'
import { buildPreview, PREVIEW_POLICY, PREVIEW_LIMITS } from './preview.js'
import { createContent } from './content.js'
import { fileLink } from './file-links.js'
import { createBroker, BrokerNotConfigured } from './broker.js'
import { createStatic } from './static.js'
import { createLog, requestId, loggedQuery } from './log.js'
import { appearanceBoot } from './appearance.js'
import { loadThemes, marketThemeCSS, themeFontsCSS } from './themes.js'
import { INSTALLATION_DEFAULTS } from '../public/installation.js'
import { createAppMedia } from './app-media.js'
import { serverFacts, signedIn } from './about.js'
import { archiveFilename, shouldExclude, streamZipArchive } from './archive.js'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const MAX_BODY = 1 << 20 // a message from a phone; not a file upload
// How often a stream with nothing to say says so. The page needs this number to
// tell a quiet connection from one that has gone — so it is announced on the
// stream's opening comment rather than left for the page to assume.
const KEEPALIVE_MS = 15000

const SECURITY_HEADERS = {
  'x-content-type-options': 'nosniff',
  'referrer-policy': 'strict-origin-when-cross-origin',
  'content-security-policy':
    "default-src 'self'; script-src 'self'; connect-src 'self' http: https: ws: wss:; img-src 'self' data: blob: http: https:; media-src 'self' blob: data: http: https:; font-src 'self' data: http: https:; " +
    // Installed page code is trusted. Network declarations are informative,
    // not per-plugin allowlists; all plugins share ordinary browser/CORS rules.
    // Executable modules and styles still come from this origin, never eval.
    // A Files image or PDF is drawn from an object URL of bytes this page
    // fetched and checked; an HTML file is framed from `/api/files/preview`,
    // which answers under a sandboxing policy of its own. Apps may frame the
    // supplied site platform; a site must also allow this page as an ancestor.
    "frame-src 'self' blob: http: https:; object-src 'none'; " +
    // One form ever leaves this page: the GitHub app manifest, posted from the
    // person's browser to GitHub's own create page. Nowhere else.
    "base-uri 'none'; form-action https://github.com; frame-ancestors 'none'",
}

export function createApp({
  config = readConfig(),
  broker = createBroker(config),
  appMedia = null,
  publicDir = path.join(ROOT, 'public'),
  log = createLog({ level: config.logLevel }),
} = {}) {
  const serveStatic = createStatic(publicDir)
  appMedia ??= createAppMedia(config.appMedia, { log })
  const installation = config.installation ?? INSTALLATION_DEFAULTS
  const securityHeaders = { ...SECURITY_HEADERS, 'content-security-policy': SECURITY_HEADERS['content-security-policy'].replace("frame-src 'self' blob:", `frame-src 'self' blob: https://*.${installation.appsDomain}${config.filesContentOrigin ? ` ${config.filesContentOrigin}` : ''}`) }
  const fallbackThemes = loadThemes()
  async function installedThemes() {
    try {
      const out = await broker.json('GET', '/v1/plugins', undefined, { signal: AbortSignal.timeout(3000) })
      if (out.status !== 200) return await fallbackThemes
      const body = JSON.parse(out.body)
      if (!Array.isArray(body.plugins)) return await fallbackThemes
      return await loadThemes(undefined, { rows: body.plugins, readTheme: async row => {
        const response = await broker.file(`/v1/plugins/${encodeURIComponent(row.id)}/files/theme.css`, { signal: AbortSignal.timeout(5000) })
        const chunks = []; let size = 0
        if (response.stream) for await (const chunk of response.stream) { size += chunk.length; if (size > MAX_BODY) { response.stream.destroy(); throw new Error('Theme CSS is too large.') }; chunks.push(chunk) }
        const text = Buffer.concat(chunks).toString('utf8')
        if (response.status !== 200) { let error; try { error = JSON.parse(text).error } catch {}; throw new Error(error || text || `Theme CSS could not be read (${response.status}).`) }
        return text
      } })
    } catch { return await fallbackThemes }
  }
  const content = createContent({ origin: config.filesContentOrigin, broker })
  const contentHost = config.filesContentOrigin ? new URL(config.filesContentOrigin).host : null

  // A 512 MB video takes over an hour at 1 Mbps. Keep the header timeout
  // and give the request body time to finish. The video relay also watches
  // for an idle connection, independently of the total request time.
  const server = http.createServer({ requestTimeout: 2 * 60 * 60 * 1000 }, async (req, res) => {
    for (const [k, v] of Object.entries(securityHeaders)) res.setHeader(k, v)
    // The one page this app frames itself (2026-11-19): a theme's preview
    // scene (public/theme-scene.html), drawn in the Market and Settings.
    if (req.url.split('?')[0] === '/theme-scene.html') res.setHeader('content-security-policy', securityHeaders['content-security-policy'].replace("frame-ancestors 'none'", "frame-ancestors 'self'"))
    const url = new URL(req.url, 'http://placeholder')
    const pathname = url.pathname
    const note = track(req, res, url, log)

    try {
      // A path with a `.` or `..` segment in it is refused before it is
      // normalised. No browser sends one, and a path that names one route and
      // is normalised into another is a path a proxy in front of this app
      // could match as the first and this app would serve as the second.
      if (DOT_SEGMENT.test(req.url.split('?')[0])) {
        return sendJson(res, 400, { error: 'A path with . or .. segments in it is not one this app serves.' })
      }

      // Host is preserved by the content proxy. Never trust Forwarded-Host:
      // that host gets file bytes only, never the app, its API or its assets.
      if (contentHost && req.headers.host?.toLowerCase() === contentHost.toLowerCase()) {
        return await content.serve(req, res, url)
      }
      if (pathname.startsWith('/content/')) return send(res, 404, 'text/plain', 'not found')

      // The host's health check, and nothing else, reads this. It answers the
      // moment this app can serve — it is not a claim about the broker.
      if (pathname === '/up') {
        return send(res, 200, 'text/plain; charset=utf-8', 'ok')
      }

      if (pathname.startsWith('/api/')) {
        // SOP prevents reading; this prevents causing a relay action with a
        // simple fetch, a form, an image, or a popup from another origin too.
        const site = req.headers['sec-fetch-site']
        const origin = req.headers.origin
        res.setHeader('vary', 'Sec-Fetch-Site, Origin')
        if ((site && !['same-origin', 'none'].includes(site)) || (origin && origin !== `https://${req.headers.host}` && origin !== `http://${req.headers.host}`)) {
          return sendJson(res, 403, { error: 'The relay accepts same-origin browser requests only.' })
        }
        return await api(req, res, url, pathname.slice('/api'.length))
      }

      // Public preferences only; a fresh navigation reads the host before paint.
      if (pathname === '/appearance-state.js' && ['GET', 'HEAD'].includes(req.method)) {
        res.setHeader('cache-control', 'no-store')
        const identities = `globalThis.LOAM_INSTALLATION=${JSON.stringify(installation).replaceAll('<', '\\u003c')};\nglobalThis.LOAM_AVATAR_VARIANTS=${JSON.stringify(config.avatarVariants ?? {}).replaceAll('<', '\\u003c')};\n`
        return send(res, 200, 'text/javascript; charset=utf-8', req.method === 'HEAD' ? '' : identities + await appearanceBoot(broker, (await installedThemes()).themes))
      }

      if (pathname === '/shipped-plugins.json' && ['GET', 'HEAD'].includes(req.method)) {
        res.setHeader('cache-control', 'no-cache')
        return sendJson(res, 200, { plugins: await shippedPlugins(publicDir) })
      }

      // An installed theme's display faces (2026-11-18): its own fonts.css,
      // read from its folder through the broker and held to @font-face rules
      // whose files are beside it (themeFontsCSS). A theme with none answers
      // an empty sheet, so the page can ask for any look it wears.
      const fonts = /^\/theme-fonts\/([a-z][a-z0-9-]*)\.css$/.exec(pathname)
      if (fonts && ['GET', 'HEAD'].includes(req.method)) {
        res.setHeader('cache-control', 'no-cache')
        let css = ''
        try {
          const out = await broker.file(`/v1/plugins/${fonts[1]}/files/fonts.css`, { signal: AbortSignal.timeout(5000) })
          const chunks = []; let size = 0
          if (out.stream) for await (const chunk of out.stream) { size += chunk.length; if (size > MAX_BODY) { out.stream.destroy(); throw new Error('too large') } chunks.push(chunk) }
          if (out.status === 200) css = themeFontsCSS(fonts[1], Buffer.concat(chunks).toString('utf8'))
        } catch { css = '' }
        return send(res, 200, 'text/css; charset=utf-8', req.method === 'HEAD' ? '' : css)
      }

      if (pathname === '/themes.css' && ['GET', 'HEAD'].includes(req.method)) {
        res.setHeader('cache-control', 'no-cache')
        return send(res, 200, 'text/css; charset=utf-8', req.method === 'HEAD' ? '' : (await installedThemes()).css)
      }

      if (req.method === 'GET' || req.method === 'HEAD') {
        // Where GitHub sends the browser back to, with a query the page reads
        // and hands to the broker. The page itself, at a path of GitHub's.
        // A Files address — `/files/<root>/<path>` — is the page, which reads
        // the rest of it. Loaded directly, refreshed or come back to, it lands
        // on the same folder or file.
        if (GITHUB_RETURNS.has(pathname) || ['/apps', '/panel'].includes(pathname) || pathname === '/files' || pathname.startsWith('/files/') || /^\/(?:threads|sessions)\/[^/]+\/?$/.test(pathname)) {
          await serveStatic(req, res, '/')
          note.static = true
          return
        }
        if (await serveStatic(req, res, pathname)) {
          note.static = true
          return
        }
      }
      return send(res, 404, 'text/plain; charset=utf-8', 'not found\n')
    } catch (err) {
      if (err instanceof BrokerNotConfigured) {
        return sendJson(res, 503, { error: err.message })
      }
      // Said once, on this request's own line, under its id.
      note.error = String(err?.message ?? err)
      note.stack = err?.stack ?? null
      if (!res.headersSent) return sendJson(res, 502, { error: String(err?.message ?? err) })
      res.end()
    }
  })

  // Every route the page is allowed to reach, and the broker route it becomes.
  // An allowlist and not a blanket proxy: a route the broker grows later is a
  // line added here on purpose, not one the page silently acquires.
  async function api(req, res, url, rest) {
    const method = req.method
    const by = url.searchParams.has('by') ? `?${new URLSearchParams({ by: url.searchParams.get('by') })}` : ''

    const pluginFile = /^\/plugins\/([^/]+)\/files\/(.+)$/.exec(rest)
    if (pluginFile) {
      if (!['GET', 'HEAD'].includes(method)) return sendJson(res, 405, { error: 'Use GET or HEAD for plugin files.' })
      const segments = pluginFile[2].split('/').map(dec)
      if (segments.some(part => !part || ['.', '..'].includes(part) || /[\\\x00]/.test(part))) return sendJson(res, 400, { error: 'Invalid plugin file path.' })
      const target = `/v1/plugins/${encodeURIComponent(dec(pluginFile[1]))}/files/${segments.map(encodeURIComponent).join('/')}`
      const out = await broker.file(target, { method, ifNoneMatch: req.headers['if-none-match'] ?? null, signal: AbortSignal.timeout(30000) })
      const respHeaders = { 'cache-control': out.cacheControl ?? 'no-cache', 'x-content-type-options': 'nosniff' }
      if (out.etag) respHeaders.etag = out.etag
      if (out.status !== 304) respHeaders['content-type'] = out.contentType ?? 'application/octet-stream'
      res.writeHead(out.status, respHeaders)
      if (method === 'HEAD' || out.status === 304 || !out.stream) { out.stream?.destroy(); return res.end() }
      return await new Promise((resolve, reject) => { out.stream.on('error', reject); res.on('close', () => { out.stream.destroy(); resolve() }); out.stream.pipe(res); res.on('finish', resolve) })
    }
    // The marketplace's list (2026-11-17): a read. An install from it is
    // POST /plugins with the entry's link, as any link.
    if (rest === '/marketplace') {
      if (method !== 'GET') return sendJson(res, 405, { error: 'Use GET for the marketplace.' })
      return await relayJson(res, 'GET', `/v1/marketplace${url.searchParams.get('refresh') === '1' ? '?refresh=1' : ''}`, undefined, { signal: AbortSignal.timeout(20000) })
    }
    // A listing's own page (2026-11-18, Settings › Market): what the plugin
    // at its link declares, and its icon, covers, README pictures and theme,
    // read by the broker
    // from the link before anything is installed. A theme's CSS is held to
    // the installed themes' rules here, so nothing but tokens reaches the page.
    // Its screenshots (`shot/<n>`, `shot/<n>-dark`) and a theme's own faces
    // (`fonts.css`, held to @font-face rules over its `font/<file>`s) came
    // on 2026-11-19, for the gallery and the theme's live preview.
    const listing = /^\/marketplace\/([a-z][a-z0-9-]*)(?:\/(icon|image|cover|cover-dark|theme\.css|fonts\.css|shot\/[0-5](?:-dark)?|font\/[A-Za-z0-9_-]+\.woff2?|readme\/(?:[A-Za-z0-9_-]+\/)*[A-Za-z0-9_-]+\.(?:webp|avif|jpe?g|png)))?$/.exec(rest)
    if (listing) {
      if (!['GET', 'HEAD'].includes(method)) return sendJson(res, 405, { error: 'Use GET for a listing.' })
      const [, id, file] = listing
      if (!file) return await relayJson(res, 'GET', `/v1/marketplace/${id}${url.searchParams.get('refresh') === '1' ? '?refresh=1' : ''}`, undefined, { signal: AbortSignal.timeout(200000) })
      if (file === 'fonts.css') {
        let css = ''
        try {
          const out = await broker.file(`/v1/marketplace/${id}/fonts.css`, { signal: AbortSignal.timeout(30000) })
          const chunks = []; let size = 0
          if (out.stream) for await (const chunk of out.stream) { size += chunk.length; if (size > MAX_BODY) { out.stream.destroy(); throw new Error('too large') } chunks.push(chunk) }
          if (out.status === 200) css = themeFontsCSS(id, Buffer.concat(chunks).toString('utf8'), `/api/marketplace/${id}/font/`)
        } catch { css = '' }
        res.writeHead(200, { 'content-type': 'text/css; charset=utf-8', 'cache-control': 'private, max-age=600', 'x-content-type-options': 'nosniff' })
        return res.end(method === 'HEAD' ? '' : css)
      }
      if (file !== 'theme.css') {
        const out = await broker.file(`/v1/marketplace/${id}/${file}`, { method, signal: AbortSignal.timeout(200000) })
        const font = file.startsWith('font/')
        const type = out.status === 200 && (font ? /^font\/woff2?$/ : /^image\//).test(out.contentType ?? '') ? out.contentType : 'application/json'
        res.writeHead(out.status, { 'content-type': type, 'cache-control': out.status === 200 ? 'private, max-age=600' : 'no-store', 'x-content-type-options': 'nosniff',
          ...(type === 'image/svg+xml' ? { 'content-security-policy': "default-src 'none'; style-src 'unsafe-inline'; sandbox" } : {}) })
        if (method === 'HEAD' || !out.stream) { out.stream?.destroy(); return res.end() }
        return await new Promise((resolve, reject) => { out.stream.on('error', reject); res.on('close', () => { out.stream.destroy(); resolve() }); out.stream.pipe(res); res.on('finish', resolve) })
      }
      const about = await broker.json('GET', `/v1/marketplace/${id}`, undefined, { signal: AbortSignal.timeout(200000) })
      if (about.status !== 200) { res.writeHead(about.status, { 'content-type': 'application/json', 'cache-control': 'no-store' }); return res.end(about.body) }
      const out = await broker.file(`/v1/marketplace/${id}/theme.css`, { signal: AbortSignal.timeout(30000) })
      const chunks = []; let size = 0
      if (out.stream) for await (const chunk of out.stream) { size += chunk.length; if (size > MAX_BODY) { out.stream.destroy(); return sendJson(res, 413, { error: 'The theme is too large.' }) } chunks.push(chunk) }
      if (out.status !== 200) return sendJson(res, out.status, { error: `${id} has no theme to try.` })
      let css
      try { css = await marketThemeCSS(id, Buffer.concat(chunks).toString('utf8'), JSON.parse(about.body).theme?.options ?? []) }
      catch (error) { return sendJson(res, 422, { error: `${id}’s theme is not one Loam can wear: ${error.message}` }) }
      res.writeHead(200, { 'content-type': 'text/css; charset=utf-8', 'cache-control': 'private, max-age=600', 'x-content-type-options': 'nosniff' })
      return res.end(method === 'HEAD' ? '' : css)
    }
    if (rest === '/plugin-updates') {
      if (method !== 'GET') return sendJson(res, 405, { error: 'Use GET for plugin updates.' })
      return await relayJson(res, method, '/v1/plugin-updates', undefined, { signal: AbortSignal.timeout(420000) })
    }
    if (rest === '/plugins') {
      if (!['GET', 'POST'].includes(method)) return sendJson(res, 405, { error: 'Use GET or POST for plugins.' })
      return await relayJson(res, method, '/v1/plugins', method === 'POST' ? await readBody(req) : undefined, { signal: AbortSignal.timeout(method === 'POST' ? 420000 : 5000) })
    }
    // Shared keys (2026-11-18): Settings › Connections › API keys. The broker never
    // answers a whole value on these routes; the app server's own key reads
    // go to /v1/plugins/<id>/secrets/<name>/value, which this relay never
    // passes.
    const keysAll = /^\/keys(?:\/([^/]+))?$/.exec(rest)
    const keysCheck = /^\/keys\/([^/]+)\/check$/.exec(rest)
    const keysPlugin = /^\/keys\/([^/]+)\/plugins\/([^/]+)$/.exec(rest)
    const keysProject = /^\/keys\/([^/]+)\/projects\/([^/]+)$/.exec(rest)
    if (keysAll || keysCheck || keysPlugin || keysProject) {
      if (rest.split('/').some(part => /[/\\\x00]/.test(dec(part)))) return sendJson(res, 400, { error: 'Invalid key path.' })
      const allowed = keysCheck ? ['POST'] : keysPlugin || keysProject ? ['PUT', 'DELETE'] : keysAll[1] ? ['GET', 'PUT', 'DELETE'] : ['GET']
      if (!allowed.includes(method)) return sendJson(res, 405, { error: 'This operation is not available for keys.' })
      const target = '/v1' + rest.split('/').map(part => encodeURIComponent(dec(part))).join('/')
      return await relayJson(res, method, target, ['POST', 'PUT'].includes(method) ? await readBody(req) : undefined, { signal: AbortSignal.timeout(30000) })
    }
    const pluginSettings = /^\/plugins\/[^/]+\/settings$/.test(rest)
    const pluginSecret = /^\/plugins\/[^/]+\/secrets(?:\/[^/]+)?$/.test(rest)
    const secretCheck = /^\/plugins\/[^/]+\/secrets\/[^/]+\/check$/.test(rest)
    if (pluginSettings || pluginSecret || secretCheck) {
      if (rest.split('/').some(part => /[/\\\x00]/.test(dec(part)))) return sendJson(res, 400, { error: 'Invalid plugin setting path.' })
      const allowed = pluginSettings ? ['GET', 'PATCH'] : secretCheck ? ['POST'] : rest.endsWith('/secrets') ? ['GET'] : ['PUT', 'DELETE']
      if (!allowed.includes(method)) return sendJson(res, 405, { error: 'This operation is not available for plugin settings.' })
      const target = '/v1' + rest.split('/').map(part => encodeURIComponent(dec(part))).join('/')
      return await relayJson(res, method, target + by, ['POST', 'PATCH', 'PUT'].includes(method) ? await readBody(req) : undefined)
    }
    const restart = /^\/plugins\/([^/]+)\/restart$/.exec(rest)
    if (restart) {
      if (method !== 'POST') return sendJson(res, 405, { error: 'Use POST to restart a plugin.' })
      return await relayJson(res, method, `/v1/plugins/${encodeURIComponent(dec(restart[1]))}/restart${by}`, await readBody(req))
    }
    const plugin = /^\/plugins\/([^/]+)$/.exec(rest)
    if (plugin) {
      if (!['GET', 'PATCH', 'DELETE'].includes(method)) return sendJson(res, 405, { error: 'Use GET, PATCH or DELETE for a plugin.' })
      return await relayJson(res, method, `/v1/plugins/${encodeURIComponent(dec(plugin[1]))}`, method === 'PATCH' ? await readBody(req) : undefined)
    }
    const permission = /^\/sessions\/([^/]+)\/permissions\/([^/]+)$/.exec(rest)
    if (permission) {
      if (method !== 'POST') return sendJson(res, 405, { error: 'Use POST to answer a permission request.' })
      return await relayJson(res, method, `/v1/sessions/${encodeURIComponent(dec(permission[1]))}/permissions/${encodeURIComponent(dec(permission[2]))}`, await readBody(req))
    }

    if (rest === '/settings/git') {
      if (!['GET', 'PATCH'].includes(method)) return sendJson(res, 405, { error: 'Use GET or PATCH for the git identity.' })
      return await relayJson(res, method, '/v1/settings/git', method === 'PATCH' ? await readBody(req) : undefined)
    }

    if (rest === '/settings/composer') {
      if (!['GET', 'PATCH'].includes(method)) return sendJson(res, 405, { error: 'Use GET or PATCH for composer settings.' })
      return await relayJson(res, method, '/v1/settings/composer', method === 'PATCH' ? await readBody(req) : undefined)
    }

    if (rest === '/settings/appearance') {
      if (!['GET', 'PATCH'].includes(method)) return sendJson(res, 405, { error: 'Use GET or PATCH for appearance.' })
      return await relayJson(res, method, '/v1/settings/appearance', method === 'PATCH' ? await readBody(req) : undefined)
    }

    // Resolve old session addresses before the generic thread-id route.
    if (rest === '/threads/imported') {
      if (method !== 'GET') return sendJson(res, 405, { error: 'Use GET for imported threads.' })
      const query = new URLSearchParams()
      if (url.searchParams.has('session')) query.set('session', url.searchParams.get('session'))
      return await relayJson(res, 'GET', `/v1/threads/imported${query.size ? `?${query}` : ''}`)
    }

    // Threads use the same authenticated /v1 relay as sessions.
    if (rest === '/threads/events') {
      if (method !== 'GET') return sendJson(res, 405, { error: 'This operation is not available for threads.' })
      const query = url.searchParams.has('archived') ? `?archived=${encodeURIComponent(url.searchParams.get('archived'))}` : ''
      return await relayEvents(req, res, `/v1/threads/events${query}`)
    }
    const threadFile = /^\/threads\/([a-zA-Z0-9_-]+)\/files\/([a-z0-9-]+)$/.exec(rest)
    if (threadFile) {
      if (!['GET', 'PUT'].includes(method)) return sendJson(res, 405, { error: 'Use GET or PUT for a thread file.' })
      const body = method === 'PUT' ? await readBody(req, FILES_TEXT_BODY) : undefined
      return await relayJson(res, method, '/v1' + rest, body)
    }
    const threadBranch = /^\/threads\/([^/]+)\/branch$/.exec(rest)
    if (threadBranch) {
      if (method !== 'POST') return sendJson(res, 405, { error: 'Use POST to open a branch composer.' })
      const target = `/v1/threads/${encodeURIComponent(dec(threadBranch[1]))}/branch`
      const body = await readBody(req).catch((error) => error)
      if (body instanceof Error) return sendJson(res, body.tooLarge ? 413 : 400, { error: body.tooLarge ? 'This text is over what this request carries here.' : body.message })
      return await relayJson(res, method, target, body)
    }
    const threadRoute = /^\/threads(?:\/([^/]+)(?:\/(messages|events|attachments|steps|answer))?)?$/.exec(rest)
    if (threadRoute) {
      const [, id, action] = threadRoute
      const allowed = !id ? ['GET', 'POST'].includes(method)
        : ['events', 'steps'].includes(action) ? method === 'GET'
        : ['attachments', 'messages'].includes(action) ? ['POST', 'PUT'].includes(method)
        : action === 'answer' ? method === 'POST'
        : ['GET', 'PATCH', 'DELETE'].includes(method)
      if (!allowed) return sendJson(res, 405, { error: 'This operation is not available for threads.' })
      const query = new URLSearchParams()
      for (const key of action === 'steps' ? ['message'] : action === 'events' ? ['after', 'steps'] : ['before', 'limit', 'archived', 'steps', ...(id && !action && method === 'DELETE' ? ['empty'] : []), ...(id && !action && method === 'GET' ? ['seen'] : [])]) {
        if (url.searchParams.has(key)) query.set(key, url.searchParams.get(key))
      }
      const target = `/v1/threads${id ? `/${encodeURIComponent(dec(id))}` : ''}${action ? `/${action}` : ''}${query.size ? `?${query}` : ''}`
      if (action === 'events') return await relayEvents(req, res, target)
      if (action === 'attachments' && method === 'PUT') {
        const length = req.headers['content-length'] ?? ''
        if (!/^[0-9]{1,16}$/.test(length)) return sendJson(res, 400, { error: 'The upload did not say how big the video is.' })
        const params = new URLSearchParams({ name: url.searchParams.get('name') ?? '' })
        const abort = new AbortController()
        const disconnected = () => abort.abort()
        res.once('close', disconnected)
        try {
          const out = await broker.uploadVideo(`/v1/threads/${encodeURIComponent(dec(id))}/attachments?${params}`, { body: req, signal: abort.signal })
          noteBrokerError(res, out)
          res.writeHead(out.status, { 'content-type': out.contentType, 'cache-control': 'no-store' })
          return res.end(out.body)
        } catch {
          if (!abort.signal.aborted) return sendJson(res, 502, { error: 'The upload lost its connection. Try attaching the video again.' })
        } finally {
          res.off('close', disconnected)
        }
        return
      }
      if (action === 'attachments') {
        const raw = await readRaw(req, FILES_UPLOAD_BODY)
        if (!raw) return sendJson(res, 413, { error: 'Attachments can be up to 10 MiB each.' })
        const out = await broker.upload(target, { contentType: req.headers['content-type'], body: raw })
        noteBrokerError(res, out)
        res.writeHead(out.status, { 'content-type': out.contentType, 'cache-control': 'no-store' })
        return res.end(out.body)
      }
      let body
      if (['POST', 'PATCH', 'PUT'].includes(method)) {
        body = await readBody(req, MAX_BODY).catch((err) => err)
        if (body instanceof Error) return sendJson(res, body.tooLarge ? 413 : 400, { error: body.tooLarge ? 'This text is over what this request carries here.' : body.message })
      }
      if (action === 'messages') {
        const abort = new AbortController()
        const disconnected = () => abort.abort()
        res.once('close', disconnected)
        if (res.destroyed) disconnected()
        try {
          return await relayJson(res, method, target, body, { signal: abort.signal })
        } catch (error) {
          if (!abort.signal.aborted) throw error
          return
        } finally {
          res.off('close', disconnected)
        }
      }
      return await relayJson(res, method, target, body)
    }

    // The broker's health, and beside it what this app adds of its own.
    // Whether a reply can be read aloud is Read aloud's own server's answer
    // (`/x/read-aloud/status`) since that plugin carries its server half.
    if (rest === '/health' && method === 'GET') {
      const health = await broker.health()
      return sendJson(res, 200, { ...health, ...(Object.keys(config.avatarColours ?? {}).length ? { avatar_colours: config.avatarColours } : {}) })
    }
    if (rest === '/agent-availability' && method === 'GET') {
      return await relayJson(res, 'GET', '/v1/agent-availability', undefined, { signal: AbortSignal.timeout(5000) })
    }
    // The numbers the page has to know before it does something the host would
    // refuse — the biggest file a chat will carry, the biggest image the model
    // can be shown, the types the host takes. They are the **host's** numbers
    // and this app keeps no copy of one: the route is relayed, so the cap the
    // browser checks against and the cap the broker enforces cannot drift.
    if (rest === '/limits' && method === 'GET') {
      return await relayJson(res, 'GET', '/v1/limits')
    }
    // Agent lifecycle writes may wait behind the host's landing lock.
    const lifecycle = rest === '/agents/new' || rest === '/agents/drafts' || /^\/agents\/drafts\/[^/]+(?:\/confirm)?$/.test(rest) || /^\/agents\/[^/]+\/(archive|restore)$/.test(rest)
    if (lifecycle) {
      const allowed = rest === '/agents/new' || /\/(confirm|archive|restore)$/.test(rest) ? ['POST']
        : rest === '/agents/drafts' ? ['GET', 'POST'] : ['GET', 'PATCH', 'DELETE']
      if (!allowed.includes(method)) return sendJson(res, 405, { error: 'This operation is not available for agents.' })
      const query = new URLSearchParams(url.searchParams)
      for (const key of [...query.keys()]) if (!['thread', 'by'].includes(key)) query.delete(key)
      const target = '/v1' + rest.split('/').map(part => encodeURIComponent(dec(part))).join('/')
      return await relayJson(res, method, target + (query.size ? `?${query}` : ''), ['POST', 'PATCH'].includes(method) ? await readBody(req) : undefined, { signal: AbortSignal.timeout(21 * 60 * 1000) })
    }
    const face = /^\/agents\/([^/]+)\/face$/.exec(rest)
    if (face) {
      const target = `/v1/agents/${encodeURIComponent(dec(face[1]))}/face`
      if (method === 'PUT') {
        const body = await readRaw(req, 2 * 1024 * 1024 + 65536)
        if (body === null) return sendJson(res, 413, { error: 'Choose an image no larger than 2 MB.' })
        const out = await broker.upload(target + by, { method, contentType: req.headers['content-type'], body })
        res.writeHead(out.status, { 'content-type': out.contentType, 'cache-control': 'no-store' })
        return res.end(out.body)
      }
      if (method === 'DELETE') return await relayJson(res, method, target + by)
      if (!['GET', 'HEAD'].includes(method)) return sendJson(res, 405, { error: 'Use GET, PUT or DELETE for a face.' })
      const digest = url.search
      const out = await broker.file(target + digest, { method })
      res.writeHead(out.status, { 'content-type': out.contentType || 'application/octet-stream', 'cache-control': out.status === 200 ? out.cacheControl || 'no-cache' : 'no-store' })
      if (!out.stream || method === 'HEAD') { out.stream?.destroy(); return res.end() }
      return await new Promise((resolve, reject) => { out.stream.on('error', reject); res.on('close', () => { out.stream.destroy(); resolve() }); out.stream.pipe(res); res.on('finish', resolve) })
    }
    if (rest === '/system/reviewer/instructions' && method === 'GET') {
      // Settings can read this one generated document. The general Files routes
      // keep the retired System tree hidden.
      const out = await broker.json('GET', '/v1/files/read?root=system&path=Reviewer%2FCLAUDE.md')
      if (out.status === 404) return sendJson(res, 200, { text: null })
      if (out.status !== 200) return sendJson(res, out.status, { error: 'Reviewer instructions are unavailable.' })
      return sendJson(res, 200, { text: safeJson(out.body)?.text ?? null })
    }
    const profileRoute = /^\/agents\/([^/]+)$/.test(rest) || /^\/engineers(?:\/([^/]+))?$/.test(rest) || rest === '/system/reviewer' || rest === '/system/persona' || /^\/plugins\/([^/]+)\/agents(?:\/([^/]+))?$/.test(rest)
    if (profileRoute) {
      if (!['GET', 'PATCH'].includes(method)) return sendJson(res, 405, { error: 'Use GET or PATCH for profile settings.' })
      // Re-encode each id independently, preserving the broker's route shape.
      const target = '/v1' + rest.split('/').map(part => encodeURIComponent(dec(part))).join('/')
      return await relayJson(res, method, target, method === 'PATCH' ? await readBody(req) : undefined)
    }
    if (rest === '/agents' && method === 'GET') {
      return await relayJson(res, 'GET', '/v1/agents' + (url.searchParams.has('archived') ? `?archived=${encodeURIComponent(url.searchParams.get('archived'))}` : ''))
    }
    if (rest === '/activity' && method === 'GET') {
      const query = new URLSearchParams()
      for (const key of ['agent', 'kind', 'limit', 'offset']) {
        if (url.searchParams.has(key)) query.set(key, url.searchParams.get(key))
      }
      return await relayJson(res, method, `/v1/activity${query.size ? `?${query}` : ''}`)
    }
    // Settings › Account and About Loam (2026-11-18): reads the app answers
    // itself, from the login's headers and from the machine it runs on.
    if (rest === '/account') {
      if (method !== 'GET') return sendJson(res, 405, { error: 'Who is signed in is read only.' })
      return sendJson(res, 200, signedIn(req.headers))
    }
    if (rest === '/server') {
      if (method !== 'GET') return sendJson(res, 405, { error: 'The server is read only.' })
      const facts = serverFacts()
      try {
        const out = await broker.json('GET', '/v1/usage/status', undefined, { signal: AbortSignal.timeout(2000) })
        if (out.status === 200) {
          const parsed = safeJson(out.body)
          if (parsed && typeof parsed === 'object' && typeof parsed.sending === 'boolean') {
            facts.usage = {
              recording: parsed.recording !== false,
              sending: parsed.sending,
              collector_host: typeof parsed.collector_host === 'string' && parsed.collector_host ? parsed.collector_host : null,
            }
          }
        }
      } catch {}
      return sendJson(res, 200, facts)
    }
    if (rest === '/usage/status') {
      if (method !== 'GET') return sendJson(res, 405, { error: 'Usage status is read only.' })
      return await relayJson(res, 'GET', '/v1/usage/status', undefined, { signal: AbortSignal.timeout(5000) })
    }
    if (rest === '/owner/used-controls' && method === 'POST') {
      return await relayJson(res, method, '/v1/owner/used-controls', await readBody(req))
    }
    if (rest === '/owner') {
      if (method !== 'GET') return sendJson(res, 405, { error: 'The owner is read only.' })
      return await relayJson(res, 'GET', '/v1/owner', undefined, { signal: AbortSignal.timeout(5000) })
    }
    if (rest === '/apps/open' && method === 'POST') return await relayJson(res, method, '/v1/apps/open', await readBody(req))
    if (rest === '/apps' && method === 'GET') return await relayJson(res, 'GET', '/v1/apps')
    if (['/apps/media', '/apps/media/image', '/apps/icon'].includes(rest) && method === 'GET') {
      const href = url.searchParams.get('url')
      // Revalidate against the current catalog, including cached image reads.
      const catalog = await broker.json('GET', '/v1/apps')
      if (catalog.status !== 200) { res.writeHead(catalog.status, { 'content-type': catalog.contentType, 'cache-control': 'no-store' }); return res.end(catalog.body) }
      const apps = (JSON.parse(catalog.body).apps ?? []).filter(app => typeof app?.url === 'string')
      const origin = `http://127.0.0.1:${server.address().port}`
      if (rest === '/apps/media') return sendJson(res, 200, { apps: await Promise.all(apps.map(app => appMedia.resolve(app, { origin }))) })
      const app = apps.find(app => app.url === href)
      if (!app) return sendJson(res, 404, { error: 'That app is no longer available.' })
      // Older open tabs receive only a verified same-origin icon, never paths
      // to probe on the site. The current launcher uses the batch media route.
      if (rest === '/apps/icon') { const media = await appMedia.resolve(app, { origin }); return sendJson(res, 200, { icons: media.icon ? [media.icon] : [] }) }
      const image = await appMedia.image(app, url.searchParams.get('kind'))
      const placeholder = Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 100"><rect width="160" height="100" rx="12" fill="#d9d5cf"/></svg>')
      const body = image?.body ?? placeholder
      res.writeHead(200, { 'content-type': image?.type ?? 'image/svg+xml', 'content-length': body.length,
        'cache-control': image ? 'private, max-age=86400' : 'no-store',
        // A remotely supplied SVG is safe as an image and remains sandboxed
        // if somebody opens its URL as a document.
        'content-security-policy': "default-src 'none'; style-src 'unsafe-inline'; sandbox" })
      return res.end(body)
    }
    const activityUndo = /^\/activity\/([^/]+)\/undo$/.exec(rest)
    const jobCancel = /^\/jobs\/([^/]+)\/cancel$/.exec(rest)
    if (method === 'POST' && (activityUndo || jobCancel)) {
      const match = activityUndo ?? jobCancel
      const path = activityUndo ? 'activity' : 'jobs'
      const action = activityUndo ? 'undo' : 'cancel'
      return await relayJson(res, method, `/v1/${path}/${encodeURIComponent(dec(match[1]))}/${action}`, await readBody(req))
    }
    // Schedules belong to the host. Only these named operations cross the
    // seam; refusals and records come back unchanged.
    if (rest === '/schedules' && method === 'GET') {
      const query = new URLSearchParams()
      if (url.searchParams.has('agent')) query.set('agent', url.searchParams.get('agent'))
      return await relayJson(res, method, `/v1/schedules${query.size ? `?${query}` : ''}`)
    }
    const schedule = /^\/schedules\/([^/]+)(?:\/([^/]+)(?:\/(run|seen))?)?$/.exec(rest)
    if (schedule) {
      const [, agent, name, action] = schedule
      const allowed = action ? method === 'POST' : name ? ['GET', 'PATCH', 'DELETE'].includes(method) : ['GET', 'POST'].includes(method)
      if (allowed) {
        const target = `/v1/schedules/${encodeURIComponent(dec(agent))}${name ? `/${encodeURIComponent(dec(name))}` : ''}${action ? `/${action}` : ''}`
        return await relayJson(res, method, target, ['POST', 'PATCH', 'DELETE'].includes(method) ? await readBody(req) : undefined)
      }
    }
    // The providers — the vendor accounts a session runs on. Every one of them,
    // wired or not: a row that says "Codex — not wired" is an answer where a
    // missing row is a bug report.
    if ((rest === '/update' || rest === '/update/progress') && method === 'GET') return await relayJson(res, 'GET', `/v1${rest}`)
    if (['/update/check', '/update/apply', '/update/rollback'].includes(rest) && method === 'POST') return await relayJson(res, 'POST', `/v1${rest}`, await readBody(req))

    if (rest === '/providers' && method === 'GET') {
      return await relayJson(res, 'GET', '/v1/providers')
    }
    // Renewing a provider's sign-in, which is a flow that **cannot finish
    // without a person**: the host answers a URL to open and waits for the code
    // the sign-in page gives them. Four routes, and nothing secret on any of
    // them — a URL, a state, two dates and, on failure, the provider's own one
    // line. No token ever reaches the browser; this app still holds only the
    // broker's.
    const login = /^\/providers\/([^/]+)\/login$/.exec(rest)
    if (login && (method === 'GET' || method === 'POST' || method === 'DELETE')) {
      const p = `/v1/providers/${encodeURIComponent(dec(login[1]))}/login`
      return await relayJson(res, method, p, method === 'POST' ? await readBody(req) : undefined)
    }
    const loginCode = /^\/providers\/([^/]+)\/login\/code$/.exec(rest)
    if (loginCode && method === 'POST') {
      const p = `/v1/providers/${encodeURIComponent(dec(loginCode[1]))}/login/code`
      return await relayJson(res, 'POST', p, await readBody(req))
    }
    const logout = /^\/providers\/([^/]+)\/logout$/.exec(rest)
    if (logout && method === 'POST') {
      return await relayJson(res, 'POST', `/v1/providers/${encodeURIComponent(dec(logout[1]))}/logout`, await readBody(req))
    }
    // GitHub, the first integration. The host runs GitHub's manifest flow and
    // holds the app, the installation and the user's authorization; the page
    // sees the state and drives the flow. `POST .../token` is the host's own
    // git credential helper's and is deliberately not here.
    if (rest === '/integrations/github/accounts') {
      if (!['GET', 'POST'].includes(method)) return sendJson(res, 405, { error: 'Use GET or POST for GitHub accounts.' })
      return await relayJson(res, method, `/v1/integrations/github/accounts${by}`, method === 'POST' ? await readBody(req) : undefined)
    }
    const githubAccount = /^\/integrations\/github\/accounts\/([^/]+)(\/refresh)?$/.exec(rest)
    if (githubAccount) {
      const expected = githubAccount[2] ? 'POST' : 'DELETE'
      if (method !== expected) return sendJson(res, 405, { error: `Use ${expected} for this GitHub account action.` })
      return await relayJson(res, method, `/v1/integrations/github/accounts/${encodeURIComponent(dec(githubAccount[1]))}${githubAccount[2] ?? ''}${by}`, await readBody(req))
    }
    if (rest === '/integrations/github' && (method === 'GET' || method === 'DELETE')) {
      return await relayJson(res, method, '/v1/integrations/github')
    }
    const github = /^\/integrations\/github\/(manifest|register|install|callback|refresh)$/.exec(rest)
    if (github && method === 'POST') {
      return await relayJson(res, 'POST', `/v1/integrations/github/${github[1]}`, await readBody(req))
    }
    // Reserved paths precede session ids. The host owns residency, memory,
    // thread mapping, and the atomic decision of which sessions are idle.
    if (rest === '/sessions/live' && method === 'GET') return await relayJson(res, 'GET', '/v1/sessions/live')
    if (rest === '/sessions/live/events' && method === 'GET') return await relayEvents(req, res, '/v1/sessions/live/events')
    if (rest === '/sessions/end-idle' && method === 'POST') return await relayJson(res, 'POST', '/v1/sessions/end-idle', await readBody(req))
    if (rest === '/sessions' && method === 'GET') {
      return await relayJson(res, 'GET', `/v1/sessions${listQuery(url)}`)
    }
    // The live list, and it is checked before the route below because `events`
    // is a path segment where that one expects a session id.
    //
    // **There is no `?from=` on this stream and there must not be one.** What
    // it carries is the session record — a snapshot, never an event, with no
    // number and nothing to reconnect at. Reopening it re-sends the whole list,
    // which is by definition current.
    if (rest === '/sessions/events' && method === 'GET') {
      return await relayEvents(req, res, `/v1/sessions/events${listQuery(url)}`)
    }

    const one = /^\/sessions\/([^/]+)$/.exec(rest)
    if (one && (method === 'GET' || method === 'DELETE')) {
      return await relayJson(res, method, `/v1/sessions/${encodeURIComponent(dec(one[1]))}`)
    }

    // The retired System tree is no longer a browser capability. Shared
    // file routes continue to serve Vault and AI Spaces.
    if (rest.startsWith('/files/') && ['root', 'from_root'].some(key => url.searchParams.get(key) === 'system')) {
      await drain(req)
      return sendJson(res, 404, { error: 'That location is no longer shown.' })
    }
    // Files capabilities and source mappings are the host's.
    const filesGet = /^\/files\/(roots|list|stat|read|resolve|recent)$/.exec(rest)
    if (filesGet && method === 'GET') {
      const query = filesQuery(url, FILES_PARAMS[filesGet[1]])
      if (query.error) return sendJson(res, 400, { error: query.error })
      return await relayJson(res, 'GET', `/v1/files/${filesGet[1]}${query.search}`)
    }
    // The published vault URLs this installation's notes link to, so the page
    // can open them in Files. Configuration, never a literal in the repo.
    if (rest === '/files/config' && method === 'GET') {
      return sendJson(res, 200, { vault_urls: config.filesVaultUrls ?? [] })
    }
    if (rest === '/files/link' && method === 'GET') {
      const query = filesQuery(url, FILES_PARAMS.download)
      if (query.error) return sendJson(res, 400, { error: query.error })
      const root = url.searchParams.get('root')
      const filePath = url.searchParams.get('path') ?? ''
      if (!root || !filePath || filePath.startsWith('/')) return sendJson(res, 400, { error: 'A file link needs a root and a relative file path.' })
      return sendJson(res, 200, fileLink(config, root, filePath))
    }
    if (rest === '/files/live' && method === 'GET') {
      const query = filesQuery(url, FILES_PARAMS.download)
      if (query.error) return sendJson(res, 400, { error: query.error })
      if (!config.filesContentOrigin) return sendJson(res, 503, { error: 'Live HTML needs a separate content origin. The host has not configured it yet.' })
      if (new URL(config.filesContentOrigin).hostname === new URL(`http://${req.headers.host}`).hostname) return sendJson(res, 503, { error: 'Live HTML needs a different hostname from this app so cookies cannot be shared by port.' })
      const root = url.searchParams.get('root')
      const filePath = url.searchParams.get('path') ?? ''
      if (!root || !filePath || filePath.startsWith('/')) return sendJson(res, 400, { error: 'A live preview needs a root and a relative file path.' })
      if (!/\.html?$/i.test(filePath)) return sendJson(res, 415, { error: 'Only an HTML file is previewed here.' })
      const stat = await broker.json('GET', `/v1/files/stat${query.search}`)
      if (stat.status !== 200) {
        noteBrokerError(res, stat)
        res.writeHead(stat.status, { 'content-type': stat.contentType, 'cache-control': 'no-store' })
        return res.end(stat.body)
      }
      const row = JSON.parse(stat.body)
      if (row.kind !== 'file' || row.capabilities?.download !== true) return sendJson(res, 403, { error: 'The host does not offer this file for preview.' })
      return sendJson(res, 200, { url: content.issue(root, filePath) })
    }
    // An HTML file as a page that runs nothing and reaches nothing: see
    // `src/preview.js`. Only `.html`/`.htm`, and only what the host will read.
    if (rest === '/files/preview' && method === 'GET') {
      const query = filesQuery(url, FILES_PARAMS.download)
      if (query.error) return sendJson(res, 400, { error: query.error })
      const root = url.searchParams.get('root') ?? ''
      const filePath = url.searchParams.get('path') ?? ''
      if (!/\.html?$/i.test(filePath)) return sendJson(res, 415, { error: 'Only an HTML file is previewed here.' })
      return await relayPreview(res, root, filePath)
    }
    // Inline PDF delivery for the app's Files viewer. Verifies `%PDF-` magic
    // bytes server-side and responds with `Content-Type: application/pdf`,
    // `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, and
    // `default-src 'none'; object-src 'self'; frame-ancestors 'self'` so Chrome,
    // Edge, Safari and Firefox render PDFs natively without `<iframe sandbox>`
    // blocking PDFium while spoofed files can never render inline.
    if (rest === '/files/pdf' && (method === 'GET' || method === 'HEAD')) {
      const query = filesQuery(url, FILES_PARAMS.download)
      if (query.error) return sendJson(res, 400, { error: query.error })
      const filePath = url.searchParams.get('path') ?? ''
      if (!/\.pdf$/i.test(filePath)) return sendJson(res, 415, { error: 'Only a PDF file is shown here.' })
      const params = new URLSearchParams(query.search)
      if (req.headers.range) params.set('range', req.headers.range)
      return await relayPdf(res, `/v1/files/download?${params}`, {
        filePath,
        headOnly: method === 'HEAD',
        checkOnly: url.searchParams.get('check') === '1',
        isRange: Boolean(req.headers.range),
      })
    }
    if (rest === '/files/download' && (method === 'GET' || method === 'HEAD')) {
      const query = filesQuery(url, FILES_PARAMS.download)
      if (query.error) return sendJson(res, 400, { error: query.error })
      const params = new URLSearchParams(query.search)
      if (req.headers.range) params.set('range', req.headers.range)
      return await relayDownload(res, `/v1/files/download?${params}`)
    }
    if (rest === '/files/archive') {
      if (!['GET', 'HEAD'].includes(method)) return sendJson(res, 405, { error: 'Use GET or HEAD to download an archive.' })
      if (!signedIn(req.headers).username) return sendJson(res, 403, { error: 'Sign in as the owner to download an archive.' })
      const query = filesQuery(url, FILES_PARAMS.download)
      if (query.error) return sendJson(res, 400, { error: query.error })
      const root = url.searchParams.get('root') ?? ''
      const relPath = (url.searchParams.get('path') ?? '').replace(/^\/+|\/+$/g, '')
      if (!['vault', 'spaces'].includes(root)) return sendJson(res, 400, { error: 'Archives can be downloaded for Vault or AI Spaces.' })
      if (relPath && shouldExclude(root, relPath.split('/').filter(Boolean), 'folder')) {
        return sendJson(res, 404, { error: `nothing at ${root}/${relPath}` })
      }
      const filename = archiveFilename(root, relPath)
      const checkOnly = url.searchParams.get('check') === '1'
      if (!checkOnly && method === 'GET') {
        const direct = await broker.download(`/v1/files/archive${query.search}`)
        if (direct.ok) {
          const headers = {
            'content-type': 'application/zip',
            'content-disposition': `attachment; filename*=UTF-8''${encodeURIComponent(filename)}`,
            'content-security-policy': "sandbox; default-src 'none'",
            'x-content-type-options': 'nosniff',
            'cache-control': 'private, no-store',
          }
          if (direct.length) headers['content-length'] = direct.length
          res.writeHead(200, headers)
          if (!direct.stream) return res.end()
          direct.stream.on('error', () => res.destroy())
          res.on('close', () => direct.stream.destroy())
          return direct.stream.pipe(res)
        }
        // 405 / 501 (or 404 "no route" / "no such route") means the host broker does not
        // implement `/v1/files/archive` yet; stream over list + download instead. Any
        // other refusal (e.g. 401/403/404 missing path/503) is handed back whole.
        const noRoute = direct.status === 405 || direct.status === 501 || (direct.status === 404 && /no (?:such )?route/i.test(direct.body || ''))
        if (!noRoute) {
          noteBrokerError(res, direct)
          res.writeHead(direct.status, { 'content-type': direct.contentType, 'cache-control': 'no-store' })
          return res.end(direct.body)
        }
      }
      const probe = relPath
        ? await broker.json('GET', `/v1/files/stat?${new URLSearchParams({ root, path: relPath })}`)
        : await broker.json('GET', `/v1/files/list?${new URLSearchParams({ root, limit: '1' })}`)
      if (probe.status !== 200) {
        noteBrokerError(res, probe)
        res.writeHead(probe.status, { 'content-type': probe.contentType, 'cache-control': 'no-store' })
        return res.end(probe.body)
      }
      if (relPath) {
        const row = safeJson(probe.body)
        if (row?.kind !== 'folder') return sendJson(res, 400, { error: 'Only folders can be downloaded as a zip.' })
      }
      if (checkOnly) return sendJson(res, 200, { ok: true, filename })
      res.writeHead(200, {
        'content-type': 'application/zip',
        'content-disposition': `attachment; filename*=UTF-8''${encodeURIComponent(filename)}`,
        'content-security-policy': "sandbox; default-src 'none'",
        'x-content-type-options': 'nosniff',
        'cache-control': 'private, no-store',
      })
      if (method === 'HEAD') return res.end()
      try {
        await streamZipArchive({ root, path: relPath, broker, req, res })
      } catch (err) {
        noteError(res, 502, err?.message ?? 'Archive stream failed.')
        res.destroy()
      }
      return
    }
    if (rest === '/files/text' && method === 'PUT') {
      const body = await readBody(req, FILES_TEXT_BODY).catch((err) => err)
      if (body instanceof Error) return sendJson(res, body.tooLarge ? 413 : 400, { error: body.tooLarge ? 'This text is over what a file save carries here.' : body.message })
      if (body?.root === 'system') return sendJson(res, 404, { error: 'That location is no longer shown.' })
      return await relayJson(res, 'PUT', '/v1/files/text', body)
    }
    // An upload into a folder: the browser's multipart envelope, one file, is
    // relayed untouched. The host names the file (a taken name becomes
    // `name 2.ext`), checks its type and its bytes, and refuses a read-only
    // folder; this app checks only the address and the size, and the size
    // without holding more than the cap: a body over it is drained unread so
    // the page gets a `413` and a sentence rather than a reset connection.
    if (rest === '/files/upload' && method === 'POST') {
      const query = filesUploadQuery(url)
      if (query.error) {
        await drain(req)
        return sendJson(res, 400, { error: query.error })
      }
      const raw = await readRaw(req, FILES_UPLOAD_BODY)
      if (raw === null) {
        return sendJson(res, 413, { error: `This file is over the ${FILES_UPLOAD_MAX_BYTES / (1 << 20)} MiB an upload carries.`, max: FILES_UPLOAD_MAX_BYTES })
      }
      const out = await broker.upload(`/v1/files/upload${query.search}`, {
        contentType: req.headers['content-type'],
        body: raw,
      })
      noteBrokerError(res, out)
      res.writeHead(out.status, { 'content-type': out.contentType, 'cache-control': 'no-store' })
      return res.end(out.body)
    }
    if (rest === '/files/rename') {
      if (method !== 'POST') return sendJson(res, 405, { error: 'Use POST to rename a file or folder.' })
      const body = await readBody(req).catch((err) => err)
      if (body instanceof Error) return sendJson(res, body.tooLarge ? 413 : 400, { error: body.message })
      return await relayJson(res, 'POST', '/v1/files/rename', body)
    }
    if (rest === '/files/delete') {
      if (method !== 'POST') return sendJson(res, 405, { error: 'Use POST to delete a file.' })
      const body = await readBody(req).catch((err) => err)
      if (body instanceof Error) return sendJson(res, body.tooLarge ? 413 : 400, { error: body.message })
      if (body?.root !== 'vault' || typeof body.path !== 'string' || !body.path || body.path.startsWith('/') || !safeFilePath(body.path)) {
        return sendJson(res, 400, { error: 'Choose a file in the vault.' })
      }
      return await relayJson(res, 'POST', '/v1/files/delete', { root: body.root, path: body.path })
    }
    if (rest === '/files/restore') {
      if (method !== 'POST') return sendJson(res, 405, { error: 'Use POST to restore a file.' })
      const body = await readBody(req).catch((err) => err)
      if (body instanceof Error) return sendJson(res, body.tooLarge ? 413 : 400, { error: body.message })
      if (body?.root !== 'vault' || typeof body.restore_id !== 'string' || !/^[0-9a-f]{32}$/.test(body.restore_id)) {
        return sendJson(res, 400, { error: 'Choose a deleted file to restore.' })
      }
      return await relayJson(res, 'POST', '/v1/files/restore', { root: body.root, restore_id: body.restore_id })
    }
    const filesCreate = /^\/files\/(file|folder)$/.exec(rest)
    if (filesCreate && method === 'POST') {
      const body = await readBody(req, FILES_TEXT_BODY).catch((err) => err)
      if (body instanceof Error) return sendJson(res, body.tooLarge ? 413 : 400, { error: body.tooLarge ? 'This text is over what a new file carries here.' : body.message })
      if (body?.root === 'system') return sendJson(res, 404, { error: 'That location is no longer shown.' })
      return await relayJson(res, 'POST', `/v1/files/${filesCreate[1]}`, body)
    }

    return sendJson(res, 404, { error: `no such route: ${method} /api${rest}` })
  }


  // The broker's answer, whole: its status and its words. A 429 over the
  // session cap says what the cap is and what to do about it, and the page
  // shows that sentence rather than one of this app's invention.
  // A file's bytes, from the host to the person's disk. **Always an
  // attachment, and never something this origin renders**: a vault holds HTML
  // and SVG that nobody here wrote, and drawn as a page of this app it would
  // run with this app's cookies. So the disposition is forced to `attachment`
  // whatever the host said, the type is the host's only when it is one a
  // browser draws inertly, and the answer carries a sandboxing policy of its
  // own. An `<img>` still shows an image from it; nothing navigates into one.
  async function relayDownload(res, brokerPath) {
    const out = await broker.download(brokerPath)
    if (!out.ok) {
      noteBrokerError(res, out)
      res.writeHead(out.status, { 'content-type': out.contentType, 'cache-control': 'no-store' })
      return res.end(out.body)
    }
    const type = String(out.contentType ?? '').split(';')[0].trim().toLowerCase()
    const headers = {
      'content-type': INERT_TYPES.has(type) ? type : 'application/octet-stream',
      'content-disposition': attachmentName(out.disposition),
      'content-security-policy': "sandbox; default-src 'none'",
      'cache-control': 'private, no-store',
    }
    if (out.length) headers['content-length'] = out.length
    if (out.contentRange) headers['content-range'] = out.contentRange
    if (out.acceptRanges) headers['accept-ranges'] = out.acceptRanges
    res.writeHead(out.status, headers)
    if (!out.stream) return res.end()
    out.stream.on('error', () => res.destroy())
    res.once('close', () => out.stream.destroy())
    out.stream.pipe(res)
  }

  async function relayPdf(res, brokerPath, { filePath, headOnly = false, checkOnly = false, isRange = false } = {}) {
    const out = await broker.download(brokerPath)
    if (!out.ok) {
      noteBrokerError(res, out)
      res.writeHead(out.status, { 'content-type': out.contentType, 'cache-control': 'no-store' })
      return res.end(out.body)
    }
    let head = Buffer.alloc(0)
    let ended = !out.stream
    if (!isRange && out.stream) {
      try {
        const got = await readLeading(out.stream, 1024)
        head = got.head
        ended = got.ended
      } catch (err) {
        out.stream.destroy()
        noteError(res, 502, err?.message ?? 'PDF stream failed.')
        return sendJson(res, 502, { error: 'Could not read this PDF.' })
      }
      if (!head.subarray(0, 1024).includes(PDF_MAGIC)) {
        out.stream.destroy()
        const msg = 'This file is not a valid PDF, so it is not shown.'
        return checkOnly ? sendJson(res, 415, { error: msg }) : send(res, 415, 'text/plain; charset=utf-8', msg)
      }
    }
    if (checkOnly) {
      out.stream?.destroy()
      return sendJson(res, 200, { ok: true })
    }
    const headers = {
      'content-type': 'application/pdf',
      'content-disposition': inlineName(out.disposition, filePath),
      'content-security-policy': PDF_CSP,
      'x-frame-options': 'SAMEORIGIN',
      'x-content-type-options': 'nosniff',
      'referrer-policy': 'no-referrer',
      'cache-control': 'private, no-store',
    }
    if (out.length) headers['content-length'] = out.length
    if (out.contentRange) headers['content-range'] = out.contentRange
    if (out.acceptRanges) headers['accept-ranges'] = out.acceptRanges
    res.writeHead(out.status, headers)
    if (headOnly || !out.stream) {
      out.stream?.destroy()
      return res.end()
    }
    if (head.length) res.write(head)
    if (ended) return res.end()
    out.stream.on('error', () => res.destroy())
    res.once('close', () => out.stream.destroy())
    out.stream.resume()
    out.stream.pipe(res)
  }

  async function relayPreview(res, root, filePath) {
    const read = async (r, p) => {
      const out = await broker.json('GET', `/v1/files/read?root=${encodeURIComponent(r)}&path=${encodeURIComponent(p)}`)
      if (out.status !== 200) {
        const err = new Error('unread')
        err.out = out
        throw err
      }
      return JSON.parse(out.body).text
    }
    const bytesOf = async (r, p, max) => {
      const out = await broker.download(`/v1/files/download?root=${encodeURIComponent(r)}&path=${encodeURIComponent(p)}`)
      if (!out.ok) throw Object.assign(new Error('unread'), { out: { status: out.status, contentType: out.contentType, body: out.body } })
      if (!out.stream) throw new Error('unread')
      if (out.length && Number(out.length) > max) {
        out.stream.destroy()
        throw Object.assign(new Error('too big'), { tooLarge: true })
      }
      const chunks = []
      let size = 0
      for await (const chunk of out.stream) {
        size += chunk.length
        if (size > max) {
          out.stream.destroy()
          throw Object.assign(new Error('too big'), { tooLarge: true })
        }
        chunks.push(chunk)
      }
      return Buffer.concat(chunks)
    }
    let html
    try {
      html = await read(root, filePath)
    } catch (err) {
      if (!err.out) throw err
      // Text editing stays subject to the host's text limit. A static HTML
      // preview can instead read the offered download, with its own bound.
      if (err.out.status === 413) {
        try {
          html = (await bytesOf(root, filePath, PREVIEW_LIMITS.htmlBytes)).toString('utf8')
        } catch (downloadError) {
          const status = downloadError.out?.status ?? (downloadError.tooLarge ? 413 : 502)
          res.writeHead(status, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store', 'content-security-policy': PREVIEW_POLICY })
          return res.end(`<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><style>:root{color-scheme:light dark}body{font:16px/1.5 system-ui;margin:32px;max-width:36em}</style><h1 style="font-size:20px">Static preview unavailable</h1><p>${downloadError.tooLarge ? 'This page is larger than the 16 MB static preview supports.' : 'This file’s download is unavailable. It may have moved or been deleted.'}</p><p>Use Source to return to Files and download the file.</p>`)
        }
      } else {
        noteBrokerError(res, err.out)
        res.writeHead(err.out.status, { 'content-type': err.out.contentType, 'cache-control': 'no-store' })
        return res.end(err.out.body)
      }
    }
    const doc = await buildPreview({ root, path: filePath, html, readText: read, readBytes: bytesOf })
    res.writeHead(200, {
      'content-type': 'text/html; charset=utf-8',
      'content-security-policy': PREVIEW_POLICY,
      'x-content-type-options': 'nosniff',
      'referrer-policy': 'no-referrer',
      'cache-control': 'private, no-store',
      'cross-origin-resource-policy': 'same-origin',
    })
    res.end(doc)
  }

  async function relayJson(res, method, brokerPath, body, options) {
    const out = await broker.json(method, brokerPath, body, options)
    noteBrokerError(res, out)
    res.writeHead(out.status, { 'content-type': out.contentType, 'cache-control': 'no-store' })
    res.end(out.body)
  }

  // Server-sent events, passed through byte for byte.
  //
  // Nothing here reframes, batches or translates: the events are the coding
  // CLI's own plus the broker's three, and the page reduces them. Every header
  // that tells a proxy not to buffer is set, because this app sits behind one.
  async function relayEvents(req, res, brokerPath) {
    const abort = new AbortController()
    req.on('close', () => abort.abort())

    const upstream = await broker.stream(brokerPath, { signal: abort.signal })
    if (!upstream.ok) {
      return sendJson(res, upstream.status, safeJson(upstream.body) ?? { error: upstream.body })
    }

    res.writeHead(200, {
      'content-type': 'text/event-stream; charset=utf-8',
      'cache-control': 'no-cache, no-store, no-transform',
      'connection': 'keep-alive',
      'x-accel-buffering': 'no', // for proxies that would otherwise hold a chunk back
    })
    res.flushHeaders?.()
    // Open the stream immediately, so the browser's reader is connected before
    // the first real event rather than at it — and say how often this end will
    // speak when it has nothing to say, which is how the page knows how long a
    // silence has to last before the connection counts as gone.
    res.write(`: open ping=${KEEPALIVE_MS}\n\n`)

    const keepalive = setInterval(() => {
      if (!res.writableEnded) res.write(': ping\n\n')
    }, KEEPALIVE_MS)
    keepalive.unref?.()

    upstream.stream.on('error', () => res.end())
    upstream.stream.on('end', () => res.end())
    res.on('close', () => {
      clearInterval(keepalive)
      abort.abort()
      upstream.stream.destroy()
    })
    upstream.stream.pipe(res, { end: false })
  }

  return { server, config }
}


// Every request's one line: written when the response is done with — finished,
// or cut off, which for an event stream is when the page went away — so `ms` is
// how long it was open and `bytes` is what was sent. A caught failure adds its
// message to the same line under the same id, so a line leads to its request
// and there is no second line to match it with.
//
// Static files are `debug`, because a page load is a score of them and they
// would bury the rest; a static request that did not succeed is not, because
// that is a thing to find. Everything under `/api` and `/up` is `info` at
// least, and anything that answered 5xx is `error`.
const NOTES = new WeakMap()

function track(req, res, url, log) {
  const note = { id: requestId(), static: false, error: null, stack: null }
  NOTES.set(res, note)
  res.setHeader('x-request-id', note.id)

  const started = performance.now()
  let bytes = 0
  const count = (chunk, encoding) => {
    if (chunk == null || typeof chunk === 'function') return
    bytes += typeof chunk === 'string' ? Buffer.byteLength(chunk, typeof encoding === 'string' ? encoding : 'utf8') : chunk.length
  }
  const write = res.write
  const end = res.end
  res.write = function (chunk, encoding, cb) {
    count(chunk, encoding)
    return write.call(this, chunk, encoding, cb)
  }
  res.end = function (chunk, encoding, cb) {
    count(chunk, encoding)
    return end.call(this, chunk, encoding, cb)
  }

  res.once('close', () => {
    const line = {
      event: 'request',
      id: note.id,
      method: req.method,
      path: url.pathname.replace(/^(\/content\/)[^/]+/, '$1…'),
      query: loggedQuery(url.searchParams),
      status: res.statusCode,
      bytes,
      ms: Math.round(performance.now() - started),
    }
    if (!res.writableFinished) line.aborted = true
    if (note.error) line.error = note.error
    if (note.stack) line.stack = note.stack
    if (res.statusCode >= 500 || note.stack) log.error(line)
    else if (note.static && res.statusCode < 400) log.debug(line)
    else log.info(line)
  })
  return note
}

function noteOf(res) {
  return NOTES.get(res) ?? { id: null }
}

// A 5xx's own sentence, onto its request's line: the broker's words when the
// broker refused, this app's when this app did.
function noteError(res, status, message) {
  const note = NOTES.get(res)
  if (note && status >= 500 && !note.error) note.error = String(message ?? `answered ${status}`)
}

function noteBrokerError(res, out) {
  if (out.status >= 500) noteError(res, out.status, safeJson(out.body)?.error ?? `the broker answered ${out.status}`)
}

// The two things that narrow a listing, and nothing else off the page's URL.
// `?agent=` filters the sessions; `?archived=1` widens them to the ones a
// person has put away. `live` and `cap` describe the machine and are left alone
// by both.
function listQuery(url) {
  const parts = []
  const agent = url.searchParams.get('agent')
  if (agent) parts.push(`agent=${encodeURIComponent(agent)}`)
  if (url.searchParams.get('archived') === '1') parts.push('archived=1')
  return parts.length === 0 ? '' : `?${parts.join('&')}`
}

// The two paths the broker's manifest names for GitHub to send the browser
// back to. Both are the page; the page reads the query.
// The parameters each Files route takes, and only those. See `filesQuery()`.
const FILES_PARAMS = {
  roots: [],
  list: ['root', 'path', 'cursor', 'limit'],
  stat: ['root', 'path'],
  read: ['root', 'path'],
  download: ['root', 'path'],
  resolve: ['ref', 'from_root', 'from_path'],
  recent: ['who', 'window'],
}

// A file's text is read up to 2 MiB; written back as JSON, every byte of it
// could be escaped to six. The host enforces its own cap on the text itself.
const FILES_TEXT_BODY = 13 * (1 << 20)

// Types a browser draws without running anything. Everything else a download
// carries goes out as bytes.
const INERT_TYPES = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/avif', 'application/pdf', 'text/plain', 'audio/mpeg', 'audio/mp4', 'video/mp4', 'video/quicktime', 'video/webm'])

// The query a Files route is relayed with, rebuilt from the names it takes. A
// path is the host's to resolve, but one with a `.` or `..` segment, a
// backslash or a control character in it is not a path a page of this app ever
// asks for, and it is refused here rather than trusted to be refused there.
export function filesQuery(url, names) {
  const parts = []
  for (const name of names) {
    const value = url.searchParams.get(name)
    if (value === null || value === '') continue
    if ((name === 'path' || name === 'from_path') && !safeFilePath(value)) {
      return { error: 'That is not a path inside a Files root.' }
    }
    if (name === 'limit' && !/^\d{1,4}$/.test(value)) return { error: 'limit is a number.' }
    parts.push(`${name}=${encodeURIComponent(value)}`)
  }
  return { search: parts.length ? `?${parts.join('&')}` : '' }
}

// The biggest file the host stores from an upload (`files_upload_max_bytes`),
// and the body this app will read for one: that plus the multipart envelope's
// own lines. The host refuses past it too; this is so the bytes are never
// held here unbounded on the way.
export const FILES_UPLOAD_MAX_BYTES = 10 * (1 << 20)
export const FILES_UPLOAD_BODY = FILES_UPLOAD_MAX_BYTES + 8 * 1024

// An upload's query: a root and a folder, both always sent, `path=` empty
// being the root's top. The root is a name, never a path; the folder is
// refused here on the same terms as every other Files path.
export function filesUploadQuery(url) {
  const root = url.searchParams.get('root') ?? ''
  const folder = url.searchParams.get('path') ?? ''
  if (!/^[a-z0-9_-]{1,40}$/i.test(root)) return { error: 'An upload names the Files root it goes into.' }
  if (!safeFilePath(folder) || folder.startsWith('/')) return { error: 'That is not a path inside a Files root.' }
  return { search: `?root=${encodeURIComponent(root)}&path=${encodeURIComponent(folder)}` }
}

// A body read to its end and dropped, so an answer sent before it is not a
// connection cut under the sender.
function drain(req) {
  return new Promise((resolve) => {
    if (req.readableEnded) return resolve()
    req.on('end', resolve)
    req.on('error', resolve)
    req.resume()
  })
}

export function safeFilePath(value) {
  if (/[\\\u0000-\u001f\u007f]/.test(value)) return false
  return value.split('/').every((segment) => segment !== '.' && segment !== '..')
}

// The name a download is saved under: the host's, if it gave one this app can
// read, and always as an attachment.
function attachmentName(disposition) {
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(String(disposition ?? ''))
  if (!match) return 'attachment'
  let name = match[1]
  try {
    name = decodeURIComponent(name)
  } catch {}
  name = name.replace(/[\\/\u0000-\u001f\u007f"]/g, '_').slice(0, 200)
  return `attachment; filename*=UTF-8''${encodeURIComponent(name)}`
}

const PDF_MAGIC = Buffer.from('%PDF-')
const PDF_CSP = "default-src 'none'; object-src 'self'; frame-ancestors 'self'"

function inlineName(disposition, fallbackPath = 'document.pdf') {
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(String(disposition ?? ''))
  let name = match ? match[1] : String(fallbackPath).split('/').pop() || 'document.pdf'
  try {
    name = decodeURIComponent(name)
  } catch {}
  name = name.replace(/[\\/\u0000-\u001f\u007f"]/g, '_').slice(0, 200) || 'document.pdf'
  return `inline; filename*=UTF-8''${encodeURIComponent(name)}`
}

async function readLeading(stream, minBytes = 1024) {
  const chunks = []
  let total = 0
  let ended = false
  await new Promise((resolve, reject) => {
    const cleanup = () => {
      stream.off('data', onData)
      stream.off('end', onEnd)
      stream.off('error', onErr)
    }
    const onData = (chunk) => {
      const buf = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)
      chunks.push(buf)
      total += buf.length
      if (total >= minBytes) {
        cleanup()
        stream.pause()
        resolve()
      }
    }
    const onEnd = () => { ended = true; cleanup(); resolve() }
    const onErr = (err) => { cleanup(); reject(err) }
    stream.on('data', onData)
    stream.once('end', onEnd)
    stream.once('error', onErr)
  })
  return { head: Buffer.concat(chunks), ended }
}

const GITHUB_RETURNS = new Set(['/integrations/github/registered', '/integrations/github/installed'])

// `.`, `..` and their percent-encoded spellings, as a whole path segment.
const DOT_SEGMENT = /(^|\/)(\.|%2e){1,2}(\/|$)/i

function dec(segment) {
  try {
    return decodeURIComponent(segment)
  } catch {
    return segment
  }
}

function safeJson(text) {
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

function send(res, status, type, body) {
  res.writeHead(status, { 'content-type': type, 'cache-control': 'no-store' })
  res.end(body)
}

function sendJson(res, status, value) {
  noteError(res, status, value?.error)
  send(res, status, 'application/json; charset=utf-8', JSON.stringify(value))
}

// The bytes of a bounded, legacy multipart upload. Videos use the stream relay.
//
// With a `limit`, a body over it is read to its end and dropped, and the answer
// is `null`: the page gets a sentence rather than a connection cut under it.
function readRaw(req, limit = Infinity) {
  return new Promise((resolve, reject) => {
    const chunks = []
    let size = 0
    req.on('data', (chunk) => {
      size += chunk.length
      if (size <= limit) chunks.push(chunk)
    })
    req.on('end', () => resolve(size > limit ? null : Buffer.concat(chunks)))
    req.on('error', reject)
  })
}


function readBody(req, limit = MAX_BODY) {
  return new Promise((resolve, reject) => {
    const chunks = []
    let size = 0
    req.on('data', (chunk) => {
      size += chunk.length
      // Over the limit, the rest is read and dropped rather than the socket
      // cut, so the refusal reaches the browser as a status and a sentence.
      if (size > limit) return
      chunks.push(chunk)
    })
    req.on('end', () => {
      if (size > limit) {
        const err = new Error('request body too large')
        err.tooLarge = true
        return reject(err)
      }
      const text = Buffer.concat(chunks).toString('utf8')
      if (text.trim() === '') return resolve({})
      try {
        resolve(JSON.parse(text))
      } catch {
        // Not the parser's own message: it quotes the body, and a body can be
        // a credential, which goes on no log line here.
        reject(new Error('request body is not JSON'))
      }
    })
    req.on('error', reject)
  })
}

// Started directly, rather than imported by a test.
if (process.argv[1] && import.meta.url === `file://${path.resolve(process.argv[1])}`) {
  const config = readConfig()
  const log = createLog({ level: config.logLevel })
  for (const complaint of configComplaints(config)) {
    log.warn({ event: 'config', message: complaint })
  }
  const { server } = createApp({ config, log })
  server.listen(config.port, config.host, () => {
    log.info({ event: 'listening', host: config.host, port: config.port })
  })
}
