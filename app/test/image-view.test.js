import test from 'node:test'
import assert from 'node:assert/strict'
import { installDom } from './fakedom.js'
import { all, find, text } from './browser.js'
import { h, morph } from '../public/ui.js'
import { messageFiles } from '../public/chat-ui.js'
import { closeImageView } from '../public/image-view.js'

function page() {
  const document = installDom()
  const listeners = new Map()
  document.body = h('div')
  document.documentElement = { classList: { set: new Set(), add(c) { this.set.add(c) }, remove(c) { this.set.delete(c) } } }
  document.addEventListener = (type, fn) => listeners.set(type, fn)
  document.removeEventListener = (type) => listeners.delete(type)
  return { document, key: (key) => listeners.get('keydown')?.({ key, preventDefault() {}, stopPropagation() {} }) }
}

test('a tap on an image in a message opens it full size; Escape puts the page back', () => {
  const { document, key } = page()
  const files = [{ path: 'Images/a.png', name: 'a.png', type: 'image/png' },
    { path: 'iris/art/b.png', root: 'spaces', name: 'b.png', type: 'image/png', alt: 'a fox' }]
  const host = h('div'); morph(host, messageFiles(files))
  const links = all(host, 'message-image')
  assert.equal(new URL(links[1].getAttribute('href'), 'http://x').searchParams.get('root'), 'spaces', 'an agent workspace image reads from its own root')
  assert.equal(find(links[1], 'message-image-name').previousSibling?.getAttribute?.('alt') ?? links[1].firstChild.getAttribute('alt'), 'a fox')
  let prevented = false
  links[1].__on.click({ preventDefault() { prevented = true }, stopPropagation() {}, button: 0 })
  assert.ok(prevented, 'the download link is not followed')
  const view = find(document.body, 'image-view')
  assert.ok(view)
  assert.equal(view.getAttribute('role'), 'dialog')
  assert.equal(text(find(view, 'image-view-name')), 'b.png')
  assert.equal(text(find(view, 'image-view-count')), '2 of 2')
  assert.ok(document.documentElement.classList.set.has('image-view-open'))
  key('ArrowRight')
  assert.equal(text(find(view, 'image-view-name')), 'a.png')
  key('Escape')
  assert.equal(find(document.body, 'image-view'), null)
  assert.ok(!document.documentElement.classList.set.has('image-view-open'))
})

test('a modified click keeps the browser’s own behaviour, and one image has no arrows', () => {
  const { document } = page()
  const host = h('div'); morph(host, messageFiles([{ path: 'Images/a.png', name: 'a.png', type: 'image/png' }]))
  const link = all(host, 'message-image')[0]
  let prevented = false
  link.__on.click({ preventDefault() { prevented = true }, stopPropagation() {}, metaKey: true, button: 0 })
  assert.equal(prevented, false)
  link.__on.click({ preventDefault() {}, stopPropagation() {}, button: 0 })
  const view = find(document.body, 'image-view')
  assert.equal(all(view, 'image-view-step').length, 0)
  assert.equal(find(view, 'image-view-count'), null)
  closeImageView()
})
