// Exact decoded-pixel comparison of two full capture tours, for both looks.
// Optional audit dependencies use the same setup as the inventory driver.
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile, writeFile, mkdir, copyFile } from 'node:fs/promises'
import path from 'node:path'
import { sharp } from '../docs/design-system/deps.mjs'

const [before, after, output] = process.argv.slice(2)
if (!output) throw new Error('Usage: node tools/compare-theme-inventory.mjs <before-dir> <after-dir> <evidence-dir>')
await mkdir(output, { recursive: true })
const hash = data => createHash('sha256').update(data).digest('hex')
const rows = [], runs = [], tokens = {}
for (const look of ['latte', 'plain']) {
  const dirs = [before, after].map(root => path.join(root, look))
  const [a, b] = await Promise.all(dirs.map(async dir => JSON.parse(await readFile(path.join(dir, 'browser.json'), 'utf8'))))
  for (const run of [a, b]) for (const key of ['errors', 'blocked', 'missing']) assert.deepEqual(run[key], [], `${look}: ${key}`)
  assert.equal(a.browser, b.browser)
  assert.deepEqual(a.captures.map(c => c.filename).sort(), b.captures.map(c => c.filename).sort())
  assert.deepEqual(a.tokenValues, b.tokenValues, `${look}: computed root tokens changed`)
  assert.deepEqual(a.selectorSamples, b.selectorSamples, `${look}: recorded selector metrics changed`)
  tokens[look] = { before: a.tokenValues, after: b.tokenValues }
  runs.push({ look, browser: a.browser, captures: a.captures.length, computedTokensIdentical: true, selectorMetricsIdentical: true, errors: [], blocked: [], missing: [] })
  for (const capture of a.captures) {
    const files = dirs.map(dir => path.join(dir, 'captures', capture.filename))
    const pngs = await Promise.all(files.map(file => readFile(file)))
    const [oldImage, newImage] = await Promise.all(pngs.map(png => sharp(png).ensureAlpha().raw().toBuffer({ resolveWithObject: true })))
    assert.deepEqual(oldImage.info, newImage.info, capture.filename)
    const { width, height } = oldImage.info
    let pixels = 0, left = width, top = height, right = -1, bottom = -1
    const a = oldImage.data, b = newImage.data
    if (!a.equals(b)) for (let i = 0; i < a.length; i += 4) {
      if (a[i] !== b[i] || a[i + 1] !== b[i + 1] || a[i + 2] !== b[i + 2] || a[i + 3] !== b[i + 3]) {
        pixels++
        const x = i / 4 % width, y = Math.floor(i / 4 / width)
        left = Math.min(left, x); right = Math.max(right, x); top = Math.min(top, y); bottom = Math.max(bottom, y)
      }
    }
    const row = { look, surface: capture.surface, mode: capture.theme, width, height, pixels,
      beforePNG: hash(pngs[0]), afterPNG: hash(pngs[1]), beforePixels: hash(oldImage.data), afterPixels: hash(newImage.data) }
    // Retain every differing pair and representative unchanged UI pairs.
    if (pixels || ['thread', 'settings-appearance'].includes(capture.surface)) {
      row.images = []
      for (const [index, phase] of ['before', 'after'].entries()) {
        const filename = `${look}-${phase}-${capture.filename}`
        await copyFile(files[index], path.join(output, filename)); row.images.push(filename)
      }
    }
    if (pixels) row.bounds = { left, top, right, bottom }
    rows.push(row)
  }
}
const differences = rows.filter(row => row.pixels)
const summary = { capturesCompared: rows.length, differingCaptures: differences.length, changedPixels: rows.reduce((sum, row) => sum + row.pixels, 0), runs }
await writeFile(path.join(output, 'comparisons.json'), JSON.stringify({ summary, surfaces: rows }, null, 2) + '\n')
await writeFile(path.join(output, 'computed-tokens.json'), JSON.stringify(tokens, null, 2) + '\n')
console.log(JSON.stringify(summary, null, 2))
for (const row of differences) console.log(`${row.look}/${row.surface}/${row.width}/${row.mode}: ${row.pixels} pixels, ${JSON.stringify(row.bounds)}`)
if (differences.length) process.exitCode = 1
