// Settings › Accounts: API keys by provider, entered once (2026-11-18).
//
// The one place a person goes for keys. Each provider (OpenAI, Google,
// Hetzner, and any a plugin declares) holds one shared key; every plugin that
// declared that provider in its PLUGIN.md uses it, unless the person gives
// that plugin a different key of its own. The host checks a key before it is
// kept and never sends one back: the page sees whether a key is set, where it
// came from, its last check, and at most the last four characters.
//
// A pasted value lives in its field until the form is sent, and the field is
// cleared before the request goes. Nothing here persists a value.
import { h, icon } from './ui.js'
import { providerMark } from './marks.js'
import { formatDate } from './time.js'

// The marks Accounts draws: the OpenAI knot is the Codex mark's own path, so
// it is that mark; the rest are drawn as their first letter on the tile.
const MARK = { openai: 'codex', google: 'gemini' }
const SPARK = 'M12 1.5c.35 5.6 4.9 10.15 10.5 10.5-5.6.35-10.15 4.9-10.5 10.5C11.65 16.9 7.1 12.35 1.5 12 7.1 11.65 11.65 7.1 12 1.5z'

function mark(id, size = 20) {
  if (id === 'google') {
    const ns = 'http://www.w3.org/2000/svg', root = document.createElementNS(ns, 'svg'), path = document.createElementNS(ns, 'path')
    for (const [k, v] of Object.entries({ width: size, height: size, viewBox: '0 0 24 24', fill: 'currentColor', 'aria-hidden': 'true', class: 'mark', 'data-mark': 'google' })) root.setAttribute(k, v)
    path.setAttribute('d', SPARK)
    root.append(path)
    return root
  }
  return providerMark(MARK[id] ?? id, size, (id[0] ?? '?').toUpperCase())
}

export function names(list) {
  return list.length < 2 ? list.join('') : `${list.slice(0, -1).join(', ')} and ${list.at(-1)}`
}

// One line under a provider's name in the list.
export function providerLine(p) {
  const users = p.plugins.map(u => u.name)
  const needs = p.plugins.filter(u => u.on && !u.has_key && !u.optional).map(u => u.name)
  if (!p.set) return needs.length ? `No key · ${names(needs)} needs one` : users.length ? `No key · for ${names(users)}` : 'No key'
  const failed = p.checked && p.checked.ok === false
  const head = failed ? 'Key refused when last checked' : p.source === 'box' ? 'Set on this server' : 'Key saved'
  return users.length ? `${head} · ${names(users)}` : head
}

// Whether the Accounts row should carry the "needs you" dot.
export function providerWarn(p) {
  return Boolean((p.set && p.checked?.ok === false) || p.plugins.some(u => u.on && !u.has_key && !u.optional))
}

