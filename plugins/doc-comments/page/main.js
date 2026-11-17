// Comments & suggestions: Loam's adapter for the calm editor.
//
// It registers one viewer in Files (`files.viewer`, for Markdown files) and
// does everything through the public plugin interface: the editor component
// (editor/editor.js) is given the plugin server's operations, Loam's faces,
// links, embeds, dictation's mic and read aloud's controls. Turned off, the
// registration goes and Files draws Markdown in its own editor again.

import { createDocEditor } from './editor/editor.js'
import { docApi } from './api.js'

const BASE = '/x/doc-comments/'
const ROOTS = { vault: 'Vault', spaces: 'AI Spaces', system: 'System' }
const COMPACT_PX = 760

export function activate(loam) {
  const host = loam.ui?.host
  const viewer = loam.places?.files?.viewer
  // Loam's page only (a share page mounts the editor itself), and only a page
  // new enough to let an editor hold the open file (`files.own`, 2026-11-16):
  // on an older copy of the app this does nothing, so turning the plugin on
  // changes a page only once that page is deployed from a release that has
  // it (staging first, then live).
  if (!host || !viewer || typeof host.files?.own !== 'function') return
  loam.ui.styles?.add?.('assets/doc-comments.css')
  const kept = loam.ui.kept || host.kept
  let cur = null
  let disposed = false

  const isMarkdown = (file) => Boolean(file && file.view === 'markdown' && !file.error && !file.loading && /\.(md|markdown)$/i.test(file.path))

  function face(author, size) {
    const font = Math.round(size * 0.5)
    try {
      if (author?.kind === 'agent' && host.agentLook && loam.ui.blob) {
        return loam.ui.blob(host.agentLook(author.id, host.state?.agents), { size, font })
      }
      if (loam.ui.blob) {
        return loam.ui.blob({ initial: (author?.name || '?').charAt(0).toUpperCase(), color: 'var(--latte-espresso)', ink: 'var(--latte-espresso-ink)', shape: '50%' }, { size, font })
      }
    } catch { /* fall through to the editor's own initial */ }
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

  function open(ctx) {
    const { files, file, options } = ctx
    const key = `${file.root}:${file.path}`
    const entry = { key, status: 'loading', editor: null, own: null, options, files, file }
    cur = entry
    const api = docApi({ base: BASE, root: file.root, path: file.path })
    api.get().then((doc) => {
      if (disposed || cur !== entry) return
      const writable = Boolean(file.caps?.write)
      if (!writable) doc = { ...doc, can: { comment: false, suggest: false, edit: false, accept: false, resolve_any: false } }
      const ed = createDocEditor({
        api, doc, face,
        phone: !options.wide,
        settings: loam.settings,
        embed: options.embed || host.embedFor || null,
        openLink: (ref, event) => {
          if (/^(https?:|mailto:)/i.test(ref)) { window.open(ref, '_blank', 'noopener,noreferrer'); return }
          host.openFileRef?.(ref, { root: file.root, path: file.path }, event)
        },
        crumbs: crumbs(file, entry.options.go || host.goFiles),
        back: entry.options.close ? { label: entry.options.closeLabel || 'Back', go: entry.options.close } : null,
        fileMenu: files.resourceMenu ? () => files.resourceMenu({ ...file.row, root: file.root, path: file.path, kind: 'file' }, entry.options.home, { openFolder: entry.options.returnBack ? entry.options.close : null }) : null,
        headExtras: () => [entry.options.readAloud?.glyph || null, entry.options.wide ? entry.options.mic || null : null].filter(Boolean),
        barExtras: () => entry.options.mic || null,
        onText: () => files.registry?.emit?.('file.edited', { root: file.root, path: file.path }),
      })
      entry.editor = ed
      entry.status = 'ready'
      // Files' own edits, saves and field (dictation) go to this editor while
      // it holds the file (core's `files.own`, 2026-11-16).
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
      if (disposed || cur !== entry) return
      entry.status = 'down'
      entry.error = err
      console.warn('doc-comments: showing Files’ own editor:', err?.message || err)
      host.schedule?.()
    })
    return entry
  }

  function fieldFor(ed) {
    return {
      get value() { return ed.text() },
      set value(v) { if (v !== ed.text()) ed.replaceText(v) },
      get selectionStart() { return ed.view.state.selection.main.from },
      get selectionEnd() { return ed.view.state.selection.main.to },
      setSelectionRange(a, b = a) { const n = ed.view.state.doc.length; ed.view.dispatch({ selection: { anchor: Math.min(a, n), head: Math.min(b, n) } }) },
      focus() { ed.view.focus() },
      get isConnected() { return ed.dom.isConnected },
      setAttribute() {}, removeAttribute() {},
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

  function close(entry) {
    if (!entry) return
    entry.ro?.disconnect()
    entry.own?.remove?.()
    const ed = entry.editor
    if (ed) Promise.resolve(ed.flush()).finally(() => ed.destroy())
  }

  const reg = viewer.add({
    id: 'doc-comments.editor',
    when: (c) => isMarkdown(c.file),
    render(c) {
      const key = `${c.file.root}:${c.file.path}`
      if (!cur || cur.key !== key) {
        close(cur)
        open(c)
      }
      cur.options = c.options
      cur.file = c.file
      if (cur.status === 'down') return c.defaultView()
      // Keyed, so Loam's reconciler matches what is drawn here by identity
      // and never by position: matched by position, the "Opening…" line was
      // once patched into the editor's place and took the editor's children
      // with it. The editor itself is the same node every time (kept).
      const h = loam.ui.h
      if (cur.status !== 'ready') return h('div', { key: `doc-comments:opening:${key}`, class: 'fs-quiet dc-opening', text: 'Opening…' })
      const ed = cur.editor
      ed.setSlots({ take: c.options.take || null, player: c.options.readAloud?.player || null })
      ed.setPhone(!c.options.wide || (ed.dom.clientWidth > 0 && ed.dom.clientWidth < COMPACT_PX))
      ed.refreshHead()
      return h('div', { key: `doc-comments:${key}`, class: 'dc-host' }, kept ? kept(ed.dom) : ed.dom)
    },
  })

  const onFocus = () => {
    if (cur?.status === 'ready' && !cur.editor.dirty()) cur.editor.reload()
  }
  globalThis.addEventListener?.('focus', onFocus)
  const renamed = loam.events?.on?.('file.renamed', () => { /* the next render opens it under its new name */ })

  return () => {
    disposed = true
    globalThis.removeEventListener?.('focus', onFocus)
    renamed?.remove?.()
    reg?.remove?.()
    close(cur)
    cur = null
  }
}
