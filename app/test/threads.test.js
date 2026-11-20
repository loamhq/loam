import { setOwner } from '../public/owner.js'
setOwner({ id: 'you', name: 'Sam', accepts: ['you', 'owner', 'person'] })
import test from 'node:test'
import { readFile } from 'node:fs/promises'
import assert from 'node:assert/strict'
import { boardLines, boardBlocks, reflowBoardQuotes, editBoard, markBoardSeen, addBoardItem, applyThreadEvent, readThreadRecord, readThreadSummary, readThreadList, readThreadBoard, readAcceptedMessage, compareThreads, recentMembers, messageSegments, firstMessageTitle, groupJoinNotices, messageRecipients } from '../public/thread-record.js'
import { fakeBroker, appAgainst, closing, TOKEN } from './harness.js'

const board = '# Notes\r\n\r\n- [ ] First <!-- Scout · new · until 2026-11-22 -->\r\n* [X] Second <!-- Quill · new · future field -->\r\n- A plain note\r\n\r\n```md\r\n- [ ] A fenced example <!-- Scout · new -->\r\n```\r\n'
test('an incomplete or wrong thread reading cannot turn into a writable empty board', () => {
  const record = { thread: { id: 'one', members: [] }, messages: [], has_more: false, turn: null, board: { path: 'boards/one.md', markdown: '' } }
  assert.equal(readThreadRecord(record, 'one'), record)
  assert.throws(() => readThreadRecord({ thread: record.thread, messages: [] }, 'one'), /could not be loaded/)
  assert.throws(() => readThreadRecord(record, 'another'), /could not be loaded/)
})
test('tick preserves every byte except the checkbox, including EOL metadata and fenced examples', () => {
  assert.equal(editBoard(board, 2, 'tick'), board.replace('- [ ] First', '- [x] First'))
  assert.equal(editBoard(board, 7, 'tick'), board)
  assert.equal(editBoard(board, 4, 'tick'), board)
  assert.equal(boardLines(board).filter((l) => l.item).length, 3)
  assert.equal(boardBlocks(board).at(-1).text, '\r\n```md\r\n- [ ] A fenced example <!-- Scout · new -->\r\n```\r\n')
})
test('seen edits only new metadata, never removes or checks an item; remove is separate', () => {
  const seen = markBoardSeen(board)
  assert.equal(seen, board.replace('Scout · new · until', 'Scout · until').replace('Quill · new · future', 'Quill · future'))
  assert.equal(boardLines(seen).filter((l) => l.item).length, 3)
  assert.equal(editBoard(board, 2, 'remove'), board.replace('- [ ] First <!-- Scout · new · until 2026-11-22 -->\r\n', ''))
  assert.equal(markBoardSeen(seen), seen)
  const inline = '- [ ] An <!-- inline --> example <!-- Scout · new · until 2026-11-22 -->\n'
  assert.equal(editBoard(inline, 0, 'seen'), inline.replace(' · new', ''))
})
test('quick add uses UTC durations and keeps prior markdown and CRLF intact', () => {
  assert.equal(addBoardItem(board, ' Walk\n outside ', 7, new Date('2026-11-08T23:59:00Z')), board + '- [ ] 2026-11-08 · Walk  outside <!-- Sam · until 2026-11-15 -->\r\n')
  assert.equal(addBoardItem('', 'Keep this'), `- [ ] ${new Date().toISOString().slice(0, 10)} · Keep this <!-- Sam -->\n`)
  assert.equal(addBoardItem(board, '  '), board)
  assert.equal(addBoardItem('A note', 'A task', 1, new Date('2026-12-31T23:59:00Z')), 'A note\n- [ ] 2026-12-31 · A task <!-- Sam · until 2027-01-01 -->\n')
})
function open() { return { thread: { members: [] }, messages: [], seq: 0, typing: new Set(), permissions: new Map() } }
test('runtime drafts replace text, reset at tool steps and reconnect only from durable messages', () => {
  const o = open()
  applyThreadEvent(o, { type: 'thread.snapshot', thread: 'one', turn: { persona: 'scout', engine: 'codex' } })
  assert.equal(o.typing.has('scout'), true)
  const delta = { type: 'message.delta', persona: 'scout', text: 'Hello ', draft: 'Hello ' }
  applyThreadEvent(o, delta); applyThreadEvent(o, delta)
  applyThreadEvent(o, { ...delta, text: 'there', draft: 'Hello there' })
  assert.equal(o.messages[0].text, 'Hello there')
  assert.equal(o.after, undefined)
  applyThreadEvent(o, { type: 'turn.step', persona: 'scout', label: 'Read: notes.md', steps: 2 })
  assert.equal(o.messages.length, 1)
  assert.deepEqual(o.messages[0].segments, [{ text: 'Hello there' }, { did: [{ label: 'Read: notes.md' }] }])
  assert.equal(o.messages[0].text, '')
  assert.equal(o.steps.get('scout').label, 'Read: notes.md')
  applyThreadEvent(o, { ...delta, text: 'Done', draft: 'Done' })
  const done = { type: 'message.done', message: { id: 'm20', author: 'scout', text: 'Done', did: [{ label: 'Read the note' }] } }
  applyThreadEvent(o, done); applyThreadEvent(o, done)
  applyThreadEvent(o, { type: 'message.done', message: { id: 'm2', author: 'system', kind: 'system', text: 'Earlier' } })
  assert.equal(o.messages.length, 2)
  assert.equal(o.after, 'm20')
  assert.equal(o.typing.size, 1, 'a reply can finish while its thread turn is still open')
  applyThreadEvent(o, { type: 'turn.finished', persona: 'scout' })
  assert.equal(o.typing.size, 0)
  applyThreadEvent(o, { type: 'a.future.event' })
  applyThreadEvent(o, { type: 'thread.snapshot', turn: null })
  assert.equal(o.after, 'm20')
})
test('members use durable system lines and classic permissions normalize by request identity', () => {
  const o = open()
  const joined = { type: 'member.joined', persona: 'scout', engine: 'codex', by: 'you' }
  applyThreadEvent(o, joined); applyThreadEvent(o, joined)
  assert.equal(o.thread.members.length, 1)
  assert.equal(o.messages.length, 0, 'the host follows member.joined with a durable system message')
  const p = { type: 'broker.permission_request', request_id: 'r', session_id: 's', persona: 'scout', input: {}, permission_suggestions: [{ destination: 'session' }, { destination: 'localSettings' }] }
  applyThreadEvent(o, p); applyThreadEvent(o, p)
  assert.equal(o.permissions.size, 1)
  assert.equal(o.permissions.get('r').session_id, 's')
  assert.deepEqual(o.permissions.get('r').suggestions, [{ destination: 'session' }])
  applyThreadEvent(o, { type: 'broker.permission_resolved', request_id: 'r' })
  assert.equal(o.permissions.size, 0)
  applyThreadEvent(o, p)
  assert.equal(o.permissions.size, 0, 'a repeated closed request cannot reopen its card')
  o.after = 'm100'
  applyThreadEvent(o, { ...p, request_id: 'r2', seq: 2 })
  assert.equal(o.permissions.size, 1)
  assert.equal(o.after, 'm100', 'harness seq cannot become the thread cursor')
})
test('General Stays is dated for the comms sweep, timed adds carry until; ordinary boards retain task checkboxes', () => {
  const date = new Date('2026-11-08T23:59:00Z'), path = '00_INBOX/from-agents/comms.md'
  assert.equal(addBoardItem('', 'Keep close', 0, date, path), '- 2026-11-08 · Keep close <!-- Sam -->\n')
  assert.equal(addBoardItem('', 'Read soon', 7, date, path), '- [ ] 2026-11-08 · Read soon <!-- Sam · until 2026-11-15 -->\n')
  assert.equal(addBoardItem('', 'Keep close', 0, date, 'boards/one.md'), '- [ ] 2026-11-08 · Keep close <!-- Sam -->\n')
})
test('recorded runtime responses parse for list, create, detail, history, patch, send, both board methods and SSE', async () => {
  const recorded = JSON.parse(await readFile(new URL('./fixtures/threads/runtime-34b5668.json', import.meta.url)))
  const r = recorded.responses
  assert.equal(recorded.runtime_commit, '34b5668')
  assert.equal(Object.keys(r).length, 9)
  assert.equal(readThreadList(r.list.body), r.list.body)
  for (const row of [r.create.body, r.patch.body, ...r.list.body.threads]) {
    assert.equal(readThreadSummary(row), row); assert.equal(typeof row.id, 'string'); assert.ok(Array.isArray(row.members)); assert.equal(typeof row.board, 'string'); assert.ok(row.created)
  }
  for (const key of ['detail', 'history']) {
    const body = readThreadRecord(r[key].body, r.create.body.id)
    assert.equal(typeof body.has_more, 'boolean'); assert.ok(Object.hasOwn(body, 'turn'))
  }
  for (const key of ['board_get', 'board_put']) {
    assert.equal(readThreadBoard(r[key].body), r[key].body); assert.equal(typeof r[key].body.path, 'string'); assert.equal(typeof r[key].body.markdown, 'string'); assert.equal(r[key].body.board_new, 1)
    assert.equal(boardLines(r[key].body.markdown).filter((l) => l.unseen).length, 1)
  }
  const o = open(); o.thread = r.create.body
  assert.equal(r.message.body.accepted, true)
  applyThreadEvent(o, { type: 'message.done', message: readAcceptedMessage(r.message.body) })
  for (const event of r.events.events) applyThreadEvent(o, event)
  assert.equal(o.messages.filter((m) => m.id === r.message.body.message.id).length, 1)
  assert.equal(r.events.events[0].type, 'thread.snapshot')
  const live = open(); live.thread = r.create.body
  for (const event of recorded.engine_events) applyThreadEvent(live, event)
  assert.equal(live.messages.length, 1)
  assert.equal(live.messages[0].text, 'I gathered the open questions.')
  assert.ok(live.messages[0].did.some((s) => s.label.startsWith('Said: ')))
  assert.ok(live.messages[0].did.some((s) => s.label.startsWith('↳ ')))
  assert.equal(live.typing.size, 1, 'this recorded excerpt ends at message.done, before turn.finished')
  applyThreadEvent(live, { type: 'thread.snapshot', turn: null })
  assert.equal(live.typing.size, 0)
  assert.equal(r.history.body.has_more, false); assert.equal(r.detail.body.has_more, true)
})
test('all agreed thread operations reach runtime /v1/threads with bearer; statuses and markdown survive', async (t) => {
  const seenBody = { markdown: board }
  const routes = {}
  for (const [method, suffix, status, body] of [
    ['GET', '', 200, { threads: [] }], ['POST', '', 201, { id: 'a b' }],
    ['GET', '/a b', 200, { thread: {}, messages: [], board: seenBody }],
    ['PATCH', '/a b', 200, { id: 'a b' }], ['POST', '/a b/messages', 429, { error: 'This engine is busy. Keep your message and try again.' }],
    ['PUT', '/a b/board', 200, seenBody], ['GET', '/a b/board', 200, seenBody],
  ]) routes[`${method} /v1/threads${suffix}`] = (req, res) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
  routes['GET /v1/threads/a b/events'] = (req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.end('data: {"type":"turn.started","persona":"scout"}\n\n') }
  const broker = await fakeBroker(routes), app = await appAgainst(broker)
  t.after(closing(app, broker))
  for (const [method, suffix, body, status] of [
    ['GET', '', undefined, 200], ['POST', '', { persona: 'scout' }, 201], ['GET', '/a%20b?before=m&limit=30&seen=0&ignored=x', undefined, 200],
    ['PATCH', '/a%20b', { engine: { scout: 'codex' } }, 200], ['POST', '/a%20b/messages', { text: '@scout A thought' }, 429], ['PUT', '/a%20b/board', seenBody, 200], ['GET', '/a%20b/board', undefined, 200],
  ]) {
    const res = await fetch(`${app.url}/api/threads${suffix}`, { method, ...(body ? { headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) } : {}) })
    assert.equal(res.status, status)
    assert.equal(res.headers.get('cache-control'), 'no-store')
    if (method === 'PUT') assert.deepEqual(await res.json(), seenBody)
  }
  const stream = await fetch(`${app.url}/api/threads/a%20b/events?after=m7&ignored=x`)
  assert.equal(stream.headers.get('x-accel-buffering'), 'no')
  assert.match(await stream.text(), /"type":"turn.started"/)
  assert.ok(broker.seen.every((r) => r.auth === `Bearer ${TOKEN}`))
  assert.equal(broker.seen.find((r) => r.method === 'PUT').body, JSON.stringify(seenBody))
  assert.equal(broker.seen.find((r) => r.path.endsWith('/events')).search, '?after=m7')
  assert.equal(broker.seen.find((r) => r.search.includes('before')).search, '?before=m&limit=30&seen=0')
  assert.equal((await fetch(`${app.url}/api/threads/a/board`, { method: 'DELETE' })).status, 405)
  assert.equal((await fetch(`${app.url}/api/threads/a/unknown`)).status, 404)
  assert.equal((await fetch(`${app.url}/api/threads`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{bad json' })).status, 400)
  assert.equal((await fetch(`${app.url}/api/threads`, { method: 'POST', headers: { 'sec-fetch-site': 'cross-site' }, body: '{}' })).status, 403)
  assert.equal((await fetch(`${app.url}/up`)).status, 200)
})

test('avatar stack leads with the default, then actual recent speakers, with at most three', () => {
  const thread = { default: 'sprout', members: [
    { persona: 'ledger', last_spoke_at: null }, { persona: 'quill', last_spoke_at: '2026-11-09T10:00:00Z' },
    { persona: 'sprout', last_spoke_at: null }, { persona: 'pip', last_spoke_at: '2026-11-09T11:00:00Z' },
    { persona: 'scout', last_spoke_at: '2026-11-09T09:00:00Z' },
  ] }
  assert.deepEqual(recentMembers(thread).map((m) => m.persona), ['sprout', 'pip', 'quill'])
  assert.deepEqual(recentMembers({ ...thread, default: null, members: [{ persona: 'ledger' }, { persona: 'pip' }, { persona: 'quill' }] }, [{ author: 'quill', at: '2026-11-09T10:00:00Z' }]).map((m) => m.persona), ['quill', 'ledger', 'pip'])
  assert.deepEqual(recentMembers({ default: null, members: [{ persona: 'quill', last_spoke_at: null }] }).map((m) => m.persona), ['quill'])
  assert.equal(recentMembers({ ...thread, default: null }, [{ author: 'ledger', streaming: true, id: 'draft:ledger' }])[0].persona, 'ledger')
})
test('first-message titles omit leading mentions without changing the message or internal mentions', () => {
  assert.equal(firstMessageTitle('  @quill @pip  A quieter week with @scout  '), 'A quieter week with @scout')
  assert.equal(firstMessageTitle('@quill'), 'New thread')
  assert.equal(firstMessageTitle('@quill '+ 'a'.repeat(90)).length, 60)
})
test('speech, work, speech stays ordered through live deltas and a durable replay', () => {
  const o = open()
  applyThreadEvent(o, { type: 'message.delta', persona: 'scout', draft: 'First I will read the note.' })
  applyThreadEvent(o, { type: 'turn.step', persona: 'scout', label: 'Read the note', steps: 2 })
  assert.deepEqual(messageSegments(o.messages[0]), [{ text: 'First I will read the note.' }, { did: [{ label: 'Read the note' }] }])
  applyThreadEvent(o, { type: 'message.delta', persona: 'scout', draft: 'Here is the answer.' })
  assert.equal(o.steps.has('scout'), false)
  const message = { id: 'm9', author: 'scout', text: 'Here is the answer.', did: [{ label: 'Said: First I will read the note.' }, { label: 'Read the note' }] }
  applyThreadEvent(o, { type: 'message.done', message })
  assert.deepEqual(messageSegments(o.messages[0]), messageSegments(message))
  assert.equal(o.progress.has('scout'), false)
})
test('a reconnect snapshot recovers pending permission cards without asking the session again', () => {
  const o = open()
  applyThreadEvent(o, { type: 'thread.snapshot', turn: { persona: 'scout' }, pending: [{ request_id: 'permission-1', tool_name: 'Read', input: {}, persona: 'scout', session_id: 'session-1' }] })
  assert.equal(o.permissions.get('permission-1').session_id, 'session-1')
  applyThreadEvent(o, { type: 'thread.snapshot', turn: null, pending: [] })
  assert.equal(o.permissions.size, 0)
})

test('thread management and uploads relay the extended contract without borrowing a session', async (t) => {
  const attachment = { path: '98_ATTACHMENTS/test/note.md', name: 'note.md', type: 'text/markdown', size: 5 }
  const routes = {
    'GET /v1/threads': (req, res, url) => { assert.equal(url.search, '?archived=1'); res.end('{}') },
    'POST /v1/threads': (req, res, url, body) => { assert.deepEqual(JSON.parse(body), {}); res.writeHead(201); res.end('{}') },
    'PATCH /v1/threads/one': (req, res, url, body) => { assert.deepEqual(JSON.parse(body), { default: null, icon: '🌱', pinned: true, archived: true, model: { scout: null } }); res.end('{}') },
    'DELETE /v1/threads/one': (req, res) => res.end('{}'),
    'POST /v1/threads/one/messages': (req, res, url, body) => { assert.deepEqual(JSON.parse(body), { text: '', attachments: [attachment.path], model: 'fast' }); res.end('{}') },
    'POST /v1/threads/one/attachments': (req, res, url, body) => {
      assert.equal(req.headers['content-type'], 'multipart/form-data; boundary=thread-upload')
      assert.equal(body, '--thread-upload\r\nContent-Disposition: form-data; name="file"; filename="note.md"\r\n\r\nhello\r\n--thread-upload--\r\n')
      res.writeHead(201, { 'content-type': 'application/json' }); res.end(JSON.stringify({ attachments: [attachment] }))
    },
  }
  const broker = await fakeBroker(routes), app = await appAgainst(broker)
  t.after(closing(app, broker))
  const request = (method, path, body) => fetch(app.url + '/api/threads' + path, { method, ...(body === undefined ? {} : { headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }) })
  assert.equal((await request('GET', '?archived=1')).status, 200)
  assert.equal((await request('POST', '', {})).status, 201)
  assert.equal((await request('PATCH', '/one', { default: null, icon: '🌱', pinned: true, archived: true, model: { scout: null } })).status, 200)
  assert.equal((await request('POST', '/one/messages', { text: '', attachments: [attachment.path], model: 'fast' })).status, 200)
  const upload = await fetch(app.url + '/api/threads/one/attachments', { method: 'POST', headers: { 'content-type': 'multipart/form-data; boundary=thread-upload' }, body: '--thread-upload\r\nContent-Disposition: form-data; name="file"; filename="note.md"\r\n\r\nhello\r\n--thread-upload--\r\n' })
  assert.equal(upload.status, 201)
  assert.deepEqual(await upload.json(), { attachments: [attachment] })
  assert.equal((await request('DELETE', '/one')).status, 200)
  assert.ok(broker.seen.every((r) => r.auth === `Bearer ${TOKEN}`))
  assert.ok(broker.seen.every((r) => !r.path.startsWith('/v1/sessions')))
})

test('board quotes retain their marker and reflow soft wraps without changing fenced examples', () => {
  const source = '# Notes\n\n> A place for things worth keeping\n> beyond this conversation.\n\n```md\n> two\n> lines\n```\n\n> ```md\n> two\n> lines\n> ```\n'
  assert.equal(reflowBoardQuotes(source), source.replace('keeping\n> beyond', 'keeping beyond'))
})


test('join notices group only the same human message, preserving durable records', () => {
  const agents = ['sprout','quill','scout','ledger','pip','brook'].map((id) => ({ id, name:id[0].toUpperCase()+id.slice(1) }))
  const first = { id:'m1', author:'person', kind:'message', text:agents.map((a)=>'@'+a.id).join(' ') }
  const joins = agents.map((a,i)=>({id:`m${i+2}`,kind:'system',author:'system',text:`Person brought in [[${a.name}]]`}))
  const second = {id:'m9',author:'person',kind:'message',text:'@sprout Again'}
  const messages = [first,...joins,second,{id:'m10',kind:'system',text:'Person brought in Sprout'}]
  const view = groupJoinNotices(messages,agents)
  assert.equal(view.length,4)
  assert.deepEqual(view[1].joins.names,agents.map((a)=>a.id))
  assert.equal(view[1].id,joins[0].id)
  assert.deepEqual(view[3].joins.names,['sprout'])
  assert.equal(messages.length,9)
  assert.equal(joins[0].joins,undefined)
})
test('joins without a visible originating mention and other system messages stay separate', () => {
  const agents=[{id:'quill',name:'Quill'}]
  const messages=[{id:'m1',kind:'system',text:'You brought in Quill'}, {id:'m2',author:'you',kind:'message',text:'Notes'}, {id:'m3',kind:'system',text:'You brought in Quill'}, {id:'m4',kind:'system',text:'The turn failed'}]
  assert.deepEqual(groupJoinNotices(messages,agents),messages)
})
test('addressed model recipients include newly mentioned agents and retain existing engines', () => {
  const agents=[{id:'quill',name:'Quill'},{id:'scout',name:'Scout'}]
  const thread={default:null,members:[{persona:'quill',engine:'codex',model:'one'}]}
  assert.deepEqual(messageRecipients(thread,'@QUILL @scout Reply',agents),[thread.members[0],{persona:'scout',engine:null,model:null}])
  assert.deepEqual(messageRecipients(thread,'No mention',agents),[])
  assert.deepEqual(messageRecipients(thread,'mail@quill @quillish',agents),[])
})

test('General leads the pinned threads by id; everything else keeps pinned-then-newest', () => {
  const row = (id, at, pinned, title = id) => ({ id, title, pinned, archived: false, last: { at }, created: at, members: [] })
  const rows = [row('plain', '2026-11-12', false), row('pin-c', '2026-11-05', true), row('plain-general', '2026-11-17', false, 'General'),
    row('newest-pin', '2026-11-18', true), row('general', '2026-11-01', true, 'General'), row('pin-b', '2026-11-10', true)]
  const ids = (list, options) => [...list].sort((a, b) => compareThreads(a, b, options)).map((t) => t.id)
  assert.deepEqual(ids(rows), ['general', 'newest-pin', 'pin-b', 'pin-c', 'plain-general', 'plain'])
  // After activity in another pinned thread, General still leads.
  const later = rows.map((t) => t.id === 'pin-c' ? { ...t, last: { at: '2026-11-19' } } : t)
  assert.deepEqual(ids(later), ['general', 'pin-c', 'newest-pin', 'pin-b', 'plain-general', 'plain'])
  // Unpinned or absent, General has no place of its own.
  const unpinned = rows.map((t) => t.id === 'general' ? { ...t, pinned: false } : t)
  assert.deepEqual(ids(unpinned), ['newest-pin', 'pin-b', 'pin-c', 'plain-general', 'plain', 'general'])
  assert.deepEqual(ids(rows.filter((t) => t.id !== 'general')), ['newest-pin', 'pin-b', 'pin-c', 'plain-general', 'plain'])
  // The archived view sorts by newest alone, as before.
  assert.deepEqual(ids(rows, { pinned: false }), ['newest-pin', 'plain-general', 'plain', 'pin-b', 'pin-c', 'general'])
})
