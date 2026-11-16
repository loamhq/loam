// What the composer is carrying, asserted without a browser: the cap, the
// states a chip goes through, what a message ends up sending, and how a list of
// attachments is read off the record.

import test from 'node:test'
import assert from 'node:assert/strict'

import {
  createAttachments,
  addFile,
  removeAttachment,
  clearAttachments,
  markProgress,
  markStored,
  markFailed,
  stored,
  uploading,
  tooBig,
  attachmentList,
  carriesFiles,
  filesFrom,
  imagesFrom,
  pastedName,
  isImage,
  fileName,
  bytes,
} from '../public/attach.js'

const file = (name, size, type = '') => ({ name, size, type })

test('a file over the host’s cap is refused before a byte of it moves', () => {
  const cap = 10 * 1024 * 1024
  assert.equal(tooBig(file('holiday.mov', 31 * 1024 * 1024), cap), 'That file is 31 MB, and a chat carries 10 MB at most.')
  assert.equal(tooBig(file('notes.md', 4096), cap), null)
  // Exactly the cap is not over it.
  assert.equal(tooBig(file('exact.bin', cap), cap), null)
  // No cap known yet — the page refuses nothing and the app's own route is the
  // gate. A page that guessed at a number would refuse files it should not.
  assert.equal(tooBig(file('holiday.mov', 31 * 1024 * 1024), null), null)
})

test('a chip goes sending → so far → stored, and the message carries the host’s path', () => {
  const state = createAttachments()
  const item = addFile(state, file('bank-export-aug.csv', 31_744))

  assert.equal(uploading(state), true, 'nothing is stored yet')
  assert.deepEqual(stored(state), [], 'and nothing is ready to send')

  markProgress(state, item, 15_872)
  assert.equal(item.progress, 0.5)

  markStored(state, item, {
    attachments: [
      { path: '/store/2026-09-21/bank-export-aug.csv', name: 'bank-export-aug.csv', size: 31_744, type: 'text/csv' },
    ],
  })
  assert.equal(item.path, '/store/2026-09-21/bank-export-aug.csv')
  assert.equal(item.progress, 1)
  assert.equal(uploading(state), false)
  assert.deepEqual(stored(state), [
    { path: '/store/2026-09-21/bank-export-aug.csv', name: 'bank-export-aug.csv', size: 31_744, type: 'text/csv' },
  ])
})

test('the name a chip carries is the name the host stored, not the one on the disk', () => {
  const state = createAttachments()
  const item = addFile(state, file('Board notes (final).md', 900, 'text/markdown'))
  // The host sanitises the name and makes it unique in the day's folder, so the
  // chip that goes on saying 'Board notes (final).md' is naming a file that is
  // not there.
  markStored(state, item, {
    attachments: [{ path: '/store/2026-09-21/Board-notes--final-.md', name: 'Board-notes--final-.md', size: 900, type: 'text/markdown' }],
  })
  assert.equal(item.name, 'Board-notes--final-.md')
  assert.equal(stored(state)[0].path, '/store/2026-09-21/Board-notes--final-.md')
})

test('a refusal is the host’s sentence, and nothing of that file is sent', () => {
  const state = createAttachments()
  const item = addFile(state, file('huge.bin', 999))
  markFailed(state, item, 'A file can be at most 25 MB.')

  assert.equal(item.error, 'A file can be at most 25 MB.')
  assert.equal(uploading(state), false, 'a file that failed is not still going')
  assert.deepEqual(stored(state), [], 'and it is not on the message')
  // Nothing more happens to it: a progress event arriving after the refusal
  // does not put it back into a sending state.
  markProgress(state, item, 500, 999)
  assert.equal(item.progress, 0)
})

test('an upload the host answers without a path is a failure and says so', () => {
  const state = createAttachments()
  const item = addFile(state, file('a.txt', 12))
  markStored(state, item, { attachments: [] })
  assert.equal(item.path, null)
  assert.match(item.error, /did not say where/)
})

test('a chip comes off, and every chip goes when the chat closes', () => {
  const state = createAttachments()
  const one = addFile(state, file('one.txt', 1))
  const two = addFile(state, file('two.txt', 2))

  removeAttachment(state, one.id)
  assert.deepEqual(state.items.map((i) => i.name), ['two.txt'])

  clearAttachments(state)
  assert.deepEqual(state.items, [])
  assert.equal(uploading(state), false)
  assert.notEqual(one.id, two.id, 'two files picked at once are two chips')
})

test('an image is known from its type, which is what the chip draws', () => {
  const state = createAttachments()
  assert.equal(addFile(state, file('IMG_2041.jpg', 2, 'image/jpeg')).kind, 'image')
  assert.equal(addFile(state, file('notes.md', 2, 'text/markdown')).kind, 'file')
  // A chooser that reports no type at all is a file, not a guess from the name.
  assert.equal(addFile(state, file('IMG_2041.jpg', 2, '')).kind, 'file')
})