export function createApiKeys({ api, changed = () => {}, openPlugin = null }) {
  const st = { list: null, loading: false, error: '', busy: '', message: '', failure: '', editing: null, plugin: null, provider: null }
  let loadedAt = 0

  async function load(force = false) {
    if (st.loading || (!force && st.list && Date.now() - loadedAt < 15000)) return
    st.loading = true; changed()
    try { st.list = (await api('GET', '/keys')).providers ?? []; st.error = ''; loadedAt = Date.now() }
    catch (e) { st.error = [404, 405].includes(e.status) ? 'This server does not hold shared keys yet. Update Loam to use Accounts.' : e.message }
    finally { st.loading = false; changed() }
  }

  function keep(answer) {
    if (answer?.id && st.list) st.list = st.list.map(p => p.id === answer.id ? answer : p)
  }

  async function act(slot, method, path, body, done) {
    if (st.busy) return
    st.busy = slot; st.failure = ''; st.message = ''; changed()
    try {
      const out = await api(method, path, body)
      keep(out)
      st.message = done(out) ?? ''
      st.editing = null; st.plugin = null
    } catch (e) { st.failure = e.body?.error || e.message }
    finally { st.busy = ''; changed() }
  }

  // The values of a key form, read and cleared before anything is sent.
  function take(form) {
    const values = {}
    for (const input of form.querySelectorAll('.keys-input')) {
      if (input.value.trim()) values[input.getAttribute('data-field')] = input.value
      input.value = ''
    }
    return values
  }

  const save = (p, form) => {
    const values = take(form)
    if (!Object.keys(values).length) { st.failure = 'Paste the key first.'; changed(); return }
    return act(`save:${p.id}`, 'PUT', `/keys/${encodeURIComponent(p.id)}`, { values }, out => out.checked?.why ?? 'Saved.')
  }
  const saveOwn = (p, u, form) => {
    const values = take(form)
    if (!Object.keys(values).length) { st.failure = 'Paste the key first.'; changed(); return }
    return act(`own:${u.id}`, 'PUT', `/keys/${encodeURIComponent(p.id)}/plugins/${encodeURIComponent(u.id)}`, { values }, () => `${u.name} now uses its own key.`)
  }
  const useShared = (p, u) => act(`shared:${u.id}`, 'DELETE', `/keys/${encodeURIComponent(p.id)}/plugins/${encodeURIComponent(u.id)}`, undefined, () => `${u.name} uses the shared key again.`)
  const recheck = p => act(`check:${p.id}`, 'POST', `/keys/${encodeURIComponent(p.id)}/check`, {}, out => out.checked?.why ?? '')
  const forget = p => act(`forget:${p.id}`, 'DELETE', `/keys/${encodeURIComponent(p.id)}`, undefined, out => out.set ? 'Forgotten here. The key set on this server is still used.' : 'Key forgotten.')

  function fields(p, prefix, busy) {
    return p.fields.map(f => h('label', { class: 'keys-field', key: f.name },
      h('span', { class: 'keys-field-label', text: p.fields.length > 1 || f.label !== 'API key' ? `${f.label}${f.optional ? ' (optional)' : ''}` : 'API key' }),
      h('input', { class: 'field-input keys-input', type: f.secret === false ? 'text' : 'password', 'data-field': f.name, id: `${prefix}-${f.name}`,
        autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false', placeholder: f.secret === false ? (p.shown?.[f.name] ?? '') : 'Paste a key',
        'aria-label': `${p.name} ${f.label}`, disabled: busy })))
  }

  function keyForm(p, { own = null } = {}) {
    const slot = own ? `own:${own.id}` : `save:${p.id}`, busy = st.busy === slot
    return h('form', { class: 'keys-form', onsubmit: e => { e.preventDefault(); own ? saveOwn(p, own, e.currentTarget) : save(p, e.currentTarget) } },
      ...fields(p, own ? `key-${p.id}-${own.id}` : `key-${p.id}`, Boolean(st.busy)),
      h('div', { class: 'keys-actions' },
        h('button', { class: 'pillbtn primary', type: 'submit', disabled: Boolean(st.busy), text: busy ? 'Checking…' : 'Save key' }),
        (own ? st.plugin === own.id : st.editing === p.id) ? h('button', { class: 'pillbtn', type: 'button', disabled: Boolean(st.busy), text: 'Cancel',
          onclick: () => { st.editing = null; st.plugin = null; st.failure = ''; changed() } }) : null,
        !own && p.where ? h('a', { class: 'quietlink keys-where', href: p.where, target: '_blank', rel: 'noopener noreferrer' }, 'Get a key', icon.external(12)) : null))
  }

  function status(p) {
    if (!p.set) return 'No key yet.'
    const shown = Object.values(p.shown ?? {}).filter(v => v && v !== 'set')
    const where = p.source === 'box' ? 'Set on this server' : 'Saved'
    return shown.length ? `${where} · ${shown.join(' · ')}` : where
  }

  function checkedLine(rec) {
    if (!rec?.why) return null
    const when = rec.at ? formatDate(rec.at, { month: 'short', day: 'numeric' }) : null
    return h('p', { class: `note keys-check${rec.ok ? '' : ' warn'}`, text: when ? `${rec.why} · ${when}` : rec.why })
  }

  function usedBy(p) {
    if (!p.plugins.length) return h('p', { class: 'note keys-unused', text: 'No plugin uses this key yet. A plugin that needs it will say so before you install it.' })
    return h('section', { class: 'keys-users', 'aria-label': 'Used by' },
      h('h3', { class: 'kicker', text: 'Used by' }),
      h('div', { class: 'rows' }, ...p.plugins.map(u => {
        const open = st.plugin === u.id, own = u.uses === 'own'
        return h('div', { class: `keys-user${open ? ' open' : ''}`, key: u.id },
          h('button', { class: 'setrow', type: 'button', 'aria-expanded': String(open), onclick: () => { st.plugin = open ? null : u.id; st.failure = ''; st.message = ''; changed() } },
            h('div', { class: 'setrow-who' },
              h('div', { class: 'setrow-title', text: u.name }),
              h('div', { class: 'setrow-sub', text: u.why || (u.on ? 'On' : 'Off') })),
            h('span', { class: `keys-badge${own ? ' own' : ''}${!u.has_key ? ' none' : ''}`, text: !u.has_key ? 'No key' : own ? 'Its own key' : 'Shared key' }),
            icon.chevronDown(12)),
          open ? h('div', { class: 'keys-user-detail' },
            own ? h('p', { class: 'note', text: `${u.name} uses a key of its own, not the shared one.` })
              : h('p', { class: 'note', text: `${u.name} uses the shared ${p.name} key. Give it a different key to keep its use apart.` }),
            checkedLine(u.checked),
            keyForm(p, { own: u }),
            own ? h('button', { class: 'pillbtn', type: 'button', disabled: Boolean(st.busy), text: st.busy === `shared:${u.id}` ? 'Switching…' : 'Use the shared key', onclick: () => useShared(p, u) }) : null,
            openPlugin ? h('button', { class: 'quietlink', type: 'button', text: `${u.name} settings`, onclick: () => openPlugin(u.id) }) : null) : null)
      })))
  }

  function detail(p, { back }) {
    const editing = !p.set || st.editing === p.id
    return [back,
      h('div', { class: 'provider-hero keys-hero' },
        h('div', { class: `icon-tile glyph keys-mark ${p.id}` }, mark(p.id)),
        h('div', { class: 'sheet-head' },
          h('div', { class: 'display sheet-title', role: 'heading', 'aria-level': '2', tabindex: '-1', text: p.name }),
          h('div', { class: 'note', text: status(p) }))),
      h('section', { class: 'card keys-card', 'aria-label': `${p.name} key` },
        editing ? keyForm(p) : [
          h('div', { class: 'keys-checkrow' },
            checkedLine(p.checked) ?? h('p', { class: 'note keys-check', text: p.source === 'box' ? 'Set in this server’s own settings file.' : 'Not checked yet.' }),
            p.checks ? h('button', { class: 'quietlink keys-recheck', type: 'button', disabled: Boolean(st.busy), text: st.busy === `check:${p.id}` ? 'Checking…' : 'Check again', onclick: () => recheck(p) }) : null),
          h('div', { class: 'keys-actions' },
            h('button', { class: 'pillbtn secondary', type: 'button', disabled: Boolean(st.busy), text: 'Replace key', onclick: () => { st.editing = p.id; st.failure = ''; st.message = ''; changed() } }),
            p.source === 'saved' ? h('button', { class: 'pillbtn', type: 'button', disabled: Boolean(st.busy), text: st.busy === `forget:${p.id}` ? 'Forgetting…' : 'Forget', onclick: () => forget(p) }) : null)],
        p.refused?.why && p.refused.why !== p.checked?.why && editing ? h('p', { class: 'note warn', text: p.refused.why }) : null,
        st.message ? h('p', { class: 'note keys-done', role: 'status', text: st.message }) : null,
        st.failure ? h('p', { class: 'note warn', role: 'alert', text: st.failure }) : null),
      usedBy(p)]
  }

  // `provider` set: that provider's page; else the list.
  function view({ provider = null, back = null, open = () => {}, head = null }) {
    if (!st.list && !st.loading && !st.error) load()
    if (provider) {
      const p = st.list?.find(item => item.id === provider)
      if (p) return detail(p, { back })
      return [back, h('p', { class: 'reason', text: st.loading || !st.list ? 'Reading keys…' : 'This server no longer lists that provider.' })]
    }
    return [head,
      st.error ? h('p', { class: 'reason', role: 'alert', text: st.error }) : null,
      st.list ? h('div', { class: 'rows keys-list' }, ...st.list.map(p => h('button', { class: 'setrow', type: 'button', key: p.id, onclick: () => { st.editing = null; st.plugin = null; st.failure = ''; st.message = ''; open(p.id) } },
        h('div', { class: `icon-tile glyph keys-mark ${p.id}` }, mark(p.id)),
        h('div', { class: 'setrow-who' }, h('div', { class: 'setrow-title', text: p.name }), h('div', { class: 'setrow-sub', text: providerLine(p) })),
        providerWarn(p) ? h('span', { class: 'navdot', title: 'Needs you' }) : null,
        icon.chevronRight(10)))) : h('p', { class: 'note', role: 'status', text: 'Reading keys…' }),
      st.list ? h('p', { class: 'footnote keys-foot', text: 'Keys stay on this server. Each goes only to the plugins that asked for it, and nobody can read one back. Your agents can save one for you.' }) : null]
  }

  return { st, load, view, warn: () => (st.list ?? []).some(providerWarn), summary: () => {
    if (!st.list) return 'API keys for your plugins'
    const saved = st.list.filter(p => p.set).length
    return saved ? `${saved} ${saved === 1 ? 'key' : 'keys'}` : 'API keys for your plugins'
  } }
}
