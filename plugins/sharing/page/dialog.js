// The owner's Share button, Invite card, and Shared with dialog.
// Works for any file in the vault (never folders). Adapts its access-level
// options strictly to what the server allows (view-only when Comments is off;
// view/comment for non-Markdown; view/comment/edit for Markdown).

import { bottomSheet, grab } from './sheet.js'
import { tone, h, icon, put, wordSelect } from './dom.js'

const ALL_MODES = [
  { id: 'view', label: 'can view', verb: 'view' },
  { id: 'comment', label: 'can comment', verb: 'comment on' },
  { id: 'edit', label: 'can edit', verb: 'edit' },
]
const DAYS = [1, 7, 30, 90, 365]
const DAY_WORDS = DAYS.map(d => [String(d), d === 1 ? '1 day' : `${d} days`])

export function createShareDialog({
  base,
  root,
  path,
  title,
  owner = 'You',
  toast,
  onChange,
  registry = null,
}) {
  const st = {
    links: [],
    allowedModes: ['view'],
    defaultDays: 30,
    kind: 'other',
    owner,
    open: null,
    card: null,
    menu: null,
  }

  const req = async (method, route, body) => {
    const q = method === 'GET' ? `?${new URLSearchParams({ root, path })}` : ''
    const res = await fetch(`${base}${route}${q}`, {
      method,
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: method === 'GET' ? undefined : JSON.stringify({ root, path, ...(body || {}) }),
    })
    const data = await res.json().catch(() => null)
    if (!res.ok) throw new Error(plainError(data?.error, res.status))
    return data
  }

  const live = () => st.links.filter(l => l.on)
  const url = (l) => `${location.origin}${l.url_path}`
  const modeOf = (id) => ALL_MODES.find(m => m.id === id) || ALL_MODES[0]
  const modesAvailable = () => ALL_MODES.filter(m => st.allowedModes.includes(m.id))

  function modeHelp(id) {
    if (st.kind === 'markdown') {
      return {
        view: 'Read the doc and its notes',
        comment: 'Comment and suggest changes',
        edit: 'Edit the text, accept suggestions',
      }[id] || ''
    }
    return {
      view: 'View and download this file',
      comment: 'View, download, and leave comments',
    }[id] || ''
  }

  function invite(l, password) {
    const who = l.name ? `${l.name}, ` : ''
    const eff = l.effective_mode || l.mode
    return [
      `${who}${st.owner} invited you to ${modeOf(eff).verb} “${title()}”.`,
      url(l),
      `Password: ${password}`,
      `(Your browser remembers it for ${l.days} day${l.days === 1 ? '' : 's'}.)`,
    ].join('\n')
  }

  async function copy(text) {
    try { await navigator.clipboard.writeText(text); return true } catch { return false }
  }

  // -- dialog lifecycle ------------------------------------------------------

  let scrim = null
  let bs = null

  async function open(which = null) {
    try {
      const got = await req('GET', 'file/links')
      st.links = got.links || []
      st.owner = got.owner || st.owner
      st.allowedModes = got.allowed_modes || ['view']
      st.defaultDays = got.default_days || 30
      st.kind = got.kind || 'other'
    } catch (err) {
      toast?.(err.message)
      return
    }
    st.open = which || (live().length ? 'list' : 'card')
    if (st.open === 'card') await freshCard()
    if (!scrim) {
      scrim = h('div', { class: 'sh-dlg-scrim', onclick: (e) => { if (e.target === scrim) close() } })
      bs = bottomSheet(scrim, { close })
      document.body.append(scrim)
      requestAnimationFrame(() => scrim?.classList.add('on'))
      document.addEventListener('keydown', onKey)
    }
    draw()
  }

  function onKey(e) {
    if (e.key === 'Escape') {
      if (st.menu) { st.menu = null; draw() }
      else close()
    }
  }

  function close() {
    if (!scrim) return
    const s = scrim
    scrim = null
    bs?.dispose()
    bs = null
    s.classList.remove('on')
    setTimeout(() => s.remove(), 320)
    document.removeEventListener('keydown', onKey)
    st.open = null
    onChange?.()
  }

  async function freshCard() {
    let pw = ''
    try { pw = (await req('GET', 'file/links/password')).password } catch { pw = '' }
    const defMode = st.allowedModes.includes('comment') ? 'comment' : 'view'
    st.card = { name: '', mode: defMode, days: st.defaultDays, password: pw, editing: false, busy: false, short: false }
  }

  function sheetExtras(view, recipientName = '') {
    if (!registry) return []
    try {
      return registry.draw('files.share', {
        slot: 'sheet',
        root,
        path,
        view,
        recipientName,
        toast,
      }).map(n => n?.__keep || n).filter(Boolean)
    } catch { return [] }
  }

  function draw() {
    if (!scrim) return
    bs.keep(() => put(scrim, st.open === 'card' ? cardView() : listView()))
    scrim.firstChild?.prepend(grab(h))
  }

  function head(text, back = null) {
    return h('div', { class: 'sh-head' },
      back ? h('button', { class: 'sh-ib', type: 'button', 'aria-label': 'Back', onclick: back }, icon('chevronLeft', 18)) : null,
      h('h2', { text }),
      h('button', { class: 'sh-ib', type: 'button', 'aria-label': 'Close', onclick: close }, icon('x', 18)))
  }

  function modeControl(current, choose, key) {
    const avail = modesAvailable()
    const shownMode = st.allowedModes.includes(current) ? current : 'view'
    // View-only (e.g. Comments is off): quiet static label, zero dead dropdown.
    if (avail.length <= 1) {
      return h('span', { class: 'sh-modepill static' }, h('span', { text: modeOf(shownMode).label }))
    }
    const openNow = st.menu === key
    return h('div', { class: `sh-ddw sh-modedd${openNow ? ' open' : ''}` },
      h('button', {
        class: 'sh-modepill', type: 'button',
        onclick: (e) => { e.stopPropagation(); st.menu = openNow ? null : key; draw() },
      }, h('span', { text: modeOf(shownMode).label }), icon('chevronDown', 12)),
      openNow ? h('div', { class: 'sh-menu sh-modemenu', role: 'menu' }, avail.map(m =>
        h('button', {
          class: `sh-mi${m.id === shownMode ? ' on' : ''}`, type: 'button',
          onclick: () => { st.menu = null; choose(m.id) },
        },
        h('span', { class: 'sh-l' },
          h('b', { text: m.label.replace('can ', 'Can ') }),
          h('small', { text: modeHelp(m.id) })),
        h('span', { class: 'sh-ck' }, icon('check', 16))))) : null)
  }

  function cardView() {
    const c = st.card
    const nameIn = h('input', {
      class: 'sh-iv-name', type: 'text', placeholder: 'Who is it for? (optional)',
      value: c.name, maxlength: '60', 'aria-label': 'Who is it for',
    })
    const av = h('span', {
      class: `sh-iv-av${c.name ? '' : ' grp'}`,
      style: c.name ? { background: tone(c.name) } : {},
      text: c.name.charAt(0).toUpperCase(),
    })
    const forName = h('div', { class: 'sh-tk-for', text: c.name ? `For ${c.name}` : 'For anyone with the link' })
    const cap = h('p', {
      class: 'sh-cap',
      text: c.name ? `The card is what ${c.name} gets.` : 'A group link: each visitor gives a name once.',
    })
    nameIn.addEventListener('input', () => {
      c.name = nameIn.value
      forName.textContent = c.name ? `For ${c.name}` : 'For anyone with the link'
      av.textContent = (c.name || '').charAt(0).toUpperCase()
      av.classList.toggle('grp', !c.name)
      av.style.background = c.name ? tone(c.name) : ''
      cap.textContent = c.name ? `The card is what ${c.name} gets.` : 'A group link: each visitor gives a name once.'
    })

    const pw = c.editing
      ? h('input', {
          class: 'sh-tk-pwin', type: 'text', value: c.password, maxlength: '100', 'aria-label': 'Password',
          oninput: (e) => { if (c.short && e.target.value.trim().length >= 6) { c.short = false; scrim?.querySelector('.sh-warn')?.remove() } },
          onkeydown: (e) => { if (e.key === 'Enter') { c.password = e.target.value.trim() || c.password; c.editing = false; draw() } },
          onblur: (e) => { c.password = e.target.value.trim() || c.password; c.editing = false; draw() },
        })
      : h('button', {
          class: 'sh-tk-pw', type: 'button', title: 'Change the password',
          onclick: () => { c.editing = true; draw(); setTimeout(() => scrim?.querySelector('.sh-tk-pwin')?.select(), 0) },
        }, c.password)

    const daysSel = wordSelect({
      options: DAY_WORDS, value: String(c.days), label: 'Remembered for', className: 'sh-tk-days',
      onChange: (v) => { c.days = Number(v) },
    })

    const go = h('button', {
      class: 'sh-go primary', type: 'button', disabled: c.busy ? true : null, onclick: create,
    }, icon('file', 15), c.busy ? 'Making the link…' : 'Copy invite')

    setTimeout(() => { if (!c.name && document.activeElement === document.body) nameIn.focus() }, 30)

    return h('div', { class: 'sh-dlg', role: 'dialog', 'aria-label': 'Invite' },
      head('Invite', live().length ? () => { st.open = 'list'; draw() } : null),
      h('div', { class: 'sh-iv-row' },
        h('label', { class: 'sh-iv-who' }, av, nameIn),
        modeControl(c.mode, (m) => { c.mode = m; draw() }, 'card')),
      h('div', { class: 'sh-ticket' },
        forName,
        h('div', { class: 'sh-tk-title', text: title() }),
        h('div', { class: 'sh-tk-by' },
          h('span', { class: 'sh-face', text: st.owner.charAt(0).toUpperCase() }),
          h('span', {}, `${st.owner} invited you to `, h('b', { text: modeOf(c.mode).verb }), ' it')),
        h('div', { class: 'sh-tk-cut' }),
        h('div', { class: 'sh-tk-foot' },
          h('div', {}, h('div', { class: 'sh-tk-lbl', text: 'Password' }), pw),
          h('div', { class: 'sh-tk-link' }, h('span', { text: location.host }), h('span', { text: '/s/…' })))),
      h('div', { class: 'sh-tk-mem' }, h('span', { text: 'Their browser remembers it for' }), daysSel),
      c.short ? h('p', { class: 'sh-cap sh-warn', role: 'alert', text: 'A password needs at least 6 characters.' }) : null,
      go,
      cap,
      ...sheetExtras('card', c.name))

    async function create() {
      if ((c.password || '').length < 6) { c.short = true; c.editing = true; draw(); return }
      c.short = false
      c.busy = true
      draw()
      try {
        const out = await req('POST', 'file/links', { name: c.name || null, mode: c.mode, days: c.days, password: c.password })
        st.links = [...st.links, out.link]
        const copied = await copy(invite(out.link, out.password))
        toast?.(copied ? `Invite copied${out.link.name ? ` for ${out.link.name}` : ''}` : `Link made. Password ${out.password}`)
        st.open = 'list'
        draw()
        onChange?.()
      } catch (err) {
        c.busy = false
        draw()
        toast?.(err.message)
      }
    }
  }

  function listView() {
    const rows = live().map(l => h('div', { class: 'sh-sw-row' },
      l.name
        ? h('span', { class: 'sh-iv-av', style: { background: tone(l.name) }, text: l.name.charAt(0).toUpperCase() })
        : h('span', { class: 'sh-iv-av grp' }, icon('people', 15)),
      h('div', { class: 'sh-sw-who' },
        h('b', { text: l.name || 'Group link' }),
        !l.name && l.people.length ? h('span', { class: 'sh-faces' }, l.people.slice(0, 4).map(p =>
          h('span', { class: 'sh-sf', title: p, style: { background: tone(p) }, text: p.charAt(0).toUpperCase() }))) : null,
        l.locked ? h('small', { class: 'bad', text: 'Locked after wrong passwords' }) : null),
      modeControl(l.effective_mode || l.mode, (m) => patch(l, { mode: m }), `mode:${l.id}`),
      rowMenu(l)))

    return h('div', { class: 'sh-dlg', role: 'dialog', 'aria-label': 'Shared with' },
      head('Shared with'),
      h('div', { class: 'sh-sw-list' }, rows.length ? rows : h('p', { class: 'sh-quiet', text: 'No one has a link to this file.' })),
      h('button', {
        class: 'sh-go primary', type: 'button',
        onclick: async () => { await freshCard(); st.open = 'card'; draw() },
      }, icon('plus', 15), 'New invite'),
      h('p', { class: 'sh-cap', text: 'Each link has its own password.' }),
      ...sheetExtras('list'))
  }

  function rowMenu(l) {
    const key = `more:${l.id}`
    const openNow = st.menu === key
    return h('div', { class: `sh-ddw${openNow ? ' open' : ''}` },
      h('button', {
        class: 'sh-ib', type: 'button', 'aria-label': 'More',
        onclick: (e) => { e.stopPropagation(); st.menu = openNow ? null : key; draw() },
      }, icon('dots', 16)),
      openNow ? h('div', { class: 'sh-menu sh-rowmenu', role: 'menu' },
        h('button', {
          class: 'sh-mi', type: 'button',
          onclick: async () => {
            st.menu = null
            draw()
            const out = await patch(l, { password: true }, false)
            if (out?.password && await copy(invite(out.link, out.password))) toast?.('Invite copied, with a new password. Browsers already in stay in.')
            else if (out?.password) toast?.(`New password ${out.password}`)
          },
        }, icon('file', 16), h('span', { class: 'sh-l' }, h('b', { text: 'Copy invite again' }), h('small', { text: 'With a new password; anyone already in stays in' }))),
        h('div', { class: 'sh-mi sh-mi-days' }, icon('history', 16), h('span', { class: 'sh-l' }, h('b', { text: 'Remembered for' })),
          wordSelect({ options: DAY_WORDS, value: String(l.days), label: 'Remembered for', onChange: (v) => patch(l, { days: Number(v) }) })),
        h('div', { class: 'sh-msep' }),
        h('button', {
          class: 'sh-mi bad', type: 'button',
          onclick: () => { st.menu = null; turnOff(l) },
        }, icon('x', 16), h('span', { class: 'sh-l' }, h('b', { text: 'Turn off' }), h('small', { text: 'The link stops working at once' })))) : null)
  }

  async function patch(l, body, redraw = true) {
    try {
      const out = await req('PATCH', `file/links/${encodeURIComponent(l.id)}`, body)
      st.links = st.links.map(x => (x.id === l.id ? out.link : x))
      if (redraw) draw()
      onChange?.()
      return out
    } catch (err) {
      toast?.(err.message)
      return null
    }
  }

  async function turnOff(l) {
    const out = await patch(l, { on: false })
    if (!out) return
    toast?.(`${l.name ? `${l.name}’s link` : 'Group link'} turned off`, () => patch(l, { on: true }))
    if (!live().length) { st.open = 'card'; await freshCard(); draw() }
  }

  return { open, close }
}

function plainError(msg, status) {
  const m = String(msg || '')
  if (/password is 6 to 100/i.test(m)) return 'A password needs 6 to 100 characters.'
  if (status === 429) return 'Too many tries just now. Wait a moment and try again.'
  if (status >= 500) return 'The sharing server did not answer. Try again in a moment.'
  return m ? m.charAt(0).toUpperCase() + m.slice(1) + (/[.!?]$/.test(m) ? '' : '.') : `The sharing server answered ${status}.`
}
