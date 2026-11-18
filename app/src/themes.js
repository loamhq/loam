import { readFile, readdir } from 'node:fs/promises'
import path from 'node:path'
import { parseCoreTokens, validateThemeCSS, validateThemeOptions } from '../public/theme-css.js'
import { shippedManifest } from './shipped-plugins.js'
import { themeImage } from '../public/theme-image.js'
export { validateThemeCSS, validateThemeOptions } from '../public/theme-css.js'
const shipped = new URL('../public/themes/', import.meta.url)
const slug = /^[a-z][a-z0-9-]*$/
export async function loadCoreTokens() { return parseCoreTokens(await readFile(new URL('../public/tokens.css', import.meta.url), 'utf8')) }

function previewCSS(id, css, options, defaults) {
  return ['light', 'dark'].map(mode => {
    const resolved = new Map(defaults[mode])
    const rules = [...css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^{}]*)\}/g)]
      .filter(rule => !rule[1].includes('[data-theme=') || rule[1].includes(`[data-theme="${mode}"]`))
      .filter(rule => [...rule[1].matchAll(/\[data-option-([^=]+)="([^"]+)"\]/g)].every(m => options.find(o => o.id === m[1])?.default === m[2]))
      .sort((a, b) => a[1].split('[').length - b[1].split('[').length)
    for (const rule of rules) for (const m of rule[2].matchAll(/(--[a-z][a-z0-9-]*):\s*([^;]+);/g)) resolved.set(m[1], m[2].trim())
    return `:root[data-theme="${mode}"] .theme-preview[data-look="${id}"] { ${[...resolved].map(([k, v]) => `${k}:${v};`).join('')} }`
  }).join('\n')
}
// Host rows select the effective layer. Local plain-file themes are the
// first-paint/offline fallback, never a second reading of the Yours folder.
export async function loadThemes(roots = [shipped], { rows = null, readTheme = null } = {}) {
  const defaults = await loadCoreTokens(), tokens = new Set(defaults.light.keys()), local = new Map(), failures = {}
  for (const root of roots) for (const entry of (await readdir(root, { withFileTypes: true })).sort((a,b) => a.name.localeCompare(b.name))) {
    if (!entry.isDirectory()) continue
    const folder = root instanceof URL ? new URL(`${entry.name}/`, root) : path.join(root, entry.name)
    const file = name => folder instanceof URL ? new URL(name, folder) : path.join(folder, name)
    const manifest = shippedManifest(await readFile(file('PLUGIN.md'), 'utf8'))
    const { id, name, version, author, kind, api } = manifest
    if (api !== 0 || !kind.includes('theme') || !slug.test(id) || id !== entry.name || local.has(id) || typeof name !== 'string' || !name.trim() || typeof author !== 'string' || !author.trim() || !/^\d+\.\d+\.\d+$/.test(version)) throw new Error(`Invalid theme manifest: ${entry.name}`)
    const options = validateThemeOptions(manifest.options, id), css = validateThemeCSS(await readFile(file('theme.css'), 'utf8'), id, defaults, options)
    local.set(id, { theme: { id, name, version, author, modes: ['light', 'dark'], ...themeImage(manifest), ...(options.length ? { options } : {}) }, css })
  }
  if (!local.has('latte') || !local.has('plain')) throw new Error('The shipped Latte & Newspaper and Plain themes are required.')
  const chosen = Array.isArray(rows) ? new Map() : new Map(local)
  if (Array.isArray(rows)) for (const row of rows.filter(row => row.effective && !row.broken && row.kind?.includes('theme'))) {
    try {
      if (!slug.test(row.id) || !Number.isInteger(row.api) || row.api > 0) throw new Error(`Unsupported theme API or id: ${row.id}`)
      if (chosen.has(row.id)) throw new Error(`Duplicate effective theme: ${row.id}`)
      const builtin = row.layer === 'shipped' ? local.get(row.id) : null
      const options = validateThemeOptions(builtin?.theme.options ?? row.options, row.id)
      const css = builtin ? builtin.css : await readTheme(row)
      validateThemeCSS(css, row.id, defaults, options)
      chosen.set(row.id, { theme: { id: row.id, name: row.name, author: row.author, version: row.version, layer: row.layer, modes: ['light', 'dark'], ...themeImage({ ...builtin?.theme, ...row }), ...(options.length ? { options } : {}) }, css })
    } catch (error) { failures[row.id] = error.message }
  }
  for (const id of ['latte','plain']) if (!chosen.has(id)) chosen.set(id, local.get(id))
  const themes = [...chosen.values()].map(value => value.theme).sort((a,b) => a.id === 'latte' ? -1 : b.id === 'latte' ? 1 : a.name.localeCompare(b.name))
  const styles = themes.flatMap(theme => { const css = chosen.get(theme.id).css; return [css, previewCSS(theme.id, css, theme.options ?? [], defaults)] })
  return { themes, css: styles.join('\n'), tokens, failures }
}

