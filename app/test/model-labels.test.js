import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { modelLabel, modelTooltip, listedModelName } from '../public/providers.js'
import { replyChip } from '../public/thread-experience.js'
const { providers } = JSON.parse(await readFile(new URL('./fixtures/threads/provider-alias-catalog.json', import.meta.url)))
const claude = providers.find((p) => p.id === 'claude-code'), codex = providers.find((p) => p.id === 'codex')

test('concrete Claude IDs resolve through alias-only catalogs using the recorded version', () => {
  for (const [id, name] of [
    ['claude-fable-5-1', 'Fable 5.1'], ['claude-opus-5-5', 'Opus 5.5'],
    ['claude-sonnet-4-5-20250929', 'Sonnet 4.5'], ['claude-haiku-4-20251001', 'Haiku 4'],
    ['claude-fable-5.1', 'Fable 5.1'],
  ]) {
    assert.equal(listedModelName(claude, id), null, 'the exact-only lookup misses the concrete record')
    assert.equal(modelLabel(claude, id).name, name)
    assert.equal(replyChip({ engine: claude.id, model: id }, providers).model, name, 'the chip uses the same resolver')
    assert.ok(modelTooltip(claude, id).includes(id), 'the complete ID is retained')
  }
  const newer = { ...claude, models: [{ id: 'fable', name: 'Claude Fable 9.9 Latest' }] }
  assert.deepEqual(modelLabel(newer, 'claude-fable-5-1'), { name: 'Fable 5.1', fullName: 'Claude Fable 5.1' })
  const strings = { ...claude, models: ['fable', 'opus'] }
  assert.equal(modelLabel(strings, 'claude-fable-5-1').name, 'Fable 5.1')
  assert.equal(modelLabel(strings, 'fable').name, 'Fable')
  assert.equal(modelLabel({ ...claude, models: [{ id: 'fable', name: 'Claude Fable' }] }, 'fable').name, 'Fable')
})
test('an exact catalog name takes precedence over derivation, even when disconnected', () => {
  const provider = { ...claude, connected: false, models: [...claude.models, { id: 'claude-fable-5-1', name: 'Claude Story 5.1 Preview' }] }
  assert.deepEqual(modelLabel(provider, 'claude-fable-5-1'), { name: 'Story 5.1', fullName: 'Claude Story 5.1 Preview' })
  assert.equal(modelLabel({ ...claude, models: [{ id: 'fable', name: 'Tale' }] }, 'claude-fable-5-1').name, 'Tale 5.1', 'alias display names are not hard-coded')
})
test('OpenAI/Codex preserves exact catalog names and has a short numeric GPT fallback', () => {
  assert.equal(modelLabel(codex, 'gpt-6.1-sol').name, 'GPT-6.1-Sol')
  assert.equal(modelLabel({ ...codex, models: [{ id: 'gpt-6.1-sol', name: 'Sol for coding' }] }, 'gpt-6.1-sol').name, 'Sol for coding')
  for (const provider of [undefined, { ...codex, models: [] }]) {
    assert.equal(modelLabel(provider, 'gpt-6.1-sol').name, 'GPT-6.1')
    assert.equal(modelLabel(provider, 'gpt-6.1-sol-20261110').name, 'GPT-6.1')
    assert.equal(modelLabel(provider, 'gpt-6-sol').name, 'GPT-6')
  }
  assert.equal(modelLabel({ id: 'openai', models: [{ id: 'sol', name: 'GPT-6.1-Sol' }] }, 'sol').name, 'GPT-6.1-Sol')
})
test('missing catalogs still give conventional IDs a readable name without guessing unknown formats', () => {
  assert.equal(modelLabel(undefined, 'claude-opus-5-5').name, 'Opus 5.5')
  assert.equal(modelLabel(undefined, 'claude-future-family-6-2').name, 'Future Family 6.2')
  assert.deepEqual(modelLabel(undefined, 'vendor-opaque-id'), { name: 'vendor-opaque-id', fullName: 'vendor-opaque-id' })
  assert.equal(modelTooltip(undefined, 'vendor-opaque-id'), 'vendor-opaque-id')
  for (const id of [undefined, null, '', 42]) {
    assert.deepEqual(modelLabel(claude, id), { name: null, fullName: null })
    assert.equal(modelTooltip(claude, id), '')
  }
})
test('short display names never collapse different recorded model identities or mutate a preference', () => {
  const first = { engine: 'codex', model: 'gpt-6.1-sol' }, second = { engine: 'codex', model: 'gpt-6.1-terra' }
  assert.equal(replyChip(first).model, replyChip(second).model)
  assert.notEqual(modelTooltip(undefined, first.model), modelTooltip(undefined, second.model))
  assert.deepEqual(first, { engine: 'codex', model: 'gpt-6.1-sol' })
})
