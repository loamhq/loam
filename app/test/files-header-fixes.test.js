import test from 'node:test'
import assert from 'node:assert/strict'
import { filesSettings, fileShortcuts, toggleFileShortcut } from '../public/store.js'
import { chatHeader, settingsRow } from '../public/chat-ui.js'
import { installDom } from './fakedom.js'
import { all, find, text } from './browser.js'
import { rootsAnswer } from './fixtures/files-contract.js'

test('removing either kind of shortcut survives storage and a renewed host answer; adding it again restores one row', () => {
  const offered = [
    { label: 'System notes', resource: { root: 'vault', path: '00_System', name: '00_System', kind: 'folder' } },
    { label: 'App', resource: { root: 'vault', path: 'Projects/App', name: 'App', kind: 'folder' } },
  ]
  let held = filesSettings({ shortcuts: [{ root: 'vault', path: 'plan.md', kind: 'file' }] })
  for (const target of [{ root: 'vault', path: 'plan.md', kind: 'file' }, offered[0].resource]) {
    held = toggleFileShortcut(held, offered, target.root, target.path, target.kind)
    held = filesSettings(JSON.parse(JSON.stringify(held)))
    assert.equal(fileShortcuts(held, offered).some(s => s.path === target.path), false)
    held = toggleFileShortcut(held, offered, target.root, target.path, target.kind)
    assert.equal(fileShortcuts(held, offered).filter(s => s.path === target.path).length, 1)
    assert.equal(held.hiddenShortcuts.length, 0)
  }
  // A previously personal pin of a suggested folder must not reveal a second
  // copy from the host when it is removed.
  held = toggleFileShortcut(held, offered, offered[0].resource.root, offered[0].resource.path)
  assert.equal(fileShortcuts(held, offered).some(s => s.path === offered[0].resource.path), false)
  assert.equal(fileShortcuts(held, offered).some(s => s.label === 'App'), true)
})

test('old Files preferences keep their shortcuts and ignore malformed dismissals', () => {
  const held = filesSettings({ shortcuts: [{ root: 'vault', path: 'Notes' }], hiddenShortcuts: [null, {}, { root: 'vault', path: 4 }] })
  assert.deepEqual(held.shortcuts, [{ root: 'vault', path: 'Notes' }])
  assert.deepEqual(held.hiddenShortcuts, [])
})

installDom()
test('the shared header omits absent subtitles and keeps supplied subtitles in one text block at both widths', () => {
  for (const wide of [false, true]) for (const subtitle of [null, '', 'Ready for your next thought']) {
    const view = chatHeader({ wide, title: 'A quieter week', subtitle })
    assert.equal(all(view, 'headtitle-who').length, subtitle ? 1 : 0)
    assert.equal(text(find(view, 'headtitle-text')), `A quieter week${subtitle || ''}`)
  }
  assert.equal(find(settingsRow({ title: 'Activity' }), 'setrow-sub'), null)
})