test('the record’s attachments are the objects the host states, and nothing else is drawn', () => {
  assert.deepEqual(attachmentList([{ path: '/store/2026-09-21/b.png', name: 'photo.png', size: 120, type: 'image/png' }]), [
    { path: '/store/2026-09-21/b.png', name: 'photo.png', size: 120, type: 'image/png' },
  ])
  // A record that names a path and nothing else is still a chip: the name is
  // the last part of the path, which is the only part worth showing.
  assert.deepEqual(attachmentList([{ path: '/store/2026-09-21/a.csv' }]), [
    { path: '/store/2026-09-21/a.csv', name: 'a.csv', size: null, type: null },
  ])
  // Nothing is a list of none, and nonsense is dropped rather than drawn as a
  // nameless chip. A bare string is not this shape and is not read as one — the
  // paths a message is *sent* with go the other way on the wire.
  assert.deepEqual(attachmentList(null), [])
  assert.deepEqual(attachmentList('/store/2026-09-21/a.csv'), [])
  assert.deepEqual(attachmentList([null, '', 42, {}, { name: 'no path.txt' }]), [])
  assert.equal(isImage('image/heic'), true)
  assert.equal(isImage('application/pdf'), false)
  assert.equal(isImage(null), false)
})

test('a chip is named after the last part of the host’s path and nothing else of it', () => {
  assert.equal(fileName('/workspaces/scout/.loam/attachments/bank-export-aug.csv'), 'bank-export-aug.csv')
  assert.equal(fileName('plain.txt'), 'plain.txt')
  assert.equal(bytes(512), '512 B')
  assert.equal(bytes(31_744), '31 KB')
  assert.equal(bytes(2_411_724), '2.3 MB')
  assert.equal(bytes(null), '')
})

// ------------------------------------------- what a clipboard and a drag carry
//
// The `DataTransfer` a browser hands a paste and a drop is read in one place,
// so the two ways in cannot disagree about what a file is. What is here is that
// reader: the objects are the shapes a browser makes, written out.

const NOW = Date.parse('2026-09-22T19:06:39Z')

test('a drag is only this app’s when it is carrying files', () => {
  assert.equal(carriesFiles({ types: ['Files'] }), true)
  // A browser that names no types still says what its items are.
  assert.equal(carriesFiles({ items: [{ kind: 'file', type: 'image/png' }] }), true)
  // Text, a link, a selection dragged out of the page: not a file, and the
  // page leaves every one of them to the browser.
  assert.equal(carriesFiles({ types: ['text/plain', 'text/html'], items: [{ kind: 'string' }] }), false)
  assert.equal(carriesFiles(null), false)
})

test('a drop is every file on it, under the names the disk gave them', () => {
  const dropped = filesFrom({
    files: [file('one.md', 12, 'text/markdown'), file('shot.png', 4096, 'image/png')],
  })
  assert.deepEqual(dropped.map((entry) => entry.name), ['one.md', 'shot.png'])
  assert.equal(dropped[0].file.size, 12, 'the bytes did not come with the name')
  assert.deepEqual(filesFrom(null), [])
})

test('a paste takes the images on the clipboard and leaves everything else', () => {
  const shot = { name: '', size: 900, type: 'image/png' }
  const csv = { name: 'books.csv', size: 90, type: 'text/csv' }
  const clipboard = {
    items: [
      { kind: 'string', type: 'text/plain' },
      { kind: 'file', type: 'image/png', getAsFile: () => shot },
      { kind: 'file', type: 'text/csv', getAsFile: () => csv },
    ],
  }
  const taken = imagesFrom(clipboard, { now: NOW })
  assert.deepEqual(taken.map((entry) => entry.name), ['pasted-20260922T190639Z.png'])
  assert.equal(taken[0].file, shot)

  // A name on the clipboard is the name it keeps: an image file copied in a
  // file manager arrives with one, and renaming it would be this app telling a
  // person their file is called something else.
  const named = imagesFrom({ files: [{ name: 'diagram.png', size: 10, type: 'image/png' }] }, { now: NOW })
  assert.deepEqual(named.map((entry) => entry.name), ['diagram.png'])

  // The extension is the type's own, and the fallback is a PNG rather than
  // nothing — a file with no extension is one a host has to guess at.
  assert.equal(pastedName('image/jpeg', NOW), 'pasted-20260922T190639Z.jpg')
  assert.equal(pastedName('image/svg+xml', NOW), 'pasted-20260922T190639Z.svg')
  assert.equal(pastedName(null, NOW), 'pasted-20260922T190639Z.png')
})

test('a pasted image goes up under the name it was given, not the one it has not got', () => {
  const state = createAttachments()
  const item = addFile(state, { name: '', size: 900, type: 'image/png' }, { name: 'pasted-20260922T190639Z.png' })
  assert.equal(item.name, 'pasted-20260922T190639Z.png')
  assert.equal(item.kind, 'image')
})
