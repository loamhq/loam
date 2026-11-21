import test from 'node:test'
import assert from 'node:assert/strict'
import { composerKeys, isMac, mobileKeyboard, sends, sendHint } from '../public/keys.js'

const key = (extra = {}) => ({ key: 'Enter', ...extra })

test('Enter sends by default and starts a new line with the modifier preference', () => {
  assert.equal(sends(key()), true)
  assert.equal(sends(key(), 'modifier'), false)
  assert.equal(sends(key(), null), false, 'do not guess while a saved preference is still loading')
  assert.equal(sends(key(), 'enter', true), false, 'mobile Return stays a new line')
})

test('Command or Control can send, and Shift+Enter always starts a new line', () => {
  for (const mode of ['enter', 'modifier']) {
    for (const modifier of [{}, { ctrlKey: true }, { metaKey: true }]) {
      assert.equal(sends(key({ ...modifier, shiftKey: true }), mode), false)
      assert.equal(sends(key({ ...modifier, altKey: true }), mode), false)
      assert.equal(sends(key({ ...modifier, repeat: true }), mode), false)
      assert.equal(sends(key({ ...modifier, isComposing: true }), mode), false)
      assert.equal(sends(key({ ...modifier, keyCode: 229 }), mode), false)
    }
    assert.equal(sends(key({ ctrlKey: true }), mode), true)
    assert.equal(sends(key({ metaKey: true }), mode, true), true)
  }
  assert.equal(sends(key({ key: 'Tab', ctrlKey: true })), false)
  assert.equal(sends(null), false)
})

test('the hint reflects the current preference and the browser’s keyboard', () => {
  assert.equal(isMac({ userAgentData: { platform: 'macOS' } }), true)
  assert.equal(isMac({ platform: 'MacIntel' }), true)
  assert.equal(isMac({ platform: 'Win32' }), false)
  assert.equal(mobileKeyboard({ platform: 'MacIntel', maxTouchPoints: 5 }), true)
  assert.equal(mobileKeyboard({ userAgent: 'Android' }), true)
  assert.equal(mobileKeyboard({ platform: 'MacIntel', maxTouchPoints: 0 }), false)
  assert.equal(sendHint(true, 'modifier'), '⌘↩ to send')
  assert.equal(sendHint(false, 'modifier'), 'Ctrl+↩ to send')
  assert.equal(sendHint(false, 'enter'), 'Enter to send · Shift+Enter for a new line')
  assert.equal(sendHint(true, 'modifier', true), 'Return adds a new line')
})

test('an older settings read cannot undo a saved selection', async () => {
  let reply
  const api = (method, path, body) => {
    assert.equal(path, '/settings/composer')
    return method === 'GET' ? new Promise(resolve => { reply = resolve }) : Promise.resolve(body)
  }
  const keys = composerKeys({ api, changed() {}, initial: 'enter', nav: { platform: 'Win32' } })
  const read = keys.refresh()
  await keys.set('modifier')
  reply({ send_key: 'enter' })
  await read
  assert.equal(keys.value, 'modifier')
  assert.equal(keys.hint, 'Ctrl+↩ to send')
})
