// Files out of one directory. No index of directories, no traversal out of it,
// no cache of its own beyond the browser's.

import { createReadStream } from 'node:fs'
import { stat } from 'node:fs/promises'
import path from 'node:path'

const TYPES = new Map(Object.entries({
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.webmanifest': 'application/manifest+json; charset=utf-8',
  '.png': 'image/png',
  '.webp': 'image/webp',
  '.avif': 'image/avif',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.txt': 'text/plain; charset=utf-8',
  '.mp3': 'audio/mpeg',
  // The three typefaces the design names, served from here rather than from a
  // font vendor at run time. See tools/fetch-fonts.js.
  '.woff2': 'font/woff2',
}))

export function createStatic(root) {
  const base = path.resolve(root)

  return async function serve(req, res, pathname) {
    const rel = pathname === '/' ? 'index.html' : decodeURIComponent(pathname).replace(/^\/+/, '')
    const file = path.resolve(base, rel)
    if (file !== base && !file.startsWith(base + path.sep)) return false

    let info
    try {
      info = await stat(file)
    } catch {
      return false
    }
    if (!info.isFile()) return false

    const type = TYPES.get(path.extname(file).toLowerCase()) ?? 'application/octet-stream'
    res.writeHead(200, {
      'content-type': type,
      'content-length': info.size,
      // Revalidate everything. There is no build step and no fingerprinted
      // filename here, and a service worker cached by the browser keeps
      // control of the page long after it was replaced.
      'cache-control': 'no-cache',
    })
    if (req.method === 'HEAD') {
      res.end()
      return true
    }
    createReadStream(file).pipe(res)
    return true
  }
}
