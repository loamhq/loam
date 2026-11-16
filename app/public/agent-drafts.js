import { h } from './ui.js'

// Drafts belong to the host and the conversation. Only an explicit confirmation
// publishes the revision currently on screen; the host rejects a stale revision.
export function createAgentDrafts({ api, redraw, made = () => {} }) {
  const st = { thread: null, rows: [], open: false, busy: false, loading: false, error: '', result: null }
  let generation = 0
  async function load(thread = st.thread) {
    const ticket = ++generation
    if (st.thread !== thread) { st.rows = []; st.open = false; st.result = null; st.error = '' }
    st.thread = thread; st.loading = true
    try {
      const body = await api('GET', `/agents/drafts?${new URLSearchParams({ thread })}`)
      if (ticket === generation) st.rows = body.drafts || []
    } catch (e) { if (ticket === generation && ![404, 405].includes(e.status)) st.error = e.message }
    finally { if (ticket === generation) { st.loading = false; redraw() } }
  }
  async function confirm(draft) {
    if (st.busy) return
    const thread = st.thread, rev = draft.rev
    st.busy = true; st.error = ''; redraw()
    try {
      const result = await api('POST', `/agents/drafts/${encodeURIComponent(draft.draft)}/confirm`, { rev })
      made(result.agent)
      if (st.thread === thread) { st.result = result; st.rows = st.rows.filter(d => d.draft !== draft.draft) }
    } catch (e) { if (st.thread === thread) { st.error = e.message; await load(thread) } }
    finally { st.busy = false; redraw() }
  }
  function view(thread) {
    if (st.thread !== thread || (!st.open && !st.rows.length)) return null
    return h('section', { class: 'agent-drafts', 'aria-label': 'New agent review' },
      h('button', { class: 'pillbtn', 'aria-expanded': String(st.open), text: st.open ? 'Hide agent review' : 'Review agent', onclick: () => { st.open = !st.open; if (st.open) load(thread); redraw() } }),
      st.open ? h('div', { class: 'agent-drafts-body' },
        st.loading ? h('p', { role: 'status', text: 'Reading the latest draft…' }) : null,
        st.busy ? h('p', { role: 'status', text: 'Creating the agent… This can take up to 20 minutes. Keep this page open.' }) : null,
        st.error ? h('p', { role: 'alert', class: 'schedule-error', text: st.error }) : null,
        st.result ? h('p', { role: 'status', text: `${st.result.agent.name || st.result.agent.id} is ready.` }) : null,
        !st.rows.length && !st.loading && !st.result ? h('p', { class: 'note', text: 'Start by telling the agent what you have in mind. Its draft will appear here for your review.' }) : null,
        st.rows.map(d => h('article', { key: d.draft }, h('h3', { text: `${d.emoji || ''} ${d.name}`.trim() }), h('p', { class: 'note', text: `Revision ${d.rev}` }),
          ...['soul', 'config'].map(key => h('details', {}, h('summary', { text: key === 'soul' ? 'Soul' : 'Config' }), h('pre', { text: d[key] }))),
          (d.problems || []).map(problem => h('p', { class: 'note warn', text: typeof problem === 'string' ? problem : JSON.stringify(problem) })),
          h('button', { class: 'pillbtn primary', disabled: st.busy || st.loading || !d.ready, text: st.busy ? 'Creating…' : 'Create agent', onclick: () => confirm(d) })))) : null)
  }
  return { st, load, confirm, view }
}
