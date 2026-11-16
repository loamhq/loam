import assert from 'node:assert/strict'
import { readFile, writeFile } from 'node:fs/promises'
const [beforePath, afterPath, output] = process.argv.slice(2)
if (!beforePath || !afterPath || !output) throw new Error('Usage: node tools/compare-button-geometry.mjs <before measurements.json> <after measurements.json> <output.json>')
const before = JSON.parse(await readFile(beforePath, 'utf8')), after = JSON.parse(await readFile(afterPath, 'utf8')), rows = []
for (const a of before) {
  const b = after.find(x => x.look === a.look && x.width === a.width && x.mode === a.mode)
  assert.ok(b); assert.equal(a.states.length, b.states.length)
  for (let i = 0; i < a.states.length; i++) {
    assert.equal(a.states[i].draft, b.states[i].draft)
    assert.equal(a.states[i].controls.length, b.states[i].controls.length)
    for (let j = 0; j < a.states[i].controls.length; j++) {
      const x = a.states[i].controls[j], y = b.states[i].controls[j]
      assert.equal(x.name, y.name)
      for (const key of ['x', 'y', 'width', 'height']) assert.ok(Math.abs(x.rect[key] - y.rect[key]) < .1, `${a.look} ${a.width} ${a.mode} ${x.name}: ${key} moved`)
      rows.push({ look: a.look, width: a.width, mode: a.mode, draft: a.states[i].draft, name: x.name, before: x.rect, after: y.rect })
    }
  }
}
await writeFile(output, JSON.stringify(rows, null, 2) + '\n')
console.log(`${rows.length} before/after control rectangles match within 0.1px in all ${before.reduce((n, r) => n + r.states.length, 0)} empty/typing comparisons.`)
