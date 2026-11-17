// Shared with me: docs other people shared from their own Loams, kept by
// this Loam's server (`/x/doc-comments/withme`) and opened in Loam's own
// editor. The page never talks to the other Loam and never holds a
// password after it is typed: the server does both (server/doccomments/
// withme.py). This module draws the section under "Shared by me", its rows
// and their menu, and the sheet that adds one.

import { bottomSheet, grab } from './sheet.js'
import { h, icon, put, tone } from './editor/dom.js'
import { how, openAppearSheet } from './appear.js'

export const WITHME_PREFIX = 'with-me/'
const MODE = { view: 'View', comment: 'Comment', edit: 'Edit' }

export function withmePath(id) { return `${WITHME_PREFIX}${id}.md` }
export function withmeId(path) { return /^with-me\/(w-[a-z0-9]{10})\.md$/.exec(path || '')?.[1] || null }

export function createWithMe({ base, root, schedule, toast, files, side, agentName = () => '' }) {
  const st = { docs: [], loaded: false, loading: false, error: '', fresh: new Set(), leaving: new Map(), appear: null }

  async function req(method, route, body) {
    let res
    try {
      res = await fetch(`${base}${route}`, {
        method, credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined,
      })
    } catch {
      throw Object.assign(new Error('Your Loam didn’t answer. Check your connection and try again.'), { code: 'offline' })
    }
    const data = await res.json().catch(() => null)
    if (!res.ok) throw Object.assign(new Error(data?.error || `Your Loam answered ${res.status}.`), { code: data?.code, status: res.status, suggest: data?.suggest })
    return data
  }

  async function load(refresh = true) {
    if (st.loading) return st.loadingPromise
    st.loading = true
    st.loadingPromise = req('GET', `withme${refresh ? '' : '?refresh=0'}`).then((got) => {
      st.docs = got.docs || []
      if (got.appear) st.appear = { appear: got.appear, owner: got.owner, alias: got.alias }
      st.loaded = true
      st.error = ''
    }).catch((err) => { st.error = err.message; st.loaded = true }).finally(() => { st.loading = false; schedule() })
    return st.loadingPromise
  }

  const byId = (id) => st.docs.find(d => d.id === id) || null
  const rowOf = (d) => ({ kind: 'file', root, path: withmePath(d.id), name: d.title, place: `Shared with me · ${d.owner ? `${d.owner}’s Loam` : d.host}`, capabilities: {}, withme: d })

  // -- the section ----------------------------------------------------------------------

  // Drawn with the app's own `h` (`H`), keyed, so its reconciler keeps the
  // rows as they are between draws (the entrance plays once).
  function section({ entry, go, H = h }) {
    const list = st.docs.map(d => ({ d, leaving: false }))
    for (const { doc, at } of st.leaving.values()) list.splice(Math.min(at, list.length), 0, { d: doc, leaving: true })
    const rows = list.map(({ d, leaving }) => row(d, { entry, go, leaving, H }))
    const add = H('button', { class: `setrow dc-wm-add${st.docs.length ? '' : ' alone'}`, type: 'button', key: 'wm:add', onclick: () => openAdd() },
      H('div', { class: 'icon-tile dc-wm-addtile' }, icon('plus', 16)),
      H('div', { class: 'fs-words' },
        H('div', { class: 'fs-name', text: 'Add a shared doc' }),
        st.docs.length ? null : H('div', { class: 'fs-meta', text: 'Someone shared a doc from their Loam? Paste its link and password to open it here.' })))
    return H('div', { class: 'card card-group dc-sd dc-wm', key: 'wm:list' }, ...rows, add)
  }

  function metaFor(d) {
    if (d.state === 'gone') return 'No longer shared'
    if (d.state === 'unreachable') return `Couldn’t reach ${d.host} · tries again when opened`
    if (d.state === 'password') return 'Its password no longer opens it'
    if (d.state === 'version') return 'That Loam is a version this one can’t open'
    const whose = d.owner ? `${d.owner}’s Loam` : d.host
    return [whose, MODE[d.mode] || 'View'].join(' · ')
  }

  function row(d, { entry, go, leaving, H = h }) {
    const r = rowOf(d)
    const dead = d.state !== 'ok'
    const extras = {
      meta: metaFor(d),
      badge: d.new && !dead ? H('span', { class: 'dc-sd-dot', key: `wmdot:${d.id}`, role: 'img', 'aria-label': 'Something new' }) : null,
      onopen: () => { d.new = false },
      menu: () => menuItems(d, go),
      className: `dc-wm-entry${dead ? ' dc-wm-dead' : ''}${st.fresh.has(d.id) ? ' dc-wm-arrive' : ''}${leaving ? ' dc-sd-leaving' : ''}`,
    }
    if (typeof entry === 'function') return entry(r, extras)
    // An older core with no shared row: a plain row opening by address.
    return H('button', { class: 'setrow', key: `wm:${d.id}`, onclick: () => go(root, r.path, { isFile: true, row: r }) },
      H('div', { class: 'fs-words' }, H('div', { class: 'fs-name', text: d.title }), H('div', { class: 'fs-meta', text: extras.meta })))
  }

  function menuItems(d, go) {
    const r = rowOf(d)
    const sideOk = side?.()?.available?.()
    return [
      { id: 'open', label: 'Open', icon: (s) => icon('file', s), run: () => go(root, r.path, { isFile: true, row: r }) },
      sideOk ? { id: 'side', label: 'Open to the side', icon: 'panelRight', run: () => side().openSide({ kind: 'file', root, path: r.path, row: r }) } : null,
      { id: 'copy', label: 'Copy link', icon: (s) => icon('link', s), run: () => copyLink(d) },
      st.appear ? { id: 'appear', label: 'How your agents appear', icon: (s) => icon('people', s), sub: appearSub(d), run: () => chooseAppear(d) } : null,
      { id: 'remove', label: 'Remove', icon: (s) => icon('trash', s), danger: true, sub: 'Forgets the link and password here. Nothing changes for the owner.', run: () => remove(d) },
    ].filter(Boolean)
  }

  // How the owner's agents appear on this doc, there: by their own names or
  // as one, "<the name on the link>'s agent". Only what is sent from now on
  // follows a change; the sheet says so.
  const aliasFor = (d) => `${d.name || st.appear?.owner || 'Your'}'s agent`
  const effective = (d) => (d.appear === 'names' || d.appear === 'one' ? d.appear : st.appear?.appear || 'names')
  function appearSub(d) {
    const words = how(effective(d), aliasFor(d))
    return words.charAt(0).toUpperCase() + words.slice(1) + (d.appear === 'names' || d.appear === 'one' ? '' : ', as usual')
  }

  function chooseAppear(d) {
    openAppearSheet({
      title: d.title, there: d.owner ? `${d.owner}’s Loam` : d.host,
      value: d.appear || 'default', def: st.appear?.appear || 'names', alias: aliasFor(d), agent: agentName(),
      onChoose: async (v) => {
        try {
          const got = await req('PATCH', `withme/${d.id}`, { appear: v })
          Object.assign(d, got.doc)
          st.docs = st.docs.map(x => (x.id === d.id ? d : x))
          schedule()
          toast?.(`Your agents appear ${how(effective(d), aliasFor(d))} on “${d.title}” from now on`)
          return true
        } catch (err) { toast?.(err.message); return false }
      },
    })
  }

  async function copyLink(d) {
    try { await navigator.clipboard.writeText(d.url); toast?.('Link copied. Its password stays with your Loam.') } catch { toast?.(d.url) }
  }

  async function remove(d) {
    try {
      await req('DELETE', `withme/${d.id}`)
    } catch (err) { toast?.(err.message); return }
    const at = st.docs.findIndex(x => x.id === d.id)
    st.docs = st.docs.filter(x => x.id !== d.id)
    st.leaving.set(d.id, { doc: d, at })
    setTimeout(() => { st.leaving.delete(d.id); schedule() }, 340)
    schedule()
    toast?.(`Removed “${d.title}” from Shared with me`)
  }

  // -- the add sheet ----------------------------------------------------------------------

  let scrim = null
  let bs = null
  function openAdd() {
    if (scrim) return
    const s = { link: '', password: '', name: '', needName: false, suggest: '', busy: false, error: '', code: '' }
    scrim = h('div', { class: 'dc-dlg-scrim dc-wm-scrim', onclick: (e) => { if (e.target === scrim) close() } })
    bs = bottomSheet(scrim, { close })
    document.body.append(scrim)
    requestAnimationFrame(() => scrim?.classList.add('on'))
    document.addEventListener('keydown', onKey)
    draw(s, true)
  }

  function onKey(e) { if (e.key === 'Escape') close() }

  function close() {
    if (!scrim) return
    const node = scrim
    scrim = null
    bs?.dispose()
    bs = null
    node.classList.remove('on')
    setTimeout(() => node.remove(), 320)
    document.removeEventListener('keydown', onKey)
  }

  function draw(s, focus = false) {
    if (!scrim) return
    const linkIn = h('input', { class: 'dc-wm-in', type: 'text', inputmode: 'url', autocapitalize: 'off', placeholder: 'https://…/s/…', value: s.link, autocomplete: 'off', spellcheck: 'false', 'aria-label': 'Link' })
    const pwIn = h('input', { class: 'dc-wm-in mono', type: 'text', placeholder: 'Password', value: s.password, autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false', 'aria-label': 'Password' })
    const nameIn = s.needName ? h('input', { class: 'dc-wm-in', type: 'text', placeholder: 'Your name there', value: s.name, maxlength: '60', autocomplete: 'name', 'aria-label': 'Your name there' }) : null
    // A whole invite pasted anywhere fills both: the link, and the password
    // on its "Password:" line.
    const fill = (text) => {
      const link = /https:\/\/\S+?\/s\/[A-Za-z0-9_-]{32,64}/.exec(text)?.[0]
      const pw = /^\s*password\s*[:：]\s*(\S+)\s*$/im.exec(text)?.[1]
      if (!link && !pw) return false
      if (link) s.link = link
      if (pw) s.password = pw
      s.error = ''
      draw(s)
      ;(s.password ? (s.needName ? scrim?.querySelector('.dc-wm-name') : scrim?.querySelector('.dc-wm-go')) : scrim?.querySelector('.dc-wm-in.mono'))?.focus()
      return true
    }
    for (const input of [linkIn, pwIn]) {
      input.addEventListener('paste', (e) => {
        const text = e.clipboardData?.getData('text') || ''
        if (/\n/.test(text) && fill(text)) e.preventDefault()
      })
    }
    linkIn.addEventListener('input', () => { s.link = linkIn.value; if (/\n|password/i.test(s.link)) fill(s.link); clearError() })
    pwIn.addEventListener('input', () => { s.password = pwIn.value; clearError() })
    nameIn?.addEventListener('input', () => { s.name = nameIn.value; clearError() })
    nameIn?.classList.add('dc-wm-name')
    for (const input of [linkIn, pwIn, nameIn].filter(Boolean)) input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); add(s) } })
    function clearError() {
      if (!s.error) return
      s.error = ''
      scrim?.querySelector('.dc-wm-err')?.remove()
      scrim?.querySelectorAll('.dc-wm-field.bad').forEach(n => n.classList.remove('bad'))
    }
    const badField = { 'not-link': 'link', private: 'link', redirect: 'link', unreachable: 'link', gone: 'link', 'too-old': 'link', 'too-new': 'link', password: 'password', throttled: 'password', 'name-taken': 'name' }[s.code]
    const field = (label, input, which, hint = null) => h('label', { class: `dc-wm-field${s.error && badField === which ? ' bad' : ''}` },
      h('span', { class: 'dc-wm-lbl', text: label }), input, hint ? h('span', { class: 'dc-wm-hint', text: hint }) : null)
    bs.keep(() => put(scrim, h('form', { class: 'dc-dlg dc-wm-dlg', role: 'dialog', 'aria-label': 'Add a shared doc', onsubmit: (e) => { e.preventDefault(); add(s) } },
      grab(h),
      h('div', { class: 'dc-sh-head' },
        h('h2', { text: 'Add a shared doc' }),
        h('button', { class: 'dc-ib', type: 'button', 'aria-label': 'Close', onclick: close }, icon('x', 18))),
      h('p', { class: 'dc-wm-lead', text: 'Paste the link and password you were sent, or the whole invite. It opens here in your editor and stays on their Loam.' }),
      field('Link', linkIn, 'link'),
      field('Password', pwIn, 'password'),
      nameIn ? field('Your name there', nameIn, 'name', 'This link isn’t for one person, so they’ll see you by this name.') : null,
      s.error ? h('p', { class: 'dc-wm-err', role: 'alert' }, icon(s.code === 'gone' ? 'x' : 'changed', 15), h('span', { text: s.error })) : null,
      h('button', { class: 'dc-sh-go dc-wm-go primary', type: 'submit', disabled: s.busy ? true : null },
        s.busy ? h('span', { class: 'dc-wm-spin', 'aria-hidden': 'true' }) : icon('plus', 15),
        s.busy ? 'Checking with their Loam…' : 'Add'),
      h('p', { class: 'dc-sh-cap', text: 'The password is kept by your Loam, never shown again, and your agents can use it too.' }))))
    if (focus) setTimeout(() => (s.needName ? nameIn : linkIn)?.focus(), 60)
  }

  async function add(s) {
    if (s.busy) return
    if (!s.link.trim()) { s.error = 'Paste the link you were sent.'; s.code = 'not-link'; return draw(s) }
    if (!s.password.trim()) { s.error = 'Type the password that came with the link.'; s.code = 'password'; draw(s); return scrim?.querySelector('.dc-wm-in.mono')?.focus() }
    if (s.needName && !s.name.trim()) { s.error = 'Say the name they know you by.'; s.code = 'name-taken'; return draw(s) }
    s.busy = true
    s.error = ''
    draw(s)
    try {
      const got = await req('POST', 'withme', { link: s.link.trim(), password: s.password.trim(), ...(s.needName ? { name: s.name.trim() } : {}) })
      const d = got.doc
      st.docs = [d, ...st.docs.filter(x => x.id !== d.id)]
      st.fresh.add(d.id)
      setTimeout(() => { st.fresh.delete(d.id); schedule() }, 900)
      close()
      schedule()
      toast?.(`“${d.title}” is in Shared with me`)
    } catch (err) {
      s.busy = false
      if (err.code === 'need-name') {
        s.needName = true
        s.name = s.name || err.suggest || ''
        s.error = ''
        s.code = ''
        draw(s)
        setTimeout(() => { const n = scrim?.querySelector('.dc-wm-name'); n?.focus(); n?.select() }, 30)
        return
      }
      if (err.code === 'already') { close(); toast?.('That doc is already in Shared with me'); return }
      s.error = err.message
      s.code = err.code || ''
      draw(s)
    }
  }

  return { st, load, section, byId, openAdd, remove, req, menuItems, rowOf }
}
