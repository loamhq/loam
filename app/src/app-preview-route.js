import http from 'node:http'
import { isIP } from 'node:net'
import { Readable, pipeline } from 'node:stream'
import { createGunzip, createInflate, createBrotliDecompress } from 'node:zlib'

// Host-owned virtual sites. Only renderer traffic uses this optional route;
// catalog identities, browser links and plugin URLs remain their own addresses.
export function readPreviewRoute({ base = '', addr = '' } = {}) {
  base = base.trim(); addr = addr.trim()
  if (!base) return { base: '', addr: '' }
  let url
  try { url = new URL(base) } catch { throw new Error('APPS_PREVIEW_BASE must be a plain HTTP origin.') }
  if (url.protocol !== 'http:' || url.username || url.password || url.pathname !== '/' || url.search || url.hash || isIP(url.hostname) || !/^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$/i.test(url.hostname)) throw new Error('APPS_PREVIEW_BASE must be a plain HTTP origin with a DNS hostname, without a path or login.')
  if (addr && !isIP(addr)) throw new Error('APPS_PREVIEW_ADDR must be an IP address without a port.')
  return { base: url.origin, addr }
}

export function previewTarget(app, { origin, base = '', addr = '' }) {
  const publicURL = new URL(app.url, origin), internal = new URL(publicURL)
  let hostResolverRules
  if (base && ['site', 'project'].includes(app.kind)) {
    const route = new URL(base)
    route.hostname = `${publicURL.hostname.split('.')[0]}.${route.hostname}`
    route.pathname = publicURL.pathname; route.search = publicURL.search; route.hash = publicURL.hash
    internal.href = route.href
    if (addr) hostResolverRules = `MAP *.${new URL(base).hostname} ${isIP(addr) === 6 ? `[${addr}]` : addr}`
  }
  return { url: internal.href, hostResolverRules,
    rewrite(value) {
      const url = new URL(value, publicURL)
      if (internal.origin !== publicURL.origin && url.origin === publicURL.origin) return internal.origin + url.pathname + url.search + url.hash
      return url.href
    } }
}

// Node's fetch has no per-request DNS override. Connect to the supplied bridge
// address with the virtual Host header; never mutate global DNS or /etc/hosts.
// Redirects remain explicit in the bounded icon reader and retain this mapping.
export function createPreviewFetch({ base = '', addr = '', fetcher = fetch } = {}) {
  if (!base || !addr) return fetcher
  const route = new URL(base)
  return (value, options = {}) => {
    const url = new URL(value)
    const suffix = `.${route.hostname}`, label = url.hostname.slice(0, -suffix.length)
    if (url.protocol !== route.protocol || url.port !== route.port || !url.hostname.endsWith(suffix) || !/^[a-z0-9-]+$/i.test(label)) return fetcher(value, options)
    return new Promise((resolve, reject) => {
      const headers = new Headers(options.headers)
      const req = http.request({ hostname: addr, port: url.port || 80, path: url.pathname + url.search,
        method: 'GET', signal: options.signal, headers: { host: url.hostname, accept: headers.get('accept') || '*/*' } }, response => {
        try {
          const outHeaders = new Headers()
          for (const [name, value] of Object.entries(response.headers)) if (value !== undefined) {
            if (Array.isArray(value)) for (const part of value) outHeaders.append(name, part)
            else outHeaders.set(name, value)
          }
          if ([204, 205, 304].includes(response.statusCode)) { response.resume(); return resolve(new Response(null, { status: response.statusCode, headers: outHeaders })) }
          const encoding = outHeaders.get('content-encoding')
          const decoder = encoding === 'gzip' ? createGunzip() : encoding === 'deflate' ? createInflate() : encoding === 'br' ? createBrotliDecompress() : null
          if (decoder) {
            outHeaders.delete('content-encoding'); outHeaders.delete('content-length')
            pipeline(response, decoder, error => { if (error) decoder.destroy(error) })
          }
          resolve(new Response(Readable.toWeb(decoder || response), { status: response.statusCode, headers: outHeaders }))
        } catch (error) { response.destroy(); reject(error) }
      })
      req.on('error', reject); req.end()
    })
  }
}
