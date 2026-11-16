import test from 'node:test'
import assert from 'node:assert/strict'
import { attachmentFilePath } from '../public/attach.js'
import { messageFiles } from '../public/chat-ui.js'
import { h, morph } from '../public/ui.js'
import { installDom, settle, clearWrites, sinceSettled } from './fakedom.js'
import { all } from './browser.js'

test('message downloads use root-relative paths without changing attachment records', () => {
  const suffix = '98_ATTACHMENTS/preview/Board notes.png'
  for (const value of [suffix, `/some/root/${suffix}`, `/${suffix}`]) assert.equal(attachmentFilePath(value), suffix)
  assert.equal(attachmentFilePath('Notes/image.png'), 'Notes/image.png')
  for (const value of ['', null, '/unknown/image.png', '/root/98_ATTACHMENTS/../secret', 'a/./b', 'a\\b', 'a\u0000b']) assert.equal(attachmentFilePath(value), null, String(value))
  installDom()
  const host = h('div'), files = [{ path: `/some/root/${suffix}`, name: 'Board notes.png', type: 'image/png' }]
  morph(host, messageFiles(files))
  const link = all(host, 'message-image')[0]
  const url = new URL(link.getAttribute('href'), 'http://fixture')
  assert.equal(url.pathname, '/api/files/download')
  assert.equal(url.searchParams.get('root'), 'vault')
  assert.equal(url.searchParams.get('path'), suffix)
  assert.equal(files[0].path, `/some/root/${suffix}`)
})

test('redraws retain loaded image nodes and a failure remains a filename chip', () => {
  installDom()
  const host = h('div'), files = [{ path: '98_ATTACHMENTS/preview/one.png', name: 'one.png', type: 'image/png' }]
  morph(host, messageFiles(files))
  const link = all(host, 'message-image')[0], image = link.firstChild
  settle(host); clearWrites()
  for (let i = 0; i < 20; i++) morph(host, messageFiles(files))
  assert.equal(all(host, 'message-image')[0].firstChild, image)
  assert.deepEqual(sinceSettled(), [])
  image.__on.error({ target: image })
  clearWrites()
  for (let i = 0; i < 20; i++) morph(host, messageFiles(files))
  assert.equal(image.hidden, true)
  assert.equal(all(host, 'message-image').length, 0)
  assert.equal(all(host, 'attachment-chip')[0], link)
  assert.equal(all(host, 'chip-name')[0].textContent, 'one.png')
  assert.deepEqual(sinceSettled(), [])
  // A changed record updates its displayed name rather than retaining stale DOM.
  files[0].name = 'renamed.png'
  morph(host, messageFiles(files))
  assert.equal(all(host, 'message-image-name')[0].textContent, 'renamed.png')
})
