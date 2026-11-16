import test from 'node:test'
import assert from 'node:assert/strict'
import { installDom, settle, clearWrites, sinceSettled } from './fakedom.js'
import { POURS, pourFor } from '../public/latte-avatar.js'
import { blob, morph } from '../public/ui.js'
import { agentLook, createAgents } from '../public/sessions.js'
import { createTheme } from '../public/theme.js'
import { loadThemes } from '../src/themes.js'
import { appearanceValues } from '../src/appearance.js'
import { mergeAppearance } from '../public/theme-options.js'

test('pours cover eight distinct designs and remain stable across roster and colour changes', () => {
  const ids = Array.from({ length: 100 }, (_, i) => `sample-${i}`)
  assert.equal(POURS.length, 8)
  assert.deepEqual(ids.slice(0, 8).map(id => pourFor(id)[0]), ['rosetta', 'twin-hearts', 'swirl', 'round', 'leaf', 'tulip', 'heart', 'monk'])
  assert.equal(new Set(ids.map(id => pourFor(id)[0])).size, 8)
  assert.equal(new Set(POURS.map(([, shapes]) => JSON.stringify(shapes))).size, 8)
  for (const id of ids) {
    assert.equal(pourFor(id), pourFor(id))
    const before = agentLook(id, createAgents([{ id, name: 'Sample' }]))
    const after = agentLook(id, createAgents([{ id: 'new', name: 'New' }, { id, name: 'Renamed' }]))
    assert.equal(pourFor(before.agentId), pourFor(after.agentId))
  }
})

test('latte faces preserve text/caret offsets and unchanged redraws mutate nothing', () => {
  const doc = installDom(), root = doc.createElement('div'), look = agentLook('sample')
  const face = () => blob(look, { size: 44, font: 19 })
  morph(root, [face()]); settle(root); clearWrites()
  morph(root, [face()])
  assert.deepEqual(sinceSettled(), [])
  assert.equal(root.firstChild.textContent, look.initial)
  const decorative = blob(look, { inline: true, decorative: true })
  assert.equal(decorative.textContent, '')
  assert.equal(decorative.getAttribute('data-latte-initial'), look.initial)
})

test('latte sub-options use the existing appearance route and retain other device/colour choices', async () => {
  const doc = installDom(); doc.documentElement = doc.createElement('html'); doc.querySelector = () => null
  const themes = (await loadThemes()).themes, host = appearanceValues(null, themes), writes = []
  const request = async (method, patch) => {
    if (patch) { writes.push(patch); for (const kind of Object.keys(patch)) host[kind] = mergeAppearance(host[kind], patch[kind]) }
    return structuredClone(host)
  }
  const controller = () => createTheme(null, { kind: 'phone', boot: { themes, values: structuredClone(host), available: true }, request })
  const theme = controller()
  theme.set({ options: { latte: { 'agent-shapes': 'latte-art', 'latte-mug': 'off', 'latte-inside': 'initials' } } }); await theme.save()
  assert.deepEqual(writes, [{ phone: { options: { latte: { 'agent-shapes': 'latte-art', 'latte-mug': 'off', 'latte-inside': 'initials' } } } }])
  assert.equal(host.phone.options.latte['agent-colours'], 'palette')
  assert.equal(host.desktop.options.latte['agent-shapes'], 'circle')
  assert.equal(host.desktop.options.latte['latte-mug'], 'on')
  assert.equal(controller().value.options.latte['latte-inside'], 'initials')
  theme.set({ theme: 'plain' }); assert.equal(doc.documentElement.hasAttribute('data-option-latte-mug'), false)
  theme.set({ theme: 'latte' }); assert.equal(doc.documentElement.getAttribute('data-option-latte-mug'), 'off')
  await theme.save()
  host.phone.options.latte['latte-mug'] = 'on'; await theme.refresh()
  assert.equal(doc.documentElement.getAttribute('data-option-latte-mug'), 'on')
})
