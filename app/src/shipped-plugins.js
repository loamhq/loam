// Offline catalog for this app's own plain-file packages. The host remains the
// canonical catalog when reachable. This parser deliberately reads only our
// shipped manifest subset; third-party manifests are parsed by the host.
import { readdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { themeImage } from '../public/theme-image.js'
// A block list of one-line flow maps under `name:` (2026-11-19: `palette`,
// `screenshots`), as `  - {key: value, key: "quoted, value"}`.
function flowList(header, name) {
  const block = new RegExp(`^${name}:\\s*\\r?\\n((?:  - \\{.*\\}[ \\t]*(?:\\r?\\n|$))+)`, 'm').exec(header)?.[1]
  if (!block) return []
  return block.split(/\r?\n/).filter(line => line.trim()).map(line => {
    const out = {}
    for (const m of line.trim().replace(/^- \{|\}$/g, '').matchAll(/([a-z_]+):\s*("(?:[^"\\]|\\.)*"|[^,]*)/g)) out[m[1]] = m[2].startsWith('"') ? JSON.parse(m[2]) : m[2].trim()
    return out
  })
}
const unquote = value => value?.startsWith('"') ? JSON.parse(value) : value

export function shippedManifest(text) {
  const header = /^---\r?\n([\s\S]*?)\r?\n---/.exec(text)?.[1]
  if (!header) throw new Error('A shipped plugin needs PLUGIN.md frontmatter.')
  const field = name => new RegExp(`^${name}:\\s*(.*)$`, 'm').exec(header)?.[1]?.trim()
  const list = name => (field(name) ?? '').replace(/^\[|\]$/g, '').split(',').map(value => value.trim()).filter(Boolean)
  const touch = name => new RegExp(`^  ${name}:\\s*(.*)$`, 'm').exec(header)?.[1]?.trim()
  const defaultOn = field('default_on')
  // Theme loading also uses this metadata parser for third-party packages.
  // Only our shipped catalog requires an explicit activation default.
  if (defaultOn !== undefined && !['true', 'false'].includes(defaultOn)) throw new Error('default_on must be true or false.')
  const network = [...header.matchAll(/^    - host:\s*(.+)\r?\n      why:\s*(.+)$/gm)].map(([, host, why]) => ({ host, why }))
  // The providers whose keys it uses (2026-11-18), as `keys:` lists them.
  const keys = [...header.matchAll(/^  - provider:\s*(\S+)\r?\n(?:    optional: (true|false)\r?\n)?    why:\s*(.+)$/gm)].map(([, provider, optional, why]) => ({ provider, why, ...(optional === 'true' ? { optional: true } : {}) }))
  return { ...(defaultOn === undefined ? {} : { default_on: defaultOn === 'true' }), id: field('id'), name: field('name'), version: field('version'), author: field('author'), api: Number(field('api')), kind: list('kind'), ...themeImage({ image: field('image'), image_position: field('image_position'), image_dark: field('image_dark'), image_position_dark: field('image_position_dark') }), ...(field('contributes') ? { contributes: JSON.parse(field('contributes')) } : {}), ...(field('options') ? { options: JSON.parse(field('options')) } : {}), ...(field('agent_settings') ? { agent_settings: JSON.parse(field('agent_settings')) } : {}), places: list('places'), overrides: list('overrides'), ...(keys.length ? { keys } : {}),
    ...(field('description') ? { description: unquote(field('description')) } : {}),
    ...(flowList(header, 'screenshots').length ? { screenshots: flowList(header, 'screenshots') } : {}),
    ...(flowList(header, 'palette').length ? { palette: flowList(header, 'palette') } : {}),
    touches: { network, record: touch('record') ?? 'none', memory: touch('memory') ?? 'none', server: touch('server') === 'true' } }
}
export async function shippedPlugins(publicDir) {
  const plugins = []
  for (const root of ['themes', 'plugins']) {
    const entries = await readdir(path.join(publicDir, root), { withFileTypes: true }).catch(() => [])
    for (const entry of entries.filter(entry => entry.isDirectory())) {
      const folder = `${root}/${entry.name}`
      const text = await readFile(path.join(publicDir, folder, 'PLUGIN.md'), 'utf8').catch(() => null)
      if (!text) continue
      const manifest = shippedManifest(text), theme = manifest.kind.includes('theme')
      if (typeof manifest.default_on !== 'boolean') throw new Error('A shipped plugin must declare default_on: true or false.')
      if (manifest.id !== entry.name) throw new Error('A shipped plugin folder must match its id.')
      plugins.push({ ...manifest, layer: 'shipped', folder, source: { kind: 'loam' }, effective: true, on: manifest.default_on, switch: theme ? 'appearance' : 'plugin', broken: null, warnings: [], has: { page_main: !theme, theme },
        declaration: { touches: manifest.touches, overrides: manifest.overrides, page_code: !theme }, local: !theme ? `/plugins/${entry.name}/page/main.js` : null })
    }
  }
  return plugins
}
