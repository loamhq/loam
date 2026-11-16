import { isOwner } from './owner.js'
// Host-owned records; see docs/broker-schedules-and-activity-api.md.
import { formatDate, formatTime } from './time.js'
import { segmentedControl } from './chat-ui.js'
import { h } from './ui.js'

const encode = encodeURIComponent
const pathOf = (agent, name) => `/schedules/${encode(agent)}${name === undefined ? '' : `/${encode(name)}`}`
const listed = (body) => {
  if (!Array.isArray(body?.schedules)) throw new Error('The broker returned an unreadable schedule list.')
  return body.schedules
}
export function dateWords(value) {
  if (!value || !Number.isFinite(Date.parse(value))) return 'Time unknown'
  return `${formatDate(value, { day: 'numeric', month: 'short' })}, ${formatTime(value)}`
}
export const cadence = (row) => row.words || row.schedule || ''
export const unseen = (row) => row.changed_since_seen === true
export const runReason = (row) => row.enabled === false ? 'Off · turn it on to run now.' : row.kind === 'one-off' && row.done ? 'This one-off has already run.' : null
export function stateWords(row) {
  if (row.state === 'live') return 'Live'
  if (row.state === 'off') return 'Off'
  if (['broken', 'refused', 'pending'].includes(row.state)) {
    const state = row.state[0].toUpperCase() + row.state.slice(1)
    const running = { previous: 'previous version still runs', none: 'nothing runs', this: 'this version runs' }[row.running]
    return [state, row.problem?.line ? `line ${row.problem.line}` : null, running].filter(Boolean).join(' · ')
  }
  return row.state_words
}
export function editWords(edit, now = Date.now()) {
  if (!edit?.ts) return null
  const elapsed = Math.max(0, now - Date.parse(edit.ts))
  if (!Number.isFinite(elapsed)) return null
  const time = elapsed < 60000 ? 'just now' : elapsed < 3600000 ? `${Math.floor(elapsed / 60000)}m ago` : elapsed < 86400000 ? `${Math.floor(elapsed / 3600000)}h ago` : `${Math.floor(elapsed / 86400000)}d ago`
  const who = isOwner(edit.by) ? 'you' : { agent: 'the agent', operator: 'an operator', runtime: 'the runtime' }[edit.by]
  return who ? `Edited ${time} by ${who}` : `Edited ${time}`
}

