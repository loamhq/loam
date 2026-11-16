// The one way this page puts text on the clipboard: `navigator.clipboard`, and
// the off-screen textarea where that is absent or refuses.

import test from 'node:test'
import assert from 'node:assert/strict'
import { copyText } from '../public/clipboard.js'

// A document just big enough for the fallback: a body to put the textarea in,
// and a `copy` command that reads what is selected.
function fakeDocument() {
  const log = []
  let selected = null
  const doc = {
    body: {
      appendChild: (node) => log.push(['append', node.value]),
      removeChild: (node) => log.push(['remove', node.value]),
    },
    createElement: () => {
      const node = { value: '', style: {}, attrs: {}, setAttribute: (k, v) => (node.attrs[k] = v), select: () => (selected = node.value) }
      return node
    },
    execCommand: (name) => {
      log.push([name, selected])
      return true
    },
  }
  return { doc, log }
}

function withGlobals(navigator, document, fn) {
  const had = { navigator: Object.getOwnPropertyDescriptor(globalThis, 'navigator'), document: globalThis.document }
  Object.defineProperty(globalThis, 'navigator', { value: navigator, configurable: true, writable: true })
  globalThis.document = document
  return fn().finally(() => {
    if (had.navigator) Object.defineProperty(globalThis, 'navigator', had.navigator)
    globalThis.document = had.document
  })
}

test('the clipboard API is asked first, and the fallback is not touched', async () => {
  const written = []
  const { doc, log } = fakeDocument()
  await withGlobals({ clipboard: { writeText: async (v) => written.push(v) } }, doc, async () => {
    assert.equal(await copyText('| a |\n|---|'), true)
  })
  assert.deepEqual(written, ['| a |\n|---|'])
  assert.deepEqual(log, [])
})

test('with no clipboard API, an off-screen textarea is selected and copied, and taken away', async () => {
  const { doc, log } = fakeDocument()
  await withGlobals({}, doc, async () => {
    assert.equal(await copyText('const a = 1'), true)
  })
  assert.deepEqual(log, [['append', 'const a = 1'], ['copy', 'const a = 1'], ['remove', 'const a = 1']])
})

test('a clipboard API that refuses falls back, and nothing at all is a false', async () => {
  const { doc, log } = fakeDocument()
  await withGlobals({ clipboard: { writeText: async () => { throw new Error('NotAllowedError') } } }, doc, async () => {
    assert.equal(await copyText('x'), true)
  })
  assert.deepEqual(log.map((entry) => entry[0]), ['append', 'copy', 'remove'])
  await withGlobals({}, {}, async () => {
    assert.equal(await copyText('x'), false)
  })
})
