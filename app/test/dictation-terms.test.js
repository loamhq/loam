// Dictation's terms note, from its own server (`GET /x/dictation/terms`, the
// gateway taking `/x/dictation` off): the one vault note of names and terms,
// read fresh on every ask, whole, uncached, and no request can name another
// file. Until 1.1.0 this was the broker's `GET /v1/dictation-terms`
// (tests/test_broker_dictation_terms.py, retired with it).

import test from 'node:test'
import assert from 'node:assert/strict'
import fsp from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { termsNote } from '../../plugins/_listed/dictation/server/config.js'
import { dictationApp } from './audio-servers.mjs'
import { shut } from './harness.js'

async function vault(t) {
  const root = await fsp.mkdtemp(path.join(os.tmpdir(), 'terms-vault-'))
  t.after(() => fsp.rm(root, { recursive: true, force: true }))
  const dir = path.join(root, 'notes')
  await fsp.mkdir(path.join(dir, '99_SYSTEM'), { recursive: true })
  return { root, dir, note: path.join(dir, '99_SYSTEM', 'Dictation terms.md') }
}

// What Loam tells a plugin's server about the vault: its own folder there.
const envFor = (dir) => ({ LOAM_VAULT_DATA: '99_SYSTEM/Loam/data/dictation', LOAM_VAULT_DATA_DIR: path.join(dir, '99_SYSTEM/Loam/data/dictation') })

test('the note is found from the plugin’s own vault folder, and nothing else names it', () => {
  assert.deepEqual(termsNote(envFor('/v/notes')), { file: '/v/notes/99_SYSTEM/Dictation terms.md', vault: '/v/notes' })
  assert.deepEqual(termsNote({ ...envFor('/v/notes'), DICTATION_TERMS: '/elsewhere/t.md' }), { file: '/elsewhere/t.md', vault: '/v/notes' })
  assert.deepEqual(termsNote({}), { file: null, vault: null }, 'outside Loam there is no note')
})

test('GET /terms answers the note whole and uncached, fresh each time; 404 when there is none', async (t) => {
  const { dir, note } = await vault(t)
  const app = await dictationApp(envFor(dir))
  t.after(() => shut(app.server))
  let res = await fetch(`${app.url}/terms`)
  assert.equal(res.status, 404)
  const missing = await res.json()
  assert.match(missing.error, /no dictation terms note/)
  assert.equal(missing.path, '99_SYSTEM/Dictation terms.md')

  await fsp.writeFile(note, '# Dictation terms\n\nAda, Grace Hopper\n')
  res = await fetch(`${app.url}/terms`)
  assert.equal(res.status, 200)
  assert.equal(res.headers.get('cache-control'), 'no-store')
  const body = await res.json()
  assert.deepEqual({ ...body, modified: typeof body.modified }, {
    path: '99_SYSTEM/Dictation terms.md',
    obsidian_url: 'obsidian://open?vault=notes&file=99_SYSTEM%2FDictation%20terms',
    modified: 'string',
    text: '# Dictation terms\n\nAda, Grace Hopper\n',
  })
  assert.match(body.modified, /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/)

  await fsp.writeFile(note, 'Quill\n')
  assert.equal((await (await fetch(`${app.url}/terms`)).json()).text, 'Quill\n', 'read fresh, never cached')
})

test('no query or longer path names another file, and only GET is a read', async (t) => {
  const { dir, note } = await vault(t)
  await fsp.writeFile(note, 'Ada\n')
  await fsp.writeFile(path.join(dir, 'secret.md'), 'not for dictation\n')
  const app = await dictationApp(envFor(dir))
  t.after(() => shut(app.server))
  for (const q of ['?path=secret.md', '?file=../secret.md']) {
    const body = await (await fetch(`${app.url}/terms${q}`)).json()
    assert.equal(body.text, 'Ada\n', q)
  }
  for (const tail of ['/secret.md', '/../secret.md']) assert.equal((await fetch(`${app.url}/terms${tail}`)).status, 404, tail)
  assert.equal((await fetch(`${app.url}/terms`, { method: 'POST' })).status, 404)
})

test('a note outside the vault has its absolute path and no Obsidian link', async (t) => {
  const { root, dir } = await vault(t)
  const outside = path.join(root, 'outside.md')
  await fsp.writeFile(outside, 'Ada\n')
  const app = await dictationApp({ ...envFor(dir), DICTATION_TERMS: outside })
  t.after(() => shut(app.server))
  const body = await (await fetch(`${app.url}/terms`)).json()
  assert.equal(body.path, outside)
  assert.equal(body.obsidian_url, null)
})
