// Dictation and Read aloud left Loam's core for the Market in 0.2.9: a fresh
// install ships neither (installation.test.js says so). Their copies are in
// this repository at ../plugins/_listed/<id>/, and the tests that drive them
// install them here, as an install that got them from the Market and has them
// on: a catalog row each, in the shape the page reads, whose module is the
// listed copy's own file.
import { readFile } from 'node:fs/promises'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { shippedManifest } from '../src/shipped-plugins.js'
export const AUDIO_PLUGINS = ['dictation', 'read-aloud']
export const LISTED_PLUGINS = new URL('../../plugins/_listed/', import.meta.url)
export const listedPluginDir = id => fileURLToPath(new URL(`${id}/`, LISTED_PLUGINS))
export const listedManifest = async id => shippedManifest(await readFile(new URL(`${id}/PLUGIN.md`, LISTED_PLUGINS), 'utf8'))
export async function audioRows() {
  return Promise.all(AUDIO_PLUGINS.map(async id => {
    const { default_on: _, ...manifest } = await listedManifest(id)
    return { ...manifest, layer: 'yours', folder: id, source: { kind: 'link' }, effective: true, on: true, switch: 'plugin', broken: null, warnings: [],
      has: { page_main: true, theme: false }, declaration: { touches: manifest.touches, overrides: manifest.overrides, page_code: true },
      local: pathToFileURL(fileURLToPath(new URL(`${id}/page/main.js`, LISTED_PLUGINS))).href }
  }))
}
