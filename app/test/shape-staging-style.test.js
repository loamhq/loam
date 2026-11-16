import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'

test('Apps explicitly resets native button surfaces and gives anchors the same pill layout', async () => {
  const css = await readFile('public/apps.css', 'utf8')
  const row = /\.apps-row\s*\{([^}]+)\}/.exec(css)[1]
  assert.match(row, /background:var\(--paper-pane\)/)
  assert.match(row, /border:0;/)
  assert.match(row, /min-height:44px/)
  assert.match(css, /\.apps-toolbar \.pillbtn[^}]+text-decoration:none/)
})
test('staging defects are held by real computed styles at both widths, themes and modes', {
  skip: !process.env.LOAM_PLAYWRIGHT ? 'Set LOAM_PLAYWRIGHT and a working Chromium to run the computed-style matrix.' : false,
  timeout: 180000,
}, async t => {
  const { stdout } = await promisify(execFile)(process.execPath, ['test/shape-staging-browser.mjs'], { timeout: 170000, maxBuffer: 2 ** 20 })
  assert.equal(JSON.parse(stdout.trim()).result, 'passed')
  t.diagnostic(stdout.trim())
})
