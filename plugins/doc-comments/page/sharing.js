// The owner's side of sharing, in Loam's editor: the Share button, the
// invite card (who, what they can do, the ticket they get) and Shared with
// (one row per link: its mode, Copy invite again, Turn off with Undo).
// Talks to the plugin server's owner routes (`/x/doc-comments/doc/links`).

import { h, icon, put } from './editor/dom.js'

const MODES = [
  { id: 'view', label: 'can view', verb: 'view' },
  { id: 'comment', label: 'can comment', verb: 'comment on' },
  { id: 'edit', label: 'can edit', verb: 'edit' },
]
const DAYS = [1, 7, 30, 90, 365]

export function createSharing({ base, root, path, title, owner, toast, onChange }) {
  const st = { links: [], owner: owner || 'You', open: null, card: null, menu: null }
  const req = async (method, route, body) => {
    const q = method === 'GET' ? `?${new URLSearchParams({ root, path })}` : ''
    const res = await fetch(`${base}${route}${q}`, {
      method, credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
      body: method === 'GET' ? undefined : JSON.stringify({ root, path, ...(body || {}) }),
    })
    const data = await res.json().catch(() => null)
    if (!res.ok) throw new Error(data?.error || `The share server answered ${res.status}`)
    return data
  }

  const live = () => st.links.filter(l => l.on)
  const url = (l) => `${location.origin}${l.url_path}`
  const modeOf = (id) => MODES.find(m => m.id === id) || MODES[1]

  function invite(l, password) {
    const who = l.name ? `${l.name}, ` : ''
    return [
      `${who}${st.owner} invited you to ${modeOf(l.mode).verb} “${title()}”.`,
      url(l),
      `Password: ${password}`,
      `(Your browser remembers it for ${l.days} day${l.days === 1 ? '' : 's'}.)`,
    ].join('\n')
  }

  async function copy(text) {
    try { await navigator.clipboard.writeText(text); return true } catch { return false }
  }

  // -- the button -----------------------------------------------------------------

  function faces(list) {
    const shown = list.slice(0, 2)
    return h('span', { class: 'dc-sharefaces' }, shown.map(l => l.name
      ? h('span', { class: 'dc-sf', text: l.name.charAt(0).toUpperCase() })
      : h('span', { class: 'dc-sf grp' }, icon('people', 11))))
  }

  function button() {
    const on = live()
    return h('button', { class: `dc-sharebtn${on.length ? ' has' : ''}`, type: 'button', onclick: () => open() },
      on.length ? faces(on) : null, h('span', { text: 'Share' }))
  }

  // -- the dialog -------------------------------------------------------------------

  let scrim = null
  async function open(which = null) {
    try {
      const got = await req('GET', 'doc/links')
      st.links = got.links
      st.owner = got.owner || st.owner
    } catch (err) {
      toast?.(err.message)
      return
    }
    st.open = which || (live().length ? 'list' : 'card')
    if (st.open === 'card') await freshCard()
    if (!scrim) {
      scrim = h('div', { class: 'dc-dlg-scrim dc-share-scrim', onclick: (e) => { if (e.target === scrim) close() } })
      document.body.append(scrim)
      requestAnimationFrame(() => scrim.classList.add('on'))
      document.addEventListener('keydown', onKey)
    }
    draw()
  }

  function onKey(e) {
    if (e.key === 'Escape') { if (st.menu) { st.menu = null; draw() } else close() }
  }

  function close() {
    if (!scrim) return
    const s = scrim
    scrim = null
    s.classList.remove('on')
    setTimeout(() => s.remove(), 220)
    document.removeEventListener('keydown', onKey)
    st.open = null
    onChange?.()
  }

  async function freshCard() {
    let pw = ''
    try { pw = (await req('GET', 'doc/links/password')).password } catch { pw = '' }
    st.card = { name: '', mode: 'comment', days: 30, password: pw, editing: false, busy: false }
  }

  function draw() {
    if (!scrim) return
    put(scrim, st.open === 'card' ? cardView() : listView())
  }

  function head(text, back = null) {
    return h('div', { class: 'dc-sh-head' },
      back ? h('button', { class: 'dc-ib', type: 'button', 'aria-label': 'Back', onclick: back }, icon('chevronLeft', 18)) : null,
      h('h2', { text }),
      h('button', { class: 'dc-ib', type: 'button', 'aria-label': 'Close', onclick: close }, icon('x', 18)))
  }

  function modeMenu(current, choose, key) {
    const openNow = st.menu === key
    return h('div', { class: `dc-ddw dc-modedd${openNow ? ' open' : ''}` },
      h('button', { class: 'dc-modepill', type: 'button', onclick: (e) => { e.stopPropagation(); st.menu = openNow ? null : key; draw() } },
        h('span', { text: modeOf(current).label }), icon('chevronDown', 12)),
      openNow ? h('div', { class: 'dc-menu dc-modemenu', role: 'menu' }, MODES.map(m =>
        h('button', { class: `dc-mi${m.id === current ? ' on' : ''}`, type: 'button', onclick: () => { st.menu = null; choose(m.id) } },
          h('span', { class: 'dc-l' }, h('b', { text: m.label.replace('can ', 'Can ') }), h('small', { text: { view: 'Read the doc and its notes', comment: 'Comment and suggest', edit: 'Edit the text, accept suggestions' }[m.id] })),
          h('span', { class: 'dc-ck' }, icon('check', 16))))) : null)
  }

  function cardView() {
    const c = st.card
    const nameIn = h('input', { class: 'dc-iv-name', type: 'text', placeholder: 'Who is it for? (optional)', value: c.name, maxlength: '60', 'aria-label': 'Who is it for' })
    nameIn.addEventListener('input', () => { c.name = nameIn.value; forName.textContent = c.name ? `For ${c.name}` : 'For anyone with the link'; av.textContent = (c.name || '').charAt(0).toUpperCase(); av.classList.toggle('grp', !c.name); cap.textContent = c.name ? `The card is what ${c.name} gets.` : 'A group link: each visitor gives a name once.' })
    const av = h('span', { class: `dc-iv-av${c.name ? '' : ' grp'}`, text: c.name.charAt(0).toUpperCase() })
    const forName = h('div', { class: 'dc-tk-for', text: c.name ? `For ${c.name}` : 'For anyone with the link' })
    const cap = h('p', { class: 'dc-sh-cap', text: c.name ? `The card is what ${c.name} gets.` : 'A group link: each visitor gives a name once.' })
    const pw = c.editing
      ? h('input', { class: 'dc-tk-pwin', type: 'text', value: c.password, maxlength: '100', 'aria-label': 'Password', onkeydown: (e) => { if (e.key === 'Enter') { c.password = e.target.value.trim() || c.password; c.editing = false; draw() } }, onblur: (e) => { c.password = e.target.value.trim() || c.password; c.editing = false; draw() } })
      : h('button', { class: 'dc-tk-pw', type: 'button', title: 'Change the password', onclick: () => { c.editing = true; draw(); setTimeout(() => scrim?.querySelector('.dc-tk-pwin')?.select(), 0) } }, c.password)
    const daysSel = h('select', { class: 'dc-tk-days', 'aria-label': 'Remembered for' }, DAYS.map(d => h('option', { value: String(d), selected: d === c.days ? true : null }, d === 1 ? '1 day' : `${d} days`)))
    daysSel.addEventListener('change', () => { c.days = Number(daysSel.value) })
    const go = h('button', { class: 'dc-gate-go dc-sh-go', type: 'button', disabled: c.busy ? true : null, onclick: create }, icon('file', 15), c.busy ? 'Making the link…' : 'Copy invite')
    setTimeout(() => { if (!c.name && document.activeElement === document.body) nameIn.focus() }, 30)
    return h('div', { class: 'dc-dlg dc-sharedlg', role: 'dialog', 'aria-label': 'Invite' },
      head('Invite', live().length ? () => { st.open = 'list'; draw() } : null),
      h('div', { class: 'dc-iv-row' }, h('label', { class: 'dc-iv-who' }, av, nameIn), modeMenu(c.mode, (m) => { c.mode = m; draw() }, 'card')),
      h('div', { class: 'dc-ticket' },
        forName,
        h('div', { class: 'dc-tk-title', text: title() }),
        h('div', { class: 'dc-tk-by' }, h('span', { class: 'dc-face', text: st.owner.charAt(0).toUpperCase() }), h('span', {}, `${st.owner} invited you to `, h('b', { text: modeOf(c.mode).verb }), ' it')),
        h('div', { class: 'dc-tk-cut' }),
        h('div', { class: 'dc-tk-foot' },
          h('div', {}, h('div', { class: 'dc-tk-lbl', text: 'Password' }), pw),
          h('div', { class: 'dc-tk-link' }, h('span', { text: location.host }), h('span', { text: '/s/…' })))),
      h('div', { class: 'dc-tk-mem' }, h('span', { text: 'Their browser remembers it for' }), daysSel),
      go, cap)

    async function create() {
      c.busy = true
      draw()
      try {
        const out = await req('POST', 'doc/links', { name: c.name || null, mode: c.mode, days: c.days, password: c.password })
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
    const rows = live().map(l => h('div', { class: 'dc-sw-row' },
      l.name ? h('span', { class: 'dc-iv-av', text: l.name.charAt(0).toUpperCase() }) : h('span', { class: 'dc-iv-av grp' }, icon('people', 15)),
      h('div', { class: 'dc-sw-who' }, h('b', { text: l.name || 'Group link' }),
        !l.name && l.people.length ? h('span', { class: 'dc-sharefaces' }, l.people.slice(0, 4).map(p => h('span', { class: 'dc-sf', title: p, text: p.charAt(0).toUpperCase() }))) : null,
        l.locked ? h('small', { class: 'bad', text: 'Locked after wrong passwords' }) : null),
      modeMenu(l.mode, (m) => patch(l, { mode: m }), `mode:${l.id}`),
      rowMenu(l)))
    return h('div', { class: 'dc-dlg dc-sharedlg', role: 'dialog', 'aria-label': 'Shared with' },
      head('Shared with'),
      h('div', { class: 'dc-sw-list' }, rows.length ? rows : h('p', { class: 'dc-quiet', text: 'No one has a link to this doc.' })),
      h('button', { class: 'dc-gate-go dc-sh-go', type: 'button', onclick: async () => { await freshCard(); st.open = 'card'; draw() } }, icon('plus', 15), 'New invite'),
      h('p', { class: 'dc-sh-cap', text: 'Each link has its own password.' }))
  }

  function rowMenu(l) {
    const key = `more:${l.id}`
    const openNow = st.menu === key
    return h('div', { class: `dc-ddw${openNow ? ' open' : ''}` },
      h('button', { class: 'dc-ib', type: 'button', 'aria-label': 'More', onclick: (e) => { e.stopPropagation(); st.menu = openNow ? null : key; draw() } }, icon('dots', 16)),
      openNow ? h('div', { class: 'dc-menu dc-rowmenu', role: 'menu' },
        h('button', { class: 'dc-mi', type: 'button', onclick: async () => {
          st.menu = null
          const out = await patch(l, { password: true }, false)
          if (out?.password && await copy(invite(out.link, out.password))) toast?.('Invite copied, with a new password. Browsers already in stay in.')
          else if (out?.password) toast?.(`New password ${out.password}`)
        } }, icon('file', 16), h('span', { class: 'dc-l' }, h('b', { text: 'Copy invite again' }), h('small', { text: 'With a new password; anyone already in stays in' }))),
        h('div', { class: 'dc-mi dc-mi-days' }, icon('history', 16), h('span', { class: 'dc-l' }, h('b', { text: 'Remembered for' })),
          (() => { const s = h('select', {}, DAYS.map(d => h('option', { value: String(d), selected: d === l.days ? true : null }, d === 1 ? '1 day' : `${d} days`))); s.addEventListener('change', () => patch(l, { days: Number(s.value) })); return s })()),
        h('div', { class: 'dc-msep' }),
        h('button', { class: 'dc-mi bad', type: 'button', onclick: () => { st.menu = null; turnOff(l) } }, icon('x', 16), h('span', { class: 'dc-l' }, h('b', { text: 'Turn off' }), h('small', { text: 'The link stops working at once' })))) : null)
  }

  async function patch(l, body, redraw = true) {
    try {
      const out = await req('PATCH', `doc/links/${encodeURIComponent(l.id)}`, body)
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

  async function refresh() {
    try { st.links = (await req('GET', 'doc/links')).links } catch { /* the button stays as it was */ }
    onChange?.()
  }

  return { button, open, refresh, set links(v) { st.links = v || [] } }
}
