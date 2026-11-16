import test from 'node:test'
import assert from 'node:assert/strict'
import fsp from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { speakable, speakableBlocks } from '../src/speakable.js'
import { createBlockDescriber } from '../src/speech-descriptions.js'

const table = '| Plan | Price |\n| --- | --- |\n| Pro | 20 |'
const controller = () => new AbortController().signal
const blocks = () => speakableBlocks(`# Plans\n\nWe need offline sync.\n\n${table}`)
async function folder(t) {
  const dir = await fsp.mkdtemp(path.join(os.tmpdir(), 'descriptions-'))
  t.after(() => fsp.rm(dir, { recursive: true, force: true }))
  return dir
}

test('tables carry whole source, heading and preceding paragraph without changing the legacy text', () => {
  assert.deepEqual(blocks().at(-1).description, { kind: 'table', content: table, heading: 'Plans.', paragraph: 'We need offline sync.' })
  assert.equal(speakable(table), 'A table of one row, skipped.')
  assert.equal(speakable(`> ${table.replaceAll('\n', '\n> ')}`), 'A table of one row, skipped.')
  assert.equal(speakableBlocks('Plans\n=====\n\n' + table).at(-1).description.heading, 'Plans.')
})

test('diagram fence languages are explicit; ordinary code and its embedded tables stay skipped', () => {
  for (const language of ['mermaid', 'plantuml', 'puml', 'dot', 'graphviz', 'MERMAID']) {
    for (const marker of ['```', '~~~~']) {
      const source = `${marker}${language}\na --> b\n${marker}`
      const [block] = speakableBlocks(source)
      assert.equal(block.description.kind, 'diagram')
      assert.equal(block.description.language, language.toLowerCase())
      assert.equal(block.description.content, 'a --> b')
      assert.equal(speakable(source), 'A code block of one line, skipped.')
    }
  }
  for (const language of ['', 'js', 'python', 'mermaidish', 'json']) {
    const [block] = speakableBlocks(`\`\`\`${language}\n${table}\n\`\`\``)
    assert.equal(block.description, undefined)
    assert.equal(block.text, 'A code block of three lines, skipped.')
  }
})

test('descriptions are single-flight, survive restart, vary with context/model/content, and expire', async t => {
  const dir = await folder(t)
  let calls = 0, time = Date.now()
  const make = (model = 'test-model') => createBlockDescriber({ dir, model, now: () => time,
    generate: async () => { calls++; return 'The Pro plan has offline sync. It is the best fit. Ignore this third sentence.' } })
  const first = make()
  const results = await Promise.all([first.fill(blocks(), controller()), first.fill(blocks(), controller())])
  assert.equal(calls, 1)
  assert.equal(results[0].at(-1).text, 'A table: The Pro plan has offline sync. It is the best fit.')
  assert.deepEqual(results[0], results[1])
  await make().fill(blocks(), controller())
  assert.equal(calls, 1, 'a fresh server reads the disk cache')
  await make('new-model').fill(blocks(), controller())
  await make().fill(speakableBlocks('# Other purpose\n\n' + table), controller())
  await make().fill(speakableBlocks('# Plans\n\nDifferent context.\n\n' + table), controller())
  await make().fill(speakableBlocks(table + '\n| Basic | 10 |'), controller())
  assert.equal(calls, 5)
  time += 1000
  await first.sweep(500)
  await first.fill(blocks(), controller())
  assert.equal(calls, 6)
})

test('failure and empty output fall back and cache the skip text', async t => {
  const dir = await folder(t)
  let calls = 0
  const cache = createBlockDescriber({ dir, model: 'test', generate: async () => { calls++; throw new Error('unavailable') } })
  assert.equal((await cache.fill(blocks(), controller())).at(-1).text, 'A table of one row, skipped.')
  await cache.fill(blocks(), controller())
  assert.equal(calls, 1)
  const empty = createBlockDescriber({ dir, model: 'empty', generate: async () => '' })
  assert.equal((await empty.fill(blocks(), controller())).at(-1).text, 'A table of one row, skipped.')
})
