// Comments & suggestions:
// - `files.viewer` (slot `viewer`, order 10): the calm Markdown editor with
//   anchored comments, suggestions, authorship tinting and history (both on
//   vault files and on collaborative Markdown files in Shared with me).
// - `files.viewer` (slot `panel`): the whole-file comments panel for every
//   non-Markdown file (PDF, images, HTML, audio, video, code, archives), as a
//   side column on desktop and a bottom sheet on phones.
// - `files.share` (slots `sheet`, `footer`, `withme-menu`): notes notice on
//   invite cards and controls for how the owner's agents appear outside Loam.

import { createDocEditor } from './editor/editor.js'
import { createCommentsPanel } from './panel.js'
import { tone, TONE_INK, icon as glyph, h as domH } from './editor/dom.js'
import { docApi } from './api.js'
import { how, appearSelect, openAppearSheet } from './appear.js'

const BASE = '/x/comments/'
const ROOTS = { vault: 'Your files', spaces: 'Agent files', system: 'System' }
const COMPACT_PX = 760

function withmeId(path) {
  return /^with-me\/(w-[a-z0-9]{10})(?:\.[A-Za-z0-9_-]{1,16})?$/.exec(path || '')?.[1] || null
}

function isRemote(file) {
  return Boolean(file && typeof file.root === 'string' && file.root.startsWith('@') && withmeId(file.path))
}

