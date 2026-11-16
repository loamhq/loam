import { isOwner } from './owner.js'
// Both pages render the broker's header diff without reconstructing records.
import { h } from './ui.js'

export function actorName(id, agents = []) {
  return (isOwner(id) ? 'you' : { engineer: 'the engineer', loam: 'Loam' }[id])
    ?? agents.find((agent) => agent.id === id)?.name
    ?? (id ? id[0].toUpperCase() + id.slice(1) : 'Unknown actor')
}

export function scheduleChange(change, { undo = null, busy = false, error = '',
  disabled = false, buttonClass = 'pillbtn schedule-undo' } = {}) {
  return h('div', { class: 'schedule-change' },
    ...Object.entries(change.diff?.header ?? {}).map(([key, [before, after]]) =>
      h('div', { class: 'schedule-difference' },
        h('strong', { text: `${key}: ` }),
        h('span', { class: 'schedule-before', text: before ?? 'Not set' }),
        h('span', { 'aria-label': 'changed to', text: ' → ' }),
        h('span', { text: after ?? 'Not set' }))),
    change.diff?.body_changed ? h('p', { class: 'muted', text: change.kind === 'refused' ? 'Instruction change refused.' : 'Instruction changed.' }) : null,
    change.diff?.created ? h('p', { class: 'muted', text: change.kind === 'refused' ? 'Creation refused.' : 'Schedule created.' }) : null,
    change.diff?.deleted ? h('p', { class: 'muted', text: change.kind === 'refused' ? 'Deletion refused.' : 'Schedule deleted.' }) : null,
    change.reason ? h('p', { class: 'schedule-reason', text: `“${change.reason}”` }) : null,
    undo ? h('button', { class: buttonClass, disabled, 'aria-disabled': String(busy || disabled),
      onclick: () => { if (!busy && !disabled) undo() }, text: busy ? 'Undoing…' : 'Undo' }) : null,
    error ? h('p', { class: 'activity-error', role: 'alert', text: error }) : null)
}
