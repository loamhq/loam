import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { spawnSync } from 'node:child_process'

test('speech measurement reports old/new Full chunks offline without exposing document text', async (t) => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'loam-speech-measure-'))
  t.after(() => fs.rm(dir, { recursive: true, force: true }))
  const file = path.join(dir, 'note.md')
  const paragraph = 'A sentence about the design. '.repeat(18)
  await fs.writeFile(file, `# First section\n\n${paragraph}\n\n# Second section\n\n${paragraph}\n\n# Third section\n\n${paragraph}`)
  const env = { ...process.env, GEMINI_API_KEY: '', SPEECH_CHUNK_CHARS: '1000', SPEECH_CONCURRENCY: '4' }
  const out = spawnSync(process.execPath, ['tools/measure-speech.mjs', file, 'full'], { env, encoding: 'utf8', timeout: 10_000 })
  assert.equal(out.status, 0, out.stderr)
  const report = JSON.parse(out.stdout)
  assert.equal(report.event, 'speech-document-comparison')
  assert.equal(report.full_chunks_before, 3)
  assert.equal(report.full_chunks_after, 3)
  assert.equal(report.concurrency_per_job, 4)
  assert.ok(report.full_chunk_lengths_after.every((n) => n > 450 && n <= 1000))
  assert.ok(!out.stdout.includes('First section') && !out.stdout.includes(file))
  const refused = spawnSync(process.execPath, ['tools/measure-speech.mjs', file, 'full', '--synthesise'], { env, encoding: 'utf8', timeout: 10_000 })
  assert.equal(refused.status, 1)
  assert.match(refused.stderr, /Synthesis requires a read-aloud plugin key/)
})
