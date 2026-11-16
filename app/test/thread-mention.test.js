import test from 'node:test'
import assert from 'node:assert/strict'
import { installDom } from './fakedom.js'
import { composerHint, chatDock } from '../public/chat-ui.js'

installDom()
test('thread hints contain only the wide send chord and omit the empty phone row', () => {
  const phone = composerHint({ wide: false, chord: 'Ctrl+Enter to send', thread: true })
  const wide = composerHint({ wide: true, chord: 'Ctrl+Enter to send', thread: true })
  assert.equal(phone, '')
  assert.equal(wide, 'Ctrl+Enter to send')
  assert.equal(chatDock(document.createElement('div'), phone).childNodes.length, 1)
  assert.equal(chatDock(document.createElement('div'), wide).childNodes.length, 2)
})
test('classic composer keeps its reassurance and wide chord separator', () => {
  assert.equal(composerHint({ wide: false, chord: 'Ctrl+Enter to send', agent: 'Scout' }), 'Scout asks before anything irreversible')
  assert.equal(composerHint({ wide: true, chord: 'Ctrl+Enter to send', agent: 'Scout' }), 'Ctrl+Enter to send · Scout asks before anything irreversible')
})
