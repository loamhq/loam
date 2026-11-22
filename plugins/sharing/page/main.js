// Sharing:
// - `files.root` (`@sharing`, "Shared"): Shared by me + Shared with me for any file type.
// - `files.action` (slot `menu` & slot `action`): Share… / Share settings on every vault file (no folders).
// - `files.viewer` (slot `viewer`, order 0): viewer for `@sharing` Shared-with-me files
//   (renders Markdown read-only, images, SVG safely, PDF, sandboxed HTML, audio, video,
//   code/text, or download card — and mounts `files.viewer` slot `panel` when available).

import { createShareDialog } from './dialog.js'
import { createWithMe, withmeId } from './withme.js'
import { renderMarkdown } from './markdown.js'
import { h as domH, icon, tone, when, formatBytes } from './dom.js'

const BASE = '/x/sharing/'
const ROOT_ID = '@sharing'
const LEAVE_MS = 340
const MODE_ORDER = ['edit', 'comment', 'view']
const MODE_WORD = { view: 'View', comment: 'Comment', edit: 'Edit' }

export function activate(loam) {
  const host = loam.ui?.host
  if (!host) return

  const onStaging = String(globalThis.location?.hostname || '').startsWith('next.')
  let reach = loam.settings?.get?.('reach', 'everywhere') || 'everywhere'
  const allowed = () => onStaging || reach === 'everywhere'

  fetch(`${BASE}config`, { credentials: 'same-origin' })
    .then(r => (r.ok ? r.json() : null))
    .then((cfg) => {
      if (!cfg || disposed) return
      const was = allowed()
      reach = cfg.reach === 'staging' ? 'staging' : 'everywhere'
      loam.settings?.set?.('reach', reach)
      if (was !== allowed()) host.schedule?.()
    })
    .catch(() => {})

  loam.ui.styles?.add?.('assets/sharing.css')
  let disposed = false
  const tip = faceTip()
  const filesOf = () => host.files
  const registryOf = () => filesOf()?.registry || null

  // -- Shared by me state ----------------------------------------------------

  const shared = { files: [], loaded: false, loading: false, rows: new Map(), leaving: new Map(), seen: new Set() }
  const fileKey = (root, path) => `${root}:${path}`

  async function loadShared() {
    const res = await fetch(`${BASE}shared`, { credentials: 'same-origin' })
    if (!res.ok) throw new Error('Shared could not be read just now.')
    const got = await res.json()
    const list = got.files || []
    const files = filesOf()
    await Promise.all(list.map(async (f) => {
      const k = fileKey(f.root, f.path)
      if (shared.rows.has(k) || typeof files?.stat !== 'function') return
      try { shared.rows.set(k, await files.stat(f.root, f.path)) } catch { /* listed by address */ }
    }))
    const now = new Set(list.map(f => f.id))
    if (shared.loaded) {
      for (const f of shared.files) {
        if (now.has(f.id) || shared.leaving.has(f.id)) continue
        const at = shared.files.indexOf(f)
        shared.leaving.set(f.id, { file: f, at })
        setTimeout(() => { shared.leaving.delete(f.id); host.schedule?.() }, LEAVE_MS)
      }
    }
    shared.files = list
    shared.loaded = true
  }

  const refreshShared = () => loadShared().catch(() => {}).finally(() => host.schedule?.())
  const sharedEntry = (root, path) => shared.files.find(f => f.root === root && f.path === path) || null
  const isShared = (root, path) => Boolean(sharedEntry(root, path))

  // Warm list quietly so Share buttons on open files show recipient faces immediately.
  loadShared().then(() => { if (!disposed) host.schedule?.() }).catch(() => {})

  function markSeen(root, path) {
    fetch(`${BASE}file/seen`, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ root, path }),
    }).catch(() => {})
  }

  function rowOf(f) {
    const name = f.filename || (f.path || '').split('/').pop()
    return {
      kind: 'file', name, size: undefined, modified: f.last, capabilities: {},
      ...(shared.rows.get(fileKey(f.root, f.path)) || {}), root: f.root, path: f.path,
    }
  }

  function sharedRow(f, entry, files, leaving = false) {
    const H = loam.ui.h
    const on = (f.links || []).filter(l => l.on)
    const modes = MODE_ORDER.filter(m => on.some(l => (l.effective_mode || l.mode) === m)).map(m => MODE_WORD[m])
    const modeText = modes.length === 1 ? modes[0] : ''
    const ago = typeof files?.ago === 'function' ? files.ago(f.last) : when(f.last)
    const fresh = f.new > 0 && !shared.seen.has(f.id)
    const meta = [
      fresh ? H('span', { class: 'sh-sd-new', text: `${f.new} new` }) : null,
      fresh ? ' · ' : null,
      [f.label, modeText, ago].filter(Boolean).join(' · '),
    ].filter(Boolean)
    const hadNew = f.new > 0
    return entry(rowOf(f), {
      meta,
      badge: hadNew ? H('span', { class: `sh-sd-dot${fresh ? '' : ' seen'}`, key: `dot:${f.id}`, role: fresh ? 'img' : null, 'aria-label': fresh ? 'Something new' : null }) : null,
      trail: people(on, f.id),
      onopen: () => { shared.seen.add(f.id); markSeen(f.root, f.path) },
      className: `sh-sd-entry${leaving ? ' sh-sd-leaving' : ''}`,
    })
  }

  function people(on, id) {
    const H = loam.ui.h
    if (!on.length) return null
    const who = on.map(l => ({ name: l.name || '', mode: l.effective_mode || l.mode, people: l.people || [] }))
    const say = (p) => p.name
      ? `${p.name} · can ${p.mode}`
      : `Group link · can ${p.mode}${p.people.length ? ` · ${p.people.slice(0, 3).join(', ')}${p.people.length > 3 ? '…' : ''}` : ''}`
    const shown = who.slice(0, 3)
    const more = who.length - shown.length
    const face = (p, i) => H('span', {
      class: `sh-sd-face${p.name ? '' : ' grp'}`, key: `face:${id}:${i}`, ...tip.props,
      style: p.name ? { background: tone(p.name) } : null,
      'data-tip': say(p),
      text: p.name ? p.name.charAt(0).toUpperCase() : null,
    }, p.name ? null : icon('people', 12))
    const nodes = shown.map(face)
    if (more > 0) {
      nodes.push(H('span', {
        class: 'sh-sd-face more', key: `face:${id}:more`, ...tip.props,
        'data-tip': who.slice(3).map(say).join('\n'), text: `+${more}`,
      }))
    }
    return H('span', {
      class: 'sh-sd-people', key: `people:${id}`, onclick: tip.swallow, oncontextmenu: tip.swallow,
      'aria-label': `Shared with ${who.map(p => p.name ? `${p.name} (can ${p.mode})` : `a group link (can ${p.mode})`).join(', ')}`,
    }, ...nodes)
  }

  // -- Shared with me --------------------------------------------------------

  const withMeOn = () => typeof filesOf()?.itemsMenu === 'function'
  const withme = createWithMe({
    base: BASE,
    root: ROOT_ID,
    schedule: () => host.schedule?.(),
    toast: (text, undo) => listToast(text, undo),
    side: () => filesOf()?.side?.() || null,
    registry: { draw: (...a) => registryOf()?.draw?.(...a) || [] },
    agentName: () => host.state?.agents?.list?.find(a => !a.archived)?.name || '',
  })

  // -- 1. Files › Shared (`files.root`) --------------------------------------

  const sharedRoot = loam.places?.files?.root?.add?.({
    id: 'sharing',
    name: 'Shared',
    desc: 'Shared by you, or with you.',
    icon: (size) => icon('people', size),
    when: () => allowed(),
    load: () => Promise.all([loadShared(), withMeOn() ? withme.load() : null]),
    menu: (path) => {
      const d = withme.byId(withmeId(path))
      return d ? withme.menuItems(d, (r, p, o) => (host.goFiles || (() => {}))(r, p, o)) : []
    },
    row: (path) => {
      const d = withme.byId(withmeId(path))
      return d ? withme.rowOf(d) : { kind: 'file', name: 'Shared file', capabilities: {} }
    },
    render({ go, files, entry }) {
      const H = loam.ui.h
      if (withMeOn() && !withme.st.loaded && !withme.st.loading) withme.load()
      const footers = registryOf()?.draw?.('files.share', {
        slot: 'footer', H, toast: (text) => listToast(text),
      }) || []
      if (withMeOn()) {
        return H('div', { class: 'sh-sd-sections', key: 'sd:sections' },
          H('div', { class: 'kicker fs-kicker sh-sd-kicker', text: 'Shared by me' }),
          sharedByMe({ go, files, entry }),
          H('div', { class: 'kicker fs-kicker sh-sd-kicker', text: 'Shared with me' }),
          withme.st.loaded ? withme.section({ entry, go, H }) : H('div', { class: 'fs-quiet', text: 'Loading…' }),
          ...footers)
      }
      return H('div', { class: 'sh-sd-sections', key: 'sd:sections' }, sharedByMe({ go, files, entry }), ...footers)
    },
  })

  function sharedByMe({ go, files, entry }) {
    const H = loam.ui.h
    if (!shared.loaded) {
      if (!shared.loading) {
        shared.loading = true
        loadShared().catch(() => {}).finally(() => { shared.loading = false; host.schedule?.() })
      }
      return H('div', { class: 'fs-quiet', text: 'Loading…' })
    }
    if (!shared.files.length && !shared.leaving.size) {
      return H('div', {
        class: 'fs-quiet sh-sd-empty', key: 'sd:empty',
        text: 'No file is shared with anyone outside Loam yet. Share any file from its ⋯ menu, or with the Share button when it is open.',
      })
    }
    const draw = typeof entry === 'function'
      ? entry
      : (row, x) => H('button', {
          class: 'setrow', key: `sd:${row.path}`,
          onclick: () => { x.onopen?.(); go(row.root, row.path, { isFile: true }) },
        }, H('div', { class: 'fs-words' }, H('div', { class: 'fs-name', text: row.name }), H('div', { class: 'fs-meta' }, x.meta)))
    const list = shared.files.map(f => ({ f, leaving: false }))
    for (const { file, at } of shared.leaving.values()) list.splice(Math.min(at, list.length), 0, { f: file, leaving: true })
    return H('div', { class: 'card card-group sh-sd' }, list.map(({ f, leaving }) => sharedRow(f, draw, files, leaving)))
  }

  // -- 2. Share actions (`files.action`, slot `menu` & slot `action`) --------

  const canShare = (r) => Boolean(allowed() && r && r.kind !== 'folder' && r.root === 'vault' && r.path)

  function openShareFor(r, titleFn = null, toastFn = null) {
    const fname = (r.path || '').split('/').pop() || 'file'
    const isMd = /\.(md|markdown)$/i.test(fname)
    let title = isMd ? fname.replace(/\.(md|markdown)$/i, '') : fname
    if (!titleFn && isMd) {
      filesOf()?.readText?.(r.root, r.path)
        .then((text) => { title = (/^#\s+(.+?)\s*#*\s*$/m.exec(text || '')?.[1]) || title })
        .catch(() => {})
    }
    const dlg = createShareDialog({
      base: BASE,
      root: r.root,
      path: r.path,
      title: titleFn || (() => title),
      toast: toastFn || ((text, undo) => listToast(text, undo)),
      onChange: () => refreshShared(),
      registry: registryOf(),
    })
    dlg.open()
  }

  const shareMenuReg = loam.places?.files?.action?.add?.({
    id: 'sharing.menu',
    slot: 'menu',
    when: (c) => canShare(c.resource),
    render: ({ resource }) => ({
      id: 'share',
      label: isShared(resource.root, resource.path) ? 'Share settings' : 'Share…',
      icon: (size) => icon('people', size),
      className: 'sh-menu-share',
      run: (r) => openShareFor(r),
    }),
  })

  const shareBtnReg = loam.places?.files?.action?.add?.({
    id: 'sharing.button',
    slot: 'action',
    when: (c) => canShare(c.resource || c.file),
    render: ({ resource, file, title, toast }) => {
      const r = resource || file
      const entry = sharedEntry(r.root, r.path)
      const live = (entry?.links || []).filter(l => l.on)
      const H = loam.ui.h
      const faces = live.length
        ? H('span', { class: 'sh-faces' }, live.slice(0, 2).map(l => l.name
            ? H('span', { class: 'sh-sf', style: { background: tone(l.name) }, text: l.name.charAt(0).toUpperCase() })
            : H('span', { class: 'sh-sf grp' }, icon('people', 11))))
        : null
      return H('button', {
        type: 'button',
        class: `sh-sharebtn secondary${live.length ? ' has' : ''}`,
        onclick: () => openShareFor(r, title, toast),
      }, faces, H('span', { text: 'Share' }))
    },
  })

  // -- 3. Shared-with-me viewer (`files.viewer`, slot `viewer`, order 0) -----

  const wmViews = new Map()

  function loadWithMeFile(files, file) {
    const key = `${file.root}:${file.path}`
    const wid = withmeId(file.path)
    const entry = { key, wid, status: 'loading', info: null, error: '' }
    wmViews.set(files, entry)
    const cur = () => wmViews.get(files)
    if (!wid) { entry.status = 'missing'; return entry }
    const begin = () => withme.req('GET', `withme/${wid}/file`).then((info) => {
      if (disposed || cur() !== entry) return
      entry.info = info
      entry.status = 'ready'
      const d = withme.byId(wid)
      if (d) {
        d.new = false
        d.state = 'ok'
        d.mode = info.mode || d.mode
        d.collab = Boolean(info.collab)
        d.kind = info.kind || d.kind
        d.filename = info.filename || d.filename
        d.title = info.title || d.title
      }
      if (file.row?.withme) {
        Object.assign(file.row.withme, { collab: Boolean(info.collab), mode: info.mode || file.row.withme.mode })
      }
      host.schedule?.()
    }).catch((err) => {
      if (disposed || cur() !== entry) return
      entry.status = err?.status === 410 ? 'gone' : err?.status === 404 ? 'missing' : 'down'
      entry.error = err?.message || 'Its Loam didn’t answer.'
      const d = withme.byId(wid)
      if (d && entry.status === 'gone') d.state = 'gone'
      host.schedule?.()
    })
    entry.retry = () => { entry.status = 'loading'; host.schedule?.(); begin() }
    if (!withme.st.loaded) withme.load(false)
    begin()
    return entry
  }

  function withMeState(entry, c) {
    const H = loam.ui.h
    const d = withme.byId(entry.wid)
    const title = d?.title || 'This file'
    const go = c.options.go || host.goFiles
    const remove = d ? H('button', { type: 'button', onclick: async () => { await withme.remove(d); go?.(ROOT_ID, '') } }, 'Remove it') : null
    const back = H('button', { type: 'button', class: entry.status === 'down' ? '' : 'go primary', onclick: () => go?.(ROOT_ID, '') }, 'Back to Shared')
    const say = {
      gone: ['No longer shared', `${d?.owner || 'Its owner'} turned off the link to “${title}”, or it expired. Nothing of it is kept here.`, [remove, back]],
      missing: ['Not in Shared with me', 'This file was removed from Shared with me on this Loam.', [back]],
      down: ['Couldn’t open it just now', entry.error || 'Its Loam didn’t answer.', [H('button', { type: 'button', class: 'go primary', onclick: () => entry.retry?.() }, 'Try again'), back]],
    }[entry.status] || ['', '', []]
    return H('div', { key: `sharing:state:${entry.key}:${entry.status}`, class: 'sh-wm-host' },
      H('div', { class: 'sh-wm-state', role: 'status' },
        H('span', { class: 'sh-wm-glyph' }, icon(entry.status === 'down' ? 'changed' : 'people', 22)),
        H('h2', { text: say[0] }), H('p', { text: say[1] }),
        H('div', { class: 'sh-wm-acts' }, ...say[2].filter(Boolean))))
  }

  function renderWithMeBody(entry, H) {
    const info = entry.info
    const wid = entry.wid
    const rawUrl = `${BASE}withme/${wid}/raw`
    const dlUrl = `${BASE}withme/${wid}/download`
    const frameUrl = `${BASE}withme/${wid}/frame`
    const dlBtn = H('a', { class: 'pillbtn content-mark fs-dlbtn', href: dlUrl, download: info.filename || 'file' }, icon('download', 14), 'Download')

    if (info.kind === 'markdown') {
      return H('div', { class: 'sh-wm-mdwrap' }, renderMarkdown(info.text || ''))
    }
    if (info.kind === 'image' || info.kind === 'svg') {
      return H('div', { class: 'fs-media' },
        H('img', { class: 'fs-img', src: rawUrl, alt: info.filename || '' }),
        dlBtn)
    }
    if (info.kind === 'pdf') {
      return H('div', { class: 'fs-media fs-media-pdf' },
        H('iframe', { class: 'fs-pdf', src: rawUrl, title: info.filename || 'PDF' }),
        dlBtn)
    }
    if (info.kind === 'html') {
      return H('div', { class: 'fs-htmlframe sh-wm-html' },
        H('iframe', {
          class: 'fs-iframe',
          sandbox: 'allow-scripts allow-forms',
          referrerpolicy: 'no-referrer',
          title: info.filename || 'HTML preview',
          src: frameUrl,
        }))
    }
    if (info.kind === 'audio') {
      return H('div', { class: 'fs-media fs-media-av' },
        H('span', { class: 'fs-chip', text: info.label || 'Audio' }),
        H('audio', { class: 'fs-audio', controls: true, preload: 'metadata', src: rawUrl }),
        dlBtn)
    }
    if (info.kind === 'video') {
      return H('div', { class: 'fs-media fs-media-av' },
        H('video', { class: 'fs-video', controls: true, playsinline: true, preload: 'metadata', src: rawUrl }),
        dlBtn)
    }
    if (info.kind === 'text' && typeof info.text === 'string') {
      return H('div', { class: 'sh-wm-codewrap' }, H('pre', { class: 'sh-wm-code' }, H('code', { text: info.text })))
    }
    return H('div', { class: 'fs-media' },
      H('span', { class: 'fs-chip', text: info.label || 'File' }),
      info.size != null ? H('div', { class: 'fs-quiet', text: formatBytes(info.size) }) : null,
      dlBtn)
  }

  const withMeViewerReg = loam.places?.files?.viewer?.add?.({
    id: 'sharing.withme',
    slot: 'viewer',
    order: 0,
    when: (c) => allowed() && c.file?.root === ROOT_ID,
    render(c) {
      const H = loam.ui.h
      const key = `${c.file.root}:${c.file.path}`
      let cur = wmViews.get(c.files)
      if (!cur || cur.key !== key) cur = loadWithMeFile(c.files, c.file)
      if (['gone', 'down', 'missing'].includes(cur.status)) return withMeState(cur, c)
      if (cur.status !== 'ready') {
        return H('div', { key: `sharing:opening:${key}`, class: 'fs-quiet sh-opening', text: 'Opening…' })
      }
      const info = cur.info
      const d = withme.byId(cur.wid)
      const wide = Boolean(c.options?.wide)
      const go = c.options.go || host.goFiles
      const dlUrl = `${BASE}withme/${cur.wid}/download`

      // Ask `files.viewer` slot `panel` so Comments attaches its whole-file comments panel seamlessly.
      const panels = c.files?.registry?.draw?.('files.viewer', {
        slot: 'panel',
        files: c.files,
        file: c.file,
        options: c.options,
      }) || []
      const panelActions = []
      const panelNodes = []
      for (const p of panels) {
        if (!p) continue
        if (p instanceof Node || p.__keep) panelNodes.push(p)
        else {
          if (p.action) panelActions.push(p.action)
          if (p.node) panelNodes.push(p.node)
        }
      }

      const head = H('div', { class: 'fs-head sh-wm-head' },
        H('span', { class: 'sh-wm-from', text: `Shared with me · ${info.owner || d?.owner || d?.host || 'Another Loam'}` }),
        H('span', { class: 'sh-grow' }),
        ...panelActions,
        H('a', { class: 'pillbtn content-mark', href: dlUrl, download: info.filename || 'file' }, icon('download', 14), 'Download'))

      const mainCol = [
        H('div', { class: 'fs-titles' },
          H('div', { class: 'display fs-edtitle', text: info.title || info.filename }),
          H('div', { class: 'fs-sub', text: [info.label, formatBytes(info.size), MODE_WORD[info.mode] || 'View'].filter(Boolean).join(' · ') })),
        renderWithMeBody(cur, H),
      ]

      return H('div', { class: `fs-file sh-wm-file${wide ? ' wide' : ''}${panelNodes.length ? ' has-panel' : ''}`, key: `sharing:view:${key}` },
        head,
        ...(panelNodes.length
          ? [H('div', { class: 'fs-split', key: 'sh:split' },
              H('div', { class: 'fs-main-col', key: 'sh:main' }, ...mainCol),
              ...panelNodes.map((node, i) => H('aside', { class: 'fs-side-panel', key: `sh:panel:${i}` }, node)))]
          : mainCol))
    },
  })

  // -- Toast helper ----------------------------------------------------------

  let toastNode = null
  let toastTimer = null
  function listToast(text, undo) {
    if (!toastNode) {
      toastNode = document.createElement('div')
      toastNode.className = 'sh-toast'
      toastNode.setAttribute('role', 'status')
      document.body.append(toastNode)
    }
    toastNode.replaceChildren(document.createTextNode(text))
    toastNode.classList.toggle('plain', !undo)
    if (undo) {
      const b = document.createElement('button')
      b.type = 'button'
      b.textContent = 'Undo'
      b.onclick = () => { toastNode.classList.remove('on'); undo() }
      toastNode.append(b)
    }
    requestAnimationFrame(() => toastNode?.classList.add('on'))
    clearTimeout(toastTimer)
    toastTimer = setTimeout(() => toastNode?.classList.remove('on'), undo ? 6000 : 3500)
  }

  const renamed = loam.events?.on?.('file.renamed', () => refreshShared())

  return () => {
    disposed = true
    renamed?.remove?.()
    sharedRoot?.remove?.()
    shareMenuReg?.remove?.()
    shareBtnReg?.remove?.()
    withMeViewerReg?.remove?.()
    wmViews.clear()
    tip.hide()
    clearTimeout(toastTimer)
    toastNode?.remove()
  }
}

function faceTip() {
  let node = null
  let timer = null
  let pressed = false
  let hideTimer = null
  function show(face) {
    const text = face?.getAttribute?.('data-tip')
    if (!text || !face.isConnected) return
    if (!node) {
      node = document.createElement('div')
      node.className = 'sh-sd-tip'
      node.setAttribute('role', 'tooltip')
      document.body.append(node)
    }
    node.textContent = text
    node.classList.remove('on')
    const r = face.getBoundingClientRect()
    const scale = (document.body.getBoundingClientRect().width / (document.body.offsetWidth || 1)) || 1
    const vw = document.documentElement.clientWidth / scale
    const half = Math.min(node.offsetWidth, vw - 16) / 2
    node.style.left = `${Math.min(Math.max((r.left + r.width / 2) / scale, half + 8), vw - half - 8)}px`
    node.style.top = `${r.top / scale}px`
    requestAnimationFrame(() => node?.classList.add('on'))
    setTimeout(() => document.addEventListener('pointerdown', hide, { once: true, capture: true }), 0)
  }
  function hide() {
    clearTimeout(timer)
    node?.classList.remove('on')
  }
  return {
    hide() { hide(); clearTimeout(hideTimer); node?.remove(); node = null },
    props: {
      onpointerenter: (e) => { if (e.pointerType === 'mouse') show(e.currentTarget) },
      onpointerleave: (e) => { if (e.pointerType === 'mouse') hide() },
      onpointerdown: (e) => {
        if (e.pointerType === 'mouse') return
        const face = e.currentTarget
        pressed = false
        clearTimeout(timer)
        timer = setTimeout(() => { pressed = true; show(face); clearTimeout(hideTimer); hideTimer = setTimeout(hide, 2200) }, 420)
      },
      onpointerup: () => clearTimeout(timer),
      onpointercancel: () => clearTimeout(timer),
    },
    swallow: (e) => {
      if (e.type === 'contextmenu') { if (pressed || e.pointerType !== 'mouse') e.preventDefault(); return }
      if (!pressed) return
      pressed = false
      e.preventDefault()
      e.stopPropagation()
    },
  }
}
