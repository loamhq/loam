import test from 'node:test'
import assert from 'node:assert/strict'
import { createFiles, folderView, formatFolderPath, shortFolderPath } from '../public/files.js'
import { installDom } from './fakedom.js'
import { all, find, text } from './browser.js'

const click = (node) => node.fire('click')

installDom()

function makeController({ roots, recentByKey, entries = {}, onCreate } = {}) {
  let redraws = 0
  const calls = []
  const api = async (method, path, body) => {
    calls.push({ method, path, body })
    if (method === 'GET' && path === '/files/roots') return roots
    if (method === 'GET' && path.startsWith('/files/recent')) {
      const qs = new URLSearchParams(path.split('?')[1] || '')
      const key = `${qs.get('who') || 'all'}:${qs.get('window') || 'default'}`
      return recentByKey[key] ?? { who: qs.get('who') || 'all', window: qs.get('window') || 'default', days: [], has_more: false, used_fallback: false, total_shown: 0, total_week: 0 }
    }
    if (method === 'GET' && path.startsWith('/files/list')) {
      const qs = new URLSearchParams(path.split('?')[1] || '')
      const k = `${qs.get('root')}:${qs.get('path') || ''}`
      return entries[k] ?? { root: qs.get('root'), path: qs.get('path') || '', entries: [], next_cursor: null }
    }
    if (method === 'POST' && path.startsWith('/files/create')) return onCreate?.(path, body) ?? { root: 'vault', path: 'Inbox/Note.md', name: 'Note.md', kind: 'file' }
    throw new Error(`unexpected ${method} ${path}`)
  }
  const f = createFiles({ api, schedule: () => { redraws++ } })
  return { f, calls, redraws: () => redraws }
}

const sampleRoots = {
  roots: [
    { root: 'vault', name: 'Your files', capabilities: { read: true, write: true, create: true, rename: true, delete: true, upload: true } },
    { root: 'spaces', name: 'Agent files', capabilities: { read: true } },
  ],
  shortcuts: [],
  loam_files: [
    { key: 'conversations', path: '15_CONVERSATIONS', name: '15_CONVERSATIONS', description: 'Transcripts of your chats with agents.', resource: { root: 'vault', path: '15_CONVERSATIONS', name: '15_CONVERSATIONS', kind: 'folder' } },
    { key: 'attachments', path: '98_ATTACHMENTS', name: '98_ATTACHMENTS', description: 'Files and images you’ve shared in chat.', resource: { root: 'vault', path: '98_ATTACHMENTS', name: '98_ATTACHMENTS', kind: 'folder' } },
    { key: 'boards', path: 'Boards', name: 'Boards', description: 'Shared scratchpads attached to chats.', resource: { root: 'vault', path: 'Boards', name: 'Boards', kind: 'folder' } },
    { key: 'system', path: '99_SYSTEM', name: '99_SYSTEM', description: 'Guides, skills, and settings Loam maintains.', resource: { root: 'vault', path: '99_SYSTEM', name: '99_SYSTEM', kind: 'folder' } },
  ],
}

const fileItem = (path, name, author, folder = 'Notes', day = 'Today', day_key = '2026-11-22') => ({
  kind: 'file', root: 'vault', path, name, folder, folder_name: folder,
  author, day, day_key, modified: '2026-11-22T20:00:00Z', mtime: 1795377600, size: 120,
  row: { root: 'vault', path, name, kind: 'file', modified: '2026-11-22T20:00:00Z', size: 120, capabilities: { read: true, write: true } },
})

