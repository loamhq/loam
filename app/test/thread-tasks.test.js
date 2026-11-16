import test from 'node:test'
import assert from 'node:assert/strict'
import { threadBackground, taskTitle, jobStatus, jobElapsed } from '../public/thread-experience.js'
import { createThreads } from '../public/threads.js'
import { installDom } from './fakedom.js'
import { h } from '../public/ui.js'
import { all, find, text } from './browser.js'

const task = { id: 'one', description: 'Brief Loam: Check the phone layout', started_at: '2026-11-11T00:35:00Z' }
const members = () => [{ persona: 'sprout', engine: 'claude-code', busy: true, working: false, background: 2, background_tasks: [task] },
  { persona: 'quill', engine: 'codex', busy: true, working: false, background: 1, background_tasks: [{ ...task, description: 'Brief loam-ui: Read the notes' }] }]
function setup(t, extra = {}) {
  installDom(); globalThis.innerWidth = 390
  const threads = createThreads({ redraw() {}, agents: () => [{ id: 'sprout', name: 'Sprout' }, { id: 'quill', name: 'Quill' }],
    compose: { dragProps: () => ({}), render: () => h('div'), field: () => ({ value: '' }) }, nav: () => null, ...extra })
  threads.st.open = { thread: { id: 'one', title: 'A thread', members: members() }, messages: [
    { id: 'empty', author: 'sprout', text: '', did: [{ label: 'Waiting on background work' }] },
    { id: 'draft:sprout', author: 'sprout', text: '', streaming: true },
    { id: 'reply', author: 'sprout', text: 'The completed reply.' },
  ], typing: new Set(['sprout', 'quill']), permissions: new Map(), steps: new Map(), turns: [], board: { markdown: '', path: '' } }
  t.after(() => threads.pause())
  return { threads, view: () => threads.view(false) }
}
test('header count sums host member counts and preserves named task creators even when ids match', () => {
  const result = threadBackground({ background: 99, members: members() })
  assert.equal(result.count, 3)
  assert.deepEqual(result.tasks.map((task) => task.persona), ['sprout', 'quill'])
  assert.equal(result.tasks.length, 2, 'notification gap is counted without inventing a task')
  assert.deepEqual(threadBackground({ members: [{ background: -1 }, {}] }), { count: 0, tasks: [] })
})
test('task list titles strip the requested prefixes, while the original description stays intact', () => {
  for (const prefix of ['Brief Loam: ', 'Brief loam-ui: ']) assert.equal(taskTitle(prefix + 'Check the layout'), 'Check the layout')
  assert.equal(taskTitle('Review the Brief Loam: notes'), 'Review the Brief Loam: notes')
  assert.equal(task.description, 'Brief Loam: Check the phone layout')
})
test('task details gain a link when the member update supplies the launching reply, and clear it with the task', (t) => {
  const { threads, view } = setup(t), o = threads.st.open
  o.tasksOpen = true; o.selectedTask = { id: 'one', persona: 'sprout' }
  assert.equal(find(view(), 'tt-task-reply'), null)
  o.thread.members[0].background_tasks = [{ ...task, message: 'earlier-reply' }]
  const link = find(view(), 'tt-task-reply')
  assert.equal(text(link), 'Started in this reply')
  link.fire('click')
  assert.equal(o.tasksOpen, false)
  assert.equal(o.replyJump, 'earlier-reply')
  assert.equal(o.atEnd, false)
  assert.equal(o.focusTasksReturn, false, 'navigation will move focus to the reply')
  o.tasksOpen = true; o.selectedTask = { id: 'one', persona: 'sprout' }
  o.thread.members[0].background_tasks = []
  assert.equal(find(view(), 'tt-task-reply'), null)
})
test('busy && !working draws no waiting line, reply slot, fold or band in flow', (t) => {
  const { view } = setup(t), tree = view()
  assert.equal(text(find(tree, 'tt-task-count')), '3')
  assert.equal(all(tree, 'tt-member-activity').length, 0)
  assert.equal(all(tree, 'tt-live-area').length, 0)
  assert.equal(all(tree, 'awaiting').length, 0)
  assert.equal(all(tree, 'fold').length, 0)
  assert.deepEqual(all(tree, 'tt-message').map((n) => n.getAttribute('data-message-id')), ['reply'])
})
test('open list renders each creator and details, then Nothing running before closing and fading the indicator', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const { threads, view } = setup(t), o = threads.st.open
  o.tasksOpen = true
  let tree = view(), panel = find(tree, 'tt-tasks-panel')
  assert.equal(all(panel, 'tt-task-row').length, 2)
  assert.match(text(panel), /Sprout.*Check the phone layout.*Quill.*Read the notes/)
  all(panel, 'tt-task-row')[0].fire('click')
  panel = find(view(), 'tt-tasks-panel')
  assert.match(text(panel), /Brief Loam: Check the phone layout/)
  assert.match(text(panel), /Started/)
  o.thread.members = []
  tree = view(); assert.match(text(find(tree, 'tt-tasks-panel')), /Nothing running/)
  assert.ok(find(tree, 'tt-tasks-button').className.includes('leaving'))
  t.mock.timers.tick(240)
  assert.equal(find(view(), 'tt-tasks-button'), null)
  t.mock.timers.tick(1160)
  assert.equal(o.tasksOpen, false)
  assert.equal(find(view(), 'tt-tasks-panel'), null)
  assert.equal(o.focusTasksReturn, true)
})
test('new work cancels an empty panel close, and zero on first open has no indicator', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const { threads, view } = setup(t), o = threads.st.open
  o.tasksOpen = true; view()
  o.thread.members = []; view()
  t.mock.timers.tick(100)
  o.thread.members = members(); view()
  t.mock.timers.tick(1400)
  assert.equal(o.tasksOpen, true)
  assert.equal(text(find(view(), 'tt-task-count')), '3')
  o.taskCount = 0; o.taskVisible = false; o.thread.members = []; o.tasksOpen = false
  assert.equal(find(view(), 'tt-tasks-button'), null)
})

