import test from 'node:test'
import assert from 'node:assert/strict'
import { canReply, quoteLine, replyQuotes, replyGesture } from '../public/thread-replies.js'
import { createThreads } from '../public/threads.js'
import { applyThreadEvent } from '../public/thread-record.js'
import { installDom } from './fakedom.js'
import { h } from '../public/ui.js'
import { all, find, text } from './browser.js'

const original = { id: 'm1', author: 'sprout', kind: 'message', text: 'The first line.\n\nThe rest stays in the message.' }
const human = { id: 'm2', author: 'you', kind: 'message', text: 'Please expand this.', reply_to: 'm1' }
function setup(t, api = async () => ({})) {
  installDom(); globalThis.innerWidth = 390
  const field = { value: 'My draft' }, files = []
  const threads = createThreads({ api, redraw() {}, agents: () => [{ id: 'sprout', name: 'Sprout' }], nav: () => null,
    compose: { field: () => field, files: () => files, busy: () => false, clear() {}, render: () => h('div'), dragProps: () => ({}) } })
  threads.st.open = { thread: { id: 'one', title: 'A thread', members: [{ persona: 'sprout', engine: 'claude-code' }] }, messages: [original, human],
    permissions: new Map(), typing: new Set(), steps: new Map() }
  threads.st.listLive = true
  t.after(() => threads.pause())
  return { threads, field, files, view: () => threads.view(false) }
}
test('reply targets exclude system, optimistic and streaming lines; previews use the first spoken line or attachment names', () => {
  assert.equal(canReply(original), true); assert.equal(canReply(human), true)
  for (const m of [null, { ...original, kind: 'system' }, { ...original, streaming: true }, { ...human, id: 'pending-send' }]) assert.equal(canReply(m), false)
  assert.equal(quoteLine(original), 'The first line.')
  assert.equal(quoteLine({ ...original, segments: [{ did: [{ label: 'Read' }] }, { text: '\nAn earlier spoken part.\nMore.' }] }), 'An earlier spoken part.')
  assert.equal(quoteLine({ text: '', attachments: [{ name: 'Notes.md' }] }), 'Notes.md')
})
test('reply selection and cancel keep draft text and attachments; both authors render quotes and missing originals do not link', (t) => {
  const { threads, field, files, view } = setup(t)
  files.push({ path: 'attachment.md', name: 'attachment.md' })
  const o = threads.st.open
  o.messages.push({ id: 'm3', author: 'sprout', text: 'Yes, here is more.', reply_to: 'm2' }, { id: 'm4', author: 'sprout', text: 'Missing.', reply_to: 'm999' }, { id: 'm5', kind: 'system', text: 'A system line.' })
  let tree = view()
  assert.equal(all(tree, 'tt-footer-reply').length, 4)
  assert.deepEqual(all(tree, 'tt-quote').map(text), ['SproutThe first line.', 'YouPlease expand this.', 'Earlier message'])
  assert.equal(all(tree, 'tt-quote').at(-1).nodeName, 'DIV')
  all(tree, 'tt-footer-reply')[0].fire('click')
  assert.equal(o.replyTo.id, 'm1'); assert.equal(o.focusReplyDraft, true)
  tree = view()
  assert.match(text(find(tree, 'tt-reply-chip')), /Replying to SproutThe first line/)
  const cancel = all(tree, 'iconbtn').find((n) => n.getAttribute('aria-label') === 'Cancel reply')
  cancel.fire('click')
  assert.equal(o.replyTo, null); assert.equal(threads.st.drafts.get('one').replyTo, null)
  assert.equal(field.value, 'My draft'); assert.equal(files[0].path, 'attachment.md')
  assert.equal(find(view(), 'tt-reply-chip'), null)
})
test('send carries reply_to beside attachments and clears the chip on acceptance; rejected sends retain it', async (t) => {
  const writes = []; let fail = true
  const { threads, files, view } = setup(t, async (method, route, body) => {
    writes.push({ method, route, body })
    if (fail) throw new Error('This earlier message no longer exists.')
    return { accepted: true, message: { id: 'm3', author: 'you', kind: 'message', ...body } }
  })
  files.push({ path: 'saved.md' })
  all(view(), 'tt-footer-reply')[0].fire('click')
  await threads.send()
  assert.equal(threads.st.open.replyTo.id, 'm1')
  assert.match(threads.st.open.error, /Your message is still here/)
  fail = false; await threads.send()
  assert.deepEqual(writes.at(-1), { method: 'POST', route: '/threads/one/messages', body: { text: 'My draft', reply_to: 'm1', attachments: ['saved.md'] } })
  assert.equal(threads.st.open.replyTo, null)
  assert.equal(threads.st.open.messages.at(-1).reply_to, 'm1')
})
test('icon-only footer Reply is available for either author at every width, including messages without timestamps', (t) => {
  const { threads, field, view } = setup(t)
  threads.st.open.messages.push({ ...original, id: 'live', streaming: true }, { ...original, id: 'system', kind: 'system' }, { ...human, id: 'pending-send' })
  for (const width of [390, 899, 900, 1440, 1920]) {
    globalThis.innerWidth = width
    const controls = all(view(), 'tt-footer-reply')
    assert.equal(controls.length, 2)
    assert.equal(all(view(), 'tt-message-actions').length, 0)
    assert.equal(all(view(), 'tt-reply-action').length, 0)
    assert.equal(all(view(), 'tt-footer-copy').length, 2)
    for (const [index, control] of controls.entries()) {
      assert.equal(control.getAttribute('aria-label'), 'Reply')
      assert.equal(text(control), '')
      control.fire('click')
      assert.equal(threads.st.open.replyTo.id, index === 0 ? 'm1' : 'm2')
      assert.equal(threads.st.open.focusReplyDraft, true)
      assert.equal(field.value, 'My draft')
    }
  }
  threads.st.open.sending = true
  assert.ok(all(view(), 'tt-footer-reply').every((n) => n.hasAttribute('disabled')))
})
test('quote resolution pages by record order, deduplicates reads and reuses them for the later anchored jump', async () => {
  const reads = [], visible = [{ id: 'm90', author: 'you', text: 'Recent', reply_to: 'm1' }]
  const pages = { m90: { messages: [{ id: 'm8', text: 'Recovered late reply' }], has_more: true }, m8: { messages: [original], has_more: false } }
  const quotes = replyQuotes({ read: async (before) => { reads.push(before); return pages[before] }, changed() {} })
  await Promise.all([quotes.ensure(visible, true), quotes.ensure(visible, true)])
  assert.deepEqual(reads, ['m90', 'm8'])
  assert.equal(quotes.get('m1'), original)
  assert.deepEqual(visible.map((m) => m.id), ['m90'], 'lookup does not expand the visible reading window')
  assert.equal(await quotes.page('m90'), pages.m90)
  assert.deepEqual(reads, ['m90', 'm8'])
  assert.equal(quotes.get('m999'), null)
})
test('footer Copy preserves markdown and confirms success for one second without a label or a toast', async (t) => {
  const { threads, view } = setup(t), writes = []
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const held = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
  let refused = false
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { clipboard: { writeText: async (text) => { if (refused) throw new Error('Refused'); writes.push(text) } } } })
  t.after(() => { if (held) Object.defineProperty(globalThis, 'navigator', held); else delete globalThis.navigator })
  const markdown = '**The first line.**\n\n- Keep [the notes](notes.md).\n- `Read` them first.'
  threads.st.open.messages[0] = { ...original, text: markdown }
  const copy = () => find(view(), 'tt-footer-copy')
  assert.equal(copy().getAttribute('aria-label'), 'Copy')
  assert.equal(text(copy()), '')
  copy().fire('click'); await new Promise(setImmediate)
  assert.deepEqual(writes, [markdown])
  assert.equal(copy().hasAttribute('data-copied'), true)
  assert.equal(copy().getAttribute('aria-label'), 'Copy')
  t.mock.timers.tick(999); assert.equal(copy().hasAttribute('data-copied'), true)
  t.mock.timers.tick(1); assert.equal(copy().hasAttribute('data-copied'), false)
  refused = true; copy().fire('click'); await new Promise(setImmediate)
  assert.equal(copy().hasAttribute('data-copied'), false, 'a clipboard refusal must never show success')
  assert.equal(threads.st.open.error, undefined)
})
test('missing and malformed quotes stop scanning, a read failure does not loop, and a departed thread receives no updates', async () => {
  for (const reply_to of ['m999', 'broken']) {
    let reads = 0
    const quotes = replyQuotes({ read: async () => { reads++; return { messages: [original], has_more: false } }, changed() {} })
    await quotes.ensure([{ id: 'm9', reply_to }], true); await quotes.ensure([{ id: 'm9', reply_to }], true)
    assert.equal(reads, reply_to === 'm999' ? 1 : 0); assert.equal(quotes.get(reply_to), null)
  }
  let reads = 0
  const quotes = replyQuotes({ read: async () => { reads++; throw new Error('Offline') }, changed() {} })
  await quotes.ensure([human], true); await quotes.ensure([human], true); assert.equal(reads, 1)
  let release, changes = 0, active = true
  const departed = replyQuotes({ read: () => new Promise((resolve) => { release = resolve }), changed: () => changes++, active: () => active })
  const pending = departed.ensure([human], true)
  await Promise.resolve(); active = false; release({ messages: [original], has_more: false }); await pending
  assert.equal(changes, 0)
})
test('persona message.done keeps the durable reply target on replay', () => {
  const o = { thread: { members: [] }, messages: [], typing: new Set(), permissions: new Map() }
  applyThreadEvent(o, { type: 'message.done', message: { ...original, reply_to: 'm0' } })
  assert.equal(o.messages[0].reply_to, 'm0')
})
test('swipe claims only a horizontal right drag; threshold and cancel preserve native scrolling', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const classes = new Set(), styles = new Map(), calls = []
  const node = { style: { setProperty: (k, v) => styles.set(k, v), removeProperty: (k) => styles.delete(k) }, classList: { add: (...items) => items.forEach((c) => classes.add(c)), remove: (...items) => items.forEach((c) => classes.delete(c)), toggle: (c, on) => on ? classes.add(c) : classes.delete(c) } }
  const e = (x, y) => ({ pointerType: 'touch', pointerId: 1, clientX: x, clientY: y, currentTarget: node, target: { closest: () => null }, preventDefault() {} })
  const gesture = replyGesture({ reply: (m) => calls.push(['reply', m.id]), reduced: () => true })
  gesture.down(e(0, 0), original); gesture.move(e(20, 60)); gesture.up(); assert.deepEqual(calls, [])
  gesture.down(e(0, 0), original); gesture.move(e(40, 0)); gesture.up(); assert.deepEqual(calls, [])
  gesture.down(e(0, 0), original); gesture.move(e(65, 2)); assert.ok(classes.has('reply-ready'))
  classes.clear(); gesture.paint(); assert.ok(classes.has('reply-ready'), 'live redraws retain gesture feedback')
  gesture.up()
  assert.deepEqual(calls, [['reply', 'm1']]); assert.equal(styles.size, 0)
  gesture.down(e(0, 0), original); gesture.move(e(65, 0)); gesture.reset(); gesture.up(); assert.equal(calls.length, 1)
  gesture.down(e(0, 0), human); t.mock.timers.tick(480); gesture.up()
  assert.deepEqual(calls, [['reply', 'm1']], 'stationary holds have no custom action')
  gesture.down(e(0, 0), original); gesture.move(e(65, 0)); gesture.move(e(65, 70)); gesture.up()
  assert.equal(calls.length, 1, 'a swipe that turns vertical does not reply')
})

