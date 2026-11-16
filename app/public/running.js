import { h, icon } from './ui.js'
import { avatar } from './chat-ui.js'
import { agentLook, createAgents } from './sessions.js'
import { createLink } from './link.js'
import { modelLabel } from './providers.js'
import { jobThread } from './activity-record.js'
import { authExpired, failureSentence } from './auth.js'
import { liveSession, readLiveSession, readBox, sessionLine } from './running-record.js'

export function createRunning({ api, redraw, agents, openPanel, openConversation, providers = () => [], linkFactory = createLink }) {
  const st = { records: new Map(), liveRecords: new Map(), mode: null, ready: false, error: '', actionError: '', confirm: null, pending: new Set(), toast: null, memory: null, count: null, phase: null, bulk: false }
  const listeners = new Set()
  let link, liveLink, loading, timer, revision = 0, started = false
  const rows = () => st.mode === 'rich' ? [...st.liveRecords.values()].map(readLiveSession).filter(Boolean) : [...st.records.values()].map(liveSession).filter(Boolean)
  const name = (id) => agents().find((a) => a.id === id)?.name ?? id ?? 'Agent'
  function receiveLive(view) {
    revision++; st.mode = 'rich'; st.liveRecords = new Map(view.sessions.map((r) => [r.id, r]))
    st.memory = readBox(view.box); st.count = view.box?.sessions?.live ?? null
    st.ready = true; st.error = ''; redraw()
  }
  function receive(event) {
    if (event.type === 'sessions.live') { receiveLive(event); return }
    if (event.type === 'broker.residency' && st.mode !== 'rich') { revision++; st.count = event.live_count ?? event.live ?? null }
    if (event.type === 'broker.session') {
      revision++; st.records.set(event.session_id, event)
      if (st.mode !== 'rich') { st.ready = true; st.error = '' }
    }
    for (const listener of listeners) listener(event)
    redraw()
  }
  function syncLinks() {
    if (!started || authExpired()) return
    if (st.mode === 'rich' && !liveLink) {
      liveLink = linkFactory({ href: () => '/api/sessions/live/events', onEvent: receive,
        onPhase: (phase) => { st.phase = phase; redraw() } })
      liveLink.start().catch(() => {})
    }
    if (st.mode === 'legacy' && liveLink) { liveLink.stop(); liveLink = null }
    if (st.mode === 'legacy' || listeners.size) {
      if (!link) {
        link = linkFactory({ href: () => '/api/sessions/events?archived=1', onEvent: receive,
          onPhase: (phase) => { if (st.mode !== 'rich') st.phase = phase; redraw() } })
        link.start().catch(() => {})
      }
    } else { link?.stop(); link = null }
  }
  async function load() {
    if (loading || authExpired()) return loading
    const before = revision
    loading = (async () => {
      try {
        let live
        try { live = await api('GET', '/sessions/live') }
        catch (e) { if (![404, 405].includes(e.status)) throw e }
        if (live) {
          if (before === revision || st.mode !== 'rich') receiveLive(live)
        } else {
          st.mode = 'legacy'; st.memory = null
          const body = await api('GET', '/sessions?archived=1')
          // A read begun before an SSE update must not erase that update.
          if (before === revision) st.records = new Map(body.sessions.map((r) => [r.session_id, r]))
          if (before === revision || st.count === null) st.count = body.live_count ?? body.live ?? null
          st.ready = true; st.error = ''
        }
        syncLinks()
      } catch (e) { st.error = failureSentence(e.message) }
      finally { loading = null; redraw() }
    })()
    return loading
  }
  function start() { started = true; load() }
  function subscribe(listener) {
    listeners.add(listener); syncLinks()
    return { start: async () => {}, stop: () => { listeners.delete(listener); syncLinks() }, wake: () => link?.wake() }
  }
  function reconnect() { (st.mode === 'rich' ? liveLink : link)?.retry?.(); return load() }
  function notify(text) { st.toast = text; clearTimeout(timer); timer = setTimeout(() => { st.toast = null; redraw() }, 6500); redraw() }
  async function end(row, confirmed = false) {
    if (st.pending.has(row.id) || st.bulk || authExpired()) return
    st.actionError = ''; st.pending.add(row.id); redraw()
    try {
      // A session may have started a turn while its idle row was on screen.
      let current
      if (st.mode === 'rich') {
        const view = await api('GET', '/sessions/live')
        receiveLive(view); current = rows().find((r) => r.id === row.id)
      } else {
        const record = await api('GET', `/sessions/${encodeURIComponent(row.id)}`)
        st.records.set(row.id, record); current = liveSession(record)
      }
      if (!current) { st.confirm = null; return }
      if ((current.state !== 'idle' || current.losesWork === true) && !confirmed) { st.confirm = row.id; st.focusConfirm = true; openPanel(); return }
      await api('DELETE', `/sessions/${encodeURIComponent(row.id)}`)
      st.confirm = null
      await load()
      notify(`${name(row.agent)} stepped out. They’ll pick up where they left off next time.`)
    } catch (e) { st.actionError = failureSentence(e.message); openPanel() }
    finally { st.pending.delete(row.id); redraw() }
  }
  function requestEnd(row) {
    if (!row) return
    if (row.state === 'idle') return end(row)
    st.confirm = row.id; st.focusConfirm = true; openPanel(); redraw()
  }
  function liveDot(resident) { return resident ? h('span', { class: 'dot run-live', title: 'Session running', role: 'img', 'aria-label': 'Session running' }) : null }
  function rowView(row, jobs = []) {
    const look = agentLook(row.agent, createAgents(agents())), busy = st.pending.has(row.id), confirming = st.confirm === row.id
    return h('div', { class: 'run-row', key: row.id, 'data-session': row.id },
      h('div', { class: 'run-row-main' }, h('button', { class: 'run-open', disabled: !row.mapped, title: row.mapped ? `Open ${row.title}` : 'Conversation link needs a runtime update', 'aria-label': row.mapped ? `Open ${row.title}` : null, onclick: () => openConversation(row) },
        avatar({ ...look, working: row.state === 'working' || row.state === 'background' }, { size: 40, font: 18 }),
        h('span', { class: 'run-who' }, h('span', { class: 'run-title', text: row.title }), h('span', { class: 'run-agent', text: name(row.agent) }),
          h('span', { class: 'run-state' }, jobs.some(job => (row.threadId && jobThread(job) === row.threadId) || job.caller_session === row.id) ? 'Waiting on an engineer' : sessionLine(row),
            row.model ? h('span', { class: 'run-model', text: modelLabel(providers().find(p => p.id === row.engine), row.model).name }) : null))),
        h('div', { class: 'run-row-end' }, row.memoryMb != null ? h('span', { class: 'run-mb', text: `${Math.round(row.memoryMb)} MB` }) : null,
          h('button', { class: 'pillbtn content-mark', type: 'button', disabled: busy || st.bulk, 'aria-label': `End ${name(row.agent)} session in ${row.title}`, onclick: () => requestEnd(row), text: busy ? 'Ending…' : 'End' }))),
      confirming ? h('div', { class: 'run-confirm', role: 'group', 'aria-label': `Confirm ending ${name(row.agent)}`, onkeydown: (e) => { if (e.key === 'Escape') { e.stopPropagation(); st.confirm = null; redraw() } } },
        h('p', { text: `This stops ${name(row.agent)}’s turn in progress.` }),
        h('div', { class: 'run-confirm-actions' }, h('button', { class: 'pillbtn content-mark', disabled: busy, onclick: () => { st.confirm = null; redraw() }, text: 'Keep running' }),
          h('button', { class: 'pillbtn primary run-danger', disabled: busy, onclick: () => end(row, true), text: 'End session' }))) : null)
  }
  async function endIdle() {
    if (st.bulk || authExpired()) return
    st.bulk = true; st.actionError = ''; redraw()
    let ended = 0
    try {
      if (st.mode === 'rich') {
        const result = await api('POST', '/sessions/end-idle', {})
        ended = result.ended.length
      } else {
        for (const row of rows().filter((r) => r.state === 'idle')) {
          const record = await api('GET', `/sessions/${encodeURIComponent(row.id)}`)
          st.records.set(row.id, record)
          if (liveSession(record)?.state !== 'idle') continue
          await api('DELETE', `/sessions/${encodeURIComponent(row.id)}`); ended++
        }
      }
      await load()
      if (!ended) notify('Nothing idle was ended. Recently active sessions keep running.')
      else notify(`${ended} ${ended === 1 ? 'agent stepped' : 'agents stepped'} out. They’ll pick up where they left off next time.`)
    } catch (e) { await load(); st.actionError = failureSentence(e.message) }
    finally { st.bulk = false; redraw() }
  }
  function memoryGauge() {
    const m = st.memory
    if (!m || !Number.isFinite(m.usedMb) || !Number.isFinite(m.totalMb) || m.totalMb <= 0) return null
    const gb = (mb) => `${(mb / 1024).toFixed(1).replace(/\.0$/, '')} GB`
    const text = `${gb(m.usedMb)} of ${gb(m.totalMb)} used${Number.isFinite(m.room) ? ` · Room for about ${m.room} more` : ''}`
    const extras = [['engineer job', m.jobs], ['wake', m.wakes]].filter(([, value]) => value?.count > 0)
      .map(([label, value]) => `${value.count} ${label}${value.count === 1 ? '' : 's'}${Number.isFinite(value.memoryMb) ? ` · ${Math.round(value.memoryMb)} MB` : ''}`)
    return extras.length ? h('details', { class: 'run-memory-details' },
      h('summary', { class: 'run-memory-line', text }), h('p', { class: 'activity-meta', text: extras.join(' · ') }))
      : h('p', { class: 'run-memory-line', text })
  }

  function inlineView(jobs = []) {
    const jobsKnown = Array.isArray(jobs)
    jobs = jobs || []
    const sessions = rows()
    return h('div', { class: 'run-inline' }, memoryGauge(),
      st.phase === 'retrying' ? h('p', { class: 'run-stale', role: 'status', text: 'Reconnecting…' }) : st.phase === 'stopped' ? h('p', { class: 'run-stale', role: 'status' }, 'Live updates paused. ', h('button', { class: 'quietlink', onclick: reconnect, text: 'Reconnect' })) : null,
      st.error || st.actionError ? h('div', { class: 'banner', role: 'alert' }, st.actionError || st.error, h('button', { class: 'quietlink', onclick: () => { st.actionError = ''; reconnect() }, text: 'Try again' })) : null,
      !st.ready && !st.error ? h('p', { class: 'muted', role: 'status', text: 'Checking what’s running…' }) : null,
      st.ready && jobsKnown && !sessions.length && !jobs.length ? h('p', { class: 'activity-empty', text: 'Nothing running' }) : null,
      sessions.length ? h('section', { class: 'run-group' }, h('h3', { class: 'kicker', text: 'Conversations' }), ...sessions.map(row => rowView(row, jobs))) : null,
      sessions.filter(row => row.state === 'idle').length >= 2 ? h('div', { class: 'run-footer' }, h('button', { class: 'pillbtn content-mark', disabled: st.bulk || st.pending.size > 0, onclick: endIdle, text: st.bulk ? 'Ending idle sessions…' : 'End all idle' })) : null)
  }
  return { st, rows, receive, load, start, subscribe, end, requestEnd, inlineView, liveDot, endIdle,
    syncDialog: () => { if (!st.focusConfirm) return; const node = document.querySelector('.run-inline .run-confirm'); if (node) { st.focusConfirm = false; node.scrollIntoView?.({ block: 'nearest' }); node.querySelector('button')?.focus?.({ preventScroll: true }) } },
    getSession: (id) => rows().find((r) => r.id === id),
    forMember: (threadId, agent) => rows().find((r) => r.kind === 'thread' && r.threadId === threadId && r.agent === agent),
    wake: () => { if (!authExpired()) { link?.wake(); liveLink?.wake(); if (!st.ready && !loading) load() } }, stop: () => { started = false; link?.stop(); liveLink?.stop(); clearTimeout(timer) },
    toastNodes: () => st.toast ? [h('div', { class: 'run-toast listnotice', role: 'status', key: 'running-toast' }, h('span', { class: 'listnotice-text', text: st.toast }), h('button', { class: 'iconbtn ghost', 'aria-label': 'Dismiss notice', onclick: () => { st.toast = null; redraw() } }, icon.close(12)))] : [] }
}
