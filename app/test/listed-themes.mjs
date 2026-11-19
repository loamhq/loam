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
