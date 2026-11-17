// Views over thread records for the shared waiting and speech UI.
import { formatTime, formatDate, formatDateTime, calendarDay } from './time.js'
import { awaitingPhrase } from './awaiting.js'
import { threadTurns, messageSegments } from './thread-record.js'
import { modelLabel } from './providers.js'

export function replyLabels(message, providers = []) {
  const provider = providers.find((p) => p.id === message.engine)
  return { provider: provider?.name ?? message.engine ?? null,
    model: modelLabel(provider, message.model).fullName }
}

// The same short label as the composer; the recorded ID still owns run identity.
export function replyChip(message, providers = []) {
  if (message.preset === true) return { provider: null, model: null, fullModel: null }
  const labels = replyLabels(message, providers)
  // Claude Code names its own error replies' model "<synthetic>" (a login that
  // could not be refreshed, a refused request); it is not a model a person
  // chose, and the chip drew it raw (the first-day walk, 2026-11-16).
  const named = /^<.*>$/.test(message.model ?? '') ? null : message.model
  const model = modelLabel(providers.find((p) => p.id === message.engine), named)
  return { ...labels, model: model.name, fullModel: model.fullName }
}

// Aliases remain saved choices. The composer reads today's target; history
// belongs to each reply. Only older brokers need the latest-reply fallback.
export function composerModel(member, messages, providers = []) {
  const provider = providers.find((p) => p.id === member.engine)
  const label = modelLabel(provider, member.model).name ?? 'Agent’s default'
  const alias = member.engine === 'claude-code' && /^[a-z]+(?:\[1m\])?$/i.test(member.model ?? '')
  const entry = provider?.models?.find((model) => model?.id === member.model)
  if (alias && entry && Object.hasOwn(entry, 'resolves_to')) {
    const resolved = modelLabel(provider, entry.resolves_to).name
    return { name: resolved ?? label, title: `Agent's choice: ${label}${resolved ? ` (now ${resolved})` : ''}` }
  }
  const latest = messages.findLast((m) => m.author === member.persona && !m.streaming && m.kind !== 'system' && replyHasContent(m))
  const used = alias && latest?.engine === member.engine && typeof latest.model === 'string' && latest.model.startsWith(`claude-${member.model}-`) ? latest.model : null
  return { name: used ? modelLabel(provider, used).name : label,
    title: alias ? `Agent's choice: ${label}${used ? ` (latest reply used ${used})` : ''}` : null }
}

// Calendar days, in the reader's time zone, rather than elapsed 24-hour blocks.
// Inject the clock/zone for boundary tests; the page uses the browser's zone.
export function replyTimestamp(value, { now = Date.now(), timeZone, locale } = {}) {
  if (typeof value !== 'string' || !value.trim()) return null
  const date = new Date(value), current = new Date(now)
  if (!Number.isFinite(+date) || !Number.isFinite(+current)) return null
  const context = { locale, timeZone }
  const sent = calendarDay(date, context), today = calendarDay(current, context), ago = today.number - sent.number
  const time = formatTime(date, context)
  const text = ago === 0 ? time : ago > 0 && ago < 7 ? `${formatDate(date, { weekday: 'short' }, context)} ${time}`
    : `${formatDate(date, { month: 'short', day: 'numeric', ...(sent.year !== today.year ? { year: 'numeric' } : {}) }, context)}, ${time}`
  return { text, title: formatDateTime(date, { dateStyle: 'full', timeStyle: 'long' }, context), dateTime: date.toISOString() }
}

export function mentionChoices(agents, query = '') {
  return [{ id: 'all', name: 'Everyone', sub: 'everyone in this thread', everyone: true }, ...agents]
    .filter((a) => `${a.name} ${a.id}${a.everyone ? ' everyone' : ''}`.toLowerCase().includes(query.toLowerCase()))
}

export function backgroundCount(record) {
  return Number.isInteger(record?.background) && record.background > 0 ? record.background : 0
}
// The member count includes the brief gap before a completion notification.
// Named tasks may be fewer; never invent a task or its launching reply.
export function threadBackground(thread, now = Date.now()) {
  const members = thread.members ?? []
  const jobs = thread.jobs_error ? [] : (thread.jobs ?? []).filter((job) => ['running', 'queued', 'landing'].includes(job.state)
    || (job.state === 'finished' && now - Date.parse(job.finished) < 10 * 60000))
  return { count: members.reduce((n, member) => n + backgroundCount(member), 0) + jobs.filter((job) => job.state !== 'finished').length,
    tasks: [...members.flatMap((member) => (member.background_tasks ?? []).map((task) => ({ ...task, persona: member.persona }))),
      ...jobs.map((job) => ({ ...job, id: job.job, persona: job.briefed_by, kind: 'job' }))] }
}

