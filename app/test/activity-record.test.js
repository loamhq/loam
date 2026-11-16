import test from 'node:test'
import assert from 'node:assert/strict'
import { foldJobChains, jobContext, engineLabel } from '../public/activity-record.js'
import { integrationCatalog } from '../public/integration-catalog.js'
import { SHAPES, ORG_SHAPES, withOrgs } from './fixtures/github.js'
import { createActivity } from '../public/activity.js'
import { createRunning } from '../public/running.js'
import { createAgents } from '../public/sessions.js'
import { installDom } from './fakedom.js'
import { all, text } from './browser.js'
import { h } from '../public/ui.js'

test('continuations fold across pages under the newest result, with no title matching', () => {
  const first = { id: 'one', kind: 'job', chain: 'one', ts: '2026-11-12T01:00:00Z', title: 'The work', outcome: 'failed' }
  const final = { ...first, id: 'three', ts: '2026-11-12T03:00:00Z', outcome: 'completed', receipt: 'Receipts/work.md', auto_continuation: 2 }
  const rows = foldJobChains([first, final, { ...first, id: 'two', ts: '2026-11-12T02:00:00Z' }, { ...first, id: 'unrelated', chain: null }])
  assert.equal(rows.length, 2)
  assert.equal(rows[0].receipt, final.receipt)
  assert.equal(rows[0].continuationCount, 2)
  assert.equal(rows[0].outcome, 'completed')
  assert.equal(foldJobChains([final])[0].continuationCount, 2)
  assert.equal(first.continuationCount, undefined)
  assert.equal(jobContext({ caller: 'cron' }), 'from a schedule')
  assert.equal(jobContext({ caller: null }), null)
  assert.equal(engineLabel('codex'), 'Codex')
})

test('working integrations group by service, with account counts and no invented services', () => {
  const state = data => ({ data, loaded: true, missing: false })
  const rows = integrationCatalog({ github: state(withOrgs(SHAPES.authorized, ORG_SHAPES.authorized)) })
  assert.deepEqual(rows.map(r => [r.id, r.summary, r.connected]), [['github', '2 accounts', true]])
  assert.equal(rows[0].accounts[0].scope, 'all repositories')
  assert.equal(rows[0].accounts[1].scope, 'selected repositories')
  assert.equal(rows[0].accounts[1].org, 'octo-org')
  assert.deepEqual(integrationCatalog({ github: { missing: true } }), [])
  assert.equal(integrationCatalog({ github: state(null) })[0].summary, 'Not connected')
})

test('Now pairs a thread with its engineer, separates independent runs, and uses the same job grammar in Earlier', () => {
  installDom()
  const agents = [{ id: 'sprout', name: 'Sprout' }], opened = []
  const providers = () => [{ id: 'claude-code', models: [{ id: 'opus', name: 'Opus' }] }]
  const running = createRunning({ api() {}, redraw() {}, agents: () => agents, providers, openPanel() {}, openConversation() {} })
  running.st.ready = true; running.st.mode = 'rich'
  running.st.liveRecords.set('session', { id: 'session', kind: 'thread', run_kind: 'thread', agent: 'sprout', thread_id: 'thread', thread_title: 'The plan', state: 'working', engine: 'claude-code', model: 'claude-opus-5-5' })
  const job = { job: 'job', kind: 'engineer job', run_kind: 'job', title: 'Build the plan', briefed_by: 'sprout', thread: 'thread', thread_title: 'The plan', caller: 'conversation', engine: 'codex', model: 'gpt-6.1-sol', project: 'Notebook' }
  const activity = createActivity({ api() {}, redraw() {}, agents: () => agents, providers,
    avatar: id => h('span', { text: id }), openRef() {}, openThread: id => opened.push(id), currentWork: jobs => running.inlineView(jobs) })
  activity.st.now = { slots: [job, { kind: 'wake', run_kind: 'reviewer', title: 'Review the changes', agent: 'sprout' }], queue: [], landing: [] }
  activity.st.items = [
    { ...job, id: 'final', kind: 'job', chain: 'chain', auto_continuation: 2, outcome: 'completed', landing: 'landed', ts: '2026-11-12T03:00:00Z' },
    { ...job, id: 'first', kind: 'job', chain: 'chain', outcome: 'failed', ts: '2026-11-12T01:00:00Z' },
  ]
  const view = activity.view()
  assert.match(text(view), /Conversations.*The plan.*Waiting on an engineer.*Opus 5.5.*Engineers.*Build the plan.*On their own.*Review the changes/s)
  assert.equal(all(view, 'activity-engineer-mark').length, 2)
  assert.equal(all(view, 'activity-briefing-face').length, 2)
  assert.equal(all(view, 'activity-engineer').length, 1)
  assert.match(text(all(view, 'activity-engineer')[0]), /landed after 2 continuations/)
  assert.doesNotMatch(text(view), /unknown/i)
  all(view, 'activity-thread-link')[0].fire('click', { stopPropagation() {} })
  assert.deepEqual(opened, ['thread'])
  running.st.liveRecords.clear(); activity.st.now.slots = []
  assert.match(text(activity.view()), /NowNothing runningEarlier/)
  activity.st.items = [{ id: 'old', kind: 'job', title: 'Older work', outcome: 'completed' }]
  assert.doesNotMatch(text(activity.view()), /unknown/i)
  activity.open(activity.st.items[0])
  assert.doesNotMatch(text(activity.dialogNodes()[0].__keep), /Unknown agent/)
})

test('agent role and short description survive the catalog adapter without inventing them', () => {
  const agents = createAgents([{ id: 'sprout', name: 'Sprout', role: ' Keeps projects moving ', description: ' A little room to think ' }, { id: 'quill' }])
  assert.equal(agents.byId.get('sprout').role, 'Keeps projects moving')
  assert.equal(agents.byId.get('sprout').description, 'A little room to think')
  assert.equal('role' in agents.byId.get('quill'), false)
})