const clock = Date.parse('2026-11-13T20:00:00Z')
const job = (fields = {}) => ({ job: 'job-one', project: 'Example project', title: 'Keep the work in view', briefed_by: 'sprout',
  state: 'running', started: new Date(clock - 120000).toISOString(), since: new Date(clock - 60000).toISOString(),
  finished: null, receipt: null, elapsed_seconds: 120, ...fields })
test('active jobs count beside member work, finished jobs stay ten minutes, and job errors leave member work intact', () => {
  const jobs = [job(), job({ job: 'queued', state: 'queued', started: null }), job({ job: 'landing', state: 'landing' }),
    job({ job: 'recent', state: 'finished', finished: new Date(clock - 599999).toISOString() }),
    job({ job: 'old', state: 'finished', finished: new Date(clock - 600000).toISOString() })]
  const result = threadBackground({ members: members(), jobs }, clock)
  assert.equal(result.count, 6)
  assert.deepEqual(result.tasks.slice(2).map(t => t.job), ['job-one', 'queued', 'landing', 'recent'])
  assert.equal(result.tasks[2].persona, 'sprout')
  assert.deepEqual(threadBackground({ members: members(), jobs, jobs_error: 'unreadable' }, clock), threadBackground({ members: members() }, clock))
  assert.equal(threadBackground({ members: [], jobs: [job()] }, clock).count, 1, 'an ended persona does not hide the job')
})
test('job words cover every host outcome and queue/landing phases; durations freeze at completion', () => {
  const words = { deployed: 'Landed and live', landed: 'Landed', 'not landed': 'Didn’t land', failed: 'Didn’t land',
    'cut off': 'Stopped before finishing', cancelled: 'Cancelled', finished: 'Done', 'deploy failed': 'Landed, not live',
    'pull request': 'Ready for review', interrupted: 'Interrupted' }
  for (const [outcome, text] of Object.entries(words)) assert.equal(jobStatus(job({ state: 'finished', outcome })), text)
  assert.equal(jobStatus(job({ state: 'queued', held_by: 'Waiting for another change to land' })), 'Waiting for another change to land')
  assert.equal(jobStatus(job({ state: 'landing', phase: 'ci' })), 'Checking the changes')
  assert.equal(jobStatus(job({ state: 'landing', phase: 'deploy' })), 'Making it live')
  assert.equal(jobElapsed(job(), clock), '2 min')
  assert.equal(jobElapsed(job(), clock + 60000), '3 min')
  assert.equal(jobElapsed(job({ state: 'queued', started: null }), clock), null)
  assert.equal(jobElapsed(job({ state: 'finished', finished: new Date(clock).toISOString() }), clock + 60000), '2 min')
  assert.equal(jobElapsed(job({ state: 'finished', elapsed_seconds: null, finished: null }), clock), null)
})
test('completed-only jobs keep Tasks available without an active badge, open their receipt and expire', (t) => {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'], now: clock })
  const refs = [], { threads, view } = setup(t, { openRef: ref => refs.push(ref) }), o = threads.st.open
  o.thread.members = []; o.typing.clear(); o.tasksOpen = true
  o.thread.jobs = [job({ state: 'finished', outcome: 'deployed', finished: new Date(clock).toISOString(), receipt: 'Projects/receipts/change.md' })]
  let tree = view()
  assert.equal(find(tree, 'tt-task-count'), null)
  assert.ok(find(tree, 'tt-tasks-button'))
  assert.match(text(find(tree, 'tt-tasks-panel')), /Keep the work in view.*Example project.*Landed and live.*2 min/)
  find(tree, 'tt-job-row').fire('click')
  assert.deepEqual(refs, ['Projects/receipts/change.md'])
  assert.equal(o.tasksOpen, false)
  o.tasksOpen = true
  t.mock.timers.tick(600000)
  tree = view()
  assert.match(text(find(tree, 'tt-tasks-panel')), /Nothing running/)
  t.mock.timers.tick(1400)
  assert.equal(o.tasksOpen, false)
})
test('job read uses seen=0, ignores unrelated reply data, survives member changes and does not overlap', async (t) => {
  t.mock.timers.enable({ apis: ['Date'], now: clock })
  let resolve, requests = []
  const { threads, view } = setup(t, { api: (method, route) => {
    requests.push([method, route]); return new Promise(r => { resolve = r })
  } }), o = threads.st.open
  // A live list avoids an unrelated list load in this isolated heartbeat.
  threads.st.listLive = true
  threads.tick(true); threads.tick(true)
  assert.deepEqual(requests.filter(([, route]) => route.includes('seen=0')), [['GET', '/threads/one?limit=1&seen=0']])
  resolve({ thread: { id: 'one', jobs: [job()], jobs_error: null }, messages: [{ id: 'unrelated' }] })
  await Promise.resolve(); await Promise.resolve()
  o.thread = { ...o.thread, members: [] }; o.typing.clear()
  o.tasksOpen = true
  assert.equal(text(find(view(), 'tt-task-count')), '1')
  assert.equal(o.messages.some(m => m.id === 'unrelated'), false)
  t.mock.timers.tick(5000); threads.tick(true)
  resolve({ thread: { id: 'one', jobs: [], jobs_error: 'temporarily unavailable' } })
  await Promise.resolve(); await Promise.resolve()
  assert.equal(find(view(), 'tt-job-row'), null)
})
