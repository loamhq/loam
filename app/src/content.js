// Read-only HTML and its assets, served ONLY on a separate content authority.
// Short-lived, root-scoped URLs work without app cookies or a broker token in
// the browser. The signing key is process-local: restart revokes every URL.
import { createHmac, randomBytes, timingSafeEqual } from 'node:crypto'
import path from 'node:path'

export const LIVE_SANDBOX = 'allow-scripts allow-same-origin allow-forms allow-popups allow-modals allow-downloads'
const TYPES = { '.html': 'text/html', '.htm': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.csv': 'text/csv', '.svg': 'image/svg+xml', '.wasm': 'application/wasm' }

export function createContent({ origin, broker, now = Date.now }) {
  const key = randomBytes(32)
  const sign = (text) => createHmac('sha256', key).update(text).digest('base64url')
  function issue(root, filePath) {
    const grant = Buffer.from(JSON.stringify({ root, until: now() + 3600_000 })).toString('base64url')
    return `${origin}/content/${grant}.${sign(grant)}/${encodeURIComponent(root)}/${filePath.split('/').map(encodeURIComponent).join('/')}`
  }
  const policy = `sandbox ${LIVE_SANDBOX}; default-src 'none'; ` +
    `script-src ${origin} https: 'unsafe-inline' 'unsafe-eval'; style-src ${origin} https: 'unsafe-inline'; ` +
    `img-src ${origin} https: data: blob:; font-src ${origin} https: data:; media-src ${origin} https: blob:; ` +
    `connect-src ${origin}; form-action ${origin} https:; frame-src 'none'; object-src 'none'; worker-src 'none'`
  async function serve(req, res, url) {
    const fail = (status, text) => { res.writeHead(status, { 'content-type': 'text/plain; charset=utf-8' }); res.end(text) }
    // These headers also apply when the file is opened in its own tab.
    res.setHeader('content-security-policy', policy)
    res.setHeader('referrer-policy', 'no-referrer')
    res.setHeader('cache-control', 'private, no-store')
    res.setHeader('x-content-type-options', 'nosniff')
    if (!['GET', 'HEAD'].includes(req.method)) return fail(405, 'Content is read-only.')
    const match = /^\/content\/([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]+)\/([^/]+)\/(.+)$/.exec(url.pathname)
    if (!match) return fail(404, 'No page here.')
    const [, grant, signature, encodedRoot, encodedPath] = match
    const expected = Buffer.from(sign(grant))
    const provided = Buffer.from(signature)
    if (expected.length !== provided.length || !timingSafeEqual(expected, provided)) return fail(403, 'This preview link is invalid. Reload the preview in Files.')
    let root, parts, permission
    try {
      permission = JSON.parse(Buffer.from(grant, 'base64url').toString())
      root = decodeURIComponent(encodedRoot)
      parts = encodedPath.split('/').map(decodeURIComponent)
    } catch { return fail(400, 'Invalid file path.') }
    if (root === 'system') return fail(404, 'That location is no longer shown.')
    if (permission.root !== root || permission.until <= now()) return fail(403, 'This preview link has expired. Reload the preview in Files.')
    if (parts.some((p) => !p || p === '.' || p === '..' || /[/\\\u0000-\u001f\u007f]/.test(p))) return fail(400, 'Invalid file path.')
    const filePath = parts.join('/')
    const out = await broker.download(`/v1/files/download?root=${encodeURIComponent(root)}&path=${encodeURIComponent(filePath)}`)
    if (!out.ok) return fail(out.status, 'This file is unavailable. It may have moved or been deleted.')
    // The broker is still the authority on which files can be read. The
    // extension supplies web MIME types where its download uses octet-stream.
    let type = TYPES[path.extname(filePath).toLowerCase()] ?? out.contentType ?? 'application/octet-stream'
    if (/^(text\/|application\/json)/i.test(type) && !/charset=/i.test(type)) type += '; charset=utf-8'
    res.writeHead(200, { 'content-type': type })
    if (req.method === 'HEAD') { out.stream?.destroy(); return res.end() }
    if (!out.stream) return res.end()
    out.stream.on('error', () => res.destroy())
    res.on('close', () => out.stream.destroy())
    out.stream.pipe(res)
  }
  return { issue, serve }
}
