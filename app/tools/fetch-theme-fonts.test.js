import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, writeFileSync, mkdirSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { execFileSync } from 'node:child_process'
import { newThemes } from './new-themes.js'

test('theme font generator reproduces committed CSS, weight descriptors, subset bytes and OFL notices offline', () => {
  const dir = mkdtempSync(join(tmpdir(), 'theme-fonts-'))
  try {
    mkdirSync(join(dir, 'tools'))
    for (const file of ['fetch-theme-fonts.js', 'new-themes.js']) writeFileSync(join(dir, 'tools', file), readFileSync(new URL(file, import.meta.url)))
    const fixtures = newThemes.map(({ id, font }) => {
      const root = new URL(`../public/themes/${id}/`, import.meta.url)
      return { id, font, css: readFileSync(new URL('fonts.css', root), 'utf8'), license: readFileSync(new URL('OFL.txt', root), 'utf8'),
        bytes: Object.fromEntries(['display-latin.woff2', 'display-latin-ext.woff2'].map(file => [file, readFileSync(new URL(file, root)).toString('base64')])) }
    })
    writeFileSync(join(dir, 'fetch-fixture.mjs'), `
      const fixtures = ${JSON.stringify(fixtures)};
      globalThis.fetch = async value => {
        const url = new URL(value);
        if (url.hostname === 'fonts.googleapis.com') {
          const family = url.searchParams.get('family').split(':')[0], f = fixtures.find(f => f.font === family);
          return { ok: true, text: async () => f.css.replaceAll('/themes/', 'https://font-fixture.invalid/') };
        }
        if (url.hostname === 'font-fixture.invalid') {
          const [, id, file] = url.pathname.split('/'), f = fixtures.find(f => f.id === id);
          return { ok: true, arrayBuffer: async () => Buffer.from(f.bytes[file], 'base64') };
        }
        const family = url.pathname.split('/').at(-2), f = fixtures.find(f => f.font.toLowerCase().replaceAll(' ', '') === family);
        return { ok: true, text: async () => f.license };
      };
    `)
    execFileSync(process.execPath, ['--import', join(dir, 'fetch-fixture.mjs'), join(dir, 'tools/fetch-theme-fonts.js')])
    for (const f of fixtures) {
      const root = join(dir, 'public/themes', f.id)
      assert.equal(readFileSync(join(root, 'fonts.css'), 'utf8'), f.css)
      assert.equal(readFileSync(join(root, 'OFL.txt'), 'utf8'), f.license)
      for (const [file, bytes] of Object.entries(f.bytes)) assert.equal(readFileSync(join(root, file)).toString('base64'), bytes)
    }
  } finally { rmSync(dir, { recursive: true, force: true }) }
})
