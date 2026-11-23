// The standalone share page at /s/<token>.
// Handles the password gate, optional group name prompt, file viewing for
// every file type (Markdown, image, SVG, PDF, sandboxed HTML, audio, video,
// text/code, download card), and dynamically loads the `share.collaboration`
// module when a collaboration plugin (such as Comments & suggestions) is on.

import { h, icon, put, tone, TONE_INK, formatBytes } from '../dom.js'
import { renderMarkdown } from '../markdown.js'

const boot = JSON.parse(document.getElementById('sh-boot').textContent)
const P = boot.prefix
const HOME = /^https:\/\//.test(boot.home || '') ? boot.home : null
const mount = document.getElementById('sh-root')
const SHARE = { 'X-Loam-Share': '1' }
const VISITOR_NAME_KEY = 'sh:visitor_name'

function savedVisitorName() {
  try { return (localStorage.getItem(VISITOR_NAME_KEY) || '').trim() } catch { return '' }
}
function saveVisitorName(v) {
  try {
    const clean = (v || '').trim()
    if (clean) localStorage.setItem(VISITOR_NAME_KEY, clean)
  } catch { /* private mode */ }
}
function clearVisitorName() {
  try { localStorage.removeItem(VISITOR_NAME_KEY) } catch { /* private mode */ }
}

async function post(route, body) {
  const res = await fetch(`${P}/${route}`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...SHARE },
    body: JSON.stringify(body || {}),
  })
  let data = null
  try { data = await res.json() } catch { data = null }
  return { ok: res.ok, status: res.status, data }
}

function made() {
  if (!HOME) return h('span', { class: 'sh-made' }, h('i'), 'Made with Loam')
  return h('a', { class: 'sh-made', href: HOME, target: '_blank', rel: 'noopener noreferrer' }, h('i'), 'Made with Loam')
}

function face(name, size = 22) {
  return h('span', {
    class: 'sh-face',
    style: { width: `${size}px`, height: `${size}px`, fontSize: `${Math.round(size * 0.46)}px`, background: tone(name), color: TONE_INK },
    text: (name || '?').charAt(0).toUpperCase(),
  })
}

function shake(el) {
  el.classList.remove('sh-shake')
  void el.offsetWidth
  el.classList.add('sh-shake')
}

// -- password gate ------------------------------------------------------------

function gate() {
  document.title = boot.title
  const input = h('input', {
    class: 'sh-gate-in', type: 'password', placeholder: 'Password',
    autocomplete: 'current-password', 'aria-label': 'Password', autofocus: true,
  })
  const msg = h('p', {
    class: 'sh-gate-msg', role: 'status',
    text: `Remembered on this browser for ${boot.days} day${boot.days === 1 ? '' : 's'}.`,
  })
  const btn = h('button', { class: 'sh-gate-go', type: 'submit' }, 'Open')
  const meta = boot.kind !== 'markdown'
    ? [boot.label, formatBytes(boot.size)].filter(Boolean).join(' · ')
    : ''
  const card = h('form', { class: 'sh-gate-card' },
    h('div', { class: 'sh-gate-who' }, face(boot.owner, 22), h('span', { text: `${boot.owner} shared this with you` })),
    h('h1', { class: 'sh-gate-title', text: boot.title }),
    meta ? h('div', { class: 'sh-gate-meta', text: meta }) : null,
    input, btn, msg)
  card.addEventListener('submit', async (e) => {
    e.preventDefault()
    if (!input.value) { shake(card); input.focus(); return }
    btn.disabled = true
    btn.textContent = 'Opening…'
    const payload = { password: input.value }
    if (!boot.named && boot.mode !== 'view') {
      const remembered = savedVisitorName()
      if (remembered) payload.name = remembered
    }
    const r = await post('open', payload)
    if (r.ok) { card.classList.add('sh-gate-ok'); setTimeout(() => location.reload(), 240); return }
    btn.disabled = false
    btn.textContent = 'Open'
    msg.textContent = r.data?.error || 'That did not work. Try again.'
    msg.classList.add('bad')
    input.select()
    shake(card)
  })
  put(mount, h('main', { class: 'sh-gate' }, card), made())
  setTimeout(() => input.focus(), 50)
}

