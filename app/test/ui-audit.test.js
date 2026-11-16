import test from 'node:test'
import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'

test('the full browser audit covers both widths, three theme/mode pairs and four locale/zone pairs', {
  skip: !process.env.LOAM_UI_AUDIT || !process.env.LOAM_PLAYWRIGHT ? 'Set LOAM_UI_AUDIT=1 and LOAM_PLAYWRIGHT with a working Chromium to run the screenshot sweep.' : false,
  timeout: 600000,
}, async t => {
  const { stdout } = await promisify(execFile)(process.execPath, ['test/ui-audit-browser.mjs'], { timeout: 590000, maxBuffer: 2 ** 20 })
  const result = JSON.parse(stdout.trim().split('\n').at(-1))
  assert.equal(result.result, 'passed')
  assert.equal(result.variants, 6)
  assert.equal(result.clocks, 4)
  assert.ok(result.surfaces >= 420)
  t.diagnostic(stdout.trim())
})
