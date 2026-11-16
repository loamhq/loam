import test from 'node:test'
import assert from 'node:assert/strict'
import { listeningRecipients, threadGlyph, paintMention, listeningInset } from '../public/thread-identity.js'
import { nodesFor, readField } from '../public/compose.js'
import { createThreads } from '../public/threads.js'
import { applyThreadEvent } from '../public/thread-record.js'
import { mentionChoices } from '../public/thread-experience.js'
import { installDom } from './fakedom.js'
import { h } from '../public/ui.js'
import { all, find, text } from './browser.js'

const agents = ['sprout', 'quill', 'scout', 'ledger', 'pip'].map((id) => ({ id, name: id[0].toUpperCase() + id.slice(1) }))
const thread = { id: 'one', title: 'A shared thought', icon: null, default: 'sprout', members: agents.map((a) => ({ persona: a.id, engine: 'claude-code', model: null })) }
test('recipient preview follows mentions instead of the default; Everyone includes members and a missing default once', () => {
  const recipients = (draft, dictating = false, record = thread) => listeningRecipients(record, draft, dictating, agents)
  assert.deepEqual(recipients(''), [])
  assert.deepEqual(recipients('  \n'), [])
  assert.deepEqual(recipients('', true), ['sprout'])
  assert.deepEqual(recipients('A thought'), ['sprout'])
  assert.deepEqual(recipients('@quill @scout @quill'), ['quill', 'scout'])
  assert.deepEqual(recipients('@QUILL, hello'), ['quill'])
  assert.deepEqual(recipients('email@quill.test @unknown @quillish'), ['sprout'])
  assert.deepEqual(recipients('@scout @all'), ['scout', 'sprout', 'quill', 'ledger', 'pip'])
  assert.deepEqual(recipients('@everyone', false, { ...thread, members: thread.members.slice(1) }), agents.slice(1).map((a) => a.id).concat('sprout'))
  assert.deepEqual(recipients('Hi', false, { ...thread, default: null }), [])
  assert.deepEqual(recipients('@quill', false, { ...thread, default: null, members: [] }), ['quill'])
})
test('@all names everyone in this thread and includes exactly its three members', () => {
  assert.equal(mentionChoices(agents)[0].sub, 'everyone in this thread')
  assert.deepEqual(listeningRecipients({ ...thread, members: thread.members.slice(0, 3) }, '@all A thought', false, agents), ['sprout', 'quill', 'scout'])
})
function setup(t, { ignore = false } = {}) {
  installDom(); globalThis.innerWidth = 1440; globalThis.innerHeight = 1000
  let value = '', dictating = false
  const field = { get value() { return value }, set value(next) { value = next } }
  const record = structuredClone(thread), calls = []
  const threads = createThreads({ redraw() {}, agents: () => agents, rail: () => null, nav: () => null,
    api: async (method, path, body) => { calls.push({ method, path, body }); if (!ignore) Object.assign(record, body); return structuredClone(record) },
    compose: { field: () => field, render: () => h('div'), dictating: () => dictating, files: () => [], dragProps: () => ({}) } })
  threads.st.threads = [record]
  threads.st.open = { thread: structuredClone(record), messages: [], permissions: new Map(), typing: new Set(), turns: [], board: { markdown: '' } }
  const view = () => threads.view(true)
  t.after(() => threads.pause())
  return { threads, record, calls, view, field, dictate: (next) => { dictating = next } }
}
test('header has a thread glyph and no persona; tasks are the only live header count and are grouped', (t) => {
  const f = setup(t), o = f.threads.st.open
  let head = find(f.view(), 'tt-head')
  assert.equal(all(head, 'blob').length, 0)
  assert.equal(all(head, 'tt-avatar-stack').length, 0)
  assert.equal(all(head, 'tt-tasks-button').length, 0)
  assert.equal(all(head, 'thread-tile').length, 1)
  assert.doesNotMatch(text(head), /Sprout|Quill/)
  o.thread.members[0].background = 2
  o.thread.members[0].background_tasks = [{ id: '1', description: 'One' }, { id: '2', description: 'Two' }]
  o.tasksOpen = true
  const tree = f.view()
  assert.equal(text(find(tree, 'tt-task-count')), '2')
  assert.equal(all(tree, 'tt-task-group').length, 1)
  assert.equal(all(tree, 'tt-task-row').length, 2)
  assert.equal(all(tree, 'tt-member-activity').length, 0)
  assert.ok(find(tree, 'tt-row').className.includes('on'))
  assert.equal(find(tree, 'tt-row').getAttribute('aria-current'), 'page')
})
test('six glyph choices PATCH the thread icon, survive a fresh reading, and reset to the group glyph', async (t) => {
  const f = setup(t)
  f.threads.st.manage = true
  assert.equal(all(f.view(), 'tt-icon-choice').length, 6)
  assert.equal(all(f.view(), 'tt-icon-choice').every((node) => node.childNodes[0].nodeName.toLowerCase() === 'svg'), true)
  for (const [label, expected] of [['Use Conversation icon', 'glyph:chat'], ['Use Folder icon', 'glyph:folder'], ['Default icon', null]]) {
    all(f.view(), 'tt-icon-choice').find((node) => node.getAttribute('aria-label') === label).fire('click')
    await f.threads.st.open.patchTail
    assert.deepEqual(f.calls.at(-1), { method: 'PATCH', path: '/threads/one', body: { icon: expected } })
    f.threads.st.open.thread = structuredClone(f.record)
    assert.equal(all(f.view(), 'tt-icon-choice').find((node) => node.getAttribute('aria-label') === label).getAttribute('aria-pressed'), 'true')
  }
  assert.equal(threadGlyph('🪴'), '🪴', 'old emoji remain intact')
  f.threads.st.open.thread.general = true
  assert.equal(all(f.view(), 'tt-icon-choice').length, 0, 'General retains its group identity')
})
test('an ignored icon PATCH reports the failure and keeps the saved icon', async (t) => {
  const f = setup(t, { ignore: true }); f.threads.st.manage = true
  all(f.view(), 'tt-icon-choice').find((node) => node.getAttribute('aria-label') === 'Use Conversation icon').fire('click')
  await f.threads.st.open.patchTail
  assert.match(f.threads.st.open.error, /hasn’t applied/)
  assert.equal(f.threads.st.open.thread.icon, null)
})
test('listening faces cap at four with overflow, follow dictation and retain a departing face briefly', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'] })
  const f = setup(t)
  assert.equal(all(f.view(), 'tt-listener').length, 0)
  assert.ok(!find(f.view(), 'tt-conversation').className.includes('has-listeners'))
  f.field.value = 'Hello'; assert.equal(all(f.view(), 'tt-listener')[0].getAttribute('aria-label'), 'Sprout')
  assert.ok(find(f.view(), 'tt-conversation').className.includes('has-listeners'))
  f.field.value = '@quill @scout'; f.view(); t.mock.timers.tick(240)
  assert.deepEqual(all(f.view(), 'tt-listener').map((n) => n.getAttribute('data-persona')), ['quill', 'scout'])
  f.field.value = '@all'; f.view(); t.mock.timers.tick(240)
  assert.equal(all(f.view(), 'tt-listener').length, 5)
  assert.equal(text(find(f.view(), 'tt-listener-more')), '+1')
  f.field.value = ''; f.view(); t.mock.timers.tick(240)
  assert.equal(all(f.view(), 'tt-listener').length, 0)
  f.dictate(true); assert.equal(all(f.view(), 'tt-listener').length, 1)
  f.dictate(false); assert.ok(all(f.view(), 'tt-listener')[0].className.includes('leaving'))
  assert.ok(find(f.view(), 'tt-conversation').className.includes('has-listeners'))
  t.mock.timers.tick(240); assert.equal(all(f.view(), 'tt-listener').length, 0)
  assert.ok(!find(f.view(), 'tt-conversation').className.includes('has-listeners'))
})
test('a draft retains faces through a pause, unresolved mention and stream/member redraws until cleared', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'] })
  const f = setup(t), o = f.threads.st.open
  o.thread.default = null
  o.thread.members = o.thread.members.slice(0, 3)
  const faces = () => all(f.view(), 'tt-listener').filter((n) => !n.className.includes('leaving')).map((n) => n.getAttribute('data-persona'))
  f.field.value = '@all A thought'
  assert.deepEqual(faces(), ['sprout', 'quill', 'scout'])
  t.mock.timers.tick(10000)
  assert.deepEqual(faces(), ['sprout', 'quill', 'scout'])
  assert.equal(all(f.view(), 'tt-listening-label').length, 0, 'the draft has faces without a label')
  f.field.value = '@al A thought'
  assert.deepEqual(faces(), ['sprout', 'quill', 'scout'])
  for (const event of [
    { type: 'message.delta', persona: 'sprout', draft: 'A live answer' },
    { type: 'members', members: o.thread.members.map((m) => ({ ...m, working: false })) },
    { type: 'thread.snapshot', members: o.thread.members, turns: [], pending: [] },
  ]) {
    applyThreadEvent(o, event); t.mock.timers.tick(10000)
    assert.deepEqual(faces(), ['sprout', 'quill', 'scout'])
  }
  f.field.value = '@quill Next thought'
  assert.deepEqual(faces(), ['quill'], 'a resolved set replaces the held one')
  o.sending = true; assert.deepEqual(faces(), [])
  o.sending = false; assert.deepEqual(faces(), ['quill'], 'failed send restores the still-held draft')
  f.field.value = ''; faces(); t.mock.timers.tick(240)
  assert.deepEqual(faces(), [])
  f.field.value = '@al A new thought'
  assert.deepEqual(faces(), [], 'a new draft inherits no recipients')
})
for (const reduced of [false, true]) test(`recipient faces have no listening label, reduced motion=${reduced}`, (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'] })
  const previous = globalThis.matchMedia
  globalThis.matchMedia = () => ({ matches: reduced })
  t.after(() => { if (previous) globalThis.matchMedia = previous; else delete globalThis.matchMedia })
  const f = setup(t)
  const check = (count) => {
    const tree = f.view(), recipients = find(tree, 'tt-listeners')
    assert.equal(all(tree, 'tt-listener').filter((n) => !n.className.includes('leaving')).length, count)
    assert.equal(all(tree, 'tt-listening-label').length, 0)
    assert.doesNotMatch(text(recipients), /listening/i)
    assert.equal(recipients.getAttribute('aria-label'), 'Message recipients')
  }
  check(0)
  f.field.value = 'Hello'; check(1)
  t.mock.timers.tick(10000); check(1)
  f.field.value = '@quill @scout'; check(2)
  f.field.value = '@all'; check(5)
  f.threads.st.open.sending = true; check(0)
  f.threads.st.open.sending = false; check(5)
  f.field.value = ''; f.dictate(true); check(1)
  f.dictate(false); check(0)
})
test('inline avatar drawing leaves the exact markdown, selection offsets and code unchanged', () => {
  installDom()
  const md = 'Hi @quill, **words** and `@scout`'
  const field = h('div', {}, ...nodesFor(md, (node) => paintMention(node, { initial: 'Q', color: 'red', shape: '50%' })))
  const mention = all(field, 'mention')[0]
  assert.equal(mention.getAttribute('data-face'), 'Q')
  assert.equal(text(mention), '@quill')
  const read = readField(field, { node: mention.firstChild, offset: 3 })
  assert.equal(read.md, md)
  assert.equal(read.caret, 6)
  assert.equal(all(field, 'mention').length, 1)
})
test('a held touch reveals the recipient name; a quick tap does not and the hint settles away', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'] })
  const f = setup(t); f.field.value = 'Hello'
  const face = () => all(f.view(), 'tt-listener')[0]
  face().fire('pointerdown', { pointerType: 'touch' }); t.mock.timers.tick(200)
  face().fire('pointerup'); t.mock.timers.tick(300)
  assert.ok(!face().className.includes('show-name'))
  face().fire('pointerdown', { pointerType: 'touch' }); t.mock.timers.tick(450)
  assert.ok(face().className.includes('show-name'))
  face().fire('pointerup'); t.mock.timers.tick(1800)
  assert.ok(!face().className.includes('show-name'))
})

