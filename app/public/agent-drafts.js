import { h, blob, icon } from './ui.js'
import { soulExcerpt } from './agent-profile.js'
import { createAgents, agentLook } from './sessions.js'
import { markdown } from './markdown.js'
import { ordinalDraft } from './thread-experience.js'

const DISMISSED_KEY = 'loam-ui.dismissed-drafts'
const MAX_DISMISSED = 50

function readDismissed(storage) {
  const map = new Map()
  if (!storage) return map
  try {
    const raw = storage.getItem(DISMISSED_KEY)
    const parsed = raw ? JSON.parse(raw) : null
    if (parsed && typeof parsed === 'object') {
      for (const [id, rev] of Object.entries(parsed)) {
        if (typeof id === 'string' && id && typeof rev === 'number' && Number.isFinite(rev)) map.set(id, rev)
      }
    }
  } catch { /* Storage disabled or corrupt: keep dismissed state in memory only. */ }
  return map
}

function writeDismissed(storage, map) {
  if (!storage) return
  try {
    while (map.size > MAX_DISMISSED) map.delete(map.keys().next().value)
    if (map.size) storage.setItem(DISMISSED_KEY, JSON.stringify(Object.fromEntries(map)))
    else storage.removeItem(DISMISSED_KEY)
  } catch { /* Ignore storage refusal; in-memory dismissal still works. */ }
}

