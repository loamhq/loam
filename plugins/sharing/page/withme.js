// Shared with me: files others shared from their own Loams, opened inside
// Files › Shared for any file type (Markdown, PDF, images, SVG, HTML, audio,
// video, code, archives). Draws `files.share` slot `withme-menu` so Comments
// can add its agent-appearance selector without coupling.

import { bottomSheet, grab } from './sheet.js'
import { h, icon, put } from './dom.js'

const MODE = { view: 'View', comment: 'Comment', edit: 'Edit' }

export function withmeId(path) {
  return /^with-me\/(w-[a-z0-9]{10})(?:\.[A-Za-z0-9_-]{1,16})?$/.exec(path || '')?.[1] || null
}

export function createWithMe({ base, root, schedule, toast, side, registry, agentName = () => '' }) {
  const st = { items: [], loaded: false, loading: false, error: '', fresh: new Set(), leaving: new Map() }

  async function req(method, route, body) {
    let res
    try {
      res = await fetch(`${base}${route}`, {
        method,
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined,
      })
    } catch {
      throw Object.assign(new Error('Your Loam didn’t answer. Check your connection and try again.'), { code: 'offline' })
    }
    const data = await res.json().catch(() => null)
    if (!res.ok) {
      throw Object.assign(new Error(data?.error || `Your Loam answered ${res.status}.`), {
        code: data?.code, status: res.status, suggest: data?.suggest,
      })
    }
    return data
  }

  async function load(refresh = true) {
    if (st.loading) return st.loadingPromise
    st.loading = true
    st.loadingPromise = req('GET', `withme${refresh ? '' : '?refresh=0'}`)
      .then((got) => {
        st.items = got.items || []
        st.loaded = true
        st.error = ''
      })
      .catch((err) => { st.error = err.message; st.loaded = true })
      .finally(() => { st.loading = false; schedule() })
    return st.loadingPromise
  }

  const byId = (id) => st.items.find(d => d.id === id) || null
  const rowOf = (d) => ({
    kind: 'file',
    root,
    path: d.plugin_path || `with-me/${d.id}`,
    name: d.title || d.filename || 'Shared file',
    place: `Shared with me · ${d.owner ? `${d.owner}’s Loam` : d.host}`,
    size: d.size,
    capabilities: {},
    withme: d,
  })

  function section({ entry, go, H = h }) {
    const list = st.items.map(d => ({ d, leaving: false }))
    for (const { doc, at } of st.leaving.values()) list.splice(Math.min(at, list.length), 0, { d: doc, leaving: true })
    const rows = list.map(({ d, leaving }) => row(d, { entry, go, leaving, H }))
    const add = H('button', {
      class: `setrow sh-wm-add${st.items.length ? '' : ' alone'}`, type: 'button', key: 'wm:add',
      onclick: () => openAdd(),
    },
    H('div', { class: 'icon-tile sh-wm-addtile' }, icon('plus', 16)),
    H('div', { class: 'fs-words' },
      H('div', { class: 'fs-name', text: 'Add a shared file' }),
      st.items.length ? null : H('div', { class: 'fs-meta', text: 'Someone shared a file from their Loam? Paste its link and password to open it here.' })))
    return H('div', { class: 'card card-group sh-sd sh-wm', key: 'wm:list' }, ...rows, add)
  }

  function metaFor(d) {
    if (d.state === 'gone') return 'No longer shared'
    if (d.state === 'unreachable') return `Couldn’t reach ${d.host} · tries again when opened`
    if (d.state === 'password') return 'Its password no longer opens it'
    if (d.state === 'version') return 'That Loam is a version this one can’t open'
    const whose = d.owner ? `${d.owner}’s Loam` : d.host
    return [whose, MODE[d.mode] || 'View'].filter(Boolean).join(' · ')
  }

  function row(d, { entry, go, leaving, H = h }) {
    const r = rowOf(d)
    const dead = d.state !== 'ok'
    const extras = {
      meta: metaFor(d),
      badge: d.new && !dead ? H('span', { class: 'sh-sd-dot', key: `wmdot:${d.id}`, role: 'img', 'aria-label': 'Something new' }) : null,
      onopen: () => { d.new = false },
      menu: () => menuItems(d, go),
      className: `sh-wm-entry${dead ? ' sh-wm-dead' : ''}${st.fresh.has(d.id) ? ' sh-wm-arrive' : ''}${leaving ? ' sh-sd-leaving' : ''}`,
    }
    if (typeof entry === 'function') return entry(r, extras)
    return H('button', { class: 'setrow', key: `wm:${d.id}`, onclick: () => go(root, r.path, { isFile: true, row: r }) },
      H('div', { class: 'fs-words' }, H('div', { class: 'fs-name', text: r.name }), H('div', { class: 'fs-meta', text: extras.meta })))
  }

  function menuItems(d, go) {
    const r = rowOf(d)
    const sideOk = side?.()?.available?.()
    const extras = registry?.draw?.('files.share', {
      slot: 'withme-menu',
      doc: d,
      toast,
      agentName,
    }) || []
    return [
      { id: 'open', label: 'Open', icon: (s) => icon('file', s), run: () => go(root, r.path, { isFile: true, row: r }) },
      sideOk ? { id: 'side', label: 'Open to the side', icon: 'panelRight', run: () => side().openSide({ kind: 'file', root, path: r.path, row: r }) } : null,
      { id: 'copy', label: 'Copy link', icon: (s) => icon('link', s), run: () => copyLink(d) },
      ...extras,
      { id: 'remove', label: 'Remove', icon: (s) => icon('trash', s), danger: true, sub: 'Forgets the link and password here. Nothing changes for the owner.', run: () => remove(d) },
    ].filter(Boolean)
  }

  async function copyLink(d) {
    try { await navigator.clipboard.writeText(d.url); toast?.('Link copied. Its password stays with your Loam.') } catch { toast?.(d.url) }
  }

  async function remove(d) {
    try {
      await req('DELETE', `withme/${d.id}`)
    } catch (err) { toast?.(err.message); return }
    const at = st.items.findIndex(x => x.id === d.id)
    st.items = st.items.filter(x => x.id !== d.id)
    st.leaving.set(d.id, { doc: d, at })
    setTimeout(() => { st.leaving.delete(d.id); schedule() }, 340)
    schedule()
    toast?.(`Removed “${d.title}” from Shared with me`)
  }

  // -- add sheet -------------------------------------------------------------

  let scrim = null
  let bs = null
  function openAdd() {
    if (scrim) return
    const s = { link: '', password: '', name: '', needName: false, busy: false, error: '', code: '' }
    scrim = h('div', { class: 'sh-dlg-scrim sh-wm-scrim', onclick: (e) => { if (e.target === scrim) close() } })
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
    const linkIn = h('input', {
      class: 'sh-wm-in', type: 'text', inputmode: 'url', autocapitalize: 'off',
      placeholder: 'https://…/s/…', value: s.link, autocomplete: 'off', spellcheck: 'false', 'aria-label': 'Link',
    })
    const pwIn = h('input', {
      class: 'sh-wm-in mono', type: 'text', placeholder: 'Password', value: s.password,
      autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false', 'aria-label': 'Password',
    })
    const nameIn = s.needName ? h('input', {
      class: 'sh-wm-in sh-wm-name', type: 'text', placeholder: 'Your name there', value: s.name,
      maxlength: '60', autocomplete: 'name', 'aria-label': 'Your name there',
    }) : null

    const fill = (text) => {
      const link = /https:\/\/\S+?\/s\/[A-Za-z0-9_-]{32,64}/.exec(text)?.[0]
      const pw = /^\s*password\s*[:：]\s*(\S+)\s*$/im.exec(text)?.[1]
      if (!link && !pw) return false
      if (link) s.link = link
      if (pw) s.password = pw
      s.error = ''
      draw(s)
      ;(s.password ? (s.needName ? scrim?.querySelector('.sh-wm-name') : scrim?.querySelector('.sh-wm-go')) : scrim?.querySelector('.sh-wm-in.mono'))?.focus()
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
    for (const input of [linkIn, pwIn, nameIn].filter(Boolean)) {
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); add(s) } })
    }

    function clearError() {
      if (!s.error) return
      s.error = ''
      scrim?.querySelector('.sh-wm-err')?.remove()
      scrim?.querySelectorAll('.sh-wm-field.bad').forEach(n => n.classList.remove('bad'))
    }

    const badField = {
      'not-link': 'link', private: 'link', redirect: 'link', unreachable: 'link', gone: 'link',
      'too-old': 'link', 'too-new': 'link', password: 'password', throttled: 'password', 'name-taken': 'name',
    }[s.code]
    const field = (label, input, which, hint = null) => h('label', { class: `sh-wm-field${s.error && badField === which ? ' bad' : ''}` },
      h('span', { class: 'sh-wm-lbl', text: label }), input, hint ? h('span', { class: 'sh-wm-hint', text: hint }) : null)

    bs.keep(() => put(scrim, h('form', {
      class: 'sh-dlg sh-wm-dlg', role: 'dialog', 'aria-label': 'Add a shared file',
      onsubmit: (e) => { e.preventDefault(); add(s) },
    },
    grab(h),
    h('div', { class: 'sh-head' },
      h('h2', { text: 'Add a shared file' }),
      h('button', { class: 'sh-ib', type: 'button', 'aria-label': 'Close', onclick: close }, icon('x', 18))),
    h('p', { class: 'sh-wm-lead', text: 'Paste the link and password you were sent, or the whole invite. It opens here in Files and stays on their Loam.' }),
    field('Link', linkIn, 'link'),
    field('Password', pwIn, 'password'),
    nameIn ? field('Your name there', nameIn, 'name', 'This link isn’t for one person, so they’ll see you by this name.') : null,
    s.error ? h('p', { class: 'sh-wm-err', role: 'alert' }, icon(s.code === 'gone' ? 'x' : 'changed', 15), h('span', { text: s.error })) : null,
    h('button', { class: 'sh-go sh-wm-go primary', type: 'submit', disabled: s.busy ? true : null },
      s.busy ? h('span', { class: 'sh-wm-spin', 'aria-hidden': 'true' }) : icon('plus', 15),
      s.busy ? 'Checking with their Loam…' : 'Add'),
    h('p', { class: 'sh-cap', text: 'The password is kept by your Loam, never shown again, and your agents can use it too.' }))))

    if (focus) setTimeout(() => (s.needName ? nameIn : linkIn)?.focus(), 60)
  }

  async function add(s) {
    if (s.busy) return
    if (!s.link.trim()) { s.error = 'Paste the link you were sent.'; s.code = 'not-link'; return draw(s) }
    if (!s.password.trim()) { s.error = 'Type the password that came with the link.'; s.code = 'password'; draw(s); return scrim?.querySelector('.sh-wm-in.mono')?.focus() }
    if (s.needName && !s.name.trim()) { s.error = 'Say the name they know you by.'; s.code = 'name-taken'; return draw(s) }
    s.busy = true
    s.error = ''
    draw(s)
    try {
      const got = await req('POST', 'withme', { link: s.link.trim(), password: s.password.trim(), ...(s.needName ? { name: s.name.trim() } : {}) })
      const d = got.item
      st.items = [d, ...st.items.filter(x => x.id !== d.id)]
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
        setTimeout(() => { const n = scrim?.querySelector('.sh-wm-name'); n?.focus(); n?.select() }, 30)
        return
      }
      if (err.code === 'already') { close(); toast?.('That file is already in Shared with me'); return }
      s.error = err.message
      s.code = err.code || ''
      draw(s)
    }
  }

  return { st, load, section, byId, openAdd, remove, req, menuItems, rowOf }
}