test('Files home renders in order: header, quick actions, Places, Pinned (empty + capped 4), Recent (filters, groups, Show more)', async () => {
  const recentAllDefault = {
    who: 'all', window: 'default', used_fallback: false, has_more: true, total_shown: 2, total_week: 4,
    days: [{
      day: 'Today', day_key: '2026-11-22',
      items: [
        {
          kind: 'group', id: '2026-11-22:agent:ada:Projects/Loam',
          author: { kind: 'agent', id: 'ada', name: 'Ada' },
          folder: 'Projects/Loam', folder_name: 'Loam', label: '3 files in Loam', count: 3,
          day: 'Today', day_key: '2026-11-22', modified: '2026-11-22T20:10:00Z', mtime: 1795378200,
          items: [
            fileItem('Projects/Loam/a.md', 'a.md', { kind: 'agent', id: 'ada', name: 'Ada' }, 'Projects/Loam'),
            fileItem('Projects/Loam/b.md', 'b.md', { kind: 'agent', id: 'ada', name: 'Ada' }, 'Projects/Loam'),
            fileItem('Projects/Loam/c.md', 'c.md', { kind: 'agent', id: 'ada', name: 'Ada' }, 'Projects/Loam'),
          ],
        },
        fileItem('Notes/ideas.md', 'ideas.md', { kind: 'owner', id: 'sam', name: 'Sam' }, 'Notes'),
      ],
    }],
  }
  const recentAllWeek = {
    ...recentAllDefault, window: 'week', has_more: false, total_shown: 3,
    days: [
      ...recentAllDefault.days,
      { day: 'Friday', day_key: '2026-11-20', items: [fileItem('Notes/older.md', 'older.md', { kind: 'owner', id: 'sam', name: 'Sam' }, 'Notes', 'Friday', '2026-11-20')] },
    ],
  }
  const recentAgents = { who: 'agents', window: 'default', used_fallback: false, has_more: false, total_shown: 0, total_week: 0, days: [] }

  const { f } = makeController({
    roots: sampleRoots,
    recentByKey: {
      'all:default': recentAllDefault,
      'all:week': recentAllWeek,
      'agents:default': recentAgents,
    },
  })
  await f.loadRoots()
  await f.loadRecent('all', 'default')

  const opened = []
  const go = (root, path, opts) => opened.push({ root, path, opts })
  const makeHome = (shortcuts = []) => ({
    showNewNote: true,
    showDailyNote: true,
    onInbox: () => opened.push({ quick: 'new' }),
    onDaily: () => opened.push({ quick: 'today' }),
    shortcuts,
    ownerName: 'Sam',
    agentLook: (id) => ({ agentId: id, initial: id[0].toUpperCase(), color: '#8b5e3c', ink: '#fff', shape: '50%' }),
  })

  // Empty Pinned state
  const view = folderView(f, { go, wide: false, home: makeHome([]) })
  const sections = find(view, 'fs-scroll').childNodes.map((n) => n.className || '')
  const pos = (cls) => sections.findIndex((c) => c.split(/\s+/).includes(cls))
  assert.ok(pos('fs-actions-home') >= 0 && pos('fs-places') >= 0 && pos('fs-pinned') >= 0 && pos('fs-recent') >= 0, sections.join(' | '))
  assert.ok(pos('fs-actions-home') < pos('fs-places'))
  assert.ok(pos('fs-places') < pos('fs-pinned'))
  assert.ok(pos('fs-pinned') < pos('fs-recent'))

  // Quick actions use plain labels with no folder codes
  const qLabels = all(view, 'fs-actionlabel').map(text)
  assert.deepEqual(qLabels, ['New note', 'Today’s note'])
  assert.doesNotMatch(text(find(view, 'fs-actions-home')), /00_INBOX|10_DAILY/)

  // Pinned empty state explains ⋯ -> Pin
  assert.match(text(find(view, 'fs-pinned-empty')), /Pin/)
  assert.match(text(find(view, 'fs-pinned-empty')), /⋯/)

  // Recent shows grouped row + owner row + Show more
  assert.equal(all(view, 'fs-recent-day').map(text)[0], 'Today')
  assert.match(text(find(view, 'fs-recent-group')), /3 files in Loam/)
  assert.ok(find(view, 'fs-recent-av'), 'renders real agent avatar')
  assert.ok(find(view, 'fs-av-you'), 'renders owner avatar')
  assert.equal(all(view, 'fs-recent-kid').length, 0, 'group starts collapsed')

  // Expand group in place
  click(find(view, 'fs-recent-grouprow'))
  const expanded = folderView(f, { go, wide: false, home: makeHome([]) })
  assert.equal(all(expanded, 'fs-recent-kid').length, 3, 'expanding reveals 3 child rows in place')
  click(all(expanded, 'fs-recent-kid')[1])
  assert.equal(opened.at(-1).path, 'Projects/Loam/b.md')

  // Show more extends to past week
  click(find(expanded, 'fs-recent-more'))
  await new Promise((r) => setTimeout(r, 0))
  const weekView = folderView(f, { go, wide: false, home: makeHome([]) })
  assert.deepEqual(all(weekView, 'fs-recent-day').map(text), ['Today', 'Friday'])
  assert.equal(find(weekView, 'fs-recent-more'), null)

  // Segmented filter switches to Agents and shows empty state
  const segBtns = find(weekView, 'fs-filter').childNodes
  assert.deepEqual(segBtns.map(text), ['Everyone', 'You', 'Agents'])
  click(segBtns[2])
  await new Promise((r) => setTimeout(r, 0))
  const agentsView = folderView(f, { go, wide: false, home: makeHome([]) })
  assert.match(text(find(agentsView, 'fs-recent-empty')), /Nothing from your agents yet/)

  // Pinned with 6 items shows first 4 + "Show all 6", then expands to 6 + "Show fewer"
  const sixPins = Array.from({ length: 6 }, (_, i) => ({
    root: 'vault', path: `Notes/p${i}.md`, label: `p${i}.md`, kind: 'file',
  }))
  const pinnedCapped = folderView(f, { go, wide: false, home: makeHome(sixPins) })
  assert.equal(all(pinnedCapped, 'fs-shortcut').length, 4)
  assert.equal(text(find(pinnedCapped, 'fs-pins-more')), 'Show all 6')
  click(find(pinnedCapped, 'fs-pins-more'))
  const pinnedAll = folderView(f, { go, wide: false, home: makeHome(sixPins) })
  assert.equal(all(pinnedAll, 'fs-shortcut').length, 6)
  assert.equal(text(find(pinnedAll, 'fs-pins-more')), 'Show fewer')
})

