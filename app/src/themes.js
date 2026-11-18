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
