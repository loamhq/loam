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
  // Hide the provider’s synthetic error model from the model chip.
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

// Use host activity timestamps to distinguish quiet jobs from active work.
export function jobActivity(job, now = Date.now()) {
  if (job.state !== 'running') return null
  if (job.quiet_since) {
    const quiet = taskElapsed(job.quiet_since, now)
    if (quiet !== null) return { stuck: true, text: `No activity for ${quiet}` }
  }
  const last = taskElapsed(job.last_activity, now)
  return last === null ? null : { stuck: false, text: last === '<1 min' ? 'active just now' : `active ${last} ago` }
}

export function jobStatus(job, now = Date.now()) {
  if (job.state === 'queued') return job.held_by || 'Waiting to start'
  if (job.state === 'running') {
    const activity = jobActivity(job, now)
    return !activity ? 'Running' : activity.stuck ? activity.text : `Running · ${activity.text}`
  }
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
  if ([...open.permissions.values()].some((p) => p.persona === message.author)) return false
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

// "2nd draft", "3rd draft": a draft's revision as a person counts it. The
// first draft is just the draft, so it has no label.
export function ordinalDraft(rev) {
  const n = Number(rev)
  if (!Number.isInteger(n) || n <= 1) return ''
  const mod100 = n % 100
  const suffix = mod100 >= 11 && mod100 <= 13 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] ?? 'th')
  return `${n}${suffix} draft`
}

// Both provider error replies and engine failures render as system messages. Keep the raw error in Details.
const PROVIDER_ERROR_RE = /^(?:Failed to authenticate|OAuth\b|API Error|Invalid API key|Authentication\b|Not logged in|Unauthorized\b|Credit balance|Rate limit|Could not reach)/i
const AUTH_ERROR_RE = /authenticat|oauth|expired|unauthori[sz]ed|\b401\b|\b403\b|api[_ ]?key|not logged in|log ?in again|sign ?in again|credential/i
const QUOTA_ERROR_RE = /rate[_ ]?limit|quota|\b429\b|credit balance|usage limit|billing/i
// Claude Code's sign-in is the person's Claude account, so the line names Claude;
// the others by the name Settings › Connections shows.
const PROVIDER_WORDS = { 'claude-code': 'Claude' }

export function failedTurnInfo(message, { agentName = (id) => id, engineOf = () => null, providers = [] } = {}) {
  if (!message || message.streaming) return null
  let agent, raw, engine = message.engine ?? null
  if (message.kind === 'system') {
    if (message.text === 'Connect a provider in Settings › Connections to start a reply.') {
      return { kind: 'setup', summary: message.text, raw: message.text, action: 'providers' }
    }
    const m = /^(.+?)['’]s turn failed:\s*([\s\S]+)$/.exec(String(message.text ?? '').trim())
    if (!m) return null
    agent = m[1].trim(); raw = m[2].trim(); engine ??= engineOf(agent)
  } else {
    const text = String(message.text ?? '').trim()
    if (message.model !== '<synthetic>') {
      // A real reply that happens to start like an error is the agent's own,
      // unless the record says the turn ended in an error with nothing done.
      if (!PROVIDER_ERROR_RE.test(text) || message.attachments?.length) return null
      const segments = messageSegments(message)
      const did = segments.some((p) => p.steps?.length || p.did?.length || p.plan || (p.item && p.item.kind !== 'result'))
      if (did || !segments.some((p) => p.item?.kind === 'result' && p.item.isError)) return null
    }
    agent = agentName(message.author)
    raw = text || 'The turn ended without a reply.'
  }
  const provider = PROVIDER_WORDS[engine] ?? providers.find((p) => p.id === engine)?.name ?? null
  const reach = provider ? `${agent} couldn’t reach ${provider}.` : `${agent} couldn’t reach their AI provider.`
  if (AUTH_ERROR_RE.test(raw)) return { kind: 'signin', agent, engine, provider, raw, summary: `${reach} Sign in again in Settings › Connections.`, action: 'providers' }
  if (QUOTA_ERROR_RE.test(raw)) return { kind: 'limit', agent, engine, provider, raw, summary: `${provider ?? 'The provider'} has paused replies for now. Try again later, or see Settings › Connections.`, action: 'providers' }
  return { kind: 'failed', agent, engine, provider, raw, summary: `${agent}’s turn didn’t finish. Send your message again, or ask another agent.`, action: null }
}

// Loam's lines include draft, thread and message IDs for the agents reading
// them. In chat, a draft revision is shown once there is more than one.
export function formatSystemLine(text) {
  const clean = String(text ?? '')
    .replace(/\s*\([^()]*settings describe agent\.draft[^()]*\)/gi, '')
    .replace(/\s*\(m[\w-]+ in t-[0-9a-f]+\)/gi, '')
    .replace(/[;,]?\s*this thread is `?t-[0-9a-f]+`?\.?/gi, '')
    .trim()

  const drafted = /^(.+?) drafted a new agent: (.+?) \(draft d-[0-9a-f]+, revision (\d+)\)\.?$/i.exec(clean)
  if (drafted) {
    const [, by, who, rev] = drafted
    const ord = ordinalDraft(rev)
    return [{ text: `${by} drafted a new agent: ${who}${ord ? ` (${ord})` : ''}` }]
  }
  const revised = /^(.+?) revised draft d-[0-9a-f]+: (.*?), revision (\d+)(?: \([^)]*\))?\.?$/i.exec(clean)
  if (revised) {
    const [, by, who, rev] = revised
    return [{ text: `${by} made a ${ordinalDraft(rev) || 'new draft'} of ${who || 'the new agent'}` }]
  }
  const aside = /^(.+?) set draft d-[0-9a-f]+ aside\.?$/i.exec(clean)
  if (aside) return [{ text: `${aside[1]} set the new agent’s draft aside` }]
  return [{ text: clean.replace(/\s*\(draft d-[0-9a-f]+(?:, revision \d+)?\)/gi, '') }]
}