test('Your files hides Loam files behind one quiet row; Loam files lists machinery folders with descriptions', async () => {
  const { f } = makeController({
    roots: sampleRoots,
    recentByKey: {},
    entries: {
      'vault:': {
        root: 'vault', path: '', next_cursor: null,
        entries: [
          { root: 'vault', path: '15_CONVERSATIONS', name: '15_CONVERSATIONS', kind: 'folder' },
          { root: 'vault', path: '98_ATTACHMENTS', name: '98_ATTACHMENTS', kind: 'folder' },
          { root: 'vault', path: '99_SYSTEM', name: '99_SYSTEM', kind: 'folder' },
          { root: 'vault', path: 'Boards', name: 'Boards', kind: 'folder' },
          { root: 'vault', path: 'Projects', name: 'Projects', kind: 'folder' },
          { root: 'vault', path: 'About me.md', name: 'About me.md', kind: 'file', size: 80 },
        ],
      },
    },
  })
  await f.loadRoots()
  await f.show('vault', '')
  const opened = []
  const go = (root, path, opts) => opened.push({ root, path, opts })
  const vaultView = folderView(f, { go, wide: false })

  const visibleNames = all(find(vaultView, 'card-group'), 'fs-name').map(text)
  assert.deepEqual(visibleNames, ['Projects', 'About me.md'])
  const loamFoot = find(vaultView, 'fs-loam-foot')
  const loamBtn = find(vaultView, 'fs-loam-entry')
  assert.ok(loamFoot && loamBtn, 'quiet Loam files row sits in bottom-pinned footer wrapper of Your files')
  assert.match(text(loamBtn), /Loam files/)
  assert.match(text(loamBtn), /What Loam needs to run/)

  click(loamBtn)
  assert.deepEqual(opened.at(-1), { root: 'loam', path: '', opts: { isFile: false } })

  // Inside /files/loam
  await f.show('loam', '')
  const loamView = folderView(f, { go, wide: false })
  assert.equal(text(find(loamView, 'fs-title')), 'Loam files')
  const loamRows = all(loamView, 'fs-loam-row')
  assert.equal(loamRows.length, 4)
  assert.match(text(loamRows[0]), /15_CONVERSATIONS.*Transcripts of your chats/)
  assert.match(text(loamRows[2]), /Boards.*Shared scratchpads/)
  click(loamRows[2])
  assert.equal(opened.at(-1).root, 'vault')
  assert.equal(opened.at(-1).path, 'Boards')
  assert.equal(opened.at(-1).opts?.isFile, false)
})

test('shortFolderPath shortens deep paths to the last two folders and Recent sets full path title', async () => {
  assert.equal(shortFolderPath(''), 'Your files')
  assert.equal(shortFolderPath('10_DAILY'), '10_DAILY')
  assert.equal(shortFolderPath('40_PROJECTS/Loam'), '40_PROJECTS › Loam')
  assert.equal(shortFolderPath('40_PROJECTS/Loam/analysis/per-case'), 'analysis › per-case')
  assert.equal(formatFolderPath('40_PROJECTS/Loam/analysis/per-case'), '40_PROJECTS › Loam › analysis › per-case')

  const deepRecent = {
    who: 'all', window: 'default', used_fallback: false, has_more: false, total_shown: 1, total_week: 1,
    days: [{
      day: 'Today', day_key: '2026-11-22',
      items: [
        fileItem(
          '40_PROJECTS/Loam/analysis/per-case/review.md',
          'review.md',
          { kind: 'agent', id: 'ada', name: 'Ada' },
          '40_PROJECTS/Loam/analysis/per-case',
        ),
      ],
    }],
  }
  const { f } = makeController({ roots: sampleRoots, recentByKey: { 'all:default': deepRecent } })
  f.registry.scope({ id: 'doc-comments' }).places['files.root'].add({
    id: 'doc-comments', name: 'Shared Docs', desc: 'Shared by you, or with you.',
    render: () => null,
  })
  await f.loadRoots()
  await f.loadRecent('all', 'default')
  const view = folderView(f, { go: () => {}, wide: false, home: { shortcuts: [], showNewNote: false, showDailyNote: false } })

  // All 3 places (Your files, Agent files, Shared Docs) render as equal standalone cards
  const placeNames = all(find(view, 'fs-places'), 'fs-rootname').map(text)
  assert.deepEqual(placeNames, ['Your files', 'Agent files', 'Shared Docs'])

  // Recent row shows shortened 2-folder path with full path in title attribute
  const meta = find(find(view, 'fs-recent-row'), 'fs-meta')
  assert.equal(text(meta), 'analysis › per-case · Ada')
  assert.equal(meta.getAttribute('title'), '40_PROJECTS › Loam › analysis › per-case')
})
