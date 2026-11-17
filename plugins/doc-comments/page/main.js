// Comments & suggestions: Loam's adapter for the calm editor.
//
// It registers one viewer in Files (`files.viewer`, for Markdown files) and
// does everything through the public plugin interface: the editor component
// (editor/editor.js) is given the plugin server's operations, Loam's faces,
// links, embeds, dictation's mic and read aloud's controls. Turned off, the
// registration goes and Files draws Markdown in its own editor again.

import { createDocEditor } from './editor/editor.js'
import { tone, icon as glyph } from './editor/dom.js'
import { docApi } from './api.js'
import { createSharing } from './sharing.js'
import { createWithMe, withmeId, WITHME_PREFIX } from './withme.js'

const BASE = '/x/doc-comments/'
const ROOTS = { vault: 'Vault', spaces: 'AI Spaces', system: 'System' }
const ROOT_ID = '@doc-comments'
const COMPACT_PX = 760
const LEAVE_MS = 340
const MODE_ORDER = ['edit', 'comment', 'view']
const MODE_WORD = { view: 'View', comment: 'Comment', edit: 'Edit' }

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
  // One editor per Files (the main panel's and the side panel's are two),
  // so a doc open beside another keeps its own editor (2026-11-17: with one
  // slot, each panel's draw closed the other's editor).
  const editors = new Map()
  let disposed = false
  const tip = faceTip()

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
    if (ctx.file.root === ROOT_ID) return openWithMe(ctx)
    const { files, file, options } = ctx
    const key = `${file.root}:${file.path}`
    const entry = { key, status: 'loading', editor: null, own: null, options, files, file }
    editors.set(files, entry)
    const cur = () => editors.get(files)
    const api = docApi({ base: BASE, root: file.root, path: file.path })
    api.get().then((doc) => {
      if (disposed || cur() !== entry) return
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
      if (disposed || cur() !== entry) return
      entry.status = 'down'
      entry.error = err
      console.warn('doc-comments: showing Files’ own editor:', err?.message || err)
      host.schedule?.()
    })
    return entry
  }

  // A doc shared with the owner from another Loam (Shared with me): the
  // same editor, its operations sent through this Loam's server to that
  // Loam's door (`withme/<id>/doc…`). What it may do is the link's mode, as
  // the door answers it (`can`); no Share and no History (both are its
  // owner's); the header says where it lives in place of the vault's
  // breadcrumb. Nothing is written into this vault.
  function openWithMe(ctx) {
    const { files, file, options } = ctx
    const key = `${file.root}:${file.path}`
    const wid = withmeId(file.path)
    const entry = { key, status: 'loading', editor: null, own: null, options, files, file, withme: wid }
    editors.set(files, entry)
    const cur = () => editors.get(files)
    if (!wid) { entry.status = 'missing'; return entry }
    const api = docApi({ base: `${BASE}withme/${wid}/`, root: '', path: '' })
    const go = () => entry.options.go || host.goFiles
    const known = () => withme.byId(wid) || file.row?.withme || null
    const begin = () => api.get().then((doc) => {
      if (disposed || cur() !== entry) return
      const d = known()
      if (d) { d.new = false; d.state = 'ok'; d.mode = doc.me?.mode || d.mode }
      const titleOf = () => (/^#\s+(.+?)\s*#*\s*$/m.exec(ed?.text?.() || doc.text)?.[1]) || d?.title || 'Shared doc'
      let ed = null
      ed = createDocEditor({
        api, doc, face,
        history: false,
        phone: !options.wide,
        settings: loam.settings,
        openLink: (ref) => { if (/^(https?:|mailto:)/i.test(ref)) window.open(ref, '_blank', 'noopener,noreferrer') },
        crumbs: [
          { label: 'Shared with me', go: () => go()?.(ROOT_ID, '') },
          { label: (known()?.owner ? `${known().owner} · ` : '') + (known()?.host || '') },
          { label: titleOf() },
        ],
        back: entry.options.close ? { label: entry.options.closeLabel || 'Back', go: entry.options.close } : null,
        menuExtras: () => [{ label: 'Copy link', icon: 'link', run: () => copyText(known()?.url, 'Link copied. Its password stays with your Loam.') }],
        headExtras: () => [entry.options.readAloud?.glyph || null].filter(Boolean),
        // Dictation's mic only while the doc takes the owner's typed text (the
        // editor decides: never in Viewing, never on a view link).
        mic: () => entry.options.mic || null,
        micInHead: () => Boolean(entry.options.wide),
        onCanType: (yes) => { if (!yes) stopDictation(entry) },
        // The link turned off while it was open: say so in its place.
        onGone: () => { entry.status = 'gone'; const k = known(); if (k) k.state = 'gone'; host.schedule?.() },
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
      entry.status = err?.status === 410 ? 'gone' : err?.status === 404 ? 'missing' : 'down'
      entry.error = err
      const k = known()
      if (k && entry.status === 'gone') k.state = 'gone'
      host.schedule?.()
    })
    entry.retry = () => { entry.status = 'loading'; host.schedule?.(); begin() }
    if (!withme.st.loaded) withme.load(false)
    begin()
    return entry
  }

  function withMeState(entry, c) {
    const H = loam.ui.h
    const d = withme.byId(entry.withme)
    const title = d?.title || 'This doc'
    const remove = d ? H('button', { type: 'button', onclick: async () => { await withme.remove(d); (c.options.go || host.goFiles)?.(ROOT_ID, '') } }, 'Remove it') : null
    const back = H('button', { type: 'button', class: remove ? '' : 'go', onclick: () => (c.options.go || host.goFiles)?.(ROOT_ID, '') }, 'Back to Shared Docs')
    const say = {
      gone: ['No longer shared', `${d?.owner || 'Its owner'} turned off the link to “${title}”, or it expired. Nothing of it is kept here.`, [remove, back]],
      missing: ['Not in Shared with me', 'This doc was removed from Shared with me on this Loam.', [back]],
      down: ['Couldn’t open it just now', entry.error?.message || 'Its Loam didn’t answer.', [H('button', { type: 'button', class: 'go', onclick: () => entry.retry?.() }, 'Try again'), back]],
    }[entry.status] || ['', '', []]
    return H('div', { key: `doc-comments:state:${entry.key}:${entry.status}`, class: 'dc-host dc-wm-host' },
      H('div', { class: 'dc-wm-state', role: 'status' },
        H('span', { class: 'dc-wm-glyph' }, glyph(entry.status === 'down' ? 'changed' : 'people', 22)),
        H('h2', { text: say[0] }), H('p', { text: say[1] }),
        H('div', { class: 'dc-wm-acts' }, ...say[2].filter(Boolean))))
  }

  async function copyText(text, done) {
    if (!text) return
    try { await navigator.clipboard.writeText(text); listToast(done) } catch { listToast(text) }
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
      let cur = editors.get(c.files)
      if (!cur || cur.key !== key) {
        close(cur)
        cur = open(c)
      }
      cur.options = c.options
      cur.file = c.file
      if (cur.withme !== undefined && ['gone', 'down', 'missing'].includes(cur.status)) return withMeState(cur, c)
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
  // only docs with a link on, each drawn by core's own file row (`entry`,
  // 2026-11-17) so it opens, opens beside and has the ⋯ menu exactly as a
  // file anywhere else does. What this adds to that row: who has a link
  // (faces, a group link as a people glyph, "+N" past three; each says who
  // and what they can do on hover or a long press), what is open (the
  // modes) and when it last moved, and a dot on the tile where something is
  // new since the owner last looked. A quiet row says nothing about news.
  const shared = { docs: [], loaded: false, rows: new Map(), leaving: new Map(), seen: new Set() }
  const filesOf = () => host.files
  const docKey = (root, path) => `${root}:${path}`
  async function loadShared() {
    const res = await fetch(`${BASE}shared`, { credentials: 'same-origin' })
    if (!res.ok) throw new Error('Shared Docs could not be read just now.')
    const docs = (await res.json()).docs || []
    // The row as a folder would list it (size, age, what it may do), asked
    // of Files once per doc; a doc it cannot answer for is still listed.
    const files = filesOf()
    await Promise.all(docs.map(async (d) => {
      const k = docKey(d.root, d.path)
      if (shared.rows.has(k) || typeof files?.stat !== 'function') return
      try { shared.rows.set(k, await files.stat(d.root, d.path)) } catch { /* listed by its address */ }
    }))
    // A doc whose last link went off leaves gently, not with a pop.
    const now = new Set(docs.map(d => d.id))
    if (shared.loaded) {
      for (const d of shared.docs) {
        if (now.has(d.id) || shared.leaving.has(d.id)) continue
        const at = shared.docs.indexOf(d)
        shared.leaving.set(d.id, { doc: d, at })
        setTimeout(() => { shared.leaving.delete(d.id); host.schedule?.() }, LEAVE_MS)
      }
    }
    shared.docs = docs
    shared.loaded = true
  }
  const refreshShared = () => loadShared().catch(() => {}).finally(() => host.schedule?.())
  const isShared = (root, path) => shared.docs.some(d => d.root === root && d.path === path)

  function rowOf(d) {
    const name = (d.path || '').split('/').pop()
    return { kind: 'file', name, size: undefined, modified: d.last, capabilities: {}, ...(shared.rows.get(docKey(d.root, d.path)) || {}), root: d.root, path: d.path }
  }

  function sharedRow(d, entry, files, leaving = false) {
    const H = loam.ui.h
    const on = (d.links || []).filter(l => l.on)
    const modes = MODE_ORDER.filter(m => on.some(l => l.mode === m)).map(m => MODE_WORD[m])
    // One mode is said by name; several are the faces' to tell (each says
    // its own on hover or a long press), so the line stays one line.
    const modeText = modes.length === 1 ? modes[0] : on.length > 1 ? `${on.length} links` : ''
    const ago = typeof files?.ago === 'function' ? files.ago(d.last) : when(d.last)
    const fresh = d.new > 0 && !shared.seen.has(d.id)
    const meta = [
      fresh ? H('span', { class: 'dc-sd-new', text: `${d.new} new` }) : null,
      fresh ? ' · ' : null,
      [modeText, ago].filter(Boolean).join(' · '),
    ].filter(Boolean)
    const hadNew = d.new > 0
    return entry(rowOf(d), {
      meta,
      badge: hadNew ? H('span', { class: `dc-sd-dot${fresh ? '' : ' seen'}`, key: `dot:${d.id}`, role: fresh ? 'img' : null, 'aria-label': fresh ? 'Something new' : null }) : null,
      trail: people(on, d.id),
      onopen: () => { shared.seen.add(d.id) },
      className: `dc-sd-entry${leaving ? ' dc-sd-leaving' : ''}`,
    })
  }

  // Faces for the links: a named link is that person's initial in their own
  // tone, a group link the people glyph; three at most, then "+N".
  function people(on, id) {
    const H = loam.ui.h
    if (!on.length) return null
    const who = on.map(l => ({ name: l.name || '', mode: l.mode, people: l.people || [] }))
    const say = (p) => p.name ? `${p.name} · can ${p.mode}` : `Group link · can ${p.mode}${p.people.length ? ` · ${p.people.slice(0, 3).join(', ')}${p.people.length > 3 ? '…' : ''}` : ''}`
    const shown = who.slice(0, 3)
    const more = who.length - shown.length
    const face = (p, i) => H('span', {
      class: `dc-sd-face${p.name ? '' : ' grp'}`, key: `face:${id}:${i}`, ...tip.props,
      style: p.name ? { background: tone(p.name) } : null,
      'data-tip': say(p),
      text: p.name ? p.name.charAt(0).toUpperCase() : null,
    }, p.name ? null : glyph('people', 12))
    const nodes = shown.map(face)
    if (more > 0) nodes.push(H('span', { class: 'dc-sd-face more', key: `face:${id}:more`, ...tip.props, 'data-tip': who.slice(3).map(say).join('\n'), text: `+${more}` }))
    return H('span', { class: 'dc-sd-people', key: `people:${id}`, onclick: tip.swallow, oncontextmenu: tip.swallow, 'aria-label': `Shared with ${who.map(p => p.name ? `${p.name} (can ${p.mode})` : `a group link (can ${p.mode})`).join(', ')}` }, ...nodes)
  }

  // Shared with me (2026-11-17): docs others shared from their own Loams,
  // under "Shared by me" in the same list. It needs core's files of a
  // plugin's own root (`openPluginFile`, a row's own menu: `itemsMenu`), so
  // an older copy of the app shows Shared by me alone.
  const withMeOn = () => typeof filesOf()?.itemsMenu === 'function'
  const withme = createWithMe({
    base: BASE, root: ROOT_ID, schedule: () => host.schedule?.(),
    toast: (text) => listToast(text), files: filesOf, side: () => filesOf()?.side?.() || null,
  })
  if (withMeOn()) loam.ui.styles?.add?.('assets/withme.css')

  const sharedRoot = loam.places?.files?.root?.add?.({
    id: 'doc-comments',
    name: 'Shared Docs',
    desc: withMeOn() ? 'Docs you share, and docs shared with you' : 'Docs open to people outside',
    icon: (size) => peopleIcon(size),
    when: () => allowed(),
    load: () => Promise.all([loadShared(), withMeOn() ? withme.load() : null]),
    // A file of this root is a doc in Shared with me: `with-me/<id>.md`.
    // Its ⋯ in a panel's header is the row's own menu.
    menu: (path) => {
      const d = withme.byId(withmeId(path))
      return d ? withme.menuItems(d, (r, p, o) => (host.goFiles || (() => {}))(r, p, o)) : []
    },
    row: (path) => {
      const d = withme.byId(withmeId(path))
      return d ? withme.rowOf(d) : { kind: 'file', name: 'Shared doc', capabilities: {} }
    },
    render({ go, files, entry }) {
      const H = loam.ui.h
      // Opened cold (before this plugin registered, so `load` never ran):
      // Shared with me asks for itself, as Shared by me does.
      if (withMeOn() && !withme.st.loaded && !withme.st.loading) withme.load()
      if (withMeOn()) return H('div', { class: 'dc-sd-sections', key: 'sd:sections' },
        H('div', { class: 'kicker fs-kicker dc-sd-kicker', text: 'Shared by me' }),
        sharedByMe({ go, files, entry }),
        H('div', { class: 'kicker fs-kicker dc-sd-kicker', text: 'Shared with me' }),
        withme.st.loaded ? withme.section({ entry, go, H }) : H('div', { class: 'fs-quiet', text: 'Loading…' }))
      return sharedByMe({ go, files, entry })
    },
  })

  function sharedByMe({ go, files, entry }) {
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
      if (!shared.docs.length && !shared.leaving.size) return H('div', { class: 'fs-quiet dc-sd-empty', text: 'No doc is shared with anyone outside Loam yet. Share one from its ⋯ menu, or with the Share button at the top of the doc.', key: 'sd:empty' })
      // An older core with no shared row: open by address, as before.
      const draw = typeof entry === 'function' ? entry : (row, x) => H('button', { class: 'setrow', key: `sd:${row.path}`, onclick: () => { x.onopen?.(); go(row.root, row.path, { isFile: true }) } }, H('div', { class: 'fs-words' }, H('div', { class: 'fs-name', text: row.name }), H('div', { class: 'fs-meta' }, x.meta)))
      const list = shared.docs.map(d => ({ d, leaving: false }))
      for (const { doc, at } of shared.leaving.values()) list.splice(Math.min(at, list.length), 0, { d: doc, leaving: true })
      return H('div', { class: 'card card-group dc-sd' }, list.map(({ d, leaving }) => sharedRow(d, draw, files, leaving)))
  }

  // "Share settings" (or "Share…" on a doc not yet shared), first in a
  // Markdown file's ⋯ menu anywhere in Files (core's `files.action`, slot
  // `menu`): the same share sheet as the doc's Share button, for that doc,
  // without opening it. Closing it leaves the list where it was, redrawn.
  const canShare = (r) => allowed() && r && r.kind !== 'folder' && r.root === 'vault' && /\.(md|markdown)$/i.test(r.path || '') && r.capabilities?.write === true
  const shareItem = loam.places?.files?.action?.add?.({
    id: 'doc-comments.share',
    slot: 'menu',
    when: (c) => canShare(c.resource),
    render: ({ resource }) => ({ id: 'share', label: isShared(resource.root, resource.path) ? 'Share settings' : 'Share…', icon: (size) => peopleIcon(size), className: 'dc-menu-share', run: (r) => shareFromList(r) }),
  })

  function shareFromList(r) {
    const name = (r.path || '').split('/').pop().replace(/\.(md|markdown)$/i, '')
    let title = name
    // The invite names the doc by its heading, as the doc's own Share does;
    // read through Files, which (unlike opening it) marks nothing seen.
    filesOf()?.readText?.(r.root, r.path).then((text) => { title = (/^#\s+(.+?)\s*#*\s*$/m.exec(text || '')?.[1]) || name }).catch(() => {})
    const sharing = createSharing({
      base: BASE, root: r.root, path: r.path, title: () => title,
      toast: (text, undo) => listToast(text, undo),
      onChange: () => refreshShared(),
    })
    sharing.open()
  }

  // A toast for the share sheet opened from a list (the editor has its own).
  let toastNode = null
  let toastTimer = null
  function listToast(text, undo) {
    if (!toastNode) {
      toastNode = document.createElement('div')
      toastNode.className = 'dc-toast dc-list-toast'
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

  const onFocus = () => {
    for (const cur of editors.values()) if (cur.status === 'ready' && !cur.editor.dirty()) cur.editor.reload()
  }
  globalThis.addEventListener?.('focus', onFocus)
  const renamed = loam.events?.on?.('file.renamed', () => { /* the next render opens it under its new name */ })

  return () => {
    disposed = true
    globalThis.removeEventListener?.('focus', onFocus)
    renamed?.remove?.()
    reg?.remove?.()
    sharedRoot?.remove?.()
    shareItem?.remove?.()
    for (const cur of editors.values()) close(cur)
    editors.clear()
    tip.hide()
    clearTimeout(toastTimer)
    toastNode?.remove()
  }
}


// Who a face is, in a small label above it: on hover with a mouse, on a
// long press by touch (which then does not open the doc under it).
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
      node.className = 'dc-sd-tip'
      node.setAttribute('role', 'tooltip')
      document.body.append(node)
    }
    node.textContent = text
    node.classList.remove('on')
    // Loam's page is zoomed (Settings › Size): a rectangle is in screen
    // pixels, a fixed position in the page's own.
    const r = face.getBoundingClientRect()
    const scale = (document.body.getBoundingClientRect().width / (document.body.offsetWidth || 1)) || 1
    // Centred over the face, kept inside the window.
    const vw = document.documentElement.clientWidth / scale
    const half = Math.min(node.offsetWidth, vw - 16) / 2
    node.style.left = `${Math.min(Math.max((r.left + r.width / 2) / scale, half + 8), vw - half - 8)}px`
    node.style.top = `${r.top / scale}px`
    requestAnimationFrame(() => node?.classList.add('on'))
    // Any next touch or click elsewhere puts it away.
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
    // A click after a long press only shows who it is; it opens nothing.
    swallow: (e) => {
      if (e.type === 'contextmenu') { if (pressed || e.pointerType !== 'mouse') e.preventDefault(); return }
      if (!pressed) return
      pressed = false
      e.preventDefault()
      e.stopPropagation()
    },
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

function when(iso) {
  const t = Date.parse(iso)
  if (!Number.isFinite(t)) return ''
  const s = (Date.now() - t) / 1000
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min`
  if (s < 86400) return `${Math.round(s / 3600)} h`
  if (s < 2 * 86400) return 'Yesterday'
  return new Date(t).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}