export function createSchedules({ api, redraw, openFile = () => {} }) {
  const st = { agent: null, rows: [], loading: false, error: null, editor: null, generation: 0, refreshing: false, lastFetch: 0 }
  let read = 0
  const current = (agent, generation) => st.agent === agent && st.generation === generation
  async function refresh(agent, generation) {
    const ticket = ++read
    const body = await api('GET', `/schedules?agent=${encode(agent)}`)
    if (!current(agent, generation) || ticket !== read) return
    // Notices and in-flight controls survive a poll; record fields are the host's.
    st.rows = listed(body).map((row) => {
      const prior = st.rows.find((r) => r.name === row.name)
      const sameEdit = prior?.last_edit?.id === row.last_edit?.id && prior?.state === row.state
      return { ...row, busy: prior?.busy, uiError: sameEdit ? prior?.uiError : null, uiMessage: sameEdit ? prior?.uiMessage : null }
    })
    st.lastFetch = Date.now()
  }
  async function mark(row) {
    await api('POST', `${pathOf(row.agent, row.name)}/seen`, {})
  }
  async function load(agent, { seen = false } = {}) {
    const generation = ++st.generation
    st.rows = st.agent === agent ? st.rows.map((row) => ({ ...row, busy: false, uiError: null, uiMessage: null })) : []
    st.agent = agent
    st.editor = null
    st.loading = true
    st.error = null
    redraw()
    try {
      await refresh(agent, generation)
      if (seen && current(agent, generation)) {
        await Promise.all(st.rows.filter(unseen).map(mark))
        await refresh(agent, generation)
      }
    } catch (error) {
      if (current(agent, generation)) st.error = error.status === 405 ? 'Schedules are not available from this host yet.' : error.message
    } finally {
      if (current(agent, generation)) { st.loading = false; st.lastFetch = Date.now(); redraw() }
    }
  }
  function open(row) {
    if (!row.file) { row.uiError = 'This host did not provide the schedule’s file.'; redraw(); return }
    // Files marks schedule files seen once it has read them, including direct URLs.
    return openFile(row.file)
  }
  async function seeFile(row) {
    if (row?.authority !== 'schedule' || row.root !== 'spaces') return
    const match = /^([^/]+)\/crons\/([^/]+)\.md$/.exec(row.path)
    if (!match) return
    await mark({ agent: match[1], name: match[2] })
    if (st.agent === match[1]) await refresh(st.agent, st.generation)
    redraw()
  }
  function add() {
    if (st.editor?.busy) return
    st.editor = { name: null, draft: { name: '', kind: 'recurring', schedule: 'daily at 09:00 PT', at: '', body: '', note: '' }, error: null }
    redraw()
  }
  async function act(item, method, path, body, message, close = false) {
    if (item.busy) return
    const agent = st.agent, generation = st.generation
    const errorKey = item === st.editor ? 'error' : 'uiError'
    const messageKey = item === st.editor ? 'message' : 'uiMessage'
    item.busy = true
    item[errorKey] = null
    item[messageKey] = null
    redraw()
    try {
      const answer = await api(method, path, body)
      item[messageKey] = message
      if (close && current(agent, generation) && st.editor === item) st.editor = null
      if (answer?.schedule && current(agent, generation)) {
        st.rows = st.rows.map((row) => row.name === answer.schedule.name ? { ...answer.schedule, busy: true, uiMessage: message } : row)
      }
      await refresh(agent, generation)
    } catch (error) {
      item[errorKey] = error.message
      if (close && st.editor !== item && current(agent, generation)) st.error = error.message
    } finally {
      item.busy = false
      // A refresh replaces row objects while a control is in flight.
      if (current(agent, generation) && item.name) {
        const row = st.rows.find((r) => r.name === item.name)
        if (row) Object.assign(row, { busy: false, uiError: item[errorKey], uiMessage: item[messageKey] })
      }
      redraw()
    }
  }
  function save(editor) {
    if (editor.busy) return
    const d = editor.draft
    const body = d.kind === 'one-off' ? { at: d.at, note: d.note } : { schedule: d.schedule }
    Object.assign(body, { body: d.body, name: d.name.trim() })
    return act(editor, 'POST', pathOf(st.agent), body, 'Saved.', true)
  }
  function toggle(row) {
    return act(row, 'PATCH', pathOf(st.agent, row.name), { enabled: !row.enabled }, null)
  }
  async function tick(active) {
    if (!active || !st.agent || st.loading || st.refreshing || st.editor?.busy || st.rows.some((r) => r.busy) || Date.now() - st.lastFetch < 5000) return
    const agent = st.agent, generation = st.generation
    st.refreshing = true
    try { await refresh(agent, generation); if (current(agent, generation)) st.error = null }
    catch (error) { if (current(agent, generation)) st.error = error.message }
    finally { st.refreshing = false; if (current(agent, generation)) st.lastFetch = Date.now(); redraw() }
  }
  return { st, load, open, seeFile, add, save, toggle, act, tick, redraw, path: (name) => pathOf(st.agent, name),
    hasUnseen: () => st.rows.some(unseen), close: () => { if (!st.editor?.busy) { st.editor = null; redraw() } } }
}
function notice(item) {
  return [item.error ? h('p', { key: 'error', class: 'schedule-error', role: 'alert', text: item.error }) : null,
    item.message ? h('p', { key: 'message', class: 'schedule-message', role: 'status', text: item.message }) : null]
}
export function schedulePanel(controller, agentName) {
  const { st } = controller
  const rows = [...st.rows].sort((a, b) => Number(b.kind === 'reflection') - Number(a.kind === 'reflection'))
  return h('section', { class: 'schedules', 'aria-label': `${agentName} schedules`, 'aria-busy': String(st.loading) },
    st.loading ? h('p', { class: 'muted', role: 'status', text: 'Loading schedules…' }) : null,
    st.error ? h('div', {}, h('p', { class: 'schedule-error', role: 'alert', text: st.error }), h('button', { class: 'pillbtn schedule-button content-mark', text: 'Try again', onclick: () => controller.load(st.agent, { seen: true }) })) : null,
    h('div', { class: 'schedule-card' }, ...rows.map((row) => {
      const reason = runReason(row)
      return h('article', { class: `schedule-item ${row.state || ''}`, key: row.name },
        h('div', { class: 'schedule-top' },
          h('button', { class: 'schedule-open', onclick: () => controller.open(row), title: 'Open in Files' },
            h('span', { class: 'schedule-heading' }, h('span', { class: 'schedule-title', text: row.name }),
              unseen(row) ? h('span', { class: 'schedule-dot', 'aria-label': 'Changed since seen' }) : null,
              cadence(row) ? h('span', { class: 'schedule-cadence', text: cadence(row) }) : null),
            h('span', { class: 'schedule-meta', text: [row.next_fire ? `Next ${dateWords(row.next_fire)}` : 'No next run', editWords(row.last_edit)].filter(Boolean).join(' · ') }),
            h('span', { class: 'schedule-state', text: stateWords(row), title: row.state_words, 'aria-label': row.state_words })),
          h('button', { class: 'schedule-run content-mark', text: 'Run now', title: reason || 'Run now', 'aria-label': `Run ${row.name} now`, disabled: !!reason, 'aria-disabled': String(!!row.busy), onclick: () => controller.act(row, 'POST', `${controller.path(row.name)}/run`, {}, 'Run requested.') }),
          h('button', { class: 'schedule-switch', role: 'switch', 'aria-checked': row.enabled === null ? 'mixed' : String(row.enabled), 'aria-label': `${row.name} on`, 'aria-disabled': String(!!row.busy), onclick: () => controller.toggle(row) }, h('span'))),
        ...notice({ error: row.uiError, message: row.uiMessage }))
    })),
    !st.loading && !st.error && !rows.length ? h('p', { class: 'schedule-empty muted', text: 'No schedules yet. Schedule a task to get started.' }) : null,
    st.editor ? scheduleEditor(controller, st.editor) : h('button', { class: 'schedule-add content-mark', text: '+ Schedule a task', onclick: controller.add }))
}
function scheduleEditor(controller, editor) {
  const { draft: d } = editor
  const id = 'new'
  const field = (label, key, type = 'text', extra = {}) => h('div', { class: 'schedule-field', key }, h('label', { for: `schedule-${id}-${key}`, text: label }),
    h(type === 'textarea' ? 'textarea' : 'input', { key: `${key}:${editor.revision || 0}`, id: `schedule-${id}-${key}`, name: key, ...(type !== 'textarea' ? { type } : {}), value: d[key], required: key !== 'note', oninput: (e) => { d[key] = e.target.value }, ...extra }, type === 'textarea' ? d[key] : null))
  return h('div', { class: 'schedule-editor', id: `schedule-${id}`, key: `editor-${id}` },
    h('div', { class: 'schedule-editor-head', key: 'head' }, h('h3', { text: 'Schedule a task' }), h('button', { class: 'pillbtn schedule-button', type: 'button', text: 'Close', 'aria-disabled': String(!!editor.busy), onclick: controller.close })),
    h('form', { class: 'schedule-form', key: 'form', onsubmit: (e) => { e.preventDefault(); controller.save(editor) } },
      h('fieldset', { key: 'fields', disabled: !!editor.busy || !!editor.loading },
        field('Task name', 'name', 'text', { pattern: '[a-z0-9-]+', maxlength: 120 }),
        segmentedControl({ label: 'Schedule type', value: d.kind, options: [{ id: 'recurring', label: 'Recurring' }, { id: 'one-off', label: 'One-off' }], choose: kind => { d.kind = kind; editor.error = null; editor.message = null; controller.redraw() }, props: { class: 'schedule-kinds' } }),
        d.kind === 'one-off' ? field('At (PT)', 'at', 'text', { placeholder: '2026-10-16 14:30 PT' }) : field('Schedule', 'schedule', 'text', { placeholder: 'weekdays at 06:30 PT' }),
        h('p', { class: 'schedule-zone', text: d.kind === 'one-off' ? 'YYYY-MM-DD HH:MM PT · 24-hour time. PT only.' : 'PT only · daily, weekdays, weekends, or days such as Mondays and Thursdays at 06:30 PT.' }),
        field('Instruction', 'body', 'textarea', { required: false }),
        d.kind === 'one-off' ? field('Note', 'note', 'textarea') : null),
      h('div', { class: 'schedule-actions', key: 'save' }, h('button', { type: 'submit', class: 'pillbtn primary schedule-button', 'aria-disabled': String(!!editor.busy || !!editor.loading), text: editor.busy ? 'Saving…' : 'Save schedule' }))),
    ...notice(editor))
}
