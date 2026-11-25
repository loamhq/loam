// Agent draft card lifecycle: expand/collapse, auto-collapse on sends, replies
// and revisions, quiet dismissal without discarding the draft, and return on
// later revisions. Close every thread to avoid dangling reconnect timers.
import test from 'node:test'
import assert from 'node:assert/strict'
import { installDom } from './fakedom.js'
import { all, find, text } from './browser.js'
import { h } from '../public/ui.js'
import { createAgentDrafts } from '../public/agent-drafts.js'
import { createThreads } from '../public/threads.js'

installDom()

const tick = () => new Promise(resolve => setImmediate(resolve))

const realFetch = globalThis.fetch
test.before(() => {
  globalThis.fetch = async (_href, { signal } = {}) => new Response(
    new ReadableStream({ start(controller) { signal?.addEventListener('abort', () => controller.close()) } }),
    { headers: { 'content-type': 'text/event-stream' } },
  )
})
test.after(() => { globalThis.fetch = realFetch })

function memoryStorage() {
  const data = new Map()
  return {
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => data.set(k, String(v)),
    removeItem: (k) => data.delete(k),
    dump: () => Object.fromEntries(data),
  }
}

const sampleDraft = (extra = {}) => ({
  draft: 'd-10203040',
  id: 'alder',
  name: 'Alder',
  rev: 1,
  ready: true,
  problems: [],
  about: 'A calm archivist who keeps reading lists and book notes tidy.',
  soul: '# Alder\n\nA calm archivist who keeps reading lists and book notes tidy.\n\nNotices patterns across the books you finish and suggests the next one quietly.',
  config: '## Scope\n\n- Reading notes and library lists\n\n## Never\n\n- Buys books or edits other folders',
  ...extra,
})

test('quiet close hides the draft without deleting it, persists across reloads, and returns collapsed on a new revision', async () => {
  const storage = memoryStorage()
  const calls = []
  let drafts = [sampleDraft()]
  let revisions = 0
  let dismissedEvents = 0

  const make = () => createAgentDrafts({
    api: async (method, path) => {
      calls.push([method, path])
      return { drafts }
    },
    redraw() {},
    onDismiss: () => { dismissedEvents += 1 },
    onRevision: () => { revisions += 1 },
    storage,
  })

  const c1 = make()
  await c1.load('t-1')
  let card = find(c1.view('t-1'), 'agent-draft-card')
  assert.ok(card, 'draft card renders initially')
  assert.equal(card.getAttribute('data-expanded'), 'false')

  // Expand, then close via the quiet close control.
  find(card, 'agent-draft-more').fire('click')
  card = find(c1.view('t-1'), 'agent-draft-card')
  assert.equal(card.getAttribute('data-expanded'), 'true')
  assert.ok(find(card, 'agent-draft-full'))

  const closeBtn = find(card, 'agent-draft-close')
  assert.ok(closeBtn, 'quiet close control is present')
  assert.equal(closeBtn.getAttribute('aria-label'), 'Hide Alder draft')
  closeBtn.fire('click')

  assert.equal(dismissedEvents, 1)
  assert.equal(c1.view('t-1'), null, 'closing hides the card')
  assert.equal(calls.some(([m]) => m === 'DELETE'), false, 'closing never deletes the draft')

  // Re-loading the same revision (e.g. on turn.finished) keeps it hidden.
  await c1.load('t-1')
  assert.equal(c1.view('t-1'), null)

  // Fresh controller (simulating page reload) keeps rev 1 hidden.
  const c2 = make()
  await c2.load('t-1')
  assert.equal(c2.view('t-1'), null, 'dismissal persists across reloads for the same revision')

  // A new revision arrives: card returns, collapsed, showing 2nd draft.
  drafts = [sampleDraft({ rev: 2, about: 'Warm archivist who tracks reading lists and quotes.' })]
  await c2.load('t-1')
  card = find(c2.view('t-1'), 'agent-draft-card')
  assert.ok(card, 'card returns when a new revision arrives')
  assert.equal(card.getAttribute('data-expanded'), 'false', 'returned card starts collapsed')
  assert.equal(find(card, 'agent-draft-full'), null)
  assert.equal(text(find(card, 'agent-draft-rev')), '2nd draft')
  assert.equal(text(find(card, 'agent-draft-summary')), 'Warm archivist who tracks reading lists and quotes.')
  assert.ok(revisions >= 1)

  // Expanding rev 2 stays open when a redundant load returns the same rev 2.
  find(card, 'agent-draft-more').fire('click')
  assert.equal(find(c2.view('t-1'), 'agent-draft-card').getAttribute('data-expanded'), 'true')
  await c2.load('t-1')
  assert.equal(find(c2.view('t-1'), 'agent-draft-card').getAttribute('data-expanded'), 'true', 'same revision does not collapse user expansion')

  // Revision 3 updates in place and collapses again.
  drafts = [sampleDraft({ rev: 3, about: 'Updated for 3rd draft.' })]
  await c2.load('t-1')
  card = find(c2.view('t-1'), 'agent-draft-card')
  assert.equal(card.getAttribute('data-expanded'), 'false', 'new revision collapses expanded card')
  assert.equal(text(find(card, 'agent-draft-rev')), '3rd draft')
  assert.equal(text(find(card, 'agent-draft-summary')), 'Updated for 3rd draft.')

  // Making the agent via chat removes the draft from GET /agents/drafts and prunes storage.
  find(card, 'agent-draft-close').fire('click')
  assert.equal(c2.view('t-1'), null)
  drafts = []
  await c2.load('t-1')
  assert.equal(c2.view('t-1'), null)
  assert.deepEqual(storage.dump(), {}, 'dismissed entry is pruned once the draft is gone')
})