test('the inset is reclaimed only at the end, preventing a near-end scroll clamp', () => {
  assert.equal(listeningInset(true, 0, false), 44)
  assert.equal(listeningInset(false, 44, false), 44)
  assert.equal(listeningInset(false, 44, true), 0)
  assert.equal(listeningInset(false, 0, false), 0)
})

test('row subtitle prioritizes asking over generating over background over unread agent replies', async () => {
  const { threadSubtitle } = await import('../public/thread-identity.js')
  const name = (id) => agents.find((a) => a.id === id)?.name ?? id
  const row = (members, extra = {}) => ({ members: members.map((m) => ({ persona: 'sprout', ...m })), ...extra })
  const sub = (members, extra, options) => threadSubtitle(row(members, extra), name, options)
  assert.equal(sub([]), 'Quiet')
  assert.equal(sub([{ working: false, busy: true, running: true }]), 'Quiet', 'explicit false wins over legacy busy and residency')
  assert.equal(sub([{ busy: true }]), 'Sprout working')
  assert.equal(sub([{ working: true, background: 2 }]), 'Sprout working')
  assert.equal(sub([{ working: false, busy: true, background: 2 }]), 'Sprout waiting')
  assert.equal(sub([{ background: 1 }]), 'Sprout waiting')
  assert.equal(sub([{ working: true }, { persona: 'quill', working: true }]), 'Sprout and Quill working')
  assert.equal(sub([{ working: true }, { persona: 'quill', background: 2, working: false }]), 'Sprout working · Quill waiting')
  assert.equal(sub([{ working: true }, { persona: 'quill', working: true }, { persona: 'scout', working: true }]), 'Sprout +2 working')
  for (const field of [{ asking: true }, { pending: [{ request_id: 'r' }] }, { pending_permissions: [{ request_id: 'r' }] }]) {
    assert.equal(sub([{ working: true, background: 2, ...field }]), 'Sprout is asking you')
  }
  assert.equal(sub([{ working: true }], {}, { pending: [{ persona: 'sprout' }] }), 'Sprout is asking you')
  assert.equal(sub([{ working: false }], {}, { resident: () => ({ state: 'waiting' }) }), 'Sprout is asking you', 'live session waiting is normalized asking')
  assert.equal(sub([{ asking: true }, { persona: 'quill', asking: true }]), 'Sprout and Quill are asking you')
  const reply = { unread: 1, last: { author: 'sprout', text: 'Ready.' } }
  assert.equal(sub([{}], reply), 'Sprout replied')
  assert.equal(sub([{ working: true }], reply), 'Sprout working')
  assert.equal(sub([{}], { ...reply, unread: 0 }), 'Quiet')
  assert.equal(sub([{}], { ...reply, last: { author: 'you' } }), 'Quiet')
  assert.equal(sub([{}], { ...reply, last: { author: 'system' } }), 'Quiet')
  assert.equal(sub([], reply, { isAgent: (id) => id === 'sprout' }), 'Sprout replied', 'a removed member’s last reply still belongs to an agent')
})

