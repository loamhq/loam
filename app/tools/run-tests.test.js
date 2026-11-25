import test from 'node:test'
import assert from 'node:assert/strict'
import { testConcurrency } from './run-tests.mjs'

test('testConcurrency scales cleanly to CPU count across 1..16+ vCPUs', () => {
  assert.equal(testConcurrency({ cpus: 1, env: {} }), 1)
  assert.equal(testConcurrency({ cpus: 2, env: {} }), 1)
  assert.equal(testConcurrency({ cpus: 4, env: {} }), 2)
  assert.equal(testConcurrency({ cpus: 8, env: {} }), 4)
  assert.equal(testConcurrency({ cpus: 16, env: {} }), 8)
  assert.equal(testConcurrency({ cpus: 32, env: {} }), 8)
})

test('testConcurrency caps browser-enabled runs to avoid stacking Chromium instances', () => {
  assert.equal(testConcurrency({ cpus: 2, env: { LOAM_CHROME: '/usr/bin/chromium-headless-shell' } }), 1)
  assert.equal(testConcurrency({ cpus: 4, env: { LOAM_CHROME: '/usr/bin/chromium-headless-shell' } }), 1)
  assert.equal(testConcurrency({ cpus: 8, env: { LOAM_PLAYWRIGHT: '/path/playwright' } }), 2)
  assert.equal(testConcurrency({ cpus: 16, env: { LOAM_CHROME: '/usr/bin/chromium-headless-shell' } }), 2)
})

test('APP_TEST_CONCURRENCY overrides the default when valid', () => {
  assert.equal(testConcurrency({ cpus: 16, env: { APP_TEST_CONCURRENCY: '3' } }), 3)
  assert.equal(testConcurrency({ cpus: 4, env: { APP_TEST_CONCURRENCY: '1', LOAM_CHROME: '/bin/chrome' } }), 1)
  assert.equal(testConcurrency({ cpus: 4, env: { APP_TEST_CONCURRENCY: '0' } }), 2)
  assert.equal(testConcurrency({ cpus: 4, env: { APP_TEST_CONCURRENCY: 'bogus' } }), 2)
})
