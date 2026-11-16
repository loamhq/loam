import test from 'node:test'
import assert from 'node:assert/strict'
import { providerModelFixture } from './capture-provider-models.mjs'
test('the provider fixture preserves alias/object/string model shapes and excludes private fields', () => {
  const fixture = providerModelFixture({ providers: [
    { id: 'claude-code', name: 'Claude Code', connected: true, login: { token: 'never retained' }, usage: { session_id: 'private' },
      models: [{ id: 'fable', name: 'Fable', api_key: 'never retained' }, 'opus'] },
    { id: 'codex', name: 'Codex', connected: false },
  ], installation: 'never retained' }, '2026-11-10T22:41:03Z')
  assert.deepEqual(fixture.providers, [
    { id: 'claude-code', name: 'Claude Code', connected: true, models: [{ id: 'fable', name: 'Fable' }, 'opus'] },
    { id: 'codex', name: 'Codex', connected: false },
  ])
  assert.equal(fixture.live_capture, true); assert.equal(fixture.captured_at, '2026-11-10T22:41:03Z')
  assert.equal(JSON.stringify(fixture).includes('never retained'), false)
})
test('a malformed response cannot silently replace the catalog fixture', () => {
  for (const body of [[], { providers: [null] }, { providers: [{ id: 'one', models: [null] }] }]) assert.throws(() => providerModelFixture(body))
})
