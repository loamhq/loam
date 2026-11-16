import test from 'node:test'
import assert from 'node:assert/strict'
import { loadCoreTokens } from '../src/themes.js'
import { AGENT_COLOURS, readAvatarColours } from '../public/avatar-colours.js'
import { agentLook, setAvatarColours } from '../public/sessions.js'
import { contrast } from '../tools/theme-contrast.mjs'
import { avatarLook } from '../public/ui.js'

// c5fbcc0:public/style.css, light-mode hues in the legacy slot order.
const ORIGINAL_HUES = ['#6E7A52', '#4F6478', '#B0684A', '#7A5A72', '#4E7A72', '#9C4F44', '#8A7D4A', '#5C6E8A', '#8C6A4E', '#6B8A6E', '#8A5E80']
const slots = [49, 51, 52, 54, 56, 58, 60, 62, 64, 66, 68]

test('all eleven legacy slots resolve to the original fixed hues and palette-aware initials in both modes', async t => {
  t.after(() => setAvatarColours())
  const tokens = await loadCoreTokens()
  assert.equal(AGENT_COLOURS.length, 11)
  for (const mode of ['light', 'dark']) {
    assert.deepEqual(AGENT_COLOURS.map(token => tokens[mode].get(`--${token}`)), ORIGINAL_HUES)
    assert.equal(tokens[mode].get('--text-on-avatar'), '#FFFFFF')
    for (const [i, name] of AGENT_COLOURS.entries()) assert.ok(contrast(tokens[mode].get(`--${name}-ink`) || tokens[mode].get('--text-on-avatar'), ORIGINAL_HUES[i]) >= 4.5, `${name} small-face contrast`)
  }
  for (const [i, slot] of slots.entries()) {
    const normalized = readAvatarColours({ sample: `c${slot}` })
    assert.equal(normalized.sample, AGENT_COLOURS[i])
    setAvatarColours(normalized)
    const face = avatarLook(agentLook('sample'))
    assert.equal(face.color, `var(--agent-avatar-tone-${face.tone}, var(--${AGENT_COLOURS[i]}))`)
    assert.equal(face.ink, `var(--agent-avatar-ink-${face.tone}, var(--${AGENT_COLOURS[i]}-ink, var(--text-on-avatar)))`)
  }
})

test('unconfigured IDs reach all eleven original slots, including the last three', t => {
  t.after(() => setAvatarColours())
  setAvatarColours()
  // These IDs have pre-theme hash residues 0 through 10 modulo 11.
  const ids = ['c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm']
  assert.deepEqual(ids.map(id => agentLook(id).color), AGENT_COLOURS.map(token => `var(--${token})`))
})
