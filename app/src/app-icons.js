// Resolve declared site icons without any browser probing or credentials.
const PAGE_LIMIT = 128 * 1024, MANIFEST_LIMIT = 64 * 1024
const entities = value => value.replace(/&(?:amp|quot|apos|lt|gt|#\d+|#x[0-9a-f]+);/gi, entity => {
  const name = entity.slice(1, -1).toLowerCase()
  if (name[0] !== '#') return ({ amp: '&', quot: '"', apos: "'", lt: '<', gt: '>' })[name]
  const n = name[1] === 'x' ? parseInt(name.slice(2), 16) : Number(name.slice(1))
  return n > 0 && n <= 0x10ffff ? String.fromCodePoint(n) : ''
})
function sameSite(value, base, origin) {
  if (typeof value !== 'string' || !value.trim()) return null
  try {
    const url = new URL(value, base)
    return ['https:', 'http:'].includes(url.protocol) && url.origin === origin && !url.username && !url.password ? url.href : null
  } catch { return null }
}
function imageURL(value, base) {
  if (typeof value !== 'string' || !value.trim()) return null
  try {
    const url = new URL(value, base)
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null
  } catch { return null }
}
function mappedURL(value, base, rewrite) {
  const url = imageURL(value, base)
  return url ? imageURL(rewrite(url)) : null
}
function links(html) {
  // Metadata tags only; never evaluate or return the site's HTML.
  return [...html.replace(/<!--[\s\S]*?-->/g, '').matchAll(/<link\b(?:[^>"']|"[^"]*"|'[^']*')*>/gi)].map(([tag]) => {
    const attrs = {}
    for (const [, key, double, single, bare] of tag.matchAll(/([\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/g)) attrs[key.toLowerCase()] = entities(double ?? single ?? bare)
    return attrs
  })
}
// Only declared icons are considered. A successful result is bytes, not a URL
// for the person's browser to probe. The media cache owns freshness on disk.
export function createAppIcons({ fetcher = fetch } = {}) {
  let active = 0
  const waiting = []
  async function read(url, origin, limit, signal, accept, rewrite) {
    for (let redirects = 0; redirects < 4; redirects++) {
      const response = await fetcher(url, { redirect: 'manual', signal, headers: { accept } })
      if ([301, 302, 303, 307, 308].includes(response.status)) {
        await response.body?.cancel()
        const location = mappedURL(response.headers.get('location'), url, rewrite)
        const next = location && (origin ? sameSite(location, url, origin) : location)
        if (!next) throw new Error('Icon metadata left the app origin.')
        url = next; continue
      }
      if (!response.ok) { await response.body?.cancel(); throw new Error('No icon metadata.') }
      const reader = response.body.getReader(), parts = []
      let size = 0
      try {
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          size += value.length
          if (size > limit) throw new Error('Icon metadata is too large.')
          parts.push(Buffer.from(value))
        }
      } finally { await reader.cancel() }
      return { body: Buffer.concat(parts), url }
    }
    throw new Error('Too many icon redirects.')
  }
  async function resolve(href, { icon = null, rewrite = value => value } = {}) {
    const pageURL = imageURL(href)
    if (!pageURL) return null
    const origin = new URL(pageURL).origin, signal = AbortSignal.timeout(7000), declared = []
    try {
      if (icon) { const url = mappedURL(icon, pageURL, rewrite); if (url) declared.push(url) }
      else {
        const page = await read(pageURL, origin, PAGE_LIMIT, signal, 'text/html', rewrite)
        const tags = links(page.body.toString('utf8'))
        const add = (value, base) => { const url = mappedURL(value, base, rewrite); if (url) declared.push(url) }
        for (const tag of tags.filter(t => /\bapple-touch-icon(?:-precomposed)?\b/i.test(t.rel)).sort((a, b) => (parseInt(b.sizes) || 0) - (parseInt(a.sizes) || 0))) add(tag.href, page.url)
        const manifest = tags.find(t => t.rel?.toLowerCase().split(/\s+/).includes('manifest'))
        const manifestLink = manifest && mappedURL(manifest.href, page.url, rewrite)
        const manifestURL = manifestLink && sameSite(manifestLink, page.url, origin)
        if (manifestURL) try {
          const data = await read(manifestURL, origin, MANIFEST_LIMIT, signal, 'application/manifest+json, application/json', rewrite)
          const icons = JSON.parse(data.body.toString('utf8')).icons
          if (Array.isArray(icons)) for (const item of icons.filter(i => typeof i?.src === 'string').sort((a, b) => (parseInt(b.sizes) || 0) - (parseInt(a.sizes) || 0))) add(item.src, data.url)
        } catch { /* A broken manifest does not hide a declared favicon. */ }
        for (const tag of tags.filter(t => t.rel?.toLowerCase().split(/\s+/).includes('icon'))) add(tag.href, page.url)
      }
      for (const candidate of [...new Set(declared)].slice(0, 6)) try {
        const image = await read(candidate, null, 512 * 1024, signal, 'image/*', rewrite)
        const type = iconType(image.body)
        if (type) return { body: image.body, type }
      } catch { /* Try the next declared icon, never conventional paths. */ }
    } catch { /* Private/offline sites have no resolved icon. */ }
    return null
  }
  return async function appIcon(...args) {
    if (active >= 4) await new Promise(resolve => waiting.push(resolve))
    else active++
    try { return await resolve(...args) }
    finally { const next = waiting.shift(); if (next) next(); else active-- }
  }
}

export function iconType(body) {
  if (body.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))) return 'image/png'
  if (body[0] === 255 && body[1] === 216) return 'image/jpeg'
  if (/^GIF8[79]a/.test(body.subarray(0, 6).toString())) return 'image/gif'
  if (body.subarray(0, 4).toString() === 'RIFF' && body.subarray(8, 12).toString() === 'WEBP') return 'image/webp'
  if (body.subarray(0, 4).equals(Buffer.from([0, 0, 1, 0]))) return 'image/x-icon'
  if (/^(?:\s|\uFEFF)*(?:<\?xml[^>]*>\s*)?(?:<!--[\s\S]*?-->\s*)*<svg\b/i.test(body.toString('utf8'))) return 'image/svg+xml'
  return null
}
