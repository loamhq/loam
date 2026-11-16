import test from 'node:test'
import assert from 'node:assert/strict'
import { installDom } from './fakedom.js'
import { personaGrid } from '../public/chat-ui.js'
import { agentLook } from '../public/sessions.js'
import { resolved } from './layout.js'

test('roster names cannot style the shared avatar initial', () => {
  installDom()
  for (const wide of [false, true]) {
    const grid = personaGrid({ agents: [{ id: 'sample', name: 'Sample' }], wide, look: agentLook })
    const [face, label] = grid.firstChild.childNodes
    const glyph = face.firstChild
    const viewport = { width: wide ? 1440 : 390 }
    assert.equal(glyph.getAttribute('class'), 'blob-letter')
    // This cascade reader checks declarations, not inherited/computed paint.
    // The browser companion checks the rendered glyph and its contrast.
    const declarations = resolved(glyph, viewport)
    for (const property of ['font-size', 'font-weight', 'color']) {
      assert.equal(declarations[property], undefined, `${property} must inherit from blob()`)
    }
    assert.equal(resolved(label, viewport)['font-size'], '12.5px')
    assert.equal(resolved(label, viewport).color, 'var(--paper-ink)')
  }
})