export function jobStatus(job) {
  if (job.state === 'queued') return job.held_by || 'Waiting to start'
  if (job.state === 'running') return 'Running'
  if (job.state === 'landing') return ({ ci: 'Checking the changes', deploy: 'Making it live' })[job.phase] || 'Landing the changes'
  return ({ deployed: 'Landed and live', landed: 'Landed', 'not landed': 'Didn’t land', failed: 'Didn’t land',
    'cut off': 'Stopped before finishing', cancelled: 'Cancelled', finished: 'Done', 'deploy failed': 'Landed, not live',
    'pull request': 'Ready for review', interrupted: 'Interrupted' })[job.outcome] || 'Done'
}

export function jobElapsed(job, now = Date.now()) {
  if (job.state === 'queued') return null
  // Completed durations never keep ticking. Prefer the host's duration; the
  // timestamps also let a running row advance between host readings.
  if (job.state === 'finished') return Number.isFinite(job.elapsed_seconds)
    ? taskElapsed(new Date(now - Math.max(0, job.elapsed_seconds) * 1000).toISOString(), now)
    : taskElapsed(job.started, Date.parse(job.finished))
  return taskElapsed(job.started, now)
}
export function backgroundWait(member) {
  const n = backgroundCount(member)
  return n ? `waiting on ${n} background ${n === 1 ? 'task' : 'tasks'}` : null
}

export function memberWaiting(open, persona) {
  const member = open.thread.members.find((m) => m.persona === persona)
  const turn = threadTurns(open).find((t) => t.persona === persona)
  return Boolean(backgroundCount(member) && (member.working === false || (member.working == null && turn?.on_background)))
}

export function memberGenerating(open, persona) {
  const member = open.thread.members.find((m) => m.persona === persona)
  if (typeof member?.working === 'boolean') return member.working
  return !memberWaiting(open, persona) && (open.typing.has(persona) || threadTurns(open).some((t) => t.persona === persona))
}

export function taskTitle(description) {
  return String(description ?? '').trim().replace(/^↳\s*/, '').replace(/^Brief\s+[^:\n]+:\s*/i, '').trim() || 'background work'
}

// A recorded tool/wait label is not prose. This applies to completed records
// as well as live drafts, so a parked turn cannot leave an empty author chip.
export function replyHasContent(message) {
  return Boolean(message.attachments?.length || messageSegments(message).some((part) => part.text?.trim()))
}

export function showReply(open, message) {
  if (replyHasContent(message)) return true
  if ([...open.permissions.values()].some((p) => p.message_id === message.id)) return true
  return Boolean(message.streaming && memberGenerating(open, message.author))
}

export function threadWaiting(open, persona, name, elapsed) {
  const member = open.thread.members.find((m) => m.persona === persona)
  const turn = threadTurns(open).find((t) => t.persona === persona)
  if ((!open.typing.has(persona) && !member?.working && !backgroundCount(member))
    || (member?.working === false && !backgroundCount(member) && !open.waiting?.has(persona))
    || [...open.permissions.values()].some((p) => p.persona === persona)) return null
  const waiting = open.waiting?.get(persona)
  if ((member?.working === false || (member?.working == null && turn?.on_background)) && backgroundCount(member)) return { text: `${name} is ${backgroundWait(member)}`, out: false, nextMs: null }
  if (waiting || open.steps?.get(persona)?.waiting) return { text: waiting || `${name} is waiting on a background task`, out: false, nextMs: null }
  // Only the current text is streaming. Earlier text preserved before a tool
  // belongs to history; the gap after that tool needs its waiting line again.
  if (open.messages.some((m) => m.streaming && m.author === persona && m.text)) return { text: `${name} is replying…`, out: false, nextMs: null }
  const { phrase, ...clock } = awaitingPhrase(elapsed)
  return { text: `${name} · ${phrase}…`, ...clock }
}

// Unknown task start times stay unknown; the count is always the broker's,
// including the gap between a task leaving the list and its notification.
export function taskElapsed(startedAt, now = Date.now()) {
  const start = typeof startedAt === 'string' ? Date.parse(startedAt) : NaN
  if (!Number.isFinite(start) || !Number.isFinite(now)) return null
  const minutes = Math.floor(Math.max(0, now - start) / 60000)
  return minutes < 1 ? '<1 min' : minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} hr${minutes % 60 ? ` ${minutes % 60} min` : ''}`
}
export function liveThreadMembers(open) {
  return [...new Set([...threadTurns(open).map((t) => t.persona), ...open.typing,
    ...open.thread.members.filter((m) => m.working || backgroundCount(m)).map((m) => m.persona)])]
    .filter((id) => threadWaiting(open, id, id, 0))
}
