// An HTML file from the vault, made into a page that can be shown without
// running anything and without reaching anything.
//
// The page arrives at the browser under a policy of its own (`PREVIEW_POLICY`):
// `sandbox` gives it an opaque origin with no scripts, no forms and no popups,
// and `default-src 'none'` gives it no network at all. What it may have is
// inline styles and `data:` images and fonts — so its own local assets are
// fetched here, through the host's Files routes and its rules, and inlined:
// raster images as bounded `data:` URLs, stylesheets as `<style>` blocks.
// Nothing on the network is ever fetched, and nothing outside the file's own
// root is asked for — a `..` that would climb out of it is not followed.

export const PREVIEW_POLICY =
  "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; " +
  "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"

export const PREVIEW_LIMITS = {
  htmlBytes: 16 * 1024 * 1024, // static document, including embedded images
  assets: 32, // local files fetched for one page, images and stylesheets together
  imageBytes: 2 * 1024 * 1024, // one image
  totalBytes: 8 * 1024 * 1024, // every image in the page together
  styleChars: 256 * 1024, // one stylesheet
}

const RASTER = {
  png: { type: 'image/png', magic: [0x89, 0x50, 0x4e, 0x47] },
  jpg: { type: 'image/jpeg', magic: [0xff, 0xd8, 0xff] },
  jpeg: { type: 'image/jpeg', magic: [0xff, 0xd8, 0xff] },
  gif: { type: 'image/gif', magic: [0x47, 0x49, 0x46, 0x38] },
  webp: { type: 'image/webp', magic: [0x52, 0x49, 0x46, 0x46] },
}

const extOf = (p) => (/\.([a-z0-9]{1,8})$/i.exec(p)?.[1] ?? '').toLowerCase()

// A reference written in the page, resolved against the page's own folder in
// the same root. `null` for anything that is not a plain relative path: a
// scheme (`http:`, `data:`, `javascript:`…), a protocol-relative `//`, an
// absolute `/`, a backslash or control character, or a `..` above the root.
export function assetPath(fromPath, ref) {
  if (typeof ref !== 'string') return null
  let r = ref.trim()
  if (r === '' || /^[a-z][a-z0-9+.-]*:/i.test(r) || r.startsWith('/') || r.startsWith('#')) return null
  r = r.split(/[?#]/)[0]
  if (/[\\\u0000-\u001f\u007f]/.test(r)) return null
  let parts
  try {
    parts = r.split('/').map((s) => decodeURIComponent(s))
  } catch {
    return null
  }
  const out = String(fromPath ?? '').split('/').filter(Boolean).slice(0, -1)
  for (const part of parts) {
    if (part === '' || part === '.') continue
    if (part === '..') {
      if (out.length === 0) return null
      out.pop()
      continue
    }
    if (/[\\/\u0000]/.test(part)) return null
    out.push(part)
  }
  return out.length ? out.join('/') : null
}

// `readText(root, path)` → string or throws; `readBytes(root, path, max)` →
// Buffer or throws. Both go through the host.
export async function buildPreview({ root, path, html, readText, readBytes, limits = PREVIEW_LIMITS }) {
  let fetched = 0
  let total = 0
  const cache = new Map()

  async function image(ref, from) {
    const p = assetPath(from, ref)
    const want = p && RASTER[extOf(p)]
    if (!want) return null
    if (cache.has(`i:${p}`)) return cache.get(`i:${p}`)
    if (fetched >= limits.assets) return null
    fetched += 1
    let uri = null
    try {
      const buf = await readBytes(root, p, limits.imageBytes)
      if (buf && buf.length <= limits.imageBytes && total + buf.length <= limits.totalBytes && want.magic.every((b, i) => buf[i] === b)) {
        total += buf.length
        uri = `data:${want.type};base64,${buf.toString('base64')}`
      }
    } catch {}
    cache.set(`i:${p}`, uri)
    return uri
  }

  // A stylesheet's own `url(…)`s are relative to the stylesheet; `@import` is
  // dropped rather than followed.
  async function css(text, from) {
    let out = String(text).replace(/@import[^;]*;?/gi, '')
    const urls = [...out.matchAll(/url\(\s*(['"]?)([^'")]+)\1\s*\)/gi)]
    for (const m of urls) {
      if (/^data:/i.test(m[2].trim())) continue
      const uri = await image(m[2], from)
      out = out.split(m[0]).join(uri ? `url("${uri}")` : 'url("")')
    }
    return out.replace(/<\/style/gi, '<\\/style')
  }

  async function stylesheet(ref) {
    const p = assetPath(path, ref)
    if (!p || extOf(p) !== 'css' || fetched >= limits.assets) return null
    fetched += 1
    try {
      const text = await readText(root, p)
      if (typeof text !== 'string' || text.length > limits.styleChars) return null
      return await css(text, p)
    } catch {
      return null
    }
  }

  let doc = String(html ?? '')
  // Inert anyway under the policy; removed so nothing in the source even looks
  // like it runs, and so a `<base>` or a refresh cannot redirect what is left.
  doc = doc.replace(/<script\b[\s\S]*?(<\/script\s*>|$)/gi, '')
  doc = doc.replace(/<(base|meta\s+http-equiv)\b[^>]*>/gi, '')

  const links = [...doc.matchAll(/<link\b[^>]*>/gi)]
  for (const m of links) {
    const tag = m[0]
    const isStyle = /\brel\s*=\s*["']?[^"'>]*stylesheet/i.test(tag)
    const href = /\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/i.exec(tag)
    const ref = href ? href[1] ?? href[2] ?? href[3] : null
    const inlined = isStyle && ref ? await stylesheet(ref) : null
    doc = doc.split(tag).join(inlined !== null ? `<style>${inlined}</style>` : '')
  }

  const blocks = [...doc.matchAll(/<style\b([^>]*)>([\s\S]*?)<\/style\s*>/gi)]
  for (const m of blocks) {
    const next = await css(m[2], path)
    if (next !== m[2]) doc = doc.split(m[0]).join(`<style${m[1]}>${next}</style>`)
  }

  const imgs = [...doc.matchAll(/<img\b[^>]*>/gi)]
  for (const m of imgs) {
    const tag = m[0]
    const src = /\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/i.exec(tag)
    const ref = src ? src[1] ?? src[2] ?? src[3] : null
    if (!ref || /^data:/i.test(ref.trim())) continue
    const uri = await image(ref, path)
    const next = tag.replace(src[0], `src="${uri ?? ''}"`).replace(/\bsrcset\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/i, '')
    doc = doc.split(tag).join(next)
  }

  return `<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${PREVIEW_POLICY.replace(/^sandbox; /, '').replace(/; frame-ancestors 'self'$/, '')}">${doc}`
}