test('a chosen icon wins; only a sole default member supplies the automatic thread face', async () => {
  const { threadPersona } = await import('../public/thread-identity.js')
  const solo = { ...thread, members: thread.members.slice(0, 1) }
  assert.equal(threadPersona(solo), 'sprout')
  assert.equal(threadPersona({ ...solo, icon: 'glyph:chat' }), null)
  assert.equal(threadPersona({ ...solo, icon: '🪴' }), null)
  assert.equal(threadPersona({ ...solo, default: null }), null)
  assert.equal(threadPersona({ ...solo, default: 'quill' }), null)
  assert.equal(threadPersona({ ...solo, general: true }), null)
  assert.equal(threadPersona(thread), null)
})

test('listening pulses throttle and respect reduced motion', async () => {
  const { listeningPulse } = await import('../public/thread-identity.js')
  const state = {}
  assert.equal(listeningPulse(state, 0, false), true)
  assert.equal(listeningPulse(state, 100, false), false)
  assert.equal(listeningPulse(state, 280, false, .6), true)
  assert.equal(state.pulseStrength, .6)
  assert.equal(listeningPulse(state, 560, false, 0), false)
  assert.equal(listeningPulse(state, 560, true, 1), false)
  assert.equal(state.pulse, 2)
})

test('rows omit the trailing default face, provider and all avatar rings', (t) => {
  const f = setup(t)
  let row = find(f.view(), 'tt-row')
  assert.equal(all(row, 'badge').length, 0)
  assert.equal(all(row, 'ring').length, 0)
  assert.equal(all(row, 'tt-default-face').length, 0)
  assert.equal(text(find(row, 'sr-only')), 'Default: Sprout')
  assert.equal(find(row, 'srow-name'), null)
  assert.ok(find(row, 'tt-quiet'))
  f.record.default = null
  row = find(f.view(), 'tt-row')
  assert.equal(all(row, 'tt-default-face').length, 0)
})