const stripFrontmatter = (text = '') => String(text ?? '').replace(/^---\s*\n[\s\S]*?\n---\s*(?:\n|$)/, '').trim()
// The name is the card's title; a soul that opens by repeating it as a heading starts below it.
const withoutTitle = (text, name) => { const [first, ...rest] = text.split('\n'); return /^#\s/.test(first) && first.replace(/^#+\s*/, '').trim().toLowerCase() === name.toLowerCase() ? rest.join('\n').trim() : text }

// Prefer the draft’s description for the person. Fall back to a description from the
// opening of the soul, keeping instructions addressed to the agent off the card.
const firstSentence = text => {
  const prose = String(text ?? '').replace(/^---\s*\n[\s\S]*?\n---\s*(?:\n|$)/, '').split('\n').filter(line => !/^\s*#/.test(line)).join(' ').replace(/\s+/g, ' ').trim()
  return (prose.match(/^.+?[.!?](?=\s|$)/)?.[0] ?? prose).trim()
}
const upper = text => text ? text[0].toLocaleUpperCase() + text.slice(1) : text
const escape = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
export function draftLine(draft = {}) {
  const about = String(draft.about ?? '').replace(/\s+/g, ' ').trim()
  if (about) return about
  const sentence = firstSentence(draft.soul), name = String(draft.name ?? '').trim()
  if (!sentence) return null
  const named = name ? new RegExp(`^(?:you are|you're|you’re)\\s+${escape(name)}\\s*(?:,|—|–|-|:)\\s*(.+)$`, 'i').exec(sentence) : null
  if (named) return upper(named[1].trim().replace(/[.!]$/, '')) + '.'
  if (/^(?:you|your|you're|you’re|be|always|never|do|don't|don’t)\b/i.test(sentence)) return null
  return soulExcerpt(draft.soul)
}

// Drafts belong to the host and the conversation. Only an explicit confirmation
// publishes the revision currently on screen; the host rejects a stale revision.
export function createAgentDrafts({
  api,
  redraw,
  made = () => {},
  askChanges = () => {},
  onToggle = () => {},
  onDismiss = () => {},
  onRevision = () => {},
  renderText = markdown,
  storage = globalThis.localStorage,
}) {
  const dismissed = readDismissed(storage)
  const st = { thread: null, rows: [], expanded: new Set(), dismissed, busy: false, loading: false, error: '' }
  const knownRevs = new Map()
  let generation = 0
  let loadedOnce = false
  const saveDismissed = () => writeDismissed(storage, st.dismissed)

  async function load(thread = st.thread) {
    const ticket = ++generation
    if (st.thread !== thread) {
      st.rows = []; st.expanded.clear(); st.error = ''
      knownRevs.clear(); loadedOnce = false
    }
    st.thread = thread; st.loading = true
    try {
      const body = await api('GET', `/agents/drafts?${new URLSearchParams({ thread })}`)
      if (ticket !== generation) return
      const prevIds = new Set(st.rows.map(d => d.draft))
      const next = body.drafts || []
      const nextIds = new Set(next.map(d => d.draft))
      let revised = false
      let dismissedChanged = false
      for (const d of next) {
        const rev = d.rev ?? 0
        const prevRev = knownRevs.get(d.draft)
        const isNewDraft = loadedOnce && prevRev === undefined
        const isNewRev = prevRev !== undefined && rev !== prevRev
        if (isNewDraft || isNewRev) {
          st.expanded.delete(d.draft)
          revised = true
        }
        if (st.dismissed.has(d.draft) && st.dismissed.get(d.draft) !== rev) {
          st.dismissed.delete(d.draft)
          dismissedChanged = true
          revised = true
        }
        knownRevs.set(d.draft, rev)
      }
      for (const id of prevIds) {
        if (!nextIds.has(id)) {
          st.expanded.delete(id)
          knownRevs.delete(id)
          if (st.dismissed.delete(id)) dismissedChanged = true
        }
      }
      if (dismissedChanged) saveDismissed()
      st.rows = next
      loadedOnce = true
      if (revised) onRevision(next)
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
      if (st.thread === thread) {
        st.rows = st.rows.filter(d => d.draft !== draft.draft)
        st.expanded.delete(draft.draft)
        knownRevs.delete(draft.draft)
        if (st.dismissed.delete(draft.draft)) saveDismissed()
      }
    } catch (e) { if (st.thread === thread) { st.error = e.message; await load(thread) } }
    finally { st.busy = false; redraw() }
  }

  function dismiss(draft) {
    if (st.busy || !draft?.draft) return
    st.dismissed.set(draft.draft, draft.rev ?? 0)
    st.expanded.delete(draft.draft)
    saveDismissed()
    onDismiss(draft)
    redraw()
  }

  function collapseAll() {
    if (!st.expanded.size) return false
    st.expanded.clear()
    return true
  }

  function visibleRows(thread = st.thread) {
    if (st.thread !== thread) return []
    return st.rows.filter(d => !st.dismissed.has(d.draft) || st.dismissed.get(d.draft) !== (d.rev ?? 0))
  }

  // The review is a card in the thread's flow, after the line that says the
  // draft was made, where the agent says it is ("below"): the face and name,
  // one plain line of who they are (draftLine), the whole soul and config rendered behind
  // one fold, then Create and Ask for changes. A revision number is shown only
  // once there is more than one ("2nd draft"); the draft's id never is.
  // Closing hides the card without discarding the draft; a later revision brings
  // it back collapsed so the person never misses updated work.
  function card(d) {
    const id = d.id || d.draft || 'new-agent'
    const name = d.name || 'Your new agent'
    const look = agentLook(id, createAgents([{ id, name }]))
    const who = draftLine(d)
    const draftLabel = ordinalDraft(d.rev)
    const expanded = st.expanded.has(d.draft)
    const soulText = withoutTitle(stripFrontmatter(d.soul), name)
    const configText = stripFrontmatter(d.config)
    const toggle = () => {
      const next = !expanded
      if (next) st.expanded.add(d.draft); else st.expanded.delete(d.draft)
      onToggle(d, next)
      redraw()
    }
    return h('article', {
      class: 'card agent-draft-card',
      key: d.draft,
      'data-draft': d.draft,
      'data-expanded': String(expanded),
      'aria-label': `${name}, a new agent to review`,
    },
      h('div', { class: 'agent-draft-head' },
        blob(look, { size: 48, font: 22 }),
        h('div', { class: 'agent-draft-who' },
          h('div', { class: 'agent-draft-title-row' },
            h('h3', { text: name }),
            draftLabel ? h('span', { class: 'agent-draft-rev', key: `rev:${d.rev}`, text: draftLabel }) : null),
          who ? h('p', { class: 'agent-draft-summary', text: who }) : null),
        h('button', {
          type: 'button',
          class: 'iconbtn ghost agent-draft-close',
          'aria-label': `Hide ${name} draft`,
          title: 'Hide card',
          disabled: st.busy,
          onclick: () => dismiss(d),
        }, icon.close(16))),
      soulText || configText ? h('button', {
        type: 'button',
        class: 'quietlink agent-draft-more',
        'aria-expanded': String(expanded),
        onclick: toggle,
      }, expanded ? 'Show less' : 'Read the whole thing', icon.chevronDown(14)) : null,
      expanded ? h('div', { class: 'agent-draft-full' },
        soulText ? h('section', { class: 'agent-draft-doc', 'aria-label': 'Soul' },
          h('h4', { class: 'kicker', text: 'Who they are' }),
          h('div', { class: 'md' }, renderText(soulText))) : null,
        configText ? h('section', { class: 'agent-draft-doc', 'aria-label': 'Config' },
          h('h4', { class: 'kicker', text: 'How they work' }),
          h('div', { class: 'md' }, renderText(configText))) : null) : null,
      ...(d.problems || []).map(problem => h('p', { class: 'note warn', text: typeof problem === 'string' ? problem : problem?.message ?? JSON.stringify(problem) })),
      st.busy ? h('p', { class: 'note', role: 'status', text: `Making ${name}… This can take up to 20 minutes. Keep this page open.` }) : null,
      h('div', { class: 'agent-draft-actions' },
        h('button', { type: 'button', class: 'pillbtn primary', disabled: st.busy || st.loading || !d.ready, text: st.busy ? 'Creating…' : 'Create', onclick: () => confirm(d) }),
        h('button', { type: 'button', class: 'pillbtn secondary', disabled: st.busy, text: 'Ask for changes', onclick: () => askChanges(d) })))
  }

  function view(thread) {
    const rows = visibleRows(thread)
    if (st.thread !== thread || (!rows.length && !st.error)) return null
    return h('section', { class: 'agent-drafts', 'aria-label': 'New agent review', 'data-thread-trailing': 'true' },
      st.error ? h('p', { role: 'alert', class: 'schedule-error', text: st.error }) : null,
      ...rows.map(card))
  }
  return { st, load, confirm, dismiss, collapseAll, visibleRows, view }
}