function gone() {
  put(mount,
    h('main', { class: 'sh-gate' },
      h('div', { class: 'sh-gate-card sh-gone-card' },
        h('h1', { class: 'sh-gate-title', text: 'This link has been turned off' }),
        h('p', { class: 'sh-gate-msg', text: `${boot.owner} turned it off or removed the file. Ask ${boot.owner} for a new link to see it again.` }))),
    made())
}

// -- group link name prompt ---------------------------------------------------

async function askName() {
  const remembered = !boot.named ? savedVisitorName() : ''
  let initialErr = ''
  if (remembered) {
    const r = await post('api/name', { name: remembered })
    if (r.ok) return location.reload()
    if (r.status === 409 && r.data?.error) initialErr = r.data.error
  }
  const input = h('input', {
    class: 'sh-gate-in', type: 'text', placeholder: 'Your name',
    autocomplete: 'name', 'aria-label': 'Your name', maxlength: '60', value: remembered,
  })
  const btn = h('button', { class: 'sh-gate-go', type: 'submit' }, 'Continue')
  const msg = h('p', {
    class: `sh-gate-msg${initialErr ? ' bad' : ''}`, role: 'status',
    text: initialErr || 'Your comments show under this name. Asked once on this browser.',
  })
  const card = h('form', { class: 'sh-gate-card' },
    h('div', { class: 'sh-gate-who' }, face(boot.owner, 22), h('span', { text: `${boot.owner} shared this with you` })),
    h('h1', { class: 'sh-gate-title', text: 'What should we call you?' }),
    input, btn, msg)
  card.addEventListener('submit', async (e) => {
    e.preventDefault()
    if (!input.value.trim()) { shake(card); return }
    btn.disabled = true
    const r = await post('api/name', { name: input.value })
    if (r.ok) {
      if (!boot.named) saveVisitorName(input.value)
      return location.reload()
    }
    btn.disabled = false
    msg.textContent = r.data?.error || 'That did not work.'
    msg.classList.add('bad')
    shake(card)
  })
  put(mount, h('main', { class: 'sh-gate' }, card), made())
  setTimeout(() => { input.focus(); if (remembered) input.select() }, 50)
}

// -- shared file page ---------------------------------------------------------

const store = {
  get(k, d) { try { const v = localStorage.getItem(`sh:${P}:${k}`); return v === null ? d : JSON.parse(v) } catch { return d } },
  set(k, v) { try { localStorage.setItem(`sh:${P}:${k}`, JSON.stringify(v)) } catch { /* private mode */ } },
}

