// Comments & suggestions: Loam's adapter for the calm editor.
//
// It registers one viewer in Files (`files.viewer`, for Markdown files) and
// does everything through the public plugin interface: the editor component
// (editor/editor.js) is given the plugin server's operations, Loam's faces,
// links, embeds, dictation's mic and read aloud's controls. Turned off, the
// registration goes and Files draws Markdown in its own editor again.

import { createDocEditor } from './editor/editor.js'
import { tone } from './editor/dom.js'
import { docApi } from './api.js'
import { createSharing } from './sharing.js'

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
  // Staging first: the plugin's `reach` setting (settings.entry, `staging` or
  // `everywhere`; `bin/settings set plugin.doc-comments.reach everywhere`, or
  // Settings > Plugins) says whether the calm editor and the share door work
  // beyond Loam's staging copy (`next.<domain>`). It replaces the hostname
  // constant of 2026-11-16. Until the server answers, the last answer this
  // browser had decides, so a page does not flicker between editors.
  const onStaging = String(globalThis.location?.hostname || '').startsWith('next.')
  let reach = loam.settings?.get?.('reach', 'staging') || 'staging'
  const allowed = () => onStaging || reach === 'everywhere'
  fetch(`${BASE}config`, { credentials: 'same-origin' }).then(r => (r.ok ? r.json() : null)).then((cfg) => {
    if (!cfg || disposed) return
    const was = allowed()
    reach = cfg.reach === 'everywhere' ? 'everywhere' : 'staging'
    loam.settings?.set?.('reach', reach)
    if (was !== allowed()) host.schedule?.()
  }).catch(() => {})
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
        // The owner is drawn as Loam draws them everywhere; anyone else in
        // their own tone from their name, the one the share page gives them,
        // so two visitors are told apart at a glance.
        const own = author?.kind === 'owner'
        return loam.ui.blob({ initial: (author?.name || '?').charAt(0).toUpperCase(), color: own ? 'var(--latte-espresso)' : tone(author?.name || '?'), ink: own ? 'var(--latte-espresso-ink)' : '#fff', shape: '50%' }, { size, font })
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
      const titleOf = () => (/^#\s+(.+?)\s*#*\s*$/m.exec(ed?.text?.() || doc.text)?.[1]) || file.path.split('/').pop().replace(/\.(md|markdown)$/i, '')
      let ed = null
      const sharing = file.root === 'vault' && writable ? createSharing({
        base: BASE, root: file.root, path: file.path, title: titleOf,
        toast: (text, undo) => ed?.toast(text, undo), onChange: () => ed?.refreshHead(),
        // What a visitor will see: the open notes, and the resolved ones apart.
        notes: () => (ed?.notes?.() || doc.notes || []).filter(n => n.state === 'open').length,
        resolved: () => ed?.state?.resolved ?? doc.resolved ?? 0,
      }) : null
      if (sharing) sharing.links = doc.links
      ed = createDocEditor({
        api, doc, face,
        headEnd: () => (sharing ? [sharing.button()] : []),
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
        headExtras: () => [entry.options.readAloud?.glyph || null].filter(Boolean),
        // Dictation's mic: the editor draws it only while the doc takes typed
        // text from this person (never in Viewing, never on a read-only file),
        // in the header on a wide page and in the phone's keyboard bar.
        mic: () => entry.options.mic || null,
        micInHead: () => Boolean(entry.options.wide),
        // Into Viewing (or the right to write gone) with a take running into
        // the doc: the take is stopped and nothing of it lands.
        onCanType: (yes) => { if (!yes) stopDictation(entry) },
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

  // Dictation's take into this doc, if one runs, is ended the way closing the
  // field ends it (dictation's own `field.closed`), in the page or panel that
  // holds the doc.
  function stopDictation(entry) {
    const owner = (host.panelHosts?.() || []).find(o => o?.files && o.files === entry.files) || host
    loam.events?.emit?.('field.closed', { target: 'file', host: owner })
  }

  function fieldFor(ed) {
    return {
      get value() { return ed.text() },
      // Nothing is written through the field while the doc does not take
      // typed text (Viewing): a late take has nowhere to land.
      set value(v) { if (ed.canType() && v !== ed.text()) ed.replaceText(v) },
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
    when: (c) => allowed() && isMarkdown(c.file),
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

  // Shared Docs: a root of its own in Files (core's `files.root`), listing
  // only docs with a link on. A dot marks one with something new since the
  // owner last opened it; nothing else anywhere gets a dot.
  const shared = { docs: [], loaded: false }
  async function loadShared() {
    const res = await fetch(`${BASE}shared`, { credentials: 'same-origin' })
    if (!res.ok) throw new Error('Shared Docs could not be read just now.')
    shared.docs = (await res.json()).docs || []
    shared.loaded = true
  }
  const sharedRoot = loam.places?.files?.root?.add?.({
    id: 'doc-comments',
    name: 'Shared Docs',
    desc: 'Docs open to people outside',
    icon: (size) => peopleIcon(size),
    when: () => allowed(),
    load: () => loadShared(),
    render({ go }) {
      const H = loam.ui.h
      if (!shared.loaded) {
        // Opened before this plugin had registered (a cold load of the
        // address): load now, and draw again when it answers.
        if (!shared.loading) {
          shared.loading = true
          loadShared().catch(() => {}).finally(() => { shared.loading = false; host.schedule?.() })
        }
        return H('div', { class: 'fs-quiet', text: 'Loading…' })
      }
      if (!shared.docs.length) return H('div', { class: 'fs-quiet dc-sd-empty', text: 'No doc is shared with anyone outside Loam yet. Share one with the Share button at the top of the doc.' })
      return H('div', { class: 'card card-group dc-sd' }, shared.docs.map(d => {
        const name = (d.path || '').split('/').pop().replace(/\.(md|markdown)$/i, '')
        const on = (d.links || []).filter(l => l.on)
        const modes = [...new Set(on.map(l => l.mode))]
        const who = d.people.length ? d.people.slice(0, 3).join(', ') : ''
        const what = modes.map(m => ({ view: 'View', comment: 'Comment', edit: 'Edit' }[m])).join(', ')
        const sub = [d.new ? `${d.new} new` : (who ? '' : 'Nothing new'), who, what].filter(Boolean).join(' · ')
        return H('button', { class: `dc-sd-row${d.new ? ' fresh' : ''}`, key: `sd:${d.id}`, onclick: () => { d.new = 0; go(d.root, d.path, { isFile: true }) } },
          H('span', { class: 'dc-sd-dot', 'aria-label': d.new ? 'Something new' : null }),
          H('span', { class: 'dc-sd-tile' }, docIcon()),
          H('span', { class: 'dc-sd-words' }, H('b', { text: name }), H('small', { text: sub })),
          H('span', { class: 'dc-sd-time', text: when(d.last) }))
      }))
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
    sharedRoot?.remove?.()
    close(cur)
    cur = null
  }
}


function peopleIcon(size) {
  const NS = 'http://www.w3.org/2000/svg'
  const s = document.createElementNS(NS, 'svg')
  for (const [k, v] of Object.entries({ viewBox: '0 0 24 24', width: size, height: size, fill: 'none', stroke: 'currentColor', 'stroke-width': 1.6, 'stroke-linecap': 'round' })) s.setAttribute(k, v)
  for (const d of ['M3 19c.7-3.2 3-5 6-5s5.3 1.8 6 5', 'M15.5 5.5a3 3 0 0 1 0 6', 'M18 14.3c1.6.7 2.6 2.3 3 4.7']) {
    const p = document.createElementNS(NS, 'path'); p.setAttribute('d', d); s.append(p)
  }
  const c = document.createElementNS(NS, 'circle'); c.setAttribute('cx', 9); c.setAttribute('cy', 8.5); c.setAttribute('r', 3.2); s.append(c)
  return s
}

function docIcon() {
  const NS = 'http://www.w3.org/2000/svg'
  const s = document.createElementNS(NS, 'svg')
  for (const [k, v] of Object.entries({ viewBox: '0 0 24 24', width: 18, height: 18, fill: 'none', stroke: 'currentColor', 'stroke-width': 1.4, 'stroke-linecap': 'round' })) s.setAttribute(k, v)
  for (const d of ['M7 3.5h6.5l4 4V20a.5.5 0 0 1-.5.5H7a.5.5 0 0 1-.5-.5V4a.5.5 0 0 1 .5-.5z', 'M9 11h6', 'M9 14h6', 'M9 17h4']) {
    const p = document.createElementNS(NS, 'path'); p.setAttribute('d', d); s.append(p)
  }
  return s
}

function when(iso) {
  const t = Date.parse(iso)
  if (!Number.isFinite(t)) return ''
  const s = (Date.now() - t) / 1000
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min`
  if (s < 86400) return `${Math.round(s / 3600)} h`
  if (s < 2 * 86400) return 'Yesterday'
  return new Date(t).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}
