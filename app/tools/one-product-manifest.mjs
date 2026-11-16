// Refresh evidence hashes after copying only the affected browser captures.
// Usage: LOAM_CHROME=<chromium> node tools/one-product-manifest.mjs <base-commit>
import assert from 'node:assert/strict'
import { readFile, readdir, writeFile, access } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import path from 'node:path'
const dir = 'docs/evidence/one-product'
const git = (...args) => execFileSync('git', args, { encoding: 'utf8' }).trim()
const base = process.argv[2]
assert.ok(base && process.env.LOAM_CHROME, 'Supply a base commit and LOAM_CHROME')
const previous = JSON.parse(await readFile(`${dir}/manifest.json`, 'utf8'))
const changed = git('diff', '--name-only', base).split('\n').filter((file) => file && !file.startsWith(dir + '/'))
const sourceFiles = [...new Set([...Object.keys(previous.sourceHashes), ...changed, 'tools/one-product-manifest.mjs'])].sort()
const hash = (data) => createHash('sha256').update(data).digest('hex')
const sourceHashes = {}
for (const file of sourceFiles) sourceHashes[file] = hash(await readFile(file))
const artifacts = {}
for (const file of (await readdir(dir)).filter((name) => name !== 'manifest.json').sort()) {
  const data = await readFile(`${dir}/${file}`)
  artifacts[file] = { sha256: hash(data), bytes: data.length }
  if (file.endsWith('.png')) {
    assert.equal(data.subarray(1, 4).toString(), 'PNG')
    artifacts[file].width = data.readUInt32BE(16); artifacts[file].height = data.readUInt32BE(20)
    assert.ok(artifacts[file].width && artifacts[file].height)
  }
}
const captures = JSON.parse(await readFile(`${dir}/captures.json`, 'utf8'))
assert.ok(captures.length > 0)
assert.equal(new Set(captures.map((capture) => capture.file)).size, captures.length)
assert.equal(Object.keys(artifacts).filter((file) => file.endsWith('.png')).length, captures.length)
for (const capture of captures) {
  assert.ok(artifacts[capture.file])
  if (capture.provenance?.startsWith('retained')) assert.equal(artifacts[capture.file].sha256, hash(execFileSync('git', ['show', `${base}:${dir}/${capture.file}`])))
}
const manifest = { baseCommit: git('rev-parse', base), browser: execFileSync(process.env.LOAM_CHROME, ['--version'], { encoding: 'utf8' }).trim(), node: process.version,
  pairedImages: captures.length, viewports: previous.viewports, themes: previous.themes,
  captureProvenance: Object.fromEntries(captures.map((capture) => [capture.file, capture.provenance])), sourceHashes, artifacts }
await writeFile(`${dir}/manifest.json`, JSON.stringify(manifest, null, 2) + '\n')
for (const file of (await readdir(dir)).filter((name) => name.endsWith('.md'))) for (const match of (await readFile(`${dir}/${file}`, 'utf8')).matchAll(/\]\(([^)]+)\)/g)) await access(path.resolve(dir, match[1]))
console.log(`${captures.length} PNGs, dimensions, capture coverage/retained hashes, evidence links and source/artifact hashes verified.`)
