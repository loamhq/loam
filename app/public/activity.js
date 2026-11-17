import { createCoreRegistry } from './core-registrations.js'
import { formatTime, formatDate } from './time.js'
import { h, kept, morph, icon } from './ui.js'
import { segmentedControl } from './chat-ui.js'
import { foldJobChains, engineLabel, jobThread, jobContext } from './activity-record.js'
import { modelLabel } from './providers.js'
import { jobActivity } from './thread-experience.js'
import { strip } from './markdown.js'
import { scheduleChange, actorName } from './schedule-change.js'

export const ACTIVITY_KINDS = [
  ['change', 'Schedule changes'], ['refused', 'Refusals'], ['fire', 'Scheduled runs'],
  ['run', 'Run requests'], ['job', 'Engineer jobs'],
]
export const ACTIVITY_FILTERS = [['', 'All'], ['chat', 'Chats'], ['fire,run', 'Runs'], ['job', 'Jobs'], ['change,refused', 'Changes']]
const PAGE = 30
const number = (n) => Number.isInteger(n) && n >= 0
const validTime = (value) => typeof value === 'string' && Number.isFinite(Date.parse(value))

export function activityDay(value, now = new Date()) {
  if (!validTime(value)) return 'Date unknown'
  const date = new Date(value)
  if (date.toDateString() === now.toDateString()) return 'Today'
  const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1)
  if (date.toDateString() === yesterday.toDateString()) return 'Yesterday'
  return formatDate(date, { weekday: 'short', day: 'numeric' })
}

export function activityTime(item) {
  const start = validTime(item.started) ? item.started : null
  // Finished jobs have no `finished`: their activity timestamp is completion.
  const end = validTime(item.finished) ? item.finished : item.kind === 'job' && validTime(item.ts) ? item.ts : null
  if (start && end && Date.parse(end) >= Date.parse(start)) {
    const ms = Date.parse(end) - Date.parse(start)
    const duration = ms < 60000 ? '<1 min' : elapsed(start, Date.parse(end))
    return `${formatTime(start)}–${formatTime(end)} · ${duration}`
  }
  return validTime(item.ts) ? formatTime(item.ts) : 'Time unknown'
}

export function activityWords(item) {
  const failed = item.kind === 'refused' || item.ok === false || ['failed', 'cancelled', 'refused', 'killed', 'cut_off', 'cut off', 'missed'].includes(item.outcome)
  const outcome = item.kind === 'refused' ? 'refused'
    : item.outcome === 'completed' ? (['landed', 'landed_earlier'].includes(item.landing) ? 'landed' : 'completed')
      : ['killed', 'cut_off', 'cut off'].includes(item.outcome) ? 'cut off' : item.outcome ?? (item.ok === false ? 'failed' : null)
  const subject = item.title ?? item.record ?? (item.kind === 'job' ? 'Engineering work' : 'Schedule')
  const op = { create: 'created', delete: 'deleted', done: 'ran', pause: 'paused', resume: 'resumed', move: 'moved', edit: 'edited' }[item.op] ?? item.op
  const action = item.kind === 'run' ? 'run requested' : item.kind === 'change' ? op ?? 'changed' : outcome
  const summary = `${subject}${action ? ` ${action}` : ''}`
  // `stage` identifies where a run stopped; it is not a failure reason.
  const reason = failed ? item.why ?? item.produced?.line?.error ?? null : null
  return { summary, subject, action, failed, reason }
}

// The feed can succeed even when the launcher's status cannot be read.
export function readActivity(body) {
  if (!body || !Array.isArray(body.items) || !(body.jobs === null || (Array.isArray(body.jobs?.slots) && Array.isArray(body.jobs?.queue) && Array.isArray(body.jobs?.landing)))) {
    throw new Error('Activity is not available in a format this page can read.')
  }
  return { items: body.items, now: body.jobs, jobsError: body.jobs_error,
    total: number(body.total) ? body.total : null, next: body.next_offset }
}

