import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, writeFileSync, mkdirSync, mkdtempSync, rmSync, readdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { execFileSync } from 'node:child_process'

test('font generator reproduces committed faces and thread-only continuous weights without changing Classic or font bytes', () => {
  const dir = mkdtempSync(join(tmpdir(), 'loam-fonts-'))
  try {
    mkdirSync(join(dir, 'tools'))
    const fonts = new URL('../public/fonts/', import.meta.url)
    const committed = readFileSync(new URL('fonts.css', fonts), 'utf8')
    const blocks = [...committed.matchAll(/\/\* (latin(?:-ext)?) \*\/(\s*@font-face \{[^}]*\})/g)]
    const original = blocks.filter((b) => !b[2].includes("'Thread Figtree'"))
    const source = original.map((b) => `/* ${b[1]} */${b[2]}`.replaceAll('url(/fonts/', 'url(https://font-fixture.invalid/')).join('\n')
    const bytes = Object.fromEntries(readdirSync(fonts).filter((n) => n.endsWith('.woff2')).map((n) => [n, readFileSync(new URL(n, fonts)).toString('base64')]))
    writeFileSync(join(dir, 'tools/fetch-fonts.mjs'), readFileSync(new URL('./fetch-fonts.js', import.meta.url)))
    writeFileSync(join(dir, 'fetch-fixture.mjs'), `
      const source = ${JSON.stringify(source)}, bytes = ${JSON.stringify(bytes)};
      globalThis.fetch = async (url) => url.includes('fonts.googleapis.com')
        ? { text: async () => source }
        : { arrayBuffer: async () => Buffer.from(bytes[new URL(url).pathname.slice(1)], 'base64') };
    `)
    execFileSync(process.execPath, ['--import', join(dir, 'fetch-fixture.mjs'), join(dir, 'tools/fetch-fonts.mjs')])
    assert.equal(readFileSync(join(dir, 'public/fonts/fonts.css'), 'utf8'), committed, 'generated stylesheet has no drift')
    const threadFaces = blocks.filter((b) => b[2].includes("'Thread Figtree'"))
    assert.equal(threadFaces.length, 2)
    for (const b of threadFaces) assert.match(b[2], /font-weight: 300 900;/)
    for (const [name, base64] of Object.entries(bytes)) assert.equal(readFileSync(join(dir, 'public/fonts', name)).toString('base64'), base64)
  } finally { rmSync(dir, { recursive: true, force: true }) }
})
