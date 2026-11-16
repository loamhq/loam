// The icons are generated, and committed: `tools/make-icons.py` is where they
// come from. So the script must make the same bytes every time it runs, and the
// files in public/ must be those bytes — a PNG edited by hand, or a script
// changed and not re-run, is a drift this catches.
//
// The two runs are the one that made public/ and the one this test makes. It
// lives here rather than in test/, and `npm test` runs it after the rest: the
// rasteriser keeps a core busy for several seconds, and the page's own tests,
// which are timed, failed now and then with it running beside them.

import test from 'node:test'
import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { mkdtemp, readdir, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'

const run = promisify(execFile)
const ROOT = path.resolve(import.meta.dirname, '..')
const PUBLIC = path.join(ROOT, 'public')

test('the icon script makes the same bytes on every run, and they are the committed icons', { timeout: 180000 }, async (t) => {
  const dir = await mkdtemp(path.join(tmpdir(), 'icons-'))
  t.after(() => rm(dir, { recursive: true }))
  await run('python3', [path.join(ROOT, 'tools/make-icons.py'), dir])

  const made = (await readdir(dir)).sort()
  assert.ok(made.includes('icon-1024.png') && made.includes('icon-maskable-1024.png'), `no 1024 icons in ${made}`)
  for (const name of made) {
    const [fresh, committed] = await Promise.all([dir, PUBLIC].map((d) => readFile(path.join(d, name))))
    assert.ok(fresh.equals(committed), `public/${name} is not what the script makes — re-run tools/make-icons.py`)
  }
  t.diagnostic(`${made.length} files, byte-identical to public/: ${made.join(', ')}`)
})

test('the manifest lists the 1024 icon, for a high-density splash screen', async () => {
  const manifest = JSON.parse(await readFile(path.join(PUBLIC, 'manifest.webmanifest'), 'utf8'))
  const icon = manifest.icons.find((i) => i.src === '/icon-1024.png')
  assert.deepEqual(icon, { src: '/icon-1024.png', sizes: '1024x1024', type: 'image/png', purpose: 'any' })
  assert.ok(manifest.icons.some((i) => i.sizes === '1024x1024' && i.purpose === 'maskable'), 'no maskable 1024')
})
