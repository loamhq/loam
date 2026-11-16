import test from 'node:test'
import assert from 'node:assert/strict'
import { appAgainst, closing, until } from './harness.js'
import { browser, all, find, text } from './browser.js'
import { schedulesHost } from './schedules-host.js'

const broker = await schedulesHost({ fileFirst: true })
const app = await appAgainst(broker)
const page = browser(app.url, { innerWidth: 390 })
page.watch()
await import('../public/app.js')
page.unwatch()
const root = page.root
const click = (node) => node.fire('click', { preventDefault() {}, stopPropagation() {} })
const button = (node, name) => allTags(node, 'BUTTON').find((n) => text(n) === name)
function allTags(node, tag) { return [...(node.nodeName === tag ? [node] : []), ...(node.childNodes || []).flatMap((n) => allTags(n, tag))] }
function field(name) { return [...allTags(root, 'INPUT'), ...allTags(root, 'TEXTAREA')].find((n) => n.getAttribute('name') === name) }
function fill(name, value) { const node = field(name); node.value = value; node.fire('input', { target: node }) }
test.after(async () => {
  page.stop()
  for (const stream of broker.streams) stream.end()
  await closing(broker, app)()
  setImmediate(() => process.exit(process.exitCode ?? 0))
})
test('Manage schedules preserves the full file list, actor edits and host seen marks', async () => {
  click(await until(() => find(root, 'roundbtn')))
  click(await until(() => all(root, 'setrow').find((r) => text(find(r, 'setrow-title')) === 'Agents')))
  click(await until(() => all(root, 'setrow').find((r) => text(find(r, 'setrow-title')) === 'Scout')))
  click(await until(() => button(root, 'Schedules')))
  click(await until(() => button(root, 'Manage schedules')))
  await until(() => all(root, 'schedule-item').length === 4 && !find(root, 'schedule-dot'))
  assert.equal(text(all(root, 'schedule-title')[0]), 'reflection')
  assert.match(text(root), /previous version still runs/)
  assert.match(text(root), /Edited .* by an operator/)
  assert.match(text(root), /Edited .* by you/)
  assert.match(text(root), /Edited .* by the agent/)
  assert.equal(find(root, 'schedule-editor'), null)
  assert.equal(button(root, 'Delete schedule'), undefined)
  assert.equal([...broker.fake.rows.values()].some((r) => r.changed_since_seen), false)
})
test('broken switch shows broker refusal and keeps its enabled state', async () => {
  const broken = all(root, 'schedule-item').find((n) => text(n).includes('morning-digest'))
  click(find(broken, 'schedule-switch'))
  await until(() => find(broken, 'schedule-error'))
  assert.match(text(find(broken, 'schedule-error')), /cannot parse schedule 'weekdays at 7am'/)
  assert.equal(find(broken, 'schedule-switch').getAttribute('aria-checked'), 'true')
})
test('creation remains a form and posts a one-off through the existing route', async () => {
  click(button(root, '+ Schedule a task'))
  await until(() => field('name'))
  click(button(root, 'One-off'))
  await until(() => field('at'))
  fill('name', 'one-more-meeting'); fill('at', '2026-10-21 10:45 PT')
  fill('body', 'Read the agenda.'); fill('note', 'Include the open questions.')
  find(root, 'schedule-form').fire('submit', { preventDefault() {} })
  await until(() => all(root, 'schedule-item').length === 5)
  assert.equal(find(root, 'schedule-editor'), null)
  assert.equal(broker.fake.rows.get('scout/one-more-meeting').note, 'Include the open questions.')
})
test('tapping a row opens the existing writable Files editor at the broker path and marks seen', async () => {
  const row = broker.fake.rows.get('scout/morning-digest')
  const event = { id: 'new-edit', agent: row.agent, record: row.name, kind: 'refused', seen: false }
  broker.fake.activities.set(event.id, event)
  click(all(root, 'schedule-open').find((n) => text(n).includes('morning-digest')))
  await until(() => find(root, 'fs-file') && !text(root).includes('Opening…'))
  assert.equal(text(find(root, 'fs-edtitle')), 'morning-digest')
  assert.ok(button(root, 'Edit'))
  assert.equal(broker.fake.activities.get(event.id).seen, true)
  click(button(root, 'Edit'))
  await until(() => find(root, 'fs-field'))
  const editor = find(root, 'fs-field')
  editor.value = broker.files.goodScheduleText
  editor.fire('input', { target: editor })
  editor.fire('blur')
  await until(() => find(root, 'fs-schedule-save'))
  assert.match(text(find(root, 'fs-schedule-save')), /Saved · runs weekdays at 07:00 PT/)
  editor.value = broker.files.brokenScheduleText
  editor.fire('input', { target: editor }); editor.fire('blur')
  await until(() => text(find(root, 'fs-schedule-save'))?.includes('Line 2'))
  assert.match(text(find(root, 'fs-schedule-save')), /previous version still runs/)
  assert.match(text(find(root, 'fs-schedule-save')), /Line 2 · schedule: weekdays at 7am/)
  assert.equal(find(root, 'banner-retry'), null)
})
