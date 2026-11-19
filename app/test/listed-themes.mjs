// The first-party looks beyond Latte and Plain (2026-11-18) are plugins from
// the Market, kept in this repository at ../plugins/_listed/<id>/ and
// published from there. Tests that hold them to the core's rules read them
// here, and give loadThemes a root of their own copies (a `_listed` folder
// also holds plugins that are not themes, which loadThemes refuses).
import { cp, mkdtemp, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
export const SHIPPED = new URL('../public/themes/', import.meta.url)
export const LISTED = new URL('../../plugins/_listed/', import.meta.url)
export const LISTED_THEMES = ['autumn', 'deep-forest', 'foundry', 'mist-trail', 'yosemite']
export const listedFile = (id, file) => fileURLToPath(new URL(`${id}/${file}`, LISTED))
export const readListed = (id, file, enc = 'utf8') => readFile(listedFile(id, file), enc)
let root = null
export async function listedRoot() {
  if (root) return root
  const dir = await mkdtemp(path.join(tmpdir(), 'listed-themes-'))
  for (const id of LISTED_THEMES) await cp(fileURLToPath(new URL(`${id}/`, LISTED)), path.join(dir, id), { recursive: true })
  return (root = dir)
}
// Every first-party look, as one catalog: the shipped two and the listed five.
export async function allRoots() { return [SHIPPED, await listedRoot()] }
// A first-party look's theme.css, wherever it lives now.
export const readThemeCSS = id => readFile(new URL(`${id}/theme.css`, LISTED_THEMES.includes(id) ? LISTED : SHIPPED), 'utf8')

// A listed look, installed into a fixture app from test/market-fixture.mjs
// (2026-11-18): its row as the broker lists an installed theme, and every
// file of its folder (theme.css, fonts.css, its faces) where the broker
// serves a plugin's files. For browser tests and captures that wear a look
// which is no longer built in.
export async function installListedTheme(app, id) {
  const { readdir } = await import('node:fs/promises')
  const manifest = await readListed(id, 'PLUGIN.md')
  const field = name => manifest.match(new RegExp(`^${name}: (.*)$`, 'm'))?.[1]
  const options = field('options') ? JSON.parse(field('options')) : []
  const row = { id, name: field('name'), version: field('version'), author: field('author'), api: 0, kind: ['theme'], places: [], overrides: [], layer: 'yours', folder: `plugins/${id}`,
    effective: true, on: true, switch: 'appearance', broken: null, warnings: [], has: { page_main: false, theme: true }, options,
    touches: { network: [], record: 'none', memory: 'none', server: false }, source: { kind: 'link', repo: 'loamhq/plugins', ref: 'v1.0.0', ref_kind: 'tag', commit: 'c'.repeat(40), url: `https://github.com/loamhq/plugins/tree/v1.0.0/${id}` } }
  app.installed.set(id, row)
  const types = { css: 'text/css; charset=utf-8', woff2: 'font/woff2', webp: 'image/webp', md: 'text/markdown', txt: 'text/plain' }
  for (const file of await readdir(fileURLToPath(new URL(`${id}/`, LISTED)))) {
    app.routes[`GET /v1/plugins/${id}/files/${file}`] = async (req, res) => { res.writeHead(200, { 'content-type': types[file.split('.').pop()] ?? 'application/octet-stream' }); res.end(await readListed(id, file, null)) }
  }
  return row
}
