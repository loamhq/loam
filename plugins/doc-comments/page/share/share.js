// The share page: what a person outside Loam sees at /s/<token>. The server
// says which of three states it is in (dc-boot): the password, a name for a
// group link, or the doc in the same calm editor Loam uses, with a slim bar
// and nothing else of the app. Everything the person or the doc says is drawn
// as text (dom.js's `h` never parses HTML).

import { createDocEditor } from '../editor/editor.js'
import { docApi } from '../api.js'
import { h, icon, put } from '../editor/dom.js'

const boot = JSON.parse(document.getElementById('dc-boot').textContent)
const P = boot.prefix
const HOME = 'https://github.com/loamhq/loam'
const mount = document.getElementById('dc-share')
const SHARE = { 'X-Loam-Share': '1' }

async function post(route, body) {
  const res = await fetch(`${P}/${route}`, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...SHARE }, body: JSON.stringify(body || {}),
  })
  let data = null
  try { data = await res.json() } catch { data = null }
  return { ok: res.ok, status: res.status, data }
}

function made() {
  return h('a', { class: 'dc-made', href: HOME, target: '_blank', rel: 'noopener noreferrer' }, h('i'), 'Made with Loam')
}

function face(name, size = 22) {
  return h('span', { class: 'dc-face', style: { width: `${size}px`, height: `${size}px`, fontSize: `${Math.round(size * 0.46)}px` }, text: (name || '?').charAt(0).toUpperCase() })
}

// -- the password ----------------------------------------------------------------

function gate() {
  document.title = boot.title
  const input = h('input', { class: 'dc-gate-in', type: 'password', placeholder: 'Password', autocomplete: 'current-password', 'aria-label': 'Password', autofocus: true })
  const msg = h('p', { class: 'dc-gate-msg', role: 'status' }, `Remembered on this browser for ${boot.days} day${boot.days === 1 ? '' : 's'}.`)
  const btn = h('button', { class: 'dc-gate-go', type: 'submit' }, 'Open')
  const card = h('form', { class: 'dc-gate-card' },
    h('div', { class: 'dc-gate-who' }, face(boot.owner, 22), h('span', { text: `${boot.owner} shared this with you` })),
    h('h1', { class: 'dc-gate-title', text: boot.title }),
    input, btn, msg)
  card.addEventListener('submit', async (e) => {
    e.preventDefault()
    if (!input.value) { shake(card); input.focus(); return }
    btn.disabled = true
    btn.textContent = 'Opening…'
    const r = await post('open', { password: input.value })
    if (r.ok) { card.classList.add('dc-gate-ok'); setTimeout(() => location.reload(), 260); return }
    btn.disabled = false
    btn.textContent = 'Open'
    msg.textContent = r.data?.error || 'That did not work. Try again.'
    msg.classList.add('bad')
    input.select()
    shake(card)
  })
  put(mount, h('main', { class: 'dc-gate' }, card), made())
  setTimeout(() => input.focus(), 50)
}

function shake(el) {
  el.classList.remove('dc-shake')
  void el.offsetWidth
  el.classList.add('dc-shake')
}

// -- a name, for a group link -------------------------------------------------------

function askName() {
  const input = h('input', { class: 'dc-gate-in', type: 'text', placeholder: 'Your name', autocomplete: 'name', 'aria-label': 'Your name', maxlength: '60' })
  const btn = h('button', { class: 'dc-gate-go', type: 'submit' }, 'Continue')
  const msg = h('p', { class: 'dc-gate-msg', role: 'status' }, 'Your notes show under this name. Asked once on this browser.')
  const card = h('form', { class: 'dc-gate-card' },
    h('div', { class: 'dc-gate-who' }, face(boot.owner, 22), h('span', { text: `${boot.owner} shared this with you` })),
    h('h1', { class: 'dc-gate-title', text: 'What should we call you?' }),
    input, btn, msg)
  card.addEventListener('submit', async (e) => {
    e.preventDefault()
    if (!input.value.trim()) { shake(card); return }
    btn.disabled = true
    const r = await post('api/name', { name: input.value })
    if (r.ok) return location.reload()
    btn.disabled = false
    msg.textContent = r.data?.error || 'That did not work.'
    msg.classList.add('bad')
    shake(card)
  })
  put(mount, h('main', { class: 'dc-gate' }, card), made())
  setTimeout(() => input.focus(), 50)
}