// A theme from the Market (2026-11-18), before it is installed: its own
// theme.css, read from the listing's link by the broker, held to the same
// rules as an installed theme's (tokens only, nothing fetched), and its
// swatch scoping (`.theme-preview[data-look=<id>]`) beside it. The page
// loads this to draw the theme's card and, on Try it, to wear it.
export async function marketThemeCSS(id, css, options = []) {
  if (!slug.test(id)) throw new Error(`Invalid theme id: ${id}`)
  const defaults = await loadCoreTokens()
  const checked = validateThemeOptions(options?.length ? options : undefined, id)
  validateThemeCSS(css, id, defaults, checked)
  return `${css}\n${previewCSS(id, css, checked, defaults)}\n`
}

// A theme's own display faces (2026-11-18, when the first-party looks left
// the shipped set for the Market): the `fonts.css` in an installed theme's
// folder, held to @font-face rules and nothing else. Each `src` is a font
// file beside it (`url(display-latin.woff2) format('woff2')`), rewritten to
// the plugin's files route, so a theme's fonts come from its own folder and
// never from another host. Anything else in the file refuses the whole file.
const FONT_DESCRIPTORS = new Set(['font-family', 'font-style', 'font-weight', 'font-display', 'font-stretch', 'unicode-range', 'src'])
const FONT_SRC = /^url\(\s*(['"]?)([A-Za-z0-9_-]+(?:\/[A-Za-z0-9_-]+)*\.(?:woff2|woff))\1\s*\)(?:\s+format\(\s*(['"])(woff2|woff)\3\s*\))?$/
export function themeFontsCSS(id, css) {
  if (!slug.test(id)) throw new Error(`Invalid theme id: ${id}`)
  if (typeof css !== 'string' || css.length > 64 * 1024) throw new Error('A theme’s fonts.css is at most 64 KB.')
  const text = css.replace(/\/\*[\s\S]*?\*\//g, '')
  const faces = [], rule = /@font-face\s*\{([^{}]*)\}/g
  let rest = text.replace(rule, (_, body) => { faces.push(body); return '' })
  if (rest.trim()) throw new Error('A theme’s fonts.css holds @font-face rules only.')
  if (faces.length > 16) throw new Error('A theme’s fonts.css has at most 16 faces.')
  const out = faces.map(body => {
    const lines = []
    for (const part of body.split(';').map(s => s.trim()).filter(Boolean)) {
      const colon = part.indexOf(':')
      const name = part.slice(0, colon).trim().toLowerCase(), value = part.slice(colon + 1).trim()
      if (colon < 1 || !FONT_DESCRIPTORS.has(name)) throw new Error(`A theme’s font rule may not say ${name || part}.`)
      if (name === 'src') {
        const sources = value.split(',').map(s => s.trim()).map(source => {
          const m = FONT_SRC.exec(source)
          if (!m) throw new Error('A theme’s font is a .woff2 or .woff file in its own folder.')
          const file = m[2].split('/').map(encodeURIComponent).join('/')
          return `url(/api/plugins/${id}/files/${file})${m[4] ? ` format('${m[4]}')` : ''}`
        })
        lines.push(`  src: ${sources.join(', ')};`)
      } else {
        if (!/^[A-Za-z0-9 ,'"+\-%.]+$/.test(value) || value.length > 2000) throw new Error(`A theme’s ${name} is plain words, numbers and ranges.`)
        lines.push(`  ${name}: ${value};`)
      }
    }
    if (!lines.some(l => l.startsWith('  src:')) || !lines.some(l => l.startsWith('  font-family:'))) throw new Error('A theme’s font rule names its family and its file.')
    return `@font-face {\n${lines.join('\n')}\n}`
  })
  return out.join('\n') + (out.length ? '\n' : '')
}