async function filePage() {
  document.title = `${boot.title} · ${boot.owner}`
  let info
  try {
    const res = await fetch(`${P}/api/file`, { credentials: 'same-origin', headers: SHARE })
    if (res.status === 401 || res.status === 404) return location.reload()
    info = await res.json()
    if (!res.ok) throw new Error(info?.error || `Server answered ${res.status}`)
  } catch (err) {
    put(mount, h('main', { class: 'sh-gate' }, h('div', { class: 'sh-gate-card' }, h('p', { text: err.message }))), made())
    return
  }

  const anonymous = Boolean(info.me?.anonymous) || (!info.me?.name && !boot.name)
  const name = anonymous ? '' : (info.me?.name || boot.name || '')
  const modeDesc = info.kind === 'markdown'
    ? { view: 'Can view', comment: 'Can comment and suggest', edit: 'Can edit' }[info.mode] || 'Can view'
    : { view: 'Can view', comment: 'Can view and comment' }[info.mode] || 'Can view'

  const dlLink = h('a', {
    class: 'sh-barbtn', href: `${P}/download`, download: info.filename || 'file',
    title: 'Download', 'aria-label': `Download ${info.filename || 'file'}`,
  }, icon('download', 15), h('span', { class: 'sh-barbtn-lbl', text: 'Download' }))

  const agentBtn = h('button', {
    class: 'sh-iconbtn sh-agentbtn', type: 'button',
    title: 'Connect my agent', 'aria-label': 'Connect my agent',
    onclick: () => openAgent(anonymous ? '' : name, info),
  }, icon('robot', 18))

  let menu = null
  const meBtn = anonymous
    ? h('button', {
        class: 'sh-iconbtn', type: 'button', title: 'Link options', 'aria-label': 'Link options',
        onclick: () => toggleMe(),
      }, icon('dots', 18))
    : h('button', {
        class: 'sh-mebtn', type: 'button', title: name, 'aria-label': `You: ${name}`,
        onclick: () => toggleMe(),
      }, face(name, 28))
  const meWrap = h('div', { class: 'sh-ddw sh-mewrap' }, meBtn)

  function toggleMe() {
    if (menu) { menu.remove(); menu = null; meWrap.classList.remove('open'); return }
    menu = h('div', { class: 'sh-menu sh-memenu', role: 'menu' },
      anonymous
        ? h('div', { class: 'sh-mehead' }, h('div', {}, h('b', { text: 'Viewing link' }), h('small', { text: modeDesc })))
        : h('div', { class: 'sh-mehead' },
            face(name, 32),
            h('div', {}, h('b', { text: name }), h('small', { text: modeDesc }))),
      h('button', { class: 'sh-mi', type: 'button', role: 'menuitem', onclick: forget },
        icon('x', 17),
        h('span', { class: 'sh-l' },
          h('b', { text: 'Forget this browser' }),
          h('small', { text: 'The password is asked again next time' }))))
    meWrap.append(menu)
    meWrap.classList.add('open')
    menu.style.right = ''
    const r = menu.getBoundingClientRect()
    const vw = document.documentElement.clientWidth
    const pad = 8
    if (r.left < pad) menu.style.right = `${-(pad - r.left)}px`
    else if (r.right > vw - pad) menu.style.right = `${r.right - (vw - pad)}px`
  }
  document.addEventListener('mousedown', (e) => { if (menu && !meWrap.contains(e.target)) toggleMe() })

  async function forget() {
    if (!boot.named && !anonymous) clearVisitorName()
    await post('forget', {})
    location.reload()
  }

  // Case A: Markdown with collaboration plugin enabled -> mount full calm editor
  if (info.kind === 'markdown' && boot.collab?.entry) {
    try {
      const mod = await import(boot.collab.entry)
      if (typeof mod.mountMarkdown === 'function') {
        const hostEl = h('div', { class: 'sh-sharepage dc-sharepage' })
        put(mount, hostEl, made())
        await mod.mountMarkdown({
          container: hostEl,
          apiBase: `${P}/api/collab/`,
          headers: SHARE,
          title: info.title || boot.title,
          owner: boot.owner,
          mode: info.mode,
          store,
          headEnd: () => [dlLink, agentBtn, meWrap],
          onGone: gone,
        })
        return
      }
    } catch (err) {
      console.warn('sharing: collaboration markdown editor unavailable, falling back to read-only view:', err)
    }
  }

  // Case B: Any non-Markdown file (or Markdown with Comments off)
  const commentsCountEl = h('span', { class: 'sh-barbtn-lbl', text: 'Comments' })
  const commentsBtn = boot.collab?.entry ? h('button', {
    class: 'sh-barbtn sh-comments-jump', type: 'button',
    onclick: () => panelAside?.scrollIntoView({ behavior: 'smooth', block: 'start' }),
  }, icon('comment', 15), commentsCountEl) : null

  const subText = [
    `${boot.owner}’s file`,
    info.label,
    formatBytes(info.size),
  ].filter(Boolean).join(' · ')

  const bar = h('header', { class: 'sh-bar' },
    h('div', { class: 'sh-bar-title' },
      h('b', { text: info.title || info.filename }),
      h('span', { class: 'sh-bar-sub', text: subText })),
    h('div', { class: 'sh-bar-end' },
      commentsBtn,
      dlLink,
      agentBtn,
      meWrap))

  const viewerNode = renderFileContent(info)
  const viewOnly = info.mode === 'view'
  const panelAside = boot.collab?.entry ? h('aside', { class: 'sh-panel-col' }) : null
  if (viewOnly && panelAside) {
    panelAside.hidden = true
    if (commentsBtn) commentsBtn.hidden = true
  }

  const stage = h('div', { class: `sh-stage${panelAside && !viewOnly ? ' with-panel' : ''} kind-${info.kind}` },
    h('main', { class: 'sh-viewer' }, viewerNode),
    panelAside)

  put(mount, h('div', { class: 'sh-page' }, bar, stage), made())

  if (panelAside && boot.collab?.entry) {
    try {
      const mod = await import(boot.collab.entry)
      if (typeof mod.mountPanel === 'function') {
        await mod.mountPanel({
          container: panelAside,
          apiBase: `${P}/api/collab/`,
          headers: SHARE,
          onGone: gone,
          onCount: (open, closed = 0) => {
            commentsCountEl.textContent = open > 0 ? `Comments · ${open}` : 'Comments'
            if (viewOnly) {
              const show = (open + closed) > 0
              panelAside.hidden = !show
              stage.classList.toggle('with-panel', show)
              if (commentsBtn) commentsBtn.hidden = !show
            }
          },
        })
      }
    } catch (err) {
      console.warn('sharing: collaboration panel unavailable:', err)
      panelAside.remove()
      stage.classList.remove('with-panel')
      commentsBtn?.remove()
    }
  }
}

