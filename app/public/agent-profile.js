import { menuSurface } from './menu-surface.js'
import { h, icon, blob } from './ui.js'
import { emptyState } from './empty-state.js'
import { settingsRow } from './chat-ui.js'
import { agentLook } from './sessions.js'
import { modelsOf, effortsOf, effortFits } from './providers.js'
import { formatDate } from './time.js'

const enc = encodeURIComponent
export function soulExcerpt(text = '') {
  const prose = String(text).replace(/^---\s*\n[\s\S]*?\n---\s*(?:\n|$)/, '').split('\n').filter(line => !/^\s*#/.test(line)).join(' ').replace(/\s+/g, ' ').trim()
  const parts = typeof Intl.Segmenter === 'function' ? [...new Intl.Segmenter('en', { granularity: 'sentence' }).segment(prose)].map(p => p.segment) : prose.match(/[^.!?]+[.!?]+(?:[”’"']+)?|[^.!?]+$/g) || []
  return parts.slice(0, 2).join('').trim()
}
const when = (value, dated = false) => value ? formatDate(value, { weekday: 'short', ...(dated ? { month: 'short', day: 'numeric' } : {}), hour: 'numeric', minute: '2-digit' }) : null
export function profileStatus(status = {}) {
  const parts = []
  if (status.threads?.length) parts.push(`Working in ${status.threads.length} ${status.threads.length === 1 ? 'thread' : 'threads'}`)
  if (status.box_read === false) parts.push('Jobs unavailable')
  else if (status.jobs?.length) parts.push(`${status.jobs.length} ${status.jobs.length === 1 ? 'job' : 'jobs'} running`)
  if (status.queued?.length) parts.push(`${status.queued.length} queued`)
  if (status.run) parts.push(`${status.run.name || 'Wake'} running`)
  if (status.next_wake) parts.push(`Next wake ${when(status.next_wake)}`)
  return parts.join(' · ') || 'Ready for a new thread'
}

// One controller for host-owned settings. Tickets prevent a late answer for
// another agent/page from replacing the page the person is now reading.
// An archive or restore response may identify the agent by ID or by an older agent row.
const agentId = agent => typeof agent === 'string' ? agent : agent?.id
const agentRow = agent => agent && typeof agent === 'object' ? agent : {}

export function createProfiles({ api, redraw, changedAgent = () => {} }) {
  const st = { target: null, data: null, loading: false, busy: false, error: '', edit: null, activity: false, tab: 'Threads', page: 1, soul: '', soulError: '', archived: null, directoryBusy: false,
    engineers: null, reviewer: null, attention: null, attentionBusy: false, attentionError: '', directoryError: '', directoryLoading: false }
  let ticket = 0, mutation = 0
  const path = target => target.kind === 'agent' ? `/agents/${enc(target.id)}` : target.kind === 'engineer' ? `/engineers/${enc(target.id)}` : '/system/reviewer'
  async function directory() {
    if (st.directoryLoading) return
    st.directoryLoading = true; st.directoryError = ''; redraw()
    const results = await Promise.allSettled([api('GET', '/engineers'), api('GET', '/system/reviewer'), api('GET', '/system/persona')])
    if (results[0].status === 'fulfilled') st.engineers = results[0].value
    if (results[1].status === 'fulfilled') st.reviewer = results[1].value
    if (results[2].status === 'fulfilled') st.attention = results[2].value
    st.directoryError = results.filter(r => r.status === 'rejected').map(r => r.reason.message).join(' ')
    st.directoryLoading = false; redraw()
  }
  async function load(kind, id) {
    const current = ++ticket
    st.lastRead = Date.now(); st.refreshing = false; st.tab = 'Threads'; st.page = 1; st.soul = ''; st.soulError = ''; st.menu = false; st.faceMenu = false; st.confirmArchive = false; st.focusField = null; st.manageSchedules = false; st.target = { kind, id }; st.data = null; st.edit = null; st.error = ''; st.activity = false; st.busy = false; st.loading = true; redraw()
    try { const data = await api('GET', path(st.target)); if (current === ticket) {
      st.data = kind === 'engineer' ? { ...data, applies: data.applies || st.engineers?.applies } : data
      if (kind === 'agent' && data.files?.soul?.root && data.files.soul.exists !== false) {
        const ref = data.files.soul
        api('GET', `/files/read?${new URLSearchParams({ root: ref.root, path: ref.path })}`).then(file => { if (current === ticket) { st.soul = soulExcerpt(file.text); redraw() } }).catch(e => { if (current === ticket) { st.soulError = e.message; redraw() } })
      }
    } }
    catch (e) { if (current === ticket) st.error = e.message }
    finally { if (current === ticket) { st.loading = false; redraw() } }
  }
  async function patch(body, customPath = null) {
    if (st.busy || !st.target) return false
    const current = ticket, target = st.target
    mutation++
    st.busy = true; st.error = ''; redraw()
    try {
      const result = await api('PATCH', customPath || path(target), body)
      if (target.kind === 'agent' && !customPath) changedAgent(result.agent)
      if (current !== ticket) return true
      if (customPath) {
        if (customPath.startsWith('/plugins/')) st.data.plugins = st.data.plugins.map(p => p.id === result.plugin ? { ...p, values: result.values } : p)
        else {
          const updated = await api('GET', path(target))
          if (current !== ticket) return true
          st.data = updated
        }
      } else if (target.kind === 'engineer') {
        st.engineers = result
        st.data = target.id === 'default' ? { project: 'default', ...result.default, applies: result.applies } : { ...result.engineers.find(r => r.project === target.id), applies: result.applies }
      } else { st.data = result; if (target.kind === 'reviewer') st.reviewer = result }
      st.edit = null
      return true
    } catch (e) { if (current === ticket) st.error = e.message; return false }
    finally { if (current === ticket) { st.busy = false; redraw() } }
  }
  async function refresh() {
    if (!st.data || st.loading || st.busy || st.edit || st.refreshing || Date.now() - st.lastRead < 15000) return
    const current = ticket, version = mutation, target = st.target
    st.refreshing = true; st.lastRead = Date.now()
    try {
      const data = await api('GET', path(target))
      if (current === ticket && version === mutation && !st.busy && !st.edit) {
        if (target.kind === 'agent' && JSON.stringify(data.agent) !== JSON.stringify(st.data.agent)) changedAgent(data.agent)
        st.data = data
      }
    } catch (e) { if (current === ticket && version === mutation) st.error = e.message }
    finally { if (current === ticket) { st.refreshing = false; redraw() } }
  }
  async function face(file = null) {
    if (st.busy || !st.target) return
    const current = ticket, target = st.target
    mutation++
    st.busy = true; st.error = ''; redraw()
    try {
      const form = new FormData(); if (file) form.append('file', file, file.name)
      const response = await fetch(`/api/agents/${enc(target.id)}/face`, { method: file ? 'PUT' : 'DELETE', ...(file ? { body: form } : {}) })
      const body = await response.json(); if (!response.ok) throw new Error(body.error)
      const data = await api('GET', path(target)); changedAgent(data.agent)
      if (current === ticket) { st.data = data; st.edit = null; st.faceMenu = false }
    } catch (e) { if (current === ticket) st.error = e.message }
    finally { if (current === ticket) { st.busy = false; redraw() } }
  }
  async function archive() {
    if (st.busy || !st.confirmArchive) return false
    const current = ticket, target = st.target
    mutation++
    st.busy = true; st.error = ''; redraw()
    try {
      const result = await api('POST', `${path(target)}/archive`, {})
      // The host answers the agent as its id, a string (found by the first-day
      // walk, 2026-11-16): spread as an object it carried no id, and Chats kept
      // showing the archived agent until a reload.
      changedAgent({ ...agentRow(result.agent), id: agentId(result.agent) ?? target.id, archived: result.archived })
      if (current === ticket) { st.data.identity.archived = result.archived; st.confirmArchive = false }
      return true
    } catch (e) { if (current === ticket) st.error = e.message; return false }
    finally { if (current === ticket) { st.busy = false; redraw() } }
  }
  async function archived() {
    st.directoryError = ''; redraw()
    try { st.archived = (await api('GET', '/agents?archived=1')).agents.filter(a => a.archived) }
    catch (e) { st.directoryError = e.message }
    redraw()
  }
  async function restore(id) {
    if (st.directoryBusy) return
    st.directoryBusy = true; st.directoryError = ''; redraw()
    try {
      const result = await api('POST', `/agents/${enc(id)}/restore`, {})
      const restored = agentId(result.agent) ?? id
      // The answer names the agent by id only; Chats needs its name and face back.
      let row = agentRow(result.agent)
      if (!row.name) { try { row = (await api('GET', `/agents/${enc(restored)}`)).agent ?? {} } catch { row = {} } }
      changedAgent({ ...row, id: restored, archived: null }); await archived()
    }
    catch (e) { st.directoryError = e.message }
    finally { st.directoryBusy = false; redraw() }
  }
  // Which agent hears when something in Loam needs attention (2026-11-19):
  // one host setting, `system.persona`; null puts the first agent back.
  async function hears(persona) {
    if (st.attentionBusy) return
    st.attentionBusy = true; st.attentionError = ''; redraw()
    try { st.attention = await api('PATCH', '/system/persona', { persona: persona || null }) }
    catch (e) { st.attentionError = e.message }
    finally { st.attentionBusy = false; redraw() }
  }
  return { st, load, directory, patch, refresh, face, archive, archived, restore, hears }
}

export function mountProfile(host, { controller = host.profiles, activity = host.profileActivity } = {}) {
  const c = controller, { st } = c
  const providerName = id => host.state.providers.find(p => p.id === id)?.name || id
  const group = (title, rows, foot = null, limitNote = null) => h('section', { class: 'profile-group', 'aria-label': title }, h('h3', { class: 'kicker', text: title }), h('div', { class: 'rows' }, rows), foot ? h('p', { class: 'footnote', text: foot }) : null, limitNote ? h('p', { class: 'footnote profile-limit-note', text: limitNote }) : null)
  const open = (ref, { folder = false } = {}) => { if (ref?.root && ref.exists !== false) host.goFiles(ref.root, ref.path || '', { isFile: !folder }) }
  const file = (title, ref, sub = null, right = null, { folder = false, unavailable = 'Not reachable from Files' } = {}) => ref?.root && ref.exists !== false
    ? settingsRow({ title, sub, right, lead: (folder ? icon.folder : icon.file)(18), onclick: () => open(ref, { folder }) })
    : h('div', { class: 'setrow profile-file-unavailable' }, h('div', { class: 'setrow-who' }, h('div', { class: 'setrow-title', text: title }), h('div', { class: 'setrow-sub', text: ref?.exists === false ? 'Not available' : unavailable })))
  function select(label, value, choices, choose, { fixed = false, hint = null, hintId = null } = {}) {
    if (fixed) return h('div', { class: 'profile-choice', key: label }, h('span', { class: 'setrow-title', text: label }), h('span', { class: 'profile-fixed', text: choices.find(o => o.id === value)?.name || value }))
    return h('label', { class: `profile-choice${label === 'Provider' ? ' profile-choice-provider' : ''}${hint ? ' profile-choice-hinted' : ''}`, key: label }, h('span', { class: 'setrow-title', text: label }),
      h('span', { class: 'settings-select' }, h('select', { 'aria-label': label, 'aria-describedby': hintId, disabled: st.busy || fixed, onchange: e => choose(e.target.value || null) },
        choices.map(o => h('option', { value: o.id ?? '', selected: (o.id ?? '') === (value ?? ''), disabled: o.disabled, text: o.name || o.label || o.id }))), icon.chevronDown(14)),
      hint ? h('span', { class: 'setrow-sub profile-choice-hint', id: hintId, text: hint }) : null)
  }
  function runs(data, kind) {
    const defaults = kind === 'agent' ? data.defaults : data
    const effective = defaults.effective || {}, provider = host.state.providers.find(p => p.id === effective.provider)
    const own = kind === 'agent' ? defaults : kind === 'engineer' ? data.own || {} : data
    const allowed = kind === 'agent' ? ['claude-code', 'codex', 'antigravity'] : data.providers || [effective.provider].filter(Boolean)
    const configured = own.provider ?? (effective.source === 'record' ? effective.provider : null)
    const inherited = effective.source === 'default' ? 'Default' : 'Automatic'
    const providerOptions = [
      { id: '', name: effective.provider ? `${inherited} · ${providerName(effective.provider)}` : 'No provider connected' },
      ...allowed.map(id => ({ id, name: providerName(id), disabled: !host.state.providers.find(p => p.id === id)?.connected })),
    ]
    const models = modelsOf(provider), efforts = effortsOf(provider, effective.model)
    const automatic = [{ id: '', name: `Default${!own.model && effective.model ? ` · ${models.find(m => m.id === effective.model)?.name || effective.model}` : ''}` }]
    const choices = [...automatic, ...models]
    if (own.model && !models.some(m => m.id === own.model)) choices.push({ id: own.model, name: own.model, disabled: true })
    const levels = [{ id: '', name: `Default${!own.effort && effective.effort ? ` · ${effective.effort}` : ''}` }, ...efforts]
    if (own.effort && !efforts.some(e => e.id === own.effort)) levels.push({ id: own.effort, name: own.effort, disabled: true })
    const applies = kind === 'agent' ? 'The default for new threads, wakes and scheduled runs. Any thread can still choose its own.'
      : kind === 'engineer' ? 'Applies to the next job. Running jobs keep their settings.' : 'Applies to the next run.'
    return group('Runs on', [
      select('Provider', configured, providerOptions, value => c.patch({ provider: value })),
      effective.provider ? select('Model', own.model, choices, value => c.patch({ model: value, ...(!effortFits(provider, value, own.effort) ? { effort: null } : {}) })) : null,
      kind === 'engineer' && effective.provider ? ['quick', 'standard', 'hard'].map(tier => {
        const setting = effective.tiers?.[tier], value = setting?.from === 'tier' ? setting.model : null
        const options = [{ id: '', name: `Default${setting?.from === 'model' && setting.model ? ` · ${models.find(m => m.id === setting.model)?.name || setting.model}` : ''}` }, ...models]
        if (value && !models.some(m => m.id === value)) options.push({ id: value, name: value, disabled: true })
        return select(tier[0].toUpperCase() + tier.slice(1), value, options, model => c.patch({ tiers: { [tier]: model } }), {
          hint: { quick: 'Small mechanical jobs', standard: 'Most work', hard: 'Design-heavy and subtle work' }[tier], hintId: `engineer-tier-${tier}-hint`,
        })
      }) : null,
      effective.provider && (kind === 'agent' || effective.provider !== 'antigravity') ? select('Thinking', own.effort, levels, value => c.patch({ effort: value })) : null,
      kind === 'engineer' && data.own && data.project !== 'default' ? settingsRow({ title: data.platform ? 'Use the built-in settings' : 'Use the default engineer settings', onclick: () => c.patch({ provider: null }) }) : null,
    ], applies, defaults.not_honoured?.[0] ?? null)
  }
  function switchRow(title, sub, value, choose) {
    return h('div', { class: 'setrow profile-switch' }, h('div', { class: 'setrow-who' }, h('div', { class: 'setrow-title', text: title }), sub ? h('div', { class: 'setrow-sub', text: sub }) : null),
      h('button', { class: 'schedule-switch', role: 'switch', 'aria-label': title, 'aria-checked': String(value), disabled: st.busy, onclick: () => choose(!value) }, h('span')))
  }
  function editor(field, value, save, { time = false, label = field, help = null } = {}) {
    if (st.edit?.field !== field) return null
    const e = st.edit
    return h('form', { class: 'profile-editor', key: `edit:${field}`, onsubmit: event => { event.preventDefault(); save(e.value) } },
      h('label', { for: `profile-${field}`, text: label }),
      h('input', { id: `profile-${field}`, class: 'field-input', type: time ? 'time' : 'text', value: e.value, disabled: st.busy, required: time, onkeydown: event => { if (event.key === 'Escape' && !st.busy) { event.preventDefault(); event.stopPropagation(); st.edit = null; st.error = ''; host.schedule() } }, maxlength: field === 'display' ? 40 : undefined, oninput: event => { e.value = event.target.value } }),
      help ? h('p', { class: 'note', text: help }) : null,
      h('div', { class: 'profile-actions' }, h('button', { class: 'pillbtn primary', type: 'submit', disabled: st.busy, text: st.busy ? 'Saving…' : 'Save' }), h('button', { class: 'pillbtn', type: 'button', disabled: st.busy, text: 'Cancel', onclick: () => { st.edit = null; st.error = ''; host.schedule() } })))
  }
  const edit = (field, value) => { if (st.busy) return; st.edit = { field, value: value || '' }; st.focusField = field; st.error = ''; host.schedule() }
  function scheduleRows(rows) {
    return rows.map(row => h('div', { class: 'profile-schedule', key: row.name },
      h('div', { class: 'profile-schedule-top' }, settingsRow({ title: row.label || row.name, sub: [row.plain || row.schedule, row.next_fire ? `Next ${when(row.next_fire)}` : 'No next run'].filter(Boolean).join(' · '), onclick: () => st.edit?.field === row.name ? (st.edit = null, host.schedule()) : edit(row.name, row.schedule) }),
        h('button', { class: 'schedule-switch', role: 'switch', 'aria-label': `${row.label || row.name} enabled`, 'aria-checked': String(row.enabled), disabled: st.busy, onclick: () => c.patch({ enabled: !row.enabled }, `/schedules/${enc(st.target.id)}/${enc(row.name)}`) }, h('span'))),
      st.edit?.field === row.name ? h('div', {}, h('p', { class: 'note', text: row.last_fire ? `Last run ${when(row.last_fire.ts, true)} · ${row.last_fire.outcome || 'Result unknown'}` : 'No run yet' }),
        editor(row.name, row.schedule, value => c.patch({ schedule: value }, `/schedules/${enc(st.target.id)}/${enc(row.name)}`), { label: 'Schedule (PT)', help: 'Daily, weekdays, weekends, or Mondays and Thursdays at 05:00 PT.' }), row.file ? file('Instructions', row.file) : null) : null))
  }
  function pluginRows(plugin, agent) {
    return group(plugin.name, plugin.fields.map(field => {
      const save = value => c.patch({ values: { [field.id]: value } }, `/plugins/${enc(plugin.id)}/agents/${enc(agent)}`)
      if (field.choices?.length) return select(field.label, plugin.values?.[field.id] ?? field.default, field.choices, save)
      return h('label', { class: 'profile-choice', key: field.id }, h('span', { class: 'setrow-title', text: field.label }), h('input', { class: 'field-input', 'aria-label': field.label, maxlength: 200, value: plugin.values?.[field.id] ?? field.default ?? '', disabled: st.busy, onchange: e => save(e.target.value) }))
    }))
  }
  function recent(agent, project = null) {
    return group(project ? 'Recent jobs' : 'Recent', [
      activity.view({ embedded: true, compact: !st.activity, limit: st.activity ? null : 4, project }),
      !st.activity ? settingsRow({ title: project ? 'All jobs' : 'All activity', onclick: () => { st.activity = true; host.schedule() } }) : null,
    ])
  }
  function body(agent) {
    if (st.target?.kind !== 'agent' || st.target.id !== agent) return [h('p', { role: 'status', text: 'Loading profile…' })]
    if (!st.data) return loading()
    const data = st.data, look = agentLook(agent, host.state.agents), name = data.identity.display
    const isArchived = Boolean(data.identity.archived)
    const threads = host.threads.st.threads.filter(t => t.default === agent || t.members?.some(m => m.persona === agent))
    const pageSize = 12, pages = Math.max(1, Math.ceil(threads.length / pageSize))
    st.page = Math.min(st.page, pages)
    const tabs = ['Threads', 'Activity', 'Schedules', 'Settings', 'Files']
    const chooseTab = tab => { st.tab = tab; st.edit = null; st.faceMenu = false; st.menu = false; host.schedule() }
    const faceSrc = data.identity.face === null ? null : data.identity.face?.url?.replace(/^\/v1\//, '/api/') || look.imageSrc
    const upload = h('input', { type: 'file', accept: 'image/png,image/jpeg,image/webp,image/gif', hidden: true, 'aria-label': 'Upload face', onchange: e => { const f = e.target.files[0]; if (f) c.face(f); e.target.value = '' } })
    const files = [file('Open workspace', data.files.workspace, null, null, { folder: true }), file('Soul', data.files.soul), file('Config', data.files.config),
      group('Memory', Object.entries(data.files.memory || {}).map(([key, ref]) => file(({ map: 'Every run', folder: 'Memory folder' })[key] || key, ref, null, null, { folder: key === 'folder' }))),
      file('Skills', data.files.skills, null, h('span', { class: 'setrow-value', text: String(data.files.skills?.count ?? '') }), { folder: true })]
    return [h('div', { class: 'agent-profile', key: `agent-profile:${agent}` },
      h('header', { class: 'profile-hero' },
        upload,
        h('div', { class: 'profile-identity' },
          h('button', { class: 'profile-face', disabled: st.busy, 'data-profile-face': c === host.profiles ? 'page' : 'settings', 'aria-label': `Change ${name}’s face`, 'aria-haspopup': 'dialog', 'aria-expanded': String(Boolean(st.faceMenu)), onclick: () => { st.menu = false; st.faceMenu = !st.faceMenu; host.schedule() } }, blob({ ...look, imageSrc: faceSrc }, { size: 64, font: 28 })),
          h('div', {}, st.edit?.field === 'display' ? editor('display', name, value => c.patch({ display: value }), { label: 'Display name' }) : h('button', { class: 'profile-name display', disabled: st.busy, 'aria-label': `Rename ${name}`, text: name, onclick: () => edit('display', name) }),
            h('p', { class: 'profile-status note', text: isArchived ? 'Archived' : profileStatus(data.status) })),
          h('button', { class: 'iconbtn profile-menu-button', 'data-profile-actions': c === host.profiles ? 'page' : 'settings', 'aria-label': 'Agent actions', 'aria-haspopup': 'menu', 'aria-expanded': String(Boolean(st.menu)), onclick: () => { st.faceMenu = false; st.menu = !st.menu; host.schedule() } }, icon.dots(20))),
        st.menu ? menuSurface(h('div', { class: 'popover profile-actions-menu', role: 'menu', key: 'profile-menu' },
          settingsRow({ title: 'Archive agent', onclick: () => { st.menu = false; st.confirmArchive = true; host.schedule() }, props: { role: 'menuitem', disabled: st.busy || isArchived } })),
          { title: 'Agent actions', heading: false, anchored: true, width: 220, anchor: `[data-profile-actions="${c === host.profiles ? 'page' : 'settings'}"]`, close: () => { st.menu = false; host.schedule() } }) : null,
        st.faceMenu ? menuSurface(h('div', { class: 'popover profile-face-menu', key: 'profile-face-menu' },
          h('div', { class: 'profile-face-preview', 'aria-hidden': 'true' },
            blob({ ...look, imageSrc: faceSrc }, { size: 96, font: 40 })),
          h('div', { class: 'popdiv' }),
          h('div', { class: 'profile-face-menu-actions', role: 'menu', 'aria-label': `${name}’s face options` },
            settingsRow({ title: 'Upload a picture', lead: icon.image(18), onclick: () => { st.faceMenu = false; upload.click(); host.schedule() }, props: { role: 'menuitem', disabled: st.busy } }),
            data.identity.face ? settingsRow({ title: 'Use the theme’s face', lead: icon.refresh(18), onclick: () => { st.faceMenu = false; c.face() }, props: { role: 'menuitem', disabled: st.busy } }) : null)),
          { title: `${name}’s face`, heading: false, width: 280, anchor: `[data-profile-face="${c === host.profiles ? 'page' : 'settings'}"]`, close: () => { st.faceMenu = false; host.schedule() } }) : null,
        st.soul ? h('blockquote', { class: 'profile-soul', cite: data.files.soul?.path }, h('p', { text: st.soul }), h('cite', { text: 'From the soul' })) : null,
        st.soulError ? h('p', { class: 'note', role: 'status', text: st.soulError }) : null,
        h('div', { class: 'profile-actions' },
          h('button', { class: `pillbtn primary${!st.edit && !st.confirmArchive && !(st.tab === 'Schedules' && st.manageSchedules) ? '' : ' quiet-primary'}`, disabled: isArchived, onclick: () => host.threads.create(agent) }, icon.plus(16), 'New thread'),
          h('button', { class: 'pillbtn', text: 'Read soul', disabled: !data.files.soul?.root || data.files.soul.exists === false, onclick: () => open(data.files.soul) }),
          h('button', { class: 'pillbtn', text: 'Change by talking', disabled: isArchived, onclick: async () => { const pending = host.threads.create(agent), opening = host.threads.st.open; await pending; if (host.threads.st.open !== opening || opening?.createFailed) return; const field = host.draft; if (field) { field.value = 'I’d like to change your soul. '; field.focus(); host.schedule() } } })),
        ...notices(),
        st.confirmArchive ? h('div', { class: 'profile-archive-confirm', role: 'alertdialog', 'aria-label': `Archive ${name}?` },
          h('p', { text: `${name} will leave Chats, pickers and mentions. Schedules will stop. Threads and files stay. You can restore this agent from Settings → Agents → Archived.` }),
          h('div', { class: 'profile-actions' }, h('button', { class: 'pillbtn danger', disabled: st.busy, text: st.busy ? 'Archiving…' : 'Archive', onclick: async () => { const target = st.target; if (await c.archive() && st.target === target && (c === host.profiles ? host.state.screen === 'profile' && host.state.profileAgent === agent : host.state.sheet?.detail?.id === agent)) { host.state.sheet = null; host.goHome() } } }), h('button', { class: 'pillbtn', disabled: st.busy, text: 'Cancel', onclick: () => { st.confirmArchive = false; host.schedule() } }))) : null),
      h('div', { class: 'profile-tabs', role: 'tablist', 'aria-label': `${name} profile` }, tabs.map(tab => h('button', { class: 'profile-tab', role: 'tab', id: `profile-tab-${tab}`, 'aria-selected': String(st.tab === tab), 'aria-controls': 'profile-content', tabindex: st.tab === tab ? '0' : '-1', text: tab, onclick: () => chooseTab(tab), onkeydown: e => { if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) { e.preventDefault(); const at = tabs.indexOf(tab); const next = e.key === 'Home' ? tabs[0] : e.key === 'End' ? tabs.at(-1) : tabs[(at + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length]; chooseTab(next); queueMicrotask(() => document.getElementById(`profile-tab-${next}`)?.focus()) } } }))),
      h('div', { class: 'profile-tab-content', key: st.tab, role: 'tabpanel', id: 'profile-content', 'aria-labelledby': `profile-tab-${st.tab}`, tabindex: '0' },
        st.tab === 'Threads' ? [threads.length ? h('div', { class: 'rows profile-threads' }, threads.slice((st.page - 1) * pageSize, st.page * pageSize).map(t => host.threads.row(t))) : emptyState({ object: 'speech', size: 'small', level: 'h3', title: 'No threads yet', body: `Threads with ${name} show up here.`, props: { class: 'profile-empty' } }),
          pages > 1 ? h('nav', { class: 'profile-pagination', 'aria-label': 'Thread pages' }, h('button', { class: 'pillbtn', disabled: st.page === 1, text: 'Previous', onclick: () => { st.page--; host.schedule() } }), h('span', { text: `${st.page} / ${pages}` }), h('button', { class: 'pillbtn', disabled: st.page === pages, text: 'Next', onclick: () => { st.page++; host.schedule() } })) : null]
        : st.tab === 'Activity' ? activity.view({ embedded: true })
        : st.tab === 'Schedules' ? [group('Schedules', [data.schedule.length ? scheduleRows(data.schedule) : h('p', { class: 'note profile-empty', text: 'No schedules yet.' }), settingsRow({ title: 'Manage schedules', onclick: () => { host.schedules.load(agent, { seen: true }); st.manageSchedules = true; host.schedule() } })]), st.manageSchedules ? host.schedulePanel(host.schedules, name) : null]
        : st.tab === 'Settings' ? [runs(data, 'agent'), ...data.plugins.filter(p => p.on && p.fields?.length && host.pluginLoader?.st.rows.find(row => row.effective && row.id === p.id)?.on !== false).flatMap(p => {
          const custom = host.registry.entries('agent.profile').filter(entry => entry.owner === p.id)
          return custom.length ? custom.flatMap(entry => host.registry.invoke(entry, 'render', { agent, plugin: p, busy: st.busy, error: st.error,
            save: values => c.patch({ values }, `/plugins/${enc(p.id)}/agents/${enc(agent)}`),
            defaultView: (fields = p.fields) => pluginRows({ ...p, fields }, agent) })) : [pluginRows(p, agent)]
        })] : files))]
  }
  function notices() { return [st.error ? h('p', { class: 'schedule-error', role: 'alert', text: st.error }) : null, st.busy ? h('p', { class: 'note', role: 'status', text: st.confirmArchive ? 'Archiving… This can take up to 20 minutes. Keep this page open.' : 'Saving…' }) : null] }
  function loading() { return [...notices(), st.loading ? h('p', { class: 'note', role: 'status', text: 'Loading…' }) : h('button', { class: 'pillbtn', text: 'Try again', onclick: () => c.load(st.target.kind, st.target.id) })] }
  function settingsDetail() {
    const { target, data } = st
    return [h('button', { class: 'backbtn', onclick: () => { host.state.sheet = { kind: 'settings', page: 'agents' }; c.directory(); host.schedule() } }, icon.chevronLeft(18), 'Agents'),
      ...(!data ? loading() : [host.settingsHead(target.kind === 'engineer' ? data.project === 'default' ? 'Every other project' : `${data.project} engineer` : data.name), ...notices(),
        ...(target.kind === 'engineer' ? [runs(data, 'engineer'), data.project === 'default' ? h('p', { class: 'footnote', text: 'Used by projects without their own settings.' }) : null,
          data.deploy_on_land != null ? group('When a job lands', switchRow(data.deploy_label || 'Deploy when a job lands', null, data.deploy_on_land, value => c.patch({ deploy_on_land: value }))) : null,
          data.project !== 'default' ? group('Instructions', file('Project instructions', data.instructions, null, null, { unavailable: "Kept in the project's code, outside Files" })) : null,
          data.project !== 'default' ? recent(null, data.project) : null] : [
          group('Schedule', [settingsRow({ title: data.plain || data.schedule, sub: data.next_fire ? `Next ${when(data.next_fire)}` : 'No next run', onclick: () => edit('reviewer-time', /at (\d\d:\d\d)/.exec(data.schedule)?.[1] || '04:20') }),
            editor('reviewer-time', '', value => c.patch({ schedule: data.schedule.replace(/at \d\d:\d\d/, `at ${value}`) }), { time: true, label: 'Time (PT)' }),
            settingsRow({ title: 'Recurrence', sub: data.schedule, onclick: () => edit('reviewer-schedule', data.schedule) }), editor('reviewer-schedule', '', value => c.patch({ schedule: value }), { label: 'Schedule (PT)', help: 'Daily, weekdays, weekends, or Mondays and Thursdays at 05:00 PT.' })]),
          runs(data, 'reviewer'), group('Last run', h('p', { class: 'note profile-empty', text: when(data.last_fire, true) || 'No run yet' })), group('Instructions', file('Reviewer instructions', data.instructions)),
        ])])]
  }
  return { body, settingsDetail, syncFocus() {
    if (!st.focusField) return
    const input = globalThis.document?.getElementById(`profile-${st.focusField}`)
    if (!input) return
    input.focus({ preventScroll: true }); if (input.type === 'text') input.select?.()
    st.focusField = null
  } }
}
