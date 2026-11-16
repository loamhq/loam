// The Enter key, as a decision.
//
// The owner, 2026-09-22: "When I press enter on keyboard it should go newline and
// not send message. On desktop we can have enter with another key send the
// message". The defect it names is one a test can hold exactly, because it is
// not about pixels: given a keydown, does the composer send? So `keys.js` takes
// the event's own fields and answers, and this asserts every case of it.
//
// **Fixture-only in the usual way** — these are objects shaped like a keydown,
// not a keyboard. What a real key does is the browser's, and there is no
// browser on the box this is built on. What is proved here is the rule the page
// applies to whatever the browser hands it.

import test from 'node:test'
import assert from 'node:assert/strict'

import { isMac, sends, sendHint } from '../public/keys.js'

const key = (over = {}) => ({ key: 'Enter', metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, ...over })

test('Enter on its own is a new line and never a send', () => {
  assert.equal(sends(key()), false, 'bare Enter sent the message, which is the whole defect')
  assert.equal(sends(key({ shiftKey: true })), false)
  assert.equal(sends(key({ altKey: true })), false)
})

test('Ctrl+Enter and Cmd+Enter send, on either platform', () => {
  // Both, on both: one of the two is the idiom wherever a person has come from,
  // and accepting the other costs nothing.
  assert.equal(sends(key({ ctrlKey: true })), true)
  assert.equal(sends(key({ metaKey: true })), true)
  assert.equal(sends(key({ metaKey: true, shiftKey: true })), true, 'a modifier held alongside is still a send')
})

test('a key that is not Enter is not a send, whatever is held with it', () => {
  assert.equal(sends(key({ key: 'a', metaKey: true })), false)
  assert.equal(sends(key({ key: 'Tab', ctrlKey: true })), false)
  assert.equal(sends(null), false)
  assert.equal(sends({}), false)
})

test('an input method’s Enter belongs to the input method', () => {
  // Picking a character out of a candidate list is that keyboard's Enter, not
  // this app's — and both flags are checked because browsers disagree about
  // which one they set.
  assert.equal(sends(key({ ctrlKey: true, isComposing: true })), false)
  assert.equal(sends(key({ ctrlKey: true, keyCode: 229 })), false)
})

test('a Mac is named by whichever field the browser still answers truthfully', () => {
  assert.equal(isMac({ userAgentData: { platform: 'macOS' } }), true)
  assert.equal(isMac({ platform: 'MacIntel' }), true)
  // An iPad that claims to be a Mac, and an iPhone, both take ⌘ from a
  // hardware keyboard.
  assert.equal(isMac({ userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)' }), true)
  assert.equal(isMac({ platform: 'Linux x86_64', userAgent: 'Mozilla/5.0 (X11; Linux x86_64)' }), false)
  assert.equal(isMac({ platform: 'Win32' }), false)
  // A browser that answers nothing is not a Mac, which is the safer half: the
  // hint says Ctrl and Cmd works anyway.
  assert.equal(isMac(undefined), false)
  assert.equal(isMac({}), false)
})

test('the hint names the chord the machine in front of the person prints', () => {
  assert.equal(sendHint(true), '⌘↩ to send')
  assert.equal(sendHint(false), 'Ctrl+↩ to send')
})