function renderFileContent(info) {
  const rawUrl = `${P}/raw`
  const dlUrl = `${P}/download`
  const frameUrl = `${P}/frame`

  if (info.kind === 'markdown') {
    return h('article', { class: 'sh-prose' }, renderMarkdown(info.text || ''))
  }
  if (info.kind === 'image' || info.kind === 'svg') {
    return h('div', { class: 'sh-media-stage' },
      h('img', { class: 'sh-img', src: rawUrl, alt: info.filename || '' }))
  }
  if (info.kind === 'pdf') {
    return h('div', { class: 'sh-frame-stage sh-pdf-stage' },
      h('iframe', { class: 'sh-pdf', referrerpolicy: 'no-referrer', src: rawUrl, title: info.filename || 'PDF document' }),
      h('div', { class: 'sh-pdf-bar' },
        h('a', { class: 'sh-barbtn sh-pdf-open', href: rawUrl, target: '_blank', rel: 'noopener noreferrer' },
          icon('external', 14), h('span', { text: 'Open full PDF' }))))
  }
  if (info.kind === 'html') {
    return h('div', { class: 'sh-frame-stage' },
      h('iframe', {
        class: 'sh-html',
        sandbox: 'allow-scripts allow-forms',
        referrerpolicy: 'no-referrer',
        title: info.filename || 'Shared HTML page',
        src: frameUrl,
      }))
  }
  if (info.kind === 'audio') {
    return h('div', { class: 'sh-card-stage' },
      h('div', { class: 'sh-filecard' },
        h('div', { class: 'sh-filecard-ic' }, icon('file', 26)),
        h('h2', { text: info.filename || info.title }),
        h('p', { class: 'sh-filecard-meta', text: [info.label, formatBytes(info.size)].filter(Boolean).join(' · ') }),
        h('audio', { class: 'sh-audio', controls: true, preload: 'metadata', src: rawUrl }),
        h('a', { class: 'sh-gate-go sh-dlprimary', href: dlUrl, download: info.filename || 'file' }, icon('download', 16), 'Download')))
  }
  if (info.kind === 'video') {
    return h('div', { class: 'sh-media-stage' },
      h('video', { class: 'sh-video', controls: true, playsinline: true, preload: 'metadata', src: rawUrl }))
  }
  if (info.kind === 'text' && typeof info.text === 'string') {
    return h('div', { class: 'sh-code-stage' },
      h('pre', { class: 'sh-code' }, h('code', { text: info.text })))
  }
  // Download card for archives and all non-browser-renderable file types
  return h('div', { class: 'sh-card-stage' },
    h('div', { class: 'sh-filecard' },
      h('div', { class: 'sh-filecard-ic' }, icon('file', 28)),
      h('h2', { text: info.filename || info.title }),
      h('p', { class: 'sh-filecard-meta', text: [info.label, formatBytes(info.size)].filter(Boolean).join(' · ') }),
      h('p', { class: 'sh-filecard-note', text: 'Download this file to open it on your device.' }),
      h('a', { class: 'sh-gate-go sh-dlprimary', href: dlUrl, download: info.filename || 'file' }, icon('download', 16), 'Download')))
}

// -- Connect my agent dialog --------------------------------------------------