test('dismissing one of two drafts leaves the other visible and keeps storage isolated per draft', async () => {
  const storage = memoryStorage()
  const first = sampleDraft({ draft: 'd-one', id: 'alder', name: 'Alder', rev: 1 })
  const second = sampleDraft({ draft: 'd-two', id: 'birch', name: 'Birch', rev: 1 })
  const c = createAgentDrafts({
    api: async () => ({ drafts: [first, second] }),
    redraw() {},
    storage,
  })
  await c.load('t-1')
  assert.equal(all(c.view('t-1'), 'agent-draft-card').length, 2)

  all(c.view('t-1'), 'agent-draft-close')[0].fire('click')
  const remaining = all(c.view('t-1'), 'agent-draft-card')
  assert.equal(remaining.length, 1)
  assert.equal(remaining[0].getAttribute('data-draft'), 'd-two')

  all(c.view('t-1'), 'agent-draft-close')[0].fire('click')
  assert.equal(c.view('t-1'), null, 'hiding all drafts removes the container')
})

test('sending a message or receiving a turn/reply collapses an expanded draft card; streaming deltas do not', async (t) => {
  let deliver = null
  const draft = sampleDraft()
  let drafts = [draft]
  let messageSeq = 3
  const field = h('textarea')
  const placeholders = []
  const compose = {
    field: () => field,
    render: (ctx) => { placeholders.push(ctx.changePrompt); return h('div', { class: 'composer-fixture' }) },
    leave() {}, files: () => [], busy: () => false, clear() { field.value = '' }, restore() {}, changed() {}, dragProps: () => ({}),
  }
  const threadRecord = {
    thread: { id: 't-1', title: 'New librarian', default: 'scout', members: [{ persona: 'scout', engine: 'claude-code' }] },
    messages: [
      { id: 'm1', author: 'you', kind: 'message', text: 'Let us draft Alder.', at: '2026-11-25T10:00:00Z' },
      { id: 'm2', author: 'scout', kind: 'message', engine: 'claude-code', text: 'Here is a first draft below.', at: '2026-11-25T10:01:00Z' },
      { id: 'm3', author: 'loam', kind: 'system', text: 'Scout drafted a new agent: Alder (draft d-10203040, revision 1)', at: '2026-11-25T10:01:01Z' },
    ],
    has_more: false,
    turn: null,
    turns: [],
  }
  const threads = createThreads({
    api: async (method, path, body) => {
      if (path.startsWith('/agents/drafts')) return { drafts }
      if (method === 'POST' && path === '/threads/t-1/messages') {
        const msg = { id: `m${++messageSeq}`, author: 'you', kind: 'message', text: body.text, at: '2026-11-25T10:02:00Z' }
        threadRecord.messages.push(msg)
        return { accepted: true, message: msg }
      }
      return threadRecord
    },
    redraw() {},
    agents: () => [{ id: 'scout', name: 'Scout' }],
    agentsReady: () => true,
    providers: () => [],
    rail: () => null,
    nav: () => null,
    enter() {},
    leave() {},
    compose,
  })
  t.after(() => threads.close({ navigate: false }))

  // Use a real SSE stream we can push events into.
  const prevFetch = globalThis.fetch
  const encoder = new TextEncoder()
  globalThis.fetch = async (_href, { signal } = {}) => new Response(
    new ReadableStream({
      start(controller) {
        deliver = (ev) => controller.enqueue(encoder.encode(`data: ${JSON.stringify(ev)}\n\n`))
        signal?.addEventListener('abort', () => controller.close())
      },
    }),
    { headers: { 'content-type': 'text/event-stream' } },
  )
  t.after(() => { globalThis.fetch = prevFetch })

  await threads.open('t-1', { routed: true })
  for (let i = 0; i < 6; i += 1) await tick()

  // Expand the draft card.
  let pane = threads.pane()[0]
  find(pane, 'agent-draft-more').fire('click')
  assert.equal(threads.agentDrafts.expanded.has('d-10203040'), true)

  // Sending a message collapses the card immediately and pins to latest.
  field.value = 'Make Alder warmer please.'
  await threads.send()
  assert.equal(threads.agentDrafts.expanded.has('d-10203040'), false, 'send collapses expanded draft card')
  assert.equal(threads.st.open.atEnd, true)

  // If the user expands the card again while idle, turn.started collapses it.
  pane = threads.pane()[0]
  find(pane, 'agent-draft-more').fire('click')
  assert.equal(threads.agentDrafts.expanded.has('d-10203040'), true)
  deliver({ type: 'turn.started', persona: 'scout', turn_id: 'turn-1' })
  for (let i = 0; i < 4; i += 1) await tick()
  assert.equal(threads.agentDrafts.expanded.has('d-10203040'), false, 'turn.started collapses expanded card')

  // If the user intentionally expands mid-turn, message.delta does not slam it shut.
  pane = threads.pane()[0]
  find(pane, 'agent-draft-more').fire('click')
  assert.equal(threads.agentDrafts.expanded.has('d-10203040'), true)
  deliver({ type: 'message.delta', persona: 'scout', text: 'Updating Alder now…' })
  for (let i = 0; i < 4; i += 1) await tick()
  assert.equal(threads.agentDrafts.expanded.has('d-10203040'), true, 'streaming delta leaves user-expanded card open')

  // When the finished reply arrives, it collapses so the new reply is in view.
  deliver({ type: 'message.done', message: { id: 'm5', author: 'scout', kind: 'message', engine: 'claude-code', text: 'Done — Alder is warmer now.', at: '2026-11-25T10:02:10Z' } })
  for (let i = 0; i < 4; i += 1) await tick()
  assert.equal(threads.agentDrafts.expanded.has('d-10203040'), false, 'new reply collapses expanded card')

  // Clicking Ask for changes sets changePrompt, and closing the card clears it.
  pane = threads.pane()[0]
  const [, askBtn] = all(find(pane, 'agent-draft-actions'), 'pillbtn')
  askBtn.fire('click')
  threads.pane()
  assert.equal(placeholders.at(-1), 'Tell Scout what to change')

  pane = threads.pane()[0]
  find(pane, 'agent-draft-close').fire('click')
  threads.pane()
  assert.equal(placeholders.at(-1), null, 'closing the card clears the ask-for-changes prompt')
  assert.equal(find(threads.pane()[0], 'agent-draft-card'), null)

  // A system revision message reloads drafts and brings the card back collapsed.
  drafts = [sampleDraft({ rev: 2, about: 'Warmer archivist for your library.' })]
  deliver({ type: 'message.done', message: { id: 'm6', author: 'loam', kind: 'system', text: 'Scout revised draft d-10203040: Alder, revision 2 (soul)', at: '2026-11-25T10:02:15Z' } })
  for (let i = 0; i < 6; i += 1) await tick()
  pane = threads.pane()[0]
  const returned = find(pane, 'agent-draft-card')
  assert.ok(returned, 'revision brings dismissed card back')
  assert.equal(returned.getAttribute('data-expanded'), 'false')
  assert.equal(text(find(returned, 'agent-draft-rev')), '2nd draft')
})