// -- the doc ---------------------------------------------------------------------------

const store = {
  get(k, d) { try { const v = localStorage.getItem(`dc:${P}:${k}`); return v === null ? d : JSON.parse(v) } catch { return d } },
  set(k, v) { try { localStorage.setItem(`dc:${P}:${k}`, JSON.stringify(v)) } catch { /* private mode */ } },
}

async function docPage() {
  document.title = `${boot.title} · ${boot.owner}`
  const api = docApi({ base: `${P}/api/`, root: '', path: '', headers: SHARE })
  let doc
  try {
    doc = await api.get()
  } catch (err) {
    if (err.status === 401 || err.status === 404) return location.reload()
    put(mount, h('main', { class: 'dc-gate' }, h('div', { class: 'dc-gate-card' }, h('p', { text: err.message }))), made())
    return
  }
  const name = doc.me?.name || boot.name || ''
  const phone = matchMedia('(max-width: 760px)')
  let menu = null
  const agentBtn = h('button', { class: 'dc-gbtn dc-agentbtn', type: 'button', title: 'Connect my agent', 'aria-label': 'Connect my agent', onclick: () => openAgent() }, robot())
  const meBtn = h('button', { class: 'dc-mebtn', type: 'button', title: name, 'aria-label': `You: ${name}`, onclick: () => toggleMe() }, face(name, 28))
  const meWrap = h('div', { class: 'dc-ddw dc-mewrap' }, meBtn)
  const ed = createDocEditor({
    api, doc,
    phone: phone.matches,
    settings: store,
    titleBar: { title: boot.title, sub: `${boot.owner}’s doc` },
    history: false,
    headEnd: () => [agentBtn, meWrap],
    openLink: (ref) => { if (/^https?:/i.test(ref)) window.open(ref, '_blank', 'noopener,noreferrer') },
  })
  phone.addEventListener('change', () => ed.setPhone(phone.matches))
  put(mount, h('div', { class: 'dc-sharepage' }, ed.dom), made())

  function toggleMe() {
    if (menu) { menu.remove(); menu = null; meWrap.classList.remove('open'); return }
    menu = h('div', { class: 'dc-menu dc-memenu', role: 'menu' },
      h('div', { class: 'dc-mehead' }, face(name, 32), h('div', {}, h('b', { text: name }), h('small', { text: { view: 'Can view', comment: 'Can comment and suggest', edit: 'Can edit' }[boot.mode] }))),
      h('button', { class: 'dc-mi', type: 'button', role: 'menuitem', onclick: forget }, icon('x', 17), h('span', { class: 'dc-l' }, h('b', { text: 'Forget this browser' }), h('small', { text: 'The password is asked again next time' }))))
    meWrap.append(menu)
    meWrap.classList.add('open')
  }
  document.addEventListener('mousedown', (e) => { if (menu && !meWrap.contains(e.target)) toggleMe() })

  async function forget() {
    await post('forget', {})
    location.reload()
  }

  // "N new since your last visit": what others wrote since this browser last
  // looked, stepped through one by one.
  const last = store.get('last', null)
  const fresh = []
  if (last) {
    for (const n of ed.notes()) {
      if (n.state !== 'open') continue
      const mine = (a) => a?.id === doc.me?.id
      if (n.created > last && !mine(n.author)) fresh.push(n.id)
      else if ((n.replies || []).some(r => r.created > last && !mine(r.author))) fresh.push(n.id)
    }
  }
  store.set('last', new Date().toISOString())
  if (fresh.length) {
    let i = -1
    const label = h('span', { text: `${fresh.length} new since your last visit` })
    const pill = h('div', { class: 'dc-newpill' },
      h('button', { class: 'dc-newstep', type: 'button', onclick: () => { i = (i + 1) % fresh.length; ed.focusNote(fresh[i]); label.textContent = `${i + 1} of ${fresh.length} new` } }, h('i'), label, icon('chevronDown', 12)),
      h('button', { class: 'dc-newx', type: 'button', 'aria-label': 'Dismiss', onclick: () => ed.setNotice(null) }, icon('x', 12)))
    ed.setNotice(pill)
  }

  // Connect my agent: a key for this person's agent, and plain words for it.
  let dialog = null
  async function openAgent() {
    if (dialog) return
    const body = h('div', { class: 'dc-agentbody' }, h('p', { class: 'dc-quiet', text: 'Making a key…' }))
    const scrim = h('div', { class: 'dc-dlg-scrim', onclick: (e) => { if (e.target === scrim) close() } })
    dialog = h('div', { class: 'dc-dlg', role: 'dialog', 'aria-label': 'Connect my agent' },
      h('button', { class: 'dc-ib dc-dlg-x', type: 'button', 'aria-label': 'Close', onclick: () => close() }, icon('x', 17)),
      h('div', { class: 'dc-dlg-head' }, h('span', { class: 'dc-agentface' }, face(name, 40)),
        h('div', {}, h('h2', { text: 'Connect my agent' }), h('p', { text: `It joins as ${name}’s agent and can ${{ view: 'read the doc and its notes', comment: 'comment and suggest, like you', edit: 'edit, like you' }[boot.mode]}.` }))),
      body)
    scrim.append(dialog)
    document.body.append(scrim)
    requestAnimationFrame(() => scrim.classList.add('on'))
    function close() { scrim.classList.remove('on'); setTimeout(() => scrim.remove(), 200); dialog = null }
    document.addEventListener('keydown', function esc(e) { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', esc) } })
    const r = await post('api/agent-key', {})
    if (!r.ok) { put(body, h('p', { class: 'dc-gate-msg bad', text: r.data?.error || 'No key could be made.' })); return }
    const k = r.data
    const text = [
      `I'd like your help with a shared doc.`,
      `Doc: ${k.doc}`,
      `Key: ${k.key}`,
      `Guide: ${k.guide}`,
      `Read the guide first (send the key as "Authorization: Bearer <key>"). Use plain HTTP with JSON; your notes show as ${k.as}.`,
    ].join('\n')
    const copy = h('button', { class: 'dc-gate-go dc-copy', type: 'button' }, icon('file', 15), 'Copy for my agent')
    copy.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(text) } catch { /* shown below to copy by hand */ }
      copy.textContent = 'Copied'
      setTimeout(() => { put(copy, icon('file', 15), 'Copy for my agent') }, 1600)
    })
    put(body,
      h('div', { class: 'dc-agentblock' },
        row('Doc', k.doc), row('Key', k.key), row('Guide', k.guide)),
      copy,
      h('p', { class: 'dc-agentfoot', text: 'Paste it into Claude, ChatGPT or any agent. The key stops working if the link is turned off.' }))
  }
}

function row(label, value) {
  return h('div', { class: 'dc-agentrow' }, h('span', { class: 'dc-agentk', text: label }), h('code', { text: value }))
}

function robot() {
  const s = icon('dots', 18)
  const NS = 'http://www.w3.org/2000/svg'
  s.replaceChildren()
  for (const [tag, attrs] of [
    ['rect', { x: 4.5, y: 8, width: 15, height: 11, rx: 3.5 }], ['path', { d: 'M12 8V5' }], ['circle', { cx: 12, cy: 4, r: 1.2 }],
    ['circle', { cx: 9.3, cy: 13, r: 1.1, fill: 'currentColor' }], ['circle', { cx: 14.7, cy: 13, r: 1.1, fill: 'currentColor' }], ['path', { d: 'M2.5 12.5v3M21.5 12.5v3' }],
  ]) {
    const el = document.createElementNS(NS, tag)
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v)
    s.append(el)
  }
  return s
}

if (boot.state === 'gate') gate()
else if (boot.state === 'name') askName()
else docPage()
