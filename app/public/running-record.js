// Residency belongs to the runtime. Never infer a Loam thread id from a CLI's
// thread_id: those are different namespaces. Older brokers provide no mapping.
export function liveSession(record) {
  if (!record || !(record.live ?? record.state === 'running')) return null
  const waiting = record.pending_permissions?.length > 0 || record.status === 'needs_input'
  const state = waiting ? 'waiting' : record.background_tasks > 0 || record.status === 'waiting' ? 'background'
    : record.status === 'working' || !['done', 'idle', 'failed'].includes(record.status) ? 'working' : 'idle'
  return { id: record.session_id, agent: record.agent, engine: record.provider, model: record.model,
    kind: 'classic', title: record.title || 'New chat', state, startedAt: record.created,
    lastActive: record.last_event_at || record.last_activity, idleSince: null, memoryMb: null, mapped: !record.archived,
    record }
}
export function groupSessions(rows) {
  return [
    { title: 'Working', rows: rows.filter((r) => ['working', 'background'].includes(r.state)) },
    { title: 'Waiting on you', rows: rows.filter((r) => r.state === 'waiting') },
    { title: 'Idle', rows: rows.filter((r) => r.state === 'idle') },
  ].filter((g) => g.rows.length)
}
export function runningAge(at, now = Date.now()) {
  const elapsed = now - Date.parse(at)
  if (!Number.isFinite(elapsed)) return ''
  const minutes = Math.max(0, Math.floor(elapsed / 60000))
  return minutes < 1 ? 'just now' : minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} hr`
}
export function sessionLine(row, now = Date.now()) {
  if (row.state === 'waiting') return 'Waiting on your answer'
  if (row.state === 'background') return 'Waiting on background work'
  const age = runningAge(row.state === 'idle' ? row.idleSince || row.lastActive : row.lastActive || row.startedAt, now)
  return `${row.state === 'idle' ? 'Idle' : 'Working'}${age ? ` · ${age}` : ''}${row.state === 'idle' && row.idleTimeoutSeconds ? `, steps out at ${Math.round(row.idleTimeoutSeconds / 60)} min` : ''}`
}

// GET /v1/sessions/live and sessions.live carry this explicit mapping. These
// thread ids belong to Loam, unlike thread_id on an ordinary CLI record.
export function readLiveSession(record) {
  if (!record || !record.id || !['thread', 'classic', 'conversation'].includes(record.kind)) return null
  return { id: record.id, agent: record.agent, engine: record.engine, model: record.model,
    kind: record.kind === 'conversation' ? 'classic' : record.kind, runKind: record.run_kind, engineLabel: record.engine_label, threadId: record.kind === 'thread' ? record.thread_id : null,
    title: (record.kind === 'thread' ? record.thread_title : record.title) || (record.kind === 'thread' ? 'New thread' : 'New chat'),
    state: record.state === 'asking' ? 'waiting' : ['idle', 'working', 'background'].includes(record.state) ? record.state : 'working',
    startedAt: record.started_at, lastActive: record.last_active, idleSince: record.idle_since,
    memoryMb: Number.isFinite(record.rss_mb) ? record.rss_mb : null,
    losesWork: record.ending?.loses_work, endingText: record.ending?.text,
    mapped: record.kind === 'classic' || typeof record.thread_id === 'string', record }
}
export function readBox(box) {
  if (!box) return null
  const unit = (kind) => ({ count: box.units?.[kind]?.count, memoryMb: box.units?.[kind]?.mb })
  return { usedMb: box.ram_used_mb, totalMb: box.ram_total_mb, room: box.can_start,
    jobs: unit('engineer_jobs'), wakes: unit('wakes') }
}
