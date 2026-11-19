// Plugin pages that explain themselves (2026-11-19): a guide's plain part and
// its technical part, a theme's options shown as Appearance shows them, and
// a computed colour's hex for a palette swatch.
import test from 'node:test'
import assert from 'node:assert/strict'
import { installDom } from './fakedom.js'
installDom()
const { guideParts } = await import('../public/plugin-settings.js')
const { hexOf, PLAIN_PALETTE } = await import('../public/theme-showcase.js')
const { shownOptions } = await import('../public/theme-options.js')

test('a guide splits at its rule or its Technical details heading, never inside code', () => {
  assert.deepEqual(guideParts('Plain words.\n\n---\n\n## Technical details\n\nIt reads `x`.'), { plain: 'Plain words.', technical: 'It reads `x`.' })
  assert.deepEqual(guideParts('Plain.\n\n## Technical details\n\nHosts.'), { plain: 'Plain.', technical: 'Hosts.' })
  assert.deepEqual(guideParts('Only plain words.'), { plain: 'Only plain words.', technical: '' })
  assert.deepEqual(guideParts('Before.\n\n```\n---\n```\n\nAfter.'), { plain: 'Before.\n\n```\n---\n```\n\nAfter.', technical: '' })
  assert.deepEqual(guideParts(null), { plain: '', technical: '' })
})

test('a theme preview shows the options Appearance would, for the faces chosen', () => {
  const options = [{ id: 'agent-shapes' }, { id: 'face-content' }, { id: 'agent-colours' }, { id: 'latte-mug' }]
  assert.deepEqual(shownOptions(options, { 'agent-shapes': 'circle' }).map(o => o.id), ['agent-shapes', 'agent-colours'])
  assert.deepEqual(shownOptions(options, { 'agent-shapes': 'signature' }).map(o => o.id), ['agent-shapes', 'face-content', 'agent-colours'])
  assert.deepEqual(shownOptions(options, { 'agent-shapes': 'latte-art' }).map(o => o.id), ['agent-shapes', 'latte-mug'])
})

test('a swatch names its colour in hex, with alpha when it has one', () => {
  assert.equal(hexOf('rgb(225, 231, 223)'), '#E1E7DF')
  assert.equal(hexOf('rgba(23, 32, 25, 0.5)'), '#172019 · 50%')
  assert.equal(hexOf('nonsense'), null)
  assert.equal(PLAIN_PALETTE.every(p => /^--[a-z-]+$/.test(p.token) && p.name), true)
})
