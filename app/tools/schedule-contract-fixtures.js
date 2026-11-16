// Shapes and values transcribed from the supplied broker responses. Agent ids
// and timer names are parameters so the fixtures describe no installation.
export const TS = '2026-10-15T01:08:50Z'
export function change(agent, record, overrides = {}) {
  return { id: `change-${agent}`, ts: TS, agent, record, op: 'move', actor: agent,
    lane: 'wake', reason: 'the market sweep reads better after the digest', kind: 'change',
    diff: { header: { schedule: ['Sundays at 05:00 PT', 'Sundays at 06:00 PT'] }, body_changed: false, created: false, deleted: false },
    seen: false, ...overrides }
}
export function schedule(agent, overrides = {}) {
  const row = { agent, name: 'weekly-review', kind: 'recurring', schedule: 'Sundays at 06:00 PT',
    words: 'Sundays at 06:00 PT', note: '', enabled: true, done: '',
    body: 'Write the morning digest.', instruction: null,
    timer: `loam-cron-${agent}-weekly-review.timer`, last_fire: null,
    last_change: null, unseen: [], refused: null, error: null,
    next_fire: '2026-10-21T12:00:00Z', ...overrides }
  return { state: row.enabled ? 'live' : 'off', running: row.enabled ? 'this' : 'none',
    state_words: row.enabled ? 'Live.' : 'Off: the file says it is not enabled.', problem: null,
    last_edit: row.last_change ? { id: row.last_change.id, ts: row.last_change.ts, actor: row.last_change.actor,
      by: row.last_change.actor === 'owner' ? 'owner' : row.last_change.actor === 'engineer' ? 'operator' : 'agent', lane: row.last_change.lane, taken: true } : null,
    changed_since_seen: row.unseen.length > 0, file: { root: 'spaces', path: `${agent}/crons/${row.name}.md` }, ...row }
}
export function refusal(agent, record = 'reflection', overrides = {}) {
  return { id: `refused-${agent}`, ts: TS, agent, record, actor: agent, lane: 'cron', reason: null,
    why: 'the nightly reflection\'s record is not deleted; pause it with `enabled: false`',
    kind: 'refused', direct: false, diff: { header: { enabled: ['true', null], lane: ['reflection', null], schedule: ['daily at 03:20 PT', null] }, body_changed: true, created: false, deleted: true },
    seen: false, ...overrides }
}
export const grammar = (value) => `cannot parse schedule '${value}'. The grammar is:\n\n  <when> at <HH:MM> PT\n\n  <when> is one of:\n    daily\n    weekdays                     Monday through Friday\n    weekends                     Saturday and Sunday\n    <Day>s                       e.g. Sundays\n    <Day>s and <Day>s            e.g. Mondays and Thursdays\n\n  The time is 24-hour, zero-padded, and PT is the only zone accepted.`
