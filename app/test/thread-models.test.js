import test from 'node:test'
import assert from 'node:assert/strict'
import { createThreads } from '../public/threads.js'
import { installDom } from './fakedom.js'
import { all, find, text } from './browser.js'

function setup({ ignore = false, fail = false, modern = true } = {}) {
  installDom(); globalThis.innerWidth = 390
  const record = { id: 'one', title: 'A thread', default: 'sprout', members: [
    { persona: 'sprout', engine: 'claude-code', ...(modern ? { model: 'deep' } : {}) },
    { persona: 'quill', engine: 'claude-code', ...(modern ? { model: null } : {}) },
  ] }
  const calls = []
  const threads = createThreads({ redraw() {}, agents: () => [{ id: 'sprout', name: 'Sprout' }, { id: 'quill', name: 'Quill' }],
    providers: () => [{ id: 'claude-code', name: 'Claude Code', connected: true, models: [{ id: 'deep', name: 'Deep' }] }, { id: 'codex', name: 'Codex', connected: true }, { id: 'offline', name: 'Offline', connected: false, reason: 'Sign in first' }],
    compose: {}, nav: () => null, api: async (method, route, body) => {
      calls.push({ method, route, body })
      if (fail) throw new Error('Provider refused this change.')
      if (!ignore) for (const field of ['engine', 'model']) for (const [persona, value] of Object.entries(body[field] ?? {})) {
        const member = record.members.find((m) => m.persona === persona); member[field] = value
        if (field === 'engine') member.model = null
      }
      return structuredClone(record)
    } })
  threads.st.manage = true
  threads.st.open = { thread: structuredClone(record), messages: [], typing: new Set(), permissions: new Map() }
  const view = () => threads.view(false)
  const control = (tree, label) => all(tree, 'modelbtn').find((n) => n.getAttribute('aria-label') === label)
  const click = (node) => { assert.ok(node, 'control exists'); node.fire('click') }
  const options = () => find(view(), 'tt-configuration-options')
  const select = async (label, value) => {
    click(control(view(), label))
    click(all(options(), 'setrow').find((n) => n.getAttribute('data-value') === value))
    await threads.st.open.patchTail
  }
  return { threads, record, calls, view, control, click, options, select }
}
test('details open an anchored listbox without a second member sheet; null saves for one member only', async () => {
  const f = setup()
  f.click(f.control(f.view(), 'Model for Sprout'))
  assert.equal(find(f.view(), 'tt-configuration'), null)
  assert.equal(f.control(f.view(), 'Model for Sprout').getAttribute('aria-haspopup'), 'listbox')
  assert.equal(f.options().getAttribute('data-menu-layout'), 'anchored')
  assert.ok(f.options().hasAttribute('data-menu-anchored'))
  assert.equal(all(f.view(), 'select').length, 0)
  const defaults = all(f.options(), 'setrow').find((n) => n.getAttribute('data-value') === '')
  assert.match(text(defaults), /Agent’s default/)
  f.click(defaults); await f.threads.st.open.patchTail
  assert.deepEqual(f.calls, [{ method: 'PATCH', route: '/threads/one', body: { model: { sprout: null } } }])
  assert.equal(f.record.members[0].model, null); assert.equal(f.record.members[1].model, null)
  assert.match(text(f.control(f.view(), 'Model for Sprout')), /Agent’s default/)
  // A new reading of the persisted record produces the same visible value.
  f.threads.st.open.thread = structuredClone(f.record)
  assert.match(text(f.control(f.view(), 'Model for Sprout')), /Agent’s default/)
  f.threads.pause()
})
test('provider PATCH is per member; disconnected choices keep the supplied reason and cannot be selected', async () => {
  const f = setup()
  f.click(f.control(f.view(), 'Provider for Sprout'))
  const offline = all(f.options(), 'setrow').find((n) => n.getAttribute('data-value') === 'offline')
  assert.ok(offline.hasAttribute('disabled')); assert.match(text(offline), /Sign in first/)
  f.click(all(f.options(), 'setrow').find((n) => n.getAttribute('data-value') === 'codex')); await f.threads.st.open.patchTail
  assert.deepEqual(f.calls[0].body, { engine: { sprout: 'codex' } })
  assert.equal(f.record.members[0].model, null); assert.equal(f.record.members[1].engine, 'claude-code')
  f.threads.pause()
})
for (const field of ['Provider', 'Model']) test(`an ignored ${field.toLowerCase()} write is reported instead of claiming it saved`, async () => {
  const f = setup({ ignore: true })
  await f.select(`${field} for Sprout`, field === 'Provider' ? 'codex' : '')
  assert.match(f.threads.st.open.error, /hasn’t applied/)
  assert.equal(f.threads.st.open.configurationSaved, false)
  assert.equal(f.record.members[0].engine, 'claude-code'); assert.equal(f.record.members[0].model, 'deep')
  f.threads.pause()
})
test('a refused change remains visible in details and retains the saved value', async () => {
  const f = setup({ fail: true })
  await f.select('Model for Sprout', '')
  assert.match(text(find(f.view(), 'tt-details')), /Provider refused this change/)
  assert.equal(f.threads.st.open.configurationSaved, false); assert.equal(f.record.members[0].model, 'deep')
  f.threads.pause()
})
test('older records still show their default value and explain why model editing is disabled', () => {
  const f = setup({ modern: false })
  f.click(f.control(f.view(), 'Provider for Sprout'))
  const details = find(f.view(), 'tt-details'), control = f.control(details, 'Model for Sprout')
  assert.ok(control.hasAttribute('disabled')); assert.match(text(control), /Agent’s default/)
  assert.match(text(details), /Model settings need a runtime update/)
  f.threads.pause()
})