let agentDialog = null
async function openAgent(name, info) {
  if (agentDialog) return
  const body = h('div', { class: 'sh-agentbody' }, h('p', { class: 'sh-quiet', text: '…' }))
  const scrim = h('div', { class: 'sh-dlg-scrim', onclick: (e) => { if (e.target === scrim) close() } })
  const canDesc = info.collab
    ? (info.kind === 'markdown'
        ? { view: 'read the file and its notes', comment: 'comment and suggest, like you', edit: 'edit, like you' }[info.mode]
        : { view: 'read and download the file', comment: 'read, download and comment, like you' }[info.mode])
    : 'read and download this file'

  agentDialog = h('div', { class: 'sh-dlg sh-agentdlg', role: 'dialog', 'aria-label': 'Connect my agent' },
    h('button', { class: 'sh-ib sh-dlg-x', type: 'button', 'aria-label': 'Close', onclick: close }, icon('x', 17)),
    h('div', { class: 'sh-dlg-head' },
      h('span', { class: 'sh-agentface' }, name ? face(name, 40) : icon('robot', 22)),
      h('div', {},
        h('h2', { text: 'Connect my agent' }),
        h('p', { text: name ? `It joins as ${name}’s agent and can ${canDesc}.` : `Your agent can ${canDesc}.` }))),
    body)
  scrim.append(agentDialog)
  document.body.append(scrim)
  requestAnimationFrame(() => scrim.classList.add('on'))
  function close() { scrim.classList.remove('on'); setTimeout(() => scrim.remove(), 200); agentDialog = null }
  document.addEventListener('keydown', function esc(e) { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', esc) } })

  const st0 = await fetch(`${P}/api/agent-key`, { credentials: 'same-origin', headers: SHARE }).then(x => x.json()).catch(() => ({}))
  if (st0?.made) {
    const dt = new Date(st0.made).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
    await new Promise((resolve) => {
      const ask = () => put(body,
        h('p', { class: 'sh-agentask', text: 'Make a new key? Your agent stops working here until you give it the new one.' }),
        h('div', { class: 'sh-agentrow2' },
          h('button', { class: 'sh-gate-go sh-agentno', type: 'button', onclick: first }, 'Keep the old key'),
          h('button', { class: 'sh-gate-go', type: 'button', onclick: resolve }, 'Make a new key')))
      const first = () => put(body,
        h('p', { class: 'sh-agentfoot', text: `Your agent has a key, made ${dt}. It works until the link is turned off.` }),
        h('button', { class: 'sh-gate-go', type: 'button', onclick: ask }, 'Make a new key'),
        h('p', { class: 'sh-agentfoot', text: 'A new key stops the old one working at once.' }))
      first()
    })
  } else {
    await new Promise((resolve) => put(body,
      h('button', { class: 'sh-gate-go', type: 'button', onclick: resolve }, icon('robot', 17), 'Make a key for my agent'),
      h('p', { class: 'sh-agentfoot', text: 'Your agent uses it to access this shared file, as you can.' })))
  }

  put(body, h('p', { class: 'sh-quiet', text: 'Making a key…' }))
  const r = await post('api/agent-key', {})
  if (!r.ok) { put(body, h('p', { class: 'sh-gate-msg bad', text: r.data?.error || 'No key could be made.' })); return }
  const k = r.data
  const text = [
    'I’d like your help with a shared file.',
    `File: ${k.url}`,
    `Key: ${k.key}`,
    `Guide: ${k.guide}`,
    `Read the guide first (send the key as "Authorization: Bearer <key>"). Use plain HTTP with JSON; your notes show as ${k.as}.`,
  ].join('\n')
  const copyBtn = h('button', { class: 'sh-gate-go', type: 'button' }, icon('file', 15), 'Copy for my agent')
  copyBtn.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(text) } catch { /* shown below */ }
    copyBtn.textContent = 'Copied'
    setTimeout(() => put(copyBtn, icon('file', 15), 'Copy for my agent'), 1600)
  })
  put(body,
    h('div', { class: 'sh-agentblock' },
      agentRow('File', k.url), agentRow('Key', k.key), agentRow('Guide', k.guide)),
    copyBtn,
    h('p', { class: 'sh-agentfoot', text: 'Paste it into any agent. The key stops working if the link is turned off.' }))
}

function agentRow(label, value) {
  return h('div', { class: 'sh-agentrow' }, h('span', { class: 'sh-agentk', text: label }), h('code', { text: value }))
}

if (boot.state === 'gate') gate()
else if (boot.state === 'name') askName()
else filePage()