export function elapsed(start, now = Date.now()) {
  const ms = now - Date.parse(start)
  if (!Number.isFinite(ms)) return 'Time unknown'
  const minutes = Math.max(0, Math.floor(ms / 60000))
  return minutes < 1 ? 'Just started' : minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} hr ${minutes % 60} min`
}

export function createActivity({ registry = createCoreRegistry(), api, redraw, agents, avatar, openRef,
  chats = () => ({ pinned: [], recent: [] }), chatFace = () => null, chatInfo = () => ({}), openChat = () => {},
  currentWork = () => null, openThread = () => {}, providers = () => [], scoped = false }) {
  const st = { items: [], now: null, total: null, next: null, jobsError: null, agent: '', kind: '',
    project: '', loaded: false, loading: false, error: '', actions: new Map(),
    confirm: null, detail: null, lastFetch: 0 }
  let request = 0
  let dialog = null
  let detailDialog = null

  async function load({ more = false } = {}) {
    const ticket = ++request
    const offset = more ? st.next : 0
    const limit = more ? PAGE : Math.min(200, Math.max(PAGE, st.items.length))
    st.loading = true
    redraw()
    try {
      const query = new URLSearchParams({ limit, offset })
      if (st.agent) query.set('agent', st.agent)
      if (st.kind && st.kind !== 'chat') query.set('kind', st.kind)
      let answer = readActivity(await api('GET', `/activity?${query}`))
      if (ticket !== request) return
      // Retain the portion already opened, using the host's cursor and its
      // 200-item request ceiling when that portion spans several pages.
      if (!more) {
        const wanted = Math.max(PAGE, st.items.length)
        const items = [...answer.items]
        while (answer.next !== null && answer.next !== undefined && items.length < wanted) {
          query.set('offset', answer.next)
          query.set('limit', Math.min(200, wanted - items.length))
          answer = readActivity(await api('GET', `/activity?${query}`))
          if (ticket !== request) return
          items.push(...answer.items)
        }
        answer = { ...answer, items }
      }
      if (ticket !== request) return
      st.items = [...new Map([...(more ? st.items : []), ...answer.items].map((i) => [i.id, i])).values()]
      st.now = answer.now
      st.jobsError = answer.jobsError
      st.total = answer.total
      st.next = answer.next
      st.loaded = true
      st.error = ''
    } catch (error) {
      if (ticket === request) st.error = error.status === 404 || error.status === 405
        ? 'Activity is not available from this host yet.' : error.message
    } finally {
      if (ticket === request) { st.loading = false; st.lastFetch = Date.now(); redraw() }
    }
  }

  function filter(key, value) {
    st[key] = value
    st.items = []
    st.total = null
    return load()
  }

  async function undo(item) {
    if (st.actions.get(item.id)?.busy) return
    st.actions.set(item.id, { busy: true, error: '' })
    redraw()
    try {
      await api('POST', `/activity/${encodeURIComponent(item.id)}/undo`, {})
      await load()
      st.actions.delete(item.id)
    } catch (error) { st.actions.set(item.id, { busy: false, error: error.message }) }
    redraw()
  }

  function dismiss() {
    const back = st.confirm?.returnFocus
    st.confirm = null
    dialog?.close?.()
    redraw()
    back?.focus?.({ preventScroll: true })
  }

  async function cancel() {
    const confirm = st.confirm
    if (!confirm || confirm.busy) return
    confirm.busy = true
    confirm.error = ''
    redraw()
    try {
      const answer = await api('POST', `/jobs/${encodeURIComponent(confirm.job.job)}/cancel`, {})
      st.message = answer.said
      if (st.confirm === confirm) dismiss()
      await load()
    } catch (error) { if (st.confirm === confirm) { confirm.error = error.message; confirm.busy = false } }
    redraw()
  }

  function name(id) { return actorName(id, agents()) }
  function engineerFace(job) {
    return h('span', { class: 'activity-engineer-mark', 'aria-label': 'Engineer' }, icon.code(20),
      job.briefed_by ? h('span', { class: 'activity-briefing-face', title: `For ${name(job.briefed_by)}` }, avatar(job.briefed_by)) : null)
  }
  function engineerContext(job) {
    const thread = jobThread(job), context = jobContext(job)
    const lead = [job.briefed_by ? `for ${name(job.briefed_by)}` : null, thread ? 'in' : context].filter(Boolean).join(' · ')
    return h('div', { class: 'activity-job-context' },
      lead ? h('span', { class: 'activity-job-source', text: lead + (thread ? ' ' : '') }) : null,
      thread ? h('button', { class: 'content-mark activity-thread-link', text: job.thread_title || 'thread',
        onclick: event => { event.stopPropagation(); openThread(thread) } }) : null)
  }
  function engineerMeta(job, timing) {
    return [job.project, engineLabel(job.engine), modelLabel(providers().find(p => p.id === job.engine), job.model).name, timing && !/unknown/i.test(timing) ? timing : null].filter(Boolean).join(' · ')
  }
  function activityLine(job) {
    const activity = job.kind === 'engineer job' ? jobActivity({ ...job, state: 'running' }) : null
    if (!activity) return null
    const stops = job.child_stops ? ` · ${job.child_stops} stuck ${job.child_stops === 1 ? 'command' : 'commands'} stopped` : ''
    return h('div', { class: `activity-meta activity-job-activity${activity.stuck ? ' activity-job-quiet' : ''}`, text: activity.text[0].toUpperCase() + activity.text.slice(1) + stops })
  }
  function jobRow(job, queued = false, position = 0) {
    const own = ['wake', 'cron', 'reviewer'].includes(job.run_kind) || job.kind === 'wake'
    return h('li', { class: `activity-job${jobThread(job) ? ' activity-paired' : ''}`, key: job.job ?? `slot:${job.slot}` },
      own ? avatar(job.agent || job.briefed_by) : engineerFace(job),
      h('div', { class: 'activity-job-words' },
        h('div', { class: 'activity-job-title', text: job.title || (own ? ({ cron: 'Scheduled work', reviewer: 'Review', wake: 'Wake' }[job.run_kind] || 'Independent work') : 'Engineering work') }),
        engineerContext(job),
        h('div', { class: 'activity-meta', text: engineerMeta(job, queued ? `Queued ${job.position ?? position + 1}` : validTime(job.since || job.started) ? elapsed(job.since || job.started) : null) }),
        // The engine's last event, not only the start: a stuck job reads as stuck (2026-11-17).
        activityLine(job)),
      job.job ? h('button', { class: 'pillbtn content-mark activity-cancel', text: 'Cancel', 'aria-label': `Cancel ${job.title}`, onclick: (event) => {
        st.confirm = { job, queued, busy: false, error: '', returnFocus: event.currentTarget }; redraw()
      } }) : null)
  }

  function agentName(id) {
    return agents().find((agent) => agent.id === id)?.name ?? (id ? id[0].toUpperCase() + id.slice(1) : 'Unknown agent')
  }

  function open(item, returnFocus = null) {
    const ref = item.receipt ?? item.produced?.line?.vault_ref ?? item.produced?.failure_log
    if (ref) return openRef(ref)
    st.detail = { item, returnFocus }
    redraw()
  }

  function closeDetail() {
    const back = st.detail?.returnFocus
    st.detail = null
    detailDialog?.close?.()
    redraw()
    back?.focus?.({ preventScroll: true })
  }

  function historyRow({ key, face, title, secondary, trailing = null, failed = false, open, popup = null, engineer = false }) {
    return h('li', { class: `activity-item${failed ? ' activity-failed' : ''}${engineer ? ' activity-engineer' : ''}`, key,
      role: 'button', tabindex: '0', 'aria-haspopup': popup,
      onclick: event => open(event.currentTarget),
      onkeydown: event => { if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); open(event.currentTarget) } } },
      h('div', { class: 'activity-face' }, face),
      h('div', { class: 'activity-words' }, h('div', { class: 'activity-summary' }, ...title),
        h('div', { class: 'activity-time' }, ...secondary)), trailing)
  }
  function entry(item) {
    const { summary, subject, action, failed, reason } = activityWords(item)
    const timing = activityTime(item)
    if (item.kind === 'job') {
      const continuations = item.continuationCount || 0
      const outcome = [action, continuations ? `after ${continuations} ${continuations === 1 ? 'continuation' : 'continuations'}` : null].filter(Boolean).join(' ')
      return historyRow({ key: item.id, face: engineerFace(item), failed, engineer: true,
        open: node => open(item, node), popup: item.receipt ? null : 'dialog',
        title: [h('strong', { text: subject })],
        secondary: [engineerContext(item), h('span', { class: 'activity-engineer-facts', text: engineerMeta(item, timing) }),
          outcome ? h('span', { class: 'activity-action', text: outcome }) : null,
          reason ? h('span', { class: 'activity-failure-reason', text: ` · ${reason}` }) : null] })
    }
    return historyRow({ key: item.id, face: avatar(item.agent), failed,
      popup: item.receipt || item.produced?.line?.vault_ref || item.produced?.failure_log ? null : 'dialog',
      open: node => open(item, node),
      title: [h('strong', { text: agentName(item.agent) }), ' · ', h('span', { class: 'activity-subject', text: subject, title: `${agentName(item.agent)} · ${summary}` })],
      secondary: [action ? h('span', { class: 'activity-action', text: action }) : null, action ? ' · ' : null,
        h('time', { datetime: item.ts, text: timing }), reason ? h('span', { class: 'activity-failure-reason', text: ` · ${reason}`, title: reason }) : null] })
  }
  function threadEntry(chat) {
    const info = chatInfo(chat), at = chat.last?.at ?? chat.created ?? chat.last_activity
    const preview = strip(info.secondary || chat.last?.text || 'Quiet')
    return historyRow({ key: `chat:${chat.id ?? chat.session_id}`, face: chatFace(chat), open: () => openChat(chat),
      title: [h('span', { class: 'activity-subject', text: chat.title || 'New thread' })],
      secondary: [h('span', { class: 'activity-last-line', text: preview, title: preview })],
      trailing: validTime(at) ? h('time', { class: 'activity-trailing-time', datetime: at, text: formatTime(at) }) : null })
  }

  function feedItems({ limit = null, project = st.project } = {}) {
    const rows = chats(st.agent)
    const showChats = !st.kind || st.kind === 'chat'
    return [
      ...(st.kind === 'chat' ? [] : foldJobChains(st.items.filter(item => !project || item.kind === 'job' && item.project === project)).map((item) => ({ item, ts: item.ts }))),
      ...(showChats ? [...rows.pinned, ...rows.recent].map((chat) => ({ chat, ts: chat.last?.at ?? chat.created ?? chat.last_activity })) : []),
    ].sort((a, b) => (Date.parse(b.ts) || 0) - (Date.parse(a.ts) || 0)).slice(0, limit ?? Infinity)
  }

  // Profiles preview the same feed and destinations with one compact row.
  // Detailed job context, day headings and filters belong in All activity.
  function compactEntry({ item, chat, ts }) {
    const words = item ? activityWords(item) : null
    const label = item?.kind === 'job' ? 'Engineer job' : ({ fire: 'Scheduled run', run: 'Run request', change: 'Schedule change', refused: 'Schedule change' })[item?.kind] || 'Activity'
    const secondary = chat ? chatInfo(chat).status || 'Thread' : [label, words.action].filter(Boolean).join(' · ')
    return h('button', { class: 'setrow profile-recent-row', key: chat ? `chat:${chat.id ?? chat.session_id}` : item.id,
      onclick: event => chat ? openChat(chat) : open(item, event.currentTarget) },
      h('span', { class: 'profile-recent-face', 'aria-hidden': 'true' }, chat ? chatFace(chat) : item.kind === 'job' ? icon.code(20) : avatar(item.agent)),
      h('span', { class: 'setrow-who' }, h('span', { class: 'setrow-title', text: strip(chat ? chat.title || 'New thread' : words.subject) }), h('span', { class: 'setrow-sub', text: secondary })),
      validTime(ts) ? h('time', { class: 'profile-recent-time', datetime: ts, text: formatTime(ts) }) : null)
  }

  function compactView(options) {
    const items = feedItems(options)
    return h('div', { class: 'profile-recent-list', 'aria-busy': String(st.loading) },
      st.error ? h('div', { class: 'activity-notice', role: 'alert' }, h('p', { text: st.error }), h('button', { class: 'pillbtn', text: 'Try again', onclick: () => load() })) : null,
      ...items.map(compactEntry),
      !items.length ? h('p', { class: 'note profile-empty', text: st.loading ? 'Loading activity…' : st.error ? 'Activity could not be loaded.' : 'No recent activity.' }) : null)
  }

  function feedRows({ embedded = false, limit = null, project = st.project } = {}) {
    const rows = chats(st.agent), showChats = !st.kind || st.kind === 'chat'
    const items = feedItems({ limit, project })
    const groups = new Map()
    for (const row of items) {
      const key = validTime(row.ts) ? new Date(row.ts).toDateString() : 'unknown'
      if (!groups.has(key)) groups.set(key, { ts: row.ts, rows: [] })
      groups.get(key).rows.push(row)
    }
    return [
      ...[...groups].map(([key, group]) => h('section', { class: 'activity-day', key },
        validTime(group.ts) ? h('h3', { class: 'kicker activity-day-title', text: activityDay(group.ts) }) : null,
        h('ul', { class: 'activity-items' }, group.rows.map(({ item, chat }) => chat
          ? threadEntry(chat) : registry.draw('activity.kind', { item, defaultView: () => entry(item) }))))),
      !items.length && !(embedded && showChats && rows.pinned.length) ? h('p', { class: 'activity-empty', text: st.loading ? 'Loading activity…' : st.error ? 'Activity could not be loaded.' : st.agent || st.kind ? 'No activity matches these filters.' : 'Nothing has happened here yet.' }) : null,
    ]
  }

  function view(...args) { return registry.component('core.activity', { variant: 'view', args, state: st, defaultView: () => builtinView(...args) }) }
  function builtinView({ embedded = false, compact = false, limit = null, project = st.project } = {}) {
    if (compact) return compactView({ limit: Math.min(limit ?? 4, 4), project })
    const choices = (label, key, options) => h('label', { class: 'activity-filter settings-select' },
      h('span', { text: label }),
      h('select', { 'aria-label': `Filter by ${label.toLowerCase()}`, onchange: (event) => filter(key, event.target.value) },
        options.map(([value, text]) => h('option', { value, selected: st[key] === value, text }))), icon.chevronDown(12))
    const slots = st.now?.slots.filter(job => !job.free) || []
    const own = slots.filter(job => ['wake', 'cron', 'reviewer'].includes(job.run_kind) || job.kind === 'wake')
    const engineers = slots.filter(job => !own.includes(job))
    const jobs = [...engineers, ...(st.now?.landing || []), ...(st.now?.queue || [])]
    return h(embedded ? 'div' : 'main', { class: `activity-page${embedded ? ' activity-embedded' : ''}`, key: 'activity', 'aria-label': 'Activity' },
      embedded ? null : h('header', { class: 'activity-head' }, h('h1', { class: 'display', text: 'Activity' })),
      st.error ? h('div', { class: 'activity-notice', role: 'alert' }, h('p', { text: st.error }), h('button', { class: 'pillbtn content-mark', text: 'Try again', disabled: st.loading, onclick: () => load() })) : null,
      st.message ? h('p', { class: 'activity-notice', role: 'status', text: st.message }) : null,
      embedded ? null : h('section', { class: 'activity-now', 'aria-labelledby': 'activity-now-title' },
        h('h2', { id: 'activity-now-title', class: 'display', text: 'Now' }),
        currentWork(st.now ? [...jobs, ...own] : null),
        st.now ? [
          jobs.length ? h('h3', { class: 'kicker activity-group-title', text: 'Engineers' }) : null,
          engineers.length ? h('ol', { class: 'activity-slots', 'aria-label': 'Engineer jobs' }, engineers.map(job => jobRow(job))) : null,
          st.now.landing.length ? h('div', {}, h('h3', { class: 'activity-subtitle', text: 'Landing' }), h('ol', { class: 'activity-landings' }, st.now.landing.map(job => jobRow(job)))) : null,
          st.now.queue.length ? h('div', {}, h('h3', { class: 'activity-subtitle', text: 'Queue' }), h('ol', { class: 'activity-queue', 'aria-label': 'Engineer queue' }, st.now.queue.map((job, i) => jobRow(job, true, i)))) : null,
          own.length ? h('section', { class: 'activity-independent' }, h('h3', { class: 'kicker activity-group-title', text: 'On their own' }), h('ol', { class: 'activity-slots' }, own.map(job => jobRow(job)))) : null,
        ] : h('p', { class: 'activity-empty', text: st.jobsError || (st.loading ? 'Loading current work…' : 'Current jobs are unavailable.') })),
      h('section', { class: 'activity-feed', 'aria-label': 'Earlier', 'aria-busy': String(st.loading) },
        embedded ? null : h('h2', { id: 'activity-feed-title', class: 'display', text: 'Earlier' }),
        limit !== null || project ? null : h('div', { class: 'activity-filters' },
          segmentedControl({ label: 'Activity filters', options: ACTIVITY_FILTERS.map(([id, label]) => ({ id, label })), value: st.kind, choose: value => filter('kind', value), props: { class: 'activity-chips' } }),
          !scoped ? choices('Agent', 'agent', [['', 'All agents'], ...agents().map((a) => [a.id, a.name])]) : null),
        ...feedRows({ embedded, limit, project }),
        limit === null && st.kind !== 'chat' && st.loaded && st.next !== null && st.next !== undefined
          ? h('button', { class: 'pillbtn content-mark activity-more', text: st.loading ? 'Loading…' : 'Load earlier', disabled: st.loading, onclick: () => load({ more: true }) }) : null))
  }

  function detailNodes() {
    if (!st.detail) return []
    if (!detailDialog) detailDialog = h('dialog', { class: 'activity-confirm activity-detail', 'aria-labelledby': 'activity-detail-title',
      oncancel: (event) => { event.preventDefault(); closeDetail() },
      onclick: (event) => { if (event.target === detailDialog) closeDetail() } })
    const item = st.items.find((i) => i.id === st.detail.item.id) ?? st.detail.item
    const words = activityWords(item)
    morph(detailDialog, [h('div', { class: 'activity-confirm-body' },
      h('h2', { id: 'activity-detail-title', class: 'display', text: item.kind === 'job' ? words.summary : `${agentName(item.agent)} · ${words.summary}` }),
      h('p', { class: 'activity-meta', text: activityTime(item) }),
      words.reason ? h('p', { class: 'activity-error', text: words.reason }) : null,
      item.kind === 'change' || item.kind === 'refused'
        ? scheduleChange(item, { ...st.actions.get(item.id), undo: item.kind === 'change' ? () => undo(item) : null })
        : h('p', { class: 'muted', text: 'No receipt or output was supplied for this item.' }),
      item.stage ? h('p', { class: 'activity-meta', text: `Stage: ${item.stage}` }) : null,
      item.landing ? h('p', { class: 'activity-meta', text: `Landing: ${item.landing}` }) : null,
      item.kind === 'fire' && item.started && !item.finished ? h('p', { class: 'activity-meta', text: 'No finish time was supplied.' }) : null,
      h('div', { class: 'activity-confirm-actions' }, h('button', { class: 'pillbtn', autofocus: true, text: 'Close', onclick: closeDetail })))])
    return [kept(detailDialog)]
  }

  function dialogNodes() {
    if (!st.confirm) return detailNodes()
    if (!dialog) dialog = h('dialog', { class: 'activity-confirm', 'aria-labelledby': 'activity-confirm-title',
      oncancel: (event) => { event.preventDefault(); dismiss() },
      onkeydown: (event) => {
        if (event.key !== 'Tab') return
        const buttons = [...dialog.querySelectorAll('button:not([disabled])')]
        const at = buttons.indexOf(document.activeElement)
        event.preventDefault()
        buttons[(at + (event.shiftKey ? -1 : 1) + buttons.length) % buttons.length]?.focus()
      },
      onclick: (event) => { if (event.target === dialog) dismiss() } })
    const { job, queued, busy, error } = st.confirm
    morph(dialog, [h('div', { class: 'activity-confirm-body' },
      h('h2', { id: 'activity-confirm-title', class: 'display', text: `Cancel ${queued ? 'queued ' : ''}job?` }),
      h('p', { text: job.title }),
      h('p', { class: 'muted', text: queued ? 'This job will never start.' : 'This records a durable cancellation. The running process is not killed; this job will not continue or recover.' }),
      error ? h('p', { role: 'alert', class: 'activity-error', text: error }) : null,
      h('div', { class: 'activity-confirm-actions' },
        h('button', { class: 'pillbtn', autofocus: true, text: 'Keep job', onclick: dismiss }),
        h('button', { class: 'pillbtn primary', disabled: busy, text: busy ? 'Cancelling…' : 'Cancel job', onclick: cancel })))])
    return [kept(dialog)]
  }

  return { st, load, view, filter, undo, cancel, dismiss, open, closeDetail, dialogNodes,
    syncDialog: () => {
      if (st.confirm && dialog && !dialog.open) dialog.showModal?.()
      if (st.detail && detailDialog && !detailDialog.open) detailDialog.showModal?.()
    },
    tick: (active) => { if (!st.loading && !st.confirm?.busy && ![...st.actions.values()].some((a) => a.busy) && Date.now() - st.lastFetch >= (active ? 5000 : 30000)) return load() } }
}
