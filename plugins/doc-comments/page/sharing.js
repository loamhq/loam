// The owner's side of sharing, in Loam's editor: the Share button, the
// invite card (who, what they can do, the ticket they get) and Shared with
// (one row per link: its mode, Copy invite again, Turn off with Undo), and
// how the owner's agents appear to them on this doc (appear.js).
// Talks to the plugin server's owner routes (`/x/doc-comments/doc/links`).

import { bottomSheet, grab } from './sheet.js'
import { tone, h, icon, put } from './editor/dom.js'
import { appearSelect, wordSelect } from './appear.js'

const MODES = [
  { id: 'view', label: 'can view', verb: 'view' },
  { id: 'comment', label: 'can comment', verb: 'comment on' },
  { id: 'edit', label: 'can edit', verb: 'edit' },
]
const DAYS = [1, 7, 30, 90, 365]
const DAY_WORDS = DAYS.map(d => [String(d), d === 1 ? '1 day' : `${d} days`])

export function createSharing({ base, root, path, title, owner, toast, onChange, notes = () => 0, resolved = () => 0 }) {
  const st = { links: [], owner: owner || 'You', open: null, card: null, menu: null, appear: null }
  const req = async (method, route, body) => {
    const q = method === 'GET' ? `?${new URLSearchParams({ root, path })}` : ''
    const res = await fetch(`${base}${route}${q}`, {
      method, credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
      body: method === 'GET' ? undefined : JSON.stringify({ root, path, ...(body || {}) }),
    })
    const data = await res.json().catch(() => null)
    if (!res.ok) throw new Error(plainError(data?.error, res.status))
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
      ? h('span', { class: 'dc-sf', style: { background: tone(l.name) }, text: l.name.charAt(0).toUpperCase() })
      : h('span', { class: 'dc-sf grp' }, icon('people', 11))))
  }

  function button() {
    const on = live()
    return h('button', { class: `dc-sharebtn${on.length ? ' has' : ''}`, type: 'button', onclick: () => open() },
      on.length ? faces(on) : null, h('span', { text: 'Share' }))
  }

  // -- the dialog -------------------------------------------------------------------

  let scrim = null
  let bs = null
  async function open(which = null) {
    try {
      const got = await req('GET', 'doc/links')
      st.links = got.links
      st.owner = got.owner || st.owner
      st.appear = got.appear || null
    } catch (err) {
      toast?.(err.message)
      return
    }
    st.open = which || (live().length ? 'list' : 'card')
    if (st.open === 'card') await freshCard()
    if (!scrim) {
      scrim = h('div', { class: 'dc-dlg-scrim dc-share-scrim', onclick: (e) => { if (e.target === scrim) close() } })
      bs = bottomSheet(scrim, { close })
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
    try { pw = (await req('GET', 'doc/links/password')).password } catch { pw = '' }
    st.card = { name: '', mode: 'comment', days: 30, password: pw, editing: false, busy: false }
  }

  function draw() {
    if (!scrim) return
    // On a phone the dialog is a bottom sheet (sheet.js), with its handle.
    bs.keep(() => put(scrim, st.open === 'card' ? cardView() : listView()))
    scrim.firstChild?.prepend(grab(h))
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
    nameIn.addEventListener('input', () => { c.name = nameIn.value; if (seen) seen.textContent = seenLine(); forName.textContent = c.name ? `For ${c.name}` : 'For anyone with the link'; av.textContent = (c.name || '').charAt(0).toUpperCase(); av.classList.toggle('grp', !c.name); av.style.background = c.name ? tone(c.name) : ''; cap.textContent = c.name ? `The card is what ${c.name} gets.` : 'A group link: each visitor gives a name once.' })
    const av = h('span', { class: `dc-iv-av${c.name ? '' : ' grp'}`, style: c.name ? { background: tone(c.name) } : {}, text: c.name.charAt(0).toUpperCase() })
    const forName = h('div', { class: 'dc-tk-for', text: c.name ? `For ${c.name}` : 'For anyone with the link' })
    const cap = h('p', { class: 'dc-sh-cap', text: c.name ? `The card is what ${c.name} gets.` : 'A group link: each visitor gives a name once.' })
    // The notes already on the doc are seen by whoever the link is for: said
    // once, quietly, so it is never a surprise.
    const n = notes(), r = resolved()
    const seenLine = () => n
      ? `${c.name || 'They'} will also see the ${n === 1 ? 'open note' : `${n} open notes`} already on this doc${r ? `, and ${r} resolved` : ''}.`
      : `${c.name || 'They'} will also see the ${r === 1 ? 'resolved note' : `${r} resolved notes`} on this doc.`
    const seen = n || r ? h('p', { class: 'dc-sh-cap dc-sh-notes', text: seenLine() }) : null
    const pw = c.editing
      ? h('input', { class: 'dc-tk-pwin', type: 'text', value: c.password, maxlength: '100', 'aria-label': 'Password', oninput: (e) => { if (c.short && e.target.value.trim().length >= 6) { c.short = false; scrim?.querySelector('.dc-sh-warn')?.remove() } }, onkeydown: (e) => { if (e.key === 'Enter') { c.password = e.target.value.trim() || c.password; c.editing = false; c.short = c.password.length < 6 && c.short; draw() } }, onblur: (e) => { c.password = e.target.value.trim() || c.password; c.editing = false; c.short = c.password.length < 6 && c.short; draw() } })
      : h('button', { class: 'dc-tk-pw', type: 'button', title: 'Change the password', onclick: () => { c.editing = true; draw(); setTimeout(() => scrim?.querySelector('.dc-tk-pwin')?.select(), 0) } }, c.password)
    const daysSel = wordSelect({ options: DAY_WORDS, value: String(c.days), label: 'Remembered for', className: 'dc-tk-days', onChange: (v) => { c.days = Number(v) } })
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
      c.short ? h('p', { class: 'dc-sh-cap dc-sh-warn', role: 'alert', text: 'A password needs at least 6 characters.' }) : null,
      go, cap, seen)

    async function create() {
      if ((c.password || '').length < 6) { c.short = true; c.editing = true; draw(); return }
      c.short = false
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
      l.name ? h('span', { class: 'dc-iv-av', style: { background: tone(l.name) }, text: l.name.charAt(0).toUpperCase() }) : h('span', { class: 'dc-iv-av grp' }, icon('people', 15)),
      h('div', { class: 'dc-sw-who' }, h('b', { text: l.name || 'Group link' }),
        !l.name && l.people.length ? h('span', { class: 'dc-sharefaces' }, l.people.slice(0, 4).map(p => h('span', { class: 'dc-sf', title: p, style: { background: tone(p) }, text: p.charAt(0).toUpperCase() }))) : null,
        l.locked ? h('small', { class: 'bad', text: 'Locked after wrong passwords' }) : null),
      modeMenu(l.mode, (m) => patch(l, { mode: m }), `mode:${l.id}`),
      rowMenu(l)))
    return h('div', { class: 'dc-dlg dc-sharedlg', role: 'dialog', 'aria-label': 'Shared with' },
      head('Shared with'),
      h('div', { class: 'dc-sw-list' }, rows.length ? rows : h('p', { class: 'dc-quiet', text: 'No one has a link to this doc.' })),
      h('button', { class: 'dc-gate-go dc-sh-go', type: 'button', onclick: async () => { await freshCard(); st.open = 'card'; draw() } }, icon('plus', 15), 'New invite'),
      h('p', { class: 'dc-sh-cap', text: 'Each link has its own password.' }),
      appearLine())
  }

  // How the owner's agents appear to everyone with a link to this doc: one
  // line, the default or this doc's own choice. Applied as notes are served,
  // so it covers what is already on the doc too.
  function appearLine() {
    const a = st.appear
    if (!a) return null
    return h('div', { class: 'dc-tk-mem dc-ap-line' },
      h('span', { text: 'Your agents appear here' }),
      appearSelect({
        value: a.choice, def: a.default, alias: a.alias, withDefault: true,
        label: 'How your agents appear on this doc',
        onChange: async (v) => {
          try {
            const out = await req('PATCH', 'doc/links/appear', { appear: v })
            st.appear = out.appear
            toast?.(st.appear.as === 'one'
              ? `Your agents appear as ${st.appear.alias} here, on notes already here too`
              : 'Your agents appear here by their own names')
          } catch (err) { toast?.(err.message) }
          draw()
        },
      }))
  }

  function rowMenu(l) {
    const key = `more:${l.id}`
    const openNow = st.menu === key
    return h('div', { class: `dc-ddw${openNow ? ' open' : ''}` },
      h('button', { class: 'dc-ib', type: 'button', 'aria-label': 'More', onclick: (e) => { e.stopPropagation(); st.menu = openNow ? null : key; draw() } }, icon('dots', 16)),
      openNow ? h('div', { class: 'dc-menu dc-rowmenu', role: 'menu' },
        h('button', { class: 'dc-mi', type: 'button', onclick: async () => {
          st.menu = null
          draw()
          const out = await patch(l, { password: true }, false)
          if (out?.password && await copy(invite(out.link, out.password))) toast?.('Invite copied, with a new password. Browsers already in stay in.')
          else if (out?.password) toast?.(`New password ${out.password}`)
        } }, icon('file', 16), h('span', { class: 'dc-l' }, h('b', { text: 'Copy invite again' }), h('small', { text: 'With a new password; anyone already in stays in' }))),
        h('div', { class: 'dc-mi dc-mi-days' }, icon('history', 16), h('span', { class: 'dc-l' }, h('b', { text: 'Remembered for' })),
          // The words chosen, in a chip; the select under them covers the row.
          wordSelect({ options: DAY_WORDS, value: String(l.days), label: 'Remembered for', onChange: (v) => patch(l, { days: Number(v) }) })),
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

// What a person is told when the share server refuses: its sentences are
// for programs; these are for people.
function plainError(msg, status) {
  const m = String(msg || '')
  if (/password is 6 to 100/i.test(m)) return 'A password needs 6 to 100 characters.'
  if (/no (note|link) '/i.test(m)) return 'That is no longer here. The list has been read again.'
  if (status === 429) return 'Too many tries just now. Wait a moment and try again.'
  if (status >= 500) return 'The share server did not answer. Try again in a moment.'
  return m ? m.charAt(0).toUpperCase() + m.slice(1) + (/[.!?]$/.test(m) ? '' : '.') : `The share server answered ${status}.`
}