export function activate(loam) {
  const host = loam.ui?.host
  const viewer = loam.places?.files?.viewer
  if (!host || !viewer || typeof host.files?.own !== 'function') return

  const onStaging = String(globalThis.location?.hostname || '').startsWith('next.')
  let reach = loam.settings?.get?.('reach', 'everywhere') || 'everywhere'
  let defaultAppear = null
  const allowed = () => onStaging || reach === 'everywhere'

  fetch(`${BASE}config`, { credentials: 'same-origin' })
    .then(r => (r.ok ? r.json() : null))
    .then((cfg) => {
      if (!cfg || disposed) return
      const was = allowed()
      reach = cfg.reach === 'staging' ? 'staging' : 'everywhere'
      loam.settings?.set?.('reach', reach)
      if (cfg.appear) defaultAppear = cfg.appear
      if (was !== allowed() || cfg.appear) host.schedule?.()
    })
    .catch(() => {})

  loam.ui.styles?.add?.('assets/comments.css')
  const kept = loam.ui.kept || host.kept
  const editors = new Map()
  const panels = new Map()
  const withmeAppear = new Map()
  let disposed = false

  const isMarkdown = (file) => Boolean(
    file && !file.error && !file.loading
    && (file.view === 'markdown' || /\.(md|markdown)$/i.test(file.path || ''))
    && /\.(md|markdown)$/i.test(file.path || ''),
  )

  const isPanelFile = (file) => {
    if (!file || file.error || file.loading || !file.path) return false
    if (/\.(md|markdown)$/i.test(file.path)) return false
    if (file.root === 'vault') return true
    if (isRemote(file)) return file.row?.withme?.collab !== false
    return false
  }

  function face(author, size) {
    const font = Math.round(size * 0.5)
    try {
      if (author?.kind === 'agent' && host.agentLook && loam.ui.blob) {
        return loam.ui.blob(host.agentLook(author.id, host.state?.agents), { size, font })
      }
      if (loam.ui.blob) {
        const own = author?.kind === 'owner'
        return loam.ui.blob(
          {
            initial: (author?.name || '?').charAt(0).toUpperCase(),
            color: own ? 'var(--latte-espresso)' : tone(author?.name || '?'),
            ink: own ? 'var(--latte-espresso-ink)' : TONE_INK,
            shape: '50%',
          },
          { size, font },
        )
      }
    } catch { /* fall through */ }
    return null
  }

  function crumbs(file, go) {
    const parts = file.path.split('/')
    const name = parts.pop().replace(/\.(md|markdown)$/i, '')
    const out = [{ label: ROOTS[file.root] || file.root, go: go ? () => go(file.root, '') : null }]
    parts.forEach((p, i) => out.push({ label: p, go: go ? () => go(file.root, parts.slice(0, i + 1).join('/')) : null }))
    out.push({ label: name })
    return out.length > 4 ? [out[0], { label: '…' }, ...out.slice(-2)] : out
  }

  function stopDictation(entry) {
    const owner = (host.panelHosts?.() || []).find(o => o?.files && o.files === entry.files) || host
    loam.events?.emit?.('field.closed', { target: 'file', host: owner })
  }

  function fieldFor(ed) {
    return {
      get value() { return ed.text() },
      set value(v) { if (ed.canType() && v !== ed.text()) ed.replaceText(v) },
      get selectionStart() { return ed.view.state.selection.main.from },
      get selectionEnd() { return ed.view.state.selection.main.to },
      setSelectionRange(a, b = a) {
        const n = ed.view.state.doc.length
        ed.view.dispatch({ selection: { anchor: Math.min(a, n), head: Math.min(b, n) } })
      },
      focus() { ed.view.focus() },
      get isConnected() { return ed.dom.isConnected },
      setAttribute() {},
      removeAttribute() {},
    }
  }

  function watchWidth(entry) {
    if (typeof ResizeObserver === 'undefined') return
    entry.ro = new ResizeObserver(() => {
      const w = entry.editor?.dom.clientWidth || 0
      if (!w) return
      entry.editor.setPhone(!entry.options.wide || w < COMPACT_PX)
    })
    entry.ro.observe(entry.editor.dom)
  }

  function closeEditor(entry) {
    if (!entry) return
    entry.ro?.disconnect()
    entry.own?.remove?.()
    const ed = entry.editor
    if (ed) Promise.resolve(ed.flush()).finally(() => ed.destroy())
  }

  function closePanel(entry) {
    if (!entry) return
    entry.panel?.destroy?.()
  }

  function drawHeaderActions(entry) {
    const reg = entry.files?.registry
    if (!reg || entry.file.root !== 'vault') return []
    const resource = entry.file.row || { root: entry.file.root, path: entry.file.path, kind: 'file', capabilities: entry.file.caps || {} }
    const nodes = reg.draw('files.action', {
      slot: 'action',
      files: entry.files,
      file: entry.file,
      resource,
      options: entry.options,
      title: () => (/^#\s+(.+?)\s*#*\s*$/m.exec(entry.editor?.text?.() || '')?.[1]) || entry.file.path.split('/').pop().replace(/\.(md|markdown)$/i, ''),
      toast: (text, undo) => entry.editor?.toast(text, undo),
    })
    return nodes.map(n => n?.__keep || n).filter(Boolean)
  }

  // -- 1. Markdown calm editor (`files.viewer`, slot `viewer`, order 10) ------

  function openEditor(ctx) {
    const { files, file, options } = ctx
    const remote = isRemote(file)
    const wid = remote ? withmeId(file.path) : null
    const key = `${file.root}:${file.path}`
    const entry = { key, status: 'loading', editor: null, own: null, options, files, file, wid }
    editors.set(files, entry)
    const cur = () => editors.get(files)

    const api = remote
      ? docApi({ base: `${BASE}remote/${wid}/` })
      : docApi({ base: BASE, root: file.root, path: file.path })

    api.get().then((doc) => {
      if (disposed || cur() !== entry) return
      if (!remote && !file.caps?.write) {
        doc = { ...doc, can: { comment: false, suggest: false, edit: false, accept: false, resolve_any: false } }
      }
      const wm = remote ? file.row?.withme : null
      const titleOf = () => (/^#\s+(.+?)\s*#*\s*$/m.exec(ed?.text?.() || doc.text)?.[1])
        || wm?.title || file.path.split('/').pop().replace(/\.(md|markdown)$/i, '')
      const go = () => entry.options.go || host.goFiles
      let ed = null
      ed = createDocEditor({
        api,
        doc,
        face,
        projection: remote ? null : file.projection,
        history: !remote,
        headEnd: () => (remote ? [] : drawHeaderActions(entry)),
        phone: !options.wide,
        settings: loam.settings,
        embed: remote ? null : (options.embed || host.embedFor || null),
        openLink: (ref, event) => {
          if (remote && !/^(https?:|mailto:)/i.test(ref)) return
          host.openFileRef?.(ref, remote ? null : { root: file.root, path: file.path }, event)
        },
        crumbs: remote
          ? [
              { label: 'Shared with me', go: () => go()?.(file.root, '') },
              { label: (wm?.owner ? `${wm.owner} · ` : '') + (wm?.host || '') },
              { label: titleOf() },
            ]
          : crumbs(file, entry.options.go || host.goFiles),
        back: entry.options.backToThread ? { label: 'Back to thread', go: entry.options.backToThread }
          : entry.options.close ? { label: entry.options.closeLabel || 'Back', go: entry.options.close } : null,
        fileMenu: !remote && files.resourceMenu
          ? () => files.resourceMenu({ ...entry.file.row }, entry.options.home, { openFolder: entry.options.returnBack ? entry.options.close : null, includeDelete: false })
          : null,
        fileDelete: !remote ? () => (files.canDeleteFile?.(entry.file.row) ? () => files.beginDelete(entry.file.row) : null) : null,
        menuExtras: remote && wm?.url
          ? () => [{ label: 'Copy link', icon: 'link', run: () => copyText(wm.url, 'Link copied. Its password stays with your Loam.') }]
          : null,
        headExtras: () => [entry.options.readAloud?.glyph || null].filter(Boolean),
        sidePanel: () => entry.options.sideChrome || null,
        mic: () => entry.options.mic || null,
        micInHead: () => Boolean(entry.options.wide),
        onCanType: (yes) => { if (!yes) stopDictation(entry) },
        onText: () => { if (!remote) files.registry?.emit?.('file.edited', { root: file.root, path: file.path }) },
        onGone: remote ? () => {
          entry.status = 'down'
          if (wm) wm.state = 'gone'
          host.schedule?.()
        } : null,
      })
      entry.editor = ed
      entry.status = 'ready'
      if (files.own) {
        entry.own = files.own(file, {
          dirty: () => ed.dirty(),
          save: () => ed.flush(),
          edit: (text) => { if (text !== ed.text()) ed.replaceText(text) },
          field: fieldFor(ed),
        })
      }
      watchWidth(entry)
      host.schedule?.()
    }).catch((err) => {
      if (disposed || cur() !== entry) return
      entry.status = 'down'
      entry.error = err
      if (remote && file.row?.withme) {
        if (err?.status === 410) file.row.withme.state = 'gone'
        if (err?.status === 404 || err?.status === 501) file.row.withme.collab = false
      }
      console.warn('comments: falling back to default viewer:', err?.message || err)
      host.schedule?.()
    })
    return entry
  }

  const editorReg = viewer.add({
    id: 'comments.editor',
    slot: 'viewer',
    order: 10,
    when: (c) => allowed() && isMarkdown(c.file) && (!isRemote(c.file) || c.file.row?.withme?.collab !== false),
    render(c) {
      const key = `${c.file.root}:${c.file.path}`
      let cur = editors.get(c.files)
      if (!cur || cur.key !== key) {
        closeEditor(cur)
        cur = openEditor(c)
      }
      cur.options = c.options
      cur.file = c.file
      if (cur.status === 'down') return null // fall through cleanly to Sharing or builtin viewer
      const h = loam.ui.h
      if (cur.status !== 'ready') return h('div', { key: `comments:opening:${key}`, class: 'fs-quiet dc-opening', text: 'Opening…' })
      const ed = cur.editor
      ed.setSlots({ take: c.options.take || null, player: c.options.readAloud?.player || null })
      ed.setPhone(!c.options.wide || (ed.dom.clientWidth > 0 && ed.dom.clientWidth < COMPACT_PX))
      ed.refreshHead()
      return h('div', { key: `comments:${key}`, class: 'dc-host' }, kept ? kept(ed.dom) : ed.dom)
    },
  })

  // -- 2. Whole-file comments panel (`files.viewer`, slot `panel`) ------------

  function ensurePanel(c) {
    const { files, file } = c
    const key = `${file.root}:${file.path}`
    let cur = panels.get(files)
    if (cur && cur.key === key) return cur
    closePanel(cur)
    const remote = isRemote(file)
    const wid = remote ? withmeId(file.path) : null
    const api = remote
      ? docApi({ base: `${BASE}remote/${wid}/` })
      : docApi({ base: BASE, root: file.root, path: file.path })
    const entry = { key, count: 0, panel: null, files, file }
    entry.panel = createCommentsPanel({
      api,
      face,
      readOnly: !remote && file.root !== 'vault',
      onCount: (open) => {
        if (entry.count !== open) {
          entry.count = open
          host.schedule?.()
        }
      },
      onGone: remote ? () => {
        if (file.row?.withme) file.row.withme.collab = false
        host.schedule?.()
      } : null,
    })
    panels.set(files, entry)
    return entry
  }

  const panelReg = viewer.add({
    id: 'comments.panel',
    slot: 'panel',
    when: (c) => allowed() && isPanelFile(c.file),
    render(c) {
      const entry = ensurePanel(c)
      const wide = Boolean(c.options?.wide)
      const H = loam.ui.h
      if (wide) {
        return {
          action: null,
          node: kept ? kept(entry.panel.dom) : entry.panel.dom,
        }
      }
      return {
        action: H('button', {
          type: 'button',
          class: `pillbtn content-mark dc-pbtn${entry.count ? ' has' : ''}`,
          'aria-label': entry.count ? `Comments (${entry.count} open)` : 'Comments',
          onclick: () => entry.panel.openSheet(),
        }, glyph('comment', 14), H('span', { text: entry.count ? `Comments · ${entry.count}` : 'Comments' })),
        node: null,
      }
    },
  })

  // -- 3. Sharing UI extensions (`files.share`) -------------------------------

  const sharePlace = loam.places?.files?.share

  // Sheet slot: invite notes notice (view === 'card') and per-file appear selector (view === 'list').
  const shareSheetReg = sharePlace?.add?.({
    id: 'comments.share-sheet',
    slot: 'sheet',
    when: () => allowed(),
    render({ root, path, view, recipientName, toast }) {
      if (!root || !path) return null
      const wrap = domH('div', { class: 'dc-ap-slot' })
      fetch(`${BASE}appear?${new URLSearchParams({ root, path })}`, { credentials: 'same-origin' })
        .then(r => (r.ok ? r.json() : null))
        .then((got) => {
          if (!got || !wrap.isConnected) return
          if (view === 'card') {
            const n = got.open || 0
            const r = got.resolved || 0
            if (!n && !r) return
            const who = recipientName || 'They'
            const line = n
              ? `${who} will also see the ${n === 1 ? 'open note' : `${n} open notes`} already on this file${r ? `, and ${r} resolved` : ''}.`
              : `${who} will also see the ${r === 1 ? 'resolved note' : `${r} resolved notes`} on this file.`
            wrap.replaceChildren(domH('p', { class: 'dc-sh-cap dc-sh-notes', text: line }))
          } else if (view === 'list' && got.appear) {
            const a = got.appear
            wrap.replaceChildren(domH('div', { class: 'dc-tk-mem dc-ap-line' },
              domH('span', { text: 'Your agents appear here' }),
              appearSelect({
                value: a.choice, def: a.default, alias: a.alias, withDefault: true,
                label: 'How your agents appear on this file',
                onChange: async (v) => {
                  try {
                    const res = await fetch(`${BASE}appear`, {
                      method: 'PATCH', credentials: 'same-origin',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ root, path, appear: v }),
                    })
                    const out = await res.json().catch(() => null)
                    if (!res.ok) throw new Error(out?.error || 'Could not save choice.')
                    toast?.(out.appear.as === 'one'
                      ? `Your agents appear as ${out.appear.alias} here, on notes already here too`
                      : 'Your agents appear here by their own names')
                  } catch (err) { toast?.(err.message) }
                },
              })))
          }
        })
        .catch(() => {})
      return wrap
    },
  })

  // Footer slot: global default appear setting at the foot of Files › Shared.
  const shareFooterReg = sharePlace?.add?.({
    id: 'comments.share-footer',
    slot: 'footer',
    when: () => allowed(),
    render({ H = loam.ui.h, toast }) {
      if (!defaultAppear) return null
      const a = defaultAppear
      const choose = async (v) => {
        if (v === a.appear) return
        try {
          const res = await fetch('/api/plugins/comments/settings', {
            method: 'PATCH', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ values: { appear: v } }),
          })
          if (!res.ok) throw new Error((await res.json().catch(() => null))?.error || 'That wasn’t saved. Try again in a moment.')
          defaultAppear = { ...a, appear: v }
          toast?.(`Your agents appear to others ${how(v, a.alias)}, unless a file says otherwise`)
        } catch (err) { toast?.(err.message) }
        host.schedule?.()
      }
      return H('label', { class: 'dc-tk-mem dc-ap-foot', key: 'comments:appear-foot' },
        H('span', { text: 'Your agents appear to others' }),
        H('span', { class: 'dc-ap-pick' },
          H('span', { class: 'dc-ap-shown', text: how(a.appear, a.alias) }),
          glyph('chevronDown', 12),
          H('select', {
            class: 'dc-ap-sel',
            'aria-label': 'How your agents appear to others',
            onchange: (e) => choose(e.target.value),
          }, ['names', 'one'].map(v => H('option', { value: v, selected: v === a.appear, text: how(v, a.alias) })))))
    },
  })

  // Shared-with-me row menu item: "How your agents appear"
  const shareWithMeMenuReg = sharePlace?.add?.({
    id: 'comments.share-withme-menu',
    slot: 'withme-menu',
    when: (c) => allowed() && Boolean(defaultAppear) && c?.doc?.collab !== false,
    render({ doc, toast, agentName }) {
      const def = defaultAppear?.appear || 'names'
      const alias = `${doc.name || defaultAppear?.owner || 'Your'}'s agent`
      const curChoice = withmeAppear.get(doc.id) || 'default'
      const eff = curChoice === 'names' || curChoice === 'one' ? curChoice : def
      const words = how(eff, alias)
      const sub = words.charAt(0).toUpperCase() + words.slice(1) + (curChoice === 'default' ? ', as usual' : '')
      if (!withmeAppear.has(doc.id)) {
        fetch(`${BASE}appear?${new URLSearchParams({ withme: doc.id })}`, { credentials: 'same-origin' })
          .then(r => (r.ok ? r.json() : null))
          .then((got) => {
            if (got?.appear?.choice && withmeAppear.get(doc.id) !== got.appear.choice) {
              withmeAppear.set(doc.id, got.appear.choice)
              host.schedule?.()
            }
          })
          .catch(() => {})
      }
      return {
        id: 'appear',
        label: 'How your agents appear',
        icon: (s) => glyph('people', s),
        sub,
        run: () => {
          openAppearSheet({
            title: doc.title,
            there: doc.owner ? `${doc.owner}’s Loam` : doc.host,
            value: withmeAppear.get(doc.id) || 'default',
            def,
            alias,
            agent: agentName?.() || host.state?.agents?.list?.find(a => !a.archived)?.name || '',
            onChoose: async (v) => {
              try {
                const res = await fetch(`${BASE}appear`, {
                  method: 'PATCH', credentials: 'same-origin',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ withme: doc.id, appear: v }),
                })
                const got = await res.json().catch(() => null)
                if (!res.ok) throw new Error(got?.error || 'Could not save choice.')
                withmeAppear.set(doc.id, got.appear.choice)
                host.schedule?.()
                toast?.(`Your agents appear ${how(got.appear.as, alias)} on “${doc.title}” from now on`)
                return true
              } catch (err) { toast?.(err.message); return false }
            },
          })
        },
      }
    },
  })

  async function copyText(text, done) {
    if (!text) return
    try { await navigator.clipboard.writeText(text); toastLine(done) } catch { toastLine(text) }
  }

  let toastNode = null
  let toastTimer = null
  function toastLine(text) {
    if (!toastNode) {
      toastNode = document.createElement('div')
      toastNode.className = 'dc-toast dc-list-toast plain'
      toastNode.setAttribute('role', 'status')
      document.body.append(toastNode)
    }
    toastNode.textContent = text
    requestAnimationFrame(() => toastNode?.classList.add('on'))
    clearTimeout(toastTimer)
    toastTimer = setTimeout(() => toastNode?.classList.remove('on'), 3200)
  }

  const onFocus = () => {
    for (const cur of editors.values()) if (cur.status === 'ready' && !cur.editor.dirty()) cur.editor.reload()
    for (const cur of panels.values()) cur.panel.reload()
  }
  globalThis.addEventListener?.('focus', onFocus)

  return () => {
    disposed = true
    globalThis.removeEventListener?.('focus', onFocus)
    editorReg?.remove?.()
    panelReg?.remove?.()
    shareSheetReg?.remove?.()
    shareFooterReg?.remove?.()
    shareWithMeMenuReg?.remove?.()
    for (const cur of editors.values()) closeEditor(cur)
    editors.clear()
    for (const cur of panels.values()) closePanel(cur)
    panels.clear()
    clearTimeout(toastTimer)
    toastNode?.remove()
  }
}