test('holds, selection drags and interactive targets never become replies or consume native events', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  let replies = 0, selected = false
  const held = Object.getOwnPropertyDescriptor(globalThis, 'getSelection')
  Object.defineProperty(globalThis, 'getSelection', { configurable: true, value: () => ({ isCollapsed: !selected }) })
  t.after(() => { if (held) Object.defineProperty(globalThis, 'getSelection', held); else delete globalThis.getSelection })
  const node = { style: { setProperty() {}, removeProperty() {} }, classList: { add() {}, remove() {}, toggle() {} } }
  const event = (x = 0, y = 0, extra = {}) => ({ pointerType: 'touch', pointerId: 1, clientX: x, clientY: y, currentTarget: node,
    target: { closest: () => null }, preventDefault() { assert.fail('native press/selection must not be consumed') }, ...extra })
  const gesture = replyGesture({ reply: () => replies++ })
  for (const drift of [0, 9]) {
    gesture.down(event(), original); gesture.move(event(drift)); t.mock.timers.tick(600)
    gesture.move(event(90)); gesture.up()
  }
  gesture.down(event(), original); gesture.move(event(0, 70)); gesture.move(event(90, 70)); gesture.up()
  for (const x of [-80, 12, 55]) {
    gesture.down(event(), original); gesture.move(event(x, 40)); gesture.up()
  }
  selected = true
  gesture.down(event(), original); gesture.move(event(90)); gesture.up()
  selected = false
  gesture.down(event(), original); selected = true; gesture.move(event(90)); gesture.up()
  selected = false
  gesture.down(event(), original); gesture.move(event(65, 0, { preventDefault() {} })); selected = true; gesture.up()
  selected = false
  for (const extra of [{ pointerType: 'mouse' }, { isPrimary: false }, { target: { closest: () => ({}) } }]) {
    gesture.down(event(0, 0, extra), original); gesture.move(event(90)); gesture.up()
  }
  assert.equal(replies, 0)
})
test('message context menus and touch starts keep their native defaults without custom overlays', (t) => {
  const { threads, view } = setup(t)
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const tree = view()
  for (const article of all(tree, 'tt-message')) {
    Object.defineProperty(article, 'classList', { value: { remove() {} } })
    const native = { pointerType: 'touch', pointerId: 1, clientX: 0, clientY: 0, currentTarget: article,
      target: { closest: () => null }, preventDefault() { assert.fail('native text interaction prevented') } }
    article.fire('touchstart', native); article.fire('contextmenu', native); article.fire('pointerdown', native)
    t.mock.timers.tick(1000); article.fire('pointerup', native)
  }
  assert.equal(find(view(), 'tt-message-menu'), null)
  assert.equal(threads.st.open.replyTo, undefined)
})
